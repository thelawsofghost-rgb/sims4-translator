#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ww_p29e_picker_row_probe.py --- P29-E: narrow RUNTIME IDENTITY probe.

GOAL (observe only; NOT a rename / NOT a source swap):
    Determine which `SexAnimationInstance` actually drives the "change animation"
    UI row when the player opens the picker, versus the single instance our
    P29-C/P29-D static+lock path reached ('TEST300').

CONTEXT (already closed, do not re-litigate):
    P28C/TUNING/P29-B/P29-C/P29-D  : the pipeline instance is SexAnimationInstance
        with display_name='TEST300' -> get_display_name()='TEST300' ->
        SexAnimationPickerRow(..., name=get_l18n_service().get_localized_string(
        get_display_name()), ...).  SexAnimationPickerRow / TurboObjectPickerRow /
        ObjectPickerRow do NOT re-query/remap `name`.  get_localized_string on a str
        does NOT do STBL/hash lookup (goes to LocalizationHelperTuning.get_raw_text).
    Yet the visible UI picker still shows "Caught Cheating 2".

REMAINING HYPOTHESIS this probe tests with runtime identity evidence:
    The widget actually built/presented by the UI comes from a SexAnimationInstance
    that may NOT be the exact object P29-C locked to 'TEST300' (e.g. a second/old
    cached instance for the same animation_id / identifier).

SCOPE LOCK (only this one method is hooked):
    module  wickedwhims.sex.animations.animation_instance  (preferred; discovery
    also accepts the P29-C-proven host / any loaded module) class SexAnimationInstance
    method  get_picker_row

The hook records, PER CALL, a block:
    P29E_PICKER_ROW_BEGIN
      CALL_INDEX=
      PY_OBJECT_ID=         hex(id(self))
      ANIMATION_ID=         self.get_animation_id()
      AUTHOR=               self.get_author()
      DISPLAY_NAME_ATTR=    repr(self.display_name)
      DISPLAY_NAME_OVERRIDE=repr(self.display_name_override)
      STAGE_NAME_ATTR=      repr(self.animation_stage_name)
      IDENTIFIER=           repr(self.get_identifier())
      ROW_CLASS=
      ROW_IDENTIFIER=
      ROW_NAME_ATTR=        repr(row.name)  (only if the row exposes .name)
      ROW_GET_NAME=         repr(row.get_name()) when row.get_name callable else ABSENT
    P29E_PICKER_ROW_END

FILTER (P29-E2: AUTHOR-only).  Authoritative fix for the real-machine gap where the
UI showed other Nevely42 rows but the narrow locator filter never fired.  Depth is now
recorded for EVERY call whose author == "Nevely42" REGARDLESS of display_name or
stage_name.  display_name / stage_name are still captured as block FIELDS but are NOT
used to decide whether to record.  All other authors return the original result
silently -- no P29E_NON_TARGET_SKIPPED spam, only a single monotonic non-Nevely
counter (P29E_NON_NEVELY_SKIPPED=N) is written once, then never again.

NOTE: stage_name remains only a captured FIELD, never a UI-source assumption
(P29-D excluded animation_stage_name as the UI title source).

If the SAME animation_id / identifier maps to multiple PY_OBJECT_ID values, every one
is recorded (each is a separate gated call; identical instance id -> same block).

SAFETY CONTRACT (P29-C-proven; fail-closed, observation-only):
    1. NEVER touches WW source / Nevely package / XML / STBL / P28C generator.
    2. NEITHER set_display_name NOR any setattr on self; original called verbatim
       with original args/kwargs; its return returned verbatim.
    3. Every attribute/method read is GUARDED; a single failure yields
       '<error-reading>' and the block continues; never crash the game.
    4. repr bounded (500 chars).  Nevely42-author depth is capped
       (_NEVELY_CAP -> P29E_NEVELY_LIMIT_REACHED) to avoid unbounded growth.
    5. Python 3.7-clean (same as game).  No 3.8+ stdlib API.
