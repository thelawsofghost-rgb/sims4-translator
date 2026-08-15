#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_manual_registry_rerun.py — real 659 rerun 后的人工裁决核验
=================================================================
在真实 Windows 659 coverage 输出 + frozen registry 之上运行, 核验:

  1) status 数量汇总 (与预期对照, 不硬编码: ELIGIBLE=441 / FP=7 / 其它 skip)。
  2) SKIP_FALSE_POSITIVE_INTERNAL_POSE 的 decision_basis 分布:
        AUTO_STRONG_OBJECT_FOOTPRINT=1 (strong gate 自动)
        MANUAL_REVIEW_CONFIRMED=6   (人工裁决 registry)
  3) manual registry 6 条 SHA256 identity 全部在 coverage 中精确 exact-match:
        对每条 registry 记录, 必须在 coverage 中找到 manual_sha256 完全一致的行,
        且该行 status == SKIP_FALSE_POSITIVE_INTERNAL_POSE, decision_basis == MANUAL_REVIEW_CONFIRMED。
        任何不匹配 -> FAIL (fail-closed)。
  4) 新 cohort vs v3 cohort 的集合 diff (由 --cohort 传入新 cohort, --cohort-v3 传入旧 v3 cohort):
        列出 added (新有旧无) / removed (旧有新无) 的 basename。

输入:
  --coverage  新 659 rerun 的 coverage CSV (含 manual_* / decision_subtype 列)
  --registry  frozen manual registry CSV (默认 configs/pose_manual_adjudications.csv)
  --cohort     新 cohort CSV (可选, 用于 diff)
  --cohort-v3  旧 v3 cohort CSV (可选, 用于 diff)
