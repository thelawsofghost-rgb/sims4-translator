#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
old_resolved_source_audit.py — 历史 CATALOG 决策 × 旧 final 译文来源审计 (只读)
=================================================================================
背景: run2 preflight 曾因把 translation_catalog.csv 的空 translation 拿去与 production
中文终稿做 translation-equality 冲突而误报。本脚本按要求先查清:
  * translation_catalog.csv 的 rows/status 分布, catalog TRANSLATE 计数
  * 历史 final translation source 到底存在哪 (不把 decision catalog 冒充 final)
  * 用 稳定 translation_id + normalized source_text 做 join, 输出:
        CATALOG_TRANSLATE_RESOLVED / CATALOG_TRANSLATE_MISSING / CATALOG_KEEP

权威口径 (与 gap_inventory.py 一致):
  * translation_catalog.csv  = decision/index ONLY (KEEP / TRANSLATE / REVIEW / APPROVED)
      - KEEP      -> 若无更高层 override, 终态 KEEP
      - TRANSLATE -> 仅说明该 source 需翻译; catalog 本身不提供 translation payload,
                     必须去 historical final translation source 找已完成译文
      - REVIEW    -> 若无更高人工终态覆盖, 则 unresolved
      - catalog 的空 translation 绝不与 final 中文译文做 equality 冲突
  * 历史 final translation source = overrides + overrides2(final2) + done:
      非空 translation 才计入 "最终译文存在" (与 gap_inventory.load_final_translations 同源同口径)
  * translation_cache.db 禁止作为 authoritative final source (phrase cache, 非 final QA artifact)

只读: 不改任何输入, 不建 package/sidecar, 不改 writer, 不生成侧车。

用法 (Windows, 先 cd 仓库根):
  python scripts\\old_resolved_source_audit.py \\
      --catalog output\\translation_catalog.csv \\
      --overrides output\\translation_overrides.csv \\
      --overrides2 output\\translation_overrides.final2.csv   (可选) \\
      --done output\\translation_done.csv \\
      [--list output\\coverage_eligible.txt]                  (可选: ELIGIBLE 包路径列表 \\
                                                              省略则仅报告 catalog 级 join, 不跑 package scan)

