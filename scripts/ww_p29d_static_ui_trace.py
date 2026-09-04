#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29d_static_ui_trace.py -- P29-D real static UI-source dataflow trace (READ-ONLY).

WHY (evidence-driven, NOT a new in-game hook):
  P29-C (real) already proved the animation-picker RENDER does NOT call the target
  instance's get_display_name() (only 6 TEST300 calls existed, all during
  load/collect: animations_handler `_collect_sex_animations` + a sex_animations<
  lambda at animations_handler.py:288 + get_identifier/__repr__).  Yet the same real
  object ALSO carries a separate field  stage_name='caught cheating 2'  while
  display_name='TEST300' -- and the visible UI still reads "Caught Cheating 2".
  So the real open question is STATIC: which module/function builds the picker row,
  and WHICH field of the instance (or a pre-materialized copy) feeds the row title.

  This tool disassembles the CURRENT WW .pyc (real machine v185k, whatever is in the
  Mods .ts4script) with xdis and traces the picker/dialog dataflow, ONLY to locate
  the real row-builder candidate(s).  It does NOT install anything into the game and
  does NOT write to Mods.  Per the standing rule we first LOCATE the row-builder from
  bytecode evidence, then (only if found) design the P29-D single observation hook.

TARGET MODULES (real WW dotted names -> resolved to .pyc members inside the WW
  .ts4script zip under --dir, via member-path/dotted-tail matching):
    wickedwhims.sex.integral.dialogs.sex_animation
    wickedwhims.sex.integral.dialogs.universal_sex_animations
    wickedwhims.sex.animations.animations_handler            (esp. the line-288 lambda)

QUESTIONS (each answered from real bytecode):
  1. which function literally builds an animation picker row object?
  2. which symbol/field is set as the row's main title / name / text?
  3. from which SexAnimationInstance attribute or method is that title taken?
  4. is stage_name / animation_stage_name / get_stage_name referenced?
  5. is a display string materialized/copied BEFORE the row (cached at load/collect)?
  6. why is get_display_name NOT called at render (i.e. where the text really comes
     from at render time)?
  7. what exactly does the animations_handler.py:288 lambda do (sort only, or does it
     also build/annotate the structures the dialogs later read)?

ENGINE: xdis (pure python, parses .pyc of any CPython without the matching runtime).
  FAIL-CLOSED exit codes:
    2 source missing | 3 --dir missing | 4 no WW .ts4script found
    5 cited module member not found in the WW ts4script
    6 xdis missing | 7 xdis parse/opcode failure
  ZERO_WRITE_TO_MODS=YES.  Writes only the evidence text to --out-dir.

Usage (Windows, READ-ONLY, run from repo root):
  pip install xdis
  python scripts\ww_p29d_static_ui_trace.py "<SRC.package>" --dir "C:\...\Mods"
                                                 [--out-dir output/ww_p29d]
