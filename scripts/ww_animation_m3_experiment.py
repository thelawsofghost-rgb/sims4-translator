#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M3 最小实验: STORY stage-chain 同步本地化 (Caught Cheating 1-8 试点)。

根因 (M2 真机已证): WW 对 STORY 动画维护 stage 链, 字段 animation_next_stages。
  M2 只改了 animation_raw_display_name, 导致:
    抓奸 1 -> next_stage = "Caught Cheating 2"   (旧名, 链断裂)
  游戏显示 "NOT Caught Cheating X"。

M3 假设: 链以 stage 名(文本)为链接键。要本地化 STORY 动画, 需同步改 3 个字段:
  1. animation_raw_display_name  -> zh
  2. animation_stage_name        -> zh   (链的链接键)
  3. animation_next_stages 内每个文本叶子
       其值 == 已知源 stage 名 -> 对应 zh   (链的后继)
  其余字段一律不动。

两种模式:
  --inspect-only (只读, 默认先跑): dump 每个目标 ordinal 的
      animation_next_stages 真实节点结构 + 全部 stage 相关字段值,
      确认节点形态(knows leaf 是 T 文本 or 包壳)再做替换。ZERO_WRITE。
  (默认生产): 生成 1 源WW + N 新instance sidecar, 每个新 XML 仅目标 ordinal 的
      上述 3 类字段改动, 其余字节保源。V1-V5 静态门全绿才写包。

输入:
  --source        真实 WW package
  --candidates    ww_m2_candidates.csv (ordinal, display_name, ...)
  --translations  CSV: ordinal, display_name, chinese_translation (逐条精确)
  --chain-map     可选 CSV: source_value, chinese_value
                   供不在候选 per-ordinal 之内的链叶子 (如终端 "Caught Cheating 9 - Climax")
  --expect-n      断言候选数
  --force         跳过 expect-n
  --inspect-only  只 dump 结构, 不生成包
  --allow-unmapped-chain  允许 next_stages 存在未映射叶子 (默认 fail-closed)

fail-closed (生产): 候选/译文缺失、ordinal 越界、display/stage/链定位非唯一、
  链叶子无映射(默认)、任一 V1-V5 失败 -> 不写包。
ZERO_WRITE_TO_MODS=YES
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

DISPLAY_FIELD = "animation_raw_display_name"
STAGE_FIELD = "animation_stage_name"
NEXT_FIELD = "animation_next_stages"
WW_ANIM_XML = 0x7DF2169C
ENTRY_LIST_FIELD = "animations_list"
OUT_DIR = Path("output/ww_animation_m3")

# 复用 M2 batch 的核心函数 (保持单一实现来源)
import ww_animation_m2_batch as _m2
_locale = lambda n: n.rsplit('}', 1)[-1] if isinstance(n, str) else None
_loc_anim = _m2._locate_animations_list
_entry_blocks = _m2._entry_blocks
_gen_inst = _m2.gen_instance
_entry_displays = _m2._entry_displays


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else None


def _text_leaves(el, acc):
    """收集元素下(含自身)所有 T/E 文本叶子。(tag, text)。"""
    for c in el.iter():
        lt = _local(c.tag)
        if lt in ("T", "E"):
            acc.append((c, (c.text or "").strip()))
        elif lt == "I":
            # I 节点也带值 (stage id), 一并收集但标注类型
            acc.append((c, (c.text or "").strip()))


