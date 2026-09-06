#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_catalog_groundtruth.py --- READ-ONLY real-XML ground-truth census that
closes the P32 catalogue location-form + gender-token bridge (sections B/C) with
REAL evidence, plus the ordinal-318 bridge portrait (section D).

Never touches source / Mods / saves; ZERO write to the package.  Output small
aggregates (NOT a 479-line dump) + the ordinal-318 bridge inventory, so the
catalogue's identity bridge is anchored on the actual WW_Nevely42 row shapes.

What it reports (all REAL, no guessing):
  LOCATION_FORM  : scalar = <T animation_locations="..">; list = <L
                   animation_locations> of direct name leaves; missing = no key
                   (loader empty default -> runtime []); unmapped-struct = the
                   <L> only contains structured <U>/<L> records whose runtime
                   location names we can't yet prove from the loader.
  GENDER_TOKENS  : distinct raw `animation_genders` per-actor values + counts, so
                   the mapping table in the catalogue is extended only by REAL
                   tokens (MALE/FEMALE proven; anything not exactly mapped must
                   be added with evidence, never coerced).
  PREF_TOKENS    : distinct `animation_pref_gender` values (if present).
  PROPS          : rows carrying a prop-list container; rows yielding >=1 real
                   prop clip/state; distinct prop-leaf key names.
  OBJECT_CLIP    : rows carrying a non-empty object_animation_clip_name (asc / 479
                   expected ~179) + object geom/material none (expected 0).
  ACTORS         : distinct actor counts per row + distinct actor clip counts.
  ORD318         : full identity-input portrait of ordinal 318 (name/author/cat/
                   locations+form/actor genders+clips/props/object) => the golden
                   bridge target for section D.

Exit 0 on success; 3 on any source/parse gate.  Run on Windows against the real
WW_Nevely42_Animations.package:
  python scripts\\ww_p32_catalog_groundtruth.py "WW_Nevely42_Animations.package"
      [--out output/p32/p32_catalog_groundtruth.txt]
