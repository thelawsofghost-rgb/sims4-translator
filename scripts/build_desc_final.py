#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_desc_final.py — DESCRIPTION 190 最终唯一终态 reconciliation (确定性, 只读, 零模型)
=========================================================================================
背景 (2026-08-15 用户裁决): DESCRIPTION 190 已跑出 173 DONE + 17 QA_FAIL (25 failed
  phrase = 23 ECHO + 2 EMPTY, completion gate 正常)。不再整体重跑模型。17 QA_FAIL
  已人工终态裁决: terminal KEEP 2 + manual final 15 (Dorothy 人工译文)。

precedence (高->低):
  manual final (15)  >  terminal KEEP (2)  >  accepted model DONE (173)
  = 190.  17 QA_FAIL 必须全部被 manual(15)+KEEP(2) 覆盖 -> QA_FAIL=0。

输入 (全部 Windows 真实文件, 只读, 绝不覆盖旧证据):
  --done    output/translation_done_batch_desc.csv     (190 行: 173 DONE + 17 QA_FAIL)
  --keep    configs/desc_terminal_keep.c26.csv         (2 terminal KEEP)
  --transl  configs/desc_manual_translate.c26.csv      (15 manual final)
  -o        output/translation_done_desc_final.csv     (新 derived, 不覆盖旧证据)

终态分配 (唯一定, 由真实输入推导, 不硬编码):
  2 terminal KEEP + 15 manual final + 173 accepted model DONE = 190
  最终必须报告: rows=190 uniqueTid=190 KEEP=2 MANUAL_FINAL=15 ACCEPTED_MODEL=173
    QA_FAIL=0 PENDING=0 REVIEW=0 empty translation=0 duplicate=0 source mismatch=0
    核对 2+15+173=190 PASS

HARD-FAIL (source mismatch / duplicate tid / 不明覆盖):
  - 任一 tid 在输出中重复                     -> FAIL
  - done 中 QA_FAIL tid 不在 manual|keep      -> FAIL (未裁决 / 不明覆盖)
  - manual/keep 的 tid 不在 done 中           -> FAIL (悬空终态)
  - 同 tid 在 done/manual/keep 的 source_text
    normalized 不一致                         -> FAIL (mismatch)
  - failed phrase 责任行: 若 done 里某 tid 的
    status=QA_FAIL 但 manual/keep 均无对应    -> FAIL
  - 计数核对 != 190                          -> FAIL
