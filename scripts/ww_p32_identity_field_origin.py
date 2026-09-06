#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_identity_field_origin.py -- Step-6 Phase-1: IDENTITY FIELD-ORIGIN AUDIT  (READ-ONLY)

P32 ordinal-318 is CLOSED (golden d0528d37... on the real WW_Nevely42 source via
the introspection location extractor).  We now audit the *origin* of every input
that SexAnimationInstance.get_identifier() serializes, so we can answer -- for a
FULL roster, not just ordinal 318 -- whether each field can be statically
recovered from the source/tuning XML, and whether it is ever non-default.

Ground rules (per operator):
  * These runtime-DEFAULTS are PROVEN ONLY for ordinal 318, NOT to be extrapolated
    to the other 478 rows: position xyz=0.0, facing=0.0, props empty,
    object_animation_clip_name empty, object_geometry/material_state None,
    version <= 1.  The roster census below flags ANY row that deviates via the
    keys IT actually carries -- NEVER assumes 318 defaults elsewhere.
  * Do NOT substitute custom_locations / location_constraints for the real
    location key.  Production exact key == "animation_locations"
    (<T n="animation_locations">DOUBLE_BED</T>).  The broad "locat" introspection
    stays DIAGNOSTIC ONLY and is never used to manufacture a value.
  * The loader/constructor transform (XML field -> runtime instance field) for the
    tuning-backed rows below is PROVEN by the P32 ordinal-318 golden closure
    (extract real XML -> reconstruct_identifier -> sha1 d0528d37...).
    The runtime-default rows carry constructor-default origin = "runtime instance
    default at construction", which P32 proved only for 318.

DELIVERABLE (this module):
  1. render_origin_table()  -> the 15-field PROVEN/UNKNOWN/reusable/reason table.
  2. census_rows(xml_text)  -> per-row identity-input census over ALL rows under
        <I ...><L n="animations_list">, using the SAME xml fields proven on 318
        (animation_locations scalar, actors/<animation_actors_list>, etc.).
        Emits for every row: provenance of each identity input + whether the row
        carries object/prop/geometry/material/version-ish tuning keys (so exotic
        real rows are surfaced, not assumed absent).  NEVER invents a value.
  3. main() -> orchestrates the origin table and, if an xml path is given, the
        census summary.

No Mods/saves writes.  ZERO_WRITE_TO_MODS=YES  ZERO_WRITE_TO_SAVES=YES.
Exit: 0 ok; 2 bad args/io.
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ENTRY_LIST_FIELD = "animations_list"
ACTOR_LIST_FIELDS = ("actors", "animation_actors_list")  # actors primary; fallback container
PROD_LOCATION_KEYS = ("animation_locations",)             # REAL exact production key(s)
AUTHOR_FIELDS = ("animation_author", "author")
ACTOR_FIELD_KEYS = ("actor_id", "animation_clip_name", "animation_type",
                    "animation_genders")

# ---- the 15 get_identifier inputs ----
# (name, meaning, status, recover_static, note)
FIELD_ORIGIN = [
    ("display_name", "raw animation display name", "PROVEN_TUNING", True,
     "T n='animation_raw_display_name'"),
    ("author", "author literal", "PROVEN_TUNING", True,
     "T n='animation_author'"),
    ("sex_category", "category literal (uppercased enum name)", "PROVEN_TUNING", True,
     "T n='animation_category'"),
    ("actors / actor_count", "per-actor actor_id+clip+type+genders", "PROVEN_TUNING", True,
     "L n='actors'|actor fields"),
    ("locations", "location literals (source order)", "PROVEN_TUNING", True,
     "T n='animation_locations' (scalar; exact production key)"),
    ("object_animation_clip_name", "object-slot clip", "RUNTIME_DEFAULT_318", False,
     "runtime; empty/absent on 318 -> contributes nothing"),
    ("object_geometry_state", "object geometry state", "RUNTIME_DEFAULT_318", False,
     "runtime; None on 318"),
    ("object_material_state", "object material state", "RUNTIME_DEFAULT_318", False,
     "runtime; None on 318"),
    ("actor.gender_type", "per-actor gender literal->SexGenderType name", "PROVEN_TUNING", True,
     "animation_genders literal (uppercase to MALE/FEMALE)"),
    ("actor.animation_clip_name", "per-actor clip", "PROVEN_TUNING", True,
     "animation_clip_name (per-actor)"),
    ("actor.position_offset.x/y/z", "per-actor offset (sim transform)", "RUNTIME_DEFAULT_318", False,
     "runtime 0.0 vs constructor default"),
    ("actor.angle_offset / facing_position_offset", "per-actor facing", "RUNTIME_DEFAULT_318", False,
     "runtime 0.0 vs constructor default"),
    ("prop_animation_clip_name", "per-prop clip", "RUNTIME_DEFAULT_318", False,
     "runtime; props empty on 318"),
    ("prop_geometry_state", "per-prop geometry state", "RUNTIME_DEFAULT_318", False,
     "runtime; props empty on 318"),
    ("version", "instance version", "RUNTIME_DEFAULT_318", False,
     "runtime; <=1 contributes None (318)"),
]


