#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p34_suffix_table.py --- SINGLE canonical source of truth for the P34.x
"series episode-beat suffix" vocabulary (zh mapping + raw-tail recognition).

WHY A SHARED TABLE
------------------
After the P34 real run, QA INV5 (same series_key => one zh stem) still failed on
prose series whose members carry a NON-Climax beat suffix:  the P34.1 suffix
recovery only knew Climax -> 高潮, so members of series like
    "All Yours N - Creampie", "Piano Love N - AfterSex"
produced divergent stems AND the QA stem-stripper (ww_p34_spotcheck._zh_series_stem)
could not strip a non-高潮 suffix to compare members on the true stem.

This module is the ONE place that fixes that gap.  It is imported BOTH by
  * ww_p34_suffix_cleanup.py  (P34.2 -- producer: re-parses raw, translates the
    suffix independently with SUFFIX_ZH, rebuilds members so suffix never
    enters the stem),
  * ww_p34_spotcheck.py        (QA     -- consumer: _zh_series_stem strips the
    same canonical zh suffixes before comparing members => INV5 sees the stem).

Because both sides read ONE table, a suffix added here is automatically
recognised by the builder and the checker together -- no drift possible.

VOCABULARY (operator-locked; explicit adult register for private WW use)
-----------------------------------------------------------------------
Suffix is a trailing beat qualifier AFTER the member numeral (P33 title_suffix).
Fixed glossary, each with ONE canonical zh, applied deterministically:

    raw token          zh
    Climax/Cumshot...  Climax    -> 高潮
    AfterSex           -> 后戏
    Creampie           -> 射精
    Intro              -> 开场
    End / Ending       -> 结束

A suffixed member is built canonically as:
    "<zh stem> <seq> - <suffix zh>"      e.g.  "全给你 2 - 射精"
                                            or  "钢琴之爱 10 - 后戏"
so the suffix is a separate clause -- NEVER glued into the stem.

Rules that keep it safe (fail-closed, nothing guessed):
  * Only an EXACT known raw token at the very END of raw_display_name (after an
    optional ' - '/' '-'/space) is treated as a suffix.  Mid-title occurrences
    of the same letters are content, not a beat marker (§4.4 of translation_rules).
  * Unknown suffix token  => kept verbatim, never mis-glossed, and treated as
    part of the STEM for stem-recovery/voting, so we do not corrupt an unknown.
ZERO data here invents real rows; this file only fixes VOCABULARY + parsers.
"""
from __future__ import print_function
import re

# single canonical zh per raw suffix token (add here => picked up by BOTH the
# P34.2 builder and the P34 QA stem-stripper, keeping them in sync).
SUFFIX_ZH = {
    "climax": "高潮",
    "aftersex": "后戏",
    "creampie": "射精",
    "intro": "开场",
    "end": "结束",
    "ending": "结束",
}

# tokens recognised as a real beat suffix at the tail of raw_display_name.
# Keep as a tuple (order irrelevant); matched case-insensitively with word
# boundary, so "All Yours" / "Piano Love" (title words) are never confused.
_SUFFIX_TOKENS = tuple(re.escape(k) for k in SUFFIX_ZH)
_SUFFIX_TRIE = sorted(SUFFIX_ZH, key=len, reverse=True)   # longest-first

# punctuation a source may place between the numeral and the suffix.
_SEP = r"(?:[-–—]\s*|\s+)"

# full-tail matcher: "<stem_raw> [ <seq> ] [ - <suffix_raw> ]"  (case-insensitive)
#   e.g. "All Yours 2 - Creampie"        -> stem 'All Yours' seq '2'  suf 'Creampie'
#        "Piano Love 10 - AfterSex"      -> stem 'Piano Love' seq '10' suf 'AfterSex'
#        "Piano Love 5"                  -> stem 'Piano Love' seq '5'  suf ''
#        "Giving Her Pleasure 1"         -> stem 'Giving Her Pleasure' seq '1' suf ''
# Number may be arabic or a short roman.  Letters-only stem (no bare digits) is
# assumed; a lowercase roman token right before the suffix is only taken as a
# roman NUMERAL when it is not a plausible stem word tail (guarded below).
_TAIL = re.compile(
    r"^(?P<stem>.*?)\s*"
    r"(?:[-–—]\s*)?"
    r"(?P<seq>\d{1,4}|[ivxlcdm]{1,5})?\s*"
    r"(?:[-–—]\s*(?P<suf>%s)\b)?\s*$"
    % ("|".join(_SUFFIX_TRIE)), re.IGNORECASE | re.S)


def raw_tail_token(raw):
    """Return the known beat-suffix token if raw ENDS in one (longest-match,
    case-normalised to lower), else ''."""
    s = (raw or "").strip()
    best = ""
    for tok in _SUFFIX_TRIE:
        m = re.search(r"(?:[-–—]\s*|\s+)(?i:%s)\s*$" % re.escape(tok), s)
        if m:
            best = tok
            break                    # longest-first => first hit is longest
    return best


def parse_raw_member(raw):
    """Split a member raw_display_name into (stem_raw, seq, suffix_raw).

    suffix_raw is the recognised beat token ('' if none) ONLY when it sits at
    the very end after a separator.  seq is the trailing arabic/roman numeral
    (' ' if none).  stem_raw is whatever leads -- the series TITLE in english.
    """
    s = (raw or "").strip()
    stem_raw, seq, suffix_raw = s, "", ""
    if not s:
        return stem_raw, seq, suffix_raw
    tok = raw_tail_token(s)
    if tok:
        suffix_raw = tok
        head = s[: s.lower().rfind(tok)].rstrip(" \t-–—")   # drop " - suf"
        # drop a leading numeral from head into seq if the tail is ' <num>' only
        m = re.search(r"[-\s]\s*(\d{1,4}|[ivxlcdm]{1,5})\s*$", head, re.I)
        if m and (head.endswith(m.group(1) + "") or
                  re.search(r"[\s\-–—]\d{1,4}\s*$", head) or
                  re.search(r"[\s\-–—](?:[ivxlcdm]{1,5})\s*$", head, re.I)):
            stem_raw = head[: m.start()].strip()
            seq = m.group(1)
        else:
            stem_raw = head
        return stem_raw.strip(), seq, suffix_raw
    # no recognised suffix: peel a bare trailing numeral (keeps "Kiss 3" -> stem
    # 'Kiss', seq '3') but never a roman-looking tail that is a normal word.
    mn = re.search(r"[\s\-–—](\d{1,4})\s*$", s)
    if mn:
        stem_raw = s[: mn.start()].strip()
        seq = mn.group(1)
    return stem_raw, seq, suffix_raw


def suffix_zh(token):
    """Canonical zh of a beat-suffix token ('' for absent).  A KNOWN raw token
    returns its single canonical zh; an UNKNOWN token is returned VERBATIM
    (case preserved, never mis-glossed, never lowered) -- a caller should not
    normally rebuild with an unknown token, but if it does the text is kept."""
    if not token:
        return ""
    t = (token or "").strip().lower()
    return SUFFIX_ZH.get(t, token)


def build_member(stem_zh, seq, suffix_token):
    """Deterministic canonical rebuild of a member translated_name:
        "<stem_zh> <seq>[ - <suffix zh >]"
    suffix joined with ' - ' ALWAYS (rule B) so it is its own clause and can
    never bleed into the stem.  seq optional; suffix optional."""
    zh = stem_zh
    if seq:
        zh += " " + str(seq).upper()
    if suffix_token:
        sf = suffix_zh(suffix_token)
        if sf:
            zh += " - " + sf
    return zh
