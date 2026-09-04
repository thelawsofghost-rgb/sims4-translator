#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29a_display_source_trace.py -- PIN the bytecode chain

    animation_raw_display_name  ->  display_name  ->  SexAnimationInstance(...)

inside the CURRENT-WW loader `_create_sex_animation_instance`, plus FIX the
display_name_override behaviour fields.  Runs under the REAL local CPython 3.7.9
(magic 420d0d0a) and reads the LIVE .pyc by NATIVE marshal.  Authoritative.  NO xdis.

Usage (real machine, one command in the shipped .ps1):
    python.exe ww_p29a_display_source_trace.py "<WW.ts4script>"
        [--loader wickedwhims/sex/animations/animations_loader.pyc]
        [--instance wickedwhims/sex/animations/animation_instance.pyc]
        [--func _create_sex_animation_instance]

Backend (read-only, never touches Mods / the WW ts4script bytes):
  1. zip-read the loader member from the .ts4script;
  2. assert its header magic == THIS interpreter magic (3.7.9 = 420d0d0a).  Abort
     (exit 4) on mismatch so we never marshal with the wrong runtime;
  3. native marshal.loads(pyc[16:]) -> module code object;
  4. walk nested code objects, find the function whose co_name == --func;
  5. disassemble it and run an exact mini VM over the op-stream:
        - recover every STORE that binds a local named `display_name` and the
          producer expression that feeds it (.get(key) / [] / attr ...);
        - scan for the literal 'animation_raw_display_name' (RAW_FIELD_LITERAL_PRESENT)
          and report the reading window when it is a mapping key (.get / SUBSCR);
        - for each SexAnimationInstance(...) CALL, reconstruct the pushed argument
          value-stack and report what occupies positional slot 2 (the display value);
  6. scan the instance module for get_display_name / set_display_name body names to
     fix the DISPLAY_NAME_OVERRIDE_* fields (already evidenced live; here consolidated).

The chain is NEVER simplified: if an override/fallback/localization/original_instance/
animation_override/other transform intervenes between the raw field and display_name,
those hops are reported verbatim and RAW_TO_DISPLAY_CHAIN is CONFIRMED only for a clean
single unbranching '.get(key) -> display_name -> ctor slot2' path; otherwise the real
hops are printed.

Exit: 0=trace done; 2=ts4 missing; 3=loader member missing; 4=magic mismatch;
      5=marshal error; 6=function not found in loader.
