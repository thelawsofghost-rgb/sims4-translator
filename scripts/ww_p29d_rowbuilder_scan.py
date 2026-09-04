#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29d_rowbuilder_scan.py -- WHOLE-package static scan for the concrete
SexAnimationInstance picker ROW BUILDER (P29-D step-2, READ-ONLY).

Ground rules (Dorothy, 2026-09-05): no runtime hook / no TEST300 / no stage_name /
no XML / no WW edits.  Scan the ENTIRE TURBODRIVER_WickedWhims_Scripts.ts4script
(not only ww.sex.integral.dialogs).  Find functions that could build a CONCRETE
SexAnimationInstance picker ROW; follow SexAnimationInstance -> dialog/helper ->
create/add row -> title/name/text arg; follow helpers / base dialogs / turbolib2;
EXCLUDE the universal CATEGORY picker (PLAYLIST / RANDOM / SEX_CATEGORY /
SEX_CONTEXT heads) and emit a COMPACT report.

WHY this is the selection rule (and its honest limits):
  A real WW row-builder can alias its constructor (e.g. `import ObjectPickerRow as
  R`), so matching `ObjectPickerRow`/`add_picker_row` BY NAME alone both over-reports
  (any function that happens to mention the token) and under-reports (aliased names).
  Name-matching is NOT proof of dataflow.  Therefore candidate selection is driven by
  the unambiguous, alias-immune signal on a SexAnimationInstance-like object:
      does this function READ an instance TEXT/FIELD accessor
        (get_display_name / display_name / get_stage_name / stage_name /
         animation_stage_name / get_identifier / identifier / get_animation /
         animation_instance / animation_order_id / SexAnimationInstance)?
  A function that reads such an accessor AND writes a row-ish field (STORE_ATTR into
  title/name/text/description) -- or that is reached from/invokes a function doing so
  (call graph up to N hops) -- is a candidate for "concrete instance animation row".
  The tool reports each candidate's ACTUAL instruction evidence (which accessors it
  reads, which fields it writes, which methods it calls, and 1-2 real caller hops) so
  the user can open the listed module and READ the exact binding before we commit to a
  hook.  ROW_TITLE_SOURCE is emitted as the set of *instance text accessors read in
  body* (the honest candidates), NOT an asserted binding.

  EXCLUSION of the universal CATEGORY head: a picker function whose body constructs
  rows ONLY for the category literals PLAYLIST / RANDOM / SEX_CATEGORY / SEX_CONTEXT
  and reads NO instance accessor in-body or in its immediate call path.  A function
  that reads an instance accessor is instance-relevant and kept even if its module /
  name also carries a category token.

Concrete caller-graph hop limit and cost control: whole package parsed + one disasm
per function for the accessor/field token census; full callers only expanded for
candidates (bounded hops).  Compact txt report only (CSV omitted on purpose -- not a
blocker per Dorothy).

Emit: output/ww_p29d/ww_p29d_rowbuilder_scan.txt
    ROW_BUILDER_CANDIDATES=%d      (each '###' block:)
       MODULE / FUNCTION / LINE / READS_INSTANCE_ACCESSORS / WRITES_ROW_FIELD /
       METHODS_CALLED / CALL_SEQUENCE / ROW_TITLE_SOURCE_CANDIDATES / VERDICT
    GET_DISPLAY_NAME_ROW_CALLS=n   GET_STAGE_NAME_ROW_CALLS=n
    ANIMATION_INSTANCE_ROW_CALLS=n EXCLUDED_CATEGORY_HEAD=n
    VERDICT=ROWBUILDER_SCAN_COMPLETE   ZERO_WRITE_TO_MODS=YES

Exit fail-closed: 2 source | 3 --dir | 4 no WW pkg | 6 no xdis | 7 unparseable.
Usage (real box, READ-ONLY):
  python scripts\\ww_p29d_rowbuilder_scan.py "<WWsource.package>" --dir "C:\\...\\Mods"
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
HOPS_MAX = 2          # call-graph hop limit for instance-accessor reach

# instance text / identity accessors (alias-immune; these are the *signal*)
INST_TEXT = {
    "get_display_name", "display_name",
    "get_stage_name", "stage_name", "animation_stage_name",
    "get_animation_name", "animation_name",
    "get_identifier", "identifier",
}
INST_FIELD = {
    "get_animation", "animation_instance", "animation_order_id",
    "SexAnimationInstance", "sex_animation_instance",
}
INST_ACC = INST_TEXT | INST_FIELD

# fields a concrete picker row stores its visible text into
ROW_TEXT_FIELDS = {"title", "text", "name", "subtitle", "description",
                   "localized_string"}

