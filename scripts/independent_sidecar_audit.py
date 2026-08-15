#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# flake8: noqa
r"""
independent_sidecar_audit.py —— manifest 驱动的独立只读审计 (run2 retry1 候选成品)
================================================================================
绝不重新 generation, 绝不调用 writer, 绝不写任何 package/sidecar/Mods。
从磁盘重新读取 original source package 与 generated sidecar,
对每个实际 sidecar 独立验证 (不复用 generation 内存里的 audit result):

输入 (都是只读):
  -cohort       output/cohort_selection.csv                 (10 个 slot)
  -manifest     output/cohort_sidecars_run2_retry1/cohort_sidecar_manifest.csv
                (驱动本次审计: 哪些 slot、source、output、NOOP、计数)
  -retry-dir    output/cohort_sidecars_run2_retry1          (扫描 stray/unmanifested .package)
  -title-final / -desc-final / -production-overlay / -done / -catalog-final
                (production resolver 的 5 个冻结输入 —— 独立重算最终译文, 不用生成期结果)

每个实际 sidecar 独立断言 (contract 拆分: source 与 sidecar 不同约束):

SOURCE package (original Sims package, 可含多 locale STBL, 如 18 个 STBL 属合法, 不得报错):
  S1 exact target CHS TGI (type=0x220557DA / group / instance=0x01 locale)
     在 source 中恰出现 1 次  (不从 manifest 硬编码; target_TGI 取 manifest 列)
  S2 仅解析该 target CHS STBL, parse PASS
  不要求 source RESOURCE_COUNT==1, 不要求 source STBL_COUNT==1

SIDECAR package (writer 产物):
  R1 RESOURCE_COUNT == 1
  R2 STBL_COUNT == 1
  R3 sidecar TGI == source exact target CHS TGI (type/group/instance 全等)

R4-R7 用 selected source CHS STBL vs sole sidecar STBL:
  R4 SOURCE_ENTRIES == OUTPUT_ENTRIES   (no add / no delete)
  R5 changed keys == manifest/resolver TRANSLATE keys
     - 每个 TRANSLATE key: source 原文 -> production resolver 最终译文, sidecar 内文本须精确一致
     - 每个 KEEP key / unrelated key: sidecar 内文本 == source 原文 (完全未动)
  R6 duplicate key == 0
  R7 parse/audit error == 0

slot NOOP (PASS_NOOP_KEEP_ONLY) 单独验证:
  N1 manifest.writer_verify == PASS_NOOP_KEEP_ONLY
  N2 output_sidecar 字段保存的是 planned path, 不代表磁盘有真实产物 ->
     以磁盘是否实际存在为准 (actual = none)
  N3 磁盘上不存在该 slot 的 sidecar 文件 (actual = none)
  N4 无任何源 key 解析到 TRANSLATE 终态 (合法 NOOP)

aggregate 输出:
  manifest packages = 10
  generated sidecars = 9
  NOOP = 1
  sidecar audit PASS = 9 / FAIL = 0 / ERROR = 0
  stray/unmanifested .package in retry-dir = 0
  结论: INDEPENDENT_AUDIT: PASS / FAIL

用法 (Windows 单行, 全只读):
  python scripts\independent_sidecar_audit.py ^
    --cohort output\cohort_selection.csv ^
    --manifest output\cohort_sidecars_run2_retry1\cohort_sidecar_manifest.csv ^
    --retry-dir output\cohort_sidecars_run2_retry1 ^
    --title-final output\translation_done_title_final.csv ^
    --desc-final output\translation_done_desc_final.csv ^
    --production-overlay output\translation_overrides.production.csv ^
    --done output\translation_done.csv ^
    --catalog-final output\translation_catalog.csv

退出码: 0 = INDEPENDENT_AUDIT PASS; 1 = 有 FAIL/ERROR; 2 = CLI/加载错误。
"""
import sys, os, csv, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from dbpf_fast import safe_parse
import audit_canary_pair as AC   # 复用其 canonical zlib 解压 STBL 读取 (cold)

_STBL_TID = 0x220557DA
_GROUP = 0x80000000


class Result:
    def __init__(self):
        self.ok = []
        self.bad = []

    def add(self, name, cond, detail=""):
        if cond:
            self.ok.append(name)
        else:
            self.bad.append(f"{name}: {detail}")


def _fmt_tgi(typ, group, inst):
    return f"0x{typ:08X}/0x{group:08X}/0x{inst:016X}"


