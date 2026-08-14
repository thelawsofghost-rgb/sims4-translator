#!/usr/bin/env python3
"""Phase 3B MVP: 单条 STBL patch 工具 — 正确的 DBPF 重排 write (不覆盖原文件)

输入:
  python patch_stbl.py input.package output.package mapping.csv
  python patch_stbl.py --inspect input.package        (只读列出 locale 0x01 keys)
  python patch_stbl.py --diag input.package ...       (只读结构诊断)

设计 (v3, 修复游戏"文件损坏"):
  根因诊断 (2026-08-14, 真包 diag + 复现):
    本包 STBL 存储为 **raw (未压缩)**, size=130, 5 keys。
    v2 用 stbl_surgical_edit 把短文本 (左=3B) 写进原槽 (Left=4B) 并把长度字段改成 3
    -> STBL 解析器按 7+len 推进下一条目 (7+3=10), 但物理槽是 7+4=11
    -> 后续 key 全部错位 -> STBL 结构损坏 -> 游戏报文件损坏。
    我的 VERI 只检查目标 key 所以漏过; 复现测试确认解码失败。

  v3 改为:**重建整个目标 STBL** (结构正确, 其余 key/flags 原样) +
    **正确的 DBPF relayout**:
      - header 原样复制 (仅 index_offset@0x40 重算)
      - 每个未编辑 resource 的 body 原字节逐字节复制 (不重压缩/不改内容)
      - 仅目标 STBL 的新 body 替换;
        目标之后的 body 顺移 delta, index entry offset 同步 +delta
      - index entry 保留原始 32 字节 (type/group/inst/flags/reserved 原样),
        只改受影响 offset 与被编辑 STBL 的 size
      - comp_flag@0x3C / entry flags(压缩类型) 原样保留
      - 绝对 index (文件末尾), index_offset 重算
  验证:
    [B-DIFF] 未编辑 resource 的 body 内容+metadata 逐字节一致
    [VERI]   回读解码 — 目标 key 新文本, 未命中 key 原样

映射列 (与 translation 输出一致):
  translation_id (形如 T_<hash>_g1) | new_text
"""
import sys, csv, struct, zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse

STBL_TID = 0x220557DA
LOCALE_CHS = 0x01          # 官方确认: 0x01 = 简体中文

# ---------- STBL v5 编码/解码 (对称于 canonical map_pose_texts.parse_stbl) ----------

