#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""白盒: ww_animation_forensic_deep_one — 观测真实结构 + STBL cross-match + 证据型结论

关键: 夹具用【非白盒字段名】的真实风格 XML (如 <ActorDisplay> 标签 + localization 引用),
证明工具【观测】而非【硬套】白盒 animation_raw_display_name/animation_id/animation_clip_name,
并正确输出 WHITEBOX_SCHEMA_ASSUMPTION_INVALID_FOR_REAL_SAMPLE。
"""
import struct, subprocess, sys, zlib, tempfile
from pathlib import Path

BASE = "/root/.openclaw/workspace/sims4-translator"
sys.path.insert(0, BASE)
sys.path.insert(0, BASE + "/scripts")

WW_ANIM_XML = 0x7DF2169C
CLIP = 0x6B20C4F3
ANIM_RCOL = 0xBC4A5044
STBL = 0x220557DA

from ww_animation_canary_builder import build_package  # noqa

# 真实风格 XML: 不用白盒字段; 用 title/actor_id/localization_hash 等
XML = """<?xml version="1.0" encoding="utf-8"?>
<WickedWhimsActorDatabase>
  <SceneEntry scene_id="scene_floor_002" title="MSWD Force Floor" localization_hash="0x3F220598" clipref="0x0F000000000000AA">
    <Tag name="floor" />
    <Actor actor_id="actor_77" voice_bank="vb_base" enabled="true" />
  </SceneEntry>
  <SceneEntry scene_id="scene_floor_002b" title="MSWD Force Floor B" localization_hash="0x00000012">
    <Actor actor_id="actor_78" voice_bank="vb_base" enabled="true" />
  </SceneEntry>
</WickedWhimsActorDatabase>
"""


def build_stbl(entries):
    body = bytearray(b"STBL")
    body += struct.pack("<H", 5) + bytes([0]) + struct.pack("<Q", len(entries)) + struct.pack("<H", 0) + struct.pack("<I", 24)
    for kh, flags, text in entries:
        tb = text.encode("utf-8")
        body += struct.pack("<I", kh) + bytes([flags]) + struct.pack("<H", len(tb)) + tb
    return bytes(body)


def main():
    fails = []
    def check(n, c, d=""):
        print(("PASS" if c else "FAIL") + f"  {n}" + (f"  {d}" if d else ""))
        if not c: fails.append(n)

    tmp = Path(tempfile.mkdtemp(prefix="wb_deepone_"))
    src = tmp / "MSWD_FORCE_FLOOR_002.package"
    STBL_KEY = 0x3F220598  # 与 XML localization_hash=0x3F220598 匹配
    build_package([
        (WW_ANIM_XML, 0, 0x7DF2_0000_0000_0001, zlib.compress(XML.encode("utf-8"))),
        (CLIP, 0, 0x0F000000000000AA, b"\x11" * 32),
        (CLIP, 0, 0x0F000000000000BB, b"\x22" * 32),
        (ANIM_RCOL, 0, 0x0F000000000000AA, b"\x33" * 32),
        (ANIM_RCOL, 0, 0x0F000000000000BB, b"\x44" * 32),
        (STBL, 0x80000000, 0x0100000000000001,
         build_stbl([(0x3F220598, 0, "MSWD Force Floor"), (0xDEADBEEF, 0, "other")])),
    ], src)

    r = subprocess.run([sys.executable, BASE + "/scripts/ww_animation_forensic_deep_one.py",
                        "--source", str(src), "--out-dir", str(tmp / "out")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = r.stdout
    err = r.stderr
    check("rc==0", r.returncode == 0, f"rc={r.returncode}\n{out}\nERR:{err}")
    check("WW_XML_COUNT=1", "WW_XML_COUNT=1" in out, "")
    check("STBL_COUNT=1", "STBL_COUNT=1" in out, "")
    check("CLIP=2", "CLIP=2" in out or "CLIP=2 " in out.replace(" ", " "), "")
    check("root observed", "WickedWhimsActorDatabase" in out, "")
    check("unique tags include SceneEntry/Tag/Actor",
          all(t in out for t in ("SceneEntry", "Tag", "Actor")), "")
    check("unique attrs observed", all(x in out for x in ("scene_id", "title", "localization_hash", "clipref", "actor_id")), "")
    # STBL entries (in report .md)
    check("STBL key printed", "key=0x3F220598" in out and "MSWD Force Floor" in out, "")
    md_report = (tmp / "out" / "ww_animation_deep_one.md").read_text(encoding="utf-8")
    check("STBL key2 printed (report)", "key=0xDEADBEEF" in md_report and "'other'" in md_report, "")
    # XML<->STBL cross match: localization_hash=0x3F220598 matches STBL key 0x3F220598
    check("XML_STBL_LINKS_FOUND>=1", "XML_STBL_LINKS_FOUND=1" in out or "XML_STBL_LINKS_FOUND=2" in out, f"got={[l for l in out.splitlines() if 'XML_STBL_LINKS_FOUND' in l]}")
    check("cross-match line present", "-> STBL key=0x3F220598" in out, "")
    # CLIP: clipref=0x0F000000000000AA matches CLIP instance
    check("CLIP_MATCH found", "CLIP_LINKS_FOUND=1" in out and "0x0F000000000000AA" in out, "")
    # whitebox invalid flag: 真实字段无 animation_raw_display_name/id/clip
    check("WHITEBOX_SCHEMA_ASSUMPTION_INVALID", "WHITEBOX_SCHEMA_ASSUMPTION_INVALID_FOR_REAL_SAMPLE" in out, "")
    check("DISPLAY_STORAGE=STBL_REFERENCED", "DISPLAY_STORAGE=STBL_REFERENCED" in out, "")
    # DISPLAY_FIELD 应为含自然语言文本的属性 (title)
    check("DISPLAY_FIELD=title", "DISPLAY_FIELD=title" in out, f"got={[l for l in out.splitlines() if l.startswith('DISPLAY_FIELD')]}")
    check("ZERO_WRITE_TO_MODS=YES", "ZERO_WRITE_TO_MODS=YES" in out, "")
    check("report mkd written", (tmp / "out" / "ww_animation_deep_one.md").exists(), "")

    # --- 无 STBL 且无自然语言文本 -> DISPLAY_STORAGE=UNKNOWN_DIRECT_TEXT_NONE
    XML2 = ('<Data><Item id="0xABC123" clip="0x0F0000000000AA" /></Data>')
    src2 = tmp / "NoText.package"
    build_package([
        (WW_ANIM_XML, 0, 0x7DF2_0000_0000_0002, zlib.compress(XML2.encode())),
        (CLIP, 0, 0x0F000000000000AA, b"\x11" * 8),
    ], src2)
    r2 = subprocess.run([sys.executable, BASE + "/scripts/ww_animation_forensic_deep_one.py",
                         "--source", str(src2), "--out-dir", str(tmp / "out2")],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("no-text rc==0", r2.returncode == 0, f"rc={r2.returncode}")
    check("no-text DISPLAY=UNKNOWN_DIRECT_TEXT_NONE", "DISPLAY_STORAGE=UNKNOWN_DIRECT_TEXT_NONE" in r2.stdout, "")

    print(f"\nHARNESS {'ALL PASS' if not fails else 'FAIL: ' + str(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
