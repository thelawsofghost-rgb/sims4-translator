#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p27_deploy_static_check.py —— 静态验证 P27 deploy v3 的三条核心不变式

在 Linux/纯静态环境即可运行 (不需要 Windows/PowerShell):
  A) ww_p27_report_check.py 对 report 必须显式 UTF-8 读取 (read_text(encoding='utf-8'))
  B) ww_p27_deploy.ps1 中不得包含用于安全判断的中文字面量
     (抓奸 / 已替换校验 等中文不得出现在 -match/-Contains/== 的判断上下文里)
  C) ww_p27_deploy.ps1 必须调用两个 Python validator:
       ww_p27_report_check.py 与 ww_p27_tgi_check.py

退出码:
  0 = 全部通过
  2 = 找不到脚本或无法读取
  3 = 任一检查未通过

用法: python scripts\\ww_p27_deploy_static_check.py
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PS1 = HERE / "ww_p27_deploy.ps1"
REPORT_CHECK = HERE / "ww_p27_report_check.py"

# 中文安全字面量 (出现在安全判断里就必须拒绝)
CN_LITERALS = ["抓奸", "已替换校验"]


def chk_report_utf8():
    if not REPORT_CHECK.is_file():
        return False, f"missing {REPORT_CHECK.name}"
    t = REPORT_CHECK.read_text(encoding="utf-8")
    if "read_text(encoding=\"utf-8\")" not in t and "read_text(encoding='utf-8')" not in t:
        return False, "report_check 未显式 UTF-8 读取"
    return True, "report_check UTF-8 读取: OK"


def chk_ps1_no_cn_safety():
    if not PS1.is_file():
        return False, f"missing {PS1.name}"
    t = PS1.read_text(encoding="utf-8")
    # 只允许中文出现在注释/字符串头部描述里; 禁止出现在匹配判断中.
    # 做法: 去掉注释行后, 检查剩余代码是否仍含中文.
    code_only = "\n".join(
        ln for ln in t.splitlines()
        if not ln.strip().startswith("#")
    )
    hits = [w for w in CN_LITERALS if w in code_only]
    if hits:
        return False, f"ps1 代码内含中文安全字面量: {hits}"
    return True, "ps1 无中文安全判断字面量: OK"


def chk_ps1_calls_validators():
    if not PS1.is_file():
        return False, f"missing {PS1.name}"
    t = PS1.read_text(encoding="utf-8")
    has_rep = "ww_p27_report_check.py" in t
    has_tgi = "ww_p27_tgi_check.py" in t
    if not (has_rep and has_tgi):
        return False, "ps1 未同时调用 report_check 与 tgi_check"
    return True, "ps1 双重 validator 调用: OK"


def main():
    checks = [
        ("A. report_check UTF-8", chk_report_utf8),
        ("B. ps1 无中文安全字面量", chk_ps1_no_cn_safety),
        ("C. ps1 双重 validator", chk_ps1_calls_validators),
    ]
    all_ok = True
    for name, fn in checks:
        ok, msg = fn()
        all_ok = all_ok and ok
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {msg}")
    if not all_ok:
        print("VERDICT=FAIL (exit 3)", file=sys.stderr)
        return 3
    print("VERDICT=PASS: 静态不变式全通过 (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
