#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""READ-ONLY deployment collision forensic — 旧 test/localization sidecar 碰撞。

输入 (均必需):
  --selection output/production_deployment_selection_441_manifest.csv
  --preflight output/production_deployment_438_manifest.csv
  --mods-root 真实 Mods 根

本工具【ZERO WRITE TO MODS】: 只读 (open/sha256/dbpf_fast.safe_parse), 不 copy/move/delete/rename/overwrite.
扫描必须以 DBPF exact STBL TGI + SHA256 为主要证据, 文件名仅辅助。

重点核查历史 test 文件 (即使未碰撞也列出供可见性):
  000_AngerFrustrationandRageflowur_CHS.package
  000_02_Gounafiers_Poses_Public_Ver (1)_CHS.package
  !Tibo131_CHS_TEST.package
  !Embracing_Faces_CHS_TEST.package

同时自动发现其他 000_* / *_CHS* / *TEST* localization sidecar 与 436 个 DEPLOY rows 的 exact TGI 冲突。

候选来源:
  1) 文件名为 localization 特征 (000_* / *_CHS* / *TEST*)
  2) 与任一 436 DEPLOY row 的 production target TGI 相撞的任何 package (即使文件名不像)

relation / recommended_action:
  EXACT_TARGET_EXISTS                  -> KEEP (字节与 production 相同) 或 REPLACE_BY_PRODUCTION (不同)
  IDENTICAL_TO_PRODUCTION              -> KEEP (已正确部署)
  OLD_TEST_SAME_TGI_DIFFERENT_BYTES    -> REMOVE_BEFORE_CANARY (旧 test 同 TGI 不同字节, 会遮蔽 production)
  SOURCE_PACKAGE_SAME_TGI              -> KEEP (普通 source 含同 TGI, 绝非删除候选)
  UNRELATED_LOCALIZATION               -> KEEP (未与 436 DEPLOY 碰撞)
  AMBIGUOUS                            -> MANUAL_REVIEW (无法解析/无法判定)

产出:
  output/deployment_collision_cleanup_candidates.csv
  output/deployment_collision_cleanup_report.md

终局:
  candidates scanned = ?
  exact old-test collisions = ?
  identical existing production targets = ?
  safe cleanup candidates = ?
  manual review = ?
  COLLISION_FORENSIC: PASS/FAIL   (PASS = 无 AMBIGUOUS, 且 safe cleanup 无 SOURCE 误伤)

fail-closed: 输出已存在则拒写除非 --force。
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

MAN_OUT = "output/deployment_collision_cleanup_candidates.csv"
REP_OUT = "output/deployment_collision_cleanup_report.md"

HISTORIC_TEST = [
    "000_AngerFrustrationandRageflowur_CHS.package",
    "000_02_Gounafiers_Poses_Public_Ver (1)_CHS.package",
    "!Tibo131_CHS_TEST.package",
    "!Embracing_Faces_CHS_TEST.package",
]

# relation keys
EXACT_TARGET = "EXACT_TARGET_EXISTS"
IDENTICAL = "IDENTICAL_TO_PRODUCTION"
OLD_TEST = "OLD_TEST_SAME_TGI_DIFFERENT_BYTES"
SOURCE_TGI = "SOURCE_PACKAGE_SAME_TGI"
UNRELATED = "UNRELATED_LOCALIZATION"
AMBIGUOUS = "AMBIGUOUS"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _res(p: str) -> Path:
    return Path(p).expanduser().resolve()


def stbl_instances(pkg: Path):
    """返回该 package 内 STBL instance id 集合 (只读)。解析失败返回 None 表示无法解析。"""
    idx, err = safe_parse(pkg)
    if err is not None or idx is None:
        return None
    insts = set()
    for e in idx.entries:
        if e.type_id == _STBL_TID and e.group_id == _STBL_GROUP and e.instance_id is not None:
            insts.add(e.instance_id)
    return insts


def is_loc_name(name: str) -> bool:
    """强 localization 标记才认为是可删除候 target 的 sidecar。

    只认 000_ 前缀 / _CHS 后缀 / _TEST 标记。
    'anger'/'gounafiers' 等词汇【不得】用来判定删除候选——它们常是原始 source/游戏资源
    (如 '[Akuiyumi] Emotions - Anger (adult - standing).package'), 绝不能成为删除候选。
    文件名仅辅助; 最终 KEEP/REMOVE 以 STBL TGI+SHA256 证据 + 该强标记为准。
    """
    low = name.lower()
    stem = name[:name.rfind(".")].lower() if "." in name else name.lower()
    return (low.startswith("000_") or stem.endswith("_chs") or "_test" in low)


def load_selection(path: Path) -> list:
    """DEPLOY rows: package_path / source_sha256 / CHS_target_TGI / sidecar_path。"""
    rows = []
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("deployment_class", "") == "DEPLOY":
                rows.append(r)
    return rows


