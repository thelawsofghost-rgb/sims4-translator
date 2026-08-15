#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diag_title_qa.py — batch done CSV deterministic QA inventory (只读, 零模型)
===============================================================================
【仅证据采集器】本轮不改 decision, 不改 completion gate。

统计口径 (用户裁决 2026-08-15):
  ROW_STATE (A/B/C 正交, 互斥, 求和 == 总行数):
    A SAME_AS_SOURCE_SEMANTIC    : translation==source 且存在玩家可见语义
                                   => FAIL/PENDING 候选
    B SAME_AS_SOURCE_LEGITIMATE  : translation==source 且有强证据合法保持原文
                                   => KEEP 候选
    C TRANSLATION_CHANGED        : translation != source
  PHRASE_FAILURE (D, 独立维度, 每行 failed_phrase=yes/no):
    D failed phrase 引擎明确失败 (cache miss 且未由 protected/glossary/B-evidence 覆盖)

B 的合法保持原文证据 (强证据, 旧 translate_mode_for() 不可靠, 禁止仅凭旧 classifier):
  B1 纯作者名/handle (含 By/©/@ 前缀, 无翻译语义)
  B2 明确品牌/专名 (专名白名单 / 大小写专名模式)
  B3 纯编号/不可译标识 (旧技术标识: 纯编号/姿态编号, 无英文语义词)
  B4 protected span 覆盖整个 source

仅当 B1-B4 任一成立才归 B; 否则有语义 -> A。

用法:
  python diag_title_qa.py <out_dir> --done <done_csv> [--also-failed]
        [--a-out output/title_A_tids.csv] [--d-out output/title_D_tids.csv]
输出 A/B/C 计数+列表 与 D failed flag 明细。不写任何 decision 文件, 不 merge。

--a-out / --d-out: (2026-08-15 新增) 从真实 done + cache 确定性落盘证据文件:
  A 文件列: translation_id, source_text        (A unique tid 硬 invariant=42)
  D 文件列: translation_id, source_text, segment_index, source_phrase
            (D unique tid 硬 invariant=5; D∩A=4; D−A=1)
  build_title_retry.py 直接消费这两个文件, 不手工构造。
