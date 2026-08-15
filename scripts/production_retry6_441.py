#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
production_retry6_441.py —— 441 writer-transport retry (仅 6 个 FAIL/ERROR 包, staging-only)
=================================================================================================
背景 (2026-08-16, 用户批准 -m64 transport 修复):
  441 v1 中 6 个 writer FAIL 的根因 = writer `-m KEYHASH:EXPECTED:VALUE` 冒号定界契约与
  pose 源文本含 ':' 冲突 (C# IndexOf 取最前两个冒号, 无转义) -> EXPECTED 在首冒号被截断。
  真实 census 收敛: writer_would_fail = 18 colon TRANSLATE keys / 6 packages,
  与真实 production 的 6 个 FAIL package 精确一致。

本次 ONLY 修复字符串传输边界, 不改 writer 语义:
  * writer 新增 backwards-compatible `-m64 KEYHASH:B64_EXP:B64_VAL` (standard Base64 of UTF-8)。
    -m64 parser 严格 exactly 3 fields; malformed Base64 / invalid UTF-8 / invalid key -> ERROR fail-closed。
    C# 端: Base64 -> UTF-8 exact decode -> decoded_expected/decoded_value ->
    继续走原有安全门 actual_source == decoded_expected (禁止 startswith/prefix/colon-strip/normalize/trim/fuzzy)。
  * orchestrator 统一以 -m64 发送【所有】TRANSLATE modifications (不只冒号 special-case)。
  * 旧 -m 完全保留, 继续跑历史 run2 regressions。

retry6-only 路径 (本脚本):
  1) 读取 441_v1 manifest, 精确选 outcome==FAIL AND writer_result==ERROR -> 必须恰好 6 包, 否则 HARD-FAIL。
  2) retry 前重新计算这 6 包 plan (approved_pv_refs + resolve), 与
     output/production_preflight_441_v2.csv 做 exact plan parity (approved/translate/keep/unresolved)。\n     plan outcome 不得与 preflight 冲突 (runtime FAIL 除外, 见下)。
  3) 输出到独立 staging: output/production_sidecars_retry6_v1/ (不写回/覆盖 441_v1, 432 冻结不变)。
  4) 逐包: writer -m64 -> reopen -> audit_canary_pair。
  5) 最终 invariant: retry input=6 / generated=6 / writer PASS=6 / audit PASS=6 / FAIL=0 / ERROR=0 / stray=0,
     且 18 个 colon TRANSLATE keys 全部通过 exact expected-source precondition。
  6) 输出 output/production_sidecars_retry6_v1_manifest.csv + _report.md。
     不 merge staging, 不部署 Mods。

