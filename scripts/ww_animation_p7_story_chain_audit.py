#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P7 只读审计 — Story chain / hidden reference: WW_ANIM_XML 内隐藏字段与 TGI 引用

背景: P4 registry 外部引用无证据; P6 CLIP/ANIM_RCOL 链完整 (124/299 均正常)。
  → 排除资源缺失。剩最大嫌疑: 复制 WW_ANIM_XML instance 后, 普通动画可用但
    STORY runtime 不注册 —— 因为 Story 动画在 XML 条目内还带着【隐藏字段或
    TGI 引用】, 当前解析器/复制逻辑没识别, 于是克隆出的故事实例缺注册键。

P7 目标 (纯只读, 只写 audit 文件):
  对 Addicted 124-126 (正常) vs Caught Cheating 299-306 (story 失败), 全量枚举
  WW_ANIM_XML 条目内每个节点, 侧重隐藏字段与引用:
    - stage / chain / story / parent / next / id / guid / instance 关键字字段
    - 任意 TGI 形态引用 (0x 十六进制 instance / type-group-instance / GUID / hash)
    - 未被当前解析器识别的节点 (无 n= 属性 / 被 _collect_fields 跳过的)
  对比 124-126 vs 299-306:
    - Story 独有的字段/引用 (正常条目没有的)
    - 字段取值形态差异 (同名字段 Story vs normal 值不同)
  目的: 找出为何复制 XML instance 后普通动画可用但 STORY 不注册。

fail-closed: 只读; 源缺/无 WW_ANIM_XML -> exit3; ordinal 越界 -> exit4;
  解析 entry 失败 -> exit5。ZERO_WRITE_TO_MODS=YES。
用法 (Windows):
  python scripts/ww_animation_p7_story_chain_audit.py "<SRC.package>" --out-dir output/ww_p7
