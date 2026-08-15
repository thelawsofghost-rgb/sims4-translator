#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run2_unresolved_dump.py —— 只读 gap dump: 真实 run2 10 包的 unresolved 明细
==================================================================================
冻结 resolver 架构 (五源), 不改 resolver/writer, 不生成 sidecar, 不对 Mods 做任何写。

本脚本唯一产物: 输出 CSV (例: output/run2_unresolved_29.csv)。除此之外零写。

用途: 人工审阅 29 条真实 unresolved source_text。对其是否纯"无语义编号/技术标签"
(1, 2, 3, Pose 1, Pose 2 ...) 做客观只读分类 (pure integer / Pose+integer / other)。
分类仅报告, 不自动裁决 KEEP —— 是否建立独立 run2_unresolved_keep.c26.csv
terminal KEEP layer 由人工决定, 绝不塞回旧 pose C26 KEEP (provenance 单独为
RUN2_TECHNICAL_LABEL_KEEP)。

行语义 (与 gen_cohort_sidecars.resolve_all_approved 完全一致):
  unresolved tag = MISSING | MISSING_REVIEW   (tr is None 且非 SOURCE_MISMATCH / 非 KEEP)

每行字段:
  package_slot       cohort 槽位
  package_path       源 .package 绝对路径
  role               PACK_TITLE | PACK_DESCRIPTION | POSE_DISPLAY_NAME
  key_hash           0x%08X
  translation_id     stable_id
  source_text        原始 source (人工审阅对象)
  catalog_decision   仅当 catalog 命中时其 status (KEEP/TRANSLATE/REVIEW/...), 否则空
  catalog_presence   1/0  (tid,norm_source) 是否在对应源 dict
  done_presence      "
  overlay_presence   "
  title_final_presence "
  desc_final_presence  "
  resolver_tag       本行 resolve() 返回的 tag (MISSING / MISSING_REVIEW / ...)

约束校验:
  rows == 29; unique(tid,norm_source) == 29; 按 3 个失败包分组:
    AlluringSims = 8 / Gentlemanly Elegance = 5 / Tibo131 = 16
  (包名取 package_path 中匹配前三个字的显示名; 若实际包名不同, 以 cohort 实际包名分组)

用法:
  python scripts/run2_unresolved_dump.py \
      --cohort output\cohort_selection.csv \
      --title-final output\translation_done_title_final.csv \
      --desc-final  output\translation_done_desc_final.csv \
      --production-overlay output\translation_overrides.production.csv \
      --done output\translation_done.csv \
      --catalog output\translation_catalog.csv \
      --out output\run2_unresolved_29.csv
