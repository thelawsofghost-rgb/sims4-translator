#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
story_display_name_source_trace.py —— 只读: 锁定 SexAnimationInstance.__init__, 追 display_name 参数来源

链路已知: animation_raw_display_name -> _create_sex_animation_instance -> display_name
本脚本只在 class SexAnimationInstance 的 function __init__ 内分析:
  1) __init__ 是否含 display_name 参数 (co_varnames[:co_argcount])
  2) 找 STORE_ATTR display_name, 输出前后 ±40 条指令
  3) 若 display_name 来源是:
        LOAD_FAST(display_name)
        LOAD_FAST(animation_raw_display_name)
        TurboLocalizedString(...)
     显示完整链 (从 LOAD_FAST 取值 -> 传参与构造 -> STORE_ATTR)
不搜所有 LOAD_ATTR display_name; 不扫其他 class; 只锁定 SexAnimationInstance.__init__。
只写 output/story_display_name_source_trace.txt (不 dump 全部 pyc)。

fail-closed(只读): 文件缺->2; 非zip->3; 未找到 class SexAnimationInstance 的 __init__->4; 无 xdis->7; 正常 0。
ZERO_WRITE_TO_MODS=YES。

用法 (Windows):
  python scripts\story_display_name_source_trace.py "TURBODRIVER_WickedWhims_Scripts.ts4script"
      [--ctx 40] [--pyc 子串] [--out output/story_display_name_source_trace.txt]
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

TARGET_CLASS = "sexanimationinstance"
TARGET_FUNC = "__init__"
TARGET_ATTR = "display_name"
CTX = 40


def get_opc(ver):
    return get_opcode_module(tuple(str(x) for x in ver[:2]), PythonImplementation.CPython)


def walk_code(co, funcs, parent_class=None):
    parent_names = set(getattr(co, "co_names", ()))
    for sub in co.co_consts:
        if not hasattr(sub, "co_name"):
            continue
        has_methods = any(hasattr(s, "co_name") for s in getattr(sub, "co_consts", ()))
        is_class = (sub.co_name in parent_names and has_methods)
        sub_cls = sub.co_name if is_class else parent_class
        if not is_class:
            funcs.append((sub, sub_cls))
        walk_code(sub, funcs, sub_cls)


def load_pyc(data, name):
    tup = load_module_from_file_object(io.BytesIO(data), name)
    ver = tup[0]
    co = next(x for x in tup if hasattr(x, "co_name"))
    return ver, co


class NotAZipError(Exception):
    pass


def collect_pyc(path, pyc_filter):
    p = Path(path)
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


def line(ins):
    s = f"L{ins.offset:6d} {(ins.opname or ''):22s}"
    if ins.argrepr:
        s += f" ({ins.argrepr})"
    return s


