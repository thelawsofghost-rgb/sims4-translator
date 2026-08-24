#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M2 批量试点: 一包多新 instance WW_ANIM_XML, 每 instance 对应一个本地化动画。

背景 (由 REIMPORT 单 entry 成功路径推广):
  - 每个已本地化动画 = 完整 WW_ANIM_XML 的副本, 仅 instance + 目标 entry 的
    animation_raw_display_name 改变, 其余全部字节保源 (M2 model)。
  - 每个新 instance 让 WW 视为"全新动画", 导入时按 XML 重新派生中文显示名。
  - 本脚本在【单个 package】内生成 N 个 WW_ANIM_XML item (N 个不同新 instance),
    每个仅对应一个候选 entry 的中文翻译; 其余 (源 WW_ANIM_XML + 非 WW 条目) 保源。
  => 验证多 instance 共存 (Dorothy 真机人工确认后) 即证明 M2 规模化可行。

要求:
  - 输入候选 CSV (ordinal, series_name, display_name, stage_index, strategy, reason)
  - 输入翻译 CSV (ordinal, display_name, chinese_translation)  [最终中文, 逐条精确]
  - 每个 entry 独立新 instance (确定性 blake2b 48-bit, 内容寻址 + 碰撞检测)
  - 复用 REIMPORT 的 V1-V5 验证 (推广到多条目)
  - 暂不写 Mods (ZERO_WRITE_TO_MODS=YES)

用法:
  python scripts/ww_animation_m2_batch.py --source <源包> \
      --candidates output/ww_m2_candidates/ww_m2_candidates.csv \
      --translations configs/ww_m2_translations.csv \
      --expect-n 11 [--force]
