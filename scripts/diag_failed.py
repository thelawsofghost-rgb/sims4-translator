#!/usr/bin/env python3
"""只读诊断: 定位全量后模型阶段失败的 phrase / translation_id。

原理 (与生产完全一致):
  - 失败 phrase 在 _on_done 里被拒 (空或 [ERR 开头) -> 不写 cache。
  - fingerprint 由 (source_phrase, glossary_hint, context) 决定; 生产用
    load_contexts() 构造行级 ctx, 复用生产同名函数保证 fingerprint 一致。
  - 对每个 FULL/PARTIAL 行的需模型 phrase: cache.get(fp) miss -> 失败。

不改任何 correctness 代码, 只读 output 的 done.csv + contexts.csv + cache。

用法:
  python scripts/diag_failed.py D:\projects\sims4_trans\output

输出:
  - 失败 phrase 总数
  - 每个失败: translation_id | source phrase | 所在行前 60 字
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

    # 生产同款 ctx 聚合 (复用 load_contexts)
    ctx_map = P.load_contexts(out_dir)

    rows = []
    with open(done_path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rows.append(r)

    cache = PhraseCache(out_dir)
    failed = []  # (tid, source_phrase, src_text)
    needcheck = 0
    for r in rows:
        mode = (r.get("translate_mode") or r.get("decision") or "").strip()
        if mode not in ("FULL_TRANSLATE", "PARTIAL_TRANSLATE"):
            continue  # KEEP/APPROVED 不进引擎, 其 phrase 不写库
        tid = r.get("translation_id", "")
        text = P.norm_text(r.get("source_text", ""))
        ctx = ctx_map.get(tid, [])
        ctx_str = " | ".join(ctx[:3]) if ctx else ""
        segs, sem = P.split_semantic_spans(text)
        gloss, pending = P.glossary_resolve(segs)
        for p in pending:
            sp = p["t"].strip()
            if not sp:
                continue
            needcheck += 1
            fp = build_fingerprint(
                source_phrase=sp,
                glossary_hint=p.get("gloss_hint", ""),
                context=ctx_str,
            )
            if not cache.get(fp):
                failed.append((tid, sp, text))
    cache.close()

    print(f"[done] 总行 {len(rows)}, 需模型 phrase 检查 {needcheck}, 识别失败 phrase = {len(failed)}")
    print("\n=== 失败 phrase 明细 (tid | failed source phrase | 所在行前60字) ===")
    for tid, sp, src in failed:
        print(f"- {tid:14s} | {sp!r}")
        print(f"    src: {src[:60]!r}")
    print(f"\n失败 phrase 合计 = {len(failed)}")
    if failed == 0:
        print("\n[OK] 无失败 phrase, cache 完整, 可进 QA。")
    else:
        print("\n[需处理] 这些 phrase 未写入 cache。可用 --only-id <真实tid> 单独重翻(见 --only-id 支持)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
