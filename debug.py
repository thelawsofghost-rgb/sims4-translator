#!/usr/bin/env python3
"""临时调试 v3: dump 真实 package 前 256 字节, 定位索引区"""
p = "C:/Users/thela/Documents/Electronic Arts/The Sims 4/Mods/2026.4.28/WWLaserAnimations.package"

with open(p, "rb") as f:
    f.seek(0)
    raw = f.read(256)
    print("=== 前 256 字节 (每行16字节) ===")
    for off in range(0, 256, 16):
        chunk = raw[off:off+16]
        hexs = " ".join(f"{b:02X}" for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"0x{off:03X}: {hexs:<48} {asc}")