禁止: 修改 writer / 432 sidecars / 441_v1 staging / manifest / overlay / preflight / Mods / 原包。\n"""
import sys, os, csv, re, base64, subprocess, hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from production_resolver import make_production_resolver
from gen_cohort_sidecars import approved_pv_refs, resolve_all_approved, _STBL_TID, _GROUP
from production_preflight_441 import _layer_441_overlay
from phase2a_catalog import norm_text
from production_generate_441 import _resolve_with_keep441, _MAN, sha256_file

_MAN_RETRY = _MAN + [
    "colon_translate_count",          # 本包计划里走 -m64 的 TRANSLATE 且 expected 源码含 ':' 的 key 数
    "colon_translate_keys",           # 这些 key hash 逗号分隔 (hex)
    "transport",                      # 恒 "-m64"
]

_SEP = ":"  # argparse 不允许; 用于拼接不会用到


def b64(s: str) -> str:
    """Python str -> UTF-8 bytes -> standard Base64 (与 C# Convert.ToBase64String(UTF8 bytes) 双向互通)。"""
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def _rel_to_mods(src, mods_root):
    src = Path(src).resolve()
    if mods_root:
        mroot = Path(mods_root).resolve()
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
        return src.parent.name


def _try_unlink(p):
    try:
        if p.exists():
            p.unlink()
    except Exception:
        pass


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest-v1", default="output/production_sidecars_441_v1_manifest.csv",
                    help="441 v1 manifest; 从中选 outcome==FAIL AND writer_result==ERROR")
    ap.add_argument("--preflight", required=True, help="production_preflight_441_v2.csv (plan parity authority)")
    ap.add_argument("--coverage", required=True, help="output/coverage_manual_adj.csv")
    ap.add_argument("--title-final", required=True)
    ap.add_argument("--desc-final", required=True)
    ap.add_argument("--production-overlay", required=True, help="241 base")
    ap.add_argument("--production-overlay-441", required=True, help="translation_overrides.production.441.csv")
    ap.add_argument("--done", default="", help="translation_done.csv")
    ap.add_argument("--catalog", default="", help="translation_catalog.csv")
    ap.add_argument("--mods-root", default="")
    ap.add_argument("--expect-retry", type=int, default=6, help="必须选中 6 包 FAIL/ERROR, 否则 HARD-FAIL")
    ap.add_argument("--expect-colon-keys", type=int, default=18,
                    help="6 包内 colon TRANSLATE key 总数 (真实 census 收敛 18; 实现按实机推导, 仅供验收核对)")
    ap.add_argument("--staging", default="output/production_sidecars_retry6_v1")
    ap.add_argument("--manifest", default="output/production_sidecars_retry6_v1_manifest.csv")
    ap.add_argument("--report", default="output/production_sidecars_retry6_v1_report.md")
    ap.add_argument("--writer", required=True, help="SidecarBuilder.exe (已含 -m64)")
    ap.add_argument("--audit", default="")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    # ---- 0) 源守卫 + fail-closed 输出 ----
    for p, lab in [(a.manifest_v1, "manifest_v1"), (a.preflight, "preflight"),
                   (a.coverage, "coverage"), (a.title_final, "title_final"),
                   (a.desc_final, "desc_final"), (a.production_overlay, "production_overlay"),
                   (a.production_overlay_441, "production_overlay_441"),
                   (a.done, "translation_done"), (a.catalog, "translation_catalog"),
                   (a.writer, "writer")]:
        if not Path(p).exists():
            print(f"[HARD-FAIL] {lab} 文件不存在: {p}"); return 3
    for tgt in (a.manifest, a.report):
        if Path(tgt).exists() and not a.force:
            print(f"[FAIL-CLOSED] 目标已存在, 拒绝覆盖 (rc=1): {tgt}  (用新路径或 --force)"); return 1
    staging = Path(a.staging)
    if staging.exists() and any(staging.iterdir()):
        print(f"[FAIL-CLOSED] staging 已存在且非空, refuse (rc=1): {staging}"); return 1

    # ---- 1) 从 441_v1 manifest 选 FAIL/ERROR 包 ----
    with open(a.manifest_v1, encoding="utf-8-sig") as f:
        mv1 = list(csv.DictReader(f))
    fail_rows = []
    for r in mv1:
        outcome = (r.get("outcome") or "").strip()
        wres = (r.get("writer_result") or "").strip()
        if outcome == "FAIL" and wres == "ERROR":
            fail_rows.append(r)
    if len(fail_rows) != a.expect_retry:
        print(f"[HARD-FAIL] 选中的 FAIL/ERROR 包 = {len(fail_rows)}, 期望 {a.expect_retry}")
        for r in fail_rows:
            print("  -", r.get("package_path"))
        return 2
    also_fail = [r for r in mv1 if (r.get("outcome") or "").strip() == "FAIL" and (r.get("writer_result") or "").strip() != "ERROR"]
    if also_fail:
        print(f"[HARD-FAIL] 存在 outcome=FAIL 但 writer_result!=ERROR 的包 (非 transport 失败, 禁止 retry6):")
        for r in also_fail:
            print("  -", r.get("package_path"), "writer_result=", r.get("writer_result"))
        return 2

    # 校验 441_v1 其余包全 PASS/NOOP (432 冻结)
    n_pass = sum(1 for r in mv1 if (r.get("outcome") or "").strip() in ("PASS", "PASS_NOOP_KEEP_ONLY"))
    if len(mv1) != a.expect_retry + n_pass:
        print(f"[HARD-FAIL] manifest 行数 {len(mv1)} != 6 失败 + {n_pass} 成功; 存在其他 outcome"); return 2
    print(f"[select] 441_v1 manifest: 共 {len(mv1)} 包; FAIL/ERROR = {len(fail_rows)} (PASS/NOOP = {n_pass})")

    # ---- 2) 构建 resolver + 覆盖 (与 441 完全一致) ----
    try:
        resolver = make_production_resolver(
            a.title_final, a.desc_final, a.production_overlay,
            translation_done=a.done, translation_catalog=a.catalog)
        layered441, keep441, trans441 = _layer_441_overlay(resolver, a.production_overlay_441)
    except RuntimeError as ex:
        print(f"[HARD-FAIL] resolver/layer 构建失败: {ex}"); return 2

    # preflight v2 parity 权威
    with open(a.preflight, encoding="utf-8-sig") as f:
        pf = list(csv.DictReader(f))
    pf_by_path = {}
    for r in pf:
        p = (r.get("package_path") or "").strip()
        if p:
            pf_by_path[p] = r

    # ---- 3) 重新计算 6 包 plan + preflight parity ----
    plan = []
    parity_err = []
    total_colon_keys = 0
    for fr in fail_rows:
        path = (fr.get("package_path") or "").strip()
        if not path or not Path(path).exists():
            parity_err.append(f"{path}: package 不存在 (manifest 里的路径失效)"); continue
        try:
            target_tgi, key_map, approved, errs = approved_pv_refs(path)
        except Exception as ex:
            parity_err.append(f"{path}: mapping 异常 {ex}"); continue
        if errs:
            parity_err.append(f"{path}: mapping errors {errs}"); continue
        mods, keeps, errs2 = _resolve_with_keep441(approved, resolver, a.production_overlay, keep441)
        unresolved = len([e for e in errs2 if "缺译文/unresolved" in e])
        sm = len([e for e in errs2 if "source mismatch" in e])
        # plan outcome: 与 441 同判定 (TRANSLATE/KEEP 全解析 -> PASS)
        if unresolved or sm:
            outcome = "FAIL"
        elif mods == [] and keeps and len(keeps) == len(approved) and len(approved) > 0:
            outcome = "PASS_NOOP_KEEP_ONLY"
        else:
            outcome = "PASS"
        # colon TRANSLATE keys (将走 -m64 且 expected 源码含 ':')
        colon_mods = [(kh, s_text, tr) for kh, s_text, tr, _tag in mods if ":" in s_text]
        total_colon_keys += len(colon_mods)
        plan.append({"path": path, "approved": len(approved), "translate": len(mods),
                     "keep": len(keeps), "unresolved": unresolved + sm, "outcome": outcome,
                     "tgi": target_tgi, "mods": mods, "keeps": keeps, "key_map": key_map,
                     "approved_list": approved, "errs2": errs2, "colon_mods": colon_mods})
        # preflight parity (numeric plan columns 必须与 preflight v2 一致)
        prow = pf_by_path.get(path)
        if prow is None:
            parity_err.append(f"{path}: preflight v2 无此包"); continue
        for col, key in (("approved_ref_count", "approved"), ("translate_count", "translate"),
                         ("keep_count", "keep"), ("unresolved_count", "unresolved")):
            got = plan[-1][key]
            want_s = (prow.get(col) or "").strip()
            try:
                want = int(want_s)
            except Exception:
                want = want_s
            if got != want:
                parity_err.append(f"{path}: parity {col}= 实得{got!r} != 权威{want!r}")
        # plan outcome 对比: 6 包 plan 在 preflight 里应为 PASS (它们是 runtime-ERROR, 非 plan-FAIL)
        pout = (prow.get("outcome") or "").strip()
        if pout not in ("PASS", "PASS_NOOP_KEEP_ONLY"):
            parity_err.append(f"{path}: preflight outcome={pout!r}, 非 PASS 系 -> 不是 transport retry 目标")
        elif outcome != pout:
            parity_err.append(f"{path}: 重算 plan outcome={outcome!r} != preflight {pout!r}")

    if parity_err:
        print("[HARD-FAIL] retry6 plan parity 不通过 → zero sidecar writes:")
        for e in parity_err:
            print("  -", e)
        return 2
    print(f"[plan parity] 6 包 recompute 与 production_preflight_441_v2.csv 数值逐包一致: PASS")
    print(f"[colon keys] 6 包内 colon TRANSLATE keys (走 -m64) = {total_colon_keys}")
    if total_colon_keys != a.expect_colon_keys:
        print(f"[HARD-FAIL] colon TRANSLATE keys = {total_colon_keys}, 期望 {a.expect_colon_keys}")
        return 2
    if total_colon_keys < 6:
        print(f"[HARD-FAIL] colon TRANSLATE keys 过少; 6 包 writer 不应失败若 < 6"); return 2

    # ---- 4) 逐包 generation: writer -m64 -> reopen -> audit ----
    staging.mkdir(parents=True, exist_ok=True)
    rows = []
    writer_pass = audit_pass = generated = 0
    for idx, p in enumerate(plan, start=1):
        src = Path(p["path"])
        rel = _rel_to_mods(src, a.mods_root)
        slot = f"{idx:03d}"
        sidecar = staging / rel / f"{slot}_{src.stem}_CHS.package"
        row = {c: "" for c in _MAN_RETRY}
        row["package_path"] = str(src)
        row["source_sha256"] = sha256_file(src)
        row["CHS_target_TGI"] = p["tgi"]
        row["approved_ref_count"] = str(p["approved"])
        row["translate_count"] = str(p["translate"])
        row["keep_count"] = str(p["keep"])
        row["unresolved_count"] = str(p["unresolved"])
        row["colon_translate_count"] = str(len(p["colon_mods"]))
        row["colon_translate_keys"] = ",".join(f"0x{kh:08X}" for kh, _, _ in p["colon_mods"])
        row["transport"] = "-m64"

        # KEEP-only NOOP (理论 6 包无此; 防御保留)
        if p["outcome"] == "PASS_NOOP_KEEP_ONLY":
            row["outcome"] = "PASS_NOOP_KEEP_ONLY"; row["writer_result"] = "PASS_NOOP_KEEP_ONLY"
            row["audit_result"] = "SKIP_NO_OUTPUT"; rows.append(row); continue

        inst = p["tgi"].split("/")[-1]
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        args = [a.writer, "-source", str(src), "-out", str(sidecar),
                "-type", "0x%08X" % _STBL_TID, "-group", "0x%08X" % _GROUP,
                "-inst", inst, "-locale", "0x01"]
        # 统一以 -m64 发送所有 TRANSLATE mods (传输边界修复, 不只是冒号)
        for kh, s_text, tr, _tag in p["mods"]:
            args += ["-m64", f"0x{kh:08X}:{b64(s_text)}:{b64(tr)}"]
        try:
            r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=120)
        except Exception as ex:
            row["outcome"] = "FAIL"; row["writer_result"] = "ERROR"
            row["audit_result"] = "SKIP"; row["error_note"] = f"writer 调用失败: {ex}"
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
            rows.append(row); _try_unlink(sidecar); continue

        # audit (retry6 对 18 colon keys 也全量 exact expected-source 校验)
        audit_script = a.audit if a.audit else str(Path(__file__).resolve().parent / "audit_canary_pair.py")
        a_args = [sys.executable, audit_script, "-source", str(src), "-out", str(sidecar),
                  "-exp-inst", inst]
        for kh, _, _, _ in p["mods"]:
            a_args += ["-m", f"0x{kh:08X}"]
        for kh, _, tr, _ in p["mods"]:
            a_args += ["-expected-key", f"0x{kh:08X}:{tr}"]
        try:
            aout_p = subprocess.run(a_args, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=120)
        except Exception as ex:
            row["audit_result"] = "ERROR"; row["outcome"] = "FAIL"
            row["error_note"] = f"audit 调用失败: {ex}"; _try_unlink(sidecar); rows.append(row); continue
        aout = (aout_p.stdout or "") + "\n" + (aout_p.stderr or "")
        if "AUDIT=PASS" in aout and aout_p.returncode == 0:
            row["audit_result"] = "PASS"; audit_pass += 1; generated += 1; row["outcome"] = "PASS"
        elif "HARD-FAIL" in aout or "AUDIT=FAIL" in aout or "AUDIT" not in aout:
            row["audit_result"] = "ERROR" if ("HARD-FAIL" in aout or "AUDIT" not in aout) else "FAIL"
            row["outcome"] = "FAIL"
            row["error_note"] = "audit: " + aout.strip().replace("\n", " | ")[:400]
            _try_unlink(sidecar)
        row["sidecar_path"] = str(sidecar) if row["audit_result"] == "PASS" else ""
        rows.append(row)

    # ---- 5) stray check ----
    stray = 0
    good = {Path(r.get("sidecar_path", "")).resolve() for r in rows if r.get("sidecar_path")}
    for pk in staging.rglob("*.package"):
        if pk.resolve() not in good:
            stray += 1

    # ---- 6) manifest ----
    Path(a.manifest).parent.mkdir(parents=True, exist_ok=True)
    with open(a.manifest, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=_MAN_RETRY + ["error_note"])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # ---- 7) report / final invariant ----
    n_writer_fail = sum(1 for r in rows if r["writer_result"] in ("FAIL", "ERROR"))
    n_audit_fail = sum(1 for r in rows if r["audit_result"] in ("FAIL", "ERROR"))
    n_error = sum(1 for r in rows if str(r.get("error_note") or "").strip())
    # 18 colon keys exact-pass 校验: 每一 colon TRANSLATE key 都在 audit 期望集中且 audit 全 PASS
    colon_ok = all(r["audit_result"] == "PASS" for r in rows) and generated == a.expect_retry and n_audit_fail == 0
    rep = []
    rep.append("# 441 writer-transport retry6 (staging, -m64)")
    rep.append("")
    rep.append(f"- 441_v1 manifest : {a.manifest_v1}  (select outcome==FAIL AND writer_result==ERROR)")
    rep.append(f"- preflight       : {a.preflight}  (plan parity authority)")
    rep.append(f"- staging         : {staging}  (独立, 不覆盖 441_v1)")
    rep.append("")
    rep.append(f"## select / parity")
    rep.append(f"- FAIL/ERROR selected = {len(fail_rows)} (期望 {a.expect_retry})")
    rep.append(f"- 6 包 recompute plan 与 preflight v2 数值逐包一致: PASS")
    rep.append(f"- colon TRANSLATE keys (走 -m64, expected 源码含 ':') = {total_colon_keys}")
    rep.append("")
    rep.append("## generation totals (逐包 writer -m64 -> reopen -> audit_canary_pair)")
    rep.append(f"- retry input      = {len(plan)}")
    rep.append(f"- generated        = {generated}")
    rep.append(f"- writer PASS      = {writer_pass}")
    rep.append(f"- audit PASS       = {audit_pass}")
    rep.append(f"- FAIL             = {n_writer_fail} (writer) / {n_audit_fail} (audit)")
    rep.append(f"- ERROR            = {n_error}")
    rep.append(f"- stray sidecar    = {stray}")
    rep.append("")
    rep.append("## colon-key exact expected-source precondition")
    rep.append(f"- {total_colon_keys} colon TRANSLATE keys 全部通过 writer exact expected-source 校验: "
               f"{'PASS' if colon_ok else 'FAIL'}")
    rep.append("")
    ok = (len(plan) == a.expect_retry and generated == a.expect_retry
          and writer_pass == a.expect_retry and audit_pass == a.expect_retry
          and n_writer_fail == 0 and n_audit_fail == 0 and n_error == 0 and stray == 0 and colon_ok)
    rep.append(f"## RETRY6_GENERATION: {'PASS' if ok else 'FAIL'}")
    rep.append("- 6 sidecars 通过 -m64 exact-transport 生成 + audit; 未 merge staging, 未部署 Mods。" if ok
               else "- 存在阻塞; 不进入部署阶段")
    text = "\n".join(rep) + "\n"
    Path(a.report).parent.mkdir(parents=True, exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print(f"[manifest]{a.manifest}")
    print(f"[staging ]{staging}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
