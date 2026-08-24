#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P6 只读审计 — WW 动画资源链完整性：WW_ANIM_XML -> animation_clip_name
                                              -> CLIP resource -> ANIM_RCOL

背景: P4 真机显示【无非 WW 资源引用 WW_ANIM_XML instance】, registry 路线
  无证据。新最大嫌疑: 动画资源链没被完整复制 —— M4 只改了 WW_ANIM_XML 里
  animation_clip_name 为新名 (xxx_zh), 但这个新 clip 名在包里【没有任何
  CLIP / ANIM_RCOL 资源对应】, 于是 runtime 找不到动画 -> NOT Caught Cheating。

P6 目标 (纯只读, 只写 audit 文件):
  对每个目标 ordinal (成功 124 Addicted vs 失败 299 Caught Cheating 1,
  可选 306 Caught Cheating 8):
    1) 从 WW_ANIM_XML 提取 animation_clip_name 列表
    2) 全包搜索每个 clip 名文本出现在哪些资源里 (按类型归类)
    3) 对真正持有该 clip 名的 CLIP / ANIM_RCOL 资源, dump type/group/
       instance/size/compression 元数据
    4) 从 CLIP 二进制正文扫描内嵌 ANIM_RCOL TGI (type 0xBC4A5044 出现处
       的 type/group/instance 16 字节), 追踪 CLIP->ANIM_RCOL 引用
  重点比较 124 vs 299:
    - clip 数量差异
    - clip 名是否映射到真实 CLIP/ANIM_RCOL (还是只存在于 XML)
    - 是否存在 Story 特有资源绑定
    - 是否 M4 只复制 XML 导致资源链断裂 (新 clip 名无 CLIP/ANIM_RCOL)
  结论:
    - 若失败 ordinal 的 clip 名缺 CLIP/ANIM_RCOL -> 下一步制作
      CLIP+ANIM_RCOL clone 实验
    - 若链完整 -> 继续找 runtime 其他注册机制

fail-closed: 全程只读; 源包解析失败/无 WW_ANIM_XML -> exit 3;
  目标 ordinal 越界 -> exit 4。ZERO_WRITE_TO_MODS=YES。
用法 (Windows):
  python scripts/ww_animation_p6_clip_rcol_audit.py "<SRC.package>" --out-dir output/ww_p6
  可选: --ordinals 124 299 306
