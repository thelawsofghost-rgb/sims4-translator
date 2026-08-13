#!/usr/bin/env python3
"""Fix 9 验证: 物件/位置槽位标签 + 语义不确定 20 条确定 + 回归。
把 phase2a_samples.py 读到 classify 定义之前, eval 出 classify() 做纯逻辑测试。"""
import os, re, importlib.util

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phase2a_samples.py")
src = open(SRC, encoding="utf-8").read()

# 截断到 "for r in rows:" (抽样/写CSV, 非分类逻辑) 之前; 并中和中间的 CSV 读取
marker = "for r in rows:"
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
    # --- 2026-08-13 第二轮 (结构规则 + 上下文优先, 不无限扩词库) ---
    # 用户确认: 物件/位置槽位 + 编号 -> NON_SEMANTIC_TAG
    ("NON_SEMANTIC_TAG", "Gaming chair 1"),
    ("NON_SEMANTIC_TAG", "container 1"),
    ("NON_SEMANTIC_TAG", "Tied up chair 2"),
    ("NON_SEMANTIC_TAG", "Tied up floor 1"),
    # 角色+编号
    ("NON_SEMANTIC_TAG", "boy 1"),
    # 纯性别/角色编号 (带数字语义名袋渗入项 -> 归位)
    ("NON_SEMANTIC_TAG", "05 Male"), ("NON_SEMANTIC_TAG", "07 v2 Male"),
    ("NON_SEMANTIC_TAG", "04 - Female"), ("NON_SEMANTIC_TAG", "Male 7-2"),
    ("NON_SEMANTIC_TAG", "8M EMPLOYEE"), ("NON_SEMANTIC_TAG", "Pose x12"),
    ("NON_SEMANTIC_TAG", "2b (animation) P.W acc"),
    # 语义不确定袋高确定性编号结构 -> KEEP
    ("NON_SEMANTIC_TAG", "03_02"), ("NON_SEMANTIC_TAG", "01_01"),
    ("NON_SEMANTIC_TAG", "left - 14"), ("NON_SEMANTIC_TAG", "right - 15"),
    ("NON_SEMANTIC_TAG", "sitting - 08"), ("NON_SEMANTIC_TAG", "standing - 01"),
    ("NON_SEMANTIC_TAG", "x_1"), ("NON_SEMANTIC_TAG", "y_2"),
    ("NON_SEMANTIC_TAG", "6 V2"), ("NON_SEMANTIC_TAG", "6 v.2"),
    ("NON_SEMANTIC_TAG", "[POSE 8]"), ("NON_SEMANTIC_TAG", "POSE 9-13"),
    ("NON_SEMANTIC_TAG", "4(move)"), ("NON_SEMANTIC_TAG", "2 F"),
    ("NON_SEMANTIC_TAG", "3 - M2"), ("NON_SEMANTIC_TAG", "Animation 1"),
    ("NON_SEMANTIC_TAG", "Animation 10"),
    # 自然语言形态 -> 语义 (构词法, 非白名单)
    ("ENGLISH_SEMANTIC", "Stretching"), ("ENGLISH_SEMANTIC", "Pacing"),
    ("ENGLISH_SEMANTIC", "Stressed"), ("ENGLISH_SEMANTIC", "Explaining"),
    ("ENGLISH_SEMANTIC", "Confessing"), ("ENGLISH_SEMANTIC", "Tripping"),
    ("ENGLISH_SEMANTIC", "Catastrophizing"), ("ENGLISH_SEMANTIC", "Playful"),
    # Solemn/Faye 无构词后缀、无 handle 特征, 靠上下文层判定 (见下) -> 不在结构层断言
    # 带数字语义名: 情绪/动作词+序号 -> SEMANTIC_WITH_NUM (结构: 幸存单/双词+数字)
    ("SEMANTIC_WITH_NUM", "Positive 3"), ("SEMANTIC_WITH_NUM", "Negative 7"),
    ("SEMANTIC_WITH_NUM", "Focused 2"), ("SEMANTIC_WITH_NUM", "7 - Sweet"),
    ("SEMANTIC_WITH_NUM", "15 reverence"),

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


# --- 上下文第二层判断测试 (用户要求: 不无限扩词库, 靠 neighbor_display_texts 判定) ---
# 生产路径是 classify_with_context(source, neighbors); 裸 classify 对无后缀单短词只会判 SEMANTIC_UNCERTAIN,
# 正是靠上下文解析到语义/专名。
print("== 上下文第二层判断 (classify_with_context) ==")
ctx_cases = [
    # (期望 decision, 期望 reason, source, neighbors)
    ("TRANSLATE", "SEMANTIC_UNCERTAIN->ENGLISH_SEMANTIC", "Walk",
     "Wave | Walk | Dance | Peace and anxiety | Happy | All in one"),
    ("TRANSLATE", "SEMANTIC_UNCERTAIN->ENGLISH_SEMANTIC", "Solemn",
     "Angry | Bored | Confused | Happy 1 | Happy 2 | Laughing | Solemn | Questioning"),
    ("KEEP", "NON_SEMANTIC_TAG", "2 F", "1 F | 1 M | 2 F | 2 M | 2 F V2"),
    ("KEEP", "NON_SEMANTIC_TAG", "boy 1", "boy 1 | boy 2 | boy 3 | boy 4"),
    ("KEEP", "NON_SEMANTIC_TAG", "Animation 1", "Animation 1 | Animation 2 | Animation 3 | Animation 4"),
    ("KEEP", "NON_SEMANTIC_TAG", "03_02", "03_01 | 03_02 | 03_03 | 03_04"),
    ("KEEP", "NON_SEMANTIC_TAG", "left - 14", "left - 01 | left - 02 | left - 03 | left - 08"),
    ("KEEP", "NON_SEMANTIC_TAG", "6 V2", "1 | 2 | 3 | 4 | 5 | 6 | 6 V2"),
    ("KEEP", "NON_SEMANTIC_TAG", "[POSE 8]", "[POSE 1] ver1 | [POSE 2] | [POSE 3] | [POSE 8]"),
    ("KEEP", "NON_SEMANTIC_TAG", "3 - M2", "3 - M1 | 3 - M2 | 4 - M1 | 4 - M2"),
    # Faye: 邻居全是人名 (首字母大写单短词) -> KEEP / PROPER_NAME
    ("KEEP", "PROPER_NAME", "Faye", "Isaac | Elliot | Faye | Skyler | Imani | Brooke"),
]
ctx_fails = 0
for expd, expr, src, neigh in ctx_cases:
    gd, gr = ns["classify_with_context"](src, neigh)
    ok = "OK " if gd == expd else "FAIL"
    if gd != expd:
        ctx_fails += 1
        fails += 1
    print(f"  [{ok}] 期望decision={expd:12} 实得={gd:12} (reason={gr}) <- {src} | 邻居: {neigh[:40]}")
print(f"\n上下文层结果: {'全部通过' if ctx_fails==0 else f'{ctx_fails} 条失败'}")

print(f"\n总计: {'全部通过' if fails==0 else f'{fails} 条失败'}  (结构 + 上下文 共 {len(cases)+len(regressions)+len(ctx_cases)} 条)")
sys_exit = 1 if fails else 0
raise SystemExit(sys_exit)
