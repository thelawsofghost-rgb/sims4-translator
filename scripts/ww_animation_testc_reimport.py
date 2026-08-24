#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEST C 实验: 新 WW_ANIM_XML instance 重新导入 (re-import-as-new)。

背景 (Dorothy 真机证据链):
  - XML raw_display_name / stage_name 修改后游戏仍显示英文  (排除直接读 XML display)
  - 无 STBL; animations_data_cache.ww 仅存 clip:=duration, 无 display 串
  - 无 hash->display 映射层  => 名字 = 运行时/导入时派生并缓存, 按稳定键引用
  => 改已导入动画的 XML 文本无效; 唯一路径 = 让 WW 把动画当作【全新】重新导入,
     从而在导入时按 XML 重新派生显示名。

本脚本 (只读 source, 新增实验模式, 不改 writer 架构):
  1. 只读 source 唯一 WW_ANIM_XML; 保留全部源条目字节不变
  2. 仅修改 1 个测试目标 (默认 ordinal 82 = You Belong To Me 1):
     对其 animation_raw_display_name 加前缀 (新 instance 重导入后应派生新名)
  3. 给 WW_ANIM_XML 生成/指定【新 instance】(≠ source) => WW 视为全新动画
  4. 用既有 build_package 重建 (仅该 entry 换 inst+body, 其余全源样)
  5. 输出前静态验证:
     - source unchanged (SHA 前后一致)
     - sidecar DBPF 可重解析 (valid)
     - 目标 diff count == 1 (仅该 entry 的 display 字段)
     - 新 instance != source instance
     - 仅目标 entry 变, 其余 entry 数/TGI 全一致

ZERO_WRITE_TO_MODS=YES  (绝不修改 Mods / 不碰 writer 架构 / 只写出 sidecar 到 output/)

用法:
  python scripts/ww_animation_testc_reimport.py --source <WW_Nevely42_Animations.package> --force
  python scripts/ww_animation_testc_reimport.py --source X.package --ordinal 82 --force
  python scripts/ww_animation_testc_reimport.py --source X.package --instance 0x1234... --force
