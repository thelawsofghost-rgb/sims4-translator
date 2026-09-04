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

  C) PY37_ARGV_SHAPE (pure text): argparse defines the gate flag as `--py37`.
       argparse does NOT accept the single-dash `-py37` (it errors
       'unrecognized arguments: -py37' exit 2) -- the real-machine 4th deployment
       hit exactly that because build_on_win passed `-py37`.  We assert every .ps1
       that invokes the gate passes the DOUBLE-dash `--py37` and never `-py37`.
       This catches wrapper-assembled-arg bugs even when only the static gate
       (and not a live pwsh run) executes on the sandbox.

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
import re
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


def _check_py37_argv_shape(text, name):
    """Ensure any gate invocation uses the double-dash '--py37', never '-py37'.

    argparse rejects single-dash '-py37' (unrecognized arguments, exit 2).  Search
    for a literal token that would be handed to argparse as the flag -- i.e. a
    double-quoted or bare '-py37' used as a PyArgs element -- while tolerating
    comment/doc references.  Return list of failure reasons."""
    fails = []
    for i, ln in enumerate(text.splitlines()):
        # allow comment lines and the worker's doc banner
        stripped = ln.lstrip()
        if stripped.startswith("#"):
            continue
        # reject a single-dash gate invocation (the bug)
        if re.search(r'"-py37"|\s-py37(\s|\))', ln) and "--py37" not in ln:
            fails.append("single-dash '-py37'@L%d: %r" % (i + 1, ln.strip()[:80]))
        # also require that an actual invocation site uses double-dash --py37
        if re.search(r'py37_gate\.py.*-PyArgs|Run-Py.*PyArgs.*py37', ln):
            if '--py37' not in ln:
                fails.append("gate PyArgs missing '--py37'@L%d: %r" % (i + 1, ln.strip()[:80]))
    return fails


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


def _token_balance(text):
    """Deterministic structural gate for the EXACT bug class found on 2026-09-04:
    a brace/paren imbalance hidden inside a multiline "..." + "..." string
    concatenation made the .ps1 die at the PowerShell PARSER stage (before any line
    executed), invisible to the pure-text PARAM_PLACEMENT check (which only looks at
    lines above the first header).

    This is a small state machine, NOT a per-line regex: it walks the raw text and,
    with correct state carried ACROSS lines, skips comments, single- and
    double-quoted strings (backtick / doubled-quote aware), and here-strings
    (@'...'@ and @"..."@ whose terminator starts a line), while tracking the running
    depth of () [] {}.  Returns a list of imbalance messages (empty == balanced).
    """
    pairs = {')': '(', ']': '[', '}': '{'}
    depth = []          # (open_char, line_no) currently open
    issues = []
    lines = text.split('\n')
    n = len(lines)
    i = 0
    while i < n:
        ln = lines[i]
        j, L = 0, len(ln)
        while j < L:
            ch = ln[j]
            nxt = ln[j + 1] if j + 1 < L else ''
            if ch == '#':
                break                     # comment to end of line
            if ch == "'":
                j += 1
                while j < L:
                    if ln[j] == "'":
                        if j + 1 < L and ln[j + 1] == "'":   # '' escape
                            j += 2
                            continue
                        break
                    j += 1
                j += 1
                continue
            if ch == '"':
                j += 1
                while j < L:
                    if ln[j] == '`' and j + 1 < L:             # backtick escape
                        j += 2
                        continue
                    if ln[j] == '"':
                        break
                    j += 1
                j += 1
                continue
            if ch == '@' and nxt in ("'", '"'):
                closer = "'@" if nxt == "'" else '"@'
                k = i + 1
                while k < n and not lines[k].lstrip().startswith(closer):
                    k += 1
                if k >= n:
                    issues.append("here-string never closed (opened L%d)" % (i + 1))
                    return issues
                i = k                      # resume after the terminator line
                break                      # line consumed by the here-string
            if ch in '([{':
                depth.append((ch, i + 1))
                j += 1
                continue
            if ch in pairs:
                want = pairs[ch]
                if not depth or depth[-1][0] != want:
                    issues.append("L%d: unmatched '%s' (expected '%s')" % (i + 1, ch, want))
                    return issues
                depth.pop()
                j += 1
                continue
            j += 1
        i += 1
    for ch, lno in depth:
        issues.append("unclosed '%s' opened at L%d" % (ch, lno))
    return issues


