#!/usr/bin/env python3
"""临时调试 v9: dump 完整索引区原始字节, 人工确认 entry 真实布局"""
import struct

p = "C:/Users/thela/Documents/Electronic Arts/The Sims 4/Mods/2026.4.28/WWLaserAnimations.package"

with open(p, "rb") as f:
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    data = f.read()

idx_off = struct.unpack_from("<I", data, 0x40)[0]
idx_size = struct.unpack_from("<I", data, 0x2C)[0]
print(f"idx_off=0x{idx_off:X} ({idx_off}), idx_size=0x{idx_size:X} ({idx_size})")

# dump 索引区前 128 字节原始
print("\n=== 索引区前 160 字节原始 hex ===")
seg = data[idx_off:idx_off+160]
for i in range(0, min(len(seg), 160), 16):
    chunk = seg[i:i+16]
    hexs = " ".join(f"{b:02X}" for b in chunk)
    asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
    print(f"  +0x{i:03X}: {hexs:<48} {asc}")

# 尝试不同 entry 大小解析, 看哪个 type 变化多样
print("\n=== 尝试 entry 大小 = 24 / 28 / 32 ===")
for ENTRY in (24, 28, 32):
    types = set()
    first_types = []
    for i in range(20):
        off = idx_off + i * ENTRY
        if off + ENTRY > size:
            break
        t = struct.unpack_from("<I", data, off)[0]
        types.add(t)
        if i < 15:
            first_types.append(f"0x{t:08X}")
    print(f"entry={ENTRY}: 前15个 type = {first_types}")
    print(f"      不同type数(20项内): {len(types)}")
