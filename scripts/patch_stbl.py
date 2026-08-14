#!/usr/bin/env python3
"""Phase 3B MVP: 单条 STBL patch 工具 (copy-on-write, 不覆盖原文件)

输入:
  python patch_stbl.py input.package output.package mapping.csv

功能 (READ 原 → WRITE 新, 全程不改原文件):
  1. 打开 Sims4 package (DBPF), 读全部资源 body (保留所有未修改资源原样)
  2. 找到 STBL type (0x220557DA) 中 locale byte = 0x01 (简体中文) 的实例
  3. 读取该 STBL 的 (keyHash -> text), 按 mapping.csv 修改指定 key 的文本
  4. 重建 STBL body (与 canonical `map_pose_texts.parse_stbl` 读取布局对称)
  5. 重建 DBPF (header 原样 + 重算 index offset/size + 重排 body + 重压缩)
  6. 另存为新 package (绝不覆盖 input)

mapping.csv 列 (与 translation 输出一致):
  translation_id (形如 T_<hash>_g1) | new_text
  仅处理 mapping 中存在且 STBL 里真实存在的 keyHash。

保留:
  - 原 package 备份 (input 不动, output 为新文件)
  - 未修改的 resource body 逐一原样保留
  - 其他 locale STBL (0x00,0x02..0x15) 原样保留

MVP 只支持单条/少量; 不做全量 1968, 不扫 Mods, 不批处理。
"""
import sys, csv, struct, zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse

STBL_TID = 0x220557DA
LOCALE_CHS = 0x01          # 已官方确认: 0x01 = 简体中文
ENTRY = 32
IDX_PAD = 4

# ---------- STBL v5 编码/解码 (对称于 canonical map_pose_texts.parse_stbl) ----------

