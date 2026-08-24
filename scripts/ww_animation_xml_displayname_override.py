#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P27 —— WW_ANIM_XML animation_raw_display_name override package 生成

已确认翻译路线: WW_ANIM_XML -> animation_raw_display_name -> SexAnimationInstance.display_name
-> TurboLocalizedString (P27 起点, 不再查 STBL/hash/L18n)。

本脚本:
  1. 只读源 WW_Nevely42_Animations.package, 找到唯一 WW_ANIM_XML (type=0x7DF2169C)。
  2. 只替换该 XML 里指定 ordinal (默认 299-306) 的 animation_raw_display_name 字段文本,
     其余字段/其他条目【逐字节不变】(在源 XML 文本上做精确 <T n="...">OLD</T> 替换)。
  3. 不修改源 package。
  4. 生成 override package (与源 WW_ANIM_XML 同 type/group/instance),
     压缩模型/header_comp/major/minor 均 source-faithful。
  5. 输出 output/ww_p27/:
       ww_p27_displayname_override.package
       ww_p27_displayname_override.txt   (报告 + 验证)
       mapping.csv                        (ordinal, old, new)
  6. 静态验证: 重读生成包 -> type=0x7DF2169C / instance 一致 / 8 个 ordinal raw 已替换。
  7. ZERO_WRITE_TO_MODS=YES (只写 --out_dir; 绝不写 Mods)。

fail-closed: 源缺->2; 无/多 WW_ANIM_XML->3; 无 animations_list->4;
  目标 ordinal 缺失或缺 raw 字段->5; 构建/验证失败->6; 正常 0。

用法 (Windows):
  python scripts\\ww_animation_xml_displayname_override.py "WW_Nevely42_Animations.package"
      [--ordinals 299-306]
      [-t '299=抓奸 1' -t '300=抓奸 2' ...]
      [--out-dir output/ww_p27]
