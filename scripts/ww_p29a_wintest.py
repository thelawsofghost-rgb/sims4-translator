#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29a_wintest.py --- OFFLINE P29-A gate (runs on sandbox OR Windows).

Proves the P29-A artifacts are internally consistent WITHOUT a live TS4+WW:
  1. static_check on the hook source        (safety contract)
  2. logic_test  wrapping semantics         (RAW_ARG/INSTANCE_DISPLAY_*/hash,
                                             TEST299 vs OLD, fail-closed restore)
  3. build_ts4script round-trip             (source py -> .ts4script member; verify
                                             the member imports on THIS interpreter)

It does NOT and CANNOT prove the in-game HOOK_INSTALLED=YES (that requires TS4+WW
on a real machine).  That single fact is returned by Dorothy via the deploy log.

Exit: 0 = all offline PASS.  Non-zero = first failing gate.
"""
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
MOD = SCRIPTS / "ww_p29a_mod.py"
TS4_OUT = Path(os.environ.get("TMPDIR", "/tmp")) / "ww_p29a_debug.ts4script"


def run(label, cmd):
    print("\n=== %s ===" % label)
    r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
    out = (r.stdout or "") + (("\n[stderr] " + r.stderr) if r.stderr else "")
    print(out.strip())
    return r.returncode


def main():
    codes = {}
    codes["STATIC"] = run("STATIC_CHECK",
                          [sys.executable, str(SCRIPTS / "ww_p29a_static_check.py")])
    codes["LOGIC"] = run("LOGIC_TEST",
                         [sys.executable, str(SCRIPTS / "ww_p29a_logic_test.py")])
    codes["BUILD"] = run("BUILD_ROUNDTRIP",
                         [sys.executable, str(SCRIPTS / "ww_p29a_build_ts4script.py"),
                          "--src", str(MOD), "--out", str(TS4_OUT)])
    print("\n=== P29A WINTEST SUMMARY ===")
    for k, v in codes.items():
        print("  %s=%s" % (k, "PASS" if v == 0 else "FAIL(%d)" % v))
    if all(v == 0 for v in codes.values()):
        print("P29A_WINTEST=PASS")
        return 0
    print("P29A_WINTEST=FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
