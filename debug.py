#!/usr/bin/env python3
"""临时调试 v10: 定论 entry 大小 (用 header count + index 区全覆盖验证)"""
import struct

p = "C:/Users/thela/Documents/Electronic Arts/The Sims 4/Mods/2026.4.28/WWLaserAnimations.package"

with open(p, "rb") as f:
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    data = f.read()

# header 候选字段
c24 = struct.unpack_from("<I", data, 0x24)[0]
c2c = struct.unpack_from("<I", data, 0x2C)[0]
c40 = struct.unpack_from("<I", data, 0x40)[0]
c3c = struct.unpack_from("<I", data, 0x3C)[0]
print(f"header: 0x24={c24}, 0x2C={c2c}(idx_size), 0x40={c40}(idx_off), 0x3C={c3c}")

idx_off = c40
idx_size = c2c
print(f"idx_off=0x{idx_off:X}, idx_size=0x{idx_size:X}, index区end=偏移+4+{idx_size}")

# 尝试: 索引起点 = idx_off+4 (跳过4字节padding), 从那里连续解析
base = idx_off + 4
remain = idx_off + idx_size - base
print(f"data区(扣除4字节padding)可用字节: {remain}")

for ENTRY in (24, 28, 32):
    n = remain // ENTRY
    leftover = remain % ENTRY
    types = set()
    ok_offsets = True
    prev_end = 0
    first = []
    for i in range(n):
        off = base + i * ENTRY
        e = data[off:off+ENTRY]
        t = struct.unpack_from("<I", e, 0)[0]
        inst_hi = struct.unpack_from("<I", e, 8)[0]
        o = struct.unpack_from("<I", e, 0x10)[0] & 0x7FFFFFFF
        sz = struct.unpack_from("<I", e, 0x14)[0] & 0x7FFFFFFF
        types.add(t)
        if i < 6:
            first.append(f"t=0x{t:08X} off=0x{o:X} sz={sz}")
    # 连续性检查: 大部分 type 应 < 0x10000000 (有效ID) 且不重复过多
    valid_ratio = sum(1 for i in range(n) if 0 < struct.unpack_from("<I", data, base+i*ENTRY)[0] < 0x10000000) / max(n,1)
    print(f"\nentry={ENTRY}: n={n} (leftover={leftover}) 不同type={len(types)} 有效type比例={valid_ratio:.2f}")
    print(f"  前6: {first}")
