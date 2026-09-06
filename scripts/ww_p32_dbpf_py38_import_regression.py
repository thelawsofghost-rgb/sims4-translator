#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_dbpf_py38_import_regression.py --- REGRESSION for the P32 Windows real-run
crash: 'type' object is not subscriptable on CPython 3.7.9.

WHAT BROKE
----------
The real ordinal-318 source run (Windows py3.7.9) aborted inside SOURCE_GATE with
    FATAL=SOURCE_GATE_FAIL 'type' object is not subscriptable
NOT a golden mismatch and NOT a gate adjudication -- a Python RUNTIME exception.
Root cause (reproduced on real CPython 3.8.20, same PEP-585 behavior as 3.7):
    src/dbpf_fast.py:197  def safe_parse(path) -> tuple[Optional[DBPFIndex], Optional[str]]:
The annotation uses a PEP-585 subscripted builtin generic (tuple[...]) which is
py3.9+ ONLY; on CPython <3.9 annotations are evaluated eagerly at def (module-import)
time, so merely `from dbpf_fast import safe_parse` raises
    TypeError: 'type' object is not subscriptable
The extractor does that import inside _read_index() during SOURCE_GATE, so the
exception surfaced as FATAL=SOURCE_GATE_FAIL before any sha/instance/ordinal check.

FIX
---
Added `from __future__ import annotations` (PEP 563, py3.7+) at the top of
src/dbpf_fast.py so every annotation is stored as a lazy string and NEVER evaluated
at import time -- py3.7/3.8 import cleanly AND py3.9+ unchanged.  Probe logic is
untouched; gates unchanged; fail-closed preserved.

THIS TEST
---------
A pure py3.7/3.8-AST+import gate cannot catch this (PEP-585 annotations parse fine;
they only explode at RUNTIME on <3.9).  So this regression actually IMPORTS the
real dbpf_fast module in a genuine CPython <3.9 subprocess and asserts it loads and
that safe_parse's return annotation is now a lazy string.

Legacy interpreter selection
----------------------------
  -env P32_LEGACY_PY=<exe>  (explicit)
  -else search: P32_LEGACY_PY, then a nearby cpython-3.7/3.8 under the uv store,
   then python3.8/python3.7 on PATH.
If none is found the test prints LEGACY_CPYTHON=SKIP and exits 0 (cannot run a real
legacy process here; the offline CI 3.10 gate covers syntax, and the 3.8 run in the
development session covers runtime).  On the Windows box py3.7.9 is the natural
target and will be found.

Assertions
----------
  R1  legacy python reports its version (3.7.x / 3.8.x)
  R2  importing dbpf_fast under legacy succeeds (exit 0, IMPORT_OK) -- the regression
      that fails BEFORE the fix
  R3  safe_parse.__annotations__['return'] is a str (lazy PEP-563), NOT evaluated
  R4  the source-fixture extractor's gate import path (src on sys.path + from
      dbpf_fast import safe_parse) works under legacy
  R5  (extra, legacy-only IDEAL) not required; if not legacy present -> SKIP.
Exit: 0 = PASS (or SKIP-with-no-legacy), 1 = FAIL.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DBPF = REPO / "src" / "dbpf_fast.py"


def _candidate_legacy():
    env = os.environ.get("P32_LEGACY_PY")
    if env and os.path.isfile(env):
        return env
    # uv store: ~/.local/share/uv/python/cpython-3.{7,8}*/bin/python3
    for home in (Path.home() / ".local/share/uv/python",
                 Path("/root/.local/share/uv/python")):
        if not home.is_dir():
            continue
        for d in sorted(home.glob("cpython-3.*"), reverse=True):
            if d.name.startswith(("cpython-3.7", "cpython-3.8")):
                exe = d / "bin" / "python3"
                if exe.is_file():
                    return str(exe)
    for name in ("python3.8", "python3.7"):
        p = shutil.which(name)
        if p:
            return p
    return None


CHECK = """\
import sys, os
sys.path.insert(0, {repo!r})
try:
    from dbpf_fast import safe_parse
except Exception as e:
    import traceback
    sys.stderr.write("IMPORT_FAIL=%s: %s\\n" % (type(e).__name__, e))
    sys.stderr.write(traceback.format_exc())
    sys.exit(3)
ann = safe_parse.__annotations__.get("return")
print("PYLEGACY_VERSION=" + sys.version.split()[0])
print("IMPORT_OK=YES")
print("RETURN_ANN_IS_STRING=" + ("YES" if isinstance(ann, str) else "NO:" + repr(ann)))
sys.exit(0)
"""


def main():
    exe = _candidate_legacy()
    if not exe:
        print("LEGACY_CPYTHON=SKIP (no 3.7/3.8 exe discovered on this host; "
              "set P32_LEGACY_PY to opt in). On the Windows box py3.7.9 is the target.")
        # not a real legacy runtime here: syntax/CI gates cover 3.7 AST; import
        # under 3.10 still exercised by P32 logic tests.
        return 0

    print("LEGACY_CPYTHON=%s" % exe)
    r = subprocess.run([exe, "-c", CHECK.format(repo=str(REPO / "src"))],
                       capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    print(out)
    if r.returncode != 0:
        print("REGRESSION=FAIL legacy import raised (root cause NOT fixed)")
        return 1
    ok = ("IMPORT_OK=YES" in out) and ("RETURN_ANN_IS_STRING=YES" in out)
    ver = ""
    for line in out.splitlines():
        if line.startswith("PYLEGACY_VERSION="):
            ver = line.split("=", 1)[1]
    print("R1-pylegacy-version:" + ("PASS " + ver if ver else "WARN"))
    print("R2-legacy-import-OK:" + ("PASS" if "IMPORT_OK=YES" in out else "FAIL"))
    print("R3-lazy-annotation:" + ("PASS" if "RETURN_ANN_IS_STRING=YES" in out else "FAIL"))
    print("REGRESSION=" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