"""
import sys, os, csv, re, argparse
from collections import Counter
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import phase2b_translate as P
from phase2a_catalog import norm_text

# ---------------- B 强证据检测 ----------------
# B1 作者名/handle: "By X", "© X", "@handle", 纯专名 + creator 词
AUTHOR_RE = re.compile(
    r"^(by\s+|©\s*|credit\s*:\s*|created\s+by\s+|made\s+by\s+|@)[\w.\- ]+$", re.I)
HANDLE_ONLY_RE = re.compile(r"^@[\w.\-]+$")
# B3 纯编号/技术标识: 无英文语义词 (仅数字/符号/单字符+数字)
TECH_ID_RE = None  # 弃用: 含实词判定改由 EN_WORD_RE 统一处理
EN_WORD_RE = re.compile(r"[A-Za-z]{3,}")  # >=3 字母英文实词
PROPER_NAME_RE = re.compile(r"^(?:[A-Z][a-zA-Z' .-]*){1,3}$")  # Title-case 1-3 词
# 明确英文语义词列表 (姿态/情绪等玩家可见词) -> 禁止归 B
SEM_WORDS = set("""
pose poses posepack pose%27pack posepacks set sets pack packs emotion emotions adult
sweet like cinammon cinnamon call gone wrong love pride pride don't leave me by your
side tender time to think under the same sky sad happy romantic romance bedroom bed
bathside bro talk pretty smile smiles male female toddler child kid kids baby
midnight night romance romantic kiss kissing hug hugging cuddle sleeping sleep sit
standing walk run jump dance dancing flirt flirting angry angry mood moods
""".split())


def strong_b_evidence(text):
    """返回 (is_b_legit, evidence_key)。B 需强证据, 严禁仅凭旧 classifier。
    B1 作者/handle; B2 专名(作者名模式); B3 纯编号/无实词技术标识;
    B4 protected span 覆盖整个 source。
    含 >=3 字母英文实词者默认视为语义 -> 不自动归 B。"""
    t = text.strip()
    if not t:
        return False, None
    # B4: protected span 覆盖整个 source
    try:
        segs, _ = P.split_semantic_spans(t)
        if segs and all(s["kind"] == "prot" for s in segs):
            return True, "B4_PROTECTED_WHOLE_SOURCE"
    except Exception:
        pass
    # B1: 作者/handle
    if AUTHOR_RE.match(t) or HANDLE_ONLY_RE.match(t):
        return True, "B1_AUTHOR_HANDLE"
    # B3: 纯编号/无实词技术标识 —— 全串无 >=3 字母英文单词
    if not EN_WORD_RE.search(t):
        return True, "B3_NO_REAL_WORD_ID"
    # B2(B2a): 专名仅当其含明确作者/品牌指示词, 或与已识别作者白名单匹配时 award。
    # 保守: 单独 title-case 英文短语(可能为常见词组合) 不自动归 B -> A 候选人工复核。
    return False, None


def row_state(text, translation):
    """ROW_STATE: A / B / C。translation!=source -> C; ==source -> A or B。"""
    src = norm_text(text)
    tr = norm_text(translation)
    if tr != src:
        return "C", None
    # translation == source
    b, bev = strong_b_evidence(src)
    if b:
        return "B", bev
    return "A", None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--done", default=None)
    ap.add_argument("--also-failed", action="store_true")
    ap.add_argument("--a-out", default=None, help="A evidence CSV 输出 (translation_id,source_text)")
    ap.add_argument("--d-out", default=None, help="D evidence CSV 输出 (translation_id,source_text,segment_index,source_phrase)")
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
    n = len(rows)
    print(f"[done] {done_path}\n[rows] {n}")

    cache = None
    if a.also_failed:
        from phrase_cache import PhraseCache
        cache = PhraseCache(out, model=None)

    A, B, C, D_failed = [], [], [], []
    row_by_tid = {}
    try:
        for r in rows:
            st, bev = row_state(r.get("source_text"), r.get("translation"))
            r["_state"] = st
            r["_bev"] = bev
            row_by_tid[r.get("translation_id")] = r
            if st == "A":
                A.append(r)
            elif st == "B":
                B.append(r)
            else:
                C.append(r)

        # PHRASE_FAILURE (D): cache miss 且未由 protected/glossary/B-evidence 覆盖
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
                    if cache.get(fp) is None:
                        D_failed.append({
                            "translation_id": tid,
                            "source_text": r.get("source_text"),
                            "source_phrase": p["t"].strip(),
                            "segment_index": p.get("key"),
                            "row_state": r.get("_state"),
                        })
    finally:
        if cache:
            cache.close()

    # 汇总
    print("\n=== ROW_STATE (A/B/C 正交, 求和应==rows) ===")
    print(f"A SAME_AS_SOURCE_SEMANTIC (FAIL/PENDING 候选)  : {len(A)}")
    print(f"B SAME_AS_SOURCE_LEGITIMATE (KEEP 候选, 强证据) : {len(B)}")
    print(f"C TRANSLATION_CHANGED                           : {len(C)}")
    total = len(A) + len(B) + len(C)
    print(f"   校验 A+B+C == rows : {total} == {n}  {'PASS' if total == n else 'FAIL'}")

    print("\n=== PHRASE_FAILURE (D, 独立维度 failed_phrase) ===")
    d_by_tid = {}
    for d in D_failed:
        d_by_tid.setdefault(d["translation_id"], []).append(d)
    print(f"含 failed phrase 的 rows (unique tid) : {len(d_by_tid)} / {n}")
    print(f"failed phrase 总数 (含多段)           : {len(D_failed)}")

    if A:
        print("\n--- A 列表 (语义未译, 候选 FAIL/PENDING) ---")
        for r in A:
            fail = "D_fail" if r.get("translation_id") in d_by_tid else "         "
            print(f"  [{fail}] {r.get('translation_id')}  src={r.get('source_text')!r}")
    if B:
        print("\n--- B 列表 (合法保持原文, KEEP 候选) ---")
        for r in B:
            fail = "D_fail" if r.get("translation_id") in d_by_tid else "         "
            print(f"  [{fail}] {r.get('translation_id')}  {r.get('_bev')}  src={r.get('source_text')!r}")
    if C:
        # C 中含 failed phrase 的也要标出
        cfail = [t for t in d_by_tid if row_by_tid[t].get("_state") == "C"]
        print(f"\n--- C: TRANSLATION_CHANGED {len(C)} 条, 其中含 failed phrase: {len(cfail)} ---")
        for t in cfail:
            r = row_by_tid[t]
            print(f"  [D_fail] {t}  src={r.get('source_text')!r}")
    if d_by_tid:
        print("\n--- D 明细 (failed phrase -> 对应 row state) ---")
        for tid, ds in d_by_tid.items():
            st = row_by_tid[tid].get("_state")
            for d in ds:
                print(f"  {tid}  state={st}  seg={d['segment_index']}  phrase={d['source_phrase']!r}")

    # ---- 2026-08-15: 确定性落盘 A / D 证据文件 (供 build_title_retry 消费) ----
    if a.a_out or a.d_out:
        a_unique = {r.get("translation_id") for r in A}
        d_unique = {d["translation_id"] for d in D_failed}
        print(f"\n=== 证据文件硬 invariant 校验 ===")
        print(f"A unique tid = {len(a_unique)}   (期望 42)")
        print(f"D unique tid = {len(d_unique)}   (期望 5)")
        print(f"D∩A            = {len(d_unique & a_unique)}   (期望 4)")
        print(f"D−A            = {len(d_unique - a_unique)}   (期望 1)")
        ok_inv = (len(a_unique) == 42 and len(d_unique) == 5
                  and len(d_unique & a_unique) == 4 and len(d_unique - a_unique) == 1)
        print(f"证据硬 invariant: {'PASS' if ok_inv else 'FAIL'}")

        if a.a_out:
            rows_a = sorted(A, key=lambda r: r.get("translation_id") or "")
            with open(a.a_out, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f); w.writerow(["translation_id", "source_text"])
                for r in rows_a:
                    w.writerow([r.get("translation_id"), r.get("source_text")])
            print(f"[写出 A] {a.a_out}  ({len(rows_a)} 行, unique tid {len(a_unique)})")
        if a.d_out:
            rows_d = sorted(D_failed, key=lambda d: (d["translation_id"] or "", d["segment_index"] or ""))
            with open(a.d_out, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["translation_id", "source_text", "segment_index", "source_phrase"])
                for d in rows_d:
                    w.writerow([d["translation_id"], d["source_text"],
                                d["segment_index"], d["source_phrase"]])
            print(f"[写出 D] {a.d_out}  ({len(rows_d)} 行, unique tid {len(d_unique)})")
        if not ok_inv:
            print("\n[FAIL] 证据硬 invariant 未通过, 请勿将 A/D 文件喂给 retry。")
            return 1
    return 0


if __name__ == "__main__":
    main()
