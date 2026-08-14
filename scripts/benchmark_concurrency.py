#!/usr/bin/env python3
"""单维度并发 benchmark: 固定 batch_size=8, 固定同一批 phrase, 只扫 concurrency。

用法:
  python scripts/benchmark_concurrency.py [--todo output/translations_todo.csv]
                                          [--ctx output/translation_contexts.csv]
                                          [--n-phrases 128]
                                          [--batch-size 8]
                                          [--cons 2,4,8]

方法论 (按用户约束):
  - 固定同批 translation_ids / phrase 列表 (先跑一次 warm-up 让模型加载进 GPU)。
  - 每组用完全相同的 phrase+context blocks, 隔离 cache (--force 语义: 全 miss)。
  - 只改 concurrency, 不动 batch_size (默认 8)。
  - 不测 16 (OLLAMA_NUM_PARALLEL=1, 先看 2/4/8 收益)。
  - 不改任何 correctness 代码: request_count / retry 通过在运行期包装
    _call_batch 计数 (不修改 phase2b_translate.py)。

输出每组: concurrency, batch_size, phrase_count, request_count, elapsed,
          phrases/min, succeeded, failed, retry。
"""
import sys
import csv
import time
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import phase2b_translate as P
import phase2a_catalog as A


def _arg(name, default):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main():
    todo_path = Path(_arg("--todo", "output/translations_todo.csv"))
    ctx_path = Path(_arg("--ctx", "output/translation_contexts.csv"))
    n_phrases = int(_arg("--n-phrases", "128"))
    batch_size = int(_arg("--batch-size", "8"))
    cons = [int(x) for x in _arg("--cons", "2,4,8").split(",") if x.strip()]

    # ---- 固定同一批: 从真实 todo 取 need_translate (PENDING=FULL+PARTIAL) 前 N 行 ----
    rows = []
    with open(todo_path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if r.get("status", "").strip().upper() in ("PENDING", ""):
                rows.append(r)
    if not rows:
        print("[FATAL] todo 无可翻译行 (status 非 PENDING)。给 --todo 指向真实 output/translations_todo.csv")
        return 1
    print(f"[输入] 真实 todo 可翻行 = {len(rows)} (取前 {n_phrases} 固定)")

    ctx_map = {}
    if ctx_path.exists():
        with open(ctx_path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                ctx_map.setdefault(r.get("translation_id", ""), []).append(
                    r.get("context_text") or r.get("source_text") or "")

    # ---- 生产同款语义切分 + glossary: 构造成完全相同、跨组一致的 phrase blocks ----
    phrase_items = []   # (ck, block) 2-tuple, 交引擎
    ck_to_src = {}
    fmt_ck = 0
    for r in rows[:n_phrases]:
        tid = r.get("translation_id")
        text = A.norm_text(r.get("source_text"))
        ctx = ctx_map.get(tid, [])[:3]
        segs, sem = P.split_semantic_spans(text)
        gloss, pending = P.glossary_resolve(segs)
        pending = [p for p in pending if p["t"].strip()]
        if not pending:
            continue  # 全 glossary 直译, 不进模型 (不计 phrase)
        for p in pending:
            block = f"Target: {p['t'].strip()}"
            if p.get("gloss_hint"):
                block += f"\n(glossary: {p['gloss_hint']})"
            if ctx:
                block += f"\nContext: {' | '.join(ctx)}"
            phrase_items.append((f"ck{fmt_ck}", block))
            ck_to_src[f"ck{fmt_ck}"] = p["t"].strip()
            fmt_ck += 1

    phrase_count = len(phrase_items)
    if phrase_count < 1:
        print("[FATAL] 无任何需模型 phrase")
        return 1
    print(f"[词组] phrase_count = {phrase_count}  (跨组固定)")

    # ---- 运行期包装 _call_batch 计数 (不修改生产文件) ----
    eng = P.OllamaTranslator()
    total_requests = [0]
    retry_calls = [0]
    # 记录每个 batch 首次是否成功, 超第一次的调用计为 retry
    first_ok = {}
    orig_call = eng._call_batch

    def counting_call(items):
        key = tuple(k for k, _ in items)
        total_requests[0] += 1
        keymap, zh, st = orig_call(items)
        if key in first_ok:
            if first_ok[key] is False:
                retry_calls[0] += 1
            first_ok[key] = True
        else:
            first_ok[key] = (st == "ok")
            if st != "ok":
                retry_calls[0] += 1
        return keymap, zh, st

    eng._call_batch = counting_call

    # ---- 先 warm-up (不计成绩), 让 ni-fei 加载进 GPU ----
    print("\n== warm-up (不计成绩, 加载模型) ==")
    warm = phrase_items[: min(8, phrase_count)]
    t0 = time.time()
    eng.translate_batch(warm, concurrency=2, per_call=8)
    print(f"  warm-up 完成, 耗时 {time.time()-t0:.1f}s (模型已加载)\n")

    # 清掉 warm-up 的计数, 且每组用独立临时 cache (全 miss, 同 phrase set)
    total_requests[0] = 0
    retry_calls[0] = 0
    first_ok.clear()

    rows_out = []
    print(f"固定: batch_size={batch_size}, phrase_count={phrase_count}\n")
    for c in cons:
        total_requests[0] = 0
        retry_calls[0] = 0
        first_ok.clear()
        iso = Path(tempfile.mkdtemp(prefix="bench_"))
        iso.mkdir(parents=True, exist_ok=True)
        (iso / "translation_cache.db").unlink(missing_ok=True)
        from phrase_cache import PhraseCache, build_fingerprint
        cache = PhraseCache(iso)
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        committed = set()

        def on_done(ck, zh):
            if ck in committed:
                return
            committed.add(ck)
            if not zh or zh.startswith("[ERR"):
                return
            src = ck_to_src.get(ck)
            cache.put(
                fingerprint=build_fingerprint(source_phrase=src),
                translation_id="BENCH", segment_index=0, source_phrase=src,
                source_hash=A.source_hash(src), translation=zh, now=now)

        t0 = time.time()
        raw = eng.translate_batch(
            phrase_items, concurrency=c, per_call=batch_size, on_done=on_done)
        elapsed = time.time() - t0
        succeeded = sum(1 for z in raw.values() if z and not z.startswith("[ERR"))
        failed = sum(1 for z in raw.values() if not z or z.startswith("[ERR"))
        cache.close()
        ppm = succeeded * 60.0 / max(elapsed, 1e-9)
        rows_out.append((c, batch_size, phrase_count, total_requests[0],
                         elapsed, ppm, succeeded, failed, retry_calls[0]))
        print(f"concurrency={c:2d} batch_size={batch_size:2d} "
              f"phrase={phrase_count:4d} request={total_requests[0]:4d} "
              f"elapsed={elapsed:6.2f}s phrases/min={ppm:7.1f} "
              f"success={succeeded} failed={failed} retry={retry_calls[0]}", flush=True)

    print("\n==== benchmark 汇总 (concurrency 单维度) ====")
    print("concurrency | batch_size | phrase_count | request_count | elapsed(s) | phrases/min | success | failed | retry")
    for c, bs, pc, rc, el, ppm, s, fl, rt in rows_out:
        print(f"{c:11d} | {bs:10d} | {pc:12d} | {rc:13d} | {el:11.2f} | {ppm:11.1f} | {s:7d} | {fl:6d} | {rt}")

    # 判据提示
    best = min(rows_out, key=lambda x: x[4])
    print(f"\n最快: concurrency={best[0]} ({best[4]:.2f}s, {best[5]:.1f} phrases/min, "
          f"failed={best[7]})")
    if best[7]:
        print("⚠ 最快组合有 failed, 需重查")
    else:
        print("bottleneck 提示: 若 2≈4≈8 -> 瓶颈在 Ollama 服务端, 停加客户端并发")
    return 0


if __name__ == "__main__":
    sys.exit(main())
