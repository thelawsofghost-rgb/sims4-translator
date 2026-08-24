#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
debug_ts4script_search.py —— 只读扫描 .ts4script(zip) 内的 .pyc, 用 xdis 反汇编,
在字节码中搜索目标字符串, 定位所在 pyc / 函数 / 命中指令附近 ±20 行。

目标字符串 (大小写不敏感, 也搜 co_names):
  story_animations
  get_localized_string_id
  get_l18n_service
  display_name
  animation_id

输出 (每条命中):
  pyc 路径 (zip 内)
  函数名 (+ 文件名/行号)
  命中指令附近 ±20 行汇编 (含 LOAD_CONST 的字符串与上下文)

只读: 不修改任何文件。ZERO_WRITE_TO_MODS=YES。

用法:
  python scripts/debug_ts4script_search.py <file.ts4script> [--pyc .pyc 过滤] [--no-consts] [--no-names]
"""
import argparse
import io
import sys
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from xdis.load import load_module_from_file_object
    from xdis.disasm import Bytecode
    from xdis.op_imports import get_opcode_module, PythonImplementation
    XDIS = True
except Exception as ex:
    XDIS = False
    XDIS_ERR = ex

TARGETS = (
    "story_animations",
    "get_localized_string_id",
    "get_l18n_service",
    "display_name",
    "animation_id",
)

WINDOW = 20   # 命中指令附近行数


def get_opc(ver):
    v = tuple(str(x) for x in ver[:2])
    return get_opcode_module(v, PythonImplementation.CPython)


def walk_code(co, funcs):
    funcs.append(co)
    for sub in co.co_consts:
        if hasattr(sub, "co_name"):
            walk_code(sub, funcs)


def search_func(fn, opc, targets, hits, want_consts, want_names):
    insns = list(Bytecode(fn, opc))
    lower = {t.lower(): t for t in targets}
    hit_idxs = set()              # 命中的指令索引
    detail = {}                   # ins_index -> (类型, 目标串原文)
    for i, ins in enumerate(insns):
        found = None
        if want_names and ins.opname and ins.argrepr:
            for lc, orig in lower.items():
                if lc in ins.argrepr.lower():
                    found = ("name", orig); break
        if found is None and want_consts and ins.opname == "LOAD_CONST" \
                and isinstance(ins.argval, str):
            for lc, orig in lower.items():
                if lc in ins.argval.lower():
                    found = ("const", orig); break
        if found:
            hit_idxs.add(i)
            detail[i] = found
    if not hit_idxs:
        return
    # 展开 ±WINDOW 行覆盖区间 (按指令索引)
    cover_idxs = set()
    for i in hit_idxs:
        for ii in range(max(0, i - WINDOW), min(len(insns), i + WINDOW + 1)):
            cover_idxs.add(ii)
    hits.append((fn, insns, sorted(cover_idxs), detail))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script", help=".ts4script (zip)")
    ap.add_argument("--pyc", help="只处理匹配该子串的 pyc 路径")
    ap.add_argument("--no-consts", action="store_true", help="关闭字符串常量搜索")
    ap.add_argument("--no-names", action="store_true", help="关闭 co_names 搜索")
    a = ap.parse_args()

    src = Path(a.script)
    if not src.is_file():
        print(f"ERROR: 文件不存在 {src}"); return 2
    if not XDIS:
        print(f"ERROR: xdis 不可用: {XDIS_ERR}; 请 pip install xdis"); return 7
    if not zipfile.is_zipfile(src):
        print(f"ERROR: 不是合法 zip: {src}"); return 3

    print("=== TS4SCRIPT SEARCH ===")
    print(f"目标: {src.name}")
    print(f"搜索字符串: {', '.join(TARGETS)}")
    print(f"窗口: ±{WINDOW} 行指令")
    print(f"模式: consts={'开' if not a.no_consts else '关'} / names={'开' if not a.no_names else '关'}")
    print("")

    want_consts = not a.no_consts
    want_names = not a.no_names

    z = zipfile.ZipFile(src)
    total_pyc = 0
    total_func_hits = 0
    for info in z.infolist():
        name = info.filename
        if not name.endswith(".pyc"):
            continue
        if a.pyc and a.pyc not in name:
            continue
        total_pyc += 1
        data = z.read(info)
        try:
            tup = load_module_from_file_object(io.BytesIO(data), name)
            ver = tup[0]
            co = next(x for x in tup if hasattr(x, "co_name"))  # 代码对象
        except Exception as ex:
            print(f"[skip] {name}: 无法解析 -> {ex}")
            continue
        fns = []
        walk_code(co, fns)
        opc = get_opc(ver)
        hits = []
        for fn in fns:
            search_func(fn, opc, TARGETS, hits, want_consts, want_names)
        if not hits:
            continue
        total_func_hits += len(hits)
        print(f"### pyc: {name}   (Python {ver})")
        for fn, insns, cover_idxs, detail in hits:
            print(f"  -- 函数: {fn.co_name}  (行 {fn.co_firstlineno}, 文件 {Path(fn.co_filename).name})")
            for i in cover_idxs:
                ins = insns[i]
                mark = ""
                if i in detail:
                    typ, s = detail[i]
                    mark = f"   <<< {typ}: {s}"
                line = f"    L{i:4d} {ins.offset:4d} {ins.opname or '':16s}"
                if ins.argrepr:
                    line += f" ({ins.argrepr})"
                print(line + mark)
            print("")
    z.close()
    print(f"扫描 .pyc 总数 = {total_pyc}   命中函数 = {total_func_hits}")
    if total_func_hits == 0:
        print("")
        print("  无命中。若 .ts4script 内确实含这些字符串, 可能:")
        print("  - 字符串被拆/加盐/生成 (如 key 由运行时拼接, 字面量不直接出现)")
        print("  - 在别的 .ts4script / .package 的模块里")
        print("  => 需结合 debug_stbl_reverse_hash (STBL) 与 L18n runtime 表定位。")
    print("")
    print("ZERO_WRITE_TO_MODS=YES (只读)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
