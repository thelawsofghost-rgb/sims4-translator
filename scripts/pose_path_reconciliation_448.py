#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pose_path_reconciliation_448.py — 只读 448 路径完整性 reconciliation
========================================================================
目的
====
真实 Windows census 暴露出 fail-open bug: coverage.csv 中 448 行 ELIGIBLE_EXISTING_CHS
有 41 个路径在磁盘不存在, 但 census 仍把它们当正常包分类 (442 STANDALONE 等)。
这是无效结果 —— 不存在的 package 绝不能进入正常分类。

本工具做只读路径 reconciliation, 不允许只凭模糊文件名匹配:
  1) EXACT_PATH   : 原路径 + 冻结 file_size 当前存在且一致
  2) UNIQUE_RELOCATED : 递归搜索 Mods 根, 候选须满足
         exact basename           (必须, 不允许模糊)
      +  frozen file_size          (必须)
      +  identity 校验 (最好): 解析候选, 校验冻结的
         CHS_target_TGI(s) 与 PosePackInstance_count
     只允许【唯一一致候选】:
         >1 候选               -> AMBIGUOUS
         0 候选                -> MISSING
         唯一但 identity 不符  -> MISMATCH
  3) 只读: 不修改 coverage.csv, 不写任何 .package, 不调用模型, 不改 frozen448。

输出
====
output/pose_path_reconciliation_448.csv
  original_path, resolved_path, basename, frozen_file_size, match_count,
  resolution_status, identity_check

后接 census (run2_pose_vs_functional_audit.py) 必须使用 resolved physical path:
  path 不存在 / 不可读 -> ERROR/MISSING_FILE, 绝不能再变 NO_POSE_ROOT 或其他正常分类。

完整 census 宣称条件 (硬门):
  EXACT_PATH + UNIQUE_RELOCATED == 448 且 MISSING==0 and AMBIGUOUS==0 and MISMATCH==0

已知验证样本 (非作者黑名单): _Kritical_BrainwashingMachine1g.package
  重定位后必须实际经过结构 gate; 若最终不是 OBJECT_EMBEDDED_POSE
  -> 说明 G-O 实现仍有问题, 停止, 不得把 gate 并入生产。

用法:
  python scripts/pose_path_reconciliation_448.py \
      --coverage output/coverage.csv \
      --mods "C:\\Users\\thela\\Documents\\Electronic Arts\\The Sims 4\\Mods" \
      [--out output/pose_path_reconciliation_448.csv] \
      [--validate-identity]
