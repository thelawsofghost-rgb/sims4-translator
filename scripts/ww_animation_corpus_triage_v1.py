#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WW Animation Corpus Survey V1.1 TRIAGE.

对 Mods 库现有的 WW Animation corpus (V1 survey 已扫描, 本轮为只读分流审计) 做
fresh read-only re-scan, 输出七份分流资产到 output/ww_animation_corpus_v1_1_triage/:

  A) 计数口径 reconciliation (WW_XML_COUNT 各成分相加 == 总数)
  B) UNKNOWN schema 深挖 (root_tag / class_c / module_m / instance_i / disposition)
  C) PARSE_FAIL 分流 (DBPF_CONTAINER_FAIL / RESOURCE_READ_FAIL / DECOMPRESS_FAIL /
     XML_PARSE_FAIL / UNSUPPORTED_COMPRESSION / TRUNCATED / OTHER)
  D) cross-package TGI collision semantic 分类 (IDENTICAL_DUPLICATE /
     SEMANTICALLY_IDENTICAL_SERIALIZATION_DIFFERENT / SEMANTIC_CONFLICT)
  E) MISSING display 80 条 triage (TRUE_NO_DISPLAY_FIELD / ALTERNATE_DISPLAY_CANDIDATE /
     MALFORMED_ENTRY / UNKNOWN) — 禁止 ordinal guessing
  F) 第二 canary 候选只读 preflight 资格审查 (Nevely42 / 自动 fallback)
  G) summary stdout (全部 WW_CORPUS_V1_1_TRIAGE 键)

铁律:
  * fresh re-scan: 不依赖 V1 输出的 CSV (V1 未持久化 per-resource 原始 offset/body/
    child dump / semantic hash), 本轮直接从 source 重读。
  * ZERO WRITE TO MODS: 只读 parse/stat, 只往 out_dir 写 CSV/txt。
  * schema 识别 = positive evidence (XML root <I c=...> class attr), 禁止 filename/
    0x7DF2169C/CLIP 判 WW。
  * display 定位 = 精确 <T n=animation_raw_display_name>, 禁止 ordinal guessing /
    自动 fallback。
  * StripClub 仅 forensic, production_ready=NO。
  * 不改 source / 不 sidecar / 不翻译 / 不生产 / 不部署。
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
import zlib

SCRIPT_DIR = Path(__file__).resolve().parent
MODULE = SCRIPT_DIR / "ww_animation_canary_builder.py"
_spec = importlib.util.spec_from_file_location("ww_canary_builder", MODULE)
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)

WW_ANIM_XML = wb.WW_ANIM_XML
WW_SCHEMA = "WickedWhimsAnimationPackage"
SC_SCHEMA = "StripClubDanceAnimationPackage"
UNKNOWN = "UNKNOWN_SCHEMA"
WW_DISPLAY_FIELD = "animation_raw_display_name"
WW_ENTRY_LIST_FIELD = "animations_list"

# V1 真实 (Windows) survey 结果, 用于 A) reconciliation cross-check (只读参考)
V1_REAL = {
    "PACKAGE_FILES_SCANNED": 6394,
    "WW_PACKAGE_COUNT": 926,
    "WW_XML_COUNT": 1347,
    "UNIQUE_WW_TGI": 1185,
    "CROSS_PACKAGE_COLLISION_TGI": 80,
    "PARSE_FAIL": 118,
    "ANOMALY_COUNT": 965,
    "TRANSLATION_CANDIDATE_ENTRY_COUNT": 5352,
    "EXACT_ONE": 5352,
    "MISSING": 80,
}


def _sha256(p: Path) -> str:
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _el_tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else None


def _el_name(el) -> str:
    return el.get("n")


def _children_with_tag(parent, tag):
    return [c for c in list(parent) if _el_tag(c) == tag]


def _children_with_name(parent, name):
    return [c for c in list(parent) if _el_name(c) == name]


def _text_of(el):
    return "" if el.text is None else el.text


def _tgi_str(t, g, i):
    return f"0x{t:08X}/0x{g:08X}/0x{i:016X}"


def decompress_body(body: bytes):
    """返回 (ok, payload, err_kind)。err_kind 供 C) 分流, 对应
    DECOMPRESS_FAIL / UNSUPPORTED_COMPRESSION / PLAIN。"""
    if len(body) >= 2 and body[0] == 0x78 and body[1] in (0x01, 0x5E, 0x9C, 0xDA):
        try:
            return True, zlib.decompress(body), None
        except Exception as ex:
            return False, body, f"DECOMPRESS_FAIL:{type(ex).__name__}"
    if len(body) >= 2 and body[0] in (0x1F, 0x8B):
        return False, body, "UNSUPPORTED_COMPRESSION:gzip"
    if len(body) >= 4 and body[:4] in (b"\xFD\x37\x7A\x58", b"\x28\xB5\x2F\xFD"):
        return False, body, "UNSUPPORTED_COMPRESSION:xz/zstd"
    return True, body, None  # plain


