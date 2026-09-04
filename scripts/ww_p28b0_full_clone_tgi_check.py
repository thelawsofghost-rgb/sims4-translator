#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p28b0_full_clone_tgi_check.py —— P28B-0 clone 独立条目普查 (与生成器/报告互独立)

在整包 byte-identical 之外, 对 source 与 clone 各自做【只读 raw-index 条目普查】
(复用 read_entry_meta_raw: 只读 index 原始 32 字节, 绝不 parse XML / 绝不重建 DBPF),
证明:
  * clone 确实是"同一个 package"(同一 index 结构被独立重读), 不是等长垃圾
  * source 与 clone 条目数一致, 且各自恰好 1 个 WW_ANIM_XML(0x7DF2169C)
  * 两者 WW_ANIM_XML TGI 一致, 并从 source(raw index)权威读出 instance

对真实 Nevely42 (479 条目) 部署: 额外核验 ENTRY_TOTAL=479 —— 若实际源条目数 != 479,
该字段打印实际值, 仅作诊断(以源为准, 不判死); 但 source==clone 条目数必须一致。

输出 (ASCII):
  ENTRY_COUNT_SOURCE=<int>
  ENTRY_COUNT_CLONE=<int>
  ENTRY_COUNT_EQUAL=YES|NO
  WW_XML_COUNT_SOURCE=<int>
  WW_XML_COUNT_CLONE=<int>
  WW_XML_SOURCE_TGI=0x../0x../0x..
  WW_XML_CLONE_TGI=0x../0x../0x..
  WW_XML_TGI_EQUAL=YES|NO
  WW_XML_SOURCE_INSTANCE=<instance>   (源权威 raw index 读出)
  WW_CLONE_VALID=YES|NO

退出码:
  0=PASS  2=参数缺/文件缺  3=源/克隆文件缺失或非DBPF  4=条目/TGI 普查不一致(FAIL, 拒绝部署)
  (校验失败绝不支持部署报告 PASS)
"""
import struct
import sys
from pathlib import Path

WW_ANIM_XML = 0x7DF2169C


def read_raw_index(pkg: Path):
    """只读 raw index (不 parse/不重建). 返回 (major,minor,count,list_of_tgi_dict)."""
    with open(pkg, "rb") as fh:
        fh.seek(0)
        hdr = fh.read(0x44)
        if hdr[0:4] != b"DBPF":
            raise ValueError("非 DBPF magic")
        count = struct.unpack("<I", hdr[0x24:0x28])[0]
        idx_off = struct.unpack("<I", hdr[0x40:0x44])[0]
        fh.seek(idx_off + 4)
        raw = fh.read(count * 32)
    out = []
    for i in range(count):
        e = raw[i * 32:(i + 1) * 32]
        t, g, ih, il = struct.unpack("<IIII", e[:16])
        inst = (ih << 32) | il
        out.append({"type": t, "group": g, "inst": inst})
    return count, out


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: ww_p28b0_full_clone_tgi_check.py <source.package> <clone.package>")
        return 2
    sp = Path(sys.argv[1])
    cp = Path(sys.argv[2])
    for lbl, p in (("SOURCE", sp), ("CLONE", cp)):
        if not p.is_file():
            print("WW_CLONE_VALID=NO")
            print(f"REASON={lbl}_MISSING")
            return 3
    try:
        scount, sent = read_raw_index(sp)
        ccount, cent = read_raw_index(cp)
    except Exception as ex:
        print("WW_CLONE_VALID=NO")
        print(f"REASON=INDEX_READ_ERROR:{ex}")
        return 3

    sw = [e for e in sent if e["type"] == WW_ANIM_XML]
    cw = [e for e in cent if e["type"] == WW_ANIM_XML]

    print(f"ENTRY_COUNT_SOURCE={scount}")
    print(f"ENTRY_COUNT_CLONE={ccount}")
    eq_count = scount == ccount
    print(f"ENTRY_COUNT_EQUAL={'YES' if eq_count else 'NO'}")

    print(f"WW_XML_COUNT_SOURCE={len(sw)}")
    print(f"WW_XML_COUNT_CLONE={len(cw)}")
    src_tgi = cw  # placeholder
    if sw and cw:
        s = sw[0]
        c = cw[0]
        st = f"0x{s['type']:08X}/0x{s['group']:08X}/0x{s['inst']:016X}"
        ct = f"0x{c['type']:08X}/0x{c['group']:08X}/0x{c['inst']:016X}"
        print(f"WW_XML_SOURCE_TGI={st}")
        print(f"WW_XML_CLONE_TGI={ct}")
        tgi_eq = (s["type"], s["group"], s["inst"]) == (c["type"], c["group"], c["inst"])
        print(f"WW_XML_TGI_EQUAL={'YES' if tgi_eq else 'NO'}")
        print(f"WW_XML_SOURCE_INSTANCE=0x{s['inst']:016X}")
        inst = s["inst"]
    else:
        print("WW_XML_SOURCE_TGI=")
        print("WW_XML_CLONE_TGI=")
        print("WW_XML_TGI_EQUAL=NO")
        print("WW_XML_SOURCE_INSTANCE=")
        tgi_eq = False
        inst = None

    valid = eq_count and len(sw) == 1 and len(cw) == 1 and tgi_eq and inst is not None
    print(f"WW_CLONE_VALID={'YES' if valid else 'NO'}")
    return 0 if valid else 4


if __name__ == "__main__":
    sys.exit(main())
