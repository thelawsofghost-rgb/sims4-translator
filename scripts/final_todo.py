#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
final_todo.py — 合并 C + D-TRANSLATE + 人工 REVIEW->TRANSLATE 为最终待补译文清单
==========================================================================
只读合并层: 不改 frozen translation_catalog.csv / coverage / cohort / writer / resolver,
不调用模型, 不生成 sidecar, 不重跑 10 包。

合并口径 (唯一 unique source, 按 norm_text 去重; stable id 沿用 T_{source_hash}_g1):
  C  类 = 29  条  (translation_missing_result.csv, 全为待补 TRANSLATE)
  D  TRANSLATE = 597 条  (translation_delta_catalog.csv, decision==TRANSLATE)
  人工 REVIEW->TRANSLATE = 5 条  (translation_manual_review.csv, final_decision==TRANSLATE)
  ----------------------------------------------------------------
  最终待补 = 631 条 (预期)

同时核验并报告:
  manual REVIEW->KEEP = 1 (不计入待补)
  remaining REVIEW    = 0 (delta 中的 REVIEW 必须全部被人工裁决覆盖)
  remaining MISSING   = 0 (不允许未裁决缺失降级)

硬 invariant (fail-fast, rc != 0):
  - todo unique 严格 == C(29) + D_TRANSLATE(597) + MANUAL_T(5)
  - 这三组内部及组间 (tid,norm) 无重复
  - REVIEW 未裁决 = 0; 若 != 0 或 631 不成立 -> 打印差异来源并停止, 不自动补数据
