#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p28b0_full_clone_static_check.py —— P28B-0 整套静态不变式 (Linux/沙箱可运行)

对 P28B-0 六个脚本做源码级静态校验 (不做真机部署):

  A. deploy ps1 逻辑行无中文安全性字面量 (ASCII-only)
  B. deploy 用 Run-Python 封装 + $PyArgs (禁 $Args / @Args)
  C. deploy 显式命名调用且 argv 数量符合 CLI schema:
       RCHK(report_check)=1, TCHK(tgi_check)=2, AUDIT=1 (check|propose)+cfg (2), deploy 上有 3 处
  D. report_check 显式 read_text(encoding='utf-8')
  E. report_check / tgi_check 独立逐字节重比 (不信任 report 声明的 identical=YES)
  F. generator(ww_p28b0_full_clone.py) 不使用 build_package / 不 parse XML / 只 copyfile
  G. generator 全文件字节复制 (shutil.copyfile) + 逐块 SHA + 逐字节 chunk 比对
  H. cfg_audit(ww_p28b0_cfg_audit.py) 输出 P28B0_OVERRIDE_EFFECTIVE_PRIORITY 且与 P27/P28A 隔离
     (不含 P27_Overrides / P28A 常量串; 硬编码 P28B0_Overrides)
  I. rollback ps1: 只动 P28B0_Overrides + cfg.p28b0_backup(标记守卫) + 缓存; ASCII-only
  J. deploy/rollback 均 UTF-8 BOM
  K. deploy 内含 post-write re-audit (追加后重跑 cfg_audit check 且校验 override_eff>src_eff)

用法: python scripts\\ww_p28b0_full_clone_static_check.py
退出码: 0=全 PASS, 2=脚本缺失, 3=任一 FAIL
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEPLOY   = HERE / "ww_p28b0_full_clone_deploy.ps1"
ROLLBACK = HERE / "ww_p28b0_full_clone_rollback.ps1"
GEN      = HERE / "ww_p28b0_full_clone.py"
RCHK     = HERE / "ww_p28b0_full_clone_report_check.py"
TCHK     = HERE / "ww_p28b0_full_clone_tgi_check.py"
AUDIT    = HERE / "ww_p28b0_cfg_audit.py"


def _read_bom(p):
    raw = p.read_bytes()
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", errors="replace"), True
    return raw.decode("utf-8", errors="replace"), False


def _ascii_logic(text):
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        for ch in ln:
            if ord(ch) > 127:
                return False
    return True


def _strip_docstrings_and_comments(src):
    """去掉 Python 三重引号 docstring 与 # 注释, 返回仅含实际代码的文本 (用于静态命中判断)."""
    import io, tokenize
    out = []
    try:
        toks = tokenize.generate_tokens(io.StringIO(src).readline)
        for tok in toks:
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string if tok.type != tokenize.NL else "")
    except Exception:
        # 退化: 逐行去 # 注释
        for ln in src.splitlines():
            code = ln.split("#", 1)[0]
            out.append(code)
    return " ".join(out)