def _z_or_raw(data: bytes) -> bytes:
    if data[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        try:
            return zlib.decompress(data)
        except Exception:
            pass
    return data


def stbl_decode(body: bytes) -> list:
    """解析 STBL v5 -> [(keyHash:int, text:str, flags:int, raw_len:int)]。
    布局 (与 canonical 读取对称, WW CHS = 6756 实证):
      @0-3 'STBL'  @4-5 version<H>=5  @6 flag byte  @7-14 count<Q>
      @15-16 reserved  @17-20 str_len<I>  @21 第一个 entry
    entry: keyHash<u32> flags<u8> len<u16> text<utf8>"""
    if len(body) < 21 or body[0:4] != b"STBL":
        return None
    if int.from_bytes(body[4:6], "little") != 5:
        return None
    n = int.from_bytes(body[7:15], "little")
    o = 21
    out = []
    for _ in range(n):
        if o + 7 > len(body):
            return None
        kh = int.from_bytes(body[o:o + 4], "little")
        flags = body[o + 4]
        ln = int.from_bytes(body[o + 5:o + 7], "little")
        o += 7
        if o + ln > len(body):
            return None
        txt = body[o:o + ln].decode("utf-8", "replace")
        out.append((kh, txt, flags, ln))
        o += ln
    if int.from_bytes(body[7:15], "little") != len(out):
        return None
    return out


def stbl_encode(records: list) -> bytes:
    """重建 STBL v5 body。records: [(keyHash:int, text:str, flags:int)]"""
    body = bytearray(b"STBL")
    body += struct.pack("<H", 5)
    body += b"\x00"
    body += struct.pack("<Q", len(records))
    body += b"\x00\x00"
    body += struct.pack("<I", 0)
    for kh, txt, flags in records:
        tb = txt.encode("utf-8")
        if len(tb) > 0xFFFF:
            raise ValueError(f"文本超长: key=0x{kh:08X} len={len(tb)}")
        body += struct.pack("<I", kh)
        body += bytes([flags & 0xFF])
        body += struct.pack("<H", len(tb))
        body += tb
    return bytes(body)


# ---------- DBPF 读取 ----------

def read_index_only(path: str):
    """只读 DBPF 索引。返回 (file_bytes, idx)。"""
    raw_file = open(path, "rb").read()
    idx, err = safe_parse(path)
    if err or idx is None:
        raise RuntimeError(f"DBPF 解析失败: {err}")
    return raw_file, idx


def find_chs_stbl(entries: list):
    """找 locale byte == 0x01 的 STBL 实例 -> 返回 (entry, raw_body)。"""
    for e in entries:
        if e.type_id == STBL_TID and ((e.instance_id >> 56) & 0xFF) == LOCALE_CHS:
            return e
    return None


def body_of(raw_file: bytes, entry) -> bytes:
    return raw_file[entry.offset:entry.offset + (entry.size & 0x7FFFFFFF)]


def is_compressed(entry) -> bool:
    # dbpf_fast 已把 offset/size 高 1 位剥到 entry.is_compressed; 不要再从 offset 读标记
    return bool(getattr(entry, "is_compressed", False))


def build_dbpf_relayout(raw: bytes, entries: list, edit_instance: int, new_body: bytes):
    """正确的 DBPF 重排 (v3): 保留所有头部/index/metadata 原字节,
    仅重排 body 区以容纳目标 STBL 的新长度。

    原则:
      - header 0x44 原样复制 (仅最后改 index_offset@0x40)
      - 每个未编辑 resource 的 body **原字节逐字节复制** (不重压缩/不改内容)
      - 被编辑的 STBL 用 new_body 替换
      - 目标 STBL 之后的所有 body 顺移 delta; 其 index entry 的 offset 同步 +delta
      - index entry 保留原始 32 字节 (type/group/inst/flags/reserved 原样),
        只改受影响的 offset (段内字段) 与被编辑 STBL 的 size
      - comp_flag@0x3C / 各 entry flags(压缩类型) 原样保留
      - 绝对 index (在文件末尾), index_offset 重算

    返回 (new_file_bytes, new_off_by_inst) 或抛错。"""
    import struct as _st
    # 原 index 位置 (绝对)
    io = _st.unpack("<I", raw[0x40:0x44])[0]
    cnt = _st.unpack("<I", raw[0x24:0x28])[0]
    assert cnt == len(entries), "index count 与解析不一致"
    # 读取每条 entry 的原始 32 字节 (按 index 顺序)
    raw_entries = [raw[io + 4 + i * 32: io + 4 + i * 32 + 32] for i in range(cnt)]
    for re in raw_entries:
        if len(re) != 32:
            raise ValueError("index entry 越界")
    # body: 原 offset 排序 (保证 body 区按物理位置重排)
    body_order = sorted(range(cnt), key=lambda i: entries[i].offset)
    tgt_idx = next((i for i, e in enumerate(entries) if e.instance_id == edit_instance), None)
    if tgt_idx is None:
        raise ValueError("编辑目标 STBL 不在索引中")
    first_off = min(e.offset for e in entries)
    # header + pre-body 区 ([0, first_off)) 原样复制 (含 header, 使 len(out) 即真实 body 偏移)
    out = bytearray(raw[:first_off])
    orig_tgt_off = entries[tgt_idx].offset
    new_off_by_inst = {}
    for i in body_order:
        e = entries[i]
        if i == tgt_idx:
            new_off_by_inst[e.instance_id] = (len(out), len(new_body))
            out += new_body
        else:
            body = raw[e.offset:e.offset + e.size]
            if len(body) != e.size:
                raise ValueError(f"resource 0x{e.instance_id:016X} body 越界")
            new_off_by_inst[e.instance_id] = (len(out), e.size)
            out += body
    new_index_off = len(out)
    # 重建 index: 保留原始 32 字节, 更新受影响 offset/size
    new_index = bytearray(4)  # 索引区 padding
    for i in range(cnt):
        re = bytearray(raw_entries[i])
        # offset 字段的压缩标记 = 该 u32 的最高位 (bit31) = 第4字节(byte19) 的 bit7
        highbit = raw_entries[i][19] & 0x80
        ho, _ = new_off_by_inst[entries[i].instance_id]
        _st.pack_into("<I", re, 16, (ho & 0x7FFFFFFF) | (highbit << 24))
        if entries[i].instance_id == edit_instance:
            _st.pack_into("<I", re, 20, len(new_body))
        new_index += re
    # 更新 header index_offset (out 已含 header @[0x44))
    struct.pack_into("<I", out, 0x40, new_index_off)
    return bytes(out) + bytes(new_index), new_off_by_inst


def byte_diff_verify_resources(orig: bytes, new: bytes, edit_instance: int):
    """v3 验证: 未编辑 resource 的 body 内容必须逐字节一致。
    位置可偏移 (relayout), 但内容字节必须完全相同; 实例集合必须一致。"""
    import struct as _st
    def bodies_of(raw):
        io = _st.unpack("<I", raw[0x40:0x44])[0]
        cnt = _st.unpack("<I", raw[0x24:0x28])[0]
        res = {}
        for i in range(cnt):
            base = io + 4 + i * 32
            e = raw[base:base + 32]
            if len(e) < 24:
                continue
            tid, gid, hi, lo, off, sz = _st.unpack("<IIIIII", e[:24])
            b_off = off & 0x7FFFFFFF
            b_sz = sz & 0x7FFFFFFF
            inst = (hi << 32) | lo
            res[inst] = (raw[b_off:b_off + b_sz], tid, gid, off & 0x80000000, sz & 0x80000000)
        return res
    bo = bodies_of(orig); bn = bodies_of(new)
    if set(bn.keys()) != set(bo.keys()):
        return False, f"inst 集合不一致: orig {len(bo)} vs new {len(bn)}"
    for inst, (b, tid, gid, ch, sh) in bo.items():
        if inst == edit_instance:
            continue
        nb, nptid, npid, nch, nsh = bn[inst]
        if nb != b:
            return False, f"resource 0x{inst:016X} body 内容不一致"
        if (nptid, npid) != (tid, gid) or nch != ch or nsh != sh:
            return False, f"resource 0x{inst:016X} metadata (tid/group/comp位) 变化"
    return True, None


# ---------- mapping ----------

def load_mapping(csv_path: str) -> dict:
    m = {}
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        if not r.fieldnames:
            return m
        id_col = next((c for c in r.fieldnames if c.strip().lower() in
                       ("translation_id", "tid", "id", "key")), r.fieldnames[0])
        txt_col = next((c for c in r.fieldnames if c.strip().lower() in
                        ("new_text", "translation", "text", "zh")), None)
        if txt_col is None:
            txt_col = r.fieldnames[1] if len(r.fieldnames) > 1 else None
        for row in r:
            tid = (row.get(id_col) or "").strip()
            txt = (row.get(txt_col) or "").strip() if txt_col else ""
            if tid:
                m[tid] = txt
    return m


def tid_to_hash(tid: str):
    s = tid.strip()
    for tok in s.split("_"):
        if 8 <= len(tok) <= 16:
            try:
                return int(tok, 16) & 0xFFFFFFFF
            except ValueError:
                continue
    return None


# ---------- main ----------

def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--inspect":
        p = sys.argv[2] if len(sys.argv) >= 3 else None
        if p is None:
            print("用法: python patch_stbl.py --inspect input.package"); return 2
        if not Path(p).exists():
            print(f"[ERROR] 输入不存在: {p}"); return 1
        raw, idx = read_index_only(p)
        chs = find_chs_stbl(idx.entries)
        if chs is None:
            print(f"[ERROR] 无 locale 0x01 STBL"); return 1
        recs = stbl_decode(_z_or_raw(body_of(raw, chs)))
        if recs is None:
            print("[ERROR] STBL 结构不符"); return 1
        print(f"locale=0x01 inst=0x{chs.instance_id:016X} keys={len(recs)}")
        for kh, txt, flags, _ in recs:
            print(f"  0x{kh:08X}  T_{kh:08x}_g1  {txt!r}")
        return 0

    if len(sys.argv) >= 2 and sys.argv[1] == "--diag":
        """只读结构诊断: dump header/index entry 原始字节 + body 头部十六进制,
        运行在原包与输出包上, 用于定位游戏拒绝加载的结构差异。"""
        for p in sys.argv[2:]:
            if not Path(p).exists():
                print(f"[ERROR] 不存在: {p}"); continue
            print(f"==== {p} ====")
            raw = open(p, "rb").read()
            print(f"文件大小: {len(raw)} bytes")
            if raw[0:4] != b"DBPF":
                print("[SKIP] 非 DBPF magic"); continue
            import struct as _st
            major, minor = _st.unpack("<II", raw[0x04:0x0C])
            flags = raw[0x0C:0x10].hex()
            entry_count = _st.unpack("<I", raw[0x24:0x28])[0]
            index_size = _st.unpack("<I", raw[0x2C:0x30])[0]
            index_offset = _st.unpack("<I", raw[0x40:0x44])[0]
            compflag = raw[0x3C:0x40].hex()
            is_rel = index_offset + index_size > len(raw) + 4
            print(f"major={major} minor={minor} flags@0x0C={flags}"
                  f" count@0x24={entry_count} index_size@0x2C={index_size}"
                  f" index_offset@0x40={index_offset} (rel? {is_rel}) range={index_offset}..{index_offset+index_size}"
                  f" comp_flag@0x3C={compflag}")
            # 重算 index 实际位置: rel -> len-index_offset
            io = index_offset if not is_rel else (len(raw) - index_offset)
            print(f"  index 实际绝对偏移={io}")
            if io + 4 + entry_count * 32 > len(raw) + 4:
                print("  [WARN] index 区超 file")
            for i in range(entry_count):
                base = io + 4 + i * 32
                e = raw[base:base + 32]
                if len(e) < 32:
                    print(f"  [i={i}] entry 不完整"); break
                tid, gid, hi, lo, off, sz, fl, rs = _st.unpack("<IIIIIIII", e)
                comp = bool(off & 0x80000000)
                b_off = off & 0x7FFFFFFF
                b_sz = sz & 0x7FFFFFFF
                body_hdr = raw[b_off:b_off + 8].hex()
                tag = "STBL" if tid == STBL_TID else "    "
                loc = ((hi << 32 | lo) >> 56) & 0xFF if tid == STBL_TID else 0
                print(f"  [i={i}] {tag} tid=0x{tid:08X} group=0x{gid:08X}"
                      f" inst=0x{(hi<<32)|lo:016X}"
                      f" offset={b_off} size={b_sz} comp={comp}"
                      f" rawFlags=0x{fl:08X} rawReserved=0x{rs:08X}"
                      f" body8={body_hdr}" + (f" locale=0x{loc:02X}" if tid == STBL_TID else ""))
        return 0

    if len(sys.argv) != 4:
        print("用法: python patch_stbl.py input.package output.package mapping.csv")
        print("  mapping.csv 列: translation_id (T_<hash>_g1) + new_text")
        return 2

    in_path, out_path, map_path = sys.argv[1], sys.argv[2], sys.argv[3]
    if not Path(in_path).exists():
        print(f"[ERROR] 输入不存在: {in_path}"); return 1

    # 读原包 (整体字节 + 索引)
    raw, idx = read_index_only(in_path)
    entries = idx.entries

    # 找 CHS STBL
    chs = find_chs_stbl(entries)
    if chs is None:
        print(f"[ERROR] 包内无 locale 0x01 STBL (STBL 共 {sum(1 for e in entries if e.type_id==STBL_TID)} 个)")
        return 1
    chs_raw = body_of(raw, chs)
    chs_body = _z_or_raw(chs_raw)
    recs = stbl_decode(chs_body)
    if recs is None:
        print(f"[ERROR] CHS STBL 结构不符, 拒写 (locale=0x01 inst=0x{chs.instance_id:016X})")
        return 1
    orig_compressed = is_compressed(chs)
    print(f"[STBL] locale=0x01 inst=0x{chs.instance_id:016X} keys={len(recs)} "
          f"size={chs.size} compressed={orig_compressed}")

    # mapping
    mapping = load_mapping(map_path)
    if not mapping:
        print("[ERROR] mapping.csv 无数据"); return 1
    print(f"[MAP ] mapping 条数={len(mapping)}")

    # 命中 key
    hash_to_tid = {}
    for tid in mapping:
        h = tid_to_hash(tid)
        if h is not None:
            hash_to_tid.setdefault(h, []).append(tid)
    existing = {kh for kh, _, _, _ in recs}
    to_patch = [(kh, tid) for kh, tids in hash_to_tid.items() if kh in existing for tid in tids]
    if not to_patch:
        print("[ERROR] mapping 无 key 命中 CHS STBL 现存 keyHash")
        print(f"        现存 {len(existing)} 个 key 样例: " +
              ", ".join(f"0x{h:08X}" for h in list(existing)[:5]))
        return 1
    print(f"[HIT ] 将修改 {len(to_patch)} 个 key")
    if len(to_patch) > 1:
        print(f"[WARN] MVP 预期单条, 实际命中 {len(to_patch)} 条 — 将全部应用")

    # 命中 key (只需一个; 逐 key 原位手术)
    hash_to_tid = {}
    for tid in mapping:
        h = tid_to_hash(tid)
        if h is not None:
            hash_to_tid.setdefault(h, []).append(tid)
    existing = {kh for kh, _, _, _ in recs}
    to_patch = [kh for kh in hash_to_tid if kh in existing]
    if not to_patch:
        print("[ERROR] mapping 无 key 命中 CHS STBL 现存 keyHash")
        print(f"        现存 {len(existing)} 个 key 样例: " +
              ", ".join(f"0x{h:08X}" for h in list(existing)[:5]))
        return 1
    print(f"[HIT ] 将修改 {len(to_patch)} 个 key: " +
          ", ".join(f"0x{h:08X}" for h in to_patch))

    # 重建整个 CHS STBL (全量, 正确的 STBL 结构; 其余 key 原样保留)
    # 对每个命中 key 应用翻译, 未命中 key 保留原文本/flags
    new_recs = []
    applied = 0
    for kh, txt, flags, _ in recs:
        if kh in hash_to_tid:
            nt = mapping[hash_to_tid[kh][0]]
            if nt != txt:
                applied += 1
            new_recs.append((kh, nt, flags))
        else:
            new_recs.append((kh, txt, flags))
    if applied == 0:
        print("[ERROR] mapping 命中但文本与现有一致 (无实际变更)"); return 1
    try:
        new_body_stored = stbl_encode(new_recs)
    except ValueError as e:
        print(f"[ERROR] STBL 编码失败: {e}"); return 1
    # 存储方式跟随原 STBL: 压缩->zlib, raw->raw (保持压缩表示一致)
    if orig_compressed:
        cands = sorted((zlib.compress(new_body_stored, lv) for lv in range(1, 10)), key=len)
        new_body = cands[0]
        print(f"[OK  ] STBL 重建: {len(new_recs)} keys, 应用 {applied} 条, "
              f"解压后 {len(new_body_stored)}B / 存储(zlib) {len(new_body)}B (原 {chs.size}B)")
    else:
        new_body = new_body_stored
        print(f"[OK  ] STBL 重建: {len(new_recs)} keys, 应用 {applied} 条, "
              f"新 body(raw) {len(new_body)}B (原 {chs.size}B)")

    # 正确的 DBPF 重排: 装下新长度 STBL, 其余 resource 原字节复制
    try:
        new_file, new_offs = build_dbpf_relayout(raw, entries, chs.instance_id, new_body)
    except (ValueError, AssertionError) as e:
        print(f"[ERROR] 重排失败: {e}"); return 1

    Path(out_path).write_bytes(new_file)
    print(f"[SAVE] 已写入: {out_path} ({len(new_file)} 字节)")

    # 验证1: 未编辑 resource 的 body 内容逐字节一致 + metadata(comp位)一致
    ok, why = byte_diff_verify_resources(raw, new_file, chs.instance_id)
    if not ok:
        print(f"[B-DIFF] FAIL: {why} — 结构被破坏, 拒发"); return 1
    print(f"[B-DIFF] PASS: 除目标 STBL 外, 其余 {len(entries)-1} 个 resource 的 body 内容/元数据逐字节一致")

    # 验证2: 回读解码确认
    try:
        vraw, vidx = read_index_only(out_path)
        vchs = find_chs_stbl(vidx.entries)
        if not vchs:
            print("[VERI] FAIL: 回读未找到 CHS STBL"); return 1
        vrecs = stbl_decode(_z_or_raw(body_of(vraw, vchs)))
        if not vrecs:
            print("[VERI] FAIL: 回读 STBL 无法解码"); return 1
        vmap = {kh: t for kh, t, _, _ in vrecs}
        ok_all = True
        for kh in to_patch:
            expect = mapping[hash_to_tid[kh][0]]
            got = vmap.get(kh)
            ok = (got == expect)
            ok_all &= ok
            print(f"     {('OK ' if ok else 'FAIL')} 0x{kh:08X}: {got!r} (期望 {expect!r})")
        # 未命中 key 也必须原样
        for kh, txt, _, _ in recs:
            if kh not in hash_to_tid and vmap.get(kh) != txt:
                ok_all = False
                print(f"     FAIL 未命中 key 0x{kh:08X} 被改动: {vmap.get(kh)!r} != {txt!r}")
        if not ok_all:
            print("[VERI] FAIL"); return 1
        print(f"[VERI] PASS: 回读 {len(vrecs)} keys, 改造 key 已写入, 未命中 key 原样")
    except Exception as e:
        print(f"[VERI] 回读异常: {e}"); return 1

    print("[DONE] MVP patch 完成 (原文件未动)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
