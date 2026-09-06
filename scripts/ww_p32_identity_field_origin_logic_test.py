#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_identity_field_origin_logic_test.py -- regression harness for the P32
Step-6 Phase-1 identity field-origin audit module.

Asserts:
  A1  origin table renders; 7 PROVEN_TUNING + 8 RUNTIME_DEFAULT_318 inputs, and
      NOTHING is silently marked statically-recoverable when it is a 318 runtime
      default.
  A2  the 318-clone row is usable (all five tuning-backed inputs present, no
      object/prop/geometry/material/version tuning carried) -> no-fabrication
      identity reconstruction OK for that row.
  A3  NON-generalization invariant: a row that CARRIES an object-animation-clip
      tuning field is gated (usable=False) -- we do NOT spread ordinal-318's
      'object clip absent' assumption to it.
  A4  a row missing author is flagged MISSING (usable False).
  A5  multi-location detection per row (['BED','FLOOR']), and 3-actor row remains
      usable when otherwise complete.
  A6  production location parser reads ONLY scalar T n='animation_locations' and
      never substitutes custom_locations / location_constraints.

Exit 0 = all PASS; 1 = any FAIL.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import ww_p32_identity_field_origin as idfo

ROSTER = """<I n="WickedWhimsAnimationPackage"><L n="animations_list">
<U n="s1"><T n="animation_raw_display_name">NOT Caught Cheating 2</T>
 <T n="animation_author">Nevely42</T><T n="animation_category">VAGINAL</T>
 <T n="animation_locations">DOUBLE_BED</T>
 <L n="actors"><U n="a0"><T n="actor_id">0</T><T n="animation_clip_name">nevely42_cheat2_a0</T>
  <T n="animation_type">VAGINAL</T><T n="animation_genders">Male</T></U>
  <U n="a1"><T n="actor_id">1</T><T n="animation_clip_name">nevely42_cheat2_a1</T>
  <T n="animation_type">VAGINAL</T><T n="animation_genders">Female</T></U></L></U>
<U n="s2"><T n="animation_raw_display_name">Multi-Loc Object</T>
 <T n="animation_author">AuthorX</T><T n="animation_category">ORAL</T>
 <T n="animation_locations">BED|FLOOR</T>
 <T n="animation_object_animation_clip_name">obj_clip_9</T>
 <L n="actors"><U n="a0"><T n="actor_id">0</T><T n="animation_clip_name">clipA</T>
  <T n="animation_type">ORAL</T><T n="animation_genders">Male</T></U></L></U>
<U n="s3"><T n="animation_raw_display_name">NoAuthor</T>
 <T n="animation_category">ANAL</T><T n="animation_locations">DESK</T>
 <L n="animation_actors_list"><U n="a0"><T n="actor_id">0</T><T n="animation_clip_name">c1</T>
  <T n="animation_type">ANAL</T><T n="animation_genders">Female</T></U></L></U>
<U n="s4"><T n="animation_raw_display_name">Triple</T>
 <T n="animation_author">B</T><T n="animation_category">VAGINAL</T>
 <T n="animation_locations">CHAIR</T>
 <L n="actors"><U n="a0"><T n="actor_id">0</T><T n="animation_clip_name">p1</T>
  <T n="animation_type">VAGINAL</T><T n="animation_genders">Male</T></U>
  <U n="a1"><T n="actor_id">1</T><T n="animation_clip_name">p2</T>
  <T n="animation_type">VAGINAL</T><T n="animation_genders">Female</T></U>
  <U n="a2"><T n="actor_id">2</T><T n="animation_clip_name">p3</T>
  <T n="animation_type">VAGINAL</T><T n="animation_genders">Male</T></U></L></U>
</L></I>"""


def main():
    passes = []

    def check(name, cond, detail=""):
        passes.append((name, bool(cond)))
        print("PASS %s%s%s" % (name, "  | " if detail else "", detail))

    # A1 origin table counts
    st = [x[2] for x in idfo.FIELD_ORIGIN]
    check("A1-status-counts",
          st.count("PROVEN_TUNING") == 7 and st.count("RUNTIME_DEFAULT_318") == 8
          and st.count("UNKNOWN") == 0,
          "PROVEN=%d RD=%d" % (st.count("PROVEN_TUNING"), st.count("RUNTIME_DEFAULT_318")))
    rec_flags = [x[3] for x in idfo.FIELD_ORIGIN]
    # every RUNTIME_DEFAULT_318 input must NOT be flagged statically-recoverable
    no_leak = all((x[3] is False) for x in idfo.FIELD_ORIGIN if x[2] == "RUNTIME_DEFAULT_318")
    check("A1-runtime-default-not-static", no_leak)

    rows = idfo.census_rows(ROSTER)
    check("A1-rowcount", len(rows) == 4, str(len(rows)))

    # A2 318-clone usable
    check("A2-318clone-usable", rows[0]["usable"] is True,
          repr(rows[0]["provenance"]))
    check("A2-318clone-loc-single", rows[0]["locations"] == ["DOUBLE_BED"])

    # A3 object-clip row gated (do NOT spread 318 'absent object clip')
    check("A3-objectclip-gated",
          rows[1]["usable"] is False
          and rows[1]["special"]["carries_object_or_prop_or_geo_mat"]
          == ["animation_object_animation_clip_name"],
          repr(rows[1]["special"]["carries_object_or_prop_or_geo_mat"]))

    # A4 author-missing flagged
    check("A4-missing-author",
          rows[2]["usable"] is False
          and rows[2]["provenance"]["author"].startswith("MISSING"),
          rows[2]["provenance"]["author"])

    # A5 multi-location + triple-actor usable
    check("A5-multiloc", rows[1]["locations"] == ["BED", "FLOOR"]
          and rows[1]["special"]["multi_location"] is True)
    check("A5-triple-actor-usable", rows[3]["usable"] is True
          and rows[3]["special"]["actor_count_gt_2"] is True)

    # A6 production parser only reads animation_locations, never custom fallback
    e = idfo.census_row(__import__("xml.etree.ElementTree",
                                   fromlist=["ElementTree"]).fromstring(
        '<U n="z"><T n="custom_locations">SHOULD_NOT_BE_USED</T>'
        '<L n="actors"><U n="a0"><T n="actor_id">0</T></U></L></U>'))
    check("A6-no-custom-fallback", e["locations"] == []
          and "custom_locations" not in e["provenance"],
          repr(e["locations"]))

    failed = [n for n, ok in passes if not ok]
    print("PASS_COUNT=%d FAIL_COUNT=%d" % (len(passes) - len(failed), len(failed)))
    if failed:
        print("FAILED_NAMES=%s" % failed)
        return 1
    print("P32_IDENTITY_FIELD_ORIGIN_LOGIC=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
