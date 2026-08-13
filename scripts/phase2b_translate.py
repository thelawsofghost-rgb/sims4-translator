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
from phase2a_catalog import norm_text, source_hash, _split_semantic_tokens, _is_id_token

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

    def translate_batch(self, items, concurrency=4, per_call=8):
        """按批次并发调用; 逐条失败时回退原生 /api/chat。返回 {key: zh}。"""
        import concurrent.futures as cf
        results = {}
        batches = [items[i:i + per_call] for i in range(0, len(items), per_call)]
        with cf.ThreadPoolExecutor(max_workers=concurrency) as ex:
            futs = {ex.submit(self._call_openai, b): b for b in batches}
            for fu in cf.as_completed(futs):
                keymap, zh = fu.result()
                for k, z in zip(keymap, zh):
                    results[k] = z
        return results

    def _call_openai(self, items):
        """优先 OpenAI-compatible /v1/chat/completions, 失败/404 回退原生 /api/chat。"""
        lines = "\n".join(f"{i}. {t}" for i, (_, t) in enumerate(items))
        prompt = (
            "你是模拟人生4动作包汉化专家。把下面的英文/多语动作姿态名翻译为简体中文。\n"
            "规则:\n"
            "1. 译文简洁自然, 符合模组姿态命名习惯。\n"
            "2. 保留原文中的数字、字母编号、版本号(V1/V2/v.2)、*anim 等技术标记, 不翻译、不改写。\n"
            "3. 每个编号行只输出一条译文, 用同样的编号序号开头, 一条一行, 不要额外说明。\n"
            "原文:\n" + lines
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
                "把这条模拟人生4动作姿态名翻译为简体中文。只输出译文, 不要解释。\n"
                "保留数字、字母编号、版本号、*anim 等技术标记不变。\n"
                f"原文: {text}"
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
        out = {}
        num_pat = re.compile(r"^\s*(?:\d+[.):]|[-*])\s*(.*)$")
        for ln in content.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            m = num_pat.match(ln)
            if m:
                out[len(out)] = m.group(1).strip()
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


def main():
    assert TODO.exists(), f"未找到 {TODO}"
    todo = load_todo()
    print(f"[输入] todo 行数 = {len(todo)}")

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
        # 分层: FULL / PARTIAL 各自按固定种子抽, 尽量跨不同 source; 并强制纳入非英语 + *anim/编号样例
        import random
        random.seed(20260813)
        need = need_translate[:]
        full = [d for d in need if d[1] == "FULL_TRANSLATE"]
        part = [d for d in need if d[1] == "PARTIAL_TRANSLATE"]
        # 非英语候选 (detected_language 非 en/zxx)
        nonen = [d for d in need if d[0].get("detected_language") not in ("en", "zxx")]
        # 优先保证覆盖: 非英语, PARTIAL 含 *anim/编号, 普通英文语义; 但总数必须 == SAMPLE
        forced = []
        for d in nonen:
            if len(forced) >= SAMPLE:
                break
            if d not in forced:
                forced.append(d)
        proto = [d for d in part if re.search(r"\*anim|\d", d[0].get("source_text", "") or "")]
        for d in proto:
            if len(forced) >= SAMPLE:
                break
            if d not in forced:
                forced.append(d)
        # full 普通语义 (保证至少 1 条, 若空间允许)
        for d in full:
            if len(forced) >= SAMPLE:
                break
            if d not in forced:
                forced.append(d)
        pool = [d for d in need if d not in forced]
        random.shuffle(pool)
        pick = forced + pool[: max(0, SAMPLE - len(forced))]
        sample_pick = set(id(d) for d in pick)
        need_translate = [d for d in need if id(d) in sample_pick]
        print(f"[抽样] 抽查 {len(need_translate)} 行 "
              f"(FULL={len([d for d in need_translate if d[1]=='FULL_TRANSLATE'])}, "
              f"PARTIAL={len([d for d in need_translate if d[1]=='PARTIAL_TRANSLATE'])}, "
              f"非英语={len([d for d in need_translate if d[0].get('detected_language') not in ('en','zxx')])})")

    # 翻译引擎: 默认本机 Ollama
    if NO_LLM or (SAMPLE is None and len(need_translate) == 0):
        eng = NoopTranslator()
    else:
        eng = OllamaTranslator()
        print(f"[引擎] {type(eng).__name__} @ {eng.base_url}/v1/chat/completions (model={eng.model})")

    zh_map = {}
    if need_translate:
        items = [(d[0]["translation_id"], d[0]["source_text"]) for d in need_translate]
        print(f"[翻译] 调用引擎 {type(eng).__name__}, 共 {len(items)} 条 ...")
        zh_map = eng.translate_batch(items)
        print("[翻译] 完成。")

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
        else:
            translation = zh_map.get(r.get("translation_id"), "") if SAMPLE else ""
            if translation and not translation.startswith("[ERR"):
                status = "DONE"
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
