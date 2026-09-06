#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_identifier_reconstruct.py --- P32 offline reconstructor: byte-for-byte
replication of the recovered WW SexAnimationInstance.get_identifier identity
construction, WITHOUT entering the game / writing Mods / writing saves.

The recovery (P32 exact evidence, Windows native-marshal disassembly) yielded the
following EXACT semantic for the SHA1 preimage (base identifier string):

    ordered concatenation (NO separator) of:

      [0] get_display_name(string_hash=True, original=True)   -> str (raw English)
      [1] author                                              -> str
      [2] sex_category  -> rendered as SexCategoryType.<NAME> (e.g. VAGINAL)
      [3] len(actors)   -> decimal int str
      [4] for each location (in order): location_category.name
          (e.g. DOUBLE_BED)
      [5] object_animation_clip_name   (skipped when empty string)
      [6] object_geometry_state  if truthy else None   (None skipped)
      [7] object_material_state  if truthy else None   (None skipped)
      [8] for each actor: actor.get_gender_type(default_gender=True)
                -> rendered as SexGenderType.<NAME> (MALE / FEMALE)
      [9] for each actor: actor.get_animation_clip_name()
                (nevely42_cheat2_a0 / ...)
      [10] for each actor, appended individually, final flattened:
                actor.position_offset.x
                actor.position_offset.y
                actor.position_offset.z
                actor.facing_position_offset
      [11] for each prop:
                if prop_geometry_state truthy:
                    (prop_animation_clip_name, prop_geometry_state) rendered
                else:
                    rendered prop_animation_clip_name
      [12] version if version > 1 else None   (None skipped)

    SKIP rule (applies at the FINAL flat level, i.e. over each produced scalar):
        identifier is None            -> skip
        identifier not a str         -> str(identifier)
        no separator, direct concatenation

    then:
        hashlib.sha1(base_identifier.encode("utf-8")).hexdigest()

This module exposes the pure function `reconstruct_identifier(*, fields)` that
takes a structured dict of SEMANTIC inputs and returns {preimage, sha1}, plus a
`build_identifier_parts()` driven off a NORMALIZED ordered spec so the field
order and skip/str rules are expressed once, not re-typed per fixture.

Design constraints:
  * Hard-codes NO magic numbers from the expected hash.  The skip/str/order rules
    are derived from the disassembly evidence and expressed structurally.
  * Renders enums by qualified constant name (SexCategoryType.VAGINAL,
    SexGenderType.MALE) matching how the runtime stringifies these enum values;
    pass the raw NAME (e.g. "VAGINAL") plus a type label that is prepended.
  * Actor position floats rendered with the same float->str the runtime uses:
    values that are integral floats print as "0.0" (repr of float 0.0).  Callers
    pass NUMERIC values (0.0) and we render with repr(float(v)); this reproduces
    Python's built-in float stringification that the runtime listcomp flattening
    used (str(identifier)).  Non-str identifiers are str()-ed (step 'skip') with
    str() semantics, === repr for floats.
  * Output writes ONLY under --out-dir (or prints to stdout).  ZERO write to Mods.

