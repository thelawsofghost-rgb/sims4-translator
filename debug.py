#!/usr/bin/env python3
"""临时调试 v5: 从文件末尾解析 Sims 4 索引区 (32字节 entry)"""
import struct

p = "C:/Users/thela/Documents/Electronic Arts/The Sims 4/Mods/2026.4.28/WWLaserAnimations.package"

with open(p, "rb") as f:
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    data = f.read()

print("文件大小:", size)

# Sims 4 resource index entry = 32 字节:
# type(4) group(4) inst_hi(4) inst_lo(4) offset(4) size(4) flags(4) reserved(4)
# offset 高4位 = 压缩类型 (0=无,1=deflate/zlib,2=?, etc)

# 从末尾取最后几个 entry 解析
tail = data[-192:]  # 6 个 entry
print("\n末尾解析 (每次32字节):")
for i in range(0, 192, 32):
    e = tail[i:i+32]
    if len(e) < 32:
        break
    t, g, hi, lo, off, sz, fl, rs = struct.unpack("<IIIIIIII", e)
    print(f"  type=0x{t:08X} group=0x{g:08X} inst=0x{(hi<<32)|lo:016X} offset=0x{off&0x7FFFFFFF:X} size={sz} flags=0x{fl:08X}")