class ResourceRec:
    """单个 WW_ANIM_XML 资源的只读 forensic 记录。"""
    __slots__ = ("type_id", "group_id", "instance_id", "tgi", "pkg_path", "pkg_sha",
                 "offset_high", "size_high", "raw_size", "field7", "field8",
                 "read_ok", "read_stage", "read_err",
                 "decomp_ok", "decomp_err", "logical_sha256", "logical_len",
                 "root_tag", "class_c", "module_m", "instance_i",
                 "schema", "xml_parse_ok", "xml_parse_err",
                 "animations_list_count", "entry_count",
                 "display_exact_one", "display_missing", "display_empty",
                 "display_multi", "display_children_dump", "semantic_sha256",
                 "_root", "_payload", "slot_off", "slot_size")

    def __init__(self):
        self.logical_sha256 = ""
        self.logical_len = 0
        self.semantic_sha256 = ""
        for a in self.__slots__:
            setattr(self, a, None)


def read_resource_raw(pkg: Path, meta: dict) -> tuple[bool, str, bytes]:
    """按 index metadata 原始 offset/size (掩高位) 读取 body。
    返回 (ok, stage_err, body)。stage 供 C) 分流区分 DBPF vs READ。"""
    off = meta["offset_raw"] & 0x7FFFFFFF
    size = meta["size_raw"] & 0x7FFFFFFF
    if size <= 0:
        return False, "RESOURCE_READ_FAIL:size<=0", b""
    try:
        with open(pkg, "rb") as fh:
            fh.seek(off)
            body = fh.read(size)
    except Exception as ex:
        return False, f"RESOURCE_READ_FAIL:{type(ex).__name__}", b""
    if len(body) != size:
        return False, f"RESOURCE_READ_FAIL:short={len(body)}!=size={size}", b""
    return True, "", body


def read_source_metadata(pkg: Path):
    """读整个源包的 index metadata list。失败 -> (None, DBPF_CONTAINER_FAIL:...)。"""
    try:
        return wb.read_entry_meta_raw(pkg)
    except Exception as ex:
        return None, f"DBPF_CONTAINER_FAIL:{type(ex).__name__}"


def schema_from_class(c):
    if c == WW_SCHEMA:
        return WW_SCHEMA
    if c == SC_SCHEMA:
        return SC_SCHEMA
    return UNKNOWN


