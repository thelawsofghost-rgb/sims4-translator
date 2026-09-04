#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p28c_ascii_canary.py —— P28C FIXED mem_size + ASCII single-field canary 生成器

P27 的问题已确认为 re-introduced mem_size/field7 regression:
  ww_animation_xml_displayname_override.py:  `"mem_size": m0.get("mem_size", len(new_plain))`
  XML 改后仍沿用 source 旧 field7 (m0["mem_size"] 恒存在, len(new_plain) 只作缺失兜底, 永不生效),
  导致 field7 != 新 payload 真实解压长度 -> 加载器按错误 buffer 长度解压/读取 -> XML 资源损坏。

P28C = 新独立生成器 (不覆盖 P27/P28B/P28B0, 独立 output/ww_p28c):
  * 只读源 WW package, 定位唯一 WW_ANIM_XML (type 0x7DF2169C, instance 0x43F3438A94EDEB2B)。
  * 只改 ordinal 299 的 animation_raw_display_name -> "TEST299"; 其余所有 raw / 字段【逐字节不变】。
  * 修复 mem_size: 写出 WRITTEN_MEM_SIZE = len(decompress_maybe(new_body)) == NEW_XML_DECOMPRESSED_SIZE
    (绝不沿用 source 旧 field7); 机验 WRITTEN_MEM_SIZE == NEW_XML_DECOMPRESSED_SIZE, 否则 fail-closed。
  * source-faithful: 同 TGI / header_comp / major / minor / offset+size high bit / comp_type。
  * 生成单资源 same-TGI override (P28B-1 真机证明 single-resource same-TGI override 可被加载)。
  * 输出 output/ww_p28c/:
      WW_P28C_TEST299_Override.package
      ww_p28c_report.txt
      mapping.csv            (ordinal, old, new)
  * ZERO_WRITE_TO_MODS=YES。

机验键 (ASCII):
  TARGET_ORDINAL=299
  TARGET_NEW_RAW=TEST299
  SOURCE_MEM_SIZE=...                  (P27 曾错误沿用的源旧 field7)
  NEW_XML_DECOMPRESSED_SIZE=...        (新解压实际长度)
  WRITTEN_MEM_SIZE=...                 (写进包的真实 field7)
  MEM_SIZE_MATCH_NEW_XML=YES|NO        (WRITTEN == NEW_XML_DECOMPRESSED_SIZE)
  TARGETS_CHANGED=1/1
  NON_TARGET_XML_DIFF=0
  WW_ANIM_XML_COUNT=1 / TYPE / GROUP / INSTANCE / TGI_MATCH=YES
  VERDICT=PASS|FAIL

fail-closed 退出码: 2=IO/参数, 3=解析/单XML/目标ordinal/替换/mem_size校验 FAIL; 0=PASS。

用法 (Windows 真机):
  python scripts\\ww_p28c_ascii_canary.py --source "C:\\Users\\thela\\Documents\\Electronic Arts\\The Sims 4\\Mods\\2026.7.20\\WW_Nevely42_Animations.package" [--force]
