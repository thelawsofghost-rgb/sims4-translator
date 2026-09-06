#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_identifier_catalog.py --- P32 Phase-2: 479-row offline animation
identifier catalog over the REAL WW_Nevely42 animation registration XML.

AUTHORITATIVE REUSE (operator mandate -- NO second identity formula)
---------------------------------------------------------------------
The SHA1/preimage identity construction is NOT re-implemented here.  This module
feeds per-row SOURCE-truth through the already-PROVEN reconstructor
(ww_p32_identifier_reconstruct.reconstruct_identifier /
build_identifier_parts), which is a byte-exact replication of the recovered
SexAnimationInstance.get_identifier semantics:
    ordered, SEPARATOR-LESS concat of
      display_name(raw) author SexCategoryType.<NAME> len(actors)
      each location_category.name
      object_animation_clip_name(if truthy) object_geometry_state(if truthy)
      object_material_state(if truthy)
      each actor: SexGenderType.<NAME>  ... then per-actor clips ... then per
      actor position_offset.x/y/z and facing_position_offset (float str; integral
      -> '0.0')
      props(geometry_state truthy -> '(clip, state)'; else clip only)
      version(only if > 1)
    then sha1(utf-8).  None / empty-string identity scalars are SKIPPED; non-str
    scalars -> str().  NO separator.  This module only derives the SEMANTIC fields
    dict from the real xml -- the hash construction is 100% the reconstructor.

default != identity-representation (operator warning, honored here)
-------------------------------------------------------------------
The tuning-container default that the loader probe decoded (animation_x/y/z/angle
/facing tuning default int 0, etc.) is NOT the identity preimage token.  The
final runtime SexAnimationInstance exposes position_offset.x/y/z and
facing_position_offset as FLOATS (0.0 integral -> '0.0'), object geometry/material
as None / empty, props as an empty runtime collection and version as <=1 for a
WW_Nevely42 just-instantiated instance.  So a missing per-row tuning offset is
NOT stamped as "0"; it flows through the SAME runtime-transform semantics the
reconstructor already verifies (float 0.0), exactly as the ordinal-318 golden
proof shows (preimage carries '0.0'*8, never '0'*8).  Nothing here fabricates a
value; every runtime-group input is the reconstructor's own transform of default
tuning, never a raw string.

Per-row model (XML -> runtime identity bridge; catalogue-side debugging target)
-------------------------------------------------------------------------------
For EVERY row under <L n='animations_list'> (ordinal = list index), the catalog
  1. reads the PROVEN-TUNING identity inputs VERBATIM from that row's real xml:
     display_name (animation_raw_display_name), author (animation_author),
     sex_category (animation_category -> enum NAME), per-actor clip + gender;
  2. location:  self.locations = the loader `_parse_sex_animation_location_types`
     of `animation_locations`.  Scalar token(s) and list-of-location-token forms
     are both read; a MISSING key is the loader empty TunableList default -> empty
     runtime list (legit, contributes nothing).  No custom_locations mix.
  3. object:  the row's object-slot identity leaves -> instance object
     clip/geometry/material; empty default is skipped (reconstructor), a NON-EMPTY
     object clip (179 real carriers) genuinely enters the hash.
  4. props:   `animation_props_list` children in SOURCE order -> ordered runtime
     prop instances; only prop_animation_clip_name / prop_geometry_state are
     identity (per runtime proof).  prop_id/type/guids never enter; the container's
     mere presence is never a gate.
  5. actor gender -> loader default_gender=True stored SexGenderType NAME (MALE /
     FEMALE proven); an UNKNOWN raw token fails the row closed with the exact
     token (no coercion).  Offsets use the reconstructor 0.0 float transform.
  Then the identifier is computed with the AUTHORITATIVE reconstructor -> VALID,
  or fail-closed to UNKNOWN with an exact reason.  Nothing is guessed/skipped/
  demoted.

Ran only on the Windows box (the real source package is Windows-only; Linux has
no byte copy).  Writes ONLY under --out-dir:
    p32_identifier_catalog.csv
    p32_identifier_catalog_report.txt
  ZERO write to Mods / saves / the WW ts4script / source / catalog-suppress lists.

