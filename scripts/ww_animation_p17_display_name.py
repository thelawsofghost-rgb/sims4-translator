#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P17 最小 animation_display_name 实验 — 仅 ordinal 299, 仅插入/改该字段

依据 P16 真机字节码:
  _create_sex_animation_instance 读取
    animation_override.animation_raw_display_name
    -> animation_tuning.animation_raw_display_name
    -> animation_tuning.animation_display_name
  然后 STORE display_name。

P9/P10 改 animation_raw_display_name 无效。
=> P17 验证: Story 显示是否来自 animation_display_name。

做法 (sidecar, 不写 Mods):
  - 只改 ordinal 299 一个动画条目
  - 只动 animation_display_name:
      * 若源条目已含该字段 -> 替换其文本为中文
      * 若源条目不含 -> 插入一个 <T n="animation_display_name">中文</T>
  - animation_raw_display_name / animation_stage_name / animation_next_stages
    / animation_clip_name 全保持原值(字节级)
  - 生成 sidecar package (源 WW + 1 新 WW), 不写 Mods
  - 静态验证 (fail-closed: 验证失败不写包)

判定:
  - 成功(游戏内 Story 显示中文) : animation_display_name 是显示源;
    -> 后续对 Story 统一写入该字段即可本地化
  - 失败(仍英文/未变) : 显示源不再 animation_display_name, 继续查
    (STBL/localize/hash 结果 -> 运行时本地化, 见 P16 候选调用)

fail-closed: 源缺->2; 无 WW/多 WW->3; ordinal 越界->3;
  display 插入失败->3; 静态验证失败->不写包 return 3。
ZERO_WRITE_TO_MODS=YES (仅本 out-dir 产物)。

用法 (Windows):
  python scripts/ww_animation_p17_display_name.py "<SRC.package>" \
      --ordinal 299 --display "抓奸 1" [--out-dir output/ww_p17]
