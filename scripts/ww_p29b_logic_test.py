#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ww_p29b_logic_test.py --- OFFLINE semantic test of ww_p29b_display_trace.py.

The real WW SexAnimationInstance + picker row + sims localization cannot run here.  We
stand-in a class named SexAnimationInstance with the real method shapes:

    get_display_name(self, string_hash, original) -> str
    get_picker_row(self, ...) -> some row object

and prove, offline:
  1. the module DISCOVERS the class and hooks BOTH methods transparently (calls the
     ORIGINAL via orig(*args,**kwargs) and forwards return unchanged);
  2. TARGET matching is by display_name/author/name VALUE (not animation_id);
  3. for a target instance get_display_name emits the observed fields and the named
     root-cause verdicts:
        BASE_DISPLAY_NAME / DISPLAY_NAME_OVERRIDE / ORIGINAL_INSTANCE_PRESENT /
        ORIGINAL_INSTANCE_DISPLAY_NAME / ARG_STRING_HASH / ARG_ORIGINAL /
        GET_DISPLAY_NAME_RETURN   and P29B_RESULT in
        {UI_USING_ORIGINAL_INSTANCE, DISPLAY_NAME_OVERRIDE_WINS,
         GET_DISPLAY_NAME_IS_SWITCH}
  4. TRANSPARENT passthrough: a SENTINEL -default method reaches the original with its
     OWN default (omitted-default semantics preserved; no authored None);
  5. get_picker_row records PICKER_INSTANCE_DISPLAY_NAME / _OVERRIDE / ROW_TEXT / NAME /
     DESCRIPTION (UNAVAILABLE when the row lacks them); a row with plain-str text that
     equals the old English while instance base is TEST299 -> PICKER_ROW_USES_OTHER_SOURCE;
  6. non-target instances are NOT framed.
  7. restore removes both method wrappers.

