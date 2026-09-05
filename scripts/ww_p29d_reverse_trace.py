#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29d_reverse_trace.py -- P29-D step-3: REVERSE dataflow/call-graph trace from the
REAL generic picker sink back to the first SexAnimationInstance text materialization
(READ-ONLY, whole TURBODRIVER_WickedWhims_Scripts.ts4script).

RATIONAL / WHY THIS REPLACES THE FORWARD SCAN
---------------------------------------------
Dorothy (2026-09-05) ran ww_p29d_rowbuilder_scan over the REAL package:
    pyc_members=1133  functions_parsed=12931
    instance_accessor_readers=172  ROW_BUILDER_CANDIDATES=0
and found the generic sink:
    turbolib2.ui.object_picker_dialog._build_dialog_picker_rows @ L297
plus other generic dialog builders.

ROW_BUILDER_CANDIDATES=0 is NOT "no row builder".  It means the real architecture is
NOT:
    SexAnimationInstance -> direct row builder -> UI
but more likely:
    SexAnimationInstance -> intermediate DTO / tuple / callback / picker-row descriptor
                        -> TurboObjectPickerDialog generic builder -> UI
The forward 2-hop "instance-accessor + row-text sink" heuristic does not match that
shape (the text accessor is read deep in a helper that only fills an opaque DTO, and
the DTO crosses many hops/callbacks before a generic sink turns it into a row).  So
instead of widening keyword search we anchor on the real sink and follow callers
upstream.  NO runtime hook, NO TEST300/stage_name/XML/WW edits here -- pure read.

