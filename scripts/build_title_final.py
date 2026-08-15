#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_title_final.py — TITLE 407 最终唯一终态 reconciliation (确定性, 只读, 零模型)
====================================================================================
背景 (2026-08-15 用户裁决, Windows 真实运行已停 retry):
  rows=407. retry_v2 (36 行) = 28 DONE + 8 QA_FAIL。8 QA_FAIL 已人工定稿入 manual,
  8 条 DONE 内容修正亦入 manual (共 manual=20)。remaining model retry = 0。

precedence (高->低):
  manual final (20)  >  terminal KEEP (3)  >  retry_v2 accepted DONE (20)  >  original clean (364)

输入 (全部 Windows 真实文件, 只读, 绝不覆盖旧证据):
  --draft   output/translation_done_batch_title.csv           (407 行, 原 draft)
  --retry   output/translation_done_batch_title_retry_v2.csv   (最后模型结果, 36 行)
  --keep    configs/title_terminal_keep.c26.csv               (3 terminal KEEP)
  --transl  configs/title_manual_translate.c26.csv            (20 manual final)
  -o        output/translation_done_title_final.csv           (新 derived, 不覆盖旧证据)

终态分配 (唯一定, 由真实输入推导, 不硬编码):
  3 terminal KEEP + 20 manual final + 20 retry_v2 accepted + 364 clean = 407
  最终必须报告: rows=407 uniqueTid=407 KEEP=3 MANUAL_FINAL=20 RETRY_ACCEPTED=20
    CLEAN_ORIGINAL=364; QA_FAIL=0 REVIEW=0 PENDING=0 empty translation=0
    duplicate=0 source mismatch=0; 3+20+20+364=407 PASS

HARD-FAIL (source_text mismatch / duplicate tid / 不明覆盖):
  - 任一 tid 在输出中重复                          -> FAIL
  - retry_v2 的 tid 不在 draft 中                 -> FAIL (over 子集)
  - 同 tid 在 draft/retry/manual/keep 的 normalized
    source_text 不一致                            -> FAIL (mismatch)
  - retry_v2 中 QA_FAIL 且不在 manual final        -> FAIL (不明覆盖 / 未裁决)
  - manual/keep 的 tid 不在 draft 中              -> FAIL (悬空终态)
  - 计数核对 != 407                              -> FAIL
