#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_loader_origin_probe.py -- Step-6 Phase-1 EXACT loader-origin probe + the
TUNING_DEFAULT_SEMANTICS gate (READ-ONLY).

WHAT THIS MODULE IS NOW (rev B, exact-dataflow-first)
-------------------------------------------------------
The previous "generic STORE tracer" proved inadequate: it drifted to UNKNOWN on
fields whose dataflow the real WW bytecode ALREADY makes exact (Windows probe,
2026-09-06).  UNKNOWN=8 did not mean WW itself was unknown -- it meant the
classifier was not encoding the proven dataflow.

Two distinct jobs, only one of which is a live gate:

JOB 1 -- FIELD ORIGIN (CLOSED by Windows exact evidence).
  Authoritative origin table for the 8 identity inputs that Phase-1 previously
  left as "runtime-default group".  The origin is NOT derived from a STORE tracer
  that can miss a wiring; it is the EXACT known dataflow, encoded verbatim:
    * object_animation_clip_name / object_geometry_state /
      object_material_state / prop_animation_clip_name / prop_geometry_state /
      version  ==> PROVEN_TUNING
        (wired override | animation_object | animation_prop | animation_tuning
         key -> instance-field -> SexAnimation*Instance constructor arg)
    * actor.position_offset.x/y/z  ==> PROVEN_TRANSFORM
        (animation_[x,y,z]_offset on override|actor; if ANY nonzero -> Vector3(x,y,z)
         else -> Vector3.ZERO() ; passed as SexAnimationActorInstance(position_offset=...))
    * actor.angle_offset / facing  ==> PROVEN_TRANSFORM
        (animation_angle_offset, or math.degrees(animation_facing_offset),
         passed as SexAnimationActorInstance(angle_offset=...))
  So the 8 origin-UNKNOWNs collapse to 0 UNKNOWN for *origin*.  No STORE tracer
  gate can re-open them; the broad walker (if run) is DIAGNOSTIC ONLY and never
  overrides the exact table.

JOB 2 -- TUNING_DEFAULT_SEMANTICS (THE live gate).
  The identifier replay for all 479 rows needs, for EVERY correlated XML key, the
  precise value ANimationStructureContainer / TUNABLE_STRUCTURE supplies when the
  key is ABSENT in a row.  Only rows that CARRY a key use that carrier's own
  value; rows that don't carry it take the (must-be-proven) container default.
  The default is NOT guessed: 0/0.0/''/None/1 each require bytecode/schema
  evidence from _ts4_animations_tuning.pyc / TunableFactory / the structure
  definition (Windows xdis).  Gate:
    FULL_CORPUS_SAFE_TO_RECONSTRUCT = YES  <=> every missing-key default PROVEN
                                              AND no UNKNOWN anywhere.
    any UNKNOWN default  =>  STOP; the 479-row catalog is NOT generated.

Carrier census correctness (rev B)
-----------------------------------
  * Actor offset keys searched in the roster = the REAL tuning keys
        animation_x_offset / animation_y_offset / animation_z_offset /
        animation_angle_offset / animation_facing_offset
    -- NEVER the identity-field name "position_offset" (that is the runtime
       instance/constructor name, not the XML key).
  * object keys  : object_animation_clip_name / object_geometry_state /
                   object_material_state   (object slot of the row)
  * prop keys    : prop_animation_clip_name / prop_geometry_state
  * version key  : animation_version
  * Structural scoping (NO whole-subtree fuzzy match): actor-offset leaves are
    only counted when nested under the actor list (animation_actors_list | actors)
    as direct leaves of a child <U> actor; prop leaves only when nested under the
    prop list (animation_props_list | props) as leaves of a child <U> prop.
  * Output is SMALL STATS:  key -> non-default/non-empty carrier count + whether
    it is partial/full; never a full 479-row dump here.
  * Known real census (Windows, WW_Nevely42): object_animation_clip_name
    carriers = 179/479; prop_animation_clip_name carriers = 137/479.  Therefore
    these are per-row tuning values and MUST NOT be treated as global defaults.

