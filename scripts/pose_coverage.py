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
  SKIP_DUPLICATE_KEYHASH  (唯一 target) 含重复 KeyHash, 保守跳过, 不进 ELIGIBLE
  SKIP_FALSE_POSITIVE_INTERNAL_POSE  STRONG_OBJECT_FOOTPRINT 命中 (OBJD>0 AND COBJ>0 AND
                             (RSLT>0 OR FTPT>0)): 功能物品内部 pose, 非独立 Pose Pack
  SKIP_MISSING_FILE       corpus 清单中的 production source 路径不存在 (保留记录, 不泛化为 ERROR)
  ERROR                   DBPF 解析失败等异常 (文件存在但解析失败)
  ERROR_COVERAGE_INVARIANT target 三分法不满足 invariant (计量单位/scope 异常)

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

# ---- STRONG_OBJECT_FOOTPRINT gate (VERIFIED resource type IDs, lib/s4pi_src 核实) ----
# 批准于 2026-08-15 真实448 census: OBJD+COBJ+(RSLT|FTPT) 在 448 里唯一命中就=Kritical。
# 命中 -> SKIP_FALSE_POSITIVE_INTERNAL_POSE; 不使用 interaction/action/animation 单列或
# >=2/>=3 signal types / 任意 signal / 文件名 / 作者名。
OBJD_TID = 0xC0DB5AE7   # ObjectDefinition(catalog)
COBJ_TID = 0x319E4F1D   # Catalog object
RSLT_TID = 0xD3044521   # Slot
FTPT_TID = 0xD382BF57   # Footprint

