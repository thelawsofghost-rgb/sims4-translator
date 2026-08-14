#!/usr/bin/env python3
"""Phase 3B-SIDECAR forensic: 只读 raw-byte 底层对比 工作(s4pi SaveAs FAIL) 与 人工(S4S Save OK) sidecar。

背景: 自动 s4pi COMPLETE-STBL sidecar VERIFY=PASS + independent AUDIT=PASS, 但游戏加载 FAIL;
人工 S4S sidecar 游戏加载 OK。两者翻译文本不同 -> raw payload 不必相同; 本工具聚焦
container / index / metadata 结构差异, 把"文本长度不同导致"的正常差异单独归类。

用法 (Windows 只读):
  python scripts/dbpf_forensic.py -a <working_s4s.package> -b <failing_s4pi.package>

逐字节/逐字段 dump 两个包的:
  - 文件大小 / DBPF magic / major.minor / 0x44 header (含 user/version 字段, reserved 非零字节)
  - resource count / index offset / index size / index 区原始字节
  - 每个 index entry 32B 原始 + 解字段 (Type/Group/Instance/offset/size/flags/reserved, 压缩位)
  - STBL raw resource: 长度 / 压缩态 / magic / version / count / reserved / stringLength / entry 布局

输出分类:
  [PAYLOAD]  仅因文本长度不同导致的合法差异 (offset/size 平移)
  [CONTAINER] DBPF/container 序列化差异 (header/index/flags/reserved/hole/order)
  [S4S-WROTE] S4S 有而 s4pi 没有/不同的字段
  [S4SPI-WROTE] s4pi 有而 S4S 不同的字段

只读, 绝不改写任何 package。
"""
import sys, os, struct

ENTRY = 32
HEADER = 0x44


class Pkg:
    def __init__(self, path):
        self.path = os.path.realpath(path)
        with open(self.path, "rb") as fh:
            self.data = fh.read()
        self.size = len(self.data)
        self.h = self.data[:HEADER]

    def u32(self, off): return struct.unpack_from("<I", self.data, off)[0]
    def hex(self, off, n): return self.data[off:off + n].hex(" ")


def read_index_raw(p):
    """从 header 读 index offset/size/count, 返回 (count, index_offset, index_size, index_raw)。"""
    count = p.u32(0x24)
    idx_size = p.u32(0x2C)
    idx_off = p.u32(0x40)
    # 相对/绝对判定: 与 dbpf_fast 同源逻辑 (读原始即可)
    if idx_off + idx_size > p.size + 4:
        idx_off = p.size - idx_off
    idx_raw = p.data[idx_off:idx_off + idx_size]
    return count, idx_off, idx_size, idx_raw


def parse_entries(idx_raw, count):
    """解析 32B/entry。索引区开头 4B padding 后接 entry。返回 list[dict] + raw 字块。"""
    ents = []
    PAD = 4
    for i in range(count):
        base = PAD + i * ENTRY
        block = idx_raw[base:base + ENTRY]
        if len(block) < 24:
            ents.append({"raw": block.hex(" "), "err": "short"})
            continue
        t, g, hi, lo, off, sz = struct.unpack("<IIIIII", block[:24])
        flags, reserved = struct.unpack("<II", block[24:32])
        inst = (hi << 32) | lo
        ents.append({
            "raw": block.hex(" "),
            "Type": t, "Group": g, "inst_hi": hi, "inst_lo": lo, "Instance": inst,
            "offset_raw": off, "offset": off & 0x7FFFFFFF,
            "size_raw": sz, "size": sz & 0x7FFFFFFF,
            "compressed": bool(off & 0x80000000),
            "flags": flags, "reserved": reserved,
        })
    return ents


def stbl_entries(p, e):
    """只读 STBL body, 返回 (count, header_ctx, [(kh,flags,len,text,rawhex)])。读不到返回 None。"""
    try:
        off = e["offset"]; sz = e["size"]
        body = p.data[off:off + sz]
        if body[:4] != b"STBL":
            return None, (None, None, None, None, None), []
        ver = struct.unpack_from("<H", body, 4)[0]
        comp = body[6]
        count = struct.unpack_from("<Q", body, 7)[0]
        reserved = body[15:17]
        strlen = struct.unpack_from("<I", body, 17)[0]
        hdr_ctx = (ver, comp, reserved, strlen, count)
        keys = []
        o = 21
        for _ in range(count):
            if o + 7 > len(body):
                break
            kh = struct.unpack_from("<I", body, o)[0]
            fl = body[o + 4]
            ln = struct.unpack_from("<H", body, o + 5)[0]
            txt = body[o + 7:o + 7 + ln].decode("utf-8", errors="replace")
            keys.append((kh, fl, ln, txt, body[o:o + 7 + ln].hex(" ")))
            o += 7 + ln
        return body, hdr_ctx, keys
    except Exception:
        return None, (None, None, None, None, None), []


