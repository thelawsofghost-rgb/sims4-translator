#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p28b0_full_clone_report_check.py —— P28B-0 report 机验 (与生成器相互独立, 可信任分离)

独立重读 ww_p28b0_clone_report.txt 并逐条校验 (不信任生成器 stdout; 完全以 report 内容为准),
并在 Python 侧对 report 声称的 SOURCE/CLONE 两个实际文件做独立逐字节比对 (不依赖 report 里
声称的 identical=YES 字样; 重新读文件比较)  —— 双独立校验。

校验条件 (全满足才 PASS):
  1. report 存在且 UTF-8 可读
  2. PACKAGE_BYTE_IDENTICAL=YES  (report 内声明)
  3. 实际文件独立重比 -> PACKAGE_SIZE_EQUAL + PACKAGE_BYTE_IDENTICAL (report_check 自己算)
  4. report 内 SOURCE_SHA 与 CLONE_SHA 一致
  5. report VERDICT=PASS
  6. ZERO_WRITE_TO_MODS=YES
  7. SOURCE_PACKAGE_PATH 真实存在, 且是 DBPF 魔数 (只读 sanity)
  8. CLONE_PACKAGE_PATH 真实存在

输出 (ASCII):
  REPORT_CHECK=PASS|FAIL
  REASON=<code>

退出码:
  0 = PASS
  2 = report 缺失/参数缺
  3 = report 格式/条件未满足 (FAIL)
  4 = 独立字节比对不一致 (SOURCe vs CLONE)
"""
import re
import sys
from pathlib import Path

BLOCK = 1 << 20


def sha256_file(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(BLOCK)
            if not b:
                break
            h.update(b)
    return h.hexdigest().lower()


def byte_identical(a: Path, b: Path) -> bool:
    if a.stat().st_size != b.stat().st_size:
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            ba = fa.read(BLOCK)
            bb = fb.read(BLOCK)
            if ba != bb:
                return False
            if not ba:
                return True


def _val(lines, key):
    for ln in lines:
        if ln.startswith(key + "="):
            return ln.split("=", 1)[1].strip()
    return None


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: ww_p28b0_full_clone_report_check.py <report.txt>")
        return 2
    rep = Path(sys.argv[1])
    if not rep.is_file():
        print("REPORT_CHECK=FAIL")
        print("REASON=REPORT_MISSING")
        return 2
    try:
        text = rep.read_text(encoding="utf-8")
    except Exception as e:
        print("REPORT_CHECK=FAIL")
        print(f"REASON=REPORT_READ_ERROR:{e}")
        return 3
    lines = text.splitlines()

    intact = _val(lines, "PACKAGE_BYTE_IDENTICAL")
    size_eq = _val(lines, "PACKAGE_SIZE_EQUAL")
    sha_eq = _val(lines, "PACKAGE_SHA_EQUAL")
    verd = _val(lines, "VERDICT")
    zw = _val(lines, "ZERO_WRITE_TO_MODS")
    src_p = _val(lines, "SOURCE_PACKAGE_PATH")
    cln_p = _val(lines, "CLONE_PACKAGE_PATH")
    src_sha = _val(lines, "SOURCE_PACKAGE_SHA256")
    cln_sha = _val(lines, "CLONE_PACKAGE_SHA256")

    ok = (
        intact == "YES" and size_eq == "YES" and sha_eq == "YES"
        and verd == "PASS" and zw == "YES"
        and src_p and cln_p and src_sha and cln_sha and src_sha == cln_sha
    )
    if not ok:
        print("REPORT_CHECK=FAIL")
        print(f"REASON=REPORT_CONDITIONS_UNMET intact={intact} size={size_eq} sha={sha_eq} verd={verd} zw={zw}")
        return 3

    src = Path(src_p)
    cln = Path(cln_p)
    if not src.is_file() or not cln.is_file():
        print("REPORT_CHECK=FAIL")
        print("REASON=CLAIMED_FILE_MISSING")
        return 3

    # 独立逐字节比对 (report_check 自己算, 不信任 report 的 YES 字样)
    try:
        real_ident = byte_identical(src, cln)
        real_size = src.stat().st_size == cln.stat().st_size
        real_sha = sha256_file(src) == sha256_file(cln) and sha256_file(src) == src_sha.lower()
    except Exception as e:
        print("REPORT_CHECK=FAIL")
        print(f"REASON=INDEPENDENT_COMPARE_ERROR:{e}")
        return 4
    if not (real_ident and real_size and real_sha):
        print("REPORT_CHECK=FAIL")
        print(f"REASON=INDEPENDENT_COMPARE_MISMATCH ident={real_ident} size={real_size} sha={real_sha}")
        return 4

    # DBPF 魔数只读 sanity on source (不 parse)
    try:
        with open(src, "rb") as f:
            magic = f.read(4)
    except Exception:
        magic = b""
    if magic != b"DBPF":
        print("REPORT_CHECK=FAIL")
        print("REASON=SOURCE_NOT_DBPF_MAGIC")
        return 3

    print("REPORT_CHECK=PASS")
    print(f"SOURCE_PACKAGE_SHA256={_val(lines, 'SOURCE_PACKAGE_SHA256')}")
    print(f"CLONE_PACKAGE_SHA256={_val(lines, 'CLONE_PACKAGE_SHA256')}")
    print(f"PACKAGE_BYTE_IDENTICAL={real_ident and 'YES' or 'NO'}")
    print("ZERO_WRITE_TO_MODS=YES")
    print(f"SOURCE_PACKAGE_PATH={src_p}")
    print(f"CLONE_PACKAGE_PATH={cln_p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
