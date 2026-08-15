#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diag_resolver_assets.py  —— 只读 loader 诊断 (对 Windows 真实 frozen asset)

对 translation_done.csv / translation_cache.db 做 read-only 体检,
并对 6 个已知 stable_id 做 exact lookup, 输出 resolver 接受/拒绝的根因。
不修改任何文件, 不新建翻译, 不改 frozen data, 不生成 sidecar。

用法 (Windows, 先 cd 到仓库根):
  python scripts\diag_resolver_assets.py ^
      --done output\translation_done.csv ^
      --cache output\translation_cache.db

输出: 逐项打印, 结尾打印 JSON 摘要。
"""
import sys, os, csv, json, sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from phase2a_catalog import source_hash, norm_text, make_translation_id

STABLE_IDS = {
    "T_b56169d01d20_g1": "Tibo title",
    "T_e9dded687c56_g1": "Pose 1",
    "T_0504a07d0736_g1": "Pose 2",
    "T_1ed60c4ed6a6_g1": "AlluringSims title",
    "T_6b86b273ff34_g1": '"1"',
    "T_8595523bec53_g1": "Gounafier title",
}
# 同款样本 source_text (供 source_text 一致性比对)
SAMPLES = {
    "T_b56169d01d20_g1": "Tibo131 Standing Pose Pack #2",
    "T_e9dded687c56_g1": "Pose 1",
    "T_0504a07d0736_g1": "Pose 2",
    "T_1ed60c4ed6a6_g1": "[AlluringSims] Annoyed Emotions",
    "T_6b86b273ff34_g1": "1",
    "T_8595523bec53_g1": "Gounafier's Pose Pack",
}


def file_meta(p):
    p = Path(p)
    meta = {"absolute_path": str(p.resolve()), "exists": p.exists()}
    if p.exists():
        meta["size_bytes"] = p.stat().st_size
        raw = p.read_bytes()[:8]
        meta["bom"] = ("UTF-8-BOM" if raw[:3] == b"\xef\xbb\xbf"
                       else "UTF-16-LE-BOM" if raw[:2] == b"\xff\xfe"
                       else "UTF-16-BE-BOM" if raw[:2] == b"\xfe\xff"
                       else "none")
    return meta


def sniff_encoding(p, bom):
    det = bom if bom != "none" else "utf-8"
    return det, "utf-8-sig" if bom == "UTF-8-BOM" else ("utf-16" if bom.startswith("UTF-16") else "utf-8")


def diag_done(done_path):
    print("=" * 76)
    print("### translation_done.csv")
    print("=" * 76)
    meta = file_meta(done_path)
    print(f"absolute path : {meta['absolute_path']}")
    print(f"exists        : {meta['exists']}")
    if not meta["exists"]:
        return None
    print(f"file size     : {meta['size_bytes']} bytes")
    print(f"BOM           : {meta['bom']}")
    enc, enc_sig = sniff_encoding(done_path, meta["bom"])
    print(f"decoded as    : {enc} (csv open encoding={enc_sig})")

    try:
        with open(done_path, encoding=enc_sig, errors="replace") as f:
            reader = csv.DictReader(f)
            hdr = list(reader.fieldnames or [])
            rows = list(reader)
    except Exception as ex:
        print(f"  !! 读取失败: {ex}")
        return {"meta": meta, "error": str(ex)}

    print(f"CSV headers   : {hdr}")
    print(f"total rows    : {len(rows)}")
    nonempty_tid = [r for r in rows if (r.get("translation_id") or "").strip()]
    print(f"rows with translation_id : {len(nonempty_tid)}")
    resolver_rows = [r for r in nonempty_tid
                     if (r.get("source_text") or "").strip()]
    print(f"resolver 实际载入 rows(有 tid+source_text) : {len(resolver_rows)}")

    # status 取值分布
    from collections import Counter
    st_cnt = Counter((r.get("status") or "").strip() for r in nonempty_tid)
    print(f"status 取值分布  : {dict(st_cnt)}")
    act_cnt = Counter((r.get("action") or "").strip() for r in nonempty_tid)
    print(f"action 列取值    : {dict(act_cnt)}")

    print("\n# 6 个 stable_id exact lookup")
    lookup = {}
    by_tid = {}
    for r in nonempty_tid:
        by_tid.setdefault((r.get("translation_id") or "").strip(), []).append(r)
    for tid, label in STABLE_IDS.items():
        rec = {"label": label, "stable_id": tid, "exists_in_csv": tid in by_tid}
        if tid in by_tid:
            cands = by_tid[tid]
            forms = []
            for r in cands:
                src = (r.get("source_text") or "")
                forms.append({"row_source_text": src,
                              "normalized": norm_text(src),
                              "hash_matches": source_hash(norm_text(src)) == tid[2:-3],
                              "status": (r.get("status") or "").strip(),
                              "translation": (r.get("translation") or "").strip() or "(空)",
                              "action": (r.get("action") or "").strip()})
            sample_src = SAMPLES[tid]
            sample_norm = norm_text(sample_src)
            accepts = False
            reject = "无匹配 row"
            for fm in forms:
                if fm["normalized"] == sample_norm:
                    accepts = True
                    reject = ""
                    break
            if not accepts:
                reject = (f"row(s) 的 normalized source_text != 样本 {sample_norm!r}; "
                          f"resolver 按 (tid, norm_text) 精确匹配 -> 拒绝")
            rec.update({"rows": forms, "resolver_accepts": accepts,
                        "resolver_reject_reason": reject,
                        "expected_norm": sample_norm})
            print(f"\n  [{tid}] {label}  -> {'FOUND' if forms else 'none'}")
            for fm in forms:
                print(f"      row: source={fm['row_source_text']!r} norm={fm['normalized']!r} "
                      f"hash_ok={fm['hash_matches']} status={fm['status']!r} "
                      f"tr={fm['translation']!r} action={fm['action']!r}")
            print(f"      resolver: accepts={accepts}  reject_reason={reject!r}")
        else:
            print(f"\n  [{tid}] {label}  -> 不存在于 done.csv")
        lookup[tid] = rec
    return {"meta": meta, "headers": hdr, "total_rows": len(rows),
            "status_dist": dict(st_cnt), "lookup": lookup}


def diag_cache(cache_path):
    print("\n" + "=" * 76)
    print("### translation_cache.db")
    print("=" * 76)
    meta = file_meta(cache_path)
    print(f"absolute path : {meta['absolute_path']}")
    print(f"exists        : {meta['exists']}")
    if not meta["exists"]:
        return None
    print(f"file size     : {meta['size_bytes']} bytes")
    try:
        conn = sqlite3.connect(cache_path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        print(f"SQLite tables : {tables}")
        if "phrase_cache" in tables:
            cur.execute("PRAGMA table_info(phrase_cache)")
            cols = [(r[1], r[2]) for r in cur.fetchall()]
            print(f"phrase_cache schema:")
            for name, typ in cols:
                print(f"    {name:<24} {typ}")
            cur.execute("SELECT COUNT(*) FROM phrase_cache")
            total = cur.fetchone()[0]
            print(f"phrase_cache rows : {total}")
            cnames = [c[0] for c in cols]
            # 6 个稳定ID / source_hash / source_text 是否存在
            print("\n# 6 个 stable_id / source_hash / source_text 在 cache 的命中")
            lookup = {}
            for tid, label in STABLE_IDS.items():
                rec = {"label": label, "stable_id": tid}
                if "translation_id" in cnames:
                    cur.execute(
                        "SELECT COUNT(*) FROM phrase_cache WHERE translation_id=?",
                        (tid,))
                    n_tid = cur.fetchone()[0]
                else:
                    n_tid = None
                if "source_hash" in cnames:
                    sh = source_hash(norm_text(SAMPLES[tid]))
                    cur.execute("SELECT COUNT(*) FROM phrase_cache WHERE source_hash=?",
                                (sh,))
                    n_sh = cur.fetchone()[0]
                else:
                    n_sh = None
                cur.execute(
                    "SELECT COUNT(*) FROM phrase_cache WHERE source_phrase=?",
                    (SAMPLES[tid],))
                n_sp = cur.fetchone()[0]
                rec.update({"n_by_translation_id": n_tid,
                            "n_by_source_hash": n_sh,
                            "n_by_source_phrase_exact": n_sp})
                print(f"  [{tid}] {label}: by translation_id={n_tid}  "
                      f"by source_hash={n_sh}  by source_phrase={n_sp}")
                lookup[tid] = rec
            # resolver 当前 SQL 实际查什么
            print("\n# resolver 当前 cache SQL 语义:")
            print("  查 phrase_cache WHERE source_phrase=?   (STBL 全文, 非 strip)")
            print("  主键为 request_fingerprint; translation_id 有 idx_cache_tid 索引")
            print("  => 若 source_phrase 存的是分段/剥离原子短语, 全文精确匹配会 miss")
            conn.close()
            return {"meta": meta, "tables": tables, "cols": cols,
                    "rows": total, "lookup": lookup}
        conn.close()
        return {"meta": meta, "tables": tables}
    except Exception as ex:
        print(f"  !! cache.db 读取失败: {ex}")
        return {"meta": meta, "error": str(ex)}


def main():
    ap = __import__("argparse").ArgumentParser()
    ap.add_argument("--done")
    ap.add_argument("--cache")
    a = ap.parse_args()
    out = {}
    if a.done:
        out["done"] = diag_done(a.done)
    if a.cache:
        out["cache"] = diag_cache(a.cache)
    print("\n" + "=" * 76)
    print("### JSON 摘要")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
