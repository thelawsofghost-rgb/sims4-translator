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
import re
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


REGEX_SINGLE_DASH = re.compile(r'"-py37"|\s-py37(\s|\))')


def livecls_mechanism():
    """Mechanism check for ww_p29a_live_class_probe.py (task #1 re-verification).

    We cannot ship Dorothy's real WW ts4script here, so we prove the PROBE logic
    on a synthesized fixture: a member animation_instance.pyc whose SexAnimation…
    Instance.__init__ carries exactly (animation_id, animation_raw_display_name,
    animation_type) must be reported LIVE_CLASS_MODULE + CONFIRMED=YES, while a
    distractor-only ts4script must come back CONFIRMED=NO -- so on the real machine
    a CONFIRMED=NO genuinely means "the live WW does not expose that path yet".
    Returns 0 on PASS."""
    import py_compile
    import tempfile
    import zipfile
    import textwrap
    probe = SCRIPTS / "ww_p29a_live_class_probe.py"
    d = tempfile.mkdtemp(prefix="wwlivecls_")
    try:
        def _member(val_name, body, dfile, z, prefix):
            src = "class %s:\n    def __init__(self, animation_id, animation_raw_display_name, animation_type):\n        self.animation_id = animation_id\n        self.display_name = animation_raw_display_name\n" % val_name
            if body:
                src = body
            m = os.path.join(d, prefix + ".py")
            with open(m, "w") as fh:
                fh.write(src)
            c = os.path.join(d, prefix + ".pyc")
            py_compile.compile(m, cfile=c, dfile=dfile)
            z.write(c, dfile)
        # positive fixture
        zp = os.path.join(d, "pos.ts4script")
        with zipfile.ZipFile(zp, "w") as z:
            _member("SexAnimationInstance", None,
                    "wickedwhims/sex/animations/animation_instance.pyc", z, "a")
        rp = subprocess.run([sys.executable, str(probe), zp],
                            cwd=str(REPO), capture_output=True, text=True)
        op = rp.stdout or ""
        print((op or "").strip())
        if rp.returncode != 0 or "LIVE_CLASS_CONFIRMED=YES" not in op:
            print("LIVECLS_POSITIVE=FAIL (probe missed a target-shaped member)")
            return 1
        if "animation_instance.pyc" not in op:
            print("LIVECLS_POSITIVE=FAIL (wrong module reported)")
            return 1
        print("LIVECLS_POSITIVE=PASS")
        # negative fixture: only a non-matching member
        zn = os.path.join(d, "neg.ts4script")
        with zipfile.ZipFile(zn, "w") as z:
            _member("Z", "class Z:\n    def __init__(self, x): self.x = x\n",
                    "wickedwhims/sex/animations/animations_loader.pyc", z, "l")
        rn = subprocess.run([sys.executable, str(probe), zn],
                            cwd=str(REPO), capture_output=True, text=True)
        on = rn.stdout or ""
        if rn.returncode == 0 or "LIVE_CLASS_CONFIRMED=NO" not in on:
            print("LIVECLS_NEGATIVE=FAIL (probe false-confirmed a non-target)")
            return 1
        print("LIVECLS_NEGATIVE=PASS")
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
    print("LIVECLS=PASS (probe mechanism OK; real answer requires Dorothy's live WW)")
    return 0


def py37_argv():
    """Assert the PowerShell wrapper assembles the DOUBLE-dash '--py37' flag and
    that the exact argv shape `--py37 <py37.exe> <files...>` parses cleanly.

    Regression for the 4th real-machine deploy: build_on_win passed '-py37'
    (single dash) which argparse rejects ('unrecognized arguments: -py37', exit 2),
    dying at the 3a. py3.7 compat gate.  We check BOTH:
      (a) no P29 ps1 contains a single-dash '-py37' invocation (pure text) and
          every gate PyArgs uses '--py37'; and
      (b) literally running `python ww_p29a_py37_gate.py --py37 <this python>
          <builder> <mod> <logic>` parses and prints ARGV_FLAG=--py37,
          proving shape correctness end-to-end on THIS interpreter.
    Returns 0 on PASS else the first failing code."""
    ps1 = "".join((SCRIPTS / p).read_text(encoding="utf-8", errors="replace")
                   for p in ("ww_p29a_build_on_win.ps1", "ww_p29a_deploy.ps1",
                             "ww_p29a_rollback.ps1"))
    if REGEX_SINGLE_DASH.search(ps1):
        print("ARGV_FLAG=FAIL (single-dash '-py37' found in a P29 ps1)")
        return 1
    gate = SCRIPTS / "ww_p29a_py37_gate.py"
    files = [str(SCRIPTS / "ww_p29a_build_ts4script.py"), str(MOD),
             str(SCRIPTS / "ww_p29a_logic_test.py")]
    cmd = [sys.executable, str(gate), "--py37", sys.executable] + files
    r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
    print((r.stdout or "").strip())
    if r.stderr:
        print("[stderr] " + r.stderr.strip())
    if r.returncode != 0:
        print("ARGV_FLAG=PASS? no -- exact --py37 argv was REJECTED (exit %d)" % r.returncode)
        return 1
    # The gate echoes ARGV_FLAG=--py37 after a successful parse with --py37.
    echoed = [ln for ln in (r.stdout or "").splitlines() if ln.startswith("ARGV_FLAG=")]
    if echoed and "--py37" in echoed[0]:
        print(echoed[0].replace("ARGV_FLAG=", "ARGV_FLAG(confirmed)="))
    print("ARGV_FLAG=--py37 (wrapper uses double-dash; exact argv parsed OK)")
    return 0


