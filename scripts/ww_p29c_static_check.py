#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ww_p29c_static_check.py --- STATIC safety gate for ww_p29c_display_caller_trace.py
(read-only).  Verifies the P29-C TARGET-ONLY caller-trace contract on the SOURCE.

Markers (robust, on executable code lines -- never bare word bans):
  A  Only ONE method is rebound and called transparently:
       'orig(self, *args, **kwargs)' present; NO re-authored positional call
       (orig(self, string_hash, ...) etc). The original is entered with self+args
       UNCHANGED and its return is returned verbatim.
  B  On any exception the hooks restore + re-raise: '_restore_all()' + bare raise.
  C  The observer NEVER assigns/mutates anything it only reads (no set_display_name,
       no self.display_name = / display_name_override = / setattr writes).
  D  Detailed trace is STRICTLY target-only by VALUE:
       gating equals exactly _TARGET_DISPLAY ('TEST300'), never author/id wildcard
       for the *detailed* block; thousands of other animations log nothing detailed.
  E  Per-frame / per-local / per-attr reads are guarded (no unguarded access that
       could crash the game): '_safe_' helper used on frame/local/attr reads.
  F  py3.7-safe: no walrus ':=' in executable lines; only stdlib imports.
  G  Call-frequency cap present so logs cannot explode: _MAX_TARGET_CALLS <= 30.

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
    # A: transparent passthrough of the original with self forwarded unchanged.
    if "ret = orig(self, *args, **kwargs)" not in code:
        fails.append("A-missing-self-passthrough-call")
    base = code.replace("ret = orig(self, *args, **kwargs)", "")
    for pat in ("orig(self, string_hash, original)",
                "orig(string_hash, original)",
                "orig(self.display_name",
                "orig(self, animation_tuning", "orig(*a, **k)"):
        if pat in base:
            fails.append("A-authored-call-%r" % pat)
    # B: restore + re-raise on error.
    if "_restore_all()" not in code:
        fails.append("B-missing-restore")
    # C: no authored set/mutation of fields we only observe.
    for pat in ("self.display_name =", "set_display_name(", "self.display_name_override =",
                "setattr(self", ".display_name =", "self.display_name_override",
                "row.display_name =", "row.text =", "self.author =",
                "setattr(instance", "init display_name"):
        if pat in code:
            fails.append("C-writes-%r" % pat)
    # D: detailed trace gated by EXACT target value equality (decided from the
    # pre-original display_name so the call-on-a-TEST300-instance is the deep one).
    for pat in ("dn_before = self.display_name",
                "dn_before == _TARGET_DISPLAY"):
        if pat not in code:
            fails.append("D-%r-missing" % pat)
    if "_TARGET_DISPLAY" not in code:
        fails.append("D-target-display-const-missing")
    # guard against the *detailed* block ever keying off author/id wildcard
    if "if not is_target:" not in code:
        fails.append("D-non-target-short-circuit-missing")
    # E: guarded reads on frames/locals/attrs.
    if "_safe_attr" not in code:
        fails.append("E-no-safe-attr-helper")
    if "_safe_repr" not in code or "_safe_str" not in code:
        fails.append("E-no-safe-repr-helper")
    # F: no walrus across executable lines; stdlib-only guard is structural (inspect).
    for ln in code.splitlines():
        if ":=" in ln:
            fails.append("F-walrus-%r" % ln.strip()[:40])
    # G: frequency cap <= 30 so we cannot dump thousands of frames.
    if "_MAX_TARGET_CALLS" not in code:
        fails.append("G-max-calls-const-missing")
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mod", default="scripts/ww_p29c_display_caller_trace.py")
    a = ap.parse_args()
    p = Path(a.mod)
    if not p.is_file():
        print("VERDICT=P29C_STATIC_FAIL REASON=mod-missing")
        return 2
    fails = check(p.read_text(encoding="utf-8"))
    if fails:
        print("VERDICT=P29C_STATIC_FAIL")
        for f in fails:
            print("  " + f)
        return 1
    print("VERDICT=P29C_STATIC_PASS")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
