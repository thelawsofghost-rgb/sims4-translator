#!/usr/bin/env python3
"""确定性生成 output/run1_missing_tids.txt — run1 的 9 个 MISSING tid。

run1 的 9 MISSING = 10-cohort 包内 approved player-visible key 中,
run1 resolver (old base overrides + done + cache) 无法 resolve, 但 run2 的
3 个 frozen production final (title_final/desc_final/overlay) 已覆盖的 tid。

确定性来源 (全部冻结/由 Windows 真实包在运行时只读派生):
  * cohort_selection.csv (frozen, rows=10) -> 真实 source package 路径
  * approved_pv_refs(source_pkg) : pose_coverage 冻结映射 -> 每包 approved
    player-visible keys (PACK_TITLE/PACK_DESCRIPTION/POSE_DISPLAY_NAME), 返回
    (category, keyHash, source_text); tid = make_translation_id(source_hash(norm(src)),1)
  * run1 的 MISSING 判定: run1 resolver 输入 = old overrides(base) + done + cache;
    只要 approved tid 出现在 run2 的任一 production final key 集, 即视为
    "run1 曾缺失、run2 已补" -> 计入 run1_missing。

本脚本生成/校验 run1_missing_tids.txt:
  * rows == len(集合) == unique 严格相等
  * 每条 tid 必须是 10-cohort approved POSE_DISPLAY_NAME(及 PACK_DESC/PACK_TITLE
    若缺失) 中的一员, 且必须已被任一 production final 覆盖
  * --expect 9,9,1 | rows,unique,unique 默认为 "9,9,9";
  * 缺 keyHash 覆盖则 HARD-FAIL。

用法 (Windows, 只读, 不写 sidecar/不改 Mods/不加缓存):
  python scripts\gen_run1_missing.py --cohort output\cohort_selection.csv \
      --title-final output\translation_done_title_final.csv \
      --desc-final output\translation_done_desc_final.csv \
      --production-overlay output\translation_overrides.production.csv \
      -o output\run1_missing_tids.txt [--expect 9,9,9] [--no-write]

--no-write: 只打印/校验, 不写文件 (dry-run)。
"""
import argparse, csv, os, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from phase2a_catalog import source_hash, make_translation_id, norm_text
from gen_cohort_sidecars import approved_pv_refs


def tid_of(src, loc=1):
    return make_translation_id(source_hash(norm_text(src)), loc)


