#!/usr/bin/env python3
"""WW 翻译优先级报告 (只读, 不生成 package, 不碰 Mods)。

读 ww_display_series.csv (聚类产物), 生成 ww_translation_priority.csv:
  字段: series_name, entry_count, translation_strategy, sample_displays, priority

优先级规则:
  P1  REUSE_BASE 系列, 按影响 entry 数(簇内成员数)降序  [一次翻 base + stage 后缀]
  P2  STANDALONE 中, 有玩家可见语义的英文词/短语 (非低价值)
  P3  低价值: 纯编号/性别版本 pose label/作者内部命名/URL/creator token

产物:
  ww_translation_priority.csv
  priority_report.txt  (计数 + P3 判定理由明细, 便于人工复核边界)

用法:
  python scripts\\ww_translation_priority.py --series output/ww_display_series/ww_display_series.csv [--out-dir DIR]
"""
import argparse, csv, re, sys
from pathlib import Path
from collections import Counter

# ---- P3 低价值识别 (数据驱动启发式) ----
# 只要命中任一规则即判 P3; base_name 归一化(小写)后判断
_LOW_RULES = [
    ("SEX_VERSION_LABEL",  # 2 F V2, 1 M, 2 F
     re.compile(r'^\d\s+[a-z](\s*[a-z]\d*)?$', re.I)),
    ("NON_SEMANTIC_TAG",   # C1-3 M, C1-3 F, v2
     re.compile(r'^[a-z]\d(-\d)?\s*[a-z]?$', re.I)),
    ("PURE_DIGIT",         # 17, 003
     re.compile(r'^\d{1,4}$')),
    ("RANGE_LABEL",        # C1-3
     re.compile(r'^[a-z]\d(-\d)?$', re.I)),
    ("CREATOR_TOKEN",     # (simmer_creator), (UNI), simmer_creator
     re.compile(r'\(.*creator.*\)|^simmer_creator$|^\(uni\)$', re.I)),
    ("URL",               # katverse.com, xyz.net
     re.compile(r'\.(com|net|org|io|edu|dev|site)/?$', re.I)),
    ("ALL_ALNUM_VERSION", # V2, v3 (短字母+数字)
     re.compile(r'^v\d+$', re.I)),
    ("SHORT_NUM_LABEL",   # 2 F V2 已由 NON_SEMANTIC 覆盖; 兜底: 全是数字+空格
     re.compile(r'^[\d\s]+$')),
]


