#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p33_translation_context.py --- P33 Phase-3: authoritative translation-CONTEXT
dataset over the P32 479-row identifier catalog (NOT a translation, NOT MT).

Goal
----
Turn the 479 real WickedWhims/Nevely42 animations into a dataset that a human/
LLM can localise into natural Simplified-Chinese *with context*.  P33 is the
"before" step: it NEVER produces Chinese, never calls an MT engine, never emits
a translated_name / override json, and never writes saves or Mods.  Each row
carries every piece of surrounding context needed to disambiguate by
category/location/stage/actors/series/clip and to keep terminology and tone
consistent *within a series* (ZERO_WRITE_TO_MODS / ZERO_WRITE_TO_SAVES).

AUTHORITATIVE IDENTITY INHERITANCE (operator mandate, no reimplementation)
--------------------------------------------------------------------------
The SHA1 identifier is NOT rebuilt here.  P33 reads the operating P32 catalog
output (`p32_identifier_catalog.csv`, produced by the real Windows P32 run whose
FINAL WINDOWS GATE = PASS) and copies `ordinal` / `raw_display_name` /
`identifier` / `author` / `source_instance` VERBATIM from each row.  The internal
gate re-asserts a strict 1:1 correspondence (SOURCE_ROWS / CONTEXT_ROWS /
IDENTIFIER_MATCH_COUNT / IDENTIFIER_MISMATCH_COUNT / ORDINAL_UNIQUE_COUNT); any
mismatch -> VERDICT=STOP and no outputs that could misalign a series are trusted.
Series grouping never mutates identifier or source order.

WHY CSV-CENTRIC (not a re-parse of the real XML)
-----------------------------------------------
P32 already validated location/gender/clip/props/object from the real source XML
and wrote the *authoritative* per-row context into the CSV (`sex_category`,
`locations`, `actor_genders`, `actor_clips`, `object_animation_clip_name`,
`prop_animation_clip_names`, `tags`, `stage_name`).  Re-parsing the XML here
would risk re-deriving identity-adjacent fields and drifting from the pinned
catalog.  P33 therefore treats the CSV as the single source of truth, copies the
already-bridged context through, and only ADDS what the CSV cannot give:
series_key/index/size, prev/next raw names, and the translation_status/note
columns.  `actor_tags`: per-actor tag granularity is not present in the P32 CSV,
so it is emitted empty and never guessed ("if reliably recoverable" caveat).

SERIES CONTEXT (conservative, translation-consistency only)
-----------------------------------------------------------
A multi-entry series is acknowledged ONLY from structural neighbour evidence:
  * adjacent ordinals (same author, no other author/family interleaving),
  * a shared name stem (leading non-numeric text before the first trailing
    number / Roman / separator),
  * a consistent trailing sequence token (decimal number, Roman numeral, or an
    explicit "- Climax"/"- Orgasm" etc. ordinal-pair suffix),
  * clip-naming continuity is NOT required to group but a mismatched author or an
    unrecognised stem breaks the chain.
Two titles that only *look* thematically close (different stems) are NEVER fused.
No reliable evidence -> the row is a STANDALONE series of size 1 with its ordinal
as the only member.  Grouping never reorders or renumbers; series_index is the
per-group member position in source order, series_size the group membership count.

Title structure (translation aid, NEVER overwrites raw)
-------------------------------------------------------
title_stem / title_sequence_token / title_suffix are derived for inspection
(e.g. "Gearshift 6 - Climax" -> Gearshift / 6 / Climax).  raw_display_name is
always preserved byte-for-byte; these extras live in their own columns.

CONTEXT PREVIEW
---------------
The report prints a small curated human-review sample only (the operator's
archetype list), NOT a 479-line dump, to keep stdout small.

Outputs (under --out-dir)
    p33_translation_context.csv
    p33_translation_context.jsonl
    p33_translation_context_report.txt
Writes nothing else.  Exit 0 VERDICT=GO; 3 STOP on any gate / i/o problem.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

