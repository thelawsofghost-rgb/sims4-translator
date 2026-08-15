#!/usr/bin/env python3
"""Phase 2B: 翻译引擎 (翻译层决策 + 中文生成)。

只做翻译, 不写任何 .package。写回由 phase2b_writeback.py 负责 (本脚本不调用)。

输入:
  output/translations_todo.csv   待译清单 (1968 行, 由 phase2a_catalog.py 生成)
输出:
  output/translation_done.csv     完成清单; 列:
      translation_id, source_text, decision, translate_mode, detection, detected_language,
      translation, status, source_hash
      其中:
        translate_mode = FULL_TRANSLATE | PARTIAL_TRANSLATE | KEEP | OVERRIDE_T | OVERRIDE_K
        decision       = TRANSLATE | REVIEW (7 条人工审批后写 TRANSLATE)
        status         = APPROVED (7 条人工审批) | DONE (翻译完成) | DONE_SKIP (KEEP) | KEEP (override 终态)
  output/translation_overrides.csv  人工终态覆盖 (可选, 优先级最高, 不写 cache/不写 .package):
      translation_id, source_text, translation, action(TRANSLATE|KEEP|REVIEW), reason, notes
      TRANSLATE -> status=DONE ; KEEP -> status=KEEP (QA 视为明确终态 PASS)
  (可选, 传 --sample N 时) output/translation_sample_zh.csv  分层抽样 N 行含译文, 供人工抽查

翻译层三档 (用户 2026-08-13 拍板):
  1. KEEP            纯技术/编号, 无可翻译语义 (剥离 ID token 后为空):
                       5M *anim / 8 *animation / F1+2 / 1B (animation)  -> 不输出中文
  2. PARTIAL_TRANSLATE  半技术半语义: 保护编号/版本/*anim 等 token, 只翻语义部分:
                       41Ha Holding Arm Fist Up -> 41Ha <译文>
                       1Aa Angry                -> 1Aa 愤怒
                       5A Nervousness           -> 5A 紧张
                       7M *anim: angry-sad      -> 7M *anim: 愤怒-悲伤
  3. FULL_TRANSLATE   全语义: 整条翻译。

7 条人工审批 RERVIEW -> TRANSLATE/APPROVED (用户拍板, 硬编码翻译, 禁止翻译引擎改写):
  Femme                  -> 女性
  pose                   -> 姿势
  Asomado                -> 探出身子
  РЫЦАРЬ / KNIGHT        -> 骑士
  Revisando              -> 查看中
  Asustado               -> 受惊
  F1 (ВЕРСИЯ С 3Д ЯЗЫКОМ)-> F1（3D舌头版）   # языком 在此语境=舌头, 非语言, 不作 KEEP

用法:
  python3 phase2b_translate.py [output_dir] [--sample N] [--no-llm] [--engine deepseek|none]
  --sample N : 只对分层抽样的 N 行调用翻译引擎 (默认全量)
  --no-llm   : 不调用任何翻译引擎, 仅跑翻译层决策 (输出 mode/status 骨架, translation 置空)
  --engine none : 同 --no-llm
  --overrides <path> : 用指定的 production overlay 作为人工 override 源 (只读,
                       不改 frozen base/不回写)。缺省回退到 frozen translation_overrides.csv。
  --authoritative : 权威 workset, decision=TRANSLATE 不得被老 classifier 改判 KEEP
  --id / --regex / --category : 作用域裁剪 (先于一切层决策), 支持 retry 定位
  retry preflight (--overrides + --id 38): 打印 requested/scoped/unique/
      production_overrides_loaded/terminal_KEEP_hit/manual_final_hit/authoritative_TRANSLATE,
      期望值从当前 production overlay / retry manifest 实际推导 (不硬编码 145/38);
      硬校验: KEEP/manual 不入 retry, authoritative==retry 行数, scope 不丢行。
      硬校验 production_overrides=145 & KEEP/manual hit=0 & authoritative=38, 不满足退出 4。
"""
import sys, os, csv, re, json, io, unicodedata
from pathlib import Path
from collections import Counter

out_dir = Path(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--")
               else "D:/projects/sims4_trans/output")
TODO = out_dir / "translations_todo.csv"
DONE = out_dir / "translation_done.csv"
ARGS = list(sys.argv[1:])
SAMPLE = None
NO_LLM = "--no-llm" in ARGS or "--engine" in ARGS
if "--sample" in ARGS:
    i = ARGS.index("--sample")
    if i + 1 < len(ARGS) and not ARGS[i + 1].startswith("--"):
        SAMPLE = int(ARGS[i + 1])
    else:
        print("--sample 后面缺少数字", file=sys.stderr); sys.exit(2)

# ---- 新增 CLI 控制 (翻译缓存 / 增量 / 定位 / 并发, 2026-08-13) ----
def _flag_val(name, default=None):
    if name in ARGS:
        i = ARGS.index(name)
        if i + 1 < len(ARGS) and not ARGS[i + 1].startswith("--"):
            return ARGS[i + 1]
        print(f"{name} 后面缺少值", file=sys.stderr); sys.exit(2)
    return default

ONLY_CHANGED = "--only-changed" in ARGS      # 只重翻 todo 里 source_hash 变化的行(cache 指纹已保证天然幂等, 此标志主要用于"感知")
FORCE = "--force" in ARGS                    # 忽略 cache, 强制重翻
ONLY_ID = _flag_val("--id")                  # 只处理指定 translation_id (逗号分隔可多个)
ID_FROM_FILE = _flag_val("--id-from-file")   # 从纯文本文件读 tid 列表 (每行一个, 或逗号分隔) → 自动并入 ONLY_ID
if ID_FROM_FILE:
    _ids = []
    for _line in Path(ID_FROM_FILE).read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line:
            continue
        _ids.extend(x.strip() for x in _line.split(",") if x.strip())
    _ids = [x for x in _ids if x]
    ONLY_ID = ",".join(_ids) if _ids else ONLY_ID
    if ONLY_ID:
        print(f"[--id-from-file] 读取 {len(_ids)} 个 tid")
ONLY_REGEX = _flag_val("--regex")            # 只处理 source_text 匹配该正则的行
ONLY_CATEGORY = _flag_val("--category")      # 只处理 decision/category 等于该值的行
AUTHORITATIVE_DECISION_ON = "--authoritative" in ARGS  # 权威 workset: decision=TRANSLATE 不得被老 classifier 改判 KEEP
CONCURRENCY = int(_flag_val("--concurrency", "8"))
BATCH_SIZE = int(_flag_val("--batch-size", "8"))

# ---- 增量 workset 接入 (2026-08-15): --todo/--done 允许直接以
#      translation_batch_manifest.csv 为唯一 workset/todo 源, 而不必是
#      旧 translations_todo.csv。不改任何翻译逻辑: glossary/protected spans/
#      override/cache/QA/done 写回 全部复用。仅替换输入与输出文件名。 ----
_TODO_OVERRIDE = _flag_val("--todo")
_DONE_OVERRIDE = _flag_val("--done")
if _TODO_OVERRIDE:
    TODO = Path(_TODO_OVERRIDE)
if _DONE_OVERRIDE:
    DONE = Path(_DONE_OVERRIDE)

# ---- production overlay 接入 (2026-08-15): --overrides <path> 让人工 override 加载
#      指定的 production overlay 文件, 而不默认读 frozen translation_overrides.csv(114)。
#      只读该文件, 绝不改 frozen base, 绝不写回 114/145。不改变 scope-at-load /
#      authoritative gate / POLICY-CONFLICT。缺省回退到 frozen baseline (向后兼容)。
OVERRIDES_PATH = _flag_val("--overrides")
if OVERRIDES_PATH:
    print(f"[overrides] 使用 production overlay: {OVERRIDES_PATH}")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from phase2a_catalog import (norm_text, source_hash, detect_language,
                             _split_semantic_tokens, _is_id_token)
from phrase_cache import (PhraseCache, build_fingerprint, system_prompt,
                          model_name, target_language, temperature)

# ---------------- 7 条人工审批 (用户硬编码, 禁止改写) ----------------
APPROVED = {
    "Femme":                      ("女性", "fr"),
    "pose":                       ("姿势", "en"),
    "Asomado":                    ("探出身子", "es"),
    "РЫЦАРЬ / KNIGHT":            ("骑士", "ru"),
    "Revisando":                  ("查看中", "es"),
    "Asustado":                   ("受惊", "es"),
    "F1 (ВЕРСИЯ С 3Д ЯЗЫКОМ)":    ("F1（3D舌头版）", "zxx"),
}
APPROVED_TEXT = set(APPROVED.keys())

# 技术标记词: 在剥离 ID token 后, 若剩余"语义"仅是这些标签词本身 -> 无可描述内容 -> KEEP。
# 有界小集合: anim/animation 等是模组技术标签, 不是姿势描述。
_TECH_TAG = {"anim", "animation", "anims"}
# 内部占位/技术标识 (开发用, 非给玩家看的描述): 无空格的 camelCase 或含下划线的标识符 -> KEEP
_CAMEL_ID = re.compile(r"^[a-z][A-Za-z0-9]*(?:[A-Z][a-z0-9]+)+$")
_UNDERSCORE_ID = re.compile(r"^[a-zA-Z0-9_]+_[a-zA-Z0-9_]+$")

def _is_technical_identifier(text: str) -> bool:
    """是否为开发用技术标识符 (非玩家可见描述): 如 placeholderIntro / my_var / pkg_001。
    仅当 无空格 且 (camelCase 或含下划线 或 特定占位前缀) 才判为技术串。
    避免误伤 walk5/right-click 这类真实姿势命名。"""
    t = text.strip()
    if not t or " " in t:
        return False
    if _UNDERSCORE_ID.match(t):
        return True
    if _CAMEL_ID.match(t):
        return True
    # 常见占位/内部标识前缀 (开发用)
    pl = t.lower()
    for pre in ("placeholder", "pkg_", "var_", "id_", "anim_", "intro", "outro", "todo", "deprecated"):
        if pl.startswith(pre):
            return True
    return False


