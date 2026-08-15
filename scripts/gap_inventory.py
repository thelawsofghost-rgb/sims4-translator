#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gap_inventory.py — 只读 frozen decision-catalog gap inventory (Phase 3B2)
==========================================================================
对最终 ELIGIBLE 包的 approved resolved player-visible unique keys, 全量 join 到
authoritative frozen catalog (translation_catalog.csv), 按 unique source text 输出:

  A. CATALOG_KEEP                      在 frozen catalog, decision=KEEP
  B. CATALOG_TRANSLATE_RESOLVED        在 catalog, decision in {TRANSLATE, APPROVED},
                                       且 override/final translation_done 存在最终译文
  C. CATALOG_TRANSLATE_MISSING_RESULT  catalog 要求 TRANSLATE/APPROVED,
                                       但最终 override/done 无译文  -> 异常, 单独列出
  D. NEW_SOURCE_NOT_IN_CATALOG         stable_id/source_text 不在旧 catalog
                                       再按 provenance 分: PACK_TITLE / PACK_DESCRIPTION / POSE_DISPLAY_NAME

只读: 不建 sidecar, 不改 frozen coverage/cohort/writer, 不改任何 .package。
translation_cache.db 不作为最终译文 fallback (phrase cache, 非 final QA artifact)。

输入 (Windows, 短相对路径, 先 cd 仓库根):
  --list <file>   明文 .package 路径列表 (一行一个); 省略则读 output/coverage.csv 取
                  status == ELIGIBLE_EXISTING_CHS 的全部包路径 (= 448 类全集)
  --catalog       输出 output/translation_catalog.csv      [必填, frozen 权威 catalog]
  --overrides     输出 output/translation_overrides.csv    [最终译文来源1]
  --overrides2    输出 output/translation_overrides.final2.csv [可选最终译文来源2]
  --done          输出 output/translation_done.csv         [最终译文来源3]
  --out           输出 output/gap_inventory.csv            [默认]

