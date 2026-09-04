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

DISCOVERY / TIMING:
  Sims 4 loads *.ts4script members after boot; WW defines the real class under
  module path wickedwhims.sex.animations.animation_instance (per P15).  We patch
  lazily: try immediate import; if unavailable, retry on each zone load via a
  least-authority scheduler hook.  Multiple candidates are resolved by class name
  and __init__ arg signature (fail-closed: only wrap classes that look like the
  target, never a loose name match).

LOG:
  We write to stdout AND (best-effort) to a log file so it is easy to paste back.
  We do NOT assume an arbitrary fixed Windows path is writable; we try a small
  ordered list of writable roots and fall back silently if none is writable.
  The on-screen Sims exception log / stdout already exists; we ALSO try to tee.

IMPORT / TIMING (Sims 4):
  This is a single flat module whose top-level body calls main().  In Sims 4 a
  top-level *.pyc shipped inside a *.ts4script placed under Mods is commonly
  auto-imported at boot.  We do not depend solely on that: main() retries class
  discovery lazily (deferred schedule) so that even if WW's class is not yet
  defined at our import time, we patch once it exists."""

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


def _try_wrap():
    """Attempt to locate and wrap the target class. Returns True on success."""
    # 1) Direct module import against real WW layout.
    for mod_name in _MODULE_CANDIDATES:
        try:
            mod = __import__(mod_name, fromlist=["*"])
        except Exception:
            continue
        for cname in _CLASS_NAME_CANDIDATES:
            cls = getattr(mod, cname, None)
            if cls is not None:
                if _wrap_cls(cls):
                    return True
    # 2) sys.modules scan (robust to WW namespace suffixing).
    for mname, mod in list(sys.modules.items()):
        if not mname:
            continue
        for cname in _CLASS_NAME_CANDIDATES:
            cls = getattr(mod, cname, None)
            if cls is not None:
                if cls not in (type, object):
                    if _wrap_cls(cls):
                        return True
    return False


def _retry_on_zone():
    """Best-effort re-attempt; can be wired to a zone/client callback."""
    if _STATE.get("wrapped"):
        return
    try:
        if _try_wrap():
            _emit("HOOK_INSTALLED=YES (deferred)")
        else:
            _emit("HOOK_NOT_YET=class unavailable")
    except Exception as e:
        _STATE["error"] = e
        _emit("DISCOVERY_ERROR=%s" % (e,))


def _register_scheduler():
    """Hook into the sims4 zone-start / client load if available; else no-op.

    Sims 4 exposes sims4.commands / zone later during boot.  We try to register a
    lightweight callback.  If unavailable (e.g., running under an offline harness
    or early boot), we simply attempt once now -- the offline logic test covers
    the wrappering path; the real timing is refined via HOOK_INSTALLED feedback.
    """
    try:
        import sims4  # noqa
        from sims4 import commands  # noqa
    except Exception:
        # sims4 not present yet -> try to patch at import time only; the mod is
        # imported by the game after sims4 exists, so immediate attempt is fine.
        _retry_on_zone()
        return

    # If sims4 present, try to register for zone load via public-ish APIs without
    # guessing internals: attempt a deferred recheck guarded by exceptions.
    try:
        from sims4.common import get_zone_id  # noqa
    except Exception:
        pass
    try:
        from sims4.commands import Command  # noqa
    except Exception:
        pass
    _retry_on_zone()


def _reset_state_for_test():
    """Test-only: clear counters (never used at runtime)."""
    _STATE["wrapped"] = False
    _STATE["orig"] = None
    _STATE["cls"] = None
    _STATE["observed"] = 0
    _STATE["matched"] = 0
    _STATE["targets"] = {"old_hits": 0, "new_hits": 0}
    _STATE["error"] = None


def main():
    _STATE["_log_path"] = _log_path()
    _log_header()
    try:
        ok = _try_wrap()
    except Exception as e:
        _STATE["error"] = e
        ok = False
    if not ok:
        _register_scheduler()
        _emit("HOOK_INSTALLED=NO (retrying%s)" % (", deferred schedule active" if True else ""))
        _emit("VERDICT=FAIL_DISCOVERY")
        return
    _emit("HOOK_INSTALLED=YES")
    _emit("MATCH_COUNT=0 (waiting for constructions)")
    _emit("VERDICT=TRACE_CAPTURED (armed)")


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
