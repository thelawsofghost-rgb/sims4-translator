#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit_override_pairs.py — 只读 pairwise audit 多个 production override 候选文件
================================================================================
用途: 对 Windows 真实 production override 文件做**只读** pairwise 对比, 搞清楚
  (A) 114/111/95/114 各文件历史关系;
  (B) Phase2B 当前真正应采用哪个作为 production base;
  (C) final/final2/final3 是 superseding layer 还是旧阶段产物。
规则: 绝不写任何文件, 绝不动 frozen production base; 不猜测文件名 precedence,
      只看数据本身 (行数/unique/overlap/only-left/only-right/同 key 异值/同 id 异 source)。

对传入的每对文件, 输出:
  row count (含/不含头)
  unique (translation_id, normalized source_text)
  overlap  unique counts (left∩right)
  only-left, only-right
  same-key same action+translation
  same-key different action
  same-key different translation
  same translation_id different normalized source_text (id 冲突, 源不一致)

用法:
  python scripts/audit_override_pairs.py <f1.csv> <f2.csv> [<f3.csv> ...]
  # 传 ≥2 个文件, 对所有组合输出。文件名仅作标签, 不做任何 precedence 假设。
"""
import sys, os, csv, itertools
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))


def norm_text(s):
    try:
        from phase2a_catalog import norm_text as _nt
        return _nt(s or "")
    except Exception:
        return (s or "").strip()


def load(path):
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            tid = (r.get("translation_id") or "").strip()
            src = (r.get("source_text") or "").strip()
            tr = (r.get("translation") or "").strip()
            act = (r.get("action") or "").strip().upper()
            rows.append({"tid": tid, "src": src, "tr": tr, "act": act,
                         "ns": norm_text(src)})
    # 去重 (同 CSV 内同 key 多行取最后)
    merged = {}
    for r in rows:
        if not r["tid"]:
            continue
        merged[(r["tid"], r["ns"])] = r
    return merged


def audit_pair(nameA, mA, nameB, mB):
    ka, kb = set(mA), set(mB)
    print(f"\n{'='*78}")
    print(f"PAIR: [{nameA}] vs [{nameB}]")
    print(f"  {nameA}: rows={len(mA)} unique(tid,ns)={len(ka)}")
    print(f"  {nameB}: rows={len(mB)} unique(tid,ns)={len(kb)}")
    overlap = ka & kb
    onlyA = ka - kb
    onlyB = kb - ka
    print(f"  overlap unique                 : {len(overlap)}")
    print(f"  only-{nameA}                      : {len(onlyA)}")
    print(f"  only-{nameB}                      : {len(onlyB)}")

    # 同 key 值分析
    same_all = diff_act = diff_tr = 0
    diff_act_rows, diff_tr_rows = [], []
    for k in overlap:
        ra, rb = mA[k], mB[k]
        same_act = (ra["act"] == rb["act"])
        same_tr = (ra["tr"] == rb["tr"])
        if same_act and same_tr:
            same_all += 1
        else:
            if not same_act:
                diff_act += 1
                diff_act_rows.append((k, ra, rb))
            elif not same_tr:
                diff_tr += 1
                diff_tr_rows.append((k, ra, rb))

    # 同 translation_id 不同 normal source_text (id 冲突)
    idA, idB = {}, {}
    for (tid, ns), r in mA.items(): idA.setdefault(tid, set()).add(ns)
    for (tid, ns), r in mB.items(): idB.setdefault(tid, set()).add(ns)
    id_conflict = [tid for tid in (set(idA) & set(idB)) if idA[tid] != idB[tid]]

    print(f"  overlap 中同 key 同 action+translation : {same_all}")
    print(f"  overlap 中同 key 不同 action           : {diff_act}")
    print(f"  overlap 中同 key 不同 translation       : {diff_tr}")
    print(f"  同 translation_id 不同 normalized source : {len(id_conflict)}")
    if diff_act_rows:
        print("    -- 不同 action --")
        for (tid, ns), ra, rb in sorted(diff_act_rows)[:15]:
            print(f"      {tid}: L={ra['act']} R={rb['act']} src={ra['src']!r}")
    if diff_tr_rows:
        print("    -- 不同 translation --")
        for (tid, ns), ra, rb in sorted(diff_tr_rows)[:15]:
            print(f"      {tid}: L={ra['tr']!r} R={rb['tr']!r}")
    if id_conflict:
        print("    -- 同 id 不同 source --")
        for tid in sorted(id_conflict)[:15]:
            print(f"      {tid}: A_ns={sorted(idA[tid])} B_ns={sorted(idB[tid])}")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    files = sys.argv[1:]
    data = {}
    for p in files:
        if not Path(p).exists():
            print(f"[FAIL] 文件不存在: {p}")
            return 1
        data[p] = load(p)
        print(f"[load] {p}: rows(unique)={len(data[p])}")
    lbl = {p: Path(p).name for p in files}
    for a, b in itertools.combinations(files, 2):
        audit_pair(lbl[a], data[a], lbl[b], data[b])
    print(f"\n{'='*78}")
    print("审计只读完成: 未修改任何文件。文件名未用于判断 precedence。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
