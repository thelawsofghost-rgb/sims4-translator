#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bake_workset.py — 构造 deterministic 增量翻译 workset (仅 626 缺译文 source)
==========================================================================
decision 层已冻结 (frozen translation_catalog / gap_inventory / delta_catalog /
manual decisions / coverage / cohort / writer 全部不再改)。本层只读构造:

  输入:  --todo    output/translation_final_todo.csv   (631 unique source, 冻结)
         --manual  output/translation_manual_review.csv (6 条人工裁决: 5 TRANSLATE + 1 KEEP)
  输出:  --out     output/translation_incremental_workset.csv  (626, 仅缺最终译文的源)

口径:
  final todo            = 631
  manual pretranslated  =   5   (manual final_decision==TRANSLATE, 已有人工最终译文,
                                 禁止再送模型重翻; 保留在最终 merge)
  actual model workset  = 626   (C 29 + D TRANSLATE 597, 唯一缺最终译文)
  KEEP (含 manual KEEP @ninawhims / EMPTY_SOURCE_NOOP / D KEEP)  全部禁止进入翻译

规则/硬 invariant (fail-fast, rc != 0):
  - workset 只从 todo 取 decision==TRANSLATE 的行
  - manual-pretranslated 排除必须用 (stable tid, norm source_text) 精确匹配 todo 行;
    若 manual TRANSLATE 的某条不在 todo -> HARD-FAIL (说明 decision 层不一致, 不自动补)
  - 排除后 workset unique 严格 == todo(631) - manual_t(5) = 626
  - 626 + 5 == 631 严格成立, 否则打印差异来源并停止
  - workset 内 (tid, norm) 无重复; source_text/source_hash exact 对应 (不重算不改)
  - 不调用模型 / 不生成 sidecar / 不重跑包; 复用 frozen glossary/overrides/protected
    spans/translation policy (这些在后续 Phase2B 流水线中沿用)
  - 不重新生成已有旧译文; cache 内容不当 final QA 译文 (这里仅做清单, 不做翻译)
"""
import sys, os, csv, argparse, hashlib, unicodedata
from collections import Counter


def norm_text(s):
    return unicodedata.normalize("NFC", (s or "")).strip()


COLS = ["translation_id", "source_text", "source_hash", "decision",
        "reason", "translation", "provenance", "package_count", "packages"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--todo", required=True)
    ap.add_argument("--manual", required=True)
    ap.add_argument("--out", default="output/translation_incremental_workset.csv")
    a = ap.parse_args()

    # ---- todo (631) ----
    todo = {}
    for r in csv.DictReader(open(a.todo, encoding="utf-8-sig")):
        n = norm_text(r.get("source_text") or "")
        if n == "":
            continue
        tid = (r.get("translation_id") or "").strip()
        key = (tid, n)
        todo[key] = r
    print(f"[todo] final todo unique = {len(todo)}")

    # ---- manual (6) ----
    man = list(csv.DictReader(open(a.manual, encoding="utf-8-sig")))
    man_t = []   # manual TRANSLATE (5)
    man_k = []   # manual KEEP (1)
    for r in man:
        fd = (r.get("final_decision") or "").strip().upper()
        if fd == "TRANSLATE":
            man_t.append(r)
        elif fd == "KEEP":
            man_k.append(r)
    print(f"[manual] pretranslated TRANSLATE = {len(man_t)} | KEEP = {len(man_k)}")

    # ---- 排除 manual TRANSLATE from todo by (tid, norm) exact ----
    exclude = {}
    for r in man_t:
        n = norm_text(r.get("source_text") or "")
        tid = (r.get("translation_id") or "").strip()
        if n == "":
            raise SystemExit(f"[HARD-FAIL] manual TRANSLATE 空 source: {r}")
        key = (tid, n)
        if key not in todo:
            raise SystemExit(
                f"[HARD-FAIL] manual pretranslated 不在 final todo: "
                f"tid={tid!r} src={r.get('source_text')!r} — decision 层不一致, 不自动补。")
        exclude[key] = r

    # ---- workset = todo 中 decision==TRANSLATE 且非 manual pretranslated ----
    workset = {}
    not_translate = 0
    for key, r in todo.items():
        dec = (r.get("decision") or "").strip()
        if dec != "TRANSLATE":
            not_translate += 1
            continue     # KEEP/EMPTY/其他 禁止进翻译
        if key in exclude:
            continue     # 人工已定案, 排除出 model workset
        workset[key] = r

    # ---- 硬 invariant ----
    if len(workset) != 626:
        raise SystemExit(
            f"[HARD-FAIL] workset != 626: 实际={len(workset)} | todo={len(todo)} "
            f"manual_t={len(man_t)} -> 626 + 5 = 631 不成立。打印差异, 不自动补。")
    if len(workset) + len(man_t) != len(todo):
        raise SystemExit(
            f"[INVARIANT-FAIL] {len(workset)} + {len(man_t)} != {len(todo)}. 不自动补。")
    # 无重复
    keys = list(workset)
    dup = [k for k, c in Counter(keys).items() if c > 1]
    if dup:
        raise SystemExit(f"[INVARIANT-FAIL] workset 重复 (tid,norm): {dup[:5]}")
    # KEEP 必须 0 进 workset
    if any((r.get("decision") or "").strip() != "TRANSLATE" for r in workset.values()):
        raise SystemExit("[INVARIANT-FAIL] workset 混入非 TRANSLATE 决策")

    print(f"[workset] 实际翻译 workset = {len(workset)}  (626 严格成立: PASS)")
    print(f"[invariant] 626 + 5 = {len(workset) + len(man_t)} == todo {len(todo)}  "
          f"({'PASS' if len(workset)+len(man_t)==len(todo) else 'FAIL'})")

    # ---- 写 workset ----
    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, COLS)
        w.writeheader()
        for key in sorted(workset):
            r = workset[key]
            w.writerow({c: r.get(c, "") for c in COLS})

    # ---- provenance 分布 ----
    pv = Counter()
    pkg_impact = {}
    for r in workset.values():
        for p in (r.get("provenance") or "").split("|"):
            if p:
                pv[p] += 1
                pkg_impact.setdefault(p, set()).update((r.get("packages") or "").split("|"))
    print("\n=== workset 626 按 provenance 分布 ===")
    for p in sorted(pv):
        print(f"  {p}: {pv[p]}  (覆盖 {len(pkg_impact[p])} 包)")

    # ---- 分批执行建议 (沿用 Phase2B 流水线, 见下) ----
    print(f"\n[out] {a.out}  ({len(workset)} unique source)")


if __name__ == "__main__":
    main()