# corpus inventory 路径修正 (2026-08-15 实查):
# 包文件被移动/改名后, 以 basename 匹配修正为真实 production 路径。
# 只影响 inventory 的 package_path (重新读取并分类); 不改 mapping/TGI/structural/duplicate 判定。
# 注意: 绝不把人工测试基准 (如 .S4S_golden.package) 当作 production source 替换原包。
PATH_REMAP = {
    "[KPC] Spread eagle.package":
        r"C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods\2026.8.14\[KPC] Spread eagle.package",
}

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
        "CHS_unique_key_hash_count": 0,
        "duplicate_key_hash_count": 0,
        "duplicate_extra_occurrences": 0,
        "duplicate_writable_key_count": 0,
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
        "OBJD_count": 0,
        "COBJ_count": 0,
        "RSLT_count": 0,
        "FTPT_count": 0,
        "strong_object_footprint": 0,
        "multiple_target_STBL_families": 0,
        "status": "ERROR",
        "reason": "",
    }

    idx, err = safe_parse(path)

    # corpus inventory 缺失源文件: 路径不存在 -> 保留记录, 标 SKIP_MISSING_FILE,
    # 不泛化为 ERROR。safe_parse 在 open() 时对不存在文件会返回 err='ERROR'。
    # 这里提前拦截, 避免把 missing-file 当 DBPF 解析失败。
    if not os.path.exists(path):
        row["reason"] = "SKIP_MISSING_FILE: 源文件路径不存在 (保留记录, 不指向非 production 基准)"
        row["status"] = "SKIP_MISSING_FILE"
        return row

    if err or idx is None:
        row["reason"] = f"DBPF 解析失败: {err}"
        return row
    try:
        row["file_size"] = os.path.getsize(path)
    except Exception:
        pass

    backend = get_backend("readonly").open(path)

    # ---- STRONG_OBJECT_FOOTPRINT signal 计数 (只读, VERIFIED type IDs) ----
    # 资源全集已在 idx.entries 解析; fail-closed: 若资源不可枚举则下方 ERROR。
    try:
        row["OBJD_count"] = sum(1 for e in idx.entries if e.type_id == OBJD_TID)
        row["COBJ_count"] = sum(1 for e in idx.entries if e.type_id == COBJ_TID)
        row["RSLT_count"] = sum(1 for e in idx.entries if e.type_id == RSLT_TID)
        row["FTPT_count"] = sum(1 for e in idx.entries if e.type_id == FTPT_TID)
    except Exception as ex:
        row["reason"] = f"STRONG_OBJECT_FOOTPRINT 资源枚举失败(fail-closed): {ex}"
        return row  # 保持 status=ERROR, 不得当 signal=0

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
    target_key_set = set()   # exact CHS target STBL 的 key hash 全集 (unique)
    if row["CHS_target_STBL_count"] == 1:
        chs_inst, chs_ver, chs_comp, chs_kvs = chs[0]
        # CHS_entry_count = 物理 entry 数 (含重复 KeyHash)。
        # 但 TRANSLATE/KEEP/UNMAPPED 是基于 unique key hash 集计数的,
        # 因此目标分类 invariant 必须用同一计量单位 —— CHS_unique_key_hash_count。
        phys_khs = [kh for kh, _, _ in chs_kvs]
        row["CHS_unique_key_hash_count"] = len(set(phys_khs))
        phys_cnt = Counter(phys_khs)
        dup_hashes = {kh for kh, c in phys_cnt.items() if c > 1}
        row["duplicate_key_hash_count"] = len(dup_hashes)
        row["duplicate_extra_occurrences"] = len(phys_khs) - len(set(phys_khs))
        target_keys = {kh: (fl, txt) for kh, fl, txt in chs_kvs}  # 仅该 exact STBL 的 key (unique)
        target_key_set = set(target_keys.keys())
        # duplicate_writable_key_count: 重复 KeyHash 中有多少落在 TRANSLATE/KEEP
        # (即会被 writer 写到的 key); 当前阶段不据此放宽, 只是报告。
        # 落在 UNMAPPED 的重复不额外写 (writer 只写 T/K), 但一律保守跳过 (见 _classify)。
        tset = translate_ref_keys & target_key_set
        kset = keep_ref_keys & target_key_set
        writable = tset | kset
        row["duplicate_writable_key_count"] = len(dup_hashes & writable)
        translate = len(tset)
        keep = len(kset)
        unmapped = len(target_key_set - tset - kset)  # target 内无任何结构引用的 orphan
    row["exact_structural_translate_count"] = translate
    row["keep_count"] = keep
    row["unmapped_uncertain_count"] = unmapped

    # ---- 硬 invariant: target STBL 三分法全量覆盖 (同一计量单位: unique key hash) ----
    # TRANSLATE + KEEP + UNMAPPED 必须 == CHS_unique_key_hash_count (exact target STBL 的
    # unique key 总数)。不满足 -> 计数串了 scope 或计量单位不一致, 绝不判 ELIGIBLE。
    # 注: CHS_entry_count 是物理数(含重复), 只作报告, 不用于三分法 invariant(避免被 duplicate 干扰)。
    if row["CHS_target_STBL_count"] == 1:
        inv = translate + keep + unmapped
        row["invariant_ok"] = 1 if inv == row["CHS_unique_key_hash_count"] else 0
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
    # ---- STRONG_OBJECT_FOOTPRINT signal: 始终计算并记录 (诊断), 不一定改 status ----
    # 命中定义 (VERIFIED): OBJD>0 AND COBJ>0 AND (RSLT>0 OR FTPT>0)。
    _strong = (row["OBJD_count"] > 0 and row["COBJ_count"] > 0
               and (row["RSLT_count"] > 0 or row["FTPT_count"] > 0))
    row["strong_object_footprint"] = 1 if _strong else 0

    # ---- baseline status (原 CHS/结构判定, 保留 provenance) ----
    _classify_baseline(row)

    # ---- strong gate 只作用于 baseline==ELIGIBLE ----
    # 只有当 baseline 会判 ELIGIBLE 时才升级为 SKIP_FALSE_POSITIVE_INTERNAL_POSE;
    # 若 baseline 已是 NO_CHS/AMBIG/MAPPING/DUP/MISSING 等 skip, 保留原 status,
    # strong_object_footprint=1 仅作附加诊断 (不覆盖 provenance, 不为了凑数)。
    if _strong and row["status"] == "ELIGIBLE_EXISTING_CHS":
        row["status"] = "SKIP_FALSE_POSITIVE_INTERNAL_POSE"
        row["decision_subtype"] = "AUTO_STRONG_OBJECT_FOOTPRINT"
        row["reason"] = (
            f"STRONG_OBJECT_FOOTPRINT: OBJD={row['OBJD_count']} AND COBJ={row['COBJ_count']} "
            f"AND (RSLT={row['RSLT_count']} OR FTPT={row['FTPT_count']}) —— 功能物品内置 pose, "
            f"非独立 Pose Pack (448 census 唯一命中即 Kritical)")

    # ---- manual adjudication (最末层, 精确 SHA256 身份匹配) ----
    # production precedence: baseline -> strong auto gate -> manual (本层)。
    # manual 只能作用于【精确匹配的 frozen package identity】; 不按 basename/author/path。
    # 命中 manual registry 且 decision=SKIP -> 强制 SKIP_FALSE_POSITIVE_INTERNAL_POSE,
    # reason 标 MANUAL_REVIEW_CONFIRMED。registry 不存在/不命中 -> 无影响。
    row.setdefault("manual_adjudicated", 0)
    row.setdefault("manual_sha256", "")
    row.setdefault("manual_decision", "")
    row.setdefault("manual_basis", "")
    row.setdefault("manual_evidence", "")
    try:
        _ma = _get_manual_adj()
        if _ma is not None:
            _applied, _hit = _ma.apply(row)
    except Exception as ex:
        # manual 层失败不阻断扫描 (registry 缺失/损坏按无裁决处理), 记录诊断
        row["status"] = row.get("status", "ERROR")
        row["reason"] = (row.get("reason", "") + f" | manual_adj_error: {ex}").strip()
    return row


