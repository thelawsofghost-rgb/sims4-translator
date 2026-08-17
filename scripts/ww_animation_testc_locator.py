#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEST C HUMAN-READABLE LOCATOR REPORT (只读)。

在【真实 Windows】上, 只从 source WW_Nevely42_Animations.package 的三个真实
animation entry (ordinal 0/239/478) 读取 metadata, 输出:
  - C1/C2/C3_LOCATOR: 原始字段 (display_name/author/locations/category/tags/
    loops/allowed_for_random/actor 数)。
  - C1/C2/C3_HUMAN_LOCATOR: 中文易读摘要。

【不改 sidecar / 不改 writer / 不重新生成 XML / 不碰 Mods】。

中文映射原则:
- 只允许基于 XML 明确值映射 (BED/FLOOR/SOFA/CHAIR/VAGINAL/ANAL/ORAL/...)。
- explicit 值 -> canonical Chinese (记录 canonical translation, 保证统一译法)。
- 无明确证据 -> UNKNOWN; 不推断。
- animation_custom_locations 若为 numeric ID -> 保留 numeric ID 并标
  CUSTOM_LOCATION_NAME=UNRESOLVED, 不猜对应家具。
- 绝不通过 display name 猜身体部位/性行为/场景。

只输出 stdout。ZERO_WRITE_TO_MODS=YES。
"""

import argparse
import importlib.util
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MODULE = SCRIPT_DIR / "ww_animation_canary_builder.py"
_spec = importlib.util.spec_from_file_location("ww_canary_builder", MODULE)
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)

WW_ANIM_XML = wb.WW_ANIM_XML
TARGET_ORDS = (0, 239, 478)
TARGET_PREFIX = {0: "C1", 239: "C2", 478: "C3"}

# canonical 中文映射 (仅 explicit XML 值 -> 统一译法)
LOCATION_CN = {
    "BED": "床",
    "FLOOR": "地板",
    "SOFA": "沙发",
    "COUCH": "沙发",
    "CHAIR": "椅子",
    "TABLE": "桌子",
    "DESK": "书桌",
    "POOL": "泳池",
    "SHOWER": "淋浴",
    "BATH": "浴缸",
    "WALL": "墙壁",
    "CAR": "汽车",
    "DOOR": "门",
    "COUNTER": "柜台",
    "KITCHEN_COUNTER": "厨房柜台",
    "DINING_TABLE": "餐桌",
}
SEX_TYPE_CN = {
    "VAGINAL": "阴道性交",
    "ANAL": "肛交",
    "ORAL": "口交",
    "HANDJOB": "手部刺激",
    "FOOTJOB": "足部刺激",
    "TITJOB": "乳交",
    "FEMDOM": "女性主导",
    "MALEDOM": "男性主导",
    "MATING_PRESS": "交配压制",
    "CLIMAX": "高潮",
    "ORGASM": "高潮",
}
ACTOR_TYPE_CN = {
    "VAGINAL": "阴道性交",
    "ANAL": "肛交",
    "ORAL": "口交",
    "HANDJOB": "手部刺激",
    "CLIMAX": "高潮",
    "DOMINANT": "主导",
    "SUBMISSIVE": "顺从",
    "STIMULATOR": "刺激方",
    "RECIPIENT": "接受方",
}
GENDER_CN = {
    "MALE": "男性",
    "FEMALE": "女性",
    "MALE_FEMALE": "男/女",
}
ANY_TAG = {"ANY", "ALL", "BOTH", "ANYONE"}


def _cn_map(mapping, val):
    """explicit 值 -> canonical 中文; 空白/ANY 类 -> UNKNOWN; 无映射 -> 原值+UNKNOWN。"""
    if val is None:
        return "UNKNOWN"
    v = str(val).strip()
    if v == "":
        return "UNKNOWN"
    if v.upper() in ANY_TAG:
        return "UNKNOWN(任意)"
    if v in mapping:
        return mapping[v]
    up = v.upper()
    if up in mapping:
        return mapping[up]
    return "UNKNOWN"


def _locate_animations_list(xml_text):
    s = re.search(r'<L\s+[^>]*\bn\s*=\s*"animations_list"[^>]*>', xml_text, re.S)
    if not s:
        return None, -1, -1
    open_end = s.end()
    depth = 1
    tag_re = re.compile(r"<(/?)([A-Za-z_][^\s/>]*)([^>]*?)(/?)>", re.S)
    for m in tag_re.finditer(xml_text, open_end):
        closing, name, _a, selfclose = m.group(1), m.group(2), m.group(3), m.group(4)
        if name == "L":
            if closing == "/" and not selfclose:
                depth -= 1
                if depth == 0:
                    return xml_text[open_end:m.start()], open_end, m.start()
            elif not closing and not selfclose:
                depth += 1
    return None, -1, -1


def _entry_blocks(list_text):
    blocks = []
    depth = 0
    start = None
    tag_re = re.compile(r"<(/?)([A-Za-z_][^\s/>]*)([^>]*?)(/?)>", re.S)
    for m in tag_re.finditer(list_text):
        closing, name, _a, selfclose = m.group(1), m.group(2), m.group(3), m.group(4)
        if name in ("?xml", "!DOCTYPE"):
            continue
        is_closing = closing == "/"
        is_selfclose = selfclose == "/"
        if is_closing and name == "U":
            if depth == 1 and start is not None:
                blocks.append((list_text[start:m.end()], (start, m.end())))
                start = None
            depth -= 1
            if depth < 0:
                depth = 0
        elif (not is_closing) and (not is_selfclose) and name == "U":
            if depth == 0:
                start = m.start()
            depth += 1
        elif (not is_closing) and (not is_selfclose):
            depth += 1
        elif is_closing and not is_selfclose:
            depth -= 1
            if depth < 0:
                depth = 0
    return blocks


_TEXT_RE = re.compile(r"<([A-Za-z_][^\s/>]*)([^>]*)>([^<]*)</\1>", re.S)
_SELF_RE = re.compile(r"<([A-Za-z_][^\s/>]*)([^>]*?)/>", re.S)
_UNPAIRED = re.compile(r"<([A-Za-z_][^\s/>]*)([^>]*)>", re.S)


def _parse_block(block_text):
    """把单个 entry block 解析成字段 dict + actor 列表 (仅用于 locator, 不改 artifact)。

    用 ElementTree 只读解析 (定位字段/actor), 不用于重建任何 XML。
    返回 (fields, actors, warnings)。
    """
    import xml.etree.ElementTree as ET
    fields = {}
    actors = []
    warnings = []
    try:
        root = ET.fromstring(block_text.encode("utf-8"))
    except Exception as e:
        return fields, actors, [f"parse_warn: {e}"]

    # entry 顶层及非-actor 子树的直接 T 字段: 收集 entry 根下所有 T, 排除位于
    # actor <U> 子树内者 (actor 字段单独收集)。metadata 可能在 <L> 内也可能在顶层。
    for child in root:
        _collect_entry_fields(child, fields)

    # actor: 收集所有拥有 actor_id 的 <U> (排除 entry 根自身)
    def walk(el):
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "U":
            actor = {}
            for sub in el.iter():
                stag = sub.tag.rsplit("}", 1)[-1]
                if stag == "T":
                    n = sub.get("n")
                    if n is not None and n not in actor:
                        actor[n] = (sub.text or "").strip()
            if actor.get("actor_id") is not None:
                actors.append(actor)
        for sub in el:
            walk(sub)

    for child in root:
        walk(child)
    return fields, actors, warnings


def _collect_entry_fields(el, fields):
    """收集 el 子树内 <T> 字段, 若 el 是 <U> (actor) 则跳过其子树 (actor 字段另收)。"""
    tag = el.tag.rsplit("}", 1)[-1]
    if tag == "U":
        return
    if tag == "T":
        n = el.get("n")
        if n is not None and n not in fields:
            fields[n] = (el.text or "").strip()
        return
    for sub in el:
        _collect_entry_fields(sub, fields)


def _attr(m, key):
    mm = re.search(r'\b%s\s*=\s*"([^"]*)"' % re.escape(key), m.group(2) or "")
    return mm.group(1) if mm else None


def _T_inner(text, after_open):
    """从 <T ...> 之后开始, 找同一 <T> 的 </T> 闭标签, 返回 inner text。"""
    close = re.search(r"</T>", text[after_open:])
    if not close:
        return ""
    return text[after_open:after_open + close.start()]


def _T_inner_iter(text):
    out = []
    for m in re.finditer(r"<T\s+[^>]*?>", text):
        out.append(m)
    return out


def _first_value(fields, key):
    return fields.get(key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="真实 source .package")
    a = ap.parse_args()
    src = Path(a.source)
    if not src.is_file():
        print("ERROR: source 不存在", file=sys.stderr)
        return 2

    idx, err = wb.safe_parse(src)
    if err is not None or idx is None:
        print("ERROR: source parse fail", file=sys.stderr)
        return 3
    ww = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    if len(ww) != 1:
        print(f"ERROR: WW XML count={len(ww)} (须 1)", file=sys.stderr)
        return 4
    body = wb.read_body_raw(src, ww[0])
    xml = wb.decompress_maybe(body).decode("utf-8", "replace")

    inner, ist, iend = _locate_animations_list(xml)
    if ist < 0:
        print("ERROR: 无 animations_list", file=sys.stderr)
        return 5
    blocks = _entry_blocks(inner)
    if len(blocks) != 479:
        print(f"ERROR: entry 数={len(blocks)} (须 479)", file=sys.stderr)
        return 6

    parsed = {}
    for ord0 in TARGET_ORDS:
        btext, (_s, _e) = blocks[ord0]
        fields, actors, warns = _parse_block(btext)
        parsed[ord0] = (fields, actors, warns)

    # ================= 原始 LOCATOR =================
    for ord0 in TARGET_ORDS:
        tag = TARGET_PREFIX[ord0]
        fields, actors, _w = parsed[ord0]
        print(f"{tag}_LOCATOR:")
        print(f"  ordinal={ord0}")
        print(f"  display_name={_first_value(fields, 'animation_raw_display_name')}")
        print(f"  animation_author={_first_value(fields, 'animation_author')}")
        print(f"  animation_locations={_first_value(fields, 'animation_locations')}")
        print(f"  animation_custom_locations={_first_value(fields, 'animation_custom_locations')}")
        print(f"  animation_category={_first_value(fields, 'animation_category')}")
        print(f"  animation_tags={_first_value(fields, 'animation_tags')}")
        print(f"  animation_loops={_first_value(fields, 'animation_loops')}")
        print(f"  animation_allowed_for_random={_first_value(fields, 'animation_allowed_for_random')}")
        print(f"  actor_count={len(actors)}")
        for i, actor in enumerate(actors):
            print(f"  ACTORS:")
            print(f"    actor_{i}_id={actor.get('actor_id')}")
            print(f"    actor_{i}_animation_clip_name={actor.get('animation_clip_name')}")
            print(f"    actor_{i}_animation_type={actor.get('animation_type')}")
            print(f"    actor_{i}_animation_genders={actor.get('animation_genders')}")
        print()

    # ================= 中文 HUMAN_LOCATOR =================
    for ord0 in TARGET_ORDS:
        tag = TARGET_PREFIX[ord0]
        fields, actors, _w = parsed[ord0]

        loc_raw = _first_value(fields, "animation_locations")
        locs = [x.strip() for x in (loc_raw or "").split(",") if x.strip()] if loc_raw else []
        loc_cn = "、".join(_cn_map(LOCATION_CN, x) for x in locs)
        if not locs:
            loc_cn = "UNKNOWN"

        custom_raw = _first_value(fields, "animation_custom_locations")
        custom_line = "UNKNOWN"
        if custom_raw:
            custs = [x.strip() for x in custom_raw.split(",") if x.strip()]
            custom_line = "、".join(custs)
            if all(not x.isdigit() for x in custs):
                custom_line = "、".join(_cn_map(LOCATION_CN, x) for x in custs)
            else:
                custom_line = "、".join(custs) + " (CUSTOM_LOCATION_NAME=UNRESOLVED, 不猜家具)"

        cat_raw = _first_value(fields, "animation_category")
        cat_line = "UNKNOWN"
        if cat_raw:
            cats = [x.strip() for x in cat_raw.split(",") if x.strip()]
            cat_line = "、".join(_cn_map(SEX_TYPE_CN, x) for x in cats)
        tags_raw = _first_value(fields, "animation_tags")
        tags = [x.strip() for x in (tags_raw or "").split(",") if x.strip()] if tags_raw else []
        tag_cn = "、".join(_cn_map(SEX_TYPE_CN, x) for x in tags)
        if not tags:
            tag_cn = "UNKNOWN"

        n_actors = len(actors)
        actor_types = [a.get("animation_type") for a in actors]
        atypes_cn = "、".join(_cn_map(ACTOR_TYPE_CN, x) for x in actor_types)

        genders = [a.get("animation_genders") for a in actors]
        genders_cn = "、".join(_cn_map(GENDER_CN, x) for x in genders)

        loops_raw = _first_value(fields, "animation_loops")

        print(f"{tag}_HUMAN_LOCATOR:")
        print(f"  名称={_first_value(fields, 'animation_raw_display_name')}")
        print(f"  场景位置={loc_cn}")
        if custom_raw:
            print(f"  自定义场景={custom_line}")
        print(f"  动作类型={cat_line}")
        print(f"  标签={tag_cn}")
        print(f"  参与人数={n_actors}")
        print(f"  参与者动作类型={atypes_cn}")
        print(f"  性别限制={genders_cn}")
        other = []
        if loops_raw:
            other.append(f"loops={loops_raw}")
        for i, a in enumerate(actors):
            aid = a.get("actor_id")
            if aid:
                other.append(f"actor{i}_id={aid}")
            clip = a.get("animation_clip_name")
            if clip:
                other.append(f"actor{i}_clip={clip}")
        other.append(f"author={_first_value(fields, 'animation_author')}")
        other.append(f"allowed_for_random={_first_value(fields, 'animation_allowed_for_random')}")
        print(f"  其他可定位信息={'; '.join(other)}")
        print()

    print("ZERO_WRITE_TO_MODS=YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
