#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
run2_unresolved_dump.py —— 只读 gap dump: 真实 run2 10 包的 unresolved 明细
==================================================================================
冻结 resolver 架构 (五源), 不改 resolver/writer, 不生成 sidecar, 不对 Mods 做任何写。

本脚本唯一产物: 输出 CSV (occurrence-level) + 可选 unique-level CSV。除此之外零写。

用途: 人工审阅 29 条真实 unresolved occurrence 的 source_text。对其是否纯
"无语义编号/技术标签" (1, 2, 3, Pose 1, Pose 2 ...) 做客观只读分类
(pure integer / Pose+integer / other)。分类仅报告, 不自动裁决 KEEP —— 是否建立
独立 run2_unresolved_keep.c26.csv terminal KEEP layer 由人工决定, 绝不塞回旧
pose C26 KEEP (provenance 单独为 RUN2_TECHNICAL_LABEL_KEEP)。

行语义 (与 gen_cohort_sidecars.resolve_all_approved 完全一致):
  unresolved tag = MISSING | MISSING_REVIEW   (tr is None 且非 SOURCE_MISMATCH / 非 KEEP)

translation_id 基于 normalized source 的全局稳定 ID。因此同一个技术标签 (例如
"Pose 1") 跨 package / entry 重复时, 本来就应共享同一个 (tid, norm_source)。
occurrence-level 保留每个未解决 occurrence 一行 (共 29), decision-level 用
unique(tid, norm_source) 去重 (真实值, 可能 24 < 29)。

--- occurrence-level gate (必须成立, 否则 HARD-FAIL) ---
  rows == 29
  package occurrence counts: AlluringSims=8 / Gentlemanly Elegance=5 / Tibo131=16

--- decision-level (报告真实值, 不强制 =29) ---
  unique(tid, norm_source) = 实际值
  对重复组做 fail-closed consistency:
    - 同一个 tid 若对应不同 normalized source  -> HARD-FAIL
    - 同一个 (tid, norm_source) 若在不同 occurrence 得到不同 resolver outcome
      -> HARD-FAIL
    - 同一 (tid, norm_source) 的 role 必须兼容 (同为 POSE_DISPLAY_NAME, 或
      同是 title/desc 类), 否则 HARD-FAIL

--- 输出 ---
  occurrence CSV (默认 --out, 例 run2_unresolved_29.csv): 29 行, 每行一个 occurrence。
  unique CSV   (--out-unique, 例 run2_unresolved_unique_24.csv): 每个
    (tid, norm_source) 一行, 供 Dorothy 人工裁决。
  duplicate occurrence report: 打印到 stdout (translation_id / source_text /
    occurrence_count / package slots+names / role / key_hashes)。

--- 每行字段 (occurrence) ---
  package_slot, package_path, role, key_hash, translation_id, source_text,
  catalog_decision, catalog_presence, done_presence, overlay_presence,
  title_final_presence, desc_final_presence, resolver_tag

--- 每行字段 (unique) ---
  translation_id, source_text, role(s), occurrence_count, packages,
  catalog_decision, catalog_presence, done_presence, overlay_presence,
  title_final_presence, desc_final_presence, resolver_tag

用法:
  python scripts/run2_unresolved_dump.py \
      --cohort output\cohort_selection.csv \
      --title-final output\translation_done_title_final.csv \
      --desc-final  output\translation_done_desc_final.csv \
      --production-overlay output\translation_overrides.production.csv \
      --done output\translation_done.csv \
      --catalog output\translation_catalog.csv \
      --out output\run2_unresolved_29.csv \
      --out-unique output\run2_unresolved_unique_24.csv
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

_UNIQUE_COLS = [
    "translation_id", "source_text", "role(s)", "occurrence_count", "packages",
    "catalog_decision", "catalog_presence", "done_presence", "overlay_presence",
    "title_final_presence", "desc_final_presence", "resolver_tag",
]

# 同一 (tid, norm_source) 下, 兼容的 role 分组 (title/desc 视为"文本类", pose 自身一组)
_ROLE_GROUP = {"PACK_TITLE": "text", "PACK_DESCRIPTION": "text", "POSE_DISPLAY_NAME": "pose"}


def classify(src):
    """只读客观分类, 不裁决。返回 (bucket, detail)。"""
    s = src.strip()
    if re.fullmatch(r"\d+", s):
        return "pure integer", s
    # Pose N / PoseN / Pose N-N / Pose N-M ... 均为无关语义的技术编号 -> Pose+integer
    m = re.fullmatch(r"(?i)pose[\s_\-]*(\d+)(?:[\s_\-]+\d+)*", s)
    if m:
        return "Pose+integer", s
    return "other", s


