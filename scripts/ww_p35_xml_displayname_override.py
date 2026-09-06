#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P35.1 -- WW_ANIM_XML display_name override builder (full 479-row, final.csv).

RESPONSIBILITY
--------------
Project the 479 translation rows of
  output/p34/p34_translation_mapping_final.csv
into a single source-faithful override package that rewrites the WW_ANIM_XML
(0x7DF2169C) `animation_raw_display_name` field for every animation entry
(ordinal 0..478), so the game shows the P34 translated display name.

Reuses the P27-verified WW_ANIM_XML override path: same canary-builder
primitives (safe_parse / read_body_raw / decompress_maybe / compress_like /
read_entry_meta_raw / build_package) and the same exact-<T>-fragment replacement
that P27 proved source-faithful.  This module is a *separate focused builder*
driven by final.csv (not the P27 `-t` CLI), so P27 stays frozen and this P35 phase
is independently auditable.

SOURCE PACKAGE is opened read-only.  NEITHER the source package nor any original
WW file is written.  The source `identifier` / `raw_display_name` / `ordinal` in
final.csv are NEVER modified -- the builder only substitutes display-name text
inside the XML, byte-for-byte preserving every other field/entry.

INPUT  : output/p34/p34_translation_mapping_final.csv  (must be exactly 479 rows)
SOURCE : real WW_Nevely42_Animations.package (read-only; single WW_ANIM_XML)
OUTPUT : output/p35/  ONLY:
           P35.1_ww_anim_displayname_override.package
           P35_XML_OVERRIDE_REPORT.txt        (build + verification report)
           mapping.csv                        (ordinal, old, new)

GATES (all must equal 479, else fail-closed exit 6):
  INPUT_ROWS=479    : final.csv row count
  MATCHED=479       : every final ordinal resolves to an XML animation entry
  RAW_CHECK=479     : final.raw_display_name == XML animation_raw_display_name
                       (byte equality) for every row before any overwrite
  OUTPUT_COUNT=479  : reopen override package -> exactly 479 ordinals now carry
                       translated_name (all 479 written + verified)

Constraints (hard, enforced by design):
  ZERO_WRITE_TO_MODS=YES    -- never writes into any Mods/ folder
  ZERO_WRITE_TO_SAVES=YES   -- never touches any Saves folder
  no STBL generation, no source-package modification, output confined to output/p35/.

exit codes: 0 PASS / 2 missing source pkg / 3 missing/malformed final.csv or
wrong WW_ANIM_XML count / 4 missing animations_list / 5 row/field/fragment
mismatch / 6 build or verify gate failure.

