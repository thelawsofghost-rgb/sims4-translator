# WW 动画中文本地化规则 — translation_rules.md

Phase: P34 Translation Layer (Rule Definition)
Input (authoritative): output/p33/p33_translation_context.csv (real Windows P32/P33 gate = PASS)
Scope here: DEFINE rules only.  This document does NOT translate, does NOT emit a
final Chinese table, does NOT touch raw_display_name / identifier, does NOT build an
STBL, does NOT write Mods / saves.  Machine translation is NOT used.

Constraint ledger (must remain true through P34):
  - ZERO_WRITE_TO_MODS=YES
  - ZERO_WRITE_TO_SAVES=YES
  - No change to: identifier formula / reconstructor / XML parser / loader-default
    decoder / P32 or P33 outputs.
  - Per-row work keys legible to a later translator step:
    identifier, ordinal, raw_display_name, series_key, series_index, series_size,
    sex_category, locations, actor_genders, actor_clips,
    object_animation_clip_name, prop_animation_clip_names.

The 479 titles are WW adult-scene animation display names authored by a small set of
creators (real authors verified via the P32 bridge/provenance: e.g. Nevely42 等按 P33
CSV author 列取值).  Titles lean toward short punchy English noun-phrases / verb
commands, some in multi-part series ("Gearshift 1..4 - Climax").  Everything below is
written to be applied deterministically to the real P33 context CSV; it never invents
vocabulary that is not present as a real location/category/author token.

------------------------------------------------------------------------------
1. TITLE TRANSLATION STYLE (标题翻译风格)
------------------------------------------------------------------------------
1.1 Voice / register:
  - WW scenes are game UI labels, NOT literary prose.  Target register: 口语化、简短、
    成人向但克制、不粗鄙堆砌。  Prefer 2-6 字核心名词/短语；avoid 文绉绉书面语 or 玩梗.
  - Preserve the "scene/position/act" semantics, never tone-policing or moral glossing.
  - Keep a natural Sims-community flavor already established by existing WW zh 词典
    when a canonical term exists.  Where a term is community-standard (见 §6), reuse it
    verbatim; do not coin a rival.

1.2 Form pattern mapping (raw shape -> preferred zh shape):
    raw is 1 token noun        -> flat zh noun      e.g. raw "Kiss"          -> "接吻"
    raw is Adj+N / two-token   -> keep modifier     (modifier meaning matters: 时间/地点/对象)
    raw is verb phrase         -> zh 动作短语 (动宾), not imperative-to-literal where awkward
    raw is "location-y" noun   -> caller relies on §5 location handling for 一致性, but the
                                 title itself keeps its own meaning only if substantive
    raw is "act/category noun" -> map via §6 category glossary, do not re-invent
1.3 Ambiguity of single short English noun (e.g. words with 2+ unrelated senses) MUST be
    flagged for human confirmation (§3), never silently resolved.

------------------------------------------------------------------------------
2. SERIES TITLE HANDLING (系列标题处理规则)
------------------------------------------------------------------------------
2.1 A series (series_size >= 2 per P33) is a single translation unit: all its members
    share ONE canon stem translation, differing only by their trailing numeral/Roman
    token and (if present) a shared episode marker/suffix.  Translate the stem ONCE,
    then decorate per member.
2.2 Stem = series_title_stem from P33 (the P33 build already splits stem / sequence
    token / suffix).  Do not re-derive from raw inside the translator; trust P33 columns.
2.3 Same series ID (series_key) across all members guaranteed => guarantee a translator
    produces identical 中文 stem spelling for every member.  No per-member drift.
2.4 The stem translation is COMMUNITY-REVIEWED once; all size>=3 series go into the
    series inventory review (§P34-A) before batch (per P34 step 16 & task gate).
2.5 Never fuse two different series_keys (=never merge two stem translations).  A series
    that P33 marked standalone (key '', size 1) is NOT a series; do not invent continuity.

------------------------------------------------------------------------------
3. NUMBERING RULES (编号规则)
------------------------------------------------------------------------------
3.1 Preserve EVERY trailing number / Roman numeral exactly as it appears in raw, in the
    SAME position, as an ARABIC digit or the Roman form the source uses.  Do not convert.
    Examples of raw tokens P33 splits as series_sequence_token: 1,2,3,4 / I,II.
3.2 A decimal continuation within a series stays decimal within the zh title too:
    "Gearshift 1/2/3/4" family keeps "1/2/3/4".
    A Roman-coded series (Heat I / Heat II) keeps the SAME Roman token in zh.
    Numerals are a back-reference to the mod's UI/content order = NEVER localised to 汉字
    (一二三) when the ENGLISH order is a content counter, to avoid mismatch with any
    English legacy references.  Only pure "ordinal-part-of-a-proper-episode-name"
    numbering that reads as meaning, not counter, qualifies for 中文 -- and only after
    human confirmation (§3).
