#!/usr/bin/env python3
"""本地验证: 按实测 Sims 4 布局构造包, 确认 FastIndexReader 能正确解析"""
import struct, sys, tempfile, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dbpf_fast import FastIndexReader, safe_parse, UnsupportedDBPFError

def build_pkg(count=41, entry=32, pad=4):
    """构造符合实测布局的 package: header 0x24=count, 0x2C=size, 0x40=offset, 32字节entry, 4字节padding"""
    body_size = count * 100
    idx_size = pad + count * entry
    idx_off = 68 + body_size
    total = idx_off + idx_size

    buf = bytearray(total)
    buf[0:4] = b"DBPF"
    struct.pack_into("<I", buf, 0x04, 2)      # major
    struct.pack_into("<I", buf, 0x08, 1)      # minor
    struct.pack_into("<I", buf, 0x24, count)  # entry count
    struct.pack_into("<I", buf, 0x2C, idx_size)  # index size
    struct.pack_into("<I", buf, 0x3C, 3)      # compression flag
    struct.pack_into("<I", buf, 0x40, idx_off)   # index offset
    for i in range(count):
        struct.pack_into("<I", buf, 68 + i * 100, 0x11110000 + i)  # fake body data
    struct.pack_into("<I", buf, idx_off, 0)  # 4 byte padding
    for i in range(count):
        eo = idx_off + pad + i * entry
        struct.pack_into("<I", buf, eo + 0x00, 0x220557DA if i % 2 == 0 else 0x0000023D)
        struct.pack_into("<I", buf, eo + 0x04, 0)          # group
        struct.pack_into("<I", buf, eo + 0x08, 0x12345678) # inst_hi
        struct.pack_into("<I", buf, eo + 0x0C, i)          # inst_lo
        o = 68 + i * 100
        if i % 3 == 0:
            o |= 0x80000000
        struct.pack_into("<I", buf, eo + 0x10, o)
        struct.pack_into("<I", buf, eo + 0x14, 100)         # size
        struct.pack_into("<I", buf, eo + 0x18, 0)
        struct.pack_into("<I", buf, eo + 0x1C, 0)
    return bytes(buf)


def main():
    pkg = build_pkg()
    with tempfile.NamedTemporaryFile(suffix=".package", delete=False) as tf:
        tf.write(pkg)
        path = tf.name

    idx, err = safe_parse(path)
    print("safe_parse err:", err)
    assert err is None, f"解析失败: {err}"
    assert len(idx.entries) == 41, f"期望 41, 得到 {len(idx.entries)}"
    types = {e.type_id for e in idx.entries}
    print("类型集合:", [f"0x{t:08X}" for t in sorted(types)])
    assert 0x220557DA in types and 0x0000023D in types

    e0 = idx.entries[0]
    print(f"entry0: type=0x{e0.type_id:08X} offset=0x{e0.offset:X} size={e0.size} inst=0x{e0.instance_id:016X} compressed={e0.is_compressed}")
    assert e0.offset == 68, f"offset 应 0x40, 得 0x{e0.offset:X}"
    assert e0.size == 100
    assert e0.is_compressed  # i=0, 0%3==0 压缩
    assert e0.instance_id == (0x12345678 << 32) | 0

    c = [e for e in idx.entries if e.is_compressed]
    print("压缩条目数:", len(c))
    assert len(c) == 14  # i%3==0 的 0,3,6,...,39 = 14 个

    os.unlink(path)
    print("\n✅ 全部断言通过 — 新布局解析正确!")


if __name__ == "__main__":
    main()
