#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P13 Story runtime 注册来源审计 (只读, 外部资源)。

背景 (已确立):
  - P11.5: Story(299-306) 条目内【无】display_key/text_key/hash/STBL-ref
    -> 显示名不经 WW_ANIM_XML 内部 key 间接读取
  - P4:   本 package 内【无】非 WW 资源引用 WW_ANIM_XML instance
  - P7:   124-126 vs 299-306 字段集合/形态无差异
  -> Story 的注册/缓存/映射必然在【本包之外】: 同一 Mods 目录下的其它
     .package, 或 WickedWhims 核心 .ts4script

本审计目标 (只读): 扫 --dir 下所有 .package + .ts4script, 对每个文件全文
检索以下线索, 判定哪个外部制品"认识/注册"Story 动画:
  1) 关键字: story / STORY / animation_stage_name / animation_category /
     caught cheating / nevely
  2) WW_ANIM_XML instance hex (0x...) — 谁引用/注册这条 XML
  3) 目标 ordinal 的显示名串 (Caught Cheating N / Addicted N)
  4) tgi/guid/hash 形态 (find_tgis)
对每个命中文件分类: 疑似 runtime 脚本 / 疑似 registry / 疑似 cache /
  疑似其它包 WW_ANIM_XML / 无关。

对比 Addicted(124-126) vs Caught Cheating(299-306):
  列出每个外部文件"认识"读系列(含哪些标识串), 看是否只有 CC 被外部注册
  (从而解释为何 runtime 对两者处理不同)。

fail-closed: 源缺->2; 无 WW->3; 目录不存在->4; 目标目录内无任何
  package/ts4script->5。只读, 不生成包, 不碰 Mods (ZERO_WRITE_TO_MODS=YES)。

用法 (Windows, 只读):
  python scripts/ww_animation_p13_story_runtime_audit.py `
      "<SRC.package>" --dir "C:\\...\\Mods\\2026.7.20" `
      [--ordinals 124 125 126 299 ... 306] [--out-dir output/ww_p13]
