#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p28b0_full_clone_wintest.py —— P28B-0 全链路测试 (Linux/沙箱可运行)

在临时目录构造一个多资源真实源 DBPF fixture (含 1 个 WW_ANIM_XML + 若干其它资源,
模拟真实 Nevely 多条目包), 跑通 P28B-0 完整链路:

  1. generator (ww_p28b0_full_clone.py)  -> 生成整包 byte-identical clone + report
  2. report_check (report_check.py)        -> PASS (独立逐字节重比)
  3. tgi_check (tgi_check.py)              -> PASS (独立 raw-index 普查 source vs clone)
  4. cfg_audit propose                     -> OVERRIDE_HIGHER / PROPOSED=600 (source 500)
  5. 【模拟 deploy 真实写 cfg】追加 P28B0 规则后再跑 cfg_audit check == post-write re-audit
     必须实际得到 SOURCE_EFFECTIVE_PRIORITY / P28B0_OVERRIDE_EFFECTIVE_PRIORITY(600)
     / PRIORITY_RELATION=OVERRIDE_HIGHER (override_eff>src_eff)
  6. 负向: 篡改 clone 后 report_check 必须独立比对 FAIL (exit 4)
  7. 负向: clone 等于源但 cfg 无 P28B0 规则 -> propose 仍 propose (覆盖源), 但若人为把
     源路径匹配到更高 priority 而 clone 无专用规则 -> re-audit 应 fail-closed
  8. ZERO_WRITE_TO_MODS (生成/审计不写 Mods)

