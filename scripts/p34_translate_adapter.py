#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
p34_translate_adapter.py --- P34-BATCH: WW animation display-name zh adapter.

REUSE (no redesign): the engine is the production pose pipeline's
OllamaTranslator from scripts/phase2b_translate.py, reached via subclass
P34Translator(OllamaTranslator).  We override only _call_batch (prompt
construction + context serialization); transport / batching / retry /
JSON-schema / on_done checkpoint / fail-fast all stay the parent's code,
verbatim, untouched.

Pipeline (per operator spec):
  Stage 1  deterministic resolver  -- series stem, sequence token, suffix,
                                      Climax, sex_category gloss, location gloss,
                                      protected (keep) words.  Fully offline;
                                      emits TRANSLATED rows with NO model call.
  Stage 2  LLM resolver             -- ONLY unresolved rows go to
                                      P34Translator(OllamaTranslator) ->
                                      ni-fei:latest.  Endpoint precedence:
                                      --url CLI > env P34_OLLAMA_URL >
                                      http://127.0.0.1:11434.  Reuses a REMOTE
                                      Ollama (e.g. the Windows box's existing
                                      Ollama+ni-fei); do NOT deploy Ollama on
                                      this ECS.
                        LLM failure  -> status=REVIEW (fail-closed, NEVER guess).

Reuse boundary (confirmed against phase2b_translate.py internals): the parent
translate_batch() NEVER reads item[1]; it only batches items and forwards them to
self._call_batch(batch).  So we pass items as (key, <P34 row-context dict>) and
our _call_batch unwraps that dict to build a rich, context-injected prompt.  The
parent's concurrency / retry / schema / fail-fast / checkpoint code runs
unchanged on those opaque payloads.

Input : output/p33/p33_translation_context.csv  (real P33 context CSV, 25 cols)
Output: output/p34/p34_translation_mapping.csv
        output/p34/p34_translation_review.txt
        output/p34/p34_translation_stats.txt

Read-only discipline: ZERO_WRITE_TO_MODS=YES, ZERO_WRITE_TO_SAVES=YES.  Never
generates STBL, never writes Mods / saves.  identifier and raw_display_name are
preserved byte-for-byte in the mapping row.

Mapping columns (exact, per P34-BATCH spec):
  source_instance,ordinal,identifier,raw_display_name,translated_name,status,note

Exit codes: 0 success; 2 input missing/gate fail; 3 Ollama unreachable while
unresolved rows remain unresolved (fail-closed so nothing is silently guessed).

--dry-run : deterministic pass only; never contacts Ollama.  Any row the
            deterministic resolver cannot close becomes status=REVIEW with a
            "would-go-to-LLM" note.  Used for offline validation + logic tests.
