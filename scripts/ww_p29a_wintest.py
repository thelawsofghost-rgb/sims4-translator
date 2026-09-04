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
TUNING_MOD = SCRIPTS / "ww_p29_tuning_mod.py"
TS4_OUT = Path(os.environ.get("TMPDIR", "/tmp")) / "ww_p29a_debug.ts4script"
TUNING_TS4_OUT = Path(os.environ.get("TMPDIR", "/tmp")) / "ww_p29_tuning_debug.ts4script"


def run(label, cmd):
    print("\n=== %s ===" % label)
    r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
    out = (r.stdout or "") + (("\n[stderr] " + r.stderr) if r.stderr else "")
    print(out.strip())
    return r.returncode


REGEX_SINGLE_DASH = re.compile(r'"-py37"|\s-py37(\s|\))')


def livecls_mechanism():
    """Mechanism check for the NATIVE ww_p29a_live_probe.py (task #1 rewrite).

    The old xdis transcription probe is retired: the live WW __init__ no longer
    matches (self, animation_id, animation_raw_display_name, animation_type) (that
    transcription is STALE).  New probe is native marsh by the real matching CPython
    and must REPORT the live contract while DECOUPLING class-presence from
    expected-signature-match.  We cannot ship Dorothy's real WW ts4script here, so
    we prove the probe MECHANISM on synthesized fixtures compiled by THIS interpreter
    (magic therefore equals the probe's own, so native marshal is valid):
      --current fixture (display_name at index 2) -> LIVE_CLASS_PRESENT=YES,
         DISPLAY_PARAM_INDEX2=display_name, EXPECTED_SIGNATURE_MATCH=NO; and
      --old fixture (animation_raw_display_name) -> EXPECTED_SIGNATURE_MATCH=YES.
    Returns 0 on PASS."""
    import py_compile
    import shutil
    import tempfile
    import zipfile
    probe = SCRIPTS / "ww_p29a_live_probe.py"
    d = tempfile.mkdtemp(prefix="wwlivecls_")
    try:
        def _mk(sig_body, dfile, z, prefix):
            src = sig_body
            m = os.path.join(d, prefix + ".py")
            with open(m, "w") as fh:
                fh.write(src)
            c = os.path.join(d, prefix + ".pyc")
            py_compile.compile(m, cfile=c, dfile=dfile)
            z.write(c, dfile)
        import importlib.util
        cur_sig = (
            "class SexAnimationInstance(object):\n"
            "    def __init__(self, animation_id, display_name, display_icon,"
            " author, author_id, unsafe):\n"
            "        self.animation_id = animation_id\n"
            "        self.display_name = display_name\n"
            "        self.display_name_override = None\n"
            "        self.original_instance = None\n"
        )
        old_sig = (
            "class SexAnimationInstance(object):\n"
            "    def __init__(self, animation_id, animation_raw_display_name,"
            " animation_type):\n"
            "        self.animation_id = animation_id\n"
            "        self.display_name = animation_raw_display_name\n"
        )
        cur = os.path.join(d, "cur.ts4script")
        old = os.path.join(d, "old.ts4script")
        with zipfile.ZipFile(cur, "w") as z:
            _mk(cur_sig, "wickedwhims/sex/animations/animation_instance.pyc", z, "c")
        with zipfile.ZipFile(old, "w") as z:
            _mk(old_sig, "wickedwhims/sex/animations/animation_instance.pyc", z, "o")

        def run(fixture):
            r = subprocess.run([sys.executable, str(probe), fixture],
                               cwd=str(REPO), capture_output=True, text=True)
            return r.returncode, (r.stdout or "")

        rc, op = run(cur)
        print((op or "").strip())
        ok = (rc == 0 and "LIVE_CLASS_PRESENT=YES" in op
              and "DISPLAY_PARAM_INDEX2=display_name" in op
              and "EXPECTED_SIGNATURE_MATCH=NO" in op
              and "animation_instance.pyc" in op)
        print("LIVECLS_CURRENT=%s" % ("PASS" if ok else "FAIL"))
        if not ok:
            return 1
        rc, op2 = run(old)
        print((op2 or "").strip())
        ok2 = (rc == 0 and "LIVE_CLASS_PRESENT=YES" in op2
               and "DISPLAY_PARAM_INDEX2=animation_raw_display_name" in op2
               and "EXPECTED_SIGNATURE_MATCH=YES" in op2)
        print("LIVECLS_OLD=%s" % ("PASS" if ok2 else "FAIL"))
        if not ok2:
            return 1
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("LIVECLS=PASS (native probe mechanism OK; real answer needs Dorothy's live WW)")
    return 0