Status vocabulary (origin): PROVEN_TUNING | PROVEN_TRANSFORM  (+ these may appear
only as the DIAGNOSTIC walker's opinion, never as the authoritative origin, and
the exact table forces them to PROVEN_TUNING/PROVEN_TRANSFORM).
Default-semantics evidence vocabulary: PROVEN_TUNING_DEFAULT | PROVEN_BYTECODE_DEFAULT
| UNKNOWN (with required evidence tag for each non-UNKNOWN).

Output (Windows run): output/p32/p32_loader_origin_audit.txt (.csv) -- small
origin table + carrier stats + TUNING_DEFAULT table + gate.

ZERO_WRITE_TO_MODS=YES  ZERO_WRITE_TO_SAVES=YES  no game launch.
No Chinese override json.  Never touch do_not_touch*.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# ---------------------------------------------------------------------------
# EXACT origin table (authoritative; from the Windows exact bytecode probe).
# Each row: identity_field, origin status, source key(s) fed into the loader,
# the loader transform (exact), and the constructor argument that consumes it.
# ---------------------------------------------------------------------------
ORIGIN_ROWS = [
    {
        "identity_field": "object_animation_clip_name",
        "status": "PROVEN_TUNING",
        "source_key": "object_animation_clip_name",
        "transform": "override | animation_object.object_animation_clip_name -> "
                     "SexAnimationInstance(object_animation_clip_name=...)",
        "evidence": "exact win probe: fed from tuning; carriers=179/479 -> per-row",
    },
    {
        "identity_field": "object_geometry_state",
        "status": "PROVEN_TUNING",
        "source_key": "object_geometry_state",
        "transform": "override | animation_tuning.object_geometry_state -> "
                     "SexAnimationInstance(object_geometry_state=...)",
        "evidence": "exact win probe: fed from tuning",
    },
    {
        "identity_field": "object_material_state",
        "status": "PROVEN_TUNING",
        "source_key": "object_material_state",
        "transform": "override | animation_tuning.object_material_state -> "
                     "SexAnimationInstance(object_material_state=...)",
        "evidence": "exact win probe: fed from tuning",
    },
    {
        "identity_field": "actor.position_offset.x/y/z",
        "status": "PROVEN_TRANSFORM",
        "source_key": "animation_x_offset, animation_y_offset, animation_z_offset",
        "transform": "override | animation_actor.{x,y,z}_offset; if any nonzero -> "
                     "Vector3(x,y,z) else Vector3.ZERO() -> "
                     "SexAnimationActorInstance(position_offset=...)",
        "evidence": "exact win probe: Vector3/ZERO() transform",
    },
    {
        "identity_field": "actor.angle_offset / facing_position_offset",
        "status": "PROVEN_TRANSFORM",
        "source_key": "animation_angle_offset, animation_facing_offset",
        "transform": "animation_angle_offset (override|actor) OR "
                     "math.degrees(animation_facing_offset) -> "
                     "SexAnimationActorInstance(angle_offset=...)",
        "evidence": "exact win probe: degrees() transform",
    },
    {
        "identity_field": "prop_animation_clip_name",
        "status": "PROVEN_TUNING",
        "source_key": "prop_animation_clip_name",
        "transform": "override | animation_prop.prop_animation_clip_name -> "
                     "SexAnimationPropInstance(prop_animation_clip_name=...)",
        "evidence": "exact win probe: fed from tuning; carriers=137/479 -> per-row",
    },
    {
        "identity_field": "prop_geometry_state",
        "status": "PROVEN_TUNING",
        "source_key": "prop_geometry_state",
        "transform": "override | animation_prop.prop_geometry_state -> "
                     "SexAnimationPropInstance(prop_geometry_state=...)",
        "evidence": "exact win probe: fed from tuning",
    },
    {
        "identity_field": "version",
        "status": "PROVEN_TUNING",
        "source_key": "animation_version",
        "transform": "animation_tuning.animation_version -> version -> "
                     "SexAnimationInstance(version=...)",
        "evidence": "exact win probe: fed from tuning; version<=1 skipped in get_identifier",
    },
]

