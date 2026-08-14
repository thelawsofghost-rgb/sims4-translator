#!/usr/bin/env python3
"""Phase 3B 诊断: 原包 vs 输出包 逐字节/逐字段对比, 定位 loader 拒绝点。

用法:
  python scripts/cmp_pkg.py <original.package> <output.package>

对比项 (全部逐字节):
  1. header 0x44 每个字段 (magic/major/minor/flags/reserved/count/index_size/
     reserved/comp_flag/index_offset)
  2. index 区: count 顺序逐条 entry 的 32 字节 (type/group/inst/offset/size/flags/reserved)
     - 报告每条 entry 的 offset/size 及与原始值的 delta
     - 报告每条 entry 与原始条目的"identity 是否对齐"
  3. index_padding (index 区前 4 字节)
  4. body 区: 每个 resource 的 body 原始字节区间是否一致 (对未编辑 resource)
  5. 文件整体字节 diff: 报告所有差异区间 (前 40 处)

输出 [PASS]/[DIFF] 逐条, 便于定位。
"""
import struct, sys

def unpack(fmt, b, off, msg=""):
    try:
        return struct.unpack(fmt, b[off:off+struct.calcsize(fmt)])
    except Exception as e:
        return (f"<err:{e}>",)

def load(p):
    with open(p, "rb") as f:
        return f.read()

def parse_index(raw):
    cnt = struct.unpack("<I", raw[0x24:0x28])[0]
    io  = struct.unpack("<I", raw[0x40:0x44])[0]
    isz = struct.unpack("<I", raw[0x2C:0x30])[0]
    # index 区 = 索引 padding(4) + cnt*32
    idx_abs = io
    entries = []
    for i in range(cnt):
        base = idx_abs + 4 + i*32
        e = bytearray(raw[base:base+32])
        if len(e) < 32:
            e += b"\x00"*(32-len(e))
        tid,gid,hi,lo,off,sz,fl,rs = struct.unpack("<IIIIIIII", e)
        entries.append(dict(i=i, e=bytes(e), tid=tid,gid=gid,inst=(hi<<32)|lo,
                            off_abs=off&0x7fffffff, sz_abs=sz&0x7fffffff,
                            comp_off=off>>31, comp_sz=sz>>31, fl=fl, rs=rs))
    return cnt, io, isz, entries