EXPECTED_SOURCE_INSTANCE = "0x43F3438A94EDEB2B"
EXPECTED_SOURCE_SHA = ("cd0093f2ec4b896121fa465672584c12"
                       "384465b631c1d9128fe97d360b87d416")
GOLD_ORDINAL = 318
GOLD_NAME = "NOT Caught Cheating 2"
GOLD_ID = "d0528d3795ca42c60ca5a9eb6bad8658ea74e4b5"
EXPECT_ROWS = 479

# result-column schema (P33 context model; the P32 identity columns are verbatim)
P33_COLUMNS = [
    # ---- identity (P32 inherit) ----
    "source_instance", "ordinal", "identifier",
    # ---- text identity ----
    "raw_display_name", "author", "stage_name",
    # ---- content context (P32 bridge passthrough) ----
    "sex_category", "actor_count", "locations", "actor_genders", "actor_clips",
    "object_animation_clip_name", "prop_animation_clip_names",
    # ---- tags ----
    "animation_tags", "actor_tags",
    # ---- series / title structure (translation aid) ----
    "series_key", "series_index", "series_size",
    "title_stem", "title_sequence_token", "title_suffix",
    # ---- ordering context ----
    "prev_raw_display_name", "next_raw_display_name",
    # ---- translation bookkeeping ----
    "translation_status", "translation_note",
]

_ROMAN = {
    "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7,
    "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12, "XIII": 13,
    "XIV": 14, "XV": 15, "XX": 20,
}


def _strip(name):
    return (name or "").strip()


def parse_title_structure(raw):
    """Return (stem, seq_token, suffix) translation-aid fields for one raw name.
    Conservative: never modifies raw.  Recognises decimal trailing numbers,
    Roman trailing numerals, and a dash-separated trailing tag (e.g. Climax).
    Falls back to (raw, "", "") when no structure is reliable."""
    raw = _strip(raw)
    if not raw:
        return ("", "", "")
    name = raw
    suffix = ""
    # Strip only a dash-separated trailing WORD tag (Climax/Orgasm/Recovery...).
    # A dash followed by a number/part is left intact so numeric reverse-parse
    # treats it as the run token (e.g. "Heat I - 1").  Tail must be alphabetic.
    for sep in (" - ", " -- ", "-"):
        if sep in name:
            head, _, tail = name.rpartition(sep)
            t = tail.strip()
            if head.strip() and t and _looks_tag(t):
                name, suffix = head.strip(), t
            break
    toks = name.split()
    if not toks:
        return (raw, "", suffix)
    last = toks[-1].strip(",")
    # decimal trailing number e.g. "... 6" -> seq "6"
    if last.isdigit():
        stem = " ".join(toks[:-1]).strip() or raw
        return (stem, str(int(last)), suffix)
    # Roman trailing numeral at a word boundary
    if last in _ROMAN:
        if len(last) >= 2 and "".join(toks[:-1]).strip():
            return (" ".join(toks[:-1]).strip(), last, suffix)
    return (raw, "", suffix)


def _looks_tag(tok):
    # a dash tail that is a word tag (Climax/Orgasm/Recovery/...) - advisory only
    return len(tok) >= 2 and tok.isalpha()


