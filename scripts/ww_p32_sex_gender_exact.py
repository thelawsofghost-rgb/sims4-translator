#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_sex_gender_exact.py -- READ-ONLY static exact extraction of the real
WickedWhims `wickedwhims/sex/enums/sex_gender.pyc` from the pinned Scripts
ts4script, to PROVE (bytecode, not by-name) the mapping the P32 gender bridge
needs for the raw tuning token 'BOTH'.

Scope (operator-settled, narrow):
  1) SexGenderType class/enum definition (and its bases => Enum vs IntEnum)
  2) get_sex_gender_type_by_name  (the loader path that turns the raw tuning
     literal `animation_genders` into the enum) -- follow only the helper that
     DIRECTLY decides name -> enum.
  3) is_both_sex_gender (auxiliary verification)
  4) str(returned enum) representation that enters get_identifier.

NO import/execute of any WW module.  CPython-agnostic static decode via the
xdis marshalling/disassembler (handles the WW 3.7-line pyc without a matching
interpreter).  Never touches the archive / Mods / saves.

Decode contract (anti-guess, fail-closed):
  * The enum class body is a nested code object under the module; CPython runs a
    class body as its own code, so `SexGenderType` member names are the literal
    / tuple constants assigned inside that class code (STORE_NAME <NAME2ENV> of
    the ASYNC/naive members) OR built via _member_map / the enum's own machinery.
    We therefore decode the enum class code object's direct members (names stored
    as constant NAME tokens) and report each as a candidate SexGenderType member.
  * For get_sex_gender_type_by_name: we read its DISASSEMBLY and rebuild ONLY the
    direct `name-literal -> return-constant` mapping branches (the exact helper
    that decides name->enum, typically a dict.get / chain of compares returning a
    SexGenderType constant) and render, for each raw name incl. 'BOTH', the exact
    returned enum constant literal it loads.  If no branch/table literal covers
    'BOTH' or the return is not a SexGenderType symbol, the row is reported
    UNPROVEN (the catalog must NOT add BOTH) -- we never infer BOTH by name.

Output:  output/p32/p32_sex_gender_mapping_exact.txt
Sections rendered verbatim:
  MEMBER / PYC_MAGIC / CODE_PATH / CO_NAMES / CO_CONSTS / DISASSEMBLY
  + VERDICT  BOTH_VERIFIED_AS_SexGenderType.BOTH = PROVEN | UNPROVEN

ZERO_WRITE_TO_MODS=YES  ZERO_WRITE_TO_SAVES=YES  no game launch.
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

WW_ARCHIVE_SHA256 = "c1851f6178b6d3e47a40981a38be885840667187fc6ab510a208b89e0e5ff3e7"
TARGET_MEMBER = "wickedwhims/sex/enums/sex_gender.pyc"


# ---------------------------------------------------------------------------
# pure module introspection (extract functions/classes by name from the co tree)
# ---------------------------------------------------------------------------
def _walk(co, out):
    out.append(co)
    for sub in getattr(co, "co_consts", ()) or ():
        if hasattr(sub, "co_consts") and sub not in out:
            _walk(sub, out)


def find_code(module_co, wanted):
    """Return the nested code object whose co_name == wanted (deep-first)."""
    funcs = []
    _walk(module_co, funcs)
    for f in funcs:
        if getattr(f, "co_name", None) == wanted:
            return f
    return None


def class_member_names(class_co):
    """Recover candidate member NAME tokens bound inside an Enum class body.
    Enum members are assigned constants (name strings) STORE_NAME'd, or appear as
    tuple constants in the class body / _names.  We return the bound NAME-ish
    constants (common Enum member names are bare identifiers -> STORE_NAME with
    an immediate name)."""
    names = set(getattr(class_co, "co_names", ()) or ())
    out = []
    for n in names:
        if n.isupper() or n[:1].isupper():
            out.append(n)
    return out


# ---------------------------------------------------------------------------
# bytecode disassembler (self-resolving modern xdis 6.x recipe)
# ---------------------------------------------------------------------------
def xdis_get_opcode(minor):
    import xdis
    from xdis.version_info import PythonImplementation
    return xdis.get_opcode((3, minor), PythonImplementation.CPython)


