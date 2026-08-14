#!/usr/bin/env python3
"""只读: 把 QA REVIEW 行自动再分类为 A/B/C/D/E, 供人工聚焦 true-review。

只读脚本: 不调 LLM, 不改任何 csv, 不改 cache, 不写 .package。
输入 (out_dir 下):
  translation_qa_report.csv    (QA 输出, 含 qa/qa_reason)
  translation_done.csv         (含 decision/translate_mode/status/translation/protected_spans)
  translation_contexts.csv     (可选, 含每行的 neighbor_display_texts)
输出 (仅新增只读文件, 不改动任何输入):
  review_classifier_report.csv  全部 REVIEW 行 + 自动分类标签 + 判定依据
  review_A_PROTECTED_token_only.csv
  review_B_intentional_keep.csv
  review_C_true_english_leak.csv
  review_D_valid_short.csv
  review_E_done_skip.csv         (细分 ok_keep / misjudged)
  review_other.csv

用法:
  python scripts/review_classifier.py <out_dir> [--print-all] [--limit N]
"""
import sys, os, csv, re
from pathlib import Path
from collections import Counter, defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))
import phase2b_qa as Q   # 复用 residual_english / _GLOSS_EN / _ALLOW_EN / missing_protected 判定

_EN_WORD = re.compile(r"[A-Za-z]{2,}")

# 明确终态的 decision (仅这些才能把 KEEP/DONE_SKIP 终态化, 不搞一刀切)
_TERMINAL_DECISIONS = {
    "KEEP", "TECHNICAL", "TECH", "PROPER_NAME", "PROPERNAME",
    "NON_SEMANTIC", "NON-SEMANTIC", "NONSEMANTIC", "ABBREVIATION",
}
# 短译文但完整正确的常见对 (Left->左 等)。列为"允许的极短语义词", 供 D 判定参考
_VALID_SHORT = {
    "左", "右", "中", "上", "下", "前", "后", "内", "外",
    "男", "女", "是", "否", "有", "无", "多", "少", "开", "关",
}


def load_done(out_dir):
    p = Path(out_dir) / "translation_done.csv"
    rows = {}
    for r in csv.DictReader(open(p, encoding="utf-8-sig")):
        rows[r["translation_id"]] = r
    return rows


def load_report(out_dir):
    p = Path(out_dir) / "translation_qa_report.csv"
    return list(csv.DictReader(open(p, encoding="utf-8-sig")))


def load_ctx(out_dir):
    p = Path(out_dir) / "translation_contexts.csv"
    if not p.exists():
        return {}
    ctx = defaultdict(list)
    for r in csv.DictReader(open(p, encoding="utf-8-sig")):
        ctx[r["translation_id"]].append(r.get("neighbor_display_texts") or "")
    return {k: " | ".join(dict.fromkeys([x for x in v if x])) for k, v in ctx.items()}


def protected_tokens(psp):
    toks = set()
    if not psp:
        return toks
    for s in psp.split(";"):
        s = s.strip()
        if not s:
            continue
        tok = s.split("@")[0].strip()
        if re.search(r"[A-Za-z0-9]", tok):
            toks.add(tok)
    return toks


def residual_tokens(zh):
    """译文中残留的英文词(含 protected 与 white-list 的都算原始残留)。"""
    low = zh.lower()
    for mw in Q._MULTI_ALLOW:
        low = low.replace(mw, " ")
    return set(_EN_WORD.findall(low))


