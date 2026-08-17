#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WW DBPF PHYSICAL LAYOUT FORENSIC — 只读对拍 source.package vs control_0.package

CONTROL_0 真机 FAIL 之后: 即使 logical contents / raw unchanged bytes /
source-relative compression metadata 全部一致, Sims 4 loader 仍拒载 (error
de4ee14f vs old canary 60c399f9)。这证明失败位于 DBPF 物理结构层。

本工具只【读取】两个包, 逐字节/逐字段对拍它们的物理布局, 找出:
  - 完整 header (含 unknown/reserved) 差异
  - index 物理位置 (head vs end) / 原始 entry 格式 / 排序 / padding / trailer
  - resource 物理 offset / stored size / alignment / gaps / padding
  - file trailer / source-only 字节区段 (footer / secondary index / checksum / signature)
  - 游戏 error code 字节 (两种 endian) 在文件中的命中

铁律 (fail-closed):
  * 只读。绝不生成 package / 绝不部署 / 绝不碰 Mods (ZERO_WRITE_TO_MODS=YES)。
  * 不做任何"猜测性修复"。只输出差异与物理结构事实。
  * 对 index field7/field8 同时给出两种项目内解释模型 (不偏袒任一):
      M1 flags/reserved   (dbpf_fast.py 注释)
      M2 mem_size/comp_type (ww_animation_forensic_deep_one.py 注释)
    由真实字节仲裁, 不猜。

用法 (Windows, 只读):
  python scripts/ww_dbpf_physical_diff.py \
      --source "C:\\Users\\thela\\...\\MSWD_FORCE_FLOOR_002.package" \
      --control "D:\\projects\\sims4_trans\\output\\ww_animation_control_0\\MSWD_FORCE_FLOOR_002_CONTROL_0.package"

输出:
  stdout 逐节报告 + 可选 --json out.json 机器可读。
退出码: 0=完成(即使发现差异); 2=参数/IO/无法解析。
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

DBPF_MAGIC = b"DBPF"

# ---- 标准 S4PE 96 字节 header 的字段表 (offset, size, name) -----------------
# 0x00..0x44 为项目 mini-parser 已知; 0x44..0x60 为 S4PE 扩展段(仅作 raw 对拍,
# 不猜测语义)。对未知字段保留 offset + source/control raw bytes。
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
    (0x44, 4, "s4pe_pad_44"),
    (0x48, 4, "s4pe_pad_48"),
    (0x4C, 4, "s4pe_pad_4C"),
    (0x50, 4, "s4pe_pad_50"),
    (0x54, 4, "s4pe_pad_54"),
    (0x58, 4, "s4pe_pad_58"),
    (0x5C, 4, "s4pe_pad_5C"),
]

ENTRY = 32
PAD = 4  # 项目 parser 认为的索引区开头 padding


# ----------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0] if off + 4 <= len(data) else None


def read_head(path: Path) -> bytes:
    with open(path, "rb") as f:
        return f.read(0x60)  # 固定 header 96 字节


def parse_header(data: bytes):
    out = {}
    for off, size, name in HEADER_FIELDS:
        if off + size <= len(data):
            raw = data[off:off + size]
            out[name] = {"offset": off, "raw": raw.hex(" "), "u32": read_u32(data, off)}
    return out


def index_entries_from_raw(idx_raw: bytes, count: int):
    """从索引原始字节解析 entries; 解析失败条目保留 raw 供比对。"""
    ents = []
    for i in range(count):
        base = i * ENTRY
        block = idx_raw[base:base + ENTRY]
        if len(block) < ENTRY:
            ents.append({"raw": block.hex(" "), "err": "short"})
            continue
        t, g, hi, lo, off, sz, f7, f8 = struct.unpack("<IIIIIIII", block)
        inst = (hi << 32) | lo
        ents.append({
            "raw": block.hex(" "),
            "type": t, "group": g, "inst_hi": hi, "inst_lo": lo, "instance": f"0x{inst:016X}",
            "offset_raw": f"0x{off:08X}", "offset_phys": off & 0x7FFFFFFF,
            "offset_comp_bit": bool(off & 0x80000000),
            "size_raw": f"0x{sz:08X}", "size_phys": sz & 0x7FFFFFFF,
            "size_comp_bit": bool(sz & 0x80000000),
            # 两种解释模型
            "M1_field7_hex": f"0x{f7:08X}",
            "M2_field7_mem_size": f7,
            "M1_field8_hex": f"0x{f8:08X}",
            "M2_field8_comp_type": f8,
        })
    return ents


