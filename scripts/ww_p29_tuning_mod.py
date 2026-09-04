#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ww_p29_tuning_mod.py --- P29-TUNING: observe the SAME animation_tuning object
the real loader uses, while P28C override ordinal299 raw=TEST299 is LIVE.

WHY (replaces the P29-A constructor-only premise):
  Real static (authoritative, 2026-09-04 18:37) showed:
      CALLER_FUNCTION=_create_sex_animation_instance
      FN_PARAMS=animation_tuning, animation_override
      DISPLAY_NAME_STORE_PATTERN=
           animation_tuning.animation_display_name -> local display_name
      RAW_FIELD_READ_PATTERN=  LOAD_ATTR animation_raw_display_name
      ANIMATION_DISPLAY_NAME_WRITERS=(none found)   # whole ts4script, no STORE
      ANIMATION_RAW_DISPLAY_NAME_WRITERS=(none found)
      XML_KEY_FOR_ANIMATION_DISPLAY_NAME= NOT_LITERAL / ATTRIBUTE_DERIVED
      XML_KEY_FOR_ANIMATION_RAW_DISPLAY_NAME= literal 'animation_raw_display_name'
      DISPLAY_NAME_OVERRIDE_BEHAVIOR= override_wins_else_base
  => We must NOT keep assuming raw is "the wrong field" NOR that it necessarily
     derives animation_display_name.  Both are RUNTIME attributes on the same
     animation_tuning object; WW's ts4script has no STORE writer for either, so
     the raw->display relation is decided at tuning/parser/dynamic-descriptor
     layer -- which is only observable AT RUNTIME.

  Therefore we do NOT hook SexAnimationInstance.__init__ anymore.  We hook the
  REAL module-level loader function

      wickedwhims.sex.animations.animations_loader._create_sex_animation_instance(
          animation_tuning, animation_override)

  and, while the P28C ordinal299 raw=TEST299 override is active, record BOTH
  animation_raw_display_name AND animation_display_name on that SAME tuning
  object before the call, plus the returned instance's display after the call.

