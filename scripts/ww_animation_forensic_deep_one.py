#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WW Animation 小包深度取证 — 只针对单个真实 CONFIRMED 包 (如 SAMPLE 3)

【观测模式, 不匹配, 不硬套白盒字段】ZERO WRITE TO MODS。

回答 (证据型, 不做语义猜测):
  A. 真实 WW registration XML 结构 (root / 全 tag / 全 attribute / text nodes / 完整 dump 若小)
  B. 完整 STBL 取证 (每个 STBL resource + 全部 entries)
  C. XML ↔ STBL 交叉匹配 (仅 decimal / hex / 0x 形式与 STBL key 精确相等; 不猜 hash 算法)
  D. CLIP linkage (CLIP TGI 列表 + XML 疑似 clip/reference 值; 仅明确才算 MATCH)
  E. 证据型结论 DISPLAY_STORAGE / DISPLAY_FIELD / INTERNAL_ID_FIELD /
     DISPLAY_INTERNAL_SEPARATION; 若真实 schema 违背白盒 fixture 假设 -> 显式写
     WHITEBOX_SCHEMA_ASSUMPTION_INVALID_FOR_REAL_SAMPLE。

用法 (Windows, 只读):
  python scripts/ww_animation_forensic_deep_one.py --source "<包路径>" [--out-dir output]

输出:
  stdout (证据链) + output/ww_animation_deep_one.md

