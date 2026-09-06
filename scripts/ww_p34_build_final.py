#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p34_build_final.py --- P34.3 final-mapping producer w/ override layer.

Builds output/p34/p34_translation_mapping_final.csv from the latest clean P34.2
stage (p34_translation_mapping_normalized2.csv) PLUS the operator override table
configs/p34_translation_overrides.csv.

Override layer (P34.3 requirement #2):
  * configs/p34_translation_overrides.csv is the SINGLE highest-priority source
    for a row's translated_name.
  * A row is keyed by `identifier` (the P34 mapping identifier, unchanged).
  * Applying an override ONLY rewrites translated_name -- it NEVER touches
    identifier or raw_display_name (those stay byte-for-byte).
  * Why overrides exist: a handful of rows (e.g. series S00317 'NOT Caught
    Cheating') cannot be reconciled by the deterministic P34.1/2 passes because
    the LLM produced two genuinely different Chinese stems with no number/suffix
    to pivot on ('未被捉奸' vs '没被发现').  The human pins ONE canonical zh for
    every such series member so INV5 (one zh stem per series) finally PASSES.

Encoding (P34.3 requirement #1):
  * Reads input + overrides with utf-8-sig.
  * RUNS the encoding gate on the input FIRST: if the input is missing a BOM OR
    carries GBK-mojibake / replacement chars, build_final REFUSES to write
    (exit 2, fail-closed) -- it never turns polluted text into a 'final' file.
  * Writes the final CSV as UTF-8 **with BOM** (utf-8-sig) so Windows/Excel can
    no longer misdecode it.

No STBL generation here.  This is a translation-mapping file only.

Exit codes: 0 = wrote final; 2 = input/gate/override failure.
"""
from __future__ import print_function
import argparse
import csv
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
import ww_p34_encoding_gate as EG     # noqa: E402

MAP_COLS = ["source_instance", "ordinal", "identifier", "raw_display_name",
            "translated_name", "status", "note"]
DEF_OVERRIDES = os.path.join(_SCRIPT_DIR, "..", "configs",
                             "p34_translation_overrides.csv")
DEF_OUT = os.path.join("output", "p34", "p34_translation_mapping_final.csv")


# --------------------------------------------------------------------------- #
# Override table ------------------------------------------------------------ #
# --------------------------------------------------------------------------- #
def read_overrides(path):
    """Read override CSV -> (overrides{identifier: translated_name}, rows_scanned)
    Only non-empty translated_name values are kept (empty override row = a
    disabled/tombstone row -> never applied)."""
    overrides, scanned = {}, 0
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        cols = rd.fieldnames or []
        need = {"identifier", "translated_name"}
        missing = need - set(cols)
        if missing:
            raise ValueError(
                "overrides CSV missing required column(s) %s (have %s)"
                % (sorted(missing), sorted(cols)))
        for row in rd:
            scanned += 1
            i = (row.get("identifier") or "").strip()
            t = (row.get("translated_name") or "").strip()
            if not i or not t:
                continue                        # tombstone / empty ignored
            overrides[i] = t
    return overrides, scanned


def load_mapping(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        cols = rd.fieldnames or []
        need = {"identifier", "translated_name", "raw_display_name"}
        if not need.issubset(cols):
            raise ValueError("mapping CSV missing required column(s) %s (have %s)"
                             % (sorted(need - set(cols)), sorted(cols)))
        return [dict(r) for r in rd]


def apply_overrides(rows, override_map):
    """Apply override_map[identifier] -> rows' translated_name.

    Returns (out_rows, applied:list[(identifier, old, new)],
             warnings:list[str]).  Never touches identifier/raw_display_name.
    An override whose identifier is absent from the mapping is a WARNING (it may
    be a stale row) but not an error: the mapping is authoritative about which
    rows exist, so a stray override is reported for human review, not silently
    dropped from the record of intent.
    """
    out = []
    applied, warnings = [], []
    idents = {r.get("identifier") for r in rows}
    for r in rows:
        i = r.get("identifier")
        ov = override_map.get(i) if i is not None else None
        if ov is not None and ov != (r.get("translated_name") or ""):
            old = r.get("translated_name") or ""
            r = dict(r)
            r["translated_name"] = ov
            applied.append((i, old, ov))
        out.append(r)
    for i in sorted(set(override_map) - idents):
        warnings.append("override references unknown identifier %r (no mapping "
                        "row) -- ignored, please audit" % i)
    return out, applied, warnings


def write_final(path, rows):
    """Write mapping as UTF-8 WITH BOM (utf-8-sig).  The .read()/write() codecs
    are unified (utf-8-sig) so read+write never disagree on the BOM."""
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=MAP_COLS, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input_mapping",
                    help="latest P34 stage mapping CSV "
                         "(e.g. output/p34/p34_translation_mapping_normalized2.csv)")
    ap.add_argument("--overrides", default=os.path.normpath(DEF_OVERRIDES),
                    help="override CSV (identifier,translated_name,...)")
    ap.add_argument("--out", default=os.path.normpath(DEF_OUT),
                    help="final mapping output path")
    ap.add_argument("--no-encoding-gate", action="store_true",
                    help="(not recommended) skip the input encoding gate; short-"
                         "circuits P34.3 #1 enforcement")
    a = ap.parse_args(argv)

    if not os.path.isfile(a.input_mapping):
        print("VERDICT=FAIL", file=sys.stderr)
        print("REASON=INPUT_MISSING:%s" % a.input_mapping, file=sys.stderr)
        return 2
    if not os.path.isfile(a.overrides):
        print("VERDICT=FAIL", file=sys.stderr)
        print("REASON=OVERRIDES_MISSING:%s" % a.overrides, file=sys.stderr)
        return 2

    # ---- encoding gate on the input (P34.3 #1) --------------------------
    if not a.no_encoding_gate:
        estatus, eproblems, _ = EG.audit_file(a.input_mapping)
        if estatus != "PASS":
            print("VERDICT=FAIL", file=sys.stderr)
            print("REASON=ENCODING_GATE_FAIL on input (will NOT write 'final' "
                  "from polluted/misencoded text):", file=sys.stderr)
            for p in eproblems[:10]:
                print("  - " + p, file=sys.stderr)
            return 2

    try:
        rows = load_mapping(a.input_mapping)
    except Exception as e:
        print("VERDICT=FAIL", file=sys.stderr)
        print("REASON=MAP_READ:%s" % e, file=sys.stderr)
        return 2
    try:
        override_map, scanned = read_overrides(a.overrides)
    except Exception as e:
        print("VERDICT=FAIL", file=sys.stderr)
        print("REASON=OVERRIDE_READ:%s" % e, file=sys.stderr)
        return 2

    out_rows, applied, warnings = apply_overrides(rows, override_map)
    if applied:
        n_before = len(rows)
        for ident, old, new in applied:
            print("OVERRIDE %s: %r -> %r" % (ident, old, new))
    else:
        print("OVERRIDES=none-active (override CSV %d scanned, %d kept non-empty)"
              % (scanned, len(override_map)))

    out_dir = os.path.dirname(os.path.abspath(a.out)) if a.out else "."
    if out_dir:
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            print("VERDICT=FAIL", file=sys.stderr)
            print("REASON=MKDIR:%s" % e, file=sys.stderr)
            return 2
    write_final(a.out, out_rows)

    for w_ in warnings:
        print("WARN " + w_)
    print("VERDICT=GO")
    print("OUT=%s" % a.out)
    print("ROW_COUNT=%d (input %d, override rows applied=%d)"
          % (len(out_rows), len(rows), len(applied)))
    print("OVERRIDES_SCANNED=%d OVERRIDES_ACTIVE=%d WARNINGS=%d"
          % (scanned, len(override_map), len(warnings)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
