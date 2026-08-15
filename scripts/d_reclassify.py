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
    dropped = []      # 被显式排除的异常行 (禁止静默)
    for r in rows:
        if (r.get("class") or "").strip() != "D":
            continue
        src = (r.get("source_text") or "")
        if src.strip() == "":
            # 空/空白 source_text: 不可作为可译文条目; 但绝不静默丢 -> 显式列入 dropped
            dropped.append({"reason": "EMPTY_OR_WHITESPACE_SOURCE", "row": dict(r)})
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

    if dropped:
        # 硬性: 不允许静默丢。发现异常入先行禁止 -> 打印全字段并 fail-fast (rc != 0)
        print(f"[HARD-FAIL] D 输入含 {len(dropped)} 个空/空白 source_text, 拒绝静默丢弃:")
        for x in dropped:
            print("   ", {k: v for k, v in x["row"].items()})
        raise SystemExit(
            f"[FAIL] 空/空白 source_text 不得静默丢; 请先裁决这些行 (排除或补 source). "
            f"现 D 唯一输入 {len(d)} (扣除 {len(dropped)} 异常行) 未输出.")

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

    # ---- 硬 invariant: D 输入 unique == delta catalog 输出 unique (禁 733->732 静默) ----
    from collections import Counter as _C
    out_key_cnt = _C((o["translation_id"], norm_text(o["source_text"])) for o in out_rows)
    dup_out = [k for k, n in out_key_cnt.items() if n > 1]
    if dup_out:
        raise SystemExit(f"[INVARIANT-FAIL] delta 输出存在重复 (tid, norm): {dup_out[:5]}")
    if len(out_rows) != len(d):
        raise SystemExit(
            f"[INVARIANT-FAIL] D 输入 unique {len(d)} != delta 输出 unique {len(out_rows)}. "
            f"不允许静默丢帧; 已停止输出.")
    print(f"[INVARIANT] D 输入 unique == delta 输出 unique = {len(d)}  PASS")

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
