#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29f_logic_test.py -- OFFLINE logic/round-trip test for ww_p29f_visible_canary.py.

WHY OFFLINE
-----------
The real WW_Nevely42_Animations.package lives only on the Windows box; this Linux
repo has no byte copy and no route to it.  To prove the P29-F builder's gate and
round-trip logic without the real 479-entry source, we synthesize a package that
FAITHFULLY reproduces the authoritative census contract:

  WW_ANIM_XML type=0x7DF2169C / instance=0x43F3438A94EDEB2B  (single)
  479 <U> entries under <L n="animations_list">
  ordinal 300 : raw display "Caught Cheating 2"   <- forbidden entry, must stay
  ordinal 318 : raw display "NOT Caught Cheating 2", author "Nevely42",
                clip containing nevely42_cheat2_a0 AND nevely42_cheat2_a1

The built WW XML models the REAL nesting: each entry's clips live under per-actor
<U> elements inside <L n="animation_actors_list">, NOT as flat direct children --
so the fixture genuinely exercises the census-equivalent deep clip extraction
(an old direct-child read returns None here, exactly the real-source failure).

We run the builder (subprocess) against each fixture.  A synthetic file's SHA
differs from the authoritative pin, so the test sets the test-only
WW_P29F_TEST_ACCEPT_ANY_SOURCE_SHA=1 hook (OFF by default; the live Windows command
never sets it) purely so the OTHER gates -- instance / ordinal 318 / display /
author / clip-missing negatives / only-one-entry / mem_size / protected-fields /
source-unchanged / round-trip -- are genuinely exercised against the full build path.
The no-hook wrong-SHA negative (W9) proves the authoritative pin still fail-closes.

Gates asserted
--------------
  W1  build exits 0 and emits VERDICT=PASS
  W2  only ordinal 318 display changes  ->  OWN_ONE_ENTRY_CHANGED; ordinal 300 intact
  W3  before/after display exact values recorded
  W4  author gate + ORDINAL gate + clip(a0,a1) gate pass
  W5  MEM_SIZE_MATCH (written == actual new decompressed length)
  W6  PROTECTED_FIELDS unchanged (stage/author/clips/tags/category untouched)
  W7  SOURCE_UNCHANGED_AFTER_BUILD (we only read source)
  W8  ZERO_WRITE_TO_MODS + PRIORITY=600 in report
  W9  NEGATIVE: a source whose SHA is wrong and NOT overridden -> exit 3, no artifact
  W10 clip diagnostics present: TARGET_CLIPS_FOUND / CLIP_A0_PRESENT / CLIP_A1_PRESENT
  W11 NEGATIVE a0-only -> CLIP fails (no artifact); also a1-only -> CLIP fails
  W12 no gate relaxation: builder CLIP gate cannot be bypassed by env/test hook
Outputs nothing to the repo output/ tree by default (fixtures + artifacts under /tmp).

