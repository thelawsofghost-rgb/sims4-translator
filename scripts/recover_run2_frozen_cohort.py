#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recover_run2_frozen_cohort.py — 从权威 manifest 恢复 run2 历史 frozen cohort (只读恢复)
=====================================================================================
背景: run2 coverage rerun 触发了 CLI 副作用, 自动重写了 output/cohort_selection.csv,
      覆盖了原本已完成真机/静态验证的历史 10-cohort。原 10 包权威证据仍保存在
      output/cohort_sidecars_run2_retry1/cohort_sidecar_manifest.csv (kritical等都在)。

本工具【新建】 frozen 恢复文件 (默认 output/cohort_selection.run2_frozen.csv):
  * 保留原 slot / 顺序 / source_package / target_TGI
  * 保留 Kritical (slot 7 = _Kritical_BrainwashingMachine1g.package) —— 它是历史验证证据。
    即使后来被裁决为 false-positive, 也不能篡改历史 cohort 记录。
  * 【不覆盖 / 不恢复】当前任何文件 (不写 cohort_selection.csv, 不动 coverage/report)。

只读/恢复纪律:
  * 只从 manifest 读 -> 只写新 frozen 文件; 不改 writer/resolver/translation/retry1 sidecars。
  * 目标已存在时不覆盖 (fail-closed), 用 --force 才重建。
"""
import sys, os, csv
from pathlib import Path
from collections import OrderedDict

_MAN_COLS = ["cohort_slot", "source_package", "output_sidecar", "target_TGI",
             "approved_key_count", "translated_key_count", "keep_key_count",
             "modified_key_count", "writer_verify", "audit_result", "error"]


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest",
                    default="output/cohort_sidecars_run2_retry1/cohort_sidecar_manifest.csv",
                    help="权威 manifest (含原 10 包 slot/order/source_package)")
    ap.add_argument("--out", default="output/cohort_selection.run2_frozen.csv",
                    help="新建 frozen 恢复文件")
    ap.add_argument("--force", action="store_true",
                    help="目标已存在时允许重建 (默认 fail-closed 不覆盖)")
    a = ap.parse_args()

    if not os.path.isfile(a.manifest):
        print(f"[ERROR] manifest 不存在: {a.manifest} (需真实 Windows 产物)")
        return 2

    out = Path(a.out)
    if out.exists() and not a.force:
        print(f"[FAIL-CLOSED] 目标已存在, 不覆盖: {a.out} (用 --force 重建)")
        return 1

    rows = list(csv.DictReader(open(a.manifest, encoding="utf-8-sig")))
    if not rows:
        print("[ERROR] manifest 为空")
        return 2

    # 按 cohort_slot 稳定排序(数字), 保留 manifest 出现顺序作为 tie-break
    def slot_key(r):
        try:
            return int(r.get("cohort_slot", 0))
        except Exception:
            return 0
    rows = list(enumerate(rows))  # (orig_index, row)
    rows.sort(key=lambda t: (slot_key(t[1]), t[0]))
    rows = [r for _, r in rows]

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["cohort_slot", "selection_reason", "package_path", "status",
                    "CHS_target_TGI", "CHS_entry_count", "CHS_unique_key_hash_count",
                    "PosePackInstance_count", "target_STBL_compression_state"])
        for r in rows:
            src = (r.get("source_package") or "").strip()
            slot = r.get("cohort_slot", "")
            tgi = r.get("target_TGI", "")
            reason = f"run2 历史冻结 cohort (authoritative manifest) slot={slot} —— 保留原顺序, 含后续被裁决 false-positive 的包(如Kritical)"
            w.writerow([slot, reason, src, "RUN2_FROZEN", tgi, "", "", "", ""])

    # 报告: 逐 slot 列出恢复内容 + Kritical 是否在 slot7
    print(f"\n[WROTE] {a.out} ({len(rows)} 行, 只读恢复, 不覆盖当前任何文件)")
    print("\n-- 恢复的 run2 frozen roster --")
    kritical_at7 = False
    for r in rows:
        slot = r.get("cohort_slot", ""); src = (r.get("source_package") or "").strip()
        base = os.path.basename(src)
        if str(slot).strip() == "7" and "Kritical" in base:
            kritical_at7 = True
        print(f"  slot {slot:>2}  {base}")
    print(f"\nKritical@slot7 保留 = {kritical_at7} "
          f"({'PASS' if kritical_at7 else 'CHECK: slot7 非 Kritical 或 manifest 不含'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
