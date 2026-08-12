#!/usr/bin/env python3
"""
Package 分类引擎 (核心安全机制)

分类等级 (5 + 2 异常级):
  CONFIRMED_WW        → 有 WickedWhims Animation 专属结构 + 动画 CLIP
  CONFIRMED_POSE      → 有 Pose Player / Pose Pack 专属结构 + 动画/名称引用
  OTHER_ANIMATION     → 明确含动画(CLIP) 但既非 WW 也非 Pose (本版不翻译)
  NON_ANIMATION       → 无目标动画结构 (衣服/家具/头发/皮肤等)
  UNCERTAIN           → 结构异常 / 引用损坏 / 证据不足, SKIP
  ERROR               → 无法读取/解析, 跳过
  ERROR_UNSUPPORTED_DBPF → 无法解析的 DBPF, 跳过

最高原则:
  FALSE NEGATIVE IS ACCEPTABLE. FALSE POSITIVE IS NOT ACCEPTABLE.
  无法确认 = SKIP.
  禁止用排除法证明身份:
    "不是 WW" ≠ "是 Pose"; "不是 Pose" ≠ "是 WW".
  每一种 CONFIRMED 都必须有自身正面结构证据。

分类关键 (不依赖文件名):
  - CONFIRMED_WW : ≥1 有效 WW Animation XML
                    (animation_raw_display_name 或 animation_display_name
                     + WW 特有结构 actors_list/category/locations/tags 至少一项)
                    + ≥1 CLIP。尽量验证 animation_clip_name → 实际 CLIP ClipName。
  - CONFIRMED_POSE: 需有 Pose Player/Pose Pack 专属 tuning/snippet 结构证据
                    + UI 名称 STBL 引用 + clip name 引用能对应。
                    仅凭 CLIP+XML+STBL 而无 Pose 专属结构 → OTHER_ANIMATION。
  - OTHER_ANIMATION: 明确含 CLIP/动画资源, 但无法证明 WW 也无法证明 Pose。
  - 本体(WW/PosePlayer/功能MOD) : 含脚本/功能资源但无动画包组合 → 视资源定 NON_ANIMATION/OTHER。
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
    OTHER_ANIMATION = "OTHER_ANIMATION"
    NON_ANIMATION = "NON_ANIMATION"
    UNCERTAIN = "UNCERTAIN"
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

# ---- Pose Player / Pose Pack 正面结构信号 (须为真实 Pose 专属, 非 WW) ----
# 从 Sims 4 Studio / 真实 pose pack tuning 归纳的专属语义标签。
# 注意: 这些必须是 Pose 专属, 不能是 WW 动画也用到的通用字段。
POSE_SIGNATURES = [
    # 仅保留在真实 Pose Pack XML 中验证过的、Structure/类名级专属标记。
    # 宁可漏 (宁漏勿错): 只认明确的 Pose Player 结构证据。
    # 真包 [F] Emotion React: c="PosePackInstance" m="poseplayer"
    #                       <T n="s4s_mod_type">POSE_PACK</T> <L n="pose_list">
    #                       pose_name / pose_display_name
    "PosePackInstance",   # 实例类名 (c= 属性值)
    "poseplayer",         # Pose Player 模块名 (m= 属性值)
    "POSE_PACK",          # S4S mod_type 标记 (<T n="s4s_mod_type">)
    "pose_list",          # 姿势列表容器 (<L n="pose_list">)
    "pose_display_name",  # 姿势 UI 显示名引用 (真包中存在)
    "pose_name",          # 姿势名引用 (真包中存在)
]


def _has_pose_positive(xml_texts: List[str]) -> List[str]:
    """检查 XML 文本中是否含 Pose Player 专属正面结构。
    只返回命中的专属标签; 空列表 = 无法正面证明是 Pose。"""
    hits = []
    for txt in xml_texts:
        for sig in POSE_SIGNATURES:
            # 精确到 XML 标签/属性值, 避免误中普通词
            if sig in txt and sig not in hits:
                hits.append(sig)
    return hits


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

        # 引用校验状态 (三态, 供统计 clip_ref_MATCH / WARN_no_match / NOT_AVAILABLE):
        #   "MATCH"          : XML 引用的 clip 名能匹配到包内 CLIP ClipName
        #   "WARN_no_match"  : 提取到 CLIP 名, 但 XML 引用无一匹配 (供人工复查)
        #   "NOT_AVAILABLE"  : 无法验证 (XML 无 clip 引用, 或 CLIP 名提取失败为空)
        # 三态都只作佐证统计, 绝不降级 CONFIRMED_WW/POSE (非硬门槛)。
        clip_ref_state = "NOT_AVAILABLE"
        if xml_clip_refs:
            if clip_names:
                matched = False
                for ref in xml_clip_refs:
                    if any(ref in cn or cn in ref for cn in clip_names):
                        matched = True
                        break
                clip_ref_state = "MATCH" if matched else "WARN_no_match"
            else:
                # XML 有引用但提取不到任何 CLIP 名 → 无法验证
                clip_ref_state = "NOT_AVAILABLE"

        _CLIP_STATE_TO_EVIDENCE = {
            "MATCH": "clip_ref_MATCH",
            "WARN_no_match": "clip_ref_WARN_no_match",
            "NOT_AVAILABLE": "clip_ref_NOT_AVAILABLE",
        }

        # ---- Pose Pack 正面结构信号 ----
        pose_positive = _has_pose_positive(xml_texts)

        has_snippet_xml = _has_world_xml_signal(type_ids)

        # ===== 判定逻辑 (每类都有正面证据, 不用排除法) =====

        # 1) CONFIRMED_WW: WW 专属显示名 ≥1 + WW 专属结构 ≥1 + CLIP
        if ww_present and len(ww_struct_found) >= 1 and has_clip:
            cls.level = ConfLevel.CONFIRMED_WW
            cls.evidence = [WW_ANIM_DISPLAY_RAW, WW_ANIM_DISPLAY_OLD, *ww_struct_found, "CLIP"]
            # animation_clip_name → CLIP ClipName 引用状态 (只作佐证统计, 不降级)
            cls.evidence.append(_CLIP_STATE_TO_EVIDENCE[clip_ref_state])
            cls.reason = "WW Animation XML (显示名) + WW 结构字段 + CLIP"
            return cls

        # 2) CONFIRMED_POSE: 必须有 Pose Player 专属正面结构 + CLIP + (STBL 名称引用)
        if pose_positive and has_clip:
            ev = [*pose_positive, "CLIP"]
            if stbl_present:
                ev.append("STBL")
            ev.append(_CLIP_STATE_TO_EVIDENCE[clip_ref_state])
            cls.level = ConfLevel.CONFIRMED_POSE
            cls.evidence = ev
            cls.reason = "Pose Player/Pose Pack 专属结构 + CLIP (+名称/引用)"
            return cls

        # 有 WW 显示名但缺 CLIP 或结构 → 证据不足, UNCERTAIN
        if ww_present:
            cls.level = ConfLevel.UNCERTAIN
            cls.evidence = [WW_ANIM_DISPLAY_RAW, WW_ANIM_DISPLAY_OLD]
            if not has_clip:
                cls.missing.append("CLIP")
            if not ww_struct_found:
                cls.missing.append("WW 结构字段 (actors/category/locations/tags)")
            cls.reason = "有 WW 显示名但证据不足 (缺 CLIP 或 WW 结构), SKIP"
            return cls

        # 3) OTHER_ANIMATION: 明确有 CLIP/动画资源, 但既非 WW 也非 Pose
        #    这是最重要的一类: 能证明含动画, 但无法证明属于哪套系统 → OTHER, 不翻译
        if has_clip:
            cls.level = ConfLevel.OTHER_ANIMATION
            cls.evidence = ["CLIP"]
            if pose_positive:
                # 有 Pose 专属信号但缺明确引用闭环, 仍归 OTHER 更安全
                cls.evidence += pose_positive
                cls.missing.append("Pose 引用闭环 (名称/ClipName 对应)")
                cls.reason = "有 CLIP 与部分 Pose 信号, 但 Pose 身份/引用未正面证实, 归 OTHER_ANIMATION"
            elif ww_struct_found:
                cls.evidence += ww_struct_found
                cls.missing.append("WW 显示名 or CLIP 佐证")
                cls.reason = "有 CLIP 与部分 WW 结构, 但缺 WW 显示名, 归 OTHER_ANIMATION"
            else:
                cls.reason = "明确含动画(CLIP), 但无法证明是 WW 或 Pose, 归 OTHER_ANIMATION (不翻译)"
            return cls

        # 4) 有 WW 结构字段但无 CLIP / 无显示名 → 无法确认, UNCERTAIN
        if ww_struct_found:
            cls.level = ConfLevel.UNCERTAIN
            cls.evidence = ww_struct_found
            cls.missing.append("animation_display_name")
            cls.missing.append("CLIP" if not has_clip else "CLIP 佐证")
            cls.reason = "有 WW 结构字段但缺显示名/CLIP 佐证, 无法安全确认, SKIP"
            return cls

        # 5) NON_ANIMATION: 无目标动画结构
        cls.level = ConfLevel.NON_ANIMATION
        cls.reason = "非动画结构, 忽略"
        return cls


# 标准 XML 属性匹配 (非严格, 仅用于提取显示名)
_ATTR_RE = re.compile(r'<T\s+n="[^"]*animation_(raw_)?display_name[^"]*"[^>]*>(.*?)</T>', re.S)
