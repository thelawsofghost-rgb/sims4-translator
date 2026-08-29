#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p27_deploy_static_check.py —— 静态验证 ww_p27_deploy.ps1 的 report 读取为显式 UTF-8

在 Linux/纯静态环境即可运行 (不需要 Windows/PowerShell):
  1. 定位 $REPORT_TXT 被 Get-Content 读取的那一行/入口
  2. 断言该读取必须带 -Encoding UTF8 (不允许依赖 Windows PowerShell 默认编码)
  3. 断言不存在对 report 的、不带 UTF8 的直接 Get-Content 读取

退出码:
  0 = 通过 (report 读取为显式 UTF-8)
  2 = 找不到 ps1 或无法读取
  3 = 静态检查未通过 (发现 report 读取未显式 UTF-8, 或找不到唯一读取入口)

用法: python scripts\\ww_p27_deploy_static_check.py
"""
import re
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve()
PS1 = SCRIPT.parent / "ww_p27_deploy.ps1"


def main():
    if not PS1.is_file():
        print(f"ERROR: 找不到 {PS1} (exit 2)", file=sys.stderr)
        return 2
    text = PS1.read_text(encoding="utf-8")

    # 找 report 变量赋值 (REPORT_TXT) 与读取点
    read_lines = []
    for ln in text.splitlines():
        # 只关心对 report 变量/路径的 Get-Content 调用
        if "Get-Content" in ln and ("REPORT_TXT" in ln or "ww_p27_displayname_override.txt" in ln):
            read_lines.append(ln.strip())

    if not read_lines:
        print(f"ERROR: 未找到读取 report 的 Get-Content 行 (exit 3)", file=sys.stderr)
        return 3

    ok = True
    for ln in read_lines:
        has_utf8 = re.search(r"-Encoding\s+UTF8", ln, re.IGNORECASE) is not None
        # 允许 'UTF8' 或 'utf8' (PowerShell 大小写不敏感)
        marker = "PASS" if has_utf8 else "FAIL"
        print(f"[{marker}] {ln}")
        if not has_utf8:
            ok = False

    # 额外: 不允许同一文件中还有其他未加 UTF8 的对 report 的读取 (唯一入口要求)
    if not ok:
        print("VERDICT=FAIL: report 读取未显式 UTF-8 (exit 3)", file=sys.stderr)
        return 3

    print("VERDICT=PASS: report 读取为显式 UTF-8 (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
