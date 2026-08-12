#!/usr/bin/env python3
"""临时调试 v7: 确认 Sims4 entry=32字节 布局 + 定位 header 索引字段"""
import struct

p = "C:/Users/thela/Documents/Electronic Arts/The Sims 4/Mods/2026.4.28/WWLaserAnimations.package"

with open(p, "rb") as f:
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    data = f.read()

print("文件大小:", size)

# ===== header 完整解析 (0x00-0x40) =====
print("\n=== Header 字段 (每4字节) ===")
for off in range(0, 0x40, 4):
    v = struct.unpack_from("<I", data, off)[0]
    print(f"  0x{off:03X}: 0x{v:08X}")

# ===== 尝试从文件末尾解析索引区 =====
# entry=32, offset@0x10(带压缩高位), size@0x14(带压缩高位)
# 逐个往前解析, 直到 offset 不再平滑, 记录资源类型多样性
print("\n=== 从文件末尾往前解析 (32字节entry) ===")
ENTRY = 32
results = []
pos = size - ENTRY
# 索引起点未知, 向上多取一些(比如200个)看连续性
MAX = 200
found_stbl = 0
types = set()
prev_off = None
for i in range(MAX):
    off = pos - i * ENTRY
    if off < 0:
        break
    e = data[off:off+ENTRY]
    if len(e) < ENTRY:
        break
    t = struct.unpack_from("<I", e, 0)[0]
    g = struct.unpack_from("<I", e, 4)[0]
    hi = struct.unpack_from("<I", e, 8)[0]
    lo = struct.unpack_from("<I", e, 0x0C)[0]
    o = struct.unpack_from("<I", e, 0x10)[0] & 0x7FFFFFFF
    sz = struct.unpack_from("<I", e, 0x14)[0]
    # 记录
    types.add(t)
    results.append((t, g, (hi<<32)|lo, o, sz))
    if t == 0x220557DA:
        found_stbl += 1
    if i < 12:
        print(f"  [{i}] type=0x{t:08X} off=0x{o:X} sz_raw=0x{sz:08X}")

print(f"\n解析 {len(results)} 个索引项, 不同 type 数: {len(types)}")
print("出现的 type 列表:", [f"0x{t:08X}" for t in sorted(types)])
print("其中 STBL(0x220557DA) 数量:", found_stbl)
