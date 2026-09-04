#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ww_p29a_mod.py --- P29-A SexAnimationInstance constructor runtime trace (debug-only).

PURPOSE (single, minimal):
  While the P28C TEST299 override is live, answer exactly one question per Nevely
  target animation:
      does the CURRENT constructor receive/retain TEST299 (our override), or the
      original English display value, or something else already lost upstream?
  We record TRUTHFULLY the display-bearing constructor arg and the resulting
  instance attrs (display_name / display_name_override / original_instance / etc.)
  and only LABEL a bucket when an observed value actually equals a known marker;
  otherwise we record the real value.  NO forced binary judgement, NO downstream
  hook, NO xml change, NO P24, NO zh.

SAFETY CONTRACT (fail-closed):
  1. We NEVER modify TURBODRIVER_WickedWhims_Scripts.ts4script.
  2. We NEVER modify any Nevely source package or P28C override files on disk.
  3. We wrap (do not replace) SexAnimationInstance.__init__:
        - call original __init__ with the SAME args, untouched
        - return its return value untouched
        - record values AFTER the original __init__ ran (read-only)
        - do NOT assign display_name / display_name_override / name / etc. ourselves
  4. If anything we do raises, we restore the original __init__ and re-raise in a
     way that must not corrupt WW: we wrap the whole hook body in try/except and
     only log failures; we never call resolvers that might have side effects.
  5. Rollback = delete this debug ts4script only (never touches WW / P28C / source).

CURRENT-WW TARGET SIGNATURE (authoritative LIVE marshal 2026-09-04, 3.7.9 / 420d0d0a):
    SexAnimationInstance.__init__(self, animation_id, display_name, display_icon,
                                  author, author_id, object_animation_clip_name,
                                  object_geometry_state, ... , unsafe)
    (*29 parameters shown live by the native probe; the OLD committed transcription
     "(self, animation_id, animation_raw_display_name, animation_type)" is STALE /
     INVALID_FOR_CURRENT_WW and we no longer trust or gate on it.)
    __init__ body also references display_name_override / original_instance /
    identifier_cache (from the LIVE co_names), so we record those attrs after wrap.
  We do NOT re-implement the body; we only observe constructor arg + resulting attrs.

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


def _hook_factory(orig_init, param_names, cls_qualname):
    """Return a wrapper around orig_init that records, then returns untouched.

    param_names: ordered list of __init__ param names EXCLUDING 'self', derived
    from inspect at wrap time (the LIVE current-WW signature).  Used to bind
    positional args to the real display-bearing slot fail-closed -- we never trust
    a hard-coded index beyond this recovered list.

    Current-WW contract (live marshal):
        index0 animation_id, index1 display_name (the display-bearing arg the
        loader passes), index2 display_icon, index3 author, index4 author_id, ...
    We ALSO record post-init instance attrs (display_name_override /
    original_instance / etc.) that the __init__ body sets, read-only.
    """
    def _hook(self, *args, **kwargs):
        # positional binding onto the LIVE parameter list (index in param_names):
        #   0 animation_id, 1 display_name, 3 author, 4 author_id
        def _pos(idx):
            if idx < len(param_names):
                return param_names[idx]
            return None
        p_anim = _pos(0)
        p_disp = _pos(1)
        p_author = _pos(3)
        p_author_id = _pos(4)
        animation_id = kwargs.get(p_anim) if p_anim else None
        display_name_arg = kwargs.get(p_disp, "<undetermined>") if p_disp else "<undetermined>"
        author = kwargs.get(p_author) if p_author else None
        author_id = kwargs.get(p_author_id) if p_author_id else None
        # positional slots < len(args) -> bind by recovered name (fail-closed)
        for pos, val in enumerate(args):
            if pos < len(param_names):
                nm = param_names[pos]
                if nm == p_anim and p_anim and animation_id is None:
                    animation_id = val
                elif nm == p_disp and p_disp and display_name_arg == "<undetermined>":
                    display_name_arg = val
                elif nm == p_author and p_author and author is None:
                    author = val
                elif nm == p_author_id and p_author_id and author_id is None:
                    author_id = val
        try:
            _STATE["observed"] += 1
            orig_init(self, *args, **kwargs)

            # read-only attrs the __init__ body may have set (never call anything)
            inst_display = _safe_attr(self, "display_name")
            inst_override = _safe_attr(self, "display_name_override")
            inst_orig = _safe_attr(self, "original_instance")
            inst_name = _safe_attr(self, "name")

            # Bucket ONLY when an actually-carried value equals a known marker;
            # otherwise leave it truthful.
            which = "OTHER"
            _norm = lambda x: str(x) if x is not None else ""
            carried = [
                _norm(display_name_arg), _norm(inst_display), _norm(inst_override),
                _norm(inst_orig), _norm(inst_name),
            ]
            if any(c == _MATCH_NEW_RAW for c in carried):
                which = "TEST299"
                _STATE["targets"]["new_hits"] += 1
            elif any(c == _MATCH_OLD_RAW for c in carried):
                which = "OLD"
                _STATE["targets"]["old_hits"] += 1
            if which in ("TEST299", "OLD"):
                _STATE["matched"] += 1

            _emit("---")
            _emit("CONSTRUCT_#%d" % _STATE["observed"])
            _emit("ANIMATION_ID=%r" % (animation_id,))
            _emit("DISPLAY_NAME_ARG=%r" % (display_name_arg,))
            _emit("AUTHOR=%r" % (author,))
            _emit("AUTHOR_ID=%r" % (author_id,))
            _emit("INSTANCE_DISPLAY_NAME=%r" % (inst_display,))
            _emit("INSTANCE_DISPLAY_NAME_OVERRIDE=%r" % (inst_override,))
            _emit("ORIGINAL_INSTANCE=%r" % (inst_orig,))
            _emit("MATCH=%s" % (which,))

        except Exception:
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
    """Wrap cls.__init__ if its LIVE signature matches the current WW contract.

    Current WW requires (index0=animation_id, index1=display_name).  We gate on the
    recovered param list so we never wrap a stale-shape or unrelated init by guess.
    """
    try:
        init = cls.__init__
        import inspect
        sig_params = list(inspect.signature(init).parameters.keys())
    except Exception:
        # inspect unavailable/signature unreadable -> refuse (do not guess)
        return False
    # current contract: self, animation_id, display_name, display_icon, ...
    if len(sig_params) < 3:
        return False
    p1 = sig_params[1] if len(sig_params) > 1 else ""   # after 'self'
    p2 = sig_params[2] if len(sig_params) > 2 else ""   # after 'self','animation_id'
    if p1 != "animation_id":
        return False
    if p2 != "display_name":
        # The old stale (animation_raw_display_name) or an unrelated shape -> skip
        return False

    _STATE["cls"] = cls
    _STATE["orig"] = init
    pnames = [p for p in sig_params if p != "self"]
    cls.__init__ = _hook_factory(init, pnames, getattr(cls, "__name__", repr(cls)))
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