"""
from __future__ import print_function
import argparse
import csv
import json
import os
import re
import sys

# --------------------------------------------------------------------------- #
# Reuse the production pose translator.  Import is side-effect free (the engine
# is only constructed under phase2b's __main__); we subclass it.
# --------------------------------------------------------------------------- #
_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

def _ollama_url():
    """Effective Ollama endpoint.

    Reuse the Windows-side Ollama + ni-fei:latest (remote endpoint support); do
    NOT deploy Ollama on this ECS.  Overridable per env var:

        P34_OLLAMA_URL=http://<Windows_IP>:11434

    Default falls back to the historical local endpoint.  Resolved at call time
    (not import time) so an operator can set the env var for a given run.
    """
    return os.environ.get("P34_OLLAMA_URL", "http://127.0.0.1:11434").strip().rstrip("/")
try:
    import phase2b_translate as P
    _OLLAMA_BASE = P.OllamaTranslator
    _IMPORT_ERR = None
except Exception as _e:                      # pragma: no cover - import env
    _OLLAMA_BASE = object
    _IMPORT_ERR = repr(_e)

REQUIRED_COLS = {
    "source_instance", "ordinal", "identifier", "raw_display_name",
    "sex_category", "actor_count", "locations", "actor_genders", "actor_clips",
    "series_key", "series_index", "series_size",
    "title_stem", "title_sequence_token", "title_suffix",
    "prev_raw_display_name", "next_raw_display_name",
}

# Mapping output columns (exact order per P34-BATCH spec).
MAP_COLS = ["source_instance", "ordinal", "identifier", "raw_display_name",
            "translated_name", "status", "note"]

STATUS_TRANSLATED = "TRANSLATED"
STATUS_LLM = "LLM"
STATUS_REVIEW = "REVIEW"
STATUS_KEEP = "KEEP"


class _Fail(Exception):
    """Fail-closed signal; main() maps to exit code 2."""


# =========================================================================== #
# Deterministic glossaries -- encoded from translation_rules.md §4.2 / §5 / §6 /
# §7.  Only these fixed one-zh-per-token mappings are applied mechanically.
# A token that is NOT in these tables is never force-mapped: it is either a
# protected keep-word (§7) or routed to the LLM (prose) / REVIEW.
# =========================================================================== #
SUFFIX_GLOSS = {          # §4.2 episode-beat suffixes (fixed canonical zh)
    "climax": "高潮",
    "climax 2": "高潮 2",
    "climax 3": "高潮 3",
    "climax 4": "高潮 4",
}

CATEGORY_GLOSS = {        # §6.1 sex_category enum name -> act-level zh
    "vaginal": "性交", "oral": "口交", "anal": "肛交",
    "handjob": "手交", "gssex": "女女", "female": "女女",
}

LOCATION_GLOSS = {        # §5.3 canonical location nouns (one zh per token)
    "double_bed": "双人床", "doublebed": "双人床",
    "floor": "地板", "door": "门口", "desk": "书桌",
    "shower": "淋浴", "bath": "浴缸", "chair": "椅子",
    "couch": "沙发", "pool": "泳池", "kitchen": "厨房",
}

# Word-level stem gloss for deterministic short content tokens.  A stem landing
# here closes in Stage 1 (mechanical, one canonical zh).  Prose / multi-sense or
# multi-token stems = Stage 2 LLM / REVIEW, never guessed by the adapter.
STEM_GLOSS = {
    "kiss": "接吻", "blowjob": "口交", "handjob": "手交",
    "fingering": "指交", "rimming": "舔肛", "deepthroat": "深喉",
    "cunnilingus": "口交", "anal": "肛交", "vaginal": "性交", "sex": "性爱",
    "missionary": "传教士", "doggy": "后入", "cowgirl": "女上位",
    "reverse-cowgirl": "反女上位", "spooning": "侧入", "spoon": "侧入",
    "masturbation": "自慰", "groping": "爱抚", "fondling": "爱抚",
    "foreplay": "前戏", "intercourse": "性交", "penetration": "插入",
    "ejaculation": "射精", "creampie": "内射", "pussy": "阴部",
    "cock": "阴茎", "penis": "阴茎", "vagina": "阴道", "ass": "臀部",
    "breasts": "胸部", "nipple": "乳尖", "clit": "阴蒂", "clitoris": "阴蒂",
    "balls": "睾丸", "cum": "精液", "sperm": "精液", "oral": "口交",
    "gssex": "女女", "orgasm": "高潮",
    "climax": "高潮", "moan": "呻吟", "moaning": "呻吟", "roleplay": "角色扮演",
    "cuddle": "拥抱", "hug": "拥抱", "dance": "舞蹈", "lapdance": "膝上舞",
    "strip": "脱衣", "handcuffs": "手铐", "blindfold": "眼罩", "whipping": "鞭打",
    "bondage": "束缚", "spanking": "打屁股", "squirting": "潮吹",
    "footjob": "足交", "grinding": "磨蹭", "rubbing": "摩擦", "licking": "舔舐",
    "sucking": "吮吸", "swallowing": "吞咽", "caressing": "抚摸",
    "embrace": "拥抱", "seduce": "诱惑", "flirting": "调情", "seduction": "诱惑",
    "making-out": "热吻", "makingout": "热吻", "massage": "按摩",
    "shower": "淋浴", "bath": "浴缸",
}

_MODIFIER_GLOSS = {
    "slow": "缓慢", "fast": "快速", "rough": "粗暴", "gentle": "温柔",
    "deep": "深入", "hard": "用力", "soft": "轻柔", "hot": "炽热",
    "wet": "湿", "dirty": "放纵", "public": "公共场所", "romantic": "浪漫",
    "kinky": "重口", "vanilla": "纯爱", "sleeping": "睡着", "blindfolded": "蒙眼",
}

# §7 protect / keep tokens (author/brand/person/number/version) -- verbatim.
_PROTECTED_EXACT = {"sex", "wild", "gemini", "kindly", "battle",
                    "wickedwhims", "ww", "sims", "sim"}
_KEEP_TOKEN_RE = re.compile(r"(?i)^(?:v\d+(?:\.\d+)?|\*?anim(?:\d*)?)$")
_ANY_CJK = re.compile(r"[\u4e00-\u9fff]")


def _no_key(x):
    x = (x or "").strip()
    if x in ("", "-", "None", "S"):
        return ""
    return x


def _is_keep_token(tok):
    if _KEEP_TOKEN_RE.match(tok):
        return True
    if re.match(r"^[a-z]+\d{1,}$", tok) and len(tok) >= 5:
        return True            # author-ish handle: Nevely42, KindlyWhatever1
    if re.match(r"^\d{2,}[a-z]*$", tok):
        return True            # model/version-ish token (42379)
    low = tok.lower()
    return low in _PROTECTED_EXACT or low.capitalize() in {"Gemini", "Grrlz"}


# =========================================================================== #
# P34Translator(OllamaTranslator): reuse parent transport/batch/retry/schema/
# checkpoint/fail-fast; replace ONLY prompt construction + context serialization.
# =========================================================================== #
class P34Translator(_OLLAMA_BASE):
    """WW animation display-name translator over the proven OllamaTranslator.

    The parent translate_batch() is NOT overridden; items are
    (key, <P34 ctx dict>); _call_batch() builds a per-batch prompt serializing
    the FULL row context (raw title, series, sex_category, locations, actor
    fields, prev/next title) + a compact rules excerpt.
    """

    def build_prompt(self, ctx_blocks):
        lines = []
        for i, c in enumerate(ctx_blocks):
            lines.append("[%s] id=%s" % (i, c["_key"]))
            lines.append("Target: %s" % (c.get("raw_display_name") or ""))
            lines.append("  sex_category : %s" % (c.get("sex_category") or "-"))
            lines.append("  locations    : %s" % (c.get("locations") or "-"))
            lines.append("  actor_count  : %s" % (c.get("actor_count") or "-"))
            lines.append("  actor_genders: %s" % (c.get("actor_genders") or "-"))
            lines.append("  actor_clips  : %s" % (c.get("actor_clips") or "-"))
            lines.append("  series_key   : %s  size=%s"
                         % (c.get("series_key") or "-", c.get("series_size") or "1"))
            if c.get("series_key"):
                lines.append("  prev_title   : %s"
                             % (c.get("prev_raw_display_name") or "-"))
                lines.append("  next_title   : %s"
                             % (c.get("next_raw_display_name") or "-"))
            lines.append("")
        return "\n".join(lines)

    def _rules_excerpt(self):
        return (
            "你是《模拟人生4》WW 成人动画缩略名中文本地化端点。规则:\n"
            "1. 只翻译每个块里 Target 行的内容为简体中文 UI 名, 口语化简短(核心 2-6 字"
            "名词/短语), 成人向但克制, 不堆砌粗口。\n"
            "2. sex_category / locations / actor_* / series / prev/next 仅为理解语境, 不"
            "翻译、不写进译文。\n"
            "3. 保留原数字/罗马数字及其间距; 版本号(V1/v2)/编号/*anim 等技术标记原样。\n"
            "4. 作者名/人物名/品牌等专名若嵌在标题中 => 原样保留英文, 不译不音译。\n"
            "5. 若 Target 是系列成员, 其中文主干必须与同 series_key 其他成员一致(仅尾部序列"
            "号/后缀不同); 后缀 Climax 一律译 高潮。\n"
            "6. 一行一项, 只输出最终中文, 不加解释。歧义或多义歧义 => 给最可能者并在译文前"
            "不加标记(交由上层 reviewer 复核)。\n"
        )

    def _call_batch(self, items):
        """Override ONLY prompt assembly.  Returns (keys, zh_list, status) in the
        exact shape the parent translate_batch() loop expects; parent does the
        batched POST /api/chat + JSON-schema + `ok`/`malformed`/`empty`/`schema`
        handling by calling *this* override per batch."""
        ctx_blocks = []
        for k, c in items:
            blk = dict(c) if isinstance(c, dict) else {"raw_display_name": str(c)}
            blk["_key"] = k
            ctx_blocks.append(blk)
        prompt = self.build_prompt(ctx_blocks) + "\n\n" + self._rules_excerpt()
        url = "/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system",
                 "content": ("You translate Sims 4 WickedWhims animation display "
                             "names to simplified Chinese.  Output strictly as JSON "
                             "matching the provided schema; exactly one object per "
                             "given id.")},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "think": False,
            "format": self._SCHEMA,
            "options": {"temperature": 0.0, "num_predict": 1024},
        }
        r = self.client.post(url, json=payload)   # trust_env=False client
        if r.status_code >= 500 or r.status_code == 429:
            raise RuntimeError("Ollama transport/load: HTTP %s" % r.status_code)
        r.raise_for_status()
        j = r.json()
        content = ((j.get("message") or {}).get("content") or "").strip()
        if not content:
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
        zhs = [P.normalize_model_output(by_id.get(str(k), "")) for k, _ in items]
        if sum(1 for z in zhs if z) < len(zhs) * 0.5:
            return keys, zhs, "malformed"
        return keys, zhs, "ok"


# =========================================================================== #
# Deterministic Stage-1 resolver.
# =========================================================================== #
def _resolve_row(row, stem_cache):
    """Deterministic Stage-1 resolver for ONE row, driven by the P33 columns the
    rules mandate we TRUST (not re-derive): title_stem / title_sequence_token /
    title_suffix (+ series_key).

    Returns (resolved:bool, zh:str|None, status, note).
      resolved True  -> deterministic close (zh final).
      resolved False -> caller routes to Stage 2 LLM / REVIEW.

    Series rules (§2): a size>=2 series is ONE unit sharing one stop zh stem;
    members differ only in their own sequence_token / suffix.  The FIRST member to
    be seen resolves the stem (glossary -> else LLM/REVIEW) and caches it per
    series_key; later members decorate with their own token/suffix and NEVER
    re-translate the stem or guess it.
    Keep rules (§7): author/person/brand/roman/digit-only labels are verbatim.
    """
    raw = (row.get("raw_display_name") or "").strip()
    key = _no_key(row.get("series_key"))
    stem = (row.get("title_stem") or "").strip()
    seq = (row.get("title_sequence_token") or "").strip()
    suffix = (row.get("title_suffix") or "").strip()
    low = raw.lower()

    def _deco(zh_stem):
        """Join a decided zh stem with this member's own numeral + suffix, faithful
        to the SOURCE separator placement (rules §3.3 / §8.2):
          raw "Gearshift 3 - Climax" -> "…stem… 3 - 高潮"
        The numeral follows the stem with a single space; only the suffix carries the
        source's ` - ` separator when it is dash-separated in the raw.
        """
        out = zh_stem or ""
        if seq:
            out += " " + seq                     # numeral: space-attached
        if suffix:
            zh_suf = _suffix_zh(suffix, raw)
            # determine the separator the SOURCE puts before the suffix
            tail = raw
            if seq:
                # find the segment after the numeral
                idx = raw.rfind(seq)
                tail = raw[idx + len(seq):] if idx >= 0 else ""
            sep = " - " if " - " in tail or re.search(r"\s-\s", tail) else " "
            out += sep + zh_suf
        return out

    def unres(why):
        # A member of an already-decided series reuses the cached stem instead of
        # failing to the LLM: only genuinely-new (stem-undecided) text is "unresolved".
        if key and stem_cache.get(key) and stem_cache[key].get("zh") is not None:
            z = _deco(stem_cache[key]["zh"])
            return True, z, STATUS_TRANSLATED, "series-stem reuse(%s): %s" % (key, why)
        return False, None, STATUS_REVIEW, why

    # ---- trivial / keep-only whole labels -----------------------------------
    if not raw:
        return True, "", STATUS_KEEP, "blank/empty raw kept"
    if _ANY_CJK.search(raw):
        return True, raw, STATUS_KEEP, "already Chinese; kept"
    low_nospace = re.sub(r"[^A-Za-z0-9]", "", low)
    # author/person/brand handle as the ENTIRE label: a single contiguous alnum
    # token that embeds a digit (Nevely42) OR a one-word latin handle.  Require
    # the raw to be one glued token (no space) so an ordinary title with a
    # trailing counter ("Kiss 1") is never mistaken for an author handle.
    _rawtokens = re.split(r"\s+", raw.strip())
    if len(_rawtokens) == 1 and re.fullmatch(r"[A-Za-z]+[0-9][A-Za-z0-9]*",
                                             _rawtokens[0]):
        return True, raw, STATUS_KEEP, "author/person handle; verbatim"
    # pure digit / roman label, no real english word
    words = re.findall(r"[a-z]+", low)
    if not words:
        return True, raw, STATUS_KEEP, "no english words (num/roman/symbol); verbatim"
    if all(_is_keep_token(w) for w in words):
        return True, raw, STATUS_KEEP, "keep-words only; verbatim"

    # If the row carries a P33 title_suffix but no stem decision yet (standalone or
    # first-seen), and it is ONLY suffix-with-content, handle it as normal title.
    # -------- single content word equals the whole title ----------------------
    content_words = [w for w in words if not _is_keep_token(w)]
    if len(content_words) == 1 and not seq:
        w = content_words[0]
        if w in STEM_GLOSS or w in LOCATION_GLOSS:
            base = STEM_GLOSS.get(w) or LOCATION_GLOSS.get(w)
            # standalone-with-suffix
            return True, _deco(base) if suffix else base, STATUS_TRANSLATED, \
                ("%s-gloss '%s'" % ('location' if w in LOCATION_GLOSS else 'stem', w))
        return unres("single stem word '%s' not in glossary -> needs LLM" % w)

    # -------- series stem via trusted P33 title_stem column ------------------
    # A series (key != '') is one unit: translate title_stem once (glossary) and
    # cache it so sibling members only decorate with their own token/suffix.  A
    # series whose stem is not a single glossary word is genuinely unresolved and
    # goes to the LLM / REVIEW -- never guessed.  Standalone rows (no key) SKIP
    # this gating and are handled by the composite / single-word logic below.
    if stem and key:
        stem_words = re.findall(r"[a-z]+", stem.lower())
        stem_content = [w for w in stem_words if not _is_keep_token(w)]
        if len(stem_content) == 1 and (stem_content[0] in STEM_GLOSS
                                       or stem_content[0] in LOCATION_GLOSS):
            base = (STEM_GLOSS.get(stem_content[0])
                    or LOCATION_GLOSS.get(stem_content[0]))
            zh = _deco(base)
            stem_cache.setdefault(key, {"zh": base})
            return True, zh, STATUS_TRANSLATED, "series-stem-gloss '%s' + deco" \
                % stem_content[0]
        return unres("series title_stem '%s' not single-glossary -> LLM" % stem)

    # -------- composite [modifiers*] + glossary-head (only when airtight) -------
    head = words[-1]
    if head in STEM_GLOSS or head in LOCATION_GLOSS:
        base = STEM_GLOSS.get(head) or LOCATION_GLOSS.get(head)
        mod_zh, ok = [], True
        for m_ in words[:-1]:
            if _is_keep_token(m_):
                continue
            if m_ in _MODIFIER_GLOSS:
                mod_zh.append(_MODIFIER_GLOSS[m_])
            else:
                ok = False
                break
        if ok:
            zh = "".join(mod_zh) + base if mod_zh else base
            if key:
                stem_cache.setdefault(key, {"zh": zh})
            return True, _deco(zh) if (seq or suffix) else zh, STATUS_TRANSLATED, \
                "modifier+%s-gloss" % head
    return unres("multi-word prose title '%s' -> needs LLM" % raw)


def _suffix_zh(suffix, raw):
    """Canonical zh of a title_suffix (rules §4).  Climax -> 高潮 (numeric
    continuation preserved).  Any other suffix is kept verbatim so a beat marker
    is never mis-glossed.  The caller joins using the source's separator style."""
    s = (suffix or "").strip()
    m = re.match(r"^(?i:climax)\s*(\d*)$", s)
    if m:
        return "高潮" + ((" " + m.group(1)) if m.group(1) else "")
    return s


