#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M2 成败差异只读取证: 对比成功 entry (124-126 Addicted) vs 失败 entry (299-306 Caught Cheating)。

背景: M2 真机结果 — Addicted 1-3 中文成功; Caught Cheating 1-8 失败, 原动画变成
  "NOT Caught Cheating X"。怀疑 WW 对 STORY/named 动画用额外唯一键覆盖 raw_display_name。

本脚本只读 dump 每个目标 entry 的【全部】WW_ANIM_XML 字段 (含类型), 归纳成结构性
schema, 然后按"分组"对比, 把所有在 成功组 vs 失败组 之间【字段存在性/取值模式】有
差异的字段标出, 重点盯 animation_id/animation_hash/animation_stage_name 等疑似 STORY 唯一键。

绝不改 writer / 不生成 package / 不写 Mods。
ZERO_WRITE_TO_MODS=YES

用法 (Windows, 只读):
  python scripts/ww_animation_m2_diff_forensic.py <WW_Nevely42_Animations.package> \
      --ok 124 125 126 --fail 299 300 301 302 303 304 305 306 [--ordinal <额外>]
"""
import argparse
import csv
import importlib.util
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("ww_canary_builder", SCRIPT_DIR / "ww_animation_canary_builder.py")
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)

WW_ANIM_XML = 0x7DF2169C
ENTRY_LIST_FIELD = "animations_list"
OUT_DIR = Path("output/ww_animation_m2_diff")
STORY_HINT_KEYS = ("animation_id", "animation_hash", "animation_key", "story", "ae_", "ae_key",
                   "animation_stage_name", "animation_group", "animation_mode", "animation_set",
                   "shared", "identity", "actor_role", "function", "loop", "category")


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else None


def _locate_animations_list(xml_text):
    m = re.search(r'<L\b[^>]*\bn="' + re.escape(ENTRY_LIST_FIELD) + r'"[^>]*>', xml_text)
    if not m:
        return None, None, None
    inner_start = m.end()
    depth = 1
    pos = inner_start
    close_re = re.compile(r'</L\s*>')
    open_re = re.compile(r'<L\b[^>]*>')
    while True:
        oc = open_re.search(xml_text, pos)
        cc = close_re.search(xml_text, pos)
        if cc is None:
            return None, None, None
        if oc is not None and oc.start() < cc.start():
            depth += 1
            pos = oc.end()
        else:
            depth -= 1
            if depth == 0:
                return xml_text[inner_start:cc.start()], inner_start, cc.start()
            pos = cc.end()


def _entry_blocks(inner):
    blocks = []
    pos = 0
    entry_re = re.compile(r'<U\b[^>]*>')
    close_re = re.compile(r'</U\s*>')
    n = len(inner)
    while pos < n:
        m = entry_re.search(inner, pos)
        if m is None:
            if pos < n:
                blocks.append((inner[pos:n], (pos, n)))
            break
        if m.start() > pos:
            blocks.append((inner[pos:m.start()], (pos, m.start())))
        s = m.start()
        depth = 1
        p = m.end()
        while True:
            oc = entry_re.search(inner, p)
            cc = close_re.search(inner, p)
            if cc is None:
                e = n
                break
            if oc is not None and oc.start() < cc.start():
                depth += 1
                p = oc.end()
            else:
                depth -= 1
                p = cc.end()
                if depth == 0:
                    e = p
                    break
        blocks.append((inner[s:e], (s, e)))
        pos = e
    return blocks


def _collect_fields(block):
    """遍历 entry 内全部可命名节点, 记录 (local_tag, n, text/child-count)。
    跳过根 <U> 自身的 n= (那是条目包装器名, 每个 entry 各异, 属结构性噪音)。"""
    root = ET.fromstring(block)
    out = {}
    for el in root.iter():
        if el is root:
            continue
        lt = _local(el.tag)
        n = el.get("n")
        if not n:
            continue
        if lt in ("T", "E", "I"):
            val = (el.text or "").strip()
        elif lt == "L":
            val = f"[{len(list(el))} items]"
        elif lt == "U":
            val = "[object]"
        else:
            val = ""
        # 多节点同名 -> 记录全部
        out.setdefault(n, []).append({"tag": lt, "val": val,
                                      "children": len(list(el)) if lt in ("L", "U") else None})
    return out


def _field_signature(entries_fields_map):
    """把一组 entry 的字段归并为 '存在性 + 取值唯一性' 模式。"""
    all_keys = set()
    for f in entries_fields_map.values():
        all_keys.update(f.keys())
    sig = {}
    for k in sorted(all_keys):
        present_in = [o for o, f in entries_fields_map.items() if k in f]
        vals = [n["val"] for o in entries_fields_map.items() if k in f for n in f[k]]
        uniq = sorted(set(vals))
        sig[k] = {
            "present": len(present_in),
            "of": len(entries_fields_map),
            "tags": sorted({n["tag"] for o in entries_fields_map.items() if k in f for n in f[k]}),
            "uniq_values": uniq[:6],
            "n_uniq": len(uniq),
            "story_hint": any(h in k.lower() for h in STORY_HINT_KEYS),
        }
    return sig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pkg", help="WW_Nevely42_Animations.package")
    ap.add_argument("--ok", nargs="+", type=int, default=[], help="成功组 ordinal")
    ap.add_argument("--fail", nargs="+", type=int, default=[], help="失败组 ordinal")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    src = Path(a.pkg)
    if not src.is_file():
        print(f"ERROR: 源不存在 {src}", file=sys.stderr); return 2
    if not a.ok and not a.fail:
        print("ERROR: 需 --ok 或 --fail 至少一组", file=sys.stderr); return 2

    idx, err = wb.safe_parse(src)
    if err is not None or idx is None:
        print(f"ERROR: 解析失败 {err}", file=sys.stderr); return 3
    ww = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    if len(ww) != 1:
        print(f"ERROR: 需单 WW_ANIM_XML; 实际={len(ww)}", file=sys.stderr); return 3
    body = wb.read_body_raw(src, ww[0])
    _schema, xml_text, xerr = wb.parse_anim_xml(body)
    if xerr is not None:
        print(f"ERROR: WW XML 解析失败 {xerr}", file=sys.stderr); return 3
    inner, _s, _e = _locate_animations_list(xml_text)
    if inner is None:
        print("ERROR: 找不到 animations_list", file=sys.stderr); return 3

    blocks = _entry_blocks(inner)
    ordinal_map = {}
    ei = 0
    for b, _rng in blocks:
        if b.lstrip().startswith("<U"):
            ordinal_map[ei] = b
            ei += 1
    n_entries = ei

    all_ords = list(a.ok) + list(a.fail)
    for o in all_ords:
        if o not in ordinal_map:
            print(f"ERROR: ordinal {o} 超出 entry 数 {n_entries}", file=sys.stderr); return 3

    ok_fields = {o: _collect_fields(ordinal_map[o]) for o in a.ok}
    fail_fields = {o: _collect_fields(ordinal_map[o]) for o in a.fail}

    ok_sig = _field_signature(ok_fields)
    fail_sig = _field_signature(fail_fields)

    # ---- 归纳差异 ----
    all_keys = sorted(set(ok_sig) | set(fail_sig))
    diff_rows = []
    for k in all_keys:
        o = ok_sig.get(k)
        f = fail_sig.get(k)
        # 结构差异: 存在性不同 / 取值唯一数不同 / 值模式不同
        if o is None or f is None:
            nature = "存在性差异 (仅一组有)"
        elif o["n_uniq"] != f["n_uniq"] or o["uniq_values"] != f["uniq_values"]:
            nature = "取值模式差异"
        elif o["tags"] != f["tags"]:
            nature = "节点类型差异"
        else:
            nature = "相同"
        diff_rows.append({
            "field": k,
            "ok_present": f"{o['present']}/{len(a.ok)}" if o else "—",
            "fail_present": f"{f['present']}/{len(a.fail)}" if f else "—",
            "ok_uniq": o["n_uniq"] if o else "—",
            "fail_uniq": f["n_uniq"] if f else "—",
            "ok_values": "|".join(o["uniq_values"]) if o else "—",
            "fail_values": "|".join(f["uniq_values"]) if f else "—",
            "tags": ",".join(sorted(set((o or {}).get("tags", []) + (f or {}).get("tags", [])))),
            "story_hint": (o or {}).get("story_hint", False) or (f or {}).get("story_hint", False),
            "nature": nature,
        })

    # ---- 输出 ----
    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "m2_diff_field_audit.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as fp:
        w = csv.DictWriter(fp, fieldnames=["field", "nature", "story_hint", "ok_present",
                                           "fail_present", "ok_uniq", "fail_uniq",
                                           "ok_values", "fail_values", "tags"])
        w.writeheader()
        for r in diff_rows:
            w.writerow({k: r[k] for k in w.fieldnames})

    txt_path = out_dir / "m2_diff_field_audit.txt"
    lines = []
    lines.append("M2 成败差异取证: OK(124-126) vs FAIL(299-306) — 全部 WW_ANIM_XML 字段结构对比")
    lines.append("=" * 90)
    lines.append(f"OK group : {a.ok}")
    lines.append(f"FAIL group: {a.fail}")
    lines.append(f"TOTAL_ENTRIES={n_entries}")
    lines.append("")
    lines.append("【疑似 STORY 唯一键全览】(任何一组出现的 STORY 候选都列出, 含两组逐 ordinal 值)")
    lines.append("-" * 90)
    story_all = [k for k in all_keys if (ok_sig.get(k) or {}).get("story_hint") or (fail_sig.get(k) or {}).get("story_hint")]
    if story_all:
        for k in story_all:
            o, f = ok_sig.get(k), fail_sig.get(k)
            nature = next((r["nature"] for r in diff_rows if r["field"] == k), "相同")
            lines.append(f"  {k:28s} {nature:14s}  OK({o['present'] if o else 0}/{len(a.ok)}={o['uniq_values'] if o else '—'})  FAIL({f['present'] if f else 0}/{len(a.fail)}={f['uniq_values'] if f else '—'})")
            if nature != "相同":
                for grp_name, grp_ords, grp_map in (("OK", a.ok, ok_fields), ("FAIL", a.fail, fail_fields)):
                    for o2 in grp_ords:
                        v = grp_map[o2].get(k)
                        if v:
                            lines.append(f"      {grp_name} {o2}: {v}")
    else:
        lines.append("  (未发现 STORY 候选字段)")
    lines.append("")
    lines.append("【全部字段差异汇总】")
    lines.append("-" * 90)
    lines.append(f"{'field':36s} {'nature':14s} {'story':5s} {'OK#':4s} {'FAIL#':5s} {'OKuniq':6s} {'FAILuniq':7s}  OK_values / FAIL_values")
    for r in diff_rows:
        if r["nature"] == "相同":
            continue
        lines.append(f"{r['field']:36s} {r['nature']:14s} {'Y' if r['story_hint'] else '':5s} "
                     f"{r['ok_present']:4s} {r['fail_present']:5s} {str(r['ok_uniq']):6s} {str(r['fail_uniq']):7s} "
                     f"{r['ok_values']!r} / {r['fail_values']!r}")
    lines.append("")
    lines.append(f"无差异字段数: {sum(1 for r in diff_rows if r['nature']=='相同')} "
                 f"(这些在两组的取值模式完全一致, 已在前综述字段名)")
    shared = [r["field"] for r in diff_rows if r["nature"] == "相同"]
    lines.append(f"相同字段: {', '.join(shared)}")
    lines.append("")
    lines.append("ZERO_WRITE_TO_MODS=YES (只读 dump, 不改 package / 不写 writer)")
    txt_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"OUT_CSV={csv_path}")
    print(f"OUT_TXT={txt_path}")
    print(f"FIELDS_TOTAL={len(all_keys)}  DIFF_FIELDS={sum(1 for r in diff_rows if r['nature']!='相同')}")
    story_diff = [r["field"] for r in diff_rows if r["story_hint"] and r["nature"] != "相同"]
    print(f"STORY_HINT_DIFF_FIELDS={story_diff}")
    print("M2_DIFF_FORENSIC=OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