def _classify_priority(base: str, strategy: str, member_count: int):
    """返回 (priority, reason)。P1=REUSE_BASE; P2/P3 判定 STANDALONE/NEED_REVIEW。"""
    if strategy == "REUSE_BASE":
        return "P1", None
    if strategy == "NEED_REVIEW":
        # 疑似系列但未识别 -> P2 稳妥 (需人工), 或按文本是否低价值分
        pass
    b = (base or "").strip().lower()
    if not b:
        return "P3", "EMPTY"
    for reason, pat in _LOW_RULES:
        if pat.search(b):
            return "P3", reason
    # 无低价值规则命中 -> 视为有语义英文词
    return "P2", None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", required=True, help="ww_display_series.csv 路径")
    ap.add_argument("--out-dir", default="output/ww_translation_priority")
    args = ap.parse_args()

    in_path = Path(args.series)
    if not in_path.exists():
        print(f"ERROR: 不存在 {in_path}", file=sys.stderr)
        sys.exit(2)

    rows = []
    with open(in_path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        cols = rd.fieldnames
        for row in rd:
            rows.append(row)
    print(f"输入 entry 数: {len(rows)}")
    if "series_name" not in cols or "translation_strategy" not in cols:
        print("ERROR: 缺少 series_name/translation_strategy 列", file=sys.stderr)
        sys.exit(2)

    # 聚合到 base: entry_count, strategy, sample_displays
    agg = {}
    order = []
    for r in rows:
        b = r.get("series_name") or ""
        strategy = r.get("translation_strategy") or ""
        d = r.get("animation_raw_display_name") or ""
        if b not in agg:
            agg[b] = {"strategy": strategy, "displays": [], "count": 0}
            order.append(b)
        agg[b]["count"] += 1
        agg[b]["displays"].append(d)

    # 分类 + 排序
    items = []
    for b in order:
        a = agg[b]
        prio, reason = _classify_priority(b, a["strategy"], a["count"])
        # sample_displays: 至多 5 个去重
        samples = []
        seen = set()
        for d in a["displays"]:
            if d not in seen:
                seen.add(d)
                samples.append(d)
            if len(samples) >= 5:
                break
        items.append({
            "series_name": b,
            "entry_count": a["count"],
            "translation_strategy": a["strategy"],
            "sample_displays": "; ".join(samples),
            "priority": prio,
            "_reason": reason,
        })

    # 排序: P1 按 entry_count 降序; P2 按 entry_count 降序; P3 同
    prio_order = {"P1": 0, "P2": 1, "P3": 2}
    items.sort(key=lambda x: (prio_order[x["priority"]], -x["entry_count"], x["series_name"]))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "ww_translation_priority.csv"
    rep_path = out_dir / "priority_report.txt"

    out_cols = ["series_name", "entry_count", "translation_strategy", "sample_displays", "priority"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=out_cols)
        w.writeheader()
        for it in items:
            w.writerow({k: it[k] for k in out_cols})

    # 统计
    c = Counter(it["priority"] for it in items)
    n_p1 = c.get("P1", 0); n_p2 = c.get("P2", 0); n_p3 = c.get("P3", 0)
    entries_p1 = sum(it["entry_count"] for it in items if it["priority"] == "P1")
    entries_p2 = sum(it["entry_count"] for it in items if it["priority"] == "P2")
    entries_p3 = sum(it["entry_count"] for it in items if it["priority"] == "P3")
    # P3 理由分布
    reason_cnt = Counter(it["_reason"] for it in items if it["priority"] == "P3")

    with open(rep_path, "w", encoding="utf-8") as f:
        f.write("WW 翻译优先级报告\n")
        f.write(f"输入: {in_path}\n")
        f.write(f"base 数: {len(items)}, entry 数: {len(rows)}\n\n")
        f.write(f"P1: {n_p1} 簇 / {entries_p1} entry (REUSE_BASE, 按影响降序)\n")
        f.write(f"P2: {n_p2} 条 / {entries_p2} entry (STANDALONE 有语义)\n")
        f.write(f"P3: {n_p3} 条 / {entries_p3} entry (低价值)\n\n")
        f.write("=== P3 低价值判定理由分布 ===\n")
        for reason, n in reason_cnt.most_common():
            f.write(f"  {reason}: {n}\n")
        f.write("\n=== P1 前列 (Top 20) ===\n")
        n = 0
        for it in items:
            if it["priority"] == "P1":
                f.write(f"  [{it['entry_count']}条] {it['series_name']}\n")
                n += 1
                if n >= 20:
                    break
        f.write("\n=== P2 前列 (Top 20) ===\n")
        n = 0
        for it in items:
            if it["priority"] == "P2":
                f.write(f"  [{it['entry_count']}条] {it['series_name']}  (e.g. {it['sample_displays'][:60]})\n")
                n += 1
                if n >= 20:
                    break
        f.write("\n=== P3 判定明细 (前 30) ===\n")
        n = 0
        for it in items:
            if it["priority"] == "P3":
                f.write(f"  [{it['entry_count']}条] {it['series_name']}  ({it['_reason']})\n")
                n += 1
                if n >= 30:
                    f.write(f"  ... 等 {n_p3-30} 条省略\n")
                    break

    print()
    print(f"=== 优先级统计 ===")
    print(f"  P1 REUSE_BASE : {n_p1} 簇 / {entries_p1} entry")
    print(f"  P2 有语义     : {n_p2} 条 / {entries_p2} entry")
    print(f"  P3 低价值     : {n_p3} 条 / {entries_p3} entry")
    print()
    print(f"  P3 理由: {dict(reason_cnt)}")
    print(f"\nCSV   -> {csv_path}")
    print(f"REPORT-> {rep_path}")
    print("ZERO_WRITE_TO_MODS=YES")


if __name__ == "__main__":
    main()