# =========================================================================== #
# Input reading / fail-closed column gate.
# =========================================================================== #
def read_ctx(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        r = list(csv.DictReader(fh))
    if not r:
        raise _Fail("input CSV has no data rows")
    col = set(r[0].keys())
    miss = REQUIRED_COLS - col
    if miss:
        raise _Fail("missing required columns: %s" % ",".join(sorted(miss)))
    return r


# =========================================================================== #
# Orchestration: Stage 1 (deterministic) -> Stage 2 (LLM) -> outputs.
# =========================================================================== #
def run_pipeline(rows, out_dir, dry_run=False, concurrency=8, per_call=8,
                 max_retry=3, llm_on=True, llm_url=None):
    """Stage 1 (deterministic) then Stage 2 (LLM for the unresolved remainder),
    then writes the three output files.  Every row's status is decided exactly
    once here; a row is never guessed by the adapter.

    dry_run / llm_on=False -> deterministic pass only; unresolved rows become
    REVIEW (never contact Ollama).  Used by offline validation + logic tests.
    """
    os.makedirs(out_dir, exist_ok=True)
    unresolved = []            # (rid, row_context_dict) for Stage 2
    unresolved_why = {}        # rid -> specific reason it wasn't deterministically closed
    stem_cache = {}
    decided = {}               # rid -> {zh,status,note} (final for non-LLM rows)

    # ---- Stage 1 : deterministic -------------------------------------------
    for row in rows:
        rid = _rid_of(row)
        ok, zh, status, note = _resolve_row(row, stem_cache)
        if ok:
            # successful deterministic close; cache decided series stems so later
            # members reuse the SAME zh stem (series consistency, rules §2.3)
            key = _no_key(row.get("series_key"))
            if key and status == STATUS_TRANSLATED and zh is not None:
                stem_cache.setdefault(key, {"zh": zh})
            decided[rid] = {"zh": zh if zh is not None else "",
                            "status": status, "note": note or ""}
        else:
            # not deterministically closed -> Stage 2 candidate
            unresolved.append((rid, row))
            unresolved_why[rid] = note or "unresolved"

    # ---- Stage 2 : LLM only for unresolved, only when enabled ----------------
    llm_map = {}
    if unresolved and not dry_run and llm_on:
        if _IMPORT_ERR:
            raise _Fail("phase2b import failed (%s)" % _IMPORT_ERR)
        eng = P34Translator(base_url=llm_url or _ollama_url())
        try:
            eng.client.get("/api/version").raise_for_status()
        except Exception:
            # fail-closed: refusing to run means we cannot mark unresolved w/o a
            # guess; surface as a gate error rather than silently REVIEW them
            # at a DOWN endpoint.
            raise _Fail("Ollama unreachable at %s (ni-fei:latest?) -- "
                        "%d unresolved rows would be LEFT UNRESOLVED; refusing. "
                        "(start `ollama serve` on the host so %s/api/version "
                        "is reachable, set P34_OLLAMA_URL if remote, or use "
                        "--dry-run to get a REVIEW list instead)"
                        % (eng.base_url, len(unresolved), eng.base_url))
        items = [(rid, row) for rid, row in unresolved]
        raw_map = eng.translate_batch(items, concurrency=concurrency,
                                      per_call=per_call, max_retry=max_retry)
        for rid, raw_z in raw_map.items():
            z = (raw_z or "").strip()
            if z and not z.startswith("[ERR"):
                llm_map[rid] = z

    # ---- final mapping rows (one per input row, ordinal source order) -------
    mapping = []
    for row in rows:
        rid = _rid_of(row)
        if rid in decided:
            d = decided[rid]
            zh, status_t, note = d["zh"], d["status"], d["note"]
        elif rid in llm_map:
            zh, status_t, note = llm_map[rid], STATUS_LLM, "LLM-stage2 resolved"
        else:
            why = unresolved_why.get(rid, "")
            zh, status_t, note = "", STATUS_REVIEW, \
                (("dry-run REVIEW: " + why) if (dry_run or not llm_on)
                 else (why + " [LLM unresolved]"))
        mapping.append({
            "source_instance": row.get("source_instance", ""),
            "ordinal": row.get("ordinal", ""),
            "identifier": row.get("identifier", ""),
            "raw_display_name": row.get("raw_display_name", ""),
            "translated_name": zh or "",
            "status": status_t,
            "note": note or "",
        })
    return mapping


def _rid_of(row):
    """Per-row natural key used to thread a row from Stage 1 into Stage 2 and
    back into the final mapping.  Prefer source_instance+identifier (stable);
    fall back to ordinal only when identifier is empty."""
    si = row.get("source_instance", "") or ""
    ident = row.get("identifier", "") or ""
    if ident:
        return "%s::%s" % (si, ident)
    return "ord::%s" % (row.get("ordinal", "") or "")


# Short aliases so logic tests can import pure helpers without touching orchestration.
def resolve_row(row, stem_cache):
    return _resolve_row(row, stem_cache)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="P33 context CSV (input)")
    ap.add_argument("--out", default="output/p34")
    ap.add_argument("--dry-run", action="store_true",
                    help="deterministic only; never contact Ollama (offline/tests)")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--per-call", type=int, default=8)
    ap.add_argument("--max-retry", type=int, default=3)
    ap.add_argument("--url", dest="llm_url", default=None,
                    help="Ollama endpoint override.  Precedence: --url > "
                         "env P34_OLLAMA_URL > http://127.0.0.1:11434.  e.g. "
                         "--url http://<Windows_IP>:11434")
    a = ap.parse_args(argv)

    if not os.path.isfile(a.csv):
        print("VERDICT=FAIL", file=sys.stderr)
        print("REASON=INPUT_MISSING:%s" % a.csv, file=sys.stderr)
        return 2
    try:
        rows = read_ctx(a.csv)
    except _Fail as e:
        print("VERDICT=FAIL", file=sys.stderr)
        print("REASON=%s" % e, file=sys.stderr)
        return 2
    try:
        mapping = run_pipeline(rows, a.out, dry_run=a.dry_run,
                               concurrency=a.concurrency, per_call=a.per_call,
                               max_retry=a.max_retry, llm_url=a.llm_url)
    except _Fail as e:
        print("VERDICT=FAIL", file=sys.stderr)
        print("REASON=%s" % e, file=sys.stderr)
        return 3

    _write_outputs(mapping, a.out)
    _print_stats(mapping, a.out)
    return 0


