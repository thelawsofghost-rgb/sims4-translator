#!/usr/bin/env python3
"""持久化翻译缓存: output/translation_cache.db (SQLite)。

每个语义 phrase 一行, 以 request_fingerprint 为主键。fingerprint 由会影响
模型输出的 canonical 输入经 SHA-256 计算而得, 因此任何输入变化(gossary/
prompt/context/model/temperature)都会自然失效, 不依赖人工维护版本号。

落盘策略: 命中即复用; miss 翻译成功后**立即**写库 + commit,
绝不等到全量结束才落盘 -> 天然支持 checkpoint/异常重启 resume。
"""
import csv
import hashlib
import json
import sqlite3
from pathlib import Path

# ---- 模型推理 canonical 输入 (fingerprint 只需这些; 不含 translation_id 等定位信息) ----
_SYSTEM_PROMPT = (
    "You translate Sims 4 pose names to simplified Chinese. "
    "Output only the numbered translations."
)
_TARGET_LANG = "zh-CN"
_TEMPERATURE = 0.2
_MODEL_DEFAULT = "ni-fei:latest"


def system_prompt() -> str:
    """返回当前 system prompt (集中管理, 便于 fingerprint 与发往模型一致)。"""
    return _SYSTEM_PROMPT


def model_name(model=None) -> str:
    return model or _MODEL_DEFAULT


def target_language() -> str:
    return _TARGET_LANG


def temperature() -> float:
    return _TEMPERATURE


def build_fingerprint(*, source_phrase: str, glossary_hint: str = "",
                      context: str = "", system_prompt: str = None,
                      model: str = None, temperature: float = None,
                      target_language: str = None) -> str:
    """由会实际影响模型输出的 canonical 输入计算 SHA-256 指纹。

    注意: glossary_hint 须先按固定顺序规范化(外部分类保证), context 原文携带。
    任一输入变化 -> 指纹变化 -> cache miss, 天然失效。
    """
    canon = {
        "source_phrase": (source_phrase or "").strip(),
        "glossary_hint": (glossary_hint or "").strip(),
        "context": (context or "").strip(),
        "system_prompt": system_prompt if system_prompt is not None else _SYSTEM_PROMPT,
        "model": model_name(model),
        "temperature": temperature if temperature is not None else _TEMPERATURE,
        "target_language": target_language if target_language is not None else _TARGET_LANG,
    }
    blob = json.dumps(canon, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class PhraseCache:
    """translation_cache.db 的封装。主键 = request_fingerprint。"""

    def __init__(self, out_dir, model=None):
        self.db_path = Path(out_dir) / "translation_cache.db"
        self.model = model_name(model)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS phrase_cache (
                request_fingerprint  TEXT PRIMARY KEY,
                translation_id       TEXT NOT NULL,
                segment_index        INTEGER NOT NULL,
                source_phrase        TEXT NOT NULL,
                source_hash          TEXT NOT NULL,
                translation          TEXT NOT NULL,
                model                TEXT NOT NULL,
                created_at           TEXT NOT NULL,
                updated_at           TEXT NOT NULL
            )
        """)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_tid ON phrase_cache(translation_id)")
        self._conn.commit()

    def get(self, fingerprint: str):
        """fingerprint hit -> 返回 {translation, ...}; miss -> None。"""
        cur = self._conn.execute(
            "SELECT translation, model FROM phrase_cache WHERE request_fingerprint=?", (fingerprint,))
        row = cur.fetchone()
        return {"translation": row["translation"], "model": row["model"]} if row else None

    def put(self, *, fingerprint, translation_id, segment_index, source_phrase,
            source_hash, translation, now):
        """写一个 phrase 的翻译结果, 立即 commit (不批量攒到结尾)。"""
        self._conn.execute("""
            INSERT OR REPLACE INTO phrase_cache
            (request_fingerprint, translation_id, segment_index, source_phrase,
             source_hash, translation, model, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (fingerprint, translation_id, segment_index, source_phrase,
              source_hash, translation, self.model, now, now))
        self._conn.commit()

    def write_many(self, rows, now):
        """批量写(供测试/预填充), 一次 commit。rows: dict 列表。"""
        for r in rows:
            self._conn.execute("""
                INSERT OR REPLACE INTO phrase_cache
                (request_fingerprint, translation_id, segment_index, source_phrase,
                 source_hash, translation, model, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (r["request_fingerprint"], r["translation_id"], r["segment_index"],
                  r["source_phrase"], r["source_hash"], r["translation"],
                  r.get("model", self.model), now, now))
        self._conn.commit()

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM phrase_cache").fetchone()[0]

    def close(self):
        try:
            self._conn.close()
        except Exception:  # noqa
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def csv_cell(v) -> str:
    """写出到 CSV 前的字符串化处理。"""
    if v is None:
        return ""
    return str(v)
