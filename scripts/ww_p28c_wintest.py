#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p28c_wintest.py —— P28C 全链路沙箱测试 (证明 mem_size 修复 + ASCII canary 语义)

构造一个真实形态的多条目源 fixture (含 1 个压缩 WW_ANIM_XML, instance 0x43F3438A94EDEB2B,
animations_list 数百条 U), 其中 ordinal 299 的 animation_raw_display_name 原文为一段**非 7 字节**
英文串 (长度 != "TEST299"), 使"改 299->TEST299"必然改变解压总长度 -> source 旧 field7 必然过期。

跑通:
  1. generator (ww_p28c_ascii_canary.py) -> output/ww_p28c/artifact + report
     必须:  TARGET_299=TEST299 / TARGETS_CHANGED=1/1 / NON_TARGET_XML_DIFF=0 (除299 全等)
           / WRITTEN_MEM_SIZE == NEW_XML_DECOMPRESSED_SIZE (修复; 已不等于 SOURCE_MEM_SIZE, 证明旧 P27 逻辑会写错)
           / MEM_SIZE_MATCH_NEW_XML=YES
  2. report_check 独立机验 PASS (独立读包 bytes, 不信 report YES)
  3. tgi_check 独立 entry/TGI 普查 PASS
  4. cfg_audit propose -> OVERRIDE_HIGHER (source 500 -> P28C 600)
  5. 【模拟 deploy 真实写 cfg】append P28C 规则后重跑 cfg_audit check == post-write re-audit
     必须实际得 SOURCE_EFFECTIVE_PRIORITY=500 / P28C_OVERRIDE_EFFECTIVE_PRIORITY=600 / OVERRIDE_HIGHER
  6. 回归演示: 同一源用 P27 坏逻辑 (source field7) 计算会得到 WRITTEN != NEW (揭示 re-regression)
  7. 负向: 篡改包后 report_check 独立比对 FAIL exit 4

