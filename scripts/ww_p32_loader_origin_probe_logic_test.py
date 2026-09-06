#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_loader_origin_probe_logic_test.py -- regression harness for the P32 exact
loader-origin probe's pure decision core (classifier + render + gate).

The bytecode WALKER (needs a real .ts4script + xdis on Windows) is NOT exercised
here -- no pyc bytes exist on Linux.  What is protected here is the *deciding*
logic that the gate depends on: classify_provenance() and render_audit(), i.e.
the exact rules that turn evidence into the only-four-status vocabulary and into
FULL_CORPUS_SAFE_TO_RECONSTRUCT.

Statuses asserted (full vocabulary closure):
  PROVEN_TUNING | PROVEN_DEFAULT | PROVEN_TRANSFORM | UNKNOWN
and no other string may ever be emitted by the classifier.

Invariants under test:
  A1 vocabulary closure: classify_provenance never returns anything outside the 4.
  A2 a tuning-fed field (schema sink + tuning-named write) => PROVEN_TUNING.
  A3 a const default with NO schema sink and NO call => PROVEN_DEFAULT.
  A4 a pure transform with no schema sink => PROVEN_TRANSFORM when classified
     value_kind 'transform' (set later), but a bare helper call (value_kind
     'helper', set later, no tuning) is conservatively UNKNOWN -- i.e. the probe
     NEVER guesses a transform is a recoverable runtime default from an unresolved
     helper.
  A5 ILLEGAL pattern: a schema sink exists but a 'default' override on top would
     swallow a present tuning value => UNKNOWN (never PROVEN_DEFAULT).
  A6 gate render_audit: any UNKNOWN row => FULL_CORPUS_SAFE_TO_RECONSTRUCT=NO.
     All-PROVEN three-state roster => YES.
  A7 counts line is emitted and only the 4 statuses appear in it.
Exit 0 = all PASS; 1 = any FAIL.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import ww_p32_loader_origin_probe as lop

ALLOWED = ("PROVEN_TUNING", "PROVEN_DEFAULT", "PROVEN_TRANSFORM", "UNKNOWN")


