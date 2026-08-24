#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P11 Story display source audit (只读) — 定位 Story 真正的显示文本来源

结论背景: P9/P10 已证 animation_raw_display_name 对 Story 不生效
  (新/原 instance 改它都不显示) -> WW_ANIM_XML 字段改法对 Story 失效。
  现在【不再动 WW_ANIM_XML】, 改为读全 package, 找:
  Caught Cheating 的显示文本到底存哪。

目标任务:
  1. 全 package 搜索(大小写不敏感): "Caught Cheating 1"-"9", "Caught Cheating",
     "stage_name", "story"
  2. 命中按资源类型分类:
     - STBL (0x220557DA)
     - tuning/XML 资源
     - binary 资源
     - 引用 WW_ANIM_XML instance 的资源
  3. 对比 Addicted 1-3 (124/125/126, 可正常显示中文)
     -> 找 Addicted 有显示路径而 Caught Cheating 没有的原因

判定 (输出到文件 + 屏幕):
  - 若 "Caught Cheating" 只出现在 WW_ANIM_XML 内部, 且没有任何 STBL/
    tuning/binary 资源承载它 -> Story 显示源不存在于本包 -> WW runtime
    从别处(游戏内置/另一包)取 Story 名, 本地化需另建 STBL 或改另一资源
  - 若 Caught Cheating 出现在某 STBL -> 显示来自 STBL, 改 / 加中文 key
  - 若 Addicted 出现在 STBL 而 Caught Cheating 没有 -> 这就是「Addicted
    能显示 / CC 不能」的关键差异 (定位到缺失的本地化资源)
  - 若两者都只在内 XML -> Story 与 normal 的显示机制本就不同, 得查 runtime

fail-closed: 源缺->2; 解析失败->3; 仅读, 不生成包, 不写 Mods。

用法 (Windows):
  python scripts/ww_animation_p11_story_display_audit.py "<SRC.package>" \
      [--out-dir output/ww_p11]