Exit: 0 VERDICT=GO (VALID=0-unknown AND gold PASS); 2 STOP -- UNKNOWN>0 catalog +
report still emitted; 3 FAIL -- source gate / XML / golden FAIL;
(per E, aggregating UNKNOWN by reason category to stdout, full list in report).
"""
import argparse
import csv
import hashlib
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

WW_ANIM_XML = 0x7DF2169C
EXPECT_SOURCE_SHA_PREFIX = "cd0093f2"          # operator-pinned (must start-match)
EXPECT_SOURCE_SHA = ("cd0093f2ec4b896121fa465672584c12"
                     "384465b631c1d9128fe97d360b87d416")
EXPECT_INSTANCE = 0x43F3438A94EDEB2B            # operator-pinned WW_ANIM_XML inst
EXPECT_ENTRY_COUNT = 479                        # operator-pinned roster size
GOLDEN_ORDINAL = 318
GOLDEN_SHA1_318 = "d0528d3795ca42c60ca5a9eb6bad8658ea74e4b5"

ENTRY_LIST_FIELD = "animations_list"
ACTOR_LIST_FIELDS = ("actors", "animation_actors_list")
PROP_LIST_CONTAINERS = ("animation_props_list", "props")
DISPLAY_FIELDS = ("animation_raw_display_name", "raw_display_name")
AUTHOR_FIELDS = ("animation_author", "author")
CATEGORY_FIELDS = ("animation_category", "category")

# authoritative loader-sourced identity-token keys (mirror ww_p32_loader_origin_*
# exact source vocabulary; NEVER whole-subtree fuzzy bucket-matching).
ACTOR_FIELD_KEYS = ("actor_id", "animation_clip_name", "animation_type",
                    "animation_genders", "animation_pref_gender")
OBJECT_KEYS = ("object_animation_clip_name", "object_geometry_state",
               "object_material_state")
PROP_KEYS = ("prop_animation_clip_name", "prop_geometry_state")
ACTOR_OFFSET_KEYS = ("animation_x_offset", "animation_y_offset",
                     "animation_z_offset", "animation_angle_offset",
                     "animation_facing_offset")
VERSION_KEY = "animation_version"

# column headers (operator-mandated minimum + translation-context extras)
CSV_COLUMNS = [
    "source_instance", "ordinal", "raw_display_name", "author", "identifier",
    "validation_status", "identity_input_summary", "status_reason",
    # translation-context extras (NEVER used in the hash)
    "sex_category", "actor_count", "locations", "tags", "stage_name",
    "actor_genders", "actor_clips", "object_animation_clip_name",
    "prop_animation_clip_names",
]
_EXTRA_COLS_NO_HASH = set(CSV_COLUMNS) - {
    "source_instance", "ordinal", "raw_display_name", "author", "identifier",
    "validation_status", "identity_input_summary",
}

# ---------------------------------------------------------------------------
# authoritative reconstructor (reused AS-IS) -- the sha1 construction is NOT
# re-implemented; only the per-row SEMANTIC fields are derived here.
# ---------------------------------------------------------------------------
import ww_p32_identifier_reconstruct as _rec  # noqa: E402

# authoritative reconstructor symbols (re-export for tests; object identity is
# the SAME function used by the golden ordinal-318 proof)
reconstruct_identifier = _rec.reconstruct_identifier
build_identifier_parts = _rec.build_identifier_parts


def _el_tag(el):
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else None


def _name(el):
    return el.get("n")


def _text(el):
    return "" if el.text is None else el.text


def sha256(p: Path):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _read_index(pkg: Path):
    sys.path.insert(0, str(SCRIPT_DIR.parent / "src"))
    from dbpf_fast import safe_parse  # noqa
    idx, err = safe_parse(str(pkg))
    if err or idx is None:
        raise RuntimeError("safe_parse failed: %s" % err)
    return idx


def _read_body(pkg: Path, entry) -> bytes:
    off = entry.offset & 0x7FFFFFFF
    size = entry.size & 0x7FFFFFFF
    with open(pkg, "rb") as fh:
        fh.seek(off)
        return fh.read(size)


def _decompress(body: bytes) -> bytes:
    if body[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        try:
            return __import__("zlib").decompress(body)
        except Exception:
            return body
    return body


# ---------------------------------------------------------------------------
# row readers (mirror the ordinal-318 source fixture; identical production fields)
# ---------------------------------------------------------------------------
def _row_field(row, tag, n):
    for nd in row.iter():
        if _el_tag(nd) == tag and _name(nd) == n:
            v = _text(nd).strip()
            if v:
                return v
    return ""


def _single_text(row, names, tag="T"):
    """first descendant (tag, one of names) with non-empty text; multi -> join.|"""
    hits = []
    for nd in row.iter():
        if _el_tag(nd) == tag and _name(nd) in names:
            v = _text(nd).strip()
            if v:
                hits.append(v)
    return "|".join(_uniq(hits)) if hits else ""


def _uniq(seq):
    out = []
    for x in seq:
        if x not in out:
            out.append(x)
    return out


def _locations(row):
    """Semantic self.locations = ordered list of location-CATEGORY NAME strings
    exactly as the loader `_parse_sex_animation_location_types` produces them for
    the runtime `SexAnimationInstance.locations`.  The identity only uses
    `[location_category.name for loc in self.locations]`.

    Grounding (no guessing): the authoritative loader-sourced tuning field is
    `animation_locations` (a TunableList of LocationType).  Its XML renders as
    either a plain scalar <T> of location-token text (ordinal-318 fixture proves
    `DOUBLE_BED` feeds a single location), or a list container of location tokens
    under `animation_locations`.  A MISSING `animation_locations` key is the
    loader's empty TunableList default -> runtime self.locations == [] (verified
    empty semantics; contributes nothing to the hash).  We do NOT fabricate
    `custom_locations`/other container mixes into self.locations unless real
    loader dataflow proves it -- the ground-truth census decides that.

    Returns (locations_list, hit_form) where hit_form in {"scalar", "list",
    "missing"} for the census.  Presence of a REAL token is required to yield a
    non-empty list; empty/blob containers yield [] (form recorded)."""
    # scalar plain token(s): exact loader-sourced scalar field
    raw = _row_field(row, "T", "animation_locations")
    if raw:
        return _split_multi(raw), "scalar"
    # list container under the loader field
    for nd in row.iter():
        if _el_tag(nd) != "L" or _name(nd) != "animation_locations":
            continue
        children = [c for c in list(nd)]
        struct = any(_el_tag(c) in ("U", "L") for c in children)
        if not struct:
            # plain list of location-name leaves directly under the container
            toks = []
            for c in children:
                if _el_tag(c) in ("T", "E") and _text(c).strip():
                    toks.extend(_split_multi(_text(c).strip()))
            return toks, "list"
        # structured records only, no direct name leaves: cannot prove the
        # runtime location-category names -> not empty; real-structure census
        # must resolve (never under-read to empty).
        return [], "unmapped-struct"
    return [], "missing"


def _split_multi(raw):
    out = []
    for sep in ("|", ",", ";"):
        raw = raw.replace(sep, "|")
    for t in raw.split("|"):
        t = t.strip()
        if t:
            out.append(t)
    return out


def _actors(row):
    """Per-actor records in source order under the loader actor list container
    (`animation_actors_list` | `actors`), reading ONLY each actor record's own
    direct-tuned leaves (actor_id, clip, animation_type, animation_genders,
    animation_pref_gender).  NO whole-subtree fuzzy match; a bare actor <U> has
    these leaves."""
    for lname in ACTOR_LIST_FIELDS:
        for nd in row.iter():
            if _el_tag(nd) == "L" and _name(nd) == lname:
                out = []
                for u in nd:
                    if _el_tag(u) != "U":
                        continue
                    direct = {}
                    for t in u:
                        if _el_tag(t) == "T" and _name(t):
                            direct.setdefault(_name(t), _text(t).strip())
                    out.append({
                        "actor_id": direct.get("actor_id", ""),
                        "clip": direct.get("animation_clip_name", ""),
                        "type": direct.get("animation_type", ""),
                        "genders": direct.get("animation_genders", ""),
                        "pref_gender": direct.get("animation_pref_gender", ""),
                    })
                if out:
                    return out
    return []


def _props(row):
    """Ordered runtime prop records from the loader prop-list container
    (`animation_props_list` | `props`), source order == runtime insertion order ==
    get_props() order.  Each runtime prop only needs prop_animation_clip_name +
    prop_geometry_state (its OWN direct leaves).  prop_id/prop_type/prop_guids and
    any other prop-tag decorations never enter the identifier.  Presence of an
    (even empty) prop-list container is NORMAL and is never a risk."""
    for lname in PROP_LIST_CONTAINERS:
        for nd in row.iter():
            if _el_tag(nd) == "L" and _name(nd) == lname:
                out = []
                for u in nd:
                    if _el_tag(u) != "U":
                        continue
                    direct = {}
                    for t in u:
                        if _el_tag(t) == "T" and _name(t):
                            direct.setdefault(_name(t), _text(t).strip())
                    out.append({
                        "ordinal": len(out),
                        "animation_clip_name": direct.get("prop_animation_clip_name", ""),
                        "geometry_state": direct.get("prop_geometry_state", ""),
                    })
                return out  # first matching prop-list container is authoritative
    return []


def _object_slot(row):
    """The row's single object slot's identity tuning: object_animation_clip_name /
    object_geometry_state / object_material_state.  Origin census treats these as
    the ROW's own object-slot leaves (optionally wrapped under an explicit
    object/override/tuning value container).  Missing keys -> proven default
    (object clip '' / geometry '' / material '') which the reconstructor skips;
    NON-EMPTY object clip (179 real carriers) genuinely enters the hash.  We read
    only the loader-sourced object-slot scope: the row's DIRECT leaves plus the
    direct children of an explicit object/override/tuning wrapper -- never an
    unrelated descendant (actor/prop) record's keys."""
    direct = {}
    scopes = []
    # row's direct T leaves only
    for nd in list(row):
        if _el_tag(nd) == "T" and _name(nd) in OBJECT_KEYS:
            scopes.append(nd)
    # an explicit wrapper value-container (direct child) named object/override/tuning
    for nd in list(row):
        if _el_tag(nd) not in ("U", "L"):
            continue
        nm = (_name(nd) or "").lower()
        if any(w in nm for w in ("animation_object", "override", "tuning", "object")):
            for t in nd:
                if _el_tag(t) == "T" and _name(t) in OBJECT_KEYS:
                    scopes.append(t)
    for nd in scopes:
        direct.setdefault(_name(nd), _text(nd).strip())
    return {
        "animation_clip_name": direct.get("object_animation_clip_name", ""),
        "geometry_state": direct.get("object_geometry_state", ""),
        "material_state": direct.get("object_material_state", ""),
    }


