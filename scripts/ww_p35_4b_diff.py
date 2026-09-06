#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P35.4B -- static structural diff: "P31 NATIVE override" vs "P35.1 XML override".

WHY:
  P35's WW_ANIM_XML loose override (same TGI group/instance/type + 479 middle
  <T n="animation_raw_display_name"> values, incl. a 3-marker ZZZ_TEST canary) has
  confirmed NO effect on the WW UI, while a P31 "NATIVE override" package DOES.
  This tool does a READ-ONLY structural diff of the two real packages to expose
  what differs -- TGI, resource inventory, XML body shape, edited-T location,
  flags/wrapper/header, and any extra fields (locale / instance link / sibling
  name field / STBL linkage) that explains why P31 reaches the UI and P35 does not.

  It does NOT modify, translate, or emit STBL. It writes ONLY a report text file
  next to each input's directory (output/p35/p35_4b_diff_report_*.txt) or adds a
  '-report' file per the two given paths; never touches Mods / Saves / packages.

USAGE (Windows box, python3):
  python scripts\ww_p35_4b_diff.py \
     --p31 "<...P31 NATIVE OVERRIDE.package>" \
     --p35 "<...P35.1_ww_anim_displayname_override.package>" \
     [--outdir "<...>"]
  -> prints a structured diff and writes P35.4B_P31_vs_P35_DIFF.txt

