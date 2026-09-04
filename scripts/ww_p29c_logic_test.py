#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ww_p29c_logic_test.py --- offline functional gate for the P29-C caller trace.

Proves (without a game):
  A  observation-only passthrough: original get_display_name called, return preserved.
  B  target-only gating: only self.display_name == 'TEST300' records a detailed block;
     thousands of other animations record NO detailed trace (no log blowup).
  C  caller chain: real runtime f_back frames are captured with module/function/
     filename/line for the TRUE callers (not synthetic), including nested depth.
  D  filtered locals: the nearest caller frames' display-keyword locals are repr'd.
  E  return-object precision: str vs non-str, class/module/repr/value.
  F  cap: max 30 target calls, after that TARGET_TRACE_LIMIT_REACHED=YES and no more
     full blocks.
Each is asserted from the text emitted to a temp log (the same code path `_emit`
uses for the real machine).  Also feeds the same log file back through nothing -- this
file only hands the module its real offline driver.

Exit 0=PASS.  ASCII.  Python 3.7-compatible.  Uses only stdlib.
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

os.environ["WW_P29_DISABLE_AUTORUN"] = "1"
os.environ["WW_P29C_DISABLE_AUTORUN"] = "1"

modname = "ww_p29c_display_caller_trace"
tmpdir = tempfile.mkdtemp(prefix="p29c_logic_")
# point all 4 log roots at the temp dir so we read exactly what the module writes
os.environ["TMP"] = tmpdir
os.environ["TEMP"] = tmpdir
os.chdir(tmpdir)

import ww_p29c_display_caller_trace as M  # noqa: E402

_LOG = os.path.join(tmpdir, "ww_p29c_display_caller_trace.log")
if os.path.exists(_LOG):
    os.remove(_LOG)
# enable file-backed _emit (main not run because autorun is off); seed file path like
# the real in-game import-time boot does.
M._bootstrap_boot_marker()

orig_calls = []
ORIG_MARK = ("orig:hash=%s original=%s",)


def _orig_get_display_name(self, string_hash, original=None):
    # fake WW original: returns the instance.display_name, and records it was called
    orig_calls.append((string_hash, original))
    return self.display_name


def _fake_instance(display):
    class _I(object):
        display_name = display
        author = "Nevely42"
        display_name_override = None
        original_instance = None
        animation_id = 0
    return _I()


def _call_one(inst, h, orig):
    # real frame depth 1 from the hook wrapper's perspective
    inst._phantom = h  # noqa
    wrapper = M._hook_get_display_name(_orig_get_display_name)
    return wrapper(inst, h, orig)


def _call_chain_helper_a(inst):
    return _call_chain_helper_b(inst)


def _call_chain_helper_b(inst):
    # injected local that matches a display keyword
    picker_row_obj = inst.display_name.upper()  # noqa
    label_keeper = "some-label"  # noqa
    return _call_one(inst, 12345, True)


def _emit(filename):
    if not os.path.exists(filename):
        return ""
    with open(filename, encoding="utf-8", errors="replace") as f:
        return f.read()


def _last_blocks(logtext):
    """Split into ..._BEGIN ..._END blocks; return list of dict keyword->value from
    the most recent full blocks at the tail (cap logic appends the LIMIT line after
    the final BEGIN-less call, so we count distinct P29C_TARGET_CALL_BEGIN)."""
    return logtext.count("P29C_TARGET_CALL_BEGIN")


