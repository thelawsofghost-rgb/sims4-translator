#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ww_p29_tuning_logic_test.py --- OFFLINE semantic test of ww_p29_tuning_mod.

The real Sims 4 / real animations_loader / runtime animation_tuning cannot run on
this sandbox.  So we validate the OBSERVATION logic of the tuning-hook module
against stand-in module + tuning + return-instance shapes that mirror the
authoritative LIVE loader contract:

    _create_sex_animation_instance(animation_tuning, animation_override):
        display_name = animation_tuning.animation_display_name
        ... build a SexAnimationInstance(..., display_name, ...) ...
        return inst

What we prove, offline:
  - the module patches a module-level function by REBINDING the module attribute
    and intercepts subsequent calls (bare-name call style) by calling the original
    with the SAME (animation_tuning, animation_override) args and returning its
    value untouched;
  - it records BEFORE attrs on the SAME tuning object (TUNING_TYPE/TUNING_MODULE/
    RAW_ATTR/DISPLAY_ATTR/ANIMATION_OVERRIDE_PRESENT) and AFTER attrs on the
    returned instance (RETURN_INSTANCE_DISPLAY_NAME / _OVERRIDE / AUTHOR /
    ANIMATION_NAME / ANIMATION_IDENTIFIER), all read-only (never assigns);
  - target-keeping ONLY when raw/display/return carries a known marker, and the
    runtime verdict maps exactly:
        A raw=TEST299 disp=OLD        -> RAW_CHANGED_DISPLAY_DERIVED_OLD
        B raw=OLD     disp=OLD        -> OVERRIDE_NOT_PRESENT_IN_RUNTIME_TUNING
        C raw=TEST299 disp=TEST299 ret=TEST299 -> TUNING_AND_INSTANCE_CORRECT
    and a non-marker call is NOT framed as a target;
  - the discovery/scheduler path arms a real zone retry and, once the loader
    module shows up in sys.modules LATE, a retry installs + emits a HOOK_INSTALLED
    block (no fabricated 'deferred schedule active');
  - restore removes the wrapper (fail-closed) on request.

