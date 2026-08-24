#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P8 只读审计 — M3/M4 克隆是否破坏 Story actor 结构

背景: P7 无隐藏 TGI/GUID/hash/chain registry, 但发现结构级差异:
    Normal Addicted      : actor_id = 0,1           animation_actors_list = L[<U>,<U>]
    Story  Caught Cheating: actor_id = 0,1,2         animation_actors_list = L[<U>,<U>,<U>]
    且 Story 特有字段: animation_pref_gender, object_animation_clip_name
  → 最大嫌疑: M3/M4 克隆 WW_ANIM_XML 时把 Story 的 actors 列表/结构破坏了,
    于是克隆出的故事实例 actor 数量/字段不对, runtime 不注册。

P8 目标 (只读, 不生成包):
  逐节点比较【源 ordinal 299】vs【M3/M4 新 instance】:
    - actor 节点数量 / 顺序 / actor_id
    - actor_interactions
    - animation_actors_list
    - receiving_actor_id / receiving_actor_category
    - animation_pref_gender / object_animation_clip_name
    - 所有 L/T/U 节点结构 (全树)
  判定:
    - 若 M3/M4 新 XML 与源结构不同 -> 定位复制器遗漏字段
    - 若结构完全一致 -> 继续找 Story runtime 最后注册条件

fail-closed: 只读; 源缺/无 WW XML->3; ordinal 越界->4; 输出包无 WW XML->5;
  源与分析目标 ordinal 的条目解析失败->6。ZERO_WRITE_TO_MODS=YES。

用法 (Windows):
  python scripts/ww_animation_p8_actor_clone_audit.py "<SRC.package>" "<M3M4_OUT.package>" \
      --ordinal 299 [--out-dir output/ww_p8]
