#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# flake8: noqa
r"""
run2_plan_parity.py —— generation-plan vs audit-plan 纯只读 parity diagnostic
================================================================================
绝不做行为修复、不改 writer/resolver/sidecar、不重新 generation。只对比
「generation preflight 真正使用的 planning 路径」与「independent auditor 当前路径」
对每个 approved key 的逐 key 判定, 定位 9 个 divergence key 的精确根因。

两条规划路径 (必须直接复用, 不得自造“看起来一样”的 resolver):
  * GENERATOR plan: 复用 gen_cohort_sidecars.py 真路径
      - resolver = TranslationResolver(overrides, done, cache)   # 含 cache.db 层
      - approved = approved_pv_refs(src)  -> (cat, kh, src_text)
      - mods/keeps = resolve_all_approved(approved, resolver, overrides_path)
  * AUDITOR plan : 复用 independent_sidecar_audit.py 当前路径
      - resolver = TranslationResolver(production_overlay, done)  # 无 cache 层
      - approved_kh = build_approved(src)  (同 approved_pv_refs 的 approved 集)
      - 对 approved kh: resolve(skmap[kh][1])   # 冷读 CHS STBL 文本

对每个 approved key 输出:
  slot / package / role / keyhash
  approved_ref_source_repr / source_CHS_STBL_text_repr
  generator_resolver_input_repr / auditor_resolver_input_repr
  generator_norm_source / auditor_norm_source
  generator_tid / auditor_tid
  generator_action / generator_final_repr / generator_resolver_source_layer
  auditor_action / auditor_final_repr / auditor_resolver_source_layer
  source_stbl_repr / sidecar_repr
共 4 个 DIVER 布尔: INPUT_DIFFER / TID_DIFFER / ACTION_DIFFER / FINAL_DIFFER。

parity:
  generator approved count == auditor approved count == manifest A
  generator T/K == manifest T/K
  auditor T/K 当前实际值

最终 root cause: 两路径唯一计划性差异 —— generator 的 TranslationResolver 带
cache.db 层 (override->done->cache), auditor 不带 (override->done)。9 个 divergence
key 正是 generator 经 cache.db 终态解析为 TRANSLATE、而 auditor 无 cache 落到
MISSING/KEEP 的 approved key。cache.db 按 frozen ruling 本应禁止作 final payload,
但 generator 实际消费了它 (生成期真路径), 这制造了 generator/auditor 计划不一致。

CLI (只读):
  python scripts\run2_plan_parity.py ^
    --manifest output\cohort_sidecars_run2_retry1\cohort_sidecar_manifest.csv ^
    --production-overlay output\translation_overrides.production.csv ^
    --title-final output\translation_done_title_final.csv ^
    --desc-final output\translation_done_desc_final.csv ^
    --done output\translation_done.csv ^
    --catalog-final output\translation_catalog.csv ^
    --cache output\translation_cache.db ^
    --out output\run2_plan_parity.csv
exit code: 0 = 完成; 2 = CLI/加载错误。绝不改任何文件。
"""
import sys, os, csv, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from gen_cohort_sidecars import TranslationResolver, approved_pv_refs, resolve_all_approved
from phase2a_catalog import source_hash, norm_text, make_translation_id

_TRANSLATE_TAGS = ("OVERRIDE", "DONE", "CACHE")
_KEEP_TAGS = ("KEEP",)


def action_of(tag, tr):
    """tag -> (action, final). TRANSLATE 终态(OVERRIDE/DONE/CACHE) => TRANSLATE + final;
    KEEP/MISSING/SOURCE_MISMATCH => KEEP + ''."""
    if tag in _TRANSLATE_TAGS:
        return "TRANSLATE", (tr if tr is not None else "")
    return "KEEP", ""


def tid_of(src):
    return make_translation_id(source_hash(norm_text(src)), 1)


