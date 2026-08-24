#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M3 runtime 差异只读取证: 源 entry vs M3 新 instance 对应 entry 的【全部字段】对比。

目的: M3 改了 raw_display_name/stage_name/next_stages 三字段后, WW runtime
  仍 fallback "NOT Caught Cheating N"。说明 identity key 在三字段之外。
  本工具 dump 源 entry 与 M3 新 entry 的每一个字段, 找出:
    A) M3 改动的字段(预期: raw/stage/next)
    B) 源->新之间【不该变却变了】的字段 -> 异常 identity 线索
    C) 源 entry 中 M3 完全没碰、但疑似 story/identity key 的字段(hash/id/ae_/UUID/story/key)
  据此定位 WW runtime 判定 STORY identity 的真正字段。

绝不改 writer / 不生成 package / 不写 Mods。
ZERO_WRITE_TO_MODS=YES

用法 (Windows, 只读):
  python scripts/ww_animation_m3_runtime_forensic.py <SRC.package> <M3.package>
      --ordinal 299 300 301 302 303 304 305 306 [--all]
"""
import argparse
import csv
import importlib.util
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
_spec = importlib.util.spec_from_file_location("ww_canary_builder", SCRIPT_DIR / "ww_animation_canary_builder.py")
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)
import ww_animation_m2_diff_forensic as _diff  # 复用 _collect_fields / _entry_blocks / _locate_animations_list

WW_ANIM_XML = 0x7DF2169C
OUT_DIR = Path("output/ww_animation_m3_runtime")
# identity/story 候选关键字 (大小写不敏感)
ID_HINTS = ("identity", "hash", "ae_", "story", "key", "uuid", "guid", "group", "msd",
            "instance", "name_id", "anim_id", "animation_id", "animation_hash",
            "id_", "clip", "source", "original", "variant", "file")


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else None


def _read_entries(pkg_path):
    """返回 (ordinal_map{ordinal:block}, ww_instances[list], n_entries)。只读。"""
    idx, err = wb.safe_parse(pkg_path)
    if err is not None or idx is None:
        return None, None, f"解析失败 {err}"
    ww = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    if not ww:
        return None, None, "无 WW_ANIM_XML"
    # 取第一个 WW_XML 建立 ordinal 映射 (条目顺序对每个 WW_XML 一致)
    body = wb.read_body_raw(pkg_path, ww[0])
    _s, xml_text, xerr = wb.parse_anim_xml(body)
    if xerr is not None:
        return None, None, f"WW XML 解析失败 {xerr}"
    inner, _st, _en = _diff._locate_animations_list(xml_text)
    if inner is None:
        return None, None, "找不到 animations_list"
    om = {}
    ei = 0
    for b, _r in _diff._entry_blocks(inner):
        if b.lstrip().startswith("<U"):
            om[ei] = b
            ei += 1
    return om, [e.instance_id for e in ww], None


def _fields_of_xml(pkg_path, inst, ordinal):
    """取某 WW_XML instance 中 ordinal 位置 entry 的全部字段。"""
    idx, err = wb.safe_parse(pkg_path)
    if err is not None:
        return None, f"解析失败 {err}"
    target = next((e for e in idx.entries if e.type_id == WW_ANIM_XML and e.instance_id == inst), None)
    if target is None:
        return None, f"instance 0x{inst:016X} 不在包内"
    body = wb.read_body_raw(pkg_path, target)
    _s, xml_text, xerr = wb.parse_anim_xml(body)
    if xerr is not None:
        return None, f"WW XML 解析失败 {xerr}"
    inner, _st, _en = _diff._locate_animations_list(xml_text)
    blocks = _diff._entry_blocks(inner)
    ei = 0
    for b, _r in blocks:
        if b.lstrip().startswith("<U"):
            if ei == ordinal:
                return _diff._collect_fields(b), None
            ei += 1
    return None, f"ordinal {ordinal} 越界"


def _fmt_val(fnode):
    if fnode["tag"] == "L":
        return f"[list:{fnode['children']}]"
    if fnode["tag"] == "U":
        return f"[obj]"
    return fnode["val"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src_pkg", help="源 WW package")
    ap.add_argument("m3_pkg", help="M3 新生成 package")
    ap.add_argument("--ordinal", nargs="+", type=int, required=True, help="对比的 target ordinal (299 300 ... )")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    src, m3 = Path(a.src_pkg), Path(a.m3_pkg)
    if not src.is_file():
        print(f"ERROR: 源不存在 {src}", file=sys.stderr); return 2
    if not m3.is_file():
        print(f"ERROR: M3 包不存在 {m3}", file=sys.stderr); return 2

    src_om, src_insts, e = _read_entries(src)
    if e:
        print(f"ERROR 源: {e}", file=sys.stderr); return 3
    m3_om, m3_insts, e2 = _read_entries(m3)
    if e2:
        print(f"ERROR M3: {e2}", file=sys.stderr); return 3
    n_entries = len(src_om)
    for o in a.ordinal:
        if o not in src_om:
            print(f"ERROR: ordinal {o} 越界 {n_entries}", file=sys.stderr); return 3

    print(f"源 WW_XML instances: {len(src_insts)} ; M3 WW_XML instances: {len(m3_insts)}")
    print(f"源 instances={[hex(i) for i in src_insts]}")
    print(f"M3 instances={[hex(i) for i in m3_insts]}")

    rows = []      # csv row: ordinal, field, src_tag, src_val, m3_tag, m3_val, classification
    txt_lines = []
    new_insts = [i for i in m3_insts if i not in src_insts]
    txt_lines.append(f"=== M3 runtime 只读取证: 源 vs M3 新 instance (逐字段) ===")
    txt_lines.append(f"源 instances: {[hex(i) for i in src_insts]}")
    txt_lines.append(f"M3 新增 instances: {[hex(i) for i in new_insts]}")
    txt_lines.append("")

    # 对每个新增 instance, 找它局部化的 ordinal = M3 XML 里 raw_display 与源不同的那条
    M3_FIELDS = ("animation_raw_display_name", "animation_stage_name", "animation_next_stages")
    inst_of_ord = {}
    for inst in new_insts:
        idx, err = wb.safe_parse(m3)
        target = next((e for e in idx.entries if e.type_id == WW_ANIM_XML and e.instance_id == inst), None)
        body = wb.read_body_raw(m3, target)
        _s, xt, xerr = wb.parse_anim_xml(body)
        if xerr is not None:
            continue
        inner, _st, _en = _diff._locate_animations_list(xt)
        ei = 0
        for b, _r in _diff._entry_blocks(inner):
            if b.lstrip().startswith("<U"):
                f = _diff._collect_fields(b)
                src_fields = _diff._collect_fields(src_om[ei]) if ei in src_om else {}
                src_disp = src_fields.get("animation_raw_display_name", [{}])[0].get("val", "") if src_fields.get("animation_raw_display_name") else ""
                new_disp = f.get("animation_raw_display_name", [{}])[0].get("val", "") if f.get("animation_raw_display_name") else ""
                if src_disp != new_disp:   # 该 entry 被局部化 -> 此 instance 对应 ordinal ei
                    inst_of_ord[inst] = ei
                ei += 1

    # 只对比 a.ordinal 指定的目标
    for o in sorted(a.ordinal):
        # 找到局部化到 o 的新 instance
        inst = next((i for i, oo in inst_of_ord.items() if oo == o), None)
        if inst is None:
            txt_lines.append(f"\n[ordinal {o}] !! 未找到对应的 M3 新 instance (可能未局部化)")
            rows.append([o, "(无对应新instance)", "", "", "", "", "MISSING"])
            continue
        src_f = _diff._collect_fields(src_om[o])
        m3_f, ferr = _fields_of_xml(m3, inst, o)
        if ferr:
            txt_lines.append(f"\n[ordinal {o}] ERROR {ferr}")
            continue
        all_keys = sorted(set(src_f.keys()) | set(m3_f.keys()))
        txt_lines.append(f"\n[ordinal {o}]  新instance=0x{inst:016X}")
        for k in all_keys:
            s_vals = src_f.get(k, [])
            m_vals = m3_f.get(k, [])
            s_present = len(s_vals) > 0
            m_present = len(m_vals) > 0
            s_v = _fmt_val(s_vals[0]) if s_vals else "—"
            m_v = _fmt_val(m_vals[0]) if m_vals else "—"
            if not s_present:
                cls = "NEW_IN_M3"          # M3 entry 新增了源没有的字段
            elif not m_present:
                cls = "REMOVED_IN_M3"      # M3 entry 删了源有的字段
            elif k in M3_FIELDS:
                cls = "M3_CHANGED"
            elif s_v == m_v:
                cls = "IDENTICAL"
            else:
                cls = "DIFFERS"
            id_hint = any(h in k.lower() for h in ID_HINTS)
            rows.append([o, k, s_v, m_v, cls, "STORY_ID_HINT" if id_hint else ""])
            if cls != "IDENTICAL" or id_hint:
                flag = ""
                if cls == "DIFFERS":
                    flag = "  <-- 异常差异!"
                if id_hint and cls == "IDENTICAL":
                    flag = "  [STORY_ID_CANDIDATE-未动]"
                txt_lines.append(f"  {k:40s} {s_v!r:20s} -> {m_v!r:20s} [{cls}]{flag}")

    # 汇总异常
    txt_lines.append("\n=== 异常差异字段 (DIFFERS / NEW / REMOVED) ===")
    anomalies = [r for r in rows if r[4] in ("DIFFERS", "NEW_IN_M3", "REMOVED_IN_M3")]
    if not anomalies:
        txt_lines.append("  (无 —— 除三字段外一切一致)")
    for r in anomalies:
        txt_lines.append(f"  ord{r[0]} {r[1]}: {r[2]!r} -> {r[3]!r} [{r[4]}]")

    txt_lines.append("\n=== 疑似 STORY identity key 候选 (源 entry 中存在, 且含 id/hash/key/story 等) ===")
    seen = set()
    for o in sorted(a.ordinal):
        src_f = _diff._collect_fields(src_om[o]) if o in src_om else {}
        for k in src_f:
            if any(h in k.lower() for h in ID_HINTS) and k not in seen:
                seen.add(k)
                txt_lines.append(f"  {k} = {_fmt_val(src_f[k][0])}  (ordinal {o})")

    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    txt = "\n".join(txt_lines) + "\n"
    (out_dir / "m3_runtime_field_diff.txt").write_text(txt, encoding="utf-8")
    with open(out_dir / "m3_runtime_field_diff.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ordinal", "field", "src_val", "m3_val", "classification", "identity_hint"])
        w.writerows(rows)
    print(txt)
    print(f"OUT_TXT={out_dir / 'm3_runtime_field_diff.txt'}")
    print(f"OUT_CSV={out_dir / 'm3_runtime_field_diff.csv'}")
    print("M3_RUNTIME_FORENSIC=OK (只读, 未生成包)")
    print("ZERO_WRITE_TO_MODS=YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
