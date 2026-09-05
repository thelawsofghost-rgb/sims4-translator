#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29d_l18n_dump.py -- P29-D FOCUSED L18N SERVICE DISASSEMBLY
                            (READ-ONLY, NO ANALYSIS / NO GUESSING)

SCOPE / CONTEXT
---------------
P29-D narrowed the animation-row text boundary to a single STATIC translation
wall:

    get_l18n_service().get_localized_string(...)

and notes that the animation-row path calls it at least TWICE:
  1. inside  SexAnimationInstance.get_picker_row
  2. inside  TurboObjectPickerRow.__init__

Question this tool supplies EVIDENCE for (it never draws the conclusion on its
own):
    Does the static implementation of get_localized_string, given a plain str
    like "TEST300", return the raw text "TEST300" unchanged?

    ...or may it route a str through:
        hash/key lookup / registry lookup / STBL lookup / some other mapping /
        type branch on str / int / LocalizedString|TurboLocalizedString / None?

THIS SCRIPT (per the owner)
  * targets exactly TWO real caller modules and resolves the exact module that
    defines the get_l18n_service function they actually import:
        - wickedwhims.sex.animations.animation_instance
        - turbolib2.ui.object_picker_dialog
  * does NOT runtime-hook, does NOT whole-package-call-graph scan, does NOT touch
    Mods/XML/TEST300, does NOT modify any existing script.
  * does NOT auto-conclude.  It prints, verbatim:
        GET_L18N_SERVICE_IMPORT_MODULE=   (statically from the caller's import
                                           bytecode; only set when the module is
                                           deterministically present)
        GET_L18N_SERVICE_DEFINITION=      (module path where the function body was
                                           actually found + dumped)
    then FULL disassembly of:
        - get_l18n_service
        - the service/singleton class it returns (get_localized_string,
          get_localized_string_id, and get_ssid when part of the same class body)
        - direct helper call targets of get_localized_string, at most ONE level down
  * prints for EVERY function:
        co_varnames / co_names / co_consts / FULL bytecode / nested code objects
  * KEEPS the branch bytecode of get_localized_string for every input type branch:
        str / int / LocalizedString|TurboLocalizedString / None
    and marks (never interprets) compare / jump / hash / subscr / len / etc.

OUTPUT
------
output/ww_p29d/ww_p29d_l18n_dump.txt   (only when actually run)

ZERO_WRITE_TO_MODS=YES.  No runtime hook.  No source modification.
Exit fail-closed: 2 source | 3 --dir | 4 no WW pkg | 6 no xdis | 7 no matched members.
NOT committed / NOT pushed unless the owner says so.
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
DEFAULT_OUT = "ww_p29d_l18n_dump.txt"

SERVICE_FUNC = "get_l18n_service"

# The two real caller modules to resolve get_l18n_service from (module suffix match,
# like the sibling object_picker_dialog scan).
CALLER_SUFFIXES = (
    ("wickedwhims.sex.animations.animation_instance",),
    ("turbolib2.ui.object_picker_dialog",),
)
CALLER_EXACT = "wickedwhims.sex.animations.animation_instance"
CALLER_DOTS = {d for (d,) in CALLER_SUFFIXES}
# second caller matched by suffix for modularity across pkg layouts
CALLER2_SUFFIX = "turbolib2.ui.object_picker_dialog"

# methods to dump on the resolved service object
SERVICE_METHODS = ("get_localized_string", "get_localized_string_id", "get_ssid")

# tokens we MARK (never interpret) inside get_localized_string + helpers
MARK_TOKENS = ("str", "int", "LocalizedString", "TurboLocalizedString", "None",
               "hash", "get_hash", "lookup", "registry", "stbl", "STBL",
               "get_localized", "localize")

# these are OBJECTS (types/classes) whose LOAD we mark as a "type seen" line
TYPE_CLASS_NAMES = ("LocalizedString", "TurboLocalizedString", "LocalizedStringIds")

IMPORT_OPS = ("IMPORT_NAME", "IMPORT_FROM")


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
    L.append("    co_freevars        = %s" %
             (", ".join(getattr(co, "co_freevars", ())) or "-"))
    L.append("    co_cellvars        = %s" %
             (", ".join(getattr(co, "co_cellvars", ())) or "-"))
    L.append("    defaults_static    = (not derived here: bytecode-level defaults live")
    L.append("    defaults_static       in the calling site const sequence)")
    return L


