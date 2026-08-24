#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P4 取证 — Story registry/mapping 外部引用审计 (只读, 目标聚焦)。

目标: 找到 WW runtime 注册 Story animation 的入口。不做全 package dump,
只回答三个问题:
  1) 哪些非 WW_ANIM_XML 资源引用了 WW_ANIM_XML instance (TGI)
  2) 所有非 WW 资源里, 哪些疑似 Story registry/mapping/index/tuning/
     STBL/binary (按类型归类)
  3) Addicted(124-126) vs Caught Cheating(299-306) 的外部引用差异:
     对每个目标 ordinal, 解析其【专属标识串】(raw_display_name /
       clip_name / animation_id 值 / stage_name), 检查每个非 WW 资源
       正文是否含这些串 -> 得出哪些外部资源"认识"该动画。

只读。不生成 package / 不改 writer / 不碰 Mods (ZERO_WRITE_TO_MODS=YES)。

复用: ww_animation_canary_builder (wb: safe_parse/read_body_raw/decompress_maybe/
  parse_anim_xml) + ww_animation_m2_diff_forensic (_diff: _locate_animations_list/
  _entry_blocks/_collect_fields) + ww_animation_p1_resource_forensic (_p1:
  decode_body/KNOWN_TYPES/hexI/parse_xml_refs)。

用法 (Windows, 只读源):
  python scripts/ww_animation_p4_registry_audit.py "<SRC.package>" \
      [--ordinals 124 125 126 299 300 301 302 303 304 305 306] \
      [--out-dir output/ww_p4]