"""
import argparse
import csv
import hashlib
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
for _p in (WORKSPACE / "src", Path(__file__).resolve().parent):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

from ww_animation_canary_builder import (  # noqa: E402
    build_package,
    decompress_maybe,
    compress_like,
    read_body_raw,
    read_entry_meta_raw,
    safe_parse,
    sha256,
)

WW_ANIM_XML = 0x7DF2169C
EXPECTED_INSTANCE = 0x43F3438A94EDEB2B
ANIM_LIST_FIELD = "animations_list"
RAW_FIELD = "animation_raw_display_name"

TARGET_ORDINAL = 299
TARGET_NEW_RAW = "TEST299"


def fmt_inst(i):
    return f"0x{i:016X}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="源 WW package (只读)")
    ap.add_argument("--out-dir", default="output", help="输出根目录 (默认 output)")
    ap.add_argument("--force", action="store_true", help="artifact 已存在时覆盖 (默认 fail-closed 拒写)")
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: source 不存在: {src} (exit 2)", file=sys.stderr)
        return 2

    out_dir = Path(a.out_dir) / "ww_p28c"
    out_pkg = out_dir / "WW_P28C_TEST299_Override.package"
    out_report = out_dir / "ww_p28c_report.txt"
    csv_out = out_dir / "mapping.csv"

    for p in (out_pkg, out_report, csv_out):
        if p.exists() and not a.force:
            print(f"ERROR: artifact 已存在 (拒绝覆盖, 用 --force): {p} (exit 2)", file=sys.stderr)
            return 2

    src_sha = sha256(src)
    idx, err = safe_parse(src)
    if err is not None or idx is None:
        print(f"ERROR: source 解析失败: {err} (exit 3)", file=sys.stderr)
        return 3

    ww = [e for e in idx.entries if getattr(e, "type_id", 0) == WW_ANIM_XML]
    if len(ww) != 1:
        print(f"ERROR: 需单 WW_ANIM_XML; 实际 = {len(ww)} (fail-closed, exit 3)", file=sys.stderr)
        return 3
    wxml = ww[0]
    t = getattr(wxml, "type_id", 0)
    g = getattr(wxml, "group_id", 0)
    inst = getattr(wxml, "instance_id", None)
    if inst != EXPECTED_INSTANCE:
        print(f"ERROR: WW_XML instance {fmt_inst(inst)} != 权威 {fmt_inst(EXPECTED_INSTANCE)} (exit 3)", file=sys.stderr)
        return 3

    src_body = read_body_raw(src, wxml)
    plain = decompress_maybe(src_body)
    try:
        text = plain.decode("utf-8")
    except Exception as ex:
        print(f"ERROR: 源 XML decode 失败: {ex} (exit 3)", file=sys.stderr)
        return 3
    try:
        root = ET.fromstring(text)
    except ET.ParseError as ex:
        print(f"ERROR: 源 XML parse 失败: {ex} (exit 3)", file=sys.stderr)
        return 3

    # 定位 animations_list
    list_el = None
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "L" and el.get("n") == ANIM_LIST_FIELD:
            list_el = el
            break
    if list_el is None:
        print(f"ERROR: 无 <L n=\"{ANIM_LIST_FIELD}\"> (exit 3)", file=sys.stderr)
        return 3

    # 按出现顺序编号 U 条目 => ordinal; 仅对 target ordinal 记录其 raw 元素 (其余不动)
    ordinal = 0
    target_el = None
    target_old = None
    all_raw_vals = {}  # ordinal -> raw text (供 NON_TARGET 逐项比对)
    for child in list_el:
        tag = child.tag.rsplit("}", 1)[-1] if isinstance(child.tag, str) else child.tag
        if tag != "U":
            continue
        raw_el = None
        for sc in child:
            sctag = sc.tag.rsplit("}", 1)[-1] if isinstance(sc.tag, str) else sc.tag
            if sctag in ("T", "I", "E") and sc.get("n") == RAW_FIELD:
                raw_el = sc
                break
        if raw_el is None or raw_el.text is None:
            print(f"ERROR: ordinal {ordinal} 无 {RAW_FIELD} 文本 (exit 3)", file=sys.stderr)
            return 3
        if ordinal == TARGET_ORDINAL:
            target_el = raw_el
            target_old = raw_el.text
        all_raw_vals[ordinal] = raw_el.text
        ordinal += 1

    if target_el is None:
        print(f"ERROR: 目标 ordinal {TARGET_ORDINAL} 不存在于 XML (总条目 {ordinal}) (exit 3)", file=sys.stderr)
        return 3
    if target_old == TARGET_NEW_RAW:
        print(f"ERROR: ordinal {TARGET_ORDINAL} 已是 {TARGET_NEW_RAW}, 无需改 (exit 3)", file=sys.stderr)
        return 3

    # 在源 XML 文本上做仅一处精确替换 <T n="animation_raw_display_name">OLD</T> -> TEST299 (其余字节不变)
    nname = target_el.get("n")
    frag_old = f'<T n="{nname}">{target_old}</T>'
    frag_new = f'<T n="{nname}">{TARGET_NEW_RAW}</T>'
    cnt = text.count(frag_old)
    if cnt != 1:
        print(f"ERROR: 片段出现 {cnt} 次 (需1): {frag_old!r} (exit 3)", file=sys.stderr)
        return 3
    new_text = text.replace(frag_old, frag_new)

    new_plain = new_text.encode("utf-8")
    new_body = compress_like(src_body, new_plain)
    # 修复 mem_size: 绝不能沿用 source field7; 用新解压实际长度
    new_xml_decompressed_size = len(decompress_maybe(new_body))
    written_mem_size = new_xml_decompressed_size
    mem_match = True  # by construction; 下方重读包再机验一次

    src_major, src_minor, hdr_comp, src_meta = read_entry_meta_raw(src)
    m = None
    for _m in src_meta:
        if _m["type"] == t and _m["group"] == g and _m["inst"] == inst:
            m = _m
            break
    if m is None:
        print("ERROR: 源 index metadata 无 WW XML 条目 (exit 3)", file=sys.stderr)
        return 3
    source_mem_size = m["mem_size"]  # P27 曾错误沿用此旧 field7
    xml_meta = {
        "comp_state": bool(m["size_comp"]),
        "comp_type": m["comp_type"],
        "mem_size": written_mem_size,  # 修复: 真实新解压长度
        "offset_high_bit": int(m["offset_comp"]),
        "size_high_bit": int(m["size_comp"]),
    }

    items = [(t, g, inst, new_body, xml_meta)]
    build_package(items, out_pkg, header_comp=hdr_comp, major=src_major, minor=src_minor)
    out_sha = sha256(out_pkg)

    # ---------- 机验 1: 重读包 -> mem_size 实际写值 + TGI + 单XML ----------
    idx2, err2 = safe_parse(out_pkg)
    parser_ok = (err2 is None and idx2 is not None)
    ww2 = [e for e in (idx2.entries if idx2 else []) if getattr(e, "type_id", 0) == WW_ANIM_XML]
    count_ok = len(ww2) == 1
    tgi_ok = False
    written_meta = None
    if count_ok:
        e2 = ww2[0]
        g2 = getattr(e2, "group_id", 0)
        inst2 = getattr(e2, "instance_id", None)
        tgi_ok = (g2 == g) and (inst2 == inst)
        # 从 index 原始 32 字节取真实 field7
        _, _, _, out_meta = read_entry_meta_raw(out_pkg)
        if out_meta:
            written_meta = out_meta[0]
    written_field7 = written_meta["mem_size"] if written_meta else -1
    mem_match_write = (written_field7 == new_xml_decompressed_size)

    # ---------- 机验 2: NON_TARGET_XML_DIFF=0 ----------
    # 读回生成包 WW XML, 逐条目 raw 与源比; 除 ordinal 299 外必须全等
    b2 = read_body_raw(out_pkg, ww2[0]) if count_ok else b""
    non_target_diff = -1
    new_pkg_plain = decompress_maybe(b2).decode("utf-8", errors="replace")
    try:
        vroot = ET.fromstring(new_pkg_plain)
        vl = None
        for el in vroot.iter():
            if el.tag.rsplit("}", 1)[-1] == "L" and el.get("n") == ANIM_LIST_FIELD:
                vl = el
                break
        v_ord = 0
        diff = 0
        for child in (vl if vl is not None else []):
            tag = child.tag.rsplit("}", 1)[-1] if isinstance(child.tag, str) else child.tag
            if tag != "U":
                continue
            for sc in child:
                sctag = sc.tag.rsplit("}", 1)[-1] if isinstance(sc.tag, str) else sc.tag
                if sctag in ("T", "I", "E") and sc.get("n") == RAW_FIELD:
                    got = sc.text or ""
                    src_val = all_raw_vals.get(v_ord)
                    if v_ord == TARGET_ORDINAL:
                        if got != TARGET_NEW_RAW:
                            diff += 1
                    else:
                        if got != src_val:
                            diff += 1
                    break
            v_ord += 1
        non_target_diff = diff
    except ET.ParseError as ex:
        print(f"ERROR: 生成包 XML 解析失败: {ex} (exit 3)", file=sys.stderr)
        return 3

    non_target_zero = (non_target_diff == 0)
    targets_changed = 1  # 恰好 ordinal 299 改成 TEST299

    verdict_ok = (parser_ok and count_ok and tgi_ok and mem_match_write
                  and non_target_zero and targets_changed == 1)

    report = []
    report.append("WW_P28C_ASCII_CANARY")
    report.append("ZERO_WRITE_TO_MODS=YES")
    report.append(f"SOURCE_PKG={src}")
    report.append(f"SOURCE_SHA256={src_sha}")
    report.append(f"OVERRIDE_PKG={out_pkg}")
    report.append(f"OVERRIDE_PKG_SHA256={out_sha}")
    report.append(f"WW_ANIM_XML_COUNT={len(ww2)}")
    report.append(f"TYPE=0x{t:08X}")
    report.append(f"GROUP=0x{g:08X}")
    report.append(f"INSTANCE={fmt_inst(inst)}")
    report.append(f"TGI_MATCH={'YES' if tgi_ok else 'NO'}")
    report.append(f"TARGET_ORDINAL={TARGET_ORDINAL}")
    report.append(f"TARGET_NEW_RAW={TARGET_NEW_RAW}")
    report.append(f"TARGETS_CHANGED={targets_changed}/{sum(1 for _ in all_raw_vals) or 1}")
    report.append(f"NON_TARGET_XML_DIFF={non_target_diff}")
    report.append(f"SOURCE_MEM_SIZE={source_mem_size}")
    report.append(f"NEW_XML_DECOMPRESSED_SIZE={new_xml_decompressed_size}")
    report.append(f"WRITTEN_MEM_SIZE={written_field7}")
    report.append(f"MEM_SIZE_MATCH_NEW_XML={'YES' if mem_match_write else 'NO'}")
    memo_src_new_equal = (source_mem_size == new_xml_decompressed_size)
    mem_regression_fixed = (mem_match_write and not memo_src_new_equal)  # 源旧值确已陈旧且已改正
    report.append(f"MEM_SIZE_REGRESSION_FIXED={'YES' if mem_regression_fixed else ('NO_CHANGE' if memo_src_new_equal else 'NO')}")
    report.append(f"VERDICT={'PASS' if verdict_ok else 'FAIL'}")

    out_dir.mkdir(parents=True, exist_ok=True)
    out_report.write_text("\n".join(report) + "\n", encoding="utf-8")
    with open(csv_out, "w", newline="", encoding="utf-8") as fh:
        wtr = csv.writer(fh)
        wtr.writerow(["ordinal", "old", "new"])
        wtr.writerow([TARGET_ORDINAL, target_old, TARGET_NEW_RAW])

    for ln in report:
        print(ln)
    if not verdict_ok:
        print("ERROR: 生成/机验 FAIL (exit 3)", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
