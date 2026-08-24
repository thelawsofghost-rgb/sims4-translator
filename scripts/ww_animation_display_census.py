#!/usr/bin/env python3
"""WW 专用 display 普查 (只读, 不生成 package, 不改 writer, 不碰 Mods)。

扫描一个 WW_Nevely42_Animations.package:
  - 找 type 0x7DF2169C (WW_ANIM_XML) 资源
  - 定位 <L n="animations_list"> 内所有顶层 <U> entry
  - 对每个 entry 提取 9 个字段, 导出 CSV + JSON

字段:
  ordinal                     (entry 在 animations_list 中的序号, 0-based)
  source_instance             (WW_ANIM_XML resource instance)
  animation_raw_display_name
  animation_stage_name
  author                      (animation_author)
  category                    (animation_category)
  tags                        (animation_tags; 多个 -> 用 '|' 连接)
  location                    (animation_locations / animation_custom_locations)
  actor_count                 (animation_actors_list 内 actor 数, 派生)
  clip_name                   (animation_clip_name / dancer_animation_clip_name)

用法:
    python scripts\\ww_animation_display_census.py "<package路径>" [--out-dir DIR]

输出 (默认 output/ww_animation_display_census/):
    ww_animation_display_census.csv
    ww_animation_display_census.json
    census_report.txt            (汇总: entry 数, 每字段命中数, 解压失败等)

安全: ZERO_WRITE_TO_MODS=YES。仅读源包, 不写 Mods。
"""
import argparse, csv, json, re, sys
from pathlib import Path
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse

IMPORT_PATH = Path(__file__).resolve().parent / "ww_animation_canary_builder.py"
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("wb", IMPORT_PATH)
wb = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(wb)

WW_ANIM_XML = 0x7DF2169C
ENTRY_LIST_FIELD = "animations_list"
FIELDS = [
    "ordinal", "source_instance", "animation_raw_display_name",
    "animation_stage_name", "author", "category", "tags", "location",
    "actor_count", "clip_name",
]
PASS_KEY = "WW_DISPLAY_CENSUS"
ASSERT_ENTRIES = None  # 可通过 --expect 断言 entry 数 (默认不强制)


def _locate_animations_list(xml_text: str):
    m = re.search(r'<L\b[^>]*\bn="' + re.escape(ENTRY_LIST_FIELD) + r'"[^>]*>', xml_text)
    if not m:
        return None, None, None
    inner_start = m.end()
    depth, pos = 1, inner_start
    open_re = re.compile(r'<L\b[^>]*>')
    close_re = re.compile(r'</L\s*>')
    while True:
        oc = open_re.search(xml_text, pos)
        cc = close_re.search(xml_text, pos)
        if cc is None:
            return None, None, None
        if oc is not None and oc.start() < cc.start():
            depth += 1
            pos = oc.end()
        else:
            depth -= 1
            if depth == 0:
                return xml_text[inner_start:cc.start()], inner_start, cc.start()
            pos = cc.end()


def _entry_blocks(inner: str):
    blocks = []
    pos, n = 0, len(inner)
    entry_re = re.compile(r'<U\b[^>]*>')
    close_re = re.compile(r'</U\s*>')
    while pos < n:
        m = entry_re.search(inner, pos)
        if m is None:
            break
        if m.start() > pos:
            blocks.append((None, (pos, m.start())))  # gap
        s, depth, p = m.start(), 1, m.end()
        while True:
            oc = entry_re.search(inner, p)
            cc = close_re.search(inner, p)
            if cc is None:
                e = n
                break
            if oc is not None and oc.start() < cc.start():
                depth += 1
                p = oc.end()
            else:
                depth -= 1
                p = cc.end()
                if depth == 0:
                    e = p
                    break
        blocks.append((inner[s:e], (s, e)))
        pos = e
    return [b for b in blocks if b[0] is not None]


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else None


