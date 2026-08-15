#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
desc_content_qa.py — DESCRIPTION 173 DONE 确定性内容 QA (只产生 REVIEW_CANDIDATE, 不自动改译文)
=================================================================================================
读真实 run done (output/translation_done_batch_desc.csv), 仅处理 status in
(DONE|APPROVED), 逐行跑 6 组确定性规则。规则只产出 candidate flag, 绝不自动
把品牌名/附件名/作者名判错, 绝不改写译文。

输入: --done output/translation_done_batch_desc.csv
       [--batch 期望 DONE 数, 缺省 173]
输出 (同 done 目录): output/translation_desc_qa_candidates.csv
  translation_id, source_text, translation, flags, detail
  flag 集合: RESIDUAL_EN | CJK_LATIN_GLUE | DIGIT_DROP | BRACKET_IMBALANCE
             | BRACKET_ID_BROKEN | LENGTH_DELTA
报告: DONE input=N  suspicious candidates=C  clean candidates=K

规则 (只对明确结构 / frozen evidence, 不建宽泛"大写词/camelCase 全 protected"规则):
  R1 residual English semantic fragments   : 译文中残留非白名单英文词
  R2 Latin/CJK 异常黏连 (如旧 'I我')        : 单 Latin letter 紧贴 CJK 且该前后无空白/保护界
  R3 source 数字完整保留                   : 原文数字序列在译文中必须全部出现 (等值保留)
  R4 bracket/parentheses 平衡              : () [] {} 各自配对 (不跨类型)
  R5 bracketed creator/URL/accessory id 被破坏: [] 内 creator/URL/accessory token 未被翻译/未被改写
  R6 极端长度变化                          : |len_tr - len_src| / max(len) 超过阈值 (超长/超短)

保护/白名单惯例 (对齐 phase2b_qa.py): 不判品牌/附件/作者/版本/编号为错。
纯 '+' 或纯符号 token 不被 R1/R3 判 (标点保护)。

语义 (只报 candidate, 不自动修):
  - R2 只报 单 Latin 字母 紧贴 CJK 且非受保护缩写 (如 'I我'); 不跑宽泛 camelCase。
  - R3 用数字序列集合 (含 '.' '-' 内数字) 比较, 缺失即 candidate。
  - R4 用正负栈计数, 不跨类型匹配 (近似均衡检查, 非真 parser)。
  - R5 只检查 source 中 '[...]' 组: 若内含 creator/URL/accessory 标识 (字母/数字/符号串),
      要求译文对应位置仍含该原文片段 (原文保持), 否则 candidate。不判定品牌语义。
  - R6 阈值 len 比 <0.35 或 >3.0 时 candidate (仅超极端, 降低误报)。