def _el_tag(el):
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else None


def _name(el):
    return el.get("n")


def _text(el):
    return "" if el.text is None else el.text


def _row_field(row, tag, n):
    """first descendant leaf (tag+n) under row with non-empty text, else ''."""
    for nd in row.iter():
        if _el_tag(nd) == tag and _name(nd) == n:
            v = _text(nd).strip()
            if v:
                return v
    return ""


def _locations(row):
    """REAL production location reader: scalar <T n='animation_locations'>.*</T>.
    Returns list of non-empty tokens in source order.  NO custom_locations /
    introspection substitution for production values."""
    raw = _row_field(row, "T", "animation_locations")
    if not raw:
        return []
    if "|" in raw:
        return [t for t in raw.split("|") if t]
    return [raw]


def _actors(row):
    """per-actor records reusing the exact actor schema proven on 318."""
    for lname in ACTOR_LIST_FIELDS:
        for nd in row.iter():
            if _el_tag(nd) == "L" and _name(nd) == lname:
                out = []
                for u in nd.iter():
                    if _el_tag(u) == "U":
                        out.append({
                            "actor_id": _row_field(u, "T", "actor_id"),
                            "clip": _row_field(u, "T", "animation_clip_name"),
                            "type": _row_field(u, "T", "animation_type"),
                            "genders": _row_field(u, "T", "animation_genders"),
                        })
                if out:
                    return out
    return []


def _row_keysets(row):
    """Introspective census of the row's own node vocabulary (exact @n spelling),
    so object/prop/geometry/version-ish keys a row carries are SEEN, not assumed
    absent (318 had none; others may differ)."""
    direct = {}
    containers = {}
    for nd in row.iter():
        nm = _name(nd)
        if nm is None:
            continue
        if _el_tag(nd) in ("L", "U"):
            containers[nm] = containers.get(nm, 0) + 1
        elif _el_tag(nd) == "T":
            v = _text(nd).strip()
            if v:
                direct.setdefault(nm, []).append(v)
    return {"T": direct, "container": containers}


def _bucket_keys(keyset):
    """classify a flat key list into identity-relevant groups (no fabrication)."""
    low = {k.lower(): k for k in keyset}
    obj_prop = [k for k in keyset if any(s in low[k.lower()]
                                         for s in ("animation_object", "object", "prop",
                                                   "geometry", "material", "dancer"))]
    ver = [k for k in keyset if "version" in low[k.lower()]]
    idonly = [k for k in keyset if low[k.lower()] == "id"]
    return {
        "identity_known": [k for k in keyset if
                           (k == "animation_raw_display_name") or k in AUTHOR_FIELDS
                           or k == "animation_category" or k in PROD_LOCATION_KEYS],
        "actor": [k for k in keyset if k in ACTOR_FIELD_KEYS],
        "object_or_prop_or_geo_mat": sorted(set(obj_prop)),
        "versionish": sorted(set(ver)),
        "id_only": sorted(set(idonly)),
        "other": sorted(set(k for k in keyset
                            if k not in (("animation_raw_display_name",) + AUTHOR_FIELDS
                                         + ("animation_category",) + PROD_LOCATION_KEYS
                                         + ACTOR_FIELD_KEYS + tuple(obj_prop)
                                         + tuple(ver) + tuple(idonly)))),
    }


def census_row(row):
    ks = _row_keysets(row)
    flat = list(ks["T"].keys())
    has_actor_list = any(_el_tag(u) == "L" and _name(u) in ACTOR_LIST_FIELDS
                         for u in row.iter())
    if has_actor_list:
        flat.append("actors")
    buckets = _bucket_keys(set(flat))
    actors = _actors(row)
    rec = {
        "display_name": _row_field(row, "T", "animation_raw_display_name"),
        "author": _row_field(row, "T", AUTHOR_FIELDS[0]) or _row_field(row, "T", AUTHOR_FIELDS[1]),
        "category": _row_field(row, "T", "animation_category"),
        "locations": _locations(row),
        "actors": actors,
        "key_buckets": buckets,
    }
    carries_extra = bool(buckets["object_or_prop_or_geo_mat"] or
                         [k for k in buckets["versionish"] if ks["T"].get(k)])
    rec["provenance"] = {
        "display_name": "tuning-verbatim" if rec["display_name"] else "MISSING(identity-unusable)",
        "author": "tuning-verbatim" if rec["author"] else "MISSING(identity-unusable)",
        "category": "tuning-verbatim" if rec["category"] else "MISSING(identity-unusable)",
        "locations": "tuning-verbatim" if rec["locations"] else "MISSING(identity-unusable)",
        "actors": "tuning-verbatim(%d)" % len(actors) if actors else "MISSING/EMPTY(identity-unusable)",
        "runtime_default_group": "PROVEN-318-only; NOT assumed for this row",
    }
    rec["special"] = {
        "carries_object_or_prop_or_geo_mat": buckets["object_or_prop_or_geo_mat"],
        "carries_versionish": [k for k in buckets["versionish"] if ks["T"].get(k)],
        "multi_location": len(rec["locations"]) > 1,
        "actor_count_gt_2": len(rec["actors"]) > 2,
        "location_absent": not rec["locations"],
        "gated_needs_real_row_if_extra": bool(carries_extra),
    }
    # usable == can reconstruct an identity input WITHOUT fabrication AND without
    # unexplained object/prop/geometry/material/version tuning on THIS row.
    rec["usable"] = bool(rec["display_name"] and rec["author"] and rec["category"]
                         and rec["locations"] and actors) and not carries_extra
    return rec


