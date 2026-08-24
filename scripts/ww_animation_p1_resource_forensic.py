#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P1 取证 — package/resource 级只读分析: 为什么复制 WW XML instance 后
  WW runtime 仍不认它是合法 Story animation?

已排除 (真机仍 NOT Caught Cheating): raw_display / stage_name / next_stages /
  clip_name[] / (M5: animation_id 根本不在 WW_ANIM_XML, 系其它资源字段)。
结论: Story identity 注册键不在 WW_ANIM_XML 内部, 而在【外部资源】层。

本工具对【源真机 package】做全资源只读取证:
  1. 枚举全部资源 (type/group/instance/size/comp) + content 签名
     -> 找出 WW_ANIM_XML 的所有关联资源 (同包其它 type)
  2. 对每个非 WW 资源: 解码为可读文本(XML/STBL/ascii), 判定其是否含
     动画引用线索: 'Caught Cheating' / 'nevely' / 动画名 / WW XML instance
     TGI / 数字 ordinal / 哈希样式串
  3. 生成 resource inventory + reference map
  4. 对比 124(Addicted, 正常) vs 299(Caught Cheating, 失败) 的引用资源差异
     -> 目标: 找到 Story 注册表 / mapping / index / tuning / STBL / 其它引用

只读。不生成 package / 不改 writer / 不碰 Mods (ZERO_WRITE_TO_MODS=YES)。
用法 (Windows, 只读源包):
  python scripts/ww_animation_p1_resource_forensic.py "<SRC.package>" \
      [--ordinals 124 125 126 299 300 301 302 303 304 305 306] [--out-dir output/ww_p1]
