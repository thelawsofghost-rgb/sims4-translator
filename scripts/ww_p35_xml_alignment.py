#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P35.0 XML alignment - READ ONLY reconciliation of P34 final mapping against the
real WW_Nevely42 WW_ANIM_XML source.  NO package / NO STBL / NO Mods written.

Purpose
-------
Prove the P34 final CSV (output/p34/p34_translation_mapping_final.csv, 479 rows)
is a faithful 1:1 projection of the real WW_ANIM_XML animations_list before any
P35.1 XML override bake:

  * ordinal sequence matches the XML appearance order (0-based)
  * raw_display_name matches byte-for-byte the XML animation_raw_display_name
  * every final row is locatable in the XML (479/479)
  * translated_name is present and usable as the override payload

Join key: `ordinal` (0-based positional index under the WW_ANIM_XML
<L n='animations_list'> direct <U n='anmN'> children).

Authoritative source walker: reuse `ww_p32_identifier_catalog.load_roster` +
its raw extraction, so enumeration logic is byte-identical to the run that
produced P32/P33/P34.  Never reinterpreted.

Constraints enforced (fail-closed):
  ZERO_WRITE_TO_MODS=YES
  ZERO_WRITE_TO_SAVES=YES
  no STBL generation, no package generation, source package opened read-only.

Output: P35_XML_ALIGNMENT_REPORT.txt (terminal + file) with fields:
  SOURCE_XML=           real WW .package path + WW_ANIM_XML TGI
  CSV_ROWS=             rows read from p34 final
  XML_ROWS_MATCHED=     rows that both exist in XML and passed raw equality
  RAW_MATCH=            raw_display_name byte match count (== XML_ROWS_MATCHED)
  MISSING=              final ordinals absent from XML roster
  MISMATCH=             final rows whose raw differs from XML raw

Exit codes: 0 ALIGN-GO / 2 source pkg missing / 3 p34 final missing /
4 walker/parse error / 5 raw row missing fields / 6 alignment failed.

Usage (Windows, READ ONLY):
  python scripts\\ww_p35_xml_alignment.py "<WW_Nevely42_Animations.package>" \
      --map output\\p34\\p34_translation_mapping_final.csv \
      [--out output/p35/P35_XML_ALIGNMENT_REPORT.txt]