"""
import sys, os, csv, argparse, re


def _norm(s):
    return (s or "").strip().casefold()


def _read_rows(path, required=()):
    if not os.path.exists(path):
        sys.exit(f"[HARD-FAIL] 输入文件不存在: {path}")
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if required:
        got = set(rows[0].keys())
        miss = set(required) - got
        if miss:
            sys.exit(f"[HARD-FAIL] {path} 缺少列 {sorted(miss)} (有 {sorted(got)})")
    return rows


def _by_tid(rows):
    m = {}
    for r in rows:
        tid = (r.get("translation_id") or "").strip()
        if tid:
            m.setdefault(tid, []).append(r)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", required=True, help="原 TITLE draft done CSV (407 行)")
    ap.add_argument("--retry", required=True, help="最后模型结果 retry_v2 CSV (36 行)")
    ap.add_argument("--keep", required=True, help="terminal KEEP CSV")
    ap.add_argument("--transl", required=True, help="manual final TRANSLATE CSV")
    ap.add_argument("-o", "--out", required=True, help="final CSV 输出")
    a = ap.parse_args()

    draft = _read_rows(a.draft, required=("translation_id", "source_text"))
    retry = _read_rows(a.retry, required=("translation_id", "source_text"))
    keep = _read_rows(a.keep, required=("translation_id", "source_text"))
    transl = _read_rows(a.transl, required=("translation_id", "source_text", "translation"))

    draft_by = _by_tid(draft)
    retry_by = _by_tid(retry)
    keep_by = _by_tid(keep)
    transl_by = _by_tid(transl)

    KEEP_tids = set(keep_by)
    MANUAL_tids = set(transl_by)
    draft_tids = set(draft_by)
    retry_tids = set(retry_by)

    # ---- HARD-FAIL: duplicate tid (分输入内 + 全量) ----
    for name, by in (("draft", draft_by), ("retry", retry_by),
                     ("keep", keep_by), ("transl", transl_by)):
        dup = {t: v for t, v in by.items() if len(v) > 1}
        if dup:
            sys.exit(f"[HARD-FAIL] {name} 内 duplicate tid: {sorted(dup)}")

    # ---- HARD-FAIL: 终态配置悬空 (manual/keep tid 不在 draft) ----
    dangling = (MANUAL_tids | KEEP_tids) - draft_tids
    if dangling:
        sys.exit(f"[HARD-FAIL] manual/keep 终态 tid 不在 draft (悬空): {sorted(dangling)}")

    # ---- HARD-FAIL: retry 必须是 draft 子集 ----
    over = retry_tids - draft_tids
    if over:
        sys.exit(f"[HARD-FAIL] retry_v2 tid 不在 draft (over 子集): {sorted(over)}")

    # ---- HARD-FAIL: 同 tid source_text 一致性 ----
    mism = []
    for tid in retry_tids:
        ns_d = _norm(draft_by[tid][0].get("source_text"))
        ns_r = _norm(retry_by[tid][0].get("source_text"))
        if ns_d != ns_r:
            mism.append(f"{tid}: draft={ns_d!r} retry={ns_r!r}")
    for tid in MANUAL_tids:
        ns_d = _norm(draft_by[tid][0].get("source_text"))
        ns_m = _norm(transl_by[tid][0].get("source_text"))
        if ns_d != ns_m:
            mism.append(f"{tid}: draft={ns_d!r} manual={ns_m!r}")
    for tid in KEEP_tids:
        ns_d = _norm(draft_by[tid][0].get("source_text"))
        ns_k = _norm(keep_by[tid][0].get("source_text"))
        if ns_d != ns_k:
            mism.append(f"{tid}: draft={ns_d!r} keep={ns_k!r}")
    if mism:
        sys.exit("[HARD-FAIL] source_text mismatch:\n  " + "\n  ".join(mism))

    # ---- precedence 覆盖 ----
    out = []
    empty_trans = 0
    n_keep = n_manual = n_retry = n_clean = 0

    for tid in sorted(draft_tids):
        d = draft_by[tid][0]
        src = d.get("source_text") or ""
        base = dict(d)

        if tid in MANUAL_tids:
            tr = (transl_by[tid][0].get("translation") or "").strip()
            base["translation"] = tr
            base["status"] = "MANUAL_FINAL"
            base["origin"] = "manual_final"
            n_manual += 1
        elif tid in KEEP_tids:
            base["translation"] = ""
            base["status"] = "KEEP"
            base["origin"] = "terminal_keep"
            n_keep += 1
        elif tid in retry_tids:
            rr = retry_by[tid][0]
            rstatus = (rr.get("status") or "").strip().upper()
            if rstatus == "DONE":
                tr = (rr.get("translation") or "").strip()
                base["translation"] = tr
                base["status"] = "RETRY_ACCEPTED"
                base["origin"] = "retry_v2_done"
                n_retry += 1
            else:
                # QA_FAIL / 其它未裁决且不在 manual -> 不明覆盖
                sys.exit(f"[HARD-FAIL] retry_v2 {tid} status={rstatus!r} "
                         f"且不在 manual final (未裁决/不明覆盖)")
        else:
            # 原 clean 结果 (含已翻译 / 已 hardcode QA 的 364)
            base["status"] = "CLEAN_ORIGINAL"
            base["origin"] = "clean_original"
            n_clean += 1

        if not (base.get("translation") or "").strip():
            # empty translation 只统计非 KEEP 行 (KEEP 终态本就空译文, 属预期终态)
            if base.get("status") != "KEEP":
                empty_trans += 1
        out.append(base)

    # ---- 全量 duplicate tid 检查 (输出) ----
    out_tids = [r.get("translation_id") for r in out]
    dup_out = len(out_tids) - len(set(out_tids))

    # ---- 最终报告计数 ----
    rows = len(out)
    unique = len(set(out_tids))
    total = n_keep + n_manual + n_retry + n_clean

    print("=== TITLE final reconciliation (deterministic, zero-model) ===")
    print(f"rows          = {rows}")
    print(f"uniqueTid     = {unique}")
    print(f"KEEP          = {n_keep}")
    print(f"MANUAL_FINAL  = {n_manual}")
    print(f"RETRY_ACCEPTED= {n_retry}")
    print(f"CLEAN_ORIGINAL= {n_clean}")
    print(f"QA_FAIL       = {len([r for r in out if r.get('status')=='QA_FAIL'])}")
    print(f"REVIEW        = {len([r for r in out if r.get('status')=='REVIEW'])}")
    print(f"PENDING       = {len([r for r in out if r.get('status')=='PENDING'])}")
    print(f"empty trans   = {empty_trans}")
    print(f"duplicate     = {dup_out}")
    print(f"source mismatch= {len(mism)}")
    print(f"核对 {n_keep}+{n_manual}+{n_retry}+{n_clean} = {total} (须=407) : "
          f"{'PASS' if total == 407 else 'FAIL'}")

    ok = (rows == 407 and unique == 407 and n_keep == 3 and n_manual == 20
          and n_retry == 20 and n_clean == 364 and total == 407
          and empty_trans == 0 and dup_out == 0 and len(mism) == 0)
    print(f"FINAL_HARD_GATE: {'PASS' if ok else 'FAIL'}")
    if not ok:
        sys.exit(1)

    with open(a.out, "w", encoding="utf-8-sig", newline="") as f:
        # 保留 draft 原列 + 新增 origin/status_final
        fieldnames = list(draft[0].keys())
        for extra in ("status", "origin"):
            if extra not in fieldnames:
                fieldnames.append(extra)
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in out:
            w.writerow(r)
    print(f"[写出] {a.out}  ({rows} 行, 不覆盖旧证据)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
