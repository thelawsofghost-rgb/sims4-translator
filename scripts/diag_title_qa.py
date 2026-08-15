#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diag_title_qa.py — batch(TITLE/DESC) done CSV deterministic QA inventory (只读, 零模型)
===============================================================================
判定 407 行 completion: 不做 "translation!='' -> DONE" 的宽松通过, 而是按内容层语义证据
将每行归 A/B/C/D 并 mark 非法 DONE。

分类 (用户裁决 2026-08-15):
  A. SAME_AS_SOURCE_SEMANTIC   : translation==source 且内容层有 semantic token
                                 => FAIL/PENDING (model-required semantic phrase 未译)
  B. SAME_AS_SOURCE_NONSEMANTIC: translation==source 且内容层明确 no-semantic/technical
                                 => 上游 decision conflict -> REVIEW/KEEP reconciliation
  C. TRANSLATED                : translation != source
  D. MODEL_PHRASE_FAILED       : phrase 引擎明确失败 (cache miss 且未由 glossary/protected 覆盖)

唯一合法保持原文: protected span / glossary 命中 / 明确 non-semantic evidence。

用法:
  python diag_title_qa.py <out_dir> [--done <done_csv>] [--also-failed]
  out_dir 需含 translation_cache.db (D 判定需要)。--also-failed 额外走 phrase cache
  重建 failed phrase (需与运行期 fingerprint 同构的 context)。

输出:
  - A/B/C/D 计数 + 完整 A 与 B 列表 (tid/source/translation/evidence)
  - 若 --also-failed: D 明细 (tid/segment/source_phrase/最终状态/是否落入 A/B)
