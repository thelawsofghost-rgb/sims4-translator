#!/usr/bin/env python3
"""临时调试 v4: 智能探测真实 package 的索引区"""
import struct

p = "C:/Users/thela/Documents/Electronic Arts/The Sims 4/Mods/2026.4.28/WWLaserAnimations.package"

with open(p, "rb") as f:
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    data = f.read()

print("文件大小:", size)

# 假设1: 标准 Sims4 v2 header (0x10=count, 0x14=off, 0x18=size)
count = struct.unpack_from("<I", data, 0x10)[0]
off = struct.unpack_from("<I", data, 0x14)[0]
sz = struct.unpack_from("<I", data, 0x18)[0]
print(f"假设标准v2: count={count}, off={off}, size={sz}")

# 假设2: 索引区通常有 zlib (78 9C) 或 已知偏移
# 搜索常见 Sims4 索引特征: 查找所有看起来像索引区的 0x10 倍数偏移
print("\n扫描可能索引区 (offset 满足 +count*20 在文件内的位置):")
candidates = []
for probe in range(0, min(size, 512*1024), 16):  # 只扫前512KB找索引
    pass

# 更直接: 找第一个非零的 plausible 索引块
# 搜 '78 9c' zlib 出现位置 (压缩索引)
zlib_offsets = []
idx = 0
while True:
    idx = data.find(b'\x78\x9c', idx + 1 if idx else 0)
    if idx == -1 or len(zlib_offsets) > 10:
        break
    zlib_offsets.append(idx)
print("zlib(78 9c)出现位置(前10):", [hex(x) for x in zlib_offsets])

# 假设3: Sims4 索引常以 0x10 开头带 entry 数; 尝试在文件后半找
# 打印文件末尾 64 字节 (索引常在末尾)
print("\n文件末尾 96 字节:")
tail = data[-96:]
for i in range(0, 96, 16):
    chunk = tail[i:i+16]
    hexs = " ".join(f"{b:02X}" for b in chunk)
    print(f"  {hexs}")