输出: 终端报告 + --out 可选写明细 CSV (默认 output/old_resolved_source_audit.csv)
"""
import sys, os, csv, argparse
from pathlib import Path
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))
from phase2a_catalog import source_hash, norm_text, make_translation_id
import gen_cohort_sidecars as G   # 复用 approved_pv_refs (生产同源审计)

# 被禁止作为 final translation source 的文件 (phrase cache 等)
BANNED_FINAL = {"translation_cache.db", "translation_cache.sqlite", "cache.db"}

_CAT_DECISIONS = {"KEEP", "TRANSLATE", "APPROVED", "REVIEW"}


def stable_id(src: str) -> str:
    return make_translation_id(source_hash(norm_text(src)), 1)


def load_catalog(path) -> dict:
    """frozen 权威 catalog -> {tid: (norm_source, decision, translation)}"""
    cat = {}
    rows_total = 0
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows_total += 1
            tid = (r.get("translation_id") or "").strip()
            stxt = (r.get("source_text") or "").strip()
            dec = (r.get("decision") or "").strip().upper()
            tr = (r.get("translation") or "").strip()
            if not tid:
                continue
            # 同 tid 重复: 保留首个, 记录重复
            if tid in cat:
                continue
            cat[tid] = (norm_text(stxt) if stxt else "", dec, tr)
    dec_count = Counter(v[1] for v in cat.values())
    return cat, rows_total, dec_count


def load_final_translations(paths) -> dict:
    """overrides(+final2)+done -> {(tid, norm_source): translation}。
    仅非空 translation 计入; cache.db 绝不允许进入。"""
    final = {}
    src_rows = {}
    for p in paths:
        if not p or not Path(p).exists():
            continue
        base = Path(p).name
        if base in BANNED_FINAL:
            print(f"[WARN] 拒绝把 {p} 作为 final translation source (被禁止)")
            continue
        with open(p, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                src_rows[base] = src_rows.get(base, 0) + 1
                tid = (r.get("translation_id") or "").strip()
                stxt = (r.get("source_text") or "").strip()
                tr = (r.get("translation") or "").strip()
                if not tid or not stxt or not tr:
                    continue
                final[(tid, norm_text(stxt))] = tr  # 后到非覆盖语义: 只判"存在最终译文"
    return final, src_rows


def classify(tid, norm_src, dec, in_final):
    if dec == "KEEP":
        return "A", "CATALOG_KEEP"
    if dec in ("TRANSLATE", "APPROVED"):
        return ("B", "CATALOG_TRANSLATE_RESOLVED") if in_final \
            else ("C", "CATALOG_TRANSLATE_MISSING")
    # REVIEW 及其它
    return ("B", f"CATALOG_TRANSLATE_RESOLVED(dec={dec})") if in_final \
        else ("C", f"CATALOG_TRANSLATE_MISSING(dec={dec})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--overrides", required=True, help="final translation source 1")
    ap.add_argument("--overrides2", default="", help="final translation source 2 (final2)")
    ap.add_argument("--done", required=True, help="final translation source 3")
    ap.add_argument("--list", default="", help="可选 ELIGIBLE 包路径列表 (一行一个)")
    ap.add_argument("--out", default="output/old_resolved_source_audit.csv")
    a = ap.parse_args()

    if not Path(a.catalog).exists():
        print(f"[HARD-FAIL] catalog 不存在: {a.catalog}"); return 2

    cat, cat_rows, cat_dec = load_catalog(a.catalog)
    final, final_rows = load_final_translations([a.overrides, a.overrides2, a.done])

    out = []
    out.append("=" * 70)
    out.append("历史 CATALOG 决策 × 旧 final 译文来源 审计 (只读)")
    out.append("=" * 70)
    out.append(f"[catalog] {a.catalog}")
    out.append(f"  rows={cat_rows}  unique_tid={len(cat)}")
    out.append(f"  decision 分布: " +
               "  ".join(f"{k}={v}" for k, v in sorted(cat_dec.items())))
    out.append(f"  catalog TRANSLATE (含 APPROVED) 计数 = "
               f"{cat_dec.get('TRANSLATE',0) + cat_dec.get('APPROVED',0)}")
    out.append(f"[final sources] 旧最终译文来源 (仅非空 translation 计入, cache.db 禁止):")
    for base, n in sorted(final_rows.items()):
        out.append(f"  {base:<40} {n} 数据行")
    out.append(f"  合并 unique (tid,norm_source) 最终译文 = {len(final)}")
    out.append("-" * 70)

    # ---- package scan (可选) ----
    approved_rows = []
    pkg_by_source = defaultdict(set)
    prov_by_source = defaultdict(set)
    n_pkg_ok = 0
    errors = []
    if a.list and Path(a.list).exists():
        pkgs = [l.strip() for l in open(a.list, encoding="utf-8-sig") if l.strip()]
        out.append(f"[scan] 从 --list 读入 {len(pkgs)} 包")
        for p in pkgs:
            try:
                tgi, key_map, approved, errs = G.approved_pv_refs(p)
            except Exception as ex:
                errors.append(f"{Path(p).name}: 映射异常 {ex}"); continue
            if errs:
                errors.append(f"{Path(p).name}: {'; '.join(errs)}"); continue
            n_pkg_ok += 1
            for cat_, kh, src in approved:
                key = norm_text(src)
                pkg_by_source[key].add(Path(p).name)
                prov_by_source[key].add(cat_)
                approved_rows.append((key, cat_, src))
        out.append(f"[scan] 解析成功 {n_pkg_ok}/{len(pkgs)} 包; 异常 {len(errors)}")
    elif a.list:
        out.append(f"[WARN] --list 文件不存在: {a.list} (跳过 package scan)")

    # 唯一 source 集合: 若 scan 跑过用 scan 的, 否则用 catalog 的 source_text
    uniq = {}
    if approved_rows:
        for key, prov, src in approved_rows:
            e = uniq.setdefault(key, {"src": src, "provs": set(), "pkgs": set()})
            e["provs"].add(prov)
            e["pkgs"] |= pkg_by_source[key]
    else:
        # 无 package scan: 用 catalog 全量 source_text
        seen_tids = set()
        for tid, (nsrc, dec, tr) in cat.items():
            if tid in seen_tids or not nsrc:
                continue
            seen_tids.add(tid)
            uniq.setdefault(nsrc, {"src": nsrc, "provs": {dec}, "pkgs": set()})
        out.append("[scan] 未提供 --list, 以 catalog 全部 source_text 作为审计全集")

    # ---- 分类 join ----
    stats = Counter()
    prov_stats = Counter()
    pkg_impact = defaultdict(lambda: defaultdict(set))
    out_rows = []
    for key, e in sorted(uniq.items()):
        tid = stable_id(e["src"])
        if tid not in cat:
            cls, detail = "D", "NEW_SOURCE_NOT_IN_CATALOG"
        else:
            dec = cat[tid][1]
            in_final = (tid, key) in final
            cls, detail = classify(tid, key, dec, in_final)
        stats[cls] += 1
        for prov in (e["provs"] or {cat[tid][1] if tid in cat else "?"}):
            prov_stats[(cls, prov)] += 1
            pkg_impact[cls][prov].update(e["pkgs"]) if e["pkgs"] else None
        out_rows.append({
            "class": cls, "detail": detail,
            "translation_id": tid, "source_text": e["src"],
            "source_hash": source_hash(norm_text(e["src"])),
            "provenance": "|".join(sorted(e["provs"])) if e["provs"] else cat.get(tid, ("", "", ""))[1],
            "package_count": len(e["pkgs"]),
            "packages": "|".join(sorted(e["pkgs"])) if e["pkgs"] else "",
        })

    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()) if out_rows else
                           ["class","detail","translation_id","source_text","source_hash",
                            "provenance","package_count","packages"])
        w.writeheader()
        for r in out_rows:
            w.writerow(r)

    # ---- 汇总 ----
    total = len(uniq)
    out.append("=" * 70)
    out.append(f"unique source 审计总数: {total}")
    out.append(f"  A CATALOG_KEEP:                      {stats['A']}")
    out.append(f"  B CATALOG_TRANSLATE_RESOLVED:        {stats['B']}")
    out.append(f"  C CATALOG_TRANSLATE_MISSING:         {stats['C']}   <- 异常(要求翻译但无最终译文)")
    out.append(f"  D NEW_SOURCE_NOT_IN_CATALOG:         {stats['D']}")
    out.append("-" * 70)
    out.append("各 (class, provenance) 数量:")
    for cls in ("A", "B", "C", "D"):
        for prov in sorted({p for (c, p) in prov_stats if c == cls}):
            out.append(f"  {cls}  {prov:<22} {prov_stats[(cls, prov)]}")
    out.append("-" * 70)
    out.append("各类别影响 package (去重) 数:")
    for cls in ("A", "B", "C", "D"):
        tot = set()
        for prov in pkg_impact[cls]:
            tot |= pkg_impact[cls][prov]
        out.append(f"  {cls}: {len(tot)} 包  |  " +
                   ", ".join(f"{p}={len(pkg_impact[cls][p])}" for p in pkg_impact[cls]))
    out.append("-" * 70)
    out.append("join 口径: stable translation_id + normalized source_text; "
               "最终译文 = overrides/final2/done 非空 translation; catalog.translation 不作为 final;"
               " cache.db 已排除。")
    out.append(f"[out] {a.out}  ({len(out_rows)} unique source)")
    if errors:
        out.append(f"[注意] 包级异常 {len(errors)} 条 (首5):")
        for e in errors[:5]:
            out.append("   " + e)

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