RAW-ENCODING SUBTLETY: raw_display_name in final.csv is the *decoded* text
(ElementTree .text, as P32 emitted it).  This builder compares against the
XML entry's decoded text and re-encodes via exact-fragment replace, so
&#..;/&amp; etc never appear: both sides are the same decoded form.  No
HTML/XML-escaping of the translation value is done (the value becomes the
<text> verbatim), matching how P27 wrote its -t Chinese values.
"""
import argparse
import csv
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import ww_animation_canary_builder as wb
from ww_animation_canary_builder import (decompress_maybe, compress_like)

WW_ANIM_XML = 0x7DF2169C
ANIM_LIST_FIELD = "animations_list"
RAW_FIELD = "animation_raw_display_name"
EXPECT_ROWS = 479


def _tag(el):
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else el.tag


def _name(el):
    return el.get("n") or ""


def fmt_instance(e):
    i = getattr(e, "instance_id", None)
    return f"0x{i:016X}" if isinstance(i, int) else str(i)


def read_final(path: Path):
    """final.csv rows keyed by ordinal -> dict{raw,zh}.  Requires exactly
    EXPECT_ROWS rows with unique ordinal 0..EXPECT_ROWS-1."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if len(rows) != EXPECT_ROWS:
        raise SystemExit("final.csv row count=%d != %d (exit 3)"
                         % (len(rows), EXPECT_ROWS))
    dense, by_ord = {}, {}
    for r in rows:
        try:
            o = int((r.get("ordinal") or "").strip())
        except ValueError:
            o = -1
        by_ord.setdefault(o, []).append(r)
    # require dense 0..EXPECT_ROWS-1 exactly once each
    expect = list(range(EXPECT_ROWS))
    if sorted(by_ord) != expect:
        raise SystemExit("final.csv ordinal set != dense 0..%d (exit 3)"
                         % (EXPECT_ROWS - 1))
    for o, rr in by_ord.items():
        if len(rr) != 1:
            raise SystemExit("final.csv duplicate ordinal %d (exit 3)" % o)
    for o in range(EXPECT_ROWS):
        r = by_ord[o][0]
        dense[o] = {"raw": (r.get("raw_display_name") or ""),
                    "zh": (r.get("translated_name") or "")}
    return dense


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pkg", help="WW_Nevely42_Animations.package (read-only)")
    ap.add_argument("--map", required=True,
                    help="output/p34/p34_translation_mapping_final.csv")
    ap.add_argument("--out-dir", default=None,
                    help="output/p35 (default ROOT/output/p35)")
    a = ap.parse_args(argv)

    src = Path(a.pkg)
    if not src.is_file():
        print("ERROR: source pkg missing (exit 2)", file=sys.stderr)
        return 2
    map_csv = Path(a.map)
    if not map_csv.is_file():
        print("ERROR: final.csv missing (exit 3)", file=sys.stderr)
        return 3
    ROOT = SCRIPT_DIR.parent
    out_dir = Path(a.out_dir) if a.out_dir else (ROOT / "output" / "p35")
    out_dir.mkdir(parents=True, exist_ok=True)
    pkg_out = out_dir / "P35.1_ww_anim_displayname_override.package"
    txt_out = out_dir / "P35_XML_OVERRIDE_REPORT.txt"
    csv_out = out_dir / "mapping.csv"

    # ---- gates ----
    INPUT_ROWS = EXPECT_ROWS

    # ---- read final rows (ordinal-keyed) ----
    try:
        final = read_final(map_csv)
    except SystemExit as ex:
        print("ERROR: %s" % ex, file=sys.stderr)
        return 3

    # ---- read source package, locate single WW_ANIM_XML ----
    idx, ierr = wb.safe_parse(src)
    if ierr is not None:
        print("ERROR: parse %s: %s (exit 3)" % (src, ierr), file=sys.stderr)
        return 3
    ww = [x for x in idx.entries if getattr(x, "type_id", 0) == WW_ANIM_XML]
    if len(ww) != 1:
        print("ERROR: WW_ANIM_XML count=%d (need 1) (exit 3)" % len(ww),
              file=sys.stderr)
        return 3
    e = ww[0]
    t_type = getattr(e, "type_id", 0)
    t_group = getattr(e, "group_id", 0)
    t_inst = (getattr(e, "instance_id", 0)
              if isinstance(getattr(e, "instance_id", None), int) else 0)
    src_inst_fmt = fmt_instance(e)

    body = wb.read_body_raw(src, e)
    plain = decompress_maybe(body)
    try:
        text = plain.decode("utf-8")
    except Exception as ex:
        print("ERROR: decode: %s (exit 5)" % ex, file=sys.stderr)
        return 5
    try:
        root = ET.fromstring(text)
    except ET.ParseError as ex:
        print("ERROR: xml parse: %s (exit 5)" % ex, file=sys.stderr)
        return 5

    list_el = None
    for el in root.iter():
        if _tag(el) == "L" and _name(el) == ANIM_LIST_FIELD:
            list_el = el
            break
    if list_el is None:
        print('ERROR: no <L n="%s"> (exit 4)' % ANIM_LIST_FIELD, file=sys.stderr)
        return 4

    # ---- ordinal -> (raw_el, old_text) by appearance order (identical to P27) ----
    ord_map = {}          # ordinal -> (node, old_text)
    ordinal = 0
    for child in list_el:
        if _tag(child) != "U":
            continue
        if ordinal in final:
            raw_el = None
            for sc in child:
                if _tag(sc) in ("T", "I", "E") and _name(sc) == RAW_FIELD:
                    raw_el = sc
                    break
            if raw_el is None or raw_el.text is None:
                print("ERROR: ordinal %d missing %s (exit 5)"
                      % (ordinal, RAW_FIELD), file=sys.stderr)
                return 5
            ord_map[ordinal] = (raw_el, raw_el.text)
        ordinal += 1

    if ordinal != EXPECT_ROWS:
        print("ERROR: XML entry count=%d != 479 (exit 5)" % ordinal,
              file=sys.stderr)
        return 5

    # MATCHED: final ordinals that resolved to an XML entry (denseness above
    # already guarantees 0..478 all walked)
    MATCHED = len([o for o in range(EXPECT_ROWS) if o in ord_map])

    # ---- RAW_CHECK: every final.raw byte-equals XML raw BEFORE any overwrite ----
    raw_fail = [o for o in range(EXPECT_ROWS)
                if o not in ord_map
                or final[o]["raw"] != ord_map[o][1]]
    if raw_fail:
        print("ERROR: RAW_CHECK %d/%d -- raw mismatch/missing at ordinals=%s "
              "(exit 6)" % (EXPECT_ROWS - len(raw_fail), EXPECT_ROWS,
                            raw_fail[:40]), file=sys.stderr)
        return 6
    RAW_CHECK = EXPECT_ROWS - len(raw_fail)

    # ---- exact-fragment replacement on source text (rest preserved) ----
    new_text = text
    mapping = []
    replaced = []
    for o in range(EXPECT_ROWS):
        node, old = ord_map[o]
        new = final[o]["zh"]
        if old == new:
            continue
        nname = node.get("n")
        if nname is None:
            print("ERROR: ordinal %d raw field lacks n= (exit 5)" % o,
                  file=sys.stderr)
            return 5
        frag_old = '<T n="%s">%s</T>' % (nname, old)
        frag_new = '<T n="%s">%s</T>' % (nname, new)
        cnt = new_text.count(frag_old)
        if cnt != 1:
            print('ERROR: ordinal %d fragment <T n="%s">%s</T> count=%d (need 1) '
                  '(exit 5)' % (o, nname, old, cnt), file=sys.stderr)
            return 5
        new_text = new_text.replace(frag_old, frag_new)
        mapping.append((o, old, new))
        replaced.append(o)

    new_plain = new_text.encode("utf-8")
    new_body = compress_like(body, new_plain)

    # ---- source-faithful metadata ----
    src_major, src_minor, hdr_comp, src_meta = wb.read_entry_meta_raw(src)
    m0 = next((m for m in src_meta if m.get("type") == t_type
               and m.get("group") == t_group and m.get("inst") == t_inst),
              None) or (src_meta[0] if src_meta else None)
    if m0 is None:
        print("ERROR: cannot read source WW_ANIM_XML meta (exit 6)",
              file=sys.stderr)
        return 6
    meta = {
        "comp_state": bool(m0.get("size_comp")),
        "comp_type": m0.get("comp_type", 0),
        "mem_size": m0.get("mem_size", len(new_plain)),
        "offset_high_bit": int(m0.get("offset_comp", 0)),
        "size_high_bit": int(m0.get("size_comp", 0)),
    }

    try:
        wb.build_package([(t_type, t_group, t_inst, new_body, meta)],
                         pkg_out, header_comp=hdr_comp,
                         major=src_major, minor=src_minor)
    except Exception as ex:
        print("ERROR: build_package: %s (exit 6)" % ex, file=sys.stderr)
        return 6

    # ---- reopen + verify OUTPUT_COUNT=479, type/instance preserved ----
    L = []
    L.append("===== P35.1 WW_ANIM_XML DISPLAY_NAME OVERRIDE (READ-ONLY SOURCE) =====")
    L.append("source    : %s" % src)
    L.append("out_pkg   : %s" % pkg_out)
    L.append("final.csv : %s" % map_csv)
    L.append("")
    L.append("INPUT_ROWS=%d" % INPUT_ROWS)
    L.append("MATCHED=%d" % MATCHED)
    L.append("RAW_CHECK=%d" % RAW_CHECK)

    v_ok = True
    OUTPUT_COUNT = 0
    try:
        v_idx, v_err = wb.safe_parse(pkg_out)
        if v_err is not None:
            L.append("reopen parse FAIL: %s" % v_err)
            v_ok = False
        else:
            v_ww = [x for x in v_idx.entries
                    if getattr(x, "type_id", 0) == WW_ANIM_XML]
            L.append("reopen type      : 0x%08X (want 0x%08X) %s"
                     % (WW_ANIM_XML, WW_ANIM_XML,
                        "OK" if len(v_ww) == 1 else "FAIL(count=%d)" % len(v_ww)))
            if len(v_ww) != 1:
                v_ok = False
            else:
                ve = v_ww[0]
                v_inst = fmt_instance(ve)
                L.append("reopen instance  : %s (source %s) %s"
                         % (v_inst, src_inst_fmt,
                            "OK" if v_inst == src_inst_fmt else "FAIL"))
                if v_inst != src_inst_fmt:
                    v_ok = False
                vbody = wb.read_body_raw(pkg_out, ve)
                vplain = decompress_maybe(vbody).decode("utf-8", errors="replace")
                try:
                    vroot = ET.fromstring(vplain)
                except ET.ParseError as ex2:
                    L.append("reopen xml parse FAIL: %s" % ex2)
                    v_ok = False
                    vroot = None
                if vroot is not None:
                    vl = None
                    for el in vroot.iter():
                        if _tag(el) == "L" and _name(el) == ANIM_LIST_FIELD:
                            vl = el
                            break
                    vord = 0
                    ok_n = 0
                    check_tail = []
                    for child in (vl if vl is not None else []):
                        if _tag(child) != "U":
                            continue
                        if vord in final:
                            got = None
                            for sc in child:
                                if _tag(sc) in ("T", "I", "E") and _name(sc) == RAW_FIELD:
                                    got = (sc.text or "")
                                    break
                            want = final[vord]["zh"]
                            good = (got == want)
                            ok_n += (1 if good else 0)
                            if not good:
                                v_ok = False
                            if len(check_tail) < 8:
                                check_tail.append((vord, repr(got), repr(want), good))
                        vord += 1
                    OUTPUT_COUNT = ok_n
                    L.append("reopen total entries: %d" % vord)
                    for o, g, w, good in check_tail:
                        L.append("  ordinal %-5d raw=%s want=%s %s"
                                 % (o, g, w, "OK" if good else "FAIL"))
                    L.append("OUTPUT_COUNT=%d / %d  %s"
                             % (OUTPUT_COUNT, EXPECT_ROWS,
                                "OK" if OUTPUT_COUNT == EXPECT_ROWS else "FAIL"))
                    if OUTPUT_COUNT != EXPECT_ROWS:
                        v_ok = False
    except Exception as ex:
        L.append("verify exception: %s" % ex)
        v_ok = False

    gate_ok = (INPUT_ROWS == EXPECT_ROWS and MATCHED == EXPECT_ROWS
               and RAW_CHECK == EXPECT_ROWS and OUTPUT_COUNT == EXPECT_ROWS)
    L.append("")
    L.append("replaced_ordinals=%d  total_final=%d"
             % (len(replaced), EXPECT_ROWS))
    L.append("VERDICT=%s" % ("PASS" if (v_ok and gate_ok) else "FAIL"))
    L.append("ZERO_WRITE_TO_MODS=YES  ZERO_WRITE_TO_SAVES=YES")
    L.append("(output confined to %s; no STBL; no source-package write)"
             % out_dir)
    report_text = "\n".join(L)
    txt_out.write_text(report_text, encoding="utf-8")

    with open(csv_out, "w", newline="", encoding="utf-8") as fh:
        wtr = csv.writer(fh)
        wtr.writerow(["ordinal", "old", "new"])
        for o in range(EXPECT_ROWS):
            _, old = ord_map[o]
            wtr.writerow([o, old, final[o]["zh"]])

    print(report_text)
    print("[written] %s" % pkg_out)
    print("[written] %s" % csv_out)
    print("[written] %s" % txt_out)
    if not (v_ok and gate_ok):
        print("ERROR: build/verify gate FAILED (exit 6)", file=sys.stderr)
        return 6
    return 0


if __name__ == "__main__":
    sys.exit(main())
