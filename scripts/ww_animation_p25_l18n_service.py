#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_animation_p25_l18n_service.py —— 只读解析 TurboL18NService.get_localized_string_id

定位到 turbolib2/services/l18n_service.pyc 后, 不再全量搜索。本脚本只解析
  TurboL18NService.get_localized_string_id
一个方法, 输出:
  1) 方法签名/co_name/行号/文件名 (确认是这个方法)
  2) 全指令反汇编, 标注:
       - 参数来源 (LOAD_FAST self / arg)
       - 属性访问 LOAD_ATTR (链式 self.xxx 的成员来源)
       - 调用点 CALL_FUNCTION / CALL_METHOD (调了哪些函数/方法)
       - RETURN_VALUE (返回了什么)
       - STBL / hash 相关指令 (fnv / hash / stbl / get_stbl / load_stbl ...)
  3) 数据流: 输入 hash 如何转换成最终 STBL lookup:
       - 找 LOAD_CONST 字符串常量 (如 "stbl" / "STBL" / 表名)
       - 找 hash 计算指令链 (LOAD_GLOBAL/LOAD_ATTR + CALL 到 hash/fnv 函数)
       - 找 STBL 资源读取调用 (get_resource / load_stbl / stbl.string ...)
  4) 结论: 该服务的 lookup 机制 (hash -> 哪张 STBL / 哪个 instance)

只读: 不修改任何文件。ZERO_WRITE_TO_MODS=YES。

用法:
  python scripts/ww_animation_p25_l18n_service.py <file.ts4script|file.pyc> [--pyc 子串]
默认: 自动在 zip 内找 turbolib2/services/l18n_service.pyc; 也可 --pyc 指定子串,
或直接传单 .pyc 文件。
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

TARGET_METHOD = "get_localized_string_id"
TARGET_CLASS = "TurboL18NService"
DEFAULT_PYC = "turbolib2/services/l18n_service.pyc"
STBL_KEYWORDS = ("stbl", "string", "hash", "fnv", "lookup", "resource", "instance")


def get_opc(ver):
    return get_opcode_module(tuple(str(x) for x in ver[:2]), PythonImplementation.CPython)


def walk_code(co, funcs):
    funcs.append(co)
    for sub in co.co_consts:
        if hasattr(sub, "co_name"):
            walk_code(sub, funcs)


def qualname(fn):
    # 向上找外层容器名 (递归时累计) —— 简单用 co_qualname 若存在
    return getattr(fn, "co_qualname", fn.co_name)


def is_target_method(fn):
    if fn.co_name != TARGET_METHOD:
        return False
    q = qualname(fn)
    if TARGET_CLASS in q:
        return True
    # 退化: 方法名匹配且文件名含 l18n_service
    if "l18n" in str(fn.co_filename).lower():
        return True
    return False


def load_pyc_from_bytes(data, name):
    tup = load_module_from_file_object(io.BytesIO(data), name)
    ver = tup[0]
    co = next(x for x in tup if hasattr(x, "co_name"))
    return ver, co


def collect_pyc_paths(script_path, pyc_filter):
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


