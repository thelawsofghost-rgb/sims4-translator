#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ww_p29c_display_caller_trace.py --- P29-C: TARGET-ONLY caller chain trace.

CONTEXT (evidence already closed by earlier phases, do NOT re-litigate):
    P28C        : Caught Cheating 2 -> TEST300            (ordinal 300, src SHA kept)
    P29-TUNING  : animation_raw_display_name='TEST300'
                  animation_display_name=None
                  SexAnimationInstance.display_name='TEST300'
    P29-B       : BASE_DISPLAY_NAME='TEST300'
                  DISPLAY_NAME_OVERRIDE=None
                  ORIGINAL_INSTANCE_PRESENT=NO
                  GET_DISPLAY_NAME_RETURN='TEST300'   (the UI still shows old English)
    and P29-B did NOT capture PICKER_INSTANCE_DISPLAY_NAME='TEST300'.

So XML / STBL / tuning / constructor / original_instance are all EXPLAINED and
excluded as the UI switch point.  The remaining unknown: once
`SexAnimationInstance.get_display_name()` returns 'TEST300', WHO calls it and how
does the value reach (or fail to reach) the visible UI?

P29-C therefore hooks ONLY `SexAnimationInstance.get_display_name` and, ONLY when
`self.display_name == 'TEST300'`, records a real RUNTIME CALLER CHAIN:
  - who called (file:function:line, up to 5 frames)
  - the returned object's precise type/repr/value
  - the nearest 3 caller frames' locals, filtered+repr-bounded (no huge dumps)
It is STRICTLY observation-only: original fn is called, args/kwargs/return/self are
never modified.  Non-target animations log nothing detailed (no log explosion).

ARCHITECTURE / BOOTSTRAP: byte-shaped like the P29-TUNING/P29-B-proven path.  Same
module-scope `if: main()` auto-run, same 3x retry + scheduler fallback, same
_SexAnimationInstance discovery by VALUE not animation_id, py3.7 pyc magic 420d0d0a.
Auto-deploy of the EXISTING P28C generator (ordinal 300) is handled by the .ps1
wrappers, never repeated or rewritten here.

SAFETY CONTRACT (fail-closed, observation-only):
  1. NEVER touch WW source ts4script / Nevely package / P28C generator / XML / STBL.
  2. TRANSPARENT passthrough: orig(self, *args, **kwargs); original return returned.
  3. No set_display_name, no setattr on self, no mutation of any object we read.
  4. Every frame/local/attr read is guarded; a single failure -> '<error-reading>' and
     the trace continues (never crash the game).
  5. repr bounded to _MAX_LOCAL_RPR (500 chars); at most 30 target calls recorded.
