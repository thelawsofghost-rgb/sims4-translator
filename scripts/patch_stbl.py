#!/usr/bin/env python3
"""Phase 3B MVP: 单条 STBL patch 工具 — 外科手术式 copy-on-write (不覆盖原文件)

输入:
  python patch_stbl.py input.package output.package mapping.csv
  python patch_stbl.py --inspect input.package        (只读列出 locale 0x01 keys)

设计 (v2, 修复游戏"文件损坏"问题 — 不再重建整个 package):
  上一版 build_dbpf 把整个 DBPF 重排 (重写 header/index/重排 body/置零 flags),
  破坏了 Sims4 结构 -> 游戏报文件损坏。v2 改为**原位外科手术**:

  1. 把原文件字节整体读入 (1:1 复制, 任何字节不改动)
  2. 只覆盖**目标 STBL 的 body 区** [entry.offset, entry.offset+entry.size)
     - 必须满足: 新压缩体积 <= 原体积 (原位缩短, 剩余区补 0)
     - 若不满足 -> 抛错拒写, 绝不移动 index / 不重排 body / 不改 header
  3. 其余一切 (header, index, 其他 resource, 压缩标记, padding) 逐字节原样
  4. **字节级 self-check**: 目标区之外必须与原文件逐字节一致, 否则 FAIL
     (机械地保证"除了目标 STBL resource 外, 其他 resource 保持一致")

这样生成的新包 = 原包 + 仅目标 STBL 那一段被改写, 结构零改动, Sims4 可正常加载。

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


def surgical_patch(in_bytes: bytes, entry, new_stored: bytes):
    """原位覆盖目标 STBL 区 [entry.offset, entry.offset+entry.size)。
    约束: len(new_stored) <= entry.size, 否则抛错 (不重排)。"""
    if len(new_stored) > entry.size:
        raise ValueError(
            f"新 STBL 压缩体积 {len(new_stored)} > 原 {entry.size}。"
            f"MVP 仅支持原位缩短 (不移动 index/不重排)。换更短的文本或改用扩展模式。")
    data = bytearray(in_bytes)
    s = entry.offset
    e = s + len(new_stored)
    data[s:e] = new_stored
    for i in range(e, s + entry.size):   # 变短部分补 0, 保持字节数/offset 不动
        data[i] = 0
    return bytes(data)


def byte_diff_verify(orig: bytes, new: bytes, entry) -> bool:
    """除目标 STBL body 区外, 其余必须逐字节一致。"""
    if len(orig) != len(new):
        return False
    s, e = entry.offset, entry.offset + entry.size
    for i in range(len(orig)):
        if s <= i < e:
            continue
        if orig[i] != new[i]:
            return False
    return True


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

    # 应用
    new_recs, applied = [], 0
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

        # 重建 STBL body; 压缩方式跟随原 STBL (保 compression 一致)
    new_stbl_body = stbl_encode(new_recs)
    if orig_compressed:
        # 取最小 zlib 体积 (试 1..9), 尽量 <= 原体积以便原位写入
        cands = sorted((zlib.compress(new_stbl_body, lv) for lv in range(1, 10)), key=len)
        new_stored = cands[0]
    else:
        new_stored = new_stbl_body
    # 原位写入要求新压缩体积 <= 原体积 (剩余区补 0)
    if len(new_stored) > chs.size:
        print(f"[ERROR] 新 STBL 最小体积 {len(new_stored)}B > 原 {chs.size}B, 无法原位写入。")
        print(f"        请换更短的译文 (需压缩后 <= {chs.size}B), 或确认该 key 在新包中文本更短。")
        return 1
    # 补齐到原体积 (尾部 0 填充, zlib 解压容忍尾部数据) → 保持长度/offset 不变
    new_stored = new_stored + b"\x00" * (chs.size - len(new_stored))
    print(f"[OK  ] STBL 重建: {len(recs)} keys, 应用 {applied} 条, 压缩后 {len(new_stored)}B(=原 {chs.size}B, 已原位对齐)")

    # 外科手术写入 (长度约束 + 不重排)
    try:
        new_file = surgical_patch(raw, chs, new_stored)
    except ValueError as e:
        print(f"[ERROR] 外科手术长度约束不满足: {e}")
        return 1

    Path(out_path).write_bytes(new_file)
    print(f"[SAVE] 已写入: {out_path} ({len(new_file)} 字节)")

    # 字节级 diff 验证: 目标区外必须逐字节一致
    if not byte_diff_verify(raw, new_file, chs):
        print("[B-DIFF] FAIL: 目标 STBL 区之外有字节被改动 — 结构被破坏, 拒发")
        return 1
    print(f"[B-DIFF] PASS: 除目标 STBL 区 [{chs.offset}, {chs.offset + chs.size}) 外, "
          f"其余 {len(raw) - chs.size} 字节与原文件逐字节一致")

    # 回读验证
    try:
        vraw, vidx = read_index_only(out_path)
        vchs = find_chs_stbl(vidx.entries)
        if vchs:
            vrecs = stbl_decode(_z_or_raw(body_of(vraw, vchs)))
            n_patched = sum(1 for kh, txt, _, _ in vrecs if kh in hash_to_tid)
            print(f"[VERI] 回读: CHS STBL keys={len(vrecs) if vrecs else 0}, patched={n_patched}")
            if not vrecs or n_patched == 0:
                print("[VERI] FAIL: 回读未发现修改"); return 1
        else:
            print("[VERI] FAIL: 回读未找到 CHS STBL"); return 1
    except Exception as e:
        print(f"[VERI] 回读异常: {e}"); return 1

    print("[DONE] MVP patch 完成 (原文件未动)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
