#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MVP 白盒: ww_animation_mvp_forensic 只读取证最小验证
3 个必需 + 2 个身份关键边界:
 W1 zlib WW_ANIM_XML 能正确解压 + 双证据 => CONFIRMED
 W2 有 CLIP 但无 WW 注册 XML => 不 CONFIRMED (fail-closed)
 W3 文件名 WW_xxx 但无正证据 => 不 CONFIRMED (文件名不作依据)
 W4 malformed WW_ANIM_XML (zlib 破损) => fail-closed, 不崩
 W5 非 WW XML 不误判 (无 WW 注册 schema 的普通 XML) => 不 CONFIRMED
"""
import struct, sys, zlib
from pathlib import Path

BASE = "/root/.openclaw/workspace/sims4-translator"
sys.path.insert(0, BASE)
sys.path.insert(0, BASE + "/scripts")

WW_ANIM_XML = 0x7DF2169C
CLIP = 0x6B20C4F3
ANIM_RCOL = 0xBC4A5044
STBL = 0x220557DA


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


def make_pkg(path, items):
    """items: list[(type, group, inst, body_bytes)]。生成标准 DBPF v2。"""
    count = len(items)
    HEADER = 0x44; PAD = 4; ENTRY = 32
    index_size = PAD + count * ENTRY
    index_offset = HEADER
    data_off = index_offset + index_size
    idx = []
    off = data_off
    for (t, g, inst, d) in items:
        sz = len(d)
        idx.append((t, g, inst, off, sz))
        off += sz
    buf = bytearray(off)
    buf[0:4] = b"DBPF"
    struct.pack_into("<I", buf, 4, 2)
    struct.pack_into("<I", buf, 8, 0)
    struct.pack_into("<I", buf, 0x24, count)
    struct.pack_into("<I", buf, 0x2C, index_size)
    struct.pack_into("<I", buf, 0x40, index_offset)
    p = index_offset
    struct.pack_into("<I", buf, p, 0); p += PAD
    for (t, g, inst, o, sz) in idx:
        struct.pack_into("<I", buf, p, t); p += 4
        struct.pack_into("<I", buf, p, g); p += 4
        struct.pack_into("<I", buf, p, (inst >> 32) & 0xFFFFFFFF); p += 4
        struct.pack_into("<I", buf, p, inst & 0xFFFFFFFF); p += 4
        struct.pack_into("<I", buf, p, o); p += 4
        struct.pack_into("<I", buf, p, sz); p += 4
        struct.pack_into("<I", buf, p, 0); p += 4
        struct.pack_into("<I", buf, p, 0); p += 4
    p = data_off
    for (t, g, inst, o, sz) in idx:
        d = next(d for (t2, g2, i2, d) in items if (t2, g2, i2) == (t, g, inst))
        buf[p:p + sz] = d; p += sz
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(buf))
    return path


WW_XML_V1 = """<?xml version="1.0" encoding="utf-8"?>
<WickedWhimsAnimationPackage>
  <Animation animation_id="creator_animation_001" animation_raw_display_name="Slow Romantic Kiss" animation_author="TestCreatorName" animation_category="Kissing" animation_clip_name="clip_0001">
    <animation_tags><Tag name="romantic" /></animation_tags>
    <animation_locations><Location object="LoveSeat" /></animation_locations>
    <animation_actors_list><Actor slot="0" id="actor_a" /></animation_actors_list>
  </Animation>
  <Animation animation_id="creator_animation_002" animation_raw_display_name="Fast Kiss" animation_author="TestCreatorName" animation_category="Kissing" animation_clip_name="clip_0002">
  </Animation>
</WickedWhimsAnimationPackage>
"""

DANCE_XML = """<?xml version="1.0" encoding="utf-8"?>
<StripClubDanceAnimationPackage>
  <Dance raw_display_name="Pole Dance Basic" dancer_animation_clip_name="dancer_clip_a" dance_type="Pole" dancer_gender="female">
  </Dance>
</StripClubDanceAnimationPackage>
"""

NON_WW_XML = """<?xml version="1.0" encoding="utf-8"?>
<Tuning Name="SomePosePack_Tuning">
  <Pose name="Pose A" clip="pose_clip_1" />
