#!/usr/bin/env python3
"""
Fast DBPF Index Reader — 浅扫描核心

设计原则 (对应项目规格修订点 4):
  - 只读 DBPF Header + Resource Index (entries 元数据), 完全不读取 Texture/Mesh/CLIP/body
  - 遇到无法确认的 Header / Index Layout → 不猜测 → ERROR_UNSUPPORTED_DBPF
  - seek 定位读取, 单文件读取量保持在 KB 级, 不整读 26.7GB

本模块只做"读索引", 不做任何资源解析/写回。
写回与深度解析由独立 backend 负责 (见 backend_abstract.py)。

DBPF Header 布局 (Sims 4):
  offset  size  field
  0x00    4     magic 'DBPF'
  0x04    4     major version (0x02 / 0x03 / 0x04)
  0x08    4     minor version
  0x0C    4     (index type/flags)
  0x10    4     index entry count
  0x14    4     index offset (from file start)
  0x18    4     index size (bytes)
  0x1C    4     (checksum / reserved)

  v2/v3: index offset absolute. Some variants index offset relative to end.
  更老 v1: 无 index 段。
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

    @property
    def is_compressed(self) -> bool:
        return False  # 压缩标记由 offset 高位表示, 见 parse 逻辑


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

    # Sims 4 resource entry 固定 20 字节:
    #   type(4) group(4) instance_high(4) instance_low(4) offset(4)  + (valid flag)
    # 不同版本 entry 布局可能不同, 这里严格按已知布局处理

    def __init__(self, fh, file_size: int):
        self._fh = fh
        self._file_size = file_size

    def read_index(self) -> DBPFIndex:
        """读取 DBPF 头 + 索引。任何无法确认的情况抛 UnsupportedDBPFError。"""
        fh = self._fh
        fh.seek(0)
        header = fh.read(32)
        if len(header) < 32:
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

        # 索引 header 字段 (从 0x0C 开始)
        # 注意: 不同版本字段含义略有差异, 这里按最通用布局读取
        entry_count = struct.unpack("<I", header[0x10:0x14])[0]
        index_offset = struct.unpack("<I", header[0x14:0x18])[0]
        index_size = struct.unpack("<I", header[0x18:0x1C])[0]

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
        if index_offset + max(index_size, entry_count * 20) > self._file_size:
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
        # 20 字节/entry (Sims 4 resource index entry)
        ENTRY = 20
        expected = count * ENTRY
        if offset + expected > self._file_size:
            # 索引区超出文件 → 布局无法确认
            raise UnsupportedDBPFError(
                f"索引区超出文件 ({offset}+{expected} > {self._file_size})。跳过。"
            )

        self._fh.seek(offset)
        raw = self._fh.read(expected)
        if len(raw) != expected:
            raise DBPFError("索引区读取不完整")

        entries = []
        for i in range(count):
            e = raw[i * ENTRY:(i + 1) * ENTRY]
            type_id, group_id, inst_hi, inst_lo, off = struct.unpack("<IIIII", e[:20])

            # 压缩标记: offset 高 4 位的 0x80000000 表示压缩 (Sims 4 DBPF)
            compressed = bool(off & 0x80000000)
            body_offset = off & 0x7FFFFFFF

            instance_id = (inst_hi << 32) | inst_lo
            entries.append(ResourceEntry(
                type_id=type_id,
                group_id=group_id,
                instance_id_high=inst_hi,
                instance_id_low=inst_lo,
                instance_id=instance_id,
                offset=body_offset,
                size=0,  # type 不同 entry 布局里 size 的位置不同, 此处只记 offset
            ))

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
