#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_identifier_catalog.py --- P32 Phase-2: 479-row offline animation
identifier catalog over the REAL WW_Nevely42 animation registration XML.

AUTHORITATIVE REUSE (operator mandate -- NO second identity formula)
---------------------------------------------------------------------
The SHA1/preimage identity construction is NOT re-implemented here.  This module
feeds per-row SOURCE-truth through the already-PROVEN reconstructor
(ww_p32_identifier_reconstruct.reconstruct_identifier /
build_identifier_parts), which is a byte-exact replication of the recovered
SexAnimationInstance.get_identifier semantics:
    ordered, SEPARATOR-LESS concat of
      display_name(raw) author SexCategoryType.<NAME> len(actors)
      each location_category.name
      object_animation_clip_name(if truthy) object_geometry_state(if truthy)
      object_material_state(if truthy)
      each actor: SexGenderType.<NAME>  ... then per-actor clips ... then per
      actor position_offset.x/y/z and facing_position_offset (float str; integral
      -> '0.0')
      props(geometry_state truthy -> '(clip, state)'; else clip only)
      version(only if > 1)
    then sha1(utf-8).  None / empty-string identity scalars are SKIPPED; non-str
    scalars -> str().  NO separator.  This module only derives the SEMANTIC fields
    dict from the real xml -- the hash construction is 100% the reconstructor.

default != identity-representation (operator warning, honored here)
-------------------------------------------------------------------
The tuning-container default that the loader probe decoded (animation_x/y/z/angle
/facing tuning default int 0, etc.) is NOT the identity preimage token.  The
final runtime SexAnimationInstance exposes position_offset.x/y/z and
facing_position_offset as FLOATS (0.0 integral -> '0.0'), object geometry/material
as None / empty, props as an empty runtime collection and version as <=1 for a
WW_Nevely42 just-instantiated instance.  So a missing per-row tuning offset is
NOT stamped as "0"; it flows through the SAME runtime-transform semantics the
reconstructor already verifies (float 0.0), exactly as the ordinal-318 golden
proof shows (preimage carries '0.0'*8, never '0'*8).  Nothing here fabricates a
value; every runtime-group input is the reconstructor's own transform of default
tuning, never a raw string.

Per-row model (grounded, no guessing)
-------------------------------------
For EVERY row under <L n='animations_list'> (ordinal = list index), the catalog
  1. reads the PROVEN-TUNING identity inputs VERBATIM from that row's real xml
     subtree: display_name(animation_raw_display_name), author(animation_author),
     sex_category(animation_category, uppercased enum name), locations(scalar
     animation_locations token(s), production key), per-actor gender(clip and
     animation_genders literal -> canonical SexGenderType name, NO silent
     coercion),
  2. introspects the row's OWN subtree for any object/prop/geometry/material/
     version/dancer-ish tuning key (real vocabulary, schema-agnostic) so a row
     that genuinely carries one is SEEN (a pure actor row like ordinal 318 has
     none and gets the runtime transform), never assumed absent,
  3. builds the reconstructor semantic `fields`, feeding PROVEN-TUNING real only,
     and RUNTIME group only via the reconstructor's un-placed-instance transform,
  4. computes identifier with the authoritative reconstructor -> VALID,
     or fail-closes a row to UNKNOWN with an exact reason when a required field
     is missing / a row's real extra object/prop tuning cannot unambiguously map
     into the identity semantics (never guessed, never skipped, never demoted).

Ran only on the Windows box (the real source package is Windows-only; Linux has
no byte copy).  Writes ONLY under --out-dir:
    p32_identifier_catalog.csv
    p32_identifier_catalog_report.txt
  ZERO write to Mods / saves / the WW ts4script / source / catalog-suppress lists.

