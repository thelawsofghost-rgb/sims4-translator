#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ww_p34_adapter_fixture.py --- synthetic P33-schema fixture for OFFLINE
p34-translate-adapter logic tests only (support file, NOT a real gate).

NOT real WW data: rows are fabricated placeholders exercising the adapter's
deterministic machinery (keep / single-gloss / series-deco / prose->LLM /
Climax / location).  Real P34-BATCH refuses to run without the genuine P33 CSV
(real Nevely42 corpus); this fixture is only for machine-behavioural tests of
the deterministic resolver + CLI --dry-run path.  Fields not central to a row
are left empty; the source_instance/identifier are synthetic and never appear
in real output.

CLI: python ww_p34_adapter_fixture.py [out.csv]   (default /tmp/...)
Runs: make_rows() is also importable for in-process tests.
"""
import csv
import os
import sys

COLS = [
    "source_instance", "ordinal", "identifier", "raw_display_name", "author",
    "stage_name", "sex_category", "actor_count", "locations", "actor_genders",
    "actor_clips", "object_animation_clip_name", "prop_animation_clip_names",
    "animation_tags", "actor_tags", "series_key", "series_index", "series_size",
    "title_stem", "title_sequence_token", "title_suffix",
    "prev_raw_display_name", "next_raw_display_name",
    "translation_status", "translation_note",
]


def row(*, source_instance, ordinal, identifier, raw, sex_category="", actor_count="",
        locations="", actor_genders="", actor_clips="", series_key="", series_index="",
        series_size="1", title_stem="", title_sequence_token="", title_suffix="",
        prev="", nxt=""):
    return {
        "source_instance": source_instance, "ordinal": str(ordinal),
        "identifier": identifier, "raw_display_name": raw, "author": "Fixture",
        "stage_name": "", "sex_category": sex_category, "actor_count": str(actor_count),
        "locations": locations, "actor_genders": actor_genders, "actor_clips": actor_clips,
        "object_animation_clip_name": "", "prop_animation_clip_names": "",
        "animation_tags": "", "actor_tags": "", "series_key": series_key,
        "series_index": str(series_index), "series_size": str(series_size),
        "title_stem": title_stem, "title_sequence_token": title_sequence_token,
        "title_suffix": title_suffix, "prev_raw_display_name": prev,
        "next_raw_display_name": nxt, "translation_status": "TODO",
        "translation_note": "",
    }


def make_rows():
    rs = []
    k = 0
    def add(r):
        nonlocal k
        k += 1
        rs.append(r)
    # keep / author handle
    add(row(source_instance="0xAA", ordinal=1, identifier="keep_author",
            raw="Nevely42", series_key="", title_stem="Nevely42"))
    # single glossary
    add(row(source_instance="0xAA", ordinal=2, identifier="single_kiss",
            raw="Kiss", series_key="", title_stem="Kiss", sex_category="VAGINAL",
            locations="DOUBLE_BED", actor_count=2, actor_genders="M+F"))
    add(row(source_instance="0xAA", ordinal=3, identifier="single_orals",
            raw="Blowjob", series_key="", title_stem="Blowjob", sex_category="ORAL"))
    # location single
    add(row(source_instance="0xAA", ordinal=4, identifier="single_shower",
            raw="Shower", series_key="", title_stem="Shower", locations="SHOWER"))
    # modifier+head composite
    add(row(source_instance="0xAA", ordinal=5, identifier="comp_deeporal",
            raw="Deep Oral", series_key="", title_stem="Deep Oral",
            sex_category="ORAL", actor_count=2, actor_genders="M+F"))
    add(row(source_instance="0xAA", ordinal=6, identifier="comp_wetkiss",
            raw="Wet Kiss", series_key="", title_stem="Wet Kiss"))
    # series with glossary stem, size 4, numbered + climax suffix
    for (idx, seq, suf, prev, nxt) in [
            (1, "1", "", "", "Kiss 2"),
            (2, "2", "", "Kiss 1", "Kiss 3"),
            (3, "3", "", "Kiss 2", "Kiss 4 - Climax"),
            (4, "4", "Climax", "Kiss 3", "")]:
        raw = "Kiss %s%s" % (seq, (" - Climax" if suf else ""))
        rs.append(row(source_instance="0xAA", ordinal=10 + idx, identifier="ser_k_%s" % idx,
                      raw=raw, series_key="SER_KISS", series_index=idx, series_size=4,
                      title_stem="Kiss", title_sequence_token=seq, title_suffix=suf,
                      prev=prev, nxt=nxt, sex_category="VAGINAL",
                      locations="DOUBLE_BED", actor_count=2, actor_genders="M+F",
                      actor_clips="clipA;clipB"))
    # non-glossary prose series stem -> REVIEW/LLM
    add(row(source_instance="0xAA", ordinal=50, identifier="ser_prose1",
            raw="Caught Cheating 1", series_key="SER_CC", series_index=1, series_size=2,
            title_stem="Caught Cheating", title_sequence_token="1",
            sex_category="VAGINAL", locations="DOUBLE_BED"))
    add(row(source_instance="0xAA", ordinal=51, identifier="ser_prose2",
            raw="Caught Cheating 2", series_key="SER_CC", series_index=2, series_size=2,
            title_stem="Caught Cheating", title_sequence_token="2",
            sex_category="VAGINAL", locations="DOUBLE_BED"))
    # standalone prose -> REVIEW/LLM
    add(row(source_instance="0xAA", ordinal=60, identifier="prose1",
            raw="Neighbors Secret", series_key="", series_size="1",
            title_stem="Neighbors Secret", sex_category="VAGINAL", actor_count=2))
    add(row(source_instance="0xAA", ordinal=70, identifier="seq1",
            raw="Orgasm", series_key="", title_stem="Orgasm",
            sex_category="VAGINAL", locations="DOUBLE_BED"))
    return rs


def main(argv):
    out = argv[0] if argv else "/tmp/p34_adapter_fixture.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        for r in make_rows():
            w.writerow(r)
    print("WROTE %d rows -> %s" % (len(make_rows()), out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