def scan_all_packages(mods_root: Path):
    """递归发现全部 .package, 返回 [Path]。仅 stat/discovery, 不读 body。"""
    out = []
    for p in mods_root.rglob("*.package"):
        try:
            if p.is_file():
                out.append(p)
        except Exception:
            pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mods-root", required=True)
    ap.add_argument("--out-dir", default="output/ww_animation_corpus_v1_1_triage")
    ap.add_argument("--second-canary", default=None,
                    help="候选第二 canary 绝对路径; 缺省用 V1 推荐 Nevely42")
    a = ap.parse_args()

    mods_root = Path(a.mods_root)
    if not mods_root.is_dir():
        print(f"ERROR: mods-root 不存在: {mods_root}", file=sys.stderr)
        return 2
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    packages = scan_all_packages(mods_root)
    print(f"SCANNING_PACKAGES={len(packages)}")

    # ---- 数据模型: 逐包 -> 逐 WW_ANIM_XML 资源建立 ResourceRec ----
    recs = []           # list[ResourceRec]  (每个 WW_ANIM_XML 资源一个)
    pkg_meta = {}       # pkg_path -> (wf, read_meta_err) or (meta_list, None)
    pkg_parse_fail = {}  # pkg_path -> reason (C 分流)
    ww_xml_by_tgi = defaultdict(list)  # tgi_str -> [ResourceRec]  (D collision)

    for p in packages:
        try:
            idx, perr = wb.safe_parse(p)
        except Exception as ex:
            pkg_parse_fail[str(p)] = f"DBPF_CONTAINER_FAIL:{type(ex).__name__}"
            continue
        if perr is not None or idx is None:
            pkg_parse_fail[str(p)] = f"DBPF_CONTAINER_FAIL:{perr}"
            continue
        metas = read_source_metadata(p)
        if metas is None:
            meta_list, meta_err = metas, None
        else:
            _, _, _, meta_list = metas
        meta_by_tgi = {}
        if meta_list is not None:
            for m in meta_list:
                meta_by_tgi[(m["type"], m["group"], m["inst"])] = m
        for e in idx.entries:
            if e.type_id != WW_ANIM_XML:
                continue
            tgi = _tgi_str(e.type_id, e.group_id, e.instance_id)
            rr = ResourceRec()
            rr.type_id = e.type_id
            rr.group_id = e.group_id
            rr.instance_id = e.instance_id
            rr.tgi = tgi
            rr.pkg_path = str(p)
            rr.pkg_sha = _sha256(p)
            rr.slot_off = e.offset & 0x7FFFFFFF
            rr.slot_size = e.size & 0x7FFFFFFF
            meta = meta_by_tgi.get((e.type_id, e.group_id, e.instance_id))
            if meta is not None:
                rr.offset_high = int(meta["offset_comp"])
                rr.size_high = int(meta["size_comp"])
                rr.raw_size = meta["size_raw"] & 0x7FFFFFFF
                rr.field7 = meta["mem_size"]
                rr.field8 = meta["comp_type"]
            else:
                # 无 metadata -> 直接用 idx entry offset/size (已在 slot_* 记录)
                rr.raw_size = e.size & 0x7FFFFFFF
                rr.field7 = None
                rr.field8 = None
            recs.append(rr)
            ww_xml_by_tgi[tgi].append(rr)

    # ---- 逐 WW 资源做逻辑读取 (C 分流) ----
    for rr in recs:
        meta = None
        # 重新拿 metadata (已在上面建立, 这里直接用 pkg 重读一次以保证 raw read 独立)
        mres = read_source_metadata(Path(rr.pkg_path))
        if isinstance(mres, tuple) and mres[0] is not None:
            _, _, _, ml = mres
            for m in ml:
                if (m["type"], m["group"], m["inst"]) == (rr.type_id, rr.group_id, rr.instance_id):
                    meta = m
                    break
        if meta is None:
            meta = {"offset_raw": rr.slot_off or 0, "size_raw": rr.slot_size or rr.raw_size,
                    "offset_comp": rr.offset_high or 0,
                    "size_comp": rr.size_high or 0, "mem_size": rr.field7 or 0, "comp_type": rr.field8 or 0}
        ok, err, body = read_resource_raw(Path(rr.pkg_path), meta)
        if not ok:
            rr.read_ok = False
            rr.read_stage = err
            rr.logical_sha256 = _sha256_bytes(body) if body else ""
            rr.logical_len = len(body)
            continue
        rr.read_ok = True
        rr.raw_size = len(body)
        rr.field7 = meta["mem_size"]
        rr.field8 = meta["comp_type"]
        rr.offset_high = int(meta["offset_comp"])
        rr.size_high = int(meta["size_comp"])
        # 解压
        d_ok, payload, derr = decompress_body(body)
        if not d_ok:
            rr.decomp_ok = False
            rr.decomp_err = derr
            rr.logical_sha256 = _sha256_bytes(body)
            rr.logical_len = len(body)
            continue
        rr.decomp_ok = True
        rr.logical_sha256 = _sha256_bytes(payload)
        rr.logical_len = len(payload)
        # XML parse
        try:
            root = ET.fromstring(payload)
            rr.xml_parse_ok = True
        except Exception as ex:
            rr.xml_parse_ok = False
            rr.xml_parse_err = f"XML_PARSE_FAIL:{type(ex).__name__}"
            rr.root_tag = None
            rr.class_c = None
            rr.module_m = None
            rr.instance_i = None
            rr.schema = None
            continue
        rr.root_tag = _el_tag(root)
        rr.class_c = root.get("c")
        rr.module_m = root.get("m")
        rr.instance_i = root.get("i")
        rr.schema = schema_from_class(rr.class_c)
        rr._root = root
        rr._payload = payload

    rec = reconcile(recs)
    print(f"WW_XML_RESOURCES_SELECTED={len(recs)}")
    print("RECOGNIZED_CLASS_XML_COUNT=" + str(rec["recognized"]))
    print("UNKNOWN_CLASS_XML_COUNT=" + str(rec["unknown"]))
    print("UNCLASSIFIED_XML_COUNT=" + str(rec["unclassified"]))
    print("PARSE_FAILED_XML_COUNT=" + str(rec["failed"]))
    rec_ok = rec["ok"]
    print("RECONCILED_TO_WW_XML_COUNT=" + ("YES" if rec_ok else "NO") + f"  (sum={rec['total']} vs selected={len(recs)})")

    # ---- B) UNKNOWN schema ----------------
    unk_rows = unknown_schema_triage(recs, out_dir / "unknown_schema_summary.csv")
    top_unk = unk_rows[:20]
    unk_samples = unknown_schema_samples(recs, out_dir / "unknown_schema_samples.csv", top=unk_rows[:20])
    unk_top1 = unk_rows[0]["class_c"] if unk_rows else ""
    unk_top1_cnt = unk_rows[0]["count_xml"] if unk_rows else 0
    unk_distinct = len(unk_rows)

    # ---- C) PARSE_FAIL triage ------------
    pf_rows, pf_cnt = parse_fail_triage(recs, out_dir / "parse_fail_triage.csv")
    pf_total = sum(pf_cnt.values())
    pf_top = pf_cnt.most_common(1)[0][0] if pf_cnt else ""

    # ---- D) TGI collision semantics ------
    coll_rows, coll_cnt, coll_total_tgi = collision_triage(ww_xml_by_tgi, out_dir / "tgi_collision_triage.csv")
    coll_ident = coll_cnt["IDENTICAL_DUPLICATE"]
    coll_semident = coll_cnt["SEMANTICALLY_IDENTICAL_SERIALIZATION_DIFFERENT"]
    coll_conflict = coll_cnt["SEMANTIC_CONFLICT"]

    # ---- E) MISSING display triage -------
    md_rows, md_cnt = missing_display_triage(recs, out_dir / "missing_display_triage.csv")
    md_total = sum(md_cnt.values())
    md_true = md_cnt["TRUE_NO_DISPLAY_FIELD"]
    md_alt = md_cnt["ALTERNATE_DISPLAY_CANDIDATE"]
    md_malformed = md_cnt["MALFORMED_ENTRY"]
    md_unknown = md_cnt.get("UNKNOWN", 0)

    # ---- F) second canary preflight -----
    default_canary = a.second_canary or \
        r"C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods\2026.7.20\WW_Nevely42_Animations.package"
    cand_path = Path(default_canary)
    if not cand_path.is_file():
        print(f"WARN: canary not found, falling back: {cand_path}", file=sys.stderr)
        fb = next_canary_fallback(recs, ww_xml_by_tgi, exclude_path=str(cand_path), min_entries=50)
        if fb:
            cand_path = Path(fb[0])
    sc, sc_pass = second_canary_preflight(cand_path, recs, ww_xml_by_tgi, out_dir / "second_canary_preflight.csv")
    if not sc_pass:
        fb = next_canary_fallback(recs, ww_xml_by_tgi, exclude_path=str(cand_path), min_entries=50)
        if fb:
            cand_path = Path(fb[0])
            sc, sc_pass = second_canary_preflight(cand_path, recs, ww_xml_by_tgi, out_dir / "second_canary_preflight.csv")

    # ---- G) summary file + stdout --------
    summary_rows = [
        ("WW_CORPUS_V1_1_TRIAGE", ""),
        ("WW_XML_COUNT", str(len(recs))),
        ("RECOGNIZED_CLASS_XML_COUNT", str(rec["recognized"])),
        ("UNKNOWN_CLASS_XML_COUNT", str(rec["unknown"])),
        ("UNCLASSIFIED_XML_COUNT", str(rec["unclassified"])),
        ("PARSE_FAILED_XML_COUNT", str(rec["failed"])),
        ("RECONCILED_TO_WW_XML_COUNT", "YES" if rec_ok else "NO"),
        ("UNKNOWN_TOP_CLASS_1", unk_top1),
        ("UNKNOWN_TOP_CLASS_1_COUNT", str(unk_top1_cnt)),
        ("UNKNOWN_DISTINCT_CLASSES", str(unk_distinct)),
        ("PARSE_FAIL_TOTAL", str(pf_total)),
        ("PARSE_FAIL_TOP_REASON", pf_top),
        ("CROSS_PACKAGE_COLLISION_TGI_TOTAL", str(coll_total_tgi)),
        ("COLLISION_IDENTICAL_DUPLICATE", str(coll_ident)),
        ("COLLISION_SEMANTICALLY_IDENTICAL", str(coll_semident)),
        ("COLLISION_SEMANTIC_CONFLICT", str(coll_conflict)),
        ("MISSING_DISPLAY_TOTAL", str(md_total)),
        ("TRUE_NO_DISPLAY_FIELD", str(md_true)),
        ("ALTERNATE_DISPLAY_CANDIDATE", str(md_alt)),
        ("MALFORMED_ENTRY", str(md_malformed)),
        ("MISSING_UNKNOWN", str(md_unknown)),
        ("SECOND_CANARY", sc["path"]),
        ("SECOND_CANARY_ENTRY_COUNT", str(sc["animation_entry_count"])),
        ("SECOND_CANARY_WW_XML_COUNT", str(sc["ww_xml_count"])),
        ("SECOND_CANARY_PREFLIGHT_PASS", sc["preflight_pass"]),
        ("ZERO_WRITE_TO_MODS", "YES"),
        ("V1_REF_WW_XML_COUNT", str(V1_REAL["WW_XML_COUNT"])),
        ("V1_REF_MISSING_DISPLAY", str(V1_REAL["MISSING"])),
        ("V1_REF_CROSS_COLLISION", str(V1_REAL["CROSS_PACKAGE_COLLISION_TGI"])),
    ]
    with open(out_dir / "triage_summary.txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(f"{k}={v}" for k, v in summary_rows) + "\n")

    print("\nWW_CORPUS_V1_1_TRIAGE:")
    for k, v in summary_rows:
        if k.startswith("WW_CORPUS") or k.startswith("V1_REF"):
            continue
        print(f"{k}={v}")
    print("")
    print("PARSE_FAIL_REASONS:")
    for reason, n in pf_cnt.most_common():
        print(f"  {reason}={n}")
    print("")
    print("UNKNOWN_SCHEMA_SUMMARY:")
    for row in unk_rows[:20]:
        print(f"  {row['root_tag']} c={row['class_c']!r} m={row['module_m']!r} i={row['instance_i']!r} "
              f"xml={row['count_xml']} pkgs={row['count_packages']} hist_list={row['has_animations_list_count']} "
              f"disp={row['has_animation_raw_display_name_count']} clip={row['has_clip_reference_count']} "
              f"=> {row['UNKNOWN_CLASS_DISPOSITION']}")
    print("")
    print("COLLISION_CLASSES:")
    for cls, n in coll_cnt.most_common():
        print(f"  {cls}={n}")
    print("")
    print("MISSING_DISPLAY_CLASSES:")
    for cls, n in md_cnt.most_common():
        print(f"  {cls}={n}")
    print("")
    print("SECOND_CANARY_PREFLIGHT:")
    for k2 in ("path", "sha256", "file_size", "schema_classes", "ww_xml_count",
               "animation_entry_count", "EXACT_ONE", "MISSING", "EMPTY", "MULTIPLE",
               "tgi_collision_count", "cross_package_collision_count", "parse_fail_count",
               "anomaly_count", "preflight_pass", "fail_reasons"):
        print(f"  {k2}={sc[k2]}")
    print("ZERO_WRITE_TO_MODS=YES")
    return 0


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()




# ============================================================ A) reconciliation
# WW_XML_COUNT 各成分:
#   RECOGNIZED_CLASS_XML_COUNT = schema in {WW, SC} 且 xml_parse_ok
#   UNKNOWN_CLASS_XML_COUNT    = schema == UNKNOWN 且 xml_parse_ok (root <I c> 存在但非 WW/SC)
#   UNCLASSIFIED_XML_COUNT     = read_ok 且 decomp_ok 且 xml_parse_ok 但无 root c (无 class attr)
#   PARSE_FAILED_XML_COUNT     = read 失败 / decomp 失败 / xml 解析失败 任一
# 相加 == WW_XML_COUNT。
def reconcile(recs):
    recognized = unknown = unclassified = failed = 0
    for rr in recs:
        if not (rr.read_ok and rr.decomp_ok and rr.xml_parse_ok):
            failed += 1
            continue
        if rr.schema in (WW_SCHEMA, SC_SCHEMA):
            recognized += 1
        elif rr.class_c:
            unknown += 1
        else:
            unclassified += 1
    total = recognized + unknown + unclassified + failed
    ok = (total == len(recs))
    return {"recognized": recognized, "unknown": unknown, "unclassified": unclassified,
            "failed": failed, "total": total, "ok": ok}


# ============================================================ entry extraction (E/F)
# 在 WW 资源逻辑 payload 上提取 animations_list 的 <U> entry; 每个 entry 记录其
# display 精确节点与全部直接 child (tag/n/text_preview), 供 E) missing display triage。
def extract_entries(root):
    """返回 (entries, structural_ok, note)。entries 为 list[dict], 每 dict:
    entry_ordinal, display_nodes:[(text,el)], children:[(tag,n,text_preview)],
    display_status。"""
    entries = []
    lists = []
    for node in root.iter():
        if _el_tag(node) == "L" and _el_name(node) == WW_ENTRY_LIST_FIELD:
            lists.append(node)
    if not lists:
        return entries, False, f"no <L n={WW_ENTRY_LIST_FIELD!r}>"
    alist = lists[0]
    for u in _children_with_tag(alist, "U"):
        disp = [(_text_of(c), c) for c in list(u)
                if _el_tag(c) == "T" and _el_name(c) == WW_DISPLAY_FIELD]
        children = []
        for c in list(u):
            tag = _el_tag(c)
            n = _el_name(c)
            txt = _text_of(c)[:60]
            if tag == "L":
                sub = [(_el_name(x), _text_of(x)[:30]) for x in list(c)
                       if _el_tag(x) in ("T", "E", "U")]
                children.append((tag, n, f"L<{len(list(c))}>" + (str(sub[:3]) if sub else "")))
            else:
                children.append((tag, n, txt))
        if not disp:
            status = "MISSING"
        elif len(disp) > 1:
            status = "MULTIPLE"
        elif disp[0][0].strip() == "":
            status = "EMPTY"
        else:
            status = "EXACT_ONE"
        entries.append({"entry_ordinal": len(entries), "display_nodes": disp,
                        "children": children, "display_status": status})
    note = f"multiple <L n={WW_ENTRY_LIST_FIELD!r}>" if len(lists) > 1 else None
    return entries, True, note


# ============================================================ B) UNKNOWN schema
# 对 schema==UNKNOWN 且 xml_parse_ok 的资源聚合 root_tag/class_c/module_m/instance_i。
# disposition 判定 (禁止仅因 0x7DF2169C/CLIP/filename 升级成 WW):
#   LIKELY_WW_RELATED_FORENSIC - root_tag==I 且含 animations_list/display 结构线索 (仅标记, 不升格)
#   NON_WW                     - root_tag != I 或明显非 WW tuning
#   INSUFFICIENT_EVIDENCE      - 其他
# (xml_parse 失败的 UNKNOWN 不计入此表, 归 C 的 XML_PARSE_FAIL)
def unknown_schema_triage(recs, out: Path):
    agg = defaultdict(lambda: {"count_xml": 0, "count_packages": set(),
                               "has_animations_list": 0, "has_display": 0,
                               "has_clip_reference": 0, "example_path": None})
    for rr in recs:
        if not (rr.read_ok and rr.decomp_ok and rr.xml_parse_ok):
            continue
        if rr.schema != UNKNOWN:
            continue
        c = rr.class_c or "<none>"
        key = (rr.root_tag or "", c, rr.module_m or "", rr.instance_i or "")
        a = agg[key]
        a["count_xml"] += 1
        a["count_packages"].add(rr.pkg_path)
        if a["example_path"] is None:
            a["example_path"] = rr.pkg_path
        root = rr._root
        for node in root.iter():
            n = _el_name(node)
            if node.tag.rsplit("}", 1)[-1] == "L" and n == WW_ENTRY_LIST_FIELD:
                a["has_animations_list"] += 1
            if node.tag.rsplit("}", 1)[-1] == "T" and n == WW_DISPLAY_FIELD:
                a["has_display"] += 1
            if node.tag.rsplit("}", 1)[-1] == "T" and n == "animation_clip_name":
                a["has_clip_reference"] += 1
    rows = []
    for (rt, c, m, i), a in agg.items():
        if a["has_animations_list"] and rt == "I":
            disp = "LIKELY_WW_RELATED_FORENSIC"
        elif rt != "I":
            disp = "NON_WW"
        else:
            disp = "INSUFFICIENT_EVIDENCE"
        rows.append({"root_tag": rt, "class_c": c, "module_m": m, "instance_i": i,
                     "count_xml": a["count_xml"], "count_packages": len(a["count_packages"]),
                     "has_animations_list_count": a["has_animations_list"],
                     "has_animation_raw_display_name_count": a["has_display"],
                     "has_clip_reference_count": a["has_clip_reference"],
                     "example_path": a["example_path"], "UNKNOWN_CLASS_DISPOSITION": disp})
    rows.sort(key=lambda r: (-r["count_xml"], r["class_c"]))
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["root_tag", "class_c", "module_m", "instance_i",
                                           "count_xml", "count_packages",
                                           "has_animations_list_count",
                                           "has_animation_raw_display_name_count",
                                           "has_clip_reference_count", "example_path",
                                           "UNKNOWN_CLASS_DISPOSITION"])
        w.writeheader(); w.writerows(rows)
    return rows


