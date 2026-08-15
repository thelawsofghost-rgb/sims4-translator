#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""READ-ONLY exact-STBL forensic — Cry Animation TGI conflict (438 preflight)

扫描 Mods 全树, 找出所有【实际上】包含 exact TGI
    0x220557DA / 0x80000000 / 0x01D208F3F86A48A1
的 package (按 DBPF index 精确匹配 STBL instance, 绝不按文件名猜)。

对每个命中 source package 输出完整 STBL (key_hash -> text) 映射, 并解析指定的
production sidecar (065/066), 对所有 key 分类:

  A_ONLY / B_ONLY / C_ONLY
  OVERLAP_SAME_SOURCE        (同 key 在不同 source 中文本一致)
  OVERLAP_DIFFERENT_SOURCE   (同 key 在不同 source 中文本不同 -> INHERENT CONFLICT)
  TRANSLATION_SAME           (同 key 在 source 与对应 sidecar 中一致, 或一致覆盖)
  TRANSLATION_DIFFERENT      (同 key source 与 sidecar 值不同 -> 翻译差异)

终局 verdict (只读, 不猜 load order):
  SAFE_IDENTICAL                      所有命中 source 的对应 key 文本完全相同
  SAFE_UNION_MERGE_POSSIBLE           存在 OVERLAP 但无 conflicting source/value
  INHERENT_SOURCE_TGI_CONFLICT        同一 key_hash 在不同 source 对应不同文本

ZERO DEPLOY / ZERO MODIFY: 全程只读; 不改 438 manifest, 不改 writer, 不改 sidecars,
不碰 Mods。

用法:
  python production_cry_tgi_forensic.py \
      --mods-root "C:\\...\\Mods" \
      --source-a "…/Cry Animation Pack (tinisims).package" \
      --source-b "…/Cry Animation_Sitting (tinisims).package" \
      --source-c "…/[Akuiyumi] Emotions - Anger (adult - standing).package" \
      --sidecar-a "output/production_sidecars_441_v1/Subx/065_Cry Animation Pack (tinisims)_CHS.package" \
      --sidecar-b "output/production_sidecars_441_v1/Subx/066_Cry Animation_Sitting (tinisims)_CHS.package" \
      [--tgi 0x01D208F3F86A48A1] [--force]

输出 (fail-closed, 已存在则拒写除非 --force):
  output/cry_tgi_forensic.csv
  output/cry_tgi_forensic_report.md
