#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit_resolved_source.py —— 旧 resolved translation source 审计 (Phase 3B2-SIDECAR)
====================================================================================
目的: 恢复"历史 CATALOG_TRANSLATE_RESOLVED 的真实最终译文到底存在哪"这一事实,
     而不是把 decision catalog 冒充 final translation source。

背景 (根因):
  output/translation_catalog.csv 是历史 decision catalog (决策目录), 不是 final
  translation source:
    - KEEP     -> 若无更高层 override, 终态 KEEP
    - TRANSLATE-> 只说明该 source 需要翻译; catalog 自己不给 translation payload,
                  必须去 historical final translation source 找已完成译文
    - REVIEW   -> 若未被更高层人工终态覆盖, 则 unresolved
  catalog 的空 translation 绝不能与 final 中文译文做 translation equality conflict。
  catalog TRANSLATE -> overlay KEEP / overlay TRANSLATE(final) 都可能是后续人工裁决
  (higher production terminal wins, 但须记录 superseded, 不能静默)。

权威来源 (与 scripts/gap_inventory.py 的 gap inventory join 完全一致):
  catalog(decision)  ×  historical final translation source
  其中 historical final translation source = 下列文件里 (translation_id, norm_source)
  对应非空 translation 的并集 (优先级: overrides 最高 -> final2 -> done):
    --overrides   output/translation_overrides.csv         [最终译文来源1, 生产 114 基线]
    --overrides2  output/translation_overrides.final2.csv  [可选最终译文来源2]
    --done        output/translation_done.csv              [最终译文来源3]
  另可再叠加已提交的人工 reconciliation layer (可选, 见 --layer 可重复):
    configs/title_manual_translate.c26.csv
    configs/title_terminal_keep.c26.csv
    configs/desc_dorothy_frozen.c26.csv
    configs/desc_terminal_keep.c26.csv
    configs/desc_content_corrections.c26.csv
  translation_cache.db 禁止作为 authoritative final source (phrase cache, 非 final QA artifact)。

报告输出:
  - translation_catalog: rows / unique tid / decision 分布 (KEEP/TRANSLATE/REVIEW/...)
  - catalog TRANSLATE/APPROVED 中: 有最终译文 (RESOLVED) 数 / 无译文 (MISSING) 数
  - catalog KEEP 数
  - 每一 historical result 文件: rows / unique tid / 非空 translation 数
  - join 后: CATALOG_TRANSLATE_RESOLVED / CATALOG_TRANSLATE_MISSING / CATALOG_KEEP
  - catalog TRANSLATE 但更高层终态覆盖 (TRANSLATE->KEEP 等 superseded) 计数