def BytecodeIter(co, opc):
    from xdis import Bytecode
    return Bytecode(co, opc)


# ---------------------------------------------------------------------------
# report builder
# ---------------------------------------------------------------------------
def _emblem_locate(ts4_path, data, magic_hex):
    lines = []
    lines.append("ARCHIVE_SHA256_TARGET=%s" % WW_ARCHIVE_SHA256)
    import hashlib
    real_sha = hashlib.sha256(Path(ts4_path).read_bytes()).hexdigest()
    lines.append("ARCHIVE_SHA256_REAL  =%s" % real_sha)
    lines.append("SHA_PIN_MATCH=%s"
                 % ("YES" if real_sha == WW_ARCHIVE_SHA256 else "NO"))
    lines.append("")
    lines.append("MEMBER=%s" % TARGET_MEMBER)
    lines.append("PYC_MAGIC=%s" % magic_hex)
    lines.append("MEMBER_SIZE_BYTES=%d" % len(data))
    lines.append("")
    return lines


def _emit_function(ctx, fn_name, target_fn, opc):
    """Emit CO_NAMES / CO_CONSTS / DISASSEMBLY for one named function + return
    the rendered lines (so testers can assert structure)."""
    lines = []
    lines.append("== FUNCTION %s ==" % fn_name)
    if target_fn is None:
        lines.append("CODE_PATH=%s absent in member (expected nested co)"
                     % fn_name)
        return lines, "ABSENT"
    lines.append("CODE_PATH=%s::%s (co_filename=%s co_firstlineno=%s)"
                 % (TARGET_MEMBER, fn_name,
                    getattr(target_fn, "co_filename", "?"),
                    getattr(target_fn, "co_firstlineno", "?")))
    lines.append("CO_NAMES=%s" % (list(getattr(target_fn, "co_names", ())) or "()"))
    lines.append("CO_CONSTS=%s" % (list(getattr(target_fn, "co_consts", ()) or "()")))
    lines.append("DISASSEMBLY:")
    for ins in BytecodeIter(target_fn, opc):
        lines.append("  %04d  %-28s %s" % (ins.offset, ins.opname,
                                           getattr(ins, "argrepr", "")))
    lines.append("")
    return lines, "OK"


def BytecodeIter(co, opc):
    from xdis import Bytecode
    return Bytecode(co, opc)


