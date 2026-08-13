#!/usr/bin/env python3
"""Fix 9 验证: 物件/位置槽位标签 + 语义不确定 20 条确定 + 回归。
把 phase2a_samples.py 读到 classify 定义之前, eval 出 classify() 做纯逻辑测试。"""
import os, re, importlib.util

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phase2a_samples.py")
src = open(SRC, encoding="utf-8").read()

# 截断到 "打分类" (抽样/写CSV, 非分类逻辑) 之前; 并中和中间的 CSV 读取
marker = "# 打分类"
idx = src.index(marker)
classify_src = src[:idx]
# 中和 src[:idx] 中的 CSV 读取段 (避免 FileNotFoundError)
import re as _re
classify_src = _re.sub(
    r"^out_dir = .*?^# ---------------- 轻量分类 ",
    "_DUMMY_CAND = []\n# ---------------- 轻量分类 ",
    classify_src, flags=_re.M | _re.S)

ns = {}
exec(compile(classify_src, SRC, "exec"), ns)
classify = ns["classify"]

cases = [
    # (期望, 文本)
    # --- 本轮 20 条 专名/作者 -> NON_SEMANTIC_TAG (Liock 家具槽位) ---
    ("NON_SEMANTIC_TAG", "stool1"), ("NON_SEMANTIC_TAG", "stool6"),
    ("NON_SEMANTIC_TAG", "armchair1"), ("NON_SEMANTIC_TAG", "armchair3"),
    ("NON_SEMANTIC_TAG", "chair1"), ("NON_SEMANTIC_TAG", "chair5"),
    ("NON_SEMANTIC_TAG", "bar1"), ("NON_SEMANTIC_TAG", "bar3"),
    ("NON_SEMANTIC_TAG", "kitchencounter1"), ("NON_SEMANTIC_TAG", "kitchencounter2"),
    ("NON_SEMANTIC_TAG", "doorarch1"),
    # --- 语义不确定 20 条 全部确定 ---
    # SEMANTIC_WITH_NUM
    ("SEMANTIC_WITH_NUM", "10 - Gasp"), ("SEMANTIC_WITH_NUM", "7 - Conversational"),
    ("SEMANTIC_WITH_NUM", "10 - Standing"), ("SEMANTIC_WITH_NUM", "1 - Conversational"),
    ("SEMANTIC_WITH_NUM", "3 - Gasp"), ("SEMANTIC_WITH_NUM", "6 - Calm"),
    # ENGLISH_SEMANTIC
    ("ENGLISH_SEMANTIC", "Teleporter"), ("ENGLISH_SEMANTIC", "TELEPORTER"),
    ("ENGLISH_SEMANTIC", "Couple"), ("ENGLISH_SEMANTIC", "Deadpan"),
    ("ENGLISH_SEMANTIC", "Uncertain"), ("ENGLISH_SEMANTIC", "Rambling"),
    ("ENGLISH_SEMANTIC", "Arrogant"),
    # NON_SEMANTIC_TAG
    ("NON_SEMANTIC_TAG", "2-1"), ("NON_SEMANTIC_TAG", "4-1"), ("NON_SEMANTIC_TAG", "4-2"),
    # TECHNICAL_LABEL
    ("TECHNICAL_LABEL", "IntroNPC"), ("TECHNICAL_LABEL", "IntroObject"),
    ("TECHNICAL_LABEL", "LoopNPC"), ("TECHNICAL_LABEL", "LoopObject"),
]

regressions = [
    # --- 关键回归: 绝不能被误伤 ---
    ("SEMANTIC_WITH_NUM", "Rescue 7"),        # 有空格, 展示结构 -> 语义
    ("PROPER_NAME", "t0nischwartz"),          # 作者 handle
    ("PROPER_NAME", "Simmerianne93"),         # 作者 handle
    ("SEMANTIC_WITH_NUM", "kiss2"),           # kiss ∈ 语义词表
    ("SEMANTIC_WITH_NUM", "Bed 2 - Kissing Belly"),
    ("SEMANTIC_WITH_NUM", "Couple Pose 2"),   # 有空格 -> 语义
    ("SEMANTIC_WITH_NUM", "Pose 1 (Larger Breasts)"),
    ("NON_SEMANTIC_TAG", "standing1"), ("NON_SEMANTIC_TAG", "laying3"),
    ("NON_SEMANTIC_TAG", "2/ F"), ("NON_SEMANTIC_TAG", "3/ M"),
    ("NON_SEMANTIC_TAG", "F2-1A"), ("NON_SEMANTIC_TAG", "1.F"), ("NON_SEMANTIC_TAG", "01M-12M"),
    ("NON_SEMANTIC_TAG", "useF1"), ("NON_SEMANTIC_TAG", "3M"), ("NON_SEMANTIC_TAG", "4F"),
    ("NON_SEMANTIC_TAG", "2.1"), ("NON_SEMANTIC_TAG", "3.1"),
    ("ENGLISH_SEMANTIC", "Left"), ("ENGLISH_SEMANTIC", "Right"),
    ("ENGLISH_SEMANTIC", "Flirty"), ("ENGLISH_SEMANTIC", "Wink"), ("ENGLISH_SEMANTIC", "Dad"),
    ("ENGLISH_SEMANTIC", "ALL IN ONE"),
    ("TECHNICAL_LABEL", "sad female a2o_listen_music_START_seated_x"),
    ("SEMANTIC_WITH_NUM", "12 - Sass"), ("SEMANTIC_WITH_NUM", "15 - Inspecting"),
    ("SEMANTIC_WITH_NUM", "7 - Cower"), ("SEMANTIC_WITH_NUM", "14 - Scream"),
    ("SEMANTIC_WITH_NUM", "10 - Uncomfortable"), ("SEMANTIC_WITH_NUM", "5 - Disappointment"),
    ("SEMANTIC_WITH_NUM", "6 - Surprise"), ("SEMANTIC_WITH_NUM", "2 - Doubt"),
    ("SEMANTIC_WITH_NUM", "Lamp Post 2 - Tease"),
    ("SEMANTIC_WITH_NUM", "Door Kiss 2 - Kicking Open The Door"),
    ("ENGLISH_SEMANTIC", "Sitting looking back at the person coming down the aisle"),
]

fails = 0
print("== 本轮 36 条 ==")
for exp, txt in cases + regressions:
    got = classify(txt)
    ok = "OK " if got == exp else "FAIL"
    if got != exp:
        fails += 1
    print(f"  [{ok}] 期望={exp:20} 实得={got:20}  <- {txt}")

print(f"\n结果: {'全部通过' if fails==0 else f'{fails} 条失败'}  (共 {len(cases)+len(regressions)} 条)")
sys_exit = 1 if fails else 0
raise SystemExit(sys_exit)
