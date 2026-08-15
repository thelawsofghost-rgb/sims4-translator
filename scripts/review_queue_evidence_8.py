#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
review_queue_evidence_8.py — 8 包只读证据提取 (供 Dorothy 最后裁决, 不改 production gate)
==========================================================================================
输入: 最后复核队列 8 包 (6 WEAK_SIGNAL + 2 LOW_CONF) 的明文路径列表。
输出: output/review_queue_evidence_8.csv + 终端逐包 evidence summary。

对每包每行生成证据 (只读, 不写任何 status / 不改 447 集合 / 不动 gate):
  package_path, current_status
  PosePackInstance_count
  PACK_TITLE_ref_count, PACK_DESCRIPTION_ref_count, POSE_DISPLAY_NAME_ref_count,
  unique_player_visible_ref_count
  approved role breakdown: exact_structural_translate_count / keep_count / unmapped_uncertain_count
  功能资源 (VERIFIED type ids, lib/s4pi_src 核实): OBJD / COBJ / RSLT / FTPT /
    interaction(0xE882D22F) / action(0x0C772E27) / animation(0xEE17C6AD)
  CLIP_count(0x6B20C4F3), ANIM_RCOL_count(0xBC4A5044)
  pose_root_count (PosePackInstance 根), nonpose_root_count (其它 XML 根, 非 pose)
  strong_object_footprint (OBJD>0 AND COBJ>0 AND (RSLT>0 OR FTPT>0))

玩家可见文本本身 (exact source + repr, 保留空格/隐藏字符):
  PACK_TITLE source / PACK_DESCRIPTION source / POSE_DISPLAY_NAME source

裁决原则 (只给证据, 不写最终 status):
  * 6 weak-signal: 不能因文件名含 interactions/override/nap/phone 直接 SKIP。
    必须证明 PosePackInstance 是功能交互/override 内部动画容器而非独立 Pose Player pack:
      - 是否缺 PACK_TITLE / PACK_DESCRIPTION
      - 是否只有内部 POSE_DISPLAY_NAME
      - 是否同时有 verified interaction/action/animation gameplay resources
    若形成明确功能性交互轮廓 -> 人工可裁 SKIP_FALSE_POSITIVE_INTERNAL_POSE; 否则 KEEP_ELIGIBLE。
  * 2 LOW_CONF: 不因 pose_display_refs=1 自动 SKIP (小 Pose Pack 可能只有 1 个姿势)。
    若结构纯 PosePackInstance、无功能 footprint、pack/pose 信息合理 -> KEEP_ELIGIBLE (宁保留不误杀)。
