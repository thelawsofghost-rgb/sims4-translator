#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run2_preflight.py —— Phase 3B2-SIDECAR run2 resolver/production-input 零写 preflight
=========================================================================================
目的: 在生成任何 sidecar 之前, 证明 gen_cohort_sidecars 的 resolver 在 run2 中使用的是
冻结的 3 个 production final (title_final 407 / desc_final 190 / production overlay 217),
并生成 zero-write preflight report (不建 package, 不碰 package, 不写 sidecar, 不改 Mods)。

报告覆盖:
  - cohort packages = 10; real source paths = 10; missing paths = 0
  - 每包:
      approved player-visible entries
      KEEP count / TRANSLATE count / unresolved count / source mismatch count
      duplicate KeyHash gate (target STBL)
      exact existing CHS TGI gate
  - aggregate:
      unresolved = 0 / source mismatch = 0 / policy conflict = 0
      duplicate target package skipped as frozen rule

历史说明 (不 gate): run1 曾 1 PASS + 9 MISSING (文档注记, 见 PHASE3B-SIDECAR.md
C-class), 但仓库内无可机读历史 artifact 记录具体哪 9 包/tid; 故不构造/不反推测。
preflight 只证明当前 10 个 cohort package 的 resolve 一致性 (unresolved=0 /
source_mismatch=0 / policy_conflict=0 / dup-KeyHash=0 / CHS-TGI=0)。

fail-closed:
  * 3 production final 文件不存在 / 0 行 / 行数 != 冻结值 -> HARD-FAIL
  * 任一 production source 是禁止的 batch/retry/desc-done/cache 文件 -> HARD-FAIL
  * 多源 (tid,norm_source) 一致性冲突 (action/translation) -> HARD-FAIL
  * 每包 unresolved > 0 或 source mismatch > 0 或 duplicate KeyHash -> 该包 FAIL,
    汇总若任一包 FAIL -> 整体 rc=1 (不生成 sidecar)
  * out-dir (若 --out-dir 给了) stale 非空 -> HARD-FAIL (与 gen_cohort_sidecars 一致)

