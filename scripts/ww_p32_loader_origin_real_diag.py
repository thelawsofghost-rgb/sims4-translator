#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
ww_p32_loader_origin_real_diag.py -- MINIMAL read-only REAL-PYC decoder diagnostic
for the P32 TUNING_DEFAULT_SEMANTICS gate (DEFAULT_UNKNOWN_COUNT=11 on the real
Windows run while the synthetic decoder PASSED).

WHY THIS FILE EXISTS
--------------------
The synthetic fixtures compile on Python 3.8/3.10 with stdlib `dis`, but the REAL
WW tuning pyc is a CPython 3.7.9 marshal blob read by `xdis`.  "synthetic PASS"
therefore does NOT prove "xdis-on-3.7 PASS": xdis may expose instruction objects
with different attribute shapes, or the decoder may not be reached at the same
traversal / opcode-normalization point.  This probe STAGES the decoder so a
Windows real run reports exactly WHERE it diverges from the synthetic
expectation, WITHOUT touching the production decoder, WITHOUT hardcoding
defaults, WITHOUT generating a catalog, and WITHOUT changing algorithms until a
stage is evidenced.

It answers four staged questions per WW owner class body:
  1 CLASS_DISCOVERY      - is the nested code object found by the SAME recursive
                           traversal populate_defaults uses (_iter_code_with_path)?
  2 MAP_DISCOVERY        - does it contain BUILD_CONST_KEY_MAP -> STORE_NAME
                           TUNABLE_STRUCTURE?
  3 KEY_TUPLE_EXTRACTION - what is the const field-name tuple / arg count?
  4 LEAF_BOUNDARY        - how many top-level value leaves the decoder counts
                           (vs KEY_COUNT); then per-key candidate + reject reason.

Output file (ASCII, SMALL): three CLASS blocks + per-key rows (only for the 11
correlated keys) + a compact xdis attribute probe + exactly one
REAL_DECODER_FAILURE_STAGE line.  No pyc dump, no large disassembly.

Rules (hard, same as the rest of P32):
  * READ ONLY. ZERO_WRITE_TO_MODS=YES, ZERO_WRITE_TO_SAVES=YES.  Never enter the
    game, never generate a catalog, never modify the runner or decoder.
  * FAIL CLOSED whenever xdis / the pyc / disassembly is unavailable: the stage
    is recorded, defaults are NEVER guessed.  This is a diagnostic, not a fix.
  * If even the _WickedWhimsAnimationData class is not found -> suspect recursive
    code-object traversal / xdis object-type detection.  If a class is found but
    no BUILD_CONST_KEY_MAP -> suspect opcode normalization.  Only if the map is
    found and KEY_COUNT is right but LEAF_COUNT is wrong do we fix the leaf
    boundary; only if key/leaf are right but the candidate is UNKNOWN do we fix
    default extraction.  The single FAILURE_STAGE reports which.

USAGE (Windows, repo root):
  powershell -ExecutionPolicy Bypass -Command ^
    & 'C:\Users\thela\AppData\Local\Programs\Python\Python37-32\python.exe' ^
       scripts\ww_p32_loader_origin_real_diag.py ^
       --tuning-pyc <extracted _ts4_animations_tuning.pyc> ^
       --out output\p32\p32_defaults_real_diagnostic.txt

