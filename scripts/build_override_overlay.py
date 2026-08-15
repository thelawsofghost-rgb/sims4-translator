#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_override_overlay.py — 构建 TITLE production override overlay (非破坏, 只读 frozen)
========================================================================================
禁止修改 frozen 文件: 本脚本**绝不写** output/translation_overrides.csv (frozen 人工 override
基线, 必须 byte/content 不变)。仅生成**新的 derived** 文件:

    output/translation_overrides.production.csv

= frozen translation_overrides.csv         (基线, byte 不变, 必在)
+ frozen translation_overrides.final2.csv  (既有 precedence 上层, 若存在; 后覆盖先)
+ c26 pose KEEP layer   (output/translation_overrides.c26_pose_keep.csv, action=KEEP)
+ title terminal KEEP    (configs/title_terminal_keep.c26.csv,   3 条, action=KEEP)
+ title manual TRANSLATE (configs/title_manual_translate.c26.csv, 2 条, action=TRANSLATE 定稿)
+ --extra 任意附加 immutable override layer (可 repeatable --overrides 语义)

下游 (production resolver / conflict audit / phase2b.load_overrides / c_extract+final_todo
load_terminal_keep_tids) 从新 derived 文件读取, 由调用方显式传路径 (repeatable --overrides);
绝不原地修改 frozen。

硬/安全保证 (2026-08-15 用户裁决):
  - 输入全部只读; 输出写到 .production.csv; 不改/不触碰 frozen 及其余任何文件
  - deterministic + idempotent (同输入 -> 逐字节相同输出; 重复运行结果一致)
  - same (tid, norm_source) 且 action/translation 不同
       且**非** frozen<->final2 既有 precedence 之间 -> HARD-FAIL (exit 非0, 不写)
  - frozen<->final2 之间为既有 precedence (final2 > frozen), 允许覆盖 (final2 是 frozen superset)
  - source mismatch (同 tid 不同 norm_source)                    -> HARD-FAIL
  - 缺必需层 / 缺 source_text / action 非法                       -> HARD-FAIL
  - 报告各 layer 行数 及 最终 unique (tid,norm_source) 数
  - 归一用 norm_text (与下游 load_overrides/override_for 一致)
  - 不调 LLM / 不改 cache / 不 write package

用法:
  python scripts/build_override_overlay.py <out_dir> [--extra <layer.csv> ...] \
        [--out <path>] [--no-write]
"""
import sys, os, csv, argparse
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

ACTIONS_OK = {"KEEP", "TRANSLATE", "REVIEW"}
# 既有 precedence 层: 它们按既有语义后覆盖先, 同 key 覆盖不算 HARD-FAIL。
# final2 是 frozen 的 superset (真实数据: frozen 22 ⊆ final2 38, 同 key 值全相同)。
PRECEDENCE_LAYERS = {"frozen translation_overrides.csv",
                     "frozen translation_overrides.final2.csv"}


def norm_text(s):
    from phase2a_catalog import norm_text
    return norm_text(s or "")


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
            rows.append({
                "translation_id": tid,
                "source_text": src,
                "translation": tr,
                "action": act,
                "reason": (r.get("reason") or "").strip(),
                "notes": (r.get("notes") or "").strip(),
                "_layer": label,
            })
    return rows


def norm_key(r):
    return (r["translation_id"], norm_text(r["source_text"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--extra", action="append", default=[], help="附加 immutable override layer CSV")
    ap.add_argument("--out", default=None, help="derived overlay 输出路径 (缺省 out_dir/translation_overrides.production.csv)")
    ap.add_argument("--no-write", action="store_true", help="dry-run, 只校验不写")
    a = ap.parse_args()
    out_dir = Path(a.out_dir)
    if not out_dir.is_dir():
        print(f"[HARD-FAIL] out_dir 不是目录: {out_dir}")
        return 3
    ROOT = Path(__file__).resolve().parent.parent

    layers = []
    layers.append(("frozen translation_overrides.csv", out_dir / "translation_overrides.csv", True))
    layers.append(("frozen translation_overrides.final2.csv", out_dir / "translation_overrides.final2.csv", False))
    for x in a.extra:
        layers.append((f"extra {Path(x).name}", Path(x), True))
    layers.append(("pose KEEP layer", out_dir / "translation_overrides.c26_pose_keep.csv", False))
    layers.append(("title terminal KEEP", ROOT / "configs" / "title_terminal_keep.c26.csv", True))
    layers.append(("title manual final TRANSLATE", ROOT / "configs" / "title_manual_translate.c26.csv", True))

    merged = {}       # norm_key -> row (后层覆盖前层)
    layer_counts = []
    src_tid = {}      # tid -> set(norm_source)  (source mismatch 检测)
    conflicts = []
    try:
        for label, path, req in layers:
            rows = load_rows(path, label, req)
            layer_counts.append((label, len(rows)))
            for r in rows:
                tid = r["translation_id"]
                ns = norm_text(r["source_text"])
                if tid in src_tid and ns not in src_tid[tid]:
                    raise HardFail(
                        f"[source mismatch] layer={label} tid={tid} "
                        f"已见 norm_source={sorted(src_tid[tid])}, 现出现 {ns!r}")
                src_tid.setdefault(tid, set()).add(ns)
                k = (tid, ns)
                if k in merged:
                    prev = merged[k]
                    is_precedence = (label in PRECEDENCE_LAYERS) and (prev["_layer"] in PRECEDENCE_LAYERS)
                    if is_precedence:
                        merged[k] = r   # final2 > frozen 既有 precedence: 覆盖
                        continue
                    if (prev["action"] != r["action"]) or (
                            r["action"] == "TRANSLATE" and prev["translation"] != r["translation"]):
                        conflicts.append((k, prev, r, label))
                        continue
                    # 同值 -> 更新层标记 (幂等)
                    merged[k]["_layer"] = r["_layer"]
                else:
                    merged[k] = r
    except HardFail as e:
        print(f"[HARD-FAIL] {e}")
        print("  -> frozen 未被修改; 无 derived 文件写出。请人工解决后重跑。")
        return 3

    print("=== production override overlay 构建 (非破坏, 只读 frozen) ===")
    print("layer 次序 (precedence 从低到高, 后层覆盖前层):")
    for label, cnt in layer_counts:
        print(f"  {label:<36} : {cnt:3d} 行")
    if conflicts:
        print("\n[HARD-FAIL] 同 (tid,norm_source) 不同 action/translation, 不写 overlay:")
        for k, prev, new, label in conflicts:
            print(f"  {k}: prev[{prev['_layer']}] action={prev['action']} trans={prev['translation']!r} "
                  f"vs new[{label}] action={new['action']} trans={new['translation']!r}")
        print("  -> frozen 未被修改; 无 derived 文件写出。")
        return 2

    n_keep = sum(1 for r in merged.values() if r["action"] == "KEEP")
    n_tr = sum(1 for r in merged.values() if r["action"] == "TRANSLATE")
    n_rev = sum(1 for r in merged.values() if r["action"] == "REVIEW")
    print(f"最终 unique (tid,norm_source) = {len(merged)}  "
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
    print("[OK] frozen output/translation_overrides.csv 未被修改 (byte/content 不变)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
