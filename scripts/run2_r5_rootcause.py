#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# flake8: noqa
r"""
run2_r5_rootcause.py —— 纯只读、逐 KeyHash 的 R5 root-cause dump (run2 retry1 失败诊断)
===============================================================================
绝不重新 generation, 绝不调用 writer, 绝不写任何 package/sidecar/Mods。
只读: 从磁盘重新读取 original source package + generated sidecar, 对每个 key 独立重算
production resolver 最终译文 (不动 resolver / auditor / writer), 输出逐行 CSV 与分类汇总。

目的: 定位 6 个 FAIL slot (2/3/4/8/9/10) 的 R5 根因 —— 是 resolver 判定与 writer 实际
写入不一致 (TRANSLATE_NOT_APPLIED / WRONG_VALUE), 还是 KEEP 键被误改 (KEEP_CHANGED),
还是源文本经过 norm 后命中但 exact 不同 (normalization 差异被视觉隐藏)。

输出(CSV 列, 每个 approved target key 一行):
  slot / package / role / keyhash
  resolver_action (KEEP | TRANSLATE)
  resolver_source  (KEEP_flag | OVERRIDE | DONE | CACHE | MISSING | SOURCE_MISMATCH)
  resolver_final   (最终译文, 用 repr; KEEP->'' ; 无终态->'')
  source_stbl_text / sidecar_stbl_text  (均 repr)
  manifest_action / manifest expected-final  (manifest 为聚合计数, 无 per-key -> 留空 '')
  source_eq_final / source_eq_sidecar / final_eq_sidecar  (exact ==)
  source_changed    (sidecar != source)

按 unique KeyHash 分类(互斥, 以 resolver_action 为轴):
  TRANSLATE_CHANGED_OK     action=TRANSLATE final!=source and sidecar==final
  TRANSLATE_NOOP           action=TRANSLATE final==source and sidecar==source
  TRANSLATE_NOT_APPLIED    action=TRANSLATE final!=source and sidecar==source
  TRANSLATE_WRONG_VALUE    action=TRANSLATE sidecar!=final and sidecar!=source
  KEEP_OK                  action=KEEP    sidecar==source
  KEEP_CHANGED             action=KEEP    sidecar!=source

每 slot aggregate:
  resolver TRANSLATE count
  TRANSLATE_CHANGED_OK / TRANSLATE_NOOP / TRANSLATE_NOT_APPLIED / TRANSLATE_WRONG_VALUE
  KEEP_CHANGED
  physical changed-key count   (cold: sidecar != source 的 key 数)
  manifest modified_key_count  (manifest 聚合列)

对指定已知 KeyHash (slot2/4/8 共 9 个) 额外输出精确 repr 对比:
  len(text) / UTF-8 byte length / NFC equality / exact Python equality
  审计以 exact stored string 为准, 不放宽。

CLI (全只读; 与 independent_sidecar_audit 同一 CLI 形状):
  python scripts\run2_r5_rootcause.py ^
    --manifest output\cohort_sidecars_run2_retry1\cohort_sidecar_manifest.csv ^
    --title-final output\translation_done_title_final.csv ^
    --desc-final output\translation_done_desc_final.csv ^
    --production-overlay output\translation_overrides.production.csv ^
    --done output\translation_done.csv ^
    --catalog-final output\translation_catalog.csv ^
    --out output\run2_r5_rootcause.csv
  # 可选: --known-keyhash 0x672FCF0A,0x21049F5E,... 仅对比指定 kh
exit code: 0 = 完成; 2 = CLI/加载错误。绝不因审计结论而改任何文件。
"""
import sys, os, csv, argparse, unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from dbpf_fast import safe_parse
import audit_canary_pair as AC   # canonical zlib 解压 STBL 读取 (cold)

_STBL_TID = 0x220557DA
_GROUP = 0x80000000

