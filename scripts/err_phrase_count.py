#!/usr/bin/env python3
"""只读: 给定 QA ERROR 清单的 tid 列表, 统计每行需模型 phrase 数,
确认 --force --id 重翻是否只触及失败的 phrase (单 phrase 行则零误伤)。

用法:
  python scripts/err_phrase_count.py D:\projects\sims4_trans\output <tid1,tid2,...>
"""
import sys, csv
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import phase2b_translate as P


def main():
    out_dir = Path(sys.argv[1])
    tid_list = [x.strip() for x in sys.argv[2].split(",") if x.strip()]
    ctx_map = P.load_contexts(out_dir)
    rows = list(csv.DictReader(open(out_dir / "translation_done.csv", encoding="utf-8-sig")))
    by_tid = {r["translation_id"]: r for r in rows}

    total_ph = 0
    for tid in tid_list:
        r = by_tid.get(tid)
        if not r:
            print(f"[缺行] {tid}"); continue
        text = P.norm_text(r.get("source_text", ""))
        ctx = ctx_map.get(tid, []); ctx_str = " | ".join(ctx[:3]) if ctx else ""
        segs, sem = P.split_semantic_spans(text)
        gloss, pending = P.glossary_resolve(segs)
        n = sum(1 for p in pending if p["t"].strip())
        total_ph += n
        print(f"{tid} : {n} phrase | src={(r.get('source_text') or '')[:45]!r}")
    print(f"\n{tid_list.__len__()} 行, 合计 phrase = {total_ph}")


if __name__ == "__main__":
    sys.exit(main())
