#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p34_suffix_cleanup.py --- P34.2 deterministic suffix cleanup / series-stem
unification for the NON-Climax episode-beat suffixes (after P34.1).

WHY (operator-reported QA INV5 failures after P34.1)
----------------------------------------------------
P34.1 fixed most series, but INV5 (same series_key => one zh stem) still fails
on series whose members end in a NON-Climax beat suffix, e.g.
    "All Yours 2 - Creampie"
    "Piano Love 10 - AfterSex"
    (series "Giving Her Pleasure" with AfterSex/Climax/Creampie variants)
Root cause = suffix-parser coverage: P34.1 / the adapter only knew Climax -> 高潮,
so a member with AfterSex/Creampie/Intro/End was rebuilt (or left) with the
suffix VERBATIM in English ("钢琴之爱 10 - AfterSex"), and the QA stem-stripper
(ww_p34_spotcheck._zh_series_stem) only strips a 高潮 / numeral tail -- so it
could not compare members of an AfterSex series on the true Chinese stem, and
the series stayed divergent.

P34.2 fixes this WITHOUT re-calling the LLM, purely deterministically, as a NEW
pipelines stage on top of P34.1's output (normalized.csv -> normalized2.csv):
  * it NEVER modifies the ORIGINAL mapping nor P34.1's file;
  * it only rewrites translated_name, and only for a series whose members all
    carry a recognised episode-beat suffix (Climax/AfterSex/Creampie/Intro/End)
    AND whose Chinese stem can be established unanimously by majority vote;
  * raw_display_name & identifier are preserved byte-for-byte.

RULES (per operator)
  1. For every member of a multi-member series_key, re-parse raw_display_name
        TITLE + sequence + suffix
     e.g.
        "All Yours 2 - Creampie"         -> stem_raw='All Yours' seq='2' sfx='Creampie'
        "Piano Love 10 - AfterSex"       -> stem_raw='Piano Love' seq='10' sfx='AfterSex'
     into stem_raw / sequence / suffix_raw.
  2. The suffix is translated INDEPENDENTLY from the shared vocabulary
     (see scripts/ww_p34_suffix_table.py) and NEVER enters the stem:
        Climax      -> 高潮      AfterSex -> 后戏
        Creampie    -> 射精      Intro    -> 开场
        End/Ending  -> 结束
  3. A series stem is chosen by MAJORITY / common translated zh stem across the
     series members (a member's zh stem = its translated_name with its own
     trailing 'sequence[ - suffix]' decoration stripped).  If an already-stable
     zh stem exists it is inherited.  Tie / unclear => fall back to the first
     clean member; if NONE is clean, series is left untouched (never guessed).
  4. Standalone / rows whose stem cannot be established are NOT processed
     (keep current translation -- P34.1 unchanged).
  5. identifier and raw_display_name are unchanged byte-for-byte.
  Every handled member is rebuilt canonically as  "<stem zh> <seq>[ - <sfix zh>]"
  so the suffix is its own clause and QA (INV5) can compare members on the stem.

A series is only rewritten when the rebuild is a strict consistency win AND the
whole series round-trips to the single chosen stem (no partial edit).  Members
with a recognised suffix are rebuilt; members without one keep the stem + their
own seq (same canonical shape), so the whole series converges.

OUTPUT
  Reads  p34_translation_mapping_normalized.csv   (P34.1 out)  +  p33 ctx
  Writes p34_translation_mapping_normalized2.csv  (7 mapping cols;
    translated_name overwritten only for safely-rewritten series members).