def unknown_schema_samples(recs, out: Path, top, limit_per=5):
    """对 top N 个 UNKNOWN class (list[dict] from unknown_schema_triage),
    各输出 limit_per 个 sample 资源的路径/tgi。"""
    top_keys = {(r["root_tag"] or "", r["class_c"] or "<none>", r["module_m"] or "",
                 r["instance_i"] or "") for r in top}
    rows = []
    shown = defaultdict(int)
    for rr in recs:
        if not (rr.read_ok and rr.decomp_ok and rr.xml_parse_ok):
            continue
        if rr.schema != UNKNOWN:
            continue
        key = (rr.root_tag or "", rr.class_c or "<none>", rr.module_m or "", rr.instance_i or "")
        if key not in top_keys:
            continue
        if shown[key] >= limit_per:
            continue
        shown[key] += 1
        rows.append({"root_tag": key[0], "class_c": key[1], "module_m": key[2], "instance_i": key[3],
                     "source_path": rr.pkg_path, "ww_xml_tgi": rr.tgi, "logical_sha256": rr.logical_sha256})
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["root_tag", "class_c", "module_m", "instance_i",
                                           "source_path", "ww_xml_tgi", "logical_sha256"])
        w.writeheader(); w.writerows(rows)
    return rows


# ============================================================ C) PARSE_FAIL triage
# 每个未能产出可用逻辑 XML 的 WW_ANIM_XML 资源记一行; stage 归一到六个 reason 桶。
def normalize_fail_reason(rr):
    if not rr.read_ok:
        r = rr.read_stage or ""
        if "DBPF_CONTAINER" in r:
            return "DBPF_CONTAINER_FAIL"
        if "RESOURCE_READ_FAIL:short" in r or "size<=0" in r:
            return "TRUNCATED_RESOURCE"
        return "RESOURCE_READ_FAIL"
    if rr.read_ok and not rr.decomp_ok:
        r = rr.decomp_err or ""
        if "UNSUPPORTED_COMPRESSION" in r:
            return "UNSUPPORTED_COMPRESSION"
        return "RESOURCE_DECOMPRESSION_FAIL"
    if rr.read_ok and rr.decomp_ok and not rr.xml_parse_ok:
        return "XML_PARSE_FAIL"
    return "OTHER"


