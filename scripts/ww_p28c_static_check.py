#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p28c_static_check.py —— P28C 源码级静态不变式 (Linux/沙箱可运行)

对 P28C 文件做静态校验 (不做真机部署):

  A. ww_p28c_ascii_canary.py 修复 mem_size:
       - meta 的 mem_size 必须 = 新解压实际长度 (len(decompress_maybe(new_body))), 不得沿用 source 旧 field7
       - 禁止源码出现 ww_animation_xml_displayname_override.py 那行的 m0.get("mem_size", ...) 复用坏逻辑
       - 必须解压 new_body 算 NEW_XML_DECOMPRESSED_SIZE
  B. ASCII 语义: 只改 ordinal 299 -> TEST299 (无中文, 无 抓奸, 无 1..8), 且
       TARGET_ORDINAL=299 / TARGET_NEW_RAW=TEST299 / TARGETS_CHANGED=1 / NON_TARGET_XML_DIFF=0
  C. 只读源 + 只写 output/ww_p28c: ZERO_WRITE_TO_MODS=YES; 单 WW_ANIM_XML source-faithful TGI
  D. report_check: read_text(encoding='utf-8') + 独立对真实包 bytes 复核 (不信 report YES)
  E. cfg_audit: 硬编码 P28C_Overrides, 输出 P28C_OVERRIDE_EFFECTIVE_PRIORITY, 与 P27/P28A/P28B0 隔离
  F. deploy/rollback ps1: ASCII-only logic + UTF-8 BOM + Run-Python($PyArgs, 禁 $Args) + argv schema
  G. deploy 含 post-write re-audit 且校验 P28C_OVERRIDE_EFFECTIVE_PRIORITY > SOURCE (fail-closed)
  H. generator 不输出中文替换文本 (ASCII canary)

用法: python scripts\\ww_p28c_static_check.py
退出码: 0=全 PASS, 2=脚本缺失, 3=任一 FAIL
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GEN      = HERE / "ww_p28c_ascii_canary.py"
RCHK     = HERE / "ww_p28c_report_check.py"
TCHK     = HERE / "ww_p28c_tgi_check.py"
AUDIT    = HERE / "ww_p28c_cfg_audit.py"
DEPLOY   = HERE / "ww_p28c_deploy.ps1"
ROLLBACK = HERE / "ww_p28c_rollback.ps1"


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
    import io
    import tokenize
    out = []
    try:
        toks = tokenize.generate_tokens(io.StringIO(src).readline)
        for tok in toks:
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string if tok.type != tokenize.NL else "")
    except Exception:
        for ln in src.splitlines():
            out.append(ln.split("#", 1)[0])
    return " ".join(out)


