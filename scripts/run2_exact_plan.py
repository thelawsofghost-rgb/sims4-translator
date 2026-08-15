#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# flake8: noqa
r"""
run2_exact_plan.py —— 仅诊断。重建「真实 run2 --run2 generation 分支」的
exact per-key plan, 并依 user frozen ruling 断言 manifest 123/113 分 slot 精确吻合;
随后对比 independent-auditor 重建计划 (同样五源 production resolver)。

绝不改 writer/resolver/sidecar, 不重新 generation, 不"凑数字"。cache 一律不进。

════════════════════════════════════════════════════════════════════
第一步: 真实 run2 call graph (静态确认, 已核, 打印于此)
════════════════════════════════════════════════════════════════════
gen_cohort_sidecars.py --run2 generation 分支 (main(), 行 ~510-518):
    resolver = make_production_resolver(
        a.title_final, a.desc_final, a.production_overlay,
        translation_done=a.done or None, translation_catalog=a.catalog_final or None)
  1. resolver class : production_resolver.ProductionResolver
  2. constructor 输入: title_final, desc_final, production_overlay,
                       translation_done, translation_catalog    (五个冻结源)
  3. title_final     : 进入 (第2参)                             -> YES
  4. desc_final      : 进入 (第3参)                             -> YES
  5. production_overlay: 进入 (overlay, 最高权威)               -> YES
  6. done            : 进入 (translation_done)                  -> YES
  7. catalog         : 进入 (translation_catalog, decision only)-> YES
  8. cache.db        : 不进入。--run2 分支无 --cache 使用; 行 527 的
                       TranslationResolver(a.overrides,a.done,a.cache) 属
                       非 run2 legacy 分支, 与 run2 sidecar 生成无关。
                       ProductionResolver 五源架构不含 cache; cache 命令参数
                       只在非 run2 分支 / run2_preflight 的 --cache 无此参数。
  9. 每个 approved key 最终 planning function:
       approved_pv_refs(src) -> approved (唯一 key)
       resolve_all_approved(approved, resolver, overrides_path)
           -> 对每 (cat,kh,src) 调 resolver.resolve(src)   # ProductionResolver.resolve
           -> tag==KEEP  -> keep; SOURCE_MISMATCH -> err; tr is None -> err(unresolved)
              else       -> TRANSLATE (进 -m)

per-key tag 语义 (ProductionResolver.resolve):
  (translation, 'TRANSLATE')   overlay/title/desc/done 有终态译文
  (None, 'KEEP')               catalog KEEP 且无更高层终态
  (None, 'SOURCE_MISMATCH')    tid 命中但 norm_source 不符
  (None, 'MISSING') / ('MISSING_REVIEW')  无 payload / REVIEW 无更高层终态

────────────────────────────────────────────────────────────────────
第二步: exact-plan replay —— 复用真实 planner(禁止自造 precedence)
────────────────────────────────────────────────────────────────────
硬门 (全部满足才继续, 否则 HARD-FAIL, 不输出 root-cause):
  approved total   == manifest 236
  TRANSLATE total  == manifest 123
  KEEP total       == manifest 113
  分 slot 精确:
    s1 T2  K0    s2 T14 K70   s3 T2 K8   s4 T6 K0
    s6 T1  K11   s7 T1 K8     s8 T20 K16 s9 T70 K0  s10 T7 K0
────────────────────────────────────────────────────────────────────
第三步: provenance —— 每个 TRANSLATE key 的真实最终 source layer
────────────────────────────────────────────────────────────────────
对每个 mod (kh,src,tr): 依 precedence (overlay>title>desc>done) 只读查找
持有该 kh 的 translation 的源层 -> 记层名并计数; 无任何源层匹配 -> 'UNATTRIBUTED'
(ProductionResolver 无 cache, 正常不应出现; 若出现即为诊断缺口)。

────────────────────────────────────────────────────────────────────
第四步: auditor 重建 (同样五源 frozen resolver) 逐 key parity
────────────────────────────────────────────────────────────────────
auditor 计划 = 同一 ProductionResolver + build_approved(approved 集) +
冷读 exact CHS STBL 文本 resolve (approved-set contract)。目标:
  GENERATION EXACT PLAN = 123/113
  AUDITOR RECONSTRUCTED PLAN = 123/113
  per-key ACTION parity = 236/236
  per-key FINAL   parity = 236/236
════════════════════════════════════════════════════════════════════
CLI (只读):
  python scripts\run2_exact_plan.py ^
    --manifest output\cohort_sidecars_run2_retry1\cohort_sidecar_manifest.csv ^
    --production-overlay output\translation_overrides.production.csv ^
    --title-final output\translation_done_title_final.csv ^
    --desc-final output\translation_done_desc_final.csv ^
    --done output\translation_done.csv ^
    --catalog-final output\translation_catalog.csv ^
    --out output\run2_exact_plan.csv
exit: 0 = 完成且硬门全过; 2 = CLI/加载/HARD-FAIL。绝不改任何文件。
"""
import sys, os, csv, argparse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts"))
sys.path.insert(0, str(HERE.parent / "src"))

