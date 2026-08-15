# -*- coding: utf-8 -*-
"""C26 决策 reconciliation 诊断 (只读, 不调模型, 不写文件).

对批1 canary 的 26 条 source 逐条打印:
  translation_id / source_text
  old translation_catalog decision/reason        (若本地或可追溯)
  old Phase2B translate_mode_for() decision/reason
  old phase2a_samples classify()/classify_meta() decision/reason (frozen semantic 规则)
  override / final2 / done evidence              (本地是否存在 terminal KEEP)

依据: frozen semantic 规则 (NON_SEMANTIC_TAG / TECHNICAL_LABEL), 禁止
translation==source 这种简单规则。
"""
import sys, os
sys.path.insert(0, "scripts"); sys.path.insert(0, "src")

# 批1 canary 的 26 条 (用户提供, 与 translation_done_batch_pose.csv 的 source_text 一致)
SOURCES = [
    "2 F V2","C1-3 M","C1-3 F","A 1-3 M","B1-3 F","3 F V2","3 M V2","1 F V2",
    "11 A2","12 A1","8 *animation","intro-obj","C2 M","B1-3 M","2 M V2",
    "A 1-3 F","4/ F V2","intro-npc","4/ M V2","C1 F","12 A2","placeholderIntro",
    "C2 F","4 F V2","4 M V2","C1 M",
]

def tid_of(s):
    from phase2a_catalog import source_hash, norm_text, make_translation_id
    return make_translation_id(source_hash(norm_text(s)), 1)

def load_override_set():
    """本地 frozen override 图层 terminal KEEP tid 集 (只读)."""
    import csv
    tids = set()
    base = "output"
    for fn in ("translation_overrides.csv", "translation_overrides.final2.csv"):
        p = os.path.join(base, fn)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                act = (r.get("action") or "").strip().upper()
                if (r.get("translation_id") or "").strip() and act == "KEEP":
                    tids.add(r["translation_id"].strip())
    return tids

def main():
    import phase2b_translate as ph2b
    import phase2a_samples as cls

    ov_keep = load_override_set()

    rows = []
    for s in SOURCES:
        tid = tid_of(s)
        # phase2b translate_mode_for
        mode, sem, alltok = ph2b.translate_mode_for(s)
        ph2b_reason = "NON_SEMANTIC_TAG" if mode == "KEEP" and ph2b._is_technical_identifier(s) else (mode if mode != "KEEP" else "NO_SEMANTIC_TOKEN")
        # phase2a classify + classify_meta
        c_cls = cls.classify(s)
        c_meta = cls.classify_meta(s)  # (decision_str, reason) e.g. ("TRANSLATE","SEMANTIC_WITH_NUM")
        c_dec, c_reason = c_meta[0], c_meta[1]
        rows.append({
            "tid": tid, "src": s,
            "ph2b": (mode, ph2b_reason),
            "cls": (c_cls, c_meta),
            "ov": tid in ov_keep,
        })

    # ---- 打印逐条证据 ----
    print("=" * 100)
    print("C26 决策 reconciliation 证据 (只读诊断)")
    print("=" * 100)
    hdr = f"{'tid':<22}{'source':<22}{'ph2b_translate_mode_for':<28}{'phase2a classify→meta':<30}{'overrideKEEP'}"
    print(hdr); print("-" * 100)
    for r in rows:
        m, mr = r["ph2b"]
        cc = r["cls"][0]
        c_dec, c_reason = r["cls"][1]
        print(f"{r['tid']:<22}{r['src']!r:<22}{m+'/'+mr:<28}{cc+'→'+c_dec+'('+c_reason+')':<34}{r['ov']}")
    print("-" * 100)

    # ---- 分组合计 ----
    from collections import Counter
    print("\n=== 按 phase2a.classify() reason 分组合计 (frozen 规则) ===")
    ccount = Counter(r["cls"][0] for r in rows)
    for k, v in sorted(ccount.items()):
        print(f"  {k:<24} {v}")
    print("\n=== 按 phase2b translate_mode_for() 分组合计 ===")
    mcount = Counter(r["ph2b"][0] for r in rows)
    for k, v in sorted(mcount.items()):
        print(f"  {k:<24} {v}")
    print("\n=== 按 classify_meta decision 分组合计 ===")
    dcount = Counter(r["cls"][1][0] for r in rows)
    for k, v in sorted(dcount.items()):
        print(f"  {k:<24} {v}")

    # ---- 每种判为 KEEP 的理由 (显示占位/编号/技术模式命中) ----
    print("\n=== 判为 KEEP 的 frozen 规则命中明细 ===")
    for r in rows:
        if r["cls"][1][0] == "KEEP":  # classify_meta decision==KEEP
            print(f"  {r['src']!r:<22} classify_meta=KEEP reason={r['cls'][1][1]}")
    for r in rows:
        if r["ph2b"][1].startswith("NON_SEMANTIC"):
            print(f"  {r['src']!r:<22} ph2b 技术标识命中 (_is_technical_identifier)")

    # ---- 最终数字 (基于 frozen 规则 classify_meta decision, 非简单规则) ----
    print("\n=== 最终 KEEP/TRANSLATE 数量 (基于 frozen classify_meta decision) ===")
    n_keep = sum(1 for r in rows if r["cls"][1][0] == "KEEP")
    n_tr = sum(1 for r in rows if r["cls"][1][0] == "TRANSLATE")
    n_rev = sum(1 for r in rows if r["cls"][1][0] == "REVIEW")
    print(f"  KEEP              = {n_keep}")
    print(f"  TRANSLATE         = {n_tr}")
    print(f"  REVIEW            = {n_rev}")
    print(f"  合计              = {len(rows)}")

    # 保存诊断到本地供复核 (只读诊断产物, 不进入 merge)
    os.makedirs("output", exist_ok=True)
    import csv
    with open("output/c26_reconcile_diag.tsv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["translation_id","source_text","ph2b_mode","ph2b_reason","classify","classify_meta_decision","classify_meta_reason","override_keep_evidence"])
        for r in rows:
            w.writerow([r["tid"], r["src"], r["ph2b"][0], r["ph2b"][1], r["cls"][0], r["cls"][1][0], r["cls"][1][1], r["ov"]])
    print(f"\n[tsv 诊断产物] output/c26_reconcile_diag.tsv")

if __name__ == "__main__":
    main()
