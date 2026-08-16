#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FINAL DEPLOYMENT RECONCILIATION — 只生成最终部署计划, 不执行 bulk deploy (ZERO WRITE TO MODS)。

输入 (均必需):
  --selection  output/production_deployment_selection_441_manifest.csv
               (DEPLOY=436 / NOOP=3 / CONFLICT_SKIP=2 / TOTAL=441)
  --mods-root  真实 Mods 根
  --sidecar-v1 output/production_sidecars_441_v1        (staging, 提供 production sidecar 字节)
  --sidecar-retry6 output/production_sidecars_retry6_v1

本工具【ZERO WRITE TO MODS】: 只读 (open/sha256/dbpf_fast.safe_parse), 不 copy/move/delete/rename/overwrite。
只生成最终部署计划 CSV + 报告。不修改 classification / translation / production overlay /
writer / staging sidecar bytes / source packages。不重新生成 sidecar, 仅引用既有 staging。
不执行 bulk deploy。

deployment target naming (冻结, 本步唯一 target-generation):
  candidate_target_filename_for(source) = "!" + source_stem + "_CHS.package"
  candidate_target_path = mods_root / rel_to_mods(source) / candidate_basename
  机器断言每一 DEPLOY row: candidate_basename.lower() < source_basename.lower()
旧 000_ naming policy 正式废弃 (仅保留历史诊断)。

deployment_target_naming 与状态模型 (冻结):
  candidate_target_filename_for(source) = "!" + source_stem + "_CHS.package"
  candidate_target_path = mods_root / rel_to_mods(source) / candidate_basename
  机器断言每一 DEPLOY row: candidate_basename.lower() < source_basename.lower()
旧 000_ naming policy 正式废弃 (仅保留历史诊断)。

对每个 DEPLOY row, 扫描 Mods 全树 exact STBL TGI 命中, 独立计算 deployment_action (互斥):
  EXACT_CANDIDATE_SATISFIED  本 row 自身 candidate_target_path 已存在 + exact TGI + SHA == prod sidecar
  EQUIVALENT_ALTNAME_SATISFIED 本 row 自身目录 (cand.parent) 其他文件名 sidecar + SHA==prod + 更早 basename
  COPY_REQUIRED              本 row 自身目录无等价 sidecar 命中 => 必须独立部署
  QUARANTINE_BLOCKED         localization/test marker 同 exact TGI + 字节 != prod => 需先隔离 (不部署)
  HARD_CONFLICT              本 row 目录内同 TGI 内容不一致且非安全重复 group 可解释 => fail-closed
  UNRELATED                  KEEP (NOOP / CONFLICT_SKIP / 无关 Mods 文件)

duplicate_source_group 是【属性/诊断】, 非互斥 deployment action:
  duplicate_source_group=YES / duplicate_group_id / duplicate_group_safe 标注到每个命中重复 group 的 row。
  多个 selected DEPLOY rows exact TGI 相同 + source SHA 相同 + production sidecar SHA 相同
  => duplicate_group_safe=YES, 只说明这些 row 之间【非 HARD_CONFLICT】, 绝不使任一 row 视为 satisfied。
  每个 row 仍必须独立判定其 deployment_action (仅看自身 source 目录);
  禁止因‘另一目录存在同 TGI/SHA source 或 sidecar’而把当前 row 当 satisfied。

机器断言 (对 436 DEPLOY rows):
  EXACT_CANDIDATE_SATISFIED + EQUIVALENT_ALTNAME_SATISFIED + COPY_REQUIRED
  + (QUARANTINE_BLOCKED|HARD_CONFLICT) rows = 436
  duplicate_source_group 行单独统计, 不得参与上式作为互斥 action。

malformed 门 (fail-closed):
  malformed_selected_source = 0 且 malformed_existing_localization_candidate = 0 才 PASS。
  Mods 中其他 unrelated malformed 只记录, 不阻塞本批部署。
  duplicate_group_safe=NO 亦 fail-closed。

已知真机现场必须正确识别:
  !!Anika_Argument_CHS.package            -> EXACT_CANDIDATE_SATISFIED
  000_AngerFrustrationandRageflowur_CHS   -> EQUIVALENT_ALTNAME_SATISFIED
  000_02_Gounafiers_Poses_Public_Ver (1)_CHS -> EQUIVALENT_ALTNAME_SATISFIED
  000_Tibo131_PosePack 2_CHS              -> EQUIVALENT_ALTNAME_SATISFIED
