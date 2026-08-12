#!/usr/bin/env python3
"""临时调试 v6: 钉死 Sims4 索引 entry 的真实 32 字节布局"""
import struct

p = "C:/Users/thela/Documents/Electronic Arts/The Sims 4/Mods/2026.4.28/WWLaserAnimations.package"

with open(p, "rb") as f:
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    data = f.read()

# 分析文件末尾一个完整 entry 的每个字节位置
# 取末尾一个 entry (从 -32 开始)
print("文件大小:", size)
print("\n末尾 entry 原始 32 字节:")
tail = data[-32:]
for i in range(0, 32, 4):
    chunk = tail[i:i+4]
    val = struct.unpack("<I", chunk)[0]
    print(f"  0x{i:02X}: {chunk.hex()}  => uint32 = 0x{val:08X}")

# 尝试: 这可能是 24字节 entry (Sims4 常见) 而不是 32
print("\n假设 entry=24字节, 从末尾解析几个:")
# 找末尾索引起始: 先假设索引区紧邻文件末尾, 尝试 24 和 32
for ENTRY in (24, 32, 16):
    print(f"\n--- 尝试 entry={ENTRY} ---")
    count_in_tail = 12  # 试12个
    for i in range(0, min(count_in_tail*ENTRY, 192), ENTRY):
        e = data[-(i+ENTRY):-(i) if i>0 else None]
        if len(e) < ENTRY:
            break
        # type 在前4字节
        t = struct.unpack_from("<I", e, 0)[0]
        # 判断是否像有效 type id (Sims4 类型常 < 0x10000000)
        if t < 0x10000000 and t != 0:
            g = struct.unpack_from("<I", e, 4)[0]
            print(f"  type=0x{t:08X} group=0x{g:08X}")
