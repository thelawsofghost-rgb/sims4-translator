#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P35.4B -- regression test: diff tool must tolerate real-world package inventory
schemas where NOT every resource body is structurally XML (handles STBL /
tuning / binary CLIP-RCOL etc. entries that have no 'root'/'fields' keys), and
must always emit the 8 contract lines. This reproduces the real-crash class:
  KeyError: 'root'  (line 168) triggered when an entry (e.g. an STBL or binary
  resource) has no XML-parsed 'root'.

Builds fixtures mirroring a real native-override package (WW_ANIM_XML +
STBL + tuning => STBL/non-XML bodies present) vs a P35.1 XML-only override, runs
scripts/ww_p35_4b_diff.py, and asserts:
  - rc == 0 (no KeyError / crash)
  - the report contains all 8 contract lines
  - VERDICT line present
  - P31_HAS_STBL=True (STBL detected despite its non-XML body)

USAGE:  python scripts\ww_p35_4b_regression_test.py     (pure synthetic, ECS-ok)
EXIT:   0 pass / 1 any assertion failed.
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    sys.path.insert(0, str(SCRIPTS))
    sys.path.insert(0, str(ROOT / "src"))
    import ww_animation_canary_builder as wb
    scan = _load("reg_scan_diff", str(SCRIPTS / "ww_p35_4b_diff.py"))

    WW = 0x7DF2169C
    ST = 0x220557DA
    G = 0x80000000

    def esc(x):
        return x.replace("&", "&amp;").replace("<", "&lt;")

    def xml_body(count=8, disp="E"):
        inner = "".join(
            '<U n="a%d"><T n="animation_raw_display_name">%s%d</T></U>' % (i, disp, i)
            for i in range(count)
        )
        return ('<U n="WW"><L n="animations_list">' + inner + "</L></U>").encode()

    fails = []
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        out = td / "out"
        # P35-like: single WW_ANIM_XML
        p35 = td / "P35.1.package"
        wb.build_package([(WW, G, 0x43F3438A94EDEB2B, xml_body(disp="E"), None)], p35)
        # P31-like NATIVE: WW_ANIM_XML + STBL(CHS) + tuning  -- non-XML bodies present
        p31 = td / "native_override.package"
        stbl_inst = (1 << 32) | 0x2222  # locale high byte 0x01 => CHS
        tune = b'<U n="driver"><T n="x">1</T></U>'
        wb.build_package(
            [(WW, G, 0x9999, xml_body(disp="Z"), None),
             (ST, G, stbl_inst, b"\x00\x01DUMMYSTBL", None),
             (0x545AC2C2, G, 1, tune, None)],
            p31,
        )
        rc = scan.main(["--p31", str(p31), "--p35", str(p35), "--outdir", str(out)])
        rep = out / "P35.4B_P31_vs_P35_DIFF.txt"
        if rc != 0:
            fails.append("diff rc=%d (expected 0)" % rc)
        if not rep.is_file():
            fails.append("report not produced: %s" % rep)
        else:
            txt = rep.read_text(encoding="utf-8")
            for key in ("P31_TYPE_INVENTORY=", "P35_TYPE_INVENTORY=",
                        "P31_HAS_STBL=", "P35_HAS_STBL=", "COMMON_TGI=",
                        "XML_COMPARE=", "DIFFERENCE_SUMMARY=", "VERDICT="):
                if key not in txt:
                    fails.append("missing contract line: %s" % key)
            if "P31_HAS_STBL=True" not in txt:
                fails.append("expected P31_HAS_STBL=True (STBL detected despite non-XML body)")
            if "STBL inst=" not in txt and "locale_highbyte" not in txt:
                fails.append("expected STBL inst/locale line in diff summary")
    if fails:
        print("REGRESSION FAIL:")
        for f in fails:
            print("  - " + f)
        return 1
    print("REGRESSION PASS: no KeyError on non-XML inventory; all 8 contract lines + STBL detection present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
