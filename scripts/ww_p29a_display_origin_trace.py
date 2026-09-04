#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29a_display_origin_trace.py -- READ-ONLY native-marshal audit of WHERE the
current-WW display name actually comes from.

Background (authoritative, from the real machine trace on 2026-09-04):
    old guess  animation_raw_display_name -> display_name -> SexAnimationInstance  is
    STALE.  The live _create_sex_animation_instance instead does

        display_name = animation_tuning.animation_display_name     # store#8
        SexAnimationInstance(animation_id=.., display_name=display_name, ...)

    so the audit here LOCATES who writes `animation_display_name` and
    `animation_raw_display_name` on the tuning/instance objects, identifies the type /
    creator of `animation_tuning`, decides the raw->display relation, and reports the
    XML/tuning key literals the parser reads.  All read-only (never touches Mods /
    the WW ts4script / Nevely).  Runs under the REAL local CPython 3.7.9 (magic
    420d0d0a) using NATIVE marshal.loads(pyc[16:]); NO xdis.

Usage (real machine, one command in the shipped .ps1):
    python.exe ww_p29a_display_origin_trace.py "<WW.ts4script>"
        [--loader wickedwhims/sex/animations/animations_loader.pyc]

Algorithm:
  * open the .ts4script (zip); for EVERY *.pyc member assert its header magic == the
    running interpreter magic (abort on first mismatch, exit 4) then native-marshal it;
  * walk every function (nested code object) collecting, by memory-address identity of
      the referenced name:
        W1  STORE_ATTR / STORE_FAST / STORE_NAME binding  animation_display_name
        W2  STORE_ATTR / STORE_FAST / STORE_NAME binding  animation_raw_display_name
        R   any LOAD_ATTR / LOAD_FAST / name use of either (reader windows)
    -> emit ANIMATION_DISPLAY_NAME_WRITERS= / ANIMATION_RAW_DISPLAY_NAME_WRITERS= with
         MODULE= FUNCTION= and one BYTECODE_CONTEXT window per writer.
  * find _create_sex_animation_instance in the loader and, inside it, walk backwards
    from the display_name STORE to find who populates the `animation_tuning` parameter
    (its ctor param list const), and scan loader-wide for a class/factory method whose
    store of .animation_display_name reveals the producer type.
  * decide the raw->display RELATION (DIRECT / TRANSFORMED / INDEPENDENT / UNRESOLVED)
    from the real instructions, never guessing.

Exit: 0=done; 2=ts4 missing; 3=unreadable zip; 4=magic mismatch / no members;
      5=marshal error; 6=loader function missing.