def translate_mode_for(text: str):
    """翻译层三档决策 (确定性, 不需要 LLM)。

    返回 (mode, 需翻译的语义片段列表, 全部 token)。
    KEEP  = 无可翻译语义 (剥离 ID/技术标记 token 后为空) 或 纯技术标识符
    PARTIAL = 既有需保留的 ID/编号, 又有真实语义片段
    FULL    = 全语义
    """
    t = norm_text(text)
    # 纯技术/占位标识符 -> KEEP, 不进模型
    if _is_technical_identifier(t):
        return "KEEP", [], [t]
    sem, ids = _split_semantic_tokens(t)
    # 孤立小写单字母 (a/i/o 等冠词/代词) 视为语义词, 非编号索引
    # (真实姿势索引/性别标记如 A/B/F/M 通常为大写)
    sem = [w for w in sem] + [w for w in ids if w.islower() and len(w) == 1]
    ids = [w for w in ids if not (w.islower() and len(w) == 1)]
    # anim/animation 技术标记词也归入 ID (如 "1B (animation)" / "8 *animation")
    real_sem = [w for w in sem if w.lower().strip("* ") not in _TECH_TAG]
    if not real_sem:
        return "KEEP", [], sem + ids
    if ids:
        return "PARTIAL_TRANSLATE", real_sem, sem + ids
    return "FULL_TRANSLATE", real_sem, sem


_ID_SPAN = re.compile(r"(?:\d[\w]*|\*anim\w*|\b[A-Za-z]\d[\w]*|\b(?:V|v)\d+(?:\.\d+)?\b|[\d.]+)")


def protected_spans(text: str) -> str:
    """找出 PARTIAL 时需原样保留的 token (编号/版本/*anim) 在原文本中的区间。

    返回形如 "41Ha@0-4; 5M@12-14" 的串; 无则空串。
    """
    t = norm_text(text)
    spans = []
    for m in _ID_SPAN.finditer(t):
        seg = m.group(0)
        spans.append(f"{seg}@{m.start()}-{m.end()}")
    return "; ".join(spans)


# ---------------- 姿势领域术语表 (硬编码, 高优先; 不进模型) ----------------
# 只固定高频且已确认的领域歧义词。数值/编号/版本/*anim 由程序保留重建,
# 此处是涉及"方向/位置/情绪/人物"等语义的固定译法。
_GLOSSARY = {
    "right": "右", "left": "左", "middle": "中",
    "positive": "积极", "negative": "消极", "neutral": "中性",
    "concern": "担忧", "doubtful": "疑惑", "smirk": "坏笑",
    "sim": "模拟市民", "idle": "待机",
    "all in one": "整合版",
}
# 统一为小写键做 casefold 不敏感查找 (All In One / ALL IN ONE / all in one 都命中)
_GLOSSARY = {k.casefold(): v for k, v in _GLOSSARY.items()}
# 单词语义键 (用于 phrase 内部独立 token 的嵌入式固定术语; 不含多词 all in one)
_WORD_GLOSS = {k: v for k, v in _GLOSSARY.items() if " " not in k}


# ---------------- 精确切分 + 重建 (格式/编号/版本由程序保留, 模型只翻语义 phrase) ----------------
# 断点 = 受保护 token (编号/版本/*anim/纯数字/性别代号) 或 独立分隔符 (斜杠/括号/逗号/空白包裹的连字符)
# 注意: f2/m2/f1 等性别/索引代号、6.2/v.2 等含点版本, 必须整体当 prot, 不给模型改的机会
_PROTECTED = re.compile(r"\d[\w]*(?:-\d[\w]*)*|\*anim\w*|[\u0400-\u04ffA-Za-z]\d[\w]*|\b(?:V|v)\d+(?:\.\d+)?\b|[\d.]+")
# 单字母性别/索引代号 + 数字, 整体作为 prot (f2/m2/f1/M2); 仅当独立 token, 避免误吃 walk5/paper2
def _is_gender_code(t, i):
    """t[i] 处是否是一个独立小写/单字母+数字的代号 (如 f2/m2/f1/M2)。
    不匹配 walk5/paper2 这类自然词, 也不匹配 ALL IN ONE 里的多字母词。"""
    if i >= len(t) - 1:
        return None
    c = t[i]
    if not c.isalpha() or not t[i + 1].isdigit():
        return None
    prev_ok = (i == 0 or not t[i - 1].isalnum())   # 前一个不是字母数字 → 独立起点
    if not prev_ok:
        return None
    # 向后吞下数字/点/短横 (f2, f2.1, M2, 1A 则不含字母在前)
    j = i + 1
    while j < len(t) and (t[j].isdigit() or t[j] in ".-"):
        # . 后必须跟数字才继续 (防止 f2. 吃掉结尾点)
        if t[j] == "." and (j + 1 >= len(t) or not t[j + 1].isdigit()):
            break
        if t[j] == "-" and (j + 1 >= len(t) or not t[j + 1].isdigit()):
            break
        j += 1
    tok = t[i:j]
    # 必须含数字, 且整串即单字母/短字母+数字号 (排除 walk5: walk 是多字母自然词)
    if any(ch.isdigit() for ch in tok) and len(tok) <= 4 and sum(1 for ch in tok if ch.isalpha()) <= 1:
        return tok
    return None


# 版本号含点: v.2 / 6.2 / v.1 整体 prot (正则在 _PROTECTED 之外再补: 字母+v.+数字, 数字.数字)
_VERSION_DOT = re.compile(r"(?:[Vv]\s*[.]\s*\d+(?:[.]\d+)*|\d+[.]\d+)")
# 下划线连接的字母数字编号: M1_1 / F2_4 / P2_1 -> 整体 prot (下划线是编号分隔, 非自然词)。
# 仅匹配 单字母+数字+(_数字)+ 形态, 不误伤 walk_2/left_leg 这类自然下划线词
_UNDERCODE = re.compile(r"[A-Za-z]\d+(?:_\d+)+")
# 方括号技术标签: [L2S] / [anim] / [mw] / [P2] -> 整体 prot
_BRACKET_TAG = re.compile(r"\[[\w./-]+\]")
# 分隔符: / \ , ; ( ) [ ] * , 以及前后带空白的连字符 (不计 angry-sad 内部连字符)
_SEPARATOR = re.compile(r"\\|/|[,;:()\[\]*]|\s+-\s+|\s+-$|^-\s+")


def split_semantic_spans(text: str, force_prot_spans=None):
    """phrase-level 切分。返回 (segs, sem_phrases)。

    segs 每项: {t, kind('sem'|'prot')}; 连续的语义多词连成**一个** phrase (sem),
    只在 受保护 token / 独立分隔符 处切断。glossary 命中保留在重建层整词/整 phrase 替换。
    """
    t = norm_text(text)
    segs = []
    i = 0
    n = len(t)
    sem_buf = []

    def flush_sem():
        if sem_buf:
            segs.append({"t": "".join(sem_buf), "kind": "sem"})
            sem_buf.clear()

    while i < n:
        # 1) 版本号含点 (v.2 / 6.2 / v.1) 整体 prot, 不给模型改 . 的机会
        vm = _VERSION_DOT.match(t, i)
        if vm:
            tok = vm.group(0)
            # 仅当独立边界, 避免误吃 "6.2" 之类的自然小数在词中
            prev_ok = (i == 0 or not t[i - 1].isalnum())
            nxt_ok = (vm.end() == n or not t[vm.end()].isalnum())
            if prev_ok and nxt_ok:
                flush_sem()
                segs.append({"t": tok, "kind": "prot"})
                i = vm.end()
                continue
        # 2b) 下划线连接编号 (M1_1 / F2_4 / P2_1) 整体 prot —— 下划线与字母数字同属编号, 不给模型翻成"下划线"
        #      (先于性别代号判断, 因 M1_1 应整体吞, 不能被 M1 性别代号抢先拆开)
        um = _UNDERCODE.match(t, i)
        if um:
            prev_ok = (i == 0 or not t[i - 1].isalnum() or t[i - 1] == "_")
            nxt_ok = (um.end() == n or not t[um.end()].isalnum())
            if prev_ok and nxt_ok:
                flush_sem()
                segs.append({"t": um.group(0), "kind": "prot"})
                i = um.end()
                continue
        # 2) 单字母性别/索引代号 (f2/m2/f1) 整体 prot
        code = _is_gender_code(t, i)
        if code:
            flush_sem()
            segs.append({"t": code, "kind": "prot"})
            i += len(code)
            continue
        # 2c) 方括号技术标签 ([L2S] / [anim] / [P2]) 整体 prot
        bm = _BRACKET_TAG.match(t, i)
        if bm:
            flush_sem()
            segs.append({"t": bm.group(0), "kind": "prot"})
            i = bm.end()
            continue
        # 受保护 token 优先 (是硬断点); 必须 以数字/*/大写字母数字混合 开头才安全, 避免误吃自然词
        pm = _PROTECTED.match(t, i)
        if pm:
            start = pm.group(0)
            prev_ch = t[i - 1] if i > 0 else " "
            next_ch = t[pm.end()] if pm.end() < n else " "
            # 词中间不切 (如 "Simple" 里不切数字; "V2" 版本号要切)
            is_break = (start[0].isdigit() or start[0] == "*") \
                or (start[0].isupper() and re.search(r"\d", start))
            # 版本号 V2/v1.3 也要断
            if re.fullmatch(r"[Vv]\d+(?:\.\d+)?", start):
                is_break = True
            if is_break:
                flush_sem()
                segs.append({"t": start, "kind": "prot"})
                i = pm.end()
                continue
        # 孤立大写单字母 = 性别/姿势索引 (F/A/B/M/А) -> prot 保留
        # 仅当前后都不是字母数字时 (真正独立 token), 否则是自然词内部 (如 RIGHT 里的 T)
        prev_is_alnum = (i > 0 and t[i - 1].isalnum())
        next_is_alnum = (i + 1 < n and t[i + 1].isalnum())
        if t[i].isupper() and not prev_is_alnum and not next_is_alnum:
            flush_sem()
            segs.append({"t": t[i], "kind": "prot"})
            i += 1
            continue
        sep = _SEPARATOR.match(t, i)
        if sep:
            flush_sem()
            segs.append({"t": sep.group(0), "kind": "prot"})
            i = sep.end()
            continue
        sem_buf.append(t[i]); i += 1
    flush_sem()

    # 给 sem phrase 编号 key
    idx = 0
    for s in segs:
        if s["kind"] == "sem":
            s["key"] = str(idx); idx += 1
    # BUG4: 强制 demote 已锁定的 creator/identifier 区间 -> prot (跳出 pending / required_translate)
    # 实现: 若来源以某 locked prefix 开头, 把该前缀作为整体 prot 段, 对剩余部分重新切分。
    if force_prot_spans:
        return _split_with_forced_span_prefix(text, force_prot_spans)
    sem_phrases = [s for s in segs if s["kind"] == "sem"]
    return segs, sem_phrases


