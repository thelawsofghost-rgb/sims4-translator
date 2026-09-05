#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ww_p29e_build_ts4script.py --- pack P29-E runtime identity probe into a
.ts4script the Sims 4 game can auto-import.

REUSE / PROVENANCE
------------------
Mirrors the P29-A family builder pattern (ww_p29a_build_ts4script.py) that P29-C
validated on the real machine:
  * a .ts4script is a zip whose python members are *.pyc at the zip root;
    Sims 4 auto-imports top-level modules from *.ts4script placed in Mods.
  * the .pyc MUST be compiled by the python the game embeds.  We compile with
    whatever interpreter runs THIS script (on Dorothy's game-python it is the
    authoritative magic; on the sandbox it is only a structural round-trip).
  * fail-closed, read-only on source, writes only --out.

This specialty builder pins the exact member/module/probe names for the P29-E
probe and passes an assertable hook attribute to the pack verifier.

USAGE
  # structural round-trip (sandbox, native interpreter):
  python scripts/ww_p29e_build_ts4script.py \
      --src scripts/ww_p29e_picker_row_probe.py \
      --out dist/ww_p29e_picker_row_probe.ts4script

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

PROBE_MODULE = "ww_p29e_picker_row_probe"
PROBE_ATTR = "_hook_factory"


def _build(src_py, out_ts4, member):
    fd, cfile = tempfile.mkstemp(suffix=".pyc")
    os.close(fd)
    tmp_pyc = Path(cfile)
    try:
        import py_compile
        py_compile.compile(str(src_py), cfile=str(tmp_pyc), dfile=str(src_py),
                           optimize=0)
        data = tmp_pyc.read_bytes()
        if not data:
            raise SystemExit("[compile] empty pyc")
    finally:
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


def _verify_pack(out_ts4, member):
    """Import the packed member on THIS interpreter (autorun disabled) and assert the
    hook factory attribute exists; proves member/module layout, not game magic."""
    import subprocess
    import sys as _sys
    with tempfile.TemporaryDirectory() as tmpdir:
        pyc_path = os.path.join(tmpdir, os.path.basename(member))
        probe = os.path.join(tmpdir, "_probe.py")
        with open(probe, "w", encoding="utf-8") as f:
            f.write(
                "import os,sys,zipfile,importlib.util\n"
                "os.environ['WW_P29_DISABLE_AUTORUN']='1'\n"
                "os.environ['WW_P29E_DISABLE_AUTORUN']='1'\n"
                "z=zipfile.ZipFile(%r)\n"
                "open(%r,'wb').write(z.read(%r))\n"
                "spec=importlib.util.spec_from_file_location(%r,%r)\n"
                "m=importlib.util.module_from_spec(spec)\n"
                "sys.modules[%r]=m\n"
                "spec.loader.exec_module(m)\n"
                "assert hasattr(m,%r)\n"
                "print('PROBE_IMPORT=OK')\n"
                % (str(out_ts4), pyc_path, member, PROBE_MODULE, pyc_path,
                   PROBE_MODULE, PROBE_ATTR)
            )
        r = subprocess.run([_sys.executable, probe], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("probe import failed: "
                               + (r.stderr or r.stdout or "")[-400:])
    return True


def _fail(code_line, exc=None):
    print(code_line, flush=True)
    if exc is not None:
        _traceback.print_exc()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--member", default=PROBE_MODULE + ".pyc")
    a = ap.parse_args()
    src = Path(a.src)
    if not src.is_file():
        print("P29E_BUILD=FAIL_SRC_MISSING")
        return 1
    try:
        out = _build(src, a.out, a.member)
    except SystemExit as e:
        _fail("P29E_BUILD=FAIL_COMPILE %r" % (e,))
        return 1
    except Exception as e:
        _fail("P29E_BUILD=FAIL_COMPILE %r" % (e,), exc=e)
        return 1
    try:
        _verify_pack(out, a.member)
    except Exception as e:
        _fail("P29E_BUILD=FAIL_PACK_VERIFY %r" % (e,), exc=e)
        return 2
    print("P29E_BUILD=OK")
    print("OUT=%s" % out)
    print("MEMBER=%s" % a.member)
    print("BYTES=%d" % out.stat().st_size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
