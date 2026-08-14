#!/usr/bin/env python3
"""只读: 导出 QA 报告里 ERROR 与指定 REVIEW 类的完整清单到 csv, 供人工逐条判断。

用法:
  python scripts/qa_dump.py D:\projects\sims4_trans\output\translation_qa_report.csv [--errors] [--review-prefix 前缀]
示例:
  python scripts/qa_dump.py ...\translation_qa_report.csv --errors            # 全部 ERROR
  python scripts/qa_dump.py ...\translation_qa_report.csv --review-prefix 残留英文   # 该REVIEW类
"""
import csv, sys
from pathlib import Path
from collections import OrderedDict


def main():
    args = sys.argv[1:]
    report = Path(args[0])
    want_errors = "--errors" in args
    prefixes = []
    i = 0
    while i < len(args):
        if args[i] == "--review-prefix":
            prefixes.append(args[i+1]); i += 2
        else:
            i += 1

    rows = list(csv.DictReader(open(report, encoding="utf-8-sig")))
    out = OrderedDict()
    for r in rows:
        qa = r["qa"]; reason = r["qa_reason"] or ""
        if want_errors and qa == "ERROR":
            out.setdefault("ERROR", []).append(r)
        if prefixes and qa == "REVIEW":
            for p in prefixes:
                if reason.startswith(p):
                    out.setdefault("REVIEW:"+p, []).append(r); break

    for key, arr in out.items():
        fn = report.parent / f"qa_dump_{key.replace(':','_').replace(' ','_')}.csv"
        cols = ["translation_id","source_text","translate_mode","status","protected_spans","translation","qa","qa_reason"]
        with open(fn,"w",encoding="utf-8-sig",newline="") as f:
            w=csv.DictWriter(f,fieldnames=cols); w.writeheader(); w.writerows(arr)
        print(f"[{key}] {len(arr)} 条 -> {fn.name}")
        if "--print-all" in args:
            print("  --- 全部条目 (tid | src | zh) ---")
            for r in arr:
                print(f"  {r['translation_id']:16s} | {(r['source_text'] or ''):45s} | {(r['translation'] or '')}")
        else:
            for r in arr[:6]:
                print(f"  {r['translation_id']:16s} src={(r['source_text'] or '')[:45]!r}  zh={(r['translation'] or '')[:45]!r} :: {(r['qa_reason'] or '')[:55]}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
