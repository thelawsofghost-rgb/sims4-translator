#!/usr/bin/env python3
"""临时诊断: dump 能解析的动画包的完整 type 列表, 用于登记资源类型"""
import sys, struct
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse

p = sys.argv[1] if len(sys.argv) > 1 else None
if not p:
    print("用法: python dump_types.py <package路径>")
    sys.exit(1)

idx, err = safe_parse(p)
if err:
    print(f"解析失败: {err}")
    sys.exit(1)

from collections import Counter
cnt = Counter(e.type_id for e in idx.entries)
print(f"文件: {Path(p).name}")
print(f"entry 总数: {len(idx.entries)}")
print(f"不同 type 数: {len(cnt)}")
print("\n各 type 出现次数 (按次数排序):")
for t, n in cnt.most_common():
    print(f"  0x{t:08X}  x{n}")

print("\n前 15 个 entry 明细:")
for e in idx.entries[:15]:
    print(f"  type=0x{e.type_id:08X} group=0x{e.group_id:08X} "
          f"inst_hi=0x{e.instance_id_high:08X} inst_lo=0x{e.instance_id_low:08X} "
          f"off=0x{e.offset:X} size={e.size} comp={e.is_compressed}")
