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
BUILD_CONST_KEY_MAP.  Verified shape (version-agnostic: 3.8+/3.10 emit
CALL_FUNCTION_KW; the WW CPython 3.7 line emits CALL_FUNCTION with the same
kw-name tuple pushed just before the call -- the decoder treats both identically):

    # per field, in source order, a self-contained leaf producer:
    LOAD_NAME   <Tunable / _TunableStructureElement>
    LOAD_CONST  <DEFAULT_LITERAL>          # the leaf default -> value_i
    LOAD_CONST  (<'default'>,)            # keyword-name tuple
    CALL_FUNCTION_KW 1 | CALL_FUNCTION n   # 3.7 reuses plain CALL_FUNCTION
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
  * a MAP-VALUE producer is recovered by FORWARD OPERAND-STACK EVALUATION of the
    whole class body from BUILD_CONST_KEY_MAP's OWN stack semantics (see
    _sim_value_producers): each CALL pops its operand group and pushes exactly one
    value, so an INNER Tunable* call (an operand of an enclosing
    _TunableStructureElement(...)) is consumed and never counted as a MAP-VALUE
    producer.  The N producers stacked just below the field-name tuple are, in
    source order, keys[0..N-1].  NO heuristic "guess where the value region starts"
    is used -- that guess is what truncated the real WW class bodies to a tail
    (3-of-22 / 2-of-26).
  * a CALL's operand count is taken from its instruction arg: modern CALL_FUNCTION_KW
    n counts the argument values and a separate kw-name tuple precedes the call;
    CPython 3.7's plain CALL_FUNCTION n (no CALL_FUNCTION_KW) also pushes the kw-name
    tuple just before the call.  The decoder therefore detects the immediately-
    preceding kw-name tuple (LOAD_CONST of a tuple of str) and pops it too, so both
    layouts give the same stack.  Pure-positional CALL_FUNCTION (no kw tuple) pops
    only its values + callable.
  * the default literal of a leaf is resolved by scanning that leaf's bounded argument
    window (bounded by the PREVIOUS top-level producer and any STORE/BUILD/return) for
    the 'default' kw-name packet and taking the scalar LOAD_CONST that is the packet's
    bound value (window order = nearest-to-leaf first).  Repeats are disambiguated by
    which packet is nearest the leaf; the floor bound guarantees a positional leaf can
    never leak a literal from the PREVIOUS field.
  * each of the N leaves must resolve to an unambiguous CONSTANT default.  A leaf
    whose default is not a literal constant (a name lookup / expression), a tuple/
    count mismatch, an unsupported/branchy body opcode in the class body, or any
    ambiguity marks the owning CLASS UNPROVEN (those keys stay UNKNOWN) -- we never
    use carrier_count or a typed guess.
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


def _leaf_default_ins(seq, call_idx, floor=-1):
    """Resolve the CONSTANT default literal of ONE value-leaf (the CALL at
    call_idx) by BYTECODE ORDER, never by a name scan.  ``floor`` is the index of
    the PREVIOUS top-level leaf's producing CALL (exclusive lower bound): every
    operand push of THIS leaf lies strictly AFTER that producer in source order,
    so the scan can never leak into the preceding value's literals.
    Recognises the shapes the WW tuning pyc and the verified compiles emit:

      S1 direct kwarg : _tse(default=<lit>)                    CALL_FUNCTION_KW 1
      S2 positional   : _tse(<x>, <lit>)                       CALL_FUNCTION n
      S3 nested tunab : _TunableStructureElement(TunableX(default=<lit>),
                                                 raw_type=...) -> OUTER call

    Unified rule (works for all three): within this leaf's contiguous argument
    window (backward from call_idx, never past floor or a STORE/BUILD boundary),
    the OWNING 'default' kw-name packet is the one nearest the leaf call; when S3
    nests, the inner TunableX call carries the only 'default' packet.  The default
    literal is the SCALAR LOAD_CONST immediately before that packet.  If no
    packet: the (positional) default is the deepest scalar literal in the window
    (S2), which with `floor` can never reach the prior leaf.

    Returns (literal, offset_of_its_LOAD_CONST) or None when the default slot is
    NOT a constant literal (a name/expression) -> fail closed, key stays UNKNOWN."""
    # backward scan of this leaf's bounded arg window for the 'default' kw-name
    # packet and the ordered operand literals.
    j = call_idx - 1
    window = []      # nearest-first (offset, const, instr)
    while j > floor and call_idx - j <= 24:
        op = _op(seq[j])
        # hard boundaries: a STORE / BUILD_ / RETURN / POP / the previous leaf's
        # producing CALL (`floor`) means we left the pure argument region of this
        # called-constructor.
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


