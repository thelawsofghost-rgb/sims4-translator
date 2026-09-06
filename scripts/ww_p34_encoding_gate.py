#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p34_encoding_gate.py --- P34 encoding gate (deterministic, fail-closed).

P34.3 requirement #1: EVERY P34 output CSV must be UTF-8 **with BOM** and must
carry NO mojibake / no replacement characters / no GBK-mangled CJK.  Windows
Excel and many editors guess GBK for a BOM-less UTF-8 file and render Chinese
as garbage (榻胯疆 / 楂樻疆 / 娌℃湁 ...).  This module is the single authority
for "is a P34 text encoding-safe?" and is used BOTH as a standalone CLI gate
AND as invariant #9 (ENCODING) inside ww_p34_spotcheck.py.

What it checks against a file (or a raw byte string):
  * BOM   -> file must open with the UTF-8 BOM bytes EF BB BF.
  * clean -> bytes must decode as UTF-8 with NO errors and NO U+FFFD (so no
             lossy decode ever happened / will happen).
  * no GBK-mangle -> the decoded text must not contain the garble that results
             from reading real Chinese UTF-8 bytes as GBK/CP936 (e.g. 没有
             -> 娌℃湁 ).  The mangled forms are DERIVED at runtime from common
             Han words (encode UTF-8 then decode as GBK), so there are no
             hand-picked junk literals to go stale.  We do NOT try to "fix"
             content -- we REJECT it so the operator regenerates/re-encodes
             rather than ship corruption.

Deterministic and side-effect free where possible; the CLI's --repair mode is
the ONLY path that writes, and it writes a NEW file (never overwrites input)
converting to a clean UTF-8-with-BOM encoding by strict re-decode.

Exit codes (CLI):
  0  ENCODING PASS (BOM + clean + no mangle)            [or repairs produced OK]
  2  ENCODING FAIL / input gate failure
  --repair writes <out> only when the input cleanly re-encodes; if the input is
     genuinely corrupt (undecodable bytes / replacement chars already burned in)
     it does NOT guess a re-encode and exits 2 (fail-closed: never fabricate).
