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
        translate_mode = FULL_TRANSLATE | PARTIAL_TRANSLATE | KEEP
        decision       = TRANSLATE | REVIEW (7 条人工审批后写 TRANSLATE)
        status         = APPROVED (7 条人工审批) | DONE (翻译完成) | DONE_SKIP (KEEP)
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
CONCURRENCY = int(_flag_val("--concurrency", "8"))
BATCH_SIZE = int(_flag_val("--batch-size", "8"))

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


def split_semantic_spans(text: str):
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
    sem_phrases = [s for s in segs if s["kind"] == "sem"]
    return segs, sem_phrases


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
    segs, _ = split_semantic_spans(text)
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
        # 确定性后处理: 剥离行首 Target: 前缀 (缓存里可能存了脏值, 0 LLM 重新清洗)
        resolved[p["key"]] = normalize_model_output(hit["translation"])
    translation = rebuild(segs, resolved)
    translation = restore_protected(segs, translation)
    if translation.strip():
        return translation, "DONE"
    return None, "PENDING"


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

    # 逐行翻译层决策
    decided = []
    for r in todo:
        text = norm_text(r.get("source_text"))
        tid = r.get("translation_id")
        dec = r.get("decision")
        lang = r.get("detected_language")
        if text in APPROVED_TEXT:
            zh, alang = APPROVED[text]
            mode, sem, toks = "APPROVED", [text], []
            status = "APPROVED"
            decided.append((r, mode, zh, status, alang))
            continue
        mode, sem, toks = translate_mode_for(text)
        if mode == "KEEP":
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
        segs, sem = split_semantic_spans(r.get("source_text"))
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
    cache = PhraseCache(out_dir, model=getattr(eng, "model", None))
    try:
        n_hit = n_miss = 0
        todo_items = []      # (ck, block) 需送模型的 (fingerprint miss)
        for ck, block in phrase_items:
            tid, orig_key, src_phrase, fp, gh = phrase_map[ck]
            cached = cache.get(fp)
            if cached and not FORCE:
                # 确定性后处理: 剥离行首 Target: 前缀 (缓存里可能存了脏值, 0 LLM 重新清洗)
                phrase_res.setdefault(tid, {})[orig_key] = normalize_model_output(cached["translation"])
                n_hit += 1
            else:
                todo_items.append((ck, block))
                n_miss += 1

        if n_hit:
            print(f"[缓存] 命中复用 {n_hit} 个 phrase (不调模型)")
        if todo_items:
            print(f"[翻译] 调用引擎 {type(eng).__name__}, 新翻译 {len(todo_items)} 个 phrase (cache miss) ...")
            import time as _t
            now = _t.strftime("%Y-%m-%d %H:%M:%S")
            # 每个成功 phrase 校验后立即 callback 写库 + commit (checkpoint/resume 根本);
            # 由引擎在每批完成时同步调用, 崩溃/重启用库内已落盘条目续跑。
            committed = set()
            def _on_done(ck, z):
                if ck in committed:
                    return
                if ck not in phrase_map:
                    return
                z = str(z or "").strip()
                if not z or z.startswith("[ERR"):
                    return
                committed.add(ck)
                tid, orig_key, src_phrase, fp, gh = phrase_map[ck]
                cache.put(
                    fingerprint=fp, translation_id=tid, segment_index=int(orig_key),
                    source_phrase=src_phrase, source_hash=source_hash(src_phrase),
                    translation=z, now=now)
                phrase_res.setdefault(tid, {})[orig_key] = z

            if hasattr(eng, "translate_batch"):
                try:
                    raw = eng.translate_batch(
                        todo_items, concurrency=CONCURRENCY, per_call=BATCH_SIZE, on_done=_on_done)
                except TypeError:
                    # 老接口 (FakeTranslator / NoopTranslator) 不接受 on_done: 退回后置补写
                    raw = eng.translate_batch(todo_items)
                    for ck, out_txt in (raw or {}).items():
                        _on_done(ck, out_txt)
            else:
                raw = eng.translate_batch(todo_items)
                for ck, out_txt in (raw or {}).items():
                    _on_done(ck, out_txt)
            print("[翻译] 完成。")
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
            text = norm_text(r.get("source_text"))
            if mode == "APPROVED":
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
                if any(v.startswith("[ERR") for v in resolved.values()):
                    status = "PENDING"
                elif translation.strip():
                    status = "DONE"
                else:
                    status = "PENDING"
            else:
                # 该行本次未进引擎 (被过滤/缺席 jobs): 仍从 cache 物化 (materialized output)
                translation, status = materialize_from_cache(r.get("translation_id"), text, mode, cache, ctx_map)
                if translation is None:
                    translation = ""
                    status = "PENDING"
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
