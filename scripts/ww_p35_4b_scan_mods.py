#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
P35.4B -- READ-ONLY scan of a Sims4 Mods folder to find the LIVE package(s)
that actually carry WW_ANIM_XML / STBL / related TGIs (i.e. the real "P31 native
override" carrier vs our P35.1 XML override).
WHY:
  We must NOT assume the P31 package filename. The user has never deleted Mods
  files, so whatever package(s) actually affect the WW UI in-game are STILL in
  Mods. This tool walks every *.package under a Mods root and reports a per-package
  DBPF type inventory (header/index only -- reads 0x44-byte header + 32-byte x
  count index entries; never touches resource bodies), flagging any package that
  contains type 0x7DF2169C (WW_ANIM_XML) or 0x220557DA (STBL) or ANY other
  non-null resource type, so you can identify the real effective override carrier
  and, if needed, point the P35.4B diff tool at it.

  Fully read-only: opens packages 'rb', writes ONLY the report txt under output/
  (or a user-specified --outdir). No package/STBL/Mods/Saves modification.

USAGE (Windows box, python3):
  python scripts\ww_p35_4b_scan_mods.py --mods "C:\Users\...\Electronic Arts\The Sims 4\Mods" [--outdir output\p35]

OUTPUT report lines per package:
  PKG=<rel path>
     TYPE_INV=0x7DF2169C(count=N);0x220557DA(count=M);...
     WW_ANIM_XML=<yes/no>  STBL=<yes/no>
     [if WW_ANIM_XML or STBL]  (e.g. XML inst=0x... group=0x... ; STBL inst=0x... locale_highbyte=0x01=CHS)

EXIT: 0 done / 2 missing mods dir / 6 unexpected.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from ww_animation_canary_builder import read_entry_meta_raw  # noqa: E402

TYPE_XML = 0x7DF2169C
TYPE_STBL = 0x220557DA
LOCALES = {0x00: "EN", 0x01: "CHS", 0x02: "CHT", 0x15: "??"}
NONNULL_TYPES = {0x7DF2169C, 0x220557DA}  # at minimum, but we print all types


def atom_int(x):
    return int(x)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mods", required=True, help="The Sims 4 Mods root folder")
    ap.add_argument("--outdir", default=None, help="where to write report (default output/p35 in repo)")
    a = ap.parse_args(argv)
    mods = Path(a.mods)
    if not mods.is_dir():
        print("ERROR: Mods dir not found: %s (exit 2)" % mods, file=sys.stderr)
        return 2
    odir = Path(a.outdir) if a.outdir else (ROOT / "output" / "p35")
    odir.mkdir(parents=True, exist_ok=True)
    rep = odir / "P35.4B_MODS_SCAN.txt"
    L = []
    add = L.append

    add("===== P35.4B READ-ONLY Mods SCAN (WW_ANIM_XML / STBL / related TGI) =====")
    add("mods root : %s" % mods)
    add("")

    pkgs = sorted([p for p in mods.rglob("*.package") if p.is_file()],
                  key=lambda p: str(p).lower())
    add("total *.package found: %d" % len(pkgs))
    add("")

    hit_xml = []
    hit_stbl = []
    n_ok = n_err = 0
    for p in pkgs:
        rel = p.relative_to(mods)
        try:
            _mj, _mn, _hc, metas = read_entry_meta_raw(p)
        except Exception as ex:
            n_err += 1
            add("PKG=%s\n   ERROR: %s" % (rel, ex))
            continue
        n_ok += 1
        inv = {}
        xml_insts = []
        stbl_insts = []
        for m in metas:
            t = atom_int(m["type"])
            inv[t] = inv.get(t, 0) + 1
            if t == TYPE_XML:
                xml_insts.append((m["group"], m["inst"]))
            elif t == TYPE_STBL:
                stbl_insts.append((m["inst"], (m["inst"] >> 32) & 0xFF))
        has_xml = bool(xml_insts)
        has_stbl = bool(stbl_insts)
        if has_xml:
            hit_xml.append(rel)
        if has_stbl:
            hit_stbl.append(rel)
        # Only print packages that carry at least one resource (data package).
        if inv:
            inv_s = ";".join("0x%08X(count=%d)" % (t, n)
                             for t, n in sorted(inv.items()))
            add("PKG=%s" % rel)
            add("    TYPE_INV=%s" % inv_s)
            if has_xml:
                for (g, i) in xml_insts:
                    add("    WW_ANIM_XML=yes  group=0x%08X inst=0x%016X" % (g, i))
            if has_stbl:
                for (inst, loc) in stbl_insts:
                    add("    STBL=yes inst=0x%016X locale_highbyte=0x%02X(%s)"
                        % (inst, loc, LOCALES.get(loc, "?")))
            if not (has_xml or has_stbl):
                add("    (no WW_ANIM_XML / STBL; only types above)")
            add("")

    add("===== SUMMARY =====")
    add("packages parsed OK : %d   errors: %d" % (n_ok, n_err))
    add("packages containing WW_ANIM_XML (0x7DF2169C): %d" % len(hit_xml))
    for rel in hit_xml:
        add("    XML+  %s" % rel)
    add("packages containing STBL (0x220557DA): %d" % len(hit_stbl))
    for rel in hit_stbl:
        add("    STBL+ %s" % rel)
    add("")
    add("=> 'P31 native override that works' is expected to be among the XML+ / "
        "STBL+ / listed packages above (whatever its filename); compare it against "
        "the P35.1 override with scripts\\ww_p35_4b_diff.py --p31 <that> --p35 <P35.1>")
    add("ZERO_WRITE_TO_MODS=YES  ZERO_WRITE_TO_SAVES=YES  (read-only scan)")

    report = "\n".join(L)
    rep.write_text(report, encoding="utf-8")
    print(report)
    print("[report written to %s]" % rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
