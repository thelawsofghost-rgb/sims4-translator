#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p28b_zero_xml_canary.py —— P28B 零修改 XML 生成器 (Linux/Windows 通用, 只读源 + 只写 output)

目的: 判定 game 内中文失败到底是
  A. 我们重新封装/构建的 WW_ANIM_XML override 本身, 还是
  B. P27 对 animation_raw_display_name 的修改。

本脚本从源 WW package 读取唯一 WW_ANIM_XML (type 0x7DF2169C),
【原封不动】把它的 raw stored payload 复制进一个新的同 TGI override package,
一个字节都不改。然后重新读生成包, 机器验证:

  SOURCE_XML_SHA256=...
  OVERRIDE_XML_SHA256=...
  XML_BYTE_IDENTICAL=YES

  WW_ANIM_XML_COUNT=1
  TYPE=0x7DF2169C
  GROUP=<same as source>
  INSTANCE=0x43F3438A94EDEB2B
  TGI_MATCH=YES
  XML_BYTE_IDENTICAL=YES
  VERDICT=PASS

禁止:
  * XML parse -> serialize
  * decode -> encode
  * ElementTree roundtrip
  * 修改 display_name
  * 修改任何 XML 字节

DBPF 封装保持 source-faithful (majors/minors/header_comp/压缩态/mem_size/TGI/high bits
均透传 source). 生成包只含 1 个 WW_ANIM_XML 资源 (sidecar 形态, 同 TGI).

用法 (Windows):
  python scripts\\ww_p28b_zero_xml_canary.py \
      --source "C:\\...\\Mods\\2026.7.20\\WW_Nevely42_Animations.package"
  (可选 --out-dir output  --force)

只读源 + 只写 output/ww_p28b/. ZERO_WRITE_TO_MODS=YES.