ZERO_WRITE: never modifies the input file; output is a NEW file.
Exit: 0 success (even if 0 cells rewritten); 2 input/gate failure.
"""
from __future__ import print_function
import argparse
import csv
import os
import re
import sys

# shared, single-source suffix vocabulary + raw/zh builders (P34.2 AND QA use
# the same table => no drift between what we build and what INV5 compares).
try:
    import ww_p34_suffix_table as T
    _T_ERR = None
except Exception as _e:                    # pragma: no cover - import env
    T = None
    _T_ERR = repr(_e)

MAP_COLS = ["source_instance", "ordinal", "identifier", "raw_display_name",
            "translated_name", "status", "note"]

# alternation of the canonical zh suffix values (longest first) for stripping a
# bare " - suffix" with no numeral during stem recovery; derived from the SAME
# shared table.  Empty when the table is absent (never fabricates suffix words).
_suffix_zh_vals = "".join(
    "(" + "|".join(re.escape(v) for v in sorted(
        {v for v in (T.SUFFIX_ZH or {}).values() if v}, key=len, reverse=True))
    + ")") if T is not None and getattr(T, "SUFFIX_ZH", None) else ""


# --------------------------------------------------------------------------- #
# Pure helpers -------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
def _zh_stem_recover(zh):
    """Strip a member zh down to a candidate Chinese stem by removing its own
    trailing decoration: " <seq>[ - <suffix text>]" (or a bare " - <known
    suffix zh>").  Language-agnostic about an UNKNOWN suffix text (works whether
    the LLM/P34.1 wrote 高潮 / 后戏 / or verbatim 'AfterSex'); a KNOWN canonical
    suffix zh is also stripped when written without a preceding numeral (a
    member whose raw has a suffix but no episode number).  Returns a clean
    digit-free CJK stem, or None when not trustworthy.
    """
    s = (zh or "").strip().rstrip()
    stem = None
    # "<stem> N - <anything>" (dash forms; N arabic or roman)
    m = re.match(
        r"^(?P<stem>.*?)\s+(?P<n>\d{1,4}|[ivxlcdm]{1,5})\s*[-–—]\s*"
        r"(?P<sfx>\S.*?)\s*$", s, re.S | re.I)
    if m:
        stem = m.group("stem").strip()
    elif _suffix_zh_vals:
        # bare "<stem> - <known canonical suffix zh>" (no numeral)
        m3 = re.match(
            r"^(?P<stem>.*?)\s*[-–—]\s*(?:" + _suffix_zh_vals + r")\s*$",
            s, re.S)
        if m3 and m3.group("stem").strip():
            stem = m3.group("stem").strip()
    if stem is None:
        m2 = re.match(r"^(?P<stem>.*?)\s+(?P<n>\d{1,4}|[ivxlcdm]{1,5})\s*$",
                      s, re.S | re.I)
        stem = m2.group("stem").strip() if m2 else s
    if not stem:
        return None
    if re.search(r"[0-9]", stem):       # stray internal arabic digit
        return None
    return stem


def _series_stem(ordered):
    """Majority-vote the shared zh stem over recovered per-member stems.
    Returns (stem, members_with_stem) or (None, []) when no trustworthy stem."""
    stems = []
    for m in ordered:
        z = _zh_stem_recover(m.get("translated_name") or "")
        if z:
            stems.append(z)
    if not stems:
        return None, []
    from collections import Counter
    cnt = Counter(stems)
    best = cnt.most_common(1)[0]
    return best[0], stems


# --------------------------------------------------------------------------- #
# Loaders (fail-closed) ----------------------------------------------------- #
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
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        cols = rd.fieldnames or []
        need = {"identifier", "raw_display_name", "series_key",
                "title_sequence_token", "title_suffix"}
        if not need.issubset(cols):
            raise ValueError("context CSV missing columns %s" % sorted(need - set(cols)))
        return [dict(x) for x in rd]


def _ctx_index(ctx_rows):
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
def cleanup(rows, ctx_idx):
    """P34.2 deterministic suffix cleanup over P34.1-normalized mapping rows.

    Returns (out_rows, stats).  Only multi-member series whose members carry a
    recognised beat suffix (Climax/AfterSex/Creampie/Intro/End) AND whose zh
    stem is recoverable are candidates.  Handled series are rebuilt canonically
    so the suffix is independent and QA can compare on the stem.
    """
    if T is None:
        raise RuntimeError("ww_p34_suffix_table import failed: %s" % _T_ERR)

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
    stats = {"series": 0, "multi": 0, "suffix_series": 0, "rewritten": 0,
             "kept_single": 0, "kept_no_stem": 0, "kept_no_suffix": 0}
    changed = {}

    for sk, members in by_series.items():
        stats["series"] += 1
        if len(members) < 2:
            stats["kept_single"] += 1
            continue
        stats["multi"] += 1
        ordered = sorted(members, key=lambda r: idx_ord.get(r["identifier"], 0))

        # 1+2: re-parse every member's raw; confirm the series is a suffix
        # series (>=1 member ends in a recognised beat suffix); if NONE of the
        # members carries a recognised suffix this is a plain numbered series
        # P34.1 already handles -> leave untouched here.
        info = []
        any_suf = False
        for m in ordered:
            stem_raw, seq, sfix = T.parse_raw_member(
                m.get("raw_display_name") or "")
            # trust P33 suffix when raw parsed none but ctx declares one
            c = ctx_idx.get(m["identifier"]) or {}
            if not sfix and (c.get("suffix") or "").strip().lower() in T.SUFFIX_ZH:
                sfix = (c.get("suffix") or "").strip().lower()
            if sfix:
                any_suf = True
            info.append({"m": m, "stem_raw": stem_raw, "seq": seq,
                         "sfix": sfix})
        if not any_suf:
            stats["kept_no_suffix"] += 1
            continue                        # plain numbered series: P34.1 domain

        # 3: majority-translated zh stem.
        stem, _ = _series_stem(ordered)
        if not stem:
            stats["kept_no_stem"] += 1
            continue                        # cannot derive: leave untouched

        # rebuild every member: stem + its own seq + its own suffix zh.
        # Round-trip guard: every member's rebuilt zh must reduce back to `stem`
        # (skip the whole series otherwise -- no partial edit).
        rebuilt = []
        for it in info:
            zh = T.build_member(stem, it["seq"], it["sfix"])
            # suffix text in zh is canonical zh; recover stem again to verify
            if _zh_stem_recover(zh) != stem:
                rebuilt = None
                break
            rebuilt.append((it["m"], zh))
        if rebuilt is None:
            stats["kept_no_stem"] += 1
            continue
        stats["suffix_series"] += 1

        for m, new_zh in rebuilt:
            old_zh = (m.get("translated_name") or "")
            if new_zh and new_zh != old_zh:
                changed[m["identifier"]] = (old_zh, new_zh)
                stats["rewritten"] += 1

    if changed:
        for r in out:
            if r["identifier"] in changed:
                r["translated_name"] = changed[r["identifier"]][1]
    return out, stats, changed


# --------------------------------------------------------------------------- #
# Writer ------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
def write_mapping(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=MAP_COLS, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mapping_csv",
                    help="P34.1 normalized mapping CSV "
                         "(p34_translation_mapping_normalized.csv)")
    ap.add_argument("context_csv",
                    help="REAL P33 context CSV (p33_translation_context.csv)")
    ap.add_argument("--out",
                    default="output/p34/p34_translation_mapping_normalized2.csv")
    a = ap.parse_args(argv)

    if not os.path.isfile(a.mapping_csv):
        print("VERDICT=FAIL", file=sys.stderr)
        print("REASON=INPUT_MISSING:%s" % a.mapping_csv, file=sys.stderr)
        return 2
    if not os.path.isfile(a.context_csv):
        print("VERDICT=FAIL", file=sys.stderr)
        print("REASON=INPUT_MISSING:%s" % a.context_csv, file=sys.stderr)
        return 2
    if T is None:
        print("VERDICT=FAIL", file=sys.stderr)
        print("REASON=IMPORT:%s" % _T_ERR, file=sys.stderr)
        return 2
    try:
        rows = load_mapping(a.mapping_csv)
        ctx_rows = load_context(a.context_csv)
    except ValueError as e:
        print("VERDICT=FAIL", file=sys.stderr)
        print("REASON=READ:%s" % e, file=sys.stderr)
        return 2

    ctx_idx = _ctx_index(ctx_rows)
    out_rows, stats, changed = cleanup(rows, ctx_idx)

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    write_mapping(a.out, out_rows)

    print("VERDICT=GO")
    print("OUT=%s" % a.out)
    print("TOTAL_ROWS=%d" % len(rows))
    print("SERIES=%d  MULTI_MEMBER_SERIES=%d" % (stats["series"],
                                                 stats["multi"]))
    print("SUFFIX_SERIES=%d" % stats["suffix_series"])
    print("REWRITTEN=%d" % stats["rewritten"])
    print("ROWS_PRESERVED=%d" % (len(rows) - stats["rewritten"]))
    print("SKIP=single_member_series:%d no_stem:%d no_beat_suffix:%d"
          % (stats["kept_single"], stats["kept_no_stem"],
             stats["kept_no_suffix"]))
    if stats["rewritten"]:
        print("(wrote %d translated_name cells; identifier & raw unchanged)"
              % stats["rewritten"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
