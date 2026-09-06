#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_loader_origin_probe_logic_test.py -- regression harness for the P32 rev-B
EXACT loader-origin probe + TUNING_DEFAULT_SEMANTICS gate.

Model under test (rev B, exact-dataflow-first)
----------------------------------------------
JOB 1 FIELD ORIGIN: closed by the exact Windows dataflow, NOT by a generic STORE
tracer.  ORIGIN_ROWS gives the 8 inputs their PROVEN status:
    PROVEN_TUNING   x6 : object_animation_clip_name, object_geometry_state,
                         object_material_state, prop_animation_clip_name,
                         prop_geometry_state, version
    PROVEN_TRANSFORM x2 : actor.position_offset.x/y/z, actor.angle_offset/facing
    UNKNOWN         x0  (origin is closed; a broad walker is diagnostic-only and
                         can never re-open it).  Assert 0 UNKNOWN and 0 default.

JOB 2 TUNING_DEFAULT_SEMANTICS (the live gate): each correlated XML key's
missing-key default must be PROVEN from bytecode/schema evidence, never guessed
(0 / 0.0 / '' / None / 1 need evidence).  Without a real _ts4_animations_tuning.pyc
decode, decode_defaults() must FAIL CLOSED (all 11 keys UNKNOWN, gate NO).

Invariants:
  A1 ORIGIN_ROWS = 8, statuses in {PROVEN_TUNING, PROVEN_TRANSFORM}; UNKNOWN==0
     and DEFAULT==0 (origin closed).
  A2 exact origin statuses by field (6 tuning/2 transform).
  A3 census uses REAL tuning keys for actor offsets (animation_x_offset/...),
     NEVER the identity-field spelling "position_offset".
  A4 STRUCTURAL scoping: prop clip/state counted ONLY inside a prop <U> under the
     prop list; the same-named bare row text must NOT count.
  A5 STRUCTURAL scoping: actor offset counted ONLY inside an actor <U> under the
     actor list.
  A6 non-empty carrier detection: an empty "" leaf is not a carrier.
  A7 carrier counts are small, exact and reproducible (object partial / prop
     partial / version).
  A8 origin_rows_from_schema annotates partial carriers as per-row-mandatory and
     exposes carrier_count.
  A9 default gate: no tuning pyc -> every DEFAULT_KEYS key UNKNOWN -> report gate
     FULL_CORPUS_SAFE_TO_RECONSTRUCT=NO with STOP_REASON; catalog must not run.
  A10 DefaultSemanticsReport.set_default with BOTH a value and evidence marks a
     key PROVEN; all_proven() reflects it; unknown_keys() mirrors reversals.
  A11 decode_defaults is a pure fail-closed no-op on Linux (returns report with
     0 proven keys) -- no fabrication of 0/0.0/''/None/1 from silence.
Exit 0 = all PASS; 1 = any FAIL.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import ww_p32_loader_origin_probe as lop


def _name(el):
    return el.get("n")


def _text(el):
    return "" if el.text is None else el.text


_SYNTH = (
    '<I n="T">'
    '<L n="animations_list">'
    # Row 0: actor list carries animation_x_offset"5.0" (real), angle 90; y empty
    '<U n="r0">'
    '  <L n="animation_actors_list">'
    '    <U n="a0">'
    '      <T n="actor_id">s</T>'
    '      <T n="animation_x_offset">5.0</T>'
    '      <T n="animation_y_offset"></T>'
    '      <T n="animation_z_offset">-1.5</T>'
    '      <T n="animation_angle_offset">90.0</T>'
    '    </U>'
    '  </L>'
    '  <T n="animation_version">4</T>'
    '</U>'
    # Row 1: prop list carries clip + geometry; ALSO a bare row-level
    # prop_animation_clip_name text that must NOT be counted (not in a prop <U>).
    '<U n="r1">'
    '  <L n="animation_props_list">'
    '    <U n="p0">'
    '      <T n="prop_animation_clip_name">cA</T>'
    '      <T n="prop_geometry_state">1</T>'
    '    </U>'
    '  </L>'
    '  <T n="prop_animation_clip_name">stray-not-in-prop-list</T>'
    '</U>'
    # Row 2: object clip at row direct; version at row level
    '<U n="r2">'
    '  <T n="object_animation_clip_name">chair</T>'
    '  <T n="animation_version">2</T>'
    '</U>'
    # Row 3: object geometry+material wrapped in an animation_object container;
    # an actor list that does NOT carry offsets must not be a carrier.
    '<U n="r3">'
    '  <U n="animation_object">'
    '    <T n="object_geometry_state">geo1</T>'
    '    <T n="object_material_state">mat1</T>'
    '  </U>'
    '  <L n="animation_actors_list"><U n="a0"><T n="actor_id">x</T></U></L>'
    '</U>'
    '</L>'
    '</I>'
)


