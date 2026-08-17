#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""白盒: ww_animation_canary_builder — A clone / B sidecar 机器验证
建单注册 XML 的真实 WickedWhimsAnimationPackage 夹具 (多 entry + CLIP + ANIM + STBL),
跑 canary builder, 断言全部 invariants + 只改目标 entry 的 display。
"""
import os, struct, sys, zlib, tempfile, csv, subprocess
from pathlib import Path

BASE = "/root/.openclaw/workspace/sims4-translator"
sys.path.insert(0, BASE)
sys.path.insert(0, BASE + "/scripts")

WW_ANIM_XML = 0x7DF2169C
CLIP = 0x6B20C4F3
ANIM_RCOL = 0xBC4A5044
STBL = 0x220557DA

from ww_animation_canary_builder import build_package, parse_anim_xml, read_body_raw, safe_parse  # noqa

WW_XML = """<?xml version="1.0" encoding="utf-8"?>
<I version="1">
  <L n="animations_list">
    <U>
      <T n="animation_raw_display_name">Slow Romantic Kiss</T>
      <T n="animation_author">TestCreator</T>
      <T n="animation_category">Kissing</T>
      <T n="animation_tags">romantic</T>
      <T n="animation_locations">LoveSeat</T>
      <L n="animation_actors_list">
        <U>
          <T n="actor_id">0</T>
          <T n="animation_clip_name">clip_0001</T>
        </U>
      </L>
    </U>
  </L>