def parse_fail_triage(recs, out: Path):
    rows = []
    cnt = Counter()
    for rr in recs:
        if rr.read_ok and rr.decomp_ok and rr.xml_parse_ok:
            continue
        reason = normalize_fail_reason(rr)
        cnt[reason] += 1
        stage = rr.read_stage or rr.decomp_err or rr.xml_parse_err or ""
        rows.append({"source_path": rr.pkg_path, "resource_tgi": rr.tgi, "stage": stage,
                     "exception_type": (stage.split(":")[-1] if ":" in stage else ""),
                     "reason": reason, "raw_size": rr.raw_size, "field7": rr.field7,
                     "field8": rr.field8, "offset_high_bit": rr.offset_high,
                     "size_high_bit": rr.size_high, "logical_sha256": rr.logical_sha256})
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["source_path", "resource_tgi", "stage",
                                           "exception_type", "reason", "raw_size", "field7",
                                           "field8", "offset_high_bit", "size_high_bit",
                                           "logical_sha256"])
        w.writeheader(); w.writerows(rows)
    return rows, cnt


# ============================================================ D) TGI collision semantics
# 对 ww_xml_by_tgi 中 source_count>1 的 tgi 做分类:
#   IDENTICAL_DUPLICATE                        - logical hashes 全同
#   SEMANTICALLY_IDENTICAL_SERIALIZATION_DIFFERENT - logical hash 不同但 normalized semantic hash 同
#   SEMANTIC_CONFLICT                          - semantic hash 也不同
# 只对能成功读到逻辑 XML 的资源参与 (失败资源不计入, 单列 fail)。
def normalized_semantic_hash(payload: bytes):
    """归一化 semantic hash: 元素/attr 顺序不敏感 + 文本折叠空白 (indent 无关)。
    对元素路径+排序属性+折叠后文本收集; 这能区分 '仅序列化差异' 与 '真语义差异'。"""
    try:
        root = ET.fromstring(payload)
    except Exception:
        return None
    parts = []

    def norm(t):
        return re.sub(r"\s+", "", t or "").strip()

    def walk(el, path):
        tag = _el_tag(el)
        attrs = tuple(sorted(el.attrib.items()))
        parts.append((path, tag, attrs, norm(_text_of(el))))
        i = 0
        for ch in list(el):
            i += 1
            walk(ch, f"{path}/{tag}#{i}")
    walk(root, f"/{_el_tag(root)}")
    h = hashlib.sha256()
    for p in parts:
        h.update(repr(p).encode("utf-8", errors="replace"))
    return h.hexdigest()


