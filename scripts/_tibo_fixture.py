#!/usr/bin/env python3
"""构建一个忠实 pose-pack fixture 包: TUNING_XML(PosePackInstance) + STBL
用于验证 audit_tibo_exact_map.py 的"结构证据 → TRANSLATE/KEEP/UNMAPPED"判定逻辑。
STBL keyHash 用 s4pi FNV32 (FNV-1a 32bit, prime 0x1000193, offset 0x811C9DC5) of lowercased ASCII。
"""
import struct, zlib, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from dbpf_fast import DBPFIndex

STBL_TID = 0x220557DA
XML_TID = 0x0333406C  # TUNING_XML
CHS_INST = 0x014EACCF17C8B091   # locale 0x01 CHS

def fnv32(s):
    h = 0x811C9DC5
    p = 0x01000193
    for b in s.lower().encode("ascii"):
        h = ((h * p) & 0xFFFFFFFF) ^ b
    return h & 0xFFFFFFFF

def build_stbl(entries):
    # entries: list[(key_hash_int, flags, text)]
    out = bytearray()
    out += b"STBL"
    out += struct.pack("<H", 5)          # version
    out += bytes([0])                     # isCompressed
    out += struct.pack("<Q", len(entries))  # numEntries
    out += struct.pack("<H", 0)          # reserved
    # stringLength = sum of (len(utf8)+1) per entry; write placeholder, backfill
    bodies = []
    tot = 0
    for (kh, fl, txt) in entries:
        b = txt.encode("utf-8")
        bodies.append((kh, fl, b))
        tot += 1 + len(b)
    out += struct.pack("<I", tot)
    for (kh, fl, b) in bodies:
        out += struct.pack("<I", kh)
        out += bytes([fl])
        out += struct.pack("<H", len(b))
        out += b
    return bytes(out)

def build_xml(pose_name, display_hashes, author="Tibo131", pack="Tibo131 Standing Pose Pack #2"):
    # 仿 classifier.py 记录的真实结构
    lines = []
    lines.append('<I c="PosePackInstance" i="poseplayer" m="...">')
    lines.append(f'  <T n="s4s_mod_type">POSE_PACK</T>')
    lines.append(f'  <T n="creator">Tibo131</T>')
    lines.append(f'  <T n="pack_title">{pack}</T>')
    lines.append('  <L n="pose_list">')
    for i, (pn, dh) in enumerate(zip(pose_name, display_hashes)):
        lines.append('    <U>')
        lines.append(f'      <T n="pose_name">{pn}</T>')
        if dh is not None:
            lines.append(f'      <T n="pose_display_name">0x{dh:08X}</T>')
        lines.append('    </U>')
    lines.append('  </L>')
    lines.append('</I>')
    return "\n".join(lines).encode("utf-8")

def make_pkg(path, stbl_entries, xml_payload):
    # entries: list of (type, group, inst, data)
    data_items = [
        (XML_TID, 0, 0x0000000000000001, xml_payload),
        (STBL_TID, 0x80000000, CHS_INST, build_stbl(stbl_entries)),
    ]
    count = len(data_items)
    # DBPF v2 布局 (dbpf_fast 实测):
    #   header = 0x44(68)B; index 从 index_offset 起有 4B padding, entry 32B/个
    #   关键字段: entry_count@0x24, index_size@0x2C, index_offset@0x40
    HEADER = 0x44
    PAD = 4
    ENTRY = 32
    index_size = PAD + count * ENTRY
    index_offset = HEADER
    data_off = index_offset + index_size
    # 计算偏移
    idx = []
    off = data_off
    for (t, g, inst, d) in data_items:
        sz = len(d)
        idx.append((t, g, inst, off, sz))
        off += sz
    total = off
    buf = bytearray(total)
    # header
    buf[0:4] = b"DBPF"
    struct.pack_into("<I", buf, 4, 2)      # major
    struct.pack_into("<I", buf, 8, 0)      # minor
    struct.pack_into("<I", buf, 0x24, count)     # entry_count
    struct.pack_into("<I", buf, 0x2C, index_size) # index_size
    struct.pack_into("<I", buf, 0x40, index_offset) # index_offset
    # 索引区
    p = index_offset
    struct.pack_into("<I", buf, p, 0)  # padding
    p += PAD
    for (t, g, inst, o, sz) in idx:
        struct.pack_into("<I", buf, p, t); p += 4
        struct.pack_into("<I", buf, p, g); p += 4
        struct.pack_into("<I", buf, p, (inst >> 32) & 0xFFFFFFFF); p += 4
        struct.pack_into("<I", buf, p, inst & 0xFFFFFFFF); p += 4
        struct.pack_into("<I", buf, p, o); p += 4
        struct.pack_into("<I", buf, p, sz); p += 4
        struct.pack_into("<I", buf, p, 0); p += 4  # flags
        struct.pack_into("<I", buf, p, 0); p += 4  # reserved
    # data
    p = data_off
    for (t, g, inst, o, sz) in idx:
        for (t2, g2, inst2, d) in data_items:
            if (t2, g2, inst2) == (t, g, inst):
                buf[p:p + sz] = d
                p += sz
                break
    open(path, "wb").write(bytes(buf))
    return path

def main():
    # STBL keys: author 名, pack title, 3 个 pose display names
    author = "Tibo131"
    pack = "Tibo131 Standing Pose Pack #2"
    poses = ["Pose 1", "Pose 2", "Pose 3"]
    displays = ["Left", "Right", "Kiss"]   # 玩家可见
    kh_author = fnv32(author)
    kh_pack = fnv32(pack)
    kh_poses = [fnv32(d) for d in displays]
    # STBL 里额外放一个无结构引用的孤立 key
    kh_lonely = fnv32("J")
    stbl_entries = [
        (kh_author, 0, author),
        (kh_pack, 0, pack),
        (kh_poses[0], 0, displays[0]),
        (kh_poses[1], 0, displays[1]),
        (kh_poses[2], 0, displays[2]),
        (kh_lonely, 0, "J"),
    ]
    xml = build_xml(poses, kh_poses)
    out = "/tmp/tibo_fixture.package"
    make_pkg(out, stbl_entries, xml)
    print("fixture wrote", out)
    print("kh_author", hex(kh_author), "| kh_pack", hex(kh_pack))
    print("kh_poses", [hex(x) for x in kh_poses], "| kh_lonely", hex(kh_lonely))

if __name__ == "__main__":
    main()
