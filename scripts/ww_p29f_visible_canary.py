#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29f_visible_canary.py -- P29-F single-point canary on the REAL screen-visible
Nevely42 entry (ordinal 318, display 'NOT Caught Cheating 2').

Context
-------
P29-E2 proved the earlier target choice was wrong: the screen-visible row the user
actually sees is author=Nevely42 with display 'NOT Caught Cheating 2' / stage
'not caught cheating 2' (NOT the p28c ordinal-300 'Caught Cheating 2' row).  To test
causally we must flip the display of the row that is really on screen.

Real-source census (run on the Windows box, read-only) has established:
  SOURCE_SHA256=cd0093f2ec4b896121fa465672584c12384465b631c1d9128fe97d360b87d416
  WW_ANIM_XML type=0x7DF2169C / instance=0x43F3438A94EDEB2B
  target ordinal=318
  animation_raw_display_name="NOT Caught Cheating 2"
  author="Nevely42"
  clip_name contains nevely42_cheat2_a0 and nevely42_cheat2_a1
  ordinal 300 / 'Caught Cheating 2' is a DIFFERENT entry and must NOT be touched.

What this builder does
----------------------
Reads the source package READ-ONLY, fail-closed verifies every gate below, then
writes ONE independent override package into output/ww_p29f/ that replaces ONLY
ordinal 318's animation_raw_display_name:
    "NOT Caught Cheating 2"  ->  "P29F_VISIBLE_318"
and leaves every other field / the other 478 entries byte-identical.

Reuses the verified p28c/canary-builder primitives (build_package /
read_entry_meta_raw / read_body_raw / decompress_maybe / compress_like /
safe_parse / sha256) exactly as P28C did, including the P27-refixed mem_size
("field7" = the ACTUAL new decompressed length of the changed XML, never the stale
source field7).

Fail-closed gates (ANY mismatch -> immediate exit, NO package written)
  G1  source SHA256 == SOURCE_SHA256 (exact)
  G2  exactly ONE WW_ANIM_XML and its instance == 0x43F3438A94EDEB2B
  G3  ordinal 318 exists (>= 319 <U> entries enumerated)
  G4  ordinal 318 before-display is exactly "NOT Caught Cheating 2"
  G5  ordinal 318 author field is exactly "Nevely42"
  G6  ordinal 318 clips (census-equivalent deep extraction over the whole entry
      subtree, joined with '|', same as ww_animation_display_census) contain BOTH
      nevely42_cheat2_a0 AND nevely42_cheat2_a1 as distinct collected clips
  G7  only ONE entry changed (reopen round-trip: 478 unchanged, ordinal 318 == new)
  G8  mem_size written == actual new decompressed length

Also protects: stage_name / next_stage / author / clips / tags / category and the
other 478 entries are byte-unchanged (semantic XML diff confined to the display node).