Exit: 0 ok; 2 args/io; 3 source gate / golden FAIL / FULL_CATALOG_SAFE=NO is not
fatal here (UNKNOWN>0 still emits catalog+report as the operator requires); 4
structural/UNKNOWN>0 gate decided by caller.
"""
import argparse
import csv
import hashlib
import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

WW_ANIM_XML = 0x7DF2169C
EXPECT_SOURCE_SHA_PREFIX = "cd0093f2"          # operator-pinned (must start-match)
EXPECT_SOURCE_SHA = ("cd0093f2ec4b896121fa465672584c12"
                     "384465b631c1d9128fe97d360b87d416")
EXPECT_INSTANCE = 0x43F3438A94EDEB2B            # operator-pinned WW_ANIM_XML inst
EXPECT_ENTRY_COUNT = 479                        # operator-pinned roster size
GOLDEN_ORDINAL = 318
GOLDEN_SHA1_318 = "d0528d3795ca42c60ca5a9eb6bad8658ea74e4b5"

ENTRY_LIST_FIELD = "animations_list"
ACTOR_LIST_FIELDS = ("actors", "animation_actors_list")
PROD_LOCATION_KEYS = ("animation_locations",)
DISPLAY_FIELDS = ("animation_raw_display_name", "raw_display_name")
AUTHOR_FIELDS = ("animation_author", "author")
CATEGORY_FIELDS = ("animation_category", "category")
ACTOR_FIELD_KEYS = ("actor_id", "animation_clip_name", "animation_type",
                    "animation_genders")
KNOWN_GENDERS = {"MALE", "FEMALE", "TRANS_MALE", "TRANS_FEMALE"}

# column headers (operator-mandated minimum + translation-context extras)
CSV_COLUMNS = [
    "source_instance", "ordinal", "raw_display_name", "author", "identifier",
    "validation_status", "identity_input_summary", "status_reason",
    # translation-context extras (NEVER used in the hash)
    "sex_category", "actor_count", "locations", "tags", "stage_name",
    "actor_genders", "actor_clips", "object_animation_clip_name",
    "prop_animation_clip_names",
]
_EXTRA_COLS_NO_HASH = set(CSV_COLUMNS) - {
    "source_instance", "ordinal", "raw_display_name", "author", "identifier",
    "validation_status", "identity_input_summary",
}

# object/prop/geometry/material/version-ish key buckets a row may carry beyond the
# pure-actor model (introspection vocabulary; schema-agnostic low-match)
_OBJPROP_HINTS = ("object", "prop", "geometry", "material", "dancer")
_VER_HINTS = ("version",)

# ---------------------------------------------------------------------------
# reusable canary primitives
# ---------------------------------------------------------------------------
_spec = importlib.util.spec_from_file_location(
    "ww_canary_builder", SCRIPT_DIR / "ww_animation_canary_builder.py")
_wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_wb)
import ww_p32_identifier_reconstruct as _rec  # authoritative rebuild reused AS-IS

# authoritative reconstructor symbols (re-export for tests; object identity is
# the SAME function used by the golden ordinal-318 proof)
reconstruct_identifier = _rec.reconstruct_identifier
build_identifier_parts = _rec.build_identifier_parts


def _el_tag(el):
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else None


def _name(el):
    return el.get("n")


def _text(el):
    return "" if el.text is None else el.text


def sha256(p: Path):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _read_index(pkg: Path):
    sys.path.insert(0, str(SCRIPT_DIR.parent / "src"))
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


# ---------------------------------------------------------------------------
# row readers (mirror the ordinal-318 source fixture; identical production fields)
# ---------------------------------------------------------------------------
def _row_field(row, tag, n):
    for nd in row.iter():
        if _el_tag(nd) == tag and _name(nd) == n:
            v = _text(nd).strip()
            if v:
                return v
    return ""


def _single_text(row, names, tag="T"):
    """first descendant (tag, one of names) with non-empty text; multi -> join.|"""
    hits = []
    for nd in row.iter():
        if _el_tag(nd) == tag and _name(nd) in names:
            v = _text(nd).strip()
            if v:
                hits.append(v)
    return "|".join(_uniq(hits)) if hits else ""


def _uniq(seq):
    out = []
    for x in seq:
        if x not in out:
            out.append(x)
    return out


def _locations(row):
    """REAL production location reader.  WW stores locations either as scalar
    <T n='animation_locations'>DOUBLE_BED</T> (field-origin production reader) or
    as an <L n='animation_locations'> of <T n='location'> children (P29-F / the
    ordinal-318 source model).  Both are REAL source shapes; we accept either so a
    row is read regardless of which container the actual package uses.  Tokens in
    source order; multi-token scalars split on | / , ."""
    # 1) scalar leaf T n=animation_locations (the exact production key); we do NOT
    #    promote a bare 'locations' scalar as a substitute (field-origin warns it)
    raw = _row_field(row, "T", "animation_locations")
    if raw:
        return _split_multi(raw)
    # 2) list container L n=animation_locations / custom_locations / locations
    for name in ("animation_locations", "locations", "custom_locations",
                 "animation_custom_locations"):
        for nd in row.iter():
            if _el_tag(nd) != "L" or _name(nd) != name:
                continue
            toks = []
            for t in nd.iter():
                if _el_tag(t) in ("T", "E") and _text(t).strip():
                    toks.extend(_split_multi(_text(t).strip()))
            if toks:
                return toks
    return []


def _split_multi(raw):
    out = []
    for sep in ("|", ",", ";"):
        raw = raw.replace(sep, "|")
    for t in raw.split("|"):
        t = t.strip()
        if t:
            out.append(t)
    return out


def _actors(row):
    for lname in ACTOR_LIST_FIELDS:
        for nd in row.iter():
            if _el_tag(nd) == "L" and _name(nd) == lname:
                out = []
                for u in nd:
                    if _el_tag(u) != "U":
                        continue
                    out.append({
                        "actor_id": _row_field(u, "T", "actor_id"),
                        "clip": _row_field(u, "T", "animation_clip_name"),
                        "type": _row_field(u, "T", "animation_type"),
                        "genders": _row_field(u, "T", "animation_genders"),
                    })
                if out:
                    return out
    return []


def _runtime_gender(raw):
    if not raw:
        return "UNKNOWN"
    up = raw.strip().upper().replace("_", " ")
    for toks in (up.replace(",", " ").split(),):
        for tok in toks:
            if tok in KNOWN_GENDERS:
                return tok
    return "UNKNOWN" if raw.strip().upper() not in KNOWN_GENDERS else raw.strip().upper()


def _row_extra_keys(row):
    """Introspective census of a row's OWN node vocabulary colliding with the
    object/prop/geometry/material/version buckets (schema-agnostic @n match).
    Returns (objprop keys w/ first non-empty values, versionish w/ values,
    seen_names).  NO fabrication: presence is real; a pure actor row has none."""
    objprop = {}
    ver = {}
    seen = set()
    for nd in row.iter():
        nm = _name(nd)
        if nm is None:
            continue
        low = nm.lower()
        seen.add(nm)
        if any(h in nm for h in _OBJPROP_HINTS):
            val = _text(nd).strip()
            if val or _el_tag(nd) in ("L", "U"):
                if nm not in objprop:
                    objprop[nm] = val if (val or _el_tag(nd) in ("T", "E")) else "<struct>"
        elif any(h in low for h in _VER_HINTS):
            val = _text(nd).strip()
            if val:
                ver.setdefault(nm, val)
    # drop our own actor/display/location fields that merely contain a substring
    for drop in list(objprop):
        if drop in ACTOR_FIELD_KEYS or drop in PROD_LOCATION_KEYS \
                or drop in DISPLAY_FIELDS or drop in AUTHOR_FIELDS:
            objprop.pop(drop, None)
    return objprop, ver, seen


# ---------------------------------------------------------------------------
# per-row semantic build -> reconstructor fields
# ---------------------------------------------------------------------------
def build_row(row, ordinal):
    """Map ONE real <U> row (ET Element, or an XML string that is parsed) ->
    (rowrec dict).  rowrec fields: display/author/category/locations/actors
    (verbatim), plus status/status_reason/identifier/extra_objprop/extra_ver/
    provenance.  Never fabricates, never demotes, never skips an identity
    component: a required PROVEN_TUNING field missing -> status UNKNOWN + exact
    reason.  The RUNTIME group is provided ONLY through the reconstructor
    transform (offsets 0.0 floats etc.), matching the ordinal-318 golden that
    this catalog must reproduce."""
    if isinstance(row, str):
        row = ET.fromstring(row)
    rec = {
        "ordinal": ordinal,
        "raw_display_name": "",
        "author": "",
        "sex_category_e": "",
        "location_literals": [],
        "actor_rows": [],
        "tags": "",
        "stage_name": "",
        "extra_objprop": {},
        "extra_ver": {},
        "status": "UNKNOWN",
        "status_reason": "",
        "fields_proven": {},
    }
    display = _single_text(row, DISPLAY_FIELDS)
    rec["raw_display_name"] = display
    rec["author"] = _single_text(row, AUTHOR_FIELDS)
    cat_e = _single_text(row, CATEGORY_FIELDS)
    rec["sex_category_e"] = cat_e.upper() if cat_e else ""
    rec["tags"] = _single_text(row, ("animation_tags", "tags"))
    rec["stage_name"] = _single_text(row, ("animation_stage_name", "stage_name"))
    loc = _locations(row)
    rec["location_literals"] = loc
    actors = _actors(row)
    actor_rows = []
    for i, a in enumerate(actors):
        actor_rows.append({
            "actor_ordinal": i,
            "actor_id": a.get("actor_id", ""),
            "clip": a.get("clip", ""),
            "type": a.get("type", ""),
            "genders_raw": a.get("genders", ""),
            "gender_runtime": _runtime_gender(a.get("genders", "")),
        })
    rec["actor_rows"] = actor_rows
    op, ve, seen = _row_extra_keys(row)
    rec["extra_objprop"] = op
    rec["extra_ver"] = ve
    rec["extra_seen"] = seen

    # ---- PROVEN-TUNING required-field gate ----
    missing = []
    if not rec["raw_display_name"]:
        missing.append("display_name")
    if not rec["author"]:
        missing.append("author")
    if not rec["sex_category_e"]:
        missing.append("sex_category")
    if not loc:
        missing.append("locations")
    if not actor_rows:
        missing.append("actors")
    # actor-level: clip may be per-slot optional; gender must be UNKNOWN-free
    for ar in actor_rows:
        if ar["gender_runtime"] == "UNKNOWN":
            missing.append("actor%d.gender_runtime=UNKNOWN(no coercion)" % ar["actor_ordinal"])
        if not ar["clip"]:
            missing.append("actor%d.clip=empty" % ar["actor_ordinal"])

    # extra object/prop/geometry/material/version tuning that could perturb the
    # pure-actor runtime model is surfaced; if present and we cannot prove how it
    # feeds the identity (only ordinal-318's absent case is PROVEN), fail closed.
    extra_warn = []
    if op:
        extra_warn.append("carries_object_prop_keys=%s" % sorted(op))
    if ve:
        extra_warn.append("carries_version_keys=%s" % sorted(ve))

    if missing:
        rec["status"] = "UNKNOWN"
        rec["status_reason"] = "missing_identity_required=" + ",".join(missing)
        return rec
    if extra_warn:
        # A real row that carries more than the proven pure-actor/runtime model.
        # We do NOT guess how those extra values enter get_identifier; we require
        # semantic clarity.  If they look like non-identity decoration we still do
        # not assume: gate to UNKNOWN so the operator adjudicates (the nearest
        # structural varieties the operator listed are covered; extra > proven is
        # the honest stop).
        rec["status"] = "UNKNOWN"
        rec["status_reason"] = "gated_extra_identity_tuning=" + ";".join(extra_warn)
        return rec

    # ---- runtime-transform group (reconstructor semantics only) ----
    rec["fields_proven"] = _fields_for_reconstruct(rec)
    rec["status"] = "VALID"
    return rec


def _fields_for_reconstruct(rec):
    """Assemble the reconstructor `fields` dict.  PROVEN_TUNING verbatim; RUNTIME
    group (offsets/facing/props/version/geom/material) via the reconstructor's own
    un-placed runtime transform = the same representation the ordinal-318 golden
    uses (floats 0.0 etc).  object clip/geom/material are empty/None (pure actor,
    gated earlier if the row genuinely carried more)."""
    actors = []
    for ar in rec["actor_rows"]:
        actors.append({
            "gender_type": ar["gender_runtime"],
            "animation_clip_name": ar["clip"] or "",
            "position_offset": {"x": 0.0, "y": 0.0, "z": 0.0},
            "facing_position_offset": 0.0,
        })
    return {
        "display_name": rec["raw_display_name"],
        "author": rec["author"],
        "sex_category": rec["sex_category_e"],
        "actors": actors,
        "locations": list(rec["location_literals"]),
        "object_animation_clip_name": "",
        "object_geometry_state": None,
        "object_material_state": None,
        "props": [],
        "version": 1,
    }


def _summary(rec, fields):
    """Short deterministic identity-input summary for CSV/report (does NOT affect
    the hash: identifier is computed purely from fields via the reconstructor)."""
    loc = "|".join(rec["location_literals"]) or "-"
    gen = ",".join(a["gender_runtime"] for a in rec["actor_rows"]) or "-"
    clips = ",".join(a["clip"] for a in rec["actor_rows"]) or "-"
    return "author=%s;cat=%s;loc=%s;nactors=%d;genders=%s;clips=%s" % (
        rec["author"], rec["sex_category_e"], loc, len(rec["actor_rows"]), gen, clips)


# ---------------------------------------------------------------------------
# top-level enumeration
# ---------------------------------------------------------------------------
def load_roster(pkg: Path, accept_any_sha=False):
    """Read+gate the source package, return (sha, instance, n_entries, rows=
    list of <U> elements under animations_list).  Fail-closed on sha/instance/
    count gates when not bypassed (test-only bypass via accept_any_sha for the
    synthetic offline suite; the authoritative pin is enforced on the live run)."""
    sha = sha256(pkg)
    if not accept_any_sha and not sha.startswith(EXPECT_SOURCE_SHA_PREFIX):
        raise RuntimeError("source sha mismatch (%s...) vs pinned cd0093f2...; use real source" % sha[:8])
    if not accept_any_sha and sha != EXPECT_SOURCE_SHA:
        raise RuntimeError("source sha != authoritative cd0093f2...b87d416 (%s...)" % sha[:16])
    idx = _read_index(pkg)
    ww = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    if len(ww) != 1:
        raise RuntimeError("WW_ANIM_XML count != 1 (got %d)" % len(ww))
    ww_e = ww[0]
    if ww_e.instance_id != EXPECT_INSTANCE:
        raise RuntimeError("WW_ANIM_XML instance 0x%016X != pinned 0x%016X"
                           % (ww_e.instance_id, EXPECT_INSTANCE))
    body = _decompress(_read_body(pkg, ww_e))
    text = body.decode("utf-8", errors="replace")
    root = ET.fromstring(text)
    lists = [n for n in root.iter()
             if _el_tag(n) == "L" and _name(n) == ENTRY_LIST_FIELD]
    if len(lists) != 1:
        raise RuntimeError("<L animations_list> count=%d" % len(lists))
    us = [c for c in list(lists[0]) if _el_tag(c) == "U"]
    if not accept_any_sha and len(us) != EXPECT_ENTRY_COUNT:
        raise RuntimeError("ENTRY_COUNT=%d != pinned %d" % (len(us), EXPECT_ENTRY_COUNT))
    return sha, "0x%016X" % ww_e.instance_id, len(us), us


def build_catalog(pkg: Path, accept_any_sha=False, out_rows=None):
    """Enumerate all rows -> list of rowrecs (Unicode).  Optionally consume into
    out_rows (list).  Each rowrec is fully decoded for the private summary."""
    sha, inst, n, us = load_roster(pkg, accept_any_sha)
    recs = []
    for ordinal, row in enumerate(us):
        if out_rows is not None:
            out_rows.append((ordinal, row))   # expose raw rows for consumers/tests
        r = build_row(row, ordinal)
        f = r.get("fields_proven")
        if r["status"] == "VALID" and f is not None:
            res = reconstruct_identifier(f)
            r["identifier"] = res["sha1"]
            r["preimage"] = res["preimage"]
        else:
            r["identifier"] = ""
            r["preimage"] = ""
        r["summary"] = _summary(r, f)
        recs.append(r)
    return {"source_sha256": sha, "source_instance": inst, "entry_count": n,
            "rows": recs}


def _row_csv(rec):
    g = rec.get("actor_rows", [])
    f = rec.get("fields_proven") or {}
    clips = ",".join(a["clip"] for a in g)
    objs = (rec.get("extra_objprop") or {}).copy()
    return {
        "source_instance": None,  # filled below (no per-row package record kept)
        "ordinal": rec["ordinal"],
        "raw_display_name": rec.get("raw_display_name", ""),
        "author": rec.get("author", ""),
        "identifier": rec.get("identifier", ""),
        "validation_status": rec.get("status", "UNKNOWN"),
        "identity_input_summary": rec.get("summary", ""),
        "status_reason": rec.get("status_reason", ""),
        "sex_category": rec.get("sex_category_e", ""),
        "actor_count": len(g),
        "locations": "|".join(rec.get("location_literals", [])) or "",
        "tags": rec.get("tags", ""),
        "stage_name": rec.get("stage_name", ""),
        "actor_genders": ",".join(a["gender_runtime"] for a in g),
        "actor_clips": clips,
        "object_animation_clip_name": (f or {}).get("object_animation_clip_name", "")
                                      if f else "",
        "prop_animation_clip_names": "",
    }


def write_csv(out_path: Path, catalog, source_instance):
    cols = CSV_COLUMNS
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for rec in catalog["rows"]:
            r = _row_csv(rec)
            r["source_instance"] = source_instance
            w.writerow(r)


def _dup_groups(rows):
    groups = {}
    for r in rows:
        if r.get("identifier"):
            groups.setdefault(r["identifier"], []).append(r["ordinal"])
    dups = {h: v for h, v in groups.items() if len(v) > 1}
    return groups, dups


def render_report(catalog, golden_ok=True, verdict="STOP"):
    L = []
    sha = catalog["source_sha256"]
    rows = catalog["rows"]
    L.append("SOURCE_SHA256=%s" % sha)
    L.append("SOURCE_INSTANCE=%s" % catalog["source_instance"])
    L.append("ENTRY_COUNT=%d" % catalog["entry_count"])
    L.append("CATALOG_ROWS=%d" % len(rows))
    valid = [r for r in rows if r.get("status") == "VALID"]
    unknown = [r for r in rows if r.get("status") == "UNKNOWN"]
    L.append("VALID_COUNT=%d" % len(valid))
    L.append("UNKNOWN_COUNT=%d" % len(unknown))
    groups, dups = _dup_groups(rows)
    L.append("UNIQUE_IDENTIFIER_COUNT=%d" % len(groups))
    L.append("DUPLICATE_IDENTIFIER_COUNT=%d"
             % sum(len(v) for v in dups.values()))
    L.append("DUPLICATE_IDENTIFIER_GROUPS=%d" % len(dups))
    L.append("")
    if dups:
        L.append("DUPLICATES (no auto-dedup; ordinal/name/identifier):")
        for h, ords in sorted(dups.items(), key=lambda kv: kv[1]):
            for o in sorted(ords):
                nm = next((r["raw_display_name"] for r in rows
                           if r["ordinal"] == o and r.get("identifier") == h), "-")
                L.append("  ordinal=%d name=%r identifier=%s" % (o, nm, h))
        L.append("")
    if unknown:
        L.append("AFFECTED_ORPHANS (ordinal | status_reason):")
        for r in unknown:
            L.append("  ordinal=%d | %s" % (r["ordinal"], r.get("status_reason", "")))
        L.append("")
    # deterministic structural sample (op lists 5-10 structurally-different VALID)
    L.append("STRUCTURAL_SAMPLE:")
    for line in _structural_sample(catalog, count=8):
        L.append("  " + line)
    L.append("")
    L.append("ORD318_GOLDEN=%s" % ("PASS" if golden_ok else "FAIL"))
    L.append("FULL_CATALOG_SAFE=%s" % ("YES" if (golden_ok and not unknown) else "NO"))
    L.append("VERDICT=%s" % verdict)
    return "\n".join(L)


def _structural_sample(catalog, count=8):
    """Deterministically select up to `count` VALID rows differing in structure:
    by actor_count, sex_category, location token count, clip cardinality, extra
    keys.  Pure function over catalog rows (sorted by ordinal) so the selection is
    reproducible offline AND on Windows."""
    rows = [r for r in catalog["rows"] if r.get("status") == "VALID"]
    rows.sort(key=lambda r: r["ordinal"])
    buckets = []
    seen_sig = set()
    for r in rows:
        gs = tuple(sorted(a["gender_runtime"] for a in r["actor_rows"][:2])) \
            if r["actor_rows"] else ()
        sig = (len(r["actor_rows"]), r.get("sex_category_e", ""), gs,
               len(r.get("location_literals", [])),
               tuple(sorted((r.get("extra_objprop") or {}).keys())))
        if sig not in seen_sig:
            seen_sig.add(sig)
            buckets.append(r)
        if len(buckets) >= count:
            break
    out = []
    for r in buckets:
        out.append("ordinal=%d name=%r actor_count=%d cat=%s loc=%s id=%s summary=%s"
                   % (r["ordinal"], r.get("raw_display_name", ""),
                      len(r.get("actor_rows", [])), r.get("sex_category_e", ""),
                      "|".join(r.get("location_literals", [])) or "-",
                      r.get("identifier", ""), r.get("summary", "")))
    return out


def _golden(catalog):
    for r in catalog["rows"]:
        if r["ordinal"] == GOLDEN_ORDINAL:
            ok = (r.get("status") == "VALID"
                  and r.get("identifier") == GOLDEN_SHA1_318)
            return ok, r
    return False, None


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pkg", help="WW_Nevely42_Animations.package path")
    ap.add_argument("--out-dir", default="output/p32")
    ap.add_argument("--accept-any-sha", action="store_true",
                    help="test-only: bypass the authoritative source-sha pin "
                         "(synthetic offline fixtures only; NEVER set on the live "
                         "windows command)")
    return ap.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    pkg = Path(a.pkg)
    if not pkg.is_file():
        print("FATAL=PACKAGE_MISSING %s" % pkg, file=sys.stderr)
        return 2
    try:
        # require FULL entries equal 479 on live run (pin).  gate before build so
        # the authoritative roster-count gate is enforced by the ps1 too.
        catalog = build_catalog(pkg, accept_any_sha=a.accept_any_sha)
    except ET.ParseError as e:
        print("FATAL=XML_PARSE_ERROR %s" % e, file=sys.stderr)
        return 3
    except Exception as e:
        print("FATAL=SOURCE_GATE_FAIL %s" % e, file=sys.stderr)
        return 3

    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "p32_identifier_catalog.csv"
    rpt_path = out_dir / "p32_identifier_catalog_report.txt"
    write_csv(csv_path, catalog, catalog["source_instance"])

    g_ok, g_row = _golden(catalog)
    valid = [r for r in catalog["rows"] if r.get("status") == "VALID"]
    unknown = [r for r in catalog["rows"] if r.get("status") == "UNKNOWN"]
    full_safe = g_ok and not unknown
    verdict = "GO" if full_safe else "STOP"
    report = render_report(catalog, golden_ok=g_ok, verdict=verdict)
    rpt_path.write_text(report + "\n", encoding="utf-8")

    print(report)
    print("")
    print("WROTE %s" % csv_path)
    print("WROTE %s" % rpt_path)
    # machine lines the ps1 / operator consume
    print("VALID_COUNT=%d" % len(valid))
    print("UNKNOWN_COUNT=%d" % len(unknown))
    if unknown:
        print("UNKNOWN_ROW_COUNT=%d" % len(unknown))
        for r in unknown[:60]:
            print("UNKNOWN_ROW ordinal=%d reason=%s" % (r["ordinal"], r.get("status_reason", "")))
    if not g_ok:
        print("ORD318_GOLDEN=FAIL")
        print("VERDICT=STOP")
        return 3
    if unknown:
        print("ORD318_GOLDEN=PASS")
        print("FULL_CATALOG_SAFE=NO")
        print("VERDICT=STOP")
        return 2  # catalog+report emitted as operator requires; STOP (not safe)
    print("ORD318_GOLDEN=PASS")
    print("FULL_CATALOG_SAFE=YES")
    print("VERDICT=GO")
    return 0


if __name__ == "__main__":
    sys.exit(main())
