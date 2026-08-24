#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
debug_localized_string_constructor.py —— 只读: 扫描 ts4script, 只追 LocalizedString 对象创建

范围: TURBODRIVER_WickedWhims_Scripts.ts4script (或单 .pyc)
目标 (不是 text 搜索 / 不是 get_localized_string_id 全量分析):
  1. TurboLocalizedString 类定义
  2. __init__ 方法
  3. 所有 TurboLocalizedString(...) 构造调用
  4. 所有 STORE_ATTR hash
  5. 所有 STORE_ATTR text/string/value/name

重点: 弄清 "hash 从哪里来"、"文本从哪里来"。
不输出全部 pyc 反汇编, 只输出 LocalizedString 相关片段:
  类定义 / __init__ 全指令 / 每处构造调用 ±20 条指令 / 每处 STORE_ATTR(hash|text|string|value|name) ±20 条指令。

解码 (pyc -> *.pyc 字节) 用 xdis (load_module_from_file_object), 结果写
  output/localized_string_trace.txt
并同步打到 stdout。

只读: 不修改任何文件。ZERO_WRITE_TO_MODS=YES。

用法:
  python scripts/debug_localized_string_constructor.py <TURBODRIVER_WickedWhims_Scripts.ts4script>
  [--pyc 子串] [--out output/localized_string_trace.txt] [--ctx 20]
退出码: 0=有命中, 2=文件缺失, 3=非zip, 4=无命中, 7=无 xdis
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

CLASS_NAME_MARK = "localizedstring"        # 类名含此子串 (大小写不敏感)
HASH_ATTRS = {"hash"}
TEXT_ATTRS = {"text", "string", "value", "name"}
WINDOW = 20                                # 构造/赋值 前后的指令窗口

import struct as _struct  # noqa: E402


def get_opc(ver):
    return get_opcode_module(tuple(str(x) for x in ver[:2]), PythonImplementation.CPython)


def walk_code(co, funcs, parent_class=None):
    """递归收集 code object; 推断类归属: 若子体 co 名出现在本(co)的 co_names 且含 __init__ 子体, 则子体是类体."""
    parent_names = set(getattr(co, "co_names", ()))
    child_bodies = [s for s in co.co_consts if hasattr(s, "co_name")]
    cls = parent_class
    for sub in child_bodies:
        sub_is_class = (sub.co_name in parent_names and
                        any(getattr(s, "co_name", None) == "__init__"
                            for s in getattr(sub, "co_consts", ()) if hasattr(s, "co_name")))
        sub_cls = sub.co_name if sub_is_class else parent_class
        funcs.append((sub, sub_cls))
        walk_code(sub, funcs, sub_cls)


def qualname(fn):
    return getattr(fn, "co_qualname", fn.co_name)


def is_class_body(co):
    return False  # 类体归属由 walk_code 推断 (保留占位避免引用错误)


def is_init_of_class(fn, cls):
    """是 __init__ 且属于 LocalizedString 类?"""
    if fn.co_name != "__init__":
        return False
    if cls and CLASS_NAME_MARK in cls.lower():
        return True
    q = qualname(fn).lower()
    return CLASS_NAME_MARK in q


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
        raise NotAZipError(str(p))
    with zipfile.ZipFile(p) as z:
        for info in z.infolist():
            if not info.filename.endswith(".pyc"):
                continue
            if pyc_filter and pyc_filter not in info.filename:
                continue
            yield info.filename, z.read(info)


class NotAZipError(Exception):
    pass


def line(ins):
    s = f"L{ins.offset:6d} {(ins.opname or ''):20s}"
    if ins.argrepr:
        s += f" ({ins.argrepr})"
    return s


def fmt_window(insns, ctr, w):
    lo = max(0, ctr - w)
    hi = min(len(insns), ctr + w + 1)
    return [line(x) for x in insns[lo:hi]]


