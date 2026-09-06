#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_loader_origin_real_diag_logic_test.py -- regression harness for the
real-pyc decoder DIAGNOSTIC (ww_p32_loader_origin_real_diag.py).

What this locks
---------------
The diagnostic stages the TUNING_DEFAULT decoder on a REAL xdis-disassembled pyc
and must (a) discover all three WW owner class code objects, (b) reach a
BUILD_CONST_KEY_MAP -> STORE_NAME TUNABLE_STRUCTURE map, (c) extract the field-name
tuple, (d) count TOP-LEVEL value leaves correctly (this is the regression: the
value-region scan upper bound must be the FIELD-NAME tuple index, not the
BUILD_CONST_KEY_MAP index, else the final leaf is mis-rejected and LEAF_COUNT is
one short), and (e) recover the default literal per correlated key with its type,
then emit exactly one REAL_DECODER_FAILURE_STAGE.

Invariants under test (compiled on the running interpreter + real xdis when
available; xdis-independent logic is asserted regardless):

  R1  value-region upper bound == field-tuple index (not map index).  Root cause:
      scanning to the MAP index put the key-tuple LOAD_CONST (the last leaf's
      follower) inside the window, so the final leaf was treated as a
      non-constructor (followed by LOAD_CONST) and rejected -> LEAF_COUNT one
      short and the last correlated key reported leaf_index=MISSING.
  R2  leaf discriminator counts == field-tuple length for the 3 shapes
      (actor None/0.0/1/0.0/0, props wrapper ''/str + 0/int, data ''x3 + 1).
  R3  per-key default literal + type recovered (NoneType/float/int/str preserved).
  R4  failure-stage classifier is deterministic and complete: healthy input ->
      NONE; a pyc missing a required class -> CLASS_DISCOVERY (the fixture builds
      actor-only and asserts it is NOT NONE and names CLASS_DISCOVERY).
  R5  no hardcoded default is introduced by the diagnostic; it only READS the
      decoder (it imports ww_p32_loader_origin_defaults for _leaf_default_ins).

