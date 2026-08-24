#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
story_display_name_patch_test.py —— 只读: 验证 WW_ANIM_XML 的 animation_raw_display_name 是否直接控制显示

只读分析 WW_Nevely42_Animations.package (type=0x7DF2169C WW_ANIM_XML)。
不修改原包。不做 ts4script 扫描; 不反汇编; 只分析 XML 字段关系。

目标(ordinal 299-306 的每条):
  1. resource instance
  2. animation_raw_display_name
  3. 是否存在其他 display_name 字段 (animation_display_name / display_name / *_display_*)
  4. 是否存在 hash / string key 字段 (hash / key / *string*id* / *localization* / *loc*)

并给建议: 修改 XML 的 animation_raw_display_name 是否会影响 SexAnimationInstance.display_name。
(依据: story_display_name_source_trace 已证 SexAnimationInstance.__init__ 由
 animation_raw_display_name 参数赋值 display_name 并喂给 TurboLocalizedString;
 若 XML 字段名即该参数名, 则直接控制。)

输出 output/story_display_name_patch_test.txt。不改原包。

fail-closed / 只读: 源缺->2; 无 WW_ANIM_XML->3; 无 animations_list->4; 解析异常->5; 正常 0。
ZERO_WRITE_TO_MODS=YES。

用法 (Windows):
  python scripts\story_display_name_patch_test.py "WW_Nevely42_Animations.package"
      [--start 299] [--end 306] [--out output/story_display_name_patch_test.txt]
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

RAW_FIELD = "animation_raw_display_name"
# 其他 display 相关字段(可能存在的孪生字段)
DISPLAY_SIBLINGS = ("display_name", "animation_display_name", "display", "display_text",
                    "display_key", "display_string", "visible_name", "name")
# hash / string key 相关字段
HASH_KEY_FIELDS = ("hash", "string_hash", "hash_key", "key", "localization_key",
                   "localized_string", "localized_string_id", "loc_key", "string_key",
                   "stbl", "text_key", "name_key", "display_hash")


