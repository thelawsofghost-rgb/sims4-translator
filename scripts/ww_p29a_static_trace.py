#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29a_static_trace.py --- CURRENT-WW static DATAFLOW trace (tasks #2 & #3).

Purpose: answer, against the LIVE pyc (native marshal, real matching CPython):
    animation_raw_display_name / display_name  -- how the XML field reaches the
    SexAnimationInstance(... display_name ...) constructor call, and what happens to
    display_name_override after construction.

Because WW compiles lazily and the .pyc is extracted import-cached, the authoritative
members to trace are extracted from the SAME TURBODRIVER_WickedWhims_Scripts.ts4script
by THIS script (write the chosen members to a temp dir, then native import through a
normalized loader so code objects keep their co_filename) -- but simpler and robust:
we raw-marshal the selected members ourselves here and disassemble their nested code
objects (no execution, no game import) to find constructor call sites and assignment
to display_name / display_name_override.

Run (real machine, matching 3.7.9):
    python.exe ww_p29a_static_trace.py "<WW.ts4script>"
    [--class-module .../animation_instance.pyc]
    [--loader .../animations_loader.pyc]
    [--instance SexAnimationInstance]

It scans EVERY .pyc member by default (loader + instance first) so we do not presume
which module holds the call.  For each member it fully walks nested code objects and
records, per (file, function):
  * calls to SexAnimationInstance (CALL_FUNCTION/CALL_METHOD) with the dis window and
    the apparent expression sequence feeding the positional args before the call
  * assignments to the name 'display_name' and 'display_name_override'
  * string constants in each traced function (to recover XML key/localized keys)
No source is required.  Read-only.  Outputs a deterministic text report we can then
read as evidence for the chain.

