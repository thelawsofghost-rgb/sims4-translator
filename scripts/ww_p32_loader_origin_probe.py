#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_loader_origin_probe.py -- Step-6 Phase-1 exact loader-origin probe (READ-ONLY)

Goal: close FULL_CORPUS_ORIGIN_UNKNOWN = 8.  These are NOT unknown field names
(IDENTITY_FIELDS_ACCOUNTED = 15/15, FIELD_NAME_UNKNOWN = 0); they are the 8
identity inputs whose *loader/constructor origin across the FULL 479-row corpus*
is not yet closed.  Ordinal-318 defaults are PROVEN but may NOT be extrapolated
to the other 478 rows.

Targeted fields (runtime-default group, to be re-proven corpus-wide here):
  6  object_animation_clip_name
  7  object_geometry_state
  8  object_material_state
  11 actor.position_offset.x/y/z
  12 actor.angle_offset / facing_position_offset
  13 prop_animation_clip_name
  14 prop_geometry_state
  15 version

Precise question, per field:  XML/tuning -> _create_sex_animation_instance ->
SexAnimationInstance / SexAnimationActorInstance / SexAnimationPropInstance ->
does this identity input originate as
  A. tuning/XML-fed   (explicitly passed in / consumed from a tuning field)
  B. constructor default
  C. set later by producer/transform
  D. still UNKNOWN

This is NOT a broad scan.  It:
  * disassembles animations_loader.pyc --_create_sex_animation_instance and the
    SexAnimation*Instance __init__ pyc files (targeted functions + the nested /
    helper code that owns object clip/state, actor position/facing offset,
    props, version);
  * for each STORE_ATTR on one of the 8 instance fields, walks the value
    provenance UP the instruction stream (LOAD_FAST / LOAD_ATTR / LOAD_GLOBAL /
    LOAD_CONST / CALL) and classifies the loader as
      tuning/XML-fed | default | transform | helper-return | unknown;
  * additionally scans the tuning schema for each of the 8 keys and reports
    whether the loader has any path to CONSUME that tuning key.  If a field can
    be set by XML/tuning it may NOT be classed constructor-default-only.

Design: the *classifier* (classify_provenance / render_audit / gate) is a pure,
synthetic-testable core (runs on Linux).  The xdis bytecode WALKER (needs a real
.ts4script on Windows) emits per-field evidence records consumed by that core.

Status vocabulary (only these):
  PROVEN_TUNING | PROVEN_DEFAULT | PROVEN_TRANSFORM | UNKNOWN
Gate:
  FULL_CORPUS_SAFE_TO_RECONSTRUCT = YES only when every one of the 15 identity
  inputs reaches PROVEN_TUNING / PROVEN_DEFAULT / PROVEN_TRANSFORM with NO
  UNKNOWN.  If a field is tuning-present on only some ordinals, the per-ordinal
  detection path must handle it (never default-override a present tuning value);
  if any runtime-only value exists that cannot be statically recovered from
  source, the catalog must STOP and report the affected ordinal count.

Output (Windows run): output/p32/p32_loader_origin_audit.txt  (small
conclusion table only).

ZERO_WRITE_TO_MODS=YES  ZERO_WRITE_TO_SAVES=YES  no game launch.
No Chinese override json.  Never touch do_not_touch*.json.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

# Alias used by the synthetic logic test on Linux and the xdis walker on Windows.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

PROBE_FIELDS = [   # (identity_field, source_key_candidates, instance_class)
    ("object_animation_clip_name", ("object_animation_clip_name",), "SexAnimationInstance"),
    ("object_geometry_state", ("object_geometry_state",), "SexAnimationInstance"),
    ("object_material_state", ("object_material_state",), "SexAnimationInstance"),
    ("actor.position_offset.x/y/z", ("position_offset", "position_offset_x",
                                     "position_offset_y", "position_offset_z"), "SexAnimationActorInstance"),
    ("actor.angle_offset / facing_position_offset",
     ("angle_offset", "facing_position_offset", "facing"), "SexAnimationActorInstance"),
    ("prop_animation_clip_name", ("prop_animation_clip_name",), "SexAnimationPropInstance"),
    ("prop_geometry_state", ("prop_geometry_state",), "SexAnimationPropInstance"),
    ("version", ("version",), "SexAnimationInstance"),
]

