#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_identifier_exact.py --- P32 STATIC EXACT extractor: recover the FINAL
5 nested expressions (locations/actor/props listcomps) of
SexAnimationInstance.get_identifier, which the P29-G2 decisive transcription
itself did not expand.

Target module
-------------
Real (Windows, py3.7.9, native marshal):
    C:\\Users\\thela\\Documents\\Electronic Arts\\The Sims 4\\Mods\\
        WickedWhimsMod\\TURBODRIVER_WickedWhims_Scripts.ts4script
    member:   wickedwhims/sex/animations/animation_instance.pyc
    pyc magic: 420d0d0a (must equal the RUNNING interpreter's magic to native
               marshal-load; WW ships py3.7.9 -> run this with the CPython 3.7.9
               under which the pyc was compiled).

What it RECOVERS (targeted, NOT a broad census of the ts4script)
---------------------------------------------------------------
1. SexAnimationInstance.get_identifier discovery in the animation_instance member
     (module path is walked; the class.method is matched by trailing path).
2. The main get_identifier code object's co_names / co_varnames / co_consts
     (ordered) / disassembly.
3. EVERY directly nested code object of get_identifier, INCLUDING the 5 nested
     listcomp / generator code objects expected at lines ~595/599/600/601/602,
     each with its OWN co_names / co_varnames / co_consts / disassembly (this is
     the gap P29-G2 left: nested MAKEFUNCTION bodies were not expanded).
4. A method-call census over those nested bodies: any attribute/method name that
     a nested body LOAD_METHOD/LOAD_ATTRs then CALLs is collected as a CALLEE
     (e.g. get_gender_signature / get_animation_clip_name / get_xxx).
5. For every unique CALLEE method name, a second targeted pass locates its
     defining code object (by co_name over the whole member tree) and dumps that
     body + its own directly nested bodies too.  Only methods actually referenced
     by the get_identifier tree are pulled -- no broad module dump.

Safe defaults (never hand-guess the identity preimage):
    - It EMITS DISASSEMBLY + const/varname facts ONLY.  It hard-codes NO field
      ordering and NO serialization rule; a later golden-reconstruction stage
      will encode rules ONLY from evidence emitted here.
    - Fail-closed magic: if a pyc member header magic != the running interpreter
      magic, that member is SKIPPED (cannot native-marshal reliably) and tagged
      SKIP_MAGIC_MISMATCH.  Real WW must be read on py3.7.9 so all match.

Modes
-----
--self-fixture    logic-test / Linux mode.  Marshals THIS interpreter's own
                  structural synth module shaped like the real target: a module
                  carrying SexAnimationInstance.get_identifier whose body embeds
                  5 listcomps/genexprs, whose listcomps call actor helper methods
                  (get_gender_signature / get_animation_clip_name), verifying
                  nested-body discovery + callee-body extraction without py3.7.
--ts4script path  real mode (Windows, py3.7.9).

Output (written ONLY under --out-dir):
    <out-dir>/p32/p32_identifier_exact.txt
    <out-dir>/p32/p32_identifier_exact_callees.txt
Exit: 0 = dumped; 2 = args/io; 3 = target not found / bad magic on all members.

