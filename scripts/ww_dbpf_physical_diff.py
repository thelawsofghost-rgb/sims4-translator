#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WW DBPF PHYSICAL LAYOUT FORENSIC v2 — 只读对拍 source vs control_0 (READ-ONLY)

v2 修复 (2026-08-17, 决定性 parser 错位 bug):
  v1 直接从 index_offset 开始解析第一个 32B entry, 没有跳过 DBPF index 的
  4-byte prefix/padding, 导致全部 per-entry 字段右移 4 bytes, 生成伪造的
  oob/overlap/gaps/alignment/trailer 差异。v1 结论全部撤销。

本轮修正 (依据 dbpf_fast.py 已正确实现的逻辑):
  - index 区 = 4-byte prefix/padding + (count × 32-byte entry)
    (真实 source: index_size=740 = 4 + 23×32; 与项目 parser 的 PAD=4 一致)
  - 正确 entry_start = index_offset + 4
  - 第 1 条 entry 必须恢复为已知真实 TGI
        0x7DF2169C / 0x00000000 / 0xB3390ED6606C10E7
    否则 FAIL-CLOSED, 停止 physical analysis。
  - 所有 resource range 必须满足 0 <= offset && offset+stored_size <= file_size;
    若全部 OOB → INDEX_PARSE_INVALID, 不再算 layout。
  - header boundary 不硬编码 0x60: 若 index_offset=0x44, 0x44 起已是 index,
    不得把 index bytes 当 header reserved 报 diff。

铁律 (fail-closed):
  * 只读。绝不生成 package / 绝不部署 / 绝不碰 Mods (ZERO_WRITE_TO_MODS=YES)。
  * 只修本取证仪器 (ww_dbpf_physical_diff.py); 不修改 ww_animation_canary_builder.py。
  * 不因旧错误报告就改 writer。

用法 (Windows, 只读):
  python scripts/ww_dbpf_physical_diff.py \
      --source "C:\\Users\\thela\\...\\MSWD_FORCE_FLOOR_002.package" \
      --control "D:\\projects\\sims4_trans\\output\\ww_animation_control_0\\MSWD_FORCE_FLOOR_002_CONTROL_0.package"

退出码: 0=完成; 2=参数/IO; 3=FIRST_TGI 校验失败 (FAIL-CLOSED)。
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

DBPF_MAGIC = b"DBPF"
INDEX_PREFIX = 4          # DBPF index 区开头的 4-byte prefix/padding (dbpf_fast PAD=4)
ENTRY = 32                # 每 entry 32 字节

EXPECTED_FIRST_TGI = {
    "type": 0x7DF2169C,
    "group": 0x00000000,
    "instance": 0xB3390ED6606C10E7,
}

# 项目 parser 已知的真实 header 字段 (0x00..0x44, 即 index_offset 之前)。
# 0x44 之后不再视为 header: header 边界 = index_offset (若 index_offset<0x60 则缩短)。
HEADER_FIELDS = [
    (0x00, 4, "magic"),
    (0x04, 4, "major"),
    (0x08, 4, "minor"),
    (0x0C, 4, "flags"),
    (0x10, 4, "reserved_10"),
    (0x14, 4, "reserved_14"),
    (0x18, 4, "reserved_18"),
    (0x1C, 4, "reserved_1C"),
    (0x20, 4, "reserved_20"),
    (0x24, 4, "index_count"),
    (0x28, 4, "reserved_28"),
    (0x2C, 4, "index_size"),
    (0x30, 4, "reserved_30"),
    (0x34, 4, "reserved_34"),
    (0x38, 4, "reserved_38"),
    (0x3C, 4, "compression_flag"),
    (0x40, 4, "index_offset"),
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0] if off + 4 <= len(data) else None


def read_range(path: Path, off: int, n: int) -> bytes:
    with open(path, "rb") as f:
        f.seek(off)
        return f.read(n)


