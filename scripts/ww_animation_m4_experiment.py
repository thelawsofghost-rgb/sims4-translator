#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M4 最小实验: 测试 Story identity 是否绑定 animation_clip_name。

背景: M3 改了 raw_display/stage/next_stages 三字段后仍 NOT。M3 runtime 取证
  确认三字段+animation_id 都不是 key（animation_id IDENTICAL 但非决定性）。
  剩余最大嫌疑: animation_clip_name —— WW runtime 可能用 clip 名作为动画注册身份。
  M3 复制源 instance 时保留了源 clip name -> runtime 认为新 instance 与源是同一
  动画(clip 冲突) -> 拒绝注册 -> fallback "NOT"。

M4 假设: 给新 instance 生成【唯一新 clip name】(如 nevely42_cheat1_a0_zh),
  同步改 4 字段:
    1) animation_clip_name   (唯一新值)
    2) animation_raw_display_name -> zh
    3) animation_stage_name       -> zh
    4) animation_next_stages 叶子  -> zh
  其余字段一律不动, 源包 byte-identical。

验证: WW runtime 是否接受新 Story identity。
  - 成功 -> Story identity 绑定 clip name
  - 失败 -> 继续找 registry key

用法 (Windows, 只读源, 产出于 out-dir):
  python scripts/ww_animation_m4_experiment.py <SRC.package> \
      --candidates m4_candidates.csv --translations m4_translations.csv \
      --chain-map configs/ww_m3_chain_map.csv \
      [--clip-template nevely42_cheat1_a0_zh] [--inspect-only] \
      [--expect-n 1] [--allow-unmapped-chain] [--out-dir ...]

