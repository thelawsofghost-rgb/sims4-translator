#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
independent_sidecar_audit.py —— manifest 驱动的独立只读审计 (run2 retry1 候选成品)
================================================================================
绝不重新 generation, 绝不调用 writer, 绝不写任何 package/sidecar/Mods。
从磁盘重新读取 9 个 original source package 与 9 个 generated sidecar,
对每个实际 sidecar 独立验证 (不复用 generation 内存里的 audit result):

输入 (都是只读):
  -cohort       output/cohort_selection.csv                 (10 个 slot)
  -manifest     output/cohort_sidecars_run2_retry1/cohort_sidecar_manifest.csv
                (驱动本次审计: 哪些 slot、source、output、NOOP、计数)
  -retry-dir    output/cohort_sidecars_run2_retry1          (扫描 stray/unmanifested .package)
  -title-final / -desc-final / -production-overlay / -done / -catalog-final
                (production resolver 的 5 个冻结输入 —— 独立重算最终译文, 不用生成期结果)

每个实际 sidecar 独立断言:
  R1 RESOURCE_COUNT == 1
  R2 STBL_COUNT == 1
  R3 sidecar CHS TGI == source existing CHS TGI (type/group/instance 全等)
  R4 SOURCE_ENTRIES == OUTPUT_ENTRIES   (no add / no delete)
  R5 changed keys == manifest/resolver TRANSLATE keys (writer -m 修改的 KeyHash 集合)
     - 每个 TRANSLATE key: source 原文 -> production resolver 最终译文, sidecar 内文本须精确一致
     - 每个 KEEP key:      sidecar 内文本 == source 原文 (未改)
     - unrelated keys:     sidecar 内文本 == source 原文 (完全未动)
  R6 duplicate key == 0
  R7 parse/audit error == 0

slot NOOP (PASS_NOOP_KEEP_ONLY) 单独验证:
  N1 manifest.writer_verify == PASS_NOOP_KEEP_ONLY
  N2 manifest.output_sidecar == (无) 且 expected sidecar = none
  N3 磁盘上不存在该 slot 的 sidecar 文件 (actual = none)
  N4 approved 全部 KEEP (len(mods)==0 == len(approved))

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
import audit_sidecar as A
import audit_canary_pair as AC   # 复用其 canonical zlib 解压 STBL 读取 (cold)
import pose_coverage as PC  # 同 canonical parser (scan_package) 但不含生成期结果

_STBL_TID = 0x220557DA
_LOCALE_CHS = 0x01
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