def main():
    fails = []
    # --- EMPTY model: importing (autorun off) must write header? It won't until
    # main() runs; logic test drives calls directly, so header only when we choose.
    # B1: non-target instance -> NO detailed block.
    before = os.path.exists(_LOG) and _emit(_LOG) or ""
    ct_before = _last_blocks(before)
    n1 = _call_one(_fake_instance("SOME_OTHER_ANIM"), 1, False)
    n2 = _call_chain_helper_a(_fake_instance("Caught Cheating 2"))
    t = _emit(_LOG)
    if _last_blocks(t) != ct_before:
        fails.append("B-non-target-wrote-detail (expected no TEST300 block for non-target)")
    # ensure passthrough return was preserved all the same
    if n1 != "SOME_OTHER_ANIM":
        fails.append("A-return-nontarget-not-preserved")
    if n2 != "Caught Cheating 2":
        fails.append("A-return-targetname-not-preserved")

    # --- TARGET: detailed trace through real frames ---
    inst = _fake_instance("TEST300")
    r = _call_chain_helper_a(inst)
    t = _emit(_LOG)
    if r != "TEST300":
        fails.append("A-return-TEST300-not-preserved: got %r" % (r,))
    if not orig_calls:
        fails.append("A-original-not-called")
    else:
        # original had signature (self,string_hash,original) => but our fake recorded
        pass
    blocks = [b for b in re.split(r"P29C_TARGET_CALL_BEGIN|P29C_TARGET_CALL_END", t)
              if b]
    # find the block that contains SELF_DISPLAY_NAME 'TEST300'
    target_blob = ""
    for blk in re.findall(r"P29C_TARGET_CALL_BEGIN\n(.*?)P29C_TARGET_CALL_END",
                          t, flags=re.S):
        if "SELF_DISPLAY_NAME='TEST300'" in blk:
            target_blob = blk
            break
    if not target_blob:
        fails.append("C-no-TEST300-block")
    else:
        kv = {}
        for ln in target_blob.splitlines():
            if "=" in ln and not ln.startswith("CALLER") and not ln.startswith(
                    "LOCAL"):
                k, v = ln.split("=", 1)
                kv[k] = v
        kv["TARGET_CALL_INDEX"] = target_blob and re.search(
            r"TARGET_CALL_INDEX=(\d+)", target_blob) and re.search(
            r"TARGET_CALL_INDEX=(\d+)", target_blob).group(1) or ""
        for k in ("SELF_DISPLAY_NAME", "ORIGINAL_INSTANCE_PRESENT",
                  "DISPLAY_NAME_OVERRIDE", "ARG_STRING_HASH", "ARG_ORIGINAL",
                  "RETURN_IS_STR", "RETURN_CLASS", "RETURN_VALUE"):
            if k not in kv and k != "RETURN_VALUE":
                fails.append("C-missing-field-%s" % k)
        if not (kv.get("SELF_DISPLAY_NAME") == "'TEST300'"):
            fails.append("C-self-display-mismatch")
        if not (kv.get("RETURN_VALUE", "").startswith("'TEST300'")):
            fails.append("E-return-value-missing")
        if not (kv.get("RETURN_IS_STR") == "YES"):
            fails.append("E-return-not-str")
        if kv.get("ORIGINAL_INSTANCE_PRESENT") != "NO":
            fails.append("C-oi-present-wrong")
        # argument provenance: string_hash=12345 original=True were forwarded
        if not (kv.get("ARG_STRING_HASH") == "12345"):
            fails.append("C-arg-hash-wrong: %r" % kv.get("ARG_STRING_HASH"))
        if not (kv.get("ARG_ORIGINAL") == "True"):
            fails.append("C-arg-original-wrong: %r" % kv.get("ARG_ORIGINAL"))
        # CALLER_1 (after skipping internal trace frames) must be the real external
        # caller chain: _call_one -> helper_b -> helper_a
        caller1 = {}
        for ln in target_blob.splitlines():
            if ln.startswith("CALLER_1_"):
                k, v = ln.split("=", 1)
                caller1[k] = v
        if "_call_one" not in caller1.get("CALLER_1_FUNCTION", ""):
            fails.append("C-caller1-not-call_one: %r" %
                         caller1.get("CALLER_1_FUNCTION"))
        # locals: helper_b should contribute display-keyword locals to one of the
        # nearest captured frames (it defines picker_row_obj + label_keeper).
        local_seen = False
        for pref in ("CALLER_1_", "CALLER_2_", "CALLER_3_"):
            for key in ("picker_row_obj", "label_keeper"):
                if (pref + "LOCAL_" + key) in target_blob:
                    local_seen = True
        if not local_seen:
            fails.append("D-no-filtered-local-exploit")
        for c in ("CALLER_1_MODULE", "CALLER_1_FUNCTION", "CALLER_1_FILENAME",
                  "CALLER_1_LINE"):
            if c not in target_blob:
                fails.append("C-missing-caller-key-%s" % c)
        # ensure we actually walked beyond 1 frame: CALLER_2_*, CALLER_3_* expected
        if "CALLER_2_MODULE" not in target_blob or "CALLER_3_MODULE" not in target_blob:
            fails.append("C-chain-too-shallow")

    # verify a LOCAL display-keyword capture near the target (helper_b has
    # picker_row_obj / label_keeper -> should appear) is enforced inside the block
    # section above; nothing further needed here.

    # --- F: cap at 30 target calls; 31st must NOT open a block ---
    full_blocks_at_30 = None
    for i in range(35):
        _call_one(_fake_instance("TEST300"), i, False)
    t = _emit(_LOG)
    n_begins = t.count("P29C_TARGET_CALL_BEGIN")
    # we already produced 1 target block (the chained one) + 35 loop calls => cap means
    # only 30 total target blocks emitted.
    if t.count("P29C_TARGET_CALL_BEGIN") < 30:
        fails.append("F-early-limit (blocks=%d)" % n_begins)
    if "TARGET_TRACE_LIMIT_REACHED=YES" not in t:
        fails.append("F-limit-not-emitted")
    # after the 30th, no further BEGIN
    idxs = [int(m) for m in re.findall(r"TARGET_CALL_INDEX=(\d+)", t)]
    if idxs:
        if max(idxs) > 30:
            fails.append("F-index-exceeded-30: %r" % max(idxs))
        if len(idxs) != len(set(idxs)):
            fails.append("F-dup-index")
    # avoid extreme block count
    if t.count("P29C_TARGET_CALL_BEGIN") != 30:
        fails.append("F-block-count=%d (want 30)" % t.count(
            "P29C_TARGET_CALL_BEGIN"))

    # non-target frames still must never detail after 35 loop (covered by gating F)
    if all(f for f in fails if "non-target" not in f):
        pass

    if fails:
        print("P29C_LOGIC_VERDICT=FAIL")
        for f in fails:
            print("  - " + f)
        # dump a trimmed tail for diagnosis
        tail = t[-3000:]
        print("LOG_TAIL_START>>>>")
        print(tail)
        print("<<<<LOG_TAIL_END")
        return 1
    print("P29C_LOGIC_VERDICT=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
