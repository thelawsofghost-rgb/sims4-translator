#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEST C READ-ONLY TARGET LOCATOR + GAME SEARCH GUIDE + EASY CANARY CANDIDATES (只读)。

在【真实 Windows】上, 只从 source WW_Nevely42_Animations.package 读取, 输出:
  1. C1/C2/C3_LOCATOR:           三个 target (ordinal 0/239/478) 完整 entry metadata
                                  + 全部 direct child (tag/n/text) dump, 供人工判断。
  2. C1/C2/C3_HUMAN_LOCATOR:     真人可读信息 (严格仅 XML 明确证据)。
  3. GAME_SEARCH_GUIDE:          每个 target 的游戏内定位线索 + 菜单可见性判定。
  4. HUMAN_CANARY_USABILITY:     该 target 是否适合做人肉 canary (POOR 则不改 sidecar)。
  5. EASY_CANARY_CANDIDATES:     从 479-entry 源推荐 5 个更容易真人验证的普通动画。

【不改 sidecar / 不改 writer / 不重新生成 XML / 不碰 Mods / 不 production / 不 translation】。

中文映射原则 (仅 XML 明确枚举值 -> canonical 中文, 统一译法):
  - 场景: BED=床 FLOOR=地板 SOFA/COUCH=沙发 CHAIR=椅子 TABLE=桌子 ...
  - 性行为: VAGINAL=阴道性交 ANAL=肛交 ORAL=口交 HANDJOB=手交/手部刺激
            TEASING=挑逗 CLIMAX=高潮 MASTURBATION=自慰 ... (可给英文原值+中文)。
  - 角色: DOMINANT=主导 RECIPIENT=接受方 STIMULATOR=刺激方 ...
  - 无明确证据 -> UNKNOWN; 严禁根据 display name 猜身体部位/性行为/场景/人数。
  - animation_custom_locations 若为 numeric ID -> 保留 ID + CUSTOM_LOCATION_NAME=UNRESOLVED。