"""
import sys, os, csv
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "scripts"))
import pose_coverage as PC

# ---- VERIFIED functional resource type ids (lib/s4pi_src 权威) ----
INTERACTION_TID = 0xE882D22F   # TS4 XML -- interaction
ACTION_TID = 0x0C772E27        # TS4 XML -- action
ANIMATION_TID = 0xEE17C6AD     # TS4 XML -- animation
CLIP_TID = 0x6B20C4F3          # CLIP (verified in repo)
ANIM_RCOL_TID = 0xBC4A5044     # ANIM_RCOL (verified in repo)
TUN_TID = 0x0333406C           # TUNING_XML
WW_ANIM_XML_TID = 0x7DF2169C   # neutral TUNING_XML/PosePackInstance (non-functional)
STBL_TID = 0x220557DA
LOCALE_CHS = 0x01

_OUT_COLS = [
    "package_path", "basename", "current_status",
    "PosePackInstance_count",
    "PACK_TITLE_ref_count", "PACK_DESCRIPTION_ref_count", "POSE_DISPLAY_NAME_ref_count",
    "unique_player_visible_ref_count",
    "translate_count", "keep_count", "unmapped_uncertain_count",
    "OBJD", "COBJ", "RSLT", "FTPT", "interaction", "action", "animation_component",
    "CLIP_count", "ANIM_RCOL_count",
    "pose_root_count", "nonpose_root_count",
    "strong_object_footprint",
    "PACK_TITLE_source", "PACK_DESCRIPTION_source", "POSE_DISPLAY_NAME_source",
    "functional_interaction_profile", "evidence_note",
]


def _functional_census(entries):
    """VERIFIED 功能资源逐 type 计数 (lib/s4pi_src 权威 id)。"""
    c = {"interaction": 0, "action": 0, "animation_component": 0,
         "CLIP_count": 0, "ANIM_RCOL_count": 0, "tuning": 0, "ww_anim": 0}
    for e in entries:
        t = e.type_id
        if t == INTERACTION_TID: c["interaction"] += 1
        elif t == ACTION_TID: c["action"] += 1
        elif t == ANIMATION_TID: c["animation_component"] += 1
        elif t == CLIP_TID: c["CLIP_count"] += 1
        elif t == ANIM_RCOL_TID: c["ANIM_RCOL_count"] += 1
        elif t == TUN_TID: c["tuning"] += 1
        elif t == WW_ANIM_XML_TID: c["ww_anim"] += 1
    return c


def _xml_root_kind(root):
    """返回根类型: 'pose' | 'other'。pose = PosePackInstance 根。"""
    return "pose" if PC.is_pose_pack_root(root) else "other"


def _extract_pv_sources(row, backend, idx):
    """从 XML 提取玩家可见字段的 exact source 字符串 (repr 保留空格/隐藏字符)。

    返回 (title_src, desc_src, pdn_src) —— 各字段首个非0-hash 引用的 STBL 原文;
    STBL 找不到则回退到 XML 内联文本。统一 repr() 输出。
    """
    # 先建 hash->text 映射 (所有 locale STBL), 供反查
    hash2text = {}
    stbl_entries = [e for e in idx.entries if e.type_id == STBL_TID]
    for e in stbl_entries:
        try:
            data = backend.read_small_resource(e, max_bytes=2 * 1024 * 1024)
        except Exception:
            data = None
        pr = PC.parse_stbl(data) if data else None
        if pr:
            for kh, _, txt in pr[2]:
                if kh not in hash2text and txt:
                    hash2text[kh] = txt
    xmls = PC.read_xml_payloads(backend, idx.entries)
    found = {"display_name": None, "description": None, "pose_display_name": None}
    for xinst_id, root, _raw in xmls:
        for el, pack_level, in_pose in PC._walk_ctx(root):
            n = el.attrib.get("n")
            if not n:
                continue
            val = (el.text or "").strip()
            if not val:
                continue
            h = PC.parse_display_hash(val)
            if h is None:
                continue
            nl = n.lower()
            if nl in PC.PV_PACK_TITLE and pack_level and found["display_name"] is None:
                found["display_name"] = hash2text.get(h, val)
            elif nl in PC.PV_PACK_DESC and pack_level and found["description"] is None:
                found["description"] = hash2text.get(h, val)
            elif nl in PC.PV_POSE_DISPLAY and in_pose and found["pose_display_name"] is None:
                found["pose_display_name"] = hash2text.get(h, val)
    axis = {"display_name": "PACK_TITLE", "description": "PACK_DESCRIPTION",
            "pose_display_name": "POSE_DISPLAY_NAME"}
    # 无唯一 CHS target 时, scan_package 的计数为0, 但证据工具仍应尽量给出可见文本;
    # 这里以“XML 中存在引用 + STBL 原文”为准, 供 Dorothy 判断。
    return {k: repr(v) if v is not None else "" for k, v in found.items()}


def evidence_one(path, status):
    row = {c: "" for c in _OUT_COLS}
    row["package_path"] = path
    row["basename"] = os.path.basename(path)
    row["current_status"] = status
    r = PC.scan_package(path)
    row = {**row, **{k: r.get(k, "") for k in [
        "PosePackInstance_count", "pack_title_ref_count", "pack_description_ref_count",
        "pose_display_name_ref_count", "unique_player_visible_ref_count",
        "exact_structural_translate_count", "keep_count", "unmapped_uncertain_count",
        "OBJD_count", "COBJ_count", "RSLT_count", "FTPT_count",
        "strong_object_footprint"]}}
    row["PACK_TITLE_ref_count"] = row.pop("pack_title_ref_count")
    row["PACK_DESCRIPTION_ref_count"] = row.pop("pack_description_ref_count")
    row["POSE_DISPLAY_NAME_ref_count"] = row.pop("pose_display_name_ref_count")
    row["translate_count"] = row.pop("exact_structural_translate_count")
    row["keep_count"] = row.pop("keep_count")
    row["unmapped_uncertain_count"] = row.pop("unmapped_uncertain_count")
    row["OBJD"] = row.pop("OBJD_count"); row["COBJ"] = row.pop("COBJ_count")
    row["RSLT"] = row.pop("RSLT_count"); row["FTPT"] = row.pop("FTPT_count")

    # 功能资源 census + 根分类 + 可见源文本 (额外只读解析)
    backend = None
    try:
        idx, err = PC.safe_parse(path)
        if err or idx is None:
            row["evidence_note"] = f"DBPF 解析失败: {err}"
            row["pose_root_count"] = 0; row["nonpose_root_count"] = 0
            return row
        backend = PC.get_backend("readonly").open(path)
        fc = _functional_census(idx.entries)
        for k in ("interaction", "action", "animation_component", "CLIP_count", "ANIM_RCOL_count"):
            row[k] = fc.get(k, 0)
        # 根分类
        xmls = PC.read_xml_payloads(backend, idx.entries)
        n_pose = sum(1 for _, root, _ in xmls if _xml_root_kind(root) == "pose")
        row["pose_root_count"] = n_pose
        row["nonpose_root_count"] = len(xmls) - n_pose
        # 玩家可见源文本 (repr)
        src = _extract_pv_sources(row, backend, idx)
        row["PACK_TITLE_source"] = src["display_name"]
        row["PACK_DESCRIPTION_source"] = src["description"]
        row["POSE_DISPLAY_NAME_source"] = src["pose_display_name"]
        # 功能性 gameplay 信号 = 仅 interaction/action/animation (visual object gameplay 参数)。
        # CLIP/ANIM_RCOL 是 animation assets (动画剪辑本体/RCOL 属性), 单独标记, 不算 gameplay functional signal。
        has_func = (fc["interaction"] > 0 or fc["action"] > 0
                    or fc["animation_component"] > 0)
        has_anim_assets = (fc["CLIP_count"] > 0 or fc["ANIM_RCOL_count"] > 0)
        missing_pack = (not src["display_name"] or row["PACK_TITLE_ref_count"] == 0)
        profile = []
        if missing_pack: profile.append("缺PACK_TITLE")
        if not src["description"] or row["PACK_DESCRIPTION_ref_count"] == 0:
            profile.append("缺PACK_DESCRIPTION")
        if row["POSE_DISPLAY_NAME_ref_count"] > 0: profile.append("有内部POSE_DISPLAY_NAME")
        if has_func:
            profile.append("有功能资源")
        if has_anim_assets:
            profile.append("有animation assets(CLIP/ANIM_RCOL,非gameplay信号)")
        if row["interaction"] > 0: profile.append(f"interaction={row['interaction']}")
        if row["action"] > 0: profile.append(f"action={row['action']}")
        if row["animation_component"] > 0: profile.append(f"animation={row['animation_component']}")
        row["functional_interaction_profile"] = ";".join(profile) if profile else "无"
        # evidence note: 结构观察 (供裁决参考, 不写 status)
        if row["OBJD"] > 0 and row["COBJ"] > 0 and (row["RSLT"] > 0 or row["FTPT"] > 0):
            row["evidence_note"] += " STRONG_OBJECT_FOOTPRINT命中(功能物品内置pose证据)".strip()
        if missing_pack and has_func and row["pose_root_count"] > 0:
            row["evidence_note"] += " 功能交互/override内部动画容器轮廓(缺pack可见信息+有功能资源)".strip()
        if missing_pack and not has_func and has_anim_assets and row["pose_root_count"] > 0:
            row["evidence_note"] += " 仅animation assets(CLIP/RCOL)无gameplay资源, 不如属override容器, 需人工核".strip()
        if row["PosePackInstance_count"] > 0 and not has_func and missing_pack:
            row["evidence_note"] += " 纯PosePackInstance但缺pack可见信息, 需人工核".strip()
    except Exception as ex:
        row["evidence_note"] = (row.get("evidence_note", "") + f" 额外解析异常: {ex}").strip()
    finally:
        if backend is not None:
            try: backend.close()
            except Exception: pass
    return row


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", default="output/review_queue_8.txt",
                    help="8 包明文路径列表 (每行: <path>\\t<status>)")
    ap.add_argument("--out", default="output/review_queue_evidence_8.csv",
                    help="证据 CSV 输出")
    ap.add_argument("--force", action="store_true", help="目标已存在允许覆盖(默认fail-closed)")
    a = ap.parse_args()

    if not os.path.isfile(a.list):
        print(f"[ERROR] --list 文件不存在: {a.list} (真实 Windows 8 包路径)")
        return 2
    if os.path.exists(a.out) and not a.force:
        print(f"[FAIL-CLOSED] 目标已存在, 不覆盖: {a.out} (用 --force)")
        return 1

    rows = []
    for line in open(a.list, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        path = parts[0].strip()
        status = parts[1].strip() if len(parts) > 1 else "UNKNOWN"
        rows.append(evidence_one(path, status))

    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=_OUT_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in _OUT_COLS})

    # 终端逐包 summary
    print(f"\n[WROTE] {a.out} ({len(rows)} 行, 只读证据, 未写任何 status)\n")
    for r in rows:
        print(f"=== {r['basename']}  [{r['current_status']}] ===")
        print(f"  PPI={r['PosePackInstance_count']}  "
              f"TITLE={r['PACK_TITLE_ref_count']} DESC={r['PACK_DESCRIPTION_ref_count']} "
              f"PDN={r['POSE_DISPLAY_NAME_ref_count']} 唯一pv={r['unique_player_visible_ref_count']}")
        print(f"  role: TRANSLATE={r['translate_count']} KEEP={r['keep_count']} "
              f"UNMAPPED={r['unmapped_uncertain_count']}")
        print(f"  func: OBJD={r['OBJD']} COBJ={r['COBJ']} RSLT={r['RSLT']} FTPT={r['FTPT']} "
              f"interaction={r['interaction']} action={r['action']} animation={r['animation_component']} "
              f"CLIP={r['CLIP_count']} ANIM_RCOL={r['ANIM_RCOL_count']}")
        print(f"  roots: pose={r['pose_root_count']} nonpose={r['nonpose_root_count']}  "
              f"strong={r['strong_object_footprint']}")
        print(f"  PACK_TITLE source:      {r['PACK_TITLE_source']}")
        print(f"  PACK_DESCRIPTION source:{r['PACK_DESCRIPTION_source']}")
        print(f"  POSE_DISPLAY_NAME source:{r['POSE_DISPLAY_NAME_source']}")
        print(f"  profile: {r['functional_interaction_profile']}")
        if r.get("evidence_note"): print(f"  note: {r['evidence_note']}")
        print()
    print(f"===== 证据已导出, 未修改 447 集合 / 未写最终 status, 交 Dorothy 裁决 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