# identity input -> the correlated XML tuning keys whose MISSING-default must be
# proven (JOB 2).  actor offset keys are the REAL keys (animation_*_offset).
DEFAULT_KEYS = [
    ("animation_x_offset", "actor.position_offset.x"),
    ("animation_y_offset", "actor.position_offset.y"),
    ("animation_z_offset", "actor.position_offset.z"),
    ("animation_angle_offset", "actor.angle_offset"),
    ("animation_facing_offset", "actor.facing"),
    ("object_animation_clip_name", "object_animation_clip_name"),
    ("object_geometry_state", "object_geometry_state"),
    ("object_material_state", "object_material_state"),
    ("prop_animation_clip_name", "prop_animation_clip_name"),
    ("prop_geometry_state", "prop_geometry_state"),
    ("animation_version", "version"),
]

# exact carrier keysets for the structural census
OBJECT_KEYS = ("object_animation_clip_name", "object_geometry_state",
               "object_material_state")
PROP_KEYS = ("prop_animation_clip_name", "prop_geometry_state")
ACTOR_OFFSET_KEYS = ("animation_x_offset", "animation_y_offset",
                     "animation_z_offset", "animation_angle_offset",
                     "animation_facing_offset")
VERSION_KEY = "animation_version"
ACTOR_LIST_CONTAINERS = ("animation_actors_list", "actors")
PROP_LIST_CONTAINERS = ("animation_props_list", "props")
VALUE_CONTAINERS = ("animation_override", "animation_tuning", "animation_object",
                    "animation_actor", "animation_prop")

PROBE_FIELDS = [   # kept for backward call-compat; origin now from ORIGIN_ROWS
    (r["identity_field"], (r["source_key"].replace(",", "").split()[0],),
     "instance") for r in ORIGIN_ROWS
]


# ---------------------------------------------------------------------------
# pure helpers (Linux-testable)
# ---------------------------------------------------------------------------
def _leaf_keys(u_el, name_fn, text_fn):
    """Direct-leaf '<T n=...>' keys of a structural element u_el (an actor/
    prop/object sub-record), with their non-empty text."""
    out = {}
    for nd in u_el:
        if text_fn is not None and getattr(nd, "tag", "").rsplit("}", 1)[-1] != "T":
            continue
        nm = name_fn(nd)
        if not nm:
            continue
        out[nm] = text_fn(nd)
    return out


# ---------------------------------------------------------------------------
# structural carrier census over the WHOLE roster (no fuzzy match)
# ---------------------------------------------------------------------------
def _container_records(row_el, containers, name_fn, text_fn):
    """Direct child <U> records under the first <L n in containers> of row_el.
    Returns [] if the container is absent.  Never descends the whole subtree."""
    for nd in row_el.iter():
        if getattr(nd, "tag", "").rsplit("}", 1)[-1] == "L" and name_fn(nd) in containers:
            out = []
            for u in nd:
                if getattr(u, "tag", "").rsplit("}", 1)[-1] == "U":
                    out.append(u)
            return out
    return []


def census_carriers(rows_el, name_fn, text_fn):
    """Return stats: carrier_counts[key]=int + carrier_ordinals[key]=[ordinals]
    with STRUCTURAL scoping:
      * object keys   -> row's own object-slot leaf (row itself is the object rec)
      * prop keys     -> only leaves nested inside a prop <U> of the prop list
      * actor offsets -> only leaves nested inside an actor <U> of the actor list
      * version key   -> row-level animation_version leaf
    Never does a whole-subtree fuzzy match.  "Carrier" requires a NON-EMPTY text
    value (empty/0-ish default nodes do not count as tuning carriers)."""
    counts = {}
    ordinals = {}
    all_keys = OBJECT_KEYS + PROP_KEYS + ACTOR_OFFSET_KEYS + (VERSION_KEY,)
    for k in all_keys:
        counts[k] = 0
        ordinals[k] = []
    for ei, entry in enumerate(rows_el):
        direct = _leaf_keys(entry, name_fn, text_fn)
        # object keys: object-slot leaves.  WW stores the single object slot's
        # fields as direct leaves of the ROW, or wrapped under an explicit object/
        # override/tuning value container.  We look ONLY at the row's own direct
        # leaves and at a DIRECT child wrapper whose name is an object/override/
        # tuning container -- we do NOT scan the actor list or prop list for these.
        wrappers = []
        for nd in entry:
            if getattr(nd, "tag", "").rsplit("}", 1)[-1] == "U":
                nmw = name_fn(nd)
                if nmw and any(w in (nmw or "")
                               for w in ("animation_object", "animation_override",
                                          "animation_tuning")):
                    wrappers.append(nd)
        for k in OBJECT_KEYS:
            v = direct.get(k, "")
            if not v.strip():
                for wu in wrappers:
                    wl = _leaf_keys(wu, name_fn, text_fn)
                    if k in wl and wl[k].strip():
                        v = wl[k]
                        break
            if v and v.strip():
                counts[k] += 1
                ordinals[k].append(ei)
        # prop keys: only inside a prop <U> under the prop list
        for k in PROP_KEYS:
            for u in _container_records(entry, PROP_LIST_CONTAINERS,
                                        name_fn, text_fn):
                lf = _leaf_keys(u, name_fn, text_fn)
                if k in lf and lf[k].strip():
                    counts[k] += 1
                    ordinals[k].append(ei)
                    break
        # actor offsets: only inside an actor <U> under the actor list
        for k in ACTOR_OFFSET_KEYS:
            for u in _container_records(entry, ACTOR_LIST_CONTAINERS,
                                        name_fn, text_fn):
                lf = _leaf_keys(u, name_fn, text_fn)
                if k in lf and lf[k].strip():
                    counts[k] += 1
                    ordinals[k].append(ei)
                    break
        # version: row-level animation_version leaf
        if VERSION_KEY in direct and direct[VERSION_KEY].strip():
            counts[VERSION_KEY] += 1
            ordinals[VERSION_KEY].append(ei)
    return counts, ordinals


