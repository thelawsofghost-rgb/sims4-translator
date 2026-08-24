#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P9 最小 Story key 实验 — 仅改 animation_raw_display_name

背景: P8 证 M3/M4 未破坏 actor graph; Story runtime 仍不注册。
  剩最大嫌疑: Story runtime 以 stage_name / next_stages / clip_name 作为
  内部注册 key。P9 做「最小改动」: 只把 ordinal 的
  animation_raw_display_name 改成中文, 其余(stage/next/clip)全部保持原英文
  字节不变 —— 看这样 Story 是否仍能显示中文 (→ runtime 不依赖这些为 key)。

判定:
  - 成功 (游戏内显示「抓奸 1」) : Story runtime 不把 stage/next/clip 当
    内部 key; 回去逐字段恢复中文化 (显示层本地化即可)
  - 失败 (仍 NOT Caught Cheating) : 问题更可能因改 instance 触发了注册
    失效; 下一步查 instance 注册机制

fail-closed: 源缺->2; 源非单 WW/无 WW XML->3; ordinal 越界->3;
  raw_display 定位失败->3; 静态验证失败->不写包 return 3。
ZERO_WRITE_TO_MODS=YES (仅本 out-dir 产物)。

用法 (Windows):
  python scripts/ww_animation_p9_story_key_min.py "<SRC.package>" \
      --ordinal 299 --display "抓奸 1" [--out-dir output/ww_p9]