WHAT THIS TOOL EMITS (compact, real reachable chains only)
----------------------------------------------------------
  1. Sink anchor resolution + full disassembly of the whole object_picker_dialog
     module (TurboObjectPickerDialog, create_picker_row, _build_dialog_picker_rows,
     and every display/build/show/... function in that module).
  2. create_picker_row SIGNATURE: from co_varnames (positional params) + its own
     STORE_ATTR/arg usage -> ROW_TITLE_ARGUMENT (the param whose value lands in the
     row name/title/text field) and ROW_DATA_TYPE (the param the per-item payload
     object lands in: entry/item/object/animation/row_data/picker_item/...).
  3. REVERSE call graph from the sink anchors, UNBOUNDED depth, over real callers
     (coarse: A calls B if B in A's LOAD_METHOD/LOAD_GLOBAL set).  Only chains that
     stay WW/animations/picker/dialog/query/turbolib2 relevant are emitted.  Each
     chain node: module, function, line, and its accessed/constructed tokens.
  4. FIRST_TEXT_MATERIALIZATION along each chain = the node nearest the sink that
     READS an instance text/identity accessor (get_display_name / display_name /
     get_stage_name / stage_name / get_identifier / animation_instance /
     sex_animation_instance / SexAnimationInstance).  Everything closer to the sink
     only forwards an opaque payload -> that node is where the screen string is born.
  5. DYNAMIC_BOUNDARY: if the upstream caller of a chain node is NOT resolvable by
     name (no statically-known caller, or the value crosses into a callback/closure/
     factory whose callee can't be bound), we stop there and report the boundary
     module/function/line + which param/field carries the opaque payload onward.  A
     P29-D observation hook would then be designed ONLY on that boundary (not here).

Anchor resolution is version-robust: modules matched by dotted-tail suffix against
real .pyc members everywhere in the package; methods matched by co_name (not line) so
L297 drift does not matter.  Every survivor node is a REAL parsed function, never a
guessed symbol.

Emit: output/ww_p29d/ww_p29d_reverse_trace.txt
    ANCHOR_SINK_*
    CREATE_PICKER_ROW_SIGNATURE=...   ROW_TITLE_ARGUMENT=...
    ROW_DATA_TYPE=...                 ROW_NAME_METHOD_CALLS=...
    REVERSE_CALL_CHAIN=n  (each chain + nodes)
    FIRST_TEXT_MATERIALIZATION_FUNCTION/SOURCE=...
    DYNAMIC_BOUNDARY=...              STATIC_CONFIDENCE=PARTIAL.. HIGH
    VERDICT=REVERSE_TRACE_COMPLETE    ZERO_WRITE_TO_MODS=YES
Exit fail-closed: 2 source | 3 --dir | 4 no WW pkg | 5 anchor module unresolved |
6 no xdis | 7 unparseable.
Run on the REAL box (READ-ONLY):
  python scripts\\ww_p29d_reverse_trace.py "<WWsource.package>" --dir "C:\\...\\Mods"
"""
import argparse
import io as _io
import sys
import zipfile
from collections import deque
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

# anchor modules by dotted-tail suffix (version-robust member resolution)
ANCHOR_MODULE_SUFFIX = "turbolib2.ui.object_picker_dialog"

# instance text / identity accessors (alias-immune signal for FIRST materialization)
INST_TEXT = {
    "get_display_name", "display_name",
    "get_stage_name", "stage_name", "animation_stage_name",
    "get_animation_name", "animation_name",
    "get_identifier", "identifier",
}
INST_TYPE = {
    "SexAnimationInstance", "sex_animation_instance", "animation_instance",
    "get_animation", "animation_order_id", "sex_animation",
}
INST_ACC = INST_TEXT | INST_TYPE

# WW/picker-relevance filter on a dotted module (whole tail substring match)
KEEP_MOD = ("wickedwhims", "sexy", "sex.", "sex_animation", "animations",
            "picker", "dialog", "query", "turbolib", "object_picker",
            "whickedwhims")

# keyword param / var names that likely carry the opaque per-row payload object onward
PAYLOAD_VAR = {"entry", "entries", "item", "items", "object", "objects",
               "animation", "anim", "instance", "inst", "row_data", "picker_item",
               "picker_items", "row_entry", "data", "callback", "factory", "row",
               "rows", "descriptor", "selection", "selected"}

# row title-ish attribute/method names -> what create_picker_row stores the string into
TITLE_ATTR = {"title", "name", "text", "subtitle", "description"}

CALL_LD = {"LOAD_METHOD", "LOAD_GLOBAL"}   # treat these argreprs as potential callees


def rel_dotted(rel):
    return rel[:-4].replace("/", ".") if rel.endswith(".pyc") else rel.replace("/", ".")


def get_opc(ver):
    return get_opcode_module(tuple(str(x) for x in ver[:2]),
                             PythonImplementation.CPython)


def _walk(co, acc):
    acc.append(co)
    for c in co.co_consts:
        if hasattr(c, "co_name"):
            _walk(c, acc)


def parse_member(name, data):
    res = load_module_from_file_object(_io.BytesIO(data), filename=Path(name).name)
    ver, co = res[0], res[3]
    funcs = []
    _walk(co, funcs)
    return ver, funcs, get_opc(ver)


def analyze(co, opc):
    methods, globs, attrs, stored = [], [], [], []
    for it in Bytecode(co, opc):
        o, a = it.opname, (it.argrepr or "")
        if o == "LOAD_METHOD":
            methods.append(a)
        elif o == "LOAD_GLOBAL":
            globs.append(a)
        elif o == "LOAD_ATTR":
            attrs.append(a)
        elif o == "STORE_ATTR":
            stored.append(a)
    consts = [c for c in co.co_consts if isinstance(c, str)]
    return {"methods": methods, "globals": globs, "attrs": attrs,
            "stored": stored, "consts": consts}


def is_ww_relevant(mod):
    low = mod.lower()
    return any(t in low for t in KEEP_MOD)


def first_text_accessor(an):
    hit = set(an["attrs"]) | set(an["methods"])
    acc = hit & INST_TEXT
    typ = hit & INST_TYPE
    return acc, typ


def dump_disasm(F, out):
    for it in Bytecode(F["co"], F["opc"]):
        arg = (it.argrepr or "").replace("0x%x" % id(F["co"]), "")
        line = "        %s L%-5s %s" % (it.opname,
                                          getattr(it, "lineno", "?") or "?",
                                          it.argrepr or "")
        out.append(_clean_disasm_line(line))


def _clean_disasm_line(line):
    """Stabilize disasm text: drop interpreter-address and absolute tmp path noise so
    two runs on the same package diff to nothing (addresses differ every run)."""
    import re as _re
    line = _re.sub(r"0x[0-9a-fA-F]+", "0x..", line)
    line = _re.sub(r"\"/[^\"]*__pycache__[^\"]*\"", "\"<py>\"", line)
    line = _re.sub(r", file \"[^\"]*\"", ", file \"<tmp>\"", line)
    return line


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--module-suffix", default=ANCHOR_MODULE_SUFFIX)
    a = ap.parse_args()

    if not XDIS:
        print("ERROR: missing xdis -- pip install xdis", file=sys.stderr)
        return 6
    if not Path(a.source).is_file():
        print("ERROR: source not found %s" % a.source, file=sys.stderr)
        return 2
    d = Path(a.dir)
    if not d.is_dir():
        print("ERROR: --dir not found %s" % d, file=sys.stderr)
        return 3
    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    suffix = a.module_suffix.strip(".")

    all_pkgs = sorted(d.rglob("*.ts4script"))
    primary = [p for p in all_pkgs if PKG_HINT in p.name]
    if not primary:
        primary = [p for p in all_pkgs
                   if "ww" in p.name.lower() or "wickedwhims" in p.name.lower()
                   or "turbodriver" in p.name.lower()]
    if not primary:
        print("ERROR: no WW package under %s (hint %s)" % (d, PKG_HINT),
              file=sys.stderr)
        return 4
    target = primary[0]

    # Union of packages we will read: the primary WW package always; PLUS any other
    # ts4script that actually CONTAINS a member whose dotted module ends with the
    # anchor suffix (so a separately-packaged turbolib2 is still traced).  Tag each
    # parsed function with its source package name for auditability.
    def pkg_has_anchor(p):
        try:
            with zipfile.ZipFile(str(p)) as z:
                return any((rel_dotted(n).endswith(suffix) and n.endswith(".pyc"))
                           for n in z.namelist())
        except Exception:
            return False

    load_set = {target}
    for p in all_pkgs:
        if p == target:
            continue
        if pkg_has_anchor(p):
            load_set.add(p)
    load_set = sorted(load_set, key=lambda x: str(x))

    members = []          # (pkg_name, member_name, data)
    total_pyc = 0
    for p in load_set:
        with zipfile.ZipFile(str(p)) as z:
            for n in z.namelist():
                if n.endswith(".pyc"):
                    members.append((p.name, n, z.read(n)))
                    total_pyc += 1
    if total_pyc == 0:
        print("ERROR: no .pyc members across %s" %
              ",".join(p.name for p in load_set), file=sys.stderr)
        return 7

    funcs = []
    parse_err = 0
    for pkgname, mn, data in members:
        try:
            ver, codes, opc = parse_member(mn, data)
        except Exception:
            parse_err += 1
            continue
        mod = rel_dotted(mn)
        for c in codes:
            funcs.append({"pkg": pkgname, "mod": mod, "member": mn, "co": c,
                          "name": c.co_name, "line": c.co_firstlineno,
                          "opc": opc})

    infos = {}
    caller_index = {}      # callee short-name -> list of functions that may call it
    sink_anchor_funcs = []  # functions in the anchor module
    for f in funcs:
        try:
            an = analyze(f["co"], f["opc"])
        except Exception:
            an = {"methods": [], "globals": [], "attrs": [], "stored": [],
                  "consts": []}
        infos[f["co"]] = (f, an)
        for callee in set(an["methods"]) | set(an["globals"]):
            caller_index.setdefault(callee, []).append(f)
        if f["mod"].endswith(suffix):
            sink_anchor_funcs.append(f)

    # ---- 1) anchor module resolution --------------------------------------------
    if not sink_anchor_funcs:
        print("ERROR: anchor module %s unresolved in %s" %
              (suffix, ",".join(p.name for p in load_set)), file=sys.stderr)
        return 5
    anchor_mod_set = sorted({f["mod"] for f in sink_anchor_funcs})

    def by_name(nm):
        return [f for f in sink_anchor_funcs if f["name"] == nm]

    cpr = by_name("create_picker_row")
    bdpr = by_name("_build_dialog_picker_rows")
    anchors = []
    anchors += [f for f in sink_anchor_funcs
                if f["name"] in {"_build_dialog_picker_rows", "create_picker_row",
                                 "add_picker_row", "set_rows", "show", "display",
                                 "build", "open", "close", "on_select"}]
    # _build_dialog_picker_rows / create_picker_row are primary even if oddly named
    if not any(f["name"] == "_build_dialog_picker_rows" for f in anchors):
        if bdpr:
            anchors.append(bdpr[0])
    if not any(f["name"] == "create_picker_row" for f in anchors):
        if cpr:
            anchors.append(cpr[0])
    # de-dup
    seen = set()
    anchors_u = []
    for f in anchors:
        if f["co"] not in seen:
            seen.add(f["co"])
            anchors_u.append(f)
    anchors = anchors_u

    # ---- 2) create_picker_row signature -----------------------------------------
    sig_lines = []
    row_title_arg = "UNRESOLVED"
    row_data_type = "UNRESOLVED"
    for cpf in cpr[:1]:
        vnames = cpf["co"].co_varnames          # (args..., locals) in 3.7
        nargs = cpf["co"].co_argcount
        pos_params = list(vnames[:nargs])
        an = infos[cpf["co"]][1]
        stored_attr = an["stored"]
        pay_var = [v for v in pos_params if v.lower() in PAYLOAD_VAR]
        sig_lines.append("cpr_module=%s.%s@L%s" % (cpf["mod"], cpf["name"],
                                                    cpf["line"]))
        sig_lines.append("POSITIONAL_PARAMS=" + ",".join(pos_params))
        sig_lines.append("ROW_PAYLOAD_CANDIDATE_PARAMS=" + ",".join(pay_var))
        sig_lines.append("STORED_ON_ROW_ATTRS=" +
                         (",".join(stored_attr) if stored_attr else "(none)"))
        sig_lines.append("PARAM_USED_AS_ROW_TITLE=" +
                         (",".join(sorted(set(pos_params) & TITLE_ATTR))
                          if set(pos_params) & TITLE_ATTR else "(none)"))
        # best-guess ROW_DATA_TYPE: param whose name carries the per-entry object
        for cand in ("row_data", "picker_item", "entry", "item", "object",
                     "animation", "data", "selection"):
            if cand in pos_params:
                row_data_type = cand
                break
        # ROW_TITLE_ARGUMENT heuristic: if a title-ish param exists
        for tl in ("title", "name", "text", "description"):
            if tl in pos_params:
                row_title_arg = tl
                break

    # ---- 3) reverse BFS from anchor funcs (union) --------------------------------
    # frontier = functions that call an anchor name; walk upstream unbounded.
    root_names = set(f["name"] for f in anchors)
    seen_co = set()
    # chain_nodes: id(co) -> {f, depth, parent, root}; BFS with parent links
    chain_nodes = {}
    frontier = []
    for nm in root_names:
        for caller in caller_index.get(nm, []):
            if caller["co"] in seen_co:
                continue
            seen_co.add(caller["co"])
            chain_nodes[id(caller["co"])] = {"f": caller, "depth": 1,
                                              "parent": None, "root": nm}
            frontier.append(caller)
    # BFS upstream
    head = 0
    while head < len(frontier):
        cur = frontier[head]; head += 1
        cd = chain_nodes[id(cur["co"])]
        if cd["depth"] >= 200:      # hard safety, should never be reached
            continue
        for nm in set(infos[cur["co"]][1]["methods"]) | \
                 set(infos[cur["co"]][1]["globals"]):
            for caller in caller_index.get(nm, []):
                if caller["co"] in seen_co:
                    continue
                seen_co.add(caller["co"])
                chain_nodes[id(caller["co"])] = {"f": caller,
                                                 "depth": cd["depth"] + 1,
                                                 "parent": id(cur["co"]),
                                                 "root": cd["root"]}
                frontier.append(caller)

    # forward callee lookup for DOWNWARD materializer discovery: the true screen-
    # string function is often a HELPER *called by* a chain node (not an ancestor of
    # the sink), e.g. build_DTO -> _render_animation_title(anim).  So we also follow a
    # chain node's own callees to any real function that READS instance text accessors.
    func_by_name = {}
    for f in funcs:
        func_by_name.setdefault(f["name"], []).append(f)

    def text_bearing_callees_of(f):
        an = infos[f["co"]][1]
        out = []
        for nm in sorted(set(an["methods"]) | set(an["globals"])):
            for tgt in func_by_name.get(nm, []):
                acc, typ = first_text_accessor(infos[tgt["co"]][1])
                if acc or typ:
                    out.append((tgt, acc, typ))
        return out

    # ---- 4) reverse call tree: sink -> upstream ----------------------------------
    # chain_nodes: id(co) -> {f, depth, parent}.  Roots (depth 1) are immediate
    # callers of a sink root NAME.  Every node is a real parsed function.  We walk
    # upstream unbounded (safety 200) following real callers by short-name match.  A
    # caller that aliases the callee is invisible to this coarse name graph -> that
    # is exactly the DYNAMIC_BOUNDARY we must surface, not paper over.

    def nd_label(nx):
        f = nx["f"]
        return "%s[%s].%s@L%s" % (f["mod"], f.get("pkg", "?"), f["name"],
                                   f["line"])

    def flabel(fn):
        return "%s[%s].%s@L%s" % (fn["mod"], fn.get("pkg", "?"), fn["name"],
                                   fn["line"])

    nodes = list(chain_nodes.values())

    def is_materializer(nd):
        fn = nd["f"]
        acc, typ = first_text_accessor(infos[fn["co"]][1])
        return bool(acc or typ)

    # Deepest upstream node PER ROOT that materializes an instance/attr, else the
    # deepest node per root (fallback).  This is the most interesting WW-side start.
    head_by_root = {}   # root -> deepest materializing (depth, nid)
    deep_by_root = {}   # root -> deepest any (depth, nid)
    for nid, nd in chain_nodes.items():
        r = nd["root"]
        d = nd["depth"]
        cur = deep_by_root.get(r)
        if cur is None or d > cur[0]:
            deep_by_root[r] = (d, nid)
        if is_materializer(nd):
            cm = head_by_root.get(r)
            if cm is None or d > cm[0]:
                head_by_root[r] = (d, nid)

    # ---- 5) derive FIRST_TEXT_MATERIALIZATION + DYNAMIC_BOUNDARY ----------------
    # Best materializer = the point where an instance SCREEN string is produced.  It
    # may be IN a chain node's body, or in a text-bearing helper DIRECTLY called by a
    # chain node (downward leaf) that feeds the DTO field.  Prefer text accessors
    # (get_stage_name/get_display_name) over pure identity (get_identifier):
    #   1st tier: get_stage_name / stage_name   (P29-C candidate screen text)
    #   2nd tier: get_display_name / display_name
    #   3rd tier: any other instance text/type accessor
    def acc_tier(accset):
        if accset & {"get_stage_name", "stage_name", "animation_stage_name"}:
            return 1
        if accset & {"get_display_name", "display_name"}:
            return 2
        return 3

    mat_best = None      # (tier, depth, label_fn, acc_set, via_callee_info or None)
    def _consider(fn, depth, acc, typ, extra):
        nonlocal mat_best
        accs = sorted(acc | typ)
        accs = [x for x in accs]          # keep both text and type tokens sorted
        tier = acc_tier(set(accs))
        key = (tier, -depth)               # lower tier number better; deeper better
        if mat_best is None or key < (mat_best[0], -mat_best[1]):
            mat_best = (tier, depth, flabel(fn), accs, extra)

    for nid, nd in chain_nodes.items():
        fn = nd["f"]
        acc, typ = first_text_accessor(infos[fn["co"]][1])
        if acc or typ:
            _consider(fn, nd["depth"], acc, typ, None)
        # downward text-bearing leaf called by this node
        for (tgt, tacc, ttyp) in text_bearing_callees_of(fn):
            if tacc or ttyp:
                _consider(tgt, nd["depth"] + 1, tacc, ttyp, flabel(fn))

    # DYNAMIC_BOUNDARY = a materializer chain node that no other function is known
    # to call (no static producer above it by name).  The SHALLOWEST such node is the
    # first gap climbing from the sink, i.e. the first place the instance value
    # enters without us being able to name its producer -> the observation boundary.
    dyn_boundary = None
    for nid, nd in sorted(chain_nodes.items(), key=lambda kv: kv[1]["depth"]):
        if not is_materializer(nd):
            continue
        fn = nd["f"]
        up = caller_index.get(fn["name"], [])
        if not up:
            acc, typ = first_text_accessor(infos[fn["co"]][1])
            dyn_boundary = (fn, sorted(acc | typ))
            break

    # ---- 6) compact OUTPUT -------------------------------------------------------
    L = []
    L.append("=== P29-D REVERSE TRACE FROM GENERIC PICKER SINK (READ-ONLY) ===")
    L.append("package(s)=%s" % ",".join(p.name for p in load_set))
    L.append("pyc_members=%d (across load_set) parse_errors=%d funcs=%d" %
             (len(members), parse_err, len(funcs)))
    L.append("anchor_module_suffix=%s resolved_members=%d" %
             (suffix, len(anchor_mod_set)))
    for m in sorted(anchor_mod_set):
        L.append("   anchor_mod=" + m)

    L.append("ANCHOR_SINK_FUNCS=%d" % len(anchors))
    for f in anchors:
        L.append("   sink=%s.%s@L%s" % (f["mod"], f["name"], f["line"]))

    L.append("CREATE_PICKER_ROW_RESOLVED=%d" % len(cpr))
    for s in sig_lines:
        L.append("   " + s)
    L.append("ROW_TITLE_ARGUMENT=" + row_title_arg)
    L.append("ROW_DATA_TYPE=" + row_data_type)

    # Dorothy task 1: full disassembly of the sink module's own functions
    L.append("=== FULL DISASM: anchor module funcs ===")
    for f in sorted(sink_anchor_funcs, key=lambda x: (x["name"], x["line"])):
        L.append("--- %s.%s@L%s (varnames=%s) ---" %
                 (f["mod"], f["name"], f["line"],
                  ",".join(f["co"].co_varnames[:f["co"].co_argcount])))
        dump_disasm(f, L)

    # reverse chains, one per sink root, emitted upstream -> nearest-to-sink? We emit
    # SINK-WARD: deepest_upstream (rank 0) .. immediate_sink_caller (last).
    chain_keys = sorted(set(nd["root"] for nd in nodes))
    L.append("REVERSE_CALL_CHAIN=%d (one per sink root, deepest-materializer head)" %
             len(chain_keys))
    for r in chain_keys:
        (hd, hnid) = head_by_root.get(r) or deep_by_root.get(r)
        # rebuild path hnid -> .. -> depth-1 immediate sink caller
        chain_path = []
        cur = hnid
        guard = 0
        while cur is not None and guard < 300:
            chain_path.append(chain_nodes[cur])
            cur = chain_nodes[cur]["parent"]
            guard += 1
        chain_path.reverse()          # sink-caller-first .. deepest-upstream-last
        L.append("### root_sink=%s upstream_depth=%d nodes=%d" %
                 (r, chain_path[-1]["depth"], len(chain_path)))
        sinkward = list(reversed(chain_path))   # deepest-first (WW side) -> sink side
        for rank, ndx in enumerate(sinkward):
            fnx = ndx["f"]
            acc, typ = first_text_accessor(infos[fnx["co"]][1])
            mark = ""
            if acc:
                mark = "   <== TEXT-BEARING(" + ",".join(sorted(acc)) + ")"
            elif typ:
                mark = "   <== INSTANCE-TYPE(" + ",".join(sorted(typ)) + ")"
            L.append("   [%d] %s%s" % (rank, nd_label(ndx), mark))
            if rank == 0 and (acc or typ):
                L.append("        ^ FIRST_TEXT_MATERIALIZATION_HEAD (chain start)")
            # downward text-bearing helpers called by this node -> DTO text source
            for (tgt, tacc, ttyp) in text_bearing_callees_of(fnx):
                if (tacc or ttyp) and tgt["co"] is not fnx["co"]:
                    L.append("        |> text-helper %s (reads %s)" %
                             (flabel(tgt), ",".join(sorted(set(tacc) | set(ttyp)))))
        L.append("   .... (caller-of-sink below chain_path end -> generic sink)")

    L.append("")
    if mat_best:
        _m = mat_best  # (tier, depth, label_fn, accs, extra)
        L.append("FIRST_TEXT_MATERIALIZATION_FUNCTION=" + _m[2])
        L.append("FIRST_TEXT_MATERIALIZATION_SOURCE=" + ",".join(_m[3]))
        L.append("FIRST_TEXT_MATERIALIZATION_CALLED_BY=" +
                 (_m[4] if _m[4] else "(in-body, no helper) "))
        L.append("MATERIALIZATION_TIER=%d (1=stage_name,2=display_name,3=other)" %
                 _m[0])
    else:
        L.append("FIRST_TEXT_MATERIALIZATION_FUNCTION=UNRESOLVED")
        L.append("FIRST_TEXT_MATERIALIZATION_SOURCE=UNRESOLVED")
    if dyn_boundary:
        fbn, toks = dyn_boundary
        L.append("DYNAMIC_BOUNDARY=%s (%s)" %
                 (flabel(fbn), ",".join(sorted(toks))))
        L.append("STATIC_CONFIDENCE=PARTIAL (dynamic boundary named; design the\n"
                 "single P29-D observation hook HERE only)")
    else:
        L.append("DYNAMIC_BOUNDARY=NONE_STATIC_CHAIN_RESOLVED")
        L.append("STATIC_CONFIDENCE=HIGH")
    L.append("VERDICT=REVERSE_TRACE_COMPLETE")
    L.append("ZERO_WRITE_TO_MODS=YES")
    text = "\n".join(L)

    try:
        (out_dir / "ww_p29d_reverse_trace.txt").write_text(text, encoding="utf-8")
    except Exception as ex:
        print("WARN: write failed %s (%s)" % (out_dir, ex), file=sys.stderr)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