# loader-mirror: `animation_genders` name/alias -> canonical runtime SexGenderType
# NAME.  The runtime get_identifier calls actor.get_gender_type(default_gender=True)
# which returns the STORED self.gender_type (real evidence: MALE->MALE, FEMALE->
# FEMALE for ordinal 318).  raw tuning literal is fed through the loader's
# get_sex_gender_type_by_name to the enum; we resolve by exact canonical NAME and
# clear aliases only, NEVER coercion of an unknown token.  A matched token's value
# is the SexGenderType NAME the reconstructor renders as SexGenderType.<NAME>.
_GENDER_NAME_TO_RUNTIME = {
    "MALE": "MALE", "FEMALE": "FEMALE",
    "TRANS_MALE": "TRANS_MALE", "TRANS_FEMALE": "TRANS_FEMALE",
    "BOTH": "BOTH",  # PROVEN 2026-09-06 from real WW sex_gender.pyc bytecode:
    #                    'BOTH' upper().strip() in SexGenderType ->
    #                    SexGenderType['BOTH'] -> SexGenderType.BOTH
}


def _runtime_gender(raw):
    """Map one animation_genders literal -> canonical SexGenderType NAME, or
    'UNKNOWN' when the literal has no authoritative match.  Mirrors the loader
    get_sex_gender_type_by_name for default_gender=True (name already the enum
    NAME).  Unknown token is returned verbatim (never guessed)."""
    if not raw:
        return "UNKNOWN"
    up = raw.strip().upper()
    if up in _GENDER_NAME_TO_RUNTIME:
        return _GENDER_NAME_TO_RUNTIME[up]
    # tolerate underscore/space/hyphen differences to a known NAME token only
    norm = up.replace("_", " ").replace("-", " ").replace(",", " ").split()
    if len(norm) == 1 and norm[0] in _GENDER_NAME_TO_RUNTIME:
        return _GENDER_NAME_TO_RUNTIME[norm[0]]
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# per-row semantic bridge -> reconstructor fields (the XML -> runtime identity
# bridge; catalogue-side debugging target)
# ---------------------------------------------------------------------------
def build_row(row, ordinal):
    """Map ONE real <U> row (ET Element, or an XML string that is parsed) -> a
    (rowrec) whose `fields_proven` drives the authoritative reconstructor.

    Semantics follow the REAL runtime get_identifier bridge town-by-town:
      * location:  self.locations = loader `_parse_sex_animation_location_types`
                   of `animation_locations`; MISSING key -> empty runtime list
                   (valid; contributes nothing).
      * object:    the row's single object-slot leaves -> instance object keys;
                   empty/absent defaults are skipped by the reconstructor; a
                   NON-EMPTY object clip genuinely enters the hash (179 real
                   carriers).
      * props:     `animation_props_list` children in SOURCE order -> runtime
                   prop instances (insertion order == get_props order); only
                   prop_animation_clip_name / prop_geometry_state are identity
                   (per runtime proof); prop_id/type/guids never enter.  The
                   container's presence is never a gate.
      * actor:     per-actor gender via loader default_gender=True (stored
                   self.gender_type).  Unknown token -> UNKNOWN + exact token.
                   Runtime offsets use the reconstructor's 0.0 float transform.
    Never fabricates/demotes/skips an identity component."""
    if isinstance(row, str):
        row = ET.fromstring(row)
    rec = {
        "ordinal": ordinal,
        "raw_display_name": "",
        "author": "",
        "sex_category_e": "",
        "location_literals": [],
        "location_form": "missing",
        "actor_rows": [],
        "prop_rows": [],
        "object_slot": {},
        "tags": "",
        "stage_name": "",
        "status": "UNKNOWN",
        "status_reason": "",
        "fields_proven": {},
    }
    rec["raw_display_name"] = _single_text(row, DISPLAY_FIELDS)
    rec["author"] = _single_text(row, AUTHOR_FIELDS)
    cat_e = _single_text(row, CATEGORY_FIELDS)
    rec["sex_category_e"] = cat_e.upper() if cat_e else ""
    rec["tags"] = _single_text(row, ("animation_tags", "tags"))
    rec["stage_name"] = _single_text(row, ("animation_stage_name", "stage_name"))
    loc, loc_form = _locations(row)
    rec["location_literals"] = loc
    rec["location_form"] = loc_form

    # location semantics (C): a MISSING `animation_locations` is the loader empty
    # TunableList default -> runtime self.locations == [] (legit, no hash token).
    # A container that exists ONLY as structured records we can't map is NOT
    # silently empty -- real-structure census must decide -> fail row closed.
    if loc_form == "unmapped-struct":
        rec["status"] = "UNKNOWN"
        rec["status_reason"] = "loader_location_uninterpretable=" \
                                "animation_locations is a structured record list " \
                                "(no plain name token) -- real loader shape requested"
        return rec

    for i, a in enumerate(_actors(row)):
        gr = a.get("genders", "")
        raw_note = gr
        rec["actor_rows"].append({
            "actor_ordinal": i,
            "actor_id": a.get("actor_id", ""),
            "clip": a.get("clip", ""),
            "type": a.get("type", ""),
            "pref_gender": a.get("pref_gender", ""),
            "genders_raw": raw_note,
            "gender_runtime": _runtime_gender(raw_note),
        })
    rec["prop_rows"] = _props(row)
    rec["object_slot"] = _object_slot(row)

    # ---- required core identity gate (PROVEN-TUNING, no fabrication) ----
    missing = []
    if not rec["raw_display_name"]:
        missing.append("display_name")
    if not rec["author"]:
        missing.append("author")
    if not rec["sex_category_e"]:
        missing.append("sex_category")
    if not rec["actor_rows"]:
        missing.append("actors")
    for ar in rec["actor_rows"]:
        if ar["gender_runtime"] == "UNKNOWN":
            missing.append("actor%d.gender_runtime=UNKNOWN(no coercion) token=%r"
                           % (ar["actor_ordinal"], ar["genders_raw"]))
        if not ar["clip"]:
            missing.append("actor%d.clip=empty" % ar["actor_ordinal"])
    # location: MISSING is legal -> empty; only a location row that hits an
    # unmappable non-empty form is flagged (see placement below).

    if missing:
        rec["status"] = "UNKNOWN"
        rec["status_reason"] = "missing_identity_required=" + ",".join(missing)
        return rec

    # ---- assemble the loader-bridge fields (VALID unless non-empty loc weird) ----
    fields, warn = _fields_for_reconstruct(rec)
    if warn:
        rec["status"] = "UNKNOWN"
        rec["status_reason"] = warn
        return rec
    rec["fields_proven"] = fields
    rec["status"] = "VALID"
    return rec


