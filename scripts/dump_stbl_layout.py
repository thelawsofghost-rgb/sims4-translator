#!/usr/bin/env python3
"""临时取证: 打印指定包内所有 STBL 的完整 64-bit id + 各字节拆解, 用于确定真 locale 布局。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse
from backend import get_backend

p = Path(sys.argv[1] if len(sys.argv) > 1 else "")
if not p.exists():
    print("用法: dump_stbl_layout.py <package路径>  (可给 t0nischwartz 包)")
    sys.exit(1)

idx, err = safe_parse(str(p))
if err or not idx:
    print("解析失败:", err); sys.exit(1)
print(f"包: {p.name}")
n = 0
for e in idx.entries:
    if e.type_id == 0x220557DA:
        inst = e.instance_id
        b = [ (inst >> (8*i)) & 0xFF for i in range(8) ]  # b[0]=LSB
        print(f"  inst=0x{inst:016X}  bytes(LSB→MSB)=[{' '.join(f'{x:02X}' for x in b)}]")
        n += 1
        if n >= 10:
            print("  ...(截断, 前10个)")
            break
if n == 0:
    print("  无 STBL")
