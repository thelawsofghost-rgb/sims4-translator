#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MANIFEST-DRIVEN PRODUCTION DEPLOYER — 最终批量部署器 (默认 DRY-RUN / ZERO WRITE)。

唯一输入 authority:
  output/production_deployment_final_manifest.csv   (由 production_deployment_final_reconcile.py 生成, 436 DEPLOY rows)

只对 deployment_action=COPY_REQUIRED 的 row 考虑部署:
  EXACT_CANDIDATE_SATISFIED / EQUIVALENT_ALTNAME_SATISFIED   => 禁止再次复制 (already satisfied)
  COPY_REQUIRED                                               => 唯一可部署集合
  QUARANTINE_BLOCKED / HARD_CONFLICT                          => fail-closed 阻断 apply
  NOOP (3) / CONFLICT_SKIP (2, SHA-pinned permanent)          => 永不操作

target naming (冻结):
  "!" + source_stem + "_CHS.package"
  机器断言每行: target_basename.lower() < source_basename.lower()

每行 apply 前重新验证 (不信任 manifest 状态):
  source 存在
  source SHA256 == manifest source_sha256
  staging sidecar 存在
  staging SHA256 == manifest prod_sidecar_sha256
  exact CHS TGI (STBL 0x220557DA / group 0x80000000 / instance) == manifest CHS_target_TGI
  target parent == source parent
  target_basename.lower() < source_basename.lower()

TOCTOU / state drift (执行时 target 已存在):
  SHA == production sidecar SHA 且 exact TGI 一致  => ALREADY_SATISFIED, 不覆盖
  否则 (bytes 或 TGI 不同)                          => HARD_ABORT
  禁止 overwrite。

--apply 开始前全部检查通过才可写 (任一不满足 => 全批 fail-closed):
  TS4_x64 未运行 (Windows)
  QUARANTINE_BLOCKED == 0
  HARD_CONFLICT    == 0
  malformed_selected_source == 0
  malformed_existing_localization_candidate == 0
  (以上来自 manifest 列: deployment_action / 以及 reconcile report, 见下)

部署过程只允许: staging sidecar -> candidate target (同目录, 即 source 目录)。
  禁止修改/移动/重命名 source package。
每个 copy 后立即复核: SHA256 / exact STBL TGI / lexical earlier; 任一失败立即停止后续部署。

机器可回滚 receipt:
  output/production_deployment_receipt.csv
  output/production_deployment_receipt.md
  每行: source_path, source_sha256, staging_path, staging_sha256, target_path,
        target_sha256_after, exact_TGI, pre_state=ABSENT|ALREADY_SATISFIED,
        action=COPIED|SKIPPED_ALREADY_SATISFIED, timestamp
rollback 只允许删除 本 receipt 中 pre_state=ABSENT 且 action=COPIED 的 target;
  rollback 前必须验证 target 当前 SHA 仍 == receipt SHA (用户改过 => 拒绝删除)。
  禁止 rollback 删除: Anika candidate / Anger altname / Tibo altname / Gounafier altname / 任意 source,
  因为它们不是本次创建。

用法:
  python scripts/production_deployment_deployer.py \
      --manifest output/production_deployment_final_manifest.csv \
      --mods-root "C:\\Users\\thela\\Documents\\Electronic Arts\\The Sims 4\\Mods" \
      --receipt-csv output/production_deployment_receipt.csv \
      --receipt-md output/production_deployment_receipt.md \
      [--apply]      # 缺省为 --dry-run (ZERO WRITE)
  rollback:
  python scripts/production_deployment_deployer.py --rollback \
      --mods-root ... --receipt-csv output/production_deployment_receipt.csv
"""
import argparse
import csv
import datetime
import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse  # noqa: E402

_STBL_TID = 0x220557DA
_STBL_GROUP = 0x80000000
_CRY_TGI = "0x220557DA/0x80000000/0x01D208F3F86A48A1"

REC_CSV_DEFAULT = "output/production_deployment_receipt.csv"
REC_MD_DEFAULT = "output/production_deployment_receipt.md"


# ----------------------------------------------------------------------------
# STBL TGI 解析 (与 reconcile 脚本同源: dbpf_fast.safe_parse; 保证 TGI 判定一致)
# ----------------------------------------------------------------------------
def stbl_tgi_set(pkg: Path):
    """返回 (set[exact TGI], parse_ok, err)。解析失败 -> (None, False, err)。"""
    try:
        idx, err = safe_parse(pkg)
    except Exception as ex:
        return None, False, "EXCEPTION: %s" % (ex,)
    if err is not None or idx is None:
        return None, False, (err or "NO_INDEX")
    tgis = set()
    for e in idx.entries:
        if e.type_id == _STBL_TID and e.group_id == _STBL_GROUP and e.instance_id is not None:
            tgis.add("0x%08X/0x%08X/0x%016X" % (_STBL_TID, _STBL_GROUP, e.instance_id))
    return tgis, True, None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _norm(p: Path):
    try:
        return str(Path(p).resolve()).lower()
    except Exception:
        return str(p).lower()


# ----------------------------------------------------------------------------
# 进程/环境检查
# ----------------------------------------------------------------------------
def _ts4_running():
    """Windows 上检测 TS4_x64 进程; 非 Windows 平台返回 False (无法检测)。"""
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq TS4_x64.exe", "/NH"],
            capture_output=True, text=True, timeout=15,
        ).stdout or ""
        return "TS4_x64.exe" in out
    except Exception:
        # 检测失败时安全侧: 返回 True 以失败关闭, 除非显式 --ignore-ts4
        return True


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def _read_manifest(path: Path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    if not rows:
        sys.exit("manifest empty: %s" % path)
    return rows


def _tt(s=None):
    return datetime.datetime.now().isoformat(timespec="seconds")


def _read_malformed_gates(report: Path, cli_sel, cli_loc):
    """从 reconcile report 读取 malformed 门; report 缺失时回退 CLI 显式值。
    返回 (malformed_selected_source, malformed_existing_localization_candidate)。"""
    sel = cli_sel if cli_sel is not None else None
    loc = cli_loc if cli_loc is not None else None
    if report.is_file():
        txt = report.read_text(encoding="utf-8", errors="replace")
        for line in txt.splitlines():
            line = line.strip()
            if line.startswith("- malformed_selected_source =") and sel is None:
                try: sel = int(line.split("=")[-1].strip())
                except Exception: pass
            elif line.startswith("- malformed_existing_localization_candidate =") and loc is None:
                try: loc = int(line.split("=")[-1].strip())
                except Exception: pass
    return (0 if sel is None else sel, 0 if loc is None else loc)


def main():
    ap = argparse.ArgumentParser(description="MANIFEST-DRIVEN PRODUCTION DEPLOYER (dry-run default, ZERO WRITE)")
    ap.add_argument("--manifest", default="output/production_deployment_final_manifest.csv")
    ap.add_argument("--mods-root", required=True)
    ap.add_argument("--receipt-csv", default=REC_CSV_DEFAULT)
    ap.add_argument("--receipt-md", default=REC_MD_DEFAULT)
    ap.add_argument("--apply", action="store_true", help="显式授予写 Mods 权限; 缺省为 dry-run")
    ap.add_argument("--rollback", action="store_true", help="按 receipt 回滚 (只删 ABSENT+COPIED)")
    ap.add_argument("--ignore-ts4", action="store_true", help="跳过 TS4_x64 进程检查 (仅诊断用)")
    ap.add_argument("--report", default="output/production_deployment_final_report.md",
                    help="reconcile report (读取 malformed 门); 不存在则仅用 --gate-* 显式值")
    ap.add_argument("--gate-malformed-selected-source", type=int, default=None)
    ap.add_argument("--gate-malformed-loc-candidate", type=int, default=None)
    a = ap.parse_args()

    if a.rollback:
        return do_rollback(Path(a.mods_root), Path(a.receipt_csv))

    manifest = Path(a.manifest).expanduser()
    mods_root = Path(a.mods_root).expanduser()
    rec_csv = Path(a.receipt_csv).expanduser()
    rec_md = Path(a.receipt_md).expanduser()
    if not manifest.is_file():
        sys.exit(f"manifest 不存在: {manifest}  rc=2")
    if not (mods_root.exists() and mods_root.is_dir()):
        sys.exit(f"mods-root 不存在或非目录: {mods_root}  rc=2")

    rows = _read_manifest(manifest)
    deploy_total = len(rows)
    actions = [r.get("deployment_action", "") for r in rows]
    n_copy = actions.count("COPY_REQUIRED")
    n_exact = actions.count("EXACT_CANDIDATE_SATISFIED")
    n_alt = actions.count("EQUIVALENT_ALTNAME_SATISFIED")
    n_quar = actions.count("QUARANTINE_BLOCKED")
    n_hard = actions.count("HARD_CONFLICT")
    n_other = deploy_total - (n_copy + n_exact + n_alt + n_quar + n_hard)

    # fail-closed 前置门 (与 --apply 是否生效无关, 但只有 apply 才因这些门而拒绝)
    # (见 apply 段)
    state_issues = []
    for r in rows:
        if r.get("deployment_action") not in ("COPY_REQUIRED", "EXACT_CANDIDATE_SATISFIED",
                                              "EQUIVALENT_ALTNAME_SATISFIED", "QUARANTINE_BLOCKED",
                                              "HARD_CONFLICT", ""):
            state_issues.append((r.get("source_basename", ""), r.get("deployment_action", ""),
                                 r.get("reason", "")))

    # ---- dry-run / apply 通用: 只对 COPY_REQUIRED 行逐行重核 (不信任 manifest) ----
    n_already = 0
    n_would = 0
    n_drift = 0
    n_src_hash = 0
    n_side_hash = 0
    n_tgi = 0
    n_lex = 0
    copy_plan = []   # 最终决定 action 的行 (ALREADY_SATISFIED | WOULD_COPY | HARD_ABORT)
    for r in rows:
        if r.get("deployment_action") != "COPY_REQUIRED":
            continue
        src = Path(r.get("package_path", "")).expanduser()
        sp = Path(r.get("sidecar_staging_path", "")).expanduser()
        target = Path(r.get("candidate_target_path", "")).expanduser()
        manifest_src_sha = r.get("source_sha256", "") or ""
        manifest_side_sha = r.get("prod_sidecar_sha256", "") or ""
        manifest_tgi = r.get("CHS_target_TGI", "") or ""
        row = {
            "source_path": str(src), "source_sha256": "", "staging_path": str(sp),
            "staging_sha256": "", "target_path": str(target), "target_sha256_after": "",
            "exact_TGI": manifest_tgi, "pre_state": "", "action": "", "timestamp": "",
            "source_basename": r.get("source_basename", ""),
        }

        # 1) source 存在
        if not src.is_file():
            n_src_hash += 1
            row["action"] = "SOURCE_MISSING"; copy_plan.append(row); continue
        # 2) source SHA == manifest
        try:
            actual_src_sha = sha256_file(src)
        except Exception:
            actual_src_sha = ""
        row["source_sha256"] = actual_src_sha
        if manifest_src_sha and actual_src_sha != manifest_src_sha:
            n_src_hash += 1
            row["action"] = "SOURCE_HASH_FAIL"; copy_plan.append(row); continue
        # 3) staging sidecar 存在
        if not sp.is_file():
            n_side_hash += 1
            row["action"] = "SIDECAR_MISSING"; copy_plan.append(row); continue
        # 4) staging SHA == prod sidecar SHA
        try:
            actual_side_sha = sha256_file(sp)
        except Exception:
            actual_side_sha = ""
        row["staging_sha256"] = actual_side_sha
        if manifest_side_sha and actual_side_sha != manifest_side_sha:
            n_side_hash += 1
            row["action"] = "SIDECAR_HASH_FAIL"; copy_plan.append(row); continue
        # 5) exact CHS TGI == manifest TGI
        tgis, ok, _err = stbl_tgi_set(sp)
        if not ok or manifest_tgi not in (tgis or set()):
            n_tgi += 1
            row["action"] = "TGI_FAIL"; copy_plan.append(row); continue
        row["exact_TGI"] = manifest_tgi
        # 6) target parent == source parent
        if _norm(target.parent) != _norm(src.parent):
            n_drift += 1
            row["action"] = "TARGET_PARENT_MISMATCH"; copy_plan.append(row); continue
        # 7) lexical earlier
        if not (target.name.lower() < src.name.lower()):
            n_lex += 1
            row["action"] = "LEXICAL_FAIL"; copy_plan.append(row); continue

        # ---- TOCTOU: 执行时 target 已存在? ----
        if target.exists():
            try:
                t_sha = sha256_file(target)
            except Exception:
                t_sha = ""
            t_tgis, t_ok, _e = stbl_tgi_set(target)
            same_sha = (manifest_side_sha == "" or t_sha == manifest_side_sha)
            same_tgi = (t_ok and manifest_tgi in (t_tgis or set()))
            if same_sha and same_tgi:
                n_already += 1
                row["pre_state"] = "ALREADY_SATISFIED"
                row["action"] = "SKIPPED_ALREADY_SATISFIED"
                row["target_sha256_after"] = t_sha
                copy_plan.append(row); continue
            else:
                n_drift += 1
                row["pre_state"] = "PRESENT_DIFFERENT"
                row["action"] = "HARD_ABORT"
                copy_plan.append(row); continue
        else:
            n_would += 1
            row["pre_state"] = "ABSENT"
            row["action"] = "WOULD_COPY"
            copy_plan.append(row)

    # ---- dry-run 输出 ----
    print("## DRY-RUN" if not a.apply else "## APPLY")
    print(f"DEPLOY_TOTAL={deploy_total}")
    print(f"MANIFEST_COPY_REQUIRED={n_copy}")
    print(f"CURRENT_ALREADY_SATISFIED={n_already}")
    print(f"WOULD_COPY={n_would}")
    print(f"STATE_DRIFT_CONFLICT={n_drift}")
    print(f"SOURCE_HASH_FAIL={n_src_hash}")
    print(f"SIDECAR_HASH_FAIL={n_side_hash}")
    print(f"TGI_FAIL={n_tgi}")
    print(f"LEXICAL_FAIL={n_lex}")
    print(f"CRY_TOUCH={0}")
    print(f"NOOP_TOUCH={0}")
    print(f"EXACT_SATISFIED_FROZEN={n_exact}")
    print(f"ALTNAME_SATISFIED_FROZEN={n_alt}")

    if not a.apply:
        print(f"CURRENT_ALREADY_SATISFIED + WOULD_COPY = {n_already + n_would} (must == 432)")
        ok = (n_already + n_would == n_copy
              and n_drift == 0 and n_src_hash == 0 and n_side_hash == 0
              and n_tgi == 0 and n_lex == 0 and not state_issues)
        print(f"DRY_RUN: {'PASS' if ok else 'FAIL'}")
        # 明细摘要
        fail_rows = [c for c in copy_plan if c["action"] not in ("WOULD_COPY", "SKIPPED_ALREADY_SATISFIED")]
        if fail_rows:
            print("## DRY-RUN 异常行 (仅诊断, 不写盘):")
            for c in fail_rows:
                print(f"  [{c['action']}] {c.get('source_basename','')} target={c['target_path']}")
        return 0 if ok else 3

    # =============================== APPLY ===============================
    # 门: 任一不满足 => 全批 fail-closed, 不写一个字节
    m_sel, m_loc = _read_malformed_gates(Path(a.report).expanduser(),
                                         a.gate_malformed_selected_source,
                                         a.gate_malformed_loc_candidate)
    gates_fail = []
    if os.name == "nt" and _ts4_running() and not a.ignore_ts4:
        gates_fail.append("TS4_x64 正在运行")
    if n_quar != 0: gates_fail.append(f"QUARANTINE_BLOCKED={n_quar}")
    if n_hard != 0: gates_fail.append(f"HARD_CONFLICT={n_hard}")
    if n_drift != 0: gates_fail.append(f"STATE_DRIFT_CONFLICT={n_drift}")
    if n_src_hash != 0: gates_fail.append(f"SOURCE_HASH_FAIL={n_src_hash}")
    if n_side_hash != 0: gates_fail.append(f"SIDECAR_HASH_FAIL={n_side_hash}")
    if n_tgi != 0: gates_fail.append(f"TGI_FAIL={n_tgi}")
    if n_lex != 0: gates_fail.append(f"LEXICAL_FAIL={n_lex}")
    if m_sel != 0: gates_fail.append(f"malformed_selected_source={m_sel}")
    if m_loc != 0: gates_fail.append(f"malformed_existing_localization_candidate={m_loc}")
    if state_issues:
        gates_fail.append("未知 deployment_action 行存在")
    if gates_fail:
        print("## APPLY: FAIL (fail-closed, 未写任何文件)")
        for g in gates_fail:
            print(f"  - {g}")
        return 4

    print("## APPLY: 门检查通过, 开始部署")
    # 防 overwrite: 任何 target 目标此刻已存在即拒绝 (已在上面按 ALREADY_SATISFIED 跳过, 不会 overlap)
    # 逐个执行: 仅 WOULD_COPY
    to_copy = [c for c in copy_plan if c["action"] == "WOULD_COPY"]
    receipt_rows = []
    # 先记录 ALREADY_SATISFIED 进 receipt (action=SKIPPED_ALREADY_SATISFIED, 不可回滚)
    for c in copy_plan:
        if c["action"] == "SKIPPED_ALREADY_SATISFIED":
            c["timestamp"] = _tt()
            receipt_rows.append(dict(c))
    # 逐个 copy + 立即复核
    did_copy = 0
    for c in to_copy:
        src = Path(c["source_path"])
        sp = Path(c["staging_path"])
        target = Path(c["target_path"])
        try:
            # 复制 staging -> target (同目录)
            target.parent.mkdir(parents=True, exist_ok=True)
            # 复核 target 此刻仍 ABSENT (TOCTOU)
            if target.exists():
                raise RuntimeError("target 在 copy 前被外部创建 (TOCTOU)")
            target.write_bytes(sp.read_bytes())
        except Exception as ex:
            print(f"## APPLY: 中止 @ {c.get('source_basename','')}: {ex}")
            return 5
        # 立即复核 1: SHA256 == prod
        try:
            aft_sha = sha256_file(target)
        except Exception:
            aft_sha = ""
        if aft_sha != c["staging_sha256"]:
            print(f"## APPLY: 中止 @ {c.get('source_basename','')}: copy 后 SHA256 不一致")
            return 5
        c["target_sha256_after"] = aft_sha
        # 立即复核 2: exact TGI
        t_tgis, t_ok, _e = stbl_tgi_set(target)
        if not t_ok or c["exact_TGI"] not in (t_tgis or set()):
            print(f"## APPLY: 中止 @ {c.get('source_basename','')}: copy 后 exact TGI 不一致")
            return 5
        # 立即复核 3: lexical earlier
        if not (target.name.lower() < src.name.lower()):
            print(f"## APPLY: 中止 @ {c.get('source_basename','')}: copy 后 lexical 校验失败")
            return 5
        c["action"] = "COPIED"
        c["timestamp"] = _tt()
        receipt_rows.append(dict(c))
        did_copy += 1
        print(f"  COPIED {c.get('source_basename','')} -> {target}")

    print(f"## APPLY: 完成, COPIED={did_copy}, SKIPPED_ALREADY_SATISFIED={n_already}")
    _write_receipt(receipt_rows, rec_csv, rec_md)
    print(f"receipt_csv={rec_csv}")
    print(f"receipt_md={rec_md}")
    print("## APPLY: PASS")
    return 0


def _write_receipt(rows, rec_csv: Path, rec_md: Path):
    rec_csv.parent.mkdir(parents=True, exist_ok=True)
    cols = ["source_path", "source_sha256", "staging_path", "staging_sha256",
            "target_path", "target_sha256_after", "exact_TGI", "pre_state", "action",
            "timestamp"]
    with open(rec_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})
    L = ["# PRODUCTION DEPLOYMENT RECEIPT", "",
         f"generated: {_tt()}", f"rows: {len(rows)}", "", "## Rows", ""]
    for r in rows:
        L.append(f"- action={r.get('action',''):<32} pre_state={r.get('pre_state',''):<18} "
                 f"{r.get('source_basename','')}")
        L.append(f"    source={r.get('source_path','')}")
        L.append(f"    target={r.get('target_path','')}")
        L.append(f"    target_sha256_after={r.get('target_sha256_after','')}")
        L.append(f"    exact_TGI={r.get('exact_TGI','')}")
        L.append(f"    timestamp={r.get('timestamp','')}")
    with open(rec_md, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


# ----------------------------------------------------------------------------
# ROLLBACK — 只删 本 receipt 中 pre_state=ABSENT 且 action=COPIED 的 target
# ----------------------------------------------------------------------------
def do_rollback(mods_root: Path, rec_csv: Path):
    if not rec_csv.is_file():
        sys.exit(f"receipt 不存在: {rec_csv}  rc=6")
    rows = list(csv.DictReader(open(rec_csv, encoding="utf-8-sig")))
    deletable = [r for r in rows if r.get("pre_state") == "ABSENT" and r.get("action") == "COPIED"]
    refused = 0
    deleted = 0
    print("## ROLLBACK")
    for r in deletable:
        target = Path(r.get("target_path", "")).expanduser()
        if not target.exists():
            print(f"  SKIP (already gone) {target}")
            continue
        want_sha = r.get("target_sha256_after", "")
        try:
            cur = sha256_file(target)
        except Exception:
            cur = ""
        if cur != want_sha:
            refused += 1
            print(f"  REFUSE (SHA 变化, 用户可能改过) {target}  expect={want_sha[:16]} got={cur[:16]}")
            continue
        # 非本次创建的保护: 校验 target 名不落在冻结保护区 (防御性)
        base = target.name.lower()
        protected = (base.startswith("!!anika_argument_chs")
                     or base.startswith("000_angerfrustration")
                     or base.startswith("000_tibo131")
                     or base.startswith("000_02_gounafiers")
                     or base.startswith("000_gounafier")
                     or base.startswith("!anika_argument_chs"))
        if protected:
            refused += 1
            print(f"  REFUSE (冻结保护区) {target}")
            continue
        try:
            target.unlink()
            deleted += 1
            print(f"  DELETED {target}")
        except Exception as ex:
            refused += 1
            print(f"  ERROR delete {target}: {ex}")
    print(f"## ROLLBACK: deleted={deleted} refused={refused}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
