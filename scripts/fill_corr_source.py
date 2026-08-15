#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fill_corr_source.py — 从真实 done 回填 DESC content-correction 的 source_text
=========================================================================================
背景: configs/desc_content_corrections.c26.csv 的 37 条译文由 Dorothy 裁决固化(仓库内),
  但 source_text 列留空(raw-in-repo 不含真实源)。真实英文源只存在于 Windows 的
  translation_done_batch_desc.csv (DONE 行)。为了让 build_override_overlay.py 的
  (tid,norm_source) 跨层检查与 build_desc_final.py 的 source-mismatch 检查基于真实源,
  用本脚本从 done 回填 source_text (写入 correction config 同文件, 幂等)。

规则 (fail-closed):
  - corr 的 translation_id 必须存在于 done (且 status=DONE), 否则 HARD-FAIL
  - done 的 source_text 非空, 否则 HARD-FAIL
  - 回填后 corr 行 source_text 与 done 逐 tid 一致 (只写, 不凭空造 source)
  - default dict 不写: --no-write 时只报告待填而不落盘

用法 (Windows):
  python scripts/fill_corr_source.py \
    --corr configs/desc_content_corrections.c26.csv \
    --done output/translation_done_batch_desc.csv
"""
import sys, os, csv, argparse


def _u(s):
    return (s or "").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corr", default="configs/desc_content_corrections.c26.csv",
                    help="DESC content-correction config (37, source 待填)")
    ap.add_argument("--done", required=True,
                    help="真实 run done (translation_done_batch_desc.csv, 190 行)")
    ap.add_argument("--no-write", action="store_true", help="只报告, 不落盘")
    a = ap.parse_args()

    hf = []
    for p in (a.corr, a.done):
        if not os.path.exists(p):
            hf.append(f"输入文件不存在: {p}")
    if hf:
        print("[HARD-FAIL] " + "; ".join(hf)); sys.exit(1)

    # done: tid -> (status, source_text)  (先用首见, 下面重建校验重复)
    done = {}
    dup = []
    with open(a.done, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            tid = _u(r.get("translation_id"))
            if not tid:
                continue
            if tid in done:
                dup.append(tid)
            done[tid] = {"status": (r.get("status") or "").strip(),
                         "source_text": (r.get("source_text") or "").strip()}
    if dup:
        print(f"[HARD-FAIL] done duplicate tid: {sorted(set(dup))}"); sys.exit(1)

    # corr rows
    with open(a.corr, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    hdr = list(rows[0].keys()) if rows else ["translation_id", "source_text", "translation", "action", "reason", "notes"]
    if "source_text" not in hdr:
        print("[HARD-FAIL] corr 缺 source_text 列"); sys.exit(1)

    fail = []
    n_fill = 0
    for r in rows:
        tid = _u(r.get("translation_id"))
        if not tid:
            fail.append("corr 行缺 tid"); continue
        if tid not in done:
            fail.append(f"corr tid 不在 done: {tid}"); continue
        if done[tid]["status"] != "DONE":
            fail.append(f"corr tid 的 done status != DONE: {tid}={done[tid]['status']}"); continue
        src = done[tid]["source_text"]
        if not src:
            fail.append(f"done 的 source_text 为空: {tid}"); continue
        # 若 corr 已有不同 source 则报冲突(避免误覆盖已有真实值)
        cur = _u(r.get("source_text"))
        if cur and cur != src:
            fail.append(f"corr source 冲突 {tid}: 已有 {cur!r} != done {src!r}"); continue
        r["source_text"] = src
        n_fill += 1

    if fail:
        print("[HARD-FAIL] correction source 回填失败:")
        for m in sorted(set(fail)):
            print(f"  - {m}")
        print("  -> 不写文件, 请人工处理。"); sys.exit(1)

    print(f"corr source_text 可填 {n_fill}/{len(rows)} 行 (全部来自 done, 未凭空造 source)。")
    if a.no_write:
        print("[dry-run] --no-write: 未落盘。")
        return 0

    with open(a.corr, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=hdr)
        w.writeheader()
        w.writerows(rows)
    print(f"[OK] 已回填 {a.corr} (source_text 来自 done, {n_fill} 行)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
