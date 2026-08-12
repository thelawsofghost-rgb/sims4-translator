#!/usr/bin/env python3
"""临时调试 v8: 验证 Sims4 header 索引字段 (0x2C=size, 0x40=offset)"""
import struct

p = "C:/Users/thela/Documents/Electronic Arts/The Sims 4/Mods/2026.4.28/WWLaserAnimations.package"

with open(p, "rb") as f:
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    data = f.read()

print("文件大小:", size)

# 假设: header 里索引字段在 0x2C (index size) 和 0x40 (index offset)
idx_size = struct.unpack_from("<I", data, 0x2C)[0]
idx_off = struct.unpack_from("<I", data, 0x40)[0]
print(f"假设: idx_size@0x2C=0x{idx_size:X} ({idx_size}), idx_off@0x40=0x{idx_off:X} ({idx_off})")
print(f"验证: idx_off + idx_size = {idx_off + idx_size} vs 文件大小 {size}")
print(f"  → 索引区起始到文件末尾: {'吻合!' if idx_off + idx_size == size else '不吻合'}")

if idx_off + idx_size == size:
    count = idx_size // 32
    print(f"\n索引项数(count estimate): {idx_size}/32 = {count}")
    print("\n=== 从 idx_off 开始解析全部索引 ===")
    types = set()
    n = 0
    for i in range(count * 2):  # 多看一些
        off = idx_off + i * 32
        if off + 32 > size:
            break
        e = data[off:off+32]
        t = struct.unpack_from("<I", e, 0)[0]
        o = struct.unpack_from("<I", e, 0x10)[0] & 0x7FFFFFFF
        sz = struct.unpack_from("<I", e, 0x14)[0] & 0x7FFFFFFF
        types.add(t)
        n += 1
        if i < 45:
            print(f"  [{i}] type=0x{t:08X} off=0x{o:X} size={sz}")
    print(f"\n共解析 {n} 项, 不同 type: {len(types)}")