# --- pure classifier core (Linux-testable) --------------------------------

def classify_provenance(ev):
    """Classify a single field-origin evidence record into one of the four
    statuses.  ev is a dict with keys:
      has_tuning_sink : bool  -- a tuning key exists AND the loader can consume it
      value_kind      : str   -- 'default'|'transform'|'tuning'|'helper'|'unknown'
      set_later       : bool  -- assigned outside __init__ by a producer/transform
      some_ordinals_only: bool-- tuning present on only a subset of ordinals
      default_overrides_present_tuning: bool (illegal pattern)
    Returns (status, reason)."""
    if ev.get("default_overrides_present_tuning"):
        return ("UNKNOWN", "default branch would override a present tuning value (illegal)")
    # A field that can be fed from tuning must NEVER be forced to default.
    if ev.get("has_tuning_sink"):
        if ev.get("value_kind") in ("tuning",):
            return ("PROVEN_TUNING", "loader consumes it from a tuning field")
        if ev.get("value_kind") in ("helper",):
            # helper-return could still internally read tuning; caller must resolve
            return ("UNKNOWN", "helper-return: unresolved whether it reads tuning")
        if ev.get("set_later"):
            return ("PROVEN_TRANSFORM", "tuning-sink but later producer/transform reassigns")
        return ("UNKNOWN", "tuning key exists but value path not tuning-verbatim")
    # no tuning sink => provenance is by construction/default
    if ev.get("some_ordinals_only"):
        return ("UNKNOWN", "tuning present on some ordinals only: must detect per-ordinal")
    if not ev.get("set_later"):
        if ev.get("value_kind") == "default":
            return ("PROVEN_DEFAULT", "constructor default, no tuning sink, not set later")
        if ev.get("value_kind") == "transform":
            return ("PROVEN_TRANSFORM", "produced by a transform on non-tuning input")
        if ev.get("value_kind") == "helper":
            return ("UNKNOWN", "helper-return without confirmed tuning/default source")
        return ("UNKNOWN", "unclassified value kind %r" % (ev.get("value_kind"),))
    # set later by a producer/transform but no stated kind
    if ev.get("value_kind") == "transform":
        return ("PROVEN_TRANSFORM", "set later by producer/transform")
    return ("UNKNOWN", "set later but transform not confirmed")


def render_audit(rows):
    """rows: list of dicts with keys matching PROBE_FIELDS columns:
      identity_field, source_key, constructor_argument, transform, default,
      status, evidence_function  -> plus a 'reason' appended.
    Returns (csv_lines, summary_lines)."""
    csv_lines = []
    csv_lines.append(["identity_field", "source_key", "constructor_argument",
                      "transform", "default", "status", "evidence_function", "reason"])
    all_status = []
    for r in rows:
        st = r.get("status")
        all_status.append(st)
        csv_lines.append([
            r.get("identity_field", ""),
            r.get("source_key", ""),
            r.get("constructor_argument", ""),
            r.get("transform", ""),
            r.get("default", ""),
            st,
            r.get("evidence_function", ""),
            r.get("reason", ""),
        ])
    n_t = all_status.count("PROVEN_TUNING")
    n_d = all_status.count("PROVEN_DEFAULT")
    n_tr = all_status.count("PROVEN_TRANSFORM")
    n_u = all_status.count("UNKNOWN")
    safe = (n_u == 0)
    summ = []
    summ.append("identity_field | source_key | constructor_argument | transform | "
                "default | status | evidence_function")
    summ.append("-" * 90)
    for r in rows:
        summ.append(" | ".join([
            str(r.get("identity_field", "")), str(r.get("source_key", "")),
            str(r.get("constructor_argument", "")), str(r.get("transform", "")),
            str(r.get("default", "")), str(r.get("status", "")),
            str(r.get("evidence_function", "")),
        ]))
    summ.append("")
    summ.append("PROVEN_TUNING=%d  PROVEN_DEFAULT=%d  PROVEN_TRANSFORM=%d  UNKNOWN=%d"
                % (n_t, n_d, n_tr, n_u))
    summ.append("FULL_CORPUS_SAFE_TO_RECONSTRUCT=%s" % ("YES" if safe else "NO"))
    return csv_lines, summ