def _obj_bool_or_none(v):
    """Empty string -> None (skipped by the reconstructor); else the string."""
    v = (v or "").strip()
    return v if v != "" else None


def _fields_for_reconstruct(rec):
    """Build the reconstructor `fields` dict from a rowrec, mirroring the loader
    -> runtime -> get_identifier bridge for the identity inputs.  Returns
    (fields, None) on success, or (None, reason) for a genuine per-row non-empty
    value the loader bridge cannot place (never guessed).  Actor offsets/version
    are the reconstructor runtime transform (0.0 / version<=1), not raw tuning
    text.  Props carry ONLY clip + geometry_state; object carries clip/geom/
    material with empty -> None.  Locations: empty runtime list is legal and is
    used (contributes nothing); a NON-EMPTY location list is committed verbatim."""
    actors = []
    for ar in rec["actor_rows"]:
        actors.append({
            "gender_type": ar["gender_runtime"],
            "animation_clip_name": ar["clip"] or "",
            "position_offset": {"x": 0.0, "y": 0.0, "z": 0.0},
            "facing_position_offset": 0.0,
        })

    props = []
    for p in rec.get("prop_rows", []):
        clip = (p.get("animation_clip_name") or "").strip()
        geom = (p.get("geometry_state") or "").strip()
        if not clip and not geom:
            # empty prop record would contribute nothing; still allow in case of
            # structural variety but leave it as a no-op identity element.
            props.append({"animation_clip_name": "", "geometry_state": ""})
        else:
            props.append({"animation_clip_name": clip, "geometry_state": geom})

    obj = rec.get("object_slot") or {}
    oclip = (obj.get("animation_clip_name") or "").strip()
    ogeom = _obj_bool_or_none(obj.get("geometry_state"))
    omat = _obj_bool_or_none(obj.get("material_state"))

    fields = {
        "display_name": rec["raw_display_name"],
        "author": rec["author"],
        "sex_category": rec["sex_category_e"],
        "actors": actors,
        "locations": list(rec["location_literals"]),
        "object_animation_clip_name": oclip,
        "object_geometry_state": ogeom,
        "object_material_state": omat,
        "props": props,
        "version": 1,
    }
    return fields, None


