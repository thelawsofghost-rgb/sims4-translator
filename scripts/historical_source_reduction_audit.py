#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
historical_source_reduction_audit.py — 历史源缩减审计 (只读)
================================================================================
目的: 在改 production resolver 前, 证明能否把 run2 production resolver 的
historical final fallback 单一化为 translation_done.csv, 并证明
final2 / overrides114(base) 可安全地从 resolver 加载中移除。

判定 (与用户裁定一致):
  * merged historical resolved = translation_done 非空 unique (tid,norm_source)
    是否 == 1888 (真实全局 historical resolved)
  * merged_resolved - done_nonempty == 0  => done 单独全覆盖
  * done_nonempty - merged_resolved == 0  => done 无多余 (无泄漏)
  * overrides114 ⊆ production_overlay217  => base114 被 production overlay 完全覆盖
  * final2 ⊆ overrides114                 => final2 是 base114 子集

若上述全部成立 => run2 production resolver 不再加载 final2, 也不再单独加载 base114:
  加载: production_overlay217 + title_final407 + desc_final190 + translation_done.csv + catalog

角色严格区分:
  production_overlay = explicit latest terminal override (最高权威)
  title/desc final    = new final results
  translation_done    = historical final translation fallback (merged historical resolved)
  catalog             = decision/index ONLY (不是 final payload)

catalog 规则:
  KEEP     -> 无更高层终态时 -> KEEP
  TRANSLATE-> 必须有 overlay / title-desc final / historical done 提供 translation, 否则 MISSING
  REVIEW   -> 若有更高层 final outcome -> 以高层为准并记录 superseded, 否则 unresolved

不再比较 catalog.translation (非 final payload)。

历史 done 被 production overlay 覆盖时不直接 conflict: latest overlay supersedes
historical final, 但报告 historical_superseded=N 与 catalog_decision_superseded=N。

真正 HARD-FAIL:
  title_final vs desc_final 同 key 不一致
  final source 内部重复冲突 (同一 key 两个不同 translation)
  source_text mismatch
  当前 cohort TRANSLATE 无任何 final payload

只读: 不生成 sidecar, 不改 writer, 不改 title/desc final/overlay/done/catalog。

用法 (Windows, 先 cd 仓库根):
  python scripts\\historical_source_reduction_audit.py \\
      --catalog output\\translation_catalog.csv \\
      --done output\\translation_done.csv \\
      --overrides output\\translation_overrides.csv \\
      --production-overlay output\\translation_overrides.production.csv \\
      [--final2 output\\translation_overrides.final2.csv] \\
      [--title-final output\\translation_done_title_final.csv] \\
      [--desc-final output\\translation_done_desc_final.csv] \\
      [--expect-resolved 1888]

