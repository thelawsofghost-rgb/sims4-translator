#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29a_game_py.py --- P29-A: discover the game's TS4 Python ABI + a LOCAL runnable
CPython that can compile matching *.pyc (read-only, cross-platform where possible).

WHY (corrects the earlier wrong assumption):
  The Sims 4 does NOT ship a runnable python.exe that we can / should invoke.  It
  embeds a CPython runtime (python*_x64.dll).  A .dll is NOT a compiler -- the ONLY
  way to produce a .pyc the game will load is to run a real CPython interpreter whose
  importlib.util.MAGIC_NUMBER equals the game's.  So we never "compile with the dll";
  instead:
    1) READ the real pyc magic from a KNOWN game-loadable .pyc (a member of the live
       WW .ts4script -- the same bytecode WW runs, so it is authoritative).
       -> TARGET_PYC_MAGIC
    2) ENUMERATE locally runnable CPythons and their MAGIC_NUMBER.
    3) Require a local compiler whose LOCAL_PYC_MAGIC == TARGET_PYC_MAGIC.
    4) If none -> FAIL-CLOSED (no guessing, no downloads, no wrong-version compile).

Modes (subcommand):
  magic-from-pyc  --locate-mod <dirs...>   (WINDOWS deploy): find a known game-
       loadable .pyc under the given Mods/Data roots and print:
       TARGET_PYC_MAGIC=<hex>  TARGET_SRC=<file>:<member>  (exit 0)
       or TARGET_PYC_MAGIC=NONE + reasons (exit 1).
  compilers       (any host incl. sandbox): enumerate local runnable CPythons, print
       per line:  PATH<TAB>VER<TAB>MAGICHEX   (exit 0 always; caller decides).
  match           --target <hex>  (any host): run `compilers`, then print which, if
       any, compiler has MAGICHEX == target (case-insensitive):
       MATCH=PATH<TAB>VER   or  MATCH=NONE ; plus AVAILABLE_COUNT=n .
       A `--prefer <exe-or-name>` allows pinning one candidate (explicit override).

Examples:
  python scripts/ww_p29a_game_py.py compilers
  python scripts/ww_p29a_game_py.py magic-from-pyc --locate-mod "C:/Users/thela/Documents/Electronic Arts/The Sims 4/Mods"
  python scripts/ww_p29a_game_py.py match --target 550d0d0a

Everything ASCII; read-only on target files; the ONLY filesystem writes are temp dirs
made + removed inside this script (none under Mods / source / workspace).
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def _iter_zip_pyc(candidates):
    """Yield (zip_path, member_name) for .pyc members of .ts4script zips."""
    for c in candidates:
        p = Path(c)
        if not p.exists():
            continue
        if p.is_dir():
            for z in p.rglob("*.ts4script"):
                if z.is_file():
                    yield from _zip_members(z)
        elif p.is_file() and p.suffix.lower() in (".ts4script", ".zip"):
            yield from _zip_members(p)


def _zip_members(zp):
    out = []
    try:
        with zipfile.ZipFile(zp) as z:
            for n in z.namelist():
                if n.endswith(".pyc"):
                    out.append((zp, n))
    except (zipfile.BadZipFile, OSError):
        pass
    return out


def _read_pyc_magic_bytes(path, member=None):
    data = None
    try:
        if member is None:
            with open(path, "rb") as f:
                data = f.read(4)
        else:
            with zipfile.ZipFile(path) as z:
                data = z.read(member)[:4]
    except Exception:
        return None
    if data is None or len(data) < 4:
        return None
    return data.hex()


def _probe_one_v2(py):
    code = ("import sys, importlib.util; "
            "print(sys.version.split()[0]); "
            "print('%d.%d' % (sys.version_info[0], sys.version_info[1])); "
            "print(importlib.util.MAGIC_NUMBER.hex())")
    try:
        r = subprocess.run([py, "-c", code], capture_output=True,
                           text=True, timeout=45)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    lines = [(l or "").strip() for l in (r.stdout or "").splitlines()]
    if len(lines) < 3:
        return None
    ver, abi, magic = lines[0], lines[1], lines[2]
    if not (len(magic) == 8 and all(c in "0123456789abcdef" for c in magic.lower())):
        return None
    return ver, abi, magic.lower()