def _marker(op, arg, av):
    """Return a short evidence marker string, or ''."""
    if op in ("LOAD_GLOBAL", "LOAD_METHOD", "LOAD_ATTR"):
        for t in MARK_TOKENS:
            if t.lower() in (arg or "").lower():
                return "   <token:%s>" % arg
        if op == "LOAD_GLOBAL" and arg in TYPE_CLASS_NAMES:
            return "   <TYPE:%s>" % arg
    if op == "IMPORT_NAME":
        return "   <IMPORT module=%s>" % arg
    if op == "IMPORT_FROM":
        return "   <IMPORT_FROM=%s>" % arg
    if op in ("POP_JUMP_IF_TRUE", "POP_JUMP_IF_FALSE", "JUMP_IF_TRUE_OR_POP",
              "JUMP_IF_FALSE_OR_POP", "JUMP_FORWARD", "JUMP_ABSOLUTE"):
        return "   <branch>"
    if op in ("COMPARE_OP", "IS_OP", "CONTAINS_OP"):
        return "   <compare>"
    if op in ("BINARY_SUBSCR", "BINARY_ADD", "BINARY_AND", "UNARY_INVERT",
              "BINARY_XOR", "BINARY_OR", "BUILD_MAP", "LOAD_CONST"):
        return ""
    if hasattr(av, "co_name"):
        return "   <nested:%s@L%s>" % (av.co_name,
                                       getattr(av, "co_firstlineno", "?"))
    return ""


def _disasm(co, opc):
    rows = []
    if co is None:
        return ["<no code object>"]
    try:
        import re as _re
        CODE_RE = _re.compile(r"^<code object (\S+) @ 0x")
        for it in Bytecode(co, opc):
            op = it.opname
            off = getattr(it, "offset", None)
            ln = getattr(it, "lineno", None)
            raw = it.argrepr or ""
            # xdis argrepr for code-object consts is <code object NAME @0xADDR>
            adisp = raw
            m = CODE_RE.match(raw)
            if m:
                adisp = "CODE:%s@L%d" % (m.group(1),
                                         getattr(it, "argval", raw).co_firstlineno
                                         if hasattr(getattr(it, "argval", None),
                                                    "co_firstlineno")
                                         else getattr(co, "co_firstlineno", 0))
            arg = adisp.replace("\n", " ")
            av = getattr(it, "argval", None)
            note = _marker(op, arg, av)
            rows.append("  L%-6d O%-6d %-22s %s%s" %
                        (ln or 0, off or 0, op, (arg or "-"), note))
    except Exception as e:
        rows.append("  <disasm error: %s>" % e)
    return rows


def _emit_body(L, co, opc, header=None, indent="    "):
    if header:
        L.append(indent + header)
    L.extend([indent + s for s in _sign(co)])
    L.append(indent + "--- bytecode (FULL) ---")
    for r in _disasm(co, opc):
        L.append(indent + r)
    kids = _direct_children(co)
    if kids:
        L.append("")
        L.append(indent + "--- nested code objects directly reachable: %d ---" %
                 len(kids))
        for k in kids:
            L.append("")
            sub_header = "[nested] %s @L%s (flags=%s)" % (
                k.co_name, getattr(k, "co_firstlineno", "?"), _co_flags(k))
            # nested bodies disasm with same opcode family
            _emit_body(L, k, opc, header=sub_header, indent=indent + "    ")


def _find_top_func(top, fname):
    """Return the module-level code object named fname directly under module top
    (the module's __init__/function def), else None.  Matches either a direct child
    of the module wrapper or a def nested in a class body only when it is reachable
    from module top and has the exact co_name."""
    for co in _all_codes(top):
        if co.co_name == fname and co is not top:
            # prefer the shallowest (module-level) definition: the first in DFS is
            # top itself, so iterate children of top first
            return co
    return None


def _find_class_body(top, cls_name):
    """Return the code object that is the class body named cls_name (its direct
    children include its methods), else None."""
    for co in _all_codes(top):
        if co is top or co.co_name != cls_name:
            continue
        kids = _direct_children(co)
        if any(k.co_name in ("__init__", "get_localized_string") for k in kids):
            return co
    return None


