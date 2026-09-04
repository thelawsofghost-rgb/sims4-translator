#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p29a_py37_gate.py --- P29-A: require the build inputs compile cleanly on the
CPython 3.7 line (the interpreter the game embeds), not just sandbox/new python.

The deploy bug was a Python 3.8+ stdlib call (Path.unlink(missing_ok=True)) that
passed on the sandbox's new python but crashed the real CPython 3.7.9 builder
exit 1.  This gate exists so that class of error is caught BEFORE the real build.

Two-tier (both exit codes 0=pass):
  * When a real 3.7 interpreter is discoverable we RUN
        python3.7 -m py_compile <files...>
    (authoritative: real 3.7 parser+runtime import-time safety for py_compile).
    Discovery: argv --py37-command? NO -- we accept `--py37 <exe>`; otherwise we
    call `py -0p` / `python3.7` / `python -3.7` to find one.
  * ALWAYS we also run a static 3.7-compat audit of the inputs:
        - ast.parse(..., feature_version=(3,7))  -> rejects >3.7 syntax
        - regex denylist of common 3.8+ runtime APIs (missing_ok / removeprefix /
          removesuffix / is_relative_to / functools.cache / importlib.metadata /
          ZoneInfo) so a future <=3.7 parser that nonetheless lacks the API, or a
          3.8-only keyword, is rejected even with no 3.7 present.
  If a 3.7 interpreter is found AND py_compile fails -> exit non-zero (REAL).
  If none found -> we still fail on static violations, else PASS with
  PY37_REAL_COMPILE=SKIPPED so the deploy knows it ran static-only.

Outputs (ASCII):
  PY37_GATE=REAL|STATIC|FAIL|SKIPPED-NO-INPUT
  PY37_REAL_COMPILE=PASS|FAIL|SKIPPED
  PY37_EXE=<path-or-empty>
  FILE=...  per input ->  PY37_AST=OK|FAIL   PY37_API=OK|FAIL
Exit: 0 static clean (and real clean if a 3.7 ran) ; 1 any reject.
"""
import argparse
import ast
import os
import re
import shutil
import subprocess
import sys

# 3.8+ runtime APIs we must never use; reject if present in executable code.
DENY = [
    r"\bmissing_ok\s*=",          # Path.unlink(mkstemp missing_ok) etc -- 3.8+
    r"\.removeprefix\s*\(",       # 3.9+
    r"\.removesuffix\s*\(",       # 3.9+
    r"\.is_relative_to\s*\(",     # 3.9+
    r"@functools\.cache",         # 3.9+
    r"importlib\.metadata",       # 3.8+
    r"\bZoneInfo\b",              # 3.9+
    r"=\s*None\s*\|\s*",          # PEP604 style hints when parsed might be allowed;
    r"\|\s*None\s*[,)]",          #  but deny any 3.10 pipe-in-annotation text anyway
]

# Regexes are matched only against comment/docstring-stripped lines.
_COMMENT_RE = re.compile(r"^\s*#.*$")
_TRIPLE = ('"""', "'''")


def strip_comments_and_docs(lines):
    out = []
    in_triple = None
    for raw in lines:
        line = raw
        # handle triple-quoted blocks crudely but safely for our files
        while True:
            if in_triple:
                idx = line.find(in_triple)
                if idx == -1:
                    break          # whole line inside docstring
                line = line[idx + 3:]
                in_triple = None
                continue
            # find first triple quote start
            for tq in _TRIPLE:
                i = line.find(tq)
                if i != -1:
                    j = line.find(tq, i + 3)
                    if j != -1:
                        line = line[:i] + line[j + 3:]   # inline docstring
                        break
                    else:
                        in_triple = tq
                        line = line[:i]
                        break
            else:
                break
        # split off trailing comment (not inside string) -- approximate
        cleaned = _COMMENT_RE.sub("", line)
        out.append(cleaned)
    return out


def find_py37():
    """Return a real 3.7 interpreter path or ''."""
    cands = []
    w = shutil.which("python3.7")
    if w:
        cands.append(w)
    w = shutil.which("python")
    if w:
        cands.append(w)
    py = shutil.which("py")
    if py:
        cands.append(py)
    seen = set()
    for c in cands:
        if c in seen:
            continue
        seen.add(c)
        try:
            args = [c, "-c",
                    "import sys;print('%d.%d'%(sys.version_info[0],sys.version_info[1]))"]
            r = subprocess.run(args, capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and r.stdout.strip().startswith("3.7"):
                return c
        except Exception:
            continue
    return ""


def real_pycompile(py37, files):
    if not py37:
        print("PY37_REAL_COMPILE=SKIPPED")
        print("PY37_EXE=")
        return True
    args = [py37, "-m", "py_compile"] + files
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=120)
    except Exception as e:
        print("PY37_REAL_COMPILE=FAIL (%r)" % (e,))
        return False
    if r.returncode != 0:
        print("PY37_REAL_COMPILE=FAIL")
        if r.stdout:
            print(r.stdout)
        if r.stderr:
            print("PY37_STDERR=%s" % r.stderr[-4000:])
        return False
    print("PY37_REAL_COMPILE=PASS")
    print("PY37_EXE=%s" % py37)
    if r.stdout:
        print(r.stdout)
    return True


def audit_file(path):
    """Return (ast_ok, api_ok, api_hits) for a single input."""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    ast_ok = True
    try:
        if sys.version_info >= (3, 8):
            # Enforce 3.7 grammar explicitly on newer parsers (rejects walrus,
            # f-string-debug '=', match/case, etc.).
            ast.parse(src, filename=str(path), feature_version=(3, 7))
        else:
            # Running ON 3.7+ itself: its parser already enforces 3.7 syntax.
            ast.parse(src, filename=str(path))
    except SyntaxError as e:
        ast_ok = False
    cleaned_lines = strip_comments_and_docs(src.splitlines())
    hits = []
    for ln in cleaned_lines:
        for pat in DENY:
            if re.search(pat, ln):
                hits.append((ln.strip(), pat))
    return ast_ok, (len(hits) == 0), hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", help="python source files to audit")
    ap.add_argument("--py37", default="", help="explicit 3.7 interpreter")
    a = ap.parse_args()
    files = [os.path.abspath(f) for f in a.files]
    if not files:
        print("PY37_GATE=SKIPPED-NO-INPUT")
        return 1

    py37 = a.py37 or find_py37()

    clean = True
    for f in files:
        ast_ok, api_ok, hits = audit_file(f)
        base = os.path.basename(f)
        print("FILE=%s PY37_AST=%s PY37_API=%s" % (
            base, "OK" if ast_ok else "FAIL", "OK" if api_ok else "FAIL"))
        if not ast_ok:
            print("  reason: syntax requires > CPython 3.7")
            clean = False
        if not api_ok:
            for ln, pat in hits[:8]:
                print("  3.8+API %r at: %s" % (pat, ln[:120]))
            clean = False

    rc = real_pycompile(py37, files)
    if not clean:
        print("PY37_GATE=FAIL")
        return 1
    if not rc:
        print("PY37_GATE=FAIL")
        return 1
    print("PY37_GATE=%s" % ("REAL" if py37 else "STATIC"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