输出: gap_inventory.csv (每 unique approved source 一行) + 终端汇总报告。
"""
import sys, os, csv, argparse
from pathlib import Path
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))
import pose_coverage as PC                     # 复用扫描映射 (冻结)
from phase2a_catalog import source_hash, norm_text, make_translation_id
import gen_cohort_sidecars as G                # 复用 approved_pv_refs (生产同源)

SAMPLE_6 = [
    "Tibo131 Standing Pose Pack #2",
    "Pose 1",
    "Pose 2",
    "[AlluringSims] Annoyed Emotions",
    "1",
    "Gounafier's Pose Pack",
]


def stable_id(src):
    return make_translation_id(source_hash(norm_text(src)), 1)


def load_catalog(catalog_path):
    """frozen 权威 catalog -> {stable_id: (norm_source, decision, translation, source_hash)}。
    schema: translation_id, source_text, decision, ..., translation, ..., source_hash"""
    cat = {}
    rows_total = 0
    with open(catalog_path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows_total += 1
            tid = (r.get("translation_id") or "").strip()
            stxt = (r.get("source_text") or "").strip()
            dec = (r.get("decision") or "").strip().upper()
            tr = (r.get("translation") or "").strip()
            sh = (r.get("source_hash") or "").strip()
            if not tid or not stxt:
                continue
            cat[tid] = (norm_text(stxt), dec, tr, sh)
    # 权威冻结基线核对 (非阻断, 仅提示)
    dec_count = Counter(v[1] for v in cat.values())
    print(f"[catalog] {rows_total} 行; 载入 {len(cat)} unique tid; decision 分布: "
          f"TRANSLATE={dec_count.get('TRANSLATE',0)} KEEP={dec_count.get('KEEP',0)} "
          f"REVIEW={dec_count.get('REVIEW',0)} APPROVED={dec_count.get('APPROVED',0)}")
    return cat


def load_final_translations(paths):
    """overrides(+final2)+done -> {(tid, norm_source): translation}。
    translation 非空才算最终译文。cache.db 不入这里 (非 final QA artifact)。"""
    final = {}
    src_rows = {}
    for p in paths:
        if not p or not Path(p).exists():
            continue
        with open(p, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                src_rows[p] = src_rows.get(p, 0) + 1
                tid = (r.get("translation_id") or "").strip()
                stxt = (r.get("source_text") or "").strip()
                tr = (r.get("translation") or "").strip()
                if not tid or not stxt or not tr:
                    continue
                # 后到覆盖: done 在 overrides 之后 (overrides 优先级更高, 但都算"最终译文存在")
                final[(tid, norm_text(stxt))] = tr
    for p, n in src_rows.items():
        print(f"[final] {os.path.basename(p)}: {n} 数据行")
    print(f"[final] 最终译文 (override/done 合并, 非空 translation) unique (tid,norm): {len(final)}")
    return final


def collect_packages(list_path):
    """返回 ELIGIBLE 包路径列表。--list 优先; 否则读 output/coverage.csv 取 ELIGIBLE。"""
    if list_path and Path(list_path).exists():
        pkgs = [l.strip() for l in open(list_path, encoding="utf-8-sig") if l.strip()]
        print(f"[packages] 从 --list 读入 {len(pkgs)} 包")
        return pkgs, "list"
    cov = Path("output/coverage.csv")
    if cov.exists():
        pkgs = []
        with open(cov, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("status", "").strip() == "ELIGIBLE_EXISTING_CHS":
                    p = (r.get("package_path") or r.get("path") or r.get("source_package") or "").strip()
                    if p:
                        pkgs.append(p)
        print(f"[packages] 从 coverage.csv 取到 {len(pkgs)} 个 ELIGIBLE_EXISTING_CHS 包")
        return pkgs, "coverage"
    raise SystemExit("[FAIL] 既无 --list 文件, 也找不到 output/coverage.csv")


def classify(src, tid, cat, final):
    """返回 (class, detail)。"""
    if tid in cat:
        dec = cat[tid][1]
        if dec == "KEEP":
            return "A", "CATALOG_KEEP"
        if dec in ("TRANSLATE", "APPROVED"):
            if (tid, norm_text(src)) in final:
                return "B", "CATALOG_TRANSLATE_RESOLVED"
            return "C", "CATALOG_TRANSLATE_MISSING_RESULT"
        # REVIEW 等其它 decision: 按无最终译文 -> 归 C? 用户口径: 要求 TRANSLATE 无译文=异常。
        if (tid, norm_text(src)) in final:
            return "B", f"CATALOG_TRANSLATE_RESOLVED(dec={dec})"
        return "C", f"CATALOG_TRANSLATE_MISSING_RESULT(dec={dec})"
    return "D", "NEW_SOURCE_NOT_IN_CATALOG"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list")
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--overrides", required=True)
    ap.add_argument("--overrides2", default="")
    ap.add_argument("--done", required=True)
    ap.add_argument("--out", default="output/gap_inventory.csv")
    a = ap.parse_args()

    cat = load_catalog(a.catalog)
    final = load_final_translations([a.overrides, a.overrides2, a.done])
    pkgs, src_kind = collect_packages(a.list)

    # 每 unique approved source 汇总
    rows = []          # (class, prov, tid, source_text)
    pkg_by_source = defaultdict(set)
    prov_by_source = defaultdict(set)
    errors = []
    n_pkg_ok = 0
    for p in pkgs:
        try:
            tgi, key_map, approved, errs = G.approved_pv_refs(str(p))
        except Exception as ex:
            errors.append(f"{os.path.basename(p)}: mapping 异常 {ex}")
            continue
        if errs:
            errors.append(f"{os.path.basename(p)}: {'; '.join(errs)}")
            continue
        n_pkg_ok += 1
        for catg, kh, src in approved:
            key = norm_text(src)
            pkg_by_source[key].add(os.path.basename(p))
            prov_by_source[key].add(catg)
            rows.append((key, catg, src))

    print(f"[scan] ELIGIBLE 包 {len(pkgs)}; 解析成功 {n_pkg_ok}; 异常 {len(errors)}")

    # 去重为 unique source; 一个 source 可有多个 provenance
    uniq_sources = {}
    for key, prov, src in rows:
        entry = uniq_sources.setdefault(key, {"src": src, "provs": set(), "pkgs": set()})
        entry["provs"].add(prov)
        entry["pkgs"] |= pkg_by_source[key]

    # 分类
    stats = {"A": 0, "B": 0, "C": 0, "D": 0}
    prov_stats = Counter()          # (class, prov)
    pkg_impact = defaultdict(lambda: defaultdict(set))  # class -> prov -> set(pkg)
    out_rows = []
    for key, e in sorted(uniq_sources.items()):
        tid = stable_id(e["src"])
        cls, detail = classify(e["src"], tid, cat, final)
        stats[cls] += 1
        for prov in e["provs"]:
            prov_stats[(cls, prov)] += 1
            pkg_impact[cls][prov].update(e["pkgs"])
        out_rows.append({
            "class": cls, "detail": detail,
            "translation_id": tid, "source_text": e["src"],
            "source_hash": source_hash(norm_text(e["src"])),
            "provenance": "|".join(sorted(e["provs"])),
            "package_count": len(e["pkgs"]),
            "packages": "|".join(sorted(e["pkgs"])),
        })

    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()) if out_rows else
                           ["class","detail","translation_id","source_text","source_hash",
                            "provenance","package_count","packages"])
        w.writeheader()
        for r in out_rows:
            w.writerow(r)

    # ---- 汇总报告 ----
    total = len(uniq_sources)
    print("\n================ 汇总报告 ================")
    print(f"unique approved source 总数: {total}")
    print(f"  A CATALOG_KEEP:                      {stats['A']}")
    print(f"  B CATALOG_TRANSLATE_RESOLVED:        {stats['B']}")
    print(f"  C CATALOG_TRANSLATE_MISSING_RESULT:  {stats['C']}   <- 异常")
    print(f"  D NEW_SOURCE_NOT_IN_CATALOG:         {stats['D']}")
    print("\n各字段类别 (class, provenance) 数量:")
    for cls in ("A", "B", "C", "D"):
        for prov in sorted({p for (c, p) in prov_stats if c == cls}):
            print(f"  {cls}  {prov:<22} {prov_stats[(cls, prov)]}")
    print("\n各类别影响 package (去重) 数:")
    for cls in ("A", "B", "C", "D"):
        tot_pkgs = set()
        for prov in pkg_impact[cls]:
            tot_pkgs |= pkg_impact[cls][prov]
        print(f"  {cls}: {len(tot_pkgs)} 包  | 分字段: " +
              ", ".join(f"{p}={len(pkg_impact[cls][p])}" for p in pkg_impact[cls]))
    print("\nD 类 provenance 拆分:")
    d_prov = Counter()
    for key, e in uniq_sources.items():
        tid = stable_id(e["src"])
        if tid not in cat:
            for prov in e["provs"]:
                d_prov[prov] += 1
    for prov, n in d_prov.items():
        print(f"  {prov:<22} {n}")

    print("\n================ 6 个样本归属 ================")
    for s in SAMPLE_6:
        tid = stable_id(s)
        cls, detail = classify(s, tid, cat, final)
        tag = "不在 catalog" if tid not in cat else f"catalog.decision={cat[tid][1]}"
        in_final = (tid, norm_text(s)) in final
        print(f"  {s!r:42} -> {cls} ({detail}) | {tag} | final_译文存在={in_final}")

    if errors:
        print(f"\n[注意] 包级异常 {len(errors)} 条 (首5):")
        for e in errors[:5]:
            print("   ", e)

    print(f"\n[out] {a.out}  ({len(out_rows)} unique source)")


if __name__ == "__main__":
    main()
