#!/usr/bin/env python3
"""Phase 2B 写回骨架 (只产出写回计划, 本轮不写任何 .package)。

用户约束 (2026-08-13):
  - 先只执行翻译, 不要立刻写 package。
  - 中文抽查通过后, 才拿 1 个小 package 副本做第一次写回测试。
  - 写回定位必须按 pose_reverse_mapping_full.csv 精确 JOIN:
        package_path / pose_pack_instance / pose entry / STBL resource instance / locale / STBL key
    (packages 列仅审计用, 绝不用它定位写回。)

本脚本只做:
  1) 把 translation_done.csv 按 translation_id 关联 translation_contexts.csv,
     再用 contexts 的引用 JOIN pose_reverse_mapping_full.csv 的精确 6 元组。
  2) 校验: 每条待写回译文都能唯一定位到 (0 或 1) 个真实 package 引用; 定位不到/定位多份 -> 报告。
  3) 输出 写回计划 writeback_plan.csv (含完整 6 元组 + package 文件路径 + 译文),
     以及 writeback_report.txt (统计, 未找到/多命中/冲突)。
  不修改任何 .package。

用法:
  python3 phase2b_writeback.py [output_dir] [--plan] [--strict]
"""
import sys, csv
from pathlib import Path
from collections import Counter, defaultdict

out_dir = Path(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--")
               else "D:/projects/sims4_trans/output")
STRICT = "--strict" in sys.argv
DONE = out_dir / "translation_done.csv"
CTX = out_dir / "translation_contexts.csv"
REV = out_dir / "pose_reverse_mapping_full.csv"
PLAN = out_dir / "writeback_plan.csv"
REPORT = out_dir / "writeback_report.txt"

_JOIN = ("package_path", "pose_pack_instance", "pose_entry_idx",
         "stbl_resource_instance", "locale", "pose_display_name_hash")


def _k(r):
    return tuple((r.get(c) or "").strip() for c in _JOIN)


def main():
    for p, nm in ((DONE, "translation_done.csv"), (CTX, "translation_contexts.csv"),
                  (REV, "pose_reverse_mapping_full.csv")):
        assert p.exists(), f"缺少 {nm}: {p}"

    with open(DONE, encoding="utf-8-sig") as f:
        done = {r["translation_id"]: r for r in csv.DictReader(f)}
    with open(CTX, encoding="utf-8-sig") as f:
        ctx = list(csv.DictReader(f))
    with open(REV, encoding="utf-8-sig") as f:
        rev = list(csv.DictReader(f))

    # reverse mapping 全文索引: 6 元组 -> 引用列表
    rev_index = defaultdict(list)
    for r in rev:
        rev_index[_k(r)].append(r)

    ready = [r for r in done.values() if r.get("status") in ("DONE", "APPROVED")
             and (r.get("translation") or "").strip()]
    print(f"[写回范围] 已译且待写回 = {len(ready)} 条 (status ∈ DONE/APPROVED, 有译文)")

    plan_rows = []
    unresolved = []
    multi = []
    for r in ready:
        tid = r["translation_id"]
        crows = [c for c in ctx if c["translation_id"] == tid]
        if not crows:
            unresolved.append((tid, r["source_text"], "无 context 行"))
            continue
        for c in crows:
            # 写回定位 = reverse mapping 精确 6 元组 (package/pack/entry/stbl/locale/stbl_key)
            # context 里 stbl_key_ref == pose_display_name_hash (即 STBL key)
            refs = rev_index[(c.get("package_path") or "").strip(),
                             (c.get("pose_pack_instance") or "").strip(),
                             (c.get("pose_entry_idx") or "").strip(),
                             (c.get("stbl_resource_instance") or "").strip(),
                             (c.get("locale") or "").strip(),
                             (c.get("stbl_key_ref") or "").strip()]
            if len(refs) == 1:
                plan_rows.append({
                    "translation_id": tid,
                    "source_text": r["source_text"],
                    "translation": r["translation"],
                    "translate_mode": r["translate_mode"],
                    "status": r["status"],
                    "package_path": c["package_path"],
                    "pose_pack_instance": c["pose_pack_instance"],
                    "pose_entry_idx": c["pose_entry_idx"],
                    "stbl_resource_instance": c["stbl_resource_instance"],
                    "locale": c["locale"],
                    "stbl_key": c["stbl_key_ref"],
                })
            elif len(refs) == 0:
                unresolved.append((tid, r["source_text"], "reverse mapping 无此 6 元组"))
            else:
                multi.append((tid, r["source_text"], len(refs)))

    with open(PLAN, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(plan_rows[0].keys()) if plan_rows else
                           ["translation_id", "source_text", "translation", "translate_mode", "status",
                            "package_path", "pose_pack_instance", "pose_entry_idx",
                            "stbl_resource_instance", "locale", "stbl_key"])
        w.writeheader(); w.writerows(plan_rows)

    lines = []
    lines.append(f"写回计划: {len(plan_rows)} 条可唯一定位写回")
    lines.append(f"未解析 (无 context/无 reverse 引用): {len(unresolved)}")
    lines.append(f"多命中 (同一 6 元组 >1 引用, 需人工): {len(multi)}")
    lines.append("")
    for u in unresolved[:30]:
        lines.append(f"  UNRESOLVED {u[0]}  {u[1]!r}  {u[2]}")
    for m in multi[:20]:
        lines.append(f"  MULTI {m[0]}  {m[1]!r}  refs={m[2]}")
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print(f"\n[写出] {PLAN}  {REPORT}")


if __name__ == "__main__":
    main()
