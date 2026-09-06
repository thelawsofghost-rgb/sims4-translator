#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29g_loader_trace.py --- P29-G2 READ-ONLY census tracer over a real WW .ts4script
(pyc native marshal), locating loader / resource / registry / collection / id /
new-animation source facts.  It NEVER concludes duplicate policy (first/last/replace/
skip/merge) or loader semantics -- it only emits keyword evidence, full disassembly
of every matched code object, 1-hop caller/reference sites, and registry mutation
opcode sites for later human audit.

Targets (non-STORY WW_ANIM_XML path, type 0x7DF2169C == int 2113017500) live in the
WW scripts ts4script under Mods\\WickedWhimsMod\\TURBODRIVER_WickedWhims_Scripts.ts4script.

Interface (real argparse -- do NOT guess parameters elsewhere):
    python.exe ww_p29g_loader_trace.py --ts4script <path> --out-dir <dir>
Optional:
    --expect-magic HEX   default "420d0d0a"; if the running interpreter's magic
                         does not equal a pyc member header magic it is skipped
                         (fail-closed per member).  The interpreter ITSELF must be
                         the native CPython that compiled the pyc (WW = 3.7.9).
    --self-fixture       do NOT require a real ts4script; marshal the current
                         interpreter's OWN module code object and run the same
                         census core.  Used ONLY by the logic test on Linux (which
                         cannot produce 3.7 magic).  Ignores --ts4script when set.
    --out-prefix NAME    report file name prefix (default "p29g").
Exit codes:
    0 = completed (reports written)
    2 = --self-fixture not set and ts4script missing/unreadable, or out-dir unwritable
    3 = no .pyc members found or archive invalid
    4 = EVERY member magic-mismatched (fail-closed; nothing semantically usable)