# resolver action 终态分类 (与 independent_sidecar_audit / gen_cohort_sidecars 完全一致)
_TRANSLATE_TAGS = ("OVERRIDE", "DONE", "CACHE")
_KEEP_TAGS = ("KEEP",)
_UNRESOLVED_TAGS = ("MISSING", "SOURCE_MISMATCH")


def _fmt_tgi(typ, group, inst):
    return f"0x{typ:08X}/0x{group:08X}/0x{inst:016X}"


def read_source_target_stbl(path, typ, group, inst):
    """cold 读取 SOURCE target CHS STBL (同 independent_sidecar_audit). 返回 (kh_map, errors)."""
    idx, err = safe_parse(path)
    if err or idx is None:
        return None, [f"parse error: {err}"]
    stbl = [e for e in idx.entries if e.type_id == _STBL_TID]
    matches = [e for e in stbl if e.type_id == typ and e.group_id == group and e.instance_id == inst]
    if len(matches) != 1:
        return None, [f"exact target TGI match count = {len(matches)} (STBL 总数={len(stbl)})"]
    e = matches[0]
    ver, comp, resv, slen, keys, rerr = AC.read_one_stbl(path, e)
    if rerr is not None or keys is None:
        return None, [f"STBL 读取失败: {rerr}"]
    return {kh: (fl, txt) for kh, fl, txt in keys}, []


def read_sole_sidecar_stbl(path):
    """cold 读取 SIDECAR sole STBL (同 independent_sidecar_audit). 返回 (kh_map, errors)."""
    idx, err = safe_parse(path)
    if err or idx is None:
        return None, [f"parse error: {err}"]
    stbl = [e for e in idx.entries if e.type_id == _STBL_TID]
    if len(idx.entries) != 1 or len(stbl) != 1:
        return None, [f"RESOURCE={len(idx.entries)} STBL={len(stbl)} (期望 1/1)"]
    e = stbl[0]
    ver, comp, resv, slen, keys, rerr = AC.read_one_stbl(path, e)
    if rerr is not None or keys is None:
        return None, [f"STBL 读取失败: {rerr}"]
    return {kh: (fl, txt) for kh, fl, txt in keys}, []


def parse_tgi(s):
    if not s:
        return None
    parts = [p.strip() for p in s.split("/")]
    if len(parts) != 3:
        return None
    try:
        return int(parts[0], 16), int(parts[1], 16), int(parts[2], 16)
    except Exception:
        return None


def classify(action, final, source, sidecar):
    """互斥分类 (按用户冻结口径). source/sidecar 为 exact stored str."""
    if action == "TRANSLATE":
        if final != source:
            if sidecar == final:
                return "TRANSLATE_CHANGED_OK"
            if sidecar == source:
                return "TRANSLATE_NOT_APPLIED"
            return "TRANSLATE_WRONG_VALUE"   # sidecar!=final 且 sidecar!=source
        # final == source
        if sidecar == source:
            return "TRANSLATE_NOOP"
        return "TRANSLATE_WRONG_VALUE"        # 仍不符 final
    # action == KEEP
    if sidecar == source:
        return "KEEP_OK"
    return "KEEP_CHANGED"