def _inspect_entry(block_text, ordinal, display_zh):
    """dump 一个 entry 的 stage 结构。返回 (结构描述, 链叶子列表, 是否 story)。"""
    root = ET.fromstring(block_text)
    info = {"ordinal": ordinal, "display_zh": display_zh, "is_story": False,
            "raw_display": "", "stage_name": "", "next_leaves": [], "next_shape": [],
            "other_stage_fields": {}}
    for el in root.iter():
        n = el.get("n")
        if not n:
            continue
        lt = _local(el.tag)
        if n == DISPLAY_FIELD and lt == "T":
            info["raw_display"] = (el.text or "").strip()
        elif n == STAGE_FIELD:
            info["is_story"] = True
            info["stage_name"] = (el.text or "").strip()
        elif n == NEXT_FIELD:
            info["is_story"] = True
            if lt == "L":
                info["next_shape"].append(("L(list)", len(list(el)) if hasattr(el, '__len__') or True else 0))
                leaves = []
                _text_leaves(el, leaves)
                for c, v in leaves:
                    info["next_leaves"].append({"tag": _local(c.tag), "n": c.get("n"), "val": v})
            else:
                leaves = []
                _text_leaves(el, leaves)
                info["next_shape"].append((lt, len(list(el))))
                for c, v in leaves:
                    info["next_leaves"].append({"tag": _local(c.tag), "n": c.get("n"), "val": v})
        elif any(h in n.lower() for h in ("stage", "story", "next", "chain", "ae_")):
            # 其它疑似 stage/story 字段都记录下来
            vs = (el.text or "").strip()
            if lt == "T":
                info["other_stage_fields"][n] = vs
            elif lt == "L":
                info["other_stage_fields"][n] = f"[list {len(list(el))}]"
    return info


def _replace_3fields(block_text, zh):
    """替换 raw_display_name / stage_name / next_stages 内匹配文本。返回 (old_display, new_block, err)。"""
    root = ET.fromstring(block_text)
    err = []
    # 1. raw_display_name / stage_name (都是单文本 T)
    replaced_display = None
    for el in root.iter():
        n = el.get("n")
        if not n:
            continue
        lt = _local(el.tag)
        if n == DISPLAY_FIELD and lt == "T":
            replaced_display = replaced_display or (el.text or "")
            el.text = zh
        elif n == STAGE_FIELD and lt == "T":
            el.text = zh
    # 2. next_stages: 每个文本叶子, 若值在链映射 -> 替换
    chain_hits = []
    chain_misses = []
    for el in root.iter():
        n = el.get("n")
        if n != NEXT_FIELD:
            continue
        leaves = []
        _text_leaves(el, leaves)
        for c, v in leaves:
            if v in CHAIN_MAP:
                c.text = CHAIN_MAP[v]
                chain_hits.append(v)
            elif v:
                chain_misses.append(v)
    new_block = ET.tostring(root, encoding="unicode")
    return replaced_display, new_block, chain_hits, chain_misses


