#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29d_row_producer_trace.py -- P29-D RECEIVER-AWARE ROW-PRODUCER TRACE (read-only)

GOAL
----
Statically locate, inside ww185 + separately-packaged turbolib, WHO fills
TurboObjectPickerRow.name for the concrete WW sex-animation picker rows.

WHY NOT the previous global method-name BFS
-------------------------------------------
Dorothy (2026-09-05) recovered real reverse_trace evidence and rejected the global
name-only caller BFS as false-positive-riddled (cross-class name collisions created
fake chains like delete_playlist/zone_tick/hello_message). A method call must only be
counted when its RECEIVER is provably a real turbolib instance/class, tracked within
the SAME function body (receiver-aware, import/alias-aware). No cross-module name-only
edge is ever created.

CONFIRMED REAL FACTS (fixed anchor, from Dorothy)
-------------------------------------------------
TurboObjectPickerDialog.create_picker_row(self, name, description, icon, tag,
                                          index, picker_rows_state)
ROW_TITLE_ARGUMENT=name
_build_dialog_picker_rows does NOT compute animation text; it only CONSUMES fully
constructed TurboObjectPickerRow via one of two sources:
    (a) dynamic_picker_rows_func(picker_rows_state)
    (b) self.picker_rows[picker_rows_state]
So the only remaining question is WHO constructs a concrete animation row and assigns
its name -- handled below as two producers:

STATIC  : dialog.create_picker_row(...) call site
          -> NAME_ARG_SOURCE / DESCRIPTION_ARG_SOURCE / TAG_ARG_SOURCE
DYNAMIC : dialog.set_dynamic_picker_rows_func(func)
          -> resolve the real code object of func / its closure, then find the
             TurboObjectPickerRow(...) constructions it returns
          -> DYNAMIC_FUNC / DYNAMIC_ROW_NAME_SOURCE

SCOPE / FILTERING
-----------------
Keep modules matching WW sex/animation context:
    wickedwhims.sex.*  animation  animations  integral  dialogs  query
Exclude: universal category picker, poseplayer, settings, CAS, stripclub and other
non-sex-animation pickers.

NAME SOURCE TAGS (must be surfaced when detected)
-------------------------------------------------
animation_instance.get_display_name()
animation_instance.display_name
animation_instance.get_stage_name()
animation_instance.animation_stage_name
DTO.name / .title / .text
otherwise "other/static/unknown"
Adjacent bytecode is emitted as evidence (never guessed).

OUTPUT FILE (written only when the user actually RUNS the tool)
---------------------------------------------------------------
output/ww_p29d/ww_p29d_row_producer_trace.txt

CONSTRAINTS
-----------
ZERO_WRITE_TO_MODS=YES. No runtime hooks. No edits to TEST300/stage_name/XML/P28C.
This tool reads .ts4script packages and writes ONLY the report file above.
Exit fail-closed: 2 source | 3 --dir | 4 no WW pkg | 5 no receiver-aware activity |
6 no xdis | 7 unparseable member.

RUN (read-only) example:
    python scripts/ww_p29d_row_producer_trace.py "<WWsource>.ts4script" --dir "C:\\...\\Mods"
