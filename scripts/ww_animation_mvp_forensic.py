#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WW Animation MVP Forensic — 只读真实样本取证 (阶段一最小版)

目标只回答三件事:
  1. WickedWhims 玩家看到的 animation display name 存在哪里?
  2. internal animation ID / clip reference 存在哪里?
  3. display text 与 internal/function identifier 能否可靠分离?

范围 (刻意最小):
  - 只处理用户给定的 3 个 (或不大于 10 个) CONFIRMED WW animation package
  - 不做全量 Mods census / 不写 sidecar / 不改 package / 不部署
  - 只解出 WW_ANIM_XML (0x7DF2169C, zlib 压缩) 并打印代表性 XML entry 片段
  - 列 CLIP / ANIM_RCOL / STBL 存在性 + CLIP linkage
  - 不自动做完整分类器; 只对"明显"字段给最小注释 (display / internal / creator / clip)

已知正证据 (2026-08-12 实测, 冻结):
  WW_ANIM_XML  = 0x7DF2169C  (zlib 压缩, 头 0x78)
  CLIP         = 0x6B20C4F3
  ANIM_RCOL    = 0xBC4A5044  (与 CLIP 一对一配对)
  STBL         = 0x220557DA
  TUNING_XML   = 0x0333406C

  两个 schema 变体 (字段名来自真实包):
    WickedWhimsAnimationPackage:
        animation_raw_display_name / animation_clip_name / animation_actors_list /
        animation_category / animation_tags / animation_locations / animation_author
    StripClubDanceAnimationPackage:
        raw_display_name / dancer_animation_clip_name / dance_type / dancer_gender

身份规则 (fail-closed): 只有同时
  (a) 含 WW_ANIM_XML 且解出上述注册 schema 之一, AND
  (b) 含 CLIP (0x6B20C4F3)
才视为 CONFIRMED。文件名只作辅助提示, 不作身份依据。

用法 (Windows, 只读):
  python scripts/ww_animation_mvp_forensic.py \
      --mods-root "C:\\Users\\thela\\Documents\\Electronic Arts\\The Sims 4\\Mods" \
      [--pick N] [--explicit P1 P2 P3 ...]

  --mods-root : Mods 根目录
  --pick N    : 自动在 Mods 下扫"候选"并挑 N 个 CONFIRMED 包 (优先不同 creator 提示/大小差异)
  --explicit  : 直接给定 1..N 个 package 路径 (跳过自动挑选)
  若不指定任一选包方式: 默认 --pick 3

输出 (ZERO WRITE TO MODS):
  output/ww_animation_mvp_forensic.csv   每包每 animation entry 的字段/原值/角色注释
  output/ww_animation_mvp_forensic.md    结构化 report + 代表性 XML 片段
  stdout 最后的 SAMPLE x + DISPLAY_STORAGE/DISPLAY_INTERNAL_SEPARATION/
          SCHEMA_DIFFERENCE/NEXT_STEP 总判断

