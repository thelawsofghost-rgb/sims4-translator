#!/usr/bin/env python3
"""
Sims 4 Resource Type ID 集中映射表

设计原则 (对应项目规格修订点 3):
  - 禁止在核心分类代码中散落 magic numbers
  - 所有 Type ID 必须经过权威来源核实后才标记 VERIFIED
  - 未核实的 Type ID (UNVERIFIED) 不得参与判定, 涉及未知类型的资源走 ERROR/UNCERTAIN

状态约定:
  VERIFIED          = 已在权威来源确认 (Sims4Studio / s4pe-Sims4 / 官方 resource definitions)
  UNVERIFIED        = 尚未从权威来源核实, 不启用判定
  OBSOLETE_WARNING  = 该值在旧版工具(s4pi-Sims3)中有不同含义, 需特别小心

⚠️ 重要: 本表初始版本大部分为 UNVERIFIED。
    必须在拿到权威来源(Sims4Studio/s4pe Sims4版源码)后逐项核实,
    核实后再启用对应判定逻辑。核实时逐项标注来源。
"""

from typing import Dict, Optional


class ResourceType:
    """单个 Resource Type 定义"""

    __slots__ = ("id", "name", "verified", "source", "notes")

    def __init__(self, type_id: int, name: str, verified: bool = False,
                 source: str = "", notes: str = ""):
        self.id = type_id
        self.name = name
        self.verified = verified          # 是否已从权威来源核实
        self.source = source              # 核实来源 (repo/文件/链接)
        self.notes = notes


# ============================================================
# Type ID 表
# 当前所有值标注状态。只有 verified=True 的才允许进入判定。
# ============================================================

# 实测确认 (2026-08-12, WWLaserAnimations.package, 41 entries):
#   0x6B20C4F3  x8  = CLIP        (动画剪辑本体, 每个后面配一个 0xBC4A5044)
#   0xBC4A5044  x8  = RCOL/动画属性 (与 CLIP 配对的小资源 225~272 字节)
#   0x220557DA  x23 = STBL        (本地化显示文本)
#   0x7DF2169C  x1  = 待定 (疑似动画定义 XML/WW 结构化资源)
#   0x0166038C  x1  = 待定 (248 字节)

_RESOURCE_TYPES: Dict[int, ResourceType] = {
    # ---- 动画相关 ----
    0x6B20C4F3: ResourceType(
        0x6B20C4F3, "CLIP", verified=True,
        source="实测 WWLaserAnimations.package (2026-08-12)",
        notes="Sims4 动画剪辑 (Pose/Animation 本体)。每个 CLIP 后配一个 0xBC4A5044。\n"
                "⚠️ 旧注释误标 0x0354E541 为 CLIP, 实测为 0x6B20C4F3。",
    ),
    0xBC4A5044: ResourceType(
        0xBC4A5044, "ANIM_RCOL", verified=True,
        source="实测 WWLaserAnimations.package (2026-08-12)",
        notes="动画属性 RCOL, 与 CLIP 一对一配对 (225~272 字节小资源)。辅助信号。",
    ),
    0x7DF2169C: ResourceType(
        0x7DF2169C, "WW_ANIM_XML", verified=False,
        source="实测 WWLaserAnimations.package (2026-08-12)",
        notes="疑似 WW 动画定义资源 (含 animation_raw_display_name 等), 待核实内容。",
    ),
    # ---- 文本 ----
    0x220557DA: ResourceType(
        0x220557DA, "STBL", verified=True,
        source="s4pi/s4pe Import.cs", notes="String Table (本地化显示文本)。"
                "已在本地 s4pi 源码确认。",
    ),
    # ---- XML / Snippet / Tuning ----
    0x052FE820: ResourceType(
        0x052FE820, "SNIPPET", verified=False,
        source="", notes="Snippet (Pose Pack 等 XML 定义)。待核实。",
    ),
    0x0333406C: ResourceType(
        0x0333406C, "TUNING_XML", verified=True,
        source="s4pi TextResource", notes="XML Tuning。已在本地 s4pi 确认。",
    ),
    0x00B2D882: ResourceType(
        0x00B2D882, "BINARY_XML", verified=False,
        source="", notes="Binary XML 或 DDS?_ ⚠️ 旧版 s4pi 把 0x00B2D882 标为 _IMG(dds), "
                "但 Sims4 社区普遍认为这是 Binary XML。身份冲突, 判定前必须先核实!",
    ),
    0x545AC6A4: ResourceType(
        0x545AC6A4, "TTAB", verified=False,
        source="", notes="Interaction Tuning (TTAB)。待核实。",
    ),
    0x025C95B6: ResourceType(
        0x025C95B6, "XML_UI_LAYOUT", verified=True,
        source="s4pi/s4pe Import.cs", notes="XML: UI Layout。已在本地 s4pi 确认。",
    ),
    # ---- 图像 (浅扫描要跳过, 不读取 body) ----
    0x4D4D5A48: ResourceType(
        0x4D4D5A48, "DDS_IMAGE", verified=False,
        source="", notes="Sims4 DDS 纹理。待核实。",
    ),
    0x00AE4E07: ResourceType(
        0x00AE4E07, "TEX_IMAGE", verified=False,
        source="", notes="Sims4 _IMG 纹理。待核实。",
    ),
    # ---- 其他常见 Sims4 资源 ----
    0x73E93EEB: ResourceType(
        0x73E93EEB, "PACKAGE_MANIFEST", verified=False,
        source="", notes="Package manifest。s4pi-Sims3 中为 sims3pack manifest。Sims4 待核实。",
    ),
}


