#!/usr/bin/env python3
"""
Fast DBPF Index Reader — 浅扫描核心

设计原则 (对应项目规格修订点 4):
  - 只读 DBPF Header + Resource Index (entries 元数据), 完全不读取 Texture/Mesh/CLIP/body
  - 遇到无法确认的 Header / Index Layout → 不猜测 → ERROR_UNSUPPORTED_DBPF
  - seek 定位读取, 单文件读取量保持在 KB 级, 不整读 26.7GB

本模块只做"读索引", 不做任何资源解析/写回。
写回与深度解析由独立 backend 负责 (见 backend_abstract.py)。

DBPF Header 布局 (Sims 4, 经真实 WW 动画包实测确认):
  offset  size  field
  0x00    4     magic 'DBPF'
  0x04    4     major version (0x02 / 0x03 / 0x04)
  0x08    4     minor version
  0x0C    4     flags (0)
  0x10..0x20    reserved (0)
  0x24    4     index entry count
  0x28    4     reserved (0)
  0x2C    4     index size (bytes)
  0x30..0x38    reserved (0)
  0x3C    4     compression flag (常见 3)
  0x40    4     index offset (from file start, 绝对偏移)

Index 区:
  index_offset:  4 字节 padding (0x00000000)
  index_offset+4: count 个 entry, 每 32 字节:
    type(4) group(4) instance_high(4) instance_low(4)
    offset(4) size(4) flags(4) reserved(4)
  offset 与 size 的最高 1 位 (0x80000000) 为压缩标记, 实际值需 & 0x7FFFFFFF。

  (2026-08-12 实测 WWLaserAnimations.package: major=2, count@0x24=41,
   size@0x2C=0x524, offset@0x40=0x208AB, entry=32 字节, 索引区有 4 字节 padding。)
"""

import struct
from dataclasses import dataclass, field
from typing import List, Optional


class DBPFError(Exception):
    """DBPF 解析异常基类"""
    pass


class UnsupportedDBPFError(DBPFError):
    """无法确认的 DBPF 布局, 禁止猜测解析"""
    pass


@dataclass
class ResourceEntry:
    """单个资源索引条目 (仅元数据, 不含 body)"""
    type_id: int
    group_id: int
    instance_id_high: int
    instance_id_low: int
    instance_id: Optional[int]  # 组合后的 64bit
    offset: int
    size: int
    is_compressed: bool = False  # offset 高位的压缩标记


@dataclass
class DBPFIndex:
    major: int
    minor: int
    index_offset: int
    index_size: int
    is_relative_offset: bool
    entries: List[ResourceEntry] = field(default_factory=list)