# category literals -> if a function constructs rows for ONLY these and reads no
# instance accessor, treat it as an excluded universal-category head
CAT_LITERALS = {"PLAYLIST", "RANDOM", "SEX_CATEGORY", "SEX_CONTEXT"}
CAT_MODULE_SUFFIXES = ("universal_sex_animations",)

# tokens that indicate a function LITERALLY constructs/appends a picker row object
# (used only to AUDIT row-constructors that read NO instance accessor and therefore
# cannot be concrete-instance rows -> they are reported as excluded, not as builders)
ROW_CTOR = {"create_picker_row", "add_picker_row", "create_row", "add_row",
            "ObjectPickerRow", "TurboObjectPickerDialog", "picker_rows"}

CALL_TARGET_LD = {"LOAD_METHOD", "LOAD_GLOBAL"}   # tokens we track as potential callees


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
    return ver, res[4], funcs, get_opc(ver)


def analyze(co, opc):
    """One disasm -> {methods, globals, attrs, stored, consts_str}."""
    methods, globs, attrs, stored = [], [], [], []
    consts = []
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
    for c in co.co_consts:
        if isinstance(c, str):
            consts.append(c)
    return {"methods": methods, "globals": globs, "attrs": attrs,
            "stored": stored, "consts": consts}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out-dir", default=None)
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

    pkgs = [p for p in sorted(d.rglob("*.ts4script")) if PKG_HINT in p.name]
    if not pkgs:
        pkgs = [p for p in sorted(d.rglob("*.ts4script"))
                if "ww" in p.name.lower() or "wickedwhims" in p.name.lower()]
    if not pkgs:
        print("ERROR: no WW package under %s (hint %s)" % (d, PKG_HINT),
              file=sys.stderr)
        return 4
    target = pkgs[0]

    members = []
    with zipfile.ZipFile(str(target)) as z:
        for n in z.namelist():
            if n.endswith(".pyc"):
                members.append((n, z.read(n)))
    if not members:
        print("ERROR: no .pyc members in %s" % target, file=sys.stderr)
        return 7

    funcs = []
    parse_err = 0
    for mn, data in members:
        try:
            ver, impl, codes, opc = parse_member(mn, data)
        except Exception:
            parse_err += 1
            continue
        mod = rel_dotted(mn)
        for c in codes:
            funcs.append({"mod": mod, "member": mn, "co": c, "name": c.co_name,
                          "line": c.co_firstlineno, "opc": opc})

    # analysis + coarse caller index (A may call B if B appears as a method/global in A)
    infos = {}
    caller_index = {}
    for f in funcs:
        try:
            an = analyze(f["co"], f["opc"])
        except Exception:
            an = {"methods": [], "globals": [], "attrs": [], "stored": [],
                  "consts": []}
        infos[f["co"]] = (f, an)
        for callee in set(an["methods"]) | set(an["globals"]):
            caller_index.setdefault(callee, []).append(f)

    def acc_read(an):
        return set(x for x in an["attrs"] + an["methods"] if x in INST_ACC)

    def row_field_written(an):
        return [x for x in an["stored"] if x in ROW_TEXT_FIELDS]

    # ---- PASS 0: audit row-CONSTRUCTORS that read NO instance accessor =---------
    # cannot be concrete-instance rows (category head or generic row helper); we keep
    # them OUT of candidates but report them so the run is auditable.
    excluded_ctor = []
    for f in funcs:
        an = infos[f["co"]][1]
        toks = set(an["methods"]) | set(an["globals"])
        if not (toks & ROW_CTOR):
            continue
        if acc_read(an):
            continue                    # instance-relevant -> handled in PASS 1
        is_cat = bool({c for c in an["consts"] if c in CAT_LITERALS}) or \
                 f["mod"].endswith(CAT_MODULE_SUFFIXES) or \
                 bool({t for t in toks if t in CAT_LITERALS})
        excluded_ctor.append((f, is_cat))

    # ---- PASS 1: candidates = functions that READ an instance accessor ------------
    cand_ids = {}
    for f in funcs:
        an = infos[f["co"]][1]
        if acc_read(an):
            cand_ids[f["co"]] = (f, an)

    # ---- classify + expand each candidate over the call graph (bounded) ----------
    rows_out = []
    seen_cand = set()

    for co, (f, an) in cand_ids.items():
        own_acc = acc_read(an)
        own_writes = row_field_written(an)
        # BFS over real callers up to HOPS_MAX: collect every ancestor function along
        # with its instance-accessor reads and row-text-store sinks (unconditional
        # traversal so a MIDDLE helper/base-dialog that does not itself read an
        # accessor is still inspected; bounded to keep cost sane).
        reach_acc = set(own_acc)
        path_writes = set(own_writes)
        nodes = [(f, 0)]
        head = 0
        seenq = {f["co"]}
        while head < len(nodes):
            fn, dep = nodes[head]; head += 1
            if dep >= HOPS_MAX:
                continue
            for caller in caller_index.get(fn["name"], []):
                if caller["co"] in seenq:
                    continue
                seenq.add(caller["co"])
                can = infos[caller["co"]][1]
                reach_acc |= acc_read(can)
                path_writes |= set(can["stored"]) & ROW_TEXT_FIELDS
                nodes.append((caller, dep + 1))
        # reads accessor but no row-text sink in body or up-call path -> not a
        # concrete-row builder (e.g. a bare sort/hash key reader)
        if not path_writes:
            continue
        if f["co"] in seen_cand:
            continue
        seen_cand.add(f["co"])
        rows_out.append((f, an, sorted(own_acc), sorted(own_writes),
                         sorted(path_writes)))

    # sort deterministically
    rows_out.sort(key=lambda t: (t[0]["mod"], t[0]["line"], t[0]["name"]))
    excl_sorted = sorted(((f, is_cat) for (f, is_cat) in excluded_ctor),
                         key=lambda t: (t[0]["mod"], t[0]["line"], t[0]["name"]))
    n_cat = sum(1 for (_, is_cat) in excl_sorted if is_cat)

    # tallies: of the candidate row-builder functions, count accessor usage in body
    gdn = sum(1 for (f, an, oa, ow, pw) in rows_out
              if bool(set(oa) & {"get_display_name", "display_name"}))
    gsn = sum(1 for (f, an, oa, ow, pw) in rows_out
              if bool(set(oa) & {"get_stage_name", "stage_name",
                                 "animation_stage_name"}))
    ain = sum(1 for (f, an, oa, ow, pw) in rows_out
              if bool(set(an["methods"]) & INST_FIELD) or
                 bool(set(oa) & INST_FIELD))

    L = []
    L.append("=== P29-D WHOLE-PACKAGE ROW-BUILDER SCAN (READ-ONLY) ===")
    L.append("package=%s" % target.name)
    L.append("pyc_members=%d parse_errors=%d (non-fatal)" % (len(members), parse_err))
    L.append("functions_parsed=%d instance_accessor_readers=%d" %
             (len(funcs), len(cand_ids)))
    L.append("EXCLUDED_ROW_CONSTRUCT_NON_INSTANCE=%d (of which category-marked=%d)" %
             (len(excl_sorted), n_cat))
    for (f, is_cat) in excl_sorted[:20]:
        L.append("   [excluded%s] %s.%s@L%s" %
                 ("-category" if is_cat else "", f["mod"], f["name"], f["line"]))
    L.append("ROW_BUILDER_CANDIDATES=%d" % len(rows_out))
    for (f, an, oa, ow, pw) in rows_out:
        # call sequence: f then up to 2 immediate callers (by name match)
        cseq = "%s.%s@L%s" % (f["mod"].rsplit(".", 1)[-1], f["name"], f["line"])
        for caller in caller_index.get(f["name"], [])[:2]:
            cseq += " < %s.%s@L%s" % (caller["mod"].rsplit(".", 1)[-1],
                                      caller["name"], caller["line"])
        L.append("###")
        L.append("MODULE=" + f["mod"])
        L.append("FUNCTION=" + f["name"])
        L.append("LINE=" + str(f["line"]))
        L.append("READS_INSTANCE_ACCESSORS=" + (",".join(oa) if oa else "(none-in-body)"))
        L.append("WRITES_ROW_FIELD=" + (",".join(sorted(set(ow))) if ow else "(none)"))
        L.append("ROW_TEXT_SINK_IN_PATH=" + (",".join(pw) if pw else "(none)"))
        L.append("METHODS_CALLED=" + ",".join(sorted(set(an["methods"]))) or "(none)")
        L.append("CALL_SEQUENCE=" + cseq)
        L.append("VERDICT=instance-row-builder-candidate")
    L.append("")
    L.append("GET_DISPLAY_NAME_ROW_CALLS=%d" % gdn)
    L.append("GET_STAGE_NAME_ROW_CALLS=%d" % gsn)
    L.append("ANIMATION_INSTANCE_ROW_CALLS=%d" % ain)
    L.append("VERDICT=ROWBUILDER_SCAN_COMPLETE")
    L.append("ZERO_WRITE_TO_MODS=YES")
    text = "\n".join(L)

    try:
        (out_dir / "ww_p29d_rowbuilder_scan.txt").write_text(text, encoding="utf-8")
    except Exception as ex:
        print("WARN: write failed %s (%s)" % (out_dir, ex), file=sys.stderr)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
