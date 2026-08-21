#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEST C: 大型 479-entry package 的独立 exact-TGI WW XML sidecar 研究 (WW Animation)。

目标: 验证一个【单 WW XML + 479 animation entries】的大包 (NEVELY42), 能否在完全不修改
原 source 的前提下, 通过 source + 独立 exact-TGI WW XML sidecar 实现【多个 (3 个)、相隔很远】
animation_raw_display_name 的安全覆盖。

真实 corpus V1.1 TRIAGE 已确认为 SECOND_CANARY:
  C:\\Users\\thela\\Documents\\Electronic Arts\\The Sims 4\\Mods\\2026.7.20\\WW_Nevely42_Animations.package
  ww_xml_count=1, animation_entry_count=479, EXACT_ONE=479,
  tgi_collision_count=0, cross_package_collision_count=0, parse_fail_count=0, anomaly_count=0,
  SECOND_CANARY_PREFLIGHT_PASS=YES

方法 (与 TEST A/B 同源的 source-faithful writer + 真实 tuning 定位):
  - 只读 source 唯一 WW_ANIM_XML (0x7DF2169C), TGI 从 source 读取 (不硬编码)。
  - 在 animations_list 的 <U> entry 序列内, 按【0-based ordinal = 0 / 239 / 478】定位
    三个相距很远的 entry (首 / 中 / 末)。
      - C1: entry ordinal 0   (1-based 1,  首位)
      - C2: entry ordinal 239 (1-based 240, 479 之正中, 对应 spec 建议 ordinal 240)
      - C3: entry ordinal 478 (1-based 479, 末位)
    明确输出真实 0-based ordinal。绝不写死原英文名 —— 从 source 真实读取该 entry 的
    animation_raw_display_name 作为 OLD。
  - 每个目标 entry 内, 精确替换其唯一 <T n="animation_raw_display_name">OLD</T> 的 inner text:
      C1: OLD_1 -> 【大包C1】OLD_1
      C2: OLD_2 -> 【大包C2】OLD_2
      C3: OLD_3 -> 【大包C3】OLD_3
    仅保留原 display 文本并加中文 canary 前缀 (验证覆盖/定位能力, 不混入翻译质量)。
  - 禁止: 全局 str.replace / ordinal child guessing / 第一个匹配 T / 文本子串全局替换。
    只允许 3 个目标 entry 的 3 个 display node 改变。

侧车架构:
  - 不修改/替换/重建 source; SOURCE_FILE_BYTES_UNCHANGED=YES。
  - 只生成【1 个 resource】的独立 package; 唯一资源 = source 唯一 WW XML 的 exact same TGI。
  - 禁止 CLIP / STBL / ANIM RCOL / source 其他 resource / 新 TGI。

source-faithful writer (与 TEST A/B 相同规则):
  - package DBPF version: major/minor/header_comp 保留 source; 预期 2.1。
  - WW XML: offset/size high bit 保留 source; field8/compression model 保留 source;
    field7 = 修改后 XML 实际 decompressed 长度; stored size = 修改后 compressed body 实际长度。

真机部署候选: !WW_Nevely42_Animations_CHS_TESTC.package (字典序更早, 但仅是候选,
不泛化为绝对 Sims/WW load-order 规则; 本轮【不自动部署】)。