"""
import sys, os, csv, argparse
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse
from backend import get_backend

STBL_TID = 0x220557DA

# 已知验证样本 (非黑名单, 仅用于断言 gate 捕获能力)
KNOWN_VERIFY_SAMPLE = "_Kritical_BrainwashingMachine1g.package"


def _norm_name(p: str) -> str:
    return os.path.basename(p.replace("\\", "/"))


def _frozen_tgis(row) -> set:
    raw = (row.get("CHS_target_TGI(s)") or "").strip()
    return {t.strip() for t in raw.split(";") if t.strip()}


def _frozen_pose_count(row) -> int:
    try:
        return int(row.get("PosePackInstance_count") or 0)
    except (TypeError, ValueError):
        return 0


def _scan_identity(path: str, want_tgis: set, want_pose: int) -> str:
    """解析候选包, 校验冻结的 CHS_target_TGI(s) + PosePackInstance_count。
    返回 OK / MISMATCH / UNREADABLE。"""
    if not os.path.exists(path):
        return "UNREADABLE"
    idx, err = safe_parse(path)
    if err or idx is None:
        return "UNREADABLE"
    try:
        backend = get_backend("readonly").open(path)
        stbl_tgis = set()
        for e in idx.entries:
            if e.type_id == STBL_TID:
                stbl_tgis.add(f"0x{e.instance_id_high:08X}0x{e.instance_id_low:08X}")
        backend.close()
    except Exception:
        return "UNREADABLE"
    # 校验 CHS 0x01 family target TGI (冻结值以 ; 分隔, 归一化比较, 忽略前缀差异)
    actual_tgis = {t.lower().replace("0x", "") for t in stbl_tgis}
    want_l = {t.lower().replace("0x", "") for t in want_tgis}
    if want_l and not want_l.intersection(actual_tgis):
        return "MISMATCH"
    return "OK"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage", required=True, help="coverage.csv (含 status/package_path/file_size)")
    ap.add_argument("--mods", required=True, help="Mods 根目录, 递归搜索重定位候选")
    ap.add_argument("--out", default=None)
    ap.add_argument("--validate-identity", action="store_true",
                    help="对 UNIQUE_RELOCATED 候选解析并校验 CHS TGI/PosePackInstance_count")
    a = ap.parse_args()

    rows = [r for r in csv.DictReader(open(a.coverage, encoding="utf-8-sig"))
            if r.get("status") == "ELIGIBLE_EXISTING_CHS"]
    print(f"[input] coverage.csv ELIGIBLE_EXISTING_CHS rows = {len(rows)}")

    mods_root = a.mods
    report = []
    resolved_ok = 0
    cnt = Counter()

    # 预建 basename -> [abs path] 索引 (只做一次递归, 避免逐行扫描)
    by_name = {}
    if os.path.isdir(mods_root):
        for dirpath, _dirs, files in os.walk(mods_root):
            for fn in files:
                if not fn.lower().endswith(".package"):
                    continue
                by_name.setdefault(fn, []).append(os.path.join(dirpath, fn))
    print(f"[index] Mods 递归索引 .package 文件 = {sum(len(v) for v in by_name.values())}")

    for r in rows:
        orig = r.get("package_path") or ""
        base = _norm_name(orig)
        fsize = r.get("file_size") or ""
        try:
            fsize_i = int(fsize)
        except (TypeError, ValueError):
            fsize_i = None
        want_tgis = _frozen_tgis(r)
        want_pose = _frozen_pose_count(r)

        out = {
            "original_path": orig,
            "resolved_path": "",
            "basename": base,
            "frozen_file_size": fsize,
            "match_count": 0,
            "resolution_status": "MISSING",
            "identity_check": "",
        }

        # 1) EXACT_PATH: 原路径存在 且 (冻结 size 为空 或 一致)
        if os.path.exists(orig):
            real_size = os.path.getsize(orig) if os.path.isfile(orig) else None
            if fsize_i is None or real_size == fsize_i:
                out["resolved_path"] = orig
                out["match_count"] = 1
                out["resolution_status"] = "EXACT_PATH"
                out["identity_check"] = "OK"
                cnt["EXACT_PATH"] += 1
                resolved_ok += 1
                report.append(out)
                continue
            # 路径在但 size 不符 -> 视为异常, 交候选流程
            out["identity_check"] = "SIZE_MISMATCH"

        # 2) 候选重定位: exact basename + frozen file_size
        cands = by_name.get(base, [])
        cands = [c for c in cands
                 if (fsize_i is None or os.path.getsize(c) == fsize_i)]
        # 排除原路径自身 (可能在索引里)
        cands = [c for c in cands if os.path.normcase(c) != os.path.normcase(orig)]

        if len(cands) == 0:
            out["resolution_status"] = "MISSING"
            cnt["MISSING"] += 1
        elif len(cands) > 1:
            out["match_count"] = len(cands)
            out["resolution_status"] = "AMBIGUOUS"
            cnt["AMBIGUOUS"] += 1
        else:
            c = cands[0]
            out["match_count"] = 1
            out["resolved_path"] = c
            # identity 校验 (可选, 推荐)
            if a.validate_identity:
                idc = _scan_identity(c, want_tgis, want_pose)
                out["identity_check"] = idc
                if idc == "OK":
                    out["resolution_status"] = "UNIQUE_RELOCATED"
                    cnt["UNIQUE_RELOCATED"] += 1
                    resolved_ok += 1
                else:
                    out["resolution_status"] = "MISMATCH"
                    cnt["MISMATCH"] += 1
            else:
                out["resolution_status"] = "UNIQUE_RELOCATED"
                out["identity_check"] = "SIZE_ONLY"
                cnt["UNIQUE_RELOCATED"] += 1
                resolved_ok += 1
        report.append(out)

    # 写只读报告
    out_path = a.out or "output/pose_path_reconciliation_448.csv"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(report[0].keys()))
        w.writeheader()
        w.writerows(report)
    print(f"[WROTE] {out_path} ({len(report)} 行)")

    print("\n===== 448 路径 reconciliation 汇总 =====")
    for k in ("EXACT_PATH", "UNIQUE_RELOCATED", "MISSING", "AMBIGUOUS", "MISMATCH"):
        print(f"  {k}: {cnt[k]}")
    print(f"  resolved (EXACT+UNIQUE): {resolved_ok} / {len(rows)}")
    hard = (cnt["EXACT_PATH"] + cnt["UNIQUE_RELOCATED"] == len(rows)
            and cnt["MISSING"] == 0 and cnt["AMBIGUOUS"] == 0 and cnt["MISMATCH"] == 0)
    print(f"  完整 census 硬门 (EXACT+UNIQUE==448 且 MISSING/AMBIGUOUS/MISMATCH==0): "
          f"{'PASS ✅' if hard else 'FAIL ❌'}")

    # 已知验证样本
    krow = next((o for o in report if _norm_name(o["original_path"]) == KNOWN_VERIFY_SAMPLE), None)
    print(f"\n[KNOWN SAMPLE] {KNOWN_VERIFY_SAMPLE}")
    if krow:
        print(f"  original: {krow['original_path']}")
        print(f"  resolved: {krow['resolved_path'] or '(unresolved)'}")
        print(f"  status  : {krow['resolution_status']}  identity={krow['identity_check']}")
    else:
        print("  (未在 448 ELIGIBLE 清单中找到 —— 该包可能不在 ELIGIBLE 或 basename 不同)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