"""
import sys, csv, re, argparse
from pathlib import Path
from collections import Counter

# ---- 白名单 (对齐 phase2b_qa.py, 不判品牌/附件/作者为错) ----
_ALLOW_EN = {
    "anim", "sims", "sims4", "sim4", "mw", "sk", "bl", "studio",
    "v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10",
    "d", "dlc", "pack", "posed", "pose", "poses", "posepack", "pif",
    "kcat", "snb", "ava", "grr", "amai", "bel", "ver", "azn", "m4", "mf",
    "unknown", "none", "n/a", "na", "id", "new", "num", "no",
    # description 额外: acc(accessory), iphone 品牌, ipod, cc, mesh, maxis, ea
    "acc", "iphone", "ipod", "cc", "mesh", "maxis", "ea", "vfx", "cas",
}
_GLOSS_EN = {"right", "left", "middle", "positive", "negative", "neutral",
             "concern", "doubtful", "smirk", "sim", "idle"}
_MULTI_ALLOW = ("*anim", "all in one", "pose pack", "english", "livestream", "pose packs")
_EN_WORD = re.compile(r"[A-Za-z]{2,}")
_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_LATIN = re.compile(r"[A-Za-z]")


def residual_english(zh, src):
    """挑出译文中残留的英文词 (排除白名单/编号/技术标签/protected 标识)。

    只针对明确结构/frozen evidence (对齐用户裁决):
      - 白名单 / glossary / multi-allow
      - preserved 标识 (品牌/作者/asset id): 该词在 source 中以 proper noun
        (source 里大写) 或位于 bracket/NA_/URL/型号 结构内出现 -> 视为保留, 非残留
      - 型号/产品组合 (13 Pro Max 里 pro/max)
    不建宽泛"大写/camelCase 全 protected"规则: 只有出现在 source 且为 proper-noun/
    结构保留 才算; 译文里新增的、或 source 里小写普通词残留 -> 判残留。
    """
    low = zh.lower()
    for mw in _MULTI_ALLOW:
        low = low.replace(mw, " ")
    src_raw = src or ""
    src_low = src_raw.lower()
    # 1) 结构保留 token: bracket 内 / NA_ / URL 域名 (含裸域名 word.com)
    src_ids = set()
    for m in re.finditer(r"\[([^\]]+)\]", src_raw):
        src_ids.update(re.findall(r"[a-z]{2,}", m.group(1).lower()))
    for m in re.finditer(r"(?i)na_[a-z0-9_\-]+", src_raw):
        src_ids.update(re.findall(r"[a-z]{2,}", m.group(0).lower()))
    # 裸域名: [a-z0-9]+.(com|net|org|io|cc|cn) 及其子串词
    for m in re.finditer(r"(?i)([a-z0-9]+\.(?:com|net|org|io|cc|cn))", src_raw):
        dom = m.group(1).lower()
        src_ids.add(dom)
        dom_host = dom.split(".")
        src_ids.update(re.findall(r"[a-z]{2,}", dom_host[0]))
        if len(dom_host) > 1:
            src_ids.add(dom_host[1])  # tld (com/net/...)
    # 2) proper-noun 保留: source 里大写的词 (专名/品牌/作者, 含 Title-Case 与 ALL-CAPS)
    src_caps_low = set()
    for w in re.findall(r"[A-Z][a-z]{1,}", src_raw):       # Bradford / iPhone
        src_caps_low.add(w.lower())
    for w in re.findall(r"\b[A-Z]{2,}\b", src_raw):       # BRADFORD / IPA
        src_caps_low.add(w.lower())

    bad = []
    for w in _EN_WORD.findall(low):
        wl = w.lower()
        if wl in _ALLOW_EN or wl in _GLOSS_EN:
            continue
        if wl in src_ids:
            continue
        if wl in src_caps_low and wl in src_low.split():
            continue  # source 里的专名 (品牌/作者) -> 保留
        # 型号/产品: source 里存在 "<word> <num>" 或 "<num> <word> <word>" 组合
        if re.search(r"(?i)\b" + re.escape(w) + r"\b\s*\d+", src_raw) or \
           re.search(r"(?i)\b\d+\s+" + re.escape(w) + r"\b", src_raw):
            continue
        bad.append(w)
    return sorted(set(bad))


def cjk_latin_glue(zh):
    """R2: 单个独立 Latin 字母紧贴 CJK 且无空白/分隔 (异常黏连, 如旧 'I我')。
    只针对单字母 (非 "english" 等多字母词的首字母), 避免把普通英文词误报。"""
    hits = []
    for i, ch in enumerate(zh):
        if not (ch.isascii() and ch.isalpha()):
            continue
        # 仅当该 Latin 字母为单字母 (前后都不是 ascii 拉丁字母) 才可能是异常黏连
        prev_ascii_alpha = i > 0 and zh[i - 1].isascii() and zh[i - 1].isalpha()
        next_ascii_alpha = i + 1 < len(zh) and zh[i + 1].isascii() and zh[i + 1].isalpha()
        if prev_ascii_alpha or next_ascii_alpha:
            continue  # 是更长英词的一部分, 不判
        # 紧贴 CJK (无空白/分隔): 前或后紧邻 CJK
        prev_cjk = i > 0 and (_CJK.match(zh[i - 1]))
        next_cjk = i + 1 < len(zh) and (_CJK.match(zh[i + 1]))
        if prev_cjk or next_cjk:
            if next_cjk:
                hits.append(zh[i] + zh[i + 1])   # latin+CJK  (如 我I)
            else:
                hits.append(zh[i - 1] + zh[i])   # CJK+latin  (如 I我 -> 但显示 里I style: CJK在前)
            continue
    return hits


def digits_from(s):
    return re.findall(r"\d+", s)


def digit_drop(src, tr):
    """R3: 原文数字序列是否在译文完整保留 (等值集合, 不判品牌/编号语义)。"""
    sd = digits_from(src)
    td = digits_from(tr)
    if not sd:
        return []
    missing = []
    sc = Counter(sd)
    tc = Counter(td)
    for d, n in sc.items():
        if tc.get(d, 0) < n:
            missing.append(d)
    return missing


def bracket_balance(s):
    """R4: () [] {} 各自配对 (独立栈)。"""
    bad = []
    for op, cl in [("(", ")"), ("[", "]"), ("{", "}")]:
        if s.count(op) != s.count(cl):
            bad.append(f"{op}{cl}:{s.count(op)}/{s.count(cl)}")
    return bad


def bracket_id_broken(src, tr):
    """R5: source 的 '[...]' 内 creator/URL/accessory id 必须原样保留于译文。"""
    bad = []
    for m in re.finditer(r"\[([^\]]+)\]", src):
        inner = m.group(1).strip()
        if not inner:
            continue
        # 只当括号内含 技术标识 (URL/域名/纯字母数字代号 或 NA_/ACC 等) 才检查保持
        if _is_technical_inner(inner):
            if inner not in tr:
                bad.append(inner)
    return bad


def _is_technical_inner(inner):
    """是否技术标识: 含 域名(.com)/下划线/连字符密集/纯字母数字无空格 或 不以纯英文词为主。"""
    if re.search(r"\.(com|net|org|cc|io)(\s|$)", inner, re.I):
        return True
    if re.search(r"\b(NA_|ACC\b|acc\b|cc\b|mesh\b|id\b)", inner, re.I):
        return True
    # 纯 token 无空白 (asset id)
    if inner and " " not in inner and re.search(r"[A-Za-z0-9_\-]", inner):
        # 排除纯英文普通词 ("Pose", "Love")
        if not re.fullmatch(r"[A-Za-z]+", inner):
            return True
    return False


def length_delta(src, tr):
    """R6: 极端长度变化 (len 比). 返回 (ratio, direction)。"""
    ns, nt = len(src), len(tr)
    if ns <= 0:
        return None
    lo, hi = min(ns, nt), max(ns, nt)
    ratio = (hi / lo) if lo else 99.0
    if ratio > 3.0 or ratio < (1 / 3.0):
        return (round(ratio, 2), "LONG" if nt > ns else "SHORT")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--done", required=True)
    ap.add_argument("--batch", type=int, default=173)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    if not Path(a.done).exists():
        sys.exit(f"[HARD-FAIL] done 不存在: {a.done}")

    rows = list(csv.DictReader(open(a.done, encoding="utf-8-sig")))
    done = [r for r in rows if (r.get("status") or "").strip() in ("DONE", "APPROVED")]
    print(f"[input] done 总行 = {len(rows)}  DONE/APPROVED = {len(done)}   (期望 {a.batch})")
    if len(done) != a.batch:
        print(f"  !!! DONE 数 {len(done)} != {a.batch}。继续按实际处理, 报告如实。")

    flags_map = ["RESIDUAL_EN", "CJK_LATIN_GLUE", "DIGIT_DROP",
                 "BRACKET_IMBALANCE", "BRACKET_ID_BROKEN", "LENGTH_DELTA"]
    out_rows = []
    clean = 0
    for r in done:
        tid = r.get("translation_id")
        src = (r.get("source_text") or "").strip()
        tr = (r.get("translation") or "").strip()
        flags = []
        detail = []

        # R1 residual English
        re_words = residual_english(tr, src)
        if re_words:
            flags.append("RESIDUAL_EN")
            detail.append("残留英文: " + ",".join(re_words))

        # R2 CJK/Latin 异常黏连
        glue = cjk_latin_glue(tr)
        if glue:
            flags.append("CJK_LATIN_GLUE")
            detail.append("中英黏连: " + ",".join(glue[:5]))

        # R3 digit drop
        dd = digit_drop(src, tr)
        if dd:
            flags.append("DIGIT_DROP")
            detail.append("数字缺失: " + ",".join(dd))

        # R4 bracket balance
        bb = bracket_balance(tr)
        if bb:
            flags.append("BRACKET_IMBALANCE")
            detail.append("括号不平衡: " + ",".join(bb))

        # R5 bracketed creator/URL/accessory id preserved
        bi = bracket_id_broken(src, tr)
        if bi:
            flags.append("BRACKET_ID_BROKEN")
            detail.append("括号技术标识被破坏: " + ",".join(bi[:4]))

        # R6 extreme length
        ld = length_delta(src, tr)
        if ld:
            flags.append("LENGTH_DELTA")
            detail.append(f"长度{ld[1]} x{ld[0]}")

        if not flags:
            clean += 1
            continue
        out_rows.append({"translation_id": tid, "source_text": src,
                         "translation": tr, "flags": "+".join(flags),
                         "detail": "; ".join(detail)})

    out_path = Path(a.out) if a.out else str(Path(a.done).parent / "translation_desc_qa_candidates.csv")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, ["translation_id", "source_text", "translation", "flags", "detail"])
        w.writeheader()
        for o in out_rows:
            w.writerow(o)

    n_susp = len(out_rows)
    print(f"\n[结果] DONE input={len(done)}  suspicious candidates={n_susp}  clean candidates={clean}")
    print(f"[写出 REVIEW_CANDIDATE] {out_path}  ({n_susp} 行, 只读不改译文)")
    if n_susp:
        c = Counter()
        for o in out_rows:
            c.update(o["flags"].split("+"))
        print("[flag 分布]:")
        for f_, n in c.most_common():
            print(f"  {f_}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