Exit: 0=PASS, 1=FAIL, 2=unexpected.
"""
import io
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
os.environ["WW_P29_DISABLE_AUTORUN"] = "1"
os.environ["WW_P29B_DISABLE_AUTORUN"] = "1"
import ww_p29b_display_trace as hook  # noqa: E402


def _cls_module():
    """The module name under which _find_class looks for SexAnimationInstance."""
    return hook._CLS_MODULES[0]


def _stand_in_class_loader():
    """Load a fresh fake SexAnimationInstance class + register in sys.modules."""
    from types import ModuleType
    import sys as _s
    mname = _cls_module()
    _s.modules.pop(mname, None)
    mod = ModuleType(mname)
    mod.__name__ = mname
    mod.__package__ = mname.rsplit(".", 1)[0]
    _s.modules[mname] = mod
    return mod


def _make_instance(display, raw=None, author=None, original=None,
                   override=None, name=None):
    Inst = type("Inst", (object,), {})
    i = Inst()
    i.display_name = display
    i.animation_raw_display_name = raw
    i.author = author
    i.original_instance = original
    i.display_name_override = override
    if name is not None:
        i.name = name
    return i


def main():
    failures = []
    def check(cond, label, extra=""):
        print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                              ("  " + extra) if extra else ""))
        if not cond:
            failures.append(label)

    print("=== P29B DISPLAY TRACE OFFLINE LOGIC TEST ===")

    # ---------- 1+2+3 : hook works; normal get_display_name (override=None,
    # original=None) returning TEST299 ----------
    print("\n-- hook + transparent gdn, base TEST299 returns TEST299 --")
    mod = _stand_in_class_loader()
    class SexAnimationInstance(object):
        def get_display_name(self, string_hash, original=False):
            # emulate real WW branch: original_overrides when requested + present
            if original and self.original_instance is not None:
                return self.original_instance.display_name
            if self.display_name_override is not None:
                return self.display_name_override
            return self.display_name
        def get_picker_row(self, *a, **k):
            return None
    mod.SexAnimationInstance = SexAnimationInstance
    hook._reset_state_for_test()
    buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
    try:
        ok, mp, cp = hook._try_patch()
        # display via the (now-wrapped) class method
        inst = _make_instance("TEST299", author="Nevely42")
        out = SexAnimationInstance.get_display_name(inst, 12345, False)
    finally:
        sys.stdout = old
    t = buf.getvalue()
    check(ok is True and cp is True, "class discovered+patched (both methods)")
    check(out == "TEST299", "get_display_name transparently returns TEST299")
    check("GDN_#1" in t, "gdn target frame emitted")
    check("BASE_DISPLAY_NAME='TEST299'" in t, "BASE_DISPLAY_NAME=TEST299")
    check("DISPLAY_NAME_OVERRIDE=None" in t, "override None recorded")
    check("ORIGINAL_INSTANCE_PRESENT=NO" in t, "no original_instance recorded")
    check("ARG_STRING_HASH=12345" in t, "string_hash recorded")
    check("ARG_ORIGINAL=False" in t, "original flag recorded")
    check("GET_DISPLAY_NAME_RETURN='TEST299'" in t, "return recorded")

    # ---------- UI_USING_ORIGINAL_INSTANCE ----------
    print("\n-- UI_USING_ORIGINAL_INSTANCE (original holds old English) --")
    hook._reset_state_for_test()
    hook._try_patch()  # re-wrap after reset (reset unwraps)
    orig_inst = _make_instance("Caught Cheating 1", author="Nevely42")
    inst2 = _make_instance("TEST299", original=orig_inst, author="Nevely42")
    buf = io.StringIO(); sys.stdout = buf
    try:
        out = SexAnimationInstance.get_display_name(inst2, 1, True)
    finally:
        sys.stdout = old
    t2 = buf.getvalue()
    check(out == "Caught Cheating 1", "gdn returns old English (via original)")
    check("ORIGINAL_INSTANCE_PRESENT=YES" in t2, "original present=YES")
    check("ORIGINAL_INSTANCE_DISPLAY_NAME='Caught Cheating 1'" in t2,
          "original display old English recorded")
    check("ARG_ORIGINAL=True" in t2, "ARG_ORIGINAL=True")
    check("P29B_RESULT=UI_USING_ORIGINAL_INSTANCE" in t2,
          "P29B_RESULT=UI_USING_ORIGINAL_INSTANCE")

    # ---------- DISPLAY_NAME_OVERRIDE_WINS ----------
    print("\n-- DISPLAY_NAME_OVERRIDE_WINS --")
    hook._reset_state_for_test()
    hook._try_patch()  # re-wrap after reset
    inst3 = _make_instance("TEST299", override="Caught Cheating 1", author="x")
    buf = io.StringIO(); sys.stdout = buf
    try:
        out = SexAnimationInstance.get_display_name(inst3, 7, False)
    finally:
        sys.stdout = old
    t3 = buf.getvalue()
    check(out == "Caught Cheating 1", "gdn returns override value")
    check("P29B_RESULT=DISPLAY_NAME_OVERRIDE_WINS" in t3,
          "P29B_RESULT=DISPLAY_NAME_OVERRIDE_WINS")

    # ---------- TRANSPARENT passthrough with SENTINEL default ----------
    print("\n-- transparent passthrough preserves omitted-default --")
    hook._reset_state_for_test()
    SENT = object()
    real_seen = {}
    class SexAnimationInstance2(object):
        def get_display_name(self, string_hash, original=SENT):
            real_seen["original"] = original
            real_seen["self"] = self
            return self.display_name
        def get_picker_row(self, *a, **k):
            return None
    mod2 = _stand_in_class_loader()
    mod2.SexAnimationInstance = SexAnimationInstance2
    hook._reset_state_for_test()
    hok, _, _ = hook._try_patch()
    instP = _make_instance("TEST299", author="Nevely42")
    osent = object()
    # caller provides string_hash but OMITS the sentinel-defaulted original
    hit = SexAnimationInstance2.get_display_name(instP, 5)
    check(hok is True, "patched second class")
    check(real_seen.get("original") is SENT,
          "omitted original reaches orig as ITS OWN SENTINEL (not synthetic)",
          "OMITTED_DEFAULT_PRESERVED=YES")

    # ---------- get_picker_row + PICKER_ROW_USES_OTHER_SOURCE ----------
    print("\n-- get_picker_row records row; row old English while base TEST299 --")
    hook._reset_state_for_test()
    row_old = type("Row", (object,), {})()
    row_old.text = "Caught Cheating 1"
    class SexAnimationInstance3(object):
        def get_display_name(self, string_hash, original=False):
            return self.display_name
        def get_picker_row(self, *a, **k):
            return row_old
    mod3 = _stand_in_class_loader()
    mod3.SexAnimationInstance = SexAnimationInstance3
    hook._reset_state_for_test()
    hook._try_patch()
    inst4 = _make_instance("TEST299", author="Nevely42")
    buf = io.StringIO(); sys.stdout = buf
    try:
        hookr = SexAnimationInstance3.get_picker_row(inst4)
    finally:
        sys.stdout = old
    t4 = buf.getvalue()
    check(hookr is row_old, "picker row returned transparently")
    check("PICKER_INSTANCE_DISPLAY_NAME='TEST299'" in t4, "picker instance display")
    check("PICKER_ROW_TEXT='Caught Cheating 1'" in t4, "PICKER_ROW_TEXT=old English")
    check("P29B_PHASE=GET_PICKER_ROW" in t4, "picker phase marker")
    check("P29B_RESULT=PICKER_ROW_USES_OTHER_SOURCE" in t4,
          "PICKER_ROW_USES_OTHER_SOURCE (row built from other source)")

    # row WITHOUT plain text -> UNAVAILABLE (no guessing)
    print("\n-- picker row without known string fields -> UNAVAILABLE --")
    hook._reset_state_for_test()
    row_empty = type("Row2", (object,), {})()
    row_empty.arbitrary_field = "x"
    class SexAnimationInstance4(object):
        def get_display_name(self, string_hash, original=False):
            return self.display_name
        def get_picker_row(self, *a, **k):
            return row_empty
    mod4 = _stand_in_class_loader()
    mod4.SexAnimationInstance = SexAnimationInstance4
    hook._reset_state_for_test()
    hook._try_patch()
    inst5 = _make_instance("Caught Cheating 1", author="Nevely42")
    buf = io.StringIO(); sys.stdout = buf
    try:
        SexAnimationInstance4.get_picker_row(inst5)
    finally:
        sys.stdout = old
    t5 = buf.getvalue()
    check("PICKER_ROW_TEXT='UNAVAILABLE'" in t5, "row text UNAVAILABLE when unknown")

    # ---------- non-target not framed ----------
    print("\n-- non-target NOT framed --")
    hook._reset_state_for_test()
    class SexAnimationInstance5(object):
        def get_display_name(self, string_hash, original=False):
            return self.display_name
        def get_picker_row(self, *a, **k):
            return None
    mod5 = _stand_in_class_loader()
    mod5.SexAnimationInstance = SexAnimationInstance5
    hook._reset_state_for_test()
    hook._try_patch()
    inst_x = _make_instance("Some Other Thing", author="OtherGuy")
    buf = io.StringIO(); sys.stdout = buf
    try:
        SexAnimationInstance5.get_display_name(inst_x, 9, False)
        SexAnimationInstance5.get_picker_row(inst_x)
    finally:
        sys.stdout = old
    t6 = buf.getvalue()
    check("GDN_#" not in t6 and "PICKER_#" not in t6,
          "non-target get_display_name/get_picker_row NOT framed")
    check(hook._STATE["observed"] == 0, "observed==0 for non-target")

    # ---------- restore removes wrappers ----------
    print("\n-- restore removes method wrappers --")
    hook._reset_state_for_test()
    mod6 = _stand_in_class_loader()
    class SexAnimationInstance6(object):
        def get_display_name(self, string_hash, original=False):
            return self.display_name
        def get_picker_row(self, *a, **k):
            return None
    mod6.SexAnimationInstance = SexAnimationInstance6
    hook._try_patch()
    hook._restore_all()
    w = SexAnimationInstance6.get_display_name
    check(not getattr(w, "_ww_p29b_wrapped", False),
          "restore removes wrapper from get_display_name")
    hook._reset_state_for_test()

    # clean sys.modules
    import sys as _s
    for mname in (hook._CLS_MODULES[0],):
        _s.modules.pop(mname, None)

    print("")
    if failures:
        print("P29B_LOGIC_TEST=FAIL")
        for f in failures:
            print("  missing: %s" % f)
        return 1
    print("P29B_LOGIC_TEST=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
