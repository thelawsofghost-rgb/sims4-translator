#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
debug_stbl_reverse_hash.py —— 只读反向 hash 发现

目的: text-first 找不到精确文本时, 不假设 "Caught Cheating 1"。改为:
  1. 扫描所有 STBL (源 + --dir Mods + --game-dir), 对每条文本做【子串/模糊】匹配:
      任何包含 "caught" / "cheat" / "story" (大小写不敏感) 的文本全部列出。
  2. 每条输出:
      文本 (英文原文)
      key hash (0x........)
      stbl instance (0x................)
      package (来源文件名)
  3. 若 Mods/Game STBL 完全无命中, 明确提示: 文本很可能在 Game data / L18n runtime 表,
     需到 game 安装目录的 (GameData) 或 base sims4.gameplay.story 相关 .ts4script / .package 查。

只读: 不修改任何 package。ZERO_WRITE_TO_MODS=YES。

用法:
  python scripts\\debug_stbl_reverse_hash.py <WW.package> \\
      --dir <Mods> [--game-dir <Game>] [--only-first-each-package]
"""
import argparse
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import ww_animation_p22_story_stbl as P22          # parse_stbl / fnv32
from dbpf_fast import safe_parse
from backend import get_backend

STBL = 0x220557DA
KEYWORDS = ("caught", "cheat", "story")            # 子串匹配, 大小写不敏感


def collect_packages(*root_paths):
    pkgs = []
    seen = set()
    for root in root_paths:
        if not root:
            continue
        r = Path(root)
        if r.is_file() and r.suffix.lower() == ".package":
            cand = [r]
        elif r.is_dir():
            cand = sorted(r.rglob("*.package"))
        else:
            cand = []
        for p in cand:
            key = str(p.resolve())
            if key not in seen:
                seen.add(key)
                pkgs.append(p)
    return pkgs


def parse_any(pkg):
    """返回 (package_path, [(instance, {key:text})...]) 或 None."""
    try:
        idx, err = safe_parse(str(pkg))
    except Exception as e:
        return None
    if err is not None or idx is None:
        return None
    try:
        b = get_backend("readonly").open(str(pkg))
    except Exception:
        return None
    out = []
    for e in idx.entries:
        if e.type_id != STBL:
            continue
        try:
            data = b.read_small_resource(e) or b""
        except Exception:
            continue
        m = P22.parse_stbl(data)
        if m:
            out.append((e.instance_id, m))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", nargs="?", default="WW_Nevely42_Animations.package")
    ap.add_argument("--dir", help="Mods 目录 (递归扫描 .package)")
    ap.add_argument("--game-dir", help="游戏安装目录 (递归扫描 .package/.ts4script 所在)")
    ap.add_argument("--all", action="store_true", help="列出每个命中 package 的每个实例 (默认每 package 只列首个命中实例)")
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: 源不存在 {src}"); return 2

    roots = [src, a.dir, a.game_dir]
    pkgs = collect_packages(src, a.dir, a.game_dir)
    print("=== STBL KEYWORD SCAN (caught|cheat|story, 大小写不敏感) ===")
    print(f"扫描 package 数 = {len(pkgs)}")
    print(f"关键词: {KEYWORDS}")
    print("")

    kw_re = re.compile("|".join(KEYWORDS), re.I)

    total_hits = 0
    per_pkg_first = {}   # package -> [(key, text, inst)]
    uniq_insts = set()
    any_hit_in_scan = False

    for pkg in pkgs:
        parsed = parse_any(pkg)
        if not parsed:
            continue
        pkg_local = pkg.name
        first_for_pkg = []
        for inst, m in parsed:
            for k, txt in m.items():
                if kw_re.search(txt):
                    total_hits += 1
                    any_hit_in_scan = True
                    uniq_insts.add(inst)
                    first_for_pkg.append((k, txt, inst))
                    if a.all or len(first_for_pkg) <= 1:
                        print(f"  [{pkg_local}]")
                        print(f"    text={txt}")
                        print(f"    key=0x{k:08X}")
                        print(f"    instance=0x{inst:016X}")
                        print(f"    package={pkg_local}")
        if first_for_pkg:
            per_pkg_first[pkg_local] = first_for_pkg

    print("")
    print("=== 汇总 ===")
    print(f"命中(文本条数)总数 = {total_hits}")
    print(f"唯一 STBL instance 数 = {len(uniq_insts)}")
    print(f"命中 package 数 = {len(per_pkg_first)}")
    for pkg_local, rows in per_pkg_first.items():
        keys = sorted({r[0] for r in rows})
        print(f"  {pkg_local}: {len(rows)} 条文本, key 样例 = " +
              ", ".join(f"0x{k:08X}" for k in keys[:10]) + (" ..." if len(keys) > 10 else ""))

    if not any_hit_in_scan:
        print("")
        print("!! 在 Mods/Game 的 .package STBL 中完全无 caught/cheat/story 命中。")
        print("   => 文本很可能不在 STBL, 而在 L18n runtime 表 / GameData 基础字符串:")
        print("      - 游戏安装目录 GameData 下的 STBL (.package) 需用同一扫描覆盖")
        print("      - 若仍无: 看 base 资源 / .ts4script 内 get_localized_string_id 的")
        print("        硬编码 key (sims4.localization / story progression 系统)")
        print("   => 继续查 L18n runtime 表 (见错误处理指引)。")
    print("")
    print("ZERO_WRITE_TO_MODS=YES (只读)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
