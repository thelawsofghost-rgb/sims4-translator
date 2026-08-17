#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WW SOURCE vs CONTROL_0 — index 字段澄清 (READ-ONLY, 小工具)

在真机 SOURCE / CONTROL_0 之间, 针对全部 23 个 TGI, 逐一拆开比对:
  1. raw_body_bytes   (index 指向的存储体原文 sha256 + 长度)
  2. offset_high_bit  (index entry 偏移字段的 0x80000000)
  3. size_high_bit    (index entry 大小字段的 0x80000000)
  4. field7           (writer/项目模型: mem_size; 或 flags)
  5. field8           (writer/项目模型: comp_type; 或 reserved)

目的: 彻底拆开 "body 字节" 与 "index 压缩位/元数据", 判定 WRITER 的
      size_high_bit normalization bug 是否真实存在于 CONTROL_0。

只读。不生成 package / 不部署 / 不碰 Mods (ZERO_WRITE_TO_MODS=YES)。
不修改 writer。不做真机测试。
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

DBPF_MAGIC = b"DBPF"
PREFIX = 4
ENTRY = 32


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def read(p: Path, off: int, n: int) -> bytes:
    with open(p, "rb") as f:
        f.seek(off)
        return f.read(n)


def parse(path: Path):
    size = path.stat().st_size
    head = read(path, 0, 0x44 + 4)
    if head[0:4] != DBPF_MAGIC:
        raise SystemExit(f"{path}: 非 DBPF")
    major = struct.unpack_from("<I", head, 4)[0]
    minor = struct.unpack_from("<I", head, 8)[0]
    count = struct.unpack_from("<I", head, 0x24)[0]
    isz = struct.unpack_from("<I", head, 0x2C)[0]
    ioff = struct.unpack_from("<I", head, 0x40)[0]
    if ioff + PREFIX + count * ENTRY > size + 4:
        raise SystemExit(f"{path}: index 越界 ioff={ioff} count={count} size={size}")
    idx = read(path, ioff, isz)
    ents = []
    for i in range(count):
        e = idx[PREFIX + i * ENTRY: PREFIX + (i + 1) * ENTRY]
        t, g, hi, lo, off, sz, f7, f8 = struct.unpack("<IIIIIIII", e)
        inst = (hi << 32) | lo
        ents.append({
            "tgi": f"0x{t:08X}/0x{g:08X}/0x{inst:016X}",
            "type": t, "group": g, "instance": inst,
            "off_raw": off, "sz_raw": sz,
            "off_phys": off & 0x7FFFFFFF, "sz_phys": sz & 0x7FFFFFFF,
            "off_hb": bool(off & 0x80000000), "sz_hb": bool(sz & 0x80000000),
            "f7": f7, "f8": f8,
        })
    return {"major": major, "minor": minor, "ents": ents, "path": path, "file_size": size}