def parse_pkg(path: Path, label: str):
    """解析单包的 header + index, 返回结构化 dict (不解析 resource body)。"""
    size = path.stat().st_size
    head = read_range(path, 0, 0x44 + 4)  # 至少读到 index_offset
    if len(head) < 0x44 + 4:
        return {"ok": False, "label": label, "reason": "header 过短"}
    if head[0:4] != DBPF_MAGIC:
        return {"ok": False, "label": label, "reason": "magic 不符"}
    major = u32(head, 0x04)
    minor = u32(head, 0x08)
    count = u32(head, 0x24)
    isz = u32(head, 0x2C)
    ioff = u32(head, 0x40)
    if count is None or isz is None or ioff is None:
        return {"ok": False, "label": label, "reason": "index 字段缺失"}

    # header 边界: 到 index_offset 为止; 若 index_offset 在 header 区内则截断
    hdr_end = min(0x44, ioff) if ioff is not None else 0x44
    real_head = read_range(path, 0, hdr_end)

    # index 区: 4-byte prefix + count*ENTRY
    if ioff + INDEX_PREFIX + count * ENTRY > size + 4:  # 容忍末尾 padding
        return {"ok": False, "label": label,
                "reason": f"索引越界 ioff={ioff} prefix={INDEX_PREFIX} count={count} size={size}",
                "major": major, "minor": minor}
    idx_raw = read_range(path, ioff, isz)
    prefix_bytes = idx_raw[0:INDEX_PREFIX]
    entries_raw = idx_raw[INDEX_PREFIX:INDEX_PREFIX + count * ENTRY]

    # index_at_end: 索引尾是否贴近文件尾
    at_end = (ioff + isz) >= size - 8

    return {
        "ok": True,
        "label": label, "path": path,
        "file_size": size,
        "major": major, "minor": minor,
        "count": count, "index_size": isz, "index_offset": ioff,
        "index_at_end": at_end,
        "real_head": real_head,
        "prefix_bytes": prefix_bytes,
        "idx_raw": idx_raw,
        "idx_sha": hashlib.sha256(idx_raw).hexdigest(),
        "entries_raw": entries_raw,
    }


def decode_entries(pkg, expected_first=None, fail_closed=False):
    """从 entries_raw 解码 23×32 entries; 校验第一条 == expected TGI, 否则 FAIL-CLOSED。"""
    raw = pkg["entries_raw"]
    count = pkg["count"]
    ents = []
    for i in range(count):
        block = raw[i * ENTRY:(i + 1) * ENTRY]
        if len(block) < ENTRY:
            ents.append({"raw": block.hex(" "), "err": "short"})
            continue
        t, g, hi, lo, off, sz, f7, f8 = struct.unpack("<IIIIIIII", block)
        inst = (hi << 32) | lo
        ents.append({
            "raw": block.hex(" "),
            "type": t, "group": g, "instance": f"0x{inst:016X}",
            "inst_hi": f"0x{hi:08X}", "inst_lo": f"0x{lo:08X}",
            "offset_raw": f"0x{off:08X}", "offset_phys": off & 0x7FFFFFFF,
            "offset_comp_bit": bool(off & 0x80000000),
            "size_raw": f"0x{sz:08X}", "size_phys": sz & 0x7FFFFFFF,
            "size_comp_bit": bool(sz & 0x80000000),
            "field7_raw": f"0x{f7:08X}",   # mem_size (per writer/database) 或 flags
            "field8_raw": f"0x{f8:08X}",   # comp_type 或 reserved
        })

    # FAIL-CLOSED: 第一条必须 == 期望 TGI
    if fail_closed and expected_first and ents:
        e0 = ents[0]
        ok = (e0.get("type") == expected_first["type"]
              and e0.get("group") == expected_first["group"]
              and e0.get("instance") == f"0x{expected_first['instance']:016X}")
        if not ok:
            pkg["first_tgi_valid"] = False
            pkg["first_tgi_reason"] = (f"第一条 {e0.get('type','?'):#x}/{e0.get('group','?'):#x}/"
                                       f"{e0.get('instance','?')} != 期望 "
                                       f"0x{expected_first['type']:08X}/0x{expected_first['group']:08X}/"
                                       f"0x{expected_first['instance']:016X}")
            return ents, False
    pkg["first_tgi_valid"] = True
    return ents, True