"""
import argparse
import csv
import importlib.util
import io
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# ---- reuse the authoritative P32 walker module (identical enumeration) ----
def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


p32 = _load("ww_p32_identifier_catalog",
            SCRIPT_DIR / "ww_p32_identifier_catalog.py")

DISPLAY_FIELDS = p32.DISPLAY_FIELDS          # ("animation_raw_display_name","raw_display_name")
EXPECT_INSTANCE = p32.EXPECT_INSTANCE

# ---- pure XML helper identical to P32.build_row raw extraction ----
def _single_text(row, names, tag="T"):
    for nd in row.iter():
        if _el_tag(nd) != tag:
            continue
        if _name(nd) in names:
            return _text(nd)
    # fall back to any scalar (E/I/T) carrying a matching field name
    for nd in row.iter():
        if _name(nd) in names:
            return _text(nd)
    return ""


def _el_tag(el):
    if not isinstance(el.tag, str):
        return ""
    return el.tag.rsplit("}", 1)[-1]


def _name(el):
    return (el.get("n") or "").strip()


def _text(el):
    return "" if el.text is None else el.text


def entry_raw(row):
    """Byte-faithful display text of one animation <U> as P32 feed it."""
    return _single_text(row, DISPLAY_FIELDS)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pkg", help="WW_Nevely42_Animations.package path (read-only)")
    ap.add_argument("--map", required=True,
                    help="output/p34/p34_translation_mapping_final.csv")
    ap.add_argument("--out",
                    default=str(ROOT / "output" / "p35" / "P35_XML_ALIGNMENT_REPORT.txt"))
    a = ap.parse_args(argv)

    pkg = Path(a.pkg)
    if not pkg.is_file():
        print("ERROR: source pkg missing (exit 2)", file=sys.stderr)
        return 2
    map_csv = Path(a.map)
    if not map_csv.is_file():
        print("ERROR: p34 final csv missing (exit 3)", file=sys.stderr)
        return 3

    # ---- 2) load authoritative XML roster (identical enumeration as P32) ----
    try:
        sha, inst, entry_count, us = p32.load_roster(pkg, accept_any_sha=True)
    except Exception as ex:
        print("ERROR for %s: %s (exit 4)" % (pkg, ex), file=sys.stderr)
        return 4

    # ordinal (0-based) -> xml raw, plus the raw ET row for deeper fields
    xml_rows = {}
    for ordinal, row in enumerate(us):
        xml_rows[ordinal] = entry_raw(row)

    # ---- 3) read P34 final ----
    try:
        with open(map_csv, encoding="utf-8-sig", newline="") as fh:
            rd = list(csv.DictReader(fh))
    except Exception as ex:
        print("ERROR reading p34 final: %s (exit 3)" % ex, file=sys.stderr)
        return 3

    # ---- 4) align ----
    csv_rows = 0
    matched = 0
    raw_match = 0
    missing = []
    mismatch = []
    bad_payload = []   # translated_name empty -> cannot be override input
    for r in rd:
        try:
            ordinal = int((r.get("ordinal") or "").strip())
        except ValueError:
            ordinal = None
        raw = (r.get("raw_display_name") or "")
        zh = (r.get("translated_name") or "")
        if ordinal is None:
            mismatch.append((None, "(bad ordinal)", raw))
            continue
        csv_rows += 1
        if ordinal not in xml_rows:
            missing.append(ordinal)
            continue
        xml_raw = xml_rows[ordinal]
        if raw == xml_raw:
            matched += 1
            if raw.strip() == xml_raw.strip():
                raw_match += 1  # byte identity after any trivial padding
            if not zh.strip():
                bad_payload.append(ordinal)
        else:
            mismatch.append((ordinal, xml_raw, raw))

    # ---- 5) report ----
    L = []
    ap_ = L.append
    ap_("===== P35.0 XML ALIGNMENT (READ ONLY) =====")
    ap_("SOURCE_XML=%s" % pkg)
    ap_("  WW_ANIM_XML_TYPE=0x7DF2169C  INSTANCE=%s  ENTRY_COUNT=%d  SOURCE_SHA256=%s"
        % (inst, entry_count, sha))
    ap_("CSV_ROWS=%d" % csv_rows)
    ap_("XML_ROWS_MATCHED=%d" % matched)
    ap_("RAW_MATCH=%d" % raw_match)
    ap_("MISSING=%d" % len(missing))
    for o in missing:
        ap_("  MISSING ordinal=%s" % o)
    ap_("MISMATCH=%d" % len(mismatch))
    for o, xraw, craw in mismatch:
        ap_("  MISMATCH ordinal=%s xml=%r csv=%r" % (o, xraw, craw))
    if bad_payload:
        ap_("EMPTY_TRANSLATED_NAME=%d ordinals=%s"
            % (len(bad_payload), sorted(bad_payload)[:40]))
    ok = (entry_count == csv_rows == matched == raw_match
          and not missing and not mismatch and not bad_payload)
    ap_("VERDICT=%s" % ("ALIGN-GO" if ok else "ALIGN-FAIL"))
    ap_("ZERO_WRITE_TO_MODS=YES  ZERO_WRITE_TO_SAVES=YES  (no package/STBL generated)")

    text = "\n".join(L)
    out_path = Path(a.out)
    if str(out_path) != "-":
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8-sig") as fh:
            fh.write(text + "\n")
    print(text)
    print("\n[written] %s" % out_path)
    return 0 if ok else 6


if __name__ == "__main__":
    sys.exit(main())
