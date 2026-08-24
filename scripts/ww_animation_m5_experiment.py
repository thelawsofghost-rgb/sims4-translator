#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M5 最小实验: 置新 animation_id (保留 M4 四字段 + 唯一新 animation_id)。

背景排除 (真机 NOT Caught Cheating):
  M2 raw_display          -> NOT
  M3 raw+stage+next       -> NOT
  M4 raw+stage+next+clip[]-> NOT   (animation_clip_name 也非 bind key)
M5 假设: WW runtime 以 animation_id 作为 Story animation 的注册身份 key;
  前几次都复制了源 animation_id(IDENTICAL) -> runtime 认为新动画与原动画同注册键
  -> 拒绝注册 -> fallback NOT。

M5 = M4 的四字段修改 + animation_id 独立唯一化:
  1) animation_clip_name   -> 各节点 _zh 唯一
  2) animation_raw_display_name -> zh
  3) animation_stage_name       -> zh
  4) animation_next_stages 叶子  -> zh
  5) animation_id           -> 从源值派生新的唯一值 (>0, !=源, 64-bit 确定性)
  其余字段 byte 保持, 源包 byte-identical。

验证: WW runtime 是否接受新 Story identity。
  - 成功 -> animation_id 是 identity key
  - 失败 -> 排除 animation_id, 改查 package 级注册路径

用法 (Windows, 只读源, 产出于 out-dir):
  python scripts/ww_animation_m5_experiment.py <SRC.package> \
      --candidates configs\ww_m4_cheating299_candidates.csv \
      --translations configs\ww_m4_cheating299_translations.csv \
      --chain-map configs\ww_m3_chain_map.csv \
      [--anim-salt 0] [--inspect-only] [--expect-n 1] \
      [--allow-unmapped-chain] [--out-dir output/ww_animation_m5]

