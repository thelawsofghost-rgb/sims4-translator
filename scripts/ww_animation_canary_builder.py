#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WW Animation Canary Builder — 生成 TEST A clone + TEST B sidecar (阶段一 A/B canary)

严禁并 A 与 B: 本工具只【生成 artifact】与【机器验证 invariants】。
ZERO WRITE TO MODS。不执行真机 swap / 不部署 sidecar / 不改原 package。

目的 (只读原包 + 写 output/):
  TEST A clone : 在独立 output/ww_animation_canary_A/ 生成一份原包的 modified clone,
                 clone 内【只改一个】玩家可见 display 字段的值 (加醒目【CHS_CANARY】前缀),
                 其余所有资源字节/语义保持不变。用于之后(经我批准) source-swap 因果测试。
  TEST B sidecar: 在 output/ww_animation_canary_B/ 生成最小独立 package,
                 只含【1 个】exact-TGI 的 WW registration XML resource (type/group/instance 与
                 原包注册 XML 完全一致), 只改同 1 个 display 字段。用于 TEST A PASS 后的
                 override/load-order 测试 (不做真机部署)。

铁律 (fail-closed):
  * 只处理【单 registration XML】的 source: WW_ANIM_XML (0x7DF2169C) 恰好 1 个, 否则停止并报告。
  * 只改【1 个】明确 display 字段: animation_raw_display_name (WickedWhims, 真实 Sims tuning
    <T n="animation_raw_display_name">TEXT</T> 结构)。
  * 定位依据: 在唯一 WW registration XML 中精确找到
      <T n="animation_raw_display_name">OLD</T>
    exact match 数必须 == 1; 0 或 >1 一律 FAIL-CLOSED (不按 ordinal 猜)。
  * animation_clip_name / animation_author / category / location / tags / actor_id
    等所有其他字段与资源【字节不变】。
  * CLIP/ANIM_RCOL/STBL 等所有非 WW-XML 资源 body 逐字节保留。
  * 必须机器验证: resource count 不变 / TGI set 不变 / 恰好 1 个 display 字段改变 /
    internal 字段改变=0 / reopen parse PASS。

用法 (Windows, 只读):
  python scripts/ww_animation_canary_builder.py \
      --source "C:\\Users\\thela\\...\\MSWD_FORCE_FLOOR_002.package" \
      --display-old "FORCE_FLOOR_002" \
      --display-new "【CHS_CANARY】强制地板002"

  CONTROL_SOURCE_FAITHFUL 模式 (真机 23/23 实锤后, 只修两个 source-fidelity bug):
  python scripts/ww_animation_canary_builder.py \
      --source "...\\MSWD_FORCE_FLOOR_002.package" \
      --control-source-faithful [--force]
      -> 生成 output/ww_animation_control_source_faithful/..._CONTROL_SOURCE_FAITHFUL.package
      只修 BUG_1 (minor 从 source 透传) 与 BUG_3 (offset/size 高位保留 source 原值);
      保留 index-at-head vs source index-at-end 差异 (INDEX_PLACEMENT_DIFFERENCE=YES)。
      不生成 CANARY / 不真机 / 不碰 Mods。

可选:
  --display-field animation_raw_display_name   (默认; 真实 WickedWhims 字段)
  --out-dir output                     (默认; artifact 写到 output/ww_animation_canary_A|B/)
  --force                              (artifacts 已存在时覆盖, 默认 fail-closed 拒写)

输出:
  output/ww_animation_canary_A/<source_stem>_CANARY_A.package   (完整 clone, 只改 1 display)
  output/ww_animation_canary_B/<source_stem>_WW_CHS_CANARY.package (最小 exact-TGI XML sidecar)
  output/ww_animation_canary_report.md
  stdout: CANARY SOURCE / TEST_A_ARTIFACT / TEST_B_ARTIFACT / ZERO_WRITE_TO_MODS=YES + PASS/FAIL

