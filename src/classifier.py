#!/usr/bin/env python3
"""
Package 分类引擎 (核心安全机制)

等级:
  CONFIRMED_WW        → 允许修改 (Phase 3)
  CONFIRMED_POSE      → 允许修改 (Phase 3)
  UNCERTAIN           → 永不修改, 记录待查
  NON_ANIMATION       → 忽略
  ERROR               → 无法读取/解析, 跳过

最高原则:
  FALSE NEGATIVE IS ACCEPTABLE. FALSE POSITIVE IS NOT ACCEPTABLE.
  无法确认 = SKIP.
  只认 VERIFIED 的 Resource Type ID (见 resource_types.py), 未核实类型不参与判定。

分类关键 (不依赖文件名):
  - CONFIRMED_WW :  ≥1 有效 WW Animation XML (animation_raw_display_name 或 animation_display_name)
                    + WW 特有结构 (actors_list / category / locations / tags 至少一项)
                    + ≥1 CLIP
  - CONFIRMED_POSE: Pose Pack Snippet + 关联 STBL 引用 + ≥1 CLIP, 且无 WW XML 特征
  - 本体(WW/PosePlayer) : 脚本/功能 MOD 特征, 无动画包资源组合 → NON_ANIMATION
"""

from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
import re

from resource_types import RESOURCE_TYPES


# ---------------- 分类常量 ----------------

class ConfLevel:
    UNKNOWN = "UNKNOWN"
    CONFIRMED_WW = "CONFIRMED_WW"
    CONFIRMED_POSE = "CONFIRMED_POSE"
    UNCERTAIN = "UNCERTAIN"
    NON_ANIMATION = "NON_ANIMATION"
    ERROR = "ERROR"
    ERROR_UNSUPPORTED_DBPF = "ERROR_UNSUPPORTED_DBPF"


@dataclass
class Classification:
    level: str
    evidence: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "evidence": self.evidence,
            "missing": self.missing,
            "reason": self.reason,
        }


# WW XML 结构信号 (标签名)
WW_ANIM_DISPLAY_RAW = "animation_raw_display_name"
WW_ANIM_DISPLAY_OLD = "animation_display_name"
WW_ANIM_ACTORS = "animation_actors_list"
WW_ANIM_CATEGORY = "animation_category"
WW_ANIM_LOCATIONS = "animation_locations"
WW_ANIM_TAGS = "animation_tags"
WW_ANIM_AUTHOR = "animation_author"

WW_STRUCT_FIELDS = [
    WW_ANIM_ACTORS,
    WW_ANIM_CATEGORY,
    WW_ANIM_LOCATIONS,
    WW_ANIM_TAGS,
]

# 从 WW XML 提取 animation_clip_name="..." 的值 (引用校验用)
_CLIP_REF_RE = re.compile(r'<T\s+n="animation_clip_name"[^>]*>\s*(.*?)\s*</T>', re.S)
# 脱衣舞变体: dancer_animation_clip_name
_DANCER_CLIP_REF_RE = re.compile(r'<T\s+n="dancer_animation_clip_name"[^>]*>\s*(.*?)\s*</T>', re.S)


def _has_clip(type_ids: set[int]) -> bool:
    """CLIP 类型是否已核实且存在。未核实则视为不可靠 → False (安全偏漏)。"""
    # 从类型集里找 CLIP: 只有当 CLIP 的 Type ID 已 VERIFIED 才算数
    clip_id = None
    for tid in type_ids:
        if RESOURCE_TYPES.is_clip(tid):
            clip_id = tid
            break
    # 如果 CLIP Type ID 尚未核实(全局), 无法确认 CLIP 存在 → 不据此判定
    # 这里依赖 is_clip() 的实现: 它只在 VERIFIED 时返回 True
    return clip_id is not None


def _has_world_xml_signal(type_ids: set[int], debug_to=None) -> bool:
    """是否存在 WW 动画 XML 类型 (SNIPPET/TUNING_XML/BINARY_XML, 均已核实才可信)。
    注意: 即使有这些 XML 类型, 仍需解析内容确认 WW 字段。此函数仅做类型级初筛。"""
    # 类型级: 看是否有已核实的 XML-ish 类型
    for tid in type_ids:
        if (RESOURCE_TYPES.is_snippet(tid)
                or RESOURCE_TYPES.is_tuning_xml(tid)
                or RESOURCE_TYPES.is_known_safely(tid, "WW_ANIM_XML")):
            return True
    # Binary XML 身份未核实, 不作为强信号
    return False


