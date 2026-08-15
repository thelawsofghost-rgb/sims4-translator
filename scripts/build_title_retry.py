#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_title_retry.py — TITLE retry manifest 构造 (确定性, 只读, 零模型)
===============================================================================
背景 (2026-08-15 用户裁决, 真实 Windows 运行):
  rows=407. real: A SAME_AS_SOURCE_SEMANTIC=37, B candidate=5(3 KEEP+2 TRANSLATE),
  C TRANSLATED=365, D unresolved cache-miss=5 (D∩A=4, D−A=1, A∪D=38).
  engine failed=8 但仅 5 unresolved 可确定; 另 3 个未经解释, 不得声称已解决。

retry set:
  retry = (A ∪ D_model_unresolved) ∪ B→TRANSLATE
  = 38 + 2 = 40 unique tid
  3 个 B→KEEP (terminal) 不进 retry。

不会仅凭 underscore/camelCase 判 PACK_TITLE technical KEEP (用户禁止)。

输入:
  --done <done_csv>              draft done (407 行)
  --a <a_tids.csv>               列 translation_id 的 A 集合 (37)
  --d <d_tids.csv>               列 translation_id 的 D unresolved 集合 (5)
  --keep <title_terminal_keep.csv>   3 个 terminal KEEP
  --transl <title_manual_translate.csv>  2 个 B→TRANSLATE manual
  -o <out_retry.csv>
校验:
  retry unique = 40, duplicate = 0, terminal KEEP ∩ retry = 0
"""
import sys, os, csv, argparse
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from phase2a_catalog import norm_text


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
    ap.add_argument("--keep", default=None, help="terminal KEEP CSV default output/title_terminal_keep.csv")
    ap.add_argument("--transl", default=None, help="B->TRANSLATE manual CSV")
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
    TR = _read_tids(a.transl)

    auD = A | D
    retry_ids = (auD | TR) - KEEP

    # 校验
    dup = len(auD | TR) - len(retry_ids)  # 应 0 (TR 不在 KEEP 即可; auD 本身去重)
    keep_inter = retry_ids & KEEP
    total = len(retry_ids)
    missing = retry_ids - set(done_by.keys())

    print("=== TITLE retry manifest ===")
    print(f"A = {len(A)}   D(unresolved) = {len(D)}   A∪D = {len(auD)}")
    print(f"B->TRANSLATE manual = {len(TR)}   terminal KEEP = {len(KEEP)}")
    print(f"retry unique = {total}")
    print(f"duplicate    = {dup}")
    print(f"terminal KEEP ∩ retry = {len(keep_inter)}")
    print(f"retry 中不在 done 的 tid = {len(missing)}")
    ok = (total == 40 and dup == 0 and len(keep_inter) == 0 and len(missing) == 0)
    print(f"INVARIANT retry==40 / dup==0 / KEEP∩==0 : {'PASS' if (total==40 and dup==0 and len(keep_inter)==0 and len(missing)==0) else 'FAIL'}")

    # 写出
    rows_out = []
    for tid in sorted(retry_ids):
        r = done_by.get(tid, {})
        rows_out.append({
            "translation_id": tid,
            "source_text": r.get("source_text") or "",
            "retry_reason": ("A_SAME_AS_SOURCE_SEMANTIC" if tid in A else "") +
                            ("|D_UNRESOLVED" if tid in D else "") +
                            ("|B_TRANSLATE_MANUAL" if tid in TR else ""),
        })
    with open(a.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["translation_id", "source_text", "retry_reason"])
        w.writeheader(); w.writerows(rows_out)
    print(f"[写出] {a.out}  ({len(rows_out)} 行 retry)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
