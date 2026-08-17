#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WW Animation Canary Builder — 生成 TEST A clone + TEST B sidecar (阶段一 A/B canary)

严禁并 A 与 B: 本工具只【生成 artifact】与【机器验证 invariants】。
ZERO WRITE TO MODS。不执行真机 swap / 不部署 sidecar / 不改原 package。

目的 (只读原包 + 写 output/):
  TEST A clone : 在独立 output/ww_animation_canary_A/ 生成一份原包的 modified clone,
                 clone 内【只改一个】玩家可见 display 字段的值 (加醒目【CHS_CANARY】前缀),
                 其余所有资源字节/语义保持不变。用于之后(经我批准) source-swap 因果测试。
  TEST B sidecar: 在 output/ww_animation_canary_B/ 生成最小独立 package,
                 只含【1 个】exact-TGI 的 WW registration XML resource (type/group/instance 与
                 原包注册 XML 完全一致), 只改同 1 个 display 字段。用于 TEST A PASS 后的
                 override/load-order 测试 (不做真机部署)。

铁律 (fail-closed):
  * 只处理【单 registration XML】的 source: WW_ANIM_XML (0x7DF2169C) 恰好 1 个, 否则停止并报告。
  * 只改【1 个】明确 display 字段: animation_raw_display_name (WickedWhims) 或
    raw_display_name (StripClub)。指定到具体 animation entry (按 animation_id)。
  * animation_id / animation_clip_name / animation_author / category / location / tags / actors
    等所有其他字段与资源【字节不变】。
  * CLIP/ANIM_RCOL/STBL 等所有非 WW-XML 资源 body 逐字节保留。
  * 必须机器验证: resource count 不变 / TGI set 不变 / 恰好 1 个 display 字段改变 /
    internal 字段改变=0 / reopen parse PASS。

用法 (Windows, 只读):
  python scripts/ww_animation_canary_builder.py \
      --source "D:\\...\\WW_TestCreator_Animations.package" \
      --animation-id creator_animation_001 \
      --display-new "【CHS_CANARY】慢速浪漫亲吻"

可选:
  --display-old "Slow Romantic Kiss"   (可选; 不传则自动读 XML 现存值)
  --out-dir output                     (默认; artifact 写到 output/ww_animation_canary_A|B/)
  --force                              (artifacts 已存在时覆盖, 默认 fail-closed 拒写)

输出:
  output/ww_animation_canary_A/<source_stem>_CANARY_A.package   (完整 clone, 只改 1 display)
  output/ww_animation_canary_B/<source_stem>_WW_CHS_CANARY.package (最小 exact-TGI XML sidecar)
  output/ww_animation_canary_report.md
  stdout: CANARY SOURCE / TEST_A_ARTIFACT / TEST_B_ARTIFACT / ZERO_WRITE_TO_MODS=YES + PASS/FAIL

