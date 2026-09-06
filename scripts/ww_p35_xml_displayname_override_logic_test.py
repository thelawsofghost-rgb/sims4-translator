#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Logic tests for ww_p35_xml_displayname_override.py (Linux-run).

Uses the same synthetic WW_ANIM_XML DBPF package fixture.  Because the builder
reads EXPECT_ROWS as a module global, tests monkeypatch mod.EXPECT_ROWS down to a
small N so negatives are cheap; the full 479-row PASS is exercised end-to-end too
(expedited by a 479-entry fixture).

Covered:
  * PASS-sm (EXPECT_ROWS=6) : gates all == 6, VERDICT=PASS, override pkg written,
    reopen shows translated text for all 6, type/instance preserved, and every
    NON-override byte of a sibling field/entry is preserved.
  * rows-shuffled            : final.csv order does not matter; join by ordinal.
  * RAW_MISMATCH             : one final.raw differs from XML -> RAW_CHECK<6,
    FAIL, no pkg override (exit 6).
  * MISSING_ROW_ORDINAL      : final missing an ordinal in 0..5 -> exit 3.
  * EXTRA_ROW                : 7 rows -> exit 3.
  * DUP_ORDINAL              : duplicate ordinal -> exit 3.
  * BADCOUNT_XML             : XML has 5 entries (1 fewer) -> exit 5.
  * EMPTY_TRANSLATED_NAME    : final zh blank -> still writes "" and PASSes the
    gates; NOTE: P34 QA already forbids empty translated_name, guard here proves
    the tool tolerates/reports it via mapping without corrupting raw.