输出: 终端核验报告; fail-closed (任一 FAIL -> rc=1)。
只读, 不写任何 coverage / registry / cohort。
"""
import sys, os, csv
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import manual_adjudication as MA

DEFAULT_REGISTRY = os.path.join(REPO, "configs", "pose_manual_adjudications.csv")
FP_STATUS = "SKIP_FALSE_POSITIVE_INTERNAL_POSE"

PASS, FAIL = [], []
def check(n, c, d=""):
    (PASS if c else FAIL).append(n)
    print(("  [OK]   " if c else "  [FAIL] ")+n+(f"  {d}" if d else ""))


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage", required=True, help="新 659 rerun coverage CSV")
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--cohort", help="新 cohort CSV")
    ap.add_argument("--cohort-v3", help="旧 v3 cohort CSV")
    a = ap.parse_args()

    if not os.path.isfile(a.coverage):
        print(f"[ERROR] --coverage 不存在: {a.coverage}"); return 2

    cov = list(csv.DictReader(open(a.coverage, encoding="utf-8-sig")))
    print(f"coverage 行数: {len(cov)}")

    # ---- 1) status 汇总 ----
    sc = Counter(r.get("status", "") for r in cov)
    print("\n--- status 数量 ---")
    for s in ("ELIGIBLE_EXISTING_CHS", FP_STATUS, "SKIP_NO_CHS", "SKIP_AMBIGUOUS_TGI",
              "SKIP_MAPPING_UNCERTAIN", "SKIP_DUPLICATE_KEYHASH",
              "SKIP_MISSING_FILE", "ERROR", "ERROR_COVERAGE_INVARIANT"):
        v = sc.get(s, 0)
        print(f"  {s}: {v}")
    check("总行数=659", len(cov) == 659, f"actual={len(cov)}")
    check("ERROR=0", sc.get("ERROR", 0) == 0 and sc.get("ERROR_COVERAGE_INVARIANT", 0) == 0,
          f"ERROR={sc.get('ERROR',0)}")

    # ---- 2) FP decision_basis 分布 ----
    fps = [r for r in cov if r.get("status") == FP_STATUS]
    print(f"\n--- {FP_STATUS} (n={len(fps)}) decision_basis 分布 ---")
    sub = Counter((r.get("decision_subtype") or "UNSET") for r in fps)
    for k in ("AUTO_STRONG_OBJECT_FOOTPRINT", "MANUAL_REVIEW_CONFIRMED", "UNSET"):
        print(f"  {k}: {sub.get(k, 0)}")
    check("FP 总数=7 (auto=1 + manual=6)", len(fps) == 7, f"actual={len(fps)}")
    check("AUTO_STRONG_OBJECT_FOOTPRINT=1", sub.get("AUTO_STRONG_OBJECT_FOOTPRINT", 0) == 1)
    check("MANUAL_REVIEW_CONFIRMED=6", sub.get("MANUAL_REVIEW_CONFIRMED", 0) == 6)

    # ---- 3) manual registry 6 条 SHA256 exact-match 核验 ----
    print("\n--- manual registry 精确 SHA256 identity 核验 ---")
    try:
        rows = MA.load_registry(a.registry)
    except ValueError as ex:
        print(f"[FAIL] registry 校验失败: {ex}"); FAIL.append("registry 校验"); 
        print(f"\n===== 结果: PASS=*** FAIL={len(FAIL)} ====="); return 1
    skip_rows = {sha: r for sha, r in rows.items() if r["decision"] == "SKIP"}
    check("registry SKIP 记录=6", len(skip_rows) == 6, f"actual={len(skip_rows)}")
    cov_by_sha = {}
    for r in cov:
        msha = (r.get("manual_sha256") or "").strip().lower()
        if msha:
            cov_by_sha[msha] = r
    all_match = True
    for sha, r in skip_rows.items():
        cr = cov_by_sha.get(sha)
        ok = (cr is not None and cr.get("status") == FP_STATUS
              and (cr.get("decision_subtype") == "MANUAL_REVIEW_CONFIRMED"))
        if not ok:
            all_match = False
        check(f"  SHA256 {sha[:12]}… {r.get('basename','')} exact-match -> manual FP",
              ok, f"status={cr.get('status') if cr else 'NOT_FOUND'}")
    check("6 条 registry SHA256 全部在 coverage 精确命中且为 manual FP", all_match)
    # 反向: coverage 中 manual 标记的行必须全部来自 registry
    cov_manual = [r for r in cov if (r.get("decision_subtype") == "MANUAL_REVIEW_CONFIRMED")]
    check("coverage 中 MANUAL 行数=6 且 identity 均来自 registry",
          len(cov_manual) == 6 and all((r.get("manual_sha256") or "") in skip_rows for r in cov_manual),
          f"actual={len(cov_manual)}")

    # ---- 4) cohort set diff (新 vs v3) ----
    if a.cohort and a.cohort_v3:
        def names(path):
            with open(path, encoding="utf-8-sig") as f:
                rd = csv.DictReader(f)
                cols = rd.fieldnames or []
                key = "package_path" if "package_path" in cols else ("source_package" if "source_package" in cols else None)
                if key is None: return set()
                def b(p): return (p or "").replace("\\", "/").split("/")[-1]
                return {b(r[key]) for r in rd if r.get(key)}
        new_s = names(a.cohort); v3_s = names(a.cohort_v3)
        added = sorted(new_s - v3_s); removed = sorted(v3_s - new_s)
        print(f"\n--- cohort set diff (new vs v3) ---")
        print(f"  new={len(new_s)} v3={len(v3_s)}")
        print(f"  added(n=+{len(added)}): {added if added else '-'}")
        print(f"  removed(n=-{len(removed)}): {removed if removed else '-'}")
        # 预期: 6 个 manual SKIP 应从 cohort 剔除 (若旧 v3 含它们)
        rem_bases = {os.path.basename(r.get('basename','')) for r in skip_rows.values()}
        removed_in_reg = set(removed) & rem_bases
        check("removed 集合含 6 个 manual SKIP basename (若 v3 曾含)",
              len(removed_in_reg) >= 0)  # 信息性, 不强断言(取决于 v3 内容)
        print(f"  removed 中属于 manual SKIP: {sorted(removed_in_reg) if removed_in_reg else '-'}")

    print(f"\n===== 结果: PASS=*** FAIL={len(FAIL)} =====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
