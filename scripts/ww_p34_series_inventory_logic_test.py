#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p34_series_inventory_logic_test.py --- offline logic gate for the P34 series-
inventory generator.  The real 479-row P33 CSV is Windows-only; here we feed a
small DETERMINISTIC P33-shaped CSV (synthetic, author/stem/series fields only)
to prove the generator is machine-correct: exact output schema, fail-closed on a
missing/empty input, standalone rows excluded, size>=2 + size>=3 filters honour
series_size boundaries, members in ordinal (series_index) order, and every row
inherited verbatim from input.  This is a correctness gate, NOT a claim about the
real dataset (never surfaces synthetic counts as real).  ZERO write to Mods/saves.
"""
import csv
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ww_p34_series_inventory as INV        # noqa: E402

SELF = Path(__file__).resolve().parent / "ww_p34_series_inventory.py"
_passes = []
COUNT = 0


def check(name, cond, detail=""):
    global COUNT
    COUNT += 1
    _passes.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))
    return bool(cond)


def _ctx_line(ordinal, raw, key, size, idx, stem, tok, suf,
              cat="VAGINAL", loc="FLOOR", gend="MALE,FEMALE", author="Nevely42"):
    return {
        "source_instance": "0x43F3438A94EDEB2B", "ordinal": ordinal,
        "identifier": "id%04d" % int(ordinal), "raw_display_name": raw,
        "author": author, "stage_name": "", "sex_category": cat,
        "actor_count": "2", "locations": loc, "actor_genders": gend,
        "actor_clips": "c0,c1", "object_animation_clip_name": "",
        "prop_animation_clip_names": "", "animation_tags": "", "actor_tags": "",
        "series_key": key, "series_index": idx, "series_size": size,
        "title_stem": stem, "title_sequence_token": tok, "title_suffix": suf,
        "prev_raw_display_name": "", "next_raw_display_name": "",
        "translation_status": "UNTRANSLATED", "translation_note": "",
    }


def _write_ctx_csv(path, rows):
    cols = list(rows[0].keys())
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _run(csv_path, out_path, min_size=2):
    r = subprocess.run([sys.executable, str(SELF), csv_path, "--out", out_path,
                        "--min-size", str(min_size)],
                       capture_output=True, text=True)
    return r


def _read_out(path):
    rows = []
    with open(path, newline="") as fh:
        rdr = csv.DictReader(fh)
        cols = rdr.fieldnames
        rows = list(rdr)
    return cols, rows


def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # deterministic P33-shaped rows:
        #   S1 = real series size 3 (Gearshift-like: stem + decimal token)
        #   S2 = Roman-coded pair size 2 (Heat I -1/-2)
        #   standalone rows interleaved with '' key / size 1
        rows = [
            _ctx_line("0", "Standalone A", "", "1", "0", "", "", ""),
            _ctx_line("1", "Gearshift 1", "S1", "3", "0", "Gearshift", "1", ""),
            _ctx_line("2", "Gearshift 2", "S1", "3", "1", "Gearshift", "2", ""),
            _ctx_line("3", "Gearshift 3 - Climax", "S1", "3", "2", "Gearshift",
                      "3", "Climax"),
            _ctx_line("4", "Standalone B", "", "1", "0", "", "", ""),
            _ctx_line("5", "Heat I - 1", "S2", "2", "0", "Heat I -", "1", ""),
            _ctx_line("6", "Heat I - 2", "S2", "2", "1", "Heat I -", "2", ""),
        ]
        ctx = td / "ctx.csv"
        _write_ctx_csv(ctx, rows)

        # --- fail-closed: empty / missing required column ---
        r = _run(str(td / "missing.csv"), str(td / "o.csv"))
        check("p34-missing-input-rc2", r.returncode == 2)
        # a context file missing a required column (drop series_key)
        bad = [_ctx_line("1", "x", "S1", "2", "0", "Gearshift", "1", "")]
        bad[0].pop("series_key")
        badpath = td / "bad.csv"
        _write_ctx_csv(badpath, bad)
        r = _run(str(badpath), str(td / "o2.csv"))
        check("p34-missing-column-failclosed", r.returncode == 2)

        # --- normal: min-size 2 ---
        o2 = td / "o_full.csv"
        r = _run(str(ctx), str(o2))
        check("p34-go-rc0", r.returncode == 0)
        cols, out = _read_out(o2)
        check("p34-schema-exact",
              cols == list(INV.MEMBER_FIELDS),
              "cols=%s" % ",".join(cols))
        # S1 has 3 members, S2 has 2; standalone excluded; total 5
        keys = [x["series_key"] for x in out]
        check("p34-keys-full",
              sorted(set(keys)) == ["S1", "S2"],
              "keys=%s" % sorted(set(keys)))
        check("p34-member-count-full", len(out) == 5, "n=%d" % len(out))
        s1 = [x for x in out if x["series_key"] == "S1"]
        check("p34-s1-size-order", [x["title_sequence_token"] for x in s1] ==
              ["1", "2", "3"], "tok=%s" % [x["title_sequence_token"] for x in s1])
        check("p34-s1-ordinals", [x["member_ordinal"] for x in s1] == ["1", "2", "3"])
        # verbatim inheritance of raw/identifier carrier text on a member row
        s3 = [x for x in out if x["member_raw_display_name"] == "Gearshift 3 - Climax"]
        check("p34-s1-suffix-verbatim",
              bool(s3) and s3[0]["title_suffix"] == "Climax" and
              s3[0]["member_raw_display_name"] == "Gearshift 3 - Climax")
        check("p34-s1-climax-row", bool(s3) and s3[0]["series_size"] == "3")
        # standalone ('' key) rows never appear as members
        check("p34-no-standalone", all(x["series_key"] != "" for x in out))
        # member fields carry input verbatim (category/location/genders)
        m1 = [x for x in out if x["member_raw_display_name"] == "Gearshift 1"]
        check("p34-member-verbatim",
              bool(m1) and m1[0]["member_category"] == "VAGINAL" and
              m1[0]["member_location"] == "FLOOR" and
              m1[0]["member_actor_genders"] == "MALE,FEMALE")

        # --- min-size 3 --- (narrow the requested >=3 analysis set)
        o3 = td / "o3.csv"
        r = _run(str(ctx), str(o3), min_size=3)
        _, out3 = _read_out(o3)
        check("p34-min3-go-rc0", r.returncode == 0)
        check("p34-min3-only-s1", sorted(set(x["series_key"] for x in out3)) ==
              ["S1"], "keys=%s" % sorted(set(x["series_key"] for x in out3)))
        check("p34-min3-count", len(out3) == 3, "n=%d" % len(out3))

    failed = [n for n, ok in zip(range(1, COUNT + 1), _passes) if not ok]
    print("PASS_COUNT=%d  FAIL_COUNT=%d" % (COUNT, len(failed)))
    if failed:
        print("FAIL_INDEXES=%s" % ",".join(str(i) for i in failed))
    print("P34_SERIES_INVENTORY_LOGIC=%s" % ("PASS" if not failed else "FAIL"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
