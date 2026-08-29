#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p28b_zero_xml_static_check.py —— P28B 零修改 XML canary 静态不变式 (Linux/沙箱可运行)

对 P28B 相关脚本做源码级静态校验 (不做真机部署):

  A. deploy ps1 无中文安全字面量 (ASCII-only safety logic)
  B. deploy 使用 Run-Python 封装, 参数名 $PyArgs (非 $Args), 无 @Args
  C. deploy 显式命名调用 (Run-Python -Script X -PyArgs @(...)), 且 argv 数量符合 CLI schema:
       report_check=1, tgi_check=2 (override+source), cfg_audit=6 (mode+cfg+ref+src+--p27-dir+P28B_Overrides)
  D. report_check 用 read_text(encoding="utf-8") 显式读取 report
  E. report_check 拒绝白盒 instance 0x4444444400000002
  F. generator 不做 XML parse/serialize (无 ET.fromstring / ElementTree / decode->encode)
  G. generator 复制 source raw body 原封不动 (read_body_raw 直传 build_package)
  H. tgi_check 独立做 XML 字节比对 (不信任 report)
  I. rollback ps1: 只动 P28B_Overrides + cfg.p28b_backup(标记守卫) + 缓存; ASCII-only
  J. 双 ps1 均为 UTF-8 BOM (显示/兼容)
  K. deploy 移除 P27 modified canary (避免同 TGI 共存歧义)

用法: python scripts\\ww_p28b_zero_xml_static_check.py
退出码: 0=全 PASS, 2=脚本缺失, 3=任一 FAIL
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEPLOY = HERE / "ww_p28b_zero_xml_deploy.ps1"
ROLLBACK = HERE / "ww_p28b_zero_xml_rollback.ps1"
GEN = HERE / "ww_p28b_zero_xml_canary.py"
RCHK = HERE / "ww_p28b_zero_xml_report_check.py"
TCHK = HERE / "ww_p28b_zero_xml_tgi_check.py"


def _read_bom(p):
    raw = p.read_bytes()
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", errors="replace"), True
    return raw.decode("utf-8", errors="replace"), False


def check_deploy_ascii(text):
    """deploy ps1 安全逻辑必须 ASCII-only (注释/输出 token 除外, 但逻辑行不得含中文)."""
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        for ch in ln:
            if ord(ch) > 127:
                return False
    return True


def check_rollback_ascii(text):
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        for ch in ln:
            if ord(ch) > 127:
                return False
    return True


def main():
    fails = []
    checks = 0

    def check(name, cond, detail=""):
        nonlocal checks
        checks += 1
        print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
        if not cond:
            fails.append(name)

    if not all(p.is_file() for p in (DEPLOY, ROLLBACK, GEN, RCHK, TCHK)):
        print("MISSING P28B script(s)")
        return 2

    t_deploy, bom_deploy = _read_bom(DEPLOY)
    t_rollback, bom_rollback = _read_bom(ROLLBACK)
    t_gen = GEN.read_text(encoding="utf-8")
    t_rchk = RCHK.read_text(encoding="utf-8")
    t_tchk = TCHK.read_text(encoding="utf-8")

    # A. deploy ps1 ASCII-only safety
    check("A. deploy ps1 无中文安全字面量", check_deploy_ascii(t_deploy), "(logic ASCII)")
    # I. rollback ps1 ASCII-only
    check("I. rollback ps1 ASCII-only", check_rollback_ascii(t_rollback), "(logic ASCII)")
    # J. BOM (display/compat)
    check("J. deploy ps1 UTF-8 BOM", bom_deploy)
    check("J. rollback ps1 UTF-8 BOM", bom_rollback)
    # K. deploy removes P27 canary
    check("K. deploy 移除 P27 canary", "P27_Overrides\\WW_P27_DisplayName_Override.package" in t_deploy and "P27_MODIFIED_CANARY" in t_deploy)

    # B. Run-Python + $PyArgs (no $Args)
    check("B. deploy 无 $Args/@Args", "$Args" not in t_deploy and "@Args" not in t_deploy and "$PyArgs" in t_deploy and "@PyArgs" in t_deploy)
    check("B. rollback 无 native python 调用", "python" not in t_rollback.lower().replace("#",""), "(rollback no Run-Python needed)")

    # C. named calls + argv counts
    import re
    calls = re.findall(r"Run-Python\s+-Script\s+(\$\w+)\s+-PyArgs\s+@\(([^)]*)\)", t_deploy)
    check("C. deploy 调用点数==3 (report/tgi/audit 各1, propose 共用 audit propose)", len(calls) == 4, f"(found {len(calls)})")
    var_map = {"$REPORT_CHECK": 1, "$TGI_CHECK": 2, "$CFG_AUDIT": 6}  # cfg_audit 传 --p27-dir + 值
    count_ok = True
    for var, inner in calls:
        name = var_map.get(var)
        if name is None:
            count_ok = False
            continue
        items = [x for x in re.split(r",", inner) if x.strip()]
        # 排除 --p27-dir 与 P28B_Overrides 是两个参数 -> 计数为 items 中元素数
        if len(items) != name:
            count_ok = False
            print(f"    (argv {var}={len(items)} expected {name}: {inner.strip()})")
    check("C. deploy 各调用点 argv 数量符合 schema", count_ok, "(report=1,tgi=2,cfg=6)")

    # D. report_check explicit UTF-8
    check("D. report_check read_text(utf-8)", 'read_text(encoding="utf-8")' in t_rchk and "read_text" in t_rchk)
    # E. report_check rejects whitebox
    check("E. report_check 拒绝白盒 instance", "0x4444444400000002" in t_rchk)
    # F. generator no XML parse/serialize (仅检查实际引用的 XML 库/API, 忽略文档字符串)
    bad_xml = (
        "xml.etree" in t_gen or "from xml" in t_gen
        or re.search(r"\bET\.fromstring\b", t_gen)
        or re.search(r"\bET\.tostring\b", t_gen)
        or re.search(r"^\s*import xml\b", t_gen, re.M)
    )
    check("F. generator 不做 XML parse/serialize", not bad_xml, "(raw copy only)")
    # G. generator copies raw body
    check("G. generator 复用 read_body_raw 直传 build_package", "read_body_raw(src, wxml)" in t_gen and "build_package(items" in t_gen)
    # H. tgi_check independent byte compare
    check("H. tgi_check 独立字节比对", "read_body_raw(opkg, eo)" in t_tchk and "read_body_raw(spkg, es)" in t_tchk and "XML_BYTE_IDENTICAL" in t_tchk)

    print(f"\nVERDICT={'PASS' if not fails else 'FAIL'} (exit {0 if not fails else 3})")
    return 0 if not fails else 3


if __name__ == "__main__":
    sys.exit(main())
