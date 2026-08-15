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

  source_basename = Path(package_path).name
  candidate       = "!" + source_stem + "_CHS.package"  (与 A/B 确认的候选一致)
  candidate_earlier = candidate.lower() < source_basename.lower()
    (Python codepoint 字典序; 与部署时确定性 ordinal basename 比较一致)

必须全部满足 (否则禁止采用 / fail-closed):
  candidate earlier PASS = 436
  candidate earlier FAIL = 0
  duplicate target paths = 0
  source leading-space count = 0
  case-insensitive target collisions = 0

可选 --mods-root (ZERO WRITE): 额外扫描
  - candidate target 是否已存在于 Mods
  - 是否与旧 localization/test sidecar 形成 exact-TGI coexist (需人工确认清理后再 canary)

产出:
  output/deployment_lexical_audit.csv      (各列: source_basename, target_basename(000_ 诊断),
                                             verdict(000_ 诊断), candidate, candidate_earlier)
  output/deployment_lexical_audit_report.md
终局: CANDIDATE_POLICY_AUDIT: ADOPT|REJECT
fail-closed: 输出已存在拒写除非 --force; candidate policy 未全达标 -> rc=1 (不授权 bulk)。
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

MAN_OUT = "output/deployment_lexical_audit.csv"
REP_OUT = "output/deployment_lexical_audit_report.md"


def target_filename_for(source_name: str) -> str:
    """与 production_deploy_preflight_438.target_filename_for() 逐字一致。"""
    stem = Path(source_name).stem
    return f"000_{stem}_CHS.package"


def _res(p: str) -> Path:
    return Path(p).expanduser().resolve()


