#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_identifier_source_fixture.py --- P32 ordinal-318 INDEPENDENT source
confirmation fixture extractor.

Purpose (breaks the golden-test circular dependency)
-----------------------------------------------------------------------
The golden fixture in ww_p32_identifier_reconstruct_logic_test.py plugs recovered
inputs (display_name / author / sex_category / gender_pair / clips / 8 zero
offsets / DOUBLE_BED / version<=1) and asserts the reconstructor reproduces the
observed runtime identifier d0528d3795...  That assertion ONLY proves the
ALGORITHM is byte-exact; it does NOT prove those inputs came from the real
source.  This extractor independently reads the REAL WW_Nevely42 source
animation XML (ordinal 318) and confirms the fixture's SEMANTIC inputs straight
from the package, so the fixture is grounded in source truth, not in the hash.

What it extracts (read-only) for the SINGLE target ordinal (default 318)
-----------------------------------------------------------------------
  * source package sha256 + the WW_ANIM_XML instance (fail-closed gates, as P29F)
  * raw animation_raw_display_name           -> display_name       (T n=...)
  * animation_author                          -> author
  * animation_category / animation_tags / animation_locations / custom locations
  * animation_actors_list <L n="actors">     -> per-actor <U>:
         actor_id , animation_clip_name , animation_type ,
         animation_genders                    -> per-actor gender + clip
  * ordinal mapping (index under animations_list)

Runtime-only (NOT stored literally in the tuning XML, emitted as RUNTIME-FIXED
so the extractor never fabricates): position_offset x/y/z and
facing_position_offset are sim transform values (0.0 for an un-placed/just-instantiated
instance), and version is a runtime SexAnimationInstance field (<=1 contributed
nothing -> None).  object_geometry_state / object_material_state and props are
runtime-instance collections; when a WW_Nevely42 entry has no object/prop tuning
they are None/empty at runtime (the golden preimage has none).  We emit a
NOTE for each such non-XML field instead of inventing a value.

Gender literal -> SexGenderType mapping: a real WW entry stores the actor gender
as animation_genders text; the runtime enum name fragment one sees in the
runtime-identifier string is SexGenderType.MALE / SexGenderType.FEMALE.  We
report the RAW literal verbatim AND, when it uppercase-maps cleanly to a
SexGenderType name (MALE/FEMALE/...), the canonical runtime name it would feed
the reconstructor.  NO silent coercion: if the raw literal is absent or does not
upper-case to a known enum name, emit gender_raw + gender_RUNTIME=UNKNOWN and let
a human adjudicate (REVIEW), never guess.

Output (write ONLY under --out-dir; ZERO write to Mods/saves):
    p32_identifier_source_fixture.txt   human report
    p32_identifier_source_fixture.json  machine block usable as reconstructor fixture seed

Exit 0 = dumped; 2 = io/args; 3 = source gates failed / target ordinal absent.

Run on the Windows box against the REAL source package:
  powershell -ExecutionPolicy Bypass -File .\\scripts\\ww_p32_identifier_source_fixture.ps1