def build_context_rows(rows):
    """rows: list of P32 CSV dicts in ordinal order (0..478).  Return a list of
    P33 row dicts in the SAME order (identity + context), one per input row.
    Raises on any ordinal/row-count inconsistency."""
    if len(rows) != EXPECT_ROWS:
        raise RuntimeError("P32_ROWS=%d != %d" % (len(rows), EXPECT_ROWS))
    idx = [int(r["ordinal"]) for r in rows]
    if idx != list(range(EXPECT_ROWS)):
        raise RuntimeError("ordinals not a contiguous 0..478 sequence: %r"
                           % (idx[:5], idx[-3:]))
    base = []
    for r in rows:                      # identity is inherited verbatim
        base.append({
            "src_inst": _strip(r.get("source_instance", "")),
            "ord": int(r["ordinal"]),
            "id": _strip(r.get("identifier", "")),
            "raw": _strip(r.get("raw_display_name", "")),
            "author": _strip(r.get("author", "")),
            "stage_name": _strip(r.get("stage_name", "")),
            "sex_category": _strip(r.get("sex_category", "")),
            "actor_count": r.get("actor_count", ""),
            "locations": _strip(r.get("locations", "")),
            "genders": _strip(r.get("actor_genders", "")),
            "clips": _strip(r.get("actor_clips", "")),
            "objclip": _strip(r.get("object_animation_clip_name", "")),
            "propclips": _strip(r.get("prop_animation_clip_names", "")),
            "tags": _strip(r.get("tags", "")),
        })
    # detect multi-entry conservative series over the ordered list
    groups = _detect_series(base)
    key_of = {}
    for g_key, members in groups.items():
        for ord_, name in members:
            key_of[ord_] = g_key
    seen = {}
    for i, b in enumerate(base):
        ord_ = b["ord"]
        stem, seq, suffix = parse_title_structure(b["raw"])
        nxt = base[i + 1]["raw"] if i + 1 < len(base) else ""
        prv = base[i - 1]["raw"] if i - 1 >= 0 else ""
        grp = key_of.get(ord_)
        if grp is not None:
            members = groups[grp]
            # map ordinal -> 1-based position by order of ordinals in this group
            pos_order = {m[0]: p for p, m in enumerate(members, 1)}
            size = len(members)
            key = grp
            si = pos_order[ord_]
            ss = size
        else:
            key, si, ss = "", 1, 1
        yield {
            "source_instance": b["src_inst"],
            "ordinal": ord_,
            "identifier": b["id"],
            "raw_display_name": b["raw"],
            "author": b["author"],
            "stage_name": b["stage_name"],
            "sex_category": b["sex_category"],
            "actor_count": b["actor_count"],
            "locations": b["locations"],
            "actor_genders": b["genders"],
            "actor_clips": b["clips"],
            "object_animation_clip_name": b["objclip"],
            "prop_animation_clip_names": b["propclips"],
            "animation_tags": b["tags"],
            "actor_tags": "",            # not granularly recoverable from P32 csv
            "series_key": key,
            "series_index": si,
            "series_size": ss,
            "title_stem": stem,
            "title_sequence_token": seq,
            "title_suffix": suffix,
            "prev_raw_display_name": prv,
            "next_raw_display_name": nxt,
            "translation_status": "UNTRANSLATED",
            "translation_note": "",
        }


def _stem_key(raw, stem):
    """A deterministic grouping key: author + leading-stem only; the trailing
    sequence number is intentionally EXCLUDED so 1..N neighbours share the key."""
    return (stem or "").strip().lower()


def _seq_number(seq_token):
    if not seq_token:
        return None
    if seq_token.isdigit():
        return int(seq_token)
    if seq_token in _ROMAN:
        return _ROMAN[seq_token]
    return None


def _detect_series(rows):
    """Conservative neighbour grouping -> {key: [(ordinal, raw_name), ...]}.
    Groups only ADJACENT ordinals sharing author+stem with an increasing numeric
    or Roman trailing sequence.  Returns dict; multi-entry groups only.  No
    freeform "topic feels similar" fusion."""
    rows = list(rows)
    groups = {}
    i = 0
    n = len(rows)
    while i < n:
        b = rows[i]
        stem, seq, suffix = parse_title_structure(b["raw"])
        base_author = b["author"]
        j = i + 1
        # members must immediately follow with same author + same stem and a
        # strictly increasing numeric/Roman sequence token.
        prev_num = _seq_number(seq)
        members = [(b["ord"], b["raw"])]
        while prev_num is not None and j < n:
            nb = rows[j]
            if nb["author"] != base_author:
                break
            nstem, nseq, nsuf = parse_title_structure(nb["raw"])
            n_num = _seq_number(nseq)
            if _stem_key(b["raw"], stem) != _stem_key(nb["raw"], nstem):
                break
            if n_num is None or n_num != prev_num + 1:
                break
            members.append((nb["ord"], nb["raw"]))
            prev_num = n_num
            j += 1
        if len(members) >= 2:
            groups["S%05d" % members[0][0]] = members
        i = max(i + 1, j if len(members) > 1 else i + 1)
    return groups