</I>
"""


def build_stbl(entries):
    body = bytearray(b"STBL")
    body += struct.pack("<H", 5)
    body += bytes([0])
    body += struct.pack("<Q", len(entries))
    body += struct.pack("<H", 0)
    body += struct.pack("<I", 24)
    for kh, flags, text in entries:
        tb = text.encode("utf-8")
        body += struct.pack("<I", kh)
        body += bytes([flags])
        body += struct.pack("<H", len(tb))
        body += tb
    return bytes(body)


def main():
    fails = []
    def check(n, c, d=""):
        print(("PASS" if c else "FAIL") + f"  {n}" + (f"  {d}" if d else ""))
        if not c: fails.append(n)

    tmp = Path(tempfile.mkdtemp(prefix="wb_canary_"))
    src = tmp / "WW_RealCreator_Animations.package"
    inst_xml = 0x0100_0000_8000_0001
    build_package([
        (WW_ANIM_XML, 0, inst_xml, zlib.compress(WW_XML.encode("utf-8"))),
        (CLIP, 0, 0x0F00000000000001, b"\x11" * 64),
        (CLIP, 0, 0x0F00000000000002, b"\x22" * 64),
        (ANIM_RCOL, 0, 0x0F00000000000001, b"\x33" * 64),
        (ANIM_RCOL, 0, 0x0F00000000000002, b"\x44" * 64),
        (STBL, 0x80000000, 0x0100000000000001, build_stbl([(0xAA, 0, "Kissing")])),
    ], src)

    new_disp = "【CHS_CANARY】强制地板002"
    old_disp = "Slow Romantic Kiss"
    r = subprocess.run([sys.executable, BASE + "/scripts/ww_animation_canary_builder.py",
                        "--source", str(src), "--display-old", old_disp,
                        "--display-new", new_disp,
                        "--out-dir", str(tmp / "out")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = r.stdout
    check("builder rc==0", r.returncode == 0, f"rc={r.returncode}\n{out}\nERR:\n{r.stderr}")

    dir_a = tmp / "out" / "ww_animation_canary_A"
    dir_b = tmp / "out" / "ww_animation_canary_B"
    clone = dir_a / "WW_RealCreator_Animations_CANARY_A.package"
    sidecar = dir_b / "WW_RealCreator_Animations_WW_CHS_CANARY.package"
    check("A clone exists", clone.exists(), "")
    check("B sidecar exists", sidecar.exists(), "")
    check("ZERO_WRITE_TO_MODS=YES in stdout", "ZERO_WRITE_TO_MODS=YES" in out, "")
    check("TEST_A PASS", "TEST_A_ARTIFACT:" in out, "")
    check("stdout PASS markers", new_disp in out and f"display_old={old_disp}" in out, "")

    # A clone: count / TGI set / 只改 1 display / internal 不变
    idxA, errA = safe_parse(clone)
    ixs, _ = safe_parse(src)
    check("A parse OK", errA is None and idxA is not None, errA or "")
    check("A resource count same", len(idxA.entries) == len(ixs.entries),
          f"{len(idxA.entries)} vs {len(ixs.entries)}")
    tgiA = {(e.type_id, e.group_id, e.instance_id) for e in idxA.entries}
    tgiS = {(e.type_id, e.group_id, e.instance_id) for e in ixs.entries}
    check("A TGI set same", tgiA == tgiS, "")
    # 非 WW-XML body 逐字节一致
    allbytes_same = True
    for e in ixs.entries:
        if e.type_id == WW_ANIM_XML:
            continue
        e2 = next((x for x in idxA.entries if (x.type_id, x.group_id, x.instance_id) == (e.type_id, e.group_id, e.instance_id)), None)
        if e2 is None or read_body_raw(src, e) != read_body_raw(clone, e2):
            allbytes_same = False
    check("A non-XML bodies byte-identical", allbytes_same, "")
    # clone XML: 只改 display T 节点 text
    w1 = next(e for e in idxA.entries if e.type_id == WW_ANIM_XML)
    _sA, txtA, _ = parse_anim_xml(read_body_raw(clone, w1))
    check("A display new present", new_disp in txtA, "")
    check("A old display gone (target)", old_disp not in txtA, "")
    check("A author same", '<T n="animation_author">TestCreator</T>' in txtA, "")
    check("A category same", '<T n="animation_category">Kissing</T>' in txtA, "")
    check("A tags same", '<T n="animation_tags">romantic</T>' in txtA, "")
    check("A location same", '<T n="animation_locations">LoveSeat</T>' in txtA, "")
    check("A actor_id same", '<T n="actor_id">0</T>' in txtA, "")
    check("A clip same", '<T n="animation_clip_name">clip_0001</T>' in txtA, "")

    # B sidecar: exactly 1 resource, exact TGI, no CLIP/ANIM/STBL
    idxB, errB = safe_parse(sidecar)
    check("B parse OK", errB is None and idxB is not None, errB or "")
    check("B resources=1", len(idxB.entries) == 1, f"{len(idxB.entries)}")
    eB = idxB.entries[0]
    check("B exact TGI match", eB.type_id == WW_ANIM_XML and eB.group_id == 0 and eB.instance_id == inst_xml,
          f"{eB.type_id:08X}/{eB.group_id:08X}/{eB.instance_id:016X}")
    _sB, txtB, _ = parse_anim_xml(read_body_raw(sidecar, eB))
    check("B display new present", new_disp in txtB, "")
    check("B internal unchanged", '<T n="animation_clip_name">clip_0001</T>' in txtB
          and '<T n="animation_author">TestCreator</T>' in txtB, "")
    check("B no CLIP/ANIM/STBL", all(e.type_id not in (CLIP, ANIM_RCOL, STBL) for e in idxB.entries), "")

    # fail-closed: 双注册 XML 应拒绝
    src2 = tmp / "WW_DoubleXML_Animations.package"
    build_package([
        (WW_ANIM_XML, 0, 0x0200_0000_0000_0001, zlib.compress(WW_XML.encode())),
        (WW_ANIM_XML, 0, 0x0200_0000_0000_0002, zlib.compress(WW_XML.encode())),
        (CLIP, 0, 0x0F00000000000001, b"\x11" * 64),
    ], src2)
    r2 = subprocess.run([sys.executable, BASE + "/scripts/ww_animation_canary_builder.py",
                         "--source", str(src2), "--display-old", "Slow Romantic Kiss",
                         "--display-new", new_disp, "--out-dir", str(tmp / "out2")],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("fail-closed: 双注册XML 拒绝 (rc=3)", r2.returncode == 3, f"rc={r2.returncode}\n{r2.stdout}\n{r2.stderr}")
    check("fail-closed: 不产 artifact", not (tmp / "out2" / "ww_animation_canary_A").exists(), "")

    # fail-closed: display_old 精确匹配 != 1 应拒绝 (0 匹配)
    r4 = subprocess.run([sys.executable, BASE + "/scripts/ww_animation_canary_builder.py",
                         "--source", str(src), "--display-old", "NoSuchDisplayText",
                         "--display-new", new_disp, "--out-dir", str(tmp / "out4")],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("fail-closed: 0 匹配 display_old 拒绝 (rc=3)", r4.returncode == 3, f"rc={r4.returncode}\n{r4.stdout}")
    check("fail-closed: 0 匹配不产 artifact", not (tmp / "out4" / "ww_animation_canary_A").exists(), "")

    # 兼容性注意: StripClub 旧 attribute-style 不再作为 production schema 权威;
    # 本 harness 只对 WickedWhimsAnimationPackage + animation_raw_display_name 做权威断言。

    print(f"\nHARNESS {'ALL PASS' if not fails else 'FAIL: ' + str(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
