#!/usr/bin/env python3
"""只读合并: 以现有 production translation_overrides.csv 为 BASE,
叠加本轮 73 条 (SAFE 34 + RESOLVED 17 + FINAL 22) -> translation_overrides.final.csv

关键语义 (用户 2026-08-14 定案):
  - BASE = 现有 production translation_overrides.csv (旧 override, 如清理 ERROR 的 22 条)
  - 本轮新增 = SAFE(34) + RESOLVED(17) + FINAL22(22) = 73
  - 匹配键 = translation_id + source_text
  - 重叠且 译文/action 相同 -> 去重 (取一条)
  - 重叠且 译文/action 不同 -> STOP, 列出 conflict, 不进 final, 不允许静默覆盖
  - 不重叠 -> 全部保留
  - 最终 = BASE ∪ 本轮 (union), 数量按实际集合计算, 不硬凑

安全保证:
  - 只读输入; 输出写到 <out_dir>/translation_overrides.final.csv (不覆盖 production 原文件)
  - 不调 LLM / 不改 cache / 不写 package
  - 任一 conflict 或 missing 时, 不写 final 文件 (STOP)

用法:
  python scripts/merge_overrides.py <out_dir> [final_csv]

  final_csv 可选: Dorothy 22 条定稿 CSV 路径 (缺省 <out_dir>/dorothy_final_22.csv)
  本轮 source_text 取自 review_52_candidates / dorothy_resolved / dorothy_still_needs_review (真实数据)
"""
import sys, csv
from pathlib import Path

COLS = ["translation_id", "source_text", "translation", "action", "reason", "notes"]

def load_rows(path):
    if not path.exists():
        print(f"[!] 缺 {path}")
        sys.exit(1)
    return [dict(r) for r in csv.DictReader(open(path, encoding="utf-8-sig"))]

def key(r):
    return (r.get("translation_id", "").strip(), r.get("source_text", "").strip())

