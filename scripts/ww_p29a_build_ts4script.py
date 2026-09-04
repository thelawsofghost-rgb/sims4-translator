#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29a_build_ts4script.py --- pack P29-A debug hook source into a .ts4script.

A .ts4script is a zip archive whose python members are stored as *.pyc at the
zip root (for a top-level module) or under the package folders.  We ship the
mod as a single top-level module  ww_p29a_mod.py  ->  member  ww_p29a_mod.pyc
at the zip root.  Sims 4 auto-imports top-level modules from *.ts4script in Mods.

To be loadable by the game, the *.pyc must be compiled by the SAME python the
game embeds (Sims 4 uses its own embedded CPython).  We therefore compile with
whatever interpreter runs this script (Windows game-python or sandbox python);
we do NOT cross-version forge magic numbers.

USAGE
  # structural round-trip (sandbox, native interpreter):
  python scripts/ww_p29a_build_ts4script.py \
      --src scripts/ww_p29a_mod.py \
      --out dist/ww_p29a_debug.ts4script

EXIT
  0 = built + self-import verified (member imports on THIS interpreter)
  1 = compile failed
  2 = self-import of packed member failed
fail-closed, read-only on source, writes only --out.
"""
import argparse
import os
import sys
import tempfile
import traceback as _traceback
import zipfile
from pathlib import Path

# Default member name for the P29-A debug mod (backward compatible).  A caller may
# override --module-name / --probe-attr (e.g. the P29-TUNING mod family).
ZIP_MEMBER = "ww_p29a_mod.pyc"


def verify_pack(out_ts4, src_py, member=None, probe_attr="_hook_factory",
                probe_mod="ww_p29_tuning_mod_probe"):
    """Confirm the packed member is a structurally importable module on THIS
    interpreter by importing it in a subprocess with autorun disabled.  This
    proves layout/member correctness; the final loadability depends on the pyc
    magic matching the game python (handled by building on game python)."""
    member = member or ZIP_MEMBER
    import subprocess
    import sys as _sys
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmpdir:
        pyc_path = os.path.join(tmpdir, os.path.basename(member))
        probe = os.path.join(tmpdir, "_probe.py")
        with open(probe, "w", encoding="utf-8") as f:
            f.write(
                "import os,sys,zipfile,importlib.util\n"
                "os.environ['WW_P29_DISABLE_AUTORUN']='1'\n"
                "os.environ['WW_P29A_DISABLE_AUTORUN']='1'\n"
                "z=zipfile.ZipFile(%r)\n"
                "open(%r,'wb').write(z.read(%r))\n"
                "spec=importlib.util.spec_from_file_location(%r,%r)\n"
                "m=importlib.util.module_from_spec(spec)\n"
                "sys.modules[%r]=m\n"
                "spec.loader.exec_module(m)\n"
                "assert hasattr(m,%r)\n"
                "print('PROBE_IMPORT=OK')\n"
                % (str(out_ts4), pyc_path, member, probe_mod, pyc_path,
                   probe_mod, probe_attr)
            )
        r = subprocess.run([_sys.executable, probe], capture_output=True,
                           text=True)
        if r.returncode != 0:
            raise RuntimeError("probe import failed: "
                               + (r.stderr or r.stdout or "")[-400:])
    return True


def build(src_py, out_ts4, member=None):
    """Compile src_py with THIS interpreter into a ts4script zip."""
    member = member or ZIP_MEMBER
    fd, cfile = tempfile.mkstemp(suffix=".pyc")
    os.close(fd)
    tmp_pyc = Path(cfile)
    try:
        import py_compile
        py_compile.compile(str(src_py), cfile=str(tmp_pyc), dfile=src_py.name,
                           optimize=0)
        data = tmp_pyc.read_bytes()
        if not data:
            raise SystemExit("[compile] empty pyc")
    finally:
        # Python-3.7-safe file remove (Path.unlink(missing_ok=...) is 3.8+ only).
        try:
            if tmp_pyc.exists():
                tmp_pyc.unlink()
        except OSError:
            pass

    out = Path(out_ts4)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(out), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, data)
    return out


def _fail(code_line, exc=None):
    """Print a truthful P29A_BUILD= line to stdout AND, when an exception is
    present, the full traceback to STDERR so the wrapper never sees only an
    empty stderr.  We intentionally do not swallow the cause."""
    print(code_line, flush=True)
    if exc is not None:
        _traceback.print_exc()  # -> stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="path to the mod .py")
    ap.add_argument("--out", required=True, help="path to output .ts4script")
    ap.add_argument("--member", default=ZIP_MEMBER,
                    help="zip member name for the .pyc (module name + .pyc)")
    ap.add_argument("--probe-attr", default="_hook_factory",
                    help="attribute to assert exists after probe import")
    ap.add_argument("--probe-mod", default="ww_p29_mod_probe",
                    help="module name to import the member under for the probe")
    a = ap.parse_args()

    src = Path(a.src)
    if not src.is_file():
        print("P29A_BUILD=FAIL_SRC_MISSING")
        return 1
    try:
        out = build(src, a.out, member=a.member)
    except SystemExit as e:
        _fail("P29A_BUILD=FAIL_COMPILE %r" % (e,))
        return 1
    except Exception as e:
        _fail("P29A_BUILD=FAIL_COMPILE %r" % (e,), exc=e)
        return 1

    try:
        verify_pack(out, src, member=a.member, probe_attr=a.probe_attr,
                    probe_mod=a.probe_mod)
    except Exception as e:
        _fail("P29A_BUILD=FAIL_PACK_VERIFY %r" % (e,), exc=e)
        return 2

    print("P29A_BUILD=OK")
    print("OUT=%s" % out)
    print("MEMBER=%s" % a.member)
    print("BYTES=%d" % out.stat().st_size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
