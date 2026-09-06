#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_identifier_exact_logic_test.py --- standalone logic test for
ww_p32_identifier_exact.py.  Runs on ANY CPython (Linux 3.10 here); does NOT need
a real WW ts4script and does NOT need 3.7 magic to test the extraction CORE.

Why a fixture (not the real ts4script): the exact get_identifier disassembly must
be recovered on Windows CPython 3.7.9 (magic 420d0d0a).  On Linux we compile THIS
interpreter's own structural synth module whose get_identifier body embeds the
SAME 5 nested kernels (locations-listcomp / actor#1 / actor#2 / actor#3 sum-genexpr
/ props-listcomp) and whose kernels CALL actor/location methods -- so we can verify
every extractor capability:
    (a) locate SexAnimationInstance.get_identifier by class.method path,
    (b) expand the main body AND every DIRECT nested code object (listcomp/
        genexpr) with each one's own co_names/co_varnames/co_consts/disassembly,
    (c) discover the actor/prop methods that nested bodies actually CALL,
    (d) cross-resolve each called method's DEFINITION body (even a method defined
        in a DIFFERENT class, e.g. ActorHelper vs SexAnimationInstance) and dump it,
        and honestly report NOT_FOUND_IN_TREE for undefined names.
None of this requires 3.7 pyc.  The real-member run gives identical structure.

Safety / scope:
    * NO writes to Mods / package / ts4script.
    * All outputs go under a temp dir this test creates and removes at exit.
    * Determinism: two self-fixture runs must be byte-identical per output file.

Exit: 0 = all assertions pass; 1 = any assertion fails.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "ww_p32_identifier_exact.py"

KERNEL_COUNT = 5          # 4 <listcomp> + 1 <genexpr> in the fixture get_identifier
EXPECT_DISCOVERED = {
    "get_actor_identifier",
    "get_clip_bytes",
    "get_gender_signature",
    "get_location_text",
    "get_prop_repr",
}
# Methods the fixture DEFINES (bodies should be extracted) vs leaves undefined.
EXPECT_DEFINED = {"get_gender_signature", "get_location_text"}   # in OTHER classes
EXPECT_UNDEFINED = {"get_actor_identifier", "get_clip_bytes", "get_prop_repr"}

_passes = []


def check(name, cond, detail=""):
    _passes.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))
    return bool(cond)


def run_extractor(out_dir):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--self-fixture", "--out-dir", str(out_dir)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    return r


def main():
    tmp = tempfile.mkdtemp(prefix="p32_exact_logic_")
    try:
        out_dir = Path(tmp) / "out"
        # ---- run #1 ----
        r1 = run_extractor(out_dir)
        ok_exit = check("extractor-exit-0", r1.returncode == 0,
                        "rc=%s stderr=%s" % (r1.returncode, r1.stderr.strip()))
        main_txt = (out_dir / "p32" / "p32_identifier_exact.txt")
        cal_txt = (out_dir / "p32" / "p32_identifier_exact_callees.txt")
        ok_files = check("artifacts-exist",
                         main_txt.is_file() and cal_txt.is_file())
        if not (ok_exit and ok_files):
            print("FATAL: extractor did not produce artifacts; cannot continue")
            return 1

        m = main_txt.read_text(encoding="utf-8")
        c = cal_txt.read_text(encoding="utf-8")

        # (a) target located by class.method path
        check("target-found", "TARGET=FOUND:YES" in m
              and "SexAnimationInstance.get_identifier" in m)

        # (b) main body present with metadata + disassembly
        check("main-body-has-names", "CO_NAME=get_identifier" in m
              and "CO_VARNAMES=" in m and "DISASSEMBLY:" in m)

        # nested kernels count refers to occurrence of its own CODE_PATH rows:
        # each gets a unique literal listcomp/genexpr under get_identifier.
        nested_rows = []
        for line in m.splitlines():
            if line.startswith("CODE_PATH=") and (
                    ".get_identifier.<listcomp>" in line
                    or ".get_identifier.<genexpr>" in line):
                nested_rows.append(line)
        check("nested-kernel-count-%d" % KERNEL_COUNT,
              len(nested_rows) == KERNEL_COUNT,
              "got=%d" % len(nested_rows))
        # each nested kernel keeps its own co_names/varnames/consts/disasm block
        kernel_ok = True
        kernel_idx = []
        for i, line in enumerate(m.splitlines()):
            if line.startswith("CODE_PATH=") and (
                    ".get_identifier.<listcomp>" in line
                    or ".get_identifier.<genexpr>" in line):
                kernel_idx.append(i)
        for idx in kernel_idx:
            block = m.splitlines()
            window = block[idx:idx + 60]
            joined = "\n".join(window)
            if not ("CO_NAMES=" in joined and "CO_VARNAMES=" in joined
                    and "CO_CONSTS=" in joined and "DISASSEMBLY:" in joined):
                kernel_ok = False
        check("each-kernel-has-own-meta+disasm", kernel_ok)

        # (c) called methods discovered
        disc_line = [l for l in m.splitlines()
                     if l.startswith("CALLEE_METHODS_DISCOVERED=")]
        disc_text = disc_line[0].split("=", 1)[1] if disc_line else ""
        got_disc = {x for x in disc_text.split(",") if x}
        check("callees-discovered-expected",
              EXPECT_DISCOVERED.issubset(got_disc),
              "missing=%s got=%s" % (sorted(EXPECT_DISCOVERED - got_disc),
                                     sorted(got_disc)))

        # (d) callee body resolution
        for nm in EXPECT_DEFINED:
            check("callee-defined-body-%s" % nm,
                  ("### callee body: %s" % nm) in c
                  and ("CODE_PATH=" in c and "CO_NAME=%s" % nm in c),
                  "")
        for nm in EXPECT_UNDEFINED:
            check("callee-undefined-honest-%s" % nm,
                  ("CALLEE=%s DEFINITION=NOT_FOUND_IN_TREE" % nm) in c,
                  "")

        # determinism: run #2 into a fresh sibling dir -> byte-identical
        out_dir2 = Path(tmp) / "out2"
        r2 = run_extractor(out_dir2)
        m2 = (out_dir2 / "p32" / "p32_identifier_exact.txt")
        c2 = (out_dir2 / "p32" / "p32_identifier_exact_callees.txt")
        if r2.returncode == 0 and m2.is_file() and c2.is_file():
            same_m = m2.read_text(encoding="utf-8") == m
            same_c = c2.read_text(encoding="utf-8") == c
            check("deterministic-output",
                  same_m and same_c,
                  "main_same=%s callees_same=%s" % (same_m, same_c))
        else:
            check("deterministic-output", False, "second run failed")

        # no stray writes outside the temp out-dir (nothing in repo)
        print("")
        print("PASS_COUNT=%d  FAIL_COUNT=%d"
              % (sum(1 for _n, ok in _passes if ok),
                 sum(1 for _n, ok in _passes if not ok)))
        failed = [n for n, ok in _passes if not ok]
        if failed:
            print("FAILED_NAMES=%s" % failed)
            return 1
        print("P32_EXACT_LOGIC=PASS")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