def main():
    ap = argparse.ArgumentParser(description="READ-ONLY lexical-earlier audit (436 DEPLOY rows)")
    ap.add_argument("--selection", required=True)
    ap.add_argument("--out", default=MAN_OUT)
    ap.add_argument("--report", default=REP_OUT)
    ap.add_argument("--mods-root", default=None,
                    help="Mods 根目录 (可选)。给出时额外做 ZERO-WRITE coexist 扫描: "
                         "candidate target 是否已存在, 及是否与旧 localization/test sidecar 形成 exact-TGI coexist")
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

    rows = []
    for r in deploys:
        src = Path(r.get("package_path", "")).name
        tgt = target_filename_for(src)
        earlier = tgt.lower() < src.lower()
        rows.append({
            "source_basename": src,
            "target_basename": tgt,
            "target_lexically_earlier": "YES" if earlier else "NO",
            "source_first_char": src[:1],
            "source_first_ord": hex(ord(src[0])) if src else "",
            "verdict": "PASS" if earlier else "FAIL",
        })

    n_pass = sum(1 for x in rows if x["verdict"] == "PASS")
    n_fail = n_deploy - n_pass
    # FAIL 按首字符分组
    fail_by_first = {}
    for x in rows:
        if x["verdict"] == "FAIL":
            fail_by_first[x["source_first_char"]] = fail_by_first.get(x["source_first_char"], 0) + 1

    ok = (n_fail == 0)
    verdict = "PASS" if ok else "FAIL"

    # ===================================================================
    # 正式候选 naming policy (A/B 真机已确认): target = "!" + source_stem + "_CHS.package"
    # -------------------------------------------------------------------
    # A/B 结果: !!Anika_Argument_CHS.package = PASS (000_!... = FAIL); sidecar bytes/SHA 未变
    # => LOAD_ORDER_CAUSE_CONFIRMED / M64_GAME_CANARY=PASS
    # 本段对全部 436 行做 ZERO-WRITE machine audit。
    # 必须全部满足才被批准 (否则禁止采用/bulk):
    #   candidate earlier PASS = 436
    #   candidate earlier FAIL = 0
    #   duplicate target paths = 0
    #   source leading-space count = 0
    #   case-insensitive target collisions = 0
    # 另: 当给 --mods-root 时, 扫描 candidate target 是否已存在于 Mods,
    #   以及是否与旧 localization/test sidecar 形成 exact-TGI coexist。
    def cand_target(src: str) -> str:
        return "!" + Path(src).stem + "_CHS.package"

    cand_passes = 0
    cand_fails = 0
    cand_dup_exact = 0
    cand_dup_exact_paths = set()
    cand_ci_collisions = 0
    cand_ci_paths = set()
    cand_seen_exact = {}   # candidate path (raw) -> source
    cand_seen_ci = {}      # candidate.lower() -> source
    cand_leading_space = sum(1 for x in rows if x["source_basename"][:1] == " ")
    for x in rows:
        s = x["source_basename"]
        cand = cand_target(s)
        if cand.lower() < s.lower():
            cand_passes += 1
        else:
            cand_fails += 1
        # duplicate target paths (exact)
        if cand in cand_seen_exact:
            cand_dup_exact += 1
            cand_dup_exact_paths.add(cand)
        else:
            cand_seen_exact[cand] = s
        # case-insensitive target collisions (candidate.lower() 重复 -> Windows 上同文件)
        cl = cand.lower()
        if cl in cand_seen_ci:
            cand_ci_collisions += 1
            cand_ci_paths.add(cand)
        else:
            cand_seen_ci[cl] = s
    # source basename lower() 碰撞 (diagnostic)
    src_seen_lower = {}
    src_collide = 0
    for x in rows:
        sl = x["source_basename"].lower()
        if sl in src_seen_lower:
            src_collide += 1
        else:
            src_seen_lower[sl] = True

    cand_ok = (cand_passes == n_deploy and cand_fails == 0 and cand_dup_exact == 0
               and cand_leading_space == 0 and cand_ci_collisions == 0)
    cand_adopt = "ADOPT" if cand_ok else "REJECT"
    cand_note = (f"{cand_adopt}: PASS={cand_passes} FAIL={cand_fails} "
                 f"dup_target_exact={cand_dup_exact} ci_collisions={cand_ci_collisions} "
                 f"leading_space={cand_leading_space}")

    def _all_stbl_tgis(p: Path):
        """枚举 package 内全部 STBL resources 的 exact (type, group, instance) TGI set.
        返回 (set[str], parse_ok: bool). parse_ok=False = malformed (fail-closed/诊断)."""
        try:
            import dbpf_fast
        except Exception:
            return set(), False
        try:
            idx, err = dbpf_fast.safe_parse(str(p))
            if idx is None:
                return set(), False
            out = set()
            for e in idx.entries:
                if e.type_id == 0x220557DA:  # STBL
                    out.add(f"0x{e.type_id:08X}/0x{e.group_id:08X}/0x{e.instance_id:016X}")
            return out, True
        except Exception:
            return set(), False
    _read_all_stbl_tgis = _all_stbl_tgis

    # 每个 source 的 exact TGI (从 selection CHS_target_TGI), 供 coexist 扫描
    src_tgi = {Path(r.get("package_path", "")).name: r.get("CHS_target_TGI", "")
               for r in deploys}

    # ---- Mods coexist 扫描 (ZERO WRITE): candidate target 已存在 / exact-TGI coexist ----
    # coexist 判定: production CHS_target_TGI ∈ package_all_STBL_TGIs (枚举全部, 不止首个)
    mods_root = _res(a.mods_root) if a.mods_root else None
    cand_mods_preexist = []          # candidate target 已存在于 Mods 的路径
    mods_exact_tgi_coexist = []      # 与该 candidate 形成 exact-TGI coexist 的 Mods 文件
    mods_scan_error = None
    mods_malformed_pkgs = []         # malformed package 诊断 (parse 失败 / 无 index)
    if mods_root is not None and mods_root.is_dir():
        # 收集 Mods 中所有 .package 的完整 STBL TGI set
        stbl_sets_in_mods = {}   # str(p) -> (set[str], parse_ok)
        for p in mods_root.rglob("*.package"):
            tgis, ok = _all_stbl_tgis(p)
            if not ok:
                mods_malformed_pkgs.append(str(p))
            stbl_sets_in_mods[str(p)] = (tgis, ok)
        # candidate target 是否已存在
        for x in rows:
            cand = cand_target(x["source_basename"])
            if (mods_root / cand).exists():
                cand_mods_preexist.append(str(mods_root / cand))
        # exact-TGI coexist: production TGI ∈ 该 package 的全部 STBL TGI set
        for x in rows:
            s = x["source_basename"]
            tgi = src_tgi.get(s, "")
            if not tgi:
                continue
            for other, (tgiset, _ok) in stbl_sets_in_mods.items():
                if tgi in tgiset:
                    mods_exact_tgi_coexist.append((s, tgi, other))
        mods_exact_tgi_coexist = sorted(set(mods_exact_tgi_coexist))
        mods_malformed_pkgs = sorted(set(mods_malformed_pkgs))
    elif mods_root is not None:
        mods_scan_error = f"Mods root 不是目录: {mods_root}"

    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["source_basename", "target_basename", "target_lexically_earlier",
            "source_first_char", "source_first_ord", "verdict",
            "candidate", "candidate_earlier"]
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for x in rows:
            s = x["source_basename"]
            cand = cand_target(s)
            x["candidate"] = cand
            x["candidate_earlier"] = "YES" if cand.lower() < s.lower() else "NO"
            w.writerow(x)

    rep.parent.mkdir(parents=True, exist_ok=True)
    L = []
    L.append("# Lexical-Earlier Audit (READ-ONLY, ZERO WRITE TO MODS)")
    L.append("")
    L.append(f"- selection: {sel}  (DEPLOY rows = {n_deploy})")
    L.append("- 比较规则: target_basename.lower() < source_basename.lower()")
    L.append("  (与 production_deploy_preflight_438.py 的确定性 ordinal basename 比较一致;")
    L.append("   target 名由 target_filename_for() 生成: `000_{source_stem}_CHS.package`)")
    L.append("- ZERO WRITE TO MODS: 仅读数, 不改名不动文件不部署")
    L.append("")
    L.append("## 终局")
    L.append(f"DEPLOY rows = {n_deploy}")
    L.append(f"lexical earlier PASS = {n_pass}")
    L.append(f"lexical earlier FAIL = {n_fail}")
    L.append(f"LEXICAL_AUDIT: {verdict}")
    L.append("")
    L.append("## FAIL 首字符分布")
    if fail_by_first:
        for ch, cnt in sorted(fail_by_first.items(), key=lambda kv: ord(kv[0])):
            L.append(f"- {repr(ch)} (ord {hex(ord(ch))}): {cnt}")
    else:
        L.append("- (无 FAIL)")
    L.append("")
    L.append("## 正式候选 naming policy: target = '!' + source_stem + '_CHS.package'")
    L.append("(A/B 真机已确认: !!Anika_Argument_CHS.package = PASS, 000_!... = FAIL;\n"
             " sidecar bytes/SHA 未变 => LOAD_ORDER_CAUSE_CONFIRMED / M64_GAME_CANARY=PASS)")
    L.append("- 当前'000_'策略诊断: lexical earlier PASS = {} / FAIL = {}".format(n_pass, n_fail))
    L.append(f"candidate earlier PASS = {cand_passes}")
    L.append(f"candidate earlier FAIL = {cand_fails}")
    L.append(f"duplicate target paths = {cand_dup_exact}")
    L.append(f"source leading-space count = {cand_leading_space}")
    L.append(f"case-insensitive target collisions = {cand_ci_collisions}")
    L.append(f"source basename collision after lower() = {src_collide}")
    L.append(f"CANDIDATE POLICY: {cand_adopt}   ({cand_note})")
    if cand_dup_exact:
        L.append("重复 target (exact) 列表:")
        for d in sorted(cand_dup_exact_paths):
            L.append(f"- {d!r}")
    if cand_ci_collisions:
        L.append("case-insensitive target 碰撞列表:")
        for d in sorted(cand_ci_paths):
            L.append(f"- {d!r}")
    if not cand_ok:
        L.append("原因: 未达到 PASS=436/FAIL=0/dup=0/leading-space=0/ci-collision=0, "
                 "禁止采用 (fail-closed).")
    L.append("")
    if mods_root is not None:
        L.append("## Mods coexist 扫描 (ZERO WRITE)")
        L.append(f"mods-root: {mods_root}")
        if mods_scan_error:
            L.append(f"扫描错误: {mods_scan_error}")
        L.append(f"candidate target 已存在于 Mods = {len(cand_mods_preexist)}")
        for p in cand_mods_preexist:
            L.append(f"- preexists: {p!r}")
        L.append(f"exact-TGI coexist with 其他 Mods 文件 = {len(mods_exact_tgi_coexist)}")
        for s, tgi, other in mods_exact_tgi_coexist:
            L.append(f"- source={s!r} TGI={tgi} <-> {other!r}")
        L.append(f"malformed package (parse 失败/无 index) = {len(mods_malformed_pkgs)}")
        for mp in mods_malformed_pkgs:
            L.append(f"- malformed: {mp!r}")
        L.append("(仅报告; ZERO WRITE; 需人工确认是否清理旧 sidecar 后再 canary)")
        L.append("(coexist 判定枚举每个 package 的**全部** STBL TGI set; "
                 "production CHS_target_TGI ∈ set 才算 coexist, 不止首个 STBL)")
        L.append("")
    L.append("## 000_ 当前策略下 FAIL rows (诊断; 000_ 已废弃)")
    for x in rows:
        if x["verdict"] == "FAIL":
            L.append(f"- FAIL  source={x['source_basename']!r}  target={x['target_basename']!r}"
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
        print(f"MODS_COEXIST: preexist={len(cand_mods_preexist)} "
              f"exact_tgi_coexist={len(mods_exact_tgi_coexist)} "
              f"malformed={len(mods_malformed_pkgs)}"
              + (f" scan_error={mods_scan_error}" if mods_scan_error else ""))
    print(f"output: {out}")
    print(f"report: {rep}")
    # 部署门: 候选 policy 必须全 436 达标; 否则 fail-closed rc=1 (不授权 bulk)
    return 0 if cand_ok else 1


if __name__ == "__main__":
    sys.exit(main())
