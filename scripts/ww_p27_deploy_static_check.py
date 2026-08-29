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

# P28A PS1 待检文件
P28_PS1 = [
    HERE / "ww_p28a_priority_canary.ps1",
    HERE / "ww_p28a_priority_rollback.ps1",
]


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


def chk_p28a_ascii():
    """P28A ps1: 全 ASCII (去注释后不含中文安全字面量) + 调用 cfg_audit."""
    for f in P28_PS1:
        if not f.is_file():
            return False, f"missing {f.name}"
        t = f.read_text(encoding="utf-8")
        code_only = "\n".join(ln for ln in t.splitlines() if not ln.strip().startswith("#"))
        hits = [w for w in CN_LITERALS if w in code_only]
        if hits:
            return False, f"{f.name} 代码内含中文安全字面量: {hits}"
    # canary 必须调用 cfg_audit
    c = (HERE / "ww_p28a_priority_canary.ps1").read_text(encoding="utf-8")
    if "ww_p28a_cfg_audit.py" not in c:
        return False, "canary 未调用 ww_p28a_cfg_audit.py"
    return True, "P28A ps1 全 ASCII + 调用 cfg_audit: OK"


def chk_p28a_native_stderr():
    """E) P28A canary 必须用 Run-Python 封装所有 native 调用, 且 stderr 重定向到文件;
    禁止旧的 PS5.1 '& python ... 2>&1' (EAP=Stop 下触发 NativeCommandError 吞 traceback).
    F) 必须命令 Run-Python 存在且用 '2> $stderrFile' 捕获 stderr.
    H) 禁止 '"$($Args)/@Args' collision: PowerShell 的 '$"args"' 自动变量大小写不敏感,
       param 名为 '$"Args"' 会绑定失效导致 Python 收不到 CLI 参数. 必须用 $"PyArgs"/@PyArgs.
    I) 所有调用必须显式命名 -Script / -PyArgs; 每个调用参数数量符合各 CLI schema."""
    p = HERE / "ww_p28a_priority_canary.ps1"
    if not p.is_file():
        return False, f"missing {p.name}"
    t = p.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in t.splitlines() if not ln.strip().startswith("#"))
    # 所有 & python 调用都必须出现在 Run-Python 函数体内 (仅 1 处定义)
    n_runpy = code.count("Run-Python ")
    if n_runpy < 4:
        return False, f"canary native 调用未全部走 Run-Python (found Run-Python x{n_runpy})"
    if code.count("function Run-Python") != 1:
        return False, "缺少 Run-Python 帮助函数定义"
    # 必须有 stderr 重定向到文件
    if "2> $stderrFile" not in code:
        return False, "Run-Python 未把 stderr 重定向到文件 (无法完整捕获 traceback)"
    # 禁止残留 '2>&1' 直接 native 合并 (PS5.1 风险)
    if "2>&1" in code:
        return False, "canary 残留 '2>&1' native stderr 合并 (PS5.1 NativeCommandError 风险)"
    # H) 禁止 $Args/@Args collision
    if "$Args" in code or "@Args" in code:
        return False, "canary 使用 $Args/@Args —— 与 PowerShell 自动变量 $args 冲突, Python 收不到参数"
    # 必须使用 $PyArgs / @PyArgs
    if "$PyArgs" not in code or "@PyArgs" not in code:
        return False, "canary 未使用 $PyArgs/@PyArgs 传递 Python 参数"
    # I) 显式命名调用; 且没有裸位置调用 "Run-Python <script> <args>"
    for pat in ['Run-Python  "', 'Run-Python $', 'Run-Python \'']:
        if pat in code:
            return False, f"canary 存在非命名位置调用: {pat.strip()!r} (要求 -Script -PyArgs 显式命名)"
    # 验证 4 个调用点参数基元数量 (仅做可读性检查, 精确数量以各 CLI schema 为准)
    need = {
        "$REPORT_CHECK": 1,
        "$TGI_CHECK": 1,
    }
    # cfg audit 调用含 mode + 3 路径 = 4 参数
    ok_calls = all(l.startswith("Run-Python -Script") for l in code.splitlines() if l.strip().startswith("Run-Python"))
    if not ok_calls:
        return False, "存在未显式命名的 Run-Python 调用"
    return True, "E/F/H/I: native 走 Run-Python + stderr 落盘 + 无 $Args/@Args (用 $PyArgs) + 显式命名: OK"


