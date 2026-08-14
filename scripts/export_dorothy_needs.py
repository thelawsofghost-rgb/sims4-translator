#!/usr/bin/env python3
"""只读: 把 NEEDS_REVIEW 候选整理成 Dorothy 可直接裁定的分组清单。

仅按明显类型做"展示分组" —— 不新增判定规则, 不改 QA / glossary, 不写 override。
每条附全字段 + 建议 action (仅展示建议, 最终由 Dorothy 定)。

用法: python scripts/export_dorothy_needs.py <out_dir> [--all]
输出: dorothy_needs_review.csv (分组排序, 全字段) + 控制台分组清单
"""
import sys, csv
from pathlib import Path
from collections import defaultdict

# 展示分组 (仅关键词匹配, 用于排版分组, 不作终判)
_GROUPS = [
    ("[Animated] 系",        ["[animated]", "[动画]", "hypnotized", "bed", "bar"]),
    ("BONUS 系",             ["bonus", "bonusa", "bonusb"]),
    ("作者/人名疑似项",        ["eleven", "skye", "natalia", "auditore", "pandora", "bien", "aimé", "herecirm", "rascgal", "akuiyumi", "gounafier", "beto", "samsims", "moc"]),
    ("动作码/缩写疑似项",       ["ga ", "db ", "aa ", "xy ", "hb ", "ea ", "aio", "[s]", "[f]", "l2s", "m-", "f-", "n/a", "sim"]),
    ("故意拼写/俚语",          ["fuck", "lve", "omg", "lol", "idc", "ngl", "tbh", "smh"]),
    ("姿势/自然英文(需逐条定)",  ["arms", "folded", "behind", "head", "legs", "spread", "standing",
                               "pose", "sitting", "looking", "through", "keyhole", "peeping",
                               "intimate", "dessert", "cigarette", "smoking", "clean",
                               "outfits", "conversational", "surprise", "annoyed", "worried",
                               "mouth", "open", "despair", "sad", "teeth", "thumb", "biting",
                               "mirror", "hang", "low", "fists", "sides", "stare", "eleven"]),
]
# 建议 action 展示规则 (仅展示; final 由 Dorothy 定)
def suggest_action(rec):
    src = (rec.get("source_text") or "").lower()
    zh = (rec.get("current_translation") or "")
    resid = (rec.get("residual_english_tokens") or "").lower()
    if any(k in resid for k in ("bonusa", "bonusb")):
        return "KEEP"          # BONUS 是技术占位符
    if any(k in resid for k in ("eleven", "skye", "natalia", "auditore", "pandora", "bien", "aimé")):
        return "KEEP"          # 作者/角色命名
    if "[animated]" in resid and "动画" not in zh:
        return "TRANSLATE"     # [Animated] 系列 -> [动画] (bar6 已定案同类)
    if not any(t.lower() in resid for t in ("sofa", "bed", "carry", "arms", "folded",
                                            "behind", "head", "intimate", "dessert",
                                            "cigarette", "smoking", "peeping", "keyhole",
                                            "teeth", "thumb", "biting", "mirror", "angry",
                                            "happy", "sad", "worried", "crying", "amused",
                                            "tool", "emotions", "chair", "down", "up",
                                            "front", "legs", "leg", "hand", "eyes", "eye",
                                            "female", "male", "holding", "standing",
                                            "rolling", "clean", "love")):
        return "REVIEW"
    return "TRANSLATE"

def find_group(rec):
    hl = (rec.get("residual_english_tokens") or " ").lower() + " " + (rec.get("source_text") or "").lower()
    for name, keys in _GROUPS:
        if any(k in hl for k in keys):
            return name
    return "其他/歧义"

def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/export_dorothy_needs.py <out_dir> [--all]")
        sys.exit(1)
    out = Path(sys.argv[1])
    cand = out / "review_52_candidates.csv"
    if not cand.exists():
        print(f"[!] 找不到 {cand}")
        sys.exit(1)
    rows = [r for r in csv.DictReader(open(cand, encoding="utf-8-sig"))
            if (r.get("category") or "").strip() == "NEEDS_REVIEW"]
    print(f"NEEDS_REVIEW 共 {len(rows)} 行 -> 按展示分组:")

    grouped = defaultdict(list)
    for r in rows:
        r["_grp"] = find_group(r)
        r["_suggest"] = suggest_action(r)
        grouped[r["_grp"]].append(r)

    # 写 CSV (分组排序, 全字段 + 建议)
    cols = ["translation_id", "source_text", "current_translation", "proposed_translation",
            "residual_english_tokens", "protected_spans", "sample_context",
            "neighbor_display_texts", "_grp", "_suggest", "reason"]
    order = [g[0] for g in _GROUPS if g[0] in grouped] + \
            [g for g in grouped if g not in [gg[0] for gg in _GROUPS]]
    with open(out / "dorothy_needs_review.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for g in order:
            for r in sorted(grouped[g], key=lambda x: x["translation_id"]):
                w.writerow({k: r.get(k, "") for k in cols})

    # 控制台分组清单
    for g in order:
        grp = grouped[g]
        print(f"\n===== {g} ({len(grp)}) =====")
        for r in sorted(grp, key=lambda x: x["translation_id"]):
            src = (r.get("source_text") or "")[:34]
            cur = (r.get("current_translation") or "")[:34]
            resid = (r.get("residual_english_tokens") or "")
            sug = r["_suggest"]
            pkg = ""
            nbr = r.get("neighbor_display_texts") or ""
            if "package=" in nbr:
                pkg = nbr.split("package=")[1].split(";")[0].split("\\")[-1]
            print(f"  [{sug}] {r['translation_id']} {src!r}")
            print(f"        cur: {cur!r} | resid: {resid} | pkg: {pkg[:30]}")
    print(f"\n[写出] {out / 'dorothy_needs_review.csv'}  ({len(rows)} 行, 分组+建议)")

    # 汇总
    from collections import Counter
    c0 = Counter(r["_grp"] for r in rows)
    c1 = Counter(r["_suggest"] for r in rows)
    print(f"\n分组分布: {dict(c0)}")
    print(f"建议 action 分布: {dict(c1)}")
    print("\n完成 (只读)。")
    return 0

if __name__ == "__main__":
    sys.exit(main())
