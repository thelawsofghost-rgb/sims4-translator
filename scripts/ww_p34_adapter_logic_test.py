#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p34_adapter_logic_test.py --- OFF-LINE logic checks for the P34 adapter.

Scope: deterministic machinery ONLY.  It does NOT contact Ollama and does NOT
perform a real translation run.  `run_pipeline(..., dry_run=True)` executes the
deterministic Stage-1 resolver over the SYNTHETIC fixture (ww_p34_adapter_fixture)
and asserts:
  1) identifier & raw_display_name are preserved byte-for-byte in every mapping
     row (no mutation), for every status.
  2) deterministic closes: keep-word/author stays verbatim; single gloss words
     translate; modifier+head composites assemble; a size-4 glossary-stem series
     translates its stem once and decorates each member (numeral + Climax).
  3) fail-closed: non-glossary prose stems (series or standalone) are NOT
     guessed -> status=REVIEW with translated_name empty in dry-run.
  4) series consistency: all members of the glossary-stem series share the same
     leading zh stem; only numeral/suffix differ.
  5) CLI --dry-run writes the three output files and prints compact stats whose
     counts match the in-process run_pipeline result.

The fixture rows are FABRICATED (never real WW data); these checks validate the
adapter's MACHINE behaviour only.  Exit 0 = all PASS; 1 = any FAIL.
"""
from __future__ import print_function
import ast
import contextlib
import csv
import io
import os
import sys
import tempfile

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "..", "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import p34_translate_adapter as M          # noqa: E402
import ww_p34_adapter_fixture as FX          # noqa: E402

_CHECKS = []
_PASS = [0]


def check(name, cond, detail=""):
    _CHECKS.append(name)
    if cond:
        _PASS[0] += 1
        print("PASS  %s" % name)
    else:
        print("FAIL  %s  %s" % (name, (" :: " + detail) if detail else ""))


def key(row):
    return (row.get("source_instance", ""), row.get("identifier", ""),
            row.get("raw_display_name", ""))


def main(argv):
    # 0) source file must be cpython-3.7 parseable (py37 gate).
    src = open(os.path.join(_SCRIPTS, "p34_translate_adapter.py"), encoding="utf-8").read()
    try:
        ast.parse(src, feature_version=(3, 7))
        check("py37_gate parse", True)
    except SyntaxError as e:
        check("py37_gate parse", False, repr(e))

    rows = FX.make_rows()
    n = len(rows)
    check("fixture row count sane", n >= 12, "n=%d" % n)

    # ---- run the deterministic-only pipeline (dry run, never Ollama) ---------
    out_dir = tempfile.mkdtemp(prefix="p34_adapter_test_")
    with contextlib.redirect_stdout(io.StringIO()):
        mapping = M.run_pipeline(rows, out_dir, dry_run=True)
    check("dry-run returns one mapping row per input", len(mapping) == n,
          "map=%d rows=%d" % (len(mapping), n))

    by = {}
    for m in mapping:
        by[(m["source_instance"], m["identifier"])] = m

    # ---- 1) identifier & raw preserved byte-for-byte -------------------------
    raw_preserved = all(
        (m["identifier"] == r.get("identifier", "")
         and m["raw_display_name"] == r.get("raw_display_name", ""))
        for m, r in zip(mapping, rows))
    check("identifier+raw preserved byte-for-byte every row", raw_preserved)

    # ---- 2) deterministic closes ---------------------------------------------
    def t(ident, expect_zh, expect_status):
        m = by.get(("0xAA", ident))
        ok = m is not None and m["translated_name"] == expect_zh \
            and m["status"] == expect_status
        check("dtrm %s -> %r [%s]" % (ident, expect_zh, expect_status), ok,
              "got %r [%s]" % (m["translated_name"] if m else None,
                               m["status"] if m else None))

    t("keep_author", "Nevely42", "KEEP")                       # author verbatim
    t("single_kiss", "接吻", "TRANSLATED")                       # single gloss
    t("single_orals", "口交", "TRANSLATED")                      # blowjob->口交
    t("single_shower", "淋浴", "TRANSLATED")                     # location gloss
    t("comp_deeporal", "深入口交", "TRANSLATED")                  # Deep Oral
    t("comp_wetkiss", "湿接吻", "TRANSLATED")                     # Wet Kiss
    t("seq1", "高潮", "TRANSLATED")                               # single "Orgasm" -> 高潮

    # ---- 3) series size-4 glossary-stem: stem once, members decorated ---------
    k1 = by[("0xAA", "ser_k_1")]; k4 = by[("0xAA", "ser_k_4")]
    check("series member 1 -> 接吻 1", k1["translated_name"] == "接吻 1",
          "got %r" % k1["translated_name"])
    check("series member 4 -> 接吻 4 - 高潮", k4["translated_name"] == "接吻 4 - 高潮",
          "got %r" % k4["translated_name"])

    # series consistency: every SER_KISS member shares the SAME stem prefix 接吻
    stems = set()
    for i in (1, 2, 3, 4):
        m = by[("0xAA", "ser_k_%d" % i)]
        stems.add(m["translated_name"].split(" ")[0])
    check("series consistency: one shared stem 接吻 across all 4 members",
          stems == {"接吻"}, "stems=%s" % stems)

    # ---- 4) fail-closed on non-glossary prose -> REVIEW, translated empty -----
    for ident, why in (("ser_prose1", "CC"), ("ser_prose2", "CC"),
                       ("prose1", "neighbors")):
        m = by[("0xAA", ident)]
        check("fail-closed %s REVIEW-empty (no guess)" % ident,
              m["status"] == "REVIEW" and m["translated_name"] == "",
              "status=%s zh=%r" % (m["status"], m["translated_name"]))
        check("fail-closed %s has REVIEW why-note" % ident, bool(m["note"]))

    # ---- 5) CLI --dry-run writes the three files + consistent stats ----------
    import subprocess
    fix_csv = os.path.join(tempfile.mkdtemp(prefix="p34_fix_"), "p33.csv")
    with open(fix_csv, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=FX.COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    cli_out = tempfile.mkdtemp(prefix="p34_cli_")
    py = sys.executable
    sp = subprocess.run(
        [py, os.path.join(_SCRIPTS, "p34_translate_adapter.py"),
         fix_csv, "--out", cli_out, "--dry-run"],
        capture_output=True, text=True)
    check("CLI dry-run exit 0", sp.returncode == 0, sp.stdout + sp.stderr)
    check("CLI dry-run mapping file written",
          os.path.isfile(os.path.join(cli_out, "p34_translation_mapping.csv")))
    check("CLI dry-run review file written",
          os.path.isfile(os.path.join(cli_out, "p34_translation_review.txt")))
    check("CLI dry-run stats file written",
          os.path.isfile(os.path.join(cli_out, "p34_translation_stats.txt")))
    # stats counts grid: TOTAL == fixture rows, REVIEW == number routed out
    stats = {}
    with open(os.path.join(cli_out, "p34_translation_stats.txt"), encoding="utf-8") as fh:
        for line in fh:
            if "=" in line:
                k_, v_ = line.strip().split("=", 1)
                stats[k_] = int(v_)
    check("stats TOTAL_ROWS equals fixture n", stats.get("TOTAL_ROWS") == n,
          "stats=%s n=%d" % (stats.get("TOTAL_ROWS"), n))
    expected_review = sum(1 for m in mapping if m["status"] == "REVIEW")
    check("stats REVIEW_COUNT equals in-process REVIEW rows",
          stats.get("REVIEW_COUNT") == expected_review,
          "stats=%s rows=%s" % (stats.get("REVIEW_COUNT"), expected_review))

    # ---- summary ------------------------------------------------------------
    print("=" * 60)
    print("CHECKS_PASSED=%d/%d" % (_PASS[0], len(_CHECKS)))
    failed = len(_CHECKS) - _PASS[0]
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