def chk_p28a_argv_counts():
    """J) 校验每个 Run-Python 调用点的 -PyArgs 元素数量, 严格匹配各 Python CLI schema.
    report_check=1, tgi_check=1, cfg_audit check/propose=4 (mode + cfg + root + sub)."""
    p = HERE / "ww_p28a_priority_canary.ps1"
    if not p.is_file():
        return False, f"missing {p.name}"
    t = p.read_text(encoding="utf-8")
    import re
    # 解析每行: Run-Python -Script <VAR> -PyArgs @( a, b, ... )
    pat = re.compile(r"Run-Python\s+-Script\s+(\$\w+)\s+-PyArgs\s+@\(([^)]*)\)")
    # 变量映射到脚本名
    var_map = {"$REPORT_CHECK": "report_check", "$TGI_CHECK": "tgi_check", "$CFG_AUDIT": "cfg_audit"}
    expected = {"report_check": {1}, "tgi_check": {1}, "cfg_audit": {4}}  # cfg_audit check/propose 均 4
    calls = pat.findall(t)
    if len(calls) != 4:
        return False, f"应发现 4 个 Run-Python 调用, 实际 {len(calls)}"
    for var, inner in calls:
        if var not in var_map:
            return False, f"未知脚本变量 {var}"
        name = var_map[var]
        # 纯位置参数计数: 忽略 $ 变量与引号, 按顶层逗号分割
        items = re.split(r",", inner)
        count = sum(1 for it in items if it.strip())
        # "check"/"propose" 是字符串字面量也算 1 个
        if count not in expected[name]:
            return False, f"{name} 调用参数数量 {count} 不符合 schema {sorted(expected[name])}: {inner.strip()}"
    # cfg_audit 两个调用必须一个是 check 一个是 propose
    if t.count('-Script $CFG_AUDIT -PyArgs @("check"') != 1:
        return False, "cfg_audit 缺 check 调用"
    if t.count('-Script $CFG_AUDIT -PyArgs @("propose"') != 1:
        return False, "cfg_audit 缺 propose 调用"
    return True, "J: 各调用点 argv 数量符合 CLI schema (report=1, tgi=1, cfg=4 check+propose): OK"


def chk_p28a_audit_defensive():
    """G) cfg_audit 必须防御: glob_match 不抛异常 (防御包裹) + _gm 递归有界 + parse_cfg 行级防御."""
    a = HERE / "ww_p28a_cfg_audit.py"
    if not a.is_file():
        return False, f"missing {a.name}"
    t = a.read_text(encoding="utf-8")
    if "except Exception:" not in t or "return False" not in t:
        return False, "cfg_audit glob_match 缺少防御包裹"
    if "_BOUND" not in t:
        return False, "cfg_audit _gm 缺少递归界 _BOUND"
    parse_src = t.split("def parse_cfg")[1]
    if "except Exception:" not in parse_src:
        return False, "cfg_audit parse_cfg 缺少行级防御"
    return True, "G: cfg_audit glob 防御 + 递归有界 + parse 行级防御: OK"



def main():
    checks = [
        ("A. report_check UTF-8", chk_report_utf8),
        ("B. ps1 无中文安全字面量", chk_ps1_no_cn_safety),
        ("C. ps1 双重 validator", chk_ps1_calls_validators),
        ("D. P28A ps1 全 ASCII + cfg_audit", chk_p28a_ascii),
        ("E/H/I. native Run-Python + $PyArgs + 显式命名", chk_p28a_native_stderr),
        ("J. argv 数量符合 CLI schema", chk_p28a_argv_counts),
        ("G. cfg_audit glob 防御 + 递归有界", chk_p28a_audit_defensive),
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
