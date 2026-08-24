#!/usr/bin/env python3
"""WW M2 试点候选清单生成 (只读, 不生成 package, 不碰 Mods)。

读 ww_translation_priority.csv (权威, P1/P2排序) + ww_display_series.csv (补 ordinal/display/stage),
自动产出第一批 M2 测试候选:
  - 1 个 P1 REUSE_BASE 系列 (取影响 entry 数最高者, 含全部 stage 成员)
  - 2~3 个 P2 STANDALONE (取 entry_count 最高者, 有语义英文)

输出 ww_m2_candidates.csv:
  ordinal, series_name, display_name, stage_index, translation_strategy, reason

暂不生成 package。候选需人工确认后才进入 M2 试点脚本。
安全: ZERO_WRITE_TO_MODS=YES。

用法:
  python scripts\\ww_m2_candidates.py --priority <priority.csv> --series <series.csv> \
      [--p1 1] [--p2 3] [--out-dir DIR]
"""
import argparse, csv, sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--priority", required=True, help="ww_translation_priority.csv")
    ap.add_argument("--series", required=True, help="ww_display_series.csv")
    ap.add_argument("--p1", type=int, default=1, help="选几个 P1 系列 (默认 1)")
    ap.add_argument("--p2", type=int, default=3, help="选几条 P2 standalone (默认 3)")
    ap.add_argument("--out-dir", default="output/ww_m2_candidates")
    args = ap.parse_args()

    prio_path = Path(args.priority)
    ser_path = Path(args.series)
    if not prio_path.exists():
        print(f"ERROR: priority 不存在 {prio_path}", file=sys.stderr); sys.exit(2)
    if not ser_path.exists():
        print(f"ERROR: series 不存在 {ser_path}", file=sys.stderr); sys.exit(2)

    # 1) 读 priority (权威)
    prio = []
    with open(prio_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            prio.append(r)
    print(f"priority base 数: {len(prio)}")

    # 2) 读 series, 按 series_name 建索引 (每 base 的所有 entry)
    by_base = {}
    with open(ser_path, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        if "series_name" not in rd.fieldnames:
            print("ERROR: series.csv 缺 series_name 列", file=sys.stderr); sys.exit(2)
        for r in rd:
            b = r.get("series_name") or ""
            by_base.setdefault(b, []).append(r)
    print(f"series base 数: {len(by_base)}")

    # 3) 按 priority 序号过滤出 P1 与 P2
    p1_list = [r for r in prio if r.get("priority") == "P1"]
    p2_list = [r for r in prio if r.get("priority") == "P2"]
    # priority 已按 entry_count 降序; 但稳妥起见再按 entry_count 排一次
    p1_list.sort(key=lambda r: -int(r.get("entry_count") or 0))
    p2_list.sort(key=lambda r: -int(r.get("entry_count") or 0))
    print(f"P1 系列: {len(p1_list)}  P2 条目: {len(p2_list)}")

    selected = []
    # --- P1: 取前 args.p1 个系列, 每系列全 stage 成员 ---
    for r in p1_list[:args.p1]:
        b = r["series_name"]
        mems = by_base.get(b)
        if not mems:
            print(f"  WARN: priority 有 {b} 但 series.csv 无该 series_name, 跳过", file=sys.stderr)
            continue
        mems.sort(key=lambda m: (m.get("stage_index") or "") or m.get("ordinal") or "")
        for m in mems:
            selected.append({
                "ordinal": m.get("ordinal", ""),
                "series_name": b,
                "display_name": m.get("animation_raw_display_name", ""),
                "stage_index": m.get("stage_index", ""),
                "translation_strategy": "REUSE_BASE",
                "reason": f"P1 最高影响系列 ({int(r.get('entry_count') or 0)} entry) 全 stage 成员",
            })

    # --- P2: 取前 args.p2 条, 每 base 取 ordinal 最小的一条 ---
    p2_added = 0
    for r in p2_list:
        if p2_added >= args.p2:
            break
        b = r["series_name"]
        mems = by_base.get(b)
        if not mems:
            print(f"  WARN: priority 有 {b} 但 series.csv 无该 series_name, 跳过", file=sys.stderr)
            continue
        # 该 base 下选 ordinal 最小的一条
        m = min(mems, key=lambda x: (int(x.get("ordinal") or 0) if str(x.get("ordinal","")).isdigit() else 10**9))
        selected.append({
            "ordinal": m.get("ordinal", ""),
            "series_name": b,
            "display_name": m.get("animation_raw_display_name", ""),
            "stage_index": m.get("stage_index", ""),
            "translation_strategy": "STANDALONE",
            "reason": f"P2 独立英文动画 ({int(r.get('entry_count') or 0)} entry)",
        })
        p2_added += 1

    if not selected:
        print("ERROR: 未选出任何候选 (检查 priority/series 匹配)", file=sys.stderr); sys.exit(2)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "ww_m2_candidates.csv"

    cols = ["ordinal", "series_name", "display_name", "stage_index", "translation_strategy", "reason"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for s in selected:
            w.writerow(s)

    # 汇总
    n_p1 = sum(1 for s in selected if s["translation_strategy"] == "REUSE_BASE")
    n_p2 = sum(1 for s in selected if s["translation_strategy"] == "STANDALONE")
    print(f"\n=== M2 候选 ===")
    print(f"  P1 (REUSE_BASE)  : {n_p1} entry")
    print(f"  P2 (STANDALONE)  : {n_p2} entry")
    print(f"  合计              : {len(selected)} entry")
    print(f"\nCSV -> {csv_path}")
    print("ZERO_WRITE_TO_MODS=YES")
    print("(暂不生成 package; 候选需人工确认)")


if __name__ == "__main__":
    main()