"""
import argparse
import hashlib
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

WW_ANIM_XML = 0x7DF2169C
ACTOR_LIST_FIELD = "actors"
ENTRY_LIST_FIELD = "animations_list"
AUTHOR_FIELDS = ("animation_author", "author")
ACTOR_FIELDS = ("actor_id", "animation_clip_name", "animation_type",
                "animation_genders")
# golden ordinal-318 runtime identifier (from the exact recovery)
GOLDEN_SHA1_318 = "d0528d3795ca42c60ca5a9eb6bad8658ea74e4b5"
# source sha256 of the exact WW_Nevely42 package the P29-F census used
EXPECT_SOURCE_SHA_PREFIX = "cd0093f2"
EXPECT_INSTANCE = 0x43F3438A94EDEB2B
TARGET_ORDINAL = 318
EXPECT_MIN_ENTRIES = 479  # real WW_Nevely42 census count (P29-F established)


def _el_tag(el):
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else None


def _name(el):
    return el.get("n")


def _text(el):
    t = el.text
    return "" if t is None else t


def sha256(p: Path):
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---- source package primitives (mirror ww_animation_canary_builder) ----
def _read_index(pkg: Path):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from dbpf_fast import safe_parse  # noqa
    idx, err = safe_parse(str(pkg))
    if err or idx is None:
        raise RuntimeError("safe_parse failed: %s" % err)
    return idx


def _read_body(pkg: Path, entry) -> bytes:
    off = entry.offset & 0x7FFFFFFF
    size = entry.size & 0x7FFFFFFF
    with open(pkg, "rb") as fh:
        fh.seek(off)
        return fh.read(size)


def _decompress(body: bytes) -> bytes:
    if body[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        try:
            return __import__("zlib").decompress(body)
        except Exception:
            return body
    return body


def _name_nodes(entry_el, name, tag=None):
    return [c for c in list(entry_el) if _name(c) == name
            and (tag is None or _el_tag(c) == tag)]


def _single_text(entry_el, names, tag="T"):
    for nm in names:
        nodes = _name_nodes(entry_el, nm, tag)
        if len(nodes) == 1:
            return _text(nodes[0]).strip()
        if len(nodes) > 1:
            return "|".join(_text(x).strip() for x in nodes)
    return ""


def _list_text(entry_el, name):
    """<L n=name> -> join of descendant <T> texts (location / tag references)."""
    ls = _name_nodes(entry_el, name, "L")
    if not ls:
        return ""
    parts = []
    for t in ls[0].iter():
        if _el_tag(t) == "T":
            v = _text(t).strip()
            if v:
                parts.append(v)
    return "|".join(parts)


def _actors(entry_el):
    """<L n='actors'> -> list of per-actor dicts (all ACTOR_FIELDS verbatim)."""
    out = []
    ls = _name_nodes(entry_el, ACTOR_LIST_FIELD, "L")
    if ls:
        for u in list(ls[0]):
            if _el_tag(u) != "U":
                continue
            act = {}
            for f in ACTOR_FIELDS:
                ns = _name_nodes(u, f)
                act[f] = _text(ns[0]).strip() if len(ns) == 1 else \
                    ("|".join(_text(x).strip() for x in ns) if ns else "")
            out.append(act)
    # fallback: a direct list of <U> under an 'animation_actors_list' container
    if not out:
        for aname in ("animation_actors_list", "animation_actors"):
            ls = _name_nodes(entry_el, aname, "L")
            if ls:
                for u in list(ls[0]):
                    if _el_tag(u) == "U":
                        out.append({f: _name_nodes(u, f)[0].text.strip()
                                    if _name_nodes(u, f) else ""
                                    for f in ACTOR_FIELDS})
                break
    return out


KNOWN_GENDERS = {"MALE", "FEMALE", "TRANS_MALE", "TRANS_FEMALE"}


def _runtime_gender(raw):
    """Upper-case the raw animation_genders literal to a SexGenderType name, if
    cleanly one token; else UNKNOWN (no silent coercion)."""
    if not raw:
        return "UNKNOWN"
    up = raw.strip().upper()
    # could be space/underscore/comma separated tokens; take first clean token
    for tok in up.replace(",", " ").replace("_", " ").split():
        if tok in KNOWN_GENDERS:
            return tok
        if up in KNOWN_GENDERS:
            return up
    return "UNKNOWN" if up not in KNOWN_GENDERS else up


def extract_ordinal(pkg: Path, ordinal=TARGET_ORDINAL):
    """Return (report_lines, machine_dict) or raise."""
    sha = sha256(pkg)
    idx = _read_index(pkg)
    ww = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    if len(ww) != 1:
        raise RuntimeError("WW_ANIM_XML count != 1 (got %d)" % len(ww))
    ww_e = ww[0]
    if ww_e.instance_id != EXPECT_INSTANCE:
        raise RuntimeError("instance 0x%016X != expected 0x%016X"
                           % (ww_e.instance_id, EXPECT_INSTANCE))
    # test-only bypass of the authoritative source-sha pin.  OFF (empty) by
    # default so the live Windows command always fail-closes on a tampered/
    # different source; the offline logic test sets it to exercise the other gates
    # against a synthetic package whose sha (necessarily) differs.
    _accept_any = os.environ.get("P32_SRC_ACCEPT_ANY_SHA", "") == "1"
    if not _accept_any and not sha.startswith(EXPECT_SOURCE_SHA_PREFIX):
        raise RuntimeError("source sha mismatch (%s...) vs pinned %s..."
                           % (sha[:8], EXPECT_SOURCE_SHA_PREFIX))

    body = _decompress(_read_body(pkg, ww_e))
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception as e:
        raise RuntimeError("decode: %s" % e)
    root = ET.fromstring(text)

    # locate ENTRY_LIST_FIELD <L>, walk <U> children in order (ordinal = index)
    lists = [n for n in root.iter()
             if _el_tag(n) == "L" and _name(n) == ENTRY_LIST_FIELD]
    if len(lists) != 1:
        raise RuntimeError("<L animations_list> count=%d" % len(lists))
    us = [c for c in list(lists[0]) if _el_tag(c) == "U"]
    n_entries = len(us)
    if ordinal < 0 or ordinal >= n_entries:
        raise RuntimeError("ordinal %d out of range (entries=%d)" % (ordinal, n_entries))
    entry = us[ordinal]

    display = _single_text(entry, ("animation_raw_display_name", "raw_display_name"))
    author = _single_text(entry, AUTHOR_FIELDS)
    category = _single_text(entry, ("animation_category", "category"))
    tags = _single_text(entry, ("animation_tags", "tags"))
    locations = _list_text(entry, "animation_locations")
    if not locations:
        locations = _list_text(entry, "animation_custom_locations")
    actors = _actors(entry)

    actor_rows = []
    for i, a in enumerate(actors):
        raw_g = a.get("animation_genders", "")
        actor_rows.append({
            "actor_ordinal": i,
            "actor_id": a.get("actor_id", ""),
            "animation_clip_name": a.get("animation_clip_name", ""),
            "animation_type": a.get("animation_type", ""),
            "animation_genders_raw": raw_g,
            "gender_runtime": _runtime_gender(raw_g),
        })

    d = {
        "source_package": str(pkg),
        "source_sha256": sha,
        "source_sha_prefix": sha[:8],
        "source_instance": "0x%016X" % ww_e.instance_id,
        "entry_count": n_entries,
        "target_ordinal": ordinal,
        "display_name": display,
        "author": author,
        "sex_category": category.upper() if category else "",
        "tags": tags,
        "location_literals": locations,
        "actor_count": len(actor_rows),
        "actors": actor_rows,
        # runtime-only note-fields (NOT literal in tuning XML; never fabricated)
        "notes": {
            "position_offset_xyz": "RUNTIME_TRANSFORM (not a tuning field) "
                                   "-> 0.0 at just-instantiated instance per recovery",
            "facing_position_offset": "RUNTIME_TRANSFORM -> 0.0",
            "object_animation_clip_name": "RUNTIME object-slot clip; empty/absent here",
            "object_geometry_state": "RUNTIME; None here",
            "object_material_state": "RUNTIME; None here",
            "props": "RUNTIME prop collection; empty for this entry",
            "version": "RUNTIME field; <=1 -> contributes None",
        },
    }
    return d


def render_report(d):
    L = []
    L.append("=== P32 source fixture: ordinal %d (independent of the golden hash) ===" % d["target_ordinal"])
    L.append("SOURCE_SHA256=%s" % d["source_sha256"])
    L.append("SOURCE_INSTANCE=%s" % d["source_instance"])
    L.append("ENTRY_COUNT=%d" % d["entry_count"])
    L.append("TARGET_ORDINAL=%d" % d["target_ordinal"])
    L.append("")
    L.append("display_name             : %r" % d["display_name"])
    L.append("author                   : %r" % d["author"])
    L.append("sex_category(upper)      : %r" % d["sex_category"])
    L.append("tags                     : %r" % d["tags"])
    L.append("location_literals        : %r" % d["location_literals"])
    L.append("actor_count              : %d" % d["actor_count"])
    for a in d["actors"]:
        L.append("  actor[%s] id=%r clip=%r type=%r gender_raw=%r gender_runtime=%s"
                 % (a["actor_ordinal"], a["actor_id"], a["animation_clip_name"],
                    a["animation_type"], a["animation_genders_raw"],
                    a["gender_runtime"]))
    L.append("")
    L.append("RUNTIME-ONLY (NOT tuning fields; see notes):")
    for k, v in d["notes"].items():
        L.append("  %-26s : %s" % (k, v))
    return "\n".join(L)


def source_to_reconstruct_fields(d):
    """Turn an extract_ordinal dict into the SEMANTIC fields the reconstructor
    takes, adding the RUNTIME-ONLY constants the tuning XML does not store
    (actor positions = 0.0 transform; object/prop/version absent for this model).
    gender_runtime must be a known SexGenderType name (never UNKNOWN) or the map
    refuses (no coercion) -> caller should have gated on it."""
    actors = []
    for a in d.get("actors", []):
        gr = a.get("gender_runtime")
        if not gr or gr == "UNKNOWN":
            return None, "actor%d gender_runtime=UNKNOWN (no coercion)" % a.get("actor_ordinal", -1)
        actors.append({
            "gender_type": gr,
            "animation_clip_name": a.get("animation_clip_name", "") or "",
            "position_offset": {"x": 0.0, "y": 0.0, "z": 0.0},
            "facing_position_offset": 0.0,
        })
    loc = (d.get("location_literals") or "").split("|") if d.get("location_literals") else []
    return {
        "display_name": d.get("display_name", ""),
        "author": d.get("author", ""),
        "sex_category": (d.get("sex_category") or "").upper(),
        "actors": actors,
        "locations": [x for x in loc if x],
        "object_animation_clip_name": "",
        "object_geometry_state": None,
        "object_material_state": None,
        "props": [],
        "version": 1,
    }, None


def golden_check(d):
    """Run the offline reconstructor over the extracted source fields and compare
    the computed sha1 against the pinned golden ordinal-318 identifier.
    Returns (ok, result_dict, sha, detail)."""
    # import late so this module stays importable without the reconstructor
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ww_p32_identifier_reconstruct import reconstruct_identifier  # noqa
    fields, err = source_to_reconstruct_fields(d)
    if err:
        return False, None, "", "SOURCE_TO_FIELDS: " + err
    fields["display_name"] = d.get("display_name", "")
    res = reconstruct_identifier(fields)
    ok = (res["sha1"] == GOLDEN_SHA1_318)
    detail = ("sha1=%s preimage_len=%d" % (res["sha1"], len(res["preimage"]))
               if not ok else "sha1 match")
    return ok, res, res["sha1"], detail


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pkg", help="WW_Nevely42_Animations.package path")
    ap.add_argument("--out-dir", default="output/p32")
    ap.add_argument("--ordinal", type=int, default=TARGET_ORDINAL,
                    help="target ordinal (default %d)" % TARGET_ORDINAL)
    ap.add_argument("--golden-check", action="store_true",
                    help="after extract, run the offline reconstructor and print "
                         "GOLDEN=PASS/FAIL vs the pinned %s" % GOLDEN_SHA1_318)
    ap.add_argument("--diag", action="store_true",
                    help="on SOURCE_GATE_FAIL print full exception type + "
                         "traceback with line numbers (diagnostic only; still "
                         "fail-closed exit 3).  Also enabled by env P32_DIAG=1.")
    return ap.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    pkg = Path(a.pkg)
    if not pkg.is_file():
        print("FATAL=PACKAGE_MISSING %s" % pkg, file=sys.stderr)
        return 2
    try:
        d = extract_ordinal(pkg, a.ordinal)
    except Exception as e:
        print("FATAL=SOURCE_GATE_FAIL %s" % e, file=sys.stderr)
        if a.diag or os.environ.get("P32_DIAG", "") == "1":
            _tb = ""
            try:
                import traceback as _tbmod
                _tb = _tbmod.format_exc()
            except Exception:
                pass
            print("EXC_TYPE=%s" % type(e).__name__, file=sys.stderr)
            print("EXC_DIAG_START", file=sys.stderr)
            print(_tb, file=sys.stderr)
            print("EXC_DIAG_END", file=sys.stderr)
        return 3

    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rep = out_dir / ("p32_identifier_source_fixture_ord%d.txt" % a.ordinal)
    jsn = out_dir / ("p32_identifier_source_fixture_ord%d.json" % a.ordinal)
    rep.write_text(render_report(d) + "\n", encoding="utf-8")
    jsn.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    print(render_report(d))
    print("")
    print("WROTE %s" % rep)
    print("WROTE %s" % jsn)
    print("SOURCE_FIXTURE=PASS")

    if a.golden_check:
        ok, _res, sha, detail = golden_check(d)
        print("")
        print("GOLDEN_TARGET=%s" % GOLDEN_SHA1_318)
        print("GOLDEN_CALC=%s" % sha)
        print("GOLDEN_DETAIL=%s" % detail)
        print("GOLDEN=%s" % ("PASS" if ok else "FAIL"))
        return 0 if ok else 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
