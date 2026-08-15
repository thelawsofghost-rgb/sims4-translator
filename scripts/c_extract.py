#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
c_extract.py — 提取 C 类 (CATALOG_TRANSLATE_MISSING_RESULT) 为 translation_missing_result.csv
==============================================================================
只读决策层: 不改 resolver/writer/coverage/cohort, 不生成 sidecar, 不调模型。
C 类 = 已在 frozen catalog 中 decision=TRANSLATE/APPROVED, 但 override/done 无最终译文。
它们已经是 frozen TRANSLATE, 不重新分类; 后续只需补最终译文。

输入: gap_inventory.py 产出的 gap_inventory.csv (含 class=C 的行)。
输出: translation_missing_result.csv
  translation_id, source_text, source_hash, old_catalog_decision, provenance,
  affected_package_count, packages
"""
import sys, os, csv, argparse
from collections import defaultdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", default="output/gap_inventory.csv")
    ap.add_argument("--out", default="output/translation_missing_result.csv")
    a = ap.parse_args()

    if not os.path.exists(a.gap):
        raise SystemExit(f"[FAIL] 缺 gap inventory: {a.gap}  (先跑 gap_inventory.py)")

    rows = list(csv.DictReader(open(a.gap, encoding="utf-8-sig")))
    print(f"[gap] {len(rows)} 行")

    out_rows = []
    seen = set()
    for r in rows:
        if (r.get("class") or "").strip() != "C":
            continue
        tid = (r.get("translation_id") or "").strip()
        src = (r.get("source_text") or "").strip()
        if (tid, src) in seen:
            continue
        seen.add((tid, src))
        out_rows.append({
            "translation_id": tid,
            "source_text": src,
            "source_hash": (r.get("source_hash") or "").strip(),
            "old_catalog_decision": "TRANSLATE",   # C 类固定: 已在 frozen catalog, decision=TRANSLATE
            "provenance": (r.get("provenance") or "").strip(),
            "affected_package_count": (r.get("package_count") or "").strip(),
            "packages": (r.get("packages") or "").strip(),
        })

    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, ["translation_id", "source_text", "source_hash",
                               "old_catalog_decision", "provenance",
                               "affected_package_count", "packages"])
        w.writeheader()
        for o in out_rows:
            w.writerow(o)

    print(f"[C] CATALOG_TRANSLATE_MISSING_RESULT unique source = {len(out_rows)}")
    prov = defaultdict(int)
    pkgs = set()
    for o in out_rows:
        for p in o["provenance"].split("|"):
            if p:
                prov[p] += 1
        for p in o["packages"].split("|"):
            if p:
                pkgs.add(p)
    print("  provenance 分布:", dict(prov))
    print(f"  affected package (去重) = {len(pkgs)}")
    print(f"[out] {a.out}")


if __name__ == "__main__":
    main()
