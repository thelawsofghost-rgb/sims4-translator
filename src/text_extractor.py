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

    实测 (2026-08-12, WWLaserAnimations.package):
      - 资源体整体为 zlib 压缩 (头 0x78 0x9C)
      - 解压后: 'STBL' magic + 版本 + 记录区
      - 记录区含多条可读文本 (动画动作名), 文本以单字节 ASCII / UTF-8 存储
      - 不同类型 STBL 文本编码可能为 UTF-16LE 或单字节 ASCII

    稳健策略: 优先尝试解析为 zlib 并提取可读字符串; 无法解压时
    回退到在原始字节中扫描可读文本 (兼容未压缩与 UTF-16LE 形态)。
    """
    import re as _re
    import zlib as _zlib
    import struct as _struct

    def _scan(data: bytes) -> List[Tuple[int, str]]:
        out = []
        # 单字节可读串 (ASCII/UTF-8)
        for m in _re.finditer(rb"[ -~]{4,}", data):
            s = m.group().decode("ascii", errors="ignore")
            if s and not _looks_like_binary_garbage(s):
                out.append((0, s))
        # UTF-16LE 可读串
        try:
            txt = data.decode("utf-16-le", errors="ignore")
        except Exception:
            txt = ""
        for m in _re.finditer(r"[ -~]{4,}", txt):
            s = m.group()
            if s and not _looks_like_binary_garbage(s):
                out.append((0, s))
        return out

    if not stbl_bytes:
        return []
    try:
        data = stbl_bytes
        # 尝试 zlib 解压 (zlib 头为 78 9c/da/01 等; 用 startswith 更稳)
        if data[:2] == b"\x78\x9c" or data[:2] == b"\x78\xda" or data[:2] == b"\x78\x01":
            try:
                data = _zlib.decompress(data)
            except Exception:
                pass
        # 去掉 STBL 头, 只处理正文
        if data[:4] == b"STBL":
            body = data[4:]
        else:
            body = data
        return _scan(body)
    except Exception:
        return []


def _looks_like_binary_garbage(s: str) -> bool:
    """过滤明显是二进制噪声的 '可读' 片段 (过多控制符/异常字符)。"""
    if not s:
        return True
    bad = sum(1 for ch in s if (ord(ch) < 0x20 and ch not in "\t\n") or ord(ch) == 0x7F)
    return bad / max(1, len(s)) > 0.2


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
