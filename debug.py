#!/usr/bin/env python3
"""临时调试: 诊断 DBPF 解析为何对真实 package 全部失败"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from dbpf_fast import FastIndexReader

p = "C:/Users/thela/Documents/Electronic Arts/The Sims 4/Mods/2026.4.28/WWLaserAnimations.package"
with open(p, "rb") as f:
    f.seek(0, 2)
    size = f.tell()
    print("文件大小:", size)
    header = f.read(32)
    print("magic:", header[0:4])
    print("major:", int.from_bytes(header[4:8], "little"))
    print("minor:", int.from_bytes(header[8:12], "little"))
    print("count@0x10:", int.from_bytes(header[0x10:0x14], "little"))
    print("idx_off@0x14:", int.from_bytes(header[0x14:0x18], "little"))
    print("idx_size@0x18:", int.from_bytes(header[0x18:0x1C], "little"))
    try:
        idx = FastIndexReader(f, size).read_index()
        print("解析成功! entries:", len(idx.entries))
    except Exception:
        import traceback
        traceback.print_exc()