class FastIndexReader:
    """极速只读 DBPF 索引解析器。严格模式: 未知布局抛 UnsupportedDBPFError。"""

    DBPF_MAGIC = b"DBPF"

    # 支持的主版本 (Sims 4)
    SUPPORTED_MAJOR = {0x02, 0x03, 0x04}

    # Sims 4 resource entry 固定 32 字节:
    #   type(4) group(4) instance_high(4) instance_low(4) offset(4) size(4) flags(4) reserved(4)
    # 索引区在 index_offset 处有 4 字节 padding(0x00000000), 真正 entry 从 index_offset+4 开始。
    # 不同版本 entry 布局可能不同, 这里严格按已知布局处理

    def __init__(self, fh, file_size: int):
        self._fh = fh
        self._file_size = file_size

    def read_index(self) -> DBPFIndex:
        """读取 DBPF 头 + 索引。任何无法确认的情况抛 UnsupportedDBPFError。"""
        fh = self._fh
        fh.seek(0)
        header = fh.read(0x44)  # 需读到 0x44 才能取到 0x40 的 index_offset
        if len(header) < 0x44:
            raise UnsupportedDBPFError("文件过短, 不是有效 DBPF")

        magic = header[0:4]
        if magic != self.DBPF_MAGIC:
            raise DBPFError("非 DBPF 格式 (magic 不符)")

        major, minor = struct.unpack("<II", header[4:12])
        if major not in self.SUPPORTED_MAJOR:
            raise UnsupportedDBPFError(
                f"不支持的 DBPF 主版本: {major} (支持: {sorted(self.SUPPORTED_MAJOR)})。"
                f"禁止猜测解析, 跳过。"
            )

        # 索引 header 字段 (Sims 4 实测布局):
        #   entry_count @ 0x24, index_size @ 0x2C, index_offset @ 0x40
        entry_count = struct.unpack("<I", header[0x24:0x28])[0]
        index_size = struct.unpack("<I", header[0x2C:0x30])[0]
        index_offset = struct.unpack("<I", header[0x40:0x44])[0]

        if index_offset == 0 and entry_count == 0:
            raise DBPFError("空索引 (无资源)")

        # 偏移量相对/绝对判定: 若 index_offset >= file_size 视为异常
        if index_offset >= self._file_size:
            raise UnsupportedDBPFError(
                f"索引偏移超出文件大小 ({index_offset} >= {self._file_size})。"
                f"可能是不支持的 DBPF 布局, 禁止猜测。"
            )

        is_relative = False
        # 某些 v2 布局 index offset 相对文件末尾, 需验证 index_offset + index_size
        # 若明显异常, 尝试反算; 若仍无法确认 → 抛错
        if index_offset + index_size > self._file_size + 4:  # +4 容忍索引区padding
            alt = self._file_size - index_offset
            if 0 <= alt < self._file_size:
                is_relative = True
                index_offset = alt
            else:
                raise UnsupportedDBPFError(
                    f"索引布局无法确认 (offset={index_offset}, size={index_size}, "
                    f"file={self._file_size})。跳过。"
                )

        entries = self._read_entries(index_offset, entry_count, major)
        return DBPFIndex(
            major=major,
            minor=minor,
            index_offset=index_offset,
            index_size=index_size,
            is_relative_offset=is_relative,
            entries=entries,
        )

    def _read_entries(self, offset: int, count: int, major: int) -> List[ResourceEntry]:
        """读取资源索引条目。只读每个 entry 的固定头部字段, 不读 body。"""
        # 32 字节/entry (Sims 4 resource index entry), 且索引区有 4 字节 padding
        ENTRY = 32
        PAD = 4  # 索引区开头 padding
        expected = PAD + count * ENTRY
        if offset + expected > self._file_size + 4:  # 容忍末尾 padding
            # 索引区超出文件 → 布局无法确认
            raise UnsupportedDBPFError(
                f"索引区超出文件 ({offset}+{expected} > {self._file_size})。跳过。"
            )

        self._fh.seek(offset + PAD)  # 跳过 padding, 从第一个 entry 开始
        raw = self._fh.read(count * ENTRY)
        if len(raw) != count * ENTRY:
            raise DBPFError("索引区读取不完整")

        entries = []
        for i in range(count):
            e = raw[i * ENTRY:(i + 1) * ENTRY]
            if len(e) < 24:
                raise DBPFError("索引条目过短")
            type_id, group_id, inst_hi, inst_lo, off, sz = struct.unpack("<IIIIII", e[:24])

            # 压缩标记: offset/size 高 4 位的 0x80000000 表示压缩 (Sims 4 DBPF)
            compressed = bool(off & 0x80000000)
            body_offset = off & 0x7FFFFFFF
            body_size = sz & 0x7FFFFFFF

            instance_id = (inst_hi << 32) | inst_lo
            entries.append(ResourceEntry(
                type_id=type_id,
                group_id=group_id,
                instance_id_high=inst_hi,
                instance_id_low=inst_lo,
                instance_id=instance_id,
                offset=body_offset,
                size=body_size,
            ))
            if compressed:
                entries[-1].is_compressed = True

        return entries


def safe_parse(path) -> tuple[Optional[DBPFIndex], Optional[str]]:
    """带错误归类的安全解析入口。
    返回 (index, error_code)。error_code in {None, 'ERROR_UNSUPPORTED_DBPF', 'ERROR'}"""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            reader = FastIndexReader(fh, size)
            idx = reader.read_index()
            return idx, None
    except UnsupportedDBPFError as e:
        return None, "ERROR_UNSUPPORTED_DBPF"
    except Exception as e:
        return None, "ERROR"
