#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_desc_final.py — DESCRIPTION 190 最终唯一终态 reconciliation (确定性, 只读, 零模型)
=========================================================================================
背景 (2026-08-15 用户裁决): DESCRIPTION 190 已跑出 173 DONE + 17 QA_FAIL (25 failed
  phrase = 23 ECHO + 2 EMPTY, completion gate 正常)。不再整体重跑模型。17 QA_FAIL
  已人工终态裁决: terminal KEEP 2 + manual final 15 (Dorothy 人工译文)。

终态 provenance (固定, 用户裁决 2026-08-15):
  KEEP                 = 2
  MANUAL_QA_FAIL       = 15
  CONTENT_CORRECTION   = 37   (configs/desc_content_corrections.c26.csv)
  ACCEPTED_MODEL       = 136
  total                = 190
  content QA: candidates=81  resolved_by_correction=37  resolved_by_allowlist=44
              unresolved=0  (allowlist 固化于 configs/desc_content_allowlist.c26.txt)

⚠️ 冲突 fail-closed (precedence 只是实现细节, 绝不掩盖配置冲突):
  manual ∩ corr / manual ∩ keep / corr ∩ keep 任一非0 -> HARD-FAIL;
  allowlist/corr 含非 candidate tid -> HARD-FAIL; candidate 未 resolve -> HARD-FAIL;
  duplicate tid (candidates/allowlist/corr) -> HARD-FAIL;
  缺 --corr (计数不符) -> HARD-FAIL。

输入 (全部 Windows 真实文件, 只读, 绝不覆盖旧证据):
  --done    output/translation_done_batch_desc.csv     (190 行: 173 DONE + 17 QA_FAIL)
  --keep    configs/desc_terminal_keep.c26.csv         (2 terminal KEEP)
  --transl  configs/desc_manual_translate.c26.csv      (15 manual final)
  --corr    configs/desc_content_corrections.c26.csv   (37 content-qa correction)
  -o        output/translation_done_desc_final.csv     (新 derived, 不覆盖旧证据)

content-QA 闸门 (用户裁决 2026-08-15, fail-closed):
  在 final 之前必须先过真实 DONE173 content QA:
    manual15 / KEEP2 落盘 -> desc_content_qa.py 检查真实 DONE173 ->
    suspicious candidates 人工复核 -> 若发现错译则加 DESC manual correction layer ->
    suspicious 全部 resolved -> 才 build_desc_final.py。
  **production fail-closed**: --qa-candidates 与 --qa-allowlist 为必填 (default dict 的
  default=None 会在无值时触发 HARD-FAIL, 不靠“提醒”放行)。
    --qa-candidates: content QA 输出的 REVIEW_CANDIDATE csv;
    --qa-allowlist  : 已 resolve 的 suspicious tid 清单 (每行一个 tid 或 csv 首列)。
    其中每个 suspicious tid 必须出现在 allowlist, 否则 HARD-FAIL
    unresolved_content_review > 0 (禁止把未处理 candidate 记 ACCEPTED_MODEL)。
  --allow-zero-candidates: 允许 candidates 为空即为全部 resolve。
  **测试专用**: 仅 white-box / 无 QA fixture 时, 显式传 --test-no-qa 才放行
  (打印 TEST 标记并跳过 content-QA 闸门)。production 默认必须 fail-closed,
  绝不因缺少 candidate 而允许静默放行。

  --corr <correction layer csv> (configs/desc_content_corrections.c26.csv, 37):
    独立 content-QA correction layer, 修正 DONE173 内 REVIEW_CANDIDATE 的错译;
    不改旧 desc manual15 / KEEP2。precedence: manual > corr > keep > accepted DONE。
    corr 只能指向 done status=DONE 的 accepted-model tid; 与 manual/keep 互斥。

终态分配 (唯一定, 由真实输入推导, 不硬编码):
  2 terminal KEEP + 15 manual final + 37 correction + 136 accepted model DONE = 190
  最终必须报告: rows=190 uniqueTid=190 KEEP=2 MANUAL_QA_FAIL=15 CORRECTION=37
    ACCEPTED_MODEL=136 QA_FAIL=0 PENDING=0 REVIEW=0 empty=0 duplicate=0 source mismatch=0
    核对 2+15+37+136=190 PASS

