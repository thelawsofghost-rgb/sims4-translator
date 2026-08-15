#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
source_exactness_audit_441.py —— 全 441 只读 source 精确性 census (zero-write)

背景 (2026-08-16 实机):
  Pose Production Generation 出现 6 个 writer expected-text precondition FAIL,
  模式统一为 "expected=<源码首冒号前> actual=<真实 STBL 全串>" (如 expected \"Need\" /
  actual \"Need: [MURPHY] iPhone 13 Pro Max (hat, LEFT)\")。
  根因在 writer -m KEYHASH:EXPECTED:VALUE 的冒号定界契约 (C# IndexOf 取最前两个冒号,
  无转义) 与含冒号的 pose 源文本冲突 —— 非数据截断, 而是 CL 序列化边界缺口。

本脚本: 只读, 不 generation / 不改任何文件 / 不改 overlay / 不动 432 个成功 sidecar。
对 coverage 中全部 441 个包, 逐 approved key:
  * plan source      = approved_pv_refs 产出的 source_text (即 generation plan 的 expected)
  * actual source    = 独立重读 exact CHS STBL (dbpf_fast.safe_parse + audit_sidecar.read_stbl_entries)
  * 逐字节 exact 比较 plan_source == actual_source
  * source_has_colon = plan_source 是否含 ':'  (writer 契约 at-risk 超集)
  * at_risk          = not exact_match 或 source_has_colon

输出:
  output/source_exactness_audit_441.csv  每 approved key 一行
  终局汇总: exact_match / mismatch / mismatch_packages / mismatch_keys
            + colon_count / at_risk_count
  rc: mismatch>0 -> 1 (仅汇总, 仍写出 CSV); colon>0 仅提示。0 = 全 exact 匹配。

用法 (Windows, 真实数据):
  python scripts\\source_exactness_audit_441.py ^
      --coverage output\\coverage_manual_adj.csv ^
      --out output\\source_exactness_audit_441.csv
"""
import sys, os, csv, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gen_cohort_sidecars import approved_pv_refs          # plan source (player-visible gated)
from dbpf_fast import safe_parse
import audit_sidecar as A                                 # 独立 canonical STBL reader


_OUT_COLS = [
    "package_path",
    "role",
    "key_hash",
    "plan_source",
    "actual_source",
    "exact_match",        # Y/N  plan_source == actual_source (逐字节)
    "source_len",
    "source_has_colon",   # Y/N  colon 超集 (writer -m 冒号定界 at-risk)
    "writer_expected",    # C# 取最前两个冒号 -> EXPECTED (源首冒号前), 空则为全源
    "writer_value",       # C# 第2冒号后 -> VALUE (含源冒号时会错位)
    "writer_would_fail",  # Y/N  source_has_colon 且 plan==actual (真机上必然 precondition FAIL)
    "at_risk",            # Y/N  not exact_match OR source_has_colon
]


def _writer_split(src):
    """镜像 SidecarBuilder C# IndexOf 前两个冒号切分。返回 (expected, value)。"""
    if src is None:
        return None, None
    c1 = src.find(':')
    if c1 < 0:
        return src, None
    c2 = src.find(':', c1 + 1)
    if c2 < 0:
        return src[:c1], src[c1 + 1:]
    return src[:c1], src[c2 + 1:]


def indep_actual_chs_stbl(path):
    """独立重读 exact CHS (0x01 locale) STBL, 返回 {kh:(flags,text)} 或 None/err。"""
    idx, err = safe_parse(path)
    if err or idx is None:
        return None, f"DBPF 解析失败: {err}"
    for e in idx.entries:
        if e.type_id == A.STBL_TID and ((e.instance_id >> 56) & 0xFF) == 0x01:
            cnt, keys = A.read_stbl_entries(path, e, idx)
            if cnt is None:
                return None, "独立 STBL 读取失败"
            return {kh: (fl, txt) for kh, fl, txt in keys}, None
    return None, "包内无独立 0x01 CHS STBL"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage", required=True, help="output/coverage_manual_adj.csv (441 ELIGIBLE)")
    ap.add_argument("--out", default="output/source_exactness_audit_441.csv")
    ap.add_argument("--expect-eligible", type=int, default=441,
                    help="hard 门: coverage 里 ELIGIBLE_EXISTING_CHS 数 (production 冻结 441)")
    a = ap.parse_args()

    cov = []
    with open(a.coverage, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            cov.append(r)
    elig = [r for r in cov if (r.get("status") or "").strip() == "ELIGIBLE_EXISTING_CHS"]
    if len(elig) != a.expect_eligible:
        print(f"[HARD-FAIL] ELIGIBLE_EXISTING_CHS = {len(elig)} != {a.expect_eligible} → 不 census")
        return 2

    # 预探测 coverage 列名 (package path 列)
    cols = list(cov[0].keys()) if cov else []
    path_col = next((c for c in cols if c.lower() in ("package_path", "path", "package")), None)
    if path_col is None:
        print(f"[HARD-FAIL] coverage 无 package 路径列; 列={cols}")
        return 2

    rows = []
    stat = {"packages": 0, "approved_keys": 0, "exact_match": 0, "mismatch": 0,
            "colon": 0, "at_risk": 0}
    mismatch_pkgs = []
    mismatch_keys = []
    for r in elig:
        path = (r.get(path_col) or "").strip()
        stat["packages"] += 1
        if not os.path.exists(path):
            # 真实路径缺失 → 视为无法独立核对; 单独标注 exact_match=NA 不计 mismatch
            rows.append({"package_path": path, "role": "", "key_hash": "",
                         "plan_source": "", "actual_source": "", "exact_match": "NA",
                         "source_len": 0, "source_has_colon": "", "at_risk": "?"})
            continue
        try:
            tgi, key_map, approved, errs = approved_pv_refs(path)
        except Exception as ex:
            rows.append({"package_path": path, "role": "", "key_hash": "",
                         "plan_source": "", "actual_source": "", "exact_match": "ERR",
                         "source_len": 0, "source_has_colon": "", "at_risk": "?",
                         })
            continue
        if approved is None:
            continue
        act, aerr = indep_actual_chs_stbl(path)
        for cat, kh, plan_src in approved:
            stat["approved_keys"] += 1
            actual = "NA"
            if act is not None:
                hit = act.get(kh)
                actual = hit[1] if hit else "<absent>"
            exact = (plan_src == actual)
            has_colon = (plan_src is not None and ":" in plan_src)
            w_exp, w_val = _writer_split(plan_src)
            wf = ("Y" if (has_colon and exact and actual != "NA") else "N")
            at_risk = (not exact) or has_colon
            stat["exact_match"] += (1 if exact else 0)
            stat["mismatch"] += (0 if exact else 1)
            stat["colon"] += (1 if has_colon else 0)
            stat["at_risk"] += (1 if at_risk else 0)
            stat["writer_would_fail"] = stat.get("writer_would_fail", 0) + (1 if wf == "Y" else 0)
            if not exact:
                if path not in mismatch_pkgs:
                    mismatch_pkgs.append(path)
                mismatch_keys.append((os.path.basename(path), cat, "0x%08X" % kh,
                                      repr(plan_src), repr(actual)))
            rows.append({
                "package_path": path, "role": cat, "key_hash": "0x%08X" % kh,
                "plan_source": plan_src, "actual_source": actual,
                "exact_match": ("Y" if exact else "N"),
                "source_len": len(plan_src) if plan_src is not None else 0,
                "source_has_colon": ("Y" if has_colon else "N"),
                "writer_expected": w_exp, "writer_value": w_val,
                "writer_would_fail": wf,
                "at_risk": ("Y" if at_risk else "N"),
            })

    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=_OUT_COLS)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print("=== source exactness census (441, read-only) ===")
    print(f"packages            = {stat['packages']}")
    print(f"approved keys       = {stat['approved_keys']}")
    print(f"exact_match         = {stat['exact_match']}")
    print(f"mismatch            = {stat['mismatch']}")
    print(f"source_has_colon    = {stat['colon']}   (writer -m 冒号契约 at-risk 超集)")
    print(f"writer_would_fail   = {stat.get('writer_would_fail', 0)}   (含冒号且 plan==actual → 真机必然 precondition FAIL)")
    print(f"at_risk             = {stat['at_risk']}   (mismatch 或含冒号)")
    print(f"mismatch packages   = {len(set(mismatch_pkgs))}")
    print(f"mismatch keys       = {len(mismatch_keys)}")
    print(f"\nCSV: {a.out}")
    if mismatch_keys:
        print("\n--- mismatch keys ---")
        for b, cat, kh, ps, act in mismatch_keys:
            print(f"  {b} | {cat} | {kh}\n      plan  ={ps}\n      actual={act}")
    return 1 if stat["mismatch"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