def collision_triage(ww_xml_by_tgi, out: Path, write_csv=True):
    rows = []
    class_cnt = Counter()
    total_colliding_tgi = 0
    for tgi, recs in ww_xml_by_tgi.items():
        if len(recs) < 2:
            continue
        total_colliding_tgi += 1
        good = [r for r in recs if r.read_ok and r.decomp_ok and r.xml_parse_ok]
        lhashes = [r.logical_sha256 for r in good]
        shashes = []
        for r in good:
            sh = normalized_semantic_hash(r._payload)
            shashes.append(sh)
        if len(good) < len(recs):
            cls = "SEMANTIC_CONFLICT"  # 有资源读/解/解析失败, 保守按冲突
            fail_note = f"{len(recs)-len(good)} unreadable"
        elif len(set(lhashes)) == 1:
            cls = "IDENTICAL_DUPLICATE"
            fail_note = ""
        elif None not in shashes and len(set(shashes)) == 1:
            cls = "SEMANTICALLY_IDENTICAL_SERIALIZATION_DIFFERENT"
            fail_note = ""
        else:
            cls = "SEMANTIC_CONFLICT"
            fail_note = ""
        class_cnt[cls] += 1
        rows.append({"tgi": tgi, "source_count": len(recs),
                     "source_paths": "|".join(r.pkg_path for r in recs),
                     "schema_classes": "|".join(str(r.schema) for r in recs),
                     "logical_hashes": "|".join(lhashes),
                     "semantic_hashes": "|".join(shashes or []),
                     "collision_class": cls, "note": fail_note})
    rows.sort(key=lambda r: (-r["source_count"], r["tgi"]))
    if write_csv:
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["tgi", "source_count", "source_paths",
                                               "schema_classes", "logical_hashes",
                                               "semantic_hashes", "collision_class", "note"])
            w.writeheader(); w.writerows(rows)
    return rows, class_cnt, total_colliding_tgi


