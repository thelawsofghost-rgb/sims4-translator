#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
production_preflight_441.py —— 441 全量 production preflight (zero-write)
=========================================================================================
目的: 在生成任何 sidecar 之前, 对 production coverage authority 中全部
`status == ELIGIBLE_EXISTING_CHS` 的包做零写全量 preflight。

production coverage authority: `output/coverage_manual_adj.csv` (用户冻结)。
只处理 ELIGIBLE_EXISTING_CHS, 必须恰好 441 包。

复用已通过 run2 验证的冻结逻辑 (零写, 不修改):
  * approved player-visible mapping + 位置门控     -> gen_cohort_sidecars.approved_pv_refs
  * CHS-TGI exact unique / target 可读            -> 同上 (fail-fast errors)
  * duplicate target KeyHash = 0                  -> 同上
  * ProductionResolver (五源, TRANSLATE/KEEP)     -> production_resolver.make_production_resolver
  * source expected-text precondition             -> approved_pv_refs (source_text + resolve)
  * writer precondition (KEEP-only NOOP)          -> PASS_NOOP_KEEP_ONLY, 不调 writer

不修改: writer / ProductionResolver / translation payload / manual registry /
        strong gate / Mods 目录。
零写: 不调 writer、不建 package、不写 sidecar、不复制进 Sims 4 Mods。
      仅生成 preflight CSV + 报告 (显式 --out/--report; 目标已存在默认 fail-closed rc=1)。

每包至少验证:
  package exists / resolved identity readable
  exact CHS TGI exists and unique
  target CHS resource readable
  duplicate target KeyHash = 0
  approved player-visible refs only (位置门控)
  source expected-text precondition
  ProductionResolver ACTION ∈ {TRANSLATE, KEEP}
  TRANSLATE final text nonempty (resolver 返回 payload 必非空)
  unresolved = 0 / REVIEW = 0 / policy conflict = 0 / source mismatch = 0 / mapping uncertain = 0

KEEP-only package -> outcome = PASS_NOOP_KEEP_ONLY (允许, 但 preflight 阶段不写 sidecar)。

全局 invariant (真实计算, 不硬编码 approved/TRANSLATE/KEEP 总数):
  input eligible = 441 / processed = 441 / skipped = 0 / ERROR = 0
  unresolved player-visible refs = 0 / duplicate KeyHash = 0 / CHS TGI = 0
  source mismatch = 0 / resolver policy conflict = 0

输出 (默认):
  output/production_preflight_441.csv        每包共 6 列
  output/production_preflight_441_report.md  汇总
  (目标已存在 -> rc=1 refuse, --force 覆盖)