"""
import argparse
import csv
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import ww_animation_canary_builder as wb  # build_package / read_entry_meta_raw / read_body_raw
from ww_animation_canary_builder import (decompress_maybe, compress_like)

WW_ANIM_XML = 0x7DF2169C
ANIM_LIST_FIELD = "animations_list"
RAW_FIELD = "animation_raw_display_name"

DEFAULT_ZH = {
    299: "抓奸 1", 300: "抓奸 2", 301: "抓奸 3", 302: "抓奸 4",
    303: "抓奸 5", 304: "抓奸 6", 305: "抓奸 7", 306: "抓奸 8",
}


def parse_ordinals(s):
    """'299-306' 或逗号列表 -> sorted set[int]."""
    out = set()
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        else:
            out.add(int(part))
    return sorted(out)


def fmt_instance(e):
    i = getattr(e, "instance_id", None)
    return f"0x{i:016X}" if isinstance(i, int) else str(i)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pkg")
    ap.add_argument("--ordinals", default="299-306")
    ap.add_argument("-t", "--trans", action="append", default=[], help="ordinal=中文")
    ap.add_argument("--out-dir", default="output/ww_p27")
    a = ap.parse_args()

    ordinals = parse_ordinals(a.ordinals)
    zh = dict(DEFAULT_ZH)
    for t in a.trans:
        if "=" in t:
            k, v = t.split("=", 1)
            zh[int(k.strip())] = v.strip()
    missing_map = [o for o in ordinals if o not in zh]
    if missing_map:
        print(f"ERROR: 缺映射 ordinal={missing_map} (exit 5)", file=sys.stderr)
        return 5

    src = Path(a.pkg)
    if not src.is_file():
        print(f"ERROR: 源不存在 {src} (exit 2)", file=sys.stderr)
        return 2

    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pkg_out = out_dir / "ww_p27_displayname_override.package"
    txt_out = out_dir / "ww_p27_displayname_override.txt"
    csv_out = out_dir / "mapping.csv"

    # ---- 读源 ----
    idx, ierr = wb.safe_parse(src)
    if ierr is not None:
        print(f"ERROR: 解析失败 {src}: {ierr} (exit 3)", file=sys.stderr)
        return 3
    ww = [e for e in idx.entries if getattr(e, "type_id", 0) == WW_ANIM_XML]
    if len(ww) != 1:
        print(f"ERROR: WW_ANIM_XML 数量={len(ww)} (需恰好1) (exit 3)", file=sys.stderr)
        return 3
    e = ww[0]
    t_type, t_group, t_inst = (
        getattr(e, "type_id", 0), getattr(e, "group_id", 0),
        getattr(e, "instance_id", 0) if isinstance(getattr(e, "instance_id", None), int) else 0)
    src_inst_fmt = fmt_instance(e)

    body = wb.read_body_raw(src, e)
    plain = decompress_maybe(body)
    try:
        text = plain.decode("utf-8")
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

    # ---- 按出现顺序编号 ordinal, 采集目标 old 文本 ----
    ord_map = {}   # ordinal -> (node, old_text)
    ordinal = 0
    for child in list_el:
        tag = child.tag.rsplit("}", 1)[-1] if isinstance(child.tag, str) else child.tag
        if tag != "U":
            continue
        if ordinal in zh:
            # 找到该条目内 <T n="animation_raw_display_name">
            raw_el = None
            for sc in child:
                sctag = sc.tag.rsplit("}", 1)[-1] if isinstance(sc.tag, str) else sc.tag
                if sctag in ("T", "I", "E") and sc.get("n") == RAW_FIELD:
                    raw_el = sc
                    break
            if raw_el is None or raw_el.text is None:
                print(f"ERROR: ordinal {ordinal} 无 {RAW_FIELD} 字段 (exit 5)", file=sys.stderr)
                return 5
            ord_map[ordinal] = (raw_el, raw_el.text)
        ordinal += 1

    missing_ord = [o for o in ordinals if o not in ord_map]
    if missing_ord:
        print(f"ERROR: 目标 ordinal 不存在于 XML: {missing_ord} (总数 {ordinal}) (exit 5)", file=sys.stderr)
        return 5

    # ---- 在源 XML 文本上精确替换 <T n="...">OLD</T> (其余字节不变) ----
    new_text = text
    mapping = []
    for o in ordinals:
        node, old = ord_map[o]
        new = zh[o]
        if old == new:
            continue
        nname = node.get("n")
        if nname is None:
            print(f"ERROR: ordinal {o} raw 字段无 n 属性 (exit 5)", file=sys.stderr)
            return 5
        # 精确片段: <T n="animation_raw_display_name">OLD</T>
        frag_old = f'<T n="{nname}">{old}</T>'
        frag_new = f'<T n="{nname}">{new}</T>'
        cnt = new_text.count(frag_old)
        if cnt != 1:
            print(f"ERROR: ordinal {o} 片段 <T n=\"{nname}\">{old}</T> 出现 {cnt} 次 (需1) (exit 5)", file=sys.stderr)
            return 5
        new_text = new_text.replace(frag_old, frag_new)
        mapping.append((o, old, new))

    new_plain = new_text.encode("utf-8")
    new_body = compress_like(body, new_plain)

    # ---- source-faithful 元数据 ----
    src_major, src_minor, hdr_comp, src_meta = wb.read_entry_meta_raw(src)
    m0 = next((m for m in src_meta if m.get("type") == t_type
               and m.get("group") == t_group
               and m.get("inst") == t_inst), None)
    if m0 is None:
        m0 = src_meta[0] if src_meta else None
    if m0 is None:
        print("ERROR: 无法读取源 WW_ANIM_XML 压缩元数据 (exit 6)", file=sys.stderr)
        return 6
    meta = {
        "comp_state": bool(m0.get("size_comp")),
        "comp_type": m0.get("comp_type", 0),
        "mem_size": m0.get("mem_size", len(new_plain)),
        "offset_high_bit": int(m0.get("offset_comp", 0)),
        "size_high_bit": int(m0.get("size_comp", 0)),
    }

    # ---- 写出 override package ----
    try:
        wb.build_package([(t_type, t_group, t_inst, new_body, meta)],
                         pkg_out, header_comp=hdr_comp, major=src_major, minor=src_minor)
    except Exception as ex:
        print(f"ERROR: build_package 失败: {ex} (exit 6)", file=sys.stderr)
        return 6

    # ---- 静态验证 ----
    L = []
    L.append("=== P27 WW_ANIM_XML display_name OVERRIDE (只读源) ===")
    L.append(f"source : {src}")
    L.append(f"out    : {pkg_out}")
    L.append(f"ordinals: {ordinals}")
    L.append("")
    v_ok = True
    try:
        v_idx, v_err = wb.safe_parse(pkg_out)
        if v_err is not None:
            L.append(f"!! 重读解析失败: {v_err}")
            v_ok = False
        else:
            v_ww = [x for x in v_idx.entries if getattr(x, "type_id", 0) == WW_ANIM_XML]
            L.append(f"重读: WW_ANIM_XML 数量 = {len(v_ww)}")
            L.append(f"       (期望 1)")
            if len(v_ww) != 1:
                v_ok = False
            else:
                ve = v_ww[0]
                v_type = getattr(ve, "type_id", 0)
                v_inst = fmt_instance(ve)
                L.append(f"type     = 0x{v_type:08X} (期望 0x{WW_ANIM_XML:08X})  {'OK' if v_type == WW_ANIM_XML else 'FAIL'}")
                L.append(f"instance = {v_inst} (源 {src_inst_fmt})  "
                         f"{'OK' if v_inst == src_inst_fmt else 'FAIL'}")
                if v_type != WW_ANIM_XML or v_inst != src_inst_fmt:
                    v_ok = False
                # 重读 XML, 确认 8 个 raw 已替换
                vbody = wb.read_body_raw(pkg_out, ve)
                vplain = decompress_maybe(vbody).decode("utf-8", errors="replace")
                try:
                    vroot = ET.fromstring(vplain)
                except ET.ParseError as ex2:
                    L.append(f"!! 生成包 XML 解析失败: {ex2}")
                    v_ok = False
                    vroot = None
                if vroot is not None:
                    vl = None
                    for el in vroot.iter():
                        if el.tag.rsplit("}", 1)[-1] == "L" and el.get("n") == ANIM_LIST_FIELD:
                            vl = el
                            break
                    v_ord = 0
                    v_count = 0
                    for child in (vl if vl is not None else []):
                        tag = child.tag.rsplit("}", 1)[-1] if isinstance(child.tag, str) else child.tag
                        if tag != "U":
                            continue
                        if v_ord in zh:
                            for sc in child:
                                sctag = sc.tag.rsplit("}", 1)[-1] if isinstance(sc.tag, str) else sc.tag
                                if sctag in ("T", "I", "E") and sc.get("n") == RAW_FIELD:
                                    got = (sc.text or "").strip()
                                    want = zh[v_ord]
                                    ok = got == want
                                    if not ok:
                                        v_ok = False
                                    L.append(f"ordinal {v_ord:4d} raw = {got!r} (期望 {want!r}) {'OK' if ok else 'FAIL'}")
                                    v_count += ok
                                    break
                        v_ord += 1
                    L.append(f"已替换校验: {v_count}/{len(zh)} OK")
    except Exception as ex:
        L.append(f"验证异常: {ex}")
        v_ok = False

    L.append("")
    L.append(f"VERDICT: {'PASS' if v_ok else 'FAIL'}")
    L.append("ZERO_WRITE_TO_MODS=YES (只写 out_dir)")
    txt_out.write_text("\n".join(L), encoding="utf-8")

    with open(csv_out, "w", newline="", encoding="utf-8") as fh:
        wtr = csv.writer(fh)
        wtr.writerow(["ordinal", "old", "new"])
        for o in ordinals:
            _, old = ord_map[o]
            wtr.writerow([o, old, zh[o]])

    print(f"已写出: {pkg_out}")
    print(f"已写出: {csv_out}")
    print(f"已写出: {txt_out}")
    print(f"VERDICT: {'PASS' if v_ok else 'FAIL'}")
    if not v_ok:
        print(f"ERROR: 验证失败 (exit 6)", file=sys.stderr)
        return 6
    return 0


if __name__ == "__main__":
    sys.exit(main())
