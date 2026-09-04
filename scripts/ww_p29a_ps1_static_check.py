#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29a_ps1_static_check.py --- P29-A PowerShell source gate (read-only).

Catches the EXACT class of bug hit on the real machine (2026-09-04): in a `.ps1`,
the script-level `param(...)` / `[CmdletBinding()]` must be the FIRST executable
construct.  If `$ErrorActionPreference = ...` / a variable assignment / any other
statement precedes it, PowerShell treats `param` as a bare command and the deploy
dies before the runtime hook experiment even starts.

Two independent checks:

  A) PARAM_PLACEMENT (pure text / cross-platform, reliable even without PowerShell):
       For each *.ps1 we take the FIRST script-level header line, i.e. the first
       line matching  ^[ \t]*\[?CmdletBinding\(\)\]?[ \t]*$   or   ^[ \t]*param\(
       (before any function body, so it is the script-level header).  Every line
       above that first header line must be blank, a comment, or `#requires`.
       Any other statement before the header -> FAIL.  This scans comment-only /
       blank lines and is robust regardless of whether PowerShell is installed.

  B) REAL_PARSER (best effort; skipped when no PowerShell host is present):
       When `pwsh` or `powershell` exists on PATH, run
         $t=@(); $e=@()
         [System.Management.Automation.Language.Parser]::ParseFile($p,[ref]$t,[ref]$e)|Out-Null
         $e.Count   -> require 0
       and surface the error text otherwise.  On sandbox (Linux, no pwsh) it is
       explicitly SKIPPED, NOT silently assumed clean -- the deploy on Windows will
       exercise it for real.

Exit: 0 = PASS (placement OK and, if a PS host exists, parser reports 0 errors).
      1 = FAIL.  2 = input ps1 missing.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HEADER_LINE_RE = ("CmdletBinding", "param(")


def _is_header_line(line):
    s = line.strip()
    if not s:
        return False
    if s.startswith("[CmdletBinding()]"):
        return True
    if s.startswith("param("):
        return True
    return False


def _check_param_placement(text_lines, name):
    """Return list of failure reasons about the script-level param placement."""
    fails = []
    first_header = None
    for i, ln in enumerate(text_lines):
        if _is_header_line(ln):
            first_header = i
            break
    if first_header is None:
        # No script-level param/CmdletBinding is itself allowed ONLY for a ps1
        # with no parameters; but we require each P29 ps1 to declare one so the
        # header rule is explicit and checked.  If absent, treat as a placement
        # warning candidate but do not hard-fail for scripts that legitimately
        # take no params -- instead note it.  (All current P29 ps1 declare one.)
        fails.append("no-script-level-header([CmdletBinding or param] never seen)")
        return fails
    # inspect lines strictly above the first header
    for i in range(first_header):
        s = text_lines[i].strip()
        if not s:
            continue
        if s.startswith("#"):
            continue  # comment or #requires (all start with '#')
        fails.append("statement-before-header@L%d: %r" % (i + 1, s[:60]))
    return fails


def _check_ps1(path):
    """Return (fail_reasons, has_ps_host, parser_fails, parser_err)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    placement = _check_param_placement(lines, path.name)

    # ---- B) real parser best-effort ----
    host = shutil.which("pwsh") or shutil.which("powershell")
    parser_fails = []
    parser_err = ""
    host_missing = host is None
    if host:
        # Build a tiny parser invocation script that reports error count + text.
        tmp_ps = None
        try:
            import tempfile
            tmp_ps = tempfile.NamedTemporaryFile(
                "w", suffix=".ps1", delete=False, encoding="utf-8")
            # write PS file that parses our target path (pass path via -File arg)
            # Build a literal path with single quotes doubled.
            p = str(path).replace("'", "''")
            tmp_ps.write(
                "$p='" + p + "'\n"
                "$tokens=$null;$errors=$null\n"
                "[System.Management.Automation.Language.Parser]::ParseFile("
                "$p,[ref]$tokens,[ref]$errors)|Out-Null\n"
                "Write-Output ('PSERR_COUNT=' + $errors.Count)\n"
                "foreach($e in $errors){ Write-Output ('PSERR:' + $e.Message) }\n"
            )
            tmp_ps.close()
            r = subprocess.run([host, "-NoProfile", "-File", tmp_ps.name],
                               capture_output=True, text=True, timeout=120)
            for line in (r.stdout or "").splitlines():
                if line.startswith("PSERR_COUNT="):
                    n = int(line.split("=", 1)[1].strip())
                    if n != 0:
                        parser_fails.append("real-parser-errors=%d" % n)
                elif line.startswith("PSERR:"):
                    parser_err += line + "\n"
            # A non-zero exit or presence of stderr mentioning 'param' is a hard sign.
            if r.returncode != 0 and not parser_fails:
                parser_fails.append("real-parser-nonzero-exit(%d)" % r.returncode)
            if (r.stderr or "").strip() and not parser_fails:
                parser_err += "[host-stderr] " + r.stderr[:300]
        except Exception as e:  # pragma: no cover - host quirks
            parser_fails.append("real-parser-invocation-exc:%r" % (e,))
        finally:
            if tmp_ps is not None:
                try:
                    os.unlink(tmp_ps.name)
                except OSError:
                    pass
    return placement, (not host_missing), parser_fails, parser_err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ps1", nargs="+",
                    default=["scripts/ww_p29a_build_on_win.ps1",
                             "scripts/ww_p29a_deploy.ps1",
                             "scripts/ww_p29a_rollback.ps1"],
                    help=".ps1 files to check")
    a = ap.parse_args()

    any_fail = False
    for name in a.ps1:
        path = Path(name)
        if not path.is_file():
            print("PS1_STATIC_FAIL %s (missing)" % name)
            any_fail = True
            continue
        placement, host_bool, parser_fails, parser_err = _check_ps1(path)
        print("PS1_STATIC %s" % path.name)
        print("  PARAM_PLACEMENT=%s" % ("PASS" if not placement else "FAIL"))
        if placement:
            any_fail = True
            for f in placement:
                print("    " + f)
        if host_bool:
            print("  REAL_PARSER=%s" % ("PASS" if not parser_fails else "FAIL"))
            if parser_fails:
                any_fail = True
                for f in parser_fails:
                    print("    " + f)
                if parser_err:
                    for ln in parser_err.splitlines():
                        print("    [ps] " + ln)
        else:
            print("  REAL_PARSER=SKIPPED (no pwsh/powershell host on THIS host)")

    if any_fail:
        print("VERDICT=PS1_STATIC_FAIL")
        return 1
    print("VERDICT=PS1_STATIC_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
