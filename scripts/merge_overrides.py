#!/usr/bin/env python3
"""只读合并: 把 RESOLVED(17) + SAFE(34) + 最终 NEEDS 定稿(22) 合并进 translation_overrides.csv。

安全保证:
  - 只读输入; 输出写到 <out_dir>/translation_overrides.merged.csv (不覆盖原 overrides)
  - override 匹配键 = translation_id + source_text (需精确一致才命中)
  - 所有 source_text 直接来自真实 CSV (review_52_candidates / dorothy_resolved), 不臆造
  - 22 条最终译文来自 <out_dir>/dorothy_final_22.csv  (由 Dorothy 定稿, 见下)
  - 不调 LLM / 不改 cache / 不写 package

用法:
  python scripts/merge_overrides.py <out_dir>

需要先准备好 <out_dir>/dorothy_final_22.csv, 列: translation_id, final_translation
(该文件由人工/Dorothy 提供, 本脚本只读引用, 不代填译文。)
若某 tid 缺失 -> 跳过并告警, 不臆造译文。
"""
import sys, csv
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/merge_overrides.py <out_dir>")
        sys.exit(1)
    out = Path(sys.argv[1])

    cand = out / "review_52_candidates.csv"      # SAFE (proposed_translation)
    res  = out / "dorothy_resolved.csv"           # RESOLVED (_final_translation)
    fin  = out / "dorothy_final_22.csv"           # 22 条 Dorothy 定稿

    if not cand.exists():
        print(f"[!] 缺 {cand}"); sys.exit(1)
    if not res.exists():
        print(f"[!] 缺 {res}"); sys.exit(1)
    if not fin.exists():
        print(f"[!] 缺 {fin} -> 需先提供 Dorothy 22 条定稿译文的 CSV"); sys.exit(1)

    final_map = {}
    for r in csv.DictReader(open(fin, encoding="utf-8-sig")):
        t = (r.get("translation_id") or "").strip()
        z = (r.get("final_translation") or "").strip()
        if t:
            final_map[t] = z

    overrides, missing = [], []

    # 1) SAFE (34)
    safe_rows = [r for r in csv.DictReader(open(cand, encoding="utf-8-sig"))
                 if (r.get("category") or "").strip() == "SAFE_CANDIDATE"]
    for r in safe_rows:
        zh = (r.get("proposed_translation") or "").strip()
        if not zh:
            missing.append((r["translation_id"], "SAFE 无 proposed"))
            continue
        overrides.append({
            "translation_id": r["translation_id"],
            "source_text": r.get("source_text", ""),
            "translation": zh,
            "action": "TRANSLATE",
            "reason": "SAFE_CANDIDATE (候选已确认)",
            "notes": "review_52_candidates",
        })

    # 2) RESOLVED (17)
    for r in csv.DictReader(open(res, encoding="utf-8-sig")):
        if (r.get("_final") or "").strip() != "RESOLVED":
            continue
        zh = (r.get("_final_translation") or "").strip()
        if not zh:
            missing.append((r["translation_id"], "RESOLVED 无译文"))
            continue
        overrides.append({
            "translation_id": r["translation_id"],
            "source_text": r.get("source_text", ""),
            "translation": zh,
            "action": "TRANSLATE",
            "reason": "RESOLVED (明确规则 / Dorothy 成组预定案)",
            "notes": f"dorothy_resolved: {r.get('_resolve_note', '')}",
        })

    # 3) 最终 22 (Dorothy 定稿)
    for tid, zh in final_map.items():
        if not zh:
            missing.append((tid, "22条 无译文"))
            continue
        # 找 source_text: 优先 dorothy_still_needs_review.csv, 其次 dorothy_needs_review.csv
        src = ""
        for f in ("dorothy_still_needs_review.csv", "dorothy_needs_review.csv"):
            p = out / f
            if not p.exists():
                continue
            for r in csv.DictReader(open(p, encoding="utf-8-sig")):
                if r["translation_id"] == tid:
                    src = r.get("source_text", "")
                    break
            if src:
                break
        if not src:
            missing.append((tid, "22条 找不到 source_text"))
            continue
        overrides.append({
            "translation_id": tid,
            "source_text": src,
            "translation": zh,
            "action": "TRANSLATE",
            "reason": "Dorothy 最终定稿",
            "notes": "dorothy_final_22",
        })

    # 去重 (同 tid+source) 并检测冲突
    seen = {}; uniq = []; conflicts = []
    for o in overrides:
        k = (o["translation_id"], o["source_text"])
        if k in seen:
            if seen[k]["translation"] != o["translation"]:
                conflicts.append((k, seen[k]["translation"], o["translation"]))
            continue
        seen[k] = o; uniq.append(o)
    overrides = uniq

    # ==== 严格验证 ====
    print("\n===== 严格验证 ====")
    # 1) source 缺失检查 (source_text 为空)
    no_src = [o["translation_id"] for o in overrides if not (o.get("source_text") or "").strip()]
    print(f"  missing source_text     : {len(no_src)}")
    for t in no_src: print(f"      {t}")
    # 2) 译文缺失检查
    no_zh = [o["translation_id"] for o in overrides if not (o.get("translation") or "").strip()]
    print(f"  missing final_translation: {len(no_zh)}")
    for t in no_zh: print(f"      {t}")
    # 3) 重复冲突检查 (同 tid+src 译文不一致)
    print(f"  duplicate conflict       : {len(conflicts)}")
    for (tid, src), a, b in conflicts:
        print(f"      {tid}: '{a}' vs '{b}'")
    # 4) 22 条 final 精确命中 (tid 存在 + 译文 == final_map 值)
    final_ids = set(final_map)
    over_tids = {o["translation_id"] for o in overrides}
    miss_final = final_ids - over_tids
    zh_mismatch = []
    for o in overrides:
        if o["translation_id"] in final_map and o["translation"] != final_map[o["translation_id"]]:
            zh_mismatch.append((o["translation_id"], final_map[o["translation_id"]], o["translation"]))
    print(f"  final22 缺 tid           : {len(miss_final)}")
    for t in sorted(miss_final): print(f"      {t}")
    print(f"  final22 译文不匹配       : {len(zh_mismatch)}")
    for t, want, got in zh_mismatch: print(f"      {t}: want {want!r} got {got!r}")
    ok = not (no_src or no_zh or conflicts or miss_final or zh_mismatch or missing)
    if missing:
        print(f"\n  另有 {len(missing)} 条因缺译文/缺 source 未写入 (见合并明细):")
        for tid, why in missing: print(f"      {tid}: {why}")
    print("\n  => " + ("全部通过 ✓" if ok else "存在失败项 ✗ (不得覆盖原文件)"))

    # 写合并结果 (不覆盖原文件)
    cols = ["translation_id", "source_text", "translation", "action", "reason", "notes"]
    dest = out / "translation_overrides.merged.csv"
    with open(dest, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for o in overrides:
            w.writerow(o)

    print(f"合并完成:")
    print(f"  SAFE            : {len(safe_rows)}")
    print(f"  RESOLVED        : {len([r for r in csv.DictReader(open(res, encoding='utf-8-sig')) if (r.get('_final') or '').strip()=='RESOLVED'])}")
    print(f"  Dorothy 22 定稿 : {len(final_map)}")
    print(f"  合计 override   : {len(overrides)}")
    print(f"\n[写出] {dest}  (未覆盖原 translation_overrides.csv)")
    print("\n完成 (只读; 未调 LLM / 未改 cache / 未写 package)。")
    print("\n下一步: 人工核对 merged 后 用 `phase2b_translate.py` 重物化 (0 LLM) + `phase2b_qa.py`。")
    return 0

if __name__ == "__main__":
    sys.exit(main())
