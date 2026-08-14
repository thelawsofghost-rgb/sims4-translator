#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""diag_invariant_duplicates.py — ERROR_COVERAGE_INVARIANT 只读诊断
(不改 pose_coverage.py / cohort / writer; 不生成 sidecar)

对指定 package 的 exact target CHS STBL 逐项输出:
  * physical_entry_count / unique_key_hash_count / duplicate_key_hash_count / duplicate key hashes
  * 每个 duplicate hash 的 occurrence count + duplicate entries 的 flags + source text
  * target physical entries / target unique hashes
  * TRANSLATE_KEY_SET / KEEP_KEY_SET / UNMAPPED_KEY_SET (与正式 coverage 同一口径:
    XML 结构引用 + 位置门控, 只限 exact CHS target STBL)
  * 三集合 union size 与 overlap
  * 判断 CHS_entry_count - (T+K+U) 是否恰等于 duplicate-keyhash 数
  * 若否, 列出 “既不属于 T/K/U 的 exact target keys” 及其文本/provenance

用法:
  python scripts\\diag_invariant_duplicates.py "<package>" [更多包...]
"""
import sys
import os
import struct
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

import pose_coverage as pc          # 只复用只读解析函数, 不改它  # noqa: E402
from dbpf_fast import safe_parse     # noqa: E402
from backend import get_backend      # noqa: E402

STBL_TID = 0x220557DA
LOCALE_CHS = 0x01


def load_target(path):
    """返回 (target_keys_physical, translate_ref_keys, keep_ref_keys, row)
    - target_keys_physical: 该 exact CHS target STBL 的【物理】entries [(kh, fl, txt)] (含重复)
    - translate_ref_keys / keep_ref_keys: 与正式 coverage 同源同门控的 hash 集合
    - row: scan_package 的完整 row
    """
    row = pc.scan_package(path)
    idx, err = safe_parse(path)
    if err or idx is None:
        return None, set(), set(), row
    backend = get_backend("readonly").open(path)

    # --- 与 scan_package 相同: 解析所有 STBL ---
    stbl_parsed = {}
    for e in idx.entries:
        if e.type_id != STBL_TID:
            continue
        try:
            data = backend.read_small_resource(e, max_bytes=2 * 1024 * 1024)
        except Exception:
            data = None
        pr = pc.parse_stbl(data) if data else None
        if pr:
            stbl_parsed[e.instance_id] = pr  # (ver, comp, [(kh,fl,txt)] physical)
    chs = [(i, v, c, kvs) for i, (v, c, kvs) in stbl_parsed.items()
           if ((i >> 56) & 0xFF) == LOCALE_CHS]
    if len(chs) != 1:
        return None, set(), set(), row
    chs_inst, _v, _c, chs_kvs = chs[0]

    # --- 与 scan_package 相同: XML 位置门控收集 translate_ref_keys / keep_ref_keys ---
    xmls = pc.read_xml_payloads(backend, idx.entries)
    posexmls = [x for x in xmls if pc.is_pose_pack_root(x[1])]
    translate_ref_keys = set()
    keep_ref_keys = set()
    for xinst_id, root, _raw in posexmls:
        for el, pack_level, in_pose in pc._walk_ctx(root):
            n = el.attrib.get("n")
            if not n:
                continue
            if any(True for _ in el) and el.text is None:
                continue
            val = (el.text or "").strip()
            if not val:
                continue
            cls = pc._classify_field(n)
            h = pc.parse_display_hash(val)
            if h is None:
                continue
            nl = n.lower()
            pvc = None
            if nl == "display_name" and pack_level:
                pvc = "TRANSLATE"
            elif nl == "description" and pack_level:
                pvc = "TRANSLATE"
            elif nl == "pose_display_name" and in_pose:
                pvc = "TRANSLATE"
            if pvc is not None:
                translate_ref_keys.add(h)
            elif cls == "AUTHORISH":
                keep_ref_keys.add(h)
    return chs_kvs, translate_ref_keys, keep_ref_keys, row


def analyze(path):
    print("=" * 78)
    print("PACKAGE:", os.path.basename(path))
    print("=" * 78)
    chs_kvs, translate_ref_keys, keep_ref_keys, row = load_target(path)
    if chs_kvs is None:
        print("  (无法定位唯一 CHS target STBL — 与 coverage 判定一致)")
        print("  status =", row.get("status"), "| reason =", row.get("reason"))
        return

    physical = chs_kvs                     # [(kh, fl, txt)] 含重复
    phys_n = len(physical)
    unique = set(kh for kh, _, _ in physical)
    dup_counts = Counter(kh for kh, _, _ in physical)
    dups = {kh: c for kh, c in dup_counts.items() if c > 1}
    dup_n = sum(1 for c in dups.values())          # 不同 duplicate hash 个数
    dup_occur_extra = phys_n - len(unique)         # 因重复多出来的 occurrence 数

    print(f"\n[1] exact CHS target STBL 基本量")
    print(f"  physical_entry_count      = {phys_n}")
    print(f"  unique_key_hash_count     = {len(unique)}")
    print(f"  duplicate_key_hash_count  = {dup_n}  (不同 hash 个数)")
    print(f"  duplicate 多出 occurence  = {dup_occur_extra} (= physical - unique)")
    print(f"  CHS_entry_count (coverage)= {row.get('CHS_entry_count')}  <-- physical(含重复)")
    print(f"  T/K/U 加总 (coverage)     = "
          f"{(row.get('exact_structural_translate_count') or 0) + (row.get('keep_count') or 0) + (row.get('unmapped_uncertain_count') or 0)}"
          f"   <-- 基于 unique set")

    if dups:
        print(f"\n[2] duplicate key hashes (共 {dup_n} 个):")
        for kh in sorted(dups):
            occ = dup_counts[kh]
            rows = [(fl, txt) for k, fl, txt in physical if k == kh]
            print(f"  0x{kh:08X}  occurrence={occ}  flags={[fl for fl,_ in rows]}  src={[t[:60] for _,t in rows]}")
        # 一次重复只解释 1 个差值, 不解释 N-1
        dup_keyset_gap = len(unique) - (len(unique) - dup_occur_extra)  # 0 占位便于阅读
    else:
        print(f"\n[2] duplicate key hashes: 无重复 (0 个)")
        dup_occur_extra = 0

    # --- T/K/U 三集合 (与 coverage 同口径, 但显式列出) ---
    target_uniq = set(unique)
    T = translate_ref_keys & target_uniq
    K = keep_ref_keys & target_uniq
    U = target_uniq - T - K
    union = T | K | U
    print(f"\n[3] 三集合 (exact target unique keys):")
    print(f"  TRANSLATE_KEY_SET = {sorted(f'0x{x:08X}' for x in T)}")
    print(f"  KEEP_KEY_SET      = {sorted(f'0x{x:08X}' for x in K)}")
    print(f"  UNMAPPED_KEY_SET  = {sorted(f'0x{x:08X}' for x in U)}")
    print(f"  union size        = {len(union)}  (应为 {len(target_uniq)})")
    print(f"  overlap: T∩K={len(T & K)}  T∩U={len(T & U)}  K∩U={len(K & U)}  T∩K∩U={len(T & K & U)}")
    print(f"  T={len(T)}  K={len(K)}  U={len(U)}   T+K+U={len(T)+len(K)+len(U)}")

    # --- 差值判断 ---
    gap = phys_n - (len(T) + len(K) + len(U))
    print(f"\n[4] 差值判断:")
    print(f"  physical({phys_n}) - (T+K+U)({len(T)+len(K)+len(U)}) = {gap}")
    print(f"  其中因重复多出的 occurrence = {dup_occur_extra}")
    if gap == dup_occur_extra:
        if gap == 0:
            print(f"  -> 无差值 (0 重复), 非 scope 串包")
        else:
            print(f"  -> 差值 {gap} 完全由 duplicate KeyHash 造成 (物理重复计数, 非 scope 串包)")
    else:
        print(f"  -> 差值 {gap} != 重复差额 {dup_occur_extra} : 非重复导致!")
        # 列出既不属于 T/K/U 的 exact target keys (unique 层面)
        unowned = set()
        for kh, fl, txt in physical:
            if kh not in T and kh not in K and kh not in U:
                unowned.add((kh, fl, txt))
        if unowned:
            print(f"\n[5] 既不属于 T/K/U 的 exact target entries:")
            for kh, fl, txt in sorted(unowned):
                print(f"  0x{kh:08X}  flags={fl}  src={txt[:80]!r}")
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python scripts\\diag_invariant_duplicates.py <package> [more...]")
        sys.exit(1)
    for p in sys.argv[1:]:
        if not os.path.exists(p):
            print("NOT FOUND:", p)
            continue
        try:
            analyze(p)
        except Exception as e:
            import traceback
            print("DIAG ERROR:", p, repr(e))
            traceback.print_exc()
