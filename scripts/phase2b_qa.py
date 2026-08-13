#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 2B 自动 QA: 读取 translation_done.csv, 产出 translation_qa_report.csv。

只读不写 .package。不自动修 REVIEW。可疑项分三类:
  PASS   - 通过
  REVIEW - 需人工确认 (疑似漏译/中英混杂/超长超短/状态非终态)
  ERROR  - 明确错误 (译文为空却有 DONE / 译文==原文(该翻没翻) / 受保护token丢失/编号改变)

用法:
  python scripts/phase2b_qa.py <out_dir>
  python scripts/phase2b_qa.py D:\\projects\\sims4_trans\\output
"""
import csv
import re
import sys
from pathlib import Path

# ---- 允许在中文译文中保留的英文: 编号/版本/技术标签/作者/ID/固定术语 ----
# 单 token 白名单 (大小写不敏感, 整 token 匹配)
_ALLOW_EN = {
    # 技术/版本/编号系列
    "anim", "sims", "sims4", "sim4", "mw", "sk", "bl", "studio",
    "v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10",
    "d", "dlc", "pack", "posed", "pose", "poses", "posepack", "pif",
    # 作者/命名 (高频姿势作者缩写)
    "kcat", "snb", "ava", "grr", "amai", "bel", "ver", "azn", "m4", "mf",
    # 覆盖词(可能作为英文留在括号里)
    "unknown", "none", "n/a", "na", "id", "new", "num", "no",
}
# glossary 已译为中文的键 (译文中不应再出现原英文; 但若残留, 算 REVIEW 而非 ERROR)
_GLOSS_EN = {"right", "left", "middle", "positive", "negative", "neutral",
             "concern", "doubtful", "smirk", "sim", "idle"}
# 多词技术串 (原文里被程序保留的)
_MULTI_ALLOW = ("*anim", "all in one", "pose pack", "english", "livestream")

# 英文小写正则 (用于检测译文中的残留英文词)
_EN_WORD = re.compile(r"[A-Za-z]{2,}")

_HELPERS = {
    "LOADED": False,
}


def _load_todo(textcol):
    pass


def residual_english(zh: str) -> list:
    """挑出译文中残留的英文词 (排除白名单/编号/技术标签)。返回 [(token)]。"""
    bad = []
    low_all = zh.lower()
    # 先排除多词白名单 (如 *anim)
    for mw in _MULTI_ALLOW:
        low_all = low_all.replace(mw, " ")
    for w in _EN_WORD.findall(low_all):
        if w.lower() in _ALLOW_EN or w.lower() in _GLOSS_EN:
            continue
        # 纯数字/版本夹字母的已由编号逻辑覆盖; 这里是普通英文词
        bad.append(w)
    return bad


def missing_protected(text: str, translation: str, psp: str, mode: str) -> list:
    """PARTIAL 行: 校验 protected_spans 声明的 token 是否都出现在译文中。"""
    if mode != "PARTIAL_TRANSLATE" or not psp:
        return []
    miss = []
    for span in [s for s in psp.split(";") if s.strip()]:
        tok = span.split("@")[0].strip()
        # 纯标点/空白不参与校验 (如 "." 来自编号格式)
        if not tok or not re.search(r"[A-Za-z0-9]", tok):
            continue
        if tok not in translation:
            miss.append(tok)
    return miss


def numbers_changed(source: str, translation: str) -> list:
    """源里的数字/编号串是否在译文中丢失或改变。返回失配的编号token。"""
    if not translation:
        return []
    src_toks = set(re.findall(r"\d[\w]*(?:-\d[\w]*)*", source))
    if not src_toks:
        return []
    bad = []
    for t in src_toks:
        # 纯数字本身可能因中文整合省略空格而"不在"译文 (如 4-6); 用数字核验而非整串
        if t.strip() and not re.search(r"\d", t):
            continue
        if t not in translation:
            # 容错: 提取核心数字看是否仍在
            core = re.match(r"\d+", t)
            if core and core.group(0) in translation:
                continue
            bad.append(t)
    return bad


def classify(row: dict) -> (str, str):
    """返回 (状态, 说明)。"""
    tid = row["translation_id"]
    src = (row.get("source_text") or "").strip()
    zh = (row.get("translation") or "").strip()
    status = row.get("status")
    mode = row.get("translate_mode")
    psp = row.get("protected_spans") or ""
    notes = []

    # 1) 状态非 DONE/APPROVED
    if status not in ("DONE", "APPROVED"):
        return "REVIEW", f"状态={status} (非终态)"

    # 2) 译文为空但标 DONE -> ERROR
    if not zh:
        return "ERROR", "译文为空但 status=DONE"

    # 3) 该翻没翻: 源有明显语义但我 == 原样 (纯KEEP除外)
    if mode != "KEEP":
        if zh == src:
            return "ERROR", "译文与原文完全相同 (疑似未翻译)"

    # gloss 词若残留英文 -> 提示 (不一定错, 如 "Sim" 可能作者刻意保留)
    gloss_left = [g for g in _GLOSS_EN if re.search(rf"\b{g}\b", zh, re.I)]
    if gloss_left:
        notes.append("残留glossary英文词:" + ",".join(gloss_left))

    # 4) protected token 丢失 (PARTIAL)
    miss = missing_protected(src, zh, psp, mode)
    if miss:
        return "ERROR", "受保护token丢失:" + ",".join(miss)

    # 5) 编号丢失/改变
    nc = numbers_changed(src, zh)
    if nc:
        return "ERROR", "编号丢失/改变:" + ",".join(nc)

    # 6) 残留英文 (排除白名单)
    res = residual_english(zh)
    if res:
        notes.append("残留英文:" + ",".join(sorted(set(res))))

    # 7) 异常长度 (中文字符占比低 -> 可能漏译; 超长)
    if zh:
        cjk = sum(1 for c in zh if "\u4e00" <= c <= "\u9fff" or "\u3000" <= c <= "\u303f")
        if zh and cjk == 0 and len(res) == 0:
            notes.append("无中文字符?")  # 纯技术行也可能无中文
        if len(zh) > 120:
            notes.append(f"译文超长({len(zh)}字)")
        if len(src) >= 4 and len(zh) < 2 and mode not in ("KEEP",):
            notes.append("译文过短")

    if notes:
        return "REVIEW", "; ".join(notes)
    return "PASS", ""


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/phase2b_qa.py <out_dir>")
        sys.exit(1)
    out_dir = Path(sys.argv[1])
    done = out_dir / "translation_done.csv"
    if not done.exists():
        print(f"找不到 {done}; 请先跑 phase2b_translate.py")
        sys.exit(1)

    rows = list(csv.DictReader(open(done, encoding="utf-8-sig")))
    out_cols = ["translation_id", "source_text", "translate_mode", "status",
                "protected_spans", "translation", "qa", "qa_reason"]
    out_rows = []
    cnt = {"PASS": 0, "REVIEW": 0, "ERROR": 0}
    for r in rows:
        qa, reason = classify(r)
        cnt[qa] += 1
        out_rows.append({
            "translation_id": r["translation_id"],
            "source_text": r.get("source_text", ""),
            "translate_mode": r.get("translate_mode", ""),
            "status": r.get("status", ""),
            "protected_spans": r.get("protected_spans", ""),
            "translation": r.get("translation", ""),
            "qa": qa,
            "qa_reason": reason,
        })

    out = out_dir / "translation_qa_report.csv"
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_cols)
        w.writeheader(); w.writerows(out_rows)

    print(f"[QA] 共 {len(rows)} 行: PASS={cnt['PASS']}  REVIEW={cnt['REVIEW']}  ERROR={cnt['ERROR']}")
    print(f"[QA] 写出 {out}")
    print(f"[QA] 校验 PASS+REVIEW+ERROR == 行数 : {cnt['PASS']+cnt['REVIEW']+cnt['ERROR']} == {len(rows)}")


if __name__ == "__main__":
    main()
