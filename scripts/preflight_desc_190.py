#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
preflight_desc_190.py — PACK_DESCRIPTION 190 无模型 preflight (确定性, 只读, 零模型)
=====================================================================================
背景 (2026-08-15 用户裁决): PACK_TITLE 407 已冻结 (translation_done_title_final.csv
  FINAL_HARD_GATE PASS, production overlay = 163)。下一阶段启动 PACK_DESCRIPTION 190,
  先只做 preflight, 不立即跑模型。

本脚本只读三个现有文件, 不调用模型, 不生成 sidecar, 不 merge, 不复用 TITLE done:
  --manifest output/translation_batch_manifest.csv          (全部 workset, 含 assigned_batch)
  --tids     output/batch_tids/batch_PACK_DESCRIPTION.tids  (190 tid)
  --overrides output/translation_overrides.production.csv   (163 production overlay)

预期 (由真实输入推导, 不硬编码; 缺一即 HARD-FAIL):
  requested=190  scoped=190  unique=190  authoritative TRANSLATE=190
  production_overrides_loaded=163  terminal KEEP/manual conflict=0  -> PASS

语义 (与 phase2b_translate.py 的 preflight 完全一致, 供后续真模型 run 复用同门):
  - requested  = --tids 里的 tid 数
  - scoped     = 从 manifest 按这些 tid 裁出的行数 (请保留 manifest 的 decision/assigned_batch)
  - unique     = scoped 唯一 tid 数
  - authoritative_TRANSLATE = scoped 中 decision in (TRANSLATE|FULL|PARTIAL) 行数,
      缺 decision 缺省 TRANSLATE (与 load_todo 一致)
  - production_overrides_loaded = production overlay 实际加载行数 (len(ovr)=163)
  - terminal KEEP/manual conflict = scoped 中命中 overlay 且 action 为 KEEP/TRANSLATE 的行数
      (应=0: PACK_DESCRIPTION 批不得含任何已终态定稿 tid)

HARD-FAIL / exit != 0:
  - 任一输入文件缺失
  - tids 文件 tid 不在 manifest (requested 非 scoped 子集 / over)
  - manifest 中该批 tid 的 assigned_batch != PACK_DESCRIPTION (不明覆盖)
  - requested/scoped/unique/权威TRANSLATE 任一 != 190 (或冲突 != 0)
  - production overlay 加载数与期望不符 (163)
  - tids 文件内或 scoped 内 duplicate tid
用法 (只读, 无模型):
  python scripts/preflight_desc_190.py \
      --manifest output/translation_batch_manifest.csv \
      --tids     output/batch_tids/batch_PACK_DESCRIPTION.tids \
      --overrides output/translation_overrides.production.csv
"""
import sys, os, csv, argparse
from pathlib import Path


def _norm(s):
    return (s or "").strip().casefold()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--tids", required=True)
    ap.add_argument("--overrides", required=True)
    a = ap.parse_args()

    for p in (a.manifest, a.tids, a.overrides):
        if not Path(p).exists():
            sys.exit(f"[HARD-FAIL] 输入文件不存在: {p}")

    # ---- 请求 tid 集 ----
    req_rows = [ln.strip() for ln in open(a.tids, encoding="utf-8") if ln.strip()]
    req_tids = [r for r in req_rows if r and "," not in r and not r.lower().startswith("translation")]
    # 若行内逗号拆分 (与 ID_FROM_FILE 兼容), 全拆
    _flat = []
    for r in req_rows:
        if r.lower().startswith("translation_id") or r.lower().startswith("tid"):
            continue
        _flat += [x.strip() for x in r.split(",") if x.strip()]
    req_tids = _flat
    _req_set = set(req_tids)
    if len(_req_set) != len(req_tids):
        sys.exit(f"[HARD-FAIL] batch_PACK_DESCRIPTION.tids 内 duplicate tid: "
                 f"{sorted(t for t, c in __import__('collections').Counter(req_tids).items() if c > 1)}")

    # ---- manifest: 裁 scoped ----
    man = list(csv.DictReader(open(a.manifest, encoding="utf-8-sig")))
    man_by = {r.get("translation_id"): r for r in man}
    if len(man_by) != len(man):
        dups = [t for t, c in __import__('collections').Counter(r.get("translation_id") for r in man).items() if c > 1]
        sys.exit(f"[HARD-FAIL] manifest 内 duplicate tid: {sorted(dups)}")

    over_tids = _req_set - set(man_by)
    if over_tids:
        sys.exit(f"[HARD-FAIL] requested tid 不在 manifest (over 子集): {sorted(over_tids)}")

    scoped = [man_by[t] for t in req_tids]  # 按 tids 文件顺序
    scope_set = set(r.get("translation_id") for r in scoped)
    assert len(scope_set) == len(scoped), "scoped duplicate tid"

    # ---- assigned_batch 校验: 全部须为 PACK_DESCRIPTION ----
    bad_batch = [r.get("translation_id") for r in scoped
                 if (r.get("assigned_batch") or "").strip() != "PACK_DESCRIPTION"]
    if bad_batch:
        sys.exit(f"[HARD-FAIL] scoped tid 的 assigned_batch != PACK_DESCRIPTION: {sorted(bad_batch)}")

    # ---- authoritative TRANSLATE (缺 decision 缺省 TRANSLATE, 与 load_todo 一致) ----
    _auth_tr = 0
    for r in scoped:
        _d = (r.get("decision") or "").strip()
        if (_d or "TRANSLATE") in ("TRANSLATE", "FULL_TRANSLATE", "PARTIAL_TRANSLATE"):
            _auth_tr += 1

    # ---- production overlay 加载 (与 phase2b load_overrides 一致: (tid, raw source)) ----
    ovr = {}
    _OV = {"TRANSLATE", "KEEP", "REVIEW"}
    with open(a.overrides, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            tid = (r.get("translation_id") or "").strip()
            src = (r.get("source_text") or "").strip()
            act = (r.get("action") or "").strip().upper()
            if not tid or not src or act not in _OV:
                continue
            ovr[(tid, src)] = act
    ovr_loaded = len(ovr)

    # ---- terminal KEEP/manual conflict (scoped 命中 overlay 终态) ----
    conflicts = []
    for r in scoped:
        key = (r.get("translation_id"), (r.get("source_text") or "").strip())
        if key in ovr:
            conflicts.append((key, ovr[key]))
    n_conflict = len(conflicts)

    requested = len(_req_set)
    scoped_n = len(scoped)
    unique_n = len(scope_set)

    print("=== PACK_DESCRIPTION 190 preflight (zero-model) ===")
    print(f"requested                    = {requested}")
    print(f"scoped                       = {scoped_n}")
    print(f"unique                       = {unique_n}")
    print(f"authoritative TRANSLATE      = {_auth_tr}")
    print(f"production_overrides_loaded  = {ovr_loaded}")
    print(f"terminal KEEP/manual conflict={n_conflict}")
    if conflicts:
        for k, act in conflicts[:10]:
            print(f"    conflict {k[0]} action={act}")

    ok = (requested == 190 and scoped_n == 190 and unique_n == 190
          and _auth_tr == 190 and ovr_loaded == 163 and n_conflict == 0)
    print(f"DESC_PREFLIGHT: {'PASS' if ok else 'FAIL'}"
          + ("  (requested/scoped/unique/auth_TR=190, overlay=163, conflict=0)" if ok else ""))
    if not ok:
        sys.exit(4)
    return 0


if __name__ == "__main__":
    sys.exit(main())
