#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""READ-ONLY lexical-earlier audit for the 436 DEPLOY rows + candidate naming policy.

背景: Canary 真机 (A/B) 结果:
  000_!Anika_Argument_CHS.package = FAIL
  !!Anika_Argument_CHS.package   = PASS   (sidecar bytes/SHA 未变)
=> LOAD_ORDER_CAUSE_CONFIRMED / M64_GAME_CANARY=PASS
=> 正式候选 target naming policy: target = "!" + source_stem + "_CHS.package"
  (不再使用统一 '000_' 假设; '000_'-prefix 对首字为低位 ASCII
   (<= 0x2F: 空格 与 ! " # $ % & ' ( ) * + , - . / 等) 的 source 失效:
   '0'(0x30) 排在它们之后, 故 target 不早于 source)

本审计【ZERO WRITE TO MODS】, 对全部 DEPLOY rows 逐行 machine-audit:

  source_basename      = Path(package_path).name          (诊断/显示)
  candidate_basename   = "!" + source_stem + "_CHS.package"
  candidate_target_path = source_package.parent / candidate_basename
                         (source_package = 每个 source 的完整路径;
                          candidate 落在与 source 同一目录)
  candidate_earlier     = candidate_basename.lower() < source_basename.lower()
                           (Python codepoint 字典序; 与部署时确定性 ordinal 比较一致)

target collision 规则 (本次修正):
  - duplicate (exact) key 使用 规范化 absolute full path
    (Path(source_package).parent / candidate_basename) -> resolve()/normpath
  - Windows CI collision 使用 case-insensitive normalized absolute full path
    (absolute full path 全小写)
  - 两个同 basename 但位于不同 source directory 的 candidate 不是 collision。

必须全部满足 (否则禁止采用 / fail-closed):
  candidate earlier PASS = 436
  candidate earlier FAIL = 0
  duplicate target paths (按完整绝对路径) = 0
  source leading-space count = 0
  case-insensitive target collisions (按完整绝对路径, Windows CI) = 0

可选 --mods-root (ZERO WRITE): 额外扫描
  - candidate_preexists: candidate_target_path 是否真实存在于 Mods (检查新 full path,
    不是旧 000_ manifest target)
  - coexist 扫描: 对每个 Mods .package 枚举其全部 STBL TGI set; 对每个匹配分类:
        SELF_SOURCE                             (matched == 当前 source_package, 绝不计入)
        OTHER_SELECTED_SOURCE_SAME_TGI          (另一个 DEPLOY source 包, 同 exact TGI)
        EXISTING_LOCALIZATION_OR_TEST           (旧 localization/test sidecar, 同 exact TGI)
        OTHER_MODS_SAME_TGI                     (其它无关 Mods 包, 同 exact TGI)
    exact_tgi_coexist 仅统计 non-self。
  - malformed package (parse 失败/无 index) 不静默忽略: 逐条输出 path + parser_error,
    并区分 malformed_total_scanned / malformed_selected_source /
    malformed_existing_localization_candidate。

产出:
  output/deployment_lexical_audit.csv
    列: source_basename, source_package, source_sha256, CHS_target_TGI,
        candidate_basename, candidate_target_path, candidate_preexists,
        candidate_earlier, verdict(000_ 诊断)
  output/deployment_lexical_audit_report.md
终局: CANDIDATE_POLICY_AUDIT: ADOPT|REJECT
fail-closed: 输出已存在拒写除非 --force; candidate policy 未全达标 -> rc=1 (不授权 bulk)。
"""
import argparse
import csv
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

MAN_OUT = "output/deployment_lexical_audit.csv"
REP_OUT = "output/deployment_lexical_audit_report.md"

STBL_TYPE_ID = 0x220557DA

# coexist 分类
SELF_SOURCE = "SELF_SOURCE"
OTHER_SELECTED_SOURCE_SAME_TGI = "OTHER_SELECTED_SOURCE_SAME_TGI"
EXISTING_LOCALIZATION_OR_TEST = "EXISTING_LOCALIZATION_OR_TEST"
OTHER_MODS_SAME_TGI = "OTHER_MODS_SAME_TGI"


def _norm_path(p: Path) -> str:
    """规范化 absolute full path (用于 exact dup key)."""
    return str(Path(p).expanduser().resolve())


def _ci_path(p: Path) -> str:
    """Windows CI normalized absolute full path (全小写, 用于 CI collision key)."""
    return _norm_path(p).lower()


def _is_loc_name(name: str) -> bool:
    """与 collision forensic is_loc_name 一致: 只认明确 localization 特征."""
    low = name.lower()
    stem = Path(name).stem
    return low.startswith("000_") or stem.endswith("_chs") or "_test" in low


def _sha256(p: Path) -> str:
    try:
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for blk in iter(lambda: fh.read(1 << 20), b""):
                h.update(blk)
        return h.hexdigest()
    except Exception:
        return ""


def cand_target(src: str) -> str:
    """A/B 真机确认的候选 naming: '!' + source_stem + '_CHS.package'."""
    return "!" + Path(src).stem + "_CHS.package"


def _res(p: str) -> Path:
    return Path(p).expanduser().resolve()


def _all_stbl_and_err(p: Path):
    """枚举 package 内全部 STBL resources 的 exact TGI set + parse 错误码.
    返回 (set[str], parse_ok: bool, parser_error: Optional[str]).
    parse_ok=False = malformed (fail-closed/诊断). parser_error ∈ {None,
    'ERROR_UNSUPPORTED_DBPF', 'ERROR'} 等."""
    try:
        import dbpf_fast
    except Exception as e:
        return set(), False, f"IMPORT_ERROR: {e}"
    try:
        idx, err = dbpf_fast.safe_parse(str(p))
        if idx is None:
            return set(), False, (err or "NO_INDEX")
        out = set()
        for e in idx.entries:
            if e.type_id == STBL_TYPE_ID:
                out.add(f"0x{e.type_id:08X}/0x{e.group_id:08X}/0x{e.instance_id:016X}")
        return out, True, None
    except Exception as e:
        return set(), False, f"EXCEPTION: {e}"


def main():
    ap = argparse.ArgumentParser(description="READ-ONLY lexical-earlier audit (436 DEPLOY rows)")
    ap.add_argument("--selection", required=True)
    ap.add_argument("--out", default=MAN_OUT)
    ap.add_argument("--report", default=REP_OUT)
    ap.add_argument("--mods-root", default=None,
                    help="Mods 根目录 (可选)。给出时额外做 ZERO-WRITE coexist 扫描: "
                         "candidate_preexists 及 exact-TGI coexist (non-self) + malformed 诊断")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    out = Path(a.out).expanduser()
    rep = Path(a.report).expanduser()
    if (out.exists() or rep.exists()) and not a.force:
        print(f"[FAIL-CLOSED] 输出已存在, refuse (rc=1) 除非 --force: {out} | {rep}")
        return 1

    sel = _res(a.selection)
    deploys = []
    with open(sel, "r", newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("deployment_class", "") == "DEPLOY":
                deploys.append(r)
    n_deploy = len(deploys)
    print(f"[load] DEPLOY rows = {n_deploy}")

    # ---- per-row bookkeeping (按完整 source_package + candidate_target_path) ----
    # source_package -> normalized full path
    src_full = {}          # source_basename -> normalized absolute full path (resolution fallback)
    src_sha = {}           # source_basename -> sha256
    src_tgi = {}           # source_basename -> CHS_target_TGI
    src_pkg_path = {}      # source_basename -> source_package Path
    for r in deploys:
        sp = _res(r.get("package_path", "") or ".")
        src_pkg_path[r["package_path"]] = sp
        b = Path(r.get("package_path", "")).name
        src_full[b] = _norm_path(sp)
        src_sha[b] = _sha256(sp)
        src_tgi[b] = r.get("CHS_target_TGI", "")

    rows = []
    for r in deploys:
        b = Path(r.get("package_path", "")).name
        sp = src_pkg_path.get(r["package_path"], _res(r.get("package_path", "") or "."))
        cand = cand_target(b)
        cand_path = (sp.parent / cand)
        rows.append({
            # --- 000_ 旧诊断 (已废弃, 仅参照) ---
            "source_basename": b,
            "target_basename_000": f"000_{Path(b).stem}_CHS.package",
            "target_lexically_earlier_000":
                "YES" if f"000_{Path(b).stem}_CHS.package".lower() < b.lower() else "NO",
            "source_first_char": b[:1],
            "source_first_ord": hex(ord(b[0])) if b else "",
            "verdict_000": "PASS" if f"000_{Path(b).stem}_CHS.package".lower() < b.lower() else "FAIL",
            # --- 正式候选 ---
            "source_package": str(sp),
            "source_sha256": src_sha.get(b, ""),
            "CHS_target_TGI": src_tgi.get(b, ""),
            "candidate_basename": cand,
            "candidate_target_path": str(cand_path),
            "candidate_preexists": "NO",
            "candidate_earlier": "YES" if cand.lower() < b.lower() else "NO",
        })

    n_pass = sum(1 for x in rows if x["verdict_000"] == "PASS")
    n_fail = n_deploy - n_pass
    fail_by_first = {}
    for x in rows:
        if x["verdict_000"] == "FAIL":
            fail_by_first[x["source_first_char"]] = fail_by_first.get(x["source_first_char"], 0) + 1
    ok = (n_fail == 0)
    verdict = "PASS" if ok else "FAIL"

    # ===================================================================
    # 正式候选 naming policy gates (逐行; 按完整绝对路径)
    # -------------------------------------------------------------------
    cand_passes = 0
    cand_fails = 0
    cand_dup_exact = 0
    cand_dup_exact_paths = set()
    cand_dup_rows = []
    cand_ci_collisions = 0
    cand_ci_paths = set()
    cand_ci_rows = []
    cand_seen_exact = {}   # normalized full path -> source_basename
    cand_seen_ci = {}      # ci norm full path -> source_basename
    cand_leading_space = 0
    for x in rows:
        b = x["source_basename"]
        cand = x["candidate_basename"]
        if cand.lower() < b.lower():
            cand_passes += 1
        else:
            cand_fails += 1
        if b[:1] == " ":
            cand_leading_space += 1
        np_ = _norm_path(Path(x["candidate_target_path"]))
        ci_ = np_.lower()
        if np_ in cand_seen_exact:
            cand_dup_exact += 1
            cand_dup_exact_paths.add(np_)
            cand_dup_rows.append((b, np_))
        else:
            cand_seen_exact[np_] = b
        if ci_ in cand_seen_ci:
            cand_ci_collisions += 1
            cand_ci_paths.add(ci_)
            cand_ci_rows.append((b, np_))
        else:
            cand_seen_ci[ci_] = b

    cand_ok = (cand_passes == n_deploy and cand_fails == 0 and cand_dup_exact == 0
               and cand_leading_space == 0 and cand_ci_collisions == 0)
    cand_adopt = "ADOPT" if cand_ok else "REJECT"
    cand_note = (f"{cand_adopt}: PASS={cand_passes} FAIL={cand_fails} "
                 f"dup_target={cand_dup_exact} ci_collisions={cand_ci_collisions} "
                 f"leading_space={cand_leading_space}")

    # ---- Mods 扫描 (ZERO WRITE) ----
    mods_root = _res(a.mods_root) if a.mods_root else None
    mods_scan_error = None
    cand_preexist_count = 0
    cand_preexist_rows = []          # (source_basename, candidate_target_path)
    coexist = []                     # (source_basename, tgi, matched_path_norm, classification)
    malformed = []                   # (path, parser_error)
    malformed_sel = []               # malformed 且是 DEPLOY source 包
    malformed_loc = []               # malformed 且文件名是 localization/test 特征
    if mods_root is not None and mods_root.is_dir():
        # 1) preexist: 检查新的 candidate_target_path (真实文件存在)
        for x in rows:
            cp = Path(x["candidate_target_path"])
            if cp.exists():
                cand_preexist_count += 1
                cand_preexist_rows.append((x["source_basename"], str(cp)))
                x["candidate_preexists"] = "YES"
        # 2) malformed + STBL set 收集
        # 预索引: source_basename 集合 (用于区分 OTHER_SELECTED_SOURCE)
        sel_basenames = set(src_full.keys())
        sel_norm_paths = set(src_full.values())   # DEPLOY source 的规范化完整路径
        for p in mods_root.rglob("*.package"):
            tgis, p_ok, perr = _all_stbl_and_err(p)
            np_ = _norm_path(p)
            nm = Path(p).name
            if not p_ok:
                malformed.append((str(p), perr))
                if np_ in sel_norm_paths:
                    malformed_sel.append((str(p), perr))
                if _is_loc_name(nm):
                    malformed_loc.append((str(p), perr))
            # coexist: production TGI ∈ 完整 STBL set, 且 matched != 当前 source (SELF)
            # 注意: 需要按(source, tgi)来判定 SELF —— 一个 Mods 文件可能同时是多个 row 的 self。
            # matched 规范化 == 某 row 的 source_package 规范化 时, 该 row 视为 SELF。
            if p_ok and tgis:
                for x in rows:
                    tgi = x["CHS_target_TGI"]
                    if not tgi or tgi not in tgis:
                        continue
                    matched_np = np_
                    if matched_np == _norm_path(Path(x["source_package"])):
                        cls = SELF_SOURCE
                        # SELF 绝不计入 coexist collision, 但保留在 coexist 查看列表并标记
                        coexist.append((x["source_basename"], tgi, matched_np, cls))
                        continue
                    # 非 self: 分类
                    if matched_np in sel_norm_paths:
                        cls = OTHER_SELECTED_SOURCE_SAME_TGI
                    elif _is_loc_name(Path(matched_np).name):
                        cls = EXISTING_LOCALIZATION_OR_TEST
                    else:
                        cls = OTHER_MODS_SAME_TGI
                    coexist.append((x["source_basename"], tgi, matched_np, cls))
        coexist = sorted(set(coexist))
        malformed = sorted(set(malformed))
        malformed_sel = sorted(set(malformed_sel))
        malformed_loc = sorted(set(malformed_loc))
    elif mods_root is not None:
        mods_scan_error = f"Mods root 不是目录: {mods_root}"

    # coexist 分类计数 (non-self only 计入 exact_tgi_coexist)
    n_self = sum(1 for _s, _t, _m, c in coexist if c == SELF_SOURCE)
    n_other_sel = sum(1 for _s, _t, _m, c in coexist if c == OTHER_SELECTED_SOURCE_SAME_TGI)
    n_loc = sum(1 for _s, _t, _m, c in coexist if c == EXISTING_LOCALIZATION_OR_TEST)
    n_other_mods = sum(1 for _s, _t, _m, c in coexist if c == OTHER_MODS_SAME_TGI)
    n_non_self = n_other_sel + n_loc + n_other_mods
    n_malformed_total = len(malformed)
    n_malformed_sel = len(malformed_sel)
    n_malformed_loc = len(malformed_loc)

    # ---- CSV ----
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["source_basename", "source_package", "source_sha256", "CHS_target_TGI",
            "candidate_basename", "candidate_target_path", "candidate_preexists",
            "candidate_earlier", "verdict_000",
            "target_basename_000", "target_lexically_earlier_000",
            "source_first_char", "source_first_ord"]
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for x in rows:
            w.writerow(x)

    # ---- Report ----
    rep.parent.mkdir(parents=True, exist_ok=True)
    L = []
    L.append("# Lexical-Earlier Audit with Candidate Policy (READ-ONLY, ZERO WRITE TO MODS)")
    L.append("")
    L.append(f"- selection: {sel}  (DEPLOY rows = {n_deploy})")
    L.append("- 正式候选 naming: target = '!' + source_stem + '_CHS.package'")
    L.append("  (A/B 真机: !!Anika_Argument_CHS = PASS / 000_!... = FAIL; "
             "sidecar bytes/SHA 未变 => M64_GAME_CANARY=PASS)")
    L.append("- target collision: 按完整绝对路径判定")
    L.append("  * duplicate(exact) key = normalized absolute full path")
    L.append("  * Windows CI collision key = normalized absolute full path 全小写")
    L.append("  * 同 basename 但不同 source directory -> 非 collision")
    L.append("- ZERO WRITE TO MODS: 仅读数, 不改名不动文件不部署")
    L.append("")
    L.append("## 终局 (000_ 已废弃, 仅供诊断)")
    L.append(f"DEPLOY rows = {n_deploy}")
    L.append(f"000_ lexical earlier PASS = {n_pass}")
    L.append(f"000_ lexical earlier FAIL = {n_fail}")
    L.append("")
    L.append(f"candidate earlier PASS = {cand_passes}")
    L.append(f"candidate earlier FAIL = {cand_fails}")
    L.append(f"duplicate target paths (full path) = {cand_dup_exact}")
    L.append(f"source leading-space count = {cand_leading_space}")
    L.append(f"case-insensitive target collisions (full path CI) = {cand_ci_collisions}")
    L.append(f"CANDIDATE POLICY: {cand_adopt}   ({cand_note})")
    if cand_dup_exact:
        L.append("重复 target (exact, 完整路径) 列表:")
        for b, np_ in cand_dup_rows:
            L.append(f"- source={b!r}  target={np_!r}")
    if cand_ci_collisions:
        L.append("case-insensitive target 碰撞 (完整路径 CI) 列表:")
        for b, np_ in cand_ci_rows:
            L.append(f"- source={b!r}  target={np_!r}")
    if not cand_ok:
        L.append("原因: 未达到 candidate PASS=436/FAIL=0/dup=0/leading-space=0/ci-collision=0, "
                 "禁止采用 (fail-closed).")
    L.append("")
    if mods_root is not None:
        L.append("## Mods coexist 扫描 (ZERO WRITE; non-self only)")
        L.append(f"mods-root: {mods_root}")
        if mods_scan_error:
            L.append(f"扫描错误: {mods_scan_error}")
        L.append(f"candidate_preexists (新 full path 真实存在) = {cand_preexist_count}")
        for b, cp in cand_preexist_rows:
            L.append(f"- preexists: source={b!r} -> {cp!r}")
        L.append("coexist 分类 (matched_package != 当前 source 才计; SELF_SOURCE 绝不计入):")
        L.append(f"- self_tgi_hits                  = {n_self}   (SELF, 不计入 collision)")
        L.append(f"- other_selected_source_same_tgi = {n_other_sel}")
        L.append(f"- existing_localization_or_test  = {n_loc}")
        L.append(f"- other_mods_same_tgi            = {n_other_mods}")
        L.append(f"- exact_tgi_coexist (non-self)   = {n_non_self}")
        for s, tgi, m, cls in coexist:
            mark = " [NON-SELF]" if cls != SELF_SOURCE else " [SELF-IGNORED]"
            L.append(f"- {cls}{mark}  source={s!r} TGI={tgi} <-> {m!r}")
        L.append("")
        L.append(f"## malformed package (parse 失败; 不静默忽略)")
        L.append(f"- malformed_total_scanned                 = {n_malformed_total}")
        L.append(f"- malformed_selected_source               = {n_malformed_sel}")
        L.append(f"- malformed_existing_localization_candidate = {n_malformed_loc}")
        mal_sel_paths = {mp for mp, _p in malformed_sel}
        mal_loc_paths = {mp for mp, _p in malformed_loc}
        for mp, perr in malformed:
            kind = []
            if mp in mal_sel_paths:
                kind.append("SELECTED_SOURCE")
            if mp in mal_loc_paths:
                kind.append("LOCALIZATION_CAND")
            L.append(f"- {mp!r}  parser_error={perr!r}  ({','.join(kind) or 'other'})")
        L.append("请先用项目内可靠 parser/fallback 复核这些文件; 不能为让 audit PASS 而忽略。")
        L.append("")
    L.append("## 000_ 当前策略下 FAIL rows (诊断; 000_ 已废弃)")
    for x in rows:
        if x["verdict_000"] == "FAIL":
            L.append(f"- FAIL  source={x['source_basename']!r}"
                     f"  target={x['target_basename_000']!r}"
                     f"  (first={x['source_first_char']!r} ord={x['source_first_ord']})")
    L.append("")
    L.append(f"CANDIDATE_POLICY_AUDIT: {cand_adopt}")
    L.append("注: 批准候选 policy 需 candidate PASS=436 / FAIL=0 / dup=0 / "
             "leading-space=0 / ci-collision=0 (及 Mods coexist 无新歧义).")
    with open(rep, "w", newline="", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    print(f"## LEXICAL_AUDIT (000_ 诊断): {verdict}")
    print(f"DEPLOY rows = {n_deploy}")
    print(f"000_ lexical earlier PASS = {n_pass}  (000_ 已废弃)")
    print(f"000_ lexical earlier FAIL = {n_fail}")
    print(f"CANDIDATE (target='!'+stem+'_CHS'): PASS={cand_passes} FAIL={cand_fails} "
          f"dup_target={cand_dup_exact} ci_collision={cand_ci_collisions} "
          f"leading_space={cand_leading_space} => {cand_adopt}")
    if mods_root is not None:
        print(f"MODS_COEXIST: preexist={cand_preexist_count} "
              f"exact_tgi_coexist={n_non_self} "
              f"self_tgi_hits={n_self} "
              f"other_selected_source_same_tgi={n_other_sel} "
              f"existing_localization_or_test={n_loc} "
              f"other_mods_same_tgi={n_other_mods} "
              f"malformed_total={n_malformed_total} "
              f"malformed_selected_source={n_malformed_sel} "
              f"malformed_existing_localization_candidate={n_malformed_loc}"
              + (f" scan_error={mods_scan_error}" if mods_scan_error else ""))
    print(f"output: {out}")
    print(f"report: {rep}")
    # 部署门: 候选 policy 必须全达标; 否则 fail-closed rc=1 (不授权 bulk)
    return 0 if cand_ok else 1


if __name__ == "__main__":
    sys.exit(main())