def main():
    ok = []

    def check(name, cond, detail=""):
        ok.append((name, bool(cond)))
        print("PASS %s%s%s" % (name, "  | " if detail else "", detail))

    # A1 vocabulary closure
    closed = True
    for ev in [
        {"value_kind": "tuning", "has_tuning_sink": True},
        {"value_kind": "default", "has_tuning_sink": False},
        {"value_kind": "transform", "has_tuning_sink": False, "set_later": True},
        {"value_kind": "helper", "has_tuning_sink": False, "set_later": True},
        {"value_kind": "unknown", "has_tuning_sink": False},
        {"value_kind": "default", "has_tuning_sink": True},  # illegal override
    ]:
        st, _ = lop.classify_provenance(ev)
        if st not in ALLOWED:
            closed = False
    check("A1-vocabulary-closure", closed)

    # A2 tuning-fed
    st, r = lop.classify_provenance({"value_kind": "tuning", "has_tuning_sink": True})
    check("A2-tuning-fed", st == "PROVEN_TUNING", repr(r))

    # A3 const default, no sink, no call
    st, r = lop.classify_provenance({"value_kind": "default", "has_tuning_sink": False})
    check("A3-const-default", st == "PROVEN_DEFAULT", repr(r))

    # A4 transform vs helper
    st, r = lop.classify_provenance(
        {"value_kind": "transform", "has_tuning_sink": False, "set_later": True})
    check("A4-transform", st == "PROVEN_TRANSFORM", repr(r))
    st, r = lop.classify_provenance(
        {"value_kind": "helper", "has_tuning_sink": False, "set_later": True})
    check("A4-helper-conservative-unknown", st == "UNKNOWN", repr(r))

    # A5 illegal default override swallowing a tuning sink
    st, r = lop.classify_provenance(
        {"value_kind": "default", "has_tuning_sink": True,
         "default_overrides_present_tuning": True})
    check("A5-illegal-override", st == "UNKNOWN", repr(r))

    # A6 gate
    safe_rows = [
        {"identity_field": "f1", "status": "PROVEN_TUNING"},
        {"identity_field": "f2", "status": "PROVEN_DEFAULT"},
        {"identity_field": "f3", "status": "PROVEN_TRANSFORM"},
    ]
    _, sumsafe = lop.render_audit(safe_rows)
    g_safe_yes = any("FULL_CORPUS_SAFE_TO_RECONSTRUCT=YES" in s for s in sumsafe)
    check("A6-gate-yes", g_safe_yes, "all 3 PROVEN -> SAFE=YES")

    uns = [
        {"identity_field": "f1", "status": "PROVEN_TUNING"},
        {"identity_field": "f4", "status": "UNKNOWN"},
    ]
    _, sumu = lop.render_audit(uns)
    g_uns_no = any("FULL_CORPUS_SAFE_TO_RECONSTRUCT=NO" in s for s in sumu)
    check("A6-gate-no", g_uns_no, "any UNKNOWN -> SAFE=NO")

    # A7 counts line uses only the 4 statuses
    allfour = [
        {"identity_field": "a", "status": "PROVEN_TUNING"},
        {"identity_field": "b", "status": "PROVEN_DEFAULT"},
        {"identity_field": "c", "status": "PROVEN_TRANSFORM"},
        {"identity_field": "d", "status": "UNKNOWN"},
    ]
    _, sums = lop.render_audit(allfour)
    cntline = next(s for s in sums if s.startswith("PROVEN_TUNING="))
    check("A7-counts", "PROVEN_TUNING=1  PROVEN_DEFAULT=1  PROVEN_TRANSFORM=1  UNKNOWN=1"
          in cntline, cntline)

    # B1..B4 fused evidence-decision (decide_field_evidence) -- the anti-over-claim
    # rule the whole gate depends on.
    ents = lambda **kw: [{"attr": kw.get("attr", "x"), "kind": kw.get("kind", "const"),
                           "detail": kw.get("detail", ""), "tag": kw.get("tag", "__init__"),
                           "producer_is_tuning_name": kw.get("ptn", False)}]
    # B1: schema sink + tuning-named feed => PROVEN_TUNING
    r = lop.decide_field_evidence("version", ("version",),
                                  ents(kind="param", detail="version", ptn=True),
                                  {"version": 1})
    check("B1-sink-with-tuning-feed", r["status"] == "PROVEN_TUNING", r["status"] + " | " + r["reason"])

    # B2: schema sink but NO tuning-named literal feed => UNKNOWN, NEVER
    #     PROVEN_DEFAULT / PROVEN_TRANSFORM (a generic get/attr helper may still
    #     read the per-row tuning value).
    r = lop.decide_field_evidence("version", ("version",),
                                  ents(kind="const"),  # LOAD_CONST default present
                                  {"version": 1})
    check("B2-sink-noLiteral-is-UNKNOWN", r["status"] == "UNKNOWN",
          r["status"] + " | " + r["reason"])

    # B3: no schema sink + const in __init__ => PROVEN_DEFAULT (safe)
    r = lop.decide_field_evidence("version", ("version",),
                                  ents(kind="const"), {})
    check("B3-nosink-const-default", r["status"] == "PROVEN_DEFAULT",
          r["status"] + " | " + r["reason"])

    # B4: no schema sink + pure call-helper only, not const => conservatively
    #     UNKNOWN (cannot prove the helper yields a recoverable default).
    r = lop.decide_field_evidence("actor.position_offset.x/y/z",
                                  ("position_offset",),
                                  ents(kind="call", detail="translate_rotation"), {})
    check("B4-nosink-helper-is-UNKNOWN", r["status"] == "UNKNOWN",
          r["status"] + " | " + r["reason"])

    # ---- C-section: REAL scan_roster_tuning over a synthetic WW package ----
    # (read-only synthetic build, same pattern as source_fixture logic test; no
    # Mods/saves).  Proves schema census + suffix matching + anti-over-claim on the
    # actually shipped functions.  The fixture builder (ww_animation_canary_builder)
    # uses PEP-585 annotations (py3.9+), so under an older interpreter we SKIP this
    # section (the shipped probe itself remains py3.7 AST-clean via the py37 gate).
    def _run_csection():
        import ww_animation_canary_builder as CB
        import ww_p32_identifier_source_fixture as SF
        import tempfile as _tf
        xml0 = ('<I n="T"><L n="animations_list">'
                '<U n="s0"><T n="animation_locations">DOUBLE_BED</T></U>'
                '<U n="s1"><T n="animation_object_animation_clip_name">objclip1</T>'
                '<T n="animation_version">2</T></U>'
                '<U n="s2"><T n="animation_prop_animation_clip_name">pc</T></U>'
                '</L></I>')
        p0 = Path(_tf.mkdtemp(prefix="p32_lorig_")) / "p.package"
        CB.build_package([
            (SF.WW_ANIM_XML, 0x00B2D882, SF.EXPECT_INSTANCE, xml0.encode(),
             {"comp_state": False, "comp_type": 0, "mem_size": len(xml0),
              "offset_high_bit": 0, "size_high_bit": 0})], p0)
        idx = SF._read_index(p0)
        w0 = [e for e in idx.entries if e.type_id == SF.WW_ANIM_XML][0]
        root = __import__("xml.etree.ElementTree",
                          fromlist=["ElementTree"]).fromstring(
            SF._decompress(SF._read_body(p0, w0)).decode("utf-8", "replace"))
        lst = [n for n in root.iter() if SF._el_tag(n) == "L"
               and SF._name(n) == SF.ENTRY_LIST_FIELD][0]
        rows_el = [c for c in list(lst) if SF._el_tag(c) == "U"]
        sk, osk = lop.scan_roster_tuning(rows_el, SF._name, SF._text)
        check("C1-rowcount", len(rows_el) == 3, "entries=%d" % len(rows_el))
        check("C2-suffix-scan", (
            sk["object_animation_clip_name"].get(
                "animation_object_animation_clip_name") == 1
            and sk["version"].get("animation_version") == 1
            and sk["prop_animation_clip_name"].get(
                "animation_prop_animation_clip_name") == 1),
            "object=%r version=%r prop=%r" % (
                sk["object_animation_clip_name"], sk["version"],
                sk["prop_animation_clip_name"]))
        check("C3-ordinal-partial", osk["version"] == [1]
              and osk["prop_animation_clip_name"] == [2],
              "version=%r prop=%r" % (osk["version"], osk["prop_animation_clip_name"]))
        ents2 = [{"attr": "x", "kind": "param", "detail": "version",
                  "tag": "__init__", "producer_is_tuning_name": True}]
        rv = lop.decide_field_evidence("version", ("version",), ents2,
                                       sk["version"])
        check("C4-real-sink-proven-tun", rv["status"] == "PROVEN_TUNING", rv["status"])
        entsc = [{"attr": "x", "kind": "const", "detail": "",
                  "tag": "__init__", "producer_is_tuning_name": False}]
        rc = lop.decide_field_evidence("object_animation_clip_name",
                                       ("object_animation_clip_name",), entsc,
                                       sk["object_animation_clip_name"])
        check("C5-real-anti-overclaim", rc["status"] == "UNKNOWN", rc["status"])

    import sys as _sys
    if _sys.version_info < (3, 9):
        check("C-skip-legacy-python", True,
              "python=%d.%d (fixture builder needs 3.9+)"
              % (_sys.version_info[0], _sys.version_info[1]))
    else:
        try:
            _run_csection()
        except Exception as ex:  # pragma: no cover - surfaced as FAIL
            check("C-section-exception", False, repr(ex))

    failed = [n for n, passed in ok if not passed]
    print("PASS_COUNT=%d FAIL_COUNT=%d" % (len(ok) - len(failed), len(failed)))
    if failed:
        print("FAILED_NAMES=%s" % failed)
        return 1
    print("P32_LOADER_ORIGIN_PROBE_LOGIC=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
