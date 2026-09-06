#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Logic test for ww_p35_xml_alignment.py (Linux-run, no real mod inputs).

Drives the READ ONLY alignment tool against a synthetic WW_ANIM_XML package
+ a P34-style final CSV, covering GO and negative branches:
  - PERFECT: CSV matches XML exactly -> ALIGN-GO, matched=raw_match=N
  - REORDER: CSV rows out of ordinal order -> still join by ordinal -> GO
  - MISSING: a CSV ordinal not present in XML -> MISSING>0 -> ALIGN-FAIL
  - MISMATCH: CSV raw differs from XML raw at an ordinal -> MISMATCH>0 -> FAIL
  - BADPAYLOAD: translated_name empty -> EMPTY_TRANSLATED_NAME -> FAIL

No STBL, no package-to-Mods, no saves.  Writes only under a tmp dir.
"""
import csv
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import ww_p35_fixture as FX  # noqa: E402

SUGGEST_REAL = ("Real inputs are Windows-only (output\\p32\\p33\\p34 + the WW "
                "package). On this Linux box only synthetic fixtures run.")

PASS, FAIL = "PASS", "FAIL"
results = []


def _run(module_name):
    spec = importlib.util.spec_from_file_location(
        module_name, SCRIPT_DIR / module_name.replace(".", "/") + ".py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print("%-4s %-22s %s" % ("PASS" if cond else "FAIL", name, detail))


def _write_csv(path, rows, cols):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _mk(tmp, rows, cols):
    p = tmp / "final.csv"
    _write_csv(p, rows, cols)
    return p


def _invoke(pkg, mapcsv, out):
    import subprocess, sys as _s
    r = subprocess.run([_s.executable, str(SCRIPT_DIR / "ww_p35_xml_alignment.py"),
                        str(pkg), "--map", str(mapcsv), "--out", str(out)],
                       capture_output=True, text=True)
    return r.returncode, r.stdout


def _go_rows(filename_raw_by_ordinal, cols):
    rows = []
    for o in sorted(filename_raw_by_ordinal):
        rows.append({"ordinal": str(o),
                     "raw_display_name": filename_raw_by_ordinal[o],
                     "translated_name": "未过场 %d" % (o + 1),
                     "identifier": "fake_%d" % o,
                     "source_instance": "0x43F3438A94EDEB2B",
                     "status": "TRANSLATED", "note": ""})
    return rows


def main():
    cols = ["source_instance", "ordinal", "identifier", "raw_display_name",
            "translated_name", "status", "note"]
    N = 5
    xml_raw = {0: "An A 1", 1: "An B 2", 2: "An C 3", 3: "An D 4", 4: "An E 5"}
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pkg = FX.build_pkg_fixture(tmp / "fixture.bpkg", list(xml_raw.items()))

        # -- PERFECT --
        out = tmp / "r_perfect.txt"
        rc, so = _invoke(pkg, _mk(tmp, _go_rows(xml_raw, cols), cols), out)
        check("perfect.exit0", rc == 0, "rc=%d" % rc)
        check("perfect.ALIGN_GO", "VERDICT=ALIGN-GO" in so, "")
        for k in ("CSV_ROWS=%d" % N, "XML_ROWS_MATCHED=%d" % N,
                  "RAW_MATCH=%d" % N, "MISSING=0", "MISMATCH=0"):
            check("perfect.has_" + k.split("=")[0], k in so, k)
        check("report.file_written", out.is_file() and "SOURCE_XML=" in out.read_text(
            encoding="utf-8-sig"), str(out))

        # -- REORDER (CSV order irrelevant; join by ordinal) --
        rows = _go_rows(xml_raw, cols)
        rows.reverse()
        out = tmp / "r_reorder.txt"
        rc, so = _invoke(pkg, _mk(tmp, rows, cols), out)
        check("reorder.GO", rc == 0 and "VERDICT=ALIGN-GO" in so,
              "rc=%d" % rc)

        # -- MISSING (CSV references ordinal 99 absent from XML) --
        bad = _go_rows(xml_raw, cols)
        bad.append({"ordinal": "99", "raw_display_name": "Ghost 100",
                    "translated_name": "x", "identifier": "f", 
                    "source_instance": "0x43F3438A94EDEB2B",
                    "status": "TRANSLATED", "note": ""})
        out = tmp / "r_missing.txt"
        rc, so = _invoke(pkg, _mk(tmp, bad, cols), out)
        check("missing.fail", rc == 6, "rc=%d" % rc)
        check("missing.count", "MISSING=1" in so and "MISSING ordinal=99" in so, "")

        # -- MISMATCH (CSV raw differs at ordinal 2) --
        mrx = dict(xml_raw)
        mrx[2] = "An Wrong Raw"
        out = tmp / "r_mismatch.txt"
        rc, so = _invoke(pkg, _mk(tmp, _go_rows(mrx, cols), cols), out)
        check("mismatch.fail", rc == 6, "rc=%d" % rc)
        check("mismatch.count", "MISMATCH=1" in so
              and "ordinal=2" in so and "An Wrong Raw" in so and "An C 3" in so, "")

        # -- BADPAYLOAD (translated_name empty) --
        byp = _go_rows(xml_raw, cols)
        byp[0]["translated_name"] = "   "
        out = tmp / "r_badpayload.txt"
        rc, so = _invoke(pkg, _mk(tmp, byp, cols), out)
        check("badpayload.fail", rc == 6, "rc=%d" % rc)
        check("badpayload.detected", "EMPTY_TRANSLATED_NAME=1" in so, "")

        # -- MISSING FILE branches --
        out = tmp / "r_nofile.txt"
        rc, _ = _invoke(pkg, tmp / "does_not_exist.csv", out)
        check("no_file.exit3", rc == 3, "rc=%d" % rc)

    # summary
    npass = sum(1 for _, c, _ in results if c)
    print("\n%d/%d checks passed" % (npass, len(results)))
    if npass != len(results):
        for name, c, d in results:
            if not c:
                print("  FAILED: %s %s" % (name, d))
        sys.exit(1)
    print("ALL GREEN")


if __name__ == "__main__":
    main()