"""
import argparse
import csv
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
import ww_animation_m2_diff_forensic as _diff
import ww_animation_p1_resource_forensic as _p1

WW_ANIM_XML = 0x7DF2169C
OUT_DIR = Path("output/ww_p4")

# 引用排序: 越靠前越像 Story 注册入口
TYPE_RANK = {
    _p1.KNOWN_TYPES.get(t, "?") : i
    for i, t in enumerate([0x545AC2C2, 0x034AEECB, 0x073FAA27, 0x00B2D882,
                           0x220557DA, 0xBC4A5044, 0x6B20C4F3])
}


def _ordinal_identity(block):
    """从 entry block 解析该条目专属标识串 (小写集), 供外部资源全文匹配。
    返回 dict: raw_display / clip_names[] / id_values[] / stage_name。"""
    root = ET_from(block)
    ident = {
        "raw_display": "",
        "clip_names": [],
        "id_values": [],
        "stage_name": "",
    }
    if root is None:
        return ident
    for el in root.iter():
        n = el.get("n") or ""
        lt = el.tag.rsplit('}', 1)[-1]
        if lt in ("T", "E", "I"):
            v = (el.text or "").strip()
        else:
            continue
        if n == "animation_raw_display_name":
            ident["raw_display"] = v.lower()
        elif n == "animation_clip_name":
            ident["clip_names"].append(v.lower())
        elif n in ("animation_id", "actor_id"):
            ident["id_values"].append(v.lower())
        elif n == "animation_stage_name":
            ident["stage_name"] = v.lower()
    return ident


def ET_from(block):
    import xml.etree.ElementTree as ET
    try:
        return ET.fromstring(block)
    except Exception:
        return None


def _tokens_of(ident):
    """把 entry 标识串拆成可匹配的原子 token(小写)。"""
    toks = set()
    if ident["raw_display"]:
        toks.add(ident["raw_display"])
        for t in re.split(r'\W+', ident["raw_display"]):
            if len(t) >= 4:
                toks.add(t)
    toks.update(ident["clip_names"])
    toks.update(ident["id_values"])
    if ident["stage_name"]:
        toks.add(ident["stage_name"])
    return toks


def rgx_of(ident):
    """生成一个对整条 entry 专属串的大小写不敏感正则, 用于判定外部资源
    '认识'该 animation。取 raw_display 全文(若存在)或其 token。"""
    parts = []
    if ident["raw_display"]:
        parts.append(re.escape(ident["raw_display"]))
    parts += [re.escape(c) for c in ident["clip_names"]]
    parts += [re.escape(i) for i in ident["id_values"]]
    if not parts:
        return None
    # 只匹配长度>=6 的多字符串, 避免过短误报 ; 允许 raw_display 或 id 任中一
    return re.compile("|".join(p for p in parts if len(p) >= 4), re.IGNORECASE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="源 WW package")
    ap.add_argument("--ordinals", nargs="*", type=int,
                    default=[124, 125, 126, 299, 300, 301, 302, 303, 304, 305, 306])
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

    # ---- 1) WW_ANIM_XML instance 集合 ----
    ww_entries = [e for e in entries if e.type_id == WW_ANIM_XML]
    ww_tgis = {f"0x{e.instance_id:016x}": e for e in ww_entries}
    if not ww_entries:
        print("ERROR: 无 WW_ANIM_XML", file=sys.stderr); return 3

    # ---- 2) 解析源 WW XML: ordinal -> 专属标识 ----
    ord_ident = {}
    ord_block = {}
    body = wb.read_body_raw(src, ww_entries[0])
    _s, xml_text, xerr = wb.parse_anim_xml(body)
    if xerr is not None:
        print(f"ERROR: WW XML 解析失败 {xerr}", file=sys.stderr); return 3
    inner, _st, _en = _diff._locate_animations_list(xml_text)
    ei = 0
    for b, _r in _diff._entry_blocks(inner):
        if b.lstrip().startswith("<U"):
            ord_ident[ei] = _ordinal_identity(b)
            ord_block[ei] = b
            ei += 1
    n_entries = ei
    for o in a.ordinals:
        if o not in ord_ident:
            print(f"ERROR: ordinal {o} 越界 {n_entries}", file=sys.stderr); return 3

    # 预编译每目标 ordinal 的匹配正则
    rgx_of_ord = {o: rgx_of(ord_ident[o]) for o in a.ordinals}

    # ---- 3) 枚举全部资源, 解码, 全文匹配 ----
    lines = []
    lines.append("=== P4 Story registry/mapping 外部引用审计 (只读, 目标聚焦) ===")
    lines.append(f"WW_ANIM_XML count={len(ww_entries)}  条目数={n_entries}  目标 ordinals={a.ordinals}")
    lines.append("")

    # 资源清单 (key=(type,group,inst), 解码信息)
    res_info = {}              # key -> dict(kind,size,comp)
    ref_ww_tgi = {}            # key -> list[instance hex] (引用哪个 WW XML TGI)
    hit_ordinal = {}           # key -> list[ordinal] (正文含该 ordinal 专属标识串)
    hit_all = {}               # key -> list[str]  (匹配到的具体 token)
    type_of = {}
    grou_of = {}
    inst_of = {}

    for e in entries:
        key = (e.type_id, e.group_id, e.instance_id)
        type_of[key] = e.type_id
        grou_of[key] = e.group_id
        inst_of[key] = e.instance_id
        raw = wb.read_body_raw(src, e)
        body = wb.decompress_maybe(raw)
        kind, text = _p1.decode_body(body)
        res_info[key] = {"kind": kind, "size": len(body), "comp": raw != body}
        lower = text.lower()

        # 引用了哪个 WW XML TGI
        ref = [tgi for tgi in ww_tgis if tgi in lower]
        if ref:
            ref_ww_tgi[key] = ref

        # 该资源正文认识哪些目标 ordinal
        for o in a.ordinals:
            rx = rgx_of_ord[o]
            if rx is None:
                continue
            if rx.search(lower):
                hit_ordinal.setdefault(key, []).append(o)
        # 记录具体 token 命中
        for o in a.ordinals:
            idt = ord_ident[o]
            for probe, name in [(idt["raw_display"], "raw_display"),
                                (idt["stage_name"], "stage_name")]:
                if probe and len(probe) >= 4 and probe in lower:
                    hit_all.setdefault(key, []).append(f"{name}='{probe[:60]}'")
            for c in idt["clip_names"]:
                if c and c in lower:
                    hit_all.setdefault(key, []).append(f"clip='{c[:60]}'")
            for iv in idt["id_values"]:
                if iv and len(iv) >= 4 and iv in lower:
                    hit_all.setdefault(key, []).append(f"id='{iv[:60]}'")

    # ---- 4) 输出 A: 非 WW 资源引用 WW XML instance ----
    lines.append("### A. 非 WW_ANIM_XML 资源引用 WW_ANIM_XML instance ###")
    ref_rows = []
    for key, refs in ref_ww_tgi.items():
        t = type_of[key]
        lines.append(f"  {_p1.KNOWN_TYPES.get(t, f'0x{t:08X}'):24s} "
                     f"g=0x{grou_of[key]:016X} 0x{inst_of[key]:016X} kind={res_info[key]['kind']} "
                     f"-> 引用 {', '.join(refs)}")
        ref_rows.append([_p1.KNOWN_TYPES.get(t, f"0x{t:08X}"), f"0x{grou_of[key]:016X}",
                         f"0x{inst_of[key]:016X}", res_info[key]["kind"], ",".join(refs)])
    if not ref_rows:
        lines.append("  (无非 WW 资源引用 WW XML instance)")
    lines.append("")

    # ---- 5) 输出 B: 所有非 WW 资源按类型归类 (registry/tuning/index/STBL/binary) ----
    lines.append("### B. 非 WW 资源全清单 (按类型, 疑似 registry/tuning/index/mapping) ###")
    non_ww = [k for k in type_of if type_of[k] != WW_ANIM_XML]
    if not non_ww:
        lines.append("  (package 中除了 WW_ANIM_XML 没有任何其它资源)")
    else:
        by_type = {}
        for k in non_ww:
            by_type.setdefault(type_of[k], []).append(k)
        for t in sorted(by_type, key=lambda x: (TYPE_RANK.get(_p1.KNOWN_TYPES.get(x, str(x)), 99),
                                                x)):
            tname = _p1.KNOWN_TYPES.get(t, f"0x{t:08X}")
            lines.append(f"  [{tname}] count={len(by_type[t])}")
            for k in by_type[t]:
                lines.append(f"      type=0x{t:08X} g=0x{grou_of[k]:016X} 0x{inst_of[k]:016X} "
                             f"size={res_info[k]['size']} comp={res_info[k]['comp']} kind={res_info[k]['kind']} "
                             f"认识ordinal={sorted(hit_ordinal.get(k, []))}")
    lines.append("")

    # ---- 6) 输出 C: 外部引用差异 Addicted vs Caught Cheating ----
    lines.append("### C. 外部引用差异: Addicted(124-126) vs Caught Cheating(299-306) ###")
    lines.append("  每个非 WW 资源 -> 它认识哪些目标 ordinal + 命中 token:")
    addic = set(range(124, 127))
    cheat = set(range(299, 307))
    for k in non_ww:
        ho = sorted(hit_ordinal.get(k, []))
        ht = hit_all.get(k, [])
        tname = _p1.KNOWN_TYPES.get(type_of[k], f"0x{type_of[k]:08X}")
        tag = f"{tname:24s} g=0x{grou_of[k]:016X} 0x{inst_of[k]:016X}"
        if not ho and not ht:
            lines.append(f"  {tag}  (未引用任何目标 ordinal)")
            continue
        a_hit = sorted(set(ho) & addic)
        c_hit = sorted(set(ho) & cheat)
        lines.append(f"  {tag}")
        lines.append(f"      Addicted 命中: {a_hit}")
        lines.append(f"      CaughtCheat 命中: {c_hit}")
        seen = set()
        for htkn in ht:
            if htkn not in seen:
                lines.append(f"      token: {htkn}")
                seen.add(htkn)
    lines.append("")

    # ---- 7) 差异结论 ----
    lines.append("### D. 差异结论 (推断 Story 注册入口) ###")
    add_hit_res = {k for k in non_ww if set(hit_ordinal.get(k, [])) & addic}
    cheat_hit_res = {k for k in non_ww if set(hit_ordinal.get(k, [])) & cheat}
    lines.append(f"  引用任一 Addicted 的非 WW 资源数: {len(add_hit_res)}")
    lines.append(f"  引用任一 Caught Cheating 的非 WW 资源数: {len(cheat_hit_res)}")
    only_cheat = cheat_hit_res - add_hit_res
    only_add = add_hit_res - add_hit_res
    lines.append(f"  仅 Caught Cheating 引用(Addicted 无) 的资源: {len(only_cheat)} -> " +
                 ", ".join(f"{_p1.KNOWN_TYPES.get(type_of[k], str(type_of[k]))}" for k in sorted(only_cheat, key=lambda x: str(x))))
    lines.append(f"  仅 Addicted 引用(Caught Cheating 无) 的资源: {len(only_add)} -> " +
                 ", ".join(f"{_p1.KNOWN_TYPES.get(type_of[k], str(type_of[k]))}" for k in sorted(only_add, key=lambda x: str(x))))
    # 两条系列都被同一资源引用的 => 强注册入口候选
    both = cheat_hit_res & add_hit_res
    lines.append(f"  两系列都引用的资源 (最疑似注册入口): {len(both)} -> " +
                 ", ".join(f"{_p1.KNOWN_TYPES.get(type_of[k], str(type_of[k]))}@0x{inst_of[k]:016X}"
                           for k in sorted(both, key=lambda x: str(x))))

    txt = "\n".join(lines) + "\n"
    out_txt = out_dir / "p4_registry_audit.txt"
    out_txt.write_text(txt, encoding="utf-8")

    with open(out_dir / "p4_registry_audit.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["type", "type_name", "group", "instance", "kind", "size",
                    "compressed", "refs_ww_instance", "hit_ordinals", "hit_tokens"])
        for k in non_ww:
            w.writerow([f"0x{type_of[k]:08X}",
                        _p1.KNOWN_TYPES.get(type_of[k], ""),
                        f"0x{grou_of[k]:016X}", f"0x{inst_of[k]:016X}",
                        res_info[k]["kind"], res_info[k]["size"], res_info[k]["comp"],
                        ";".join(ref_ww_tgi.get(k, [])),
                        ";".join(map(str, sorted(hit_ordinal.get(k, [])))),
                        ";".join(sorted(set(hit_all.get(k, []))))])
    print(txt)
    print(f"OUT_TXT={out_txt}")
    print(f"OUT_CSV={out_dir / 'p4_registry_audit.csv'}")
    print("P4_REGISTRY_AUDIT=OK (只读, 未生成包)")
    print("ZERO_WRITE_TO_MODS=YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
