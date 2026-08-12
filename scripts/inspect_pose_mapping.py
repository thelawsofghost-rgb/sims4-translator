#!/usr/bin/env python3
"""临时抽查: 验证 pose_text_mapping.csv 的真实性 (不依赖猜测)。"""
import sys, csv
from collections import Counter
p = sys.argv[1] if len(sys.argv) > 1 else "D:/projects/sims4_trans/output/pose_text_mapping.csv"
rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
print(f"总行: {len(rows)}")
print("status:", dict(Counter(r["status"] for r in rows)))
print("intent:", dict(Counter(r["text_intent"] for r in rows)))

print("\n--- 前 12 条 MAPPED (文本应真实可读) ---")
n = 0
for r in rows:
    if r["status"] == "MAPPED":
        print(f"  [{r['stbl_key_hash']}] {r['stbl_text']!r} | intent={r['text_intent']} | locale={r['locale']} | pkg={r['package_path'].split(chr(92))[-1][:55]}")
        n += 1
        if n >= 12:
            break

print("\n--- 前 12 条 REF_FAIL ---")
n = 0
for r in rows:
    if r["status"] == "REF_FAIL":
        print(f"  disp={r['display_ref']!r} | key={r['stbl_key_hash']} | {r['reason']} | pkg={r['package_path'].split(chr(92))[-1][:45]}")
        n += 1
        if n >= 12:
            break

# 抽样分布: 每个包 mapped 数
from collections import defaultdict
per = defaultdict(int)
for r in rows:
    if r["status"] == "MAPPED":
        per[r["package_path"]] += 1
print(f"\n== 每包 mapped 数分布 ==")
vals = sorted(per.values())
print("  包数:", len(per), "| mapped 总数:", sum(vals))
print("  min:", vals[0], "| max:", vals[-1])
print("  中位:", vals[len(vals)//2])