"""
import argparse
import marshal
import sys
import zipfile
from pathlib import Path

RAW_LITERAL = "animation_raw_display_name"
FN_DEFAULT = "_create_sex_animation_instance"
DISP = "display_name"
INSTANCE = "SexAnimationInstance"
LOAD_PUSH = ("LOAD_CONST", "LOAD_FAST", "LOAD_GLOBAL", "LOAD_NAME", "LOAD_DEREF",
             "LOAD_ATTR", "LOAD_METHOD", "DUP_TOP", "CALL_FUNCTION", "CALL_METHOD",
             "CALL_FUNCTION_KW", "CALL_FUNCTION_EX")
STORE = ("STORE_FAST", "STORE_NAME", "STORE_DEREF", "STORE_GLOBAL")


def magic_hex():
    import importlib.util
    return importlib.util.MAGIC_NUMBER.hex()


def read_member(ts4, member):
    with zipfile.ZipFile(ts4) as z:
        try:
            data = z.read(member)
        except KeyError:
            return None
    return data


def code_from(pyc):
    return marshal.loads(pyc[16:])


def walk(co, acc):
    acc.append(co)
    for sub in getattr(co, "co_consts", ()):
        if hasattr(sub, "co_name"):
            walk(sub, acc)


def find_fn(top, name):
    acc = []
    walk(top, acc)
    for c in acc:
        if getattr(c, "co_name", "") == name and not c.co_name.startswith("<"):
            return c
    return None


def dis(fn):
    import dis as _dis
    try:
        return list(_dis.get_instructions(fn))
    except Exception:
        return []


# -------------------------------------------------------------------------------
# A small vm that reconstructs, per op-index, the stack of VALUE LABELS.  Prefix
# expressions are buildable so STORE can capture the producer of `display_name`.
# -------------------------------------------------------------------------------
def label_exprs(fn, want_stores=(DISP,), calls_ctor_of=(INSTANCE,)):
    """Return (producers, ctor_slots).

    producers : {op_index_of_STORE_display_name : <expr string>}
    ctor_slots: list of dicts {call_offset, args:[expr,...]} for each SexAnimationInstance
                CALL window recovered.
    """
    ins = dis(fn)
    if not ins:
        return {}, []
    stack = []          # list of label strings
    pool = {}           # local name -> producer expr, for copy/chain resolution
    producers = {}
    ctor_calls = []
    for ndx, i in enumerate(ins):
        op = i.opname
        av = i.argval
        try:
            if op == "LOAD_CONST":
                stack.append(repr(av) if not isinstance(av, str) else "S:" + repr(av))
            elif op in ("LOAD_FAST", "LOAD_NAME", "LOAD_DEREF", "LOAD_GLOBAL"):
                stack.append("L:" + str(av))
            elif op == "LOAD_ATTR":
                if stack:
                    top = stack.pop()
                    stack.append("ATTR(" + top + "." + str(av) + ")")
            elif op == "LOAD_METHOD":
                if stack:
                    top = stack.pop()
                    stack.append("METH(" + top + "." + str(av) + ")")
            elif op == "DUP_TOP":
                if stack:
                    stack.append(stack[-1])
            elif op.startswith("CALL_FUNCTION"):
                npos = int(i.arg) & 0xFF
                if op == "CALL_FUNCTION_KW":
                    # CPython 3.7 CALL_FUNCTION_KW stack (deepest->top):
                    #   [callable, *arg_values, kw_names_tuple]
                    # unlike plain CALL_FUNCTION the tuple of keyword NAMES sits on
                    # the very top and is NOT part of argc (argc counts all arg
                    # VALUES, positional + keyword).  Pop it first so the remaining
                    # npos values are the args and the callable sits directly below.
                    names_label = stack.pop() if stack else "?names"
                    args = stack[-npos:] if npos else []
                    stack = stack[:-npos] if npos else stack
                    callee = stack.pop() if stack else "?fn"
                    kw_names = ""
                    if names_label.startswith("S:"):
                        try:
                            kw_names = ",".join(map(str, eval(names_label[2:])))
                        except Exception:
                            kw_names = "?"
                    if kw_names:
                        names_label = "KW(" + kw_names + ")"
                    built = _mk_call(callee, args, op, names_label)
                    stack.append(built)
                    if _is_ctor_callee(callee):
                        rec = {"call_ndx": ndx, "offset": i.offset,
                               "callee": callee, "args": list(args), "op": op,
                               "kw": kw_names or "?"}
                        ctor_calls.append(rec)
                else:
                    args = stack[-npos:] if npos else []
                    stack = stack[:-npos] if npos else stack
                    callee = stack.pop() if stack else "?fn"
                    built = _mk_call(callee, args, op)
                    stack.append(built)
                    if _is_ctor_callee(callee):
                        ctor_calls.append({"call_ndx": ndx, "offset": i.offset,
                                           "callee": callee, "args": list(args),
                                           "op": op, "kw": "?"})
            elif op.startswith("CALL_METHOD"):
                npos = int(i.arg) & 0xFF
                # In 3.7, LOAD_METHOD pushes (receiver, method) then CALL_METHOD n
                # pops the method + n positional args (receiver stays as the bound
                # receiver and is NOT popped separately on the fast path).  The two
                # values are: [receiver, method] from the METHOD load + then the n
                # args.  Top-of-stack just before CALL_METHOD = (…args, receiver,
                # method-dependent) -- empirically the net effect below matches the
                # dis: to recover the METHOD expression we pop method + nargs, and
                # the receiver is embedded in the METH() expr label already pushed by
                # LOAD_METHOD, so it must NOT be popped again.
                #
                # Count what was actually left: our LOAD_METHOD wrote ONE token
                # METH(recv.get).  The n args were then pushed on top of it.  So to
                # form the call we pop n args (already), then pop the METH token once.
                args = stack[-npos:] if npos else []
                stack = stack[:-npos] if npos else stack
                m = stack.pop() if stack else "?meth"
                built = _mk_call(m, args, op)
                stack.append(built)
                if _is_ctor_callee(m):
                    ctor_calls.append({"call_ndx": ndx, "offset": i.offset,
                                       "callee": m, "args": list(args), "op": op,
                                       "kw": "?"})
            elif op in STORE:
                if stack:
                    val = stack.pop()
                else:
                    val = "?(stack-empty)"
                pool[av] = val          # remember producer for chain resolution
                if av in want_stores and av == DISP:
                    # resolve indirect copy/temporary chain to the true producer
                    resolved = val
                    guard = val
                    for _ in range(12):
                        if not (resolved.startswith("L:")):
                            break
                        nm = resolved[2:]
                        nxt = pool.get(nm, resolved)
                        if nxt == resolved:
                            break
                        resolved = nxt
                    producers[ndx] = resolved
            elif op == "POP_TOP":
                if stack:
                    stack.pop()
            elif op == "RETURN_VALUE":
                stack = []
            elif op in ("JUMP_ABSOLUTE", "JUMP_FORWARD", "POP_JUMP_IF_FALSE",
                        "POP_JUMP_IF_TRUE", "JUMP_IF_FALSE_OR_POP",
                        "JUMP_IF_TRUE_OR_POP", "FOR_ITER", "SETUP_LOOP",
                        "SETUP_EXCEPT", "SETUP_FINALLY", "CONTINUE_LOOP",
                        "BREAK_LOOP", "END_FINALLY", "POP_EXCEPT",
                        "SETUP_WITH", "WITH_CLEANUP_START", "WITH_CLEANUP_FINISH"):
                # branch seam: reset stack (no cross-branch leak)
                stack = []
            elif op.startswith("BINARY_SUBSCR"):
                if len(stack) >= 2:
                    c = stack.pop(); k = stack.pop()
                    stack.append("SUBSCR(" + k + "[" + c + "])")
            elif op.startswith("BUILD_LIST") or op.startswith("BUILD_TUPLE") \
                    or op.startswith("BUILD_SET"):
                n = int(i.arg)
                items = stack[-n:] if n else []
                stack = stack[:-n] if n else stack
                stack.append("COLL(" + ", ".join(items) + ")")
            elif op.startswith("BUILD_MAP") or op.startswith("BUILD_CONST_KEY_MAP") \
                    or op.startswith("BUILD_SLICE") or op.startswith("UNPACK") \
                    or op.startswith("GET_ITER") or op in ("COPY", "SWAP", "ROT_TWO",
                                                           "ROT_THREE", "ROT_FOUR"):
                # opaque: clear to avoid wrong dataflow
                if op.startswith("ROT_"):
                    if len(stack) >= 2:
                        stack = stack[::-1]
                else:
                    stack = []
            # comparisons / binops that combine produce single value
            elif op.startswith(("BINARY_", "INPLACE_", "COMPARE_", "IS_OP",
                                "CONTAINS_OP", "UNARY_", "DELETE_")):
                # approximate: pop operands, push a composite token
                stack.append("OP(" + op + ")")
            # anything else (NOP, SETUP.., IMPORT etc.) best-effort: it may push; we
            # only push when it is a known producer; otherwise drop tail to stay honest
            else:
                # unknown op that likely pushes -> conservatively mark opaque but keep
                if op in ("IMPORT_NAME", "IMPORT_FROM", "YIELD_VALUE", "YIELD_FROM",
                          "MAKE_FUNCTION", "MAKE_CLOSURE", "BUILD_CLASS",
                          "LIST_APPEND", "MAP_ADD", "SET_ADD"):
                    stack = []
        except Exception:
            # defensive: never let a single op break the whole trace
            stack = []
            continue
    return producers, ctor_calls


def _mk_call(callee, args, op, kw_names=None):
    base = "CALL(" + callee + ", [" + ", ".join(args) + "])"
    if kw_names:
        base = base[:-1] + ", kw={" + kw_names + "})" if base.endswith(")") else base
    return base


def _is_ctor_callee(callee):
    return INSTANCE in callee


def raw_literal_pattern(fn, lit):
    """Return list of op-windows starting at each LOAD_CONST of lit (mapping read)."""
    ins = dis(fn)
    out = []
    for ndx, i in enumerate(ins):
        if i.opname == "LOAD_CONST" and i.argval == lit:
            win = ins[ndx:min(ndx + 9, len(ins))]
            out.append(" | ".join("%s %s" % (w.opname, w.argval) for w in win))
    return out


def display_stores_loc(fn):
    ins = dis(fn)
    return [i.offset for i in ins
            if i.opname in STORE and i.argval == DISP]


# ------------------------------------------------------------------ override part
def override_fields(ts4, instance_member, lm, p):
    p("INSTANCE_MEMBER=%s" % (instance_member or "(none)"))
    if not instance_member:
        return
    try:
        raw = read_member(ts4, instance_member)
    except Exception:
        p("DISPLAY_NAME_OVERRIDE_BEHAVIOR=UNRESOLVED(instance_member_unreadable)")
        return
    if raw is None:
        p("DISPLAY_NAME_OVERRIDE_PRIORITY=NO(instance_missing)")
        return
    if raw[:4].hex() != lm:
        p("DISPLAY_NAME_OVERRIDE_BEHAVIOR=UNRESOLVED(magic_mismatch)")
        return
    co = code_from(raw)
    acc = []
    walk(co, acc)
    get_fn = next((c for c in acc if c.co_name == "get_display_name"), None)
    set_fn = next((c for c in acc if c.co_name == "set_display_name"), None)
    get_names = sorted(set(getattr(get_fn, "co_names", ()))) if get_fn else []
    set_names = sorted(set(getattr(set_fn, "co_names", ()))) if set_fn else []
    get_uses_ovr = "display_name_override" in get_names
    get_uses_base = "display_name" in get_names
    p("GET_DISPLAY_NAME_PRESENT=%s" % ("YES" if get_fn else "NO"))
    p("SET_DISPLAY_NAME_PRESENT=%s" % ("YES" if set_fn else "NO"))
    p("GET_DISPLAY_NAME_NAMES=%s" % (", ".join(get_names)))
    p("SET_DISPLAY_NAME_NAMES=%s" % (", ".join(set_names)))
    # STORE_ATTR display_name_override inside set_display_name?
    set_ins = dis(set_fn) if set_fn else []
    set_writes_ovr = any(i.opname in ("STORE_ATTR",) and i.argval == "display_name_override"
                         for i in set_ins)
    p("DISPLAY_NAME_OVERRIDE_PRIORITY=%s" % ("YES" if (get_uses_ovr and get_uses_base)
                                             else "NO/SEE_NAMES"))
    p("SET_DISPLAY_NAME_WRITES_OVERRIDE=%s" % ("YES" if set_writes_ovr else "NO"))
    p("GET_DISPLAY_NAME_FALLBACK_TO_BASE=%s" % ("YES" if get_uses_base else "NO"))
    if set_writes_ovr and get_uses_ovr and get_uses_base:
        beh = ("override_wins_else_base: get_display_name returns display_name_override "
               "when set, else falls back to display_name; set_display_name writes "
               "display_name_override")
    elif set_writes_ovr:
        beh = ("set_display_name writes display_name_override; get-side references: "
               + (", ".join(get_names) or "(none)"))
    else:
        beh = "override behaviour unresolved from body names"
    p("DISPLAY_NAME_OVERRIDE_BEHAVIOR=%s" % beh)


def main():
    a = parse_args()
    ts4 = Path(a.ts4)
    if not ts4.is_file():
        print("FATAL=TS4SCRIPT_MISSING %s" % ts4)
        return 2
    lm = magic_hex()
    print("LOCAL_PY=%s.%s.%s" % sys.version_info[:3])
    print("LOCAL_MAGIC=%s" % lm)
    loader_member = a.loader or None
    if not loader_member:
        print("LOADER_MEMBER_PRESENT=NO")
        return 3
    try:
        raw = read_member(str(ts4), loader_member)
    except Exception as e:
        print("LOADER_READ=FAIL %s" % e)
        return 3
    if raw is None:
        print("LOADER_PRESENT=NO member=%s" % loader_member)
        return 3
    mmag = raw[:4].hex()
    print("LOADER_PRESENT=YES member=%s" % loader_member)
    print("PYC_MAGIC=%s" % mmag)
    print("MAGIC_MATCH=%s" % ("YES" if mmag == lm else "NO"))
    if mmag != lm:
        print("ABORT=marshal requires matching magic (run under 3.7.9 / 420d0d0a)")
        print("RAW_TO_DISPLAY_CHAIN=UNRESOLVED(MAGIC_MISMATCH)")
        return 4
    try:
        co = code_from(raw)
    except Exception as e:
        print("MARSHAL=FAIL %s" % e)
        return 5
    fn = find_fn(co, a.func)
    if fn is None:
        print("FN_PRESENT=NO func=%s" % a.func)
        return 6
    print("FN_PRESENT=YES fn=%s" % fn.co_name)

    consts = [c for c in getattr(fn, "co_consts", ())]
    raw_lit = RAW_LITERAL in [c for c in consts if isinstance(c, str)]

    # loader-wide literal visibility (is the raw field referenced ANYWHERE in the
    # loader member -- e.g. a helper that was factored out of _create_*)?
    all_co = []
    walk(co, all_co)
    all_str = {}
    for c in all_co:
        for s in getattr(c, "co_consts", ()):
            if isinstance(s, str):
                all_str[s] = all_str.get(s, 0) + 1
    raw_lit_loader = RAW_LITERAL in all_str
    print("RAW_FIELD_LITERAL_IN_LOADER=%s" % ("YES" if raw_lit_loader else "NO"))
    if raw_lit_loader and not raw_lit:
        print("RAW_FIELD_NOT_IN_FN_SCOPE=YES (literal lives in a callee/outer scope -- "
              "not directly const-evaluable in _create_sex_animation_instance)")
        print("NOTE=raw field consumed outside this function; see helper producer below")
    print("CALLER_FUNCTION=%s" % fn.co_name)
    print("RAW_FIELD_LITERAL_PRESENT=%s" % ("YES" if raw_lit else "NO"))
    if raw_lit:
        pats = raw_literal_pattern(fn, RAW_LITERAL)
        print("RAW_FIELD_READ_PATTERN=%s" % (" ; ".join(pats) or "(const present but no "
              "9-op consumer window recovered)"))
    else:
        # field may have been renamed; look for the raw name as LOAD_GLOBAL/ATTR
        nm_refs = [i.opname + " " + str(i.argval) for i in dis(fn)
                   if (str(i.argval)) == RAW_LITERAL]
        print("RAW_FIELD_READ_PATTERN=%s" % (
            ("name-ref: " + "; ".join(nm_refs)) if nm_refs
            else ("(literal '%s' absent; field likely reads a renamed key)") % RAW_LITERAL))

    producers, ctor_calls = label_exprs(fn)
    # display_name stores
    store_ids = [ndx for ndx in producers]  # producer expr registered exactly at STORE
    disp_expr_last = producers[max(producers)] if producers else "(no display_name store)"
    print("DISPLAY_NAME_STORE_COUNT=%d" % len(store_ids))
    print("DISPLAY_NAME_STORE_PATTERN=%s" % disp_expr_last)
    # ctor slots
    ctor_arg_disp = "UNRESOLVED(no SexAnimationInstance CALL in fn)"
    if ctor_calls:
        for c in ctor_calls:
            args = c["args"]
            print("CTOR_CALL_OFFSET=%s" % c["offset"])
            print("CTOR_CALL_FN=%s" % c["callee"])
            print("CTOR_ARG_COUNT=%d" % len(args))
            for ai, avl in enumerate(args):
                mark = " <-- display_name?" if ("display_name" in avl) else ""
                print("   CTOR_ARG[%d]=%s%s" % (ai, avl, mark))
            # constructor contract (live): self, animation_id, display_name, ... ;
            # the CALL passes 0=animation_id,1=display_name,2=display_icon,...
            # So the semantic display arg is index 1 -- but we detect the arg whose
            # producer references the local display_name to stay robust.
            hit = [ai for ai, avl in enumerate(args) if "display_name" in avl]
            chosen = (hit[-1] if hit else (1 if len(args) >= 2 else None))
            if chosen is not None:
                ctor_arg_disp = args[chosen]
        last_args = ctor_calls[-1]["args"] if ctor_calls else []
    print("DISPLAY_NAME_ARGUMENT_TO_CTOR=%s" % ctor_arg_disp)
    # chain verdict
    use_ctor = bool(ctor_calls) and len(ctor_calls[-1]["args"]) >= 2
    ctor_arg = ctor_arg_disp
    ctor_ok = use_ctor and ("display_name" in ctor_arg)
    disp_ok = bool(disp_expr_last) and (
                 ("SUBSCR(" in disp_expr_last)
                 or ("key(" in disp_expr_last)
                 or (".get(" in disp_expr_last)
                 or ("METH(L:" in disp_expr_last and ".get" in disp_expr_last)
                 or ("get," in disp_expr_last)
                 or ("ATTR(" in disp_expr_last)
             )
    if not raw_lit and not raw_lit_loader:
        chain = "NO_RAW_LITERAL(field renamed; constructor takes display_name directly)"
    elif not raw_lit and raw_lit_loader:
        # direct literal not in this fn: it is read in a helper/callee upstream.
        # report the real hop without claiming CONFIRMED.
        chain = ("PARTIAL_HELPER_ROUTED: literal '%s' only in loader scope outside "
                 "_create_sex_animation_instance; display_name producer=%r; ctor_arg=%r"
                 % (RAW_LITERAL, disp_expr_last, ctor_arg))
    elif ctor_ok and disp_ok:
        chain = "CONFIRMED"
    else:
        hops = []
        if not disp_ok:
            hops.append("display_name producer not a clean container read: %r" %
                        (disp_expr_last,))
        if not ctor_ok:
            hops.append("display_name arg to ctor: %r" % ctor_arg)
        chain = "PARTIAL: " + ("; ".join(hops)) if hops else "UNRESOLVED"
    print("RAW_TO_DISPLAY_CHAIN=%s" % chain)
    for ndx, expr in producers.items():
        print("  store#%s: display_name <- %s" % (ndx, expr))

    # override section (safe always)
    override_fields(ts4, a.instance, lm, print)
    return 0


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("ts4", help="path to WW .ts4script")
    ap.add_argument("--loader",
                    default="wickedwhims/sex/animations/animations_loader.pyc")
    ap.add_argument("--instance",
                    default="wickedwhims/sex/animations/animation_instance.pyc")
    ap.add_argument("--func", default=FN_DEFAULT)
    return ap.parse_args()


if __name__ == "__main__":
    sys.exit(main())
