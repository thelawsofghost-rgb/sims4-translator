#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local synthetic fixture for ww_p35_xml_alignment.py.

Builds a real DBPF package (via ww_animation_canary_builder.build_package) that
contains a single WW_ANIM_XML (inst 0x43F3438A94EDEB2B) whose animations_list
has a configurable number of <U n='anmN'> entries, each carrying
animation_raw_display_name, so that the authoritative loader
(ww_p32_identifier_catalog.load_roster) opens it and enumerates identically.

Used ONLY by the local (Linux) logic test -- never shipped to Mods, never a
real mod.  Mirrors real attribute shape for validation purposes.
"""
import importlib.util
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

WW_ANIM_XML = 0x7DF2169C
INSTANCE = 0x43F3438A94EDEB2B
GROUP = 0x80000000  # canary uses this group default for STBL-adjacent tests


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wb = _load("ww_animation_canary_builder",
           SCRIPT_DIR / "ww_animation_canary_builder.py")


def entry_xml(ordinal, raw):
    """One <U n='anmN'> under animations_list with display-name scalar."""
    n = "anm%d" % ordinal
    return ('<U n="%s"><T n="animation_raw_display_name">%s</T>'
            '<T n="animation_author">test_author</T></U>'
            % (n, _esc(raw)))


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def wwxml(rows):
    """rows: list[(ordinal, raw)]; builds WW_ANIM_XML wrapper text."""
    inner = "".join(entry_xml(o, raw) for o, raw in sorted(rows))
    return ('<?xml version="1.0"?><U n="WW"><L n="animations_list">'
            + inner + "</L></U>")


def build_pkg_fixture(out_pkg: Path, rows):
    """rows: list[(ordinal, raw)].  Write DBPF pkg with one WW_ANIM_XML."""
    xml_bytes = wwxml(rows).encode("utf-8")
    body = xml_bytes  # uncompressed, like many source XMLs
    out_pkg.parent.mkdir(parents=True, exist_ok=True)
    # bare 4-tuple -> build_package auto-detects uncompressed body (comp=False)
    wb.build_package([(WW_ANIM_XML, GROUP, INSTANCE, body)], out_pkg)
    return out_pkg


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/p35fixture.bpkg")
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    rows = []
    for i in range(n):
        if i == 3:
            raw = "NOT Caught Cheating %d - Climax" % (i + 1)  # incl. suffix & ampersand-free
        elif i == 4:
            raw = "*CUSTOM VOICES* 的定位用 ascii<&> 斜杠"
        else:
            raw = "NOT Caught Cheating %d" % (i + 1)
        rows.append((i, raw))
    build_pkg_fixture(out, rows)
    print("built %s entries=%d inst=0x%016X size=%d"
          % (out, n, INSTANCE, out.stat().st_size))


if __name__ == "__main__":
    main()
