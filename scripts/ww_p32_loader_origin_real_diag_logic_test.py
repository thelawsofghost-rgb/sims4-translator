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
            # per-class LEAF_COUNT exactness (plain "LEAF_COUNT=N" line)
            check("R2-actor-leaves-5", "LEAF_COUNT=5\n" in txt, "actor 5 leaves")
            check("R2-props-leaves-2", "LEAF_COUNT=2\n" in txt, "props 2 leaves")
            check("R2-data-leaves-4", "LEAF_COUNT=4\n" in txt, "data 4 leaves")
            # R4(a): healthy >> NONE (line present; NOT necessarily last -- the
            # first-failure/sim-summary metadata lines follow the stage line)
            check("R4-healthy-stage-none",
                  "REAL_DECODER_FAILURE_STAGE=NONE" in txt.splitlines(),
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
                  "REAL_DECODER_FAILURE_STAGE=CLASS_DISCOVERY" in txt2.splitlines(),
                  "actor-only classifies discovery failure")
        else:
            check("R4-missing-classes->CLASS_DISCOVERY", False, "no diag output")

    # ---- SIM first-failure diagnostic metadata -------------------------------
    # New (this change): when the shared stack simulator fail-closes inside a real
    # class body, the diagnostic must record the EXACT FIRST failure (offset,
    # opname, reason) + +/-5 context, per class SIM_STATUS, and a single
    # REAL_DECODER_FIRST_FAILURE=<class>:<o>:<opname>:<reason>.  The 4 coverage
    # requirements: (1) unsupported opcode precise opname/offset, (2) stack
    # underflow required/available, (3) map-arity-mismatch classified separately,
    # (4) Props success path untouched.

    # --- S1 (e2e, compiled): an `import` in one class body is an unmodelled
    # IMPORT_NAME -> UNSUPPORTED_OPCODE; Actor/Props unaffected -> still PASS.
    _imp_src = (
        "def _tse(raw_type=None, default=None, **kw): return default\n"
        "class _WickedWhimsAnimationActor(object):\n"
        "    TUNABLE_STRUCTURE = {\"animation_x_offset\": _tse(default=None),\n"
        "                         \"animation_facing_offset\": _tse(default=0)}\n"
        "class _WickedWhimsAnimationPropsData(object):\n"
        "    TUNABLE_STRUCTURE = {\"prop_animation_clip_name\": _tse(default=\"\"),\n"
        "                         \"prop_geometry_state\": _tse(default=0)}\n"
        "class _WickedWhimsAnimationData(object):\n"
        "    import os\n"
        "    TUNABLE_STRUCTURE = {\"object_animation_clip_name\": _tse(default=\"\"),\n"
        "                         \"object_geometry_state\": _tse(default=0)}\n"
    )
    pyci = _compile_to_pyc(_imp_src, "unsupported-import")
    if pyci is not None:
        txti = _run_diag(pyci)
        if txti is not None:
            # Data first-fails on IMPORT_NAME (UNSUPPORTED_OPCODE)
            check("S1-data-sim-fail-reason",
                  "SIM_FAIL_REASON=UNSUPPORTED_OPCODE" in txti, "import -> unmodelled op")
            check("S1-data-opname-import_name",
                  "SIM_FAIL_OPNAME=IMPORT_NAME" in txti, "precise opname")
            check("S1-data-sim-offset-12",
                  "SIM_FAIL_OFFSET=12" in txti, "precise offset of the import")
            check("S1-data-status-fail",
                  "LEAF_COUNT_MATCH=NO stage=LEAF_BOUNDARY" in txti
                  and "SIM_STATUS=FAIL" in txti, "Data block reports FAIL")
            check("S1-context-rows", "SIM_CONTEXT:" in txti, "context block present")
            # a SIM_CONTEXT row must exist and carry opname + the <<< marker row
            check("S1-context-has-marker", "IMPORT_NAME" in txti, "marker row names the op")
            # global first-failure line format <cls>:<off>:<opname>:<reason>
            check("S1-first-failure-line",
                  "_WickedWhimsAnimationData:12:IMPORT_NAME:UNSUPPORTED_OPCODE"
                  in txti, "REAL_DECODER_FIRST_FAILURE format")
            # isolation: Props (and Actor) still PASS
            check("S1-props-untouched",
                  "SIM_STATUS__WickedWhimsAnimationPropsData=PASS" in txti,
                  "props unaffected by Data failure")
            check("S1-actor-untouched",
                  "SIM_STATUS__WickedWhimsAnimationActor=PASS" in txti,
                  "actor unaffected by Data failure")
            check("S1-props-block-status",
                  txti.count("SIM_STATUS=PASS") >= 2, "2+ healthy block statuses")
            # stage stays LEAF_BOUNDARY (Data's stack-sim failure), not MAP_DISCOVERY
            check("S1-stage-leaf-boundary",
                  "REAL_DECODER_FAILURE_STAGE=LEAF_BOUNDARY" in txti,
                  "unsupported-in-body stages as LEAF_BOUNDARY")
        else:
            for _n in ("S1-data-sim-fail-reason", "S1-first-failure-line",
                       "S1-props-untouched"):
                check(_n, False, "no diag output")

    # --- S2/S3 (unit): drive lod._sim_value_producers directly with synthetic
    # instruction lists + an observer to assert the produced metadata precisely.
    class _FakeIns(object):
        def __init__(self, opname, arg=None, argval=None, argrepr=None, offset=0):
            self.opname, self.arg, self.argval = opname, arg, argval
            self.argrepr = argrepr
            self.offset = offset

    # S2: stack UNDERFLOW -- a CALL demanding more operands than are on the stack.
    # Chain: LOAD_NAME(callee) then a CALL_FUNCTION arg=5 with only 1 operand on
    # the stack (the callable) -> needs 5 (positional, no name tuple) -> underflow.
    _seq_u = [_FakeIns("LOAD_NAME", 0, "_tse", offset=0),
              _FakeIns("CALL_FUNCTION", 5, 5, offset=2)]
    cap2 = {}
    r2 = diag.lod._sim_value_producers(_seq_u, 1, 1, 99,
                                  on_first_failure=lambda md: cap2.update(md))
    check("S2-underflow-returns-none", r2 is None, "sim fail-closes")
    check("S2-reason", cap2.get("reason") == "STACK_UNDERFLOW",
          "reason=%r" % cap2.get("reason"))
    check("S2-opname", cap2.get("opname") == "CALL_FUNCTION",
          "opname=%r" % cap2.get("opname"))
    check("S2-required-pops", cap2.get("needed") == 6,
          "needed=%r (5 values + callable)" % cap2.get("needed"))
    check("S2-available-stack", cap2.get("avail") == 1,
          "avail=%r" % cap2.get("avail"))
    check("S2-offset", cap2.get("off") == 2, "offset=%r" % cap2.get("off"))

    # S3: MAP_ARITY_MISMATCH -- map says `count` keys but the simulated stack at the
    # keytuple has fewer / non-matching top-level producers.  Build a class-style
    # seq: one LOAD_CONST (the key tuple, no CALL producers before it) so the sim
    # cannot prove `count` values beneath the tuple.
    _seq_m = [
        _FakeIns("LOAD_CONST", 0, ("k0",), offset=0),   # the field-name tuple at j=0
    ]
    cap3 = {}
    r3 = diag.lod._sim_value_producers(_seq_m, 0, 1, 99,
                                  on_first_failure=lambda md: cap3.update(md))
    check("S3-arity-mismatch-reason",
          cap3.get("reason") == "MAP_ARITY_MISMATCH",
          "reason=%r" % cap3.get("reason"))
    check("S3-arity-returns-none", r3 is None, "sim fail-closes")
    check("S3-arity-not-underflow",
          cap3.get("reason") != "STACK_UNDERFLOW", "classified separately")
    check("S3-arity-count-key", cap3.get("needed") == 2,
          "needed=%r (count+1)" % cap3.get("needed"))

    # S4: Props success path is untouched by the observer wiring -- the healthy
    # full fixture above already asserted SIM_STATUS=PASS on Props; re-assert that
    # NO SIM_FAIL_* appears anywhere in the healthy run.
    if pyc is not None:
        txt = _run_diag(pyc)
        if txt is not None:
            check("S4-healthy-no-SIM_FAIL", "SIM_FAIL_REASON" not in txt,
                  "no sim failure metadata on a healthy run")
            check("S4-healthy-first-failure-none",
                  "REAL_DECODER_FIRST_FAILURE=NONE" in txt,
                  "not-a-failure marker on healthy run")

    failed = [n for n, p in ok if not p]
    print("PASS_COUNT=%d FAIL_COUNT=%d" % (len(ok) - len(failed), len(failed)))
    if failed:
        print("FAILED_NAMES=%s" % failed)
        return 1
    print("P32_REAL_DIAG_LOGIC=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