"""
import os
import sys
import time
import traceback as _traceback

_TARGET_OLD_RAW = "Caught Cheating 2"
_TARGET_NEW_RAW = "TEST300"
_TARGET_DISPLAY = _TARGET_NEW_RAW            # the ONLY display value we trace in depth
_TARGET_AUTHOR = "Nevely42"

_CLS_MODULES = ("wickedwhims.sex.animations.animations_data",)
_CLS_NAME = "SexAnimationInstance"
_METHOD_NAME = "get_display_name"

_MAX_TARGET_CALLS = 30
_MAX_LOCAL_RPR = 500
_MAX_CALLER_FRAMES = 5
_MAX_LOCAL_FRAMES = 3
_LOCAL_KEYWORDS = (
    "display", "name", "text", "row", "picker", "animation", "label",
    "description", "title", "tooltip", "localized", "string",
)
_RETURN_ATTR_PROBES = ("hash", "string_id", "text", "value", "key")

_UNSET_SENTINEL = object()

_LOG_ROOTS = (
    os.environ.get("TMP", ""),
    os.environ.get("TEMP", ""),
    os.path.expanduser("~"),
    os.getcwd(),
)

_STATE = {
    "wrapped": False,
    "patched_cls": None,
    "patched_methods": {},
    "target_calls": 0,
    "limit_reached": False,
    "error": None,
    "_log_path": "",
    "retry_count": 0,
    "retry_cb_executed": False,
    "scheduler_armed": False,
    "max_retries": 20,
}


def _log_basename():
    return "ww_p29c_display_caller_trace.log"


def _log_path():
    for root in _LOG_ROOTS:
        if not root:
            continue
        try:
            p = os.path.join(root, _log_basename())
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


def _safe_str(x):
    if x is None:
        return None
    try:
        return str(x)
    except Exception:
        return "<??>"


def _safe_repr(x, limit=_MAX_LOCAL_RPR):
    """Bounded repr.  Non-trivial objects dump no deeper than `limit` chars."""
    out = "<error-reading>"
    try:
        out = repr(x)
    except Exception:
        out = "<error-reading>"
    if not isinstance(out, str):
        return "<error-reading>"
    if len(out) > limit:
        out = out[:limit] + "...<truncated-%d>" % len(out)
    return out


def _log_header():
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    _emit("=== P29C DISPLAY CALLER TRACE ===")
    _emit("HOOK_LOADED_AT=%s" % ts)
    _emit("TARGET_DISPLAY=%s" % _TARGET_DISPLAY)


def _frame_code_name(fr):
    try:
        c = fr.f_code
        return c.co_name
    except Exception:
        return "<error-reading>"


def _frame_info(fr):
    """Safe (module, function, filename, line) for one frame; per-field guarded."""
    out = {}
    try:
        out["func"] = fr.f_code.co_name
    except Exception:
        out["func"] = "<error-reading>"
    try:
        out["file"] = fr.f_code.co_filename
    except Exception:
        out["file"] = "<error-reading>"
    try:
        out["line"] = fr.f_lineno
    except Exception:
        out["line"] = "<error-reading>"
    try:
        gmod = None
        g = fr.f_globals
        gmod = g.get("__name__")
        if gmod is None:
            gmod = "<unknown>"
        out["mod"] = gmod
    except Exception:
        out["mod"] = "<error-reading>"
    return out


def _caller_frames(depth=_MAX_CALLER_FRAMES):
    """Walk f_back from the hook wrapper; skip ALL frames belonging to this trace
    module (our own inline helpers add noise) so CALLER_1 is the FIRST real external
    caller of get_display_name (the actual WW caller chain).  Guarded: any bad frame
    yields an error row and we continue walking; never raise."""
    out = []
    try:
        import inspect as _ins
        cur = _ins.currentframe()
        if cur is not None:
            cur = cur.f_back  # out of this function
    except Exception:
        cur = None
    _SELF_MODULE = __name__
    while len(out) < depth:
        if cur is None:
            break
        # skip frames that belong to THIS tracing module (internal noise)
        try:
            this_mod = cur.f_globals.get("__name__")
        except Exception:
            this_mod = None
        if this_mod == _SELF_MODULE:
            try:
                cur = cur.f_back
            except Exception:
                break
            continue
        try:
            finfo = _frame_info(cur)
            finfo["frame"] = cur
            out.append(finfo)
        except Exception:
            out.append({"func": "<error-reading>", "file": "<error-reading>",
                        "line": "<error-reading>", "mod": "<error-reading>",
                        "frame": None})
        try:
            cur = cur.f_back
        except Exception:
            break
    return out


def _local_filtered(fr, depth=0):
    """Collect locals of one frame that are cheap to record.  Returns a dict of
    name -> bounded repr only for locals whose NAME contains a display keyword and
    which survive a guarded read.  Never dumps whole large objects."""
    if fr is None:
        return {}
    try:
        locs = fr.f_locals
    except Exception:
        return {}
    if not isinstance(locs, dict):
        return {}
    res = {}
    try:
        keys = list(locs.keys())
    except Exception:
        keys = []
    for k in keys:
        if not isinstance(k, str):
            continue
        kl = k.lower()
        if not any(w in kl for w in _LOCAL_KEYWORDS):
            continue
        try:
            v = locs[k]
        except Exception:
            res.setdefault("_errors", []).append(k)
            continue
        # Cheap scalar-ish objects only repr at bounded length; functions/modules/
        # classes frame too deep -> record type marker only.
        tname = _safe_str(type(v).__name__)
        try:
            import types as _t
            if isinstance(v, (_t.ModuleType, _t.FunctionType, _t.BuiltinFunctionType)):
                res[k] = "<%s:%s>" % (tname, _safe_str(getattr(v, "__name__", "")))
                continue
        except Exception:
            pass
        if isinstance(v, (str, int, float, bool)) or v is None:
            res[k] = _safe_repr(v)
        else:
            # object, tuple, list, etc -> bounded repr only (500 cap)
            res[k] = _safe_repr(v)
    return res


def _emit_callers():
    frames = _caller_frames()
    for idx, fr in enumerate(frames, start=1):
        if idx > _MAX_CALLER_FRAMES:
            break
        prefix = "CALLER_%d" % idx
        _emit("%s_MODULE=%s" % (prefix, fr.get("mod", "<error-reading>")))
        _emit("%s_FUNCTION=%s" % (prefix, fr.get("func", "<error-reading>")))
        _emit("%s_FILENAME=%s" % (prefix, fr.get("file", "<error-reading>")))
        _emit("%s_LINE=%s" % (prefix, fr.get("line", "<error-reading>")))
        # locals only for the NEAREST _MAX_LOCAL_FRAMES caller frames
        if idx <= _MAX_LOCAL_FRAMES:
            locmap = _local_filtered(fr.get("frame")) if fr.get("frame") else {}
            errs = locmap.pop("_errors", [])
            if errs:
                _emit("%s_LOCAL_ERRORS=%s" % (prefix, ",".join(errs)))
            if locmap:
                for k in sorted(locmap.keys()):
                    _emit("%s_LOCAL_%s=%s" % (prefix, k, locmap[k]))
            else:
                if not errs:
                    _emit("%s_LOCAL_NONE=<no display-keyword locals>" % prefix)
    if not frames:
        _emit("CALLER_NONE=YES")


def _read_named(orig_params, args, kwargs, name):
    """Value a caller supplied for a named param, honoring positional + keyword form.
    Returns _UNSET_SENTINEL when omitted (never fills from a default)."""
    if name in kwargs:
        return kwargs[name]
    for i, pn in enumerate(orig_params):
        if pn == name:
            if i < len(args):
                return args[i]
            return _UNSET_SENTINEL
    return _UNSET_SENTINEL


def _instance_sig_params(orig):
    """Names of orig's params EXCLUDING the leading self/cls (bound-method view)."""
    import inspect as _ins
    try:
        names = list(_ins.signature(orig).parameters.keys())
    except Exception:
        return []
    if names and names[0] in ("self", "cls"):
        names = names[1:]
    return names