"""
import sys, os, csv, re, ast
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from production_resolver import make_production_resolver, EXPECTED_ROWS
from gen_cohort_sidecars import approved_pv_refs, resolve_all_approved
from phase2a_catalog import norm_text


def _layer_441_overlay(resolver, path):
    """把 441 专用 terminal overlay (241 无损复制 + 111 explicit) 作为最高优先级层
    注入 resolver.overlay (layer composition, 不修改 ProductionResolver 类本身)。
    返回 (added, keep_srcs, trans_srcs):
      keep_srcs  : 441 KEEP 决策的 norm_text(src) 集合
      trans_srcs : 441 TRANSLATE 决策的 norm_text(src) 集合
    行非法/非 KEEP/TRANSLATE 且空 translation -> HARD-FAIL。"""
    p = Path(path)
    if not p.exists():
        raise RuntimeError(f"preflight FAIL: --production-overlay-441 不存在: {p}")
    added = 0
    keep_srcs = set()
    trans_srcs = set()
    with open(p, encoding="utf-8-sig") as f:
        rdr = csv.DictReader(f)
        hdr = list(rdr.fieldnames or [])
        for need in ("translation_id", "source_text", "translation", "action"):
            if need not in hdr:
                raise RuntimeError(f"preflight FAIL: 441 overlay 缺列 {need!r}; 实际列={hdr}")
        for r in rdr:
            tid = (r.get("translation_id") or "").strip()
            src_text = (r.get("source_text") or "")
            src = src_text.strip()
            if not tid:
                continue
            act = (r.get("action") or r.get("status") or "").strip().upper()
            tr = (r.get("translation") or "").strip()
            nsrc = norm_text(src_text)
            if act == "KEEP":
                key = (tid, nsrc)
                resolver.overlay[key] = {
                    "translation": "", "status": "KEEP", "is_keep": True,
                    "source_text": src_text, "from": "production_overlay_441"}
                keep_srcs.add(nsrc)
                added += 1
            elif act == "TRANSLATE":
                if not tr:
                    raise RuntimeError(f"preflight FAIL: 441 overlay TRANSLATE 行空 translation: tid={tid} src={src_text!r}")
                key = (tid, nsrc)
                resolver.overlay[key] = {
                    "translation": tr, "status": "TRANSLATE", "is_keep": False,
                    "source_text": src_text, "from": "production_overlay_441"}
                trans_srcs.add(nsrc)
                added += 1
            else:
                raise RuntimeError(f"preflight FAIL: 441 overlay action 非法 {act!r} (tid={tid})")
    return added, keep_srcs, trans_srcs

_OUT_COLS = [
    "package_path",
    "approved_ref_count",
    "translate_count",
    "keep_count",
    "unresolved_count",
    "outcome",
]


def _err_cat(errs):
    """把 approved_pv_refs 的 error 字符串归类为 (dup_keyhash, tgi, other)。"""
    dup = []
    tgi = []
    other = []
    for e in errs:
        if "重复 KeyHash" in e or "重复" in e:
            dup.append(e)
        elif ("数 != 1" in e or "CHS 目标" in e or "no 0x01" in e or "无 0x01" in e
              or "解析失败" in e or "无 0x01" in e or "DBPF 解析" in e):
            tgi.append(e)
        else:
            other.append(e)
    return dup, tgi, other


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage", required=True,
                    help="production coverage authority CSV (coverage_manual_adj.csv)")
    ap.add_argument("--title-final", required=True, help="output/translation_done_title_final.csv (407)")
    ap.add_argument("--desc-final", required=True, help="output/translation_done_desc_final.csv (190)")
    ap.add_argument("--production-overlay", required=True,
                    help="output/translation_overrides.production.csv (241)")
    ap.add_argument("--production-overlay-441", default="",
                    help="output/translation_overrides.production.441.csv (可选, 241无损+111 explicit, 最高优先级层)")
    ap.add_argument("--done", default="", help="output/translation_done.csv (historical final nonempty unique 1888)")
    ap.add_argument("--catalog", default="", help="output/translation_catalog.csv (decision/index only 3540)")
    ap.add_argument("--expect-eligible", type=int, default=441,
                    help="期望 ELIGIBLE_EXISTING_CHS 包数 (默认 441)")
    ap.add_argument("--out", default="output/production_preflight_441.csv",
                    help="输出 CSV (目标已存在默认 fail-closed rc=1)")
    ap.add_argument("--report", default="output/production_preflight_441_report.md",
                    help="输出报告 md (目标已存在默认 fail-closed rc=1)")
    ap.add_argument("--force", action="store_true", help="覆盖已存在目标")
    a = ap.parse_args()

    # ---- 输出 fail-closed ----
    for target in (a.out, a.report):
        p = Path(target)
        if p.exists() and not a.force:
            print(f"[FAIL-CLOSED] 输出目标已存在, 拒绝覆盖 (rc=1): {p}   (用新路径或 --force)")
            return 1

    # ---- 禁止源守卫 + production resolver (五源) ----
    for p, lab in [(a.title_final, "title_final"), (a.desc_final, "desc_final"),
                   (a.production_overlay, "production_overlay"), (a.done, "translation_done"),
                   (a.catalog, "translation_catalog")]:
        if not Path(p).exists():
            print(f"[HARD-FAIL] {lab} 文件不存在: {p}"); return 3
    if a.production_overlay_441 and not Path(a.production_overlay_441).exists():
        print(f"[HARD-FAIL] production_overlay_441 文件不存在: {a.production_overlay_441}"); return 3
    try:
        resolver = make_production_resolver(
            a.title_final, a.desc_final, a.production_overlay,
            translation_done=a.done, translation_catalog=a.catalog)
        layered441 = 0
        keep441 = set()
        trans441 = set()
        if a.production_overlay_441:
            layered441, keep441, trans441 = _layer_441_overlay(resolver, a.production_overlay_441)
    except RuntimeError as ex:
        print(str(ex)); return 2

    # ---- 读 coverage, 过滤 ELIGIBLE ----
    cov_rows = []
    with open(a.coverage, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            cov_rows.append(r)
    status_col = "status"
    elig = [r for r in cov_rows if (r.get(status_col, "").strip() or "").upper() == "ELIGIBLE_EXISTING_CHS"]
    if len(elig) != a.expect_eligible:
        print(f"[HARD-FAIL] 期望 ELIGIBLE_EXISTING_CHS = {a.expect_eligible}, 实得 {len(elig)}  → 不进入 preflight")
        return 2

    # ---- 每包 preflight ----
    agg = {"processed": 0, "approved": 0, "translate": 0, "keep": 0, "unresolved": 0,
           "dup_keyhash": 0, "tgi": 0, "source_mismatch": 0, "policy_conflict": 0,
           "skip": 0, "error": 0, "keep_only": 0, "sidecar_needed": 0}
    pack_rows = []
    for r in elig:
        path = (r.get("package_path", "") or "").strip()
        per = {"package_path": path, "approved_ref_count": 0, "translate_count": 0,
               "keep_count": 0, "unresolved_count": 0, "outcome": "FAIL", "detail": []}

        # package exists
        if not path or not Path(path).exists():
            per["outcome"] = "FAIL"
            per["detail"].append("package 不存在")
            agg["error"] += 1
            pack_rows.append(per)
            continue
        try:
            target_tgi, key_map, approved, errs = approved_pv_refs(path)
        except Exception as ex:
            per["outcome"] = "FAIL"; per["detail"].append(f"mapping 异常: {ex}")
            agg["error"] += 1
            pack_rows.append(per)
            continue
        if errs:
            dup, tgi, other = _err_cat(errs)
            per["detail"].extend(errs)
            if dup:
                per["unresolved_count"] = len(dup); agg["dup_keyhash"] += len(dup)
            if tgi or other:
                agg["tgi"] += len(tgi)
            per["outcome"] = "FAIL"
            agg["error"] += 1
            pack_rows.append(per)
            continue

        per["approved_ref_count"] = len(approved)
        agg["approved"] += len(approved)
        # empty/whitespace-only source: ProductionResolver 恒返 MISSING; 只有 441 overlay 的
        # KEEP_EMPTY_DISPLAY 决策 (按 norm_text(src) 匹配) 才能把它定为 KEEP, 否则算 unresolved。
        mods, keeps, errs2 = resolve_all_approved(approved, resolver, a.production_overlay)
        # 由 441 overlay 冻结为 KEEP 的 unresolved (含空源) 转为 KEEP:
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
        per["translate_count"] = len(mods); agg["translate"] += len(mods)
        per["keep_count"] = len(keeps); agg["keep"] += len(keeps)
        per["unresolved_count"] = len([e for e in errs2 if "缺译文/unresolved" in e])
        source_mismatch = len([e for e in errs2 if "source mismatch" in e])
        per["detail"] = errs2
        agg["unresolved"] += per["unresolved_count"]
        agg["source_mismatch"] += source_mismatch

        # policy conflict: TRANSLATE+KEEP+unresolved+mismatch 必须 == approved (每个 approved key 恰好归一类)
        if per["translate_count"] + per["keep_count"] + per["unresolved_count"] + source_mismatch != per["approved_ref_count"]:
            per["detail"].append("policy conflict: 分类不等 approved")
            agg["policy_conflict"] += 1
            per["outcome"] = "FAIL"
        elif per["unresolved_count"] or source_mismatch:
            per["outcome"] = "FAIL"
        elif per["translate_count"] == 0 and per["keep_count"] == per["approved_ref_count"] and per["approved_ref_count"] > 0:
            per["outcome"] = "PASS_NOOP_KEEP_ONLY"
            agg["keep_only"] += 1
        else:
            per["outcome"] = "PASS"
            agg["sidecar_needed"] += 1
        agg["processed"] += 1
        pack_rows.append(per)

    agg["skip"] = 0  # 无跳过路径 (所有 441 均已处理)

    # ---- 全局结论 ----
    ok = (agg["processed"] == len(elig) == a.expect_eligible
          and agg["unresolved"] == 0 and agg["dup_keyhash"] == 0
          and agg["tgi"] == 0 and agg["source_mismatch"] == 0
          and agg["policy_conflict"] == 0 and agg["error"] == 0
          and agg["skip"] == 0)
    # REVIEW 不存在独立 tag: resolver NEVER returns REVIEW; 只有 MISSING_REVIEW(-> unresolved fail)。确认。
    # TRANSLATE final text nonempty 由 ProductionResolver 保证 (payload 非空才返回 TRANSLATE)。

    # ---- 写 CSV ----
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=_OUT_COLS + ["detail"])
        w.writeheader()
        for per in pack_rows:
            row = {c: per[c] for c in _OUT_COLS}
            row["detail"] = "; ".join(per["detail"]) if per["detail"] else ""
            w.writerow(row)

    # ---- 报告 ----
    out = []
    out.append("# 441 Production Preflight (zero-write)")
    out.append("")
    out.append(f"- coverage authority: {a.coverage}")
    if a.production_overlay_441:
        out.append(f"- 441 terminal overlay: {a.production_overlay_441} (注入 {layered441} 行最高优先级层)")
    out.append(f"- ELIGIBLE input/processed = {agg['processed']} / {agg['processed']}")
    out.append(f"- skipped unexpectedly    = {agg['skip']}")
    out.append(f"- ERROR                    = {agg['error']}")
    out.append(f"- approved player-visible refs total = {agg['approved']} (真实计算)")
    out.append(f"- TRANSLATE total          = {agg['translate']}")
    out.append(f"- KEEP total               = {agg['keep']}")
    out.append(f"- KEEP-only package count  = {agg['keep_only']}")
    out.append(f"- sidecar-needed count     = {agg['sidecar_needed']}")
    out.append("")
    out.append("## 全局 invariant")
    out.append(f"- unresolved player-visible refs = {agg['unresolved']}")
    out.append(f"- duplicate KeyHash violations   = {agg['dup_keyhash']}")
    out.append(f"- CHS TGI violations            = {agg['tgi']}")
    out.append(f"- source mismatch               = {agg['source_mismatch']}")
    out.append(f"- resolver policy conflict      = {agg['policy_conflict']}")
    out.append("")
    out.append(f"## PRODUCTION_PREFLIGHT: {'PASS' if ok else 'FAIL'}")
    if ok:
        out.append("- 441 包全部可安全进入 generation (尚无 sidecar 写出, 后续生成前再跑 gen)")
    else:
        out.append("- 存在阻塞, 不生成 sidecar")
    # per-package table
    out.append("")
    out.append("## per-package (summaries)")
    out.append("| outcome | count |")
    out.append("|---|---|")
    from collections import Counter
    oc = Counter(p["outcome"] for p in pack_rows)
    for k in ("PASS", "PASS_NOOP_KEEP_ONLY", "FAIL"):
        out.append(f"| {k} | {oc.get(k, 0)} |")
    out.append("")
    fails = [p for p in pack_rows if p["outcome"] == "FAIL"]
    if fails:
        out.append("### FAIL 明细")
        for p in fails:
            out.append(f"- `{p['package_path']}`: {'; '.join(p['detail'])}")

    text = "\n".join(out) + "\n"
    Path(a.report).parent.mkdir(parents=True, exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as f:
        f.write(text)

    print(text)
    print(f"[out]   {a.out}")
    print(f"[report]{a.report}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
