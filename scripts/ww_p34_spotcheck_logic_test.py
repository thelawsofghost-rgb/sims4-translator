#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p34_spotcheck_logic_test.py --- machine-behaviour logic test for
scripts/ww_p34_spotcheck.py.

ASSERTS MACHINE BEHAVIOUR ONLY.  The rows exercised here are SYNTHETIC
fixtures for the pure invariant evaluator; they are NOT real WickedWhims data
and are never claimed to be.  This test proves the spot-checker correctly FIRES
each invariant on a crafted violation and stays silent on a clean pass.

Checks
------
  1. py37 parse gate (adapter + this test).
  2. clean synthetic mapping  -> 0 failures, INV2 PASS, INV5 PASS.
  3. byte-for-byte divergence  -> INV2 fires.
  4. status=REVIEW present     -> INV3 fires (REVIEW must be empty).
  5. unknown status token      -> INV3 fires.
  6. empty translated_name     -> INV4 fires.
  7. doubled whitespace        -> INV7 fires.
  8. leading whitespace in zh  -> INV7 fires.
  9. control char in zh        -> INV7 fires.
 10. raw ends 'Climax' but zh lacks 高潮 -> INV6 fires.
 11. divergent series stems    -> INV5 fires.
 12. row count mismatch        -> INV1 fires.
 13. missing --series-csv      -> INV2/INV5 SKIPPED notes (no fabrication).
 14. load_mapping rejects mapping missing required columns (fail-closed).
 15. load_series_csv parses identifier+series_key+raw from real-shape CSV.