def magic_chain():
    """Prove the deploy magic mechanism WITHOUT PowerShell, on THIS host.

    Mirrors what scripts/ww_p29a_build_on_win.ps1 drives on Windows:
      1) magic-from-pyc reads TARGET_PYC_MAGIC from a KNOWN game-loadable .pyc
         (here we fabricate one whose magic == THIS interpreter's magic, standing
         in for the live WW member on Windows).
      2) match --target picks a local compiler whose MAGIC == target.
      3) build under that compiler, then confirm the produced member magic == target
         (the ps1 does this step-4 check; we reproduce the equality here).
    This is the conservative, magic-pinning chain; if it holds here the deploy's
    identical logic is sound modulo the real target value/compiler on Windows.
    """
    import tempfile, zipfile, py_compile
    code = "import sys, importlib.util; print(importlib.util.MAGIC_NUMBER.hex())"
    ours = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True).stdout.strip()

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        src = d / "probe_src.py"
        pyc = d / "probe_src.pyc"
        src.write_text("_hook_factory = None\n")
        py_compile.compile(str(src), cfile=str(pyc))
        data = pyc.read_bytes()
        assert data[:4].hex() == ours, "fixture magic != host magic"
        mods = d / "Mods"
        mods.mkdir()
        zpath = mods / "FakeWW_Scripts.ts4script"
        with zipfile.ZipFile(str(zpath), "w") as z:
            z.writestr("wickedwhims/sex/animations/animations_loader.pyc", data)

        # 1) discover target magic from the 'known game-loadable' member
        g = subprocess.run([sys.executable,
                            str(SCRIPTS / "ww_p29a_game_py.py"),
                            "magic-from-pyc", "--locate-mod", str(mods)],
                           capture_output=True, text=True).stdout
        target = ""
        for ln in g.splitlines():
            if ln.startswith("TARGET_PYC_MAGIC="):
                target = ln.split("=", 1)[1].strip()
        print("TARGET_PYC_MAGIC=%s" % target)
        assert target == ours, "discovered target != host magic"

        # 2) select a local compiler whose magic == target
        m = subprocess.run([sys.executable,
                            str(SCRIPTS / "ww_p29a_game_py.py"),
                            "match", "--target", target],
                           capture_output=True, text=True)
        mout = m.stdout
        for ln in mout.splitlines():
            print(ln)
        assert m.returncode == 0, "match should succeed with host compiler"
        assert "PYC_MAGIC_MATCH=YES" in mout
        compiler = ""
        for ln in mout.splitlines():
            if ln.startswith("MATCH="):
                compiler = ln.split("\t")[0][len("MATCH="):]
        assert compiler and os.path.exists(compiler)

        # 3) build under the matched compiler, then check member magic == target
        out_ts4 = d / "ww_p29a_debug.ts4script"
        b = subprocess.run([compiler,
                            str(SCRIPTS / "ww_p29a_build_ts4script.py"),
                            "--src", str(MOD), "--out", str(out_ts4)],
                           capture_output=True, text=True)
        bout = b.stdout + (("\n[stderr] " + b.stderr) if b.stderr else "")
        print(bout.strip())
        assert b.returncode == 0
        with zipfile.ZipFile(str(out_ts4)) as z:
            hdr = z.read("ww_p29a_mod.pyc")[:4].hex()
        print("BUILT_PYC_MAGIC=%s" % hdr)
        assert hdr == target, "built pyc magic != target"
        print("PYC_MAGIC_MATCH=YES")
    print("MAGIC_CHAIN_VERDICT=PASS")
    return 0


def main():
    codes = {}
    codes["MAGIC"] = magic_chain()
    codes["PS1"] = run("PS1_STATIC_CHECK",
                       [sys.executable, str(SCRIPTS / "ww_p29a_ps1_static_check.py")])
    codes["STATIC"] = run("STATIC_CHECK",
                          [sys.executable, str(SCRIPTS / "ww_p29a_static_check.py")])
    codes["LOGIC"] = run("LOGIC_TEST",
                         [sys.executable, str(SCRIPTS / "ww_p29a_logic_test.py")])
    codes["PY37"] = run("PY37_GATE",
                         [sys.executable, str(SCRIPTS / "ww_p29a_py37_gate.py"),
                          str(SCRIPTS / "ww_p29a_build_ts4script.py"),
                          str(MOD), str(SCRIPTS / "ww_p29a_logic_test.py")])
    codes["PY37ARGV"] = py37_argv()
    codes["LIVECLS"] = livecls_mechanism()
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