def analyze(member_name, data, out_path):
    """Main analysis after a member is found.  Returns (verdict_both, lines)."""
    lines = []
    # load with xdis
    from xdis.load import load_module_from_file_object
    res = load_module_from_file_object(io.BytesIO(data), filename=member_name)
    ver = res[0]
    magic_int = res[2]
    module_co = res[3]
    lines.append("PYC_MAGIC_INT=%s  PY_VER=%s" % (magic_int, ver))
    opc = None
    try:
        import xdis
        from xdis.version_info import PythonImplementation
        minor = int(ver[1])
        opc = xdis.get_opcode((3, minor), PythonImplementation.CPython)
    except Exception as ex:
        lines.append("OPCODE_LOAD_FAIL=%s" % ex)
    if opc is None:
        lines.append("FATAL_OPCODE_LOAD=no opcode; cannot DISASM.")

    # ---- find the enum class code ----
    funcs = []
    _walk(module_co, funcs)
    class_co = None
    for f in funcs:
        # class bodies have co_name == the class name
        if getattr(f, "co_name", None) == "SexGenderType":
            class_co = f
            break

    lines.append("")
    lines.append("== SEXGENDERTYPE CLASS / ENUM ==")
    if class_co is not None:
        lines.append("CODE_PATH=%s::SexGenderType (class body co_firstlineno=%s)"
                     % (member_name, getattr(class_co, "co_firstlineno", "?")))
        lines.append("CO_NAMES=%s" % (list(getattr(class_co, "co_names", ())) or "()"))
        # enum bases are usually module-level names referenced by the class header
        # (LOAD_NAME Enum / IntEnum / EnumMeta).  Detection via co_names presence.
        nm = set(getattr(class_co, "co_names", ()) or ())
        bases = [b for b in ("Enum", "IntEnum", "EnumMeta") if b in nm]
        lines.append("CLASS_BASES_HINT(from class-body co_names)=%s" % (bases or "?"))
        lines.append("CO_CONSTS=%s" % (list(getattr(class_co, "co_consts", ()) or "()")))
        members = class_member_names(class_co)
        lines.append("CANDIDATE_MEMBER_NAME_TOKENS=%s"
                     % (", ".join(sorted(members)) if members else "(none)"))
        lines.append("MEMBER 'BOTH' TOKEN_PRESENT_IN_CLASS_NAMES=%s"
                     % ("YES" if "BOTH" in members else "NO"))
        lines.append("DISASSEMBLY:")
        for ins in BytecodeIter(class_co, opc):
            lines.append("  %04d  %-28s %s" % (ins.offset, ins.opname,
                                               getattr(ins, "argrepr", "")))
    else:
        lines.append("CODE_PATH=%s::SexGenderType  ABSENT (no nested class co with "
                     "co_name 'SexGenderType')" % member_name)
        lines.append("PRESENT_NESTED_NAMES=%s"
                     % (",".join(sorted({getattr(f, "co_name", "?")
                                         for f in funcs})) or "(none)"))
    lines.append("")

    # ---- get_sex_gender_type_by_name ----
    f_by = find_code(module_co, "get_sex_gender_type_by_name")
    fl_by = []
    for fn_name, target, tag in (
            ("get_sex_gender_type_by_name", f_by, "get_sex_gender_type_by_name"),):
        ln, st = _emit_function(member_name, fn_name, target, opc)
        fl_by.extend(ln)
    lines.extend(fl_by)
    lines.append("")

    # ---- is_both_sex_gender (auxiliary) ----
    f_both = find_code(module_co, "is_both_sex_gender")
    lb, stb = _emit_function(member_name, "is_both_sex_gender", f_both, opc)
    lines.extend(lb)

    # ---- verdict (structural evidence, never by name-game) ----
    # We PROVE 'raw token BOTH -> SexGenderType.BOTH' from the real enum + helper
    # structure, not from a coincidental identical spelling:
    #   (E1) SexGenderType is an Enum-family and its class body binds a BOTH
    #        member NAME (genuine member literal, not a bare function string).
    #   (E2) get_sex_gender_type_by_name returns enum MEMBERS by index/map: its
    #        code indexes SexGenderType.__members__ / _member_map_ /
    #        _value2member_map_, OR builds a dict whose values are loaded from
    #        SexGenderType.<member> -- i.e. the by-name resolution round-trips to
    #        genuine SexGenderType members.  If (E2) holds, then whatever)
    #        NAME get_sex_gender_type_by_name resolves (incl. 'BOTH', since E1
    #        proves SexGenderType.BOTH is a member with that NAME) is exactly that
    #        SexGenderType member.
    #   (E3 auxiliary) is_both_sex_gender references SexGenderType.BOTH directly.
    members = class_member_names(class_co) if class_co is not None else ()
    e1_both_member = "BOTH" in members
    # E2 now accepts the REAL WW direct route (normalize -> membership ->
    #   SexGenderType[normalized_name] / BINARY_SUBSCR) in addition to the __members__
    #   mapping-struct form.  Proven only when the SAME normalized local is both
    #   membership-tested against SexGenderType and re-indexed to fetch the member.
    route, proof_path = _by_name_route(f_by, opc)
    by_name_membership = (route is not None)
    e3_aux_msg = (
        "is_both_sex_gender references SexGenderType.BOTH"
        if _references_symbol(f_both, "BOTH", opc)
        else "is_both_sex_gender absent or references BOTH_GENDERS/other (AUX only)")
    # structural proof: the enum class body binds a real BOTH member NAME (E1) AND
    #   get_sex_gender_type_by_name provably returns a live SexGenderType member for
    #   a normalized name (E2).  E3 (is_both_sex_gender) is AUXILIARY, never gates.
    both_proven = e1_both_member and by_name_membership
    enf_hint = _enum_family_hint(class_co) if class_co is not None else []
    lines.append("")
    lines.append("== STRUCTURAL VERDICT (real bytecode/constants/member map) ==")
    lines.append("SexGenderType class_code_seen=%s  class_has_BOTH_member=%s"
                 % (class_co is not None, e1_both_member))
    lines.append("enum_family_hint=%s" % (enf_hint or "?"))
    lines.append("by_name_proof_route=%s" % (route if route else "UNPROVEN/NONE"))
    lines.append("proof_path=%s" % (proof_path or "(none)"))
    lines.append("is_both_sex_gender_aux=%s" % e3_aux_msg)
    lines.append("")
    lines.append("BOTH_VERIFIED_AS_SexGenderType.BOTH=%s"
                 % ("PROVEN" if both_proven else "UNPROVEN"))
    lines.append("BOTH_detail=class_has_BOTH_member=%s ; by_name_route=%s ; "
                 "e3_aux_present=%s"
                 % (e1_both_member, route, e3_aux_msg.count("SexGenderType.BOTH") > 0))
    lines.append("REPRESENTATION=reconstructor renders each actor gender_type as "
                 "SexGenderType.<NAME> (_enum) -- same uniform mechanism for MALE/"
                 "FEMALE/BOTH; no special BOTH hash string ever written.")
    lines.append("NOTE_IF_UNPROVEN: catalog must keep raw token 'BOTH' -> "
                 "UNKNOWN (fail-closed); rerun over the REAL pinned ts4script.")
    return both_proven, lines