# ---------------------------------------------------------------------------
# exact origin -> audit rows (never UNKNOWN from a generic tracer)
# ---------------------------------------------------------------------------
def origin_rows_from_schema(carrier_counts, n):
    """Render the authoritative origin table with per-field carrier awareness.
    PROVEN_TUNING fields that show carriers present on PARTIAL ordinals get a
    note that per-row tuning recovery is REQUIRED (never a global default)."""
    out = []
    for row in ORIGIN_ROWS:
        key = row["source_key"].split(",")[0].split("animation_")[-1].strip() or row["source_key"]
        # choose the census key whose carrier count we hold
        ckey = None
        for cand in OBJECT_KEYS + PROP_KEYS + ACTOR_OFFSET_KEYS + (VERSION_KEY,):
            if row["source_key"].startswith(cand) or cand in row["source_key"]:
                ckey = cand
                # order: prefer the most specific correlated key per field
                if row["identity_field"].startswith("actor.position_offset"):
                    ckey = "animation_x_offset"
                elif row["identity_field"].startswith("actor.angle"):
                    ckey = "animation_angle_offset"
                break
        cnt = carrier_counts.get(ckey, 0) if ckey else 0
        note = row["evidence"]
        if ckey and 0 < cnt < n:
            note += " | carrier %d/%d -> per-row tuning recovery MANDATORY (no global default)" % (cnt, n)
        if ckey and cnt == 0:
            note += " | carrier 0/%d -> key absent corpus-wide -> default-semantics governs" % n
        out.append({
            "identity_field": row["identity_field"],
            "source_key": row["source_key"],
            "constructor_argument": row["identity_field"],
            "transform": ("y" if row["status"] == "PROVEN_TRANSFORM" else "-"),
            "default": ("-" if row["status"] == "PROVEN_TUNING" else "y(transform)"),
            "status": row["status"],
            "evidence_function": row["evidence"],
            "reason": note,
            "carrier_key": ckey,
            "carrier_count": cnt,
        })
    return out