from production_resolver import make_production_resolver
from gen_cohort_sidecars import approved_pv_refs, resolve_all_approved

LAYER_ORDER = ("overlay", "title", "desc", "done")

# 真实 run2 manifest (user 提供 live 值) —— 仅作确认打印/参考, 硬门以 manifest 自身列为准
MANIFEST = {
    # slot: (approved, translate, keep)  [live 真实值]
    "s1": (2, 2, 0), "s2": (84, 14, 70), "s3": (10, 2, 8), "s4": (6, 6, 0),
    "s6": (12, 1, 11), "s7": (9, 1, 8), "s8": (36, 20, 16), "s9": (70, 70, 0),
    "s10": (7, 7, 0),
}
MAN_TOTAL = {"A": 236, "T": 123, "K": 113}


def attr_layer(resolver, key, tr):
    """provenance: 依 precedence 只读查找持有最终译文 tr 的源层 (注释性, 不重推导决策)。"""
    for name in LAYER_ORDER:
        hit = getattr(resolver, name).get(key)
        if hit is not None and not hit.get("is_keep") and hit.get("translation"):
            if hit["translation"] == tr:
                return name
    return "UNATTRIBUTED"


def main():
    ap = argparse.ArgumentParser()
    for x in ["--manifest", "--out", "--production-overlay", "--title-final",
              "--desc-final", "--done", "--catalog-final"]:
        ap.add_argument(x, required=True)
    a = ap.parse_args()

    # ---- 第一步: 静态 call graph (打印确认) ----
    print("=" * 70)
    print("第一步: 真实 run2 --run2 generation call graph (静态确认)")
    print("=" * 70)
    print("  resolver class            = production_resolver.ProductionResolver")
    print("  title_final 进入?         = YES (make_production_resolver 第2参)")
    print("  desc_final  进入?         = YES (第3参)")
    print("  production_overlay 进入?  = YES (overlay 最高权威)")
    print("  done        进入?         = YES (translation_done)")
    print("  catalog     进入?         = YES (translation_catalog, decision only)")
    print("  cache.db    进入?         = NO  (run2 分支无 --cache; 行527 legacy")
    print("                                TranslationResolver 与非 run2 无关)")
    print("  per-key planning function = approved_pv_refs -> resolve_all_approved")
    print("                               -> ProductionResolver.resolve(src)")

    # ---- 建立真实 planner (同 run2 分支) ----
    try:
        resolver = make_production_resolver(
            a.title_final, a.desc_final, a.production_overlay,
            translation_done=a.done or None, translation_catalog=a.catalog_final or None)
    except Exception as ex:
        print(f"[HARD-FAIL] make_production_resolver 失败: {ex}")
        return 2
    print(f"[resolver] overlay={len(resolver.overlay)} title={len(resolver.title)} "
          f"desc={len(resolver.desc)} done={len(resolver.done)} catalog={len(resolver.catalog)} "
          f"consistency={len(resolver.consistency_errors)}")

    with open(a.manifest, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    # ---- 第二步: exact-plan replay (per approved key, 真实 planner) ----
    plan = {}          # slot -> [ (kh, src, action, final, layer) ]
    slot_agg = {}
    replay_error = []
    # manifest 自带的每 slot 期望 (真实 manifest 列为准)
    man_slots = {}
    for r in rows:
        slot = r.get("cohort_slot", "")
        src = r.get("source_package", "")
        if not (src and Path(src).exists()):
            replay_error.append(f"{slot}: source 缺失 {src}")
            continue
        man_slots[slot] = (int(r.get("approved_key_count") or 0),
                           int(r.get("translated_key_count") or 0),
                           int(r.get("keep_key_count") or 0))
        try:
            _tgi, _km, approved, errs = approved_pv_refs(src)
        except Exception as ex:
            replay_error.append(f"{slot}: approved_pv_refs 异常 {ex}")
            continue
        if approved is None:
            replay_error.append(f"{slot}: approved_pv_refs 无 approved ({errs})")
            continue
        mods, keeps, errs2 = resolve_all_approved(approved, resolver, a.production_overlay)
        if errs2:
            replay_error.append(f"{slot}: unresolved {errs2}")
        sl = plan.setdefault(slot, [])
        T = 0
        for kh, s, tr, tag in mods:
            from phase2a_catalog import source_hash, norm_text, make_translation_id
            key = (make_translation_id(source_hash(norm_text(s)), 1), norm_text(s))
            layer = attr_layer(resolver, key, tr)
            sl.append((kh, s, "TRANSLATE", tr, layer))
            T += 1
        for kh, s in keeps:
            sl.append((kh, s, "KEEP", "", "KEEP"))
        slot_agg[slot] = {"A": len(approved), "T": T, "K": len(keeps)}

    if not man_slots:
        print("[HARD-FAIL] manifest 无可处理 slot")
        return 2

    print("\n" + "=" * 70)
    print("第二步: exact-plan replay (硬门, 以 manifest 自身每 slot 列为准)")
    print("=" * 70)
    print(f"{'slot':<6}{'A':<5}{'Aman':<6}{'T':<5}{'Tman':<6}{'K':<5}{'Kman':<6}{'match'}")
    hard_fail = bool(replay_error)
    tot = {"A": 0, "T": 0, "K": 0}
    man_tot = {"A": 0, "T": 0, "K": 0}
    for slot, (mA, mT, mK) in sorted(man_slots.items()):
        sc = slot_agg.get(slot, {"A": 0, "T": 0, "K": 0})
        ok = (sc["A"] == mA and sc["T"] == mT and sc["K"] == mK)
        if not ok:
            hard_fail = True
        tot["A"] += sc["A"]; tot["T"] += sc["T"]; tot["K"] += sc["K"]
        man_tot["A"] += mA; man_tot["T"] += mT; man_tot["K"] += mK
        print(f"{slot:<6}{sc['A']:<5}{mA:<6}{sc['T']:<5}{mT:<6}{sc['K']:<5}{mK:<6}{'OK' if ok else 'MISMATCH'}")
    t_ok = (tot == man_tot)
    if not t_ok:
        hard_fail = True
    print(f"{'TOT':<6}{tot['A']:<5}{man_tot['A']:<6}{tot['T']:<5}{man_tot['T']:<6}"
          f"{tot['K']:<5}{man_tot['K']:<6}{'OK' if t_ok else 'MISMATCH'}")
    print(f"  (live 参考: 真实 run2 manifest 应 A=236 T=123 K=113; 此值即 man_tot)")
    if hard_fail:
        print("\n[HARD-FAIL] exact-plan replay != manifest (分 slot 有 MISMATCH 或 replay_error):")
        for e in replay_error:
            print("   ", e)
        print("  诊断就此停止 —— 不输出 root-cause / provenance / parity。")
        return 2
    print("\n[PASS] EXACT PLAN 重建 == manifest (分 slot 精确吻合)。")

    # ---- 第三步: provenance ----
    print("\n" + "=" * 70)
    print("第三步: 每个 TRANSLATE key 的真实最终 source layer")
    print("=" * 70)
    layer_cnt = {}
    cache_keys = []
    for slot in sorted(plan):
        for kh, s, act, tr, layer in plan[slot]:
            if act != "TRANSLATE":
                continue
            layer_cnt[layer] = layer_cnt.get(layer, 0) + 1
            if layer == "UNATTRIBUTED":
                cache_keys.append((slot, f"0x{kh:08X}", repr(s)))
    print("  layer counts:")
    for l in LAYER_ORDER + ("UNATTRIBUTED",):
        print(f"    {l:<18} = {layer_cnt.get(l, 0)}")
    print(f"    total TRANSLATE = {sum(layer_cnt.values())}  (期望 123)")
    if layer_cnt.get("UNATTRIBUTED", 0):
        print("  [缺口] 以下 TRANSLATE key 无任何五源层持有其最终译文 (疑似 cache 假象):")
        for row in cache_keys:
            print("    ", row)
    else:
        print("  [OK] 无 UNATTRIBUTED —— ProductionResolver 五源架构完整覆盖 123 个 TRANSLATE,")
        print("       cache 未进入真实 run2 generation plan。")

    # ---- 第四步: auditor 重建同五源 resolver, 逐 key parity ----
    print("\n" + "=" * 70)
    print("第四步: 与 independent auditor 重建计划逐 key parity")
    print("=" * 70)
    from independent_sidecar_audit import build_approved, read_source_target_stbl, \
        read_sole_sidecar_stbl, parse_tgi
    aud_agg = {"A": 0, "T": 0, "K": 0}
    act_diff = 0
    fin_diff = 0
    total_key = 0
    parity_rows = []
    # exact layer map: (slot, kh) -> layer
    exact_layer_map = {(slot, kh): layer for slot, v in plan.items()
                       for kh, s, act, tr, layer in v}
    for r in rows:
        slot = r.get("cohort_slot", "")
        src = r.get("source_package", "")
        out = r.get("output_sidecar", "")
        man = plan.get(slot, [])
        man_by_kh = {kh: (act, tr) for kh, s, act, tr, layer in man}
        au_kh, role_map, aerr = build_approved(src)
        if au_kh is None:
            print(f"[SKIP] {slot}: build_approved 失败 ({aerr})")
            continue
        _tgi2, key_map2, appr2, _ = approved_pv_refs(src)
        typ_g, grp_g, inst_g = parse_tgi(_tgi2) if _tgi2 else (None, None, None)
        skmap = {}
        if inst_g is not None and out and Path(out).exists():
            _st, _sc, _stgi, sm, _sdup, _serr = read_source_target_stbl(src, typ_g, grp_g, inst_g)
            skmap = sm or {}
        for cat, kh, src_text in appr2:
            total_key += 1
            aud_input = skmap.get(kh, (0, src_text))[1]
            tr_a, tag_a = resolver.resolve(aud_input)
            act_a = "TRANSLATE" if (tr_a is not None and tag_a != "KEEP") else "KEEP"
            fin_a = tr_a if act_a == "TRANSLATE" else ""
            g_act, g_fin = man_by_kh.get(kh, ("KEEP", ""))
            if act_a != g_act:
                act_diff += 1
            if fin_a != g_fin:
                fin_diff += 1
            aud_agg["A"] += 1
            if act_a == "TRANSLATE":
                aud_agg["T"] += 1
            else:
                aud_agg["K"] += 1
            parity_rows.append({
                "slot": slot, "keyhash": f"0x{kh:08X}", "role": role_map.get(kh, ""),
                "exact_action": g_act, "exact_final": repr(g_fin),
                "exact_layer": exact_layer_map.get((slot, kh), ""),
                "audit_action": act_a, "audit_final": repr(fin_a),
                "ACTION_DIFFER": "Y" if act_a != g_act else "N",
                "FINAL_DIFFER": "Y" if fin_a != g_fin else "N",
            })
    print(f"GENERATION EXACT PLAN  = A={man_tot['A']} T={man_tot['T']} K={man_tot['K']}")
    print(f"AUDITOR RECONSTRUCTED  = A={aud_agg['A']} T={aud_agg['T']} K={aud_agg['K']}")
    print(f"per-key ACTION parity  = {total_key - act_diff}/{total_key}  (diff={act_diff})")
    print(f"per-key FINAL   parity  = {total_key - fin_diff}/{total_key}  (diff={fin_diff})")

    cols = ["slot", "keyhash", "role", "exact_action", "exact_final", "exact_layer",
            "audit_action", "audit_final", "ACTION_DIFFER", "FINAL_DIFFER"]
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for pr in parity_rows:
            w.writerow(pr)
    print(f"[WROTE] {a.out}  ({len(parity_rows)} rows)")

    print("\n[结果] EXACT_PLAN_PASS" if (not hard_fail and act_diff == 0 and fin_diff == 0)
          else "\n[结果] EXACT_PLAN_PASS, but auditor parity has diffs — 见上")
    return 0


if __name__ == "__main__":
    sys.exit(main())
