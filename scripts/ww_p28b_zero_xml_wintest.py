#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p28b_zero_xml_wintest.py —— P28B 零修改 XML canary 全链路测试 (Linux/沙箱可运行)

在临时目录构造一个真实的单 WW_ANIM_XML 源 fixture (真机 instance 0x43F3438A94EDEB2B),
跑通 P28B 完整链路:

  1. generator (ww_p28b_zero_xml_canary.py) -> 生成 override + report
  2. report_check (ww_p28b_zero_xml_report_check.py) 对 report -> PASS
  3. tgi_check (ww_p28b_zero_xml_tgi_check.py) 对 override+source -> PASS (字节一致)
  4. cfg_audit 以 deploy 的精确 argv (--p27-dir P28B_Overrides) 决策 -> OVERRIDE_HIGHER / 600
  5. 负向: 篡改 override -> tgi_check 必须报 XML_BYTE_IDENTICAL=NO
  6. 负向: report 含白盒 instance -> report_check 必须 FAIL (exit 3)

验证 ZERO_WRITE_TO_MODS (生成/验证不写 Mods), 与输出目录只读性检查。

用法: python scripts\\ww_p28b_zero_xml_wintest.py
退出码: 0=全 PASS, 1=任一断言失败
"""
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
WS = HERE.parent
PY = sys.executable

GEN = HERE / "ww_p28b_zero_xml_canary.py"
RCHK = HERE / "ww_p28b_zero_xml_report_check.py"
TCHK = HERE / "ww_p28b_zero_xml_tgi_check.py"
AUDIT = HERE / "ww_p28a_cfg_audit.py"

WW_ANIM_XML = 0x7DF2169C
GROUP = 0x00000000
INST = 0x43F3438A94EDEB2B


def make_fixture(mods_root: Path):
    """构造真实源 fixture: 单 WW_ANIM_XML, 压缩 body, 真机 instance."""
    (mods_root / "2026.7.20").mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(HERE))
    from ww_animation_canary_builder import build_package
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<WickedWhimsAnimationPackage xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:noNamespaceSchemaLocation="http://wickedwhimsmod.com/schemas/animationpackage.xsd" version="1111">\n'
        '  <U n="author" value="Nevely42"/>\n'
        '  <T n="animation_raw_display_name">Caught Cheating 1</T>\n'
        '  <T n="animation_category">Story</T>\n'
        '</WickedWhimsAnimationPackage>\n'
    ).encode("utf-8")
    comp = zlib.compress(xml)
    meta = {"comp_state": True, "comp_type": 0x5A42, "mem_size": len(xml),
            "offset_high_bit": 1, "size_high_bit": 1}
    pkg = mods_root / "2026.7.20" / "WW_Nevely42_Animations.package"
    build_package([(WW_ANIM_XML, GROUP, INST, comp, meta)], pkg, header_comp=0x80000000, major=2, minor=0)
    return pkg


def run(argv):
    r = subprocess.run([PY] + argv, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def main():
    fails = []
    checks = 0

    def check(name, cond, detail=""):
        nonlocal checks
        checks += 1
        print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
        if not cond:
            fails.append(name)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        mods = root / "Mods"
        src_pkg = make_fixture(mods)

        respkg = root / "respkg"
        canary_out = respkg / "ww_p28b"
        canary_pkg = canary_out / "WW_Nevely42_ZeroXML_Override.package"
        canary_report = canary_out / "ww_p28b_zero_xml_report.txt"

        mods_before = sorted(p for p in mods.rglob("*") if p.is_file())

        # ---- 1. generator ----
        code, out, err = run([str(GEN), "--source", str(src_pkg), "--out-dir", str(respkg), "--force"])
        g = {}
        for ln in out.splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                g[k] = v
        check("1. generator exit=0", code == 0, f"(exit {code})")
        check("1. 无 stderr", err.strip() == "", f"stderr={err!r}")
        check("1. VERDICT=PASS", g.get("VERDICT") == "PASS", g.get("VERDICT"))
        check("1. XML_BYTE_IDENTICAL=YES", g.get("XML_BYTE_IDENTICAL") == "YES", g.get("XML_BYTE_IDENTICAL"))
        check("1. WW_ANIM_XML_COUNT=1", g.get("WW_ANIM_XML_COUNT") == "1", g.get("WW_ANIM_XML_COUNT"))
        check("1. INSTANCE=真实", g.get("INSTANCE") == "0x43F3438A94EDEB2B", g.get("INSTANCE"))
        check("1. TGI_MATCH=YES", g.get("TGI_MATCH") == "YES", g.get("TGI_MATCH"))
        check("1. report 生成", canary_report.is_file())
        check("1. ZERO_WRITE_TO_MODS=YES", g.get("ZERO_WRITE_TO_MODS") == "YES", g.get("ZERO_WRITE_TO_MODS"))
        mods_after = sorted(p for p in mods.rglob("*") if p.is_file())
        check("1. 源 Mods 未被写 (ZERO_WRITE)", mods_before == mods_after)

        # ---- 2. report_check ----
        code, out, err = run([str(RCHK), str(canary_report)])
        check("2. report_check exit=0", code == 0, f"(exit {code})")
        check("2. report_check PASS", "REPORT_CHECK=PASS" in out, "yes" if "REPORT_CHECK=PASS" in out else out)

        # ---- 3. tgi_check (override vs source) ----
        code, out, err = run([str(TCHK), str(canary_pkg), str(src_pkg)])
        check("3. tgi_check exit=0", code == 0, f"(exit {code})")
        check("3. tgi_check XML_BYTE_IDENTICAL=YES", "XML_BYTE_IDENTICAL=YES" in out, "yes" if "XML_BYTE_IDENTICAL=YES" in out else out)
        check("3. tgi_check TGI_MATCH=YES", "TGI_MATCH=YES" in out)

        # ---- 4. cfg_audit 精确 deploy argv (--p27-dir P28B_Overrides) ----
        cfg = mods / "Resource.cfg"
        cfg.write_text("Priority 500\nPackedFile *.package\nPackedFile */*.package\nPackedFile */**/*.package\n", encoding="utf-8")
        canary_ref = mods / "P28B_Overrides" / "WW_Nevely42_ZeroXML_Override.package"  # 虚拟, 不存在
        code, out, err = run([str(AUDIT), "propose", str(cfg), str(canary_ref), str(src_pkg), "--p27-dir", "P28B_Overrides"])
        ga = {}
        for ln in out.splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                ga[k] = v
        check("4. audit deploy-argv exit=0", code == 0, f"(exit {code})")
        check("4. SOURCE_EFFECTIVE_PRIORITY=500", ga.get("SOURCE_EFFECTIVE_PRIORITY") == "500", ga.get("SOURCE_EFFECTIVE_PRIORITY"))
        check("4. OVERRIDE_HIGHER", str(ga.get("PRIORITY_RELATION", "")).startswith("OVERRIDE_HIGHER"), ga.get("PRIORITY_RELATION"))
        check("4. PROPOSED=600", ga.get("PROPOSED_PRIORITY") == "600", ga.get("PROPOSED_PRIORITY"))
        import base64
        lines = base64.b64decode(ga.get("APPEND_LINES", "")).decode("utf-8")
        check("4. APPEND 指向 P28B_Overrides", "P28B_Overrides/*.package" in lines, lines.strip().replace("\n", " | "))
        check("4. cfg 未变 (只读)", cfg.read_text(encoding="utf-8") == "Priority 500\nPackedFile *.package\nPackedFile */*.package\nPackedFile */**/*.package\n")

        # ---- 5. 负向: 篡改 override -> tgi_check FAIL ----
        sys.path.insert(0, str(HERE))
        from ww_animation_canary_builder import build_package, read_body_raw, safe_parse
        bpidx, _ = safe_parse(canary_pkg)
        be = bpidx.entries[0]
        body = read_body_raw(canary_pkg, be)
        bad = bytearray(body); bad[len(bad) // 2] ^= 0xFF; bad = bytes(bad)
        bad_out = root / "bad_override.package"
        bmeta = {"comp_state": True, "comp_type": 0x5A42, "mem_size": len(bad), "offset_high_bit": 1, "size_high_bit": 1}
        build_package([(WW_ANIM_XML, be.group_id, be.instance_id, bad, bmeta)], bad_out, header_comp=0x80000000, major=2, minor=0)
        code, out, err = run([str(TCHK), str(bad_out), str(src_pkg)])
        check("5. 篡改 override tgi_check exit=4", code == 4, f"(exit {code})")
        check("5. 篡改 XML_BYTE_IDENTICAL=NO", "XML_BYTE_IDENTICAL=NO" in out)

        # ---- 6. 负向: report 含白盒 -> report_check FAIL ----
        wb_report = root / "report_wb.txt"
        wb_report.write_text(canary_report.read_text(encoding="utf-8").replace("0x43F3438A94EDEB2B", "0x4444444400000002"), encoding="utf-8")
        code, out, err = run([str(RCHK), str(wb_report)])
        check("6. 白盒 report exit=3", code == 3, f"(exit {code})")
        check("6. 白盒 拒绝", "WHITEBOX_INSTANCE" in out)

    print(f"\n总计 {checks} 项检查, 失败 {len(fails)} 项")
    if fails:
        print("P28B_WINTEST=FAIL")
        return 1
    print("P28B_WINTEST=PASS (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