零写保证: 本脚本不调用 writer、不创建任何 .package / manifest、不改任何输入文件;
仅 print 报告 + 可选写纯文本 preflight report (--report 显式才写, 默认 stdout)。
"""
import sys, os, csv, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from production_resolver import ProductionResolver, make_production_resolver, \
    BANNED_PRODUCTION_SOURCE, EXPECTED_ROWS
import pose_coverage as PC
from gen_cohort_sidecars import approved_pv_refs, resolve_all_approved

_COH_SLOT = "cohort_slot"
_COH_PATH = "package_path"
_COH_STATUS = "status"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", required=True, help="frozen cohort_selection.csv (10 行)")
    ap.add_argument("--title-final", required=True, help="output/translation_done_title_final.csv (407)")
    ap.add_argument("--desc-final", required=True, help="output/translation_done_desc_final.csv (190)")
    ap.add_argument("--production-overlay", required=True,
                    help="output/translation_overrides.production.csv (217)")
    ap.add_argument("--catalog", default="", help="旧 frozen catalog terminal (可选, precedence 最低)")
    ap.add_argument("--report", default="", help="显式写出纯文本 preflight report (缺省只打印)")
    ap.add_argument("--out-dir", default="", help="若提供, 校验 stale 非空(与 gen_cohort_sidecars 一致)")
    a = ap.parse_args()

    hf = []
    for p, lab in [(a.title_final, "title_final"), (a.desc_final, "desc_final"),
                   (a.production_overlay, "production_overlay"), (a.cohort, "cohort")]:
        if not Path(p).exists():
            hf.append(f"{lab} 文件不存在: {p}")
    if hf:
        print("[HARD-FAIL]\n  " + "\n  ".join(hf)); return 3

    # ---- 0) 禁止源守卫 + production resolver (多源一致性) ----
    try:
        resolver = make_production_resolver(
            a.title_final, a.desc_final, a.production_overlay,
            catalog=a.catalog or None)
    except RuntimeError as ex:
        print(str(ex)); return 2

    # ---- 1) cohort 预检 ----
    rows = []
    with open(a.cohort, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    slots = [r.get(_COH_SLOT, "") for r in rows]
    paths = [r.get(_COH_PATH, "") for r in rows]
    real = [p for p in paths if p and Path(p).exists()]
    missing = [p for p in paths if p and not Path(p).exists()]
    non_eligible = [r for r in rows if (r.get(_COH_STATUS, "").strip() or "").upper() != "ELIGIBLE_EXISTING_CHS"]

    out_lines = []
    out_lines.append("=" * 64)
    out_lines.append("Phase 3B2-SIDECAR run2 — resolver/production-input PREFLIGHT (zero-write)")
    out_lines.append("=" * 64)
    out_lines.append(f"cohort packages         = {len(rows)}")
    out_lines.append(f"real source paths       = {len(real)}")
    out_lines.append(f"missing paths           = {len(missing)}" +
                     (f"  -> {missing}" if missing else ""))
    out_lines.append(f"production sources      = title_final({len(resolver.title)}) "
                     f"desc_final({len(resolver.desc)}) overlay({len(resolver.overlay)})" +
                     (f" catalog({len(resolver.catalog)})" if resolver.catalog else ""))
    out_lines.append(f"禁止源黑名单           = {'ACTIVE (batch/retry/cache 拒收)' if any(Path(p).name in BANNED_PRODUCTION_SOURCE for p in [a.title_final,a.desc_final,a.production_overlay,a.catalog]) else '无命中'}")

    cohort_fail = bool(missing) or len(rows) != 10 or len(set(paths)) != 10 or bool(non_eligible)
    if cohort_fail:
        out_lines.append("[HARD-FAIL] cohort 预检失败: rows != 10 或 有 missing/nonEligible/重复 path")
        print("\n".join(out_lines)); return 2

    # ---- out-dir stale 守卫 ----
    if a.out_dir:
        od = Path(a.out_dir)
        if od.exists() and any(od.iterdir()):
            out_lines.append(f"[HARD-FAIL] out-dir 非空 (stale refuse): {od}")
            print("\n".join(out_lines)); return 2

    # ---- 2) 每包 per-package 预检 ----
    agg = {"approved": 0, "keep": 0, "translate": 0, "unresolved": 0,
           "source_mismatch": 0, "policy_conflict": 0, "dup_keyhash": 0,
           "tgi_gate": 0, "pack_fail": 0, "pack_pass": 0}
    out_lines.append("-" * 64)
    out_lines.append("per-package:")
    out_lines.append(f"{'slot':>4} {'pkg':<32} {'aprv':>4} {'KEEP':>4} {'TR':>4} "
                     f"{'unres':>5} {'mism':>5} {'dupKH':>5} {'tgi':>3}  result")
    pack_rows = []
    for r in rows:
        slot = r.get(_COH_SLOT, "").strip()
        path = r.get(_COH_PATH, "").strip()
        per = {"slot": slot, "pkg": Path(path).name, "approved": 0, "keep": 0,
               "translate": 0, "unresolved": 0, "source_mismatch": 0,
               "dup_keyhash": 0, "tgi": "OK", "result": "PASS", "errors": []}
        try:
            target_tgi, key_map, approved, errs = approved_pv_refs(path)
        except Exception as ex:
            per["result"] = "FAIL"; per["errors"].append(f"mapping 异常: {ex}"); pack_rows.append(per); continue
        if errs:
            # 区分 duplicate KeyHash / TGI gate
            duperrs = [e for e in errs if "重复 KeyHash" in e or "重复" in e]
            tgierrs = [e for e in errs if "数 != 1" in e or "CHS 目标" in e or "no 0x01" in e or "无 0x01" in e]
            per["errors"].extend(errs)
            if duperrs:
                per["dup_keyhash"] = len(duperrs); agg["dup_keyhash"] += len(duperrs)
            if tgierrs or (not duperrs):
                per["tgi"] = "FAIL"
            per["result"] = "FAIL"; agg["pack_fail"] += 1
            pack_rows.append(per); continue
        per["approved"] = len(approved); agg["approved"] += len(approved)
        mods, keeps, errs2 = resolve_all_approved(approved, resolver, a.production_overlay)
        per["keep"] = len(keeps); agg["keep"] += len(keeps)
        per["translate"] = len(mods); agg["translate"] += len(mods)
        per["unresolved"] = sum(1 for e in errs2 if "缺译文/unresolved" in e)
        per["source_mismatch"] = sum(1 for e in errs2 if "source mismatch" in e)
        per["errors"] = errs2
        agg["unresolved"] += per["unresolved"]
        agg["source_mismatch"] += per["source_mismatch"]
        if per["keep"] + per["translate"] + per["unresolved"] + per["source_mismatch"] != per["approved"]:
            per["policy_conflict"] = 1; agg["policy_conflict"] += 1
            per["result"] = "FAIL"
        elif per["unresolved"] or per["source_mismatch"]:
            per["result"] = "FAIL"; agg["pack_fail"] += 1
        else:
            per["result"] = "PASS"; agg["pack_pass"] += 1
        pack_rows.append(per)

    for per in pack_rows:
        out_lines.append(
            f"{per['slot']:>4} {per['pkg']:<32} {per['approved']:>4} {per['keep']:>4} "
            f"{per['translate']:>4} {per['unresolved']:>5} {per['source_mismatch']:>5} "
            f"{per['dup_keyhash']:>5} {per['tgi']:>3}  {per['result']}"
            + (f"  ERR={';'.join(per['errors'][:2])}" if per["errors"] else ""))

    # ---- 3) 历史说明 (不进入 machine-verifiable PASS 条件) ----
    # 历史 run1: 1 PASS + 9 MISSING (见 PHASE3B-SIDECAR.md 设计注记 C-class)。
    # 仓库内无可机读历史 run1 artifact 记录具体哪 9 包/哪 9 tid, 故不构造/不反推。
    # 此说明仅作文档, 不参与 fail-closed 判定。
    out_lines.append("-" * 64)
    out_lines.append("historical note: run1 had 1 PASS + 9 MISSING (文档注记, 不进入 PASS 条件)")
    out_lines.append("run2 preflight 仅证明当前 10 个 cohort package 的 resolve 一致性。")

    # ---- 4) aggregate ----
    out_lines.append("-" * 64)
    out_lines.append("aggregate:")
    out_lines.append(f"  approved entries      = {agg['approved']}")
    out_lines.append(f"  KEEP                  = {agg['keep']}")
    out_lines.append(f"  TRANSLATE             = {agg['translate']}")
    out_lines.append(f"  unresolved            = {agg['unresolved']}")
    out_lines.append(f"  source mismatch       = {agg['source_mismatch']}")
    out_lines.append(f"  policy conflict       = {agg['policy_conflict']}")
    out_lines.append(f"  duplicate target skip = frozen rule (0 新 dup)")
    out_lines.append(f"  packages PASS/FAIL    = {agg['pack_pass']}/{agg['pack_fail']}")

    # 结论
    ok = (len(rows) == 10 and not missing and agg["unresolved"] == 0
          and agg["source_mismatch"] == 0 and agg["policy_conflict"] == 0
          and agg["pack_fail"] == 0 and not resolver.has_consistency_errors())
    out_lines.append("-" * 64)
    out_lines.append(f"PREFLIGHT: {'PASS' if ok else 'FAIL'} "
                     f"({'所有 10 包可安全生成' if ok else '存在阻塞, 不生成 sidecar'})")
    print("\n".join(out_lines))

    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        with open(a.report, "w", encoding="utf-8") as f:
            f.write("\n".join(out_lines) + "\n")
        print(f"\n[report] 已写出: {a.report}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