产物: output/ww_p11/p11_story_display_audit.txt + .csv
"""
import argparse
import importlib.util
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
_spec = importlib.util.spec_from_file_location(
    "ww_canary_builder", SCRIPT_DIR / "ww_animation_canary_builder.py")
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)
import ww_animation_p1_resource_forensic as _p1

WW_ANIM_XML = 0x7DF2169C
STBL = 0x220557DA
OUT_DIR = Path("output/ww_p11")

# 关键搜索词 (小写)
KEY_PHRASES = [
    "caught cheating",
    "caught",                 # 更宽松的 CC 前缀
    "stage_name",
    "animation_stage_name",
    "story",
    "nevely",                 # 作者/前缀
    "clí¾",                   # (占位, 不会误命中)
]
# 逐条目精确名: 由源 WW_ANIM_XML 的 raw_display 提取, 更准
DISP_ALL = {}   # ordinal -> display str


def match_classify(text_lower, disp_lower, ordinal):
    """返回命中的标签列表 (用于各行分类)。"""
    hits = []
    if "caught cheating" in text_lower:
        hits.append("系列名:Caught Cheating")
    if "addicted" in text_lower:
        hits.append("系列名:Addicted")
    if "animation_stage_name" in text_lower or "stage_name" in text_lower:
        hits.append("字段:stage_name")
    if "story" in text_lower:
        hits.append("词:story")
    if "nevely" in text_lower:
        hits.append("作者:nevely")
    if disp_lower and (disp_lower in text_lower or disp_lower.split()[-1] in text_lower):
        hits.append(f"精确名:ordinal-{ordinal}")
    return hits


def ctx_snippet(text, needle, width=45):
    """返回 needle 在 text 中的一段上下文片段 (读得懂的量)。"""
    i = text.find(needle)
    if i < 0:
        return ""
    start = max(0, i - width)
    end = min(len(text), i + len(needle) + width)
    return text[start:end].replace("\n", "⏎")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="源 WW package")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: 源不存在 {src}", file=sys.stderr); return 2
    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    idx, err = wb.safe_parse(src)
    if err is not None or idx is None:
        print(f"ERROR: 源解析失败 {err}", file=sys.stderr); return 3
    entries = idx.entries
    if not any(e.type_id == WW_ANIM_XML for e in entries):
        print("ERROR: 无 WW_ANIM_XML", file=sys.stderr); return 3

    lines = []
    csv_rows = []

    # ---- 从 WW_ANIM_XML 提取 ordinals 的显示名 (Addicted 124-126, CC 299-306) ----
    cc_ord = list(range(299, 307))
    add_ord = [124, 125, 126]
    all_ord = add_ord + cc_ord
    ww = [e for e in entries if e.type_id == WW_ANIM_XML]
    for e in ww:
        body = wb.read_body_raw(src, e)
        _s, xml_txt, xerr = wb.parse_anim_xml(body)
        if xerr is not None:
            continue
        inner, _st, _en = None, None, None
        # 枚举 animations_list 里的 <U> 条目
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(xml_txt)
        except Exception:
            continue
        lst = None
        for node in root.iter():
            tag = node.tag.rsplit('}', 1)[-1] if isinstance(node.tag, str) else None
            if tag == "L" and node.get("n") == "animations_list":
                lst = node; break
        if lst is None:
            continue
        o = 0
        for c in lst:
            if (c.tag.rsplit('}', 1)[-1] if isinstance(c.tag, str) else None) != "U":
                continue
            disp = next((t.text or "" for t in c.iter()
                         if (t.tag.rsplit('}', 1)[-1] if isinstance(t.tag, str) else None) == "T"
                         and t.get("n") == "animation_raw_display_name"), "")
            if o in all_ord:
                DISP_ALL[o] = disp.strip()
            o += 1

    lines.append("========== P11 STORY DISPLAY SOURCE AUDIT ==========")
    lines.append(f"SRC={src}")
    lines.append("目标: 定位 Caught Cheating 显示文本的真正来源; 对比 Addicted 1-3。")
    lines.append("(P9/P10 已证 animation_raw_display_name 对 Story 不生效, 故不再改 WW_ANIM_XML)")
    lines.append("")
    lines.append("=== 目标 ordinals 的显示名 ===")
    for o in all_ord:
        lines.append(f"  ordinal {o:3d}: {DISP_ALL.get(o,'(未取到)')!r}")

    lines.append("")
    lines.append("=== 全 package 资源体全文搜索 (含中文/英文关键串, 大小写不敏感) ===")
    # 命中行: 每资源一行
    hit_rows = []   # (type, inst, kind, labels, snippet)
    disp_lower_map = {o: (DISP_ALL.get(o, "") or "").lower() for o in all_ord}

    for e in entries:
        key = (e.type_id, e.group_id, e.instance_id)
        raw = wb.read_body_raw(src, e)
        body = wb.decompress_maybe(raw)
        kind, text = _p1.decode_body(body)
        tl = text.lower()
        labels = []
        for o in all_ord:
            dl = disp_lower_map.get(o, "")
            if not dl:
                continue
            # 精确名匹配: 整串在文本中, 或至少完整短语 (避免单数字"1"误命中)
            if dl in tl or (" " in dl and dl in tl):
                labels.append(f"精确名#{o}")
        if "caught cheating" in tl:
            labels.append("CC系列")
        if "addicted" in tl:
            labels.append("Addicted系列")
        if "stage_name" in tl or "animation_stage_name" in tl:
            labels.append("stage_name字段")
        if "story" in tl:
            labels.append("story词")
        if "nevely" in tl:
            labels.append("作者nevely")
        # WW_ANIM_XML 引用
        if "0x" in tl and any(f"{t:016x}" in tl or f"{t:x}" in tl for t in
                              {e.instance_id for e in entries if e.type_id == WW_ANIM_XML}):
            labels.append("引用WW_XML_instance")
        if labels:
            tname = _p1.KNOWN_TYPES.get(e.type_id, f"0x{e.type_id:08X}")
            snip = ctx_snippet(tl, labels[0].split()[0] if labels else "", 45)
            hit_rows.append((e.type_id, key[2], kind, tname, sorted(set(labels)), snip))

    # 分类聚合
    by_kind = {"STBL": [], "tuningXML": [], "binary": [], "ascii": [], "WW_XML_ref": []}
    for (tid, iid, kind, tname, labels, snip) in hit_rows:
        cat = None
        if tid == STBL:
            cat = "STBL"
        elif tid == WW_ANIM_XML:
            cat = "WW_XML_ref"
        elif kind == "xml":
            cat = "tuningXML"
        elif kind in ("binary",):
            cat = "binary"
        else:
            cat = "ascii"
        if cat in by_kind:
            by_kind[cat].append((tid, iid, kind, tname, labels, snip))

    # 输出
    if not hit_rows:
        lines.append("  (无任何资源命中关键串 —— 显示文本不在本 package 内!)")
    for cat in ["STBL", "tuningXML", "binary", "WW_XML_ref", "ascii"]:
        rows = by_kind[cat]
        lines.append(f"\n  --- [{cat}] 命中 {len(rows)} 个资源 ---")
        for (tid, iid, kind, tname, labels, snip) in rows:
            lines.append(f"    {tname:26s} {_p1.hexI(iid):18s} kind={kind:8s} 标签={labels}")
            if snip:
                lines.append(f"        片段: {snip[:100]}")
            csv_rows.append([cat, _p1.hexI(tid), _p1.hexI(iid), kind, "|".join(labels), snip[:200]])

    lines.append("")
    lines.append("=== STBL 资源明细 (若存在, 显示文本候选) ===")
    stbl_found = [e for e in entries if e.type_id == STBL]
    if not stbl_found:
        lines.append("  (本 package 无 STBL 资源 —— Story 显示名不来自本包 STBL)")
    for e in stbl_found:
        raw = wb.read_body_raw(src, e)
        body = wb.decompress_maybe(raw)
        kind, text = _p1.decode_body(body)
        lines.append(f"  STBL {_p1.hexI(e.instance_id)} size={len(body)} kind={kind}")
        # 扫可读片段 (STBL 二进制里的字符串)
        for m in re.finditer(rb'[\x20-\x7e]{4,}', body):
            frag = m.group().decode("ascii", "replace")
            fl = frag.lower()
            if any(w in fl for w in ("caught", "addict", "cheat", "story", "stage_name", "nevely")):
                lines.append(f"      STR: {frag[:120]}")

    lines.append("")
    lines.append("=== Addicted(124-126, 可显示) vs Caught Cheating(299-306, 失败) 显示路径对比 ===")
    cc_outside = False
    add_outside = False
    cc_stbl = False
    add_stbl = False
    for (tid, iid, kind, tname, labels, snip) in hit_rows:
        if tid == WW_ANIM_XML:
            continue
        has_cc = any(("精确名" in l and any(f"#{o}" in l for o in cc_ord)) or "CC系列" in l for l in labels)
        has_add = any(("精确名" in l and any(f"#{o}" in l for o in add_ord)) or "Addicted系列" in l for l in labels)
        if has_cc:
            cc_outside = True
            if tid == STBL:
                cc_stbl = True
        if has_add:
            add_outside = True
            if tid == STBL:
                add_stbl = True

    lines.append(f"  Caught Cheating 命中非 WW 资源: {cc_outside}   (其中 STBL: {cc_stbl})")
    lines.append(f"  Addicted       命中非 WW 资源: {add_outside}   (其中 STBL: {add_stbl})")
    lines.append("")
    if cc_outside and not cc_stbl and add_stbl:
        lines.append("  >>> 判定:CC 系列在外资源但【非 STBL】; Addicted 命中 STBL")
        lines.append("      -> Addicted 有 STBL 显示路径而 CC 没有 -> Story 缺本地化 STBL")
    elif cc_stbl:
        lines.append("  >>> 判定:CC 系列名出现在 STBL ")
        lines.append("      -> Story 显示来自 STBL, 改/加该 STBL 的中文 key 即可")
    elif not cc_outside and add_outside:
        lines.append("  >>> 判定:CC 系列名【完全没有】非 WW 资源承载; Addicted 有")
        lines.append("      -> CC 的显示文本不在本包 -> 需查另一资源/游戏内置, 或补 STBL")
    elif not cc_outside and not add_outside:
        lines.append("  >>> 判定:CC 与 Addicted 都只在 WW_ANIM_XML 内, 无外部显示源")
        lines.append("      -> 两者显示机制在本包内本就无文本; 差异在 runtime 读取处")
    else:
        lines.append("  >>> 判定:边界情况, 需人工看上面命中表")

    txt = "\n".join(lines) + "\n"
    (out_dir / "p11_story_display_audit.txt").write_text(txt, encoding="utf-8")
    import csv
    with open(out_dir / "p11_story_display_audit.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["category", "type", "instance", "kind", "labels", "snippet"])
        for r in csv_rows:
            w.writerow(r)

    print(txt)
    print(f"OUT_TXT={out_dir / 'p11_story_display_audit.txt'}")
    print(f"OUT_CSV={out_dir / 'p11_story_display_audit.csv'}")
    print("P11_STORY_DISPLAY_AUDIT=OK (只读, 未生成包, 未动 Mods)")
    print("ZERO_WRITE_TO_MODS=YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
