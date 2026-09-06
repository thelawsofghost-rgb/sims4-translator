#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p34_gen_overrides.py --- ONE-SHOT, deterministic override generator for
series 'NOT Caught Cheating' (P34.3, operator-driven, no hand-editing).

WHY
---
The P34 LLM run left members of series S00317 'NOT Caught Cheating' with two
genuinely different Chinese stems (e.g. 未被捉奸 vs 没被发现), which INV5
(one zh stem per series_key) can never accept and which the deterministic
P34.1/2 passes cannot reconcile (no shared number/suffix to pivot on).

The operator does NOT want to hand-edit configs/p34_translation_overrides.csv.
Instead this generator:

  1. scans a P34 mapping CSV (output/p34/n2_clean.csv) for ANY row whose
     raw_display_name contains the token 'NOT Caught Cheating';
  2. for every such row writes ONE override row:
       identifier                reused VERBATIM from the source row,
       translated_name          deterministic canonical zh derived from the
                                row's OWN raw tail (see below),
       reason                   'series consistency',
       note                     'NOT Caught Cheating';
  3. NEVER modifies the source file; only *writes* the override CSV
     (configs/p34_translation_overrides.csv) as UTF-8 **with BOM**.

DETERMINISTIC translated_name (fixed head + per-row tail)
---------------------------------------------------------
The head 'NOT Caught Cheating' -> 未被捉奸 (operator-locked canonical stem).
Everything after the stem is rebuilt from the row's own tail so numbering,
suffix and the *CUSTOM VOICES* marker survive exactly:

    NOT Caught Cheating 1              -> 未被捉奸 1
    NOT Caught Cheating 8 - Climax     -> 未被捉奸 8 - 高潮
    NOT Caught Cheating 2 - AfterSex   -> 未被捉奸 2 - 后戏
    NOT Caught Cheating 3 *CUSTOM VOICES*        -> 未被捉奸 3 *自定义声音*
    NOT Caught Cheating 4 - Intro *CUSTOM VOICES* -> 未被捉奸 4 - 开场 *自定义声音*

So EVERY generated member shares the identical zh stem 未被捉奸 -> INV5 PASSes,
while each member keeps its own numeral / beat suffix / custom-voice flag.

Only translated_name is set by an override.  identifier and raw_display_name
in the FINAL mapping are guaranteed untouched because ww_p34_build_final.py
never writes those fields from the override row.

Exit codes: 0 = wrote overrides; 2 = bad input / no input file.
"""
from __future__ import print_function
import argparse
import csv
import os
import re
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
import ww_p34_suffix_table as ST      # noqa: E402   -- canonical suffix glossary

HEAD_RAW = "NOT CAUGHT CHEATING"              # matching is case-insensitive
HEAD_ZH = "未被捉奸"                            # operator-locked canonical stem

_MARKER = re.compile(r"\*\s*CUSTOM\s+VOICES\s*\*", re.IGNORECASE)
# optional arabic/roman numeral immediately after the stem
# tail allowed: [num] [ ' - ' suffix] [ *custom voices*]  (any order/omission)
# suffix clause is captured ONLY when followed by end-of-string or the marker,
# so a bare word that is not really a beat is not swallowed.
_NUM_RE = r"(?:\d{1,4}|[ivxlcdm]{1,6})"
_SUF_RE = r"(?:[-–—]\s*([A-Za-z]+))(?=\s*$|\s*\*\s*CUSTOM\s+VOICES\s*\*)"
_TAIL = re.compile(
    r"^\s*(?P<num>" + _NUM_RE + r")?\s*"
    + r"(?:" + _SUF_RE + r")?"
    + r"(?:\s*\*\s*CUSTOM\s+VOICES\s*\*)?\s*$",
    re.IGNORECASE | re.S)


def looks_like_target(raw):
    """True if raw_display_name contains the 'NOT Caught Cheating' marker."""
    return bool(raw and HEAD_RAW in (raw or "").upper())


def derive_zh(raw):
    """Deterministic canonical zh for one member raw.

    Returns HEAD_ZH + the member's own numeral / ' - <suffix zh>' /
    '*自定义声音*' tail.  If the tail cannot be parsed cleanly we FALL BACK to
    HEAD_ZH alone (safe: the stem is still unified) -- we never fabricate a
    numeral/suffix that is not in the source, and never mis-gloss a beat."""
    tail_raw = raw[len(HEAD_RAW):] if looks_like_target(raw) else ""
    custom = _MARKER.search(raw)
    m = _TAIL.match(tail_raw)
    num = ""
    sf = ""
    if m:
        num = (m.group("num") or "").upper()
        tok = m.group(2) or ""
        if tok:
            sf = ST.suffix_zh(tok)          # Climax->高潮, AfterSex->后戏, ...
            if sf == "" or sf == tok:         # unknown suffix left verbatim
                sf = ""                       # do not invent a beat (fail-closed)
    zh = HEAD_ZH
    if num:
        zh += " " + num
    if sf:
        zh += " - " + sf
    if custom:
        zh += " *自定义声音*"
    return zh


def gen_overrides(rows):
    """Scan mapping rows -> list of override dicts (target rows only)."""
    out = []
    for r in rows:
        raw = r.get("raw_display_name") or ""
        ident = r.get("identifier")
        if not ident or not looks_like_target(raw):
            continue
        out.append({
            "identifier": ident,
            "translated_name": derive_zh(raw),
            "reason": "series consistency",
            "note": HEAD_RAW.title(),
        })
    return out


def load_mapping(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        return [dict(x) for x in rd]


def write_overrides(path, rows):
    cols = ["identifier", "translated_name", "reason", "note"]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mapping_csv",
                    help="P34 mapping CSV to scan (e.g. output/p34/n2_clean.csv)")
    ap.add_argument("--out", default=os.path.normpath(os.path.join(
        _SCRIPT_DIR, "..", "configs", "p34_translation_overrides.csv")),
        help="override CSV output path (default configs/...overrides.csv)")
    a = ap.parse_args(argv)

    if not os.path.isfile(a.mapping_csv):
        print("VERDICT=FAIL", file=sys.stderr)
        print("REASON=INPUT_MISSING:%s" % a.mapping_csv, file=sys.stderr)
        return 2
    rows = load_mapping(a.mapping_csv)
    ov = gen_overrides(rows)
    if not ov:
        print("VERDICT=FAIL", file=sys.stderr)
        print("REASON=NO_TARGET_ROWS:'NOT Caught Cheating' not found in any "
              "raw_display_name" , file=sys.stderr)
        return 2
    out_dir = os.path.dirname(os.path.abspath(a.out))
    if out_dir:
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            print("VERDICT=FAIL", file=sys.stderr)
            print("REASON=MKDIR:%s" % e, file=sys.stderr)
            return 2
    write_overrides(a.out, ov)
    for o in ov:
        print("OVERRIDE %s: raw=%r -> %r"
              % (o["identifier"],
                 next((r.get("raw_display_name") for r in rows
                       if r.get("identifier") == o["identifier"]), "?"),
                 o["translated_name"]))
    print("VERDICT=GO")
    print("ROW_COUNT=%d OVERRIDES=%d OUT=%s"
          % (len(rows), len(ov), a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
