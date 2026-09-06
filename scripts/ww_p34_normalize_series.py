#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p34_normalize_series.py --- P34.1 deterministic series-stem normalization
(a post-processing layer, NO LLM re-call).

WHY
---
The P34 real run translated 479 rows 100% via Ollama.  A QA INV5 check found
that different members of the same series_key disagree on the Chinese stem:
a series whose title_stem is NOT a single glossary word (prose/multi-word, e.g.
"Caught Cheating", "Neighbors Secret") has no deterministic stem_cache base, so
each member was sent to the LLM SEPARATELY -> divergent Chinese phrases.

P34.1 fixes this purely deterministically, over the ALREADY-completed mapping:
  * it does NOT re-request the LLM;
  * it does NOT modify raw_display_name or identifier;
  * it only rewrites translated_name -- and only where it can do so
    unambiguously.  Nothing is guessed.

CONVENTIONS (mirror scripts/p34_translate_adapter.py)
  Canonical member form:  "<zh_stem> <num>[ - <suffix_zh>]"
      determinstic examples:
        "Gearshift 6 - Climax" -> "齿轮摇摆 6 - 高潮"   (numeral "6", suffix Climax)
        "Kiss 3"               -> "接吻 3"              (numeral only)
  Stem = the shared Chinese phrase common to a series, EQUAL for all members.
  num / suffix per member are recovered from that member's OWN P33 columns:
      title_sequence_token  -> the numeral part
      title_suffix          -> e.g. Climax -> "高潮" (Climax always 高潮)

RULES (P34.1, per operator)
  A. one unique zh stem per series_key:
       - the first valid member (in mapping ordinal order) provides the stem
         candidate;
       - every later member of the same series asserts / inherits that stem.
     A member is VALID as a stem source when its translated_name, stripped of its
     own trailing num/suffix decoration, yields one contiguous stem with no
     stray embedded number token and no leftover internal series number.
  B. suffix is handled independently: Climax -> 高潮 is appended as a SUFFIX
     after a separator the source uses, forming  "num - 高潮"  -- never bare
     "xxx 高潮" glued to the stem, and never squeezing it mid-stem.
  C. numbering is preserved from the row's own P33 title_sequence_token (falling
     back to the number present in raw_display_name when the token is blank).
  D. standalone / unresolved rows outside any recoverable series or whose stem
     cannot be established are LEFT UNTOUCHED (keep current translation).

OUTPUT
  Reads  p34_translation_mapping.csv   +   p33_translation_context.csv
  Writes p34_translation_mapping_normalized.csv   (same 7 mapping columns;
    translated_name overwritten ONLY where this pass proves a rewrite is safe).

ZERO_WRITE: never modifies the ORIGINAL mapping; output is a NEW file.
Read-only discipline: this script writes only its one output file.

