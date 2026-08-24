#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
story_animation_entry_index.py —— 只读: 建立 WW_ANIM_XML 动画条目索引

背景(P27 目标调整): 不再追 STBL/不再追 ts4script。已确认:
  - WW_ANIM_XML (type=0x7DF2169C) 是动画注册入口
  - animation_raw_display_name 存在于 XML
  - 先前 STBL/hash audit 失败原因可能是没有正确建立 XML entry ordinal -> animation_id 映射
  本脚本先建立【XML 动画条目索引】: 按 <animations_list> 内条目出现顺序编号 ordinal。

只解析 type=0x7DF2169C (WW_ANIM_XML)。
不做: 扫 ts4script / 扫 STBL / dump 全XML / 搜文本。

解析: 用 ElementTree 正确处理嵌套, 不做简单字符串搜索 <U>。
  - 根 <U n="WW"> -> 内嵌 <L n="animations_list"> -> 直接子 <U n="anmN"> 即动画条目
  - 每个条目的直接标量子节点 (T/I/E), n= 即字段名, 文本即值
  - 按 animations_list 内出现顺序 ordinal = 0,1,2,...

输出 output/story_animation_entry_index.txt:
  package / type / group / instance
  === ordinal N ===
  animation_id:
  animation_raw_display_name:
  animation_display_name:
  animation_author:
  (字段缺失写 <MISSING>)
  默认至少 ordinal 295-310。

fail-closed / 只读: 源缺->2; 无 WW_ANIM_XML->3; 无 animations_list->4;
  解析异常->5; 正常 0。ZERO_WRITE_TO_MODS=YES。

用法 (Windows):
  python scripts\story_animation_entry_index.py "WW_Nevely42_Animations.package"
      [--start 0] [--end 319] [--out output/story_animation_entry_index.txt]
"""
import argparse
import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

_spec = importlib.util.spec_from_file_location(
    "ww_canary_builder", SCRIPT_DIR / "ww_animation_canary_builder.py")
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)

WW_ANIM_XML = 0x7DF2169C
ANIM_LIST_FIELD = "animations_list"
FIELDS = [
    "animation_id",
    "animation_raw_display_name",
    "animation_display_name",
    "animation_author",
]


def _fmt_instance(e):
    i = getattr(e, "instance_id", None)
    return f"0x{i:016X}" if isinstance(i, int) else str(i)


def entry_ordinal(name):
    """从条目名 anmNNN 提取 ordinal NNN; 否则 None."""
    if not isinstance(name, str):
        return None
    if name.startswith("anm") and name[3:].isdigit():
        return int(name[3:])
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pkg")
    ap.add_argument("--start", type=int, default=295)
    ap.add_argument("--end", type=int, default=310)
    ap.add_argument("--out", default="output/story_animation_entry_index.txt")
    a = ap.parse_args()
    if a.end < a.start:
        a.end = a.start

    src = Path(a.pkg)
    if not src.is_file():
        print(f"ERROR: 源不存在 {src} (exit 2)", file=sys.stderr)
        return 2

    idx, err = wb.safe_parse(src)
    if err is not None:
        print(f"ERROR: 解析失败 {src}: {err} (exit 3)", file=sys.stderr)
        return 3

    ww = [e for e in idx.entries if getattr(e, "type_id", 0) == WW_ANIM_XML]
    if not ww:
        print(f"ERROR: 无 WW_ANIM_XML (type=0x{WW_ANIM_XML:08X}) (exit 3)", file=sys.stderr)
        return 3

    # 取第一个(通常唯一) WW_ANIM_XML
    e = ww[0]
    instance = _fmt_instance(e)
    group = getattr(e, "group_id", 0)
    type_id = getattr(e, "type_id", 0)

    try:
        body = wb.read_body_raw(src, e)
    except Exception as ex:
        print(f"ERROR: read_body_raw 失败: {ex} (exit 5)", file=sys.stderr)
        return 5
    try:
        body = wb.decompress_maybe(body)
    except Exception:
        pass
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception as ex:
        print(f"ERROR: decode 失败: {ex} (exit 5)", file=sys.stderr)
        return 5

    try:
        root = ET.fromstring(text)
    except ET.ParseError as ex:
        print(f"ERROR: XML 解析失败: {ex} (exit 5)", file=sys.stderr)
        return 5

    # 定位 <L n="animations_list">
    list_el = None
    for el in root.iter():
        if (el.tag.rsplit("}", 1)[-1] == "L"
                and el.get("n") == ANIM_LIST_FIELD):
            list_el = el
            break
    if list_el is None:
        print(f"ERROR: 无 <L n=\"{ANIM_LIST_FIELD}\"> 容器 (exit 4)", file=sys.stderr)
        return 4

    # 每个直接子 <U> = 一个动画条目, 按出现顺序编号
    entries = []
    for child in list_el:
        tag = child.tag.rsplit("}", 1)[-1] if isinstance(child.tag, str) else child.tag
        if tag != "U":
            continue
        ordinal = len(entries)  # 出现顺序, 从 0 开始
        fields = {}
        for f in FIELDS:
            fields[f] = None
        fields["_entry_name"] = child.get("n")
        for sc in child:
            sctag = sc.tag.rsplit("}", 1)[-1] if isinstance(sc.tag, str) else sc.tag
            if sctag not in ("T", "I", "E"):
                continue
            scn = sc.get("n")
            if scn in fields:
                fields[scn] = (sc.text or "").strip()
        entries.append((ordinal, child.get("n"), fields))

    out = []
    out.append("=== STORY ANIMATION ENTRY INDEX (只读) ===")
    out.append(f"package  : {src.name}")
    out.append(f"type     : 0x{type_id:08X} (WW_ANIM_XML)")
    out.append(f"group    : 0x{group:08X}")
    out.append(f"instance : {instance}")
    out.append(f"条目总数 : {len(entries)}  (ordinal 0..{len(entries)-1})")
    out.append(f"输出范围 : ordinal {a.start}..{a.end}")
    out.append("")

    want = range(a.start, a.end + 1)
    shown = 0
    for ordinal, name, fields in entries:
        if ordinal not in want:
            continue
        shown += 1
        out.append(f"=== ordinal {ordinal} ===")
        e_name = fields.get("_entry_name")
        if e_name:
            out.append(f"(entry node: <U n=\"{e_name}\">)")
        for f in FIELDS:
            v = fields.get(f)
            out.append(f"{f}: {v if v is not None else '<MISSING>'}")
        out.append("")

    if shown == 0:
        out.append(f"!! 范围内 (ordinal {a.start}..{a.end}) 无条目 (总数 {len(entries)})。")
        out.append("   若总数 < 307, 说明此包动画条目不足, ordinal 从 0 起计。")
    out.append("---")
    out.append(f"显示条目 = {shown}")
    out.append("ZERO_WRITE_TO_MODS=YES (只读)")

    out_text = "\n".join(out)
    out_path = Path(a.out)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(out_text, encoding="utf-8")
        print(f"index 已写入: {out_path}")
    except Exception as ex:
        print(f"WARN: 写文件失败: {ex}", file=sys.stderr)
    print(out_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
