#!/usr/bin/env python3
"""
Package Backend 抽象层

设计原则 (对应项目规格):
  - 分类引擎分类时不依赖具体 Python/C# DBPF 实现
  - 将来从轻量只读 backebd 切换到 s4pi/Sims4Tools(C#) 时, 只需新增一个 backend,
    分类引擎与扫描逻辑不变
  - Phase 1 只用只读 backend (ReadOnlyFileBackend), 不实现写回

职责:
  - 打开 package, 读取资源索引 (调用 FastIndexReader)
  - 按需读取指定资源的 body 元数据/小体积资源 (XML/STBL/Snippet)
  - 不负责分类 (分类在 classifier.py)
  - 不负责写回 (Phase 3 由写回 backend 负责)
"""

from typing import List, Optional
from pathlib import Path
import os

from dbpf_fast import DBPFIndex, ResourceEntry, FastIndexReader, safe_parse


class PackageReadError(Exception):
    pass


class IPackageBackend:
    """Package 读取层抽象接口。所有 backend 必须实现。"""

    def open(self, path) -> "IPackageBackend": ...

    def close(self) -> None: ...

    def read_index(self) -> DBPFIndex: ...

    def index_entries(self) -> List[ResourceEntry]: ...

    def resource_types(self) -> set[int]: ...

    def count_type(self, type_id: int) -> int: ...

    def read_small_resource(self, entry: ResourceEntry, max_bytes: int = 256 * 1024) -> Optional[bytes]:
        """读取小体积资源 body (XML/STBL/Snippet)。Texture/Mesh/CLIP body 禁止调用。"""
        ...


class ReadOnlyFileBackend(IPackageBackend):
    """轻量只读 backend — 基于 FastIndexReader。Phase 1 使用。"""

    def __init__(self):
        self._fh = None
        self._path = None
        self._file_size = 0
        self._index: Optional[DBPFIndex] = None

    def open(self, path) -> "ReadOnlyFileBackend":
        self._path = Path(path)
        p = self._path
        if not p.exists() or not p.is_file():
            raise PackageReadError(f"文件不存在: {p}")
        try:
            self._fh = open(p, "rb")
        except OSError as e:
            raise PackageReadError(f"无法打开: {e}")
        self._fh.seek(0, 2)
        self._file_size = self._fh.tell()
        return self

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None

    def read_index(self) -> DBPFIndex:
        if self._index is None:
            self._index = self._parse()
        return self._index

    def _parse(self) -> DBPFIndex:
        idx, err = safe_parse_with_fh(self._fh, self._file_size)
        if err or idx is None:
            raise PackageReadError(f"DBPF 解析失败: {err or 'unknown'}")
        return idx

    def index_entries(self) -> List[ResourceEntry]:
        return self.read_index().entries

    def resource_types(self) -> set[int]:
        return {e.type_id for e in self.index_entries()}

    def count_type(self, type_id: int) -> int:
        return sum(1 for e in self.index_entries() if e.type_id == type_id)

    def read_small_resource(self, entry: ResourceEntry, max_bytes: int = 256 * 1024) -> Optional[bytes]:
        if entry.size:
            size = entry.size
        else:
            # 本 backend 未解析 size; 若为0则保守读取到 max_bytes 或文件结束
            size = min(max_bytes, self._file_size - entry.offset) if entry.offset < self._file_size else 0

        if size <= 0 or size > max_bytes:
            return None  # 不信任超限读取
        if entry.offset >= self._file_size:
            return None
        self._fh.seek(entry.offset)
        data = self._fh.read(size)
        return data


def safe_parse_with_fh(fh, file_size: int) -> tuple[Optional[DBPFIndex], Optional[str]]:
    """供 backend 使用的安全解析 (带异常分类)。"""
    try:
        reader = FastIndexReader(fh, file_size)
        idx = reader.read_index()
        return idx, None
    except Exception as e:
        # 归类
        msg = str(e)
        if "UnsupportedDBPF" in type(e).__name__ or "不支持" in msg or "布局" in msg or "超出" in msg:
            return None, "ERROR_UNSUPPORTED_DBPF"
        return None, "ERROR"


# 后端工厂 — 将来切换 s4pi/Sims4Tools 时扩展
def get_backend(kind: str = "readonly") -> IPackageBackend:
    """创建指定类型的 backend。kind in {'readonly', ...}"""
    if kind == "readonly":
        return ReadOnlyFileBackend()
    raise ValueError(f"未知 backend: {kind}")
