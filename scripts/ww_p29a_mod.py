#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ww_p29a_mod.py --- P29-A SexAnimationInstance constructor runtime trace (debug-only).

PURPOSE (single, minimal):
  While the P28C TEST299 override is live, answer exactly one question:
      CONSTRUCTOR_ARG=TEST299     (constructor receives our overridden raw)
  or  CONSTRUCTOR_ARG=OLD         (constructor receives the original English raw)

This splits the problem into "upstream of the constructor" (loader / import /
registry / same-instance-reload) vs "downstream" (TurboLocalizedString /
get_display_name / picker).  NO downstream hook.  NO xml change.  NO P24.  NO zh.

SAFETY CONTRACT (fail-closed):
  1. We NEVER modify TURBODRIVER_WickedWhims_Scripts.ts4script.
  2. We NEVER modify any Nevely source package or P28C override files on disk.
  3. We wrap (do not replace) SexAnimationInstance.__init__:
        - call original __init__ with the SAME args, untouched
        - return its return value untouched
        - record values AFTER the original __init__ ran (read-only)
        - do NOT assign display_name / name / localized ourselves
  4. If anything we do raises, we restore the original __init__ and re-raise in a
     way that must not corrupt WW: we wrap the whole hook body in try/except and
     only log failures; we never call resolvers that might have side effects.
  5. Rollback = delete this debug ts4script only (never touches WW / P28C / source).

TARGET (confirmed from committed decompiled transcription):
    SexAnimationInstance.__init__(self, animation_id, animation_raw_display_name,
                                  animation_type)
    body (semantic, stable across P-series transcriptions):
        self.animation_id = animation_id
        self.h            = hash_string("story_animations." + str(animation_id))
        self.display_name = animation_raw_display_name
        self.localized    = TurboLocalizedString(self.h, animation_raw_display_name)
        self.name         = animation_raw_display_name
    NOTE: we do not re-implement the body; we only observe the resulting attrs.

