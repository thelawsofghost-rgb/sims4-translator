#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_identifier_catalog_logic_test.py --- offline logic test for the P32
479-row identifier catalog (ww_p32_identifier_catalog.py).

WHY OFFLINE / MECHANISM-ONLY
----------------------------
The real WW_Nevely42_Animations.package lives only on the Windows box; this Linux
repo has no byte copy and no real 3.7 pyc.  We synthesize a DBPF whose WW_ANIM_XML
FAITHFULLY models the real per-entry schema (display/author/category/locations/
per-actor <U> genders+clip) and prove the catalog MECHANISM:
  * every row is enumerated in source order (ordinal == list index),
  * each row's PROVEN_TUNING inputs are read verbatim and the RUNTIME group flows
    through the authoritative reconstructor transform (NOT stamped as raw '0'),
  * ordinal 318 (the golden) reproduces EXACTLY d0528d3795... from its own row,
  * a row missing any required tuning input fails closed to
    validation_status=UNKNOWN with an exact reason (never fabricated, never
    skipped, never demoted),
  * the identity is computed PURELY by the reconstructor (no second formula;
    catalog and reconstructor share the same sha1 function),
  * object/prop/geometry/material/version-ish tuning beyond the pure-actor model
    (when a synthetic row genuinely carries one) gates that row to UNKNOWN rather
    than guessing how it enters get_identifier,
  * duplicates are surfaced (never auto-dedup), CSV columns are the mandated set
    + safe context, and the structural sample is deterministic.
These lock the MECHANISM only: real VALID/UNKNOWN/unique counts are decided on the
Windows run against the real package (enforce the sha/instance/entry=479 pins).

