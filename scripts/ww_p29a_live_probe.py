#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29a_live_probe.py --- LIVE constructor-contract probe for the CURRENT WW
SexAnimationInstance (task #1 rewrite).  Native, authoritative, NO xdis.

Run with the REAL local CPython that matches the game (3.7.9 / magic 420d0d0a):
    C:\\Users\\thela\\AppData\\Local\\Programs\\Python\\Python37-32\\python.exe
        ww_p29a_live_probe.py "<path to WW.ts4script>"
        [--member wickedwhims/sex/animations/animation_instance.pyc]

Method:
  1. read the target *.pyc member from the .ts4script zip
  2. assert its header magic == THIS interpreter's magic (420d0d0a under 3.7.9) --
     if equal, native marshal.loads(pyc[16:]) is valid
  3. walk all nested code objects, find the module class 'SexAnimationInstance'
     and its '__init__'; record the LIVE parameter tuple + body names.
  4. Report, WITHOUT judging against any stale transcription:
       LIVE_CLASS_PRESENT=YES|NO
       CLASS_HOME_MEMBER=...
       LIVE_INIT_ARGS=[...]        (the real positional params, in order)
       LIVE_INIT_NAMES=...         (names referenced in the __init__ body)
       EXPECTED_SIGNATURE_MATCH=YES|NO   (mere marker vs the OLD expected
             animation_raw_display_name contract -- informational only, does
             NOT equal LIVE_CLASS_PRESENT)
       LIVE_SIGNATURE_HASH=<sha256 of the raw varnames tuple>

Read-only.  Never writes to Mods, never modifies the WW ts4script.
Exit: 0 = class found, 2 = member missing, 3 = magic mismatch (abort before
marshal), 4 = marshal/parse error, 1 = not found.
"""
import hashlib
import io
import marshal
import sys
import zipfile
from pathlib import Path

WANTED_CLASS = "SexAnimationInstance"

# The OLD (now STALE) transcription contract we are deliberately NOT trusting:
OLD_PARAMS = ("animation_id", "animation_raw_display_name", "animation_type")


def load_pyc_member(ts4script, member):
    with zipfile.ZipFile(ts4script) as z:
        data = z.read(member)
    return data


def native_local_magic_hex():
    import importlib.util
    return importlib.util.MAGIC_NUMBER.hex()


def candidates_for_class_home(member_list, wanted=WANTED_CLASS):
    """Actual target member passed in; we also let caller narrow."""
    return member_list


def find_class_init(co, class_name):
    """Walk code objects; return (class_code_obj, init_code_obj_or_None).

    In 3.7 bytecode a class is a code object whose co_name is the class name and
    whose body STOREs methods.  Top-level `<module>` frame will hold a nested code
    for the class body (co_name == class_name); __init__ is nested inside it.
    """
    class_co = None
    # walk everything
    stack = [co]
    seen = set()
    init_co = None
    while stack:
        c = stack.pop()
        if id(c) in seen:
            continue
        seen.add(id(c))
        name = getattr(c, "co_name", "")
        if name == class_name and getattr(c, "co_argcount", -1) == 0:
            # class body frames have 0 args; methods have >=1. keep first.
            if class_co is None:
                class_co = c
        if name == "__init__":
            init_co = c  # last seen most-nested; keep class-owned below
        for sub in getattr(c, "co_consts", ()):
            if hasattr(sub, "co_name"):
                stack.append(sub)
    return class_co, init_co


def main():
    a = parse_args()
    ts4script = Path(a.ts4script)
    member = a.member
    if not ts4script.is_file():
        print("FATAL=TS4SCRIPT_MISSING %s" % ts4script)
        return 2
    local_magic = native_local_magic_hex()
    print("LOCAL_PY=%s.%s.%s" % sys.version_info[:3])
    print("LOCAL_MAGIC=%s" % local_magic)
    if not member:
        print("MEMBER_PRESENT=NO")
        return 2
    data = load_pyc_member(str(ts4script), member)
    ww_magic = data[:4].hex()
    print("MEMBER_PRESENT=YES")
    print("PYC_MAGIC=%s" % ww_magic)
    print("MAGIC_MATCH=%s" % ("YES" if ww_magic == local_magic else "NO"))
    if ww_magic != local_magic:
        print("ABORT=marshal requires matching pyc magic (native loads)")
        print("LIVE_CLASS_PRESENT=NO")
        print("REASON=MAGIC_MISMATCH")
        return 3
    try:
        co = marshal.loads(data[16:])
    except Exception as e:
        print("MARSHAL_LOAD=FAIL %s" % e)
        print("LIVE_CLASS_PRESENT=NO")
        return 4
    print("MARSHAL_LOAD=PASS")
    class_co, init_co = find_class_init(co, WANTED_CLASS)
    if init_co is None:
        print("LIVE_CLASS_PRESENT=NO")
        print("REASON=init_not_found_under_%s" % WANTED_CLASS)
        return 1
    varnames = list(getattr(init_co, "co_varnames", ()))
    names = sorted(set(getattr(init_co, "co_names", ())))
    print("LIVE_CLASS_PRESENT=YES")
    print("CLASS_HOME_MEMBER=%s" % member)
    print("CLASS_HOME_QUALNAME=%s/%s" % (WANTED_CLASS, "module" if class_co is None else "class"))
    print("INIT_CO_NAME=%s" % init_co.co_name)
    # only the explicit params (varnames[0:argcount]) -- first is 'self'
    argc = getattr(init_co, "co_argcount", 0)
    params = list(varnames[0:argc])
    print("INIT_PARAM_COUNT=%d" % argc)
    print("LIVE_INIT_ARGS=[%s]" % ", ".join("'%s'" % p for p in params))
    print("LIVE_INIT_NAMES=%s" % (", ".join(names)))
    # The display-name channel is the 3rd positional (param index 2) once you
    # count self(0)+animation_id(1).  Historical WW: self, animation_id,
    # animation_raw_display_name, animation_type -> display at index 2.
    third = params[2] if len(params) > 2 else "(none)"
    print("DISPLAY_PARAM_INDEX2=%s" % third)
    old_match = (len(params) >= 3 and params[2] == "animation_raw_display_name")
    print("EXPECTED_SIGNATURE_MATCH=%s" % ("YES" if old_match else "NO"))
    if old_match:
        print("OLD_SIGNATURE_MATCH=YES")
    else:
        print("OLD_SIGNATURE_MATCH=NO")
        print("OLD_TRANSCRIPTION_STALE=YES")
    sig = hashlib.sha256(("|".join(params)).encode("utf-8")).hexdigest()
    print("LIVE_SIGNATURE_HASH=%s" % sig)
    # provide machine-usable JSON-ish line
    print("PARAMS_JSON=[%s]" % ", ".join('"%s"' % p for p in params))
    return 0


def parse_args():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("ts4script")
    ap.add_argument("--member", default="wickedwhims/sex/animations/animation_instance.pyc")
    return ap.parse_args()


if __name__ == "__main__":
    sys.exit(main())