def load_preflight(path: Path) -> dict:
    """preflight manifest: keyed by target_path, 提供 production_target TGI 等。"""
    out = {}
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            out[str(_res(r.get("target_path", "")))] = r
    return out


def target_for_source(sp: Path) -> Path:
    stem = sp.stem
    return sp.parent / f"000_{stem}_CHS.package"


def main():
    ap = argparse.ArgumentParser(description="READ-ONLY deployment collision forensic")
    ap.add_argument("--selection", required=True)
    ap.add_argument("--preflight", required=True)
    ap.add_argument("--mods-root", required=True)
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

    deploys = load_selection(_res(a.selection))
    preflight_map = load_preflight(_res(a.preflight))
    print(f"[load] DEPLOY rows = {len(deploys)}")

    # ---- 建立 production target 索引 ----
    # tgi INST -> set of (source_package_path, target_path, production_sidecar_sha256)
    prod_by_inst = {}
    prod_source_by_inst = {}
    prod_target_by_inst = {}
    sidecar_sha_by_target = {}
    for r in deploys:
        tgi = r.get("CHS_target_TGI", "")
        if "/" not in tgi:
            continue
        try:
            inst = int(tgi.split("/")[-1], 16)
        except ValueError:
            continue
        sp = _res(r.get("package_path", ""))
        tp = target_for_source(sp)
        ss = _res(r.get("sidecar_path", ""))
        ssha = sha256_file(ss) if ss.is_file() else ""
        prod_by_inst.setdefault(inst, set()).add(str(sp))
        prod_source_by_inst.setdefault(inst, set()).add(str(sp))
        prod_target_by_inst.setdefault(inst, set()).add(str(tp))
        sidecar_sha_by_target[str(tp)] = ssha

    # ---- 扫描 Mods 全树 ----
    candidates = []          # path -> info
    all_pkgs = sorted(mods_root.rglob("*.package"))
    n_scanned = 0
    for p in all_pkgs:
        n_scanned += 1
        name = p.name
        try:
            insts = stbl_instances(p)
        except Exception:
            insts = None
        is_loc = is_loc_name(name)
        collides = False
        if insts is not None:
            collides = any(i in prod_by_inst for i in insts)
        # 候选: 文件名像 localization, 或与 production target 碰撞 (即使文件名不像)
        if is_loc or collides or name in HISTORIC_TEST:
            candidates.append({"path": p, "insts": insts, "is_loc": is_loc,
                               "collides": collides, "historic": name in HISTORIC_TEST})

    print(f"[scan] packages scanned = {n_scanned}, candidates = {len(candidates)}")

    # ---- 分类 ----
    rows = []
    for cand in candidates:
        p = cand["path"]
        insts = cand["insts"]
        if insts is None:
            rows.append({"path": str(p), "sha256": "",
                         "exact_STBL_TGI": "", "matched_production_source": "",
                         "matched_production_target": "",
                         "production_sidecar_sha256": "",
                         "relation": AMBIGUOUS, "recommended_action": "MANUAL_REVIEW",
                         "reason": "无法解析 DBPF index (非 DBPF 或格式不支持)"})
            continue
        psha = sha256_file(p)
        # 该候选命中的 production TGIs
        hit_insts = sorted(insts & set(prod_by_inst))
        if not hit_insts:
            # 未与任何 production target 碰撞
            rel = UNRELATED
            action = "KEEP"
            reason = "未与任何 436 DEPLOY target 的 exact STBL TGI 碰撞"
        else:
            inst = hit_insts[0]
            srcs = sorted(prod_source_by_inst[inst])
            tgts = sorted(prod_target_by_inst[inst])
            rel, action, reason = _classify_collision(p, psha, inst, srcs, tgts,
                                                      sidecar_sha_by_target, cand)
        rows.append({
            "path": str(p), "sha256": psha,
            "exact_STBL_TGI": "0x220557DA/0x80000000/0x%016X" % (hit_insts[0] if hit_insts else 0),
            "matched_production_source": ";".join(srcs) if hit_insts else "",
            "matched_production_target": ";".join(tgts) if hit_insts else "",
            "production_sidecar_sha256": (sidecar_sha_by_target.get(tgts[0], "") if hit_insts else ""),
            "relation": rel, "recommended_action": action, "reason": reason,
        })

    # ---- 汇总 ----
    n_candidates = len(candidates)
    n_old_test = sum(1 for r in rows if r["relation"] == OLD_TEST)
    n_identical = sum(1 for r in rows if r["relation"] == IDENTICAL)
    n_safe_cleanup = sum(1 for r in rows if r["recommended_action"] == "REMOVE_BEFORE_CANARY")
    n_manual = sum(1 for r in rows if r["recommended_action"] == "MANUAL_REVIEW")
    # 安全判定: 无 AMBIGUOUS, 且无 SOURCE 被误判为删除
    src_misclass = sum(1 for r in rows
                       if r["relation"] == SOURCE_TGI and r["recommended_action"] == "REMOVE_BEFORE_CANARY")
    ok = (n_manual == 0 and src_misclass == 0)
    verdict = "PASS" if ok else "FAIL"

    # ---- 输出 (ZERO WRITE) ----
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["path", "sha256", "exact_STBL_TGI", "matched_production_source",
            "matched_production_target", "production_sidecar_sha256",
            "relation", "recommended_action", "reason"]
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    rep.parent.mkdir(parents=True, exist_ok=True)
    L = []
    L.append("# Deployment Collision Forensic (READ-ONLY, ZERO WRITE TO MODS)")
    L.append("")
    L.append(f"- selection  : {_res(a.selection)}  (DEPLOY rows = {len(deploys)})")
    L.append(f"- preflight  : {_res(a.preflight)}")
    L.append(f"- mods_root  : {mods_root}")
    L.append("")
    L.append("## 终局")
    L.append(f"candidates scanned = {n_candidates}")
    L.append(f"exact old-test collisions = {n_old_test}")
    L.append(f"identical existing production targets = {n_identical}")
    L.append(f"safe cleanup candidates = {n_safe_cleanup}")
    L.append(f"manual review = {n_manual}")
    L.append(f"COLLISION_FORENSIC: {verdict}")
    L.append("")
    L.append("## 历史 test 文件核查")
    for h in HISTORIC_TEST:
        hit = [r for r in rows if Path(r["path"]).name == h]
        if hit:
            for r in hit:
                L.append(f"- `{h}`  relation={r['relation']}  action={r['recommended_action']}")
                L.append(f"    reason: {r['reason']}")
        else:
            L.append(f"- `{h}`  未在 Mods 中发现?? 或未命中候选")
    L.append("")
    L.append("## 明细")
    for r in rows:
        L.append(f"- relation={r['relation']:<38} action={r['recommended_action']:<22} {Path(r['path']).name}")
        L.append(f"    sha256={r['sha256'][:16]}…  TGI={r['exact_STBL_TGI']}")
        if r["reason"]:
            L.append(f"    reason: {r['reason']}")
    L.append("")
    L.append("## safe cleanup candidates (REMOVE_BEFORE_CANARY)")
    for r in rows:
        if r["recommended_action"] == "REMOVE_BEFORE_CANARY":
            L.append(f"- {Path(r['path']).name}  (sha={r['sha256'][:16]}…, TGI={r['exact_STBL_TGI']})")
    L.append("")
    L.append(f"COLLISION_FORENSIC: {verdict}")
    with open(rep, "w", newline="", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    print(f"## COLLISION_FORENSIC: {verdict}")
    print(f"candidates scanned = {n_candidates}")
    print(f"exact old-test collisions = {n_old_test}")
    print(f"identical existing production targets = {n_identical}")
    print(f"safe cleanup candidates = {n_safe_cleanup}")
    print(f"manual review = {n_manual}")
    print(f"output: {out}")
    print(f"report: {rep}")
    return 0 if ok else 1


def _classify_collision(p: Path, psha: str, inst: int, srcs, tgts,
                        sidecar_sha_by_target, cand) -> tuple:
    """候选与 production target TGI 相撞时的分类。

    铁律: 凡非 localization 文件名 (000_/*_CHS*/*TEST*) 的普通 source 包, 即使含同 TGI,
    也【绝不能】成为删除候选 -> 一律 SOURCE_PACKAGE_SAME_TGI, KEEP。
    只有明确是 localization/test 特征的文件才可能被标 REMOVE_BEFORE_CANARY。
    """
    tgt = str(_res(tgts[0]))
    prod_sha = sidecar_sha_by_target.get(tgts[0], "")
    p_res = str(_res(str(p)))

    # 1) 普通 source 包 (非 localization 文件名) 含同 TGI -> 绝不删除
    if not cand["is_loc"]:
        return SOURCE_TGI, "KEEP", \
            "普通 source package 含同 STBL TGI (原始游戏/动画资源), 严禁成为删除候选"

    # 2) 已是 production target 精确位置
    if p_res == tgt:
        if prod_sha and psha == prod_sha:
            return EXACT_TARGET, "KEEP", "exact production target 已存在且字节与 production sidecar 一致"
        return EXACT_TARGET, "REPLACE_BY_PRODUCTION", \
            "exact production target 已存在但字节与 production sidecar 不同, 部署时会覆盖"

    # 3) 字节与 production sidecar 一致
    if prod_sha and psha == prod_sha:
        return IDENTICAL, "KEEP", "与 production sidecar 字节一致 (已正确部署/等价)"

    # 4) localization/test 特征文件, 同 TGI 且字节不同 -> 会遮蔽 production
    return OLD_TEST, "REMOVE_BEFORE_CANARY", \
        "旧 test/localization sidecar 与 production target 同 STBL TGI 且字节不同, canary 前应清除以免遮蔽"


if __name__ == "__main__":
    sys.exit(main())