对这些已满足项禁止再部署第二份相同 TGI sidecar。

输出 (fail-closed, 已存在则拒写除非 --force):
  output/production_deployment_final_manifest.csv
  output/production_deployment_final_report.md

stdout 至少:
  DEPLOY rows=436
  EXACT_CANDIDATE_SATISFIED=?
  EQUIVALENT_ALTNAME_SATISFIED=?
  COPY_REQUIRED=?
  QUARANTINE_BLOCKED=?
  HARD_CONFLICT=0
  duplicate_source_group rows=? (属性/诊断, 非互斥 action)
  NOOP=3
  CONFLICT_SKIP=2
  TOTAL=441
"""
import argparse
import csv
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse  # noqa: E402

_STBL_TID = 0x220557DA
_STBL_GROUP = 0x80000000

MAN_OUT = "output/production_deployment_final_manifest.csv"
REP_OUT = "output/production_deployment_final_report.md"

# deployment_action (每个 DEPLOY row 独立计算, 互斥)
#   SATISFIED = EXACT | ALTNAME
#   COPY_REQUIRED, QUARANTINE_BLOCKED, HARD_CONFLICT
EXACT = "EXACT_CANDIDATE_SATISFIED"
ALTNAME = "EQUIVALENT_ALTNAME_SATISFIED"
OLD_TEST = "QUARANTINE_BLOCKED"
HARD = "HARD_CONFLICT"
COPY = "COPY_REQUIRED"
UNRELATED = "UNRELATED"
# duplicate_source_group 与 duplicate_group_safe 是【属性/诊断】, 非互斥 action。
# 机器断言: EXACT + ALTNAME + COPY_REQUIRED + (QUARANTINE_BLOCKED|HARD_CONFLICT) = DEPLOY total;
# duplicate_source_group 行单独统计, 不参与上式作为互斥 action。

# Cry INHERENT conflict (skipped from deployment; 永不视为 HARD_CONFLICT)
_CRY_TGI = "0x220557DA/0x80000000/0x01D208F3F86A48A1"


def _res(p):
    return Path(p).expanduser().resolve()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def rel_to_mods(src: Path, mods_root: Path) -> str:
    """镜像原 package 相对 Mods 根的目录结构 (与 production_generate_441 一致)。"""
    src = src.resolve()
    if mods_root:
        mroot = mods_root.resolve()
        try:
            rel = src.relative_to(mroot)
            return str(rel.parent)
        except ValueError:
            pass
    cwd = Path.cwd().resolve()
    try:
        rel = src.relative_to(cwd)
        return str(rel.parent) if str(rel.parent) != "." else ""
    except ValueError:
        return ""


def stbl_tgi_set(pkg: Path):
    """完整 STBL TGI set (非仅首个)。解析失败 -> (None for insts), parse_ok=False, parser_error。"""
    try:
        idx, err = safe_parse(pkg)
    except Exception as ex:
        return None, False, "EXCEPTION: %s" % (ex,)
    if err is not None or idx is None:
        return None, False, ("ERROR_UNSUPPORTED_DBPF" if err else "NO_INDEX")
    tgis = set()
    for e in idx.entries:
        if e.type_id == _STBL_TID and e.group_id == _STBL_GROUP and e.instance_id is not None:
            tgis.add("0x%08X/0x%08X/0x%016X" % (_STBL_TID, _STBL_GROUP, e.instance_id))
    return tgis, True, None


def is_loc_name(name: str) -> bool:
    low = name.lower()
    stem = name[:name.rfind(".")].lower() if "." in name else name.lower()
    return (low.startswith("000_") or stem.endswith("_chs") or "_test" in low)


def inst_from_tgi(tgi: str):
    try:
        return int(tgi.split("/")[-1], 16)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="FINAL DEPLOYMENT RECONCILIATION (ZERO WRITE TO MODS)")
    ap.add_argument("--selection", required=True)
    ap.add_argument("--mods-root", required=True)
    ap.add_argument("--sidecar-v1", required=True)
    ap.add_argument("--sidecar-retry6", required=True)
    ap.add_argument("--out", default=MAN_OUT)
    ap.add_argument("--report", default=REP_OUT)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    out = Path(a.out).expanduser()
    rep = Path(a.report).expanduser()
    if (out.exists() or rep.exists()) and not a.force:
        print(f"[FAIL-CLOSED] 输出已存在, refuse (rc=1) 除非 --force: {out} | {rep}")
        return 1

    mods_root = _res(a.mods_root)
    if not mods_root.is_dir():
        print(f"[HARD-FAIL] mods-root 不是目录 (rc=2): {mods_root}")
        return 2
    sv1 = _res(a.sidecar_v1)
    sr6 = _res(a.sidecar_retry6)
    if not (sv1.is_dir() and sr6.is_dir()):
        print(f"[HARD-FAIL] sidecar staging 目录缺失 (rc=2): {sv1} | {sr6}")
        return 2

    # ---------- 载入 selection ----------
    sel_path = _res(a.selection)
    DEPLOY, NOOP, CONFLICT = [], [], []
    with open(sel_path, "r", newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            cls = r.get("deployment_class", "")
            if cls == "DEPLOY":
                DEPLOY.append(r)
            elif cls == "NOOP":
                NOOP.append(r)
            elif cls == "CONFLICT_SKIP":
                CONFLICT.append(r)
    n_deploy = len(DEPLOY)
    n_noop = len(NOOP)
    n_conflict = len(CONFLICT)
    print(f"[load] DEPLOY={n_deploy} NOOP={n_noop} CONFLICT_SKIP={n_conflict}")
    if n_deploy != 436 or n_noop != 3 or n_conflict != 2:
        print(f"[HARD-FAIL] 期望 DEPLOY=436 NOOP=3 CONFLICT_SKIP=2 (got {n_deploy}/{n_noop}/{n_conflict}) rc=2")
        return 2

    # ---------- 逐 DEPLOY row: 计算 candidate target / source sha / production sidecar sha ----------
    rows = []
    for r in DEPLOY:
        src = _res(r.get("package_path", ""))
        stem = src.stem
        cand_basename = "!" + stem + "_CHS.package"
        rel = rel_to_mods(src, mods_root)
        cand_path = mods_root / (rel or ".") / cand_basename
        if rel:
            cand_path = mods_root / rel / cand_basename
        else:
            cand_path = mods_root / cand_basename
        # production sidecar sha: 由 manifest sidecar_path (staging) 提供
        sp = _res(r.get("sidecar_path", ""))
        if sp.is_file():
            prod_sha = sha256_file(sp)
        else:
            prod_sha = ""  # 无法证明生产 sidecar 字节 -> 该 row 无法满足/判定, 计入待 COPY 或 fail
        # source sha: 重新核 exact SHA (用户要求, 不信任 manifest)
        src_sha = sha256_file(src) if src.is_file() else ""
        rows.append({
            "package_path": str(src),
            "source_stem": stem,
            "source_basename": src.name,
            "source_sha256": src_sha,
            "CHS_target_TGI": r.get("CHS_target_TGI", ""),
            "candidate_basename": cand_basename,
            "candidate_target_path": str(cand_path),
            "sidecar_staging_path": str(sp),
            "prod_sidecar_sha256": prod_sha,
            "candidate_target_tgi_lower_lt_source": (cand_basename.lower() < src.name.lower()),
        })

    # 机器断言: candidate_basename.lower() < source_basename.lower() 每个 DEPLOY row
    bad_lt = [r for r in rows if not r["candidate_target_tgi_lower_lt_source"]]
    if bad_lt:
        print(f"[HARD-FAIL] {len(bad_lt)} DEPLOY rows 违反 candidate.lower()<source.lower(): {[r['source_basename'] for r in bad_lt]} rc=2")
        return 2

    # ---------- 建立 Mods 全树索引 (exact TGI -> set of (path, sha, parse_ok, err)) ----------
    mods_index = {}        # tgi -> list of dict(path, sha, is_loc)
    malformed_total = []
    malformed_selected_source = []
    malformed_existing_loc_candidate = []
    for p in sorted(mods_root.rglob("*.package")):
        npath = str(p.resolve())
        tgis, ok, err = stbl_tgi_set(p)
        if not ok:
            malformed_total.append({"path": npath, "parser_error": err})
            continue
        psha = sha256_file(p)
        for tgi in tgis:
            mods_index.setdefault(tgi, []).append({
                "path": npath, "sha": psha, "is_loc": is_loc_name(p.name),
            })

    # malformed 分类
    sel_norm = {str(_res(r["package_path"])) for r in rows}
    for m in malformed_total:
        if m["path"] in sel_norm:
            malformed_selected_source.append(m)
        elif is_loc_name(Path(m["path"]).name):
            malformed_existing_loc_candidate.append(m)
    print(f"[scan] malformed_total={len(malformed_total)} "
          f"malformed_selected_source={len(malformed_selected_source)} "
          f"malformed_existing_localization_candidate={len(malformed_existing_loc_candidate)}")

    # ---------- 同 TGI 重复 source group 检测 (duplicate_source_group = 属性/诊断, 非 action) ----------
    from collections import defaultdict
    tgi_groups = defaultdict(list)
    for r in rows:
        tgi_groups[r["CHS_target_TGI"]].append(r)
    dup_tgi_groups = {t: grp for t, grp in tgi_groups.items() if len(grp) > 1}
    for gid, (t, grp) in enumerate(sorted(dup_tgi_groups.items())):
        src_shas = {g["source_sha256"] for g in grp}
        prod_shas = {g["prod_sidecar_sha256"] for g in grp}
        group_safe = (len(src_shas) == 1 and len(prod_shas) == 1 and prod_shas != {""})
        for g in grp:
            g["duplicate_source_group"] = "YES"
            g["duplicate_group_id"] = f"dup{gid + 1}"
            g["duplicate_group_safe"] = "YES" if group_safe else "NO"
    for r in rows:
        r.setdefault("duplicate_source_group", "NO")
        r.setdefault("duplicate_group_id", "")
        r.setdefault("duplicate_group_safe", "")

    # ---------- 逐 row 裁决 (deployment_action 与重复诊断无关, 只看该 row 自身 source 目录) ----------
    for r in rows:
        tgi = r["CHS_target_TGI"]
        cand = _res(r["candidate_target_path"])
        cand_norm = str(cand.resolve())
        prod_sha = r["prod_sidecar_sha256"]
        matches = mods_index.get(tgi, [])  # Mods 中含该 exact TGI 的文件

        # 2) EXACT_CANDIDATE_SATISFIED (仅本 row 自身 source 目录的 candidate 位置)
        if cand.exists():
            cm = [m for m in matches if m["path"] == cand_norm]
            if cm and prod_sha and cm[0]["sha"] == prod_sha:
                r["reconcile"] = EXACT
                r["reason"] = "candidate_target_path 已存在, exact TGI 相同, SHA 与 production sidecar 一致"
                continue
            if cm:
                r["reconcile"] = HARD
                r["reason"] = "candidate_target_path 存在但字节与 production sidecar 不一致"
                continue

        # 3) EQUIVALENT_ALTNAME_SATISFIED: 仅本 row 自身 source 目录 (cand.parent) 的其他文件名 sidecar
        cand_dir = str(cand.parent.resolve())
        equi = None
        for m in matches:
            mp = Path(m["path"])
            if m["path"] != cand_norm and str(mp.parent.resolve()) == cand_dir:
                if prod_sha and m["sha"] == prod_sha and mp.name.lower() < r["source_basename"].lower():
                    equi = mp
                    break
        if equi is not None:
            r["reconcile"] = ALTNAME
            r["equivalent_path"] = str(equi)
            r["reason"] = f"同目录等价 sidecar 已存在且排序更早, 字节一致: {equi.name}"
            continue

        # 4) OLD_TEST_DIFFERENT_BYTES -> QUARANTINE_BLOCKED (localization/test 同 TGI 不同字节)
        old_tests = [m for m in matches if m["is_loc"] and (not prod_sha or m["sha"] != prod_sha)]
        if old_tests:
            r["reconcile"] = OLD_TEST
            r["quarantine_candidates"] = ";".join(m["path"] for m in old_tests)
            r["reason"] = "旧 test/localization 同 exact TGI 且字节不同, canary 前需隔离"
            continue

        # 5) HARD: 本 row 目录内同 TGI 内容不一致 (非 loc, 非自身), 且非安全重复 group 可解释
        #     (identical duplicate group 只证明其 row 间非 hard conflict, 不满足本 row)
        non_self_conflicts = [
            m for m in matches
            if not m["is_loc"]
            and m["path"] != str(_res(r["package_path"]).resolve())
            and (not prod_sha or m["sha"] != prod_sha)
        ]
        # 若本 row 属于安全重复 group, duplicate 同源/同字节不构成对【部署目标自身】的冲突
        if non_self_conflicts and r["duplicate_source_group"] == "NO":
            r["reconcile"] = HARD
            r["reason"] = f"同 exact TGI 的非本地化、非自身同字节命中 {len(non_self_conflicts)} 个且与 production 字节不一致"
            continue

        # 9) 否则: 本 row 自身 source 目录无已部署等价 sidecar => 必须 COPY_REQUIRED
        #     (即使它是某重复 group 的一员: 另一目录有同 TGI/SHA source 或 sidecar 不使本 row 满足)
        r["reconcile"] = COPY
        r["reason"] = "本 row 自身 source 目录无命中 EXACT/ALTNAME/QUARANTINE; 需独立部署生产 sidecar"

    # ---------- 汇总 ----------
    from collections import Counter
    cnt = Counter(r["reconcile"] for r in rows)
    n_copy = cnt[COPY]
    n_quar = cnt[OLD_TEST]
    n_hard = cnt[HARD]
    n_exact = cnt[EXACT]
    n_alt = cnt[ALTNAME]
    n_dup_group_rows = sum(1 for r in rows if r["duplicate_source_group"] == "YES")

    # 机器断言: EXACT + ALTNAME + COPY + (QUARANTINE_BLOCKED|HARD) == DEPLOY total
    action_total = n_exact + n_alt + n_copy + n_quar + n_hard
    if action_total != n_deploy:
        print(f"[MACHINE-ASSERT-FAIL] 部署 action 互斥合计 {action_total} != DEPLOY {n_deploy}")
        print("  禁止将 duplicate_source_group 视为互斥 action 代替 SATISFIED/COPY")
        return 2

    # fail-closed 门: HARD_CONFLICT=0 且 malformed 门通过 且 非安全重复 group=0
    unsafe_dup = [r for r in rows if r["duplicate_group_safe"] == "NO"]
    hard_mal = len(malformed_selected_source) > 0 or len(malformed_existing_loc_candidate) > 0
    ok = (n_hard == 0 and not hard_mal and not unsafe_dup)
    verdict = "PASS" if ok else "FAIL"

    # ---------- 输出 (ZERO WRITE) ----------
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["package_path", "source_basename", "source_sha256", "CHS_target_TGI",
            "candidate_basename", "candidate_target_path",
            "sidecar_staging_path", "prod_sidecar_sha256",
            "duplicate_source_group", "duplicate_group_id", "duplicate_group_safe",
            "deployment_action", "equivalent_path", "quarantine_candidates", "reason"]
    for r in rows:  # 暴露 deployment_action 列 (alias of reconcile action)
        r["deployment_action"] = r.get("reconcile", "")
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    rep.parent.mkdir(parents=True, exist_ok=True)
    L = []
    L.append("# FINAL DEPLOYMENT RECONCILIATION (ZERO WRITE TO MODS)")
    L.append("")
    L.append(f"- selection   : {sel_path}   (DEPLOY={n_deploy} NOOP={n_noop} CONFLICT_SKIP={n_conflict})")
    L.append(f"- mods_root   : {mods_root}")
    L.append(f"- sidecar_v1  : {sv1}")
    L.append(f"- sidecar_retry6: {sr6}")
    L.append("")
    L.append("## deployment target naming (冻结)")
    L.append("candidate_target_filename_for(source) = `!` + source_stem + `_CHS.package`")
    L.append("machine assert: 每一 DEPLOY row candidate_basename.lower() < source_basename.lower()  PASS")
    L.append("(旧 000_ naming policy 已废弃, 仅历史诊断证据)")
    L.append("")
    L.append("## 终局")
    for name, n in [("EXACT_CANDIDATE_SATISFIED", n_exact),
                    ("EQUIVALENT_ALTNAME_SATISFIED", n_alt),
                    ("COPY_REQUIRED", n_copy),
                    ("QUARANTINE_BLOCKED", n_quar),
                    ("HARD_CONFLICT", n_hard)]:
        L.append(f"- {name} = {n}")
    L.append(f"- machine assert: EXACT+ALTNAME+COPY+QUARANTINE+HARD == DEPLOY ({action_total}=={n_deploy})  PASS")
    L.append(f"- duplicate_source_group rows = {n_dup_group_rows} (属性/诊断, 非互斥 action)")
    L.append(f"- NOOP = {n_noop}")
    L.append(f"- CONFLICT_SKIP = {n_conflict}")
    L.append(f"- TOTAL = {n_deploy + n_noop + n_conflict}")
    L.append(f"- malformed_total_scanned = {len(malformed_total)}")
    L.append(f"- malformed_selected_source = {len(malformed_selected_source)}")
    L.append(f"- malformed_existing_localization_candidate = {len(malformed_existing_loc_candidate)}")
    L.append(f"- RECONCILIATION: {verdict}")
    L.append("")
    if unsafe_dup:
        L.append("## duplicate source group 非安全 (fail-closed)")
        for r in unsafe_dup:
            L.append(f"- dup{r['duplicate_group_id']} {r['source_basename']}  duplicate_group_safe=NO")
        L.append("")
    if malformed_selected_source or malformed_existing_loc_candidate:
        L.append("## malformed (fail-closed gate)")
        for m in malformed_selected_source:
            L.append(f"- [SELECTED_SOURCE] {m['path']}  parser_error={m['parser_error']}")
        for m in malformed_existing_loc_candidate:
            L.append(f"- [LOCALIZATION_CANDIDATE] {m['path']}  parser_error={m['parser_error']}")
        L.append("")
    L.append("## 明细 (DEPLOY rows)")
    for r in rows:
        dup_tag = f"  [dup={r['duplicate_group_id']} safe={r['duplicate_group_safe']}]" if r["duplicate_source_group"] == "YES" else ""
        L.append(f"- action={r['reconcile']:<24} {r['candidate_basename']}{dup_tag}")
        L.append(f"    src={r['source_basename']}  TGI={r['CHS_target_TGI']}")
        L.append(f"    cand={r['candidate_target_path']}")
        if r.get("prod_sidecar_sha256"):
            L.append(f"    prod_sha={r['prod_sidecar_sha256'][:16]}…")
        if r.get("equivalent_path"):
            L.append(f"    eq={r['equivalent_path']}")
        if r.get("quarantine_candidates"):
            L.append(f"    quarantine={r['quarantine_candidates']}")
        L.append(f"    reason: {r['reason']}")
    L.append("")
    L.append("## COPY_REQUIRED 明细")
    for r in rows:
        if r["reconcile"] == COPY:
            L.append(f"- {r['source_basename']}  ->  {r['candidate_target_path']}")
    L.append("")
    L.append("## QUARANTINE_REQUIRED 候选 (OLD_TEST_DIFFERENT_BYTES)")
    for r in rows:
        if r["reconcile"] == OLD_TEST and r.get("quarantine_candidates"):
            for q in r["quarantine_candidates"].split(";"):
                L.append(f"- {q}")
    L.append("")
    L.append(f"RECONCILIATION: {verdict}")
    with open(rep, "w", newline="", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    # stdout
    print(f"## RECONCILIATION: {verdict}")
    print(f"DEPLOY rows={n_deploy}")
    print(f"EXACT_CANDIDATE_SATISFIED={n_exact}")
    print(f"EQUIVALENT_ALTNAME_SATISFIED={n_alt}")
    print(f"COPY_REQUIRED={n_copy}")
    print(f"QUARANTINE_BLOCKED={n_quar}")
    print(f"HARD_CONFLICT={n_hard}")
    print(f"duplicate_source_group rows={n_dup_group_rows} (属性/诊断)")
    print(f"NOOP={n_noop}")
    print(f"CONFLICT_SKIP={n_conflict}")
    print(f"TOTAL={n_deploy + n_noop + n_conflict}")
    print(f"malformed_selected_source={len(malformed_selected_source)}")
    print(f"malformed_existing_localization_candidate={len(malformed_existing_loc_candidate)}")
    print(f"output: {out}")
    print(f"report: {rep}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