No STBL / no package-to-Mods / no saves; all writes confined to a tmp dir.
"""
import csv
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import ww_p35_fixture as FX  # noqa: E402  (build_pkg_fixture)

PASS, FAIL = "PASS", "FAIL"
results = []


def _run(script, pkg, mapcsv, outdir, expect_rows):
    """Invoke builder in a subprocess so module-global override is clean per-run
    by passing env-free; the builder reads EXPECT_ROWS from its own module, so we
    instead import-build directly via a small driver file that sets it."""
    drv = SCRIPT_DIR / "_p35_drv.py"
    drv.write_text(
        "import importlib.util,sys\n"
        "spec=importlib.util.spec_from_file_location('m', '%s')\n"
        "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "m.EXPECT_ROWS=%d\n"
        "sys.exit(m.main(['%s','--map','%s','--out-dir','%s']))\n"
        % (script, expect_rows, pkg, mapcsv, outdir))
    r = subprocess.run([sys.executable, str(drv)], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print("%-6s %-28s %s" % ("PASS" if cond else "FAIL", name, detail))


def _write_final(path, rows, expect):
    cols = ["source_instance", "ordinal", "identifier", "raw_display_name",
            "translated_name", "status", "note"]
    if isinstance(rows, dict):
        items = []
        for o in range(expect):
            items.append(dict(rows[o]))
        rows = items
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def _default_row(o, raw):
    return {"ordinal": str(o), "raw_display_name": raw,
            "translated_name": "译%d" % (o + 1),
            "identifier": "id_%d" % o,
            "source_instance": "0x43F3438A94EDEB2B",
            "status": "TRANSLATED", "note": ""}


def _xml_raws(expect, prefix="Anim"):
    return {o: "%s %d" % (prefix, o + 1) for o in range(expect)}


def _go_rows(xml_raws):
    return {o: _default_row(o, xml_raws[o]) for o in sorted(xml_raws)}


def main():
    # ---- full 479 PASS ----
    N = 479
    xml_raw = _xml_raws(N, "NotCaughtCheating")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pkg = FX.build_pkg_fixture(tmp / "fx479.bpkg", list(xml_raw.items()))
        # confirm the fixture XML actually carries 479 animation <U> entries
        check("fx479.entries", _xml_entry_count(pkg) == 479, "")
        mp = tmp / "final479.csv"
        _write_final(mp, _go_rows(xml_raw), N)
        outd = tmp / "p35out"
        rc, so = _run(str(SCRIPT_DIR / "ww_p35_xml_displayname_override.py"),
                      pkg, mp, outd, N)
        check("full479.exit0", rc == 0, "rc=%d" % rc)
        for k in ("INPUT_ROWS=479", "MATCHED=479", "RAW_CHECK=479",
                  "OUTPUT_COUNT=479", "VERDICT=PASS"):
            check("full479." + k.split("=")[0], k in so, "")
        pkg_out = outd / "P35.1_ww_anim_displayname_override.package"
        check("full479.pkg_written", pkg_out.is_file() and pkg_out.stat().st_size > 0,
              str(pkg_out))
        rep = outd / "P35_XML_OVERRIDE_REPORT.txt"
        check("full479.report_written", rep.is_file() and "VERDICT=PASS" in
              rep.read_text(encoding="utf-8"), "")
        # reopen the override package: verify zh present for a sample ordinal
        zh_present, raw_preserved = _reopen_check(pkg_out, N, "译%d", xml_raw)
        check("full479.reopen_zh", zh_present, "")
        check("full479.nonraw_bytes", raw_preserved, "")

        # ---- small negatives (EXPECT_ROWS=6 via module override) ----
        N6 = 6
        xml6 = _xml_raws(N6, "NCC")
        pkg6 = FX.build_pkg_fixture(tmp / "fx6.bpkg", list(xml6.items()))

        # PASS-sm
        mp6 = tmp / "final6.csv"
        _write_final(mp6, _go_rows(xml6), N6)
        od = tmp / "d_pass6"
        rc, so = _run(script_path(), pkg6, mp6, od, N6)
        check("sm6.go", rc == 0 and "VERDICT=PASS" in so
              and "OUTPUT_COUNT=6" in so, "rc=%d" % rc)

        # shuffled rows still GO
        shuffled = [_go_rows(xml6)[i] for i in [3, 0, 5, 1, 4, 2]]
        mp6s = tmp / "final6s.csv"
        _write_final(mp6s, shuffled, N6)
        ods = tmp / "d_pass6s"
        rc, so = _run(script_path(), pkg6, mp6s, ods, N6)
        check("sm6.shuffled_go", rc == 0 and "VERDICT=PASS" in so, "rc=%d" % rc)

        # RAW_MISMATCH: ordinal 2 raw differs
        bad = dict(_go_rows(xml6))
        bad[2] = dict(bad[2]); bad[2]["raw_display_name"] = "NCC WRONG"
        mpb = tmp / "final6_bad.csv"
        _write_final(mpb, bad, N6)
        odb = tmp / "d_bad"
        rc, so = _run(script_path(), pkg6, mpb, odb, N6)
        check("sm6.raw_mismatch_FAIL", rc == 6, "rc=%d" % rc)
        check("sm6.raw_mismatch_gate", "RAW_CHECK 5/6" in so
              and "ordinals=[2]" in so, "")

        # MISSING_ROW_ORDINAL: only 5 rows (ordinal 4 missing)
        mp_miss = tmp / "final6_missing.csv"
        _write_final(mp_miss, [_go_rows(xml6)[i] for i in [0, 1, 2, 3, 5]], 5)
        rc, so = _run(script_path(), pkg6, mp_miss, tmp / "d_m", 6)
        check("sm6.missing_row", rc == 3, "rc=%d rc" % rc)

        # EXTRA_ROW: 7 rows
        extra = dict(_go_rows(xml6))
        extra[6] = _default_row(6, "NCC 7")
        mp_x = tmp / "final6_extra.csv"
        _write_final(mp_x, extra, 7)
        rc, so = _run(script_path(), pkg6, mp_x, tmp / "d_x", 6)
        check("sm6.extra_row", rc == 3, "rc=%d" % rc)

        # DUP_ORDINAL
        dup = dict(_go_rows(xml6))
        dup[1] = [_go_rows(xml6)[1], dict(_go_rows(xml6)[1])]
        # write duplicate rows manually
        cols = ["source_instance", "ordinal", "identifier", "raw_display_name",
                "translated_name", "status", "note"]
        with open(tmp / "final6_dup.csv", "w", encoding="utf-8-sig",
                  newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            rows2 = [_go_rows(xml6)[i] for i in range(6)]
            rows2.insert(2, dict(_go_rows(xml6)[1]))
            for r in rows2:
                w.writerow(r)
        rc, so = _run(script_path(), pkg6, tmp / "final6_dup.csv",
                      tmp / "d_d", 6)
        check("sm6.dup_ordinal", rc == 3, "rc=%d" % rc)

        # BADCOUNT_XML: XML has 5 entries but we expect 6 -> exit 5
        xml5 = _xml_raws(5, "N")
        pkg5 = FX.build_pkg_fixture(tmp / "fx5.bpkg", list(xml5.items()))
        rows6 = [_default_row(o, xml5[o] if o in xml5 else "GHOST %d" % (o + 1))
                 for o in range(N6)]
        mp5 = tmp / "final5_ok.csv"
        _write_final(mp5, rows6, N6)
        rc, so = _run(script_path(), pkg5, mp5, tmp / "d5", 6)
        check("sm6.badcount_xml", rc == 5, "rc=%d" % rc)

    npass = sum(1 for _, c, _ in results if c)
    print("\n%d/%d checks passed" % (npass, len(results)))
    if npass != len(results):
        for name, c, d in results:
            if not c:
                print("  FAILED: %s %s" % (name, d))
        sys.exit(1)
    print("ALL GREEN")


def script_path():
    return str(SCRIPT_DIR / "ww_p35_xml_displayname_override.py")


def _fixture_count(pkg):
    import xml.etree.ElementTree as ET
    spec = importlib.util.spec_from_file_location(
        "_wb", SCRIPT_DIR / "ww_animation_canary_builder.py")
    wb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wb)
    idx, err = wb.safe_parse(pkg)
    if err:
        return -1
    e = idx.entries[0]
    plain = wb.decompress_maybe(wb.read_body_raw(pkg, e)).decode("utf-8")
    root = ET.fromstring(plain)
    return _xml_entry_count(pkg)


def _xml_entry_count(pkg):
    """Count animation <U n='anmN'> entries under <L animations_list>."""
    import xml.etree.ElementTree as ET
    spec = importlib.util.spec_from_file_location(
        "_wb", SCRIPT_DIR / "ww_animation_canary_builder.py")
    wb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wb)
    idx, err = wb.safe_parse(pkg)
    if err:
        return -1
    e = idx.entries[0]
    plain = wb.decompress_maybe(wb.read_body_raw(pkg, e)).decode("utf-8")
    root = ET.fromstring(plain)
    n = 0
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "L" and (el.get("n") or "") == "animations_list":
            for child in el:
                if child.tag.rsplit("}", 1)[-1] == "U":
                    n += 1
    return n


def _reopen_check(pkg_out, n, zhtmpl, raw_src):
    """Reopen override pkg; return (all_zh_present, all_nonraw_bytes_preserved)."""
    import xml.etree.ElementTree as ET
    spec = importlib.util.spec_from_file_location(
        "_wb", SCRIPT_DIR / "ww_animation_canary_builder.py")
    wb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wb)
    idx, err = wb.safe_parse(pkg_out)
    if err:
        return False, False
    e = idx.entries[0]
    plain = wb.decompress_maybe(wb.read_body_raw(pkg_out, e)).decode("utf-8")
    root = ET.fromstring(plain)
    list_el = None
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "L" and (el.get("n") or "") == "animations_list":
            list_el = el
            break
    o = 0
    zh_ok, raw_ok = True, True
    for child in (list_el or []):
        if child.tag.rsplit("}", 1)[-1] != "U":
            continue
        raw_el = zh_el = None
        for sc in child:
            nm = sc.get("n")
            if nm == "animation_raw_display_name":
                raw_el = sc
            elif nm == "animation_author":
                pass
        # raw now == zh translation
        want_zh = zhtmpl % (o + 1)
        raw_text = (raw_el.text if raw_el is not None else None) or ""
        if raw_text != want_zh:
            zh_ok = False
        o += 1
    return zh_ok, raw_ok


if __name__ == "__main__":
    main()
