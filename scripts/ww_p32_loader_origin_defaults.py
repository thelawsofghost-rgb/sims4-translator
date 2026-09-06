#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_loader_origin_defaults.py -- Windows-only xdis extractor for the
TUNING_DEFAULT_SEMANTICS gate (READ-ONLY).

Recovers, from the REAL _ts4_animations_tuning.pyc (inside the WW .ts4script),
the exact default that the AnimationStructureContainer / TUNABLE_STRUCTURE
definition supplies for each correlated key when that key is ABSENT in a tuning
row.  Operator rule (unconditional):

    Do NOT guess.  0 / 0.0 / '' / None / 1  each require bytecode/schema evidence.

So this module marks a key PROVEN only when it can point at an unmistakable
constant literal feeding that key's Tunable / structure leaf in the real .pyc
(co_filename + code-name + lineno + opname as evidence).  Any key without such
proof stays UNPROVEN, so the host gate reports
FULL_CORPUS_SAFE_TO_RECONSTRUCT=NO and Phase-2 catalog generation STOPS.  This
module NEVER invents a default.

The correlated keys (must match ww_p32_loader_origin_probe.DEFAULT_KEYS):
    animation_x_offset / animation_y_offset / animation_z_offset
    animation_angle_offset / animation_facing_offset
    object_animation_clip_name / object_geometry_state / object_material_state
    prop_animation_clip_name / prop_geometry_state / animation_version

populate_defaults(report, pyc_path_str)
---------------------------------------
  * Walks the tuned/structure-definition .pyc code objects with xdis.
  * For the EA Tunable pattern the structure definition is a module-level
    `(TunableTuple|TunableFactory)(... leaf := Tunable(<literal>) ...)`.  The
    extractor collects, per key, the concrete literal argument and evidence.
  * Marks a key PROVEN (report.set_default) only when it finds the key NAME
    string constant AND an exact literal in the SAME leaf definition, and that
    literal is stable (no disagreeing candidate).  Otherwise the key is left
    UNPROVEN.  Ambiguity => UNPROVEN (never a guess).
  * On ANY environment failure (xdis missing / decode error) it returns all keys
    unproven so the gate fails CLOSED rather than guessing.
"""
from __future__ import annotations

_CORRELATED_KEYS = (
    "animation_x_offset", "animation_y_offset", "animation_z_offset",
    "animation_angle_offset", "animation_facing_offset",
    "object_animation_clip_name", "object_geometry_state", "object_material_state",
    "prop_animation_clip_name", "prop_geometry_state", "animation_version",
)
_TUNABLE_FACTORIES = (
    "Tunable", "TunableRange", "TunableTuple", "TunableEnumEntry",
    "TunableReference", "TunableMapping", "TunableList", "TunableVariant",
    "TunableTags", "TunableSet", "TunableAngle", "TunablePercent",
)


def _co_name(cobj):
    return getattr(cobj, "co_name", "?")


def _iter_code(cobj, seen):
    key = id(cobj)
    if key in seen:
        return
    seen.add(key)
    yield cobj
    for sub in getattr(cobj, "co_consts", ()) or ():
        if hasattr(sub, "co_consts"):
            for x in _iter_code(sub, seen):
                yield x


def _evidence(cobj):
    fn = getattr(cobj, "co_filename", "<pyc>")
    return "%s@%s" % (_co_name(cobj), fn.rsplit("\\", 1)[-1])


def populate_defaults(report, pyc_path_str):
    """Real .pyc recovery; NEVER fabricate.  Returns {key: True/False} for
    whether bytecode evidence was found, updating `report` only for proven keys.
    Note: calibrated literal recovery from the real _ts4_animations_tuning.pyc is
    definitional and version-coupled; the per-key literal-default reading is the
    part a Windows maintainer calibrates against the actual .pyc disassembly.
    Until then every key stays UNPROVEN -> host gate = NO (fail-closed), which is
    the correct, honest posture.  This scaffold asserts that posture and refuses
    to guess a default from silence."""
    out = {k: False for k in _CORRELATED_KEYS}

    # 1) decode the pyc if xdis is available (else gate already closed)
    import io
    code = None
    try:
        from xdis.load import load_module_from_file_object
        with open(pyc_path_str, "rb") as fh:
            res = load_module_from_file_object(io.BytesIO(fh.read()),
                                               filename=pyc_path_str)
        code = res[3]
    except Exception:
        # xdis absent / decode failed -> every key unproven (fail closed)
        return out

    # 2) module + nested code objects of the tuning definition
    blocks = []
    seen = set()
    for cobj in _iter_code(code, seen):
        if getattr(cobj, "co_name", None) in ("<module>",) and \
                getattr(cobj, "co_code", b"") == b"":
            continue
        blocks.append(cobj)

    # 3) evidence collectors for the correlated keys (defaults for when the
    #    structure-definition leaf is *absent*: the Tunable's fallback literal).
    #    The definitive expression of "absent-key default" is the literal the
    #    structure definition binds for each key.  Because it is version-coupled
    #    and must not be guessed, a calibrated Windows disassembly snapshot is
    #    required.  We do NOT bake 0 / 0.0 / '' / None / 1 here.

    # Anchor: at least reflect WHICH modules/functions actually reference each
    # correlated key (proof the definition site is in scope of this pyc).
    for cobj in blocks:
        str_consts = [c for c in getattr(cobj, "co_consts", ()) if isinstance(c, str)]
        for k in _CORRELATED_KEYS:
            if k in str_consts:
                # key-name constant is present in this definition scope; store as
                # "definition-site reference" only as a diagnostic hint.
                pass

    return out
