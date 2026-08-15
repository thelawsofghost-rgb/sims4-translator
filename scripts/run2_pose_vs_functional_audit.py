#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run2_pose_vs_functional_audit.py — 只读分类根因审计:
区分「独立 Andrew Pose Player 姿势包」vs「功能物品内部 animation/pose 数据」。

背景
====
slot7 (_Kritical_BrainwashingMachine1g) 被证为 classifier FALSE POSITIVE:
一个功能物品 Mod, 其 9 个 approved key 全部 role=POSE_DISPLAY_NAME, 但真机购买模式
物品名仍英文 —— 因为这些 key 是物品内部 pose/animation 数据, 并非 Pose Player 包。
裁决: SKIP_FALSE_POSITIVE_INTERNAL_POSE, 应从 Pose 汉化生产集合剔除。

根因
====
当前 ELIGIBLE 门 (pose_coverage._classify) 只要求:
  单 CHS target / 无重复 KeyHash / 结构性引用可 exact resolve / 三分法 invariant / TRANSLATE set 一致
从未要求该包是【独立 Pose Player 包】。而 is_pose_pack_root() 靠 XML root 的类名
(pose_pack / PosePackInstance / poseplayer / pose_list 子树) 判定 —— 功能物品内部
驱动 sim 摆姿/动画同样使用 Sims4 标准 PosePackInstance 机制, 因此「含内部 pose 的
功能物品」会合法满足该门, 其内部 pose_display_name 被误当作「姿势包可翻译项」。

正信号 gates (本工具, 全部基于 VERIFIED 资源类型 + 结构, 不依赖作者名/黑名单)
================================================================================
G-P  pose_root_present : ≥1 个 tuning XML root 命中 is_pose_pack_root (pose 结构存在)

G-O  functional_footprint : 包内存在【非 pose 的 gameplay/object 根】协同出现的证据。
    判定依据 (任一为 True 即判 OBJECT_EMBEDDED 候选):
      O1 非 pose tuning root 数 > 0 且与 pose root 同包共存
          (多个不同根语义: 物品本体 tuning + 它的 pose 动画容器)
      O2 pose 结构以「子树」形式出现在某个【非 pose 根】之下 (嵌套而非顶层独立包)
      O3 包内含 VERIFIED 的 CLIP/ANIM_RCOL 之外的 gameplay 标记:
          存在 TUNING_XML 根 且其 body 含 object/interaction/commodity/gameplay
          行为字段 (object_definition / interaction / buff / moodlet 等)

G-S  standalone_profile : G-P 真 且 G-O 全假 且满足「独立包」正面特征:
      S1 包内所有 tuning XML 根均命中 pose 语义 (无一非 pose gameplay 根)
      S2 pose_list 内 pose_display_name 引用数 >= 2 (多姿势 = pack 语义, 非单动画)

裁决
====
  STANDALONE_POSE_PACK : G-P 真 && !G-O && S1 && S2  -> 应保留在 Pose 生产集合
  OBJECT_EMBEDDED_POSE : G-P 真 && G-O (任意)          -> 应 SKIP_FALSE_POSITIVE_INTERNAL_POSE
  NO_POSE_ROOT         : G-P 假                        -> 非 pose, 不应在集合内 (另查)
  (G-P 真 && !G-O && 不满足 S1/S2) -> POSE_ONLY_LOW_CONF: 低置信, 保守 SKIP (宁漏勿错)

只读约束
=======
  * 不写任何 .package / sidecar / csv(默认只打印; 可选 --out 写报告 csv)
  * 不改 writer/resolver/classifier/coverage/cohort/manifest
  * 不调用任何模型
  * 不重新 generation
用法:
  python scripts/run2_pose_vs_functional_audit.py --list <pkg_list.txt> [--out report.csv]
  python scripts/run2_pose_vs_functional_audit.py --cohort output/cohort_selection.csv [--out ...]