def _check_ps1(path):
    """Return (placement_fails, has_ps_host, parser_fails, parser_err, imbalance)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    placement = _check_param_placement(lines, path.name)
    imbalance = _token_balance(text)

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
    return placement, (not host_missing), parser_fails, parser_err, imbalance


def _self_balance_tests():
    """Validate the STRUCT_BALANCE detector itself can BOTH flag the bug class that
    bit us (2026-09-04) and NOT false-positive on legitimately tricky PowerShell
    (braces inside strings, ${scope} vars, here-strings).  Returns list of failures."""
    fails = []
    # 1) the real-machine bug class: an EXTRA unbalanced brace/paren that makes a
    #    .ps1 die at the PowerShell PARSER stage (structurally must be caught even
    #    though PARAM_PLACEMENT/PY37_ARGV_SHAPE only look at text above the header).
    broken = (
        "if (-not (Test-Path -LiteralPath $Py37)) {\n"
        "    throw \"PY37_MISSING=$Py37; pass -Py37 if installed elsewhere\"\n"
        "}\n"
        "}\n"          # stray extra closing brace -> must be flagged
    )
    if not _token_balance(broken):
        fails.append("detector MISSED extra unbalanced brace")

    # 2) an extra/earlier closing brace that PowerShell would reject
    if not _token_balance("if ($a) { Write-Output 'x' } } }\n"):
        fails.append("detector MISSED stray trailing braces")

    # 3) legitimately tricky but VALID PowerShell must not be flagged
    ok_samples = [
        # braces + parens nested inside double-quoted strings
        "${scope}x = 'lit'\nWrite-Output \"result={0} and }}\"\n",
        # single-line block heavily used by Fail-style helpers
        "function F($r) { Write-Output \"R=$r\"; Write-Output \"V=FAIL\"; exit 1 }\n",
        # here-string with braces and parens inside content (terminator at line start)
        "$doc = @'\nraw { paren ( content\n'@\nWrite-Output $doc\n",
    ]
    for s in ok_samples:
        bad = _token_balance(s)
        if bad:
            fails.append("false positive on valid PS: %r -> %r" % (s[:40], bad))
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ps1", nargs="+",
                    default=["scripts/ww_p29a_build_on_win.ps1",
                             "scripts/ww_p29a_deploy.ps1",
                             "scripts/ww_p29a_rollback.ps1",
                             "scripts/ww_p29a_liveprobe.ps1",
                             "scripts/ww_p29a_static_trace.ps1",
                             "scripts/ww_p29a_display_source_trace.ps1",
                             "scripts/ww_p29a_display_origin_trace.ps1",
                             "scripts/ww_p29_tuning_deploy.ps1",
                             "scripts/ww_p29_tuning_read_log.ps1",
                             "scripts/ww_p29_tuning_rollback.ps1"],
                    help=".ps1 files to check")
    ap.add_argument("--selftest", action="store_true", default=True,
                    help="run the balance-detector self tests (guards the guard)")
    a = ap.parse_args()

    any_fail = False
    for name in a.ps1:
        path = Path(name)
        if not path.is_file():
            print("PS1_STATIC_FAIL %s (missing)" % name)
            any_fail = True
            continue
        placement, host_bool, parser_fails, parser_err, imbalance = _check_ps1(path)
        argv_shape = _check_py37_argv_shape(path.read_text(encoding="utf-8", errors="replace"),
                                            path.name)
        print("PS1_STATIC %s" % path.name)
        print("  STRUCT_BALANCE=%s" % ("PASS" if not imbalance else "FAIL"))
        if imbalance:
            any_fail = True
            for f in imbalance:
                print("    " + f)
        print("  PARAM_PLACEMENT=%s" % ("PASS" if not placement else "FAIL"))
        if placement:
            any_fail = True
            for f in placement:
                print("    " + f)
        print("  PY37_ARGV_SHAPE=%s" % ("PASS" if not argv_shape else "FAIL"))
        if argv_shape:
            any_fail = True
            for f in argv_shape:
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

    if a.selftest:
        print("SELF_BALANCE_TESTS ...")
        sf = _self_balance_tests()
        print("  SELF_BALANCE=%s" % ("PASS" if not sf else "FAIL"))
        if sf:
            any_fail = True
            for f in sf:
                print("    " + f)

    if any_fail:
        print("VERDICT=PS1_STATIC_FAIL")
        return 1
    print("VERDICT=PS1_STATIC_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