"""
import argparse
import csv
import hashlib
import importlib.util
import re
import struct
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
import ww_animation_m2_diff_forensic as _diff  # 复用 _locate_animations_list/_entry_blocks/_collect_fields

WW_ANIM_XML = 0x7DF2169C
STBL = 0x220557DA
ANIM_RCOL = 0xBC4A5044
CLIP = 0x6B20C4F3
OUT_DIR = Path("output/ww_p1")

KNOWN_TYPES = {
    WW_ANIM_XML: "WW_ANIM_XML",
    STBL: "STBL(str-table)",
    ANIM_RCOL: "ANIM_RCOL(clip-rcol)",
    CLIP: "CLIP",
    0x00B2D882: "0x00B2D882(?tuning/catalog)",
    0x545AC2C2: "0x545AC2C2(?tuning)",
    0x034AEECB: "0x034AEECB(?registry)",
    0x073FAA27: "0x073FAA27(?tuning/index)",
}

INTRIGUING = ("545AC2C2", "034AEECB", "073FAA27", "00B2D882", "220557DA", "BC4A5044")


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else None


def hexI(x):
    return f"0x{x:016X}" if x > 0xFFFFFFFF else f"0x{x:08X}"


# ---------- 资源解码 ----------
def decode_body(raw: bytes):
    """返回 (kind, text)。kind: xml/tuning / stbl / anim_xml / binary-ish / empty。"""
    if not raw:
        return "empty", ""
    if raw.startswith(b"DBPF"):
        return "nested-dbpf", ""
    # 先试 UTF-8 文本
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # STBL 二进制字符串表: 扫 ascii 可读片段
        frags = []
        for m in re.finditer(rb'[\x20-\x7e]{3,}', raw):
            frags.append(m.group().decode("ascii", "replace"))
        return "binary", "\n".join(frags[:400])
    if "<" in text and ">" in text and ("<U" in text or "<T" in text or "<L" in text or "<?xml" in text):
        return "xml", text
    return "ascii", text


def class_refs(text, lower, ord_idx, inst_tgis):
    """在解码文本里找动画引用线索。返回命中列表。"""
    hits = []
    if "caught cheating" in lower or "addicted" in lower:
        hits.append("动画名(en)")
    if "nevely" in lower:
        hits.append("作者/前缀 nevely")
    for tgi in inst_tgis:
        if tgi in lower:
            hits.append(f"WW XML instance {tgi}")
    return hits


def parse_xml_refs(text):
    """对 xml/tuning 资源: 找含动画字段名 / n= 属性, 粗分类。"""
    refs = []
    try:
        root = ET.fromstring(text)
    except Exception:
        return refs
    for el in root.iter():
        n = el.get("n")
        if not n:
            continue
        if any(k in n.lower() for k in ("anim", "story", "id", "hash", "key", "clip", "file", "tuning", "msd")):
            refs.append(n)
    return refs


# ---------- 主流程 ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="源 WW package")
    ap.add_argument("--ordinals", nargs="*", type=int, default=[124, 125, 126, 299, 300, 301, 302, 303, 304, 305, 306],
                    help="要对比的 target ordinal (缺省 Addicted 124-126 + Caught Cheating 299-306)")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: 源不存在 {src}", file=sys.stderr); return 2
    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    idx, err = wb.safe_parse(src)
    if err is not None or idx is None:
        print(f"ERROR: 源解析失败 {err}", file=sys.stderr); return 3
    entries = idx.entries

    # 1) 资源清单
    rows = []          # csv: type, group, inst, size, comp, kind, refs, fields
    inv_lines = ["=== P1 资源级取证: 全部资源清单 ==="]

    ww_first = next((e for e in entries if e.type_id == WW_ANIM_XML), None)
    # 记录每个 WW XML instance 的 hex 串(小写), 供全文引用搜索
    ww_inst_hex = {f"0x{e.instance_id:016x}" for e in entries if e.type_id == WW_ANIM_XML}
    # 也搜源 WW XML 里的 ordinal 显示名
    src_disp_names = {}   # ordinal -> 显示名
    ord_blocks = {}       # ordinal -> entry block

    if ww_first is not None:
        body = wb.read_body_raw(src, ww_first)
        _s, xml_text, xerr = wb.parse_anim_xml(body)
        if xerr is None:
            inner, _st, _en = _diff._locate_animations_list(xml_text)
            ei = 0
            for b, _r in _diff._entry_blocks(inner):
                if b.lstrip().startswith("<U"):
                    f = _diff._collect_fields(b)
                    dv = f.get("animation_raw_display_name", [{}])
                    src_disp_names[ei] = (dv[0].get("val", "") if dv and isinstance(dv[0], dict) else "")
                    ord_blocks[ei] = b
                    ei += 1

    # 引用搜索词(小写)
    want_disp = {o: (src_disp_names.get(o, "") or "").lower() for o in a.ordinals if o in src_disp_names}
    want_names = set()
    for d in want_disp.values():
        for tok in d.split():
            if len(tok) >= 4:
                want_names.add(tok)

    ref_hit = {}        # (type,group,inst) -> list[str]
    ref_fields = {}     # (type,group,inst) -> list[str] (xml n= 字段名)
    kind_of = {}
    size_of = {}
    comp_of = {}
    body_text_of = {}   # key -> 小写体文本 (供引用搜索)

    for e in entries:
        key = (e.type_id, e.group_id, e.instance_id)
        raw = wb.read_body_raw(src, e)
        body = wb.decompress_maybe(raw)
        kind, text = decode_body(body)
        kind_of[key] = kind
        size_of[key] = len(body)
        comp_of[key] = raw != body  # compressed?
        body_text_of[key] = text.lower()

        lower = text.lower()
        hits = []
        # 动画显示名 / 系列名
        if any(w in lower for w in want_names) or any(d in lower for d in want_disp.values()):
            hits.append("动画显示名")
        if "caught cheating" in lower:
            hits.append("系列名 Caught Cheating")
        if "addicted" in lower:
            hits.append("系列名 Addicted")
        if "nevely" in lower:
            hits.append("作者 Nevely42")
        for tgi in ww_inst_hex:
            if tgi in lower:
                hits.append(f"引用 WW-XML instance {tgi}")
        ref_hit[key] = hits

        if kind == "xml":
            fields = parse_xml_refs(text)
            if fields:
                ref_fields[key] = fields

        tname = KNOWN_TYPES.get(e.type_id, f"0x{e.type_id:08X}")
        inv_lines.append(
            f"  {tname:28s} {hexI(e.group_id):12s} {hexI(e.instance_id):18s} "
            f"size={len(body):7d} comp={1 if raw!=body else 0} kind={kind:10s} refs={','.join(hits) or '-'}")

    # 2) 资源类型分组统计
    by_type = {}
    for e in entries:
        by_type.setdefault(e.type_id, []).append(e)
    inv_lines.append("\n=== 按类型分组 ===")
    for tid, lst in sorted(by_type.items()):
        tname = KNOWN_TYPES.get(tid, f"0x{tid:08X}")
        inv_lines.append(f"  {tname:28s} count={len(lst):3d}")

    # 3) 引用命中表 (非 WW 资源)
    inv_lines.append("\n=== 含动画引用的非 WW 资源 (可疑注册表/mapping/index) ===")
    ref_nonww = {k: v for k, v in ref_hit.items() if k[0] != WW_ANIM_XML and v}
    if not ref_nonww:
        inv_lines.append("  (无 —— 没有非 WW 资源引用动画名/instance)")
    for (tid, gid, iid), hits in sorted(ref_nonww.items(), key=lambda kv: kv[0]):
        inv_lines.append(f"  {KNOWN_TYPES.get(tid, f'0x{tid:08X}'):28s} {hexI(iid):18s} hits={hits}")
        if ref_fields.get((tid, gid, iid)):
            inv_lines.append(f"      xml字段: {ref_fields[(tid, gid, iid)][:30]}")

    # 4) 关联资源: 对每个被关注 ordinal, 看哪些资源引用了它的显示名
    inv_lines.append("\n=== ordinal -> 引用它的资源 (仅显示名级) ===")
    for o in a.ordinals:
        disp = (src_disp_names.get(o) or "").strip()
        refs_here = []
        if disp:
            dl = disp.lower()
            for (tid, gid, iid), bl in body_text_of.items():
                if tid != WW_ANIM_XML and dl in bl:
                    refs_here.append(f"{KNOWN_TYPES.get(tid, f'0x{tid:08X}')}/{hexI(iid)}")
        inv_lines.append(f"  ordinal {o} '{disp}': " + (", ".join(refs_here) if refs_here else "仅 WW_ANIM_XML 内部, 无外部资源引用"))

    # 5) 124 vs 299 结构差异小结
    inv_lines.append("\n=== 124(Addicted, 正常) vs 299(Caught Cheating, 失败) WW_ANIM_XML entry 字段差异 ===")
    cmp_ord = [o for o in a.ordinals if o in (124, 126, 299, 300, 306)]
    fld_of_ord = {}
    for o in cmp_ord:
        b = ord_blocks.get(o)
        if b is None:
            inv_lines.append(f"  ordinal {o}: (取不到 entry)")
            continue
        flds = _diff._collect_fields(b)
        fld_of_ord[o] = set(flds.keys())
        inv_lines.append(f"  ordinal {o} '{src_disp_names.get(o,'')}': 字段 {sorted(flds.keys())}")
    if 124 in fld_of_ord and 299 in fld_of_ord:
        inv_lines.append("  --- 124 有而 299 无 的字段 (Addicted 特有=可能含注册键) ---")
        only124 = fld_of_ord.get(124, set()) - fld_of_ord.get(299, set())
        if only124:
            for fname in sorted(only124):
                inv_lines.append(f"      {fname}")
        else:
            inv_lines.append("      (无)")
        inv_lines.append("  --- 299 有而 124 无 的字段 (Story 特有=可能含 identity key) ---")
        only299 = fld_of_ord.get(299, set()) - fld_of_ord.get(124, set())
        if only299:
            for fname in sorted(only299):
                inv_lines.append(f"      {fname}")
        else:
            inv_lines.append("      (无)")
    txt = "\n".join(inv_lines) + "\n"
    (out_dir / "p1_resource_inventory.txt").write_text(txt, encoding="utf-8")
    with open(out_dir / "p1_resource_inventory.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["type", "type_name", "group", "instance", "size", "compressed", "kind", "refs"])
        for e in entries:
            key = (e.type_id, e.group_id, e.instance_id)
            w.writerow([hexI(e.type_id), KNOWN_TYPES.get(e.type_id, "?"), hexI(e.group_id),
                        hexI(e.instance_id), size_of.get(key, 0), comp_of.get(key, 0),
                        kind_of.get(key, ""), "|".join(ref_hit.get(key, []))])
    print(txt)
    print(f"OUT_TXT={out_dir / 'p1_resource_inventory.txt'}")
    print(f"OUT_CSV={out_dir / 'p1_resource_inventory.csv'}")
    print("P1_RESOURCE_FORENSIC=OK (只读, 未生成包)")
    print("ZERO_WRITE_TO_MODS=YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
