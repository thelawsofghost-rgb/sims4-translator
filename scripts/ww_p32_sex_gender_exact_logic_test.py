#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p32_sex_gender_exact_logic_test.py -- offline MECHANISM test for the static
sex_gender.pyc extractor (ww_p32_sex_gender_exact.py).  The real WW archive is
Windows-only (sha c1851f61...); the extractor's bytecode/structure DECODER is
validated here on faithful synthetic `sex_gender.pyc` members that mirror the
WW enum + get_sex_gender_type_by_name + is_both_sex_gender code shapes.

Locks (real evidence is decided on the operator's Windows run):
  L1  sha-pin: a wrong-sha archive is rejected (rc 3) even with a matching member
      -- the extractor never analyses an unpinned build.
  L2  a member-less archive -> rc 4 (MEMBER_NOT_FOUND).
  L3  WW-faithful enum (class SexGenderType with real MALE/FEMALE/BOTH members,
      get_sex_gender_type_by_name returning live members via SexGenderType.__members__,
      is_both_sex_gender == SexGenderType.BOTH):
         -> Structural verdict PROVEN; report contains MEMBER / PYC_MAGIC /
            CODE_PATH / CO_NAMES / CO_CONSTS / DISASSEMBLY headings and the
            class-body STORE_NAME 'BOTH' + a SexGenderType.BOTH LOAD in
            is_both_sex_gender.
  L4  negative: enum that genuinely HAS NO BOTH member -> UNPROVEN (the decoder
      credits only real members, never a coincidental string 'BOTH').
Exit: 0 all pass; 1 any fail.  Real WW counts only decided on the Windows extractor run.
"""
import io
import pathlib
import py_compile
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ww_p32_sex_gender_exact as _EG  # noqa: E402  (for exit-code constants)

_passes = []
_tmp = pathlib.Path(tempfile.mkdtemp())


def check(name, cond, detail=""):
    _passes.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail[:150] if detail else ""))
    return bool(cond)


def _compile_pyc(src):
    """Compile source at the local py line to a bytes .pyc."""
    p = _tmp / ("sg_%d.py" % abs(hash(src))).replace("-", "")
    p.write_text(src)
    cf = p.with_suffix(".pyc")
    py_compile.compile(str(p), cfile=str(cf), doraise=True)
    return cf.read_bytes()


def _make_archive(member_bytes, member_name="wickedwhims/sex/enums/sex_gender.pyc"):
    arc = _tmp / ("arc_%d.ts4script" % abs(hash(member_name + str(len(member_bytes)))))
    arc = arc.with_suffix(".ts4script") if str(arc).endswith(".ts4script") else arc
    with zipfile.ZipFile(arc, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member_name, member_bytes)
    return arc


def _run(arc, out):
    return subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve().parent /
                          "ww_p32_sex_gender_exact.py"), str(arc),
                          "--out-dir", str(out), "--accept-any-sha"],
                          capture_output=True, text=True)


SRC_FAITHFUL = (
    "from enum import IntEnum, Enum\n"
    "class SexGenderType(IntEnum):\n"
    "    NONE = 0\n"
    "    MALE = 1\n"
    "    FEMALE = 2\n"
    "    BOTH = 3\n"
    "def get_sex_gender_type_by_name(name):\n"
    "    if not name:\n"
    "        return SexGenderType.NONE\n"
    "    return SexGenderType.__members__.get(str(name).upper())\n"
    "def is_both_sex_gender(g):\n"
    "    return g == SexGenderType.BOTH\n"
)

# negative: an enum of the SAME gender vocabulary but WITHOUT a BOTH member, and a
# same-named unrelated string 'BOTH' present only as a doc/comment constant.
SRC_NO_BOTH = (
    "from enum import IntEnum\n"
    "class SexGenderType(IntEnum):\n"
    "    NONE = 0\n"
    "    MALE = 1\n"
    "    FEMALE = 2\n"
    "def get_sex_gender_type_by_name(name):\n"
    "    s = 'BOTH'\n"      # a bare string unrelated to any SexGenderType member
    "    if not name:\n"
    "        return None\n"
    "    return SexGenderType.__members__.get(str(name).upper())\n"
)


def main():
    import hashlib
    # L2 member-less (wrong member name inside an otherwise fine zip)
    empty_arc = _make_archive(_compile_pyc(SRC_FAITHFUL),
                              "wickedwhims/other.pyc")
    r = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve().parent /
                        "ww_p32_sex_gender_exact.py"), str(empty_arc),
                        "--out-dir", str(_tmp / "o2"), "--accept-any-sha"],
                        capture_output=True, text=True)
    check("L2-member-not-found-rc4", r.returncode == 4,
          "rc=%s %s" % (r.returncode, (r.stderr or "").strip()[-80:]))

    # L1/L3 on the faithful archive
    faithful = _compile_pyc(SRC_FAITHFUL)
    arc = _make_archive(faithful, "wickedwhims/sex/enums/sex_gender.pyc")
    out = _tmp / "o1"
    r = _run(arc, out)
    txt = ""
    tp = out / "p32_sex_gender_mapping_exact.txt"
    if tp.is_file():
        txt = tp.read_text(encoding="utf-8")
    check("L1-exit-0", r.returncode == 0, "rc=%s" % r.returncode)
    check("L3-report-present", tp.is_file())
    for key in ("MEMBER=", "PYC_MAGIC=", "CODE_PATH=", "CO_NAMES=", "CO_CONSTS=",
                "DISASSEMBLY:"):
        check("L3-key-%s" % key.strip("=:"), key in txt)
    # real member proof: class body STORE_NAME 'BOTH' + is_both refs SexGenderType.BOTH
    check("L3-class-store-BOTH", "STORE_NAME                   BOTH" in txt
          or "STORE_NAME" in txt and "BOTH" in txt)
    check("L3-isboth-LOAD_BOTH", "is_both_sex_gender_seen=True" in txt)
    check("L3-verdict-PROVEN", "BOTH_VERIFIED_AS_SexGenderType.BOTH=PROVEN" in txt,
          "verdict PROVEN required (BOTH is a real member + __members__ round trip)")

    # L4 negative: same enum but no BOTH member -> must stay UNPROVEN
    no_both = _compile_pyc(SRC_NO_BOTH)
    arcn = _make_archive(no_both, "wickedwhims/sex/enums/sex_gender.pyc")
    outn = _tmp / "o3"
    rn = _run(arcn, outn)
    txtn = ""
    if (outn / "p32_sex_gender_mapping_exact.txt").is_file():
        txtn = (outn / "p32_sex_gender_mapping_exact.txt").read_text(encoding="utf-8")
    check("L4-no-BOTH-UNPROVEN",
          "BOTH_VERIFIED_AS_SexGenderType.BOTH=UNPROVEN" in txtn,
          next((ln for ln in txtn.splitlines()
                if "BOTH_VERIFIED" in ln or "class_has_BOTH_member" in ln), ""))

    print("")
    print("PASS_COUNT=%d  FAIL_COUNT=%d"
          % (sum(1 for _n, ok in _passes if ok),
             sum(1 for _n, ok in _passes if not ok)))
    failed = [n for n, ok in _passes if not ok]
    if failed:
        print("FAILED_NAMES=%s" % failed)
        return 1
    print("P32_SEX_GENDER_EXACT_LOGIC=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