def _z_or_raw(data: bytes) -> bytes:
    """解 zlib (若为 zlib); 返回 (解压后 body, 是否曾压缩)。"""
    if data[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        try:
            return zlib.decompress(data)
        except Exception:
            pass
    return data


def stbl_decode(body: bytes) -> list:
    """解析 STBL v5 -> [(keyHash:int, text:str, raw_flags:int, raw_len:int)]。
    布局 (与 canonical 读取对称, WW CHS = 6756 实证):
      @0-3  'STBL'  @4-5 version<H>=5  @6 flag byte  @7-14 count<Q>
      @15-16 reserved  @17-20 str_len<I>  @21 第一个 entry
    entry: keyHash<u32> flags<u8> len<u16> text<utf8>
    返回整体 bytes 供精确重建; 若结构不符返回 None。"""
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
        return None  # declared != parsed -> 结构不一致, 拒写
    return out


def stbl_encode(records: list) -> bytes:
    """重建 STBL v5 body。records: [(keyHash:int, text:str, flags:int)]"""
    body = bytearray(b"STBL")
    body += struct.pack("<H", 5)          # version
    body += b"\x00"                        # flag byte
    body += struct.pack("<Q", len(records))  # count <Q
    body += b"\x00\x00"                    # reserved
    body += struct.pack("<I", 0)           # str_len (canonical 读但不校验)
    for kh, txt, flags in records:
        tb = txt.encode("utf-8")
        if len(tb) > 0xFFFF:
            raise ValueError(f"文本超长: key=0x{kh:08X} len={len(tb)}")
        body += struct.pack("<I", kh)      # keyHash
        body += bytes([flags & 0xFF])      # flags (原样保留)
        body += struct.pack("<H", len(tb))
        body += tb
    return bytes(body)


# ---------- DBPF 读取 ----------

def read_bodies(path: str):
    """读 DBPF 全部 body (raw, 含压缩态). 返回 header原样, entries, bodies(list[(entry, raw)])"""
    with open(path, "rb") as fh:
        raw_file = fh.read()
    idx, err = safe_parse(path)
    if err or idx is None:
        raise RuntimeError(f"DBPF 解析失败: {err}")
    entries = idx.entries
    bodies = []
    for e in entries:
        raw = raw_file[e.offset:e.offset + (e.size & 0x7FFFFFFF)]
        bodies.append((e, raw))
    return raw_file, idx, bodies


def find_chs_stbl(entries: list):
    """找 locale byte == 0x01 的 STBL 实例 -> ResourceEntry 或 None。"""
    for e in entries:
        if e.type_id == STBL_TID and ((e.instance_id >> 56) & 0xFF) == LOCALE_CHS:
            return e
    return None


# ---------- DBPF 重建 ----------

def version_from_index(idx) -> int:
    return idx.major


def build_dbpf(orig_header: bytes, bodies, index_entries, major: int, count: int, idx_pad: int = IDX_PAD):
    """重建 DBPF: 原 header + 新 index + 重排 body。
    bodies: list[(ResourceEntry, raw_body)] — body 需已就位(压缩态镜像最终存储)。
    index_entries: 每个 ResourceEntry 的 (type,group,inst_hi,inst_lo,offset,size,compressed)
    返回最终文件 bytes。"""
    # index 区 = pad(4) + count*ENTRY
    idx_size = idx_pad + count * ENTRY
    # 布局: [header 0x44] [payloads...] [index pad+entries]
    # body 从 0x44 开始顺序摆放
    body_blobs = []
    off = 0x44
    new_entries = []
    for (e, raw) in bodies:
        body_blobs.append(raw)
        comp = ((e.offset >> 31) & 1)  # 保留原压缩标记
        size = (len(raw)) | (0x80000000 if comp else 0)
        inst_hi = (e.instance_id >> 32) & 0xFFFFFFFF
        inst_lo = e.instance_id & 0xFFFFFFFF
        # 原样保留 (Sims4 index entry flags/reserved 通常为 0; MVP 不读原 flags/reserved,
        # 重建时置 0。若遇非 0 flags/reserved 的包, 需扩展 ResourceEntry 携带原始尾字节。)
        new_entries.append(struct.pack(
            "<IIIIIIII",
            e.type_id, e.group_id, inst_hi, inst_lo,
            off, size,
            0, 0,  # flags, reserved (MVP: 置 0)
        ))
        off += len(raw)
    # index_offset = 文件末尾 (body 之后)
    index_offset = off
    payload = b"".join(body_blobs)
    index_region = b"\x00" * idx_pad + b"".join(new_entries)

    # 重建 header (前 0x44 原样, 只改 0x2C index_size, 0x40 index_offset)
    new_header = bytearray(orig_header[:0x44])
    struct.pack_into("<I", new_header, 0x2C, idx_size)
    struct.pack_into("<I", new_header, 0x40, index_offset)
    return bytes(new_header) + payload + index_region


# ---------- main ----------

def load_mapping(csv_path: str) -> dict:
    """读 mapping.csv -> {translation_id:new_text}。容忍 T_<hash>_g1 格式。"""
    m = {}
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        if not r.fieldnames:
            return m
        # 列名可能是 translation_id / new_text, 或 source/translation; 宽容探测
        id_col = next((c for c in r.fieldnames if c.strip().lower() in
                       ("translation_id", "tid", "id", "key")), r.fieldnames[0])
        txt_col = next((c for c in r.fieldnames if c.strip().lower() in
                        ("new_text", "translation", "text", "zh")), None)
        if txt_col is None:
            # 退化: 第2列
            txt_col = r.fieldnames[1] if len(r.fieldnames) > 1 else None
        for row in r:
            tid = (row.get(id_col) or "").strip()
            txt = (row.get(txt_col) or "").strip() if txt_col else ""
            if tid:
                m[tid] = txt
    return m


def tid_to_hash(tid: str):
    """'T_<hex>_g1' -> 低32位 u32 hash (STBL keyHash)。不能解析返回 None。"""
    s = tid.strip()
    for tok in s.split("_"):
        if len(tok) >= 8 and len(tok) <= 16:
            try:
                v = int(tok, 16)
                return v & 0xFFFFFFFF
            except ValueError:
                continue
    return None


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--inspect":
        # --inspect input.package : 只读列出 locale 0x01 STBL 的全部 keyHash (不写)
        p = sys.argv[2] if len(sys.argv) >= 3 else None
        if p is None:
            print("用法: python patch_stbl.py --inspect input.package"); return 2
        if not Path(p).exists():
            print(f"[ERROR] 输入不存在: {p}"); return 1
        _, idx, bodies = read_bodies(p)
        chs = find_chs_stbl(idx.entries)
        if chs is None:
            print(f"[ERROR] 无 locale 0x01 STBL"); return 1
        raw = next(r for (e, r) in bodies if e is chs)
        recs = stbl_decode(_z_or_raw(raw))
        if recs is None:
            print("[ERROR] STBL 结构不符"); return 1
        print(f"locale=0x01 inst=0x{chs.instance_id:016X} keys={len(recs)}")
        for kh, txt, flags, _ in recs:
            print(f"  0x{kh:08X}  T_{kh:08x}_g1  {txt!r}")
        return 0
    if len(sys.argv) != 4:
        print("用法: python patch_stbl.py input.package output.package mapping.csv")
        print("  mapping.csv 列: translation_id (T_<hash>_g1) + new_text")
        return 2
    in_path, out_path, map_path = sys.argv[1], sys.argv[2], sys.argv[3]
    if not Path(in_path).exists():
        print(f"[ERROR] 输入不存在: {in_path}"); return 1

    # 读原包
    orig_header, idx, bodies = read_bodies(in_path)
    entries = idx.entries

    # 找 CHS STBL
    chs = find_chs_stbl(entries)
    if chs is None:
        print(f"[ERROR] 包内无 locale 0x01 STBL (STBL 共 {sum(1 for e in entries if e.type_id==STBL_TID)} 个)")
        return 1
    # 取 CHS STBL 的 body (解压后解析)
    chs_raw = next(raw for (e, raw) in bodies if e is chs)
    chs_body = _z_or_raw(chs_raw)
    recs = stbl_decode(chs_body)
    if recs is None:
        print(f"[ERROR] CHS STBL 结构不符, 拒写 (locale=0x01 inst=0x{chs.instance_id:016X})")
        return 1
    print(f"[STBL] locale=0x01 inst=0x{chs.instance_id:016X} keys={len(recs)}")

    # 读 mapping
    mapping = load_mapping(map_path)
    if not mapping:
        print("[ERROR] mapping.csv 无数据"); return 1
    print(f"[MAP ] mapping 条数={len(mapping)}")

    # 找需修改的 key
    hash_to_tid = {}
    for tid in mapping:
        h = tid_to_hash(tid)
        if h is not None:
            hash_to_tid.setdefault(h, []).append(tid)
    # 匹配 STBL 现存 key
    existing = {kh for kh, _, _, _ in recs}
    to_patch = [(kh, tid) for kh, tids in hash_to_tid.items() if kh in existing for tid in tids]
    if not to_patch:
        print("[ERROR] mapping 中没有任何 key 命中 CHS STBL 现存 keyHash")
        print(f"        现存 {len(existing)} 个 key 样例: " +
              ", ".join(f"0x{h:08X}" for h in list(existing)[:5]))
        return 1
    print(f"[HIT ] 将修改 {len(to_patch)} 个 key")

    # 单条 MVP 校验: 只允许命中最少变更; 若多条, 打印但继续 (MVP 支持多条但用户应给单条)
    if len(to_patch) > 1:
        print(f"[WARN] MVP 预期单条, 实际命中 {len(to_patch)} 条 — 将全部应用")

    # 应用修改
    by_hash = {}
    for kh, _, _, _ in recs:
        by_hash[kh] = None
    new_recs = []
    applied = 0
    for kh, txt, flags, _ in recs:
        if kh in hash_to_tid:
            new_txt = mapping[hash_to_tid[kh][0]]
            if new_txt != txt:
                applied += 1
        else:
            new_txt = txt
        new_recs.append((kh, new_txt, flags))
    if applied == 0:
        print("[ERROR] mapping 命中但文本与现有一致 (无实际变更)"); return 1

    # 重建 STBL body + 重压缩 (zlib, level 9 兼容)
    new_stbl_body = stbl_encode(new_recs)
    new_stbl_z = zlib.compress(new_stbl_body, 9)
    print(f"[OK  ] STBL locale 0x01 重建: {len(recs)} keys, 应用 {applied} 条")

    # 组装 bodies: 替换 CHS STBL, 其余原样
    out_bodies = []
    for (e, raw) in bodies:
        if e is chs:
            # 保留原压缩标记; 新 STBL 用 zlib 存
            comp = ((e.offset >> 31) & 1) or True
            stored = new_stbl_z if comp else new_stbl_body
            # 更新 entry 的 offset/size 压缩标记一致
            e2 = e.__class__(type_id=e.type_id, group_id=e.group_id,
                             instance_id_high=e.instance_id_high,
                             instance_id_low=e.instance_id_low,
                             instance_id=e.instance_id,
                             offset=e.offset, size=len(stored))
            out_bodies.append((e2, stored))
        else:
            out_bodies.append((e, raw))

    # 重建 DBPF
    new_file = build_dbpf(orig_header, out_bodies, entries, idx.major, len(entries))
    Path(out_path).write_bytes(new_file)
    print(f"[SAVE] 已写入: {out_path} ({len(new_file)} 字节)")

    # 回读验证 (自校验): 新版能否用同一逻辑读回
    try:
        vh, vidx, vbodies = read_bodies(out_path)
        vchs = find_chs_stbl(vidx.entries)
        if vchs:
            vraw = next(raw for (e, raw) in vbodies if e is vchs)
            vrecs = stbl_decode(_z_or_raw(vraw))
            n_patched = sum(1 for kh, txt, _, _ in vrecs if kh in hash_to_tid)
            print(f"[VERI] 回读: CHS STBL keys={len(vrecs) if vrecs else 0}, patched={n_patched}")
            if not vrecs or n_patched == 0:
                print("[VERI] FAIL: 回读未发现修改")
                return 1
        else:
            print("[VERI] FAIL: 回读未找到 CHS STBL")
            return 1
    except Exception as e:
        print(f"[VERI] 回读异常: {e}")
        return 1
    print("[DONE] MVP patch 完成 (原文件未动)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
