#!/usr/bin/env python3
"""final2 增量批: BASE=production(95), 原地改 5, 新增 16 -> translation_overrides.final2.csv

严格集合校验, 不覆盖 production, 不物化/QA/package。
用法: python scripts/apply_final2_batch.py <out_dir>
"""
import csv, sys
from pathlib import Path

OUT = Path(sys.argv[1]) if len(sys.argv) >= 2 else Path("output")

# ---- existing 5 原地修正 (同 key 改 translation; TOOL 专名保留) ----
existing_fix = {
    "T_2cb4ec6fe8d1_g1": "19（使用 TOOL 调整模拟市民）",
    "T_458361189bb4_g1": "20（使用 TOOL 调整模拟市民）",
    "T_af2cde13c672_g1": "16（使用 TOOL 调整模拟市民）",
    "T_afe4d635d5e8_g1": "18（使用 TOOL 调整模拟市民）",
    "T_d15f36883d5e_g1": "17（使用 TOOL 调整模拟市民）",
}

# ---- 新增 16: (tid, source, translation, action, reason) ----
new_rows = [
    # 3 residual sim
    ("T_5432492b9285_g1", "sim sitting on desk", "模拟市民坐在桌子上", "TRANSLATE", "residual sim 修正"),
    ("T_c9e5ecedaad7_g1", "[L2S] down position (thick sim)", "[L2S] 下位姿势（丰满体型模拟市民）", "TRANSLATE", "residual sim 修正"),
    ("T_d2f368620f0b_g1", "[L2S] finger in ass (thick sim L)", "[L2S] 手指插入肛门（丰满体型模拟市民 L）", "TRANSLATE", "residual sim 修正"),
    # Injured_01..10 SEMANTIC_WITH_CODE
    ("T_397e53b3219e_g1", "Injured_01", "受伤_01", "TRANSLATE", "SEMANTIC_WITH_CODE"),
    ("T_3c576a94dbb2_g1", "Injured_02", "受伤_02", "TRANSLATE", "SEMANTIC_WITH_CODE"),
    ("T_e58ff2795a47_g1", "Injured_03", "受伤_03", "TRANSLATE", "SEMANTIC_WITH_CODE"),
    ("T_d0eb9bba6aa2_g1", "Injured_04", "受伤_04", "TRANSLATE", "SEMANTIC_WITH_CODE"),
    ("T_f6f32a04b8b5_g1", "Injured_05", "受伤_05", "TRANSLATE", "SEMANTIC_WITH_CODE"),
    ("T_cb64ded48a3a_g1", "Injured_06", "受伤_06", "TRANSLATE", "SEMANTIC_WITH_CODE"),
    ("T_322707a14c52_g1", "Injured_07", "受伤_07", "TRANSLATE", "SEMANTIC_WITH_CODE"),
    ("T_aae48e736d1c_g1", "Injured_08", "受伤_08", "TRANSLATE", "SEMANTIC_WITH_CODE"),
    ("T_ee8a178eb322_g1", "Injured_09", "受伤_09", "TRANSLATE", "SEMANTIC_WITH_CODE"),
    ("T_6e6bfcdcf55d_g1", "Injured_10", "受伤_10", "TRANSLATE", "SEMANTIC_WITH_CODE"),
    # 2 semantic
    ("T_7592a9a04623_g1", "All_in_one", "整合版", "TRANSLATE", "SEMANTIC_WITH_CODE"),
    ("T_e5352033f844_g1", "Homme_2(F1)", "男性_2(F1)", "TRANSLATE", "SEMANTIC_WITH_CODE"),
    # 1 explicit KEEP
    ("T_2a2ce211e44a_g1", "sofa_footrest", "", "KEEP", "NON_SEMANTIC_TAG / TECHNICAL"),
]

# ---- final3 增批 (Dorothy 拍板 3 条) - BASE 应为 final2 ----
FINAL3_NEW = [
    ("T_544cbac95162_g1", "All In One Right", "整合版 右", "TRANSLATE", "Dorothy 修正"),
    ("T_ebd4ffe75547_g1", "All In One left", "整合版 左", "TRANSLATE", "Dorothy 修正"),
    ("T_b79128b4364c_g1", "Looking Left", "向左看", "TRANSLATE", "Dorothy 修正"),
]

COLS = ["translation_id", "source_text", "translation", "action", "reason", "notes"]

def load(path):
    return [dict(r) for r in csv.DictReader(open(path, encoding="utf-8-sig"))]

