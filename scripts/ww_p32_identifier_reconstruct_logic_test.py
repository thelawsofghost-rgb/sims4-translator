#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_identifier_reconstruct_logic_test.py --- logic test for
ww_p32_identifier_reconstruct.py.  Pure offline; NO game / NO Mods / NO saves.

Validates that the reconstructor is a faithful, byte-exact replication of the
recovered WW SexAnimationInstance.get_identifier by:

  (A) GOLDEN ordinal-318 reproduction.  The semantic inputs recovered from the
      real WW source + exact disassembly must yield EXACTLY
          sha1 == d0528d3795ca42c60ca5a9eb6bad8658ea74e4b5
          preimage == "NOT Caught Cheating 2Nevely42SexCategoryType.VAGINAL2"
                       "DOUBLE_BEDSexGenderType.MALESexGenderType.FEMALE"
                       "nevely42_cheat2_a0nevely42_cheat2_a1"
                       "0.0"*8
      This is the authoritative gate: it is NOT derived from any expected hash --
      the reconstructor is input-agnostic; the fixture merely plugs real inputs
      and the assertion is exact equality with the independently-observed runtime
      identifier.

  (B) Algorithm invariants (each decodeable straight from the evidence, asserted
      structurally, NOT fitted to the hash):
      B1  actor_count is rendered BEFORE locations (evidence order [3] then [4]).
      B2  gender types for ALL actors precede clip names for ALL actors precede
          per-actor offsets flattened (i.e. not per-actor interleaved): for a
          two-actor fixture the parts order is G0,G1,C0,C1,o0x,o0y,o0z,f0,o1x,...
      B3  positions/facing rendered with Python float str(): integral -> "0.0",
          negative zero -> "-0.0", non-integral -> full repr.
      B4  None / empty-string identity fields are SKIPPED (never contribute "");
          truthy object_geometry/material states DO contribute.
      B5  prop rendering: no geometry_state -> clip only; geometry_state truthy
          -> "(clip, state)" tuple repr.
      B6  version: <=1 -> None (skipped); >1 -> contributed as its float str.
      B7  props with empty clip AND no geometry_state contribute nothing.
      B8  an actor with no gender_type / empty clip contributes only its offsets.
      B9  zero actors -> actor_count "0", no gender/clip/offset parts.
       B10 no props -> nothing; empty locations -> nothing (but count still there).

Exit: 0 = all pass; 1 = any fail.
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ww_p32_identifier_reconstruct import (  # noqa: E402
    GOLDEN_SHA1_318, build_identifier_parts, reconstruct_identifier,
)

GOLDEN_PREIMAGE = ("NOT Caught Cheating 2" + "Nevely42" + "SexCategoryType.VAGINAL"
                   + "2" + "DOUBLE_BED"
                   + "SexGenderType.MALE" + "SexGenderType.FEMALE"
                   + "nevely42_cheat2_a0" + "nevely42_cheat2_a1"
                   + ("0.0" * 8))

_passes = []


def check(name, cond, detail=""):
    _passes.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))
    return bool(cond)


def _mkactor(gender=None, clip=None, x=0.0, y=0.0, z=0.0, facing=0.0):
    a = {"position_offset": {"x": x, "y": y, "z": z}, "facing_position_offset": facing}
    if gender is not None:
        a["gender_type"] = gender
    if clip:
        a["animation_clip_name"] = clip
    return a


