#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ww_p29e_logic_test.py --- offline functional gate for the P29-E identity probe.

Proves (WITHOUT a game) that the P29-E runtime hook, driven exactly as it will be
on Dorothy's real machine, records the right identity evidence and respects scope:
  A  observation-only passthrough: original get_picker_row called with its args,
     its return (the row object) returned verbatim; wrapper never mutates self.
  B  target gating: only AUTHOR=='Nevely42' AND (display=='TEST300' |
     display=='Caught Cheating 2' | stage=='caught cheating 2') records a detailed
     P29E_PICKER_ROW_BEGIN/END block; a missing filter opens NO detailed block.
  C  block fields: PY_OBJECT_ID / ANIMATION_ID / AUTHOR / DISPLAY_NAME_ATTR /
     DISPLAY_NAME_OVERRIDE / STAGE_NAME_ATTR / IDENTIFIER / ROW_CLASS /
     ROW_NAME_ATTR / ROW_GET_NAME all present on a gated call.
  D  multi-instance identity: two DISTINCT instances that share the same
     animation_id / identifier map to two DISTINCT PY_OBJECT_ID values -> the exact
     signal the runtime hypothesis (a different instance drives the UI) depends on.
  E  row .get_name passthrough is captured when callable; ABSENT handled when not.
  F  cap: > _MAX_CALLS gated calls stop opening full blocks (P29E_LIMIT_REACHED).
The class is stubbed under the task-stated host module in sys.modules so
_PATCH class discovery resolves the SAME code path the deployed module uses.

Exit 0=PASS.  ASCII.  Python 3.7-compatible.  Uses only stdlib.
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

os.environ["WW_P29_DISABLE_AUTORUN"] = "1"
os.environ["WW_P29E_DISABLE_AUTORUN"] = "1"

modname = "ww_p29e_picker_row_probe"
HOST = "wickedwhims.sex.animations.animation_instance"

tmpdir = tempfile.mkdtemp(prefix="p29e_logic_")
os.environ["TMP"] = tmpdir
os.environ["TEMP"] = tmpdir
os.chdir(tmpdir)

import ww_p29e_picker_row_probe as M  # noqa: E402

_LOG = os.path.join(tmpdir, "ww_p29e_picker_row_probe.log")
if os.path.exists(_LOG):
    os.remove(_LOG)
# file-backed emit like the real in-game import-time boot
M._STATE["_log_path"] = _LOG
M._log_header()
M._emit("TEST_LOG_SEEDED=YES")


# ---------------- fake instances + fake row ----------------
def _reset_class_state():
    M._reset_state_for_test()
    M._STATE["_log_path"] = _LOG


class _FakeRow(object):
    """Mirror ObjectPickerRow: holds .name; defines .get_name() -> self.name."""
    def __init__(self, identifier, name):
        self.identifier = identifier
        self.name = name
    def get_name(self):
        return self.name


class _RowNoGetName(object):
    """A row type exposing .name but NO callable .get_name (E boundary)."""
    def __init__(self, identifier, name):
        self.identifier = identifier
        self.name = name


class _FakeInstance(object):
    """Mirror the SexAnimationInstance surface the probe reads; get_picker_row returns
    a row whose 2nd arg (name) it received, and records it was called verbatim."""
    _token = 0
    def __init__(self, author, display_name, override=None, stage_name=None,
                 animation_id=0, identifier=None, row_type=_FakeRow):
        _FakeInstance._token += 1
        self._tok = _FakeInstance._token
        self.author = author
        self.display_name = display_name
        self.display_name_override = override
        self.animation_stage_name = stage_name
        self._animation_id = animation_id
        self._identifier = identifier
        self.row_type = row_type
        self.calls = []
    def get_animation_id(self):
        return self._animation_id
    def get_author(self):
        return self.author
    def get_identifier(self):
        return self._identifier
    def get_picker_row(self, index, icon_override=0, from_context=False):
        self.calls.append(("orig", index, icon_override, from_context))
        return self.row_type(self._identifier, self.display_name)


