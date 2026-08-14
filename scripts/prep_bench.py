#!/usr/bin/env python3
"""一次性: 从真实 todo 取前 N 个 PENDING 行, 生成固定 benchmark 数据集 (out_dir)。

目的: 让 batch_size 4/8/16 三组用完全相同输入 (同一固定 benchmark 数据集,
同一 phrase_count), 可比。不改 correctness 代码, 只准备数据。

用法:
  python scripts/prep_bench.py output_bench --todo output/translations_todo.csv \
        --ctx output/translation_contexts.csv --n 128

输出:
  output_bench/translations_todo.csv
  output_bench/translation_contexts.csv

注意: 取行逻辑与 benchmark_concurrency.py 完全一致:
  - 只取 status PENDING 的行 (status 为空也视为 PENDING, 同 benchmark 脚本)
  - 按 CSV 原始顺序取前 n 行
"""
import sys
import csv
from pathlib import Path


def _arg(name, default):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main():
    out_dir = Path(_arg("--out", "output_bench"))
    todo_path = Path(_arg("--todo", "output/translations_todo.csv"))
    ctx_path = Path(_arg("--ctx", "output/translation_contexts.csv"))
    n = int(_arg("--n", "128"))

    rows = []
    with open(todo_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for r in reader:
            if r.get("status", "").strip().upper() in ("PENDING", ""):
                rows.append(r)

    pick = rows[:n]
    print(f"[输入] 真实 todo PENDING 行 = {len(rows)}, 取前 {n} 固定")

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "translations_todo.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in pick:
            w.writerow(r)

    # context: 只保留 pick 里出现的 translation_id (与 benchmark 的 ctx_map 一致)
    picked_ids = {r.get("translation_id") for r in pick}
    ctx_rows = []
    ctx_fieldnames = None
    if ctx_path.exists():
        with open(ctx_path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            ctx_fieldnames = reader.fieldnames
            for r in reader:
                if r.get("translation_id", "") in picked_ids:
                    ctx_rows.append(r)
    with open(out_dir / "translation_contexts.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ctx_fieldnames or ["translation_id", "context_text"])
        w.writeheader()
        for r in ctx_rows:
            w.writerow(r)

    print(f"[写出] {out_dir / 'translations_todo.csv'}  ({len(pick)} 行)")
    print(f"[写出] {out_dir / 'translation_contexts.csv'}  ({len(ctx_rows)} 行)")
    print(f"[提示] 现可对同一 out_dir 分别跑 --batch-size 4 / 8 / 16 (--force), 三组 phrase_count 将一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
