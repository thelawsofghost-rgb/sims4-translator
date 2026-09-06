#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
P35.5A -- READ-ONLY: parse the real P31 native override's STBL (0x220557DA), join
its strings against the 479 WW_Nevely42_Animations XML display values, and build
the  animation_raw_display_name / text -> STBL keyHash  map.

WHY  (VerDICT A, from the P35.4B diff you ran):
  P31 and P35 WW_ANIM_XML are byte-identical; the ONLY difference is that P31
  also carries an STBL (Present in P31, absent in P35). The in-game UI therefore
  resolves display through the localized-string (STBL) route, NOT through the raw
  XML text. To reproduce P31's effect we must attach that STBL layer. Before we
  generate any STBL override, this tool maps WHICH STBL keys (and inst/locale)
  back the display strings the XML names.

WHAT IT DOES (all read-only; opens package 'rb', never writes a package):
  1. Open the P31 package, list STBL entries (read_entry_meta_raw) -> instance +
     locale high byte (0x00=EN,0x01=CHS,0x02=CHT).
  2. Read each STBL raw body (read_body_raw -> decompress_maybe) and parse v5/v4
     layout -> ordered [keyHash, text] list per STBL.
  3. Read the package's WW_ANIM_XML (== P35 bytes), extract the ordered 479
     <T n="animation_raw_display_name"> display values.
  4. JOIN:  for each XML display value D, list every (STBL inst, keyHash) whose
     text == D  -> the empirical  D -> STBL key  map (no prefix guessing).
  5. Also: per STBL entry report any XML display it matches, so unmatched STBL
     entries surface too (they may be internal/other-locale strings).

OUTPUT (only text files):
  output/p35/P35.5A_P31_STBL_DISPLAY_MAP.txt   (full readable report)
  output/p35/P35.5A_P31_STBL_DISPLAY_MAP.tsv   (machine map: display -> keyHash)

USAGE (Windows, python3; --p31 = the real TARGET+ override package you diffed):
  python scripts\ww_p35_5a_p31_stbl_map.py --p31 "<real P31 override.package>" --outdir output\p35