def _enumerate_compilers():
    """Return list of (path, ver, abi, magichex) for runnable local CPythons."""
    found = {}
    # 1) py launcher -0p list
    py = shutil.which("py")
    if py:
        try:
            r = subprocess.run([py, "-0p"], capture_output=True, text=True,
                               timeout=45)
            if r.returncode == 0:
                for ln in (r.stdout or "").splitlines():
                    ln = ln.strip()
                    # format:  -3.7        C:\Python37\python.exe
                    parts = ln.split(None, 1)
                    if len(parts) == 2:
                        exe = parts[1].strip().strip('"')
                        if exe.lower().endswith("python.exe"):
                            found.setdefault(exe, None)
        except Exception:
            pass
    # 2) bare python / python3 on PATH
    for cand in ("python", "python3"):
        w = shutil.which(cand)
        if w:
            found.setdefault(w, None)
    # 3) common install dirs
    for d in ("C:\\Python37", "C:\\Python38", "C:\\Python39", "C:\\Python310",
              "C:\\Python311", os.path.expandvars("%LOCALAPPDATA%\\Programs\\Python"),
              os.path.expandvars("%ProgramFiles%\\Python37"),
              os.path.expandvars("%ProgramFiles(x86)%\\Python37-32")):
        for exe in ("python.exe", "python3.exe"):
            c = os.path.join(d, exe)
            if os.path.isfile(c):
                found.setdefault(c, None)

    out = []
    for exe in found:
        res = _probe_one_v2(exe)
        if res:
            ver, abi, magic = res
            out.append((exe, ver, abi, magic))
    # dedupe by magic+abi: keep first
    seen = set()
    uniq = []
    for e in out:
        k = (e[3], e[2])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def _cmd_compilers(_a):
    comps = _enumerate_compilers()
    if not comps:
        print("AVAILABLE_PYTHONS=0")
        return 0
    for exe, ver, abi, magic in comps:
        print("PYTHON\t%s\t%s\t%s\t%s" % (exe, ver, abi, magic))
    print("AVAILABLE_PYTHONS=%d" % len(comps))
    return 0


def _cmd_match(a):
    target = (a.target or "").lower()
    comps = _enumerate_compilers()
    # honor an explicit prefer override (path or tag) -- still require its magic to match
    prefer = (a.prefer or "").lower()
    chosen = None
    for exe, ver, abi, magic in comps:
        if magic == target:
            hit = (prefer and (prefer in exe.lower() or prefer in ver.lower()
                               or prefer in abi.lower()))
            # first exact magic match wins unless a prefer narrowed it
            if chosen is None:
                chosen = (exe, ver, abi, magic)
            if hit and (chosen is None or chosen[3] != magic):
                chosen = (exe, ver, abi, magic)
    if chosen is None:
        print("MATCH=NONE")
        print("TARGET_PYC_MAGIC=%s" % target)
        print("AVAILABLE_PYTHONS=%d" % len(comps))
        for exe, ver, abi, magic in comps:
            print("HAVE\t%s\t%s\t%s\t%s" % (exe, ver, abi, magic))
        return 1  # FAIL-CLOSED: no matching compiler
    exe, ver, abi, magic = chosen
    print("MATCH=%s\t%s\t%s" % (exe, ver, abi))
    print("MATCH_PYC_MAGIC=%s" % magic)
    print("TARGET_PYC_MAGIC=%s" % target)
    print("PYC_MAGIC_MATCH=YES")
    return 0


def _cmd_magic(a):
    dirs = a.locate_mod
    # candidate .ts4script roots
    roots = [Path(d) for d in dirs]
    # Prefer the WW script member names we know the game loads
    priority_names = ["animations_loader.pyc", "animation_instance.pyc"]
    best = None
    for r in roots:
        for zp, member in _iter_zip_pyc([r]):
            base = member.split("/")[-1].lower()
            # pick a known-good member first; else first pyc
            pref = 0 if any(base == n for n in priority_names) else 1
            if best is None or pref < best[0]:
                magic = _read_pyc_magic_bytes(zp, member)
                if magic:
                    best = (pref, magic, str(zp), member)
    if best is None:
        print("TARGET_PYC_MAGIC=NONE")
        print("REASON=no loadable .pyc found under %r (checked *.ts4script zips)" % (dirs,))
        return 1
    _p, magic, zp, member = best
    print("TARGET_PYC_MAGIC=%s" % magic)
    print("TARGET_SRC=%s:%s" % (zp, member))
    return 0


def build_parser():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("compilers")
    p.set_defaults(fn=_cmd_compilers)
    p = sub.add_parser("match")
    p.add_argument("--target", required=True)
    p.add_argument("--prefer", default="")
    p.set_defaults(fn=_cmd_match)
    p = sub.add_parser("magic-from-pyc")
    p.add_argument("--locate-mod", nargs="+", required=True)
    p.set_defaults(fn=_cmd_magic)
    return ap


if __name__ == "__main__":
    ap = build_parser()
    try:
        a = ap.parse_args()
    except SystemExit:
        raise
    sys.exit(a.fn(a) if a.fn else 1)