def build_chain_map(translations_path, chain_map_path):
    """统一链映射: per-ordinal(display->zh) + 显式 chain-map(source->zh)。"""
    m = {}
    with open(translations_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            src = (r.get("display_name") or "").strip()
            zh = (r.get("chinese_translation") or "").strip()
            if src and src not in m:
                m[src] = zh
            elif src:
                # 冲突: 同一 source 两个不同 zh -> 拒绝
                if m[src] != zh:
                    print(f"ERROR: 链映射冲突 {src!r}: {m[src]!r} vs {zh!r}", file=sys.stderr)
                    return None
    if chain_map_path:
        with open(chain_map_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                src = (r.get("source_value") or "").strip()
                zh = (r.get("chinese_value") or "").strip()
                if not src:
                    continue
                if src in m and m[src] != zh:
                    print(f"ERROR: chain-map 冲突 {src!r}: {m[src]!r} vs {zh!r}", file=sys.stderr)
                    return None
                m[src] = zh
    return m


CHAIN_MAP = {}


def main():
    global CHAIN_MAP
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--translations", required=True)
    ap.add_argument("--chain-map", default=None)
    ap.add_argument("--expect-n", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--inspect-only", action="store_true",
                    help="只 dump next_stages 真实结构, 不生成包 (ZERO_WRITE)")
    ap.add_argument("--allow-unmapped-chain", action="store_true")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: source 不存在 {src}", file=sys.stderr); return 2

    cands = []
    with open(a.candidates, newline="", encoding="utf-8") as f:
        cands = list(csv.DictReader(f))
    if a.expect_n is not None and not a.force and len(cands) != a.expect_n:
        print(f"ERROR: 候选数 {len(cands)} != 预期 {a.expect_n} (fail-closed)", file=sys.stderr); return 2
    tr_map = {}
    with open(a.translations, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            tr_map[str(r["ordinal"]).strip()] = (r.get("display_name") or "").strip(), r["chinese_translation"].strip()
    missing = [str(c["ordinal"]).strip() for c in cands if str(c["ordinal"]).strip() not in tr_map]
    if missing:
        print(f"ERROR: 缺少翻译 ordinal: {missing} (fail-closed)", file=sys.stderr); return 2

    CHAIN_MAP = build_chain_map(a.translations, a.chain_map)
    if CHAIN_MAP is None:
        return 2
    print(f"M3 candidates={len(cands)} translations={len(tr_map)} chain_map={len(CHAIN_MAP)}")

    # ---- 源解析 ----
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
    inner, inner_start, inner_end = _loc_anim(xml_text_orig)
    if inner is None:
        print("ERROR: 找不到 animations_list", file=sys.stderr); return 3
    blocks = _entry_blocks(inner)
    entry_blocks = {}
    eidx = 0
    for b, _rng in blocks:
        if b.lstrip().startswith("<U"):
            entry_blocks[eidx] = b
            eidx += 1
    n_entries = eidx
    src_major, src_minor, hdr_comp, entries_meta = wb.read_entry_meta_raw(src)
    if len(entries_meta) != len(idx.entries):
        print("ERROR: meta/条目数不一致", file=sys.stderr); return 3
    ww_meta = next((m for m in entries_meta if (m["type"], m["group"], m["inst"]) == src_tgi), None)
    if ww_meta is None:
        print("ERROR: 无源 WW meta", file=sys.stderr); return 3
    src_off_hi = int(ww_meta["offset_comp"]); src_sz_hi = int(ww_meta["size_comp"]); src_f8 = ww_meta["comp_type"]

    # ---- inspect-only: dump 每个目标的 stage 结构 ----
    insp_lines = []
    for c in cands:
        ord_s = str(c["ordinal"]).strip()
        ordinal = int(ord_s)
        if ordinal not in entry_blocks:
            print(f"ERROR: ordinal {ordinal} 越界 {n_entries}", file=sys.stderr); return 3
        _, zh = tr_map.get(ord_s, ("", ""))
        info = _inspect_entry(entry_blocks[ordinal], ordinal, zh)
        insp_lines.append(f"\n[ordinal {ordinal}]  display_zh={zh!r}  is_story={info['is_story']}")
        insp_lines.append(f"    raw_display   = {info['raw_display']!r}")
        insp_lines.append(f"    stage_name    = {info['stage_name']!r}")
        insp_lines.append(f"    next_shape    = {info['next_shape']}")
        insp_lines.append(f"    next_leaves   = {info['next_leaves']}")
        if info["other_stage_fields"]:
            insp_lines.append(f"    other_stage   = {info['other_stage_fields']}")
        else:
            insp_lines.append(f"    other_stage   = (无)")
        # chain 映射命中/缺失
        hits = [lv["val"] for lv in info["next_leaves"] if lv["val"] in CHAIN_MAP]
        misses = [lv["val"] for lv in info["next_leaves"] if lv["val"] and lv["val"] not in CHAIN_MAP]
        insp_lines.append(f"    chain hit     = {hits}")
        insp_lines.append(f"    chain MISS    = {misses}")
    insp_txt = "\n".join(insp_lines)
    print("========== M3 INSPECT (只读结构) ==========")
    print(insp_txt)

    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "m3_inspect_structure.txt").write_text(insp_txt, encoding="utf-8")

    if a.inspect_only:
        print(f"OUT_INSPECT={out_dir / 'm3_inspect_structure.txt'}")
        print("M3_INSPECT=OK (只读, 未生成包)")
        print("ZERO_WRITE_TO_MODS=YES")
        return 0

    # ---- 生产: 逐候选替换 3 类字段 ----
    used = {src_inst}
    jobs = []
    for c in cands:
        ord_s = str(c["ordinal"]).strip()
        ordinal = int(ord_s)
        _, zh = tr_map.get(ord_s, ("", ""))
        old, new_block, hits, miss = _replace_3fields(entry_blocks[ordinal], zh)
        if old is None:
            print(f"ERROR: ordinal {ordinal} raw_display 定位失败", file=sys.stderr); return 3
        if miss and not a.allow_unmapped_chain:
            print(f"ERROR: ordinal {ordinal} next_stages 有未映射叶子 {miss} (fail-closed; "
                  f"加 --chain-map 或 --allow-unmapped-chain)", file=sys.stderr); return 3
        inst = _gen_inst(src_inst, ordinal, zh, 0)
        salt = 0
        while inst in used:
            salt += 1
            inst = _gen_inst(src_inst, ordinal, zh, salt)
        used.add(inst)
        new_inner = []
        ei2 = 0
        for b, _rng in blocks:
            if b.lstrip().startswith("<U"):
                new_inner.append(new_block if ei2 == ordinal else b)
                ei2 += 1
            else:
                new_inner.append(b)
        new_xml = xml_text_orig[:inner_start] + "".join(new_inner) + xml_text_orig[inner_end:]
        new_body = wb.compress_like(body_orig, new_xml.encode("utf-8"))
        jobs.append({"ordinal": ordinal, "zh": zh, "inst": inst, "body": new_body,
                     "new_xml": new_xml, "chain_hits": hits, "chain_miss": miss})
        print(f"  job ordinal={ordinal}  display={old!r}->{zh!r}  inst=0x{inst:016X}  chain_hits={hits}")

    # ---- 组装 ----
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
    print("\n=== 静态验证 (M3) ===")
    ok = True
    v1 = wb.sha256(src) == src_sha_before
    print(f"  [V1] source unchanged: {v1}"); ok &= v1
    new_insts = {j["inst"] for j in jobs}
    v2 = all(i != src_inst for i in new_insts) and len(new_insts) == len(jobs)
    print(f"  [V2] 新inst互异且!=源: {v2}"); ok &= v2
    # V3: 只用 display 判(链字段解析对 display 无影响), 仅目标 ordinal 变
    d_src = _entry_displays(xml_text_orig)
    v3 = True
    for j in jobs:
        d_new = _entry_displays(j["new_xml"])
        if d_src is None or d_new is None or len(d_src) != len(d_new):
            v3 = False; break
        diffs = [i for i in range(len(d_src)) if (d_src[i] or "") != (d_new[i] or "")]
        if diffs != [j["ordinal"]]:
            v3 = False; break
    print(f"  [V3] 每新XML仅目标 display 变: {v3}"); ok &= v3
    # V4: reparse
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
        print("M3_STATIC_FAIL -> 不写包 (fail-closed)", file=sys.stderr)
        return 3

    # ---- 写包 ----
    stem = src.stem
    out_path = out_dir / f"{stem}_M3_BATCH_{len(jobs)}inst.package"
    wb.build_package(items, out_path, header_comp=hdr_comp, major=src_major, minor=src_minor)
    print(f"OUT_PATH={out_path}")
    print(f"OUT_SHA256={wb.sha256(out_path)}")
    new_inst_list = [hex(j["inst"]) for j in jobs]
    print(f"NEW_INSTANCES={new_inst_list}")
    print("M3_BATCH PASS=YES")
    print("ZERO_WRITE_TO_MODS=YES (仅本 out-dir 产物)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