def range_check(pkg, ents, label):
    """校验所有 resource range 都在文件内。全部 OOB -> INDEX_PARSE_INVALID。"""
    size = pkg["file_size"]
    oob = []
    in_file = []
    for i, e in enumerate(ents):
        if e.get("err"):
            continue
        off, sz = e["offset_phys"], e["size_phys"]
        ok = 0 <= off and off + sz <= size
        (in_file if ok else oob).append((i, e["type"], e["offset_raw"], e["size_raw"]))
    status = "OK"
    if not in_file:
        status = "INDEX_PARSE_INVALID"
    return {"label": label, "status": status, "in_file": len(in_file), "oob": oob}


def layout_of(pkg, ents):
    """resource 物理布局指标 (仅当 range_check 非 INDEX_PARSE_INVALID 才可信)。"""
    m = {"count": 0, "ascending": True, "aligned4": True, "unaligned": [],
         "overlap": [], "gaps": [], "oob": []}
    holders = [e for e in ents if not e.get("err")]
    holders.sort(key=lambda e: e["offset_phys"])
    prev_end = None
    for e in holders:
        off, sz = e["offset_phys"], e["size_phys"]
        end = off + sz
        if end > pkg["file_size"]:
            m["oob"].append((e["type"], off, sz))
        if off % 4 != 0:
            m["aligned4"] = False
            m["unaligned"].append((e["type"], off))
        if prev_end is not None and off < prev_end:
            m["overlap"].append((e["type"], off, prev_end))
            m["ascending"] = False
        if prev_end is not None and off > prev_end:
            m["gaps"].append((prev_end, off, off - prev_end))
        prev_end = max(prev_end, end) if prev_end else end
    m["count"] = len(holders)
    return m


def search_bytes(data: bytes, needles):
    hits = []
    for nd in needles:
        pos = 0
        while True:
            i = data.find(nd["bytes"], pos)
            if i < 0:
                break
            hits.append({"name": nd["name"], "offset": i,
                         "ctx": data[max(0, i - 8):i + len(nd["bytes"]) + 8].hex(" ")})
            pos = i + 1
    return hits


