#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pose_coverage.py — 659 CONFIRMED_POSE 只读 coverage 扫描 + 代表性 10-cohort 选择
================================================================================

本脚本对【真实 package】做覆盖扫描。真实的 659 包位于 Windows 生产机, 因此本脚本
在 Windows 上直接运行 (python scripts\\pose_coverage.py), 对真实包只读解析:
  * 不生成任何 sidecar
  * 不写 Mods
  * 不改动任何 .package (只读)
  * 不触碰 Animation / 不批 10/50/659 / 不处理缺 CHS 创建规则
  * 不改变 frozen 9061 pose action translation layer

输入 (优先级):
  1) --list <file>    明文 package 路径列表 (一行一个), 或
  2) 默认读取 D:/projects/sims4_trans/output/pose_verification.csv
     过滤 verification_status == "POSE_VERIFIED" (即 659 包清单)

输出 (写在工作目录 output/ 下, 若不存在则创建):
  coverage.csv          每包一行, 全字段
  coverage_report.md    汇总 + 各 status 数量 + cohort 名单与理由
  cohort_selection.csv  10-cohort 选择明细 (why / TGI / entries / 分类计数 ...)

状态分类 (status):
  ELIGIBLE_EXISTING_CHS   存在 0x01 CHS 目标 STBL 且 TGI 唯一可写
  SKIP_NO_CHS             缺 0x01 CHS (不自行创建)
  SKIP_AMBIGUOUS_TGI      CHS 目标 STBL >1 或多 target family, 无法唯一确定
  SKIP_MAPPING_UNCERTAIN  无结构证据 / pack 级字段引不到 STBL
  ERROR                   DBPF 解析失败等异常

铁律:
  * 不根据 STBL 文本“看起来像英文”判 TRANSLATE; 只认 XML 结构引用。
  * player-visible model:
      PACK_TITLE        <- display_name / pack_title / title (显示类)
      PACK_DESCRIPTION  <- description (非0且结构引用)
      POSE_DISPLAY_NAME <- pose_display_name
      KEEP              <- creator_name / creator / author 等
      非UI/默认不翻:     pose_name / sort_name / internal IDs