def is_load_fast_of(ins, name):
    return (ins.opname or "") == "LOAD_FAST" and (ins.argrepr or "") == name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--ctx", type=int, default=CTX)
    ap.add_argument("--pyc", default="")
    ap.add_argument("--out", default="output/story_display_name_source_trace.txt")
    a = ap.parse_args()
    w = a.ctx

    if not XDIS:
        print(f"ERROR: xdis 不可用: {XDIS_ERR}", file=sys.stderr)
        return 7
    src = Path(a.script)
    if not src.is_file():
        print(f"ERROR: 文件不存在 {src}", file=sys.stderr)
        return 2
    try:
        pyc_iter = list(collect_pyc(src, a.pyc or None))
    except NotAZipError as e:
        print(f"ERROR: 不是合法 ts4script/zip: {e} (exit 3)", file=sys.stderr)
        return 3

    out = []
    out.append("=== STORY DISPLAY_NAME SOURCE TRACE (只读) ===")
    out.append(f"锁定 class: {TARGET_CLASS}  function: {TARGET_FUNC}")
    out.append("链: animation_raw_display_name -> _create_sex_animation_instance -> display_name")
    out.append("#" * 72)

    found_target = False
    total_stores = 0
    chain_shown = 0

    for pyc_name, data in pyc_iter:
        try:
            ver, co = load_pyc(data, pyc_name)
        except Exception as ex:
            out.append(f"[skip] {pyc_name}: {ex}")
            continue
        fns = []
        walk_code(co, fns)
        opc = get_opc(ver)
        for fn, cls in fns:
            if fn.co_name != TARGET_FUNC or (cls or "").lower() != TARGET_CLASS:
                continue
            found_target = True
            out.append("")
            out.append(f"pyc     : {pyc_name}")
            out.append(f"class   : {cls}")
            out.append(f"function: {fn.co_name}")

            # Q1: display_name 是否为参数
            args = list(fn.co_varnames[: fn.co_argcount])
            out.append("")
            out.append(f"=== Q1: __init__ arguments ===")
            out.append(f"  arguments = {args}")
            has_disp_arg = TARGET_ATTR in args
            out.append(f"  含 display_name 参数? : {'是' if has_disp_arg else '否'}")
            # 是否含 animation_raw_display_name 参数
            has_raw_arg = any("raw" in v and "display" in v for v in args)
            out.append(f"  含 animation_raw_display_name 类参数? : {'是' if has_raw_arg else '否'}")

            insns = list(Bytecode(fn, opc))
            # Q2: 所有 STORE_ATTR display_name
            outs = [i for i, ins in enumerate(insns)
                    if (ins.opname or "") == "STORE_ATTR" and (ins.argrepr or "") == TARGET_ATTR]
            out.append("")
            out.append(f"=== Q2: STORE_ATTR {TARGET_ATTR} 位置 ===")
            if not outs:
                out.append(f"  未找到 STORE_ATTR {TARGET_ATTR}。")
            for oi in outs:
                total_stores += 1
                out.append(f"  -> L{insns[oi].offset}  (±{w} 指令)")
                lo = max(0, oi - w)
                hi = min(len(insns), oi + w + 1)
                out.append("  " + "-" * 66)
                for j in range(lo, hi):
                    txt = "    " + line(insns[j])
                    if j == oi:
                        txt += "   <<< STORE_ATTR display_name"
                    out.append(txt)

            # Q3: 显示来源完整链 (display_name / animation_raw_display_name / TurboLocalizedString)
            out.append("")
            out.append("=== Q3: display_name 来源完整链 ===")
            chain_txts = []
            for ins in insns:
                ar = (ins.argrepr or "").lower()
                if is_load_fast_of(ins, "display_name"):
                    chain_txts.append((ins.offset, "LOAD_FAST(display_name)     <- 参数直接来源"))
                if is_load_fast_of(ins, "animation_raw_display_name"):
                    chain_txts.append((ins.offset, "LOAD_FAST(animation_raw_display_name)  <- raw 参数来源"))
                if (ins.opname or "").startswith("call") and "turbolocalizedstring" in ar:
                    chain_txts.append((ins.offset, "CALL TurboLocalizedString(...)"))
            if not chain_txts:
                out.append("  未见 LOAD_FAST(display_name) / LOAD_FAST(animation_raw_display_name) / TurboLocalizedString 调用。")
            for off, desc in chain_txts:
                idx = next((i for i, ins in enumerate(insns) if ins.offset == off), None)
                chain_shown += 1
                out.append(f"  [{desc}]  @L{off}")
                if idx is not None:
                    lo = max(0, idx - w)
                    hi = min(len(insns), idx + w + 1)
                    for j in range(lo, hi):
                        out.append("      " + line(insns[j]))
                    out.append("")
            out.append("")
            out.append("#" * 72)

    if not found_target:
        out.append(f"!! 未找到 class {TARGET_CLASS} 的 function {TARGET_FUNC} (exit 4)")
        out.append("   可能类名不同, 或用 --pyc 限定文件后用 debug_ts4script_search.py 确认。")
        code = 4
    else:
        code = 0

    out.append("---")
    if total_stores == 0 and found_target:
        out.append(f"!! 找到 __init__, 但 {'未见' if True else ''} STORE_ATTR {TARGET_ATTR} → display_name 可能并非设在此处")
    out.append(f"STORE_ATTR {TARGET_ATTR} 计数: {total_stores}")
    out.append(f"来源链标记计数: {chain_shown}")
    out.append("ZERO_WRITE_TO_MODS=YES (只读)")

    text = "\n".join(out)
    out_path = Path(a.out)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"已写入: {out_path}")
    except Exception as ex:
        print(f"WARN: 写文件失败: {ex}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