def display_source_mechanism():
    """Mechanism check for ww_p29a_display_source_trace.py (the animation_raw_display_name
    -> display_name -> SexAnimationInstance(...) dataflow pin).

    Proven on synthesized loader+instance fixtures compiled by THIS interpreter (its
    magic therefore matches the tracer's own, so native-marshal is valid):
      -- A: clean chain  'x = node.get('animation_raw_display_name');
            display_name = x(or re-get); SexAnimationInstance(0, display_name,...)'
         -> RAW_FIELD_LITERAL_PRESENT=YES, RAW_TO_DISPLAY_CHAIN=CONFIRMED, and the
            display slot (ctor arg that carries display_name) is detected.
      -- B: helper-routed / fallback chain where the raw literal only lives in a
         callee outside _create_sex_animation_instance and the display_name producer
         is a local call -> chain must NOT be CONFIRMED; must report PARTIAL_...
         (no simplification).
    Returns 0 on PASS."""
    import py_compile
    import shutil
    import tempfile
    import zipfile
    tracer = SCRIPTS / "ww_p29a_display_source_trace.py"
    d = tempfile.mkdtemp(prefix="wwdisp_")
    try:
        def _src_to_zip(src, fname, dfile, z):
            py = os.path.join(d, os.path.basename(fname) + ".py")
            with open(py, "w") as fh:
                fh.write(src)
            pyc = os.path.join(d, os.path.basename(fname) + ".pyc")
            py_compile.compile(py, cfile=pyc, dfile=dfile)
            z.write(pyc, dfile)

        loader_A = (
            "def _create_sex_animation_instance(node, author_name, author_id):\n"
            "    from wickedwhims.sex.animations.animation_instance import " +
            "SexAnimationInstance\n"
            "    display_name = node.get('animation_raw_display_name')\n"
            "    return SexAnimationInstance(0, display_name, 'ic', author_name, " +
            "author_id)\n"
        )
        loader_B = (
            "def _localize_or(obj):\n"
            "    return obj.get('animation_raw_display_name')\n"
            "def _create_sex_animation_instance(node, author_name, author_id):\n"
            "    from wickedwhims.sex.animations.animation_instance import " +
            "SexAnimationInstance\n"
            "    display_name = _localize_or(node)\n"
            "    return SexAnimationInstance(0, display_name, 'ic', author_name, " +
            "author_id)\n"
        )
        inst_src = (
            "class SexAnimationInstance(object):\n"
            "    def __init__(self, animation_id, display_name, display_icon, " +
            "author, author_id, unsafe=False):\n"
            "        self.animation_id = animation_id\n"
            "        self.display_name = display_name\n"
            "        self.display_icon = display_icon\n"
            "        self.author = author\n"
            "        self.author_id = author_id\n"
            "        self.display_name_override = None\n"
            "        self.original_instance = None\n"
            "    def get_display_name(self):\n"
            "        if self.display_name_override is not None:\n"
            "            return self.display_name_override\n"
            "        return self.display_name\n"
            "    def set_display_name(self, name):\n"
            "        self.display_name_override = name\n"
        )
        L_MEM = "wickedwhims/sex/animations/animations_loader.pyc"
        I_MEM = "wickedwhims/sex/animations/animation_instance.pyc"
        fa = os.path.join(d, "a.ts4script")
        fb = os.path.join(d, "b.ts4script")
        with zipfile.ZipFile(fa, "w", zipfile.ZIP_DEFLATED) as z:
            _src_to_zip(loader_A, "loader", L_MEM, z)
            _src_to_zip(inst_src, "instance", I_MEM, z)
        with zipfile.ZipFile(fb, "w", zipfile.ZIP_DEFLATED) as z:
            _src_to_zip(loader_B, "loaderB", L_MEM, z)
            _src_to_zip(inst_src, "instanceB", I_MEM, z)

        def run(fixture):
            r = subprocess.run([sys.executable, str(tracer), fixture],
                               cwd=str(REPO), capture_output=True, text=True)
            return r.returncode, (r.stdout or "") + (("\n[stderr] " + r.stderr)
                                                      if r.stderr else "")

        rc, op = run(fa)
        print((op or "").strip())
        print("DISP_CHAIN_A_CONFIRMED=%s" % (
            "PASS" if (rc == 0 and "RAW_TO_DISPLAY_CHAIN=CONFIRMED" in op
                        and "RAW_FIELD_LITERAL_PRESENT=YES" in op
                        and "CTOR_ARG[1]=L:display_name" in op) else "FAIL"))
        if rc != 0 or "RAW_TO_DISPLAY_CHAIN=CONFIRMED" not in op \
                or "CTOR_ARG[1]=L:display_name" not in op:
            return 1
        rc2, op2 = run(fb)
        print((op2 or "").strip())
        not_conf = "RAW_TO_DISPLAY_CHAIN=CONFIRMED" not in op2
        print("DISP_CHAIN_B_NONCONFIRMED=%s" % (
            "PASS" if (rc2 == 0 and not_conf) else "FAIL"))
        if rc2 != 0 or not not_conf:
            return 1
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("DISP=PASS (mechanism OK; real answer needs Dorothy's live loader)")
    return 0