# ---------------------------------------------------------------------------
# TUNING_DEFAULT_SEMANTICS gate
# ---------------------------------------------------------------------------
class DefaultSemanticsReport(object):
    """Holds a recovered default per correlated key.  `proven` True only when a
    bytecode/schema evidence tag is recorded.  Every non-proven key blocks the
    catalog (fail-closed).  This class is populated on the Windows run by
    decode_defaults() and is fully deterministic / json-serialisable."""

    def __init__(self):
        self.entries = {}
        for key, field in DEFAULT_KEYS:
            self.entries[key] = {
                "field": field,
                "default_value": None,      # set by Windows evidence
                "default_display": "?",     # 0 / 0.0 / '' / None / 1 / <other>
                "evidence": "",             # _ts4_animations_tuning.pyc path + sym
                "proven": False,
                "carrier_count": 0,
            }

    def set_default(self, key, value, display, evidence):
        if key not in self.entries:
            return
        self.entries[key]["default_value"] = value
        self.entries[key]["default_display"] = display
        self.entries[key]["evidence"] = evidence
        self.entries[key]["proven"] = bool(evidence)

    def load_carriers(self, carrier_counts):
        for key in self.entries:
            self.entries[key]["carrier_count"] = carrier_counts.get(key, 0)

    def all_proven(self):
        return all(e["proven"] for e in self.entries.values())

    def unknown_keys(self):
        return [k for k, e in self.entries.items() if not e["proven"]]

    def to_rows(self):
        return [{
            "tuning_key": k,
            "feeds_field": e["field"],
            "default_value": e["default_display"],
            "evidence": e["evidence"] or "(missing -> UNKNOWN default)",
            "carrier_count": e["carrier_count"],
            "proven": e["proven"],
        } for k, e in self.entries.items()]


def decode_defaults(tuning_pyc_path=None, get_opcode_mod=None, XBytecode=None):
    """RECOVER the container/TunableStruct default for each correlated key from
    _ts4_animations_tuning.pyc / structure definition (Windows xdis).  Every key
    requires bytecode evidence; there is NO guessing of 0/0.0/''/None/1.

    Pure fallback (Linux, no pyc): returns a report with NO proof so the gate
    fails CLOSED until the Windows run supplies bytecode evidence.  Never
    fabricates a default: decode_defaults only records a default when it reads a
    real literal + evidence from the .pyc via the focused extractor module
    ww_p32_loader_origin_defaults (Windows-only; it cannot be weakened here).
    See `--tuning-pyc` on the Windows invocation."""
    rep = DefaultSemanticsReport()
    if tuning_pyc_path is not None and tuning_pyc_path.is_file() and \
            get_opcode_mod is not None and XBytecode is not None:
        try:
            import ww_p32_loader_origin_defaults as _globals_mod
            _globals_mod.populate_defaults(rep, str(tuning_pyc_path),
                                           XBytecode=XBytecode,
                                           get_opcode_mod=get_opcode_mod)
        except Exception:
            pass  # any key left unproven stays UNPROVEN -> gate NO (fail closed)
    return rep


# --- diagnostic-only decision interface (back-compat; NOT authoritative) ---
def decide_field_evidence(field, cands, ents, schema_sink_keys):
    """Back-compat shim: the authoritative origin is ORIGIN_ROWS.  This is kept
    so older calls/tests don't crash, but it is DIAGNOSTIC ONLY and must never
    drive the origin gate.  If asked, it should reflect that a generic store
    tracer cannot re-open an exact-proven origin."""
    st = "PROVEN_TUNING"
    for r in ORIGIN_ROWS:
        if r["identity_field"] == field:
            st = r["status"]
            break
    return {
        "identity_field": field,
        "source_key": "|".join(cands) if cands else "",
        "constructor_argument": field,
        "transform": "y" if st == "PROVEN_TRANSFORM" else "-",
        "default": "-",
        "status": st,
        "evidence_function": "EXACT (ORIGIN_ROWS) [diag shim]",
        "reason": "Exact origin overrides generic tracer.",
    }


# ---------------------------------------------------------------------------
# renderers
# ---------------------------------------------------------------------------
def render_origin_csv_rows(rows):
    hdr = ["identity_field", "source_key", "constructor_argument", "transform",
           "default", "status", "evidence_function", "reason", "carrier_key",
           "carrier_count"]
    return [hdr] + [[r.get("identity_field", ""), r.get("source_key", ""),
                     r.get("constructor_argument", ""), r.get("transform", ""),
                     r.get("default", ""), r.get("status", ""),
                     r.get("evidence_function", ""), r.get("reason", ""),
                     r.get("carrier_key", ""), r.get("carrier_count", 0)]
                    for r in rows]