Auto-run on import is gated by WW_P29_*_DISABLE_AUTORUN so offline logic tests can
drive the wrapper without a game.
"""
import os
import sys
import time
import traceback as _traceback

_TARGET_AUTHOR = "Nevely42"
_NEVELY_CAP = 200            # depth cap for Nevely42 calls (P29-E2 widening)

# Preferred host modules for the class; an ordered list.  `animation_instance` is the
# task-stated host; the others are the P29-C-proven real hosts, used only as a
# discovery fallback (never assumed ahead of runtime).
_CLS_MODULES = (
    "wickedwhims.sex.animations.animation_instance",
    "wickedwhims.sex.animations.animations_data",
    "wickedwhims.sex.animations.animations_operator",
)
_CLS_NAME = "SexAnimationInstance"
_METHOD_NAME = "get_picker_row"

_MAX_LOCAL_RPR = 500
_UNSET_SENTINEL = object()

_STATE = {
    "wrapped": False,
    "patched_cls": None,
    "patched_methods": {},
    "calls": 0,
    "nevely_limit_reached": False,
    "silent_non_nevely_calls": 0,
    "error": None,
    "_log_path": "",
    "retry_count": 0,
    "retry_cb_executed": False,
    "scheduler_armed": False,
    "max_retries": 30,
}


# Deterministic on-game log destination.
#
# The real machine ALWAYS logs to this fixed absolute path -- no TEMP/TMP/HOME/CWD
# "first writable root" fallback (that ambiguity is exactly what made "did the
# module even run / where did it log?" undecidable).  Offline logic tests may set
# WW_P29E_LOG_PATH to a writable temp file so the same code stays OS-portable; on
# the real machine the variable is unset and the fixed absolute path below is used.
_DEFAULT_LOG_PATH = ("C:\\Users\\thela\\Documents\\Electronic Arts\\The Sims 4"
                     + "\\p29e_picker_row_probe.log")


def _resolve_log_path():
    override = os.environ.get("WW_P29E_LOG_PATH") or ""
    if override.strip():
        return override.strip()
    return _DEFAULT_LOG_PATH


def _log_basename():
    return "p29e_picker_row_probe.log"


def _log_path():
    # Deterministic: env override if set, else the fixed absolute game path.
    # (No first-writable fallback; always one known target.)
    return _resolve_log_path()


def _raw_log_append(text):
    """Append TEXT + newline (UTF-8) to the deterministic log file.

    Independent of _STATE/_emit/_log_path, which only take effect once main()'s
    boot runs -- so the module-enter / env-state / main-exception lines below are
    recorded even if main() is never reached (or only fires much later via the
    in-world retry path).  Never raises: a logging failure drops the line quietly
    but the module still imports and the game never sees an exception.
    """
    try:
        path = _resolve_log_path()
    except Exception:
        path = _DEFAULT_LOG_PATH
    # Only write to an ABSOLUTE target.  The fixed on-Windows default is a real
    # drive-letter absolute path (os.path.isabs True on Windows) and the
    # WW_P29E_LOG_PATH override on any host is an absolute path in practice (a
    # tempfile).  A NON-absolute path means we are offline on a non-Windows host
    # where the drive-letter default would otherwise be misread as a relative
    # filename containing literal backslashes and create a bogus "C:\..." file in
    # the current directory.  Guard that out: skip the write entirely (dropped
    # quietly) so offline/logic-test runs never spray that artifact; the real
    # machine is Windows and is unaffected.
    if not os.path.isabs(path):
        return
    try:
        parent = os.path.dirname(path)
        if parent and not os.path.exists(parent):
            try:
                os.makedirs(parent)
            except Exception:
                pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass


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
    """Read attribute without side effects; never call anything."""
    try:
        return getattr(obj, name)
    except Exception:
        return "<error-reading>"


def _safe_call0(obj, name):
    """Call a zero-arg method rea-only; guard everything."""
    try:
        v = getattr(obj, name)
    except Exception:
        return "<error-reading>"
    try:
        return v() if callable(v) else "<not-callable:%s>" % repr(v)[:120]
    except Exception:
        return "<error-reading>"


def _safe_repr(x, limit=_MAX_LOCAL_RPR):
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
    _emit("=== P29E PICKER ROW IDENTITY PROBE ===")
    _emit("HOOK_LOADED_AT=%s" % ts)
    _emit("TARGET_AUTHOR=%s" % _TARGET_AUTHOR)
    _emit("AUTHOR_ONLY_FILTER=YES")


def _instance_read(self):
    """Guard-read all per-instance identity values recorded as block fields."""
    got = {}
    got["author"] = _safe_attr(self, "author")
    got["display_name"] = _safe_attr(self, "display_name")
    got["stage_name"] = _safe_attr(self, "animation_stage_name")
    return got


def _is_nevely(got):
    """P29-E2 gating: record depth for EVERY Nevely42 author call."""
    return got.get("author") == _TARGET_AUTHOR


def _hook_get_picker_row(orig):
    def _wrapped(self, *args, **kwargs):
        got0 = _instance_read(self)
        target = _is_nevely(got0)
        # --- observation-only passthrough (always call orig) ---
        ret = None
        try:
            ret = orig(self, *args, **kwargs)
        except Exception:
            tb = _traceback.format_exc()
            try:
                _emit("P29E_HOOK_ERROR=%s" % (tb,))
            except Exception:
                pass
            if target:
                _restore_all()
            raise
        if not target:
            # P29-E2: non-Nevely authors are silent passthrough (original result
            # returned, NO per-call skip line).  Only a monotonic counter is kept
            # so the boot summary can show how many non-Nevely rows were walked.
            _STATE["silent_non_nevely_calls"] = \
                _STATE.get("silent_non_nevely_calls", 0) + 1
            return ret
        st = _STATE
        idx = st.get("calls", 0) + 1
        if idx > _NEVELY_CAP:
            if not st.get("nevely_limit_reached"):
                st["nevely_limit_reached"] = True
                _emit("P29E_NEVELY_LIMIT_REACHED=YES")
            return ret
        st["calls"] = idx
        # re-read AFTER the original for the emitted values (informational only)
        got = got0
        try:
            got["display_name"] = _safe_attr(self, "display_name")
        except Exception:
            pass
        self_repr = _safe_repr(self) if self is not None else "None"
        try:
            oid = hex(id(self))
        except Exception:
            oid = "<error-reading>"
        _emit("---")
        _emit("P29E_PICKER_ROW_BEGIN")
        _emit("CALL_INDEX=%d" % idx)
        _emit("PY_OBJECT_ID=%s" % oid)
        _emit("SELF_REPR=%s" % self_repr)
        _emit("ANIMATION_ID=%s" % _safe_repr(_safe_call0(self, "get_animation_id")))
        _emit("AUTHOR=%r" % (got.get("author"),))
        _emit("DISPLAY_NAME_ATTR=%r" % (got.get("display_name"),))
        _emit("DISPLAY_NAME_OVERRIDE=%r" %
              (_safe_attr(self, "display_name_override"),))
        _emit("STAGE_NAME_ATTR=%r" % (got.get("stage_name"),))
        _emit("IDENTIFIER=%s" % _safe_repr(_safe_call0(self, "get_identifier")))
        # ---- row introspection (from the ORIGINAL return; never mutated) ----
        row = ret
        if row is None:
            _emit("ROW_CLASS=None")
            _emit("ROW_IDENTIFIER=<n/a>")
            _emit("ROW_NAME_ATTR=<n/a>")
            _emit("ROW_GET_NAME=<n/a>")
        else:
            try:
                rcls = type(row).__name__
            except Exception:
                rcls = "<error-reading>"
            _emit("ROW_CLASS=%s" % rcls)
            row_id = None
            for attr in ("identifier", "key"):
                v = _safe_attr(row, attr)
                if v != "<error-reading>" and v is not None:
                    row_id = v
                    break
            _emit("ROW_IDENTIFIER=%s" % _safe_repr(row_id))
            if hasattr(row, "name"):
                _emit("ROW_NAME_ATTR=%r" % (getattr(row, "name"),))
            else:
                _emit("ROW_NAME_ATTR=<no .name attr>")
            gn = getattr(row, "get_name", None)
            if callable(gn):
                try:
                    _emit("ROW_GET_NAME=%r" % (gn(),))
                except Exception:
                    _emit("ROW_GET_NAME=<error-reading>")
            else:
                _emit("ROW_GET_NAME=<no callable .get_name>")
        _emit("P29E_PICKER_ROW_END")
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


def _find_class():
    import sys as _s
    # 1) preferred ordered host modules
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
    # 2) whole sys.modules discovery by class+method name (P29-C-proven fallback)
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
    if getattr(orig, "_ww_p29e_wrapped", False):
        _STATE["patched_methods"].setdefault(_METHOD_NAME, orig)
        _STATE["wrapped"] = True
        return True, where
    wrapped = _hook_get_picker_row(orig)
    try:
        wrapped._ww_p29e_wrapped = True
    except Exception:
        pass
    try:
        setattr(cls, _METHOD_NAME, wrapped)
    except Exception:
        return False, where
    _STATE["patched_methods"][_METHOD_NAME] = orig
    _STATE["wrapped"] = True
    _emit("P29E_HOOK_CLS_FOUND_IN=%s" % (where and where or "sys.modules-scan"))
    _emit("P29E_HOOK_METHOD=%s" % _METHOD_NAME)
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


def _try_patch():
    ok, where = _patch_class()
    return ok, (where is not None)


def _trace_attempt(import_exc=None):
    idx = _STATE.get("retry_count", 0)
    _emit("P29E_RETRY_INDEX=%d" % idx)
    _emit("P29E_MODULE_PRESENT=%s" %
          ("YES" if _STATE.get("last_cls_present") else "NO"))
    _emit("P29E_IMPORT_EXCEPTION=%s" % (import_exc or "NONE"))
    cands = _runtime_candidates()
    _emit("P29E_RUNTIME_MODULE_CANDIDATES=%s" %
          (",".join(cands) if cands else "(none yet)"))


def _retry_once():
    if _STATE.get("wrapped"):
        return True
    if _STATE.get("retry_count", 0) >= _STATE.get("max_retries", 30):
        return False
    _STATE["retry_count"] = _STATE.get("retry_count", 0) + 1
    import_exc = "NONE"
    try:
        ok, cp = _try_patch()
        _STATE["last_cls_present"] = cp
    except Exception as e:
        ok = False
        import_exc = "%s: %s" % (type(e).__name__, e)
        _STATE["error"] = e
        _STATE["last_cls_present"] = False
    _trace_attempt(import_exc)
    if ok:
        _STATE["retry_cb_executed"] = True
        _emit("P29E_HOOK_TARGET=%s.%s" % (_CLS_NAME, _METHOD_NAME))
        _emit("P29E_HOOK_INSTALLED=YES")
        _STATE["scheduler_armed"] = False
        return True
    return False


def _arm_via_zone(alarm_fire):
    armed = False
    note = "no sims4 zone service reachable yet"
    try:
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
    _emit("P29E_SCHEDULER_ARMED=%s (%s)" % ("YES" if armed else "NO", note))
    return armed


def _reset_state_for_test():
    _restore_all()
    _STATE["wrapped"] = False
    _STATE["calls"] = 0
    _STATE["nevely_limit_reached"] = False
    _STATE["silent_non_nevely_calls"] = 0
    _STATE["error"] = None
    _STATE["retry_count"] = 0
    _STATE["retry_cb_executed"] = False
    _STATE["scheduler_armed"] = False


def _hook_factory():
    """Module-level hook factory asserted by the builder probe (importability)."""
    return _hook_get_picker_row


def _bootstrap_boot_marker():
    path = _log_path()
    _STATE["_log_path"] = path
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    _log_header()
    _emit("P29E_MODULE_IMPORTED=YES")
    _emit("BOOT_AT=%s" % ts)
    _emit("MODULE_NAME=ww_p29e_picker_row_probe")
    _emit("TARGET_CLASS=%s.%s" % (_CLS_NAME, _METHOD_NAME))


def main():
    # First line of main(): visible proof main() was entered, before the boot
    # marker plumbing sets _STATE["_log_path"].  Uses the raw deterministic file
    # so it is recorded even if a later boot line fails.
    _raw_log_append("P29E_MAIN_ENTER=YES")
    _bootstrap_boot_marker()
    ok = False
    for _ in range(3):
        if _retry_once():
            ok = True
            break
    if ok:
        _emit("FILTER_MODE=AUTHOR_ONLY")
        _emit("VERDICT=PROBE_ARMED (records every Nevely42 get_picker_row; "
              "all other authors silent)")
        return
    armed = _register_scheduler()
    if not armed:
        _emit("P29E_HOOK_INSTALLED=NO")
        _emit("P29E_RETRY_COUNT=%d" % _STATE.get("retry_count", 0))
        _emit("P29E_IMPORT_EXCEPTION=%s" % (str(_STATE.get("error") or "NONE"),))
        _emit("VERDICT=FAIL_DISCOVERY")
        return
    _emit("VERDICT=DISCOVERY_PENDING (in-world retry armed)")


# ---- module-enter provenance ------------------------------------------------
# Fires on ANY real execution of this module body, BEFORE _RUN is computed and
# before main() is (or is not) called.  A P29E_MODULE_ENTER line proves the body
# truly ran; MODULE_NAME records the live __name__; LOG_PATH names the exact
# deterministic file that subsequent lines land in.
_raw_log_append("P29E_MODULE_ENTER=YES")
_raw_log_append("MODULE_NAME=%s" % (__name__,))
_raw_log_append("LOG_PATH=%s" % (_resolve_log_path(),))


_RUN = (not os.environ.get("WW_P29_DISABLE_AUTORUN")) and \
    (not os.environ.get("WW_P29E_DISABLE_AUTORUN"))

# Env-state + decision, recorded before any gated execution so a skipped autorun is
# distinguishable from "module never ran" and from "main() raised".
_raw_log_append("WW_P29_DISABLE_AUTORUN=%r" %
                 (os.environ.get("WW_P29_DISABLE_AUTORUN"),))
_raw_log_append("WW_P29E_DISABLE_AUTORUN=%r" %
                 (os.environ.get("WW_P29E_DISABLE_AUTORUN"),))
_raw_log_append("P29E_RUN=%s" % (_RUN and "True" or "False",))
if not _RUN:
    _raw_log_append("P29E_AUTORUN_SKIPPED=YES")


if _RUN:
    try:
        main()
    except Exception as _exc_main:
        # NEVER propagate to the game (would risk a crash), but NEVER swallow
        # silently: append type + repr + full traceback to the same deterministic
        # log so a main() failure is visible and auditable from the file alone.
        try:
            _raw_log_append("P29E_MAIN_EXCEPTION=YES")
            _raw_log_append("P29E_MAIN_EXCEPT_TYPE=%s" %
                             (type(_exc_main).__name__,))
            _raw_log_append("P29E_MAIN_EXCEPT_REPR=%s" % (repr(_exc_main),))
            for _tb_line in _traceback.format_exc().splitlines():
                _raw_log_append("P29E_MAIN_EXCEPT_TB:" + _tb_line)
        except Exception:
            pass