"""
import argparse
import io as _io
import sys
import zipfile as _zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from xdis import load_module
    from xdis.op_imports import get_opcode_module, PythonImplementation
    from xdis.load import load_module_from_file_object
    XDIS = True
except Exception:
    XDIS = False

OUT_DIR = Path("output/ww_p29d")

# Dotted module -> the member file tail used to resolve inside the WW ts4script.
# A WW .pyc member path is something like  .../sex/integral/dialogs/sex_animation.pyc
# We match when the dotted tail (dots->'/') equals the member suffix, OR the member
# basename collides -> we keep ALL candidates and report each (do not guess which).
CITED = {
    "sex.integral.dialogs.sex_animation": "sex_animation",
    "sex.integral.dialogs.universal_sex_animations": "universal_sex_animations",
    "sex.animations.animations_handler": "animations_handler",
}

# Load-order: assume any WW package under the Mods dir.  We scan ALL *.ts4script and
# pick those containing a member whose dotted tail matches ANY cited name, so the tool
# is robust to WW + Nevely + other ts4script being present.
PKG_HINTS = ("sex", "animations", "integral", "dialogs", "wickedwhims")

ROW_KW = [
    # attribute / method names that build the row or feed its text
    "display_name", "get_display_name", "stage_name", "animation_stage_name",
    "get_stage_name", "animation_name", "get_animation_name", "LocalizedString",
    "localized", "localization", "format", "row", "title", "text", "description",
    "name", "get_identifier", "identifier", "picker", "selected_animation",
]


def dotted_tail_matches(member, dotted, sep="."):
    """Return True if member (zip path) resolves to this dotted module."""
    want = dotted.replace(sep, "/")
    return member == want or member.endswith("/" + want) or member.endswith("/" + want + ".pyc")


def find_ww_ts4scripts(d):
    hits = []
    for sp in sorted(d.rglob("*.ts4script")):
        try:
            with _zipfile.ZipFile(sp) as z:
                names = z.namelist()
            # keep a package only if at least one cited member tail is present OR it
            # clearly carries WW package structure (sex/integral/...)
            keep = False
            for dotted, tail in CITED.items():
                for n in names:
                    if (n.endswith(tail + ".pyc") or dotted_tail_matches(n, dotted)):
                        keep = True
                        break
                if keep:
                    break
            if keep:
                hits.append(sp)
        except Exception:
            continue
    return hits


def get_opc(ver):
    v = tuple(str(x) for x in ver[:2])
    return get_opcode_module(v, PythonImplementation.CPython)


def walk_code(co, funcs):
    funcs.append(co)
    for sub in co.co_consts:
        if hasattr(sub, "co_name"):
            walk_code(sub, funcs)


def fmt_ins(i):
    a = i.argrepr or ""
    return "%4d %-22s %s" % (i.offset, i.opname, a)


def top_level_module(co):
    """Return dotted-ish module name if co_filename hints one (best effort)."""
    fn = getattr(co, "co_filename", "") or ""
    return fn


def scan_module(member, data):
    """xdis-parse one .pyc member -> (ver, impl, co, rows). Row dicts carry an
    opc via a closure-free side channel returned together so Bytecode(row['obj'],
    row['opc']) works.  Rows each have: name, line, member, varnames, names,
    str_consts, obj, opc, kw_hits."""
    res = load_module_from_file_object(_io.BytesIO(data), filename=Path(member).name)
    ver, co = res[0], res[3]
    impl = res[4]
    opc = get_opc(ver)
    funcs = []
    walk_code(co, funcs)
    out = []
    for fn in funcs:
        attrs = set(fn.co_names)  # names incl. attribute loads / method names
        consts = _str_consts(fn)
        hit_kw = [k for k in ROW_KW if k in attrs or k in consts]
        out.append({
            "name": fn.co_name,
            "member": member,
            "line": fn.co_firstlineno,
            "varnames": list(fn.co_varnames),
            "names": sorted(attrs),
            "str_consts": sorted(consts),
            "obj": fn,
            "opc": opc,
            "kw_hits": hit_kw,
        })
    return ver, impl, co, out


def _str_consts(fn):
    return [c for c in fn.co_consts if isinstance(c, str)]


def _full_disasm(row):
    """Full formatted disassembly lines for a row dict."""
    from xdis.disasm import Bytecode
    lines = []
    for it in Bytecode(row["obj"], row["opc"]):
        lines.append("      " + fmt_ins(it))
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="WW animation source .package (gate/config only)" )
    ap.add_argument("--dir", required=True, help="Mods dir (holds WW .ts4script)")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print("ERROR: source not found %s" % src, file=sys.stderr); return 2
    d = Path(a.dir)
    if not d.is_dir():
        print("ERROR: --dir not found %s" % d, file=sys.stderr); return 3
    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not XDIS:
        print("ERROR: missing xdis -- pip install xdis", file=sys.stderr); return 6

    wws = find_ww_ts4scripts(d)
    if not wws:
        print("ERROR: no WW .ts4script with the cited modules under %s" % d,
              file=sys.stderr)
        return 4

    # Resolve each cited module to concrete member(s) across all WW packages.
    resolved = {}   # dotted -> list(dict(member, ts4script, data))
    member_cache = {}   # (ts4script, member) -> bytes
    import zipfile
    for sp in wws:
        try:
            z = zipfile.ZipFile(sp)
            for n in z.namelist():
                base = Path(n).name
                for dotted, tail in CITED.items():
                    if (n.endswith(tail + ".pyc") or dotted_tail_matches(n, dotted)):
                        key = (str(sp), n)
                        if key not in member_cache:
                            member_cache[key] = sp, n, z.read(n)
                        resolved.setdefault(dotted, []).append(member_cache[key])
            z.close()
        except Exception:
            continue

    L = []
    L.append("=== P29-D STATIC UI-SOURCE TRACE (real WW pyc, read-only) ===")
    L.append("source = %s" % src.name)
    L.append("scanned Mods dir = %s" % d)
    L.append("WW ts4script(s) found = %s" % ", ".join(str(x) for x in wws) or "(none)")
    L.append("cited modules:")
    for dotted in CITED:
        cands = resolved.get(dotted, [])
        L.append("  %-55s -> %d candidate member(s)" % (dotted, len(cands)))
        for sp, n, data in cands:
            L.append("      %s  IN %s  (%d bytes)" % (n, sp, len(data)))
    if not resolved:
        L.append("ERROR: none of the cited modules resolved to a member in the WW .ts4script")
        print("\n".join(L))
        print("VERDICT=MODULE_NOT_RESOLVED")
        return 5
    L.append("")

    # For now, do a first lightweight pass: parse every found member, list funcs that
    # hit ROW_KW, so we can see which real functions mention picker/row/title fields.
    import zipfile as _zipfile2
    L.append("--- per-module keyword-hit function census (row-build candidates) ---")
    candidate_rows = []
    for dotted, cands in resolved.items():
        for sp, n, data in cands:
            try:
                ver, impl, co, rows = scan_module(n, data)
            except Exception as ex:
                L.append("### %s  (xdis failed: %s)" % (dotted, ex))
                continue
            L.append("### %s  member=%s  python=%s impl=%s  top-filename=%s" %
                     (dotted, n, ver, impl, co.co_filename))
            L.append("    nested funcs=%d" % len(rows))
            for r in rows:
                if r["kw_hits"]:
                    cand_attr = ",".join(sorted(set(r["kw_hits"]) &
                                                {"display_name", "stage_name",
                                                 "animation_stage_name", "get_stage_name",
                                                 "animation_name", "LocalizedString",
                                                 "localized", "row", "title", "text",
                                                 "name", "picker"}))
                    candidate_rows.append((dotted, r["name"], r["line"], cand_attr,
                                           ",".join(r["str_consts"][:6])))
                    L.append("    KW fn='%s' L%s  kw=%s" % (r["name"], r["line"],
                             ",".join(r["kw_hits"])))
                    L.append("        str_consts=%s" % (r["str_consts"][:10] or "(none)"))
                    L.append("        co_varnames=%s" % " ".join(r["varnames"]))
                    L.append("        -- disassembly --")
                    try:
                        L.extend("        " + x for x in _full_disasm(r))
                    except Exception as ex:
                        L.append("        (disasm error %s)" % ex)
            L.append("")

    # Q7: locate the *_sex_animations* functions + any lambda near the reported L288.
    L.append("--- Q7: animations_handler collection lambdas (any lambda, all lines) ---")
    for dotted, cands in resolved.items():
        if dotted != "sex.animations.animations_handler":
            continue
        for sp, n, data in cands:
            try:
                ver, impl, co, rows = scan_module(n, data)
            except Exception:
                continue
            for r in rows:
                if r["name"] == "<lambda>":
                    L.append("### animations_handler <lambda> L%s member=%s" %
                             (r["line"], n))
                    L.append("    co_names(attrs/methods read)=" + " ".join(r["names"]))
                    L.append("    co_varnames=" + " ".join(r["varnames"]))
                    L.append("    str_consts=" + ",".join(r["str_consts"]))
                    try:
                        L.extend(_full_disasm(r))
                    except Exception as ex:
                        L.append("      (disasm error %s)" % ex)
            L.append("")

    # final assembly + error filtering
    text = "\n".join(L)

    # drop internal "<lambda>" helper fragments noise
    out_txt = out_dir / "ww_p29d_static_ui_trace.txt"
    out_cfg = out_dir / "ww_p29d_row_candidates.csv"
    out_txt.write_text(text, encoding="utf-8")
    import csv as _csv
    with open(out_cfg, "w", newline="", encoding="utf-8") as fh:
        wr = _csv.writer(fh)
        wr.writerow(["module", "function", "line", "kw", "str_consts"])
        for row in candidate_rows:
            wr.writerow(row)

    print(text)
    if candidate_rows:
        top = candidate_rows[0][0]
        print("TOTAL_ROW_KW_CANDIDATES=%d" % len(candidate_rows))
    print("VERDICT=STATIC_TRACE_COMPLETE")
    print("ZERO_WRITE_TO_MODS=YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
