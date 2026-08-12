#!/usr/bin/env python3
"""临时诊断: dump STBL 原始前 64 字节 + 布局字段, 定位真实 STBL 结构"""
import sys, struct
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse
from backend import get_backend

p = sys.argv[1] if len(sys.argv) > 1 else None
if not p:
    print("用法: python dump_stbl_raw.py <package路径>")
    sys.exit(1)

idx, err = safe_parse(p)
if err:
    print(f"解析失败: {err}")
    sys.exit(1)

backend = get_backend("readonly").open(p)
shown = 0
for e in idx.entries:
    if e.type_id != 0x220557DA:
        continue
    data = backend.read_small_resource(e)
    if not data:
        print(f"STBL inst={e.instance_id:016X} 读取失败")
        continue
    print(f"\n=== STBL inst=0x{e.instance_id:016X} size={len(data)} magic={data[0:4]!r} ===")
    print("  前 64 字节 hex:", data[:64].hex())
    if len(data) >= 16:
        print(f"  bytes[4:8]={struct.unpack('<I',data[4:8])[0]:#x} "
              f"bytes[8:12]={struct.unpack('<I',data[8:12])[0]:#x} "
              f"bytes[12:16]={struct.unpack('<I',data[12:16])[0]:#x}")
        print(f"  bytes[16:20]={struct.unpack('<I',data[16:20])[0]:#x} "
              f"bytes[20:24]={struct.unpack('<I',data[20:24])[0]:#x}")
    shown += 1
    if shown >= 4:
        break
