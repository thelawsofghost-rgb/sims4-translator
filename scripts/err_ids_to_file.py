#!/usr/bin/env python3
"""只读: 把 QA 报告里 ERROR(或指定 REVIEW 前缀)的 translation_id 写入 txt 文件,
供 phase2b_translate --id-from-file 使用。也顺带统计每行 phrase 数。

用法:
  python scripts/err_ids_to_file.py D:\projects\sims4_trans\output
"""
import sys, csv
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import phase2b_translate as P


def main():
    out_dir = Path(sys.argv[1])
    report = out_dir / "translation_qa_report.csv"
    rows = list(csv.DictReader(open(report, encoding="utf-8-sig")))
    err_ids = []
    for r in rows:
        if r["qa"] == "ERROR":
            err_ids.append(r["translation_id"].strip())

    dst = out_dir / "err_ids.txt"
    dst.write_text("\n".join(err_ids) + ("\n" if err_ids else ""), encoding="utf-8")
    print(f"ERROR tid 数 = {len(err_ids)}")
    print(f"已写入 {dst}")

    # 顺带统计每行 phrase 数 (确认 --force 重翻是否零误伤)
    ctx_map = P.load_contexts(out_dir)
    done = {r["translation_id"]: r for r in
            csv.DictReader(open(out_dir / "translation_done.csv", encoding="utf-8-sig"))}
    multi = []
    for tid in err_ids:
        r = done.get(tid)
        if not r:
            print(f"[缺行] {tid}"); multi.append(tid); continue
        text = P.norm_text(r.get("source_text", ""))
        ctx = ctx_map.get(tid, []); ctx_str = " | ".join(ctx[:3]) if ctx else ""
        segs, sem = P.split_semantic_spans(text)
        gloss, pending = P.glossary_resolve(segs)
        n = sum(1 for p in pending if p["t"].strip())
        if n != 1:
            multi.append(tid)
            print(f"[多phrase] {tid}: {n} | {(r.get('source_text') or '')[:40]!r}")
    print(f"非单 phrase 行数 = {len(multi)} (若为0, --force --id-from-file 零误伤)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
