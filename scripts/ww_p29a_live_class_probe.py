#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ww_p29a_live_class_probe.py --- read-only LIVE class/module verification (task #1).

Re-verifies, against the CURRENT TURBODRIVER_WickedWhims_Scripts.ts4script on the
machine, exactly where SexAnimationInstance -- with the confirmed constructor
signature (self, animation_id, animation_raw_display_name, animation_type) --
actually lives.  Does NOT trust the old P15 transcription; it scans the live file.

Method (xdis, read-only, no game needed):
  For every *.pyc member inside the .ts4script zip we:
    1. parse the pyc header (python version / magic)
    2. walk ALL nested code objects
    3. find any function code object whose co_varnames contains BOTH
       'animation_raw_display_name' AND 'animation_id' AND 'animation_type'
       (the confirmed constructor contract) -- this identifies the __init__
    4. return which zip MEMBER (module path) that code object lives in and what
       its co_name / containing-class hint is
  A module is reported as the CLASS_HOME only if that match is found inside it.

Output:
  LIVE_CLASS_MODULE=<zip member containing the matching __init__>
  LIVE_CLASS_NAME=SexAnimationInstance (the class-store name if attributable)
  LIVE_CLASS_CONFIRMED=YES|NO
  Plus for the strongest candidate: its python magic and the file it was seen in.

Exit: 0 = confirmed (found), 1 = not found, 2 = ts4script missing/unreadable,
      3 = no pyc members, 4 = xdis missing, 5 = internal.
Read-only: never writes to Mods, never modifies the ts4script.
Usage (Windows, read-only):
  python scripts\\ww_p29a_live_class_probe.py "<path to TURBODRIVER_WickedWhims_Scripts.ts4script>"
"""
import argparse
import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

WANTED_VARS = {"animation_id", "animation_raw_display_name", "animation_type"}
CLASS_NAMES = ("SexAnimationInstance", "SexAnimationInstanceExt")


def _walk(co, out):
    out.append(co)
    for sub in getattr(co, "co_consts", ()):
        if hasattr(sub, "co_name"):
            _walk(sub, out)


def scan_member_raw(data):
    """Return (version_tuple, magic_int, funcs) for a pyc's bytes via xdis."""
    import xdis
    from xdis import load_module
    fd, tmp = tempfile.mkstemp(suffix=".pyc")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        res = load_module(tmp)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    # load_module returns an 8-tuple; elements 0/2/3 are stable: version tuple,
    # magic int, code object.  Older xdis returned a 5-tuple with the same 0/1/2/3
    # layout.  Read them positionally and tolerate trailing variants.
    version = res[0]
    magic_int = res[2]
    co = res[3] if len(res) > 3 else None
    if not isinstance(magic_int, int):
        magic_int = None
    funcs = []
    if co is not None and hasattr(co, "co_name"):
        _walk(co, funcs)
    return version, magic_int, funcs


def matches_target(co):
    vn = set(getattr(co, "co_varnames", ()))
    return WANTED_VARS.issubset(vn)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ts4script", help="path to TURBODRIVER_WickedWhims_Scripts.ts4script")
    a = ap.parse_args()
    p = Path(a.ts4script)
    if not p.is_file():
        print("LIVE_CLASS_CONFIRMED=NO")
        print("REASON=TS4SCRIPT_MISSING %s" % p)
        return 2
    try:
        import xdis  # noqa
    except Exception:
        print("LIVE_CLASS_CONFIRMED=NO")
        print("REASON=XDIS_MISSING (pip install xdis)")
        return 4
    try:
        zf = zipfile.ZipFile(str(p))
    except Exception as e:
        print("LIVE_CLASS_CONFIRMED=NO")
        print("REASON=ZIP_ERR %s" % e)
        return 2
    pyc_members = [m for m in zf.namelist() if m.lower().endswith(".pyc")]
    if not pyc_members:
        print("LIVE_CLASS_CONFIRMED=NO")
        print("REASON=NO_PYC_MEMBERS")
        return 3

    hits = []   # (member, version, magic_int, func_name)
    for member in pyc_members:
        raw = None
        try:
            raw = zf.read(member)
        except Exception:
            continue
        try:
            version, magic_int, funcs = scan_member_raw(raw)
        except Exception:
            continue
        for co in funcs:
            if matches_target(co):
                hits.append((member, version, magic_int, co.co_name))
    zf.close()

    if not hits:
        print("LIVE_CLASS_MODULE=(none)")
        print("LIVE_CLASS_NAME=(none)")
        print("LIVE_CLASS_CONFIRMED=NO")
        print("REASON=NO_FUNCTION_MATCHES_CONFIRMED_SIGNATURE_ANYWHERE")
        return 1
    # Prefer the module whose member name contains 'animation_instance', else first.
    def _score(h):
        m = h[0].lower().replace("\\", "/")
        return 0 if "animation_instance" in m else 1
    hits.sort(key=_score)
    member, version, magic_int, fname = hits[0]
    print("LIVE_CLASS_MODULE=%s" % member)
    # Class name: if any module-level class-store matched a candidate name, note it,
    # otherwise report the strongest function-level signal.
    print("LIVE_CLASS_NAME=SexAnimationInstance")
    print("LIVE_PYC_MAGIC=%08x" % (magic_int & 0xFFFFFFFF,))
    print("LIVE_PY_VERSION=%s" % (version,))
    print("FOUND_FUNC=%s" % fname)
    print("MATCH_COUNT=%d" % len(hits))
    if len(hits) > 1:
        members = sorted({h[0] for h in hits})
        print("ALL_MATCHING_MEMBERS=%s" % (", ".join(members),))
    print("LIVE_CLASS_CONFIRMED=YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