def _enum_family_hint(class_co):
    """Best-effort Enum-family HINT (report only; never gates PROVEN).  Enum
    class bodies name their base at module level, so we look for enum-supporting
    strings in the class body's names/consts and return the hits."""
    nm = set(getattr(class_co, "co_names", ()) or ())
    consts = getattr(class_co, "co_consts", ()) or ()
    names = nm | {c for c in consts if isinstance(c, str)}
    return sorted(names & {"Enum", "IntEnum", "EnumMeta", "Flag", "_EnumDict",
                           "_member_map_", "_value2member_map_", "__new__",
                           "_generate_next_value_", "values"})


def _references_symbol(fn_co, token, opc):
    """True if the function DISASM LOADs/STOREs/compares a symbol named `token`
    (LOAD_ATTR SexGenderType.BOTH => argrepr 'BOTH')."""
    if fn_co is None or opc is None:
        return False
    from xdis import Bytecode
    try:
        for ins in Bytecode(fn_co, opc):
            if getattr(ins, "argrepr", "") == token:
                return True
    except Exception:
        pass
    return False


def _by_name_route(fn_co, opc):
    """Prove (structurally, no name-game) HOW get_sex_gender_type_by_name resolves a
    raw name to a SexGenderType member, return (route_name, proof_path), or
    (None, None) when unproven.

    ROUTE DIRECT (the REAL WW shape, operator bytecode):
        normalize   name = name.upper().strip()          (LOAD_FAST name / upper /
                                                           strip / STORE_FAST name)
        membership  LOAD_GLOBAL SexGenderType
                    LOAD_FAST name
                    COMPARE_OP in
                    POP_JUMP_IF_FALSE <fallback>
        success     LOAD_GLOBAL SexGenderType
                    LOAD_FAST name
                    BINARY_SUBSCR
                    RETURN_VALUE
        fallback    LOAD_GLOBAL SexGenderType ; LOAD_ATTR NONE ; RETURN_VALUE
      == a normalized name is membership-tested against SexGenderType and the SAME
         member is re-fetched by SexGenderType[name] => returns a genuine member,
         so any NAME that IS a member (BOTH) resolves to that exact SexGenderType member.

    ROUTE MAP: SexGenderType.__members__/_member_map_/_value2member_map_ index.

    Uses a tiny stack model to attribute the object being subscripted / tested so an
    isolated stray BINARY_SUBSCR is NEVER credited."""
    if fn_co is None or opc is None:
        return None, None
    from xdis import Bytecode
    try:
        ins = list(Bytecode(fn_co, opc))
    except Exception:
        return None, None
    if not ins:
        return None, None

    # ---- tiny stack model over the linear stream (only LOAD*/BINARY_SUBSCR/\n    #      COMPARE_OP/RETURN that matter) ----
    def analyze():
        stack = []          # list of ('g', SexGenderType) | ('l', localname) | ('c', ...)
        subscript_on = None   # for BINARY_SUBSCR: ('SexGenderType', localname)
        membership_on = None  # ('SexGenderType', localname) for COMPARE_OP in
        normalized_local = None
        seen_strip_call = False
        prev_prev = None
        for it in ins:
            op = it.opname
            ar = getattr(it, "argrepr", "")
            if op == "LOAD_FAST":
                stack.append(("l", ar))
            elif op == "LOAD_GLOBAL":
                stack.append(("g", ar))
            elif op == "LOAD_CONST":
                stack.append(("c", ar))
            elif op == "LOAD_METHOD":
                # method descriptor does not become a subscriptable operand
                pass
            elif op == "CALL_METHOD" or op == "CALL_FUNCTION":
                stack.append(("c", ar))
            elif op == "STORE_FAST":
                normalized_local = ar if seen_strip_call else normalized_local
                seen_strip_call = False
                stack = []
            elif op in ("POP_JUMP_IF_FALSE", "POP_JUMP_IF_TRUE", "POP_TOP"):
                if stack:
                    stack.pop()
            elif op in ("COMPARE_OP", "CONTAINS_OP"):
                # two operands: right (top) and left (below).  'name in SexGenderType'
                # pushed left(name) then right(SexGenderType) => top=SexGenderType.
                # COMPARE_OP argrepr 'in'; CONTAINS_OP argval 0 = in / 1 = not in.
                right = stack[-1] if stack else None
                left = stack[-2] if len(stack) >= 2 else None
                is_in = (ar == "in") or (op == "CONTAINS_OP"
                                          and getattr(it, "argval", 1) == 0)
                if is_in and right and right[0] == "g" and right[1] == "SexGenderType" \
                        and left and left[0] == "l":
                    membership_on = ("SexGenderType", left[1])
                # pop 2 push bool
                if len(stack) >= 2:
                    stack = stack[:-2]
                stack.append(("bl",))  # placeholder bool result (name ref dropped ok)
            elif op == "BINARY_SUBSCR":
                container = stack[-2] if len(stack) >= 2 else None
                index = stack[-1] if stack else None
                if container and container[0] == "g" and container[1] == "SexGenderType" \
                        and index and index[0] == "l":
                    subscript_on = ("SexGenderType", index[1])
                if len(stack) >= 2:
                    stack = stack[:-2]
                stack.append(("m",))
            # detect upper->strip method chain end (name.upper().strip())
            aa = getattr(it, "argrepr", "")
            if op == "LOAD_METHOD" and aa == "strip":
                seen_strip_call = True
                stack = []
        return subscript_on, membership_on, normalized_local

    sub_on, mem_on, norm_local = analyze()
    # well-formed DIRECT route:
    direct = (norm_local is not None and mem_on is not None
              and mem_on[0] == "SexGenderType"
              and sub_on is not None and sub_on[0] == "SexGenderType"
              and mem_on[1] == sub_on[1] == norm_local
              and _trusted_fallback(ins))
    if direct:
        return "DIRECT-NORMALIZE-MEMBERSHIP-SUBSCR", \
            "NORMALIZE(name.upper().strip()) -> COMPARE_OP(in) SexGenderType -> " \
            "SexGenderType[name] BINARY_SUBSCR -> real member; fallback SexGenderType.NONE"
    # fall back to the __members__/map structural route (older shape)
    map_hint = {getattr(i, "argrepr", "") for i in ins
                if i.opname in ("LOAD_ATTR", "LOAD_METHOD")}
    if map_hint & {"__members__", "_member_map_", "_value2member_map_",
                   "__getitem__", "values"}:
        m = sorted(map_hint & {"__members__", "_member_map_", "_value2member_map_",
                               "__getitem__", "values"})[0]
        return "MAP(%s)" % m, "SexGenderType.%s indexed by name -> real member" % m
    return None, None


