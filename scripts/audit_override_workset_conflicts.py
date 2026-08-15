#!/usr/bin/env python3
"""只读审计: 追踪任一条 tid 在全部 artifacts 里的 provenance, 并全量求出
   authoritative-TRANSLATE workset ∩ terminal-KEEP overrides 的冲突集合。

用法 (Windows, 在 D:\\projects\\sims4_trans):
  python scripts\\audit_override_workset_conflicts.py <out_dir> [tid...]
  python scripts\\audit_override_workset_conflicts.py output T_2a2ce211e44a_g1

特性:
  - 只读: 不写任何文件, 不调 LLM, 不生成 sidecar
  - tid 省略时不追踪单条, 只做全量冲突审计
  - 全量冲突: 遍历 workset 所有 decision==TRANSLATE, 找 terminal KEEP override 与之冲突

11 层 artifacts (存在才报告):
  translation_catalog.csv / translation_overrides.csv / translation_overrides.final2.csv /
  translation_done.csv / gap_inventory.csv / translation_missing_result.csv /
  translation_delta_catalog.csv / translation_manual_review.csv / translation_final_todo.csv /
  translation_incremental_workset.csv / translation_batch_manifest.csv
"""
import sys, csv, os
from pathlib import Path

LAYERS = [
    "translation_catalog.csv", "translation_overrides.csv", "translation_overrides.final2.csv",
    "translation_done.csv", "gap_inventory.csv", "translation_missing_result.csv",
    "translation_delta_catalog.csv", "translation_manual_review.csv", "translation_final_todo.csv",
    "translation_incremental_workset.csv", "translation_batch_manifest.csv",
]
FIELDS = ["translation_id", "source_text", "decision", "status", "translation",
          "reason", "final_decision", "provenance", "action"]
KEY_COLS = ["translation_id"]


def load(path):
    """返回 {tid: [row...]}  (一个 tid 可能在同层出现多次, 保留全部)。"""
    p = Path(path)
    if not p.exists():
        return None
    out = {}
    with open(p, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            tid = (r.get("translation_id") or "").strip()
            if not tid:
                continue
            out.setdefault(tid, []).append(r)
    return out


def pick(row):
    """压缩该行为的展示字段。"""
    seen = {}
    for k in FIELDS:
        v = row.get(k)
        if v is not None and str(v).strip():
            seen[k] = str(v).strip()
    return seen


def trace_one(tid, dir_):
    print("=" * 78)
    print(f"TRACE  tid = {tid}")
    found_any = False
    for name in LAYERS:
        idx = load(os.path.join(dir_, name))
        if idx is None:
            print(f"\n  [{name}]  <文件不存在>")
            continue
        rows = idx.get(tid)
        print(f"\n  [{name}]  exists={bool(rows)}")
        if not rows:
            continue
        found_any = True
        for r in rows:
            d = pick(r)
            print(f"      {d}")
    if not found_any:
        print("\n  (任何已存在层中均未找到该 tid)")
    print("=" * 78)


def full_conflict_audit(dir_):
    """workset(decision==TRANSLATE) ∩ terminal KEEP override => 冲突 tid 全量。"""
    print("\n" + "=" * 78)
    print("FULL CONFLICT AUDIT: authoritative-TRANSLATE workset ∩ terminal-KEEP override")
    workset = load(os.path.join(dir_, "translation_incremental_workset.csv"))
    if workset is None:
        print("  [!] translation_incremental_workset.csv 不存在, 无法全量审计")
        return
    ovr_idx = load(os.path.join(dir_, "translation_overrides.csv"))
    if ovr_idx is None:
        # 退而求其次: final2
        ovr_idx = load(os.path.join(dir_, "translation_overrides.final2.csv"))
        print("  [note] translation_overrides.csv 不存在, 用 final2 代替")
    if ovr_idx is None:
        print("  [!] 无任何 override 文件, 冲突=0")
        return

    # 收集 terminal KEEP overrides (按 tid; 一个 tid 的 override 需 KEEP 且无更新层 TRANSLATE 推翻)
    keep_tids = {}
    for tid, rows in ovr_idx.items():
        act = (rows[-1].get("action") or "").strip().upper()  # 取该层最后一次
        if act == "KEEP":
            keep_tids[tid] = True

    conflicts = []
    for tid, rows in workset.items():
        in_ws_translate = any(
            (r.get("decision") or "").strip() == "TRANSLATE" for r in rows)
        if in_ws_translate and tid in keep_tids:
            conflicts.append(tid)

    conf_src = {}
    for tid in conflicts:
        for r in workset[tid]:
            conf_src[tid] = (r.get("source_text") or "", r.get("provenance") or "")
    print(f"\n  workset TRANSLATE 总数          = {len(workset)}")
    print(f"  terminal KEEP override tid 数    = {len(keep_tids)}")
    print(f"  冲突 tid 数                       = {len(conflicts)}")
    if conflicts:
        print("\n  -- conflict tid 全量 --")
        for tid in sorted(conflicts):
            src, prov = conf_src[tid]
            print(f"    {tid}  src={src!r}  provenance={prov}")
        print("\n  [结论] terminal KEEP override 必须从 workset 排除, 否则 Phase2B 会 POLICY-CONFLICT。")
    else:
        print("  [结论] 无冲突。")
    return conflicts


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    dir_ = sys.argv[1]
    tids = sys.argv[2:]
    for t in tids:
        trace_one(t, dir_)
    if tids:
        print("\n  (仅追踪指定 tid; 如需全量冲突审计, 省略 tid 参数)")
    else:
        full_conflict_audit(dir_)
    print("\n  [read-only] 未写任何文件, 未调模型, 未生成 sidecar。")


if __name__ == "__main__":
    main()
