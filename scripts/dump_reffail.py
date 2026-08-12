#!/usr/bin/env python3
"""取证: 精确 dump 6 条 REF_FAIL 的 PosePackInstance / pose entry / hash 来源。
只读。key 在包内确实不存在 -> 保持 REF_FAIL。不猜、不翻、不扩范围。"""
import sys, csv
from pathlib import Path
import xml.etree.ElementTree as ET
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse
from backend import get_backend
from resource_types import RESOURCE_TYPES

# 直接吃映射 CSV 里所有 REF_FAIL 记录 (不靠文件名猜, 用真实路径)
mapping_csv = Path(sys.argv[1] if len(sys.argv) > 1 else "D:/projects/sims4_trans/output/pose_text_mapping.csv")
rows = []
with open(mapping_csv, encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        if r.get("status") == "REF_FAIL":
            rows.append(r)

print(f"REF_FAIL 记录总数: {len(rows)}\n")
for r in rows:
    p = r.get("package_path") or ""
    key = (r.get("stbl_key_hash") or "").strip().lower()
    disp = r.get("display_ref") or ""
    pname = r.get("pose_name") or ""
    print("=" * 72)
    print(f"包: {p}")
    print(f"  目标 key = {key}   display_ref={disp}   pose_name={pname!r}")

    idx, err = safe_parse(p)
    if err or not idx:
        print("  解析失败:", err); continue
    backend = get_backend("readonly").open(p)

    # (1) key 是否存在于包内任何 STBL 资源的 keyHash?
    key_found_any_stbl = False
    stbl_ids = []
    for e in idx.entries:
        if e.type_id == 0x220557DA:
            stbl_ids.append((e.instance_id, e.size))
    print(f"  包内 STBL 资源数: {len(stbl_ids)}")
    for e in idx.entries:
        if e.type_id == 0x220557DA:
            try:
                d = backend.read_small_resource(e, max_bytes=2 * 1024 * 1024)
            except Exception:
                continue
            if not d:
                continue
            kb = bytes.fromhex(key[2:])  # 4 字节 key
            if kb in d:
                key_found_any_stbl = True
    print(f"  该 key (4字节) 是否出现在任一 STBL 资源体内: {'是' if key_found_any_stbl else '否 —— 包内确实无此 key'}")

    # (2) 定位 pose 引用出现在哪个 XML / 哪个 pose entry
    xml_hits = []
    for e in idx.entries:
        if not (RESOURCE_TYPES.is_snippet(e.type_id)
                or RESOURCE_TYPES.is_tuning_xml(e.type_id)
                or RESOURCE_TYPES.is_known_safely(e.type_id, "WW_ANIM_XML")):
            continue
        try:
            d = backend.read_small_resource(e, max_bytes=2 * 1024 * 1024)
        except Exception:
            continue
        if not d:
            continue
        try:
            root = ET.fromstring(d)
        except Exception:
            continue
        # 收集所有 pose_display_name == key 的位置, 以及它们所在 pose entry 的 pose_name
        for el in root.iter():
            if el.attrib.get("n") == "pose_display_name" and el.text and el.text.strip().lower() == key:
                # 回溯父链找 pose entry (含 pose_name / pose_animation 的容器)
                blk = el
                entry_desc = {}
                for _ in range(8):
                    if blk is None:
                        break
                    # 收集该容器下所有子节点名
                    kids = {ch.attrib.get("n") for ch in blk if ch.attrib.get("n")}
                    if "pose_name" in kids or "pose_animation" in kids:
                        pn = nxt = None
                        for ch in blk:
                            if ch.attrib.get("n") == "pose_name" and ch.text: pn = ch.text.strip()
                        entry_desc = {"container_kids": sorted(kids), "pose_name": pn}
                        break
                    blk = blk.getparent() if hasattr(blk, "getparent") else None
                xml_hits.append({
                    "type_id": e.type_id, "inst": e.instance_id,
                    "root_tag": root.tag, "entry": entry_desc,
                })
    if xml_hits:
        for h in xml_hits:
            print(f"  [XML type=0x{h['type_id']:08X} inst=0x{h['inst']:016X} root={h['root_tag']}]")
            print(f"     所属 pose entry: {h['entry']}")
    else:
        print("  未在任何 XML 的 pose_display_name 中找到该 key 的引用位置 (仅 CSV 记录)")
    backend.close()
