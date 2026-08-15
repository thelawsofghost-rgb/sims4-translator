#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_review_queue.py — 只读 人工/结构复核队列 (weak-signal + low-conf)
========================================================================
输入 (真实 Windows 已产生):
  output/functional_signal_census_448.csv   (强 gate 判定 + 8 signal 计数)
  output/pose_vs_functional_census.csv      (可选: POSE_ONLY_LOW_CONF 锚点)
输出:
  output/review_queue_weak_lowconf.csv      复核队列 (只读清单, 不自动 SKIP)

从 functional_signal_census 中提取「任意 signal 命中但 *不* 命中 STRONG_
OBJECT_FOOTPRINT」的包 -> weak-signal 队列。再从 pose_vs_functional 中提取
POSE_ONLY_LOW_CONF -> low-conf 队列。两类合并为复核队列。

STRONG_OBJECT_FOOTPRINT (与 pose_coverage 一致, VERIFIED type IDs):
  OBJD>0 AND COBJ>0 AND (RSLT>0 OR FTPT>0)   -> 命中(即被 production 排除)
弱信号: 有 signal 但未达强 gate -> 保留, 进入人工/结构复核, 暂不自动 SKIP。

只读: 不改 coverage / 448 / 分类 / gate; 不定义 production 行为; 无黑名单 / 无文件名特判。
"""
import sys, os, csv
from pathlib import Path
from collections import Counter

OBJD, COBJ, RSLT, FTPT = "OBJD_count", "COBJ_count", "RSLT_count", "FTPT_count"
sig_cols = [OBJD, COBJ, "interaction_count", "action_count", "object_xml_count",
            RSLT, FTPT, "animation_component_count"]


def _num(v):
    try:
        return int(v)
    except Exception:
        return 0


def is_strong(r):
    return (_num(r[OBJD]) > 0 and _num(r[COBJ]) > 0
            and (_num(r[RSLT]) > 0 or _num(r[FTPT]) > 0))


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--census", default="output/functional_signal_census_448.csv",
                    help="真实 functional_signal_census (Windows)")
    ap.add_argument("--lowconf", default="output/pose_vs_functional_census.csv",
                    help="真实 pose_vs_functional census (可选, 供 POSE_ONLY_LOW_CONF)")
    ap.add_argument("--out", default="output/review_queue_weak_lowconf.csv")
    a = ap.parse_args()

    if not os.path.isfile(a.census):
        print(f"[ERROR] 缺 functional_signal_census: {a.census} (需先在 Windows 生成)")
        return 2

    rows = list(csv.DictReader(open(a.census, encoding="utf-8-sig")))
    signal_hit = [r for r in rows if _num(r["functional_signal_type_count"]) > 0]

    # 强 gate 命中 (production 会排除) vs weak-signal (保留复核)
    strong = [r for r in signal_hit if is_strong(r)]
    weak = [r for r in signal_hit if not is_strong(r)]

    # LOW_CONF 锚点 (来自真实 pose_vs_functional census)
    lowconf = []
    if os.path.isfile(a.lowconf):
        try:
            lrows = list(csv.DictReader(open(a.lowconf, encoding="utf-8-sig")))
            lowconf = [r for r in lrows if r.get("verdict") == "POSE_ONLY_LOW_CONF"]
        except Exception as e:
            print(f"[warn] lowconf 解析失败: {e}")

    out_cols = ["queue_type", "package_path", "basename", "status",
                "OBJD_count", "COBJ_count", "interaction_count", "action_count",
                "object_xml_count", "RSLT_count", "FTPT_count",
                "animation_component_count", "functional_signal_type_count",
                "functional_signal_resource_count", "signal_signature",
                "reason_note"]
    qrows = []
    for r in weak:
        qrows.append({
            "queue_type": "WEAK_SIGNAL",
            "package_path": r.get("package_path", ""), "basename": r.get("basename", ""),
            "status": r.get("status", ""),
            **{c: r.get(c, "") for c in sig_cols},
            "functional_signal_type_count": r.get("functional_signal_type_count", ""),
            "functional_signal_resource_count": r.get("functional_signal_resource_count", ""),
            "signal_signature": r.get("signal_signature", ""),
            "reason_note": "弱 signal: 未达 STRONG_OBJECT_FOOTPRINT (OBJD+COBJ+(RSLT|FTPT)), 人工/结构复核, 暂不自动SKIP",
        })
    for r in lowconf:
        qrows.append({
            "queue_type": "POSE_ONLY_LOW_CONF",
            "package_path": r.get("package_path", "") or r.get("resolved_path", ""),
            "basename": os.path.basename(r.get("package_path", "") or r.get("resolved_path", "")),
            "status": "POSE_ONLY_LOW_CONF",
            **{c: "" for c in sig_cols},
            "functional_signal_type_count": "", "functional_signal_resource_count": "",
            "signal_signature": "",
            "reason_note": "低置信 pose-only (run2 census), 保守复核, 暂不自动SKIP",
        })

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=out_cols)
        w.writeheader()
        w.writerows(qrows)

    print(f"[WROTE] {a.out} ({len(qrows)} 行复核队列)")
    print(f"\n  signal-hit 包总数: {len(signal_hit)}")
    print(f"  STRONG gate 命中(production将排除): {len(strong)}")
    print(f"  weak-signal(保留复核): {len(weak)}")
    print(f"  POSE_ONLY_LOW_CONF(lowconf): {len(lowconf)}")
    print("\n-- STRONG gate 命中明细 (应=Kritical, 已由 production 排除) --")
    for r in strong:
        print(f"  {r['basename']}: OBJD={_num(r[OBJD])} COBJ={_num(r[COBJ])} "
              f"RSLT={_num(r[RSLT])} FTPT={_num(r[FTPT])}")
    print("\n-- weak-signal 复核队列 --")
    for r in weak:
        print(f"  {r['basename']}: type={r['functional_signal_type_count']} "
              f"sig=[{r['signal_signature']}]")
    print("\n-- POSE_ONLY_LOW_CONF 复核队列 --")
    for r in lowconf:
        print(f"  {os.path.basename(r.get('package_path') or r.get('resolved_path') or '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
