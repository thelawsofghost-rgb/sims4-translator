#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29d_instance_picker_row_dump.py -- P29-D SexAnimationInstance METHOD DISASM
                                        (READ-ONLY, NO ANALYSIS / NO GUESSING)

CONTEXT / WHY THIS SCOPE
-----------------------
Dorothy's real-machine focus_dump reached a DECISIVE conclusion:

  1. Concrete per-animation rows are NOT built directly in the sex_animation dialog.
     Both entry points funnel to:
         animation_instance.get_picker_row(
             index=index, icon_override=icon_override, from_context=...)
         -> animation_picker_row
         -> append
  2. The TurboObjectPickerRow(...) calls visible inside the dialog bodies are the
     AUTHOR-GROUP rows:
         TurboObjectPickerRow(identifier=author_id, name=author_name,
                              description=<localized animations_count>,
                              icon=get_arrow_icon(), tag=author_id)
     i.e. NOT per-animation rows.
  3. TurboObjectPickerRow.__init__ confirmed:
         (self, identifier, name, description, ...)
         name -> l18n.get_localized_string(name) -> self.name

Therefore the REAL per-animation text producer lives inside
        SexAnimationInstance.get_picker_row
(and its accessors get_display_name / get_stage_name), and the dialog-level
DIRECT_ROW_NAME_SOURCE=display_authors is a FALSE POSITIVE.

THIS SCRIPT (per Dorothy)
  * targets ONLY the class SexAnimationInstance in module
        wickedwhims.sex.animations.animation_instance
  * fully disassembles these methods (no +/- N-instruction window):
        1. get_picker_row
        2. get_display_name
        3. get_stage_name
  * prints, for each, its REAL object metadata: co_varnames / co_names / co_consts /
    co_argcount / co_kwonlyargcount (+ co_kwonlyargcount when present) / co_flags,
    FULL bytecode, and every nested listcomp/lambda/genexpr reachable from the body.
  * does NOT auto-guess answers.  It only *marks* occurrences of:
        TurboObjectPickerRow
        get_display_name
        display_name
        display_name_override
        get_stage_name
        animation_stage_name
        get_identifier
        get_author
        get_animation_id
  * for the row construction inside get_picker_row it must keep the ENTIRE span from
    a LOAD_GLOBAL TurboObjectPickerRow up to the CALL_FUNCTION / CALL_FUNCTION_KW /
    CALL_FUNCTION_EX that CONSUMES that class, without accidentally treating a nested
    helper CALL_METHOD (a method call on an intermediate value) as the row ctor call.

The goal is to let a human hand-reconstruct, by CPython-3.7 stack discipline, the real
constructor:      TurboObjectPickerRow(identifier=???, name=???, description=???, ...)

OUTPUT
------
output/ww_p29d/ww_p29d_instance_picker_row_dump.txt   (only when the user actually runs it)

