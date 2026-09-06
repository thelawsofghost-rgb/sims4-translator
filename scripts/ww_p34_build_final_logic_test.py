#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p34_build_final_logic_test.py --- machine-behaviour logic test for
scripts/ww_p34_build_final.py (P34.3 override layer, final builder).

ASSERTS MACHINE BEHAVIOUR ONLY.  Rows are SYNTHETIC fixtures; they are NOT real
WickedWhims/P34 data and never claimed to be.  Proves the override mechanics:

  * read_overrides parses (identifier -> translated_name) and ignores empty /
    tombstone rows;
  * apply_overrides overrides translated_name at HIGHEST priority, NEVER touches
    identifier or raw_display_name (byte-for-byte), and reports unknown-id rows
    as warnings (not silent drops);
  * an override equal to the current text is not counted as a change;
  * writing uses UTF-8 WITH BOM (the P34.3 #1 requirement) and the encoding gate
    passes the produced final file;
  * CLI main: with a clean (BOM) input + overrides -> rc 0 and writes final that
    the encoding gate PASSes; with a polluted (no-BOM or GBK-mojibake) input the
    gate REFUSES to write (fail-closed) -> rc 2.
"""
from __future__ import print_function
import ast
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import ww_p34_build_final as BF       # noqa: E402
import ww_p34_encoding_gate as EG     # noqa: E402

_PASS = []


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)
    _PASS.append(msg)


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix="p34bf_"), name)


def _map_rows(specs):
    rows = []
    for o, i, raw, zh in specs:
        rows.append(dict(source_instance="0xAA", ordinal=str(o), identifier=i,
                         raw_display_name=raw, translated_name=zh,
                         status="TRANSLATED", note=""))
    return rows


def _map_csv(path, rows, bom=True):
    enc = "utf-8-sig" if bom else "utf-8"
    with open(path, "w", encoding=enc, newline="") as fh:
        import csv
        w = csv.DictWriter(fh, fieldnames=BF.MAP_COLS, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _write_overrides_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        import csv
        w = csv.DictWriter(fh, fieldnames=["identifier", "translated_name",
                                           "reason", "note"], lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_py37():
    for f in ("ww_p34_build_final.py", "ww_p34_build_final_logic_test.py"):
        src = open(os.path.join(_HERE, f), encoding="utf-8").read()
        ast.parse(src, feature_version=(3, 7))
        _assert(True, "py37 parse PASS %s" % f)


def test_read_overrides():
    p = _tmp("ov.csv")
    _write_overrides_csv(p, [
        {"identifier": "abc123", "translated_name": "没被抓", "reason": "S00317", "note": ""},
        {"identifier": "", "translated_name": "ignored-no-id", "reason": "", "note": ""},
        {"identifier": "tomb", "translated_name": "", "reason": "disabled", "note": ""},
    ])
    ov, scanned = BF.read_overrides(p)
    _assert(scanned == 3, "scanned 3 override rows")
    _assert(ov == {"abc123": "没被抓"}, "empty-id and empty-text rows ignored")


def test_apply_override_highest_priority_bytes():
    rows = _map_rows([
        (317, "m317", "NOT Caught Cheating 1", "未被捉奸"),
        (318, "m318", "NOT Caught Cheating 2", "没被发现"),
    ])
    ov = {"m317": "没被抓 1", "m318": "没被抓 2"}
    out, applied, warnings = BF.apply_overrides(rows, ov)
    _assert(len(applied) == 2, "2 overrides applied")
    _assert(out[0]["translated_name"] == "没被抓 1" and
            out[1]["translated_name"] == "没被抓 2", "override wins on zh")
    _assert(out[0]["identifier"] == "m317" and out[1]["identifier"] == "m318",
            "identifier untouched")
    _assert(out[0]["raw_display_name"] == "NOT Caught Cheating 1" and
            out[1]["raw_display_name"] == "NOT Caught Cheating 2",
            "raw_display_name untouched (byte-for-byte)")
    _assert(warnings == [], "no unknown-id warnings here")


def test_apply_override_reports_unknown_id():
    rows = _map_rows([(1, "known", "X 1", "甲")])
    ov = {"known": "乙", "ghost": "幽灵"}
    out, applied, warnings = BF.apply_overrides(rows, ov)
    _assert(applied[0][0] == "known", "known applied")
    _assert(any("ghost" in w for w in warnings), "unknown id reported as warning")


def test_apply_equal_override_not_counted():
    rows = _map_rows([(1, "a", "X 1", "接吻 1")])
    out, applied, _ = BF.apply_overrides(rows, {"a": "接吻 1"})
    _assert(applied == [], "same-text override -> not a change, row preserved")


def test_write_final_has_bom_and_passes_gate():
    p = _tmp("final.csv")
    rows = _map_rows([(9, "gh3", "Giving Her Pleasure 3 - AfterSex",
                       "予她愉悦 3 - 后戏")])
    BF.write_final(p, rows)
    with open(p, "rb") as fh:
        head = fh.read(3)
    _assert(head == EG.BOM, "final file starts with UTF-8 BOM")
    st, probs, _ = EG.audit_file(p)
    _assert(st == "PASS", "final file passes the ENCODING gate (st=%s probs=%s)"
            % (st, probs))


def test_cli_writes_final_on_clean_input():
    inp = _tmp("normalized2.csv")
    ov = _tmp("overrides.csv")
    out = _tmp("final.csv")
    _map_csv(inp, _map_rows([
        (317, "m317", "NOT Caught Cheating 1", "未被捉奸"),
        (318, "m318", "NOT Caught Cheating 2", "没被发现"),
    ]), bom=True)
    _write_overrides_csv(ov, [
        {"identifier": "m317", "translated_name": "没被抓 1", "reason": "S00317", "note": ""},
        {"identifier": "m318", "translated_name": "没被抓 2", "reason": "S00317", "note": ""},
    ])
    rc = BF.main([inp, "--overrides", ov, "--out", out])
    _assert(rc == 0, "clean BOM input + overrides -> rc 0 (rc=%d)" % rc)
    _assert(os.path.isfile(out), "final written")
    with open(out, "rb") as fh:
        _assert(fh.read(3) == EG.BOM, "final has BOM")
    st, _, _ = EG.audit_file(out)
    _assert(st == "PASS", "final passes encoding gate")
    import csv as _c
    with open(out, encoding="utf-8-sig") as fh:
        got = {r["identifier"]: r["translated_name"] for r in _c.DictReader(fh)}
    _assert(got["m317"] == "没被抓 1" and got["m318"] == "没被抓 2",
            "override reflected in final mapping file")


def test_cli_refuses_missing_bom_input():
    inp = _tmp("nobom.csv")
    ov = _tmp("overrides.csv")
    out = _tmp("final.csv")
    _map_csv(inp, _map_rows([(1, "a", "X 1", "甲")]), bom=False)
    _write_overrides_csv(ov, [])
    rc = BF.main([inp, "--overrides", ov, "--out", out])
    _assert(rc == 2, "no-BOM input -> encoding gate refusal (rc=2), got %d" % rc)
    _assert(not os.path.exists(out), "refused to write final on polluted input")


def test_cli_refuses_mojibake_input():
    inp = _tmp("moji.csv")
    ov = _tmp("overrides.csv")
    out = _tmp("final.csv")
    _write_overrides_csv(ov, [])          # ensure override file exists (empty)
    # build a BOM'd file whose body includes GBK-mangle (valid UTF-8 but garbled)
    body = "translated_name\n".encode("utf-8") + \
        ("接吻 %s\n" % "没有".encode("utf-8").decode("gbk")).encode("utf-8")
    with open(inp, "wb") as fh:
        fh.write(EG.BOM + body)
    rc = BF.main([inp, "--overrides", ov, "--out", out])
    _assert(rc == 2, "GBK-mojibake input -> refusal (rc=2), got %d" % rc)
    _assert(not os.path.exists(out), "no final written on mojibake input")


def main():
    test_py37()
    test_read_overrides()
    test_apply_override_highest_priority_bytes()
    test_apply_override_reports_unknown_id()
    test_apply_equal_override_not_counted()
    test_write_final_has_bom_and_passes_gate()
    test_cli_writes_final_on_clean_input()
    test_cli_refuses_missing_bom_input()
    test_cli_refuses_mojibake_input()
    print("CHECKS_PASSED=%d/%d" % (len(_PASS), len(_PASS)))
    print("=" * 60)


if __name__ == "__main__":
    main()