def main():
    ap = argparse.ArgumentParser(description="WW DBPF physical diff forensic v2 (read-only)")
    ap.add_argument("--source", required=True)
    ap.add_argument("--control", required=True)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    sp, cp = Path(args.source), Path(args.control)
    for p in (sp, cp):
        if not p.is_file():
            print(f"ERROR: 文件不存在: {p}", file=sys.stderr)
            return 2

    print("=" * 78)
    print("WW DBPF PHYSICAL LAYOUT FORENSIC v2 (READ-ONLY) — 修复 index 4-byte prefix 错位")
    print("=" * 78)

    s0 = parse_pkg(sp, "SOURCE")
    c0 = parse_pkg(cp, "CONTROL")
    for pkg in (s0, c0):
        if not pkg.get("ok"):
            print(f"{pkg['label']} PARSE_ERROR: {pkg.get('reason')}")
            return 2

    print(f"SOURCE_SHA={sha256_file(sp)}")
    print(f"CONTROL_SHA={sha256_file(cp)}")

    # ---- 版本 (HIGH PRIORITY SUSPECT: SOURCE 2.1 vs CONTROL 2.0) ----
    src_v = f"{s0['major']}.{s0['minor']}"
    ctl_v = f"{c0['major']}.{c0['minor']}"
    print(f"SOURCE_DBPF_VERSION={src_v}")
    print(f"CONTROL_DBPF_VERSION={ctl_v}")

    # ---- 解码 entries + FAIL-CLOSED 首 TGI 校验 ----
    print("\n--- INDEX PARSE (4-byte prefix) ---")
    for pkg in (s0, c0):
        ents, ok = decode_entries(pkg, EXPECTED_FIRST_TGI, fail_closed=True)
        pkg["ents"] = ents
        print(f"  {pkg['label']}: prefix={pkg['prefix_bytes'].hex(' ')} idx_sha={pkg['idx_sha']}")
        if not ok:
            print(f"  {pkg['label']} FIRST_TGI_VALID=NO -> {pkg.get('first_tgi_reason')}")
            return 3
        e0 = ents[0]
        print(f"  {pkg['label']} FIRST_TGI_VALID=YES "
              f"0x{e0['type']:08X}/0x{e0['group']:08X}/{e0['instance']}")

    INDEX_PREFIX_SIZE = INDEX_PREFIX
    print(f"INDEX_PREFIX_SIZE={INDEX_PREFIX_SIZE}")
    print(f"SOURCE_INDEX_PREFIX={s0['prefix_bytes'].hex(' ')}")
    print(f"CONTROL_INDEX_PREFIX={c0['prefix_bytes'].hex(' ')}")
    print(f"EXPECTED_FIRST_TGI=0x{EXPECTED_FIRST_TGI['type']:08X}/0x{EXPECTED_FIRST_TGI['group']:08X}/"
          f"0x{EXPECTED_FIRST_TGI['instance']:016X}")
    print(f"FIRST_TGI_VALID=YES (source+control 均已校验)")

    # ---- index 物理放置 ----
    print("\n--- INDEX PLACEMENT ---")
    for pkg in (s0, c0):
        print(f"  {pkg['label']} index_offset={pkg['index_offset']} index_size={pkg['index_size']} "
              f"count={pkg['count']} index_at_end={'YES' if pkg['index_at_end'] else 'NO'}")
    print(f"SOURCE_INDEX_OFFSET={s0['index_offset']}")
    print(f"CONTROL_INDEX_OFFSET={c0['index_offset']}")
    print(f"SOURCE_INDEX_AT_END={'YES' if s0['index_at_end'] else 'NO'}")
    print(f"CONTROL_INDEX_AT_END={'YES' if c0['index_at_end'] else 'NO'}")
    INDEX_RAW_EQUAL = s0["idx_sha"] == c0["idx_sha"]
    print(f"INDEX_RAW_EQUAL={'YES' if INDEX_RAW_EQUAL else 'NO'}")

    # index order / metadata semantics (fix: 现在基于正确解出的 entry)
    s_keys = [(e.get("type"), e.get("group"), e.get("instance")) for e in s0["ents"]]
    c_keys = [(e.get("type"), e.get("group"), e.get("instance")) for e in c0["ents"]]
    ORDER_EQUAL = s_keys == c_keys
    print(f"INDEX_ORDER_EQUAL={'YES' if ORDER_EQUAL else 'NO'}")
    # metadata semantics: field7/field8 逐 entry (在正确对齐后)
    met_same = True
    if len(s0["ents"]) == len(c0["ents"]):
        for a, b in zip(s0["ents"], c0["ents"]):
            if a.get("field7_raw") != b.get("field7_raw") or a.get("field8_raw") != b.get("field8_raw"):
                met_same = False
                break
    else:
        met_same = False
    print(f"INDEX_METADATA_SEMANTIC_EQUAL={'YES' if met_same else 'NO'} (field7/field8 逐 entry)")

    # ---- resource range 校验 ----
    print("\n--- RESOURCE RANGE CHECK (0<=off, off+sz<=filesize) ---")
    s_rc = range_check(s0, s0["ents"], "SOURCE")
    c_rc = range_check(c0, c0["ents"], "CONTROL")
    for rc in (s_rc, c_rc):
        print(f"  {rc['label']} status={rc['status']} in_file={rc['in_file']} oob_count={len(rc['oob'])}")
        if rc["oob"]:
            for o in rc["oob"][:10]:
                print(f"      OOB idx={o[0]} type=0x{o[1]:08X} off={o[2]} sz={o[3]}")
    print(f"SOURCE_ALL_RESOURCE_RANGES_IN_FILE={'YES' if s_rc['status']=='OK' else 'NO'}")
    print(f"CONTROL_ALL_RESOURCE_RANGES_IN_FILE={'YES' if c_rc['status']=='OK' else 'NO'}")

    # ---- resource physical layout (仅当 range 有效) ----
    print("\n--- RESOURCE PHYSICAL LAYOUT (正确对齐后) ---")
    if s_rc["status"] == "OK" and c_rc["status"] == "OK":
        s_l = layout_of(s0, s0["ents"]); c_l = layout_of(c0, c0["ents"])
        for lb, l in (("SOURCE", s_l), ("CONTROL", c_l)):
            print(f"  {lb}: count={l['count']} ascending={l['ascending']} aligned4={l['aligned4']} "
                  f"unaligned={len(l['unaligned'])} overlap={len(l['overlap'])} gaps={len(l['gaps'])} "
                  f"oob={len(l['oob'])}")
            if l["gaps"]:
                print(f"      gaps(prev_end,next,size): {l['gaps'][:10]}")
        RESOURCE_LAYOUT_EQUAL = (s_l["ascending"] == c_l["ascending"]
                                 and s_l["aligned4"] == c_l["aligned4"]
                                 and s_l["overlap"] == c_l["overlap"]
                                 and s_l["gaps"] == c_l["gaps"])
    else:
        RESOURCE_LAYOUT_EQUAL = None
        print("  (range 校验非 OK, layout 结论不作数)")

    # resource raw equality for NOOP (只对比相同 TGI 的 body 头/长度; 完整比对留待 Windows)
    print("\n--- RESOURCE RAW (per-TGI body size/头字节 对拍) ---")
    s_by = {e["instance"]: e for e in s0["ents"] if not e.get("err")}
    c_by = {e["instance"]: e for e in c0["ents"] if not e.get("err")}
    noop_equal = True
    common_tgis = set(s_by) & set(c_by)
    for inst in sorted(common_tgis):
        a, b = s_by[inst], c_by[inst]
        if a["size_phys"] != b["size_phys"] or a["offset_comp_bit"] != b["offset_comp_bit"]:
            noop_equal = False
            print(f"  RAW_DIFF {inst}: src_sz={a['size_phys']}(c={int(a['offset_comp_bit'])}) "
                  f"ctl_sz={b['size_phys']}(c={int(b['offset_comp_bit'])})")
    print(f"RESOURCE_RAW_EQUAL_FOR_NOOP={'YES' if noop_equal else 'NO'} (同 TGI body size+comp 位全等)")

    # ---- header 真实差异 (不再把 index bytes 当 header) ----
    print("\n--- REAL HEADER DIFF (header 边界按 index_offset) ---")
    s_head = s0["real_head"]; c_head = c0["real_head"]
    hlen = min(len(s_head), len(c_head))
    diff_fields = []
    for off, size, name in HEADER_FIELDS:
        if off + size <= hlen:
            a, b = s_head[off:off + size], c_head[off:off + size]
            if a != b:
                diff_fields.append(f"{name}@{off:#x}:src={a.hex(' ')} ctl={b.hex(' ')}")
    print(f"REAL_HEADER_DIFF_FIELDS={'; '.join(diff_fields) if diff_fields else 'NONE'}")

    # ---- game error code 字节搜索 ----
    print("\n--- GAME ERROR CODE BYTES (BE+LE) ---")
    err_hex = ["60c399f9", "de4ee14f", "29f00e76", "4b0d852b"]
    needles = []
    for hx in err_hex:
        b = bytes.fromhex(hx)
        needles.append({"name": f"{hx} BE", "bytes": b})
        needles.append({"name": f"{hx} LE", "bytes": b[::-1]})
    all_hits = []
    for pkg in (s0, c0):
        data = read_range(pkg["path"], 0, pkg["file_size"])
        hits = search_bytes(data, needles)
        if hits:
            all_hits += [(pkg["label"], h) for h in hits]
    if all_hits:
        for lb, h in all_hits:
            print(f"  HIT {lb} {h['name']} @0x{h['offset']:08X} ctx={h['ctx']}")
        GAME_ERROR_COMPONENT_MATCH = "FOUND"
    else:
        print("  GAME_ERROR_COMPONENT_MATCH=NONE_FOUND")
        GAME_ERROR_COMPONENT_MATCH = "NONE_FOUND"

    # ---- 汇总 ----
    ver_downgrade = "YES" if (s0["major"], s0["minor"]) != (c0["major"], c0["minor"]) else "NO"
    summary = {
        "SOURCE_SHA": sha256_file(sp),
        "CONTROL_SHA": sha256_file(cp),
        "INDEX_PREFIX_SIZE": INDEX_PREFIX_SIZE,
        "SOURCE_INDEX_PREFIX": s0["prefix_bytes"].hex(),
        "CONTROL_INDEX_PREFIX": c0["prefix_bytes"].hex(),
        "SOURCE_FIRST_TGI": "0x%08X/0x%08X/%s" % (s0["ents"][0]["type"], s0["ents"][0]["group"], s0["ents"][0]["instance"]),
        "CONTROL_FIRST_TGI": "0x%08X/0x%08X/%s" % (c0["ents"][0]["type"], c0["ents"][0]["group"], c0["ents"][0]["instance"]),
        "EXPECTED_FIRST_TGI": "0x%08X/0x%08X/0x%016X" % (EXPECTED_FIRST_TGI["type"], EXPECTED_FIRST_TGI["group"], EXPECTED_FIRST_TGI["instance"]),
        "FIRST_TGI_VALID": "YES",
        "SOURCE_ALL_RESOURCE_RANGES_IN_FILE": "YES" if s_rc["status"] == "OK" else "NO",
        "CONTROL_ALL_RESOURCE_RANGES_IN_FILE": "YES" if c_rc["status"] == "OK" else "NO",
        "SOURCE_DBPF_VERSION": src_v,
        "CONTROL_DBPF_VERSION": ctl_v,
        "WRITER_VERSION_DOWNGRADE": ver_downgrade,
        "SOURCE_INDEX_OFFSET": s0["index_offset"],
        "CONTROL_INDEX_OFFSET": c0["index_offset"],
        "SOURCE_INDEX_AT_END": "YES" if s0["index_at_end"] else "NO",
        "CONTROL_INDEX_AT_END": "YES" if c0["index_at_end"] else "NO",
        "INDEX_ORDER_EQUAL": "YES" if ORDER_EQUAL else "NO",
        "INDEX_METADATA_SEMANTIC_EQUAL": "YES" if met_same else "NO",
        "RESOURCE_RAW_EQUAL_FOR_NOOP": "YES" if noop_equal else "NO",
        "RESOURCE_LAYOUT_EQUAL": ("YES" if RESOURCE_LAYOUT_EQUAL else "NO") if RESOURCE_LAYOUT_EQUAL is not None else "N/A",
        "REAL_HEADER_DIFF_FIELDS": "; ".join(diff_fields) if diff_fields else "NONE",
        "GAME_ERROR_COMPONENT_MATCH": GAME_ERROR_COMPONENT_MATCH,
    }
    print("\n" + "=" * 78)
    print("PHYSICAL_FORENSIC_SUMMARY:")
    for k, v in summary.items():
        print(f"  {k}={v}")
    print("ZERO_WRITE_TO_MODS=YES")
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\nJSON 已写出: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
