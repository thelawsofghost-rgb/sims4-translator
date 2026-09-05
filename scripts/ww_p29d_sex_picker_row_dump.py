#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29d_sex_picker_row_dump.py -- P29-D SexAnimationPickerRow DISASM
                                     (READ-ONLY, NO ANALYSIS / NO GUESSING)

CONTEXT / WHY THIS SCOPE
------------------------
Dorothy's real-machine instance dump reached: SexAnimationInstance.get_picker_row
builds, per animation row -- from a NON-favorite path
    name = get_l18n_service().get_localized_string( self.get_display_name() )
or a FAVORITE path
    name = localized_template( tokens=( self.get_display_name(), ) )
-- then (possibly) passes that through a localized wrapper, and finally calls:

    SexAnimationPickerRow(
        self.get_animation_id() + 1,
        name,
        description,
        icon=...,
        is_disabled=...,
        tag=(self, SexAnimationSecondaryTag.NONE),
        tag_list=categories,
        **kwargs,
    )

get_stage_name / animation_stage_name are NOT used inside get_picker_row, so the
stage-name UI-title hypothesis is EXCLUDED on that path.

OPEN QUESTION this tool supplies evidence for:
    Once the 2nd positional argument `name` reaches SexAnimationPickerRow, is it
    later replaced / re-queried against an animation_instance / remapped by
    identifier / turned into a different text?

THIS SCRIPT
  * targets ONLY class  SexAnimationPickerRow in module
        wickedwhims.sex.animations.animations_operator
  * does NOT scan the whole-package call graph / no runtime hook / no Mods/XML/TEST300.
  * does NOT import any existing script (self-contained readers/helpers).
  * prints:
      1) the module's class-construction site (BUILD_CLASS context) so the real
         bases operand can be read; plus a name->module map of every candidate class
         found in the loaded packages that shares the target name,
      2) __init__ real signature (co_argcount / co_varnames / ...) + FULL bytecode.
      3) if the class ITSELF defines get_name / get_object_picker_row /
         get_base_picker_row / get_dropdown_picker_row, full-dump each.
      4) if a listed method is NOT defined by the class, print an explicit
         INHERITED_FROM=/ABSENT line and, when a same-named method is found on a
         candidate class body in the loaded packages, FULL DUMP that method so the
         reader can decide the real MRO parent (evidence only - never assumed).
      5) marks (never interprets) tokens:
            name / display_name / get_display_name / localized /
            get_localized_string / TurboObjectPickerRow / ObjectPickerRow
         plus every STORE_ATTR name.
  * prints watch hits inline but draws NO conclusion about `name` replacement.

OUTPUT
------
output/ww_p29d/ww_p29d_sex_picker_row_dump.txt   (only when actually run)

ZERO_WRITE_TO_MODS=YES.
Exit fail-closed: 2 source | 3 --dir | 4 no WW pkg | 6 no xdis | 7 target missing.
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
DEFAULT_OUT = "ww_p29d_sex_picker_row_dump.txt"

TARGET_MODULE = "wickedwhims.sex.animations.animations_operator"
TARGET_CLASS = "SexAnimationPickerRow"
IMPORTANT_METHODS = ("get_name", "get_object_picker_row", "get_base_picker_row",
                     "get_dropdown_picker_row")

WATCH = ("name", "display_name", "get_display_name", "localized",
         "get_localized_string", "TurboObjectPickerRow", "ObjectPickerRow")

ROW_CALL_OPS = ("CALL_FUNCTION", "CALL_FUNCTION_KW", "CALL_FUNCTION_EX",
                "CALL_FUNCTION_VAR", "CALL_FUNCTION_VAR_KW")

EXACT_NAME_TOKENS = ("name", "display_name")  # watch only via LOAD/STORE_ATTR name


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


def _class_bodies(top):
    """Return (cls_name, body_co, methods_dict) for each class-body code object
    directly reachable from a non-method scope in this module top.

    A class definition compiles to a body code object whose *direct children* are
    its method code objects; its name is the class name.  We detect one reliably by
    requiring __init__ OR any of IMPORTANT_METHODS among its direct children, and by
    NOT being itself a direct method of another detected class.
    """
    out = []
    for co in _all_codes(top):
        if co.co_name in ("<module>", "<lambda>", "<listcomp>", "<setcomp>",
                          "<dictcomp>", "<genexpr>"):
            continue
        children = _direct_children(co)
        child_names = {c.co_name for c in children}
        if "__init__" in child_names or (child_names & set(IMPORTANT_METHODS)):
            out.append((co.co_name, co, {c.co_name: c for c in children}))
    return out


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


