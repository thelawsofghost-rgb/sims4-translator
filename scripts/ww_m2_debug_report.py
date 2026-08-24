#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M2 真机排查报告: 让我(Dorothy)知道每个被本地化动作在游戏里"去哪找"。

背景: M2 批量试点生成 11 个新 WW_ANIM_XML instance 后, 在游戏里需要知道
  每个动作的描述性信息 (几个人/哪个分类/标签/作者/clip 名), 才能定位并确认
  中文名是否正确注册。本脚本只读三份 CSV, 产出人可读 + 机器可查两份报告。

输入 (全部只读):
  - output/ww_m2_candidates/ww_m2_candidates.csv   (ordinal, series_name, display_name, stage_index, translation_strategy, reason)
  - output/ww_animation_display_census/ww_animation_display_census.csv
      (ordinal, source_instance, animation_raw_display_name, animation_stage_name,
       author, category, tags, location, actor_count, clip_name)
  - configs/ww_m2_translations.csv                 (ordinal, chinese_translation)

输出:
  - output/ww_m2_debug/m2_debug_report.csv
  - output/ww_m2_debug/m2_debug_report.txt

每个候选/译文行输出:
  ordinal, old_display_name, chinese_display_name, actor_count, category, tags,
  location, author, clip_name, stage_index, translation_strategy

fail-closed: 关键输入缺失/列不匹配时中止; 缺失的辅助字段标注 (—) 而非静默。
ZERO_WRITE_TO_MODS=YES (只读 package 派生 CSV, 不改 package, 不碰 writer)
"""
import argparse
import csv
import sys
from pathlib import Path

DEFAULT_CAND = "output/ww_m2_candidates/ww_m2_candidates.csv"
DEFAULT_CEN = "output/ww_animation_display_census/ww_animation_display_census.csv"
DEFAULT_TR = "configs/ww_m2_translations.csv"
DEFAULT_OUT = "output/ww_m2_debug"


def _read_csv(path, *, columns):
    p = Path(path)
    if not p.is_file():
        print(f"ERROR: 输入不存在 {p}", file=sys.stderr)
        return None
    rows = []
    with open(p, newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        missing = [c for c in columns if c not in (r.fieldnames or [])]
        if missing:
            print(f"ERROR: {p.name} 缺少列 {missing} (实际: {list(r.fieldnames or [])})", file=sys.stderr)
            return None
        for row in r:
            rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default=DEFAULT_CAND)
    ap.add_argument("--census", default=DEFAULT_CEN)
    ap.add_argument("--translations", default=DEFAULT_TR)
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    a = ap.parse_args()

    cands = _read_csv(a.candidates, columns=["ordinal", "display_name", "stage_index", "translation_strategy"])
    cens = _read_csv(a.census, columns=[
        "ordinal", "animation_raw_display_name", "author", "category", "tags",
        "location", "actor_count", "clip_name", "animation_stage_name"])
    trans = _read_csv(a.translations, columns=["ordinal", "chinese_translation"])
    if cands is None or cens is None or trans is None:
        return 2

    cen_by_ord = {str(r["ordinal"]).strip(): r for r in cens}
    tr_by_ord = {str(r["ordinal"]).strip(): r["chinese_translation"].strip() for r in trans}
    miss_tr = [str(c["ordinal"]).strip() for c in cands if str(c["ordinal"]).strip() not in tr_by_ord]
    if miss_tr:
        print(f"ERROR: 候选缺少译文 ordinal: {miss_tr}", file=sys.stderr)
        return 2

    rows = []
    for c in cands:
        ord_s = str(c["ordinal"]).strip()
        cen = cen_by_ord.get(ord_s, {})
        old = (c.get("display_name") or "").strip() or cen.get("animation_raw_display_name", "").strip()
        rows.append({
            "ordinal": ord_s,
            "old_display_name": old,
            "chinese_display_name": tr_by_ord[ord_s],
            "actor_count": (cen.get("actor_count") or "—").strip(),
            "category": (cen.get("category") or "—").strip(),
            "tags": (cen.get("tags") or "—").strip(),
            "location": (cen.get("location") or "—").strip(),
            "author": (cen.get("author") or "—").strip(),
            "clip_name": (cen.get("clip_name") or "—").strip(),
            "stage_index": (c.get("stage_index") or "—").strip(),
            "translation_strategy": (c.get("translation_strategy") or "—").strip(),
        })

    cols = ["ordinal", "old_display_name", "chinese_display_name", "actor_count",
            "category", "tags", "location", "author", "clip_name",
            "stage_index", "translation_strategy"]

    # ---- 写 CSV ----
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "m2_debug_report.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    # ---- 写 TXT (人可读) ----
    txt_path = out_dir / "m2_debug_report.txt"
    lines = []
    lines.append("M2 真机排查报告  (WW 本地化试点 — 去哪找每个动作)")
    lines.append("=" * 78)
    actor_label = {"1": "1人", "2": "2人", "3": "3人", "4": "4人", "5": "5人+", "—": "未知"}
    for r in rows:
        lines.append("")
        lines.append(f"[ordinal {r['ordinal']}]  {r['old_display_name']}")
        lines.append(f"    中文名     : {r['chinese_display_name']}")
        lines.append(f"    动作人数   : {actor_label.get(str(r['actor_count']), r['actor_count'] + '人')}")
        lines.append(f"    WW 分类    : {r['category']}")
        lines.append(f"    标签       : {r['tags']}")
        lines.append(f"    位置       : {r['location']}")
        lines.append(f"    作者       : {r['author']}")
        lines.append(f"    clip 名    : {r['clip_name']}")
        lines.append(f"    阶段/策略  : stage={r['stage_index']}  ({r['translation_strategy']})")
    lines.append("")
    lines.append("=" * 78)
    lines.append(f"条目数: {len(rows)}  先生成 CSV 供机器排查, 此 TXT 供人工快速定位。")
    lines.append("ZERO_WRITE_TO_MODS=YES (只读派生, 不改 package, 不碰 writer)")
    txt_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"OUT_CSV={csv_path}")
    print(f"OUT_TXT={txt_path}")
    print(f"REPORT_ROWS={len(rows)}")
    print("M2_DEBUG_REPORT=OK")
    print("ZERO_WRITE_TO_MODS=YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