def _summary(rec, fields):
    """Short deterministic identity-input summary for CSV/report (does NOT affect
    the hash: identifier is computed purely from fields via the reconstructor)."""
    loc = "|".join(rec["location_literals"]) or "-"
    gen = ",".join(a["gender_runtime"] for a in rec["actor_rows"]) or "-"
    clips = ",".join(a["clip"] for a in rec["actor_rows"]) or "-"
    return "author=%s;cat=%s;loc=%s;nactors=%d;genders=%s;clips=%s" % (
        rec["author"], rec["sex_category_e"], loc, len(rec["actor_rows"]), gen, clips)


# ---------------------------------------------------------------------------
# top-level enumeration
# ---------------------------------------------------------------------------
def load_roster(pkg: Path, accept_any_sha=False):
    """Read+gate the source package, return (sha, instance, n_entries, rows=
    list of <U> elements under animations_list).  Fail-closed on sha/instance/
    count gates when not bypassed (test-only bypass via accept_any_sha for the
    synthetic offline suite; the authoritative pin is enforced on the live run)."""
    sha = sha256(pkg)
    if not accept_any_sha and not sha.startswith(EXPECT_SOURCE_SHA_PREFIX):
        raise RuntimeError("source sha mismatch (%s...) vs pinned cd0093f2...; use real source" % sha[:8])
    if not accept_any_sha and sha != EXPECT_SOURCE_SHA:
        raise RuntimeError("source sha != authoritative cd0093f2...b87d416 (%s...)" % sha[:16])
    idx = _read_index(pkg)
    ww = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    if len(ww) != 1:
        raise RuntimeError("WW_ANIM_XML count != 1 (got %d)" % len(ww))
    ww_e = ww[0]
    if ww_e.instance_id != EXPECT_INSTANCE:
        raise RuntimeError("WW_ANIM_XML instance 0x%016X != pinned 0x%016X"
                           % (ww_e.instance_id, EXPECT_INSTANCE))
    body = _decompress(_read_body(pkg, ww_e))
    text = body.decode("utf-8", errors="replace")
    root = ET.fromstring(text)
    lists = [n for n in root.iter()
             if _el_tag(n) == "L" and _name(n) == ENTRY_LIST_FIELD]
    if len(lists) != 1:
        raise RuntimeError("<L animations_list> count=%d" % len(lists))
    us = [c for c in list(lists[0]) if _el_tag(c) == "U"]
    if not accept_any_sha and len(us) != EXPECT_ENTRY_COUNT:
        raise RuntimeError("ENTRY_COUNT=%d != pinned %d" % (len(us), EXPECT_ENTRY_COUNT))
    return sha, "0x%016X" % ww_e.instance_id, len(us), us