def main():
    # ---- (A) golden ordinal-318 ----
    golden_fields = {
        "display_name": "NOT Caught Cheating 2",
        "author": "Nevely42",
        "sex_category": "VAGINAL",
        "actors": [
            _mkactor("MALE", "nevely42_cheat2_a0"),
            _mkactor("FEMALE", "nevely42_cheat2_a1"),
        ],
        "locations": ["DOUBLE_BED"],
        "object_animation_clip_name": "",
        "object_geometry_state": None,
        "object_material_state": None,
        "props": [],
        "version": 1,
    }
    res = reconstruct_identifier(golden_fields)
    check("A-golden-sha1-318", res["sha1"] == GOLDEN_SHA1_318,
          "got=%s want=%s" % (res["sha1"], GOLDEN_SHA1_318))
    check("A-golden-preimage-318", res["preimage"] == GOLDEN_PREIMAGE,
          "mismatch=%s" % (res["preimage"] if res["preimage"] != GOLDEN_PREIMAGE
                           else "(equal)"))

    # parts exactly: [dn, author, cat, '2', loc, M,F, clips, then 8x0.0]
    parts = res["parts"]
    check("A-golden-parts-octet", parts == [
        "NOT Caught Cheating 2", "Nevely42", "SexCategoryType.VAGINAL",
        "2", "DOUBLE_BED",
        "SexGenderType.MALE", "SexGenderType.FEMALE",
        "nevely42_cheat2_a0", "nevely42_cheat2_a1",
        "0.0", "0.0", "0.0", "0.0", "0.0", "0.0", "0.0", "0.0",
    ])

    # consistent independent recompute of the sha1 over preimage bytes
    check("A-sha1-consistent", hashlib.sha1(
        GOLDEN_PREIMAGE.encode("utf-8")).hexdigest() == GOLDEN_SHA1_318)

    # ---- (B) invariants ----
    # B1: actor_count before location
    f2 = {
        "display_name": "X", "author": "A", "sex_category": "VAGINAL",
        "actors": [_mkactor("MALE", "c0")],
        "locations": ["KITCHEN", "SHOWER"],
        "object_animation_clip_name": "", "object_geometry_state": None,
        "object_material_state": None, "props": [], "version": 1,
    }
    p2 = build_identifier_parts(f2)
    check("B1-count-before-locations",
          "1" in p2 and p2.index("1") < min(i for i, v in enumerate(p2)
                                            if v in ("KITCHEN", "SHOWER")),
          "parts=%s" % p2)

    # B2: two-actor gender/clip/offset block ordering (not interleaved)
    f3 = {
        "display_name": "Z", "author": "B", "sex_category": "ANAL",
        "actors": [_mkactor("MALE", "c0", 0.1, 0.2, 0.3, 0.4),
                   _mkactor("FEMALE", "c1", 1.0, 2.0, 3.0, 4.0)],
        "locations": [], "object_animation_clip_name": "",
        "object_geometry_state": None, "object_material_state": None,
        "props": [], "version": 1,
    }
    p3full = build_identifier_parts(f3)
    idx = {}
    for name in ("SexGenderType.MALE", "SexGenderType.FEMALE", "c0", "c1"):
        idx[name] = p3full.index(name)
    check("B2-genders-before-clips-before-offsets",
          idx["SexGenderType.MALE"] < idx["SexGenderType.FEMALE"]
          < idx["c0"] < idx["c1"]
          and all(p3full[i] in ("0.1", "0.2", "0.3", "0.4", "1.0", "2.0", "3.0", "4.0")
                  for i in range(idx["c1"] + 1, len(p3full))),
          "order=%s" % [p3full])

    # B3 float rendering: integral -> '0.0', negative zero -> '-0.0',
    # non-integral -> full repr.  (actor has no gender/clip -> only its 4 offsets)
    check("B3-float-repr",
          build_identifier_parts({"display_name": "d", "author": "", "sex_category": "",
                                  "actors": [_mkactor(None, None, 0.0, -0.0, 1.5, -2.25)],
                                  "locations": [], "object_animation_clip_name": "",
                                  "object_geometry_state": None, "object_material_state": None,
                                  "props": [], "version": 1})[-4:]
          == ["0.0", "-0.0", "1.5", "-2.25"])

    # B4 None/empty skip + truthy object states contribute: object_animation_clip
    # and a non-None geometry_state are appended after locations; a None
    # material_state is skipped.  Layout: [dn,author,cat,count,loc(s),objclip,geom].
    f5 = {
        "display_name": "d", "author": "a", "sex_category": "ORAL",
        "actors": [_mkactor("MALE", "c")],
        "locations": ["DESK"],
        "object_animation_clip_name": "objclip",
        "object_geometry_state": 3,
        "object_material_state": None,   # None skipped
        "props": [], "version": 1,
    }
    p5 = build_identifier_parts(f5)
    # full expected rendered order:
    # dn,author,SexCategoryType.ORAL,count '1', 'DESK', objclip, geom '3',
    # then actor gender MALE, clip c, offsets 0.0*4
    check("B4-object-states-and-sequence",
          p5 == ["d", "a", "SexCategoryType.ORAL", "1", "DESK", "objclip", "3.0",
                 "SexGenderType.MALE", "c", "0.0", "0.0", "0.0", "0.0"],
          "parts=%s" % p5)

    # B5 props tuple vs clip only
    f6 = {
        "display_name": "d", "author": "a", "sex_category": "VAGINAL",
        "actors": [], "locations": [],
        "object_animation_clip_name": "", "object_geometry_state": None,
        "object_material_state": None,
        "props": [{"animation_clip_name": "pclip", "geometry_state": None},
                  {"animation_clip_name": "q", "geometry_state": 2}],
        "version": 1,
    }
    p6 = build_identifier_parts(f6)
    # dn,author,SexCategoryType.VAGINAL,count '0', pclip, then (q, geom '2.0')
    check("B5-prop-render",
          p6 == ["d", "a", "SexCategoryType.VAGINAL", "0", "pclip", "(q, 2.0)"],
          "parts=%s" % p6)

    # B6 version > 1 contributes
    f7 = dict(f6, version=5)
    p7 = build_identifier_parts(f7)
    check("B6-version-gt1", p7[-1] == "5.0", "last=%s" % (p7[-1] if p7 else None))

    # B7 props empty clip & no geom -> nothing
    f8 = dict(f6, props=[{"animation_clip_name": "", "geometry_state": None}])
    check("B7-empty-prop-skip",
          len(build_identifier_parts(f8)) == 4)  # only dn,author,cat,count

    # B8 actor no gender/empty clip -> only offsets
    f9 = {
        "display_name": "d", "author": "a", "sex_category": "VAGINAL",
        "actors": [_mkactor(None, None, 0.0, 0.0, 0.0, 0.0)],
        "locations": [], "object_animation_clip_name": "",
        "object_geometry_state": None, "object_material_state": None,
        "props": [], "version": 1,
    }
    p9 = build_identifier_parts(f9)
    check("B8-actor-min-contrib",
          p9 == ["d", "a", "SexCategoryType.VAGINAL", "1", "0.0", "0.0", "0.0", "0.0"],
          "parts=%s" % p9)

    # B9 zero actors
    p10 = build_identifier_parts(dict(f9, actors=[]))
    check("B9-zero-actors", p10 == ["d", "a", "SexCategoryType.VAGINAL", "0"],
          "parts=%s" % p10)

    # B11 determinism + no accidental mutation of input
    import copy
    f11 = copy.deepcopy(golden_fields)
    r11a = reconstruct_identifier(f11)
    r11b = reconstruct_identifier(f11)
    check("B11-deterministic-no-mutation",
          r11a["sha1"] == r11b["sha1"] == GOLDEN_SHA1_318 and f11 == golden_fields)

    print("")
    print("PASS_COUNT=%d  FAIL_COUNT=%d"
          % (sum(1 for _n, ok in _passes if ok),
             sum(1 for _n, ok in _passes if not ok)))
    failed = [n for n, ok in _passes if not ok]
    if failed:
        print("FAILED_NAMES=%s" % failed)
        return 1
    print("P32_RECONSTRUCT_LOGIC=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