def find_constructor_calls(insns):
    """找 'LOAD_* <名含 LocalizedString> .... CALL*' 的构造调用位置. 排除 BUILD_CLASS 类定义."""
    calls = []
    for i, ins in enumerate(insns):
        op = ins.opname or ""
        if not op.startswith("CALL"):
            continue
        # 排除类定义: 前面有 LOAD_BUILD_CLASS + MAKE_FUNCTION -> BUILD_CLASS 调用
        if i >= 2 and (insns[i-1].opname == "MAKE_FUNCTION" or
                       any(insns[j].opname == "LOAD_BUILD_CLASS" for j in range(max(0, i-4), i))):
            continue
        # 往回看最多 4 条指令找 LOAD_GLOBAL/LOAD_NAME/LOAD_FAST 名字含 LocalizedString
        for j in range(max(0, i - 4), i):
            jop = insns[j].opname or ""
            if jop in ("LOAD_GLOBAL", "LOAD_NAME", "LOAD_FAST", "LOAD_METHOD") and insns[j].argrepr and \
               CLASS_NAME_MARK in insns[j].argrepr.lower():
                calls.append((i, insns[j].argrepr))
                break
    return calls


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--pyc", help="zip 内 pyc 子串过滤")
    ap.add_argument("--out", default="output/localized_string_trace.txt")
    ap.add_argument("--ctx", type=int, default=WINDOW, help="构造/赋值前后指令窗口")
    a = ap.parse_args()

    if not XDIS:
        print(f"ERROR: xdis 不可用: {XDIS_ERR}"); return 7
    src = Path(a.script)
    if not src.is_file():
        print(f"ERROR: 文件不存在 {src}"); return 2

    out = []
    out.append("=== LOCALIZED STRING CONSTRUCTOR TRACE (只读) ===")
    try:
        pyc_iter = list(collect_pyc(src, a.pyc))
    except NotAZipError as e:
        print(f"ERROR: 不是合法 ts4script/zip: {e} (exit 3)")
        return 3

    out.append("范围: TurboLocalizedString 类定义 / __init__ / 构造调用 / STORE_ATTR hash|text|string|value|name")
    out.append("模式: 只追 LocalizedString 相关, 不分析 get_localized_string_id, 不 dump 全部 pyc。")
    out.append("")
    w = a.ctx

    n_class = 0
    n_init = 0
    n_call = 0
    n_hash = 0
    n_text = 0

    for pyc_name, data in pyc_iter:
        try:
            ver, co = load_pyc_bytes(data, pyc_name)
        except Exception as ex:
            out.append(f"[skip] {pyc_name}: 无法解析 -> {ex}")
            continue
        fns = []
        walk_code(co, fns)
        opc = get_opc(ver)

        for fn, cls in fns:
            q = qualname(fn)
            ql = q.lower()

            # --- 1) 类定义: 通过 __init__ 归属推断 ---
            if is_init_of_class(fn, cls):
                n_class += 1
                out.append("=== CLASS ===")
                out.append(f"file: {pyc_name}")
                out.append(f"class: {cls if cls else (q.rsplit('.', 1)[0] if '.' in q else q)}")
                # 关联 __init__
                out.append("")
                out.append("=== INIT ===")
                n_init += 1
                shown = f"{cls}.__init__" if cls else q
                out.append(f"function: {shown}  (line {fn.co_firstlineno}, {Path(fn.co_filename).name})")
                out.append(f"arguments: {list(fn.co_varnames[:fn.co_argcount + (1 if fn.co_flags & 8 else 0)])}")
                insns = list(Bytecode(fn, opc))
                out.append("instructions:")
                for ins in insns:
                    out.append("    " + line(ins))
                out.append("")
                # 该 __init__ 内的 STORE_ATTR hash/text
                for i, ins in enumerate(insns):
                    op = ins.opname or ""
                    if op == "STORE_ATTR":
                        ar = (ins.argrepr or "").lower()
                        if ar in HASH_ATTRS:
                            n_hash += 1
                            out.append("=== STORE_ATTR hash (__init__) ===")
                            out.append(f"line: {ins.offset}  window ±{w}")
                            out.extend("    " + x for x in fmt_window(insns, i, w))
                            out.append("")
                        elif ar in TEXT_ATTRS:
                            n_text += 1
                            out.append(f"=== STORE_ATTR {ins.argrepr} (__init__) ===")
                            out.append(f"line: {ins.offset}  window ±{w}")
                            out.extend("    " + x for x in fmt_window(insns, i, w))
                            out.append("")
                continue

            # --- 3) 构造调用 (非 __init__ 函数里) ---
            insns = list(Bytecode(fn, opc))
            calls = find_constructor_calls(insns)
            for ctr, callee in calls:
                n_call += 1
                n_class += 1
                out.append("=== CONSTRUCTOR CALL ===")
                out.append(f"file: {pyc_name}")
                out.append(f"function: {q}  (line {fn.co_firstlineno})")
                out.append(f"line: {insns[ctr].offset}  callee={callee}  window ±{w}")
                out.extend("    " + x for x in fmt_window(insns, ctr, w))
                out.append("")

            # --- 4/5) STORE_ATTR hash / text|string|value|name (任意函数) ---
            for i, ins in enumerate(insns):
                op = ins.opname or ""
                if op != "STORE_ATTR":
                    continue
                ar = (ins.argrepr or "").lower()
                if ar in HASH_ATTRS:
                    n_hash += 1
                    out.append("=== STORE_ATTR hash ===")
                    out.append(f"file: {pyc_name}")
                    out.append(f"function: {q}  (line {fn.co_firstlineno})")
                    out.append(f"line: {ins.offset}  window ±{w}")
                    out.extend("    " + x for x in fmt_window(insns, i, w))
                    out.append("")
                elif ar in TEXT_ATTRS:
                    n_text += 1
                    out.append(f"=== STORE_ATTR {ins.argrepr} ===")
                    out.append(f"file: {pyc_name}")
                    out.append(f"function: {q}  (line {fn.co_firstlineno})")
                    out.append(f"line: {ins.offset}  window ±{w}")
                    out.extend("    " + x for x in fmt_window(insns, i, w))
                    out.append("")

    n_class_adj = n_class
    out.append("---")
    out.append(f"类(__init__ 归属) = {n_init}")
    out.append(f"构造调用          = {n_call}")
    out.append(f"STORE_ATTR hash   = {n_hash}")
    out.append(f"STORE_ATTR text/string/value/name = {n_text}")
    if n_init == 0 and n_call == 0 and n_hash == 0:
        out.append("")
        out.append("!! 未命中任何 LocalizedString 相关信号。可能:")
        out.append("  - 类名不含 'LocalizedString' (如 LocalizedStringTuple / LocalizationKey / GameLocalizedString)")
        out.append("  - hash/文本在其它 pyc (非本 script)")
        out.append("  - 构造调用未紧跟 LOAD(4 指令内) / 用 CALL_METHOD 且形式不同")
        out.append("  => 用 debug_ts4script_search.py 全量搜 'LocalizedString' 确认真实类/构造名。")
    out.append("")
    out.append("ZERO_WRITE_TO_MODS=YES (只读)")

    text = "\n".join(out)
    out_path = Path(a.out)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"trace 已写入: {out_path}")
    except Exception as ex:
        print(f"WARN: 写文件失败: {ex}", file=sys.stderr)
    print(text)
    return 0 if (n_init or n_call or n_hash) else 4


if __name__ == "__main__":
    sys.exit(main())
