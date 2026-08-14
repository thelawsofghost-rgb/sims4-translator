#!/usr/bin/env python3
"""只读: 按用户明确规则, 对 dorothy_needs_review.csv 中"已明确"条目预定案 (标 RESOLVED + 最终译文)。

只处理规则明确命中项; 其余保持 NEEDS_REVIEW, 不擅自写 override / 不改 QA / 不改 glossary / 不调 LLM。
统一不做全局替换 —— 逐条 (translation_id) 精准判定。

用法: python scripts/apply_clear_candidates.py <out_dir>
输入: dorothy_needs_review.csv  (export_dorothy_needs.py 产物)
输出: dorothy_resolved.csv (RESOLVED 部分) + dorothy_still_needs_review.csv (剩余 NEEDS_REVIEW)
"""
import sys, re, csv
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/apply_clear_candidates.py <out_dir>")
        sys.exit(1)
    out = Path(sys.argv[1])
    srcf = out / "dorothy_needs_review.csv"
    if not srcf.exists():
        print(f"[!] 找不到 {srcf}; 先跑 export_dorothy_needs.py")
        sys.exit(1)
    rows = list(csv.DictReader(open(srcf, encoding="utf-8-sig")))
    print(f"输入 {len(rows)} 行 (dorothy_needs_review.csv)")

    resolved, keep = [], []

    def mark(r, zh, note):
        r["_final"] = "RESOLVED"
        r["_final_translation"] = zh
        r["_resolve_note"] = note
        resolved.append(r)

    for r in rows:
        tid = r.get("translation_id") or ""
        src = (r.get("source_text") or "").strip()
        cur = (r.get("current_translation") or "").strip()
        resid = (r.get("residual_english_tokens") or "").lower()
        r.setdefault("_final", "NEEDS_REVIEW")
        r.setdefault("_final_translation", "")
        r.setdefault("_resolve_note", "")

        # 1. 真 [Animated] 后缀 -> [动画]; 前方自然英文保留已译部分
        m = re.search(r"\[Animated\]", src, re.I)
        if m and resid.strip() and "animated" in resid:
            # 前面的自然语言也已有译文 -> 仅换后缀
            zh = re.sub(r"\[[Aa]nimated\]", "[动画]", cur)
            mark(r, zh, "规则1: [Animated]->[动画]")
            continue

        # 2. Carry Upstairs X (1M/1F/2M/2F)
        m = re.match(r"^Carry Upstairs\s*([124]?[MF])$", src, re.I)
        if m:
            suf = m.group(1)
            mark(r, f"抱上楼 {suf}", f"规则2: 抱上楼 + 保护 {suf}")
            continue

        # 3. All-In-One: Look Down -> 整合版：低头
        if re.match(r"^All[- ]?In[- ]?One\s*[:：]\s*Look Down$", src, re.I):
            mark(r, "整合版：低头", "规则3: 整合版：低头 (pose 动作名)")
            continue

        # 4. [S][FLOOR] PEEPING THROUGH KEYHOLE -> [S][FLOOR] 从钥匙孔偷看
        if re.match(r"^\[S\]\s*\[FLOOR\]\s*PEEPING THROUGH KEYHOLE", src, re.I):
            mark(r, "[S][FLOOR] 从钥匙孔偷看", "规则4: 保护 [S][FLOOR]")
            continue

        # 5. [L2S] legs behind spreading -> [L2S] 双腿向后张开
        if re.match(r"^\[L2S\]\s*legs behind spreading", src, re.I):
            mark(r, "[L2S] 双腿向后张开", "规则5: 保护 [L2S]")
            continue

        # 6. F/M - AIO - 2 outfits -> F/M - 整合版 - 2套服装
        m = re.match(r"^([FM])\s*-\s*AIO\s*-\s*2\s*outfits$", src, re.I)
        if m:
            mark(r, f"{m.group(1).upper()} - 整合版 - 2套服装",
                 f"规则6: 保护 {m.group(1).upper()}, AIO->整合版")
            continue

        # 7. [N] Left - Surprise / [N] Left - Conversational -> [N]左 - 惊讶 / [N]左 - 交谈
        #    支持可选数字前缀, Left/Right/Middle 位置不限首
        m = re.match(r"^(?:(\d+)\s*)?(Left|Right|Middle)\s*-\s*(.*)$", src, re.I)
        if m:
            pre = (m.group(1) or "")
            side = {"left": "左", "right": "右", "middle": "中"}[m.group(2).lower()]
            tail = m.group(3).strip()
            tailzh = {
                "surprise": "惊讶", "conversational": "交谈",
            }.get(tail.lower())
            if tailzh:
                mark(r, f"{pre}{side} - {tailzh}", f"规则7: {pre}{side} - {tailzh}")
                continue

        # 8. optional: cigarette acc by MOC -> 可选：香烟配件 by MOC
        if re.match(r"^optional\s*:\s*cigarette acc by MOC$", src, re.I):
            mark(r, "可选：香烟配件 by MOC", "规则8: 保护 MOC 作者名")
            continue

        keep.append(r)

    # 写 RESOLVED
    cols = list(rows[0].keys())
    with open(out / "dorothy_resolved.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in resolved:
            w.writerow(r)
    # 写剩余 NEEDS_REVIEW
    with open(out / "dorothy_still_needs_review.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in keep:
            w.writerow(r)

    print(f"\nRESOLVED (已明确预定案) : {len(resolved)}")
    for r in resolved:
        print(f"  {r['translation_id']} {r['source_text'][:30]!r} -> {r['_final_translation']!r}")
    print(f"\n仍 NEEDS_REVIEW : {len(keep)}")
    for r in keep:
        print(f"  {r['translation_id']} {r['source_text'][:38]!r} | cur: {r['current_translation'][:30]!r}")
    print(f"\n[写出] dorothy_resolved.csv ({len(resolved)}) / dorothy_still_needs_review.csv ({len(keep)})")
    print("\n完成 (只读; 仅更新候选分类, 未应用任何 override / 未调 LLM / 未改 cache / 未写 package)。")
    return 0

if __name__ == "__main__":
    sys.exit(main())