def _return_details(ret):
    """Precise describe of the returned object (never mutates it)."""
    if isinstance(ret, str):
        return {
            "is_str": "YES",
            "class": "str",
            "module": "<builtin>",
            "rep": _safe_repr(ret, 5000) if len(ret) > _MAX_LOCAL_RPR else repr(ret),
            "value": ret,
        }
    out = {
        "is_str": "NO",
        "class": _safe_str(type(ret).__name__),
        "module": "<unknown>",
        "rep": _safe_repr(ret),
        "value": "<nonstr>",
    }
    try:
        out["module"] = _safe_str(type(ret).__module__)
    except Exception:
        out["module"] = "<error-reading>"
    for attr in _RETURN_ATTR_PROBES:
        v = _safe_attr(ret, attr)
        if v == "<error-reading>" or v is None:
            continue
        out["attr_%s" % attr] = _safe_repr(v, 300)
    return out


def _hook_get_display_name(orig):
    _sig_names = _instance_sig_params(orig)

    def _wrapped(self, *args, **kwargs):
        # --- target-only gating decided from the PRE-ORIGINAL display_name so a call
        # whose instance already carries 'TEST300' is the one we deep-trace.  The
        # original is still invoked UNCONDITIONALLY and its return returned verbatim
        # (observation-only).  Guarded reads never crash the game. ---
        try:
            dn_before = self.display_name
        except Exception:
            dn_before = None
        is_target = isinstance(dn_before, str) and dn_before == _TARGET_DISPLAY
        # --- observation-only passthrough; error path restores & re-raises ---
        ret = None
        try:
            ret = orig(self, *args, **kwargs)
        except Exception:
            tb = _traceback.format_exc()
            try:
                _emit("HOOK_ERROR=%s" % (tb,))
            except Exception:
                pass
            _restore_all()
            raise
        if not is_target:
            return ret  # thousands of other animations: record nothing detailed
        # re-read after the call for the emitted SELF_DISPLAY_NAME (informational)
        try:
            dn = self.display_name
        except Exception:
            dn = dn_before or None
        st = _STATE
        idx = st.get("target_calls", 0) + 1
        if idx > _MAX_TARGET_CALLS:
            if not st.get("limit_reached"):
                st["limit_reached"] = True
                _emit("TARGET_TRACE_LIMIT_REACHED=YES")
            return ret
        st["target_calls"] = idx
        # ---- record this target call ----
        _emit("---")
        _emit("P29C_TARGET_CALL_BEGIN")
        _emit("TARGET_CALL_INDEX=%d" % idx)
        _emit("SELF_DISPLAY_NAME=%r" % (dn,))
        _emit("SELF_CLASS=%s" % _safe_str(type(self).__name__))
        _emit("SELF_AUTHOR=%r" % (_safe_attr(self, "author"),))
        oi = _safe_attr(self, "original_instance")
        _emit("ORIGINAL_INSTANCE_PRESENT=%s" %
              ("YES" if (oi is not None and oi is not _UNSET_SENTINEL) else "NO"))
        ovr = _safe_attr(self, "display_name_override")
        _emit("DISPLAY_NAME_OVERRIDE=%r" % (ovr,))
        # ---- args (never modified) ----
        _read_kw = _read_named
        string_hash = _read_kw(_sig_names, args, kwargs, "string_hash")
        original = _read_kw(_sig_names, args, kwargs, "original")
        _emit("ARG_STRING_HASH=%s" % (repr(string_hash) if string_hash is not
                                      _UNSET_SENTINEL else "OMITTED"))
        _emit("ARG_ORIGINAL=%s" % (repr(original) if original is not
                                   _UNSET_SENTINEL else "OMITTED"))
        # ---- return object details (never mutate) ----
        rd = _return_details(ret)
        _emit("RETURN_IS_STR=%s" % rd["is_str"])
        _emit("RETURN_CLASS=%s" % rd["class"])
        _emit("RETURN_MODULE=%s" % rd["module"])
        _emit("RETURN_REPR=%s" % rd["rep"])
        if rd["is_str"] == "YES":
            _emit("RETURN_VALUE=%r" % ret)
        else:
            for k in sorted(rd.keys()):
                if k.startswith("attr_"):
                    _emit("RETURN_%s=%s" % (k[len("attr_"):].upper(), rd[k]))
        # ---- caller chain (up to 5) + filtered locals (nearest 3) ----
        _emit_callers()
        _emit("P29C_TARGET_CALL_END")
        return ret
    return _wrapped


