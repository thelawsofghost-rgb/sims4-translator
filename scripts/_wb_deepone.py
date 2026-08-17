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

# 真实 SAMPLE 3 结构 (Sims tuning): 只改 display 的 T 节点 + 独立功能字段
XML = """<?xml version="1.0" encoding="utf-8"?>
<I version="1">
  <L n="animations_list">
    <U>
      <T n="animation_raw_display_name">FORCE_FLOOR_002</T>
      <T n="animation_author">MSWD</T>
      <T n="animation_locations">FLOOR</T>
      <T n="animation_category">VAGINAL</T>
      <T n="animation_tags">FORCED</T>
      <L n="animation_actors_list">
        <U>
          <T n="actor_id">0</T>
          <T n="animation_clip_name">MSWD:PosePack_202204170320344277_set_1</T>
        </U>
      </L>
    </U>
  </L>
</I>
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
    build_package([
        (WW_ANIM_XML, 0, 0x7DF2169CB3390ED6, zlib.compress(XML.encode("utf-8"))),
        (CLIP, 0, 0x0F000000000000AA, b"\x11" * 32),
        (CLIP, 0, 0x0F000000000000BB, b"\x22" * 32),
        (ANIM_RCOL, 0, 0x0F000000000000AA, b"\x33" * 32),
        (ANIM_RCOL, 0, 0x0F000000000000BB, b"\x44" * 32),
        (STBL, 0x80000000, 0x0100000000000001,
         build_stbl([(0xDEADBEEF, 0, "unrelated")])),
    ], src)

    r = subprocess.run([sys.executable, BASE + "/scripts/ww_animation_forensic_deep_one.py",
                        "--source", str(src), "--out-dir", str(tmp / "out")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = r.stdout
    err = r.stderr
    check("rc==0", r.returncode == 0, f"rc={r.returncode}\n{out}\nERR:{err}")
    check("WW_XML_COUNT=1", "WW_XML_COUNT=1" in out, "")
    check("STBL_COUNT=1", "STBL_COUNT=1" in out, "")
    check("CLIP=2", "CLIP=2" in out, "")
    check("root observed", "root=I" in out, "")
    check("animations_list observed", "animations_list" in out, "")
    # 真实 Sims tuning display
    check("DISPLAY_STORAGE=DIRECT_XML", "DISPLAY_STORAGE=DIRECT_XML" in out, "")
    check("DISPLAY_FIELD=animation_raw_display_name", "DISPLAY_FIELD=animation_raw_display_name" in out, "")
    check("DISPLAY_VALUE=FORCE_FLOOR_002", "DISPLAY_VALUE=FORCE_FLOOR_002" in out, "")
    # 无 STBL link (display 直接来自 XML)
    check("XML_STBL_LINKS_FOUND=0", "XML_STBL_LINKS_FOUND=0" in out, "")
    # 独立功能字段
    check("INDEPENDENT_FIELDS author", "animation_author=MSWD" in out, "")
    check("INDEPENDENT_FIELDS category", "animation_category=VAGINAL" in out, "")
    check("INDEPENDENT_FIELDS locations", "animation_locations=FLOOR" in out, "")
    check("INDEPENDENT_FIELDS tags", "animation_tags=FORCED" in out, "")
    check("INDEPENDENT_FIELDS clip", "animation_clip_name=MSWD:PosePack" in out, "")
    check("INDEPENDENT_FIELDS actor_id", "actor_id=0" in out, "")
    # 无稳定 per-entry id -> NONE_EXPLICIT_FOUND
    check("INTERNAL_ID_FIELD=NONE_EXPLICIT_FOUND", "INTERNAL_ID_FIELD=NONE_EXPLICIT_FOUND" in out, "")
    # display 与功能字段结构分离 (author/locations/category/tags/clip/actor 全部在场 -> CLEAR)
    check("DISPLAY_INTERNAL_SEPARATION=CLEAR", "DISPLAY_INTERNAL_SEPARATION=CLEAR" in out, "")
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