ZERO_WRITE_TO_MODS=YES / ZERO_WRITE_TO_SAVES=YES.  Reads the ts4script read-only.
"""
import argparse
import dis
import importlib.util
import marshal
import re
import sys
import zipfile
from pathlib import Path

TARGET_MEMBER_BASENAME = "animation_instance.pyc"
CLASS_SEG = "SexAnimationInstance"
METHOD_SEG = "get_identifier"
_CODE_TYPE = type((lambda: 0).__code__)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def magic_hex():
    return importlib.util.MAGIC_NUMBER.hex()


def _clean_arg(arg):
    """Strip ephemeral '0x7f..>' object addresses so output is byte-deterministic."""
    return re.sub(r"0x[0-9a-fA-F]+", "0xADDR", str(arg or ""))


def _fmt_const(c):
    if isinstance(c, _CODE_TYPE):
        return "<code %s>" % getattr(c, "co_name", "?")
    return _clean_arg(repr(c))


def _ops(co):
    try:
        return list(dis.get_instructions(co))
    except Exception:
        return []


# ---------------------------------------------------------------------------
# code-object walk provenance (module.class.method[.<listcomp>])
# ---------------------------------------------------------------------------
def recursive_walk(co, parent_path, acc):
    if not parent_path:
        parent_path = getattr(co, "co_name", "") or "<module>"
    acc.append((co, parent_path))
    for sub in getattr(co, "co_consts", ()):
        if isinstance(sub, _CODE_TYPE):
            nm = getattr(sub, "co_name", "") or ""
            child = parent_path + "." + nm if nm else parent_path
            recursive_walk(sub, child, acc)


def _walk_nested_only(code, path):
    """Directly nested code objects (listcomps/genexprs/lambdas in co_consts)."""
    out = []
    for sub in getattr(code, "co_consts", ()):
        if isinstance(sub, _CODE_TYPE):
            nm = getattr(sub, "co_name", "") or ""
            out.append((sub, (path + "." + nm) if nm else path))
    return out


# ---------------------------------------------------------------------------
# member discovery (real ts4script)
# ---------------------------------------------------------------------------
def find_member_top(ts4_path, local_magic, member_facts, all_members=False):
    """Decode member(s). When all_members=False, read ONLY the animation_instance
    member(s) (target discovery). When all_members=True, decode EVERY .pyc that
    matches magic, so a callee method body that lives in another module can be
    located.  Native decode only when member magic == running magic."""
    out_members = {}
    with zipfile.ZipFile(str(ts4_path)) as z:
        names = [n for n in z.namelist() if n.endswith(".pyc")]
        if all_members:
            cands = list(names)
        else:
            cands = [n for n in names if Path(n).name == TARGET_MEMBER_BASENAME
                     or n.endswith(TARGET_MEMBER_BASENAME)]
            if not cands:
                cands = [n for n in names if "animation_instance" in n]
    for m in sorted(cands):
        try:
            with zipfile.ZipFile(str(ts4_path)) as z:
                raw = z.read(m)
        except KeyError:
            member_facts.append("MEMBER=%s READ=NO" % m)
            continue
        mm = raw[:4].hex() if len(raw) >= 4 else ""
        if not all_members:
            member_facts.append("MEMBER=%s PYC_MAGIC=%s" % (m, mm or "(no-header)"))
        if mm != local_magic:
            if not all_members:
                member_facts.append("SKIP_MAGIC_MISMATCH|MEMBER=%s|PYC_MAGIC=%s|LOCAL_MAGIC=%s"
                                    % (m, mm or "", local_magic))
            continue
        try:
            co = marshal.loads(raw[16:])
        except Exception as e:  # noqa
            if not all_members:
                member_facts.append("MEMBER=%s DECODE=FAIL %s" % (m, e))
            continue
        out_members[m] = co
    return out_members


# ---------------------------------------------------------------------------
# finding the class.method
# ---------------------------------------------------------------------------
def find_target(members_top):
    """Deepest code whose exact path tail == SexAnimationInstance.get_identifier.
    Ties broken by nested-code richness then path (deterministic)."""
    hits = []
    for member, top in members_top.items():
        objs = []
        recursive_walk(top, "", objs)
        for code, path in objs:
            segs = path.split(".")
            if segs and segs[-1] == METHOD_SEG and CLASS_SEG in segs:
                hits.append((member, code, path))
    if not hits:
        return None

    def _score(h):
        _m, _c, _p = h
        nested = len([x for x in getattr(_c, "co_consts", ())
                      if isinstance(x, _CODE_TYPE)])
        return (nested, _p)

    hits.sort(key=_score)
    return hits[-1]


# ---------------------------------------------------------------------------
# dumpers
# ---------------------------------------------------------------------------
def dump_code_block(lines, code, path, label=""):
    if label:
        lines.append("### %s" % label)
    lines.append("CODE_PATH=%s" % path)
    lines.append("CO_NAME=%s" % getattr(code, "co_name", "?"))
    lines.append("CO_FIRSTLINENO=%d" % getattr(code, "co_firstlineno", 0))
    lines.append("CO_ARGCOUNT=%d" % getattr(code, "co_argcount", 0))
    lines.append("CO_NAMES=%s" % (list(getattr(code, "co_names", ())) or "(none)"))
    lines.append("CO_VARNAMES=%s" % (list(getattr(code, "co_varnames", ())) or "(none)"))
    _cv = list(getattr(code, "co_freevars", ())) + list(getattr(code, "co_cellvars", ()))
    lines.append("CO_FREE_CELL=%s" % (_cv or "(none)"))
    lines.append("CO_CONSTS=%s" % ([_fmt_const(c) for c in getattr(code, "co_consts", ())]
                                   or "(none)"))
    lines.append("DISASSEMBLY:")
    for ins in _ops(code):
        _sl = getattr(ins, "starts_line", None) or 0
        lines.append("    L%d %5d %s %s" % (_sl, ins.offset, ins.opname,
                                            _clean_arg(ins.argrepr)))
    lines.append("")
    return lines


def walk_nested_and_dump(lines, code, path):
    """Dump code + every directly nested code, each with own metadata/disasm.
    Also returns called-method names seen across code and nested bodies."""
    called = set()
    dump_code_block(lines, code, path)
    for sub, sub_path in _walk_nested_only(code, path):
        dump_code_block(lines, sub, sub_path)
    _collect_called_methods(code, called)
    for sub, _sp in _walk_nested_only(code, path):
        _collect_called_methods(sub, called)
    return called


def _collect_called_methods(code, acc):
    """Attribute/method name that is LOADed then CALLed within a short window."""
    ops = _ops(code)
    for idx, ins in enumerate(ops):
        if ins.opname in ("LOAD_METHOD", "LOAD_ATTR"):
            nm = str(ins.argval or "")
            if not nm or nm.startswith("_"):
                continue
            _end = min(idx + 12, len(ops))
            for j in range(idx + 1, _end):
                if ops[j].opname.startswith(("CALL_FUNCTION", "CALL_METHOD")):
                    acc.add(nm)
                    break


def find_definitions(members_top, names):
    """Definition code objects by co_name across the member tree (deterministic)."""
    found = {}
    for member, top in members_top.items():
        objs = []
        recursive_walk(top, "", objs)
        for code, path in objs:
            _cn = getattr(code, "co_name", "") or ""
            if _cn in names and _cn not in found:
                found[_cn] = (member, code, path)
    return found


# ---------------------------------------------------------------------------
# structural synth fixture (logic test / Linux)
# ---------------------------------------------------------------------------
def build_self_fixture_module_code():
    """Compile a module carrying SexAnimationInstance.get_identifier whose body
    embeds 5 listcomp/genexpr kernels and calls actor-helpers (structural echo of
    the real target's shape, NOT a semantic copy of WW)."""
    # The 5 nested kernels mirror the real get_identifier body shape.  To exercise
    # the callee-extraction requirement (recover actor/prop method bodies actually
    # CALLED by listcomps), kernels call real actor methods via attribute access:
    #   get_locations / get_gender_signature / get_animation_clip_name /
    #   location-object text, prop rendering, etc.
    src = (
        "class SexAnimationInstance(object):\n"
        "    def get_identifier(self):\n"
        "        _cache = getattr(self,'get_identifier_cache',None)\n"
        "        locs=[loc.get_location_text() for loc in (getattr(self,'get_locations',None) or [])]\n"
        "        a1=[_a.get_gender_signature() for _a in (getattr(self,'get_actors',None) or [])]\n"
        "        a2=[_b.get_actor_identifier() for _b in (getattr(self,'get_actor_fields',None) or [])]\n"
        "        a3=sum(_c.get_clip_bytes() for _c in (getattr(self,'get_clips',None) or []))\n"
        "        pr=[_p.get_prop_repr() for _p in (getattr(self,'props',None) or [])]\n"
        "        return (_cache, locs, a1, a2, a3, pr, getattr(self,'author',None))\n"
        "    def get_locations(self):\n"
        "        return []\n"
        "    def get_actors(self):\n"
        "        return []\n"
        "    def get_actor_fields(self):\n"
        "        return []\n"
        "    def get_clips(self):\n"
        "        return [0]\n"
        "class ActorHelper(object):\n"
        "    def get_gender_signature(self):\n"
        "        return 'sig'\n"
        "    def get_animation_clip_name(self):\n"
        "        return 'clip'\n"
        "class Location(object):\n"
        "    def get_location_text(self):\n"
        "        return 'loc'\n"
        "def actor_lib_get_clip(a):\n"
        "    return getattr(a,'get_animation_clip_name',lambda:None)()\n"
    )
    return compile(src, "<p32fixture>", "exec")


# ---------------------------------------------------------------------------
# report assembly
# ---------------------------------------------------------------------------
def assemble_report(members_target, members_all, member_facts):
    """members_target: where to FIND SexAnimationInstance.get_identifier (usually
    animation_instance member only).  members_all: superset searched to resolve a
    callee method body that may live in another module (in real mode = whole
    ts4script; in self-fixture = same single-member dict)."""
    lines = []
    lines.append("=== P32 STATIC EXACT: SexAnimationInstance.get_identifier ===")
    lines.append("TARGET_MEMBER=%s" % TARGET_MEMBER_BASENAME)
    lines.append("LOCAL_MAGIC=%s" % magic_hex())
    lines.append("LOCAL_PYTHON=%s" % sys.version.split()[0])
    lines.append("")
    lines.append("=== MEMBER FACTS ===")
    lines.extend(member_facts)
    lines.append("")

    target = find_target(members_target)
    callee_lines = ["=== CALLED ACTOR/PROP METHOD BODIES (callee extract) ==="]
    if target is None:
        lines.append("TARGET=FOUND:NO")
        lines.append("REASON=not located in natively-decoded members; check magic "
                     "(run real ts4script on py3.7.9).")
        return lines, callee_lines

    member, code, path = target
    lines.append("TARGET=FOUND:YES")
    lines.append("TARGET_MEMBER=%s" % member)
    lines.append("TARGET_CODE_PATH=%s" % path)
    lines.append("")
    lines.append("--- get_identifier main + DIRECT nested code objects ---")
    discovered = walk_nested_and_dump(lines, code, path)
    lines.append("CALLEE_METHODS_DISCOVERED=%s"
                 % (",".join(sorted(discovered)) if discovered else "(none)"))
    lines.append("")

    if discovered:
        defs_local = find_definitions(members_all, discovered)
        for nm in sorted(discovered):
            if nm not in defs_local:
                callee_lines.append("CALLEE=%s DEFINITION=NOT_FOUND_IN_TREE" % nm)
                continue
            _memb, _c, _p = defs_local[nm]
            callee_lines.append("CALLEE=%s DEFINED_IN_MEMBER=%s" % (nm, _memb))
            dump_code_block(callee_lines, _c, _p, label="callee body: %s" % nm)
            for sub_c, sub_p in _walk_nested_only(_c, _p):
                dump_code_block(callee_lines, sub_c, sub_p)
    else:
        callee_lines.append("(no actor/prop method calls discovered)")
    return lines, callee_lines


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ts4script", default=None,
                    help="path to WW .ts4script (Windows; ignored when --self-fixture)")
    ap.add_argument("--out-dir", required=True,
                    help="directory that will hold p32/ subfolder outputs")
    ap.add_argument("--self-fixture", action="store_true",
                    help="logic-test/Linux: compile THIS interpreter's structural "
                         "fixture (no python3.7 / no real ts4script)")
    return ap.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    out_dir = Path(a.out_dir)
    try:
        p32dir = out_dir / "p32"
        p32dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        print("FATAL=P32_OUT_DIR_UNWRITABLE %s" % (out_dir / "p32"))
        return 2

    member_facts = []
    if a.self_fixture:
        member_facts.append("MODE=SELF_FIXTURE (compile of this interpreter)")
        top = build_self_fixture_module_code()
        members_target = {"<p32-fixture>/animation_instance.pyc": top}
        members_all = members_target
    else:
        if not a.ts4script:
            print("FATAL=TS4SCRIPT_REQUIRED (or --self-fixture)")
            return 2
        ts4 = Path(a.ts4script)
        if not ts4.is_file():
            print("FATAL=TS4SCRIPT_MISSING %s" % ts4)
            return 2
        # 1) target member only (fast; this is where get_identifier lives).
        try:
            members_target = find_member_top(ts4, magic_hex(), member_facts,
                                             all_members=False)
        except Exception as e:  # noqa
            print("FATAL=INVALID_ARCHIVE %s" % e)
            return 3
        # 2) full ts4script decode (read-only) so a callee body that lives in a
        #    DIFFERENT module (actor/prop/location helpers) can be resolved exactly.
        if members_target:
            member_facts.append("CALLEE_SEARCH=ALL_MEMBERS (full ts4script, read-only)")
            try:
                members_all = find_member_top(ts4, magic_hex(), member_facts,
                                              all_members=True)
            except Exception as e:  # noqa
                print("FATAL=CALLEE_SEARCH_DECODE %s" % e)
                return 3
            members_all.update(members_target)  # ensure the target member is present
        else:
            members_all = {}
        if not member_facts or not members_target:
            print("FATAL=NO_TARGET_MEMBER_FOUND")
            return 3

    main_lines, callee_lines = assemble_report(members_target, members_all, member_facts)
    main_path = p32dir / "p32_identifier_exact.txt"
    callee_path = p32dir / "p32_identifier_exact_callees.txt"
    main_path.write_text("\n".join(main_lines) + ("\n" if main_lines else ""),
                         encoding="utf-8")
    callee_path.write_text("\n".join(callee_lines) + ("\n" if callee_lines else ""),
                           encoding="utf-8")
    print("WROTE %s" % main_path)
    print("WROTE %s" % callee_path)

    hit = any(l.startswith("TARGET=FOUND:YES") for l in main_lines)
    return 0 if hit else 3


if __name__ == "__main__":
    sys.exit(main())