def _sim_value_producers(seq, j, count, map_off, on_first_failure=None):
    """Recover, in SOURCE ORDER, the indexes of the CALL instructions that produce
    the `count` TOP-LEVEL value expressions stacked just below the field-name tuple
    LOAD_CONST at index `j` (immediately before BUILD_CONST_KEY_MAP pop count).

    Uses a forward operand-stack simulation of the enclosing class body from its
    first instruction up to `j`, so nested calls (e.g. an inner TunableX(...) that
    is an argument of the outer _TunableStructureElement(...)) are CONSUMED as
    operands and only the OUTER, top-level value producer survives to the map slot.

    Why not a "value region start + count CALLs" heuristic: the real WW class
    bodies (Actor 22 keys, Data 26 keys) are preceded by OTHER builds so the CALLs
    of THIS map are not a clean trailing block; guessing the region start mimed
    only the tail (LEAF_COUNT 3-of-22, 2-of-26).  Forward stack evaluation is exact:
    a CALL always pops its operand group and pushes exactly one result, so the
    stack discipline - not any name/offset scan - decides top-level vs nested.

    Each stack item is tagged with its producing instruction index.  Returning:
        list of `count` call-indexes (oldest first = the tuple's order), OR None
    when the class body uses an unsupported/ambiguous opcode that could affect the
    target stack (fail closed), or when stack balance can't be proven at `j`
    (underflow / unexpected depth).

    ``on_first_failure`` is an OPT-IN observer used ONLY by the read-only real-pyc
    diagnostic (ww_p32_loader_origin_real_diag.py).  When None (production) the
    behaviour and return values are byte-for-byte identical to a caller that never
    passes it.  When a callable, it is invoked EXACTLY ONCE at the FIRST class-body
    opcode where the sim decides to fail closed, with a metadata dict so the
    diagnostic can pinpoint which 3.7 xdis instruction diverges:
        reason   UNSUPPORTED_OPCODE | STACK_UNDERFLOW | MAP_ARITY_MISMATCH
        t        failing instruction index in seq
        off      its code offset
        needed   operand count the op demanded (STACK_UNDERFLOW / MAP_ARITY)
        avail    operand stack depth available at the failing index
    The observer must NOT rely on the sim continuing: the sim ALWAYS returns None
    after consulting it (fail-closed is unchanged)."""
    # Operand-stack effect table (net effect on the list is handled inline).  A
    # stack item is either None (uninteresting operand) or an int (index of the
    # CALL that produced this value) so the producers can be read back later.
    stack = []
    # opcode families
    PUSH1 = ("LOAD_CONST", "LOAD_NAME", "LOAD_GLOBAL", "LOAD_FAST",
             "LOAD_DEREF", "LOAD_CLASSDEREF", "LOAD_ATTR",
             "LOAD_BUILD_CLASS")
    CALLOP = ("CALL_FUNCTION", "CALL_FUNCTION_KW", "CALL_FUNCTION_EX",
              "CALL_METHOD", "CALL")

    _fired = [False]

    def _fail(reason, needed=None, ins=None, t=None):
        """Fail closed: record the FIRST failure through the optional observer,
        then always return None.  When no observer is wired this is a pure
        `return None` (production semantics unchanged)."""
        if on_first_failure is not None and not _fired[0]:
            _fired[0] = True
            on_first_failure({
                "reason": reason,
                "t": t,
                "ins": ins,
                "off": _off(ins) if ins is not None else -1,
                "opname": _op(ins) if ins is not None else "",
                "arg": getattr(ins, "arg", None) if ins is not None else None,
                "argval": getattr(ins, "argval", None) if ins is not None else None,
                "argrepr": getattr(ins, "argrepr", None) if ins is not None else None,
                "needed": needed,
                "avail": len(stack),
            })
        return None

    for t in range(0, j + 1):
        if t > j:
            break
        ins = seq[t]
        op = _op(ins)
        if op == "KEEP_ALIVE" or op == "NOP" or op == "EXTENDED_ARG":
            continue
        if op in PUSH1:
            stack.append(None)          # a plain operand (not a known producer)
            continue
        if op == "LOAD_METHOD":
            # CPython 3.7 method-call protocol (the WW compiler target).  Unlike a
            # plain LOAD_* the receiver object ON the stack is CONSUMED and replaced
            # by TWO method-protocol slots [method|callable, self-or-NULL] so the
            # matching CALL_METHOD can pop them.  Net effect +1 here, cancelled by
            # CALL_METHOD's arity+2 pop so a matched pair nets 0 (no residual).
            if not stack:
                return _fail("STACK_UNDERFLOW", needed=1, ins=ins, t=t)
            stack.pop()                 # consume the receiver
            stack.append(None)          # method / callable slot
            stack.append(None)          # self-or-NULL slot (top)
            continue
        if op == "DUP_TOP":
            if not stack:
                return _fail("STACK_UNDERFLOW", needed=1, ins=ins, t=t)
            stack.append(stack[-1])
            continue
        if op == "DUP_TOP_TWO":
            if len(stack) < 2:
                return _fail("STACK_UNDERFLOW", needed=2, ins=ins, t=t)
            stack.extend(stack[-2:])
            continue
        if op == "ROT_TWO":
            if len(stack) >= 2:
                stack[-1], stack[-2] = stack[-2], stack[-1]
            continue
        if op == "ROT_THREE":
            if len(stack) >= 3:
                stack[-1], stack[-3], stack[-2] = stack[-3], stack[-2], stack[-1]
            continue
        if op == "POP_TOP":
            if stack:
                stack.pop()
            continue
        if op.startswith("STORE_NAME") or op == "STORE_GLOBAL" or op == "STORE_FAST" \
           or op == "STORE_DEREF" or op == "STORE_CLASSDEREF":
            if stack:
                stack.pop()
            continue
        if op == "STORE_ATTR" or op == "STORE_SUBSCR":
            # pops (obj, value)  / (obj, subscript, value)
            for _ in range(2 if op == "STORE_ATTR" else 3):
                if stack:
                    stack.pop()
                else:
                    return _fail("STACK_UNDERFLOW",
                                 needed=(2 if op == "STORE_ATTR" else 3),
                                 ins=ins, t=t)
            continue
        if op.startswith("DELETE_"):
            continue                       # symbol-table only, no operand stack
        if op.startswith("UNARY_"):
            # pop 1 push 1: neutral net; keep the top tag (already on stack)
            continue
        if op.startswith("BINARY_") or op == "BINARY_SUBSCR" or op == "COMPARE_OP" \
           or op == "INPLACE_OP" or op == "BINARY_OP":
            # pop 2 push 1 -> net -1
            for _ in range(2):
                if stack:
                    stack.pop()
                else:
                    return _fail("STACK_UNDERFLOW", needed=2, ins=ins, t=t)
            stack.append(None)
            continue
        if op in ("BUILD_TUPLE", "BUILD_LIST", "BUILD_SET", "BUILD_SLICE",
                  "BUILD_STRING"):
            cnt = getattr(ins, "arg", 1) or 1
            if len(stack) < cnt:
                return _fail("STACK_UNDERFLOW", needed=cnt, ins=ins, t=t)
            del stack[-cnt:]
            stack.append(None)
            continue
        if op == "BUILD_MAP":
            cnt = getattr(ins, "arg", 0) or 0
            # CPython >=3.6 BUILD_MAP n pops n key + n value pairs (2n operands)
            # pre-3.6 semantics differ but WW is 3.7+; accept modern form.
            need = 2 * cnt
            if len(stack) < need:
                return _fail("STACK_UNDERFLOW", needed=need, ins=ins, t=t)
            del stack[-need:]
            stack.append(None)
            continue
        if op == "MAKE_FUNCTION" or op == "MAKE_FUNCTION_NORGS":
            # A method/def inside the class body binds a NAME.  CPython pushes the
            # code object then (for a def) the __qualname__ const then MAKE_FUNCTION,
            # which pops code + qualname (+ optional closure/kwdefault/default/
            # annotation operands counted by flag bits) and pushes the function.
            # Model exactly so the class's own statements net-balance.
            flags = getattr(ins, "arg", 0) or 0
            extras = 0
            for _bit in (0x01, 0x04, 0x08, 0x10):   # closure/kwdefault/defaults/annot
                if flags & _bit:
                    extras += 1
            need = 2 + extras           # code const + __qualname__ const + optionals
            if len(stack) < need:
                return _fail("STACK_UNDERFLOW", needed=need, ins=ins, t=t)
            del stack[-need:]
            stack.append(None)         # the function object result
            continue
        if op == "CALL_METHOD":
            # CPython 3.7 method-call protocol (mirror of the LOAD_METHOD branch):
            # pops `arity` argument values + the two method-protocol slots that
            # LOAD_METHOD left (self/NULL on top, method below) = arity+2, then
            # pushes one result.  Net -(arity+1); matched with LOAD_METHOD's +1 a
            # pair nets 0 (no residual).  CALL_METHOD never carries a kw-name tuple.
            arity = getattr(ins, "arg", 0) or 0
            need = arity + 2            # values + method slot + self/NULL slot
            if len(stack) < need:
                return _fail("STACK_UNDERFLOW", needed=need, ins=ins, t=t)
            del stack[-need:]
            stack.append(t)             # this call produced exactly one value
            continue
        if op in CALLOP:
            # A CALL pops its operand group and pushes one result.  `arity` = the
            # number of argument VALUES (positional+keyword) the compiler counted.
            #
            # Whether a keyword-NAME tuple is ALSO on the stack (and must be popped
            # with this call) is NOT encoded uniformly across CPython versions:
            #    * modern (3.8+) keyword calls emit CALL_FUNCTION_KW n ;  n already
            #      counts the kw values, and the kw-name tuple precedes the call.
            #    * CPython 3.7 (the WW compiler) has NO CALL_FUNCTION_KW: keyword
            #      calls emit a plain CALL_FUNCTION n where n still counts only the
            #      values, and the kw-name tuple is pushed just before the call.
            #    * a truly positional call (modern CALL_FUNCTION n with no keywords)
            #      has NO preceding name tuple.
            # So detect the name tuple by looking at the instruction immediately
            # before this CALL (skipping NOPs): if it is a LOAD_CONST of a tuple of
            # str (a kw-name packet), pop it too; otherwise the call is positional.
            has_names = False
            k = t - 1
            while k >= 0 and _op(seq[k]) in ("NOP", "EXTENDED_ARG", "KEEP_ALIVE"):
                k -= 1
            if k >= 0 and _op(seq[k]) == "LOAD_CONST":
                cv = _const(seq[k])
                if isinstance(cv, tuple) and cv and all(
                        isinstance(s, str) for s in cv):
                    has_names = True
            arity = getattr(ins, "arg", 0) or 0
            need = arity + (2 if has_names else 1)   # values + (names?) + callable
            if len(stack) < need:
                return _fail("STACK_UNDERFLOW", needed=need, ins=ins, t=t)
            del stack[-need:]
            stack.append(t)            # this CALL produced exactly one stack value
            continue
        if op == "RETURN_VALUE":
            if stack:
                stack.pop()
            continue
        if op in ("BEGIN_FINALLY", "END_FINALLY", "POP_EXCEPT", "POP_BLOCK",
                  "WITH_CLEANUP_START", "WITH_CLEANUP_FINISH", "CLEANUP_THROW",
                  "PUSH_EXC_INFO"):
            # exception table bookkeeping; WW data-class owners do not wrap the
            # map build in a try/with.  If such control flow precedes the build it
            # is outside the straight-line value region -> not a map-value operand.
            return _fail("UNSUPPORTED_OPCODE", ins=ins, t=t)
        # Any remaining opcode (e.g. a real JUMP_*, SETUP_*, IMPORT_, FORMAT_VALUE)
        # either does not affect a straight-line class-body operand stack in a way
        # we can prove or signals non-straight-line control flow.  Fail closed
        # rather than guess.
        return _fail("UNSUPPORTED_OPCODE", ins=ins, t=t)

    # At index j the field-name tuple was JUST pushed (LOAD_CONST).  So the stack
    # above the `count` values is: [*count producers (oldest ../first), keytuple].
    # We simulated only up to and INCLUDING j (the keytuple push was recorded as a
    # plain operand).  Verify the bottom `count + 1` look right and that there is
    # exactly one keytuple on top of exactly `count` value slots with nothing else
    # unexpected directly relevant.  Because earlier unrelated builds (method defs,
    # __slots__ etc.) are balanced, require: the top of stack at j has at least
    # count+1 entries (count values + key tuple) and the count entries directly
    # below the key tuple are all CALL-producers (ints), in source order.
    if len(stack) < count + 1:
        # stack too shallow at the tuple: cannot prove `count` top-level values.
        return _fail("MAP_ARITY_MISMATCH", needed=count + 1,
                     ins=seq[j], t=j)
    # key tuple is the very last push (top of stack after j).
    if stack[-1] is not None:
        # the final push before map must be the key tuple (a plain const, None tag)
        return _fail("MAP_ARITY_MISMATCH", ins=seq[j], t=j)
    wait = stack[-1 * (count + 1):]       # bottom..top preserved order
    vals = wait[:-1]                       # exclude the key tuple (top)
    # exactly count producers, each an int CALL index, none None/other
    if len(vals) != count:
        return _fail("MAP_ARITY_MISMATCH",
                     needed=(count + 1), ins=seq[j], t=j)
    if not all(isinstance(x, int) for x in vals):
        return _fail("MAP_ARITY_MISMATCH",
                     needed=(count + 1), ins=seq[j], t=j)
    return list(vals)


