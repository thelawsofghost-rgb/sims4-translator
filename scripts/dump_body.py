#!/usr/bin/env python3
"""临时诊断: dump 指定 type 的 body 内容 (前 N 字节), 确认资源类型映射"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse
from backend import get_backend

p = sys.argv[1] if len(sys.argv) > 1 else None
want = int(sys.argv[2], 0) if len(sys.argv) > 2 else None
if not p:
    print("用法: python dump_body.py <package路径> [type_id_hex, 缺省则全部非STBL]")
    sys.exit(1)

idx, err = safe_parse(p)
if err:
    print(f"解析失败: {err}")
    sys.exit(1)

backend = get_backend("readonly").open(p)
seen = 0
for e in idx.entries:
    if want is not None and e.type_id != want:
        continue
    if want is None and e.type_id == 0x220557DA:  # 跳过 STBL 正文
        continue
    data = backend.read_small_resource(e, max_bytes=2048)
    if not data:
        continue
    print(f"\n=== type=0x{e.type_id:08X} inst=0x{e.instance_id:016X} size={e.size} ===")
    # 尝试 utf-16-le (STBL/XML 常为此) 和 utf-8 打印可读文本
    for enc in ("utf-16-le", "utf-8"):
        try:
            txt = data.decode(enc, errors="ignore")
        except Exception:
            continue
        printable = "".join(ch if ch.isprintable() or ch in "\n\t" else "·" for ch in txt)
        # 只打印有意义的片段
        if any(c.isalpha() for c in printable):
            print(f"  [{enc}] {printable[:400]}")
            break
    seen += 1
    if seen >= 12:
        print("\n... (截断, 只看前 12 个) ...")
        break
