#!/usr/bin/env python3
"""只读: 导出指定 translation_id 的全部 context / neighbor / pose 邻接信息。

不改 cache / translation_done / QA / 不写 package。纯读取 + 打印。

读:
  - translation_catalog.csv   (decision/reason/package_count/samples)
  - translation_contexts.csv  (每个实例的 package/pose_pack/neighbor/stbl/idx)
  - translation_done.csv      (当前 zh)

用法:
  python scripts/dump_error_contexts.py D:\projects\sims4_trans\output [tid1,tid2,...]
  若省略 tid 列表, 自动取 qa 报告里 ERROR 的 tid。

输出分区 (按同 source_text 有多个 context/group 时, 逐 package 分开列)。
"""
import sys, csv, os
from pathlib import Path


def _load(out: Path, name: str):
    p = out / name
    if not p.exists():
        print(f"  [缺文件] {name}"); return []
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main():
    out = Path(sys.argv[1])
    tid_arg = sys.argv[2] if len(sys.argv) > 2 else None

    if tid_arg:
        want = {x.strip() for x in tid_arg.split(",") if x.strip()}
    else:
        report = out / "translation_qa_report.csv"
        want = set()
        if report.exists():
            for r in csv.DictReader(open(report, encoding="utf-8-sig")):
                if (r.get("qa") or "").strip() == "ERROR":
                    want.add((r.get("translation_id") or "").strip())
        else:
            print("[缺文件] translation_qa_report.csv, 请传 tid 列表"); sys.exit(1)

    cat = _load(out, "translation_catalog.csv")
    ctx = _load(out, "translation_contexts.csv")
    done = _load(out, "translation_done.csv")

    # 索引
    cat_by_tid = {r.get("translation_id"): r for r in cat}
    done_by_tid = {r.get("translation_id"): r for r in done}
    ctx_by_tid = {}
    for r in ctx:
        tid = (r.get("translation_id") or "").strip()
        ctx_by_tid.setdefault(tid, []).append(r)

    for tid in sorted(want):
        src = ""
        r_done = done_by_tid.get(tid, {})
        r_cat = cat_by_tid.get(tid, {})
        src = (r_done.get("source_text") or r_cat.get("source_text") or "").strip()
        zh = (r_done.get("translation") or "").strip()
        dec = (r_cat.get("decision") or "").strip()
        reason = (r_cat.get("reason") or "").strip()
        pc = (r_cat.get("package_count") or "").strip()
        samples = (r_cat.get("packages") or r_cat.get("unique_refs") or "").strip()

        print("=" * 78)
        print(f"tid: {tid}")
        print(f"source_text: {src!r}")
        print(f"当前 zh:      {zh!r}")
        print(f"decision:     {dec or '(空)'} | reason: {reason or '(空)'}")
        print(f"package_count:{pc or '?'} | samples(packages): {samples or '(空)'}")

        rows = ctx_by_tid.get(tid, [])
        groups = {}
        for r in rows:
            pkg = (r.get("package_path") or "?").strip()
            groups.setdefault(pkg, []).append(r)
        print(f"context 实例数: {len(rows)} (分组数: {len(groups)})")
        for pkg, grp in groups.items():
            print(f"  ── package: {pkg} ({len(grp)} 实例)")
            for r in grp:
                pose_pack = (r.get("pose_pack_instance") or "").strip()
                stbl = (r.get("stbl_resource_instance") or "").strip()
                locale = (r.get("locale") or "").strip()
                keyref = (r.get("stbl_key_ref") or "").strip()
                idx = (r.get("pose_entry_idx") or "").strip()
                namehash = (r.get("pose_display_name_hash") or "").strip()
                nbr = (r.get("neighbor_display_texts") or "").strip()
                print(f"     pose_pack={pose_pack or '?'} stbl={stbl or '?'} locale={locale or '?'}")
                print(f"     key_ref={keyref or '?'} entry_idx={idx or '?'} name_hash={namehash or '?'}")
                if nbr:
                    print(f"     neighbor_display_texts: {nbr}")
                else:
                    print(f"     neighbor_display_texts: (无)")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
