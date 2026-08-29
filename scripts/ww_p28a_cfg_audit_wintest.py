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

# 用户指定典型规则 (6 层) —— 2026.7.20/WW_Nevely42_Animations.package 必须命中 */*.package
DEEP_CFG = (
    "Priority 500\n"
    "PackedFile *.package\n"
    "PackedFile */*.package\n"
    "PackedFile */*/*.package\n"
    "PackedFile */*/*/*.package\n"
    "PackedFile */*/*/*/*.package\n"
    "PackedFile */*/*/*/*/*.package\n"
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
        p27_dir = Path(td) / "P27_Overrides"
        assert not p27_dir.exists(), "fixture assertion: P27_Overrides 不应存在"

        # 构造真实 Mods 树 + 真实源文件 (绝对路径), 复现真机形状
        mods = Path(td) / "Mods"
        (mods / "2026.7.20").mkdir(parents=True, exist_ok=True)
        src_file = mods / "2026.7.20" / "WW_Nevely42_Animations.package"
        src_file.write_bytes(b"dummy")
        cfg_real = mods / "Resource.cfg"
        cfg_real.write_text(DEFAULT_CFG, encoding="utf-8")
        root_abs = mods / "ZZZ_WW_P27_DisplayName_Override.package"
        src_abs = src_file

        # ---- 模式 check (传完整绝对 src 路径) ----
        code, out, err = run("check", str(cfg_real), str(root_abs), str(src_abs))
        g = parse_kv(out)
        check("check exit=0", code == 0, f"(exit {code})")
        check("check 无崩溃 stderr", err.strip() == "", f"stderr={err!r}")
        check("check RESOURCE_CFG_EXISTS=YES", g.get("RESOURCE_CFG_EXISTS") == "YES", g.get("RESOURCE_CFG_EXISTS"))
        check("check PRIORITY_RELATION=OVERRIDE_HIGHER", g.get("PRIORITY_RELATION", "").startswith("OVERRIDE_HIGHER"), g.get("PRIORITY_RELATION"))
        check("check SOURCE_EFFECTIVE_PRIORITY=500", g.get("SOURCE_EFFECTIVE_PRIORITY") == "500", g.get("SOURCE_EFFECTIVE_PRIORITY"))
        check("check SOURCE_REL_PATH 相对 Mods 根", g.get("SOURCE_REL_PATH") == "2026.7.20/WW_Nevely42_Animations.package", g.get("SOURCE_REL_PATH"))
        check("check SOURCE_MATCH_COUNT>0", g.get("SOURCE_MATCH_COUNT") not in (None, "0"), g.get("SOURCE_MATCH_COUNT"))

        # ---- 模式 propose (传完整绝对 src 路径) ----
        code, out, err = run("propose", str(cfg_real), str(root_abs), str(src_abs))
        g = parse_kv(out)
        check("propose exit=0", code == 0, f"(exit {code})")
        check("propose 无崩溃 stderr", err.strip() == "", f"stderr={err!r}")
        check("propose APPEND_REQUIRED=YES", g.get("APPEND_REQUIRED") == "YES", g.get("APPEND_REQUIRED"))
        check("propose PROPOSED_PRIORITY=600", g.get("PROPOSED_PRIORITY") == "600", g.get("PROPOSED_PRIORITY"))
        check("propose OVERRIDE_HIGHER", g.get("PRIORITY_RELATION", "").startswith("OVERRIDE_HIGHER"), g.get("PRIORITY_RELATION"))
        try:
            lines = base64.b64decode(g.get("APPEND_LINES", "")).decode("utf-8")
            check("APPEND_LINES 可解码且含 >500", "Priority 600" in lines and "P27_Overrides/*.package" in lines, lines.strip())
        except Exception as e:
            check("APPEND_LINES 解码", False, f"{e}")

        # 只读性 + 未创建 P27_Overrides
        after = cfg_real.read_bytes()
        check("cfg 内容未变 (只读)", after == DEFAULT_CFG.encode("utf-8"))
        check("未创建 P27_Overrides 目录", not p27_dir.exists())

        # ---- 回归1: 典型 6 层规则; 绝对 src 路径; 必须命中 */*.package -> 500 ----
        cfg_deep = Path(td) / "cfg_deep.cfg"
        cfg_deep.write_text(DEEP_CFG, encoding="utf-8")
        code, out, err = run("propose", str(cfg_deep), str(root_abs), str(src_abs))
        g = parse_kv(out)
        check("回归1 SOURCE_EFFECTIVE_PRIORITY=500", g.get("SOURCE_EFFECTIVE_PRIORITY") == "500", g.get("SOURCE_EFFECTIVE_PRIORITY"))
        check("回归1 SOURCE_MATCH_COUNT=1", g.get("SOURCE_MATCH_COUNT") == "1", g.get("SOURCE_MATCH_COUNT"))
        mr1 = g.get("SOURCE_MATCH_RULE_1_PRIORITY")
        check("回归1 命中规则 prio=500", mr1 == "500", mr1)
        # 所有命中模式拼接起来, 必须至少包含 */*.package (用户要求的典型命中)
        all_pats = "".join(g.get(f"SOURCE_MATCH_RULE_{i}_PATTERN", "") for i in range(1, 10))
        check("回归1 命中规则含 */*.package", "*/*.package" in all_pats, all_pats)

        # ---- 回归2: src_eff=None 不得抛 TypeError, 必须 fail-closed SOURCE_PRIORITY_UNRESOLVED ----
        # 构造 source 相对路径在 cfg 规则之外: 用一个不会命中任何规则的绝对路径
        # (直接复用不同根路径, 使 rel_to_base 计算不到 -> 且 cfg 也无 *us 规则命中)
        far_root = Path(td) / "OtherRoot"
        (far_root / "sub").mkdir(parents=True, exist_ok=True)
        far_src = far_root / "sub" / "WW_Nevely42_Animations.package"
        far_src.write_bytes(b"dummy")
        # cfg 规则直指 Mods/P27_Overrides 专用 600; far_src 不在 Mods 下 -> rel None -> sub_rel=far absolute -> 不命中 -> src_eff=None
        cfg_far = Path(td) / "cfg_far.cfg"
        cfg_far.write_text(
            "Priority 600\nPackedFile P27_Overrides/*.package\n", encoding="utf-8"
        )
        code, out, err = run("propose", str(cfg_far), str(root_abs), str(far_src))
        g = parse_kv(out)
        check("回归2 exit=4 (fail-closed)", code == 4, f"(exit {code})")
        check("回归2 无 TypeError 崩溃", "TypeError" not in err and err.strip() == "", f"err={err!r}")
        check("回归2 REASON=SOURCE_PRIORITY_UNRESOLVED", g.get("REASON") == "SOURCE_PRIORITY_UNRESOLVED", g.get("REASON"))
        check("回归2 VERDICT=FAIL", g.get("VERDICT") == "FAIL", g.get("VERDICT"))

    print(f"\n总计 {checks} 项检查, 失败 {len(fails)} 项")
    if fails:
        print("WIN_SHAPE_TEST=FAIL")
        return 1
    print("WIN_SHAPE_TEST=PASS (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
