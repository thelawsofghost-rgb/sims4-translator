#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p28a_cfg_audit_wintest.py —— Windows 路径形状的只读审计测试 (Linux/沙箱可运行)

覆盖用户指定的真实 Windows 路径 (含空格 + 反斜杠 + 目标 P27_Overrides 尚不存在):
  C:\\Users\\thela\\Documents\\Electronic Arts\\The Sims 4\\Mods\\Resource.cfg
  C:\\Users\\thela\\Documents\\Electronic Arts\\The Sims 4\\Mods\\2026.7.20\\WW_Nevely42_Animations.package
  C:\\Users\\thela\\Documents\\Electronic Arts\\The Sims 4\\Mods\\ZZZ_WW_P27_DisplayName_Override.package
  C:\\Users\\thela\\Documents\\Electronic Arts\\The Sims 4\\Mods\\P27_Overrides\\WW_P27_DisplayName_Override.package

验证:
  * 在 P27_Overrides 目录/文件不存在时, check 与 propose 均不崩溃、不报 CFG_MISSING 误判
  * cfg 仅从真实临时文件读取; 审计不创建/修改任何文件 (只读)
  * 决策正确: 默认 EA cfg (Priority 500) -> PROPOSED=600, APPEND_REQUIRED=YES, OVERRIDE_HIGHER

用法: python scripts\\ww_p28a_cfg_audit_wintest.py
退出码: 0=全过, 1=任一断言失败
"""
import base64
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUDIT = HERE / "ww_p28a_cfg_audit.py"
PY = sys.executable

WIN_MODS = r"C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods"
WIN_SRC = WIN_MODS + r"\2026.7.20\WW_Nevely42_Animations.package"
WIN_ROOT = WIN_MODS + r"\ZZZ_WW_P27_DisplayName_Override.package"
WIN_CFG = WIN_MODS + r"\Resource.cfg"
WIN_P27 = WIN_MODS + r"\P27_Overrides\WW_P27_DisplayName_Override.package"

DEFAULT_CFG = (
    "Priority 500\n"
    "PackedFile *.package\n"
    "PackedFile */*.package\n"
    "PackedFile */**/*.package\n"
)


def run(mode, cfg_path, root=WIN_ROOT, src=WIN_SRC):
    r = subprocess.run(
        [PY, str(AUDIT), mode, cfg_path, root, src],
        capture_output=True, text=True,
    )
    return r.returncode, r.stdout, r.stderr


def parse_kv(stdout):
    g = {}
    for line in stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            g[k] = v
    return g


def main():
    fails = []
    checks = 0

    def check(name, cond, detail=""):
        nonlocal checks
        checks += 1
        tag = "PASS" if cond else "FAIL<<<"
        print(f"[{tag}] {name} {detail}")
        if not cond:
            fails.append(name)

    with tempfile.TemporaryDirectory() as td:
        # 真实临时 cfg 文件内容 = 默认 EA cfg; 路径字符串用 Windows 形状
        cfg_real = Path(td) / "Resource.cfg"
        cfg_real.write_text(DEFAULT_CFG, encoding="utf-8")
        # 注意: 用真实 cfg 文件路径 (否则本机不存在), 但用 Windows 形状的 root/src 参数
        # 证明: 目标 P27_Overrides 目录不存在也不影响审计
        p27_dir = Path(td) / "P27_Overrides"
        assert not p27_dir.exists(), "fixture assumption: P27_Overrides 不应存在"

        # 模式 check
        code, out, err = run("check", str(cfg_real))
        g = parse_kv(out)
        check("check exit=0", code == 0, f"(exit {code})")
        check("check 无崩溃 stderr", err.strip() == "", f"stderr={err!r}")
        check("check RESOURCE_CFG_EXISTS=YES", g.get("RESOURCE_CFG_EXISTS") == "YES", g.get("RESOURCE_CFG_EXISTS"))
        check("check PRIORITY_RELATION=OVERRIDE_HIGHER", g.get("PRIORITY_RELATION", "").startswith("OVERRIDE_HIGHER"), g.get("PRIORITY_RELATION"))
        check("check SOURCE_EFFECTIVE_PRIORITY=500", g.get("SOURCE_EFFECTIVE_PRIORITY") == "500", g.get("SOURCE_EFFECTIVE_PRIORITY"))

        # 模式 propose
        code, out, err = run("propose", str(cfg_real))
        g = parse_kv(out)
        check("propose exit=0", code == 0, f"(exit {code})")
        check("propose 无崩溃 stderr", err.strip() == "", f"stderr={err!r}")
        check("propose APPEND_REQUIRED=YES", g.get("APPEND_REQUIRED") == "YES", g.get("APPEND_REQUIRED"))
        check("propose PROPOSED_PRIORITY=600", g.get("PROPOSED_PRIORITY") == "600", g.get("PROPOSED_PRIORITY"))
        check("propose OVERRIDE_HIGHER", g.get("PRIORITY_RELATION", "").startswith("OVERRIDE_HIGHER"), g.get("PRIORITY_RELATION"))
        # APPEND_LINES 可解码为预期 cfg 行
        try:
            lines = base64.b64decode(g.get("APPEND_LINES", "")).decode("utf-8")
            check("APPEND_LINES 可解码且含 >500", "Priority 600" in lines and "P27_Overrides/*.package" in lines, lines.strip())
        except Exception as e:
            check("APPEND_LINES 解码", False, f"{e}")

        # 只读性: 审计运行前后 cfg 文件内容/SHA 不变, 且未创建 P27_Overrides
        after = cfg_real.read_bytes()
        check("cfg 内容未变 (只读)", after == DEFAULT_CFG.encode("utf-8"))
        check("未创建 P27_Overrides 目录", not p27_dir.exists())

    print(f"\n总计 {checks} 项检查, 失败 {len(fails)} 项")
    if fails:
        print("WIN_SHAPE_TEST=FAIL")
        return 1
    print("WIN_SHAPE_TEST=PASS (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
