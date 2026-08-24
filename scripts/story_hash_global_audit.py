#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
story_hash_global_audit.py —— 只读: 用已确认的 Story hash 反向扫全 STBL, 找真实读取表

背景 (前面审计已确认):
  WW_ANIM_XML -> animation_id -> TurboLocalizedString.hash
    -> get_localized_string_id() -> 返回 hash key (STBL key)
  已知目标不是文本搜索 ("Caught Cheating"), 而是 【STBL key hash】 搜索。
  Story ordinal 的显示 hash key = FNV-32("story_animations." + str(animation_id))

本脚本:
  1. 从 WW_ANIM_XML 提取 ordinal 299-306 的 animation_id, 生成每 ordinal 的 Story hash
       - ordinal 299/300 用【权威已知 hash】覆盖 (299=0x0883B1CF, 300=0x2703091A)
       - ordinal 301-306 按 P22 逻辑自动生成 (XML animation_id -> FNV-32)
  2. 反向扫描所有 STBL(type=0x220557DA):
       范围A: Mods (--dir 递归所有 .package)
       范围B: 游戏目录 Data\\Client + Data\\Simulation + Data\\Delta (--game-dir 下列)
  3. 对每个 story hash, 在 STBL 全表中反查命中的 (instance, group, type, text, package)
  4. 输出报告 (无 pyc 反汇编):
       output/story_hash_audit/summary.txt
       output/story_hash_audit/stbl_hits.csv
     summary 每 ordinal:
       ordinal / animation_id / hash / FOUND(或 NOT FOUND) / package / instance / group / type / text
     NOT FOUND 时明确提示: 可能为 runtime L18N string, 不在 STBL。

要求: 只读 (不修改 Mods), fail-closed, 只出 txt/csv 报告, 不输出反汇编。
ZERO_WRITE_TO_MODS=YES。缺源/无XML/无STBL -> fail-closed exit。

用法:
  python scripts/story_hash_global_audit.py "<WW.package>" \
      --dir "C:\\...\\Mods" \
      --game-dir "C:\\...\\The Sims 4" \
      [--ordinals 299-306] [--out-dir output/story_hash_audit]