def build_catalog(pkg: Path, accept_any_sha=False, out_rows=None):
    """Enumerate all rows -> list of rowrecs (Unicode).  Optionally consume into
    out_rows (list).  Each rowrec is fully decoded for the private summary."""
    sha, inst, n, us = load_roster(pkg, accept_any_sha)
    recs = []
    for ordinal, row in enumerate(us):
        if out_rows is not None:
            out_rows.append((ordinal, row))   # expose raw rows for consumers/tests
        r = build_row(row, ordinal)
        f = r.get("fields_proven")
        if r["status"] == "VALID" and f is not None:
            res = reconstruct_identifier(f)
            r["identifier"] = res["sha1"]
            r["preimage"] = res["preimage"]
        else:
            r["identifier"] = ""
            r["preimage"] = ""
        r["summary"] = _summary(r, f)
        recs.append(r)
    return {"source_sha256": sha, "source_instance": inst, "entry_count": n,
            "rows": recs}


def _row_csv(rec):
    g = rec.get("actor_rows", [])
    f = rec.get("fields_proven") or {}
    clips = ",".join(a["clip"] for a in g)
    props = f.get("props") or []
    prop_clip_names = ",".join(
        (p.get("animation_clip_name") or "") for p in props)
    return {
        "source_instance": None,  # filled below (no per-row package record kept)
        "ordinal": rec["ordinal"],
        "raw_display_name": rec.get("raw_display_name", ""),
        "author": rec.get("author", ""),
        "identifier": rec.get("identifier", ""),
        "validation_status": rec.get("status", "UNKNOWN"),
        "identity_input_summary": rec.get("summary", ""),
        "status_reason": rec.get("status_reason", ""),
        "sex_category": rec.get("sex_category_e", ""),
        "actor_count": len(g),
        "locations": "|".join(rec.get("location_literals", [])) or "",
        "tags": rec.get("tags", ""),
        "stage_name": rec.get("stage_name", ""),
        "actor_genders": ",".join(a["gender_runtime"] for a in g),
        "actor_clips": clips,
        "object_animation_clip_name": (f or {}).get("object_animation_clip_name", "")
                                      or "",
        "prop_animation_clip_names": prop_clip_names,
    }


