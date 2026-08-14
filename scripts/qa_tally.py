#!/usr/bin/env python3
"""只读: 对 translation_qa_report.csv 按 qa_reason 分组统计, 并抽样代表性行。

不改任何 correctness 代码, 只读。用途: 快速判断 ERROR/REVIEW 是
真问题还是 QA 规则误报 (英文残留/受保护token大小写/纯技术行等常误报)。

用法:
  python scripts/qa_tally.py D:\projects\sims4_trans\output\translation_qa_report.csv
"""
import csv
import sys
import collections
from pathlib import Path


def main():
    report = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/translation_qa_report.csv")
    rows = list(csv.DictReader(open(report, encoding="utf-8-sig")))
    groups = collections.defaultdict(lambda: collections.Counter())
    sample = collections.defaultdict(list)
    for r in rows:
        qa = r["qa"]
        reason = r["qa_reason"] or ""
        key = (reason.split(":")[0].split(";")[0].strip()) or "(空)"
        groups[qa][key] += 1
        if len(sample[(qa, key)]) < 3:
            sample[(qa, key)].append(
                (r["translation_id"], (r["source_text"] or "")[:40],
                 (r["translation"] or "")[:50], reason[:60]))

    for qa in ("ERROR", "REVIEW", "PASS"):
        if qa not in groups:
            continue
        print(f"\n===== {qa} (共 {sum(groups[qa].values())}) =====")
        for k, c in groups[qa].most_common():
            print(f"  {c:4d}  {k!r}")
        print("  --- 抽样 (最多前8类各3条) ---")
        for k, c in groups[qa].most_common(8):
            print(f"  [{k!r}]")
            for tid, src, zh, reason in sample[(qa, k)]:
                print(f"     {tid:14s} src={src!r}")
                print(f"               zh={zh!r} :: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