def _split_with_forced_span_prefix(text: str, force_prot_spans):
    """BUG4: 把锁定的 creator 区间强制为 prot 段。

    force_prot_spans: [(start, end, reason)] 在 norm_text(text) 的字符下标上
    (title_creator_protection 产出)。支持两类:
      - 起始前缀 (start==0): 前缀作为整体 prot 段, 剩余部分递归切分。
      - 中置 creator token (start>0, 如 [Jarride]xLienaEnna): 把与之文本相等的
        sem 段 demote 为 prot (无需重切整个串)。
    """
    t = norm_text(text)
    if not t:
        return [], []
    if not force_prot_spans:
        return split_semantic_spans(text)
    s0 = force_prot_spans[0]
    start = int(s0[0]) if isinstance(s0, (tuple, list)) else 0
    end = int(s0[1]) if isinstance(s0, (tuple, list)) else int(s0)
    # 中置 creator token -> 按段文本 demote
    if start > 0:
        segs, sem = split_semantic_spans(text)
        _tar = norm_text(t[start:end]).strip()
        _forced = [s for s in segs if s["kind"] == "sem"
                   and s["t"].strip().casefold() == _tar.casefold()]
        for s in _forced:
            s["kind"] = "prot"
            s.pop("key", None)
        _k = 0
        for s in segs:
            if s["kind"] == "sem":
                s["key"] = str(_k); _k += 1
        return segs, [s for s in segs if s["kind"] == "sem"]
    # 起始前缀 -> 前缀整体 prot + 剩余递归切分
    end = max(end, 1)
    if end >= len(t):
        end = len(t)
    creator = t[:end]
    rest = t[end:]
    prot_seg = [{"t": creator, "kind": "prot"}]
    if not rest.strip():
        return prot_seg, []
    rsegs, _ = split_semantic_spans(rest)
    segs = prot_seg + rsegs
    _k = 0
    for s in segs:
        if s["kind"] == "sem":
            s["key"] = str(_k); _k += 1
    return segs, [s for s in segs if s["kind"] == "sem"]


def rebuild(segs: list, resolved: dict):
    """按原模板重建: prot 段原样, sem phrase 替换为 resolved[key]。

    resolved: {key: 译文}。缺译文则保留该 phrase 原文。
    """
    out = []
    for s in segs:
        if s["kind"] == "sem":
            out.append(resolved.get(s.get("key"), s["t"]))
        else:
            out.append(s["t"])
    return "".join(out)


# 行首 "Target:" 前缀剥离 (确定性后处理, 不调 LLM)。
# Ollama 在 JSON Schema 输出时偶把 prompt 里的 "Target: <原文>" 语义带进 zh 值前,
# 模型也可能输出中文 "目标：<原文>"。这里只匹配译文开头 (仅剥前缀, 不全局删除),
# 允许 target|目标 + 大小写不敏感 + 全/半角冒号。
_TARGET_PREFIX_RE = re.compile(r"^\s*(?:target|目标)\s*[:：]\s*", re.IGNORECASE)


def normalize_model_output(text):
    """清洗模型输出/缓存译文: 剥掉行首 target:/目标： 前缀 (含全/半角冒号)。

    仅匹配译文开头, 不碰正文里的 "Target" 词 (如 "My Target: Pose" 不应被误删),
    也不碰正文里的"目标"词 (如 "目标管理" 不应被误删)。
    """
    if not text:
        return text
    return _TARGET_PREFIX_RE.sub("", str(text))


def materialize_from_cache(tid: str, text: str, mode: str, cache, ctx_map=None) -> (str, str):
    """从 cache 物化一行 PARTIAL/FULL 的完整译文 (materialized output)。

    对该行重新切分 + glossary, 对每个 pending phrase 查指纹; 全部命中 ->
    rebuild 出译文并置 DONE; 任一 phrase 缺失 -> (None, 'PENDING') 表示需重翻。
    模式 KEEP/APPROVED 不在此处理 (调用方已分支)。

    tid/ctx_map: 与生产主流程同源的行级上下文 (tid -> list[str]); 用于给每个
    pending 填 p['ctx'] (与 jobs 构建时一致), 保证 fingerprint 与 cache 写入时
    完全一致。缺省时按无 ctx 处理 (与旧行为一致, 但会导致带 ctx 的行查不到)。
    """
    if mode not in ("PARTIAL_TRANSLATE", "FULL_TRANSLATE"):
        return None, "PENDING"
    _prot, _r = title_creator_protection(text)
    segs, _ = split_semantic_spans(text, force_prot_spans=_prot)
    gloss, pending = glossary_resolve(segs)
    pending = [p for p in pending if p["t"].strip()]
    if not pending:
        # 全由 glossary 直译 -> 可物化
        return rebuild(segs, gloss), "DONE"
    ctx = ctx_map.get(tid, []) if ctx_map else []
    # 生产同款: 前 3 个上下文用 " | " 连接, 填入每个 pending 的 p['ctx']
    ctx_str = " | ".join(ctx[:3]) if ctx else ""
    for p in pending:
        p["ctx"] = ctx_str
    resolved = dict(gloss)
    for p in pending:
        fp = build_fingerprint(source_phrase=p["t"].strip(),
                               glossary_hint=p.get("gloss_hint", ""),
                               context=p.get("ctx", ""))
        hit = cache.get(fp)
        if not hit:
            return None, "PENDING"
        # BUG1: cached 译文 == normalized source phrase (echo) -> invalid, 视为缺失重翻
        _cand = normalize_model_output(hit["translation"])
        if _cand.strip() and _cand.strip() == p["t"].strip():
            return None, "PENDING"
        # 确定性后处理: 剥离行首 Target: 前缀 (缓存里可能存了脏值, 0 LLM 重新清洗)
        resolved[p["key"]] = _cand
    translation = rebuild(segs, resolved)
    translation = restore_protected(segs, translation)
    if translation.strip():
        return translation, "DONE"
    return None, "PENDING"


# ---------------- 人工 override (translation_overrides.csv) ----------------
# 用户 2026-08-14 拍板: 22 条 ERROR 全部 final 定案, 走 override 而非改 phrase cache。
# override 优先级 > cache > LLM; 永不写 phrase cache; 不写 .package。
#
# 列: translation_id, source_text, translation, action, reason, notes
#   action=TRANSLATE : 人工译文, 终态 DONE (translation 必填非空)
#   action=KEEP      : 保留英文, 终态 KEEP (translation 留空, QA 视为明确终态而非未完成)
#   action=REVIEW    : 显式挂起, 不改写该行 (走正常流程)
# 校验: 必须 translation_id + source_text 两者同时匹配才允许 override;
#       缺列/非法 action/(tid,source_text) 不一致 -> 告警并跳过该条。
OVER_FILE = Path(OVERRIDES_PATH) if OVERRIDES_PATH else out_dir / "translation_overrides.csv"
_OVERRIDE_ACTIONS = {"TRANSLATE", "KEEP", "REVIEW"}


def load_overrides(out_dir_) -> dict:
    """读 translation_overrides.csv (或 --overrides 指定的 production overlay),
    返回 {(tid, source_text): override_row}。

    校验 (translation_id, source_text) 组合; 不一致或缺关键列的条目跳过并告警。
    文件不存在 -> 空 dict (机制可逆: 删文件即回退到纯 cache/LLM 流程)。
    """
    p = OVERRIDES_PATH if OVERRIDES_PATH else (Path(out_dir_) / "translation_overrides.csv")
    p = Path(p)
    ovr = {}
    if not p.exists():
        if OVERRIDES_PATH:
            print(f"[HARD-FAIL] --overrides 指定的文件不存在: {p}", file=sys.stderr)
            sys.exit(3)
        return ovr
    with open(p, encoding="utf-8-sig") as f:
        for i, r in enumerate(csv.DictReader(f), start=2):
            tid = (r.get("translation_id") or "").strip()
            src = (r.get("source_text") or "").strip()
            act = (r.get("action") or "").strip().upper()
            if not tid or not src:
                print(f"[override] 第 {i} 行: 缺 translation_id/source_text, 跳过", file=sys.stderr)
                continue
            if act not in _OVERRIDE_ACTIONS:
                print(f"[override] 第 {i} 行: action={act!r} 非法, 跳过", file=sys.stderr)
                continue
            key = (tid, src)
            if key in ovr:
                print(f"[override] 第 {i} 行: 重复 (tid,source_text), 后者覆盖", file=sys.stderr)
            ovr[key] = {"translation_id": tid, "source_text": src,
                        "translation": (r.get("translation") or "").strip(),
                        "action": act,
                        "reason": (r.get("reason") or "").strip(),
                        "notes": (r.get("notes") or "").strip()}
    return ovr


def override_for(ovr: dict, tid, text) -> dict | None:
    """查 (tid, source_text) 的 override(需两者同时匹配); 无则 None。"""
    if not ovr:
        return None
    key = (tid, (text or "").strip())
    return ovr.get(key)