def locate_index(path: Path, header, file_size: int):
    """根据 header.index_offset / index_count / index_size 定位索引区, 返回 dict。"""
    count = header.get("index_count", {}).get("u32")
    isz = header.get("index_size", {}).get("u32")
    ioff = header.get("index_offset", {}).get("u32")
    if count is None or isz is None or ioff is None:
        return {"ok": False, "reason": "header 字段缺失"}
    with open(path, "rb") as f:
        # index 区 = 从 ioff 起 isz 字节; 若越界尝试从末尾反算
        f.seek(ioff)
        idx_raw = f.read(isz)
        if len(idx_raw) < isz:
            return {"ok": False, "reason": f"索引越界 ioff={ioff} isz={isz}"}
    at_end = (ioff + isz) >= file_size - 8  # 容忍末尾 padding
    return {
        "ok": True,
        "index_offset": ioff,
        "index_size": isz,
        "index_count_field": count,
        "index_at_end": at_end,
        "idx_raw": idx_raw,
        "idx_sha256": hashlib.sha256(idx_raw).hexdigest(),
    }


def collect_data_offsets(idx_info):
    """从索引 entries 收集每个资源的物理 offset/size/end。"""
    res = {}
    if not idx_info.get("ok"):
        return res
    raw = idx_info["idx_raw"]
    count = idx_info["index_count_field"]
    for i in range(count):
        base = i * ENTRY
        if base + ENTRY > len(raw):
            break
        t, g, hi, lo, off, sz, _, _ = struct.unpack("<IIIIIIII", raw[base:base + ENTRY])
        inst = (hi << 32) | lo
        poff = off & 0x7FFFFFFF
        psz = sz & 0x7FFFFFFF
        res[f"0x{t:08X}/0x{g:08X}/0x{inst:016X}"] = {
            "phys_off": poff, "stored_size": psz, "end": poff + psz,
        }
    return res


def compute_layout_metrics(path: Path, data_offsets, file_size: int):
    """对某文件的资源物理布局做统计: 排序性 / 对齐 / gaps / 重叠 / 超界。"""
    metrics = {
        "resource_count": len(data_offsets),
        "offsets_ascending": True,
        "sorted_offsets": [],
        "aligned_to_4": True,
        "unaligned_resources": [],
        "overlapping": [],
        "out_of_bounds": [],
        "gaps": [],          # (prev_end, next_off, size)
        "padding_before_first": None,
        "file_size": file_size,
    }
    sorted_off = sorted(data_offsets.items(), key=lambda kv: kv[1]["phys_off"])
    metrics["sorted_offsets"] = [k for k, _ in sorted_off]
    prev_end = None
    for tgi, d in sorted_off:
        if d["phys_off"] % 4 != 0:
            metrics["aligned_to_4"] = False
            metrics["unaligned_resources"].append((tgi, d["phys_off"]))
        if d["end"] > file_size:
            metrics["out_of_bounds"].append((tgi, d["phys_off"], d["stored_size"]))
        if prev_end is not None and d["phys_off"] < prev_end:
            metrics["overlapping"].append((tgi, d["phys_off"], prev_end))
            metrics["offsets_ascending"] = False
        if prev_end is not None and d["phys_off"] > prev_end:
            metrics["gaps"].append((prev_end, d["phys_off"], d["phys_off"] - prev_end))
        prev_end = max(prev_end, d["end"]) if prev_end else d["end"]
    return metrics


