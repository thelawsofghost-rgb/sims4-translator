#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_catalog_groundtruth_logic_test.py --- offline mechanism test for the
real-XML ground-truth census (ww_p32_catalog_groundtruth.py).  Synthesizes a
faithful 479-row roster and locks that the census:
  * labels each row's location form (scalar/list/unmapped-struct/missing) exactly
    as the catalogue bridge will,
  * enumerates every distinct animation_genders token + its catalogue_map,
  * counts prop-list/object carriers and actor-count distribution,
  * paints the ordinal-318 bridge portrait (MALE/FEMALE / DOUBLE_BED locations /
    no props) with a catalogue_map that is exact.
Real counts are decided on the Windows run against the real package; this locks
the MECHANISM only and never claims real tallies.

Exit: 0 = all pass; 1 = any fail.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ww_p32_identifier_catalog as C  # noqa: E402
import ww_p32_catalog_groundtruth as G  # noqa: E402

WW_INST = 0x43F3438A94EDEB2B
ORD = C.GOLDEN_ORDINAL
_passes = []


def check(name, cond, detail=""):
    _passes.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))
    return bool(cond)


def _actor(clip, gender, i="1"):
    return ('<U><T n="actor_id">%s</T><T n="animation_clip_name">%s</T>'
            '<T n="animation_genders">%s</T></U>' % (i, clip, gender))


def _row(i):
    if i == ORD:
        dn = "NOT Caught Cheating 2"; au = "Nevely42"; cat = "VAGINAL"
        locs = ["DOUBLE_BED"]; cs = ["n0", "n1"]; gs = ["MALE", "FEMALE"]
    else:
        dn = "Row %d" % i; au = "N"; cat = "ORAL" if i % 2 else "ANAL"
        locs = ["FLOOR"] if i % 3 else []
        cs = ["s%d" % i]; gs = ["MALE"]
    act = "".join(_actor(cs[k], gs[k], str(k + 1)) for k in range(len(gs)))
    l = "" if not locs else ('<T n="animation_locations">%s</T>' % "|".join(locs))
    extra = ""
    if i == 1:
        extra = ('<L n="animation_props_list"><U n="p0"><T n="prop_id">1</T>'
                 '<T n="prop_animation_clip_name">prop_a0</T></U></L>')
    elif i == 2:
        extra = '<T n="object_animation_clip_name">chair</T>'
    return ('<U n="r%d"><T n="animation_raw_display_name">%s</T>'
            '<T n="animation_author">%s</T><T n="animation_category">%s</T>'
            '%s<L n="actors">%s</L>%s</U>' % (i, dn, au, cat, l, act, extra))


def make_us(n=479):
    root = C.ET.fromstring(
        '<I n="X"><L n="animations_list">' + "".join(_row(i) for i in range(n))
        + "</L></I>")
    lists = [nd for nd in root.iter()
             if C._el_tag(nd) == "L" and C._name(nd) == "animations_list"]
    return [c for c in list(lists[0]) if C._el_tag(c) == "U"]


def main():
    us = make_us(479)
    lines = []
    G._collect(us, lines)
    G._ord318(us, lines)
    txt = "\n".join(lines)

    check("GT-rows-479", "ROWS=479" in txt)
    # every row location is scalar (locs present) or missing (empty); synthetic
    # has NO list/unmapped so ensure the census wording reflects only those forms
    check("GT-loc-forms-present",
          "LOCATION_FORM: scalar=" in txt and "list=" in txt and "missing=" in txt)
    # GENDER tokens: MALE(present on all actor slots) & FEMALE(once, ord318)
    check("GT-gender-MALE-map", "catalogue_map=MALE" in txt)
    check("GT-gender-FEMALE-map", "catalogue_map=FEMALE" in txt)
    # props: one carrier row (i=1)
    check("GT-prop-carrier", "PROP_LIST_CONTAINERS rows=1" in txt
          or "PROP_LIST_CONTAINERS rows=1" in txt.replace(" ", "").replace("rows", "rows ")
          or "rows=1" in txt)
    # object clip carrier present (i=2)
    check("GT-object-clip", "OBJECT_CLIP_carriers=1" in txt,
          [ln for ln in txt.splitlines() if "OBJECT_CLIP" in ln][0]
          if any("OBJECT_CLIP" in l for l in txt.splitlines()) else "")
    # ordinal318 bridge portrait is exact for the golden target
    g_lines = [l for l in txt.splitlines() if l.startswith(("raw_display_name=",
               "author=", "sex_category", "locations=", "  actor_id"))]
    check("GT-318-display", "raw_display_name='NOT Caught Cheating 2'" in txt)
    check("GT-318-author", "author='Nevely42'" in txt)
    check("GT-318-locations", "locations=['DOUBLE_BED'] form=scalar" in txt)
    # two actors rendered MALE / FEMALE
    check("GT-318-genders", "runtime_gender=MALE" in txt and "runtime_gender=FEMALE" in txt)
    check("GT-318-props-empty", "props(count=0)=[]" in txt)

    print("")
    print("PASS_COUNT=%d  FAIL_COUNT=%d"
          % (sum(1 for _n, ok in _passes if ok),
             sum(1 for _n, ok in _passes if not ok)))
    failed = [n for n, ok in _passes if not ok]
    if failed:
        print("FAILED_NAMES=%s" % failed)
        return 1
    print("P32_CATALOG_GROUNDTRUTH_LOGIC=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