</Tuning>
"""


def main():
    import tempfile, subprocess, os
    from importlib import util

    spec = util.spec_from_file_location("mvp", os.path.join(BASE, "scripts", "ww_animation_mvp_forensic.py"))
    mvp = util.module_from_spec(spec)
    spec.loader.exec_module(mvp)

    fails = []
    def check(n, c, d=""):
        print(("PASS" if c else "FAIL") + f"  {n}" + (f"  {d}" if d else ""))
        if not c: fails.append(n)

    tmp = Path(tempfile.mkdtemp(prefix="ww_mvp_"))
    mods = tmp / "Mods"; mods.mkdir()

    # W1: 真 zlib WW_ANIM_XML + CLIP => CONFIRMED
    ww_xml_body = zlib.compress(WW_XML_V1.encode("utf-8"))
    p1 = mods / "WW_TestCreatorA_Animations.package"
    make_pkg(p1, [
        (WW_ANIM_XML, 0, 0x101, ww_xml_body),
        (CLIP, 0, 0x0A00000000000001, b"\x00" * 64),
        (ANIM_RCOL, 0, 0x0A00000000000001, b"\x00" * 64),
    ])
    d1 = mvp.identify_ww(p1)
    check("W1 zlib XML 解压 + 双证据 => CONFIRMED", d1["identity"] == "CONFIRMED", str(d1.get("schemas")))

    # W2: 只有 CLIP, 无 WW 注册 XML => 不 CONFIRMED
    p2 = mods / "SomePoseAnimations.package"
    make_pkg(p2, [
        (CLIP, 0, 0x0B00000000000001, b"\x00" * 64),
        (ANIM_RCOL, 0, 0x0B00000000000001, b"\x00" * 64),
    ])
    d2 = mvp.identify_ww(p2)
    check("W2 CLIP-only => 不 CONFIRMED", d2["identity"] != "CONFIRMED", d2["identity"])

    # W3: 文件名 WW_xxx 但无正证据 => 不 CONFIRMED
    p3 = mods / "WW_FakePackage.package"
    make_pkg(p3, [
        (STBL, 0x80000000, 0x0100000000000001, build_stbl([(0x11, 0, "some text")])),
    ])
    d3 = mvp.identify_ww(p3)
    check("W3 文件名 WW_ 但无正证据 => 不 CONFIRMED", d3["identity"] != "CONFIRMED", d3["identity"])

    # W4: malformed WW_ANIM_XML (zlib 破损) => fail-closed, 不崩
    bad_zlib = b"\x78\x9c" + b"\xff\x00" * 10  # zlib magic 但 content 破损
    p4 = mods / "WW_Broken_Animations.package"
    make_pkg(p4, [
        (WW_ANIM_XML, 0, 0x102, bad_zlib),
        (CLIP, 0, 0x0C00000000000001, b"\x00" * 64),
    ])
    d4 = mvp.identify_ww(p4)
    # malformed: zlib 解压失败 -> return 原 body -> 判定失败 => schema UNKNOWN, 仍需 CLIP => POSSIBLE (不崩)
    check("W4 malformed zlib => fail-closed 不崩", "schemas" in d4 and d4["schemas"] == [] or (
        d4["schemas"] and all(s == "UNKNOWN_SCHEMA" for s in d4["schemas"])
    ), f"schemas={d4.get('schemas')}")
    check("W4 malformed => 不 CONFIRMED", d4["identity"] != "CONFIRMED", d4["identity"])

    # W5: 非 WW XML (普通 tuning, 无 WW 注册 schema) + CLIP => 不 CONFIRMED
    p5 = mods / "PosePack_Generic.package"
    make_pkg(p5, [
        (WW_ANIM_XML, 0, 0x103, zlib.compress(NON_WW_XML.encode("utf-8"))),
        (CLIP, 0, 0x0D00000000000001, b"\x00" * 64),
    ])
    d5 = mvp.identify_ww(p5)
    check("W5 非 WW XML => 不 CONFIRMED", d5["identity"] != "CONFIRMED", f"schemas={d5.get('schemas')}")

    # 端到端: explicit 三个混合包, 验证 stdout 有 SAMPLE + 总判断行
    e1 = p1
    e2 = mods / "WW_StripClub_Animations.package"
    make_pkg(e2, [
        (WW_ANIM_XML, 0, 0x104, zlib.compress(DANCE_XML.encode("utf-8"))),
        (CLIP, 0, 0x0E00000000000001, b"\x00" * 64),
    ])
    import os as _os
    env = dict(_os.environ)
    env["_MVP_OUT"] = str(tmp)
    r = subprocess.run([sys.executable, _os.path.join(BASE, "scripts", "ww_animation_mvp_forensic.py"),
                        "--mods-root", str(mods), "--explicit", str(p1), str(e2), str(p2)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    out = r.stdout
    check("E2E rc==0", r.returncode == 0, f"rc={r.returncode}\n{out}\nSTDERR:\n{r.stderr}")
    check("E2E SAMPLE 出现", "SAMPLE 1" in out and "SAMPLE 2" in out and "SAMPLE 3" in out, "")
    check("E2E CSV 生成", Path(BASE + "/output/ww_animation_mvp_forensic.csv").exists(), "")
    check("E2E MD 生成", Path(BASE + "/output/ww_animation_mvp_forensic.md").exists(), "")
    check("E2E 总判断行", "DISPLAY_STORAGE=" in out and "DISPLAY_INTERNAL_SEPARATION=" in out
          and "SCHEMA_DIFFERENCE=" in out and "NEXT_STEP=" in out, "")

    print(f"\nHARNESS {'ALL PASS' if not fails else 'FAIL: ' + str(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
