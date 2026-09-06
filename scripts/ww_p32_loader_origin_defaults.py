#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_loader_origin_defaults.py -- bytecode decoder for the TUNING_DEFAULT_SEMANTICS
gate (READ-ONLY).  Recovers, from the REAL _ts4_animations_tuning.pyc, the exact
default each correlated tuning key yields when ABSENT in a row.

WHY the earlier version failed on the real run
----------------------------------------------
The old populate_defaults walked code objects and ran a no-op "key in consts" pass.
WW defines each structure as a CLASS BODY (CPython executes a class body as its own
code object nested in <module>.co_consts), so actor/prop/top defaults were never
actually decoded -- the walker flattened code objects and never interpreted the
BUILD_CONST_KEY_MAP stack semantics.  Result: 11/11 UNKNOWN on the real pyc.

THE DECODE CONTRACT (settled from p30_tuning_mapping.txt + the operator + a
real compile of the documented idiom)
------------------------------------------------------------------------------
Inside each owning class body the TUNABLE_STRUCTURE is a real dict literal of
field -> Tunable leaf.  CPython compiles a dict with constant literal keys into
BUILD_CONST_KEY_MAP.  Verified shape (CPython 3.10 dis of the idiom; 3.7 differs
only in that kwarg packets use CALL_FUNCTION positional-with-name-tuple):

    # per field, in source order, a self-contained leaf producer:
    LOAD_NAME   <Tunable / _TunableStructureElement>
    LOAD_CONST  <DEFAULT_LITERAL>          # the leaf default -> value_i
    LOAD_CONST  (<'default'>,)            # keyword-name tuple
    CALL_FUNCTION_KW 1                     # (3.7: CALL_FUNCTION 2)
    ... N times ...
    LOAD_CONST  (<key_1>,...,<key_N>)      # FIELD-NAME TUPLE  (single constant)
    BUILD_CONST_KEY_MAP  N
    STORE_NAME  TUNABLE_STRUCTURE

ALL keys come verbatim from that one field-name tuple constant.  value for
keytuple[i] == the i-th leaf producer's default literal, matched by BYTECODE
ORDER (the i-th value pushed before BUILD_CONST_KEY_MAP), NEVER by "a 0/''/1 near
the name".  Repeated identical literals disambiguate by order.

Wrapper form also recognised: a leaf may call a Tunable wrapper
(_TunableStructureElement(TunableX(default=<literal>), raw_type=...)); the
default-bearing inner call is resolved to its constant literal.

STRICTNESS (anti-guess / anti-mismatch / fail-closed)
-----------------------------------------------------
  * key tuple length must equal BUILD_CONST_KEY_MAP count.
  * a MAP-VALUE leaf is a TOP-LEVEL CALL: one NOT consumed as an operand of a later
    (enclosing) call.  A call t whose callable-load is strictly before some later call
    u (and t<u) is an INNER operand call and is dropped -- this is what correctly peels
    the inner Tunable* call inside _TunableStructureElement(TunableX(...), raw_type=..)
    so only the OUTER structure-element call counts as that field's leaf.
  * the default literal of a leaf is resolved by scanning that leaf's bounded argument
    window for the 'default' kw-name packet and taking the scalar LOAD_CONST that is
    the packet's bound value (window order = nearest-to-leaf first).  Repeats are
    disambiguated by which packet is nearest the leaf.
  * each of the N leaves must resolve to an unambiguous CONSTANT default.  A leaf
    whose default is not a literal constant (a name lookup / expression), a tuple/
    count mismatch, or any ambiguity marks the owning CLASS UNPROVEN (those keys
    stay UNKNOWN) -- we never use carrier_count or a typed guess.
  * default TYPE is preserved: ''->str, 0->int, 0.0->float, None->NoneType,
    1->int, recorded as default_type.
  * class bodies are located by EXACT nested code-object name (traversal keeps the
    module.<Class> path, so recursion now works); <module> is scanned too.  Owners
    not present simply can't prove -> UNKNOWN (honest).

EVIDENCE, per proven key: owning_class, default_value (typed), default_type,
evidence_code_path, evidence_offset_range  -> status PROVEN / UNKNOWN.