def write_csv(out_path: Path, catalog, source_instance):
    cols = CSV_COLUMNS
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for rec in catalog["rows"]:
            r = _row_csv(rec)
            r["source_instance"] = source_instance
            w.writerow(r)


def _dup_groups(rows):
    groups = {}
    for r in rows:
        if r.get("identifier"):
            groups.setdefault(r["identifier"], []).append(r["ordinal"])
    dups = {h: v for h, v in groups.items() if len(v) > 1}
    return groups, dups


def _reason_category(reason):
    """Map a per-row UNKNOWN status_reason to a coarse category + detail bucket
    for the top-line aggregate (E).  Deterministic; full per-row reasons stay in
    the report's orphan list."""
    if not reason:
        return "none"
    if reason.startswith("missing_identity_required="):
        return "missing_identity_required"
    if reason.startswith("gated_"):
        return reason.split("=", 1)[0]
    return reason.split("=", 1)[0]


def _reason_categories(unknown_rows):
    """collections.Counter-like dict of category -> [count, sample_reason]."""
    out = {}
    for r in unknown_rows:
        cat = _reason_category(r.get("status_reason", ""))
        e = out.setdefault(cat, [0, ""])
        e[0] += 1
        if not e[1]:
            e[1] = r.get("status_reason", "")
    return out


def render_report(catalog, golden_ok=True, verdict="STOP"):
    L = []
    sha = catalog["source_sha256"]
    rows = catalog["rows"]
    L.append("SOURCE_SHA256=%s" % sha)
    L.append("SOURCE_INSTANCE=%s" % catalog["source_instance"])
    L.append("ENTRY_COUNT=%d" % catalog["entry_count"])
    L.append("CATALOG_ROWS=%d" % len(rows))
    valid = [r for r in rows if r.get("status") == "VALID"]
    unknown = [r for r in rows if r.get("status") == "UNKNOWN"]
    L.append("VALID_COUNT=%d" % len(valid))
    L.append("UNKNOWN_COUNT=%d" % len(unknown))
    groups, dups = _dup_groups(rows)
    L.append("UNIQUE_IDENTIFIER_COUNT=%d" % len(groups))
    L.append("DUPLICATE_IDENTIFIER_COUNT=%d"
             % sum(len(v) for v in dups.values()))
    L.append("DUPLICATE_IDENTIFIER_GROUPS=%d" % len(dups))
    L.append("")
    if dups:
        L.append("DUPLICATES (no auto-dedup; ordinal/name/identifier):")
        for h, ords in sorted(dups.items(), key=lambda kv: kv[1]):
            for o in sorted(ords):
                nm = next((r["raw_display_name"] for r in rows
                           if r["ordinal"] == o and r.get("identifier") == h), "-")
                L.append("  ordinal=%d name=%r identifier=%s" % (o, nm, h))
        L.append("")
    # E: reason-category aggregate on top (then full ordinal list below).
    L.append("UNKNOWN_REASON_CATEGORIES:")
    cats = _reason_categories(unknown)
    if cats:
        for cat, (cnt, sample) in sorted(cats.items(), key=lambda kv: (-kv[1][0], kv[0])):
            L.append("  %-30s : %d   (sample: %s)" % (cat, cnt, sample))
    else:
        L.append("  (none)")
    L.append("")
    if unknown:
        L.append("AFFECTED_ORPHANS_FULL (ordinal | status_reason) -- %d rows:" % len(unknown))
        for r in unknown:
            L.append("  ordinal=%d | %s" % (r["ordinal"], r.get("status_reason", "")))
        L.append("")
    # deterministic structural sample (op lists 5-10 structurally-different VALID)
    L.append("STRUCTURAL_SAMPLE:")
    for line in _structural_sample(catalog, count=8):
        L.append("  " + line)
    L.append("")
    L.append("ORD318_GOLDEN=%s" % ("PASS" if golden_ok else "FAIL"))
    L.append("FULL_CATALOG_SAFE=%s" % ("YES" if (golden_ok and not unknown) else "NO"))
    L.append("VERDICT=%s" % verdict)
    return "\n".join(L)