# ---------------------------------------------------------------------------
def _to_jsonable(x):
    # keep numbers/bools as themselves (ordinal/series_index/size/actor_count);
    # None -> "" for CSV round-trip convenience.
    if x is None:
        return ""
    return x


def read_p32_csv(csv_path: Path):
    with open(csv_path, "r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _ctx_rows_to_dicts(gen):
    return list(gen)


def write_outputs(out_dir: Path, rows, source_sha, source_inst):
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "p33_translation_context.csv", "w", encoding="utf-8",
              newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=P33_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: _to_jsonable(r[k]) for k in P33_COLUMNS})
    with open(out_dir / "p33_translation_context.jsonl", "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps({k: _to_jsonable(r[k]) for k in P33_COLUMNS},
                                ensure_ascii=False) + "\n")
    report = _build_report(rows, source_sha, source_inst)
    (out_dir / "p33_translation_context_report.txt").write_text(report, encoding="utf-8")
    return report


def _group_stats(rows):
    sizes = {}
    for r in rows:
        k = r["series_key"]
        if k:
            sizes[k] = r["series_size"]
    multi = {k: v for k, v in sizes.items() if v >= 2}
    return len(multi), len([r for r in rows if r["series_key"]]), sizes


def _sample_rows(rows):
    """Curated human-review sample covering the operator's archetypes.  Only a
    handful of rows (never a 479 dump).  Prefer rows whose fields light each
    archetype, dedup by archetype list."""
    want = []
    seen = set()

    def pick(**pred):
        for r in rows:
            ok = all(r.get(k) == v for k, v in pred.items())
            rwc = tuple(sorted(pred.items()))
            if ok and rwc not in seen:
                seen.add(rwc)
                return r
        return None

    # 1 / 2 / 3+ actor
    want.append(pick(actor_count="1"))
    w2 = pick(actor_count="2")
    # BOTH gender
    both = next((r for r in rows if "BOTH" in (r.get("actor_genders") or "")), None)
    nov = next((r for r in rows if not (r.get("locations") or "").strip()), None)
    db = next((r for r in rows if "DOUBLE_BED" in (r.get("locations") or "")), None)
    door = next((r for r in rows if "DOOR" in (r.get("locations") or "")), None)
    og = next((r for r in rows if r.get("object_animation_clip_name")), None)
    pg = next((r for r in rows if r.get("prop_animation_clip_names")), None)
    cl = next((r for r in rows if r.get("title_suffix")), None)
    rom = next((r for r in rows if r.get("title_sequence_token") and
                not r["title_sequence_token"].isdigit()), None)
    sa0 = next((r for r in rows if not r.get("series_key")), None)
    three = next((r for r in rows if int(r["actor_count"]) >= 3), None)
    want.append(w2)
    for x in (both, nov, db, door, og, pg, cl, rom, three, sa0):
        if x is not None and x not in want:
            want.append(x)
    return want[:13]


