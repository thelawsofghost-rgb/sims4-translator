#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_manual_registry.py — 用真实 Windows 包路径计算 SHA256, 冻结 6 个 SKIP 人工裁决
=====================================================================================
输入: 6 个 MANUAL_REVIEW_CONFIRMED SKIP 包的真实路径列表 (每行一个, 应全部存在)。
输出: configs/pose_manual_adjudications.csv (HEADER 已含必需列; 本脚本填充 SHA256 + 裁决字段)。

设计 (用户 2026-08-15 终审):
  * 匹配身份 = package SHA256 (唯一、frozen)。basename/path 只是可读诊断, 永不参与匹配。
  * 本脚本在【真实 Windows 文件】上一次性计算 SHA256 并冻结; 之后任何文件内容更新
    导致 hash 改变 -> 旧裁决不继承 (不命中) -> fail conservative / 重新 review。
  * registry 默认 fail-closed: 已有内容(非纯 HEADER)时拒绝覆盖, --force 才重建。
  * 每个 SKIP 行的语义字段由本脚本统一写入:
        decision=SKIP
        semantic_status=SKIP_FALSE_POSITIVE_INTERNAL_POSE
        decision_basis=MANUAL_REVIEW_CONFIRMED
        evidence_note=<人工复核简注 (可带文件名诊断)>
"""
import os, sys, csv, hashlib
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import manual_adjudication as MA

REG = os.path.join(REPO, "configs", "pose_manual_adjudications.csv")

# 确认的 6 个 SKIP 包 basename (仅诊断显示, 不用作匹配)
CONFIRMED_SKIP = {
    "simkatu_music_listening_interactions.package",
    "simkatu_music_listening_interactions_autonomy_off.package",
    "simkatu_phone_calls_animations_override.package",
    "simkatu_relaxed_tv_watching_override.package",
    "simkatu_nap_on_the_table.package",
    "simkatu_nap_on_the_table_more_energy.package",
}

EV_NOTES = {
    "simkatu_music_listening_interactions.package": "功能交互内部动画容器: 缺PACK_TITLE/DESCRIPTION, 有interaction/action/animation gameplay 资源",
    "simkatu_music_listening_interactions_autonomy_off.package": "同系列 autonomy 关闭变体, 同为功能交互内部动画容器",
    "simkatu_phone_calls_animations_override.package": "通话动画 override 容器, 非独立 Pose Player pack",
    "simkatu_relaxed_tv_watching_override.package": "看 TV 放松动画 override 容器, 非独立 Pose Player pack",
    "simkatu_nap_on_the_table.package": "桌上打盹交互动画容器, 有 gameplay 资源 + 缺 pack 可见信息",
    "simkatu_nap_on_the_table_more_energy.package": "同上 more_energy 变体, 功能交互内部动画容器",
}


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", required=True, help="6 个 SKIP 包真实路径列表 (每行一个)")
    ap.add_argument("--force", action="store_true", help="registry 已有内容时允许覆盖重建")
    a = ap.parse_args()

    if not os.path.isfile(a.list):
        print(f"[ERROR] --list 不存在: {a.list}")
        return 2

    paths = [ln.strip() for ln in open(a.list, encoding="utf-8") if ln.strip()]
    if len(paths) != 6:
        print(f"[ERROR] 需要正好 6 个 SKIP 包路径 (实际 {len(paths)})")
        return 2

    # 定位: 校验路径存在 + basename 属于确认 SKIP 集 (仅诊断)
    rows = []
    for p in paths:
        if not os.path.isfile(p):
            print(f"[ERROR] 包文件不存在: {p}")
            return 2
        base = os.path.basename(p)
        if base not in CONFIRMED_SKIP:
            print(f"[WARN] basename 不在确认 SKIP 集 (仍按 SHA256 冻结, 供诊断): {base}")
        sha = sha256_of(p)
        rows.append({
            "package_sha256": sha,
            "decision": "SKIP",
            "semantic_status": "SKIP_FALSE_POSITIVE_INTERNAL_POSE",
            "decision_basis": "MANUAL_REVIEW_CONFIRMED",
            "evidence_note": EV_NOTES.get(base, "人工复核确认为功能交互内部动画容器"),
            "basename": base,
            "path": p,
        })

    # fail-closed: registry 已有内容 (非纯 HEADER) 拒绝覆盖
    existing = []
    if os.path.isfile(REG):
        with open(REG, encoding="utf-8-sig", newline="") as f:
            rr = list(csv.DictReader(f))
        existing = [r for r in rr if (r.get("package_sha256") or "").strip()]
    if existing and not a.force:
        print(f"[FAIL-CLOSED] registry 已有 {len(existing)} 条, 拒绝覆盖: {REG} (用 --force)")
        return 1

    # 重新计算 6 条 SHA256 去重校验
    shas = [r["package_sha256"] for r in rows]
    if len(set(shas)) != len(shas):
        print("[ERROR] 6 个包 SHA256 存在重复 (同一文件?)")
        return 2

    # 写 registry (header + 6 行)
    Path(REG).parent.mkdir(parents=True, exist_ok=True)
    with open(REG, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=MA.REQ_COLS + MA.OPT_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({**r})

    print(f"[WROTE] {REG}")
    for r in rows:
        print(f"  {r['package_sha256'][:16]}…  SKIP  {r['basename']}")
    print(f"  ({len(rows)} 条人工裁决, 精确 SHA256 身份冻结; basename/path 仅诊断)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