Exit: 0 = diagnostic written (a FAILED stage is reported by the line, not by the
exit code); 1 = could not produce output.
"""
from __future__ import annotations

import argparse
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ww_p32_loader_origin_defaults as lod  # noqa: E402 reuses decoder internals

# production targets -- keep aligned with defaults._CORRELATED_KEYS so the probe
# inspects the SAME structures the decoder decodes.
_CLASSES = ("_WickedWhimsAnimationActor",
            "_WickedWhimsAnimationPropsData",
            "_WickedWhimsAnimationData")
_WANTED = {
    "_WickedWhimsAnimationActor": (
        "animation_x_offset", "animation_y_offset", "animation_z_offset",
        "animation_angle_offset", "animation_facing_offset"),
    "_WickedWhimsAnimationPropsData": (
        "prop_animation_clip_name", "prop_geometry_state"),
    "_WickedWhimsAnimationData": (
        "object_animation_clip_name", "object_geometry_state",
        "object_material_state", "animation_version"),
}
_TUNABLE_STORE = "TUNABLE_STRUCTURE"
_PROBE_OPS = ("LOAD_CONST", "CALL_FUNCTION", "CALL_FUNCTION_KW",
              "BUILD_CONST_KEY_MAP", "STORE_NAME")


# ---------------------------------------------------------------------------
def _op(ins):
    return getattr(ins, "opname", "")


def _off(ins):
    return getattr(ins, "offset", -1)


def _argsval(ins):
    return getattr(ins, "argval", None)


def _find_index_by_off(seq, off):
    """Return the index of the first instruction whose offset equals `off`, else -1.
    Builds an index mapping so repeated calls are cheap (lists are small)."""
    if off < 0:
        return -1
    return next((k for k, ins in enumerate(seq) if _off(ins) == off), -1)


def _attrs_proj(ins):
    """Deterministic projection of a real xdis instruction row -- the fields the
    decoder reads plus those whose shape most plausibly differs stdlib-dis vs
    xdis, so an unexpected 3.7 representation is visible verbatim."""
    want = ("opname", "opcode", "arg", "argval", "argrepr",
            "offset", "starts_line", "lineno", "is_jump_target")
    return " ".join("%s=%r" % (w, getattr(ins, w))
                    for w in want if hasattr(ins, w))


# ---------------------------------------------------------------------------
def iter_xdis_instructions(co, version=None):
    """Disassemble one code object through xdis using the opcode table matching the
    loaded pyc's CPython minor line (WW ships CPython 3.7 -> minor 7).  ``version``
    is the xdis version value from load_module_from_file_object (res[0]): in xdis
    >= 6 this is a (major, minor) tuple; older builds expose an object with
    .version/.minor.  Falls back to CPython 3.7 (the WW line).

    Uses the xdis 6.x modern recipe: xdis.Bytecode(co, opc) is an ITERABLE; the
    legacy ``.get_instructions()`` and ``xdis.get_opcode_mod`` names do NOT exist in
    xdis 6.x and are avoided here deliberately (they are the documented divergence
    suspected behind DEFAULT_UNKNOWN_COUNT=11 on Windows)."""
    import xdis
    from xdis.version_info import PythonImplementation
    from xdis import Bytecode
    minor = None
    if version is not None:
        v = getattr(version, "version", version)
        if isinstance(v, (tuple, list)) and len(v) >= 2:
            minor = v[1]
        else:
            m = getattr(version, "minor", None)
            if m is None:
                m = getattr(getattr(version, "version", None), "minor", None)
            minor = m
    if minor is None:
        minor = 7
    vt = (3, minor)
    opc = xdis.get_opcode(vt, PythonImplementation.CPython)
    return list(Bytecode(co, opc))


def discover_classes(code):
    """Return {dotted_path: code_object} for the module + every nested code object
    via the SAME recursive traversal populate_defaults uses."""
    out = {}
    for path, cobj in lod._iter_code_with_path(code):
        out[".".join(path)] = cobj
    return out


# ---------------------------------------------------------------------------
class DiagResult(object):
    """Accumulates per-class staged findings + the single failure stage."""

    def __init__(self):
        self.lines = []
        self.found = {}
        # real default-extraction failures on classes whose leaf count matched
        self.leaf_ok_by_class = {}        # cls -> bool
        self.default_unknown_on = []      # cls where a leaf_ok class had UNKNOWN

    def line(self, s):
        self.lines.append(s)
        return s


def analyze_class(cls, dotted, cobj, res, version=None):
    res.line("CLASS=%s" % cls)
    res.line("FOUND=YES")
    res.line("DOTTED_PATH=%s" % dotted)
    res.line("FIRSTLINENO=%s" % getattr(cobj, "co_firstlineno", "?"))
    res.found[cls] = True
    res.leaf_ok_by_class[cls] = False

    # disassemble the class body through xdis
    try:
        seq = iter_xdis_instructions(cobj, version=version)
    except Exception as exc:  # noqa: BLE001
        res.line("DISASSEMBLY=FAIL err=%r" % (exc,))
        res.line("STAGE=MAP_DISCOVERY")
        return
    res.line("DISASSEMBLY=OK insns=%d" % len(seq))

    # compact xdis attribute probe on this class body
    seen = set()
    for ins in seq:
        op = _op(ins)
        if op in _PROBE_OPS and op not in seen:
            seen.add(op)
            res.line("XDIS_ATTR %s :: %s" % (op, _attrs_proj(ins)))

    # MAP_DISCOVERY ------------------------------------------------
    store_tunable = any(
        _op(ins) == "STORE_NAME" and _argsval(ins) == _TUNABLE_STORE
        for ins in seq)
    res.line("STORE_TUNABLE_STRUCTURE=%s" % ("YES" if store_tunable else "NO"))

    # find BUILD_CONST_KEY_MAP with its preceding field-name tuple
    maps = []
    n = len(seq)
    for idx, ins in enumerate(seq):
        if _op(ins) != "BUILD_CONST_KEY_MAP":
            continue
        bag = getattr(ins, "arg", -1)
        j = idx - 1
        while j >= 0 and _op(seq[j]) == "NOP":
            j -= 1
        ft = None
        if j >= 0 and _op(seq[j]) == "LOAD_CONST":
            ft = _argsval(seq[j])
        maps.append({"idx": idx, "off": _off(ins), "arg": bag,
                     "field_off": _off(seq[j]) if j >= 0 else -1,
                     "fieldtuple": ft})
    res.line("BUILD_CONST_KEY_MAP_COUNT=%d" % len(maps))

    # use only the FIRST authoritative map (defensive: no flooding)
    if not maps:
        res.line("STAGE=MAP_DISCOVERY")
        return
    m = maps[0]
    ft = m["fieldtuple"]
    res.line("BUILD_CONST_KEY_MAP_OFFSET=%d" % m["off"])

    # KEY_TUPLE_EXTRACTION ----------------------------------------
    if not (isinstance(ft, tuple) and ft and all(isinstance(k, str) for k in ft)):
        res.line("FIELD_TUPLE=NOT-A-STR-TUPLE (%r)" % (ft,))
        res.line("STAGE=KEY_TUPLE_EXTRACTION")
        return
    keys = list(ft)
    res.line("KEY_COUNT=%d" % len(keys))
    res.line("KEY_TUPLE=%s" % ",".join(keys))
    want = _WANTED.get(cls, ())
    idx_of = {k: i for i, k in enumerate(keys)}
    found_tgt = [k for k in want if k in idx_of]
    res.line("TARGET_KEYS_FOUND=%s" % ",".join(found_tgt))
    if m["arg"] != len(keys):
        res.line("ARG_COUNT=%d TUPLE_LEN=%d MISMATCH stage=KEY_TUPLE_EXTRACTION"
                 % (m["arg"], len(keys)))
        return

    # LEAF_BOUNDARY + DEFAULT_EXTRACTION --------------------------
    # value region lo .. ft_idx where ft_idx = the field-name tuple LOAD_CONST that
    # precedes BUILD_CONST_KEY_MAP -- the SAME upper bound defaults.decode_structure
    # uses (range(lo, j) with j = field-tuple index).  Scanning up to the MAP index
    # instead would put the key-tuple LOAD_CONST (the final leaf's follower) inside
    # the window and wrongly REJECT the last leaf as non-constructor (LEAF_COUNT
    # one short).
    ft_idx = _find_index_by_off(seq, m["field_off"])
    hi = ft_idx if ft_idx >= 0 else m["idx"]
    lo = hi - 1
    while lo >= 0 and not (
            _op(seq[lo]).startswith("STORE") or
            _op(seq[lo]).startswith("BUILD_") or
            _op(seq[lo]) in ("RETURN_VALUE", "POP_TOP", "END_FINALLY")):
        lo -= 1
    lo += 1
    # identical top-level leaf discriminator
    leaves = []
    rejected = []
    for t in range(lo, hi):
        if _op(seq[t]) not in lod._CALL_OPCODES:
            continue
        nx = t + 1
        while nx < hi and _op(seq[nx]) == "NOP":
            nx += 1
        nxop = _op(seq[nx]) if nx < hi else None
        if nx >= hi or nxop in ("LOAD_NAME", "LOAD_GLOBAL", "LOAD_METHOD",
                                "LOAD_FAST", "LOAD_DEREF", "LOAD_CLASSDEREF",
                                "LOAD_BUILD_CLASS"):
            leaves.append((t, _op(seq[t]), nxop))
        else:
            rejected.append((_op(seq[t]), nxop))
    res.line("LEAF_COUNT=%d (value region [%d,%d))" % (len(leaves), lo, hi))
    res.line("LEAF_TERMINALS=%s" % ",".join(op for (_, op, _) in leaves))
    res.line("REJECTED_INNER_TERMINALS=%s" %
             (",".join("%s->%s" % (a, b) for (a, b) in rejected) or "-"))

    leaf_ok = len(leaves) == len(keys)
    res.leaf_ok_by_class[cls] = leaf_ok
    res.line("LEAF_COUNT_MATCH=%s" % ("YES" if leaf_ok else "NO stage=LEAF_BOUNDARY"))

    # default candidate + reject reason per wanted (correlated) key only
    for k in want:
        if k not in idx_of:
            res.line("key=%s | key_index=NONE-IN-TUPLE | leaf_index=- "
                     "| leaf_terminal=? | candidate=UNKNOWN | type=? "
                     "| reject:key-absent-from-tuple" % k)
            continue
        ki = idx_of[k]
        if ki < len(leaves):
            lidx, top, nxop = leaves[ki]
            try:
                dl = lod._leaf_default_ins(seq, lidx)
            except Exception as exc:  # noqa: BLE001
                res.line("key=%s | key_index=%d | leaf_index=%d | leaf_terminal=%s "
                         "| candidate=UNKNOWN | type=? | reject:resolver-raise %r"
                         % (k, ki, lidx, top, exc))
                if leaf_ok:
                    res.default_unknown_on.append((cls, k))
                continue
            if dl is None:
                res.line("key=%s | key_index=%d | leaf_index=%d | leaf_terminal=%s "
                         "| next_after=%s | candidate=UNKNOWN | type=? "
                         "| reject:non-const-or-no-default-packet"
                         % (k, ki, lidx, top, nxop))
                if leaf_ok:
                    res.default_unknown_on.append((cls, k))
                continue
            lit, loff = dl
            res.line("key=%s | key_index=%d | leaf_index=%d | leaf_terminal=%s "
                     "| next_after=%s | candidate=%r | type=%s "
                     "| RESOLVED (default LOAD_CONST@%d)"
                     % (k, ki, lidx, top, nxop, lit, type(lit).__name__, loff))
        else:
            res.line("key=%s | key_index=%d | leaf_index=MISSING | leaf_terminal=? "
                     "| candidate=UNKNOWN | type=? | reject:index-out-of-leaf-range"
                     % (k, ki))


def _classify(res):
    """Single canonical REAL_DECODER_FAILURE_STAGE by the operator-mandated
    decision tree (first failing stage wins)."""
    for c in _CLASSES:
        if not res.found.get(c):
            return "CLASS_DISCOVERY"
    # any class where DISASSEMBLY/map discovery failed closed
    joined = "\n".join(res.lines)
    if "DISASSEMBLY=FAIL" in joined or "STAGE=MAP_DISCOVERY" in joined:
        return "MAP_DISCOVERY"
    if "STAGE=KEY_TUPLE_EXTRACTION" in joined:
        return "KEY_TUPLE_EXTRACTION"
    # leaf boundary: any class where the leaf count did not match the tuple len
    if "NO stage=LEAF_BOUNDARY" in joined:
        return "LEAF_BOUNDARY"
    # default extraction: leaf count matched everywhere but some key UNKNOWN
    for (c, _k) in res.default_unknown_on:
        return "DEFAULT_EXTRACTION"
    return "NONE"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tuning-pyc", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if not os.path.isfile(args.tuning_pyc):
        sys.stderr.write("ERROR: tuning pyc not found: %s\n" % args.tuning_pyc)
        return 1

    # load really like populate_defaults
    code = None
    version = None
    try:
        from xdis.load import load_module_from_file_object
        with open(args.tuning_pyc, "rb") as fh:
            data = fh.read()
        res_t = load_module_from_file_object(io.BytesIO(data), filename=args.tuning_pyc)
        code = res_t[3]
        try:
            version = res_t[0]
        except Exception:  # noqa: BLE001
            version = None
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("PYC_LOAD_FAIL %r\n" % exc)
        return 1
    if code is None:
        sys.stderr.write("PYC_LOAD_NONE\n")
        return 1

    cdm = discover_classes(code)
    res = DiagResult()
    for cls in _CLASSES:
        matched = None
        for p in cdm:
            if p == cls or p.endswith("." + cls):
                matched = p
                break
        if matched is None:
            res.line("CLASS=%s" % cls)
            res.line("FOUND=NO")
            # log a few sibling paths so a traversal/naming mismatch is visible
            siblings = sorted(k for k in cdm if k.endswith(cls.split("_")[-1]) or
                              "WickedWhimsAnimation" in k)[:8]
            if siblings:
                res.line("SIMILAR_PATHS=%s" % "|".join(siblings))
            res.found[cls] = False
            continue
        analyze_class(cls, matched, cdm[matched], res, version=version)

    stage = _classify(res)
    res.line("")
    res.line("REAL_DECODER_FAILURE_STAGE=%s" % stage)

    outdir = os.path.dirname(os.path.abspath(args.out))
    try:
        os.makedirs(outdir, exist_ok=True)
    except Exception:
        pass
    try:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(res.lines) + "\n")
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("WRITE_FAIL %r\n" % exc)
        return 1

    print("WROTE %s" % args.out)
    print("REAL_DECODER_FAILURE_STAGE=%s" % stage)
    return 0


if __name__ == "__main__":
    sys.exit(main())