def main():
    if len(sys.argv) != 3:
        print("用法: python scripts/cmp_pkg.py <original.package> <output.package>")
        return 1
    o = load(sys.argv[1]); n = load(sys.argv[2])
    any_diff = False
    def chk(label, ok, detail=""):
        nonlocal any_diff
        print(f"  [{'PASS' if ok else 'DIFF'}] {label} {detail}")
        if not ok: any_diff = True

    print("===== 1. HEADER (0x00..0x43) =====")
    hdr_fields = [
        ("magic", 0, 4, "4s"), ("major_toc", 4, 4, "<I"), ("minor", 8, 4, "<I"),
        ("flags@0C", 0x0C, 4, "<I"), ("reserved@10", 0x10, 12, "12s"),
        ("reserved@1C", 0x1C, 4, "4s"), ("reserved@20", 0x20, 4, "4s"),
        ("count@24", 0x24, 4, "<I"), ("first_type@28", 0x28, 4, "4s"),
        ("index_size@2C", 0x2C, 4, "<I"), ("reserved@30", 0x30, 12, "12s"),
        ("comp_flag@3C", 0x3C, 4, "<I"), ("index_offset@40", 0x40, 4, "<I"),
    ]
    for name, off, sz, fmt in hdr_fields:
        a = unpack(fmt, o, off)[0]; b = unpack(fmt, n, off)[0]
        chk(name, a == b, f"原={a} 新={b}")
    oc, oio, oisz, oe = parse_index(o)
    nc, nio, nisz, ne = parse_index(n)
    print(f"  [-- ] file_size: 原={len(o)} 新={len(n)} (delta {len(n)-len(o)})")
    chk("resource count", oc == nc, f"原={oc} 新={nc}")
    chk("index_size field", oisz == nisz, f"原=0x{oisz:08X} 新=0x{nisz:08X}")
    print(f"  [-- ] index_offset: 原={oio} 新={nio} (delta {nio-oio}) — 若仅因 body 缩短而减小属正常")

    print("\n===== 2. INDEX entries (按原包顺序逐条对齐) =====")
    # 按 inst+type 作为 identity 对齐 (有些实现会重排 index, 但 v3 不重排)
    max_c = max(oc, nc)
    for i in range(max_c):
        a = oe[i] if i < oc else None
        b = ne[i] if i < nc else None
        if a is None or b is None:
            chk(f"entry[{i}]", False, f"数量不符 (原{oc} 新{nc}) 出现在一侧")
            continue
        ident_a = (a["tid"], a["gid"], a["inst"])
        ident_b = (b["tid"], b["gid"], b["inst"])
        idok = ident_a == ident_b
        off_ok = a["off_abs"] == b["off_abs"]
        sz_ok  = a["sz_abs"] == b["sz_abs"]
        fl_ok  = a["fl"] == b["fl"] and a["rs"] == b["rs"]
        comp_ok = a["comp_off"] == b["comp_off"] and a["comp_sz"] == b["comp_sz"]
        detail = (f"inst=0x{a['inst']:016X} tid=0x{a['tid']:08X} "
                  f"off {a['off_abs']}->{b['off_abs']} "
                  f"sz {a['sz_abs']}->{b['sz_abs']} "
                  f"comp_off {a['comp_off']}->{b['comp_off']} "
                  f"flags 0x{a['fl']:08X}->0x{b['fl']:08X} rs 0x{a['rs']:08X}->0x{b['rs']:08X}")
        ok = idok and off_ok and sz_ok and fl_ok and comp_ok
        chk(f"entry[{i}]", ok, detail)

    print("\n===== 3. INDEX 顺序 / padding =====")
    oid = [(e["tid"],e["gid"],e["inst"]) for e in oe]
    nid = [(e["tid"],e["gid"],e["inst"]) for e in ne]
    chk("index 顺序一致", oid == nid,
        "原顺序="+",".join(f"0x{i[2]:016X}" for i in oid) +
        " 新顺序="+",".join(f"0x{i[2]:016X}" for i in nid))
    # index 区 padding (index_offset 起的前 4 字节)
    if len(o) >= oio+4 and len(n) >= nio+4:
        chk("index padding(4B)", o[oio:oio+4] == n[nio:nio+4],
            f"原={o[oio:oio+4].hex()} 新={n[nio:nio+4].hex()}")
    else:
        chk("index padding", False, "越界无法读")

    print("\n===== 4. BODY bytes (未编辑 resource 原字节区间) =====")
    for i in range(oc):
        a = oe[i]
        # 在原包中提取 body
        ob = o[a["off_abs"]:a["off_abs"]+a["sz_abs"]]
        # 在新包中同 identity 找
        match = next((x for x in ne if (x["tid"],x["gid"],x["inst"])==(a["tid"],a["gid"],a["inst"])), None)
        if match is None:
            chk(f"body[{i}]", False, "新包缺该 resource")
            continue
        nb = n[match["off_abs"]:match["off_abs"]+match["sz_abs"]]
        chk(f"body[{i}] inst=0x{a['inst']:016X}", ob == nb,
            f"len 原={a['sz_abs']} 新={match['sz_abs']} 内容一致={ob==nb}")

    print("\n===== 5. 文件级逐字节 diff (前40处差异) =====")
    diffs = []
    L = min(len(o), len(n))
    for i in range(L):
        if o[i] != n[i]:
            diffs.append(i)
            if len(diffs) >= 40: break
    if o[L:] : diffs.append(("orig-trailing", L, len(o)))
    if n[L:] : diffs.append(("new-trailing", L, len(n)))
    if not diffs:
        print("  无差异 (文件完全一致)")
    else:
        for d in diffs:
            if isinstance(d, tuple):
                print(f"  差异: {d}")
            else:
                print(f"  差异 @{d}: 原=0x{o[d]:02X} 新=0x{n[d]:02X}")

    print("\n===== 6. STBL body 展开 + 非 STBL 资源核查 (golden 对照) =====")
    STBL_TID = 0x220557DA
    print("  --- 各 resource 类型分布 ---")
    from collections import Counter
    def dist(es):
        c = Counter()
        for e in es:
            t = "STBL" if e["tid"] == STBL_TID else f"0x{e['tid']:08X}"
            c[t] += 1
        return c
    print(f"  原包资源分布: {dict(dist(oe))}")
    print(f"  新包资源分布: {dict(dist(ne))}")
    # 列出所有非 STBL 资源 (golden 模式下重点看这些是否被 s4pi 改动)
    print("  --- 非 STBL 资源清单 (原包) ---")
    for e in oe:
        if e["tid"] != STBL_TID:
            print(f"    tid=0x{e['tid']:08X} group=0x{e['gid']:08X} inst=0x{e['inst']:016X} "
                  f"off={e['off_abs']} sz={e['sz_abs']} flags=0x{e['fl']:08X} rs=0x{e['rs']:08X}")
    # STBL body 全 hex (前 64B) 逐字节对比
    print("  --- STBL body 前 64 字节 hex: 原 vs 新 ---")
    for i in range(oc):
        a = oe[i]
        if a["tid"] != STBL_TID:
            continue
        match = next((x for x in ne if (x["tid"],x["gid"],x["inst"])==(a["tid"],a["gid"],a["inst"])), None)
        if match is None:
            print(f"    STBL inst=0x{a['inst']:016X} 新包缺失")
            continue
        ob = o[a["off_abs"]:a["off_abs"]+a["sz_abs"]]
        nb = n[match["off_abs"]:match["off_abs"]+match["sz_abs"]]
        mark = "=" if ob == nb else "*"
        loc = (a["inst"] >> 56) & 0xFF
        print(f"    [{mark}] locale=0x{loc:02X} inst=0x{a['inst']:016X} "
              f"size原={a['sz_abs']} 新={match['sz_abs']}")
        if ob != nb:
            print(f"          原: {ob[:64].hex()}")
            print(f"          新: {nb[:64].hex()}")
            diffb = [j for j in range(min(len(ob), len(nb))) if ob[j] != nb[j]]
            print(f"          差异字节偏移 (body 内): {diffb[:30]}")

    print(f"\n===== 结果: {'存在 DIFF' if any_diff else '全部 PASS'} =====")
    return 0 if not any_diff else 1

if __name__ == "__main__":
    sys.exit(main())