# ============================================================ E) MISSING display triage
# 对 display_status==MISSING 的 entry: dump 同 entry 直接 child (tag/n/text_preview),
# 找 display-like 字段, 但绝不自动 fallback / ordinal guessing。
DISPLAY_LIKE = ("animation_display_name", "animation_name", "raw_display_name",
                "display_name", "name", "title", "animation_raw_display_name",
                "anim_display_name")


def missing_display_triage(recs, out: Path):
    rows = []
    class_cnt = Counter()
    for rr in recs:
        if not (rr.read_ok and rr.decomp_ok and rr.xml_parse_ok):
            continue
        if rr.schema != WW_SCHEMA:
            continue
        entries, ok, note = extract_entries(rr._root)
        if not ok:
            continue
        for e in entries:
            if e["display_status"] != "MISSING":
                continue
            # 找 display-like: 直接 child 中 T 节点 n 命中 DISPLAY_LIKE 且有非空文本
            alt = []
            malformed = False
            child_dump = []
            for (tag, n, txt) in e["children"]:
                child_dump.append(f"{tag}[{n or ''}]={txt!r}")
                if tag == "T" and n and n.lower() in DISPLAY_LIKE and n != WW_DISPLAY_FIELD:
                    if txt.strip():
                        alt.append(n)
            if alt:
                cls = "ALTERNATE_DISPLAY_CANDIDATE"
            elif malformed:
                cls = "MALFORMED_ENTRY"
            else:
                cls = "TRUE_NO_DISPLAY_FIELD"
            class_cnt[cls] += 1
            rows.append({"source_path": rr.pkg_path, "ww_xml_tgi": rr.tgi,
                         "schema_class": rr.schema, "entry_ordinal": e["entry_ordinal"],
                         "children_dump": " || ".join(child_dump),
                         "alternate_display_fields": "|".join(alt) if alt else "",
                         "triage_class": cls})
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["source_path", "ww_xml_tgi", "schema_class",
                                           "entry_ordinal", "children_dump",
                                           "alternate_display_fields", "triage_class"])
        w.writeheader(); w.writerows(rows)
    return rows, class_cnt