EXIT: 0 diff produced / 2 missing arg or file / 3 parse error / 6 internal.
"""
import argparse
import sys
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import ww_animation_canary_builder as wb  # noqa: E402
from ww_animation_canary_builder import read_entry_meta_raw, read_body_raw, \
    decompress_maybe  # noqa: E402

TYPE_XML = 0x7DF2169C
STBL = 0x220557DA
KNOWN = {
    0x7DF2169C: "WW_ANIM_XML",
    0x220557DA: "STBL",
    0x545AC2C2: "tuning",
    0x0333406C: "xml",
    0x6B20C4F3: "clip",
    0xBC4A5044: "anim_rcol",
}
LOCALES = {0x00: "EN", 0x01: "CHS", 0x02: "CHT", 0x15: "??"}


def tag(el):
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else el.tag


def name(el):
    return el.attrib.get("n", "")


def atom_int(x):
    return int(x, 16) if isinstance(x, str) else int(x)


def fmt_type(t):
    return "0x%08X%s" % (atom_int(t), "/%s" % KNOWN.get(atom_int(t), "?")
                         if atom_int(t) in KNOWN else "")


def collect_index(pkg: Path, label):
    """Return list of dicts describing each resource + body digest."""
    major, minor, hdr_comp, meta = read_entry_meta_raw(pkg)
    idx, err = wb.safe_parse(str(pkg))
    if err:
        return None, (major, minor, hdr_comp, meta), "safe_parse_err: " + err
    out = []
    for e, m in zip(idx.entries, meta):
        t = atom_int(e.type_id)
        g = atom_int(e.group_id)
        i = atom_int(e.instance_id) if isinstance(e.instance_id, int) else None
        body = read_body_raw(pkg, e)
        plain = decompress_maybe(body)
        rec = {
            "type": t, "group": g, "inst": i, "order": len(out),
            "meta": m,
            "body_len": len(body), "plain_len": len(plain),
            "body_head": body[:16].hex() if body else "",
        }
        # XML-ish parse for structural fields
        if body and plain[:1] in (b"<", b"\xef", b"\xff", b"\xfe") or \
                plain.lstrip()[:1] == b"<":
            try:
                root = ET.fromstring(plain)
                rec["root"] = tag(root)
                rec["root_name"] = name(root)
                names = {}
                for el in root.iter():
                    if el.text and el.text.strip():
                        names.setdefault(tag(el), []).append(name(el))
                rec["node_types"] = {k: len(v) for k, v in names.items()}
                # leading text-bearing T fields
                tvals = []
                for el in root.iter():
                    if tag(el) == "T":
                        tvals.append(name(el))
                rec["T_field_names"] = tvals
                # localized-string / extra fields presence
                rec["fields"] = sorted({name(el) for el in root.iter() if name(el)})
            except Exception as ex:
                rec["xml_parse_err"] = str(ex)
        out.append(rec)
    return out, (major, minor, hdr_comp, meta), None


def describe_fields(rec):
    fs = rec.get("fields", [])
    interesting = [f for f in fs if any(k in f.lower() for k in
                   ("display", "localiz", "name", "str", "hash", "text", "key"))]
    return interesting or fs[:20]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--p31", required=True, help="P31 NATIVE OVERRIDE .package")
    ap.add_argument("--p35", required=True, help="P35.1 override .package")
    ap.add_argument("--outdir", default=None)
    a = ap.parse_args(argv)
    p31 = Path(a.p31); p35 = Path(a.p35)
    for p in (p31, p35):
        if not p.is_file():
            print("ERROR: not found: %s (exit 2)" % p, file=sys.stderr)
            return 2
    odir = Path(a.outdir) if a.outdir else (Path(__file__).resolve().parents[1] / "output" / "p35")
    odir.mkdir(parents=True, exist_ok=True)
    rep = odir / "P35.4B_P31_vs_P35_DIFF.txt"
    L = []
    add = L.append

    add("===== P35.4B STATIC DIFF : P31 NATIVE vs P35.1 XML override =====")
    add("P31 : %s" % p31)
    add("P35 : %s" % p35)

    results = {}
    for path, label in ((p31, "P31"), (p35, "P35")):
        recs, hdr, err = collect_index(path, label)
        add("")
        add("############ %s  %s ############" % (label, path.name))
        if err:
            add("  INDEX_ERROR: %s" % err)
            results[label] = ("err", err)
            continue
        maj, mino, hcomp, meta = hdr
        add("  header  : major=%d minor=%d header_comp=%d entries=%d"
            % (maj, mino, hcomp, len(recs)))
        for r in recs:
            m = r["meta"]
            add("  [%02d] type=%-28s group=0x%08X inst=%s"
                % (r["order"], fmt_type(r["type"]), r["group"],
                   "0x%016X" % r["inst"] if r["inst"] is not None else "None"))
            add("        offset_raw=0x%X size_raw=0x%X mem=%d comp_type=0x%X "
                "off_comp=%s size_comp=%s body=%dB plain=%dB"
                % (m.get("offset_raw", 0), m.get("size_raw", 0),
                   m.get("mem_size", 0), m.get("comp_type", 0),
                   m.get("offset_comp"), m.get("size_comp"),
                   r["body_len"], r["plain_len"]))
            if r["root"]:
                add("        root=<%s n=%r> node_types=%s"
                    % (r["root"], r["root_name"], r["node_types"]))
            if r.get("T_field_names"):
                add("        T-fields(%d): %s" % (len(r["T_field_names"]),
                    r["T_field_names"][:25]))
            if r["fields"]:
                add("        distinct-names(%d): %s" % (len(r["fields"]),
                    describe_fields(r)[:30]))
        results[label] = ("ok", recs)

    add("")
    add("############ DIFF SUMMARY ############")
    if results["P31"][0] == "ok" and results["P35"][0] == "ok":
        r31 = results["P31"][1]; r35 = results["P35"][1]
        add("resource counts: P31=%d  P35=%d" % (len(r31), len(r35)))
        def tk(r): return (r["type"], r["group"], r["inst"])
        s31 = {tk(x) for x in r31}; s35 = {tk(x) for x in r35}
        add("TGI-only-in-P31 : %s" % (["0x%08X/0x%08X/0x%016X" % t for t in sorted(s31 - s35)] or "(none)"))
        add("TGI-only-in-P35 : %s" % (["0x%08X/0x%08X/0x%016X" % t for t in sorted(s35 - s31)] or "(none)"))
        add("TGI-in-both     : %s" % (["0x%08X/0x%08X/0x%016X" % t for t in sorted(s31 & s35)] or "(none)"))
        # type inventory
        def inv(rs):
            d = {}
            for r in rs:
                d.setdefault(r["type"], 0)
                d[r["type"]] += 1
            return {fmt_type(t): n for t, n in sorted(d.items())}
        add("P31 type-inventory : %s" % inv(r31))
        add("P35 type-inventory : %s" % inv(r35))
        # locale detection on STBL-bearing types (high byte of instance = locale)
        for lab, rs in (("P31", r31), ("P35", r35)):
            for r in rs:
                if r["type"] == STBL and r["inst"] is not None:
                    loc = (r["inst"] >> 32) & 0xFF
                    add("%s STBL inst=0x%016X  locale_highbyte=0x%02X(%s)"
                        % (lab, r["inst"], loc, LOCALES.get(loc, "?")))
        # any resource whose instance differs only by locale/flag linkage
    else:
        add("(one side failed to parse; see per-side section)")

    add("")
    add("ZERO_WRITE_TO_MODS=YES  ZERO_WRITE_TO_SAVES=YES  (read-only diff; "
        "no package/STBL written)")

    report = "\n".join(L)
    rep.write_text(report, encoding="utf-8")
    print(report)
    print("\n[report written to %s]" % rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