产物: output/ww_p9/<stem>_P9_DISPLAYONLY_1inst.package (sidecar, 不写 Mods)
"""
import argparse
import importlib.util
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
_spec = importlib.util.spec_from_file_location(
    "ww_canary_builder", SCRIPT_DIR / "ww_animation_canary_builder.py")
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)
import ww_animation_m3_experiment as _m3
import ww_animation_m4_experiment as _m4

WW_ANIM_XML = 0x7DF2169C
DISPLAY_FIELD = "animation_raw_display_name"
OUT_DIR = Path("output/ww_p9")


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else None


def _replace_1field_display(block_text, zh, old_display):
    """仅替换 raw_display 文本; 其余字段字节不动。
    返回 (old_display, new_block, err)。用 ET 改文本但保证只动 display 节点。"""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(block_text)
    found = None
    for el in root.iter():
        if _local(el.tag) in ("T", "E", "I") and el.get("n") == DISPLAY_FIELD:
            found = el
            break
    if found is None or not (found.text or "").strip():
        return None, None, f"raw_display 定位失败"
    old = (found.text or "").strip()
    found.text = zh
    new_block = ET.tostring(root, encoding="unicode")
    return old, new_block, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="源 WW package")
    ap.add_argument("--ordinal", type=int, default=299)
    ap.add_argument("--display", required=True, help="新中文 display (仅此字段改)")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: source 不存在 {src}", file=sys.stderr); return 2
    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

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
    if a.ordinal not in entry_blocks:
        print(f"ERROR: ordinal {a.ordinal} 越界 {n_entries}", file=sys.stderr); return 3

    src_major, src_minor, hdr_comp, entries_meta = wb.read_entry_meta_raw(src)
    if len(entries_meta) != len(idx.entries):
        print("ERROR: meta/条目数不一致", file=sys.stderr); return 3
    ww_meta = next((m for m in entries_meta if (m["type"], m["group"], m["inst"]) == src_tgi), None)
    src_off_hi = int(ww_meta["offset_comp"]); src_sz_hi = int(ww_meta["size_comp"]); src_f8 = ww_meta["comp_type"]

    zh = a.display
    ordinal = a.ordinal

    # 只读 inspect: 源该条目改前各字段 (证明除 display 外其余不变)
    info = _m3._inspect_entry(entry_blocks[ordinal], ordinal, zh)
    clip_vals = []
    import xml.etree.ElementTree as ET
    rt = ET.fromstring(entry_blocks[ordinal])
    for el in rt.iter():
        if el.get("n") == "animation_clip_name":
            clip_vals.append((_local(el.tag), (el.text or "").strip()))
    print("========== P9 INSPECT (源 ordinal, 改前) ==========")
    print(f"[ordinal {ordinal}]  is_story={info['is_story']}")
    print(f"    raw_display    = {info['raw_display']!r}  ->  改 {zh!r}")
    print(f"    stage_name     = {info['stage_name']!r}  (保持英文)")
    print(f"    next_shape     = {info['next_shape']}")
    print(f"    next_leaves    = {info['next_leaves']}  (保持英文)")
    print(f"    clip_name      = {clip_vals}  (保持英文)")

    # ---- 仅改 display ----
    old_disp, new_block, repl_err = _replace_1field_display(
        entry_blocks[ordinal], zh, info["raw_display"])
    if old_disp is None:
        print(f"ERROR: ordinal {ordinal} {repl_err}", file=sys.stderr); return 3
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

    inst = _m3._gen_inst(src_inst, ordinal, zh, 0)
    salt = 0
    while inst == src_inst:
        salt += 1
        inst = _m3._gen_inst(src_inst, ordinal, zh, salt)
    print(f"  inst=0x{inst:016X}  (与源 0x{src_inst:016X} 不同)")

    # ---- 组装 (含源 WW + 1 新 WW + 全部非 WW 拷贝) ----
    items = [(src_tgi[0], src_tgi[1], src_tgi[2], body_orig,
              {"comp_state": True, "comp_type": src_f8, "mem_size": len(wb.decompress_maybe(body_orig)),
               "offset_high_bit": src_off_hi, "size_high_bit": src_sz_hi})]
    items.append((WW_ANIM_XML, src_tgi[1], inst, new_body,
                  {"comp_state": True, "comp_type": src_f8,
                   "mem_size": len(wb.decompress_maybe(new_body)),
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
    print(f"ITEM_COUNT={len(items)}  (1源WW + 1新WW + {non_ww}非WW)")

    # ---- V1-V5 ----
    print("\n=== 静态验证 (P9) ===")
    ok = True
    v1 = wb.sha256(src) == src_sha_before
    print(f"  [V1] source unchanged: {v1}"); ok &= v1
    v2 = inst != src_inst
    print(f"  [V2] 新inst!=源inst: {v2}"); ok &= v2
    d_src = _m3._entry_displays(xml_text_orig)
    d_new = _m3._entry_displays(new_xml)
    v3 = (d_src is not None and d_new is not None and len(d_src) == len(d_new)
          and [i for i in range(len(d_src)) if (d_src[i] or "") != (d_new[i] or "")] == [ordinal])
    print(f"  [V3] 仅目标 ordinal display 变: {v3}"); ok &= v3
    # V3b: stage/next/clip 必须字节级未变 (只改 display 的核心保证)
    import re as _re
    def _grab(xml_text, pat):
        m = _re.search(pat, xml_text)
        return m.group(1) if m else None
    st_pat = r'<T\b[^>]*\bn="animation_stage_name"[^>]*>(.*?)</T>'
    nx_pat = r'<L\b[^>]*\bn="animation_next_stages"[^>]*>(.*?)</L>'
    cl_pat = r'<L\b[^>]*\bn="animation_clip_name"[^>]*>(.*?)</L>'
    v3b = (_grab(xml_text_orig, st_pat) == _grab(new_xml, st_pat)
           and _grab(xml_text_orig, nx_pat) == _grab(new_xml, nx_pat)
           and _grab(xml_text_orig, cl_pat) == _grab(new_xml, cl_pat))
    print(f"  [V3b] stage/next_stages/clip_name 字节未变: {v3b}"); ok &= v3b
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
            v4b = len(ww2) == 2 and {e.instance_id for e in ww2} == {src_inst, inst}
        print(f"  [V4b] sidecar 重读 2 WW_XML (源+新): {v4b}"); ok &= v4b
        probe_hc = wb.read_entry_meta_raw(probe)[2]
        om = wb.read_entry_meta_raw(probe)[3]
        v5 = probe_hc == hdr_comp
        src_nonww = {(m["type"], m["group"], m["inst"], int(m["offset_comp"]), int(m["size_comp"]), m["comp_type"])
                     for i, m in enumerate(entries_meta) if (m["type"], m["group"], m["inst"]) != src_tgi}
        out_nonww = {(mo["type"], mo["group"], mo["inst"], int(mo["offset_comp"]), int(mo["size_comp"]), mo["comp_type"])
                     for mo in om if (mo["type"], mo["group"], mo["inst"]) != src_tgi and mo["inst"] != inst}
        v5_nonww = out_nonww == src_nonww
        print(f"  [V5] header_comp保源={v5} 非WW保源={v5_nonww}"); ok &= v5 and v5_nonww

    print(f"  STATIC_PASS={ok}")
    if not ok:
        print("P9_STATIC_FAIL -> 不写包 (fail-closed)", file=sys.stderr)
        return 3

    stem = src.stem
    out_path = out_dir / f"{stem}_P9_DISPLAYONLY_{ordinal}inst.package"
    wb.build_package(items, out_path, header_comp=hdr_comp, major=src_major, minor=src_minor)
    print(f"OUT_PATH={out_path}")
    print(f"OUT_SHA256={wb.sha256(out_path)}")
    print(f"NEW_INSTANCE=0x{inst:016X}")
    print(f"P9_SUMMARY 仅改字段: animation_raw_display_name -> {zh!r}")
    print("P9_KEPT_ENGLISH: animation_stage_name / animation_next_stages / animation_clip_name")
    print("P9_BATCH PASS=YES")
    print("ZERO_WRITE_TO_MODS=YES (仅本 out-dir 产物)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
