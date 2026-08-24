#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P3 取证 — actor graph / role identity 审计 (只读)。

目标: 确认 WW Story runtime 是否依赖 actor graph / role identity 判定
  Story animation (而不只是条目入口)。dump ordinal 124/299/306 的真实
  actor 图结构:
    候选字段(用户指定):
      animation_actors_list
      actor_id
      actor_interactions
      animation_actor_tags
      receiving_actor_id
      receiving_actor_category
  因真机 census 显示 actor 列表实际字段名可能是 "actors"(非
  animation_actors_list), 本工具【递归】dump 每个 actor <U> 对象的
  【全部命名子字段】及其真实值 (T/E/I/L/U 形态), 确保不遗漏
  actor_interactions / animation_actor_tags / receiving_* 等深层字段。
  并输出 124 vs 299 的 actor 图差异表。

只读。不生成 package / 不改 writer / 不碰 Mods (ZERO_WRITE_TO_MODS=YES)。
用法 (Windows, 只读源):
  python scripts/ww_animation_p3_actor_audit.py "<SRC.package>" \
      [--ordinals 124 299 306] [--out-dir output/ww_p3]
"""
import argparse
import csv
import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
_spec = importlib.util.spec_from_file_location(
    "ww_canary_builder", SCRIPT_DIR / "ww_animation_canary_builder.py")
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)
import ww_animation_m2_diff_forensic as _diff

WW_ANIM_XML = 0x7DF2169C
OUT_DIR = Path("output/ww_p3")

CANDIDATE_FIELDS = [
    "animation_actors_list",
    "actor_id",
    "actor_interactions",
    "animation_actor_tags",
    "receiving_actor_id",
    "receiving_actor_category",
]
# 真实 census 已知 actor 列表字段名 (二选一命中)
ACTOR_LIST_NAMES = ["animation_actors_list", "actors", "animation_actor_list"]


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else None


def _leaf_val(el):
    lt = _local(el.tag)
    if lt in ("T", "E", "I"):
        return (el.text or "").strip()
    if lt == "L":
        return "[L]"
    if lt == "U":
        return "[U]"
    return (el.text or "").strip()


def _dump_el(el, depth):
    """递归 dump 一个 XML 元素: 返回 (depth, n, tag, val, kids)。"""
    lt = _local(el.tag)
    n = el.get("n")
    if lt == "U":
        kids = []
        for c in el:
            kids.extend(_dump_el(c, depth + 1))
        return [(depth, n or "", "U", "[OBJECT]", kids)]
    if lt == "L":
        kids = []
        for c in el:
            kids.extend(_dump_el(c, depth + 1))
        return [(depth, n or "", "L", f"[{len(list(el))} items]", kids)]
    return [(depth, n or "", lt, _leaf_val(el), None)]


def _find_actor_list(root):
    """找 actor 列表节点 (L), 返回 (list_node, actual_field_name) 或 (None, name)。"""
    for name in ACTOR_LIST_NAMES:
        for el in root.iter():
            if el.get("n") == name:
                return el, name
    # 兜底: 任何含 actor_id 子节点的 L
    for el in root.iter():
        if _local(el.tag) == "L":
            for c in el:
                if _local(c.tag) == "U":
                    for gc in c:
                        if gc.get("n") == "actor_id":
                            return el, (el.get("n") or "?")
    return None, None


def _strip_none_rec(rows):
    """去掉纯容器(无 T/E/I 叶子)的空 U/L, 保留结构。rows 已是 [(indent,n,tag,val,kids),...] 扁平列表"""
    return rows


def _collect_ordinals(src):
    idx, err = wb.safe_parse(src)
    if err is not None or idx is None:
        return None, [], f"解析失败 {err}"
    ww = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    if not ww:
        return None, [], "无 WW_ANIM_XML"
    body = wb.read_body_raw(src, ww[0])
    _s, xml_text, xerr = wb.parse_anim_xml(body)
    if xerr is not None:
        return None, [], f"WW XML 解析失败 {xerr}"
    inner, _st, _en = _diff._locate_animations_list(xml_text)
    om = {}
    ei = 0
    for b, _r in _diff._entry_blocks(inner):
        if b.lstrip().startswith("<U"):
            om[ei] = b
            ei += 1
    return om, [e.instance_id for e in ww], None


def _flat(rows):
    """把 _dump_el 的嵌套树 (list of [indent,n,tag,val,kids]) 展平为 [ (n,tag,val), ... ] 叶。"""
    out = []
    for node in rows if isinstance(rows, list) else [rows]:
        if isinstance(node, list):
            out.extend(_flat(node))
            continue
        indent, n, tag, val, kids = node
        if val and val not in ("[OBJECT]", "[L]", "[U]"):
            out.append((n, tag, val))
        if kids:
            out.extend(_flat(kids))
    return out


def _actor_signature(rows):
    """从嵌套 dump rows 提取 actor 图签名: 只留叶 (n:tag=val) 三元组。"""
    return sorted(f"{n}:{tag}={val}" for n, tag, val in _flat(rows))



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="源 WW package")
    ap.add_argument("--ordinals", nargs="*", type=int, default=[124, 299, 306],
                    help="要 dump 的 ordinal (缺省 124 299 306)")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: 源不存在 {src}", file=sys.stderr); return 2
    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    ord_blocks, _insts, err = _collect_ordinals(src)
    if err is not None:
        print(f"ERROR: {err}", file=sys.stderr); return 3
    n_entries = len(ord_blocks)
    for o in a.ordinals:
        if o not in ord_blocks:
            print(f"ERROR: ordinal {o} 越界 {n_entries}", file=sys.stderr); return 3

    lines = []
    lines.append("=== P3 actor graph / role identity 审计 (只读) ===")
    lines.append(f"WW_ANIM_XML count={len(_insts)}  条目数={n_entries}")
    lines.append("")

    graphs = {}    # ordinal -> (actor_list_field_name, flat_rows)
    for o in a.ordinals:
        root = ET.fromstring(ord_blocks[o])
        al_node, fname = _find_actor_list(root)
        lines.append(f"ORDINAL {o}")
        lines.append(f"  actor 列表字段名(实际): {fname or '(未找到 actors 列表)'}")
        lines.append(f"  候选字段存在性:")
        for cv in CANDIDATE_FIELDS:
            hits = [el for el in root.iter() if el.get("n") == cv]
            if hits:
                lines.append(f"    {cv} = {'; '.join(_leaf_val(h) for h in hits)}")
            else:
                lines.append(f"    {cv} = (缺失)")
        # 递归 actor 图
        lines.append("  actor 图完整结构 (每 actor 的全部字段):")
        if al_node is not None:
            rows = _dump_el(al_node, 2)
            _emit_rows(lines, rows)
            graphs[o] = (fname, rows)
        else:
            lines.append("    (无 actor 列表节点)")
        lines.append("")

    # 差异表: 124 vs 299 (及 306)
    lines.append("=== actor 图差异: 124(Addicted) vs 299/306(Caught Cheating) ===")
    base = 124
    if base in graphs:
        base_sig = _actor_signature(graphs[base][1])
        lines.append(f"  124 '{_disp_of(ord_blocks, base)}' actor 图签名:")
        for s in base_sig:
            lines.append(f"      {s}")
        for cmp in (299, 306):
            if cmp in graphs:
                sig = _actor_signature(graphs[cmp][1])
                only_cmp = [s for s in sig if s not in base_sig]
                only_base = [s for s in base_sig if s not in sig]
                lines.append(f"  --- {cmp} '{_disp_of(ord_blocks, cmp)}' vs 124 ---")
                lines.append(f"      {cmp} 独有 (124 无): " + (", ".join(only_cmp) if only_cmp else "(无)"))
                lines.append(f"      124 独有 ({cmp} 无): " + (", ".join(only_base) if only_base else "(无)"))
                if only_cmp or only_base:
                    lines.append("      <-- actor 图存在差异 (可能含 role identity 判别)")
                else:
                    lines.append("      (actor 图逐字段一致)")
    else:
        lines.append("  (ordinal 124 无 actor 图可对比)")

    txt = "\n".join(lines) + "\n"
    (out_dir / "p3_actor_audit.txt").write_text(txt, encoding="utf-8")
    with open(out_dir / "p3_actor_audit.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ordinal", "field", "tag", "value"])
        for o in a.ordinals:
            if o in graphs:
                for n, tag, val in _flat(graphs[o][1]):
                    w.writerow([o, n, tag, val])
    print(txt)
    print(f"OUT_TXT={out_dir / 'p3_actor_audit.txt'}")
    print(f"OUT_CSV={out_dir / 'p3_actor_audit.csv'}")
    print("P3_ACTOR_AUDIT=OK (只读, 未生成包)")
    print("ZERO_WRITE_TO_MODS=YES")
    return 0


def _disp_of(ord_blocks, o):
    b = ord_blocks.get(o)
    if not b:
        return "?"
    root = ET.fromstring(b)
    for el in root.iter():
        if el.get("n") == "animation_raw_display_name":
            return (el.text or "").strip()
    return "?"


def _emit_rows(lines, rows):
    """递归打印嵌套 rows (list of [indent,n,tag,val,kids])。"""
    rec_print(lines, rows)


def rec_print(lines, node):
    if isinstance(node, list):
        for sub in node:
            rec_print(lines, sub)
        return
    indent, n, tag, val, kids = node
    prefix = " " * indent
    if tag in ("T", "E", "I"):
        lines.append(f"{prefix}{tag} n={n} = {val!r}" if n else f"{prefix}{tag} = {val!r}")
    elif tag == "L":
        lines.append(f"{prefix}L n={n} [{val}]" if n else f"{prefix}L [{val}]")
    else:
        lines.append(f"{prefix}U n={n}" if n else f"{prefix}U")
    if kids:
        for k in kids:
            rec_print(lines, k)


if __name__ == "__main__":
    sys.exit(main())