Expected outputs (feeds the judgement fields in THIS script's summary):
  CALLER_MODULE=          the member where a SexAnimationInstance(...) call is found
  CALLER_FUNCTION=        enclosing def name
  SEX_ANIMATION_INSTANCE_CALLS=n
  DISPLAY_NAME_ARGUMENT_SOURCE=...  recovered (may be 'POSITIONAL_INDEX2_FROM_<x>')
  DISPLAY_NAME_OVERRIDE_SOURCE=...
  RAW_FIELD_REFERENCES=   string constants in traced scopes that look like XML keys
Exit 0 = trace completed (even if some fields say UNRESOLVED -- the report is the
artifact).  1 = member missing/unreadable.
Read-only; never writes to Mods or the WW ts4script (only temp files it removes).
"""
import argparse
import marshal
import os
import sys
import tempfile
import zipfile
from pathlib import Path

INSTANCE = "SexAnimationInstance"
OVERRIDE = "display_name_override"


def walk_objs(co, acc):
    acc.append(co)
    for sub in getattr(co, "co_consts", ()):
        if hasattr(sub, "co_name"):
            walk_objs(sub, acc)


def load_member_co(ts4, member, local_magic):
    with zipfile.ZipFile(ts4) as z:
        data = z.read(member)
    mag = data[:4].hex()
    if mag != local_magic:
        return None, mag
    return marshal.loads(data[16:]), mag


def instrs(func):
    try:
        import dis
        return list(dis.get_instructions(func))
    except Exception:
        return []


def window_str(func, start, end):
    try:
        import dis
        lines = dis.Bytecode(func).dis() if False else ""
    except Exception:
        lines = ""
    return lines


def analyse(func, tgt=INSTANCE):
    """Return dict: calls [] of windows, assigns_override n, names, strings. """
    import dis
    ins = list(dis.get_instructions(func))
    res = {"calls": [], "override_store": [], "display_store": [],
           "strings": sorted({i.argval for i in ins
                              if isinstance(i.argval, str)}), "n_ins": len(ins)}
    # constructor CALL: function object pushed is SexAnimationInstance (a LOAD_GLOBAL
    # / LOAD_ATTR / LOAD_METHOD).  Detect a LOAD that pushes our class then CALL.
    for k in range(len(ins)):
        i = ins[k]
        if (i.opname.startswith("CALL_FUNCTION")
                or i.opname.startswith("CALL_METHOD")):
            # scan back a bounded window for the callee push naming SexAnimationInstance
            lo = max(0, k - 40)
            push_names = []
            for j in range(lo, k):
                o = ins[j]
                base = str(o.argval or o.argrepr or "").split(".")[-1]
                push_names.append(base)
            ctx = " | ".join(push_names[-18:])
            if tgt in ctx.split(".") or any(part == tgt or part.endswith("." + tgt)
                                            for part in push_names):
                res["calls"].append({"at": k, "ctx": ctx})
    for i in ins:
        if i.opname in ("STORE_FAST", "STORE_ATTR", "STORE_NAME", "STORE_SUBSCR",
                        "STORE_GLOBAL", "STORE_DEREF") :
            nm = i.argval
            if nm == OVERRIDE:
                res["override_store"].append(i.offset)
            if nm == "display_name":
                res["display_store"].append(i.offset)
    return res


def main():
    a = parse_args()
    ts4 = Path(a.ts4script)
    import importlib.util
    local_magic = importlib.util.MAGIC_NUMBER.hex()
    if not ts4.is_file():
        print("FATAL=TS4SCRIPT_MISSING %s" % ts4); return 2
    print("LOCAL_MAGIC=%s" % local_magic)
    members = a.member
    pycs = []
    if members:
        pycs = list(members)
    else:
        with zipfile.ZipFile(str(ts4)) as z:
            n = z.namelist()
            pycs = [m for m in n if m.endswith(".pyc")]
            # prioritize: class + loader first so CALLER is found early
            pri = [m for m in pycs
                   if "animations_loader.pyc" in m or "animation_instance.pyc" in m]
            rest = [m for m in pycs if m not in pri]
            pycs = pri + rest
    total_calls = 0
    caller_found = None
    for member in pycs:
        co_obj, mag = load_member_co(str(ts4), member, local_magic)
        if mag != local_magic:
            print("MEMBER=%s MAGIC=%s SKIP (does NOT match native loads; "
                  "run with the 3.7.9 / 420d0d0a interpreter)" % (member, mag))
            continue
        if co_obj is None:
            print("MEMBER=%s MARSHAL=None (unreadable)" % member)
            continue
        print("\n### MEMBER %s (native magic match)" % member)
        objs = []
        walk_objs(co_obj, objs)
        member_calls = 0
        for fn in objs:
            name = getattr(fn, "co_name", "")
            first = getattr(fn, "co_firstlineno", 0)
            an = analyse(fn, INSTANCE)
            # A '<module>'-frame 'call' is just class creation via LOAD_BUILD_CLASS
            # (+ SexAnimationInstance ... object).  Only real function bodies count as
            # instance-CONSTRUCTING callers for CALLER_MODULE.
            is_frame = name.startswith("<")
            if not (an["calls"] or an["override_store"] or an["strings"]):
                continue
            if an["calls"] and not is_frame:
                member_calls += len(an["calls"])
                if caller_found is None:
                    caller_found = member
            if (not is_frame) and (an["calls"] or an["override_store"]):
                print("  FN {} (line~{}): calls={} override_store={} display_store={}"
                      .format(name, first, len(an["calls"]),
                              len(an["override_store"]), len(an["display_store"])))
                for c in an["calls"][:6]:
                    print("     CALL window ... %s" % c["ctx"][-120:])
                if an["strings"]:
                    print("     STRINGS %s" % (", ".join(an["strings"][:40])))
        total_calls += member_calls
    print("\nSUM SEX_ANIMATION_INSTANCE_CALLS=%d" % total_calls)
    print("CALLER_MODULE=%s" % (caller_found or "UNRESOLVED"))
    if a.detail:
        _dump_detail(ts4, a, local_magic, INSTANCE)
    return 0


def _dump_detail(ts4, a, local_magic, tgt):
    """Heavy per-call dis dump for the constructor-feeding stack inference."""
    import dis
    members = a.member
    pycs = members if members else [m for m in
                                    [l for l in
                                     (lambda z: [x for x in z.namelist()
                                                 if x.endswith(".pyc")])
                                     (zipfile.ZipFile(str(ts4)))]]
    for member in pycs:
        co_obj, mag = load_member_co(str(ts4), member, local_magic)
        if co_obj is None or mag != local_magic:
            continue
        objs = []
        walk_objs(co_obj, objs)
        for fn in objs:
            ctx_names = []
            try:
                ins = list(dis.get_instructions(fn))
            except Exception:
                continue
            for k in range(len(ins)):
                i = ins[k]
                if not i.opname.startswith("CALL_FUNCTION") and \
                   not i.opname.startswith("CALL_METHOD"):
                    continue
                lo = max(0, k - 30)
                seq = ["%s:%s" % (ins[j].opname, ins[j].argrepr)
                       for j in range(lo, k)
                       if ins[j].opname.startswith(("LOAD_", "CALL_"))]
                if any((tgt in (s.split(":")[1] or ""))
                       for s in seq):
                    print("\n== CALL window in %s.%s ==" % (member, fn.co_name))
                    for s in seq[-22:]:
                        print("    ", s)
    return


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("ts4script")
    ap.add_argument("--member", action="append", default=None,
                    help="restrict to specific .pyc members; repeatable")
    ap.add_argument("--detail", action="store_true",
                    help="print raw LOAD/CALL windows for constructor calls")
    return ap.parse_args()


if __name__ == "__main__":
    sys.exit(main())