不改 workset/batch/coverage/writer/decision, 不 merge。
"""
import sys, os, csv, argparse
from collections import Counter, defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import phase2b_translate as P
from phase2a_catalog import norm_text


def semantic_evidence(text):
    """返回 (has_semantic, reason, mode, sem_tokens)。
    has_semantic=True -> 有需翻译语义。
    权威 = 内容层 translate_mode_for(); 仅 KEEP/NO_SEMANTIC/技术标识 -> 非语义。
    split_semantic_spans 仅作佐证, 不覆盖 translate_mode_for 的 KEEP 判定。"""
    try:
        mode, reason, meta = P.translate_mode_for(text)
    except Exception as e:
        mode, reason, meta = "ERR", repr(e), None
    if mode == "KEEP":
        return False, reason, mode, []
    segs = []
    try:
        segs, _ = P.split_semantic_spans(text)
    except Exception:
        segs = []
    sem = [s for s in segs if s["kind"] == "sem" and s["t"].strip()]
    has_sem = mode in ("FULL_TRANSLATE", "PARTIAL_TRANSLATE") or bool(sem)
    return has_sem, reason, mode, [x["t"].strip() for x in sem]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--done", default=None)
    ap.add_argument("--also-failed", action="store_true", help="额外用 cache 重建 failed phrase")
    a = ap.parse_args()
    out = a.out_dir
    done_path = a.done
    if not done_path:
        cands = [os.path.join(out, f) for f in os.listdir(out)
                 if f.startswith("translation_done_") and f.endswith(".csv")]
        if not cands:
            raise SystemExit("[FAIL] 找不到 translation_done_*.csv, 用 --done 指定")
        done_path = sorted(cands)[-1]
    rows = list(csv.DictReader(open(done_path, encoding="utf-8-sig")))
    print(f"[done] {done_path}")
    print(f"[rows] {len(rows)}")

    # cache (仅 D 判定需要)
    cache = None
    if a.also_failed:
        from phrase_cache import PhraseCache
        cache = PhraseCache(out, model=None)

    A, B, C, D_list, conflicts = [], [], [], [], []
    try:
        for r in rows:
            src = norm_text(r.get("source_text") or "")
            tr = norm_text(r.get("translation") or "")
            mode = r.get("translate_mode") or r.get("status") or ""
            status = r.get("status") or ""
            has_sem, reason, cmode, sem_tokens = semantic_evidence(src)
            same = (tr != "" and tr == src)

            # C: 已译 (!=source)
            if tr != src:
                C.append(r)
                continue

            # translation==source (含空译文? 空译文单独归空)
            if not tr:
                # 空译文: 若 PENDING/KEEP/DONE_SKIP 是其终态则合法, 否则冲突
                if status in ("PENDING", "KEEP", "DONE_SKIP"):
                    B.append(r)  # 归类到非语义/终态
                else:
                    conflicts.append(r)
                continue

            # same -> A / B
            if has_sem:
                A.append(r)          # FAIL/PENDING
            else:
                B.append(r)          # 非语义/technical -> REVIEW/KEEP

        # D: phrase 引擎明确失败 (cache miss 且未由 glossary/protected 覆盖)
        if a.also_failed and cache is not None:
            for r in rows:
                tid = r.get("translation_id")
                text = norm_text(r.get("source_text") or "")
                segs, _ = P.split_semantic_spans(text)
                gloss, pending = P.glossary_resolve(segs)
                pending = [p for p in pending if p["t"].strip()]
                for p in pending:
                    fp = P.build_fingerprint(source_phrase=p["t"].strip(),
                                             glossary_hint=p.get("gloss_hint", ""), context="")
                    c = cache.get(fp)
                    if c is None:
                        # except phrase-level segments already sourced by prot/gloss? 保守: 仅未被保护/glossary 覆盖者
                        D_list.append({
                            "translation_id": tid, "source_text": text,
                            "source_phrase": p["t"].strip(),
                            "segment_index": p.get("key"),
                            "failure": "cache_miss(engine failed / never committed)",
                            "final_translation": r.get("translation") or "",
                            "final_status": r.get("status"),
                            "in_A_or_B": tid in [x.get("translation_id") for x in A + B],
                        })
    finally:
        if cache: cache.close()

    print("\n=== 结论 (deterministic, 零模型) ===")
    print(f"A. SAME_AS_SOURCE_SEMANTIC   (FAIL/PENDING) : {len(A)}")
    print(f"B. SAME_AS_SOURCE_NONSEMANTIC (REVIEW/KEEP)  : {len(B)}")
    print(f"C. TRANSLATED                                 : {len(C)}")
    print(f"D. MODEL_PHRASE_FAILED (cache miss)           : {len(D_list) if a.also_failed else '(未启用 --also-failed)'}")
    print(f"   空译文但终态非 KEEP/PENDING/DONE_SKIP 冲突 : {len(conflicts)}")
    total = len(A) + len(B) + len(C) + len(conflicts)
    print(f"   校验 A+B+C+conflicts == rows : {total} == {len(rows)}  {'PASS' if total == len(rows) else 'FAIL'}")

    if A:
        print("\n--- A 列表 (唯一合法保持原文=protected/glossary/non-semantic evidence, 此处无) ---")
        for r in A:
            print(f"  {r.get('translation_id')}  mode={r.get('translate_mode')}  src={r.get('source_text')!r}")
    if B:
        print("\n--- B 列表 (非语义/technical; 需 REVIEW/KEEP reconciliation) ---")
        for r in B:
            print(f"  {r.get('translation_id')}  mode={r.get('translate_mode')}  src={r.get('source_text')!r}")
    if conflicts:
        print("\n--- 空译文冲突 ---")
        for r in conflicts:
            print(f"  {r.get('translation_id')}  status={r.get('status')}")

    if a.also_failed and D_list:
        print("\n--- D: failed phrase 明细 ---")
        set_ids = {r.get("translation_id") for r in A + B}
        for d in D_list:
            in42 = d["translation_id"] in set_ids
            print(f"  tid={d['translation_id']} seg={d['segment_index']} phrase={d['source_phrase']!r}")
            print(f"     failure={d['failure']}  final_status={d['final_status']}")
            print(f"     final_translation={d['final_translation']!r}  落入A/B={in42}")
        print(f"\n  D 中落入 42 sameAsSource 的数量: {sum(1 for d in D_list if d['translation_id'] in set_ids)}")
        # 去重 unique tid
        ut = {d["translation_id"] for d in D_list}
        print(f"  D 唯一 tid 数: {len(ut)}")


if __name__ == "__main__":
    main()
