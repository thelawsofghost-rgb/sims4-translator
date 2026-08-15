#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_desc_manual.py — 从真实 Windows run 生成 DESCRIPTION manual final 配置 (确定性, 只读)
=============================================================================================
DESC 17 QA_FAIL 人工终态裁决 (2026-08-15):
  KEEP 2             -> configs/desc_terminal_keep.c26.csv (已建: katverse.com / NA_Iphone ACC)
  manual final 15    -> 本脚本由 真实 QA_FAIL 行 + frozen dorothy 表 生成 configs/desc_manual_translate.c26.csv

原则:
  - 只读; 不改旧 frozen layers; 不改 translation_done_batch_desc.csv。
  - tid + source_text 一律取自 真实 run done (translation_done_batch_desc.csv) 的 QA_FAIL 行,
    绝不凭空造 source_text (避免 TID/引号/逗号 手抄错误)。
  - final_translation 取自 **仓库内 frozen dorothy 源表** (configs/desc_dorothy_frozen.c26.csv),
    列: translation_id, final_translation。
    —— 不要求用户在 Windows 手抄 CSV; 15 条已定稿的冻结译文由仓库持有。
  - 若某 QA_FAIL manual tid 缺 frozen 译文 / dorothy 表出现未知 tid / 重复 tid /
    dorothy 表含已裁决 KEEP tid -> HARD-FAIL (不写; 待补全再跑)。

输出 configs/desc_manual_translate.c26.csv (与 title_manual_translate.c26.csv 同格式):
  translation_id, source_text, translation, action, reason, notes
  action=TRANSLATE
  reason = 'DESC retry QA_FAIL 2026-08-15 人工定稿 (Dorothy), 禁止再送模型' (可在 --reason 覆盖)
  notes  = 'DESC manual final TRANSLATE (c26_desc_manual) 独立人工 reconciliation, 不改旧 frozen catalog'

用途:
  python scripts/build_desc_manual.py \
      --done output/translation_done_batch_desc.csv \
      --dorothy configs/desc_dorothy_frozen.c26.csv \
      [--out configs/desc_manual_translate.c26.csv] [--no-write]
"""
import sys, csv, argparse
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--done", required=True, help="真实 run done (translation_done_batch_desc.csv)")
    ap.add_argument("--dorothy", default="configs/desc_dorothy_frozen.c26.csv",
                    help="仓库内 frozen dorothy 译文源表: translation_id,final_translation (15 行)")
    ap.add_argument("--keep", default="configs/desc_terminal_keep.c26.csv",
                    help="已裁决 KEEP 配置 (其 tid 不得入 manual)")
    ap.add_argument("--reason", default=None, help="覆盖 reason 文本")
    ap.add_argument("--out", default="configs/desc_manual_translate.c26.csv")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()

    for p in (a.done, a.dorothy, a.keep):
        if not Path(p).exists():
            sys.exit(f"[HARD-FAIL] 输入不存在: {p}")

    # ---- 真实 run QA_FAIL 行 (tid -> source) ----
    qa = {}
    with open(a.done, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            st = (r.get("status") or "").strip()
            if st != "QA_FAIL":
                continue
            tid = (r.get("translation_id") or "").strip()
            src = (r.get("source_text") or "").strip()
            if not tid or not src:
                sys.exit(f"[HARD-FAIL] QA_FAIL 行缺 tid/source: {r}")
            if tid in qa:
                sys.exit(f"[HARD-FAIL] done 内 QA_FAIL duplicate tid: {tid}")
            qa[tid] = src
    print(f"[qa_fail] done 内 QA_FAIL 行 = {len(qa)}   (期望 17; 其中 2 KEEP + 15 manual)")

    # ---- 已裁决 KEEP tid (从 keep 配置读, 不硬编码) ----
    keep_tids = set()
    with open(a.keep, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            tid = (r.get("translation_id") or "").strip()
            if not tid:
                sys.exit("[HARD-FAIL] keep 配置缺 translation_id")
            keep_tids.add(tid)
    print(f"[keep] 已裁决 KEEP tid = {len(keep_tids)}   (期望 2): {sorted(keep_tids)}")

    # ---- frozen dorothy 译文 (仓库内源表, 非手抄) ----
    dor = {}
    with open(a.dorothy, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            tid = (r.get("translation_id") or "").strip()
            tr = (r.get("final_translation") or "").strip()
            if not tid:
                sys.exit("[HARD-FAIL] dorothy 表缺 translation_id")
            if tid in dor:
                sys.exit(f"[HARD-FAIL] dorothy 表 duplicate tid: {tid}")
            if not tr:
                sys.exit(f"[HARD-FAIL] dorothy 表 {tid} 缺 final_translation")
            dor[tid] = tr
    print(f"[dorothy] frozen 译文条数 = {len(dor)}   (期望 manual 15; 不含 2 个 KEEP tid)")

    man_tids = [t for t in qa if t not in keep_tids]
    extra_dor = [t for t in dor if t not in qa]
    missing_dor = [t for t in man_tids if t not in dor]
    keep_in_dor = [t for t in dor if t in keep_tids]

    if extra_dor:
        sys.exit(f"[HARD-FAIL] dorothy 表含 done QA_FAIL 之外的 tid: {sorted(extra_dor)}")
    if missing_dor:
        sys.exit(f"[HARD-FAIL] 以下 QA_FAIL manual tid 缺 frozen 译文: {sorted(missing_dor)}")
    if keep_in_dor:
        sys.exit(f"[HARD-FAIL] dorothy 表含已裁决 KEEP 的 tid: {sorted(keep_in_dor)} (KEEP 不入 manual)")

    print(f"[manual] QA_FAIL manual tid = {len(man_tids)}   (期望 15)")
    if len(man_tids) != 15:
        print(f"  !!! 实际 {len(man_tids)}, 期望 15 (17 QA_FAIL - 2 KEEP)。请核对 done / KEEP 裁决。")

    reason = a.reason or "DESC retry QA_FAIL 2026-08-15 人工定稿 (Dorothy), 禁止再送模型"
    rows = []
    for tid in sorted(man_tids):
        rows.append({
            "translation_id": tid,
            "source_text": qa[tid],
            "translation": dor[tid],
            "action": "TRANSLATE",
            "reason": reason,
            "notes": "DESC manual final TRANSLATE (c26_desc_manual) 独立人工 reconciliation, 不改旧 frozen catalog",
        })

    print(f"\n== DESCRIPTION manual final (15) ==")
    for r in rows:
        print(f"  {r['translation_id']}  {r['source_text']!r} -> {r['translation']!r}")

    if a.no_write:
        print("[dry-run] 未写文件。")
        return 0

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, ["translation_id", "source_text", "translation", "action", "reason", "notes"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"[写出] {a.out}  ({len(rows)} 行 manual final)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