def _restore_all():
    cls = _STATE.get("patched_cls")
    if cls is not None:
        for name, orig in list(_STATE.get("patched_methods", {}).items()):
            try:
                setattr(cls, name, orig)
            except Exception:
                pass
    _STATE["patched_cls"] = None
    _STATE["patched_methods"] = {}
    _STATE["wrapped"] = False


def _looks_like_target_method(m):
    """Refuse to wrap by name alone: require callable (name+signature identity is
    established separately by the discovery pre-checks)."""
    return callable(m)


def _find_class():
    import sys as _s
    for cand in _CLS_MODULES:
        try:
            mod = __import__(cand, fromlist=["*"])
        except Exception:
            mod = _s.modules.get(cand)
        if mod is None:
            continue
        cls = getattr(mod, _CLS_NAME, None)
        if cls is not None and isinstance(cls, type) and \
                callable(getattr(cls, _METHOD_NAME, None)):
            return cls, cand
    for modname, modobj in list(_s.modules.items()):
        if not modname or not hasattr(modobj, _CLS_NAME):
            continue
        try:
            cls = getattr(modobj, _CLS_NAME)
        except Exception:
            continue
        if isinstance(cls, type) and callable(getattr(cls, _METHOD_NAME, None)):
            return cls, modname
    return None, None


def _patch_class():
    cls, where = _find_class()
    if cls is None:
        return False, where
    _STATE["patched_cls"] = cls
    try:
        orig = cls.__dict__.get(_METHOD_NAME)
    except Exception:
        orig = None
    if orig is None:
        try:
            orig = getattr(cls, _METHOD_NAME)
        except Exception:
            orig = None
    if orig is None or not callable(orig):
        return False, where
    if getattr(orig, "_ww_p29c_wrapped", False):
        _STATE["patched_methods"].setdefault(_METHOD_NAME, orig)
        _STATE["wrapped"] = True
        return True, where
    wrapped = _hook_get_display_name(orig)
    try:
        wrapped._ww_p29c_wrapped = True
    except Exception:
        pass
    try:
        setattr(cls, _METHOD_NAME, wrapped)
    except Exception:
        return False, where
    _STATE["patched_methods"][_METHOD_NAME] = orig
    _STATE["wrapped"] = True
    _emit("HOOK_CLS_FOUND_IN=%s" % (where and where or "sys.modules-scan"))
    _emit("HOOK_METHOD=%s" % _METHOD_NAME)
    return True, where


def _runtime_candidates():
    out = []
    try:
        for n in list(sys.modules.keys()):
            nl = (n or "").lower()
            if "wickedwhims" in nl and ("animation" in nl or "sex" in nl):
                out.append(n)
    except Exception:
        pass
    return sorted(out)


def _try_patch(_trace=False):
    mod_present = False
    for cand in _CLS_MODULES:
        try:
            __import__(cand, fromlist=["*"])
            mod_present = True
            break
        except Exception:
            pass
    ok, where = _patch_class()
    return ok, mod_present, (where is not None)