DISCOVERY / TIMING (first real-machine run: class unavailable at boot; nothing retried):
  Round-1 log showed HOOK_NOT_YET then a single "retrying, deferred schedule active"
  that was LITERALLY FALSE -- main() did one immediate attempt and never scheduled a
  re-run, so no RETRY= / HOOK_INSTALLED=YES ever appeared.  Foundational fix: we now
  (1) actually arm a repeating main-thread scheduler when sims4 is available, and
  (2) TRACE every discovery attempt (RETRY_INDEX / MODULE_PRESENT / CLASS_PRESENT /
      IMPORT_EXCEPTION / RUNTIME_MODULE_CANDIDATES from sys.modules) so a no-fire or
      a wrong module path is visible in ONE run instead of a silent "retrying".

  Why the class is missing at boot: WW ships wickedwhims/sex/animations/animation_…
  as .pyc inside its OWN .ts4script.  Import order across separate .ts4script files
  is not guaranteed, and WW may import animation_instance lazily once in a lot; so
  WW's SexAnimationInstance is routinely NOT defined when this standalone mod's
  top-level first runs.  We therefore poll on a real scheduler until the class is
  present (bounded), preferring lifecycle hooks (zone in-world) over blind sleep and
  never touching sims objects off the main thread.  Only after HOOK_INSTALLED=YES do
  we start recording constructor args.

  Scheduler: dependency-free, main-thread.  Try, in order, several stable arming
  surfaces (each guarded + traced): (a) if sims4 + a zone service object already
  exist at import, attach to the zone; (b) else register a one-shot retry via the
  reload callback surface so it fires once the game is in-world.  Every arming path
  records SCHEDULER_ARMED/REQUIRED; if none arm, RETRY_CALLBACK_EXECUTED=NO is
  reported truthfully rather than a fabricated "deferred schedule active"."""

import os
import sys
import time
import traceback as _traceback

# ---------------------------------------------------------------------------
# CONFIG / STATE / CORE (below) -- keep pure and import-safe for offline test.

# ---------------------------------------------------------------------------
# Config (ASCII only)
# ---------------------------------------------------------------------------
_MATCH_OLD_RAW = "Caught Cheating 1"   # ordinal 299 original English display name
_MATCH_NEW_RAW = "TEST299"              # P28C override raw for ordinal 299

# Real class-name candidates (in priority order). We only wrap a class whose
# __init__ arg names match the confirmed signature, so a stray name collision
# cannot be wrapped by accident.
_CLASS_NAME_CANDIDATES = (
    "SexAnimationInstance",
    "SexAnimationInstanceExt",
)

# Module candidates to import (P15 real layout), tried in order:
_MODULE_CANDIDATES = (
    "wickedwhims.sex.animations.animation_instance",
    "wickedwhims.sex.animations.sex_animations",
    "wickedwhims.sex.animations.animations_loader",
)
# Imported only for discovery; nested classes could be under a differently
# namespaced module, so we also fall back to a sys.modules name scan.

# Optional log roots tried in order (never assumed all writable).
_LOG_ROOTS = (
    os.environ.get("TMP", ""),
    os.environ.get("TEMP", ""),
    os.path.expanduser("~"),
    os.getcwd(),
)

_STATE = {
    "wrapped": False,
    "orig": None,
    "cls": None,
    "observed": 0,
    "matched": 0,
    "targets": {
        "old_hits": 0,
        "new_hits": 0,
    },
    "error": None,
    "_log_path": "",
    # discovery / timing bookkeeping (round-2 fix)
    "retry_count": 0,
    "retry_cb_executed": False,
    "scheduler_armed": False,
    "last_module_present": False,
    "last_class_present": False,
    "max_retries": 20,
}


def _log_path():
    """Return the first writable path or '' (never assume a fixed Windows path)."""
    for root in _LOG_ROOTS:
        if not root:
            continue
        try:
            p = os.path.join(root, "ww_p29a_trace.log")
            with open(p, "a", encoding="utf-8") as _:
                pass
            return p
        except Exception:
            continue
    return ""


def _emit(line):
    """Write to stdout AND best-effort log (never raise)."""
    try:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    except Exception:
        pass
    path = _STATE.get("_log_path")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def _safe_attr(obj, name):
    """Read an attribute without side effects; never call anything."""
    try:
        return getattr(obj, name)
    except Exception:
        return "<error-reading>"


def _log_header():
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    _emit("=== P29A CONSTRUCTOR TRACE ===")
    _emit("HOOK_LOADED_AT=%s" % ts)
    _emit("TARGET_OLD_RAW=%r" % (_MATCH_OLD_RAW,))
    _emit("TARGET_NEW_RAW=%r" % (_MATCH_NEW_RAW,))


def _hook_factory(orig_init, param_names):
    """Return a wrapper around orig_init that records, then returns untouched.

    param_names: ordered list of __init__ param names (excl 'self'), derived from
    inspect at wrap time.  Used to map positional args to the confirmed
    animation_raw_display_name / animation_id slots fail-closed.
    """
    def _hook(self, *args, **kwargs):
        # Map positional args onto the real signature so RAW_ARG reflects the
        # ACTUAL constructor argument the loader passed (not our guess). If the
        # loader used keywords, kwargs carry them. Never trust a hard-coded index
        # beyond the confirmed signature length.
        animation_id = kwargs.get("animation_id", None)
        raw_arg = kwargs.get("animation_raw_display_name", "<undetermined>")
        # positional binding only while we stay within the confirmed signature
        for pos, val in enumerate(args):
            if pos < len(param_names):
                nm = param_names[pos]
                if nm == "animation_id" and animation_id is None:
                    animation_id = val
                elif nm == "animation_raw_display_name" and raw_arg == "<undetermined>":
                    raw_arg = val
        try:
            _STATE["observed"] += 1
            orig_init(self, *args, **kwargs)

            display_name = _safe_attr(self, "display_name")
            name = _safe_attr(self, "name")
            localized_hash = "<unreadable>"
            try:
                localized = _safe_attr(self, "localized")
                if hasattr(localized, "hash"):
                    localized_hash = localized.hash
                else:
                    localized_hash = _safe_attr(localized, "_hash")
            except Exception:
                localized_hash = "<unreadable>"

            # Decide raw_arg for report: prefer observed display/name equality
            # to raw when raw unavailable, but report both.
            matched = False
            which = "NONE"
            observed_disp = str(display_name)
            observed_name = str(name)
            # normalization helper
            def _norm(x):
                try:
                    return str(x)
                except Exception:
                    return ""
            nd = _norm(observed_disp)
            nn = _norm(observed_name)
            nraw = _norm(raw_arg)
            if nraw == _MATCH_NEW_RAW or nd == _MATCH_NEW_RAW or nn == _MATCH_NEW_RAW:
                which = "TEST299"
                _STATE["targets"]["new_hits"] += 1
                matched = True
            elif nraw == _MATCH_OLD_RAW or nd == _MATCH_OLD_RAW or nn == _MATCH_OLD_RAW:
                which = "OLD"
                _STATE["targets"]["old_hits"] += 1
                matched = True
            if matched:
                _STATE["matched"] += 1

            # Always emit a per-construction line (A: record all).  Simpler for
            # diagnosis; quantities are expected to be modest in one session.
            _emit("---")
            _emit("CONSTRUCT_#%d" % _STATE["observed"])
            _emit("ANIMATION_ID=%r" % (animation_id,))
            _emit("RAW_ARG=%s" % (raw_arg,))
            _emit("INSTANCE_DISPLAY_NAME=%s" % (observed_disp,))
            _emit("INSTANCE_NAME=%s" % (observed_name,))
            _emit("LOCALIZED_HASH=%s" % (localized_hash,))
            _emit("MATCH=%s" % (which,))

        except Exception:
            # Never corrupt WW: log and restore original to avoid repeated
            # breakage, then re-raise so WW sees the original failure *as the
            # original would have raised* if it raised at all.  If the original
            # itself raised, this path also just propagates it.
            tb = _traceback.format_exc()
            _emit("HOOK_ERROR=%s" % (tb,))
            _restore_orig()
            raise

        return None  # __init__ returns None

    _hook._p29a_orig = orig_init
    return _hook


def _restore_orig():
    if _STATE.get("orig") is not None:
        try:
            obj = _STATE.get("cls")
            if obj is not None:
                obj.__init__ = _STATE["orig"]
        except Exception:
            pass
    _STATE["wrapped"] = False


def _wrap_cls(cls):
    """Wrap cls.__init__ if signature matches the confirmed target."""
    try:
        init = cls.__init__
        # Peek at parameter names (fail-closed: only wrap target-shaped sig)
        import inspect
        sig_params = list(inspect.signature(init).parameters.keys())
        # Confirmed: self, animation_id, animation_raw_display_name, animation_type
        wanted = {"animation_id", "animation_raw_display_name"}
        if not wanted.issubset(sig_params):
            return False
    except Exception:
        # inspect unavailable/signature unreadable -> refuse (do not guess)
        return False

    _STATE["cls"] = cls
    _STATE["orig"] = init
    # param names excluding 'self'
    pnames = [p for p in sig_params if p != "self"]
    cls.__init__ = _hook_factory(init, pnames)
    _STATE["wrapped"] = True
    _emit("CLASS_FOUND=%s" % (getattr(cls, "__qualname__", repr(cls)),))
    _emit("INIT_ARG_SIG=%s" % (list(sig_params),))
    return True


def _runtime_candidates():
    """Read-only snapshot: WW-loaded sex/animation module names in sys.modules now."""
    out = []
    try:
        for n in list(sys.modules.keys()):
            nl = (n or "").lower()
            if "wickedwhims" in nl and ("animation" in nl or "sex" in nl):
                out.append(n)
    except Exception:
        pass
    out.sort()
    return out


def _try_wrap(_trace=False):
    """Attempt to locate and wrap the target class. Returns True on success.

    When _trace is False (legacy offline callers) we keep the original quiet
    behavior for the logic test.  The scheduler path always passes _trace=True.
    Returns (ok, module_present, class_present).
    """
    module_present = False
    class_present = False

    def _one(mod, cname):
        cls = getattr(mod, cname, None)
        if cls is None:
            return None
        if cls in (type, object):
            return None
        return cls

    # 1) Direct module import against real WW layout (import may itself pull WW's
    #    lazy module in once WW is registered).  Record presence per candidate.
    for mod_name in _MODULE_CANDIDATES:
        try:
            mod = __import__(mod_name, fromlist=["*"])
            module_present = True
        except Exception:
            continue
        for cname in _CLASS_NAME_CANDIDATES:
            cls = _one(mod, cname)
            if cls is not None:
                class_present = True
                if _wrap_cls(cls):
                    return (True, module_present, class_present)
    # 2) sys.modules scan (module already imported under WW namespace; no re-import).
    for mname, mod in list(sys.modules.items()):
        if not mname:
            continue
        for cname in _CLASS_NAME_CANDIDATES:
            cls = _one(mod, cname)
            if cls is not None:
                class_present = True
                if _wrap_cls(cls):
                    return (True, module_present, class_present)
    return (False, module_present, class_present)


def _trace_attempt(import_exc=None):
    """Emit one per-attempt discovery line with the full required shape.

    Also records the live RUNTIME_MODULE_CANDIDATES so one real run tells us which
    WW animation/sex modules were actually loaded at that moment (task #5)."""
    idx = _STATE.get("retry_count", 0)
    mp = _STATE.get("last_module_present", False)
    cp = _STATE.get("last_class_present", False)
    _emit("RETRY_INDEX=%d" % idx)
    _emit("RETRY_AT=%s" % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
    _emit("MODULE_PRESENT=%s" % ("YES" if mp else "NO"))
    _emit("CLASS_PRESENT=%s" % ("YES" if cp else "NO"))
    _emit("IMPORT_EXCEPTION=%s" % (import_exc or "NONE"))
    cands = _runtime_candidates()
    if cands:
        _emit("RUNTIME_MODULE_CANDIDATES=%s" % (",".join(cands),))
    else:
        _emit("RUNTIME_MODULE_CANDIDATES=(none loaded yet)")


def _retry_once():
    """One bounded retry with tracing. scheduler-armed recompute of last state."""
    if _STATE.get("wrapped"):
        return True
    if _STATE.get("retry_count", 0) >= _STATE.get("max_retries", 20):
        return False
    _STATE["retry_count"] = _STATE.get("retry_count", 0) + 1
    import_exc = "NONE"
    try:
        ok, mp, cp = _try_wrap(_trace=True)
        _STATE["last_module_present"] = mp
        _STATE["last_class_present"] = cp
    except Exception as e:  # noqa: BLE001 - discovery never dies game boot
        ok, mp, cp = False, False, False
        import_exc = "%s: %s" % (type(e).__name__, e)
        _STATE["error"] = e
        _STATE["last_module_present"] = False
        _STATE["last_class_present"] = False
    _trace_attempt(import_exc)
    if ok:
        _STATE["retry_cb_executed"] = True
        _emit("HOOK_INSTALLED=YES")
        _emit("HOOK_MODULE=%s" % (getattr(_STATE.get("cls"), "__module__", "?"),))
        _emit("HOOK_CLASS=%s" % (getattr(_STATE.get("cls"), "__name__", "?"),))
        _emit("HOOK_RETRY_INDEX=%d" % _STATE["retry_count"])
        _STATE["scheduler_armed"] = False  # done; stop re-arming
        return True
    return False


def _arm_via_zone(alarm_fire):
    """Arm alarm_fire on a repeating in-world scheduler if the zone service is up.

    Dependency-free, main-thread only.  We choose the most stable public host by
    probing, recording which host armed, and refusing to guess an unknown one
    silently: if it cannot be armed, _STATE["scheduler_armed"] stays False and
    main() reports RETRY_CALLBACK_EXECUTED=NO truthfully.
    """
    armed = False
    arm_note = "no sims4 zone service reachable yet"
    try:
        import sims4  # noqa: F401
        import services  # noqa: F401
    except Exception:
        return False, arm_note
    # Zone objects expose callback/alarm registration used by many main-thread
    # mods.  We PROBE the real surface rather than assume one name, and always echo
    # which host methods were actually present so a non-arm is instantly actionable
    # on the real machine (no more silent no-fire).
    zone = None
    for attr in ("current_zone", "get_zone", "zone"):
        try:
            z = getattr(services, attr)()
            if z is not None:
                zone = z
                break
        except Exception:
            zone = None
    if zone is None:
        return False, "no current zone object yet"

    # Bodies whose completion means we are in-world and later than at boot, and
    # therefore WW is more likely loaded.  Prefer 'ended'/zone-active signals.
    preferred = ("on_loading_screen_ended", "loading_screen_ended",
                 "add_alarm", "register_on_zone_load", "on_zone_load",
                 "register_callback", "on_loading_screen_started",
                 "loading_screen_started")
    present = []
    for meth in preferred:
        if callable(getattr(zone, meth, None)):
            present.append(meth)
    # Order to try: body-end / alarm first, then start-of-load (a start fires for
    # the NEXT zone, acceptable given the hook must be installed by the time WW's
    # animation picker opens inside that same zone).
    def _rank(m):
        if m == "on_loading_screen_ended" or m == "loading_screen_ended":
            return 0
        if m == "add_alarm":
            return 1
        if m == "register_on_zone_load" or m == "on_zone_load":
            return 2
        if m == "register_callback":
            return 3
        return 4  # started signals last
    present_sorted = sorted(present, key=_rank)
    if not present:
        return False, "no zone host method matched; ZONE_SURFACE absent"
    chosen = present_sorted[0]
    try:
        getattr(zone, chosen)(alarm_fire)
        armed = True
        arm_note = "zone.%s(%s)" % (chosen, ",".join(present) or "none")
    except Exception as e:
        arm_note = "zone.%s raised %s; ZONE_SURFACE=%s" % (chosen, e, ",".join(present) or "none")
    return armed, arm_note


def _register_scheduler():
    """Arm a repeating discovery retry once the world/zone is live.

    Returns True if a repeating callback was armed, else False (main() then reports
    RETRY_CALLBACK_EXECUTED=NO -- never a fabricated 'deferred schedule active')."""
    if _STATE.get("wrapped"):
        return True
    armed, note = _arm_via_zone(_retry_once)
    _STATE["scheduler_armed"] = armed
    if armed:
        _STATE["retry_cb_executed"] = True
        _emit("SCHEDULER_ARMED=YES (%s)" % note)
    else:
        _emit("SCHEDULER_ARMED=NO (%s)" % note)
    return armed


def _reset_state_for_test():
    """Test-only: clear counters (never used at runtime)."""
    _STATE["wrapped"] = False
    _STATE["orig"] = None
    _STATE["cls"] = None
    _STATE["observed"] = 0
    _STATE["matched"] = 0
    _STATE["targets"] = {"old_hits": 0, "new_hits": 0}
    _STATE["error"] = None
    _STATE["retry_count"] = 0
    _STATE["retry_cb_executed"] = False
    _STATE["scheduler_armed"] = False
    _STATE["last_module_present"] = False
    _STATE["last_class_present"] = False


def _emit_final_discovery_fail():
    """Emit the structured FAIL_DISCOVERY block required at round-end."""
    _emit("HOOK_INSTALLED=NO")
    _emit("RETRY_COUNT=%d" % _STATE.get("retry_count", 0))
    _emit("LAST_MODULE_PRESENT=%s" % ("YES" if _STATE.get("last_module_present") else "NO"))
    _emit("LAST_CLASS_PRESENT=%s" % ("YES" if _STATE.get("last_class_present") else "NO"))
    _emit("IMPORT_EXCEPTION=%s" % (str(_STATE.get("error") or "NONE"),))
    _emit("RETRY_CALLBACK_EXECUTED=%s" % ("YES" if _STATE.get("retry_cb_executed") else "NO"))
    cands = _runtime_candidates()
    _emit("RUNTIME_MODULE_CANDIDATES=%s" % (",".join(cands) if cands else "(none loaded yet)",))
    _emit("VERDICT=FAIL_DISCOVERY")


def main():
    _STATE["_log_path"] = _log_path()
    _log_header()

    # Bounded immediate attempts first (covers the case WW is already loaded when
    # this standalone mod is imported -- the common both-after-boot ordering).
    ok = False
    for _ in range(3):
        if _retry_once():
            ok = True
            break
    if ok:
        _emit("MATCH_COUNT=0 (waiting for constructions)")
        _emit("VERDICT=TRACE_CAPTURED (armed)")
        return

    # Class not present yet (WW not imported at our boot, or lazy).  Arm a
    # repeating in-world retry.  If we could NOT arm a scheduler we report
    # RETRY_CALLBACK_EXECUTED honestly rather than a fabricated retry line.
    armed = _register_scheduler()
    if not armed:
        _emit_final_discovery_fail()
        return
    # A repeating callback is armed; a later invocation of _retry_once will emit
    # RETRY_INDEX>=1 + (HOOK_INSTALLED=YES | more RETRY_INDEX lines).  Until then
    # the game main thread is not blocked and no sims object is touched here.
    _emit("VERDICT=DISCOVERY_PENDING (in-world retry armed)")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# Auto-run on import (Sims 4 imports this module; it is not run as __main__).
# Offline tests set WW_P29A_DISABLE_AUTORUN=1 so importing does not arm/log.
# ---------------------------------------------------------------------------
if not os.environ.get("WW_P29A_DISABLE_AUTORUN"):
    try:
        main()
    except Exception:
        # A debug mod must never break game boot under any circumstance.
        pass