# --- xdis walker (Windows: needs real .ts4script) -------------------------

def _imp_optional(modname):
    try:
        return __import__(modname, fromlist=["*"])
    except Exception:
        return None


def get_opcode(ver):
    """xdis opcode table for a python version tuple-2 (CPython)."""
    from xdis.op_imports import get_opcode_module, PythonImplementation
    v = tuple(str(x) for x in ver[:2])
    return get_opcode_module(v, PythonImplementation.CPython)


def named_callee(lines, i):
    run = []
    j = i - 1
    while j >= 0 and j >= i - 12 and lines[j].opname.startswith(("LOAD_", "IMPORT_")):
        run.append(j)
        j -= 1
    for k in reversed(run):
        if lines[k].opname in ("LOAD_GLOBAL", "LOAD_NAME", "LOAD_METHOD",
                               "IMPORT_NAME", "LOAD_METHOD_HANDLE"):
            return lines[k].argrepr
        if lines[k].opname == "LOAD_ATTR":
            return lines[k].argrepr
    return "?"


def _value_kind_of(lines, i):
    """Walk back from instruction i (a STORE operand) to classify the immediate
    value producer: default?  const?  param/local(possibly tuning name)?  call
    (helper/transform)?  attr?  Also surfaces whether an identically-named
    tuning constant is in the string consts (given in consts)."""
    # returns (kind, detail)
    j = i - 1
    while j >= 0 and j >= i - 12:
        it = lines[j]
        if it.opname.startswith("LOAD_CONST"):
            return "const", repr(it.argrepr)
        if it.opname == "LOAD_FAST":
            return "param", str(it.argrepr)
        if it.opname == "LOAD_ATTR":
            return "attr", str(it.argrepr)
        if it.opname == "LOAD_GLOBAL":
            return "global", str(it.argrepr)
        if it.opname.startswith("CALL"):
            return "call", str(named_callee(lines, j))
        if it.opname == "LOAD_METHOD":
            return "method", str(it.argrepr)
        j -= 1
    return "unknown", ""


# --- per-field evidence decision (module-level, synthetic-testable) --------

def _any(e, k):
    return any(x["kind"] == k for x in e)