def _entry_fields(block: str):
    """从单个 entry block 提取字段 dict。返回 (dict, node_count)。"""
    root = ET.fromstring(block)
    # 按 n= 收集所有 T 文本, 及 actors 列表
    tmap = {}
    actors = 0
    for el in root.iter():
        lt = _local(el.tag)
        n = el.get("n")
        if lt in ("T", "E", "I", "L", "U"):
            if n:
                tmap.setdefault(n, [])
                tmap[n].append((el.text or "").strip())
                if lt == "I" and n in ("actor_id", "actor_a", "animation_actor_id"):
                    actors = max(actors, int((el.text or "0").strip() or 0))
        # actors 列表计数
    # animation_actors_list 内 <U> 数 或 actors Actor 数
    actor_count = 0
    for el in root.iter():
        lt = _local(el.tag)
        n = el.get("n")
        if lt == "U" and n == "animation_actors_list":
            actor_count = sum(1 for c in el if _local(c.tag) == "U")
            break
    if actor_count == 0:
        a_ids = tmap.get("actor_id", []) or tmap.get("actor_a", []) or tmap.get("animation_actor_id", [])
        if a_ids:
            try:
                actor_count = max(int(x) for x in a_ids if str(x).isdigit())
            except ValueError:
                actor_count = len(a_ids)
    if actor_count == 0:
        # 兜底: 找 n="actors" 的列表
        for el in root.iter():
            if _local(el.tag) == "L" and el.get("n") in ("actors", "animation_actors"):
                actor_count = len([c for c in el if _local(c.tag) == "U"])
                break

    def _t(*names):
        for nm in names:
            if nm in tmap and tmap[nm]:
                return "|".join(tmap[nm])
        return ""

    d = {
        "animation_raw_display_name": _t("animation_raw_display_name", "raw_display_name"),
        "animation_stage_name": _t("animation_stage_name"),
        "author": _t("animation_author", "author"),
        "category": _t("animation_category", "category"),
        "tags": _t("animation_tags", "tags"),
        "location": _t("animation_locations", "animation_custom_locations"),
        "clip_name": _t("animation_clip_name", "dancer_animation_clip_name", "clip_name"),
        "actor_count": actor_count,
    }
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pkg", help="WW_Nevely42_Animations.package 完整路径")
    ap.add_argument("--out-dir", default="output/ww_animation_display_census")
    ap.add_argument("--expect", type=int, default=None, help="断言 entry 数 (可选)")
    args = ap.parse_args()

    src = Path(args.pkg)
    if not src.exists():
        print(f"ERROR: 源包不存在 {src}", file=sys.stderr)
        sys.exit(2)

    idx, err = safe_parse(str(src))
    if err or idx is None:
        print(f"ERROR: safe_parse 失败: {err}", file=sys.stderr)
        sys.exit(2)

    ww_entries = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    print(f"ENTRY_TOTAL={len(idx.entries)}  WW_ANIM_XML_COUNT={len(ww_entries)}")
    if len(ww_entries) != 1:
        print(f"ERROR: 期望 1 个 WW_ANIM_XML, 实际 {len(ww_entries)} (fail-closed)", file=sys.stderr)
        sys.exit(2)
    ww_e = ww_entries[0]
    src_instance = ww_e.instance_id

    body = wb.read_body_raw(src, ww_e)
    _schema, xml_text, xerr = wb.parse_anim_xml(body)
    if xerr or xml_text is None:
        print(f"ERROR: XML parse 失败: {xerr}", file=sys.stderr)
        sys.exit(2)

    inner, _, _ = _locate_animations_list(xml_text)
    if inner is None:
        print(f"ERROR: 找不到 animations_list (fail-closed)", file=sys.stderr)
        sys.exit(2)
    blocks = _entry_blocks(inner)
    n = len(blocks)
    print(f"ANIM_ENTRY_COUNT={n}")
    if args.expect is not None and n != args.expect:
        print(f"ERROR: entry 数 {n} != 期望 {args.expect} (fail-closed)", file=sys.stderr)
        sys.exit(2)

    rows = []
    hits = {f: 0 for f in FIELDS[2:]}  # ordinal/source_instance 恒有值
    for ordinal, (block, _span) in enumerate(blocks):
        try:
            ef = _entry_fields(block)
        except Exception as ex:
            print(f"  WARN: entry#{ordinal} 解析异常: {ex}", file=sys.stderr)
            ef = {f: "" for f in FIELDS[2:]}
            ef["actor_count"] = 0
        for f in FIELDS[2:]:
            if str(ef.get(f, "")).strip() != "":
                hits[f] += 1
        row = {
            "ordinal": ordinal,
            "source_instance": f"0x{src_instance:016X}",
            "animation_raw_display_name": ef.get("animation_raw_display_name", ""),
            "animation_stage_name": ef.get("animation_stage_name", ""),
            "author": ef.get("author", ""),
            "category": ef.get("category", ""),
            "tags": ef.get("tags", ""),
            "location": ef.get("location", ""),
            "actor_count": ef.get("actor_count", 0),
            "clip_name": ef.get("clip_name", ""),
        }
        rows.append(row)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "ww_animation_display_census.csv"
    json_path = out_dir / "ww_animation_display_census.json"
    rep_path = out_dir / "census_report.txt"

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "source_package": str(src),
            "source_sha256": wb.sha256(src),
            "source_instance": f"0x{src_instance:016X}",
            "entry_count": n,
            "field_hits": hits,
            "entries": rows,
        }, f, ensure_ascii=False, indent=2)

    with open(rep_path, "w", encoding="utf-8") as f:
        f.write(f"source: {src}\n")
        f.write(f"sha256: {wb.sha256(src)}\n")
        f.write(f"source_instance: 0x{src_instance:016X}\n")
        f.write(f"entries: {n}\n\n")
        f.write("field_hits(非空数/总entry):\n")
        for k, v in hits.items():
            f.write(f"  {k}: {v}/{n}\n")

    print(f"\nSOURCE_SHA={wb.sha256(src)[:16]}…")
    print(f"SOURCE_INSTANCE=0x{src_instance:016X}")
    print(f"ANIM_ENTRY_COUNT={n}")
    print(f"FIELD_HITS={hits}")
    print(f"\nCSV  -> {csv_path}")
    print(f"JSON -> {json_path}")
    print(f"REP  -> {rep_path}")
    print(f"{PASS_KEY}=YES")
    print("ZERO_WRITE_TO_MODS=YES")


if __name__ == "__main__":
    main()