3.3 Separators: keep the dash/separation raw uses around the numeral.  Do not re-space or
    re-punctuate numbering (ties to §8 punctuation policy).
    Raw "Gearshift 3 - Climax" -> 译 stem + " 3" + separator + suffix, i.e. numeral stays
    "3", position and spacing preserved.

------------------------------------------------------------------------------
4. SUFFIX RULES (suffix 规则, e.g. Climax)
------------------------------------------------------------------------------
4.1 A suffix is a trailing qualifying word(s) after the numeral that denotes an episode
    beat rather than a new stem (P33 already separates it into title_suffix; the example
    archetype suffix = "Climax").
4.2 Fixed glossary for the most common episode-beat suffixes (apply deterministically):
      Climax    -> "高潮"
      (any other repeating beat suffixes observed in real data: record each ONE into the
       suffix glossary with a single canonical zh, then reuse.  Do not translate a beat
       suffix per-title with synonyms.)
4.3 The suffix is NOT part of the stem; it is translated separately and joined after the
    member's numeral using the SAME separator the raw uses (raw "3 - Climax" -> "3 - 高潮").
4.4 UNKNOWN / ambiguous suffix (e.g. a word that could be a literal part of the scene or a
    beat marker): flag for human (§3).  Never guess between "beat marker" and "content".

------------------------------------------------------------------------------
5. LOCATION WORD HANDLING (地点词处理规则)
------------------------------------------------------------------------------
5.1 The animation carries machine location token(s) = the real location records P33
    'locations' column already validated by P32 (e.g. DOUBLE_BED, FLOOR, DOOR, DESK,
    SHOWER, BATH, CHAIR, COUCH, POOL, KITCHEN 等 -- only tokens actually present in the
    real CSV are in scope).  These are STAND-ALONE place labels and are NOT to be woven
    into every title.
5.2 Location tokens belong to a FIXED location glossary (one zh per token), reused for BOTH
    a) display of the location column and b) any title whose stem IS a location noun -- so
    the location word inside a title agrees with the locations column when they match.
5.3 Canonical zh for the confirmed base set (extend only from tokens actually in real CSV).
    Values below are PROPOSED candidate glosses for operator confirmation -- they are a
    recommendation to lock, not a pre-set final table; a conflicting community WW zh term
    wins after review (§3/§8A.3):
      DOUBLE_BED -> "双人床"     FLOOR   -> "地板"        DOOR   -> "门口/门边"
      DESK       -> "书桌/办公桌" SHOWER  -> "淋浴"        BATH   -> "浴缸"
      CHAIR      -> "椅子"        COUCH   -> "沙发"        POOL   -> "泳池"
      KITCHEN    -> "厨房"
    (任何在真实 CSV 中出现但未列出的地点 token -> 收进灰色区 §3/§8A.3, 不给猜测译名.)
5.4 Do NOT repeat the location word if it would read redundantly in the already-short zh
    title.  Locations is metadata; the title should still stand alone.

------------------------------------------------------------------------------
6. SEX-CATEGORY / ACTION-CATEGORY WORD HANDLING (动作类别词处理规则)
------------------------------------------------------------------------------
6.1 P33 sex_category is a machine enum NAME (already validated).  These are the act-level
    gloss used when a title is literally the act name.  Fixed glossary (community WW zh;
    PROPOSED candidates, operator confirms -- final token gloss is locked after review):
      VAGINAL -> "性交/阴道性交"   ORAL -> "口交"    ANAL -> "肛交"
      HANDJOB -> "手交/手活"      GSSEX -> "女女(蕾丝)性交"  (GSSEX 译法易歧义, 进 §8A.3 人工确认)
    (只扩真实 CSV 里出现的类别; 未出现类别不预设.)
6.2 Category words inside a title (e.g. raw includes the act) should reuse the SAME swatch
    as §6.1 so the title agrees with sex_category; this prevents 同动作两套中文.
6.3 A category label is never "translated creatively"; it is mechanical glossary reuse.
    Any title where the act word carries extra scene shading beyond the bare category
    (e.g. modifier + act) is a normal title translation, subject to section 1 style.

------------------------------------------------------------------------------
7. KEEP-WORDS RULE (保留词: 作者 / 人名 / 品牌)
------------------------------------------------------------------------------
7.1 Preserve verbatim (English) and do NOT translate:
      - creator/author names (Nevely42 等 P33 author 值)  -- they are handles.
      - people/character proper names appearing in a title (first names, nicknames).
      - real-world brand / franchise tokens (game/chanel/sto) -- they are trademarks/handles.
7.2 A retained Latin word is rendered in the zh string exactly as written (case sensitivity
    per §8).  It is copied, not transliterated, unless a universally accepted 中文
    community exonym exists for a real person/brand -- and that only after §3 confirmation.