# 懒加载的 manual adjudicator (模块级缓存, key=registry path)
_MANUAL_CACHE = {}

def _get_manual_adj():
    """按默认 registry 路径懒加载 ManualAdjudicator; 缺失/损坏 -> None (不阻塞扫描)。"""
    from manual_adjudication import ManualAdjudicator, DEFAULT_REGISTRY
    if not os.path.isfile(DEFAULT_REGISTRY):
        return None
    if DEFAULT_REGISTRY not in _MANUAL_CACHE:
        try:
            _MANUAL_CACHE[DEFAULT_REGISTRY] = ManualAdjudicator(DEFAULT_REGISTRY).load()
        except Exception:
            return None
    return _MANUAL_CACHE[DEFAULT_REGISTRY]


def _classify_baseline(row: dict) -> None:
    if row["CHS_0x01_exists"] == 0:
        row["status"] = "SKIP_NO_CHS"
        row["reason"] = "缺 0x01 CHS (不自行创建, 不推导新 Instance)"
        return
    if row["CHS_target_STBL_count"] != 1:
        row["status"] = "SKIP_AMBIGUOUS_TGI"
        row["reason"] = f"CHS 目标 STBL 数 {row['CHS_target_STBL_count']} != 1 (多 target / 多 family)"
        return
    # (唯一 target) 含重复 KeyHash — 保守跳过, 不进 ELIGIBLE。
    # 即使重复只落在 UNMAPPED, 当前阶段一律不区分, 全部跳过 (写 sidecar 需逐 key 精确
    # 反查 + 原文保留, 重复 hash 使“唯一 key→唯一文本”映射失效, 无法保证 writer 只动目标 key)。
    if row["duplicate_key_hash_count"] > 0:
        row["status"] = "SKIP_DUPLICATE_KEYHASH"
        row["reason"] = (
            "target CHS contains duplicate KeyHash: "
            f"physical={row['CHS_entry_count']}, unique={row['CHS_unique_key_hash_count']}, "
            f"duplicate_hashes={row['duplicate_key_hash_count']}, "
            f"extra_occurrences={row['duplicate_extra_occurrences']}")
        return
    # 硬 invariant: target STBL 三分法必须全量覆盖 (同一计量单位: unique key hash)。
    # TRANSLATE + KEEP + UNMAPPED == CHS_unique_key_hash_count (exact CHS target STBL 的
    # unique key 总数)。不满足 => 计数串了 scope / 计量单位不一致, 绝不判 ELIGIBLE。
    _sum = (row["exact_structural_translate_count"]
            + row["keep_count"] + row["unmapped_uncertain_count"])
    if _sum != row["CHS_unique_key_hash_count"]:
        row["status"] = "ERROR_COVERAGE_INVARIANT"
        row["reason"] = (f"target 三分法不满足 invariant (按 unique key hash 计量): "
                          f"TRANSLATE+{row['exact_structural_translate_count']} "
                          f"+KEEP+{row['keep_count']} +UNMAPPED+{row['unmapped_uncertain_count']} "
                          f"={_sum} != CHS_unique_key_hash_count={row['CHS_unique_key_hash_count']} "
                          f"(scope 串包或计量单位不一致: 其他 STBL/locale/orphan 混入)")
        return
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
        return
    if row["translate_set_complete"] == 0:
        row["status"] = "SKIP_MAPPING_UNCERTAIN"
        # set-level invariant: 收集 pv_refs 与生成 TRANSLATE keys 必须同源 (同一字段定义)。
        # TRANSLATE_KEY_SET != RESOLVED_PLAYER_VISIBLE_KEY_SET => 分类逻辑与收集逻辑脱节,
        # 或存在被旧 DISPLAY 分类吞掉的额外字段 (如 pose_description), 不允许 ELIGIBLE。
        row["reason"] = (f"TRANSLATE key set != resolved player-visible key set: "
                          f"TRANSLATE_SET={row['translate_key_set_size']} vs "
                          f"RESOLVED_PV_SET={row['resolved_pv_key_set_size']} "
                          f"(分类与 pv_refs 不同源或有额外字段被吞)")
        return
    if row["exact_structural_translate_count"] == 0 and row["keep_count"] == 0:
        row["status"] = "SKIP_MAPPING_UNCERTAIN"
        # 语义确认 (2026-08-15): unmapped_uncertain_count>0 本身【不】触发本状态。
        # 本状态仅当: 结构上【没有任何】 player-visible 引用能解析到 CHS target STBL key
        # (translate==0 且 keep==0, 即玩家可见字段引用完全无法 join) 时触发。
        # 普通 orphan/旧 STBL/无 XML 引用 key 只记 unmapped_uncertain_count, 不阻塞写入
        # 例: Tibo131 translate=36/keep=1/unmapped=17 -> 仍 ELIGIBLE (17 unmapped 全 untouched)
        row["reason"] = ("无任何已解析到 target CHS STBL 的 player-visible 结构引用 "
                          "(translate=0 且 keep=0); unmapped/orphan key 不阻塞写入")
        return
    row["status"] = "ELIGIBLE_EXISTING_CHS"
    row["reason"] = ("存在唯一 0x01 CHS 目标, 无重复 KeyHash, 三分法全量覆盖 + invariant 满足 + "
                      "player-visible refs 全部 exact resolve")
    return row