退出码: 0=全 PASS, 1=任一失败
"""
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ww_animation_canary_builder import build_package  # noqa: E402

PY = sys.executable
GEN   = HERE / "ww_p28c_ascii_canary.py"
RCHK  = HERE / "ww_p28c_report_check.py"
TCHK  = HERE / "ww_p28c_tgi_check.py"
AUDIT = HERE / "ww_p28c_cfg_audit.py"

WW_ANIM_XML = 0x7DF2169C
GROUP = 0x00000000
INST = 0x43F3438A94EDEB2B
STBL = 0x220557DA
CLIP = 0x0354E541

TARGET_ORD = 299
TARGET_NEW = "TEST299"
OLD_299 = "A deliberately longer english animation display name NumberTwoNineNine not seven bytes"


def make_anim_xml_body(n_entries=306):
    """构造含 n_entries 个 U 的 WickedWhimsAnimationPackage XML (list of U each with raw)."""
    parts = ['<?xml version="1.0" encoding="utf-8"?>',
             '<I c="WickedWhimsAnimationPackage" i="1" s="1">',
             '<U n="wickedwhims_animations"><L n="animations_list">']
    for i in range(n_entries):
        raw = OLD_299 if i == TARGET_ORD else f"Animation English #{i} plain text"
        author = "Nevely42" if i in (TARGET_ORD, 0) else "SomeAuthor"
        clip = f"clip_{i}"
        parts.append(f'<U n="animation"><T n="animation_raw_display_name">{raw}</T>'
                     f'<T n="animation_clip_name">{clip}</T>'
                     f'<U n="animation_author"><T n="name">{author}</T></U></U>')
    parts.append('</L></U></I>')
    return "\n".join(parts).encode("utf-8")


def make_fixture(pkg: Path):
    """源 fixture: 1 个压缩 WW_ANIM_XML + 若干附带资源, mem_size=真实解压长."""
    xml_plain = make_anim_xml_body()
    xml_comp = zlib.compress(xml_plain)
    xml_meta = {"comp_state": True, "comp_type": 0x5A42, "mem_size": len(xml_plain),
                "offset_high_bit": 1, "size_high_bit": 1}
    items = [(WW_ANIM_XML, GROUP, INST, xml_comp, xml_meta)]
    for i in range(3):
        b = f"fake-stbl-{i}".encode()
        items.append((STBL, GROUP, 0x2200000000000000 + i, b,
                      {"comp_state": False, "comp_type": 0, "mem_size": len(b),
                       "offset_high_bit": 0, "size_high_bit": 0}))
    for i in range(3):
        b = f"fake-clip-{i}".encode()
        items.append((CLIP, GROUP, 0x0354000011110000 + i, b,
                      {"comp_state": False, "comp_type": 0, "mem_size": len(b),
                       "offset_high_bit": 0, "size_high_bit": 0}))
    build_package(items, pkg, header_comp=0x80000000, major=2, minor=1)
    return pkg


def run(argv):
    r = subprocess.run([PY] + argv, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def kv(out):
    d = {}
    for ln in out.splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            d[k] = v.strip()
    return d


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
        src_pkg = make_fixture(root / "WW_Nevely42_Animations.package")
        outdir = root / "out" / "ww_p28c"
        art = outdir / "WW_P28C_TEST299_Override.package"
        report = outdir / "ww_p28c_report.txt"

        # ---- 1. generator ----
        code, out, err = run([str(GEN), "--source", str(src_pkg), "--out-dir", str(root / "out"), "--force"])
        g = kv(out)
        check("1. generator exit=0", code == 0, f"(exit {code})")
        check("1. 无 stderr", err.strip() == "", err[:120])
        check("1. VERDICT=PASS", g.get("VERDICT") == "PASS", g.get("VERDICT"))
        check("1. TARGET_ORDINAL=299", g.get("TARGET_ORDINAL") == "299", g.get("TARGET_ORDINAL"))
        check("1. TARGET_NEW_RAW=TEST299", g.get("TARGET_NEW_RAW") == "TEST299", g.get("TARGET_NEW_RAW"))
        check("1. TARGETS_CHANGED=1/1", str(g.get("TARGETS_CHANGED", "")).startswith("1/"), g.get("TARGETS_CHANGED"))
        check("1. NON_TARGET_XML_DIFF=0", g.get("NON_TARGET_XML_DIFF") == "0", g.get("NON_TARGET_XML_DIFF"))
        check("1. artifact 存在", art.is_file())
        check("1. report 存在", report.is_file())

        # mem_size 关键: source 旧值必须 != 新解压长 (证明 P27 旧逻辑会写错) 且写入==新解压长
        src_mem = int(g.get("SOURCE_MEM_SIZE", "-1"))
        new_decomp = int(g.get("NEW_XML_DECOMPRESSED_SIZE", "-1"))
        written = int(g.get("WRITTEN_MEM_SIZE", "-1"))
        check("1. 长度改变 (source field7 已过期) 证明前提成立", src_mem != new_decomp, f"(src={src_mem} new={new_decomp})")
        check("1. WRITTEN_MEM_SIZE==NEW_XML_DECOMPRESSED_SIZE (fix)", written == new_decomp, f"(written={written} new={new_decomp})")
        check("1. WRITTEN != SOURCE (揭示 P27 旧 bug)", written != src_mem, f"(written={written} src={src_mem})")
        check("1. MEM_SIZE_MATCH_NEW_XML=YES", g.get("MEM_SIZE_MATCH_NEW_XML") == "YES", g.get("MEM_SIZE_MATCH_NEW_XML"))
        check("1. MEM_SIZE_REGRESSION_FIXED=YES", g.get("MEM_SIZE_REGRESSION_FIXED") == "YES", g.get("MEM_SIZE_REGRESSION_FIXED"))

        # ---- 2. report_check 独立机验 ----
        code, out, err = run([str(RCHK), str(report)])
        rc = kv(out)
        check("2. report_check exit=0", code == 0, f"(exit {code})")
        check("2. REPORT_CHECK=PASS", "REPORT_CHECK=PASS" in out, out.splitlines()[0] if out else "")
        check("2. MEM_SIZE_INDEP_MATCH=YES", rc.get("MEM_SIZE_INDEP_MATCH") == "YES", rc.get("MEM_SIZE_INDEP_MATCH"))
        check("2. P27_MEM_SIZE_REGRESSION_FIXED=YES", rc.get("P27_MEM_SIZE_REGRESSION_FIXED") == "YES", rc.get("P27_MEM_SIZE_REGRESSION_FIXED"))

        # ---- 3. tgi_check ----
        code, out, err = run([str(TCHK), str(src_pkg), str(art)])
        t = kv(out)
        check("3. tgi_check exit=0", code == 0, f"(exit {code})")
        check("3. SOURCE entry 多条目", int(t.get("SOURCE_ENTRY_COUNT", "0")) > 1, t.get("SOURCE_ENTRY_COUNT"))
        check("3. OVERRIDE_ENTRY_COUNT=1", t.get("OVERRIDE_ENTRY_COUNT") == "1", t.get("OVERRIDE_ENTRY_COUNT"))
        check("3. TGI_EQUAL=YES", t.get("TGI_EQUAL") == "YES", t.get("TGI_EQUAL"))
        check("3. WW_XML_SOURCE_INSTANCE=real", t.get("WW_XML_SOURCE_INSTANCE") == "0x43F3438A94EDEB2B", t.get("WW_XML_SOURCE_INSTANCE"))
        check("3. VERDICT=PASS", t.get("VERDICT") == "PASS", t.get("VERDICT"))

        # ---- 4. cfg_audit propose ----
        mods = root / "Mods"
        (mods / "2026.7.20").mkdir(parents=True, exist_ok=True)
        cfg = mods / "Resource.cfg"
        cfg.write_text("Priority 500\nPackedFile *.package\nPackedFile */*.package\nPackedFile */**/*.package\n", encoding="utf-8")
        code, out, err = run([str(AUDIT), "propose", str(cfg)])
        a = kv(out)
        check("4. propose exit=0", code == 0, f"(exit {code})")
        check("4. SOURCE_EFFECTIVE_PRIORITY=500", a.get("SOURCE_EFFECTIVE_PRIORITY") == "500", a.get("SOURCE_EFFECTIVE_PRIORITY"))
        check("4. OVERRIDE_HIGHER", str(a.get("PRIORITY_RELATION", "")).startswith("OVERRIDE_HIGHER"), a.get("PRIORITY_RELATION"))
        check("4. PROPOSED=600", a.get("PROPOSED_PRIORITY") == "600", a.get("PROPOSED_PRIORITY"))
        import base64
        lines = base64.b64decode(a.get("APPEND_LINES", "")).decode("utf-8")
        check("4. APPEND 指向 P28C_Overrides", "P28C_Overrides/*.package" in lines, lines.strip().replace("\n", " | "))
        check("4. cfg 未变(propose 只读)", "600" not in cfg.read_text(encoding="utf-8"))

        # ---- 5. 模拟 deploy 真实写 cfg + post-write re-audit ----
        with open(cfg, "a", encoding="utf-8") as f:
            f.write(lines)
        code, out, err = run([str(AUDIT), "check", str(cfg)])
        pw = kv(out)
        check("5. post-write check exit=0", code == 0, f"(exit {code})")
        check("5. SOURCE_EFFECTIVE_PRIORITY=500", pw.get("SOURCE_EFFECTIVE_PRIORITY") == "500", pw.get("SOURCE_EFFECTIVE_PRIORITY"))
        check("5. P28C_OVERRIDE_EFFECTIVE_PRIORITY=600", pw.get("P28C_OVERRIDE_EFFECTIVE_PRIORITY") == "600", pw.get("P28C_OVERRIDE_EFFECTIVE_PRIORITY"))
        check("5. PRIORITY_RELATION=OVERRIDE_HIGHER", str(pw.get("PRIORITY_RELATION", "")).startswith("OVERRIDE_HIGHER"), pw.get("PRIORITY_RELATION"))
        o = int(pw.get("P28C_OVERRIDE_EFFECTIVE_PRIORITY", "0"))
        s = int(pw.get("SOURCE_EFFECTIVE_PRIORITY", "0"))
        check("5. 实际 override_eff>src_eff (post-write)", o > s, f"({o} > {s})")

        # ---- 6. 回归: P27 坏逻辑 (沿用 source field7) 会写错 mem_size ----
        # 在源 data 上演示: 若写 WRITTEN=SOURCE_MEM_SIZE(旧) 则 != NEW_XML_DECOMPRESSED(新)
        check("6. P27 旧逻辑(复用 source field7) 必错 (即为本次 re-regression 证明)",
              src_mem != new_decomp, f"(旧 field7={src_mem} != 新解压长={new_decomp})")

        # ---- 7. 负向: 篡改包 + bad report -> report_check exit 4 ----
        tamper = root / "tampered.package"
        data = art.read_bytes()
        mid = len(data) // 2
        tamper.write_bytes(data[:mid] + bytes([data[mid] ^ 0xFF]) + data[mid + 1:])
        badrep = root / "badrep.txt"
        badrep.write_text(report.read_text(encoding="utf-8").replace(str(art), str(tamper)), encoding="utf-8")
        code, out, err = run([str(RCHK), str(badrep)])
        check("7. 篡改包 + bad report -> exit=4", code == 4, f"(exit {code})")

        # ---- 8. 负向: 空 cfg -> propose exit 3 ----
        cfg2 = root / "cfg.txt"
        cfg2.write_text("", encoding="utf-8")
        code, out, err = run([str(AUDIT), "propose", str(cfg2)])
        check("8. 空 cfg propose exit=3 (fail-closed)", code == 3, f"(exit {code})")

    print(f"\n总计 {checks} 项检查, 失败 {len(fails)}")
    if fails:
        print("P28C_WINTEST=FAIL")
        return 1
    print("P28C_WINTEST=PASS (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
