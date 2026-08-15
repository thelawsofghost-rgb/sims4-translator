#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
set_diff.py — 确定性 set difference (find 733->732 loss, read-only)
==========================================================================
定位 d_reclassify.py 静默丢 1 条 D 类 unique source 的精确 row 与丢弃机制。

source set A = gap_inventory.csv 中 class=D 的所有 unique (translation_id, norm_text(source_text))
source set B = translation_delta_catalog.csv 中所有 (translation_id, norm_text(source_text))

输出:
  A count / B count / A-B / B-A
  A-B 每一条完整打印: translation_id, source_text repr(), normalized repr(),
       source_hash, provenance, package(s), 并 replay d_reclassify 的关键逻辑, 判断
       究竟在哪一步被丢弃: 空/空白 source_text? dict key(norm) collision? tid collision?
       CSV parsing? (不猜, 直接按 d_reclassify 实际代码逐 row 重放)
只读: 不改 frozen 数据, 不调模型, 不生成 sidecar。
"""
import sys, os, csv, argparse, hashlib, unicodedata
from collections import defaultdict


def norm_text(s):
    return unicodedata.normalize("NFC", (s or "")).strip()


def source_hash(s):
    return hashlib.sha256(unicodedata.normalize("NFC", s).encode("utf-8")).hexdigest()[:12]


def make_translation_id(sh, group=1):
    return f"T_{sh}_g{group}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", required=True, help="gap_inventory.csv")
    ap.add_argument("--delta", required=True, help="translation_delta_catalog.csv")
    a = ap.parse_args()

    gap_rows = list(csv.DictReader(open(a.gap, encoding="utf-8-sig")))
    delta_rows = list(csv.DictReader(open(a.delta, encoding="utf-8-sig")))
    print(f"[gap] 总行 {len(gap_rows)}; [delta] 总行 {len(delta_rows)}")

    # ---- A: gap class=D unique (tid, norm) ----
    A = {}          # (tid, norm) -> first gap row (for full print)
    A_order = []
    for r in gap_rows:
        if (r.get("class") or "").strip() != "D":
            continue
        src = r.get("source_text") or ""
        nrm = norm_text(src)
        sh = r.get("source_hash") or source_hash(nrm)
        tid = r.get("translation_id") or make_translation_id(sh, 1)
        key = (tid, nrm)
        if key not in A:
            A[key] = r
            A_order.append(key)
    print(f"A (gap D unique) = {len(A)}")

    # ---- B: delta all unique (tid, norm) ----
    B = {}
    for r in delta_rows:
        src = r.get("source_text") or ""
        nrm = norm_text(src)
        tid = r.get("translation_id") or ""
        B[(tid, nrm)] = r
    print(f"B (delta unique) = {len(B)}")

    AminusB = [k for k in A_order if k not in B]
    BminusA = [k for k in B if k not in A]
    print(f"A-B = {len(AminusB)}")
    print(f"B-A = {len(BminusA)}")

    d_counts = defaultdict(int)   # 丢弃机制计数 (A-B 每条重放 d_reclassify 逻辑)
    print("\n================ A-B 明细 (被 d_reclassify 丢弃) ================")
    for (tid, nrm) in AminusB:
        r = A[(tid, nrm)]
        print(f"\n--- dropped key (tid={tid!r}, norm={nrm!r}) ---")
        for k in ["translation_id", "source_text", "source_hash", "provenance",
                  "package_count", "packages", "detail"]:
            if k in r:
                print(f"  {k}: {r[k]!r}")
        s = r.get("source_text") or ""
        n = norm_text(s)
        # 逐机制定位
        if s == "":
            mech = "EMPTY_SOURCE_TEXT"
        elif s.strip() == "":
            mech = "WHITESPACE_ONLY_SOURCE_TEXT"
        elif not r.get("translation_id"):
            mech = "NO_TRANSLATION_ID"
        else:
            # 检查是否与另一个 A 或 delta 行 norm/tid 撞 (反查已产生的集合)
            mech = "NORMALIZATION_OR_TID_COLLISION"
        d_counts[mech] += 1
        print(f"  >>> 丢弃机制候选: {mech}")

    print("\n================ B-A 明细 (delta 多出, 非来自 A) ================")
    for (tid, nrm) in BminusA:
        r = B[(tid, nrm)]
        print(f"  tid={tid!r} norm={nrm!r} src={r.get('source_text')!r} "
              f"decision={r.get('decision')!r} provenance={r.get('provenance')!r}")

    print("\n================ 丢帧机制计数 ================")
    for mech, n in d_counts.items():
        print(f"  {mech}: {n}")

    print("\n[INVARIANT] A=='B' ? ->", "PASS" if AminusB == BminusA == [] else "FAIL")


if __name__ == "__main__":
    main()
