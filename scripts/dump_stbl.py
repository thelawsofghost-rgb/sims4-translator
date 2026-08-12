#!/usr/bin/env python3
"""临时诊断: dump STBL 资源里的实际文本"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse
from backend import get_backend
from text_extractor import extract_stbl_strings

p = sys.argv[1] if len(sys.argv) > 1 else None
if not p:
    print("用法: python dump_stbl.py <package路径>")
    sys.exit(1)

idx, err = safe_parse(p)
if err:
    print(f"解析失败: {err}")
    sys.exit(1)

backend = get_backend("readonly").open(p)
shown = 0
for e in idx.entries:
    if e.type_id != 0x220557DA:  # STBL
        continue
    data = backend.read_small_resource(e)
    if not data:
        continue
    strings = extract_stbl_strings(data)
    print(f"\n=== STBL inst=0x{e.instance_id:016X} size={e.size} 提取到 {len(strings)} 条 ===")
    for sh, txt in strings[:20]:
        t = txt if len(txt) <= 120 else txt[:117] + "..."
        print(f"  [{sh:#x}] {t!r}")
    if len(strings) > 20:
        print(f"  ... 其余 {len(strings)-20} 条省略")
    shown += 1
    if shown >= 6:
        print("\n... (截断, 只看前 6 个 STBL) ...")
        break
if shown == 0:
    print("未找到可解析的 STBL 资源")