def _trace_attempt(import_exc=None):
    idx = _STATE.get("retry_count", 0)
    _emit("RETRY_INDEX=%d" % idx)
    _emit("MODULE_PRESENT=%s" % ("YES" if _STATE.get("last_module_present") else "NO"))
    _emit("CLS_PRESENT=%s" % ("YES" if _STATE.get("last_cls_present") else "NO"))
    _emit("IMPORT_EXCEPTION=%s" % (import_exc or "NONE"))
    cands = _runtime_candidates()
    _emit("RUNTIME_MODULE_CANDIDATES=%s" % (",".join(cands) if cands else "(none yet)"))


def _retry_once():
    if _STATE.get("wrapped"):
        return True
    if _STATE.get("retry_count", 0) >= _STATE.get("max_retries", 20):
        return False
    _STATE["retry_count"] = _STATE.get("retry_count", 0) + 1
    import_exc = "NONE"
    try:
        ok, mp, cp = _try_patch()
        _STATE["last_module_present"] = mp
        _STATE["last_cls_present"] = cp
    except Exception as e:
        ok = False
        import_exc = "%s: %s" % (type(e).__name__, e)
        _STATE["error"] = e
        _STATE["last_module_present"] = False
        _STATE["last_cls_present"] = False
    _trace_attempt(import_exc)
    if ok:
        _STATE["retry_cb_executed"] = True
        _emit("HOOK_TARGET=%s.get_display_name" % _CLS_NAME)
        _emit("HOOK_INSTALLED=YES")
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
    preferred = ("on_loading_screen_ended", "loading_screen_ended", "add_alarm",
                 "register_on_zone_load", "on_zone_load", "register_callback",
                 "on_loading_screen_started", "loading_screen_started")
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
    except Exception:
        armed = False
    if armed:
        _STATE["scheduler_armed"] = True
    return armed, ("zone.%s for %s" % (chosen, ",".join(present) or "none"))


def _register_scheduler():
    if _STATE.get("wrapped"):
        return True
    armed, note = _arm_via_zone(_retry_once)
    _STATE["scheduler_armed"] = armed
    _emit("SCHEDULER_ARMED=%s (%s)" % ("YES" if armed else "NO", note))
    return armed


def _reset_state_for_test():
    _restore_all()
    _STATE["wrapped"] = False
    _STATE["target_calls"] = 0
    _STATE["limit_reached"] = False
    _STATE["error"] = None
    _STATE["retry_count"] = 0
    _STATE["retry_cb_executed"] = False
    _STATE["scheduler_armed"] = False


def _hook_cls_factory():
    """Module-level attr asserted by the builder probe (importability only)."""
    return _find_class


def _bootstrap_boot_marker():
    """Earliest-possible in-game proof-of-import (P29-B-proven pattern).  Written as
    the very first executed statements, before any WW discovery / scheduling."""
    path = _log_path()
    _STATE["_log_path"] = path
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    _log_header()
    _emit("P29C_MODULE_IMPORTED=YES")
    _emit("BOOT_AT=%s" % ts)
    _emit("MODULE_NAME=ww_p29c_display_caller_trace")
    _emit("TARGET_DISPLAY=%s" % _TARGET_DISPLAY)


def main():
    _bootstrap_boot_marker()
    ok = False
    for _ in range(3):
        if _retry_once():
            ok = True
            break
    if ok:
        _emit("VERDICT=TRACE_CAPTURED (armed; only self.display_name=='TEST300' "
              "get_display_name calls are traced in depth)")
        return
    armed = _register_scheduler()
    if not armed:
        _emit("HOOK_INSTALLED=NO")
        _emit("RETRY_COUNT=%d" % _STATE.get("retry_count", 0))
        _emit("IMPORT_EXCEPTION=%s" % (str(_STATE.get("error") or "NONE"),))
        _emit("VERDICT=FAIL_DISCOVERY")
        return
    _emit("VERDICT=DISCOVERY_PENDING (in-world retry armed)")


# Auto-run on import by the game.  P29-TUNING/P29-B-proven bootstrap; offline tests
# set WW_P29_DISABLE_AUTORUN / WW_P29C_DISABLE_AUTORUN.
_RUN = (not os.environ.get("WW_P29_DISABLE_AUTORUN")) and (not os.environ.get(
    "WW_P29C_DISABLE_AUTORUN") and not os.environ.get("WW_P29B_DISABLE_AUTORUN"))
if _RUN:
    try:
        main()
    except Exception:
        pass