ORACLE (NOT a hardcode): the real Windows bytes previously showed top-level
(_WickedWhimsAnimationData) structure as object_* -> '' (x3) and animation_version
-> 1.  This decoder must REACH those from the real pyc itself; if it derives a
different literal for a real pyc it FAILS loudly rather than force-match.  The
synthetic Linux fixtures mirror that orientation only to validate the bytecode
logic, not to bake values in.
"""
from __future__ import annotations

import sys

# correlated keys each OWNING class must prove (per-class field sets; the keys a
# class exposes come from that class's own field tuple).
_CORRELATED_KEYS = {
    "_WickedWhimsAnimationActor": (
        "animation_x_offset", "animation_y_offset", "animation_z_offset",
        "animation_angle_offset", "animation_facing_offset",
    ),
    "_WickedWhimsAnimationPropsData": (
        "prop_animation_clip_name", "prop_geometry_state",
    ),
    "_WickedWhimsAnimationData": (
        "object_animation_clip_name", "object_geometry_state",
        "object_material_state", "animation_version",
    ),
}
_ALL_KEYS = tuple(k for ks in _CORRELATED_KEYS.values() for k in ks)

_TUPLE_KEYS_CONTAINERS = ("BUILD_CONST_KEY_MAP",)          # map built from a const-tuple
_CALL_OPCODES = ("CALL_FUNCTION", "CALL_FUNCTION_KW",
                 "CALL_FUNCTION_EX", "CALL_KW", "CALL")


# ---------------------------------------------------------------------------
# instruction accessors -- work for real xdis instruction objects and for the
# lightweight stand-in used by the synthetic Linux fixtures below, so decode
# logic is identical in both.
# ---------------------------------------------------------------------------
def _op(ins):
    return getattr(ins, "opname", "")


def _off(ins):
    return getattr(ins, "offset", -1)


def _lineno(ins):
    ln = getattr(ins, "starts_line", None)
    if ln is None:
        ln = getattr(ins, "lineno", None)
    return ln


def _const(ins):
    """Object loaded by LOAD_CONST: prefer argval (xdis may wrap (idx, val)? no --
    xdis argval is the actual python object).  Fall back to arg."""

    if not hasattr(ins, "argval"):
        # py3 'dis' only via stdlib? not used; keep arg.
        return getattr(ins, "arg", None)
    av = ins.argval
    # xdis sometimes leaves argval as literal; if it is a tuple shaped like a
    # code-coordinate wrapper, unwrap defensively.
    if hasattr(av, "__module__") and str(type(av)).find("co_consts") >= 0:
        return av
    return av


# ---------------------------------------------------------------------------
# recursive traversal that KEEPS path (module.<Class>.<fn>)
# ---------------------------------------------------------------------------
def _iter_code_with_path(co):
    yield (co.co_name,), co
    seen = {id(co)}
    stack = [(co, (co.co_name,))]
    while stack:
        cur, path = stack.pop()
        for sub in getattr(cur, "co_consts", ()) or ():
            if hasattr(sub, "co_consts") and id(sub) not in seen:
                seen.add(id(sub))
                subpath = path + (sub.co_name,)
                yield subpath, sub
                stack.append((sub, subpath))


def _lines_for(co, instr):
    """Convert a code object to a list of instruction objects with .opname/.offset/
    .argval/.starts_line.  Accepts an already-disassembled iterator `instr` (xdis
    Bytecode.get_instructions) attached as co._dis or passed directly."""
    if co is None:
        return []
    return list(instr)


def _collect_code_objects(code, name=None):
    """Return {path_name: code_object} keyed by the dotted path string, for the
    module and every nested code object."""
    out = {}
    for path, cobj in _iter_code_with_path(code):
        out[".".join(path)] = cobj
    return out


# ---------------------------------------------------------------------------
# BUILD_CONST_KEY_MAP structure recovery (ORDER-based)
# ---------------------------------------------------------------------------
def _is_default_kwtuple(v):
    """kw-name packet ('default',) or ('default','raw_type',...) contains default."""
    if not isinstance(v, tuple):
        return False
    return any(s == "default" for s in v if isinstance(s, str))


def _leaf_default_ins(seq, call_idx):
    """Resolve the CONSTANT default literal of ONE value-leaf (the CALL at
    call_idx) by BYTECODE ORDER, never by a name scan.  Recognises the shapes the
    WW tuning pyc and the verified compiles emit for a TUNABLE_STRUCTURE leaf:

      S1 direct kwarg : _tse(default=<lit>)                    CALL_FUNCTION_KW 1
      S2 positional   : _tse(<x>, <lit>)                       CALL_FUNCTION n
      S3 nested tunab : _TunableStructureElement(TunableX(default=<lit>),
                                                 raw_type=...) -> OUTER call

    Unified rule (works for all three): within this leaf's contiguous argument
    window (backward from call_idx until the previous CALL / a STORE/BUILD
    boundary / the callable load), the OWNING 'default' kw-name packet is the one
    nearest the leaf call; when S3 nests, the inner TunableX call carries the only
    'default' packet in the window.  The default literal is the SCALAR LOAD_CONST
    immediately before that packet.  If no packet: the (positional) default is the
    deepest scalar literal in the window (S2 raw_type,<lit>).

    Returns (literal, offset_of_its_LOAD_CONST) or None when the default slot is
    NOT a constant literal (a name/expression)  ->  fail closed, key stays UNKNOWN."""
    # 1) backward scan of this leaf's bounded arg window for the 'default' kw-name
    #    packet and the ordered operand literals.  A leaf region carries at most one
    #    'default' packet (wrapper form nests it inside the inner Tunable* call); we
    #    scan a bounded window without stopping at CALLs so the nearest such packet
    #    to the leaf is authoritative regardless of nesting depth.
    j = call_idx - 1
    window = []      # nearest-first (offset, const, instr)
    while j >= 0 and call_idx - j <= 24:
        op = _op(seq[j])
        # hard boundaries: a STORE / BUILD_ / RETURN / POP / other leaf producer
        # means we left the pure argument region of this called-constructor.
        if op in ("POP_TOP", "RETURN_VALUE") or op.startswith("STORE") or \
                op.startswith("BUILD_"):
            break
        if op == "LOAD_CONST":
            v = _const(seq[j])
            window.append((_off(seq[j]), v, seq[j]))
        j -= 1

    # 2) the default packet's value = the const entry immediately AFTER the packet
    #    entry in the nearest-first (reversed-source) window list.
    for widx in range(len(window)):
        _wo, wv, _wi = window[widx]
        if isinstance(wv, tuple) and any(s == "default" for s in wv
                                         if isinstance(s, str)):
            if widx + 1 < len(window):
                next_off, next_val, _ = window[widx + 1]
                if _is_scalar_literal(next_val):
                    return next_val, next_off
            # packet exists but its bound value is a name/expr (not a const literal)
            # -> fail closed for this key/class
            return None

    # 3) positional rule (S2 / no explicit default packet): the default literal is
    #    the deepest (last in source order) scalar literal the constructor arg list
    #    carries.
    if window:
        src = list(reversed(window))  # source order (0 = first pushed)
        for _of, v, _i in reversed(src):
            if _is_scalar_literal(v):
                return v, _of
    return None


def _is_scalar_literal(v):
    """A default-carrying constant literal: int, float, str (incl ''), None, bool.
    (str short field-name constants appear as tuple members, not alone, but a lone
    str const IS a valid default, e.g. clip name ''; keep str.)"""
    return v is None or isinstance(v, (int, float, str, bool, complex))


def decode_structure(seq, path_name):
    """Given a code-object instruction list `seq`, find every TUNABLE_STRUCTURE
    dict-literal (BUILD_CONST_KEY_MAP with a preceding const field-name tuple) and
    return {field_key: {default, type, evidence_off_lo, evidence_off_hi, path}}.
    Fail-closed: an element whose default is not a constant causes the whole map
    to be skipped (returned as UNRESOLVED marker -> caller marks class UNPROVEN)."""
    result = {}
    unresolved = []
    n = len(seq)
    i = 0
    while i < n:
        if _op(seq[i]) != "BUILD_CONST_KEY_MAP":
            i += 1
            continue
        count = getattr(seq[i], "arg", 1)
        # the field-name tuple is the constant pushed immediately before
        j = i - 1
        while j >= 0 and _op(seq[j]) in ("NOP",):
            j -= 1
        if j < 0 or _op(seq[j]) != "LOAD_CONST":
            i += 1
            continue
        fieldtuple = _const(seq[j])
        if not (isinstance(fieldtuple, tuple) and fieldtuple and
                all(isinstance(k, str) for k in fieldtuple)):
            i += 1
            continue
        keys = list(fieldtuple)
        if count != len(keys):
            unresolved.append(("keytuple/count mismatch", _off(seq[i])))
            i += 1
            continue
        # keys authoritative.  Now the value leaves: a map value is produced by a
        # TOP-LEVEL CALL in the value sequence, i.e. a CALL NOT consumed as an
        # operand of a later (enclosing) CALL within the same contiguous region.
        # This correctly drops the inner TunableX(...) calls in the wrapper form
        # (_TunableStructureElement(TunableX(default=..), raw_type=..)) where only
        # the OUTER structure-element CALL stacks a map value.
        # 1) collect every CALL index between the field tuple and the nearest prior
        #    hard boundary (STORE/BUILD/RETURN or the prior map's field tuple).
        lo = j - 1
        while lo >= 0 and not (
                _op(seq[lo]).startswith("STORE") or
                _op(seq[lo]).startswith("BUILD_") or
                _op(seq[lo]) in ("RETURN_VALUE", "POP_TOP", "END_FINALLY")):
            lo -= 1
        lo += 1
        all_calls = [t for t in range(lo, j) if _op(seq[t]) in _CALL_OPCODES]
        if not all_calls:
            unresolved.append(("leaf count mismatch", _off(seq[i])))
            i += 1
            continue
        # TOP-LEVEL leaf CALLs = a map value's producer.  Discriminator (ORDER
        # based, not a name scan): a top-level leaf CALL is followed by one of
        # (a) the field-name tuple LOAD_CONST at j (the final leaf), or (b) a fresh
        # constructor callable LOAD_NAME/LOAD_GLOBAL/LOAD_METHOD/... that starts the
        # NEXT top-level leaf.  An inner TunableX(...) call is instead followed by
        # the enclosing structure-element's OWN trailing argument LOAD_CONSTs
        # (raw_type value / kw-name packet), so it is correctly NOT counted as a
        # leaf.  Fails closed (leaf count mismatch) if the structure differs.
        kept = []  # list indices of top-level leaf CALLs, source order
        for t in range(lo, j):
            if _op(seq[t]) not in _CALL_OPCODES:
                continue
            nxt = t + 1
            while nxt < j and _op(seq[nxt]) == "NOP":
                nxt += 1
            if nxt >= j:
                kept.append(t)  # structurally final call before the map
                continue
            pnxt = _op(seq[nxt])
            if pnxt in ("LOAD_NAME", "LOAD_GLOBAL", "LOAD_METHOD", "LOAD_FAST",
                        "LOAD_DEREF", "LOAD_CLASSDEREF", "LOAD_BUILD_CLASS"):
                kept.append(t)
        # kept already in source order; must equal field-tuple length
        if len(kept) != len(keys):
            unresolved.append(("leaf count mismatch (%d calls vs %d keys)"
                               % (len(kept), len(keys)), _off(seq[i])))
            i += 1
            continue
        call_idxs = kept
        ok = True
        for idx_key in range(len(keys)):
            key = keys[idx_key]
            dl = _leaf_default_ins(seq, call_idxs[idx_key])
            if dl is None:
                ok = False
                break
            lit, loff = dl
            result[key] = {
                "default": lit,
                "type": type(lit).__name__,
                "evidence_offset_range": (loff, _off(seq[i])),
                "path": path_name,
            }
        if not ok:
            unresolved.append(("leaf default not a constant", _off(seq[i])))
        i += 1
    return result, unresolved


# ---------------------------------------------------------------------------
# top-level decode used by populate_defaults
# ---------------------------------------------------------------------------
def decode_code_with_instr(code_map, instructions_by_path, wanted_map):
    """code_map: {path_str: code_object}; instructions_by_path: {path: [ins]}.
    wanted_map: {owner_suffix: [keys]}.  Returns
      {owner: {key: evidence}} for owners decoded fully, plus
      owners_unknown if a class could not be ruled complete."""
    out = {}
    for path_str, keys in wanted_map.items():
        path = tuple(path_str.split(".")) if path_str else ("<module>",)
        # find a code object whose dotted path ENDS with this class name
        matched = None
        for p in code_map:
            if p == path_str or p.endswith("." + path_str):
                matched = p
                break
        if matched is None:
            continue
        ins = instructions_by_path.get(matched, [])
        found, unresolved = decode_structure(ins, matched)
        # completeness: every wanted key proven?
        done = {k: found.get(k) for k in keys if k in found}
        out[path_str] = {"evidence": done, "unresolved": unresolved}
    return out


# ---------------------------------------------------------------------------
# public entry used by populate_defaults (xdis world) -- assemble instruction
# lists for every nested code object then decode the three owners.
# ---------------------------------------------------------------------------
def _resolve_opc_and_dis(get_opcode_mod, XBytecode, version_tuple=None,
                         pyc_version=None, code=None):
    """Return (opc, Bytecode-class) usable to disassemble the pyc's code objects,
    or (None, None) if unavailable (then callers fail closed)."""
    if get_opcode_mod is None or XBytecode is None:
        return None, None
    try:
        from xdis.op_imports import PythonImplementation  # noqa: F401
        # choose CPython flavor + version: prefer the module's own .3.x via magic
        flavor = getattr(pyc_version, "version", None)
        minor = None
        if flavor is not None:
            minor = flavor
        if minor is None and version_tuple is not None and len(version_tuple) >= 2:
            minor = version_tuple[1]
        if minor is None:
            minor = 7  # WW ships CPython 3.7; safest fallback (fail-hard later if wrong)
        try:
            opc = get_opcode_mod(PythonImplementation.CPython, minor)
        except Exception:
            opc = None
    except Exception:
        opc = None
    if opc is None:
        return None, None
    return opc, XBytecode


def extract_from_code(code, disinst_fn, wanted=None):
    """code: module code object.  disinst_fn(co) -> iterable of instructions
    (xdis Bytecode.get_instructions).  wanted: {owner: [keys]}; default from
    _CORRELATED_KEYS.  Returns (per_owner, module_path) where per_owner is
    {owner: (found:{key:ev} , unresolved)}."""
    if wanted is None:
        wanted = _CORRELATED_KEYS
    code_map = {}
    inst_map = {}
    for path, cobj in _iter_code_with_path(code):
        p = ".".join(path)
        code_map[p] = cobj
        try:
            inst_map[p] = list(disinst_fn(cobj))
        except Exception:
            inst_map[p] = []
    res = decode_code_with_instr(code_map, inst_map, wanted)
    return res, code_map


def populate_defaults(report, pyc_path_str, XBytecode=None, get_opcode_mod=None,
                      disinst_fn=None):
    """Recover defaults from the REAL tuning pyc via xdis; NEVER fabricate.
    `report` is the probe's DefaultSemanticsReport.  Loads the pyc, disassembles
    every nested code object, decodes the three WW owner classes' TUNABLE_STRUCTURE
    maps, and for each proven key calls report.set_default(key, value, display,
    evidence).  Any owner/key not provable stays UNKNOWN (fail closed)."""
    import io as _io

    # decode path: load pyc with xdis, get the module code + version.
    code = None
    pyc_flavor = None
    try:
        from xdis.load import load_module_from_file_object
        with open(pyc_path_str, "rb") as fh:
            data = fh.read()
        res = load_module_from_file_object(_io.BytesIO(data), filename=pyc_path_str)
        # res: (version, timestamp, magic_int, code, is_pypy, source_size, sip_hash)
        try:
            code = res[3]
        except Exception:
            code = None
        try:
            pyc_flavor = res[0]
        except Exception:
            pyc_flavor = None
    except Exception:
        return  # every key stays UNKNOWN -> fail closed
    if code is None:
        return

    def _default_dis(co):
        if disinst_fn is not None:
            return disinst_fn(co)
        if XBytecode is None or get_opcode_mod is None:
            return []
        opc, BC = _resolve_opc_and_dis(get_opcode_mod, XBytecode,
                                       pyc_version=pyc_flavor, code=co)
        if opc is None or BC is None:
            return []
        try:
            bc = BC(co, opc)
            return list(bc.get_instructions())
        except Exception:
            return []

    result, _code_map = extract_from_code(code, _default_dis)
    proven_seen = {}
    for _owner, payload in result.items():
        found = payload.get("evidence", {})
        for key, ev in found.items():
            if ev is not None and key in _ALL_KEYS:
                proven_seen[key] = ev
    for key in _ALL_KEYS:
        ev = proven_seen.get(key)
        if ev is None:
            continue
        value = ev["default"]
        display = repr(value)
        lo, hi = ev["evidence_offset_range"]
        evidence = "%s default %r type %s bytecode LOAD_CONST@%s..BUILD_CONST_KEY_MAP@%s [%s]" % (
            key, value, ev["type"], lo, hi, ev["path"])
        report.set_default(key, value, display, evidence)
    return
