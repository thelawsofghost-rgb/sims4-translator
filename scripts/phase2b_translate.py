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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase2a_catalog import (norm_text, source_hash, detect_language,
                             _split_semantic_tokens, _is_id_token)

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

def translate_mode_for(text: str):
    """翻译层三档决策 (确定性, 不需要 LLM)。

    返回 (mode, 需翻译的语义片段列表, 全部 token)。
    KEEP  = 无可翻译语义 (剥离 ID/技术标记 token 后为空)
    PARTIAL = 既有需保留的 ID/编号, 又有真实语义片段
    FULL    = 全语义
    """
    t = norm_text(text)
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
# 断点 = 受保护 token (编号/版本/*anim/纯数字) 或 独立分隔符 (斜杠/括号/逗号/空白包裹的连字符)
_PROTECTED = re.compile(r"\d[\w]*(?:-\d[\w]*)*|\*anim\w*|[\u0400-\u04ffA-Za-z]\d[\w]*|\b(?:V|v)\d+(?:\.\d+)?\b|[\d.]+")
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
    """本机 Ollama 翻译后端。

    优先 OpenAI-compatible:  http://localhost:11434/v1/chat/completions, api_key=ollama
    失败时回退 Ollama 原生:  http://localhost:11434/api/chat (逐条, 可靠优先)
    """
    def __init__(self, base_url="http://localhost:11434", model="ni-fei:latest", api_key="ollama"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        import httpx
        self._httpx = httpx

    def translate_batch(self, items, concurrency=2, per_call=8, max_retry=3):
        """按批次并发调用; 失败/空结果重试, 逐条回退原生 /api/chat。返回 {key: zh}。

        全量场景(数千 phrase)下并发过高会压垮本机 Ollama 导致返回空译文,
        这里压低并发并加入: 空结果重试 + native 回退。
        """
        import concurrent.futures as cf
        import time
        results = {}
        cur = items[:]
        for attempt in range(max_retry):
            if not cur:
                break
            batches = [cur[i:i + per_call] for i in range(0, len(cur), per_call)]
            got = {}
            with cf.ThreadPoolExecutor(max_workers=concurrency) as ex:
                futs = {ex.submit(self._call_openai, b): b for b in batches}
                for fu in cf.as_completed(futs):
                    keymap, zh = fu.result()
                    for k, z in zip(keymap, zh):
                        got[k] = z
            # 完成/空 判定
            done_here = {k: v for k, v in got.items() if v and not v.startswith("[ERR")}
            results.update(done_here)
            cur = [kvt for kvt in cur if kvt[0] not in done_here]
            if cur and attempt < max_retry - 1:
                print(f"[重试] 第{attempt+2}轮: 仍缺 {len(cur)} phrase, 稍候重试 ...")
                time.sleep(3 * (attempt + 1))
        # 仍缺失的: 用原生逐条兜底 (可靠优先)
        if cur:
            print(f"[回退] 剩余 {len(cur)} phrase 用原生 /api/chat 逐条兜底 ...")
            for k, t in cur:
                try:
                    _, zh = self._call_native([(k, t)])
                    if zh and zh[0] and not zh[0].startswith("[ERR"):
                        results[k] = zh[0]
                except Exception:  # noqa
                    pass
        return results

    def _call_openai(self, items):
        """优先 OpenAI-compatible /v1/chat/completions, 失败/404 回退原生 /api/chat。"""
        # 每 item 一个 block; 用编号前缀区分, 便于按序回填
        blocks = []
        for i, (k, t) in enumerate(items):
            blocks.append(f"[{i}]\n{t}")
        lines = "\n\n".join(blocks)
        prompt = (
            "你是模拟人生4动作包汉化专家。下面每一段是一个待翻译块, 结构为:\n"
            "[编号]\nTarget 行: 需要翻译的语义片段\nContext 行(可选): 仅供参考\n"
            "规则:\n"
            "1. 只翻译每个 [编号] 块里 Target: 之后的内容为简体中文\n"
            "2. Context: 只是参考, 不要翻译、不要输出、不要改写它\n"
            "3. 若块里有『固定术语』行, 译文中必须原样嵌入这些指定中文, 不得改用同义词\n"
            "4. 保留 Target 中的数字、字母编号、版本号(V1/v2)、*anim 等技术标记不变\n"
            "5. 逐块输出, 每块一行译文, 用同样编号开头 (如 [0] 译文), 一条一行, 不要额外说明\n"
            + lines
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You translate Sims 4 pose names to simplified Chinese. Output only the numbered translations."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 4096,
        }
        url = f"{self.base_url}/v1/chat/completions"
        try:
            r = self._httpx.post(url, headers={"Authorization": f"Bearer {self.api_key}"},
                                 json=payload, timeout=180)
            if r.status_code == 404:  # 老版本 Ollama 无 /v1, 回退原生
                return self._call_native(items)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return self._parse_numbered(content, items)
        except Exception as e:  # noqa
            try:
                return self._call_native(items)
            except Exception as e2:  # noqa
                return [k for k, _ in items], [f"[ERR openai:{e!r} native:{e2!r}]" for _ in items]

    def _call_native(self, items):
        """Ollama 原生 /api/chat, 逐条 (可靠优先)。返回 (keys, zh)。"""
        url = f"{self.base_url}/api/chat"
        keys, zhs = [], []
        for k, text in items:
            prompt = (
                "你是模拟人生4动作包汉化专家。下面是待翻译内容:\n"
                "Target 行: 需要翻译的语义片段 (可能含 Context 行, 仅供参考)。\n"
                "规则:\n"
                "1. 只翻译 Target: 之后的内容为简体中文, 只输出这一条译文, 不要解释。\n"
                "2. Context: 只是参考, 不要翻译、不要输出、不要改写它。\n"
                "3. 若内容含『固定术语』行, 译文中必须原样嵌入这些指定中文, 不得改用同义词。\n"
                "4. 保留 Target 中的数字、字母编号、版本号、*anim 等技术标记不变。\n"
                f"{text}"
            )
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 128},
            }
            r = self._httpx.post(url, json=payload, timeout=180)
            r.raise_for_status()
            keys.append(k)
            try:
                j = r.json()
                content = (j.get("message") or {}).get("content", "")
            except Exception:
                # 可能是 SSE 流式: 逐行取 data: 里的 final message
                content = ""
                for ln in r.text.splitlines():
                    if ln.startswith("data:"):
                        import json as _j
                        seg = ln[5:].strip()
                        if seg and seg != "[DONE]":
                            try:
                                content = (_j.loads(seg).get("message") or {}).get("content", content)
                            except Exception:
                                pass
            zhs.append((content or "").strip())
        return keys, zhs

    @staticmethod
    def _parse_numbered(content, items):
        """解析模型输出: 每行 '[n] 译文' 或 'n. 译文' 或 '- 译文', 按 n 映射到对应 item。"""
        out = {}
        pat_idx = re.compile(r"^\s*\[(\d+)\]\s*(.*)$")
        num_pat = re.compile(r"^\s*(?:\d+[.):]|[-*])\s*(.*)$")
        auto = 0
        for ln in content.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            m = pat_idx.match(ln)
            if m and m.group(2).strip():
                out[int(m.group(1))] = m.group(2).strip()
                continue
            m = num_pat.match(ln)
            if m and m.group(1).strip():
                # 无显式编号时按出现顺序
                while auto in out:
                    auto += 1
                out[auto] = m.group(1).strip()
                auto += 1
        return [k for k, _ in items], [out.get(i, "") for i in range(len(items))]


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

    # 翻译引擎: 默认本机 Ollama
    if NO_LLM or (SAMPLE is None and len(need_translate) == 0):
        eng = NoopTranslator()
    else:
        eng = OllamaTranslator()
        print(f"[引擎] {type(eng).__name__} @ {eng.base_url}/v1/chat/completions (model={eng.model})")

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
    phrase_map = {}            # composite_key -> (tid, orig_key)
    for tid, j in jobs.items():
        for p in j["pending"]:
            ck = f"{tid}:::{p['key']}"
            block = f"Target: {p['t'].strip()}"
            if p.get("gloss_hint"):
                block += f"\n固定术语(译文中必须原样使用): {p['gloss_hint']}"
            if p.get("ctx"):
                block += f"\nContext: {p['ctx']}"
            phrase_items.append((ck, block))
            phrase_map[ck] = (tid, p["key"])

    phrase_res = {}
    if phrase_items:
        print(f"[翻译] 调用引擎 {type(eng).__name__}, 共 {len(phrase_items)} 个 phrase ...")
        raw = eng.translate_batch(phrase_items)
        print("[翻译] 完成。")
        for ck, out_txt in raw.items():
            if ck in phrase_map:
                tid, orig_key = phrase_map[ck]
                phrase_res.setdefault(tid, {})[orig_key] = str(out_txt).strip()

    # 组装 done; 含 protected_spans (PARTIAL 时列出需原样保留的 token 在原文本中的区间)
    done_cols = ["translation_id", "source_text", "decision", "translate_mode",
                 "detected_language", "protected_spans", "translation", "status", "source_hash"]
    done_rows = []
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
            if any(v.startswith("[ERR") for v in resolved.values()):
                status = "PENDING"
            elif translation.strip():
                status = "DONE"
            else:
                status = "PENDING"
        else:
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
