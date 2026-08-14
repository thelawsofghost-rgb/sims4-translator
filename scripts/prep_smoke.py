#!/usr/bin/env python3
"""生成生产 main() smoke-test 用的隔离 out_dir (output_smoke)。

从真实 output/translations_todo.csv 中按"确定性非随机"规则挑选覆盖各类别的行,
写入 output_smoke/translations_todo.csv 并复制 translation_contexts.csv,
从而:
  - 保持生产 main() 全链路 (决策层 APPROVED/KEEP/PARTIAL/FULL + cache 写回)
  - 使用独立 cache (output_smoke/translation_cache.db), 不污染正式 output cache
  - 固定 ID, 可复现, 不像 --sample 那样随机

用法: python scripts/prep_smoke.py [--todo output/translations_todo.csv] [--ctx output/translation_contexts.csv] [--out output_smoke] [--max 40]
"""
import sys, os, csv, re, shutil
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
    real_dir = Path(_arg("--out", "output_smoke"))
    todo_path = Path(_arg("--todo", "output/translations_todo.csv"))
    ctx_path = Path(_arg("--ctx", "output/translation_contexts.csv"))
    max_n = int(_arg("--max", "40"))

    assert todo_path.exists(), f"todo 不存在: {todo_path}"
    rows = list(csv.DictReader(open(todo_path, encoding="utf-8-sig")))
    print(f"[输入] 真实 todo = {len(rows)} 行 @ {todo_path}")

    # ---- 确定性分类 ----
    def classify(r):
        text = A.norm_text(r.get("source_text", ""))
        if text in P.APPROVED_TEXT:
            return "APPROVED"
        mode, _, _ = P.translate_mode_for(text)
        return mode

    # 特殊踩坑 pattern (source_text 子串, casefold 不敏感)
    def tags(r):
        t = (r.get("source_text") or "").casefold()
        out = []
        for pat in ["f1", "f2", "m1", "m2", "pose 6.2", "sim", "idle",
                    "all in one", "right", "left", "middle",
                    "positive", "negative", "neutral", "pen + paper",
                    "*anim", "6.2", "v1", "v2"]:
            if pat in t:
                out.append(pat)
        return out

    # 每类至少保底选一些; 再按特殊 tag 补选
    bucket = {"APPROVED": [], "KEEP": [], "PARTIAL_TRANSLATE": [], "FULL_TRANSLATE": []}
    for r in rows:
        c = classify(r)
        if c in bucket:
            bucket[c].append(r)

    picked = []
    seen_ids = set()

    def add(r):
        tid = r.get("translation_id")
        if tid in seen_ids or len(picked) >= max_n:
            return False
        seen_ids.add(tid)
        picked.append(r)
        return True

    # 1) 先每类至少 1 个
    for c in ["APPROVED", "KEEP", "PARTIAL_TRANSLATE", "FULL_TRANSLATE"]:
        for r in bucket[c]:
            if add(r):
                break
    # 2) 再按特殊 tag 补 (含所有踩坑 pattern)
    for r in rows:
        if len(picked) >= max_n:
            break
        if tags(r):
            add(r)
    # 3) 填满到 max_n (按序扫真实 todo)
    for r in rows:
        if len(picked) >= max_n:
            break
        add(r)

    # ---- 输出统计 ----
    from collections import Counter
    cnt = Counter(classify(r) for r in picked)
    print(f"[选中] {len(picked)} 行  (目标 {max_n})")
    print("  decision 分布:", dict(cnt))
    all_tags = Counter()
    for r in picked:
        for t in tags(r):
            all_tags[t] += 1
    print("  踩坑 tag 覆盖:", dict(all_tags))
    print("  selected translation_id: " + ", ".join(r.get("translation_id") for r in picked))

    # 校验覆盖
    need = {"APPROVED", "KEEP", "PARTIAL_TRANSLATE", "FULL_TRANSLATE"} - set(cnt)
    if need:
        print(f"[警告] 缺少类别: {need}  (真实 todo 可能没有该类行)")
    for pat in ["f1", "f2", "m1", "m2", "pose 6.2", "all in one", "pen + paper"]:
        if not any(pat in (r.get("source_text") or "").casefold() for r in picked):
            print(f"[警告] 真实 todo 中找不到踩坑 pattern: {pat!r}")

    # ---- 写出隔离 out_dir ----
    out = Path(real_dir)
    out.mkdir(parents=True, exist_ok=True)
    cols = list(rows[0].keys())
    with open(out / "translations_todo.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in picked:
            w.writerow(r)
    if ctx_path.exists():
        shutil.copy(ctx_path, out / "translation_contexts.csv")
        print(f"[写出] {out/'translation_contexts.csv'} (复制)")
    else:
        print(f"[提示] 无 {ctx_path}, 该 smoke out_dir 无 context (仍可跑)")
    print(f"[写出] {out/'translations_todo.csv'}  ({len(picked)} 行)")
    print("\n下一步: python scripts\\phase2b_translate.py output_smoke --force")


if __name__ == "__main__":
    main()
