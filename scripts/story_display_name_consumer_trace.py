#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
story_display_name_consumer_trace.py —— 只读: 追 display_name 的消费位置

已确认链: animation_raw_display_name -> _create_sex_animation_instance(display_name)
本脚本不问构造, 而是找所有 消费 display_name 的地方 (读取方):
  - 扫描 TURBODRIVER_WickedWhims_Scripts.ts4script 内全部 code object
  - 找所有 LOAD_ATTR display_name
  - 过滤: 所在 class / function 名 或 ±30 指令窗口文本 含任一关键词
      SexAnimationInstance / animation / display / picker / menu / localized
  - 每个命中输出: pyc 文件, class/function, 前后 ±30 条指令,
     并在命中处/相关调用打标:
        <<< LOAD_ATTR display_name
        <<< CALL LocalizedString/TurboLocalizedString
        <<< get_localized_string_id
        <<< hash
只写 output/story_display_name_consumer_trace.txt。

fail-closed(只读): 文件缺->2; 非zip->3; 未命中 LOAD_ATTR display_name->4; 无 xdis->7; 正常 0。
ZERO_WRITE_TO_MODS=YES。

用法 (Windows):
  python scripts\story_display_name_consumer_trace.py "TURBODRIVER_WickedWhims_Scripts.ts4script"
      [--ctx 30] [--pyc 子串] [--out output/story_display_name_consumer_trace.txt]
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

CONTEXT_KEYWORDS = ("sexanimationinstance", "animation", "display", "picker", "menu", "localized")
TARGET_ATTR = "display_name"
CTX = 30


def get_opc(ver):
    return get_opcode_module(tuple(str(x) for x in ver[:2]), PythonImplementation.CPython)


def qualname(fn):
    return getattr(fn, "co_qualname", fn.co_name)


def walk_code(co, funcs, parent_class=None):
    parent_names = set(getattr(co, "co_names", ()))
    child_bodies = [s for s in co.co_consts if hasattr(s, "co_name")]
    cls = parent_class
    for sub in child_bodies:
        # 类体: 子 code 名在父 co_names 中 且 含方法(co_consts 里的函数)
        has_methods = any(hasattr(s0, "co_name") for s0 in getattr(sub, "co_consts", ()))
        sub_is_class = (sub.co_name in parent_names and has_methods)
        sub_cls = sub.co_name if sub_is_class else parent_class
        # 类体自身不算“方法”, 不加入 funcs; 只对其子方法递归
        if not sub_is_class:
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


def mark(ins):
    op = (ins.opname or "").lower()
    ar = (ins.argrepr or "").lower()
    if op == "load_attr" and ar == TARGET_ATTR:
        return "   <<< LOAD_ATTR display_name"
    if op.startswith("call") and ("localizedstring" in ar or "localized_string" in ar
                                  or "turbolocalizedstring" in ar):
        return "   <<< CALL LocalizedString/TurboLocalizedString"
    if op.startswith("call") and "get_localized_string_id" in ar:
        return "   <<< get_localized_string_id"
    if op.startswith("call") and ("hash_string" in ar or "string_hash" in ar or ar == "fnv"):
        return "   <<< hash"
    # 非 call 的 hash/本地化 引用 (LOAD_GLOBAL/LOAD_ATTR/LOAD_METHOD)
    if ("hash_string" in ar or "string_hash" in ar or "get_localized_string_id" in ar
            or "localizedstring" in ar or "localized_string" in ar):
        if op.startswith(("load_global", "load_attr", "load_method", "load_name")):
            return "   <<< (hash/localized 引用)"
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--ctx", type=int, default=CTX)
    ap.add_argument("--pyc", default="")
    ap.add_argument("--out", default="output/story_display_name_consumer_trace.txt")
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
    out.append("=== STORY DISPLAY_NAME CONSUMER TRACE (只读) ===")
    out.append("链: animation_raw_display_name -> _create_sex_animation_instance -> display_name")
    out.append("目标: 找所有 消费(读取) display_name 的位置")
    out.append(f"过滤关键词: {CONTEXT_KEYWORDS}")
    out.append(f"窗口: 前后 ±{w} 条指令")
    out.append("")

    total_hits = 0
    filtered_hits = 0
    seen_outputs = set()

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
            try:
                insns = list(Bytecode(fn, opc))
            except Exception:
                continue
            for i, ins in enumerate(insns):
                if (ins.opname or "") != "LOAD_ATTR" or (ins.argrepr or "") != TARGET_ATTR:
                    continue
                total_hits += 1
                lo = max(0, i - w)
                hi = min(len(insns), i + w + 1)
                # 过滤依据: 所在 class/function 命名上下文 (不用窗口文本,
                # 否则 display_name 自身恒命中 'display')
                ctx_txt = f"{cls or ''} {qualname(fn)}".lower()
                if not any(k in ctx_txt for k in CONTEXT_KEYWORDS):
                    continue
                filtered_hits += 1
                key = (pyc_name, cls, qualname(fn), insns[i].offset)
                if key in seen_outputs:
                    continue
                seen_outputs.add(key)
                out.append("#" * 72)
                out.append(f"pyc     : {pyc_name}")
                out.append(f"class   : {cls or '<module级>'}")
                out.append(f"function: {qualname(fn)}")
                out.append(f"offset  : L{insns[i].offset}   (±{w})")
                out.append("")
                for j in range(lo, hi):
                    line_txt = line(insns[j])
                    if j == i:
                        line_txt += "   <<< LOAD_ATTR display_name"
                    else:
                        line_txt += mark(insns[j])
                    out.append("    " + line_txt)
                out.append("")
                out.append("-" * 72)
                out.append("")

    if total_hits == 0:
        out.append(f"!! 全程未找到 LOAD_ATTR {TARGET_ATTR} (exit 4)")
        code = 4
    elif filtered_hits == 0:
        out.append(f"!! 找到 {total_hits} 处 LOAD_ATTR {TARGET_ATTR}，但均未通过过滤关键词"
                   f" {CONTEXT_KEYWORDS} (exit 4)")
        out.append("   可放宽 --ctx 或换关键词, 或全量 dump (去掉过滤)。")
        code = 4
    else:
        code = 0

    out.append("---")
    out.append(f"总 LOAD_ATTR display_name 命中: {total_hits}")
    out.append(f"通过过滤(已输出): {filtered_hits}")
    out.append("ZERO_WRITE_TO_MODS=YES (只读)")

    text = "\n".join(out)
    out_path = Path(a.out)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"已写入: {out_path}  (显示消费 hits={filtered_hits}/{total_hits})")
    except Exception as ex:
        print(f"WARN: 写文件失败: {ex}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