ZERO_WRITE_TO_MODS=YES (仅 out-dir 产物, 不碰 Mods)
"""
import argparse
import csv
import importlib.util
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
_spec = importlib.util.spec_from_file_location("ww_canary_builder", SCRIPT_DIR / "ww_animation_canary_builder.py")
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)
import ww_animation_m3_experiment as _m3  # 复用 _loc_anim/_entry_blocks/_gen_inst/_entry_displays/_text_leaves/build_chain_map/_inspect_entry

WW_ANIM_XML = 0x7DF2169C
DISPLAY_FIELD = "animation_raw_display_name"
STAGE_FIELD = "animation_stage_name"
NEXT_FIELD = "animation_next_stages"
CLIP_FIELD = "animation_clip_name"
OUT_DIR = Path("output/ww_animation_m4")
CHAIN_MAP = {}


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else None


def _replace_4fields(block_text, zh, zh_clip):
    """替换 clip_name + raw_display + stage + next_stages 内匹配文本。
    返回 (old_display, old_clip, new_block, chain_hits, chain_misses, err)。
    clip_name 必须是唯一 <T> 节点, 否则 fail-closed。"""
    root = ET.fromstring(block_text)
    err = []
    replaced_display = None
    replaced_clip = None
    clip_cnt = 0
    for el in root.iter():
        n = el.get("n")
        if not n:
            continue
        lt = _local(el.tag)
        if n == CLIP_FIELD and lt == "T":
            clip_cnt += 1
            if replaced_clip is None:
                replaced_clip = (el.text or "").strip()
            el.text = zh_clip
        elif n == DISPLAY_FIELD and lt == "T":
            replaced_display = replaced_display or (el.text or "").strip()
            el.text = zh
        elif n == STAGE_FIELD and lt == "T":
            el.text = zh
    if clip_cnt != 1:
        err.append(f"animation_clip_name 节点数={clip_cnt} (需恰1)")
    chain_hits, chain_misses = [], []
    for el in root.iter():
        n = el.get("n")
        if n != NEXT_FIELD:
            continue
        leaves = []
        _m3._text_leaves(el, leaves)
        for c, v in leaves:
            if v in CHAIN_MAP:
                c.text = CHAIN_MAP[v]
                chain_hits.append(v)
            elif v:
                chain_misses.append(v)
    new_block = ET.tostring(root, encoding="unicode")
    return replaced_display, replaced_clip, new_block, chain_hits, chain_misses, err


def main():
    global CHAIN_MAP
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="源 WW package")
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--translations", required=True)
    ap.add_argument("--chain-map", default=None)
    ap.add_argument("--clip-template", default=None,
                    help="唯一新 clip name 模板; 缺省 = 自动 {源clip}_{后缀}")
    ap.add_argument("--clip-suffix", default="zh",
                    help="自动派生后缀 (clipname -> clipname_{suffix}); 缺省 zh")
    ap.add_argument("--expect-n", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--inspect-only", action="store_true")
    ap.add_argument("--allow-unmapped-chain", action="store_true")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: source 不存在 {src}", file=sys.stderr); return 2

    cands = list(csv.DictReader(open(a.candidates, newline="", encoding="utf-8")))
    if a.expect_n is not None and not a.force and len(cands) != a.expect_n:
        print(f"ERROR: 候选数 {len(cands)} != 预期 {a.expect_n}", file=sys.stderr); return 2
    tr_map = {}
    for r in csv.DictReader(open(a.translations, newline="", encoding="utf-8")):
        tr_map[str(r["ordinal"]).strip()] = r["chinese_translation"].strip()
    missing = [str(c["ordinal"]).strip() for c in cands if str(c["ordinal"]).strip() not in tr_map]
    if missing:
        print(f"ERROR: 缺少翻译 ordinal: {missing} (fail-closed)", file=sys.stderr); return 2
    CHAIN_MAP = _m3.build_chain_map(a.translations, a.chain_map)
    if CHAIN_MAP is None:
        return 2
    print(f"M4 candidates={len(cands)} chain_map={len(CHAIN_MAP)}")

    src_sha_before = wb.sha256(src)
    idx, err = wb.safe_parse(src)
    if err is not None or idx is None:
        print(f"ERROR: 源解析失败: {err}", file=sys.stderr); return 3
    ww = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    if len(ww) != 1:
        print(f"ERROR: 源需单 WW_ANIM_XML; 实际={len(ww)}", file=sys.stderr); return 3
    ww_e = ww[0]
    src_inst = ww_e.instance_id
    src_tgi = (ww_e.type_id, ww_e.group_id, ww_e.instance_id)
    body_orig = wb.read_body_raw(src, ww_e)
    _schema, xml_text_orig, xerr = wb.parse_anim_xml(body_orig)
    if xerr is not None:
        print(f"ERROR: WW XML 解析失败: {xerr}", file=sys.stderr); return 3
    inner, inner_start, inner_end = _m3._loc_anim(xml_text_orig)
    if inner is None:
        print("ERROR: 找不到 animations_list", file=sys.stderr); return 3
    blocks = _m3._entry_blocks(inner)
    entry_blocks = {}
    eidx = 0
    for b, _r in blocks:
        if b.lstrip().startswith("<U"):
            entry_blocks[eidx] = b
            eidx += 1
    n_entries = eidx
    src_major, src_minor, hdr_comp, entries_meta = wb.read_entry_meta_raw(src)
    if len(entries_meta) != len(idx.entries):
        print("ERROR: meta/条目数不一致", file=sys.stderr); return 3
    ww_meta = next((m for m in entries_meta if (m["type"], m["group"], m["inst"]) == src_tgi), None)
    src_off_hi = int(ww_meta["offset_comp"]); src_sz_hi = int(ww_meta["size_comp"]); src_f8 = ww_meta["comp_type"]

    # ---- inspect: dump 每个目标 entry 的 clip 及 chain 结构 ----
    insp_lines = []
    src_clips = {}
    for c in cands:
        ord_s = str(c["ordinal"]).strip()
        ordinal = int(ord_s)
        if ordinal not in entry_blocks:
            print(f"ERROR: ordinal {ordinal} 越界 {n_entries}", file=sys.stderr); return 3
        zh = tr_map[ord_s]
        info = _m3._inspect_entry(entry_blocks[ordinal], ordinal, zh)
        # 单独取 clip_name
        clip_vals = []
        root_t = ET.fromstring(entry_blocks[ordinal])
        for el in root_t.iter():
            if el.get("n") == CLIP_FIELD:
                clip_vals.append((_local(el.tag), (el.text or "").strip()))
        src_clips[ordinal] = clip_vals
        insp_lines.append(f"\n[ordinal {ordinal}]  display_zh={zh!r}  is_story={info['is_story']}")
        insp_lines.append(f"    raw_display    = {info['raw_display']!r}")
        insp_lines.append(f"    stage_name     = {info['stage_name']!r}")
        insp_lines.append(f"    clip_name      = {clip_vals}")
        insp_lines.append(f"    next_shape     = {info['next_shape']}")
        insp_lines.append(f"    next_leaves    = {info['next_leaves']}")
        hits = [lv["val"] for lv in info["next_leaves"] if lv["val"] in CHAIN_MAP]
        miss = [lv["val"] for lv in info["next_leaves"] if lv["val"] and lv["val"] not in CHAIN_MAP]
        insp_lines.append(f"    chain hit     = {hits}")
        insp_lines.append(f"    chain MISS    = {miss}")
    insp_txt = "\n".join(insp_lines)
    print("========== M4 INSPECT (只读结构) ==========")
    print(insp_txt)

    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "m4_inspect_structure.txt").write_text(insp_txt, encoding="utf-8")
    if a.inspect_only:
        print(f"OUT_INSPECT={out_dir / 'm4_inspect_structure.txt'}")
        print("M4_INSPECT=OK (只读, 未生成包)")
        print("ZERO_WRITE_TO_MODS=YES")
        return 0

    # ---- 生产: 逐候选 4 字段替换 ----
    used = {src_inst}
    jobs = []
    for c in cands:
        ord_s = str(c["ordinal"]).strip()
        ordinal = int(ord_s)
        zh = tr_map[ord_s]
        sc = src_clips.get(ordinal, [])
        if not sc:
            print(f"ERROR: ordinal {ordinal} 无 animation_clip_name (M4 需该字段)", file=sys.stderr); return 3
        if len(sc) != 1 or sc[0][0] != "T":
            print(f"ERROR: ordinal {ordinal} clip_name 形态异常 {sc} (需单一<T>)", file=sys.stderr); return 3
        src_clip = sc[0][1]
        # 唯一新 clip name
        if a.clip_template:
            zh_clip = a.clip_template
        else:
            zh_clip = f"{src_clip}_{a.clip_suffix}"
        old_disp, old_clip, new_block, hits, miss, repl_err = _replace_4fields(entry_blocks[ordinal], zh, zh_clip)
        if old_disp is None:
            print(f"ERROR: ordinal {ordinal} raw_display 定位失败", file=sys.stderr); return 3
        if repl_err:
            print(f"ERROR: ordinal {ordinal} {repl_err} (fail-closed)", file=sys.stderr); return 3
        # 新 clip 必须 != 源 clip (唯一性前提)
        if zh_clip == src_clip:
            print(f"ERROR: ordinal {ordinal} 新clip==源clip ({zh_clip!r}), 无唯一性 (fail-closed)", file=sys.stderr); return 3
        if miss and not a.allow_unmapped_chain:
            print(f"ERROR: ordinal {ordinal} next_stages 未映射 {miss} (fail-closed)", file=sys.stderr); return 3
        inst = _m3._gen_inst(src_inst, ordinal, zh, 0)
        salt = 0
        while inst in used:
            salt += 1
            inst = _m3._gen_inst(src_inst, ordinal, zh, salt)
        used.add(inst)
        new_inner = []
        ei2 = 0
        for b, _r in blocks:
            if b.lstrip().startswith("<U"):
                new_inner.append(new_block if ei2 == ordinal else b)
                ei2 += 1
            else:
                new_inner.append(b)
        new_xml = xml_text_orig[:inner_start] + "".join(new_inner) + xml_text_orig[inner_end:]
        new_body = wb.compress_like(body_orig, new_xml.encode("utf-8"))
        jobs.append({"ordinal": ordinal, "zh": zh, "clip": zh_clip, "old_clip": src_clip,
                     "inst": inst, "body": new_body, "new_xml": new_xml})
        print(f"  job ordinal={ordinal}  display={old_disp!r}->{zh!r}  "
              f"clip={src_clip!r}->{zh_clip!r}  inst=0x{inst:016X}")

    # ---- 组装 (与 M3 同骨架) ----
    items = [(src_tgi[0], src_tgi[1], src_tgi[2], body_orig,
              {"comp_state": True, "comp_type": src_f8, "mem_size": len(wb.decompress_maybe(body_orig)),
               "offset_high_bit": src_off_hi, "size_high_bit": src_sz_hi})]
    for j in jobs:
        items.append((WW_ANIM_XML, src_tgi[1], j["inst"], j["body"],
                      {"comp_state": True, "comp_type": src_f8,
                       "mem_size": len(wb.decompress_maybe(j["body"])),
                       "offset_high_bit": src_off_hi, "size_high_bit": src_sz_hi}))
    non_ww = 0
    for i, e in enumerate(idx.entries):
        if (e.type_id, e.group_id, e.instance_id) == src_tgi:
            continue
        body = wb.read_body_raw(src, e)
        m = entries_meta[i]
        items.append((e.type_id, e.group_id, e.instance_id, body,
                      {"comp_state": bool(m["comp_type"]), "comp_type": m["comp_type"],
                       "mem_size": m["mem_size"],
                       "offset_high_bit": int(m["offset_comp"]), "size_high_bit": int(m["size_comp"])}))
        non_ww += 1
    print(f"ITEM_COUNT={len(items)}  (1源WW + {len(jobs)}新WW + {non_ww}非WW)")

    # ---- V1-V5 ----
    print("\n=== 静态验证 (M4) ===")
    ok = True
    v1 = wb.sha256(src) == src_sha_before
    print(f"  [V1] source unchanged: {v1}"); ok &= v1
    new_insts = {j["inst"] for j in jobs}
    v2 = all(i != src_inst for i in new_insts) and len(new_insts) == len(jobs)
    print(f"  [V2] 新inst互异且!=源: {v2}"); ok &= v2
    d_src = _m3._entry_displays(xml_text_orig)
    v3 = True
    for j in jobs:
        d_new = _m3._entry_displays(j["new_xml"])
        if d_src is None or d_new is None or len(d_src) != len(d_new):
            v3 = False; break
        diffs = [i for i in range(len(d_src)) if (d_src[i] or "") != (d_new[i] or "")]
        if diffs != [j["ordinal"]]:
            v3 = False; break
    print(f"  [V3] 每新XML仅目标 display 变: {v3}"); ok &= v3
    out_path = None
    with tempfile.TemporaryDirectory() as td:
        probe = Path(td) / "probe.package"
        wb.build_package(items, probe, header_comp=hdr_comp, major=src_major, minor=src_minor)
        valid, fails, warns = wb.dbpf_metadata_valid(probe)
        print(f"  [V4] DBPF valid: {valid}  WARNS={warns}"); ok &= valid
        r2, r2err = wb.safe_parse(probe)
        v4b = False
        if r2err is None and r2 is not None:
            ww2 = [e for e in r2.entries if e.type_id == WW_ANIM_XML]
            v4b = len(ww2) == 1 + len(jobs) and {e.instance_id for e in ww2} == {src_inst} | new_insts
        print(f"  [V4b] sidecar 重读 1+N WW_XML: {v4b}"); ok &= v4b
        probe_hc = wb.read_entry_meta_raw(probe)[2]
        om = wb.read_entry_meta_raw(probe)[3]
        v5 = probe_hc == hdr_comp
        src_nonww = {(m["type"], m["group"], m["inst"], int(m["offset_comp"]), int(m["size_comp"]), m["comp_type"])
                     for i, m in enumerate(entries_meta) if (m["type"], m["group"], m["inst"]) != src_tgi}
        out_nonww = {(mo["type"], mo["group"], mo["inst"], int(mo["offset_comp"]), int(mo["size_comp"]), mo["comp_type"])
                     for mo in om if (mo["type"], mo["group"], mo["inst"]) != src_tgi and mo["inst"] not in new_insts}
        v5_nonww = out_nonww == src_nonww
        print(f"  [V5] header_comp保源={v5} 非WW保源={v5_nonww}"); ok &= v5 and v5_nonww

    print(f"  STATIC_PASS={ok}")
    if not ok:
        print("M4_STATIC_FAIL -> 不写包 (fail-closed)", file=sys.stderr)
        return 3

    stem = src.stem
    out_path = out_dir / f"{stem}_M4_CLIP_{len(jobs)}inst.package"
    wb.build_package(items, out_path, header_comp=hdr_comp, major=src_major, minor=src_minor)
    print(f"OUT_PATH={out_path}")
    print(f"OUT_SHA256={wb.sha256(out_path)}")
    print(f"NEW_INSTANCES={[hex(j['inst']) for j in jobs]}")
    clip_map_str = ', '.join(f"{j['ordinal']}: " + j["old_clip"] + "->" + j["clip"] for j in jobs)
    print(f"CLIP_MAP = {clip_map_str}")
    print("M4_BATCH PASS=YES")
    print("ZERO_WRITE_TO_MODS=YES (仅本 out-dir 产物)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