def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/merge_overrides.py <out_dir> [final_csv]")
        sys.exit(1)
    out = Path(sys.argv[1])
    fin = Path(sys.argv[2]) if len(sys.argv) >= 3 else out / "dorothy_final_22.csv"

    base_path = out / "translation_overrides.csv"
    cand = out / "review_52_candidates.csv"
    res  = out / "dorothy_resolved.csv"

    # ---------- 载入 & 构建本轮 73 条 ----------
    final_map = {}
    for r in load_rows(fin):
        t = (r.get("translation_id") or "").strip()
        z = (r.get("final_translation") or "").strip()
        if t:
            final_map[t] = z

    new_rows = []          # 本轮 73
    missing = []           # 缺译文/缺 source 的 tid
    src_index = {}         # tid -> source_text (从真实 CSV 回填)

    # source_text 回填池: SAFE/RESOLVED 各自带 source; FINAL 从 still/needs 找
    def collect_src(tid):
        if tid in src_index:
            return
        for f in ("dorothy_still_needs_review.csv", "dorothy_needs_review.csv", "review_52_candidates.csv"):
            p = out / f
            if not p.exists():
                continue
            for r in csv.DictReader(open(p, encoding="utf-8-sig")):
                if (r.get("translation_id") or "").strip() == tid:
                    src_index[tid] = (r.get("source_text") or "").strip()
                    return

    # 1) SAFE (34)
    safe_rows = [r for r in load_rows(cand) if (r.get("category") or "").strip() == "SAFE_CANDIDATE"]
    for r in safe_rows:
        zh = (r.get("proposed_translation") or "").strip()
        if not zh:
            missing.append((r["translation_id"], "SAFE 无 proposed")); continue
        new_rows.append({
            "translation_id": r["translation_id"], "source_text": (r.get("source_text") or "").strip(),
            "translation": zh, "action": "TRANSLATE", "reason": "SAFE_CANDIDATE (候选已确认)", "notes": "review_52_candidates",
        })

    # 2) RESOLVED (17)
    for r in load_rows(res):
        if (r.get("_final") or "").strip() != "RESOLVED":
            continue
        zh = (r.get("_final_translation") or "").strip()
        if not zh:
            missing.append((r["translation_id"], "RESOLVED 无译文")); continue
        new_rows.append({
            "translation_id": r["translation_id"], "source_text": (r.get("source_text") or "").strip(),
            "translation": zh, "action": "TRANSLATE", "reason": "RESOLVED (明确规则/Dorothy 成组预定案)",
            "notes": f"dorothy_resolved: {r.get('_resolve_note','')}",
        })

    # 3) FINAL 22 (Dorothy 定稿)
    for tid, zh in final_map.items():
        if not zh:
            missing.append((tid, "22条 无译文")); continue
        collect_src(tid)
        src = src_index.get(tid, "")
        if not src:
            missing.append((tid, "22条 找不到 source_text")); continue
        new_rows.append({
            "translation_id": tid, "source_text": src,
            "translation": zh, "action": "TRANSLATE", "reason": "Dorothy 最终定稿", "notes": "dorothy_final_22",
        })

    # ---------- 载入 BASE (production) ----------
    old_rows = load_rows(base_path)

    # ---------- 集合运算 key 索引 ----------
    new_idx = {}   # key -> row (本轮, 先同 key 去重+查冲突)
    n_conf = []
    for o in new_rows:
        k = key(o)
        if k in new_idx:
            if new_idx[k]["translation"] != o["translation"] or new_idx[k]["action"] != o["action"]:
                n_conf.append((k, new_idx[k], o))
            continue
        new_idx[k] = o
    # 本轮内部冲突即 STOP (不去重覆盖)
    if n_conf:
        print("[STOP] 本轮新增内部存在同 key 不同译文/action 冲突, 不生成 final:")
        for k, a, b in n_conf:
            print(f"   {k}: '{a['translation']}'/'{a['action']}' vs '{b['translation']}'/'{b['action']}'")
        sys.exit(2)

    old_idx = {key(r): r for r in old_rows}   # BASE (假定 BASE 本身无重复)

    # ---------- 重叠 & 冲突 & 缺席校验 ----------
    missing_old = [k for k in old_idx if k not in new_idx]   # 旧有而本轮无 -> 保留
    overlap = [k for k in old_idx if k in new_idx]
    old_only = [k for k in old_idx if k not in new_idx]
    new_only = [k for k in new_idx if k not in old_idx]

    conflicts = []   # overlap 中译文/action 不一致
    for k in overlap:
        a, b = old_idx[k], new_idx[k]
        if (a.get("translation") or "").strip() != (b.get("translation") or "").strip() or \
           (a.get("action") or "").strip() != (b.get("action") or "").strip():
            conflicts.append((k, a, b))

    # ---------- 汇总 ----------
    print("\n========== 集合合并报告 ==========")
    print(f"  old count (production BASE) : {len(old_rows)}")
    print(f"  new count (本轮 73)         : {len(new_rows)}")
    print(f"  overlap                     : {len(overlap)}")
    print(f"  old_only (保留)             : {len(old_only)}")
    print(f"  new_only (新增)             : {len(new_only)}")
    print(f"  union count                 : {len(old_only) + len(new_only) + len(overlap)}")
    print(f"  missing old (旧条须全保留)  : {len(missing_old)}  (旧有但本轮无 -> 全部保留, 故应=old_only)")
    print(f"  conflicts (译文/action不同) : {len(conflicts)}")

    if overlap:
        print("\n  [overlap 明细] (同 key 相同时刻按 old 译文核对 new 是否一致):")
        for k in sorted(overlap):
            same = "相同" if (old_idx[k].get("translation") or "").strip() == (new_idx[k].get("translation") or "").strip() else "不同!"
            print(f"      {k[0]} | old='{old_idx[k]['translation']}' | new='{new_idx[k]['translation']}' | {same}")
    if conflicts:
        print("\n  [CONFLICT 明细] (必须人工解决, 不写入 final):")
        for k, a, b in conflicts:
            print(f"      {k[0]} | {k[1]!r}")
            print(f"         old: '{a['translation']}' / {a['action']} | reason: {a.get('reason','')}")
            print(f"         new: '{b['translation']}' / {b['action']} | reason: {b.get('reason','')}")

    # ---------- 阻塞条件 ----------
    blocked = bool(conflicts) or bool(missing) or (len(missing_old) != len(old_only))
    if missing:
        print(f"\n  [missing] {len(missing)} 条无法取得译文/source (本轮):")
        for tid, why in missing:
            print(f"      {tid}: {why}")

    if blocked:
        print("\n  => BLOCKED ✗: 存在新冲突/缺失, 不生成 final 文件")
        print("     已跳过覆盖; 请先人工解决冲突或补齐缺失后重跑")
        sys.exit(2)

    # ---------- 构建 final (union): old_all + new_only ----------
    union = []
    for k in old_idx:                 # 先全部保留 old (含 overlap 用 old 版本)
        union.append(dict(old_idx[k]))
    for k in new_only:                # 追加本轮独有
        union.append(dict(new_idx[k]))
    # union 里 overlap 保持 old; new 独有附加。去重保护:
    seen = set(); final_rows = []
    for r in union:
        k = key(r)
        if k in seen:
            continue
        seen.add(k); final_rows.append(r)

    dest = out / "translation_overrides.final.csv"
    with open(dest, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in final_rows:
            w.writerow({c: r.get(c, "") for c in COLS})

    print(f"\n  final count                : {len(final_rows)}")
    print(f"\n[写出] {dest}  (未覆盖 production translation_overrides.csv)")
    print("\n全部通过 ✓ (无 conflict / 无 missing / old 全保留)")
    print("\n确认 final 后再启用: 用 final 覆盖 production, 再做 0 LLM 重物化 + QA。")
    return 0

if __name__ == "__main__":
    sys.exit(main())