ZERO_WRITE_TO_MODS=YES (仅 out-dir 产物, 不碰 Mods)
"""
import argparse
import csv
import hashlib
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
import ww_animation_m3_experiment as _m3
import ww_animation_m4_experiment as _m4  # 复用 DISPLAY/STAGE/NEXT/CLIP + _replace_4fields

WW_ANIM_XML = 0x7DF2169C
ANIM_ID_FIELD = "animation_id"
OUT_DIR = Path("output/ww_animation_m5")


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else None


def gen_anim_id(src_id: int, ordinal: int, zh: str, salt: int) -> int:
    """确定性 64-bit 新 animation_id: blake2b(src_id, ordinal, zh, salt), != src, 全64位空间。"""
    payload = f"{src_id:x}|{ordinal}|{zh}|{salt}".encode("utf-8")
    h = hashlib.blake2b(payload, digest_size=8).digest()  # 64 bit
    val = int.from_bytes(h, "big")
    if val == 0:
        val = 0x8000000000000000
    return val


def _replace_anim_id(block_text, new_id):
    """把 entry 内 animation_id 节点(文本/整数值)替换为 new_id。
    要求恰 1 个节点, 且新值 != 源值 (fail-closed)。
    返回 (src_anim_id, tag, new_block, err)。形态兼容 <I>/<T>/<E>。"""
    root = ET.fromstring(block_text)
    nodes = [el for el in root.iter() if el.get("n") == ANIM_ID_FIELD]
    if len(nodes) != 1:
        return None, None, None, [f"animation_id 节点数={len(nodes)} (需恰1)"]
    el = nodes[0]
    lt = _local(el.tag)
    raw = (el.text or "").strip()
    try:
        # 仅 0x 前缀视为十六进制; 否则一律十进制整数 (真实 ID 为十进制)
        src_val = int(raw, 16) if raw.lower().startswith("0x") else int(raw, 10)
    except ValueError:
        return None, None, None, [f"animation_id 非整数值 {raw!r} (tag={lt})"]
    el.text = str(new_id)
    new_block = ET.tostring(root, encoding="unicode")
    return src_val, lt, new_block, []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="源 WW package")
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--translations", required=True)
    ap.add_argument("--chain-map", default=None)
    ap.add_argument("--anim-salt", type=int, default=0, help="animation_id 派生 salt (去重)")
    ap.add_argument("--expect-n", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--inspect-only", action="store_true")
    ap.add_argument("--allow-unmapped-chain", action="store_true")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: source 不存在 {src}", file=sys.stderr); return 2

    # ---- 读候选 + 翻译 (逐条精确) ----
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
    _m4.CHAIN_MAP = CHAIN_MAP  # _m4._replace_4fields 读的是 m4 模块全局
    print(f"M5 candidates={len(cands)} chain_map={len(CHAIN_MAP)}")

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

    # ---- inspect: dump 每个目标 entry 的 4 字段 + animation_id 形态 ----
    insp_lines = []
    src_anim_ids = {}
    for c in cands:
        ord_s = str(c["ordinal"]).strip()
        ordinal = int(ord_s)
        if ordinal not in entry_blocks:
            print(f"ERROR: ordinal {ordinal} 越界 {n_entries}", file=sys.stderr); return 3
        zh = tr_map[ord_s]
        info = _m3._inspect_entry(entry_blocks[ordinal], ordinal, zh)
        clip_vals = [(el.get("n"), _local(el.tag), (el.text or "").strip())
                     for el in ET.fromstring(entry_blocks[ordinal]).iter() if el.get("n") == "animation_clip_name"]
        ai_raw = None
        for el in ET.fromstring(entry_blocks[ordinal]).iter():
            if el.get("n") == ANIM_ID_FIELD:
                ai_raw = (_local(el.tag), (el.text or "").strip())
        src_anim_ids[ordinal] = ai_raw
        insp_lines.append(f"\n[ordinal {ordinal}]  display_zh={zh!r}  is_story={info['is_story']}")
        insp_lines.append(f"    raw_display = {info['raw_display']!r}")
        insp_lines.append(f"    stage_name  = {info['stage_name']!r}")
        insp_lines.append(f"    clip_name   = {clip_vals}")
        insp_lines.append(f"    animation_id= {ai_raw}   <<< M5 目标")
        insp_lines.append(f"    next_leaves = {info['next_leaves']}")
        miss = [lv["val"] for lv in info["next_leaves"] if lv["val"] and lv["val"] not in CHAIN_MAP]
        insp_lines.append(f"    chain MISS  = {miss}")
    insp_txt = "\n".join(insp_lines)
    print("========== M5 INSPECT (只读结构) ==========")
    print(insp_txt)

    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "m5_inspect_structure.txt").write_text(insp_txt, encoding="utf-8")
    if a.inspect_only:
        print(f"OUT_INSPECT={out_dir / 'm5_inspect_structure.txt'}")
        print("M5_INSPECT=OK (只读, 未生成包)")
        print("ZERO_WRITE_TO_MODS=YES")
        return 0

    # ---- 生产: 逐候选 M4四字段 + animation_id 唯一化 ----
    used_inst = {src_inst}
    used_anim = set()
    jobs = []
    for c in cands:
        ord_s = str(c["ordinal"]).strip()
        ordinal = int(ord_s)
        zh = tr_map[ord_s]
        # 收集 clip (T 节点)
        sc_raw = [(el.get("n"), (el.text or "").strip())
                  for el in ET.fromstring(entry_blocks[ordinal]).iter()
                  if el.get("n") == "animation_clip_name"]
        src_clip_vals = [v for _, v in sc_raw]
        non_t = [el for el in ET.fromstring(entry_blocks[ordinal]).iter()
                 if el.get("n") == "animation_clip_name" and _local(el.tag) != "T"]
        if non_t:
            print(f"ERROR: ordinal {ordinal} clip_name 存在非 T 节点 (fail-closed)", file=sys.stderr); return 3
        if not src_clip_vals:
            print(f"ERROR: ordinal {ordinal} 无 animation_clip_name T 节点", file=sys.stderr); return 3
        zh_clip_list = [f"{v}_zh" for v in src_clip_vals]
        if any(nv == ov for nv, ov in zip(zh_clip_list, src_clip_vals)):
            print(f"ERROR: ordinal {ordinal} 新clip==源clip (无唯一性)", file=sys.stderr); return 3

        # M4 四字段替换
        old_disp, old_clips, block_4, hits, miss, err4 = _m4._replace_4fields(entry_blocks[ordinal], zh, zh_clip_list)
        if old_disp is None or err4:
            print(f"ERROR: ordinal {ordinal} 四字段替换失败: {err4 or 'raw_display 缺失'}", file=sys.stderr); return 3
        if miss and not a.allow_unmapped_chain:
            print(f"ERROR: ordinal {ordinal} next_stages 未映射 {miss} (fail-closed)", file=sys.stderr); return 3

        # animation_id: 读源值 + 派生唯一新值
        ai_raw = src_anim_ids.get(ordinal)
        if ai_raw is None or ai_raw[0] is None:
            print(f"ERROR: ordinal {ordinal} 无 animation_id 节点 (M5 需该字段)", file=sys.stderr); return 3
        tag, rawv = ai_raw
        try:
            src_id = int(rawv, 16) if rawv.lower().startswith("0x") else int(rawv, 10)
        except ValueError:
            print(f"ERROR: ordinal {ordinal} animation_id 非整数值 {rawv!r} (tag={tag})", file=sys.stderr); return 3
        salt = a.anim_salt
        new_id = gen_anim_id(src_id, ordinal, zh, salt)
        while new_id in used_anim or new_id == src_id:
            salt += 1
            new_id = gen_anim_id(src_id, ordinal, zh, salt)
        used_anim.add(new_id)

        # 在新块(4字段已改)上再做 animation_id 替换
        _src2, tag2, block_5, err5 = _replace_anim_id(block_4, new_id)
        if err5 or _src2 != src_id:
            print(f"ERROR: ordinal {ordinal} animation_id 替换失败: {err5 or '值不一致'}", file=sys.stderr); return 3
        new_block = block_5

        inst = _m3._gen_inst(src_inst, ordinal, zh, 0)
        isalt = 0
        while inst in used_inst:
            isalt += 1
            inst = _m3._gen_inst(src_inst, ordinal, zh, isalt)
        used_inst.add(inst)
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
        jobs.append({"ordinal": ordinal, "zh": zh, "clip": zh_clip_list, "old_clip": src_clip_vals,
                     "anim_id_old": src_id, "anim_id_new": new_id,
                     "inst": inst, "body": new_body, "new_xml": new_xml})
        print(f"  job ordinal={ordinal}  display={old_disp!r}->{zh!r}")
        for ov, nv in zip(src_clip_vals, zh_clip_list):
            print(f"        clip {ov!r} -> {nv!r}")
        print(f"        animation_id {src_id} -> {new_id}  (tag={tag2})")
        print(f"        inst=0x{inst:016X}")

    # ---- 组装 (M4/M3 同骨架) ----
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
    print("\n=== 静态验证 (M5) ===")
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
    # V4a: animation_id 唯一且 != 源
    v4_id = all(j["anim_id_new"] != j["anim_id_old"] for j in jobs) and \
            len({j["anim_id_new"] for j in jobs}) == len(jobs)
    print(f"  [V4] 新 animation_id 互异且!=源: {v4_id}"); ok &= v4_id
    out_path = None
    with tempfile.TemporaryDirectory() as td:
        probe = Path(td) / "probe.package"
        wb.build_package(items, probe, header_comp=hdr_comp, major=src_major, minor=src_minor)
        valid, fails, warns = wb.dbpf_metadata_valid(probe)
        print(f"  [V5a] DBPF valid: {valid}  WARNS={len(warns)}"); ok &= valid
        r2, r2err = wb.safe_parse(probe)
        v5b = False
        if r2err is None and r2 is not None:
            ww2 = [e for e in r2.entries if e.type_id == WW_ANIM_XML]
            v5b = len(ww2) == 1 + len(jobs) and {e.instance_id for e in ww2} == {src_inst} | new_insts
        print(f"  [V5b] sidecar 重读 1+N WW_XML: {v5b}"); ok &= v5b
        probe_hc = wb.read_entry_meta_raw(probe)[2]
        om = wb.read_entry_meta_raw(probe)[3]
        v5c = probe_hc == hdr_comp
        src_nonww = {(m["type"], m["group"], m["inst"], int(m["offset_comp"]), int(m["size_comp"]), m["comp_type"])
                     for i, m in enumerate(entries_meta) if (m["type"], m["group"], m["inst"]) != src_tgi}
        out_nonww = {(mo["type"], mo["group"], mo["inst"], int(mo["offset_comp"]), int(mo["size_comp"]), mo["comp_type"])
                     for mo in om if (mo["type"], mo["group"], mo["inst"]) != src_tgi and mo["inst"] not in new_insts}
        v5d = out_nonww == src_nonww
        print(f"  [V5c] header_comp保源={v5c}  非WW保源={v5d}"); ok &= v5c and v5d

    print(f"  STATIC_PASS={ok}")
    if not ok:
        print("M5_STATIC_FAIL -> 不写包 (fail-closed)", file=sys.stderr)
        return 3

    stem = src.stem
    out_path = out_dir / f"{stem}_M5_ANIMID_{len(jobs)}inst.package"
    wb.build_package(items, out_path, header_comp=hdr_comp, major=src_major, minor=src_minor)
    print(f"OUT_PATH={out_path}")
    print(f"OUT_SHA256={wb.sha256(out_path)}")
    print(f"NEW_INSTANCES={[hex(j['inst']) for j in jobs]}")
    anim_map_str = ', '.join(f"{j['ordinal']}: " + str(j["anim_id_old"]) + "->" + str(j["anim_id_new"]) for j in jobs)
    print(f"ANIM_ID_MAP = {anim_map_str}")
    print("M5_BATCH PASS=***")
    print("ZERO_WRITE_TO_MODS=YES (仅本 out-dir 产物)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