"""
import argparse
import hashlib
import importlib.util
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("ww_canary_builder", SCRIPT_DIR / "ww_animation_canary_builder.py")
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)

WW_ANIM_XML = 0x7DF2169C
ENTRY_LIST_FIELD = "animations_list"
DISPLAY_FIELD = "animation_raw_display_name"

OUT_DIR = Path("output/ww_animation_m2_batch")
PASS_KEY = "M2_BATCH_STATIC_PASS"


def _locate_animations_list(xml_text: str):
    m = re.search(r'<L\b[^>]*\bn="' + re.escape(ENTRY_LIST_FIELD) + r'"[^>]*>', xml_text)
    if not m:
        return None, None, None
    inner_start = m.end()
    depth = 1
    pos = inner_start
    close_re = re.compile(r'</L\s*>')
    open_re = re.compile(r'<L\b[^>]*>')
    while True:
        oc = open_re.search(xml_text, pos)
        cc = close_re.search(xml_text, pos)
        if cc is None:
            return None, None, None
        if oc is not None and oc.start() < cc.start():
            depth += 1
            pos = oc.end()
        else:
            depth -= 1
            if depth == 0:
                return xml_text[inner_start:cc.start()], inner_start, cc.start()
            pos = cc.end()


def _entry_blocks(inner: str):
    blocks = []
    pos = 0
    entry_re = re.compile(r'<U\b[^>]*>')
    close_re = re.compile(r'</U\s*>')
    n = len(inner)
    while pos < n:
        m = entry_re.search(inner, pos)
        if m is None:
            if pos < n:
                blocks.append((inner[pos:n], (pos, n)))
            break
        if m.start() > pos:
            blocks.append((inner[pos:m.start()], (pos, m.start())))
        s = m.start()
        depth = 1
        p = m.end()
        while True:
            oc = entry_re.search(inner, p)
            cc = close_re.search(inner, p)
            if cc is None:
                e = n
                break
            if oc is not None and oc.start() < cc.start():
                depth += 1
                p = oc.end()
            else:
                depth -= 1
                p = cc.end()
                if depth == 0:
                    e = p
                    break
        blocks.append((inner[s:e], (s, e)))
        pos = e
    return blocks


def _replace_one_display(block_text: str, new_val):
    root = ET.fromstring(block_text)
    nodes = [
        t for t in root.iter()
        if (t.tag.rsplit('}', 1)[-1] if isinstance(t.tag, str) else None) == "T"
        and t.get("n") == DISPLAY_FIELD
    ]
    if len(nodes) != 1:
        return None, None, len(nodes)
    old = nodes[0].text or ""
    nodes[0].text = new_val
    return old, ET.tostring(root, encoding="unicode"), 1


def gen_instance(src_inst: int, ordinal: int, zh: str, salt: int) -> int:
    """确定性 48-bit instance: blake2b(source_inst, ordinal, zh, salt)。"""
    payload = f"{src_inst:x}|{ordinal}|{zh}|{salt}".encode("utf-8")
    h = hashlib.blake2b(payload, digest_size=6).digest()  # 48 bit
    inst = int.from_bytes(h, "big")
    inst |= 0x100000000  # 保证 >= 2^32 (安全区间)
    return inst & 0x0000FFFFFFFFFFFF


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--candidates", required=True, help="ww_m2_candidates.csv")
    ap.add_argument("--translations", required=True, help="翻译 CSV: ordinal,display_name,chinese_translation")
    ap.add_argument("--expect-n", type=int, default=None, help="预期候选 entry 数 (fail-closed)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: source 不存在 {src}", file=sys.stderr); return 2

    # ---- 读候选 + 翻译 (逐条精确) ----
    cands = []
    with open(a.candidates, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            cands.append(r)
    if a.expect_n is not None and len(cands) != a.expect_n:
        print(f"ERROR: 候选数 {len(cands)} != 预期 {a.expect_n} (fail-closed)", file=sys.stderr); return 2
    tr_map = {}
    with open(a.translations, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            ord_s = str(r["ordinal"]).strip()
            tr_map[ord_s] = r["chinese_translation"]
    missing = [c["ordinal"] for c in cands if str(c["ordinal"]).strip() not in tr_map]
    if missing:
        print(f"ERROR: 缺少翻译的 ordinal: {missing} (fail-closed)", file=sys.stderr); return 2
    print(f"M2_BATCH candidates={len(cands)} translations={len(tr_map)}")

    # ---- 源解析 ----
    src_sha_before = wb.sha256(src)
    idx, err = wb.safe_parse(src)
    if err is not None or idx is None:
        print(f"ERROR: 源解析失败: {err}", file=sys.stderr); return 3
    ww = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    if len(ww) != 1:
        print(f"ERROR: 源需单 WW_ANIM_XML; 实际={len(ww)} (fail-closed)", file=sys.stderr); return 3
    ww_e = ww[0]
    src_inst = ww_e.instance_id
    src_tgi = (ww_e.type_id, ww_e.group_id, ww_e.instance_id)
    print(f"SOURCE_WW_INST=0x{src_inst:016X}  ENTRY_COUNT_SOURCE={len(idx.entries)}")

    src_major, src_minor, hdr_comp, entries_meta = wb.read_entry_meta_raw(src)
    if len(entries_meta) != len(idx.entries):
        print(f"ERROR: meta/条目数不一致 ({len(entries_meta)} vs {len(idx.entries)})", file=sys.stderr); return 3
    ww_meta = next((m for m in entries_meta if (m["type"], m["group"], m["inst"]) == src_tgi), None)
    if ww_meta is None:
        print("ERROR: 找不到源 WW XML meta", file=sys.stderr); return 3
    src_off_hi = int(ww_meta["offset_comp"]); src_sz_hi = int(ww_meta["size_comp"]); src_f8 = ww_meta["comp_type"]

    # 读源 WW XML logical
    body_orig = wb.read_body_raw(src, ww_e)
    _schema, xml_text_orig, xerr = wb.parse_anim_xml(body_orig)
    if xerr is not None:
        print(f"ERROR: WW XML 解析失败: {xerr}", file=sys.stderr); return 3
    inner, inner_start, inner_end = _locate_animations_list(xml_text_orig)
    if inner is None:
        print("ERROR: 找不到 animations_list", file=sys.stderr); return 3
    blocks = _entry_blocks(inner)
    n_entries = len([b for b in blocks if b[0].lstrip().startswith("<U")])

    # ordinal -> block 文本
    entry_blocks = {}
    eidx = 0
    for b, _rng in blocks:
        if b.lstrip().startswith("<U"):
            entry_blocks[eidx] = b
            eidx += 1

    # ---- 逐候选: 生成 (new_instance, new_body) ----
    # 碰撞检测: 批内去重 + 避开源 inst
    used_instances = {src_inst}
    jobs = []
    for c in cands:
        ord_s = str(c["ordinal"]).strip()
        ordinal = int(ord_s)
        zh = tr_map[ord_s]
        if ordinal not in entry_blocks:
            print(f"ERROR: ordinal {ordinal} 超出源 entry 数 {n_entries} (fail-closed)", file=sys.stderr); return 3
        btext = entry_blocks[ordinal]
        old, new_block, cnt = _replace_one_display(btext, zh)
        if cnt != 1 or old is None:
            print(f"ERROR: ordinal {ordinal} display 非唯一/定位失败 (cnt={cnt})", file=sys.stderr); return 3
        if old == zh:
            print(f"WARN: ordinal {ordinal} 译文与原文相同, 仍按新 instance 生成", file=sys.stderr)
        # 新 instance: 确定性 + 碰撞递增 salt
        inst = gen_instance(src_inst, ordinal, zh, 0)
        salt = 0
        while inst in used_instances:
            salt += 1
            inst = gen_instance(src_inst, ordinal, zh, salt)
        used_instances.add(inst)
        # 新 body: 拼 inner (仅该 entry 换), 其余保源
        new_inner_parts = []
        ei2 = 0
        for b, _rng in blocks:
            if b.lstrip().startswith("<U"):
                new_inner_parts.append(new_block if ei2 == ordinal else b)
                ei2 += 1
            else:
                new_inner_parts.append(b)
        new_xml = xml_text_orig[:inner_start] + "".join(new_inner_parts) + xml_text_orig[inner_end:]
        new_body = wb.compress_like(body_orig, new_xml.encode("utf-8"))
        jobs.append({"ordinal": ordinal, "zh": zh, "old": old, "inst": inst, "body": new_body,
                     "new_xml": new_xml})
        print(f"  job ordinal={ordinal}  old={old!r}  ->  {zh!r}  new_inst=0x{inst:016X}")

    # ---- 组装 items: N 个 WW XML (各不同 inst) + 全部源条目 ----
    items = []
    # 先源 WW_XML (inst 不变, 体保源)
    items.append((src_tgi[0], src_tgi[1], src_tgi[2], body_orig,
                  {"comp_state": True, "comp_type": src_f8, "mem_size": len(wb.decompress_maybe(body_orig)),
                   "offset_high_bit": src_off_hi, "size_high_bit": src_sz_hi}))
    # N 个新本地化 WW_XML
    for j in jobs:
        items.append((WW_ANIM_XML, src_tgi[1], j["inst"], j["body"],
                      {"comp_state": True, "comp_type": src_f8,
                       "mem_size": len(wb.decompress_maybe(j["body"])),
                       "offset_high_bit": src_off_hi, "size_high_bit": src_sz_hi}))
    # 其余源条目 (非 WW_XML) 保源
    non_ww_count = 0
    for i, e in enumerate(idx.entries):
        if (e.type_id, e.group_id, e.instance_id) == src_tgi:
            continue
        body = wb.read_body_raw(src, e)
        m = entries_meta[i]
        items.append((e.type_id, e.group_id, e.instance_id, body,
                      {"comp_state": bool(m["comp_type"]), "comp_type": m["comp_type"],
                       "mem_size": m["mem_size"],
                       "offset_high_bit": int(m["offset_comp"]), "size_high_bit": int(m["size_comp"])}))
        non_ww_count += 1
    print(f"ITEM_COUNT={len(items)}  (1 源WW + {len(jobs)} 新WW + {non_ww_count} 非WW保源)")

    # ---- V1-V5 静态验证 (推广多实例) ----
    print("\n=== 静态验证 (M2 batch) ===")
    ok = True
    v1 = wb.sha256(src) == src_sha_before
    print(f"  [V1] source unchanged: {v1}")
    ok &= v1
    new_insts = {j["inst"] for j in jobs}
    v2 = (all(inst != src_inst for inst in new_insts)
          and len(new_insts) == len(jobs))  # 批内无重复 + 均 != 源
    print(f"  [V2] 新inst互异且!=源: {v2}  (n={len(new_insts)})")
    ok &= v2
    # V3: 每个新 XML 仅对应 ordinal 变, 其余 entry display 全等源
    d_src = _entry_displays(xml_text_orig)
    v3 = True
    for j in jobs:
        d_new = _entry_displays(j["new_xml"])
        if d_src is None or d_new is None or len(d_src) != len(d_new):
            v3 = False; break
        diffs = [i for i in range(len(d_src)) if (d_src[i] or "") != (d_new[i] or "")]
        if diffs != [j["ordinal"]]:
            v3 = False; break
    print(f"  [V3] 每新XML仅目标 ordinal 变: {v3}")
    ok &= v3
    # V4: package 可重解析, WW_XML 数 = 1+N, 含全部新 inst
    import tempfile
    out_path = None
    with tempfile.TemporaryDirectory() as td:
        probe = Path(td) / "probe.package"
        wb.build_package(items, probe, header_comp=hdr_comp, major=src_major, minor=src_minor)
        valid, fails, warns = wb.dbpf_metadata_valid(probe)
        print(f"  [V4] DBPF valid: {valid}")
        print(f"       WARNS: {warns}")
        ok &= valid
        r2, r2err = wb.safe_parse(probe)
        v4b = False
        if r2err is None and r2 is not None:
            ww2 = [e for e in r2.entries if e.type_id == WW_ANIM_XML]
            insts2 = {e.instance_id for e in ww2}
            v4b = (len(ww2) == 1 + len(jobs) and insts2 == {src_inst} | new_insts
                   and len(r2.entries) == len(items))
        print(f"  [V4b] sidecar 重读: 1+N WW_XML, 全部新inst在 = {v4b}")
        ok &= v4b
        # V5: header_comp/版本保源; 非WW TGI+高位保源
        probe_hc = wb.read_entry_meta_raw(probe)[2]
        om = wb.read_entry_meta_raw(probe)[3]
        v5 = probe_hc == hdr_comp
        v5_nonww = True
        src_nonww = {(m["type"], m["group"], m["inst"], int(m["offset_comp"]), int(m["size_comp"]), m["comp_type"])
                     for i, m in enumerate(entries_meta) if (m["type"], m["group"], m["inst"]) != src_tgi}
        out_nonww = {(mo["type"], mo["group"], mo["inst"], int(mo["offset_comp"]), int(mo["size_comp"]), mo["comp_type"])
                     for mo in om if (mo["type"], mo["group"], mo["inst"]) != src_tgi
                     and mo["inst"] not in new_insts}
        v5_nonww = out_nonww == src_nonww
        print(f"  [V5] header_comp保源={v5}  非WW TGI+高位保源={v5_nonww}")
        ok &= v5 and v5_nonww

    print(f"  STATIC_PASS={ok}")
    if not ok:
        print("ERROR: 静态验证未通过 (fail-closed, 不写出)", file=sys.stderr); return 1

    # ---- 写出 sidecar ----
    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    src_name = src.stem
    out_path = out_dir / f"{src_name}_M2_BATCH_{len(jobs)}inst.package"
    if out_path.exists() and not a.force:
        print(f"ERROR: 输出已存在 {out_path} (--force 覆盖)", file=sys.stderr); return 2
    wb.build_package(items, out_path, header_comp=hdr_comp, major=src_major, minor=src_minor)
    print(f"\nOUT_PATH={out_path}")
    print(f"OUT_SHA256={wb.sha256(out_path)}")
    print(f"NEW_INSTANCES={','.join(f'0x{i:016X}' for i in sorted(new_insts))}")
    print(f"{PASS_KEY}=YES")
    print("ZERO_WRITE_TO_MODS=YES")
    return 0


def _entry_displays(txt):
    try:
        root = ET.fromstring(txt)
    except Exception:
        return None
    lst = next((node for node in root.iter()
                if (node.tag.rsplit('}', 1)[-1] if isinstance(node.tag, str) else None) == "L"
                and node.get("n") == ENTRY_LIST_FIELD), None)
    if lst is None:
        return None
    out = []
    for c in lst:
        if (c.tag.rsplit('}', 1)[-1] if isinstance(c.tag, str) else None) == "U":
            d = next((t.text for t in c.iter()
                      if (t.tag.rsplit('}', 1)[-1] if isinstance(t.tag, str) else None) == "T"
                      and t.get("n") == DISPLAY_FIELD), None)
            out.append(d)
    return out


if __name__ == "__main__":
    import csv
    sys.exit(main())
