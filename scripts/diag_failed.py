#!/usr/bin/env python3
"""只读诊断: 定位全量后模型阶段失败的 phrase / translation_id。

原理 (与生产一致):
  - 失败 phrase 在 _on_done 里被拒 (空或 [ERR 开头) -> 不写 cache、不写 phrase_res。
  - 所以对每行, "需要的全部需模型 phrase" - "cache 里该行已存的 phrase" = 失败 phrase。
  - 用 cache 反查: fingerprint(source_phrase) 是否命中, 命中的是成功项。

不改任何 correctness 代码, 只读 output 的 done.csv + cache。

用法:
  python scripts/diag_failed.py D:\projects\sims4_trans\output

输出:
  - 失败 phrase 总数
  - 每个失败: translation_id | source phrase | 所属行 source_text 片段
"""
import sys
import csv
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import phase2b_translate as P
from phrase_cache import PhraseCache, build_fingerprint


def main():
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "output")
    done_path = out_dir / "translation_done.csv"
    if not done_path.exists():
        print(f"[FATAL] 未找到 {done_path}")
        return 1

    rows = []
    with open(done_path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rows.append(r)

    cache = PhraseCache(out_dir)

    failed = []  # (tid, source_phrase, src_text)
    for r in rows:
        mode = (r.get("translate_mode") or r.get("decision") or "")
        # 只扫描实际进引擎的行 (FULL/PARTIAL); KEEP/APPROVED 不进引擎, 其 phrase 不写库
        if mode not in ("FULL_TRANSLATE", "PARTIAL_TRANSLATE"):
            continue
        tid = r.get("translation_id", "")
        text = P.norm_text(r.get("source_text", ""))
        segs, sem = P.split_semantic_spans(text)
        gloss, pending = P.glossary_resolve(segs)
        for p in pending:
            sp = p["t"].strip()
            if not sp:
                continue
            fp = build_fingerprint(source_phrase=sp)
            if not cache.get(fp):
                # 该 phrase 无 cache 项 -> 成功时必写库, 未写则失败
                failed.append((tid, sp, text))

    cache.close()

    print(f"[done] 总行 {len(rows)}, 识别失败 phrase = {len(failed)}")
    print("\n=== 失败 phrase 明细 (tid | failed source phrase | 所在行前60字) ===")
    for tid, sp, src in failed:
        print(f"- {tid:14s} | {sp!r}")
        print(f"    src: {src[:60]!r}")
    print(f"\n失败 phrase 合计 = {len(failed)}")
    if failed:
        print("\n[需处理] 这些 phrase 未写入 cache, 需单独重翻(可忽略 --force 单独跑这少数几行)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
