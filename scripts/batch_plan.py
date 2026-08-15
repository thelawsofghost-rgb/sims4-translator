#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
batch_plan.py — 只读 overlap audit + deterministic batch plan (按 translation_id 为唯一单位)
==========================================================================
workset 冻结 (626 unique source, translation_incremental_workset.csv)。本层不再改
decision/catalog/coverage/cohort/writer; 只做两件事, 且不调用模型:

1) overlap audit: 同一 unique tid 跨多个 provenance 的数量与组合。
   provenance 报告 193+407+29=629 != 626, 差异来自 multi-provenance source。

2) deterministic batch plan: 每个 translation_id 只分配到一个 batch (唯一单位),
   multi-provenance 只进一个 batch, 禁止重复。
   优先级 POSE_DISPLAY_NAME > PACK_TITLE > PACK_DESCRIPTION。

   bug 定义: 某 tid 的 provenance 含 POSE_DISPLAY_NAME -> batch=POSE_DISPLAY_NAME;
   否则含 PACK_TITLE -> batch=PACK_TITLE; 否则 batch=PACK_DESCRIPTION。
   这是确定性单值划分 (每个 tid 恰一个 batch)。

输入:  --ws    output/translation_incremental_workset.csv (626)
输出:  --out   output/translation_batch_manifest.csv
              (translation_id, source_text, source_hash, provenance, assigned_batch)
        --audit 可选, 默认 stdout 打印 audit。

硬 invariant (fail-fast, rc != 0):
  batch unique tid union == 626
  intersections between batches == 0
  missing == 0
  duplicate == 0 (任一 tid 只在一个 batch, 不重复送模型)
"""
import sys, os, csv, argparse
from collections import Counter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", required=True)
    ap.add_argument("--out", default="output/translation_batch_manifest.csv")
    ap.add_argument("--audit", default="")
    a = ap.parse_args()

    rows = list(csv.DictReader(open(a.ws, encoding="utf-8-sig")))
    print(f"[ws] workset 行 = {len(rows)}")

    PRIO = ["POSE_DISPLAY_NAME", "PACK_TITLE", "PACK_DESCRIPTION"]

    # ---- 每个 tid 的 provenance 组合 (norm source 聚合, tid 唯一) ----
    by_tid = {}          # tid -> {source_text, source_hash, provs:set, pkgs}
    for r in rows:
        tid = (r.get("translation_id") or "").strip()
        provs = {p for p in (r.get("provenance") or "").split("|") if p}
        e = by_tid.setdefault(tid, {"source_text": r.get("source_text") or "",
                                    "source_hash": r.get("source_hash") or "",
                                    "provs": set(), "pkgs": set()})
        e["provs"] |= provs
        e["pkgs"] |= {p for p in (r.get("packages") or "").split("|") if p}

    # ---- overlap audit ----
    single = 0
    multi = {}
    prov_sets_map = Counter()
    for tid, e in by_tid.items():
        if len(e["provs"]) == 1:
            single += 1
        else:
            multi[tid] = sorted(e["provs"])
            prov_sets_map[tuple(sorted(e["provs"]))] += 1

    print("\n=== overlap audit ===")
    print(f"unique workset tid = {len(by_tid)}")
    print(f"single-provenance tid = {single}")
    print(f"multi-provenance tid = {len(multi)}")
    print(f"single + multi = {single + len(multi)}  (应 == {len(by_tid)})")
    if single + len(multi) != len(by_tid):
        raise SystemExit(f"[INVARIANT-FAIL] single+multi {single+len(multi)} != {len(by_tid)}")
    print("\noverlap 组合及数量 (multi-provenance):")
    if prov_sets_map:
        for combo in sorted(prov_sets_map, key=lambda c: (-prov_sets_map[c], c)):
            print(f"  {'+'.join(combo)}: {prov_sets_map[combo]}")
    else:
        print("  (无 multi-provenance)")

    if a.audit:
        with open(a.audit, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["translation_id", "provenance_combination"])
            for tid, combo in sorted(multi.items()):
                w.writerow([tid, "+".join(combo)])
        print(f"\n[audit] {a.audit}  (multi-provenance tid, {len(multi)} 行)")

    # ---- batch assignment (确定性, 每 tid 恰一个 batch) ----
    plan = []
    for tid, e in by_tid.items():
        assigned = next((p for p in PRIO if p in e["provs"]), None)
        if assigned is None:
            raise SystemExit(f"[HARD-FAIL] tid {tid} 无已知 provenance: {e['provs']}")
        plan.append({"translation_id": tid,
                     "source_text": e["source_text"],
                     "source_hash": e["source_hash"],
                     "provenance": "+".join(sorted(e["provs"])),
                     "assigned_batch": assigned})

    # ---- invariants ----
    all_tids = [p["translation_id"] for p in plan]
    assert len(set(all_tids)) == len(all_tids), "duplicate tid in plan"
    by_batch = {}
    for p in plan:
        by_batch.setdefault(p["assigned_batch"], []).append(p["translation_id"])

    union = set().union(*[set(v) for v in by_batch.values()])
    inter = set.intersection(*[set(v) for v in by_batch.values()]) if by_batch else set()
    missing = set(all_tids) - union
    dup_in = [tid for tid, c in Counter(all_tids).items() if c > 1]

    n_total = len(all_tids)
    print(f"\n=== batch manifest ({n_total} unique tid) ===")
    for b in PRIO:
        print(f"  {b}: {len(by_batch.get(b, []))} tid")
    print(f"  batch unique tid union = {len(union)}")
    print(f"  intersection between batches = {len(inter)}")
    print(f"  missing = {len(missing)}  duplicate = {len(dup_in)}")

    ok = (len(union) == 626 and not inter and not missing and not dup_in)
    if not ok:
        raise SystemExit(
            f"[HARD-FAIL] batch invariant 不成立: union={len(union)}(应626) inter={len(inter)} "
            f"missing={len(missing)} dup={len(dup_in)}. 不启动翻译。")
    print("[INVARIANT] batch union==626, inter==0, missing==0, duplicate==0  PASS")

    # ---- 写 manifest ----
    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, ["translation_id", "source_text", "source_hash",
                               "provenance", "assigned_batch"])
        w.writeheader()
        for p in sorted(plan, key=lambda p: (p["assigned_batch"], p["translation_id"])):
            w.writerow(p)
    print(f"\n[out] {a.out}  ({len(plan)} 行 = 626 unique tid)")


if __name__ == "__main__":
    main()