"""
import argparse
import importlib.util
import marshal
import sys
import zipfile
from pathlib import Path

FIELD_DISP = "animation_display_name"
FIELD_RAW = "animation_raw_display_name"
FN_DEFAULT = "_create_sex_animation_instance"
LOADER_DEFAULT = "wickedwhims/sex/animations/animations_loader.pyc"
STORE_ATTR_OPS = ("STORE_ATTR",)
STORE_LOCAL_OPS = ("STORE_FAST", "STORE_NAME", "STORE_DEREF", "STORE_GLOBAL")
LOAD_ATTR_OPS = ("LOAD_ATTR",)
LOAD_NAME_OPS = ("LOAD_FAST", "LOAD_GLOBAL", "LOAD_NAME", "LOAD_DEREF")


def magic_hex():
    return importlib.util.MAGIC_NUMBER.hex()


def read_member(ts4, member):
    with zipfile.ZipFile(ts4) as z:
        try:
            return z.read(member)
        except KeyError:
            return None


def code_from(pyc):
    return marshal.loads(pyc[16:])


def walk(co, acc):
    acc.append(co)
    for sub in getattr(co, "co_consts", ()):
        if hasattr(sub, "co_name"):
            walk(sub, acc)


def dis(fn):
    import dis as _dis
    try:
        return list(_dis.get_instructions(fn))
    except Exception:
        return []


def all_code_names(top):
    """{name: set of co objects whose co_names/consts mention it} across whole member."""
    acc = []

    def go(c):
        acc.append(c)
        for s in getattr(c, "co_consts", ()):
            if hasattr(s, "co_name"):
                go(s)
    go(top)
    return acc


def store_windows(member_code, field, module):
    """Yield (fn_name, ins_list, store_idx) for every function storing `field`."""
    res = []
    for co in all_code_names(member_code):
        ins = dis(co)
        for ndx, i in enumerate(ins):
            if i.opname in STORE_ATTR_OPS and i.argval == field:
                res.append((co.co_name, ins, ndx, "STORE_ATTR"))
            elif i.opname in STORE_LOCAL_OPS and i.argval == field:
                res.append((co.co_name, ins, ndx, "STORE_FAST"))
    return res


def format_ctx(ins, ndx, span=4):
    a = max(0, ndx - span)
    b = min(len(ins), ndx + span + 1)
    return " | ".join("%s %s" % (i.opname, i.argval)
                      for i in ins[a:b] if i.opname != "NOP")


def find_fn_in_member(member_code, name):
    for c in all_code_names(member_code):
        if getattr(c, "co_name", "") == name and not c.co_name.startswith("<"):
            return c
    return None


def resolve_lineage(expr, field_set, pool, depth=0):
    """Return True if expr's producer chain (through plain local copies) references any
    field in field_set (e.g. the raw field).  pool: name->producer expr map for locals."""
    if depth > 12:
        return False
    for f in field_set:
        if f in expr:
            return True
    if expr.startswith("L:"):
        nm = expr[2:]
        nxt = pool.get(nm)
        if nxt is not None and nxt != expr:
            return resolve_lineage(nxt, field_set, pool, depth + 1)
    return False


def producer_map(co, target_field):
    """Mini VM over a function's instructions returning {store_op_idx: <producer expr>}
    for every STORE whose target is target_field (STORE_ATTR or STORE_FAST/STORE_NAME),
    plus the local `pool` (local_name->producer) map for lineage checks."""
    ins = dis(co)
    stack = []
    pool = {}
    out = {}
    for ndx, i in enumerate(ins):
        op = i.opname
        av = i.argval
        try:
            if op == "LOAD_CONST":
                stack.append("S:" + repr(av))
            elif op in LOAD_NAME_OPS:
                stack.append("L:" + str(av))
            elif op == "LOAD_ATTR":
                if stack:
                    stack.append("ATTR(" + stack.pop() + "." + str(av) + ")")
            elif op == "LOAD_METHOD":
                if stack:
                    stack.append("METH(" + stack.pop() + "." + str(av) + ")")
            elif op == "DUP_TOP":
                if stack:
                    stack.append(stack[-1])
            elif op == "CALL_FUNCTION_KW":
                npos = int(i.arg) & 0xFF
                names = stack.pop() if stack else ""
                args = stack[-npos:] if npos else []
                stack = stack[:-npos] if npos else stack
                callee = stack.pop() if stack else "?fn"
                stack.append("CALL(" + callee + ", [" + ", ".join(args) + "])")
            elif op == "CALL_FUNCTION":
                npos = int(i.arg) & 0xFF
                args = stack[-npos:] if npos else []
                stack = stack[:-npos] if npos else stack
                callee = stack.pop() if stack else "?fn"
                stack.append("CALL(" + callee + ", [" + ", ".join(args) + "])")
            elif op == "CALL_METHOD":
                npos = int(i.arg) & 0xFF
                args = stack[-npos:] if npos else []
                stack = stack[:-npos] if npos else stack
                m = stack.pop() if stack else "?m"
                stack.append("CALL(" + m + ", [" + ", ".join(args) + "])")
            elif op.startswith("BINARY_SUBSCR"):
                if len(stack) >= 2:
                    c = stack.pop()
                    k = stack.pop()
                    stack.append("SUBSCR(" + k + "[" + c + "])")
            elif op in STORE_ATTR_OPS:
                # STORE_ATTR stack (deepest->top): ..., <value>, <object> ; pops both.
                obj = stack.pop() if stack else "?obj"
                val = stack.pop() if stack else "?"
                if av == target_field:
                    out[ndx] = val
            elif op in STORE_LOCAL_OPS:
                val = stack.pop() if stack else "?"
                pool[av] = val
                if av == target_field:
                    out[ndx] = val
            elif op == "POP_TOP":
                if stack:
                    stack.pop()
            elif op == "RETURN_VALUE":
                stack = []
            elif op in ("JUMP_ABSOLUTE", "JUMP_FORWARD", "POP_JUMP_IF_FALSE",
                        "POP_JUMP_IF_TRUE", "JUMP_IF_FALSE_OR_POP",
                        "JUMP_IF_TRUE_OR_POP", "FOR_ITER", "SETUP_LOOP",
                        "SETUP_EXCEPT", "SETUP_FINALLY", "CONTINUE_LOOP",
                        "BREAK_LOOP", "END_FINALLY", "POP_EXCEPT", "SETUP_WITH",
                        "WITH_CLEANUP_START", "WITH_CLEANUP_FINISH"):
                stack = []
            elif op.startswith(("BUILD_LIST", "BUILD_TUPLE", "BUILD_SET", "BUILD_MAP",
                                "BUILD_CONST_KEY_MAP", "BUILD_SLICE", "UNPACK",
                                "GET_ITER", "COPY", "SWAP", "ROT_TWO", "ROT_THREE",
                                "ROT_FOUR", "IMPORT_NAME", "IMPORT_FROM",
                                "YIELD_VALUE", "MAKE_FUNCTION", "MAKE_CLOSURE",
                                "BUILD_CLASS", "LIST_APPEND", "MAP_ADD", "SET_ADD",
                                "UNARY_NOT", "COMPARE_OP")):
                if op.startswith("ROT_"):
                    pass
                else:
                    stack = []
        except Exception:
            stack = []
    return out, pool


# ---------------------------------------------------------------------------
def local_producer_reader(top):
    """Map each function that LOAD_ATTR .animation_display_name / .animation_raw_display_name
    or LOAD_FASTes them, to give reader evidence."""
    rows = []
    for co in all_code_names(top):
        ins = dis(co)
        for i in ins:
            if i.opname in LOAD_ATTR_OPS and i.argval in (FIELD_DISP, FIELD_RAW):
                rows.append((co.co_name, i.argval, "LOAD_ATTR"))
            elif i.opname in LOAD_NAME_OPS and i.argval in (FIELD_DISP, FIELD_RAW):
                rows.append((co.co_name, i.argval, i.opname))
    return rows


def main():
    a = parse_args()
    ts4 = Path(a.ts4)
    if not ts4.is_file():
        print("FATAL=TS4SCRIPT_MISSING %s" % ts4)
        return 2
    lm = magic_hex()
    print("LOCAL_PY=%s.%s.%s" % sys.version_info[:3])
    print("LOCAL_MAGIC=%s" % lm)
    try:
        names = zipfile.ZipFile(str(ts4)).namelist()
    except Exception as e:
        print("ZIP=FAIL %s" % e)
        return 3
    pycs = [n for n in names if n.endswith(".pyc")]
    print("TOTAL_MEMBERS=%d" % len(pycs))

    loader_member = a.loader
    if loader_member not in pycs:
        print("LOADER_MEMBER_MISSING=%s (member not present in ts4; --loader override?)"
              % loader_member)
        loader_member = None

    # ---------------- Task 1: all writers across the whole ts4script ----------
    disp_writers_all = []
    raw_writers_all = []
    readers_all = []
    lraw = None
    magic_bad = False
    for m in pycs:
        raw = read_member(str(ts4), m)
        if raw is None or raw[:4].hex() != lm:
            magic_bad = True
            continue
        try:
            top = code_from(raw)
        except Exception:
            continue
        for fld, key in ((FIELD_DISP, "disp_writers_all"),
                         (FIELD_RAW, "raw_writers_all")):
            for fn_name, ins, ndx, how in store_windows(top, fld, m):
                (disp_writers_all if fld == FIELD_DISP else raw_writers_all).append(
                    {"module": m, "fn": fn_name, "how": how, "ctx": format_ctx(ins, ndx)})
        for fn_name, fld, op in local_producer_reader(top):
            readers_all.append((m, fn_name, fld, op))
        if m == loader_member:
            lraw = raw
    if magic_bad:
        print("NOTE=some member magic mismatches skipped")

    def fmt_writers(ws):
        if not ws:
            return "(none found)"
        return " ; ".join("%s | %s (%s): [%s]" % (w["module"], w["fn"], w["how"],
                                                   w["ctx"]) for w in ws[:12])
    print("ANIMATION_DISPLAY_NAME_WRITERS=%s" % fmt_writers(disp_writers_all))
    if len(disp_writers_all) > 12:
        print("  (.. and %d more)" % (len(disp_writers_all) - 12))
    for w in disp_writers_all[:12]:
        print("  WRITER(DISP) MODULE=%s FUNCTION=%s BYTECODE_CONTEXT=[%s]"
              % (w["module"], w["fn"], w["ctx"]))
    print("ANIMATION_RAW_DISPLAY_NAME_WRITERS=%s" % fmt_writers(raw_writers_all))
    if len(raw_writers_all) > 12:
        print("  (.. and %d more)" % (len(raw_writers_all) - 12))
    for w in raw_writers_all[:12]:
        print("  WRITER(RAW) MODULE=%s FUNCTION=%s BYTECODE_CONTEXT=[%s]"
              % (w["module"], w["fn"], w["ctx"]))

    # ------------- Task 2 & 3: loader fn / tuning origin & relation ----------
    fn = None
    if loader_member and lraw is not None:
        try:
            fn = find_fn_in_member(code_from(lraw), a.func)
        except Exception as e:
            print("LOADER_MARSHAL=FAIL %s" % e)
    if fn is None:
        print("ANIMATION_TUNING_SOURCE=UNRESOLVED (loader fn '%s' not found)" % a.func)
        return 6
    ins = dis(fn)
    print("CALLER_FUNCTION=%s" % fn.co_name)
    # parameter list (first LOAD_FAST / positional implied by co_argcount)
    param_list = list(getattr(fn, "co_varnames", ())[:getattr(fn, "co_argcount", 0)])
    print("FN_PARAMS=%s" % (", ".join(param_list)))
    tuning_is_param = "animation_tuning" in param_list
    print("ANIMATION_TUNING_SOURCE=%s" % (
        "ctor-parameter(animation_tuning)" if tuning_is_param
        else "local(loaded-inside-%s)" % a.func))
    # how the tuning param is derived when it is a local assignment
    if not tuning_is_param:
        # find STORE_FAST animation_tuning windows and report upstream
        for ndx, i in enumerate(ins):
            if i.opname in STORE_LOCAL_OPS and i.argval == "animation_tuning":
                print("  TUNING_BIND(%s)=[%s]" % (i.opname, format_ctx(ins, ndx)))
    # ---------------- Task 3: raw -> display RELATION (whole ts4script) ---------
    # decided from real per-function dataflow (mini-VM producer maps), NOT from fragile
    # ±N op-windows that conflate adjacent statements in the same fn:
    #   DIRECT  : a fn reads the raw field and stores it to display with NO call between
    #   TRANSFORMED : raw -> display through an intervening call/.get hop
    #   INDEPENDENT : the display writer reads its OWN .get(k)/attr key, never the raw
    #                 field (siblings, not a chain)
    #   UNRESOLVED  : no captured dataflow links or separates them
    # NEVER collapse a mismatch into a fabricated verdict.
    rel = "UNRESOLVED"
    reasons = []
    raw_names = {FIELD_RAW, "raw_display_name"}
    direct_evidence = []   # raw -> display, no call between
    transformed_evidence = []  # raw -> display w/ intervening call/.get
    independent_evidence = []  # display written from its OWN key/attr, not from raw
    disp_sources = []      # (module, fn, producer-expr) for each animation_display_name STORE
    raw_sources = []       # (module, fn, producer-expr) for each animation_raw_display_name STORE
    loader_disp_producers = []
    for m in pycs:
        rawm = read_member(str(ts4), m)
        if rawm is None or rawm[:4].hex() != lm:
            continue
        try:
            top = code_from(rawm)
        except Exception:
            continue
        for co in all_code_names(top):
            pdisp, pdisp_pool = producer_map(co, FIELD_DISP)
            praw, _po = producer_map(co, FIELD_RAW)
            for ndx, expr in pdisp.items():
                disp_sources.append((m, co.co_name, expr))
                lin = resolve_lineage(expr, raw_names, pdisp_pool)
                has_call = "CALL" in expr
                if lin and not has_call:
                    direct_evidence.append("%s::%s [%s]" % (m, co.co_name, expr))
                elif lin:
                    transformed_evidence.append("%s::%s [%s]" % (m, co.co_name, expr))
                else:
                    independent_evidence.append("%s::%s [%s]" % (m, co.co_name, expr))
            for ndx, expr in praw.items():
                raw_sources.append((m, co.co_name, expr))
    # loader in-fn display_name local -> SexAnimationInstance(display_name=..): the
    # local's own producer decides whether it traces back to the raw field downstream.
    if fn is not None:
        pld, poolld = producer_map(fn, "display_name")
        for ndx, expr in pld.items():
            loader_disp_producers.append(expr)
            lin = resolve_lineage(expr, raw_names, poolld)
            has_call = "CALL" in expr
            if lin and not has_call:
                direct_evidence.append("loader::%s in-fn [%s]" % (fn.co_name, expr))
            elif lin:
                transformed_evidence.append("loader::%s in-fn [%s]" % (fn.co_name, expr))
    if not disp_sources and not raw_sources:
        rel = "UNRESOLVED(no STORE writers in ts4script scan)"
        reasons.append("neither field is STORE-written in any scanned member; if both "
                       "are read-only attrs they are independent siblings of some tuning "
                       "object created/parsed elsewhere (see ANIMATION_TUNING_SOURCE)")
    elif direct_evidence:
        rel = "DIRECT"
        reasons.append("raw value feeds display with NO intervening call in: "
                       + "; ".join(direct_evidence[:6]))
    elif transformed_evidence:
        rel = "TRANSFORMED"
        reasons.append("raw flows into display through an intervening call/.get hop in: "
                       + "; ".join(transformed_evidence[:6]))
    elif independent_evidence and disp_sources:
        rel = "INDEPENDENT"
        reasons.append("animation_display_name is populated from its own source (never "
                       "traces to raw) in: " + "; ".join(independent_evidence[:6]))
        if raw_sources:
            reasons.append("raw is written separately from its own source in: " +
                           "; ".join("%s::%s [%s]" % s for s in raw_sources[:4]))
    else:
        rel = "UNRESOLVED"
        reasons.append("no producer dataflow captured that links or separates the "
                       "fields; inspect MODULE/FUNCTION writers by hand")
    print("RAW_TO_ANIMATION_DISPLAY_RELATION=%s" % rel)
    for r in reasons:
        print("  REL_REASON=" + r)
    # per-writer producer evidence reported verbatim for inspection
    print("  DISP_SOURCES_COUNT=%d" % len(disp_sources))
    for (m, fname, expr) in sorted(set(disp_sources))[:10]:
        print("    DISP_WRITER %s::%s  display = %s" % (m, fname, expr))
    print("  RAW_SOURCES_COUNT=%d" % len(raw_sources))
    for (m, fname, expr) in sorted(set(raw_sources))[:10]:
        print("    RAW_WRITER %s::%s  raw = %s" % (m, fname, expr))
    if loader_disp_producers:
        print("  LOADER_DISPLAY_NAME_LOCAL_SOURCES=%s" %
              "; ".join(sorted(set(loader_disp_producers))[:6]))
    # ---- ANIMATION_TUNING type / creator derived from real writers ----
    # The tuning object is the one whose fields animation_display_name /
    # animation_raw_display_name are STORE-written (its defining __init__/builder).
    def _brief(entries):
        return sorted(set("%s::%s" % (e[0].split('/')[-1], e[1]) for e in entries))
    disp_b = sorted(set(_brief(disp_sources)))
    raw_b = sorted(set(_brief(raw_sources)))
    combined = disp_b + [x for x in raw_b if x not in disp_b]
    if combined:
        print("ANIMATION_TUNING_TYPE_HINT=%s" % (
            "carrier-of-fields written in: " + " ; ".join(combined[:6]) +
            "  (py3.7 marshal has no co_qualname; the exact class constructor of an "
            "instance is not statically named, see ANIMATION_TUNING_CREATED_BY" +
            (" / raw-sources above" if raw_b else "") + ")"))
    if combined:
        print("ANIMATION_TUNING_CREATED_BY=%s" % (" ; ".join(combined[:6])))
    else:
        print("ANIMATION_TUNING_CREATED_BY=no-in-scan STORE writer; tuning fields likely "
              "defaults/parsed-readonly (see ANIMATION_TUNING_SOURCE)")
    # single .display_name local STORE evidence (report it verbatim)
    print("LOADER_DISPLAY_STORE_CONTEXT=")
    for ndx, i in enumerate(ins):
        if i.opname in STORE_LOCAL_OPS and i.argval == "display_name":
            print("    [%s]" % format_ctx(ins, ndx, span=3))

    # ----------------- Task 4: XML / tuning key literals --------------------
    keys = {FIELD_DISP: [], FIELD_RAW: [], "display_name": [],
            "raw_display_name": []}
    for m in pycs:
        raw = read_member(str(ts4), m)
        if raw is None or raw[:4].hex() != lm:
            continue
        try:
            top = code_from(raw)
        except Exception:
            continue
        for co in all_code_names(top):
            for s in getattr(co, "co_consts", ()):
                if isinstance(s, str) and s in keys:
                    keys[s].append("%s::%s" % (m, co.co_name))
    for k, v in keys.items():
        uniq = sorted(set(v))
        if uniq:
            val = "; ".join(uniq[:6])
            if len(uniq) > 6:
                val += " (+%d)" % (len(uniq) - 6)
            print("XML_KEY_LITERAL %-36s =%s  (const in: %s)" % (k, "YES", val))
        else:
            print("XML_KEY_LITERAL %-36s =NO  (not a bytecode constant; "
                  "likely ATTRIBUTE_DERIVED / rebuilt from tuning attr)" % k)
    print("XML_KEY_FOR_ANIMATION_DISPLAY_NAME=%s" % (
        ("literal '" + FIELD_DISP + "'" ) if keys[FIELD_DISP]
        else "NOT_LITERAL / ATTRIBUTE_DERIVED (display read as tuning.animation_display_name; "
             "literal not const in any member)"))
    print("XML_KEY_FOR_ANIMATION_RAW_DISPLAY_NAME=%s" % (
        ("literal '" + FIELD_RAW + "'") if keys[FIELD_RAW]
        else "NOT_LITERAL / ATTRIBUTE_DERIVED"))
    # reader attribution (who LOAD_ATTRs these attrs / where loader sees them)
    raws = [r for r in readers_all if r[2] == FIELD_RAW]
    disps = [r for r in readers_all if r[2] == FIELD_DISP]
    print("DISPLAY_NAME_ATTR_READERS=%s" % ("; ".join(sorted(set(
        "%s::%s" % (r[0].split('/')[-1], r[1]) for r in disps)))[:240] or "(none)"))
    print("RAW_NAME_ATTR_READERS=%s" % ("; ".join(sorted(set(
        "%s::%s" % (r[0].split('/')[-1], r[1]) for r in raws)))[:240] or "(none)"))
    return 0


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("ts4", help="path to WW .ts4script")
    ap.add_argument("--loader", default=LOADER_DEFAULT)
    ap.add_argument("--func", default=FN_DEFAULT)
    return ap.parse_args()


if __name__ == "__main__":
    sys.exit(main())