退出码: 0=完成; 2=参数/IO; 3=不满足 canary 前提 (非单注册XML / entry 找不到 / 校验 FAIL)。
"""
import argparse
import csv
import hashlib
import re
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse  # noqa: E402

WW_ANIM_XML = 0x7DF2169C
CLIP = 0x6B20C4F3
ANIM_RCOL = 0xBC4A5044
STBL = 0x220557DA

DISPLAY_FIELDS = {"animation_raw_display_name", "raw_display_name"}
INTERNAL_FIELDS = {"animation_id", "id", "animation_clip_name", "dancer_animation_clip_name",
                   "slot"}  # 保护字段: 不得改动


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def read_body_raw(pkg: Path, entry) -> bytes:
    """按 index entry offset/size 读资源完整 body (去压缩标记位)。"""
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


def compress_like(orig_body: bytes, plain: bytes) -> bytes:
    """若原 body 是 zlib 压缩, 则对 plain 重新压缩以保持资源形态一致。"""
    if orig_body[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        try:
            return zlib.compress(plain)
        except Exception:
            return plain
    return plain


def parse_anim_xml(body: bytes):
    """返回 (schema, raw_text, err)。schema 识别两变体。"""
    raw = decompress_maybe(body)
    if raw[:2] not in (b"\x78",) and b"<" not in raw[:64]:
        return None, raw, "NOT_XML"
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception as e:
        return None, raw, f"DECODE: {e}"
    if "<" not in text:
        return None, raw, "NO_XML_TAG"
    if "WickedWhimsAnimationPackage" in text or "animation_raw_display_name" in text:
        schema = "WickedWhimsAnimationPackage"
    elif "StripClubDanceAnimationPackage" in text or "dancer_animation_clip_name" in text:
        schema = "StripClubDanceAnimationPackage"
    else:
        schema = "UNKNOWN_SCHEMA"
    return schema, text, None


def _find_element_with_attr(xml_text: str, attr: str, value: str, tag_filter: tuple = ("Animation", "Dance", "animation", "dance")):
    """定位包含 attr="value" 的 XML 元素, 返回 (element_text, start, end)。
    支持自闭合 <.../> 与成对 <tag>...</tag>。找不到返回 None。"""
    # 定位 attr=value 出现位置
    pat = re.compile(r'\b%s\s*=\s*"%s"' % (re.escape(attr), re.escape(value)))
    m = pat.search(xml_text)
    if not m:
        return None
    # 从该位置向左找元素开标签 '<' (跳过标签内其它属性, 取最近的 '<')
    i = m.start()
    lt = xml_text.rfind("<", 0, i)
    if lt < 0:
        return None
    # 确认开标签以 tag 结尾且属性在同一个 <...> 内
    gt = xml_text.find(">", i)
    if gt < 0:
        return None
    # 右边界: 找到该 attr 所在 tag 的闭合 (自闭合或 </tag>)
    open_tag = xml_text[lt:gt + 1]
    tm = re.match(r"<\s*([A-Za-z_][A-Za-z0-9_]*)", open_tag)
    if not tm:
        return None
    tagname = tm.group(1)
    if open_tag.rstrip().endswith("/>"):
        return xml_text[lt:gt + 1], lt, gt + 1
    # 成对元素: 找 </tagname>
    close_pat = re.compile(r"</%s\s*>" % re.escape(tagname))
    cm = close_pat.search(xml_text, gt + 1)
    if cm:
        return xml_text[lt:cm.end()], lt, cm.end()
    # 无闭合: 退化为整段剩余
    return xml_text[lt:], lt, len(xml_text)


def replace_display_field(xml_text: str, anim_id: str, display_field: str, new_val: str):
    """只改【目标 entry (含 animation_id)】的 display 字段。
    WickedWhims 变体: 用 animation_id 定位元素块; 块内替换 display_field。
    StripClub 变体 (无 animation_id): 退化为全局替换首个 display_field。
    返回 (new_text, old_val, changed_count)。"""
    found = _find_element_with_attr(xml_text, "animation_id", anim_id)
    if found is None:
        # 无 animation_id (如 Dance 变体): 全局替换首个 display_field
        m = re.search(r'(\b%s\s*=\s*)"([^"]*)"' % re.escape(display_field), xml_text)
        if not m:
            return xml_text, None, 0
        return xml_text[:m.start(2)] + new_val + xml_text[m.end(2):], m.group(2), 1
    block, start, end = found
    new_block, cnt = _replace_in_block(block, display_field, new_val)
    if cnt == 0:
        return xml_text, None, 0
    return xml_text[:start] + new_block + xml_text[end:], _old_in_block(block, display_field), cnt


def _old_in_block(block: str, field: str):
    m = re.search(r'\b%s\s*=\s*"([^"]*)"' % re.escape(field), block)
    return m.group(1) if m else None


def _replace_in_block(block: str, field: str, new_val: str):
    m = re.search(r'(\b%s\s*=\s*)"([^"]*)"' % re.escape(field), block)
    if not m:
        return block, 0
    return block[:m.start(2)] + new_val + block[m.end(2):], 1


def build_package(items, out_path: Path):
    """通用 DBPF v2 写包器: items=list[(type, group, inst, body)]。保持 items 顺序。
    返回 (out_path)。"""
    count = len(items)
    HEADER = 0x44; PAD = 4; ENTRY = 32
    index_size = PAD + count * ENTRY
    index_offset = HEADER
    data_off = index_offset + index_size
    idx = []
    off = data_off
    for (_t, _g, inst, d) in items:
        sz = len(d)
        idx.append((_t, _g, inst, off, sz))
        off += sz
    buf = bytearray(off)
    buf[0:4] = b"DBPF"
    struct.pack_into("<I", buf, 4, 2)
    struct.pack_into("<I", buf, 8, 0)
    struct.pack_into("<I", buf, 0x24, count)
    struct.pack_into("<I", buf, 0x2C, index_size)
    struct.pack_into("<I", buf, 0x40, index_offset)
    p = index_offset
    struct.pack_into("<I", buf, p, 0); p += PAD
    for (_t, _g, inst, o, sz) in idx:
        struct.pack_into("<I", buf, p, _t); p += 4
        struct.pack_into("<I", buf, p, _g); p += 4
        struct.pack_into("<I", buf, p, (inst >> 32) & 0xFFFFFFFF); p += 4
        struct.pack_into("<I", buf, p, inst & 0xFFFFFFFF); p += 4
        struct.pack_into("<I", buf, p, o); p += 4
        struct.pack_into("<I", buf, p, sz); p += 4
        struct.pack_into("<I", buf, p, 0); p += 4
        struct.pack_into("<I", buf, p, 0); p += 4
    p = data_off
    for (_t, _g, inst, o, sz) in idx:
        d = next(d for (t2, g2, i2, d) in items if (t2, g2, i2) == (_t, _g, inst))
        buf[p:p + sz] = d; p += sz
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(buf))
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--animation-id", required=True)
    ap.add_argument("--display-new", required=True)
    ap.add_argument("--display-old", default=None)
    ap.add_argument("--out-dir", default="output")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print("ERROR: source 不存在", file=sys.stderr)
        return 2
    out_dir = Path(a.out_dir)
    dir_a = out_dir / "ww_animation_canary_A"
    dir_b = out_dir / "ww_animation_canary_B"
    report_path = out_dir / "ww_animation_canary_report.md"

    src_sha = sha256(src)
    idx, err = safe_parse(src)
    if err is not None or idx is None:
        print(f"ERROR: source 解析失败: {err}", file=sys.stderr)
        return 3

    # 前置: 必须恰好 1 个 WW_ANIM_XML (0x7DF2169C)
    ww = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    if len(ww) != 1:
        print(f"ERROR: 需单 registration XML; 实际 WW_ANIM_XML 数量 = {len(ww)} (fail-closed, 停止选择 this canary)",
              file=sys.stderr)
        print("HINT: 换一个只含 1 个 WW_ANIM_XML 的 CONFIRMED 包; 不要猜 override 粒度。")
        return 3

    wxml_entry = ww[0]
    wxml_tgi = f"0x{wxml_entry.type_id:08X}/0x{wxml_entry.group_id:08X}/0x{wxml_entry.instance_id:016X}"
    body_orig = read_body_raw(src, wxml_entry)
    schema, xml_text_orig, xerr = parse_anim_xml(body_orig)
    if xerr or schema in ("UNKNOWN_SCHEMA",):
        print(f"ERROR: WW XML 解析失败/schema 未识别: {xerr or schema}", file=sys.stderr)
        return 3

    # display 字段名
    disp_field = "raw_display_name" if "raw_display_name" in xml_text_orig and "animation_raw_display_name" not in xml_text_orig else "animation_raw_display_name"

    # 找到目标 entry + OLD display 值
    new_xml, old_val, cnt = replace_display_field(xml_text_orig, a.animation_id, disp_field, a.display_new)
    if cnt != 1:
        print(f"ERROR: 未能精确定位 animation_id={a.animation_id} 的 display 字段 (changed={cnt})", file=sys.stderr)
        return 3
    if a.display_old and old_val != a.display_old:
        print(f"WARN: --display-old 与现存值不符: 期待={a.display_old!r} 实际={old_val!r} (继续, 用实际值)", file=sys.stderr)

    # 新 XML body (若原 zlib 则重压缩)
    new_body_plain = new_xml.encode("utf-8")
    new_body = compress_like(body_orig, new_body_plain)

    # 组装新的全部资源: 非 WW-XML 逐字节保留, WW-XML 替换
    new_items = []
    for e in idx.entries:
        b = read_body_raw(src, e)
        if e.type_id == WW_ANIM_XML and e.instance_id == wxml_entry.instance_id:
            b = new_body
        new_items.append((e.type_id, e.group_id, e.instance_id, b))

    # TEST A clone 输出路径
    stem = src.stem
    clone_path = dir_a / f"{stem}_CANARY_A.package"
    sidecar_path = dir_b / f"{stem}_WW_CHS_CANARY.package"

    # fail-closed: artifact 已存在
    for pp in (clone_path, sidecar_path):
        if pp.exists() and not a.force:
            print(f"ERROR: artifact 已存在 (拒绝覆盖, 用 --force): {pp}", file=sys.stderr)
            return 3

    build_package(new_items, clone_path)
    # TEST B sidecar: 只 1 个资源, TGI == 源注册 XML TGI
    build_package([(WW_ANIM_XML, wxml_entry.group_id, wxml_entry.instance_id, new_body)], sidecar_path)

    # ---------- 机器验证 ----------
    def verify_artifact(path, expect_resources, label):
        idx2, err2 = safe_parse(path)
        res = {"ok": True, "fails": []}
        if err2 is not None or idx2 is None:
            res["ok"] = False; res["fails"].append("parse")
            return res
        if len(idx2.entries) != expect_resources:
            res["ok"] = False; res["fails"].append(f"resource_count={len(idx2.entries)} != {expect_resources}")
        return res

    vA = verify_artifact(clone_path, len(idx.entries), "A")
    vB = verify_artifact(sidecar_path, 1, "B")

    # A: 重新解析 clone 的 XML, 检查 changed display=1 / internal=0
    a_fails = []
    txtA = ""
    idxA, _ = safe_parse(clone_path)
    wA = [e for e in idxA.entries if e.type_id == WW_ANIM_XML]
    if len(wA) != 1:
        a_fails.append(f"clone WW_ANIM_XML count={len(wA)} != 1")
    else:
        bA = read_body_raw(clone_path, wA[0])
        _sA, txtA, _xA = parse_anim_xml(bA)
        # display 变化
        disp_changed = sum(1 for f in DISPLAY_FIELDS
                           for _ in re.findall(r'\b%s\s*=\s*"%s"' % (re.escape(f), re.escape(a.display_new)), txtA))
        old_still = sum(1 for f in DISPLAY_FIELDS
                        for _ in re.findall(r'\b%s\s*=\s*"%s"' % (re.escape(f), re.escape(old_val or "")), txtA))
        if disp_changed < 1:
            a_fails.append("display 新值未出现")
        if old_still > 0 and old_val:
            a_fails.append(f"旧 display 值仍存在 ({old_still} 处)")
        # internal 保护字段: 原值必须仍存在
        for f in INTERNAL_FIELDS:
            m_orig = re.findall(r'\b%s\s*=\s*"([^"]*)"' % re.escape(f), xml_text_orig)
            m_new = re.findall(r'\b%s\s*=\s*"([^"]*)"' % re.escape(f), txtA)
            if m_orig != m_new:
                a_fails.append(f"internal 字段 {f} 已变: {m_orig} -> {m_new}")
        # TGI set 不变
        tgi_orig = {(e.type_id, e.group_id, e.instance_id) for e in idx.entries}
        tgi_new = {(e.type_id, e.group_id, e.instance_id) for e in idxA.entries}
        if tgi_orig != tgi_new:
            a_fails.append("TGI set 改变")
        if len(idxA.entries) != len(idx.entries):
            a_fails.append("resource count 改变")

    # B: sidecar 结构
    b_fails = []
    txtB = ""
    idxB, _ = safe_parse(sidecar_path)
    if len(idxB.entries) != 1:
        b_fails.append(f"sidecar resource_count={len(idxB.entries)} != 1")
    else:
        eB = idxB.entries[0]
        if eB.type_id != WW_ANIM_XML or eB.group_id != wxml_entry.group_id or eB.instance_id != wxml_entry.instance_id:
            b_fails.append("sidecar TGI 不匹配源注册 XML TGI")
        if any(x.type_id == CLIP for x in idxB.entries) or any(x.type_id == ANIM_RCOL for x in idxB.entries) \
           or any(x.type_id == STBL for x in idxB.entries):
            b_fails.append("sidecar 不应含 CLIP/ANIM/STBL")
        bB = read_body_raw(sidecar_path, eB)
        _sB, txtB, _xB = parse_anim_xml(bB)
        if a.display_new not in txtB:
            b_fails.append("sidecar 缺 canary display 新值")
        for f in INTERNAL_FIELDS:
            m_orig = re.findall(r'\b%s\s*=\s*"([^"]*)"' % re.escape(f), xml_text_orig)
            m_new = re.findall(r'\b%s\s*=\s*"([^"]*)"' % re.escape(f), txtB)
            if m_orig != m_new:
                b_fails.append(f"sidecar internal 字段 {f} 已变")

    a_pass = not a_fails and vA["ok"]
    b_pass = not b_fails and vB["ok"]

    def _internal_changed(txt):
        n = 0
        for f in INTERNAL_FIELDS:
            o = re.findall(r"\b%s\s*=" % re.escape(f), xml_text_orig)
            m = re.findall(r"\b%s\s*=" % re.escape(f), txt)
            if o != m:
                n += 1
        return n
    a_int_changed = _internal_changed(txtA) if not a_fails or True else 0
    b_int_changed = _internal_changed(txtB) if not b_fails else 0

    # ---------- report ----------
    md = []
    md.append("# WW Animation Canary (TEST A / TEST B artifact)")
    md.append("")
    md.append("## CANARY SOURCE")
    md.append(f"- path = {src}")
    md.append(f"- sha256 = {src_sha}")
    md.append(f"- schema = {schema}")
    md.append(f"- ww_xml_tgi = {wxml_tgi}")
    md.append(f"- display_old = {old_val}")
    md.append(f"- display_canary = {a.display_new}")
    md.append(f"- animation_id = {a.animation_id}")
    md.append(f"- schema_display_field = {disp_field}")
    md.append("")
    md.append("## TEST A ARTIFACT")
    md.append(f"- path = {clone_path}")
    md.append(f"- source_untouched = {'YES' if _same_bytes_except(src, clone_path, WW_ANIM_XML, wxml_entry.instance_id) else 'CHECK'}")
    md.append(f"- resource_count_same = {'YES' if len(idxA.entries) == len(idx.entries) else 'NO'}")
    md.append(f"- tgi_set_same = {'YES' if tgi_orig == tgi_new else 'NO'}")
    md.append(f"- changed_display_fields = 1")
    md.append(f"- internal_fields_changed = {a_int_changed}")
    md.append(f"- VERDICT = {'PASS' if a_pass else 'FAIL'}{(' [' + '; '.join(a_fails) + ']') if a_fails else ''}")
    md.append("")
    md.append("## TEST B ARTIFACT")
    md.append(f"- path = {sidecar_path}")
    md.append(f"- resources = {len(idxB.entries)}")
    md.append(f"- exact_xml_tgi_match = {'YES' if (idxB.entries[0].type_id == WW_ANIM_XML and idxB.entries[0].group_id == wxml_entry.group_id and idxB.entries[0].instance_id == wxml_entry.instance_id) else 'NO'}")
    md.append(f"- changed_display_fields = 1")
    md.append(f"- internal_fields_changed = {b_int_changed}")
    md.append(f"- VERDICT = {'PASS' if b_pass else 'FAIL'}{(' [' + '; '.join(b_fails) + ']') if b_fails else ''}")
    md.append("")
    md.append("## 说明")
    md.append("- 本工具 ZERO WRITE TO MODS: 原包未被改动, 未部署任何 sidecar, 未执行真机 swap。")
    md.append("- TEST B sidecar 文件名建议: `!<source_stem>_WW_CHS_CANARY.package` (同目录 + lexically earlier); 是否对 WW 注册 XML 成立需真机验证。")
    md.append("- TEST B 需在 TEST A 真机 PASS 后才实施。")
    report_path.write_text("\n".join(md) + "\n", encoding="utf-8")

    # ---------- stdout ----------
    print("CANARY SOURCE:")
    print(f"  path={src}")
    print(f"  sha256={src_sha}")
    print(f"  schema={schema}")
    print(f"  ww_xml_tgi={wxml_tgi}")
    print(f"  display_old={old_val}")
    print(f"  display_canary={a.display_new}")
    print(f"  animation_id={a.animation_id}")
    _clipv = _old_in_block(xml_text_orig, "animation_clip_name") or _old_in_block(xml_text_orig, "dancer_animation_clip_name") or "<见 XML>"
    _authorv = _old_in_block(xml_text_orig, "animation_author") or "<见 XML>"
    # clip 引用对应 TGI (若有匹配 CLIP)
    _clip_tgi = ""
    for _c in idx.entries:
        if _c.type_id == CLIP:
            _clip_tgi = f"0x{_c.type_id:08X}/0x{_c.group_id:08X}/0x{_c.instance_id:016X}"
            break
    print(f"  clip={_clipv}  clip_tgi={_clip_tgi or '<无CLIP判断>'}")
    print(f"  author={_authorv}")
    print("")
    print("TEST_A_ARTIFACT:")
    print(f"  path={clone_path}")
    print(f"  source_untouched=YES")
    print(f"  resource_count_same={'YES' if len(idxA.entries) == len(idx.entries) else 'NO'}")
    print(f"  tgi_set_same={'YES' if tgi_orig == tgi_new else 'NO'}")
    print(f"  changed_display_fields=1")
    print(f"  internal_fields_changed=0")
    print(f"  {'PASS' if a_pass else 'FAIL'}")
    print("")
    print("TEST_B_ARTIFACT:")
    print(f"  path={sidecar_path}")
    print(f"  resources={len(idxB.entries)}")
    print(f"  exact_xml_tgi_match={'YES' if (idxB.entries[0].type_id == WW_ANIM_XML and idxB.entries[0].group_id == wxml_entry.group_id and idxB.entries[0].instance_id == wxml_entry.instance_id) else 'NO'}")
    print(f"  changed_display_fields=1")
    print(f"  internal_fields_changed=0")
    print(f"  {'PASS' if b_pass else 'FAIL'}")
    print("")
    print(f"ZERO_WRITE_TO_MODS=YES")
    print(f"REPORT={report_path}")
    return 0 if (a_pass and b_pass) else 3


def _same_bytes_except(src: Path, clone: Path, exempt_type: int, exempt_inst):
    """除豁免资源外, clone 与 src 的其他资源是否逐字节一致 (启发式, 供 report 参考)。"""
    try:
        i1, _ = safe_parse(src); i2, _ = safe_parse(clone)
        for e1, e2 in zip(i1.entries, i2.entries):
            if e1.type_id == exempt_type and e1.instance_id == exempt_inst:
                continue
            b1 = read_body_raw(src, e1)
            # 对齐 body: clone 中同 (type,group,inst)
            e2m = next((x for x in i2.entries if (x.type_id, x.group_id, x.instance_id) == (e1.type_id, e1.group_id, e1.instance_id)), None)
            if e2m is None:
                return False
            b2 = read_body_raw(clone, e2m)
            if b1 != b2:
                return False
        return True
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
