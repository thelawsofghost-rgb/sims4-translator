#!/usr/bin/env python3
"""只读: 把 review_C_true_english_leak.csv 按"泄露英文 token"二次分组。

目的: 203 条 C 类里可能大量是同一批低频/技术/作者 token (sim/idle/tool/作者名)。
按 token 频率聚合, 让 Dorothy 先看高频 token 对应的若干代表行, 而非逐条。

只读: 不调 LLM, 不改输入, 不写 cache, 不写 package。
用法:
  python scripts/review_c_leak_group.py <out_dir> [--top N] [--print-all]
输出:
  review_C_leak_token_stats.csv   (token -> 频次 -> 代表 tid/src/zh)
"""
import sys, csv, re
from pathlib import Path
from collections import Counter, defaultdict

_EN_WORD = re.compile(r"[A-Za-z]{2,}")
_MULTI_ALLOW = ("*anim", "all in one", "pose pack", "english", "livestream")

def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/review_c_leak_group.py <out_dir> [--top N] [--print-all]")
        sys.exit(1)
    out = Path(sys.argv[1])
    args = sys.argv[2:]
    top = 20
    if "--top" in args:
        top = int(args[args.index("--top") + 1])
    print_all = "--print-all" in args

    src = out / "review_C_true_english_leak.csv"
    if not src.exists():
        print(f"[!] 找不到 {src}; 请先跑 review_classifier.py")
        sys.exit(1)
    rows = list(csv.DictReader(open(src, encoding="utf-8-sig")))

    # 提取每条的泄露 token (同样排除 protected/白名单, 但此处无 psp, 直接取 evidence 里的 leak 集合)
    # evidence 形如 "残留自然英文: ['smoking', 'sim']" -> 解析
    tok_rows = defaultdict(list)   # token -> [行]
    tk_cnt = Counter()
    for r in rows:
        ev = r.get("_evidence", "")
        m = re.search(r"\[(.*?)\]", ev)
        toks = [t.strip().strip("'\"") for t in m.group(1).split(",")] if m else []
        toks = [t for t in toks if t and t.lower() not in ("",)]
        seen = set()
        for t in toks:
            low = t.lower()
            if low in seen:
                continue
            seen.add(low)
            tk_cnt[low] += 1
            tok_rows[low].append(r)

    print(f"C 类共 {len(rows)} 行, 泄露 token 种类 {len(tk_cnt)}")
    print(f"\n=== 高频泄露 token (Top {top}) ===")
    print(f"{'token':<16}{'条数':>5}   代表 (tid / src -> zh)")
    for tok, c in tk_cnt.most_common(top):
        reps = tok_rows[tok][:3]
        r0 = reps[0]
        print(f"  {tok:<14}{c:>5}   {len(reps)}例: "
              f"{r0.get('translation_id')} {r0.get('source_text','')[:22]!r} -> {r0.get('translation','')[:30]!r}")

    # 全量 token 统计写文件
    out_stats = out / "review_C_leak_token_stats.csv"
    with open(out_stats, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["token", "count", "sample_translation_id", "sample_source", "sample_translation"])
        for tok, c in tk_cnt.most_common():
            for r in tok_rows[tok][:1]:
                w.writerow([tok, c, r.get("translation_id"), r.get("source_text"), r.get("translation")])
    print(f"\n[写出] {out_stats}")

    if print_all:
        print("\n=== 全部 C 类明细 ===")
        for r in rows:
            print(f"  {r.get('translation_id')} {r.get('source_text','')[:28]!r} "
                  f"-> {r.get('translation','')[:40]!r} | {r.get('_evidence')}")
    print("\n完成 (只读)。")
    return 0

if __name__ == "__main__":
    sys.exit(main())
