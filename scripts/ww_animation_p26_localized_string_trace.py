#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_animation_p26_localized_string_trace.py —— 只读反向追 localized_string 对象创建

前提: get_localized_string_id 只 return hash. 所以反向追 localized_string 对象
在哪里被创建 / 被赋值 hash. 本脚本在 .ts4script(或单 .pyc) 全 pyc 内, 扫描每个
code object, 检测下列"构造/赋值/字符串"信号:

  目标信号 (大小写不敏感, 命中即输出):
    1. LocalizedString(          —— 构造调用 (CALL* 且 callee 名含 LocalizedString)
    2. TurboLocalizedString      —— 类名/常量/属性名
    3. .hash=                    —— STORE_ATTR name=="hash" (对象被赋 hash)
    4. story_animations          —— 字符串常量/属性 (Story 分支 key 前缀)
    5. _localize 返回值          —— CALL* 到 *_localize 后紧跟 RETURN_VALUE

输出:
  - pyc 路径
  - 函数名 (qualname + 行号)
  - 该函数全指令反汇编, 命中行标注信号类型
  - 汇总: 找到的所有 story_animations 常量 / hash 赋值点 / LocalizedString 构造点
    => 呈现 Story 分支最终生成的 hash (由输入字符串算出的 key 或直接 .hash= 赋值)

只读: 不修改任何文件。ZERO_WRITE_TO_MODS=YES。

用法:
  python scripts/ww_animation_p26_localized_string_trace.py <file.ts4script|file.pyc>
  [--pyc 子串过滤] [--no-consts] [--no-names]
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

SIGNALS = (
    "localizedstring",   # LocalizedString( 构造
    "turbolocalizedstring",
    "story_animations",
    "_localize",
)
HASH_ATTR = "hash"      # .hash= 赋值


def get_opc(ver):
    return get_opcode_module(tuple(str(x) for x in ver[:2]), PythonImplementation.CPython)


def walk_code(co, funcs):
    funcs.append(co)
    for sub in co.co_consts:
        if hasattr(sub, "co_name"):
            walk_code(sub, funcs)


def qualname(fn):
    return getattr(fn, "co_qualname", fn.co_name)


def analyze_fn(fn, opc, want_consts, want_names):
    """扫描单函数, 返回 (命中的指令索引集, 命中详情{idx:(信号,原文)}, 该函数字符串常量)."""
    insns = list(Bytecode(fn, opc))
    hit = {}
    str_consts = [c for c in fn.co_consts if isinstance(c, str)]

    for i, ins in enumerate(insns):
        op = ins.opname or ""
        low_arg = (ins.argrepr or "").lower()
        low_op = op.lower()
        found = None
        # 1) 构造/类名: 调用名或属性名/常量含信号
        if want_names and low_arg:
            for s in SIGNALS:
                if s in low_arg:
                    if op.startswith("CALL"):
                        found = ("构造/调用", s)
                    elif op == "STORE_ATTR" or op == "LOAD_ATTR":
                        found = ("属性", s)
                    else:
                        found = ("名字", s)
                    break
        # 2) 字符串常量含信号 (story_animations / LocalizedString / TurboLocalizedString)
        if found is None and want_consts and op == "LOAD_CONST" and isinstance(ins.argval, str):
            for s in SIGNALS:
                if s in ins.argval.lower():
                    found = ("const", s)
                    break
        # 3) .hash= 赋值
        if found is None and op == "STORE_ATTR" and (ins.argrepr or "").lower() == HASH_ATTR:
            found = ("hash赋值", ".hash=")
        # 4) _localize 后紧跟 RETURN (返回值)
        if op.startswith("CALL") and "_localize" in low_arg:
            for j in range(i + 1, min(i + 4, len(insns))):
                if insns[j].opname == "RETURN_VALUE":
                    found = ("_localize返回值", "_localize -> RETURN")
                    break
        if found:
            hit[i] = found
    return insns, hit, str_consts


def load_pyc_bytes(data, name):
    tup = load_module_from_file_object(io.BytesIO(data), name)
    ver = tup[0]
    co = next(x for x in tup if hasattr(x, "co_name"))
    return ver, co


def collect_pyc(script_path, pyc_filter):
    p = Path(script_path)
    if p.is_file() and p.suffix.lower() == ".pyc":
        yield p.name, p.read_bytes()
        return
    if not zipfile.is_zipfile(p):
        raise SystemExit(f"ERROR: 不是合法 ts4script/zip: {p} (exit 3)")
    with zipfile.ZipFile(p) as z:
        for info in z.infolist():
            if not info.filename.endswith(".pyc"):
                continue
            if pyc_filter and pyc_filter not in info.filename:
                continue
            yield info.filename, z.read(info)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--pyc", help="zip 内 pyc 子串过滤")
    ap.add_argument("--no-consts", action="store_true")
    ap.add_argument("--no-names", action="store_true")
    a = ap.parse_args()

    if not XDIS:
        print(f"ERROR: xdis 不可用: {XDIS_ERR}"); return 7
    src = Path(a.script)
    if not src.is_file():
        print(f"ERROR: 文件不存在 {src}"); return 2

    out = []
    out.append("=== P26: localized_string 对象创建反向追 (只读) ===")
    out.append("信号: LocalizedString( / TurboLocalizedString / .hash= / story_animations / _localize 返回值")
    out.append("模式: consts=" + ("开" if not a.no_consts else "关") + " names=" + ("开" if not a.no_names else "关"))
    out.append("")

    want_consts = not a.no_consts
    want_names = not a.no_names
    total_hits = 0
    all_story_keys = []

    for pyc_name, data in collect_pyc(src, a.pyc):
        try:
            ver, co = load_pyc_bytes(data, pyc_name)
        except Exception as ex:
            out.append(f"[skip] {pyc_name}: 无法解析 -> {ex}")
            continue
        fns = []
        walk_code(co, fns)
        opc = get_opc(ver)
        for fn in fns:
            insns, hit, str_consts = analyze_fn(fn, opc, want_consts, want_names)
            if not hit:
                continue
            total_hits += 1
            out.append(f"### pyc: {pyc_name}  (Python {ver})")
            out.append(f"  -- 函数: {qualname(fn)}  (行 {fn.co_firstlineno}, {Path(fn.co_filename).name})")
            # story_animations 字符串常量收集
            for c in str_consts:
                if "story_animations" in c.lower():
                    sv = f"{c[0]}{c[1]}" if c.startswith(("f'", "f\"")) else c
                    all_story_keys.append((pyc_name, qualname(fn), sv))
            # 全指令打印, 命中标注
            for i, ins in enumerate(insns):
                line = f"    L{i:4d} {ins.offset:3d} {(ins.opname or ''):18s}"
                if ins.argrepr:
                    line += f" ({ins.argrepr})"
                if i in hit:
                    kind, sig = hit[i]
                    line += f"   <<< {kind}: {sig}"
                out.append(line)
            out.append("")
    out.append("---")
    out.append(f"命中函数总数 = {total_hits}")
    if all_story_keys:
        out.append("")
        out.append("== story_animations 字符串常量 (Story 分支 key 前缀) ==")
        seen = set()
        for pyc_name, fn, s in all_story_keys:
            if s not in seen:
                seen.add(s)
                out.append(f"  {s!r}   (from {pyc_name}::{fn})")
    if total_hits == 0:
        out.append("")
        out.append("!! 未命中任何信号。可能:")
        out.append("  - localized_string 对象在别的 pyc / .package 模块里")
        out.append("  - 构造名不同 (如 LocalizedStringTuple / LocalizationKey)")
        out.append("  - 哈希不是 'story_animations.' 前缀 (换 key 前缀再查)")
        out.append("  => 用 debug_ts4script_search.py 全量确认真实构造/类名。")
    out.append("")
    out.append("ZERO_WRITE_TO_MODS=YES (只读)")
    print("\n".join(out))
    return 0 if total_hits else 4


if __name__ == "__main__":
    sys.exit(main())