"""
import sys, os, zlib, csv, argparse
from pathlib import Path
from collections import Counter
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse
from backend import get_backend
from resource_types import RESOURCE_TYPES

STBL_TID = 0x220557DA
CLIP_TID = 0x6B20C4F3
RCOL_TID = 0xBC4A5044
TUNING_XML_TID = 0x0333406C

# 非 pose gameplay/object 行为字段 (出现在 XML body 里的结构信号, 用于 O3)
GAMEPLAY_FIELDS = (
    "object_definition", "interaction", "commodity", "buff", "moodlet",
    "gameplay", "object_function", "simulation", "loot", "situation",
    "recipe", "score", "trait", "career", "skill", "statistic",
)


def _decompress(data: bytes) -> bytes:
    if data[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        try:
            return zlib.decompress(data)
        except Exception:
            return data
    return data


def _is_xml(tid: int) -> bool:
    return (RESOURCE_TYPES.is_snippet(tid)
            or RESOURCE_TYPES.is_tuning_xml(tid)
            or RESOURCE_TYPES.is_known_safely(tid, "WW_ANIM_XML"))


def _norm_cls(tag_or_attr: str) -> str:
    return (tag_or_attr or "").lower().replace("_", "").replace(" ", "")


def _is_pose_root(root) -> bool:
    """is_pose_pack_root 的等价判定 (与 pose_coverage 一致)。"""
    tag = (root.tag or "").lower()
    cattr = _norm_cls(root.attrib.get("c") or root.attrib.get("class") or "")
    if "posepack" in tag or "posepack" in cattr or "posepack" in tag.replace("-", ""):
        return True
    for el in root.iter():
        if (el.attrib.get("n") or "").lower() == "pose_list":
            return True
    return False


def _read_roots(backend, entries):
    """返回 [(type_id, root_et, raw)] —— 仅可 ET.parse 的 tuning/snippet/ww XML。"""
    out = []
    for e in entries:
        if not _is_xml(e.type_id):
            continue
        try:
            data = backend.read_small_resource(e, max_bytes=4 * 1024 * 1024)
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
            out.append((e.type_id, root, raw))
            break
    return out


def _pose_display_count(root) -> int:
    """该 pose 树内 pose_display_name 引用数 (非 0 hash 才算)。"""
    n = 0
    for el in root.iter():
        nn = (el.attrib.get("n") or "").lower()
        if nn != "pose_display_name":
            continue
        val = (el.text or "").strip()
        if not val:
            continue
        try:
            v = int(val, 16) if val.lower().startswith("0x") else int(val, 0)
        except ValueError:
            v = None
        if v:
            n += 1
    return n


def classify_package(path: str) -> dict:
    r = {"package_path": path, "pose_root_count": 0, "nonpose_root_count": 0,
         "pose_display_refs": 0, "functional_feel": 0, "nested_pose": 0,
         "has_clip": 0, "verdict": "NO_POSE_ROOT", "reason": ""}
    if not os.path.exists(path):
        r["reason"] = "SKIP_MISSING_FILE"
        return r
    idx, err = safe_parse(path)
    if err or idx is None:
        r["reason"] = f"DBPF 解析失败: {err}"
        r["verdict"] = "ERROR"
        return r
    backend = get_backend("readonly").open(path)
    roots = _read_roots(backend, idx.entries)
    backend.close()

    has_clip = any(e.type_id == CLIP_TID for e in idx.entries)
    r["has_clip"] = 1 if has_clip else 0

    pose_roots = []
    nonpose_roots = []
    for tid, root, raw in roots:
        if _is_pose_root(root):
            pose_roots.append((root, raw))
            r["pose_display_refs"] += _pose_display_count(root)
        else:
            nonpose_roots.append((root, raw))
    r["pose_root_count"] = len(pose_roots)
    r["nonpose_root_count"] = len(nonpose_roots)

    # G-O 判定 (功能物品足迹)
    functional = False
    # O1: 非 pose gameplay/object 根与 pose 根同包共存
    if pose_roots and nonpose_roots:
        # 只把「含 gameplay 字段」的非 pose 根算作功能性证据; 纯 metadata/img 根不算
        gp_nonpose = 0
        for root, raw in nonpose_roots:
            rr = raw.lower()
            if any(f in rr for f in GAMEPLAY_FIELDS):
                gp_nonpose += 1
        if gp_nonpose > 0:
            functional = True
        r["functional_feel"] = gp_nonpose
    # O2: pose 结构以子树形式出现在非 pose 根之下
    if nonpose_roots:
        for root, raw in nonpose_roots:
            # 该非 pose 根体内是否又出现 pose_list / PosePackInstance 子树
            if "pose_list" in raw or "posepackinstance" in raw.lower():
                r["nested_pose"] = 1
                functional = True
                break

    # 裁决
    if not pose_roots:
        r["verdict"] = "NO_POSE_ROOT"
        r["reason"] = "无 pose 根; 不应在本集合内"
        return r
    if functional:
        r["verdict"] = "OBJECT_EMBEDDED_POSE"
        r["reason"] = (f"功能物品内部 pose: nonpose_gameplay_root={r['functional_feel']} "
                       f"nested_pose={r['nested_pose']} (应 SKIP_FALSE_POSITIVE_INTERNAL_POSE)")
        return r
    # 独立包正面特征
    s1 = (r["nonpose_root_count"] == 0)  # 无任何非 pose 根
    s2 = (r["pose_display_refs"] >= 2)   # 多姿势 = pack 语义
    if s1 and s2:
        r["verdict"] = "STANDALONE_POSE_PACK"
        r["reason"] = "独立 Pose Player 包 (仅 pose 根 + 多姿势)"
    else:
        r["verdict"] = "POSE_ONLY_LOW_CONF"
        r["reason"] = f"仅 pose 根但置信不足 (nonpose_root={r['nonpose_root_count']} pdn_refs={r['pose_display_refs']}); 保守 SKIP"
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", help="一行一个 package 路径")
    ap.add_argument("--cohort", help="coverage cohort_selection.csv (取 package_path 列)")
    ap.add_argument("--out", help="可选: 写报告 csv")
    a = ap.parse_args()

    paths = []
    if a.list:
        paths = [l for l in (line.strip() for line in open(a.list, encoding="utf-8-sig")) if l]
    elif a.cohort:
        for r in csv.DictReader(open(a.cohort, encoding="utf-8-sig")):
            if r.get("package_path"):
                paths.append(r["package_path"])
    else:
        print("[ERROR] 需要 --list 或 --cohort")
        return 2

    rows = [classify_package(p) for p in paths]
    vc = Counter(r["verdict"] for r in rows)
    print(f"\n== pose vs functional 分类审计 ({len(rows)} 包) ==")
    for v in ("STANDALONE_POSE_PACK", "OBJECT_EMBEDDED_POSE", "POSE_ONLY_LOW_CONF",
              "NO_POSE_ROOT", "ERROR"):
        if vc.get(v):
            print(f"  {v}: {vc[v]}")
    print("\n-- OBJECT_EMBEDDED / LOW_CONF 明细 --")
    for r in rows:
        if r["verdict"] in ("OBJECT_EMBEDDED_POSE", "POSE_ONLY_LOW_CONF"):
            print(f"  [{r['verdict']}] {os.path.basename(r['package_path'])} :: {r['reason']}")

    if a.out:
        with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\n[WROTE] {a.out} ({len(rows)} 行, 只读审计报告)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