"""
import sys, os, zlib, csv, struct
from pathlib import Path
from collections import Counter, defaultdict
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse
from backend import get_backend
from resource_types import RESOURCE_TYPES

STBL_TID = 0x220557DA
MAGIC = b"STBL"
LOCALE_CHS = 0x01
DEFAULT_VERI = r"D:/projects/sims4_trans/output/pose_verification.csv"

# 玩家可见字段模型 (语义保守, 只认名字明确的)
DISPLAY_HINTS = ("display_name", "display", "pack_title", "title", "modal_name",
                 "ui_name", "tooltip", "label", "description")
AUTHOR_HINTS = ("author", "creator", "creator_name", "by_line", "pose_name",
                "internal_name", "clip_name", "animation_name", "raw_display",
                "file_name", "package_name", "id", "unique_id", "key", "version",
                "category", "tags", "tag", "sort_name")
# 明确记为用户可见字段 (用于 ref_count 精确计数)
PV_PACK_TITLE = ("display_name", "pack_title", "title", "modal_name", "ui_name", "label")
PV_PACK_DESC = ("description",)
PV_POSE_DISPLAY = ("pose_display_name",)

LONG_STRING_LEN = 200  # 长字符串阈值 (UTF-16 units 约为 bytes/2; 用字符数判)


# ---------------------------------------------------------------- 工具
def _is_xml_candidate(tid: int) -> bool:
    return (RESOURCE_TYPES.is_snippet(tid)
            or RESOURCE_TYPES.is_tuning_xml(tid)
            or RESOURCE_TYPES.is_known_safely(tid, "WW_ANIM_XML"))


def _decompress(data: bytes) -> bytes:
    if data[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        try:
            return zlib.decompress(data)
        except Exception:
            return data
    return data


def parse_stbl(data: bytes):
    """解析 STBL v5, 返回 (version, is_compressed, [(keyHash, flags, text)])。失败返回 None。"""
    if not data:
        return None
    data = _decompress(data)
    if data[:4] != MAGIC:
        return None
    try:
        off = 4
        version = struct.unpack_from("<H", data, off)[0]; off += 2
        comp = data[off]; off += 1
        num = struct.unpack_from("<Q", data, off)[0]; off += 8
        reserved = data[off:off + 2]; off += 2
        _sl = struct.unpack_from("<I", data, off)[0]; off += 4
        if version != 5:
            return None
        out = []
        for _ in range(num):
            if off + 8 > len(data):
                break
            kh = struct.unpack_from("<I", data, off)[0]; off += 4
            fl = data[off]; off += 1
            ln = struct.unpack_from("<H", data, off)[0]; off += 2
            if off + ln > len(data):
                break
            txt = data[off:off + ln].decode("utf-8", errors="replace")
            off += ln
            out.append((kh, fl, txt))
        return (version, comp, out)
    except Exception:
        return None


def read_xml_payloads(backend, entries):
    """返回 [(instance_id, root_et, raw)] —— 真能 ET.parse 的才算。"""
    out = []
    for e in entries:
        if not _is_xml_candidate(e.type_id):
            continue
        try:
            data = backend.read_small_resource(e, max_bytes=2 * 1024 * 1024)
        except Exception:
            continue
        if not data:
            continue
        data = _decompress(data)
        for enc in ("utf-8", "utf-16-le"):
            try:
                raw = data.decode(enc)
                root = ET.fromstring(raw)
            except Exception:
                continue
            out.append((getattr(e, "instance_id", 0), root, raw))
            break
    return out


def _classify_field(fname):
    fl = (fname or "").lower()
    if any(h in fl for h in DISPLAY_HINTS):
        return "DISPLAY"
    if any(h in fl for h in AUTHOR_HINTS):
        return "AUTHORISH"
    return "OTHER"


def is_hash_like(s):
    s = (s or "").strip()
    if not s:
        return False
    try:
        v = int(s, 16) if s.lower().startswith("0x") else int(s, 0)
        return True
    except ValueError:
        return False


def parse_display_hash(s):
    """规范化解析 XML 引用值为 32-bit key hash。

    规则(2026-08-15): 先规范化解析成整数, 再判断有效性。
      - 无法解析 -> None (非 0x 或非法)
      - 解析为 0 / 0x0 / 0x00000000 (canonical zero sentinel) -> None, 表示“无引用”: 不得计入任何 ref count, 也不得成为 unresolved。
    不依赖“字符串是否以0x开头”来判断有效性。
    """
    s = (s or "").strip()
    if not s:
        return None
    try:
        v = int(s, 16) if s.lower().startswith("0x") else int(s, 0)
    except ValueError:
        return None
    # canonical zero sentinel = 无引用
    if v == 0:
        return None
    return v


def _is_pose_container(nl):
    """该节点名是否为 pose 容器 (pose_list / pose / pose_entry)。pose_display_name 只能出现在这里。"""
    nl = nl.replace("_", "")
    return nl in ("poselist", "pose", "poseentry")


def _walk_ctx(root):
    """带结构位置的遍历: yield (el, pack_level, in_pose_container)。

    - pack_level=True  : 该节点位于 PosePackInstance 树内、且不在任何 pose_list/pose entry 下
                         (PACK_TITLE / PACK_DESCRIPTION 的有效位置)
    - in_pose_container=True: 该节点位于 pose_list/pose/pose_entry 子树内
                         (POSE_DISPLAY_NAME 的有效位置)
    """
    # 父->子 递归, 边遍历边跟踪祖先是否为 pose 容器
    stack = [(root, None)]  # (node, parent_name_lower)
    # 用显式栈模拟, 维护当前是否处在 pose 容器内
    def _rec(node, in_pose):
        nl = (node.attrib.get("n") or "").lower()
        n_in_pose = in_pose or _is_pose_container(nl)
        pack_level = (not in_pose) and (not n_in_pose)
        yield (node, pack_level, n_in_pose)
        for child in node:
            yield from _rec(child, n_in_pose)
    yield from _rec(root, False)


def is_pose_pack_root(root):
    """PosePackInstance 判定: 根元素名或其 c= 属性含 pose_pack / posepackinstance, 或树内含 pose_list。"""
    tag = (root.tag or "").lower()
    cattr = ((root.attrib.get("c") or root.attrib.get("class") or "").lower()
             .replace("_", "").replace(" ", ""))
    tagn = tag.replace("_", "").replace(" ", "")
    if "pose_pack" in tag or "posepack" in tag or "posepack" in cattr:
        return True
    # 兜底: 树内存在 pose_list 节点
    for el in root.iter():
        n = (el.attrib.get("n") or "").lower()
        if n == "pose_list":
            return True
    return False


# ---------------------------------------------------------------- 单包扫描
def scan_package(path: str) -> dict:
    row = {
        "package_path": path,
        "file_size": 0,
        "PosePackInstance_count": 0,
        "STBL_count_total": 0,
        "CHS_0x01_exists": 0,
        "CHS_target_STBL_count": 0,
        "CHS_target_TGI(s)": "",
        "CHS_entry_count": 0,
        "pack_title_ref_count": 0,
        "pack_description_ref_count": 0,
        "pose_display_name_ref_count": 0,
        "exact_structural_translate_count": 0,
        "keep_count": 0,
        "unmapped_uncertain_count": 0,
        "invariant_ok": 0,
        "player_visible_structural_ref_count": 0,
        "unique_player_visible_ref_count": 0,
        "resolved_player_visible_ref_count": 0,
        "unresolved_player_visible_ref_count": 0,
        "translate_set_complete": 0,
        "resolved_pv_key_set_size": 0,
        "translate_key_set_size": 0,
        "STBL_version": "",
        "compression_state": "",
        "non_ascii_source_present": 0,
        "long_string_present": 0,
        "repeated_source_text_present": 0,
        "multiple_target_STBL_families": 0,
        "status": "ERROR",
        "reason": "",
    }

    idx, err = safe_parse(path)
    if err or idx is None:
        row["reason"] = f"DBPF 解析失败: {err}"
        return row
    try:
        row["file_size"] = os.path.getsize(path)
    except Exception:
        pass

    backend = get_backend("readonly").open(path)

    # ---- STBL 汇总 ----
    stbl_entries = [e for e in idx.entries if e.type_id == STBL_TID]
    row["STBL_count_total"] = len(stbl_entries)
    stbl_parsed = {}   # inst_id -> (version, comp, [(kh,fl,txt)])
    versions = set()
    compstates = set()
    stbl_inst = []     # (inst_id, version, comp, n_entries)
    for e in stbl_entries:
        try:
            data = backend.read_small_resource(e, max_bytes=2 * 1024 * 1024)
        except Exception:
            data = None
        pr = parse_stbl(data) if data else None
        if pr:
            ver, comp, kvs = pr
            stbl_parsed[e.instance_id] = (ver, comp, kvs)
            stbl_inst.append((e.instance_id, ver, comp, len(kvs)))
            versions.add(ver); compstates.add(comp)
    row["STBL_version"] = ",".join(str(v) for v in sorted(versions))
    row["compression_state"] = "COMPRESSED" if (1 in compstates or 0x5A42 in compstates) else ("UNCOMPRESSED" if compstates else "NONE")

    # 压缩态判别: comp 字段非 0 或资源 offset 高位压缩标记
    compressed_pkg = any(e.is_compressed for e in stbl_entries)
    if compressed_pkg:
        row["compression_state"] = "COMPRESSED"

    # ---- CHS 目标: locale 顶层字节 >>56 == 0x01 ----
    chs = [(i, v, c, kvs) for i, (v, c, kvs) in stbl_parsed.items() if ((i >> 56) & 0xFF) == LOCALE_CHS]
    row["CHS_0x01_exists"] = 1 if chs else 0
    row["CHS_target_STBL_count"] = len(chs)
    if chs:
        row["CHS_target_TGI(s)"] = ";".join(
            f"0x220557DA/0x80000000/0x{i:016X}" for i, *_ in chs)
        row["CHS_entry_count"] = sum(len(kvs) for *_ , kvs in chs)
        # 多 target family: 高 32 位(<locale 字节>)不同 -> 多 family
        fams = {(i >> 32) & 0xFFFFFFFF for i, *_ in chs}
        row["multiple_target_STBL_families"] = 1 if len(fams) > 1 else 0

    # ---- XML 结构引用 (PosePackInstance 及其它 tuning) ----
    # struct_ref: 全包 XML 引用 hash -> [(field, cls, xml_inst_id)], 该 hash 在所有 STBL
    # (任意 locale) 中出现与否, 仅作“包内该 hash 是否有结构引用”的证据;
    # 最终能否 writable 取决于该 hash 是否存在于【exact CHS target STBL】并落在它上面。
    xmls = read_xml_payloads(backend, idx.entries)
    posexmls = [x for x in xmls if is_pose_pack_root(x[1])]
    row["PosePackInstance_count"] = len(posexmls)

    struct_ref = {}    # kh -> [(field, cls, xml_inst_id)]
    translate_ref_keys = set()   # 播放器可见 TRANSLATE 字段引用的 key hash 集合 (单一真源)
    keep_ref_keys = set()        # AUTHORISH (creator/author) 引用的 key hash 集合
    pv_refs = []       # player-visible refs: (field, hash|None, cls|pvc, xml_inst_id)
    ref_pv_packtitle = 0
    ref_pv_packdesc = 0
    ref_pv_posedisplay = 0
    for xinst_id, root, _raw in posexmls:
        # 带结构位置遍历: 仅处理 PosePackInstance 树 (非 PosePack 的 display_name/description
        # 一律不参与), 并按 pack_level / in_pose_container 区分三类的有效位置。
        for el, pack_level, in_pose in _walk_ctx(root):
            n = el.attrib.get("n")
            if not n:
                continue
            has_children = any(True for _ in el)
            if has_children and el.text is None:
                continue
            val = (el.text or "").strip()
            if not val:
                continue
            cls = _classify_field(n)
            h = parse_display_hash(val)   # None 代表无效或 zero sentinel (0/0x0/0x00000000)
            nl = n.lower()
            # 结构位置门控 (2026-08-15 终审):
            #   PACK_TITLE        = PosePackInstance-level display_name        (pack_level, 非 pose 容器内)
            #   PACK_DESCRIPTION  = PosePackInstance-level description (非0hash) (pack_level)
            #   POSE_DISPLAY_NAME = pose_list/pose entry 内的 pose_display_name   (in_pose)
            pvc = None
            if nl == "display_name" and pack_level:
                pvc = "TRANSLATE"
            elif nl == "description" and pack_level:
                pvc = "TRANSLATE"
            elif nl == "pose_display_name" and in_pose:
                pvc = "TRANSLATE"
            # 精确字段名计数 (zero sentinel h is None 不计)
            if h is not None:
                if nl in PV_PACK_TITLE and pack_level: ref_pv_packtitle += 1
                if nl in PV_PACK_DESC and pack_level: ref_pv_packdesc += 1
                if nl in PV_POSE_DISPLAY and in_pose: ref_pv_posedisplay += 1
            if h is not None:
                struct_ref.setdefault(h, []).append((n, cls, xinst_id))
            # player-visible ref: 位置门控后, 只记“确实在该规范位置且非0 hash”的字段
            if pvc is not None and h is not None:
                pv_refs.append((n, h, pvc, xinst_id))
            # 结构引用证据 (非0 hash): translate keys 仅取位置门控后的 pvc;
            # 其余 (authorish 且 pack_level 或任意) 归 keep。
            if h is not None:
                if pvc is not None:
                    translate_ref_keys.add(h)
                elif cls == "AUTHORISH":
                    keep_ref_keys.add(h)
    row["pack_title_ref_count"] = ref_pv_packtitle
    row["pack_description_ref_count"] = ref_pv_packdesc
    row["pose_display_name_ref_count"] = ref_pv_posedisplay

    # ---- 逐 key 判定: 严格限定到 exact CHS target STBL ----
    # 只有当包内 CHS 目标 STBL 唯一 (CHS_target_STBL_count == 1) 时, 才在它的 key 全集上
    # 做三分法。多 target / 无 target 时计数一律置 0, 避免把其他 STBL/locale/orphan
    # 的 key 混进 target 统计 (修复: [Kritical]BrainwashingMachineAtropos1c 64 vs 2)。
    translate = keep = unmapped = 0
    target_key_set = set()   # exact CHS target STBL 的 key hash 全集
    if row["CHS_target_STBL_count"] == 1:
        chs_inst, chs_ver, chs_comp, chs_kvs = chs[0]
        target_keys = {kh: (fl, txt) for kh, fl, txt in chs_kvs}  # 仅该 exact STBL 的 key
        target_key_set = set(target_keys.keys())
        # TRANSLATE keys: 只取【结构位置门控后的 exact 3 字段】引用的 key (与 pv_refs 同一套
        # 定义+位置: PosePackInstance-level display_name/description + pose_list 内 pose_display_name),
        # 与 pv_refs 用同一套定义 —— 绝不用宽泛 DISPLAY substring (修: pose_description 等被吞)。
        tset = translate_ref_keys & target_key_set
        kset = keep_ref_keys & target_key_set
        translate = len(tset)
        keep = len(kset)
        unmapped = len(target_key_set - tset - kset)  # target 内无任何结构引用的 orphan
    row["exact_structural_translate_count"] = translate
    row["keep_count"] = keep
    row["unmapped_uncertain_count"] = unmapped

    # ---- 硬 invariant: target STBL 三分法全量覆盖 ----
    # TRANSLATE + KEEP + UNMAPPED 必须 == CHS_entry_count (exact target STBL key 总数)。
    # 不满足 -> 计数串了 scope, 绝不判 ELIGIBLE。
    if row["CHS_target_STBL_count"] == 1:
        inv = translate + keep + unmapped
        row["invariant_ok"] = 1 if inv == row["CHS_entry_count"] else 0
    else:
        row["invariant_ok"] = 0

    # ---- set-level invariant: TRANSLATE_KEY_SET == RESOLVED_PLAYER_VISIBLE_KEY_SET ----
    # 收集 pv_refs 与生成 TRANSLATE keys 必须同源 (同一字段定义+结构位置); 重复 XML ref 去重后比 key set.
    # 即: 所有 resolve 成功的 player-visible key 集合 == 实际标成 TRANSLATE 的 key 集合。
    # KEEP (creator) 单独检查, 不混入本式。
    if row["CHS_target_STBL_count"] == 1:
        resolved_pv_keys = {h for _, h, _, _ in pv_refs if h is not None and h in target_key_set}
        translate_key_set = translate_ref_keys & target_key_set
        if resolved_pv_keys == translate_key_set:
            row["translate_set_complete"] = 1
        else:
            row["translate_set_complete"] = 0
        row["resolved_pv_key_set_size"] = len(resolved_pv_keys)
        row["translate_key_set_size"] = len(translate_key_set)
    else:
        row["translate_set_complete"] = 0
        row["resolved_pv_key_set_size"] = 0
        row["translate_key_set_size"] = 0

    # ---- structural-ref resolution completeness (反向: XML ref -> target STBL) ----
    # 只统计 player-visible refs (PACK_TITLE / PACK_DESCRIPTION / POSE_DISPLAY_NAME)。
    # 每个 ref 检查: hash 有效 / 唯一解析 / 落到当前 exact CHS target TGI / 该 key 存在。
    # 方向是 XML structural ref -> STBL; 普通 orphan / 旧 STBL key 不参与, 不计 unresolved。
    pv_total = len(pv_refs)
    pv_unique = len({h for _, h, _, _ in pv_refs if h is not None})
    pv_resolved = 0
    pv_unresolved = 0
    unique_hashes = set()
    if pv_refs:
        if row["CHS_target_STBL_count"] == 1:
            chs_inst2, _v, _c, chs_kvs2 = chs[0]
            tkeys = {kh for kh, _, _ in chs_kvs2}
        else:
            chs_inst2, tkeys = None, set()
        for fname, h, cls, xid in pv_refs:
            if h is None:
                pv_unresolved += 1          # hash 无效 (不是 0x... 或解析失败)
                continue
            if h in unique_hashes:
                continue                    # 同一 hash 的重复 ref 只算一次
            unique_hashes.add(h)
            if chs_inst2 is None:
                pv_unresolved += 1          # 无唯一 CHS target, 无法落到 exact TGI
                continue
            # 唯一解析: 该 hash 的 XML ref 不能同时伴随歧义 (这里按 hash 唯一计一次; 重复已跳过)
            # 落到 exact CHS target TGI 且 key 存在于该 STBL
            if h in tkeys:
                pv_resolved += 1
            else:
                pv_unresolved += 1          # 该 ref hash 在 target CHS STBL 中不存在
    row["player_visible_structural_ref_count"] = pv_total
    row["unique_player_visible_ref_count"] = pv_unique
    row["resolved_player_visible_ref_count"] = pv_resolved
    row["unresolved_player_visible_ref_count"] = pv_unresolved

    # ---- 文本特征 (仅 target CHS STBL 文本, 与三分法同 scope) ----
    non_ascii = long_str = repeated = False
    if row["CHS_target_STBL_count"] == 1:
        alltexts = [txt for _, _, txt in chs[0][3]]
        non_ascii = any(any(ord(ch) > 127 for ch in t) for t in alltexts)
        long_str = any(len(t) >= LONG_STRING_LEN for t in alltexts)
        cnt = Counter(t for t in alltexts if t.strip())
        repeated = any(n > 1 for n in cnt.values())
    row["non_ascii_source_present"] = 1 if non_ascii else 0
    row["long_string_present"] = 1 if long_str else 0
    row["repeated_source_text_present"] = 1 if repeated else 0

    backend.close()

    # ---- 状态分类 ----
    row = _classify(row)
    return row


def _classify(row: dict) -> dict:
    if row["CHS_0x01_exists"] == 0:
        row["status"] = "SKIP_NO_CHS"
        row["reason"] = "缺 0x01 CHS (不自行创建, 不推导新 Instance)"
        return row
    if row["CHS_target_STBL_count"] != 1:
        row["status"] = "SKIP_AMBIGUOUS_TGI"
        row["reason"] = f"CHS 目标 STBL 数 {row['CHS_target_STBL_count']} != 1 (多 target / 多 family)"
        return row
    # 硬 invariant: target STBL 三分法必须全量覆盖。
    # TRANSLATE + KEEP + UNMAPPED == CHS_entry_count (即 exact CHS target STBL key 总数)。
    # 不满足 => 计数串了 scope (把其他 STBL/locale/orphan key 混进 target), 绝不判 ELIGIBLE。
    _sum = (row["exact_structural_translate_count"]
            + row["keep_count"] + row["unmapped_uncertain_count"])
    if _sum != row["CHS_entry_count"]:
        row["status"] = "ERROR_COVERAGE_INVARIANT"
        row["reason"] = (f"target 三分法不满足 invariant: "
                          f"TRANSLATE+{row['exact_structural_translate_count']} "
                          f"+KEEP+{row['keep_count']} +UNMAPPED+{row['unmapped_uncertain_count']} "
                          f"={_sum} != CHS_entry_count={row['CHS_entry_count']} "
                          f"(scope 串包: 其他 STBL/locale/orphan 混入)")
        return row
    if row["unresolved_player_visible_ref_count"] != 0:
        row["status"] = "SKIP_MAPPING_UNCERTAIN"
        # structural-ref resolution completeness gate (XML ref -> target STBL):
        # 只要还有 player-visible refs 未 exact resolve 到当前 CHS target STBL
        # (hash 无效 / 无唯一 target / key 不存在于该 STBL), 就不能判 ELIGIBLE。
        # 普通 orphan / 旧 STBL key 不是 unresolved (方向是 XML -> STBL)。
        row["reason"] = (f"player-visible 结构引用未全部 exact resolve: "
                          f"unresolved={row['unresolved_player_visible_ref_count']} / "
                          f"resolved={row['resolved_player_visible_ref_count']} "
                          f"(total={row['player_visible_structural_ref_count']})")
        return row
    if row["translate_set_complete"] == 0:
        row["status"] = "SKIP_MAPPING_UNCERTAIN"
        # set-level invariant: 收集 pv_refs 与生成 TRANSLATE keys 必须同源 (同一字段定义)。
        # TRANSLATE_KEY_SET != RESOLVED_PLAYER_VISIBLE_KEY_SET => 分类逻辑与收集逻辑脱节,
        # 或存在被旧 DISPLAY 分类吞掉的额外字段 (如 pose_description), 不允许 ELIGIBLE。
        row["reason"] = (f"TRANSLATE key set != resolved player-visible key set: "
                          f"TRANSLATE_SET={row['translate_key_set_size']} vs "
                          f"RESOLVED_PV_SET={row['resolved_pv_key_set_size']} "
                          f"(分类与 pv_refs 不同源或有额外字段被吞)")
        return row
    if row["exact_structural_translate_count"] == 0 and row["keep_count"] == 0:
        row["status"] = "SKIP_MAPPING_UNCERTAIN"
        # 语义确认 (2026-08-15): unmapped_uncertain_count>0 本身【不】触发本状态。
        # 本状态仅当: 结构上【没有任何】 player-visible 引用能解析到 CHS target STBL key
        # (translate==0 且 keep==0, 即玩家可见字段引用完全无法 join) 时触发。
        # 普通 orphan/旧 STBL/无 XML 引用 key 只记 unmapped_uncertain_count, 不阻塞写入
        # 例: Tibo131 translate=36/keep=1/unmapped=17 -> 仍 ELIGIBLE (17 unmapped 全 untouched)
        row["reason"] = ("无任何已解析到 target CHS STBL 的 player-visible 结构引用 "
                          "(translate=0 且 keep=0); unmapped/orphan key 不阻塞写入")
        return row
    row["status"] = "ELIGIBLE_EXISTING_CHS"
    row["reason"] = ("存在唯一 0x01 CHS 目标, 三分法全量覆盖 + invariant 满足 + "
                      "player-visible refs 全部 exact resolve")
    return row


# ---------------------------------------------------------------- 输入清单
def load_packages(list_path=None) -> list:
    if list_path:
        with open(list_path, encoding="utf-8-sig") as f:
            return [ln.strip() for ln in f if ln.strip()]
    if not os.path.exists(DEFAULT_VERI):
        print(f"[ERROR] 默认清单不存在: {DEFAULT_VERI}")
        print("  请用 --list <文件> 提供 package 路径列表 (一行一个)")
        sys.exit(2)
    paths = []
    with open(DEFAULT_VERI, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("verification_status") == "POSE_VERIFIED":
                p = (r.get("package_path") or "").strip()
                if p:
                    paths.append(p)
    return paths


# ---------------------------------------------------------------- cohort
def pick_cohort(rows):
    """按类别覆盖程序化挑选代表性 10 包 (确定性 tie-break, 非随机/非人工名字)。

    返回: [(slot, why, row), ...]   slot 为 1..N; 某类缺则标 NOT_PRESENT_IN_CORPUS。
    """
    elig = [r for r in rows if r["status"] == "ELIGIBLE_EXISTING_CHS"]
    eligible = set(r["package_path"] for r in elig)

    def pick(fn, eligible_set):
        cand = [r for r in rows if r["package_path"] in eligible_set and fn(r)]
        if not cand:
            return None
        # 确定性: 文件路径字典序最小
        return min(cand, key=lambda r: r["package_path"])

    picks = []          # (slot, why, row|None)
    used = set()

    def place(slot, why, r):
        picks.append((slot, why, r))
        if r is not None:
            used.add(r["package_path"])

    # 1) 最小 CHS STBL / entry 最少
    if elig:
        r = min(elig, key=lambda r: (r["CHS_entry_count"], r["package_path"]))
        place(1, "最小 CHS STBL / entry 最少", r)
    else:
        place(1, "最小 CHS STBL", None)

    # 2) 最大 CHS STBL / entry 很多
    if elig:
        r = max(elig, key=lambda r: (r["CHS_entry_count"], r["package_path"]))
        place(2, "最大 CHS STBL / entry 很多", r)
    else:
        place(2, "最大 CHS STBL", None)

    # 3) 普通中等规模 (中位 entry 数, 未被选)
    if elig:
        mid = sorted(r["CHS_entry_count"] for r in elig)
        med = mid[len(mid) // 2]
        unused3 = [r for r in elig if r["package_path"] not in used]
        # 防御: 若全部 eligible 已被前几槽占用 (真实 659 不会, 但退化成小样本时防 min() 空崩)
        if not unused3:
            unused3 = [min(elig, key=lambda r: r["package_path"])]
        r = min(unused3,
                key=lambda r: (abs(r["CHS_entry_count"] - med), r["package_path"]))
        place(3, "普通中等规模 (接近 median entry)", r)
    else:
        place(3, "普通中等规模", None)

    # 4) 多个 PosePackInstance
    r = pick(lambda r: r["PosePackInstance_count"] > 1, eligible - used)
    if r: place(4, "多个 PosePackInstance", r)
    else: place(4, "多个 PosePackInstance", None)

    # 5) 多个 STBL family / 多 target STBL
    r = pick(lambda r: r["multiple_target_STBL_families"] or r["CHS_target_STBL_count"] > 1,
             eligible - used)
    if r: place(5, "多个 STBL family / 多 target STBL", r)
    else: place(5, "多个 STBL family", None)

    # 6) pack title + description 都存在
    r = pick(lambda r: r["pack_title_ref_count"] > 0 and r["pack_description_ref_count"] > 0,
             eligible - used)
    if r: place(6, "pack title + description 都存在", r)
    else: place(6, "pack title + description", None)

    # 7) 只有 pose_display_name, 无 description
    r = pick(lambda r: r["pose_display_name_ref_count"] > 0 and r["pack_description_ref_count"] == 0,
             eligible - used)
    if r: place(7, "只有 pose_display_name, 无 description", r)
    else: place(7, "只有 pose_display_name", None)

    # 8) source 含非 ASCII / 特殊字符
    r = pick(lambda r: r["non_ascii_source_present"], eligible - used)
    if r: place(8, "source 含非 ASCII / 特殊字符", r)
    else: place(8, "source 非 ASCII", None)

    # 9) 长字符串
    r = pick(lambda r: r["long_string_present"], eligible - used)
    if r: place(9, "长字符串", r)
    else: place(9, "长字符串", None)

    # 10) repeated source / protected token 较多
    r = pick(lambda r: r["repeated_source_text_present"], eligible - used)
    if r: place(10, "repeated source / protected token", r)
    else: place(10, "repeated source", None)

    # 附加: compressed STBL/package (若 corpus 存在则优先补 1)
    compressed_exist = any("COMPRESSED" in r["compression_state"] for r in rows)
    if compressed_exist:
        r = pick(lambda r: "COMPRESSED" in r["compression_state"], eligible - used)
        if r:
            picks.append((11, "compressed STBL/package (corpus 存在, 优先纳入)", r))

    return picks


# ---------------------------------------------------------------- main
def main():
    list_path = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--list":
            list_path = args[i + 1]; i += 1
        i += 1
    if list_path and not os.path.exists(list_path):
        print(f"[ERROR] --list 文件不存在: {list_path}")
        return 2

    paths = load_packages(list_path)
    if not paths:
        print("[ERROR] 包清单为空")
        return 2
    print(f"待扫描包数: {len(paths)}")

    rows = []
    for idx, p in enumerate(paths, 1):
        try:
            r = scan_package(p)
        except Exception as ex:
            r = {"package_path": p, "status": "ERROR", "reason": f"扫描异常: {ex}"}
            for c in _COLS:
                r.setdefault(c, 0 if c not in ("STBL_version", "compression_state",
                                               "CHS_target_TGI(s)", "reason",
                                               "package_path", "status") else "")
            r["package_path"] = p; r["status"] = "ERROR"
        rows.append(r)
        if idx % 100 == 0:
            print(f"  {idx}/{len(paths)} ...")

    out_dir = Path(os.getcwd()) / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    cov_csv = out_dir / "coverage.csv"
    cols = _COLS
    with open(cov_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    print(f"\ncoverage: {cov_csv} ({len(rows)} 行)")

    # ---- status 汇总 ----
    sc = Counter(r["status"] for r in rows)
    print("\n--- status 数量 ---")
    for s in ("ELIGIBLE_EXISTING_CHS", "SKIP_NO_CHS", "SKIP_AMBIGUOUS_TGI",
              "SKIP_MAPPING_UNCERTAIN", "ERROR", "ERROR_COVERAGE_INVARIANT"):
        print(f"  {s}: {sc.get(s, 0)}")

    # ---- cohort ----
    picks = pick_cohort(rows)
    coh_csv = out_dir / "cohort_selection.csv"
    with open(coh_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["slot", "why", "package_path", "CHS_target_TGI", "CHS_entry_count",
                    "PosePackInstance_count", "structural_TRANSLATE", "KEEP", "UNMAPPED",
                    "pack_title_ref", "pack_description_ref", "pose_display_name_ref",
                    "STBL_version", "compression_state", "status"])
        for slot, why, r in picks:
            if r is None:
                w.writerow([slot, why + " = NOT_PRESENT_IN_CORPUS", "N/A", "", "", "", "", "", "", "", "", "", "", "", ""])
            else:
                w.writerow([slot, why, r["package_path"], r["CHS_target_TGI(s)"],
                            r["CHS_entry_count"], r["PosePackInstance_count"],
                            r["exact_structural_translate_count"], r["keep_count"],
                            r["unmapped_uncertain_count"],
                            r["pack_title_ref_count"], r["pack_description_ref_count"],
                            r["pose_display_name_ref_count"],
                            r["STBL_version"], r["compression_state"], r["status"]])
    print(f"cohort: {coh_csv} ({len(picks)} 行)")

    # ---- markdown report ----
    rep = out_dir / "coverage_report.md"
    with open(rep, "w", encoding="utf-8") as f:
        f.write("# 659 CONFIRMED_POSE coverage 报告 (只读)\n\n")
        f.write(f"扫描包数: {len(rows)}\n\n## status 数量\n\n")
        for s in ("ELIGIBLE_EXISTING_CHS", "SKIP_NO_CHS", "SKIP_AMBIGUOUS_TGI",
                  "SKIP_MAPPING_UNCERTAIN", "ERROR", "ERROR_COVERAGE_INVARIANT"):
            f.write(f"- {s}: {sc.get(s, 0)}\n")
        f.write("\n## 代表性 10-cohort (程序化选择, 非随机/非人工)\n\n")
        f.write("| slot | why | package | CHS TGI | CHS entries | TRANSLATE | KEEP | UNMAPPED |\n")
        f.write("|---|---|---|---|---|---|---|---|\n")
        for slot, why, r in picks:
            if r is None:
                f.write(f"| {slot} | {why} **NOT_PRESENT_IN_CORPUS** | - | - | - | - | - | - |\n")
            else:
                f.write(f"| {slot} | {why} | {Path(r['package_path']).name} | "
                        f"{r['CHS_target_TGI(s)']} | {r['CHS_entry_count']} | "
                        f"{r['exact_structural_translate_count']} | {r['keep_count']} | "
                        f"{r['unmapped_uncertain_count']} |\n")
        # compressed 存在性记录
        any_comp = any("COMPRESSED" in r["compression_state"] for r in rows)
        f.write(f"\ncompressed STBL/package 在 659 中: "
                f"{'存在 (已优先纳入 cohort)' if any_comp else 'NOT_PRESENT_IN_CORPUS'}\n")
    print(f"report: {rep}")
    return 0


_COLS = ["package_path", "file_size", "PosePackInstance_count", "STBL_count_total",
         "CHS_0x01_exists", "CHS_target_STBL_count", "CHS_target_TGI(s)", "CHS_entry_count",
         "pack_title_ref_count", "pack_description_ref_count", "pose_display_name_ref_count",
         "exact_structural_translate_count", "keep_count", "unmapped_uncertain_count",
         "invariant_ok",
         "player_visible_structural_ref_count", "unique_player_visible_ref_count",
         "resolved_player_visible_ref_count", "unresolved_player_visible_ref_count",
         "translate_set_complete", "resolved_pv_key_set_size", "translate_key_set_size",
         "STBL_version", "compression_state",
         "non_ascii_source_present", "long_string_present", "repeated_source_text_present",
         "multiple_target_STBL_families", "status", "reason"]


if __name__ == "__main__":
    sys.exit(main())
