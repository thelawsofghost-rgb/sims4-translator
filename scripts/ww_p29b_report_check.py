#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ww_p29b_report_check.py --- P29-B: derive the run-level P29B_RESULT from the
ACTUAL captured trace log (run after the game session on the real box).

Post-session derivation only (no mid-run guessing).  Scans every frame the mod
wrote and classifies.  Precedence (strict):
  0. no log, or log but NO boot marker (P29B_MODULE_IMPORTED=YES)
        -> P29B_RESULT=MODULE_NOT_IMPORTED   (module body never ran in game)
  1. module imported but no HOOK_INSTALLED=YES
        -> P29B_RESULT=HOOK_NOT_INSTALLED
  2. any HOOK_ERROR=...                      -> P29B_RESULT=INVALID_HOOK_ERROR
  3. a captured get_display_name root cause, strongest first:  (install verified)
        UI_USING_ORIGINAL_INSTANCE  (self.original_instance used -> old English)
        DISPLAY_NAME_OVERRIDE_WINS  (self.display_name_override == old English)
        GET_DISPLAY_NAME_IS_SWITCH  (base TEST300 but get_display_name returns old)
     (the mod also emits these inline as P29B_RESULT=... inside the GDN frame)
  4. picker-stage branch (no in-gdn old-English switch proved):
        if a get_picker_row frame shows PICKER_ROW_TEXT == 'Caught Cheating 2'
            while a gdn returning TEST300 exists -> PICKER_ROW_USES_OTHER_SOURCE
        else (all observed gdn return TEST300, rows have no old English)
            -> PICKER_POSTPROCESSING_OR_OTHER_UI_SOURCE
  5. installed + zero target frames          -> TARGET_TUNING_NOT_OBSERVED
Exit 0 always (informational); ASCII output for the ps1 to echo.
"""
import argparse
import collections
import os

_OLD_RAW = "Caught Cheating 2"
_NEW_RAW = "TEST300"
# token prefixes (ASCII emitted by the mod)
_OLD_TOK = "PICKER_ROW_TEXT=%r" % (_OLD_RAW,)
_NEW_BASE_TOK = "BASE_DISPLAY_NAME=%r" % (_NEW_RAW,)
_NEW_RET_TOK = "GET_DISPLAY_NAME_RETURN=%r" % (_NEW_RAW,)
_ORDERED_GDN = ("UI_USING_ORIGINAL_INSTANCE", "DISPLAY_NAME_OVERRIDE_WINS",
                "GET_DISPLAY_NAME_IS_SWITCH")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logpath")
    a = ap.parse_args()
    if not os.path.isfile(a.logpath):
        print("LOG_ENTRY=NOT_FOUND")
        print("MODULE_IMPORTED=NO")
        print("P29B_RESULT=MODULE_NOT_IMPORTED")
        return 0
    installed = False
    hook_error = False
    imported = False
    gdn_verdicts = collections.Counter()
    gdn_returned_test299 = False
    gdn_base_test299 = False
    picker_frames = 0
    picker_row_old = False
    any_target = False
    with open(a.logpath, encoding="utf-8", errors="replace") as f:
        for ln in f:
            s = ln.rstrip("\n")
            if s.startswith("P29B_MODULE_IMPORTED=YES"):
                imported = True
            elif s.startswith("HOOK_INSTALLED=YES"):
                installed = True
            elif s.startswith("HOOK_ERROR="):
                hook_error = True
            elif s.startswith("HOOK_INSTALLED=NO"):
                installed = False
            elif s.startswith("P29B_RESULT="):
                v = s.split("=", 1)[1].strip()
                if v in _ORDERED_GDN:
                    gdn_verdicts[v] += 1
            elif s.startswith(_NEW_BASE_TOK):
                gdn_base_test299 = True
                any_target = True
            elif s.startswith(_NEW_RET_TOK):
                gdn_returned_test299 = True
                any_target = True
            elif s.startswith("PICKER_#"):
                picker_frames += 1
                any_target = True
            elif s.startswith(_OLD_TOK):
                picker_row_old = True
                any_target = True
    # 0. module-import proof is mandatory gate
    if not imported:
        print("MODULE_IMPORTED=NO")
        print("LOG_PRESENT=YES")
        print("P29B_RESULT=MODULE_NOT_IMPORTED")
        return 0
    # 1. imported but hook not installed
    if not installed:
        print("MODULE_IMPORTED=YES")
        print("HOOK_INSTALLED=NO")
        print("P29B_RESULT=HOOK_NOT_INSTALLED")
        return 0
    # 2. error precedence (module imported + hook installed)
    if hook_error:
        print("HOOK_ERROR=PRESENT")
        print("P29B_RESULT=INVALID_HOOK_ERROR")
        return 0
    # 3. in-gdn named root cause (strongest present)
    for v in _ORDERED_GDN:
        if gdn_verdicts.get(v, 0):
            print("P29B_GDN_VERDICT=%s x%d" % (v, gdn_verdicts[v]))
            # Use canonical name for UI_USING_ORIGINAL_INSTANCE etc straight through.
            print("P29B_RESULT=%s" % v)
            return 0
    # 4/5. no in-gdn switch observed
    if any_target and picker_frames:
        if picker_row_old and gdn_base_test299:
            # branch C: gdn returned TEST300 but picker row shows old English
            print("P29B_RESULT=PICKER_ROW_USES_OTHER_SOURCE")
            return 0
        print("P29B_RESULT=PICKER_POSTPROCESSING_OR_OTHER_UI_SOURCE")
        return 0
    if any_target:
        print("MATCH=TARGET_FRAMES=present_no_picker")
        print("P29B_RESULT=GDN_ONLY_NO_PICKER")
        return 0
    print("MATCH=TARGET_FRAMES=0")
    print("P29B_RESULT=TARGET_TUNING_NOT_OBSERVED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
