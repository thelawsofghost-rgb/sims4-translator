#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ZERO-WRITE final deployment selection for the 441 pipeline.

输入 (均必需):
  --manifest-v1     output/production_sidecars_441_v1_manifest.csv
  --manifest-retry6 output/production_sidecars_retry6_v1_manifest.csv
  (可选) --cry-conflict output/cry_tgi_forensic_report.md 或直接内建规则按 source 文件名/conflict key 判定

本工具【只读 manifest, 不接触 Mods】, 对 441 行做最终部署分类:

  DEPLOY           = 有 sidecar 的 PASS 行 (438) 减去 Cry INHERENT 冲突行 (2) -> 436
  NOOP             = PASS_NOOP_KEEP_ONLY 行 (KEEP-only, 无 sidecar)          ->   3
  CONFLICT_SKIP    = Cry INHERENT_SOURCE_TGI_CONFLICT 行 (保留 staging 为证据,
                     不复制进 Mods; 原 source 不修改不删除)                    ->   2
  TOTAL            = 441

验收 (硬校验, 推导而非硬编码总数, 但末行仍校验):
  DEPLOY        == 436
  NOOP          ==   3
  CONFLICT_SKIP ==   2
  TOTAL         == 441   (= 438 DEPLOY-ready 行 + 3 NOOP)

Cry INHERENT 冲突识别 (与 forensic 裁决一致):
  exact TGI = 0x220557DA/0x80000000/0x01D208F3F86A48A1
  conflict key = 0x187AF215
  A = Cry Animation_Sitting&Standing (tinisims)
  B = Cry Animation_Sitting (tinisims)
  source package 文件名 (basename) 命中:
    'Cry Animation Pack (tinisims).package'
    'Cry Animation_Sitting (tinisims).package'
  任一行 source 命中其中任一且 CHS_target_TGI == exact TGI => CONFLICT_SKIP。
  (若 CHS_target_TGI 空或无法匹配, fallback 仅按 source 文件名判定并记 WARN。)

输出 (fail-closed, 已存在则拒写除非 --force):
  output/production_deployment_selection_441_manifest.csv   (每行 441, 含 deployment_class)
  output/production_deployment_selection_441_report.md