def decide_field_evidence(field, cands, ents, schema_sink_keys):
    """Fuse raw STORE evidence for one identity field into a row dict whose
    status is decided by the single pure classifier.  `schema_sink_keys` is the
    dict of tuning-key->count present in the package XML for this field (empty if
    none of its candidate keys are real package tuning nodes).

    Anti-over-claim rule (operator): a schema sink that is NOT backed by a write
    literally fed by a tuning-named symbol may still be consumed by the loader
    behind a generic get()/attr (a present tuning value would override any
    constructor default and change the identifier per-ordinal).  That case is
    therefore UNKNOWN -- it is NEVER classed constructor-default-only or a pure
    runtime transform.  Only a schema sink __with__ a tuning-named feed yields
    PROVEN_TUNING."""
    sink = bool(schema_sink_keys)
    kinds = {e["kind"] for e in ents}
    tuning_named_write = any(e["producer_is_tuning_name"] for e in ents)
    evidence_fn = "/".join(sorted({e["tag"] for e in ents})) or "(no write found)"
    has_any_write = bool(ents)
    if sink and not tuning_named_write:
        # schema exposes the key but no write fed by a tuning-named symbol: the
        # loader may read per-row tuning behind a generic get()/attr.  UNKNOWN.
        status, reason = classify_provenance(
            {"value_kind": "unknown", "has_tuning_sink": True})
        reason += ("  [schema exposes %s; no write fed by a tuning-named symbol "
                   "=> residue must be read per-row, not defaulted]"
                   % ",".join(sorted(schema_sink_keys.keys())))
        return {
            "identity_field": field,
            "source_key": "|".join(cands),
            "constructor_argument": field,
            "transform": ("y" if _any(ents, "call") else "-"),
            "default": ("y" if _any(ents, "const") else "-"),
            "status": status,
            "evidence_function": evidence_fn,
            "reason": reason,
        }
    if sink and tuning_named_write:
        ev = {"value_kind": "tuning"}
    elif not sink and has_any_write and _any(ents, "const") and not _any(ents, "call"):
        ev = {"value_kind": "default"}         # const, no call, no schema key
    elif not sink and _any(ents, "call") and not _any(ents, "const"):
        ev = {"value_kind": "helper"}          # no schema key: helper only
    elif not sink and _any(ents, "call") and _any(ents, "const"):
        ev = {"value_kind": "unknown"}         # both -> ambiguous
    elif not sink and _any(ents, "attr"):
        ev = {"value_kind": "unknown"}
    else:
        ev = {"value_kind": "unknown"}
    ev["has_tuning_sink"] = sink
    # `set_later` means truly REASSIGNED by a producer/transform AFTER a plain
    # default.  A bare LOAD_CONST default store is NOT set-later.  Calls/attrs on
    # the field indicate a possible transform assignment.
    ev["set_later"] = not (ev.get("value_kind") in ("default", "tuning")) and \
        bool(has_any_write)
    ev["some_ordinals_only"] = False
    ev["default_overrides_present_tuning"] = False
    status, reason = classify_provenance(ev)
    detail = ("writes=%s" % sorted(kinds) if kinds else "no-write")
    return {
        "identity_field": field,
        "source_key": "|".join(cands),
        "constructor_argument": field,
        "transform": ("y" if _any(ents, "call") else "-"),
        "default": ("y" if _any(ents, "const") else "-"),
        "status": status,
        "evidence_function": evidence_fn,
        "reason": "%s  [%s]" % (reason, detail),
    }


def _sink_keys_in_row(entry, cands, name_fn, text_fn):
    """Node @n present under an entry that is a tuning sink for this field.
    SUFFIX match: WW prefixes instance tuning keys with `animation_` (real
    location key == animation_locations), while the identity field name is the
    unprefixed tail (object_animation_clip_name).  A candidate is a sink when a
    node @n equals it or ends with `_<candidate>`.  Detection only -- never a
    value, and no hardcoded prefix list."""
    out = set()
    for nd in entry.iter():
        nm = name_fn(nd)
        if not nm:
            continue
        for cand in cands:
            if nm == cand or nm.endswith("_" + cand):
                out.add(nm)
    return out


def scan_roster_tuning(rows_el, name_fn, text_fn, probe_fields=PROBE_FIELDS):
    """Return (schema_sinks, ordinal_sinks).  schema_sinks[field] =
    {actual_node_key: count_of_rows_carrying_it}; ordinal_sinks[field] = list of
    row-indexes that carry a NON-EMPTY tuning value (never default-override those)."""
    schema_sinks = {}
    for field, cands, _cls in probe_fields:
        agg = {}
        for entry in rows_el:
            for nm in _sink_keys_in_row(entry, cands, name_fn, text_fn):
                agg[nm] = agg.get(nm, 0) + 1
        schema_sinks[field] = agg
    ordinal_sinks = {}
    for field, cands, _cls in probe_fields:
        carriers = []
        for ei, entry in enumerate(rows_el):
            if any(_sink_keys_in_row(entry, (cand,), name_fn, text_fn)
                   and any(text_fn(nd).strip() for nd in entry.iter()
                           if name_fn(nd) and (name_fn(nd) == cand
                                               or name_fn(nd).endswith("_" + cand)))
                   for cand in cands):
                carriers.append(ei)
        ordinal_sinks[field] = carriers
    return schema_sinks, ordinal_sinks


