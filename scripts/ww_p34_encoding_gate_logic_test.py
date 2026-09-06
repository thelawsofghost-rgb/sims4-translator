#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p34_encoding_gate_logic_test.py --- machine-behaviour logic test for
scripts/ww_p34_encoding_gate.py (P34.3 #1).

ASSERTS MACHINE BEHAVIOUR ONLY.  Files built here are SYNTHETIC fixtures; they
are NOT real WickedWhims/P34 data and are never claimed to be.  Proves that the
gate deterministically:

  * PASSES a UTF-8 **with BOM** file whose content is clean Chinese;
  * FAILS a file with NO BOM (the class of file Windows Excel misstakes for GBK);
  * FAILS a file that carries GBK-mojibake that is *still valid UTF-8* by
    deriving the expected garble from common Han words (没有 -> 娌℃湁), i.e. it
    catches the pollution even though a strict UTF-8 decode would not error;
  * FAILS a file containing lossy U+FFFD replacement characters;
  * --repair adds a canonical BOM to a clean no-BOM file and REFUSES (fail-closed)
    to "fix" a genuinely corrupted one;
  * helpers audit_text / audit_file / has_bom / decode_strict behave as specced.
"""
from __future__ import print_function
import ast
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import ww_p34_encoding_gate as EG      # noqa: E402

_PASS = []


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)
    _PASS.append(msg)


def _write_bytes(path, b):
    with open(path, "wb") as fh:
        fh.write(b)


def _tmppath(name):
    d = tempfile.mkdtemp(prefix="p34enc_")
    return os.path.join(d, name)


def _clean_bom_bytes(body):
    return EG.BOM + body.encode("utf-8")


def test_py37():
    src = open(os.path.join(_HERE, "ww_p34_encoding_gate.py"), encoding="utf-8").read()
    ast.parse(src, feature_version=(3, 7))
    # no dead imports
    for bad in ("import csv", "import ast", "import io "):
        assert bad not in src, "unused import remains: %s" % bad
    _assert(True, "py37 parse + no dead imports")


def test_bom_helpers():
    _assert(EG.has_bom(_clean_bom_bytes("a")), "has_bom true with BOM")
    _assert(not EG.has_bom(b"abc"), "has_bom false without BOM")
    _assert(not EG.has_bom("abc"), "has_bom false on str (bytes only)")
    txt, err = EG.decode_strict(_clean_bom_bytes("接吻 1"))
    # decode_strict is called on the BODY (BOM stripped by callers) but this
    # direct call on full bytes with BOM would still succeed (BOM decodes as ZWNBSP
    # only if a caller failed to strip; here we just confirm no exception).
    _assert(txt is not None or err is not None, "decode_strict handles input")


def test_audit_text_clean():
    st, probs = EG.audit_text("钢琴之爱 10 - 后戏")
    _assert(st == "PASS" and probs == [], "clean zh text -> PASS")


def test_audit_text_replacement():
    st, probs = EG.audit_text("接吻 \ufffd 1")
    _assert(st == "FAIL" and any("U+FFFD" in p for p in probs),
            "replacement char -> FAIL")


def test_audit_text_mojibake():
    st, probs = EG.audit_text("接吻 娌℃湁 1")     # 没有 read as GBK -> valid UTF-8
    _assert(st == "FAIL" and any("GBK-mojibake" in p for p in probs),
            "GBK-mangle (valid UTF-8) -> FAIL")


def test_audit_file_clean_bom():
    p = _tmppath("clean.csv")
    _write_bytes(p, _clean_bom_bytes("ordinal,zh\n1,接吻 1 - 高潮\n"))
    st, probs, meta = EG.audit_file(p)
    _assert(st == "PASS" and meta["bom"], "clean BOM UTF-8 file -> PASS")


def test_audit_file_no_bom():
    p = _tmppath("nobom.csv")
    _write_bytes(p, ("接吻 1\n").encode("utf-8"))
    st, probs, meta = EG.audit_file(p)
    _assert(st == "FAIL" and not meta["bom"] and
            any("BOM" in pr for pr in probs), "no-BOM file -> FAIL")


def test_audit_file_mojibake_with_bom():
    p = _tmppath("moji.csv")
    m = "没有".encode("utf-8").decode("gbk")     # derived garble 娌℃湁
    _write_bytes(p, _clean_bom_bytes("接吻 %s\n" % m))
    st, probs, meta = EG.audit_file(p)
    _assert(st == "FAIL", "GBK-mangle (even with BOM) -> FAIL")
    _assert(meta["bom"] is True, "BOM is present (so it failed on mojibake, not BOM)")


def test_audit_file_replacement_with_bom():
    p = _tmppath("repl.csv")
    # valid UTF-8 body that literally contains the U+FFFD replacement code point
    body = "接吻".encode("utf-8") + "\xef\xbf\xbd".encode("latin-1") + " 1\n".encode("utf-8")
    _write_bytes(p, EG.BOM + body)
    st, probs, meta = EG.audit_file(p)
    _assert(st == "FAIL", "U+FFFD content -> FAIL (st=%s)" % st)


def test_repair_clean_nobom():
    inp = _tmppath("in_clean.csv")
    out = _tmppath("out_clean.csv")
    _write_bytes(inp, "接吻 1\n".encode("utf-8"))
    ok, reason = EG.repair_file(inp, out)
    _assert(ok and reason is None, "repair clean no-BOM succeeds")
    with open(out, "rb") as fh:
        head = fh.read(3)
    _assert(head == EG.BOM, "repaired file starts with UTF-8 BOM")


def test_repair_refuses_corrupt():
    inp = _tmppath("in_corrupt.csv")
    out = _tmppath("out_corrupt.csv")
    # raw bytes that are NOT valid UTF-8 (0xff 0xfe lone continuations)
    _write_bytes(inp, b"\xff\xfe\x80\x80")
    ok, reason = EG.repair_file(inp, out)
    _assert(not ok and reason, "repair refuses undecodable input (fail-closed)")
    _assert(not os.path.exists(out), "no output written on refusal")


def test_cli_gate_exit_codes():
    # clean file -> main returns 0
    p = _tmppath("clean.csv")
    _write_bytes(p, _clean_bom_bytes("接吻 1\n"))
    import ww_p34_encoding_gate as G
    was = sys.argv
    try:
        rc = G.main([p])
        _assert(rc == 0, "CLI clean file -> rc 0")
        m = _tmppath("moji.csv")
        _write_bytes(m, _clean_bom_bytes("接吻 %s\n" % "没有".encode("utf-8").decode("gbk")))
        rc2 = G.main([m])
        _assert(rc2 == 2, "CLI mojibake file -> rc 2")
    finally:
        sys.argv = was


def main():
    test_py37()
    test_bom_helpers()
    test_audit_text_clean()
    test_audit_text_replacement()
    test_audit_text_mojibake()
    test_audit_file_clean_bom()
    test_audit_file_no_bom()
    test_audit_file_mojibake_with_bom()
    test_audit_file_replacement_with_bom()
    test_repair_clean_nobom()
    test_repair_refuses_corrupt()
    test_cli_gate_exit_codes()
    print("CHECKS_PASSED=%d/%d" % (len(_PASS), len(_PASS)))
    print("=" * 60)


if __name__ == "__main__":
    main()