def show_func(ver, fn, opc, out):
    out.append("")
    out.append(f"== TurboL18NService.get_localized_string_id ==")
    out.append(f"  函数名(qualname) : {qualname(fn)}")
    out.append(f"  co_name          : {fn.co_name}")
    out.append(f"  行号             : {fn.co_firstlineno}")
    out.append(f"  源文件           : {Path(fn.co_filename).name}")
    out.append(f"  参数 (co_varnames len={len(fn.co_varnames)}): {list(fn.co_varnames[:10])}")
    str_consts = [c for c in fn.co_consts if isinstance(c, str)]
    if str_consts:
        out.append(f"  字符串常量 len={len(str_consts)}:")
        for c in str_consts:
            mark = "  <<STBL/hash" if any(k in c.lower() for k in STBL_KEYWORDS) else ""
            out.append(f"      {c!r}{mark}")
    out.append("")

    insns = list(Bytecode(fn, opc))
    out.append(f"  -- 指令数 {len(insns)} ----------")
    # 记录 LOAD_ATTR 链, 调用点, RETURN
    stack = []   # 简易值栈 (仅跟踪 load 来源名)
    for i, ins in enumerate(insns):
        op = ins.opname or ""
        tag = ""
        # 参数来源
        if op == "LOAD_FAST":
            tag = f"  <— arg/local: {ins.argrepr}" if i < len(fn.co_varnames) * 2 + 8 else "  <— local"
        if op == "LOAD_ATTR":
            tag = f"  <— attr: self.{ins.argrepr}" if i > 0 and insns[i-1].opname == "LOAD_FAST" and i <= 3 else "  <— attr"
        if op in ("CALL_FUNCTION", "CALL_METHOD", "CALL_FUNCTION_EX", "CALL_FUNCTION_KW"):
            tag = f"  <— 调用: {ins.argrepr}"
        if op == "RETURN_VALUE":
            tag = "  <— RETURN"
        if any(k in op.lower() for k in ("LOAD_METHOD", "IMPORT", "GLOBAL")):
            if ins.argrepr:
                tag = f"  <— 名字: {ins.argrepr}"
        # STBL/hash 高亮
        low = (ins.argrepr or "").lower()
        if any(k in low for k in STBL_KEYWORDS):
            hl = " <<<STBL/HASH"
            tag = (tag + hl) if tag else hl
        line = f"    L{i:4d} {ins.offset:3d} {op:18s}"
        if ins.argrepr:
            line += f" ({ins.argrepr})"
        out.append(line + tag)
    out.append("")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script", help=".ts4script (zip) 或 .pyc")
    ap.add_argument("--pyc", default=DEFAULT_PYC, help=f"zip 内 pyc 子串 (默认 {DEFAULT_PYC})")
    a = ap.parse_args()

    if not XDIS:
        print(f"ERROR: xdis 不可用: {XDIS_ERR}"); return 7
    src = Path(a.script)
    if not src.is_file():
        print(f"ERROR: 文件不存在 {src}"); return 2

    out = []
    out.append("=== P25: TurboL18NService.get_localized_string_id (只读反汇编) ===")
    out.append(f"目标类/方法: {TARGET_CLASS}.{TARGET_METHOD}")
    out.append(f"pyc 定位: {a.pyc}")
    out.append("")

    found_any = False
    for pyc_name, data in collect_pyc_paths(src, a.pyc):
        try:
            ver, co = load_pyc_from_bytes(data, pyc_name)
        except Exception as ex:
            out.append(f"[skip] {pyc_name}: 无法解析 -> {ex}")
            continue
        fns = []
        walk_code(co, fns)
        opc = get_opc(ver)
        for fn in fns:
            if not is_target_method(fn):
                continue
            found_any = True
            out.append(f"### 命中方法: {pyc_name}  (Python {ver})")
            show_func(ver, fn, opc, out)
        if not found_any:
            out.append(f"[在 {pyc_name} 中未找到 {TARGET_CLASS}.{TARGET_METHOD}]")
            mnames = sorted({qualname(f) for f in fns})
            out.append(f"  该 pyc 内方法样例: {mnames[:20]}")

    out.append("---")
    if not found_any:
        out.append("")
        out.append("!! 未找到目标方法。可能:")
        out.append("  - 类名不是 TurboL18NService (查看上面方法样例定位真实类名)")
        out.append("  - 方法名不同 (如 get_localized_string / localized_string_id)")
        out.append("  - 该方法在别的 pyc (换 --pyc 或直接给单 .pyc)")
        out.append("  => 用 debug_ts4script_search.py 先确认真正的方法/类名。")
    out.append("")
    out.append("ZERO_WRITE_TO_MODS=YES (只读)")

    print("\n".join(out))
    return 0 if found_any else 4


if __name__ == "__main__":
    sys.exit(main())
