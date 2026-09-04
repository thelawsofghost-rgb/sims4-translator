#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p28b0_full_clone.py —— P28B-0 整包 byte-identical clone 生成器 (P28B-0 唯一生成路径)

对真实源包做【整个文件字节不变】的完整 clone, 完全绕开本项目任何 DBPF writer:

  * 不调用 build_package()
  * 不读取/重建 XML
  * 不修改 XML / resource / TGI / header / index / compression metadata / mem_size / payload
  * 只允许: 读取整包 bytes -> 原样写出到 <out>/WW_Nevely42_Animations.package

验证 (不以 SHA 自满, 双保险):
  1. 逐块 SHA256
  2. 逐字节 chunk 比对 (不等长或任一块不等即 FAIL_CLONE_CHANGED)

产出 report (ASCII):
  SOURCE_PACKAGE_PATH=<...>
  CLONE_PACKAGE_PATH=<...>
  SOURCE_PACKAGE_SIZE=<int>
  CLONE_PACKAGE_SIZE=<int>
  SOURCE_PACKAGE_SHA256=<hex>
  CLONE_PACKAGE_SHA256=<hex>
  PACKAGE_BYTE_IDENTICAL=YES|NO
  PACKAGE_SIZE_EQUAL=YES|NO
  DBPF_MAGIC_VERIFY=YES|NO        (只读头部 4 字节 DBPF sanity, 不 parse)
  ZERO_WRITE_TO_MODS=YES
  VERDICT=PASS|FAIL
  REASON=<code>

安全:
  * 源包绝不被写/移动/改名; 只读打开。
  * 只写 <out>/WW_Nevely42_Animations.package (+ report)。
  * ZERO_WRITE_TO_MODS=YES (本脚本不感知 Mods 路径; 输出只在用户指定 out-dir)。

退出码:
  0 = PASS (clone 字节一致)
  2 = source 缺失
  3 = source 非可读文件 / 头部非 DBPF 魔数 (fail-closed)
  4 = 已存在同名输出且未给 --force (避免覆盖混淆; 给 --force 则覆盖)
  5 = 字节比对不一致 (clone != source -> 不产出 PASS)

用法:
  python scripts\\ww_p28b0_full_clone.py --source "<WW_Nevely42_Animations.package>"
  python scripts\\ww_p28b0_full_clone.py --source X.package --out-dir "output\\ww_p28b0" --force
"""
import argparse
import hashlib
import shutil
import sys
from pathlib import Path

BLOCK = 1 << 20


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            blk = f.read(BLOCK)
            if not blk:
                break
            h.update(blk)
    return h.hexdigest().lower()


def byte_identical(a: Path, b: Path) -> bool:
    """逐块 + 逐字节 chunk 比对, 不只是信任各自 SHA. 任一块不等即 False."""
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


def is_dbpf_magic(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            return f.read(4) == b"DBPF"
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--out-dir", default="output/ww_p28b0")
    ap.add_argument("--force", action="store_true", help="允许覆盖已存在的同名 clone")
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print("SOURCE_PACKAGE_PATH=%s" % src)
        print("CLONE_PACKAGE_PATH=")
        print("VERDICT=FAIL")
        print("REASON=SOURCE_MISSING")
        return 2

    # 只读魔数 sanity: 不 parse, 不重建; 仅确认这是 DBPF 形态 (Windows 上真实 .package 必为 DBPF).
    dbpf_ok = is_dbpf_magic(src)
    print(f"DBPF_MAGIC_VERIFY={'YES' if dbpf_ok else 'NO'}")
    if not dbpf_ok:
        print("SOURCE_PACKAGE_PATH=%s" % src)
        print("VERDICT=FAIL")
        print("REASON=SOURCE_NOT_DBPF_MAGIC")
        return 3

    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    clone = out_dir / src.name  # 与 source 同名 (WW_Nevely42_Animations.package), cfg 才能用 <dir>/*.package 指到

    if clone.exists() and not a.force:
        print("SOURCE_PACKAGE_PATH=%s" % src)
        print(f"CLONE_PACKAGE_PATH={clone}")
        print("VERDICT=FAIL")
        print("REASON=CLONE_EXISTS_NEED_FORCE")
        return 4

    src_sha = sha256_file(src)
    src_size = src.stat().st_size

    shutil.copyfile(src, clone)  # 原始字节复制 (不解析)
    clone_sha = sha256_file(clone)
    clone_size = clone.stat().st_size

    identical = byte_identical(src, clone)      # 逐字节 chunk 比对
    sha_match = (src_sha == clone_sha)          # 双保险 SHA
    size_eq = (src_size == clone_size)
    pass_ = identical and sha_match and size_eq and dbpf_ok

    print(f"SOURCE_PACKAGE_PATH={src}")
    print(f"CLONE_PACKAGE_PATH={clone}")
    print(f"SOURCE_PACKAGE_SIZE={src_size}")
    print(f"CLONE_PACKAGE_SIZE={clone_size}")
    print(f"SOURCE_PACKAGE_SHA256={src_sha}")
    print(f"CLONE_PACKAGE_SHA256={clone_sha}")
    print(f"PACKAGE_BYTE_IDENTICAL={'YES' if identical else 'NO'}")
    print(f"PACKAGE_SIZE_EQUAL={'YES' if size_eq else 'NO'}")
    print(f"PACKAGE_SHA_EQUAL={'YES' if sha_match else 'NO'}")
    print("ZERO_WRITE_TO_MODS=YES")
    print(f"VERDICT={'PASS' if pass_ else 'FAIL'}")
    print(f"REASON={'OK' if pass_ else 'CLONE_BYTE_MISMATCH'}")
    # report 落盘 (ASCII), 供 deploy 侧 report_check 读取
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        rep = out_dir / "ww_p28b0_clone_report.txt"
        rep.write_text("\n".join([
            f"SOURCE_PACKAGE_PATH={src}",
            f"CLONE_PACKAGE_PATH={clone}",
            f"SOURCE_PACKAGE_SIZE={src_size}",
            f"CLONE_PACKAGE_SIZE={clone_size}",
            f"SOURCE_PACKAGE_SHA256={src_sha}",
            f"CLONE_PACKAGE_SHA256={clone_sha}",
            f"PACKAGE_BYTE_IDENTICAL={'YES' if identical else 'NO'}",
            f"PACKAGE_SIZE_EQUAL={'YES' if size_eq else 'NO'}",
            f"PACKAGE_SHA_EQUAL={'YES' if sha_match else 'NO'}",
            "ZERO_WRITE_TO_MODS=YES",
            f"VERDICT={'PASS' if pass_ else 'FAIL'}",
            f"REASON={'OK' if pass_ else 'CLONE_BYTE_MISMATCH'}",
        ]) + "\n", encoding="ascii")
    except Exception as e:
        print(f"REPORT_WRITE_ERROR={e}")
        return 5

    return 0 if pass_ else 5


if __name__ == "__main__":
    sys.exit(main())