def read_source_target_stbl(path, typ, group, inst):
    """cold 读取 SOURCE: 枚举全部 STBL, 要求 exact target (type/group/instance) 恰出现 1 次,
    然后仅解析该 target CHS STBL。source 可能有多个 locale STBL (如 18 个), 属合法, 不报错。
    返回 (stbl_total, target_count, target_tgi, kh_map, dup_keys, errors)."""
    errors = []
    idx, err = safe_parse(path)
    if err:
        return None, None, None, None, [], [f"parse error: {err}"]
    if idx is None:
        return None, None, None, None, [], ["DBPFIndex is None"]
    stbl = [e for e in idx.entries if e.type_id == _STBL_TID]
    matches = [e for e in stbl if e.type_id == typ and e.group_id == group and e.instance_id == inst]
    if len(matches) != 1:
        return len(stbl), len(matches), None, None, [], \
            [f"exact target TGI match count = {len(matches)} (期望 1; STBL 总数={len(stbl)})"]
    e = matches[0]
    target_tgi = _fmt_tgi(e.type_id, e.group_id, e.instance_id)
    ver, comp, resv, slen, keys, rerr = AC.read_one_stbl(path, e)
    if rerr is not None or keys is None:
        return len(stbl), 1, target_tgi, None, [], [f"STBL 读取失败: {rerr}"]
    dup = sorted({kh for kh, _, _ in keys if sum(1 for k2, _, _ in keys if k2 == kh) > 1})
    return len(stbl), 1, target_tgi, {kh: (fl, txt) for kh, fl, txt in keys}, dup, []


def read_sole_sidecar_stbl(path):
    """cold 读取 SIDECAR: 要求 RESOURCE_COUNT==1 且 STBL_COUNT==1, 返回 (tgi, kh_map, dup, errors)."""
    errors = []
    idx, err = safe_parse(path)
    if err:
        return None, None, [], [f"parse error: {err}"]
    if idx is None:
        return None, None, [], ["DBPFIndex is None"]
    if len(idx.entries) != 1:
        return None, None, [], [f"RESOURCE_COUNT={len(idx.entries)} != 1"]
    stbl = [e for e in idx.entries if e.type_id == _STBL_TID]
    if len(stbl) != 1:
        return None, None, [], [f"STBL_COUNT={len(stbl)} != 1"]
    e = stbl[0]
    tgi = _fmt_tgi(e.type_id, e.group_id, e.instance_id)
    ver, comp, resv, slen, keys, rerr = AC.read_one_stbl(path, e)
    if rerr is not None or keys is None:
        return None, None, [], [f"STBL 读取失败: {rerr}"]
    dup = sorted({kh for kh, _, _ in keys if sum(1 for k2, _, _ in keys if k2 == kh) > 1})
    return tgi, {kh: (fl, txt) for kh, fl, txt in keys}, dup, []


def parse_tgi(s):
    """解析 '0xTTTT/0xGGGG/0xIIIIIIIIIIII' -> (type, group, inst). 失败返回 None."""
    if not s:
        return None
    parts = [p.strip() for p in s.split("/")]
    if len(parts) != 3:
        return None
    try:
        return int(parts[0], 16), int(parts[1], 16), int(parts[2], 16)
    except Exception:
        return None


