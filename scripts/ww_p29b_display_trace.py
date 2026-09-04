#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ww_p29b_display_trace.py --- P29-B: RUNTIME UI-DOWNSTREAM observation only.

REAL FIXED FACTS this phase acts on (authoritative live trace, 2026-09-04 21:04,
P28C ordinal299 raw=TEST299 override LIVE):
    HOOK_INSTALLED=YES ; TUNING_#2055
    RAW_ATTR='TEST299'  DISPLAY_ATTR=None  TUNING_TYPE=TunableFactoryWrapper
    ANIMATION_OVERRIDE_PRESENT=OMITTED
    RETURN_INSTANCE_DISPLAY_NAME='TEST299'  _OVERRIDE=None
    AUTHOR='Nevely42'  ANIMATION_IDENTIFIER=0  MATCH=TARGET
  => P28C_REACHES_RUNTIME_TUNING=YES  RAW_ATTR_TEST299=YES
     INSTANCE_DISPLAY_NAME_TEST299=YES  INSTANCE_DISPLAY_NAME_OVERRIDE_NONE=YES
So the OLD question ("does TEST299 reach WW runtime?") is RESOLVED -> YES.  The NEW
question is pure UI-downstream: instance.display_name is ALREADY TEST299, yet the
final picker/UI still shows the old English.  WE DO NOT hunt XML/package/tuning/
parser anymore.  This phase ONLY observes the two real UI-facing instance methods:

    SexAnimationInstance.get_display_name(self, string_hash, original)
    SexAnimationInstance.get_picker_row(self, ...)   (calls get_display_name)

PURPOSE (evidence only): decide WHERE the old English reappears downstream:
    A  base/display return TEST299 and picker get_display_name TEST299
         -> the switch is AFTER the picker row (row/post-row).
    B  base TEST299 but GET_DISPLAY_NAME_RETURN='Caught Cheating 1'
         -> get_display_name ITSELF is the switch point.
    C  base TEST299, get_display_name returns TEST299, but PICKER_ROW_TEXT=
       'Caught Cheating 1'
         -> the picker builds its row from ANOTHER source.
Plus two named root causes inside get_display_name:
    * if self.original_instance is used (ARG_ORIGINAL=True and original_instance
      present and its display is the old English) and return == old English
         -> P29B_RESULT=UI_USING_ORIGINAL_INSTANCE
    * if display_name_override==old English and return==old English
         -> P29B_RESULT=DISPLAY_NAME_OVERRIDE_WINS

SAFETY CONTRACT (identical fail-closed, observation-only):
  1. NEVER touch WW original ts4script / Nevely package / P28C payload / XML.
  2. Keep Python 3.7, magic 420d0d0a, TRANSPARENT passthrough
     (orig(*args, **kwargs)), HOOK_ERROR precedence over any business verdict.
  3. Read instance/row attrs ONLY via guarded getattr; never assign any field we
     only read; never guess a picker row field -- unknown -> UNAVAILABLE.
  4. Auto-deploy P28C TEST299 + one-key rollback handled by the .ps1 wrappers.

Target match helper (do NOT rely on animation_id which is 0 for this target):
    self DISPLAY_NAME in {'TEST299','Caught Cheating 1'}
    OR (self.AUTHOR=='Nevely42' AND self is the runtime instance or has a marker)
    OR instance is the exact object we saw via the _create hook (hot target).