def _watch_hit(op, arg):
    if op == "STORE_ATTR" and arg == "name":
        return True
    for w in WATCH:
        if w in EXACT_NAME_TOKENS:
            if w == "name" and (arg == "name"):
                return True
            if w == "display_name" and arg == "display_name":
                return True
            continue
        if w in (arg or ""):
            return True
    return False


def _disasm(co, opc):
    """rows = (lineno, offset, op, arg, marker, hit)

    marker is one of: '', 'ROW_PUSH', 'ROW_CALL', 'ROW_ARG'."""
    rows = []
    pending = False
    for it in Bytecode(co, opc):
        op = it.opname
        off = getattr(it, "offset", None)
        ln = getattr(it, "lineno", None)
        arg = (it.argrepr or "").replace("\n", " ")
        av = getattr(it, "argval", None)

        is_push = (op == "LOAD_GLOBAL" and
                   arg in ("TurboObjectPickerRow", "ObjectPickerRow"))
        is_row_call = op in ROW_CALL_OPS

        if is_push:
            pending = True
            rows.append((ln, off, op, arg, "ROW_PUSH", False))
            continue
        if pending:
            if is_row_call:
                rows.append((ln, off, op, arg, "ROW_CALL", False))
                pending = False
            else:
                rows.append((ln, off, op, arg, "ROW_ARG", False))
            continue
        hit = _watch_hit(op, arg)
        rows.append((ln, off, op, arg, "", hit))
    return rows


def _emit_rows(L, rows, indent="    "):
    for (ln, off, op, arg, marker, hit) in rows:
        extra = ""
        if marker == "ROW_PUSH":
            extra = "   <== LOAD %s (ROW CLASS PUSH)" % arg
        elif marker == "ROW_CALL":
            extra = "   <== ROW OBJECT CALL consumes the class"
        elif marker == "ROW_ARG":
            extra = "   <== [row-argument span]"
        if hit and not extra:
            extra = "   <== watch token"
        L.append("%sL%-6d O%-6d %-22s %-28s %s" %
                 (indent, ln or 0, off or 0, op, (arg or "-"), extra.rstrip()))


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
    L.append("    co_consts          = %s" % _const_list(co))
    L.append("    co_freevars        = %s" %
             (", ".join(getattr(co, "co_freevars", ())) or "-"))
    L.append("    co_cellvars        = %s" %
             (", ".join(getattr(co, "co_cellvars", ())) or "-"))
    return L


def _const_list(co):
    parts = []
    for c in co.co_consts:
        if hasattr(c, "co_name"):
            parts.append("CODE:%s@L%s" % (c.co_name,
                                          getattr(c, "co_firstlineno", "?")))
        else:
            r = repr(c)
            parts.append(r if len(r) <= 90 else r[:90] + "...")
    return ", ".join(parts) if parts else "(empty)"


def _dump_body(L, co, opc, indent="    ", title=None):
    if title:
        L.append(indent + title)
    for s in _sign(co):
        L.append(indent + s)
    L.append(indent + "--- bytecode (FULL) ---")
    _emit_rows(L, _disasm(co, opc), indent + "  ")
    kids = _direct_children(co)
    if kids:
        L.append("")
        L.append(indent + ("--- nested code objects reachable from this body: %d ---"
                           % len(kids)))
        for k in kids:
            L.append("")
            _dump_body(L, k, opc, indent + "    ",
                       title="[nested] %s @L%s (flags=%s)" %
                             (k.co_name, getattr(k, "co_firstlineno", "?"),
                              _co_flags(k)))
    return L


def _find_class_site(top, cls_name):
    """Return the enclosing code object (module top or a def) whose co_consts
    directly holds a code object named cls_name AND that enclosing body references
    the class via BUILD_CLASS, plus the disasm rows of that enclosing scope."""
    # find any code object under top whose direct children include one named cls_name
    for scope in _all_codes(top):
        if any(c.co_name == cls_name for c in _direct_children(scope)):
            return scope
    return None