def dump_pkg(tag, p, cfg):
    print(f"\n================ {tag} : {p.path} ================")
    print(f"FILE_SIZE = {p.size}")
    magic = p.h[0:4]
    print(f"DBPF_MAGIC = {magic!r}")
    major, minor = struct.unpack("<II", p.h[4:12])
    # 0x0C..0x23 为 user/version/creation 等字段: 原样 hex
    user_ver = p.h[12:0x24]
    print(f"MAJOR = {major}  MINOR = {minor}")
    print(f"HEADER[0x0C..0x23] (user/version 等, 原样) = {user_ver.hex(' ')}")
    # 逐字段拆 0x24 起
    count = p.u32(0x24)
    print(f"A 0x24 entry_count = {count}")
    for off, name in [(0x28, "0x28 ?"), (0x2C, "0x2C index_size"),
                      (0x30, "0x30 ?"), (0x34, "0x34 ?"), (0x38, "0x38 ?"),
                      (0x3C, "0x3C ?"), (0x40, "0x40 index_offset")]:
        print(f"B {name} = 0x{p.u32(off):08X} ({p.u32(off)})")
    # 非零 reserved: header 0x12..0x23 除已拆外的剩余冗余
    print(f"HEADER_FULL_HEX = {p.h.hex(' ')}")
    # index
    icount, ioff, isize, iraw = read_index_raw(p)
    print(f"INDEX count={icount} offset=0x{ioff:X} size={isize}")
    ents = parse_entries(iraw, icount)
    print(f"RESOURCE_COUNT = {len(ents)}")
    for i, e in enumerate(ents):
        st = "C" if e.get("compressed") else " "
        print(f"  [entry {i}] 0x{000:04X} raw={e['raw']}")
        print(f"      Type=0x{e['Type']:08X} Group=0x{e['Group']:08X} Instance=0x{e['Instance']:016X}")
        print(f"      offset_raw=0x{e['offset_raw']:08X}({e['offset']}) comp={':' if e['compressed'] else '0'}{st}"
              f" size_raw=0x{e['size_raw']:08X}({e['size']}) flags=0x{e['flags']:08X} reserved=0x{e['reserved']:08X}")
        if e["Type"] == 0x220557DA:
            body, hdr, keys = stbl_entries(p, e)
            if body is None:
                print(f"      [STBL] 读失败")
                continue
            ver, comp, reserved, strlen, cnt = hdr
            print(f"      [STBL raw] byte_len={len(body)} comp_state={comp} magic={body[:4]!r} version={ver}"
                  f" count={cnt} reserved={reserved.hex()} stringLength={strlen}")
            for (kh, fl, ln, txt, rh) in keys:
                print(f"         key=0x{kh:08X} flags={fl} len={ln} raw={rh} :: {txt!r}")
    return ents


def diff_kv(name, a, b, bucket):
    if a != b:
        bucket.append(f"  {name}: A={a!r} vs B={b!r}")