"""

import argparse
import io as _io
import re as _re
import sys
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from xdis.load import load_module_from_file_object
    from xdis.op_imports import get_opcode_module, PythonImplementation
    from xdis.disasm import Bytecode
    XDIS = True
except Exception:
    XDIS = False

OUT_DIR = Path("output/ww_p29d")
PKG_HINT = "TURBODRIVER_WickedWhims_Scripts"
ANCHOR_MODULE = "turbolib2.ui.object_picker_dialog"
DEFAULT_OUT = "ww_p29d_row_producer_trace.txt"

# The turbolib classes we build receivers / rows from.
DIALOG_CLS = "TurboObjectPickerDialog"
ROW_CLS = "TurboObjectPickerRow"

# WW sex / animation relevance: dotted-module subsequence tests (substring)
KEEP_MOD = ("wickedwhims.sex.", ".sex.", "animation", "integral.", "dialogs.",
            ".dialog.", "query.")
# explicit module exclusions that should never be treated as a target picker
EXCLUDE_MOD = ("poseplayer", "pose_player", "settings", "cas", "stripclub",
               "tattoo", "birth", "pregnan", "menstru", "socialpicker",
               "outfit", "wardrobe")
# function-name exclusions (universal / category pickers)
EXCLUDE_FN = ("universal", "category", "category_picker", "pose_picker")

# row-name attribute set (DTO / instance field that can carry the shown text)
DTO_NAME_ATTR = {"name", "title", "text", "display_name", "display_text"}
# recognized animation-name accessor method attrs
ANIM_ACC = {"get_display_name", "display_name", "get_stage_name", "stage_name",
            "animation_stage_name", "get_animation_name", "animation_name",
            "get_name", "get_title"}


def _rel_dotted(rel):
    base = rel[:-4].replace("/", ".") if rel.endswith(".pyc") else rel
    return base.strip(".")


def _get_opc(fn_like):
    try:
        ver = tuple(str(x) for x in fn_like.co_version[:2])
    except Exception:
        ver = (str(fn_like.co_firstlineno or 3), "0")
    return get_opcode_module(ver, PythonImplementation.CPython)


def _walk_codes(co, acc):
    acc.append(co)
    for c in co.co_consts:
        if hasattr(c, "co_name"):
            _walk_codes(c, acc)


def parse_member(name, data):
    """(opc, [code_objects]) for one .pyc member inside a ts4script."""
    mod = load_module_from_file_object(_io.BytesIO(data), filename=Path(name).name)
    ver = mod[0]
    co = mod[3]
    codes = []
    _walk_codes(co, codes)
    try:
        vstr = tuple(str(x) for x in ver[:2])
    except Exception:
        vstr = ("3", "7")
    opc = get_opcode_module(vstr, PythonImplementation.CPython)
    return opc, codes, co


def _insns(fn):
    """xdis Bytecode iteration -> list of dicts {index, op, arg:string, argrepr,
    value, line}.

    ``value`` is the resolved argument value when available (we specifically need
    it for code-object constants so MAKE_FUNCTION can capture the real co).
    """
    out = []
    try:
        for it in Bytecode(fn["co"], fn["opc"]):
            arg = it.argrepr or ""
            out.append({"index": getattr(it, "offset", None),
                        "op": it.opname, "arg": arg,
                        "value": getattr(it, "argval", None),
                        "line": getattr(it, "lineno", None) or fn["line"]})
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# module-level alias resolution
# ---------------------------------------------------------------------------
def module_alias_env(mod_code_fn):
    """Return (set_of_dialog_global_names, set_of_row_global_names).

    Walk the <module> code's IMPORT_NAME / IMPORT_FROM / STORE_NAME to learn the
    names under which the turbolib dialog/row classes are reachable as globals in
    every function of that module.  The distinctive class names are always included
    (covers unaliased direct use); extra names come from `import X as Y`.
    """
    dg, rg = set(), set()
    names_txt = " ".join(mod_code_fn["co"].co_names)
    if DIALOG_CLS in names_txt:
        dg.add(DIALOG_CLS)
    if ROW_CLS in names_txt:
        rg.add(ROW_CLS)
    insns = _insns(mod_code_fn)
    cur_dotted = None
    i = 0
    n = len(insns)
    while i < n:
        op = insns[i]["op"]
        arg = insns[i]["arg"]
        if op == "IMPORT_NAME":
            cur_dotted = (arg or "").strip().strip("'\"")
            j = i + 1
            # `import a.b.c` optionally followed by STORE_NAME alias
            if j < n and insns[j]["op"] in ("STORE_NAME", "STORE_GLOBAL"):
                alias = insns[j]["arg"].strip().strip("'\"")
                base = cur_dotted.split(".")[-1] or ""
                if base == DIALOG_CLS and cur_dotted.endswith("object_picker_dialog"):
                    dg.add(alias)
                elif base == ROW_CLS and cur_dotted.endswith("object_picker_dialog"):
                    rg.add(alias)
                i = j + 1
                continue
        elif op == "IMPORT_FROM":
            comp = (arg or "").strip().strip("'\"")
            j = i + 1
            alias = None
            if j < n and insns[j]["op"] in ("STORE_NAME", "STORE_GLOBAL"):
                alias = insns[j]["arg"].strip().strip("'\"")
                i = j + 1
            else:
                i += 1
            nm = alias if alias else comp
            if comp == DIALOG_CLS:
                dg.add(nm)
            elif comp == ROW_CLS:
                rg.add(nm)
            continue
        i += 1
    return dg, rg


# ---------------------------------------------------------------------------
# value descriptors
# ---------------------------------------------------------------------------
# Each stack/local value is a tuple descriptor:
#   ("const",)
#   ("var",  name)
#   ("cls",  KIND)             class object (KIND "D"ialog / "R"ow)
#   ("inst", KIND)             instance produced by cls(...)
#   ("attr", obj, attrname)
#   ("meth",  mname)           bound method pending (LOAD_METHOD result)
#   ("call", srcstr)           call result summary
#   ("fnlit", co)              function-literal code object (co carryable)
#   ("unk",)

KIND_D, KIND_R = "D", "R"


def _vstr(v):
    if not isinstance(v, tuple):
        return str(v)
    t = v[0]
    if t == "const":
        return "const"
    if t == "var":
        return v[1]
    if t == "cls":
        return DIALOG_CLS if v[1] == KIND_D else ROW_CLS
    if t == "inst":
        return ("%s_instance" % DIALOG_CLS) if v[1] == KIND_D else \
               ("%s_instance" % ROW_CLS)
    if t == "attr":
        return "%s.%s" % (_vstr(v[1]), v[2])
    if t == "meth":
        return "method:" + v[1]
    if t == "call":
        return v[1]
    if t == "fnlit":
        return "<func:%s>" % getattr(v[1], "co_name", "?")
    if t == "unk":
        return "?"
    return "?"


def _cls_name_src(v):
    """Human tag for a value feeding a row 'name', or None if not obviously
    an animation/DTO text yet (caller then labels it 'other/static')."""
    if not isinstance(v, tuple):
        return None
    t = v[0]
    if t in ("meth", "attr"):
        return None  # caller-level classification on receiver handled separately
    if t == "call":
        return v[1]
    if t == "var":
        return "var:" + v[1]
    if t == "const":
        return "constant"
    if t == "attr":
        return "%s.%s" % (_vstr(v[1]), v[2])
    return None


def _attr_to_source(v):
    """If v is (attr, obj, attrname) where attrname is a DTO/anim text slot,
    return a stable string.  Else None."""
    if isinstance(v, tuple) and v[0] == "attr":
        nm = v[2]
        if nm in ANIM_ACC or nm in DTO_NAME_ATTR:
            recv = v[1]
            if isinstance(recv, tuple) and recv[0] == "inst":
                return "instance." + nm
            return "%s.%s" % (_vstr(recv), nm)
    return None


def _is_dialog_v(v):
    return isinstance(v, tuple) and v[0] in ("cls", "inst") and v[1] == KIND_D


def _is_row_v(v):
    return isinstance(v, tuple) and v[0] in ("cls", "inst") and v[1] == KIND_R


def _as_name_src(v):
    """Best-effort source string for the value passed as a row's name."""
    if isinstance(v, tuple) and v[0] == "attr":
        s = _attr_to_source(v)
        if s:
            return s
        # recursive: obj.attr where obj may itself be a tracked chain
        if isinstance(v[1], tuple) and v[1][0] == "meth":
            return "method-result." + v[2]
        if isinstance(v[1], tuple) and v[1][0] == "call":
            return "call-result." + v[2]
        return _vstr(v)
    if isinstance(v, tuple) and v[0] == "meth":
        # LOAD_METHOD of an accessor; description needs the CALL neighbour
        return v[1]
    return _cls_name_src(v) or _vstr(v)


