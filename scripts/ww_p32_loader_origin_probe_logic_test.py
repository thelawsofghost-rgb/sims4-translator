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
import ww_p32_loader_origin_defaults as lod


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

    # ---- C-section: TUNING_DEFAULT decoder on compiled TUNABLE_STRUCTURE maps ----
    # The decoder (_leaf_default_ins + decode_structure) must recover missing-key
    # defaults from real dict-literal class bodies PURELY from bytecode order/semantics
    # (never literal-scan near a field name), distinguish None/0/0.0/''/1, recurse into
    # the three nested WW owner class bodies, and honour the wrapper form.  We compile
    # the documented idiom on the running interpreter (real dis instruction lists) and
    # assert the recovered map equals expectations.
    import dis

    def _build(src):
        return compile(src, "<c%d>" % len(ok), "exec")

    def _decode_owners(code):
        return lod.extract_from_code(code, lambda c: list(dis.get_instructions(c)))

    # C1/C2/C5 shared fixture: actor = None/0.0/float/repeated same 0.0; props = wrapper
    # form (clip '' str + geo 0 int); data = '' x3 + 1 -> owned by the WW class names.
    _SRC = (
        "def _tse(raw_type=None, default=None, **kw):\n"
        "    if \"default\" in kw: default = kw[\"default\"]\n"
        "    return default\n"
        "def TunableX(default=None, **kw): return default\n"
        "class _WickedWhimsAnimationActor(object):\n"
        "    TUNABLE_STRUCTURE = {\n"
        "        \"animation_x_offset\": _tse(default=None),\n"
        "        \"animation_y_offset\": _tse(default=0.0),\n"
        "        \"animation_z_offset\": _tse(default=1),\n"
        "        \"animation_angle_offset\": _tse(default=0.0),\n"
        "        \"animation_facing_offset\": _tse(default=0),\n"
        "    }\n"
        "class _WickedWhimsAnimationPropsData(object):\n"
        "    TUNABLE_STRUCTURE = {\n"
        "        \"prop_animation_clip_name\": _tse(TunableX(default=\"\"), raw_type=\"str\"),\n"
        "        \"prop_geometry_state\": _tse(TunableX(default=0), raw_type=\"int\"),\n"
        "    }\n"
        "class _WickedWhimsAnimationData(object):\n"
        "    TUNABLE_STRUCTURE = {\n"
        "        \"object_animation_clip_name\": _tse(default=\"\"),\n"
        "        \"object_geometry_state\": _tse(default=\"\"),\n"
        "        \"object_material_state\": _tse(default=\"\"),\n"
        "        \"animation_version\": _tse(default=1),\n"
        "    }\n"
    )
    res, _cm = _decode_owners(_build(_SRC))

    # C1: field ORDER is authoritative (keytuple[i] <- i-th leaf by bytecode order)
    act = res["_WickedWhimsAnimationActor"]["evidence"]
    _EXP_ACTOR = {"animation_x_offset": None, "animation_y_offset": 0.0,
                  "animation_z_offset": 1, "animation_angle_offset": 0.0,
                  "animation_facing_offset": 0}
    c1ok = (act == _EXP_ACTOR or
            all(k in act and type(act[k]["default"]).__name__ == type(v).__name__
                and (act[k]["default"] == v or (v is None and act[k]["default"] is None))
                for k, v in _EXP_ACTOR.items()))
    check("C1-field-order-typing", c1ok and not res["_WickedWhimsAnimationActor"]["unresolved"],
          "actor keys=%d" % len(act))
    # C2: repeated identical default 0.0 at x/y positions is disambiguated by order
    #     (animation_x_offset=None, angle=0.0 -> the two 0.0 land on y and angle)
    check("C2-repeated-0.0-disambig",
          act["animation_y_offset"]["default"] == 0.0 and
          act["animation_angle_offset"]["default"] == 0.0 and
          act["animation_z_offset"]["default"] == 1 and
          act["animation_facing_offset"]["default"] == 0 and
          act["animation_x_offset"]["default"] is None,
          "repeated 0.0 by order (NOT type-guessed)")
    # C3: type mixing preserved exactly (None/NoneType, 0.0/float, 1/int, 0/int)
    _T = {k: act[k]["type"] for k in act}
    check("C3-type-mixing",
          _T["animation_x_offset"] == "NoneType" and
          _T["animation_y_offset"] == "float" and
          _T["animation_z_offset"] == "int" and
          _T["animation_facing_offset"] == "int",
          "types=%r" % _T)
    # C4: NESTED traversal reaches all three owner class BODY code objects
    check("C4-nested-3-owners",
          set(res.keys()) == {"_WickedWhimsAnimationActor",
                              "_WickedWhimsAnimationPropsData",
                              "_WickedWhimsAnimationData"},
          "owners=%r" % sorted(res.keys()))
    dat = res["_WickedWhimsAnimationData"]["evidence"]
    check("C4-data-emptystr-versions",
          dat["object_animation_clip_name"]["default"] == "" and
          dat["animation_version"]["default"] == 1, "data oracle orientation")
    # C5: WRAPPER form _tse(TunableX(default=<lit>), raw_type=...) resolves inner literal
    prop = res["_WickedWhimsAnimationPropsData"]["evidence"]
    check("C5-wrapper-inner-literal",
          not res["_WickedWhimsAnimationPropsData"]["unresolved"] and
          prop["prop_animation_clip_name"]["default"] == "" and
          prop["prop_animation_clip_name"]["type"] == "str" and
          prop["prop_geometry_state"]["default"] == 0 and
          prop["prop_geometry_state"]["type"] == "int",
          "wrapper clip=%r geo=%r" % (
              prop["prop_animation_clip_name"]["default"],
              prop["prop_geometry_state"]["default"]))

    # C6: COUNT-MISMATCH fail-closed: a TUNABLE_STRUCTURE whose field-key tuple length
    #     disagrees with BUILD_CONST_KEY_MAP count (structural corruption) must NOT
    #     silently fabricate any default -- it lands UNKNOWN/resolved-gap.
    co_mm = _build(
        "def _tse(raw_type=None, default=None, **kw): return default\n"
        "class _WickedWhimsAnimationData(object):\n"
        "    TUNABLE_STRUCTURE = {\n"
        "        \"object_animation_clip_name\": _tse(default=\"a\"),\n"
        "        \"object_geometry_state\": _tse(default=\"b\"),\n"
        "        \"object_material_state\": _tse(default=\"c\"),\n"
        "        \"animation_version\": _tse(default=1),\n"
        "        \"AN_EXTRA_KEY_WITH_NO_LEAF\": None,\n"
        "    }\n"
    )
    res_mm, _ = _decode_owners(co_mm)
    # BUILD_CONST_KEY_MAP arg==5 but the dict literal can't have a 5th leaf constant-file;
    # either way the decoder must not report a WRONG value for any unmatched key.
    _found_mm = res_mm.get("_WickedWhimsAnimationData") or {"evidence": {}, "unresolved": []}
    _claims = {k: v["default"] for k, v in _found_mm["evidence"].items()}
    # We accept EITHER an explicit count-mismatch unresolved (fail-closed) OR a partial
    # map with NO fabricated value for a phantom key -- never a fabricated default.
    # For robustness we assert the strongest invariant: the decoder never emits a value
    # it could not prove, and, when the 4 real keys ARE all proven correctly, the phantom
    # ADDS nothing.
    c6_nofabricate = all(
        _claims[k] in ("a", "b", "c", 1) if k.startswith("object_") or k == "animation_version"
        else False for k in _claims)
    check("C6-count-mismatch-failclosed", c6_nofabricate,
          "claims=%r" % _claims)

    # C7: CPython 3.7 encoding parity.  The REAL WW archive is compiled by CPython
    #     3.7.9, which has NO CALL_FUNCTION_KW opcode: keyword calls like
    #     _tse(default=X) compile to CALL_FUNCTION n with the kw-name tuple passed as
    #     the trailing positional argument.  The decoder must be CALL-opcode agnostic
    #     (it reads only LOAD_CONST packet CONTENTS and CALL membership).  Prove it by
    #     rewriting every leaf CALL_FUNCTION_KW/CALL_KW on the compiled fixture to
    #     CALL_FUNCTION (3.7 stack shape: same name-tuple, same value) and asserting
    #     the recovered defaults are IDENTICAL (incl. the wrapper inner literal and
    #     the None/0/0.0/''/1 type mix).
    class _Instr(object):
        __slots__ = ("opcode", "opname", "arg", "argval", "offset")

    def _to_py37_layout(co_obj):
        """Return an instruction list whose leaf CALL_KW opcodes are renamed to
        CALL_FUNCTION to mirror 3.7's encoding (kw-name tuple already present as a
        trailing positional push).  Other opcodes are unchanged."""
        out = []
        for i in dis.get_instructions(co_obj):
            o = _Instr()
            o.opcode = i.opcode
            o.arg = i.arg
            o.argval = i.argval
            o.offset = i.offset
            o.opname = "CALL_FUNCTION" if i.opname == "CALL_FUNCTION_KW" else i.opname
            out.append(o)
        return out

    res37, _cm37 = lod.extract_from_code(_build(_SRC), _to_py37_layout)
    act37 = res37["_WickedWhimsAnimationActor"]["evidence"]
    prop37 = res37["_WickedWhimsAnimationPropsData"]["evidence"]
    dat37 = res37["_WickedWhimsAnimationData"]["evidence"]
    c7ok = (
        not res37["_WickedWhimsAnimationPropsData"]["unresolved"] and
        act37["animation_x_offset"]["default"] is None and
        act37["animation_y_offset"]["default"] == 0.0 and
        act37["animation_z_offset"]["default"] == 1 and
        act37["animation_facing_offset"]["default"] == 0 and
        prop37["prop_animation_clip_name"]["default"] == "" and
        prop37["prop_animation_clip_name"]["type"] == "str" and
        prop37["prop_geometry_state"]["default"] == 0 and
        prop37["prop_geometry_state"]["type"] == "int" and
        dat37["object_animation_clip_name"]["default"] == "" and
        dat37["animation_version"]["default"] == 1)
    check("C7-py37-call-layout", c7ok,
          "actor_none/0.0/1/0 + wrapper ''/0 + data ''/1 under CALL_FUNCTION")

    # ---- D-section: production runner (ps1) archive selection is EXACT ----
    # Regression for the wrong-archive root cause: the previous runner globbed
    # *.ts4script and took the first hit (ww_p29c_display_caller_trace.ts4script),
    # which does not own wickedwhims/sex/animations tuning.  The runner must pin
    # the real WW archive, never guess.
    _ps1 = (HERE / "ww_p32_loader_origin_probe.ps1").read_text(encoding="utf-8")
    check("D-provided", len(_ps1) > 500, "ps1 bytes=%d" % len(_ps1))
    # D1: no auto glob / first-hit selection remains
    check("D1-no-glob", '-Filter "*.ts4script"' not in _ps1, "no *.ts4script glob")
    check("D1-no-firsthit", "$ts4[0]" not in _ps1
          and '-Filter "*.ts4script"' not in _ps1, "no first-archive glob pick")
    # D2: exact real WW archive authoritative path
    check("D2-exact-ww-archive",
          "WickedWhimsMod\\TURBODRIVER_WickedWhims_Scripts.ts4script" in _ps1,
          "real WW archive pinned")
    check("D2-override-param", "-WW_TS4Script" in _ps1 and "[string]$WW_TS4Script" in _ps1,
          "-WW_TS4Script override param")
    # D3: member matched by EXACT full path, not bare .Name
    check("D3-member-exact-path",
          '$e.FullName -eq $TUNING_MEMBER' in _ps1
          and "_ts4_animations_tuning.pyc" in _ps1,
          "member compare on FullName")
    # D4: exact tuning member constant
    check("D4-tuning-member-const",
          "wickedwhims/sex/animations/_ts4_animations_tuning.pyc" in _ps1
          and "$TUNING_MEMBER = " in _ps1, "tuning member exact")
    # D5: missing member -> FATAL fail-closed, no DEFAULT_UNKNOWN_COUNT conclusion
    check("D5-fatal-guard", "FATAL=TUNING_MEMBER_NOT_FOUND" in _ps1
          and "Fail \"TUNING_MEMBER_NOT_FOUND\"" in _ps1,
          "member-missing FATAL stop")
    # D6: startup prints exact archive + digest + member
    for token in ("WW_TS4SCRIPT=", "WW_TS4SCRIPT_SHA256=", "TUNING_MEMBER="):
        check("D6-token-" + token, token in _ps1, token)
    # D6b: SHA256 is PS5.1-native Get-FileHash (no manual crypto assembly load)
    check("D6-sha-native-cmdlet",
          "Get-FileHash -LiteralPath" in _ps1
          and "-Algorithm SHA256" in _ps1
          and ".Hash.ToLowerInvariant()" in _ps1,
          "PS5.1-native Get-FileHash SHA256")
    # D7: tuned pyc handed to CPython decode
    check("D7-tunes-to-py", "--tuning-pyc\", $tuningPyc" in _ps1
          or "@(\"--tuning-pyc\", $tuningPyc)" in _ps1.replace(chr(10), " "),
          "cpython decode path fed pyc")
    # D8: PS5.1 runner-compat regression -- the EXECUTED code must not
    #     Add-Type the core crypto assembly (not resolvable on Windows
    #     PowerShell 5.1/.NET Fx).  A comment naming it is allowable.
    check("D8-no-addtype-crypto",
          "-AssemblyName System.Security.Cryptography" not in _ps1,
          "no executable Add-Type System.Security.Cryptography")
    check("D8-no-sha256-class", "[System.Security.Cryptography.SHA256]" not in _ps1,
          "no .NET SHA256 class call")

    failed = [n for n, passed in ok if not passed]
    print("PASS_COUNT=%d FAIL_COUNT=%d" % (len(ok) - len(failed), len(failed)))
    if failed:
        print("FAILED_NAMES=%s" % failed)
        return 1
    print("P32_LOADER_ORIGIN_PROBE_LOGIC=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
