#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run2_preflight.py —— Phase 3B2-SIDECAR run2 resolver/production-input 零写 preflight
=========================================================================================
目的: 在生成任何 sidecar 之前, 证明 gen_cohort_sidecars 的 resolver 在 run2 中使用的是
冻结的五份 production source, 并生成 zero-write preflight report
(不建 package, 不碰 package, 不写 sidecar, 不改 Mods)。

production source (preflight 与 generation 必须使用完全相同):
  production_overlay  = 241   (explicit latest terminal override, 最高权威)
  title_final         = 407   (新批次 final)
  desc_final          = 190   (新批次 final)
  translation_done    = 1888  (historical final translation fallback, nonempty unique)
  translation_catalog = 3540  (decision/index ONLY, 不是 final payload)

已退出 resolver (只作 provenance/audit, base114 已被 production overlay241 完整包含;
历史 217 为 run2 KEEP24 冻结前快照, 属 provenance):
  translation_overrides.csv (base114), translation_overrides.final2.csv

禁止作 final payload: catalog.translation, translation_cache.db。

报告结构:
  1) production source health:
       overlay rows = 241 / title rows = 407 / desc rows = 190
       historical done nonempty unique = 1888 / catalog rows = 3540
       title_desc_overlap = ? / title_desc_conflict = 0
       historical_superseded = ? / catalog_decision_superseded = ?
  2) 10 个真实 cohort:
       packages = 10 / real paths = 10 / missing paths = 0
       每包: approved / KEEP / TRANSLATE / unresolved / source_mismatch /
             duplicate_KeyHash / CHS_TGI
  3) aggregate:
       unresolved = 0 / source_mismatch = 0 / policy_conflict = 0
       duplicate_KeyHash_violation = 0 / CHS_TGI_violation = 0

fail-closed (真正 HARD-FAIL):
  * 五份 production source 缺文件 / 0 行 / 行数 != 冻结值 -> HARD-FAIL
  * 任一 production source 是禁止的 base114/final2/batch/retry/cache 文件 -> HARD-FAIL
  * source_text mismatch、final source 内部 duplicate key 且 outcome 不同、
    title_final vs desc_final 同 key outcome 不一致 -> HARD-FAIL
  * 当前 cohort catalog TRANSLATE 最终无 payload / REVIEW 无更高层终态 -> 该包 FAIL -> rc
  * out-dir (若给) stale 非空 -> HARD-FAIL (与 gen_cohort_sidecars 一致)

superseded (合法历史修订, 仅计数, 不 HARD-FAIL):
  historical_superseded / catalog_decision_superseded

零写保证: 本脚本不调用 writer、不创建任何 .package / manifest / sidecar / out-dir;
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
                    help="output/translation_overrides.production.csv (241)")
    ap.add_argument("--done", default="", help="output/translation_done.csv (historical final, nonempty unique 1888)")
    ap.add_argument("--catalog", default="", help="output/translation_catalog.csv (decision/index only 3540)")
    ap.add_argument("--report", default="", help="显式写出纯文本 preflight report (缺省只打印)")
    ap.add_argument("--out-dir", default="", help="若提供, 校验 stale 非空(与 gen_cohort_sidecars 一致)")
    a = ap.parse_args()

    # 必须给 done + catalog (五源完整)
    for p, lab in [(a.title_final, "title_final"), (a.desc_final, "desc_final"),
                   (a.production_overlay, "production_overlay"), (a.done, "translation_done"),
                   (a.catalog, "translation_catalog"), (a.cohort, "cohort")]:
        if not Path(p).exists():
            print(f"[HARD-FAIL] {lab} 文件不存在: {p}"); return 3

    # ---- 0) 禁止源守卫 + production resolver (五源一致性) ----
    try:
        resolver = make_production_resolver(
            a.title_final, a.desc_final, a.production_overlay,
            translation_done=a.done, translation_catalog=a.catalog)
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

    # ---- production source health ----
    ov_keys = set(resolver.overlay.keys())
    tf_keys = set(resolver.title.keys())
    df_keys = set(resolver.desc.keys())
    td_overlap = len(tf_keys & df_keys)
    td_conflict = sum(1 for k in (tf_keys & df_keys)
                      if resolver.title[k]["translation"] != resolver.desc[k]["translation"])
    out_lines.append("production source health:")
    out_lines.append(f"  overlay rows                     = {len(resolver.overlay)}  (期望 {EXPECTED_ROWS['production_overlay']})")
    out_lines.append(f"  title rows                       = {len(resolver.title)}  (期望 407)")
    out_lines.append(f"  desc rows                        = {len(resolver.desc)}  (期望 190)")
    out_lines.append(f"  historical done nonempty unique  = {len(resolver.done)}  (期望 1888)")
    out_lines.append(f"  catalog rows                     = {len(resolver.catalog)}  (期望 3540)")
    out_lines.append(f"  title_desc_overlap               = {td_overlap}")
    out_lines.append(f"  title_desc_conflict              = {td_conflict}")
    out_lines.append(f"  historical_superseded            = {resolver.historical_superseded}  (仅计数, 不 HARD-FAIL)")
    out_lines.append(f"  catalog_decision_superseded      = {resolver.catalog_decision_superseded}  (仅计数, 不 HARD-FAIL)")
    out_lines.append(f"  consistency_errors               = {len(resolver.consistency_errors)}")
    banneds = [Path(p).name for p in [a.title_final, a.desc_final, a.production_overlay, a.done, a.catalog]
               if Path(p).name in BANNED_PRODUCTION_SOURCE]
    out_lines.append(f"  禁止源黑名单命中                  = {banneds if banneds else '无'}")

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
    out_lines.append("cohort:")
    out_lines.append(f"  packages = {len(rows)} / real paths = {len(real)} / missing paths = {len(missing)}")
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

    # ---- aggregate ----
    out_lines.append("-" * 64)
    out_lines.append("aggregate:")
    out_lines.append(f"  approved entries              = {agg['approved']}")
    out_lines.append(f"  KEEP                          = {agg['keep']}")
    out_lines.append(f"  TRANSLATE                     = {agg['translate']}")
    out_lines.append(f"  unresolved                    = {agg['unresolved']}")
    out_lines.append(f"  source_mismatch               = {agg['source_mismatch']}")
    out_lines.append(f"  policy_conflict               = {agg['policy_conflict']}")
    out_lines.append(f"  duplicate_KeyHash_violation   = {agg['dup_keyhash']}")
    out_lines.append(f"  CHS_TGI_violation             = {1 if agg['tgi_gate'] else 0}")
    out_lines.append(f"  packages PASS/FAIL            = {agg['pack_pass']}/{agg['pack_fail']}")

    # 结论
    ok = (len(rows) == 10 and not missing
          and agg["unresolved"] == 0 and agg["source_mismatch"] == 0
          and agg["policy_conflict"] == 0 and agg["dup_keyhash"] == 0
          and agg["pack_fail"] == 0 and not resolver.has_consistency_errors()
          and td_conflict == 0)
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
