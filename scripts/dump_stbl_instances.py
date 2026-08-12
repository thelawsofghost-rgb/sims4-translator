#!/usr/bin/env python3
"""临时: 导出 659 包内所有 STBL 的完整 64-bit instance_id + 分组, 为确定正确 locale 编码方式取证。"""
import sys, csv
from pathlib import Path
from collections import Counter, defaultdict
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse
from backend import get_backend

veri = Path(sys.argv[1] if len(sys.argv) > 1 else "D:/projects/sims4_trans/output/pose_verification.csv")
out = Path(sys.argv[2] if len(sys.argv) > 2 else "D:/projects/sims4_trans/output/stbl_instances.csv")

paths = []
with open(veri, encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        if r.get("verification_status") == "POSE_VERIFIED":
            p = (r.get("package_path") or "").strip()
            if p: paths.append(p)

rows = []
high32 = Counter()
low32 = Counter()
for i, p in enumerate(paths, 1):
    try:
        idx, err = safe_parse(p)
        if err or not idx:
            continue
        for e in idx.entries:
            if e.type_id == 0x220557DA:
                inst = e.instance_id
                rows.append({"package_path": p, "instance_id": f"0x{inst:016X}",
                             "high32": f"0x{(inst >> 32) & 0xFFFFFFFF:08X}",
                             "low32": f"0x{inst & 0xFFFFFFFF:08X}"})
                high32[(inst >> 32) & 0xFFFFFFFF] += 1
                low32[inst & 0xFFFFFFFF] += 1
    except Exception:
        pass
    if i % 200 == 0:
        print(f"  {i}/{len(paths)} ...")

with open(out, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["package_path", "instance_id", "high32", "low32"])
    w.writeheader()
    for r in rows:
        w.writerow(r)

print(f"\nSTBL 总数: {len(rows)}")
print("\n--- 高 32 位 (locale 指示) 分布 Top 15 ---")
for h, n in high32.most_common(15):
    print(f"  0x{h:08X} : {n}")
print("\n--- 低 32 位 (字符串块定位) Top 10 ---")
for h, n in low32.most_common(10):
    print(f"  0x{h:08X} : {n}")
print(f"\n已写出: {out}")