PURPOSE (single): decide, per target animation, WHICH of A/B/C/D is true:
  A  RAW_ATTR==TEST299 and DISPLAY_ATTR=="Caught Cheating 1"
        -> P29_RESULT=RAW_CHANGED_DISPLAY_DERIVED_OLD
  B  RAW_ATTR=="Caught Cheating 1" and DISPLAY_ATTR=="Caught Cheating 1"
        -> P29_RESULT=OVERRIDE_NOT_PRESENT_IN_RUNTIME_TUNING
  C  RAW_ATTR=="TEST299" and DISPLAY_ATTR=="TEST299" and
     RETURN_INSTANCE_DISPLAY_NAME=="TEST299"
        -> P29_RESULT=TUNING_AND_INSTANCE_CORRECT
  D  installed + loader ok + no error + no target-keeping call across the whole run
        -> P29_RESULT=TARGET_TUNING_NOT_OBSERVED
     (Derived POST-SESSION by ww_p29_tuning_report_check.py only -- this module never
     guesses D mid-run.  If a HOOK_ERROR was captured the session is INVALID:
        -> P29_RESULT=INVALID_HOOK_ERROR, which takes precedence over D.
  Buckets are LABELED only when an actually-carried value equals a known marker;
  otherwise we record the real value truthfully.  No forced binary verdict.

SAFETY CONTRACT (fail-closed, temporary, read-only on the tuning object):
  1. We NEVER modify TURBODRIVER_WickedWhims_Scripts.ts4script.
  2. We NEVER modify any Nevely source package or P28C override on disk.
  3. We WRAP (do not replace the original function semantics): we call the ORIGINAL
     strictly transparently by forwarding the EXACT received *args/**kwargs verbatim
     (never re-authoring a positional override -- authoring a None default for an arg
     whose real default is a WW sentinel was the 18:55 INVALID-run root cause).  Real
     defaults are recorded at install (ORIG_SIGNATURE/ORIG_DEFAULTS/ORIG_KWDEFAULTS),
     and we return the original's value untouched.  We READ tuning/instance attrs
     only (no attribute assignment to animation_display_name / animation_raw_display_name
     / display_name / display_name_override / name / etc.).
  4. If any of our observation code raises, we restore the original binding, log the
     traceback, and re-raise so WW behavior is not silently corrupted.
  5. Rollback = delete this debug ts4script only (never touches WW / P28C / Nevely).

DISCOVERY / TIMING (reuses the P29-A-proven real scheduler -- no fake retries):
  We patch a MODULE-LEVEL function by rebinding the module attribute, and also
  re-point every already-imported `from ... import _create_sex_animation_instance`
  alias (a module dict entry bound to the same original object) so both call
  styles are intercepted once animations_loader is actually in sys.modules.  If it
  is not present at our boot (WW imports lazily), we arm a repeating in-world
  retry on the zone service and retry only when the module visibly appears; we
  never print a fabricated "deferred schedule active".
"""
import os
import sys
import time
import traceback as _traceback

_MATCH_OLD_RAW = "Caught Cheating 1"   # ordinal 299 original English display
_MATCH_NEW_RAW = "TEST299"              # P28C override raw for ordinal 299

# Real module to patch (authoritative live layout).
_TUNING_MODULE = "wickedwhims.sex.animations.animations_loader"
_TUNING_FUNC = "_create_sex_animation_instance"

# Internal marker meaning "argument was NOT provided by the caller".  Distinct
# object(); it is NEVER forwarded to orig (we only test identity against it).
_UNSET_SENTINEL = object()

_LOG_ROOTS = (
    os.environ.get("TMP", ""),
    os.environ.get("TEMP", ""),
    os.path.expanduser("~"),
    os.getcwd(),
)

_STATE = {
    "wrapped": False,
    "patched": {},        # module-name -> original bound object (for restore)
    "observed": 0,
    "kept": 0,            # target-keeping calls (raw/display carries a marker)
    "target_buckets": {"A_raw_changed": 0, "B_override_absent": 0,
                       "C_correct": 0},
    "seen_markers_on": {"raw": set(), "display": set()},
    "target_record_emitted": False,
    "final_verdict": None,  # D verdict is derived post-session ONLY by report_check
    "error": None,
    "_log_path": "",
    # discovery / timing
    "retry_count": 0,
    "retry_cb_executed": False,
    "scheduler_armed": False,
    "last_module_present": False,
    "last_func_present": False,
    "max_retries": 20,
}


def _log_path():
    for root in _LOG_ROOTS:
        if not root:
            continue
        try:
            p = os.path.join(root, "ww_p29_tuning_trace.log")
            with open(p, "a", encoding="utf-8") as _:
                pass
            return p
        except Exception:
            continue
    return ""


def _emit(line):
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


def _norm(x):
    return str(x) if x is not None else ""


def _log_header():
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    _emit("=== P29 TUNING TRACE ===")
    _emit("HOOK_LOADED_AT=%s" % ts)
    _emit("TARGET_OLD_RAW=%r" % (_MATCH_OLD_RAW,))
    _emit("TARGET_NEW_RAW=%r" % (_MATCH_NEW_RAW,))


def _judge(raw, disp, ret_disp, override=None, ret=None):
    """Return (bucket_key, P29_RESULT_or_OTHER, label).

    Bucket only on actual marker equality; otherwise P29_RESULT is replaced by the
    truthful raw/display readout.  Never fabricate one of A/B/C/D.
    """
    r = _norm(raw)
    d = _norm(disp)
    rd = _norm(ret_disp)
    marker = (_MATCH_NEW_RAW, _MATCH_OLD_RAW)
    if r in marker or d in marker or rd in marker:
        # target-keeping call (a known marker is actually carried)
        if r == _MATCH_NEW_RAW and d == _MATCH_OLD_RAW:
            return ("A_raw_changed", "RAW_CHANGED_DISPLAY_DERIVED_OLD", "A")
        if r == _MATCH_OLD_RAW and d == _MATCH_OLD_RAW:
            return ("B_override_absent",
                    "OVERRIDE_NOT_PRESENT_IN_RUNTIME_TUNING", "B")
        if (r == _MATCH_NEW_RAW and d == _MATCH_NEW_RAW
                and rd == _MATCH_NEW_RAW):
            return ("C_correct", "TUNING_AND_INSTANCE_CORRECT", "C")
        # any other marker-carry combination that is not A/B/C -> still target
        return ("other_marker", "MATCH_TARGET_OTHER_PATTERN", "TARGET")
    return (None, None, "NON_TARGET")


def _signature_digest(orig):
    """Emit the REAL default semantics of orig, recorded once at install time so
    the next real run authoritatively shows what the loader's omitted args are.

    Returns a list of ASCII lines to _emit().  We never repr() an arbitrary object
    by calling str/repr on it (a hostile __repr__ could run code); we record only
    shape and guarded identity facts:
      ORIG_SIGNATURE=pos:<names> kw:<names> varargs:<y|n> varkw:<y|n>
      ORIG_DEFAULTS=<n> <field> | ...   (positional-or-keyword, in order)
      ORIG_KWDEFAULTS=<n> <field> | ... (keyword-only, in order)
    where each <field> is:
      UNSET                                  (no default at all)
      type=<type-name>; isNone=<y|n>; <known-sentinel matches>
    A '<known-sentinel>' match is only reported when we can resolve the real WW
    symbol (e.g. EMPTY_ANIMATION_STRUCTURE_CONTAINER) from the loader module's own
    namespace by identity -- never a guessed constant.
    """
    import os as _os
    import inspect as _ins
    out = []
    try:
        sig = _ins.signature(orig)
    except Exception as e:
        out.append("ORIG_SIGNATURE=<unreadable> %r %s" % (type(e).__name__, e))
        return out
    params = list(sig.parameters.values())
    pos_params = [p for p in params
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    kw_params = [p for p in params if p.kind == p.KEYWORD_ONLY]
    pos = [p.name for p in pos_params]
    kw = [p.name for p in kw_params]
    varargs = any(p.kind == p.VAR_POSITIONAL for p in params)
    varkw = any(p.kind == p.VAR_KEYWORD for p in params)
    # resolve real WW sentinels from the loader module namespace by identity
    _try_collections = None
    try:
        import wickedwhims.sex.animations.animations_loader as _awl
        _try_collections = [
            ("EMPTY_ANIMATION_STRUCTURE_CONTAINER",
             getattr(_awl, "EMPTY_ANIMATION_STRUCTURE_CONTAINER", None)),
        ]
    except Exception:
        _try_collections = None
    out.append("ORIG_SIGNATURE=pos:%s kw:%s varargs:%s varkw:%s" % (
        ",".join(pos) if pos else "(none)",
        ",".join(kw) if kw else "(none)",
        "yes" if varargs else "no", "yes" if varkw else "no"))

    def _healthies(d):
        if d is _UNSET_SENTINEL:  # no default
            return "UNSET"
        if d is None:
            return "type=NoneType; isNone=y"
        try:
            dt = type(d).__name__
        except Exception:
            dt = "<unknown-type>"
        bits = ["type=%s" % dt, "isNone=n"]
        if _try_collections:
            for sym, real in _try_collections:
                try:
                    eq = (d is real) and (real is not None)
                except Exception:
                    eq = False
                if eq:
                    bits.append("=WW.%s:y" % sym)
        return ";".join(bits)

    pd = []
    for p in pos_params:
        d = p.default if p.default is not p.empty else _UNSET_SENTINEL
        pd.append(_healthies(d))
    out.append("ORIG_DEFAULTS=%d %s" % (len(pd), " | ".join(pd)))
    kd = []
    for p in kw_params:
        d = p.default if p.default is not p.empty else _UNSET_SENTINEL
        kd.append(_healthies(d))
    out.append("ORIG_KWDEFAULTS=%d %s" % (len(kd), " | ".join(kd)))
    return out


def _hook_fn_impl(orig, modname):
    """Return a STRICTLY TRANSPARENT wrapper around orig.

    Root cause of the first real 18:55 INVALID run: the old wrapper authored its
    own defaults `def _hook(animation_tuning=None, animation_override=None,...)`
    and always forwarded `orig(animation_tuning, animation_override, *a, **kw)`.
    If the real loader's second-parameter default is a non-None sentinel (WW
    EMPTY_ANIMATION_STRUCTURE_CONTAINER / similar), then a caller that OMITS that
    arg used to get the sentinel, but through our old wrapper it got a literal
    None -- corrupting omitted-default semantics -> the loader then resolved a
    None where it expected a tuning object (animations_loader.py:123
    AttributeError: 'NoneType'.animation_raw_display_name).

    Fix: do NOT write our own defaults, do NOT re-author the call.  Observe via
    inspect.signature(orig).bind_partial(*args, **kwargs) [read-only, never fills
    in an omitted arg], and forward the ORIGINAL `*args, **kwargs` verbatim to
    orig.  An omitted arg is recorded as '<OMITTED>' and is NEVER synthesized.
    """
    import inspect as _hook_ins
    _sig = None
    try:
        _sig = _hook_ins.signature(orig)
    except Exception:
        _sig = None

    def _hook(*args, **kwargs):
        # ---- map observed args (bind_partial: never fills omitted) ----
        animation_tuning = _UNSET_SENTINEL
        animation_override = _UNSET_SENTINEL
        bound = None
        if _sig is not None:
            try:
                bound = _sig.bind_partial(*args, **kwargs)
            except Exception:
                bound = None
        if bound is not None:
            anim_args = bound.arguments
            animation_tuning = anim_args.get("animation_tuning", _UNSET_SENTINEL)
            animation_override = anim_args.get("animation_override", _UNSET_SENTINEL)

        # ---- BEFORE (read-only) ----
        tuning_type = "<none>"
        tuning_module = "<none>"
        raw_attr = "<undetermined>"
        disp_attr = "<undetermined>"
        if animation_override is _UNSET_SENTINEL:
            override_present = "OMITTED"
        elif animation_override is None:
            override_present = "NO"  # provided as None == override not active
        else:
            override_present = "YES"
        if animation_tuning is not _UNSET_SENTINEL and animation_tuning is not None:
            try:
                tuning_type = type(animation_tuning).__name__
            except Exception:
                tuning_type = "<unknown-type>"
            try:
                tuning_module = (getattr(type(animation_tuning), "__module__", "")
                                 or "<no-module>") + "." + tuning_type
            except Exception:
                tuning_module = "<unknown-module>"
            raw_attr = _safe_attr(animation_tuning, "animation_raw_display_name")
            disp_attr = _safe_attr(animation_tuning, "animation_display_name")

        # judge pre-call (marker pre-check; C recomputed after return)
        bucket = None
        result = None
        keep = False
        try:
            bucket, result, _label = _judge(raw_attr, disp_attr, "")
            keep = result is not None
        except Exception:
            pass

        ret = None
        ret_disp = "<no-return>"
        ret_override = "<no-return>"
        author = "<no-return>"
        anim_name = "<no-return>"
        anim_id = "<no-return>"
        try:
            _STATE["observed"] += 1
            # TRANSPARENT FORWARD: identical args/kwargs, never synthesized
            ret = orig(*args, **kwargs)
            if keep:
                _STATE["kept"] += 1
            if ret is not None:
                ret_disp = _safe_attr(ret, "display_name")
                ret_override = _safe_attr(ret, "display_name_override")
                author = _safe_attr(ret, "author")
                anim_name = _safe_attr(ret, "animation_name")
                anim_id = _safe_attr(ret, "animation_id")
            bucket, result, _label = _judge(raw_attr, disp_attr, ret_disp)
            keep = result is not None
        except Exception:
            tb = _traceback.format_exc()
            _emit("HOOK_ERROR=%s" % (tb,))
            _restore_all()
            raise

        # ---- record: full frame ONLY for target-keeping calls ----
        if keep:
            if bucket in _STATE["target_buckets"]:
                _STATE["target_buckets"][bucket] += 1
            _STATE["target_record_emitted"] = True
            _STATE["final_verdict"] = result
            _emit("---")
            _emit("TUNING_#%d" % _STATE["observed"])
            _emit("RAW_ATTR=%r" % (raw_attr,))
            _emit("DISPLAY_ATTR=%r" % (disp_attr,))
            _emit("TUNING_TYPE=%s" % (tuning_type,))
            _emit("TUNING_MODULE=%s" % (tuning_module,))
            _emit("ANIMATION_OVERRIDE_PRESENT=%s" % (override_present,))
            _emit("RETURN_INSTANCE_DISPLAY_NAME=%r" % (ret_disp,))
            _emit("RETURN_INSTANCE_DISPLAY_NAME_OVERRIDE=%r" % (ret_override,))
            _emit("AUTHOR=%r" % (author,))
            _emit("ANIMATION_NAME=%r" % (anim_name,))
            _emit("ANIMATION_IDENTIFIER=%r" % (anim_id,))
            _emit("MATCH=TARGET")
            _emit("P29_RESULT=%s" % (result,))
        _STATE["last_result"] = result
        return ret

    _hook._p29tuning_orig = orig
    _hook._p29tuning_module = modname
    return _hook


def _restore_all():
    for modname, orig in list(_STATE.get("patched", {}).items()):
        try:
            import sys as _s
            mod = _s.modules.get(modname)
            if mod is not None and getattr(mod, _TUNING_FUNC, None) \
                    not in (None, orig):
                # only restore if currently our wrapper
                cur = getattr(mod, _TUNING_FUNC, None)
                if cur is not None and getattr(cur, "_p29tuning_orig", None) is orig:
                    setattr(mod, _TUNING_FUNC, orig)
        except Exception:
            pass
    _STATE["patched"] = {}
    _STATE["wrapped"] = False


def _looks_like_target(orig):
    """Refuse to wrap by name alone: require (animation_tuning) first param."""
    try:
        import inspect
        sig = list(inspect.signature(orig).parameters.keys())
    except Exception:
        return False
    if not sig:
        return False
    if sig[0] != "animation_tuning":
        return False
    return True


def _patch_module(mod, modname):
    """Rebind the loader function on THIS module + any importing alias modules."""
    orig = getattr(mod, _TUNING_FUNC, None)
    if orig is None or not callable(orig):
        return False
    if not _looks_like_target(orig):
        return False
    hook = _hook_fn_impl(orig, modname)
    # 1) local module binding (bare-name calls inside animations_loader)
    try:
        setattr(mod, _TUNING_FUNC, hook)
    except Exception:
        return False
    _STATE["patched"][modname] = orig
    # 2) any module that did `from ... import _create_sex_animation_instance`
    #    holds a dict entry bound to `orig` in its OWN globals -> re-point it.
    import sys as _s
    for other_name, other in list(_s.modules.items()):
        if not other_name:
            continue
        try:
            bound = getattr(other, _TUNING_FUNC, None)
        except Exception:
            bound = None
        if bound is None:
            continue
        if bound is orig and other is not mod:
            try:
                setattr(other, _TUNING_FUNC, hook)
                _STATE["patched"].setdefault(other_name, orig)
            except Exception:
                pass
    _STATE["wrapped"] = True
    # record the REAL loaded-function signature/defaults so the next run shows
    # exactly what omitted args resolve to in the actual WW build.
    for _digest_line in _signature_digest(orig):
        _emit(_digest_line)
    _emit("PASSTHROUGH_MODE=YES (args/kwargs forwarded verbatim, no authored defaults)")
    return True


def _runtime_candidates():
    out = []
    try:
        for n in list(sys.modules.keys()):
            nl = (n or "").lower()
            if "wickedwhims" in nl and ("animation" in nl or "sex" in nl
                                        or "loader" in nl):
                out.append(n)
    except Exception:
        pass
    return sorted(out)


def _try_patch(_trace=False):
    """Locate the loader module + function and wrap. Returns (ok, mod_present,
    func_present)."""
    mod_present = False
    func_present = False
    import sys as _s
    # module may only be importable once WW is registered; try both import and
    # sys.modules presence.
    for candidate in (_TUNING_MODULE,):
        try:
            mod = __import__(candidate, fromlist=["*"])
            mod_present = True
        except Exception:
            mod = _s.modules.get(candidate)
            if mod is None:
                continue
            mod_present = True
        if _patch_module(mod, candidate):
            func_present = True
            return (True, mod_present, func_present)
    # already-imported alias modules may host the function even before the parent
    # resolves; patch any module that holds a target-shaped binding
    for mname, other in list(_s.modules.items()):
        if not mname:
            continue
        try:
            bound = getattr(other, _TUNING_FUNC, None)
        except Exception:
            bound = None
        if bound is None or not callable(bound):
            continue
        if _patch_module(other, mname):
            func_present = True
            mod_present = True
            return (True, mod_present, func_present)
    return (False, mod_present, func_present)


def _trace_attempt(import_exc=None):
    idx = _STATE.get("retry_count", 0)
    _emit("RETRY_INDEX=%d" % idx)
    _emit("RETRY_AT=%s" % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
    _emit("MODULE_PRESENT=%s" % ("YES" if _STATE.get("last_module_present") else "NO"))
    _emit("FUNC_PRESENT=%s" % ("YES" if _STATE.get("last_func_present") else "NO"))
    _emit("IMPORT_EXCEPTION=%s" % (import_exc or "NONE"))
    cands = _runtime_candidates()
    _emit("RUNTIME_MODULE_CANDIDATES=%s" % (",".join(cands) if cands else "(none loaded yet)"))


def _retry_once():
    if _STATE.get("wrapped"):
        return True
    if _STATE.get("retry_count", 0) >= _STATE.get("max_retries", 20):
        return False
    _STATE["retry_count"] = _STATE.get("retry_count", 0) + 1
    import_exc = "NONE"
    try:
        ok, mp, fp = _try_patch(_trace=True)
        _STATE["last_module_present"] = mp
        _STATE["last_func_present"] = fp
    except Exception as e:
        ok, mp, fp = False, False, False
        import_exc = "%s: %s" % (type(e).__name__, e)
        _STATE["error"] = e
        _STATE["last_module_present"] = False
        _STATE["last_func_present"] = False
    _trace_attempt(import_exc)
    if ok:
        _STATE["retry_cb_executed"] = True
        _emit("HOOK_INSTALLED=YES")
        _emit("HOOK_MODULE=%s" % (_TUNING_MODULE,))
        _emit("HOOK_TARGET=%s.%s" % (_TUNING_MODULE, _TUNING_FUNC))
        _emit("HOOK_RETRY_INDEX=%d" % _STATE["retry_count"])
        _STATE["scheduler_armed"] = False
        return True
    return False


def _arm_via_zone(alarm_fire):
    armed = False
    note = "no sims4 zone service reachable yet"
    try:
        import sims4  # noqa: F401
        import services  # noqa: F401
    except Exception:
        return False, note
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
    preferred = ("on_loading_screen_ended", "loading_screen_ended",
                 "add_alarm", "register_on_zone_load", "on_zone_load",
                 "register_callback", "on_loading_screen_started",
                 "loading_screen_started")
    present = [m for m in preferred if callable(getattr(zone, m, None))]
    if not present:
        return False, "no zone host method matched; ZONE_SURFACE absent"
    def _rank(m):
        if m in ("on_loading_screen_ended", "loading_screen_ended"):
            return 0
        if m == "add_alarm":
            return 1
        if m in ("register_on_zone_load", "on_zone_load"):
            return 2
        if m == "register_callback":
            return 3
        return 4
    chosen = sorted(present, key=_rank)[0]
    try:
        getattr(zone, chosen)(alarm_fire)
        armed = True
        note = "zone.%s(%s)" % (chosen, ",".join(present) or "none")
    except Exception as e:
        note = "zone.%s raised %s; ZONE_SURFACE=%s" % (chosen, e, ",".join(present) or "none")
    return armed, note


def _register_scheduler():
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
    _restore_all()
    _STATE["wrapped"] = False
    _STATE["observed"] = 0
    _STATE["kept"] = 0
    _STATE["target_buckets"] = {"A_raw_changed": 0, "B_override_absent": 0,
                                "C_correct": 0}
    _STATE["seen_markers_on"] = {"raw": set(), "display": set()}
    _STATE["target_record_emitted"] = False
    _STATE["final_verdict"] = None  # D verdict is derived post-session ONLY by report_check
    _STATE["error"] = None
    _STATE["retry_count"] = 0
    _STATE["retry_cb_executed"] = False
    _STATE["scheduler_armed"] = False
    _STATE["last_module_present"] = False
    _STATE["last_func_present"] = False


def _emit_final_unused_removed():
    """(removed) The D verdict is derived POST-SESSION by ww_p29_tuning_report_check.py
    from the real log when HOOK_INSTALLED=YES with zero target frames and NO
    HOOK_ERROR.  This module never guesses TARGET_TUNING_NOT_OBSERVED mid-run."""
    return None


def main():
    _STATE["_log_path"] = _log_path()
    _log_header()
    ok = False
    for _ in range(3):
        if _retry_once():
            ok = True
            break
    if ok:
        _emit("VERDICT=TRACE_CAPTURED (armed; waiting for loader calls)")
        return
    armed = _register_scheduler()
    if not armed:
        _emit("HOOK_INSTALLED=NO")
        _emit("RETRY_COUNT=%d" % _STATE.get("retry_count", 0))
        _emit("LAST_MODULE_PRESENT=%s" % ("YES" if _STATE.get("last_module_present") else "NO"))
        _emit("IMPORT_EXCEPTION=%s" % (str(_STATE.get("error") or "NONE"),))
        _emit("VERDICT=FAIL_DISCOVERY")
        return
    _emit("VERDICT=DISCOVERY_PENDING (in-world retry armed)")


if __name__ == "__main__":
    main()

# Auto-run on import by the game.  Offline tests set WW_P29_DISABLE_AUTORUN=1.
if not os.environ.get("WW_P29_DISABLE_AUTORUN"):
    try:
        main()
    except Exception:
        pass