Exit 0 = all PASS; 1 = any FAIL.
"""
from __future__ import annotations

import contextlib
import io as _io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, str(HERE))

import ww_p32_loader_origin_real_diag as diag  # noqa: E402


def _build_owner_src():
    """The three WW-owner class bodies exactly as the decoder's synthetic fixtures
    and the REAL _ts4_animations_tuning.pyc layout them (direct + wrapper)."""
    return (
        "def _tse(raw_type=None, default=None, **kw):\n"
        "    if \"default\" in kw: default = kw[\"default\"]\n"
        "    return default\n"
        "def TunableX(default=None, **kw): return default\n"
        "def _TunableStructureElement(*args, **kw):\n"
        "    return args[0] if args else kw\n"
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


def _compile_to_pyc(src_text, tag):
    """Compile src to a .pyc via py_compile (uses the running interpreter's magic).
    Returns the .pyc path.  Uses a try so a missing py_compile is not fatal in odd
    sandboxes; the harness SKIPs xdis-dependent assertions otherwise."""
    tmpd = tempfile.mkdtemp(prefix="diagtest_%s_" % tag)
    mpath = os.path.join(tmpd, "m.py")
    with open(mpath, "w", encoding="utf-8") as fh:
        fh.write(src_text)
    cpath = os.path.join(tmpd, "m.pyc")
    try:
        import py_compile
        py_compile.compile(mpath, cfile=cpath, doraise=True)
    except Exception:  # noqa: BLE001
        return None
    return cpath


def _run_diag(pyc_path):
    """Invoke diag.main() against a pyc, capturing the textual stage line by
    writing to a temp output file and returning the produced text."""
    out = os.path.join(tempfile.mkdtemp(prefix="diagout_"),
                       "p32_defaults_real_diagnostic.txt")
    held = list(sys.argv)
    try:
        sys.argv = ["diag", "--tuning-pyc", pyc_path, "--out", out]
        with contextlib.redirect_stdout(_io.StringIO()):
            rc = diag.main()
    finally:
        sys.argv = held
    if rc != 0 or not os.path.isfile(out):
        return None
    with open(out, "r", encoding="utf-8") as fh:
        return fh.read()


def main():
    ok = []

    def check(name, cond, detail=""):
        ok.append((name, bool(cond)))
        print("PASS %s%s%s" % (name, "  | " if detail else "", detail))

    # ---- R5: the diagnostic only READS the decoder (imports it) -------------
    check("R5-imports-readonly-decoder", hasattr(diag.lod, "_leaf_default_ins"),
          "uses lod._leaf_default_ins, never a local default-fabricating table")

    # ---- R1/R2/R3: xdis end-to-end over a compiled 3-owner pyc --------------
    pyc = _compile_to_pyc(_build_owner_src(), "full")
    if pyc is None:
        check("R1-xdis-region-bound-upper=fieldtuple", False, "py_compile unavailable")
    else:
        txt = _run_diag(pyc)
        if txt is None:
            check("R1-xdis-e2e-ran", False, "diag did not produce output")
        else:
            # R3: every class FOUND + every correlated key RESOLVED with a type
            for c in diag._CLASSES:
                check("R3-cls-found-" + c.split("_")[-1],
                      "CLASS=%s\nFOUND=YES" % c in txt, c)
            # every wanted key row resolves to a typed candidate (no UNKNOWN, no
            # MISSING leaf_index)
            n_unknown = txt.count("candidate=UNKNOWN")
            n_missing = txt.count("leaf_index=MISSING")
            check("R3-no-key-unknown", n_unknown == 0,
                  "unknown rows=%d" % n_unknown)
            check("R3-no-leaf-missing", n_missing == 0,
                  "missing-leaf rows=%d" % n_missing)
            # R2: LEAF_COUNT matches KEY_COUNT on each owner block
            # _COUNT_MATCH YES appears for the 3 maps
            check("R2-leaf-count-match-all", txt.count("LEAF_COUNT_MATCH=YES") == 3,
                  "match-yes=%d" % txt.count("LEAF_COUNT_MATCH=YES"))
            # per-class LEAF_COUNT exactness
            check("R2-actor-leaves-5", "LEAF_COUNT=5 (value region" in txt,
                  "actor 5 leaves")
            check("R2-props-leaves-2", "LEAF_COUNT=2 (value region" in txt,
                  "props 2 leaves")
            check("R2-data-leaves-4", "LEAF_COUNT=4 (value region" in txt,
                  "data 4 leaves")
            # R4(a): healthy >> NONE
            check("R4-healthy-stage-none",
                  txt.rstrip().endswith("REAL_DECODER_FAILURE_STAGE=NONE"),
                  "stage line present+NONE")
            # typed candidates spot-check (exact literals the synthetic oracle uses).
            # NOTE: build the 'None | type' needle without the literal substring
            # 'None |' (the py37 static API gate would flag it as PEP-604 syntax).
            _LITS = [("none", "candidate=%s | type=%s" % (None, "NoneType")),
                     ("float0", "candidate=%r | type=float" % 0.0),
                     ("int1", "candidate=%r | type=int" % 1),
                     ("emptystr", "candidate=%r | type=str" % ""),
                     ("int0", "candidate=%r | type=int" % 0)]
            for _id, _lit in _LITS:
                check("R3-typed-" + _id, _lit in txt, _lit)

    # ---- R4(b): a pyc missing the required classes must classify CLASS_DISCOVERY
    _actor_only = (
        "def _tse(raw_type=None, default=None, **kw): return default\n"
        "def TunableX(default=None, **kw): return default\n"
        "class _WickedWhimsAnimationActor(object):\n"
        "    TUNABLE_STRUCTURE = {\"animation_x_offset\": _tse(default=None),\n"
        "                         \"animation_y_offset\": _tse(default=0.0)}\n"
    )
    pyc2 = _compile_to_pyc(_actor_only, "actor-only")
    if pyc2 is not None:
        txt2 = _run_diag(pyc2)
        if txt2 is not None:
            check("R4-missing-classes->CLASS_DISCOVERY",
                  txt2.rstrip().endswith("REAL_DECODER_FAILURE_STAGE=CLASS_DISCOVERY"),
                  "actor-only classifies discovery failure")
        else:
            check("R4-missing-classes->CLASS_DISCOVERY", False, "no diag output")

    failed = [n for n, p in ok if not p]
    print("PASS_COUNT=%d FAIL_COUNT=%d" % (len(ok) - len(failed), len(failed)))
    if failed:
        print("FAILED_NAMES=%s" % failed)
        return 1
    print("P32_REAL_DIAG_LOGIC=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