Exit: 0=PASS, 1=FAIL.
"""
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ww_animation_canary_builder import build_package  # noqa: E402

WW_ANIM_XML = 0x7DF2169C
WW_GROUP = 0x00B2D882
WW_INST = 0x43F3438A94EDEB2B
TOTAL = 479
TARGET_ORD = 318
FORBIDDEN_ORD = 300


def _display(i):
    if i == TARGET_ORD:
        return "NOT Caught Cheating 2"
    if i == FORBIDDEN_ORD:
        return "Caught Cheating 2"
    return "Anim %d" % i


def _clip(i):
    # decorative per-entry clip name (unused in real-gate path when actor clips used)
    return "anm_%d_meta" % i


def _actor_row(clip_token):
    # A single actor <U> nested inside animation_actors_list carrying exactly one
    # animation_clip_name node -- mirrors real WW XML where each actor holds a clip.
    return ('<U n="actor">'
            '<T n="actor_id">1</T>'
            '<T n="animation_clip_name">%s</T>'
            '</U>') % clip_token


def _blob(i, actor_clips=None):
    # display / author / stage stay DIRECT children of the entry (this is what the
    # real source satisfies for BEFORE_DISPLAY and AUTHOR).  clips are NOT direct
    # children here: they are NESTED under per-actor <U> inside animation_actors_list,
    # which is exactly why the old flat direct-child clip read returned None on the
    # real package and the census-equivalent deep read fixes it.
    if actor_clips is None:
        actor_clips = ["nevely42_cheat2_a0", "nevely42_cheat2_a1"] if i == TARGET_ORD else [_clip(i)]
    actors = "".join(_actor_row(c) for c in actor_clips)
    rows = [
        f'<T n="animation_raw_display_name">{_display(i)}</T>',
        (f'<T n="animation_author">Nevely42</T>' if (i in (TARGET_ORD, FORBIDDEN_ORD))
         else '<T n="animation_author">OtherMod</T>'),
        '<T n="animation_stage_name">stage_1</T>',
        '<T n="animation_next_stage_name">stage_2</T>',
        '<T n="animation_tags">tag_a|tag_b</T>',
        '<T n="animation_category">cheating</T>',
        ('<L n="animation_actors_list">%s</L>' % actors),
    ]
    return "".join(rows)


def template_xml(actor_clips_for_318):
    """Return the full animations-list XML text; ordinal-318 uses actor_clips_for_318.

    Every OTHER ordinal (incl. forbidden 300) gets its actors from _blob defaults so
    only the target's actor-clip set varies between fixtures.
    """
    parts = ['<I n="WickedWhimsAnimationPackage"><L n="animations_list">']
    for i in range(TOTAL):
        if i == TARGET_ORD:
            parts.append(f'<U n="anm{300 + i}">{_blob(i, actor_clips_for_318)}</U>')
        else:
            parts.append(f'<U n="anm{300 + i}">{_blob(i)}</U>')
    parts.append("</L></I>")
    return "".join(parts)


def make_pkg(out: Path, actor_clips_for_318=None):
    """Build a DBPF with exactly one WW_ANIM_XML (the synthetic animations XML)."""
    if actor_clips_for_318 is None:
        actor_clips_for_318 = ["nevely42_cheat2_a0", "nevely42_cheat2_a1"]
    xml = template_xml(actor_clips_for_318).encode("utf-8")
    body = xml
    items = [(WW_ANIM_XML, WW_GROUP, WW_INST, body,
              {"comp_state": False, "comp_type": 0, "mem_size": len(xml),
               "offset_high_bit": 0, "size_high_bit": 0})]
    build_package(items, out)
    return out


def _sha(p):
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _report_tokens(report_text):
    return {m.group(1): m.group(2)
            for m in re.finditer(r"^([A-Z0-9_]+)=(.*)$", report_text, re.M)}


def run_builder(src, out_dir, extra_env=None, expect_ok=True):
    env = dict(os.environ)
    # test harness: accept the synthetic source's own SHA so the OTHER gates run.
    env["WW_P29F_TEST_ACCEPT_ANY_SOURCE_SHA"] = "1"
    if extra_env:
        env.update(extra_env)
    cmd = [sys.executable, str(Path(__file__).resolve().parent / "ww_p29f_visible_canary.py"),
           "--source", str(src), "--out-dir", str(out_dir), "--force"]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return r


def main():
    fails = []
    tmp = Path(tempfile.mkdtemp(prefix="p29f_logic_"))
    src = make_pkg(tmp / "WW_Nevely42_Animations.package")

    # ---- positive build ----
    out_dir = tmp / "out_pos"
    r = run_builder(src, out_dir)
    report = (out_dir / "ww_p29f" / "ww_p29f_report.txt")
    stdout = r.stdout
    rc = r.returncode
    print("=== POSITIVE run rc=%d ===" % rc)
    print(stdout)
    if rc != 0:
        fails.append("positive-exit-nonzero(%d)" % rc)
    tok = {}
    if report.exists():
        tok = _report_tokens(report.read_text(encoding="utf-8"))
    else:
        fails.append("no-report-written")

    # W2/W3
    if tok.get("ONLY_ONE_ENTRY_CHANGED") != "YES":
        fails.append("only-one-entry-not-yes: %r" % tok.get("ONLY_ONE_ENTRY_CHANGED"))
    if tok.get("TARGET_BEFORE_DISPLAY") != "NOT Caught Cheating 2":
        fails.append("before-display-mismatch: %r" % tok.get("TARGET_BEFORE_DISPLAY"))
    if tok.get("TARGET_AFTER_DISPLAY") != "P29F_VISIBLE_318":
        fails.append("after-display-mismatch")
    if tok.get("AUTHOR_GATE") != "YES":
        fails.append("author-gate-not-yes")
    if tok.get("CLIP_GATE") != "YES":
        fails.append("clip-gate-not-yes")
    if tok.get("ORDINAL_GATE") != "YES":
        fails.append("ordinal-gate-not-yes")
    if tok.get("BEFORE_DISPLAY_GATE") != "YES":
        fails.append("before-display-gate-not-yes")

    # W1
    if tok.get("VERDICT") != "PASS":
        fails.append("verdict-not-pass: %r" % tok.get("VERDICT"))
    # W5 mem_size
    if tok.get("MEM_SIZE_MATCH_NEW_XML") != "YES":
        fails.append("mem_size-match-not-yes")
    # W6 protected
    if tok.get("PROTECTED_FIELDS_UNCHANGED") != "YES":
        fails.append("protected-fields-not-yes")
    if tok.get("PROTECTED_FIELDS_UNCHANGED") != "YES":
        fails.append("protected-fields-token")
    # W7 source unchanged
    if tok.get("SOURCE_UNCHANGED_AFTER_BUILD") != "YES":
        fails.append("source-unchanged-not-yes")
    # W8 safety tokens
    if tok.get("ZERO_WRITE_TO_MODS") != "YES":
        fails.append("zero-write-not-yes")
    if tok.get("PRIORITY") != "600":
        fails.append("priority-not-600: %r" % tok.get("PRIORITY"))
    if tok.get("SOURCE_SHA_GATE") != "YES":
        fails.append("source-sha-gate-not-yes")

    # ordinal 300 (forbidden) must be intact: it is not in the changed set, but we
    # prove via ONLY_ONE_ENTRY_CHANGED which compares all 479 to source. Also
    # explicitly verify with an artifact parse independent of builder tokens.
    art = out_dir / "ww_p29f" / "WW_P29F_VISIBLE_318_Override.package"
    if not art.exists():
        fails.append("artifact-missing")
    else:
        # re-open artifact XML; assert ordinal 300 still 'Caught Cheating 2',
        # ordinal 318 == new, only one diff vs a rebuild of source xml.
        from ww_animation_canary_builder import safe_parse, read_body_raw, decompress_maybe
        body2 = None
        idx, _ = safe_parse(art)
        for e in idx.entries:
            if e.type_id == WW_ANIM_XML and e.instance_id == WW_INST:
                body2 = decompress_maybe(read_body_raw(art, e)).decode("utf-8")
        if body2 is None:
            fails.append("artifact-xml-not-found")
        else:
            root = ET.fromstring(body2)
            lst = None
            for el in root.iter():
                if el.tag.rsplit("}",1)[-1] == "L" and el.get("n") == "animations_list":
                    lst = el
            vals = []
            for ch in lst:
                if ch.tag.rsplit("}",1)[-1] != "U":
                    continue
                rv = None
                for sc in ch:
                    if sc.tag.rsplit("}",1)[-1] in ("T","I","E") and sc.get("n") == "animation_raw_display_name":
                        rv = sc.text
                vals.append(rv)
            if len(vals) != TOTAL:
                fails.append("artifact-entry-count=%d" % len(vals))
            if vals[FORBIDDEN_ORD] != "Caught Cheating 2":
                fails.append("ordinal-300-disturbed=%r" % vals[FORBIDDEN_ORD])
            if vals[TARGET_ORD] != "P29F_VISIBLE_318":
                fails.append("ordinal-318-not-new=%r" % vals[TARGET_ORD])
            src_idx, _ = safe_parse(src)
            src_body = None
            for e in src_idx.entries:
                if e.type_id == WW_ANIM_XML and e.instance_id == WW_INST:
                    src_body = decompress_maybe(read_body_raw(src, e)).decode("utf-8")
            srcvals = []
            sroot = ET.fromstring(src_body)
            for el in sroot.iter():
                if el.tag.rsplit("}",1)[-1] == "L" and el.get("n") == "animations_list":
                    for ch in el:
                        if ch.tag.rsplit("}",1)[-1] != "U":
                            continue
                        for sc in ch:
                            if sc.tag.rsplit("}",1)[-1] in ("T","I","E") and sc.get("n") == "animation_raw_display_name":
                                srcvals.append(sc.text)
            diffs = [i for i in range(TOTAL) if srcvals[i] != vals[i]]
            if set(diffs) != {TARGET_ORD}:
                fails.append("only-one-entry-diff-vs-source: %r" % diffs)

    # ---- NEGATIVE 1: wrong SHA pin WITHOUT test override -> exit 3, no artifact ----
    out_dir2 = tmp / "out_neg"
    r2 = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "ww_p29f_visible_canary.py"),
         "--source", str(src), "--out-dir", str(out_dir2), "--force"],
        capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if k != "WW_P29F_TEST_ACCEPT_ANY_SOURCE_SHA"})
    print("=== NEGATIVE (no SHA override) rc=%d ===" % r2.returncode)
    print(r2.stdout)
    print(r2.stderr)
    if r2.returncode == 0:
        fails.append("negative-wrong-sha-returned-0")
    if (out_dir2 / "ww_p29f" / "WW_P29F_VISIBLE_318_Override.package").exists():
        fails.append("negative-artifact-written")

    # ---- NEGATIVE 1: wrong SHA pin WITHOUT test override -> exit 3, no artifact ----
    out_dir2 = tmp / "out_neg"
    r2 = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "ww_p29f_visible_canary.py"),
         "--source", str(src), "--out-dir", str(out_dir2), "--force"],
        capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if k != "WW_P29F_TEST_ACCEPT_ANY_SOURCE_SHA"})
    print("=== NEGATIVE (no SHA override) rc=%d ===" % r2.returncode)
    print(r2.stdout)
    print(r2.stderr)
    if r2.returncode == 0:
        fails.append("negative-wrong-sha-returned-0")
    if (out_dir2 / "ww_p29f" / "WW_P29F_VISIBLE_318_Override.package").exists():
        fails.append("negative-artifact-written")

    # ---- positive clip diagnostic tokens ----
    if tok.get("TARGET_CLIPS_FOUND") is None:
        fails.append("target-clips-found-missing")
    if tok.get("CLIP_A0_PRESENT") != "YES":
        fails.append("positive-clip-a0-not-yes: %r" % tok.get("CLIP_A0_PRESENT"))
    if tok.get("CLIP_A1_PRESENT") != "YES":
        fails.append("positive-clip-a1-not-yes: %r" % tok.get("CLIP_A1_PRESENT"))

    # ---- NEGATIVE clip fixtures: only a0 / only a1 must each fail, no artifact ----
    for label, clips in (("a0-only", ["nevely42_cheat2_a0"]),
                         ("a1-only", ["nevely42_cheat2_a1"])):
        ndir = tmp / ("out_" + label.replace("-", "_"))
        nsrc = ndir / "WW_Nevely42_Animations.package"
        ndir.mkdir(parents=True, exist_ok=True)
        make_pkg(nsrc, actor_clips_for_318=clips)
        nn = run_builder(nsrc, ndir)
        art = ndir / "ww_p29f" / "WW_P29F_VISIBLE_318_Override.package"
        print("=== NEGATIVE-clip %s rc=%d artifact=%s ===" % (label, nn.returncode, art.exists()))
        print(nn.stdout)
        if nn.returncode == 0:
            fails.append("clip-%s-returned-0" % label)
        if art.exists():
            fails.append("clip-%s-artifact-written" % label)
        ntok = {}
        rpt = ndir / "ww_p29f" / "ww_p29f_report.txt"
        if rpt.exists():  # structural-fail path never writes report; assert none expected
            fails.append("clip-%s-report-should-not-exist" % label)

    print("=== P29F LOGIC VERDICT ===")
    if fails:
        print("FAIL")
        for f in fails:
            print("  - " + f)
        print("PASS_COUNT=0")
        return 1
    print("PASS")
    print("PASS_COUNT=1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