退出码: 0=PASS, 2=参数/IO, 3=source 解析失败 / WW_ANIM_XML 数 !=1 / 生成校验 FAIL.
"""
import argparse
import hashlib
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
for _p in (WORKSPACE / "src", Path(__file__).resolve().parent):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

# 复用 canary builder 的 DBPF 读/写助手 (不修改其架构)
from ww_animation_canary_builder import (  # noqa: E402
    build_package,
    dbpf_metadata_valid,
    read_body_raw,
    read_entry_meta_raw,
    safe_parse,
    sha256,
)

WW_ANIM_XML = 0x7DF2169C
EXPECTED_INSTANCE = 0x43F3438A94EDEB2B


def fmt_inst(i):
    return f"0x{i:016X}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="源 WW package (只读)")
    ap.add_argument("--out-dir", default="output", help="输出根目录 (默认 output)")
    ap.add_argument("--force", action="store_true", help="artifact 已存在时覆盖 (默认 fail-closed 拒写)")
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: source 不存在: {src} (exit 2)", file=sys.stderr)
        return 2

    out_dir = Path(a.out_dir) / "ww_p28b"
    out_pkg = out_dir / "WW_Nevely42_ZeroXML_Override.package"
    out_report = out_dir / "ww_p28b_zero_xml_report.txt"

    for p in (out_pkg, out_report):
        if p.exists() and not a.force:
            print(f"ERROR: artifact 已存在 (拒绝覆盖, 用 --force): {p} (exit 2)", file=sys.stderr)
            return 2

    # ---- 只读源 ----
    src_sha = sha256(src)
    idx, err = safe_parse(src)
    if err is not None or idx is None:
        print(f"ERROR: source 解析失败: {err} (exit 3)", file=sys.stderr)
        return 3

    ww = [e for e in idx.entries if getattr(e, "type_id", 0) == WW_ANIM_XML]
    if len(ww) != 1:
        print(f"ERROR: 需单 WW_ANIM_XML; 实际 = {len(ww)} (fail-closed, exit 3)", file=sys.stderr)
        return 3
    wxml = ww[0]

    t = getattr(wxml, "type_id", 0)
    g = getattr(wxml, "group_id", 0)
    inst = getattr(wxml, "instance_id", None)

    # 源 raw 存储 body —— 原封不动 (含压缩态), 一个字节不改; 绝不 parse/decode/serialize
    src_body = read_body_raw(src, wxml)
    src_body_sha = hashlib.sha256(src_body).hexdigest()

    # ---- source-faithful metadata ----
    src_major, src_minor, hdr_comp, src_meta = read_entry_meta_raw(src)
    m = None
    for _m in src_meta:
        if _m["type"] == t and _m["group"] == g and _m["inst"] == inst:
            m = _m
            break
    if m is None:
        print("ERROR: 未在源 index metadata 中找到 WW XML 条目 (exit 3)", file=sys.stderr)
        return 3
    xml_meta = {
        "comp_state": bool(m["size_comp"]),
        "comp_type": m["comp_type"],
        "mem_size": m["mem_size"],
        "offset_high_bit": int(m["offset_comp"]),
        "size_high_bit": int(m["size_comp"]),
    }

    # ---- 生成 override: 只含 1 个资源, 同 TGI, raw body 字节不变 ----
    items = [(t, g, inst, src_body, xml_meta)]
    build_package(items, out_pkg, header_comp=hdr_comp, major=src_major, minor=src_minor)
    out_sha = sha256(out_pkg)

    # ---- 重新读生成包验证 ----
    idx2, err2 = safe_parse(out_pkg)
    parser_ok = (err2 is None and idx2 is not None)
    ww2 = [e for e in (idx2.entries if idx2 else []) if getattr(e, "type_id", 0) == WW_ANIM_XML]
    count_ok = len(ww2) == 1
    tgi_ok = False
    body2_sha = ""
    byte_identical = False
    meta_ok = False
    meta_fails = []
    if count_ok:
        e2 = ww2[0]
        g2 = getattr(e2, "group_id", 0)
        inst2 = getattr(e2, "instance_id", None)
        tgi_ok = (g2 == g) and (inst2 == inst)
        b2 = read_body_raw(out_pkg, e2)
        body2_sha = hashlib.sha256(b2).hexdigest()
        byte_identical = b2 == src_body
        meta_ok, meta_fails, _w = dbpf_metadata_valid(out_pkg)

    # ---- 重新解析源 metadata 确认 group/instance 一致性 ----
    _, _, _, src_meta2 = read_entry_meta_raw(out_pkg)
    meta2_match = False
    if len(src_meta2) == 1:
        meta2_match = (
            src_meta2[0]["type"] == t
            and src_meta2[0]["group"] == g
            and src_meta2[0]["inst"] == inst
            and src_meta2[0]["mem_size"] == m["mem_size"]
            and src_meta2[0]["comp_type"] == m["comp_type"]
            and src_meta2[0]["size_comp"] == m["size_comp"]
            and src_meta2[0]["offset_comp"] == m["offset_comp"]
        )

    verdict_ok = (
        parser_ok and count_ok and tgi_ok and byte_identical
        and meta_ok and meta2_match
        and inst == EXPECTED_INSTANCE
    )

    report = []
    report.append("P28B_ZERO_XML_CANARY")
    report.append(f"ZERO_WRITE_TO_MODS=YES")
    report.append(f"SOURCE_PKG={src}")
    report.append(f"SOURCE_SHA256={src_sha}")
    report.append(f"SOURCE_XML_SHA256={src_body_sha}")
    report.append(f"OVERRIDE_XML_SHA256={body2_sha}")
    report.append(f"XML_BYTE_IDENTICAL={'YES' if byte_identical else 'NO'}")
    report.append(f"WW_ANIM_XML_COUNT={len(ww2)}")
    report.append(f"TYPE=0x{t:08X}")
    report.append(f"GROUP=0x{g:08X}")
    report.append(f"INSTANCE={fmt_inst(inst)}")
    report.append(f"TGI_MATCH={'YES' if tgi_ok else 'NO'}")
    report.append(f"OVERRIDE_PKG={out_pkg}")
    report.append(f"OVERRIDE_PKG_SHA256={out_sha}")
    report.append(f"OVERRIDE_META_MATCH={'YES' if meta2_match else 'NO'}")
    report.append(f"DBPF_METADATA_VALID={'YES' if meta_ok else 'NO'}")
    for f in meta_fails:
        report.append(f"METADATA_FAIL:{f}")
    report.append(f"VERDICT={'PASS' if verdict_ok else 'FAIL'}")

    out_dir.mkdir(parents=True, exist_ok=True)
    out_report.write_text("\n".join(report) + "\n", encoding="utf-8")

    # stdout (ASCII)
    for ln in report:
        print(ln)
    return 0 if verdict_ok else 3


if __name__ == "__main__":
    sys.exit(main())