def restore_protected(segs: list, translation: str):
    """防御性兜底: 强制恢复受保护 token。

    理论上 prot 段从不过模型、rebuild 已原样带回; 但为保不变量
    (模型没有任何机会/任何边界情况改掉保护 token), 这里再扫一遍 final 串:
    对每个 prot 段, 若其原样未出现在译文中, 则追加/补回, 保证格式/编号/版本不被吞。
    """
    if not translation:
        return translation
    for s in segs:
        if s["kind"] != "prot":
            continue
        tok = s["t"]
        if not tok.strip():
            continue
        if tok in translation:
            continue
        # 未在译文中原样出现 (被模型吞/改) -> 补回 (主要防止纯技术 token 丢失)
        translation += tok
    return translation


# ---------------- BUG4: PACK_TITLE creator/identifier 保护层 (确定性, 可审计) ----------------
# required_translate / j["pending"] 不能把作者/占位标识亲逐为可译语义。
# 规则: 精确 per-source 锁定 creator 前缀 (configs/title_creator_prefix.c26.csv), 非宽泛启发式
#       (禁止: 首个英文词==作者 / 所有 underscore 都 KEEP / title-case 都翻)。
# 方括号 token ([ROSELIPA]/[Raspberrywhimss]/[AA]...) 已由 split_semantic_spans 的 _BRACKET_TAG
# 整体 prot, 不在本层重复。本层只补非方括号的 creator 前缀。
_PROT_CREATOR_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "configs", "title_creator_prefix.c26.csv")


def _load_creator_protect():
    """读 frozen title_creator_prefix.c26.csv -> [(prefix, reason)]。缺失则空列表 (退化, 不 HARD-FAIL)。"""
    out = []
    p = Path(_PROT_CREATOR_FILE)
    if not p.exists():
        return out
    with open(p, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            pre = (r.get("protected_prefix") or "").strip()
            if pre:
                out.append((pre, (r.get("reason") or r.get("creator") or pre).strip()))
    # 按前缀长度降序, 保证 Grownasssimmer Kaley 优先于 Grownasssimmer
    out.sort(key=lambda x: len(x[0]), reverse=True)
    return out


_CREATOR_PROTECT = None


def title_creator_protection(text: str):
    """返回 (prot_spans, reasons) 供 split_semantic_spans 强制 prot。

    prot_spans: [(start, end, reason)] 字符区间 (在 norm_text(text) 上)。
    reasons:    { (start,end): reason }
    匹配规则 (精确, 避免 substring/宽泛):
      - creator 前缀必须位于文本起始 (t.lstrip() 起点处), 且
      - 前缀后必须紧跟分隔符/空格/结尾才成立 —— 不匹配 Simpler/xxcreator。
    """
    global _CREATOR_PROTECT
    if _CREATOR_PROTECT is None:
        _CREATOR_PROTECT = _load_creator_protect()
    t = norm_text(text).strip()
    spans = []
    reasons = {}
    if not t:
        return spans, reasons
    lead = len(t) - len(t.lstrip())  # 前置空白 (本函数已 strip, 恒 0)
    for pre, reason in _CREATOR_PROTECT:
        # 情况 A: 前缀位于文本起始 (最常见 creator 前缀)
        if t.startswith(pre):
            after = len(pre)
            if after >= len(t) or not t[after].isalnum():
                # 扩展: 吞掉 creator 后的分隔符/空白 (保持 rebuild 边界空格, 如 "(simmer_creator) ",
                # "Loulicorn - "), 使剩余部分从干净语义词开始切分。
                _sepchars = "-:/\\),]"
                while after < len(t) and (t[after].isspace() or t[after] in _sepchars):
                    after += 1
                spans.append((0, after + lead, reason))
                reasons[(0, after + lead)] = reason
                break
        # 情况 B: creator token 紧跟在方括号后 (如 [Jarride]xLienaEnna -> 保护 xLienaEnna)。
        # 匹配: 前一个是 ']', 且本 token 从词首到其末尾是独立 creator (非 prefix 子串)。
        else:
            ridx = t.find(pre)
            if ridx >= 0:
                # 必须是词首 (前一个非字母数字) 且后一个非字母数字 (独立 token)
                pre_ok = (ridx == 0 or (not t[ridx - 1].isalnum()))
                after = ridx + len(pre)
                post_ok = (after >= len(t) or not t[after].isalnum())
                if pre_ok and post_ok:
                    spans.append((ridx, after, reason))
                    reasons[(ridx, after)] = reason
                    break
    return spans, reasons


def glossary_resolve(segs: list):
    """整词/整 phrase 精确匹配术语表 (casefold + whole match, 禁止 substring)。

    命中即翻译, 不进模型 (resolved)。
    对 phrase 内部含独立术语 token 的 (如 sim walking near desk 里的 sim),
    不强拆词序, 而是给该 phrase 打 gloss_hint (固定术语, 模型必须原样使用),
    保证 sim/idle 等不被模型自由翻走, 同时保持自然词序 (pending)。

    返回 (resolved, pending); pending 元素为 sem 段 dict, 可能带 "gloss_hint"。
    """
    resolved = {}
    pending = []
    _WORD_RE = re.compile(r"[A-Za-z]+")
    for s in segs:
        if s["kind"] != "sem":
            continue
        stripped = s["t"].strip()
        if not stripped:
            continue
        cf = stripped.casefold()
        # 1) 整 phrase 命中 (All In One / ALL IN ONE / all in one)
        if cf in _GLOSSARY:
            resolved[s["key"]] = _GLOSSARY[cf]
            continue
        # 2) phrase 内含独立术语 token (整词, 不会伤及 Simple/Simmer/simkatu)
        hits = {}
        for tok in _WORD_RE.findall(stripped):
            tcf = tok.casefold()
            if tcf in _WORD_GLOSS:
                hits.setdefault(tcf, _WORD_GLOSS[tcf])
        if hits:
            s = dict(s)
            s["gloss_hint"] = "; ".join(f"{k}={v}" for k, v in sorted(hits.items()))
        pending.append(s)
    return resolved, pending


# ---------------- 翻译引擎 (可插拔) ----------------
class Translator:
    def translate_batch(self, items):
        """items: [(key, text), ...] -> {key: translation}。子类实现。"""
        raise NotImplementedError


class DeepSeekTranslator(Translator):
    """OpenAI 兼容 chat completions (默认 https://api.deepseek.com/v1/chat/completions)。"""
    def __init__(self):
        self.api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("YY_LLM_KEY", "")
        self.base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1/chat/completions")
        self.model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        import httpx
        self._httpx = httpx

    def translate_batch(self, items, concurrency=8, per_call=60):
        """把 items 分批, 每批一条 prompt 批量翻译。返回 {key: zh}。"""
        import concurrent.futures as cf
        results = {}
        # 分批: 每批 per_call 条, 并发 concurrency
        batches = [items[i:i + per_call] for i in range(0, len(items), per_call)]
        with cf.ThreadPoolExecutor(max_workers=concurrency) as ex:
            futs = {ex.submit(self._call, b): b for b in batches}
            for fu in cf.as_completed(futs):
                keymap, zh = fu.result()
                for k, z in zip(keymap, zh):
                    results[k] = z
        return results

    def _call(self, items):
        lines = []
        for i, (key, text) in enumerate(items):
            lines.append(f"{i}. {text}")
        prompt = (
            "你是模拟人生4动作包汉化专家。把下面的英文/多语动作姿态名翻译为简体中文。\n"
            "规则:\n"
            "1. 译文简洁自然, 符合模组姿态命名习惯。\n"
            "2. 保留原文中的数字、字母编号、版本号(V1/V2/v.2)、*anim 等技术标记, 不翻译、不改写。\n"
            "3. 每个编号行只输出一条译文, 用同样的编号序号开头, 一条一行, 不要额外说明。\n"
            "原文:\n" + "\n".join(lines)
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You translate Sims 4 pose names to simplified Chinese. Output only the numbered translations."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 2048,
        }
        try:
            r = self._httpx.post(self.base, headers={"Authorization": f"Bearer {self.api_key}"},
                                 json=payload, timeout=120)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa
            return [k for k, _ in items], [f"[ERR {e!r}]" for _ in items]
        # 解析 "i. 译文" 行 (允许 i. / i: / *) 
        out = {}
        num_pat = re.compile(r"^\s*(?:\d+[.):]|[-*])\s*(.*)$")
        for ln in content.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            m = num_pat.match(ln)
            if m:
                idx = len(out)
                out[idx] = m.group(1).strip()
        # 尽量按行序对齐 (可能缺失/多余)
        zh = []
        for i in range(len(items)):
            zh.append(out.get(i, ""))
        return [k for k, _ in items], zh


class OllamaTranslator(Translator):
    """本机 Ollama 翻译后端 (native /api/chat 为主, 结构化 JSON 严格输出)。

    生产路径: Ollama 原生 /api/chat
        think=false  (禁用思考, 译文稳定落 content)
        stream=false
        temperature=0
        format=<JSON Schema>  -> 严格返回 {"translations":[{"id":"..","zh":".."}]}
    不再依赖 reasoning/thinking 最后一行猜译文; 每批成功即 on_done() 即时 checkpoint。
    降并发仅由 transport/load 故障触发; 解析/空结果类错误 fail-fast, 绝不整批重跑全量。
    """
    # ------------------------------------------------------------------ client -
    # 统一本机 Ollama HTTP client。所有请求必须走 self.client:
    #   - trust_env=False: 绝不读系统 HTTP(S)_PROXY 代理环境变量。用户机器常配
    #     全局代理, 若走代理则本地 Ollama 请求被劫持 -> 502。这是生产必修项。
    #   - 127.0.0.1 而非 localhost: 避免 localhost 被代理/解析器劫持。
    # 禁止散用 httpx.post/get。health check / native chat / openai fallback
    # 全部经由本 client, 以保持 trust_env=False 语义一致。
    def __init__(self, base_url="http://127.0.0.1:11434", model="ni-fei:latest", api_key="ollama", timeout=300.0):
        import httpx
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        # base_url 可被外部指定为 http://localhost:11434 (旧默认)。这里统一归一为
        # 127.0.0.1, 避免 localhost 走代理/被解析器劫持。
        host = self.base_url.split("//")[-1].split("/")[0]
        if host.rstrip("/").lower() == "localhost:11434" or host == "localhost":
            self.base_url = "http://127.0.0.1:11434"
        self.client = httpx.Client(
            base_url=self.base_url,
            trust_env=False,
            timeout=timeout,
            # 本机回环, 带上简短 User-Agent, 代理/网关不会据此劫持
            headers={"User-Agent": "sims4-translator/phase2b"},
        )
        self._httpx = httpx

    # ---------------------------------------------------------------- schema -
    _SCHEMA = {
        "type": "object",
        "properties": {
            "translations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "zh": {"type": "string"},
                    },
                    "required": ["id", "zh"],
                },
            }
        },
        "required": ["translations"],
    }

    def translate_batch(self, items, concurrency=8, per_call=8, max_retry=3, on_done=None):
        """按批次并发调用 Ollama 原生 /api/chat (think=false + JSON Schema)。

        返回 {key: zh}。每个成功 phrase 校验后立即调 on_done(key, zh) (调用方负责写库 checkpoint)。
        Retry 分类:
          - transport/timeout/5xx          -> 该批可重试 (属 load/网络故障)
          - 单批 malformed (解析/schema 错) -> 仅重试当前批
          - 大面积 EMPTY/PARSE/SCHEMA 错    -> fail-fast, 绝不整批重跑
        """
        import concurrent.futures as cf
        import time
        total = len(items)
        t0 = time.time()
        print(f"[进度] 待翻译 phrase 共 {total} 个, 开始 (并发={concurrency}, 每批={per_call}) ...")
        results = {}
        attempted = 0
        ok = 0
        err_n = 0
        last_log = [0.0]

        def _log(force=False):
            now = time.time()
            if not force and now - last_log[0] < 12.0:
                return
            last_log[0] = now
            el = now - t0
            print(f"[进度] attempted={attempted} 成功={ok} 失败={err_n}  已用 {el/60:4.1f}min", flush=True)

        def _emit(k, z):
            nonlocal ok
            results[k] = z
            ok += 1
            if on_done:
                try:
                    on_done(k, z)
                except Exception as e:  # 写库失败不应中断翻译主体
                    print(f"[警告] checkpoint 写库失败 {k!r}: {e!r}", flush=True)

        batches = [items[i:i + per_call] for i in range(0, len(items), per_call)]
        # 大面积失败统计: 一旦单轮内 malformed/空结果占比过高 -> fail-fast
        round_bad = 0
        with cf.ThreadPoolExecutor(max_workers=concurrency) as ex:
            futs = {ex.submit(self._call_batch, b): b for b in batches}
            for fu in cf.as_completed(futs):
                b = futs[fu]
                try:
                    keymap, zh, st = fu.result()
                except Exception as e:  # transport/unknown: 该批重试
                    err_n += len(b)
                    attempted += len(b)
                    _log()
                    for k, _t in b:
                        results[k] = f"[ERR {e!r}]"
                    continue
                attempted += len(b)
                if st == "ok":
                    for k, z in zip(keymap, zh):
                        if z and not z.startswith("[ERR"):
                            _emit(k, z)
                        else:
                            results[k] = z or ""
                            err_n += 1
                elif st == "malformed":
                    # 单批 malformed: 仅重试当前批 (最多 max_retry 次)
                    for _a in range(max_retry):
                        try:
                            keymap2, zh2, st2 = self._call_batch(b)
                        except Exception:
                            st2, keymap2, zh2 = "err", b, []
                        if st2 == "ok":
                            for k, z in zip(keymap2, zh2):
                                if z and not z.startswith("[ERR"):
                                    _emit(k, z)
                                else:
                                    results[k] = z or ""
                                    err_n += 1
                            break
                    else:
                        for k, _t in b:
                            results[k] = "[ERR malformed-after-retry]"
                            err_n += 1
                else:  # 'empty' / 'schema' -> 算大面积 bad
                    round_bad += len(b)
                    for k, z in zip(keymap, zh):
                        results[k] = z or ""
                        err_n += 1
                _log()
        _log(force=True)

        # 大面积失败 -> fail-fast (说明 prompt/schema/模型行为有系统性问题, 整批重跑无意义)
        if round_bad and round_bad / max(1, attempted) > 0.5:
            print(f"[FATAL] 大面积解析/空结果失败 ({round_bad}/{attempted}), 判定系统性问题 fail-fast, 不再整批重跑。", flush=True)
            raise RuntimeError(f"Ollama 大面积解析失败 ({round_bad}/{attempted}); 请检查模型/prompt/JSON schema")

        el = time.time() - t0
        print(f"[进度] 完成: attempted={attempted} 成功={ok} 失败={err_n}  总耗时 {el/60:.1f}min", flush=True)
        return results

    def _call_batch(self, items):
        """调用 Ollama 原生 /api/chat, 单批。返回 (keys, zh_list, status)。

        status: 'ok' | 'malformed' | 'empty' | 'schema'
        """
        blocks = [{"id": k, "t": t} for k, t in items]
        prompt = (
            "你是模拟人生4动作包汉化专家。把下列每个 block 中 Target 行的内容翻译为简体中文。\n"
            "规则:\n"
            "1. 只翻译每个 block 的 Target 行, 只输出最终中文, 不解释、不思考过程。\n"
            "2. Context 仅供理解, 不翻译、不输出。\n"
            "3. 若含『固定术语』行, 译文中必须原样嵌入这些指定中文, 不得用同义词。\n"
            "4. 保留数字、字母编号、版本号(V1/v2)、*anim 等技术标记不变。\n"
            "5. 严格按 JSON Schema 输出 translations 数组, 每项 id 用给定 id, zh 为译文。\n"
            "输入 blocks:\n" + "\n".join(f"id={k}\nTarget: {t}\n" for k, t in items)
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You translate Sims 4 pose names to simplified Chinese. Output strictly as JSON matching the provided schema."},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "think": False,
            "format": self._SCHEMA,
            "options": {"temperature": 0.0, "num_predict": 1024},
        }
        url = "/api/chat"
        # 统一走 self.client (trust_env=False), 不读系统代理
        r = self.client.post(url, json=payload)
        if r.status_code >= 500 or r.status_code == 429:
            raise RuntimeError(f"Ollama transport/load: HTTP {r.status_code}")
        r.raise_for_status()
        j = r.json()
        content = ((j.get("message") or {}).get("content") or "").strip()
        if not content:
            # 关 thinking 后仍无 content -> 空结果
            return [k for k, _ in items], ["" for _ in items], "empty"
        try:
            data = json.loads(content) if isinstance(content, str) else content
        except Exception:
            return [k for k, _ in items], [content] * len(items), "malformed"
        tr = data.get("translations")
        if not isinstance(tr, list):
            return [k for k, _ in items], [], "schema"
        by_id = {}
        for item in tr:
            if isinstance(item, dict) and item.get("id") is not None:
                by_id[str(item.get("id"))] = str(item.get("zh") or "").strip()
        keys = [k for k, _ in items]
        # 确定性后处理: 剥离行首 Target: 前缀, 再写 cache (fresh model result)
        zhs = [normalize_model_output(by_id.get(str(k), "")) for k, _ in items]
        # 空/缺太多 -> malformed (交由上层 retry 该批)
        if sum(1 for z in zhs if z) < len(zhs) * 0.5:
            return keys, zhs, "malformed"
        return keys, zhs, "ok"

    def _call_native(self, items):
        """兼容旧调用: 逐条通过结构化批调用。返回 (keys, zh)。"""
        keys, zhs = [], []
        for k, t in items:
            try:
                _, zh, st = self._call_batch([(k, t)])
                zhs.append(zh[0] if zh else "")
            except Exception as e:  # noqa
                zhs.append(f"[ERR {e!r}]")
            keys.append(k)
        return keys, zhs