def _kw_keys(keys_desc):
    """Return ordered keyword names from a ('keys', (..)) descriptor, or [] if the
    value is not a recognised keys tuple."""
    try:
        if isinstance(keys_desc, tuple) and keys_desc and \
                keys_desc[0] == "keys":
            return list(keys_desc[1])
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# receiver-aware single-function analysis
# ---------------------------------------------------------------------------
class _FnRes(object):
    def __init__(self, fn):
        self.fn = fn
        self.excluded = None
        self.dialog_local = None
        self.ctor_line = None
        self.static = []     # [ {"line":, "rec": name-src-desc} ]
        self.dynamic = []    # [ {"line":, "arg0": desc} ]  -> resolved in phase 2
        self.row_ctors = []  # [ {"line":, "name":..., "name_src":...} ]


def analyze_function(fn, dg, rg):
    """Linear, receiver-aware walk of a single function.  Returns _FnRes."""
    res = _FnRes(fn)
    mod = (fn["mod"] or "").lower()
    for ex in EXCLUDE_MOD:
        if ex in mod:
            res.excluded = ex
            return res
    insns = _insns(fn)
    # simple structural pre-check: we must actually reference row/dialog names
    name_txt = " ".join(fn["co"].co_names)
    if not (DIALOG_CLS in name_txt or ROW_CLS in name_txt):
        return res

    stack = []
    var_o = {}

    def push(v):
        stack.append(v)

    def pop():
        return stack.pop() if stack else ("unk",)

    # ---- helpers for capturing arg descriptors at a call boundary ----
    # We walk linearly; because jumps appear once (no unrolling), branches leave the
    # stack coarse, but the local regions around the ctor + the row build that we care
    # about are straight-line enough that descriptors line up.
    for idx, it in enumerate(insns):
        op = it["op"]
        arg = it["arg"].strip().strip("'\"")
        line = it["line"]
        # ---------- loads ----------
        if op in ("LOAD_GLOBAL", "LOAD_NAME"):
            if arg in dg or arg == DIALOG_CLS:
                push(("cls", KIND_D))
            elif arg in rg or arg == ROW_CLS:
                push(("cls", KIND_R))
            else:
                v = var_o.get(arg)
                push(v if v is not None else ("var", arg))
            continue
        if op in ("LOAD_FAST", "LOAD_DEREF"):
            v = var_o.get(arg)
            push(v if v is not None else ("var", arg))
            continue
        if op.startswith("LOAD_CONST"):
            val = it.get("value")
            if hasattr(val, "co_name"):
                push(("fnlit", val))
            elif isinstance(val, tuple) and val and \
                    all(isinstance(x, str) for x in val):
                push(("keys", val))  # keyword-names tuple for a _KW call
            else:
                push(("const",))
            continue
        if op == "LOAD_ATTR":
            obj = pop()
            push(("attr", obj, arg))
            continue
        if op == "LOAD_METHOD":
            recv = pop()
            push(("meth_argless", (recv, arg)))
            continue
        # ---------- direct / method calls (unified, receiver-aware) ----------
        # Lookup rule: in a straight-line region the *callable* is the bottom-most
        # (lowest stack index) real-callable descriptor still beneath the call, and
        # everything above it up to the operand limit are its arguments.  A trailing
        # CALL_FUNCTION_KW keys-tuple names the keyword order.  No cross-function or
        # cross-module guess is ever made here.
        if op == "CALL_METHOD" or op.startswith("CALL_METHOD"):
            # receiver+method already stacked as a meth_argless marker by LOAD_METHOD
            val = it.get("value")
            if isinstance(val, int):
                n = val
            else:
                try:
                    tail = arg.rsplit("_", 1)[-1]
                    n = int(tail) if tail.isdigit() else 0
                except Exception:
                    n = 0
            mark = None
            for j in range(len(stack) - 1, -1, -1):
                if isinstance(stack[j], tuple) and stack[j] and \
                        stack[j][0] == "meth_argless":
                    mark = j
                    break
            if mark is None:
                if n + 1 <= len(stack):
                    del stack[-(n + 1):]
                push(("unk",))
                continue
            _recv, mname = stack[mark][1]
            argvals = stack[mark + 1: mark + 1 + n]
            del stack[mark: mark + 1 + n]
            push(_render_method(res, _recv, mname, argvals, line))
            continue

        if op in ("CALL_FUNCTION", "CALL_FUNCTION_KW", "CALL_FUNCTION_EX",
                  "CALL_FUNCTION_VAR", "CALL_FUNCTION_VAR_KW"):
            # Find the callable: scan from bottom for a class object / fnlit that is
            # the frame's call target.  Because our decoder only models a coarse linear
            # stack we pick the LAST cls/fnlit before any value constants that follow;
            # in practice each statement pushes exactly one callable.
            k = None
            for j in range(len(stack)):
                if isinstance(stack[j], tuple) and stack[j] and \
                        stack[j][0] in ("cls", "fnlit", "meth_argless"):
                    k = j  # keep the furthest callable (right before its args)
            if k is None:
                # unknown plain call: drop the top callable slot + args coarsely
                push(("unk",))
                continue
            callee = stack[k]
            operands = stack[k + 1:]
            # A CALL_FUNCTION_KW puts a keys-tuple (LOAD_CONST <tuple>) on TOS naming
            # keyword order.  Split it so positionals align to keys when all-kw.
            fromkeys = None
            if op.endswith("KW") and operands and isinstance(operands[-1], tuple) \
                    and operands[-1][0] == "keys":
                fromkeys = list(operands[-1][1])
            if fromkeys and len(operands) - 1 == len(fromkeys):
                # every operand is a kw value in keys order -> keep in keys order so
                # _render_call reads the leading positional-equivalent (usually name)
                argvals = operands[:-1]
            else:
                argvals = operands
            del stack[k:]
            push(_render_call(res, callee, argvals, line))
            continue

        # keyword / varar combined forms rarely appear for the row builders we care
        # about; fall through conservatively (keep the stack balanced enough).
        if op in ("MAKE_FUNCTION", "MAKE_FUNCTION_FILTER_TRACE"):
            # 3.7: MAKE_FUNCTION pops the code object pushed by LOAD_CONST<code>;
            # optional defaults annotate via LOAD_CONST before it. Keep the co as a
            # fnlit so the DYNAMIC second pass can resolve its real code object.
            # The co already sits on the stack (from LOAD_CONST argval) as fnlit, so
            # we leave it in place; if a plain const was on top, replace it.
            if stack and stack[-1] == ("const",):
                stack[-1] = ("fnlit", fn["co"])
            continue
        # ---------- stores ----------
        if op in ("STORE_FAST", "STORE_NAME"):
            if stack:
                v = pop()
                var_o[arg] = v
                if _is_dialog_v(v) and not res.dialog_local:
                    res.dialog_local = arg
                    res.ctor_line = line
            continue
        if op == "STORE_DEREF":
            if stack:
                var_o[arg] = pop()
                continue
        if op == "STORE_ATTR":
            if len(stack) >= 2:
                val = pop()
                obj = pop()
                if _is_row_v(obj) and arg in DTO_NAME_ATTR:
                    res.row_ctors.append({"line": line, "name": arg,
                                          "src": _as_name_src(val)})
                elif _is_dialog_v(obj):
                    pass  # dialog.rows append etc.
            continue
        if op in ("POP_TOP",):
            if stack:
                stack.pop()
            continue
        if op in ("DUP_TOP",):
            if stack:
                stack.append(stack[-1])
            continue
        if op == "DUP_TOP_TWO" and len(stack) >= 2:
            stack.append(stack[-2]); stack.append(stack[-1])
        # other opcodes: compare/jumps/iteration/build_* -> we let the stack alone
        # (coarse linear), guarded above against over-pop.
    return res


