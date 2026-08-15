#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
d_reclassify.py — D 类 (NEW_SOURCE_NOT_IN_CATALOG) 重新分类 -> translation_delta_catalog.csv
==============================================================================
只读决策层: 不改 resolver/writer/coverage/cohort, 不生成 sidecar, 不调模型,
不修改原 frozen translation_catalog.csv。

D 类 = stable_id/source_text 不在旧 frozen catalog。使用与原 phase2a_catalog.py
完全相同的 decision 规则 (phase2a_samples.classify + classify_with_context) 重新分类:
  TRANSLATE / KEEP / REVIEW

stable ID 继续使用 T_{source_hash}_g1 (source_hash = sha256(NFC)[:12], 禁 STBL KeyHash)。
provenance 保留 PACK_TITLE / PACK_DESCRIPTION / POSE_DISPLAY_NAME;
同一 source 若出现在多个 provenance, 仍一条 unique source, 但记录全部 provenance。

输入: gap_inventory.py 产出的 gap_inventory.csv (含 class=D 的行)。
输出: translation_delta_catalog.csv
  translation_id, source_text, source_hash, decision, reason, provenance,
  ref_count, package_count, packages
"""
import sys, os, csv, argparse
from collections import Counter, defaultdict

# 复用 frozen 同一套 decision 规则 (与 phase2a_catalog.py 完全一致)
sys.path.insert(0, os.path.dirname(__file__))
from phase2a_samples import classify, classify_with_context
from phase2a_catalog import source_hash, norm_text, make_translation_id


def stable_id(src):
    return make_translation_id(source_hash(norm_text(src)), 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", default="output/gap_inventory.csv")
    ap.add_argument("--out", default="output/translation_delta_catalog.csv")
    a = ap.parse_args()

    if not os.path.exists(a.gap):
        raise SystemExit(f"[FAIL] 缺 gap inventory: {a.gap}  (先跑 gap_inventory.py)")

    rows = list(csv.DictReader(open(a.gap, encoding="utf-8-sig")))
    print(f"[gap] {len(rows)} 行")

    # 收集 D 类 unique source (按 norm_text 去重; 同一 source 多 provenance 合并)
    d = {}
    for r in rows:
        if (r.get("class") or "").strip() != "D":
            continue
        src = (r.get("source_text") or "").strip()
        if not src:
            continue
        key = norm_text(src)
        e = d.setdefault(key, {
            "src": src,
            "provs": set(),
            "pkgs": set(),
            "ref_count": 0,
        })
        for p in (r.get("provenance") or "").split("|"):
            if p:
                e["provs"].add(p)
        for p in (r.get("packages") or "").split("|"):
            if p:
                e["pkgs"].add(p)
        try:
            e["ref_count"] += int((r.get("package_count") or "0").strip() or 0)
        except ValueError:
            pass

    print(f"[D] NEW_SOURCE_NOT_IN_CATALOG unique source = {len(d)}")

    # 重新分类 (与原 catalog 相同的 decision 规则)
    out_rows = []
    dec_cnt = Counter()
    reason_cnt = Counter()
    prov_by_dec = Counter()       # (decision, provenance)
    pkg_impact = defaultdict(lambda: defaultdict(set))  # decision -> provenance -> set(pkg)
    for key, e in sorted(d.items()):
        cls = classify(e["src"])
        dec, reason = classify_with_context(e["src"], "")   # approved key 无邻居上下文
        sh = source_hash(norm_text(e["src"]))
        tid = make_translation_id(sh, 1)
        prov = "|".join(sorted(e["provs"]))
        dec_cnt[dec] += 1
        reason_cnt[reason] += 1
        for p in e["provs"]:
            prov_by_dec[(dec, p)] += 1
            pkg_impact[dec][p].update(e["pkgs"])
        out_rows.append({
            "translation_id": tid,
            "source_text": e["src"],
            "source_hash": sh,
            "decision": dec,
            "reason": reason,
            "provenance": prov,
            "ref_count": e["ref_count"],
            "package_count": len(e["pkgs"]),
            "packages": "|".join(sorted(e["pkgs"])),
        })

    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, ["translation_id", "source_text", "source_hash", "decision",
                               "reason", "provenance", "ref_count", "package_count", "packages"])
        w.writeheader()
        for o in out_rows:
            w.writerow(o)

    # ---- 汇总 ----
    print("\n================ D 类重新分类汇总 ================")
    print(f"D 总数: {len(d)}")
    print(f"  TRANSLATE: {dec_cnt.get('TRANSLATE', 0)}")
    print(f"  KEEP:      {dec_cnt.get('KEEP', 0)}")
    print(f"  REVIEW:    {dec_cnt.get('REVIEW', 0)}")
    print("\n各 decision 按 provenance 分布:")
    for dec in ("TRANSLATE", "KEEP", "REVIEW"):
        provs = sorted({p for (c, p) in prov_by_dec if c == dec})
        if not provs:
            continue
        line = "  ".join(f"{p}={prov_by_dec[(dec, p)]}" for p in provs)
        print(f"  {dec}: {line}")
    print("\n各 decision 影响 package (去重) 数:")
    for dec in ("TRANSLATE", "KEEP", "REVIEW"):
        allp = set()
        for p in pkg_impact[dec]:
            allp |= pkg_impact[dec][p]
        detail = ", ".join(f"{p}={len(pkg_impact[dec][p])}" for p in pkg_impact[dec])
        print(f"  {dec}: {len(allp)} 包  | {detail}")
    print("\nreason 分布:", dict(reason_cnt))

    print("\n================ 6 样本 delta decision ================")
    for s in ["Tibo131 Standing Pose Pack #2", "Pose 1", "Pose 2",
              "[AlluringSims] Annoyed Emotions", "1", "Gounafier's Pose Pack"]:
        tid = stable_id(s)
        cls = classify(s)
        dec, reason = classify_with_context(s, "")
        match = next((o for o in out_rows if o["source_text"] == s), None)
        print(f"  {s!r:42} -> {dec:<9} | reason={reason:<24} | tid={tid} | in_delta={match is not None}")

    print(f"\n[out] {a.out}  ({len(out_rows)} unique source)")


if __name__ == "__main__":
    main()
