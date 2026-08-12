#!/usr/bin/env python3
"""
增量扫描缓存 — mod_index_cache.db (SQLite)

记录每个 package 的 path + size + mtime 摘要, 未变则复用, 只重扫新增/变更。
Phase 1 不计算全文件 hash (只读 path+size+mtime)。
"""

import os
import sqlite3
import time
from pathlib import Path
from typing import Optional, Dict, Any


class ScanCache:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._init()

    def _init(self):
        self._conn.execute("""
        CREATE TABLE IF NOT EXISTS package_cache (
            path TEXT PRIMARY KEY,
            size INTEGER,
            mtime REAL,
            level TEXT,
            reason TEXT,
            evidence TEXT,
            scan_time REAL,
            has_clip INTEGER,
            has_ww_xml INTEGER,
            has_pose_snippet INTEGER,
            has_stbl INTEGER,
            visible_text_count INTEGER DEFAULT 0,
            english_count INTEGER DEFAULT 0,
            chinese_count INTEGER DEFAULT 0,
            uncertain_count INTEGER DEFAULT 0,
            scan_status TEXT
        )
        """)
        self._conn.commit()

    def is_unchanged(self, path: str, size: int, mtime: float) -> bool:
        row = self._conn.execute(
            "SELECT size, mtime FROM package_cache WHERE path=?", (path,)
        ).fetchone()
        if row is None:
            return False
        return int(row[0]) == int(size) and abs(float(row[1]) - mtime) < 0.5

    def get(self, path: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM package_cache WHERE path=?", (path,)
        ).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self._conn.execute(
            "SELECT * FROM package_cache WHERE path=?").description]
        return dict(zip(cols, row))

    def upsert(self, rec: Dict[str, Any]):
        cols = list(rec.keys())
        placeholders = ",".join("?" * len(cols))
        colnames = ",".join(f'"{c}"' for c in cols)
        self._conn.execute(
            f"INSERT OR REPLACE INTO package_cache ({colnames}) VALUES ({placeholders})",
            [rec[c] for c in cols],
        )
        self._conn.commit()

    def set_status(self, path: str, size: int, mtime: float, **kw):
        rec = {
            "path": path, "size": int(size), "mtime": float(mtime),
            "scan_time": time.time(),
        }
        rec.update(kw)
        self.upsert(rec)

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass
