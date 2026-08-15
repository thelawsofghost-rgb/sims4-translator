#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
production_generate_441.py —— Pose Production Generation (441, staging-only)
=========================================================================================
阶段: Pose Production Generation (用户批准 2026-08-16)。目标 = 在独立 production staging
生成 438 个 sidecar + 3 个 verified NOOP (KEEP-only), 全量静态 audit PASS。

权威输入冻结 (用户):
  coverage      : output/coverage_manual_adj.csv                (production coverage authority)
  preflight     : output/production_preflight_441_v2.csv        (exact-plan parity authority)
  base overlay  : output/translation_overrides.production.csv   (241)
  441 overlay   : output/translation_overrides.production.441.csv (241无损 + 111 explicit)
  5-source      : title-final / desc-final / done / catalog      (同 v2 preflight)

铁律 (用户冻结, 本轮):
  * 不再修改: ProductionResolver / writer / sidecar_builder / translation payload /
    classification gates / manual registry / 241 base overlay / 441 terminal decisions
  * orchestrator 可增加 --production-overlay-441, 但必须在【orchestrator 层】加载 441
    terminal layer, 且实现与已 PASS 的 production_preflight_441.py 完全一致
    (导入其 _layer_441_overlay; 不修改 ProductionResolver 本体)。
  * 优先复用 run2-verified generation+writer+audit 路径; 不另造 package writer。
  * 只输出到独立 staging (如 output/production_sidecars_441_v1/);
    目标已存在且非空 -> fail-closed; 绝不写入 Sims 4 Mods。
  * 原始 package 只读, 不修改/不 rename/不 move。
  * 为避免不同目录同 basename 冲突, staging 镜像原 package 相对 Mods 的目录结构。
  * KEEP-only 3 包 -> output sidecar = NONE / writer calls = 0 / manifest outcome =
    PASS_NOOP_KEEP_ONLY (不生成空翻译 sidecar)。
  * 每包生成后立即 static audit (复用 run2 audit_canary_pair 路径)。
  * 写入前 exact-plan parity: 与 production_preflight_441_v2.csv 逐包核对
    approved/translate/keep/unresolved/outcome; 全局仍须 approved=6693 / TRANSLATE=2362 /
    KEEP=4331 / unresolved=0 / sidecar-needed=438 / KEEP-only=3; 任一 mismatch -> HARD-FAIL,
    zero sidecar writes。

每包输出 (复用 run_one 的 writer + audit 调用, 仅修 441 keep441 rescue):
  sidecar_path = staging/<rel_to_mods>/<slot>_<basename>_CHS.package (one-resource)
  writer       = SidecarBuilder.exe (COMPLETE-STBL clone, same TGI, 仅 TRANSLATE 改文本)
  audit        = audit_canary_pair.py (resource_count=1 / STBL=1 / TGI exact / reopen /
                expected-key exact-match / KEEP keys unchanged / no unexpected)

生成后最终 invariant (真实统计): input=441 / processed=441 / sidecars expected=438 /
  physically generated=438 / PASS_NOOP_KEEP_ONLY=3 / writer FAIL=0 / audit FAIL=0 /
  ERROR=0 / stray sidecar=0; 计划 approved=6693 / TRANSLATE=2362 / KEEP=4331 / unresolved=0。

