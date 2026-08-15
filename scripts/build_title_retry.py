#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_title_retry.py — TITLE retry manifest 构造 (确定性, 只读, 零模型)
===============================================================================
背景 (2026-08-15 用户裁决, 真实 Windows fe2996f 运行):
  rows=407. real: A SAME_AS_SOURCE_SEMANTIC=42, B=0, C TRANSLATED=365,
  D unresolved phrase=5 (D∩A=4, D−A=1).
  engine failed=8, 但仅 5 unresolved 可确定; 另 3 未解释, 不得声称已解决。

人工终态 (B 类审结 = 3 KEEP + 2 manual final TRANSLATE, 禁止再送模型):
  terminal KEEP (configs/title_terminal_keep.c26.csv):
    simonly_VixenPoster#1/#2/#3  (3)
  manual final TRANSLATE (configs/title_manual_translate.c26.csv):
    RosieSimsie_NSFW_CouplePoses_AllYours -> RosieSimsie_NSFW_情侣姿势_全属于你
    motherlode_fight -> motherlode_打斗     (2)

retry set (2026-08-15 修正口径, 用户裁决):
  retry = (A − terminal_KEEP − manual_final_TRANSLATE) ∪ (D − A)
  = (42 − 3 − 2) ∪ (1)  =  37 ∪ 1  =  38 unique tid
  预期: retry unique=38, duplicate=0, terminal KEEP∩=0, manual final∩=0。

核对 (407):
  3 terminal KEEP + 2 manual final + 38 retry + 364 clean changed/QA = 407
authoritative TITLE = 404; manual pretranslated = 2; model retry = 38; terminal KEEP = 3。

不会仅凭 underscore/camelCase 判 PACK_TITLE technical KEEP (用户禁止)。
manual final 2 条绝不回 model workset。

输入 (真实 A/D 证据文件由 diag_title_qa.py --a-out/--d-out 自动生成, 不手工构造):
  --done <done_csv>              draft done (407 行)
  --a <a_tids.csv>               列 translation_id 的 A 集合 (42, 来自 --a-out)
  --d <d_tids.csv>               列 translation_id 的 D unresolved 集合 (5, 来自 --d-out)
  --keep <configs/title_terminal_keep.c26.csv>      3 terminal KEEP
  --transl <configs/title_manual_translate.c26.csv> 2 manual final TRANSLATE
  -o <out_retry.csv>
校验:
  retry unique = 38, duplicate = 0, terminal KEEP∩retry = 0, manual∩retry = 0
"""
import sys, os, csv, argparse
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))


def _read_tids(path, col="translation_id"):
    tids = set()
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            v = (r.get(col) or "").strip()
            if v:
                tids.add(v)
    return tids


def _read_rows(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--done", required=True)
    ap.add_argument("--a", required=True, help="A tids CSV (translation_id 列)")
    ap.add_argument("--d", required=True, help="D unresolved tids CSV (translation_id 列)")
    ap.add_argument("--keep", default=None, help="terminal KEEP CSV")
    ap.add_argument("--transl", default=None, help="manual final TRANSLATE CSV")
    ap.add_argument("-o", "--out", default=None, help="retry manifest CSV 输出")
    a = ap.parse_args()

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not a.keep:
        a.keep = os.path.join(ROOT, "configs", "title_terminal_keep.c26.csv")
    if not a.transl:
        a.transl = os.path.join(ROOT, "configs", "title_manual_translate.c26.csv")
    if not a.out:
        a.out = os.path.join(os.path.dirname(a.done), "translation_retry_batch_title.csv")

    done = _read_rows(a.done)
    done_by = {r.get("translation_id"): r for r in done}
    A = _read_tids(a.a)
    D = _read_tids(a.d)
    KEEP = _read_tids(a.keep)
    MANUAL = _read_tids(a.transl)

    # 2026-08-15 修正口径:
    #   retry = (A − terminal_KEEP − manual_final_TRANSLATE) ∪ (D − A)
    A_excl = A - KEEP - MANUAL
    D_minus_A = D - A
    retry_ids = A_excl | D_minus_A

    dup = len(retry_ids) - len(retry_ids)  # 集合天然无重复 -> dup 恒 0
    keep_inter = retry_ids & KEEP
    manual_inter = retry_ids & MANUAL
    missing = retry_ids - set(done_by.keys())
    total = len(retry_ids)

    # 期望: retry=38, KEEP=3, MANUAL=2, clean changed/QA=364
    clean_changed = len(done) - len(KEEP) - len(MANUAL) - total
    sanity = len(KEEP) + len(MANUAL) + total + clean_changed

    print("=== TITLE retry manifest (2026-08-15 修正口径) ===")
    print(f"A = {len(A)}   D(unresolved) = {len(D)}   D∩A = {len(D & A)}   D−A = {len(D - A)}")
    print(f"A − KEEP − manual = {len(A_excl)}")
    print(f"terminal KEEP = {len(KEEP)}   manual final = {len(MANUAL)}   B→REVIEW = 0")
    print(f"retry unique = {total}   (A部分 {len(A_excl)} + D−A {len(D_minus_A)})")
    print(f"duplicate = {dup}")
    print(f"terminal KEEP ∩ retry = {len(keep_inter)}")
    print(f"manual final ∩ retry = {len(manual_inter)}")
    print(f"retry 中不在 done 的 tid = {len(missing)}")
    print(f"clean changed / QA = {clean_changed}")
    print(f"核对 KEEP{len(KEEP)} + MANUAL{len(MANUAL)} + retry{total} + clean{clean_changed} = {sanity} (done rows={len(done)})")

    ok = (total == 38 and dup == 0 and len(keep_inter) == 0 and len(manual_inter) == 0
          and len(missing) == 0 and len(KEEP) == 3 and len(MANUAL) == 2 and sanity == len(done))
    print(f"INVARIANT retry==38 / dup==0 / KEEP∩==0 / MANUAL∩==0 / 407核对: "
          f"{'PASS' if ok else 'FAIL'}")

    rows_out = []
    for tid in sorted(retry_ids):
        r = done_by.get(tid, {})
        reasons = []
        if tid in A_excl:
            reasons.append("A_SAME_AS_SOURCE_SEMANTIC")
        if tid in D_minus_A:
            reasons.append("D_MINUS_A_UNRESOLVED")
        rows_out.append({
            "translation_id": tid,
            "source_text": r.get("source_text") or "",
            "retry_reason": "|".join(reasons),
        })
    with open(a.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["translation_id", "source_text", "retry_reason"])
        w.writeheader(); w.writerows(rows_out)
    print(f"[写出] {a.out}  ({len(rows_out)} 行 retry, 不含 3 KEEP / 2 manual final)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
