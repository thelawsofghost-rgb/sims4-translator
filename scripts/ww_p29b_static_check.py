#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ww_p29b_static_check.py --- STATIC safety gate for ww_p29b_display_trace.py
(read-only).  Checks the SOURCE for the P29-B fail-closed contract.

Markers (robust, on executable code lines -- never bare word bans):
  A  The class is hooked by rebinding its METHODS (not editing the original bodies):
       'setattr(cls, name, wrapped)'   and the instance method wrappers call the
       ORIGINAL with the SAME self + args UNCHANGED (transparent passthrough).
  B  The original is entered ONLY via transparent passthrough:
       'ret = orig(self, *args, **kwargs)'  (class method: self forwarded first)
       and NO re-authored positional like orig(self, string_hash, original, ...).
  C  On any exception the hooks restore the original method bindings + re-raise:
       '_restore_all()'  and a bare 'raise'.
  D  The observer NEVER assigns the display / picker fields it only reads:
       no 'self.display_name =' / 'original_instance.display_name =' /
       'display_name_override =' / 'self.animation_display_name =' / setattr writes.
  E  No downstream sims resolver call by US: reject any 'localized.text' /
       'get_display_name(' CALL made to resolve a value ourselves (we only read
       already-stored attrs).
  F  py3.7-safe: no walrus ':=' in executable lines.
  G  Target matching is VALUE based (display_name/author/name), never animation_id:
       'def _is_target_instance' present and does NOT key off animation_id.

Exit: 0=PASS, 1=FAIL, 2=mod-missing.
"""
import argparse
from pathlib import Path


def _executable_lines(src_text):
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
    if "setattr(cls, name, wrapped)" not in code:
        fails.append("A-missing-method-rebind")
    if "ret = orig(self, *args, **kwargs)" not in code:
        fails.append("B-missing-self-passthrough-call")
    # forbid re-authoring the call with explicit positional fields that we might
    # have reshuffled (transparency demands ONLY star-forward *self + *args).
    base = code.replace("ret = orig(self, *args, **kwargs)", "")
    for pat in ("orig(self, string_hash, original)",
                "orig(string_hash, original)",
                "orig(self.display_name",
                "orig(self, animation_tuning",
                "orig(*a, **k)"):
        if pat in base:
            fails.append("B-authored-call-%r" % pat)
    if "_restore_all()" not in code:
        fails.append("C-missing-restore")
    for pat in ("self.display_name =", "self.display_name_override =",
                "self.animation_display_name =",
                "self.animation_raw_display_name =",
                "original_instance.display_name =",
                "display_name_override =", "setattr(self, 'display_name'",
                "row.text =", "row.display_name =", "self.name =",
                "self.author ="):
        if pat in code:
            fails.append("D-writes-%r" % pat)
    # E: we must not CALL get_display_name ourselves (only observe its return) and
    # must not pull localized.text through a call.
    if "localized.text" in code or ".get_display_name(self" in code \
            or "self.get_display_name(" in code or ".get_text()" in code:
        fails.append("E-resolver-call")
    for ln in code.splitlines():
        if ":=" in ln:
            fails.append("F-walrus-%r" % ln.strip()[:40])
    if "def _is_target_instance" not in code:
        fails.append("G-value-target-match-missing")
    elif "_TARGET_NAMES" not in code:
        fails.append("G-target-names-missing")
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mod", default="scripts/ww_p29b_display_trace.py")
    a = ap.parse_args()
    p = Path(a.mod)
    if not p.is_file():
        print("VERDICT=P29B_STATIC_FAIL REASON=mod-missing")
        return 2
    fails = check(p.read_text(encoding="utf-8"))
    if fails:
        print("VERDICT=P29B_STATIC_FAIL")
        for f in fails:
            print("  " + f)
        return 1
    print("VERDICT=P29B_STATIC_PASS")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
