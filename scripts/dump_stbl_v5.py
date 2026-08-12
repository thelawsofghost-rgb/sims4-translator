#!/usr/bin/env python3
"""临时诊断: 精确解析 STBL v5 布局 (entry 格式 + 字符串提取)"""
import sys, struct, zlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse
from backend import get_backend

p = sys.argv[1] if len(sys.argv) > 1 else None
if not p:
    print("用法: python dump_stbl_v5.py <package路径>")
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
    raw = backend.read_small_resource(e)
    if not raw:
        continue
    d = zlib.decompress(raw) if raw[:2] == b"\x78" else raw
    print(f"\n=== STBL inst={e.instance_id:016X} raw={len(raw)}->{len(d)} ===")
    print("  完整 hex:", d.hex())
    # 逐 offset 字段解读
    if len(d) >= 4 and d[:4] == b"STBL":
        ver = struct.unpack("<I", d[4:8])[0]
        print(f"  version={ver}")
        print(f"  u32[8]  = {struct.unpack('<I', d[8:12])[0]}   (entry count?)")
        print(f"  u32[12] = {struct.unpack('<I', d[12:16])[0]}   (reserved?)")
        print(f"  u32[16] = {struct.unpack('<I', d[16:20])[0]}   (0x{struct.unpack('<I',d[16:20])[0]:X})")
        # 尝试 count = u32[8]
        count = struct.unpack("<I", d[8:12])[0]
        print(f"  -- 假设 count@{8} = {count} --")
        pos = 16
        entries = []
        for i in range(min(count, 8)):
            if pos + 12 > len(d):
                break
            h = struct.unpack("<Q", d[pos:pos+8])[0]
            off = struct.unpack("<I", d[pos+8:pos+12])[0]
            entries.append((h, off, off))
            pos += 12
        for i, (h, off, _) in enumerate(entries):
            end = d.find(b"\x00\x00", off) if off < len(d) else -1
            txt = ""
            if 0 <= off < len(d):
                lim = d[off:off+120]
                txt = lim.decode("utf-16-le", errors="ignore").rstrip("\x00")
            print(f"    entry[{i}] hash=0x{h:016X} off={off} -> {txt!r}")
    shown += 1
    if shown >= 3:
        break