def main():
    if len(sys.argv) != 5 or (sys.argv[1] != "-a" or sys.argv[3] != "-b"):
        print("用法: python scripts/dbpf_forensic.py -a <working_s4s.package> -b <failing_s4pi.package>")
        return 2
    pa = Pkg(sys.argv[2]); pb = Pkg(sys.argv[4])
    print(f"[forensic] A(work/S4S)={pa.path}\n          B(fail/s4pi)={pb.path}")

    ea = dump_pkg("A (工作 S4S sidecar)", pa, True)
    eb = dump_pkg("B (失败 s4pi sidecar)", pb, True)

    # ---- 分类对比 ----
    print("\n\n================ 差异分类 ================")
    payload = []
    container = []
    s4s_wrote = []
    s4pi_wrote = []

    if pa.size != pb.size:
        container.append(f"  FILE_SIZE: A={pa.size} vs B={pb.size}")
    ma = pa.h[0:4]; mb = pb.h[0:4]
    if ma != mb: container.append(f"  DBPF_MAGIC: A={ma!r} vs B={mb!r}")
    vam = struct.unpack("<II", pa.h[4:12]); vbm = struct.unpack("<II", pb.h[4:12])
    if vam != vbm:
        container.append(f"  MAJOR/MINOR: A={vam} vs B={vbm}")
    # user/version header 12..0x23
    if pa.h[12:0x24] != pb.h[12:0x24]:
        container.append(f"  HEADER user/version[0x0C..0x23]: A={pa.h[12:0x24].hex(' ')} vs B={pb.h[12:0x24].hex(' ')}")

    ca, ia, sa, _ = read_index_raw(pa)
    cb, ib, sb, _ = read_index_raw(pb)
    if ca != cb: container.append(f"  RESOURCE_COUNT: A={ca} vs B={cb}")
    if ia != ib: container.append(f"  INDEX_OFFSET: A=0x{ia:X} vs B=0x{ib:X}")
    if sa != sb: container.append(f"  INDEX_SIZE: A={sa} vs B={sb}")

    if len(ea) == len(eb):
        for i, (x, y) in enumerate(zip(ea, eb)):
            if x.get("Type") != y.get("Type"):
                container.append(f"  entry[{i}] Type: A=0x{x.get('Type'):08X} vs B=0x{y.get('Type'):08X}")
            if x.get("Group") != y.get("Group"):
                container.append(f"  entry[{i}] Group: A=0x{x.get('Group'):08X} vs B=0x{y.get('Group'):08X}")
            if x.get("Instance") != y.get("Instance"):
                container.append(f"  entry[{i}] Instance: A=0x{x.get('Instance'):016X} vs B=0x{y.get('Instance'):016X}")
            if x.get("compressed") != y.get("compressed"):
                container.append(f"  entry[{i}] 压缩位: A={'压缩' if x.get('compressed') else '未压缩'} vs B={'压缩' if y.get('compressed') else '未压缩'}")
            if x.get("flags") != y.get("flags"):
                bucket = (s4s_wrote if (x.get("flags") and not y.get("flags")) else
                          s4pi_wrote if (y.get("flags") and not x.get("flags")) else container)
                bucket.append(f"  entry[{i}] flags: A=0x{x.get('flags'):08X} vs B=0x{y.get('flags'):08X}")
            if x.get("reserved") != y.get("reserved"):
                bucket = (s4s_wrote if (x.get("reserved") and not y.get("reserved")) else
                          s4pi_wrote if (y.get("reserved") and not x.get("reserved")) else container)
                bucket.append(f"  entry[{i}] reserved: A=0x{x.get('reserved'):08X} vs B=0x{y.get('reserved'):08X}")
            # offset/size 不同 -> 若为 STBL 且文本不同则 PAYLOAD, 否则先标潜在
            if x.get("offset") != y.get("offset") or x.get("size") != y.get("size"):
                payload.append(f"  entry[{i}] offset/size 不同 (文本长度/布局差异, 见 STBL raw): "
                               f"A=off0x{x.get('offset'):X}/sz{x.get('size')} B=off0x{y.get('offset'):X}/sz{y.get('size')}")
    else:
        container.append(f"  entry 数量不同, 无法逐条对齐 (A={len(ea)} B={len(eb)})")

    # STBL header / stringLength / reserved
    def stbl_hdr(p, e):
        for x in (e if isinstance(e, list) else [e]):
            if x.get("Type") == 0x220557DA:
                body, h, k = stbl_entries(p, x)
                if body is not None:
                    return h, len(body), k
        return None, None, None

    ha, lena, ka = stbl_hdr(pa, ea)
    hb, lenb, kb = stbl_hdr(pb, eb)
    if ha and hb:
        (va, coa, ra, sla, cnta) = ha
        (vb, cob, rb, slb, cntb) = hb
        if va != vb: container.append(f"  STBL version: A={va} vs B={vb}")
        if coa != cob: container.append(f"  STBL comp_state: A={coa} vs B={cob}")
        if cnta != cntb: container.append(f"  STBL count: A={cnta} vs B={cntb}")
        if ra != rb:
            if any(ra) and not any(rb): s4s_wrote.append(f"  STBL reserved: A={ra.hex()} vs B={rb.hex()}")
            elif any(rb) and not any(ra): s4pi_wrote.append(f"  STBL reserved: A={ra.hex()} vs B={rb.hex()}")
            else: container.append(f"  STBL reserved: A={ra.hex()} vs B={rb.hex()}")
        if sla != slb: payload.append(f"  STBL stringLength: A={sla} vs B={slb} (文本不同, 正常)")
        if lena != lenb: payload.append(f"  STBL raw byte_len: A={lena} vs B={lenb} (文本不同, 正常)")

    print("\n--- [PAYLOAD] 文本长度不同导致的正常差异 ---")
    for x in payload: print(x)
    if not payload: print("  (无)")
    print("\n--- [CONTAINER] DBPF/container 序列化差异 ---")
    for x in container: print(x)
    if not container: print("  (无)")
    print("\n--- [S4S-WROTE / 差异字段] ---")
    for x in s4s_wrote: print(x)
    if not s4s_wrote: print("  (无)")
    print("\n--- [S4SPI-WROTE / 需人工核验] ---")
    for x in s4pi_wrote: print(x)
    if not s4pi_wrote: print("  (无)")

    print("\n结论: 上述 CONTAINER/S4S-WROTE 项为字段级可疑差异; PAYLOAD 项为正常文本差异。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