ZERO_WRITE_TO_MODS=YES.  No runtime hook.  No Mods/XML/TEST300/stage_name touch.
Exit fail-closed: 2 source | 3 --dir | 4 no WW pkg | 6 no xdis | 7 module member missing.
NOT committed / NOT pushed unless the user says so.
"""

import argparse
import io as _io
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
DEFAULT_OUT = "ww_p29d_instance_picker_row_dump.txt"

TARGET_MODULE = "wickedwhims.sex.animations.animation_instance"
TARGET_CLASS = "SexAnimationInstance"
TARGET_METHODS = ("get_picker_row", "get_display_name", "get_stage_name")

# tokens to highlight but never to interpret automatically
WATCH = ("TurboObjectPickerRow", "get_display_name", "display_name",
         "display_name_override", "get_stage_name", "animation_stage_name",
         "get_identifier", "get_author", "get_animation_id")

# opcodes that consume the top callable pushed by LOAD_GLOBAL TurboObjectPickerRow
ROW_CALL_OPS = ("CALL_FUNCTION", "CALL_FUNCTION_KW", "CALL_FUNCTION_EX",
                "CALL_FUNCTION_VAR", "CALL_FUNCTION_VAR_KW")


def _rel_dotted(rel):
    base = rel[:-4].replace("/", ".") if rel.endswith(".pyc") else rel
    return base.strip(".")


def _direct_children(co):
    out = []
    for c in co.co_consts:
        if hasattr(c, "co_name"):
            out.append(c)
    return out


def _all_codes(top):
    acc = []
    stack = [top]
    while stack:
        cur = stack.pop()
        acc.append(cur)
        for c in _direct_children(cur):
            stack.append(c)
    return acc


def _load_member(data):
    modobj = load_module_from_file_object(_io.BytesIO(data), filename="m.pyc")
    ver = modobj[0]
    co = modobj[3]
    try:
        vstr = tuple(str(x) for x in ver[:2])
    except Exception:
        vstr = ("3", "7")
    opc = get_opcode_module(vstr, PythonImplementation.CPython)
    return opc, co


def _co_flags(co):
    f = getattr(co, "co_flags", 0)
    bits = []
    if f & 0x02:
        bits.append("CO_NESTED")
    if f & 0x04:
        bits.append("CO_GENERATOR")
    if f & 0x08:
        bits.append("CO_NOFREE")
    if f & 0x20:
        bits.append("CO_COROUTINE")
    if f & 0x40:
        bits.append("CO_VARARGS")
    if f & 0x80:
        bits.append("CO_VARKEYWORDS")
    if f & 0x100:
        bits.append("CO_ASYNC_GENERATOR")
    if getattr(co, "co_posonlyargcount", 0):
        bits.append("POSONLY=%d" % co.co_posonlyargcount)
    return "|".join(bits) if bits else "0x%x" % f


def _is_call(op):
    return op in ROW_CALL_OPS or op == "CALL_METHOD"


def _argval_note(op, av):
    if hasattr(av, "co_name"):
        return "  <nested code: %s @L%s>" % (av.co_name,
                                             getattr(av, "co_firstlineno", "?"))
    if isinstance(av, tuple) and av and op == "LOAD_CONST" and \
            all(isinstance(x, str) for x in av):
        return "  <keys/signature tuple: %s>" % ", ".join(av)
    return ""


def _disasm(co, opc):
    """Return list of dict-ish lines for one body, WITH row-ctor frame marking.

    Marking rule (evidence only, matched by a real candidate stack frame):
      * a LOAD_GLOBAL that resolves to 'TurboObjectPickerRow' is a candidate
        callable push.
      * the *first* subsequently seen call opcode (CALL_FUNCTION*/CALL_METHOD)
        that can legally be a top-level row ctor is then paired.  Because a class
        object itself is only ever the callee of a CALL_FUNCTION* (not the receiver
        of a CALL_METHOD), we only treat CALL_FUNCTION* as the frame closer and we
        skip intermediate CALL_METHOD occurrences that clearly belong to argument
        expressions evaluated BEFORE the class is consumed.  When we close on a
        CALL_FUNCTION*, everything emitted since the LOAD_GLOBAL is (verbatim) the
        row-construction argument span.
    """
    lines = []  # each: (lineno, offset, op, argtext, marker, watch_hit)
    pending_ctor = False
    for it in Bytecode(co, opc):
        op = it.opname
        off = getattr(it, "offset", None)
        ln = getattr(it, "lineno", None)
        arg = it.argrepr or ""
        av = getattr(it, "argval", None)
        is_row_push = (op == "LOAD_GLOBAL" and arg == "TurboObjectPickerRow")
        is_call = _is_call(op)

        if is_row_push:
            pending_ctor = True
            lines.append([ln, off, op, (arg or "-"), "CTOR_OPEN", True])
            continue
        if pending_ctor:
            if op in ROW_CALL_OPS:
                lines.append([ln, off, op, (arg or "-"), "CTOR_CALL", False])
                pending_ctor = False
                continue
            if is_call and op == "CALL_METHOD":
                # intermediate method call INSIDE an argument expression: keep it in
                # the span but do NOT close the row frame.
                lines.append([ln, off, op, (arg or "-"), "CTOR_SPAN", False])
                continue
            lines.append([ln, off, op, (arg or "-"), "CTOR_SPAN", False])
            continue
        hit = any(w in (arg or "") for w in WATCH) or \
            any(w in (op or "") for w in WATCH)
        lines.append([ln, off, op, (arg or "-"), "", hit])
    return lines


def _const_list(co):
    out = []
    for c in co.co_consts:
        if hasattr(c, "co_name"):
            out.append("CODE:%s@L%s" % (c.co_name, getattr(c, "co_firstlineno", "?")))
        else:
            r = repr(c)
            out.append(r if len(r) <= 90 else r[:90] + "...")
    return out


def _sign(co):
    L = []
    L.append("    co_argcount        = %s" % getattr(co, "co_argcount", "?"))
    if hasattr(co, "co_posonlyargcount"):
        L.append("    co_posonlyargcount = %s" % co.co_posonlyargcount)
    L.append("    co_kwonlyargcount  = %s" % getattr(co, "co_kwonlyargcount", "?"))
    L.append("    co_nlocals         = %s" % getattr(co, "co_nlocals", "?"))
    L.append("    co_stacksize       = %s" % getattr(co, "co_stacksize", "?"))
    L.append("    co_flags           = %s" % _co_flags(co))
    L.append("    co_varnames        = %s" % ", ".join(co.co_varnames))
    L.append("    co_names           = %s" % ", ".join(co.co_names))
    L.append("    co_consts          = %s" % (", ".join(_const_list(co)) or "(empty)"))
    L.append("    co_freevars        = %s" % (", ".join(getattr(co, "co_freevars", ())) or "-"))
    L.append("    co_cellvars        = %s" % (", ".join(getattr(co, "co_cellvars", ())) or "-"))
    return L


def _fmt_row(r, indent):
    ln, off, op, arg, marker, hit = r
    extra = ""
    if marker == "CTOR_OPEN":
        extra = "   <== LOAD TurboObjectPickerRow (ROW CLASS PUSH)"
    elif marker == "CTOR_CALL":
        extra = "   <== ROW CONSTRUCTOR CALL consumes the class"
    elif marker == "CTOR_SPAN":
        extra = "   <== [row-argument span]"
    if hit and extra == "":
        extra = "   <== watch token"
    return "%s   L%-6d O%-6d %-20s %s%s" % (indent, ln or 0, off or 0, op,
                                            (arg or "-"), extra)


def _emit_body(L, co, opc):
    L.extend(_sign(co))
    L.append("    --- bytecode (FULL) ---")
    for r in _disasm(co, opc):
        L.append(_fmt_row(r, "    "))

    kids = _direct_children(co)
    L.append("")
    L.append("    --- nested code objects reachable from this body: %d ---" % len(kids))
    for k in kids:
        L.append("")
        L.append("    [nested] %s @L%s (flags=%s)" %
                 (k.co_name, getattr(k, "co_firstlineno", "?"), _co_flags(k)))
        for s in _sign(k):
            L.append("        " + s)
        L.append("        --- nested bytecode (FULL) ---")
        subopc = opc
        for r in _disasm(k, subopc):
            L.append(_fmt_row(r, "            "))
        for kk in _direct_children(k):
            L.append("            [nested2] %s @L%s" %
                     (kk.co_name, getattr(kk, "co_firstlineno", "?")))
            for r in _disasm(kk, opc):
                L.append(_fmt_row(r, "                "))


def _main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", default=None)
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
    out_path = Path(a.out) if a.out else (OUT_DIR / DEFAULT_OUT)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pkgs = sorted(modsdir.rglob("*.ts4script"))
    primary = [p for p in pkgs if PKG_HINT in p.name]
    if not primary:
        primary = [p for p in pkgs if "wickedwhims" in p.name.lower() or
                   "turbodriver" in p.name.lower() or "ww" in p.name.lower()]
    if not primary:
        print("ERROR: no WW package under %s (hint: %s)" % (modsdir, PKG_HINT),
              file=sys.stderr)
        return 4

    # locate the module member carrying SexAnimationInstance
    member = None      # (pkg, dotted, data)
    opc = None
    top = None
    matched_obj = None
    for p in pkgs:
        try:
            with zipfile.ZipFile(str(p)) as z:
                for nn in z.namelist():
                    if not nn.endswith(".pyc"):
                        continue
                    if _rel_dotted(nn) != TARGET_MODULE:
                        continue
                    data = z.read(nn)
                    member = (p.name, nn, data)
                    opc, top = _load_member(data)
                    if top is not None:
                        matched_obj = top
                    break
        except Exception:
            continue
        if member:
            break

    L = []
    L.append("=== P29-D SexAnimationInstance METHOD DISASM (READ-ONLY, NO ANALYSIS) ===")
    L.append("ZERO_WRITE_TO_MODS=YES")
    L.append("target_module = " + TARGET_MODULE)
    L.append("target_class  = " + TARGET_CLASS)
    L.append("target_methods= " + ", ".join(TARGET_METHODS))
    L.append("watch_tokens  = " + ", ".join(WATCH))
    L.append("")

    if member is None:
        L.append("NOT FOUND: no .pyc member whose dotted path == %s" % TARGET_MODULE)
    else:
        pkg, nn, _ = member
        L.append("member=%s  (module=%s)" % (nn, TARGET_MODULE))
        L.append("")
        # gather class body -> its method code objects
        class_co = None
        methods = {}  # method co_name -> co
        for co in _all_codes(top):
            if co.co_name == TARGET_CLASS:
                class_co = co
                for m in TARGET_METHODS:
                    for sub in _direct_children(co):
                        if sub.co_name == m:
                            methods.setdefault(m, []).append(sub)
        if class_co is None:
            L.append("NOT FOUND: class %s absent in %s" % (TARGET_CLASS, TARGET_MODULE))
        else:
            L.append("class %s @L%s (pkg=%s)" %
                     (TARGET_CLASS, getattr(class_co, "co_firstlineno", "?"), pkg))
            for m in TARGET_METHODS:
                L.append("")
                L.append("=" * 74)
                cands = methods.get(m)
                if not cands:
                    L.append("METHOD %s NOT FOUND as a direct child of class %s" %
                             (m, TARGET_CLASS))
                    continue
                for co in cands:
                    L.append("")
                    L.append("### %s : %s @L%s  (%s)" %
                             (TARGET_CLASS, m, getattr(co, "co_firstlineno", "?"),
                              pkg))
                    _emit_body(L, co, opc)

    L.append("")
    L.append("ZERO_WRITE_TO_MODS=YES")

    out_path.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("WROTE %s" % out_path, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
