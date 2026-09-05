#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29d_focus_dump.py -- P29-D FOCUSED DISASSEMBLY (READ-ONLY, NO ANALYSIS)

SCOPE
-----
The whole-package row-producer trace (ww_p29d_row_producer_trace.py) is EVIDENCE
ONLY.  Its 42-candidate filter header is NOT used as a conclusion
(TARGET_PICKER_ENTRYPOINT=turbolib2.services.dialog_service.display_objects_picker_dialog
is a wrong generic fallback).  The two highest-priority real candidates are:

  A) wickedwhims.sex.integral.dialogs.sex_animation
     open_change_sex_animations_picker_dialog            (line ~108)
  B) wickedwhims.sex.integral.dialogs.sex_init_player
     open_start_sex_animations_picker_dialog             (line ~370)

DIRECT_ROW_NAME_SOURCE=var:display_authors was NOT accepted as a conclusion: the
row-producer trace did not emit the complete stack/bytecode around the
TurboObjectPickerRow(...) call, so we cannot yet prove display_authors binds the
`name` slot of TurboObjectPickerRow.__init__.

THIS SCRIPT (per Dorothy):
  * does ONLY focused disassembly - NO new analysis framework,
  * does NOT modify ww_p29d_row_producer_trace.py,
  * does NOT whole-package BFS,
  * does NOT design hooks,
  * does NOT touch Mods/XML/TEST300/stage_name,
  * does NOT auto-guess NAME_ARG_SOURCE.  It prints evidence so the reader can do the
    CPython-3.7 stack reconstruction by hand.