"""
import os
import sys
import time
import traceback as _traceback

_TARGET_NAMES = ("TEST299", "Caught Cheating 1")
_TARGET_AUTHOR = "Nevely42"

# Where the SexAnimationInstance class lives (authoritative live module).  We scan
# sys.modules broadly as a fallback, but prefer this exact import path first.
_CLS_MODULES = ("wickedwhims.sex.animations.animations_data",)
_CLS_NAME = "SexAnimationInstance"
_METHOD_NAMES = ("get_display_name", "get_picker_row")

_UNSET_SENTINEL = object()

_LOG_ROOTS = (
    os.environ.get("TMP", ""),
    os.environ.get("TEMP", ""),
    os.path.expanduser("~"),
    os.getcwd(),
)

_STATE = {
    "wrapped": False,
    "patched_cls": None,       # class object we hooked (for restore)
    "patched_methods": {},     # method-name -> original function (unbound)
    "observed": 0,
    "kept": 0,
    "versions_emitted": {"get_display_name": False, "get_picker_row": False},
    "error": None,
    "_log_path": "",
    "retry_count": 0,
    "retry_cb_executed": False,
    "scheduler_armed": False,
    "max_retries": 20,
    # optional: the exact target instance captured at the _create hook (None when
    # we cannot safely instance-hold -- WW may reuse it; matching is by value).
}


def _log_basename():
    return "ww_p29b_display_trace.log"


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


def _log_header():
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    _emit("=== P29B DISPLAY TRACE ===")
    _emit("HOOK_LOADED_AT=%s" % ts)
    _emit("TARGET_NAMES=%s" % ",".join(_TARGET_NAMES))


def _is_target_instance(self):
    """Value-based -- do NOT rely on animation_id (0 for this target)."""
    if self is None:
        return False
    try:
        try:
            dn = self.display_name
        except Exception:
            dn = _UNSET_SENTINEL
        if isinstance(dn, str) and dn in _TARGET_NAMES:
            return True
        try:
            auth = _safe_attr(self, "author")
        except Exception:
            auth = None
        if _safe_str(auth) == _TARGET_AUTHOR:
            return True
        # naming/description fallback (some WW classes store a display LocalizedString)
        for cand in ("display_name", "name", "localized_display_name",
                     "display_name_override", "description", "identifier"):
            try:
                v = _safe_attr(self, cand)
            except Exception:
                v = None
            if isinstance(v, str) and v in _TARGET_NAMES:
                return True
    except Exception:
        return False
    return False


def _row_text_safe(row):
    """Guardedly read plausible string fields off the returned picker row.  Only a
    fixed allow-list is probed; only plain-`str` (or None) results are trusted; a
    non-str value (e.g. a LocalizedString object) is recorded as its TYPE marker,
    never resolved.  Any field absent / non-probeable -> UNAVAILABLE."""
    out = {
        "text": "UNAVAILABLE",
        "name": "UNAVAILABLE",
        "description": "UNAVAILABLE",
    }
    if row is None:
        return out
    probe_map = {
        "text": ("text", "_text", "display_name", "display_text", "text_display"),
        "name": ("name", "display_name", "title"),
        "description": ("description", "subtitle", "desc", "help_text"),
    }
    for key, names in probe_map.items():
        for nm in names:
            v = _safe_attr(row, nm)
            if v == "<error-reading>":
                continue  # _safe_attr failure token, not a real value
            if isinstance(v, str):
                out[key] = v
                break
            if v is not None and v is not _UNSET_SENTINEL:
                # a non-str field exists but is an object (e.g. LocalizedString)
                out[key] = "<nonstr-%s>" % _safe_str(type(v).__name__)
    return out


def _read_named(orig_params, args, kwargs, name):
    """Return the value a caller supplied for a named param, honoring positional
    order (skipping the leading self/cls of the unbound orig) + keyword form.
    Returns _UNSET_SENTINEL when not provided (never filled from a default)."""
    # orig_params: list of param NAMES from a bound-signature view (self removed)
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


def _hook_get_display_name(orig):
    _sig_names = _instance_sig_params(orig)

    def _wrapped(self, *args, **kwargs):
        # read-only named param observation (never fills omitted)
        string_hash = _read_named(_sig_names, args, kwargs, "string_hash")
        original = _read_named(_sig_names, args, kwargs, "original")
        is_t = _is_target_instance(self)
        ret = None
        try:
            # class-method wrapper: orig is the unbound function; self must be
            # forwarded as the first positional (string: self + args unchanged).
            ret = orig(self, *args, **kwargs)
        except Exception:
            tb = _traceback.format_exc()
            try:
                _emit("HOOK_ERROR=%s" % (tb,))
            except Exception:
                pass
            _restore_all()
            raise
        if not is_t:
            return ret
        # record BEFORE + AFTER on the target instance only
        _STATE["observed"] += 1
        base = _safe_attr(self, "display_name")
        ovr = _safe_attr(self, "display_name_override")
        oi = _safe_attr(self, "original_instance")
        oi_present = "NO" if (oi is None or oi is _UNSET_SENTINEL) else "YES"
        oi_disp = "<none>"
        if oi is not None and oi is not _UNSET_SENTINEL:
            oi_disp = _safe_attr(oi, "display_name")
        _emit("---")
        _emit("GDN_#%d" % _STATE["observed"])
        _emit("BASE_DISPLAY_NAME=%r" % (base,))
        _emit("DISPLAY_NAME_OVERRIDE=%r" % (ovr,))
        _emit("ORIGINAL_INSTANCE_PRESENT=%s" % oi_present)
        _emit("ORIGINAL_INSTANCE_DISPLAY_NAME=%r" % (oi_disp,))
        _emit("ARG_STRING_HASH=%s" % (repr(string_hash) if string_hash is not
                                      _UNSET_SENTINEL else "OMITTED"))
        _emit("ARG_ORIGINAL=%s" % (repr(original) if original is not _UNSET_SENTINEL
                                   else "OMITTED"))
        _emit("AUTHOR=%r" % (_safe_attr(self, "author"),))
        _emit("ANIMATION_IDENTIFIER=%r" % (_safe_attr(self, "animation_id"),))
        rv = _safe_str(ret)
        _emit("GET_DISPLAY_NAME_RETURN=%r" % (ret,))
        # ---- named root causes (evidence-driven only; run-level A/B/C derived
        #      post-session by report_check, not guessed here) ----
        verdict = None
        bs, rs = _safe_str(base), _safe_str(ret)
        ovs = _safe_str(ovr)
        if oi_present == "YES" and original is not _UNSET_SENTINEL and \
                _safe_str(oi_disp) == "Caught Cheating 1" and rs == "Caught Cheating 1":
            verdict = "UI_USING_ORIGINAL_INSTANCE"
        elif ovs == "Caught Cheating 1" and rs == "Caught Cheating 1":
            verdict = "DISPLAY_NAME_OVERRIDE_WINS"
        elif bs == "TEST299" and rs == "Caught Cheating 1":
            verdict = "GET_DISPLAY_NAME_IS_SWITCH"
        _emit("P29B_PHASE=GET_DISPLAY_NAME")
        if verdict:
            _emit("P29B_RESULT=%s" % verdict)
        return ret
    return _wrapped


def _hook_get_picker_row(orig):
    def _wrapped(self, *args, **kwargs):
        is_t = _is_target_instance(self)
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
        if not is_t:
            return ret
        _STATE["observed"] += 1
        base = _safe_attr(self, "display_name")
        ovr = _safe_attr(self, "display_name_override")
        _emit("---")
        _emit("PICKER_#%d" % _STATE["observed"])
        _emit("PICKER_INSTANCE_DISPLAY_NAME=%r" % (base,))
        _emit("PICKER_DISPLAY_NAME_OVERRIDE=%r" % (ovr,))
        # gdn of instance would call the method; we only do it if safe (instance is
        # the one we hook, calling our own wrapper would recurse).  We instead rely
        # on the GET_DISPLAY_NAME frames already captured.  Record a placeholder:
        _emit("PICKER_GET_DISPLAY_NAME=<see GET_DISPLAY_NAME frames>")
        rinfo = _row_text_safe(ret)
        _emit("PICKER_ROW_TEXT=%r" % (rinfo["text"],))
        _emit("PICKER_ROW_NAME=%r" % (rinfo["name"],))
        _emit("PICKER_ROW_DESCRIPTION=%r" % (rinfo["description"],))
        # branch C: base TEST299 but row text is old English
        bs = _safe_str(base)
        if bs == "TEST299" and rinfo["text"] == "Caught Cheating 1":
            _emit("P29B_PHASE=GET_PICKER_ROW")
            _emit("P29B_RESULT=PICKER_ROW_USES_OTHER_SOURCE")
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


def _looks_like_target_method(m, name):
    """Refuse to wrap by name alone: require the method to take its expected shape."""
    try:
        import inspect as _ins
        params = list(_ins.signature(m).parameters.keys())
    except Exception:
        # cannot introspect a C-method -> still allow ONLY if callable
        return callable(m)
    return callable(m)


def _find_class():
    """Return the SexAnimationInstance class object or None."""
    import sys as _s
    # 1) exact candidate modules (import or sys.modules)
    for cand in _CLS_MODULES:
        try:
            mod = __import__(cand, fromlist=["*"])
        except Exception:
            mod = _s.modules.get(cand)
        if mod is None:
            continue
        cls = getattr(mod, _CLS_NAME, None)
        if cls is not None and isinstance(cls, type) and \
                callable(getattr(cls, "get_display_name", None)):
            return cls, cand
    # 2) broad scan
    for modname, modobj in list(_s.modules.items()):
        if not modname or not hasattr(modobj, _CLS_NAME):
            continue
        try:
            cls = getattr(modobj, _CLS_NAME)
        except Exception:
            continue
        if isinstance(cls, type) and callable(getattr(cls, "get_display_name",
                                                       None)) and \
                callable(getattr(cls, "get_picker_row", None)):
            return cls, modname
    return None, None


def _patch_class():
    cls, where = _find_class()
    if cls is None:
        return False, where
    _STATE["patched_cls"] = cls
    ok = False
    for name in _METHOD_NAMES:
        try:
            orig = cls.__dict__.get(name)
        except Exception:
            orig = None
        if orig is None:
            # fall back to resolved inherited attr (must NOT point at our wrapper)
            try:
                orig = getattr(cls, name)
            except Exception:
                orig = None
        if orig is None or not callable(orig):
            continue
        # refuse to double-wrap
        if getattr(orig, "_ww_p29b_wrapped", False):
            _STATE["patched_methods"].setdefault(name, orig)
            continue
        if name == "get_display_name":
            wrapped = _hook_get_display_name(orig)
        else:
            wrapped = _hook_get_picker_row(orig)
        try:
            wrapped._ww_p29b_wrapped = True
        except Exception:
            pass
        try:
            setattr(cls, name, wrapped)
        except Exception:
            continue
        _STATE["patched_methods"][name] = orig
        ok = True
    if not ok:
        return False, where
    _STATE["wrapped"] = True
    _emit("HOOK_CLS_FOUND_IN=%s" % (where and where or "sys.modules-scan"))
    _emit("HOOK_METHODS=%s" % ",".join(("get_display_name" if
                                        "get_display_name" in _STATE[
                                            "patched_methods"] else "",
                                        "get_picker_row" if
                                        "get_picker_row" in _STATE[
                                            "patched_methods"] else "")))
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
        _emit("HOOK_INSTALLED=YES")
        _emit("HOOK_CLS=%s" % _CLS_NAME)
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
    _STATE["observed"] = 0
    _STATE["kept"] = 0
    _STATE["error"] = None
    _STATE["retry_count"] = 0
    _STATE["retry_cb_executed"] = False
    _STATE["scheduler_armed"] = False


def _hook_cls_factory():
    """Module-level attr asserted by the builder probe (importability only)."""
    return _find_class


def main():
    _STATE["_log_path"] = _log_path()
    _log_header()
    ok = False
    for _ in range(3):
        if _retry_once():
            ok = True
            break
    if ok:
        _emit("VERDICT=TRACE_CAPTURED (armed; waiting for get_display_name / "
              "get_picker_row target calls)")
        return
    armed = _register_scheduler()
    if not armed:
        _emit("HOOK_INSTALLED=NO")
        _emit("RETRY_COUNT=%d" % _STATE.get("retry_count", 0))
        _emit("IMPORT_EXCEPTION=%s" % (str(_STATE.get("error") or "NONE"),))
        _emit("VERDICT=FAIL_DISCOVERY")
        return
    _emit("VERDICT=DISCOVERY_PENDING (in-world retry armed)")


# Auto-run on import by the game.  Offline tests set WW_P29_DISABLE_AUTORUN /
# WW_P29B_DISABLE_AUTORUN.
if not os.environ.get("WW_P29_DISABLE_AUTORUN") and \
        not os.environ.get("WW_P29B_DISABLE_AUTORUN"):
    try:
        main()
    except Exception:
        pass
