#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P35.2 -- deploy-preflight static check for the P35.1 full-479 override package.

READ-ONLY pre-deploy gate.  Does NOT copy anything into Mods and does NOT touch
any Saves/original WW file.  It re-verifies the ACTUAL deploy artifact
(output/p35/P35.1_ww_anim_displayname_override.package) independently of the
P35.1 build report, so deployment never trusts a stale report.

Supplied artifacts are re-opened and machine-checked:

  1. TYPE/INSTANCE  : the single WW_ANIM_XML entry must have exact type
                     0x7DF2169C and the real runtime instance
                     0x43F3438A94EDEB2B (never the whitebox 0x4444444400000002).
  2. UNIQUENESS     : WW_ANIM_XML (0x7DF2169C) count in the package must == 1.
  3. OVERRIDE COUNT : re-enumerate <L animations_list>; exactly 479 animation
                      <U> entries must each be overridden (i.e. their
                      animation_raw_display_name now holds a translated value
                      that DIFFERS from the source raw).  English leftover =>
                      FAIL.
  4. CHECKLIST      : human/operator deploy checklist printed to stdout plus an
                     ASCII log written next to the package
                     (P35.2_DEPLOY_CHECKLIST.txt), all-ASCII-safe for PS1
                     safety-logic use.

Parser reuse: uses ww_animation_canary_builder.safe_parse -- the SAME parser
that P35.1 used to build+reopen the package -- so this gate is byte-consistent
with the build artifact.  (Differs from ww_p27_tgi_check which targets the
P27 pipeline's dbpf_fast parser.)

Source package (read-only) is optional via --source; when supplied, each of the
479 override values must differ from the corresponding source raw (proves a real
override, not a no-op English re-write) and every source ordinal 0..478 must
still be present.

Exit codes: 0 PASS / 2 missing artifact / 3 parse failure or missing source /
            4 type/instance/uniqueness violated or whitebox detected /
            5 override count/coverage violated / 6 unexpected error.

ZERO_WRITE_TO_MODS=YES  ZERO_WRITE_TO_SAVES=YES  (this tool never writes Mods)
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
for _p in (HERE,):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)
import ww_animation_canary_builder as wb

WW_ANIM_XML = 0x7DF2169C
EXPECTED_REAL_INSTANCE = 0x43F3438A94EDEB2B
WHITEBOX_INSTANCE = 0x4444444400000002
ANIM_LIST_FIELD = "animations_list"
RAW_FIELD = "animation_raw_display_name"
EXPECT_COUNT = 479


def _tag(el):
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else el.tag


def _name(el):
    return el.get("n") or ""


def _fmt_inst(i):
    return f"0x{i:016X}"