只读: 不建 sidecar, 不改任何输入, 不改 writer, 不用 cache.db。
"""
import sys, os, csv, argparse
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from phase2a_catalog import source_hash, norm_text, make_translation_id


def load_catalog(path):
    """translation_catalog.csv (决策目录) -> {tid: (norm_source, decision, translation)}"""
    cat = {}
    rows = 0
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows += 1
            tid = (r.get("translation_id") or "").strip()
            stxt = (r.get("source_text") or "").strip()
            dec = (r.get("decision") or "").strip().upper()
            tr = (r.get("translation") or "").strip()
            if not tid or not stxt:
                continue
            cat[tid] = (norm_text(stxt), dec, tr)
    return cat, rows


def load_resolved(paths):
    """historical final translation source -> {(tid, norm_source): translation}。
    非空 translation 才算最终译文。cache.db 不入这里。"""
    final = {}
    file_stats = []          # (label, rows, unique_tid, nonempty)
    for p in paths:
        if not p:
            continue
        if not Path(p).exists():
            print(f"[skip] {os.path.basename(p)}: 不存在")
            continue
        rows = 0; uniq = set(); nonempty = 0
        with open(p, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                rows += 1
                tid = (r.get("translation_id") or "").strip()
                stxt = (r.get("source_text") or "").strip()
                tr = (r.get("translation") or r.get("final_translation") or "").strip()
                if not tid or not stxt:
                    continue
                uniq.add(tid)
                if tr:
                    nonempty += 1
                    final[(tid, norm_text(stxt))] = tr
        file_stats.append((Path(p).name, rows, len(uniq), nonempty))
    return final, file_stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True, help="output/translation_catalog.csv (决策目录)")
    ap.add_argument("--overrides", required=True, help="output/translation_overrides.csv [最终译文1]")
    ap.add_argument("--done", required=True, help="output/translation_done.csv [最终译文3]")
    ap.add_argument("--overrides2", default="", help="output/translation_overrides.final2.csv [可选2]")
    ap.add_argument("--layer", action="append", default=[], help="可重复: 已提交人工 reconciliation layer csv")
    ap.add_argument("--report", default="", help="写出纯文本报告 (缺省只打印)")
    a = ap.parse_args()

    for fl, lab, val in [("--catalog", "catalog", a.catalog), ("--overrides", "overrides", a.overrides), ("--done", "done", a.done)]:
        if not Path(val).exists():
            print(f"[HARD-FAIL] {lab} 文件不存在: {val}")
            return 2

    cat, cat_rows = load_catalog(a.catalog)
    dec_count = Counter(v[1] for v in cat.values())

    src_paths = [a.overrides, a.overrides2] + a.layer + [a.done]
    final, file_stats = load_resolved(src_paths)

    L = []
    L.append("=" * 64)
    L.append("Phase 3B2-SIDECAR — old resolved source audit (decision catalog × final tree)")
    L.append("=" * 64)
    L.append(f"translation_catalog: rows={cat_rows}  unique tid={len(cat)}")
    L.append(f"  decision 分布: " + ", ".join(f"{k}={v}" for k, v in dec_count.items()))
    cat_translate = sum(1 for v in cat.values() if v[1] in ("TRANSLATE", "APPROVED"))
    cat_keep = sum(1 for v in cat.values() if v[1] == "KEEP")
    cat_review = sum(1 for v in cat.values() if v[1] == "REVIEW")
    L.append(f"  catalog TRANSLATE/APPROVED = {cat_translate} | KEEP = {cat_keep} | REVIEW = {cat_review}")

    L.append("")
    L.append("historical final translation source 逐文件 (非空 translation 才算 resolved):")
    for label, rows, utid, nonempty in file_stats:
        L.append(f"  {label:<45} rows={rows:<5} unique_tid={utid:<5} nonempty_tr={nonempty}")
    L.append(f"  合并后 (tid,norm_source) 最终译文 unique = {len(final)}   [cache.db 未用]")

    # join: catalog decision × final 译文
    resolved = 0; missing = 0
    translate_seq = 0; keep_seq = 0; review_unresolved = 0
    resolved_samples = []
    still_missing_samples = []
    for tid, (nsrc, dec, cat_tr) in cat.items():
        if dec in ("TRANSLATE", "APPROVED"):
            if (tid, nsrc) in final:
                resolved += 1
                if len(resolved_samples) < 10:
                    resolved_samples.append((tid, final[(tid, nsrc)]))
            else:
                missing += 1
                if len(still_missing_samples) < 10:
                    still_missing_samples.append(tid)
        elif dec == "KEEP":
            if (tid, nsrc) in final:
                keep_seq += 1       # KEEP 被更高层终态覆盖为有译文 (superseded)
        elif dec == "REVIEW":
            if (tid, nsrc) in final:
                translate_seq += 1
            else:
                review_unresolved += 1

    L.append("")
    L.append("join 结果 (catalog decision × final 译文树):")
    L.append(f"  CATALOG_TRANSLATE_RESOLVED   = {resolved}")
    L.append(f"  CATALOG_TRANSLATE_MISSING    = {missing}   <- 需更高层终态才能消除")
    L.append(f"  CATALOG_KEEP                 = {cat_keep}")
    L.append(f"  其中 KEEP 被更高层覆盖为有译文 (superseded) = {keep_seq}")
    L.append(f"  REVIEW 有更高层终态译文      = {translate_seq}")
    L.append(f"  REVIEW 无更高层终态 (unresolved) = {review_unresolved}")

    sup_total = translate_seq + keep_seq
    L.append(f"  catalog_superseded_action             = {sup_total}")
    L.append(f"  catalog_superseded_translation_requirement = {missing} (catalog 要求译文但无终态)")

    if resolved_samples:
        L.append("")
        L.append("RESOLVED 样本 (tid -> 最终译文):")
        for tid, tr in resolved_samples:
            L.append(f"  {tid}  ->  {tr}")
    if still_missing_samples:
        L.append("")
        L.append("TRANSLATE_MISSING 样本 (tid, 无终态译文):")
        for tid in still_missing_samples:
            L.append(f"  {tid}")

    L.append("")
    L.append("结论:")
    if missing == 0:
        L.append("  所有 catalog TRANSLATE 均有 historical final translation -> RESOLVED 完整。")
    else:
        L.append(f"  有 {missing} 个 catalog TRANSLATE 缺终态译文 -> 这些才真正 unresolved。")
    L.append("  translation_catalog 本身无 translation payload, 仅作决策/索引 (不冒充 final)。")

    out = "\n".join(L)
    print(out)
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(out + "\n", encoding="utf-8")
        print(f"\n[report] {a.report}")
    return 0 if missing == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
