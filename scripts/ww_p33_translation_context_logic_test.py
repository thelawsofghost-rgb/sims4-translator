#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p33_translation_context_logic_test.py --- offline logic gate for the P33
translation-context builder.  The real P32 source package + its 479-row CSV are
Windows-only; here we drive the REAL P32 machinery (C.build_catalog +
C.write_csv over a synthetic package) to produce a deterministic, column/identity
faithful P33 interface CSV, then run ww_p33_translation_context against it and
assert the operator's P33 gate lines plus conservative series behaviour.

Keeps the P32 identity path untouched (the CSV identifiers come from P32 itself,
so P33 inherits them verbatim -- the very property the gate checks).  ZERO write
to Mods/saves/source.  No Chinese generated (P33 must never translate).
"""
import csv
import json
import pathlib
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import ww_p32_identifier_catalog as C          # noqa: E402
import ww_p33_translation_context as P33       # noqa: E402

WW_ANIM_XML = 0x7DF2169C
WW_GROUP = 0x00B2D882
WW_INST = 0x43F3438A94EDEB2B
ORD = C.GOLDEN_ORDINAL                        # 318
EXPECT = C.EXPECT_ENTRY_COUNT                 # 479

_passes = []
SELF = Path(__file__).resolve().parent / "ww_p33_translation_context.py"


def check(name, cond, detail=""):
    _passes.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))
    return bool(cond)


def _esc(v):
    return "" if v is None else str(v)


def _actor(clip, gender, cid="1", atype="penetrative"):
    g = ('<T n="animation_genders">%s</T>' % gender) if gender else ""
    return ('<U n="actor"><T n="actor_id">%s</T>'
            '<T n="animation_animation_type">%s</T>'
            '<T n="animation_clip_name">%s</T>%s</U>') % (cid, atype, clip, g)


def _loc_raw(locs):
    if locs is None:
        return ""
    return '<T n="animation_locations">%s</T>' % locs


def mkrow(i, extra_actors=(), genders=("MALE",),
          clips=(""), locs="FLOOR", name=None, author="Synth",
          cat=None, oclip="", props="", tags="", stage="", pref=""):
    """Deterministic valid row.  Provide explicit actors/clips/locations so the
    offline fixture can cover every P33 archetype while remaining valid (gender
    MALE/FEMALE/BOTH all proven).  ordinal318 special-cased to golden."""
    if i == ORD:
        dn = "NOT Caught Cheating 2"; au = "Nevely42"; cc = "VAGINAL"
        lc = "DOUBLE_BED"; cl = ["nevely42_cheat2_a0", "nevely42_cheat2_a1"]
        gs = ["MALE", "FEMALE"]
    else:
        dn = name if name is not None else "Catalog %d" % i
        au = author; cc = cat if cat else (["ORAL", "ANAL", "VAGINAL"][i % 3])
        lc = "FLOOR" if locs is None else locs
        cl = list(clips) if clips else ["synth_%d_a0" % i]
        gs = list(genders)
    actors = [_actor(cl[k], gs[k], cid=str(k + 1))
              for k in range(len(gs))]
    actors += extra_actors
    obj = ('<T n="object_animation_clip_name">%s</T>' % oclip) if oclip else ""
    pr = ('<L n="animation_props_list"><U n="p"><T n="prop_animation_clip_name">%s'
          '</T></U></L>' % props) if props else ""
    tg = ('<T n="animation_tags">%s</T>' % tags) if tags else ""
    st = ('<T n="animation_stage_name">%s</T>' % stage) if stage else ""
    pf = ('<T n="animation_pref_gender">%s</T>' % pref) if pref else ""
    extra_container = extra_actors and (obj or pr or tg or st or pf)
    return ('<U n="anm%03d">' % i
            + '<T n="animation_raw_display_name">%s</T>' % dn
            + '<T n="animation_author">%s</T>' % au
            + '<T n="animation_category">%s</T>' % cc
            + _loc_raw(lc)
            + '<L n="actors">%s</L>' % "".join(actors)
            + obj + pr + tg + st + pf
            + '</U>')


def full_479():
    pool = ["Late Night Whisper", "Kitchen Counter", "Morning Stretch",
            "Behind the Blinds", "Staircase Scene", "Couch Session",
            "Powder Room", "Midday Break", "Wall Practice", "Corner Office",
            "Rooftop Hour", "Front Hallway", "Half Open Door", "Quiet Study",
            "Back Porch", "Wide Window", "Tight Corner", "Open Lounge",
            "Guest Bed", "Second Floor", "Night Shift", "Slow Awakening"]
    rows = [mkrow(i, name=pool[i % len(pool)], author="Nevely42")
            for i in range(EXPECT)]
    # decorate a deterministic handful to light every preview archetype
    rows[10] = mkrow(10, genders=("BOTH",), clips=("g_b0",), locs="DOUBLE_BED",
                     name="Solo Both", cat="ORAL")
    rows[20] = mkrow(20, genders=("MALE", "FEMALE", "BOTH"),
                     clips=("t1_a0", "t1_a1", "t1_a2"), locs="DOOR",
                     name="Trio Mix", cat="VAGINAL")
    rows[30] = mkrow(30, genders=("MALE",), clips=("o1_a0"), locs=None,
                     name="Orphan Room", cat="ANAL", oclip="obj_o1_a0",
                     props="prop_o1_p0", tags="object", stage="main",
                     pref="dominant")
    # ---- series: decimal 1..4, same author + stem ----
    rows[40] = mkrow(40, name="Gearshift 1", author="Racer", cat="GSSEX")
    rows[41] = mkrow(41, name="Gearshift 2", author="Racer", cat="GSSEX")
    rows[42] = mkrow(42, name="Gearshift 3 - Climax", author="Racer", cat="GSSEX")
    rows[43] = mkrow(43, name="Gearshift 4", author="Racer", cat="GSSEX")
    # a NON-adjacent faker-looking title (different stem) must NOT join them
    rows[44] = mkrow(44, name="Gear Box Test", author="Racer", cat="GSSEX")
    # ---- roman series I..II + suffix twin ----
    rows[50] = mkrow(50, name="Heat I - 1", author="Ember", cat="VAGINAL")
    rows[51] = mkrow(51, name="Heat I - 2", author="Ember", cat="VAGINAL")
    rows[52] = mkrow(52, name="Heat II - 1", author="Ember", cat="VAGINAL")
    rows[53] = mkrow(53, name="Heat II - 2", author="Ember", cat="VAGINAL")
    # standalone title (no numeric sequence)
    rows[60] = mkrow(60, name="Under the Desk", author="Ember", cat="ORAL")
    rows[61] = mkrow(61, name="Desk Job", author="Ember", cat="ORAL")  # diff stem
    return rows


def make_pkg(out, rows):
    from ww_animation_canary_builder import build_package
    xml = '<I n="WickedWhimsAnimationPackage"><L n="animations_list">' + \
          "".join(rows) + '</L></I>'
    body = xml.encode("utf-8")
    meta = {"comp_state": False, "comp_type": 0, "mem_size": len(xml),
            "offset_high_bit": 0, "size_high_bit": 0}
    build_package([(WW_ANIM_XML, WW_GROUP, WW_INST, body, meta)], out)


def _run_p33(csv_path, out_dir):
    return subprocess.run([sys.executable, str(SELF), str(csv_path),
                           "--out-dir", str(out_dir)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="p33_logic_"))
    # 1) produce the P32 479 CSV through the real P32 machinery
    pkg = tmp / "p32.package"
    rows_ = full_479()
    make_pkg(pkg, rows_)
    cat = C.build_catalog(pkg, accept_any_sha=True)
    p32csv = tmp / "p32_identifier_catalog.csv"
    C.write_csv(p32csv, cat, "0x43F3438A94EDEB2B")

    with open(p32csv, "r", encoding="utf-8", newline="") as fh:
        src_rows = list(csv.DictReader(fh))
    check("G0-p32-csv-479-rows", len(src_rows) == EXPECT, str(len(src_rows)))
    # the surrogate MUST reproduce plausible real context (some rows carry BOTH)
    check("G0-fixture-BOTH-present",
          any("BOTH" in (r.get("actor_genders") or "") for r in src_rows))
    check("G0-fixture-object-carrier",
          any(r.get("object_animation_clip_name") for r in src_rows))

    # 2) run P33 over the CSV
    out = tmp / "out"
    r = _run_p33(p32csv, out)
    rp = out / "p33_translation_context_report.txt"
    check("G1-exit-0", r.returncode == 0, "rc=%d" % r.returncode)
    rep_txt = rp.read_text(encoding="utf-8") if rp.is_file() else ""
    for key in ("SOURCE_SHA256=", "SOURCE_INSTANCE=", "P32_ROWS=",
                "CONTEXT_ROWS=", "IDENTIFIER_MATCH_COUNT=",
                "IDENTIFIER_MISMATCH_COUNT=", "SERIES_GROUP_COUNT=",
                "MULTI_ENTRY_SERIES_COUNT=", "STANDALONE_COUNT=",
                "UNTRANSLATED_COUNT=", "ORD318_CONTEXT_CHECK=",
                "FULL_CONTEXT_SAFE=", "VERDICT="):
        check("G1-rep-%s" % key.strip("="), key in rep_txt, key)
    gk = {ln.split("=")[0]: ln.split("=", 1)[1]
          for ln in rep_txt.splitlines() if "=" in ln}
    check("G1-rows-go", gk.get("P32_ROWS") == str(EXPECT)
          and gk.get("CONTEXT_ROWS") == str(EXPECT)
          and gk.get("IDENTIFIER_MATCH_COUNT") == str(EXPECT)
          and gk.get("IDENTIFIER_MISMATCH_COUNT") == "0"
          and gk.get("VERDICT") == "GO", " ".join(rep_txt.splitlines()[:6]))
    check("G1-ord318-pass", gk.get("ORD318_CONTEXT_CHECK") == "PASS")

    # 3) structural sampling: series detection is CONSERVATIVE
    jsonl = [json.loads(l) for l in
             (out / "p33_translation_context.jsonl").read_text(
                 encoding="utf-8").splitlines() if l.strip()]
    check("G2-jsonl-479", len(jsonl) == EXPECT, str(len(jsonl)))
    names40 = {r["ordinal"]: r["raw_display_name"] for r in jsonl}
    s40 = next(r for r in jsonl if r["ordinal"] == 40)
    check("G2-decimal-series-key",
          s40["series_key"] and s40["series_size"] == 4
          and s40["series_index"] == 1, s40["raw_display_name"])
    s43 = next(r for r in jsonl if r["ordinal"] == 43)
    check("G2-decimal-series-last", s43["series_index"] == 4
          and s43["series_size"] == 4, s43["raw_display_name"])
    # 44 (different stem, not trailing-numbered) must NOT be in the Gearshift set
    s44 = next(r for r in jsonl if r["ordinal"] == 44)
    check("G2-nonadjacent-excluded", s44["series_key"] != s40["series_key"],
          "%r vs %r" % (s40["series_key"], s44["series_key"]))
    # the standalone 60/61 are standalone (single)
    s60 = next(r for r in jsonl if r["ordinal"] == 60)
    s61 = next(r for r in jsonl if r["ordinal"] == 61)
    check("G2-standalone-60", not s60["series_key"] and s60["series_size"] == 1,
          "%r" % s60["series_key"])
    # 61 "Desk Job" is its own standalone (different stem); both empty-key/size-1
    check("G2-standalone-61", not s61["series_key"] and s61["series_size"] == 1,
          "%r" % s61["series_key"])
    # raw_display_name preserved byte-for-byte through P33 (never normalized)
    for r in jsonl:
        if r["ordinal"] in (40, 50, 60, ORD):
            src = next(x for x in src_rows if int(x["ordinal"]) == r["ordinal"])
            check("G2-raw-preserved-%d" % r["ordinal"],
                  r["raw_display_name"] == src["raw_display_name"])
    # identifier faithfully inherited from CSV
    for r in jsonl[:5] + [next(x for x in jsonl if x["ordinal"] == ORD)]:
        src = next(x for x in src_rows if int(x["ordinal"]) == r["ordinal"])
        check("G2-id-inherit-%d" % r["ordinal"],
              r["identifier"] == src["identifier"])
    # CSV has exactly the P33 column set in declared order
    csvp = out / "p33_translation_context.csv"
    with open(csvp, "r", encoding="utf-8", newline="") as fh:
        rd = list(csv.DictReader(fh))
    check("G3-ctx-csv-479", len(rd) == EXPECT, str(len(rd)))
    need = {"source_instance", "ordinal", "identifier", "raw_display_name",
            "author", "stage_name", "sex_category", "actor_count", "locations",
            "actor_genders", "actor_clips", "object_animation_clip_name",
            "prop_animation_clip_names", "animation_tags", "actor_tags",
            "series_key", "series_index", "series_size", "title_stem",
            "title_sequence_token", "title_suffix", "prev_raw_display_name",
            "next_raw_display_name", "translation_status", "translation_note"}
    check("G3-all-required-cols", need <= set(rd[0].keys()),
          "missing=%s" % (need - set(rd[0].keys())))
    check("G3-no-extra-cols-missing", set(rd[0].keys()) == set(P33.P33_COLUMNS))
    # every UNTRANSLATED, note empty by default
    check("G3-all-untranslated",
          all(x["translation_status"] == "UNTRANSLATED" for x in jsonl))
    check("G3-note-empty-default",
          all(x["translation_note"] == "" for x in jsonl))
    # title-structure aid never corrupts raw
    sm = next(r for r in jsonl if r["ordinal"] == 42)   # Gearshift 3 - Climax
    check("G4-suffix-token", sm["title_suffix"] == "Climax"
          and sm["raw_display_name"] == "Gearshift 3 - Climax",
          "%r %r" % (sm["title_stem"], sm["title_sequence_token"]))
    check("G4-stem-aid", sm["title_stem"] == "Gearshift"
          and sm["title_sequence_token"] == "3",
          "%r %r" % (sm["title_stem"], sm["title_sequence_token"]))
    # ordinal318 identity/prev-next intact
    o318 = next(x for x in jsonl if x["ordinal"] == ORD)
    o317 = next(x for x in jsonl if x["ordinal"] == ORD - 1)
    o319 = next(x for x in jsonl if x["ordinal"] == ORD + 1)
    check("G5-ord318-raw-id", o318["raw_display_name"] == "NOT Caught Cheating 2"
          and o318["identifier"] == C.GOLDEN_SHA1_318)
    check("G5-ord318-prevnext",
          o318["prev_raw_display_name"] == o317["raw_display_name"]
          and o318["next_raw_display_name"] == o319["raw_display_name"])

    print("")
    print("PASS_COUNT=%d  FAIL_COUNT=%d"
          % (sum(1 for _n, ok in _passes if ok),
             sum(1 for _n, ok in _passes if not ok)))
    failed = [n for n, ok in _passes if not ok]
    if failed:
        print("FAILED_NAMES=%s" % failed)
        return 1
    print("P33_TRANSLATION_CONTEXT_LOGIC=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