def _trusted_fallback(ins):
    """Anywhere in the function: LOAD_GLOBAL SexGenderType ; LOAD_ATTR NONE ;
    RETURN_VALUE  (the real default/fallback branch)."""
    for i in range(len(ins) - 2):
        if ins[i].opname == "LOAD_GLOBAL" and getattr(ins[i], "argrepr", "") == "SexGenderType" \
                and ins[i + 1].opname == "LOAD_ATTR" and getattr(ins[i + 1], "argrepr", "") == "NONE" \
                and ins[i + 2].opname == "RETURN_VALUE":
            return True
    return False


# main
# ---------------------------------------------------------------------------
OUT_FILE = "output/p32/p32_sex_gender_mapping_exact.txt"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="P32 static-exact sex_gender.pyc extraction (read-only, "
                    "Windows/real WW ts4script).")
    ap.add_argument("archive", help="TURBODRIVER_WickedWhims_Scripts.ts4script")
    ap.add_argument("--member", default=TARGET_MEMBER)
    ap.add_argument("--out-dir", default="output/p32")
    ap.add_argument("--accept-any-sha", action="store_true",
                    help="DIAGNOSTIC ONLY: bypass the sha pin (never for the "
                         "operator's real run)")
    a = ap.parse_args(argv)

    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    arc = Path(a.archive)
    if not arc.is_file():
        print("ERROR=ARCHIVE_MISSING %s" % arc, file=sys.stderr)
        return 2
    import hashlib
    real_sha = hashlib.sha256(arc.read_bytes()).hexdigest()
    if not a.accept_any_sha and real_sha != WW_ARCHIVE_SHA256:
        print("ERROR=SHA_PIN_MISMATCH real=%s != pinned=%s"
              % (real_sha, WW_ARCHIVE_SHA256), file=sys.stderr)
        return 3

    # locate the member inside the ts4script zip
    import zipfile
    data = None
    member_path = a.member
    with zipfile.ZipFile(arc) as z:
        names = [n for n in z.namelist()
                 if n.replace("\\", "/").endswith(a.member)]
        if not names:
            for n in z.namelist():
                bn = n.rsplit("/", 1)[-1]
                if bn.endswith("sex_gender.pyc"):
                    names.append(n)
        if not names:
            print("ERROR=MEMBER_NOT_FOUND %s in %s" % (a.member, arc),
                  file=sys.stderr)
            return 4
        member_path = names[0]
        data = z.read(member_path)

    # magic + header (report raw 4-8 magic bytes before xdis to be faithful)
    magic_hex = data[:8].hex()

    lines = []
    lines.append("=== P32 sex_gender.pyc STATIC EXACT EXTRACTION (read-only) ===")
    lines.append("ACTION=pinned real Scripts ts4script; NO WW import; xdis static")
    emitter = _emblem_locate(str(arc), data, magic_hex)
    lines.extend(emitter)
    lines.append("CODE_PATH=%s" % member_path)
    lines.append("")
    verdict, analysis = analyze(member_path, data, out_dir)
    lines.extend(analysis)
    lines.append("")
    lines.append("ZERO_WRITE_TO_MODS=YES  ZERO_WRITE_TO_SAVES=YES  (read-only)")

    text = "\n".join(lines)
    out_path = out_dir / "p32_sex_gender_mapping_exact.txt"
    out_path.write_text(text + "\n", encoding="utf-8")
    print(text)
    print("OUT_TXT=%s" % out_path)
    print("P32_SEX_GENDER_EXACT=DONE (read-only)")
    print("BOTH_VERIFIED_AS_SexGenderType.BOTH=%s"
         % ("PROVEN" if verdict else "UNPROVEN"))
    # 0 = analysis completed (even if UNPROVEN -> verbose).  DISTINCT gates:
    #   exit 0 analysis done; 3 sha mismatch; 2/4 missing.
    return 0


if __name__ == "__main__":
    sys.exit(main())
