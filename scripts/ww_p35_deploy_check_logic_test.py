#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Logic tests for ww_p35_deploy_check.py (Linux-run).

Because EXPECT_COUNT is a module global on ww_p35_deploy_check, tests drop it to
small N via a tiny subprocess driver so negative branches are cheap.  The real
479-row PASS is exercised end-to-end with a genuine artifact produced by the
P35.1 builder.

Covered:
  PASS-src        full 479 override artifact + --source -> exit 0, OVERRIDE_COUNT=479,
                  TYPE/INSTANCE/UNIQUE OK, checklist log written, VERDICT=PASS.
  PASS-pre        P35.1 P35.1 builder PASS (no --source arg) exit 0.
  WHITEBOX        reject whitebox instance 0x4444444400000002 -> exit 4.
  WRONGTYPE       type != 0x7DF2169C -> exit 4.
  TWOWWXML        2 WW_ANIM_XML entries -> exit 4 (UNIQUE fail).
  WRONGINST       instance != real -> exit 4.
  LEFTOVER        one entry text == source raw (not overridden) -> exit 5.
  BADCOUNT        body has <N entries -> exit 5.
  MISSING         artifact path absent -> exit 2.
All writes confined to a tmp dir; never Mods/Saves.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

WW_ANIM_XML = 0x7DF2169C
REAL = 0x43F3438A94EDEB2B
WHITEBOX = 0x4444444400000002
GROUP = 0x80000000

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print("%-6s %-16s %s" % ("PASS" if cond else "FAIL", name, detail))


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _xml(rows, inst=REAL, wwtype=WW_ANIM_XML):
    """rows: list[(ordinal,text)]; return (type,group,inst,xml_bytes)."""
    inner = "".join(
        '<U n="anm%d"><T n="animation_raw_display_name">%s</T>'
        '<T n="animation_author">deploy_test</T></U>'
        % (o, _esc(t)) for o, t in sorted(rows))
    text = ('<?xml version="1.0"?><U n="WW"><L n="animations_list">'
            + inner + '</L></U>')
    return (wwtype, GROUP, inst, text.encode("utf-8"))


def _build_pkg(out, items):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_wb", SCRIPT_DIR / "ww_animation_canary_builder.py")
    wb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wb)
    wb.build_package(items, out)
    return out