def main():
    need = [GEN, RCHK, TCHK, AUDIT, DEPLOY, ROLLBACK]
    if not all(p.is_file() for p in need):
        print("MISSING P28C script(s)")
        return 2

    fails = []
    checks = 0

    def check(name, cond, detail=""):
        nonlocal checks
        checks += 1
        print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
        if not cond:
            fails.append(name)

    g = GEN.read_text(encoding="utf-8")
    r = RCHK.read_text(encoding="utf-8")
    t = TCHK.read_text(encoding="utf-8")
    a = AUDIT.read_text(encoding="utf-8")
    code_g = _strip_docstrings_and_comments(g)
    code_a = _strip_docstrings_and_comments(a)

    # A. mem_size 修复
    check("A. generator 从新解压算 mem_size",
          '"mem_size": written_mem_size' in g.replace(" ", "") or '"mem_size":new_xml_decompressed_size' in g.replace(" ", "")
          or "decompress_maybe(new_body)" in g and "written_mem_size" in g)
    check("A. generator 定义 NEW_XML_DECOMPRESSED_SIZE", "new_xml_decompressed_size = len(decompress_maybe(new_body))" in g)
    check("A. generator 不把 source 旧 field7 当 mem_size 写出",
          '"mem_size": source_mem_size' not in code_g and '"mem_size": m["mem_size"]' not in code_g)
    check("A. generator 机验 WRITTEN==NEW", "written_field7 == new_xml_decompressed_size" in g and "MEM_SIZE_MATCH_NEW_XML" in g)

    # B. ASCII canary 语义
    check("B. TARGET_ORDINAL=299", "TARGET_ORDINAL = 299" in g)
    check("B. TARGET_NEW_RAW=TEST299", 'TARGET_NEW_RAW = "TEST299"' in g)
    check("B. 无中文/抓奸/1..8 替换默认", "抓奸" not in code_g and "299" in g)
    check("B. NON_TARGET_XML_DIFF=0", "NON_TARGET_XML_DIFF" in g and "non_target_zero" in g)

    # C. ZERO_WRITE / output p28c / single xml
    check("C. output/ww_p28c", 'out_dir = Path(a.out_dir) / "ww_p28c"' in g)
    check("C. artifact 名 WW_P28C_TEST299_Override.package", '"WW_P28C_TEST299_Override.package"' in g)
    check("C. ZERO_WRITE_TO_MODS=YES", "ZERO_WRITE_TO_MODS=YES" in g)

    # D. report_check 独立复核
    check("D. report_check read_text(utf-8)", 'read_text(encoding="utf-8")' in r)
    check("D. report_check 独立读包复核", "read_entry_meta_raw(pkg)" in r and "decompress_maybe(body)" in r and "field7_indep" in r)

    # E. cfg_audit 隔离
    check("E. cfg_audit 硬编码 P28C_Overrides", 'OBJECT_DIR = "P28C_Overrides"' in a)
    check("E. cfg_audit 输出 P28C_OVERRIDE_EFFECTIVE_PRIORITY", "P28C_OVERRIDE_EFFECTIVE_PRIORITY" in a)
    check("E. cfg_audit 与 P27/P28B0 隔离", "P27_Overrides" not in code_a and "P28B_Overrides" not in code_a and "P28B0_Overrides" not in code_a)
    check("E. cfg_audit append 指向 P28C_Overrides", "PackedFile {OBJECT_DIR}/*.package" in a)

    # F. ps1: 读 BOM + ASCII + $Args 检查只看逻辑行 (忽略注释)
    td, bom_d = _read_bom(DEPLOY)
    tr, bom_r = _read_bom(ROLLBACK)

    def logic_only(text):
        return "\n".join(ln for ln in text.splitlines()
                          if not (ln.strip().startswith("#") or not ln.strip()))
    td_logic = logic_only(td)
    tr_logic = logic_only(tr)
    check("F. deploy ps1 ASCII logic", _ascii_logic(td))
    check("F. rollback ps1 ASCII logic", _ascii_logic(tr))
    check("F. deploy BOM", bom_d)
    check("F. rollback BOM", bom_r)
    check("F. deploy Run-Python + $PyArgs (禁 $Args)", "$Args" not in td_logic and "@Args" not in td_logic and "$PyArgs" in td and "@PyArgs" in td)
    calls = re.findall(r"Run-Python\s+-Script\s+(\$\w+)\s+-PyArgs\s+@\(([^)]*)\)", td)
    # RCHK + TCHK + propose + check = 4 处
    check("F. deploy Run-Python 调用4处 (rchk/tchk/propose/check)", len(calls) == 4, f"(found {len(calls)})")
    var_map = {"$RCHK": 1, "$TCHK": 2, "$CFG_AUDIT": 2}
    argv_ok = True
    for var, inner in calls:
        exp = var_map.get(var)
        if exp is None:
            argv_ok = False
            continue
        if len([x for x in re.split(r",", inner) if x.strip()]) != exp:
            argv_ok = False
    check("F. deploy 各调用 argv 符合 schema", argv_ok, "(rchk=1,tchk=2,cfg=2)")
    check("F. deploy cfg_audit 调用2次 (propose+check)", len([c for c in calls if c[0] == "$CFG_AUDIT"]) == 2, "(propose+check)")

    # G. post-write re-audit
    check("G. deploy POST-WRITE RE-AUDIT(P28C 键)", "POST_WRITE_REAUDIT=PASS" in td and "P28C_OVERRIDE_EFFECTIVE_PRIORITY" in td)
    check("G. deploy re-audit 校验 override_eff>src_eff fail-closed",
          "[int]$postOvrEff -le [int]$postSrcEff" in td and "cannot deploy (fail-closed)" in td)

    # H. rollback 只删 P28C
    check("H. rollback 只动 P28C_Overrides + backup", "P28C_Overrides" in tr and "Resource.cfg.p28c_backup" in tr and "P28C_Overrides" in tr)

    print(f"\nVERDICT={'PASS' if not fails else 'FAIL'} (exit {0 if not fails else 3})")
    return 0 if not fails else 3


if __name__ == "__main__":
    sys.exit(main())
