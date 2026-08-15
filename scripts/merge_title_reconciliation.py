#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_title_reconciliation.py — 把 TITLE terminal KEEP + manual final TRANSLATE
两层人工 reconciliation 合并进 production output/translation_overrides.csv,
使下游 (phase2b_translate.load_overrides / c_extract+final_todo.load_terminal_keep_tids /
audit_override_workset_conflicts) 全部能读到, 不会只有孤儿 config 文件。

原则 (2026-08-15 用户裁决):
  - 3 条 terminal KEEP    (configs/title_terminal_keep.c26.csv)
    simonly_VixenPoster#1/#2/#3 -> action=KEEP, 不进 retry, 不进 model workset
  - 2 条 manual final TRANSLATE (configs/title_manual_translate.c26.csv)
    RosieSimsie_NSFW_CouplePoses_AllYours -> RosieSimsie_NSFW_情侣姿势_全属于你
    motherlode_fight -> motherlode_打斗
    -> action=TRANSLATE + translation, 人工定稿, 禁止再送模型
  - 只读输入 (configs 源 + 现有 output/translation_overrides.csv)
  - 输出到 output/translation_overrides.csv (idempotent merge: 按 (tid,source) 幂等,
    不覆盖已存在的不同值 -> 若冲突 STOP 并列出, 不静默覆盖)
  - 不调 LLM / 不改 cache / 不 write package

幂等性: 重复运行结果相同; 已合并的行不会重复追加。
用法:
  python scripts/merge_title_reconciliation.py <out_dir>
    默认源 = configs/title_terminal_keep.c26.csv + configs/title_manual_translate.c26.csv
"""
import sys, os, csv
from pathlib import Path

COLS = ["translation_id", "source_text", "translation", "action", "reason", "notes"]
ACTIONS_OK = {"KEEP", "TRANSLATE"}


def _load(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    out = []
    for r in rows:
        tid = (r.get("translation_id") or "").strip()
        src = (r.get("source_text") or "").strip()
        act = (r.get("action") or "").strip().upper()
        if not tid or not src:
            continue
        if act not in ACTIONS_OK:
            print(f"[!] {path}: {tid} action={act!r} 非法, 跳过"); continue
        out.append({
            "translation_id": tid, "source_text": src,
            "translation": (r.get("translation") or "").strip(),
            "action": act,
            "reason": (r.get("reason") or "").strip(),
            "notes": (r.get("notes") or "").strip(),
        })
    return out


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/merge_title_reconciliation.py <out_dir>"); return 1
    out_dir = Path(sys.argv[1])
    roo = Path(__file__).resolve().parent.parent
    srcs = [
        roo / "configs" / "title_terminal_keep.c26.csv",
        roo / "configs" / "title_manual_translate.c26.csv",
    ]
    new = []
    for s in srcs:
        if not s.exists():
            print(f"[!] 缺 {s}"); return 1
        new.extend(_load(s))
    new_by_key = {(r["translation_id"], r["source_text"]): r for r in new}
    print(f"[title reconciliation] 待合并 = {len(new_by_key)} 条  "
          f"(KEEP={sum(1 for r in new if r['action']=='KEEP')}, "
          f"TRANSLATE={sum(1 for r in new if r['action']=='TRANSLATE')})")

    ovr_path = out_dir / "translation_overrides.csv"
    merged = {}
    if ovr_path.exists():
        for r in _load(str(ovr_path)):
            merged[(r["translation_id"], r["source_text"])] = r
    before = len(merged)

    conflicts = []
    added = 0
    for k, r in new_by_key.items():
        if k in merged:
            ex = merged[k]
            if (ex["action"] != r["action"]) or (
                    r["action"] == "TRANSLATE" and ex["translation"] != r["translation"]):
                conflicts.append((k, ex, r))
                continue
            # 同值 -> 幂等, 已存在 (不重复追加)
            continue
        merged[k] = r
        added += 1

    if conflicts:
        print("\n[STOP] 冲突, 不写文件 (不允许静默覆盖):")
        for k, ex, r in conflicts:
            print(f"  {k}: existing action={ex['action']} trans={ex['translation']!r} "
                  f"vs new action={r['action']} trans={r['translation']!r}")
        return 1

    # 写出 (保留其余 override 行, 追加新层)
    with open(ovr_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in merged.values():
            w.writerow({c: r.get(c, "") for c in COLS})
    print(f"[写出] {ovr_path}  add={added}  total={before+added}  "
          f"(KEEP={sum(1 for r in merged.values() if r['action']=='KEEP')}, "
          f"TRANSLATE={sum(1 for r in merged.values() if r['action']=='TRANSLATE')})")
    print("[OK] TITLE 两层 reconciliation 已并入 canonical override 流, 下游可读")
    return 0


if __name__ == "__main__":
    sys.exit(main())
