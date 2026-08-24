#!/usr/bin/env python3
"""WW display 名 × STBL 翻译表 纯文本匹配分析 (只读, 不生成 package, 不碰 Mods)。

把 ww_animation_display_census.csv 的 animation_raw_display_name 原值,
与现有 STBL 翻译表 (title_manual_translate / desc_* / translation_overrides / 等)
按【纯文本值】匹配 (不按 translation_id join)。

输出分类:
  A. EXACT_REUSE     — 有一个明确的非空中文译文, 可直接复用
  B. NEED_TRANSLATION— 完全无对应译文 (或译文为 KEEP 保持原文/空), 需新翻译
  C. AMBIGUOUS       — 同一原文文本命中多个不同译文, 需人工判断

产物:
  ww_animation_translation.csv   (census 全字段 + translation_status + chinese_translation)
  ww_animation_match_report.txt  (A/B/C 计数 + 命中明细)

用法:
  python scripts\\ww_animation_text_match.py --census output/ww_animation_display_census/ww_animation_display_census.csv [--out-dir DIR] [--stbl DIR]
"""
import argparse, csv, json, sys
from pathlib import Path

# STBL 翻译表: (文件名, 列: source_text, translation)
STBL_SOURCES = [
    ("title_manual_translate.c26.csv", "source_text", "translation"),
    ("title_terminal_keep.c26.csv", "source_text", "translation"),
    ("desc_content_corrections.c26.csv", "source_text", "translation"),
    ("desc_terminal_keep.c26.csv", "source_text", "translation"),
    ("translation_overrides.c26_pose_keep.csv", "source_text", "translation"),
    ("desc_dorothy_frozen.c26.csv", "source_text", "final_translation"),
]
KEEP_MARKERS = ("KEEP", "keep")

CENSUS_COLUMNS = [
    "ordinal", "source_instance", "animation_raw_display_name",
    "animation_stage_name", "author", "category", "tags", "location",
    "actor_count", "clip_name",
]