def bytes_len(s):
    return len(s.encode("utf-8", "surrogatepass"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    for x in ["--title-final", "--desc-final", "--production-overlay",
              "--done", "--catalog-final"]:
        ap.add_argument(x, required=True)
    ap.add_argument("--known-keyhash", default="",
                    help="逗号分隔的 0xXXXXXXXX, 仅对这些 kh 输出精确 repr 对比 (默认: 全量行也含)")
    ap.add_argument("--slots", default="", help="仅诊断指定 slot (逗号分隔), 默认全部")
    a = ap.parse_args()

    from gen_cohort_sidecars import TranslationResolver
    try:
        res = TranslationResolver(a.production_overlay, done_path=a.done)
    except Exception as ex:
        print(f"[FATAL] resolver 加载失败: {ex}")
        return 2

    known = set()
    for tok in a.known_keyhash.split(","):
        tok = tok.strip()
        if tok:
            known.add(int(tok, 16))
    slot_filter = {s.strip() for s in a.slots.split(",") if s.strip()}

    rows = []
    with open(a.manifest, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        for r in rd:
            rows.append(r)

    out_rows = []           # 每 key 一行
    slot_agg = {}           # slot -> agg dict
    _known_exact = {}       # kh -> (slot, exact_source, exact_final, exact_sidecar)

    def src_target(r):
        t = parse_tgi((r.get("target_TGI") or "").strip())
        if t is None:
            return _STBL_TID, _GROUP, None
        return t

    for r in rows:
        slot = r.get("cohort_slot", "")
        if slot_filter and slot not in slot_filter:
            continue
        if r.get("writer_verify") != "PASS":
            continue   # 只诊断实际生成了 sidecar 的 slot
        src = r.get("source_package", "")
        out = r.get("output_sidecar", "")
        if not (src and os.path.exists(src) and out and os.path.exists(out)):
            print(f"[SKIP] slot {slot}: source/sidecar 缺失 (src={src and os.path.exists(src)} out={out and os.path.exists(out)})")
            continue
        typ, group, inst = src_target(r)
        if inst is None:
            print(f"[SKIP] slot {slot}: manifest target_TGI 缺失/格式错误, 无法定位 source target")
            continue
        skmap, serr = read_source_target_stbl(src, typ, group, inst)
        okmap, oerr = read_sole_sidecar_stbl(out)
        if skmap is None or okmap is None:
            print(f"[SKIP] slot {slot}: 读取失败 serr={serr} oerr={oerr}")
            continue

        # role per key: 复用 approved_pv_refs (只读 XML 遍历) 得 (category, kh, src_text)
        from gen_cohort_sidecars import approved_pv_refs
        role_map = {}
        try:
            _tgi_pv, _km_pv, _appr, _errs = approved_pv_refs(src)
            if _appr is not None:
                for cat, kh, _t in _appr:
                    role_map[kh] = cat
            elif _errs:
                print(f"[note] slot {slot}: approved_pv_refs 无 approved (errors={_errs[:2]})")
        except Exception as _ex:
            print(f"[note] slot {slot}: approved_pv_refs 异常: {_ex}")

        role = r.get("role") or ""
        agg = {
            "slot": slot, "package": os.path.basename(src),
            "resolver_translate": 0,
            "TRANSLATE_CHANGED_OK": 0, "TRANSLATE_NOOP": 0,
            "TRANSLATE_NOT_APPLIED": 0, "TRANSLATE_WRONG_VALUE": 0,
            "KEEP_CHANGED": 0, "KEEP_OK": 0,
            "physical_changed": 0, "manifest_modified_key_count": r.get("modified_key_count"),
        }
        sset = set(skmap)
        for kh in sorted(sset):
            sfl, stxt = skmap[kh]
            ofl, otxt = okmap.get(kh, (None, None))
            if otxt is None:
                continue  # sidecar 缺该 key (R4 no-add/deploy 已覆盖; 这里只诊断都存在的)
            tr, tag = res.resolve(stxt)
            if tag in _TRANSLATE_TAGS:
                action = "TRANSLATE"
                final = tr if tr is not None else ""
                rsrc = tag
                agg["resolver_translate"] += 1
            elif tag in _KEEP_TAGS:
                action, final, rsrc = "KEEP", "", "KEEP_flag"
            else:
                action, final = "KEEP", ""       # MISSING/SOURCE_MISMATCH 视为非 player-visible, 须 verbatim
                rsrc = tag
            status = classify(action, final, stxt, otxt)
            agg[status] += 1
            if otxt != stxt:
                agg["physical_changed"] += 1
            if known and kh in known and kh not in _known_exact:
                _known_exact[kh] = (slot, stxt, final, otxt)
            out_rows.append({
                "slot": slot, "package": os.path.basename(src),
                "role": role_map.get(kh, role),
                "keyhash": f"0x{kh:08X}",
                "resolver_action": action, "resolver_source": rsrc,
                "resolver_final": repr(final),
                "source_stbl_text": repr(stxt), "sidecar_stbl_text": repr(otxt),
                # manifest 为聚合计数, 无 per-key action/final -> 留空
                "manifest_action": "", "manifest_expected_final": "",
                "source_eq_final": "Y" if final == stxt else "N",
                "source_eq_sidecar": "Y" if stxt == otxt else "N",
                "final_eq_sidecar": "Y" if final == otxt else "N",
                "source_changed": "Y" if otxt != stxt else "N",
            })
        slot_agg[slot] = agg

    # ---- 写 CSV ----
    cols = ["slot", "package", "role", "keyhash",
            "resolver_action", "resolver_source", "resolver_final",
            "source_stbl_text", "sidecar_stbl_text",
            "manifest_action", "manifest_expected_final",
            "source_eq_final", "source_eq_sidecar", "final_eq_sidecar",
            "source_changed"]
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in out_rows:
            w.writerow(row)
    print(f"[WROTE] {a.out}  ({len(out_rows)} key rows)")

    # ---- slot aggregate ----
    print("\n==== slot aggregate ====")
    print(f"{'slot':<6}{'pkg':<22}{'TRtrans':<9}{'CHG_OK':<8}{'NOOP':<6}{'NOT_APP':<9}{'WRONG':<7}{'KEEP_CHG':<9}{'phys_chg':<10}{'man_mod_cnt'}")
    for slot in sorted(slot_agg):
        ag = slot_agg[slot]
        print(f"{slot:<6}{ag['package']:<22}{ag['resolver_translate']:<9}{ag['TRANSLATE_CHANGED_OK']:<8}"
              f"{ag['TRANSLATE_NOOP']:<6}{ag['TRANSLATE_NOT_APPLIED']:<9}{ag['TRANSLATE_WRONG_VALUE']:<7}"
              f"{ag['KEEP_CHANGED']:<9}{ag['physical_changed']:<10}{ag['manifest_modified_key_count']}")

    # 全量汇总
    tot = {k: sum(slot_agg[s][k] for s in slot_agg) for k in
           ["resolver_translate", "TRANSLATE_CHANGED_OK", "TRANSLATE_NOOP",
            "TRANSLATE_NOT_APPLIED", "TRANSLATE_WRONG_VALUE", "KEEP_CHANGED",
            "KEEP_OK", "physical_changed"]}
    print("\n=== ALL slots sum ===")
    for k, v in tot.items():
        print(f"  {k} = {v}")

    # ---- 已知 KeyHash 精确 repr 对比 (exact stored str, 主循环已收集到 _known_exact) ----
    if known and _known_exact:
        print("\n==== known KeyHash exact repr 对比 (exact stored string) ====")
        print(f"{'slot':<6}{'kh':<12}{'len src':<9}{'len out':<9}{'bytes src':<10}{'bytes out':<10}"
              f"{'srcNFC':<7}{'outNFC':<7}{'src==final':<10}{'sidecar==final':<15}")
        for kh in sorted(known):
            if kh not in _known_exact:
                print(f"  ! kh 0x{kh:08X} 未在任一诊断 slot 中出现")
                continue
            (slot, s, f, o) = _known_exact[kh]
            print(f"{slot:<6}{f'0x{kh:08X}':<12}{len(s):<9}{len(o):<9}{bytes_len(s):<10}{bytes_len(o):<10}"
                  f"{'Y' if s == unicodedata.normalize('NFC', s) else 'N':<7}"
                  f"{'Y' if o == unicodedata.normalize('NFC', o) else 'N':<7}"
                  f"{'Y' if s == f else 'N':<10}"
                  f"{'Y' if o == f else 'N':<15}")
            print(f"        src    {s!r}")
            print(f"        final  {f!r}")
            print(f"        out    {o!r}")

    print("\n[DONE] 只读诊断完成; 未修改任何 package/sidecar/resolver/writer。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