def census_rows(xml_text):
    root = ET.fromstring(xml_text)
    rows = []
    for l in root.iter():
        if _el_tag(l) == "L" and _name(l) == ENTRY_LIST_FIELD:
            for u in l:
                if _el_tag(u) == "U":
                    rows.append(census_row(u))
    return rows


def render_origin_table():
    L = []
    L.append("=== Step-6/Phase-1: get_identifier FIELD-ORIGIN AUDIT ===")
    L.append("mapping = xml/tuning field -> loader/constructor -> runtime instance "
             "field -> get_identifier input")
    L.append("")
    L.append("%-34s %-22s %-6s %s" % ("identity input", "status", "static", "source/note"))
    L.append("-" * 120)
    for name, meaning, status, rec, note in FIELD_ORIGIN:
        L.append("%-34s %-22s %-6s %s :: %s"
                 % (name, status, "YES" if rec else "no", meaning, note))
    n_pt = sum(1 for x in FIELD_ORIGIN if x[2] == "PROVEN_TUNING")
    n_rd = sum(1 for x in FIELD_ORIGIN if x[2] == "RUNTIME_DEFAULT_318")
    L.append("")
    L.append("PROVEN_TUNING=%d  RUNTIME_DEFAULT_318=%d  (UNKNOWN=neither: %d)"
             % (n_pt, n_rd, len(FIELD_ORIGIN) - n_pt - n_rd))
    L.append("NOTE: RUNTIME_DEFAULT_318 verdicts are PROVEN ONLY for ordinal 318; "
             "other rows' presence is decided per-row by census keys, never assumed.")
    L.append("NOTE: location exact production key = 'animation_locations' (scalar T).  "
             "custom_locations / location_constraints are NOT substitutes.")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="P32 identity field-origin audit (read-only)")
    ap.add_argument("xml", nargs="?", help="path to a WW_ANIM_XML xml text file for a "
                    "whole-roster census; omit to print only the origin table")
    args = ap.parse_args(argv)
    L = [render_origin_table()]
    if args.xml:
        xml_text = Path(args.xml).read_text(encoding="utf-8")
        try:
            rows = census_rows(xml_text)
        except ET.ParseError as e:
            print("FATAL=XML_PARSE_ERROR %s" % e, file=sys.stderr)
            return 2
        L.append("")
        L.append("=== WHOLE-ROSTER CENSUS (%d rows) ===" % len(rows))
        n_usable = sum(1 for r in rows if r["usable"])
        n_extra = sum(1 for r in rows if r["special"]["gated_needs_real_row_if_extra"])
        n_absent_loc = sum(1 for r in rows if r["special"]["location_absent"])
        n_multi_loc = sum(1 for r in rows if r["special"]["multi_location"])
        n_many_actor = sum(1 for r in rows if r["special"]["actor_count_gt_2"])
        L.append("usable(no fabrication, no un-explained extra tuning)=%d" % n_usable)
        L.append("gated_by_extra_object_prop_geo_mat_version_tuning=%d" % n_extra)
        L.append("location_absent=%d  multi_location=%d  actor_count>2=%d"
                 % (n_absent_loc, n_multi_loc, n_many_actor))
        L.append("runtime_default_group is PROVEN-318-only; a safe FULL catalog further "
                 "needs real per-row confirmation for the runtime-default group.")
        for i, r in enumerate(rows):
            prov = r["provenance"]
            bad = [k for k, v in prov.items() if v.startswith("MISSING")]
            extra = r["special"]["carries_object_or_prop_or_geo_mat"]
            ver = r["special"]["carries_versionish"]
            flags = (bad + extra + ver)
            if flags:
                L.append("  row#%d flags=%s loc=%r gender_role=%r"
                         % (i, flags, r["locations"],
                            [a["genders"] for a in r["actors"]]))
        print("\n".join(L))
        print("ROW_CENSUS=%s" % ("nomissing" if sum(
            1 for r in rows if any(v.startswith("MISSING")
                                   for k, v in r["provenance"].items()
                                   if k != "runtime_default_group")) == 0
            else "HAS_MISSING"))
        return 0
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
