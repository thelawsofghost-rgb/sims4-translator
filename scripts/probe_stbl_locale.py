#!/usr/bin/env python3
"""Phase 3A 只读取证: 扫指定 .package 内所有 STBL 的 locale(最高字节) + keyHash 统计。
不写任何文件。用法: python scripts/probe_stbl_locale.py <package_path...>
"""
import sys, zlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse

def locale_of(inst):
    return (inst >> 56) & 0xFF

def parse_stbl(d):
    if not d: return None
    if d[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        try: d = zlib.decompress(d)
        except Exception: return None
    off = d.find(b"STBL")
    if off < 0: return None
    b = d[off:]
    if len(b) < 16: return None
    if int.from_bytes(b[4:6], "little") != 5: return None
    n = int.from_bytes(b[8:16], "little"); o = 18; out = []
    for _ in range(n):
        if o + 7 > len(b): break
        kh = int.from_bytes(b[o:o+4], "little")
        flags = b[o+4]; ln = int.from_bytes(b[o+5:o+7], "little"); o += 7
        if o + ln > len(b): break
        txt = b[o:o+ln].decode("utf-8", "replace"); out.append((kh, flags, txt)); o += ln
    return out

for p in sys.argv[1:]:
    pp = Path(p)
    if not pp.exists():
        print(f"[缺] {p}"); continue
    idx, err = safe_parse(str(pp))
    if err or idx is None:
        print(f"[解析失败] {p}: {err}"); continue
    from backend import get_backend
    bk = get_backend("readonly").open(str(pp))
    stbls = [e for e in idx.entries if e.type_id == 0x220557DA]
    print(f"\n=== {pp.name} : STBL 资源 {len(stbls)} 个 ===")
    seen = {}
    for e in stbls:
        inst = getattr(e, "instance_id", 0)
        lo = locale_of(inst)
        data = bk.read_small_resource(e, max_bytes=2*1024*1024)
        parsed = parse_stbl(data)
        nk = len(parsed) if parsed else 0
        sam = ""
        if parsed:
            first = next(iter(parsed))
            sam = repr(first[2][:40])
        seen.setdefault(lo, []).append((inst, nk, sam))
    for lo in sorted(seen):
        for inst, nk, sam in seen[lo]:
            print(f"  locale=0x{lo:02X}  inst={inst:016X}  keys={nk}  e.g. {sam}")
    bk.close()
print("\n取证完成(只读, 未写任何文件)")
