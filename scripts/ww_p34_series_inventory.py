#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p34_series_inventory.py --- P34: series inventory over the P33 context CSV.

This is a RULE/READ phase, NOT translation.  It reads a real P33 context CSV
(output/p33/p33_translation_context.csv, produced by the real P32/P33 gate run)
and emits the reviewable series inventory that gates P34-BATCH human/LLM work.

It inherits every field VERBATIM from P33 -- it never recomputes identifier,
raw_display_name, series_key/index/size, title_stem/token/suffix, sex_category,
locations, actor_genders, or author.  The only thing this script decides is
*which* rows to surface as members of *multi-entry* series and in what order.

Why size>=2 (not only >=3): the operator's analysis target is size>=3, but the
inventory file also lists every series so a human can see the full landscape and
confirm the >=3 cutoff is the right batch boundary.  Filtering flags:
  --min-size N   only series with series_size >= N (default 2).
So `--min-size 3` narrows to the requested >=3 analysis set; the default file
keeps the complete series map.

CSV shape (one row per series MEMBER, machine-consumable):
  series_key series_size          -- on every member row of that series
  title_stem title_sequence_token title_suffix  -- series-level aid, on every row
  member_ordinal member_raw_display_name member_category member_location
  member_actor_genders
Members are in source ordinal order (series_index order), which P33 guarantees.

Output columns (exact, header per spec):
  series_key,series_size,title_stem,title_sequence_token,title_suffix,
  member_ordinal,member_raw_display_name,member_category,member_location,
  member_actor_genders

Read-only discipline: ZERO_WRITE_TO_MODS=YES, ZERO_WRITE_TO_SAVES=YES.  Writes
only the single CSV under --out (default output/p34_series_inventory.csv).  Never
ties to synthetic fixtures; real rows require the real P33 CSV (Windows-only).
Exit 0 success; 2 input missing/gate fail; never fabricates series.
"""
from __future__ import print_function
import argparse
import csv
import os
import sys

MEMBER_FIELDS = (
    "series_key", "series_size",
    "title_stem", "title_sequence_token", "title_suffix",
    "member_ordinal", "member_raw_display_name", "member_category",
    "member_location", "member_actor_genders",
)


def _read_ctx(path):
    """Read a P33 context CSV.  Returns (rows, columns).  Fails closed on any
    required column missing / 0 data rows so a bad/mismatched input can never
    silently yield an empty or misaligned inventory."""
    req = {
        "series_key", "series_size", "title_stem", "title_sequence_token",
        "title_suffix", "ordinal", "raw_display_name", "sex_category",
        "locations", "actor_genders",
    }
    rows = []
    with open(path, newline="") as fh:
        r = csv.DictReader(fh)
        rows = list(r)
    if not rows:
        raise _Fail("input CSV has no data rows -> refusing (fail-closed)")
    col = set(rows[0].keys())
    missing = req - col
    if missing:
        raise _Fail("input CSV %s missing required columns %s -> refusing"
                    % (path, ",".join(sorted(missing))))
    return rows


class _Fail(Exception):
    """Fail-closed signal with a reason; main() maps it to exit code 2."""


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _no_key(x):
    """A standalone P33 row has series_key == '' / '-' and series_size == 1.
    Normalise to '' so the inventory only ever reports real multi-entry groups
    with a usable key, mirroring P33's standalone convention."""
    x = (x or "").strip()
    if x in ("", "-", "None", "S"):
        return ""
    return x


def build_inventory(rows, min_size):
    """Rows are in ordinal/source order already.  Yield grouped member rows for
    every series whose series_size >= min_size, keyed by P33 series_key.  Preserve
    P33's series_index (member order within group)."""
    # group by the series_key the far more careful P33 build assigned
    groups = {}
    order = []
    for row in rows:
        key = _no_key(row.get("series_key", ""))
        if not key:
            continue  # standalone rows are not inventory members of a series proper
        size = _int(row.get("series_size"))
        if key not in groups:
            groups[key] = {"size": size, "members": []}
            order.append(key)
        groups[key]["members"].append(row)
    lines = []
    for key in order:
        g = groups[key]
        if g["size"] is None or g["size"] < min_size:
            continue
        # members carry THEIR OWN per-row title_aid and context (P33 already wrote
        # per-member title_stem/token/suffix on each row), so nothing is invented
        # nor borrowed from a group sibling.
        members = sorted(g["members"], key=lambda r: (_int(r.get("ordinal")) or 0,
                                                      _int(r.get("series_index")) or 0))
        for m in members:
            lines.append({
                "series_key": key,
                "series_size": g["size"],
                "title_stem": m.get("title_stem", ""),
                "title_sequence_token": m.get("title_sequence_token", ""),
                "title_suffix": m.get("title_suffix", ""),
                "member_ordinal": m.get("ordinal", ""),
                "member_raw_display_name": m.get("raw_display_name", ""),
                "member_category": m.get("sex_category", ""),
                "member_location": m.get("locations", ""),
                "member_actor_genders": m.get("actor_genders", ""),
            })
    return lines


def write_csv(path, lines):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=MEMBER_FIELDS,
                           extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for ln in lines:
            w.writerow(ln)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", help="P33 context CSV (input)")
    ap.add_argument("--out", default="output/p34_series_inventory.csv")
    ap.add_argument("--min-size", type=int, default=2,
                    help="only series with series_size >= N (batch review cutoff)")
    a = ap.parse_args(argv)
    if not os.path.isfile(a.csv):
        print("VERDICT=FAIL", file=sys.stderr)
        print("REASON=INPUT_MISSING:%s" % a.csv, file=sys.stderr)
        return 2
    try:
        rows = _read_ctx(a.csv)
    except _Fail as e:
        print("VERDICT=FAIL", file=sys.stderr)
        print("REASON=%s" % e, file=sys.stderr)
        return 2
    lines = build_inventory(rows, a.min_size)
    write_csv(a.out, lines)
    # stdout = gate/status only, keep it small (never dump the inventory)
    series = {}
    for ln in lines:
        series.setdefault(ln["series_key"], ln["series_size"])
    n_series = len(series)
    n_members = len(lines)
    print("VERDICT=GO")
    print("SOURCE_ROWS=%d" % len(rows))
    print("INVENTORY_SERIES(n>=%d)=%d" % (a.min_size, n_series))
    print("INVENTORY_MEMBER_ROWS=%d" % n_members)
    print("OUT=%s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