产物: output/ww_p7/p7_story_chain_audit.txt + .csv
"""
import argparse
import csv
import importlib.util
import json
import re
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

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
OUT_DIR = Path("output/ww_p7")

# Story/hidden 关键字 (命中即高亮)
KEYWORDS = ("stage", "chain", "story", "parent", "next", "id", "guid",
            "instance", "hash", "key", "ref", "link", "trigger", "event")
# TGI / GUID / hash 形态
TGI16 = re.compile(r"0x[0-9A-Fa-f]{16}")
TGI8 = re.compile(r"0x[0-9A-Fa-f]{8}")
HEX_U64 = re.compile(r"\b[0-9A-Fa-f]{16}\b")
GUID = re.compile(r"\b[0-9A-Fa-f]{8}[-][0-9A-Fa-f]{4}[-][0-9A-Fa-f]{4}[-][0-9A-Fa-f]{4}[-][0-9A-Fa-f]{12}\b")
TGI_TRIPLE = re.compile(r"\b(?:type\s*[:=]\s*)?0x[0-9A-Fa-f]{8}\s*[,;/\s]+\s*0x[0-9A-Fa-f]{16}\b", re.I)
TGI_TRIPLE_SHORT = re.compile(r"\b0x[0-9A-Fa-f]{8}\s+0x[0-9A-Fa-f]{8}\s+0x[0-9A-Fa-f]{16}\b", re.I)


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else None


def load_xml(src):
    idx, err = wb.safe_parse(src)
    if err is not None or idx is None:
        return None, f"源解析失败: {err}"
    wws = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    if not wws:
        return None, "包内无 WW_ANIM_XML"
    return wws[0], None


def ordinal_blocks(src, ww_first, ordinals):
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
    out = {}
    for o in ordinals:
        if o not in om:
            return None, f"ordinal {o} 越界({len(om)})"
        try:
            out[o] = ET.fromstring(om[o])
        except Exception as ex:
            return None, f"entry {o} 解析失败: {ex}"
    return out, None


def node_dump(root, ser=None):
    """递归 dump 整棵 entry 树为扁平节点序列 (跳过根 U 自身)。"""
    rows = []

    def walk(el, depth):
        for child in el:
            lt = _local(child.tag)
            n = child.get("n")
            kids = list(child)
            if lt in ("T", "E", "I"):
                val = (child.text or "").strip()
            elif lt == "L":
                val = []
                for k in kids:
                    if _local(k.tag) in ("T", "E", "I"):
                        val.append((k.text or "").strip())
                    else:
                        val.append(f"<{_local(k.tag)}>{k.get('n') or ''}")
                val = "L[" + ",".join(str(x) for x in val) + "]"
            elif lt == "U":
                val = "[object]"
            else:
                val = (child.text or "").strip()
            rows.append({
                "tag": lt, "n": n or "", "val": val,
                "children": len(kids), "depth": depth,
                "kw": 1 if (n and any(k in n.lower() for k in KEYWORDS)) else 0,
            })
            walk(child, depth + 1)

    walk(root, 0)
    return rows


def find_tgis(text):
    """在文本里找 TGI/GUID/hash 形态。"""
    if not text:
        return []
    found = set()
    for rx in (GUID, TGI16, TGI_TRIPLE, TGI_TRIPLE_SHORT, TGI8, HEX_U64):
        for m in rx.finditer(text):
            found.add(m.group(0).strip().rstrip(",;"))
    return sorted(found)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--ok", nargs="*", type=int, default=[124, 125, 126],
                    help="正常条目 (Addicted)")
    ap.add_argument("--fail", nargs="*", type=int, default=[299, 300, 301, 302, 303, 304, 305, 306],
                    help="Story 失败条目 (Caught Cheating)")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: 源不存在 {src}", file=sys.stderr); return 2
    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    ww_first, err = load_xml(src)
    if ww_first is None:
        print(f"ERROR: {err}", file=sys.stderr); return 3

    all_ords = list(dict.fromkeys(a.ok + a.fail))
    blocks, err = ordinal_blocks(src, ww_first, all_ords)
    if blocks is None:
        print(f"ERROR: {err}", file=sys.stderr); return 4

    # ---- 逐 ordinal 全量 dump ----
    dumps = {o: node_dump(root) for o, root in blocks.items()}
    # 字段名聚合 (含无名节点用 tag#")
    field_keys = {}
    for o in all_ords:
        for r in dumps[o]:
            k = f"{r['tag']}#{r['n']}"
            field_keys.setdefault(k, []).append(o)

    # ---- Story 特有字段: 只在 fail 出现 (或 fail 与 ok 形态不同) ----
    ok_set = set(a.ok)
    fail_set = set(a.fail)
    story_only = []
    same_name_diff_shape = []
    for k, present_in in field_keys.items():
        pset = set(present_in)
        tag, name = k.split("#", 1)
        if pset & fail_set and not (pset & ok_set):
            story_only.append(k)
        elif pset & fail_set and pset & ok_set:
            ok_vals = {r["val"] for o in a.ok for r in dumps[o] if f"{r['tag']}#{r['n']}" == k}
            fail_vals = {r["val"] for o in a.fail for r in dumps[o] if f"{r['tag']}#{r['n']}" == k}
            if ok_vals != fail_vals:
                same_name_diff_shape.append((k, sorted(ok_vals), sorted(fail_vals)))

    # ---- 组装报告 ----
    L = []
    L.append("=== P7 Story chain / hidden reference 审计 (只读) ===")
    L.append(f"WW_ANIM_XML instance = 0x{ww_first.instance_id:016X}")
    L.append(f"正常(Addicted)= {a.ok}")
    L.append(f"Story(Caught Cheating)= {a.fail}")
    L.append("")

    # 1) 每个 ordinal 全量字段 + TGI 引用
    for o in all_ords:
        L.append(f"ORDINAL {o}  ({'STORY' if o in fail_set else 'NORMAL'})")
        rows = dumps[o]
        L.append(f"  节点总数 = {len(rows)}")
        kw_nodes = [r for r in rows if r['kw']]
        if kw_nodes:
            L.append("  含 Story/hidden 关键字字段:")
            for r in kw_nodes:
                v = (r['val'][:80] + "…") if len(str(r['val'])) > 80 else r['val']
                L.append(f"    {r['tag']} n={r['n']!r} = {v!r}")
        tgis_here = {}
        for r in rows:
            for t in find_tgis(str(r['val'])):
                tgis_here.setdefault(t, []).append(r['n'] or r['tag'])
        if tgis_here:
            L.append("  检测到 TGI/GUID/hash 引用:")
            for t, where in sorted(tgis_here.items()):
                L.append(f"    {t}  <- {sorted(set(where))}")
        else:
            L.append("  (未检测到 TGI/GUID/hash 引用)")
        L.append("")

    # 2) Story 特有字段
    L.append("=== Story 特有 / 仅 fail 出现的字段 ===")
    if story_only:
        for k in sorted(story_only):
            tag, name = k.split("#", 1)
            present = sorted(set(field_keys[k]))
            vals = sorted({r["val"] for o in present for r in dumps[o] if f"{r['tag']}#{r['n']}" == k})
            L.append(f"  {tag} n={name!r} 出现在 {present} 值= {vals[:8]}")
    else:
        L.append("  (无 fail 独有字段)")
    L.append("")

    # 3) 同名字段值形态差异
    L.append("=== 同名字段 Story vs Normal 取值差异 ===")
    if same_name_diff_shape:
        for k, ok_vals, fail_vals in sorted(same_name_diff_shape):
            L.append(f"  {k}:")
            L.append(f"    NORMAL: {ok_vals[:6]}")
            L.append(f"    STORY : {fail_vals[:6]}")
    else:
        L.append("  (无同名字段值差异)")
    L.append("")

    # 4) 结论
    L.append("=== 结论 ===")
    if story_only:
        L.append("发现 fail(Story) 独有字段/引用 -> 极可能是 STORY runtime 注册所需, "
                 "M2/M3/M4 克隆 XML 时未复制/改写这些隐藏引用, 导致故事实例不注册。")
    elif same_name_diff_shape:
        L.append("无 fail 独有字段, 但同名字段取值形态 Story vs Normal 不同 -> "
                 "注册键可能是取值形态 (如某字段 Story 指向一个外部 TGI, Normal 为空/0)。")
    else:
        L.append("124-126 vs 299-306 在字段集合与取值形态上无差异 -> Story 不注册的键"
                 "不在 WW_ANIM_XML 条目内部; 需查 XML 之外的机制 (WW runtime 全局 story 表)。")
    L.append("")

    txt = "\n".join(L)
    txt_path = out_dir / "p7_story_chain_audit.txt"
    txt_path.write_text(txt, encoding="utf-8")

    # CSV: 每 ordinal 每节点一行
    csv_path = out_dir / "p7_story_chain_audit.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ordinal", "series", "tag", "n", "value", "children", "depth",
                    "keyword_hit", "tgis"])
        for o in all_ords:
            for r in dumps[o]:
                w.writerow([o, "STORY" if o in fail_set else "NORMAL",
                            r["tag"], r["n"], r["val"], r["children"], r["depth"],
                            r["kw"], ";".join(find_tgis(str(r["val"])))])
    # CSV: Story-only 字段
    csv2_path = out_dir / "p7_story_only_fields.csv"
    with open(csv2_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["field", "tag", "present_in", "values"])
        for k in sorted(story_only):
            tag, name = k.split("#", 1)
            present = sorted(set(field_keys[k]))
            vals = sorted({r["val"] for o in present for r in dumps[o] if f"{r['tag']}#{r['n']}" == k})
            w.writerow([name, tag, ";".join(map(str, present)), "|".join(vals[:12])])

    print(txt)
    print(f"OUT_TXT={txt_path}")
    print(f"OUT_CSV={csv_path}")
    print(f"OUT_STORY_ONLY={csv2_path}")
    print("P7_STORY_CHAIN_AUDIT=OK (只读, 未生成包, 未动 Mods)")
    print("ZERO_WRITE_TO_MODS=YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