只生成 artifact + 静态验证; 不写 Mods / 不 swap / 不真机 / 不全量翻译 / 不 production。
ZERO_WRITE_TO_MODS=YES。
"""

import argparse
import importlib.util
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MODULE = SCRIPT_DIR / "ww_animation_canary_builder.py"

_spec = importlib.util.spec_from_file_location("ww_canary_builder", MODULE)
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)

WW_ANIM_XML = wb.WW_ANIM_XML
DISP_FIELD = "animation_raw_display_name"
ENTRY_LIST_FIELD = "animations_list"

# 0-based ordinals: 首 / 中 / 末 (479 entries)
# C2 中间: 0-based 239 == 1-based 240 (479 之正中, spec 建议 ordinal 240)
C_TARGETS = [
    (0,   "C1", "大包C1"),
    (239, "C2", "大包C2"),
    (478, "C3", "大包C3"),
]
TARGET_ORDS = {o for o, _t, _p in C_TARGETS}

# Easy 真人验证版 (locator 推荐, 普通菜单可定位): 不改架构, 仅换 target 集。
# 每 target 只改 display, 前缀不带数字后缀以免影响定位。
EASY_TARGETS = [
    (35,  "EASY_C1", "大包EASY_C1"),
    (36,  "EASY_C2", "大包EASY_C2"),
    (82,  "EASY_C3", "大包EASY_C3"),
]
EASY_ORDS = {o for o, _t, _p in EASY_TARGETS}
EASY_OUT_DIR = "output/ww_animation_testc_easy_nevely42"
EASY_PASS_KEY = "TEST_C_EASY_STATIC_PASS"
SH_EXPECTED = "cd0093f2ec4b896121fa465672584c12384465b631c1d9128fe97d360b87d416"


# ============================================================ raw 文本切分 (entry-scoped edit)
def _entry_blocks(list_text: str):
    """扫 raw XML text, 按 depth 找顶层 <U>...</U> block 的字节区间 (容忍内嵌 actor <U>)。

    返回 list[(block_text, (start, end))]; end 含闭标签 </U> (字节级保留)。
    这是【不覆盖 gaps】的定位版, 供 ordinal/OLD 读取与验证用。
    """
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


def _entry_partition(list_text: str):
    """把 animations_list inner text 切成【完整覆盖分区】: 交替的 gap 与 entry block。

    返回 [(is_entry, text, (start,end))]; 所有区间首尾相接、无空隙无重叠,
    [0..] 到 [len..] 全覆盖, 从而重建时可逐字节保留所有非目标 whitespace。
    """
    blocks = _entry_blocks(list_text)
    parts = []
    pos = 0
    for btext, (bst, be) in blocks:
        if bst > pos:
            parts.append((False, list_text[pos:bst], (pos, bst)))
        parts.append((True, btext, (bst, be)))
        pos = be
    if pos < len(list_text):
        parts.append((False, list_text[pos:], (pos, len(list_text))))
    return parts


def _replace_display_in_block(block_text: str, new_val: str):
    """entry block 内精确替换 <T n=animation_raw_display_name>OLD</T> inner text。

    定位: 每 U block 应恰有 1 个 display 节点 (WW 结构); 否则 FAIL-CLOSED (不猜 ordinal)。
    返回 (old_val, new_block_text, node_count)。
    """
    pat = re.compile(
        r'(<T\s+[^>]*\bn\s*=\s*"%s"[^>]*>)([^<]*)(</T>)' % re.escape(DISP_FIELD), re.S
    )
    matches = list(pat.finditer(block_text))
    if len(matches) != 1:
        return None, block_text, len(matches)
    m = matches[0]
    old = m.group(2)
    new_block = block_text[:m.start(2)] + new_val + block_text[m.end(2):]
    return old, new_block, len(matches)


def _locate_animations_list(xml_text: str):
    """定位 <L n="animations_list"> ... </L> 的 inner 区间。返回 (inner_text, inner_start, inner_end)。"""
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


# ============================================================ TEST C 专属 semantic 比较
def _display_values(text):
    """按序抽全部 display inner text (用于 476 unchanged 逐 entry 校验)。"""
    pat = re.compile(r'<T\s+[^>]*\bn\s*=\s*"%s"[^>]*>([^<]*)</T>' % re.escape(DISP_FIELD), re.S)
    return [m.group(1) for m in pat.finditer(text)]


def _is_display_node(el):
    tag = el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else None
    return tag == "T" and el.get("n") == DISP_FIELD


def _tree_diff(a, b, path, allow_display_text):
    """递归比较两 entry; 仅允许 display text 改变。返回 dict 或 None (display 变在非目标 entry)。"""
    tag_a = a.tag.rsplit("}", 1)[-1] if isinstance(a.tag, str) else None
    tag_b = b.tag.rsplit("}", 1)[-1] if isinstance(b.tag, str) else None
    res = {"display_changed": False, "internal": []}
    if tag_a != tag_b or a.attrib != b.attrib:
        res["internal"].append((path, f"tag/attr {tag_a}{dict(a.attrib)} != {tag_b}{dict(b.attrib)}"))
    if a.tail != b.tail:
        res["internal"].append((path, f"tail {a.tail!r} != {b.tail!r}"))
    ca, cb = list(a), list(b)
    if len(ca) != len(cb):
        res["internal"].append((path, f"child count {len(ca)} != {len(cb)}"))
    for k in range(max(len(ca), len(cb))):
        if k >= len(ca) or k >= len(cb):
            res["internal"].append((path, f"child#{k} missing"))
            continue
        x, y = ca[k], cb[k]
        if _is_display_node(x) and _is_display_node(y):
            if x.text != y.text:
                if not allow_display_text:
                    return None
                res["display_changed"] = True
            continue
        sub = _tree_diff(x, y, f"{path}/{tag_a}#{k}", allow_display_text)
        if sub is None:
            return None
        res["display_changed"] = res["display_changed"] or sub["display_changed"]
        res["internal"].extend(sub["internal"])
    return res


def _entry_paired_semantic_diff(src_text: str, can_text: str, target_ordinals: set):
    """ordinal-by-ordinal entry 配对比较 (不 collapse; 适配多 entry 大包)。

    返回 (display_change_count, internal_diffs)。display text 变化仅在 target_ordinals 内允许。
    """
    try:
        r1 = ET.fromstring(src_text)
    except Exception as e:
        return None, [("PARSE-SRC", str(e))]
    try:
        r2 = ET.fromstring(can_text)
    except Exception as e:
        return None, [("PARSE-CANARY", str(e))]

    def _find_list(root):
        for node in root.iter():
            tag = node.tag.rsplit("}", 1)[-1] if isinstance(node.tag, str) else None
            if tag == "L" and node.get("n") == ENTRY_LIST_FIELD:
                return node
        return None

    l1, l2 = _find_list(r1), _find_list(r2)
    if l1 is None or l2 is None:
        return None, [("NO-ANIMATIONS-LIST", "")]
    e1 = [c for c in list(l1) if (c.tag.rsplit("}", 1)[-1] if isinstance(c.tag, str) else None) == "U"]
    e2 = [c for c in list(l2) if (c.tag.rsplit("}", 1)[-1] if isinstance(c.tag, str) else None) == "U"]
    if len(e1) != len(e2):
        return None, [(f"ENTRY-COUNT {len(e1)} vs {len(e2)}", "")]

    display_changes = 0
    internal = []
    for i, (a, b) in enumerate(zip(e1, e2)):
        sub = _tree_diff(a, b, f"entry[{i}]", i in target_ordinals)
        if sub is None:
            internal.append((f"entry[{i}]", "display-text-change-outside-target"))
            continue
        if sub["display_changed"]:
            display_changes += 1
        for d in sub["internal"]:
            internal.append(d)
    return display_changes, internal


# ============================================================ 主流程
def run(src: Path, out_dir: Path, force: bool, expected_sha: str, variant: bool = False) -> int:
    # 选择 target 集 (False=C_TARGETS 原版; True=EASY_TARGETS)。不改架构, 仅换集。
    targets = EASY_TARGETS if variant else C_TARGETS
    ords = EASY_ORDS if variant else TARGET_ORDS
    pass_key = EASY_PASS_KEY if variant else "TEST_C_LARGE_PACKAGE_STATIC_PASS"
    print(f"RUN_MODE={'EASY' if variant else 'ORIGINAL'}")
    src_sha_before = wb.sha256(src)

    # ---- 解析 source: 必须恰好 1 个 WW XML ----
    idx, err = wb.safe_parse(src)
    if err is not None or idx is None:
        print(f"ERROR: source 解析失败: {err}", file=sys.stderr)
        return 3
    ww = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    if len(ww) != 1:
        print(f"ERROR: 需单 WW_ANIM_XML; 实际 = {len(ww)} (fail-closed)", file=sys.stderr)
        return 3
    ww_e = ww[0]
    src_tgi = (ww_e.type_id, ww_e.group_id, ww_e.instance_id)
    src_tgi_str = f"0x{ww_e.type_id:08X}/0x{ww_e.group_id:08X}/0x{ww_e.instance_id:016X}"

    # ---- source metadata (source-faithful) ----
    src_major, src_minor, hdr_comp, entries_meta = wb.read_entry_meta_raw(src)
    if len(entries_meta) != len(idx.entries):
        print(f"ERROR: 源 index metadata 数与解析条目数不一致 ({len(entries_meta)} vs {len(idx.entries)})", file=sys.stderr)
        return 3
    ww_meta = None
    for m in entries_meta:
        if (m["type"], m["group"], m["inst"]) == src_tgi:
            ww_meta = m
            break
    if ww_meta is None:
        print("ERROR: 找不到 WW XML 的原始 index metadata (fail-closed)", file=sys.stderr)
        return 3
    src_off_hi = int(ww_meta["offset_comp"])
    src_sz_hi = int(ww_meta["size_comp"])
    src_f8 = ww_meta["comp_type"]
    src_path = str(src)
    src_file_size = src.stat().st_size

    # ---- 读 source WW XML body -> logical XML ----
    body_orig = wb.read_body_raw(src, ww_e)
    _schema, xml_text_orig, xerr = wb.parse_anim_xml(body_orig)
    if xerr is not None:
        print(f"ERROR: WW XML 解析失败: {xerr}", file=sys.stderr)
        return 3

    # ---- 定位 animations_list inner text ----
    inner, inner_start, inner_end = _locate_animations_list(xml_text_orig)
    if inner is None:
        print(f"ERROR: 找不到 <L n={ENTRY_LIST_FIELD!r}> (fail-closed)", file=sys.stderr)
        return 3
    blocks = _entry_blocks(inner)
    n_entries = len(blocks)
    if n_entries != 479:
        print(f"ERROR: animations_list 顶层 <U> 数 = {n_entries} (须 479) (fail-closed)", file=sys.stderr)
        return 3

    # ---- 对 3 个目标 entry 做 entry-scoped display 替换 ----
    edits = []
    used = set()
    for ordinal0, tag, prefix in targets:
        if ordinal0 >= n_entries:
            print(f"ERROR: target ordinal {ordinal0} 超出 entry 数 {n_entries} (fail-closed)", file=sys.stderr)
            return 3
        if ordinal0 in used:
            print(f"ERROR: ordinal {ordinal0} 重复 (fail-closed)", file=sys.stderr)
            return 3
        used.add(ordinal0)
        btext, (_bst, _be) = blocks[ordinal0]
        old, _nb, node_cnt = _replace_display_in_block(btext, "X")  # 先探 OLD
        if node_cnt != 1:
            print(f"ERROR: entry[{ordinal0}] display node 数 = {node_cnt} (须 1) (fail-closed)", file=sys.stderr)
            return 3
        new_val = f"【{prefix}】{old}"
        old2, new_block, node_cnt2 = _replace_display_in_block(btext, new_val)
        if node_cnt2 != 1 or old2 != old:
            print(f"ERROR: entry[{ordinal0}] display 二次替换不一致 (fail-closed)", file=sys.stderr)
            return 3
        edits.append({"ordinal0": ordinal0, "tag": tag, "prefix": prefix,
                      "old": old, "new": new_val, "block": new_block})

    # 三个 OLD 来自三个不同 entry (由不同 ordinal 保证); 若文本重复仅警告 (大包内 animation 名重复合法)。
    olds = [e["old"] for e in edits]
    if len(set(olds)) != 3:
        print(f"WARN: 三个 OLD 文本有重复 (仍来自 3 个不同 entry): {olds}", file=sys.stderr)

    # ---- 拼接新的 animations_list inner (覆盖分区: 逐字节保留全部 gap whitespace) ----
    part_edits = {e["ordinal0"]: e["block"] for e in edits}
    new_inner_parts = []
    entry_idx = 0
    for is_entry, ptext, (_ps, _pe) in _entry_partition(inner):
        if is_entry:
            if entry_idx in part_edits:
                new_inner_parts.append(part_edits[entry_idx])
            else:
                new_inner_parts.append(ptext)  # 原样保留 (含该 entry 全部 bytes)
            entry_idx += 1
        else:
            new_inner_parts.append(ptext)  # gap whitespace 原样保留
    new_inner = "".join(new_inner_parts)
    new_xml = xml_text_orig[:inner_start] + new_inner + xml_text_orig[inner_end:]

    # ---- TEST C 专属语义差异 (以关闭 artifact 后重读为准; 此处仅作生成前自检) ----
    _dc, _intl = _entry_paired_semantic_diff(xml_text_orig, new_xml, ords)
    if _dc is not None and _dc != 3 and not _intl:
        print(f"ERROR: 生成前语义差异异常 display={_dc} (须 3) (fail-closed)", file=sys.stderr)
        return 3

    # ---- 476 未改 display 逐 entry source-equivalent ----
    dv_src = _display_values(xml_text_orig)
    dv_new = _display_values(new_xml)
    unchanged_count = 0
    unchanged_eq = True
    if len(dv_src) != n_entries or len(dv_new) != n_entries:
        unchanged_eq = False
    else:
        for i in range(n_entries):
            if i in ords:
                continue
            if dv_src[i] != dv_new[i]:
                unchanged_eq = False
                break
            unchanged_count += 1

    # ---- 构造 sidecar 的 WW XML body (压缩模型与 source 一致) ----
    new_body = wb.compress_like(body_orig, new_xml.encode("utf-8"))
    new_decomp_len = len(wb.decompress_maybe(new_body))
    new_stored_len = len(new_body)

    # ---- 唯一 resource 的 meta: high bits/field8 保留 source, field7=新解压长度 ----
    item_meta = {
        "comp_state": True,
        "comp_type": src_f8,
        "mem_size": new_decomp_len,
        "offset_high_bit": src_off_hi,
        "size_high_bit": src_sz_hi,
    }

    # ---- 生成 sidecar package ----
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{src.stem}_SIDECAR_TESTC.package"
    if out.exists() and not force:
        print(f"ERROR: SIDECAR_TESTC 已存在 (拒绝覆盖, 用 --force): {out}", file=sys.stderr)
        return 3
    wb.build_package(
        [(src_tgi[0], src_tgi[1], src_tgi[2], new_body, item_meta)],
        out, header_comp=hdr_comp, major=src_major, minor=src_minor,
    )
    side_sha = wb.sha256(out)

    # ---- 只读静态 gate ----
    src_file_unchanged = (wb.sha256(src) == src_sha_before)
    sidx, serr = wb.safe_parse(out)
    parser_ok = (serr is None and sidx is not None)
    s_count = len(sidx.entries) if sidx else -1
    s_tgi = (sidx.entries[0].type_id, sidx.entries[0].group_id, sidx.entries[0].instance_id) if (sidx and s_count == 1) else (None, None, None)
    tgi_equal = (s_count == 1 and s_tgi == src_tgi)
    side_body = wb.read_body_raw(out, sidx.entries[0]) if sidx and s_count == 1 else b""
    side_text = wb.decompress_maybe(side_body).decode("utf-8", "replace") if side_body else ""
    # 侧车 WW XML 与 source 的语义差异 (关闭 artifact 后重读)
    ds2, idf2 = _entry_paired_semantic_diff(xml_text_orig, side_text, ords)
    if ds2 is None:
        ds2 = 0
        idf2 = idf2 or [("PARSE", "")]
    ic2 = (1 if idf2 else 0)  # boolean: 是否有 internal diff
    internal2 = len(idf2)     # 真实 count (用于回显, 不含 padding)
    ranges_ok = _check_ranges(out, sidx)
    s_major, s_minor, _sc, s_meta = wb.read_entry_meta_raw(out)
    sm = s_meta[0] if (s_meta and len(s_meta) == 1) else None
    s_off_hi = int(sm["offset_comp"]) if sm else -1
    s_sz_hi = int(sm["size_comp"]) if sm else -1
    s_f8 = sm["comp_type"] if sm else -1
    s_f7 = sm["mem_size"] if sm else -1
    s_decomp = len(wb.decompress_maybe(side_body)) if side_body else -1
    s_stored = sm["size_raw"] & 0x7FFFFFFF if sm else -1
    dbpf_ver_equal = (src_major == s_major) and (src_minor == s_minor)
    off_hi_eq = (src_off_hi == s_off_hi)
    sz_hi_eq = (src_sz_hi == s_sz_hi)
    f8_eq = (src_f8 == s_f8)
    f7_matches = (s_f7 == s_decomp)
    stored_ok = (s_stored == new_stored_len)
    source_sha_verified = (src_sha_before == expected_sha)

    # 验证 side_text 里 3 个改动分别对应 C1/C2/C3 (置位于目标 entry 的 display 值)
    sv_src = _display_values(xml_text_orig)
    sv_new2 = _display_values(side_text)
    c_match = True
    if len(sv_src) == n_entries and len(sv_new2) == n_entries:
        for (ordinal0, tag, prefix) in targets:
            if sv_new2[ordinal0] != f"【{prefix}】{sv_src[ordinal0]}":
                c_match = False
    else:
        c_match = False

    all_gates = (
        s_count == 1 and tgi_equal and parser_ok and ranges_ok
        and dbpf_ver_equal and off_hi_eq and sz_hi_eq and f8_eq and f7_matches and stored_ok
        and ic2 == 0 and (ds2 is not None and ds2 == 3)
        and unchanged_eq and unchanged_count == n_entries - len(targets)
        and c_match and source_sha_verified and src_file_unchanged
    )

    # ---- report ----
    print("TEST_C_NEVELY42_EASY:" if variant else "TEST_C_NEVELY42:")
    print(f"  SOURCE_PATH={src_path}")
    print(f"  SOURCE_SHA={src_sha_before}")
    print(f"  SOURCE_FILE_SIZE={src_file_size}")
    print(f"  SCHEMA=WickedWhimsAnimationPackage")
    print(f"  WW_XML_COUNT={len(ww)}")
    print(f"  SOURCE_WW_XML_TGI={src_tgi_str}")
    print(f"  ANIMATION_ENTRY_COUNT={n_entries}")
    for e in edits:
        print(f"  {e['tag']}_ENTRY_ORDINAL={e['ordinal0']}")
        print(f"  {e['tag']}_OLD={e['old']}")
        print(f"  {e['tag']}_NEW={e['new']}")
    print(f"  TARGET_CANARY_ENTRY_COUNT={len(edits)}")
    print(f"  DISPLAY_SEMANTIC_DIFF_COUNT={ds2 if ds2 is not None else 'ERR'}")
    print(f"  UNCHANGED_DISPLAY_ENTRY_COUNT={unchanged_count}")
    print(f"  UNCHANGED_DISPLAY_ENTRIES_EQUAL={'YES' if unchanged_eq else 'NO'}")
    print(f"  INTERNAL_XML_SEMANTIC_DIFF_COUNT={internal2}")
    if idf2:
        for it in idf2:
            print(f"  INTERNAL_DIFFS path={it[0]} msg={it[1]}")
    else:
        print(f"  INTERNAL_DIFFS=none")
    print(f"  SIDECAR_PATH={out}")
    print(f"  SIDECAR_SHA={side_sha}")
    print(f"  SIDECAR_RESOURCE_COUNT={s_count}")
    print(f"  SIDECAR_TGI_COUNT={1 if tgi_equal else 0}")
    print(f"  SIDECAR_TGI={src_tgi_str}")
    print(f"  SOURCE_WW_XML_TGI_EQUAL_SIDECAR={'YES' if tgi_equal else 'NO'}")
    print(f"  SOURCE_DBPF_VERSION={src_major}.{src_minor}")
    print(f"  SIDECAR_DBPF_VERSION={s_major}.{s_minor}")
    print(f"  DBPF_VERSION_EQUAL={'YES' if dbpf_ver_equal else 'NO'}")
    print(f"  WW_XML_OFFSET_HIGH_BIT_EQUAL={'YES' if off_hi_eq else 'NO'} (src={src_off_hi} side={s_off_hi})")
    print(f"  WW_XML_SIZE_HIGH_BIT_EQUAL={'YES' if sz_hi_eq else 'NO'} (src={src_sz_hi} side={s_sz_hi})")
    print(f"  WW_XML_FIELD8_EQUAL={'YES' if f8_eq else 'NO'} (src={src_f8} side={s_f8})")
    print(f"  WW_XML_SIDECAR_DECOMPRESSED_SIZE={s_decomp}")
    print(f"  WW_XML_SIDECAR_FIELD7={s_f7}")
    print(f"  WW_XML_SIDECAR_FIELD7_MATCHES_ACTUAL={'YES' if f7_matches else 'NO'}")
    print(f"  WW_XML_SIDECAR_STORED_SIZE={s_stored} (expected {new_stored_len})")
    print(f"  WW_XML_SIDECAR_STORED_SIZE_MATCHES_ACTUAL={'YES' if stored_ok else 'NO'}")
    print(f"  ALL_RESOURCE_RANGES_VALID={'YES' if ranges_ok else 'NO'}")
    print(f"  PARSER_VALID={'YES' if parser_ok else 'NO'}")
    print(f"  SOURCE_FILE_BYTES_UNCHANGED={'YES' if src_file_unchanged else 'NO'}")
    print(f"  SOURCE_SHA_VERIFIED={'YES' if source_sha_verified else 'NO'}")
    print(f"  {pass_key}={'YES' if all_gates else 'NO'}")
    if not all_gates:
        if s_count != 1:
            print(f"    GATE_FAIL: SIDECAR_RESOURCE_COUNT={s_count}")
        if not tgi_equal:
            print(f"    GATE_FAIL: SOURCE_WW_XML_TGI_EQUAL_SIDECAR ({s_tgi})")
        if dbpf_ver_equal is False:
            print(f"    GATE_FAIL: DBPF_VERSION ({src_major}.{src_minor}->{s_major}.{s_minor})")
        if not off_hi_eq:
            print(f"    GATE_FAIL: OFFSET_HIGH_BIT ({src_off_hi}->{s_off_hi})")
        if not sz_hi_eq:
            print(f"    GATE_FAIL: SIZE_HIGH_BIT ({src_sz_hi}->{s_sz_hi})")
        if not f8_eq:
            print(f"    GATE_FAIL: FIELD8 ({src_f8}->{s_f8})")
        if not f7_matches:
            print(f"    GATE_FAIL: FIELD7_MATCHES_ACTUAL ({s_f7} vs {s_decomp})")
        if not stored_ok:
            print(f"    GATE_FAIL: STORED_SIZE ({s_stored} vs {new_stored_len})")
        if not ranges_ok:
            print("    GATE_FAIL: ALL_RESOURCE_RANGES_VALID")
        if not parser_ok:
            print("    GATE_FAIL: PARSER_VALID")
        if ic2 != 0:
            print(f"    GATE_FAIL: INTERNAL_XML_SEMANTIC_DIFF_COUNT != 0 ({internal2})")
        if ds2 != 3:
            print(f"    GATE_FAIL: DISPLAY_SEMANTIC_DIFF_COUNT != 3 ({ds2})")
        if unchanged_count != n_entries - len(targets) or not unchanged_eq:
            print(f"    GATE_FAIL: UNCHANGED_DISPLAY ({unchanged_count}, equal={unchanged_eq})")
        if not c_match:
            print("    GATE_FAIL: C1/C2/C3 display 对应检验失败")
        if not source_sha_verified:
            print("    GATE_FAIL: SOURCE_SHA_VERIFIED")
        if not src_file_unchanged:
            print("    GATE_FAIL: SOURCE_FILE_BYTES_UNCHANGED")
    # spec 要求的简写键 (与上述详细键同一来源, 保证精确一致)
    print(f"  TARGET_ENTRY_COUNT={len(targets)}")
    print(f"  TARGET_ORDINAL={','.join(str(o) for o,_t,_p in targets)}")
    for (ordinal0, _tag, prefix) in targets:
        print(f"  OLD_DISPLAY\[{ordinal0}\]= {sv_src[ordinal0]}")
        print(f"  NEW_DISPLAY\[{ordinal0}\]= {sv_new2[ordinal0]}")
    print(f"  OFFSET_HIGH_BIT_EQUAL={'YES' if off_hi_eq else 'NO'}")
    print(f"  SIZE_HIGH_BIT_EQUAL={'YES' if sz_hi_eq else 'NO'}")
    print(f"  FIELD8_EQUAL={'YES' if f8_eq else 'NO'}")
    print(f"  FIELD7_MATCHES_ACTUAL={'YES' if f7_matches else 'NO'}")
    print(f"  STORED_SIZE_MATCHES_ACTUAL={'YES' if stored_ok else 'NO'}")
    print("ZERO_WRITE_TO_MODS=YES")
    return 0 if all_gates else 3


def _check_ranges(path: Path, sidx):
    if sidx is None:
        return False
    size = path.stat().st_size
    _major, _minor, _hc, metas = wb.read_entry_meta_raw(path)
    for m in metas:
        boff = m["offset_raw"] & 0x7FFFFFFF
        bsz = m["size_raw"] & 0x7FFFFFFF
        if not (0 <= boff and boff + bsz <= size):
            return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="真实 Nevely42 .package 路径")
    ap.add_argument("--out-dir", default="", help="输出目录 (默认: easy=output/ww_animation_testc_easy_nevely42, 原版=output)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--easy", action="store_true", help="EASY 真人验证版 (ordinal 35/36/82)")
    ap.add_argument("--expected-sha", default=SH_EXPECTED,
                    help="期望的 source SHA256 (默认=真机 Nevely42; 白盒可传 fixture SHA)")
    a = ap.parse_args()
    src = Path(a.source)
    if not src.is_file():
        print("ERROR: source 不存在", file=sys.stderr)
        return 2
    out_dir = (Path(EASY_OUT_DIR) if (a.easy and not a.out_dir) else
               Path(a.out_dir) if a.out_dir else Path("output"))
    return run(src, out_dir, a.force, a.expected_sha, variant=a.easy)


if __name__ == "__main__":
    sys.exit(main())