def search_bytes(path: Path, needles, label):
    """在文件中搜索若干字节序列 (error code 两种 endian 形式), 返回命中。"""
    with open(path, "rb") as f:
        data = f.read()
    hits = []
    for nd in needles:
        pos = 0
        while True:
            i = data.find(nd["bytes"], pos)
            if i < 0:
                break
            ctx = data[max(0, i - 8):i + len(nd["bytes"]) + 8]
            hits.append({"name": nd["name"], "file": label,
                         "offset": i, "context": ctx.hex(" ")})
            pos = i + 1
    return hits


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="WW DBPF physical layout forensic (read-only)")
    ap.add_argument("--source", required=True, help="真实 source package 路径")
    ap.add_argument("--control", required=True, help="CONTROL_0 package 路径")
    ap.add_argument("--json", default=None, help="可选: 输出机器可读 JSON 文件路径")
    args = ap.parse_args()

    sp = Path(args.source)
    cp = Path(args.control)
    for p in (sp, cp):
        if not p.is_file():
            print(f"ERROR: 文件不存在: {p}", file=sys.stderr)
            return 2

    src_sha = sha256_file(sp)
    ctl_sha = sha256_file(cp)
    src_size = sp.stat().st_size
    ctl_size = cp.stat().st_size

    s_hdr = parse_header(read_head(sp))
    c_hdr = parse_header(read_head(cp))
    s_idx = locate_index(sp, s_hdr, src_size)
    c_idx = locate_index(cp, c_hdr, ctl_size)

    print("=" * 78)
    print("WW DBPF PHYSICAL LAYOUT FORENSIC (READ-ONLY)")
    print("=" * 78)
    print(f"SOURCE_SHA={src_sha}")
    print(f"CONTROL_SHA={ctl_sha}")
    print(f"SOURCE_FILE_SIZE={src_size}")
    print(f"CONTROL_FILE_SIZE={ctl_size}")

    # ---- A. HEADER 完整对拍 -------------------------------------------------
    print("\n--- A. FULL DBPF HEADER (0x00..0x60) 逐字段对拍 ---")
    all_names = list(dict.fromkeys(list(s_hdr.keys()) + list(c_hdr.keys())))
    same = True
    diff_fields = []
    for n in all_names:
        s = s_hdr.get(n, {}); c = c_hdr.get(n, {})
        s_r = s.get("raw"); c_r = c.get("raw")
        eq = (s_r == c_r)
        if not eq:
            same = False
            diff_fields.append(n)
        mark = "==" if eq else "!="
        print(f"  {n:24s} off=0x{s.get('offset', '?'):02X} "
              f"src={s_r or '<?':<40s} ctl={c_r or '<?':<40s} {mark}")
    # 逐字节 raw header diff (含未知字节)
    s_hb = read_head(sp); c_hb = read_head(cp)
    hlen = max(len(s_hb), len(c_hb))
    HEADER_BYTE_EQUAL = (s_hb == c_hb)
    unknown_diffs = []
    for i in range(0, min(len(s_hb), len(c_hb)), 4):
        if s_hb[i:i + 4] != c_hb[i:i + 4]:
            known = [n for n in HEADER_FIELDS if n[0] == i]
            unknown_diffs.append({
                "offset": i,
                "src": s_hb[i:i + 4].hex(" "),
                "ctl": c_hb[i:i + 4].hex(" "),
                "known_field": known[0][2] if known else None,
            })
    print(f"HEADER_BYTE_EQUAL={HEADER_BYTE_EQUAL}")
    print(f"HEADER_DIFF_FIELDS={','.join(diff_fields) if diff_fields else 'NONE'}")
    unk = [u for u in unknown_diffs if not u["known_field"]]
    if unk:
        print("UNKNOWN_HEADER_FIELD_DIFFS (保留 offset/src/ctl raw):")
        for u in unk:
            print(f"  off=0x{u['offset']:02X} src=0x{u['src']} ctl=0x{u['ctl']}")
    UNKNOWN_HEADER_FIELDS_EQUAL = len(unk) == 0
    print(f"UNKNOWN_HEADER_FIELDS_EQUAL={'YES' if UNKNOWN_HEADER_FIELDS_EQUAL else 'NO'}")

    # ---- B. INDEX PHYSICAL LAYOUT ------------------------------------------
    INDEX_RAW_EQUAL = None
    INDEX_STRUCTURE_EQUAL = None
    print("\n--- B. INDEX PHYSICAL LAYOUT ---")
    for label, idx in (("SOURCE", s_idx), ("CONTROL", c_idx)):
        print(f"  {label}: ok={idx.get('ok')} index_offset={idx.get('index_offset')} "
              f"index_size={idx.get('index_size')} index_count_field={idx.get('index_count_field')} "
              f"index_at_end={'YES' if idx.get('index_at_end') else 'NO'}")
    if s_idx.get("ok") and c_idx.get("ok"):
        print(f"INDEX_OFFSET: SOURCE={s_idx['index_offset']} CONTROL={c_idx['index_offset']}")
        print(f"INDEX_AT_END: SOURCE={'YES' if s_idx['index_at_end'] else 'NO'} "
              f"CONTROL={'YES' if c_idx['index_at_end'] else 'NO'}")
        s_es = len(s_idx["idx_raw"]) // ENTRY
        c_es = len(c_idx["idx_raw"]) // ENTRY
        print(f"INDEX_ENTRY_SIZE: SOURCE=32 CONTROL=32 (每 entry 32B)")
        print(f"INDEX_RAW_SHA256: SOURCE={s_idx['idx_sha256']}")
        print(f"INDEX_RAW_SHA256: CONTROL={c_idx['idx_sha256']}")
        INDEX_RAW_EQUAL = s_idx["idx_sha256"] == c_idx["idx_sha256"]
        print(f"INDEX_RAW_EQUAL={'YES' if INDEX_RAW_EQUAL else 'NO'}")
        # entry 级对拍
        s_ents = index_entries_from_raw(s_idx["idx_raw"], s_idx["index_count_field"])
        c_ents = index_entries_from_raw(c_idx["idx_raw"], c_idx["index_count_field"])
        n = min(len(s_ents), len(c_ents))
        struct_same = (len(s_ents) == len(c_ents))
        order_same = True
        for i in range(n):
            a, b = s_ents[i], c_ents[i]
            if a.get("raw") != b.get("raw"):
                struct_same = False
            if a.get("type") != b.get("type") or a.get("instance") != b.get("instance"):
                order_same = False
        # 排序是否一致 (按 TGI)
        s_keys = [(e.get("type"), e.get("group"), e.get("instance")) for e in s_ents]
        c_keys = [(e.get("type"), e.get("group"), e.get("instance")) for e in c_ents]
        print(f"INDEX_STRUCTURE_EQUAL={'YES' if struct_same else 'NO'} "
              f"(含原样 raw entry 全等={struct_same})")
        INDEX_STRUCTURE_EQUAL = struct_same or bool(INDEX_RAW_EQUAL)
        print(f"INDEX_ORDER_SAME={'YES' if order_same and s_keys == c_keys else 'NO'}")
        print("--- per-entry raw + decoded (前若干, 差异的用 '!' 标出) ---")
        diff_shown = 0
        for i in range(n):
            a, b = s_ents[i], c_ents[i]
            if a.get("err") or b.get("err"):
                continue
            same_e = a["raw"] == b["raw"]
            flag = "  " if same_e else "! "
            if not same_e and diff_shown < 60:
                diff_shown += 1
                print(f"{flag}[{i}] {a['type']:08X}/0x{a['group']:08X}/{a['instance']} "
                      f"off={a['offset_phys']}(cb={int(a['offset_comp_bit'])}/{int(b['offset_comp_bit'])}) "
                      f"sz={a['size_phys']}(cb={int(a['size_comp_bit'])}/{int(b['size_comp_bit'])}) "
                      f"f7={a['M1_field7_hex']}/{b['M1_field7_hex']} f8={a['M1_field8_hex']}/{b['M1_field8_hex']}")
            elif same_e and i < 3:
                print(f"  [{i}] {a['type']:08X}/0x{a['group']:08X}/{a['instance']} (完全一致)")
        if diff_shown == 0 and struct_same:
            print("  (所有 entry raw 完全一致)")

    # ---- C. RESOURCE PHYSICAL LAYOUT ---------------------------------------
    print("\n--- C. RESOURCE PHYSICAL LAYOUT ---")
    s_do = collect_data_offsets(s_idx)
    c_do = collect_data_offsets(c_idx)
    s_met = compute_layout_metrics(sp, s_do, src_size)
    c_met = compute_layout_metrics(cp, c_do, ctl_size)
    for label, m in (("SOURCE", s_met), ("CONTROL", c_met)):
        print(f"  {label}: resource_count={m['resource_count']} offsets_ascending={m['offsets_ascending']} "
              f"aligned_to_4={m['aligned_to_4']} unaligned={len(m['unaligned_resources'])} "
              f"overlap={len(m['overlapping'])} oob={len(m['out_of_bounds'])} gaps={len(m['gaps'])}")
        if m["gaps"]:
            print(f"    gaps(prev_end,next_off,size): {m['gaps'][:10]}")
    tgis = set(s_do) | set(c_do)
    phys_diffs = []
    for tgi in sorted(tgis):
        d = s_do.get(tgi); e = c_do.get(tgi)
        if d == e:
            continue
        phys_diffs.append(tgi)
        print(f"  LAYOUT_DIFF {tgi}: src=off{d['phys_off'] if d else '?', d['stored_size'] if d else '?'} "
              f"ctl=off{e['phys_off'] if e else '?', e['stored_size'] if e else '?'}")
    RESOURCE_LAYOUT_EQUAL = not phys_diffs
    ALIGNMENT_EQUAL = (s_met["aligned_to_4"] == c_met["aligned_to_4"]
                       and s_met["unaligned_resources"] == c_met["unaligned_resources"])
    print(f"RESOURCE_LAYOUT_EQUAL={'YES' if RESOURCE_LAYOUT_EQUAL else 'NO'}")
    print(f"ALIGNMENT_EQUAL={'YES' if ALIGNMENT_EQUAL else 'NO'}")

    # ---- D. FILE TRAILER / UNKNOWN DATA / source-only ranges ----------------
    print("\n--- D. FILE TRAILER / UNKNOWN / SOURCE-ONLY RANGES ---")
    with open(sp, "rb") as f:
        s_data = f.read()
    with open(cp, "rb") as f:
        c_data = f.read()

    def index_span(idx, data_len):
        if not idx.get("ok"):
            return None
        ioff = idx["index_offset"]
        # 分辨 index 区实际结束: 从 ioff+4·0x28 到 index_size 尾部
        return (ioff, ioff + idx["index_size"])

    s_span = index_span(s_idx, len(s_data))
    c_span = index_span(c_idx, len(c_data))

    def resource_covers(data_off_map):
        cov = set()
        for d in data_off_map.values():
            for b in range(d["phys_off"], d["end"]):
                cov.add(b)
        return cov

    s_res_cov = resource_covers(s_do)
    c_res_cov = resource_covers(c_do)

    def source_only_ranges(label, data, res_cov, span, data_len):
        """找出既不属于资源 body、也不属于 index 区的字节区段 (gap/trailer/unknown)。"""
        ranges = []
        occupied = set(res_cov)
        if span:
            occupied.update(range(span[0], span[1]))
        i = 0
        while i < data_len:
            if i in occupied:
                i += 1
                continue
            j = i
            while j < data_len and j not in occupied:
                j += 1
            ranges.append((i, j, data[i:j].hex(" ")[:120]))
            print(f"  {label} UNKNOWN_RANGE 0x{i:08X}..0x{j:08X} len={j - i} bytes={data[i:j].hex(' ')[:120]}")
            i = j + 1
        return ranges

    print(f"  SOURCE index_span={s_span} CONTROL index_span={c_span}")
    s_only = source_only_ranges("SOURCE", s_data, s_res_cov, s_span, len(s_data))
    c_only = source_only_ranges("CONTROL", c_data, c_res_cov, c_span, len(c_data))
    # EOF / trailer
    def trailer_desc(data, data_off_map, data_len):
        ends = [d["end"] for d in data_off_map.values()] or [0]
        last_body_end = max(ends)
        tail = data[last_body_end:data_len]
        return last_body_end, len(tail)
    s_last, s_tail = trailer_desc(s_data, s_do, len(s_data))
    c_last, c_tail = trailer_desc(c_data, c_do, len(c_data))
    print(f"  SOURCE last_body_end=0x{s_last:X} trailer_len={s_tail}")
    print(f"  CONTROL last_body_end=0x{c_last:X} trailer_len={c_tail}")
    TRAILER_EQUAL = (s_only == [] and c_only == [] and s_tail == c_tail)
    print(f"SOURCE_ONLY_BYTE_RANGES={len(s_only)}")
    print(f"CONTROL_ONLY_BYTE_RANGES={len(c_only)}")
    print(f"TRAILER_EQUAL={'YES' if TRAILER_EQUAL else 'NO'}")

    # ---- E. GAME ERROR CODE 搜索 -------------------------------------------
    print("\n--- E. GAME ERROR CODE BYTES 搜索 (non-endian + LE 形式) ---")
    err_components = ["60c399f9", "de4ee14f", "29f00e76", "4b0d852b"]
    needles = []
    for hx in err_components:
        b = bytes.fromhex(hx)
        needles.append({"name": f"{hx} (BE)", "bytes": b})
        needles.append({"name": f"{hx} (LE)", "bytes": b[::-1]})
    hits = search_bytes(sp, needles, "SOURCE") + search_bytes(cp, needles, "CONTROL")
    if hits:
        for h in hits:
            print(f"  HIT {h['file']} {h['name']} @0x{h['offset']:08X} ctx={h['context']}")
        GAME_ERROR_COMPONENT_MATCH = "FOUND"
    else:
        print("  GAME_ERROR_COMPONENT_MATCH=NONE_FOUND")
        GAME_ERROR_COMPONENT_MATCH = "NONE_FOUND"

    # ---- F. 汇总 -----------------------------------------------------------
    print("\n" + "=" * 78)
    idx_struct = (INDEX_RAW_EQUAL, INDEX_STRUCTURE_EQUAL)
    summary = {
        "SOURCE_SHA": src_sha,
        "CONTROL_SHA": ctl_sha,
        "HEADER_BYTE_EQUAL": HEADER_BYTE_EQUAL,
        "HEADER_DIFF_FIELDS": diff_fields if diff_fields else "NONE",
        "SOURCE_INDEX_OFFSET": s_idx.get("index_offset") if s_idx.get("ok") else None,
        "CONTROL_INDEX_OFFSET": c_idx.get("index_offset") if c_idx.get("ok") else None,
        "SOURCE_INDEX_AT_END": bool(s_idx.get("index_at_end")) if s_idx.get("ok") else None,
        "CONTROL_INDEX_AT_END": bool(c_idx.get("index_at_end")) if c_idx.get("ok") else None,
        "INDEX_RAW_EQUAL": bool(idx_struct[0]) if idx_struct and idx_struct[0] is not None else None,
        "INDEX_STRUCTURE_EQUAL": bool(idx_struct[1]) if idx_struct and idx_struct[1] is not None else None,
        "RESOURCE_LAYOUT_EQUAL": RESOURCE_LAYOUT_EQUAL,
        "ALIGNMENT_EQUAL": ALIGNMENT_EQUAL,
        "SOURCE_ONLY_BYTE_RANGES": len(s_only),
        "CONTROL_ONLY_BYTE_RANGES": len(c_only),
        "UNKNOWN_HEADER_FIELDS_EQUAL": UNKNOWN_HEADER_FIELDS_EQUAL,
        "TRAILER_EQUAL": TRAILER_EQUAL,
        "GAME_ERROR_COMPONENT_MATCH": GAME_ERROR_COMPONENT_MATCH,
    }
    print("PHYSICAL_FORENSIC_SUMMARY:")
    for k, v in summary.items():
        print(f"  {k}={v}")
    print(f"ZERO_WRITE_TO_MODS=YES")
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\nJSON 已写出: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
