#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m_review.py — 增量人工裁决层 (只记录 6 条人工 REVIEW 裁决, 不改任何 frozen 数据)
==========================================================================
背景: d_reclassify 输出的 REVIEW 共 6 条, 已由人工逐条裁决:
  5 条 -> TRANSLATE (附最终译文), 1 条 -> KEEP (@ninawhims, 作者 handle 不翻译)。
本轮只新增一个"人工裁决"增量层; 不调用模型、不生成 sidecar、不重跑包、
不改 frozen translation_catalog.csv / coverage / cohort / writer / resolver。

输入:
  --review   output/manual_review.tsv   (人工裁决清单, tab 分隔)
  --gap      output/gap_inventory.csv   (拉取 provenance/packages/宽松校验)
  --delta    output/translation_delta_catalog.csv  (校验该源确为 REVIEW 决策)
  --out      output/translation_manual_review.csv   [默认]

manual_review.tsv 列 (tab): translation_id(可空,留空自动算 stable id)
  | source_text | final_decision | translation | reason
  stable id 一律沿用 T_{source_hash}_g1 (source_hash = sha256(NFC(norm))[:12]),
 由本脚本推导, 不信任手填 tid。

窄校验 (fail-fast, rc != 0):
  - every source_text 必须在 --delta 中且其 frozen decision == REVIEW
    (禁止把非 REVIEW 的源静默改判 / 把普通 MISSING/unresolved 降级)
  - final_decision 只允许 TRANSLATE / KEEP
  - TRANSLATE 必须有非空 translation; KEEP 的 translation 留空
  - 输出 (tid, norm) 无重复; 输入 REVIEW 条数 == 输出条数 (无静默丢)
"""
import sys, os, csv, argparse, hashlib, unicodedata
from collections import Counter


def norm_text(s):
    return unicodedata.normalize("NFC", (s or "")).strip()


def source_hash(s):
    return hashlib.sha256(unicodedata.normalize("NFC", s).encode("utf-8")).hexdigest()[:12]


def make_translation_id(sh, group=1):
    return f"T_{sh}_g{group}"


def load_tsv(path):
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        rdr = csv.reader(f, delimiter="\t")
        header = [h.strip() for h in next(rdr)]
        for line in rdr:
            if not line or all(not c.strip() for c in line):
                continue
            rec = dict(zip(header, [c.strip() for c in line]))
            rows.append(rec)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", required=True, help="manual_review.tsv")
    ap.add_argument("--gap", required=True)
    ap.add_argument("--delta", required=True, help="translation_delta_catalog.csv")
    ap.add_argument("--out", default="output/translation_manual_review.csv")
    a = ap.parse_args()

    revs = load_tsv(a.review)
    if not revs:
        raise SystemExit("[FAIL] manual_review.tsv 无数据行")
    print(f"[manual] 人工裁决条目 = {len(revs)}")

    # delta 中 frozen decision 查表
    delta = {}
    for r in csv.DictReader(open(a.delta, encoding="utf-8-sig")):
        delta[norm_text(r.get("source_text") or "")] = r
    # gap 中 provenance/packages 查表
    gap = {}
    for r in csv.DictReader(open(a.gap, encoding="utf-8-sig")):
        gap[norm_text(r.get("source_text") or "")] = r

    out = []
    dec_cnt = Counter()
    for rc_ in revs:
        src = rc_.get("source_text") or ""
        n = norm_text(src)
        if n == "":
            raise SystemExit(f"[FAIL] manual_review 含空 source_text: {rc_}")
        if n not in delta:
            raise SystemExit(f"[FAIL] source 不在 delta catalog (非 REVIEW?): {src!r}")
        if (delta[n].get("decision") or "").strip() != "REVIEW":
            raise SystemExit(
                f"[FAIL] 该源在 frozen/pipeline 决策不是 REVIEW, 禁止人工改判: {src!r} "
                f"实际={delta[n].get('decision')!r}")
        fd = (rc_.get("final_decision") or "").strip().upper()
        if fd not in ("TRANSLATE", "KEEP"):
            raise SystemExit(f"[FAIL] final_decision 只允许 TRANSLATE/KEEP, got {fd!r}: {src!r}")
        tr = (rc_.get("translation") or "").strip()
        if fd == "TRANSLATE" and tr == "":
            raise SystemExit(f"[FAIL] TRANSLATE 必须给非空 translation: {src!r}")
        if fd == "KEEP" and tr != "":
            # KEEP 语义: translation 留空 (或等于原文)。非空即视为语义偏差 -> 硬拦
            raise SystemExit(f"[FAIL] KEEP 不应有 translation: {src!r} translation={tr!r}")
        sh = source_hash(n)
        tid = make_translation_id(sh, 1)
        dec_cnt[fd] += 1
        out.append({
            "translation_id": tid,
            "source_text": src,
            "source_hash": sh,
            "final_decision": fd,
            "translation": tr,
            "reason": (rc_.get("reason") or "").strip(),
            "provenance": (gap[n].get("provenance") if n in gap else "") or "",
            "package_count": (gap[n].get("package_count") if n in gap else "") or "",
            "packages": (gap[n].get("packages") if n in gap else "") or "",
        })

    # 硬 invariant: 输入 == 输出, 无重复 (tid, norm)
    keys = [(o["translation_id"], norm_text(o["source_text"])) for o in out]
    dup = [k for k, c in Counter(keys).items() if c > 1]
    if dup:
        raise SystemExit(f"[INVARIANT-FAIL] manual review 输出重复 (tid,norm): {dup[:5]}")
    if len(out) != len(revs):
        raise SystemExit(f"[INVARIANT-FAIL] 输入 {len(revs)} != 输出 {len(out)}")
    print(f"[INVARIANT] manual review 输入 == 输出 = {len(out)}  PASS")

    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, ["translation_id", "source_text", "source_hash",
                               "final_decision", "translation", "reason",
                               "provenance", "package_count", "packages"])
        w.writeheader()
        for o in out:
            w.writerow(o)

    print(f"\n[manual] 输出 {a.out}: TRANSLATE={dec_cnt.get('TRANSLATE',0)} / "
          f"KEEP={dec_cnt.get('KEEP',0)} / REVIEW=0   ({len(out)} 条)")


if __name__ == "__main__":
    main()
