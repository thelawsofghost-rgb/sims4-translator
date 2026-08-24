#!/usr/bin/env python3
"""WW display series 聚类 (只读, 不生成 package, 不碰 Mods)。

对 ww_animation_translation.csv 的 animation_raw_display_name 做 series 聚类:
  识别数字后缀系列 (xxx 1/2/3, xxx TWO SIMS 1/2, xxx (1), xxx I/II ...),
  提取 base_name + stage_index, 标注翻译策略, 输出 grouped CSV。

新增字段:
  series_name               (base_name; 系列内所有 entry 共享, 翻译单位)
  stage_index               (该 entry 在系列内的序号; 无 / 无法识别 -> 空)
  translation_strategy      (REUSE_BASE 系列可一次翻译 base + 每 stage 后缀;
                             STANDALONE 单条单独翻译; NEED_REVIEW 无法确定需要人工)

安全: ZERO_WRITE_TO_MODS=YES。仅读 census/translation CSV。
"""
import argparse, csv, re, sys
from pathlib import Path

# ---- 后缀 stage 模式 (倒序匹配, 取最符合者) ----
# 返回 (base, index, rule) 或 None (不匹配为 stage 后缀 => 整个作为 base, STANDALONE)
# 注意: base 保留原大小写与 spacing; 只剥离被判定为 stage 的尾部标记

# 顺序 = 优先级 (先匹配更特定的)
_SUFFIX_RULES = [
    # (正则, index, 说明)   -- index 组可为函数
    (r'^(?P<base>.*?)\s+\((?P<idx>\d+)\)$',      "paren_int",  "xxx (1)"),
    (r'^(?P<base>.*?)\s+\((?P<idx>[ivx]+)\)$',   "paren_roman","xxx (iii)"),
    (r'^(?P<base>.*?)\s+\((?P<idx>one|two|three|four|five|six|seven|eight|nine|ten)\)$', "paren_word","xxx (two)"),
    # 数字后缀, 允许跟非字母变体标记 (性别/版本): "xxx 2 F V2", "xxx 1 M"
    (r'^(?P<base>.*?)\s+(?P<idx>\d{1,3})\s*$',   "num_tail",   "xxx 2"),
    (r'^(?P<base>.*?)\s+(?P<idx>\d{1,3})\s*$',   "num_tail_nosp", "xxx2 (保守不分,见下)"),
    (r'^(?P<base>.*?)\s+(?P<idx>[IVX]{1,4})$',   "roman_tail", "xxx II"),
    (r'^(?P<base>.*?)\s+(?P<idx>one|two|three|four|five|six|seven|eight|nine|ten)$', "word_tail","xxx Two"),
]
# 数字+带标签: "TWO SIMS 1 / 2", "1 M", "2 F V2"
# 策略: 剥离尾部 "空格+数字" (最后一个整数 token), 其余并入 base
# 因此 "TWO SIMS 2" -> base="TWO SIMS", idx=2
# "2 F V2" -> 尾部 token "V2" 非纯数字, 不剥; 前面 "2 F" 也不是纯数字尾 -> base=整串 STANDALONE

_WORD_IDX = {"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,
             "eight":8,"nine":9,"ten":10}
_ROMAN = {"i":1,"ii":2,"iii":3,"iv":4,"v":5,"vi":6,"vii":7,"viii":8,"ix":9,"x":10}


