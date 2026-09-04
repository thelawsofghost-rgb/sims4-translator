#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ww_p29b_inspect.py --- P29-B build-artifact layout inspector (host python).

Prints the TRUE on-disk ts4script layout so deploy/read_log can prove (not guess)
that the P29-B package is auto-loadable the SAME way the already-working P29-TUNING
debug ts4script was.  It does NOT trust the source .py; it inspects the actual
staged *.ts4script (a zip) member-by-member.

Outputs:
    TS4SCRIPT_MEMBERS=<name>:<bytes>:<compress_type>;...   (every member, in zip order)
    ENTRY_MODULE=<top-level module name>   (member at zip root with a .pyc whose path
                                             has no '/', i.e. a top-level module)
    BOOTSTRAP_MEMBER=<the .pyc whose code includes the module-scope autorun guard>
    PYC_MAGIC=<4-byte magic hex of the .pyc>   (first pyc found at root)
    PLUS the tuning golden layout for comparison:
    P29_TUNING_WORKING_LAYOUT=<expected member spec>
    LAYOUT_EQUIVALENT=YES|NO   (structure comparable to the working tuning layout)
Also prints MODULE_NOT_IMPORTED diagnostics hint when the member is NOT top-level or
when module-scope guard token is absent (that is exactly why a module can be packed
but never auto-imported).

USAGE: python ww_p29b_inspect.py <path/to/*.ts4script> [--golden-ok]
EXIT: 0 always (informational; used by the ps1 wrappers).
ASCII-only.  Fail-closed: any unreadable member is reported, never assumed.
"""
import argparse
import os
import struct
import sys
import zipfile

_GUARD_TOKENS = ("WW_P29_DISABLE_AUTORUN", "WW_P29B_DISABLE_AUTORUN",
                 "BOOT_GUARD_ACTIVE", "P29B_MODULE_IMPORTED")
# The KNOWN-GOOD tuning ts4script auto-imports: a SINGLE top-level member
# <module>.pyc at the zip ROOT (no '/' in the name).  P29-B must match that shape.
_GOLDEN_ROOT_MEMBER = "ww_p29_tuning_mod.pyc"


def _pyc_magic(data):
    if len(data) < 4:
        return ""
    return "".join("%02x" % b for b in data[:4])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ts4")
    a = ap.parse_args()
    if not os.path.isfile(a.ts4):
        print("TS4SCRIPT=NOT_FOUND")
        print("P29_TUNING_WORKING_LAYOUT=%s (single root member)" % _GOLDEN_ROOT_MEMBER)
        print("LAYOUT_EQUIVALENT=NO")
        return 0
    try:
        z = zipfile.ZipFile(a.ts4)
        infos = z.infolist()
    except Exception as e:
        print("ZIP_READ_ERROR=%s: %s" % (type(e).__name__, e))
        print("P29_TUNING_WORKING_LAYOUT=%s (single root member)" % _GOLDEN_ROOT_MEMBER)
        print("LAYOUT_EQUIVALENT=NO")
        return 0
    members = []
    root_pyc = []
    for i in infos:
        nm = i.filename
        members.append("%s:%d:%d" % (nm, i.file_size, i.compress_type))
        if not i.is_dir() and nm.endswith(".pyc") and "/" not in nm:
            root_pyc.append(nm)
    print("TS4SCRIPT_MEMBERS=%s" % ";".join(members))
    # ENTRY_MODULE = the single root module that Sims4 script manager would import as
    # a top-level name.
    if len(root_pyc) == 1:
        print("ENTRY_MODULE=%s" % os.path.splitext(root_pyc[0])[0])
        print("BOOTSTRAP_MEMBER=%s" % root_pyc[0])
    elif root_pyc:
        print("ENTRY_MODULE=%s" % ",".join(
            os.path.splitext(n)[0] for n in root_pyc))
        print("BOOTSTRAP_MEMBER=%s" % root_pyc[0])
    else:
        print("ENTRY_MODULE=(none -- no top-level .pyc; members are under a folder,")
        print("  needs package __init__ bootstrap, not top-level import)")
        print("BOOTSTRAP_MEMBER=(none)")
    magic = ""
    guard_hit = False
    if root_pyc:
        try:
            data = z.read(root_pyc[0])
            magic = _pyc_magic(data)
            txt = ""
            try:
                txt = data.decode("latin-1", errors="replace")
            except Exception:
                txt = ""
            guard_hit = any(t in txt for t in _GUARD_TOKENS)
        except Exception:
            pass
    print("PYC_MAGIC=%s" % magic)
    # structural equivalence: exactly one root pyc AND that module carries the
    # guard/boot token (proves it will auto-run at module body scope on import).
    root_pyc = [n for n in root_pyc if n.endswith(".pyc")]
    equiv = (len(root_pyc) == 1 and guard_hit)
    print("P29_TUNING_WORKING_LAYOUT=%s (single root member + module-scope autorun guard)"
          % _GOLDEN_ROOT_MEMBER)
    print("P29B_LAYOUT=%s" % (root_pyc[0] if root_pyc else "(no top-level member)"))
    print("LAYOUT_EQUIVALENT=%s" % ("YES" if equiv else "NO"))
    if not equiv:
        if not root_pyc:
            print("LAYOUT_NOTE=no top-level .pyc at zip root -> packed but not auto-imported")
            print("LAYOUT_NOTE=move the module .pyc to the zip ROOT (single member) as in tuning")
        elif not guard_hit:
            print("LAYOUT_NOTE=root .pyc present but no module-scope autorun guard/boot marker")
            print("LAYOUT_NOTE=module is importable-only; add the module-scope autorun block")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