产物: output/ww_p6/p6_clip_rcol_audit.txt + .csv
"""
import argparse
import csv
import importlib.util
import re
import struct
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
_spec = importlib.util.spec_from_file_location(
    "ww_canary_builder", SCRIPT_DIR / "ww_animation_canary_builder.py")
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)
import ww_animation_m2_diff_forensic as _diff
import ww_animation_p1_resource_forensic as _p1

WW_ANIM_XML = 0x7DF2169C
CLIP = 0x6B20C4F3
ANIM_RCOL = 0xBC4A5044
OUT_DIR = Path("output/ww_p6")


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else None


def extract_pkg(src):
    idx, err = wb.safe_parse(src)
    if err is not None or idx is None:
        return None, f"源解析失败: {err}"
    entries = idx.entries
    wws = [e for e in entries if e.type_id == WW_ANIM_XML]
    if not wws:
        return None, "包内无 WW_ANIM_XML"
    return entries, wws[0]


def ord_clip_names(src, ww_first, ordinal):
    """返回 ordinal 的 animation_clip_name 列表。"""
    body = wb.read_body_raw(src, ww_first)
    _s, xml_text, xerr = wb.parse_anim_xml(body)
    if xerr is not None:
        return None, xerr
    inner, _st, _en = _diff._locate_animations_list(xml_text)
    om = {}
    ei = 0
    for b, _r in _diff._entry_blocks(inner):
        if b.lstrip().startswith("<U"):
            om[ei] = b
            ei += 1
    if ordinal not in om:
        return None, f"ordinal {ordinal} 越界({len(om)})"
    block = om[ordinal]
    clips = []
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(block)
        # animation_clip_name 是 <L n="animation_clip_name"><T>..</T>*</L> 列表容器
        for el in root.iter():
            if _local(el.tag) == "L" and el.get("n") == "animation_clip_name":
                for sub in el.iter():
                    if _local(sub.tag) == "T":
                        clips.append((sub.text or "").strip())
                    elif _local(sub.tag) == "U":
                        # 罕见: L 内嵌 U 条目
                        for t in sub.iter():
                            if _local(t.tag) == "T" and t.get("n"):
                                clips.append((t.text or "").strip())
        if not clips:
            # 兼容直接 <T n="animation_clip_name"> 单值形式
            for el in root.iter():
                if _local(el.tag) == "T" and el.get("n") == "animation_clip_name":
                    clips.append((el.text or "").strip())
    except Exception as ex:
        return None, f"clip 解析失败: {ex}"
    return clips, None


def clip_name_hits(src, entries, clip_names):
    """全包搜索每个 clip 名文本出现在哪个资源(解码正文), 按类型归类。
    返回 {clip_name: [(type, group, inst, kind)]}, 及每个资源存放的 clip。
    """
    per_clip = {c: [] for c in clip_names}
    res_hits = {}  # key -> set(clip)
    for e in entries:
        key = (e.type_id, e.group_id, e.instance_id)
        raw = wb.read_body_raw(src, e)
        body = wb.decompress_maybe(raw)
        kind, text = _p1.decode_body(body)
        lower = text.lower()
        for c in clip_names:
            if c.lower() in lower:
                per_clip[c].append((e.type_id, e.group_id, e.instance_id, kind))
                res_hits.setdefault(key, set()).add(c)
    return per_clip, res_hits


def scan_rcol_refs(body):
    """在字节正文里扫描内嵌 ANIM_RCOL TGI。
    标准 Sims4 resource key = type(u32) + group(u32) + instance(u64) 16 字节;
    ANIM_RCOL 出现处即 CLIP 对 clip-rcol 的引用。返回 [(type, group, inst)]。
    """
    refs = []
    tgt = struct.pack("<I", ANIM_RCOL)
    pos = 0
    while True:
        i = body.find(tgt, pos)
        if i < 0:
            break
        # type 在 i; group 在 i+4; instance 在 i+8 (8 字节)
        if i + 16 <= len(body):
            _t = struct.unpack("<I", body[i:i + 4])[0]
            g = struct.unpack("<I", body[i + 4:i + 8])[0]
            inst = struct.unpack("<Q", body[i + 8:i + 16])[0]
            refs.append((_t, g, inst))
        pos = i + 4
    return refs


def type_name(t):
    return _p1.KNOWN_TYPES.get(t, f"0x{t:08X}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--ordinals", nargs="*", type=int, default=[124, 299, 306])
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: 源不存在 {src}", file=sys.stderr); return 2
    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    entries, ww_first = extract_pkg(src)
    if entries is None:
        print(f"ERROR: {ww_first}", file=sys.stderr); return 3

    # ---- 收集所有目标 ordinal 的 clip 名 + 元数据 ----
    ord_info = {}
    all_clips = []
    for o in a.ordinals:
        clips, err = ord_clip_names(src, ww_first, o)
        if err is not None:
            print(f"ERROR: {err}", file=sys.stderr); return 4
        ord_info[o] = {"clips": clips, "block_size": 0}
        all_clips += clips

    # ---- 全包 clip 名定位 + CLIP/ANIM_RCOL 目录 ----
    per_clip, res_hits = clip_name_hits(src, entries, all_clips)
    clip_res = [e for e in entries if e.type_id == CLIP]
    rcol_res = [e for e in entries if e.type_id == ANIM_RCOL]
    # 每个 CLIP 资源: 扫描内嵌 ANIM_RCOL 引用
    clip_rcol_refs = {}
    for e in clip_res:
        raw = wb.read_body_raw(src, e)
        body = wb.decompress_maybe(raw)
        refs = scan_rcol_refs(body)
        if refs:
            clip_rcol_refs[(e.type_id, e.group_id, e.instance_id)] = refs

    # ---- 每个 ordinal 元数据 (CLIP/ANIM_RCOL 是否持有它的 clip 名) ----
    # 预解码一次避免重复
    _decoded_cache = {}
    def dec(entry):
        key = (entry.type_id, entry.group_id, entry.instance_id)
        if key not in _decoded_cache:
            body = wb.decompress_maybe(wb.read_body_raw(src, entry))
            _decoded_cache[key] = _p1.decode_body(body)
        return _decoded_cache[key]

    def carrying_of_type(otype, clip_list):
        found = []
        for e in entries:
            if e.type_id != otype:
                continue
            _k, txt = dec(e)
            low = txt.lower()
            if any(c.lower() in low for c in clip_list):
                found.append(e)
        return found

    # ---- 组装报告 ----
    L = []
    L.append("=== P6 WW 动画资源链完整性审计 (只读) ===")
    L.append(f"WW_ANIM_XML instance = 0x{ww_first.instance_id:016X}")
    L.append(f"包内 CLIP 资源数 = {len(clip_res)}   ANIM_RCOL 资源数 = {len(rcol_res)}")
    L.append(f"带内嵌 ANIM_RCOL 引用的 CLIP 数 = {len(clip_rcol_refs)}")
    L.append("")

    csv_rows = []
    for o in a.ordinals:
        clips = ord_info[o]["clips"]
        L.append(f"ORDINAL {o}")
        L.append("  animation_clip_name:")
        for c in clips:
            L.append(f"    - {c}")
        # 1) clip 名在哪些资源里出现
        L.append("  clip 名定位 (含该文本的资源):")
        for c in clips:
            hits = per_clip.get(c, [])
            if not hits:
                L.append(f"    '{c}' -> 无任何资源承载 (只存在于 XML 或不存在)")
            else:
                for (t, g, i, kind) in hits:
                    L.append(f"    '{c}' -> type=0x{t:08X}({type_name(t)}) "
                             f"g=0x{g:016X} 0x{i:016X} kind={kind}")
        # 2) 持有这些 clip 名的 CLIP 资源
        L.append("  持有这些 clip 名的 CLIP 资源:")
        cc = carrying_of_type(CLIP, clips)
        for e in cc:
            raw = wb.read_body_raw(src, e)
            body = wb.decompress_maybe(raw)
            comp = "zlib/压缩" if wb._is_zlib(raw) else "plaintext/未压缩"
            L.append(f"    CLIP type=0x{CLIP:08X} g=0x{e.group_id:016X} 0x{e.instance_id:016X} "
                     f"size={len(body)}B comp={comp}")
            refs = clip_rcol_refs.get((e.type_id, e.group_id, e.instance_id), [])
            for (rt, rg, ri) in refs:
                L.append(f"      -> ANIM_RCOL 引用 type=0x{rt:08X} g=0x{rg:016X} 0x{ri:016X}")
            csv_rows.append([o, "clip", f"0x{e.type_id:08X}", f"0x{e.instance_id:016X}",
                             "STORY" if o >= 299 else "NORMAL", len(body), "yes" if refs else "no"])
        if not cc:
            L.append("    (无 CLIP 资源承载该 ordinal 的 clip 名)")
        # 3) 持有这些 clip 名的 ANIM_RCOL 资源
        L.append("  持有这些 clip 名的 ANIM_RCOL 资源:")
        ar = carrying_of_type(ANIM_RCOL, clips)
        for e in ar:
            raw = wb.read_body_raw(src, e)
            body = wb.decompress_maybe(raw)
            comp = "zlib/压缩" if wb._is_zlib(raw) else "plaintext/未压缩"
            L.append(f"    ANIM_RCOL type=0x{e.type_id:08X} g=0x{e.group_id:016X} 0x{e.instance_id:016X} "
                     f"size={len(body)}B comp={comp}")
            csv_rows.append([o, "rcol", f"0x{e.type_id:08X}", f"0x{e.instance_id:016X}",
                             "STORY" if o >= 299 else "NORMAL", len(body), "n/a"])
        if not ar:
            L.append("    (无 ANIM_RCOL 资源承载该 ordinal 的 clip 名)")
        # 4) Story 特有绑定
        refs_total = sum(len(v) for k, v in clip_rcol_refs.items() if any(
            c.lower() in dec(next(e for e in entries if (e.type_id, e.group_id, e.instance_id) == k))[1].lower()
            for c in clips))
        L.append(f"  该 ordinal 的 clip 名 -> CLIP 内嵌 ANIM_RCOL 引用数: {refs_total if cc else 0}")
        L.append("")
        for c in clips:
            csv_rows.append([o, "name", c, "-",
                             "STORY" if o >= 299 else "NORMAL", len(c),
                             "yes" if per_clip.get(c) else "no"])

    # ---- 124 vs 299 差异比较 ----
    if 124 in ord_info and 299 in ord_info:
        L.append("=== 差异比较: 124 Addicted(成功) vs 299 Caught Cheating(失败) ===")
        c124 = ord_info[124]["clips"]
        c299 = ord_info[299]["clips"]
        L.append(f"  clip 数量: 124={len(c124)}  299={len(c299)}  差异={len(c124)-len(c299)}")
        miss124 = [c for c in c124 if not per_clip.get(c)]
        miss299 = [c for c in c299 if not per_clip.get(c)]
        L.append(f"  124 无资源承载的 clip: {miss124 if miss124 else '(全部有资源)'}")
        L.append(f"  299 无资源承载的 clip: {miss299 if miss299 else '(全部有资源)'}")
        cc124 = carrying_of_type(CLIP, c124)
        cc299 = carrying_of_type(CLIP, c299)
        L.append(f"  124 持有 clip 名的 CLIP 资源数: {len(cc124)}")
        L.append(f"  299 持有 clip 名的 CLIP 资源数: {len(cc299)}")
        ar124 = carrying_of_type(ANIM_RCOL, c124)
        ar299 = carrying_of_type(ANIM_RCOL, c299)
        L.append(f"  124 持有 clip 名的 ANIM_RCOL 资源数: {len(ar124)}")
        L.append(f"  299 持有 clip 名的 ANIM_RCOL 资源数: {len(ar299)}")
        # 关键判断: 相较成功案例 124, 299 是否缺 ANIM_RCOL/CLIP 承载
        if cc124 and cc299 and ar124 and not ar299:
            verdict = ("299 资源链【断裂】: 与 124 相比, 299 的 clip 名有 CLIP 但【无 ANIM_RCOL】承载, "
                       "且其 CLIP 无内嵌 ANIM_RCOL 引用 -> 支持 M4 只复制 XML 致链在 ANIM_RCOL 层断裂, "
                       "下一步做 CLIP+ANIM_RCOL clone 实验")
        elif (not cc124 and not cc299):
            verdict = ("两者都无 CLIP 承载 clip 名 -> 链在 CLIP 层对两系列都缺失? "
                       "需确认成功案例 124 的 clip 名如何被 runtime 找到")
        elif (cc299 and ar299):
            verdict = "299 资源链【完整】: clip 名有 CLIP+ANIM_RCOL -> 继续找其他注册机制"
        elif (ar124 and not ar299):
            verdict = ("299 资源链【断裂】: 124 有 ANIM_RCOL 承载 clip 名而 299 无 "
                       "-> 支持 M4 只复制 XML 致 ANIM_RCOL 层断裂, 做 CLIP+ANIM_RCOL clone 实验")
        else:
            verdict = "299 资源链【部分缺失】: 需人工比对 124 vs 299 的 CLIP/ANIM_RCOL 覆盖差异"
        L.append("")
        L.append(f"  判定: {verdict}")
        L.append("")

    # ---- 报告落盘 ----
    txt_path = out_dir / "p6_clip_rcol_audit.txt"
    txt_path.write_text("\n".join(L), encoding="utf-8")
    csv_path = out_dir / "p6_clip_rcol_audit.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ordinal", "kind", "clip_or_tgi", "res_instance", "series", "size", "has_rcol_ref"])
        w.writerows(csv_rows)

    print("\n".join(L))
    print(f"OUT_TXT={txt_path}")
    print(f"OUT_CSV={csv_path}")
    print("P6_CLIP_RCOL_AUDIT=OK (只读, 未生成包, 未动 Mods)")
    print("ZERO_WRITE_TO_MODS=YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
