#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEST B: 独立 exact-TGI XML sidecar 研究 (WW Animation)。

目标: 生成一个‘只含 1 个 resource’的独立 sidecar package, 其唯一 TGI 与真实 source 的
WW registration XML 完全相同 (0x7DF2169C/0x00000000/0xB3390ED6606C10E7), 以便与 source
同时存在于 Mods 时测试: sidecar 能否覆盖 source 的 registration/display, 而不产生
duplicate/disappear/internal-linkage-break/package-load-failure。

关键边界 (与 CANARY 不同):
  - RESOURCE_COUNT=1 (只含唯一 WW XML; 禁止 STBL/CLIP/其他 XML/source 其余 22 资源/source TGI 之外的任何)。
  - 不修改/替换/重建原 source; SOURCE_FILE_BYTES_UNCHANGED=YES。
  - 不改源 source 的 field7 (那是 source 自己的); sidecar 的 WW XML field7 = sidecar 新 XML 实际解压长度。
  - high bit / field8 / compression model 保留 source WW XML 原值 (source-faithful writer 规则)。

唯一 XML 修改 (exact semantic selector, 与 TEST A / CANARY 相同):
  <T n="animation_raw_display_name">FORCE_FLOOR_002</T> -> <T n="animation_raw_display_name">【CHS_SIDECAR_B】强制地板002</T>
  要求: display_matches=1, DISPLAY_SEMANTIC_DIFF_COUNT=1, INTERNAL_XML_SEMANTIC_DIFF_COUNT=0。

先决 (真机已确认):
  SOURCE_FAITHFUL_CANARY_GAME_LOAD=PASS
  DISPLAY_FIELD_CAUSALITY=PASS