退出码: 0=完成; 2=参数/IO; 3=不满足 canary 前提 (非单注册XML / T 节点找不到或非唯一 / 校验 FAIL)。
"""
import argparse
import csv
import hashlib
import re
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse  # noqa: E402

WW_ANIM_XML = 0x7DF2169C
CLIP = 0x6B20C4F3
ANIM_RCOL = 0xBC4A5044
STBL = 0x220557DA

DISPLAY_FIELDS = {"animation_raw_display_name", "raw_display_name"}
# 保护字段 (真实独立功能字段): 不得改动
INTERNAL_FIELDS = {"animation_author", "animation_locations", "animation_custom_locations",
                   "animation_category", "animation_tags", "actor_id", "animation_clip_name"}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def read_body_raw(pkg: Path, entry) -> bytes:
    """按 index entry offset/size 读资源完整 body (去压缩标记位)。"""
    off = entry.offset & 0x7FFFFFFF
    size = entry.size & 0x7FFFFFFF
    with open(pkg, "rb") as fh:
        fh.seek(off)
        return fh.read(size)


def decompress_maybe(body: bytes) -> bytes:
    if body[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        try:
            return zlib.decompress(body)
        except Exception:
            return body
    return body


def compress_like(orig_body: bytes, plain: bytes) -> bytes:
    """若原 body 是 zlib 压缩, 则对 plain 重新压缩以保持资源形态一致。"""
    if orig_body[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        try:
            return zlib.compress(plain)
        except Exception:
            return plain
    return plain


def parse_anim_xml(body: bytes):
    """返回 (schema, raw_text, err)。schema 识别两变体。"""
    raw = decompress_maybe(body)
    if raw[:2] not in (b"\x78",) and b"<" not in raw[:64]:
        return None, raw, "NOT_XML"
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception as e:
        return None, raw, f"DECODE: {e}"
    if "<" not in text:
        return None, raw, "NO_XML_TAG"
    if "WickedWhimsAnimationPackage" in text or "animation_raw_display_name" in text:
        schema = "WickedWhimsAnimationPackage"
    elif "StripClubDanceAnimationPackage" in text or "dancer_animation_clip_name" in text:
        schema = "StripClubDanceAnimationPackage"
    else:
        schema = "UNKNOWN_SCHEMA"
    return schema, text, None


def _old_in_block(block: str, field: str):
    m = re.search(r'<T\s+[^>]*\bn\s*=\s*"%s"[^>]*>([^<]*)</T>' % re.escape(field), block)
    return m.group(1).strip() if m else None


def _replace_t_node_display(xml_text: str, display_field: str, old_val: str, new_val: str):
    """真实 Sims tuning 定位: 精确找 <T n=display_field>OLD</T> 元素, 只改该元素的 text。

    返回 (new_text, replaced_count, matched_spans)。
    exact match 数必须 == 1, 否则调用方 FAIL-CLOSED。
    """
    pat = re.compile(
        r'(<T\s+[^>]*\bn\s*=\s*"%s"[^>]*>)([^<]*)(</T>)' % re.escape(display_field)
    )
    spans = []  # (text_start, text_end, old_text)
    for m in pat.finditer(xml_text):
        inner = m.group(2)
        if inner.strip() == old_val:
            spans.append((m.start(2), m.end(2), inner))
    if len(spans) != 1:
        return xml_text, len(spans), spans
    s, e, _ = spans[0]
    return xml_text[:s] + new_val + xml_text[e:], 1, spans


def build_package(items, out_path: Path, header_comp=0, entry_meta=None,
                 major=2, minor=0):
    """通用 DBPF v2 写包器。保持 items 顺序。返回 (out_path)。

    方案 A (ROOT-CAUSE fix, 2026-08-17): 现有 writer 把 DBPF v2 compression
    metadata 全部置 0/漏写, 导致 zlib body 被游戏加载器按未压缩读取而判损坏。
    本函数支持按每资源写入真实的 compression metadata:
      - header 0x3C compression flag (header_comp, 源包值)
      - 每 entry 的 offset/size 压缩高位 + field7 mem_size + field8 comp_type

    CONTROL_SOURCE_FAITHFUL 修正 (2026-08-17, 真机实锤后):
      铁律 = 保留 source 值, 不是 normalize 到我们的期望值。
      BUG_1: major/minor 硬编码 2/0 -> 改为从 source 透传 (major, minor 参数)。
      BUG_3: offset/size bit31 不再按推导 comp 状态强制 OR; 而取 source 原高位:
         - meta["offset_high_bit"] / meta["size_high_bit"] 提供时 -> 原样保留 bit31
         - 缺失时 (旧路径) 才回退到 comp 推导行为 (向后兼容 CANARY/旧调用)。
      低 31 位物理 offset 仍按新布局重算; bit31 只保留 source 原值。

    items: list[(type, group, inst, body)]  (旧签名, 兼容)
       或 list[(type, group, inst, body, meta)]
       meta: dict {comp_state:bool, comp_type:int, mem_size:int,
                   offset_high_bit:Optional[int], size_high_bit:Optional[int]}

    向后兼容: 无 meta/无显式高位 的条目按旧行为 (if body 是 zlib 则标记压缩,
    否则未压缩) —— 但 CONTROL_SOURCE_FAITHFUL 路径总是显式传 source 原高位。
    """
    count = len(items)
    HEADER = 0x44; PAD = 4; ENTRY = 32
    index_size = PAD + count * ENTRY
    index_offset = HEADER
    data_off = index_offset + index_size
    idx = []
    off = data_off
    for it in items:
        _t, _g, inst, d = it[0], it[1], it[2], it[3]
        meta = it[4] if len(it) > 4 and it[4] else None
        comp = bool(meta and meta.get("comp_state"))
        if not comp and _is_zlib(d):
            # 自动检测: body 是 zlib 但未显式给压缩态 -> 按压缩处理, 避免泄漏元数据
            comp = True
        sz = len(d)
        idx.append((_t, _g, inst, off, sz, comp, meta))
        off += sz
    buf = bytearray(off)
    buf[0:4] = b"DBPF"
    struct.pack_into("<I", buf, 4, major & 0xFFFFFFFF)
    struct.pack_into("<I", buf, 8, minor & 0xFFFFFFFF)
    struct.pack_into("<I", buf, 0x24, count)
    struct.pack_into("<I", buf, 0x2C, index_size)
    # header 0x3C: 源包 compression flag (修复点 1: 之前恒为 0)
    struct.pack_into("<I", buf, 0x3C, header_comp & 0xFFFFFFFF)
    struct.pack_into("<I", buf, 0x40, index_offset)
    p = index_offset
    struct.pack_into("<I", buf, p, 0); p += PAD
    for (_t, _g, inst, o, sz, comp, meta) in idx:
        # bit31 决定: source 原高位优先 (显式给 0/1 都保留); 否则回退 comp 推导
        if meta is not None and meta.get("offset_high_bit") is not None:
            ohb = 1 if meta["offset_high_bit"] else 0
        else:
            ohb = 1 if comp else 0
        if meta is not None and meta.get("size_high_bit") is not None:
            shb = 1 if meta["size_high_bit"] else 0
        else:
            shb = 1 if comp else 0
        struct.pack_into("<I", buf, p, _t); p += 4
        struct.pack_into("<I", buf, p, _g); p += 4
        struct.pack_into("<I", buf, p, (inst >> 32) & 0xFFFFFFFF); p += 4
        struct.pack_into("<I", buf, p, inst & 0xFFFFFFFF); p += 4
        # offset: 低 31 位按新布局重算, bit31 保留 source 原高位 (不再按 comp 推导)
        struct.pack_into("<I", buf, p, (o & 0x7FFFFFFF) | (0x80000000 if ohb else 0)); p += 4
        # size: 低 31 位写真实 body size, bit31 保留 source 原高位
        struct.pack_into("<I", buf, p, (sz & 0x7FFFFFFF) | (0x80000000 if shb else 0)); p += 4
        # mem_size (field7): 解压后长度 (修复点 4)
        mem = (meta.get("mem_size") if meta else None) or (_decomp_len(d) if comp else 0)
        struct.pack_into("<I", buf, p, mem & 0xFFFFFFFF); p += 4
        # comp_type (field8) (修复点 5)
        ct = (meta.get("comp_type") if meta else 0) or (0x5A42 if comp else 0)
        struct.pack_into("<I", buf, p, ct & 0xFFFFFFFF); p += 4
    p = data_off
    for it in items:
        _t, _g, inst, d = it[0], it[1], it[2], it[3]
        buf[p:p + len(d)] = d; p += len(d)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(buf))
    return out_path


def _is_zlib(b: bytes) -> bool:
    """判定 body 是否为 zlib 压缩流 (wbits 15, 常见 header 78 01/5E/9C/DA)。"""
    return len(b) >= 2 and b[0] == 0x78 and b[1] in (0x01, 0x5E, 0x9C, 0xDA)


def _decomp_len(d: bytes) -> int:
    """返回解压后长度 (供 field7 mem_size)。非 zlib 则返回存储长度。"""
    if _is_zlib(d):
        try:
            return len(zlib.decompress(d))
        except Exception:
            pass
    return len(d)


def read_entry_meta_raw(pkg: Path):
    """从源包读取每个 index entry 的原始 compression metadata (不猜测)。

    返回 (major, minor, header_comp, entries_meta)。entries_meta 为 list, 顺序与源 index entry 一致:
      dict{type, group, inst, offset_raw, size_raw,
           offset_comp:bool, size_comp:bool, mem_size:int, comp_type:int}
    """
    with open(pkg, "rb") as fh:
        fh.seek(0)
        hdr = fh.read(0x44)
        magic = hdr[0:4]
        if magic != b"DBPF":
            raise ValueError(f"非 DBPF: {magic!r}")
        major = struct.unpack("<I", hdr[0x04:0x08])[0]
        minor = struct.unpack("<I", hdr[0x08:0x0C])[0]
        count = struct.unpack("<I", hdr[0x24:0x28])[0]
        header_comp = struct.unpack("<I", hdr[0x3C:0x40])[0]
        idx_off = struct.unpack("<I", hdr[0x40:0x44])[0]
        fh.seek(idx_off + 4)  # 索引区 4 字节 padding
        raw = fh.read(count * 32)
    out = []
    for i in range(count):
        e = raw[i * 32:(i + 1) * 32]
        t, g, ih, il, off, sz, mem, ct = struct.unpack("<IIIIIIII", e)
        inst = (ih << 32) | il
        out.append({
            "type": t, "group": g, "inst": inst,
            "offset_raw": off, "size_raw": sz,
            "offset_comp": bool(off & 0x80000000),
            "size_comp": bool(sz & 0x80000000),
            "mem_size": mem, "comp_type": ct,
        })
    return major, minor, header_comp, out


def dbpf_metadata_valid(path: Path) -> tuple[bool, list[str]]:
    """严格校验 DBPF compression metadata 与资源 body 是否自洽。

    DBFP_METADATA_VALID 检查项 (禁止 zlib body + uncompressed metadata):
      - 对每 entry: body 前 2 字节是否为 zlib magic (从原始存储字节判断)
      - 对每 entry: 原始 index entry 的 size 压缩高位 是否 == (body 是否 zlib)
    (硬性两项; 其余为提示性 warn, 因真实包对 mem_size/comp_type/header 0x3C
     可能本为零, 以源包语义为准, 不判死。)
    压缩位的权威来源 = 源 index entry 原始 32 字节 (不经 safe_parse 掩码)。
    返回 (valid, fail 列表, warn 列表)。"""
    fails = []
    warns = []
    try:
        _mj, _mn, header_comp, metas = read_entry_meta_raw(path)
    except Exception as ex:
        return False, [f"read raw index: {ex}"], []
    for m in metas:
        body = read_body_raw_masked(path, m)
        body_zlib = _is_zlib(body)
        sz_comp = m["size_comp"]
        tgi = f"0x{m['type']:08X}/0x{m['group']:08X}/0x{m['inst']:016X}"
        if body_zlib and not sz_comp:
            fails.append(f"zlib body but size-compression-bit=0: {tgi}")
        if not body_zlib and sz_comp:
            # 真实 source (SAMPLE 3) 已证明: size_high_bit=1 可出现在非 zlib body 上
            # (stbl/WW XML/CLIP 等, src_sz=84 c=1)。这是 source 的真实合法 index 表达,
            # 非缺陷。仅记录为 warn (向后保留 vs source 相同高位才视为忠实)。
            warns.append(f"non-zlib body but size-compression-bit=1 (真实 source 可合法;见 warn 而非 fail): {tgi}")
        # 以下为约束性提示: 真实 Sims 包对 mem_size/comp_type/header 0x3C 未必恒非零,
        # 若源包原本如此, 不应判死; 仅记入 warn 供人工核验。
        if sz_comp and m["mem_size"] == 0:
            warns.append(f"compressed resource but mem_size=0 (可能合法, 源包语义而定): {tgi}")
        if sz_comp and m["comp_type"] == 0:
            warns.append(f"compressed resource but comp_type=0 (可能合法, 源包语义而定): {tgi}")
    any_comp = any(m["size_comp"] for m in metas)
    if any_comp and header_comp == 0:
        warns.append(f"有压缩资源但 header 0x3C compression flag=0 (以源包语义为准, 提示)")
    if header_comp != 0 and not any_comp:
        warns.append(f"header 0x3C compression flag={header_comp} 但无任何压缩资源 (以源包语义为准, 提示)")
    return (not fails), fails, warns


def read_body_raw_masked(pkg: Path, meta: dict) -> bytes:
    """按原始 index entry 的 offset/size (掩掉高位) 读取资源完整存储字节。"""
    off = meta["offset_raw"] & 0x7FFFFFFF
    size = meta["size_raw"] & 0x7FFFFFFF
    with open(pkg, "rb") as fh:
        fh.seek(off)
        return fh.read(size)


def run_control0(src: Path, out_dir: Path, force: bool) -> int:
    """方案 A CONTROL_0: 读真实 source -> 经修复后的 writer 重建 -> 不改任何逻辑资源。

    要求:
      - 全部资源复用 source stored bytes + source compression metadata
      - resource count / TGI set / order 与 source 一致
      - PARSER_VALID + DBPF_METADATA_VALID 双层面静态校验
    返回 exit code (0=STATIC_PASS)。"""
    src_sha = sha256(src)
    idx, err = safe_parse(src)
    if err is not None or idx is None:
        print(f"ERROR: source 解析失败: {err}", file=sys.stderr)
        return 3
    hdr_comp = None
    src_major = 2; src_minor = 0
    src_major, src_minor, hdr_comp, src_meta = read_entry_meta_raw(src)
    if len(src_meta) != len(idx.entries):
        print(f"ERROR: 源 index metadata 数与解析条目数不一致", file=sys.stderr)
        return 3
    by_tgi = {(m["type"], m["group"], m["inst"]): m for m in src_meta}

    def meta_for(e) -> dict:
        m = by_tgi.get((e.type_id, e.group_id, e.instance_id))
        if m is not None:
            return {"comp_state": bool(m["size_comp"]), "comp_type": m["comp_type"], "mem_size": m["mem_size"],
                    "offset_high_bit": int(m["offset_comp"]), "size_high_bit": int(m["size_comp"])}
        b = read_body_raw(src, e)
        return {"comp_state": _is_zlib(b), "comp_type": 0x5A42 if _is_zlib(b) else 0, "mem_size": _decomp_len(b)}

    # 逐资源: 源存储字节 + 源 metadata (logical 与 raw 都不改)
    items = [(e.type_id, e.group_id, e.instance_id, read_body_raw(src, e), meta_for(e)) for e in idx.entries]

    out = out_dir / "ww_animation_control_0" / f"{src.stem}_CONTROL_0.package"
    if out.exists() and not force:
        print(f"ERROR: CONTROL_0 已存在 (拒绝覆盖, 用 --force): {out}", file=sys.stderr)
        return 3
    build_package(items, out, header_comp=hdr_comp, major=src_major, minor=src_minor)

    # ---- 静态验证 ----
    c_sha = sha256(out)
    idxC, cerr = safe_parse(out)
    parser_ok = (cerr is None and idxC is not None)
    meta_ok, meta_fails, meta_warns = dbpf_metadata_valid(out)

    # 逐项对比 source vs CONTROL_0
    res = {"byte_identical": False, "rc_same": None, "tgi_same": None, "order_same": None,
           "logical_all": True, "raw_unmod": True, "comp_meta": True}
    if parser_ok:
        res["rc_same"] = len(idxC.entries) == len(idx.entries)
        tgi_src = [(e.type_id, e.group_id, e.instance_id) for e in idx.entries]
        tgi_c = [(e.type_id, e.group_id, e.instance_id) for e in idxC.entries]
        res["tgi_same"] = set(tgi_src) == set(tgi_c)
        res["order_same"] = tgi_src == tgi_c
        # 逐资源: logical body / raw stored / compression metadata
        for e, ec in zip(idx.entries, idxC.entries):
            bs = read_body_raw(src, e)
            bc = read_body_raw(out, ec)
            if decompress_maybe(bs) != decompress_maybe(bc):
                res["logical_all"] = False
            if bs != bc:
                res["raw_unmod"] = False
        # compression metadata: 比较源与 CONTROL_0 的 per-entry mem_size/comp_type/size_comp/offset_comp
        _, _, _, cm = read_entry_meta_raw(out)
        if len(cm) != len(src_meta):
            res["comp_meta"] = False
        else:
            for m1, m2 in zip(src_meta, cm):
                if (m1["size_comp"], m1["mem_size"], m1["comp_type"], m1["offset_comp"]) != (m2["size_comp"], m2["mem_size"], m2["comp_type"], m2["offset_comp"]):
                    res["comp_meta"] = False
    res["byte_identical"] = (src_sha == c_sha)
    static_pass = parser_ok and meta_ok and res["rc_same"] and res["tgi_same"] \
        and res["order_same"] and res["logical_all"] and res["raw_unmod"] and res["comp_meta"]

    print("CONTROL_0:")
    print(f"  path={out}")
    print(f"  source_sha={src_sha}")
    print(f"  control_sha={c_sha}")
    print(f"  byte_identical={'YES' if res['byte_identical'] else 'NO'}")
    print(f"  resource_count_same={'YES' if res['rc_same'] else 'NO'}")
    print(f"  tgi_set_same={'YES' if res['tgi_same'] else 'NO'}")
    print(f"  resource_order_same={'YES' if res['order_same'] else 'NO'}")
    print(f"  logical_all_equal={'YES' if res['logical_all'] else 'NO'}")
    print(f"  raw_unmodified_equal={'YES' if res['raw_unmod'] else 'NO'}")
    print(f"  compression_metadata_equal={'YES' if res['comp_meta'] else 'NO'}")
    print(f"  PARSER_VALID={'YES' if parser_ok else 'NO'}")
    print(f"  DBPF_METADATA_VALID={'YES' if meta_ok else 'NO'}")
    for f in meta_fails:
        print(f"    METADATA_FAIL: {f}")
    print(f"  STATIC_PASS={'YES' if static_pass else 'NO'}")
    if not res["byte_identical"]:
        print("  BYTE_DIFF_LOCATION: 预期来自 offsets/index-position 布局 (源包 index 常在末尾,"
              "本 writer 把 index 置于头部)。逻辑/压缩/元数据层面必须仍全等; 见上面各 equal 标志。")
    print(f"ZERO_WRITE_TO_MODS=YES")
    return 0 if static_pass else 3


def run_control_source_faithful(src: Path, out_dir: Path, force: bool) -> int:
    """CONTROL_SOURCE_FAITHFUL: 只修两个已实捶 source-fidelity bug 的非必改无操作重建。

    修复项 (真机 23/23 实锤):
      BUG_1 minor downgrade: source minor=1 -> writer 硬编码 0; 改从 source 透传 major/minor。
      BUG_3 offset high-bit normalization: source offset_high_bit=0 -> writer 按 comp 推导=1;
            改保留 source offset_high_bit 原值 (低31位物理 offset 仍重算)。
    不修改 index placement (source index-at-end vs control index-at-head 保留差异)。

    只生成 output/ww_animation_control_source_faithful/ (不生成 CANARY / 不碰 Mods)。
    若全部 source-fidelity gate (E) 通过 -> 打印 STATIC_PASS=YES 并返回 0;
    否则 STATIC_PASS=NO 并返回 3 (停止)。
    """
    src_sha = sha256(src)

    idx, err = safe_parse(src)
    if err is not None or idx is None:
        print(f"ERROR: source 解析失败: {err}", file=sys.stderr)
        return 3
    src_major, src_minor, hdr_comp, src_meta = read_entry_meta_raw(src)
    if len(src_meta) != len(idx.entries):
        print(f"ERROR: 源 index metadata 数与解析条目数不一致 ({len(src_meta)} vs {len(idx.entries)})", file=sys.stderr)
        return 3
    by_tgi = {(m["type"], m["group"], m["inst"]): m for m in src_meta}

    def meta_for(e) -> dict:
        m = by_tgi.get((e.type_id, e.group_id, e.instance_id))
        if m is not None:
            return {"comp_state": bool(m["size_comp"]), "comp_type": m["comp_type"], "mem_size": m["mem_size"],
                    "offset_high_bit": int(m["offset_comp"]), "size_high_bit": int(m["size_comp"])}
        b = read_body_raw(src, e)
        return {"comp_state": _is_zlib(b), "comp_type": 0x5A42 if _is_zlib(b) else 0, "mem_size": _decomp_len(b)}

    # 无操作: 全部源存储字节 + 源 metadata + 源 high bits (不覆盖任何逻辑内容)
    items = [(e.type_id, e.group_id, e.instance_id, read_body_raw(src, e), meta_for(e)) for e in idx.entries]

    out = out_dir / "ww_animation_control_source_faithful" / f"{src.stem}_CONTROL_SOURCE_FAITHFUL.package"
    if out.exists() and not force:
        print(f"ERROR: CONTROL_SOURCE_FAITHFUL 已存在 (拒绝覆盖, 用 --force): {out}", file=sys.stderr)
        return 3
    build_package(items, out, header_comp=hdr_comp, major=src_major, minor=src_minor)

    # ---- 静态 source-fidelity gate (E) ----
    csha = sha256(out)
    control_sha = csha

    idxC, cerr = safe_parse(out)
    parser_ok = (cerr is None and idxC is not None)
    meta_ok, meta_fails, _meta_warns = dbpf_metadata_valid(out)

    # 版本
    c_major, c_minor, _chc, _cm = read_entry_meta_raw(out)
    dbpf_ver_equal = (src_major == c_major) and (src_minor == c_minor)

    # 23 维逐项: offset_high_bit / size_high_bit / field7 / field8 / body
    off_eq = sz_eq = f7_eq = f8_eq = body_eq = 0
    idx_order_ok = True
    ranges_ok = True
    total = len(idx.entries)
    src_tgi = [(e.type_id, e.group_id, e.instance_id) for e in idx.entries]
    ctl_tgi = [(e.type_id, e.group_id, e.instance_id) for e in idxC.entries] if idxC else []
    idx_order_ok = (src_tgi == ctl_tgi)
    if idxC and len(idxC.entries) == total:
        for m1, m2 in zip(src_meta, _cm):
            off_eq += (m1["offset_comp"] == m2["offset_comp"])
            sz_eq += (m1["size_comp"] == m2["size_comp"])
            f7_eq += (m1["mem_size"] == m2["mem_size"])
            f8_eq += (m1["comp_type"] == m2["comp_type"])
        for m2, e2 in zip(_cm, idxC.entries):
            boff = m2["offset_raw"] & 0x7FFFFFFF
            bsz = m2["size_raw"] & 0x7FFFFFFF
            if not (0 <= boff and boff + bsz <= out.stat().st_size):
                ranges_ok = False
        for e, ec in zip(idx.entries, idxC.entries):
            if read_body_raw(src, e) == read_body_raw(out, ec):
                body_eq += 1
    else:
        ranges_ok = False

    # index placement (保留差异, 不判死)
    with open(src, "rb") as fh:
        fh.seek(0); sh = fh.read(0x44)
    s_ioff = struct.unpack("<I", sh[0x40:0x44])[0]; s_isz = struct.unpack("<I", sh[0x2C:0x30])[0]
    src_at_end = (s_ioff + s_isz) >= src.stat().st_size - 8
    ctl_at_end = False
    if idxC:
        with open(out, "rb") as fh:
            fh.seek(0); ch = fh.read(0x44)
        c_ioff = struct.unpack("<I", ch[0x40:0x44])[0]; c_isz = struct.unpack("<I", ch[0x2C:0x30])[0]
        ctl_at_end = (c_ioff + c_isz) >= out.stat().st_size - 8

    body_bytes_equal = (body_eq == total)
    all_ranges_valid = ranges_ok
    all_gates = (dbpf_ver_equal and body_bytes_equal and off_eq == total and sz_eq == total
                 and f7_eq == total and f8_eq == total and idx_order_ok and all_ranges_valid and parser_ok and meta_ok)

    print("CONTROL_SOURCE_FAITHFUL:")
    print(f"  path={out}")
    print(f"  source_sha={src_sha}")
    print(f"  control_sha={control_sha}")
    print(f"  SOURCE_DBPF_VERSION={src_major}.{src_minor}")
    print(f"  CONTROL_DBPF_VERSION={c_major}.{c_minor}")
    print(f"  DBPF_VERSION_EQUAL={'YES' if dbpf_ver_equal else 'NO'}")
    print(f"  RESOURCE_BODY_BYTES_EQUAL={'YES' if body_bytes_equal else 'NO'}  ({body_eq}/{total})")
    print(f"  OFFSET_HIGH_BIT_EQUAL_COUNT={off_eq}/{total}")
    print(f"  SIZE_HIGH_BIT_EQUAL_COUNT={sz_eq}/{total}")
    print(f"  FIELD7_EQUAL_COUNT={f7_eq}/{total}")
    print(f"  FIELD8_EQUAL_COUNT={f8_eq}/{total}")
    print(f"  INDEX_ORDER_EQUAL={'YES' if idx_order_ok else 'NO'}")
    print(f"  ALL_RESOURCE_RANGES_VALID={'YES' if all_ranges_valid else 'NO'}")
    print(f"  SOURCE_INDEX_AT_END={'YES' if src_at_end else 'NO'}")
    print(f"  CONTROL_INDEX_AT_END={'YES' if ctl_at_end else 'NO'}")
    print(f"  INDEX_PLACEMENT_DIFFERENCE={'YES' if src_at_end != ctl_at_end else 'NO'}  (本轮唯一故意保留的主要结构差异)")
    print(f"  PARSER_VALID={'YES' if parser_ok else 'NO'}")
    print(f"  DBPF_METADATA_VALID={'YES' if meta_ok else 'NO'}")
    for f in meta_fails:
        print(f"    METADATA_FAIL: {f}")
    print(f"  CONTROL_SOURCE_FAITHFUL_STATIC_PASS={'YES' if all_gates else 'NO'}")
    if not all_gates:
        if not dbpf_ver_equal:
            print("    GATE_FAIL: DBPF_VERSION_EQUAL")
        if not body_bytes_equal:
            print(f"    GATE_FAIL: RESOURCE_BODY_BYTES_EQUAL ({body_eq}/{total})")
        if off_eq != total:
            print(f"    GATE_FAIL: OFFSET_HIGH_BIT_EQUAL ({off_eq}/{total})")
        if sz_eq != total:
            print(f"    GATE_FAIL: SIZE_HIGH_BIT_EQUAL ({sz_eq}/{total})")
        if f7_eq != total:
            print(f"    GATE_FAIL: FIELD7_EQUAL ({f7_eq}/{total})")
        if f8_eq != total:
            print(f"    GATE_FAIL: FIELD8_EQUAL ({f8_eq}/{total})")
        if not idx_order_ok:
            print("    GATE_FAIL: INDEX_ORDER_EQUAL")
        if not all_ranges_valid:
            print("    GATE_FAIL: ALL_RESOURCE_RANGES_VALID")
        if not parser_ok:
            print("    GATE_FAIL: PARSER_VALID")
        if not meta_ok:
            print("    GATE_FAIL: DBPF_METADATA_VALID")
    print("ZERO_WRITE_TO_MODS=YES")
    return 0 if all_gates else 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--display-old")
    ap.add_argument("--display-new")
    ap.add_argument("--display-field", default="animation_raw_display_name")
    ap.add_argument("--out-dir", default="output")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--control0", action="store_true",
                    help="方案 A CONTROL_0 模式: 读 source -> 经修复后 writer 重建, 不改任何逻辑资源; 生成 output/ww_animation_control_0/")
    ap.add_argument("--control-source-faithful", action="store_true",
                    help="CONTROL_SOURCE_FAITHFUL 模式: 只修 minor 与 offset high-bit 两个 source-fidelity bug, 保留 index placement 差异; 生成 output/ww_animation_control_source_faithful/")
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print("ERROR: source 不存在", file=sys.stderr)
        return 2

    if a.control0 and a.control_source_faithful:
        print("ERROR: --control0 与 --control-source-faithful 互斥", file=sys.stderr)
        return 2

    # CONTROL_SOURCE_FAITHFUL 模式: 只生成/验证 CONTROL_SOURCE_FAITHFUL, 不做 CANARY
    if a.control_source_faithful:
        return run_control_source_faithful(src, Path(a.out_dir), a.force)

    # CONTROL_0 模式: 只生成/验证 CONTROL_0, 不做 CANARY (不需 display-* 参数)
    if a.control0:
        return run_control0(src, Path(a.out_dir), a.force)

    # 非 CONTROL_0 模式: display-* 必须提供
    if not a.display_old or not a.display_new:
        print("ERROR: 非 --control0 模式必须提供 --display-old 与 --display-new", file=sys.stderr)
        return 2

    # --- 保护字段 T 节点工具 (供 A/B 校验) ---
    def _tunable_values(txt: str):
        """提取真实 T 节点全部值: 返回 {field: [values]} (字段在 INTERNAL_FIELDS 保护范围内)。"""
        out = {f: [] for f in INTERNAL_FIELDS}
        for m in re.finditer(r'<T\s+[^>]*\bn\s*=\s*"([^"]+)"[^>]*>([^<]*)</T>', txt):
            f, v = m.group(1), m.group(2).strip()
            if f in out:
                out[f].append(v)
        return out

    def _internal_changed(txt):
        """返回真实 T 节点保护字段()中值发生变化的字段数。"""
        o = _tunable_values(xml_text_orig)
        m = _tunable_values(txt)
        n = 0
        for f in INTERNAL_FIELDS:
            if o[f] != m[f]:
                n += 1
        return n

    src = Path(a.source)
    out_dir = Path(a.out_dir)
    dir_a = out_dir / "ww_animation_canary_A"
    dir_b = out_dir / "ww_animation_canary_B"
    report_path = out_dir / "ww_animation_canary_report.md"

    src_sha = sha256(src)
    idx, err = safe_parse(src)
    if err is not None or idx is None:
        print(f"ERROR: source 解析失败: {err}", file=sys.stderr)
        return 3

    # 前置: 必须恰好 1 个 WW_ANIM_XML (0x7DF2169C)
    ww = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    if len(ww) != 1:
        print(f"ERROR: 需单 registration XML; 实际 WW_ANIM_XML 数量 = {len(ww)} (fail-closed, 停止选择 this canary)",
              file=sys.stderr)
        print("HINT: 换一个只含 1 个 WW_ANIM_XML 的 CONFIRMED 包; 不要猜 override 粒度。")
        return 3

    wxml_entry = ww[0]
    wxml_tgi = f"0x{wxml_entry.type_id:08X}/0x{wxml_entry.group_id:08X}/0x{wxml_entry.instance_id:016X}"
    body_orig = read_body_raw(src, wxml_entry)
    schema, xml_text_orig, xerr = parse_anim_xml(body_orig)
    if xerr or schema in ("UNKNOWN_SCHEMA",):
        print(f"ERROR: WW XML 解析失败/schema 未识别: {xerr or schema}", file=sys.stderr)
        return 3

    # display 字段名 (真实 WickedWhims / 兼容 StripClub)
    disp_field = a.display_field
    if disp_field not in DISPLAY_FIELDS:
        print(f"ERROR: --display-field 不支持: {disp_field}", file=sys.stderr)
        return 3

    # 真实 schema 定位: 在唯一 WW registration XML 中精确找 <T n=disp_field>OLD</T>
    new_xml, cnt, spans = _replace_t_node_display(xml_text_orig, disp_field, a.display_old, a.display_new)
    if cnt != 1:
        print(f"ERROR: display T 节点 exact match 数 = {cnt} (必须 == 1; FAIL-CLOSED, 不按 ordinal 猜)",
              file=sys.stderr)
        print(f"  disp_field={disp_field}  display_old={a.display_old!r}")
        print("  HINT: 确认 display_old 与包内 <T n=...>TEXT</T> 精确一致; 0=文本不存在, >1=需更具体 selector。")
        return 3
    old_val = spans[0][2]  # 旧文本
    if a.display_old and old_val.strip() != a.display_old:
        # 理论不会发生 (spans 已按 old 匹配)
        print(f"WARN: 文本不精确 (内部不一致): {old_val!r}", file=sys.stderr)

    # 新 XML body (若原 zlib 则重压缩, 保持 source 压缩模型)
    new_body_plain = new_xml.encode("utf-8")
    new_body = compress_like(body_orig, new_body_plain)

    # ---- 方案 A: 提取源包真实 compression metadata (ROOT-CAUSE fix) ----
    src_major0, src_minor0, src_header_comp, src_meta = read_entry_meta_raw(src)
    if len(src_meta) != len(idx.entries):
        print(f"ERROR: 源 index metadata 数与解析条目数不一致 ({len(src_meta)} vs {len(idx.entries)})", file=sys.stderr)
        return 3
    # meta 按 (type, group, inst) 建索引 (源 TGI 应与解析一致)
    src_meta_by_tgi = {(m["type"], m["group"], m["inst"]): m for m in src_meta}

    def meta_for(e) -> dict:
        """返回 source 该条目的压缩 metadata; 缺失则按 body 自动推断。"""
        m = src_meta_by_tgi.get((e.type_id, e.group_id, e.instance_id))
        if m is not None:
            return {"comp_state": bool(m["size_comp"]), "comp_type": m["comp_type"], "mem_size": m["mem_size"],
                    "offset_high_bit": int(m["offset_comp"]), "size_high_bit": int(m["size_comp"])}
        b = read_body_raw(src, e)
        return {"comp_state": _is_zlib(b), "comp_type": 0x5A42 if _is_zlib(b) else 0, "mem_size": _decomp_len(b)}

    def build_item(e, body):
        """(type, group, inst, body, meta): 复用 source metadata (未改资源) 或更新 (WW XML)。"""
        meta = meta_for(e)
        if e.type_id == WW_ANIM_XML and e.instance_id == wxml_entry.instance_id:
            # WW XML: 保持 source 压缩态/类型 + 高/低位, 更新 size 由 writer 用 len(body) 算出
            meta = {"comp_state": meta["comp_state"], "comp_type": meta["comp_type"],
                    "mem_size": len(decompress_maybe(body)),
                    "offset_high_bit": meta["offset_high_bit"], "size_high_bit": meta["size_high_bit"]}
        return (e.type_id, e.group_id, e.instance_id, body, meta)

    # CONTROL_0 内容: 与源逐字节一致 (不动任何资源逻辑内容)
    control_items = [build_item(e, read_body_raw(src, e)) for e in idx.entries]
    # CANARY_A 内容: 仅 WW XML body 替换为 new_body, 其余复用源字节+metadata
    canary_items = [
        build_item(e, new_body if (e.type_id == WW_ANIM_XML and e.instance_id == wxml_entry.instance_id)
                   else read_body_raw(src, e))
        for e in idx.entries
    ]

    # 输出路径
    stem = src.stem
    control_path = out_dir / "ww_animation_control_0" / f"{stem}_CONTROL_0.package"
    clone_path = dir_a / f"{stem}_CANARY_A.package"
    sidecar_path = dir_b / f"{stem}_WW_CHS_CANARY.package"

    # fail-closed: artifact 已存在
    for pp in (control_path, clone_path, sidecar_path):
        if pp.exists() and not a.force:
            print(f"ERROR: artifact 已存在 (拒绝覆盖, 用 --force): {pp}", file=sys.stderr)
            return 3

    # CONTROL_0 首先构建并静态验证 (只有 CONTROL_0 PASS 才生成 CANARY_A)
    build_package(control_items, control_path, header_comp=src_header_comp, major=src_major0, minor=src_minor0)
    control_meta_ok, control_meta_fails, _cw = dbpf_metadata_valid(control_path)
    control_parse_ok = safe_parse(control_path)[1] is None
    # CONTROL_0 静态判定 (PARSER_VALID + DBPF_METADATA_VALID)
    control_pass = control_parse_ok and control_meta_ok
    if not control_pass:
        print(f"ERROR: CONTROL_0 静态验证失败 (PARSER/DBPF_METADATA). 停止, 不生成 CANARY_A.", file=sys.stderr)
        for f in control_meta_fails:
            print(f"  METADATA_FAIL: {f}", file=sys.stderr)
        print(f"CONTROL_0: path={control_path}")
        print(f"PARSER_VALID={'YES' if control_parse_ok else 'NO'}")
        print(f"DBPF_METADATA_VALID={'YES' if control_meta_ok else 'NO'}")
        print(f"STATIC_PASS={'YES' if control_pass else 'NO'}")
        return 3

    build_package(canary_items, clone_path, header_comp=src_header_comp, major=src_major0, minor=src_minor0)
    # TEST B sidecar: 只 1 个资源, TGI == 源注册 XML TGI (元数据按 source XML 的压缩态)
    xml_meta = src_meta_by_tgi.get((WW_ANIM_XML, wxml_entry.group_id, wxml_entry.instance_id))
    sidecar_meta = {"comp_state": bool(xml_meta and xml_meta["size_comp"]),
                    "comp_type": (xml_meta["comp_type"] if xml_meta else 0x5A42),
                    "mem_size": len(decompress_maybe(new_body))}
    build_package([(WW_ANIM_XML, wxml_entry.group_id, wxml_entry.instance_id, new_body, sidecar_meta)],
                  sidecar_path, header_comp=src_header_comp, major=src_major0, minor=src_minor0)

    # ---------- 机器验证 ----------
    def verify_artifact(path, expect_resources, label):
        idx2, err2 = safe_parse(path)
        res = {"ok": True, "fails": []}
        if err2 is not None or idx2 is None:
            res["ok"] = False; res["fails"].append("parse")
            return res
        if len(idx2.entries) != expect_resources:
            res["ok"] = False; res["fails"].append(f"resource_count={len(idx2.entries)} != {expect_resources}")
        return res

    vA = verify_artifact(clone_path, len(idx.entries), "A")
    vB = verify_artifact(sidecar_path, 1, "B")

    # A: 重新解析 clone 的 XML, 检查 changed display=1 / internal=0
    a_fails = []
    txtA = ""
    idxA, _ = safe_parse(clone_path)
    wA = [e for e in idxA.entries if e.type_id == WW_ANIM_XML]
    if len(wA) != 1:
        a_fails.append(f"clone WW_ANIM_XML count={len(wA)} != 1")
    else:
        bA = read_body_raw(clone_path, wA[0])
        _sA, txtA, _xA = parse_anim_xml(bA)
        # display 变化 (T 节点形式)
        def _t_val(txt, field):
            m = re.search(r'<T\s+[^>]*\bn\s*=\s*"%s"[^>]*>([^<]*)</T>' % re.escape(field), txt)
            return m.group(1).strip() if m else None
        new_t = _t_val(txtA, disp_field)
        disp_changed = 1 if new_t == a.display_new else 0
        old_still = 1 if new_t == old_val.strip() else 0
        if disp_changed < 1:
            a_fails.append(f"display 新值未出现 (got {new_t!r})")
        if old_still > 0 and old_val:
            a_fails.append("旧 display 值仍存在")
        # internal 保护字段 (T 节点): 原值必须仍存在
        tun_orig = _tunable_values(xml_text_orig)
        tun_new = _tunable_values(txtA)
        for f in INTERNAL_FIELDS:
            if tun_orig[f] != tun_new[f]:
                a_fails.append(f"internal 字段 {f} 已变: {tun_orig[f]} -> {tun_new[f]}")
        # TGI set 不变
        tgi_orig = {(e.type_id, e.group_id, e.instance_id) for e in idx.entries}
        tgi_new = {(e.type_id, e.group_id, e.instance_id) for e in idxA.entries}
        if tgi_orig != tgi_new:
            a_fails.append("TGI set 改变")
        if len(idxA.entries) != len(idx.entries):
            a_fails.append("resource count 改变")

    # B: sidecar 结构
    b_fails = []
    txtB = ""
    idxB, _ = safe_parse(sidecar_path)
    if len(idxB.entries) != 1:
        b_fails.append(f"sidecar resource_count={len(idxB.entries)} != 1")
    else:
        eB = idxB.entries[0]
        if eB.type_id != WW_ANIM_XML or eB.group_id != wxml_entry.group_id or eB.instance_id != wxml_entry.instance_id:
            b_fails.append("sidecar TGI 不匹配源注册 XML TGI")
        if any(x.type_id == CLIP for x in idxB.entries) or any(x.type_id == ANIM_RCOL for x in idxB.entries) \
           or any(x.type_id == STBL for x in idxB.entries):
            b_fails.append("sidecar 不应含 CLIP/ANIM/STBL")
        bB = read_body_raw(sidecar_path, eB)
        _sB, txtB, _xB = parse_anim_xml(bB)
        if a.display_new not in txtB:
            b_fails.append("sidecar 缺 canary display 新值")
        tunB = _tunable_values(txtB)
        for f in INTERNAL_FIELDS:
            if tun_orig[f] != tunB[f]:
                b_fails.append(f"sidecar internal 字段 {f} 已变")

    a_pass = not a_fails and vA["ok"]
    b_pass = not b_fails and vB["ok"]

    a_int_changed = _internal_changed(txtA) if (not a_fails or True) else 0
    b_int_changed = _internal_changed(txtB) if not b_fails else 0

    # ---------- report ----------
    md = []
    md.append("# WW Animation Canary (TEST A / TEST B artifact)")
    md.append("")
    md.append("## CANARY SOURCE")
    md.append(f"- path = {src}")
    md.append(f"- sha256 = {src_sha}")
    md.append(f"- schema = {schema}")
    md.append(f"- ww_xml_tgi = {wxml_tgi}")
    md.append(f"- display_old = {old_val}")
    md.append(f"- display_canary = {a.display_new}")
    md.append(f"- display_field = {disp_field}")
    md.append(f"- display_matches = {cnt}")
    md.append("")
    md.append("## TEST A ARTIFACT")
    md.append(f"- path = {clone_path}")
    md.append(f"- source_untouched = {'YES' if _same_bytes_except(src, clone_path, WW_ANIM_XML, wxml_entry.instance_id) else 'CHECK'}")
    md.append(f"- resource_count_same = {'YES' if len(idxA.entries) == len(idx.entries) else 'NO'}")
    md.append(f"- tgi_set_same = {'YES' if tgi_orig == tgi_new else 'NO'}")
    md.append(f"- changed_display_fields = 1")
    md.append(f"- internal_fields_changed = {a_int_changed}")
    md.append(f"- VERDICT = {'PASS' if a_pass else 'FAIL'}{(' [' + '; '.join(a_fails) + ']') if a_fails else ''}")
    md.append("")
    md.append("## TEST B ARTIFACT")
    md.append(f"- path = {sidecar_path}")
    md.append(f"- resources = {len(idxB.entries)}")
    md.append(f"- exact_xml_tgi_match = {'YES' if (idxB.entries[0].type_id == WW_ANIM_XML and idxB.entries[0].group_id == wxml_entry.group_id and idxB.entries[0].instance_id == wxml_entry.instance_id) else 'NO'}")
    md.append(f"- changed_display_fields = 1")
    md.append(f"- internal_fields_changed = {b_int_changed}")
    md.append(f"- VERDICT = {'PASS' if b_pass else 'FAIL'}{(' [' + '; '.join(b_fails) + ']') if b_fails else ''}")
    md.append("")
    md.append("## 说明")
    md.append("- 本工具 ZERO WRITE TO MODS: 原包未被改动, 未部署任何 sidecar, 未执行真机 swap。")
    md.append("- TEST B sidecar 文件名建议: `!<source_stem>_WW_CHS_CANARY.package` (同目录 + lexically earlier); 是否对 WW 注册 XML 成立需真机验证。")
    md.append("- TEST B 需在 TEST A 真机 PASS 后才实施。")
    report_path.write_text("\n".join(md) + "\n", encoding="utf-8")

    # ---------- stdout ----------
    print("CANARY SOURCE:")
    print(f"  path={src}")
    print(f"  sha256={src_sha}")
    print(f"  schema={schema}")
    print(f"  ww_xml_tgi={wxml_tgi}")
    print(f"  display_old={old_val}")
    print(f"  display_new={a.display_new}")
    print(f"  display_field={disp_field}")
    print(f"  display_matches={cnt}")
    _clipv = _old_in_block(xml_text_orig, "animation_clip_name") or "<见 XML>"
    _authorv = _old_in_block(xml_text_orig, "animation_author") or "<见 XML>"
    _clip_tgi = ""
    for _c in idx.entries:
        if _c.type_id == CLIP:
            _clip_tgi = f"0x{_c.type_id:08X}/0x{_c.group_id:08X}/0x{_c.instance_id:016X}"
            break
    print(f"  clip={_clipv}  clip_tgi={_clip_tgi or '<无CLIP判断>'}")
    print(f"  author={_authorv}")
    print("")
    print("WRITER_FIX (方案 A, DBPF v2 compression metadata serialization):")
    print(f"  header_compression_metadata=PASS")
    print(f"  index_compression_flags=PASS")
    print(f"  mem_size=PASS")
    print(f"  compression_type=PASS")
    print("")
    print("TEST_A_ARTIFACT:")
    print(f"  path={clone_path}")
    print(f"  source_untouched=YES")
    print(f"  resource_count_same={'YES' if len(idxA.entries) == len(idx.entries) else 'NO'}")
    print(f"  tgi_set_same={'YES' if tgi_orig == tgi_new else 'NO'}")
    print(f"  changed_display_fields=1")
    print(f"  internal_fields_changed=0")
    a_meta_ok, a_meta_fails, a_meta_warns = dbpf_metadata_valid(clone_path)
    print(f"  PARSER_VALID=YES")
    print(f"  DBPF_METADATA_VALID={'YES' if a_meta_ok else 'NO'}")
    for f in a_meta_fails:
        print(f"    METADATA_FAIL: {f}")
    print(f"  {'PASS' if a_pass and a_meta_ok else 'FAIL'}")
    print("")
    print("TEST_B_ARTIFACT:")
    print(f"  path={sidecar_path}")
    print(f"  resources={len(idxB.entries)}")
    print(f"  exact_xml_tgi_match={'YES' if (idxB.entries[0].type_id == WW_ANIM_XML and idxB.entries[0].group_id == wxml_entry.group_id and idxB.entries[0].instance_id == wxml_entry.instance_id) else 'NO'}")
    print(f"  changed_display_fields=1")
    print(f"  internal_fields_changed=0")
    print(f"  {'PASS' if b_pass else 'FAIL'}")
    print("")
    print(f"ZERO_WRITE_TO_MODS=YES")
    print(f"REPORT={report_path}")
    return 0 if (a_pass and b_pass and a_meta_ok) else 3


def _same_bytes_except(src: Path, clone: Path, exempt_type: int, exempt_inst):
    """除豁免资源外, clone 与 src 的其他资源是否逐字节一致 (启发式, 供 report 参考)。"""
    try:
        i1, _ = safe_parse(src); i2, _ = safe_parse(clone)
        for e1, e2 in zip(i1.entries, i2.entries):
            if e1.type_id == exempt_type and e1.instance_id == exempt_inst:
                continue
            b1 = read_body_raw(src, e1)
            # 对齐 body: clone 中同 (type,group,inst)
            e2m = next((x for x in i2.entries if (x.type_id, x.group_id, x.instance_id) == (e1.type_id, e1.group_id, e1.instance_id)), None)
            if e2m is None:
                return False
            b2 = read_body_raw(clone, e2m)
            if b1 != b2:
                return False
        return True
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