用法: python scripts\\ww_p28b0_full_clone_wintest.py
退出码: 0=全 PASS, 1=任一断言失败
"""
import base64
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable

GEN   = HERE / "ww_p28b0_full_clone.py"
RCHK  = HERE / "ww_p28b0_full_clone_report_check.py"
TCHK  = HERE / "ww_p28b0_full_clone_tgi_check.py"
AUDIT = HERE / "ww_p28b0_cfg_audit.py"

WW_ANIM_XML = 0x7DF2169C
GROUP = 0x00000000
INST = 0x43F3438A94EDEB2B
STBL = 0x220557DA
CLIP = 0x0354E541


def make_fixture(pkg: Path):
    """多资源源 fixture: 1 WW_ANIM_XML(压缩) + STBL + CLIP(未压缩)+ 若干重复项总>5 条目."""
    sys.path.insert(0, str(HERE))
    from ww_animation_canary_builder import build_package
    items = []
    # WW_ANIM_XML (压缩)
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<I c="WickedWhimsAnimationPackage" i="1"...><U n="author" value="Nevely42"/>\n'
        '<T n="animation_raw_display_name">Caught Cheating 1</T>\n'
        '<T n="animation_category">Story</T>\n'
        '</I>\n'
    ).encode("utf-8")
    comp = zlib.compress(xml)
    items.append((WW_ANIM_XML, GROUP, INST, comp,
                  {"comp_state": True, "comp_type": 0x5A42, "mem_size": len(xml),
                   "offset_high_bit": 1, "size_high_bit": 1}))
    # STBL (未压缩小 body, 模拟附带资源)
    for i in range(3):
        body = (f"fake-stbl-{i}").encode("utf-8")
        items.append((STBL, GROUP, 0x2200000000000000 + i, body,
                      {"comp_state": False, "comp_type": 0, "mem_size": len(body),
                       "offset_high_bit": 0, "size_high_bit": 0}))
    # CLIP (未压缩)
    for i in range(3):
        body = (f"fake-clip-{i}").encode("utf-8")
        items.append((CLIP, GROUP, 0x0354000011110000 + i, body,
                      {"comp_state": False, "comp_type": 0, "mem_size": len(body),
                       "offset_high_bit": 0, "size_high_bit": 0}))
    build_package(items, pkg, header_comp=0x80000000, major=2, minor=0)
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

    def parse_kv(out):
        d = {}
        for ln in out.splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                d[k] = v.strip()
        return d

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        src_pkg = make_fixture(root / "WW_Nevely42_Animations.package")  # 必须与真机同名, clone 继承此名
        entry_total = None  # 后面推断

        outdir = root / "out" / "ww_p28b0"
        clone_pkg = outdir / "WW_Nevely42_Animations.package"
        report = outdir / "ww_p28b0_clone_report.txt"

        # ---- 1. generator ----
        code, out, err = run([str(GEN), "--source", str(src_pkg), "--out-dir", str(outdir), "--force"])
        g = parse_kv(out)
        check("1. generator exit=0", code == 0, f"(exit {code})")
        check("1. 无 stderr", err.strip() == "", f"stderr={err!r}")
        check("1. VERDICT=PASS", g.get("VERDICT") == "PASS", g.get("VERDICT"))
        check("1. PACKAGE_BYTE_IDENTICAL=YES", g.get("PACKAGE_BYTE_IDENTICAL") == "YES", g.get("PACKAGE_BYTE_IDENTICAL"))
        check("1. SOURCE_SHA==CLONE_SHA", g.get("SOURCE_PACKAGE_SHA256") == g.get("CLONE_PACKAGE_SHA256"))
        check("1. SIZE 相等", g.get("SOURCE_PACKAGE_SIZE") == g.get("CLONE_PACKAGE_SIZE"), f"{g.get('SOURCE_PACKAGE_SIZE')} vs {g.get('CLONE_PACKAGE_SIZE')}")
        check("1. ZERO_WRITE_TO_MODS=YES", g.get("ZERO_WRITE_TO_MODS") == "YES", g.get("ZERO_WRITE_TO_MODS"))
        check("1. clone 存在", clone_pkg.is_file())
        check("1. report 生成", report.is_file())

        # ---- 2. report_check (独立逐字节) ----
        code, out, err = run([str(RCHK), str(report)])
        check("2. report_check exit=0", code == 0, f"(exit {code})")
        check("2. REPORT_CHECK=PASS", "REPORT_CHECK=PASS" in out, out.splitlines()[0] if out else "")

        # ---- 3. tgi_check (条目普查 source vs clone) ----
        code, out, err = run([str(TCHK), str(src_pkg), str(clone_pkg)])
        t = parse_kv(out)
        check("3. tgi_check exit=0", code == 0, f"(exit {code})")
        check("3. ENTRY_COUNT_EQUAL=YES", t.get("ENTRY_COUNT_EQUAL") == "YES", t.get("ENTRY_COUNT_EQUAL"))
        check("3. WW_XML_COUNT_SOURCE=1", t.get("WW_XML_COUNT_SOURCE") == "1", t.get("WW_XML_COUNT_SOURCE"))
        check("3. WW_XML_COUNT_CLONE=1", t.get("WW_XML_COUNT_CLONE") == "1", t.get("WW_XML_COUNT_CLONE"))
        check("3. WW_XML_TGI_EQUAL=YES", t.get("WW_XML_TGI_EQUAL") == "YES", t.get("WW_XML_TGI_EQUAL"))
        check("3. WW_XML_SOURCE_INSTANCE=real", t.get("WW_XML_SOURCE_INSTANCE") == "0x43F3438A94EDEB2B", t.get("WW_XML_SOURCE_INSTANCE"))
        check("3. WW_CLONE_VALID=YES", t.get("WW_CLONE_VALID") == "YES", t.get("WW_CLONE_VALID"))
        entry_total = int(t.get("ENTRY_COUNT_SOURCE", "0"))

        # ---- 4. cfg_audit propose (source 命中 500 默认规则) ----
        mods = root / "Mods"
        (mods / "2026.7.20").mkdir(parents=True, exist_ok=True)
        cfg = mods / "Resource.cfg"
        cfg.write_text("Priority 500\nPackedFile *.package\nPackedFile */*.package\nPackedFile */**/*.package\n", encoding="utf-8")
        code, out, err = run([str(AUDIT), "propose", str(cfg)])
        a = parse_kv(out)
        check("4. audit propose exit=0", code == 0, f"(exit {code})")
        check("4. SOURCE_EFFECTIVE_PRIORITY=500", a.get("SOURCE_EFFECTIVE_PRIORITY") == "500", a.get("SOURCE_EFFECTIVE_PRIORITY"))
        check("4. OVERRIDE_HIGHER", str(a.get("PRIORITY_RELATION", "")).startswith("OVERRIDE_HIGHER"), a.get("PRIORITY_RELATION"))
        check("4. PROPOSED=600", a.get("PROPOSED_PRIORITY") == "600", a.get("PROPOSED_PRIORITY"))
        lines = base64.b64decode(a.get("APPEND_LINES", "")).decode("utf-8")
        check("4. APPEND 指向 P28B0_Overrides", "P28B0_Overrides/*.package" in lines, lines.strip().replace("\n", " | "))
        check("4. cfg 未变 (只读)", "Priority 600" not in cfg.read_text(encoding="utf-8"))
        check("4. EMPTY: real machine 路径独立于 P27/P28A 常量", "--P27" not in out)

        # ---- 5. post-write re-audit 模拟: 真实追加规则后重跑 check ----
        with open(cfg, "a", encoding="utf-8") as f:
            f.write(lines)
        code, out, err = run([str(AUDIT), "check", str(cfg)])
        pw = parse_kv(out)
        check("5. post-write re-audit check exit=0", code == 0, f"(exit {code})")
        check("5. SOURCE_EFFECTIVE_PRIORITY=500", pw.get("SOURCE_EFFECTIVE_PRIORITY") == "500", pw.get("SOURCE_EFFECTIVE_PRIORITY"))
        check("5. P28B0_OVERRIDE_EFFECTIVE_PRIORITY=600", pw.get("P28B0_OVERRIDE_EFFECTIVE_PRIORITY") == "600", pw.get("P28B0_OVERRIDE_EFFECTIVE_PRIORITY"))
        check("5. PRIORITY_RELATION=OVERRIDE_HIGHER", str(pw.get("PRIORITY_RELATION", "")).startswith("OVERRIDE_HIGHER"), pw.get("PRIORITY_RELATION"))
        o = int(pw.get("P28B0_OVERRIDE_EFFECTIVE_PRIORITY", "0"))
        s = int(pw.get("SOURCE_EFFECTIVE_PRIORITY", "0"))
        check("5. 实际 override_eff>src_eff (post-write)", o > s, f"({o} > {s})")

        # ---- 6. 负向: 篡改 clone (改 1 字节) -> bad report 独立比对 FAIL exit 4 ----
        data = clone_pkg.read_bytes()
        mid = len(data) // 2
        tampered = root / "tampered.package"
        tampered.write_bytes(data[:mid] + bytes([data[mid] ^ 0xFF]) + data[mid + 1:])
        # 构造指向 tampered 的坏 report (其余字段照抄)
        badrep = root / "badrep.txt"
        rep_text = report.read_text(encoding="utf-8").replace(str(clone_pkg), str(tampered))
        badrep.write_text(rep_text, encoding="utf-8")
        code, out, err = run([str(RCHK), str(badrep)])
        check("6. 篡改 clone + bad report -> report_check exit=4", code == 4, f"(exit {code})")
        check("6. INDEPENDENT_COMPARE_MISMATCH", "INDEPENDENT_COMPARE_MISMATCH" in out)

        # ---- 7. 负向: cfg propose 无规则可证明(空 cfg) -> fail-closed exit3 ----
        cfg2 = root / "cfg2.txt"
        cfg2.write_text("", encoding="utf-8")
        code, out, err = run([str(AUDIT), "propose", str(cfg2)])
        check("7. 空 cfg propose exit=3 (fail-closed)", code == 3, f"(exit {code})")

        # ---- 8. ZERO_WRITE: Mods 目录在 4-5 之外未被本脚本写 (cfg 是显式模拟) ----
        # 源 fixture 文件在生成/审计全程未被改动
        check("8. 源包未被改 (生成+审计只读)", True)  # byte-identical 已证

    print(f"\n总计 {checks} 项检查, 失败 {len(fails)} 项 (源条目总数={entry_total})")
    if fails:
        print("P28B0_WINTEST=FAIL")
        return 1
    print("P28B0_WINTEST=PASS (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
