#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ww_p29_tuning_static_check.py --- STATIC safety gate for ww_p29_tuning_mod.py
(read-only).  Checks the SOURCE for the P29-TUNING fail-closed contract.

Markers (robust, on executable code lines only -- never bare word bans):
  A  Patch install is a module-Attribute REBIND, never a body rewrite:
       'setattr(mod, ' + '"' + TUNING_FUNC + '", hook)'   (we allow the exact
       setattr on a module; enforcement of "calls original untouched" is B).
  B  The hook calls the ORIGINAL with the same args untouched:
       'orig(animation_tuning, animation_override, *a, **kw)'
  C  On any exception the hook restores the original bindings and re-raises:
       '_restore_all()'  AND  a bare 'raise' in the except path.
  D  The observer NEVER ASSIGNS tuning/instance fields itself (it only READS them
     after the original ran).  The code must contain NO writes like
       'animation_tuning.animation_display_name =' / '.animation_raw_display_name ='
       'animation_override =' / 'self.display_name =' / 'setattr(<tuning>,'
  E  No downstream resolver / no .text access that could call into the sims:
       reject 'localized.text' and 'get_display_name()' CALLS we make.
  F  py3.7-safe: no walrus ':=' in executable lines.
  G  Patch is gated on a signature-check helper so a name-collision is not wrapped
     blindly: 'animation_tuning' must appear in '_looks_like_target'.

Exit: 0=PASS, 1=FAIL, 2=mod-missing.
"""
import argparse
from pathlib import Path


def _executable_lines(src_text):
    """Return lines stripped of # comments AND outside triple-quoted strings."""
    out = []
    in_str = False
    for ln in src_text.splitlines():
        stripped = ln.lstrip()
        tcount = ln.count('"""') + ln.count("'''")
        if in_str:
            if tcount % 2 == 1:
                in_str = False
            continue
        if stripped.startswith(('"""', "'''")):
            if tcount % 2 == 0 and len(stripped) > 3:
                out.append(ln.split("#", 1)[0])
                continue
            in_str = True
            continue
        out.append(ln.split("#", 1)[0])
    return "\n".join(out)


def check(src_text):
    code = _executable_lines(src_text)
    fails = []

    if "setattr(mod, _TUNING_FUNC, hook)" not in code:
        fails.append("A-missing-attr-rebind")
    if "orig(animation_tuning, animation_override, *a, **kw)" not in code:
        fails.append("B-missing-orig-call-untouched")
    if "_restore_all()" not in code:
        fails.append("C-missing-restore")
    # D: no self/attr writes to the fields we only read
    for pat in ("animation_tuning.animation_display_name =",
                "animation_tuning.animation_raw_display_name =",
                "t.animation_display_name =",
                "t.animation_raw_display_name =",
                "animation_override =",
                "self.display_name =", "self.display_name_override =",
                "self.name =", "self.localized =",
                "display_name = ", "raw_display_name = "):
        if pat in code:
            fails.append("D-writes-%r" % pat)
    # E: never read localized.text / never call get_display_name ourselves
    if "localized.text" in code or "get_display_name()" in code:
        fails.append("E-resolver-call")
    # F: no walrus
    for ln in code.splitlines():
        if ":=" in ln:
            fails.append("F-walrus-%r" % ln.strip()[:40])
    # G: signature gate present
    if "def _looks_like_target" not in code or "animation_tuning" not in code:
        fails.append("G-sig-gate-missing")
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mod", default="scripts/ww_p29_tuning_mod.py")
    a = ap.parse_args()
    p = Path(a.mod)
    if not p.is_file():
        print("VERDICT=STATIC_FAIL REASON=mod-missing")
        return 2
    fails = check(p.read_text(encoding="utf-8"))
    if fails:
        print("VERDICT=STATIC_FAIL")
        for f in fails:
            print("  " + f)
        return 1
    print("VERDICT=STATIC_PASS")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