FOR EACH OF A/B IT PRINTS:
  * the FULL function bytecode (never just +/- 15 instructions) with every
        LOAD_GLOBAL TurboObjectPickerRow ... CALL_FUNCTION*  span highlighted so the
        whole row-construction region is shown verbatim,
  * every nested code object reachable from it (listcomp / lambda / closure / default
    thunks), each with its own full bytecode + metadata,
  * co_varnames / co_names / co_consts,
  * co_argcount / co_kwonlyargcount (+ posonly when present) and co_flags,
  * defaults only when statically recoverable (noted otherwise - they live in the
    calling site's MAKE_FUNCTION const sequence).

IT ALSO PRINTS the real signature + full body of:
  TurboObjectPickerRow.__init__

OUTPUT
------
output/ww_p29d/ww_p29d_focus_dump.txt   (only when the user actually runs it)

ZERO_WRITE_TO_MODS=YES.  No runtime hook.  No source modification.
Exit fail-closed: 2 source | 3 --dir | 4 no WW pkg | 6 no xdis | 7 no matched members.
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
DEFAULT_OUT = "ww_p29d_focus_dump.txt"

ROW_CLS = "TurboObjectPickerRow"
ROW_MODULE_SUFFIX = "object_picker_dialog"

# target functions: dotted module path -> function (bare co_name)
TARGET_MODULES = [
    ("wickedwhims.sex.integral.dialogs.sex_animation",
     "open_change_sex_animations_picker_dialog"),
    ("wickedwhims.sex.integral.dialogs.sex_init_player",
     "open_start_sex_animations_picker_dialog"),
]


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
    acc = [top]
    stack = [top]
    while stack:
        cur = stack.pop()
        for c in _direct_children(cur):
            acc.append(c)
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
    f = co.co_flags
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


def _argval_note(op, av):
    if hasattr(av, "co_name"):
        return "   <nested code: %s @L%s>" % (av.co_name,
                                              getattr(av, "co_firstlineno", "?"))
    if isinstance(av, tuple) and av and op in ("LOAD_CONST", "LOAD_FAST") and \
            all(isinstance(x, str) for x in av):
        return "   <keys tuple: %s>" % ", ".join(av)
    if op.endswith("KW") and isinstance(av, int):
        return ""
    return ""


def _disasm(co, opc):
    rows = []
    pending_row_load = False
    try:
        for it in Bytecode(co, opc):
            op = it.opname
            off = getattr(it, "offset", None)
            ln = getattr(it, "lineno", None)
            arg = it.argrepr or ""
            av = getattr(it, "argval", None)
            if op == "LOAD_GLOBAL" and arg == ROW_CLS:
                pending_row_load = True
                rows.append(">> L%-5d O%-6d %-20s %s   <-- ROW CLASS PUSH" %
                            (ln or 0, off, op, arg))
                continue
            if pending_row_load and op in (
                    "CALL_FUNCTION", "CALL_FUNCTION_KW", "CALL_FUNCTION_EX",
                    "CALL_FUNCTION_VAR", "CALL_FUNCTION_VAR_KW", "CALL_METHOD"):
                rows.append("*! L%-5d O%-6d %-20s %s   <-- ROW CONSTRUCTOR CALL" %
                            (ln or 0, off, op, arg))
                pending_row_load = False
                continue
            rows.append("   L%-5d O%-6d %-20s %s%s" %
                        (ln or 0, off, op, (arg or "-"),
                         _argval_note(op, av)))
    except Exception as e:
        rows.append("   <disasm error: %s>" % e)
    if not rows:
        rows.append("   <no bytecode / disasm unavailable>")
    return rows


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
    L.append("    defaults_static    = (not derived here: bytecode-level defaults live")
    L.append("    defaults_static       in the calling site's MAKE_FUNCTION const sequence;")
    L.append("    defaults_static       see calling-site bytecode above)")
    return L


def _emit_target(L, dotted, fn_name, cands, modid2dot, co2opc):
    L.append("")
    L.append("=" * 74)
    L.append("TARGET  module=%s" % dotted)
    L.append("        function=%s" % fn_name)
    if not cands:
        L.append("NOT FOUND: no loaded member whose dotted path == %s, or the code")
        L.append("object name %r is absent in that member." % fn_name)
        return
    index = 0
    for (pkg, co) in cands:
        index += 1
        opc = co2opc.get(id(co))
        L.append("")
        L.append("### [hit %d/%d] %s : %s @L%s (pkg=%s)" %
                 (index, len(cands), dotted, co.co_name,
                  getattr(co, "co_firstlineno", "?"), pkg))
        L.extend(_sign(co))
        L.append("    --- main body bytecode (FULL, offsets O=byte offset) ---")
        for r in _disasm(co, opc):
            L.append("    " + r)
        kids = _direct_children(co)
        L.append("")
        L.append("    --- nested code objects directly reachable from this body: %d ---" %
                 len(kids))
        for k in kids:
            L.append("")
            L.append("    [nested] %s @L%s (flags=%s)" %
                     (k.co_name, getattr(k, "co_firstlineno", "?"), _co_flags(k)))
            L.append("            varnames=%s" % ", ".join(k.co_varnames))
            L.append("            names   =%s" % ", ".join(k.co_names))
            subopc = co2opc.get(id(k)) or opc
            for r in _disasm(k, subopc):
                L.append("        " + r)
            for kk in _direct_children(k):
                L.append("        [nested2] %s @L%s" %
                         (kk.co_name, getattr(kk, "co_firstlineno", "?")))
                for r in _disasm(kk, co2opc.get(id(kk)) or subopc):
                    L.append("            " + r)


def _emit_ctor(L, row_ctor_cands, co2opc):
    L.append("")
    L.append("=" * 74)
    L.append("TurboObjectPickerRow.__init__  (REAL signature + body)")
    if not row_ctor_cands:
        L.append("NOT FOUND: no loaded %s class body with a direct __init__ child."
                 % ROW_CLS)
        return
    for (pkg, dotted, cls_co, ctor) in row_ctor_cands:
        opc = co2opc.get(id(ctor)) or co2opc.get(id(cls_co))
        L.append("")
        L.append("### class=%s  module=%s  pkg=%s  class@L%s" %
                 (ROW_CLS, dotted, pkg, getattr(cls_co, "co_firstlineno", "?")))
        L.append("    __init__ @L%s" % getattr(ctor, "co_firstlineno", "?"))
        L.extend(_sign(ctor))
        L.append("    --- __init__ bytecode (FULL) ---")
        for r in _disasm(ctor, opc):
            L.append("    " + r)
        for k in _direct_children(ctor):
            L.append("    [nested in __init__] %s @L%s" %
                     (k.co_name, getattr(k, "co_firstlineno", "?")))
            for r in _disasm(k, co2opc.get(id(k)) or opc):
                L.append("        " + r)


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

    target_dots = {d for (d, _f) in TARGET_MODULES}
    members = []  # (pkgname, dotted, data)
    for p in pkgs:
        try:
            with zipfile.ZipFile(str(p)) as z:
                for nn in z.namelist():
                    if not nn.endswith(".pyc"):
                        continue
                    dotted = _rel_dotted(nn)
                    if dotted in target_dots or dotted.endswith(ROW_MODULE_SUFFIX):
                        members.append((p.name, dotted, z.read(nn)))
        except Exception:
            continue
    members = sorted(members, key=lambda m: (m[0], m[1]))
    if not members:
        print("ERROR: no .pyc members matched focused modules (targets or "
              "object_picker_dialog)", file=sys.stderr)
        return 7

    found = {}          # dotted -> list of (pkg, co)
    row_ctor_cands = [] # (pkg, dotted, class_co, ctor_co)
    modid2dot = {}
    co2opc = {}
    fn_by_dot = dict(TARGET_MODULES)
    for pkg, dotted, data in members:
        try:
            opc, top = _load_member(data)
        except Exception:
            continue
        for co in _all_codes(top):
            co2opc[id(co)] = opc
            modid2dot[id(co)] = dotted
        for co in _all_codes(top):
            if dotted in fn_by_dot and co.co_name == fn_by_dot[dotted]:
                found.setdefault(dotted, []).append((pkg, co))
            if co.co_name == ROW_CLS:
                for sub in _direct_children(co):
                    if sub.co_name == "__init__":
                        row_ctor_cands.append((pkg, dotted, co, sub))

    L = []
    L.append("=== P29-D FOCUSED DISASSEMBLY (READ-ONLY, NO ANALYSIS) ===")
    L.append("ZERO_WRITE_TO_MODS=YES")
    L.append("row_class=" + ROW_CLS)
    L.append("row_module_suffix=" + ROW_MODULE_SUFFIX)
    L.append("")

    for (dotted, fn_name) in TARGET_MODULES:
        _emit_target(L, dotted, fn_name, found.get(dotted), modid2dot, co2opc)

    _emit_ctor(L, row_ctor_cands, co2opc)

    L.append("")
    L.append("ZERO_WRITE_TO_MODS=YES")

    out_path.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("WROTE %s" % out_path, file=sys.stderr)
    missing = [fn for (d, fn) in TARGET_MODULES if not found.get(d)]
    if missing:
        print("WARNING: target(s) not found: %s" % ", ".join(missing),
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