def _split_stage(display: str):
    """返回 (base_without_stage, stage_index_str, matched_rule, stage_suffix)。
    stage 无法识别 -> (display, '', None, '')。保守: 只剥离确定是 stage 的尾部。"""
    if not display:
        return display, '', None, ''
    t = display.strip()
    # 1) 括号整数
    m = re.match(r'^(?P<base>.*?)\s+\((?P<idx>\d+)\)$', t)
    if m:
        return m.group("base").strip(), str(int(m.group("idx"))), "paren_int", f"({m.group('idx')})"
    # 2) 括号罗马
    m = re.match(r'^(?P<base>.*?)\s+\((?P<idx>[ivx]+)\)$', t, re.I)
    if m:
        idx = m.group("idx").lower()
        return m.group("base").strip(), str(_ROMAN.get(idx, idx.upper())), "paren_roman", f"({m.group('idx')})"
    # 3) 括号单词数词
    m = re.match(r'^(?P<base>.*?)\s+\((?P<idx>[A-Za-z]+)\)$', t)
    if m and m.group("idx").lower() in _WORD_IDX:
        return m.group("base").strip(), str(_WORD_IDX[m.group("idx").lower()]), "paren_word", f"({m.group('idx')})"
    # 4) 尾部纯数字 (空格分隔)  -> "xxx 2"
    m = re.match(r'^(?P<base>.*?)\s+(?P<idx>\d{1,3})$', t)
    if m:
        return m.group("base").strip(), str(int(m.group("idx"))), "num_tail", m.group("idx")
    # 5) 尾部罗马大写  -> "xxx II"
    m = re.match(r'^(?P<base>.*?)\s+(?P<idx>[IVX]{1,4})$', t)
    if m:
        return m.group("base").strip(), str(_ROMAN.get(m.group("idx").lower(), m.group("idx"))), "roman_tail", m.group("idx")
    # 6) 尾部单词数词 (非括号)  -> "xxx Two"
    m = re.match(r'^(?P<base>.*?)\s+(?P<idx>[A-Za-z]+)$', t)
    if m and m.group("idx").lower() in _WORD_IDX:
        return m.group("base").strip(), str(_WORD_IDX[m.group("idx").lower()]), "word_tail", m.group("idx")
    # 7) "TWO SIMS 1/2" 已由 (4) num_tail 覆盖 (base="TWO SIMS")
    # 其余: 无法识别 stage
    return t, '', None, ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--translation", required=True, help="ww_animation_translation.csv 路径")
    ap.add_argument("--out-dir", default="output/ww_display_series")
    args = ap.parse_args()

    in_path = Path(args.translation)
    if not in_path.exists():
        print(f"ERROR: 不存在 {in_path}", file=sys.stderr)
        sys.exit(2)

    rows = []
    with open(in_path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        for row in rd:
            rows.append(row)
    print(f"输入 entry 数: {len(rows)}")

    # 1) 逐条拆分 stage
    parsed = []
    for row in rows:
        display = (row.get("animation_raw_display_name") or "").strip()
        base, idx, rule, suffix = _split_stage(display)
        parsed.append({"row": row, "display": display, "base": base,
                       "idx": idx, "rule": rule, "suffix": suffix})

    # 2) 按 base 聚类
    clusters = {}
    order = []
    for p in parsed:
        b = p["base"] or ""
        if b not in clusters:
            clusters[b] = {"members": [], "bases": []}
            order.append(b)
        clusters[b]["members"].append(p)
        if p["display"] not in clusters[b]["bases"]:
            clusters[b]["bases"].append(p["display"])

    # 3) 判定策略
    out_rows = []
    cluster_stats = []
    for b in order:
        c = clusters[b]
        mems = c["members"]
        distinct_display = set(p["display"] for p in mems)
        # 有明确 stage index 的成员
        idxed = [p for p in mems if p["idx"] != ""]
        idx_set = set(p["idx"] for p in idxed)
        all_same_base = len(distinct_display) >= 2 and all(p["base"] == b for p in mems)
        # REUSE_BASE: 至少2条不同display, 且多数有 stage index, 且 stage 互异
        if (all_same_base and len(distinct_display) >= 2
                and len(idx_set) >= 2 and len(idxed) >= 2):
            strategy = "REUSE_BASE"
        elif (all_same_base and len(distinct_display) >= 2):
            # 多条共享 base 但 stage 无法识别 -> 可能 "xxx 2 F V2" 系 -> NEED_REVIEW
            strategy = "NEED_REVIEW"
        else:
            strategy = "STANDALONE"
        c["strategy"] = strategy
        is_series = strategy in ("REUSE_BASE", "NEED_REVIEW")
        for p in mems:
            out_rows.append({**p["row"],
                             "series_name": b,
                             "stage_index": p["idx"],
                             "translation_strategy": strategy})
        cluster_stats.append((b, len(mems), len(distinct_display), sorted(idx_set), strategy))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "ww_display_series.csv"
    rep_path = out_dir / "series_report.txt"

    cols = list(rows[0].keys()) + ["series_name", "stage_index", "translation_strategy"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in out_rows:
            w.writerow(r)

    # 统计
    from collections import Counter
    strat_cnt = Counter(r["translation_strategy"] for r in out_rows)
    n_reuse = strat_cnt.get("REUSE_BASE", 0)
    n_stand = strat_cnt.get("STANDALONE", 0)
    n_review = strat_cnt.get("NEED_REVIEW", 0)
    n_series_clusters = sum(1 for b,c in clusters.items() if c.get("strategy")=="REUSE_BASE")
    n_review_clusters = sum(1 for b,c in clusters.items() if c.get("strategy")=="NEED_REVIEW")
    n_uniq_base = len(clusters)

    with open(rep_path, "w", encoding="utf-8") as f:
        f.write("WW display series 聚类报告\n")
        f.write(f"输入: {in_path}\n")
        f.write(f"entry 数: {len(rows)}\n")
        f.write(f"唯一 base_name 数: {n_uniq_base}\n\n")
        f.write(f"策略条目数:\n  REUSE_BASE: {n_reuse}\n  STANDALONE: {n_stand}\n  NEED_REVIEW: {n_review}\n")
        f.write(f"系列簇数(REUSE_BASE): {n_series_clusters}\n")
        f.write(f"待审簇数(NEED_REVIEW): {n_review_clusters}\n\n")
        f.write("=== REUSE_BASE 系列明细 (base | entries | stages | strategy) ===\n")
        for b, nmem, ndis, idxs, strat in cluster_stats:
            if strat == "REUSE_BASE":
                f.write(f"  {b} | {nmem}条 | stages={idxs} | {strat}\n")
        f.write("\n=== NEED_REVIEW 系列 (疑似系列但 stage 未识别) ===\n")
        for b, nmem, ndis, idxs, strat in cluster_stats:
            if strat == "NEED_REVIEW":
                f.write(f"  {b} | {nmem}条 | displays={sorted(clusters[b]['bases'])} | {strat}\n")

    print()
    print(f"=== 聚类统计 ===")
    print(f"  唯一 base_name : {n_uniq_base}")
    print(f"  REUSE_BASE     : {n_reuse} 条 / {n_series_clusters} 簇")
    print(f"  STANDALONE     : {n_stand} 条")
    print(f"  NEED_REVIEW    : {n_review} 条 / {n_review_clusters} 簇")
    print(f"\nCSV   -> {csv_path}")
    print(f"REPORT-> {rep_path}")
    print("ZERO_WRITE_TO_MODS=YES")


if __name__ == "__main__":
    main()