def _fmt_instance(e):
    i = getattr(e, "instance_id", None)
    return f"0x{i:016X}" if isinstance(i, int) else str(i)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pkg")
    ap.add_argument("--start", type=int, default=299)
    ap.add_argument("--end", type=int, default=306)
    ap.add_argument("--out", default="output/story_display_name_patch_test.txt")
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

    list_el = None
    for el in root.iter():
        if (el.tag.rsplit("}", 1)[-1] == "L"
                and el.get("n") == ANIM_LIST_FIELD):
            list_el = el
            break
    if list_el is None:
        print(f"ERROR: 无 <L n=\"{ANIM_LIST_FIELD}\"> 容器 (exit 4)", file=sys.stderr)
        return 4

    # 按出现顺序编号 ordinal(与 entry_index 一致)
    enames = []
    entry_nodes = []
    for child in list_el:
        tag = child.tag.rsplit("}", 1)[-1] if isinstance(child.tag, str) else child.tag
        if tag != "U":
            continue
        ordinal = len(enames)
        enames.append(ordinal)
        entry_nodes.append(child)

    out = []
    out.append("=== STORY DISPLAY_NAME PATCH TEST (只读) ===")
    out.append("目的: 验证 animation_raw_display_name 是否直接控制显示 (不修改原包)")
    out.append(f"package  : {src.name}")
    out.append(f"type     : 0x{type_id:08X} (WW_ANIM_XML)")
    out.append(f"group    : 0x{group:08X}")
    out.append(f"instance : {instance}")
    out.append(f"条目总数 : {len(enames)} (ordinal 0..{len(enames)-1})")
    out.append(f"分析范围 : ordinal {a.start}..{a.end}")
    out.append("")

    want = set(range(a.start, a.end + 1))
    shown = 0
    raw_only = 0       # 只有 raw, 无孪生 display 字段
    has_sibling = 0    # 存在其他 display 字段
    has_hashkey = 0    # 存在 hash/string key 字段

    for ordinal, node in zip(enames, entry_nodes):
        if ordinal not in want:
            continue
        shown += 1
        out.append(f"=== ordinal {ordinal} ===")
        e_name = node.get("n")
        if e_name:
            out.append(f"(entry node: <U n=\"{e_name}\">)")
        out.append(f"resource instance : {instance}")
        out.append("")

        # 收集该条目所有标量字段名 -> 值
        field_map = {}
        for sc in node:
            sctag = sc.tag.rsplit("}", 1)[-1] if isinstance(sc.tag, str) else sc.tag
            if sctag not in ("T", "I", "E"):
                continue
            scn = sc.get("n")
            if scn:
                field_map.setdefault(scn, (sc.text or "").strip())

        raw_val = field_map.get(RAW_FIELD)
        out.append(f"animation_raw_display_name : {raw_val if raw_val is not None else '<MISSING>'}")

        # 其他 display 相关字段
        sib = {}
        for fname, fval in field_map.items():
            fl = fname.lower()
            if fname != RAW_FIELD and any(k in fl for k in ("display", "visible_name")):
                sib[fname] = fval
        out.append(f"是否存在其他 display_name 字段 : {'是 -> ' if sib else '否'}")
        if sib:
            for fname, fval in sib.items():
                out.append(f"    {fname} = {fval}")
        else:
            out.append("    (无 animation_display_name / display_name / name 等孪生显示字段)")
        if sib:
            has_sibling += 1
        else:
            raw_only += 1

        # hash / string key 相关
        hk = {}
        for fname, fval in field_map.items():
            fl = fname.lower()
            if any(k in fl for k in HASH_KEY_FIELDS):
                hk[fname] = fval
        out.append(f"是否存在 hash / string key 字段 : {'是 -> ' if hk else '否'}")
        if hk:
            for fname, fval in hk.items():
                out.append(f"    {fname} = {fval}")
        else:
            out.append("    (本条目光标无 hash/string-key 字段; raw 为纯文本)")
        if hk:
            has_hashkey += 1

        # 建议
        out.append("")
        out.append("建议(改 raw 是否影响 SexAnimationInstance.display_name):")
        if raw_val is None:
            out.append("    ⚠ 本条无 animation_raw_display_name, 不存在可改的 raw 文本。")
        elif hk:
            out.append("     ⚠ 本条除 raw 外还有 hash/string-key 字段 — 显示可能走 key 而非 raw,"
                       "单改 raw 不保证生效(需进一步确认该 key 的消费方)。")
        else:
            out.append("     ✓ raw 为唯一显示文本源, SexAnimationInstance.__init__ 直接以"
                       "animation_raw_display_name 赋值 display_name 并喂 TurboLocalizedString;"
                       "改此 raw 即可直接改变显示名(前提: XML 字段名即该参数名)。")
        out.append("")
        out.append("-" * 70)
        out.append("")

    if shown == 0:
        out.append(f"!! 范围内 (ordinal {a.start}..{a.end}) 无条目 (总数 {len(enames)})。")
        out.append("   若总数 < 308, 说明此包动画条目不足, ordinal 从 0 起计。")
    out.append("---")
    out.append(f"显示/分析条目 = {shown}")
    out.append(f"其中: 仅 raw 文本(可直接改) = {raw_only}; 含其他 display 字段 = {has_sibling}; 含 hash/key 字段 = {has_hashkey}")
    out.append("")
    out.append("结论: 若某条 仅含 raw、无 hash/key 孪生字段, 且 display 消费走该 raw,"
               "则改动 animation_raw_display_name 会直接反映到 SexAnimationInstance.display_name。")
    out.append("ZERO_WRITE_TO_MODS=YES (只读)")

    out_text = "\n".join(out)
    out_path = Path(a.out)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(out_text, encoding="utf-8")
        print(f"已写入: {out_path}")
    except Exception as ex:
        print(f"WARN: 写文件失败: {ex}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