Exit: 0 success (output written even if 0 rows rewritten); 2 input/gate failure.
"""
from __future__ import print_function
import argparse
import csv
import os
import re
import sys

MAP_COLS = ["source_instance", "ordinal", "identifier", "raw_display_name",
            "translated_name", "status", "note"]

# --------------------------------------------------------------------------- #
# Pure helpers (no I/O) ------------------------------------------------------ #
# --------------------------------------------------------------------------- #
def suffix_zh(suffix):
    """Canonical zh of a P33 title_suffix.  Climax -> 高潮 (numeric continuation
    e.g. Climax2 -> 高潮 2 preserved).  Unknown suffix kept verbatim (never
    mis-glossed)."""
    s = (suffix or "").strip()
    if not s:
        return ""
    m = re.match(r"^(?i:climax)\s*(\d*)$", s)
    if m:
        return "高潮" + ((" " + m.group(1)) if m.group(1) else "")
    return s


def _tail_deco(zh):
    """Return (stem, tail).  stem is zh with any clean trailing decoration
    (' N', ' N - suffix', roman ' II') removed; tail is the removed decoration
    (may be '').  Conservative: only strips well-formed numeral tails (arabic or
    a short roman)."""
    s = (zh or "").strip().rstrip()
    # '<stem> N - <suffix>' (dash forms; N arabic or roman)
    m = re.match(
        r"^(?P<stem>.*?)\s+(?P<n>\d{1,4}|[ivxlcdm]{1,5})\s*[-–—]\s*(?P<sfx>.*?)\s*$",
        s, re.S | re.I)
    if m:
        tail = s[m.start("n"):]
        return m.group("stem").strip(), tail
    # '<stem> N' (numeral-only; arabic or roman)
    m2 = re.match(
        r"^(?P<stem>.*?)\s+(?P<n>\d{1,4}|[ivxlcdm]{1,5})\s*$", s, re.S | re.I)
    if m2:
        tail = s[m2.start("n"):]
        return m2.group("stem").strip(), tail
    return s, ""


def _stem_of(zh):
    """Recover the shared stem of a member translation by stripping its own
    trailing num/suffix decoration.  Returns None when the result is not a
    clean single stem (contains a stray internal ASCII[CJK-mixed digit or an
    embedded extra number), so the member is NOT a trustworthy stem source."""
    stem, _ = _tail_deco(zh or "")
    if not stem:
        return None
    # reject stems that still carry a digit token in the body (ambiguous)
    if re.search(r"[0-9]", stem):
        return None
    return stem


def member_deco(stem, seq, suffix):
    """Deterministic rebuild of a member translated_name from its shared stem
    + its OWN numeral (seq) + suffix (suffix_zh), in the CANONICAL series form:
        <stem> <N>[ - <suffix_zh>]
    The suffix is ALWAYS joined with ' - ' (rule B forbids a bare-glued 高潮 or
    a missing dash); a series member is numbered by construction, so the raw
    dash signal is not needed.  Examples:
        stem='齿轮摇摆', seq='6', suffix='Climax' -> '齿轮摇摆 6 - 高潮'
        stem='接吻',    seq='2', suffix='Climax' -> '接吻 2 - 高潮'
        stem='捉奸进行时', seq='2', suffix=''    -> '捉奸进行时 2'
    """
    out = stem
    if seq:
        out += " " + seq
    suff = suffix_zh(suffix)
    if suff:
        out += " - " + suff
    return out


# --------------------------------------------------------------------------- #
# Loaders (fail-closed) ------------------------------------------------------ #
# --------------------------------------------------------------------------- #
def load_mapping(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        cols = rd.fieldnames or []
        need = {"ordinal", "identifier", "raw_display_name", "translated_name",
                "status"}
        if not need.issubset(cols):
            raise ValueError("mapping missing required cols need=%s got=%s"
                             % (sorted(need), sorted(cols)))
        return [dict(x) for x in rd]


def load_context(path):
    """Read REAL P33 context CSV.  Returns rows list[dict].  Fail-closed if
    required identity/series columns absent."""
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        cols = rd.fieldnames or []
        need = {"identifier", "raw_display_name", "series_key",
                "title_sequence_token", "title_suffix"}
        miss = need - set(cols)
        if miss:
            raise ValueError("context CSV missing columns %s" % sorted(miss))
        return [dict(x) for x in rd]


def _ctx_index(ctx_rows):
    """Build identifier -> dict{series_key,seq,suffix} from the REAL P33 rows."""
    idx = {}
    for r in ctx_rows:
        i = (r.get("identifier") or "").strip()
        if not i:
            continue
        idx.setdefault(i, {
            "series_key": (r.get("series_key") or "").strip(),
            "seq": (r.get("title_sequence_token") or "").strip(),
            "suffix": (r.get("title_suffix") or "").strip(),
        })
    return idx


# --------------------------------------------------------------------------- #
# Core pass ---------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
def normalize(rows, ctx_idx):
    """Deterministic series-stem unification over completed mapping rows.

    Returns (out_rows, stats) where stats reports rewrites/skips per reason.
    Only rows whose identifier maps to a series_key with >=1 member and for
    which a stem candate can be established are candidates for rewrite.
    """
    # 1) map identifier -> (row, series_key, ordinal-int)
    by_series = {}
    idx_ord = {}
    for r in rows:
        i = r.get("identifier")
        c = ctx_idx.get(i)
        sk = (c or {}).get("series_key") if c else None
        if not sk:
            continue
        by_series.setdefault(sk, []).append(r)
        try:
            idx_ord[r["identifier"]] = int(r.get("ordinal") or 0)
        except ValueError:
            idx_ord[r["identifier"]] = 0

    out = list(rows)
    stats = {"series": 0, "multi": 0, "rewritten": 0,
             "kept_no_stem": 0, "kept_single": 0, "orphan": 0}
    changed = {}

    for sk, members in by_series.items():
        stats["series"] += 1
        if len(members) < 2:
            stats["kept_single"] += 1
            continue                       # single-member series: nothing to unify
        stats["multi"] += 1
        ordered = sorted(members, key=lambda r: idx_ord.get(r["identifier"], 0))
        # A: first VALID member provides the shared stem.
        stem = None
        for m in ordered:                  # first that cleanly yields a stem
            s = _stem_of(m.get("translated_name") or "")
            if s is not None:
                stem = s
                break
        if stem is None:
            stats["kept_no_stem"] += 1
            continue                       # cannot derive: leave untouched
        # rebuild every member deterministically; first validate the whole
        # series round-trips back to `stem` (guards against a member whose
        # suffix/numeral decoration would not cleanly strip -> avoid a
        # PARTIAL rewrite that would itself break INV5).
        rebuilt = []
        for m in ordered:
            c = ctx_idx.get(m["identifier"]) or {}
            seq = c.get("seq") or _seq_from_raw(m.get("raw_display_name") or "")
            suff = c.get("suffix") or ""
            new_zh = member_deco(stem, seq, suff)
            rebuilt.append((m, new_zh))
        if any(_stem_of(nz) != stem for _, nz in rebuilt):
            # a rebuild does not strip cleanly back to stem -> refuse the whole
            # series (never half-apply).
            stats["kept_no_stem"] += 1
            continue
        for m, new_zh in rebuilt:
            old_zh = (m.get("translated_name") or "")
            if new_zh and new_zh != old_zh:
                changed[m["identifier"]] = (old_zh, new_zh)
                stats["rewritten"] += 1
    # apply writes only to changed rows (in-place lookup)
    if changed:
        for r in out:
            if r["identifier"] in changed:
                r["translated_name"] = changed[r["identifier"]][1]
    return out, stats, changed


def _seq_from_raw(raw):
    """Fallback numeral extracted from raw tail ('<word> N[- suffix]')."""
    m = re.search(r"(?i)\s*[-–—]?\s*(?:(\d+)|\b([ivxlcdm]{1,5})\b)\s*"
                  r"(?:[-–—]\s*(?:climax|ending|finish))?\s*$", raw or "")
    if not m:
        return ""
    return m.group(1) or (m.group(2) or "").upper()


# --------------------------------------------------------------------------- #
# Writer ------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
def write_mapping(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=MAP_COLS, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mapping_csv",
                    help="P34 mapping CSV (p34_translation_mapping.csv)")
    ap.add_argument("context_csv",
                    help="REAL P33 context CSV (p33_translation_context.csv)")
    ap.add_argument("--out", default="output/p34/p34_translation_mapping_normalized.csv")
    a = ap.parse_args(argv)

    if not os.path.isfile(a.mapping_csv):
        print("VERDICT=FAIL", file=sys.stderr)
        print("REASON=INPUT_MISSING:%s" % a.mapping_csv, file=sys.stderr)
        return 2
    if not os.path.isfile(a.context_csv):
        print("VERDICT=FAIL", file=sys.stderr)
        print("REASON=INPUT_MISSING:%s" % a.context_csv, file=sys.stderr)
        return 2
    try:
        rows = load_mapping(a.mapping_csv)
        ctx_rows = load_context(a.context_csv)
    except ValueError as e:
        print("VERDICT=FAIL", file=sys.stderr)
        print("REASON=READ:%s" % e, file=sys.stderr)
        return 2

    ctx_idx = _ctx_index(ctx_rows)
    out_rows, stats, changed = normalize(rows, ctx_idx)

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    write_mapping(a.out, out_rows)

    print("VERDICT=GO")
    print("OUT=%s" % a.out)
    print("TOTAL_ROWS=%d" % len(rows))
    print("SERIES=%d  MULTI_MEMBER_SERIES=%d" % (stats["series"],
                                                 stats["multi"]))
    print("REWRITTEN=%d" % stats["rewritten"])
    print("ROWS_PRESERVED=%d" % (len(rows) - stats["rewritten"]))
    print("SKIP=single_member_series:%d no_stem:%d"
          % (stats["kept_single"], stats["kept_no_stem"]))
    if stats["rewritten"]:
        print("(wrote %d translated_name cells; identifier & raw unchanged)"
              % stats["rewritten"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