Exit: 0=PASS, 1=FAIL, 2=unexpected.
"""
import io
import os
import sys
import importlib

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
os.environ["WW_P29_DISABLE_AUTORUN"] = "1"
import ww_p29_tuning_mod as hook  # noqa: E402


def _mk_loader(display_value, raw_value, ret_display, mark_attrs=True):
    """Build a fake animations_loader-ish module whose loader returns a stand-in
    instance whose display_name == ret_display.  Tuning carries the display/raw."""
    inst = type("SexAnimationInstance", (object,), {"__module__": "ww_test"})
    def _loader(animation_tuning=None, animation_override=None):
        t = animation_tuning
        d = getattr(t, "animation_display_name", None)
        name = getattr(t, "animation_name", None)
        idv = getattr(t, "animation_id", None)
        o = inst()
        o.animation_id = idv if mark_attrs else ret_display
        o.display_name = ret_display if ret_display is not None else d
        o.display_name_override = None
        o.author = getattr(t, "author", None)
        o.animation_name = name
        return o
    return _loader


def main():
    failures = []
    def check(cond, label, extra=""):
        print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                              ("  " + extra) if extra else ""))
        if not cond:
            failures.append(label)

    print("=== P29-TUNING OFFLINE LOGIC TEST (sandbox, no TS4) ===")
    hook._reset_state_for_test()

    # ---- A scenario: raw=TEST299, display=OLD (P28C fed raw, display pre-derived) ----
    print("\n-- A) RAW_CHANGED_DISPLAY_DERIVED_OLD --")
    modA = type("animations_loader", (object,), {"__name__": "animations_loader"})
    buf = io.StringIO()
    old_out = sys.stdout
    sys.stdout = buf
    try:
        loader = _mk_loader("Caught Cheating 1", "TEST299", "Caught Cheating 1")
        modA._create_sex_animation_instance = loader
        sys.modules["wickedwhims.sex.animations.animations_loader"] = modA
        ok, mp, fp = hook._try_patch()
        # fabricate a runtime tuning call
        class Tuning(object):
            animation_raw_display_name = "TEST299"
            animation_display_name = "Caught Cheating 1"
            author = "WW"
            animation_name = "Caught Cheating 1"
        r = modA._create_sex_animation_instance(Tuning(), None)
    finally:
        sys.stdout = old_out
    t = buf.getvalue()
    check(ok is True, "patch intercepts animations_loader module fn")
    check(r is not None, "original return preserved (non-None)")
    check("RAW_ATTR='TEST299'" in t, "RAW_ATTR records TEST299")
    check("DISPLAY_ATTR='Caught Cheating 1'" in t, "DISPLAY_ATTR records OLD English")
    check("ANIMATION_OVERRIDE_PRESENT=NO" in t, "override absent recorded NO")
    check("RETURN_INSTANCE_DISPLAY_NAME='Caught Cheating 1'" in t,
          "return instance display recorded")
    check("MATCH=TARGET" in t, "MATCH=TARGET framed")
    check("P29_RESULT=RAW_CHANGED_DISPLAY_DERIVED_OLD" in t,
          "A verdict RAW_CHANGED_DISPLAY_DERIVED_OLD")

    # ---- B scenario: both OLD (override NOT present in runtime tuning) ----
    print("\n-- B) OVERRIDE_NOT_PRESENT_IN_RUNTIME_TUNING --")
    hook._reset_state_for_test()
    modB = type("animations_loader", (object,), {})
    modB._create_sex_animation_instance = _mk_loader("Caught Cheating 1",
                                                     "Caught Cheating 1",
                                                     "Caught Cheating 1")
    sys.modules["wickedwhims.sex.animations.animations_loader"] = modB
    hook._try_patch()
    buf = io.StringIO(); sys.stdout = buf
    try:
        class T2(object):
            animation_raw_display_name = "Caught Cheating 1"
            animation_display_name = "Caught Cheating 1"
        modB._create_sex_animation_instance(T2(), None)
    finally:
        sys.stdout = old_out
    tB = buf.getvalue()
    check(hook._STATE["target_buckets"]["B_override_absent"] >= 1,
          "B bucket incremented")
    check("P29_RESULT=OVERRIDE_NOT_PRESENT_IN_RUNTIME_TUNING" in tB,
          "B verdict OVERRIDE_NOT_PRESENT_IN_RUNTIME_TUNING")

    # ---- C scenario: both TEST299 + return TEST299 ----
    print("\n-- C) TUNING_AND_INSTANCE_CORRECT --")
    hook._reset_state_for_test()
    modC = type("animations_loader", (object,), {})
    modC._create_sex_animation_instance = _mk_loader("TEST299", "TEST299", "TEST299")
    sys.modules["wickedwhims.sex.animations.animations_loader"] = modC
    hook._try_patch()
    buf = io.StringIO(); sys.stdout = buf
    try:
        class T3(object):
            animation_raw_display_name = "TEST299"
            animation_display_name = "TEST299"
        modC._create_sex_animation_instance(T3(), "x")
    finally:
        sys.stdout = old_out
    tC = buf.getvalue()
    check(hook._STATE["target_buckets"]["C_correct"] >= 1, "C bucket incremented")
    check("P29_RESULT=TUNING_AND_INSTANCE_CORRECT" in tC,
          "C verdict TUNING_AND_INSTANCE_CORRECT")
    check("ANIMATION_OVERRIDE_PRESENT=YES" in tC, "override present recorded YES")

    # ---- NON-target not framed as target ----
    print("\n-- non-target NOT kept --")
    hook._reset_state_for_test()
    modN = type("animations_loader", (object,), {})
    modN._create_sex_animation_instance = _mk_loader("Some Other Name",
                                                     "Some Other Name",
                                                     "Some Other Name")
    sys.modules["wickedwhims.sex.animations.animations_loader"] = modN
    hook._try_patch()
    buf = io.StringIO(); sys.stdout = buf
    try:
        class T4(object):
            animation_raw_display_name = "Some Other Name"
            animation_display_name = "Some Other Name"
        modN._create_sex_animation_instance(T4(), None)
    finally:
        sys.stdout = old_out
    tN = buf.getvalue()
    check("MATCH=TARGET" not in tN, "non-marker call NOT framed as TARGET")
    check(hook._STATE["kept"] == 0, "kept==0 for non-marker")

    # ---- read-only: tuning attrs must NOT be reassigned by us ----
    print("\n-- read-only discipline --")
    hook._reset_state_for_test()
    modR = type("animations_loader", (object,), {})
    modR._create_sex_animation_instance = _mk_loader("TEST299", "TEST299", "TEST299")
    sys.modules["wickedwhims.sex.animations.animations_loader"] = modR
    hook._try_patch()
    class T5(object):
        def __init__(self):
            self.animation_raw_display_name = "TEST299"
            self.animation_display_name = "TEST299"
    inst5 = T5()
    modR._create_sex_animation_instance(inst5, None)
    check(inst5.animation_raw_display_name == "TEST299"
          and inst5.animation_display_name == "TEST299",
          "tuning object attrs unchanged by observer (read-only)")

    # ---- TRANSPARENT PASSTHROUGH: omitted-default must reach orig UNCHANGED ----
    # Regression for the first real 18:55 run: the old wrapper authored its own
    # (..., animation_override=None) and forwarded orig(tuning, None) even when the
    # caller omitted the arg -- so a real non-None default (WW sentinel) was
    # clobbered by a literal None -> loader dereferenced the None.  The transparent
    # wrapper must forward *args/**kwargs VERBATIM so orig's OWN default applies.
    print("\n-- transparent passthrough preserves omitted-default semantics --")
    hook._reset_state_for_test()
    SENTINEL = object()
    seen = {}
    def _real_sentinel_loader(animation_tuning, animation_override=SENTINEL):
        # the REAL loader contract: when the arg is omitted it must be SENTINEL
        seen["tuning"] = animation_tuning
        seen["override"] = animation_override
        d = getattr(animation_tuning, "animation_display_name", None)
        o = type("Inst", (object,), {})()
        o.display_name = d
        o.display_name_override = None
        return o
    modP = type("animations_loader", (object,), {})
    modP._create_sex_animation_instance = _real_sentinel_loader
    sys.modules["wickedwhims.sex.animations.animations_loader"] = modP
    buf = io.StringIO(); sys.stdout = buf
    try:
        okP, _, _ = hook._try_patch()
        class Tp(object):
            animation_raw_display_name = "TEST299"
            animation_display_name = "TEST299"
        # CALLER OMITS the second arg entirely (the real WW call style that broke)
        modP._create_sex_animation_instance(Tp())
    finally:
        sys.stdout = old_out
    tP = buf.getvalue()
    check(okP is True, "patch intercepts sentinel-default loader")
    check(seen.get("override") is SENTINEL,
          "omitted second arg reaches orig as ITS OWN SENTINEL (not synthetic None)",
          "OMITTED_DEFAULT_PRESERVED=YES")
    check(seen.get("override") is not None, "orig did NOT receive a literal None")
    check(seen.get("tuning") is not None,
          "first positional tuning forwarded unchanged")
    check("ANIMATION_OVERRIDE_PRESENT=OMITTED" in tP,
          "observer logs OMITTED (never injects None)")
    check("PASSTHROUGH_MODE=YES" in tP, "install records PASSTHROUGH_MODE=YES")
    check("ORIG_DEFAULTS=" in tP, "install records ORIG_DEFAULTS digest")
    check("ORIG_SIGNATURE=" in tP, "install records ORIG_SIGNATURE digest")
    sys.modules.pop("wickedwhims.sex.animations.animations_loader", None)
    hook._restore_all()

    # ...and an explicit-keyword call is forwarded unchanged (kwargs untouched)
    print("\n-- passthrough leaves kwargs unchanged --")
    hook._reset_state_for_test()
    seen2 = {}
    def _real_kw_loader(animation_tuning, animation_override=SENTINEL):
        seen2["tuning"] = animation_tuning
        seen2["override"] = animation_override
        return type("Inst", (object,), {})()
    modK = type("animations_loader", (object,), {})
    modK._create_sex_animation_instance = _real_kw_loader
    sys.modules["wickedwhims.sex.animations.animations_loader"] = modK
    hook._try_patch()
    class Tk(object):
        animation_display_name = "TEST299"
        animation_raw_display_name = "TEST299"
        animation_name = None
        animation_id = None
    _obj = object()
    _tk_inst = Tk()
    modK._create_sex_animation_instance(animation_override=_obj, animation_tuning=_tk_inst)
    check(seen2.get("override") is _obj and seen2.get("tuning") is _tk_inst,
          "explicit keyword args forwarded unchanged (PASSTHROUGH_KWARGS_UNCHANGED=YES)")
    sys.modules.pop("wickedwhims.sex.animations.animations_loader", None)


    # ---- scheduler/discovery: arm then late-load then HOOK_INSTALLED ----
    print("\n-- scheduler arms; retry installs on late module load --")
    hook._reset_state_for_test()
    hook._STATE["_log_path"] = ""
    class _FZ(object):
        def __init__(self):
            self.cb = None
        def on_loading_screen_ended(self, cb):
            self.cb = cb
    fz = _FZ()
    fake_services = type("services", (object,), {})
    fake_services.current_zone = lambda: fz
    fake_sims4 = type("sims4", (object,), {})
    sys.modules["services"] = fake_services
    sys.modules["sims4"] = fake_sims4
    armed = hook._register_scheduler()
    check(armed is True, "scheduler arms when a zone host is present")
    buf = io.StringIO(); sys.stdout = buf
    try:
        # WW loads animations_loader LATE (after our boot): simulate + fire cb
        late = type("animations_loader", (object,), {})
        late._create_sex_animation_instance = _mk_loader("TEST299", "TEST299", "TEST299")
        sys.modules["wickedwhims.sex.animations.animations_loader"] = late
        fz.cb()
    finally:
        sys.stdout = old_out
    tS = buf.getvalue()
    check("RETRY_INDEX=1" in tS, "retry emits RETRY_INDEX=1")
    check("HOOK_INSTALLED=YES" in tS, "HOOK_INSTALLED=YES after late load")
    check("HOOK_MODULE=wickedwhims.sex.animations.animations_loader" in tS,
          "HOOK_MODULE=animations_loader")
    for k in list(sys.modules):
        if k.startswith("wickedwhims"):
            sys.modules.pop(k, None)
    sys.modules.pop("services", None)
    sys.modules.pop("sims4", None)

    # ---- restore removes wrapper ----
    print("\n-- restore --")
    hook._reset_state_for_test()
    modZ = type("animations_loader", (object,), {})
    modZ._create_sex_animation_instance = _mk_loader("TEST299", "TEST299", "TEST299")
    sys.modules["wickedwhims.sex.animations.animations_loader"] = modZ
    hook._try_patch()
    wrapper = modZ._create_sex_animation_instance
    hook._restore_all()
    check(modZ._create_sex_animation_instance is not wrapper
          and callable(modZ._create_sex_animation_instance),
          "restore removes wrapper binding")
    sys.modules.pop("wickedwhims.sex.animations.animations_loader", None)

    print("")
    if failures:
        print("P29_TUNING_LOGIC_TEST=FAIL")
        for f in failures:
            print("  missing: %s" % f)
        return 1
    print("P29_TUNING_LOGIC_TEST=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