def main():
    base_path = OUT / "translation_overrides.csv"
    if not base_path.exists():
        print(f"[!] 缺 BASE: {base_path}"); sys.exit(2)
    base = load(base_path)

    # 索引 key -> 校验重复
    idx = {}
    for r in base:
        k = (r["translation_id"].strip(), r["source_text"].strip())
        if k in idx:
            print(f"[!] BASE 重复 key: {k}"); sys.exit(2)
        idx[k] = r
    print(f"old_count(BASE)          : {len(base)}")

    # ---- existing 5 原地修正 ----
    updated = 0; missing_existing = []
    for tid, new_zh in existing_fix.items():
        cand = [r for r in base if r["translation_id"].strip() == tid]
        if not cand:
            missing_existing.append(tid); continue
        r = cand[0]
        r["translation"] = new_zh
        r["reason"] = "residual sim 修正 (TOOL 专名保留)"
        updated += 1
    print(f"existing_updated         : {updated}")
    print(f"missing_existing         : {len(missing_existing)}")
    for t in missing_existing: print("    ", t)

    # ---- 新增 16 ----
    added = 0; dup = []; src_mm = []
    for tid, src, zh, act, why in new_rows:
        # source 一致性: 若 tid 已在 BASE, 比对 source
        if tid in {r["translation_id"].strip() for r in base}:
            ex = [r for r in base if r["translation_id"].strip() == tid][0]
            e_src = (ex.get("source_text") or "").strip()
            if e_src != src:
                src_mm.append((tid, e_src, src))
        k = (tid, src)
        if k in idx:
            dup.append(k); continue
        idx[k] = None
        base.append({"translation_id": tid, "source_text": src, "translation": zh,
                     "action": act, "reason": why, "notes": "final2 batch"})
        added += 1
    print(f"new_keys                 : {added}")
    print(f"duplicate_key (跳过)     : {len(dup)}")
    for k in dup: print("    ", k)
    print(f"source_text_mismatch     : {len(src_mm)}")
    for a, b, c in src_mm: print(f"     {a}: base='{b}' vs batch='{c}'")

    # ---- final union ----
    final = []; seen = set()
    for r in base:
        k = (r["translation_id"].strip(), r["source_text"].strip())
        if k in seen: continue
        seen.add(k); final.append(r)
    print(f"final_count              : {len(final)}")

    # 校验 expected
    expect = len(base) + added - (len(src_mm))  # 直觉: 95 + added(若无 dup/重叠)
    ok = (len(missing_existing) == 0 and len(dup) == 0 and len(src_mm) == 0)
    print(f"conflict/异常            : {'无' if ok else '有, 见上'}")
    if not ok:
        print("=> BLOCKED, 不写 final2"); sys.exit(2)

    dest = OUT / "translation_overrides.final2.csv"
    with open(dest, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS); w.writeheader()
        for r in final: w.writerow({c: r.get(c, "") for c in COLS})
    print(f"\n[写出] {dest}  (未覆盖 production translation_overrides.csv)")
    print("全部通过 ✓")
    return 0


def mode_final3():
    """final3: BASE=final2(111), 仅新增 Dorothy 拍板 3 条 -> final3(应114)"""
    base_path = OUT / "translation_overrides.final2.csv"
    if not base_path.exists():
        print(f"[!] 缺 BASE final2: {base_path}"); sys.exit(2)
    base = load(base_path)
    idx = {}
    for r in base:
        k = (r["translation_id"].strip(), r["source_text"].strip())
        if k in idx:
            print(f"[!] final2 重复 key: {k}"); sys.exit(2)
        idx[k] = r
    print(f"old_count(BASE final2)   : {len(base)}")

    added = 0; dup = []; src_mm = []
    for tid, src, zh, act, why in FINAL3_NEW:
        if tid in {r["translation_id"].strip() for r in base}:
            ex = [r for r in base if r["translation_id"].strip() == tid][0]
            e_src = (ex.get("source_text") or "").strip()
            if e_src != src:
                src_mm.append((tid, e_src, src))
        k = (tid, src)
        if k in idx:
            dup.append(k); continue
        idx[k] = None
        base.append({"translation_id": tid, "source_text": src, "translation": zh,
                     "action": act, "reason": why, "notes": "final3 batch"})
        added += 1
    print(f"new_keys                 : {added}")
    print(f"duplicate_key (跳过)     : {len(dup)}")
    for k in dup: print("    ", k)
    print(f"source_text_mismatch     : {len(src_mm)}")
    for a, b, c in src_mm: print(f"     {a}: final2='{b}' vs batch='{c}'")

    final = []; seen = set()
    for r in base:
        k = (r["translation_id"].strip(), r["source_text"].strip())
        if k in seen: continue
        seen.add(k); final.append(r)
    print(f"final_count              : {len(final)}")

    ok = (len(dup) == 0 and len(src_mm) == 0)
    print(f"conflict/异常            : {'无' if ok else '有, 见上'}")
    if not ok:
        print("=> BLOCKED, 不写 final3"); sys.exit(2)

    dest = OUT / "translation_overrides.final3.csv"
    with open(dest, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS); w.writeheader()
        for r in final: w.writerow({c: r.get(c, "") for c in COLS})
    print(f"\n[写出] {dest}  (未覆盖 production translation_overrides.csv)")
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    mode = sys.argv[2] if len(sys.argv) >= 3 else "final2"
    if mode == "final3":
        sys.exit(mode_final3())
    sys.exit(main())