"""
import sys, os, csv, re, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from production_resolver import make_production_resolver
from phase2a_catalog import source_hash, make_translation_id, norm_text
from gen_cohort_sidecars import approved_pv_refs

_UNRES_TAGS = {"MISSING", "MISSING_REVIEW"}   # 与 resolve_all_approved 的 "缺译文/unresolved" 一致

_OUT_COLS = [
    "package_slot", "package_path", "role", "key_hash", "translation_id",
    "source_text", "catalog_decision", "catalog_presence", "done_presence",
    "overlay_presence", "title_final_presence", "desc_final_presence",
    "resolver_tag",
]


def classify(src):
    """只读客观分类, 不裁决。返回 (bucket, detail)。"""
    s = src.strip()
    if re.fullmatch(r"\d+", s):
        return "pure integer", s
    # Pose N / PoseN / Pose N-N / Pose N-M ... 均为无关语义的技术编号 → Pose+integer
    m = re.fullmatch(r"(?i)pose[\s_\-]*(\d+)(?:[\s_\-]+\d+)*", s)
    if m:
        return "Pose+integer", s
    return "other", s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", required=True)
    ap.add_argument("--title-final", required=True)
    ap.add_argument("--desc-final", required=True)
    ap.add_argument("--production-overlay", required=True)
    ap.add_argument("--done", required=True)
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    for p, lab in [(a.cohort, "cohort"), (a.title_final, "title_final"),
                   (a.desc_final, "desc_final"), (a.production_overlay, "production_overlay"),
                   (a.done, "done"), (a.catalog, "catalog")]:
        if not Path(p).exists():
            print(f"[HARD-FAIL] {lab} 不存在: {p}"); return 3

    resolver = make_production_resolver(
        a.title_final, a.desc_final, a.production_overlay,
        translation_done=a.done, translation_catalog=a.catalog)
    print(f"[resolver] overlay={len(resolver.overlay)} title={len(resolver.title)} "
          f"desc={len(resolver.desc)} done={len(resolver.done)} catalog={len(resolver.catalog)} "
          f"consistency_errors={len(resolver.consistency_errors)}")

    # presence sets 预计算 (用 (tid,norm_source) key 对齐 resolver dict)
    presence = {
        "catalog_presence": set(resolver.catalog.keys()) or set(),
        "done_presence": set(resolver.done.keys()),
        "overlay_presence": set(resolver.overlay.keys()) if resolver.overlay else set(),
        "title_final_presence": set(resolver.title.keys()),
        "desc_final_presence": set(resolver.desc.keys()),
    }
    catalog_decision = {k: v["status"] for k, v in resolver.catalog.items()}

    rows = []
    with open(a.cohort, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    rows = [r for r in rows if (r.get("status", "").strip() or "").upper() == "ELIGIBLE_EXISTING_CHS"]

    out = []
    per_pkg = {}
    role_counts = {"PACK_TITLE": 0, "PACK_DESCRIPTION": 0, "POSE_DISPLAY_NAME": 0}
    cls = {"pure integer": 0, "Pose+integer": 0, "other": 0}
    other_samples = []

    for r in rows:
        slot = r.get("cohort_slot", "").strip()
        path = r.get("package_path", "").strip()
        pkg = Path(path).name
        try:
            target_tgi, key_map, approved, errs = approved_pv_refs(path)
        except Exception as ex:
            print(f"[warn] {pkg} mapping 异常: {ex}"); continue
        if errs:
            print(f"[warn] {pkg} mapping errs: {errs}"); continue
        for cat, kh, src in approved:
            tid = make_translation_id(source_hash(norm_text(src)), 1)
            tr, tag = resolver.resolve(src)
            if tag not in _UNRES_TAGS:
                continue   # 非 unresolved 不 dump (translate/keep/mismatch 皆剔除)
            key = (tid, norm_text(src))
            row = {
                "package_slot": slot,
                "package_path": path,
                "role": cat,
                "key_hash": f"0x{kh:08X}",
                "translation_id": tid,
                "source_text": src,
                "catalog_decision": catalog_decision.get(key, ""),
                "catalog_presence": "1" if key in presence["catalog_presence"] else "0",
                "done_presence": "1" if key in presence["done_presence"] else "0",
                "overlay_presence": "1" if key in presence["overlay_presence"] else "0",
                "title_final_presence": "1" if key in presence["title_final_presence"] else "0",
                "desc_final_presence": "1" if key in presence["desc_final_presence"] else "0",
                "resolver_tag": tag,
            }
            out.append(row)
            per_pkg.setdefault(pkg, 0)
            per_pkg[pkg] += 1
            role_counts[cat] = role_counts.get(cat, 0) + 1
            bucket, _ = classify(src)
            cls[bucket] = cls.get(bucket, 0) + 1
            if bucket == "other":
                other_samples.append((pkg, cat, src))

    # ---- 约束校验 ----
    n = len(out)
    nuniq = len({(r["translation_id"], norm_text(r["source_text"])) for r in out})
    print(f"\n[dump] 行数 = {n} | unique(tid,norm_source) = {nuniq}")
    print(f"[dump] per-package 分组:")
    for pkg, c in sorted(per_pkg.items(), key=lambda kv: -kv[1]):
        print(f"        {pkg:<34} {c}")
    if n != 29 or nuniq != 29:
        print(f"[HARD-FAIL] 期望 rows=29 unique=29, 实得 {n}/{nuniq}"); return 2

    # 分组断言 (按用户冻结: AlluringSims=8 / Gentlemanly Elegance=5 / Tibo131=16)
    grp_expect = {"alluringsims": 8, "gentlemanlyelegance": 5, "tibo131": 16}
    norm_key = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    got = {k: 0 for k in grp_expect}
    unknown = {}
    for r in out:
        ln = norm_key(r["package_path"])
        matched = False
        for gk in grp_expect:
            if gk in ln:
                got[gk] += 1
                matched = True
                break
        if not matched:
            unknown[r["package_path"]] = unknown.get(r["package_path"], 0) + 1
    print("[dump] 分组期望 vs 实得:")
    for gk, v in grp_expect.items():
        mark = "OK" if got[gk] == v else "MISMATCH"
        print(f"        {gk:<24} expect={v} got={got[gk]}  {mark}")
    for up, c in unknown.items():
        print(f"        [未经分组匹配的包] {up}: {c}")
    grp_ok = all(got[g] == v for g, v in grp_expect.items()) and not unknown
    if not grp_ok:
        print("[HARD-FAIL] 按包分组数与冻结不符 (包名未匹配需人工校核)"); return 2

    # ---- 只读分类统计 ----
    print(f"\n[分类统计] (只读, 不裁决)")
    print(f"        pure integer   = {cls['pure integer']}")
    print(f"        Pose+integer   = {cls['Pose+integer']}")
    print(f"        other          = {cls['other']}")
    if other_samples:
        print("        [other 样本, 需人工裁决]")
        for pkg, cat, s in other_samples:
            print(f"          {pkg} | {cat} | {s!r}")

    # ---- 写 CSV (唯一产物) ----
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_OUT_COLS)
        w.writeheader()
        for r in sorted(out, key=lambda x: (x["package_slot"], x["role"], x["key_hash"])):
            w.writerow(r)
    print(f"\n[out] 已写 {n} 行 -> {a.out}")
    print("[done] 只读 dump 完成; 未写任何 package/sidecar/Mods, 未做自动裁决。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