7.3 The author is a UNIT not occurring inside rows' actor names necessarily -- P33 author
    column is metadata; never place author in the translated display unless the raw title
    literally contains the author handle, in which case keep it (per 7.1) and flag.

------------------------------------------------------------------------------
8. CASE & PUNCTUATION RULES (大小写和标点规则)
------------------------------------------------------------------------------
8.1 The source raw_display_name is byte-exact English.  Our translation is a separate
    target field; it must never mutate raw.  For text we CONTENT-translate we write 全角/
    半角 by rule below; retained Latin tokens keep their source case.
8.2 PUNCTUATION follows the SOURCE spacing of a member within a series:
      - source "Gearshift 3 - Climax" => zh "… 3 - 高潮" (即数字两侧与后缀以 " - " 拼接,
        半角空格 + 半角连字符, 保持原间距风格).  Do not switch to 全角 dash 或加多余空格.
      - title-final period/question never added; never strip a meaningful hyphen INSIDE a
        word (e.g. a hyphenated compound keeps its hyphen).
8.3 No Chinese-leading article/particle padding.  No trailing "呢/啦/啊" unless the raw
    communicative tone genuinely carries it (rare; confirm via §3).
8.4 Retained-English RUNS stay exactly as in raw capitalisation (do not upper/lowercase a
    brand/handle you are keeping).  Content-zh portions are not affected by English case.
8.5 Full-width vs half-width: use half-width digits and half-width ASCII punctuation for
    numerals/separators inside a title (ties to §3/§8.2).  Use Chinese punctuation only
    where a real Chinese clause needs it (rare inside a short label) -- prefer no heavy
    punctuation on short labels.

------------------------------------------------------------------------------
P34-A. SERIES INVENTORY (series_size >= 3) -- PROCEDURE, files, and ambiguity list
------------------------------------------------------------------------------
This section defines WHAT MUST be produced from the real P33 CSV and HOW it is produced.
The concrete inventory cannot be truthfully enumerated on this Linux sandbox because the
real 479-row CSV lives on the Windows box (P32/P33 real gate outputs are Windows-only);
see "held-back items" below.  The producer script walks output/p33/p33_translation_context.csv
deterministically so the Windows run yields a reviewable inventory.

8A.1 Inputs of interest (per operator): every row with series_size >= 3.  Group by
     series_key (all members share key).  For each such series emit:
       series_key | size | members(ordinal: raw title) | sex_category | locations | author
8A.2 Recommend an explicit translation policy PER series ("策略"): 建议 stem 中文 + 如何带
     编号/suffix, whether it is a clean counter-series (deterministic label, no review
     beyond stem) or contains scene-text needing per-member care.
8A.3 AMBIGUITY list ("需要人工确认的歧义列表"): each row/series flagged for human YES/NO
     or for term-choice when:
       a) stem word has 2+ unrelated senses (see 1.3),
       b) a location/category/suffix token not in the fixed glossaries (§5/§6/§4),
       c) raw mixes a kept L2 word with content such that separation is uncertain (7.x),
       d) a numeral/Roman could be content rather than a counter (3.2),
       e) two members of different series_keys share a stem-looking word but P33 kept them
          separate (double-check the split)  -- 人工复核 series 判定,
       f) title = author/person-name only (keep-word risk).
     Every flagged item = one line with ordinal/raw/why + "ACTION(operator)=" to fill in.
8A.4 Output files under output/p34/ (produced by the Windows run when real CSV present):
       p34_series_inventory.txt      (8A.1 + 8A.2 recommendations)
       p34_ambiguity_review.tsv      (8A.3 flag list; operator fills ACTION)
     Not produced here (input absent).  No final Chinese table is emitted at this stage.

------------------------------------------------------------------------------
HELD-BACK / HONEST BOUNDARY (must be surfaced)
------------------------------------------------------------------------------
  * This commit = rule document only.  translation_rules.md is authored and submitted.
  * The literal series inventory + ambiguity list (P34-A-8A) are NOT included as concrete
    real rows here because output/p33/p33_translation_context.csv is a real Windows
    artifact not present on this Linux box.  Producing them from synthetic rows would
    fabricate the real series structure -- forbidden by project discipline.
  * Ready in repo so the operator's Windows run yields them deterministically:
      (producer hook) -- to be landed with the P34-BATCH scripts, keyed to §P34-A.
  * 请把你 Windows 上的 output/p33/p33_translation_context.csv 放到可读取位置或以文本回贴
    其 series_size>=3 的系列段; 我随即输出 8A.1 inventory / 8A.2 推荐策略 / 8A.3 歧义表,
    再进入 P34-BATCH, 全程不改 raw/identifier/不生成 STBL/不写 Mods.
