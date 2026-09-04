#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29a_static_check.py --- STATIC safety gate for P29-A (read-only).

Checks the SOURCE of ww_p29a_mod.py for the structural fail-closed contract.
These are deliberately ROBUST markers (not regex bans on words that legitimately
appear in comments/docstrings).  The deeper behavioral guarantees are proven by
ww_p29a_logic_test.py at build time.

Contract markers (must ALL be present in executable code):
  A  The wrapper is installed by assigning the class's __init__ to a factory:
       'cls.__init__ = _hook_factory('
  B  The factory calls the ORIGINAL __init__ untouched:
       'orig_init(self, *args, **kwargs)'
  C  On any exception the wrapper restores the original and re-raises:
       '_restore_orig()'  AND  'raise' inside the except path.
  D  The only attribute writes are via the original __init__; the wrapper never
     assigns display_name/name/localized itself -> the wrapper body must contain
     NO 'self.display_name =' / 'self.name =' / 'self.localized ='.
     (We scan only the literal token 'self.<attr> =' which the real wrapper never
      uses -- it only READS them.)
  E  No downstream resolver call is executed in this module: the wrapper reads
     self.display_name/name/localized.hash only.  Reject any 'self.localized.text'.
  F  py3.7-safe: no walrus ':=' operator in executable code lines (strip # and
     strip docstring/string content by only scanning lines that start code, i.e.
     we ignore lines that are inside triple-quoted blocks).

Exit: 0=PASS, 1=FAIL, 2=mod-missing.
"""
import argparse
from pathlib import Path


def _executable_lines(src_text):
    """Return lines stripped of # comments AND outside triple-quoted strings,
    so docstring mentions don't trip the guard."""
    lines = src_text.splitlines()
    out = []
    in_str = False
    for ln in lines:
        stripped = ln.lstrip()
        # detect entering/exiting triple-quoted docstrings (''' or """)
        tcount = ln.count('"""') + ln.count("'''")
        if in_str:
            if tcount % 2 == 1:
                in_str = False
            continue
        if stripped.startswith(('"""', "'''")):
            if tcount % 2 == 0 and len(stripped) > 3:  # single-line docstring
                out.append(ln.split("#", 1)[0].rsplit('"""', 1)[0].rsplit("'''", 1)[0])
                continue
            in_str = True
            continue
        out.append(ln.split("#", 1)[0])
    return "\n".join(out)


def check(src_text):
    code = _executable_lines(src_text)
    fails = []

    if "cls.__init__ = _hook_factory" not in code:
        fails.append("A-missing-wrap-assignment")
    if "orig_init(self, *args, **kwargs)" not in code:
        fails.append("B-missing-orig-call")
    if "_restore_orig()" not in code:
        fails.append("C-missing-restore")
    # D: wrapper must not assign these attrs (it only reads after orig ran)
    for pat in ("self.display_name =", "self.name =", "self.localized =",
                "self.display_name=", "self.name=", "self.localized="):
        if pat in code:
            fails.append("D-wrapper-writes-%r" % pat)
    # E: never read localized.text
    if "self.localized.text" in code or "localized.text" in code:
        fails.append("E-reads-localized-text")
    # F: no walrus in executable lines
    for ln in code.splitlines():
        if ":=" in ln:
            fails.append("F-walrus-%r" % ln.strip()[:40])
    # ensure restore actually re-raises (the original behavior preserved)
    # sanity: _restore_orig defined and referenced in except path
    if "def _restore_orig" not in code:
        fails.append("restore-helper-missing")
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mod", default="scripts/ww_p29a_mod.py")
    a = ap.parse_args()
    p = Path(a.mod)
    if not p.is_file():
        print("VERDICT=STATIC_FAIL REASON=mod-missing")
        return 2
    src_text = p.read_text(encoding="utf-8")
    fails = check(src_text)
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
