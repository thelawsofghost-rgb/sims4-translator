#!/usr/bin/env python3
"""
分类引擎冒烟测试 — 验证安全机制

核心验证点 (FALSE POSITIVE NOT ACCEPTABLE):
  1. WW 动画包在 CLIP/SNIPPET Type ID **未核实**时, 必须判 UNCERTAIN, 绝不能判 CONFIRMED_WW
  2. 普通 CC (有 STBL 无 CLIP) 必须 NON_ANIMATION/UNCERTAIN, 绝不误判
  3. 一旦 Type ID 核实, WW 动画包正确判 CONFIRMED_WW

运行: python scripts/smoke_test_classifier.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from classifier import Classifier, ConfLevel
from resource_types import RESOURCE_TYPES

# 模拟 "核实" 环境: 临时把 CLIP/SNIPPET 标记为 verified, 用于测试功能逻辑
# (不修改正式表, 只在本测试里模拟)
import resource_types as rt_mod


def enable_unverified_for_test():
    """在测试进程内临时把所有类型标记为 verified, 测试分类逻辑本身。"""
    for t in rt_mod._RESOURCE_TYPES.values():
        t.verified = True


clf = Classifier()
ww_xml = (
    '<T n="animation_raw_display_name">Standing Kiss</T>'
    '<T n="animation_author">Khlas</T>'
    '<T n="animation_actors_list">actor actor2</T>'
    '<T n="animation_category">KISSING</T>'
)

def run():
    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  ✅ {name}")
        else:
            failed += 1
            print(f"  ❌ {name} {detail}")

    print("[A] 安全模式 (未核实 CLIP/SNIPPET):")
    # WW 包虽有 WW 字段, 但因 CLIP 未核实 → UNCERTAIN (安全)
    cls = clf.classify_from_texts({0x0354E541, 0x220557DA, 0x052FE820}, [ww_xml], True)
    check("WW动画包(CLIP未核实) → 不判CONFIRMED_WW",
          cls.level != ConfLevel.CONFIRMED_WW, f"实际={cls.level}")
    check("WW动画包(CLIP未核实) → UNCERTAIN", cls.level == ConfLevel.UNCERTAIN, f"实际={cls.level}")

    # 普通 CC: 只有 STBL
    cls2 = clf.classify_from_texts({0x220557DA}, [], True)
    check("普通CC(仅STBL) → 不判动作包",
          cls2.level not in (ConfLevel.CONFIRMED_WW, ConfLevel.CONFIRMED_POSE), f"实际={cls2.level}")

    print("[B] 功能模式 (模拟已核实, 测试分类逻辑):")
    enable_unverified_for_test()

    cls3 = clf.classify_from_texts({0x0354E541, 0x220557DA, 0x052FE820}, [ww_xml], True)
    check("WW动画包(已核实) → CONFIRMED_WW", cls3.level == ConfLevel.CONFIRMED_WW, f"实际={cls3.level}")

    # 仅 CLIP 无结构 → UNCERTAIN
    cls4 = clf.classify_from_texts({0x0354E541}, [], False)
    check("仅CLIP → UNCERTAIN", cls4.level == ConfLevel.UNCERTAIN, f"实际={cls4.level}")

    # Pose 包: XML + CLIP + STBL, 无 WW 字段
    pose_xml = '<T n="pose_name">Sitting</T>'
    cls5 = clf.classify_from_texts({0x052FE820, 0x0354E541, 0x220557DA}, [pose_xml], True)
    check("Pose包(已核实) → CONFIRMED_POSE", cls5.level == ConfLevel.CONFIRMED_POSE, f"实际={cls5.level}")

    # 普通 CC (有 STBL + 普通 XML, 无 CLIP) → 不误判
    cls6 = clf.classify_from_texts({0x220557DA, 0x0333406C}, ['<T n="x">Shirt</T>'], True)
    check("普通CC(有XML+STBL无CLIP) → 非动作包",
          cls6.level not in (ConfLevel.CONFIRMED_WW, ConfLevel.CONFIRMED_POSE), f"实际={cls6.level}")

    print()
    print(f"结果: 通过 {passed}, 失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