"""
import argparse
import importlib.util
import re as _re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
_spec = importlib.util.spec_from_file_location(
    "ww_canary_builder", SCRIPT_DIR / "ww_animation_canary_builder.py")
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)
import ww_animation_m3_experiment as _m3

WW_ANIM_XML = 0x7DF2169C
TARGET_FIELD = "animation_display_name"     # P16 真机发现字段
KEEP_FIELDS = ("animation_raw_display_name", "animation_stage_name",
               "animation_next_stages", "animation_clip_name")
OUT_DIR = Path("output/ww_p17")


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else None


def _apply_display_name(block_text, zh):
    """对单条目: 若已有 animation_display_name 则替换文本; 否则插入该节点。
    其余节点(文本/结构)一律不动。
    返回 (existed: bool, new_block: str, err: str|None)."""
    root = ET.fromstring(block_text)
    target = None
    for el in root.iter():
        if _local(el.tag) in ("T", "E", "I") and el.get("n") == TARGET_FIELD:
            target = el
            break
    existed = target is not None
    if target is not None:
        target.text = zh
    else:
        new_el = ET.SubElement(root, "T")
        new_el.set("n", TARGET_FIELD)
        new_el.text = zh
    new_block = ET.tostring(root, encoding="unicode")
    return existed, new_block, None


def _field_text_map(block_text):
    """返回 {(n): text} 及 节点名集合, 用于逐字段比对。"""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(block_text)
    m = {}
    names = []
    for el in root.iter():
        n = el.get("n")
        if n is None:
            continue
        ln = _local(el.tag)
        names.append(n)
        if ln in ("T", "E", "I"):
            m[n] = (el.text or "").strip()
    return m, names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--ordinal", type=int, default=299)
    ap.add_argument("--display", required=True, help="新中文 animation_display_name")
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

    # ---- 只读 inspect: 源条目改前各字段 ----
    src_m, src_names = _field_text_map(entry_blocks[ordinal])
    print("========== P17 INSPECT (源 ordinal, 改前) ==========")
    print(f"[ordinal {ordinal}]  字段数={len(src_m)}")
    for k, v in src_m.items():
        print(f"    {k} = {v!r}")
    existed, new_block, repl_err = _apply_display_name(entry_blocks[ordinal], zh)
    if repl_err is not None:
        print(f"ERROR: {repl_err}", file=sys.stderr); return 3
    if not existed:
        print(f"  -> 源无 {TARGET_FIELD}, P17 将插入 <T n=\"{TARGET_FIELD}\">{zh}</T>")
    else:
        print(f"  -> 源已有 {TARGET_FIELD}, 替换为 {zh!r}")

    # ---- 组装新条目 ----
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

    # ---- 组装 sidecar (源 WW + 1 新 WW + 全部非 WW) ----
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

    # ---- 静态验证 ----
    print("\n=== 静态验证 (P17) ===")
    ok = True
    v1 = wb.sha256(src) == src_sha_before
    print(f"  [V1] source unchanged: {v1}"); ok &= v1
    v2 = inst != src_inst
    print(f"  [V2] 新inst!=源inst: {v2}"); ok &= v2

    # V3: 新条目字段级验证 —— 除 animation_display_name 外全部文本一致
    new_m, new_names = _field_text_map(new_block)
    common_shared = []
    diff = []
    for k in sorted(set(src_m) | set(new_m)):
        sv = src_m.get(k)
        nv = new_m.get(k)
        if k != TARGET_FIELD:
            if sv != nv:
                diff.append(f"{k}: {sv!r} -> {nv!r}")
        else:
            if nv != zh:
                diff.append(f"{TARGET_FIELD}: = {nv!r} (期望 {zh!r})")
    v3_no_diff = len(diff) == 0
    v3_name_only = (set(new_names) - set(src_names)) in (set(), {TARGET_FIELD})
    v3_target_present = new_m.get(TARGET_FIELD) == zh
    v3 = v3_no_diff and v3_name_only and v3_target_present
    print(f"  [V3] 除 {TARGET_FIELD} 外零字段变化: {v3_no_diff}")
    for dd in diff[:10]:
        print(f"       DIFF: {dd}")
    print(f"  [V3b] 仅新增/替换 {TARGET_FIELD} 节点名: {v3_name_only}  目标值命中: {v3_target_present}")
    ok &= v3

    # V3c: KEEP_FIELDS 必须字节级一致 (raw/stage/next/clip 全保原值, 覆盖 P17 硬约束)
    def _grab(txt, pat):
        m = _re.search(pat, txt)
        return m.group(1) if m else None
    keep_pats = {
        "animation_raw_display_name": r'<T\b[^>]*\bn="animation_raw_display_name"[^>]*>(.*?)</T>',
        "animation_stage_name": r'<T\b[^>]*\bn="animation_stage_name"[^>]*>(.*?)</T>',
        "animation_next_stages": r'<L\b[^>]*\bn="animation_next_stages"[^>]*>(.*?)</L>',
        "animation_clip_name": r'<L\b[^>]*\bn="animation_clip_name"[^>]*>(.*?)</L>',
    }
    v3c = True
    for fn, pat in keep_pats.items():
        same = _grab(entry_blocks[ordinal], pat) == _grab(new_block, pat)
        print(f"  [V3c] {fn} 字节未变: {same}")
        v3c &= same
    ok &= v3c

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
        print("P17_STATIC_FAIL -> 不写包 (fail-closed)", file=sys.stderr)
        return 3

    stem = src.stem
    out_path = out_dir / f"{stem}_P17_DISPLAYNAME_{ordinal}inst.package"
    wb.build_package(items, out_path, header_comp=hdr_comp, major=src_major, minor=src_minor)
    print(f"OUT_PATH={out_path}")
    print(f"OUT_SHA256={wb.sha256(out_path)}")
    print(f"NEW_INSTANCE=0x{inst:016X}")
    print(f"P17_SUMMARY 仅改字段: animation_display_name={zh!r} (原条目{'替换' if existed else '插入'})")
    print(f"P17_KEPT_BYTE_IDENTICAL: {', '.join(KEEP_FIELDS)}")
    print("P17_BATCH PASS=YES")
    print("ZERO_WRITE_TO_MODS=YES (仅本 out-dir 产物)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
