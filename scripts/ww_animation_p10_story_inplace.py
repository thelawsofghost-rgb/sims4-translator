#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P10 最小 Story key 实验 (原地) — 在原 WW_ANIM_XML instance 内仅改 ordinal 的 raw_display

背景: P9 在【新克隆 instance】上仅改 raw_display 仍 NOT -> 怀疑 Story runtime
  锁定【原 XML instance】。P10 做对照: 不新增 instance, 直接把 ordinal 的
  animation_raw_display_name 在原 WW_ANIM_XML instance 内改中文,
  stage/next/clip 完全不动。

判定:
  - 成功 (游戏内显示「抓奸 1」) : Story runtime 锁定原 XML instance;
    -> Story 本地化必须【原地编辑原 instance】(不重建 instance)
  - 失败 (仍 NOT Caught Cheating) : 连原 instance 原地改 display 也不显示
    -> Story 的显示根本不来自 animation_raw_display_name; 该字段对
      Story 不生效, 显示文本另有所在 (如 STBL/其他资源) -> 转向显示源调查

fail-closed: 源缺->2; 无/多 WW XML->3; ordinal 越界->3; display 定位/不唯一->3;
  静态验证失败->不写包 return 3。ZERO_WRITE_TO_MODS=YES (仅本 out-dir)。

用法 (Windows):
  python scripts/ww_animation_p10_story_inplace.py "<SRC.package>" \
      --ordinal 299 --display "抓奸 1" [--out-dir output/ww_p10]
产物: output/ww_p10/<stem>_P10_INPLACE_ord<ord>.package (sidecar, 不写 Mods;
  其 WW_ANIM_XML instance == 源 instance)