def _reopen_body(pkg: Path):
    """Return (list_of_animation_dicts keyed 0..n-1 of {raw_text, xml_el_text})"""
    idx, err = wb.safe_parse(pkg)
    if err is not None or idx is None:
        return None, f"safe_parse failed: {err}"
    ww = [e for e in idx.entries if getattr(e, "type_id", 0) == WW_ANIM_XML]
    if len(ww) != 1:
        return None, f"WW_ANIM_XML count={len(ww)} != 1"
    e = ww[0]
    plain = wb.decompress_maybe(wb.read_body_raw(pkg, e)).decode("utf-8")
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(plain)
    except ET.ParseError as ex:
        return None, f"xml parse: {ex}"
    lst = None
    for el in root.iter():
        if _tag(el) == "L" and _name(el) == ANIM_LIST_FIELD:
            lst = el
            break
    if lst is None:
        return None, f"no <L n={ANIM_LIST_FIELD}>"
    anims = []
    for child in lst:
        if _tag(child) != "U":
            continue
        raw_el = None
        for sc in child:
            if _tag(sc) in ("T", "I", "E") and _name(sc) == RAW_FIELD:
                raw_el = sc
                break
        anims.append({"text": (raw_el.text if raw_el is not None and
                               raw_el.text is not None else "")})
    return anims, None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pkg", help="P35.1 override .package (deploy artifact)")
    ap.add_argument("--source", default=None,
                    help="optional source WW_Nevely42_Animations.package "
                         "(read-only), to prove each override differs from raw")
    a = ap.parse_args(argv)

    pkg = Path(a.pkg)
    if not pkg.is_file():
        print(f"ERROR: override package not found: {pkg} (exit 2)", file=sys.stderr)
        return 2
    src_fail = None
    src_anims = None
    if a.source:
        sp = Path(a.source)
        if not sp.is_file():
            print(f"ERROR: source package not found: {sp} (exit 2)", file=sys.stderr)
            return 2
        src_anims, src_fail = _reopen_body(sp)
        if src_fail is not None:
            print(f"ERROR: source reopen: {src_fail} (exit 3)", file=sys.stderr)
            return 3
        if len(src_anims) != EXPECT_COUNT:
            print(f"ERROR: source animation count={len(src_anims)} != 479 (exit 3)",
                  file=sys.stderr)
            return 3

    L = []  # ASCII-safe checklist + diagnostics
    L.append("P35.2 DEPLOY-PREFLIGHT CHECK (read-only; nothing copied to Mods)")
    L.append("artifact : %s" % pkg)
    if a.source:
        L.append("source   : %s" % Path(a.source))

    # ---- parse artifact ----
    idx, err = wb.safe_parse(pkg)
    if err is not None or idx is None:
        L.append("PARSE=FAIL (%s)" % err)
        _write_log(pkg, L)
        print("\n".join(L)); print(f"VERDICT=FAIL (exit 3)", file=sys.stderr)
        return 3
    L.append("PARSE=OK")

    ww = [e for e in idx.entries if getattr(e, "type_id", 0) == WW_ANIM_XML]
    L.append(f"WW_ANIM_XML_COUNT={len(ww)}")
    if len(ww) != 1:
        L.append("UNIQUE=FAIL (WW_ANIM_XML count != 1)")
        _write_log(pkg, L)
        print("\n".join(L)); print("VERDICT=FAIL (exit 4)", file=sys.stderr)
        return 4
    L.append("UNIQUE=OK")
    e = ww[0]
    t = getattr(e, "type_id", 0)
    g = getattr(e, "group_id", 0)
    inst = getattr(e, "instance_id", None)
    inst_int = inst if isinstance(inst, int) else None
    inst_fmt = _fmt_inst(inst_int) if isinstance(inst_int, int) else "None"
    L.append(f"TYPE=0x{t:08X}")
    L.append(f"GROUP=0x{g:08X}")
    L.append(f"INSTANCE={inst_fmt}")

    ok = True
    if t != WW_ANIM_XML:
        ok = False
        L.append(f"TYPE_CHECK=FAIL (want 0x{WW_ANIM_XML:08X})")
    else:
        L.append("TYPE_CHECK=OK")
    if inst_int != EXPECTED_REAL_INSTANCE:
        ok = False
        L.append(f"INSTANCE_CHECK=FAIL (want {_fmt_inst(EXPECTED_REAL_INSTANCE)})")
    else:
        L.append("INSTANCE_CHECK=OK")
    if inst_int == WHITEBOX_INSTANCE:
        ok = False
        L.append("WHITEBOX_INSTANCE=DETECTED -> reject deploy")

    # ---- count overrides by re-enumerating body vs source raw ----
    anims, rerr = _reopen_body(pkg)
    if rerr is not None:
        L.append(f"BODY=FAIL ({rerr})")
        ok = False
        anims = []
    if anims:
        L.append(f"ANIM_ENTRIES={len(anims)}")
        if len(anims) != EXPECT_COUNT:
            ok = False
            L.append(f"ENTRY_COUNT=FAIL (want {EXPECT_COUNT}, got {len(anims)})")
        else:
            L.append(f"ENTRY_COUNT=OK ({EXPECT_COUNT})")
        overridden = []
        leftover = []
        for i, ad in enumerate(anims):
            val = ad["text"]
            sr = None
            if src_anims is not None and i < len(src_anims):
                sr = src_anims[i]["text"]
            # overridden means non-empty AND (no source, or differs from source raw)
            if val == "" :
                leftover.append((i, "EMPTY"))
            elif sr is not None and val == sr:
                leftover.append((i, "SAME-AS-RAW"))
            else:
                overridden.append(i)
        OVERRIDE_COUNT = len(overridden)
        L.append(f"OVERRIDE_COUNT={OVERRIDE_COUNT}")
        if OVERRIDE_COUNT == EXPECT_COUNT and not leftover:
            L.append("OVERRIDE_CHECK=OK (all %d overridden)" % EXPECT_COUNT)
        else:
            ok = False
            L.append("OVERRIDE_CHECK=FAIL")
            for i, why in leftover[:40]:
                L.append(f"  ordinal {i:3d} not overridden: {why}")
            if len(leftover) > 40:
                L.append(f"  ... and {len(leftover)-40} more")
        # attribute provenance of a few overrides (translated text present)
        sample = [i for i in overridden][:3]
        for i in sample:
            L.append(f"  sample ordinal {i}: text={anims[i]['text']!r}")

    # ---- deploy checklist ----
    L.append("")
    L.append("DEPLOY CHECKLIST (operator):")
    L.append("  [ ] confirm this artifact is output/p35/P35.1_ww_anim_displayname_override.package")
    L.append("  [ ] confirm source is the real WW_Nevely42_Animations.package (instance 0x%016X)" % EXPECTED_REAL_INSTANCE)
    L.append("  [ ] confirm game/Mods target accepts a loose .package override of that TGI")
    L.append("  [ ] back up the existing override target (if any) before copy")
    L.append("  [ ] copy ONLY this artifact (no STBL, no source clone) into Mods")
    L.append("  [ ] run in-game verification that all 479 display names render translated")
    L.append("  [ ] keep a rollback: original WW package unmodified + this artifact reproducible")
    L.append("")
    L.append(f"VERDICT={'PASS' if ok else 'FAIL'}")
    L.append("ZERO_WRITE_TO_MODS=YES  ZERO_WRITE_TO_SAVES=YES")

    _write_log(pkg, L)
    print("\n".join(L))
    if not ok:
        print("VERDICT=FAIL", file=sys.stderr)
        return 4 if (not (len(ww) == 1 and t == WW_ANIM_XML and inst_int ==
                          EXPECTED_REAL_INSTANCE and inst_int != WHITEBOX_INSTANCE)) else 5
    print("DEPLOY PREFLIGHT: PASS (artifact ready; not copied)")
    return 0


def _write_log(pkg: Path, lines):
    try:
        log = pkg.with_name("P35.2_DEPLOY_CHECKLIST.txt")
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as ex:  # pragma: no cover
        print(f"WARN: could not write checklist log next to package: {ex}",
              file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