本轮禁止: 复制进 Mods / 批量部署 / 删 localthumbcache / 启动游戏 / 改原包 / 重译 / 重分类。
"""
import sys, os, csv, re, ast, subprocess, hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from production_resolver import make_production_resolver
from gen_cohort_sidecars import approved_pv_refs, resolve_all_approved, _STBL_TID, _GROUP
from production_preflight_441 import _layer_441_overlay, _OUT_COLS
from phase2a_catalog import norm_text

# 每包 manifest 列 (用户要求至少这些)
_MAN = [
    "package_path", "source_sha256", "CHS_target_TGI",
    "approved_ref_count", "translate_count", "keep_count", "unresolved_count",
    "outcome", "sidecar_path", "writer_result", "audit_result",
]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _resolve_with_keep441(approved, resolver, overrides_path, keep441):
    """与 production_preflight_441 完全一致的分类: resolve + keep441 rescue。"""
    mods, keeps, errs2 = resolve_all_approved(approved, resolver, overrides_path)
    keep441_rescued = []
    if keep441:
        new_errs = []
        for e in errs2:
            m = re.search(r"key 0x([0-9A-Fa-f]+).*?source=('[^']*'|\"[^\"]*\")", e)
            if m:
                kh = int(m.group(1), 16)
                src = ast.literal_eval(m.group(2)) if m.group(2).startswith(("'", '"')) else m.group(2)
                if norm_text(src) in keep441:
                    keep441_rescued.append((kh, src))
                    continue
            new_errs.append(e)
        errs2 = new_errs
    keeps = keeps + keep441_rescued
    # policy conflict check
    un = [e for e in errs2 if "缺译文/unresolved" in e]
    sm = [e for e in errs2 if "source mismatch" in e]
    if len(mods) + len(keeps) + len(un) + len(sm) != len(approved):
        return mods, keeps, errs2 + ["policy conflict: 分类不等 approved"]
    return mods, keeps, errs2


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage", required=True)
    ap.add_argument("--preflight", required=True, help="production_preflight_441_v2.csv (parity authority)")
    ap.add_argument("--title-final", required=True)
    ap.add_argument("--desc-final", required=True)
    ap.add_argument("--production-overlay", required=True, help="241 base")
    ap.add_argument("--production-overlay-441", required=True, help="translation_overrides.production.441.csv")
    ap.add_argument("--done", default="", help="translation_done.csv")
    ap.add_argument("--catalog", default="", help="translation_catalog.csv")
    ap.add_argument("--mods-root", default="",
                    help="原始包相对 Mods 的根 (用于镜像目录结构; 默认空=相对当前工作目录)")
    ap.add_argument("--expect-eligible", type=int, default=441)
    ap.add_argument("--staging", default="output/production_sidecars_441_v1")
    ap.add_argument("--manifest", default="output/production_sidecars_441_v1_manifest.csv")
    ap.add_argument("--report", default="output/production_sidecars_441_v1_report.md")
    ap.add_argument("--writer", required=True, help="SidecarBuilder.exe 路径")
    ap.add_argument("--audit", default="",
                    help="audit 脚本路径 (默认本 dir audit_canary_pair.py 验证路径; 白盒可传 mock)")
    ap.add_argument("--expect-approved", type=int, default=6693, help="真实验收基线 (production 默认 6693)")
    ap.add_argument("--expect-translate", type=int, default=2362)
    ap.add_argument("--expect-keep", type=int, default=4331)
    ap.add_argument("--expect-sidecar-needed", type=int, default=438)
    ap.add_argument("--expect-keep-only", type=int, default=3)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    staging = Path(a.staging)

    # ---------- 0) fail-closed: manifest/report/staging 状态 ----------
    for tgt in (a.manifest, a.report):
        if Path(tgt).exists() and not a.force:
            print(f"[FAIL-CLOSED] 目标已存在, 拒绝覆盖 (rc=1): {tgt}  (用新路径或 --force)"); return 1
    if staging.exists() and any(staging.iterdir()):
        print(f"[FAIL-CLOSED] staging 目录已存在且非空, refuse (rc=1): {staging}")
        print("   绝不自动删除。请手动清空或换 --staging 后重试。"); return 1

    # ---------- 0b) 禁止源守卫 ----------
    for p, lab in [(a.title_final, "title_final"), (a.desc_final, "desc_final"),
                   (a.production_overlay, "production_overlay"),
                   (a.production_overlay_441, "production_overlay_441"),
                   (a.done, "translation_done"), (a.catalog, "translation_catalog"),
                   (a.coverage, "coverage"), (a.preflight, "preflight"),
                   (a.writer, "writer")]:
        if not Path(p).exists():
            print(f"[HARD-FAIL] {lab} 文件不存在: {p}"); return 3
    if not a.production_overlay_441:
        raise SystemExit("[HARD-FAIL] --production-overlay-441 必填 (441 terminal layer)")

    # ---------- 1) 构建 441-layered resolver (与 preflight 完全一致) ----------
    try:
        resolver = make_production_resolver(
            a.title_final, a.desc_final, a.production_overlay,
            translation_done=a.done, translation_catalog=a.catalog)
        layered441, keep441, trans441 = _layer_441_overlay(resolver, a.production_overlay_441)
    except RuntimeError as ex:
        print(f"[HARD-FAIL] resolver/layer 构建失败: {ex}"); return 2

    # ---------- 2) 读取 coverage (ELIGIBLE=441) 与 parity 权威 ----------
    with open(a.coverage, encoding="utf-8-sig") as f:
        cov = list(csv.DictReader(f))
    elig = [r for r in cov if (r.get("status", "").strip() or "").upper() == "ELIGIBLE_EXISTING_CHS"]
    if len(elig) != a.expect_eligible:
        print(f"[HARD-FAIL] 期望 ELIGIBLE={a.expect_eligible}, 实得={len(elig)}"); return 2

    with open(a.preflight, encoding="utf-8-sig") as f:
        pf = list(csv.DictReader(f))
    pf_by_path = {}
    for r in pf:
        p = (r.get("package_path", "") or "").strip()
        if p:
            pf_by_path[p] = r

    # ---------- 3) 写入前 exact-plan parity (逐包 + 全局) ----------
    plan = []  # 有序: (path, approved, translate, keep, unresolved, outcome)
    parity_err = []
    for r in elig:
        path = (r.get("package_path", "") or "").strip()
        if not path or not Path(path).exists():
            parity_err.append(f"{path}: package 不存在")
            continue
        try:
            target_tgi, key_map, approved, errs = approved_pv_refs(path)
        except Exception as ex:
            parity_err.append(f"{path}: mapping 异常 {ex}")
            continue
        if errs:
            parity_err.append(f"{path}: mapping errors {errs}")
            continue
        mods, keeps, errs2 = _resolve_with_keep441(approved, resolver, a.production_overlay, keep441)
        unresolved = len([e for e in errs2 if "缺译文/unresolved" in e])
        sm = len([e for e in errs2 if "source mismatch" in e])
        # outcome: 同 preflight 判定
        if unresolved or sm:
            outcome = "FAIL"
        elif mods == [] and keeps and len(keeps) == len(approved) and len(approved) > 0:
            outcome = "PASS_NOOP_KEEP_ONLY"
        else:
            outcome = "PASS"
        plan.append({"path": path, "approved": len(approved), "translate": len(mods),
                     "keep": len(keeps), "unresolved": unresolved + sm, "outcome": outcome,
                     "tgi": target_tgi, "mods": mods, "keeps": keeps, "key_map": key_map,
                     "approved_list": approved, "errs2": errs2})
        # 对比 parity 权威
        prow = pf_by_path.get(path)
        if prow is None:
            parity_err.append(f"{path}: preflight 无此包")
            continue
        for col, key in (("approved_ref_count", "approved"), ("translate_count", "translate"),
                         ("keep_count", "keep"), ("unresolved_count", "unresolved"),
                         ("outcome", "outcome")):
            got = plan[-1][key]
            want_s = (prow.get(col, "") or "").strip()
            try:
                want = int(want_s) if want_s.lstrip("-").isdigit() else want_s
            except Exception:
                want = want_s
            if got != want:
                parity_err.append(f"{path}: parity {col}= 实得{got!r} != 权威{want!r}")

    if parity_err:
        print("[HARD-FAIL] exact-plan parity 不通过 → zero sidecar writes:")
        for e in parity_err:
            print("  -", e)
        return 2

    # 全局 parity vs 真实验收基线 (用户冻结, 但仅作为汇总核对; 分类由逐包推导, 不硬编码)
    g = {"packages": len(plan), "approved": sum(p["approved"] for p in plan),
         "translate": sum(p["translate"] for p in plan),
         "keep": sum(p["keep"] for p in plan),
         "unresolved": sum(p["unresolved"] for p in plan)}
    g["keep_only"] = sum(1 for p in plan if p["outcome"] == "PASS_NOOP_KEEP_ONLY")
    g["sidecar_needed"] = sum(1 for p in plan if p["outcome"] == "PASS")
    g["fail"] = sum(1 for p in plan if p["outcome"] == "FAIL")
    print("[plan parity]")
    for k in ("packages", "approved", "translate", "keep", "unresolved",
              "sidecar_needed", "keep_only", "fail"):
        print(f"  {k:14}= {g[k]}")
    if g["approved"] != a.expect_approved or g["translate"] != a.expect_translate \
       or g["keep"] != a.expect_keep or g["unresolved"] != 0 \
       or g["sidecar_needed"] != a.expect_sidecar_needed or g["keep_only"] != a.expect_keep_only \
       or g["fail"] != 0:
        print("[HARD-FAIL] 全局计划与验收基线不符 → zero sidecar writes"); return 2
    print("[plan parity] PASS — 与 production_preflight_441_v2.csv 逐包一致; 可进入 generation")

    # ---------- 4) 逐包 generation (复用 run_one 的 writer + audit 调用) ----------
    staging.mkdir(parents=True, exist_ok=True)
    rows = []
    writer_pass = audit_pass = noop = generated = 0
    for idx, p in enumerate(plan, start=1):
        src = Path(p["path"])
        rel = _rel_to_mods(src, a.mods_root)
        slot = f"{idx:03d}"
        sidecar = staging / rel / f"{slot}_{src.stem}_CHS.package"
        row = {c: "" for c in _MAN}
        row["package_path"] = str(src)
        row["source_sha256"] = sha256_file(src)
        row["CHS_target_TGI"] = p["tgi"]
        row["approved_ref_count"] = str(p["approved"])
        row["translate_count"] = str(p["translate"])
        row["keep_count"] = str(p["keep"])
        row["unresolved_count"] = str(p["unresolved"])

        # KEEP-only NOOP: 不调 writer, 无 sidecar
        if p["outcome"] == "PASS_NOOP_KEEP_ONLY":
            row["outcome"] = "PASS_NOOP_KEEP_ONLY"
            row["sidecar_path"] = ""           # 必须为空
            row["writer_result"] = "PASS_NOOP_KEEP_ONLY"
            row["audit_result"] = "SKIP_NO_OUTPUT"
            noop += 1
            rows.append(row)
            continue

        # PASS: 调 writer (mods only) + audit
        inst = p["tgi"].split("/")[-1]
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        args = [a.writer, "-source", str(src), "-out", str(sidecar),
                "-type", "0x%08X" % _STBL_TID, "-group", "0x%08X" % _GROUP,
                "-inst", inst, "-locale", "0x01"]
        for kh, s_text, tr, _tag in p["mods"]:
            args += ["-m", f"0x{kh:08X}:{s_text}:{tr}"]
        try:
            r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=120)
        except Exception as ex:
            row["outcome"] = "FAIL"; row["writer_result"] = "ERROR"
            row["audit_result"] = "SKIP"; row["unresolved_count"] = row["unresolved_count"]
            row["error_note"] = f"writer 调用失败: {ex}"
            rows.append(row); continue
        out_txt = (r.stdout or "") + "\n" + (r.stderr or "")
        if "VERIFY=PASS" in out_txt and r.returncode == 0:
            row["writer_result"] = "PASS"; writer_pass += 1
        elif "VERIFY=FAIL" in out_txt:
            row["writer_result"] = "FAIL"
        else:
            row["writer_result"] = "ERROR"
        if row["writer_result"] != "PASS":
            row["outcome"] = "FAIL"
            row["error_note"] = "writer 非 PASS: " + out_txt.strip().replace("\n", " | ")[:400]
            rows.append(row)
            # 清理半成品, 不保留
            _try_unlink(sidecar)
            continue

        # 独立 audit (默认复用 run2 audit_canary_pair 验证路径; --audit 可覆盖为白盒 mock)
        audit_script = a.audit if a.audit else str(Path(__file__).resolve().parent / "audit_canary_pair.py")
        a_args = [sys.executable, audit_script,
                  "-source", str(src), "-out", str(sidecar), "-exp-inst", inst]
        for kh, _, _, _ in p["mods"]:
            a_args += ["-m", f"0x{kh:08X}"]
        for kh, _, tr, _ in p["mods"]:
            a_args += ["-expected-key", f"0x{kh:08X}:{tr}"]
        try:
            aout_p = subprocess.run(a_args, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=120)
        except Exception as ex:
            row["audit_result"] = "ERROR"; row["outcome"] = "FAIL"
            row["error_note"] = f"audit 调用失败: {ex}"
            _try_unlink(sidecar)
            rows.append(row); continue
        aout = (aout_p.stdout or "") + "\n" + (aout_p.stderr or "")
        if "AUDIT=PASS" in aout and aout_p.returncode == 0:
            row["audit_result"] = "PASS"; audit_pass += 1; generated += 1
            row["outcome"] = "PASS"
        elif "HARD-FAIL" in aout:
            row["audit_result"] = "ERROR"; row["outcome"] = "FAIL"
            row["error_note"] = "audit HARD-FAIL: " + aout.strip().replace("\n", " | ")[:400]
            _try_unlink(sidecar)
        elif "AUDIT=FAIL" in aout:
            row["audit_result"] = "FAIL"; row["outcome"] = "FAIL"
            row["error_note"] = "audit: " + aout.strip().replace("\n", " | ")[:400]
            _try_unlink(sidecar)
        else:
            row["audit_result"] = "ERROR"; row["outcome"] = "FAIL"
            row["error_note"] = "audit 无 AUDIT 标签: " + aout.strip().replace("\n", " | ")[:400]
            _try_unlink(sidecar)
        row["sidecar_path"] = str(sidecar) if row["audit_result"] == "PASS" else ""
        rows.append(row)

    # ---------- 5) stray-sidecar 检查: staging 内 *package 数量 == generated ----------
    stray = 0
    for pk in staging.rglob("*.package"):
        if pk.resolve() not in {Path(x.get("sidecar_path", "")).resolve() for x in rows if x.get("sidecar_path")}:
            stray += 1

    # ---------- 6) manifest ----------
    Path(a.manifest).parent.mkdir(parents=True, exist_ok=True)
    with open(a.manifest, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=_MAN + ["error_note"])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # ---------- 7) 报告 / 最终 invariant ----------
    n_writer_fail = sum(1 for r in rows if r["writer_result"] in ("FAIL", "ERROR"))
    n_audit_fail = sum(1 for r in rows if r["audit_result"] in ("FAIL", "ERROR"))
    n_error = sum(1 for r in rows if str(r.get("error_note") or "").strip() != "")
    rep = []
    rep.append("# 441 Production Generation (staging)")
    rep.append("")
    rep.append(f"- coverage      : {a.coverage}")
    rep.append(f"- preflight     : {a.preflight}  (parity authority)")
    rep.append(f"- 441 overlay   : {a.production_overlay_441} (注入 {layered441} 行)")
    rep.append(f"- staging       : {staging}")
    rep.append("")
    rep.append("## exact-plan parity")
    rep.append("- 逐包 approved/translate/keep/unresolved/outcome 与 preflight 一致: PASS")
    rep.append(f"- 全局: packages={g['packages']} approved={g['approved']} TRANSLATE={g['translate']} "
               f"KEEP={g['keep']} unresolved={g['unresolved']}")
    rep.append(f"- sidecar-needed={g['sidecar_needed']} KEEP-only={g['keep_only']} FAIL={g['fail']}")
    rep.append("")
    rep.append("## generation totals")
    rep.append(f"- input packages                  = {len(plan)}")
    rep.append(f"- processed                       = {len(plan)}")
    rep.append(f"- sidecars expected               = {g['sidecar_needed']}")
    rep.append(f"- sidecars physically generated   = {generated}")
    rep.append(f"- PASS_NOOP_KEEP_ONLY             = {noop}")
    rep.append(f"- writer FAIL                     = {n_writer_fail}")
    rep.append(f"- audit FAIL                      = {n_audit_fail}")
    rep.append(f"- ERROR                           = {n_error}")
    rep.append(f"- stray sidecar                   = {stray}")
    rep.append("")
    rep.append("## 汇总计划")
    rep.append(f"- approved refs = {g['approved']} / TRANSLATE = {g['translate']} / "
               f"KEEP = {g['keep']} / unresolved = {g['unresolved']}")
    rep.append("")
    rep.append("## KEEP-only 3 包 (NOOP, sidecar=NONE)")
    for r in rows:
        if r["outcome"] == "PASS_NOOP_KEEP_ONLY":
            rep.append(f"- {r['package_path']}")
    rep.append("")
    rep.append(f"## manifest: {a.manifest}")
    rep.append(f"## staging : {staging}")
    ok = (len(plan) == a.expect_eligible and generated == a.expect_sidecar_needed and noop == a.expect_keep_only
          and n_writer_fail == 0 and n_audit_fail == 0 and n_error == 0 and stray == 0
          and g["approved"] == a.expect_approved and g["translate"] == a.expect_translate
          and g["keep"] == a.expect_keep and g["unresolved"] == 0)
    rep.append("")
    rep.append(f"## PRODUCTION_GENERATION: {'PASS' if ok else 'FAIL'}")
    if ok:
        rep.append("- 438 sidecars + 3 NOOP 生成完成, 全量静态 audit PASS, 待部署 (本轮不部署)")
    else:
        rep.append("- 存在阻塞; 不进入部署阶段")
    text = "\n".join(rep) + "\n"
    Path(a.report).parent.mkdir(parents=True, exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print(f"[manifest]{a.manifest}")
    print(f"[staging ]{staging}")
    return 0 if ok else 1


def _try_unlink(p):
    try:
        if p.exists():
            p.unlink()
    except Exception:
        pass


def _rel_to_mods(src, mods_root):
    """镜像原 package 相对 Mods 的目录结构, 避免不同目录同 basename 冲突。
    mods_root 为空 -> 相对当前工作目录的相对路径。"""
    src = Path(src).resolve()
    if mods_root:
        mroot = Path(mods_root).resolve()
        try:
            rel = src.relative_to(mroot)
            return str(rel.parent)
        except ValueError:
            pass
    # fallback: 相对 cwd 或取单层父目录名
    cwd = Path.cwd().resolve()
    try:
        rel = src.relative_to(cwd)
        return str(rel.parent) if str(rel.parent) != "." else ""
    except ValueError:
        return src.parent.name


if __name__ == "__main__":
    sys.exit(main())