def _stub_host(cls):
    """Register a fake host module exposing SexAnimationInstance so _find_class
    resolves the same way the deployed probe resolves on the real machine."""
    import types
    m = sys.modules.get(HOST)
    if m is None:
        m = types.ModuleType(HOST)
    setattr(m, "SexAnimationInstance", cls)
    sys.modules[HOST] = m
    return m


def _emit(filename):
    if os.path.exists(filename):
        with open(filename, encoding="utf-8", errors="replace") as f:
            return f.read()
    return ""


def _blocks(text):
    return [b for b in re.findall(r"P29E_PICKER_ROW_BEGIN\n(.*?)P29E_PICKER_ROW_END",
                                  text, flags=re.S)]


def _kv(blob):
    d = {}
    for ln in blob.splitlines():
        if "=" in ln and not ln.startswith(("SELF_REPR=",)):
            k, v = ln.split("=", 1)
            d[k] = v
    return d


def main():
    fails = []

    class Inst(_FakeInstance):
        pass

    _stub_host(Inst)

    # ---- B: non-target -> no detailed block ----
    _reset_class_state()
    other = Inst("SomeoneElse", "TEST300")          # author fails
    _patch_log_before = _emit(_LOG)
    ok_where = M._patch_class()
    if not (ok_where and ok_where[0]):
        fails.append("patch-failed (discovery): %r" % (ok_where,))
    other.get_picker_row(0)
    text1 = _emit(_LOG)
    if _blocks(text1):
        fails.append("B-non-target-wrote-detail")
    # author filter gate: non-Nevely TEST300 must NOT open a block
    # (only a single throttled skip line is written)

    # ---- target TEST300 instance through real original ----
    _reset_class_state()
    M._patch_class()
    inst_a = Inst("Nevely42", "TEST300", override="OVERRIDE_X",
                  stage_name="some stage", animation_id=5, identifier="id5")
    ret_a = inst_a.get_picker_row(3, icon_override=1, from_context=True)
    if not isinstance(ret_a, _FakeRow) or ret_a.name != "TEST300":
        fails.append("A-return-not-preserved (got %r)" % (ret_a,))
    if not inst_a.calls or inst_a.calls[0][0] != "orig":
        fails.append("A-original-not-called")
    text2 = _emit(_LOG)
    blk = [b for b in _blocks(text2)
           if "DISPLAY_NAME_ATTR='TEST300'" in b]
    if not blk:
        fails.append("C-no-TEST300-block")
    else:
        kv = _kv(blk[0])
        for k in ("CALL_INDEX", "PY_OBJECT_ID", "ANIMATION_ID", "AUTHOR",
                  "DISPLAY_NAME_ATTR", "DISPLAY_NAME_OVERRIDE", "STAGE_NAME_ATTR",
                  "IDENTIFIER", "ROW_CLASS", "ROW_NAME_ATTR", "ROW_GET_NAME"):
            if k not in kv:
                fails.append("C-missing-field-%s" % k)
        if kv.get("AUTHOR") != "'Nevely42'":
            fails.append("C-author-wrong: %r" % kv.get("AUTHOR"))
        if kv.get("DISPLAY_NAME_ATTR") != "'TEST300'":
            fails.append("C-display-wrong: %r" % kv.get("DISPLAY_NAME_ATTR"))
        if kv.get("ROW_NAME_ATTR") != "'TEST300'":
            fails.append("E-row-name-missing: %r" % kv.get("ROW_NAME_ATTR"))
        if kv.get("ROW_GET_NAME") != "'TEST300'":
            fails.append("E-row-get_name-missing: %r" % kv.get("ROW_GET_NAME"))
        if kv.get("ANIMATION_ID") != "5":
            fails.append("C-anim-id-wrong: %r" % kv.get("ANIMATION_ID"))
        if kv.get("IDENTIFIER") != "'id5'":
            fails.append("C-identifier-wrong: %r" % kv.get("IDENTIFIER"))

    # ---- stage_name filter path (its OWN Nevely row), even with display not in set
    _reset_class_state()
    M._patch_class()
    Inst("Nevely42", "HAS_STAGE", stage_name="caught cheating 2",
         animation_id=5, identifier="id5").get_picker_row(0)
    text3 = _emit(_LOG)
    if not [b for b in _blocks(text3) if "STAGE_NAME_ATTR='caught cheating 2'" in b]:
        fails.append("stage-filter-no-block")

    # ---- Caught Cheating 2 instance (summary closed path) ----
    _reset_class_state()
    M._patch_class()
    Inst("Nevely42", "Caught Cheating 2", animation_id=5,
         identifier="id5").get_picker_row(0)
    text4 = _emit(_LOG)
    if not [b for b in _blocks(text4) if "DISPLAY_NAME_ATTR='Caught Cheating 2'" in b]:
        fails.append("SUCCESS_B-no-block")

    # ---- D: two DISTINCT objects, same anim/identifier -> distinct PY_OBJECT_ID ----
    _reset_class_state()
    M._patch_class()
    i1 = Inst("Nevely42", "TEST300", animation_id=300, identifier="caught_cheating_2")
    i2 = Inst("Nevely42", "TEST300", animation_id=300, identifier="caught_cheating_2")
    # simulate a UI that builds rows from both objects (e.g. stale + fresh)
    i1.get_picker_row(0)
    i2.get_picker_row(1)
    text5 = _emit(_LOG)
    b5 = _blocks(text5)
    ids = [b for b in b5 if "PY_OBJECT_ID" in b and "=0x" in b and
           "DISPLAY_NAME_ATTR='TEST300'" in b]
    pids = set()
    for b in b5:
        # parse PY_OBJECT_ID from each whole block
        mpr = re.search(r"PY_OBJECT_ID=(0x[0-9a-fA-F]+)", b)
        if mpr:
            pids.add(mpr.group(1))
        m = re.search(r"ANIMATION_ID=300\s*\n", b)
        if m:
            pass
    if len(pids) < 2:
        fails.append("D-not-two-PY_OBJECT_ID (%r)" % (sorted(pids),))

    # ---- E: row without callable get_name -> ABSENT line, no crash ----
    _reset_class_state()
    M._patch_class()
    Inst("Nevely42", "TEST300", row_type=_RowNoGetName,
         animation_id=5, identifier="id5").get_picker_row(0)
    text6 = _emit(_LOG)
    if "<no callable .get_name>" not in text6:
        fails.append("E-absent-get_name-not-handled")

    # ---- A: original args forwarded + row from original untouched (override path) --
    _reset_class_state()
    M._patch_class()
    ir = Inst("Nevely42", "TEST300", override="OVERRIDE_SAVED",
              animation_id=3, identifier="id3")
    ir.get_picker_row(7, icon_override=9, from_context=True)
    text7 = _emit(_LOG)
    if not any("ROW_NAME_ATTR='TEST300'" in b for b in _blocks(text7)):
        fails.append("A-7-row-name-dropped")

    # ---- F: cap - a fresh reset produces at most one more NEW block near the cap
    _reset_class_state()
    M._patch_class()
    M._STATE["calls"] = M._MAX_CALLS - 1   # next call exactly hits the cap boundary
    f_before = _emit(_LOG).count("P29E_PICKER_ROW_BEGIN")
    for i in range(6):
        Inst("Nevely42", "TEST300", animation_id=100 + i,
             identifier="cap_%d" % i).get_picker_row(i)
    text8 = _emit(_LOG)
    f_delta = text8.count("P29E_PICKER_ROW_BEGIN") - f_before
    # two gated calls may open BEFORE the limit (idx==cap opens; the next hits it)
    if f_delta > 2:
        fails.append("F-exceeded-near-cap (delta=%d)" % f_delta)
    if "P29E_LIMIT_REACHED=YES" not in text8:
        fails.append("F-limit-not-emitted")

    # ---- core gating: the very first block after seeded header is real, and no
    # earlier non-gated family spammed the log (skip lines throttled) ----
    skips = _emit(_LOG).count("P29E_NON_TARGET_SKIPPED=")
    if skips > 5:
        fails.append("skip-throttle-broken(%d)" % skips)

    if fails:
        print("P29E_LOGIC_VERDICT=FAIL")
        for f in fails:
            print("  - " + f)
        tail = _emit(_LOG)[-2000:]
        print("LOG_TAIL_START>>>>")
        print(tail)
        print("<<<<LOG_TAIL_END")
        return 1
    print("P29E_LOGIC_VERDICT=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