"""
import argparse
import csv
import sys
import struct as _struct
from importlib import import_module
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
try:
    from dbpf_fast import safe_parse
    from backend import get_backend
except Exception as ex:
    print(f"ERROR: 依赖加载失败: {ex} (需在 sims4-translator 内运行)", file=sys.stderr)
    sys.exit(5)

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import ww_animation_p7_story_chain_audit as P7   # load_xml / ordinal_blocks
import ww_animation_p22_story_stbl as P22        # fnv32 / parse_stbl / parse_ordinals

WW_ANIM_XML = 0x7DF2169C
STBL = 0x220557DA
STORY_PREFIX = "story_animations."
ANIM_ID_FIELD = "animation_id"

# 权威已知 hash (前面审计已确认)
KNOWN_HASHES = {299: 0x0883B1CF, 300: 0x2703091A}

# 游戏目录下要扫的 Data 子目录
GAME_SUBDIRS = ("Data", "Data/Client", "Data/Simulation", "Data/Delta")


def parse_ordinals(spec: str):
    return P22.parse_ordinals(spec)


def extract_anim_id(el):
    """从 ordinal entry (ET元素) 提取 animation_id 整数值."""
    for n in el.iter():
        tag = (n.tag or "").rsplit("}", 1)[-1]
        if tag != "I":
            continue
        if n.get("n") == ANIM_ID_FIELD:
            raw = (n.text or "").strip()
            try:
                return int(raw, 16) if raw.lower().startswith("0x") else int(raw)
            except Exception:
                return None
    return None


def story_hash_for(oid, aid):
    if oid in KNOWN_HASHES:
        return KNOWN_HASHES[oid], f"known(权威)"
    if aid is None:
        return None, "no_anim_id"
    return P22.fnv32(f"{STORY_PREFIX}{aid}"), f"P22:{aid}"


def collect_packages(paths):
    """收集一组文件/目录下的所有 .package, 去重."""
    seen = set()
    out = []
    for p in paths:
        q = Path(p)
        if not q.exists():
            continue
        cands = []
        if q.is_file() and q.suffix.lower() == ".package":
            cands = [q]
        elif q.is_dir():
            cands = sorted(x for x in q.rglob("*.package") if x.is_file())
        for c in cands:
            r = str(c.resolve())
            if r not in seen:
                seen.add(r)
                out.append(c)
    return out


def scan_all_stbl(paths):
    """扫所有 package 的 STBL, 返回 {key: [(instance_id, group_id, type_id, text, pkg)]}."""
    by_key = {}
    total_stbl = 0
    scanned_pkg = 0
    for p in paths:
        try:
            idx, err = safe_parse(str(p))
        except Exception:
            continue
        if err is not None or idx is None:
            continue
        scanned_pkg += 1
        try:
            backend = get_backend("readonly").open(str(p))
        except Exception:
            continue
        for e in idx.entries:
            if e.type_id != STBL:
                continue
            total_stbl += 1
            try:
                data = backend.read_small_resource(e)
            except Exception:
                continue
            if not data:
                continue
            try:
                stbl = P22.parse_stbl(data)
            except Exception:
                continue
            for k, txt in stbl.items():
                if not txt:
                    continue
                by_key.setdefault(k, []).append(
                    (e.instance_id, e.group_id, e.type_id, txt, p.name))
    return by_key, total_stbl, scanned_pkg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="含 WW_ANIM_XML 的源 package")
    ap.add_argument("--dir", required=True, help="Mods 目录 (递归扫 .package)")
    ap.add_argument("--game-dir", default=None, help="游戏根目录 (扫其下 Data 各子目录)")
    ap.add_argument("--ordinals", default="299-306", help="ordinal 区间, 默认 299-306")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: 源文件不存在 {src}"); return 2
    out_dir = Path(a.out_dir if a.out_dir else "output/story_hash_audit")
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- 1) 提取 animation_id + 生成 story hash ---
    ww_first, err = P7.load_xml(src)
    if err is not None or ww_first is None:
        print(f"ERROR: WW_ANIM_XML 不可用: {err}"); return 3
    ordinals = parse_ordinals(a.ordinals)
    blocks, berr = P7.ordinal_blocks(src, ww_first, ordinals)
    if berr is not None:
        print(f"ERROR: ordinal 提取失败: {berr}"); return 4

    story = {}
    for o in ordinals:
        aid = extract_anim_id(blocks[o])
        h, how = story_hash_for(o, aid)
        story[o] = {"ordinal": o, "animation_id": aid, "hash": h, "how": how}

    # --- 2) 收集扫描范围 ---
    scan_paths = [a.dir]
    if a.game_dir:
        gd = Path(a.game_dir)
        for sub in GAME_SUBDIRS:
            scan_paths.append(str(gd / sub))
        # 兜底: 整个 game-dir 也扫 (去重由 collect_packages 处理)
        scan_paths.append(str(gd))
    pkg_paths = collect_packages(scan_paths)
    by_key, total_stbl, scanned_pkg = scan_all_stbl(pkg_paths)

    # --- 3) 反查每个 story hash ---
    L = []
    L.append("=== STORY HASH AUDIT ===")
    L.append("")
    L.append("依据: WW_ANIM_XML -> animation_id -> TurboLocalizedString.hash")
    L.append("      -> get_localized_string_id() -> 返回 hash key (STBL key)")
    L.append("关键: 目标是 STBL key hash, 不是文本 'Caught Cheating'。")
    L.append("")
    L.append(f"源 WW package : {src.name}")
    L.append(f"扫描范围      : Mods(={a.dir})" + (f" + Game(={a.game_dir})" if a.game_dir else ""))
    L.append(f"扫描 package  : {scanned_pkg} 个")
    L.append(f"STBL 资源数   : {total_stbl} 个")
    L.append("")
    L.append("---")

    rows = []
    hits_total = 0
    for o in ordinals:
        s = story[o]
        h = s["hash"]
        aid = s["animation_id"]
        L.append("")
        L.append(f"ordinal : {o}")
        L.append(f"animation_id : {aid if aid is not None else '(无 animation_id 字段)'}")
        L.append(f"hash    : {('0x%08X' % h) if h is not None else '(未生成)'}   [{s['how']}]")
        if h is None:
            L.append("NOT FOUND")
            L.append("  - 无法生成 hash (无 animation_id, 也非已知 299/300)。")
            rows.append({"ordinal": o, "animation_id": aid or "", "hash": "", "found": "NO_HASH",
                         "package": "", "instance": "", "group": "", "type": "", "text": ""})
            continue
        recs = by_key.get(h)
        if not recs:
            L.append("FOUND: NO")
            L.append("NOT FOUND")
            L.append("  - 该 hash 不在任何扫描到的 STBL 中。")
            L.append("  - 可能: runtime L18N string (TurboLib L18n 运行期表 / GameData基础字符串), STBL 不在本扫描范围,")
            L.append("         或 hash 非 FNV-32(Story 前缀 'story_animations.' 不成立)。")
            rows.append({"ordinal": o, "animation_id": aid or "", "hash": f"0x{h:08X}", "found": "NO",
                         "package": "", "instance": "", "group": "", "type": "", "text": ""})
            continue
        hits_total += 1
        # 去重 (同一 instance 同文本)
        seen = set()
        unique = []
        for inst, grp, typ, txt, pkg in recs:
            key = (inst, grp, txt, pkg)
            if key in seen:
                continue
            seen.add(key)
            unique.append((inst, grp, typ, txt, pkg))
        L.append(f"FOUND: YES  ({len(unique)} 处)")
        for inst, grp, typ, txt, pkg in unique:
            L.append(f"  package : {pkg}")
            L.append(f"  instance: 0x{inst:016X}")
            L.append(f"  group   : 0x{grp:08X}")
            L.append(f"  type    : 0x{typ:08X}  (STBL)")
            L.append(f"  text    : {txt}")
            L.append("  ---")
            rows.append({"ordinal": o, "animation_id": aid or "", "hash": f"0x{h:08X}",
                         "found": "YES", "package": pkg,
                         "instance": f"0x{inst:016X}", "group": f"0x{grp:08X}",
                         "type": f"0x{typ:08X}", "text": txt})

    L.append("")
    L.append("---")
    L.append(f"命中 ordinal 数 = {hits_total}/{len(ordinals)}")
    if hits_total < len(ordinals):
        L.append("")
        L.append("!! 存在未命中 ordinal.")
        L.append("   这些 hash 很可能是 runtime L18N string (不在 STBL 内),")
        L.append("   显示文本由 TurboLib L18n 运行期表 / GameData基础字符串 / .ts4script 生成,")
        L.append("   而非 .package STBL。需进一步查 runtime 或 .ts4script。")
    L.append("")
    L.append("ZERO_WRITE_TO_MODS=YES (只读)")
    L.append(f"EXIT=" + ("0" if hits_total == len(ordinals) else "4"))

    # --- 写输出 ---
    (out_dir / "summary.txt").write_text("\n".join(L), encoding="utf-8")
    with open(out_dir / "stbl_hits.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ordinal", "animation_id", "hash", "found", "package",
                    "instance", "group", "type", "text"])
        for r in rows:
            w.writerow([r["ordinal"], r["animation_id"], r["hash"], r["found"],
                        r["package"], r["instance"], r["group"], r["type"], r["text"]])

    print("\n".join(L))
    print("")
    print(f"报告已写入: {out_dir}/summary.txt , {out_dir}/stbl_hits.csv")
    return 0 if hits_total == len(ordinals) else 4


if __name__ == "__main__":
    sys.exit(main())
