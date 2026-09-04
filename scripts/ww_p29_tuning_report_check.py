#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ww_p29_tuning_report_check.py --- P29-TUNING: derive the runtime P29_RESULT
from the ACTUAL captured trace log (run after the game session on the real box).

Why a post-hoc derivation: inside the game there is no reliable "session over"
hook, so the mod can only emit per-target verdicts when a target-keeping call is
actually observed.  The all-or-nothing D verdict (target NEVER observed) can only
be stated truthfully AFTER the session, from the real log -- never guessed mid-run.

Input: the path to ww_p29_tuning_trace.log.
Rules (HOOK_ERROR always wins -- an error invalidates the whole session):
  - If the log shows HOOK_ERROR=...      -> P29_RESULT=INVALID_HOOK_ERROR
    (regardless of any later target frame; the loader raised mid-call through the
     wrapper, so the session is not clean evidence)
  - If the log shows HOOK_INSTALLED=NO    -> P29_RESULT=HOOK_NOT_INSTALLED
  - If any captured target frame sets P29_RESULT=<A|B|C|OTHER> -> report that/those
    (the primary one(s); multiple target frames may appear for distinct animations)
  - HOOK_INSTALLED=YES, no HOOK_ERROR, but ZERO target frames
                                        -> P29_RESULT=TARGET_TUNING_NOT_OBSERVED
Exit 0 always (informational); output ASCII lines for the ps1 to echo.
"""
import argparse
import os

_A = "RAW_CHANGED_DISPLAY_DERIVED_OLD"
_B = "OVERRIDE_NOT_PRESENT_IN_RUNTIME_TUNING"
_C = "TUNING_AND_INSTANCE_CORRECT"
_TARGET_RES = {_A, _B, _C, "MATCH_TARGET_OTHER_PATTERN"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logpath")
    a = ap.parse_args()
    if not os.path.isfile(a.logpath):
        print("LOG=NOT_FOUND")
        print("P29_RESULT=NO_LOG")
        return 0
    installed = False
    hook_error = False
    frames = []
    seen_frames = 0
    with open(a.logpath, encoding="utf-8", errors="replace") as f:
        for ln in f:
            s = ln.rstrip("\n")
            if s.startswith("HOOK_INSTALLED=YES"):
                installed = True
            elif s.startswith("HOOK_ERROR="):
                hook_error = True
            elif s.startswith("MATCH=TARGET"):
                seen_frames += 1
            elif s.startswith("P29_RESULT="):
                v = s.split("=", 1)[1].strip()
                if v in _TARGET_RES:
                    frames.append(v)
    # ERROR always wins: a hook_error invalidates the whole session regardless of
    # any later target frames (the loader raised mid-call through our wrapper).
    if hook_error:
        print("HOOK_ERROR=PRESENT")
        print("P29_RESULT=INVALID_HOOK_ERROR")
        return 0
    if not installed:
        print("HOOK_INSTALLED=NO")
        print("P29_RESULT=HOOK_NOT_INSTALLED")
        return 0
    if frames:
        import collections
        c = collections.Counter(frames)
        for v, n in c.most_common():
            print("P29_RESULT=%s x%d" % (v, n))
    elif seen_frames:
        print("MATCH=TARGET_FRAMES=%d" % seen_frames)
        print("P29_RESULT=HOOK_INSTALLED_NO_TARGET_VERDICT")
    else:
        print("MATCH=TARGET_FRAMES=0")
        print("P29_RESULT=TARGET_TUNING_NOT_OBSERVED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