def _structural_sample(catalog, count=8):
    """Deterministically select up to `count` VALID rows differing in structure:
    by actor_count, sex_category, location token count, clip cardinality, extra
    keys.  Pure function over catalog rows (sorted by ordinal) so the selection is
    reproducible offline AND on Windows."""
    rows = [r for r in catalog["rows"] if r.get("status") == "VALID"]
    rows.sort(key=lambda r: r["ordinal"])
    buckets = []
    seen_sig = set()
    for r in rows:
        gs = tuple(sorted(a["gender_runtime"] for a in r["actor_rows"][:2])) \
            if r.get("actor_rows") else ()
        sig = (len(r.get("actor_rows", [])), r.get("sex_category_e", ""), gs,
               len(r.get("location_literals", [])),
               len(r.get("prop_rows", [])),
               bool((r.get("object_slot") or {}).get("animation_clip_name")))
        if sig not in seen_sig:
            seen_sig.add(sig)
            buckets.append(r)
        if len(buckets) >= count:
            break
    out = []
    for r in buckets:
        out.append("ordinal=%d name=%r actor_count=%d cat=%s loc=%s id=%s summary=%s"
                   % (r["ordinal"], r.get("raw_display_name", ""),
                      len(r.get("actor_rows", [])), r.get("sex_category_e", ""),
                      "|".join(r.get("location_literals", [])) or "-",
                      r.get("identifier", ""), r.get("summary", "")))
    return out


def _golden(catalog):
    for r in catalog["rows"]:
        if r["ordinal"] == GOLDEN_ORDINAL:
            ok = (r.get("status") == "VALID"
                  and r.get("identifier") == GOLDEN_SHA1_318)
            return ok, r
    return False, None


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pkg", help="WW_Nevely42_Animations.package path")
    ap.add_argument("--out-dir", default="output/p32")
    ap.add_argument("--accept-any-sha", action="store_true",
                    help="test-only: bypass the authoritative source-sha pin "
                         "(synthetic offline fixtures only; NEVER set on the live "
                         "windows command)")
    return ap.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    pkg = Path(a.pkg)
    if not pkg.is_file():
        print("FATAL=PACKAGE_MISSING %s" % pkg, file=sys.stderr)
        return 2
    try:
        # require FULL entries equal 479 on live run (pin).  gate before build so
        # the authoritative roster-count gate is enforced by the ps1 too.
        catalog = build_catalog(pkg, accept_any_sha=a.accept_any_sha)
    except ET.ParseError as e:
        print("FATAL=XML_PARSE_ERROR %s" % e, file=sys.stderr)
        return 3
    except Exception as e:
        print("FATAL=SOURCE_GATE_FAIL %s" % e, file=sys.stderr)
        return 3

    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "p32_identifier_catalog.csv"
    rpt_path = out_dir / "p32_identifier_catalog_report.txt"
    write_csv(csv_path, catalog, catalog["source_instance"])

    g_ok, g_row = _golden(catalog)
    valid = [r for r in catalog["rows"] if r.get("status") == "VALID"]
    unknown = [r for r in catalog["rows"] if r.get("status") == "UNKNOWN"]
    full_safe = g_ok and not unknown
    verdict = "GO" if full_safe else "STOP"
    report = render_report(catalog, golden_ok=g_ok, verdict=verdict)
    rpt_path.write_text(report + "\n", encoding="utf-8")

    print(report)
    print("")
    print("WROTE %s" % csv_path)
    print("WROTE %s" % rpt_path)
    # machine lines the ps1 / operator consume (E: aggregate, no 479-row dump)
    print("VALID_COUNT=%d" % len(valid))
    print("UNKNOWN_COUNT=%d" % len(unknown))
    if unknown:
        print("UNKNOWN_REASON_CATEGORIES:")
        for cat, (cnt, sample) in sorted(
                _reason_categories(unknown).items(),
                key=lambda kv: (-kv[1][0], kv[0])):
            print("  %s : %d" % (cat, cnt))
    if not g_ok:
        print("ORD318_GOLDEN=FAIL")
        print("VERDICT=STOP")
        return 3
    if unknown:
        print("ORD318_GOLDEN=PASS")
        print("FULL_CATALOG_SAFE=NO")
        print("VERDICT=STOP")
        return 2  # catalog+report emitted as operator requires; STOP (not safe)
    print("ORD318_GOLDEN=PASS")
    print("FULL_CATALOG_SAFE=YES")
    print("VERDICT=GO")
    return 0


if __name__ == "__main__":
    sys.exit(main())