def main():
    ap = argparse.ArgumentParser(description="WW SOURCE vs CONTROL_0 index 字段澄清 (只读)")
    ap.add_argument("--source", required=True)
    ap.add_argument("--control", required=True)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    s = parse(Path(args.source))
    c = parse(Path(args.control))
    smap = {e["tgi"]: e for e in s["ents"]}
    cmap = {e["tgi"]: e for e in c["ents"]}

    all_tgis = sorted(set(smap) | set(cmap))
    print(f"source version=2.{s['minor']}  control version=2.{c['minor']}  "
          f"TGI 总数(source={len(smap)} control={len(cmap)})")

    diffs = []
    n_body_sha, n_body_sz, n_off, n_sz, n_f7, n_f8 = (0,) * 6
    off_diff, sz_diff = [], []
    total = len(all_tgis)

    for tgi in all_tgis:
        a, b = smap.get(tgi), cmap.get(tgi)
        if a is None or b is None:
            diffs.append((tgi, "missing_in_one_side", a, b))
            continue
        a_body = read(s["path"], a["off_phys"], a["sz_phys"])
        b_body = read(c["path"], b["off_phys"], b["sz_phys"])
        sha_eq = (sha256(a_body) == sha256(b_body)) and (len(a_body) == len(b_body))
        off_eq = a["off_hb"] == b["off_hb"]
        sz_eq = a["sz_hb"] == b["sz_hb"]
        f7_eq = a["f7"] == b["f7"]
        f8_eq = a["f8"] == b["f8"]
        n_body_sha += sha_eq
        n_body_sz += (len(a_body) == len(b_body))
        n_off += off_eq
        n_sz += sz_eq
        n_f7 += f7_eq
        n_f8 += f8_eq
        if not off_eq:
            off_diff.append(tgi)
        if not sz_eq:
            sz_diff.append(tgi)
        if not (sha_eq and off_eq and sz_eq and f7_eq and f8_eq):
            diffs.append((tgi, "diff", a, b))

    # ---- 展开有差异的 ----
    print("\n--- 差异资源明细 (未全展开的均相等) ---")
    if not diffs:
        print("  (23 个 TGI 全部 5 维相等)")
    for tgi, kind, a, b in diffs:
        if kind == "missing_in_one_side":
            print(f"  {tgi}: 单侧缺失 (src={'Y' if a else 'N'} ctl={'Y' if b else 'N'})")
            continue
        a_body = read(s["path"], a["off_phys"], a["sz_phys"])
        b_body = read(c["path"], b["off_phys"], b["sz_phys"])
        print(f"--- {tgi} {'src' if a['instance'] in smap else 'ctl'} ---")
        print(f"  SOURCE: body_sha={sha256(a_body)} body_size={len(a_body)} "
              f"off_hb={int(a['off_hb'])} sz_hb={int(a['sz_hb'])} "
              f"f7=0x{a['f7']:08X} f8=0x{a['f8']:08X}")
        print(f"  CONTROL:body_sha={sha256(b_body)} body_size={len(b_body)} "
              f"off_hb={int(b['off_hb'])} sz_hb={int(b['sz_hb'])} "
              f"f7=0x{b['f7']:08X} f8=0x{b['f8']:08X}")
        print(f"  EQUAL: body_bytes={'YES' if sha256(a_body)==sha256(b_body) and len(a_body)==len(b_body) else 'NO'} "
              f"off_hb={'YES' if a['off_hb']==b['off_hb'] else 'NO'} "
              f"sz_hb={'YES' if a['sz_hb']==b['sz_hb'] else 'NO'} "
              f"f7={'YES' if a['f7']==b['f7'] else 'NO'} "
              f"f8={'YES' if a['f8']==b['f8'] else 'NO'}")

    # ---- summary ----
    body_sha_eq_all = (n_body_sha == total)
    body_sz_eq_all = (n_body_sz == total)
    print("\n" + "=" * 78)
    print("SUMMARY:")
    print(f"  RESOURCE_BODY_BYTES_EQUAL_FOR_NOOP={'YES' if body_sha_eq_all else 'NO'} "
          f"(body_sha 全等 {n_body_sha}/{total}, body_size 全等 {n_body_sz}/{total})")
    print(f"  OFFSET_HIGH_BIT_EQUAL_COUNT={n_off}/{total}")
    print(f"  SIZE_HIGH_BIT_EQUAL_COUNT={n_sz}/{total}")
    print(f"  FIELD7_EQUAL_COUNT={n_f7}/{total}")
    print(f"  FIELD8_EQUAL_COUNT={n_f8}/{total}")
    print(f"  OFFSET_HIGH_BIT_DIFF_COUNT={len(off_diff)}")
    print("  OFFSET_HIGH_BIT_DIFF_TGIS=" + (", ".join(off_diff) if off_diff else "NONE"))
    print(f"  SIZE_HIGH_BIT_DIFF_COUNT={len(sz_diff)}")
    print("  SIZE_HIGH_BIT_DIFF_TGIS=" + (", ".join(sz_diff) if sz_diff else "NONE"))

    # 判定: 大量资源 SOURCE off_hb=1 sz_hb=0 / CONTROL off_hb=1 sz_hb=1
    pat_sz = 0
    pat_tgis = []
    for tgi in all_tgis:
        a, b = smap.get(tgi), cmap.get(tgi)
        if a and b and a["off_hb"] and (not a["sz_hb"]) and b["off_hb"] and b["sz_hb"]:
            pat_sz += 1
            pat_tgis.append(tgi)
    print(f"  PATTERN_SRC(off=1,sz=0)->CTL(off=1,sz=1)_COUNT={pat_sz}")
    print("  " + "PATTERN_TGIS=" + (", ".join(pat_tgis) if pat_tgis else "NONE"))
    if pat_sz >= total * 0.5:
        print("  WRITER_SIZE_HIGH_BIT_NORMALIZATION_BUG=YES")
    else:
        print("  WRITER_SIZE_HIGH_BIT_NORMALIZATION_BUG=NO (pattern 不占多数)")

    # 旧 metric 说明
    old_fp = (body_sha_eq_all and body_sz_eq_all and len(sz_diff) > 0)
    print(f"  RESOURCE_RAW_EQUAL_FOR_NOOP_OLD_METRIC="
          f"{'FALSE_NEGATIVE' if old_fp else 'N/A'}"
          f"{' (body 字节全等但 size_high_bit 有差异 -> 旧 NO 是被 index bit 污染)' if old_fp else ''}")
    print("  ZERO_WRITE_TO_MODS=YES")

    if args.json:
        out = {
            "RESOURCE_BODY_BYTES_EQUAL_FOR_NOOP": "YES" if body_sha_eq_all else "NO",
            "OFFSET_HIGH_BIT_EQUAL_COUNT": f"{n_off}/{total}",
            "SIZE_HIGH_BIT_EQUAL_COUNT": f"{n_sz}/{total}",
            "FIELD7_EQUAL_COUNT": f"{n_f7}/{total}",
            "FIELD8_EQUAL_COUNT": f"{n_f8}/{total}",
            "OFFSET_HIGH_BIT_DIFF_COUNT": len(off_diff),
            "OFFSET_HIGH_BIT_DIFF_TGIS": off_diff,
            "SIZE_HIGH_BIT_DIFF_COUNT": len(sz_diff),
            "SIZE_HIGH_BIT_DIFF_TGIS": sz_diff,
            "WRITER_SIZE_HIGH_BIT_NORMALIZATION_BUG": "YES" if pat_sz >= total * 0.5 else "NO",
            "ZERO_WRITE_TO_MODS": "YES",
        }
        Path(args.json).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"\nJSON 已写出: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