def load_stbl_text_map(stbl_dir: Path):
    """返回 {normalized_text: {"action":.., "translations":{translation: [files]}, "keep":bool}}"""
    m = {}
    for fname, scol, tcol in STBL_SOURCES:
        f = stbl_dir / fname
        if not f.exists():
            print(f"  (跳过缺失表 {fname})", file=sys.stderr)
            continue
        with open(f, newline="", encoding="utf-8") as fh:
            rd = csv.DictReader(fh)
            for row in rd:
                src = (row.get(scol) or "").strip()
                if not src:
                    continue
                nk = src  # 纯文本值, 不做 lower (区分大小写, 避免误合并)
                rec = m.setdefault(nk, {"action": set(), "translations": {}, "keep": False})
                act = (row.get("action") or "")
                rec["action"].add(act.strip())
                if any(k in act for k in KEEP_MARKERS):
                    rec["keep"] = True
                tr = (row.get(tcol) or "").strip()
                if tr:
                    rec["translations"].setdefault(tr, []).append(fname)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--census", required=True, help="ww_animation_display_census.csv 路径")
    ap.add_argument("--stbl", default="configs", help="STBL 翻译表所在目录")
    ap.add_argument("--out-dir", default="output/ww_animation_text_match")
    args = ap.parse_args()

    census_path = Path(args.census)
    if not census_path.exists():
        print(f"ERROR: census 不存在 {census_path}", file=sys.stderr)
        sys.exit(2)
    stbl_dir = Path(args.stbl)

    print("加载 STBL 翻译文本映射…")
    text_map = load_stbl_text_map(stbl_dir)
    print(f"STBL 唯一 source_text 数: {len(text_map)}")
    # 统计可用译文总数
    total_tr = sum(len(r["translations"]) for r in text_map.values())
    print(f"其中带非空译文的 source_text 数: {sum(bool(r['translations']) for r in text_map.values())}, 译文条目总数: {total_tr}")

    rows = []
    with open(census_path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        for row in rd:
            display = (row.get("animation_raw_display_name") or "").strip()
            if not display:
                status = "NO_DISPLAY"
                zh = ""
                match_info = None
            else:
                rec = text_map.get(display)
                if rec is None:
                    status = "B_NEED_TRANSLATION"
                    zh = ""
                    match_info = None
                else:
                    trans = rec["translations"]
                    if not trans:
                        # 有记录但无译文 (KEEP 或空) -> 需新翻译
                        status = "B_NEED_TRANSLATION"
                        zh = ""
                        match_info = {"action": sorted(rec["action"]), "keep": rec["keep"]}
                    elif len(trans) == 1:
                        tr = next(iter(trans))
                        status = "A_EXACT_REUSE"
                        zh = tr
                        match_info = {"translations": {tr: trans[tr]}, "action": sorted(rec["action"])}
                    else:
                        status = "C_AMBIGUOUS"
                        zh = ""
                        match_info = {"translations": trans, "action": sorted(rec["action"])}
            out_row = {c: row.get(c, "") for c in CENSUS_COLUMNS}
            out_row["translation_status"] = status
            out_row["chinese_translation"] = zh
            rows.append((display, status, zh, match_info, out_row))

    # 统计
    counts = {}
    for _d, st, _z, _mi, _r in rows:
        counts[st] = counts.get(st, 0) + 1
    n_A = counts.get("A_EXACT_REUSE", 0)
    n_B = counts.get("B_NEED_TRANSLATION", 0)
    n_C = counts.get("C_AMBIGUOUS", 0)
    n_NO = counts.get("NO_DISPLAY", 0)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "ww_animation_translation.csv"
    rep_path = out_dir / "ww_animation_match_report.txt"

    # 写 CSV
    cols = CENSUS_COLUMNS + ["translation_status", "chinese_translation"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for _d, _st, _z, _mi, out_row in rows:
            w.writerow(out_row)

    # 写 report
    with open(rep_path, "w", encoding="utf-8") as f:
        f.write("WW display 名 × STBL 纯文本匹配报告\n")
        f.write(f"census: {census_path}\n")
        f.write(f"STBL 表: {stbl_dir}\n")
        f.write(f"唯一 entry: {len(rows)}\n\n")
        f.write(f"计数:\n  A_EXACT_REUSE: {n_A}\n  B_NEED_TRANSLATION: {n_B}\n  C_AMBIGUOUS: {n_C}\n  NO_DISPLAY: {n_NO}\n\n")
        f.write("=== A. EXACT_REUSE 明细 (display -> 中文) ===\n")
        for d, st, z, mi, _r in rows:
            if st == "A_EXACT_REUSE":
                f.write(f"  {d} -> {z}\n")
        f.write("\n=== C. AMBIGUOUS 明细 (display -> 多候选译文) ===\n")
        for d, st, z, mi, _r in rows:
            if st == "C_AMBIGUOUS" and mi:
                trs = mi["translations"]
                f.write(f"  {d}:\n")
                for tr, files in trs.items():
                    f.write(f"      [{tr}] from {','.join(files)}\n")
        f.write("\n=== B. NEED_TRANSLATION (前 100 条) ===\n")
        b_done = 0
        for d, st, z, mi, _r in rows:
            if st == "B_NEED_TRANSLATION":
                f.write(f"  {d}\n")
                b_done += 1
                if b_done >= 100:
                    f.write(f"  ... 等 {n_B - 100} 条省略\n")
                    break

    print()
    print(f"=== 分类结果 ===")
    print(f"  A_EXACT_REUSE     = {n_A}")
    print(f"  B_NEED_TRANSLATION= {n_B}")
    print(f"  C_AMBIGUOUS       = {n_C}")
    print(f"  NO_DISPLAY        = {n_NO}")
    print(f"\nCSV   -> {csv_path}")
    print(f"REPORT-> {rep_path}")
    print("ZERO_WRITE_TO_MODS=YES")


if __name__ == "__main__":
    main()