"""
from __future__ import print_function
import argparse
import codecs
import os
import sys

# --------------------------------------------------------------------------- #
# BOM / decode helpers ------------------------------------------------------- #
# --------------------------------------------------------------------------- #
BOM = codecs.BOM_UTF8                 # b'\xef\xbb\xbf'


def has_bom(raw):
    return isinstance(raw, bytes) and raw.startswith(BOM)


def decode_strict(raw):
    """Strictly decode bytes as UTF-8 -> (text, None); on failure
    (text=None, err:str)."""
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError as e:
        return None, "undecodable UTF-8: %s" % e


def _replacement_count(text):
    return text.count("\ufffd") if text else 0


# GBK-mangle probe detector ------------------------------------------------ #
# Reading UTF-8 bytes as GBK/CP936 turns real Chinese words into gibberish that
# still happens to be *valid* UTF-8 (so strict-decode alone can't catch it):
#   没有(UTF-8) read as GBK -> 娌℃湁 ;  無/温/然 -> 鎽/娓/鐒...
# Instead of hardcoding those artefacts we DERIVE the expected garbling from a
# small set of very common Han words and look for the exact produced runs.
# Deterministic, no junk literals, and a natural Chinese phrase essentially
# never contains such an artefact.
_PROBE_WORDS = (u"没有", u"已经", u"现在", u"什么", u"我们", u"你们",
                u"真的", u"怎么", u"可以", u"知道", u"时候", u"不要",
                u"感觉", u"一直", u"一下", u"有点", u"这里", u"那里",
                u"朋友", u"身体", u"因为", u"所以", u"如果", u"这个")


def _gbk_mangle_probes():
    """Map each probe Han word -> the text pandas/Excel would show if its
    original UTF-8 bytes were mis-read as GBK.  Only pairs that happen to be
    valid GBK are produced (Python .decode('gbk') is strict); the rest are
    skipped silently.  Returns a list of mangled substrings to treat as
    corruption evidence."""
    probes = []
    for w in _PROBE_WORDS:
        b = w.encode("utf-8")
        try:
            probes.append(b.decode("gbk"))
        except UnicodeDecodeError:
            continue                     # that pair not a valid GBK seq
    return probes

_GBK_MANGLE = _gbk_mangle_probes()


def _is_mojibake(text):
    """True when decoded text contains any derived GBK-mangle artefact of a real
    common Han word (strong, low-false-positive signal that UTF-8 was read as
    GBK somewhere in the pipeline)."""
    if not text:
        return False
    return any(m and m in text for m in _GBK_MANGLE)


def audit_text(text):
    """Audit a decoded UTF-8 string -> dict(status, problems[list]).
    status: 'PASS' | 'FAIL'."""
    problems = []
    if text is None:
        problems.append("text is None")
    if _replacement_count(text):
        problems.append("contains %d U+FFFD replacement char(s) (lossy/pre-"
                        "corrupted)" % _replacement_count(text))
    if _is_mojibake(text):
        problems.append("GBK-mojibake pattern detected (likely UTF-8 bytes read "
                        "as GBK)")
    return ("PASS" if not problems else "FAIL", problems)


def audit_file(path):
    """Audit a CSV file's raw encoding -> (status, problems, decode_meta).
    decode_meta = dict(raw_len, bom(bool), had_replacement(bool),
                       first_cell_sample[str|None]).
    Does NOT parse CSV rows -- encoding is a whole-file property here."""
    with open(path, "rb") as fh:
        raw = fh.read()
    bom = has_bom(raw)
    body = raw[len(BOM):] if bom else raw
    text, err = decode_strict(body)
    problems = []
    if not text:
        problems.append(err or "cannot decode")
    prob_garb = []
    if text is not None:
        prob_garb = _garbled_cells(text)
    if not bom:
        problems.append("missing UTF-8 BOM (file starts %r; P34 outputs must "
                        "start with EF BB BF)" % raw[:8])
    nrepl = _replacement_count(text) if text is not None else 0
    if nrepl:
        problems.append("contains %d U+FFFD replacement char(s) (lossy/"
                        "pre-corrupted)" % nrepl)
    if text is None:
        return ("FAIL", problems, dict(raw_len=len(raw), bom=bom,
                                        had_replacement=nrepl > 0))
    if prob_garb:
        problems.append("GBK-mojibake artefact(s) present (%d): %s"
                        % (len(prob_garb), prob_garb[:5]))
    status = "PASS" if not problems else "FAIL"
    return (status, problems, dict(
        raw_len=len(raw), bom=bom,
        had_replacement=nrepl > 0,
        first_cell_sample=prob_garb[:1] or None))


def _garbled_cells(text):
    """Report up to 8 distinct GBK-mangle artefacts actually present in the
    decoded text (matched probe + short surroundings) for human inspection."""
    bad, seen = [], set()
    for m in _GBK_MANGLE:
        if not m or m in seen or m not in text:
            continue
        seen.add(m)
        i = text.find(m)
        ctx = text[max(0, i - 12): i + len(m) + 12].replace("\n", " ")
        bad.append(u"%r (in %r)" % (m, ctx))
        if len(bad) >= 8:
            break
    return bad


def repair_file(path, out_path):
    """Strictly produce a clean UTF-8-with-BOM copy at out_path (never touches
    input).  Returns (ok:bool, reason:str|None).
       - reads bytes, requires BOM-less-or-with decode to succeed with ZERO
         replacement chars; if the file has genuine binary/undecodable bytes it
         returns False (no guessing).
       - when input ALREADY has BOM + decodes clean, repair is a no-op copy but
         still re-emits with one canonical BOM (safe idempotent).
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    bom = has_bom(raw)
    body = raw[len(BOM):] if bom else raw
    text, err = decode_strict(body)
    if text is None or _replacement_count(text) > 0 or _is_mojibake(text):
        return False, "cannot safely repair: still corrupt after strict UTF-8 " \
                      "decode (undecodable/replacement/mojibake)"
    with open(out_path, "wb") as fh:
        fh.write(BOM + body)          # canonical BOM; existing content preserved
    return True, None


# --------------------------------------------------------------------------- #
# CLI ----------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="P34 encoding gate: assert a P34 CSV is UTF-8-with-BOM and "
                    "free of GBK mojibake.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="P34 output CSV to audit (or --repair to copy "
                                 "as clean BOM file)")
    ap.add_argument("--repair", metavar="OUT",
                    help="instead of only auditing, write a clean UTF-8-with-BOM "
                         "copy to OUT (never modifies the input)")
    a = ap.parse_args(argv)

    if not os.path.isfile(a.path):
        print("ENCODING=FAIL", file=sys.stderr)
        print("REASON=INPUT_MISSING:%s" % a.path, file=sys.stderr)
        return 2
    if a.repair:
        ok, reason = repair_file(a.path, a.repair)
        if not ok:
            print("ENCODING=FAIL", file=sys.stderr)
            print("REASON=%s" % reason, file=sys.stderr)
            return 2
        print("ENCODING=GO")
        print("REPAIRED_TO=%s" % a.repair)
        return 0
    status, problems, meta = audit_file(a.path)
    print("ENCODING=%s" % ("GO" if status == "PASS" else "FAIL"))
    for p in problems:
        print("  - " + p)
    print("BOM=%s RAW_BYTES=%d" % ("yes" if meta.get("bom") else "no",
                                   meta.get("raw_len", 0)))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