def classify_review(r, done, ctx):
    """返回 (class, sub, evidence)。class ∈ A/B/C/D/E/OTHER。"""
    tid = r["translation_id"]
    d = done.get(tid, {})
    src = (r.get("source_text") or d.get("source_text") or "").strip()
    zh = (r.get("translation") or d.get("translation") or "").strip()
    status = r.get("status") or d.get("status") or ""
    mode = r.get("translate_mode") or d.get("translate_mode") or ""
    decision = (d.get("decision") or "").strip().upper()
    psp = r.get("protected_spans") or d.get("protected_spans") or ""
    reason = r.get("qa_reason") or ""
    nbr = ctx.get(tid, "")

    prot = protected_tokens(psp)
    resid = residual_tokens(zh)
    # 真正"该翻没翻"的泄露词: 不在 protected, 不在白名单, 不在 glossary
    leak = {w for w in resid
            if w.lower() not in prot
            and w.lower() not in Q._ALLOW_EN
            and w.lower() not in Q._GLOSS_EN}

    # --- E. DONE_SKIP (状态类) ---
    if status == "DONE_SKIP" or (status == "KEEP" and decision in _TERMINAL_DECISIONS):
        if decision in _TERMINAL_DECISIONS or mode in ("KEEP", "OVERRIDE_K"):
            return "E", "ok_keep", f"terminal_decision={decision} mode={mode}"
        return "E", "misjudged", f"决策={decision} 但 DONE_SKIP, 需人工"

    # --- D. 短但完整 (译文过短 reason, 且无真正泄露) ---
    short_reason = "译文过短" in reason
    if short_reason and not leak:
        # 短但中文语义完整 (单字方位/状态词)
        if zh.strip() in _VALID_SHORT:
            return "D", "valid", f"极短完整译文 {zh!r}"
        return "D", "suspect", f"译文 <2 字且非白名单 {zh!r}, 待核"

    # --- A. 残留英文只来自 protected token ---
    if resid and not leak:
        # 全部残留都在 protected/白名单/glossary 内 -> 受保护保留
        return "A", "protected_only", f"残留词全为 protected/白名单: {sorted(resid)}"

    # --- C. 真正英文泄露 ---
    if leak:
        return "C", "leak", f"残留自然英文: {sorted(leak)}"

    # --- B. 有意保留 (decision 明确) ---
    if decision in _TERMINAL_DECISIONS or mode in ("KEEP", "OVERRIDE_K"):
        return "B", "intentional_keep", f"decision={decision} mode={mode}"

    # --- 其它 reason (超长/无中文/general REVIEW) ---
    return "OTHER", "misc", reason or "-"


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/review_classifier.py <out_dir> [--print-all] [--limit N]")
        sys.exit(1)
    out_dir = Path(sys.argv[1])
    pr_args = sys.argv[2:]
    print_all = "--print-all" in pr_args
    limit = None
    if "--limit" in pr_args:
        i = pr_args.index("--limit")
        limit = int(pr_args[i + 1])

    done = load_done(out_dir)
    report = load_report(out_dir)
    ctx = load_ctx(out_dir)

    reviews = [r for r in report if r.get("qa") == "REVIEW"]
    print(f"REVIEW 共 {len(reviews)} 行 (来自 {out_dir})")

    tagged = []
    cls_cnt = Counter()
    sub_cnt = Counter()
    for r in reviews:
        cls, sub, ev = classify_review(r, done, ctx)
        cls_cnt[cls] += 1
        sub_cnt[f"{cls}:{sub}"] += 1
        r2 = dict(r)
        r2["_auto_class"] = cls
        r2["_sub"] = sub
        r2["_evidence"] = ev
        r2["_decision"] = done.get(r["translation_id"], {}).get("decision", "")
        r2["_neighbor"] = ctx.get(r["translation_id"], "")
        tagged.append(r2)

    # 汇总
    print("\n=== REVIEW 自动分类汇总 ===")
    for cls in ("A", "B", "C", "D", "E", "OTHER"):
        if cls_cnt[cls]:
            print(f"  {cls_cnt[cls]:4d}  {cls}")
    print("  ---- 子类 ----")
    for k, c in sub_cnt.most_common():
        print(f"  {c:4d}  {k}")

    # auto-pass vs true-review
    auto_pass = cls_cnt["A"] + cls_cnt["B"] + cls_cnt["D"] + sub_cnt.get("E:ok_keep", 0)
    true_review = len(reviews) - auto_pass
    print(f"\n[结论] auto-pass ≈ {auto_pass}  (A protected_only + B intentional_keep + D valid_short + E ok_keep)")
    print(f"[结论] true-review ≈ {true_review}  (C english_leak + E misjudged + OTHER + 待核), 供人工聚焦")

    # 输出所有 REVIEW 到 report
    out_report = out_dir / "review_classifier_report.csv"
    cols = list(tagged[0].keys())
    with open(out_report, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in tagged:
            w.writerow(r)
    print(f"\n[写出] {out_report}  ({len(tagged)} 行, 含 _auto_class/_sub/_evidence/_decision/_neighbor)")

    # 按类导出
    groups = defaultdict(list)
    for r in tagged:
        groups[r["_auto_class"]].append(r)
    name = {"A": "A_PROTECTED_token_only", "B": "B_intentional_keep",
            "C": "C_true_english_leak", "D": "D_valid_short",
            "E": "E_done_skip", "OTHER": "other"}
    for cls, rows in groups.items():
        fname = out_dir / f"review_{name[cls]}.csv"
        with open(fname, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["translation_id", "source_text", "translation",
                                              "status", "decision", "translate_mode",
                                              "protected_spans", "qa_reason", "_auto_class",
                                              "_sub", "_evidence", "_neighbor"])
            w.writeheader()
            for r in rows:
                w.writerow({"translation_id": r.get("translation_id", ""),
                            "source_text": r.get("source_text", ""),
                            "translation": r.get("translation", ""),
                            "status": r.get("status", ""),
                            "decision": r.get("_decision", ""),
                            "translate_mode": r.get("translate_mode", ""),
                            "protected_spans": r.get("protected_spans", ""),
                            "qa_reason": r.get("qa_reason", ""),
                            "_auto_class": r.get("_auto_class", ""),
                            "_sub": r.get("_sub", ""),
                            "_evidence": r.get("_evidence", ""),
                            "_neighbor": r.get("_neighbor", "")})
        print(f"[写出] {fname}  ({len(rows)} 行)")

    # 可选打印明细
    if print_all:
        print("\n=== 明细 ===")
        for r in tagged:
            print(f"[{r['_auto_class']}:{r['_sub']}] {r['translation_id']} {r['source_text'][:30]!r} "
                  f"-> {r['translation'][:40]!r} | {r['_evidence']}")
            if r["_neighbor"]:
                print(f"    nbr: {r['_neighbor'][:100]}")

    print(f"\n完成 (只读; 未改任何输入/未调 LLM/未写 package)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