def load_final_keys(path, label):
    """返回 (tid_set, rows, err)。schema: translation_id 列必须存在。"""
    if not path:
        return set(), 0, f"{label}: 未提供路径"
    if not os.path.exists(path):
        return set(), 0, f"{label}: 文件不存在 {path}"
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    except Exception as ex:
        return set(), 0, f"{label}: 读取失败 {ex}"
    if not rows or "translation_id" not in rows[0]:
        return set(), 0, f"{label}: 空或缺 translation_id 列 ({len(rows)} 行)"
    keys = {r["translation_id"] for r in rows if (r.get("translation_id") or "").strip()}
    return keys, len(rows), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", required=True)
    ap.add_argument("--title-final", required=True)
    ap.add_argument("--desc-final", required=True)
    ap.add_argument("--production-overlay", required=True)
    ap.add_argument("-o", "--out", default="output/run1_missing_tids.txt")
    ap.add_argument("--expect", default="9,9,9", help="rows,unique,unique (run1 实证)")
    ap.add_argument("--no-write", action="store_true", help="dry-run, 只打印/校验不写文件")
    a = ap.parse_args()

    # ---- 0) 载入 3 production final key 集 ----
    finals = {}
    for label, path, exp in [("title_final", a.title_final, 407),
                             ("desc_final", a.desc_final, 190),
                             ("overlay", a.production_overlay, 217)]:
        keys, rows, err = load_final_keys(path, label)
        if err:
            print(f"[HARD-FAIL] {err}"); return 2
        if rows != exp:
            print(f"[HARD-FAIL] {label} 行数 {rows} != 冻结 {exp}"); return 2
        finals[label] = keys
        print(f"[final] {label}: {rows} 行 / key {len(keys)}")

    union = set().union(*finals.values())
    print(f"[union] production final key 集 = {len(union)}")

    # ---- 1) 读取 cohort, 收集 10 包 approved tids ----
    if not os.path.exists(a.cohort):
        print(f"[HARD-FAIL] cohort 不存在 {a.cohort}"); return 2
    with open(a.cohort, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    cols = {c.lower() for c in (rows[0].keys() if rows else {})}
    if "package_path" not in cols and "source_package" not in cols:
        print(f"[HARD-FAIL] cohort 缺 package_path/source_package 列"); return 2
    pkg_col = "package_path" if "package_path" in cols else "source_package"
    pkgs = [r[pkg_col] for r in rows if (r.get(pkg_col) or "").strip()]
    if len(pkgs) != 10:
        print(f"[HARD-FAIL] cohort packages={len(pkgs)} != 10"); return 2

    approved_by_pkg = {}
    all_approved_tids = {}
    missing_paths = 0
    for p in pkgs:
        if not os.path.exists(p):
            print(f"[HARD-FAIL] cohort package 不存在: {p}"); return 2
        tgi, kmap, app, errs = approved_pv_refs(p)
        if errs or app is None:
            print(f"[HARD-FAIL] {os.path.basename(p)} approved_pv_refs: {errs}"); return 2
        seen = set()
        for cat, kh, src in app:
            t = tid_of(src)
            if t in seen:
                print(f"[HARD-FAIL] {os.path.basename(p)} 重复 approved tid {t}"); return 2
            seen.add(t)
            all_approved_tids.setdefault(t, (cat, src))
            approved_by_pkg.setdefault(os.path.basename(p), []).append(t)
    print(f"[cohort] 10 包 approved unique tids = {len(all_approved_tids)}")

    # ---- 2) run1 MISSING = approved POSE_DISPLAY_NAME 类 (C-class) ----
    # 历史 run1 实证: run1 resolver (old base overrides+done+cache) 已能 resolve PACK_TITLE/
    # PACK_DESCRIPTION; 唯 POSE_DISPLAY_NAME (C CATALOG_TRANSLATE_MISSING_RESULT) 在 run1 中
    # MISSING。故 run1_missing = 10-cohort 包内 approved POSE_DISPLAY_NAME 的 tid。
    # (PACK_TITLE/PACK_DESCRIPTION 即使已入 finals 也不计入, 因其在 run1 未缺失。)
    keep_tids = {t for t, (cat, _s) in all_approved_tids.items()
                 if cat == "POSE_DISPLAY_NAME"}
    missing_sorted = sorted(keep_tids)
    missing = list(keep_tids)
    # 校验: 每条 run1-missing 必须已被 production final 覆盖 (run2 已补, 否则无法消除)
    not_covered = [t for t in missing if t not in union]
    if not_covered:
        print("[HARD-FAIL] run1 MISSING 仍有未被任一 production final 覆盖(无法消除):")
        for t in not_covered:
            print(f"    {t}")
        return 2
    print(f"[run1-missing] 10-cohort approved POSE_DISPLAY_NAME tids = {len(missing)}; "
          f"全部已被 production final 覆盖(可消除)")

    # ---- 3) 期望 gate ----
    try:
        ef = [int(x) for x in a.expect.split(",")]
    except ValueError:
        print(f"[HARD-FAIL] --expect 非法: {a.expect}"); return 2
    if len(ef) == 3 and ef[1] == ef[2] and ef[1] != 0:
        exp_rows, exp_unique, _ = ef
    elif len(ef) == 2:
        exp_rows, exp_unique = ef
    else:
        print(f"[HARD-FAIL] --expect 需 2 或 3 个值 (rows,unique[,unique])"); return 2
    rows = len(missing)
    unique = len(set(missing))
    print(f"---")
    print(f"run1_missing_expected = {exp_rows}")
    print(f"run1_missing_rows     = {rows}")
    print(f"run1_missing_unique   = {unique}")
    if rows != exp_rows or unique != exp_unique:
        print(f"[HARD-FAIL] run1_missing rows/unique != 期望 {exp_rows}/{exp_unique} "
              f"(实际 {rows}/{unique})")
        for t in missing:
            cat, src = all_approved_tids[t]
            print(f"    {t}  {cat}  {src!r}")
        return 2

    # ---- 4) 写文件: 每行一个 tid, 无注释, sort 稳定 ----
    if a.no_write:
        print(f"[dry-run] 不写文件; 将写出 {rows} 行 -> {a.out}")
    else:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            for t in missing_sorted:
                f.write(t + "\n")
        print(f"[write] {a.out}: {rows} 行 (unique={unique})")
    print(f"run1_missing done: rows={rows} unique={unique} 期望 {exp_rows}/{exp_unique} → OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