def _roster():
    root = ET.fromstring(_SYNTH)
    lst = [x for x in root.iter()
           if x.tag.rsplit("}", 1)[-1] == "L" and _name(x) == "animations_list"][0]
    return [c for c in list(lst) if c.tag.rsplit("}", 1)[-1] == "U"]


def main():
    ok = []

    def check(name, cond, detail=""):
        ok.append((name, bool(cond)))
        print("PASS %s%s%s" % (name, "  | " if detail else "", detail))

    # ---- A1 / A2  exact origin closed ----
    check("A1-origin-count", len(lop.ORIGIN_ROWS) == 8, "rows=%d" % len(lop.ORIGIN_ROWS))
    oset = {r["status"] for r in lop.ORIGIN_ROWS}
    check("A1-origin-no-unknown", "UNKNOWN" not in oset and
          oset <= {"PROVEN_TUNING", "PROVEN_TRANSFORM"}, "statuses=%s" % sorted(oset))
    byf = {r["identity_field"]: r["status"] for r in lop.ORIGIN_ROWS}
    n_tun = sum(1 for v in byf.values() if v == "PROVEN_TUNING")
    n_tr = sum(1 for v in byf.values() if v == "PROVEN_TRANSFORM")
    check("A2-origin-split", (n_tun, n_tr) == (6, 2),
          "tuning=%d transform=%d" % (n_tun, n_tr))
    for f in ("object_animation_clip_name", "object_geometry_state",
              "object_material_state", "prop_animation_clip_name",
              "prop_geometry_state", "version"):
        check("A2-tun-" + f, byf.get(f) == "PROVEN_TUNING", byf.get(f, "MISSING"))
    check("A2-tfm-pos", byf.get("actor.position_offset.x/y/z") == "PROVEN_TRANSFORM")
    check("A2-tfm-angle", byf.get("actor.angle_offset / facing_position_offset")
          == "PROVEN_TRANSFORM")

    # ---- census over the synthetic roster ----
    rows_el = _roster()
    counts, ordinals = lop.census_carriers(rows_el, _name, _text)

    # A3 real tuning-key search: actor carrier counts nonzero for the animation_*
    # offset keys, and NOT attribute to a nonexistent "position_offset" span.
    check("A3-xoffset-search", counts["animation_x_offset"] == 1,
          "x=%d" % counts["animation_x_offset"])
    check("A3-zoffset-search", counts["animation_z_offset"] == 1,
          "z=%d" % counts["animation_z_offset"])
    check("A3-angle-search", counts["animation_angle_offset"] == 1,
          "angle=%d" % counts["animation_angle_offset"])
    # y present row r0 but empty string -> NOT a carrier (A6)
    check("A6-empty-not-carrier", counts["animation_y_offset"] == 0
          and ordinals["animation_y_offset"] == [],
          "y_count=%d" % counts["animation_y_offset"])

    # A4 structural: prop counted (r1 prop <U>), but the bare row-level stray text
    # in r1 and every non-prop text do NOT add carriers.
    check("A4-prop-clip-scoped", counts["prop_animation_clip_name"] == 1
          and ordinals["prop_animation_clip_name"] == [1],
          "prop=%d ord=%r" % (counts["prop_animation_clip_name"],
                              ordinals["prop_animation_clip_name"]))
    check("A4-prop-geo-scoped", counts["prop_geometry_state"] == 1
          and ordinals["prop_geometry_state"] == [1], "geo=%d" % counts["prop_geometry_state"])

    # A5 actor offset only inside an actor <U> under the actor list
    check("A5-actor-scoped", ordinals["animation_angle_offset"] == [0],
          "angle-ord=%r" % ordinals["animation_angle_offset"])

    # object keys: r2 direct (clip), r3 wrapped (geometry/material)
    check("A7-object-clip", counts["object_animation_clip_name"] == 1
          and ordinals["object_animation_clip_name"] == [2],
          "clip=%d %r" % (counts["object_animation_clip_name"],
                          ordinals["object_animation_clip_name"]))
    check("A7-object-geo", counts["object_geometry_state"] == 1
          and ordinals["object_geometry_state"] == [3],
          "geo=%d %r" % (counts["object_geometry_state"],
                         ordinals["object_geometry_state"]))
    check("A7-object-mat", counts["object_material_state"] == 1
          and ordinals["object_material_state"] == [3],
          "mat=%d %r" % (counts["object_material_state"],
                         ordinals["object_material_state"]))
    check("A7-version", counts["animation_version"] == 2
          and ordinals["animation_version"] == [0, 2],
          "ver=%d %r" % (counts["animation_version"],
                         ordinals["animation_version"]))

    # A8 origin rows annotate partial carriers, expose counts
    n = len(rows_el)
    orows = lop.origin_rows_from_schema(counts, n)
    ob = {r["identity_field"]: r for r in orows}
    obj = ob["object_animation_clip_name"]
    check("A8-partial-note", "per-row" in obj["reason"] and "MANDATORY" in obj["reason"],
          obj["reason"])
    check("A8-carrier-count-mirror", obj["carrier_count"] == 1,
          "count=%d" % obj["carrier_count"])
    ver = ob["version"]
    check("A8-version-partial", ver["carrier_count"] == 2 and "per-row" in ver["reason"],
          "ver-count=%d" % ver["carrier_count"])

    # ---- A9 / A10 / A11  default gate fails closed ----
    rep = lop.decode_defaults()          # no tuning pyc on Linux
    rep.load_carriers(counts)
    allp = rep.all_proven()
    check("A11-fail-closed-noop", allp is False,
          "proven=%s" % [k for k in rep.entries if rep.entries[k]["proven"]])
    unk = rep.unknown_keys()
    check("A9-unknown-all-keys", len(unk) == len(lop.DEFAULT_KEYS),
          "unknown_count=%d keys_total=%d" % (len(unk), len(lop.DEFAULT_KEYS)))
    # render gate NO
    lines = lop.render_report(orows, counts, ordinals, n, rep)
    joined = "\n".join(lines)
    check("A9-gate-no", "FULL_CORPUS_SAFE_TO_RECONSTRUCT=NO" in joined
          and "STOP_REASON=" in joined, "gate(NO)+stop present")

    # A10 set_default with value+evidence flips that key PROVEN
    rep2 = lop.DefaultSemanticsReport()
    rep2.set_default("animation_x_offset", 0.0, "0.0",
                     "test: TunableTuple leaf default literal co@x:0")
    check("A10-single-proven", rep2.all_proven() is False
          and rep2.entries["animation_x_offset"]["proven"], "x proven")
    rep2.set_default("animation_version", 1, "1", "test: contra")
    check("A10-two-proven", rep2.entries["animation_version"]["proven"], "ver proven")
    check("A10-unknown-list", rep2.unknown_keys() == [
        k for k, _e in lop.DEFAULT_KEYS
        if k not in ("animation_x_offset", "animation_version")],
        "unknown=%d" % len(rep2.unknown_keys()))

    # gate YES is only reachable when every default key is proven
    rep_good = lop.DefaultSemanticsReport()
    for k, _f in lop.DEFAULT_KEYS:
        rep_good.set_default(k, 0, "0", "test literal evidence")
    check("A10-all-proven-yes", rep_good.all_proven(), "all 11 proven")

    failed = [n for n, passed in ok if not passed]
    print("PASS_COUNT=%d FAIL_COUNT=%d" % (len(ok) - len(failed), len(failed)))
    if failed:
        print("FAILED_NAMES=%s" % failed)
        return 1
    print("P32_LOADER_ORIGIN_PROBE_LOGIC=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