"""  # noqa: E501
from __future__ import print_function
import ast
import io
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import ww_p34_spotcheck as S            # noqa: E402

_PASS = []


def _c(rows, expect, series=None, src=None):
    """spotcheck wrapper: empty-series-safe, returns all results."""
    return S.spotcheck(rows, expect_rows=expect, series_map=series, src_map=src)


def _mk(ord_, ident, raw, zh, status="TRANSLATED"):
    return dict(ordinal=str(ord_), identifier=ident, raw_display_name=raw,
                translated_name=zh, status=status)


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)
    _PASS.append(msg)


def _clean(n=4):
    return [
        _mk(1, "a1", "Kiss", "接吻"),
        _mk(2, "a2", "Kiss 1", "接吻 1"),
        _mk(3, "a3", "Kiss 2 - Climax", "接吻 2 - 高潮"),
        _mk(4, "a4", "Deep Oral", "深入口交"),
    ][:n]


def test_py37():
    for f in ("ww_p34_spotcheck.py", "ww_p34_spotcheck_logic_test.py"):
        src = open(os.path.join(_HERE, f), encoding="utf-8").read()
        ast.parse(src, feature_version=(3, 7))
        _assert(True, "py37 parse PASS %s" % f)


def test_clean_pass():
    rows = _clean()
    src = {r["identifier"]: r["raw_display_name"] for r in rows}
    series = {"a1": "K1", "a2": "K1", "a3": "K1"}   # 3-member series
    fail, _, notes = _c(rows, len(rows), series=series, src=src)
    _assert(fail == [], "clean mapping -> 0 failures (got %r)" % fail)
    _assert(any("INV2 byte-for-byte: PASS" in x for x in notes),
            "clean -> INV2 PASS note")
    _assert(any("INV5 series stem: PASS" in x for x in notes),
            "clean -> INV5 PASS note (3-member series stem 接吻)")


def test_inv2_byte_divergence():
    rows = _clean(); bad = dict(rows[0]); bad["raw_display_name"] = "KissXXX"
    rows = [bad] + rows[1:]
    src = {r["identifier"]: r["raw_display_name"] for r in _clean()}
    fail, _, _ = _c(rows, len(rows), src=src)
    _assert(any("INV2 byte-for-byte divergence" in f for f in fail),
            "INV2 fires on raw divergence: %r" % fail)


def test_inv3_review_present():
    rows = _clean(); rows[0]["status"] = "REVIEW"
    fail, _, _ = _c(rows, len(rows))
    _assert(any("INV3 status=REVIEW present" in f for f in fail),
            "INV3 fires when status=REVIEW present")


def test_inv3_unknown_status():
    rows = _clean(); rows[0]["status"] = "BOGUS"
    fail, _, _ = _c(rows, len(rows))
    _assert(any("INV3 unknown status" in f for f in fail),
            "INV3 fires on unknown status token")


def test_inv4_empty():
    rows = _clean(); rows[0]["translated_name"] = ""
    fail, _, _ = _c(rows, len(rows))
    _assert(any("INV4" in f and "empty" in f for f in fail),
            "INV4 fires on empty translated_name")


def test_inv7_doubled_space():
    rows = _clean(); rows[0]["translated_name"] = "接吻  1"   # two spaces
    fail, _, _ = _c(rows, len(rows))
    _assert(any("doubled whitespace" in f for f in fail),
            "INV7 fires on doubled whitespace")


def test_inv7_leading_space():
    rows = _clean(); rows[0]["translated_name"] = " 接吻"
    fail, _, _ = _c(rows, len(rows))
    _assert(any("leading/trailing whitespace" in f for f in fail),
            "INV7 fires on leading whitespace")


def test_inv7_control_char():
    rows = _clean(); rows[0]["translated_name"] = "接\x07吻"
    fail, _, _ = _c(rows, len(rows))
    _assert(any("INV7 control char" in f for f in fail),
            "INV7 fires on control char")


def test_inv6_climax():
    rows = _clean(); rows[3]["raw_display_name"] = "Late — Climax"
    rows[3]["translated_name"] = "夜深"     # dropped the 高潮 suffix
    fail, _, _ = _c(rows, len(rows))
    _assert(any("INV6" in f for f in fail),
            "INV6 fires when raw ends Climax but zh lacks 高潮")


def test_inv5_divergent_stem():
    rows = _clean()
    rows[0]["translated_name"] = "亲吻"      # stray member translated differently
    src = {r["identifier"]: r["raw_display_name"] for r in rows}
    series = {"a1": "K1", "a2": "K1", "a3": "K1"}
    fail, _, _ = _c(rows, len(rows), series=series, src=src)
    _assert(any("INV5 series divergent" in f for f in fail),
            "INV5 fires on divergent zh stem within a series")


def test_inv1_count():
    rows = _clean(3)
    fail, _, _ = _c(rows, 4)
    _assert(any("INV1 row_count" in f for f in fail),
            "INV1 fires on row count != expect")


def test_skip_no_source():
    rows = _clean()
    fail, _, notes = _c(rows, len(rows))     # no series / no src
    _assert(fail == [], "no source -> no machine FAIL (nothing fabricated)")
    _assert(any("INV2 byte-for-byte: SKIPPED" in x for x in notes),
            "no source -> INV2 SKIPPED note")
    _assert(any("INV5 series stem: SKIPPED" in x for x in notes),
            "no source -> INV5 SKIPPED note")


def test_load_mapping_failclosed():
    bad_csv = "ordinal,translated_name\n1,x\n"   # missing identifier etc.
    p = tempfile.mktemp(suffix=".csv")
    with io.open(p, "w", encoding="utf-8") as fh:
        fh.write(bad_csv)
    try:
        try:
            S.load_mapping(p)
            _assert(False, "load_mapping should reject missing identity cols")
        except ValueError:
            _assert(True, "load_mapping fail-closed on missing identity cols")
    finally:
        os.remove(p)


def test_load_series_csv_shape():
    hdr = "identifier,raw_display_name,series_key\n"
    body = "b1,Kiss 1,K1\nb2,Nothing,,,\n"   # trailing empty series_key row
    p = tempfile.mktemp(suffix=".csv")
    with io.open(p, "w", encoding="utf-8") as fh:
        fh.write(hdr + body)
    try:
        sm, src = S.load_series_csv(p)
        _assert(sm == {"b1": "K1"}, "series map picks only keyed rows")
        _assert(src == {"b1": "Kiss 1", "b2": "Nothing"},
                "source map keeps every (identifier, raw)")
    finally:
        os.remove(p)


def main():
    test_py37()
    test_clean_pass()
    test_inv2_byte_divergence()
    test_inv3_review_present()
    test_inv3_unknown_status()
    test_inv4_empty()
    test_inv7_doubled_space()
    test_inv7_leading_space()
    test_inv7_control_char()
    test_inv6_climax()
    test_inv5_divergent_stem()
    test_inv1_count()
    test_skip_no_source()
    test_load_mapping_failclosed()
    test_load_series_csv_shape()
    print("CHECKS_PASSED=%d/%d" % (len(_PASS), len(_PASS)))
    print("=" * 60)


if __name__ == "__main__":
    main()