"""
import argparse
import csv
import importlib.util
import re
import sys
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
_spec = importlib.util.spec_from_file_location(
    "ww_canary_builder", SCRIPT_DIR / "ww_animation_canary_builder.py")
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)
import ww_animation_m2_diff_forensic as _diff
import ww_animation_p1_resource_forensic as _p1
import ww_animation_p7_story_chain_audit as _p7

WW_ANIM_XML = 0x7DF2169C
OUT_DIR = Path("output/ww_p13")

# 关键字 (小写; 用单词边界/包含匹配)
KW_STORY = ["story", "animation_category", "animation_stage_name",
            "caught cheating", "nevely", "storyboard", "story_line",
            "narrative", "ww_story", "story_id"]


def _scan_zip(fpath):
    """.ts4script 是 zip。返回 (member_list, all_lower_bytes)。
    损坏 -> 返回 (None, None)。"""
    try:
        with zipfile.ZipFile(fpath) as z:
            names = z.namelist()
            blob = b"".join(z.read(n) for n in names if z.getinfo(n).file_size < 8_000_000)
    except Exception:
        return None, None
    return names, blob.lower()


def _scan_package(fpath):
    """.package: 枚举所有 entry, 解码正文。返回 (type_count, lower_text_parts)。"""
    try:
        idx, err = wb.safe_parse(fpath)
        if err is not None or idx is None:
            return None, None, f"解析失败: {err}"
    except Exception as ex:
        return None, None, f"解析异常: {ex}"
    tc = {}
    parts = []
    ww_instances = []
    for e in idx.entries:
        tc[e.type_id] = tc.get(e.type_id, 0) + 1
        if e.type_id == WW_ANIM_XML:
            ww_instances.append(f"0x{e.instance_id:016x}")
        try:
            raw = wb.read_body_raw(fpath, e)
            body = wb.decompress_maybe(raw)
            _k, text = _p1.decode_body(body)
            if text:
                parts.append(text.lower())
        except Exception:
            pass
    return tc, "\n".join(parts), ww_instances


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="源 WW 动画 package")
    ap.add_argument("--dir", required=True, help="扫描的 Mods 目录 (含其它 .package/.ts4script)")
    ap.add_argument("--ordinals", nargs="*", type=int,
                    default=[124, 125, 126, 299, 300, 301, 302, 303, 304, 305, 306])
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: 源不存在 {src}", file=sys.stderr); return 2
    d = Path(a.dir)
    if not d.is_dir():
        print(f"ERROR: --dir 不存在 {d}", file=sys.stderr); return 4
    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 1) 从源 package 提取: WW XML instance + 每 ordinal 显示名串 ----
    ww_first, err = _p7.load_xml(src)
    if ww_first is None:
        print(f"ERROR: {err}", file=sys.stderr); return 3
    src_inst = f"0x{ww_first.instance_id:016x}"
    src_tgis = set(_p7.find_tgis(src_inst) or [])

    src_idx, serr = wb.safe_parse(src)
    if serr is not None or src_idx is None:
        print(f"ERROR: 源解析失败 {serr}", file=sys.stderr); return 3
    ww_inst_all = {f"0x{e.instance_id:016x}" for e in src_idx.entries
                   if e.type_id == WW_ANIM_XML}

    # per-ordinal display + stage + clip (小写)
    disp_of, stage_of, clip_of = {}, {}, {}
    ords_ok = set(range(124, 127))
    ords_fail = set(range(299, 307))
    blocks, berr = _p7.ordinal_blocks(src, ww_first, a.ordinals)
    if blocks is None:
        print(f"ERROR: {berr}", file=sys.stderr); return 3
    for o, root in blocks.items():
        for node in root.iter():
            n = node.get("n") or ""
            v = (node.text or "").strip().lower()
            nl = n.lower()
            if nl in ("animation_raw_display_name", "animation_display_name") and v:
                disp_of.setdefault(o, []).append(v)
            elif "animation_stage_name" in nl and v:
                stage_of.setdefault(o, []).append(v)
            elif "animation_clip_name" in nl and v:
                clip_of.setdefault(o, []).append(v)

    # ---- 2) 枚举外部文件 ----
    pkgs = sorted(d.glob("*.package"))
    scripts = sorted(d.glob("*.ts4script"))
    if not pkgs and not scripts:
        print(f"ERROR: {d} 下没有 .package/.ts4script", file=sys.stderr); return 5
    files = [(p, "package") for p in pkgs] + [(s, "script") for s in scripts]

    # 匹配直接在下方 hit() 内对 disp_of/stage_of 做命中, 无需预构建

    L = []
    L.append("=== P13 Story runtime 注册来源审计 (只读, 外部资源) ===")
    L.append(f"源 package = {src.name}")
    L.append(f"WW_ANIM_XML instance(枚举) = {sorted(ww_inst_all)}")
    L.append(f"扫描目录 = {d}")
    L.append(f"发现 .package={len(pkgs)}  .ts4script={len(scripts)}")
    L.append(f"目标 ordinals = {a.ordinals}")
    L.append(f"  Addicted(124-126) vs Caught Cheating(299-306)")
    L.append("")

    # ---- 3) 逐文件扫描 ----
    rows = []   # csv: file, kind, story_kw, cc_hit, add_hit, ww_inst_hit, tgis, verdict
    for fpath, kind in files:
        if fpath.resolve() == src.resolve():
            continue  # 跳过源包本身
        if kind == "script":
            names, blob = _scan_zip(fpath)
            if blob is None:
                rows.append([fpath.name, kind, "zip损坏", "", "", "", "", "zip损坏"])
                continue
            lower = blob.decode("latin-1", "replace")
            members = " ".join(names).lower()
        else:
            tc, lower, ww_insts = _scan_package(fpath)
            if lower is None:
                rows.append([fpath.name, kind, tc, "", "", "", "", "读取失败"])
                continue
            members = ""

        # 命中判定 (小写)
        def hit(*sublist):
            for s in sublist:
                if s and s in lower:
                    return True
            return False

        story_kw = [k for k in KW_STORY if k in lower or k in members]
        cc_hit = hit("caught cheating", *[s for o in ords_fail for s in disp_of.get(o, [])])
        add_hit = hit("addicted", *[s for o in ords_ok for s in disp_of.get(o, [])])
        ww_inst_hit = [i for i in ww_inst_all if i in lower]
        tgis = _p7.find_tgis(lower)[:50]
        # 判定
        if story_kw or cc_hit or ww_inst_hit:
            if ww_inst_hit:
                verdict = "疑似引用/注册这个 WW XML"
            elif cc_hit and not add_hit:
                verdict = "只认识 Caught Cheating (Story) -> 疑似 Story 注册表"
            elif cc_hit and add_hit:
                verdict = "同时认识 Addicted 与 CC -> 通用动画注册/索引"
            else:
                verdict = "含 Story 关键字, 疑似 runtime/registry"
        else:
            verdict = "未命中 (无关)"
        rows.append([fpath.name, kind, ";".join(story_kw), "Y" if cc_hit else "N",
                     "Y" if add_hit else "N", ";".join(ww_inst_hit),
                     ";".join(tgis), verdict])

        L.append(f"### {fpath.name} [{kind}]")
        L.append(f"  Story关键字命中: {story_kw or '(无)'}")
        L.append(f"  认识 CC(Caught Cheating)={cc_hit}  认识 Addicted={add_hit}")
        L.append(f"  引用 WW XML instance: {ww_inst_hit or '(无)'}")
        L.append(f"  判定: {verdict}")
        L.append("")

    # ---- 4) 对比总结 ----
    L.append("=== Addicted vs Caught Cheating 外部注册对比 ===")
    only_cc = [r for r in rows if r[3] == "Y" and r[4] == "N"]
    only_add = [r for r in rows if r[3] == "N" and r[4] == "Y"]
    both = [r for r in rows if r[3] == "Y" and r[4] == "Y"]
    L.append(f"  只认识 CC 的文件: {len(only_cc)} -> {[r[0] for r in only_cc]}")
    L.append(f"  只认识 Addicted 的文件: {len(only_add)} -> {[r[0] for r in only_add]}")
    L.append(f"  同时认识两者的文件: {len(both)} -> {[r[0] for r in both]}")
    L.append("")
    L.append("=== 结论 ===")
    if only_cc and not only_add:
        L.append("发现【只】认识 Caught Cheating(Story) 的外部文件 -> Story 由该文件注册,")
        L.append("且 Addicted 不走该注册 -> 这就是 runtime 处理差异的根源。下一步:")
        L.append("在 P14 中读该文件对应资源, 看 Story 显示名如何注册/映射。")
    elif both and not only_cc and not only_add:
        L.append("外部文件同时认识两者 -> 注册是共用的, 差异在 WW runtime 对")
        L.append("animation_category=story 的特殊分支, 而非独立注册表。")
    elif not only_cc and not only_add and not both:
        L.append("所有外部文件均不按名称认识 CC/Addicted -> 注册表不在此目录;")
        L.append("可能按 WW_ANIM_XML instance / 或 WW 核心脚本内建字段映射, 需进一步。")
    else:
        L.append("外部注册混合命中, 以 .csv 明细为准。")
    L.append("")
    L.append("ZERO_WRITE_TO_MODS=YES (只读, 未生成包, 未动 Mods)")

    txt = "\n".join(L)
    txt_path = out_dir / "p13_story_runtime_audit.txt"
    txt_path.write_text(txt, encoding="utf-8")

    csv_path = out_dir / "p13_story_runtime_audit.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "kind", "story_kw", "cc_hit", "add_hit",
                    "ww_inst_hit", "tgis", "verdict"])
        w.writerows(rows)

    print(txt)
    print(f"OUT_TXT={txt_path}")
    print(f"OUT_CSV={csv_path}")
    print("P13_STORY_RUNTIME_AUDIT=OK (只读)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
