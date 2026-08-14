#!/usr/bin/env python3
"""只读: 精确判定 C 类是"品牌/动作码保留型"还是"自然英文真泄露"。

C 类(残留英文)里其实混着两类:
  1) KEEP_TYPE (品牌/作者/姿势包/动作码前缀, 原样保留是对的, 只是没进 protected_spans 列):
       pinup / skye / tinisims / ahegao / ea / hb / ga / db / aa / xy / fboss / boss ...
  2) TRUE_LEAK (自然英文, 翻译该翻没翻):
       sofa / carry / upstairs / arms / folded / emotions / bed / amused / angry ...
判别: token 是否属于"常用英语词表"_COMMON_EN。是 -> TRUE_LEAK(该翻);
      否则是品牌/代号 -> KEEP_TYPE。再辅以"是否在原文 source 里"佐证。

只读: 不调 LLM, 不改输入, 不写 cache, 不写 package。
用法: python scripts/review_c_refine.py <out_dir> [--print-all] [--mintok T]
输出: review_C_refined.csv (全部 C 类 + KEEP_TYPE/TRUE_LEAK + 依据)
"""
import sys, csv, re
from pathlib import Path
from collections import Counter

# 常用英语词表 —— 出现即视为"该翻的自然英文"(可扩充)
_COMMON_EN = {
    # 身体 / 动作
    "sofa", "bed", "table", "chair", "desk", "carry", "upstairs", "downstairs",
    "arms", "arm", "head", "hand", "hands", "leg", "legs", "foot", "feet", "face",
    "behind", "folded", "crossed", "pointing", "point", "rolling", "roll", "eyes",
    "open", "closed", "mouth", "tongue", "smile", "smiling", "laugh", "laughing",
    "angry", "sad", "worried", "emotions", "negative", "positive", "neutral",
    "amused", "happy", "crying", "cry", "shout", "sleep", "sleeping", "sit",
    "sitting", "stand", "standing", "walk", "walking", "lean", "leaning", "bend",
    "kiss", "hug", "hold", "holding", "touch", "touching", "wave", "raise", "lower",
    # 通用
    "left", "right", "middle", "up", "down", "front", "back", "top", "bottom",
    "in", "on", "under", "over", "between", "male", "female", "man", "woman", "boy",
    "girl", "tool", "mode", "version", "sleeping", "drunk", "idle", "pose", "pose",
    "sheet", "option", "optional", "adjust", "adjusting", "use", "using", "with",
    "and", "the", "of", "for", "big", "small", "close", "enough", "new", "old",
    "worry", "worried", "despair", "question", "questioning", "something", "nothing",
    "one", "two", "all", "in", "m", "f",
}
# 明确品牌/作者/姿势包/动作码前缀 —— 即使拼写像英文也强制保留
_BRAND = {
    "pinup", "skye", "tinisims", "ahegao", "fboss", "boss", "ea", "hb", "ga",
    "db", "aa", "xy", "kcat", "snb", "ava", "grr", "amai", "bel", "ver", "azn",
}

def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/review_c_refine.py <out_dir> [--print-all] [--mintok T]")
        sys.exit(1)
    out = Path(sys.argv[1])
    args = sys.argv[2:]
    print_all = "--print-all" in args
    mintok = None
    if "--mintok" in args:
        mintok = int(args[args.index("--mintok") + 1])

    src = out / "review_C_true_english_leak.csv"
    if not src.exists():
        print(f"[!] 找不到 {src}; 请先跑 review_classifier.py")
        sys.exit(1)
    rows = list(csv.DictReader(open(src, encoding="utf-8-sig")))

    keep, leak = [], []
    for r in rows:
        ev = r.get("_evidence", "")
        m = re.search(r"\[(.*?)\]", ev)
        toks = [t.strip().strip("'\"") for t in m.group(1).split(",")] if m else []
        src_text = (r.get("source_text") or "").lower()
        in_src = [t for t in toks if t.lower() in src_text]
        not_in_src = [t for t in toks if t.lower() not in src_text]
        r2 = dict(r)
        r2["_in_source"] = ",".join(in_src)
        r2["_not_in_source"] = ",".join(not_in_src)
        # 判定: 任一 token 是自然英语(且非品牌) -> 真泄露; 否则品牌/代号 -> 保留
        leaked = []
        for t in toks:
            low = t.lower().rstrip("s")  # 容忍复数
            if low in _BRAND or t.lower() in _BRAND:
                continue
            if low in _COMMON_EN or t.lower() in _COMMON_EN:
                leaked.append(t)
            elif t.lower() in src_text and len(t) <= 4:
                # 短 token 且在 source 里 -> 动作码/缩写 (ea/hb/xy...), 保留
                continue
            else:
                # 长 token 不在常用表, 且在原文里 -> 品牌保留; 不在原文 -> 存疑归真泄露
                if t.lower() in src_text:
                    continue
                else:
                    leaked.append(t)
        if leaked:
            r2["_refined"] = "TRUE_LEAK"
            r2["_leak_tokens"] = ",".join(leaked)
            leak.append(r2)
        else:
            r2["_refined"] = "KEEP_TYPE"
            r2["_leak_tokens"] = ""
            keep.append(r2)

    print(f"C 类共 {len(rows)} 行 -> 品牌/代号保留(KEEP_TYPE) {len(keep)} / 自然英文真泄露(TRUE_LEAK) {len(leak)}")

    if leak:
        print(f"\n=== 真泄露 TRUE_LEAK ({len(leak)}) 按 token ===")
        tk = Counter()
        for r in leak:
            for t in r["_leak_tokens"].split(","):
                if t:
                    tk[t.lower().rstrip("s")] += 1
        shown = 0
        for t, c in tk.most_common(40):
            lim = mintok if mintok else None
            if lim and shown >= lim:
                break
            shown += 1
            print(f"  {c:4d}  {t}")
    if keep:
        print(f"\n=== 保留型 KEEP_TYPE 抽样 (前 12) ===")
        for r in keep[:12]:
            print(f"  {r['translation_id']} {r['source_text'][:30]!r} -> {r['translation'][:30]!r} "
                  f"| src无:{r['_not_in_source'][:40]}")

    # 输出
    cols = ["translation_id", "source_text", "translation", "_refined", "_leak_tokens",
            "_in_source", "_not_in_source", "_evidence", "_neighbor"]
    with open(out / "review_C_refined.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in leak + keep:
            w.writerow({k: r.get(k, "") for k in cols})
    print(f"\n[写出] {out / 'review_C_refined.csv'}  ({len(rows)} 行)")

    if print_all:
        print("\n=== 真泄露明细 ===")
        for r in leak:
            print(f"  {r['translation_id']} {r['source_text'][:30]!r} -> {r['translation'][:36]!r} "
                  f"| 泄露:{r['_leak_tokens']}")
    print("\n完成 (只读)。")
    return 0

if __name__ == "__main__":
    sys.exit(main())
