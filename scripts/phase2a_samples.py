#!/usr/bin/env python3
"""Phase 2A 前置: 100 条代表性样本抽取 + 轻量文本类型分类 (只读, 不写 package)。

目的: 正式批量生成 translations.csv 前, 先审 100 条样本文风与分类是否符合预期。

分类 (轻量启发式, 供人工确认; 作者/角色/品牌名默认保留不硬翻):
  ENGLISH_SEMANTIC    可读英文短语/姿势描述 (送翻译)
  SEMANTIC_WITH_NUM   带数字但含真实语义 (翻文字, 保序号; e.g. "4 - Arms Crossed")
  NON_SEMANTIC_TAG    编号/角色/变体标签 非语义 (e.g. "1" "2.1" "F2" "3M" "Pose 1" "Female 2" "1-A") 默认不译
  TECHNICAL_LABEL     技术内部标识 (a2o_/loopN/START/STOP/_seated_x/蛇纹) 默认不译
  PROPER_NAME         强势专名 (作者 handle / 角色 / 品牌 / 账号格式) 保留不译
  SYMBOL_OR_MIXED     符号/混合/难以归类
  SEMANTIC_UNCERTAIN  拿不准是否专名→标此, 不硬猜 (宁可不确定, 不误跳 PROPER_NAME)
  NON_ENGLISH         非英文 (德语/法语/韩文/日文等)

2026-08-13 第二轮 (decision/reason 分离 + 上下文第二层):
  - 每个候选输出 decision (TRANSLATE/KEEP/REVIEW) + reason (SEMANTIC/SEMANTIC_WITH_NUM/
    PROPER_NAME/NON_SEMANTIC_TAG/TECHNICAL_LABEL/...)。llama1 与 Faye 都是 KEEP(不译)
    但 reason 必须不同 (llama1=>NON_SEMANTIC_TAG; Faye=>PROPER_NAME)。
  - 不再无限扩 _SEM_WORD 白名单: 优先结构规则 (编号/性别/版本/槽位/方向+序号/下划线编号/
    方括号/括号注解/构词法后缀)。剩余单短词拿不准时, 用 neighbor_display_texts 上下文层判定
    (邻居全是语义词->语义; 全是编号->不译; 全是人名->PROPER_NAME)。

translation_id 预览: candidate 表先给 source_text+context 分组示意,
  正式 translations.csv 将用 translation_id (source_text+context_group) 承载拆分。

输入: output/pose_translation_candidates.csv (3567 语义候选, 列:
       source_text, ref_count, unique_keys, sample_package, sample_pose_pack,
       sample_stbl_instance, sample_locale, sample_neighbor_poses, sample_neighbor_display_texts)
输出: output/translation_samples_100.csv
"""
import sys, csv, re
from pathlib import Path
from collections import Counter, defaultdict

out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "D:/projects/sims4_trans/output")
cand_csv = out_dir / "pose_translation_candidates.csv"

