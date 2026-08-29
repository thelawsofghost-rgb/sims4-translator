#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p27_report_check.py —— P27 report txt 语义校验 (纯 Python, 不受 PS5.1 编码影响)

由 scripts/ww_p27_deploy.ps1 调用。对 report txt 做机器校验, 输出 **ASCII-only** 结果。
Python 3 源码恒为 UTF-8, read_text(encoding="utf-8") 显式按 UTF-8 读取,
因此 .ps1 源码在 Windows PowerShell 5.1 下按 ANSI 解析导致的中文乱码问题, 在这里完全无关。

校验内容 (与 deploy 原逻辑一一对应):
  1. 含 VERDICT: PASS
  2. 含 ZERO_WRITE_TO_MODS=YES
  3. type = 0x7DF2169C
  4. instance = 0x43F3438A94EDEB2B
  5. 不得含白盒 instance 0x4444444400000002
  6. ordinal 299..306 各自原文 = 抓奸 1..抓奸 8
  7. 已替换校验: 8/8 OK

成功 (exit 0), 仅输出 ASCII:
  REPORT_CHECK=PASS
  TYPE=0x7DF2169C
  INSTANCE=0x43F3438A94EDEB2B
  TARGETS=8/8

失败 (exit != 0), 输出 ASCII reason code:
  REPORT_CHECK=FAIL
  REASON=<ASCII code>

reason 码:
  MISSING_VERDICT_PASS
  MISSING_ZERO_WRITE
  WRONG_TYPE
  WRONG_INSTANCE
  WHITEBOX_INSTANCE
  TARGET_<ordinal>_MISMATCH
  TARGET_COUNT_MISMATCH
  READ_ERROR
  NO_REPORT

退出码: 0=通过 / 2=文件缺失或不可读 / 3=任一项校验失败

用法: python scripts\\ww_p27_report_check.py "<report.txt>"
只读; 不改任何文件。
"""
import re
import sys
from pathlib import Path

EXPECTED_TYPE = "0x7DF2169C"
EXPECTED_INSTANCE = "0x43F3438A94EDEB2B"
WHITEBOX_INSTANCE = "0x4444444400000002"

# 唯一含中文的位置: 作为 Python 源码字面量 (UTF-8), 供匹配 report 内容.
TARGET_TXT = {n: f"抓奸 {i}" for i, n in enumerate(range(299, 307), start=1)}
VERIFIED_LINE = "已替换校验: 8/8"


def fail(code, exit_code=3):
    print("REPORT_CHECK=FAIL")
    print(f"REASON={code}")
    return exit_code


def main():
    if len(sys.argv) < 2:
        return fail("NO_REPORT", 2)
    p = Path(sys.argv[1])
    if not p.is_file():
        return fail("NO_REPORT", 2)

    try:
        rep = p.read_text(encoding="utf-8")
    except Exception:
        return fail("READ_ERROR", 2)
    if not rep.strip():
        return fail("NO_REPORT", 2)

    if "VERDICT:" not in rep or not re.search(r"VERDICT:\s*PASS", rep):
        return fail("MISSING_VERDICT_PASS")
    if "ZERO_WRITE_TO_MODS=YES" not in rep:
        return fail("MISSING_ZERO_WRITE")
    if "0x7DF2169C" not in rep or "type" not in rep.lower():
        return fail("WRONG_TYPE")
    if EXPECTED_INSTANCE not in rep:
        return fail("WRONG_INSTANCE")
    if WHITEBOX_INSTANCE in rep:
        return fail("WHITEBOX_INSTANCE")
    if VERIFIED_LINE not in rep:
        return fail("TARGET_COUNT_MISMATCH")

    for n, want in TARGET_TXT.items():
        # 兼容 'ordinal 299 raw = '抓奸 1'' 或 'ordinal  299 raw = '抓奸 1''
        pat = r"ordinal\s+%d\s+raw\s*=\s*'%s'" % (n, re.escape(want))
        if not re.search(pat, rep):
            return fail(f"TARGET_{n}_MISMATCH")

    print("REPORT_CHECK=PASS")
    print("TYPE=" + EXPECTED_TYPE)
    print("INSTANCE=" + EXPECTED_INSTANCE)
    print("TARGETS=8/8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