# ---------------------------------------------------------------- 输入清单
def _apply_path_remap(p: str) -> str:
    """corpus inventory 路径修正: 按 basename 匹配 PATH_REMAP。
    不命中则原样返回。仅修正 production 源路径 (移动/改名), 不改 mapping 判定。"""
    p = (p or "").strip()
    if not p:
        return p
    # 同时按 / 与 \\ 切 basename (Windows 路径在 Linux 上无 os.path 分隔符语义)
    base = p.replace("\\", "/").split("/")[-1]
    if base in PATH_REMAP:
        return PATH_REMAP[base]
    return p


def load_packages(list_path=None) -> list:
    if list_path:
        with open(list_path, encoding="utf-8-sig") as f:
            return [_apply_path_remap(ln) for ln in f if ln.strip()]
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
                    paths.append(_apply_path_remap(p))
    return paths


# ---------------------------------------------------------------- cohort
def _cohort_detail(row):
    """cohort 层只读补算 (不触碰 frozen coverage):

    target_STBL_compression_state   <exact CHS 目标 STBL> 自己的压缩态
    package_has_compressed_resources 包内任意资源被压缩

    这两个字段在 frozen scan_package 里被合并成 compression_state;
    此处仅对进入 roster 的少数候选 (≤10) 重新只读解析以拆分, 不写任何东西。
    """
    d = dict(row)
    d["target_STBL_compression_state"] = "UNKNOWN"
    d["package_has_compressed_resources"] = "UNKNOWN"
    p = row.get("package_path")
    if not p or not os.path.exists(p):
        return d
    try:
        idx, err = safe_parse(p)
        if err or idx is None:
            return d
        backend = get_backend("readonly").open(p)
        num = len(idx.entries)
        d["package_has_compressed_resources"] = \
            "YES" if any(e.is_compressed for e in idx.entries) else "NO"
        # 唯一 CHS 目标 STBL (ELIGIBLE 保证 CHS_target_STBL_count==1)
        target = None
        for e in idx.entries:
            if e.type_id == STBL_TID and ((e.instance_id >> 56) & 0xFF) == LOCALE_CHS:
                target = e
                break
        if target is not None:
            comp = 0
            try:
                data = backend.read_small_resource(target, max_bytes=2 * 1024 * 1024)
            except Exception:
                data = None
            pr = parse_stbl(data) if data else None
            if pr:
                comp = pr[1]
            # is_compressed (资源 offset 高位) 或 header comp 字节非 0 (zlib 'ZB'=0x5A42 亦视为压缩)
            if target.is_compressed or comp or comp == 0x5A42:
                d["target_STBL_compression_state"] = "COMPRESSED"
            else:
                d["target_STBL_compression_state"] = "UNCOMPRESSED"
        backend.close()
    except Exception:
        pass
    return d


def _pick_best(avail, keyfn, label, used):
    """确定性: 从 avail (未被 used 占用) 中按 keyfn 取最优, 平手取路径字典序最小。"""
    cand = [r for r in avail if r["package_path"] not in used]
    if not cand:
        return None
    return min(cand, key=lambda r: (keyfn(r), r["package_path"]))