def decode_structure(seq, path_name, on_first_failure=None):
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
        # keys authoritative.  Recover the ordered top-level value PRODUCERS from
        # BUILD_CONST_KEY_MAP's own stack semantics (forward symbolic evaluation).
        call_idxs = _sim_value_producers(seq, j, len(keys), _off(seq[i]),
                                         on_first_failure=on_first_failure)
        if call_idxs is None:
            unresolved.append(("stack-sim value recovery failed (unsupported/branchy "
                              "body or arity mismatch)", _off(seq[i])))
            i += 1
            continue
        ok = True
        for idx_key in range(len(keys)):
            key = keys[idx_key]
            # floor = the previous top-level leaf's producing CALL so the default
            # window for this leaf never reaches into the preceding value's packets.
            floor = call_idxs[idx_key - 1] if idx_key > 0 else -1
            dl = _leaf_default_ins(seq, call_idxs[idx_key], floor)
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
# RESIDUAL-PROVENANCE diagnostic replay (DESCRIPTIVE ONLY -- never used by
# populate_defaults / decode / decode_code_with_instr / _sim_value_producers;
# production parser semantics are NOT affected by its existence).
#
# The decision sim above (see _sim_value_producers) is the DECISION engine and is
# left byte-for-byte untouched.  For a TUNABLE_STRUCTURE map whose BUILD_CONST_KEY_MAP
# balance is broken NOT by a leaf-default but by REAL EXTRA stack depth accumulated
# in the preamble (Actor +2, Data +4 while Props +0 and PASS on the real pyc), a
# diagnostic-only replay reconstructs how every stack slot was built so it can say
# WHICH producer's push first left a surplus and WHAT the surplus nodes are.
#
# Returns a dict (never raises), or None if a truly unmodellable op is met:
#    expected_value_count   count (== len(field tuple))
#    stack_depth_at_j       total sim depth AT j INCLUDING the field-name tuple
#    actual_stack_depth     stack_depth_at_j - 1   (just BEFORE the tuple push)
#    extra_stack_count      actual_stack_depth - expected_value_count   (>=0)
#    depth_timeline         [(index, opname, offset, depth_after)]
#    residual_items         bottom-most surplus nodes (depth < tuple count region)
#    first_divergence       {index, opname, offset, reason} (lowest surviving producer)
# Every node carries producer offset / opname / kind and, where the node was built
# by an aggregating op (BUILD_MAP/BUILD_LIST/.../CALL*), a __children__ of up to one
# level (child producer offsets only - never a full tree).
def sim_residual_report(seq, j, count, map_off):
    _PUSH1 = ("LOAD_CONST", "LOAD_NAME", "LOAD_GLOBAL", "LOAD_FAST",
              "LOAD_DEREF", "LOAD_CLASSDEREF", "LOAD_ATTR",
              "LOAD_BUILD_CLASS")
    _CALLOP = ("CALL_FUNCTION", "CALL_FUNCTION_KW", "CALL_FUNCTION_EX",
               "CALL_METHOD", "CALL")

    def _mk(kind, off, opname, argval=None, children=None, rep=None):
        node = {"kind": kind, "producer_offset": off, "producer_opname": opname,
                "argval": argval, "children": list(children) if children else [],
                "short_repr": rep if rep is not None else _short(argval, kind)}
        return node

    def _short(argval, kind):
        try:
            if kind == "build" or kind == "call":
                # argval is the op name string -> a compact `BUILD_MAP(len=N)`
                return "%s" % (argval,)
            if kind == "const":
                return repr(argval)
            return str(argval)
        except Exception:  # noqa: BLE001
            return "?"

    stack = []            # symbolic nodes (decision-free diagnostic mirror)
    depth_timeline = []
    try:
        for t in range(0, j + 1):
            ins = seq[t]
            op = _op(ins)
            if op in ("KEEP_ALIVE", "NOP", "EXTENDED_ARG"):
                continue
            _of = _off(ins)
            if op == "LOAD_METHOD":
                # CPython 3.7 protocol: consume the receiver, push two
                # method-protocol slots (method / callable, self-or-NULL).  The
                # receiver is always a real operand below; pop it and push 2.
                if stack:
                    stack.pop()
                stack.append(_mk("name", _of, op,
                                 argval=getattr(ins, "argval", None)))
                stack.append(_mk("name", _of, op,
                                 argval=getattr(ins, "argval", None)))
            elif op in _PUSH1:
                if op == "LOAD_CONST":
                    stack.append(_mk("const", _of, op, argval=_const(ins)))
                else:
                    stack.append(_mk("name", _of, op,
                                     argval=getattr(ins, "argval", None)))
            elif op == "DUP_TOP":
                if stack:
                    stack.append(dict(stack[-1]))
            elif op == "DUP_TOP_TWO":
                if len(stack) >= 2:
                    stack.append(dict(stack[-2])); stack.append(dict(stack[-1]))
            elif op == "ROT_TWO":
                if len(stack) >= 2:
                    stack[-1], stack[-2] = stack[-2], stack[-1]
            elif op == "ROT_THREE":
                if len(stack) >= 3:
                    stack[-1], stack[-3], stack[-2] = stack[-3], stack[-2], stack[-1]
            elif op == "POP_TOP":
                if stack:
                    stack.pop()
            elif op.startswith("STORE_NAME") or op in ("STORE_GLOBAL", "STORE_FAST",
                                                        "STORE_DEREF",
                                                        "STORE_CLASSDEREF"):
                if stack:
                    stack.pop()
            elif op in ("STORE_ATTR", "STORE_SUBSCR"):
                for _ in range(2 if op == "STORE_ATTR" else 3):
                    if stack:
                        stack.pop()
            elif op.startswith("DELETE_"):
                pass
            elif op.startswith("UNARY_"):
                pass                       # pop1 push1 net-neutral, keep top tag
            elif op.startswith("BINARY_") or op in ("BINARY_SUBSCR", "COMPARE_OP",
                                                     "INPLACE_OP", "BINARY_OP"):
                for _ in range(2):
                    if stack:
                        stack.pop()
                stack.append(_mk("name", _of, op))
            elif op in ("BUILD_TUPLE", "BUILD_LIST", "BUILD_SET", "BUILD_SLICE",
                        "BUILD_STRING"):
                cnt = getattr(ins, "arg", 1) or 1
                if len(stack) >= cnt:
                    kids = stack[-cnt:]
                    del stack[-cnt:]
                    stack.append(
                        _mk("build", _of, op, argval=op,
                            children=[c["producer_offset"] for c in kids]))
            elif op == "BUILD_MAP":
                cnt = getattr(ins, "arg", 0) or 0
                need = 2 * cnt
                if len(stack) >= need:
                    kids = stack[-need:]
                    del stack[-need:]
                    stack.append(
                        _mk("build", _of, op, argval=op,
                            children=[c["producer_offset"] for c in kids]))
            elif op in ("MAKE_FUNCTION", "MAKE_FUNCTION_NORGS"):
                flags = getattr(ins, "arg", 0) or 0
                extras = 0
                for _bit in (0x01, 0x04, 0x08, 0x10):
                    if flags & _bit:
                        extras += 1
                need = 2 + extras
                if len(stack) >= need:
                    kids = stack[-need:]
                    del stack[-need:]
                    stack.append(_mk("call", _of, op, argval=op,
                                     children=[c["producer_offset"] for c in kids]))
            elif op == "CALL_METHOD":
                # CPython 3.7 protocol mirror: pop arity values + method slot +
                # self/NULL slot = arity+2; matched with LOAD_METHOD net 0.
                arity = getattr(ins, "arg", 0) or 0
                need = arity + 2
                take_n = need if len(stack) >= need else len(stack)
                kids = stack[-take_n:] if take_n else []
                if take_n:
                    del stack[-take_n:]
                stack.append(_mk("call", _of, op, argval=op,
                                 children=[c["producer_offset"] for c in kids]))
            elif op in _CALLOP:
                has_names = False
                k = t - 1
                while k >= 0 and _op(seq[k]) in ("NOP", "EXTENDED_ARG", "KEEP_ALIVE"):
                    k -= 1
                if k >= 0 and _op(seq[k]) == "LOAD_CONST":
                    cv = _const(seq[k])
                    if isinstance(cv, tuple) and cv and all(
                            isinstance(s, str) for s in cv):
                        has_names = True
                arity = getattr(ins, "arg", 0) or 0
                need = arity + (2 if has_names else 1)
                take_n = need if len(stack) >= need else len(stack)
                kids = stack[-take_n:] if take_n else []
                if take_n:
                    del stack[-take_n:]
                stack.append(_mk("call", _of, op, argval=op,
                                 children=[c["producer_offset"] for c in kids]))
            elif op == "RETURN_VALUE":
                if stack:
                    stack.pop()
            elif op in ("BEGIN_FINALLY", "END_FINALLY", "POP_EXCEPT", "POP_BLOCK",
                        "WITH_CLEANUP_START", "WITH_CLEANUP_FINISH", "CLEANUP_THROW",
                        "PUSH_EXC_INFO"):
                return {"unsupported": op, "index": t, "offset": _of}
            else:
                # unmodelled straight-line consumer that popped nothing we track
                # would corrupt depth; report instead of guess.
                if op.startswith(("JUMP", "SETUP", "IMPORT", "FORMAT")):
                    return {"unsupported": op, "index": t, "offset": _of}
                # tolerate small push-1/other by continuing depth bookkeeping
                continue
            depth_timeline.append((t, op, _of, len(stack)))

        shelf = stack[:-1] if stack else []          # drop the field tuple (top)
        actual = len(stack) - 1 if stack else 0       # depth before the tuple
        extra = actual - count
        residual = shelf[:extra] if extra > 0 else []
        # FIRST_DIVERGENCE: lowest producer offset that survived un-popped to j
        # (an unmatched surplus push); this is where net depth first stopped
        # balancing against the map region.
        fd = None
        if extra > 0 and residual:
            low = min(residual, key=lambda n: n["producer_offset"])
            fd = {"index": None, "offset": low["producer_offset"],
                  "opname": low["producer_opname"], "kind": low["kind"],
                  "reason": "unmatched-surplus-push"}
        return {"expected_value_count": count,
                "stack_depth_at_j": len(stack),
                "actual_stack_depth": actual,
                "extra_stack_count": extra,
                "residual_items": residual,
                "depth_timeline": depth_timeline,
                "first_divergence": fd,
                "field_tuple_index": j, "map_offset": map_off}
    except Exception:  # noqa: BLE001
        return {"error": True}
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
        # keys authoritative.  Recover the ordered top-level value PRODUCERS from
        # BUILD_CONST_KEY_MAP's own stack semantics (forward symbolic evaluation).
        call_idxs = _sim_value_producers(seq, j, len(keys), _off(seq[i]),
                                         on_first_failure=on_first_failure)
        if call_idxs is None:
            unresolved.append(("stack-sim value recovery failed (unsupported/branchy "
                              "body or arity mismatch)", _off(seq[i])))
            i += 1
            continue
        ok = True
        for idx_key in range(len(keys)):
            key = keys[idx_key]
            # floor = the previous top-level leaf's producing CALL so the default
            # window for this leaf never reaches into the preceding value's packets.
            floor = call_idxs[idx_key - 1] if idx_key > 0 else -1
            dl = _leaf_default_ins(seq, call_idxs[idx_key], floor)
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
def _minor_from(pyc_version, version_tuple):
    """Extract the CPython minor version to pick the opcode table: prefer the
    loaded pyc's own reported version, else the (major,minor) tuple."""
    minor = None
    v = getattr(pyc_version, "version", pyc_version)
    if isinstance(v, (tuple, list)) and len(v) >= 2:
        minor = v[1]
    else:
        m = getattr(pyc_version, "minor", None)
        if m is None:
            m = getattr(getattr(pyc_version, "version", None), "minor", None)
        minor = m
    if minor is None and version_tuple is not None and len(version_tuple) >= 2:
        minor = version_tuple[1]
    if minor is None:
        minor = 7      # WW ships CPython 3.7; safest fallback (fail-hard later)
    return minor