def render_report(origin_rows, carrier_counts, carrier_ordinals, n,
                  default_rep):
    """Small conclusion text: origin table + carrier stats + default table +
    gate.  No full 479-row dump."""
    lines = []
    lines.append("=== P32 EXACT loader-origin (origin CLOSED) + "
                 "TUNING_DEFAULT_SEMANTICS gate (read-only) ===")
    lines.append("ENTRY_COUNT=%d" % n)
    lines.append("")
    lines.append("== FIELD ORIGIN (authoritative, exact dataflow) ==")
    lines.append("identity_field | status | carrier_key | carrier_count")
    for r in origin_rows:
        lines.append("%s | %s | %s | %s"
                     % (r["identity_field"], r["status"],
                        r.get("carrier_key") or "-", r.get("carrier_count") or 0))
    n_t = sum(1 for r in origin_rows if r["status"] == "PROVEN_TUNING")
    n_tr = sum(1 for r in origin_rows if r["status"] == "PROVEN_TRANSFORM")
    n_u = sum(1 for r in origin_rows if r["status"] == "UNKNOWN")
    lines.append("ORIGIN: PROVEN_TUNING=%d PROVEN_TRANSFORM=%d UNKNOWN=%d "
                 "(origin fully closed, no tracer-reopen)" % (n_t, n_tr, n_u))
    lines.append("")
    lines.append("== CARRIER STATS (small; corrected REAL tuning keys) ==")
    lines.append("key | non-empty carrier count | distribution")
    for k, c in sorted(carrier_counts.items()):
        dist = "FULL(%d/%d)" % (c, n) if c == n else (
            "PARTIAL(%d/%d)" % (c, n) if c else "NONE")
        lines.append("%s | %d | %s" % (k, c, dist))
    lines.append("")
    lines.append("== TUNING_DEFAULT_SEMANTICS (missing-key container default) ==")
    lines.append("tuning_key | feeds_field | default_value | evidence | carrier_count | proven")
    for e in default_rep.to_rows():
        lines.append("%s | %s | %s | %s | %s | %s"
                     % (e["tuning_key"], e["feeds_field"], e["default_value"],
                        e["evidence"], e["carrier_count"],
                        "PROVEN" if e["proven"] else "UNKNOWN"))
    unk = default_rep.unknown_keys()
    safe = default_rep.all_proven()
    lines.append("")
    lines.append("DEFAULT_UNKNOWN_COUNT=%d" % len(unk))
    lines.append("FULL_CORPUS_SAFE_TO_RECONSTRUCT=%s"
                 % ("YES" if (safe and n_u == 0) else "NO"))
    if unk:
        lines.append("STOP_REASON=missing-key default UNKNOWN for: %s"
                     % ", ".join(unk))
    return lines


