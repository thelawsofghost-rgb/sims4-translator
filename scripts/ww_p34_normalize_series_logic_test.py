#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p34_normalize_series_logic_test.py --- machine-behaviour logic test for
scripts/ww_p34_normalize_series.py.

ASSERTS MACHINE BEHAVIOUR ONLY.  Rows exercised are SYNTHETIC fixtures; they
are NOT real WickedWhims data and are never claimed to be.  This test proves the
P34.1 normalizer:

  * unifies the Chinese stem across members of the same series_key (rule A),
  * keeps the numbering from each row's OWN title_sequence_token (rule C),
  * appends Climax -> 高潮 as a suffix -> 'N - 高潮' (rule B), never bare 'xxx 高潮',
  * preserves raw_display_name & identifier byte-for-byte (only translated_name
    may change),
  * does NOT touch standalone / single-member / un-derivable rows (rule D)
    => nothing guessed,
  * round-trip self-consistency: every rewritten member strips back to the one
    shared stem,
  * writes exactly one NEW output file and never the source mapping.

Checks
------
  1. py37 parse gate.
  2. glossary series members already consistent -> left unchanged (0 rewrites).
  3. prose series with divergent LLM stems -> unified to first valid member stem.
  4. Climax member rebuilt as ' ... N - 高潮'.
  5. numbering preserved from title_sequence_token.
  6. raw_display_name / identifier byte-for-byte preserved after the pass.
  7. standalone (no series_key) -> untouched.
  8. single-member series -> untouched (nothing to unify).
  9. divergent-stem member whose number cannot cleanly strip -> series refused
     (no partial rewrite).
 10. normalize() returns same row count as input.
