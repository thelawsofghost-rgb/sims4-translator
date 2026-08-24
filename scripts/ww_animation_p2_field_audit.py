#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P2 取证 — WW_ANIM_XML 字段值审计 (只读)。

目标: 找出 Story runtime 判断依据。dump ordinal 124/126/299/306 的
  以下字段【真实值】:
    animation_type
    animation_category
    animation_tags
    animation_events_list
    animation_next_stages
    animation_stage_name
    animation_raw_display_name
    animation_clip_name
  并输出 124 vs 299 的字段值差异表。

注意: 字段可能存在/缺失/形态不同(L/T/E/I/U); 本工具如实输出存在性与值,
  绝不臆造。ZERO_WRITE_TO_MODS=YES (只读, 不生成包)。

用法 (Windows, 只读源):
  python scripts/ww_animation_p2_field_audit.py "<SRC.package>" \
      [--ordinals 124 126 299 306] [--out-dir output/ww_p2]
"""
import argparse
import csv
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
import ww_animation_m2_diff_forensic as _diff

WW_ANIM_XML = 0x7DF2169C
OUT_DIR = Path("output/ww_p2")

# 目标字段 (存在性 + 真实值)
TARGET_FIELDS = [
    "animation_type",
    "animation_category",
    "animation_tags",
    "animation_events_list",
    "animation_next_stages",
    "animation_stage_name",
    "animation_raw_display_name",
    "animation_clip_name",
]


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else None


def _node_detail(root, n):
    """取名为 n 的全部节点, 深挖值。返回 list[node_detail dict]。
    对 T/E/I: val + tag; 对 L: child list (每 child 的 n/tag/val);
    对 U: 内部每个子字段名。"""
    out = []
    for el in root.iter():
        if el.get("n") == n:
            lt = _local(el.tag)
            if lt in ("T", "E", "I"):
                out.append({"tag": lt, "val": (el.text or "").strip()})
            elif lt == "L":
                kids = []
                for c in el:
                    clt = _local(c.tag)
                    cv = (c.text or "").strip() if clt in ("T", "E", "I") else \
                        (f"[{len(list(c))} items]" if clt in ("L",) else "[obj]")
                    kids.append({"n": c.get("n"), "tag": clt, "val": cv})
                out.append({"tag": "L", "val": kids})
            elif lt == "U":
                sub = [(c.get("n"), _local(c.tag)) for c in el.iter() if c is not el and c.get("n")]
                out.append({"tag": "U", "val": sub})
            else:
                out.append({"tag": lt, "val": (el.text or "").strip()})
    return out


def _fmt_detail(d):
    """把 node_detail 转成可读字符串 (单行, 便于 diff/比对)。"""
    if d["tag"] == "L":
        if not d["val"]:
            return "L[]"
        parts = []
        for k in d["val"]:
            if k["tag"] in ("T", "E", "I"):
                parts.append(f"{k['n'] or ''}={k['val']!r}")
            else:
                parts.append(f"{k['n'] or ''}={k['tag']}/{[i['n'] for i in (k['val'] if isinstance(k['val'], list) else []) ]}")
        return "L[" + ", ".join(parts) + "]"
    if d["tag"] == "U":
        return "U{" + ", ".join(f"{nn}={tt}" for nn, tt in d["val"]) + "}"
    return f"{d['tag']}({d['val']!r})"


def _collect_ordinals(src):
    """返回 ordinal -> block text。只读。"""
    idx, err = wb.safe_parse(src)
    if err is not None or idx is None:
        return None, [], f"解析失败 {err}"
    ww = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    if not ww:
        return None, [], "无 WW_ANIM_XML"
    body = wb.read_body_raw(src, ww[0])
    _s, xml_text, xerr = wb.parse_anim_xml(body)
    if xerr is not None:
        return None, [], f"WW XML 解析失败 {xerr}"
    inner, _st, _en = _diff._locate_animations_list(xml_text)
    om = {}
    ei = 0
    for b, _r in _diff._entry_blocks(inner):
        if b.lstrip().startswith("<U"):
            om[ei] = b
            ei += 1
    return om, [e.instance_id for e in ww], None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="源 WW package")
    ap.add_argument("--ordinals", nargs="*", type=int, default=[124, 126, 299, 306],
                    help="要 dump 的 ordinal (缺省 124 126 299 306)")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: 源不存在 {src}", file=sys.stderr); return 2
    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    ord_blocks, _insts, err = _collect_ordinals(src)
    if err is not None:
        print(f"ERROR: {err}", file=sys.stderr); return 3
    n_entries = len(ord_blocks)
    for o in a.ordinals:
        if o not in ord_blocks:
            print(f"ERROR: ordinal {o} 越界 {n_entries}", file=sys.stderr); return 3

    lines = []
    lines.append("=== P2 字段值审计 (只读) ===")
    lines.append(f"WW_ANIM_XML count={len(_insts)}  条目数={n_entries}")
    lines.append("")

    schemas = {}   # ordinal -> {field: node_detail list}
    for o in a.ordinals:
        root = ET.fromstring(ord_blocks[o])
        all_n = {}
        for el in root.iter():
            n = el.get("n")
            if n:
                all_n.setdefault(n, []).append(el)
        lines.append(f"ORDINAL {o}")
        lines.append(f"  全部字段名: {sorted(all_n.keys())}")
        lines.append("  目标字段值:")
        seen_extra = set(TARGET_FIELDS)
        for f in TARGET_FIELDS:
            if f in all_n:
                det = _node_detail(root, f)
                schemas.setdefault(o, {})[f] = det
                lines.append(f"    {f} = {_fmt_detail(det[0]) if len(det)==1 else '; '.join(_fmt_detail(d) for d in det)}")
            else:
                schemas.setdefault(o, {})[f] = []
                lines.append(f"    {f} = (缺失)")
        # 也列出目标字段以外的、可能含 identity 的字段(含 id/hash/key/story/ae_/variant)
        hint = sorted(k for k in all_n
                      if k not in seen_extra and any(h in k.lower() for h in
                                                    ("id", "hash", "key", "story", "ae_", "variant", "instance", "uuid")))
        if hint:
            lines.append("  疑似 identity 补充字段:")
            for f in hint:
                det = _node_detail(root, f)
                lines.append(f"    {f} = {'; '.join(_fmt_detail(d) for d in det)}")
        lines.append("")

    # 124 vs 299 差异表
    lines.append("=== 124(Addicted) vs 299(Caught Cheating) 字段值差异表 ===")
    if 124 in schemas and 299 in schemas:
        s124, s299 = schemas[124], schemas[299]
        allf = sorted(set(s124) | set(s299))
        for f in allf:
            v124 = _fmt_detail(s124[f][0]) if s124.get(f) else "(缺失)"
            v299 = _fmt_detail(s299[f][0]) if s299.get(f) else "(缺失)"
            flag = ""
            if (not s124.get(f)) != (not s299.get(f)):
                flag = "  <-- 存在性不同!"
            elif s124.get(f) and s299.get(f) and _fmt_detail(s124[f][0]) != _fmt_detail(s299[f][0]):
                flag = "  <-- 值不同!"
            lines.append(f"  {f:32s}")
            lines.append(f"      124: {v124}")
            lines.append(f"      299: {v299}{flag}")
    else:
        lines.append("  (缺少 124 或 299)")

    txt = "\n".join(lines) + "\n"
    (out_dir / "p2_field_audit.txt").write_text(txt, encoding="utf-8")
    with open(out_dir / "p2_field_audit.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ordinal", "field", "present", "value"])
        for o in a.ordinals:
            for f, det in schemas.get(o, {}).items():
                w.writerow([o, f, len(det) > 0, _fmt_detail(det[0]) if det else ""])
    print(txt)
    print(f"OUT_TXT={out_dir / 'p2_field_audit.txt'}")
    print(f"OUT_CSV={out_dir / 'p2_field_audit.csv'}")
    print("P2_FIELD_AUDIT=OK (只读, 未生成包)")
    print("ZERO_WRITE_TO_MODS=YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