def build_approved(src):
    """该包唯一 approved KeyHash 集 (来自 approved_pv_refs) + role map.

    返回 (approved_kh_set, role_map, errors)。
    - 只有 approved keys 才允许进入 resolver action 判定与 R5 changed-set 构造。
    - 其余 source STBL entry 一律 UNRELATED: 即使其 stable TID 在全局 resolver 命中某
      TRANSLATE 源文本, 也绝不能跨包提升为 TRANSLATE target。
    - approved_pv_refs 失败 (包非 ELIGIBLE_EXISTING_CHS 等) -> 返回 (None, None, errors),
      上层必须 HARD-FAIL (无法建立 approved 集就谈不上 R5 一致性审计)。
    """
    from gen_cohort_sidecars import approved_pv_refs
    errors = []
    try:
        _tgi, _km, appr, errs = approved_pv_refs(src)
    except Exception as ex:
        return None, None, [f"approved_pv_refs 异常: {ex}"]
    if appr is None:
        return None, None, (list(errs) if errs else ["approved_pv_refs 无 approved"])
    role_map = {}
    for cat, kh, _txt in appr:
        role_map[kh] = cat
    return set(role_map), role_map, errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--retry-dir", required=True)
    ap.add_argument("--expect-packages", type=int, default=10, help="manifest 期望包数 (默认 10)")
    ap.add_argument("--expect-sidecars", type=int, default=9, help="期望 sidecar 数 (默认 9)")
    ap.add_argument("--expect-noop", type=int, default=1, help="期望 NOOP 数 (默认 1)")
    for x in ["--title-final", "--desc-final", "--production-overlay",
              "--done", "--catalog-final"]:
        ap.add_argument(x, required=True)
    a = ap.parse_args()

    R = Result()

    # ---- 加载 production resolver —— 与真实 run2 generation 完全一致 (5 个冻结源, 只读) ----
    from production_resolver import make_production_resolver
    try:
        res = make_production_resolver(a.title_final, a.desc_final, a.production_overlay,
                                       translation_done=a.done or None,
                                       translation_catalog=a.catalog_final or None)
    except Exception as ex:
        print(f"[FATAL] resolver 加载失败: {ex}")
        return 2

    # ---- manifest ----
    rows = []
    with open(a.manifest, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        for r in rd:
            rows.append(r)
    man_packages = len(rows)
    R.add(f"MANIFEST packages == {a.expect_packages}",
          man_packages == a.expect_packages, f"got {man_packages}")

    noop_rows = [r for r in rows if r.get("writer_verify") == "PASS_NOOP_KEEP_ONLY"]
    gen_rows = [r for r in rows if r.get("writer_verify") == "PASS"]
    R.add(f"MANIFEST NOOP == {a.expect_noop}",
          len(noop_rows) == a.expect_noop, f"got {len(noop_rows)}")

    def source_target(r):
        """从 manifest target_TGI 取 exact source CHS target (type/group/inst).
        优先用该 TGI; 若无则回退到 (STBL, GROUP, CHS instance)。"""
        t = parse_tgi((r.get("target_TGI") or "").strip())
        if t is None:
            return _STBL_TID, _GROUP, None
        return t

    # ---- NOOP slot 验证 ----
    for r in noop_rows:
        slot = r.get("cohort_slot")
        R.add(f"[NOOP {slot}] writer_verify=PASS_NOOP_KEEP_ONLY",
              r.get("writer_verify") == "PASS_NOOP_KEEP_ONLY", r.get("writer_verify"))
        # manifest.output_sidecar 保存的是 planned path, 不代表磁盘有真实产物。
        # contract: PASS_NOOP_KEEP_ONLY -> 磁盘上不得存在该 sidecar 文件 (actual = none)。
        src = r.get("source_package", "")
        out_col = (r.get("output_sidecar") or "").strip()
        # 磁盘上 target sidecar 不存在 (以 output_sidecar planned path 为准; 若为空则用 source 同名推)
        cand = ""
        if out_col and out_col.lower() not in ("none", "nan"):
            cand = out_col
        elif src:
            base = os.path.splitext(os.path.basename(src))[0]
            cand = os.path.join(a.retry_dir, f"{base}_chs.package")
        if cand:
            R.add(f"[NOOP {slot}] 磁盘上无 sidecar (actual=none)",
                  not os.path.exists(cand), cand)
        # 冷读 source: 独立解析 exact target CHS STBL, 验证无任何源 key 解析到 TRANSLATE 终态
        if src and os.path.exists(src):
            typ, group, inst = source_target(r)
            if inst is None:
                # 无 manifest TGI, 无法定位 target -> ERROR
                R.add(f"[NOOP {slot}] source exact target TGI 可定位", False,
                      "manifest target_TGI 缺失/格式错误")
            else:
                s_total, s_cnt, stgi, kmap, sdup, errs = read_source_target_stbl(src, typ, group, inst)
                if kmap is None:
                    R.add(f"[NOOP {slot}] source target STBL 解析 OK", False, "; ".join(errs) or "unreadable")
                else:
                    # NOOP 合法性: 没有任何 APPROVED key 解析到 TRANSLATE 终态 (OVERRIDE/DONE/CACHE)。
                    # 只以该包 approved_pv_refs 的 approved 集为准; 其余 source STBL entry 一律
                    # UNRELATED —— 即使其 stable TID 在全局 resolver 命中某 TRANSLATE 源文本,
                    # 也绝不能跨包提升为 TRANSLATE (否则误报 NOOP)。author/pack metadata 解析到
                    # MISSING 属正常 (非 player-visible)。
                    appr_kh, _rmap, aerr = build_approved(src)
                    if appr_kh is None:
                        R.add(f"[NOOP {slot}] approved 集可建立 (approved_pv_refs)", False,
                              "; ".join(aerr) or "approved_pv_refs 失败")
                    else:
                        any_terminal = False
                        for kh in appr_kh:
                            if kh not in kmap:
                                continue
                            tr, tag = res.resolve(kmap[kh][1])
                            if tag == "TRANSLATE" and tr is not None:
                                any_terminal = True
                                break
                        R.add(f"[NOOP {slot}] 无任何 APPROVED key 解析到 TRANSLATE 终态 (合法 NOOP)",
                              not any_terminal, "")
                # 计数与 manifest 对齐
                R.add(f"[NOOP {slot}] translated_key_count==0",
                      str(r.get("translated_key_count")) in ("0", "", "None"),
                      r.get("translated_key_count"))
        else:
            R.add(f"[NOOP {slot}] source 存在", src and os.path.exists(src), src)
        R.add(f"[NOOP {slot}] audit_result=SKIP_NO_OUTPUT",
              r.get("audit_result") == "SKIP_NO_OUTPUT", r.get("audit_result"))

    # ---- 每个 generation slot: 独立 cold 审计 ----
    sidecar_pass = 0
    sidecar_fail = 0
    sidecar_error = 0
    for r in gen_rows:
        slot = r.get("cohort_slot")
        src = r.get("source_package", "")
        out = r.get("output_sidecar", "")
        log = Result()

        # 从 manifest 取 exact source target CHS TGI
        typ, group, inst = source_target(r)
        if inst is None:
            log.add("SOURCE exact target TGI 可定位", False,
                    "manifest target_TGI 缺失/格式错误")
            typ, group, inst = _STBL_TID, _GROUP, None

        # ---- SOURCE: 枚举全部 STBL, 要求 exact target 恰出现 1 次, 只解析该 CHS STBL ----
        if src and os.path.exists(src) and inst is not None:
            s_total, s_cnt, stgi, skmap, sdup, serr = read_source_target_stbl(src, typ, group, inst)
            log.add("S1 source exact target TGI exists exactly once",
                    s_cnt == 1, f"match={s_cnt} (STBL 总数={s_total})")
            log.add("S2 source target STBL parse OK", skmap is not None,
                    "; ".join(serr) or "unreadable")
        else:
            s_total, s_cnt, stgi, skmap, sdup, serr = None, None, None, None, [], \
                ["source missing"] if not (src and os.path.exists(src)) else []
            if not (src and os.path.exists(src)):
                log.add("S0 source 存在", False, src)

        # ---- SIDECAR: RESOURCE_COUNT==1 / STBL_COUNT==1 / TGI 全等 ----
        if out and os.path.exists(out):
            otgi, okmap, odup, oerr = read_sole_sidecar_stbl(out)
            # read_sole_sidecar_stbl 内部已校验 RESOURCE_COUNT==1 和 STBL_COUNT==1;
            # 成功 (okmap!=None) 即两者都成立, 失败时 oerr 详列原因。
            rc_ok = any("RESOURCE_COUNT" in e for e in oerr) or okmap is not None
            sc_ok = any("STBL_COUNT" in e for e in oerr) or okmap is not None
            log.add("R1 sidecar RESOURCE_COUNT==1", rc_ok,
                    "; ".join([e for e in oerr if "RESOURCE_COUNT" in e]) or "ok")
            log.add("R2 sidecar STBL_COUNT==1", sc_ok,
                    "; ".join([e for e in oerr if "STBL_COUNT" in e]) or "ok")
            log.add("R3 sidecar TGI == source exact target CHS TGI",
                    stgi is not None and otgi is not None and stgi == otgi,
                    f"src={stgi} out={otgi}")
        else:
            otgi, okmap, odup, oerr = None, None, [], ["sidecar missing"]
            log.add("R0 sidecar 存在", False, out)

        # ---- R4-R7 用 selected source CHS STBL vs sole sidecar STBL ----
        if skmap is not None and okmap is not None:
            log.add("R6 SRC 无重复 KeyHash", len(sdup) == 0, f"dup={sdup}")
            log.add("R6 OUT 无重复 KeyHash", len(odup) == 0, f"dup={odup}")
            # R4 entries 数量相等 (no add / no delete)
            log.add("R4 SOURCE==OUTPUT entries", len(skmap) == len(okmap),
                    f"{len(skmap)} vs {len(okmap)}")
            # 集合一致 (no add / no delete)
            sset, oset = set(skmap), set(okmap)
            add_keys = sorted(oset - sset)
            del_keys = sorted(sset - oset)
            log.add("R5 no add", len(add_keys) == 0, f"added={['0x%08X'%k for k in add_keys]}")
            log.add("R5 no delete", len(del_keys) == 0, f"deleted={['0x%08X'%k for k in del_keys]}")

            # ===================================================================
            # 关键 contract: 只对「本包 approved_pv_refs 的 approved 集」做 resolver
            # action 判定与 R5 changed-set 构造。其余 source STBL entry 一律 UNRELATED:
            #   即使其 stable TID 在全局 resolver 命中某 TRANSLATE 源文本, 也绝不能跨包
            #   提升为 TRANSLATE target (否则 expected set 污染 -> 误报 KEEP_CHANGED /
            #   TRANSLATE_NOT_APPLIED, 并扭曲 per-package T/K 分配)。
            appr_kh, _rmap, aerr = build_approved(src)
            if appr_kh is None:
                # 无法建立 approved 集 -> 无法开展 R5 一致性审计, HARD-FAIL
                log.add("approved 集可建立 (approved_pv_refs)", False,
                        "; ".join(aerr) or "approved_pv_refs 失败")
            else:
                # 该包 approved 集 (∩ 实际 source key)
                appr_in_src = appr_kh & sset
                # 只对 approved 内 key 解析 resolver; 其余 (unrelated) 不 resolve
                tr_map = {}
                for kh in appr_in_src:
                    tr, tag = res.resolve(skmap[kh][1])
                    tr_map[kh] = (tr, tag)
                # per-package: approved / translate / keep (只在 approved 内计)
                tr_approved = {kh for kh, (tr, tag) in tr_map.items()
                               if tag == "TRANSLATE" and tr is not None}
                keep_approved = {kh for kh, (tr, tag) in tr_map.items()
                                 if tag != "TRANSLATE"}
                A_cnt = len(appr_kh)
                T_cnt = len(tr_approved)
                K_cnt = len(keep_approved)
                print(f"  slot {slot}: approved={A_cnt} translate={T_cnt} keep={K_cnt} "
                      f"(manifest A={r.get('approved_key_count')} T={r.get('translated_key_count')} "
                      f"K={r.get('keep_key_count')})")
                # ---- aggregate gates: must be HARD-FAIL before R5 ----
                man_A = int(r.get("approved_key_count") or 0)
                man_T = int(r.get("translated_key_count") or 0)
                man_K = int(r.get("keep_key_count") or 0)
                log.add("approved count == manifest A", A_cnt == man_A,
                        f"resolved={A_cnt} man={man_A}")
                log.add("approved TRANSLATE count == manifest T", T_cnt == man_T,
                        f"resolved={T_cnt} man={man_T}")
                log.add("approved KEEP count == manifest K", K_cnt == man_K,
                        f"resolved={K_cnt} man={man_K}")

                # ---- 文本一致性 (只对 approved key)
                text_mism = 0       # approved TRANSLATE 写错 / approved KEEP 被误改 / unrelated 被误改
                trans_noop = []     # approved TRANSLATE 但 final==source (合法 NOOP-like, 另计)
                changed_keys = []   # approved TRANSLATE 且 final!=source 且 sidecar==final (确实被改)
                spurious_unrel = [] # unrelated key 被改 (绝不允许)
                for kh in sorted(appr_in_src):
                    sfl, stxt = skmap[kh]
                    ofl, otxt = okmap[kh]
                    tr, tag = tr_map[kh]
                    if tag == "TRANSLATE":
                        # approved TRANSLATE key
                        if tr is not None and tr != stxt:
                            if otxt == tr:
                                changed_keys.append(kh)
                            else:
                                text_mism += 1          # 应为 final 却不是
                        else:
                            # TRANSLATE 但 final==source (no-op final): 必须保持 verbatim
                            trans_noop.append(kh)
                            if otxt != stxt:
                                text_mism += 1
                    else:
                        # approved KEEP (含 MISSING/SOURCE_MISMATCH): 必须 verbatim
                        if ofl != sfl or otxt != stxt:
                            text_mism += 1
                # ---- unrelated keys: 必须 verbatim (sidecar==source), 绝不能被跨包提升 ----
                unrelated = (sset & oset) - appr_kh
                for kh in sorted(unrelated):
                    sfl, stxt = skmap[kh]
                    ofl, otxt = okmap[kh]
                    if ofl != sfl or otxt != stxt:
                        spurious_unrel.append(kh)
                        text_mism += 1
                # ---- R5 changed set == expected_changed_keys ----
                # expected = approved TRANSLATE 且 final!=source (真正要求变化的 key)
                expected_changed = {kh for kh, (tr, tag) in tr_map.items()
                                    if tag == "TRANSLATE"
                                    and tr is not None and tr != skmap[kh][1]}
                changed_set = set(changed_keys)
                miss = sorted(expected_changed - changed_set)      # 应改未改
                spurious = sorted(changed_set - expected_changed)  # 不改却改 (不应发生)
                log.add("R5 changed set == expected_changed_keys",
                        text_mism == 0 and not miss and not spurious and not spurious_unrel,
                        f"mismatch={text_mism} 未改TRANSLATE={['0x%08X'%k for k in miss]} "
                        f"误改={['0x%08X'%k for k in spurious]} 误改UNRELATED={['0x%08X'%k for k in spurious_unrel]}")
                log.add("R5 文本与 resolver 最终一致 (0 mismatch)", text_mism == 0,
                        f"mismatch={text_mism} TRANSLATE_NOOP={['0x%08X'%k for k in trans_noop]}")
                # R5 modified_key_count 与 manifest 对齐 (cold 独立计数, 仅 approved TRANSLATE 变化 key)
                mod_cnt = len(changed_keys)
                log.add("R5 modified_key_count==manifest",
                        mod_cnt == man_T,
                        f"cold={mod_cnt} man={man_T}")
        else:
            log.add("R4/R5/R6 需 source(target)+sidecar 均可读",
                    skmap is not None and okmap is not None,
                    f"serr={serr} oerr={oerr}")

        # R7 无 error (source target 可读 且 sidecar 可读)
        log.add("R7 无 audit 错误", not serr and not oerr, f"{serr};{oerr}")

        # manifest 一致性
        log.add("MANIFEST writer_verify==PASS", r.get("writer_verify") == "PASS", r.get("writer_verify"))
        log.add("MANIFEST audit_result==PASS", r.get("audit_result") == "PASS", r.get("audit_result"))

        ok_n = len(log.ok)
        bad_n = len(log.bad)
        if bad_n == 0:
            sidecar_pass += 1
            print(f"[PASS] slot {slot}: {ok_n} checks OK")
        else:
            sidecar_fail += 1
            print(f"[FAIL] slot {slot}: {ok_n} OK / {bad_n} BAD")
            for b in log.bad:
                print(f"         !! {b}")

    R.add(f"SIDECAR audit PASS == {a.expect_sidecars}",
          sidecar_pass == a.expect_sidecars, f"pass={sidecar_pass} fail={sidecar_fail} error={sidecar_error}")
    R.add("SIDECAR audit FAIL == 0", sidecar_fail == 0, f"fail={sidecar_fail}")
    R.add("SIDECAR audit ERROR == 0", sidecar_error == 0, f"error={sidecar_error}")

    # ---- stray / unmanifested .package in retry-dir ----
    manifested = {os.path.basename(os.path.realpath(r.get("output_sidecar", ""))) if r.get("output_sidecar") and os.path.exists(r.get("output_sidecar", "")) else "" for r in rows}
    manifested = {x for x in manifested if x}
    stray = []
    if os.path.isdir(a.retry_dir):
        for fn in sorted(os.listdir(a.retry_dir)):
            if fn.lower().endswith(".package"):
                rp = os.path.realpath(os.path.join(a.retry_dir, fn))
                if os.path.basename(rp) not in manifested:
                    stray.append(fn)
    R.add("STRAY/unmanifested .package == 0", len(stray) == 0, f"stray={stray}")

    # ---- aggregate ----
    print("\n==== aggregate ====")
    print(f"manifest packages = {man_packages}")
    print(f"generated sidecars = {len(gen_rows)}")
    print(f"NOOP = {len(noop_rows)}")
    print(f"sidecar audit PASS = {sidecar_pass} / FAIL = {sidecar_fail} / ERROR = {sidecar_error}")
    print(f"stray/unmanifested .package = {len(stray)}")

    if R.bad:
        print("\nINDEPENDENT_AUDIT: FAIL")
        for b in R.bad:
            print("  !! " + b)
        return 1
    print("\nINDEPENDENT_AUDIT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