"""
import sys, os, csv, argparse, hashlib, unicodedata
from collections import Counter


def norm_text(s):
    return unicodedata.normalize("NFC", (s or "")).strip()


def source_hash(s):
    return hashlib.sha256(unicodedata.normalize("NFC", s).encode("utf-8")).hexdigest()[:12]


def make_translation_id(sh, group=1):
    return f"T_{sh}_g{group}"


def load_csv_rows(path, cols):
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    # 补齐缺列
    for r in rows:
        for c in cols:
            r.setdefault(c, "")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--missing", required=True, help="translation_missing_result.csv (C 类 29)")
    ap.add_argument("--delta", required=True, help="translation_delta_catalog.csv (D 类)")
    ap.add_argument("--manual", required=True, help="translation_manual_review.csv")
    ap.add_argument("--out", default="output/translation_final_todo.csv")
    a = ap.parse_args()

    COLS = ["translation_id", "source_text", "source_hash", "decision",
            "reason", "translation", "provenance", "package_count", "packages"]

    # ---- C 类 (29) ----
    c_rows = load_csv_rows(a.missing, COLS)
    C = {}
    c_ok = 0
    for r in c_rows:
        n = norm_text(r.get("source_text") or "")
        if n == "":
            continue
        key = (r.get("translation_id") or make_translation_id(source_hash(n), 1), n)
        C.setdefault(key, {"source_text": r["source_text"] or n,
                           "decision": "TRANSLATE",
                           "reason": r.get("old_catalog_decision") or "C_MISSING_RESULT",
                           "translation": "",
                           "provenance": r.get("provenance") or "",
                           "package_count": r.get("affected_package_count") or "",
                           "packages": r.get("packages") or ""})
        c_ok += 1

    # ---- D 类 TRANSLATE (597) ----
    delta_rows = load_csv_rows(a.delta, COLS)
    D_TR = {}
    raw_review = Counter()
    for r in delta_rows:
        n = norm_text(r.get("source_text") or "")
        if n == "":
            continue
        dec = (r.get("decision") or "").strip()
        tid = r.get("translation_id") or make_translation_id(source_hash(n), 1)
        key = (tid, n)
        if dec == "REVIEW":
            raw_review[key] = r     # REVIEW 待人工裁决核对
        elif dec == "TRANSLATE":
            D_TR.setdefault(key, {"source_text": r["source_text"] or n,
                                  "decision": "TRANSLATE",
                                  "reason": r.get("reason") or "D_TRANSLATE",
                                  "translation": "",
                                  "provenance": r.get("provenance") or "",
                                  "package_count": r.get("package_count") or "",
                                  "packages": r.get("packages") or ""})
        # KEEP / EMPTY_SOURCE_NOOP 不计入待补

    # ---- 人工 REVIEW->TRANSLATE (5) ----
    man_rows = load_csv_rows(a.manual, COLS + ["final_decision"])
    MAN_T = {}
    MAN_K = 0
    MAN_K_src = []   # (tid, norm) for manual KEEP rulings
    man_covered = Counter()
    for r in man_rows:
        n = norm_text(r.get("source_text") or "")
        if n == "":
            continue
        fd = (r.get("final_decision") or "").strip().upper()
        tid = r.get("translation_id") or make_translation_id(source_hash(n), 1)
        key = (tid, n)
        if fd == "TRANSLATE":
            MAN_T[key] = {"source_text": r["source_text"] or n,
                          "decision": "TRANSLATE",
                          "reason": r.get("reason") or "MANUAL_REVIEW",
                          "translation": r.get("translation") or "",
                          "provenance": r.get("provenance") or "",
                          "package_count": r.get("package_count") or "",
                          "packages": r.get("packages") or ""}
        elif fd == "KEEP":
            MAN_K += 1
            MAN_K_src.append(key)

    # ---- 组间 (tid,norm) 重复检查 ----
    allk = list(C) + list(D_TR) + list(MAN_T)
    dup = [k for k, c in Counter(allk).items() if c > 1]
    if dup:
        raise SystemExit(f"[INVARIANT-FAIL] 合并后重复 (tid,norm): {dup[:5]}  "
                         f"— 报告差异来源, 不自动补数据")

    # ---- REVIEW 未裁决核对: delta REVIEW 必须全部被人工裁决(ruling)覆盖 (按 norm 匹配) ----
    man_ruled_norms = {k[1] for k in MAN_T} | {n for (_, n) in MAN_K_src}
    unruly = [(k, raw_review[k].get("source_text")) for k in raw_review
              if k[1] not in man_ruled_norms]
    if unruly:
        raise SystemExit(f"[INVARIANT-FAIL] {len(unruly)} 条 REVIEW 未经人工裁决, 不得进入待补: "
                         f"{[s for _, s in unruly][:5]} — 不自动补数据")

    # ---- 合并为唯一 todo ----
    todo = {}
    for k, v in C.items():
        todo[k] = v
    for k, v in D_TR.items():
        todo[k] = v
    for k, v in MAN_T.items():
        todo[k] = v

    total = len(todo)
    c_n, d_n, m_n = len(C), len(D_TR), len(MAN_T)
    expected = c_n + d_n + m_n
    print(f"[C] 类 待补 = {c_n}")
    print(f"[D] TRANSLATE = {d_n}")
    print(f"[manual] REVIEW->TRANSLATE = {m_n} | REVIEW->KEEP = {MAN_K}")
    print(f"[REVIEW 未裁决] = {len(unruly)}  (必须 0)")

    strict = (total == expected) and (total == 631) and (len(unruly) == 0)
    if not strict:
        raise SystemExit(
            f"[HARD-FAIL] 631 不严格成立: todo unique={total}, 期望={expected}(={c_n}+{d_n}+{m_n}), "
            f"REVIEW 未裁决={len(unruly)}. 报告差异来源, 不自动补数据.")
    print(f"[todo] todo unique source = {total}   (631 严格成立: PASS)")

    # ---- 写最终待补清单 ----
    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, COLS)
        w.writeheader()
        for k in sorted(todo):
            v = todo[k]
            w.writerow({"translation_id": k[0], "source_text": v["source_text"],
                        "source_hash": source_hash(norm_text(v["source_text"])),
                        "decision": v["decision"], "reason": v["reason"],
                        "translation": v["translation"],
                        "provenance": v["provenance"],
                        "package_count": v["package_count"],
                        "packages": v["packages"]})

    print(f"\n[out] {a.out}  ({total} unique source 待补译文)")
    # 汇总
    pv = Counter()
    for k in todo:
        for p in (todo[k]["provenance"] or "").split("|"):
            if p:
                pv[p] += 1
    print("provenance 分布 (待补):", dict(pv))


if __name__ == "__main__":
    main()