退出码: 0=完成; 2=参数/IO; 3=解析失败。
"""
import argparse
import hashlib
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse  # noqa: E402

WW_ANIM_XML = 0x7DF2169C
CLIP = 0x6B20C4F3
ANIM_RCOL = 0xBC4A5044
STBL = 0x220557DA


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def read_body_raw(pkg: Path, entry) -> bytes:
    off = entry.offset & 0x7FFFFFFF
    size = entry.size & 0x7FFFFFFF
    with open(pkg, "rb") as fh:
        fh.seek(off)
        return fh.read(size)


def decompress_maybe(body: bytes) -> bytes:
    if body[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        try:
            return zlib.decompress(body)
        except Exception:
            return body
    return body


# ---------- STBL ----------
def parse_stbl(body: bytes):
    if body[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        try:
            body = zlib.decompress(body)
        except Exception as e:
            raise ValueError(f"zlib: {e}")
    if len(body) < 24 or body[0:4] != b"STBL":
        raise ValueError(f"magic 不符: {body[0:4]!r}")
    version = struct.unpack_from("<H", body, 4)[0]
    is_compressed = body[6]
    num_entries = struct.unpack_from("<Q", body, 7)[0]
    off = 21
    entries = []
    for _ in range(num_entries):
        if off + 7 > len(body):
            raise ValueError(f"entry 越界 off={off}")
        kh = struct.unpack_from("<I", body, off)[0]
        flags = body[off + 4]
        ln = struct.unpack_from("<H", body, off + 5)[0]
        off += 7
        if off + ln > len(body):
            raise ValueError(f"string 越界 key=0x{kh:08X}")
        text = body[off:off + ln].decode("utf-8", errors="replace")
        off += ln
        entries.append((kh, flags, text))
    return version, is_compressed, num_entries, entries


# ---------- XML 结构观测 ----------
def xml_observe(text: str):
    """用 ElementTree 观察真实结构 (root / child tag 集合 / 属性集合 / text nodes)。
    WW XML 若可解析则给出完整树; 否则退回正则属性收集。返回 dict。"""
    res = {"root": None, "tags": set(), "attrs": set(), "nodes": {}, "text_nodes": [], "parse_ok": False}
    # 属性收集 (正则, 覆盖无法解析的情况)
    for m in re.finditer(r'<([A-Za-z_][\w:-]*)\b([^>]*?)(/?)>', text):
        tag = m.group(1)
        res["tags"].add(tag)
        for am in re.finditer(r'([A-Za-z_][\w:-]*)\s*=\s*"([^"]*)"', m.group(2)):
            res["attrs"].add(am.group(1))
            res["nodes"].setdefault(am.group(1), set()).add(am.group(2))
    try:
        root = ET.fromstring(text)
        res["parse_ok"] = True
        res["root"] = root.tag
        for el in root.iter():
            res["tags"].add(el.tag)
            for k, v in el.attrib.items():
                res["attrs"].add(k)
                res["nodes"].setdefault(k, set()).add(v)
            if el.text and el.text.strip():
                res["text_nodes"].append((el.tag, el.text.strip()))
    except ET.ParseError as e:
        res["parse_error"] = str(e)
    return res


def scalar_values(xml_text: str):
    """收集 XML 中所有 scalar 候选 (decimal / hex / 0x / key-like), 供 STBL cross-match。"""
    out = []
    for m in re.finditer(r'"(?P<v>[A-Za-z0-9_\-\.:]{1,64})"', xml_text):
        v = m.group("v")
        out.append(v)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--out-dir", default="output")
    a = ap.parse_args()
    src = Path(a.source)
    if not src.is_file():
        print("ERROR: source 不存在", file=sys.stderr)
        return 2
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    s_sha = sha256(src)
    idx, err = safe_parse(src)
    if err is not None or idx is None:
        print(f"ERROR: 解析失败: {err}", file=sys.stderr)
        return 3

    ww = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    stbls = [e for e in idx.entries if e.type_id == STBL]
    clips = [e for e in idx.entries if e.type_id == CLIP]
    anims = [e for e in idx.entries if e.type_id == ANIM_RCOL]

    md = []
    md.append("# WW Animation 小包深度取证 (deep one)")
    md.append("")
    md.append(f"SOURCE={src}")
    md.append(f"SHA256={s_sha}")
    md.append(f"WW_XML_COUNT={len(ww)}  STBL_COUNT={len(stbls)}  CLIP={len(clips)}  ANIM_RCOL={len(anims)}  TOTAL_RESOURCES={len(idx.entries)}")
    md.append("")

    # ---------- A: registration XML 结构 ----------
    xml_blocks = []
    for i, e in enumerate(ww):
        tgi = f"0x{e.type_id:08X}/0x{e.group_id:08X}/0x{e.instance_id:016X}"
        body = read_body_raw(src, e)
        csize = len(body)
        plain = decompress_maybe(body)
        dsize = len(plain)
        try:
            text = plain.decode("utf-8", errors="replace")
        except Exception as ex:
            text = ""
        obs = xml_observe(text)
        md.append("## A. WW registration XML")
        md.append(f"- index={i} TGI={tgi}")
        md.append(f"- compressed_size={csize}")
        md.append(f"- decompressed_size={dsize}")
        md.append(f"- root={obs['root']}")
        md.append(f"- parse_ok={obs['parse_ok']}")
        md.append(f"- unique_tags={sorted(obs['tags'])}")
        md.append(f"- unique_attributes={sorted(obs['attrs'])}")
        for k, vs in obs["nodes"].items():
            md.append(f"  - attr[{k}] = {sorted(vs)[:12]}")
        for (tag, txt) in obs["text_nodes"]:
            md.append(f"  - text[{tag}] = {txt[:120]}")
        md.append("- REAL_XML_SAMPLE (truncated 4000):")
        md.append("```xml")
        md.append(text[:4000])
        md.append("```")
        xml_blocks.append((tgi, text, obs))
    if not ww:
        md.append("## A. WW registration XML")
        md.append("NONE  (此包无 WW_ANIM_XML)")
        xml_blocks = []

    # ---------- B: STBL 取证 ----------
    md.append("")
    md.append("## B. STBL 取证")
    stbl_keys = []          # list of int keys
    stbl_resources = []     # (tgi, version, comp, count)
    stbl_full = {}          # key -> (tgi, text)
    for e in stbls:
        tgi = f"0x{e.type_id:08X}/0x{e.group_id:08X}/0x{e.instance_id:016X}"
        try:
            ver, comp, cnt, entries = parse_stbl(read_body_raw(src, e))
        except ValueError as vex:
            md.append(f"- STBL {tgi}: PARSE_ERROR: {vex}")
            continue
        stbl_resources.append((tgi, ver, comp, cnt))
        md.append(f"- STBL {tgi}: locale=group(0x{e.group_id:08X}) version={ver} compressed={comp} entry_count={cnt}")
        md.append("  - ENTRIES:")
        for (kh, flags, txt) in entries:
            stbl_keys.append(kh)
            stbl_full[kh] = (tgi, txt)
            md.append(f"      key=0x{kh:08X} (dec {kh}) flags={flags}: {txt!r}")
    md.append("")
    md.append(f"STBL_TOTAL_ENTRIES={len(stbl_keys)}")

    # ---------- C: XML ↔ STBL cross-match ----------
    md.append("")
    md.append("## C. XML ↔ STBL linkage (仅 scalar 精确相等, 不猜 hash)")
    stbl_key_set = set(stbl_keys)
    links = []
    if xml_blocks:
        for (tgi_x, xml_text, _obs) in xml_blocks:
            for v in scalar_values(xml_text):
                matched = []
                if v.isdigit():
                    iv = int(v)
                    if iv in stbl_key_set:
                        matched.append(iv)
                # hex 不含 0x 前缀: 形如 A1B2C3D4
                if re.fullmatch(r'[0-9A-Fa-f]{6,16}', v):
                    try:
                        hv = int(v, 16)
                        if hv in stbl_key_set:
                            matched.append(hv)
                    except ValueError:
                        pass
                # 0x 前缀
                if v.lower().startswith("0x") and re.fullmatch(r'0x[0-9A-Fa-f]{1,16}', v, re.I):
                    try:
                        hv = int(v, 16)
                        if hv in stbl_key_set:
                            matched.append(hv)
                    except ValueError:
                        pass
                for mkey in matched:
                    tgi_s, text_s = stbl_full[mkey]
                    links.append((tgi_x, v, mkey, text_s))
                    md.append(f"- XML(xml_tgi={tgi_x}) value={v!r} -> STBL key=0x{mkey:08X} ({tgi_s}) text={text_s!r}")
    if not links:
        md.append("XML_STBL_LINKAGE=NONE_FOUND")
    md.append(f"XML_STBL_LINKS_FOUND={len(links)}")

    # ---------- D: CLIP linkage ----------
    md.append("")
    md.append("## D. CLIP linkage")
    md.append("- CLIP resources:")
    for e in clips:
        md.append(f"    TGI=0x{e.type_id:08X}/0x{e.group_id:08X}/0x{e.instance_id:016X} size={e.size & 0x7FFFFFFF}")
    md.append("- ANIM_RCOL resources:")
    for e in anims:
        md.append(f"    TGI=0x{e.type_id:08X}/0x{e.group_id:08X}/0x{e.instance_id:016X} size={e.size & 0x7FFFFFFF}")
    if xml_blocks:
        md.append("- XML 中疑似 clip/reference 值 (clip/ref/id/name 相关属性 + 全部 scalar):")
        clip_ref_vals = []
        for (tgi_x, xml_text, _obs) in xml_blocks:
            for m in re.finditer(r'\b([\w]*clip[\w]*|reference|ref|instance|resource|id)\b\s*=\s*"([^"]*)"', xml_text, re.I):
                clip_ref_vals.append((tgi_x, m.group(1), m.group(2)))
        if clip_ref_vals:
            for (t, k, v) in clip_ref_vals:
                md.append(f"    XML({t}) {k}={v!r}")
        else:
            md.append("    (无明确 clip/reference 属性)")
    # 明确关联判断: 仅当 XML 有 clip 属性值且其 hex/dec 与某 CLIP instance 相等
    clip_insts = [e.instance_id for e in clips]
    clip_match = []
    if xml_blocks:
        for (tgi_x, xml_text, _obs) in xml_blocks:
            for (t, k, v) in clip_ref_vals:
                hv = None
                if v.isdigit():
                    hv = int(v)
                elif v.lower().startswith("0x") and re.fullmatch(r'0x[0-9A-Fa-f]{1,16}', v):
                    hv = int(v, 16)
                elif re.fullmatch(r'[0-9A-Fa-f]{8,16}', v):
                    hv = int(v, 16)
                if hv is not None and hv in clip_insts:
                    clip_match.append((t, k, v, f"0x{hv:016X}"))
    if clip_match:
        md.append("- CLIP_MATCH (明确):")
        for (t, k, v, inst) in clip_match:
            md.append(f"    XML({t}) {k}={v!r} -> CLIP instance {inst}")
    else:
        md.append("CLIP_LINKS_FOUND=NONE_FOUND (未发现明确 instance 引用)")
    md.append(f"CLIP_LINKS_FOUND={len(clip_match)}")

    # ---------- E: 证据型结论 ----------
    md.append("")
    md.append("## E. 证据型结论")
    # 真实 schema 是否违背白盒假设 (WickedWhims 期望 animation_raw_display_name / animation_id / animation_clip_name)
    schema_invalid = False
    if ww:
        xml_text_all = "\n".join(t for (_tg, t, _o) in xml_blocks)
        whitebox_expected = {"animation_raw_display_name", "animation_id", "animation_clip_name"}
        found_attrs = {k for k in re.findall(r'([A-Za-z_][\w:-]*)\s*=', xml_text_all)}
        if not whitebox_expected.issubset(found_attrs):
            schema_invalid = True
    if schema_invalid:
        md.append("WHITEBOX_SCHEMA_ASSUMPTION_INVALID_FOR_REAL_SAMPLE: 白盒 fixture 期望的 "
                  "animation_raw_display_name / animation_id / animation_clip_name 未在真实包所有属性集合中发现。")
        md.append("(不硬套白盒字段; 以下结论仅基于真实观测)")

    # DISPLAY_STORAGE
    display_storage = "UNKNOWN"
    display_field = "UNKNOWN"
    internal_id_field = "UNKNOWN"
    disp_sep = "UNKNOWN"

    if ww:
        # 收集全部属性值, 找"最像显示文本"的属性 (含空格/自然语言, 非纯 hash/key)
        cand_disp = []
        for (tgi_x, xml_text, obs) in xml_blocks:
            for k, vs in obs["nodes"].items():
                for v in vs:
                    if re.search(r'\s+[A-Za-z]', v) and not re.fullmatch(r'[0-9A-Fa-f]{6,}', v):
                        cand_disp.append((k, v))
        if cand_disp:
            display_field = cand_disp[0][0]
            display_storage = "DIRECT_XML"
        else:
            display_storage = "UNKNOWN_DIRECT_TEXT_NONE"

        # internal id: 属性名含 id/hash/key/instance 且值非自然语言
        for (tgi_x, xml_text, obs) in xml_blocks:
            for k, vs in obs["nodes"].items():
                if re.search(r'id|hash|key|instance|ref', k, re.I):
                    for v in vs:
                        if re.fullmatch(r'[0-9A-Za-z_-]{1,64}', v) and not re.search(r'\s', v):
                            internal_id_field = k
                            break
                    if internal_id_field != "UNKNOWN":
                        break
            if internal_id_field != "UNKNOWN":
                break

        # 若有 STBL links -> display 可能经 STBL
        if links:
            display_storage = "STBL_REFERENCED"
            if display_field != "UNKNOWN":
                disp_sep = "PARTIAL"
        elif stbls:
            if display_storage == "DIRECT_XML":
                display_storage = "STBL_PRESENT_BUT_UNLINKED"
                disp_sep = "CLEAR" if display_field != "UNKNOWN" else "UNKNOWN"
        else:
            if display_storage == "DIRECT_XML":
                disp_sep = "CLEAR" if display_field != "UNKNOWN" else "UNKNOWN"
    else:
        display_storage = "UNKNOWN (无 WW XML)"

    md.append(f"DISPLAY_STORAGE={display_storage}")
    md.append(f"DISPLAY_FIELD={display_field}")
    md.append(f"INTERNAL_ID_FIELD={internal_id_field}")
    md.append(f"DISPLAY_INTERNAL_SEPARATION={disp_sep}")

    out_path = out_dir / "ww_animation_deep_one.md"
    out_path.write_text("\n".join(md) + "\n", encoding="utf-8")

    # ---------- stdout ----------
    print(f"SOURCE={src}")
    print(f"SHA256={s_sha}")
    print(f"WW_XML_COUNT={len(ww)}  STBL_COUNT={len(stbls)}  CLIP={len(clips)}")
    if ww:
        tgi0 = f"0x{ww[0].type_id:08X}/0x{ww[0].group_id:08X}/0x{ww[0].instance_id:016X}"
        print(f"WW_XML_TGI={tgi0}")
        print(f"REAL_XML_STRUCTURE: root={xml_blocks[0][2]['root']}")
        print(f"  unique_tags={sorted(xml_blocks[0][2]['tags'])}")
        print(f"  unique_attributes={sorted(xml_blocks[0][2]['attrs'])}")
        print("REAL_XML_SAMPLE:")
        print("```xml")
        print(xml_blocks[0][1][:2500])
        print("```")
    print(f"STBL_COUNT={len(stbls)}")
    print(f"STBL_RESOURCES={stbl_resources}")
    print(f"XML_STBL_LINKS_FOUND={len(links)}")
    if links:
        for (t, v, k, s) in links[:20]:
            print(f"  XML({t}) {v!r} -> STBL key=0x{k:08X} -> {s!r}")
    print(f"CLIP_LINKS_FOUND={len(clip_match)}")
    if clip_match:
        for (t, k, v, inst) in clip_match:
            print(f"  XML({t}) {k}={v!r} -> CLIP {inst}")
    if schema_invalid:
        print("WHITEBOX_SCHEMA_ASSUMPTION_INVALID_FOR_REAL_SAMPLE=YES")
    print(f"DISPLAY_STORAGE={display_storage}")
    print(f"DISPLAY_FIELD={display_field}")
    print(f"INTERNAL_ID_FIELD={internal_id_field}")
    print(f"DISPLAY_INTERNAL_SEPARATION={disp_sep}")
    print(f"ZERO_WRITE_TO_MODS=YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