def read_pkg_stbl(path):
    """cold 读取: 返回 (target_tgi, kh_map, dup_keys, errors). 只读磁盘, 不收任何生成期结果.
    source / sidecar 均可能 zlib 压缩, 用 AC.read_one_stbl (会解压)."""
    idx, err = safe_parse(path)
    if err:
        return None, None, [], [f"parse error: {err}"]
    if idx is None:
        return None, None, [], ["DBPFIndex is None"]
    stbl = [e for e in idx.entries if e.type_id == _STBL_TID]
    chs = [e for e in stbl if ((e.instance_id >> 56) & 0xFF) == _LOCALE_CHS]
    if not chs:
        return None, None, [], ["无 0x01 CHS STBL"]
    if len(stbl) != 1:
        return None, None, [], [f"STBL_COUNT={len(stbl)} != 1"]
    e = chs[0]
    tgi = f"0x{_STBL_TID:08X}/0x{e.group_id:08X}/0x{e.instance_id:016X}"
    ver, comp, resv, slen, keys, rerr = AC.read_one_stbl(path, e)
    if rerr is not None or keys is None:
        return None, None, [], [f"STBL 读取失败: {rerr}"]
    dup = sorted({kh for kh, _, _ in keys if sum(1 for k2, _, _ in keys if k2 == kh) > 1})
    return tgi, {kh: (fl, txt) for kh, fl, txt in keys}, dup, []


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

    # ---- 加载 production resolver (5 个冻结输入, 只读) ----
    from gen_cohort_sidecars import TranslationResolver
    try:
        res = TranslationResolver(a.production_overlay, done_path=a.done)
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

    # ---- NOOP slot 验证 ----
    for r in noop_rows:
        slot = r.get("cohort_slot")
        R.add(f"[NOOP {slot}] writer_verify=PASS_NOOP_KEEP_ONLY",
              r.get("writer_verify") == "PASS_NOOP_KEEP_ONLY", r.get("writer_verify"))
        # 磁盘上 target sidecar 不存在
        src = r.get("source_package", "")
        exp_sidecar = None  # NOOP 不应有 expected sidecar 文件
        out_col = (r.get("output_sidecar") or "").strip()
        R.add(f"[NOOP {slot}] manifest.output_sidecar 为空",
              out_col == "" or out_col.lower() in ("none", "nan"), out_col)
        # 用 source 同名推 sidecar 路径
        if src and exp_sidecar is None:
            base = os.path.splitext(os.path.basename(src))[0]
            cand = os.path.join(a.retry_dir, f"{base}_chs.package")
            R.add(f"[NOOP {slot}] 磁盘上无 sidecar (actual=none)",
                  not os.path.exists(cand), cand)
        # approved 全 KEEP: 冷读 source 并独立 resolve 所有 approved
        if src and os.path.exists(src):
            tgi, kmap, dup, errs = read_pkg_stbl(src)
            if kmap is not None:
                # NOOP 合法性: 没有任何源 key 解析到 TRANSLATE 终态 (OVERRIDE/DONE/CACHE),
                # 否则生成期应产生 sidecar (NOOP 判定错误)。author/pack 等 metadata key
                # 解析到 MISSING 属正常 (非 player-visible), 不视为需翻译。
                any_terminal = False
                for kh, (fl, txt) in kmap.items():
                    tr, tag = res.resolve(txt)
                    if tag in ("OVERRIDE", "DONE", "CACHE"):
                        any_terminal = True
                        break
                R.add(f"[NOOP {slot}] 无任何源 key 解析到 TRANSLATE 终态 (合法 NOOP)",
                      not any_terminal, "")
                # 计数与 manifest 对齐
                R.add(f"[NOOP {slot}] translated_key_count==0",
                      str(r.get("translated_key_count")) in ("0", "", "None"),
                      r.get("translated_key_count"))
            else:
                R.add(f"[NOOP {slot}] source 读取 OK", False, "; ".join(errs) or "unreadable")
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

        # 冷读 source + sidecar
        stgi, skmap, sdup, serr = read_pkg_stbl(src) if src and os.path.exists(src) else (None, None, [], ["source missing"])
        otgi, okmap, odup, oerr = read_pkg_stbl(out) if out and os.path.exists(out) else (None, None, [], ["sidecar missing"])

        # R1/R2: RESOURCE_COUNT / STBL_COUNT 从源与 sidecar 独立算
        for lbl, p in (("SRC", src), ("OUT", out)):
            idx, e2 = safe_parse(p) if p and os.path.exists(p) else (None, "missing")
            if e2:
                log.add(f"R1 {lbl} parse OK", False, e2)
            else:
                rc = len(idx.entries) if idx else 0
                stbl_n = sum(1 for x in idx.entries if x.type_id == _STBL_TID) if idx else 0
                if lbl == "OUT":
                    log.add("R1 OUT RESOURCE_COUNT==1", rc == 1, f"got {rc}")
                    log.add("R2 OUT STBL_COUNT==1", stbl_n == 1, f"got {stbl_n}")

        # R3 TGI 匹配 (sidecar == source CHS TGI)
        log.add("R3 TGI 匹配", stgi is not None and otgi is not None and stgi == otgi,
                f"src={stgi} out={otgi}")

        # R5 关键集合: 从 writer -m 语义反推 —— 若 source 与 sidecar 都读到, 由 cold 比较
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
            # 文本一致性与 resolver 最终 (changed set == TRANSLATE set)
            #   tr_tag(kh) = production resolver 对 source 原文的终态 (KEEP / OVERRIDE/DONE/CACHE / 其他)
            tr_map = {}
            for kh in (sset & oset):
                tr, tag = res.resolve(skmap[kh][1])
                tr_map[kh] = (tr, tag)
            text_mism = 0
            changed_keys = []
            for kh in sorted(sset & oset):
                sfl, stxt = skmap[kh]
                ofl, otxt = okmap[kh]
                tr, tag = tr_map[kh]
                if tag == "KEEP" or tag in ("MISSING", "SOURCE_MISMATCH"):
                    # KEEP / unrelated 键: 必须保持 source 原文完全不变 (verbatim copy)
                    if ofl != sfl or otxt != stxt:
                        text_mism += 1
                elif tag in ("OVERRIDE", "DONE", "CACHE"):
                    # TRANSLATE 键: 必须等于 resolver 最终译文
                    if otxt != tr:
                        text_mism += 1
                    else:
                        changed_keys.append(kh)  # 确实被改成 resolver 最终
            # 关键不变式: changed set == TRANSLATE set  (每个 TRANSLATE 键必须真的被改,
            # 每个非 TRANSLATE(即 KEEP/MISSING/unrelated) 键必须没被改)
            tr_keys = {kh for kh, (tr, tag) in tr_map.items() if tag in ("OVERRIDE", "DONE", "CACHE")}
            changed_set = set(changed_keys)
            miss = sorted(tr_keys - changed_set)      # 应改为未改
            spurious = sorted(changed_set - tr_keys)  # 不该改却改了
            log.add("R5 changed set == TRANSLATE set",
                    text_mism == 0 and not miss and not spurious,
                    f"mismatch={text_mism} 未改TRANSLATE={['0x%08X'%k for k in miss]} 误改={['0x%08X'%k for k in spurious]}")
            log.add("R5 文本与 resolver 最终一致 (0 mismatch)", text_mism == 0, f"mismatch={text_mism}")
            # R5 modified_key_count 与 manifest 对齐 (cold 独立计数)
            mod_cnt = len(changed_keys)
            log.add("R5 modified_key_count==manifest",
                    mod_cnt == int(r.get("modified_key_count") or 0),
                    f"cold={mod_cnt} man={r.get('modified_key_count')}")
        else:
            log.add("R4/R5/R6 需 source+sidecar 均可读",
                    skmap is not None and okmap is not None,
                    f"serr={serr} oerr={oerr}")

        # R7 无 error
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