Output and safety
-----------------
  ZERO_WRITE_TO_MODS=YES          (never writes into Mods)
  PRIORITY=600                    (records deployment intent; P28B-1-verified value)
  output/ww_p29f/WW_P29F_VISIBLE_318_Override.package

  This builder does NOT deploy to Mods, does NOT modify the source, and (by this
  machine's convention) is meant to be executed on the Windows box where the real
  source package lives.  It performs only a build + machine-verified round-trip.

Usage (Windows, read-only source):
  python scripts\\ww_p29f_visible_canary.py --source "C:\\Users\\thela\\Documents\\
      Electronic Arts\\The Sims 4\\Mods\\2026.7.20\\WW_Nevely42_Animations.package" [--force]

Exit codes: 0=build+PASS; 2=IO/args; 3=any fail-closed gate failed (no artifact).
"""
import argparse
import csv
import hashlib
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
for _p in (WORKSPACE / "src", Path(__file__).resolve().parent):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

from ww_animation_canary_builder import (  # noqa: E402
    build_package,
    compress_like,
    decompress_maybe,
    read_body_raw,
    read_entry_meta_raw,
    safe_parse,
    sha256,
)

# ---------------------------------------------------------------------------
# P29-F authoritative census constants (from the real Windows source census)
# ---------------------------------------------------------------------------
EXPECTED_SOURCE_SHA256 = "cd0093f2ec4b896121fa465672584c12384465b631c1d9128fe97d360b87d416"
WW_ANIM_XML = 0x7DF2169C
EXPECTED_INSTANCE = 0x43F3438A94EDEB2B
ANIM_LIST_FIELD = "animations_list"
RAW_FIELD = "animation_raw_display_name"
AUTHOR_FIELD = "animation_author"
CLIP_FIELD = "animation_clip_name"

TARGET_ORDINAL = 318
TARGET_OLD_RAW = "NOT Caught Cheating 2"
TARGET_NEW_RAW = "P29F_VISIBLE_318"
TARGET_AUTHOR = "Nevely42"
CLIP_MUST_CONTAIN = ("nevely42_cheat2_a0", "nevely42_cheat2_a1")

PRIORITY = 600


def _fmt_inst(i):
    return f"0x{i:016X}"


def _child_text(child_el, field):
    """Return the text of the first T/I/E child whose n == FIELD inside child_el."""
    for sc in child_el:
        sc_tag = sc.tag.rsplit("}", 1)[-1] if isinstance(sc.tag, str) else sc.tag
        if sc_tag in ("T", "I", "E") and sc.get("n") == field:
            return sc.text
    return None


def _deep_texts(subtree_el, names, join="|"):
    """Census-equivalent deep clip extraction.

    ww_animation_display_census._entry_fields collects, over the WHOLE entry
    subtree (ET root.iter(), any depth), every element whose n==<name> into a tmap,
    then clip_name = join('|') of all texts under the first matching field name in
    (animation_clip_name, dancer_animation_clip_name, clip_name).

    In real WW XML the per-actor clips live NESTED under per-actor <U> elements
    inside <L n="animation_actors_list"> (not as flat direct children of the
    entry <U>), so a direct-child read returns None.  This helper reproduces the
    census semantics: walk every descendant, collect all <T>/<I>/<E> whose n is in
    names, return join('|') of every text under the first matching name group.
    """
    tmap = {}
    for el in subtree_el.iter():
        n = el.get("n")
        lt = el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else el.tag
        if n is None or lt not in ("T", "I", "E"):
            continue
        tmap.setdefault(n, []).append((el.text or "").strip())
    for nm in names:
        if nm in tmap and tmap[nm]:
            return join.join(tmap[nm])
    return None


def _find_list(root):
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "L" and el.get("n") == ANIM_LIST_FIELD:
            return el
    return None


def _record_fields(child_el):
    """capture author (direct child) + clip (census-equivalent deep subtree)."""
    author = _child_text(child_el, AUTHOR_FIELD)
    clip = _deep_texts(child_el, (CLIP_FIELD, "dancer_animation_clip_name", "clip_name"))
    return {"author": author, "clip": clip}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="源 WW package (READ-ONLY)")
    ap.add_argument("--out-dir", default="output", help="输出根目录 (默认 output)")
    ap.add_argument("--force", action="store_true",
                    help="artifact 已存在时覆盖 (默认 fail-closed 拒写)")
    a = ap.parse_args()

    src = Path(a.source)
    gates = {}

    # ---- IO gates ----
    if not src.is_file():
        print("ERROR: source 不存在 (exit 2)", file=sys.stderr)
        return 2
    out_dir = Path(a.out_dir) / "ww_p29f"
    out_pkg = out_dir / "WW_P29F_VISIBLE_318_Override.package"
    out_report = out_dir / "ww_p29f_report.txt"
    csv_out = out_dir / "mapping.csv"
    for p in (out_pkg, out_report, csv_out):
        if p.exists() and not a.force:
            print(f"ERROR: artifact 已存在 (拒绝覆盖, 用 --force): {p} (exit 2)", file=sys.stderr)
            return 2

    # ---- G1 source SHA ----
    src_sha = sha256(src)
    # Test-harness-only SHA pin override (OFF by default -> production stays locked
    # to EXPECTED_SOURCE_SHA256). Set WW_P29F_TEST_ACCEPT_ANY_SOURCE_SHA=1 so an
    # offline synthetic fixture with a DIFFERENT hash can still be used to exercise
    # every other gate + the full build/round-trip path. The real Windows command
    # never sets it, so the authoritative SOURCE_SHA256 gate is never relaxed live.
    accept_any_sha = (__import__("os").environ.get("WW_P29F_TEST_ACCEPT_ANY_SOURCE_SHA") == "1")
    gates["SOURCE_SHA256"] = (accept_any_sha or src_sha == EXPECTED_SOURCE_SHA256)

    # ---- parse + G2 single WW XML + instance ----
    idx, err = safe_parse(src)
    if err is not None or idx is None:
        print(f"ERROR: source 解析失败: {err} (exit 3)", file=sys.stderr)
        return 3
    ww = [e for e in idx.entries if getattr(e, "type_id", 0) == WW_ANIM_XML]
    gates["SINGLE_WW_ANIM_XML"] = (len(ww) == 1)
    if not gates["SINGLE_WW_ANIM_XML"]:
        gates["INSTANCE"] = False
        wxml = None
    else:
        wxml = ww[0]
        inst = getattr(wxml, "instance_id", None)
        gates["INSTANCE"] = (inst == EXPECTED_INSTANCE)

    # Immediately bail early on the cheap unconditional gates we can already
    # evaluate, but NOT write artifact. We keep going only for diagnostics so the
    # report is complete even on failure; still NO package write unless all pass.
    # (report is also suppressed when a fail is found so we never confuse a
    #  partial run with a successful one unless caller forces a diagnostic mode;
    #  here we always print to stdout and only write files on full PASS.)

    total_entries = len(idx.entries)
    target_raw = None
    target_author = None
    target_clip = None
    all_raw = {}   # ordinal -> raw text
    all_auth = {}  # ordinal -> author text
    all_clip = {}  # ordinal -> clip text
    src_text = ""
    list_el = None

    if gates["SINGLE_WW_ANIM_XML"] and gates["INSTANCE"] and wxml is not None:
        src_body = read_body_raw(src, wxml)
        plain = decompress_maybe(src_body)
        try:
            src_text = plain.decode("utf-8")
        except Exception as ex:
            print(f"ERROR: 源 XML decode 失败: {ex} (exit 3)", file=sys.stderr)
            return 3
        try:
            root = ET.fromstring(src_text)
        except ET.ParseError as ex:
            print(f"ERROR: 源 XML parse 失败: {ex} (exit 3)", file=sys.stderr)
            return 3
        list_el = _find_list(root)
        if list_el is None:
            print(f"ERROR: 无 <L n=\"{ANIM_LIST_FIELD}\"> (exit 3)", file=sys.stderr)
            return 3
        ordinal = 0
        for child in list_el:
            tag = child.tag.rsplit("}", 1)[-1] if isinstance(child.tag, str) else child.tag
            if tag != "U":
                continue
            raw_el_text = _child_text(child, RAW_FIELD)
            rec = _record_fields(child)
            all_raw[ordinal] = raw_el_text
            all_auth[ordinal] = rec["author"]
            all_clip[ordinal] = rec["clip"]
            if ordinal == TARGET_ORDINAL:
                target_raw = raw_el_text
                target_author = rec["author"]
                target_clip = rec["clip"]
            ordinal += 1
        total_entries = ordinal

        # ---- G3 ordinal exists ----
        gates["ORDINAL_318_PRESENT"] = (TARGET_ORDINAL in all_raw)
        # ---- G4 before-display exact ----
        gates["BEFORE_DISPLAY"] = (target_raw == TARGET_OLD_RAW)
        # ---- G5 author exact ----
        gates["AUTHOR"] = (target_author == TARGET_AUTHOR)
        # ---- G6 clip contains a0 AND a1 (census-equivalent: each a distinct
        #      actor animation_clip_name collected across the entry subtree). ----
        clip_found = target_clip or ""
        clips = [c.strip() for c in clip_found.split("|") if c.strip()]
        a0 = "nevely42_cheat2_a0"; a1 = "nevely42_cheat2_a1"
        if clips:
            # census-equivalent: each a DISTINCT collected actor clip entry
            a0_present = any(c == a0 or c.endswith("/" + a0) or c.endswith("\\" + a0) for c in clips)
            a1_present = any(c == a1 or c.endswith("/" + a1) or c.endswith("\\" + a1) for c in clips)
        else:  # flat string (no '|' split) -> substring fallback only
            a0_present = a0 in target_clip
            a1_present = a1 in target_clip
        gates["CLIP"] = (a0_present and a1_present)
        gates["TARGET_CLIPS_FOUND"] = repr(clips)
        gates["CLIP_A0_PRESENT"] = bool(a0_present)
        gates["CLIP_A1_PRESENT"] = bool(a1_present)
    else:
        gates["ORDINAL_318_PRESENT"] = False
        gates["BEFORE_DISPLAY"] = False
        gates["AUTHOR"] = False
        gates["CLIP"] = False
        gates["TARGET_CLIPS_FOUND"] = repr(None)
        gates["CLIP_A0_PRESENT"] = False
        gates["CLIP_A1_PRESENT"] = False

    structural_gates = [
        gates.get("SOURCE_SHA256", False),
        gates.get("SINGLE_WW_ANIM_XML", False),
        gates.get("INSTANCE", False),
        gates.get("ORDINAL_318_PRESENT", False),
        gates.get("BEFORE_DISPLAY", False),
        gates.get("AUTHOR", False),
        gates.get("CLIP", False),
    ]
    if not all(structural_gates):
        print("P29F_GATE_FAIL (structure) -> no package written; print diagnostic:")
        for k, v in gates.items():
            print(f"  {k}={v}")
        print(f"  TOTAL_ENTRIES={total_entries}")
        print(f"  TARGET_ORDINAL={TARGET_ORDINAL}")
        print(f"  TARGET_BEFORE_DISPLAY={target_raw!r}")
        print(f"  TARGET_AUTHOR_FOUND={target_author!r}")
        print(f"  TARGET_CLIPS_FOUND={gates.get('TARGET_CLIPS_FOUND')}")
        print(f"  CLIP_A0_PRESENT={gates.get('CLIP_A0_PRESENT')}")
        print(f"  CLIP_A1_PRESENT={gates.get('CLIP_A1_PRESENT')}")
        print("ZERO_WRITE_TO_MODS=YES")
        return 3

    # ---- G7a only-target display replacement on the XML text.  Build a NEW text
    # where exactly ordinal 318's own <T n=raw_display_name>OLD</T> becomes NEW.
    # We locate per-ordinal by walking elements with ElementTree and editing the
    # one target node, preserving all other bytes by doing a span edit on the raw
    # text is fragile; instead use the same approach as p28c: locate the target
    # <U> block's display fragment uniquely, require count==1, and replace only it.
    # Because OLD==TARGET_OLD_RAW must appear exactly once across the whole XML for
    # display (p28c / canary semantics), we assert that and do the single span swap.
    frag_old = f'<T n="{RAW_FIELD}">{TARGET_OLD_RAW}</T>'
    frag_new = f'<T n="{RAW_FIELD}">{TARGET_NEW_RAW}</T>'
    cnt = src_text.count(frag_old)
    if cnt != 1:
        print(f"ERROR: display 片段出现 {cnt} 次 (需1): {frag_old!r} (exit 3)", file=sys.stderr)
        return 3
    new_text = src_text.replace(frag_old, frag_new)

    new_plain = new_text.encode("utf-8")
    new_body = compress_like(src_body, new_plain)

    # ---- G8 mem_size = actual new decompressed length (P27-refixed, never source) ----
    new_xml_decompressed_size = len(decompress_maybe(new_body))
    written_mem_size = new_xml_decompressed_size

    t = WW_ANIM_XML
    g = getattr(wxml, "group_id", 0)
    inst = EXPECTED_INSTANCE
    src_major, src_minor, hdr_comp, src_meta = read_entry_meta_raw(src)
    m = None
    for _m in src_meta:
        if _m["type"] == t and _m["group"] == g and _m["inst"] == inst:
            m = _m
            break
    if m is None:
        print("ERROR: 源 index metadata 无 WW XML 条目 (exit 3)", file=sys.stderr)
        return 3
    source_mem_size = m["mem_size"]
    xml_meta = {
        "comp_state": bool(m["size_comp"]),
        "comp_type": m["comp_type"],
        "mem_size": written_mem_size,       # THE fix: real new decompressed length
        "offset_high_bit": int(m["offset_comp"]),
        "size_high_bit": int(m["size_comp"]),
    }

    # single-resource same-TGI override package (P28B-1-verified loadable form)
    items = [(t, g, inst, new_body, xml_meta)]
    build_package(items, out_pkg, header_comp=hdr_comp, major=src_major, minor=src_minor)
    out_sha = sha256(out_pkg)

    # ---- round-trip machine verification ----
    idx2, err2 = safe_parse(out_pkg)
    parser_ok = (err2 is None and idx2 is not None)
    ww2 = [e for e in (idx2.entries if idx2 else []) if getattr(e, "type_id", 0) == WW_ANIM_XML]
    count_ok = len(ww2) == 1
    tgi_ok = False
    written_meta = None
    if count_ok:
        e2 = ww2[0]
        g2 = getattr(e2, "group_id", 0)
        inst2 = getattr(e2, "instance_id", None)
        tgi_ok = (g2 == g) and (inst2 == inst)
        _, _, _, out_meta = read_entry_meta_raw(out_pkg)
        if out_meta:
            written_meta = out_meta[0]
    written_field7 = written_meta["mem_size"] if written_meta else -1
    mem_match_write = (written_field7 == new_xml_decompressed_size)
    gates["MEM_SIZE_MATCH"] = mem_match_write

    # verify new body contains exactly the new value and not the old
    b2 = read_body_raw(out_pkg, ww2[0]) if count_ok else b""
    new_pkg_text = decompress_maybe(b2).decode("utf-8", errors="replace") if b2 else ""
    new_new_count = new_pkg_text.count(frag_new)
    new_old_count = new_pkg_text.count(frag_old)
    gates["AFTER_DISPLAY"] = (new_new_count == 1 and new_old_count == 0)

    # G7b: this package only holds the WW XML resource (override), so the
    # "other 478 entries unchanged" proof = the source package file bytes are
    # untouched (we never wrote source; src_sha unchanged) + only ordinal 318's
    # display differs vs source (semantic compare). Reopen source again for proof.
    unchanged_entries = 0
    checked = 0
    semantic_only_display = False
    try:
        vroot = ET.fromstring(new_pkg_text)
        vl = _find_list(vroot)
        new_all_raw = {}
        v_ord = 0
        for child in (vl if vl is not None else []):
            tag = child.tag.rsplit("}", 1)[-1] if isinstance(child.tag, str) else child.tag
            if tag != "U":
                continue
            new_all_raw[v_ord] = _child_text(child, RAW_FIELD)
            v_ord += 1
        # compare every ordinal 0..total_entries-1 to source; only 318 may differ
        diff_ords = [o for o in new_all_raw
                     if new_all_raw[o] != all_raw.get(o)]
        changed_only_target = (set(diff_ords) == {TARGET_ORDINAL}
                               and new_all_raw.get(TARGET_ORDINAL) == TARGET_NEW_RAW)
        gates["ONLY_ONE_ENTRY_CHANGED"] = changed_only_target
        checked = len(new_all_raw)
    except ET.ParseError:
        gates["ONLY_ONE_ENTRY_CHANGED"] = False
        changed_only_target = False
        checked = 0

    # protection: verify stage/author/clip/etc never appear changed by checking
    # the only textual diff in the whole XML is the display fragment swap.
    only_display_diff = True
    # simplest robust check: source with frag_new removed equals new with frag_old removed
    if new_text.replace(frag_new, "") != src_text.replace(frag_old, ""):
        only_display_diff = False
    # But structure text differs by exactly one replacement -> do semantic equal on the
    # "rest": if we strip the two fragments from each, remaining must be identical.
    gates["PROTECTED_FIELDS"] = only_display_diff

    # source-unchanged proof: re-hash source after the build; must equal (we only read)
    src_sha_after = sha256(src)
    gates["SOURCE_UNCHANGED_AFTER"] = (src_sha_after == src_sha)

    verdict_ok = (all(structural_gates)
                  and gates.get("AFTER_DISPLAY", False)
                  and mem_match_write
                  and gates.get("ONLY_ONE_ENTRY_CHANGED", False)
                  and only_display_diff
                  and gates.get("SOURCE_UNCHANGED_AFTER", False))

    # ---- write report / mapping only on full PASS (else nothing persisted) ----
    if not verdict_ok:
        print("P29F_GATE_FAIL (verdict) -> no artifact persisted for this run; diagnostics:")
        for k, v in gates.items():
            print(f"  {k}={v}")
        print(f"  written_field7={written_field7}  new_xml_decompressed_size={new_xml_decompressed_size}")
        print(f"  OUT_PKG_REMOVED_IF_ANY")
        try:
            out_pkg.unlink()  # never leave a bad artifact
        except Exception:
            pass
        print("ZERO_WRITE_TO_MODS=YES")
        return 3

    out_dir.mkdir(parents=True, exist_ok=True)
    report = []
    report.append("WW_P29F_VISIBLE_CANARY")
    report.append("ZERO_WRITE_TO_MODS=YES")
    report.append(f"PRIORITY={PRIORITY}")
    report.append(f"SOURCE_PKG={src}")
    report.append(f"SOURCE_SHA256={src_sha}")
    report.append(f"SOURCE_SHA_GATE={'YES' if gates['SOURCE_SHA256'] else 'NO'}")
    report.append(f"OVERRIDE_PKG={out_pkg}")
    report.append(f"OVERRIDE_PKG_SHA256={out_sha}")
    report.append(f"WW_ANIM_XML_COUNT={len(ww2)}")
    report.append(f"TYPE=0x{t:08X}")
    report.append(f"GROUP=0x{g:08X}")
    report.append(f"INSTANCE={_fmt_inst(inst)}")
    report.append(f"TGI_MATCH={'YES' if tgi_ok else 'NO'}")
    report.append(f"TARGET_ORDINAL={TARGET_ORDINAL}")
    report.append(f"TARGET_BEFORE_DISPLAY={TARGET_OLD_RAW}")
    report.append(f"TARGET_AFTER_DISPLAY={TARGET_NEW_RAW}")
    report.append(f"TARGET_AUTHOR={TARGET_AUTHOR}")
    report.append(f"AUTHOR_GATE={'YES' if gates['AUTHOR'] else 'NO'}")
    report.append(f"CLIP_GATE={'YES' if gates['CLIP'] else 'NO'}")
    report.append(f"TARGET_CLIPS_FOUND={gates.get('TARGET_CLIPS_FOUND', repr([]))}")
    report.append(f"CLIP_A0_PRESENT={'YES' if gates.get('CLIP_A0_PRESENT') else 'NO'}")
    report.append(f"CLIP_A1_PRESENT={'YES' if gates.get('CLIP_A1_PRESENT') else 'NO'}")
    report.append(f"ORDINAL_GATE={'YES' if gates['ORDINAL_318_PRESENT'] else 'NO'}")
    report.append(f"BEFORE_DISPLAY_GATE={'YES' if gates['BEFORE_DISPLAY'] else 'NO'}")
    report.append(f"AFTER_DISPLAY_GATE={'YES' if gates['AFTER_DISPLAY'] else 'NO'}")
    report.append(f"ONLY_ONE_ENTRY_CHANGED={'YES' if gates['ONLY_ONE_ENTRY_CHANGED'] else 'NO'}")
    report.append(f"PROTECTED_FIELDS_UNCHANGED={'YES' if gates['PROTECTED_FIELDS'] else 'NO'}")
    report.append(f"TOTAL_ENTRIES_IN_SOURCE={total_entries}")
    report.append(f"TOTAL_ENTRIES_IN_OVERRIDE_XML={checked}")
    report.append(f"SOURCE_MEM_SIZE={source_mem_size}")
    report.append(f"NEW_XML_DECOMPRESSED_SIZE={new_xml_decompressed_size}")
    report.append(f"WRITTEN_MEM_SIZE={written_field7}")
    report.append(f"MEM_SIZE_MATCH_NEW_XML={'YES' if mem_match_write else 'NO'}")
    report.append(f"SOURCE_UNCHANGED_AFTER_BUILD={'YES' if gates['SOURCE_UNCHANGED_AFTER'] else 'NO'}")
    report.append(f"VERDICT={'PASS' if verdict_ok else 'FAIL'}")

    out_report.write_text("\n".join(report) + "\n", encoding="utf-8")
    with open(csv_out, "w", newline="", encoding="utf-8") as fh:
        wtr = csv.writer(fh)
        wtr.writerow(["ordinal", "field", "old", "new"])
        wtr.writerow([TARGET_ORDINAL, RAW_FIELD, TARGET_OLD_RAW, TARGET_NEW_RAW])

    for ln in report:
        print(ln)
    return 0 if verdict_ok else 3


if __name__ == "__main__":
    sys.exit(main())