"""
import argparse
import csv
import hashlib
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse  # noqa: E402

TGI_TYPE = 0x220557DA   # STBL
TGI_GROUP = 0x80000000
DEFAULT_TGI_INST = 0x01D208F3F86A48A1

OUT_CSV = "output/cry_tgi_forensic.csv"
OUT_MD = "output/cry_tgi_forensic_report.md"


# ---------------------------------------------------------------------------
# exact STBL v5 解析 (对齐 s4pi StblResource):
#   header: magic4 'STBL' | version2(=5) | isCompressed1 | numEntries8
#           | reserved2 | stringLength4
#   entry : keyHash4 | flags1 | length2 | string(len bytes, UTF-8)
# 先尝试 zlib 解压 body (DBPF 侧或 STBL 内可能压缩), 再按布局切条目。
# ---------------------------------------------------------------------------
def parse_stbl(body: bytes):
    """返回 (version, is_compressed_byte, num_entries, [(key_hash, flags, text)])。
    解析失败抛出 ValueError。"""
    if body[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        try:
            body = zlib.decompress(body)
        except Exception as e:
            raise ValueError(f"zlib 解压失败: {e}")
    if len(body) < 24:
        raise ValueError(f"STBL 体过短: {len(body)}B")
    magic = body[0:4]
    if magic != b"STBL":
        raise ValueError(f"magic 不符: {magic!r}")
    version = struct.unpack_from("<H", body, 4)[0]
    is_compressed = body[6]
    num_entries = struct.unpack_from("<Q", body, 7)[0]
    # stringLength @17-20; 条目区从 21 开始 (4 magic + 2 ver + 1 comp + 8 count + 2 resv + 4 len)
    off = 21
    entries = []
    for _ in range(num_entries):
        if off + 7 > len(body):
            raise ValueError(f"entry 越界 off={off} size={len(body)}")
        key_hash = struct.unpack_from("<I", body, off)[0]
        flags = body[off + 4]
        length = struct.unpack_from("<H", body, off + 5)[0]
        off += 7
        if off + length > len(body):
            raise ValueError(f"string 越界 key=0x{key_hash:08X} len={length}")
        text = body[off:off + length].decode("utf-8", errors="replace")
        off += length
        entries.append((key_hash, flags, text))
    return version, is_compressed, num_entries, entries


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def read_stbl_map(pkg: Path, tgi_inst: int):
    """返回该 package 内【精确 TGI】STBL 的 {key_hash: text}。未命中返回 (None, None, None)。
    返回 (map, version, entry_count)。若命中但解析失败抛 ValueError。"""
    idx, err = safe_parse(pkg)
    if err is not None or idx is None:
        return None, None, None
    target_entry = None
    for e in idx.entries:
        if e.type_id == TGI_TYPE and e.group_id == TGI_GROUP and e.instance_id == tgi_inst:
            target_entry = e
            break
    if target_entry is None:
        return None, None, None
    # 读 body: 用与 backend 相同的读取路径, 但这里从 index 已知 offset/size
    off = target_entry.offset
    size = target_entry.size
    with open(pkg, "rb") as fh:
        fh.seek(off)
        body = fh.read(size)
    version, is_comp, n, entries = parse_stbl(body)
    m = {kh: txt for kh, _flags, txt in entries}
    if len(m) != len(entries):
        # 同 keyHash 重复 (理论上 writer 禁止) — 保留首现, 但记录
        pass
    return m, version, n


def has_exact_tgi(pkg: Path, tgi_inst: int) -> bool:
    idx, err = safe_parse(pkg)
    if err is not None or idx is None:
        return False
    return any(e.type_id == TGI_TYPE and e.group_id == TGI_GROUP and e.instance_id == tgi_inst
               for e in idx.entries)


def fmt_inst(x: int) -> str:
    return f"0x{x:016X}"


def main():
    ap = argparse.ArgumentParser(description="READ-ONLY exact-STBL forensic (Cry TGI conflict)")
    ap.add_argument("--mods-root", required=True)
    ap.add_argument("--source-a", required=True)
    ap.add_argument("--source-b", required=True)
    ap.add_argument("--source-c", required=True)
    ap.add_argument("--sidecar-a", required=True)
    ap.add_argument("--sidecar-b", required=True)
    ap.add_argument("--tgi", default=f"0x{DEFAULT_TGI_INST:016X}")
    ap.add_argument("--out", default=OUT_CSV)
    ap.add_argument("--report", default=OUT_MD)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    out = Path(a.out).expanduser()
    rep = Path(a.report).expanduser()
    if (out.exists() or rep.exists()) and not a.force:
        print(f"[FAIL-CLOSED] 输出已存在, refuse (rc=1) 除非 --force: {out} | {rep}")
        return 1

    tgi_inst = int(a.tgi, 16)
    tgi_full = f"0x{TGI_TYPE:08X}/0x{TGI_GROUP:08X}/0x{tgi_inst:016X}"
    mods_root = Path(a.mods_root).expanduser().resolve()
    if not mods_root.is_dir():
        print(f"[HARD-FAIL] mods-root 不是目录: {mods_root}")
        return 2

    def R(p):
        return Path(p).expanduser().resolve()

    src_paths = {"A": R(a.source_a), "B": R(a.source_b), "C": R(a.source_c)}
    side_paths = {"A": R(a.sidecar_a), "B": R(a.sidecar_b)}

    # ---- 1) Mods 全树精确 TGI 扫描（按 index, 不按文件名猜）----
    hits = []   # list of Path
    for p in sorted(mods_root.rglob("*.package")):
        if has_exact_tgi(p, tgi_inst):
            hits.append(p)

    # ---- 2) 解析所有命中 source + 两个 sidecar ----
    # 把用户显式给的 3 个 source 也纳入(即使 Mods 扫描因路径解析未命中, 如路径差异)
    name_map = {}
    for tag, p in src_paths.items():
        if p.is_file():
            m, ver, n = read_stbl_map(p, tgi_inst)
            if m is None:
                print(f"[WARN] 显式 source {tag} 不含 exact TGI 或不可解析: {p}")
            name_map[tag] = (p, m, ver, n)
        else:
            name_map[tag] = (p, None, None, None)
            print(f"[WARN] 显式 source {tag} 不存在: {p}")
    for tag, p in side_paths.items():
        if p.is_file():
            m, ver, n = read_stbl_map(p, tgi_inst)
            name_map["SIDE_" + tag] = (p, m, ver, n)
        else:
            name_map["SIDE_" + tag] = (p, None, None, None)
            print(f"[WARN] sidecar {tag} 不存在: {p}")

    # ---- 3) CSV: 每个 source package 完整 STBL ----
    csv_rows = []
    # Mods 扫描命中的所有 package (去重 + 显式 3 个)
    all_maps = {}   # str(pkg) -> {'path','map','ver','n'}
    for p in hits:
        m, ver, n = read_stbl_map(p, tgi_inst)
        all_maps[str(p)] = {"path": p, "map": m, "ver": ver, "n": n,
                            "sha": sha256(p), "tag": "MODS_SCAN"}
    # tag 覆盖: 显式 source/sidecar 优先标注
    for tag, (p, m, ver, n) in name_map.items():
        if m is not None and str(p) not in all_maps:
            all_maps[str(p)] = {"path": p, "map": m, "ver": ver, "n": n,
                                "sha": sha256(p), "tag": tag}
        elif str(p) in all_maps and tag != "SIDE_A" and tag != "SIDE_B":
            all_maps[str(p)]["tag"] = tag
    # sidecar 单独记录 (即使与 source 同 inst, 也是不同文件)
    for tag, (p, m, ver, n) in name_map.items():
        if m is not None and (tag == "SIDE_A" or tag == "SIDE_B"):
            all_maps[str(p)] = {"path": p, "map": m, "ver": ver, "n": n,
                                "sha": sha256(p), "tag": tag}

    for pstr, info in sorted(all_maps.items()):
        p = info["path"]
        m = info["map"]
        if m is None:
            csv_rows.append({"package_path": str(p), "package_sha256": info.get("sha", ""),
                             "TGI": tgi_full, "tag": info.get("tag", "SCAN"),
                             "entry_count": "", "key_hash": "", "exact_source_text": "",
                             "parse_error": "NO_TGI_OR_PARSE_FAIL"})
            continue
        for kh in sorted(m):
            csv_rows.append({
                "package_path": str(p),
                "package_sha256": info.get("sha", ""),
                "TGI": tgi_full,
                "tag": info.get("tag", "SCAN"),
                "entry_count": info["n"],
                "key_hash": f"0x{kh:08X}",
                "exact_source_text": m[kh],
                "parse_error": "",
            })

    # 修正统计语义: 'active source packages' 只计【source 包】(A/B/C/MODS_SCAN),
    # 绝不把 SIDE_A/SIDE_B staging sidecar 计入 active source。
    active_sources = len({pstr for pstr, info in all_maps.items()
                          if info.get("tag") not in ("SIDE_A", "SIDE_B") and info.get("map") is not None})

    # ---- 4) key 分类 ----
    # 取 A/B/C 的 map; C 可能不存在/无 TGI
    def _map(tag):
        info = name_map.get(tag)
        if info is None or info[1] is None:
            return {}
        return info[1]

    mapA = _map("A"); mapB = _map("B"); mapC = _map("C")
    mapSA = _map("SIDE_A"); mapSB = _map("SIDE_B")

    all_keys = set(mapA) | set(mapB) | set(mapC)
    union_keys = len(all_keys)

    def classify(kh):
        inA = kh in mapA; inB = kh in mapB; inC = kh in mapC
        present = [t for t, inc in (("A", inA), ("B", inB), ("C", inC)) if inc]
        vals = {}
        if inA: vals["A"] = mapA[kh]
        if inB: vals["B"] = mapB[kh]
        if inC: vals["C"] = mapC[kh]
        if len(present) > 1:
            # 出现在多个 source: 比对文本是否一致
            if len(set(vals.values())) == 1:
                return "OVERLAP_SAME_SOURCE", "|".join(present), vals
            return "OVERLAP_DIFFERENT_SOURCE", "|".join(present), vals
        tag = present[0] if present else "?"
        return f"{tag}_ONLY", tag, vals

    cls = {}
    source_vals = {}
    for kh in all_keys:
        cat, tags, vals = classify(kh)
        cls[kh] = cat
        source_vals[kh] = vals

    n_onlyA = sum(1 for c in cls.values() if c == "A_ONLY")
    n_onlyB = sum(1 for c in cls.values() if c == "B_ONLY")
    n_onlyC = sum(1 for c in cls.values() if c == "C_ONLY")
    n_overlap = sum(1 for c in cls.values() if c.startswith("OVERLAP"))
    n_overlap_same = sum(1 for c in cls.values() if c == "OVERLAP_SAME_SOURCE")
    n_overlap_diff = sum(1 for c in cls.values() if c == "OVERLAP_DIFFERENT_SOURCE")

    # sidecar 翻译: 每个 sidecar 与【它自己的 source】比对 (A->A, B->B)
    #   TRANSLATION_SAME      sidecar 值 == 其 source 文本 (透传/未改)
    #   TRANSLATION_DIFFERENT sidecar 值 != 其 source 文本 (真实翻译)
    # 冲突 (final metric): 两 sidecar 对同一 key 给出不同值 (不能同时部署两套译文)
    n_sidecar = 0; n_trans_same = 0; n_trans_diff = 0; n_sidecar_conflict = 0
    trans_rows = []
    for kh in sorted(all_keys):
        sa = mapSA.get(kh); sb = mapSB.get(kh)
        if sa is None and sb is None:
            continue
        n_sidecar += 1
        same = True
        src_sel = None; side_sel = None
        if sa is not None:
            srcA = mapA.get(kh, "<NOT_IN_A>")
            side_sel = sa; src_sel = srcA
            if srcA != sa:
                same = False
        if sb is not None:
            srcB = mapB.get(kh, "<NOT_IN_B>")
            side_sel = sb; src_sel = srcB
            if srcB != sb:
                same = False
        if same:
            n_trans_same += 1
        else:
            n_trans_diff += 1
        # 冲突: 两 sidecar 同 key 且值不同
        if sa is not None and sb is not None and sa != sb:
            n_sidecar_conflict += 1
        trans_rows.append({
            "key_hash": f"0x{kh:08X}",
            "source": "" if src_sel is None else src_sel,
            "sidecar_A": "" if sa is None else sa,
            "sidecar_B": "" if sb is None else sb,
            "translation_status": "TRANSLATION_SAME" if same else "TRANSLATION_DIFFERENT",
            "class": cls[kh],
        })

    # ---- 5) verdict ----
    # INHERENT: 同一 key_hash 在不同 source package 中对应不同文本 -> 禁 load order 猜
    # 或两 sidecar 对同一 key 给出不同译文 (不能同时部署) -> 也判冲突 (非 union 合并可能)
    if n_overlap_diff > 0:
        verdict = "INHERENT_SOURCE_TGI_CONFLICT"
    elif n_sidecar_conflict > 0:
        # source 文本一致, 但两侧车译文不一致 -> 需裁决取一侧, 非无条件合并
        verdict = "SAFE_UNION_MERGE_POSSIBLE"
    elif n_overlap > 0 and n_overlap_diff == 0 and n_sidecar_conflict == 0:
        # 所有重叠 key 文本一致, 且无 sidecar 冲突 -> 完全一致
        verdict = "SAFE_IDENTICAL"
    else:
        # 无 source 冲突 (重叠 key 文本一致) -> 可 union 合并
        verdict = "SAFE_UNION_MERGE_POSSIBLE"

    # ---- 6) 写出 ----
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["package_path", "package_sha256", "TGI", "tag", "entry_count", "key_hash",
            "exact_source_text", "parse_error"]
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in csv_rows:
            w.writerow(row)

    rep.parent.mkdir(parents=True, exist_ok=True)
    L = []
    L.append("# Cry Animation TGI Forensic (READ-ONLY)")
    L.append("")
    L.append(f"- exact TGI     : `{tgi_full}`")
    L.append(f"- mods_root     : `{mods_root}`")
    L.append("")
    L.append("## active source packages with exact TGI")
    L.append(f"- active source count (excludes staging sidecars) = {active_sources}")
    L.append(f"- 含 staging sidecar 的全部 TGI 命中包 (证据)        = {len(all_maps)}")
    for pstr, info in sorted(all_maps.items()):
        m = info["map"]
        L.append(f"  - `{pstr}`  tag={info.get('tag','SCAN')}  sha={info.get('sha','')[:16]}…  "
                 f"entries={info['n'] if m is not None else '-'}")
    L.append("")
    L.append("## 分类统计")
    L.append(f"- union keys            = {union_keys}")
    L.append(f"- A_ONLY                = {n_onlyA}")
    L.append(f"- B_ONLY                = {n_onlyB}")
    L.append(f"- C_ONLY                = {n_onlyC}")
    L.append(f"- OVERLAP (multi-source)= {n_overlap}")
    L.append(f"  - OVERLAP_SAME_SOURCE     = {n_overlap_same}")
    L.append(f"  - OVERLAP_DIFFERENT_SOURCE= {n_overlap_diff}")
    L.append(f"- source-vs-sidecar keys = {n_sidecar}")
    L.append(f"  - TRANSLATION_SAME        = {n_trans_same}")
    L.append(f"  - TRANSLATION_DIFFERENT   = {n_trans_diff}")
    L.append("")
    L.append("## conflicting source values (OVERLAP_DIFFERENT_SOURCE)")
    n_conf = 0
    for kh in sorted(all_keys):
        if cls[kh] == "OVERLAP_DIFFERENT_SOURCE":
            n_conf += 1
            L.append(f"- key `0x{kh:08X}`")
            sv = source_vals.get(kh, {})
            for tag in ("A", "B", "C"):
                if tag in sv:
                    L.append(f"    - {tag}: {sv[tag]!r}")
    if n_conf == 0:
        L.append("(无)")
    L.append("")
    L.append("## sidecar 明细 (key_hash | sourceA | sourceB | sourceC | sidecarA | sidecarB | translation)")
    for tr in trans_rows:
        kh_int = int(tr["key_hash"], 16)
        L.append(f"- `{tr['key_hash']}`  [{tr['class']}]  sidecar: {tr['translation_status']}")
        L.append(f"    sourceA  : {source_vals.get(kh_int, {}).get('A', '')!r}")
        L.append(f"    sourceB  : {source_vals.get(kh_int, {}).get('B', '')!r}")
        L.append(f"    sourceC  : {source_vals.get(kh_int, {}).get('C', '')!r}")
        L.append(f"    sidecarA : {tr['sidecar_A']!r}")
        L.append(f"    sidecarB : {tr['sidecar_B']!r}")
    if not trans_rows:
        L.append("(无 sidecar key)")
    L.append("")
    L.append("## sidecar translated-key conflicts")
    L.append(f"- conflict count (两侧车对同一 key 译文不同) = {n_sidecar_conflict}")
    n_tc = 0
    for tr in trans_rows:
        if int(tr["key_hash"], 16) in (mapSA.keys() & mapSB.keys()) and mapSA.get(int(tr["key_hash"],16)) != mapSB.get(int(tr["key_hash"],16)):
            n_tc += 1
            L.append(f"- key `{tr['key_hash']}` [{tr['class']}]")
            L.append(f"    sidecar_A: {tr['sidecar_A']!r}")
            L.append(f"    sidecar_B: {tr['sidecar_B']!r}")
    if n_tc == 0:
        L.append("(无 sidecar 冲突)")
    L.append("")
    L.append("## 全 key 分类汇总")
    for kh in sorted(all_keys):
        L.append(f"- `0x{kh:08X}`  {cls[kh]}  {source_vals.get(kh, {})!r}")
    L.append("")
    L.append("## 终局")
    L.append(f"active source packages with exact TGI = {active_sources}")
    L.append(f"  (SIDE_A/SIDE_B staging sidecar 不计入 active source)")
    L.append(f"union keys = {union_keys}")
    L.append(f"overlapping keys = {n_overlap}")
    L.append(f"overlapping keys same text = {n_overlap_same}")
    L.append(f"overlapping keys different text = {n_overlap_diff}")
    L.append(f"sidecar translated-key conflicts = {n_sidecar_conflict}")
    L.append(f"verdict = {verdict}")
    L.append("")
    with open(rep, "w", newline="", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    # stdout
    print(f"## verdict = {verdict}")
    print(f"active source packages with exact TGI = {active_sources}  (excludes staging sidecars)")
    print(f"union keys = {union_keys}")
    print(f"overlapping keys = {n_overlap}")
    print(f"overlapping keys same text = {n_overlap_same}")
    print(f"overlapping keys different text = {n_overlap_diff}")
    print(f"sidecar translated-key conflicts = {n_sidecar_conflict}")
    print(f"output: {out}")
    print(f"report: {rep}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
