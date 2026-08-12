#!/usr/bin/env python3
"""Phase 2 前置最终统计 (只读):
  1. locale: 打印全部实际存在的 locale byte 列表 (确认到底几个, 是否有真实 zh 变体)
  2. 9061 MAPPED 去重: unique_package_stbl_keys / unique_source_texts / duplicate_reference_count
  3. 保留完整反向映射: package_path + PosePackInstance + pose entry + STBL key + locale + source_text
"""
import sys, csv
from pathlib import Path
from collections import Counter, defaultdict

out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "D:/projects/sims4_trans/output")
mapping_csv = out_dir / "pose_text_mapping.csv"

rows = []
with open(mapping_csv, encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        rows.append(r)

mapped = [r for r in rows if r.get("status") == "MAPPED"]
print(f"mapped_pose_entries = {len(mapped)}")

# ---- 1) locale byte 实清单 ----
# locale 字段形如 "locale_byte_0x00(byte0x00);locale_byte_0x01(...)..." 
# 每个 MAPPED 行的 locale 字段 = 该 STBL 到的多 locale 变体。取全部去重。
locale_counter = Counter()
for r in mapped:
    loc = r.get("locale") or ""
    for part in loc.split(";"):
        part = part.strip()
        # 形如 locale_byte_0x00(byte0x00) 或 locale_byte_0x00
        import re
        m = re.search(r"byte0x([0-9A-Fa-f]{2})", part)
        if m:
            locale_counter[int(m.group(1), 16)] += 1
print(f"\n=== locale byte 实际出现的集合 (去重后 {len(locale_counter)} 个) ===")
for b in sorted(locale_counter):
    print(f"  byte 0x{b:02X} : 出现在 {locale_counter[b]} 个 MAPPED 行的 locale 列表中")

# ---- 2) 去重统计 ----
# unique_package_stbl_keys: 同一 (package, stbl_key_hash) 组合去重
pkgkey = set()
for r in mapped:
    pkgkey.add((r.get("package_path"), r.get("stbl_key_hash")))
print(f"\nunique_package_stbl_keys = {len(pkgkey)}  (不同 包×(STBL keyHash) 组合)")

# unique_source_texts: 按 stbl 文本本身去重 (翻译模型按此去重后翻)
text_counter = Counter()
for r in mapped:
    text_counter[r.get("stbl_text") or ""] += 1
print(f"unique_source_texts       = {len(text_counter)}  (不同源文本, 翻译去重基数)")
print(f"duplicate_reference_count = {len(mapped) - len(text_counter)}  (重复引用, 无需重复翻译)")

# 文本重复分布概览
print("\n=== 源文本重复 Top 10 (一个文本被多个 key/包引用) ===")
for txt, n in text_counter.most_common(10):
    print(f"  ×{n}  {txt!r}")

# ---- 3) 完整反向映射 CSV ----
rev_cols = ["package_path", "stbl_resource_instance", "stbl_key_hash",
            "source_text", "locale_bytes", "pose_name", "file_pose_name"]
rev_rows = []
for r in mapped:
    rev_rows.append({
        "package_path": r.get("package_path", ""),
        "stbl_resource_instance": "",  # 由 STBL 读取时填充; 这里暂记录 locale 字段里的实例提示
        "stbl_key_hash": r.get("stbl_key_hash", ""),
        "source_text": r.get("stbl_text", ""),
        "locale_bytes": r.get("locale", ""),
        "pose_name": r.get("pose_name", ""),
        "file_pose_name": "",  # 预留: 文件级 pose 名 (pose_display 对应)
    })
rev_out = out_dir / "pose_reverse_mapping.csv"
with open(rev_out, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=rev_cols)
    w.writeheader()
    for row in rev_rows:
        w.writerow(row)
print(f"\n完整反向映射已写出: {rev_out}  ({len(rev_rows)} 行)")
print("\n注: 反向映射的 stbl_resource_instance/locale 需结合 STBL 读取进一步补全 —— 当前 locale 来自映射表。")