def pick_cohort(rows):
    """确定性地选出恰好 10 个【不同】真实 ELIGIBLE_EXISTING_CHS 包 (无 N/A 槽)。

    优先级顺序 (风险维度覆盖):
      1. target CHS entry count 最小
      2. target CHS entry count 最大
      3. median-ish (普通规模)
      4. PACK_TITLE + PACK_DESCRIPTION 都在
      5. 只有 pose_display_name, 无 pack title/description
      6. non_ascii_source_present = 1
      7. long_string_present = 1
      8. repeated_source_text_present = 1
      9. unmapped_uncertain_count 较高但仍 ELIGIBLE
     10. compression 风险样本

    每个 slot 都从「剩余未用」的 ELIGIBLE 里确定性挑选; 若某优先级无可选
    (真实 448 基本不可能), 则用后续优先级 + 剩余候选按顺序补齐, 始终保证
    恰好 min(10, len(eligible)) 个不同真实包。不产生 N/A、不重复、非 ELIGIBLE 不进。

    额外风险维度备注 (不占槽, 仅记录):
      multi-PPI (PosePackInstance_count>1) 是否存在于 ELIGIBLE;
      multiple-target-STBL-family 在 ELIGIBLE 中永不存在 (因 ELIGIBLE 要求
        CHS_target_STBL_count==1) -> 记为 PRESENT_BUT_UNSUPPORTED。

    返回: (picks, notes)  picks=[(slot, why, row), ...], notes=[str, ...]
    """
    elig = [r for r in rows if r["status"] == "ELIGIBLE_EXISTING_CHS"]
    notes = []

    # ---- 额外风险维度存在性备注 (不占槽) ----
    multi_ppi = [r for r in elig if r["PosePackInstance_count"] > 1]
    if multi_ppi:
        notes.append(f"multi-PPI (PosePackInstance_count>1) 存在于 ELIGIBLE: {len(multi_ppi)} 个 "
                     f"(作为额外风险维度可被选入)")
    else:
        notes.append("multi-PPI (PosePackInstance_count>1): NOT_PRESENT_AMONG_ELIGIBLE")
    multi_fam = [r for r in elig
                 if r["multiple_target_STBL_families"] or r["CHS_target_STBL_count"] > 1]
    if multi_fam:
        notes.append(f"multiple-target-STBL-family 存在于 ELIGIBLE: {len(multi_fam)} 个")
    else:
        # ELIGIBLE 强制 CHS_target_STBL_count==1, 故多 target family 不会进 ELIGIBLE
        present_all = any(r["multiple_target_STBL_families"] for r in rows)
        if present_all:
            notes.append("multiple-target-STBL-family: PRESENT_BUT_UNSUPPORTED "
                         "(corpus 存在但仅限 SKIP_AMBIGUOUS_TGI, 不进 ELIGIBLE/roster)")
        else:
            notes.append("multiple-target-STBL-family: NOT_PRESENT_IN_CORPUS")

    used = set()
    picks = []
    slot = 0

    def place(label, r):
        nonlocal slot
        slot += 1
        picks.append((slot, label, r))
        if r is not None:
            used.add(r["package_path"])

    # ---- 优先级槽 (每槽从剩余 eligible 确定性挑选) ----
    # 1) 最小 CHS entry
    r = _pick_best(elig, lambda r: r["CHS_entry_count"], None, used)
    place("target CHS entry count 最小 (极小 target 风险)", r)

    # 2) 最大 CHS entry
    r = _pick_best(elig, lambda r: -r["CHS_entry_count"], None, used)
    place("target CHS entry count 最大 (极大 target 风险)", r)

    # 3) median-ish
    if elig:
        mid = sorted(x["CHS_entry_count"] for x in elig)
        med = mid[len(mid) // 2]
        r = _pick_best(elig, lambda r: abs(r["CHS_entry_count"] - med), None, used)
    else:
        r = None
    place("median 附近 CHS entry (普通规模)", r)

    # 4) pack title + description 都在
    r = _pick_best(elig, lambda r: not (r["pack_title_ref_count"] > 0 and r["pack_description_ref_count"] > 0),
                   None, used)
    if r is None:
        r = _pick_best(elig, lambda r: 0, None, used)
    place("PACK_TITLE + PACK_DESCRIPTION 都在", r)

    # 5) 只有 pose_display_name, 无 pack title/description
    r = _pick_best(elig,
                   lambda r: not (r["pose_display_name_ref_count"] > 0
                                  and r["pack_title_ref_count"] == 0
                                  and r["pack_description_ref_count"] == 0),
                   None, used)
    if r is None:
        r = _pick_best(elig, lambda r: 0, None, used)
    place("只有 pose_display_name, 无 pack title/description", r)

    # 6) non_ascii
    r = _pick_best(elig, lambda r: 0 if r["non_ascii_source_present"] == 1 else 1, None, used)
    place("non_ascii_source_present = 1", r)

    # 7) long_string
    r = _pick_best(elig, lambda r: 0 if r["long_string_present"] == 1 else 1, None, used)
    place("long_string_present = 1", r)

    # 8) repeated
    r = _pick_best(elig, lambda r: 0 if r["repeated_source_text_present"] == 1 else 1, None, used)
    place("repeated_source_text_present = 1", r)

    # 9) unmapped_uncertain 较高但仍 ELIGIBLE
    r = _pick_best(elig, lambda r: -r["unmapped_uncertain_count"], None, used)
    place("unmapped_uncertain_count 较高但仍 ELIGIBLE", r)

    # 10) compression 风险样本 (目标 CHS STBL 压缩优先; 其次包内任意压缩)
    if elig:
        def comp_key(r):
            d = _cohort_detail(r)
            t = d["target_STBL_compression_state"]
            pkg = d["package_has_compressed_resources"]
            return (0 if t == "COMPRESSED" else 1, 0 if pkg == "YES" else 1)
        r = _pick_best(elig, comp_key, None, used)
    else:
        r = None
    place("compression 风险样本 (target-STBL 压缩优先)", r)

    # ---- 若某些槽未选出 (小样本退化), 用剩余候选按顺序补齐到 min(10, len(eligible)) ----
    i = 0
    while len(picks) < 10 and len(eligible_pool := [x for x in elig if x["package_path"] not in used]):
        r = _pick_best(elig, lambda r: (r["CHS_entry_count"], i), None, used)
        if r is None:
            break
        place(f"补齐 (剩余候选, 确定性顺序)", r)
        i += 1

    return picks, notes


# ---------------------------------------------------------------- main
def _print_help():
    print(__doc__)
    print("""用法:
  python scripts/pose_coverage.py --list <file> [--out cov.csv] [--report rep.md] [--cohort-out cohort.csv]
  python scripts/pose_coverage.py -h | --help

选项:
  --list <file>    明文 package 路径列表 (一行一个)。缺省读 output/pose_verification.csv
                   (过滤 POSE_VERIFIED, 即 659 清单)。
  --out <csv>      新 coverage 输出路径 (默认 output/coverage.csv)。
  --report <md>    新报告输出路径 (默认 output/coverage_report.md)。
  --cohort-out <csv> 新 cohort 输出路径。若不指定且 output/cohort_selection.csv 已存在
                   (冻结历史 cohort), 则【拒绝覆盖】, 只报告 roster 是否变化, 不写文件。
  --force          显式允许覆盖已存在的 --out/--report/--cohort-out 目标 (默认 fail-closed)。

安全:
  * -h/--help 立即返回 (rc=0), 零扫描、零写入。
  * 默认不覆盖已存在的历史 frozen cohort (除非显式给 --cohort-out)。
  * --out/--report/--cohort-out 目标已存在时默认 fail-closed (rc=1), 需 --force 或新路径。
  * coverage.csv / report 为本次 rerun 产物, 默认覆盖各自路径。
""")


_COH_COLS = ["cohort_slot", "selection_reason", "package_path", "status",
             "CHS_target_TGI", "CHS_entry_count", "CHS_unique_key_hash_count",
             "exact_structural_translate_count", "keep_count", "unmapped_uncertain_count",
             "pack_title_ref_count", "pack_description_ref_count", "pose_display_name_ref_count",
             "non_ascii_source_present", "long_string_present", "repeated_source_text_present",
             "target_STBL_compression_state", "package_has_compressed_resources",
             "PosePackInstance_count", "multiple_target_STBL_families"]


def _write_cohort(path, cols, picks):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for slot, why, r in picks:
            if r is None:
                continue
            d = _cohort_detail(r)
            w.writerow([slot, why, d["package_path"], d["status"],
                        d["CHS_target_TGI(s)"], d["CHS_entry_count"], d["CHS_unique_key_hash_count"],
                        d["exact_structural_translate_count"], d["keep_count"], d["unmapped_uncertain_count"],
                        d["pack_title_ref_count"], d["pack_description_ref_count"], d["pose_display_name_ref_count"],
                        d["non_ascii_source_present"], d["long_string_present"], d["repeated_source_text_present"],
                        d["target_STBL_compression_state"], d["package_has_compressed_resources"],
                        d["PosePackInstance_count"], d["multiple_target_STBL_families"]])


def main():
    list_path = None
    out_csv = None
    report_path = None
    cohort_out = None
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        _print_help()
        return 0
    i = 0
    while i < len(args):
        if args[i] == "--list":
            list_path = args[i + 1]; i += 2; continue
        if args[i] == "--out":
            out_csv = args[i + 1]; i += 2; continue
        if args[i] == "--report":
            report_path = args[i + 1]; i += 2; continue
        if args[i] == "--cohort-out":
            cohort_out = args[i + 1]; i += 2; continue
        if args[i] == "--force":
            i += 1; continue
        print(f"[ERROR] 未知参数: {args[i]} (用 -h 查看用法)")
        return 2
        i += 1
    if list_path and not os.path.exists(list_path):
        print(f"[ERROR] --list 文件不存在: {list_path}")
        return 2

    # ---- fail-closed: 显式输出目标已存在则不覆盖 (安全新文件) ----
    # 默认拒绝覆盖历史/已存在产物; --force 才覆盖。coverage/report/cohort 均检查。
    _force = "--force" in args
    _targets = [("--out", out_csv), ("--report", report_path), ("--cohort-out", cohort_out)]
    for flag, t in _targets:
        if t and os.path.exists(t) and not _force:
            print(f"[FAIL-CLOSED] {flag} 目标已存在, 不覆盖: {t} (用 --force 才覆盖, 或用新路径)")
            return 1

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

    cov_csv = Path(out_csv) if out_csv else (out_dir / "coverage.csv")
    cov_csv.parent.mkdir(parents=True, exist_ok=True)
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
              "SKIP_MAPPING_UNCERTAIN", "SKIP_DUPLICATE_KEYHASH",
              "SKIP_FALSE_POSITIVE_INTERNAL_POSE",
              "SKIP_MISSING_FILE", "ERROR", "ERROR_COVERAGE_INVARIANT"):
        print(f"  {s}: {sc.get(s, 0)}")

    # ---- cohort: 冻结保护 + roster-change 报告 ----
    picks, coh_notes = pick_cohort(rows)
    # 防退化: 无 ELIGIBLE 时槽位 r 可能为 None (0 ELIGIBLE 边界), 不崩溃。
    _paths_new = [r["package_path"] for _, _, r in picks if r is not None]
    _dcount_new = len(set(_paths_new))
    frozen_coh = out_dir / "cohort_selection.csv"

    # 读取既有 frozen cohort (若存在), 供 roster 对比; 不可读即视为无历史。
    frozen_names = set()
    frozen_exists = frozen_coh.exists()
    if frozen_exists:
        try:
            with open(frozen_coh, encoding="utf-8-sig") as f:
                fr = list(csv.reader(f))
            if fr:
                hdr = fr[0]
                try:
                    pi = hdr.index("package_path")
                    frozen_names = {os.path.basename(r[pi]) for r in fr[1:] if len(r) > pi and r[pi]}
                except ValueError:
                    frozen_names = set()
        except Exception:
            frozen_exists = False

    new_names = {os.path.basename(p) for p in _paths_new}
    added = sorted(new_names - frozen_names)
    removed = sorted(frozen_names - new_names)

    # 决定 cohort 输出路径: 显式 --cohort-out 才写; 否则有 frozen 则拒绝覆盖。
    if cohort_out:
        coh_csv = Path(cohort_out)
        coh_csv.parent.mkdir(parents=True, exist_ok=True)
        _write_cohort(coh_csv, _COH_COLS, picks)
        wrote = True
        print(f"cohort: {coh_csv} ({len(picks)} 行, 显式 --cohort-out)")
    elif frozen_exists:
        coh_csv = frozen_coh
        wrote = False
        print(f"cohort_selection.csv 已存在 (冻结历史 cohort): 拒绝覆盖, 未写入。"
              f"(如需新 cohort 用 --cohort-out <path>)")
    else:
        coh_csv = frozen_coh
        _write_cohort(coh_csv, _COH_COLS, picks)
        wrote = True
        print(f"cohort: {coh_csv} ({len(picks)} 行)")

    # roster-change 报告 (无论是否写入都输出):
    print(f"  roster 变化: 新选择 {_dcount_new} distinct ELIGIBLE")
    print(f"    added(n=新增)   = {len(added)}: {added if added else '-'}")
    print(f"    removed(n=剔除) = {len(removed)}: {removed if removed else '-'}")
    if wrote and not frozen_exists:
        print("  注: 首次生成 cohort (无历史 frozen)。")
    elif not wrote and frozen_exists:
        print("  注: 历史 frozen cohort 保留, 本次 roster 仅对比未写入。")

    # ---- markdown report ----
    rep = Path(report_path) if report_path else (out_dir / "coverage_report.md")
    rep.parent.mkdir(parents=True, exist_ok=True)
    with open(rep, "w", encoding="utf-8") as f:
        f.write("# 659 CONFIRMED_POSE coverage 报告 (只读)\n\n")
        f.write(f"扫描包数: {len(rows)}\n\n## status 数量\n\n")
        for s in ("ELIGIBLE_EXISTING_CHS", "SKIP_NO_CHS", "SKIP_AMBIGUOUS_TGI",
                  "SKIP_MAPPING_UNCERTAIN", "SKIP_DUPLICATE_KEYHASH",
                  "SKIP_FALSE_POSITIVE_INTERNAL_POSE",
                  "SKIP_MISSING_FILE", "ERROR", "ERROR_COVERAGE_INVARIANT"):
            f.write(f"- {s}: {sc.get(s, 0)}\n")
        f.write(f"\n## cohort roster 变化 (对比历史 frozen)\n\n")
        f.write(f"- frozen 存在: {frozen_exists}; 本次写入: {wrote}\n")
        f.write(f"- 新增 (added): {len(added)} → {added if added else '-'}\n")
        f.write(f"- 剔除 (removed): {len(removed)} → {removed if removed else '-'}\n")
        f.write("\n## cohort 风险维度备注\n\n")
        for n in coh_notes:
            f.write(f"- {n}\n")
        f.write("\n## 代表性 10-cohort (程序化选择, 非随机/非人工; 均为 ELIGIBLE 真实包)\n\n")
        f.write("| slot | why | package | status | CHS TGI | CHS entries | unique | TRANSLATE | KEEP | UNMAPPED |"
                " title | desc | pdn | non_a | long | rep | tgt_comp | pkg_comp | PPI | multi_fam |\n")
        f.write("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
        for slot, why, r in picks:
            if r is None:
                continue
            d = _cohort_detail(r)
            f.write(f"| {slot} | {why} | {Path(d['package_path']).name} | {d['status']} | "
                    f"{d['CHS_target_TGI(s)']} | {d['CHS_entry_count']} | {d['CHS_unique_key_hash_count']} | "
                    f"{d['exact_structural_translate_count']} | {d['keep_count']} | {d['unmapped_uncertain_count']} | "
                    f"{d['pack_title_ref_count']} | {d['pack_description_ref_count']} | {d['pose_display_name_ref_count']} | "
                    f"{d['non_ascii_source_present']} | {d['long_string_present']} | {d['repeated_source_text_present']} | "
                    f"{d['target_STBL_compression_state']} | {d['package_has_compressed_resources']} | "
                    f"{d['PosePackInstance_count']} | {d['multiple_target_STBL_families']} |\n")
        # compressed 存在性记录
        any_comp = any("COMPRESSED" in r["compression_state"] for r in rows)
        f.write(f"\ncompressed STBL/package 在 659 中: "
                f"{'存在 (已优先纳入 cohort)' if any_comp else 'NOT_PRESENT_IN_CORPUS'}\n")
    print(f"report: {rep}")
    return 0


_COLS = ["package_path", "file_size", "PosePackInstance_count", "STBL_count_total",
         "CHS_0x01_exists", "CHS_target_STBL_count", "CHS_target_TGI(s)", "CHS_entry_count",
         "CHS_unique_key_hash_count", "duplicate_key_hash_count",
         "duplicate_extra_occurrences", "duplicate_writable_key_count",
         "pack_title_ref_count", "pack_description_ref_count", "pose_display_name_ref_count",
         "exact_structural_translate_count", "keep_count", "unmapped_uncertain_count",
         "invariant_ok",
         "player_visible_structural_ref_count", "unique_player_visible_ref_count",
         "resolved_player_visible_ref_count", "unresolved_player_visible_ref_count",
         "translate_set_complete", "resolved_pv_key_set_size", "translate_key_set_size",
         "STBL_version", "compression_state",
         "non_ascii_source_present", "long_string_present", "repeated_source_text_present",
         "OBJD_count", "COBJ_count", "RSLT_count", "FTPT_count",
         "strong_object_footprint",
         "manual_adjudicated", "manual_sha256", "manual_decision", "manual_basis", "manual_evidence",
         "decision_subtype",
         "multiple_target_STBL_families", "status", "reason"]


if __name__ == "__main__":
    sys.exit(main())
