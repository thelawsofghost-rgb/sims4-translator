#!/usr/bin/env python3
"""临时诊断: zlib 解压 STBL 资源, 验证解压后是否为合法 STBL"""
import sys, struct, zlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse
from backend import get_backend

p = sys.argv[1] if len(sys.argv) > 1 else None
if not p:
    print("用法: python dump_stbl_zlib.py <package路径>")
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
        continue
    raw = data
    # 尝试 zlib / gzip / plain
    dec = None
    meth = None
    if raw[:2] == b"\x78\x9c" or raw[:2] == b"\x78\x01" or raw[:2] == b"\x78\xda":
        try:
            dec = zlib.decompress(raw)
            meth = f"zlib(len {len(raw)}->{len(dec)})"
        except Exception as ex:
            print(f"  zlib 失败: {ex}")
    if dec is None:
        # 尝试找 STBL magic 偏移 (可能有前置头)
        idx2 = raw.find(b"STBL")
        if idx2 >= 0:
            try:
                dec = zlib.decompress(raw[idx2+4:])
                meth = f"STBL@{idx2}+zlib"
            except Exception:
                dec = raw[idx2:]
                meth = f"STBL@{idx2} 明文"
    if dec is None:
        dec, meth = raw, "原样"

    print(f"\n=== STBL inst=0x{e.instance_id:016X} 压缩size={len(raw)} -> 解压方式={meth} 解码size={len(dec)} ===")
    if dec[:4] == b"STBL" and len(dec) >= 16:
        ver = struct.unpack("<I", dec[4:8])[0]
        # 尝试几个可能的 count 位置
        for pos, label in ((12, "off12"), (16, "off16"), (8, "off8")):
            if pos + 4 <= len(dec):
                c = struct.unpack("<I", dec[pos:pos+4])[0]
                print(f"  count@{pos}({label}) = {c}")
        print("  前 48 字节(解压后) hex:", dec[:48].hex())
    else:
        print("  解压后前 48 字节 hex:", dec[:48].hex())
        # 找可读文本
        for enc in ("utf-16-le", "utf-8"):
            t = dec.decode(enc, errors="ignore")
            ptext = "".join(ch if ch.isprintable() else "." for ch in t[:200])
            if any(c.isalpha() for c in ptext):
                print(f"  [{enc}] {ptext}")
                break
    shown += 1
    if shown >= 4:
        break
