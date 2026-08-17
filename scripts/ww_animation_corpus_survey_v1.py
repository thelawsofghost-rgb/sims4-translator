#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WW Animation Corpus Survey V1。

只读扫描 Mods 根目录下递归 .package, 识别真实 WW Animation registration XML
(WickedWhimsAnimationPackage / StripClubDanceAnimationPackage), 逐 animation entry
结构提取字段, display 字段分类, schema 分流, TGI collision 审计, 翻译一致性预备统计,
并推荐第二个真机 canary 候选。

严格 positive evidence:
  - 不作 filename/WW-name/仅 CLIP/仅 0x7DF2169C 判定。
  - schema 由 XML root <I c=...> 的 class 属性 (真实 Sims tuning) 判定。
  - animation entry 由真实 tuning tree 语义定位 (WickedWhims <L n="animations_list"> 的 <U> child)。
  - display 由精确 <T n="animation_raw_display_name">TEXT</T> 语义定位, 严禁 ordinal guessing。

本轮 ZERO WRITE TO MODS: 只读/解析/统计/输出 CSV+report, 不改任何 source, 不生成 sidecar,
不翻译, 不生产, 不部署。
"""

import argparse
import csv
import hashlib
import importlib.util
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
import xml.etree.ElementTree as ET

SCRIPT_DIR = Path(__file__).resolve().parent
MODULE = SCRIPT_DIR / "ww_animation_canary_builder.py"
_spec = importlib.util.spec_from_file_location("ww_canary_builder", MODULE)
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)

WW_ANIM_XML = wb.WW_ANIM_XML
WW_SCHEMA = "WickedWhimsAnimationPackage"
SC_SCHEMA = "StripClubDanceAnimationPackage"
UNKNOWN = "UNKNOWN_SCHEMA"

# 真实 Sims 4 资源类型 (source-faithful; 非 WW XML 资源不判定为 animation)
CLIP_TYPE = 0x6B20C4F3
ANIM_RCOL_TYPE = 0xBC4A5044
STBL_TYPE = 0x220557DA

# WW: display 字段名 (语义 selector)
WW_DISPLAY_FIELD = "animation_raw_display_name"
# StripClub: 待 forensic (不假设字段名相同)
SC_DISPLAY_FIELDS = ["animation_raw_display_name", "dancer_animation_clip_name", "display_name"]

ACTOR_LIST_FIELD = "actors"
WW_ENTRY_LIST_FIELD = "animations_list"

# WW entry 内可安全抽取的结构字段
WW_ENTRY_FIELDS = [
    "animation_author",
    "animation_locations",
    "animation_custom_locations",
    "animation_category",
    "animation_tags",
    "animation_loops",
    "animation_allowed_for_random",
]
ACTOR_FIELDS = ["actor_id", "animation_clip_name", "animation_type", "animation_genders"]


def _sha256(p: Path) -> str:
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _el_tag(el) -> str:
    """去掉 namespace 前缀后的 tag 名。"""
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else None


def _el_name(el) -> str:
    """precise tuning name 语义 = n attribute (若存在)。"""
    return el.get("n")


def _children_with_tag(parent, tag):
    return [c for c in list(parent) if _el_tag(c) == tag]


def _children_with_name(parent, name):
    return [c for c in list(parent) if _el_name(c) == name]


def _text_of(el):
    t = el.text
    return "" if t is None else t


def _exact_display_nodes(entry_el, field):
    """在 entry 根下精确语义定位 display 节点 (TAG=T, n=field)。返回 [(text, el)]。"""
    out = []
    for c in list(entry_el):
        if _el_tag(c) == "T" and _el_name(c) == field:
            out.append((_text_of(c), c))
    return out


class PackageResult:
    def __init__(self, path, sha):
        self.path = path
        self.filename = path.name
        self.sha = sha
        self.parse_status = "OK"
        self.parse_err = ""
        self.schema_classes = []
        self.ww_xml_count = 0
        self.ww_xml_map = {}       # tgi_str -> {"schema", "path", "entry_count", "display"}
        self.stripclub_forensic = []  # list[dict] per SC XML
        self.animation_entry_count = 0
        self.exact_display_count = 0
        self.clip_count = 0
        self.stbl_count = 0
        self.anomalies = []
        self.entries = []          # list[dict] per animation entry


def classify_schema(text):
    """positive evidence: 解析后的 root class。返回 (schema, root_class, ok)。"""
    try:
        root = ET.fromstring(text)
    except Exception:
        return None, None, False
    c = root.get("c")
    tag = _el_tag(root)
    if c == WW_SCHEMA:
        return WW_SCHEMA, c, True
    if c == SC_SCHEMA:
        return SC_SCHEMA, c, True
    # 兜底: 从 tunable root 类名推断 (class 属性不存在时)
    if tag and "WickedWhimsAnimationPackage" in tag:
        return WW_SCHEMA, c, True
    if tag and "StripClubDanceAnimationPackage" in tag:
        return SC_SCHEMA, c, True
    return UNKNOWN, c, True


def extract_ww_entries(root, text):
    """按真实 tuning tree 提取 WW animation entries。
    WickedWhimsAnimationPackage:
      <I c="WickedWhimsAnimationPackage" ...>
        <T n="wickedwhims_animations">N</T>
        <L n="animations_list">
          <U>  ... 每个 <U> = 一个 animation entry  ...
    返回 (entries, structural_ok, anomaly)。
    """
    entries = []
    structural_ok = True
    anomaly = None
    # animations_list 定位
    lists = []
    for node in root.iter():
        if _el_tag(node) == "L" and _el_name(node) == WW_ENTRY_LIST_FIELD:
            lists.append(node)
    if not lists:
        return entries, False, f"no <L n={WW_ENTRY_LIST_FIELD!r}>"
    if len(lists) > 1:
        anomaly = f"multiple <L n={WW_ENTRY_LIST_FIELD!r}> ({len(lists)})"
    alist = lists[0]
    for u in _children_with_tag(alist, "U"):
        if _el_name(u) is not None and _el_name(u) != "":
            # 有些可能以具名 N 包裹, 但真实结构为无名 <U>
            pass
        entry = {
            "entry_ordinal": len(entries),
            "display_nodes": _exact_display_nodes(u, WW_DISPLAY_FIELD),
        }
        # 结构字段
        for f in WW_ENTRY_FIELDS:
            nodes = _children_with_name(u, f)
            vals = []
            for nd in nodes:
                if _el_tag(nd) == "T":
                    vals.append(_text_of(nd))
                elif _el_tag(nd) == "L":
                    # L 列表: 收集 <T> 子文本 (reference/tag 列表内容)
                    vals.append("|".join(_text_of(x) for x in _children_with_tag(nd, "T")))
                elif _el_tag(nd) == "E":
                    vals.append(_text_of(nd))
            entry[f] = vals[0] if len(vals) == 1 else vals
        # actors
        actor_nodes = _children_with_name(u, ACTOR_LIST_FIELD)
        actors = []
        if actor_nodes:
            for au in _children_with_tag(actor_nodes[0], "U"):
                act = {"actor_ordinal": len(actors)}
                for f in ACTOR_FIELDS:
                    t_ = _children_with_name(au, f)
                    act[f] = _text_of(t_[0]) if len(t_) == 1 else ([_text_of(x) for x in t_] if t_ else None)
                actors.append(act)
        entry["actors"] = actors
        entry["actor_count"] = len(actors)
        # display 分类
        disp = entry["display_nodes"]
        display_status = classify_display(disp, entry)
        entry["display_status"] = display_status
        entry["animation_raw_display_name"] = (disp[0][0].strip() if disp else "")
        entry["_el"] = u
        entries.append(entry)
    return entries, True, anomaly


def classify_display(disp_nodes, entry):
    """DISPLAY_STATUS 分类:
    STRUCTURE_UNSUPPORTED / MISSING / EMPTY / MULTIPLE / EXACT_ONE。"""
    if not disp_nodes:
        return "MISSING"
    if len(disp_nodes) > 1:
        return "MULTIPLE"
    text = disp_nodes[0][0]
    if text.strip() == "":
        return "EMPTY"
    return "EXACT_ONE"


def forensic_stripclub(root, text):
    """StripClubDanceAnimationPackage forensic-only: 不套 WW selector, 只描述结构。
    返回 dict: root_class, entry_list_field, entry_count, display_candidates, structure_note。"""
    info = {
        "root_class": root.get("c"),
        "entry_list_field": None,
        "entry_count": 0,
        "display_candidates": [],
        "structure_note": "",
    }
    # 找包含多个 <U> 的 <L> 列表 (registration entry list), 和含 display-like 字段的节点
    candidate_lists = []
    for node in root.iter():
        if _el_tag(node) == "L":
            us = _children_with_tag(node, "U")
            if len(us) >= 1:
                candidate_lists.append((_el_name(node) or "", len(us)))
    if candidate_lists:
        # 取最多 U 的列表作为 entry list
        best = max(candidate_lists, key=lambda x: x[1])
        info["entry_list_field"] = best[0]
        info["entry_count"] = best[1]
    # display-like 候选字段 (仅描述, 不套 selector)
    seen = set()
    for node in root.iter():
        n = _el_name(node)
        if n and ("display" in n or "name" in n) and n not in seen:
            seen.add(n)
            info["display_candidates"].append(n)
    info["structure_note"] = (f"entry_list=<L n={info['entry_list_field']!r}> entries={info['entry_count']}; "
                               f"display_candidates={info['display_candidates']}")
    return info


def scan_package(path: Path) -> PackageResult:
    sha = _sha256(path)
    r = PackageResult(path, sha)
    try:
        idx, err = wb.safe_parse(path)
    except Exception as ex:
        r.parse_status = "PARSE_FAIL"
        r.parse_err = str(ex)
        r.anomalies.append(f"package parse fail: {ex}")
        return r
    if err is not None or idx is None:
        r.parse_status = "PARSE_FAIL"
        r.parse_err = str(err or "None index")
        r.anomalies.append(f"package parse fail: {err}")
        return r

    schema_classes = set()
    for e in idx.entries:
        t = e.type_id
        if t == CLIP_TYPE:
            r.clip_count += 1
            continue
        if t == ANIM_RCOL_TYPE:
            continue
        if t == STBL_TYPE:
            r.stbl_count += 1
            continue
        if t not in (0x00B2D882, 0x545AC2C2, 0x034AEECB, 0x073FAA27, WW_ANIM_XML):
            # 其它未知类型: 不参与 animation 统计 (positive evidence only)
            continue
        if t == WW_ANIM_XML:
            tgis = f"0x{t:08X}/0x{e.group_id:08X}/0x{e.instance_id:016X}"
            r.ww_xml_count += 1
            try:
                body = wb.read_body_raw(path, e)
                raw = wb.decompress_maybe(body)
                text = raw.decode("utf-8", "replace")
            except Exception as ex:
                r.anomalies.append(f"WW XML read/decompress fail {tgis}: {ex}")
                r.ww_xml_map[tgis] = {"schema": "READ_FAIL", "entry_count": 0, "display": 0}
                continue
            schema, root_class, ok = classify_schema(text)
            schema_classes.add(schema)
            if schema == WW_SCHEMA:
                try:
                    entries, sok, anomaly = extract_ww_entries(ET.fromstring(text), text)
                except Exception as ex:
                    r.anomalies.append(f"WW XML structural parse fail {tgis}: {ex}")
                    r.ww_xml_map[tgis] = {"schema": WW_SCHEMA, "entry_count": 0, "display": 0, "anomaly": f"ET fail: {ex}"}
                    continue
                if anomaly:
                    r.anomalies.append(f"[{tgis}] {anomaly}")
                if not sok:
                    # registration 被识别但结构异常: 记 STRUCTURE_UNSUPPORTED 占位 entry
                    placeholder = {
                        "entry_ordinal": 0,
                        "display_nodes": [],
                        "display_status": "STRUCTURE_UNSUPPORTED",
                        "animation_raw_display_name": "",
                        "actors": [],
                        "actor_count": 0,
                        "structure_note": anomaly,                 }
                else:
                    placeholder = None
                if placeholder is not None:
                    entries = [placeholder]
                for en in entries:
                    en["_tgi"] = tgis
                    en["_schema"] = schema
                    en["source_path"] = str(path)
                    en["source_filename"] = r.filename
                    en["source_sha256"] = sha
                    en["ww_xml_tgi"] = tgis
                    en["schema_class"] = schema
                    r.entries.append(en)
                    r.animation_entry_count += 1
                    if en["display_status"] == "EXACT_ONE":
                        r.exact_display_count += 1
                r.ww_xml_map[tgis] = {
                    "schema": schema,
                    "entry_count": len(entries),
                    "display": sum(1 for en_ in entries if en_["display_status"] == "EXACT_ONE"),
                    "anomaly": anomaly,
                }
            elif schema == SC_SCHEMA:
                fc = forensic_stripclub(ET.fromstring(text), text)
                fc["_tgi"] = tgis
                fc["source_path"] = str(path)
                r.stripclub_forensic.append(fc)
                r.ww_xml_map[tgis] = {"schema": SC_SCHEMA, "entry_count": 0, "display": 0}
            elif schema == UNKNOWN:
                r.ww_xml_map[tgis] = {"schema": UNKNOWN, "entry_count": 0, "display": 0}
            else:
                r.ww_xml_map[tgis] = {"schema": schema, "entry_count": 0, "display": 0}

    r.schema_classes = sorted(schema_classes)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mods-root", required=True)
    ap.add_argument("--out-dir", default="output/ww_animation_corpus_v1")
    a = ap.parse_args()
    root = Path(a.mods_root)
    if not root.is_dir():
        print("ERROR: mods root 不存在", file=sys.stderr)
        return 2
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    packages = sorted(root.rglob("*.package")) + sorted(root.rglob("*.PACKAGE"))
    seen = {}
    for p in packages:
        seen[str(p)] = _sha256(p)
    pkgs = list(seen.keys())
    results = []
    for p in pkgs:
        results.append(scan_package(Path(p)))

    # 汇总
    ww_packages = [r for r in results if r.ww_xml_count > 0]
    ww_xml_count = sum(r.ww_xml_count for r in ww_packages)
    anim_entry_count = sum(r.animation_entry_count for r in ww_packages)

    schema_counter = Counter()
    for r in ww_packages:
        for s in r.schema_classes:
            schema_counter[s] += 1
    schema_ww = schema_counter[WW_SCHEMA]
    schema_sc = schema_counter[SC_SCHEMA]
    schema_unk = schema_counter[UNKNOWN]

    display_counter = Counter()
    display_entries = [en for r in ww_packages for en in r.entries]
    for en in display_entries:
        display_counter[en["display_status"]] += 1

    # TGI 全库 map (跨 package)
    tgi_global = defaultdict(list)  # tgi_str -> [(path, schema, entry_count)]
    for r in ww_packages:
        for tgi_str, info in r.ww_xml_map.items():
            tgi_global[tgi_str].append((str(r.path), info["schema"], info["entry_count"]))

    unique_tgi = 0
    dup_same_pkg = []
    cross_collision = []
    for tgi_str, lst in tgi_global.items():
        packages = set(p for p, _, _ in lst)
        if len(lst) == 1:
            unique_tgi += 1
        elif len(packages) == 1:
            dup_same_pkg.append(tgi_str)
        else:
            cross_collision.append((tgi_str, sorted(packages)))

    # display 文本频率
    disp_text_counter = Counter(en["animation_raw_display_name"].strip() for en in display_entries if en["display_status"] == "EXACT_ONE")
    disp_text_pkg = defaultdict(set)
    for en in display_entries:
        if en["display_status"] == "EXACT_ONE":
            disp_text_pkg[en["animation_raw_display_name"].strip()].add(en["source_path"])
    # token 频率
    token_counter = Counter()
    for txt in disp_text_counter:
        for tok in re.findall(r"[A-Za-z0-9_]+", txt):
            token_counter[tok.lower()] += disp_text_counter[txt]

    # 异常收集
    anomalies = []
    for r in results:
        for an in r.anomalies:
            anomalies.append((str(r.path), an))
    # display 重复 (同 package / 全库)
    pkg_disp = defaultdict(Counter)
    for en in display_entries:
        pkg_disp[en["source_path"]][en["animation_raw_display_name"].strip()] += 1
    for p, c in pkg_disp.items():
        for txt, n in c.items():
            if n > 1 and txt:
                anomalies.append((p, f"duplicate display name within package ({n}x): {txt}"))
    for txt, n in disp_text_counter.items():
        if n > 1:
            anomalies.append(("GLOBAL", f"duplicate display name across corpus ({n}x): {txt}"))

    # 写入 CSV
    _write_package_inventory(out_dir, results, ww_packages)
    _write_animation_entries(out_dir, display_entries)
    _write_schema_summary(out_dir, schema_counter, results)
    _write_tgi_collision(out_dir, tgi_global, dup_same_pkg, cross_collision)
    _write_anomalies(out_dir, anomalies)
    _write_freq(out_dir, disp_text_counter, disp_text_pkg, token_counter)

    # 推荐第二个 canary
    candidates = _recommend_candidates(ww_packages, display_entries, cross_collision)

    # summary.txt
    _write_summary(out_dir, {
        "package_scanned": len(pkgs),
        "ww_package_count": len(ww_packages),
        "ww_xml_count": ww_xml_count,
        "anim_entry_count": anim_entry_count,
        "schema_counter": schema_counter,
        "display_counter": display_counter,
        "unique_tgi": unique_tgi,
        "dup_same_pkg": dup_same_pkg,
        "cross_collision": cross_collision,
        "anomaly_count": len(anomalies),
        "translation_candidate": sum(1 for en in display_entries if en["display_status"] == "EXACT_ONE"),
        "stripclub_forensic": [fc for r in ww_packages for fc in r.stripclub_forensic],
        "candidates": candidates,
        "parse_fail": sum(1 for r in results if r.parse_status != "OK"),
    })

    rec = candidates[0] if candidates else None
    # stdout compact summary
    print("WW_ANIMATION_CORPUS_V1:")
    print(f"PACKAGE_FILES_SCANNED={len(pkgs)}")
    print(f"WW_PACKAGE_COUNT={len(ww_packages)}")
    print(f"WW_XML_COUNT={ww_xml_count}")
    print(f"ANIMATION_ENTRY_COUNT={anim_entry_count}")
    print("SCHEMA_COUNTS:")
    print(f"WickedWhimsAnimationPackage={schema_ww}")
    print(f"StripClubDanceAnimationPackage={schema_sc}")
    print(f"UNKNOWN={schema_unk}")
    print("DISPLAY_STATUS:")
    for st in ("EXACT_ONE", "MISSING", "EMPTY", "MULTIPLE", "STRUCTURE_UNSUPPORTED"):
        print(f"{st}={display_counter.get(st, 0)}")
    print(f"UNIQUE_WW_TGI={unique_tgi}")
    print(f"DUPLICATE_SAME_PACKAGE_TGI={len(dup_same_pkg)}")
    print(f"CROSS_PACKAGE_COLLISION_TGI={len(cross_collision)}")
    print(f"PARSE_FAIL={sum(1 for r in results if r.parse_status != 'OK')}")
    print(f"ANOMALY_COUNT={len(anomalies)}")
    print(f"TRANSLATION_CANDIDATE_ENTRY_COUNT={sum(1 for en in display_entries if en['display_status'] == 'EXACT_ONE')}")
    print(f"RECOMMENDED_SECOND_CANARY={rec['path'] if rec else 'NONE'}")
    print(f"RECOMMENDED_SECOND_CANARY_ENTRY_COUNT={rec['entry_count'] if rec else 0}")
    print("STRIPCLUB_PRODUCTION_READY=NO")
    print("ZERO_WRITE_TO_MODS=YES")
    return 0


def _recommend_candidates(ww_packages, display_entries, cross_collision):
    """推荐 3 个与 MSWD_FORCE_FLOOR_002 不同的候选 (WW, entry 多, EXACT_ONE 多, 无 collision, parser clean)。"""
    collision_pkgs = set()
    for _tgi, pkgs in cross_collision:
        collision_pkgs.update(pkgs)
    scored = []
    for r in ww_packages:
        if WW_SCHEMA not in r.schema_classes:
            continue
        if str(r.path) in collision_pkgs:
            continue
        if r.parse_status != "OK":
            continue
        if r.animation_entry_count < 10:
            continue
        exact = r.exact_display_count
        if exact == 0:
            continue
        # functional/object mod heuristic: 无 WW XML 的 WW 包很少见; 这里主要靠 schema
        ratio = exact / r.animation_entry_count
        score = (r.animation_entry_count, ratio, exact)
        scored.append((score, r))
    scored.sort(key=lambda x: (-x[0][0], -x[0][1]))
    cands = []
    for i, (_, r) in enumerate(scored[:3]):
        cands.append({
            "rank": i + 1,
            "path": str(r.path),
            "sha": r.sha,
            "schema": WW_SCHEMA,
            "ww_xml_count": r.ww_xml_count,
            "entry_count": r.animation_entry_count,
            "exact_display_count": r.exact_display_count,
            "anomaly_count": len(r.anomalies),
            "why": f"{r.animation_entry_count} entries, {r.exact_display_count} EXACT_ONE, parser clean, no TGI collision",
        })
    return cands


def _write_package_inventory(out_dir, results, ww_packages):
    rows = []
    for r in results:
        rows.append({
            "source_path": str(r.path),
            "source_sha256": r.sha,
            "ww_identity": "YES" if r.ww_xml_count > 0 else "NO",
            "schema_classes": "|".join(r.schema_classes) or "",
            "ww_xml_count": r.ww_xml_count,
            "animation_entry_count": r.animation_entry_count,
            "clip_count": r.clip_count,
            "stbl_count": r.stbl_count,
            "parse_status": r.parse_status,
        })
    _csv(out_dir / "package_inventory.csv", rows)


def _write_animation_entries(out_dir, display_entries):
    rows = []
    for en in display_entries:
        rows.append({
            "source_path": en["source_path"],
            "source_sha256": en["source_sha256"],
            "ww_xml_tgi": en["ww_xml_tgi"],
            "schema_class": en["schema_class"],
            "entry_ordinal": en.get("entry_ordinal", ""),
            "animation_raw_display_name": en.get("animation_raw_display_name", ""),
            "display_status": en.get("display_status", ""),
            "animation_author": _fmt(en.get("animation_author")),
            "animation_locations": _fmt(en.get("animation_locations")),
            "animation_custom_locations": _fmt(en.get("animation_custom_locations")),
            "animation_category": _fmt(en.get("animation_category")),
            "animation_tags": _fmt(en.get("animation_tags")),
            "animation_loops": _fmt(en.get("animation_loops")),
            "animation_allowed_for_random": _fmt(en.get("animation_allowed_for_random")),
            "actor_count": en.get("actor_count", 0),
            "actor_id": _fmt([a.get("actor_id") for a in en.get("actors", [])]),
            "animation_clip_name": _fmt([a.get("animation_clip_name") for a in en.get("actors", [])]),
            "animation_type": _fmt([a.get("animation_type") for a in en.get("actors", [])]),
            "animation_genders": _fmt([a.get("animation_genders") for a in en.get("actors", [])]),
        })
    _csv(out_dir / "animation_entries.csv", rows)


def _write_schema_summary(out_dir, schema_counter, results):
    rows = []
    total_pkg = len(results)
    for s in sorted(set(list(schema_counter.keys()) + [WW_SCHEMA, SC_SCHEMA, UNKNOWN])):
        rows.append({
            "schema_class": s,
            "package_count": schema_counter.get(s, 0),
            "xml_count": sum(1 for r in results if s in r.schema_classes),
            "entry_count": sum(r.animation_entry_count for r in results if s in r.schema_classes),
            "production_ready": "YES" if s == WW_SCHEMA else "NO",
        })
    _csv(out_dir / "schema_summary.csv", rows)
    # StripClub forensic-only 明细 (独立文件)
    sc_rows = []
    for r in results:
        for fc in r.stripclub_forensic:
            sc_rows.append({
                "source_path": fc["source_path"],
                "ww_xml_tgi": fc["_tgi"],
                "root_class": fc["root_class"],
                "entry_list_field": fc["entry_list_field"],
                "entry_count": fc["entry_count"],
                "display_candidates": "|".join(fc["display_candidates"]),
                "structure_note": fc["structure_note"],
            })
    if sc_rows:
        _csv(out_dir / "stripclub_forensic.csv", sc_rows)


def _write_tgi_collision(out_dir, tgi_global, dup_same_pkg, cross_collision):
    rows = []
    for tgi_str, lst in tgi_global.items():
        pkgs = set(p for p, _, _ in lst)
        if len(lst) == 1:
            cls = "UNIQUE"
        elif len(pkgs) == 1:
            cls = "DUPLICATE_SAME_PACKAGE"
        else:
            cls = "CROSS_PACKAGE_COLLISION"
        rows.append({
            "ww_xml_tgi": tgi_str,
            "source_count": len(lst),
            "source_paths": "|".join(sorted(pkgs)),
            "schema_classes": "|".join(sorted(set(s for _, s, _ in lst))),
            "classification": cls,
        })
    _csv(out_dir / "tgi_collision_report.csv", rows)


def _write_anomalies(out_dir, anomalies):
    rows = [{"source_path": p, "anomaly": a} for p, a in anomalies]
    _csv(out_dir / "anomalies.csv", rows)


def _write_freq(out_dir, disp_text_counter, disp_text_pkg, token_counter):
    rows = [{
        "display_text": txt,
        "occurrence_count": n,
        "package_count": len(disp_text_pkg[txt]),
    } for txt, n in disp_text_counter.most_common()]
    _csv(out_dir / "display_text_frequency.csv", rows)
    rows2 = [{"token": tok, "occurrence_count": n if False else cnt}
             for tok, cnt in token_counter.most_common()]
    _csv(out_dir / "display_token_frequency.csv", rows2)


def _write_summary(out_dir, d):
    lines = []
    lines.append("WW_ANIMATION_CORPUS_V1 SUMMARY")
    lines.append(f"PACKAGE_FILES_SCANNED={d['package_scanned']}")
    lines.append(f"WW_PACKAGE_COUNT={d['ww_package_count']}")
    lines.append(f"WW_XML_COUNT={d['ww_xml_count']}")
    lines.append(f"ANIMATION_ENTRY_COUNT={d['anim_entry_count']}")
    lines.append("SCHEMA_COUNTS:")
    for s, n in d["schema_counter"].items():
        lines.append(f"  {s}={n}")
    lines.append("DISPLAY_STATUS:")
    for st in ("EXACT_ONE", "MISSING", "EMPTY", "MULTIPLE", "STRUCTURE_UNSUPPORTED"):
        lines.append(f"  {st}={d['display_counter'].get(st, 0)}")
    lines.append(f"UNIQUE_WW_TGI={d['unique_tgi']}")
    lines.append(f"DUPLICATE_SAME_PACKAGE_TGI={len(d['dup_same_pkg'])}")
    lines.append(f"CROSS_PACKAGE_COLLISION_TGI={len(d['cross_collision'])}")
    for tgi, pkgs in d["cross_collision"]:
        lines.append(f"  COLLISION {tgi}: {', '.join(pkgs)}")
    lines.append(f"PARSE_FAIL={d['parse_fail']}")
    lines.append(f"ANOMALY_COUNT={d['anomaly_count']}")
    lines.append(f"TRANSLATION_CANDIDATE_ENTRY_COUNT={d['translation_candidate']}")
    if d.get('stripclub_forensic'):
        lines.append("STRIPCLUB_FORENSIC (只描述, 不套 WW selector, 不判 display 安全):")
        for fc in d['stripclub_forensic']:
            lines.append(f"  {fc['source_path']}")
            lines.append(f"    tgi={fc['_tgi']} root_class={fc['root_class']} entry_list_field={fc['entry_list_field']} entry_count={fc['entry_count']}")
            lines.append(f"    display_candidates={fc['display_candidates']}")
            lines.append(f"    structure_note={fc['structure_note']}")
        lines.append("  -> STRIPCLUB_PRODUCTION_READY=NO (需独立真机证据方生产放行)")
    lines.append("SECOND_CANARY_CANDIDATES:")
    for c in d["candidates"]:
        lines.append(f"{c['rank']}. path={c['path']}")
        lines.append(f"   sha={c['sha']}")
        lines.append(f"   schema={c['schema']} ww_xml_count={c['ww_xml_count']} animation_entry_count={c['entry_count']} exact_display_count={c['exact_display_count']} anomaly_count={c['anomaly_count']}")
        lines.append(f"   why_selected={c['why']}")
    lines.append(f"RECOMMENDED_SECOND_CANARY={d['candidates'][0]['path'] if d['candidates'] else 'NONE'}")
    lines.append("STRIPCLUB_PRODUCTION_READY=NO")
    lines.append("ZERO_WRITE_TO_MODS=YES")
    (out_dir / "survey_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, list):
        return "|".join(str(x) if x is not None else "" for x in v)
    return str(v)


def _csv(path, rows):
    if not rows:
        (path).write_text("", encoding="utf-8")
        return
    cols = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    sys.exit(main())