def _build_report(rows, source_sha, source_inst):
    valid = [r for r in rows if r.get("identifier")]
    id_match = sum(1 for r in rows if bool(r.get("identifier")))
    ids = [r.get("identifier") for r in rows if r.get("identifier")]
    id_mismatch = sum(1 for r in rows if not r.get("identifier"))
    ord_uniq = len({r["ordinal"] for r in rows})
    multi, in_series, _sizes = _group_stats(rows)
    stand = len(rows) - in_series
    untr = sum(1 for r in rows if r["translation_status"] == "UNTRANSLATED")
    ord318 = next((r for r in rows if r["ordinal"] == GOLD_ORDINAL), None)
    g318 = bool(ord318 and ord318["raw_display_name"] == GOLD_NAME
                and ord318["identifier"] == GOLD_ID)
    ok = (len(rows) == EXPECT_ROWS and id_mismatch == 0
          and ord_uniq == EXPECT_ROWS and id_match == EXPECT_ROWS
          and g318 and source_sha.startswith(EXPECTED_SOURCE_SHA[:8])
          and source_inst == EXPECTED_SOURCE_INSTANCE)
    lines = []
    lines.append("SOURCE_SHA256=%s" % source_sha)
    lines.append("SOURCE_INSTANCE=%s" % source_inst)
    lines.append("P32_ROWS=%d" % len(rows))
    lines.append("CONTEXT_ROWS=%d" % len(rows))
    lines.append("IDENTIFIER_MATCH_COUNT=%d" % id_match)
    lines.append("IDENTIFIER_MISMATCH_COUNT=%d" % id_mismatch)
    lines.append("SERIES_GROUP_COUNT=%d" % multi)
    lines.append("MULTI_ENTRY_SERIES_COUNT=%d" % multi)
    lines.append("STANDALONE_COUNT=%d" % stand)
    lines.append("UNTRANSLATED_COUNT=%d" % untr)
    lines.append("ORD318_CONTEXT_CHECK=%s" % ("PASS" if g318 else "FAIL"))
    lines.append("FULL_CONTEXT_SAFE=%s" % ("YES" if ok else "NO"))
    lines.append("VERDICT=%s" % ("GO" if ok else "STOP"))
    lines.append("")
    lines.append("== CONTEXT PREVIEW (curated human-review sample) ==")
    lines.append("ordinal | ac| loc        | genders      | series[#/size] | category | id8 | raw_display_name")
    for r in _sample_rows(rows):
        ac = r["actor_count"]
        loc = (r["locations"] or "-")
        gd = (r["actor_genders"] or "-")
        ser = ("%s[%s/%s]" % (r["series_key"] or "-", r["series_index"],
                               r["series_size"]))
        id8 = (r["identifier"] or "-")[:8]
        lines.append("%6d | %2s | %-12s | %-12s | %-12s | %-8s | %-8s | %s"
                     % (r["ordinal"], ac, loc, gd, ser, r["sex_category"], id8,
                        r["raw_display_name"]))
    lines.append("")
    lines.append("== ORDINAL 318 CONTEXT ==")
    if ord318:
        for k in ("ordinal", "identifier", "raw_display_name", "author",
                  "sex_category", "locations", "actor_genders", "actor_clips",
                  "series_key", "series_size", "stage_name"):
            lines.append("%s=%s" % (k, ord318.get(k, "")))
    return "\n".join(lines) + "\n"


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?", default=None,
                    help="P32 identifier catalog CSV (p32_identifier_catalog.csv)")
    ap.add_argument("--source-sha", default=EXPECTED_SOURCE_SHA,
                    help="source package SHA256 to record (default: pinned)")
    ap.add_argument("--source-instance", default=EXPECTED_SOURCE_INSTANCE)
    ap.add_argument("--out-dir", default="output/p33")
    ap.add_argument("--role", action="store_true",
                    help="print README-style role/client note then exit")
    return ap.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    if a.role:
        print(P33_DOC)
        return 0
    if not a.csv:
        print("need the P32 catalog CSV path (see --help)", file=sys.stderr)
        return 3
    try:
        csv_rows = read_p32_csv(Path(a.csv))
        ctx = list(build_context_rows(csv_rows))
        out_dir = Path(a.out_dir)
        report = write_outputs(out_dir, ctx, a.source_sha, a.source_instance)
    except Exception as e:                       # pragma: no cover - gate path
        print("P33_GATE=%s" % e, file=sys.stderr)
        return 3
    sys.stdout.write(report)
    if any(ln.startswith("VERDICT=GO") for ln in report.splitlines()):
        return 0
    return 3


P33_DOC = """\
P33 translation-context dataset (NOT translation / NOT MT).
Reads P32 p32_identifier_catalog.csv (real Windows GATE=PASS) and adds a
human/LLM localisation-context layer: verbatim identity inheritance; P32
bridge-context passthrough; conservative multi-entry series detection on
adjacent author+stem+trailing numeral/Roman tokens; prev/next raw name;
title structure aid; translation_status=UNTRANSLATED/note='' initially.
ZERO_WRITE_TO_MODS / ZERO_WRITE_TO_SAVES; never alters P32 outputs."""


if __name__ == "__main__":
    sys.exit(main())