退出码: 0=完成; 2=参数/IO 错误; 4=给定包全部非 CONFIRMED (fail-closed)
"""
import argparse
import csv
import hashlib
import re
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse  # noqa: E402

WW_ANIM_XML = 0x7DF2169C
CLIP = 0x6B20C4F3
ANIM_RCOL = 0xBC4A5044
STBL = 0x220557DA
TUNING_XML = 0x0333406C

OUT_CSV = "output/ww_animation_mvp_forensic.csv"
OUT_MD = "output/ww_animation_mvp_forensic.md"

# 两 schema 的关键字段 (带最小角色注释; 不自动当权威判定, 仅在显式同名时提示)
FIELD_HINTS = {
    "display": {"animation_raw_display_name", "raw_display_name"},
    "internal/clip": {"animation_clip_name", "dancer_animation_clip_name"},
    "creator": {"animation_author"},
    "category": {"animation_category", "dance_type"},
    "location/object": {"animation_locations"},
    "internal/id": {"animation_id", "id"},
    "tags": {"animation_tags"},
    "actors": {"animation_actors_list"},
    "gender": {"dancer_gender"},
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def resource_type_hist(idx) -> dict:
    h = {}
    for e in idx.entries:
        h[e.type_id] = h.get(e.type_id, 0) + 1
    return h


def read_body(pkg: Path, entry) -> bytes:
    """按 index entry 的 offset/size 读资源 body (处理压缩标记位)。"""
    off = entry.offset & 0x7FFFFFFF
    size = entry.size & 0x7FFFFFFF
    with open(pkg, "rb") as fh:
        fh.seek(off)
        return fh.read(size)


def find_ww_anim_xml_entries(idx):
    return [e for e in idx.entries if e.type_id == WW_ANIM_XML]


def decompress_maybe(body: bytes) -> bytes:
    if body[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        try:
            return zlib.decompress(body)
        except Exception:
            return body  # 让上层判断为 malformed
    return body


def xml_to_text(xml: str) -> str:
    """去掉 XML 标签, 只留文本 (便于看内部是否嵌自然语言)。"""
    return re.sub(r"<[^>]+>", "", xml).strip()


def parse_anim_xml(body: bytes):
    """返回 (schema_name, raw_xml, entries_parsed, err)。
    schema 识别: 通过根/首个元素名或关键字段名。失败返回 (None, None, [], 'msg')。"""
    raw = decompress_maybe(body)
    if raw[:2] not in (b"\x78",) and not raw.lstrip().startswith(b"<") and b"<" not in raw[:64]:
        # 非 zlib 且非明显 XML
        return None, raw, [], "NOT_XML"
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception as e:
        return None, raw, [], f"DECODE: {e}"
    if "<" not in text:
        return None, raw, [], "NO_XML_TAG"
    # schema 判定: 优先看标签/字段
    if re.search(r"WickedWhimsAnimationPackage", text) or "animation_raw_display_name" in text:
        schema = "WickedWhimsAnimationPackage"
    elif "StripClubDanceAnimationPackage" in text or "dancer_animation_clip_name" in text:
        schema = "StripClubDanceAnimationPackage"
    else:
        schema = "UNKNOWN_SCHEMA"
    return schema, raw, [], None


def extract_entries(text: str):
    """极简: 把每个 animation 元素切成独立块 (只在 <Animation>/<Dance> 开标签处切)。
    不匹配根 package 元素, 保证代表性片段是一个完整动画条目。"""
    pretty = re.sub(r">\s*<", ">\n<", text)
    blocks = re.split(r"(?=<(?:Animation|Dance)\b[^>]*>)", pretty)
    return [b.strip() for b in blocks if b.strip()]


def classify_field(field: str) -> str:
    """给字段名一个最小角色注释。绝不因像英文句子就标 PLAYER_VISIBLE。"""
    fl = field.lower()
    for role, names in FIELD_HINTS.items():
        if fl in names:
            return role
    if fl in ("name",):
        return "name?(需查被谁引用)"
    return "unknown-field"


def stbl_summary(pkg: Path, idx):
    """STBL 存在性 + 数量统计 (不解析内容)。"""
    stbls = [e for e in idx.entries if e.type_id == STBL]
    if not stbls:
        return None
    return stbls


def link_clip(text: str, idx) -> bool:
    """检查 XML 里是否出现与任何 CLIP instance 相关的引用 (hex 低位片段)。"""
    clip_insts = set()
    for e in idx.entries:
        if e.type_id == CLIP and e.instance_id is not None:
            clip_insts.add(e.instance_id)
    if not clip_insts:
        return False
    # 把 XML 中出现的 16 进制片段与 CLIP instance 低 bit 比对 (启发式, 仅提示)
    lower = text.lower()
    hits = 0
    for inst in clip_insts:
        low = inst & 0xFFFFFFFF
        for pat in (f"{low:08x}", f"{low:x}", f"0x{low:x}"):
            if pat in lower:
                hits += 1
                break
    return hits > 0


def identify_ww(pkg: Path):
    """对单个包返回身份诊断 dict。fail-closed: 需 WW_ANIM_XML + CLIP 双证据。"""
    d = {"path": str(pkg), "sha256": sha256(pkg)}
    idx, err = safe_parse(pkg)
    if err is not None or idx is None:
        d["identity"] = "UNKNOWN"
        d["identity_err"] = err or "NO_INDEX"
        return d
    hist = resource_type_hist(idx)
    d["resource_count"] = sum(hist.values())
    d["histogram"] = {f"0x{t:08X}": c for t, c in sorted(hist.items())}
    d["clip_count"] = hist.get(CLIP, 0)
    d["anim_rcol_count"] = hist.get(ANIM_RCOL, 0)
    d["stbl_entries"] = stbl_summary(pkg, idx)
    ww_xmls = find_ww_anim_xml_entries(idx)
    d["ww_anim_xml_count"] = len(ww_xmls)
    # 关键: 双证据
    has_clip = d["clip_count"] > 0
    schemas = set()
    xml_details = []
    for e in ww_xmls:
        body = read_body(pkg, e)
        schema, raw, _entries, xerr = parse_anim_xml(body)
        if schema:
            schemas.add(schema)
        xml_details.append({
            "tgi": f"0x{e.type_id:08X}/0x{e.group_id:08X}/0x{e.instance_id:016X}"
                   if e.instance_id is not None else f"0x{e.type_id:08X}/0x{e.group_id:08X}/",
            "schema": schema or "UNKNOWN_SCHEMA",
            "size": len(body),
            "err": xerr,
        })
    d["schemas"] = sorted(schemas)
    d["xml_details"] = xml_details
    # 仅当出现被识别的 WW 注册 schema (非 UNKNOWN_SCHEMA) 才算正注册证据
    known_ww = [s for s in schemas if s in ("WickedWhimsAnimationPackage", "StripClubDanceAnimationPackage")]
    has_reg = bool(known_ww)
    if ww_xmls and has_reg and has_clip:
        d["identity"] = "CONFIRMED"
    elif ww_xmls and has_reg and not has_clip:
        d["identity"] = "POSSIBLE"   # 有注册 XML 但缺 CLIP
    elif ww_xmls and not has_reg:
        d["identity"] = "POSSIBLE"   # 有 WW_ANIM_XML 但 schema 未识别 (待查)
    elif has_clip and not ww_xmls:
        d["identity"] = "POSSIBLE"   # 有 CLIP 但缺 WW 注册 XML
    else:
        d["identity"] = "NOT_WW"
    # 文件名校验 (仅提示, 不作依据)
    d["filename_hint"] = "WW" in pkg.name.upper() or "ANIMATION" in pkg.name.upper()
    return d


def pick_auto(mods_root: Path, n: int):
    """扫 Mods 下 *.package, 先对每个做 identify, 返回 CONFIRMED 集合。
    再按 (schema 多样性, creator 提示差异, 大小差异) 挑 n 个。"""
    candidates = []
    for pkg in mods_root.rglob("*.package"):
        try:
            d = identify_ww(pkg)
        except Exception as e:
            continue
        if d["identity"] == "CONFIRMED":
            d["__path"] = pkg
            candidates.append(d)
    if not candidates:
        return []
    # 排序: 覆盖不同 schema / 大小差异 / creator 文件名前缀差异
    # 先按 schema 分组, 保证至少覆盖两个 schema; 再大小拉开
    by_schema = {}
    for d in candidates:
        key = tuple(d["schemas"]) or ("UNKNOWN_SCHEMA",)
        by_schema.setdefault(key, []).append(d)
    chosen = []
    # 每个 schema 挑一个最小 + 一个最大 (如果同 schema 多包)
    for key, group in by_schema.items():
        group.sort(key=lambda x: Path(x["path"]).stat().st_size)
        chosen.append(group[0])
        if len(group) > 1 and len(chosen) < n:
            chosen.append(group[-1])
    chosen = chosen[:n]
    # 补足到 n
    if len(chosen) < n:
        for d in candidates:
            if d not in chosen:
                chosen.append(d)
                if len(chosen) >= n:
                    break
    return chosen[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mods-root", required=True)
    ap.add_argument("--pick", type=int, default=None)
    ap.add_argument("--explicit", nargs="+", default=None)
    a = ap.parse_args()

    mods_root = Path(a.mods_root)
    if not mods_root.is_dir():
        print("ERROR: mods-root 不存在", file=sys.stderr)
        return 2

    if a.explicit:
        pkgs = [Path(p) for p in a.explicit]
    else:
        n = a.pick if a.pick else 3
        chosen = pick_auto(mods_root, n)
        if not chosen:
            print("ERROR: 未找到任何 CONFIRMED WW animation package (fail-closed)", file=sys.stderr)
            return 4
        pkgs = [c["__path"] for c in chosen]

    # 对显式/挑选的包全部做身份再确认
    confirmed = []
    for p in pkgs:
        d = identify_ww(p)
        d["__path"] = p
        confirmed.append(d)

    # ---- 收集 CSV 行 + md ----
    md_lines = []
    csv_rows = []
    schema_seen = set()
    any_confirmed = False

    for d in confirmed:
        pkg = d["__path"]
        verified = d["identity"] == "CONFIRMED"
        if verified:
            any_confirmed = True
        md_lines.append("=" * 72)
        md_lines.append("PACKAGE")
        md_lines.append(f"path={pkg}")
        md_lines.append(f"sha256={d['sha256']}")
        md_lines.append(f"identity={d['identity']}   (filename_hint={d['filename_hint']})")
        md_lines.append(f"resource_count={d['resource_count']}")
        hist = "  ".join(f"{k}:{v}" for k, v in sorted(d['histogram'].items()))
        md_lines.append(f"resource_histogram={hist}")
        md_lines.append(f"clip_count={d['clip_count']}  anim_rcol_count={d['anim_rcol_count']}")
        md_lines.append(f"stbl_present={'YES (' + str(len(d['stbl_entries'])) + ' entries)' if d['stbl_entries'] else 'NO'}")
        md_lines.append(f"ww_anim_xml_count={d['ww_anim_xml_count']}  schemas={d['schemas']}")

        # 逐 XML 解出结构 + 代表性片段 (读一次, 与 xml_details 按序对齐)
        idx_pkg, _ = safe_parse(pkg)
        ww_entries = [ee for ee in idx_pkg.entries if ee.type_id == WW_ANIM_XML] if idx_pkg else []
        for xmlinfo in d["xml_details"]:
            md_lines.append(f"  XML TGI={xmlinfo['tgi']}  schema={xmlinfo['schema']}  size={xmlinfo['size']}B  err={xmlinfo['err']}")
            if xmlinfo["err"]:
                md_lines.append(f"    MAKEFORMED/fail-closed: {xmlinfo['err']}")
                csv_rows.append({
                    "package": str(pkg), "sha256": d["sha256"], "identity": d["identity"],
                    "xml_tgi": xmlinfo["tgi"], "schema": xmlinfo["schema"],
                    "field": "(resource-level)", "raw_value": f"<MAKEFORMED: {xmlinfo['err']}>",
                    "role": "UNKNOWN/fail-closed",
                })
                continue
            # 定位与该 xmlinfo 对齐的 entry (按 WW_ANIM_XML 顺序)
            i_xml = d["xml_details"].index(xmlinfo)
            if i_xml >= len(ww_entries):
                continue
            ee = ww_entries[i_xml]
            body_xml = read_body(pkg, ee)
            raw = decompress_maybe(body_xml)
            text = raw.decode("utf-8", errors="replace")
            schema_now = xmlinfo["schema"]
            schema_seen.add(schema_now)
            entries = extract_entries(text)
            md_lines.append(f"  extracted_entries={len(entries)}  schema={schema_now}")

            # 代表性片段: 打印第一个【真实动画条目】(跳过 XML 声明 + 根元素前奏)
            anim_entries = [en for en in entries if re.match(r"<\s*(Animation|Dance)\b", en.strip())]
            sample_entries = anim_entries if anim_entries else entries[:1]
            for ent in sample_entries[:1]:
                lines = ent.splitlines()
                shown = "\n".join("    | " + ln for ln in lines[:40])
                md_lines.append("  --- representative entry (first) ---")
                md_lines.append(shown)
                if len(lines) > 40:
                    md_lines.append(f"    | ... ({len(lines) - 40} more lines)")
                md_lines.append("  --- end entry ---")

            # 字段抽取: 用正则抓 attr= 字段
            fields = re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*)"', text)
            entry_roles = set()
            for field, val in fields[:120]:
                role = classify_field(field)
                entry_roles.add(role)
                csv_rows.append({
                    "package": str(pkg), "sha256": d["sha256"], "identity": d["identity"],
                    "xml_tgi": xmlinfo["tgi"], "schema": schema_now,
                    "field": field, "raw_value": val[:500], "role": role,
                })

            # CLIP linkage
            linked = link_clip(text, idx_pkg)
            md_lines.append(f"  CLIP_linked_by_xml={linked}")
            md_lines.append(f"  roles_seen_in_entries={sorted(entry_roles)}")

        md_lines.append("")

    # ---- 总判断 ----
    display_storage = set()
    sep = set()
    for d in confirmed:
        if d["identity"] != "CONFIRMED":
            continue
        # display 源: 看 CSV 里该包 display 字段值是否像自然语言直接写在 XML
        disp_vals = [r["raw_value"] for r in csv_rows
                     if r["package"] == str(d["__path"]) and r["role"] == "display"]
        if disp_vals:
            display_storage.add("DIRECT_XML")
        else:
            display_storage.add("UNKNOWN")
        # 分离: display 与 internal 是否在包内并存
        disp_fields = {r["field"] for r in csv_rows if r["package"] == str(d["__path"]) and r["role"] == "display"}
        int_fields = {r["field"] for r in csv_rows if r["package"] == str(d["__path"]) and r["role"] in ("internal/clip", "internal/id")}
        if disp_fields and int_fields and disp_fields != int_fields:
            sep.add("CLEAR/PARTIAL")
        elif disp_fields or int_fields:
            sep.add("UNKNOWN")
        else:
            sep.add("UNKNOWN")
    if len(display_storage) == 1:
        DISPLAY_STORAGE = display_storage.pop()
    elif len(display_storage) > 1:
        DISPLAY_STORAGE = "MIXED"
    else:
        DISPLAY_STORAGE = "UNKNOWN"
    DISPLAY_INTERNAL_SEPARATION = ("CLEAR" if sep == {"CLEAR/PARTIAL"} else
                                   ("PARTIAL" if len(sep) > 0 and "CLEAR/PARTIAL" in sep else "UNKNOWN"))
    SCHEMA_DIFFERENCE = "MAJOR" if len(schema_seen) > 1 else ("MINOR" if len(schema_seen) == 1 else "UNKNOWN")

    # ---- 写 CSV ----
    out_csv = Path(OUT_CSV)
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        cols = ["package", "sha256", "identity", "xml_tgi", "schema", "field", "raw_value", "role"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in csv_rows:
            w.writerow(r)

    # ---- 写 MD ----
    md_lines.append("=" * 72)
    md_lines.append("SUMMARY")
    md_lines.append(f"DISPLAY_STORAGE={DISPLAY_STORAGE}")
    md_lines.append(f"DISPLAY_INTERNAL_SEPARATION={DISPLAY_INTERNAL_SEPARATION}")
    md_lines.append(f"SCHEMA_DIFFERENCE={SCHEMA_DIFFERENCE}")
    md_lines.append(f"SCHEMAS_OBSERVED={sorted(schema_seen)}")
    md_lines.append(f"CONFIRMED_COUNT={sum(1 for d in confirmed if d['identity'] == 'CONFIRMED')}")
    for d in confirmed:
        md_lines.append(f"  {d['path']} -> {d['identity']}")
    out_md = Path(OUT_MD)
    out_md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    # ---- stdout 简洁总结 ----
    lines = md_lines[:]
    for i, d in enumerate(confirmed, 1):
        disp = [r["raw_value"] for r in csv_rows if r["package"] == str(d["__path"]) and r["role"] == "display"]
        internal = [r["raw_value"] for r in csv_rows if r["package"] == str(d["__path"]) and r["role"] in ("internal/clip", "internal/id")]
        clip_link = any("CLIP_linked_by_xml=" in ln and ln.rstrip().endswith("True") for ln in [])
        print(f"\nSAMPLE {i}")
        print(f"  path={d['path']}")
        print(f"  identity={d['identity']}")
        print(f"  schema={d['schemas']}")
        print(f"  display_candidate={disp if disp else '(none detected / UNKNOWN)'}")
        print(f"  internal_id_candidate={internal if internal else '(none detected / UNKNOWN)'}")
        print(f"  clip_count={d['clip_count']}  anim_rcol={d['anim_rcol_count']}")
        print(f"  stbl={'YES' if d['stbl_entries'] else 'NO'}")
    print(f"\nDISPLAY_STORAGE={DISPLAY_STORAGE}")
    print(f"DISPLAY_INTERNAL_SEPARATION={DISPLAY_INTERNAL_SEPARATION}")
    print(f"SCHEMA_DIFFERENCE={SCHEMA_DIFFERENCE}")
    print(f"NEXT_STEP=" + (
        "建议最小真机 A/B: 仅在 CONFIRMED 包的 display 字段重写一次 (PosePack sidecar 备份), "
        "真机验证玩家可见名称是否改变; internal/clip 字段绝不动。" if DISPLAY_STORAGE == "DIRECT_XML" and "CLEAR" in DISPLAY_INTERNAL_SEPARATION
        else "继续取证 (display 源或分离不明确, 需更多真实包样本)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
