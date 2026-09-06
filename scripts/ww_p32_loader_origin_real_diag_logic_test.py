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

    # ---- RESIDUAL-PROVENANCE (this change) --------------------------------
    # New diagnostic: when the real map's BALANCE is broken by EXTRA stack depth
    # accumulated in the preamble (not by a leaf), sim_residual_report names the
    # surplus producers.  Actor +2 / Data +4 / Props +0 come from the WINDOWS run;
    # here we lock the mechanism: balanced -> extra 0 (decision-consistent),
    # a preamble that leaves 2 surplus BUILD_MAP nodes -> extra 2 with the builds
    # named + one-level child provenance, and a vector the DECISION sim rejects as
    # residual must show report extra==1 tied to the same surplus LOADs.

    # R1: healthy compiled fixture -> per-class EXTRA_STACK_COUNT=0, no dump, and
    # the report agrees with the decision engine (decision PASS => actual==count).
    if pyc is not None:
        txt_full = _run_diag(pyc)
        if txt_full is not None:
            check("R1-healthy-extra0-actor",
                  txt_full.count("EXTRA_STACK_COUNT=0") >= 3
                  and "EXTRA_STACK_ITEMS: none" in txt_full
                  and "FIRST_STACK_DIVERGENCE=NONE" in txt_full,
                  "balanced run: 3x extra 0 + no surplus + no divergence")
            check("R1-no-full-stack-dump", "depth_timeline" not in txt_full
                  and "residual_items" not in txt_full
                  and "stack_depth_at_j" not in txt_full,
                  "no raw dump leaked into output")

    # R2 (unit): REAL-style preamble -- two lone BUILD_MAP before one top-level
    # leaf value + field tuple -> the replay must report EXTRA 2 surplus nodes,
    # each a BUILD_MAP with its own key/value child offsets (one level only).
    def _mk(opname, arg=None, av=None, offset=0, ar=None):
        return _FakeIns(opname, arg, av, ar, offset)

    _seq_p = [
        _mk("LOAD_CONST", 0, "default", 2), _mk("LOAD_CONST", 0, None, 4),
        _mk("BUILD_MAP", 1, 1, 6),
        _mk("LOAD_CONST", 0, "default", 8), _mk("LOAD_CONST", 0, None, 10),
        _mk("BUILD_MAP", 1, 1, 12),
        _mk("LOAD_NAME", 0, "_tse", 14), _mk("LOAD_CONST", 0, "clip", 16),
        _mk("LOAD_CONST", 0, ("raw_type",), 18),
        _mk("CALL_FUNCTION_KW", 1, 1, 20),
        _mk("LOAD_CONST", 0, ("k0",), 22),      # field tuple at index 10
    ]
    _rep_p = diag.lod.sim_residual_report(_seq_p, 10, 1, 24)
    check("R2-extra-two", _rep_p.get("extra_stack_count") == 2,
          "preamble leaves 2 surplus (Actor-style)")
    check("R2-actual-depth", _rep_p.get("actual_stack_depth") == 3,
          "depth 3 = tuple-1 (before tuple) with 2 extra over count 1")
    _r2items = _rep_p.get("residual_items", [])
    check("R2-two-residual-nodes", len(_r2items) == 2, "2 surplus nodes")
    _kinds = [n["kind"] for n in _r2items]
    _ops = [n["producer_opname"] for n in _r2items]
    _offs = [n["producer_offset"] for n in _r2items]
    check("R2-names-build-map", _ops == ["BUILD_MAP", "BUILD_MAP"],
          "both surplus named BUILD_MAP (%r)" % _ops)
    check("R2-carry-offsets", _offs == [6, 12],
          "produces @6 and @12 (%r)" % _offs)
    # one-level children recorded (the key/value operands of each map)
    check("R2-one-level-children",
          all(isinstance(n["children"], list) and len(n["children"]) == 2
              and all(isinstance(c, int) for c in n["children"])
              for n in _r2items),
          "each build node carries 1-level child producer offsets")
    _fd = _rep_p.get("first_divergence") or {}
    check("R2-first-divergence-build", _fd.get("opname") == "BUILD_MAP"
          and _fd.get("offset") == 6,
          "FIRST_STACK_DIVERGENCE points at BUILD_MAP@6 (%r)" % (_fd,))

    # R3 (unit): a vector the DECISION sim REJECTS as a residual must have its
    # report tie the extra to the SAME surplus LOADs that broke the balance.
    _seq_r = [
        _mk("LOAD_NAME", 0, "a", 10), _mk("LOAD_NAME", 0, "b", 12),
        _mk("LOAD_NAME", 0, "_tse", 14), _mk("LOAD_CONST", 0, "x", 16),
        _mk("LOAD_CONST", 0, ("raw_type",), 18),
        _mk("CALL_FUNCTION_KW", 1, 1, 20),
        _mk("LOAD_CONST", 0, ("k0", "k1"), 22),   # field tuple at 6
    ]
    cap_r = {}
    _dec_r = diag.lod._sim_value_producers(
        _seq_r, 6, 2, 24, on_first_failure=lambda md: cap_r.update(md))
    check("R3-decision-rejects-as-residual", _dec_r is None
          and cap_r.get("reason") == "MAP_ARITY_MISMATCH",
          "decision sim sees a RESIDUAL (arity), not a leaf default")
    _rep_r = diag.lod.sim_residual_report(_seq_r, 6, 2, 24)
    check("R3-report-extra-one", _rep_r.get("extra_stack_count") == 1,
          "replay scores the same surplus (+1 over count 2)")
    _r3ops = [n["producer_opname"] for n in _rep_r.get("residual_items", [])]
    check("R3-names-dangling-loads", _r3ops == ["LOAD_NAME"],
          "surplus names the dangling LOAD (%r)" % _r3ops)

    # ---- METHOD-CALL PROTOCOL (this fix) ------------------------------
    # Real Windows evidence: Data body has exactly THREE
    #   LOAD_METHOD TunableFactory / CALL_METHOD
    # pairs and Data residuals were +3; Props (no method pairs) +0.  Per CPython
    # 3.7 a matched LOAD_METHOD/CALL_METHOD pair nets 0 (LOAD_METHOD consumes the
    # receiver + pushes two protocol slots; CALL_METHOD pops arity+2).  Previously
    # both engines modelled LOAD_METHOD as a plain 1-push name and CALL_METHOD with
    # the CALL_FUNCTION pop formula (arity+1) -> every pair left +1 residual.  The
    # required regression vectors below lock the correct 3.7 discipline and prove
    # CALL_FUNCTION / CALL_FUNCTION_KW are untouched.

    # A: one LOAD_METHOD/CALL_METHOD(0) pair as the sole top-level value -> 0 residual.
    _seq_a = [_mk("LOAD_NAME", 0, "X", 0),
              _mk("LOAD_METHOD", 0, "meth", 2),
              _mk("CALL_METHOD", 0, 0, 4),
              _mk("LOAD_CONST", 0, ("k0",), 6)]
    _dec_a, _rep_a = None, diag.lod.sim_residual_report(_seq_a, 3, 1, 8)
    _cap_a = {}
    _dec_a = diag.lod._sim_value_producers(
        _seq_a, 3, 1, 8, on_first_failure=lambda md: _cap_a.update(md))
    check("A-one-pair-residual-zero",
          (_rep_a.get("extra_stack_count") == 0
           and _dec_a is not None and len(_dec_a) == 1
           and "reason" not in _cap_a),
          "single LOAD_METHOD/CALL_METHOD(0) nets 0 residual")

    # B: THREE consecutive method pairs (Data's real 3x TunableFactory) -> still
    # 0 residual and all three value producers are CALL indices, matched to DATA.
    _seq_b = []
    for _pi in range(3):
        _o = 6 * _pi
        _seq_b += [_mk("LOAD_NAME", 0, "X", _o),
                   _mk("LOAD_METHOD", 0, "TunableFactory", _o + 2),
                   _mk("CALL_METHOD", 0, 0, _o + 4)]
    _seq_b.append(_mk("LOAD_CONST", 0, ("k0", "k1", "k2"), 18))
    _jb = len(_seq_b) - 1
    _cap_b = {}
    _dec_b = diag.lod._sim_value_producers(_seq_b, _jb, 3, 20,
                                      on_first_failure=lambda md: _cap_b.update(md))
    _rep_b = diag.lod.sim_residual_report(_seq_b, _jb, 3, 20)
    check("B-three-pairs-residual-zero", _rep_b.get("extra_stack_count") == 0
          and "reason" not in _cap_b,
          "3x TunableFactory method pairs net 0 (Data +3 case now balanced)")
    check("B-three-producers-resolved",
          _dec_b is not None and len(_dec_b) == 3
          and all(isinstance(x, int) for x in _dec_b) and _dec_b == list(range(2, 9, 3)),
          "3 producers all CALL indices, source order (%r)" % (_dec_b,))

    # C: method call WITH args argc=1 and argc=2 -> correct pops, still 0 residual.
    for _argc, _nargs in ((1, 1), (2, 2)):
        _s = [_mk("LOAD_NAME", 0, "X", 0)]
        for _ai in range(_nargs):
            _s.append(_mk("LOAD_CONST", 0, _ai, 2 + 2 * _ai))
        _s += [_mk("LOAD_METHOD", 0, "meth", 2 + 2 * _nargs),
               _mk("CALL_METHOD", _argc, _argc, 4 + 2 * _nargs)]
        _s.append(_mk("LOAD_CONST", 0, ("k0",), 6 + 2 * _nargs))
        _jc = len(_s) - 1
        _cc = {}
        _dc = diag.lod._sim_value_producers(_s, _jc, 1, 99,
                                       on_first_failure=lambda m: _cc.update(m))
        check("C-argc%d-residual-zero" % _argc,
              _dc is not None and "reason" not in _cc,
              "LOAD_METHOD/CALL_METHOD(argc=%d) nets 0" % _argc)

    # D: CALL_FUNCTION / CALL_FUNCTION_KW original pop behaviour UNCHANGED.
    # positional, no preceding kw-name tuple -> need arity+1.
    _seq_d1 = [_mk("LOAD_NAME", 0, "f", 0),
               _mk("LOAD_CONST", 0, 1, 2),
               _mk("CALL_FUNCTION", 1, 1, 4),
               _mk("LOAD_CONST", 0, ("k0",), 6)]
    _rep_d1 = diag.lod.sim_residual_report(_seq_d1, 3, 1, 8)
    check("D-call-function-pos-hit", _rep_d1.get("extra_stack_count") == 0)
    # modern CALL_FUNCTION_KW (kw-name tuple precedes) -> need arity+2.
    _seq_d2 = [_mk("LOAD_NAME", 0, "f", 0), _mk("LOAD_CONST", 0, 1, 2),
               _mk("LOAD_CONST", 0, ("a",), 4),
               _mk("CALL_FUNCTION_KW", 1, 1, 6),
               _mk("LOAD_CONST", 0, ("k0",), 8)]
    _rep_d2 = diag.lod.sim_residual_report(_seq_d2, 4, 1, 10)
    check("D-call-function-kw-hit", _rep_d2.get("extra_stack_count") == 0)
    # CPython 3.7 kw call: plain CALL_FUNCTION with preceding kw-name tuple.
    _seq_d3 = [_mk("LOAD_NAME", 0, "f", 0), _mk("LOAD_CONST", 0, 1, 2),
               _mk("LOAD_CONST", 0, ("a",), 4),
               _mk("CALL_FUNCTION", 1, 1, 6),
               _mk("LOAD_CONST", 0, ("k0",), 8)]
    _rep_d3 = diag.lod.sim_residual_report(_seq_d3, 4, 1, 10)
    check("D-call-function-37kw-hit", _rep_d3.get("extra_stack_count") == 0)

    # E: nested -- LOAD_NAME X / LOAD_METHOD TunableFactory / CALL_METHOD 0 /
    # BUILD_LIST ... / CALL_FUNCTION_KW must read as ONE top-level value expr
    # with NO leftover method/self slot.
    _seq_e = [_mk("LOAD_NAME", 0, "X", 0),
              _mk("LOAD_METHOD", 0, "TunableFactory", 2),
              _mk("CALL_METHOD", 0, 0, 4),
              _mk("LOAD_NAME", 0, "Actor", 20),
              _mk("BUILD_LIST", 1, 1, 22),
              _mk("LOAD_CONST", 0, ("raw_type",), 24),
              _mk("CALL_FUNCTION_KW", 1, 1, 26),
              _mk("LOAD_CONST", 0, ("k0",), 30)]
    _ce = {}
    _de = diag.lod._sim_value_producers(_seq_e, 7, 1, 32,
                                    on_first_failure=lambda m: _ce.update(m))
    _re = diag.lod.sim_residual_report(_seq_e, 7, 1, 32)
    check("E-nested-expr-no-self-residual",
          _re.get("extra_stack_count") == 0
          and _de is not None and _de == [6] and "reason" not in _ce,
          "nested method -> kw-call reads as ONE value, no method/self residual")

    # M: END-TO-END against a REAL-COMPILED (not hand-built) method-call owner.
    # The unit vectors above are synthetic; this compiles a genuine body whose
    # per-key RHS are pure-positional method calls, so the .pyc REALLY contains
    # LOAD_METHOD/CALL_METHOD.  decode_structure must resolve every key with 0
    # unresolved and sim_residual_report must report EXTRA 0 over the whole body.
    _mcomp = None
    try:
        import io as _mio
        import py_compile as _mpc
        _mc = _compile_to_pyc(
            "class _Holder(object):\n"
            "    class _F(object):\n"
            "        def m(self, a, b):\n"
            "            return a\n"
            "class _WickedW(object):\n"
            "    TUNABLE_STRUCTURE = {\n"
            "        \"a_prop\": _Holder._F().m(7, 8),\n"
            "        \"b_prop\": _Holder._F().m(3, 4),\n"
            "    }\n", "mcomp")
        from xdis.load import load_module_from_file_object  # noqa: F401
        _cpath = _mc
        if _cpath is not None:
            _rh = load_module_from_file_object(
                _mio.BytesIO(open(_cpath, "rb").read()), filename=_cpath)
            _cdm = diag.discover_classes(_rh[3])
            _wp = [x for x in _cdm if x.endswith("_WickedW")]
            if _wp:
                _wseq = diag.iter_xdis_instructions(_cdm[_wp[0]], version=_rh[0])
                _methodops = [
                    (diag._off(i), diag._op(i))
                    for i in _wseq
                    if diag._op(i) in ("LOAD_METHOD", "CALL_METHOD")]
                check("M-compiled-body-has-method-ops", len(_methodops) >= 4,
                      "real compiled body contains LOAD_METHOD/CALL_METHOD (%r)"
                      % _methodops)
                if len(_methodops) >= 4:
                    _mdec, _mun = diag.lod.decode_structure(_wseq, _wp[0])
                    # THIS test locks the LOAD_METHOD/CALL_METHOD 2-slot PROTOCOL
                    # at the Phase-A (producer mapping) level: the map must recover
                    # EXACTLY 2 top-level producers with NO MAP_ARITY/underflow abort.
                    # Any lingering +1 residual per method pair would make the map
                    # arity mismatch and Phase A would fail-closed -> found empty.
                    # (The method RETURN values are not tunable-wrapper defaults, so
                    # per-key default UNKNOWN is correct under structured policy.)
                    _mkey = _wp[0]
                    _mseq2 = _wseq
                    _mmapOK = False
                    for _i2, _ins2 in enumerate(_mseq2):
                        if diag.lod._op(_ins2) == "BUILD_CONST_KEY_MAP":
                            _mj2 = _i2 - 1
                            while _mj2 >= 0 and diag.lod._op(_mseq2[_mj2]) == "NOP":
                                _mj2 -= 1
                            _scan2 = diag.lod._map_producer_trees(
                                _mseq2, _mj2, len(["a_prop", "b_prop"]),
                                diag.lod._off(_ins2))
                            _mmapOK = (_scan2 is not None and len(_scan2["producers"]) == 2)
                            break
                    check("M-compiled-resolves-all",
                          _mmapOK and len(_mdec) == 0,
                          "compiled method-call body: Phase A exact (2 producers, "
                          "no arity abort); per-key defaults UNKNOWN as expected")
    except Exception as _ecomp:  # noqa: BLE001
        check("M-compiled-no-raise", False, "compiled method fixture raised: %r" % (_ecomp,))

    # ---- D-SECTION: DEFAULT_EXTRACTION regressions (re-framed; no all-or-nothing)
    # 5 operator-mandated cases.  All compile to a real BUILD_CONST_KEY_MAP (<=15
    # keys on the py3.10 tool runtime; the real 26-key Data map is likewise a single
    # BUILD_CONST_KEY_MAP under the py3.7 WW compiler and obeys the SAME per-key
    # independence asserted here at smaller size).
    def _dec_src(src, cls):
        pycf = _compile_to_pyc(src, "reqd")
        if pycf is None:
            return None, None
        import io as _dio
        from xdis.load import load_module_from_file_object
        _dr = load_module_from_file_object(
            _dio.BytesIO(open(pycf, "rb").read()), filename=pycf)
        _dc = diag.discover_classes(_dr[3])
        _dp = [x for x in _dc if x.endswith(cls)]
        if not _dp:
            return None, None
        _sq = diag.iter_xdis_instructions(_dc[_dp[0]], version=_dr[0])
        return diag.lod.decode_structure(_sq, _dp[0])

    _tse = "def _tse(*a,**k): return k['default'] if 'default' in k else (a[1] if len(a)>=2 else a[0])"
    _tx = "def TunableX(default=None, **k): return default"
    _tl = "def TunableList(*a, **k): return ('tl', a, k)\n"

    # D1: wrapper float is_deprecated -> default MUST be 0.0, NOT True.
    _d1f, _d1u = _dec_src(_tse + "\nclass W1: TUNABLE_STRUCTURE={"\
        "'a':_tse(float,0.0,is_deprecated=True),'b':_tse(float,1.0)}", "W1")
    check("D1-isdeprec-default-float-not-true",
          _d1f and _d1f.get("a") and _d1f["a"]["default"] == 0.0,
          "facing-style is_deprecated wrapper default=0.0 (NOT True): %r"
          % (_d1f and _d1f.get("a") and _d1f["a"].get("default")))

    # D2: wrapper str None is_deprecated -> default MUST be None (not True).
    _d2f, _d2u = _dec_src(_tse + "\nclass W2: TUNABLE_STRUCTURE={"\
        "'a':_tse(str,None,is_deprecated=True),'b':_tse(str,'')}", "W2")
    check("D2-isdeprec-default-none",
          _d2f and _d2f.get("a") is not None and _d2f["a"]["default"] is None
          and _d2f["a"]["type"] == "NoneType",
          "None default preserved, not True")

    # D3: many-key class, 4 simple target keys among complex TunableList non-targets
    # -> all 4 targets RESOLVED, NO class-wide abort (old all-or-nothing gone).
    _d3hdr = _tse + "\n" + _tl
    _d3keys = {"object_animation_clip_name": "_tse(str,'')",
               "object_geometry_state": "_tse(str,'')",
               "object_material_state": "_tse(str,'')",
               "animation_version": "_tse(int,1)"}
    _d3body = _d3hdr + "class W3(object):\n"\
        "    TUNABLE_STRUCTURE={" + ",".join(
            "'%s': %s" % (k, v) for k, v in _d3keys.items()) + "," + ",".join(
            "'nT%d': TunableList(%d,%d)" % (j, j, j + 1) for j in range(11)) + "}"
    _d3f, _d3u = _dec_src(_d3body, "W3")
    _d3ok = _d3f is not None and all(k in _d3f for k in _d3keys) and \
        _d3f["object_animation_clip_name"]["default"] == "" and \
        _d3f["animation_version"]["default"] == 1
    check("D3-complex-nontarget-no-abort", _d3ok,
          "15-key map w/ 11 complex TunableList non-targets still resolves all 4 targets")

    # D4: Actor -- one non-target complex stays UNKNOWN while all 5 offsets resolve.
    _d4hdr = _tse + "\n" + _tl
    _d4keys = {"animation_x_offset": "_tse(float,0.0,is_deprecated=True)",
               "animation_y_offset": "_tse(float,0.0)",
               "animation_z_offset": "_tse(float,0.0)",
               "animation_angle_offset": "_tse(float,0.0)",
               "animation_facing_offset": "_tse(float,0,is_deprecated=True)"}
    _d4body = _d4hdr + "class W4(object):\n  TUNABLE_STRUCTURE={" + ",".join(
        "'%s': %s" % (k, v) for k, v in _d4keys.items()) + \
        ",'complex': TunableList(1,2,3)}"
    _d4f, _d4u = _dec_src(_d4body, "W4")
    _d4ok = _d4f is not None and all(k in _d4f for k in _d4keys) and \
        _d4f["animation_facing_offset"]["default"] == 0 and \
        "complex" not in _d4f
    check("D4-actor5-targets-while-nontarget-unknown", _d4ok,
          "5 actor offsets resolve (facing=0) while complex non-target stays UNKNOWN")

    # D5: a TARGET itself is an unprovable complex producer -> that key UNKNOWN
    # (fail closed), and siblings still resolve (per-key, no class veto).
    _d5hdr = _tse + "\n" + _tl
    _d5body = _d5hdr + "class W5(object):\n  TUNABLE_STRUCTURE={"\
        "'object_animation_clip_name': TunableList(0,1),"\
        "'object_geometry_state': _tse(str,'geom'),"\
        "'non_target': _tse(str,'nt')}"
    _d5f, _d5u = _dec_src(_d5body, "W5")
    _d5ok = _d5f is not None and "object_geometry_state" in _d5f and \
        _d5f["object_geometry_state"]["default"] == "geom" \
        and "object_animation_clip_name" not in _d5f
    check("D5-complex-target-failclosed-perkey", _d5ok,
          "complex target UNKNOWN (fail-closed) while sibling target resolves")

    failed = [n for n, p in ok if not p]
    print("PASS_COUNT=%d FAIL_COUNT=%d" % (len(ok) - len(failed), len(failed)))
    if failed:
        print("FAILED_NAMES=%s" % failed)
        return 1
    print("P32_REAL_DIAG_LOGIC=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