def _write_csv(path, rows, cols):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", required=True)
    ap.add_argument("--title-final", required=True)
    ap.add_argument("--desc-final", required=True)
    ap.add_argument("--production-overlay", required=True)
    ap.add_argument("--done", required=True)
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--out", required=True, help="occurrence-level CSV (29 行)")
    ap.add_argument("--out-unique", default="", help="unique-level CSV (每 tid 一行), 可省略")
    a = ap.parse_args()

    for p, lab in [(a.cohort, "cohort"), (a.title_final, "title_final"),
                   (a.desc_final, "desc_final"), (a.production_overlay, "production_overlay"),
                   (a.done, "done"), (a.catalog, "catalog")]:
        if not Path(p).exists():
            print(f"[HARD-FAIL] {lab} 不存在: {p}")
            return 3

    resolver = make_production_resolver(
        a.title_final, a.desc_final, a.production_overlay,
        translation_done=a.done, translation_catalog=a.catalog)
    print(f"[resolver] overlay={len(resolver.overlay)} title={len(resolver.title)} "
          f"desc={len(resolver.desc)} done={len(resolver.done)} catalog={len(resolver.catalog)} "
          f"consistency_errors={len(resolver.consistency_errors)}")

    presence = {
        "catalog": set(resolver.catalog.keys()),
        "done": set(resolver.done.keys()),
        "overlay": set(resolver.overlay.keys()) if resolver.overlay else set(),
        "title_final": set(resolver.title.keys()),
        "desc_final": set(resolver.desc.keys()),
    }
    catalog_decision = {k: v["status"] for k, v in resolver.catalog.items()}

    rows = []
    with open(a.cohort, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    rows = [r for r in rows if (r.get("status", "").strip() or "").upper() == "ELIGIBLE_EXISTING_CHS"]

    per_pkg = {}
    role_counts = {"PACK_TITLE": 0, "PACK_DESCRIPTION": 0, "POSE_DISPLAY_NAME": 0}
    cls = {"pure integer": 0, "Pose+integer": 0, "other": 0}
    other_samples = []
    out = []

    for r in rows:
        slot = r.get("cohort_slot", "").strip()
        path = r.get("package_path", "").strip()
        pkg = Path(path).name
        try:
            target_tgi, key_map, approved, errs = approved_pv_refs(path)
        except Exception as ex:
            print(f"[warn] {pkg} mapping 异常: {ex}")
            continue
        if errs:
            print(f"[warn] {pkg} mapping errs: {errs}")
            continue
        for cat, kh, src in approved:
            nsrc = norm_text(src)
            tid = make_translation_id(source_hash(nsrc), 1)
            tr, tag = resolver.resolve(src)
            if tag not in _UNRES_TAGS:
                continue   # 非 unresolved 不 dump
            key = (tid, nsrc)
            row = {
                "package_slot": slot,
                "package_path": path,
                "role": cat,
                "key_hash": f"0x{kh:08X}",
                "translation_id": tid,
                "source_text": src,
                "catalog_decision": catalog_decision.get(key, ""),
                "catalog_presence": "1" if key in presence["catalog"] else "0",
                "done_presence": "1" if key in presence["done"] else "0",
                "overlay_presence": "1" if key in presence["overlay"] else "0",
                "title_final_presence": "1" if key in presence["title_final"] else "0",
                "desc_final_presence": "1" if key in presence["desc_final"] else "0",
                "resolver_tag": tag,
            }
            out.append(row)
            per_pkg[pkg] = per_pkg.get(pkg, 0) + 1
            role_counts[cat] = role_counts.get(cat, 0) + 1
            bucket, _ = classify(src)
            cls[bucket] = cls.get(bucket, 0) + 1
            if bucket == "other":
                other_samples.append((pkg, cat, src))

    # ================= occurrence-level gate =================
    n = len(out)
    print(f"\n[occurrence] rows = {n}")
    print("[occurrence] per-package occurrence counts:")
    for pkg, c in sorted(per_pkg.items(), key=lambda kv: -kv[1]):
        print(f"        {pkg:<34} {c}")
    if n != 29:
        print(f"[HARD-FAIL] occurrence rows 期望 29, 实得 {n}")
        return 2

    # 按包分组断言: AlluringSims=8 / Gentlemanly Elegance=5 / Tibo131=16 (归一化显示名)
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
    print("[occurrence] 按包分组期望 vs 实得:")
    for gk, v in grp_expect.items():
        mark = "OK" if got[gk] == v else "MISMATCH"
        print(f"        {gk:<24} expect={v} got={got[gk]}  {mark}")
    for up, c in unknown.items():
        print(f"        [未经分组匹配的包] {up}: {c}")
    grp_ok = all(got[g] == v for g, v in grp_expect.items()) and not unknown
    if not grp_ok:
        print("[HARD-FAIL] 按包分组数与冻结不符 (包名未匹配需人工校核)")
        return 2

    # ================= decision-level: unique + 重复组一致性 =================
    # key: (tid, norm_source) -> [row dict...]
    groups = {}
    tid_to_nsrc = {}
    for row in out:
        k = (row["translation_id"], norm_text(row["source_text"]))
        groups.setdefault(k, []).append(row)
        prev = tid_to_nsrc.get(row["translation_id"])
        if prev is not None and prev != norm_text(row["source_text"]):
            print(f"[HARD-FAIL] 同一 tid {row['translation_id']} 对应不同 normalized source: "
                  f"{prev!r} vs {norm_text(row['source_text'])!r}")
            return 2
        tid_to_nsrc[row["translation_id"]] = norm_text(row["source_text"])

    nuniq = len(groups)
    dup_extra = n - nuniq
    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"\n[decision] unique unresolved decisions = {nuniq}")
    print(f"[decision] duplicate extra occurrences  = {dup_extra}")
    print(f"[decision] duplicate groups             = {len(dup_groups)}")

    # fail-closed: 同 (tid,norm) 不同 resolver outcome / 不兼容 role -> HARD-FAIL
    for (tid, nsrc), occs in groups.items():
        tags = {o["resolver_tag"] for o in occs}
        if len(tags) > 1:
            print(f"[HARD-FAIL] 同 (tid,norm) {tid} 不同 resolver outcome: {sorted(tags)}")
            return 2
        roles = {_ROLE_GROUP[o["role"]] for o in occs}
        if len(roles) > 1:
            print(f"[HARD-FAIL] 同 (tid,norm) {tid} role 不兼容: {[o['role'] for o in occs]}")
            return 2

    # ================= duplicate occurrence report =================
    if dup_groups:
        print("\n[duplicate occurrence report] (每重复组一行)")
        for (tid, nsrc), occs in sorted(dup_groups.items(), key=lambda kv: -len(kv[1])):
            slots = ",".join(sorted({o["package_slot"] for o in occs}))
            names = ",".join(sorted({Path(o["package_path"]).name for o in occs}))
            khashes = ",".join(sorted({o["key_hash"] for o in occs}))
            roles = ",".join(sorted({o["role"] for o in occs}))
            print(f"  tid={tid}")
            print(f"    source_text        = {nsrc!r}")
            print(f"    occurrence_count   = {len(occs)}")
            print(f"    package slots/names= {slots} : {names}")
            print(f"    role               = {roles}")
            print(f"    key_hashes         = {khashes}")
            print(f"    resolver_tag       = {occs[0]['resolver_tag']}")
    else:
        print("\n[duplicate occurrence report] 无重复组")

    # ================= 只读分类统计 =================
    print(f"\n[分类统计] (只读, 不裁决) — 按 occurrence 计")
    for b in ("pure integer", "Pose+integer", "other"):
        print(f"        {b:<14} = {cls[b]}")
    if other_samples:
        print("        [other 样本, 需人工裁决]")
        for pkg, cat, s in other_samples:
            print(f"          {pkg} | {cat} | {s!r}")

    # ================= unique-level 行 =================
    unique_rows = []
    for (tid, nsrc), occs in sorted(groups.items(), key=lambda kv: kv[0][0]):
        k = (tid, nsrc)
        roles = ",".join(sorted({o["role"] for o in occs}))
        packages = ",".join(sorted({Path(o["package_path"]).name for o in occs}))
        o0 = occs[0]
        unique_rows.append({
            "translation_id": tid,
            "source_text": o0["source_text"],
            "role(s)": roles,
            "occurrence_count": str(len(occs)),
            "packages": packages,
            "catalog_decision": o0["catalog_decision"],
            "catalog_presence": o0["catalog_presence"],
            "done_presence": o0["done_presence"],
            "overlay_presence": o0["overlay_presence"],
            "title_final_presence": o0["title_final_presence"],
            "desc_final_presence": o0["desc_final_presence"],
            "resolver_tag": o0["resolver_tag"],
        })

    # ================= 写 CSV =================
    _write_csv(a.out, sorted(out, key=lambda x: (x["package_slot"], x["role"], x["key_hash"])), _OUT_COLS)
    print(f"\n[out] 已写 {n} 行 (occurrence-level) -> {a.out}")
    if a.out_unique:
        _write_csv(a.out_unique, unique_rows, _UNIQUE_COLS)
        print(f"[out] 已写 {len(unique_rows)} 行 (unique-level) -> {a.out_unique}")

    print("[done] 只读 dump 完成; 未写任何 package/sidecar/Mods, 未做自动裁决。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