Operator pastes the result back; the catalogue reads/maps are then finalised for
whatever distinct token/forms actually occur (no fabrication either way).
"""
import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import ww_p32_identifier_catalog as C  # noqa: E402

GOLD = C.GOLDEN_ORDINAL


def _collect(root_us, text_lines):
    loc_form = {"scalar": 0, "list": 0, "unmapped-struct": 0, "missing": 0}
    gender = {}
    pref = {}
    prop_rows_present = 0
    prop_rows_with_identity = 0
    prop_leaf_keys = {}
    object_clip = 0
    object_geom = 0
    object_mat = 0
    actor_count = {}
    actor_clip_dist = {}
    per_row_loc = {}
    trans = {"display": 0, "author": 0, "category": 0}
    import ww_p32_identifier_catalog as _C
    for ei, u in enumerate(root_us):
        loc, f = _C._locations(u)
        loc_form[f] += 1
        per_row_loc.setdefault((f, len(loc)), 0)
        per_row_loc[(f, len(loc))] += 1
        for a in _C._actors(u):
            gr = (a.get("genders") or "").strip()
            gender[gr] = gender.get(gr, 0) + 1
            pg = (a.get("pref_gender") or "").strip()
            pref[pg] = pref.get(pg, 0) + 1
        pr = _C._props(u)
        if pr:
            prop_rows_present += 1
            if any((p.get("animation_clip_name") or "") or (p.get("geometry_state") or "")
                   for p in pr):
                prop_rows_with_identity += 1
            for p in pr:
                for k in p:
                    prop_leaf_keys[k] = prop_leaf_keys.get(k, 0) + 1
        obj = _C._object_slot(u)
        if (obj.get("animation_clip_name") or "").strip():
            object_clip += 1
        if (obj.get("geometry_state") or "").strip():
            object_geom += 1
        if (obj.get("material_state") or "").strip():
            object_mat += 1
        nact = len(_C._actors(u))
        actor_count[nact] = actor_count.get(nact, 0) + 1
        clips = tuple(sorted(a.get("clip") for a in _C._actors(u) if (a.get("clip") or "")))
        actor_clip_dist[len(clips)] = actor_clip_dist.get(len(clips), 0) + 1

        disp = _C._single_text(u, C.DISPLAY_FIELDS)
        au = _C._single_text(u, C.AUTHOR_FIELDS)
        cat = _C._single_text(u, C.CATEGORY_FIELDS)
        if disp:
            trans["display"] += 1
        if au:
            trans["author"] += 1
        if cat:
            trans["category"] += 1

    text_lines.append("== P32 catalog real-XML ground-truth census (read-only) ==")
    text_lines.append("ROWS=%d" % len(root_us))
    text_lines.append("LOCATION_FORM: scalar=%d list=%d unmapped-struct=%d missing=%d"
                      % (loc_form["scalar"], loc_form["list"],
                         loc_form["unmapped-struct"], loc_form["missing"]))
    text_lines.append("LOCATION_(form,len) distribution:"
                      + " ".join("%s:%d=%d" % (k, l, n)
                                 for (k, l), n in sorted(per_row_loc.items())))
    text_lines.append("GENDER_TOKENS:")
    for tok in sorted(gender, key=lambda t: -gender[t]):
        mapped = C._runtime_gender(tok)
        text_lines.append("  %-28r : count=%d  catalogue_map=%s"
                          % (tok, gender[tok], mapped))
    text_lines.append("PREF_TOKENS:")
    for tok in sorted(pref, key=lambda t: -pref[t]):
        text_lines.append("  %-28r : count=%d" % (tok, pref[tok]))
    text_lines.append("PROP_LIST_CONTAINERS rows=%d ; rows_with_real_prop_identity=%d"
                      % (prop_rows_present, prop_rows_with_identity))
    text_lines.append("ACTOR_COUNT distribution:"
                      + " ".join("%s=%d" % (k, actor_count[k])
                                 for k in sorted(actor_count)))
    text_lines.append("ACTOR_(clip_count) distribution:"
                      + " ".join("%s=%d" % (k, actor_clip_dist[k])
                                 for k in sorted(actor_clip_dist)))
    text_lines.append("OBJECT_CLIP_carriers=%d OBJECT_GEOM_carriers=%d "
                      "OBJECT_MAT_carriers=%d  (identity enters on truthy only)"
                      % (object_clip, object_geom, object_mat))
    text_lines.append("TUNING_PRESENT: display=%d author=%d category=%d"
                      % (trans["display"], trans["author"], trans["category"]))


def _ord318(root_us, lines):
    u = root_us[GOLD]
    lines.append("== ordinal318 bridge portrait ==")
    loc, f = C._locations(u)
    lines.append("raw_display_name=%r" % C._single_text(u, C.DISPLAY_FIELDS))
    lines.append("author=%r" % C._single_text(u, C.AUTHOR_FIELDS))
    lines.append("sex_category(upper)=%r"
                 % C._single_text(u, C.CATEGORY_FIELDS).upper())
    lines.append("locations=%r form=%s" % (loc, f))
    lines.append("actors:")
    for a in C._actors(u):
        gr = (a.get("genders") or "").strip()
        lines.append("  actor_id=%r clip=%r animation_type=%r "
                     "animation_genders=%r pref=%r -> runtime_gender=%s"
                     % (a.get("actor_id"), a.get("clip"), a.get("type"), gr,
                        (a.get("pref_gender") or "").strip(),
                        C._runtime_gender(gr)))
    lines.append("objects=%r" % (C._object_slot(u),))
    pr = C._props(u)
    lines.append("props(count=%d)=%r" % (len(pr), pr))
    # optional: all direct keys of the ordinal-318 row (present), for schema light


def _direct_keys(u):
    out = {}
    for nd in u.iter():
        n = C._name(nd)
        if n and n not in out:
            out[n] = C._el_tag(nd)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pkg", help="WW_Nevely42_Animations.package path")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    pkg = Path(a.pkg)
    if not pkg.is_file():
        print("FATAL=PACKAGE_MISSING %s" % pkg, file=sys.stderr)
        return 2
    try:
        _sha, _inst, _n, us = C.load_roster(pkg)  # enforces authoritative pins
    except Exception as e:
        print("FATAL=SOURCE_GATE_FAIL %s" % e, file=sys.stderr)
        return 3
    lines = []
    lines.append("SOURCE_SHA256=%s" % _sha)
    lines.append("SOURCE_INSTANCE=%s" % _inst)
    lines.append("ENTRY_COUNT=%d" % _n)
    _collect(us, lines)
    _ord318(us, lines)
    lines.append("direct-key vocabulary seen (tag:n):")
    keys = {}
    for u in us:
        for n, t in _direct_keys(u).items():
            keys.setdefault(n, t)
    for n in sorted(keys):
        lines.append("  %s : %s" % (keys[n], n))
    text = "\n".join(lines)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
        print("WROTE %s" % a.out)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