只输出 stdout。ZERO_WRITE_TO_MODS=YES。
"""

import argparse
import importlib.util
import re
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

SCRIPT_DIR = Path(__file__).resolve().parent
MODULE = SCRIPT_DIR / "ww_animation_canary_builder.py"
_spec = importlib.util.spec_from_file_location("ww_canary_builder", MODULE)
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)

WW_ANIM_XML = wb.WW_ANIM_XML
TARGET_ORDS = (0, 239, 478)
TARGET_PREFIX = {0: "C1", 239: "C2", 478: "C3"}

# canonical 中文映射 (仅 explicit XML 枚举值)
LOCATION_CN = {
    "BED": "床", "FLOOR": "地板", "SOFA": "沙发", "COUCH": "沙发",
    "CHAIR": "椅子", "TABLE": "桌子", "DESK": "书桌", "POOL": "泳池",
    "SHOWER": "淋浴", "BATH": "浴缸", "TUB": "浴缸", "WALL": "墙壁",
    "CAR": "汽车", "DOOR": "门", "COUNTER": "柜台",
    "KITCHEN_COUNTER": "厨房柜台", "DINING_TABLE": "餐桌", "WINDOW": "窗户",
    "YARD": "院子", "PLAYGROUND": "游乐场", "POOL_TUB": "泳池/浴缸",
    "HOT_TUB": "热水浴缸", "SHOWER_TUB": "淋浴/浴缸", "BENCH": "长椅",
    "STOOL": "凳子", "BED_FLOOR": "床/地板", "FLOOR_BED": "地板/床",
}
SEX_TYPE_CN = {
    "VAGINAL": "阴道性交", "ANAL": "肛交", "ORAL": "口交",
    "HANDJOB": "手交/手部刺激", "FOOTJOB": "足部刺激", "TITJOB": "乳交",
    "FEMDOM": "女性主导", "MALEDOM": "男性主导", "MATING_PRESS": "交配压制",
    "CLIMAX": "高潮", "ORGASM": "高潮", "TEASING": "挑逗",
    "MASTURBATION": "自慰", "MASTURBATING": "自慰", "FOREPLAY": "前戏",
    "EJACULATION": "射精", "CREAMPIE": "内射", "PETTING": "爱抚",
}
ACTOR_TYPE_CN = {
    "VAGINAL": "阴道性交", "ANAL": "肛交", "ORAL": "口交",
    "HANDJOB": "手部刺激", "CLIMAX": "高潮", "ORGASM": "高潮",
    "DOMINANT": "主导", "SUBMISSIVE": "顺从", "STIMULATOR": "刺激方",
    "RECIPIENT": "接受方", "TOP": "主动方", "BOTTOM": "被动方",
    "ACTIVE": "主动", "PASSIVE": "被动", "PENETRATIVE": "插入方",
    "PENETRATED": "被插入方",
}
GENDER_CN = {
    "MALE": "男性", "FEMALE": "女性", "MALE_FEMALE": "男/女",
    "FEMALE_MALE": "女/男", "TRANS_MALE": "跨性别男", "TRANS_FEMALE": "跨性别女",
    "ANY": "任意", "MIXED": "混合",
}
ANY_TAG = {"ANY", "ALL", "BOTH", "ANYONE", "MIXED", "ANY_MALE", "ANY_FEMALE"}

COMMON_LOCATIONS = {"BED", "FLOOR", "SOFA", "COUCH", "CHAIR", "TABLE", "DESK",
                    "POOL", "SHOWER", "BATH", "TUB", "WALL", "CAR", "DOOR",
                    "COUNTER", "KITCHEN_COUNTER", "DINING_TABLE", "WINDOW",
                    "YARD", "BENCH", "STOOL", "HOT_TUB"}


def _cn(mapping, val):
    """explicit 值 -> canonical 中文; 空白/ANY 类 -> UNKNOWN(任意); 无映射 -> UNKNOWN。"""
    if val is None:
        return "UNKNOWN"
    v = str(val).strip()
    if v == "":
        return "UNKNOWN"
    up = v.upper()
    if up in ANY_TAG:
        return "UNKNOWN(任意)"
    if v in mapping:
        return mapping[v]
    if up in mapping:
        return mapping[up]
    return "UNKNOWN"


def _cn_or_raw(mapping, val):
    """明确可枚举 -> 中文; 无映射但非空白 -> '原值(UNKNOWN)'; 空白/None -> UNKNOWN。"""
    if val is None:
        return "UNKNOWN"
    v = str(val).strip()
    if v == "":
        return "UNKNOWN"
    up = v.upper()
    if up in ANY_TAG:
        return "UNKNOWN(任意)"
    if v in mapping or up in mapping:
        return _cn(mapping, val)
    return f"{v}(UNKNOWN)"


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


def _parse_block(block_text):
    """ElementTree 只读解析 entry block: 返回 (fields, actors, direct_children, warnings)。"""
    fields = {}
    actors = []
    direct = []  # (tag, n, text) 全部 direct child, 供人工 dump
    warnings = []
    try:
        root = ET.fromstring(block_text.encode("utf-8"))
    except Exception as e:
        return fields, actors, direct, [f"parse_warn: {e}"]

    for child in root:
        tag = child.tag.rsplit("}", 1)[-1]
        n = child.get("n")
        text = (child.text or "").strip()
        direct.append((tag, n, text))
        if tag == "T" and n is not None and n not in fields:
            fields[n] = text

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
    return fields, actors, direct, warnings


def _fv(fields, key):
    return fields.get(key)


def _all_text_nodes(block_text):
    """只读：递归 dump entry XML 内【所有文本节点】的路径 + n + text(去空白)。
    用于寻找 WW UI 实际读取的 display 字段(如 stage 内嵌套字段)。返回 [(path, n, text), ...]。"""
    out = []
    try:
        root = ET.fromstring(block_text.encode("utf-8"))
    except Exception as e:
        return [("<parse_error>", None, str(e))]

    def walk(el, path):
        tag = el.tag.rsplit("}", 1)[-1]
        p = f"{path}/{tag}"
        n = el.get("n")
        lab = p + (f"[@n={n!r}]" if n is not None else "")
        # 元素自身文本(若有非空)
        txt = (el.text or "").strip()
        if txt:
            out.append((lab, n, txt))
        # 递归子元素 (含 tail 已并入各自元素 text)
        for sub in el:
            walk(sub, p)

    walk(root, "")
    return out


def _split_list(raw):
    return [x.strip() for x in (raw or "").split(",") if x.strip()] if raw else []


def _has_custom_location(fields):
    cr = _fv(fields, "animation_custom_locations")
    if cr is None:
        return False, None
    custs = _split_list(cr)
    if not custs:
        return False, None
    return True, custs


def _locations(fields):
    loc = _split_list(_fv(fields, "animation_locations"))
    if not loc:
        return []
    # 清掉空白/None
    return loc


def _is_sequence(fields, all_displays, ord0, name):
    """sequence 判定 (仅结构证据, 不用来猜身体/场景):
    - 明确 sequence 字段, 或
    - 同 stem 且有 phase 后缀的兄弟 entry 存在。
    """
    # 1) 明确 XML sequence/member 字段
    for k in ("animation_sequence", "is_sequence_part", "sequence_part",
              "animation_phase", "sequence_index", "is_climax"):
        if _fv(fields, k) not in (None, "", "False", "false", "0"):
            return True
    # 2) actor animation_type 若含 CLIMAX/ORGASM 且 loops>1 -> 高潮段可能 (弱信号, 不硬判)
    # 3) 结构: 兄弟 entry 同 stem + 数字/phase 后缀
    if not name:
        return False
    stem = re.split(r"[-_]", name.strip())[0] if re.search(r"[-_]", name.strip()) else name.strip()
    if len(stem) < 3:
        return False
    phases = 0
    phase_re = re.compile(
        re.escape(stem) + r"(?:[-_](?:\d+|start|loop|end|climax|intro|outro))", re.I)
    for d in all_displays:
        if d == name:
            continue
        if phase_re.search(d or ""):
            phases += 1
    return phases >= 1


def _game_guide(fields, actors):
    """基于 XML 证据的菜单可见性判定。"""
    locs = _locations(fields)
    custom, custs = _has_custom_location(fields)
    n_actors = len(actors)
    genders = set()
    for a in actors:
        g = (a.get("animation_genders") or "").upper()
        if g and g not in ANY_TAG:
            genders.add(g)
    cats = set(x.upper() for x in _split_list(_fv(fields, "animation_category")))
    tags = set(x.upper() for x in _split_list(_fv(fields, "animation_tags")))

    gen_locs = [x.upper() for x in locs]
    has_bed = any(x in ("BED", "BED_FLOOR", "FLOOR_BED") for x in gen_locs)
    has_sofa = any(x in ("SOFA", "COUCH", "BENCH") for x in gen_locs)
    has_floor = any(x == "FLOOR" for x in gen_locs)

    def verdict(in_list):
        if custom:
            # 有 numeric custom location -> 无法确认通用菜单可见
            return "UNKNOWN"
        if in_list:
            return "YES"
        return "NO" if gen_locs else "UNKNOWN"

    visibility = {
        "floor": verdict(has_floor),
        "bed": verdict(has_bed),
        "sofa": verdict(has_sofa),
    }
    # 若无任何 location -> UNKNOWN
    if not gen_locs:
        visibility = {"floor": "UNKNOWN", "bed": "UNKNOWN", "sofa": "UNKNOWN"}
    return {
        "locs": gen_locs,
        "custom": custom,
        "n_actors": n_actors,
        "genders": sorted(genders),
        "category": sorted(cats),
        "tags": sorted(tags),
        "visibility": visibility,
        "recommended": ("CUSTOM" if custom else
                        ("BED" if has_bed else
                         ("SOFA" if has_sofa else
                          ("FLOOR" if has_floor else
                           (gen_locs[0] if gen_locs else "UNKNOWN"))))),
    }


def _usability(guide, is_seq):
    """判断是否适合做人肉 canary。返回 (grade, reasons)。"""
    reasons = []
    grade = "GOOD"
    if guide["custom"]:
        reasons.append("需要特殊 custom location")
        grade = "POOR"
    if is_seq:
        reasons.append("可能是 sequence 中一段(非独立列出)")
        grade = "POOR"
    if guide["n_actors"] == 0:
        reasons.append("actor_count 不明/0")
        grade = "POOR"
    if guide["recommended"] == "UNKNOWN":
        reasons.append("无明确 common location")
        if grade != "POOR":
            grade = "FAIR"
    return grade, reasons


def _safe_xpath(el, parent_map):
    """基于 parent_map 祖先链构造简单 XPath (tag|n 属性)。"""
    segs = []
    node = el
    while node is not None:
        tag = node.tag.rsplit("}", 1)[-1]
        n = node.get("n")
        seg = tag + (f"[@n={n!r}]" if n is not None else "")
        segs.append(seg)
        node = parent_map.get(node)
    return "/".join(reversed(segs))


def _classify_container(el, parent_map):
    """根据祖先链(经 parent_map)判断节点所在容器类型。"""
    kinds = []
    node = el
    while node is not None:
        tag = node.tag.rsplit("}", 1)[-1]
        n = node.get("n")
        if n:
            if "stage" in n.lower():
                kinds.append("stage")
            elif "actor" in n.lower():
                kinds.append("actor")
            elif "override" in n.lower():
                kinds.append("override")
            elif "meta" in n.lower():
                kinds.append("metadata")
            elif "list" in n.lower() or tag == "L":
                if kinds and kinds[-1] != "list":
                    kinds.append("list")
            else:
                if kinds and kinds[-1] != "other":
                    kinds.append("other")
        node = parent_map.get(node)
    if not kinds:
        kinds.append("root")
    return ",".join(dict.fromkeys(kinds))


def _parse_block_tree(block_text):
    """ET 解析并构建 parent_map。返回 (root, parent_map, warnings)。"""
    try:
        root = ET.fromstring(block_text.encode("utf-8"))
    except Exception as e:
        return None, {}, [f"parse_warn: {e}"]
    pmap = {}
    for parent in root.iter():
        for child in parent:
            pmap[child] = parent
    return root, pmap, []


def _text_equal_nodes(block_text, needle):
    """只读：返回文本精确等于 needle 的所有节点详情。
    每个: (xpath, n, text, tag, container_class)。"""
    root, pmap, wrn = _parse_block_tree(block_text)
    if root is None:
        return [], wrn
    out = []
    for el in root.iter():
        txt = (el.text or "").strip()
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "T" and txt == needle:
            out.append((_safe_xpath(el, pmap), el.get("n"), txt, tag, _classify_container(el, pmap)))
    return out, wrn


def _field_names(root, pmap):
    """返回 entry 树中全部 T 节点的 (n, 容器类别, 文本前32字)。"""
    out = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "T":
            n = el.get("n")
            txt = (el.text or "").strip()
            out.append((n, _classify_container(el, pmap), txt[:40]))
    return out


def _candidate_field_census(fields_list):
    """对全部 entry 统计候选字段名的出现次数 (用于判断是否存在)。"""
    wanted = ["display_name", "title", "name", "stage_name", "animation_name",
              "ui_name", "override", "display", "label", "loc_key", "string",
              "tooltip", "description"]
    cnt = {w: 0 for w in wanted}
    for f in fields_list:
        for w in wanted:
            for k in f.keys():
                if w in k.lower():
                    cnt[w] += 1
    return cnt


def main():
    import argparse as _ap
    ap = _ap.ArgumentParser()
    ap.add_argument("--source", required=True, help="真实 source .package")
    ap.add_argument("--easy-top", type=int, default=5, help="EASY_CANARY_CANDIDATES 数量")
    ap.add_argument("--ordinals", default="",
                    help="可选: 覆盖 target 集, 逗号分隔 0-based ordinals, 如 --ordinals 35,36,82 (只读, 不改 writer)")
    ap.add_argument("--text", default="",
                    help="可选: 只输出文本值精确等于该字符串的 XML 节点 (含完整路径/父结构/容器分类), 不填则照常 dump")
    ap.add_argument("--field-census", action="store_true",
                    help="可选: 对 target entry 输出全部存在字段名(n=)清单 + 你关心的候选字段匹配情况")
    a = ap.parse_args()
    # target 集: 默认 C1/C2/C3 (0/239/478); 传 --ordinals 则用之(前缀按位置 TAG)
    if a.ordinals.strip():
        try:
            custom_ords = tuple(int(x) for x in a.ordinals.split(",") if x.strip() != "")
        except ValueError:
            print("ERROR: --ordinals 须为逗号分隔整数", file=sys.stderr)
            return 7
        target_ords = custom_ords
        target_prefix = {o: f"O{o}" for o in target_ords}
    else:
        target_ords = TARGET_ORDS
        target_prefix = dict(TARGET_PREFIX)
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

    # 预解析全部 entry (用于 sequence 检测 + easy candidates)
    all_fields = []
    all_actors = []
    all_direct = []
    all_names = []
    for ord0 in range(len(blocks)):
        btext, (_s, _e) = blocks[ord0]
        f, act, d, w = _parse_block(btext)
        all_fields.append(f)
        all_actors.append(act)
        all_direct.append(d)
        all_names.append(_fv(f, "animation_raw_display_name"))

    # ============ 1. target 完整 LOCATOR ============
    for ord0 in target_ords:
        tag = target_prefix[ord0]
        f = all_fields[ord0]
        act = all_actors[ord0]
        dn = _fv(f, "animation_raw_display_name")
        print(f"{tag}_LOCATOR:")
        print(f"  ENTRY_ORDINAL={ord0}")
        print(f"  DISPLAY_NAME={dn}")
        print(f"  animation_author={_fv(f, 'animation_author')}")
        print(f"  animation_locations={_fv(f, 'animation_locations')}")
        print(f"  animation_custom_locations={_fv(f, 'animation_custom_locations')}")
        print(f"  animation_category={_fv(f, 'animation_category')}")
        print(f"  animation_tags={_fv(f, 'animation_tags')}")
        print(f"  animation_loops={_fv(f, 'animation_loops')}")
        print(f"  animation_allowed_for_random={_fv(f, 'animation_allowed_for_random')}")
        print(f"  actor_count={len(act)}")
        for i, actor in enumerate(act):
            print(f"  actor_{i}_id={actor.get('actor_id')}")
            print(f"  actor_{i}_animation_clip_name={actor.get('animation_clip_name')}")
            print(f"  actor_{i}_animation_type={actor.get('animation_type')}")
            print(f"  actor_{i}_animation_genders={actor.get('animation_genders')}")
        # 额外: entry 全部 direct child (影响 WW UI 筛选/显示)
        skip = {"animation_raw_display_name", "animation_author"}
        extra = [d for d in all_direct[ord0] if d[1] and d[1] not in skip]
        if extra:
            print("  DIRECT_CHILDREN_DUMP:")
            for (tg, n, tx) in extra:
                print(f"    tag={tg} n={n} text={tx}")
        # 递归全部文本节点 (含 stages/嵌套 display 候选; 用于定位 WW UI 实际读的字段)
        tnode = _all_text_nodes(blocks[ord0][0])
        print("  ALL_TEXT_NODES_DUMP (path | n | text):")
        for (path, n, tx) in tnode:
            print(f"    {path}  n={n!r}  text={tx!r}")
        # 只读: 文本值精确等于 --text 的所有节点 (完整路径/父结构/容器)
        if a.text:
            eq, wrn2 = _text_equal_nodes(blocks[ord0][0], a.text)
            print(f"  TEXT_EQUAL_NODES for {a.text!r}:")
            if wrn2:
                for wmsg in wrn2:
                    print(f"    warn: {wmsg}")
            if not eq:
                print("    (无文本值精确相等的 T 节点)")
            for (xp, nattr, tx, tag, cls) in eq:
                print(f"    XPath={xp}")
                print(f"      n={nattr!r}")
                print(f"      text={tx!r}")
                print(f"      tag={tag}")
                print(f"      container={cls}  (stage/list/override/metadata 判定)")
        # 只读: 该 entry 全部字段名 (含候选字段是否存在)
        if a.field_census:
            root, pmap3, wrn3 = _parse_block_tree(blocks[ord0][0])
            print(f"  ENTRY_FIELD_CENSUS (n | container | text[:40]):")
            if root is not None:
                for (nattr, cls, tx) in _field_names(root, pmap3):
                    print(f"    n={nattr!r}  container={cls}  text={tx!r}")
        print()

    # ============ 2. HUMAN_LOCATOR + 3. GAME_SEARCH_GUIDE + 4. USABILITY ============
    for ord0 in target_ords:
        tag = target_prefix[ord0]
        f = all_fields[ord0]
        act = all_actors[ord0]
        dn = _fv(f, "animation_raw_display_name")
        locs = _locations(f)
        custom, custs = _has_custom_location(f)
        is_seq = _is_sequence(f, all_names, ord0, dn)
        guide = _game_guide(f, act)
        usability, ureasons = _usability(guide, is_seq)

        loc_cn = "、".join(_cn_or_raw(LOCATION_CN, x) for x in locs) if locs else "UNKNOWN"
        custom_line = "UNKNOWN"
        if custom:
            if all(not x.isdigit() for x in custs):
                custom_line = "、".join(_cn_or_raw(LOCATION_CN, x) for x in custs)
            else:
                custom_line = "、".join(custs) + " (CUSTOM_LOCATION_NAME=UNRESOLVED, 不猜家具)"

        cat_line = "、".join(_cn_or_raw(SEX_TYPE_CN, x) for x in _split_list(_fv(f, "animation_category")))
        if not _split_list(_fv(f, "animation_category")):
            cat_line = "UNKNOWN"
        tag_line = "、".join(_cn_or_raw(SEX_TYPE_CN, x) for x in _split_list(_fv(f, "animation_tags")))
        if not _split_list(_fv(f, "animation_tags")):
            tag_line = "UNKNOWN"

        atypes_cn = "、".join(_cn_or_raw(ACTOR_TYPE_CN, a.get("animation_type")) for a in act)
        genders_cn = "、".join(_cn_or_raw(GENDER_CN, a.get("animation_genders")) for a in act)
        seq_line = "可能(含 phase 兄弟/structure 证据)" if is_seq else "UNKNOWN/未见明确 sequence 结构"

        print(f"{tag}_HUMAN_LOCATOR:")
        print(f"  名称={dn}")
        print(f"  作者={_fv(f, 'animation_author')}")
        print(f"  需要的场景/物体={loc_cn}")
        if custom:
            print(f"  自定义物体={custom_line}")
        print(f"  动作大类={cat_line}")
        print(f"  动作标签={tag_line}")
        print(f"  参与人数={len(act)}")
        print(f"  参与者性别={genders_cn}")
        print(f"  参与者动作类型={atypes_cn}")
        print(f"  是否可能为 animation sequence 中的一段={seq_line}")
        other = []
        if _fv(f, "animation_loops"):
            other.append(f"loops={_fv(f,'animation_loops')}")
        if _fv(f, "animation_allowed_for_random"):
            other.append(f"allowed_for_random={_fv(f,'animation_allowed_for_random')}")
        for i, actor in enumerate(act):
            if actor.get("animation_clip_name"):
                other.append(f"actor{i}_clip={actor.get('animation_clip_name')}")
        print(f"  其他游戏内定位线索={'; '.join(other) if other else 'UNKNOWN'}")
        print()

        # GAME_SEARCH_GUIDE
        print(f"{tag}_GAME_SEARCH_GUIDE:")
        print(f"  recommended_object_or_location={guide['recommended']}")
        print(f"  required_actor_count={guide['n_actors']}")
        print(f"  required_genders={'/'.join(guide['genders']) if guide['genders'] else 'UNKNOWN'}")
        print(f"  likely_category={'/'.join(guide['category']) if guide['category'] else 'UNKNOWN'}")
        print(f"  likely_tags={'/'.join(guide['tags']) if guide['tags'] else 'UNKNOWN'}")
        v = guide["visibility"]
        print(f"  VISIBLE_IN_GENERIC_FLOOR_MENU={v['floor']}")
        print(f"  VISIBLE_IN_BED_MENU={v['bed']}")
        print(f"  VISIBLE_IN_SOFA_MENU={v['sofa']}")
        print()

        # HUMAN_CANARY_USABILITY
        print(f"{tag}_HUMAN_CANARY_USABILITY={usability}")
        if ureasons:
            print(f"  reasons: {'; '.join(ureasons)}")
        print()

    # ============ 5. EASY_CANARY_CANDIDATES ============
    scored = []
    seen = set()
    for ord0 in range(len(blocks)):
        if ord0 in target_ords:
            continue
        f = all_fields[ord0]
        act = all_actors[ord0]
        dn = _fv(f, "animation_raw_display_name") or ""
        locs_l = _locations(f)
        custom, _c = _has_custom_location(f)
        is_seq = _is_sequence(f, all_names, ord0, dn)
        score = 0
        why = []
        # 场景: 常见 location 且非 custom
        if custom:
            continue
        common = [x for x in locs_l if x.upper() in COMMON_LOCATIONS]
        if not common:
            continue  # 必须落在常见 location
        if any(x.upper() in ("BED", "FLOOR", "SOFA", "COUCH") for x in locs_l):
            score += 3
            why.append("含 BED/FLOOR/SOFA 常见场景")
        else:
            score += 1
            why.append("含常见场景")
        # 独立可识别: 非 sequence
        if is_seq:
            continue
        score += 2
        why.append("非 sequence/可独立列出")
        # actor_count 明确
        if len(act) == 2:
            score += 2
            why.append("双人明确")
        elif len(act) >= 1:
            score += 1
            why.append(f"{len(act)}人明确")
        else:
            continue
        # display 名易搜索: 不含大量特殊符号/CUSTOM VOICES
        if "*" not in dn and "CUSTOM VOICES" not in dn.upper():
            score += 1
            why.append("display 名简洁易搜")
        # 无特殊 gender/type 条件
        # 允许 for random
        key = (dn, tuple(sorted(locs_l)), len(act))
        if key in seen:
            continue
        seen.add(key)
        scored.append((score, ord0, dn, locs_l, _fv(f, "animation_category"),
                       _fv(f, "animation_tags"), len(act), why))

    scored.sort(key=lambda x: (-x[0], x[1]))
    print("EASY_CANARY_CANDIDATES:")
    for (score, ord0, dn, locs_l, cat, tags, na, why) in scored[:a.easy_top]:
        print(f"  ordinal={ord0}")
        print(f"    display={dn}")
        print(f"    location={'/'.join(locs_l)}")
        print(f"    category={cat}")
        print(f"    tags={tags}")
        print(f"    actor_count={na}")
        print(f"    why_easy={'; '.join(why)}")
    if not scored:
        print("  (无可推荐)")

    if a.field_census:
        print("CANDIDATE_FIELD_GLOBAL_CENSUS (跨 479 entry, 出现 entry 数):")
        cnt = _candidate_field_census(all_fields)
        for w in sorted(cnt, key=lambda k: -cnt[k]):
            print(f"  {w}: {cnt[w]} entries")
        # 全部 distinct 字段名 (全局)
        all_names_set = {}
        for f in all_fields:
            for k in f.keys():
                all_names_set[k] = all_names_set.get(k, 0) + 1
        print("ALL_FIELD_NAMES_GLOBAL (distinct n=, entry 出现次数):")
        for k in sorted(all_names_set, key=lambda x: -all_names_set[x]):
            print(f"  {k!r}: {all_names_set[k]}")
        print()

    print()
    print("ZERO_WRITE_TO_MODS=YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