rows = []
with open(cand_csv, encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        rows.append(r)
print(f"候选总数: {len(rows)}")
# ---- 诊断: 候选表上下文列覆盖 ----
for col in ["sample_pose_pack", "sample_stbl_instance", "sample_neighbor_poses", "sample_locale"]:
    n = sum(1 for r in rows if (r.get(col) or "").strip())
    print(f"[诊断] 候选表 {col:24} 非空 = {n}/{len(rows)}")

# ---------------- 轻量分类 ----------------
def is_cjk(s):  return any('\u4e00' <= c <= '\u9fff' for c in s)
def is_kr(s):   return any('\uac00' <= c <= '\ud7af' or '\u3130' <= c <= '\u318f' for c in s)
def is_jp(s):   return any('\u3040' <= c <= '\u30ff' for c in s)
CJK_REDUCE = str.maketrans("", "", "\u4e00-\u9fff\uac00-\ud7af\u3040-\u30ff\u3000-\u303f")
def ascii_alpha(s): return re.sub(r"[^A-Za-z]", "", s)

# 作者/角色名常见特征: 姓名大小写 (PascalCase 多词), 含 . 或 ' 或 &, 无小写动词短语
_KNOWN_AUTHOR_TOKENS = {"sims","sim","pose","poser","studio","cc","mod","by","the","and","of","for",
                        "meme","pack","collection","vol","set","part","v","mvp","tutorial"}

# ---------------- 新分类规则 (2026-08-12 重构, 用户四方向确认后) ----------------
# 目标类别: ENGLISH_SEMANTIC / SEMANTIC_WITH_NUM / NON_SEMANTIC_TAG /
#           PROPER_NAME / SYMBOL_OR_MIXED / SEMANTIC_UNCERTAIN (另有 NON_ENGLISH)

# 常见英文功能词/介词 -> 有功能词=语义文本
_FUNC = {"a","an","the","on","off","in","out","up","down","to","of","for","with",
         "by","and","or","at","from","over","under","into","onto","as","is","be",
         "while","when","after","before","during","her","his","my","your","are","was","not"}

# 位置/方向/动作短词白名单: 一定是语义
_DIR_SEM = {"left","right","top","bottom","front","back","up","down","sit","stood","stand",
            "kneel","lay","lie","lying","sitting","standing","in","out","on","over","under"}

# 姿势/成人相关强语义词: 命中即语义(即便标题大小写/单短词)
_SEM_WORD = {"kiss","kissing","kicked","kicking","open","opening","door","wall","bed","stairs","stair",
             "belly","breasts","breast","tease","teasing","massage","flirty","sass","couch","love",
             "ride","riding","clean","drunk","confession","goth","emotion","argue","argument",
             "listen","listening","music","sad","male","female","punch","pulling","dance","dancing",
             "thrust","oral","nude","strip","orgasm","lick","rub","grind","spank",
             "happy","angry","sad","normal","mum","mom","thinking","think","crossed","arms",
             "dad","father","son","daughter","wife","husband",
             "wink","smirk","woman","teen","child","irritated","cocky","confused","nervous",
             "unsure","talking","talk","injured","hurt","sleep","sleeping","cry","crying",
             "laugh","laughing","smile","smiling","shy","scared","afraid","proud","excited",
             "tired","bored","surprised","shocked","worried","embarrassed",
             "shrug","inspect","inspecting","cower","uncomfortable","scream","shout",
             "doubt","disappointment","surprise","rescue","nervous","squeeze","pinching",
             "balled","fists","fist","peering","peek","pointing","pointed","lean","leaning",
             # 2026-08-13 新增确定语义单短词 (从 SEMANTIC_UNCERTAIN 归位)
             "deadpan","uncertain","rambling","arrogant","couple","teleporter","gasp",
             "conversational","calm","standing"}

# ---- 编号/角色/变体标签 -> NON_SEMANTIC_TAG (默认不译) ----
# 纯数字 / 小数: 1 2.1
_TAG_PURE_NUM = re.compile(r"^\d+(?:\.\d+)?$")
# 数字+单字母变体(不限 M/F): 3-M 1-F 2-A 1-B 4-M 1-A
_TAG_NUM_DASH_LETTER = re.compile(r"^\d+-[A-Za-z]$")
# 单字母+数字: F2 3M 1F M2 2b m1 f4 (字母开头的作者编号体系)
_TAG_LETTER_NUM = re.compile(r"^[A-Za-z]\d+$")
# 数字+字母(无空格, 短): 1A 3A 2B 6F 2b 8F
_TAG_NUM_LETTER = re.compile(r"^\d+[A-Za-z]$")
# Pose N / PoseN
_TAG_POSE = re.compile(r"^(?:pose\s*)?\d+$", re.I)
# 角色+数字: Female 1~6 / Male 1~6 / Pose 4 Female / Female 2
_TAG_FEMALE_MALE_NUM = re.compile(r"^(?:female|male)\s+\d+$", re.I)
_TAG_ROLE_POS_NUM = re.compile(r"^pose\s+\d+\s+female$|^pose\s+\d+\s+male$", re.I)
# m / f 单个字母 (性别标签)
_TAG_MF = re.compile(r"^[mf]$", re.I)
# 角色+数字带性别字母: Pose 1F / Pose 8M (数字紧跟性别字母)
_TAG_POSE_NUM_MF = re.compile(r"^pose\s*\d+[a-z]$", re.I)
# 编号连字符+数字: F1-2 / 1-F1 / 2-F1 (字母+数字-数字 或 数字-字母数字)
_TAG_LETNUM_DASH = re.compile(r"^(?:[A-Za-z]+\d+-\d+|\d+-[A-Za-z]+\d+)$")
# NvN 版本标签: 3v2 / 1v1 / 2v3 (数字+单个字母+数字)
_TAG_NUM_V_NUM = re.compile(r"^\d+[a-z]\d+$", re.I)
# Femme_N / Homme_N (法文 性别+编号, 带或不带下划线/空格): Femme_1 Homme_3 Femme_3
_TAG_FEMME_HOMME = re.compile(r"^(?:femme|homme)\s*_?\s*\d+$", re.I)
# 数字/斜杠/字母 角色编号: 2/F 3/F 5/M 6/M (数字/单字母)
_TAG_NUM_SLASH_LETTER = re.compile(r"^\d+/[A-Za-z]$")
# 数字/空格斜杠/字母 角色编号: 2/ F 3/ M (数字+空格+斜杠隔空格+字母)
_TAG_NUM_SPACE_SLASH_LETTER = re.compile(r"^\d+\s*/\s*[A-Za-z]$")
# 补充: 纯数字-数字编号: 2-1 / 4-1 / 4-2 (作者槽位编码, 无字母无语义)
_TAG_NUM_DASH_NUM = re.compile(r"^\d+-\d+$")
# 方向/位置/姿态词 + 连字符 + 纯编号: left-14 / right-15 / sitting-08 / standing-01
#   (作者"方向/部位 + 序号"体系, 无实际语义标题; 区别于 "10 - Standing" 有空格=语义)
_TAG_DIR_DASH_NUM = re.compile(r"^(?:left|right|sitting|standing|sit|stand|front|back|side|lying|laying|kneeling|kneel|crouch|crouching|squat|squatting|top|bottom|walking|walk|running|run)\s*-\s*\d{1,2}$", re.I)
# 下划线编号: 03_02 / 04_01 (NN_NN, 纯数字下划线编号)
_TAG_NUM_UNDERSCORE_NUM = re.compile(r"^\d{1,2}_\d{1,2}$")
# 坐标/变量轴编号: x_1 / y_2 / x_3 (字母+下划线+数字, 坐标轴)
_TAG_AXIS_UNDERSCORE_NUM = re.compile(r"^[A-Za-z]{1,3}_\d{1,2}$")
# 版本标签 N V2 / N v.2 / N v2: 数字 + 空格/V 变体
_TAG_NUM_V_SPACE = re.compile(r"^\d{1,2}\s*(?:v\.?|ver\.?|version)\s*\d{1,2}$", re.I)
# [POSE N] 方括号编号: [POSE 8]
_TAG_BRACKET_POSE = re.compile(r"^\s*\[\s*pose\s*\d+\s*\]\s*$", re.I)
# POSE N-M 范围: POSE 9-13 / POSE 1-6
_TAG_POSE_DASH_RANGE = re.compile(r"^pose\s*\d{1,2}-\d{1,2}$", re.I)
# 数字(括号)注释标签: 4(move) / 5(move) / 6(move)
_TAG_NUM_PAREN_ANNOT = re.compile(r"^\d{1,2}\s*\([A-Za-z]{1,12}\)\s*$")
# 数字+空格+性别字母: 2 F / 1 M / 3 F (单数字+性别, 作者角色编号; 区别于 2 pose: woman 1 有语义)
_TAG_NUM_SPACE_MF = re.compile(r"^\d{1,2}\s+[FM]$", re.I)
# 数字-单字母+数字 变体: 3 - M2 / 4 - M1 (编号-性别版本)
_TAG_NUM_DASH_MF_NUM = re.compile(r"^\d{1,2}\s*-\s*[FM]\d{1,2}$", re.I)
# 数字+空格+性别+数字: 7 А2 含 Cyrillic 变体已由 NUM_DASH 覆盖; 2 F V2 也由 V 规则处理

# Animation N / Animation NN: 动画序号标题 (Goodnight Animation Pack)
_TAG_ANIMATION_NUM = re.compile(r"^animation\s*\d+$", re.I)
# 纯性别单词+数字 (无空格): Female3 / Male2 (作者"性别+序号"编号)
_TAG_MFWORD_NUM = re.compile(r"^(?:female|male|woman|man|boy|girl|child|infant|baby|teen|adult|senior|elder)\d+$", re.I)
# "<性别词> N" 无 more 语义 -> 编号 (Male 7-2 已由 dash 规则覆盖; Male 2 / Female 1)
_TAG_MFWORD_SPACE_NUM = re.compile(r"^(?:female|male|woman|man|boy|girl|child|infant|baby|teen|adult|senior|elder)\s+\d{1,2}$", re.I)
# "<性别词> N-N" 角色+双编号: Male 7-2 / Female 3-1 / Female 2-1
_TAG_MFWORD_DASH_NUM = re.compile(r"^(?:female|male|woman|man|boy|girl|child|infant|baby|teen|adult|senior|elder)\s*\d{1,2}\s*-\s*\d{1,2}$", re.I)
# 编号+字母+(注解) 动画/变体标注: 2b (animation) P.W acc / 2a (static)
_TAG_NUM_LET_PAREN_ANNOT = re.compile(r"^\d{1,2}[A-Za-z]?\s*\([A-Za-z ]{1,15}\)", re.I)
# "N v2 <性别>" / "07 v2 Male" / "05 Male" (数字+版本+性别, 无语义)
_TAG_NUM_V_MF = re.compile(r"^\d{1,2}\s*(?:v\.?\d{1,2})?\s+(?:female|male|woman|man|boy|girl|child|infant|baby|teen)$", re.I)
# "N - <性别>" 纯角色编号: 04 - Female / 05 - Male
_TAG_NUM_DASH_MF = re.compile(r"^\d{1,2}\s*-\s*(?:female|male|woman|man|boy|girl)$", re.I)
# 数字+性别字母+空格+角色词: 8M EMPLOYEE / 8F BOSS (角色编号+角色身份)
_TAG_NUM_MF_ROLE = re.compile(r"^\d{1,2}[FM]\s+[A-Za-z'\s]{2,15}$", re.I)
# 字母+数字+组合编号: Pose x12 / Pose x1 (字母 x + 数字, 坐标/变体)
_TAG_POSE_X_NUM = re.compile(r"^pose\s*x\d{1,2}$", re.I)

# 字母+数字-数字+字母 变体: F2-1A / F2-1B (字母编号-子变体)
_TAG_LETNUM_DASH_LET = re.compile(r"^[A-Za-z]+\d+-\d+[A-Za-z]$")
# 数字.单字母 性别变体: 1.F 1.M 2.F 2.M
_TAG_NUM_DOT_LETTER = re.compile(r"^\d+\.[A-Za-z]$")
# 补零两位编号+性别-范围: 01M-12M / 01F-12F / 02F-03F
_TAG_RANGE_MF = re.compile(r"^\d{1,2}[A-Za-z]-\d{1,2}[A-Za-z]$")
# use+F/M+数字 开关变体: useF1 useM1 (作者角色开关)
_TAG_USE_MF_NUM = re.compile(r"^use[FM]\d+$", re.I)

# ---- 技术内部标识 -> TECHNICAL_LABEL (默认不译) ----
# 特征: a2o_ / 大量下划线 / START|STOP / loopN / _seated_x / 蛇纹命名
_TECH_LABEL = re.compile(
    r"^(?=.*[A-Za-z])(?=.*\d).*(?:^|_)(?:a2o_|loop\d|start|stop|seated|standing|lying)(?:_|$)",
    re.I)
_TECH_HEAVY_UNDERSCORE = (
    lambda t: t.count("_") >= 2 and bool(re.search(r"\d", t))
)

# 功能 MOD 阶段/状态命名: Intro/Loop + NPC/Object (Brainwashing Machine, 无数字无下划线)
_TECH_STAGE = re.compile(r"^(?:intro|loop)(?:npc|object)$", re.I)

# 账号/作者 handle 强证据: 含数字字母混合且无空格的笔名 (t0nischwartz/Simmerianne93)
_HANDLE_LIKE = re.compile(r"^(?=.*[0-9])(?=.*[A-Za-z])[A-Za-z0-9_.-]{1,20}$")


# ---- 一般性「物件/位置槽位标签」规则 (2026-08-13) ----
# 不是逐词白名单, 而是: 通用物件/位置语义域词干 + 紧贴数字(+可选性别字母) -> 作者编号标签
# 覆盖: stool1~6 armchair1~3 chair1~5 bar1~3 kitchencounter1~2 doorarch1 standing1 laying3
# 必须带数字且紧贴 (无空格)。"Rescue 7" 有空格是展示结构 -> 语义。Teleporter/Chair 无数字 -> 不触发。
# 词干仅限通用物件/位置/身体槽位语义域, 不含情绪/动作词 (那些即便 short 也仍是语义)。
_SLOT_STEMS = {
    # 家具/台面
    "stool", "armchair", "chair", "bar", "desk", "table", "bench", "sofa", "couch",
    "counter", "kitchencounter", "shelf", "shelves", "cabinet", "dresser", "wardrobe",
    "closet", "bed", "crib", "cradle", "seat", "cushion", "pillow", "mattress",
    # 建筑/位置
    "door", "doorarch", "doorway", "window", "wall", "floor", "ceiling", "stair",
    "stairs", "ladder", "railing", "balcony", "porch", "patio", "roof", "garden",
    "yard", "fence", "gate", "pool", "shower", "bathtub", "sink", "toilet", "tile",
    # 身体/动作槽位
    "arm", "leg", "head", "foot", "hand", "knee", "hip", "shoulder", "neck",
    "waist", "chest", "back", "lap", "ground", "floor", "side", "front", "chairside",
    # 2026-08-13 用户确认: 物件/容器/主题槽位 + 编号 -> NON_SEMANTIC_TAG (非逐词白名单语义, 而是槽位体系)
    "gaming", "picnic", "llama", "container", "box", "crate", "basket", "bin", "tub",
    "cupboard", "fridge", "oven", "stove", "washer", "dryer", "armoire", "hutch",
    # 站/坐/躺 体位
    "standing", "sitting", "laying", "lying", "kneeling", "crouching", "squatting",
    "kneel", "crouch", "squat", "lyingdown", "standingup", "sitdown", "lean",
}
# 词干+紧贴数字(+可选单个性别/变体字母结尾, 如 stool1 / stool1f / bar2m)
_TAG_SLOT_NUM = re.compile(r"^([A-Za-z]+)(\d+)(?:[A-Za-z])?$")
# 多词物件/位置槽位 + 数字结尾: "Gaming chair 1" / "Tied up chair 2" / "Tied up floor 1"
#   规则: 末尾是紧贴编号, 且前面词干全部属于物件/位置/体位语义域 -> 槽位编号
#   (区别于 "Rescue 7" 有空格但 Rescue 是动作语义词)。"Tied up chair 2" 含动作词 tied, 但
#   "up chair N/foor N" 是明确槽位, 由用户确认归 NON_SEMANTIC_TAG。
_TAG_SLOT_NUM_MULTIWORD = re.compile(r"^(?P<words>[A-Za-z][A-Za-z ]*[A-Za-z])\s+(?P<num>\d{1,2})$")


_SLOT_DOMAIN_WORDS = ("gaming", "chair", "stool", "floor", "bed", "table", "desk", "sofa",
                      "couch", "counter", "shelf", "cabinet", "door", "window", "wall",
                      "stair", "stairs", "bench", "seat", "pool", "tub", "sink", "toilet",
                      "shower", "ground", "side", "front", "back", "top", "lap", "kneel",
                      "kneeling", "crouch", "crouching", "squat", "tied", "up", "down",
                      "lying", "laying", "sitting", "standing", "container", "box", "crate")


def _is_slot_label_mult(iw: list[str]) -> bool:
    """多词槽位标签: 末尾紧贴数字, 且词干全在槽位/体位域 (含可忽略的功能词)。
    仅当原始串形如 '<槽位词...> N' (空格分隔末尾数字) 时启用, 避免误伤纯语义词组。"""
    if not iw:
        return False
    func_ok = {"a", "an", "the", "of", "on", "in", "at", "for", "and", "with"}
    # 展平所有非功能词, 任一不在槽位域 -> 不是槽位标签
    sig = [w.lower() for w in iw if w.lower() not in func_ok and w.lower() not in _FUNC]
    if not sig:
        return False
    return all(w in _SLOT_DOMAIN_WORDS for w in sig)


def _is_slot_label(t: str) -> bool:
    """物件/位置词干 + 紧贴数字 -> 作者编号标签 (NON_SEMANTIC_TAG)。"""
    m = _TAG_SLOT_NUM.match(t.strip())
    if not m:
        return False
    stem = m.group(1).lower()
    return stem in _SLOT_STEMS or stem.rstrip("s") in _SLOT_STEMS


def _is_non_semantic_tag(t: str) -> bool:
    tt = t.strip()
    return bool(_TAG_PURE_NUM.match(tt) or _TAG_NUM_DASH_LETTER.match(tt)
                or _TAG_LETTER_NUM.match(tt) or _TAG_NUM_LETTER.match(tt)
                or _TAG_POSE.match(tt) or _TAG_FEMALE_MALE_NUM.match(tt)
                or _TAG_ROLE_POS_NUM.match(tt) or _TAG_MF.match(tt)
                or _TAG_POSE_NUM_MF.match(tt) or _TAG_LETNUM_DASH.match(tt)
                or _TAG_NUM_V_NUM.match(tt) or _TAG_FEMME_HOMME.match(tt)
                or _TAG_NUM_SLASH_LETTER.match(tt)
                or _TAG_NUM_SPACE_SLASH_LETTER.match(tt)
                or _TAG_LETNUM_DASH_LET.match(tt) or _TAG_NUM_DOT_LETTER.match(tt)
                or _TAG_RANGE_MF.match(tt) or _TAG_USE_MF_NUM.match(tt)
                or _TAG_NUM_DASH_NUM.match(tt)
                or _TAG_DIR_DASH_NUM.match(tt) or _TAG_NUM_UNDERSCORE_NUM.match(tt)
                or _TAG_AXIS_UNDERSCORE_NUM.match(tt) or _TAG_NUM_V_SPACE.match(tt)
                or _TAG_BRACKET_POSE.match(tt) or _TAG_POSE_DASH_RANGE.match(tt)
                or _TAG_NUM_PAREN_ANNOT.match(tt) or _TAG_NUM_SPACE_MF.match(tt)
                or _TAG_NUM_DASH_MF_NUM.match(tt) or _TAG_ANIMATION_NUM.match(tt)
                or _TAG_MFWORD_NUM.match(tt) or _TAG_MFWORD_SPACE_NUM.match(tt)
                or _TAG_MFWORD_DASH_NUM.match(tt) or _TAG_NUM_LET_PAREN_ANNOT.match(tt)
                or _TAG_NUM_V_MF.match(tt) or _TAG_NUM_DASH_MF.match(tt)
                or _TAG_NUM_MF_ROLE.match(tt) or _TAG_POSE_X_NUM.match(tt)
                or _is_slot_label(tt)
                or (_TAG_SLOT_NUM_MULTIWORD.match(tt)
                    and _is_slot_label_mult(re.findall(r"[A-Za-z]+", tt))))


def _is_technical_label(t: str) -> bool:
    tt = t.strip()
    if _TECH_LABEL.match(tt):
        return True
    if _TECH_HEAVY_UNDERSCORE(tt):
        return True
    if _TECH_STAGE.match(tt):
        return True
    return False


def classify(s: str) -> str:
    t = (s or "").strip()
    if not t:
        return "EMPTY"
    if is_cjk(t) or is_kr(t) or is_jp(t):
        return "NON_ENGLISH"
    n_nonlat = sum(1 for c in t if ord(c) > 127)
    if n_nonlat >= 2:
        return "NON_ENGLISH" if (is_cjk(t) or is_kr(t) or is_jp(t)) else "SYMBOL_OR_MIXED"

    tl = t.lower()
    words = re.findall(r"[A-Za-z]+", t)
    has_func = any(w.lower() in _FUNC for w in words)
    has_sem_word = any(w.lower().rstrip("s") in _SEM_WORD or w.lower() in _SEM_WORD for w in words)

    # 1) 方向上方向/动作短词白名单 -> 语义
    if tl in _DIR_SEM:
        return "ENGLISH_SEMANTIC"

    # 2) 纯编号/角色/变体标签 -> 不译
    if _is_non_semantic_tag(t):
        return "NON_SEMANTIC_TAG"

    # 2.5) 英文词+数字 无分隔(无空格/下划线): 若词干为通用姿势动作词 -> 作者编号标签
    #      standing1/laying3/sitting1 -> NON_SEMANTIC_TAG;  kiss2(语义) / Simmerianne93(笔名) 例外
    _BARE_WORD_NUM_STEMS = {"standing","sitting","laying","lying","kneeling","crouching",
                            "kneel","crouch","squat","lyingdown","standingup","sitdown",
                            "pose","variant","variation","ver","version","copy"}
    bm = re.match(r"^([A-Za-z]+)(\d+)$", t)
    if bm and (bm.group(1).lower().rstrip("s") in _BARE_WORD_NUM_STEMS
               or bm.group(1).lower() in _BARE_WORD_NUM_STEMS):
        return "NON_SEMANTIC_TAG"

    # 3) 技术内部标识 (a2o_/loopN/START/STOP/_seated_x/蛇纹) -> 不译
    if _is_technical_label(t):
        return "TECHNICAL_LABEL"

    # 3.5) 强语义词命中(Emotion/姿势/家庭角色…) 必须优先于 handle/专名判定:
    #       带数字 -> SEMANTIC_WITH_NUM (Injured_01, 2 - Unsure, Bed 2 - Kissing Belly)
    #       不带数字 -> ENGLISH_SEMANTIC (Wink/Smirk/Cocky/Dad…)
    if has_sem_word:
        return "SEMANTIC_WITH_NUM" if re.search(r"\d", t) else "ENGLISH_SEMANTIC"

    # 4) 账号/作者 handle 强证据: 字母+数字无空格短标识符, 且不在常见语义词表
    if _HANDLE_LIKE.match(t) and not tl in _SEM_WORD and not tl in _DIR_SEM:
        # 排除形如 "Pose1" 这类 = 已被 _TAG_POSE 捕获; 剩下的像是笔名
        return "PROPER_NAME"

    # 5) 带数字但含真实语义 -> SEMANTIC_WITH_NUM (保序号翻文字)
    if re.search(r"\d", t):
        # 能走到这里的: 已过滤掉所有物件/角色/性别/技术/编号标签(步骤 2/2.5/3/3.5 及新 tag 规则),
        # 剩余 = 带数字的语义姿势名。 只要含一个英文词 (无论单双词 + 序号), 即为语义带序号。
        #   覆盖: Rescue 1 / Positive 3 / 7 - Sweet / 15 reverence / Walk 2
        if words:
            return "SEMANTIC_WITH_NUM"
        # 带数字但无英文词 (纯 4(move) 等已在上层捕获, 此处为兜底)
        return "SEMANTIC_UNCERTAIN"

    # 6) 英文单词/短语
    if not any(re.match(r"[A-Za-z]", w) for w in words):
        return "SYMBOL_OR_MIXED"

    # 7) 姿势语义词命中 -> 语义 (Flirty/Massage/Tease/Sass/Kiss...)
    if has_sem_word:
        return "ENGLISH_SEMANTIC"

    # 8) 有功能词 (介词/连词) -> 语义短语
    if has_func:
        return "ENGLISH_SEMANTIC"

    # 9) 多词短语 (>=2 词) 无功能词 -> 语义 (标题大小写不再当专名证据)
    if len(words) >= 2 and len(t) <= 40:
        return "ENGLISH_SEMANTIC"

    # 9.5) 自然语言形态 (构词法, 非单词白名单): -ing/-ed/-tion/-sion/-ous/-ful/-ive/-ly/-ness 等
    #       后缀 -> 明显英文自然语言, 即便单短词也归语义 (Stretching/Pacing/Stressed/Explaining/
    #       Confessing/Tripping/Catastrophizing/Playful/Solemn/Walk…)。这是结构判断, 不建逐个词白名单。
    if len(words) == 1 and len(words[0]) <= 16:
        w = words[0]
        wl = w.lower()
        if (wl in _SEM_WORD or wl.rstrip("s") in _SEM_WORD):
            return "ENGLISH_SEMANTIC"
        if re.search(r"(?:ing|ed|tion|sion|ous|ful|ive|ness|able|ible|est|er)\b$", wl):
            return "ENGLISH_SEMANTIC"

    # 10) 单短词(非语义词表、非功能词): 无法确认是专名还是姿势名 -> 语义不确定
    if len(words) == 1 and len(words[0]) <= 15:
        return "SEMANTIC_UNCERTAIN"

    # 11) 其他(长串内部名等) -> 语义
    return "ENGLISH_SEMANTIC"

# 打分类 (decision/reason 分离)
#   decision = TRANSLATE / KEEP / REVIEW   (是否处理、是否送人工复核)
#   reason   = 细分依据: SEMANTIC / SEMANTIC_WITH_NUM / PROPER_NAME /
#              NON_SEMANTIC_TAG / TECHNICAL_LABEL / SYMBOL_OR_MIXED / SEMANTIC_UNCERTAIN
# 说明: llama1 与 Faye 最终都是 KEEP(不译), 但 reason 必须不同
#       (llama1 => NON_SEMANTIC_TAG; Faye => PROPER_NAME), 不能用"都不译"掩盖分类差异。
def classify_meta(s: str) -> tuple:
    cls = classify(s)
    if cls in ("ENGLISH_SEMANTIC", "SEMANTIC_WITH_NUM"):
        return ("TRANSLATE", cls)
    if cls in ("PROPER_NAME", "NON_SEMANTIC_TAG", "TECHNICAL_LABEL"):
        return ("KEEP", cls)
    if cls in ("SYMBOL_OR_MIXED", "SEMANTIC_UNCERTAIN", "NON_ENGLISH"):
        return ("REVIEW", cls)
    return ("REVIEW", cls)


def _split_neighbors(neigh: str) -> list[str]:
    """把 neighbor_display_texts 拆成条目 (以 | 分隔, 也可能逗号/换行)。"""
    if not neigh:
        return []
    parts = re.split(r"\s*\|\s*|\s*,\s*|\n+", neigh.strip())
    return [p.strip() for p in parts if p.strip()]


def _neighbor_semantic_ratio(neigh: str):
    """邻居中清晰语义条目占比 vs 清晰编号/专名条目占比。返回 (sem_cnt, tag_cnt)。"""
    sem_cnt = tag_cnt = 0
    for n in _split_neighbors(neigh):
        c = classify(n)
        if c in ("ENGLISH_SEMANTIC", "SEMANTIC_WITH_NUM"):
            sem_cnt += 1
        elif c in ("NON_SEMANTIC_TAG", "PROPER_NAME", "TECHNICAL_LABEL"):
            tag_cnt += 1
    return sem_cnt, tag_cnt


def classify_with_context(s: str, neigh: str = "") -> tuple:
    """分层分类: 先用结构规则 classify(), 若落到 SEMANTIC_UNCERTAIN(拿不准),
    再用 neighbor_display_texts 上下文做第二层判断 —— 而不是无限加单词白名单。

    - 邻居以清晰语义姿势名/情绪词为主 -> 提升为语义 (TRANSLATE / 原类别)
    - 邻居以编号/性别/专名标签为主 -> 保持不译 (KEEP / NON_SEMANTIC_TAG)
    - 邻居全是单短词人名/首字母大写 (Isaac|Elliot|Faye...) -> KEEP / PROPER_NAME
    - 邻居也判断不出 -> 保留 REVIEW / SEMANTIC_UNCERTAIN (不硬猜)
    """
    cls = classify(s)
    dec, rs = classify_meta(s)
    # 只有拿不准的才动用上下文 (明确 TRANSLATE/KEEP 不动, 保持结构优先)
    if dec != "REVIEW" or cls != "SEMANTIC_UNCERTAIN":
        return dec, rs
    nb = _split_neighbors(neigh)
    sem_cnt, tag_cnt = _neighbor_semantic_ratio(neigh)
    # 人名组: 邻居全是 单个首字母大写短词 (姓名集), 且本串也似人名 -> PROPER_NAME
    if nb and all(re.fullmatch(r"[A-Z][a-z]{1,15}", n.strip()) for n in nb) \
            and re.fullmatch(r"[A-Z][a-z]{1,15}", s.strip()):
        return ("KEEP", "PROPER_NAME")
    if sem_cnt >= 2 and sem_cnt > tag_cnt:
        # 邻居几乎全是语义词 -> 本串也是姿势名 (Walk 在 Wave|Walk|Dance|Happy 邻居中)
        return ("TRANSLATE", "SEMANTIC_UNCERTAIN->ENGLISH_SEMANTIC")
    if tag_cnt >= 2 and tag_cnt > sem_cnt:
        return ("KEEP", "NON_SEMANTIC_TAG")
    return ("REVIEW", "SEMANTIC_UNCERTAIN")


for r in rows:
    r["_cls"] = classify(r["source_text"])
    r["_decision"], r["_reason"] = classify_with_context(
        r["source_text"], r.get("sample_neighbor_display_texts") or "")
    r["_ref"] = int(r.get("ref_count") or 0)

cls_dist = Counter(r["_cls"] for r in rows)
print("\n类型分布 (3567 候选):")
for k, v in cls_dist.most_common():
    print(f"  {k:18} = {v}")
print("\ndecision 分布:")
for k, v in Counter(r["_decision"] for r in rows).most_common():
    print(f"  {k:12} = {v}")

# ---------------- 分层抽样 100 条 (跨 package 随机, 可复现) ----------------
# 2026-08-13 重写: 上一版严重聚集于少数 package (PROPER_NAME 全来自 Gounafiers,
# SEMANTIC_WITH_NUM 27 条来自 2 个 SamsSims 包), 不能当类别准确率。
# 新策略: 固定 seed; 同一 package 每类别最多 1~2 条; 优先覆盖尽可能多 package/作者。
import random
random.seed(20260813)

samples = []
seen_text = set()          # 全局去重 source_text
pkg_used = {}              # pkg -> {label -> count}  每类别每包限额

# 每 package 每类别最大抽取数 (重点类别放宽到 2, 其余 1)
PER_PKG_CAP = {
    "专名/作者": 2, "语义不确定": 2, "带数字语义名": 2,
    "普通短句": 1, "长姿势名": 1, "Left/Right/方向": 1,
    "非语义标签": 1, "技术内部标识": 1, "成人/姿势术语": 1, "其他/补充": 1,
}


def _pkg_of(r):
    return str(r.get("sample_package") or "").strip()


def pick(pred, n, label):
    """跨 package 分层随机抽 n 条 (固定 seed)。硬上限 100; 同一包同一 label 最多 PER_PKG_CAP。"""
    pool = []
    for r in rows:
        if r["source_text"] in seen_text:
            continue
        if not pred(r):
            continue
        pkg = _pkg_of(r)
        if pkg_used.setdefault(pkg, {}).get(label, 0) >= PER_PKG_CAP.get(label, 1):
            continue
        pool.append(r)
    random.shuffle(pool)
    got = 0
    for r in pool:
        if len(samples) >= 100:      # 全局硬上限
            break
        if got >= n:
            break
        if r["source_text"] in seen_text:
            continue
        pkg = _pkg_of(r)
        if pkg_used[pkg].get(label, 0) >= PER_PKG_CAP.get(label, 1):
            continue
        samples.append((label, r))
        seen_text.add(r["source_text"])
        pkg_used[pkg][label] = pkg_used[pkg].get(label, 0) + 1
        got += 1
    if got < n:
        print(f"  [提示] {label}: 跨包配额/总上限下仅抽到 {got}/{n}")
    return got


print("\n抽样 (跨 package 分层随机, seed=20260813, 全局上限 100):")

# ---- 主评审三袋: 30/40/30 = 100, 优先抽满 ----
# 4) 专名 —— 重点, 跨包 30 条 (尽量 30 个不同 package)
pick(lambda r: r["_cls"] == "PROPER_NAME", 30, "专名/作者")
# 5) 带数字语义名 —— 重点, 跨包 30 条
pick(lambda r: r["_cls"] == "SEMANTIC_WITH_NUM", 30, "带数字语义名")
# 6) 语义不确定 —— 重点, 跨包 40 条
pick(lambda r: r["_cls"] == "SEMANTIC_UNCERTAIN", 40, "语义不确定")

# ---- 次要复核小袋: 只占剩余额度, 不超 100 ----
# 7) 非语义标签 (少量抽查)
pick(lambda r: r["_cls"] == "NON_SEMANTIC_TAG", 8, "非语义标签")
# 8) 技术内部标识 (少量抽查)
pick(lambda r: r["_cls"] == "TECHNICAL_LABEL", 6, "技术内部标识")
# 1) 普通短句
short_sem = [r for r in rows if r["_cls"] == "ENGLISH_SEMANTIC" and len(r["source_text"]) <= 24]
pick(lambda r: r in short_sem, 6, "普通短句")
# 2) 长姿势名
long_sem = [r for r in rows if r["_cls"] == "ENGLISH_SEMANTIC" and len(r["source_text"]) > 40]
pick(lambda r: r in long_sem, 3, "长姿势名")
# 3) Left/Right/方向
pick(lambda r: r["source_text"].strip().lower() in {"left","right","top","bottom","front","back"}, 2, "Left/Right/方向")
# 9) 成人/姿势术语
adult_kw = re.compile(r"(sex|kiss|fuck|fuckin|blow|oral|thrust|penetrat|nude|strip|bdsm|mastur|orgasm|cum|erect|arous|bondage|vibrat|fellat|cunniling|anal|breast|ass\b|booty|pussy|dick|whore|lust|seduct|flirt|tease|massage|sass)", re.I)
pick(lambda r: bool(adult_kw.search(r["source_text"])), 3, "成人/姿势术语")
# 10) 其他 (若仍未满 100 则跨包补足)
pick(lambda r: True, 100 - len(samples), "其他/补充")

# ---------------- 输出 ----------------
print(f"\n抽样总数: {len(samples)} / 100")
out_cols = ["sample_group", "text_class", "source_text", "ref_count", "unique_keys",
            "sample_package", "sample_pose_pack", "sample_stbl_instance", "sample_locale",
            "neighbor_poses", "neighbor_display_texts", "decision", "reason"]
sample_out = out_dir / "translation_samples_100.csv"
with open(sample_out, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=out_cols)
    w.writeheader()
    for grp, r in samples:
        w.writerow({
            "sample_group": grp,
            "text_class": r["_cls"],
            "source_text": r["source_text"],
            "ref_count": r.get("ref_count", ""),
            "unique_keys": r.get("unique_keys", ""),
            "sample_package": r.get("sample_package", ""),
            "sample_pose_pack": r.get("sample_pose_pack", ""),
            "sample_stbl_instance": r.get("sample_stbl_instance", ""),
            "sample_locale": r.get("sample_locale", ""),
            "neighbor_poses": r.get("sample_neighbor_poses", ""),
            "neighbor_display_texts": r.get("sample_neighbor_display_texts", ""),
            "decision": r.get("_decision", ""),
            "reason": r.get("_reason", ""),
        })
print(f"已写出: {sample_out}")
print("\n按样本组统计:")
for grp, cnt in Counter(g for g, _ in samples).most_common():
    print(f"  {grp:16} = {cnt}")
print("\ndecision 分布 (抽样内):")
for k, v in Counter(r.get("_decision", "") for _, r in samples).most_common():
    print(f"  {k:12} = {v}")