只生成: output/ww_animation_sidecar_testb/MSWD_FORCE_FLOOR_002_SIDECAR_TESTB.package
不写 Mods / 不 swap / 不真机 / 不 quarantine / 不 production (ZERO_WRITE_TO_MODS=YES)。
真机候选文件名: !MSWD_FORCE_FLOOR_002_CHS_TESTB.package (字典序早于 source, 但 LEXICALLY_EARLIER
仅是候选条件, 不是已证明的 WW load-order 规则; 不要把 Pose 项目结论直接继承给 WW)。
"""

import argparse
import importlib.util
import re
import struct
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MODULE = SCRIPT_DIR / "ww_animation_canary_builder.py"

# ---- 复用已真机验证的 source-faithful writer primitives ----
_spec = importlib.util.spec_from_file_location("ww_canary_builder", MODULE)
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)

WW_ANIM_XML = wb.WW_ANIM_XML
EXPECTED_TGI = (0x7DF2169C, 0x00000000, 0xB3390ED6606C10E7)

DISP_FIELD = "animation_raw_display_name"
DISP_OLD = "FORCE_FLOOR_002"
DISP_NEW = "【CHS_SIDECAR_B】强制地板002"


def src_sidecar(ww_meta):
    """从 source 的唯一 WW XML metadata 构造 sidecar 的 meta (source-faithful):
    offset/size high bit + comp_type 保留 source 原值; field7 由调用方按新解压长度覆盖。"""
    return {
        "comp_state": True,  # WW XML 必为 zlib (source-faithful writer 确保)
        "comp_type": ww_meta["comp_type"],
        "mem_size": 0,  # 占位; 由 sidecar 新解压长度覆盖
        "offset_high_bit": int(ww_meta["offset_comp"]),
        "size_high_bit": int(ww_meta["size_comp"]),
    }


def run(src: Path, out_dir: Path, force: bool) -> int:
    src_sha = wb.sha256(src)

    # ---- 解析 source, 必须恰好 1 个 WW XML, TGI 须与真机期望完全一致 ----
    idx, err = wb.safe_parse(src)
    if err is not None or idx is None:
        print(f"ERROR: source 解析失败: {err}", file=sys.stderr)
        return 3
    ww = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    if len(ww) != 1:
        print(f"ERROR: 需单 WW_ANIM_XML; 实际 = {len(ww)} (fail-closed)", file=sys.stderr)
        return 3
    ww_e = ww[0]
    src_tgi = (ww_e.type_id, ww_e.group_id, ww_e.instance_id)
    if src_tgi != EXPECTED_TGI:
        print(f"ERROR: WW XML TGI 与期望不符: {src_tgi} != {EXPECTED_TGI} (fail-closed)", file=sys.stderr)
        return 3

    src_major, src_minor, hdr_comp, entries_meta = wb.read_entry_meta_raw(src)
    if len(entries_meta) != len(idx.entries):
        print(f"ERROR: 源 index metadata 数与解析条目数不一致 ({len(entries_meta)} vs {len(idx.entries)})", file=sys.stderr)
        return 3
    # WW entry 对应 metadata: 按 TGI 精确定位
    ww_meta = None
    for m in entries_meta:
        if (m["type"], m["group"], m["inst"]) == src_tgi:
            ww_meta = m
            break
    if ww_meta is None:
        print("ERROR: 找不到 WW XML 的原始 index metadata (fail-closed)", file=sys.stderr)
        return 3

    src_off_hi = int(ww_meta["offset_comp"])
    src_sz_hi = int(ww_meta["size_comp"])
    src_f8 = ww_meta["comp_type"]

    # ---- 读 source WW XML body, 解压得 logical XML ----
    body_orig = wb.read_body_raw(src, ww_e)
    _schema, xml_text_orig, xerr = wb.parse_anim_xml(body_orig)
    if xerr is not None:
        print(f"ERROR: WW XML 解析失败: {xerr}", file=sys.stderr)
        return 3

    # ---- 唯一修改: display 文本 (exact semantic selector) ----
    new_xml, cnt, _spans = wb._replace_t_node_display(xml_text_orig, DISP_FIELD, DISP_OLD, DISP_NEW)
    if cnt != 1:
        print(f"ERROR: display exact match 数 = {cnt} (必须 == 1); field={DISP_FIELD!r}", file=sys.stderr)
        return 3

    # ---- semantic diff: display 恰好 1, internal 0 ----
    ds, internal_changed, internal_diffs = wb.xml_semantic_diff(xml_text_orig, new_xml, DISP_FIELD)
    if ds is None:
        print("ERROR: semantic diff 解析失败 (fail-closed)", file=sys.stderr)
        internal_diffs = ["PARSE"] if internal_diffs is None else internal_diffs
        ds = 0
        internal_changed = 1
    if internal_changed != 0 or ds != 1:
        print(f"ERROR: semantic diff 异常 display={ds} (须 1) internal={internal_changed} (须 0) (fail-closed)", file=sys.stderr)
        if internal_diffs:
            for d in internal_diffs:
                print(f"  INTERNAL_DIFF {d}", file=sys.stderr)
        return 3

    # ---- 构造 sidecar 的 WW XML body (压缩模型与 source 一致) ----
    new_body = wb.compress_like(body_orig, new_xml.encode("utf-8"))
    new_decomp_len = len(wb.decompress_maybe(new_body))
    new_stored_len = len(new_body)

    # ---- 组装唯一 resource 的 meta: high bits/field8 保留 source, field7=新解压长度 ----
    item_meta = src_sidecar(ww_meta)
    item_meta["mem_size"] = new_decomp_len  # field7 必须 = sidecar 新 XML 实际解压长度

    # ---- 生成 sidecar package ----
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{src.stem}_SIDECAR_TESTB.package"
    if out.exists() and not force:
        print(f"ERROR: SIDECAR_TESTB 已存在 (拒绝覆盖, 用 --force): {out}", file=sys.stderr)
        return 3
    wb.build_package(
        [(src_tgi[0], src_tgi[1], src_tgi[2], new_body, item_meta)],
        out, header_comp=hdr_comp, major=src_major, minor=src_minor,
    )
    side_sha = wb.sha256(out)

    # ---- 只读静态 gate ----
    # 1) source 未被修改
    src_file_unchanged = (wb.sha256(src) == src_sha)
    # 2) reopen sidecar: 单 resource, TGI 相等
    sidx, serr = wb.safe_parse(out)
    parser_ok = (serr is None and sidx is not None)
    s_count = len(sidx.entries) if sidx else -1
    s_tgi = (sidx.entries[0].type_id, sidx.entries[0].group_id, sidx.entries[0].instance_id) if (sidx and s_count == 1) else (None, None, None)
    tgi_equal = (s_count == 1 and s_tgi == src_tgi)
    # 3) metadata / 范围校验
    meta_ok, _mf, _mw = wb.dbpf_metadata_valid(out)
    ranges_ok = wb._ranges_valid(out) if hasattr(wb, "_ranges_valid") else None
    if ranges_ok is None:
        ranges_ok = _check_ranges(out, sidx)
    s_major, s_minor, _sc, s_meta = wb.read_entry_meta_raw(out)
    dbpf_ver_equal = (src_major == s_major) and (src_minor == s_minor)
    # 4) sidecar WW entry semantic 与 source WW entry 一致 (仅 display 变)
    side_body = wb.read_body_raw(out, sidx.entries[0]) if sidx and s_count == 1 else b""
    side_text = wb.decompress_maybe(side_body).decode("utf-8", "replace") if side_body else ""
    ds2, ic2, idf2 = wb.xml_semantic_diff(xml_text_orig, side_text, DISP_FIELD)
    # 5) WW entry 物理 metadata compare (source vs sidecar)
    sm = s_meta[0] if (s_meta and len(s_meta) == 1) else None
    s_off_hi = int(sm["offset_comp"]) if sm else -1
    s_sz_hi = int(sm["size_comp"]) if sm else -1
    s_f8 = sm["comp_type"] if sm else -1
    s_f7 = sm["mem_size"] if sm else -1
    s_decomp = len(wb.decompress_maybe(side_body)) if side_body else -1
    s_stored = sm["size_raw"] & 0x7FFFFFFF if sm else -1
    off_hi_eq = (src_off_hi == s_off_hi)
    sz_hi_eq = (src_sz_hi == s_sz_hi)
    f8_eq = (src_f8 == s_f8)
    f7_matches = (s_f7 == s_decomp)
    stored_ok = (s_stored == new_stored_len)

    all_gates = (
        s_count == 1 and tgi_equal and parser_ok and meta_ok and ranges_ok
        and dbpf_ver_equal and off_hi_eq and sz_hi_eq and f8_eq and f7_matches and stored_ok
        and ic2 == 0 and (ds2 is not None and ds2 == 1) and src_file_unchanged
    )

    # ---- report ----
    print("TEST_B_SIDECAR:")
    print(f"  SOURCE_SHA={src_sha}")
    print(f"  SIDECAR_SHA={side_sha}")
    print(f"  SIDECAR_RESOURCE_COUNT={s_count}")
    print(f"  SIDECAR_TGI_COUNT={1 if tgi_equal else 0}")
    print(f"  SIDECAR_TGI=0x{src_tgi[0]:08X}/0x{src_tgi[1]:08X}/0x{src_tgi[2]:016X}")
    print(f"  SOURCE_WW_XML_TGI_EQUAL_SIDECAR={'YES' if tgi_equal else 'NO'}")
    print(f"  DISPLAY_OLD={DISP_OLD}")
    print(f"  DISPLAY_NEW={DISP_NEW}")
    print(f"  DISPLAY_MATCHES={cnt}")
    print(f"  DISPLAY_SEMANTIC_DIFF_COUNT={ds2}")
    print(f"  INTERNAL_XML_SEMANTIC_DIFF_COUNT={len(idf2) if idf2 else 0}")
    if idf2:
        for (p, kind, v1, v2) in idf2:
            print(f"  INTERNAL_DIFFS path={p} kind={kind} source_value={v1!r} sidecar_value={v2!r}")
    else:
        print(f"  INTERNAL_DIFFS=none")
    print(f"  SIDECAR_DBPF_VERSION={s_major}.{s_minor}")
    print(f"  WW_XML_OFFSET_HIGH_BIT_SOURCE={src_off_hi}")
    print(f"  WW_XML_OFFSET_HIGH_BIT_SIDECAR={s_off_hi}")
    print(f"  WW_XML_OFFSET_HIGH_BIT_EQUAL={'YES' if off_hi_eq else 'NO'}")
    print(f"  WW_XML_SIZE_HIGH_BIT_SOURCE={src_sz_hi}")
    print(f"  WW_XML_SIZE_HIGH_BIT_SIDECAR={s_sz_hi}")
    print(f"  WW_XML_SIZE_HIGH_BIT_EQUAL={'YES' if sz_hi_eq else 'NO'}")
    print(f"  WW_XML_FIELD8_SOURCE={src_f8}")
    print(f"  WW_XML_FIELD8_SIDECAR={s_f8}")
    print(f"  WW_XML_FIELD8_EQUAL={'YES' if f8_eq else 'NO'}")
    print(f"  WW_XML_SIDECAR_STORED_SIZE={s_stored} (expected {new_stored_len})")
    print(f"  WW_XML_SIDECAR_DECOMPRESSED_SIZE={s_decomp}")
    print(f"  WW_XML_SIDECAR_FIELD7={s_f7}")
    print(f"  WW_XML_SIDECAR_FIELD7_MATCHES_ACTUAL={'YES' if f7_matches else 'NO'}")
    print(f"  ALL_RESOURCE_RANGES_VALID={'YES' if ranges_ok else 'NO'}")
    print(f"  PARSER_VALID={'YES' if parser_ok else 'NO'}")
    print(f"  SOURCE_FILE_BYTES_UNCHANGED={'YES' if src_file_unchanged else 'NO'}")
    print(f"  TEST_B_SIDECAR_STATIC_PASS={'YES' if all_gates else 'NO'}")
    if not all_gates:
        if s_count != 1:
            print(f"    GATE_FAIL: SIDECAR_RESOURCE_COUNT={s_count}")
        if not tgi_equal:
            print(f"    GATE_FAIL: SOURCE_WW_XML_TGI_EQUAL_SIDECAR ({s_tgi})")
        if dbpf_ver_equal is False:
            print(f"    GATE_FAIL: DBPF_VERSION ({src_major}.{src_minor}->{s_major}.{s_minor})")
        if not off_hi_eq:
            print(f"    GATE_FAIL: OFFSET_HIGH_BIT ({src_off_hi}->{s_off_hi})")
        if not sz_hi_eq:
            print(f"    GATE_FAIL: SIZE_HIGH_BIT ({src_sz_hi}->{s_sz_hi})")
        if not f8_eq:
            print(f"    GATE_FAIL: FIELD8 ({src_f8}->{s_f8})")
        if not f7_matches:
            print(f"    GATE_FAIL: FIELD7_MATCHES_ACTUAL ({s_f7} vs {s_decomp})")
        if not stored_ok:
            print(f"    GATE_FAIL: STORED_SIZE ({s_stored} vs {new_stored_len})")
        if not ranges_ok:
            print("    GATE_FAIL: ALL_RESOURCE_RANGES_VALID")
        if not parser_ok:
            print("    GATE_FAIL: PARSER_VALID")
        if not meta_ok:
            print("    GATE_FAIL: DBPF_METADATA_VALID")
        if ic2 != 0:
            print(f"    GATE_FAIL: INTERNAL_XML_SEMANTIC_DIFF_COUNT != 0 ({ic2})")
        if ds2 != 1:
            print(f"    GATE_FAIL: DISPLAY_SEMANTIC_DIFF_COUNT != 1 ({ds2})")
        if not src_file_unchanged:
            print("    GATE_FAIL: SOURCE_FILE_BYTES_UNCHANGED")
    print("ZERO_WRITE_TO_MODS=YES")
    return 0 if all_gates else 3


def _check_ranges(path: Path, sidx):
    """校验 sidecar 内每个资源 offset/size 范围合法。"""
    if sidx is None:
        return False
    with open(path, "rb") as fh:
        size = path.stat().st_size
        major, minor, _hc, metas = wb.read_entry_meta_raw(path)
        for m in metas:
            boff = m["offset_raw"] & 0x7FFFFFFF
            bsz = m["size_raw"] & 0x7FFFFFFF
            if not (0 <= boff and boff + bsz <= size):
                return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--out-dir", default="output")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    src = Path(a.source)
    if not src.is_file():
        print("ERROR: source 不存在", file=sys.stderr)
        return 2
    return run(src, Path(a.out_dir), a.force)


if __name__ == "__main__":
    sys.exit(main())