def _render_call(res, callee, rest, line):
    """Direct TurboObjectPickerRow(...) / TurboObjectPickerDialog(...) module call."""
    if isinstance(callee, tuple):
        if callee[0] == "cls" and callee[1] == KIND_D:
            return ("inst", KIND_D)
        if callee[0] == "cls" and callee[1] == KIND_R:
            # TurboObjectPickerRow(...) positional: name first among remaining args
            nm = None
            if rest:
                nm = _as_name_src(rest[0])
            res.row_ctors.append({"line": line, "name": "name",
                                  "src": nm or "constant/unknown"})
            return ("inst", KIND_R)
        if callee[0] == "meth_argless":
            # got a LOAD_METHOD consumed as part of CALL_FUNCTION (unusual) -> no-op
            return ("unk",)
    return ("unk",)


def _render_method(res, recv, mname, argvals, line):
    """Instance-method call bound to a tracked receiver (receiver-aware)."""
    if not _is_dialog_v(recv):
        # not proven to be a turbolib dialog -> do NOT create an edge.  This is the
        # explicit receiver-awareness gate that kills the old false positives.
        return ("unk",)
    if mname == "create_picker_row":
        nm = _as_name_src(argvals[0]) if argvals else None
        res.static.append({"line": line, "name": nm or "constant/unknown",
                           "name_src": nm or "other/static"})
    elif mname in ("add_picker_row",):
        nm = _as_name_src(argvals[0]) if argvals else None
        res.static.append({"line": line, "name": nm or "ignored"})
    elif mname == "set_dynamic_picker_rows_func":
        # arg0 should be a function reference / literal; record its descriptor so a
        # second pass can resolve its real code object.
        arg0 = argvals[0] if argvals else ("unk",)
        res.dynamic.append({"line": line, "arg0": arg0,
                            "name_src": _as_name_src(arg0)})
    return ("unk",)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def _main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--dir", required=True)
    ap.add_argument("--module-suffix", default=ANCHOR_MODULE)
    ap.add_argument("--out", default=None, help="default output/ww_p29d/<DEFAULT_OUT>")
    a = ap.parse_args(argv)

    if not XDIS:
        print("ERROR: missing xdis -- pip install xdis", file=sys.stderr)
        return 6
    src = Path(a.source)
    if not src.is_file():
        print("ERROR: source not found %s" % src, file=sys.stderr)
        return 2
    modsdir = Path(a.dir)
    if not modsdir.is_dir():
        print("ERROR: --dir not found %s" % modsdir, file=sys.stderr)
        return 3
    out_path = Path(a.out) if a.out else (Path("output/ww_p29d") / DEFAULT_OUT)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    anchor = a.module_suffix.strip(".")

    pkgs = sorted(modsdir.rglob("*.ts4script"))
    primary = [p for p in pkgs if PKG_HINT in p.name]
    if not primary:
        primary = [p for p in pkgs if "wickedwhims" in p.name.lower() or
                   "turbodriver" in p.name.lower() or "ww" in p.name.lower()]
    if not primary:
        print("ERROR: no WW package under %s (hint: %s)" % (modsdir, PKG_HINT),
              file=sys.stderr)
        return 4
    target = primary[0]

    def _has_anchor(p):
        try:
            with zipfile.ZipFile(str(p)) as z:
                for nn in z.namelist():
                    if nn.endswith(".pyc") and _rel_dotted(nn).endswith(anchor):
                        return True
        except Exception:
            return False
        return False

    load_set = {target}
    for p in pkgs:
        if p != target and _has_anchor(p):
            load_set.add(p)
    load_set = sorted(load_set, key=str)

    members = []
    for p in load_set:
        with zipfile.ZipFile(str(p)) as z:
            for nn in z.namelist():
                if nn.endswith(".pyc"):
                    members.append((p.name, nn, z.read(nn)))
    if not members:
        print("ERROR: no .pyc members found under load_set", file=sys.stderr)
        return 7

    funcs = []
    module_code = {}
    parse_err = 0
    for pkgname, mn, data in members:
        try:
            opc, codes, code_top = parse_member(mn, data)
        except Exception:
            parse_err += 1
            continue
        dotted = _rel_dotted(mn)
        for c in codes:
            funcs.append({"pkg": pkgname, "mod": dotted, "member": mn,
                          "co": c, "name": c.co_name,
                          "line": c.co_firstlineno, "opc": opc})
        module_code.setdefault(dotted, code_top)

    # alias env per module (once each)
    alias_env = {}
    for dotted, topco in module_code.items():
        mf = funcs and next((x for x in funcs if x["mod"] == dotted and
                             x["co"] is topco and x["name"] == "<module>"), None)
        if mf is None:
            dg, rg = set(), set()
            if DIALOG_CLS in " ".join(topco.co_names):
                dg.add(DIALOG_CLS)
            if ROW_CLS in " ".join(topco.co_names):
                rg.add(ROW_CLS)
            alias_env[dotted] = (dg, rg)
        else:
            alias_env[dotted] = module_alias_env(mf)

    # phase 1: receiver-aware scan
    results = []
    seen = set()
    for f in funcs:
        if f["name"] == "<module>":
            continue
        if id(f["co"]) in seen:
            continue
        seen.add(id(f["co"]))
        dg, rg = alias_env.get(f["mod"], ({DIALOG_CLS}, {ROW_CLS}))
        if not dg:
            dg = {DIALOG_CLS}
        if not rg:
            rg = {ROW_CLS}
        r = analyze_function(f, dg, rg)
        if r.excluded:
            continue
        if r.dialog_local or r.static or r.dynamic or r.row_ctors:
            results.append((f, r))

    if not results:
        print("ERROR: no receiver-aware dialog/row activity", file=sys.stderr)
        return 5

    # module relevance filter (words appearing in dotted module path)
    def _relevant(f):
        m = (f["mod"] or "").lower()
        return any(t in m for t in KEEP_MOD)

    results = [x for x in results if _relevant(x[0]) or "picker" in
               (x[0]["mod"] or "").lower() or ".dialog" in (x[0]["mod"] or "").lower()]

    # function-name exclusions
    results = [x for x in results if not any(t in (x[0]["name"] or "").lower()
                                             for t in EXCLUDE_FN)]

    # -------------------------------------------------------------------
    # PHASE 2 - DYNAMIC func resolution (receiver-local only, NO cross-module
    # name-only BFS).  For a set_dynamic_picker_rows_func(func) call we resolve the
    # real code object that builds the returned rows:
    #   * arg0 == ("fnlit", co) -> inline function literal: find the func record
    #                              whose .co IS co (same module / its own body)
    #   * arg0 == ("var", name) -> module-global or inner function reference;
    #                              resolved against the SAME module only (exactly one
    #                              top-level func of that name), else boundary.
    # Then re-scan that func body receiver-aware to find TurboObjectPickerRow(...)
    # constructions / create_picker_row(...) and the resulting row-name source.
    # Any failure or ambiguity -> DYNAMIC_BOUNDARY with a reason (we never guess).
    # -------------------------------------------------------------------

    # module -> {funcname -> [fn records with that exact top-level/inner name]}
    func_index = {}
    for f in funcs:
        if f["name"] == "<module>":
            continue
        func_index.setdefault(f["mod"], {}).setdefault(f["name"], []).append(f)

    def _resolve_co_to_fn(co):
        """map a code object back to a func record (any module; identity match)."""
        for f in funcs:
            if f["co"] is co:
                return f
        return None

    def _resolve_var_func(mod, name):
        """resolve a same-module func reference by name; only when unambiguous."""
        cand = func_index.get(mod, {}).get(name)
        if not cand:
            return None
        if len(cand) == 1:
            return cand[0]
        return None  # ambiguous in-module name -> boundary

    # global summary accumulators (filled from candidates after per-DFS resolve)
    summary = {"cand": 0,
               "entry": None,
               "producer": None,
               "mode": "NONE",
               "name_src": None,
               "conf": "NONE",
               "dyn_boundary": None}

    def _classify_row_name_src(rc):
        """Normalise a DIRECT_ROW_* name source captured by the interpreter into a
        machine-stable tag + confidence."""
        s = (rc.get("src") or "").lower()
        if "get_display_name" in s:
            return "animation_instance.get_display_name()", "HIGH"
        if "display_name" in s:
            return "animation_instance.display_name", "HIGH"
        if "get_stage_name" in s:
            return "animation_instance.get_stage_name()", "HIGH"
        if "animation_stage_name" in s:
            return "animation_instance.animation_stage_name", "HIGH"
        if "get_animation_name" in s or "animation_name" in s:
            return "animation_instance.(get_)animation_name", "HIGH"
        if "get_name" in s or ".name" in s or s.startswith("instance.") or \
                s.split(".")[-1] in DTO_NAME_ATTR:
            return "dto/instance.name", "MEDIUM"
        if "get_title" in s or ".title" in s:
            return "dto/instance.title", "MEDIUM"
        if "constant" in s or s in ("other/static", "unknown", "?", ""):
            return "other/static-or-unknown", "LOW"
        return "other", "LOW"

    def _analyse_target_func(tfn, seen_stack):
        """Receiver-aware rescan of a DYNAMIC target func; returns list of row-name
        source strings found (from direct row ctor / create_picker_row) plus the row
        constructor descriptor.  visited guards against recursion cycles."""
        if id(tfn["co"]) in seen_stack:
            return [], "<cycle>"
        seen_stack.add(id(tfn["co"]))
        dg, rg = alias_env.get(tfn["mod"], ({DIALOG_CLS}, {ROW_CLS}))
        r2 = analyze_function(tfn, dg or {DIALOG_CLS}, rg or {ROW_CLS})
        out_src = []
        ctor = None
        for rc in r2.row_ctors:
            out_src.append(rc.get("src") or "other/static")
            if ctor is None:
                ctor = "TurboObjectPickerRow(...) L%d" % rc["line"]
        for st in r2.static:
            out_src.append(st.get("name_src") or "other/static")
            if ctor is None:
                ctor = ("TurboObjectPickerDialog.create_picker_row(...) L%d"
                        % st["line"])
        # also chase nested dynamic funcs inside the target (depth-bounded chain)
        if len(seen_stack) <= 8:
            for dd in r2.dynamic:
                inner = None
                inner_argsrc = dd.get("arg0")
                if isinstance(inner_argsrc, tuple):
                    if inner_argsrc[0] == "fnlit":
                        inner = _resolve_co_to_fn(inner_argsrc[1])
                    elif inner_argsrc[0] == "var":
                        inner = _resolve_var_func(tfn["mod"], inner_argsrc[1])
                if inner is not None:
                    sub_src, _sc = _analyse_target_func(inner, seen_stack)
                    out_src.extend(sub_src)
        seen_stack.discard(id(tfn["co"]))
        return out_src, ctor

    any_receiver_bound = False
    for (f, r) in results:
        has_receiver_bound = bool(r.dialog_local or r.static or r.dynamic)
        if has_receiver_bound:
            any_receiver_bound = True
        for dc in r.dynamic:
            a0 = dc.get("arg0")
            dc["dyn_boundary"] = None
            dc["dyn_func"] = None
            dc["row_ctor"] = None
            dc["row_src"] = None
            dc["conf"] = None
            target_fn = None
            if isinstance(a0, tuple) and a0:
                if a0[0] == "fnlit":
                    target_fn = _resolve_co_to_fn(a0[1])
                elif a0[0] == "var":
                    target_fn = _resolve_var_func(f["mod"], a0[1])
            if target_fn is None:
                if isinstance(a0, tuple) and a0[0] == "var":
                    dc["dyn_boundary"] = ("func '%s' not resolvable statically in "
                                          "module %s (ambiguous or not a module-global)"
                                          % (a0[1], f["mod"]))
                elif isinstance(a0, tuple) and a0[0] == "fnlit":
                    dc["dyn_boundary"] = (
                        "inline func-literal at L%d has no matching code object in "
                        "loaded modules (closure/opaque origin)" % dc["line"])
                else:
                    dc["dyn_boundary"] = ("func argument not a statically bound "
                                          "reference (descriptor %s)" % _vstr(a0))
                continue
            dc["dyn_func"] = "%s@%s[%s]" % (target_fn["name"], target_fn["mod"],
                                          target_fn["pkg"])
            rowsrcs, ctor = _analyse_target_func(target_fn, set())
            if rowsrcs:
                dc["row_src"] = rowsrcs[0]
            dc["row_ctor"] = ctor
            tag, conf = _classify_row_name_src({"src": dc.get("row_src")})
            dc["conf"] = conf
            dc["row_src"] = dc.get("row_src") or tag

        # ---- fold this candidate into the global summary ----
        # Only receiver-bound candidates (a dialog was really constructed and driven
        # here) may claim the picker ENTRYPOINT / PRODUCER.  Standalone row-builder
        # bodies (r.row_ctors only, no dialog local) are supporting evidence and do
        # NOT set mode/entry/producer so they cannot shadow the real entry.
        if not has_receiver_bound and not (r.static or r.dynamic or r.row_ctors):
            continue
        summary["cand"] += 1
        if r.dynamic or r.static or r.dialog_local:
            if summary["mode"] == "NONE":
                summary["mode"] = "DYNAMIC" if (r.dynamic or r.row_ctors) else "STATIC"
        if summary["producer"] is None and has_receiver_bound:
            # record the row-name evidence from whichever producer is present
            if r.dynamic:
                dc = r.dynamic[0]
                summary["producer"] = ("%s@%s L%d (set_dynamic_picker_rows_func -> "
                                        "%s)" % (f["name"], f["mod"], dc["line"],
                                                 (dc.get("dyn_func") or "<unresolved>")))
                summary["mode"] = "DYNAMIC"
                summary["entry"] = ("%s@%s L%d"
                                     % (f["name"], f["mod"], f["line"]))
                if dc.get("row_src"):
                    summary["name_src"] = dc["row_src"]
                    summary["conf"] = dc.get("conf") or "DYNAMIC_MEDIUM"
                elif dc.get("dyn_boundary"):
                    summary["name_src"] = "unresolved"
                    summary["conf"] = "DYNAMIC_BOUNDARY"
                    summary["dyn_boundary"] = dc["dyn_boundary"]
            elif r.static:
                st = r.static[0]
                summary["mode"] = "STATIC"
                summary["name_src"] = st.get("name_src")
                summary["conf"] = "STATIC_MEDIUM"
                summary["producer"] = ("%s@%s L%d (create_picker_row)"
                                        % (f["name"], f["mod"], st["line"]))
                summary["entry"] = "%s@%s L%d" % (f["name"], f["mod"], f["line"])
            elif r.dialog_local and r.row_ctors:
                summary["producer"] = "%s@%s L%d (add_picker_row)" % (
                    f["name"], f["mod"], f["line"])
                summary["entry"] = "%s@%s L%d" % (f["name"], f["mod"], f["line"])
                if r.row_ctors:
                    rc = r.row_ctors[0]
                    summary["name_src"] = rc.get("src")
                    summary["conf"] = "STATIC_MEDIUM"

    # If a real dialog was constructed/driven somewhere but no receiver-bound
    # producer (STATIC create_picker_row / DYNAMIC settler) could be resolved to a
    # row-name, emit an explicit DYNAMIC_BOUNDARY instead of a silent UNRESOLVED.
    if summary["producer"] is None:
        if any_receiver_bound:
            summary["conf"] = "UNRESOLVED_STATICALLY"
            summary["mode"] = "STATIC" if not summary["dyn_boundary"] else "DYNAMIC"
            summary["dyn_boundary"] = (summary["dyn_boundary"] or
                "receiver-bound dialog found but no row-name producer resolvable "
                "statically from the analysed bodies (rows likely filled inside the "
                "turbolib generic sink / a non-local closure)")

    # ------------------------------------------------------------------ output
    L = []
    L.append("=== P29-D ROW-PRODUCER TRACE (RECEIVER-AWARE, READ-ONLY) ===")
    L.append("package(s)=%s" % ",".join(p.name for p in load_set))
    L.append("members=%d parse_errors=%d functions=%d" %
             (len(members), parse_err, len(funcs)))
    L.append("anchor_module=" + anchor)
    L.append("dialog_ctor=" + DIALOG_CLS)
    L.append("row_ctor=" + ROW_CLS)

    def _evidence(f, line):
        ins = _insns(f)
        byline = {}
        for i in ins:
            byline.setdefault(i["line"], []).append(i["op"] + " " + i["arg"])
        ls = sorted(byline.keys())
        if line not in byline and ls:
            line = min(ls, key=lambda l: abs(l - line))
        if line not in byline:
            return []
        # take up to 2 surrounding source lines each way
        try:
            pos = ls.index(line)
        except ValueError:
            pos = min(range(len(ls)), key=lambda k: abs(ls[k] - line))
        sel = ls[max(0, pos - 2): pos + 3]
        out = []
        for l in sel:
            for opa in byline[l]:
                out.append("  L%-5d %s" % (l, opa))
        return out[:28]

    cnt = 0
    for (f, r) in sorted(results, key=lambda t: (t[0]["mod"], t[0]["line"])):
        cnt += 1
        L.append("")
        L.append("--- candidate %d ---" % cnt)
        L.append("MODULE=%s[%s]" % (f["mod"], f["pkg"]))
        L.append("FUNCTION=%s" % f["name"])
        L.append("LINE=%d" % f["line"])
        L.append("DIALOG_LOCAL=%s" % (r.dialog_local if r.dialog_local else
                                      "(none in-body / dynamic)"))
        L.append("DIALOG_CONSTRUCTOR_LINE=%s" %
                 (r.ctor_line if r.ctor_line else "-"))
        if r.static:
            L.append("ROW_SOURCE_MODE=STATIC")
            for s in r.static:
                L.append("  CREATE_PICKER_ROW_LINE=%s" % s["line"])
                L.append("  NAME_ARG_SOURCE=%s" % s.get("name_src", "other/static"))
                L.append("  DESCRIPTION_ARG_SOURCE=other/static(check adjacent)")
                L.append("  TAG_ARG_SOURCE=constant/field(check adjacent)")
                L.append("  BYTECODE_EVIDENCE:")
                L += _evidence(f, s["line"])
        if r.dynamic:
            L.append("ROW_SOURCE_MODE=DYNAMIC")
            for d in r.dynamic:
                L.append("  SET_DYNAMIC_FUNC_LINE=%s" % d["line"])
                L.append("  DYNAMIC_FUNC_ARG=%s" % d["name_src"])
                if d.get("dyn_func"):
                    L.append("  DYNAMIC_FUNC=%s" % d["dyn_func"])
                    L.append("  DYNAMIC_ROW_CONSTRUCTOR=%s"
                             % (d.get("row_ctor") or "not-found-in-body"))
                    L.append("  DYNAMIC_ROW_NAME_SOURCE=%s"
                             % (d.get("row_src") or "(none captured)"))
                    L.append("  DYNAMIC_ROW_NAME_CONFIDENCE=%s"
                             % (d.get("conf") or "LOW"))
                if d.get("dyn_boundary"):
                    L.append("  DYNAMIC_BOUNDARY=%s" % d["dyn_boundary"])
                L.append("  BYTECODE_EVIDENCE:")
                L += _evidence(f, d["line"])
                if d.get("dyn_func"):
                    # the row-construction evidence actually lives in the body of the
                    # resolved DYNAMIC_FUNC; the bytecode block above is the setter
                    # call site. The resolved func's own row source is reported in
                    # DYNAMIC_ROW_NAME_SOURCE / DYNAMIC_ROW_CONSTRUCTOR.
                    L.append("  -- (no guess: row-construction is INSIDE the "
                              "resolved DYNAMIC_FUNC, see its fields)")
        if r.row_ctors and not (r.static or r.dynamic):
            L.append("ROW_SOURCE_MODE=DYNAMIC(returns prebuilt rows)")
        for rc in r.row_ctors:
            L.append("  DIRECT_ROW_CTOR_LINE=%s" % rc["line"])
            L.append("  DIRECT_ROW_NAME_ATTR=%s" % rc.get("name", "name"))
            L.append("  DIRECT_ROW_NAME_SOURCE=%s" % rc.get("src", "other/static"))

    L.append("")
    L.append("CANDIDATES=%d" % cnt)
    L.append("TARGET_ANIMATION_PICKER_CANDIDATES=%d" % cnt)
    L.append("TARGET_PICKER_ENTRYPOINT=%s" % (summary["entry"] or "NONE"))
    L.append("ROW_PRODUCER_FUNCTION=%s" % (summary["producer"] or "UNRESOLVED"))
    L.append("ROW_PRODUCER_MODE=%s" % summary["mode"])
    L.append("ROW_NAME_SOURCE=%s" % (summary["name_src"] or "UNKNOWN"))
    L.append("ROW_NAME_SOURCE_CONFIDENCE=%s" % summary["conf"])
    L.append("DYNAMIC_BOUNDARY=%s" % (summary["dyn_boundary"] or "none"))
    L.append("ZERO_WRITE_TO_MODS=YES")

    text = "\n".join(L)
    try:
        out_path.write_text(text, encoding="utf-8")
    except Exception as ex:
        print("WARN: write failed %s (%s)" % (out_path, ex), file=sys.stderr)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