"""
import sys, os, csv, argparse, re
from collections import Counter


def _norm(s):
    return (s or "").strip().casefold()


def _read(path, required=()):
    if not os.path.exists(path):
        sys.exit(f"[HARD-FAIL] 输入文件不存在: {path}")
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if required:
        got = set(rows[0].keys()) if rows else set()
        miss = set(required) - got
        if miss:
            sys.exit(f"[HARD-FAIL] {path} 缺少列 {sorted(miss)} (有 {sorted(got)})")
    return rows


def _u(tid):
    return (tid or "").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--done", required=True, help="DESC run done CSV (190 行: 173 DONE + 17 QA_FAIL)")
    ap.add_argument("--keep", required=True, help="DESC terminal KEEP CSV (2)")
    ap.add_argument("--transl", required=True, help="DESC manual final TRANSLATE CSV (15)")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()

    done = _read(a.done, required=("translation_id", "source_text", "status"))
    keep = _read(a.keep, required=("translation_id", "source_text"))
    transl = _read(a.transl, required=("translation_id", "source_text", "translation"))

    dfail = []

    def fail(msg):
        dfail.append(msg)

    # ---- done 按 tid / 状态 ----
    done_by = {}
    for r in done:
        tid = _u(r.get("translation_id"))
        if not tid:
            fail(f"done 缺 tid: {r}")
            continue
        if tid in done_by:
            fail(f"done duplicate tid: {tid}")
        done_by[tid] = r
    done_status = {t: (r.get("status") or "").strip() for t, r in done_by.items()}
    qa_fail_tids = [t for t, s in done_status.items() if s == "QA_FAIL"]
    done_tids = set(done_by)

    # ---- manual / keep by tid ----
    man_by = {}
    for r in transl:
        tid = _u(r.get("translation_id"))
        if tid in man_by:
            fail(f"manual duplicate tid: {tid}")
        man_by[tid] = r
    keep_by = {}
    for r in keep:
        tid = _u(r.get("translation_id"))
        if tid in keep_by:
            fail(f"keep duplicate tid: {tid}")
        keep_by[tid] = r
    man_tids = set(man_by)
    keep_tids = set(keep_by)

    # ---- 悬空终态: manual/keep tid 不在 done ----
    dangling = sorted((man_tids | keep_tids) - done_tids)
    if dangling:
        fail(f"manual/keep tid 不在 done (悬空终态): {dangling}")

    # ---- 未裁决 QA_FAIL: done QA_FAIL tid 不在 manual|keep ----
    unresolved_qa = sorted(set(qa_fail_tids) - man_tids - keep_tids)
    if unresolved_qa:
        fail(f"done QA_FAIL tid 未被 manual|keep 裁决: {unresolved_qa}")

    # ---- manual ∩ keep 冲突 ----
    conflict = sorted(man_tids & keep_tids)
    if conflict:
        fail(f"manual ∩ keep 冲突 tid: {conflict}")

    # ---- source mismatch (done vs manual/keep) ----
    for tid in man_tids | keep_tids:
        if tid not in done_by:
            continue
        d_src = _norm(done_by[tid].get("source_text"))
        src = _norm((man_by.get(tid) or keep_by.get(tid) or {}).get("source_text"))
        if d_src != src:
            fail(f"source mismatch: {tid} done={done_by[tid].get('source_text')!r} "
                 f"config={src!r}")

    # ---- 组装终态 (precedence: manual > keep > accepted DONE) ----
    origin = {}
    rows = []
    for tid in sorted(done_tids):
        if tid in man_by:
            origin[tid] = "MANUAL_FINAL"
            tr = man_by[tid].get("translation") or ""
            rows.append({"translation_id": tid, "source_text": done_by[tid].get("source_text"),
                         "translation": tr, "status": "DONE", "origin": "MANUAL_FINAL"})
        elif tid in keep_by:
            origin[tid] = "KEEP"
            rows.append({"translation_id": tid, "source_text": done_by[tid].get("source_text"),
                         "translation": "", "status": "KEEP", "origin": "KEEP"})
        elif done_status.get(tid) == "DONE":
            origin[tid] = "ACCEPTED_MODEL"
            tr = done_by[tid].get("translation") or ""
            rows.append({"translation_id": tid, "source_text": done_by[tid].get("source_text"),
                         "translation": tr, "status": "DONE", "origin": "ACCEPTED_MODEL"})
        else:
            # 单测: 不应到达 (QA_FAIL 必须已被裁决)
            origin[tid] = "UNRESOLVED"
            fail(f"内部: tid {tid} status={done_status.get(tid)} 未分配终态")

    # ---- 计数 ----
    n_rows = len(rows)
    n_uniq = len({r["translation_id"] for r in rows})
    n_keep = sum(1 for r in rows if r["origin"] == "KEEP")
    n_man = sum(1 for r in rows if r["origin"] == "MANUAL_FINAL")
    n_acc = sum(1 for r in rows if r["origin"] == "ACCEPTED_MODEL")
    n_qa = sum(1 for r in rows if r["status"] == "QA_FAIL")
    n_pending = sum(1 for r in rows if r["status"] == "PENDING")
    n_review = sum(1 for r in rows if r["status"] == "REVIEW")
    n_empty = sum(1 for r in rows if not (r["translation"] or "").strip() and r["origin"] != "KEEP")
    n_dup = len(rows) - n_uniq

    print(f"DONE input raw            = {len(done)}")
    print(f"  DONE rows               = {sum(1 for s in done_status.values() if s == 'DONE')}")
    print(f"  QA_FAIL rows            = {len(qa_fail_tids)}  (manual {len(man_tids)} + KEEP {len(keep_tids)} = 应 17)")
    print("\n=== DESCRIPTION final reconciliation ===")
    print(f"rows                      = {n_rows}")
    print(f"uniqueTid                 = {n_uniq}")
    print(f"terminal KEEP             = {n_keep}")
    print(f"MANUAL_FINAL              = {n_man}")
    print(f"ACCEPTED_MODEL            = {n_acc}")
    print(f"QA_FAIL                   = {n_qa}")
    print(f"PENDING                   = {n_pending}")
    print(f"REVIEW                    = {n_review}")
    print(f"empty translation         = {n_empty}")
    print(f"duplicate                 = {n_dup}")
    print(f"source mismatch           = {len([m for m in dfail if m.startswith('source mismatch')])}")
    sumok = (n_keep + n_man + n_acc == n_rows)
    print(f"核对 {n_keep}+{n_man}+{n_acc} = {n_keep + n_man + n_acc}  (rows={n_rows})  {'PASS' if sumok else 'FAIL'}")

    ok = (n_rows == 190 and n_uniq == 190 and n_keep == 2 and n_man == 15
          and n_acc == 173 and n_qa == 0 and n_pending == 0 and n_review == 0
          and n_empty == 0 and n_dup == 0 and not dfail and sumok)
    print(f"FINAL_HARD_GATE: {'PASS' if ok else 'FAIL'}  (期望 rows=190 uniq=190 KEEP=2 MANUAL=15 ACCEPTED=173 QA/PENDING/REVIEW=0)")

    if dfail:
        print("\n[HARD-FAIL 明细]:")
        for m in dfail:
            print(f"  - {m}")

    if not ok:
        sys.exit(1)

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, ["translation_id", "source_text", "translation", "status", "origin"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"[写出] {a.out}  ({n_rows} 行 = {n_uniq} unique). 不覆盖旧证据.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