def _write_outputs(mapping, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    map_p = os.path.join(out_dir, "p34_translation_mapping.csv")
    with open(map_p, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=MAP_COLS, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        for m in mapping:
            w.writerow(m)

    rev = [m for m in mapping if m["status"] == STATUS_REVIEW]
    with open(os.path.join(out_dir, "p34_translation_review.txt"),
              "w", encoding="utf-8") as fh:
        fh.write("P34 translation review queue (status=REVIEW)\n")
        fh.write("=" * 72 + "\n")
        for m in rev:
            fh.write("[%s] %s\n  raw=%s\n  zh=%s note=%s\n"
                     % (m["ordinal"], m["identifier"], m["raw_display_name"],
                        m["translated_name"], m["note"]))
        if not rev:
            fh.write("(none) -- all rows deterministically closed.\n")

    t = {"TOTAL_ROWS": len(mapping)}
    for s in (STATUS_TRANSLATED, STATUS_LLM, STATUS_REVIEW, STATUS_KEEP):
        t[s] = sum(1 for m in mapping if m["status"] == s)
    t["TRANSLATED_COUNT"] = t[STATUS_TRANSLATED] + t[STATUS_LLM]
    t["REVIEW_COUNT"] = t[STATUS_REVIEW]
    t["KEEP_COUNT"] = t[STATUS_KEEP]
    with open(os.path.join(out_dir, "p34_translation_stats.txt"),
              "w", encoding="utf-8") as fh:
        for k in ("TOTAL_ROWS", "TRANSLATED_COUNT", STATUS_TRANSLATED,
                  STATUS_LLM, "REVIEW_COUNT", STATUS_REVIEW, "KEEP_COUNT",
                  STATUS_KEEP):
            fh.write("%s=%s\n" % (k, t[k]))


def _print_stats(mapping, out_dir):
    t = {"TOTAL_ROWS": len(mapping)}
    for s in (STATUS_TRANSLATED, STATUS_LLM, STATUS_REVIEW, STATUS_KEEP):
        t[s] = sum(1 for m in mapping if m["status"] == s)
    t["TRANSLATED_COUNT"] = t[STATUS_TRANSLATED] + t[STATUS_LLM]
    t["REVIEW_COUNT"] = t[STATUS_REVIEW]
    print("VERDICT=GO")
    print("TOTAL_ROWS=%s" % t["TOTAL_ROWS"])
    print("TRANSLATED_COUNT=%s" % t["TRANSLATED_COUNT"])
    print("REVIEW_COUNT=%s" % t["REVIEW_COUNT"])
    print("OUT=%s" % os.path.join(out_dir, "p34_translation_mapping.csv"))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