HARD-FAIL (source mismatch / duplicate tid / 不明覆盖):
  - 任一 tid 在输出中重复                     -> FAIL
  - done 中 QA_FAIL tid 不在 manual|keep      -> FAIL (未裁决 / 不明覆盖)
  - manual/keep 的 tid 不在 done 中           -> FAIL (悬空终态)
  - 同 tid 在 done/manual/keep 的 source_text
    normalized 不一致                         -> FAIL (mismatch)
  - failed phrase 责任行: 若 done 里某 tid 的
    status=QA_FAIL 但 manual/keep 均无对应    -> FAIL
  - unresolved_content_review > 0            -> FAIL (content-QA candidate 未 resolve)
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
    ap.add_argument("--transl", required=True, help="DESC manual final TRANSLATE CSV (15, QA_FAIL 裁决)")
    ap.add_argument("--corr", default=None,
                    help="DESC content-qa correction layer CSV (configs/desc_content_corrections.c26.csv, 37)")
    ap.add_argument("--qa-candidates", default=None,
                    help="content QA 的 REVIEW_CANDIDATE csv (production 必填, fail-closed 于脚本内强制)")
    ap.add_argument("--qa-allowlist", default=None,
                    help="已 resolve 的 suspicious tid 清单 (production 必填, fail-closed 于脚本内强制; 每行一个 tid)")
    ap.add_argument("--allow-zero-candidates", action="store_true",
                    help="candidates 为空视为已全部 resolve")
    ap.add_argument("--test-no-qa", action="store_true",
                    help="仅测试/无 QA fixture 时显式跳过 content-QA 闸门 (production 禁用)")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()

    done = _read(a.done, required=("translation_id", "source_text", "status"))
    keep = _read(a.keep, required=("translation_id", "source_text"))
    transl = _read(a.transl, required=("translation_id", "source_text", "translation"))
    corr = _read(a.corr, required=("translation_id", "source_text", "translation")) if a.corr else []

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
    corr_by = {}
    for r in corr:
        tid = _u(r.get("translation_id"))
        if tid in corr_by:
            fail(f"corr duplicate tid: {tid}")
        corr_by[tid] = r
    man_tids = set(man_by)
    keep_tids = set(keep_by)
    corr_tids = set(corr_by)

    # ---- 悬空终态: manual/keep/corr tid 不在 done ----
    dangling = sorted((man_tids | keep_tids | corr_tids) - done_tids)
    if dangling:
        fail(f"manual/keep/corr tid 不在 done (悬空终态): {dangling}")

    # ---- 未裁决 QA_FAIL: done QA_FAIL tid 不在 manual|keep ----
    unresolved_qa = sorted(set(qa_fail_tids) - man_tids - keep_tids)
    if unresolved_qa:
        fail(f"done QA_FAIL tid 未被 manual|keep 裁决: {unresolved_qa}")

    # ---- manual ∩ keep / manual ∩ corr / keep ∩ corr 冲突 ----
    conflict = sorted((man_tids & keep_tids) | (man_tids & corr_tids) | (keep_tids & corr_tids))
    if conflict:
        fail(f"manual/keep/corr 相互冲突 tid: {conflict}")

    # ---- corr 只能指向 DONE (非 QA_FAIL) 的 accepted-model tid ----
    corr_not_done = sorted(t for t in corr_tids if done_status.get(t) != "DONE")
    if corr_not_done:
        fail(f"corr tid 的 done status != DONE (correction 只能修正已接受模型): {corr_not_done}")

    # ---- source mismatch (done vs manual/keep/corr) ----
    for tid in man_tids | keep_tids | corr_tids:
        if tid not in done_by:
            continue
        d_src = _norm(done_by[tid].get("source_text"))
        cfg = (man_by.get(tid) or keep_by.get(tid) or corr_by.get(tid) or {})
        src = _norm(cfg.get("source_text"))
        if d_src != src:
            fail(f"source mismatch: {tid} done={done_by[tid].get('source_text')!r} "
                 f"config={src!r}")

    # ---- content-QA 闸门 (fail-closed): 未 resolve 的 suspicious 禁止记 ACCEPTED_MODEL ----
    if not a.qa_candidates or not a.qa_allowlist:
        # 即使 argparse required 兜底, 仍防御性 fail-closed: 缺任一即拒绝
        if a.test_no_qa:
            print("  [TEST] --test-no-qa: 跳过 content-QA 闸门 (仅测试/无 QA fixture, production 禁用)")
        else:
            sys.exit("[HARD-FAIL] content-QA 闸门 fail-closed: 必须同时提供 "
                     "--qa-candidates 与 --qa-allowlist (production 不允许靠提醒绕过 "
                     "content QA)。测试用 --test-no-qa 显式跳过。")
    elif a.test_no_qa:
        print("  [TEST] --test-no-qa: 跳过 content-QA 闸门 (仅测试, production 禁用)")
    else:
        cand_tids = set()
        import os as _os
        if not _os.path.exists(a.qa_candidates):
            fail(f"qa-candidates 文件不存在: {a.qa_candidates}")
        else:
            with open(a.qa_candidates, encoding="utf-8-sig") as _f:
                _rd = list(csv.DictReader(_f))
            _cand_dup = [t for t, c in Counter(
                _u(r.get("translation_id")) for r in _rd if _u(r.get("translation_id"))).items() if c > 1]
            if _cand_dup:
                fail(f"qa-candidates duplicate tid: {_cand_dup}")
            for _r in _rd:
                _t = _u(_r.get("translation_id"))
                if _t:
                    cand_tids.add(_t)
        allow_tids = set()
        _allow_raw = []
        if a.qa_allowlist and _os.path.exists(a.qa_allowlist):
            with open(a.qa_allowlist, encoding="utf-8-sig") as _f:
                for _ln in _f:
                    _t = _ln.strip().split(",")[0].strip()
                    if _t and _t.lower() not in ("translation_id", "tid"):
                        _allow_raw.append(_t)
        elif a.qa_allowlist:
            # 内联逗号分隔 allowlist
            _allow_raw = [x.strip() for x in a.qa_allowlist.split(",") if x.strip()]
        _allow_dup = [t for t, c in Counter(_allow_raw).items() if c > 1]
        if _allow_dup:
            fail(f"allowlist duplicate tid: {_allow_dup}")
        allow_tids = set(_allow_raw)
        if a.allow_zero_candidates and not cand_tids:
            print("  [content-qa] 无 suspicious candidate (allow-zero-candidates), resolve 通过。")
        else:
            # fail-closed: 不允许额外 allowlist/corr TID 吞掉变动后的 candidate 集
            allow_extra = sorted(allow_tids - cand_tids)
            if allow_extra:
                fail(f"allowlist 含非 candidate tid: {allow_extra} (candidate 集变动会被旧 allowlist 静默吞掉, 禁止)")
            corr_extra = sorted(corr_tids - cand_tids)
            if corr_extra:
                fail(f"correction 含非 candidate tid: {corr_extra}")
            # corrected (corr layer) 亦视为已 resolve; 剩余需在 allowlist
            resolved_extra = allow_tids | corr_tids
            unresolved_content_review = sorted(cand_tids - resolved_extra)
            if unresolved_content_review:
                fail(f"unresolved_content_review > 0: {unresolved_content_review} "
                     f"(content-QA suspicious 未 resolve, 禁止记 ACCEPTED_MODEL)")
            else:
                print(f"  [content-qa] 全部 {len(cand_tids)} 个 suspicious candidate 已 resolve "
                      f"(corr {len(corr_tids & cand_tids)} + allowlist {len(allow_tids & cand_tids)})")

    # ---- 组装终态 (precedence: manual > corr > keep > accepted DONE) ----
    origin = {}
    rows = []
    for tid in sorted(done_tids):
        if tid in man_by:
            origin[tid] = "MANUAL_QA_FAIL"
            tr = man_by[tid].get("translation") or ""
            rows.append({"translation_id": tid, "source_text": done_by[tid].get("source_text"),
                         "translation": tr, "status": "DONE", "origin": "MANUAL_QA_FAIL"})
        elif tid in corr_by:
            origin[tid] = "CORRECTION"
            tr = corr_by[tid].get("translation") or ""
            rows.append({"translation_id": tid, "source_text": done_by[tid].get("source_text"),
                         "translation": tr, "status": "DONE", "origin": "CORRECTION"})
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
    n_man = sum(1 for r in rows if r["origin"] == "MANUAL_QA_FAIL")
    n_corr = sum(1 for r in rows if r["origin"] == "CORRECTION")
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
    print(f"MANUAL_QA_FAIL              = {n_man}")
    print(f"CORRECTION                = {n_corr}")
    print(f"ACCEPTED_MODEL            = {n_acc}")
    print(f"QA_FAIL                   = {n_qa}")
    print(f"PENDING                   = {n_pending}")
    print(f"REVIEW                    = {n_review}")
    print(f"empty translation         = {n_empty}")
    print(f"duplicate                 = {n_dup}")
    print(f"source mismatch           = {len([m for m in dfail if m.startswith('source mismatch')])}")
    print(f"unresolved_content_review = {len([m for m in dfail if m.startswith('unresolved_content_review')])}")
    sumok = (n_keep + n_man + n_corr + n_acc == n_rows)
    print(f"核对 {n_keep}+{n_man}+{n_corr}+{n_acc} = {n_keep + n_man + n_corr + n_acc}  (rows={n_rows})  {'PASS' if sumok else 'FAIL'}")

    unresolved_review = any(m.startswith("unresolved_content_review") for m in dfail)
    ok = (n_rows == 190 and n_uniq == 190 and n_keep == 2 and n_man == 15
          and n_corr == 37 and n_acc == 136 and n_qa == 0 and n_pending == 0 and n_review == 0
          and n_empty == 0 and n_dup == 0 and not unresolved_review and not dfail and sumok)
    print(f"FINAL_HARD_GATE: {'PASS' if ok else 'FAIL'}  "
          f"(期望 rows=190 uniq=190 KEEP=2 MANUAL=15 CORR=37 ACCEPTED=136 QA/PENDING/REVIEW=0)")

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