class Classifier:
    """分类引擎 — 纯逻辑, 不读写文件, 接收"资源类型摘要 + XML 文本内容"进行判定。"""

    def __init__(self):
        pass

    def classify_from_texts(
        self,
        type_ids: set[int],
        xml_texts: List[str],
        stbl_present: bool,
        clip_names: Optional[set] = None,
    ) -> Classification:
        """
        主分类入口。

        Args:
            type_ids: 该 package 的所有资源类型集合
            xml_texts: 提取到的 XML/Snippet 文本内容 (用于确认 WW 字段)
            stbl_present: 是否存在 STBL
            clip_names: 从 CLIP 资源尽力提取的 ClipName 集合 (可为 None/空)

        Returns:
            Classification
        """
        cls = Classification(level=ConfLevel.NON_ANIMATION)
        has_clip = _has_clip(type_ids)

        # ---- 提取 WW 相关信号 ----
        # 需要在 xml_texts 里找 animation_raw_display_name / animation_display_name
        ww_display_count = 0
        ww_struct_found = []
        ww_present = False
        for txt in xml_texts:
            c = txt.count(WW_ANIM_DISPLAY_RAW) + txt.count(WW_ANIM_DISPLAY_OLD)
            ww_display_count += c
            if c > 0:
                ww_present = True
            for f in WW_STRUCT_FIELDS:
                if f in txt and f not in ww_struct_found:
                    ww_struct_found.append(f)

        # 提取 XML 中的 animation_clip_name 值 (引用校验用, 含脱衣舞变体 dancer_animation_clip_name)
        xml_clip_refs = set()
        if clip_names is not None:
            for txt in xml_texts:
                for m in _CLIP_REF_RE.finditer(txt):
                    val = m.group(1).strip()
                    if val:
                        xml_clip_refs.add(val)
                for m in _DANCER_CLIP_REF_RE.finditer(txt):
                    val = m.group(1).strip()
                    if val:
                        xml_clip_refs.add(val)

        # 校验: XML 引用的 animation_clip_name 是否至少一个能匹配包内 CLIP 名
        clip_ref_verified = False
        if clip_names and xml_clip_refs:
            for ref in xml_clip_refs:
                if any(ref in cn or cn in ref for cn in clip_names):
                    clip_ref_verified = True
                    break

        # ---- Pose Pack 信号 (简化): 有 snippet + clip + stbl, 无 WW 字段 ----
        has_snippet_xml = _has_world_xml_signal(type_ids)

        # ===== 判定逻辑 =====
        # CONFIRMED_WW: WW 显示名 ≥1 + WW 结构字段 ≥1 + CLIP
        if ww_present and len(ww_struct_found) >= 1 and has_clip:
            cls.level = ConfLevel.CONFIRMED_WW
            cls.evidence = [WW_ANIM_DISPLAY_RAW, WW_ANIM_DISPLAY_OLD, *ww_struct_found, "CLIP"]
            # 若成功提取到 CLIP 名且 XML 引用可匹配, 记录为强佐证; 否则不影响 (尽量验证)
            if clip_ref_verified:
                cls.evidence.append("clip_ref_verified")
            if clip_names and not clip_ref_verified:
                # 提取到 CLIP 名但 XML 引用无法匹配 → 仍 CONFIRMED (WW XML 结构已足够强),
                # 但记录警告, 供人工复查 False Positive
                cls.evidence.append("clip_ref_WARN_no_match")
            cls.reason = "WW Animation XML (显示名) + WW 结构字段 + CLIP"
            return cls

        # WW 显示名存在但缺 CLIP 或结构 → UNCERTAIN
        if ww_present:
            cls.level = ConfLevel.UNCERTAIN
            cls.evidence = [WW_ANIM_DISPLAY_RAW, WW_ANIM_DISPLAY_OLD]
            if not has_clip:
                cls.missing.append("CLIP")
            if not ww_struct_found:
                cls.missing.append("WW 结构字段 (actors/category/locations/tags)")
            cls.reason = "有 WW 显示名但证据不足"
            return cls

        # CONFIRMED_POSE: 有 XML(Snippet) + CLIP + STBL, 且无 WW 字段
        if has_snippet_xml and has_clip and stbl_present:
            # 无 WW 字段 → 判为 Pose 候选
            cls.level = ConfLevel.CONFIRMED_POSE
            cls.evidence = ["Snippet/Tuning XML", "CLIP", "STBL"]
            cls.reason = "Pose Pack 结构: XML + CLIP + STBL, 无 WW 字段"
            return cls

        # 有 CLIP 但无 XML 佐证 → 可能是动画但不明确
        if has_clip:
            cls.level = ConfLevel.UNCERTAIN
            cls.evidence = ["CLIP"]
            cls.missing.append("明确 XML/Pose 结构")
            cls.reason = "有 CLIP 但无明确 Pose/WW 结构验证"
            return cls

        # 有 WW 结构字段但无显示名 / 无 CLIP → UNCERTAIN
        if ww_struct_found:
            cls.level = ConfLevel.UNCERTAIN
            cls.evidence = ww_struct_found
            cls.missing.append("animation_display_name")
            cls.missing.append("CLIP" if not has_clip else "")
            cls.reason = "有 WW 结构字段但无显示名/CLIP 佐证"
            return cls

        # 其他 → NON_ANIMATION
        cls.level = ConfLevel.NON_ANIMATION
        cls.reason = "非动画结构, 忽略"
        return cls


# 标准 XML 属性匹配 (非严格, 仅用于提取显示名)
_ATTR_RE = re.compile(r'<T\s+n="[^"]*animation_(raw_)?display_name[^"]*"[^>]*>(.*?)</T>', re.S)
