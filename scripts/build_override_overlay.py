#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_override_overlay.py — 构建 production override overlay (非破坏, 只读 frozen BASE 114)
============================================================================================
production base (frozen, 唯一, byte/content 必须不变):
  output/translation_overrides.csv = 114

增量层 (仅以下, action/translation 定稿, 非递增叠加在其上, 后列覆盖先列):
  configs/translation_overrides.c26_pose_keep.csv    -> 26 pose C26 KEEP
  configs/title_terminal_keep.c26.csv                ->  3 title terminal KEEP
  configs/title_manual_translate.c26.csv             -> 20 title manual final TRANSLATE
  configs/desc_terminal_keep.c26.csv                 ->  2 DESC terminal KEEP
  configs/desc_manual_translate.c26.csv              -> 15 DESC manual final TRANSLATE

输出 (新 derived 文件, 绝不触碰 frozen base 及其余任何文件):
  output/translation_overrides.production.csv

**不读取** (仅历史/审计用途):
  root/translation_overrides.csv        (22)
  root/translation_overrides.merged.csv (73)
  output/translation_overrides.final.csv   (95, 含5条旧值后被 canonical 修正, 禁止参与)
  output/translation_overrides.final2.csv  (111, canonical 严格子集, 不参与)
  output/translation_overrides.final3.csv  (114, canonical 完全相同历史快照, 不参与)

硬/安全保证 (2026-08-15 用户裁决):
  - 输入全部只读; 输出写到 .production.csv; 不改/不触碰 frozen base 114
  - deterministic + idempotent (同输入 -> 逐字节相同; 重复运行结果一致)
  - same tid different normalized source        -> HARD-FAIL (exit!=0, 不写)
  - same (tid,ns) different action             -> HARD-FAIL
  - same (tid,ns) different translation        -> HARD-FAIL
  - 增量层间 同 key 不同值                       -> HARD-FAIL
  - 缺必需层 / 缺 source_text / action 非法       -> HARD-FAIL
  - 报告各 layer 行数 + 各增量层 vs base 的交集 + 最终 unique (由真实输入验证, 不硬编码)
  - 归一用 norm_text (与下游 load_overrides/override_for 一致)
  - 不调 LLM / 不改 cache / 不 write package

用法:
  python scripts/build_override_overlay.py <out_dir> [--out <path>] [--no-write]