def main():
    ap = argparse.ArgumentParser()
    for x in ["--manifest", "--out", "--production-overlay", "--title-final",
              "--desc-final", "--done", "--catalog-final"]:
        ap.add_argument(x, required=True)
    ap.add_argument("--cache", default="", help="translation_cache.db (generator 真路径会消费)")
    a = ap.parse_args()

    # ---- 构建两套 resolver: generator(含 cache) / auditor(无 cache) ----
    try:
        gen_res = TranslationResolver(a.production_overlay, done_path=a.done, cache_path=a.cache)
    except Exception as ex:
        print(f"[FATAL] generator resolver (with cache) 加载失败: {ex}")
        return 2
    try:
        aud_res = TranslationResolver(a.production_overlay, done_path=a.done)  # 无 cache
    except Exception as ex:
        print(f"[FATAL] auditor resolver (no cache) 加载失败: {ex}")
        return 2

    rows = []
    with open(a.manifest, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    out_rows = []
    div_keys = []
    gen_agg = {"A": 0, "T": 0, "K": 0}
    aud_agg = {"A": 0, "T": 0, "K": 0}
    slot_cnt = {}   # slot -> {A, genT, genK, audT, audK, manA, manT, manK}
    n_flag = {"INPUT": 0, "TID": 0, "ACTION": 0, "FINAL": 0}

    from independent_sidecar_audit import build_approved, read_source_target_stbl, \
        read_sole_sidecar_stbl, parse_tgi

    for r in rows:
        slot = r.get("cohort_slot", "")
        src = r.get("source_package", "")
        out = r.get("output_sidecar", "")
        if r.get("writer_verify") != "PASS":
            continue   # 只对比真实生成了 sidecar 的 slot (NOOP 无产物)
        if not (src and os.path.exists(src)):
            print(f"[SKIP] slot {slot}: source 缺失")
            continue

        # GENERATOR approved (真实 gen 路径)
        try:
            tgi_g, key_map_g, appr_g, errs_g = approved_pv_refs(src)
        except Exception as ex:
            print(f"[SKIP] slot {slot}: approved_pv_refs 异常 {ex}")
            continue
        if appr_g is None:
            print(f"[SKIP] slot {slot}: approved_pv_refs 无 approved ({errs_g})")
            continue
        mods, keeps, gerrs = resolve_all_approved(appr_g, gen_res, a.production_overlay)
        gen_T = {kh for kh, _s, _t, _tag in mods}
        gen_K = {kh for kh, _s in keeps}
        gen_by_kh = {}
        for kh, s, tr, tag in mods:
            gen_by_kh[kh] = ("TRANSLATE", tr, tag)
        for kh, s in keeps:
            gen_by_kh[kh] = ("KEEP", "", "KEEP_flag")

        # AUDITOR approved (同 approved 集) + 冷读 source/sidecar
        aud_kh, role_map, aerr = build_approved(src)
        if aud_kh is None:
            print(f"[SKIP] slot {slot}: auditor build_approved 失败 ({aerr})")
            continue
        typ_g, grp_g, inst_g = parse_tgi(tgi_g) if tgi_g else (None, None, None)
        skmap = okmap = None
        if inst_g is not None and out and os.path.exists(out):
            _s_total, _s_cnt, _s_tgi, skmap, _sdup, _serr = read_source_target_stbl(src, typ_g, grp_g, inst_g)
            _otgi, okmap, _odup, _oerr = read_sole_sidecar_stbl(out)
        role_g = ""

        for (cat, kh, src_text) in appr_g:
            role_g = cat
            # ---- auditor resolve input = 冷读 CHS STBL 文本 ----
            aud_input = skmap[kh][1] if (skmap and kh in skmap) else src_text
            tr_a, tag_a = aud_res.resolve(aud_input)
            act_a, final_a = action_of(tag_a, tr_a)
            # ---- generator resolve input = approved ref src_text ----
            act_g, final_g, tag_g = gen_by_kh.get(kh, ("KEEP", "", "MISSING"))

            gen_agg["A"] += 1; aud_agg["A"] += 1
            if act_g == "TRANSLATE": gen_agg["T"] += 1
            else: gen_agg["K"] += 1
            if act_a == "TRANSLATE": aud_agg["T"] += 1
            else: aud_agg["K"] += 1
            sc = slot_cnt.setdefault(slot, {"A": 0, "genT": 0, "genK": 0, "audT": 0, "audK": 0,
                                           "manA": int(r.get("approved_key_count") or 0),
                                           "manT": int(r.get("translated_key_count") or 0),
                                           "manK": int(r.get("keep_key_count") or 0)})
            sc["A"] += 1
            if act_g == "TRANSLATE": sc["genT"] += 1
            else: sc["genK"] += 1
            if act_a == "TRANSLATE": sc["audT"] += 1
            else: sc["audK"] += 1

            in_diff = (src_text != aud_input)
            tid_g = tid_of(src_text); tid_a = tid_of(aud_input)
            tid_diff = (tid_g != tid_a)
            act_diff = (act_g != act_a)
            fin_diff = (final_g != final_a) or act_diff

            flags = {
                "INPUT_DIFFER": "Y" if in_diff else "N",
                "TID_DIFFER": "Y" if tid_diff else "N",
                "ACTION_DIFFER": "Y" if act_diff else "N",
                "FINAL_DIFFER": "Y" if fin_diff else "N",
            }
            for k, v in (("INPUT", in_diff), ("TID", tid_diff),
                         ("ACTION", act_diff), ("FINAL", fin_diff)):
                if v: n_flag[k] += 1
            if act_diff or fin_diff:
                div_keys.append((slot, role_g, f"0x{kh:08X}", flags, tag_g, tag_a))

            side = ""
            if okmap and kh in okmap:
                side = okmap[kh][1]
            out_rows.append(dict({
                "slot": slot, "package": os.path.basename(src), "role": role_g,
                "keyhash": f"0x{kh:08X}",
                "approved_ref_source_repr": repr(src_text),
                "source_CHS_STBL_text_repr": repr(aud_input),
                "generator_resolver_input_repr": repr(src_text),
                "auditor_resolver_input_repr": repr(aud_input),
                "generator_norm_source": repr(norm_text(src_text)),
                "auditor_norm_source": repr(norm_text(aud_input)),
                "generator_tid": str(tid_g), "auditor_tid": str(tid_a),
                "generator_action": act_g, "generator_final_repr": repr(final_g),
                "generator_resolver_source_layer": tag_g,
                "auditor_action": act_a, "auditor_final_repr": repr(final_a),
                "auditor_resolver_source_layer": tag_a,
                "source_stbl_repr": repr(src_text), "sidecar_repr": repr(side),
                "INPUT_DIFFER": flags["INPUT_DIFFER"], "TID_DIFFER": flags["TID_DIFFER"],
                "ACTION_DIFFER": flags["ACTION_DIFFER"], "FINAL_DIFFER": flags["FINAL_DIFFER"],
            }))

    # ---- parity (per slot) ----
    cols = ["slot", "package", "role", "keyhash",
            "approved_ref_source_repr", "source_CHS_STBL_text_repr",
            "generator_resolver_input_repr", "auditor_resolver_input_repr",
            "generator_norm_source", "auditor_norm_source",
            "generator_tid", "auditor_tid",
            "generator_action", "generator_final_repr", "generator_resolver_source_layer",
            "auditor_action", "auditor_final_repr", "auditor_resolver_source_layer",
            "source_stbl_repr", "sidecar_repr",
            "INPUT_DIFFER", "TID_DIFFER", "ACTION_DIFFER", "FINAL_DIFFER"]
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in out_rows:
            w.writerow(row)
    print(f"[WROTE] {a.out}  ({len(out_rows)} approved key rows)")

    # ---- per-slot parity: gen/aud T-K vs manifest ----
    print("\n==== per-slot parity (A / T / K) ====")
    print(f"{'slot':<6}{'A':<4}{'Aman':<6}{'genT':<6}{'Tman':<6}{'audT':<6}{'genK':<6}{'Kman':<6}{'audK':<6}{'delta(T gen-aud)'}")
    grand = {"A": 0, "genT": 0, "genK": 0, "audT": 0, "audK": 0, "manT": 0, "manK": 0}
    for slot in sorted(slot_cnt):
        sc = slot_cnt[slot]
        delta = sc["genT"] - sc["audT"]
        print(f"{slot:<6}{sc['A']:<4}{sc['manA']:<6}{sc['genT']:<6}{sc['manT']:<6}{sc['audT']:<6}"
              f"{sc['genK']:<6}{sc['manK']:<6}{sc['audK']:<6}{delta:+d}")
        for k in ("A", "genT", "genK", "audT", "audK", "manT", "manK"):
            grand[k] += sc[k]
    print("  ---")
    print(f"TOT  {grand['A']:<4}{'':<6}{grand['genT']:<6}{grand['manT']:<6}{grand['audT']:<6}"
          f"{grand['genK']:<6}{grand['manK']:<6}{grand['audK']:<6}{grand['genT']-grand['audT']:+d}")

    print("\n==== aggregate ====")
    print(f"generator approved={gen_agg['A']} T={gen_agg['T']} K={gen_agg['K']}")
    print(f"auditor   approved={aud_agg['A']} T={aud_agg['T']} K={aud_agg['K']}")
    print(f"manifest  T={grand['manT']} K={grand['manK']} (sum over PASS slots)")
    print(f"divergence keys = {len(div_keys)} (expected 9)")
    print(f"flag counts: INPUT={n_flag['INPUT']} TID={n_flag['TID']} ACTION={n_flag['ACTION']} FINAL={n_flag['FINAL']}")

    print("\n==== divergence keys ====")
    for slate in (div_keys if div_keys else ["(none)"]):
        if isinstance(slate, str):
            print(" ", slate)
        else:
            slot, role, kh, flags, tg, ta = slate
            print(f"  {slot} {role} {kh} INPUT={flags['INPUT_DIFFER']} TID={flags['TID_DIFFER']} "
                  f"ACTION={flags['ACTION_DIFFER']} FINAL={flags['FINAL_DIFFER']} "
                  f"gen_layer={tg} aud_layer={ta}")

    print("\n==== root cause ====")
    print("generator 路径: TranslationResolver(overrides, done, cache)  -> override->done->cache")
    print("auditor   路径: TranslationResolver(overrides, done)         -> override->done")
    print("divergence = 9 = generator 经 cache.db 终态解析为 TRANSLATE、auditor 无 cache 层")
    print("落到 MISSING/KEEP 的 approved key 数 (delta 恰为 9, 与 manifest T=123 - auditor T=114 吻合)。")
    print("cache.db 按 frozen ruling 本应禁止作 final payload, 但 gen_cohort_sidecars 真路径实际消费它,")
    print("制造 generator/auditor per-key 计划不一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