# --- main orchestration ----------------------------------------------------
LOADER_NAME = "animations_loader.pyc"
TARGET_PRODN = "_create_sex_animation_instance"
INSTANCE_MODS = {
    "SexAnimationInstance": "animation_instance.pyc",
    "SexAnimationActorInstance": "animation_instance.pyc",
    "SexAnimationPropInstance": "animation_instance.pyc",
}
OUT_FILE = "output/p32/p32_loader_origin_audit.txt"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="P32 exact loader-origin probe for the 8 FULL_CORPUS_ORIGIN_UNKNOWN "
                    "identity inputs (read-only; Windows xdis against real .ts4script).")
    ap.add_argument("source", help="WW_Nevely42 .package path (has WW_ANIM_XML)")
    ap.add_argument("--dir", required=True, help="Mods dir containing the .ts4script")
    ap.add_argument("--out-dir", default="output/p32")
    ap.add_argument("--no-roster", action="store_true",
                    help="skip 479-row roster per-ordinal tuning detection ")
    a = ap.parse_args(argv)

    # ---- deps ----
    # Reuse the proven source package reader + xml root builder from the P32
    # source-fixture module (no xdis needed on this side).
    try:
        import ww_p32_identifier_source_fixture as SF
    except Exception as ex:
        print("ERROR: 无法导入 ww_p32_identifier_source_fixture: %s" % ex,
              file=sys.stderr)
        return 6
    # xdis needed only for the .pyc loader/constructor disassembly.
    xdis_load = None
    try:
        import importlib
        xdis_load = getattr(
            importlib.import_module("xdis.load"), "load_module_from_file_object")
    except Exception:
        xdis_load = None
    XBytecode_mod = _imp_optional("xdis.disasm")
    if xdis_load is None or XBytecode_mod is None:
        print("ERROR: 缺依赖 xdis —— pip install xdis", file=sys.stderr)
        return 7

    import io as _io
    import zipfile as _zipfile

    src = Path(a.source)
    if not src.is_file():
        print("ERROR: 源不存在", file=sys.stderr)
        return 2
    d = Path(a.dir)
    if not d.is_dir():
        print("ERROR: --dir 不存在", file=sys.stderr)
        return 4
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- tuning schema scan over the WHOLE roster (all 479 rows) ----
    # Build the xml root the same proven way as the source-fixture extractor, then
    # walk EVERY row under <L n="animations_list"> and count how many rows carry
    # each of the 8 candidate keys as a tuning node.  This answers "which fields
    # are tuning-settable on some/all ordinals" per-row, NOT by assuming 318.
    try:
        idx = SF._read_index(src)
        ww = [e for e in idx.entries if e.type_id == SF.WW_ANIM_XML]
        if len(ww) != 1:
            raise RuntimeError("WW_ANIM_XML count != 1 (got %d)" % len(ww))
        ww_e = ww[0]
        body = SF._decompress(SF._read_body(src, ww_e))
        text = body.decode("utf-8", errors="replace")
        import xml.etree.ElementTree as ET
        root = ET.fromstring(text)
    except Exception as ex:
        print("ERROR: 读取 package xml 失败: %s" % ex, file=sys.stderr)
        return 3

    lists = [n for n in root.iter()
             if SF._el_tag(n) == "L" and SF._name(n) == SF.ENTRY_LIST_FIELD]
    if len(lists) != 1:
        print("ERROR: <L %s> count=%d (expected 1)" % (SF.ENTRY_LIST_FIELD, len(lists)),
              file=sys.stderr)
        return 3
    rows_el = [c for c in list(lists[0]) if SF._el_tag(c) == "U"]
    n_entries = len(rows_el)

    # schema tuning census (suffix-matched; detects per-row, never assumes 318)
    schema_sinks, ordinal_sinks = scan_roster_tuning(rows_el, SF._name, SF._text)

    # ---- locate loader .pyc bytes separately from animation_instance bytes ----
    import io as _io2
    scalars = {}
    ts4path = None
    for sp in [p for p in d.rglob("*.ts4script") if p.is_file()]:
        try:
            with _zipfile.ZipFile(sp) as z:
                for name in z.namelist():
                    leaf = Path(name).name
                    if leaf in (LOADER_NAME,) + tuple(set(INSTANCE_MODS.values())):
                        scalars.setdefault(name, z.read(name))
                        ts4path = sp
        except Exception:
            continue
    loader_bytes = None
    loader_member = None
    inst_bytes = None
    inst_member = None
    for name, b in scalars.items():
        if Path(name).name == LOADER_NAME:
            loader_bytes, loader_member = b, name
        else:
            inst_bytes, inst_member = b, name
    if loader_bytes is None:
        print("ERROR: 未在 .ts4script 内找到 %s" % LOADER_NAME, file=sys.stderr)
        return 5
    if ts4path is None:
        print("ERROR: 未定位 .ts4script", file=sys.stderr)
        return 5

    XBytecode = XBytecode_mod.Bytecode

    def load_co(pyc_bytes, label):
        try:
            res = xdis_load(_io2.BytesIO(pyc_bytes), filename=label)
        except Exception as ex:
            print("ERROR: xdis 解析 %s 失败: %s" % (label, ex), file=sys.stderr)
            return None, None
        ver = res[0]
        opc = get_opcode(ver)
        return opc, res[3]

    lopc, lco = load_co(loader_bytes, LOADER_NAME)
    if lco is None:
        return 6
    # ---- gather candidate code objects across loader + instance pyc ----
    candidates = []

    def collect_code(cobj, tag, seen):
        if id(cobj) in seen:
            return
        seen.add(id(cobj))
        candidates.append((cobj, tag))
        for sub in cobj.co_consts:
            if hasattr(sub, "co_name"):
                collect_code(sub, tag + "/" + (sub.co_name or "?"), seen)

    seen_loader = set()
    collect_code(lco, LOADER_NAME, seen_loader)
    seen_inst = set()
    if inst_bytes is not None:
        iopc, ico = load_co(inst_bytes, inst_member)
        if ico is None:
            return 6
        collect_code(ico, inst_member, seen_inst)

    # find the producer function among candidates (any module)
    fn = None
    for cobj, tag in candidates:
        if cobj.co_name == TARGET_PRODN:
            fn = (cobj, tag)
            break
    if fn is None:
        print("ERROR: 未找到函数 %s" % TARGET_PRODN, file=sys.stderr)
        return 8
    fn_cobj, fn_tag = fn
    lines_fn = list(XBytecode(fn_cobj, lopc))

    # ---- scan every candidate code object for STORE_ATTR / STORE onto the 8 ----
    # Evidence collector: finds (in any targeted code object) writes to an
    # instance/attr spelling matching a candidate field, with the value-kind of
    # the producing operand (via upward walk) + whether that operand symbol
    # literally equals a tuning candidate key.
    def store_target_attr(nm, field, cands):
        # suffix match mirrors schema-scan: catch both the bare identity field
        # and WW's `animation_`-prefixed spelling, plus dotted actor sub-fields.
        for cand in cands:
            if nm == cand or nm.endswith("_" + cand):
                return True
        # position_offset.<x|y|z> dotted attrs
        if any(cand in nm for cand in cands) and any(
                x in ("position_offset", "angle_offset", "facing", "facing_position_offset")
                for x in cands):
            return True
        return False

    def scan_for_stores(lines, tag, opc):
        hits = {}
        for i, it in enumerate(lines):
            attr = None
            if it.opname == "STORE_ATTR":
                attr = it.argrepr
            for field, cands, _cls in PROBE_FIELDS:
                if attr is None or not store_target_attr(attr, field, cands):
                    continue
                kind, det = _value_kind_of(lines, i)
                producer_is_tuning_name = bool(
                    det and any(ck in str(det) for ck in cands)) and schema_sinks.get(field)
                hits.setdefault(field, []).append({
                    "attr": attr, "kind": kind, "detail": det,
                    "tag": tag, "producer_is_tuning_name": producer_is_tuning_name,
                })
        return hits

    all_stores = {}
    for cobj, tag in candidates:
        opcx = lopc
        try:
            lines = list(XBytecode(cobj, opcx))
        except Exception:
            continue
        for fld, ents in scan_for_stores(lines, tag, opcx).items():
            all_stores.setdefault(fld, []).extend(ents)

    # ---- decide every field via the SINGLE tested decision fn ----
    rows = []
    for field, cands, cls in PROBE_FIELDS:
        rows.append(decide_field_evidence(
            field, cands, all_stores.get(field, []), schema_sinks.get(field)))

    csv_lines, summ = render_audit(rows)
    txt = []
    txt.append("=== P32 loader-origin exact probe (read-only) ===")
    txt.append("IDENTITY_FIELDS_ACCOUNTED=15/15  FIELD_NAME_UNKNOWN=0")
    txt.append("FULL_CORPUS_ORIGIN_UNKNOWN=8 (loader/constructor origin not yet closed for the 8 runtime-default fields)")
    txt.append("source=%s" % src.name)
    txt.append("ts4script=%s" % ts4path)
    txt.append("loader_member=%s bytes=%d  instance_member=%s bytes=%d"
               % (loader_member or "-", len(loader_bytes or b""),
                  inst_member or "-", len(inst_bytes or b"")))
    txt.append("target=""%s"" + SexAnimation*Instance.__init__ disasm" % TARGET_PRODN)
    txt.append("ENTRY_COUNT (rows under <L %s>) = %d" % (SF.ENTRY_LIST_FIELD, n_entries))
    # tuning schema vocabulary (the 8 keys present?)
    present_keys = {ck for fld, present in schema_sinks.items() for ck in present}
    txt.append("tuning_schema_keys_present (subset of the 8): %s"
               % (",".join(sorted(present_keys)) or "(none of the 8 field keys seen in xml node @n)"))
    txt.append("tuning_full_counts_of_8_keys: %s" % schema_sinks)
    # which ordinals carry a tuning value for each of the 8 fields (partial vs full)
    txt.append("per_ordinal_carriers (ordinals that carry a non-empty tuning value "
               "for that field):")
    for field, cands, _cls in PROBE_FIELDS:
        carriers = ordinal_sinks.get(field, [])
        kind = "FULL(%d/%d)" % (len(carriers), n_entries) if carriers and \
            len(carriers) == n_entries else (("PARTIAL(%d/%d)" % (len(carriers),
                                                                  n_entries)) if carriers else "NONE")
        tx = ",".join(str(c) for c in carriers[:40])
        if len(carriers) > 40:
            tx += ",...(total %d)" % len(carriers)
        txt.append("  %s -> %s  carriers=[%s]" % (field, kind, tx))
    txt.append("")
    txt.append("")
    txt.extend(summ)
    txt.append("")
    txt.append("ZERO_WRITE_TO_MODS=YES  ZERO_WRITE_TO_SAVES=YES  (read-only)")

    text_out = "\n".join(txt)
    out_path = out_dir / "p32_loader_origin_audit.txt"
    out_path.write_text(text_out, encoding="utf-8")
    with open(out_dir / "p32_loader_origin_audit.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerows(csv_lines)

    n_unk = sum(1 for r in rows if r["status"] == "UNKNOWN")
    print(text_out)
    print("OUT_TXT=%s" % out_path)
    print("GATE_ALL_PROVEN=%s" % ("YES" if n_unk == 0 else "NO"))
    print("P32_LOADER_ORIGIN_PROBE=DONE (read-only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