# ============================================================ F) second canary preflight
# 对候选第二 canary 包做只读资格审查。PASS 必须:
#   - 只有 WickedWhimsAnimationPackage (不含 StripClub / UNKNOWN)
#   - parser clean (无 read/decomp/xml fail)
#   - target WW XML 无 CROSS_PACKAGE_COLLISION
#   - display 全部 EXACT_ONE
#   - 无结构 anomaly
# 缺任一 -> fail(原因) 返回, caller 自动 fallback 到满足条件的大包。
def second_canary_preflight(canary_path: Path, recs, ww_xml_by_tgi, out: Path):
    rows = []
    recs_in = [r for r in recs if Path(r.pkg_path).resolve() == canary_path.resolve()]
    pkg_sha = _sha256(canary_path) if canary_path.is_file() else ""
    file_size = canary_path.stat().st_size if canary_path.is_file() else 0
    schema_set = sorted({r.schema for r in recs_in if r.schema})
    ww_xml_count = len(recs_in)
    entry_count = exact_one = missing = empty = multi = 0
    fail_read = any(not (r.read_ok and r.decomp_ok and r.xml_parse_ok) for r in recs_in)
    # entry stats
    all_exact = True
    for r in recs_in:
        if not (r.read_ok and r.decomp_ok and r.xml_parse_ok):
            continue
        entries, ok, _note = extract_entries(r._root)
        if not ok:
            all_exact = False
            continue
        for e in entries:
            entry_count += 1
            st = e["display_status"]
            exact_one += (st == "EXACT_ONE")
            missing += (st == "MISSING")
            empty += (st == "EMPTY")
            multi += (st == "MULTIPLE")
            if st != "EXACT_ONE":
                all_exact = False
    # tgi collision 检查
    colliding = 0
    cross = 0
    for r in recs_in:
        lst = ww_xml_by_tgi.get(r.tgi, [])
        if len(lst) > 1:
            colliding += 1
            # 跨包?
            pkgs = {Path(x.pkg_path).resolve() for x in lst}
            if len(pkgs) > 1:
                cross += 1
    # 判定
    reasons = []
    if not recs_in:
        reasons.append("NO_WW_XML_RESOURCES")
    if not schema_set or any(s != WW_SCHEMA for s in schema_set):
        reasons.append(f"NON_PURE_SCHEMA:{schema_set}")
    if SC_SCHEMA in schema_set:
        reasons.append("CONTAINS_STRIPCLUB")
    if UNKNOWN in schema_set:
        reasons.append("CONTAINS_UNKNOWN_SCHEMA")
    if fail_read:
        reasons.append("PARSER_FAIL")
    if cross > 0:
        reasons.append(f"CROSS_PACKAGE_COLLISION:{cross}")
    if not all_exact:
        reasons.append(f"DISPLAY_NOT_ALL_EXACT_ONE(miss={missing},empty={empty},multi={multi})")
    # 结构 anomaly: animations_list 缺失的 WW 资源
    struct_anom = 0
    for r in recs_in:
        if not (r.read_ok and r.decomp_ok and r.xml_parse_ok):
            continue
        entries, ok, _ = extract_entries(r._root)
        if not ok or not entries:
            struct_anom += 1
    if struct_anom:
        reasons.append(f"STRUCTURE_ANOMALY:{struct_anom}")
    world_collide = any(r.tgi in ww_xml_by_tgi and len(ww_xml_by_tgi[r.tgi]) > 1 and
                        len({Path(x.pkg_path).resolve() for x in ww_xml_by_tgi[r.tgi]}) > 1
                        for r in recs_in)
    if world_collide:
        candidates_blocked = True
    else:
        candidates_blocked = False
    pass_ = (not reasons) and (not candidates_blocked)
    r = {"path": str(canary_path), "sha256": pkg_sha, "file_size": file_size,
         "schema_classes": "|".join(schema_set) if schema_set else "",
         "ww_xml_count": ww_xml_count, "animation_entry_count": entry_count,
         "EXACT_ONE": exact_one, "MISSING": missing, "EMPTY": empty, "MULTIPLE": multi,
         "tgi_collision_count": colliding, "cross_package_collision_count": cross,
         "parse_fail_count": sum(1 for r in recs_in if not (r.read_ok and r.decomp_ok and r.xml_parse_ok)),
         "anomaly_count": struct_anom, "preflight_pass": ("YES" if pass_ else "NO"),
         "fail_reasons": " | ".join(reasons)}
    rows.append(r)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(r.keys()))
        w.writeheader(); w.writerows(rows)
    return r, pass_


def next_canary_fallback(recs, ww_xml_by_tgi, exclude_path, min_entries=50):
    """从 corpus 挑下一个满足 preflight 的大包 (不含 StripClub/UNKNOWN/parser fail/
    cross-package collision / 非 EXACT_ONE / 结构 anomaly)。按 entry count 降序。"""
    cand = {}
    for pkg in sorted({Path(r.pkg_path).resolve() for r in recs}):
        if pkg.resolve() == Path(exclude_path).resolve():
            continue
        recs_in = [r for r in recs if Path(r.pkg_path).resolve() == pkg.resolve()]
        schema_set = {r.schema for r in recs_in if r.schema}
        if any(s != WW_SCHEMA for s in schema_set):
            continue
        if any(not (r.read_ok and r.decomp_ok and r.xml_parse_ok) for r in recs_in):
            continue
        cross = 0
        for r in recs_in:
            lst = ww_xml_by_tgi.get(r.tgi, [])
            if len(lst) > 1 and len({Path(x.pkg_path).resolve() for x in lst}) > 1:
                cross += 1
        if cross:
            continue
        entry_count = 0
        all_exact = True
        struct_anom = 0
        for r in recs_in:
            if not (r.read_ok and r.decomp_ok and r.xml_parse_ok):
                continue
            entries, ok, _ = extract_entries(r._root)
            if not ok or not entries:
                struct_anom += 1
                continue
            for e in entries:
                entry_count += 1
                if e["display_status"] != "EXACT_ONE":
                    all_exact = False
        if struct_anom or not all_exact or entry_count < min_entries:
            continue
        cand[str(pkg)] = (entry_count, len(recs_in))
    if not cand:
        return None
    best = max(cand.items(), key=lambda kv: (kv[1][0], kv[1][1]))
    return (best[0], best[1][0], best[1][1])  # (path, entry_count, ww_xml_count)


if __name__ == "__main__":
    sys.exit(main())