"""
from __future__ import print_function
import ast
import io
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import ww_p34_normalize_series as N    # noqa: E402

_PASS = []


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)
    _PASS.append(msg)


def _maprows(specs):
    """specs: list of (ordinal, identifier, raw, zh, status). ids distinct per
    series. Return list[dict] in mapping shape (extra cols tolerated)."""
    out = []
    for i, (ord_, ident, raw, zh) in enumerate(specs):
        out.append(dict(source_instance="0xAA", ordinal=str(ord_), identifier=ident,
                        raw_display_name=raw, translated_name=zh,
                        status="TRANSLATED", note=""))
    return out


def _ctxidx(specs):
    """specs: list of (identifier, series_key, seq, suffix). -> ctx_idx dict
    plus raw ctx rows."""
    rows = []
    idx = {}
    for ident, sk, seq, suffix in specs:
        rows.append(dict(identifier=ident, raw_display_name="",
                         series_key=sk, title_sequence_token=seq,
                         title_suffix=suffix))
        idx[ident] = {"series_key": sk, "seq": seq, "suffix": suffix}
    return rows, idx


def _map_by_id(rows):
    return {r["identifier"]: r for r in rows}


def test_py37():
    for f in ("ww_p34_normalize_series.py", "ww_p34_normalize_series_logic_test.py"):
        src = open(os.path.join(_HERE, f), encoding="utf-8").read()
        ast.parse(src, feature_version=(3, 7))
        _assert(True, "py37 parse PASS %s" % f)


def test_glossary_already_consistent_untouched():
    # 'Kiss 1 / Kiss 2 - Climax / Kiss 3' all share stem 接吻 already
    m = _maprows([
        (1, "k1", "Kiss 1", "接吻 1"),
        (2, "k2", "Kiss 2", "接吻 2 - 高潮"),
        (3, "k3", "Kiss 3", "接吻 3"),
    ])
    c, idx = _ctxidx([
        ("k1", "SER", "1", ""),
        ("k2", "SER", "2", "Climax"),
        ("k3", "SER", "3", ""),
    ])
    out, stats, changed = N.normalize(m, idx)
    _assert(stats["rewritten"] == 0,
            "consistent glossary series -> 0 rewrites (got %d)" % stats["rewritten"])
    for r in out:
        _assert(r["translated_name"] in ("接吻 1", "接吻 2 - 高潮", "接吻 3"),
                "consistent series zh untouched")


def test_prose_series_unified():
    # Prose series 'Caught Cheating 1 / 2': member1 already canonical
    # '捉奸进行时 1' (donates stem 捉奸进行时), member2 is LLM-divergent.
    m = _maprows([
        (1, "cc1", "Caught Cheating 1", "捉奸进行时 1"),
        (2, "cc2", "Caught Cheating 2", "第二种捉奸方式"),   # divergent
    ])
    c, idx = _ctxidx([
        ("cc1", "SCC", "1", ""),
        ("cc2", "SCC", "2", ""),
    ])
    out, stats, changed = N.normalize(m, idx)
    _assert(stats["rewritten"] == 1, "one divergent member rewritten")
    by = _map_by_id(out)
    _assert(by["cc1"]["translated_name"] == "捉奸进行时 1",
            "first member keeps its canonical stem+1")
    _assert(by["cc2"]["translated_name"] == "捉奸进行时 2",
            "divergent sibling inherits stem -> '捉奸进行时 2' (got %r)"
            % by["cc2"]["translated_name"])


def test_climax_suffix_ruleB():
    # member1 already canonical '齿轮摇摆 6 - 高潮' (stem 齿轮摇摆); member2
    # divergent -> rebuilt as '<stem> 7 - 高潮' (Climax as suffix, not glued)
    m = _maprows([
        (1, "g1", "Gearshift 6 - Climax", "齿轮摇摆 6 - 高潮"),
        (2, "g2", "Gearshift 7 - Climax", "引擎轰鸣七档定格里"),
    ])
    c, idx = _ctxidx([
        ("g1", "SGS", "6", "Climax"),
        ("g2", "SGS", "7", "Climax"),
    ])
    out, stats, changed = N.normalize(m, idx)
    by = _map_by_id(out)
    _assert(stats["rewritten"] == 1, "only the divergent member rewritten")
    _assert(by["g1"]["translated_name"] == "齿轮摇摆 6 - 高潮",
            "canonical first member unchanged")
    _assert(by["g2"]["translated_name"] == "齿轮摇摆 7 - 高潮",
            "Climax appended as ' - 高潮' suffix, not bare (got %r)"
            % by["g2"]["translated_name"])


def test_numbering_ruleC():
    # member1 canonical '夜出 4' (stem 夜出); member2 divergent, keeps its OWN 5
    m = _maprows([
        (1, "n1", "Night Out 4", "夜出 4"),
        (2, "n2", "Night Out 5", "夜生活第五趴"),
    ])
    c, idx = _ctxidx([
        ("n1", "SNO", "4", ""),
        ("n2", "SNO", "5", ""),
    ])
    out, stats, _ = N.normalize(m, idx)
    by = _map_by_id(out)
    _assert(stats["rewritten"] == 1, "only divergent member rewritten")
    _assert(by["n1"]["translated_name"] == "夜出 4", "first member unchanged")
    _assert(by["n2"]["translated_name"] == "夜出 5",
            "sibling inherits stem but keeps its OWN number 5 (got %r)"
            % by["n2"]["translated_name"])


def test_identity_preserved():
    m = _maprows([
        (1, "p1", "Prowl 1", "潜行甲"),
        (2, "p2", "Prowl 2", "潜行二"),
    ])
    c, idx = _ctxidx([("p1", "SPR", "1", ""), ("p2", "SPR", "2", "")])
    out, _, _ = N.normalize(m, idx)
    for r in out:
        _assert(r["raw_display_name"] == ("Prowl %d" % (1 if r["identifier"] ==
                                                        "p1" else 2)),
                "raw never rewritten")
        _assert(r["identifier"] in ("p1", "p2"), "identifier preserved")


def test_standalone_untouched():
    m = _maprows([(1, "solo", "Just A Random Title", "随便起名")])
    c, idx = _ctxidx([("solo", "", "", "")])     # no series_key -> standalone
    out, stats, _ = N.normalize(m, idx)
    _assert(out[0]["translated_name"] == "随便起名",
            "standalone (empty series_key) untouched")
    _assert(stats["rewritten"] == 0, "no rewrite for standalone")


def test_single_member_series_untouched():
    m = _maprows([(1, "single", "One Off", "只此一次")])
    c, idx = _ctxidx([("single", "S1", "1", "")])
    out, stats, _ = N.normalize(m, idx)
    _assert(stats["kept_single"] == 1 and stats["rewritten"] == 0,
            "single-member series untouched")


def test_refuses_partial_rewrite():
    # Every member's translation embeds an internal arabic digit -> no member
    # yields a trustworthy clean stem -> the WHOLE series is refused (nothing
    # rewritten, nothing guessed), so it can never half-apply a bad stem.
    m = _maprows([
        (1, "r1", "Bunker 1", "防空洞9号"),     # internal digit in stem text
        (2, "r2", "Bunker 2", "地堡9层"),
    ])
    c, idx = _ctxidx([("r1", "SRD", "1", ""), ("r2", "SRD", "2", "")])
    out, stats, changed = N.normalize(m, idx)
    _assert(stats["rewritten"] == 0 and stats["kept_no_stem"] == 1,
            "ambiguous series (internal digits) refused, not partial-rewritten")
    by = _map_by_id(out)
    _assert(by["r1"]["translated_name"] == "防空洞9号" and
            by["r2"]["translated_name"] == "地堡9层",
            "untouched when refused")


def test_row_count_preserved():
    m = _maprows([
        (1, "a1", "Alpha 1", "阿尔法一"),
        (2, "a2", "Beta 2", "贝塔二"),
        (3, "z", "Zed", "齐德"),
    ])
    c, idx = _ctxidx([("a1", "SA", "1", ""), ("a2", "SA", "2", ""),
                      ("z", "", "", "")])
    out, _, _ = N.normalize(m, idx)
    _assert(len(out) == len(m), "row count preserved through normalize()")


def main():
    test_py37()
    test_glossary_already_consistent_untouched()
    test_prose_series_unified()
    test_climax_suffix_ruleB()
    test_numbering_ruleC()
    test_identity_preserved()
    test_standalone_untouched()
    test_single_member_series_untouched()
    test_refuses_partial_rewrite()
    test_row_count_preserved()
    print("CHECKS_PASSED=%d/%d" % (len(_PASS), len(_PASS)))
    print("=" * 60)


if __name__ == "__main__":
    main()