"""
import argparse
import importlib.util
import sys
import tempfile
from pathlib import Path
import xml.etree.ElementTree as ET

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
_spec = importlib.util.spec_from_file_location(
    "ww_canary_builder", SCRIPT_DIR / "ww_animation_canary_builder.py")
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)
import ww_animation_testc_reimport as _ri

WW_ANIM_XML = 0x7DF2169C
FIELD = "animation_raw_display_name"
OUT_DIR = Path("output/ww_p10")


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="源 WW package")
    ap.add_argument("--ordinal", type=int, default=299)
    ap.add_argument("--display", required=True, help="新中文 display (仅此字段改, 原地)")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: source 不存在 {src}", file=sys.stderr); return 2
    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    ordinal, zh = a.ordinal, a.display

    src_sha_before = wb.sha256(src)
    idx, err = wb.safe_parse(src)
    if err is not None or idx is None:
        print(f"ERROR: source 解析失败: {err}", file=sys.stderr); return 3
    ww = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    if len(ww) != 1:
        print(f"ERROR: 需单 WW_ANIM_XML; 实际={len(ww)}", file=sys.stderr); return 3
    ww_e = ww[0]
    src_tgi = (ww_e.type_id, ww_e.group_id, ww_e.instance_id)
    inst = ww_e.instance_id
    print(f"P10_RUN_MODE=INPLACE (同一 instance)  FIELD={FIELD}  TARGET_ORDINAL={ordinal}  "
          f"WW_XML_INSTANCE=0x{inst:016X}")

    src_major, src_minor, hdr_comp, entries_meta = wb.read_entry_meta_raw(src)
    if len(entries_meta) != len(idx.entries):
        print(f"ERROR: meta/条目数不一致 ({len(entries_meta)} vs {len(idx.entries)})", file=sys.stderr); return 3
    ww_meta = next((m for m in entries_meta if (m["type"], m["group"], m["inst"]) == src_tgi), None)
    if ww_meta is None:
        print("ERROR: 找不到 WW XML 原始 metadata", file=sys.stderr); return 3

    body_orig = wb.read_body_raw(src, ww_e)
    _schema, xml_text_orig, xerr = wb.parse_anim_xml(body_orig)
    if xerr is not None:
        print(f"ERROR: WW XML 解析失败: {xerr}", file=sys.stderr); return 3
    inner, inner_start, inner_end = _ri._locate_animations_list(xml_text_orig)
    if inner is None:
        print("ERROR: 找不到 animations_list", file=sys.stderr); return 3
    blocks = _ri._entry_blocks(inner)
    n_entries = len([b for b in blocks if b[0].lstrip().startswith("<U")])
    if ordinal >= n_entries:
        print(f"ERROR: ordinal {ordinal} 越界 {n_entries}", file=sys.stderr); return 3

    # 定位目标 entry
    entry_idx = 0
    target = None
    for b, (bs, be) in blocks:
        if b.lstrip().startswith("<U"):
            if entry_idx == ordinal:
                target = (b, bs, be); break
            entry_idx += 1
    btext = target[0]
    # 探 old display (唯一)
    old_probe = None
    try:
        troot = ET.fromstring(btext)
        tnodes = [t for t in troot.iter() if _local(t.tag) == "T" and t.get("n") == FIELD]
        old_probe = (tnodes[0].text or "") if len(tnodes) == 1 else None
        node_cnt = len(tnodes)
    except Exception:
        node_cnt = -1
    if old_probe is None:
        print(f"ERROR: entry[{ordinal}] {FIELD} 定位失败/非唯一 display (node_cnt={node_cnt})", file=sys.stderr); return 3
    old_val = old_probe

    _old2, new_block, node_cnt2 = _ri._replace_one_display(btext, zh)
    if node_cnt2 != 1 or _old2 != old_val:
        print(f"ERROR: entry[{ordinal}] {FIELD} 二次替换不一致 (node_cnt={node_cnt2})", file=sys.stderr); return 3

    # 拼接新 inner (仅该 entry 替换, gap 原样) — 与 reimport 相同
    new_inner_parts = []
    entry_idx2 = 0
    for b, _rng in blocks:
        if b.lstrip().startswith("<U"):
            new_inner_parts.append(new_block if entry_idx2 == ordinal else b)
            entry_idx2 += 1
        else:
            new_inner_parts.append(b)
    new_xml = xml_text_orig[:inner_start] + "".join(new_inner_parts) + xml_text_orig[inner_end:]

    # 校验: 仅目标 display 变
    def _entry_displays(txt):
        root = ET.fromstring(txt)
        lst = None
        for node in root.iter():
            if _local(node.tag) == "L" and node.get("n") == "animations_list":
                lst = node; break
        if lst is None:
            return None
        out = []
        for c in lst:
            if _local(c.tag) == "U":
                d = next((tt.text for tt in c.iter()
                          if _local(tt.tag) == "T" and tt.get("n") == FIELD), None)
                out.append(d)
        return out
    d_src = _entry_displays(xml_text_orig)
    d_new = _entry_displays(new_xml)
    if d_src is None or d_new is None or len(d_src) != len(d_new):
        print("ERROR: display 枚举失败/计数不一致", file=sys.stderr); return 3
    diff_idx = [i for i in range(len(d_src)) if (d_src[i] or "") != (d_new[i] or "")]
    print(f"TARGET_DIFF_INDICES={diff_idx}")
    if diff_idx != [ordinal]:
        print(f"ERROR: 应仅 ordinal {ordinal} 变化; 实际={diff_idx}", file=sys.stderr); return 3

    new_body = wb.compress_like(body_orig, new_xml.encode("utf-8"))
    src_off_hi = int(ww_meta["offset_comp"]); src_sz_hi = int(ww_meta["size_comp"]); src_f8 = ww_meta["comp_type"]
    item_meta = {"comp_state": True, "comp_type": src_f8,
                 "mem_size": len(wb.decompress_maybe(new_body)),
                 "offset_high_bit": src_off_hi, "size_high_bit": src_sz_hi}

    # 组装: 全部条目源样; WW XML 用【同一 instance】, 仅 body 换 (原地)
    items = []
    for idx_i, e in enumerate(idx.entries):
        meta = entries_meta[idx_i]
        t, g, i2 = e.type_id, e.group_id, e.instance_id
        if (t, g, i2) == src_tgi:
            items.append((t, g, inst, new_body, item_meta))  # 同一 instance!
        else:
            body = wb.read_body_raw(src, e)
            other_meta = {"comp_state": bool(meta["comp_type"]), "comp_type": meta["comp_type"],
                          "mem_size": meta["mem_size"],
                          "offset_high_bit": int(meta["offset_comp"]), "size_high_bit": int(meta["size_comp"])}
            items.append((t, g, i2, body, other_meta))
    print(f"ITEM_COUNT={len(items)}  (源 {len(idx.entries)}); WW_XML 同 instance 原地替换")

    # ---- V1-V5 (reimport 语义, 但 instance 必须 = 源) ----
    print("\n=== 静态验证 (P10) ===")
    ok = True
    v1 = wb.sha256(src) == src_sha_before
    print(f"  [V1] source unchanged: {v1}"); ok &= v1
    v2 = inst == src_tgi[2]
    print(f"  [V2] instance 未变 (原地): {v2}"); ok &= v2
    v3 = diff_idx == [ordinal]
    print(f"  [V3] target diff_count==1 @ordinal{ordinal}: {v3}"); ok &= v3
    # V3b: stage/next/clip 字节未变
    import re as _re
    def _grab(t, pat):
        m = _re.search(pat, t); return m.group(1) if m else None
    st = r'<T\b[^>]*\bn="animation_stage_name"[^>]*>(.*?)</T>'
    nx = r'<L\b[^>]*\bn="animation_next_stages"[^>]*>(.*?)</L>'
    cl = r'<L\b[^>]*\bn="animation_clip_name"[^>]*>(.*?)</L>'
    v3b = (_grab(xml_text_orig, st) == _grab(new_xml, st)
           and _grab(xml_text_orig, nx) == _grab(new_xml, nx)
           and _grab(xml_text_orig, cl) == _grab(new_xml, cl))
    print(f"  [V3b] stage/next_stages/clip_name 字节未变: {v3b}"); ok &= v3b
    with tempfile.TemporaryDirectory() as td:
        probe = Path(td) / "probe.package"
        wb.build_package(items, probe, header_comp=hdr_comp, major=src_major, minor=src_minor)
        v4, _f, _w = wb.dbpf_metadata_valid(probe)
        print(f"  [V4] sidecar DBPF valid: {v4}"); ok &= v4
        r2, r2err = wb.safe_parse(probe)
        v4b = False
        if r2err is None and r2 is not None:
            ww2 = [e for e in r2.entries if e.type_id == WW_ANIM_XML]
            v4b = len(ww2) == 1 and ww2[0].instance_id == inst and len(r2.entries) == len(idx.entries)
        print(f"  [V4b] sidecar 重读: 单WW_XML同实例={v4b}, entry数保源"); ok &= v4b
        probe_hc = wb.read_entry_meta_raw(probe)[2]
        om = wb.read_entry_meta_raw(probe)[3]
        v5 = probe_hc == hdr_comp
        # 非 WW 条目必须 TGI+高位+comp_type 全部保源 (防破坏其它资源)
        src_nonww = {(m["type"], m["group"], m["inst"], int(m["offset_comp"]),
                      int(m["size_comp"]), m["comp_type"])
                     for i, m in enumerate(entries_meta)
                     if (m["type"], m["group"], m["inst"]) != src_tgi}
        out_nonww = {(mo["type"], mo["group"], mo["inst"], int(mo["offset_comp"]),
                      int(mo["size_comp"]), mo["comp_type"])
                     for mo in om if (mo["type"], mo["group"], mo["inst"]) != src_tgi}
        v5_nonww = src_nonww == out_nonww
        # WW XML 原地保证: 同一 instance; 高位标志保留 (comp_type 随新body合法演化)
        out_ww = next((mo for mo in om if (mo["type"], mo["group"], mo["inst"]) == (src_tgi[0], src_tgi[1], inst)), None)
        v5_ww = (out_ww is not None
                 and int(out_ww["offset_comp"]) == int(ww_meta["offset_comp"])
                 and int(out_ww["size_comp"]) == int(ww_meta["size_comp"]))
        print(f"  [V5] header_comp保源={v5}  非WW TGI+高位+comp保源={v5_nonww}  "
              f"WW_XML同inst+高位保源={v5_ww}")
        ok &= v5 and v5_nonww and v5_ww

    print(f"  STATIC_PASS={ok}")
    if not ok:
        print("P10_STATIC_FAIL -> 不写包 (fail-closed)", file=sys.stderr); return 3

    stem = src.stem
    out_path = out_dir / f"{stem}_P10_INPLACE_ord{ordinal}.package"
    wb.build_package(items, out_path, header_comp=hdr_comp, major=src_major, minor=src_minor)
    print(f"OUT_PATH={out_path}")
    print(f"OUT_SHA256={wb.sha256(out_path)}")
    print(f"WW_XML_INSTANCE_KEPT=0x{inst:016X}  (与原包相同)")
    print(f"P10_SUMMARY 仅改: {FIELD} -> {zh!r}, 原地同一 instance, stage/next/clip 全英文未动")
    print("P10_BATCH PASS=***")
    print("ZERO_WRITE_TO_MODS=YES (仅本 out-dir 产物)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