def _run(artifact, source, expect):
    drv = SCRIPT_DIR / "_p352_drv.py"
    arg = [str(artifact)]
    if source:
        arg += ["--source", str(source)]
    drv.write_text(
        "import importlib.util,sys\n"
        "spec=importlib.util.spec_from_file_location('m','%s')\n"
        "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "m.EXPECT_COUNT=%d\n"
        "sys.exit(m.main(%r))\n"
        % (SCRIPT_DIR / "ww_p35_deploy_check.py", expect, arg))
    r = subprocess.run([sys.executable, str(drv)], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def main():
    N = 6
    en_raw = ["NOT Caught %d" % (i + 1) for i in range(N)]
    zh = ["未捉奸 %d" % (i + 1) for i in range(N)]
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # ---- negatives at N=6 built directly ----
        def build_over(rows, inst=REAL, wtype=WW_ANIM_XML, label="x"):
            p = tmp / ("%s_%s.bpkg" % (label, inst if inst else 'x'))
            _build_pkg(p, [_xml(rows, inst, wtype)])
            return p

        over_rows = [(i, zh[i]) for i in range(N)]
        src = build_over([(i, en_raw[i]) for i in range(N)], label="src")

        p_ok = build_over(over_rows, label="ok")
        rc, so = _run(p_ok, src, N)
        check("sm.go", rc == 0 and "OVERRIDE_COUNT=%d" % N in so,
              "rc=%d" % rc)
        check("sm.verdict_pass", "VERDICT=PASS" in so, "")
        check("sm.logwritten",
              (tmp / ("ok_%s.bpkg" % REAL))
              .with_name("P35.2_DEPLOY_CHECKLIST.txt").is_file(), "")

        p_wb = build_over(over_rows, inst=WHITEBOX, label="wb")
        rc, so = _run(p_wb, src, N)
        check("sm.whitebox", rc == 4 and "WHITEBOX_INSTANCE=DETECTED" in so,
              "rc=%d" % rc)

        p_wt = build_over(over_rows, wtype=0x220557DA, label="wt")
        rc, so = _run(p_wt, src, N)
        # a wrong-typed-only package contains NO 0x7DF2169C -> UNIQUE fail -> reject
        check("sm.wrongtype", rc == 4 and "UNIQUE=FAIL" in so, "rc=%d" % rc)

        # 2 WW_ANIM_XML
        p2 = tmp / "two.bpkg"
        _build_pkg(p2, [_xml(over_rows),
                        _xml(over_rows)])
        rc, so = _run(p2, src, N)
        check("sm.twoww", rc == 4 and "UNIQUE=FAIL" in so, "rc=%d" % rc)

        p_wi = build_over(over_rows, inst=0x1111111111111111, label="wi")
        rc, so = _run(p_wi, src, N)
        check("sm.wronginst", rc == 4 and "INSTANCE_CHECK=FAIL" in so,
              "rc=%d" % rc)

        # LEFTOVER: ordinal 2 keeps English == source raw
        lo_rows = [(i, en_raw[i] if i == 2 else zh[i]) for i in range(N)]
        p_lo = build_over(lo_rows, label="lo")
        rc, so = _run(p_lo, src, N)
        check("sm.leftover", rc == 5 and "OVERRIDE_COUNT=%d" % (N - 1) in so,
              "rc=%d" % rc)

        # BADCOUNT: body has N-1 entries
        p_bc = build_over([(i, zh[i]) for i in range(N - 1)], label="bc")
        rc, so = _run(p_bc, src, N)
        check("sm.badcount", rc == 5 and "ENTRY_COUNT=FAIL" in so, "rc=%d" % rc)

        # MISSING artifact
        rc, so = _run(tmp / "does_not_exist.bpkg", src, N)
        check("sm.missing", rc == 2, "rc=%d" % rc)

        # ---- full 479 PASS via genuine P35.1 builder output ----
        N479 = 479
        big_raw = ["NOT Caught Cheating %d - Climax *Custom*" % (i + 1)
                   for i in range(N479)]
        big_zh = ["未被捉奸 %d - 高潮 *自定义*" % (i + 1) for i in range(N479)]
        # Build genuine source fixture via ww_p35_fixture (matches P35.1 inputs)
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "fx", SCRIPT_DIR / "ww_p35_fixture.py")
        fx = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fx)
        fx.build_pkg_fixture(tmp / "src479.bpkg",
                             [(i, big_raw[i]) for i in range(N479)])

        # produce override artifact via P35.1 builder (subprocess driver EXPECT_ROWS=479)
        import csv
        cols = ["source_instance", "ordinal", "identifier", "raw_display_name",
                "translated_name", "status", "note"]
        with open(tmp / "fin479.csv", "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            for i in range(N479):
                w.writerow({"source_instance": "0x%016X" % REAL,
                            "ordinal": str(i), "identifier": "i%d" % i,
                            "raw_display_name": big_raw[i],
                            "translated_name": big_zh[i],
                            "status": "TRANSLATED", "note": ""})
        drv = SCRIPT_DIR / "_p351_drv.py"
        drv.write_text(
            "import importlib.util,sys\n"
            "spec=importlib.util.spec_from_file_location('m','%s')\n"
            "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "m.EXPECT_ROWS=%d\n"
            "sys.exit(m.main(['%s','--map','%s','--out-dir','%s']))\n"
            % (SCRIPT_DIR / "ww_p35_xml_displayname_override.py", N479,
               tmp / "src479.bpkg", tmp / "fin479.csv", tmp / "ov479"))
        r1 = subprocess.run([sys.executable, str(drv)], capture_output=True, text=True)
        check("p352.src479_build", r1.returncode == 0 and "VERDICT=PASS" in
              r1.stdout + r1.stderr, "rc=%d" % r1.returncode)
        artifact = tmp / "ov479" / "P35.1_ww_anim_displayname_override.package"
        check("p352.artifact_exists", artifact.is_file(), "")
        rc, so = _run(artifact, tmp / "src479.bpkg", N479)
        check("p352.go_479", rc == 0, "rc=%d" % rc)
        for k in ("WW_ANIM_XML_COUNT=1", "UNIQUE=OK", "TYPE_CHECK=OK",
                  "INSTANCE_CHECK=OK", "OVERRIDE_COUNT=479",
                  "OVERRIDE_CHECK=OK", "VERDICT=PASS"):
            check("p352." + k.split("=")[0], k in so, "")
        # without --source, still PASS (provenance optional)
        rc, so = _run(artifact, None, N479)
        check("p352.go_479_nosrc", rc == 0 and "OVERRIDE_COUNT=479" in so,
              "rc=%d" % rc)

    n_pass = sum(1 for _, c, _ in results if c)
    print("\n%d/%d checks passed" % (n_pass, len(results)))
    if n_pass != len(results):
        for name, c, d in results:
            if not c:
                print("  FAILED: %s %s" % (name, d))
    sys.exit(0 if n_pass == len(results) else 1)


if __name__ == "__main__":
    main()