"""
import sys, os, csv, argparse
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

ACTIONS_OK = {"KEEP", "TRANSLATE", "REVIEW"}


def norm_text(s):
    try:
        from phase2a_catalog import norm_text as _nt
        return _nt(s or "")
    except Exception:
        return (s or "").strip()


class HardFail(Exception):
    pass


def load_rows(path, label, require=True):
    p = Path(path)
    if not p.exists():
        if require:
            raise HardFail(f"缺必需层 {label}: {p}")
        print(f"[skip] 可选层 {label} 不存在: {p}")
        return []
    rows = []
    with open(p, encoding="utf-8-sig") as f:
        seen_key = {}
        for r in csv.DictReader(f):
            tid = (r.get("translation_id") or "").strip()
            src = (r.get("source_text") or "").strip()
            act = (r.get("action") or "").strip().upper()
            tr = (r.get("translation") or "").strip()
            if not tid:
                continue
            if not src:
                raise HardFail(f"{label}: {tid} 缺 source_text (source mismatch)")
            if act not in ACTIONS_OK:
                raise HardFail(f"{label}: {tid} action={act!r} 非法 (仅 KEEP/TRANSLATE/REVIEW)")
            row = {
                "translation_id": tid, "source_text": src, "translation": tr,
                "action": act,
                "reason": (r.get("reason") or "").strip(),
                "notes": (r.get("notes") or "").strip(),
                "_layer": label,
            }
            key = (tid, norm_text(src))
            if key in seen_key and seen_key[key]["translation"] != tr:
                raise HardFail(f"{label}: 同文件内同 key {key} 存在不同 translation")
            rows.append(row)
            seen_key[key] = row
    return rows


def norm_key(r):
    return (r["translation_id"], norm_text(r["source_text"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--out", default=None, help="derived overlay 输出路径 (缺省 out_dir/translation_overrides.production.csv)")
    ap.add_argument("--no-write", action="store_true", help="dry-run, 只校验不写")
    a = ap.parse_args()
    out_dir = Path(a.out_dir)
    if not out_dir.is_dir():
        print(f"[HARD-FAIL] out_dir 不是目录: {out_dir}")
        return 3
    ROOT = Path(__file__).resolve().parent.parent

    # 仅 production base + 增量层 (含 DESC KEEP2 + manual15)
    layers = [
        ("BASE output/translation_overrides.csv", out_dir / "translation_overrides.csv", True),
        ("pose C26 KEEP", ROOT / "configs" / "translation_overrides.c26_pose_keep.csv", True),
        ("title terminal KEEP", ROOT / "configs" / "title_terminal_keep.c26.csv", True),
        ("title manual final TRANSLATE", ROOT / "configs" / "title_manual_translate.c26.csv", True),
        ("desc terminal KEEP", ROOT / "configs" / "desc_terminal_keep.c26.csv", True),
        ("desc manual final TRANSLATE", ROOT / "configs" / "desc_manual_translate.c26.csv", True),
    ]

    print("=== production overlay 构建 (非破坏, 只读 frozen BASE 114) ===")
    base_rows = None
    merged = {}
    src_tid = {}
    hard_fail = []
    reports = []  # (label, n, inter_with_base)

    try:
        for label, path, req in layers:
            rows = load_rows(path, label, req)
            nk = set()
            for r in rows:
                tid, ns = r["translation_id"], norm_text(r["source_text"])
                nk.add((tid, ns))
                if tid in src_tid and ns not in src_tid[tid]:
                    hard_fail.append(f"[source mismatch] layer={label} tid={tid} "
                                     f"已见 norm_source={sorted(src_tid[tid])}, 现出现 {ns!r}")
                    continue
                src_tid.setdefault(tid, set()).add(ns)
                k = (tid, ns)
                if k in merged:
                    prev = merged[k]
                    if prev["action"] != r["action"]:
                        hard_fail.append(f"[action conflict] layer={label} key={k} "
                                         f"prev action={prev['action']} vs new action={r['action']}")
                        continue
                    if prev["translation"] != r["translation"]:
                        hard_fail.append(f"[translation conflict] layer={label} key={k} "
                                         f"prev trans={prev['translation']!r} vs new trans={r['translation']!r}")
                        continue
                    merged[k]["_layer"] = r["_layer"]  # 同值幂等
                else:
                    merged[k] = r
            inter = nk & set(merged.keys()) if merged else set()
            reports.append((label, len(rows), len(nk), len(nk & (set(base_rows) if base_rows else set())) if base_rows is not None else None))
            if label.startswith("BASE"):
                base_rows = set()
                for r in rows:
                    base_rows.add((r["translation_id"], norm_text(r["source_text"])))
    except HardFail as e:
        print(f"[HARD-FAIL] {e}")
        print("  -> frozen base 未被修改; 无 derived 文件写出。请人工解决后重跑。")
        return 3

    print("\nlayer 计数:")
    for label, n, nk, inter in reports:
        extra = f"    (与 BASE 交集 unique={inter})" if inter is not None else ""
        print(f"  {label:<38}: {n:3d} 行, unique={nk}{extra}")

    if hard_fail:
        print("\n[HARD-FAIL] 存在 source/action/translation 冲突, 不生成文件:")
        for m in hard_fail:
            print(f"  {m}")
        print("  -> frozen base 未被修改; 无 derived 文件写出。")
        return 2

    # 增量层相互交集报告 (真实输入计算, 不假设)
    print("\n增量层相互 intersection (unique key):")
    layer_keys = {}
    for label, path, req in layers:
        layer_keys[label] = {(r["translation_id"], norm_text(r["source_text"])) for r in load_rows(path, label, req)}
    inc_lbls = [l for l, _, _, _ in reports if not l.startswith("BASE")]
    for i in range(len(inc_lbls)):
        for j in range(i + 1, len(inc_lbls)):
            la, lb = inc_lbls[i], inc_lbls[j]
            inter = layer_keys[la] & layer_keys[lb]
            print(f"  {la} ∩ {lb} = {len(inter)}")

    n_keep = sum(1 for r in merged.values() if r["action"] == "KEEP")
    n_tr = sum(1 for r in merged.values() if r["action"] == "TRANSLATE")
    n_rev = sum(1 for r in merged.values() if r["action"] == "REVIEW")
    print(f"\n最终 unique (tid,norm_source) = {len(merged)}  "
          f"(KEEP={n_keep}, TRANSLATE={n_tr}, REVIEW={n_rev})")

    if a.no_write:
        print("[dry-run] --no-write: 未生成文件。校验通过 (无 HARD-FAIL)。")
        return 0

    out_path = Path(a.out) if a.out else out_dir / "translation_overrides.production.csv"
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["translation_id", "source_text", "translation", "action", "reason", "notes"])
        for r in merged.values():
            w.writerow([r["translation_id"], r["source_text"], r["translation"],
                        r["action"], r["reason"], r["notes"]])
    print(f"[写出 derived overlay] {out_path}  ({len(merged)} 行 unique)")
    print("[OK] frozen output/translation_overrides.csv (114) 未被修改 (byte/content 不变)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