"""

import argparse
import hashlib
import importlib.util
import random
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MODULE = SCRIPT_DIR / "ww_animation_canary_builder.py"
_spec = importlib.util.spec_from_file_location("ww_canary_builder", MODULE)
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)

WW_ANIM_XML = 0x7DF2169C
ENTRY_LIST_FIELD = "animations_list"

OUT_DIR = Path("output/ww_animation_testc_reimport")
PASS_KEY = "TEST_C_REIMPORT_STATIC_PASS"


def _locate_animations_list(xml_text: str):
    """返回 (<L ...> 的 inner text, inner_start, inner_end)。fail-closed。"""
    m = re.search(r'<L\b[^>]*\bn="' + re.escape(ENTRY_LIST_FIELD) + r'"[^>]*>', xml_text)
    if not m:
        return None, None, None
    inner_start = m.end()
    # 匹配到对应 </L>
    tag = "L"
    depth = 1
    pos = inner_start
    close_re = re.compile(r'</' + tag + r'\s*>')
    open_re = re.compile(r'<' + tag + r'\b[^>]*>')
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
    """把 animations_list inner 切分为 (block_text, (start,end)) 序列, 含 gap。</br>
    每个 top-level <U> 归为一个 entry; 非 <U> 内容 (gap whitespace) 单独成段。"""
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
            blocks.append((inner[pos:m.start()], (pos, m.start())))  # gap
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
    """在单个 entry block 内替换 animation_raw_display_name 文本。返回 (old, new_block, node_cnt)。"""
    root = ET.fromstring(block_text)
    nodes = [
        t for t in root.iter()
        if (t.tag.rsplit('}', 1)[-1] if isinstance(t.tag, str) else None) == "T"
        and t.get("n") == "animation_raw_display_name"
    ]
    if len(nodes) != 1:
        return None, None, len(nodes)
    old = nodes[0].text or ""
    nodes[0].text = new_val
    new_block = ET.tostring(root, encoding="unicode")
    return old, new_block, 1


def _count_display_nodes(block_text: str) -> int:
    try:
        root = ET.fromstring(block_text)
    except Exception:
        return -1
    return len([
        t for t in root.iter()
        if (t.tag.rsplit('}', 1)[-1] if isinstance(t.tag, str) else None) == "T"
        and t.get("n") == "animation_raw_display_name"
    ])


def run(src: Path, out_dir: Path, force: bool, ordinal: int, new_instance: int) -> int:
    prefix = "大包EASY_C3"  # 单一测试目标 (C3)
    field = "animation_raw_display_name"
    print(f"RUN_MODE=REIMPORT  FIELD={field}  TARGET_ORDINAL={ordinal}  PREFIX=【{prefix}】")
    src_sha_before = wb.sha256(src)

    # ---- source 解析: 单 WW_ANIM_XML ----
    idx, err = wb.safe_parse(src)
    if err is not None or idx is None:
        print(f"ERROR: source 解析失败: {err}", file=sys.stderr)
        return 3
    ww = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    if len(ww) != 1:
        print(f"ERROR: 需单 WW_ANIM_XML; 实际={len(ww)} (fail-closed)", file=sys.stderr)
        return 3
    ww_e = ww[0]
    src_tgi = (ww_e.type_id, ww_e.group_id, ww_e.instance_id)
    src_tgi_str = f"0x{ww_e.type_id:08X}/0x{ww_e.group_id:08X}/0x{ww_e.instance_id:016X}"
    print(f"SOURCE_WW_XML_TGI={src_tgi_str}")

    # ---- source metadata (source-faithful) ----
    src_major, src_minor, hdr_comp, entries_meta = wb.read_entry_meta_raw(src)
    if len(entries_meta) != len(idx.entries):
        print(f"ERROR: 源 index metadata 数与条目数不一致 ({len(entries_meta)} vs {len(idx.entries)})", file=sys.stderr)
        return 3
    ww_meta = None
    for m in entries_meta:
        if (m["type"], m["group"], m["inst"]) == src_tgi:
            ww_meta = m
            break
    if ww_meta is None:
        print("ERROR: 找不到 WW XML 原始 index metadata (fail-closed)", file=sys.stderr)
        return 3

    # ---- 读 WW XML body -> logical XML ----
    body_orig = wb.read_body_raw(src, ww_e)
    _schema, xml_text_orig, xerr = wb.parse_anim_xml(body_orig)
    if xerr is not None:
        print(f"ERROR: WW XML 解析失败: {xerr}", file=sys.stderr)
        return 3

    # ---- 定位 animations_list ----
    inner, inner_start, inner_end = _locate_animations_list(xml_text_orig)
    if inner is None:
        print("ERROR: 找不到 animations_list (fail-closed)", file=sys.stderr)
        return 3
    blocks = _entry_blocks(inner)
    n_entries = len([b for b in blocks if b[0].lstrip().startswith("<U")])
    print(f"ENTRY_COUNT_SOURCE={n_entries}")

    # ---- 目标 entry 解析 (gap 感知) ----
    if ordinal >= n_entries:
        print(f"ERROR: target ordinal {ordinal} 超出 entry 数 {n_entries} (fail-closed)", file=sys.stderr)
        return 3
    # 找到第 ordinal 个 entry block 的 (text, start, end)
    entry_idx = 0
    target = None
    for b, (bs, be) in blocks:
        if b.lstrip().startswith("<U"):
            if entry_idx == ordinal:
                target = (b, bs, be)
                break
            entry_idx += 1
    if target is None:
        print(f"ERROR: 定位 ordinal {ordinal} 失败 (fail-closed)", file=sys.stderr)
        return 3
    btext, _bs, _be = target
    # 两遍: 先探 OLD, 再写 NEW (唯一 animation_raw_display_name)
    old_probe = None
    try:
        troot = ET.fromstring(btext)
        tnodes = [t for t in troot.iter()
                  if (t.tag.rsplit('}', 1)[-1] if isinstance(t.tag, str) else None) == "T"
                  and t.get("n") == field]
        old_probe = (tnodes[0].text or "") if len(tnodes) == 1 else None
        node_cnt_probe = len(tnodes)
    except Exception:
        old_probe = None
        node_cnt_probe = -1
    if old_probe is None:
        print(f"ERROR: entry[{ordinal}] {field} 定位/无唯一 display (fail-closed) node_cnt={node_cnt_probe}", file=sys.stderr)
        return 3
    old_val = old_probe
    new_val = f"【{prefix}】{old_val}"
    _old2, new_block, node_cnt2 = _replace_one_display(btext, new_val)
    if node_cnt2 != 1 or _old2 != old_val:
        print(f"ERROR: entry[{ordinal}] {field} 二次替换不一致 (fail-closed) node_cnt={node_cnt2}", file=sys.stderr)
        return 3

    # ---- 拼接新 inner (仅该 entry 替换, gap 原样) ----
    part_edits = {ordinal: (btext, new_block)}
    new_inner_parts = []
    entry_idx2 = 0
    for b, _rng in blocks:
        if b.lstrip().startswith("<U"):
            if entry_idx2 in part_edits:
                _old, nb = part_edits[entry_idx2]
                new_inner_parts.append(nb)
            else:
                new_inner_parts.append(b)
            entry_idx2 += 1
        else:
            new_inner_parts.append(b)
    new_inner = "".join(new_inner_parts)
    new_xml = xml_text_orig[:inner_start] + new_inner + xml_text_orig[inner_end:]

    # 校验: 仅 1 个 target 变, 其余 entry 的 display 全等
    def _entry_displays(txt):
        root = ET.fromstring(txt)
        lst = None
        for node in root.iter():
            tag = node.tag.rsplit('}', 1)[-1] if isinstance(node.tag, str) else None
            if tag == "L" and node.get("n") == ENTRY_LIST_FIELD:
                lst = node
                break
        if lst is None:
            return None
        out = []
        for c in lst:
            if (c.tag.rsplit('}', 1)[-1] if isinstance(c.tag, str) else None) == "U":
                d = next((t.text for t in c.iter()
                          if (t.tag.rsplit('}', 1)[-1] if isinstance(t.tag, str) else None) == "T"
                          and t.get("n") == field), None)
                out.append(d)
        return out
    d_src = _entry_displays(xml_text_orig)
    d_new = _entry_displays(new_xml)
    if d_src is None or d_new is None or len(d_src) != len(d_new):
        print("ERROR: display 枚举失败/计数不一致 (fail-closed)", file=sys.stderr)
        return 3
    diff_idx = [i for i in range(len(d_src)) if (d_src[i] or "") != (d_new[i] or "")]
    print(f"TARGET_DIFF_INDICES={diff_idx}")
    if diff_idx != [ordinal]:
        print(f"ERROR: 应仅 ordinal {ordinal} 变化; 实际={diff_idx} (fail-closed)", file=sys.stderr)
        return 3

    # ---- 压缩 body (严格同 SIDECAR_TESTC 的 proven-good 路径) ----
    new_body = wb.compress_like(body_orig, new_xml.encode("utf-8"))
    new_decomp_len = len(wb.decompress_maybe(new_body))
    new_stored_len = len(new_body)

    # ---- WW XML 输出的 compression metadata: 严格复刻 SIDECAR_TESTC (proven-good),
    #      唯一区别 = instance。
    #      关键修复: read_entry_meta_raw 的 meta dict 没有 offset_high_bit/size_high_bit 键,
    #      高位布尔在 offset_comp/size_comp; 之前 meta.get(...) 返回 None 致 build_package
    #      回退 comp 推导 => 索引高位错乱 => Sims 4 无法启动。现在显式映射。
    src_off_hi = int(ww_meta["offset_comp"])
    src_sz_hi = int(ww_meta["size_comp"])
    src_f8 = ww_meta["comp_type"]
    item_meta = {
        "comp_state": True,
        "comp_type": src_f8,
        "mem_size": new_decomp_len,
        "offset_high_bit": src_off_hi,
        "size_high_bit": src_sz_hi,
    }
    print(f"WW_XML_META src_off_hi={src_off_hi} src_sz_hi={src_sz_hi} comp_type={src_f8} "
          f"body_compressed={wb._is_zlib(new_body)} new_stored_len={new_stored_len} mem_size={new_decomp_len}")

    # ---- 组装 items: 全部条目源样 (TGI+body), 仅 WW XML 仅换 instance (body+meta 同 SIDECAR) ----
    items = []
    for idx_i, e in enumerate(idx.entries):
        meta = entries_meta[idx_i]
        t, g, inst = e.type_id, e.group_id, e.instance_id
        if (t, g, inst) == src_tgi:
            items.append((t, g, new_instance, new_body, item_meta))
        else:
            body = wb.read_body_raw(src, e)
            # 其余 entry 保留源样: meta 需把 offset_raw/size_raw 的高位布尔映射为 high_bit 传给 build_package
            other_meta = {
                "comp_state": bool(meta["comp_type"]),
                "comp_type": meta["comp_type"],
                "mem_size": meta["mem_size"],
                "offset_high_bit": int(meta["offset_comp"]),
                "size_high_bit": int(meta["size_comp"]),
            }
            items.append((t, g, inst, body, other_meta))
    print(f"ITEM_COUNT={len(items)}  (源 {len(idx.entries)}); 含其余条目 body 源样保留")

    # ---- 静态验证 (写前) ----
    print()
    print("=== 静态验证 ===")
    ok = True

    # V1: source unchanged
    src_sha_after = wb.sha256(src)
    v1 = src_sha_before == src_sha_after
    print(f"  [V1] source unchanged: {v1}  ({src_sha_before[:16]}…)")
    ok &= v1

    # V2: 新 instance != source instance
    v2 = new_instance != src_tgi[2]
    print(f"  [V2] new instance != source: {v2}  new=0x{new_instance:016X}  src=0x{src_tgi[2]:016X}")
    ok &= v2

    # V3: target diff count (should be exactly ordinal)
    v3 = diff_idx == [ordinal]
    print(f"  [V3] target diff_count==1 @ordinal{ordinal}: {v3}")
    ok &= v3

    # V4: sidecar DBPF 可重解析 (valid package + 单 WW XML 新 inst)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        probe = Path(td) / "probe.package"
        wb.build_package(items, probe, header_comp=hdr_comp, major=src_major, minor=src_minor)
        v4_valid, v4_fails, v4_warns = wb.dbpf_metadata_valid(probe)
        print(f"  [V4] sidecar DBPF valid: {v4_valid}")
        if v4_fails:
            print(f"       FAILS: {v4_fails}")
        if v4_warns:
            print(f"       WARNS: {v4_warns}")
        ok &= v4_valid
        # 重读校验新 inst 存在、条目数一致
        r2, r2err = wb.safe_parse(probe)
        if r2err is not None or r2 is None:
            print(f"  [V4b] sidecar safe_parse 失败: {r2err}")
            ok = False
        else:
            ww2 = [e for e in r2.entries if e.type_id == WW_ANIM_XML]
            n2 = len(r2.entries)
            v4b = len(ww2) == 1 and ww2[0].instance_id == new_instance and n2 == len(idx.entries)
            print(f"  [V4b] sidecar 重读: 单WW_XML新inst={v4b}, entry数 {n2}==source {len(idx.entries)}")
            ok &= v4b

        # V5: header_comp/版本与 source 一致; 非 WW 条目 TGI+高位元数据均保源 (防再次破坏索引)
        probe_hc = wb.read_entry_meta_raw(probe)[2]
        v5 = (probe_hc == hdr_comp)
        _v52, _v53 = True, True
        om = wb.read_entry_meta_raw(probe)[3]
        if len(om) != len(entries_meta):
            _v52 = False
        else:
            for mi, m in enumerate(entries_meta):
                mo = om[mi]
                if (m["type"], m["group"], m["inst"]) == src_tgi:
                    # WW XML: instance 变为 new; 高位/类型保留 source
                    if not (mo["inst"] == new_instance and
                            int(mo["offset_comp"]) == int(m["offset_comp"]) and
                            int(mo["size_comp"]) == int(m["size_comp"]) and
                            mo["comp_type"] == m["comp_type"]):
                        _v52 = False
                else:
                    if not (mo["type"] == m["type"] and mo["group"] == m["group"] and
                            mo["inst"] == m["inst"] and
                            int(mo["offset_comp"]) == int(m["offset_comp"]) and
                            int(mo["size_comp"]) == int(m["size_comp"]) and
                            mo["comp_type"] == m["comp_type"]):
                        _v53 = False
        print(f"  [V5] header_comp/版式保源={v5}  WW_XML_高位保源={_v52}  非WW条目TGI+高位保源={_v53}")
        ok &= v5 and _v52 and _v53

    print(f"  STATIC_PASS={ok}")
    if not ok:
        print(f"ERROR: 静态验证未通过 (fail-closed, 不写出)", file=sys.stderr)
        return 1

    # ---- 写 sidecar ----
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    src_name = src.stem
    out_path = out_dir / f"{src_name}_REIMPORT_C3_instance_{new_instance:016X}.package"
    if out_path.exists() and not force:
        print(f"ERROR: 输出已存在 {out_path} (--force 覆盖)", file=sys.stderr)
        return 2
    wb.build_package(items, out_path, header_comp=hdr_comp, major=src_major, minor=src_minor)
    out_sha = wb.sha256(out_path)
    print()
    print(f"OUT_PATH={out_path}")
    print(f"OUT_SHA256={out_sha}")
    print(f"NEW_WW_XML_TGI=0x{WW_ANIM_XML:08X}/0x{src_tgi[1]:08X}/0x{new_instance:016X}")
    print(f"OLD_VALUE={old_val!r}")
    print(f"NEW_VALUE={new_val!r}")
    print(f"{PASS_KEY}=YES")
    print("ZERO_WRITE_TO_MODS=YES")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--ordinal", type=int, default=82, help="测试目标 ordinal (默认 82 = You Belong To Me 1)")
    ap.add_argument("--instance", default=None, help="新 WW_ANIM_XML instance (十六进制, 0x…); 缺省则随机生成")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: source 不存在 {src}", file=sys.stderr)
        return 2

    if a.instance is not None:
        try:
            new_instance = int(a.instance, 0)
        except ValueError:
            print(f"ERROR: 非法 instance {a.instance!r}", file=sys.stderr)
            return 2
    else:
        # 确定性但 ≠ source: 用 seed=ordinal 生成一个随机 48-bit instance
        rnd = random.Random(f"ww-reimport-c3-{a.ordinal}")
        new_instance = rnd.getrandbits(48)
        # 确保高位非 0 且 >= 0x100000000 (48-bit)
        new_instance |= 0x0000FFFFFFFFFFFF & new_instance
        if new_instance < 0x100000000:
            new_instance += 0x100000000

    out_dir = a.out_dir or OUT_DIR
    return run(src, Path(out_dir), a.force, a.ordinal, new_instance)


if __name__ == "__main__":
    sys.exit(main())
