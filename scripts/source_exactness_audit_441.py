#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
source_exactness_audit_441.py —— 全 441 只读 source 精确性 census (zero-write, v2)
================================================================================================
背景 (2026-08-16 实机):
  Pose Production Generation 出现 6 个 writer expected-text precondition FAIL,
  模式统一为 "expected=<源码首冒号前> actual=<真实 STBL 全串>"。根因在 writer
  -m KEYHASH:EXPECTED:VALUE 的冒号定界契约 (C# IndexOf 取最前两个冒号, 无转义) 与
  含冒号的 pose 源文本冲突 —— 非数据截断, 而是 CLI 序列化边界缺口。

v2 修复 (2026-08-16, 用户指出 auditor 缺陷):
  A) 独立重读必须区分 "找不到/读失败/缺 key" 与 "真实字符串":
       actual_found ∈ {Y, N, AUDIT_ERROR}
       actual_source = 找到时的真实字节串; 未找到/读失败 = 空 (绝不伪造 "NA"/"<absent>" 参与比对)
       actual_read_error = AUDIT_ERROR 时的错误码
     lookup 失败 → 该行 actual_found=AUDIT_ERROR, 不计入 exact_match / mismatch。
     任何读失败/缺 key 一律走 AUDIT_ERROR, 绝不当作 source mismatch。
  B) writer_would_fail 定义收紧: 仅当
       resolver_action == TRANSLATE   (该 key 确实作为 -m modification 发送)
       AND source_has_colon           (expected_source 含 ':')
     KEEP refs 即使含冒号也从不进 -m, 不得计 writer_would_fail。
     分类与 orchestrator production_generate_441 完全一致 (复用 _resolve_with_keep441)。

列:
  package_path / role / key_hash / plan_source / actual_found / actual_source /
  actual_read_error / exact_match / source_len / source_has_colon /
  resolver_action / writer_expected / writer_value / writer_would_fail / transport_risk

汇总:
  exact_match / mismatch(=actual_found=Y 且 !=) / audit_error / read_failed_packages /
  source_has_colon (全 approved) / translate_mod_has_colon / writer_would_fail
  mismatch_packages / mismatch_keys

rc: mismatch>0 -> 1 (仍写出 CSV); 0 = 无真 mismatch 且无 AUDIT_ERROR 且 writer_would_fail=0。

用法 (Windows, 真实数据):
  python scripts\\source_exactness_audit_441.py ^
      --coverage output\\coverage_manual_adj.csv ^
      --title-final output\\translation_done_title_final.csv ^
      --desc-final output\\translation_done_desc_final.csv ^
      --production-overlay output\\translation_overrides.production.csv ^
      --production-overlay-441 output\\translation_overrides.production.441.csv ^
      --done output\\translation_done.csv ^
      --catalog output\\translation_catalog.csv ^
      --out output\\source_exactness_audit_441.csv