The golden ordinal-318 fixture (validated against the real WW runtime identifier
d0528d3795ca42c60ca5a9eb6bad8658ea74e4b5) is in the logic test, not hard-wired here
as an expected assertion (the reconstructor itself must be input-agnostic).
"""
import hashlib
import json
import sys
from pathlib import Path

# ---- golden runtime identifier (ordinal 318, WW_Nevely42) for the logic test ==
GOLDEN_SHA1_318 = "d0528d3795ca42c60ca5a9eb6bad8658ea74e4b5"


def _raw(float_or_num):
    """Render a numeric position/facing as the exact str a str(identifier)
    produces at runtime.  float 0.0 -> '0.0'.  ints become '0'.  We take a float
    positional value; the WW fields are floats so repr(float) is authoritative."""
    f = float(float_or_num)
    if f == int(f) and abs(f) < 1e15:
        # Python repr of a whole float: 0.0 -> '0.0', -0.0 -> '-0.0'
        text = repr(f)
    else:
        text = repr(f)
    return text


def _enum(label, name):
    """Render an enum constant as '<label>.<name>' (SexCategoryType.VAGINAL)."""
    return "%s.%s" % (label, name)


def _version_or_none(version):
    return version if (str(version).strip() and float(version) > 1) else None


def build_identifier_parts(fields):
    """Produce the ordered FINAL flat list of scalars (post skip/str/expand)
    exactly as the runtime get_identifier would append them.  `fields` is the
    normalized semantic dict (see reconstruct_identifier docstring)."""
    out = []

    # [0] display name (string_hash=True, original=True) -> raw English str
    dn = fields.get("display_name")
    if dn is not None:
        out.append(dn)

    # [1] author
    au = fields.get("author")
    if au is not None:
        out.append(au)

    # [2] sex_category (SexCategoryType.<NAME>)
    sc = fields.get("sex_category")
    if sc is not None:
        out.append(_enum("SexCategoryType", sc))

    # [3] len(actors)
    actors = fields.get("actors") or []
    out.append(str(len(actors)))

    # [4] each location_category.name (in order)
    for loc in (fields.get("locations") or []):
        if loc:
            out.append(loc)

    # [5] object_animation_clip_name (skipped when empty)
    oclip = fields.get("object_animation_clip_name")
    if oclip:
        out.append(oclip)

    # [6] object_geometry_state if truthy else None
    ogeom = fields.get("object_geometry_state")
    if ogeom:
        out.append(_raw(ogeom) if isinstance(ogeom, (int, float)) else str(ogeom))
    # [7] object_material_state if truthy else None
    omat = fields.get("object_material_state")
    if omat:
        out.append(_raw(omat) if isinstance(omat, (int, float)) else str(omat))

    # [8],[9],[10] per-actor ordered fields
    for actor in actors:
        g = actor.get("gender_type")  # SexGenderType name, e.g. MALE / FEMALE
        if g:
            out.append(_enum("SexGenderType", g))
    for actor in actors:
        clip = actor.get("animation_clip_name")
        if clip:
            out.append(clip)
    for actor in actors:
        off = actor.get("position_offset") or {}
        facing = actor.get("facing_position_offset")
        out.append(_raw(off.get("x", 0.0)))
        out.append(_raw(off.get("y", 0.0)))
        out.append(_raw(off.get("z", 0.0)))
        if facing is not None:
            out.append(_raw(facing))

    # [11] props
    for prop in (fields.get("props") or []):
        p_geom = prop.get("geometry_state")
        p_clip = prop.get("animation_clip_name")
        if p_geom:
            # (prop_animation_clip_name, prop_geometry_state) rendered tuple-ish
            out.append("(%s, %s)" % (p_clip, _raw(p_geom) if isinstance(p_geom, (int, float)) else str(p_geom)))
        else:
            if p_clip:
                out.append(p_clip)

    # [12] version if version > 1 else None
    v = fields.get("version")
    _v = _version_or_none(v)
    if _v is not None:
        out.append(_raw(_v) if isinstance(_v, (int, float)) else str(_v))

    return out


def reconstruct_identifier(fields):
    """Return {'sha1':..., 'preimage':..., 'parts':[...]} for the given semantic
    fields dict:
        {
          'display_name': str,
          'author': str,
          'sex_category': 'VAGINAL',
          'actors': [ { 'gender_type':'MALE','animation_clip_name':'a0',
                        'position_offset':{'x':..,'y':..,'z':..},
                        'facing_position_offset':.. }, ... ],
          'locations': ['DOUBLE_BED', ...],
          'object_animation_clip_name': str,
          'object_geometry_state': .., 'object_material_state': ..,
          'props': [{'animation_clip_name':..,'geometry_state':..},...],
          'version': int,
        }
    Order of actor[8]/[9]/[10] differs from actor-order interleave: the runtime
    appends genders for ALL actors, then clips for ALL actors, then each actor's
    3 offsets + facing flattened.  See build_identifier_parts.
    """
    parts = build_identifier_parts(fields)
    preimage = "".join(str(p) for p in parts)
    sha1 = hashlib.sha1(preimage.encode("utf-8")).hexdigest()
    return {"sha1": sha1, "preimage": preimage, "parts": parts}


def parse_args(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None,
                    help="path to a JSON file with the semantic `fields` dict "
                         "(keys per reconstruct_identifier docstring). "
                         "If omitted, prints usage + a sample reconstruction.")
    ap.add_argument("--out", default=None,
                    help="optional output path for the result JSON (default stdout)")
    return ap.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    if a.json:
        with open(a.json, "r", encoding="utf-8") as f:
            data = json.load(f)
        fields = data if isinstance(data, dict) and "actors" in data else data.get("fields", data)
        res = reconstruct_identifier(fields)
        payload = json.dumps(res, ensure_ascii=False, indent=2)
    else:
        # self-test sample reproducing the ordinal-318 SHAPE with literal 0.0 so
        # the tool is usable standalone.
        sample = {
            "display_name": "NOT Caught Cheating 2",
            "author": "Nevely42",
            "sex_category": "VAGINAL",
            "actors": [
                {"gender_type": "MALE", "animation_clip_name": "nevely42_cheat2_a0",
                 "position_offset": {"x": 0.0, "y": 0.0, "z": 0.0},
                 "facing_position_offset": 0.0},
                {"gender_type": "FEMALE", "animation_clip_name": "nevely42_cheat2_a1",
                 "position_offset": {"x": 0.0, "y": 0.0, "z": 0.0},
                 "facing_position_offset": 0.0},
            ],
            "locations": ["DOUBLE_BED"],
            "object_animation_clip_name": "",
            "object_geometry_state": None,
            "object_material_state": None,
            "props": [],
            "version": 1,
        }
        res = reconstruct_identifier(sample)
        payload = json.dumps(res, ensure_ascii=False, indent=2)

    if a.out:
        Path(a.out).write_text(payload + "\n", encoding="utf-8")
        print("WROTE %s" % a.out)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
