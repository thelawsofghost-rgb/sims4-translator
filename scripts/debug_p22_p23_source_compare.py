#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
debug_p22_p23_source_compare.py  —— 只读诊断

目的: 解释为什么 P22 对 ordinal 299 得到 animation_id=1299, 而 P23 对同样
ordinal 得到 None。两者提取代码本应完全相同 (P23 复用 P22.extract_entry_anim_id
+ P22.P7.ordinal_blocks)。本脚本对【同一个源文件】用与 P22/P23 完全相同的
参数并行走两条提取路径, 逐字节对比 XML, 定位差异。

只读: 不修改任何 package。只输出报告 (stdout)。

默认 source: WW_Nevely42_Animations.package (当前目录或显式传入)
默认 ordinals: 299-306 (重点深查 299)

用法:
  python scripts\\debug_p22_p23_source_compare.py [source] [--ordinals 299-306]
"""
import argparse
import hashlib
import re
import struct
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# 与 P22 / P23 完全相同的导入链
import ww_animation_p22_story_stbl as P22            # extract_entry_anim_id / parse_ordinals / parse_anim_id
import ww_animation_p7_story_chain_audit as P7       # P22 也经此模块
import ww_animation_canary_builder as wb             # safe_parse / read_body_raw / parse_anim_xml
import ww_animation_m2_diff_forensic as _diff        # _locate_animations_list / _entry_blocks

WW_ANIM_XML = 0x7DF2169C
ANIM_ID_FIELD = "animation_id"


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_animation_id_raw(text):
    """独立于 ET 的原始文本检测: n="animation_id" 是否存在于块文本 / raw 值."""
    m = re.search(r'<[A-Za-z_:][^<>]*\bn="' + re.escape(ANIM_ID_FIELD) + r'"[^<>]*>',
                  text, re.S)
    if not m:
        return None, None, None
    tag_open = m.group(0)
    tag = re.match(r'<([A-Za-z_:][^ \t\r\n/>]*)', tag_open).group(1)
    # 取该节点闭合前的文本 (单行 T/E/I 或带 />)
    rest = text[m.end():]
    mm = re.match(r'([^<]*)<', rest, re.S)
    raw = mm.group(1).strip() if mm else ""
    return tag, raw, tag_open


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", nargs="?", default="WW_Nevely42_Animations.package")
    ap.add_argument("--ordinals", default="299-306")
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print("ERROR: 源不存在", src); return 2

    ordinals = P22.parse_ordinals(a.ordinals)
    print("=== SOURCE INFO ===")
    print(f"source = {src}")
    print(f"package sha256: {sha256_of(src)}")

    idx, ierr = wb.safe_parse(str(src))
    if ierr is not None or idx is None:
        print(f"ERROR: 源解析失败 {ierr}"); return 3
    wws = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    if not wws:
        print("ERROR: 包内无 WW_ANIM_XML"); return 3
    we = wws[0]
    print(f"WW_ANIM_XML instance: 0x{we.instance_id:016X}")
    body = wb.read_body_raw(str(src), we)
    print(f"resource size: {len(body)} bytes (uncompressed WW_ANIM_XML body)")
    print(f"ordinals: {ordinals}")
    print("")

    # ---- P22 与 P23 的【共同】extract 函数对象 (确认同一份代码) ----
    extract = P22.extract_entry_anim_id
    print("=== EXTRACT FUNCTION IDENTITY ===")
    print(f"extract_entry_anim_id module = {extract.__module__}")
    print(f"P22.extract_entry_anim_id is P23 所用同一函数: 见模块名 (两者均经 P22)")
    print("")

    # ---- 走共享路径 ordinal_blocks ----
    ww_first, werr = P7.load_xml(str(src))
    if ww_first is None:
        print(f"ERROR: {werr}"); return 3
    blocks, berr = P7.ordinal_blocks(str(src), ww_first, ordinals)
    if berr is not None:
        print(f"ERROR: ordinal_blocks: {berr}"); return 3

    for o in ordinals[:1]:   # 深查首个 ordinal (299); 其余统统计
        print(f"=== ORDINAL {o} COMPARE ===")

        # ---------- P22 路径 ----------
        el_p22 = blocks[o]                       # P22 用同一 blocks
        tag22, raw22, aid22 = extract(el_p22)
        ser_xml = _ser(el_p22)
        xml_sha = hashlib.sha256(ser_xml.encode("utf-8")).hexdigest()
        # 独立原始文本检测
        body = wb.read_body_raw(str(src), ww_first)
        _, xml_text, _xerr = wb.parse_anim_xml(body)
        inner, _istart, _iend = _diff._locate_animations_list(xml_text)
        # 用 _entry_blocks 重建 U 块文本, 定位 ordinal 对应块
        raw_blocks = []
        for b, _r in _diff._entry_blocks(inner):
            if b.lstrip().startswith("<U"):
                raw_blocks.append(b)
        if o < len(raw_blocks):
            rtag, rraw, _r_tagopen = find_animation_id_raw(raw_blocks[o])
        else:
            rtag, rraw = None, None

        print("")
        print("P22路径:")
        print(f"- blocks数量: {len(blocks)}")
        print(f"- ordinal {o} XML长度: {len(ser_xml)}")
        print(f"- ordinal {o} XML sha256: {xml_sha}")
        print(f"- 是否存在 n=\"animation_id\": {tag22 is not None}")
        print(f"- raw value: {raw22!r}  ->  parse_anim_id => {aid22}")
        print("  [独立原始文本扫描] U块数=%d  该块 n=\"animation_id\" tag=%r raw=%r" %
              (len(raw_blocks), rtag, rraw))

        # ---------- P23 路径 ----------
        # P23 调用完全相同的 extract() 与 blocks[o], 即上面 P22 路径; 这里显式分行展示
        el_p23 = blocks[o]
        tag23, raw23, aid23 = P22.extract_entry_anim_id(el_p23)
        # P23 的 reverse text fallback 探针
        probe = f"Caught Cheating {o - 298}"
        print("")
        print("P23路径:")
        print(f"- blocks数量: {len(blocks)}")
        print(f"- ordinal {o} XML长度: {len(_ser(el_p23))}")
        print(f"- ordinal {o} XML sha256: {hashlib.sha256(_ser(el_p23).encode('utf-8')).hexdigest()}")
        print(f"- 是否存在 n=\"animation_id\": {tag23 is not None}")
        print(f"- raw value: {raw23!r}  ->  parse_anim_id => {aid23}")
        print(f"- TEXT-FALLBACK 探针: {probe!r}")

        # ---------- 对比 ----------
        print("")
        print("=== 对比结果 ===")
        same_xml = (_ser(el_p22) == _ser(el_p23))
        print(f"- P22与P23的 blocks[{o}] 是否同一对象: {el_p22 is el_p23}")
        print(f"- XML 是否完全一致: {same_xml}")
        if same_xml:
            print("  => 两者输入完全相同, extract 同一函数。若 P22 得 1299 而 P23 得 None,")
            print("     则差异不可能来自提取代码; 只可能来自【不同的源文件/不同的 blocks 顺序】。")
            print("  继续检查 extract 内部:")
            print(f"    extract_entry_anim_id 输入对象类型: {type(el_p23).__name__}")
            print(f"    ET root tag: {el_p23.tag!r}")
            ns = re.match(r'\{([^}]+)\}', str(el_p23.tag))
            print(f"    ET namespace: {ns.group(1) if ns else '(无, 默认命名空间)'}")
            # 手动复现 extract 的迭代逻辑
            hits = []
            for child in el_p23.iter():
                n = child.get("n")
                if (n or "") == ANIM_ID_FIELD:
                    rawt = (child.text or "").strip()
                    hits.append((child.tag.rsplit('}', 1)[-1], rawt, P22.parse_anim_id(rawt)))
            print(f"    el.iter() 命中 n=\"animation_id\" 的节点数: {len(hits)}  ->  {hits}")
            if not hits:
                # dump 前若干个节点的 n= 值, 看看实际字段名
                names = []
                for child in el_p23.iter():
                    n = child.get("n")
                    if n:
                        names.append(n)
                uniq = sorted(set(names))
                print(f"    该 entry 实际出现的 n= 字段: {uniq}")
                if "animation_id" not in uniq:
                    print("    => 该 entry 内根本没有动画 id 字段 (或字段名不同); extract 返回 None 属正确行为。")
        else:
            print("  => XML 不同! 差异根因见下:")
            # 粗粒度 diff
            ser22 = _ser(el_p22); ser23 = _ser(el_p23)
            if len(ser22) != len(ser23):
                print(f"    长度不同: P22={len(ser22)}  P23={len(ser23)}")
            # 找首个差异位置
            n = min(len(ser22), len(ser23))
            diffpos = next((i for i in range(n) if ser22[i] != ser23[i]), None)
            if diffpos is not None:
                print(f"    首个差异位置 index={diffpos}")
                print(f"    P22: ...{ser22[max(0,diffpos-40):diffpos+60]!r}...")
                print(f"    P23: ...{ser23[max(0,diffpos-40):diffpos+60]!r}...")
        print("")
        print("ZERO_WRITE_TO_MODS=YES (只读, 未修改任何 package)")

    return 0


def _ser(el):
    """把 ET element 序列化回字节(规范文本)用于对比与哈希."""
    import xml.etree.ElementTree as ET
    if hasattr(ET, "tostring"):
        return ET.tostring(el, encoding="unicode")
    return ""


if __name__ == "__main__":
    sys.exit(main())