def _collect_imports(top, opc):
    """Dump every IMPORT_NAME / adjacent IMPORT_FROM line of the module top level
    (scope that actually holds imports) into rows."""
    rows = []
    around = []
    for it in Bytecode(top, opc):
        op = it.opname
        ln = getattr(it, "lineno", None)
        off = getattr(it, "offset", None)
        arg = (it.argrepr or "").replace("\n", " ")
        if op == "IMPORT_NAME":
            rows.append((ln, off, op, arg, "<IMPORT module=%s>" % arg))
        elif op == "IMPORT_FROM":
            rows.append((ln, off, op, arg, "<IMPORT_FROM=%s>" % arg))
    return rows


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

    # load ALL .pyc members once: dotted -> (opc, top, pkgname)
    module_map = {}
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
    if not module_map:
        print("ERROR: no .pyc members loaded from any package", file=sys.stderr)
        return 4

    L = []
    L.append("=== P29-D FOCUSED L18N SERVICE DISASSEMBLY (READ-ONLY, NO ANALYSIS) ===")
    L.append("ZERO_WRITE_TO_MODS=YES")
    L.append("service_function = " + SERVICE_FUNC)
    L.append("caller_modules   = " + ", ".join(sorted(CALLER_DOTS | {CALLER2_SUFFIX})))
    L.append("service_methods  = " + ", ".join(SERVICE_METHODS))
    L.append("")

    # -----------------------------------------------------------------
    # 1) IMPORT bytecode of the two real callers (which module binds
    #    get_l18n_service / which alias reaches it)
    # -----------------------------------------------------------------
    L.append("=" * 74)
    L.append("1. CALLER IMPORT BYTECODE  (find the real get_l18n_service source)")
    caller_targets = [CALLER_EXACT, CALLER2_SUFFIX]
    import_evidence = []      # (caller_dotted, lines)
    for scope in caller_targets:
        cand = {d for d in module_map if d == scope or d.endswith("." + scope.split(".")[-1])}
        L.append("")
        L.append("### caller %-50s  loaded_module=%s" %
                 (scope, ", ".join(sorted(cand)) if cand else "(none)"))
        for d in sorted(cand):
            opc, top, pkg = module_map[d]
            L.append("   module %s (pkg=%s)" % (d, pkg))
            for (ln, off, op, arg, mark) in _collect_imports(top, opc):
                L.append("       L%-6d O%-6d %-16s %s%s" %
                         (ln or 0, off or 0, op, arg, mark))
            import_evidence.append((d, _collect_imports(top, opc)))

    # -----------------------------------------------------------------
    # 2) statically find a candidate module whose top-level defines
    #    get_l18n_service, requiring at least one caller imported a module
    #    name that contains it; print GET_L18N_SERVICE_IMPORT_MODULE lines.
    # -----------------------------------------------------------------
    defs = []   # dotted modules that define get_l18n_service at module level
    for d, (opc, top, pkg) in module_map.items():
        if _find_top_func(top, SERVICE_FUNC) is not None:
            defs.append(d)
    # prefer modules referenced by an IMPORT_NAME shown above
    mentioned = set()
    for (cd, rows) in import_evidence:
        for (_ln, _off, op, arg, _mk) in rows:
            if op == "IMPORT_NAME":
                mentioned.add(arg)
    matched_def = None
    # 1st: an exact def whose dotted is among the mentioned import targets / suffix
    for d in defs:
        base = d.split(".")[-1]
        if any(base == __m.split(".")[-1] and d != __m for __m in mentioned) or \
           any(d.startswith(__m) or __m.startswith(d) for __m in mentioned if __m):
            matched_def = d
            break
    if matched_def is None and defs:
        matched_def = defs[0]   # last-resort evidence-only: a module that defines it

    L.append("")
    L.append("=" * 74)
    L.append("2. SERVICE SOURCE RESOLUTION")
    if mentioned:
        L.append("modules mentioned by IMPORT_NAME across callers: %s" %
                 ", ".join(sorted(mentioned)))
    L.append("")
    listed_import = sorted(_arg for (cd, rows) in import_evidence
                           for (_ln, _off, _op, _arg, _mk) in rows
                           if _op == "IMPORT_NAME")
    L.append("GET_L18N_SERVICE_IMPORT_MODULE= " +
             (", ".join(listed_import) or "(no IMPORT_NAME literal found; see "
                                          "caller bytecode below)"))
    L.append("GET_L18N_SERVICE_DEFINITION=   " +
             (matched_def if matched_def else "(none found as module-level def)"))
    L.append("")

    if matched_def:
        opc, top, pkg = module_map[matched_def]
        L.append("=" * 74)
        L.append("3. get_l18n_service  (definition found in module=%s, pkg=%s)" %
                 (matched_def, pkg))
        fn = _find_top_func(top, SERVICE_FUNC)
        if fn is not None:
            _emit_body(L, fn, opc, header="#" * 1, indent="    ")
        # what service does it return? print the module-level binding names of the
        # class the function references most (LOAD_GLOBAL/LOAD_NAME right before a
        # RETURN), as evidence (not conclusion).
        ret_names = []
        for it in Bytecode(fn, opc):
            if it.opname in ("LOAD_GLOBAL", "LOAD_NAME", "LOAD_ATTR") and \
                    (it.argrepr or ""):
                ret_names.append(it.argrepr)
        L.append("")
        L.append("names the function body loads (evidence for the returned service "
                 "type, NOT a conclusion):")
        L.append("    " + (", ".join(ret_names) if ret_names else "-"))

        # -----------------------------------------------------------------
        # 4) dump service_methods found on the SAME module / class where the
        #    returned service type would live: search class bodies that define
        #    get_localized_string, prefer one in the matched_def module.
        # -----------------------------------------------------------------
        L.append("")
        L.append("=" * 74)
        L.append("4. SERVICE METHOD FULL DUMP (candidate class bodies defining "
                 "service_methods)")
        # collect candidate class names that define get_localized_string
        service_of = []   # (cls_name, dotted)
        for d, (o2, t2, p2) in module_map.items():
            seen_cls = set()
            for co in _all_codes(t2):
                if co.co_name in seen_cls:
                    continue
                if co is t2 or co.co_name in ("__init__",):
                    continue
                for sub in _direct_children(co):
                    if sub.co_name in SERVICE_METHODS:
                        seen_cls.add(co.co_name)
                        service_of.append((co.co_name, d))
                        break
        # de-dupe prefer module==matched_def, else first seen
        chosen = None
        for (cn, d) in service_of:
            if d == matched_def:
                chosen = (cn, d)
                break
        if chosen is None and service_of:
            chosen = service_of[0]
        if chosen:
            cn, d = chosen
            L.append("service class candidate (evidence-only, see bases/site): "
                     "%s  in module=%s" % (cn, d))
        else:
            L.append("(no class body defining any service_method found in loaded "
                     "packages)")
            cn, d = None, None

        if cn and d:
            o2, t2, p2 = module_map[d]
            # class-body code object + sibling-method table (name -> code) for
            # the one-level helper follow
            cls_co = None
            for co in _all_codes(t2):
                if co.co_name == cn and co is not t2:
                    cls_co = co
                    break
            method_tbl = {}
            if cls_co is not None:
                for sub in _direct_children(cls_co):
                    method_tbl.setdefault(sub.co_name, []).append(sub)

            for m in SERVICE_METHODS:
                found_m = []
                # search direct children of the class body named cn
                if cls_co is not None:
                    for sub in _direct_children(cls_co):
                        if sub.co_name == m:
                            found_m.append(sub)
                L.append("")
                L.append("-" * 74)
                L.append("METHOD %s  (class=%s module=%s pkg=%s)" %
                         (m, cn, d, p2))
                if not found_m:
                    L.append("  NOT DEFINED directly on class %s (inherited or "
                             "absent)" % cn)
                for fm in found_m:
                    _emit_body(L, fm, o2,
                               header="[method] %s @L%s" %
                               (fm.co_name, getattr(fm, "co_firstlineno", "?")),
                               indent="")
                    # one-level sibling-helper follow: every LOAD_METHOD/CALL_METHOD
                    # operand in THIS method that names a sibling defined on the same
                    # class is dumped once (at most one level deep).
                    follows = set()
                    try:
                        for it in Bytecode(fm, o2):
                            if it.opname in ("LOAD_METHOD", "CALL_METHOD") and \
                                    (it.argrepr or "") in method_tbl:
                                follows.add(it.argrepr)
                    except Exception:
                        pass
                    if follows:
                        L.append("")
                        L.append("  -- direct sibling helpers of %s (1 level down) --" % m)
                        for hname in sorted(follows):
                            for hco in method_tbl[hname]:
                                L.append("")
                                _emit_body(L, hco, o2,
                                           header="[helper %s.%s via %s] @L%s" %
                                           (cn, hname, m,
                                            getattr(hco, "co_firstlineno", "?")),
                                           indent="  ")
    else:
        L.append("=" * 74)
        L.append("3/4. SKIPPED - no module-level get_l18n_service definition found;")
        L.append("     the caller module bytecode above is the only evidence.")

    L.append("")
    L.append("ZERO_WRITE_TO_MODS=YES")
    out_path.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("WROTE %s" % out_path, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