Read-only.  Writes ONLY under --out-dir.  Never writes to Mods / package / ts4script.
"""
import argparse
import dis
import importlib.util
import marshal
import sys
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Provenance / recursion
# ---------------------------------------------------------------------------
def magic_hex():
    return importlib.util.MAGIC_NUMBER.hex()


def read_member_bytes(ts4, member):
    with zipfile.ZipFile(str(ts4)) as z:
        try:
            return z.read(member)
        except KeyError:
            return None


def _co_name(co):
    return getattr(co, "co_name", "?")


def recursive_walk(co, path, acc):
    """acc: list of (code, nesting_path_str).  Recurses into lambda/comprehension.
    Root is labelled by its own co_name so provenance is honest
    (module.func.lambda...) whether the root is a real module frame (`<module>`)
    or (for the logic fixture) a function frame (`_outer`)."""
    if not path:
        path = getattr(co, "co_name", "") or "<module>"
    acc.append((co, path))
    for sub in getattr(co, "co_consts", ()):
        if hasattr(sub, "co_name") and isinstance(sub, type(co)):
            child = path + "." + sub.co_name if sub.co_name else path
            recursive_walk(sub, child, acc)


def _co_names_bag(co):
    return list(getattr(co, "co_names", ()))


def _str_consts_bag(co):
    return [c for c in getattr(co, "co_consts", ()) if isinstance(c, str)]


def _int_consts_bag(co):
    return [c for c in getattr(co, "co_consts", ()) if isinstance(c, int) and not isinstance(c, bool)]


# ---------------------------------------------------------------------------
# Keyword tables
# ---------------------------------------------------------------------------
KEYWORDS = {
    "RESOURCE_TYPE": [
        "WW_ANIM_XML",
        "0x7DF2169C",
        "7DF2169C",
        "2113017500",
    ],
    "DISPLAY_XML": [
        "animation_raw_display_name",
        "animation_display_name",
        "animation_stage_name",
        "animation_clip_name",
    ],
    "LOADER_RESOURCE": [
        "resource",
        "resource_key",
        "resource_manager",
        "instance",
        "package",
        "xml",
        "tuning",
        "load",
        "loader",
        "parse",
    ],
    "IDENTITY_REGISTRY": [
        "identifier_cache",
        "get_identifier",
        "identifier",
        "all_sex_animations",
        "sex_animations",
        "register",
        "registry",
        "original_instance",
        "display_name_override",
    ],
    "COLLECTION": [
        "_collect_sex_animations",
        "collect_sex_animations",
        "_cache_animations_stages_lookup",
        "cache_animations_stages_lookup",
    ],
    "ID_ORDER": [
        "animation_id",
        "animation_order_id",
        "order_id",
    ],
    "NEW_CACHE": [
        "new animation",
        "new animations",
        "animations count",
        "animation count",
        "animations_count",
        "animation_count",
        "cache",
        "cached",
    ],
}

# For caller/reference census: function names that, when referenced from another
# function's co_names/disasm, we want to capture as 1-hop callers (or REFERENCE_ONLY).
CALLER_TARGETS = [
    "_collect_sex_animations",
    "collect_sex_animations",
    "get_identifier",
    "_cache_animations_stages_lookup",
    "cache_animations_stages_lookup",
    "SexAnimationInstance",
    "sex_animation_instance",
]

# Registry mutation opcodes of interest (3.7-safe; newer opcodes added via guard).
REGISTRY_OPCODES = [
    "STORE_SUBSCR", "DELETE_SUBSCR", "BINARY_SUBSCR",
    "LIST_APPEND", "SET_ADD", "MAP_ADD",
    "STORE_ATTR", "STORE_NAME", "STORE_FAST", "STORE_DEREF", "STORE_GLOBAL",
]
# method-name attrs/loads that indicate mutation vs read of a container.
MUTATION_METHODS = ["append", "add", "update", "setdefault", "get", "pop",
                    "insert", "extend", "clear", "copy", "remove", "discard"]


def _ops(co):
    try:
        return list(dis.get_instructions(co))
    except Exception:
        return []


def _membership_ops():
    """Names that indicate a membership test for a key being added/checked. 3.7 =
    COMPARE_OP with arg 'in'; 3.9+ CONTAINS_OP.  Return set of opnames to flag."""
    names = set()
    if "COMPARE_OP" in dis.opmap:
        names.add("COMPARE_OP")
    if "CONTAINS_OP" in dis.opmap:
        names.add("CONTAINS_OP")
    return names


def op_has_arg_name(ins, name):
    """True when ins.argval string-equals name (co_names element or const)."""
    return str(ins.argval) == name


def _clean_arg(arg):
    """Strip ephemeral object addresses (e.g. 'at 0x7f...>') from disassembly arg
    repr so matched_functions output is byte-deterministic across runs/processes."""
    import re as _re
    return _re.sub(r"0x[0-9a-fA-F]+", "0xADDR", str(arg or ""))


# ---------------------------------------------------------------------------
# Core census (operates on one member's top code object + a member-label string)
# ---------------------------------------------------------------------------
def run_member_census(member, co, match_lines, func_lines, caller_lines):
    """Populate report row buffers for one member. Return per-member dict of facts."""
    objs = []
    recursive_walk(co, "", objs)
    facts = {
        "co_names_matches": set(),
        "str_const_matches": set(),
        "int_const_matches": set(),
        "matched_fn_names": set(),  # nested code paths that matched any keyword
    }
    for code, path in objs:
        name = _co_name(code).replace("<", "").replace(">", "")
        if path.startswith("."):
            path = path[1:] or name
        # name-like sources: co_names (globals/attrs) + local/free/cell var names.
        # Real WW keeps registry/collection locals (all_sex_animations, identifier,
        # sex_animations) as LOAD_FAST/STORE_FAST locals (co_varnames), NOT co_names,
        # so both must be scanned. co_varnames is reported distinctly.
        def _var_bag(co):
            out = []
            out.extend(list(getattr(co, "co_varnames", ())))
            out.extend(list(getattr(co, "co_freevars", ())))
            out.extend(list(getattr(co, "co_cellvars", ())))
            return out
        var_bag = _var_bag(code)
        # --- collect keyword matches across sources ---
        kw_matches = []  # (cat, kw, source, value)
        for cat, kws in KEYWORDS.items():
            for kw in kws:
                # int const match for the resource type
                if kw.isdigit() and int(kw) in _int_consts_bag(code):
                    kw_matches.append((cat, kw, "int_const", kw))
                # str const match
                if kw in _str_consts_bag(code):
                    kw_matches.append((cat, kw, "string_const", kw))
                # co_names match (globals/attrs)
                for n in _co_names_bag(code):
                    if n == kw or kw.lower() in n.lower():
                        kw_matches.append((cat, kw, "co_names", n))
                # local/free/cell var-name match
                for n in var_bag:
                    if n == kw or kw.lower() in n.lower():
                        kw_matches.append((cat, kw, "co_varnames", n))
        if kw_matches:
            facts["matched_fn_names"].add(path or name or "<module>")
        # --- keyword census rows ---
        for cat, kw, source, val in kw_matches:
            match_lines.append(
                "KEYWORD=%s|CAT=%s|MEMBER=%s|CODE_PATH=%s|CO_NAME=%s|MATCH_SOURCE=%s|MATCH_VALUE=%s"
                % (kw, cat, member, path or "<module>", _co_name(code), source, val))
            if source == "co_names" or source == "co_varnames":
                facts["co_names_matches"].add(val)
            elif source == "string_const":
                facts["str_const_matches"].add(val)
            elif source == "int_const":
                try:
                    facts["int_const_matches"].add(int(val))
                except ValueError:
                    pass
        # --- matched function full disassembly ---
        if kw_matches:
            names_bag = _co_names_bag(code)
            str_bag = _str_consts_bag(code)
            int_bag = _int_consts_bag(code)
            func_lines.append("=== FUNCTION ===")
            func_lines.append("MEMBER=%s" % member)
            func_lines.append("CODE_PATH=%s" % (path or "<module>"))
            func_lines.append("CO_NAMES=%s" % (names_bag or "(none)"))
            func_lines.append("STRING_CONSTS=%s" % (str_bag or "(none)"))
            func_lines.append("INT_CONSTS=%s" % (int_bag or "(none)"))
            func_lines.append("DISASSEMBLY:")
            for ins in _ops(code):
                func_lines.append("        %5d %s %s" % (ins.offset, ins.opname, _clean_arg(ins.argrepr)))
            func_lines.append("")
    return facts


# ---------------------------------------------------------------------------
# Caller / registry pass across ALL matched code objects of all members
# ---------------------------------------------------------------------------
def _iter_all_code(members_top):
    """members_top: dict member->(top_co_or_None, magic_info).  yield (member, code, path)."""
    for member, (co, _mm) in members_top.items():
        if co is None:
            continue
        objs = []
        recursive_walk(co, "", objs)
        for code, path in objs:
            yield member, code, path


def caller_census(caller_lines, members_top, matched_members):
    """1-hop static caller/reference census: any code object whose co_names/disasm
    references a CALLER_TARGETS name is reported.  Call site is proven only when a
    CALL_FUNCTION/CALL_METHOD op directly consumes a LOAD of that name within the
    argument window; otherwise REFERENCE_ONLY."""
    lines = caller_lines
    for member, code, path in _iter_all_code(members_top):
        ops = _ops(code)
        names = _co_names_bag(code)
        for t in CALLER_TARGETS:
            refs_here = [n for n in names if n == t or t.lower() in n.lower()]
            if not refs_here:
                continue
            # try to prove a real CALL: find CALL op whose preceding LOAD_* arg is t
            proven = False
            for idx, ins in enumerate(ops):
                if ins.opname.startswith(("CALL_FUNCTION", "CALL_METHOD")):
                    # walk back up to ~12 ops for a LOAD_* with arg == t
                    for j in range(max(0, idx - 12), idx):
                        p = ops[j]
                        if p.opname.startswith("LOAD_") and op_has_arg_name(p, t):
                            proven = True
                            break
                    if proven:
                        break
            kind = "CONFIRMED_CALL" if proven else "REFERENCE_ONLY"
            lines.append("TARGET=%s|%s|CALLER_MEMBER=%s|CALLER_CODE_PATH=%s"
                         % (t, kind, member, path or "<module>"))
    return lines


def registry_sites(reg_lines, members_top):
    """Emit opcode facts for registry-mutation / container sites in matched fns.
    NO semantic verdict here."""
    mops = _membership_ops()
    for member, code, path in _iter_all_code(members_top):
        ops = _ops(code)
        for ins in ops:
            in_member = ins.opname in REGISTRY_OPCODES
            is_membr = ins.opname in mops
            # method-like mutation via LOAD_ATTR/LOAD_METHOD -> store? handled below
            if in_member:
                reg_lines.append(
                    "MEMBER=%s|CODE_PATH=%s|OPCODE=%s|ARG=%s|OFFSET=%s"
                    % (member, path or "<module>", ins.opname, ins.argrepr or "", ins.offset))
            elif is_membr and str(ins.argval) in ("in", "not in", True, False, 10):
                reg_lines.append(
                    "MEMBER=%s|CODE_PATH=%s|OPCODE=%s|ARG=%s|OFFSET=%s"
                    % (member, path or "<module>", ins.opname, ins.argrepr or "", ins.offset))
            # container mutation method via LOAD_METHOD/LOAD_ATTR ... append() etc
            if ins.opname in ("LOAD_METHOD", "LOAD_ATTR") and str(ins.argval) in MUTATION_METHODS:
                reg_lines.append(
                    "MEMBER=%s|CODE_PATH=%s|OPCODE=%s|METHOD=%s|OFFSET=%s"
                    % (member, path or "<module>", ins.opname, ins.argval, ins.offset))
    return reg_lines


# ---------------------------------------------------------------------------
# Summary aggregation
# ---------------------------------------------------------------------------
def generate_summary(pycs, matched_members, facts_agg, caller_lines, reg_lines, magic):
    total = len(pycs) if pycs else 0
    matched = set()
    for k in matched_members:
        matched.add(k)
    sum_lines = []
    sum_lines.append("MAGIC_MATCH=%s" % ("YES" if magic else "NO"))
    sum_lines.append("PYC_MEMBER_COUNT=%d" % total)
    sum_lines.append("MATCHED_MEMBER_COUNT=%d" % len(matched))
    sum_lines.append("WW_ANIM_XML_LITERAL_FOUND=%s" % (
        "YES" if "WW_ANIM_XML"
        in facts_agg.get("str", set()) or "WW_ANIM_XML" in facts_agg.get("name", set())
        else "NO"))
    sum_lines.append("WW_ANIM_XML_INT_FOUND=%s" % (
        "YES" if 2113017500 in facts_agg.get("int", set()) else "NO"))
    for sym, key in [("IDENTIFIER_CACHE", "identifier_cache"),
                     ("GET_IDENTIFIER", "get_identifier"),
                     ("COLLECT_SEX_ANIMATIONS", "_collect_sex_animations"),
                     ("CACHE_STAGES_LOOKUP", "_cache_animations_stages_lookup")]:
        found = key in facts_agg.get("name", set()) or key in facts_agg.get("str", set())
        sum_lines.append("%s_FOUND=%s" % (sym, "YES" if found else "NO"))
    res_terms = facts_agg.get("name", set()) | facts_agg.get("str", set())
    res_terms = {t for t in res_terms if
                 any(k in t.lower() for k in KEYWORDS["LOADER_RESOURCE"])}
    sum_lines.append("RESOURCE_TERMS_FOUND=%s" % (",".join(sorted(res_terms)) if res_terms else "NONE"))
    sum_lines.append("REGISTRY_MUTATION_SITES=%d" % len(reg_lines))
    sum_lines.append("CALLER_REFERENCES=%d" % len(caller_lines))
    sum_lines.append("DUPLICATE_POLICY_INFERENCE=NONE (tracer emits facts only)")
    return sum_lines


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ts4script", default=None,
                    help="path to WW .ts4script (ignored when --self-fixture)")
    ap.add_argument("--out-dir", required=True,
                    help="directory to write the five report files")
    ap.add_argument("--expect-magic", default="420d0d0a",
                    help="expected pyc magic hex of the WW ts4script (default 420d0d0a)")
    ap.add_argument("--self-fixture", action="store_true",
                    help="logic-test mode: marshal THIS interpreter's own module code "
                         "object (no real ts4script / no 3.7 required)")
    ap.add_argument("--out-prefix", default="p29g")
    return ap.parse_args(argv)


def load_archive_members(ts4):
    """Return list of member names ending in .pyc inside the zip (read-only)."""
    pyc_members = []
    with zipfile.ZipFile(str(ts4)) as z:
        for n in z.namelist():
            if n.endswith(".pyc"):
                pyc_members.append(n)
    return pyc_members


def self_fixture_top():
    """Deterministic native code object for logic-test / Linux (no 3.7 needed).
    Carries nested code objects, co_names, string const, int 2113017500, the
    caller/reference targets the audit cares about, and registry-mutation markers,
    so the logic test can positively assert each discovery capability."""
    def _outer():
        identifier_cache = {}                 # dict insert + identifier_cache varname
        _collect_sex_animations = []          # call/reference target
        _cache_animations_stages_lookup = []  # call/reference target

        def _nested(_x):
            _all = _collect_sex_animations
            _all.append("animation_raw_display_name")      # append + str const
            identifier_cache.update({_x: 2113017500})       # update + int 2113017500
            identifier_cache["_k"] = 1                      # STORE_SUBSCR
            del identifier_cache["_k"]                      # DELETE_SUBSCR
            return identifier_cache.get("WW_ANIM_XML", 0), \
                   getattr(identifier_cache, "get_identifier", lambda: None)()

        _inner = [_nested(i) for i in range(3)]             # comprehension (nested)
        return _inner
    return _outer.__code__


def main(argv=None):
    a = parse_args(argv)
    out_dir = Path(a.out_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        print("FATAL=OUT_DIR_UNWRITABLE %s" % a.out_dir)
        return 2

    local_magic = magic_hex()

    if a.self_fixture:
        # --- logic-test / Linux: current interpreter's own code object (no real ts4script) ---
        members_top = {"<self-fixture>": (self_fixture_top(), local_magic)}
        pycs = ["<self-fixture>"]
        all_magic_match = True
    else:
        # --- real WW ts4script on Windows 3.7.9 (native marshal) ---
        if not a.ts4script:
            print("FATAL=TS4SCRIPT_REQUIRED (or --self-fixture)")
            return 2
        ts4 = Path(a.ts4script)
        if not ts4.is_file():
            print("FATAL=TS4SCRIPT_MISSING %s" % ts4)
            return 2
        try:
            pycs = load_archive_members(ts4)
        except Exception as e:
            print("FATAL=INVALID_ARCHIVE %s" % e)
            return 3
        if not pycs:
            print("FATAL=NO_PYC_MEMBERS")
            return 3
        members_top = {}
        for m in pycs:
            raw = read_member_bytes(ts4, m)
            if raw is None:
                members_top[m] = (None, "")
                continue
            mm = raw[:4].hex() if len(raw) >= 4 else ""
            try:
                co = marshal.loads(raw[16:])
            except Exception:
                co = None
            members_top[m] = (co, mm)
        # native decode is only valid when pyc magic == interpreter magic
        mm_vals = [members_top[m][1] for m in pycs if members_top[m][1]]
        if not mm_vals:
            all_magic_match = False
        else:
            all_magic_match = all(v == local_magic for v in mm_vals)

    match_lines = []
    func_lines = []
    caller_lines = []
    reg_lines = []
    matched_members = set()
    facts_agg = {"name": set(), "str": set(), "int": set()}

    decoded_any = False
    for member in sorted(members_top.keys()):
        co, mm = members_top[member]
        if co is None:
            continue
        if not a.self_fixture:
            if (mm or "") != local_magic:
                # fail-closed: cannot native-marshal decode reliably; skip semantics
                match_lines.append("SKIP_MAGIC_MISMATCH|MEMBER=%s|PYC_MAGIC=%s|LOCAL_MAGIC=%s"
                                   % (member, mm or "(no-header)", local_magic))
                continue
        decoded_any = True
        fm = run_member_census(member, co, match_lines, func_lines, caller_lines)
        facts_agg["name"] |= fm["co_names_matches"]
        facts_agg["str"] |= fm["str_const_matches"]
        facts_agg["int"] |= fm["int_const_matches"]
        if fm["matched_fn_names"]:
            matched_members.add(member)

    # caller & registry passes only over natively-decoded members
    members_top_native = {}
    for member in members_top:
        co, mm = members_top[member]
        if co is None:
            continue
        if a.self_fixture or (mm or "") == local_magic:
            members_top_native[member] = (co, mm)
    if decoded_any and members_top_native:
        caller_census(caller_lines, members_top_native, matched_members)
        registry_sites(reg_lines, members_top_native)

    prefix = a.out_prefix
    writes = (
        ("%s_keyword_census.txt" % prefix, match_lines),
        ("%s_matched_functions.txt" % prefix, func_lines),
        ("%s_callers.txt" % prefix, caller_lines),
        ("%s_registry_sites.txt" % prefix, reg_lines),
    )
    for fname, lines in writes:
        (out_dir / fname).write_text("\n".join(lines) + ("\n" if lines else ""),
                                     encoding="utf-8")
    sum_lines = generate_summary(pycs, matched_members, facts_agg,
                                 caller_lines, reg_lines, magic=all_magic_match)
    (out_dir / ("%s_summary.txt" % prefix)).write_text(
        "\n".join(sum_lines) + "\n", encoding="utf-8")

    if not decoded_any:
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
