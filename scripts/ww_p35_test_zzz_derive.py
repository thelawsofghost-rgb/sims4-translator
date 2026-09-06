#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P35.4B -- derive a ZERO-modification TEST package to prove loose-override routing.

PURPOSE (validation tool; read-only on every input):
  Take the ALREADY-DEPLOYED real override package
      output/p35/P35.1_ww_anim_displayname_override.package
  (faithful source TGI 0x7DF2169C + full 479 body) and produce ONE separate test
  package, P35_TEST_zzz.package, whose WW_ANIM_XML body is byte-identical except
  ~3 scattered animation_raw_display_name values replaced by ASCII markers:
      ZZZ_TEST_0 / ZZZ_TEST_1 / ZZZ_TEST_2
  Keeps the SAME type/group/instance/meta + header comp/major/minor as the real
  override (mirrors the P35.1 builder exactly), so it is a drop-in shadow.

  Place it in Mods ALONE during the test (real override moved out):
     * UI shows ZZZ_TEST_*        => loose override of this TGI IS read & rendered
                                      verbatim -> "still English" is NOT routing
                                      failure -> hypothesis C, not A.
     * UI still English original  => loose override is NOT consulted -> A.

HARD CONSTRAINTS (unchanged iron rules):
  ZERO_WRITE_TO_MODS=YES  -- writes ONLY the new test .package next to the input,
                             never into Mods/ (you move it in/out).
  ZERO_WRITE_TO_SAVES=YES
  Never opens source WW package for write. No STBL. No re-translation.
  Caller deletes the test package after the test and restores the real override.

USAGE (Windows box, python3):
  python scripts\ww_p35_test_zzz_derive.py "<...P35.1_ww_anim_displayname_override.package>"
  -> writes "<same dir>\P35_TEST_zzz.package"

EXIT: 0 PASS / 2 missing input / 3 not exactly one WW_ANIM_XML / 4 no field
      entries / 6 build/verify failure.
"""
import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import ww_animation_canary_builder as wb  # noqa: E402
from ww_animation_canary_builder import decompress_maybe, compress_like  # noqa: E402

WW_ANIM_XML = 0x7DF2169C
XML_FIELD = "animation_raw_display_name"
MARKERS = ["ZZZ_TEST_0", "ZZZ_TEST_1", "ZZZ_TEST_2"]


def _tag(el):
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else el.tag


def _name(el):
    return el.attrib.get("n", "")


def _atom_int(x):
    return int(x, 16) if isinstance(x, str) else int(x)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pkg", help="real deployed P35.1 override .package (read-only)")
    a = ap.parse_args(argv)
    src = Path(a.pkg)
    if not src.is_file():
        print("ERROR: input package not found (exit 2)", file=sys.stderr)
        return 2
    out = src.with_name("P35_TEST_zzz.package")

    try:
        idx, err = wb.safe_parse(str(src))
    except Exception as ex:
        print("ERROR: safe_parse: %s (exit 6)" % ex, file=sys.stderr)
        return 6
    if err:
        print("ERROR: %s (exit 3)" % err, file=sys.stderr)
        return 3

    ww = [x for x in idx.entries if _atom_int(getattr(x, "type_id", 0)) == WW_ANIM_XML]
    if len(ww) != 1:
        print("ERROR: WW_ANIM_XML count=%d (need 1) (exit 3)" % len(ww),
              file=sys.stderr)
        return 3
    e = ww[0]
    t_type = WW_ANIM_XML
    t_group = getattr(e, "group_id", 0)
    t_inst = (getattr(e, "instance_id", 0)
              if isinstance(getattr(e, "instance_id", None), int) else 0)

    body = wb.read_body_raw(src, e)          # raw (may be zlib)
    plain = decompress_maybe(body)           # decoded XML bytes
    try:
        root = ET.fromstring(plain)
    except Exception as ex:
        print("ERROR: XML parse of input body: %s (exit 4)" % ex, file=sys.stderr)
        return 4

    # collect field entries under animations_list (same as builder)
    fields = []
    for node in root.iter():
        if _tag(node) == "L" and _name(node) == "animations_list":
            for child in node:
                f = child.find(".//*[@n='%s']" % XML_FIELD)
                if f is not None and f.text is not None and f.text != "":
                    fields.append((child, f))
    if not fields:
        print("ERROR: no non-empty field entries found (exit 4)", file=sys.stderr)
        return 4
    n = len(fields)
    idx_t = sorted({0, n // 2, n - 1})
    marked = 0
    for slot in idx_t:
        if slot < 0 or slot >= len(fields):
            continue
        _c, f = fields[slot]
        f.text = MARKERS[marked]
        marked += 1
    if not marked:
        print("ERROR: zero markup (exit 5)", file=sys.stderr)
        return 5

    new_plain = ET.tostring(root, encoding="utf-8")
    new_body = compress_like(body, new_plain)

    # faithful meta + header, mirror P35.1 builder
    src_major, src_minor, hdr_comp, src_meta = wb.read_entry_meta_raw(src)
    m0 = next((m for m in src_meta
               if _atom_int(m.get("type", 0)) == t_type
               and _atom_int(m.get("group", 0)) == t_group
               and _atom_int(m.get("inst", 0)) == t_inst), None) or \
        (src_meta[0] if src_meta else None)
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
        wb.build_package([(t_type, t_group, t_inst, new_body, meta)], out,
                         header_comp=hdr_comp, major=src_major, minor=src_minor)
    except Exception as ex:
        print("ERROR: build_package: %s (exit 6)" % ex, file=sys.stderr)
        return 6

    # reopen verify: single WW_ANIM_XML, markers present
    try:
        v_idx, v_err = wb.safe_parse(str(out))
    except Exception as ex:
        print("ERROR: verify parse: %s (exit 6)" % ex, file=sys.stderr)
        return 6
    if v_err:
        print("ERROR: verify: %s (exit 6)" % v_err, file=sys.stderr)
        return 6
    vw = [x for x in v_idx.entries if _atom_int(getattr(x, "type_id", 0)) == WW_ANIM_XML]
    if len(vw) != 1:
        print("ERROR: verify WW_ANIM_XML count != 1 (exit 6)", file=sys.stderr)
        return 6
    ve = vw[0]
    vbody = wb.read_body_raw(out, ve)
    found = [mk for mk in MARKERS if mk.encode() in vbody]

    print("===== P35.4B TEST PACKAGE (READ-ONLY DERIVE) =====")
    print("input        : %s" % src)
    print("output       : %s" % out)
    print("type         : 0x%08X" % t_type)
    print("group        : 0x%08X" % _atom_int(t_group))
    print("instance     : 0x%016X" % _atom_int(t_inst))
    print("body_entries : %d  (markers on ordinals %s)" % (n, idx_t))
    print("markers      : %s" % (",".join(found) if found else "NONE"))
    if not found:
        print("ERROR: markers missing after reopen (exit 6)", file=sys.stderr)
        return 6
    print("VERDICT      : PASS")
    print("ZERO_WRITE_TO_MODS=YES  ZERO_WRITE_TO_SAVES=YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