class NoopTranslator(Translator):
    def translate_batch(self, items):
        return {k: "" for k, _ in items}


# ---------------- 主流程 ----------------
def load_todo():
    rows = []
    with open(TODO, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            # workset/manifest 无 decision 列时, 缺省为 TRANSLATE (增量层已冻结决策)
            if not (r.get("decision") or "").strip():
                r["decision"] = "TRANSLATE"
            if not (r.get("detected_language") or "").strip():
                try:
                    r["detected_language"] = detect_language(
                        norm_text(r.get("source_text") or ""), "TRANSLATE", "") or ""
                except Exception:
                    r["detected_language"] = ""
            rows.append(r)
    return rows


def load_contexts(out_dir):
    """从 translation_contexts.csv 按 translation_id 聚合上下文 (package + neighbor)。
    返回 {translation_id: [ctx_str, ...]}。文件缺失则返回空 (context 仅辅助, 不写回)。
    """
    p = out_dir / "translation_contexts.csv"
    agg = {}
    if not p.exists():
        return agg
    with open(p, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            tid = r.get("translation_id")
            if not tid:
                continue
            pkg = (r.get("package_path") or "").strip()
            nbr = (r.get("neighbor_display_texts") or "").strip()
            parts = []
            if pkg:
                parts.append(f"package={pkg}")
            if nbr:
                parts.append(f"neighbors={nbr}")
            if parts:
                agg.setdefault(tid, []).append("; ".join(parts))
    return agg


def sanity_check_lang(todo):
    """对 todo 每条: 用最新 detect_language 重测, 与 todo.detected_language 对比。

    一致 -> 通过; 不一致 -> 标 LANGUAGE_MISMATCH (不覆盖 todo 值, 仅报警)。
    返回 mismatch 列表 [(translation_id, source_text, todo_lang, re_lang)]。
    """
    mismatch = []
    for r in todo:
        text = norm_text(r.get("source_text"))
        todo_lang = (r.get("detected_language") or "").strip()
        cls = r.get("decision")
        reason = r.get("reason") or ""
        try:
            re_lang = detect_language(text, cls, reason)
        except Exception:
            re_lang = ""
        if re_lang and re_lang != todo_lang and todo_lang not in ("", "zxx"):
            mismatch.append((r.get("translation_id"), text, todo_lang, re_lang))
    return mismatch


def main():
    assert TODO.exists(), f"未找到 {TODO}"
    todo = load_todo()
    print(f"[输入] todo 行数 = {len(todo)}")

    # ---- 作用域: CLI 定位 (--id / --id-from-file / --regex / --category) 必须在任何
    #      classification / override / translation / write 之前, 先把 todo 裁成目标 tid 集。
    #      否则分类与 done 都会看到整份 todo (泄出其它 batch)。----
    if ONLY_ID or ONLY_REGEX or ONLY_CATEGORY:
        _idreq = set(x.strip() for x in ONLY_ID.split(",")) if ONLY_ID else None
        _rxreq = re.compile(ONLY_REGEX) if ONLY_REGEX else None
        _scoped = []
        for r in todo:
            tid = r.get("translation_id", "")
            src = norm_text(r.get("source_text", ""))
            cat = (r.get("category") or r.get("decision") or "")
            if _idreq is not None and tid not in _idreq:
                continue
            if _rxreq is not None and not _rxreq.search(src):
                continue
            if ONLY_CATEGORY is not None and cat != ONLY_CATEGORY:
                continue
            _scoped.append(r)
        _req = set(r.get("translation_id", "") for r in todo)
        if _idreq is not None:
            _missing = _idreq - _req
            if _missing:
                raise SystemExit(f"[HARD-FAIL] requested tid 不存在于 todo: {sorted(_missing)[:10]}")
        todo = _scoped
        print(f"[scope] todo 裁剪: 全量-> {len(todo)} 行 (CLI 定位)")
        assert _idreq is None or set(r.get('translation_id') for r in todo) <= _idreq, "scope 泄出 requested tid"
        print(f"[invariant] requested={len(_idreq) if _idreq else 'regex'} scoped-todo={len(todo)}  output⊆requested")

    # ---- authoritative workset: 当 --todo 是 frozen manifest (带权威 decision),
    #      不得用老 classifier 把 authoritative TRANSLATE 重新判成 KEEP/DONE_SKIP。----
    AUTHORITATIVE = AUTHORITATIVE_DECISION_ON
    if AUTHORITATIVE:
        _auth_tr = 0
        for r in todo:
            _d = (r.get("decision") or "").strip()
            if _d in ("TRANSLATE", "FULL_TRANSLATE", "PARTIAL_TRANSLATE"):
                _auth_tr += 1
        print(f"[authoritative] 权威 decision=TRANSLATE 行数 = {_auth_tr} (老 classifier 不得改判 KEEP)")

    # 语言检测 sanity check (只报不一致, 不覆盖 todo 值)
    mism = sanity_check_lang(todo)
    if mism:
        print(f"[LANGUAGE_MISMATCH] {len(mism)} 条 todo.detected_language 与重测不一致 (未覆盖):")
        for tid, txt, tl, rl in mism[:20]:
            print(f"    {tid}  {txt!r}  todo={tl}  re={rl}")
        print("    建议重跑最新 Phase 2A 重新生成 todo; 本脚本不会静默改写输入。")

    # 上下文 (仅辅助理解, 不参与写回定位)
    ctx_map = load_contexts(out_dir)
    nbr_ok = sum(1 for r in todo if r.get("translation_id") in ctx_map)
    print(f"[上下文] translation_contexts.csv 关联 TODO {nbr_ok}/{len(todo)} 行 (仅辅助, 不写回)")

    # 人工 override (优先级最高; 不写 cache; 不写 .package)
    ovr = load_overrides(out_dir)
    _ovsrc = OVERRIDES_PATH if OVERRIDES_PATH else (Path(out_dir) / "translation_overrides.csv")
    if ovr:
        n_o = sum(1 for r in todo if override_for(ovr, r.get("translation_id"), norm_text(r.get("source_text"))))
        src = "production overlay" if OVERRIDES_PATH else "frozen translation_overrides.csv"
        print(f"[override] 加载 {len(ovr)} 条 [{src}] ({_ovsrc}), 命中 todo {n_o} 行 (TRANSLATE/KEEP 终态, REVIEW 挂起)")

    # ---- retry preflight invariant (仅确认 loader, 不调用模型) ----
    # 不再硬编码 145/38: 期望值从当前 production overlay / retry manifest 实际推导。
    #   requested/scoped/unique        = retry manifest 行数 (本次 todo)
    #   production_overrides_loaded    = production overlay 实际加载行数 (len(ovr))
    #   terminal_KEEP_hit / manual_final_hit = 本次 todo 命中 KEEP/manual 终态的行数 (应=0)
    #   authoritative_TRANSLATE        = 本次 todo 中权威 TRANSLATE 行数 (应==retry 行数)
    _req_tids = set(x.strip() for x in ONLY_ID.split(",")) if ONLY_ID else set(r.get("translation_id", "") for r in todo)
    _scope_tids = set(r.get("translation_id", "") for r in todo)
    _uniq = len(_scope_tids)
    _keep_hit = 0
    _man_hit = 0
    _auth_tr = 0
    for r in todo:
        tid = r.get("translation_id", "")
        o = override_for(ovr, tid, norm_text(r.get("source_text", "")))
        if o:
            if o["action"] == "KEEP":
                _keep_hit += 1
            else:
                _man_hit += 1
        if (r.get("decision") or "").strip() in ("TRANSLATE", "FULL_TRANSLATE", "PARTIAL_TRANSLATE"):
            _auth_tr += 1
    print(f"[preflight] requested={len(_req_tids)} scoped={len(_scope_tids)} unique={_uniq} "
          f"production_overrides_loaded={len(ovr) if OVERRIDES_PATH else 0} "
          f"terminal_KEEP_hit={_keep_hit} manual_final_hit={_man_hit} authoritative_TRANSLATE={_auth_tr}")
    # 硬预检 (自洽, 非魔数): 当 --overrides 传 production overlay 且本次是 retry 批时:
    #   - retry 批不得含任何 KEEP/manual 终态 (它们已人工定稿, 不进 retry)
    #   - 权威 TRANSLATE 行数必须等于 retry 批行数 (每条 retry 都必是权威 TRANSLATE)
    #   - requested 必须全部在 scoped 中 (scope 不丢行)
    if OVERRIDES_PATH and _req_tids == _scope_tids and _scope_tids:
        _missing_scope = _req_tids - _scope_tids
        exp = {"terminal_KEEP_hit": 0, "manual_final_hit": 0, "authoritative_TRANSLATE": len(_req_tids)}
        got = {"terminal_KEEP_hit": _keep_hit, "manual_final_hit": _man_hit, "authoritative_TRANSLATE": _auth_tr}
        bad = [k for k in exp if got.get(k) != exp[k]]
        if _missing_scope:
            print(f"[PREFLIGHT-FAIL] requested 含 scope 外 tid: {sorted(_missing_scope)}", file=sys.stderr)
            sys.exit(4)
        if bad:
            print(f"[PREFLIGHT-FAIL] 校验未过: { {k: (got.get(k), '期望', exp[k]) for k in bad} }", file=sys.stderr)
            sys.exit(4)
        print(f"[preflight] production overlay={len(ovr)} 校验 PASS "
              f"(terminal KEEP/manual 不入 retry; authoritative TRANSLATE={_auth_tr}==retry {len(_req_tids)})")

    # 逐行翻译层决策
    decided = []
    for r in todo:
        text = norm_text(r.get("source_text"))
        tid = r.get("translation_id")
        dec = r.get("decision")
        lang = r.get("detected_language")
        # override 优先于 APPROVED/cache/LLM
        o = override_for(ovr, tid, text)
        if o and o["action"] in ("TRANSLATE", "KEEP"):
            if o["action"] == "TRANSLATE":
                if not o["translation"]:
                    # TRANSLATE 缺少译文: 视为无效, 走正常流程
                    print(f"[override] {tid} {text!r}: TRANSLATE 缺 translation, 走正常流程", file=sys.stderr)
                else:
                    decided.append((r, "OVERRIDE_T", o["translation"], "DONE", lang))
                    continue
            else:  # KEEP
                if AUTHORITATIVE_DECISION_ON and (r.get("decision") or "").strip() in ("TRANSLATE", "FULL_TRANSLATE", "PARTIAL_TRANSLATE"):
                    raise SystemExit(
                        f"[POLICY-CONFLICT] tid {tid} authoritative decision=TRANSLATE, "
                        f"但 override 声称 KEEP ({text!r}). 权威 workset 不得被静默 KEEP。")
                decided.append((r, "OVERRIDE_K", "", "KEEP", lang))
                continue
        if text in APPROVED_TEXT:
            zh, alang = APPROVED[text]
            mode, sem, toks = "APPROVED", [text], []
            status = "APPROVED"
            decided.append((r, mode, zh, status, alang))
            continue
        mode, sem, toks = translate_mode_for(text)
        _auth_tr = AUTHORITATIVE_DECISION_ON and (r.get("decision") or "").strip() in ("TRANSLATE", "FULL_TRANSLATE", "PARTIAL_TRANSLATE")
        if mode == "KEEP" and _auth_tr:
            # 权威 workset 明确 TRANSLATE, 老 classifier 不得改判 KEEP/DONE_SKIP:
            # 强制进翻译流程 (无 ID/编号需保留 -> FULL; 有 -> PARTIAL)
            _ids_kept = [tk for tk in toks if not (tk.islower() and len(tk) == 1)]
            mode = "FULL_TRANSLATE" if not _ids_kept else "PARTIAL_TRANSLATE"
            decided.append((r, mode, None, "PENDING", lang))  # 权威强制翻译
        elif mode == "KEEP":
            decided.append((r, mode, "", "DONE_SKIP", lang))
        else:
            decided.append((r, mode, None, "PENDING", lang))  # 待翻译

    # 分层抽样 (用于抽查; 若指定 --sample 则只翻这批)
    mode_cnt = Counter(d[1] for d in decided)
    print("\n[翻译层决策] " + "  ".join(f"{k}={v}" for k, v in mode_cnt.most_common()))

    need_translate = [d for d in decided if d[3] == "PENDING"]
    print(f"[待翻译] {len(need_translate)} 行 (FULL+PARTIAL)")

    sample_pick = None
    if SAMPLE:
        # 分层 + 强制覆盖: 术语表现(方向/情绪/人物), *anim/编号, 非英语, 纯语义, 歧义短句
        import random
        random.seed(20260813)
        need = need_translate[:]
        def has_gloss(s):
            low = norm_text(s).lower()
            return any(k in low for k in _GLOSSARY if len(k) > 1)
        def is_short(s):
            return len(norm_text(s).split()) <= 4
        nonen = [d for d in need if d[0].get("detected_language") not in ("en", "zxx")]
        gloss = [d for d in need if has_gloss(d[0].get("source_text", "") or "")]
        proto = [d for d in need if re.search(r"\*anim|\d", d[0].get("source_text", "") or "")]
        short = [d for d in need if is_short(d[0].get("source_text", "") or "")]
        forced = []
        def put(ds):
            for d in ds:
                if len(forced) >= SAMPLE:
                    return
                if d not in forced:
                    forced.append(d)
        put(nonen); put(gloss); put(proto); put(short)
        pool = [d for d in need if d not in forced]
        random.shuffle(pool)
        pick = forced + pool[: max(0, SAMPLE - len(forced))]
        sample_pick = set(id(d) for d in pick)
        need_translate = [d for d in need if id(d) in sample_pick]
        _n_proto = sum(1 for d in need_translate if re.search(r"[*]anim|\d", d[0].get("source_text", "") or ""))
        print(f"[抽样] 抽查 {len(need_translate)} 行 "
              f"(术语表={len([d for d in need_translate if has_gloss(d[0].get('source_text','') or '')])}, "
              f"非英语={len([d for d in need_translate if d[0].get('detected_language') not in ('en','zxx')])}, "
              f"编号/*anim={_n_proto})")

    # ---- 定位/增量过滤 (CLI): 只处理用户指定的行, 其余不进引擎 ----
    if ONLY_ID or ONLY_REGEX or ONLY_CATEGORY or ONLY_CHANGED:
        before = len(need_translate)
        filt = []
        id_set = set(x.strip() for x in ONLY_ID.split(",")) if ONLY_ID else None
        rx = re.compile(ONLY_REGEX) if ONLY_REGEX else None
        for d in need_translate:
            r = d[0]
            tid = r.get("translation_id", "")
            src = norm_text(r.get("source_text", ""))
            cat = (r.get("category") or r.get("decision") or "")
            if id_set is not None and tid not in id_set:
                continue
            if rx is not None and not rx.search(src):
                continue
            if ONLY_CATEGORY is not None and cat != ONLY_CATEGORY:
                continue
            filt.append(d)
        need_translate = filt
        print(f"[过滤] CLI 定位: {before} -> {len(need_translate)} 行 "
              + (f"(id={ONLY_ID} regex={ONLY_REGEX} cat={ONLY_CATEGORY})" if (ONLY_ID or ONLY_REGEX or ONLY_CATEGORY) else "(only-changed 语义由 cache 指纹保证)"))

    # ---- 作用域 (scope): 当使用 CLI 定位时, done 只写命中的 tid,
    #      而非整份 todo。否则 --done 会泄出其它 batch 的行 (PENDING 空译文),
    #      破坏按批隔离/union-dup 校验。未启用过滤时 scope=None = 全量。 ----
    scope_tids = None
    if ONLY_ID or ONLY_REGEX or ONLY_CATEGORY:
        id_set2 = set(x.strip() for x in ONLY_ID.split(",")) if ONLY_ID else None
        rx2 = re.compile(ONLY_REGEX) if ONLY_REGEX else None
        sset = set()
        for r, _m, _z, _st, _l in decided:
            tid = r.get("translation_id", "")
            src = norm_text(r.get("source_text", ""))
            cat = (r.get("category") or r.get("decision") or "")
            if id_set2 is not None and tid not in id_set2:
                continue
            if rx2 is not None and not rx2.search(src):
                continue
            if ONLY_CATEGORY is not None and cat != ONLY_CATEGORY:
                continue
            sset.add(tid)
        if sset:
            scope_tids = sset
            print(f"[scope] done 仅写 {len(scope_tids)} 个命中 tid (其余不写出)")

    # 翻译引擎: 默认本机 Ollama (并发/批大小可由 CLI 覆盖)
    if NO_LLM or (SAMPLE is None and len(need_translate) == 0):
        eng = NoopTranslator()
    elif globals().get("FAKE_FORCE"):
        eng = globals()["FAKE_FORCE"]()
        print(f"[引擎] FakeTranslator (回归用, 不调 LLM)")
    else:
        eng = OllamaTranslator()
        print(f"[引擎] {type(eng).__name__} @ {eng.base_url}/v1/chat/completions (model={eng.model}) "
              f"并发={CONCURRENCY} 批大小={BATCH_SIZE}")

    # ---- phrase-level 翻译准备: 每行切 phrase -> glossary 直译 -> 剩余交模型(带 context) -> rebuild ----
    jobs = {}
    n_modelfree = 0   # 该行所有 phrase 均由 glossary 直译 (零模型调用)
    n_needmodel = 0   # 该行至少 1 个 phrase 需交模型
    n_phrase_total = 0
    for d in need_translate:
        r = d[0]
        tid = r.get("translation_id")
        ctx = ctx_map.get(tid, [])
        # BUG4: PACK_TITLE creator/identifier 保护先于 pending 判定; 被保护段 -> prot,
        #       不 required_translate、不产生 echo 假阳性, rebuild 原样保留。
        _prot, _ = title_creator_protection(r.get("source_text"))
        segs, sem = split_semantic_spans(r.get("source_text"), force_prot_spans=_prot)
        gloss, pending = glossary_resolve(segs)
        # 过滤纯空白 phrase (切分残留), 不交模型
        pending = [p for p in pending if p["t"].strip()]
        n_phrase_total += len(pending)
        if pending:
            n_needmodel += 1
        else:
            n_modelfree += 1
        ctx_str = " | ".join(ctx[:3]) if ctx else ""
        for p in pending:
            p["ctx"] = ctx_str
        jobs[tid] = {"segs": segs, "gloss": gloss, "pending": pending}

    # 精确分类 breakdown (必须严格等于 todo 行数)
    _appr = sum(1 for d in decided if d[1] == "APPROVED")
    _keep = sum(1 for d in decided if d[1] == "KEEP")
    _full = sum(1 for d in decided if d[1] == "FULL_TRANSLATE")
    _part = sum(1 for d in decided if d[1] == "PARTIAL_TRANSLATE")
    print("[计数] TOTAL={}  APPROVED={}  KEEP={}  PARTIAL={}  FULL={}  | 校验: TOTAL==({}+{}+{}+{})".format(
        len(decided), _appr, _keep, _part, _full, _appr, _keep, _part, _full))
    print("[计数] 待翻译(PENDING=FULL+PARTIAL)={}  | 其中 glossary 直译(零模型)={}  需调模型(≥1 phrase)={}  总 phrase={}".format(
        len(need_translate), n_modelfree, n_needmodel, n_phrase_total))
    print("[计数] 校验 待翻译 == glossary直译 + 需调模型 : {} == {}".format(
        len(need_translate), n_modelfree + n_needmodel))

    # 构建一条待翻译 = 单个 pending phrase + 其 context (便于逐 phrase 映射回填)
    phrase_items = []          # (composite_key, block_text)
    phrase_map = {}            # composite_key -> (tid, orig_key, source_phrase, fingerprint, gloss_hint)
    for tid, j in jobs.items():
        for p in j["pending"]:
            ck = f"{tid}:::{p['key']}"
            block = f"Target: {p['t'].strip()}"
            if p.get("gloss_hint"):
                block += f"\n固定术语(译文中必须原样使用): {p['gloss_hint']}"
            if p.get("ctx"):
                block += f"\nContext: {p['ctx']}"
            fp = build_fingerprint(
                source_phrase=p["t"].strip(),
                glossary_hint=p.get("gloss_hint", ""),
                context=p.get("ctx", ""),
            )
            phrase_items.append((ck, block))
            phrase_map[ck] = (tid, p["key"], p["t"].strip(), fp, p.get("gloss_hint", "") or "")

    # ---- 缓存感知翻译: fingerprint hit->复用(不调 Ollama); miss->调模型并立即写库 ----
    phrase_res = {}
    phrase_failures = {}   # ck -> error (completion gate + 持久化失败报告用)
    cache = PhraseCache(out_dir, model=getattr(eng, "model", None))
    cache_echo_rejected = 0   # BUG1: invalid echo cache entry 不计 hit, 视为 miss 重翻
    try:
        n_hit = n_miss = 0
        todo_items = []      # (ck, block) 需送模型的 (fingerprint miss)
        for ck, block in phrase_items:
            tid, orig_key, src_phrase, fp, gh = phrase_map[ck]
            cached = cache.get(fp)
            _echo = False
            if cached and not FORCE:
                _cand = normalize_model_output(cached["translation"])
                # BUG1 修复: cached 译文 == normalized source phrase -> invalid echo cache entry,
                # 不计 hit、不 materialize、视为 miss 重翻 (物理 row 保留, 读层忽略)。
                if _cand.strip() and _cand.strip() == (src_phrase or "").strip():
                    _echo = True
            if cached and not FORCE and not _echo:
                # 确定性后处理: 剥离行首 Target: 前缀 (缓存里可能存了脏值, 0 LLM 重新清洗)
                phrase_res.setdefault(tid, {})[orig_key] = normalize_model_output(cached["translation"])
                n_hit += 1
            else:
                if _echo:
                    cache_echo_rejected += 1
                todo_items.append((ck, block))
                n_miss += 1

        if n_hit:
            print(f"[缓存] 命中复用 {n_hit} 个 phrase (不调模型)")
        if cache_echo_rejected:
            print(f"[缓存] 拒绝 echo cache entry {cache_echo_rejected} 个 -> 视为 miss 重翻 (invalid echo cache)")
        if todo_items:
            print(f"[翻译] 调用引擎 {type(eng).__name__}, 新翻译 {len(todo_items)} 个 phrase (cache miss) ...")
            import time as _t
            now = _t.strftime("%Y-%m-%d %H:%M:%S")
            # 每个成功 phrase 校验后立即 callback 写库 + commit (checkpoint/resume 根本);
            # 由引擎在每批完成时同步调用, 崩溃/重启用库内已落盘条目续跑。
            # 失败 phrase (含模型 echo) 不入 cache, 记入 fail_map 供 completion gate + 持久化报告。
            committed = set()
            fail_map = {}   # ck -> error 字符串 {completion gate 用; 也写持久化报告}
            def _record_fail(ck, err):
                nonlocal fail_map
                if not err:
                    return
                if ck in committed or ck in fail_map:
                    return
                if ck not in phrase_map:
                    return
                fail_map[ck] = err
            def _on_done(ck, z):
                if ck in committed:
                    return
                if ck not in phrase_map:
                    return
                z = normalize_model_output(str(z or "").strip())
                tid, orig_key, src_phrase, fp, gh = phrase_map[ck]
                # echo 保护: 模型返回 == 原文且 phrase 带可译 semantic token -> 记失败, 不缓存
                if z and z == src_phrase:
                    _record_fail(ck, "ECHO")
                    return
                if not z or z.startswith("[ERR"):
                    _record_fail(ck, z or "EMPTY")
                    return
                committed.add(ck)
                cache.put(
                    fingerprint=fp, translation_id=tid, segment_index=int(orig_key),
                    source_phrase=src_phrase, source_hash=source_hash(src_phrase),
                    translation=z, now=now)
                phrase_res.setdefault(tid, {})[orig_key] = z

            raw = {}
            if hasattr(eng, "translate_batch"):
                try:
                    raw = eng.translate_batch(
                        todo_items, concurrency=CONCURRENCY, per_call=BATCH_SIZE, on_done=_on_done)
                except TypeError:
                    # 老接口 (FakeTranslator / NoopTranslator) 不接受 on_done: 退回后置补写
                    raw = eng.translate_batch(todo_items)
                    for ck, out_txt in (raw or {}).items(): _on_done(ck, out_txt)
            else:
                raw = eng.translate_batch(todo_items)
                for ck, out_txt in (raw or {}).items(): _on_done(ck, out_txt)
            # 引擎返回值里未被 _on_done 采纳的 [ERR/空/echo] → 统一记入 fail_map (持久化失败明细)
            for ck, out_txt in (raw or {}).items():
                if ck not in phrase_map:
                    continue
                if ck in committed:
                    continue
                z = normalize_model_output(str(out_txt or "").strip())
                tid, orig_key, src_phrase, fp, gh = phrase_map[ck]
                if z and z == src_phrase:
                    _record_fail(ck, "ECHO")
                elif (not z) or z.startswith("[ERR"):
                    _record_fail(ck, z or "EMPTY")
            print("[翻译] 完成。")
            # 持久化失败报告 (本次运行独立文件, 不靠 cache 反推)
            if fail_map:
                _batch_label = DONE.stem
                if _batch_label.startswith("translation_done_"):
                    _batch_label = _batch_label[len("translation_done_"):]
                fail_path = out_dir / f"translation_phrase_failures_{_batch_label or 'default'}.csv"
                with open(fail_path, "w", encoding="utf-8-sig", newline="") as _f:
                    _w = csv.writer(_f)
                    _w.writerow(["translation_id", "segment_index", "source_phrase", "error"])
                    for ck, err in sorted(fail_map.items()):
                        tid, orig_key, src_phrase, fp, gh = phrase_map[ck]
                        _w.writerow([tid, orig_key, src_phrase, err])
                print(f"[失败报告] 持久化 {len(fail_map)} 个 unresolved failed phrase -> {fail_path}")
            else:
                print("[失败报告] 本运行 0 个 unresolved failed phrase")
            # 把 fail_map 暴露给 completion gate
            phrase_failures.update(fail_map)
            print(f"[缓存] 本轮: hit={n_hit} miss={n_miss} 库总条数={cache.count()}")
    except Exception:
        cache.close()
        raise

    # 组装 done; 含 protected_spans (PARTIAL 时列出需原样保留的 token 在原文本中的区间)
    done_cols = ["translation_id", "source_text", "decision", "translate_mode",
                 "detected_language", "protected_spans", "translation", "status", "source_hash"]
    done_rows = []
    try:
        for r, mode, zh, status, lang in decided:
            if scope_tids is not None and r.get("translation_id") not in scope_tids:
                continue  # CLI 定位下只写命中 tid, 不泄其它 batch
            text = norm_text(r.get("source_text"))
            if mode in ("OVERRIDE_T", "OVERRIDE_K"):
                # 人工 override 终态 (不写 cache, 不写 .package)
                if mode == "OVERRIDE_T":
                    translation = zh; status = "DONE"
                else:  # OVERRIDE_K
                    translation = ""; status = "KEEP"
            elif mode == "APPROVED":
                translation = zh
            elif mode == "KEEP":
                translation = ""
                status = "DONE_SKIP"
            elif r.get("translation_id") in jobs:
                j = jobs[r.get("translation_id")]
                resolved = dict(j["gloss"])
                resolved.update(phrase_res.get(r.get("translation_id"), {}))
                translation = rebuild(j["segs"], resolved)
                translation = restore_protected(j["segs"], translation)
                # ===== completion gate (2026-08-15 裁决, segment-level) =====
                # 1) 任一 model-required phrase unresolved/engine failed -> QA_FAIL/PENDING,
                #    禁止原文 fallback 后仍 DONE。
                # 2) semantic 最终译文 == semantic source (模型 echo / 缓存 echo) -> QA_FAIL/PENDING,
                #    模型回显/未变化不叫成功。
                # 3) 仅 protected/glossary/明确 non-semantic terminal evidence 才允许 unchanged DONE/KEEP。
                # 4) BUG2 修复: 逐 model-required semantic seg 判定 (不是只比整行),
                #    每个需要翻译的 semantic segment 都必须 resolved 且 resolved != source segment。
                # 5) BUG3 修复: authoritative TRANSLATE + 整行 unchanged + 无 terminal
                #    KEEP/manual evidence -> QA_FAIL (authoritative 不能因"无 pending phrase"
                #    就 unchanged+DONE)。
                row_has_failed_phrase = any(
                    f"{r.get('translation_id')}:::{p['key']}" in phrase_failures
                    for p in j["pending"]
                )
                # BUG2: 未 resolved 的 model-required semantic seg
                bad_seg = []
                for _p in j["pending"]:
                    _rv = resolved.get(_p["key"])
                    _src = _p["t"].strip()
                    if _rv is None or not str(_rv).strip():
                        bad_seg.append((_p["key"], _src, "UNRESOLVED"))
                    elif str(_rv).strip() == _src:
                        bad_seg.append((_p["key"], _src, "ECHO"))
                status = "DONE"
                if row_has_failed_phrase or bad_seg:
                    status = "QA_FAIL"
                elif not translation.strip():
                    status = "PENDING"
                else:
                    # 整行 unchanged 且有可译 semantic -> 仍 QA_FAIL (echo 守底)
                    if translation.strip() == text.strip() and j["pending"]:
                        status = "QA_FAIL"
                    else:
                        # BUG3: authoritative TRANSLATE + 整行 unchanged + 无 terminal evidence
                        _row_unchanged = translation.strip() == text.strip()
                        _term_evidence = bool(j["pending"]) and not bad_seg
                        if (
                            AUTHORITATIVE_DECISION_ON
                            and _row_unchanged
                            and (r.get("decision") or "").strip()
                            in ("TRANSLATE", "FULL_TRANSLATE", "PARTIAL_TRANSLATE")
                            and not _term_evidence
                        ):
                            status = "QA_FAIL"
            else:
                # 该行本次未进引擎 (被过滤/缺席 jobs): 仍从 cache 物化 (materialized output)
                translation, status = materialize_from_cache(r.get("translation_id"), text, mode, cache, ctx_map)
                if translation is None:
                    translation = ""
                    status = "PENDING"
                elif status == "DONE" and mode in ("PARTIAL_TRANSLATE", "FULL_TRANSLATE"):
                    # echo 保护: 缓存物化后译文==原文且该行有可译 semantic -> 判定 QA_FAIL
                    # (旧 buggy 运行可能把 echo 写进 cache; 不能因 cache 有值就标 DONE)
                    _chg = translation.strip() and translation.strip() == text.strip()
                    if _chg:
                        status = "QA_FAIL"
            psp = protected_spans(text) if mode == "PARTIAL_TRANSLATE" else ""
            done_rows.append({
                "translation_id": r.get("translation_id"),
                "source_text": text,
                "decision": "TRANSLATE" if text in APPROVED_TEXT else r.get("decision"),
                "translate_mode": "APPROVED" if mode == "APPROVED" else mode,
                "detected_language": lang,
                "protected_spans": psp,
                "translation": translation,
                "status": status,
                "source_hash": r.get("source_hash"),
            })
        with open(DONE, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=done_cols)
            w.writeheader(); w.writerows(done_rows)
        print(f"[写出] {DONE}  ({len(done_rows)} 行)")
        # BUG1/BUG2/BUG3 结果汇总 (user 报告同款指标)
        _st = [d["status"] for d in done_rows]
        _n_done = _st.count("DONE") + _st.count("APPROVED")
        _n_fail = _st.count("QA_FAIL")
        _n_uniq = len({d["translation_id"] for d in done_rows})
        _n_empty = sum(1 for d in done_rows if not (d.get("translation") or "").strip())
        _n_same = sum(1 for d in done_rows if (d.get("translation") or "").strip()
                      and (d.get("translation") or "").strip() == (d.get("source_text") or "").strip())
        print(f"[结果] rows={len(done_rows)}  DONE={_n_done}  QA_FAIL={_n_fail}  "
              f"unique={_n_uniq}  empty={_n_empty}  sameAsSource={_n_same}  "
              f"cache_echo_rejected={cache_echo_rejected}")
    finally:
        cache.close()

    if SAMPLE:
        samp = out_dir / "translation_sample_zh.csv"
        with open(samp, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=done_cols)
            w.writeheader()
            for d in done_rows:
                if d["status"] in ("DONE", "APPROVED") and d["translate_mode"] != "KEEP":
                    w.writerow(d)
        print(f"[抽样] {samp}")


if __name__ == "__main__":
    main()