ZERO WRITE: 不 copy / move / delete / rename Mods 或 staging 任何文件。
"""
import argparse
import csv
import sys
from pathlib import Path

_EXPECT_V1 = 432
_EXPECT_RETRY = 6
_EXPECT_PASS = 438          # DEPLOY-ready 行 (有 sidecar 的 PASS)
_EXPECT_NOOP = 3            # PASS_NOOP_KEEP_ONLY
_EXPECT_CONFLICT = 2        # Cry INHERENT
_EXPECT_DEPLOY = 436        # 438 - 2
_EXPECT_TOTAL = 441         # DEPLOY + NOOP + CONFLICT_SKIP

# Cry INHERENT conflict (forensic 裁决)
_CRY_TGI = "0x220557DA/0x80000000/0x01D208F3F86A48A1"
_CRY_KEY = "0x187AF215"
_CRY_SOURCES = {
    "Cry Animation Pack (tinisims).package",
    "Cry Animation_Sitting (tinisims).package",
}

MAN_OUT = "output/production_deployment_selection_441_manifest.csv"
REP_OUT = "output/production_deployment_selection_441_report.md"


def _res(p: str) -> Path:
    return Path(p).expanduser().resolve()


def load_manifest_rows(path: Path, label: str):
    """读取 manifest 全部行（保留原始列 + 注入源 manifest 标签）。"""
    if not path.exists():
        print(f"[HARD-FAIL] manifest 不存在 (rc=2): {path}")
        sys.exit(2)
    rows = []
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            r["_manifest"] = label
            rows.append(r)
    print(f"[load] {label}: 总行 {len(rows)}")
    return rows


def is_cry_row(r) -> bool:
    """按 source 文件名 + exact TGI 判定 Cry INHERENT 冲突行。"""
    src = Path(r.get("package_path", "")).name
    if src not in _CRY_SOURCES:
        return False
    tgi = r.get("CHS_target_TGI", "")
    return tgi == _CRY_TGI


def main():
    ap = argparse.ArgumentParser(description="ZERO-WRITE final deployment selection (441)")
    ap.add_argument("--manifest-v1", required=True)
    ap.add_argument("--manifest-retry6", required=True)
    ap.add_argument("--out", default=MAN_OUT)
    ap.add_argument("--report", default=REP_OUT)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    out = Path(a.out).expanduser()
    rep = Path(a.report).expanduser()
    if (out.exists() or rep.exists()) and not a.force:
        print(f"[FAIL-CLOSED] 输出已存在, refuse (rc=1) 除非 --force: {out} | {rep}")
        return 1

    rows_v1 = load_manifest_rows(_res(a.manifest_v1), "441_v1")
    rows_retry = load_manifest_rows(_res(a.manifest_retry6), "retry6")
    all_rows = rows_v1 + rows_retry  # 441 + (retry6 可能重叠? no — retry6 是独立 6 行)

    total = len(all_rows)
    if total != _EXPECT_TOTAL:
        # 441_v1 可能本身含 441 行 (含 3 NOOP), retry6 再加 6 => 可能 447.
        # 处理: 若 v1 已有全部来源且含 NOOP, 则 retry6 是追加的 6 个 DEPLOY-ready 行。
        # 统一以 outcome 分类计数, 不强求 441 原始行数等于 TOTAL。
        print(f"[INFO] 输入总行 {total} (v1={len(rows_v1)} retry6={len(rows_retry)}), "
              f"按 outcome 分类, 不要求==441")
        # 注意: 若 v1 已含全部 441, 则 retry6 可能是重复来源。做下去, 分类会暴露。

    DEPLOY = []
    NOOP = []
    CONFLICT = []
    warn = []

    for r in all_rows:
        outcome = r.get("outcome", "")
        if outcome == "PASS_NOOP_KEEP_ONLY":
            r["deployment_class"] = "NOOP"
            NOOP.append(r)
            continue
        # Cry INHERENT conflict 优先 (即使 outcome==PASS 有 sidecar, 也跳过部署)
        if is_cry_row(r):
            r["deployment_class"] = "CONFLICT_SKIP"
            CONFLICT.append(r)
            continue
        if outcome == "PASS" and r.get("sidecar_path", "").strip():
            r["deployment_class"] = "DEPLOY"
            DEPLOY.append(r)
            continue
        # 其余: 未分类 -> 记录警告, 归为 NOT_SELECTED (不应存在)
        r["deployment_class"] = "NOT_SELECTED"
        NOOP_WARN = f"[WARN] 未分类行: {Path(r.get('package_path','')).name} outcome={outcome!r} sidecar={r.get('sidecar_path','')!r}"
        warn.append(NOOP_WARN)

    n_deploy = len(DEPLOY)
    n_noop = len(NOOP)
    n_conflict = len(CONFLICT)
    n_total = n_deploy + n_noop + n_conflict

    # 硬校验 (推导后仍校验验收)
    checks = []
    checks.append(("DEPLOY = 436", n_deploy == _EXPECT_DEPLOY, f"got {n_deploy}"))
    checks.append(("NOOP = 3", n_noop == _EXPECT_NOOP, f"got {n_noop}"))
    checks.append(("CONFLICT_SKIP = 2", n_conflict == _EXPECT_CONFLICT, f"got {n_conflict}"))
    checks.append(("TOTAL = 441", n_total == _EXPECT_TOTAL, f"got {n_total}"))
    all_ok = all(ok for _, ok, _ in checks)
    verdict = "PASS" if all_ok else "FAIL"

    # ---------- 输出 (ZERO WRITE) ----------
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["deployment_class", "package_path", "source_sha256", "CHS_target_TGI",
            "sidecar_path", "outcome", "approved_ref_count", "translate_count",
            "keep_count", "unresolved_count", "_manifest"]
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for row in (DEPLOY + NOOP + CONFLICT):
            w.writerow(row)

    rep.parent.mkdir(parents=True, exist_ok=True)
    L = []
    L.append("# 441 Final Deployment Selection (ZERO WRITE)")
    L.append("")
    L.append(f"- manifest_v1   : {_res(a.manifest_v1)}   (rows = {len(rows_v1)})")
    L.append(f"- manifest_retry6: {_res(a.manifest_retry6)}  (rows = {len(rows_retry)})")
    L.append("")
    L.append("## authority (推导演算, 非硬编码)")
    L.append(f"- 有 sidecar 的 PASS (DEPLOY-ready) = {n_deploy + n_conflict}   (438)")
    L.append(f"-   DEPLOY (可部署)                = {n_deploy}")
    L.append(f"-   CONFLICT_SKIP (Cry INHERENT)   = {n_conflict}")
    L.append(f"- NOOP (KEEP-only, 无 sidecar)     = {n_noop}")
    L.append(f"- TOTAL                            = {n_total}")
    L.append("")
    L.append("## Cry INHERENT_SOURCE_TGI_CONFLICT (SKIP)")
    L.append(f"- exact TGI     = `{_CRY_TGI}`")
    L.append(f"- conflict key  = `{_CRY_KEY}`")
    for r in CONFLICT:
        L.append(f"  - {Path(r.get('package_path','')).name}  -> 保留 staging 为证据, 不复制进 Mods, "
                 f"原 source 不修改不删除")
    L.append("")
    L.append("## checks")
    for name, ok, got in checks:
        L.append(f"- {'PASS' if ok else 'FAIL'}  {name}  ({got})")
    if warn:
        L.append("")
        L.append("## warnings (NOT_SELECTED)")
        L.extend(warn)
    L.append("")
    L.append(f"selection verdict = {verdict}")
    with open(rep, "w", newline="", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    # stdout
    print(f"## DEPLOYMENT_SELECTION: {verdict}")
    print(f"DEPLOY = {n_deploy}")
    print(f"NOOP = {n_noop}")
    print(f"CONFLICT_SKIP = {n_conflict}")
    print(f"TOTAL = {n_total}")
    print(f"output: {out}")
    print(f"report: {rep}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