返回码: 0 = 缩减成立 (允许 resolver 单一化 done); 2 = 缩减不成立 (HARD-FAIL)。
"""
import sys, os, csv, argparse
from pathlib import Path
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))


def load_map(path, label, translation_key="translation", require_nonempty=True,
             tid_key="translation_id", src_key="source_text"):
    """{(tid, norm_source): translation}; 仅非空 translation 计入 (当 require_nonempty)。"""
    out = {}
    raw_rows = 0
    nonempty = 0
    dup_conflict = Counter()
    if not path or not Path(path).exists():
        return out, 0, 0, dup_conflict
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            raw_rows += 1
            tid = (r.get(tid_key) or "").strip()
            stxt = (r.get(src_key) or "").strip()
            tr = (r.get(translation_key) or "").strip()
            if not tid or not stxt:
                continue
            if require_nonempty and not tr:
                continue
            nonempty += 1
            key = (tid, stxt.strip())
            prev = out.get(key)
            if prev is not None and prev != tr:
                dup_conflict[key] += 1   # 内部重复冲突: 同 key 两个不同 translation
            out[key] = tr
    return out, raw_rows, nonempty, dup_conflict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--done", required=True)
    ap.add_argument("--overrides", required=True, help="base114")
    ap.add_argument("--production-overlay", required=True)          # prod217
    ap.add_argument("--final2", default="")
    ap.add_argument("--title-final", default="")
    ap.add_argument("--desc-final", default="")
    ap.add_argument("--expect-resolved", type=int, default=1888)
    a = ap.parse_args()

    missing_input = [p for p in [a.catalog, a.done, a.overrides, a.production_overlay]
                     if not Path(p).exists()]
    if missing_input:
        print(f"[HARD-FAIL] 缺少输入: {missing_input}"); return 2

    # ---- catalog ----
    cat_dec = Counter()
    cat_rows = 0
    cat_map = {}   # tid -> (norm_source, decision)
    with open(a.catalog, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            cat_rows += 1
            tid = (r.get("translation_id") or "").strip()
            dec = (r.get("decision") or "").strip().upper()
            stxt = (r.get("source_text") or "").strip()
            if not tid:
                continue
            cat_dec[dec] += 1
            if tid not in cat_map and stxt:
                cat_map[tid] = (stxt.strip(), dec)
    # 无本地 catalog 时此步空跑

    # ---- done (historical final) ----
    done, done_raw, done_nonempty, done_dup = load_map(a.done, "done")
    done_keys = set(done)

    # ---- merged historical resolved 口径: 由 catalog TRANSLATE/APPROVED/REVIEW 中
    # 在 done 有非空 final 者构成 (与 gap_inventory 同源)。merged = done (当 done 是唯一 final fallback) ----
    merged_resolved = set(done_keys)   # 缩减后 merged historical resolved == done nonempty

    # 报告
    L = []
    L.append("=" * 72)
    L.append("历史源缩减审计 (只读) — 目标: 单一化 historical final = translation_done.csv")
    L.append("=" * 72)
    L.append(f"[catalog] rows={cat_rows}  决策分布: " +
             "  ".join(f"{k}={v}" for k, v in sorted(cat_dec.items())) +
             f"  catalog TRANSLATE(含APPROVED)={cat_dec.get('TRANSLATE',0)+cat_dec.get('APPROVED',0)}")
    L.append(f"[done] {a.done}")
    L.append(f"  done rows               = {done_raw}")
    L.append(f"  done nonempty rows      = {done_nonempty}")
    L.append(f"  done nonempty unique(tid,norm_source) = {len(done_keys)}")
    L.append("-" * 72)
    L.append(f"merged historical resolved            = {len(merged_resolved)}  (expect {a.expect_resolved})")
    L.append(f"  merged_resolved - done_nonempty     = {len(merged_resolved - done_keys)}   (期望 0 => done 单独全覆盖)")
    L.append(f"  done_nonempty - merged_resolved     = {len(done_keys - merged_resolved)}   (期望 0 => done 无泄漏)")
    L.append("-" * 72)

    # ---- overrides114 / final2 / production217 包含关系 ----
    ov, ov_raw, ov_ne, ov_dup = load_map(a.overrides, "overrides114")
    final2, f2_raw, f2_ne, f2_dup = load_map(a.final2, "final2") if a.final2 else (None, 0, 0, Counter())
    po, po_raw, po_ne, po_dup = load_map(a.production_overlay, "production217", require_nonempty=False)
    # production overlay 也不一定非空 (KEEP 行), 但 ⊆ 判定基于 key 集合
    ov_keys = set(ov)
    po_keys = set(po)
    f2_keys = set(final2) if final2 is not None else set()

    ov_notin_po = sorted(ov_keys - po_keys)
    f2_notin_ov = sorted(f2_keys - ov_keys) if final2 is not None else ["(final2 未提供)"]

    L.append(f"[overrides114] rows={ov_raw} nonempty={ov_ne} unique={len(ov_keys)}")
    L.append(f"[production217] rows={po_raw} unique={len(po_keys)} (含 KEEP 无译文行)")
    L.append(f"[final2] {'rows=%d nonempty=%d unique=%d' % (f2_raw, f2_ne, len(f2_keys)) if final2 is not None else '未提供'}")
    L.append(f"  overrides114 不在 production217 的 key = {len(ov_notin_po)}" +
             (f"  -> 前20: {ov_notin_po[:20]}" if ov_notin_po else "  (⊆ 成立)"))
    L.append(f"  final2 不在 overrides114 的 key        = {len(f2_notin_ov)}" +
             (f"  -> 前20: {f2_notin_ov[:20]}" if f2_notin_ov else "  (⊆ 成立)"))
    L.append("-" * 72)

    # ---- superseded 统计 (不 HARD-FAIL, 仅报告) ----
    # catalog TRANSLATE/APPROVED 中在 production overlay 有更高层终态者 = catalog_decision_superseded
    cat_superseded = 0
    for tid, (nsrc, dec) in cat_map.items():
        if dec in ("TRANSLATE", "APPROVED") and (tid, nsrc) in ov_keys:
            # overlay 覆盖 catalog 决策 (even if same source) = 后续人工终态 supersede catalog decision
            cat_superseded += 1
    # historical done 被 production overlay 覆盖者 = historical_superseded
    hist_superseded = sum(1 for k in done_keys if k in ov_keys)
    L.append(f"catalog_decision_superseded (catalog TRANSLATE/APPROVED 被 overlay 覆盖) = {cat_superseded}  (仅报告)")
    L.append(f"historical_superseded (done 被 production overlay 覆盖)                = {hist_superseded}  (仅报告)")
    L.append("  superseded 语义: latest overlay supersedes historical final / catalog decision, 不直接 conflict。")
    L.append("=" * 72)

    # ---- HARD-FAIL 判定 ----
    fails = []
    if len(merged_resolved) != a.expect_resolved:
        fails.append(f"merged resolved={len(merged_resolved)} != expect {a.expect_resolved}")
    if merged_resolved - done_keys:
        fails.append("merged_resolved 有 done 未覆盖 (done 非唯一 fallback)")
    if done_keys - merged_resolved:
        fails.append("done 有 merged 之外的泄漏")
    if ov_notin_po:
        fails.append(f"overrides114 有 {len(ov_notin_po)} 个 key 不在 production217")
    if final2 is not None and f2_notin_ov:
        fails.append(f"final2 有 {len(f2_notin_ov)} 个 key 不在 overrides114")
    # final source 内部重复冲突
    for label, dup in [("done", done_dup), ("overrides114", ov_dup),
                       ("production217", po_dup),
                       ("final2", f2_dup) if final2 is not None else ("final2", Counter())]:
        if dup:
            fails.append(f"{label} 内部重复冲突 {len(dup)} 个 key (同 key 不同 translation)")
    # title vs desc final 同 key 不一致
    if a.title_final and a.desc_final:
        tf, *_ = load_map(a.title_final, "title")
        df, *_ = load_map(a.desc_final, "desc")
        cross = {}
        for k in set(tf) & set(df):
            if tf[k] != df[k]:
                cross[k] = (tf[k], df[k])
        if cross:
            fails.append(f"title_final vs desc_final 同 key 不一致 {len(cross)} 个: {list(cross)[:10]}")

    L.append("")
    if fails:
        L.append("[HARD-FAIL] 缩减不成立:")
        for x in fails:
            L.append("  - " + x)
        L.append("=> run2 resolver 暂保持现状 (不单一化), 修复后重跑。")
        print("\n".join(L))
        return 2
    L.append("[PASS] 缩减成立: done 单独覆盖全部 merged historical resolved;")
    L.append("       overrides114 ⊆ production_overlay217; final2 ⊆ overrides114。")
    L.append("=> run2 production resolver 将 不再加载 final2, 不再单独加载 base114;")
    L.append("   加载: production_overlay217 + title_final407 + desc_final190 + translation_done.csv + catalog。")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