def _class_site_rows(site_co, cls_name, opc):
    """Extract the instruction segment that builds `cls_name`: from just before the
    class-body class const is pushed through BUILD_CLASS and the following STORE, so
    the real bases operand is visible."""
    rows = []
    body = None
    for c in _direct_children(site_co):
        if c.co_name == cls_name:
            body = c
            break
    if body is None:
        return []
    out = []
    built_site_seen = False
    pending = False
    for it in Bytecode(site_co, opc):
        op = it.opname
        ln = getattr(it, "lineno", None)
        off = getattr(it, "offset", None)
        arg = (it.argrepr or "").replace("\n", " ")
        if not pending:
            # detect pushing of the class body code object
            if op in ("LOAD_CONST", "LOAD_CODE") and arg.startswith(
                    "<code object %s" % cls_name):
                pending = True
                continue
            continue
        if op == "BUILD_CLASS":
            out.append("    L%-6d O%-6d %-22s %s   <== BUILD_CLASS (consumes "
                       "meta,name,*bases,body)" % (ln or 0, off or 0, op,
                                                   (arg or "-")))
            built_site_seen = True
            continue
        out.append("    L%-6d O%-6d %-22s %s" % (ln or 0, off or 0, op,
                                                 (arg or "-")))
        if built_site_seen and op.startswith("STORE"):
            break
        if built_site_seen and op in ("POP_TOP", "RETURN_VALUE", "STORE_FAST",
                                      "STORE_NAME", "STORE_GLOBAL"):
            break
    return out


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

    # ------------------------------------------------------------------
    # A) Load the WW packages once into memory:  pkg_name -> {dotted: (opc, top)}
    # ------------------------------------------------------------------
    module_map = {}          # dotted -> (opc, top, pkg_name)
    for p in pkgs:
        try:
            with zipfile.ZipFile(str(p)) as z:
                for nn in z.namelist():
                    if not nn.endswith(".pyc"):
                        continue
                    dotted = _rel_dotted(nn)
                    if dotted in module_map:
                        continue
                    try:
                        opc, top = _load_member(z.read(nn))
                    except Exception:
                        continue
                    module_map[dotted] = (opc, top, p.name)
        except Exception:
            continue

    # ------------------------------------------------------------------
    # B) class registry:  cls_name -> list of (dotted, body_co, methods, opc)
    # ------------------------------------------------------------------
    classes = {}
    for dotted, (opc, top, pname) in module_map.items():
        for (cn, body_co, methods) in _class_bodies(top):
            entry = {"dotted": dotted, "pkg": pname, "opc": opc,
                     "body": body_co, "methods": methods}
            classes.setdefault(cn, []).append(entry)

    L = []
    L.append("=== P29-D SexAnimationPickerRow DISASM (READ-ONLY, NO ANALYSIS) ===")
    L.append("ZERO_WRITE_TO_MODS=YES")
    L.append("target_module = " + TARGET_MODULE)
    L.append("target_class  = " + TARGET_CLASS)
    L.append("methods       = " + ", ".join(IMPORTANT_METHODS))
    L.append("watch_tokens  = " + ", ".join(WATCH))
    L.append("")

    target = classes.get(TARGET_CLASS, [])
    target_entry = [e for e in target if e["dotted"] == TARGET_MODULE]
    if not target_entry:
        L.append("NOT FOUND: class %s absent as a class body in module %s" %
                 (TARGET_CLASS, TARGET_MODULE))
        if classes.get(TARGET_CLASS):
            L.append("same name appears in other modules (see section 1):")
        out_path.write_text("\n".join(L) + "\n", encoding="utf-8")
        return 7
    entry = target_entry[0]
    top = module_map[TARGET_MODULE][1]
    opc = entry["opc"]

    L.append("=" * 74)
    L.append("1. CLASS BASES (real BUILD_CLASS construction site)")
    L.append("")
    site = _find_class_site(top, TARGET_CLASS)
    if site is not None:
        L.append("enclosing scope that builds %s : %s @L%s (module=%s)" %
                 (TARGET_CLASS, site.co_name,
                  getattr(site, "co_firstlineno", "?"), TARGET_MODULE))
        L.append("instruction segment pushing the class body then BUILD_CLASS:")
        site_rows = _class_site_rows(site, TARGET_CLASS, opc)
        if site_rows:
            for r in site_rows:
                L.append(r)
        else:
            L.append("  (no BUILD_CLASS tail decoded from enclosing %s)" %
                     site.co_name)
    else:
        L.append("(no enclosing scope found whose co_consts directly holds the "
                 "class body; class is likely built via an alias/metaclass path)")
    # list every class of the same name seen across loaded modules (evidence for MRO)
    if len(target) > 1:
        L.append("same-name class bodies found across loaded packages:")
        for e in target:
            L.append("    - %s  (module=%s, pkg=%s, @L%s)" %
                     (TARGET_CLASS, e["dotted"], e["pkg"],
                      getattr(e["body"], "co_firstlineno", "?")))
    L.append("")
    L.append("(NOTE: read the BUILD_CLASS segment above: the real base object(s) are")
    L.append(" the LOAD_* operands pushed immediately before BUILD_CLASS.  This dump")
    L.append(" shows them verbatim; it does NOT guess the MRO.)")
    L.append("")

    # ------------------------------------------------------------------
    # 2) __init__
    # ------------------------------------------------------------------
    L.append("=" * 74)
    L.append("2. __init__ (own or inherited)")
    init_map = entry["methods"]
    if "__init__" in init_map:
        L.append("")
        L.append("OWN __init__ @L%s" %
                 getattr(init_map["__init__"], "co_firstlineno", "?"))
        _dump_body(L, init_map["__init__"], opc)
    else:
        L.append("NO OWN __init__ child -> INHERITED_FROM= (to resolve, find the")
        L.append("real base named in section 1 and read its __init__ below in")
        L.append("section 3-candidates)")
        _emit_parent_candidates(L, "__init__", classes, opc)

    # ------------------------------------------------------------------
    # 3) IMPORTANT_METHODS  (own or inherited)
    # ------------------------------------------------------------------
    for m in IMPORTANT_METHODS:
        L.append("")
        L.append("=" * 74)
        L.append("METHOD %s" % m)
        own = init_map.get(m)
        if own is not None:
            L.append("")
            L.append("OWN @L%s" % getattr(own, "co_firstlineno", "?"))
            _dump_body(L, own, opc)
        else:
            L.append("NOT DEFINED BY %s." % TARGET_CLASS)
            L.append("INHERITED_FROM= (candidates below that define %r in loaded"
                     " packages)" % m)
            _emit_parent_candidates(L, m, classes, opc)

    L.append("")
    L.append("ZERO_WRITE_TO_MODS=YES")
    out_path.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("WROTE %s" % out_path, file=sys.stderr)
    return 0


