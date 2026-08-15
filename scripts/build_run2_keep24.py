#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
build_run2_keep24.py —— 由真实 run2_unresolved_unique_XX.csv 冻结 KEEP24 layer
==================================================================================
Dorothy 裁决 (2026-08-15): run2 unresolved 24/24 unique decisions = KEEP,
provenance = RUN2_TECHNICAL_LABEL_KEEP。理由: 全部 POSE_DISPLAY_NAME, source 仅属
  ^\d+$
  ^Pose\s+\d+$
均为无语义姿势编号/技术标签。

只做一件事(非破坏): 从真实 dump unique CSV 生成独立 frozen layer
  configs/run2_unresolved_keep.c26.csv
并 HARD-FAIL 验证:
  rows == 24
  unique(tid, norm_source) == 24
  role == POSE_DISPLAY_NAME 全部成立
  source regex 只允许 ^\d+$ 或 ^Pose\s+\d+$   (other=0)
 与现有 production overlay key 交集 == 0
不使用 dump 里没有的新 TID; 直接沿用 dump 的稳定 translation_id + source
(不重算, 不造 package-specific TID)。

输出列 (与既有 c26 layers 同 schema):
  translation_id, source_text, translation, action, reason, notes
  action = KEEP; translation = 空; reason = RUN2_TECHNICAL_LABEL_KEEP

用法:
  python scripts/build_run2_keep24.py \
      --unique output\run2_unresolved_unique_24.csv \
      --overlay output\translation_overrides.production.csv \
      --out configs\run2_unresolved_keep.c26.csv \
      [--no-write] [--expect 24]
"""
import sys, os, csv, re, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def norm_text(s):
    from phase2a_catalog import norm_text as _nt
    return _nt(s or "")


_SRC_RE = re.compile(r"^\d+$|^Pose\s+\d+$", re.IGNORECASE)


def _load_overlay_keys(path):
    keys = set()
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            tid = (r.get("translation_id") or "").strip()
            if tid:
                keys.add((tid, norm_text(r.get("source_text") or "")))
    return keys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--unique", required=True, help="真实 run2_unresolved_unique_XX.csv")
    ap.add_argument("--overlay", required=True, help="现有 production overlay (核对 key 交集)")
    ap.add_argument("--out", required=True, help="冻结输出 configs/run2_unresolved_keep.c26.csv")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--expect", default="24", help="期望 unique KEEP 行数 (默认 24)")
    a = ap.parse_args()

    for p, lab in [(a.unique, "unique"), (a.overlay, "overlay"), (a.out, "out")]:
        pp = Path(p)
        if lab in ("unique", "overlay") and not pp.exists():
            print(f"[HARD-FAIL] {lab} 不存在: {p}")
            return 3

    print("=== run2 KEEP24 冻结 (Dorothy 裁决 RUN2_TECHNICAL_LABEL_KEEP) ===")
    src_rows = []
    with open(a.unique, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            src_rows.append(r)

    # role 全 POSE + source regex 校验 (只读, 逐行)
    bad = []
    for i, r in enumerate(src_rows):
        role = (r.get("role(s)") or "").strip().upper()
        src = (r.get("source_text") or "").strip()
        if "POSE_DISPLAY_NAME" not in role:
            bad.append(f"row{i} role={role!r} 非 POSE_DISPLAY_NAME")
        if not _SRC_RE.fullmatch(src):
            bad.append(f"row{i} source={src!r} 不在允许正则 (^\\d+$ | ^Pose\\s+\\d+$)")
    if bad:
        print("[HARD-FAIL] 存在 role/source 违规 (other>0):")
        for m in bad:
            print(f"  {m}")
        return 2

    # translation_id 必须存在且稳定
    rows = []
    seen = {}
    for r in src_rows:
        tid = (r.get("translation_id") or "").strip()
        src = (r.get("source_text") or "").strip()
        if not tid:
            print("[HARD-FAIL] 存在空 translation_id"); return 2
        nsrc = norm_text(src)
        if tid in seen and seen[tid] != nsrc:
            print(f"[HARD-FAIL] 同 tid {tid} 不同 norm_source"); return 2
        seen[tid] = nsrc
        rows.append({"translation_id": tid, "source_text": src, "translation": "",
                     "action": "KEEP", "reason": "RUN2_TECHNICAL_LABEL_KEEP", "notes": ""})

    n = len(rows)
    nuniq = len({(r["translation_id"], norm_text(r["source_text"])) for r in rows})
    print(f"[rows] {n} | unique(tid,norm_source) = {nuniq}")
    if n != int(a.expect) or nuniq != int(a.expect):
        print(f"[HARD-FAIL] 期望 rows={a.expect} unique={a.expect}, 实得 {n}/{nuniq}")
        return 2

    # 与现有 production overlay key 交集
    overlay_keys = _load_overlay_keys(a.overlay)
    new_keys = {(r["translation_id"], norm_text(r["source_text"])) for r in rows}
    inter = new_keys & overlay_keys
    print(f"[overlay] 现有 production overlay unique keys = {len(overlay_keys)}")
    print(f"[overlay] 新 KEEP24 ∩ 现有 overlay = {len(inter)}")
    if inter:
        print("[HARD-FAIL] 新 KEEP24 与现有 overlay key 交集非空 (不允许):")
        for k in sorted(inter):
            print(f"  {k}")
        return 2
    print("[overlay] disjoint OK: 新 KEEP24 与现有 overlay 无交集")

    if a.no_write:
        print("[dry-run] --no-write: 未写 frozen layer。校验通过。")
        return 0

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["translation_id", "source_text", "translation", "action", "reason", "notes"])
        for r in sorted(rows, key=lambda x: x["translation_id"]):
            w.writerow([r["translation_id"], r["source_text"], r["translation"],
                        r["action"], r["reason"], r["notes"]])
    print(f"[写出 frozen layer] {a.out} ({n} 行 unique KEEP)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