def _modern_dis(co, pyc_version):
    """Disassemble one code object with the xdis 6.x MODERN recipe that the real-pyc
    diagnostic proved works:

        opc  = xdis.get_opcode((3, minor), PythonImplementation.CPython)
        inst = list(Bytecode(co, opc))

    The LEGACY xdis 6.x-ABSENT names (xdis.op_imports.PythonImplementation,
    xdis.disasm.Bytecode, Bytecode(...).get_instructions(),
    get_opcode_mod(...)) do NOT exist in xdis 6.3.0 -- production silently
    returned [] through them, which with the decoder's fail-closed gate surfaced as
    DEFAULT_UNKNOWN_COUNT=11.  Return [] (caller fails closed) on any error."""
    try:
        import xdis
        from xdis.version_info import PythonImplementation
        from xdis import Bytecode
        minor = _minor_from(pyc_version, None)
        opc = xdis.get_opcode((3, minor), PythonImplementation.CPython)
        return list(Bytecode(co, opc))
    except Exception:
        return []


def extract_from_code(code, disinst_fn, wanted=None):
    """code: module code object.  disinst_fn(co) -> iterable of instructions
    (xdis Bytecode iteration).  wanted: {owner: [keys]}; default from
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
            try:
                out = list(disinst_fn(co))
                if out:
                    return out
            except Exception:
                pass
        # modern xdis recipe (see _modern_dis); [] -> caller keeps the key UNKNOWN
        return _modern_dis(co, pyc_flavor)

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
