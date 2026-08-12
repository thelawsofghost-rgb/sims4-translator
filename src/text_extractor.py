#!/usr/bin/env python3
"""
显示文本提取器 — 只提取玩家可见的动作/Pose 名称

严格范围:
  - 只提取 WW animation_display_name 内容
  - 只提取 Pose Pack / STBL 中真正显示的名称
  - 绝不修改: animation_author, WWID, Resource/Instance/Group ID, Hash,
    ClipName, actor_id, animation_category, animation_locations, animation_tags, 内部引用
  - 纯提取, 不做任何写回
"""

import re
from typing import List, Dict, Tuple

# WW 显示名标签 (只做提取, 内容是从 <T> 标签里取)
WW_RAW = "animation_raw_display_name"
WW_OLD = "animation_display_name"

# 提取 <T n="...">TEXT</T> 中 TEXT
_T_ATTR_RAW = re.compile(r'<T\s+n="%s"[^>]*>(.*?)</T>' % re.escape(WW_RAW), re.S)
_T_ATTR_OLD = re.compile(r'<T\s+n="%s"[^>]*>(.*?)</T>' % re.escape(WW_OLD), re.S)


def extract_ww_display_texts(xml_text: str) -> List[str]:
    """从 WW 动画 XML 中提取所有玩家可见的显示名。只提取, 不改字段。"""
    found = []
    found += _T_ATTR_RAW.findall(xml_text)
    found += _T_ATTR_OLD.findall(xml_text)
    # 清理空白 & CDATA
    cleaned = []
    for s in found:
        s = s.replace("<![CDATA[", "").replace("]]>", "").strip()
        if s:
            cleaned.append(s)
    return cleaned


def extract_stbl_strings(stbl_bytes: bytes) -> List[Tuple[int, str]]:
    """
    从 STBL 二进制中提取 (string_id, text) 列表。
    仅用于显示文本提取; 绝不修改 STBL。
    STBL 布局 (Sims 4):
      magic 'STBL' (4)
      version (4)
      reserved (4)
      string count (4)
      then entries: 每项 hash(8) offset(4) 指向 string
    """
    if not stbl_bytes or len(stbl_bytes) < 16:
        return []
    try:
        import struct
        if stbl_bytes[0:4] != b"STBL":
            return []
        version = struct.unpack("<I", stbl_bytes[4:8])[0]
        if version not in (1, 2, 3, 4, 5):
            return []
        count = struct.unpack("<I", stbl_bytes[12:16])[0]
        out = []
        # 每项 12 字节在 offset 16 开始
        # item: hash(8) offset(4)
        pos = 16
        for i in range(count):
            if pos + 12 > len(stbl_bytes):
                break
            s_hash = struct.unpack("<Q", stbl_bytes[pos:pos+8])[0]
            s_off = struct.unpack("<I", stbl_bytes[pos+8:pos+12])[0]
            pos += 12
            # string 在 s_off 处, 以 \0 或 \x00 结束
            if s_off >= len(stbl_bytes):
                continue
            end = stbl_bytes.find(b"\x00", s_off)
            if end == -1:
                end = len(stbl_bytes)
            try:
                text = stbl_bytes[s_off:end].decode("utf-16-le", errors="ignore")
            except Exception:
                text = stbl_bytes[s_off:end].decode("utf-8", errors="ignore")
            if text:
                out.append((s_hash, text))
        return out
    except Exception:
        return []


def is_chinese(text: str) -> bool:
    """判断文本是否基本为中文 (达到一定比例)。"""
    if not text:
        return False
    cn = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')
    return cn / max(1, len(text)) > 0.3


def is_numeric_or_id(text: str) -> bool:
    """判断是否为数字/ID/hash (跳过)。"""
    if not text:
        return True
    return bool(re.fullmatch(r"[\d\s_\-xXa-fA-F]+", text)) and bool(re.search(r"\d", text))


def classify_text_intent(text: str) -> str:
    """
    判断文本是否应翻译。
    Returns one of:
      'TRANSLATE'       → 玩家可见英文, 应翻译
      'CHINESE'         → 已是中文, 保持
      'SKIP_ID'         → 数字/ID/hash
      'SKIP_UNCERTAIN'  → 无法确定是否玩家可见
    """
    if not text or not text.strip():
        return "SKIP_UNCERTAIN"
    if is_chinese(text):
        return "CHINESE"
    if is_numeric_or_id(text):
        return "SKIP_ID"
    # 纯英文/可读文本 → 翻译
    if re.search(r"[A-Za-z]{2,}", text):
        return "TRANSLATE"
    return "SKIP_UNCERTAIN"