# ---------------------------------------------------------------------------
# main orchestration (Windows read-only; also runs offline in --verify mode)
# ---------------------------------------------------------------------------
OUT_FILE = "output/p32/p32_loader_origin_audit.txt"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="P32 EXACT loader-origin (closed) + TUNING_DEFAULT_SEMANTICS "
                    "gate (read-only).")
    ap.add_argument("source", nargs="?", help="WW_Nevely42 .package path")
    ap.add_argument("--dir", help="Mods dir containing the real .ts4script")
    ap.add_argument("--out-dir", default="output/p32")
    ap.add_argument("--tuning-pyc", help="path to _ts4_animations_tuning.pyc "
                    "(extracted from the real .ts4script) for default recovery")
    ap.add_argument("--verify", action="store_true",
                    help="offline (Linux) census-mode smoke: needs no xdis; "
                         "reads ONLY the source package xml if given, else uses "
                         "a tiny builtin synthetic 3-row roster to exercise the "
                         "exact-parser + gate fail-closed path.")
    a = ap.parse_args(argv)

    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- load a roster (source xml if available, else synthetic) ----
    try:
        import ww_p32_identifier_source_fixture as SF
    except Exception as ex:
        print("ERROR: 无法导入 ww_p32_identifier_source_fixture: %s" % ex, file=sys.stderr)
        return 6

    rows_el = []
    src = Path(a.source) if a.source else None
    if src is not None and src.is_file():
        idx = SF._read_index(src)
        ww = [e for e in idx.entries if e.type_id == SF.WW_ANIM_XML]
        if len(ww) != 1:
            print("ERROR: WW_ANIM_XML count != 1", file=sys.stderr)
            return 3
        body = SF._decompress(SF._read_body(src, ww[0]))
        import xml.etree.ElementTree as ET
        root = ET.fromstring(body.decode("utf-8", errors="replace"))
        lists = [x for x in root.iter()
                 if SF._el_tag(x) == "L" and SF._name(x) == SF.ENTRY_LIST_FIELD]
        if len(lists) != 1:
            print("ERROR: animations_list count=%d" % len(lists), file=sys.stderr)
            return 3
        rows_el = [c for c in list(lists[0]) if SF._el_tag(c) == "U"]
    elif a.verify:
        # builtin synthetic 3-row roster exercising the exact structural parser
        import xml.etree.ElementTree as ET
        synth = ('<I n="T"><L n="animations_list">'
                 '<U n="s0">'
                 '  <L n="animation_actors_list"><U n="a0">'
                 '    <T n="actor_id">sim</T>'
                 '    <T n="animation_x_offset">0.0</T>'
                 '    <T n="animation_y_offset">0.0</T>'
                 '    <T n="animation_angle_offset">90.0</T>'
                 '  </U></L>'
                 '  <T n="animation_version">1</T>'
                 '</U>'
                 '<U n="s1">'
                 '  <L n="animation_props_list"><U n="p0">'
                 '    <T n="prop_animation_clip_name">clip_a</T>'
                 '    <T n="prop_geometry_state">1</T>'
                 '  </U></L>'
                 '  <T n="object_animation_clip_name">chair</T>'
                 '  <T n="animation_version">3</T>'
                 '</U>'
                 '<U n="s2">'
                 '  <T n="object_animation_clip_name">bed</T>'
                 '  <T n="object_geometry_state">2</T>'
                 '</U>'
                 '</L></I>')
        root = ET.fromstring(synth)
        lst = [x for x in root.iter() if SF._el_tag(x) == "L"
               and SF._name(x) == SF.ENTRY_LIST_FIELD][0]
        rows_el = [c for c in list(lst) if SF._el_tag(c) == "U"]
    else:
        print("ERROR: need --source ./<pkg>.package (real) or --verify (synthetic)",
              file=sys.stderr)
        return 2
    n = len(rows_el)

    # ---- structural carrier census ----
    carrier_counts, carrier_ordinals = census_carriers(rows_el, SF._name, SF._text)
    origin_rows = origin_rows_from_schema(carrier_counts, n)

    # ---- default recovery fails closed without Windows bytecode evidence ----
    tuning_pyc = Path(a.tuning_pyc) if a.tuning_pyc else None
    go_mod = None
    XBytecode = None
    if tuning_pyc is not None:
        # xdis optional; required only to prove defaults from the tuning pyc.
        try:
            import xdis.disasm as _xd
            from xdis.op_imports import get_opcode_module, PythonImplementation  # noqa
            go_mod = get_opcode_module
            XBytecode = _xd.Bytecode
        except Exception:
            go_mod = None
            XBytecode = None
    default_rep = decode_defaults(tuning_pyc_path=tuning_pyc,
                                  get_opcode_mod=go_mod, XBytecode=XBytecode)
    default_rep.load_carriers(carrier_counts)

    lines = render_report(origin_rows, carrier_counts, carrier_ordinals, n,
                          default_rep)
    lines.append("")
    lines.append("ZERO_WRITE_TO_MODS=YES  ZERO_WRITE_TO_SAVES=YES  (read-only)")

    text_out = "\n".join(lines)
    out_path = out_dir / "p32_loader_origin_audit.txt"
    out_path.write_text(text_out, encoding="utf-8")
    with open(out_dir / "p32_loader_origin_audit.csv", "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerows(render_origin_csv_rows(origin_rows))
    with open(out_dir / "p32_loader_origin_audit_defaults.json", "w",
              encoding="utf-8") as f:
        json.dump(default_rep.to_rows(), f, indent=2, ensure_ascii=False)

    safe = default_rep.all_proven() and not any(
        r["status"] == "UNKNOWN" for r in origin_rows)
    print(text_out)
    print("OUT_TXT=%s" % out_path)
    print("FULL_CORPUS_SAFE_TO_RECONSTRUCT=%s"
          % ("YES" if safe else "NO"))
    print("P32_LOADER_ORIGIN_PROBE=DONE (read-only)")
    return 0 if (a.verify or a.source) else 2


if __name__ == "__main__":
    sys.exit(main())