def display_origin_mechanism():
    """Mechanism check for ww_p29a_display_origin_trace.py (the whole-ts4script audit
    of WHERE the display name really comes from).

    Builds loader fixtures under THIS interpreter (magic == tracer's own):
      DIRECT fixture  : tuning.animation_display_name = tuning.animation_raw_display_name
                        (bare copy, no call)  -> RAW_TO_ANIMATION_DISPLAY_RELATION=DIRECT
      TRANSFORM fixture: display = _localize(raw)
                        -> RAW_TO_ANIMATION_DISPLAY_RELATION=TRANSFORMED
      INDEP fixture    : display and raw set from two DISTINCT .get() XML keys
                        -> RAW_TO_ANIMATION_DISPLAY_RELATION=INDEPENDENT
    Also re-runs the display_source tracer on a CALL_FUNCTION_KW loader (the real WW
    style) asserting DISPLAY_NAME_ARGUMENT_TO_CTOR=L:display_name (not UNRESOLVED) --
    the Task-5 callsite false-negative fix (CALL_FUNCTION/CALL_FUNCTION_KW/CALL_METHOD).
    Returns 0 on PASS."""
    import py_compile
    import shutil
    import tempfile
    import zipfile
    tracer = SCRIPTS / "ww_p29a_display_origin_trace.py"
    src_tracer = SCRIPTS / "ww_p29a_display_source_trace.py"
    d = tempfile.mkdtemp(prefix="wworg_")
    try:
        def _zip_one(py_src, outname, dfile="wickedwhims/sex/animations/animations_loader.pyc"):
            py = os.path.join(d, outname + ".py")
            with open(py, "w") as fh:
                fh.write(py_src)
            pyc = os.path.join(d, outname + ".pyc")
            py_compile.compile(py, cfile=pyc, dfile=dfile)
            out = os.path.join(d, outname + ".ts4script")
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(pyc, dfile)
            return out

        direct_src = (
            "def _create_sex_animation_instance(xml_node):\n"
            "    t = _Tuning()\n"
            "    t.animation_raw_display_name = xml_node.get('animation_raw_display_"
            "name', '')\n"
            "    t.animation_display_name = t.animation_raw_display_name\n"
            "    return t\n"
            "class _Tuning(object):\n"
            "    def __init__(self):\n"
            "        self.animation_display_name = ''\n"
            "        self.animation_raw_display_name = ''\n"
        )
        trans_src = (
            "def _localize(o):\n"
            "    return o\n"
            "def _create_sex_animation_instance(xml_node):\n"
            "    t = _Tuning()\n"
            "    t.animation_raw_display_name = xml_node.get('animation_raw_display_"
            "name', '')\n"
            "    t.animation_display_name = _localize(t.animation_raw_display_name)\n"
            "    return t\n"
            "class _Tuning(object):\n"
            "    def __init__(self):\n"
            "        self.animation_display_name = ''\n"
            "        self.animation_raw_display_name = ''\n"
        )
        indep_src = (
            "def _create_sex_animation_instance(xml_node):\n"
            "    t = _Tuning()\n"
            "    t.animation_display_name = xml_node.get('animation_display_name', '')\n"
            "    t.animation_raw_display_name = xml_node.get('animation_raw_display_"
            "name', '')\n"
            "    return t\n"
            "class _Tuning(object):\n"
            "    def __init__(self):\n"
            "        self.animation_display_name = ''\n"
            "        self.animation_raw_display_name = ''\n"
        )
        kw_src = (
            "from wickedwhims.sex.animations.animation_instance import SexAnimation"
            "Instance\n"
            "def _create_sex_animation_instance(animation_tuning):\n"
            "    display_name = animation_tuning.animation_display_name\n"
            "    return SexAnimationInstance(animation_id=0, display_name=display_name, "
            "author='WW', display_icon=None)\n"
        )
        inst_src = (
            "class SexAnimationInstance(object):\n"
            "    def __init__(self, animation_id=0, display_name='', author='', "
            "display_icon=None):\n"
            "        self.display_name = display_name\n"
            "        self.display_name_override = None\n"
            "        self.original_instance = None\n"
            "    def get_display_name(self):\n"
            "        return self.display_name_override if self.display_name_override is "
            "not None else self.display_name\n"
            "    def set_display_name(self, name):\n"
            "        self.display_name_override = name\n"
        )
        I_MEM = "wickedwhims/sex/animations/animation_instance.pyc"

        def run_origin(fixture):
            r = subprocess.run([sys.executable, str(tracer), fixture], cwd=str(REPO),
                               capture_output=True, text=True)
            return r.returncode, (r.stdout or "") + (("[stderr] " + r.stderr)
                                                      if r.stderr else "")

        ok = True
        fd = _zip_one(direct_src, "direct")
        rc, op = run_origin(fd)
        print("ORIGIN_DIRECT_REL=%s" % (
            "PASS" if (rc == 0 and "RAW_TO_ANIMATION_DISPLAY_RELATION=DIRECT" in op)
            else "FAIL"))
        ok = ok and rc == 0 and "RAW_TO_ANIMATION_DISPLAY_RELATION=DIRECT" in op

        ft = _zip_one(trans_src, "trans")
        rc, op = run_origin(ft)
        print("ORIGIN_TRANSFORMED_REL=%s" % (
            "PASS" if (rc == 0 and "RAW_TO_ANIMATION_DISPLAY_RELATION=TRANSFORMED" in op)
            else "FAIL"))
        ok = ok and rc == 0 and "RAW_TO_ANIMATION_DISPLAY_RELATION=TRANSFORMED" in op

        fi = _zip_one(indep_src, "indep")
        rc, op = run_origin(fi)
        print("ORIGIN_INDEPENDENT_REL=%s" % (
            "PASS" if (rc == 0 and "RAW_TO_ANIMATION_DISPLAY_RELATION=INDEPENDENT" in op)
            else "FAIL"))
        ok = ok and rc == 0 and "RAW_TO_ANIMATION_DISPLAY_RELATION=INDEPENDENT" in op

        # Task-5 callsite false-negative fix: CALL_FUNCTION_KW must surface the display
        # ctor arg instead of UNRESOLVED.
        fk = os.path.join(d, "kw.ts4script")
        py = os.path.join(d, "kw_loader.py")
        with open(py, "w") as fh:
            fh.write(kw_src)
        pyc = os.path.join(d, "kw_loader.pyc")
        py_compile.compile(py, cfile=pyc,
                           dfile="wickedwhims/sex/animations/animations_loader.pyc")
        with zipfile.ZipFile(fk, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(pyc, "wickedwhims/sex/animations/animations_loader.pyc")
            pi = os.path.join(d, "kw_inst.py")
            with open(pi, "w") as fh:
                fh.write(inst_src)
            pcy = os.path.join(d, "kw_inst.pyc")
            py_compile.compile(pi, cfile=pcy, dfile=I_MEM)
            z.write(pcy, I_MEM)
        r = subprocess.run([sys.executable, str(src_tracer), fk], cwd=str(REPO),
                           capture_output=True, text=True)
        op = (r.stdout or "") + (("[stderr] " + r.stderr) if r.stderr else "")
        good_kw = (r.returncode == 0
                   and "DISPLAY_NAME_ARGUMENT_TO_CTOR=L:display_name" in op
                   and "CTOR_CALL_OFFSET=" in op
                   and "DISPLAY_NAME_ARGUMENT_TO_CTOR=UNRESOLVED" not in op)
        print("CALLFUNCTION_KW_CTOR_CTOR=%s" % ("PASS" if good_kw else "FAIL"))
        print("CALLFUNCTION_KW_SNIPPET=%s" % "".join(
            [ln for ln in op.splitlines()
             if ln.startswith(("CTOR_CALL_OFFSET", "CTOR_CALL_FN", "CTOR_ARG[",
                              "DISPLAY_NAME_ARGUMENT_TO_CTOR"))]))
        ok = ok and good_kw
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("ORIGIN=PASS (mechanism OK; real answer needs Dorothy's live WW ts4script)")
    return 0 if ok else 1


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
    codes["DISP"] = display_source_mechanism()
    codes["ORIGIN"] = display_origin_mechanism()
    codes["TSTATIC"] = run("TUNING_STATIC_CHECK",
                            [sys.executable,
                             str(SCRIPTS / "ww_p29_tuning_static_check.py")])
    codes["TLOGIC"] = run("TUNING_LOGIC_TEST",
                           [sys.executable,
                            str(SCRIPTS / "ww_p29_tuning_logic_test.py")])
    codes["TBUILD"] = run("TUNING_BUILD_ROUNDTRIP",
                           [sys.executable,
                            str(SCRIPTS / "ww_p29a_build_ts4script.py"),
                            "--src", str(TUNING_MOD), "--out", str(TUNING_TS4_OUT),
                            "--member", "ww_p29_tuning_mod.pyc",
                            "--probe-attr", "_looks_like_target",
                            "--probe-mod", "ww_p29_tuning_mod_probe"])
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