（--done/--catalog 可省略 = 空; 与 orchestrator 同源可传真实文件以精确复刻分类。）
"""
import sys, os, csv, argparse, re, ast
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gen_cohort_sidecars import approved_pv_refs, resolve_all_approved
from production_preflight_441 import _layer_441_overlay
from production_resolver import make_production_resolver
from production_generate_441 import _resolve_with_keep441   # 与 orchestrator 完全一致的分类
from dbpf_fast import safe_parse
import audit_sidecar as A                                 # 独立 canonical STBL reader
from phase2a_catalog import norm_text


_OUT_COLS = [
    "package_path",
    "role",
    "key_hash",
    "plan_source",
    "actual_found",        # Y / N / AUDIT_ERROR
    "actual_source",       # 找到时的真实字节串; 否则空
    "actual_read_error",   # AUDIT_ERROR 时错误码; 否则空
    "exact_match",         # Y/N  仅 actual_found=Y 时定义
    "source_len",
    "source_has_colon",    # Y/N  transport-risk 超集 (全 approved)
    "resolver_action",     # TRANSLATE / KEEP / UNRESOLVED / ERROR
    "writer_expected",     # C# 取前两个冒号 -> EXPECTED (源首冒号前)
    "writer_value",        # C# 第2冒号后 -> VALUE (源含冒号时会错位)
    "writer_would_fail",   # Y/N  TRANSLATE 且含冒号 (真正进 -m 的 key)
    "transport_risk",      # Y/N  source_has_colon (任何 key, 含 KEEP)
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
    """独立重读 exact CHS (0x01 locale) STBL。
    返回 (actual_map, read_error): actual_map={kh:text} 或 None; read_error 非 None 表示读失败。

    绝不把 找不到/读失败 伪装成普通字符串; 失败一律 read_error -> AUDIT_ERROR。"""
    try:
        idx, err = safe_parse(path)
        if err or idx is None:
            return None, f"AUDIT_ERROR.DBPF:{err}"
    except Exception as ex:
        return None, f"AUDIT_ERROR.DBPF_EXC:{type(ex).__name__}:{ex}"
    try:
        for e in idx.entries:
            if e.type_id == A.STBL_TID and ((e.instance_id >> 56) & 0xFF) == 0x01:
                cnt, keys = A.read_stbl_entries(path, e, idx)
                if cnt is None:
                    return None, "AUDIT_ERROR.STBL_READ"
                return {kh: (fl, txt) for kh, fl, txt in keys}, None
        return None, "AUDIT_ERROR.NO_CHS_STBL"
    except Exception as ex:
        return None, f"AUDIT_ERROR.STBL_EXC:{type(ex).__name__}:{ex}"


def _classify_like_orchestrator(approved, resolver, overlay_path, keep441):
    """与 production_generate_441 完全一致的分类 (直接复用它的 _resolve_with_keep441)。
    返回 (mod_keys, keep_keys, unresolved_keys)。"""
    mods, keeps, errs2 = _resolve_with_keep441(approved, resolver, overlay_path, keep441)
    mod_keys = {kh for kh, _s, _tr, _tag in mods}
    keep_keys = {kh for kh, _s in keeps}
    un_rescued = set()
    for e in errs2:
        m = re.search(r"key 0x([0-9A-Fa-f]+)", e)
        if m:
            try:
                un_rescued.add(int(m.group(1), 16))
            except Exception:
                pass
    return mod_keys, keep_keys, un_rescued


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage", required=True, help="output/coverage_manual_adj.csv (441 ELIGIBLE)")
    ap.add_argument("--title-final", required=True)
    ap.add_argument("--desc-final", required=True)
    ap.add_argument("--production-overlay", required=True, help="241 base overlay")
    ap.add_argument("--production-overlay-441", required=True, help="441 terminal overlay")
    ap.add_argument("--done", default="", help="translation_done.csv (可空)")
    ap.add_argument("--catalog", default="", help="translation_catalog.csv (可空)")
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

    cols = list(cov[0].keys()) if cov else []
    path_col = next((c for c in cols if c.lower() in ("package_path", "path", "package")), None)
    if path_col is None:
        print(f"[HARD-FAIL] coverage 无 package 路径列; 列={cols}")
        return 2

    # ---- 构建与 orchestrator 完全一致的 441-layered resolver ----
    try:
        resolver = make_production_resolver(
            a.title_final, a.desc_final, a.production_overlay,
            translation_done=(a.done if a.done else None),
            translation_catalog=(a.catalog if a.catalog else None))
        _layered441, keep441, _trans441 = _layer_441_overlay(resolver, a.production_overlay_441)
    except RuntimeError as ex:
        print(f"[HARD-FAIL] resolver/layer 构建失败: {ex}")
        return 2

    rows = []
    stat = {"packages": 0, "approved_keys": 0, "exact_match": 0, "mismatch": 0,
            "audit_error": 0, "colon": 0, "translate_colon": 0, "writer_would_fail": 0}
    mismatch_pkgs = []
    mismatch_keys = []
    for r in elig:
        path = (r.get(path_col) or "").strip()
        stat["packages"] += 1
        if not os.path.exists(path):
            rows.append({"package_path": path, "role": "", "key_hash": "",
                         "plan_source": "", "actual_found": "AUDIT_ERROR",
                         "actual_source": "", "actual_read_error": "AUDIT_ERROR.PKG_MISSING",
                         "exact_match": "", "source_len": 0, "source_has_colon": "",
                         "resolver_action": "", "writer_expected": "", "writer_value": "",
                         "writer_would_fail": "", "transport_risk": ""})
            continue
        try:
            _tgi, _key_map, approved, _errs = approved_pv_refs(path)
        except Exception as ex:
            stat["audit_error"] += 1
            rows.append({"package_path": path, "role": "", "key_hash": "",
                         "plan_source": "", "actual_found": "AUDIT_ERROR",
                         "actual_source": "", "actual_read_error": f"AUDIT_ERROR.MAP_EXC:{type(ex).__name__}",
                         "exact_match": "", "source_len": 0, "source_has_colon": "",
                         "resolver_action": "", "writer_expected": "", "writer_value": "",
                         "writer_would_fail": "", "transport_risk": ""})
            continue
        if approved is None:
            # approved_pv_refs 自身 mapping 失败 (scan 状态不符/CHS 异常等) -> 包级 AUDIT_ERROR, 不静默丢弃
            stat["audit_error"] += 1
            rows.append({"package_path": path, "role": "", "key_hash": "",
                         "plan_source": "", "actual_found": "AUDIT_ERROR",
                         "actual_source": "", "actual_read_error": "AUDIT_ERROR.MAP_NONE",
                         "exact_match": "", "source_len": 0, "source_has_colon": "",
                         "resolver_action": "", "writer_expected": "", "writer_value": "",
                         "writer_would_fail": "", "transport_risk": ""})
            continue

        # 分类 (与 orchestrator 一致): 哪些 key 会作为 -m 发送
        try:
            mod_keys, keep_keys, un_rescued = _classify_like_orchestrator(
                approved, resolver, a.production_overlay, keep441)
        except Exception as ex:
            mod_keys, keep_keys, un_rescued = set(), set(), set()
            for _cat, kh, _src in approved:
                pass  # 分类异常: 全部按 ERROR
            _class_err = str(ex)
        else:
            _class_err = ""

        act, aerr = indep_actual_chs_stbl(path)
        read_failed = (act is None)

        for cat, kh, plan_src in approved:
            stat["approved_keys"] += 1
            # resolver_action
            if kh in mod_keys:
                action = "TRANSLATE"
            elif kh in keep_keys:
                action = "KEEP"
            elif kh in un_rescued:
                action = "UNRESOLVED"
            else:
                action = "ERROR"

            if read_failed:
                stat["audit_error"] += 1
                rows.append({"package_path": path, "role": cat, "key_hash": "0x%08X" % kh,
                             "plan_source": plan_src, "actual_found": "AUDIT_ERROR",
                             "actual_source": "", "actual_read_error": aerr,
                             "exact_match": "", "source_len": len(plan_src) if plan_src else 0,
                             "source_has_colon": ("Y" if plan_src and ":" in plan_src else "N"),
                             "resolver_action": action,
                             "writer_expected": _writer_split(plan_src)[0],
                             "writer_value": _writer_split(plan_src)[1],
                             "writer_would_fail": "",
                             "transport_risk": ("Y" if plan_src and ":" in plan_src else "N")})
                continue

            hit = act.get(kh)
            if hit is None:
                stat["audit_error"] += 1
                rows.append({"package_path": path, "role": cat, "key_hash": "0x%08X" % kh,
                             "plan_source": plan_src, "actual_found": "N",
                             "actual_source": "", "actual_read_error": "AUDIT_ERROR.KEY_ABSENT",
                             "exact_match": "", "source_len": len(plan_src) if plan_src else 0,
                             "source_has_colon": ("Y" if plan_src and ":" in plan_src else "N"),
                             "resolver_action": action,
                             "writer_expected": _writer_split(plan_src)[0],
                             "writer_value": _writer_split(plan_src)[1],
                             "writer_would_fail": "",
                             "transport_risk": ("Y" if plan_src and ":" in plan_src else "N")})
                continue

            actual = hit[1]
            exact = (plan_src == actual)
            has_colon = (plan_src is not None and ":" in plan_src)
            w_exp, w_val = _writer_split(plan_src)
            wf = ("Y" if (action == "TRANSLATE" and has_colon and exact) else "N")
            stat["exact_match"] += (1 if exact else 0)
            stat["mismatch"] += (0 if exact else 1)
            stat["colon"] += (1 if has_colon else 0)
            if action == "TRANSLATE" and has_colon:
                stat["translate_colon"] += 1
            if wf == "Y":
                stat["writer_would_fail"] += 1
            if not exact:
                if path not in mismatch_pkgs:
                    mismatch_pkgs.append(path)
                mismatch_keys.append((os.path.basename(path), cat, "0x%08X" % kh,
                                      repr(plan_src), repr(actual)))
            rows.append({"package_path": path, "role": cat, "key_hash": "0x%08X" % kh,
                         "plan_source": plan_src, "actual_found": "Y",
                         "actual_source": actual, "actual_read_error": "",
                         "exact_match": ("Y" if exact else "N"),
                         "source_len": len(plan_src) if plan_src else 0,
                         "source_has_colon": ("Y" if has_colon else "N"),
                         "resolver_action": action,
                         "writer_expected": w_exp, "writer_value": w_val,
                         "writer_would_fail": wf,
                         "transport_risk": ("Y" if has_colon else "N")})

    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=_OUT_COLS)
        w.writeheader()
        for rr in rows:
            w.writerow(rr)

    print("=== source exactness census v2 (441, read-only) ===")
    print(f"packages            = {stat['packages']}")
    print(f"approved keys       = {stat['approved_keys']}")
    print(f"exact_match         = {stat['exact_match']}")
    print(f"mismatch            = {stat['mismatch']}   (仅 actual_found=Y 且字符串不等)")
    print(f"audit_error         = {stat['audit_error']}   (读失败/缺 key/读异常 → 非 mismatch)")
    print(f"source_has_colon    = {stat['colon']}   (transport-risk 超集, 全 approved)")
    print(f"translate_mod_colon = {stat['translate_colon']}   (TRANSLATE 且含冒号)")
    print(f"writer_would_fail   = {stat['writer_would_fail']}   (TRANSLATE 且含冒号且 exact → 真机必 FAIL)")
    print(f"mismatch packages   = {len(set(mismatch_pkgs))}")
    print(f"mismatch keys       = {len(mismatch_keys)}")
    print(f"\nCSV: {a.out}")
    if mismatch_keys:
        print("\n--- mismatch keys (真实字符串不等, 非 NA) ---")
        for b, cat, kh, ps, act in mismatch_keys:
            print(f"  {b} | {cat} | {kh}\n      plan  ={ps}\n      actual={act}")
    if stat["audit_error"]:
        print(f"\n注意: audit_error={stat['audit_error']} — 这些 key 无法独立核对, 已排除出 mismatch。")
    return (1 if (stat["mismatch"] > 0 or stat["audit_error"] > 0 or stat["writer_would_fail"] > 0) else 0)


if __name__ == "__main__":
    sys.exit(main())