def _emit_parent_candidates(L, method, classes, opc):
    """Find every other class in the loaded modules defining `method`; full-dump the
    FIRST one as inline evidence so the reader can pick the real MRO parent."""
    cand = []
    for cn, entries in classes.items():
        if cn == TARGET_CLASS:
            continue
        for e in entries:
            if method in e["methods"]:
                cand.append((cn, e))
    if not cand:
        L.append("  (no candidate parent class in loaded packages defines %r)" %
                 method)
        return
    seen = set()
    L.append("")
    L.append("  candidate classes defining %r in loaded packages:" % method)
    for (cn, e) in cand:
        key = e["dotted"]
        if key in seen:
            continue
        seen.add(key)
        L.append("    - %s  (module=%s, pkg=%s, @L%s)" %
                 (cn, e["dotted"], e["pkg"],
                  getattr(e["methods"][method], "co_firstlineno", "?")))
    # full-dump the first candidate as inline evidence
    (cn, e) = cand[0]
    L.append("")
    L.append("  FULL DUMP of first candidate: %s . %s  (module=%s)" %
             (cn, method, e["dotted"]))
    L.append("  (CANDIDATE: the real MRO decides which parent actually provides it)")
    _dump_body(L, e["methods"][method], e["opc"], indent="    ")
    if len(cand) > 1:
        L.append("")
        L.append("  (%d more candidate parent class(es); inspect the intended base "
                 "listed in section 1)" % (len(cand) - 1))


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