产物: output/ww_p8/p8_actor_clone_audit.txt + .csv
"""
import argparse
import csv
import importlib.util
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
OUT_DIR = Path("output/ww_p8")

# 重点 actor 结构字段 (用户指定 + P7 新增 Story 特有)
ACTOR_FIELDS = ("actor_id", "actor_interactions", "receiving_actor_id",
                "receiving_actor_category", "animation_pref_gender",
                "object_animation_clip_name", "animation_actor_tags",
                "animation_genders", "animation_role", "actor_role")
LIST_FIELD_ACTORS = "animation_actors_list"


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else None


def get_ww_instances(pkg):
    idx, err = wb.safe_parse(pkg)
    if err is not None or idx is None:
        return None, f"解析失败: {err}", None
    wws = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    if not wws:
        return None, "包内无 WW_ANIM_XML", idx
    return wws, None, idx


def entry_root(pkg, ww_e, ordinal):
    """返回 ordinal 条目的 ET root。"""
    body = wb.read_body_raw(pkg, ww_e)
    _s, xml_text, xerr = wb.parse_anim_xml(body)
    if xerr is not None:
        return None, f"XML 解析失败: {xerr}"
    inner, _st, _en = _diff._locate_animations_list(xml_text)
    om = {}
    ei = 0
    for b, _r in _diff._entry_blocks(inner):
        if b.lstrip().startswith("<U"):
            om[ei] = b
            ei += 1
    if ordinal not in om:
        return None, f"ordinal {ordinal} 越界({len(om)})"
    try:
        return ET.fromstring(om[ordinal]), None
    except Exception as ex:
        return None, f"entry {ordinal} 解析失败: {ex}"


def flat_dump(root):
    """平铺整棵树为 (path, tag, n, text) 带 child 索引。"""
    rows = []

    def walk(el, path, depth):
        for ci, child in enumerate(el):
            lt = _local(child.tag)
            n = child.get("n") or ""
            kids = list(child)
            cpath = f"{path}[{ci}]"
            if lt in ("T", "E", "I"):
                val = (child.text or "").strip()
            elif lt == "L":
                val = f"L[{len(kids)}]"
            elif lt == "U":
                val = "U"
            else:
                val = (child.text or "").strip()
            rows.append({"path": cpath, "tag": lt, "n": n, "val": val, "depth": depth})
            walk(child, cpath, depth + 1)

    walk(root, "root", 0)
    return rows


def actor_structure(root):
    """抽取 actor 结构签名: actors list 内每个 U 的字段。"""
    acts = []
    for el in root.iter():
        if _local(el.tag) == "L" and el.get("n") == LIST_FIELD_ACTORS:
            for u in el:
                if _local(u.tag) != "U":
                    continue
                rec = {"fields": {}}
                for sub in u.iter():
                    st = _local(sub.tag)
                    sn = sub.get("n") or ""
                    if st in ("T", "E", "I") and sn:
                        rec["fields"][sn] = (sub.text or "").strip()
                acts.append(rec)
    return acts


def sig_actor(root):
    """actor 独特签名 (顺序 + 每 actor 的关键字段)。"""
    return [a["fields"].get("actor_id") for a in actor_structure(root)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("output_m34", help="M3/M4 输出 sidecar package")
    ap.add_argument("--ordinal", type=int, default=299)
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    src = Path(a.source)
    out_pkg = Path(a.output_m34)
    if not src.is_file():
        print(f"ERROR: 源不存在 {src}", file=sys.stderr); return 2
    if not out_pkg.is_file():
        print(f"ERROR: M3/M4 输出不存在 {out_pkg}", file=sys.stderr); return 2
    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    src_wws, err, _ = get_ww_instances(src)
    if src_wws is None:
        print(f"ERROR: 源 {err}", file=sys.stderr); return 3
    out_wws, err2, _ = get_ww_instances(out_pkg)
    if out_wws is None:
        print(f"ERROR: M3/M4 输出 {err2}", file=sys.stderr); return 5

    src_root, err3 = entry_root(src, src_wws[0], a.ordinal)
    if src_root is None:
        print(f"ERROR: 源 {err3}", file=sys.stderr); return 4

    src_nodes = flat_dump(src_root)
    src_acts = actor_structure(src_root)
    src_sig = sig_actor(src_root)

    L = []
    L.append("=== P8 M3/M4 克隆是否破坏 Story actor 结构 (只读) ===")
    L.append(f"源包 WW instance = 0x{src_wws[0].instance_id:016X}")
    L.append(f"M3/M4 输出包: {len(out_wws)} 个 WW instance: "
             f"{', '.join(f'0x{e.instance_id:016X}' for e in out_wws)}")
    L.append(f"分析 ordinal = {a.ordinal}")
    L.append("")

    # ---- 源 baseline ----
    L.append("=== 源 ordinal 299 actor 结构 (baseline) ===")
    L.append(f"  actor_id 顺序 = {src_sig}")
    L.append(f"  actors 数 = {len(src_acts)}")
    for i, ac in enumerate(src_acts):
        L.append(f"  actor[{i}]:")
        for f in sorted(ac["fields"]):
            L.append(f"    {f} = {ac['fields'][f]!r}")
    L.append("")

    cmp_rows = []
    identical_all = True
    for out_ww in out_wws:
        inst = out_ww.instance_id
        L.append(f"=== M3/M4 新 instance 0x{inst:016X} ===")
        croot, err4 = entry_root(out_pkg, out_ww, a.ordinal)
        if croot is None:
            L.append(f"  ERROR: {err4}")
            cmp_rows.append([f"0x{inst:016X}", "ERROR", err4])
            identical_all = False
            continue
        cnodes = flat_dump(croot)
        cacts = actor_structure(croot)
        csig = sig_actor(croot)

        # actor 结构对比
        L.append(f"  actor_id 顺序 = {csig}   (源={src_sig})")
        L.append(f"  actors 数 = {len(cacts)}   (源={len(src_acts)})")
        same_actor = (len(cacts) == len(src_acts)
                      and all(a["fields"] == b["fields"]
                              for a, b in zip(cacts, src_acts)))
        L.append(f"  actor 结构全等 = {same_actor}")

        # 全树节点对比
        s_map = {r["path"]: r for r in src_nodes}
        c_map = {r["path"]: r for r in cnodes}
        only_src = [p for p in s_map if p not in c_map]
        only_c = [p for p in c_map if p not in s_map]
        val_diff = [p for p in s_map if p in c_map
                    and (s_map[p]["tag"], s_map[p]["n"], s_map[p]["val"])
                    != (c_map[p]["tag"], c_map[p]["n"], c_map[p]["val"])]

        if same_actor and not only_src and not only_c and not val_diff:
            verdict_inst = "结构完全一致"
        else:
            verdict_inst = "结构不一致"
        L.append(f"  全树 path 对比: 仅源有 {len(only_src)}  仅克隆有 {len(only_c)}  "
                 f"取值不同 {len(val_diff)}")
        if only_src:
            L.append("    [仅源存在] " + "; ".join(only_src[:20]))
        if only_c:
            L.append("    [仅克隆存在] " + "; ".join(only_c[:20]))
        if val_diff:
            L.append("    [值不同]")
            for p in val_diff[:40]:
                s = s_map[p]; c = c_map[p]
                L.append(f"      {p} {s['tag']} n={s['n']!r}: 源={s['val']!r} -> 克隆={c['val']!r}")
        L.append(f"  ==> 判定: {verdict_inst}")
        L.append("")

        cmp_rows.append([f"0x{inst:016X}", verdict_inst, len(cacts), len(src_acts),
                         csig, same_actor, len(only_src), len(only_c), len(val_diff)])
        if verdict_inst != "结构完全一致":
            identical_all = False

    # ---- 总判定 ----
    L.append("=== 总判定 ===")
    if identical_all:
        L.append("所有 M3/M4 新 instance 与源 ordinal 的 actor/全树结构【完全一致】")
        L.append("-> 克隆未破坏 Story actor 结构; Story 不注册的最后条件在别处")
        L.append("  (继续找 Story runtime 最后注册条件, 而非复制器字段遗漏)")
    else:
        L.append("发现 M3/M4 新 instance 与源结构【不一致】")
        L.append("-> 定位到复制器遗漏/破坏的字段, 下一步修正复制逻辑")
    L.append("")

    txt = "\n".join(L)
    txt_path = out_dir / "p8_actor_clone_audit.txt"
    txt_path.write_text(txt, encoding="utf-8")

    csv_path = out_dir / "p8_actor_clone_audit.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["m34_instance", "verdict", "clone_actors", "src_actors",
                    "clone_actor_ids", "actor_equal", "only_src_nodes",
                    "only_clone_nodes", "val_diff_nodes"])
        w.writerows(cmp_rows)

    print(txt)
    print(f"OUT_TXT={txt_path}")
    print(f"OUT_CSV={csv_path}")
    print("P8_ACTOR_CLONE_AUDIT=OK (只读, 未生成包, 未动 Mods)")
    print("ZERO_WRITE_TO_MODS=YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