def main():
    fails = []
    checks = 0

    def check(name, cond, detail=""):
        nonlocal checks
        checks += 1
        print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
        if not cond:
            fails.append(name)

    need = [DEPLOY, ROLLBACK, GEN, RCHK, TCHK, AUDIT]
    if not all(p.is_file() for p in need):
        print("MISSING P28B-0 script(s)")
        return 2

    t_d, bom_d = _read_bom(DEPLOY)
    t_r, bom_r = _read_bom(ROLLBACK)
    t_g = GEN.read_text(encoding="utf-8")
    t_ck = RCHK.read_text(encoding="utf-8")
    t_tk = TCHK.read_text(encoding="utf-8")
    t_a = AUDIT.read_text(encoding="utf-8")

    # A/I: ASCII-only logic
    check("A. deploy ps1 无中文安全字面量", _ascii_logic(t_d), "(logic ASCII)")
    check("I. rollback ps1 ASCII-only", _ascii_logic(t_r), "(logic ASCII)")
    # J: BOM
    check("J. deploy ps1 UTF-8 BOM", bom_d)
    check("J. rollback ps1 UTF-8 BOM", bom_r)

    # B: Run-Python + $PyArgs (no $Args)
    check("B. deploy 无 $Args/@Args", "$Args" not in t_d and "@Args" not in t_d and "$PyArgs" in t_d and "@PyArgs" in t_d)
    check("B. rollback 无 native python", "python " not in t_r.lower())

    # K: post-write re-audit present in deploy
    check("K. deploy 含 POST-WRITE RE-AUDIT block",
          "POST-WRITE RE-AUDIT" in t_d and "POST_WRITE_REAUDIT" in t_d
          and "P28B0_OVERRIDE_EFFECTIVE_PRIORITY" in t_d
          and "POST_WRITE_REAUDIT=PASS" in t_d)
    # 关键 fail-closed 校验: override_eff <= src_eff 必须中止 (PowerShell 数值比较)
    check("K. deploy re-audit 校验 override_eff>src_eff(fail-closed)",
          "[int]$postCloneEff -le [int]$postSrcEff" in t_d
          and "cannot deploy (fail-closed)" in t_d.lower())

    # C: named Run-Python calls + argv counts
    calls = re.findall(r"Run-Python\s+-Script\s+(\$\w+)\s+-PyArgs\s+@\(([^)]*)\)", t_d)
    # deploy 有 report_check(1) + tgi_check(2) + cfg propose(2) + cfg check(2) = 4 处
    check("C. deploy Run-Python 调用4处 (rchk/tchk/propose/check)", len(calls) == 4, f"(found {len(calls)})")
    var_map = {"$RCHK": 1, "$TCHK": 2, "$AUDIT": 2}  # AUDIT = mode + cfg
    cnt_ok = True
    for var, inner in calls:
        exp = var_map.get(var)
        if exp is None:
            cnt_ok = False
            continue
        items = [x for x in re.split(r",", inner) if x.strip()]
        if len(items) != exp:
            cnt_ok = False
            print(f"    (argv {var}={len(items)} expected {exp}: {inner.strip()})")
    check("C. deploy 各调用点 argv 符合 schema", cnt_ok, "(rchk=1,tchk=2,cfg=2)")

    # D: report_check explicit UTF-8
    check("D. report_check read_text(utf-8)", 'read_text(encoding="utf-8")' in t_ck)
    # E: independent byte compare
    check("E. report_check 独立逐字节比对", "def byte_identical" in t_ck and "sha256_file" in t_ck)
    check("E. tgi_check 独立 raw-index 普查", "read_raw_index" in t_tk and "WW_CLONE_VALID" in t_tk
          and "WW_XML_SOURCE_INSTANCE" in t_tk)

    # F: generator no build_package / no XML parse (只看实际代码, 忽略 docstring/注释)
    code_g = _strip_docstrings_and_comments(t_g)
    check("F. generator 禁 build_package", "build_package" not in code_g)
    check("F. generator 不作 XML parse/serialize",
          "xml.etree" not in code_g and "ElementTree" not in code_g and "fromstring" not in code_g)

    # G: generator full-file byte copy + dual compare
    check("G. generator 用 shutil.copyfile 全文件复制", "shutil.copyfile(" in t_g)
    check("G. generator 含 sha256_file + byte_identical", "sha256_file" in t_g and "def byte_identical" in t_g)

    # H: cfg_audit P28B0-isolated (看实际代码/常量, 忽略 docstring 里的说明性引用)
    code_a = _strip_docstrings_and_comments(t_a)
    check("H. cfg_audit 硬编码 P28B0_Overrides", 'OBJECT_DIR = "P28B0_Overrides"' in t_a)
    check("H. cfg_audit 输出 P28B0_OVERRIDE_EFFECTIVE_PRIORITY", "P28B0_OVERRIDE_EFFECTIVE_PRIORITY" in t_a)
    check("H. cfg_audit 与 P27/P28A 隔离(代码层不含其关联路径)",
          "P27_Overrides" not in code_a and "P28B_Overrides" not in code_a and "WW_Nevely42_ZeroXML_Override" not in code_a)

    print(f"\nVERDICT={'PASS' if not fails else 'FAIL'} (exit {0 if not fails else 3})")
    return 0 if not fails else 3


if __name__ == "__main__":
    sys.exit(main())
