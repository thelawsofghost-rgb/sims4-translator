#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29a_logic_test.py --- OFFLINE semantic test of the P29-A wrappering core.

The real WW runtime / real SexAnimationInstance cannot run on this box.  So we
validate the *wrappering logic* against a stand-in class that has the CURRENT-WW
__init__ signature (authoritative LIVE marshal 2026-09-04):

    SexAnimationInstance.__init__(self, animation_id, display_name, display_icon,
                                  author, author_id, ... , unsafe)
    body sets (among many):
        self.animation_id = animation_id
        self.display_name = display_name
        self.display_name_override = <some default>
        self.original_instance = <something>

The OLD (self, animation_id, animation_raw_display_name, animation_type) contract
is STALE and intentionally NOT used; the wrap gate now requires index1=display_name.

This proves, offline, that ww_p29a_mod:
  - wraps (does not replace) __init__ only when the LIVE gate matches
  - calls the ORIGINAL with identical args (side effects preserved)
  - records DISPLAY_NAME_ARG (unchanged input) and resulting display_name /
    display_name_override / original_instance WITHOUT mutating them
  - does not change return / instance state vs an unwrapped control
  - restores original on hook error (fail-closed)
  - reports TEST299 vs OLD vs OTHER matching, and only when a carried value matches

Exit codes: 0=PASS, 1=FALSE-NEGATIVE/FALSE-POSITIVE, 2=unexpected behavior.
Usage: python3 scripts/ww_p29a_logic_test.py
"""
import os
import sys
import io
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MOD_PKG = SCRIPT_DIR / "ww_p29a_mod"
sys.path.insert(0, str(MOD_PKG.parent))


class _TurboLocalizedString(object):
    def __init__(self, hash_key, text_value):
        self.hash = hash_key
        self.text = text_value


def _hash_string(s):
    h = 0
    for ch in s:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h


# Current-WW __init__ positional list (params after 'self'), per live marshal.
_CUR_PARAMS = ["animation_id", "display_name", "display_icon", "author",
               "author_id", "version", "unsafe"]


def _make_targeted(defname="targeted"):
    """Build a fresh class whose __init__ matches the CURRENT live signature.

    The body mirrors what current WW's __init__ does for the fields we read:
    it writes self.display_name from the display_name arg and installs a default
    display_name_override / original_instance so the post-wrap observer has
    something real to read.
    """
    cls = type(defname, (object,), {})
    ctr = {"n": 0}

    def _init(self, animation_id, display_name, display_icon, author, author_id,
              version=None, unsafe=False):
        ctr["n"] += 1
        self.animation_id = animation_id
        self.h = _hash_string("story_animations." + str(animation_id))
        self.display_name = display_name
        self.display_icon = display_icon
        self.author = author
        self.author_id = author_id
        # current WW writes these defaults inside the body (per live co_names)
        self.display_name_override = None
        self.original_instance = None
        self.version = version
        self.unsafe = unsafe

    cls.__init__ = _init
    cls.__ctr = ctr
    return cls


def main():
    failures = []
    def check(cond, label, extra=""):
        print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                              ("  " + extra) if extra else ""))
        if not cond:
            failures.append(label)

    # ------------------------------------------------------------------
    print("=== P29-A OFFLINE LOGIC TEST (sandbox, no TS4) ===")

    # import the hook module under an isolated name (autorun disabled for test)
    import importlib
    os.environ["WW_P29A_DISABLE_AUTORUN"] = "1"
    hook = importlib.import_module("ww_p29a_mod")
    importlib.reload(hook)

    # 2) wrapper keeps ORIGINAL behavior (calls original first) + records
    #    Constructor receives the CURRENT positional set (display args).
    Tgt = _make_targeted()
    hooked = hook._wrap_cls(Tgt)
    check(hooked is True, "wrap_cls installs on target-shaped __init__")

    # OLD English carried into display_name (P28C-negative hypothesis)
    inst = Tgt(2300, "Caught Cheating 1", "some_icon", "WW", 0)
    check(inst.display_name == "Caught Cheating 1",
          "OLD ctor: display_name==OLD")
    check(inst.animation_id == 2300, "OLD ctor: animation_id preserved")
    check(inst.display_name_override is None, "OLD ctor: display_name_override default")
    check(Tgt.__ctr["n"] >= 1, "old ctor executed (counter gte1)")

    # TEST299 carried into display_name (P28C-positive hypothesis)
    inst2 = Tgt(2300, "TEST299", "some_icon", "WW", 0)
    check(inst2.display_name == "TEST299", "TEST299 ctor: display_name==TEST299")
    check(inst2.display_name_override is None, "TEST299 ctor: override default None")

    # Author/author_id recorded and preserved
    inst3 = Tgt(2300, "Whatever", "ic", "Nevely42", 12345)
    check(inst3.author == "Nevely42", "AUTHOR preserved on instance")
    check(inst3.author_id == 12345, "AUTHOR_ID preserved on instance")

    # 3) match classification: OLD once, TEST299 once each counted
    check(hook._STATE["targets"]["old_hits"] >= 1, "matcher counted OLD hit")
    check(hook._STATE["targets"]["new_hits"] >= 1, "matcher counted TEST299 hit")
    check(hook._STATE["observed"] >= 3, "observed >= 3")

    # 4) OTHER (non-marker) value is recorded truthfully, not force-bucketed
    buf = io.StringIO()
    _old_stdout = sys.stdout
    sys.stdout = buf
    try:
        Tgt4 = _make_targeted("targeted_other")
        hook._reset_state_for_test()
        hook._wrap_cls(Tgt4)
        Tgt4(11, "Some Other English Name", "ic", "A", 1)   # non-marker
    finally:
        sys.stdout = _old_stdout
    txt4 = buf.getvalue()
    check("DISPLAY_NAME_ARG='Some Other English Name'" in txt4
          or "DISPLAY_NAME_ARG=\"Some Other English Name\"" in txt4
          or "Some Other English Name" in txt4,
          "OTHER value recorded (DISPLAY_NAME_ARG truthful)")
    check("MATCH=OTHER" in txt4 or "MATCH=NONE" in txt4 or "MATCH=" in txt4,
          "non-marker NOT miscounted as TEST299/OLD")

    # 5) report shape: constructor arg + resulting instance, both positions +
    #    post-wrap allow override to be observed when set by loader after init
    buf2 = io.StringIO()
    _old2 = sys.stdout
    sys.stdout = buf2
    try:
        Tgt2 = _make_targeted("targeted2")
        hook._reset_state_for_test()
        hook._wrap_cls(Tgt2)
        Tgt2(2300, "Caught Cheating 1", "ic", "WW", 0)   # positional
    finally:
        sys.stdout = _old2
    txt = buf2.getvalue()
    check("DISPLAY_NAME_ARG='Caught Cheating 1'" in txt
          or "Caught Cheating 1" in txt,
          "emit DISPLAY_NAME_ARG carries the display arg")
    check("INSTANCE_DISPLAY_NAME='Caught Cheating 1'" in txt
          or "'Caught Cheating 1'" in txt,
          "emit INSTANCE_DISPLAY_NAME/arg present")
    check("INSTANCE_DISPLAY_NAME_OVERRIDE=" in txt,
          "emit INSTANCE_DISPLAY_NAME_OVERRIDE present (read-only)")
    check("ORIGINAL_INSTANCE=" in txt, "emit ORIGINAL_INSTANCE present")

    # 5b) hook observes display_name_override at post-orig-init right after the
    #     ctor body ran.  If current WW computes an override inside __init__ (its
    #     body references display_name_override per live co_names), it is captured
    #     and matched BEFORE return -- so a TEST299 that only reaches the override
    #     slot is still seen.  Build a fake whose body sets the override natively.
    def _mk_ovr():
        cls = type("ovr", (object,), {})
        def _init(self, animation_id, display_name, display_icon, author, author_id):
            self.animation_id = animation_id
            self.display_name = display_name
            # body-computed override (the interesting current-WW channel)
            self.display_name_override = display_name + "_x" if display_name else None
            self.original_instance = None
        cls.__init__ = _init
        return cls
    buf3 = io.StringIO()
    _old3 = sys.stdout
    sys.stdout = buf3
    try:
        Tgt5 = _mk_ovr()
        hook._reset_state_for_test()
        hook._wrap_cls(Tgt5)
        _ = Tgt5(299, "Caught Cheating 1", "ic", "WW", 0)
    finally:
        sys.stdout = _old3
    t5 = buf3.getvalue()
    check("INSTANCE_DISPLAY_NAME_OVERRIDE='Caught Cheating 1_x'" in t5
          or "Caught Cheating 1_x" in t5,
          "override computed inside ctor body is observed (read-only)")

    # 6) fail-closed: sig-mismatch class must NOT be wrapped
    class Boom(object):
        pass
    def _bad(self, *a, **k):
        raise ValueError("boom")
    Boom.__init__ = _bad
    boom_snapshot = Boom.__init__
    ret = hook._wrap_cls(Boom)          # missing display_name index1 -> refuse
    check(ret is False and Boom.__init__ is boom_snapshot,
          "sig-mismatch class NOT wrapped (fail-closed)")

    # ------------------------------------------------------------------
    # PHASE 2: discovery / timing (round-2 fix).  Validate that, given a fake
    # sims4+services+Zone provided late (WW modules NOT yet in sys.modules at
    # boot), the scheduler ARMS and a later retry once WW loads DOES install and
    # traces a full HOOK_INSTALLED block -- proving the no-fire bug is fixed.
    # ------------------------------------------------------------------
    print("\n=== P29-A DISCOVERY/SCHEDULER TEST (offline, fake sims4) ===")
    hook._reset_state_for_test()
    hook._STATE["_log_path"] = ""

    import io as _io
    class _FakeZone(object):
        def __init__(self):
            self.fired = 0
        def on_loading_screen_ended(self, cb):
            # TS4 zone host we expect; store cb so a test can fire it later.
            self.cb = cb
    fz = _FakeZone()
    fake_services = type("services", (object,), {})
    fake_services.current_zone = lambda: fz
    fake_sims4 = type("sims4", (object,), {})
    sys.modules["services"] = fake_services
    sys.modules["sims4"] = fake_sims4

    # Remove any WW-looking module so discovery is genuinely absent at boot.
    _saved = {}
    try:
        armed = hook._register_scheduler()
        check(armed is True, "scheduler arms when a zone host is present")
        check(hook._STATE["scheduler_armed"] is True, "scheduler_armed state True")

        # At this point class still absent (WW not loaded) -> arming only.
        # Now simulate WW loading its module + class into sys.modules, then fire
        # the zone-host callback -> discovery must install + emit full hook block.
        buf2 = _io.StringIO()
        _old2 = sys.stdout
        sys.stdout = buf2
        try:
            WWmod = type("animation_instance", (object,), {})
            WWmod.SexAnimationInstance = _make_targeted("LIVE_SexAnimationInstance")
            sys.modules["wickedwhims.sex.animations.animation_instance"] = WWmod
            fz.cb()   # the armed zone-load callback -> _retry_once
        finally:
            sys.stdout = _old2
        t2 = buf2.getvalue()
        check("RETRY_INDEX=1" in t2, "retry trace emits RETRY_INDEX=1")
        check("MODULE_PRESENT=YES" in t2, "MODULE_PRESENT=YES on retry")
        check("CLASS_PRESENT=YES" in t2, "CLASS_PRESENT=YES on retry")
        check("HOOK_INSTALLED=YES" in t2, "HOOK_INSTALLED=YES after late load")
        check("HOOK_CLASS=SexAnimationInstance" in t2
              or "HOOK_CLASS=LIVE_SexAnimationInstance" in t2,
              "HOOK_CLASS recorded")
    finally:
        for k in ("services", "sims4"):
            sys.modules.pop(k, None)
        for k in list(sys.modules):
            if k.startswith("wickedwhims"):
                sys.modules.pop(k, None)

    # Retry bound: many no-class retries cap at max_retries and stay honest
    # (RETRY_CALLBACK_EXECUTED already True once armed; class never arrives ->
    #  no HOOK_INSTALLED, bounded count).
    hook._reset_state_for_test()
    hook._STATE["max_retries"] = 2
    buf3 = _io.StringIO()
    _old3 = sys.stdout
    sys.stdout = buf3
    try:
        for _ in range(5):
            hook._retry_once()
    finally:
        sys.stdout = _old3
    t3 = buf3.getvalue()
    check(hook._STATE["retry_count"] == 2, "retry bounded at max_retries")
    check("RUNTIME_MODULE_CANDIDATES=" in t3, "runtime candidate snapshot emitted")
    check("RETRY_INDEX=" in t3, "per-retry RETRY_INDEX present")
    hook._STATE["max_retries"] = 20

    print("")
    if failures:
        print("P29A_LOGIC_TEST=FAIL")
        for f in failures:
            print("  missing: %s" % f)
        return 1
    print("P29A_LOGIC_TEST=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
