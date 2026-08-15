#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit_source_diag.py —— 只读诊断 source vs canary 输出包的 STBL 读取失败 (slot7 类)
================================================================================
目的: 当 audit_canary_pair 对 source 报 SOURCE_ENTRIES=0 / SOURCE_AUDIT_ERROR 时,
无副本地打印精确元数据 + audit reader 捕获的真实异常, 以区分:

  1) audit parser 不支持该 source 的 package/STBL/compression   (parser 缺陷 -> 修 audit)
  2) source STBL 确实为空 / 无法解析                          (违反 complete-clone 不变式)

对比对象 (均只读, 绝不改写):
  -source <source.package>   原 mod 的 CHS STBL (只读)
  -out    <canary.package>   SidecarBuilder 生成的独立包 (只读)

输出:
  SOURCE_PATH / SOURCE_PARSE_ERROR / SOURCE_STBL_COUNT / SOURCE_CHS_COUNT
  SOURCE_CHS_TGI (type/group/instance) / SOURCE_ENTRY_offset / SOURCE_ENTRY_size
  SOURCE_ENTRY_compressed_flag / SOURCE_BODY_first_bytes / SOURCE_DECOMPRESS_RESULT
  SOURCE_STBL_VERSION / SOURCE_STBL_ISCOMP / SOURCE_STBL_ENTRIES / SOURCE_READ_ERROR
  OUTPUT_PATH / ...  (同 source 一套)
  结论: SOURCE_READ_OK / SOURCE_READ_FAIL (带 detail) / OUTPUT_READ_OK / OUTPUT_READ_FAIL

用法:
  python scripts/audit_source_diag.py -source <SOURCE> -out <CANARY>

只读诊断, 绝不改写任何 package。
"""
import sys, os, struct, zlib, argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dbpf_fast import safe_parse
import audit_sidecar as A

LOCALE_CHS = 0x01


def _decompress(data: bytes) -> bytes:
    if data[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        try:
            return zlib.decompress(data)
        except Exception:
            return data
    return data


def diagnose_one(label, path):
    """打印单个 package 的 STBL 元数据。"""
    print(f"--- {label} ---")
    idx, err = safe_parse(path)
    if err:
        print(f"{label}_PARSE_ERROR = {err}")
        return None
    if idx is None:
        print(f"{label}_PARSE_ERROR = DBPFIndex is None")
        return None
    print(f"{label}_PATH = {os.path.realpath(path)}")
    stbl = [e for e in idx.entries if e.type_id == A.STBL_TID]
    print(f"{label}_STBL_COUNT = {len(stbl)}")
    chs = [e for e in stbl if ((e.instance_id >> 56) & 0xFF) == LOCALE_CHS]
    print(f"{label}_CHS_COUNT = {len(chs)}")
    sel = (chs[0] if chs else stbl[0]) if stbl else None
    if sel is not None:
        print(f"{label}_CHS_TGI = 0x{sel.type_id:08X}/0x{sel.group_id:08X}/0x{sel.instance_id:016X}")
        print(f"{label}_ENTRY_offset = {sel.offset & 0x7FFFFFFF}  size = {sel.size & 0x7FFFFFFF}")
        print(f"{label}_ENTRY_compressed_flag = {bool(sel.offset & 0x80000000)}  "
              f"is_compressed = {sel.is_compressed}")
        off = sel.offset & 0x7FFFFFFF
        sz = sel.size & 0x7FFFFFFF
        with open(path, "rb") as fh:
            fh.seek(off)
            body = fh.read(sz)
        print(f"{label}_BODY_first_bytes = {body[:16]!r}")
        dec = _decompress(body)
        print(f"{label}_DECOMPRESS_RESULT = len {len(body)} -> {len(dec)}  head {dec[:8]!r}")
        if len(dec) >= 21 and dec[:4] == b"STBL":
            version = struct.unpack_from("<H", dec, 4)[0]
            is_comp = dec[6]
            count = struct.unpack_from("<Q", dec, 7)[0]
            print(f"{label}_STBL_VERSION = {version}")
            print(f"{label}_STBL_ISCOMP = {is_comp}")
            print(f"{label}_STBL_ENTRIES(header) = {count}")
            # 逐 entry 实际读 (与 audit 相同逻辑)
            keys = []
            o = 21
            for _ in range(count):
                if o + 7 > len(dec):
                    break
                kh = struct.unpack_from("<I", dec, o)[0]
                ln = struct.unpack_from("<H", dec, o + 5)[0]
                keys.append((kh, dec[o + 4], dec[o + 7:o + 7 + ln].decode("utf-8", errors="replace")))
                o += 7 + ln
            print(f"{label}_STBL_ENTRIES(parsed) = {len(keys)}")
            return sel
        else:
            print(f"{label}_READ_ERROR = 非 STBL magic (前4字节={dec[:4]!r}, 长度={len(dec)})")
            return None
    else:
        print(f"{label}_READ_ERROR = 无 CHS/STBL resource 可选")
        return None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-source", required=True)
    ap.add_argument("-out", required=True)
    a = ap.parse_args()
    print("=========== audit_source_diag (只读) ===========")
    diagnose_one("SOURCE", os.path.realpath(a.source))
    print()
    diagnose_one("OUTPUT", os.path.realpath(a.out))
    print("=========== end ===========")
    return 0


if __name__ == "__main__":
    sys.exit(main())