EXIT: 0 ok / 2 missing arg or file / 3 parse error / 6 internal.
ZERO_WRITE_TO_MODS=YES  ZERO_WRITE_TO_SAVES=YES  (read-only; writes report only)
"""
import argparse
import csv
import re
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from ww_animation_canary_builder import (  # noqa: E402
    read_entry_meta_raw, read_body_raw_masked, decompress_maybe,
)

STBL = 0x220557DA
WW = 0x7DF2169C
LOCALES = {0x00: "EN", 0x01: "CHS", 0x02: "CHT"}


def parse_stbl(blob: bytes):
    """v5/v4 STBL -> {keyHash: (text, idx)}. Signature-tolerated. Mirror of P22.parse_stbl."""
    body = decompress_maybe(blob)
    data = body
    if data[:4] != b"STBL":
        i = data.find(b"STBL")
        if i < 0:
            raise ValueError("no STBL magic")
        data = data[i:]
    ver = struct.unpack_from("<H", data, 4)[0]
    out = {}
    if ver == 5:
        n = struct.unpack_from("<Q", data, 7)[0]
        pos = 21
        for idx in range(n):
            if pos + 7 > len(data):
                break
            key = struct.unpack_from("<I", data, pos)[0]
            ln = struct.unpack_from("<H", data, pos + 5)[0]
            txt = data[pos + 7: pos + 7 + ln].decode("utf-8", errors="replace")
            out[key] = (txt, idx)
            pos += 7 + ln
    elif ver == 4:
        n = struct.unpack_from("<I", data, 6)[0]
        pos = 10
        for idx in range(n):
            if pos + 7 > len(data):
                break
            key = struct.unpack_from("<I", data, pos)[0]
            ln = struct.unpack_from("<H", data, pos + 5)[0]
            txt = data[pos + 7: pos + 7 + ln * 2].decode("utf-16-le", errors="replace")
            out[key] = (txt, idx)
            pos += 7 + ln * 2
    else:
        raise ValueError("unsupported STBL ver=%d" % ver)
    return out


def xml_display_values(text: str):
    vals = []
    for m in re.finditer(r'<T\s+n\s*=\s*"animation_raw_display_name"\s*>([^<]*)</T>', text):
        vals.append(m.group(1).strip())
    return vals


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--p31", required=True, help="real P31 native override .package (carries the STBL)")
    ap.add_argument("--outdir", default=None)
    a = ap.parse_args(argv)
    p31p = Path(a.p31)
    if not p31p.is_file():
        print("ERROR: --p31 file not found: %s (exit 2)" % p31p, file=sys.stderr)
        return 2
    odir = Path(a.outdir) if a.outdir else (ROOT / "output" / "p35")
    odir.mkdir(parents=True, exist_ok=True)
    L = []
    add = L.append

    add("===== P35.5A READ-ONLY: P31 STBL -> display map (WW_Nevely42_Animations) =====")
    add("p31 : %s" % p31p)

    try:
        _mj, _mn, _hc, metas = read_entry_meta_raw(p31p)
    except Exception as ex:
        add("  read_entry_meta_raw ERROR: %s (exit 3?)" % ex)
        print("\n".join(L)); return 3
    stbls = [m for m in metas if m["type"] == STBL]
    wws = [m for m in metas if m["type"] == WW]
    add("entries total=%d  STBL=%d  WW_ANIM_XML=%d" % (len(metas), len(stbls), len(wws)))
    if not stbls:
        add("=> NO STBL in this package -- not the P31 native carrier. exit 3")
        (odir / "P35.5A_P31_STBL_DISPLAY_MAP.txt").write_text("\n".join(L), encoding="utf-8")
        print("\n".join(L)); return 3

    # --- collect STBL key/text + xml display values ---
    stbl_rows = []          # (inst, loc, key, text)
    xml_vals_set = set()
    xml_vals_ordered = []
    for st in stbls:
        inst = st["inst"]
        loc = (inst >> 56) & 0xFF if inst is not None else None
        body = b""
        try:
            body = read_body_raw_masked(p31p, st)
            parsed = parse_stbl(body)
        except Exception as ex:
            add("  STBL inst=0x%016X locale=0x%02X parse ERROR: %s (body=%dB)"
                % (inst or 0, loc or 0, ex, len(body)))
            continue
        add("  STBL inst=0x%016X locale_highbyte=0x%02X(%s) entries=%d"
            % (inst or 0, loc or 0, LOCALES.get(loc, "?"), len(parsed)))
        for key, (txt, idx) in parsed.items():
            stbl_rows.append((inst, loc, key, txt, idx))
    for w in wws:
        try:
            body = read_body_raw_masked(p31p, w)
            raw = decompress_maybe(body)
            text = raw.decode("utf-8", errors="replace")
        except Exception as ex:
            add("  WW_ANIM_XML inst=0x%016X read ERROR: %s" % (w["inst"] or 0, ex))
            continue
        vals = xml_display_values(text)
        xml_vals_ordered = vals
        xml_vals_set = {v for v in vals if v}
        add("  WW_ANIM_XML inst=0x%016X <- %d display values parsed"
            % (w["inst"] or 0, len(vals)))
    if not xml_vals_ordered:
        add("  WARN: no WW_ANIM_XML display values found in p31 (source-WW-only package?)")

    # --- JOIN direction A: display -> STBL keys (text equality) ---
    add("")
    add("===== 1) JOIN: XML display value -> STBL key(s) (text equality) =====")
    by_text = {}
    for (inst, loc, key, txt, idx) in stbl_rows:
        by_text.setdefault(txt, []).append((inst, loc, key, idx))
    matched_display = 0
    rows_tsv = []
    disp_by_occurrence = xml_vals_ordered if xml_vals_ordered else sorted(xml_vals_set)
    for d in disp_by_occurrence:
        hits = by_text.get(d, [])
        if hits:
            matched_display += 1
            desc = ";".join("0x%016X/0x%08X(idx%d)" % (h[0] or 0, h[2], h[3]) for h in hits)
            add("  display=%r -> %s" % (d, desc))
            for (inst, loc, key, idx) in hits:
                rows_tsv.append({"display": d, "stbl_instance": "0x%016X" % (inst or 0),
                                 "locale": "0x%02X" % (loc or 0), "stbl_key_hash": "0x%08X" % key,
                                 "stbl_text": d})
    add("")
    add("matched displays (text present in P31 STBL): %d / %d"
        % (matched_display, len(disp_by_occurrence) if disp_by_occurrence else 0))

    add("")
    add("===== 2) STBL entries whose text NOT among the XML displays =====")
    stbl_text_set = {txt for (_i, _l, _k, txt, _x) in stbl_rows}
    unused = sorted({txt for txt in stbl_text_set if txt not in xml_vals_set},
                    key=lambda x: x.lower())
    for txt in unused[:80]:
        keys = by_text[txt]
        ks = ";".join("0x%016X/0x%08X" % (k[0] or 0, k[2]) for k in keys)
        add("   [unmatched STBL text=%r] %s" % (txt, ks))
    add("   (unmatched STBL text total: %d)" % len(unused))

    add("")
    add("===== 3) Inventory summary =====")
    add("STBL instances           : %d" % len(stbls))
    add("STBL key/text pairs total: %d" % len(stbl_rows))
    add("XML display values       : %d" % len(disp_by_occurrence))
    add("display -> STBL key joins: %d" % matched_display)
    add("")
    add("=> If matched_display > 0: the STBL keys above are exactly what WW's picker "
        "reads for these rows; a STBL override on those keys (same inst+locale) is the "
        "P31-equivalent fix. See P35.5A tsv.")
    add("=> If matched_display == 0 but unmatched STBL exist: WW keys off an id-derived "
        "hash whose text differs from the raw XML display; the next step is to reverse "
        "WW's hash-string namespace (read its .pyc on the Windows box), NOT to guess.")
    add("ZERO_WRITE_TO_MODS=YES  ZERO_WRITE_TO_SAVES=YES  (read-only map; writes report only)")

    report = "\n".join(L)
    (odir / "P35.5A_P31_STBL_DISPLAY_MAP.txt").write_text(report, encoding="utf-8")
    with open(odir / "P35.5A_P31_STBL_DISPLAY_MAP.tsv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["display", "stbl_instance", "locale",
                                           "stbl_key_hash", "stbl_text"])
        w.writeheader()
        for r in rows_tsv:
            w.writerow(r)
    print(report)
    print("[report ] %s" % (odir / "P35.5A_P31_STBL_DISPLAY_MAP.txt"))
    print("[tsv    ] %s" % (odir / "P35.5A_P31_STBL_DISPLAY_MAP.tsv"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