class _ResourceTypeRegistry:
    """类型注册表: 提供查询, 并强制只允许 VERIFIED 类型参与判定"""

    def __init__(self, types: Dict[int, ResourceType]):
        self._types = types

    def name_for(self, type_id: int) -> str:
        rt = self._types.get(type_id)
        return rt.name if rt else f"0x{type_id:08X}"

    def is_verified(self, type_id: int) -> bool:
        rt = self._types.get(type_id)
        return bool(rt and rt.verified)

    def get(self, type_id: int) -> Optional[ResourceType]:
        return self._types.get(type_id)

    def is_known_safely(self, type_id: int, name: str) -> bool:
        """仅当该名称对应的 Type ID 是 VERIFIED 时才返回 True。
        用于避免未核实的 magic number 被误用于判定。"""
        rt = self._types.get(type_id)
        return bool(rt and rt.verified and rt.name == name)

    # ---- 分类引擎使用的便捷判定 (全部只认 VERIFIED) ----
    def is_clip(self, type_id: int) -> bool:
        return self.is_known_safely(type_id, "CLIP")

    def is_stbl(self, type_id: int) -> bool:
        return self.is_known_safely(type_id, "STBL")

    def is_snippet(self, type_id: int) -> bool:
        return self.is_known_safely(type_id, "SNIPPET")

    def is_tuning_xml(self, type_id: int) -> bool:
        return self.is_known_safely(type_id, "TUNING_XML")

    def is_animation_xml(self, type_id: int) -> bool:
        """WW 动画 XML 可能以 Snippet 或 Tuning XML 或 Binary XML 形式存在。
        仅当这些类型已核实才判定, 否则视为不可靠(不参与判定)。"""
        return (self.is_snippet(type_id)
                or self.is_tuning_xml(type_id)
                or self.is_known_safely(type_id, "BINARY_XML"))


# 全局单例
RESOURCE_TYPES = _ResourceTypeRegistry(_RESOURCE_TYPES)


def list_verified_ids() -> str:
    """返回当前已核实类型的清单, 用于诊断输出"""
    out = []
    for tid, rt in sorted(_RESOURCE_TYPES.items()):
        mark = "✔" if rt.verified else "✖"
        s = f"  {mark} 0x{tid:08X} {rt.name}"
        if not rt.verified:
            s += f"  [未核实: {rt.notes}]"
        out.append(s)
    return "\n".join(out)


def verified_type_count() -> int:
    return sum(1 for rt in _RESOURCE_TYPES.values() if rt.verified)
