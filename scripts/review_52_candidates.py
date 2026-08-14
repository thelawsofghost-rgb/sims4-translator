#!/usr/bin/env python3
"""只读: 把 TRUE_LEAK 候选与 KEEP_TYPE 复核, 导出 SAFE_CANDIDATE / NEEDS_REVIEW 候选清单。

规则 (用户明确要求):
  1. 不做全局 token replacement, 不做 glossary 扩表 —— 每条最终是 translation_id + source_text 精确 override。
  2. 同一 source_text 不同 context 语义不同 -> 分别独立产译文, 不共用建议。
  3. auto-pass (KEEP_TYPE) 必须"所有"残留英文都落在 protected span / ID / proper name / technical token 内;
     若一行同时有 protected ID + 真自然英文 -> 必须留在 TRUE_LEAK (不能因检测到动作码就整行 PASS)。

输入 (out_dir):
  review_C_refined.csv       (C 类细分, 含 _refined / _leak_tokens)
  translation_done.csv       (source_text / protected_spans / translation)
  translation_contexts.csv   (neighbor_display_texts / package_path)
输出 (仅新增只读文件):
  review_52_candidates.csv   (全部候选: SAFE_CANDIDATE + NEEDS_REVIEW, 字段齐全)
用法: python scripts/review_52_candidates.py <out_dir> [--print-all] [--minev]
"""
import sys, csv, re
from pathlib import Path
from collections import defaultdict

# ---- 常用词 -> 该行的建议译文 (仅在"该 token 出现于该行当前译文中"时应用, 非全局) ----
_TOKEN_ZH = {
    "sofa": "沙发", "bed": "床", "chair": "椅子", "table": "桌子", "desk": "书桌",
    "angry": "愤怒", "happy": "开心", "sad": "悲伤", "worried": "担忧", "crying": "哭泣",
    "amused": "忍俊不禁", "smiling": "微笑", "laughing": "大笑",
    "female": "女性", "male": "男性", "man": "男人", "woman": "女人", "boy": "男孩", "girl": "女孩",
    "carry": "抱起", "upstairs": "上楼", "downstairs": "下楼",
    "arms": "手臂", "arm": "手臂", "folded": "交叉", "crossed": "交叉",
    "behind": "后面", "head": "头", "hand": "手", "hands": "双手", "leg": "腿", "legs": "双腿",
    "eye": "眼睛", "eyes": "双眼", "rolling": "翻滚", "roll": "转动",
    "front": "前面", "down": "下", "up": "上", "middle": "中", "left": "左", "right": "右",
    "emotions": "情绪", "emotion": "情绪", "negative": "负面", "positive": "正面", "neutral": "中性",
    "tool": "工具", "standing": "站立", "sitting": "坐姿", "holding": "握住",
    "pointing": "指向", "open": "张开", "closed": "闭合", "mouth": "嘴", "tongue": "舌头",
    "fuckiforgot": "卧槽我忘了",
}

# ---- 需要人工的 token (作者命名 / 动作码 / 缩写 / 故意拼写 / 直译破坏自然度) ----
_NEEDS_REVIEW_TOKEN = {
    "bonusa", "bonusb", "fuckiforgot", "ahegao", "tinisims", "pinup", "skye",
    "fboss", "boss", "ea", "hb", "ga", "db", "aa", "xy",
}

# ---- 短语级规则 (仅在"该短语出现在该行当前译文"时应用, 非全局) ----
_PHRASE_ZH = [
    (re.compile(r"\bALL[- ]?IN[- ]?ONE\b", re.IGNORECASE), "整合版"),
    (re.compile(r"\bCarry Upstairs\b", re.IGNORECASE), "抱上楼"),
    (re.compile(r"\bNegative Emotions\b", re.IGNORECASE), "负面情绪"),
]
# 品牌/动作码前缀 (4字母内短码 + 某特征): 用于 KEEP_TYPE 复核
_ACTION_CODE_RE = re.compile(r"^[0-9]{0,3}[A-Za-z]{1,3}$")  # 12Bf / 61La / 40Gd / 5Ae / v2
_BRAND_WORDS = {"pinup", "skye", "tinisims", "ahegao", "fboss", "boss", "kcat",
                "snb", "ava", "grr", "amai", "bel", "ver", "azn", "m4", "mf"}