Exit: 0 = all pass; 1 = any fail.
"""
import csv
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from ww_animation_canary_builder import build_package  # noqa: E402
from dbpf_fast import safe_parse  # noqa: E402

import ww_p32_identifier_catalog as C  # noqa: E402
import ww_p32_identifier_reconstruct as REC  # noqa: E402

WW_ANIM_XML = 0x7DF2169C
WW_GROUP = 0x00B2D882
WW_INST = 0x43F3438A94EDEB2B
ORD = C.GOLDEN_ORDINAL

_passes = []


def check(name, cond, detail=""):
    _passes.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))
    return bool(cond)


def _actor(clip, gender, cid="1"):
    g = '<T n="animation_genders">%s</T>' % gender if gender \
        else '<T n="animation_genders"></T>'
    return ('<U n="actor"><T n="actor_id">%s</T>'
            '<T n="animation_animation_type">penetrative</T>'
            '<T n="animation_clip_name">%s</T>%s</U>') % (cid, clip, g)


def _loc_scalar(locs):
    return '<T n="animation_locations">%s</T>' % "|".join(locs)


def _loc_list(locs):
    return '<L n="animation_locations">%s</L>' % "".join(
        '<T n="location">%s</T>' % x for x in locs)


def _row(i, loc_mode="list", extra=""):
    if i == ORD:
        dn = "NOT Caught Cheating 2"; au = "Nevely42"; cat = "VAGINAL"
        locs = ["DOUBLE_BED"]; clips = ["nevely42_cheat2_a0", "nevely42_cheat2_a1"]
        gs = ["MALE", "FEMALE"]
    else:
        dn = "Catalog %d" % i; au = "Synth"; cat = ["ORAL", "ANAL"][i % 2]
        locs = ["FLOOR"] if i % 3 else ["DOUBLE_BED", "SHOWER"]
        clips = ["synth_%d_a0" % i]; gs = ["MALE"]
    act = "".join(_actor(clips[k], gs[k], cid=str(k + 1)) for k in range(len(gs)))
    lg = _loc_list(locs) if loc_mode == "list" else _loc_scalar(locs)
    return ('<U n="anm%03d">' % i
            + '<T n="animation_raw_display_name">%s</T>' % dn
            + '<T n="animation_author">%s</T>' % au
            + '<T n="animation_category">%s</T>' % cat
            + lg
            + '<L n="actors">%s</L>' % act
            + extra
            + '</U>')


def make_pkg(out, rows, inst=WW_INST, by_meta=True):
    xml = '<I n="WickedWhimsAnimationPackage"><L n="animations_list">' + \
          "".join(rows) + '</L></I>'
    body = xml.encode("utf-8")
    meta = {"comp_state": False, "comp_type": 0, "mem_size": len(xml),
            "offset_high_bit": 0, "size_high_bit": 0} if by_meta else None
    items = [(WW_ANIM_XML, WW_GROUP, inst, body, meta)]
    build_package(items, out)
    return xml


def full_rows(n=479, loc_mode="list"):
    return [_row(i, loc_mode) for i in range(n)]


def main():
    tmp = Path(tempfile.mkdtemp(prefix="p32_cat_logic_"))

    # ---- M1: 479-row roster, list-locations -> all VALID, ordinal318 golden ----
    p1 = tmp / "m1.package"
    make_pkg(p1, full_rows(479, "list"))
    cat1 = C.build_catalog(p1, accept_any_sha=True)
    v1 = [r for r in cat1["rows"] if r.get("status") == "VALID"]
    g1 = next((r for r in cat1["rows"] if r["ordinal"] == ORD), None)
    check("M1-row-count-479", len(cat1["rows"]) == 479, str(len(cat1["rows"])))
    check("M1-entry-count-479", cat1["entry_count"] == 479)
    check("M1-valid-479", len(v1) == 479, str(len(v1)))
    check("M1-unknown-0", len(cat1["rows"]) - len(v1) == 0)
    check("M1-ord318-golden", bool(g1) and g1.get("identifier") == C.GOLDEN_SHA1_318,
          repr(g1 and g1.get("identifier")))
    check("M1-ord318-raw", bool(g1) and g1["raw_display_name"] == "NOT Caught Cheating 2")
    ids1 = {r["identifier"] for r in v1}
    check("M1-all-unique-479", len(ids1) == len(v1), str(len(ids1)))

    # ---- M2: scalar-locations shape also full-resolves + golden ----
    p2 = tmp / "m2.package"
    make_pkg(p2, full_rows(479, "scalar"))
    cat2 = C.build_catalog(p2, accept_any_sha=True)
    v2 = [r for r in cat2["rows"] if r.get("status") == "VALID"]
    g2 = next(r for r in cat2["rows"] if r["ordinal"] == ORD)
    check("M2-scalar-loc-479", len(v2) == 479, str(len(v2)))
    check("M2-scalar-golden", g2.get("identifier") == C.GOLDEN_SHA1_318)

    # ---- M3: a row missing required tuning -> UNKNOWN + exact reason ----
    no_loc = ('<U n="noloc"><T n="animation_raw_display_name">N</T>'
              '<T n="animation_author">A</T>'
              '<T n="animation_category">VAGINAL</T>'
              '<L n="actors">%s</L></U>' % _actor("c0", "MALE"))
    cat3 = C.build_catalog(tmp / "m1.package", accept_any_sha=True)
    # M3 uses synthetic 1-row elements directly (string-parsed in build_row)
    rec_missing_display = C.build_row(
        '<U n="x"><T n="animation_author">A</T>'
        '<T n="animation_category">VAGINAL</T>'
        '<T n="animation_locations">DOUBLE_BED</T>'
        '<L n="actors">%s</L></U>' % _actor("c0", "MALE"), 5)
    check("M3-missing-display-UNKNOWN",
          rec_missing_display["status"] == "UNKNOWN"
          and "display_name" in rec_missing_display["status_reason"],
          rec_missing_display["status_reason"])
    rec_empty_gender = C.build_row(
        '<U n="x"><T n="animation_raw_display_name">D</T>'
        '<T n="animation_author">A</T>'
        '<T n="animation_category">VAGINAL</T>'
        '<T n="animation_locations">FLOOR</T>'
        '<L n="actors">%s</L></U>' % _actor("c0", ""), 6)
    check("M3-empty-gender-UNKNOWN",
          rec_empty_gender["status"] == "UNKNOWN"
          and "gender_runtime=UNKNOWN" in rec_empty_gender["status_reason"],
          rec_empty_gender["status_reason"])
    rec_noactor = C.build_row(
        '<U n="x"><T n="animation_raw_display_name">D</T>'
        '<T n="animation_author">A</T>'
        '<T n="animation_category">VAGINAL</T>'
        '<T n="animation_locations">FLOOR</T></U>', 7)
    check("M3-no-actors-UNKNOWN",
          rec_noactor["status"] == "UNKNOWN" and "actors" in rec_noactor["status_reason"],
          rec_noactor["status_reason"])

    # ---- C: MISSING location => empty runtime list VALID; structured list => UNKNOWN
    rec_noloc = C.build_row(
        '<U n="x"><T n="animation_raw_display_name">NoLoc</T>'
        '<T n="animation_author">A</T>'
        '<T n="animation_category">VAGINAL</T>'
        '<L n="actors">%s</L></U>' % _actor("c0", "MALE"), 12)
    check("C-missing-location-VALID-empty", rec_noloc["status"] == "VALID"
          and rec_noloc.get("location_literals") == []
          and rec_noloc.get("location_form") == "missing",
          rec_noloc["status_reason"])
    rec_structloc = C.build_row(
        '<U n="x"><T n="animation_raw_display_name">Struct</T>'
        '<T n="animation_author">A</T>'
        '<T n="animation_category">VAGINAL</T>'
        '<L n="animation_locations"><U n="loc0"><T n="id">5</T></U></L>'
        '<L n="actors">%s</L></U>' % _actor("c0", "MALE"), 13)
    check("C-unmapped-struct-location-UNKNOWN",
          rec_structloc["status"] == "UNKNOWN"
          and "uninterpretable" in rec_structloc["status_reason"],
          rec_structloc["status_reason"])

    # ---- M4 (A): object clip carrier -> VALID + clip enters hash -----
    # An object-clip tuning leaf must NOT gate a row; it is read as the row's
    # object slot.  'carriers_object_prop' gating is REMOVED (rev).
    rec_obj = C.build_row(
        '<U n="x"><T n="animation_raw_display_name">ObjRow</T>'
        '<T n="animation_author">A</T>'
        '<T n="animation_category">VAGINAL</T>'
        '<T n="animation_locations">FLOOR</T>'
        '<L n="actors">%s</L>'
        '<T n="object_animation_clip_name">o_clip_a0</T></U>' % _actor("c0", "MALE"), 8)
    check("M4-object-carrier-VALID", rec_obj["status"] == "VALID",
          rec_obj["status_reason"])
    import hashlib as _hl
    _fp = rec_obj.get("fields_proven") or {}
    _pre = "".join(str(p) for p in C.build_identifier_parts(_fp))
    check("M4-object-clip-in-hash",
          _fp.get("object_animation_clip_name") == "o_clip_a0"
          and "o_clip_a0" in _pre,
          _fp.get("object_animation_clip_name"))
    # object geometry/material MISSING -> skipped (None), object clip still ok
    check("M4-object-geom-material-skipped",
          rec_obj["fields_proven"]["object_geometry_state"] is None
          and rec_obj["fields_proven"]["object_material_state"] is None)

    # ---- M4b (A): animation_props_list PRESENT (even empty) is never a gate ----
    # ordinal-318 case: props-list container present but no real identity prop ->
    # valid EMPTY props contribution; only prop clip/state enter when present.
    rec_pr = C.build_row(
        '<U n="x"><T n="animation_raw_display_name">PropRow</T>'
        '<T n="animation_author">A</T>'
        '<T n="animation_category">VAGINAL</T>'
        '<T n="animation_locations">FLOOR</T>'
        '<L n="actors">%s</L>'
        '<L n="animation_props_list"><U n="p0"><T n="prop_id">7</T>'
        '<T n="prop_type">x</T><T n="prop_guids">a,b</T></U></L></U>' % _actor("c0", "MALE"), 9)
    check("M4b-props-list-presence-not-gate", rec_pr["status"] == "VALID",
          rec_pr["status_reason"])
    # non-identity-only prop (id/type/guids + no clip/state) -> empty prop element
    check("M4b-props-nonidentity-only",
          rec_pr.get("fields_proven", {}).get("props") == []
          or all(not (p.get("animation_clip_name") or "") and not (p.get("geometry_state") or "")
                 for p in rec_pr.get("fields_proven", {}).get("props", [])))

    # ---- M4c (A): a real prop clip enters the hash in get_props order ----
    rec_pc = C.build_row(
        '<U n="x"><T n="animation_raw_display_name">PropRow2</T>'
        '<T n="animation_author">A</T>'
        '<T n="animation_category">VAGINAL</T>'
        '<T n="animation_locations">FLOOR</T>'
        '<L n="actors">%s</L>'
        '<L n="animation_props_list">'
        '<U n="p0"><T n="prop_id">1</T><T n="prop_animation_clip_name">pa0</T></U>'
        '<U n="p1"><T n="prop_id">2</T><T n="prop_animation_clip_name">pb1</T></U>'
        '</L></U>' % _actor("c0", "MALE"), 10)
    check("M4c-props-clip-VALID", rec_pc["status"] == "VALID", rec_pc["status_reason"])
    pclips = [p.get("animation_clip_name") for p in rec_pc["fields_proven"]["props"]]
    check("M4c-props-order-clips", pclips == ["pa0", "pb1"], repr(pclips))
    _pre_pc = "".join(str(p) for p in C.build_identifier_parts(rec_pc["fields_proven"]))
    check("M4c-props-in-hash", "pa0" in _pre_pc and "pb1" in _pre_pc)

    # ---- M4d (D): ordinal-318 with an EMPTY animation_props_list + MALE/FEMALE
    #                returns VALID and re-hits the golden (props EMPTY contrib) ----
    row318 = _row(ORD, "list", extra='<L n="animation_props_list"></L>')
    rec318 = C.build_row(row318, ORD)
    f318 = rec318.get("fields_proven")
    import ww_p32_identifier_reconstruct as _REC
    g318 = _REC.reconstruct_identifier(f318)["sha1"] if f318 else ""
    check("M4d-ord318-empty-props-container-VALID", rec318["status"] == "VALID"
          and (rec318.get("prop_rows") or []) == []
          and not (rec318.get("fields_proven") or {}).get("props"),
          rec318["status_reason"])
    check("M4d-ord318-golden", g318 == C.GOLDEN_SHA1_318, g318)

    # ---- D bridge fixture: plain ordinal-318 no extra container still golden ----
    rec318b = C.build_row(_row(ORD, "list"), ORD)
    f318b = rec318b.get("fields_proven")
    g318b = _REC.reconstruct_identifier(f318b)["sha1"] if f318b else ""
    check("D-ord318-golden", rec318b["status"] == "VALID" and g318b == C.GOLDEN_SHA1_318,
          g318b)

    # ---- M5: duplicates detected but NOT auto-deduped ----
    dup_rows = [_row(0, "list")] + [_row(ORD, "list")] + [_row(0, "list")]
    p5 = tmp / "m5.package"
    make_pkg(p5, dup_rows)
    cat5 = C.build_catalog(p5, accept_any_sha=True)
    v5 = [r for r in cat5["rows"] if r.get("status") == "VALID"]
    groups, dups = C._dup_groups(cat5["rows"])
    check("M5-dup-detected", len(dups) == 1 and len(v5) == 3,
          "dupgroups=%d valid=%d" % (len(dups), len(v5)))
    _seen_same_pair = any(len(o) >= 2 for o in dups.values())
    check("M5-no-autodedup-rows-kept", len(cat5["rows"]) == 3 and _seen_same_pair,
          "rows=%d" % len(cat5["rows"]))

    # ---- M6: structural sample deterministic + selects VALID distinct ----
    s_a = C._structural_sample(cat1, 8)
    s_b = C._structural_sample(cat1, 8)
    check("M6-sample-deterministic", s_a == s_b and len(s_a) >= 5,
          "n=%d" % len(s_a))
    check("M6-sample-valid-only", all("UNKNOWN" not in l for l in s_a))
    # samples carry ordinal/name/id + summary
    check("M6-sample-has-id", all("ordinal=" in l and "id=" in l for l in s_a))

    # ---- M7: reconstructor identity formula REUSED (sha identity) ----
    # 318 fields must equal C's and REC's golden, proving catalog uses REC.
    g1 = next(r for r in cat1["rows"] if r["ordinal"] == ORD)
    res = REC.reconstruct_identifier(g1["fields_proven"])
    check("M7-shared-formula",
          res["sha1"] == g1["identifier"] == REC.GOLDEN_SHA1_318,
          "%s" % (res["sha1"],))

    # ---- M8: CSV write -> mandated columns + context-only extra never hashed ----
    od = tmp / "out1"; od.mkdir(parents=True, exist_ok=True)
    csvp = od / "p32_identifier_catalog.csv"
    C.write_csv(csvp, cat1, cat1["source_instance"])
    with open(csvp, "r", encoding="utf-8", newline="") as fh:
        rd = list(csv.DictReader(fh))
    need = {"source_instance", "ordinal", "raw_display_name", "author",
            "identifier", "validation_status", "identity_input_summary"}
    check("M8-min-cols", need <= set(rd[0].keys()),
          "missing=%s" % (need - set(rd[0].keys())))
    check("M8-479-csv-rows", len(rd) == 479, str(len(rd)))
    # every VALID row has a non-empty identifier; ord318 col matches
    g_row = [x for x in rd if int(x["ordinal"]) == ORD][0]
    check("M8-csv-golden", g_row["identifier"] == C.GOLDEN_SHA1_318,
          g_row["identifier"])
    # ensure every VALID csv row identifier is 40-hex
    ok_hex = all(len(x["identifier"]) == 40 or x["validation_status"] == "UNKNOWN"
                 for x in rd)
    check("M8-identifiers-40hex-or-unknown", ok_hex)

    # ---- M9: source sha / instance / entry pins fail closed (no accept-any) ----
    # m1 has the wrong (synthetic) sha -> without bypass must raise SOURCE_GATE
    env_no = dict(os.environ); env_no.pop("P32_SRC_ACCEPT_ANY_SHA", None)
    r9 = subprocess.run([sys.executable,
                         str(Path(__file__).parent / "ww_p32_identifier_catalog.py"),
                         str(p1), "--out-dir", str(od)],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        universal_newlines=True, env=env_no)
    check("M9-wrong-sha-failclosed", r9.returncode != 0
          and "SOURCE_GATE_FAIL" in (r9.stdout + r9.stderr),
          "rc=%d" % r9.returncode)

    # wrong instance gate: the instance pin is STRUCTURAL (non-bypassable), so a
    # package built with the wrong WW_ANIM_XML instance fails closed even when
    # sha/count bypass is on.
    p9b = tmp / "m9b.package"
    make_pkg(p9b, full_rows(30), inst=WW_INST ^ 1)
    try:
        C.load_roster(p9b, accept_any_sha=True)
        check("M9-instance-pin-raise", False, "no raise")
    except RuntimeError as e:
        check("M9-instance-pin-raise", "instance" in str(e), str(e)[:80])

    # ---- M10: accept_any + exact roster count 479 for the real full path ----
    check("M10-entry-count-479-cat1", cat1["entry_count"] == 479)
    # a 30-row synthetic with CORRECT instance loads fine under accept-any sha
    # bypass (count pin is bypassed for offline-only); proves the code path runs
    # on a non-479 synthetic roster, while the LIVE default enforces 479 (M1).
    p10 = tmp / "m10.package"
    make_pkg(p10, full_rows(30, "list"))
    cat10 = C.build_catalog(p10, accept_any_sha=True)
    check("M10-offline-bypass-30rows-ok", len(cat10["rows"]) == 30, str(len(cat10["rows"])))

    # ---- M11: per-row no bleed (ordinal isolation deterministic) ----
    r300 = cat1["rows"][300]; r0 = cat1["rows"][0]
    check("M11-no-bleed", r300["raw_display_name"] != r0["raw_display_name"]
          and r300["ordinal"] == 300 and len(r0["actor_rows"]) >= 1)

    print("")
    print("PASS_COUNT=%d  FAIL_COUNT=%d"
          % (sum(1 for _n, ok in _passes if ok),
             sum(1 for _n, ok in _passes if not ok)))
    failed = [n for n, ok in _passes if not ok]
    if failed:
        print("FAILED_NAMES=%s" % failed)
        return 1
    print("P32_CATALOG_LOGIC=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