_RESID_WORD = re.compile(r"[A-Za-z]{2,}")


def resid_words(zh):
    if not zh:
        return set()
    low = zh.lower()
    for mw in ("*anim", "all in one", "pose pack", "english", "livestream"):
        low = low.replace(mw, " ")
    return set(_RESID_WORD.findall(low))


def protected_tokens(psp):
    toks = set()
    if not psp:
        return toks
    for s in str(psp).split(";"):
        s = s.strip()
        if not s:
            continue
        tok = s.split("@")[0].strip()
        if re.search(r"[A-Za-z0-9]", tok):
            toks.add(tok.lower())
    return toks


def is_protected_or_brand(tok):
    low = tok.lower()
    if low in _BRAND_WORDS:
        return True
    if _ACTION_CODE_RE.match(low):
        return True  # 短动作码/版本号 (12bf / v2 / ea / hb)
    if low in {"anim", "sims", "sims4", "sim4", "mw", "sk", "bl", "studio",
               "v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10", "dlc",
               "pack", "pose", "poses", "posepack", "pif", "unknown", "none", "n/a",
               "id", "new", "num", "no", "d"}:
        return True
    return False


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/review_52_candidates.py <out_dir> [--print-all]")
        sys.exit(1)
    out = Path(sys.argv[1])
    print_all = "--print-all" in sys.argv

    refined = out / "review_C_refined.csv"
    if not refined.exists():
        print(f"[!] 找不到 {refined}; 请先跑 review_c_refine.py")
        sys.exit(1)

    # load done + contexts
    done = {}
    for r in csv.DictReader(open(out / "translation_done.csv", encoding="utf-8-sig")):
        done[r["translation_id"]] = r
    ctx = defaultdict(list)
    cp = out / "translation_contexts.csv"
    if cp.exists():
        for r in csv.DictReader(open(cp, encoding="utf-8-sig")):
            nbr = (r.get("neighbor_display_texts") or "").strip()
            pkg = (r.get("package_path") or "").strip()
            p = []
            if pkg:
                p.append(f"package={pkg}")
            if nbr:
                p.append(f"neighbors={nbr[:400]}")
            if p:
                ctx[r["translation_id"]].append("; ".join(p))

    rows = list(csv.DictReader(open(refined, encoding="utf-8-sig")))

    safe, need = [], []
    reaudit_keep = []
    for r in rows:
        tid = r["translation_id"]
        d = done.get(tid, {})
        src = r.get("source_text") or d.get("source_text") or ""
        zh = r.get("translation") or d.get("translation") or ""
        psp = r.get("protected_spans") or d.get("protected_spans") or ""
        nbr = " | ".join(ctx.get(tid, []))
        refined2 = (r.get("_refined") or "").strip()
        leak_toks = [t.strip().lower().rstrip("s") for t in (r.get("_leak_tokens") or "").split(",") if t.strip()]
        prot = protected_tokens(psp)
        resid = resid_words(zh)
        # 自然英文泄漏 (排除 protected/品牌/动作码)
        natural_leak = {w for w in resid
                        if not is_protected_or_brand(w) and w.lower() not in prot}

        if refined2 == "TRUE_LEAK":
            # ---- TRUE_LEAK: 生成候选译文 (按行, 仅替换该行泄漏 token / 短语) ----
            new_zh = zh
            changed = []
            # 短语级优先 (ALL-IN-ONE, carry upstairs 等整词组合)
            for pat, base in _PHRASE_ZH:
                if pat.search(new_zh):
                    new_zh = pat.sub(base, new_zh)
                    changed.append(pat.pattern + "->" + base)
            # 单词级 (仅当该 token 确实在该行译文中)
            for tok in sorted(natural_leak | set(leak_toks), key=len, reverse=True):
                base = _TOKEN_ZH.get(tok)
                if not base:
                    continue
                pat = re.compile(rf"\b{tok}\b", re.IGNORECASE)
                if pat.search(new_zh):
                    new_zh = pat.sub(base, new_zh)
                    changed.append(f"{tok}->{base}")
            # 判定 SAFFE vs NEEDS_REVIEW
            if set(leak_toks) & _NEEDS_REVIEW_TOKEN or not changed:
                cat = "NEEDS_REVIEW"
                reason = ("含需人工 token: " + ",".join(sorted(set(leak_toks) & _NEEDS_REVIEW_TOKEN))
                          if set(leak_toks) & _NEEDS_REVIEW_TOKEN
                          else "无可用直译规则(需看上下文)")
            else:
                cat = "SAFE_CANDIDATE"
                reason = "可精确 override; 替换: " + "; ".join(changed)
            rec = {
                "translation_id": tid, "source_text": src,
                "current_translation": zh, "proposed_translation": new_zh if changed else "",
                "residual_english_tokens": ",".join(sorted(leak_toks)),
                "protected_spans": psp, "category": cat, "reason": reason,
                "sample_context": src, "neighbor_display_texts": nbr,
            }
            (safe if cat == "SAFE_CANDIDATE" else need).append(rec)
        else:
            # ---- KEEP_TYPE: 复核 auto-pass 规则 ----
            if natural_leak:
                # 违反规则: 有 protected ID + 真自然英文 -> 应留在 TRUE_LEAK
                rec = {
                    "translation_id": tid, "source_text": src,
                    "current_translation": zh, "proposed_translation": "",
                    "residual_english_tokens": ",".join(sorted(natural_leak)),
                    "protected_spans": psp, "category": "NEEDS_REVIEW",
                    "reason": "复核: 存在 protected ID 之外的英文自然语言 -> 不应整行 PASS: "
                              + ",".join(sorted(natural_leak)),
                    "sample_context": src, "neighbor_display_texts": nbr,
                }
                need.append(rec)
                reaudit_keep.append(tid)

    print(f"TRUE_LEAK 候选导出: SAFE_CANDIDATE {len(safe)} / NEEDS_REVIEW {len(need)}")
    if reaudit_keep:
        print(f"[复核] 从 KEEP_TYPE 揪出 {len(reaudit_keep)} 条混入自然英文, 已归 NEEDS_REVIEW: "
              f"{reaudit_keep[:10]}{'...' if len(reaudit_keep)>10 else ''}")

    # 汇总 category
    print(f"\n=== 汇总 ===")
    print(f"  SAFE_CANDIDATE : {len(safe)}")
    print(f"  NEEDS_REVIEW   : {len(need)}")

    # 写候选清单
    cols = ["translation_id", "source_text", "current_translation", "proposed_translation",
            "residual_english_tokens", "protected_spans", "category", "reason",
            "sample_context", "neighbor_display_texts"]
    with open(out / "review_52_candidates.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for rec in safe + need:
            w.writerow({k: rec.get(k, "") for k in cols})
    print(f"\n[写出] {out / 'review_52_candidates.csv'}  ({len(safe)+len(need)} 行)")

    if print_all:
        print("\n=== SAFE_CANDIDATE ===")
        for rec in safe:
            print(f"  {rec['translation_id']} {rec['source_text'][:24]!r} -> "
                  f"{rec['current_translation'][:28]!r} ==> {rec['proposed_translation'][:34]!r} | {rec['reason']}")
        print("\n=== NEEDS_REVIEW ===")
        for rec in need:
            print(f"  {rec['translation_id']} {rec['source_text'][:24]!r} -> "
                  f"{rec['current_translation'][:28]!r} | {rec['reason'][:80]}")
            if rec["neighbor_display_texts"]:
                print(f"      nbr: {rec['neighbor_display_texts'][:120]}")

    print("\n完成 (只读; 未应用任何 override / 未调 LLM / 未写 cache / 未写 package)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
