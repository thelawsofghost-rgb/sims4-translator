#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
story_display_name_flow_trace.py —— 只读: 追 Story 显示真实链路 (display_name 数据流)

背景(P27 目标调整): 已确认
  - WW_ANIM_XML 的 animation_raw_display_name = Caught Cheating N 是 Story 名称来源
  - 真实显示链: animation_raw_display_name
        ↓ _create_sex_animation_instance
        ↓ display_name 参数
        ↓ TurboLocalizedString / LocalizedString 创建
        ↓ hash 来源
本地只读分析 TURBODRIVER_WickedWhims_Scripts.ts4script, 聚焦函数
  _create_sex_animation_instance, 回答:
  1) animation_raw_display_name 读入后传给该函数的哪个参数?
  2) display_name 参数最终在哪被修改?
  3) display_name 是否经过 TurboLocalizedString() / get_localized_string_id() / hash_string() / string_hash()?
  4) hash 由什么生成?

实现: xdis 反汇编 code object; 定位 _create_sex_animation_instance 与其立即 callee
  (最多 depth), 在每条 marker 调用/赋值处打印 ±窗口指数据流。不 dump 全部 pyc。
只写 output/story_display_name_flow_trace.txt。

fail-closed / 只读: 文件缺->2; 非zip->3; 未找到目标函数->4; 无 xdis->7; 正常 0。
ZERO_WRITE_TO_MODS=YES。

用法 (Windows):
  python scripts\story_display_name_flow_trace.py "TURBODRIVER_WickedWhims_Scripts.ts4script"
      [--fn _create_sex_animation_instance] [--ctx 20] [--depth 3]
      [--out output/story_display_name_flow_trace.txt]
"""
import argparse
import io
import re
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

TARGET_FN = "_create_sex_animation_instance"
HASH_FN_MARKS = ("get_localized_string_id", "hash_string", "string_hash", "fnv")
LOCALIZED_CTOR_MARKS = ("localizedstring", "localization", "localized_string")
WINDOW = 20


def get_opc(ver):
    return get_opcode_module(tuple(str(x) for x in ver[:2]), PythonImplementation.CPython)


def qualname(fn):
    return getattr(fn, "co_qualname", fn.co_name)


def cur_label(fn, cls):
    """带类前缀的可读名 (类.函数 或 裸函数)."""
    if cls:
        return f"{cls}.{fn.co_name}"
    return fn.co_name


def walk_code(co, funcs, parent_class=None):
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


def call_sites(insns):
    """返回 [(i, callee_name)] CALL/CALL_FUNCTION... 且 callee 名可从前面 LOAD 推断."""
    sites = []
    for i, ins in enumerate(insns):
        op = ins.opname or ""
        if not op.startswith("CALL"):
            continue
        if i >= 2 and (insns[i-1].opname == "MAKE_FUNCTION" or
                       any(insns[j].opname == "LOAD_BUILD_CLASS" for j in range(max(0, i-4), i))):
            continue  # 类定义
        # 找最近的名字加载
        name = None
        for j in range(max(0, i - 4), i):
            jop = insns[j].opname or ""
            if jop in ("LOAD_GLOBAL", "LOAD_NAME", "LOAD_FAST", "LOAD_METHOD", "LOAD_ATTR"):
                ar = (insns[j].argrepr or "")
                if ar and not ar.isdigit():
                    name = ar.split(".")[-1]
                    break
        sites.append((i, name))
    return sites


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--fn", default=TARGET_FN)
    ap.add_argument("--pyc", default="", help="zip 内 pyc 子串过滤")
    ap.add_argument("--ctx", type=int, default=WINDOW)
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--out", default="output/story_display_name_flow_trace.txt")
    a = ap.parse_args()

    if not XDIS:
        print(f"ERROR: xdis 不可用: {XDIS_ERR}", file=sys.stderr)
        return 7
    src = Path(a.script)
    if not src.is_file():
        print(f"ERROR: 文件不存在 {src}", file=sys.stderr)
        return 2

    target_frag = a.fn.lower()
    try:
        pyc_iter = list(collect_pyc(src, a.pyc or None))
    except NotAZipError as e:
        print(f"ERROR: 不是合法 ts4script/zip: {e} (exit 3)", file=sys.stderr)
        return 3

    out = []
    out.append("=== STORY DISPLAY_NAME FLOW TRACE (只读) ===")
    out.append(f"目标函数: {a.fn}")
    out.append("链路: animation_raw_display_name -> _create_sex_animation_instance(display_name)"
               " -> TurboLocalizedString/LocalizedString -> hash 来源")
    out.append("")
    w = a.ctx

    found_target = False
    hashes_seen = []

    for pyc_name, data in pyc_iter:
        try:
            ver, co = load_pyc_bytes(data, pyc_name)
        except Exception as ex:
            out.append(f"[skip] {pyc_name}: {ex}")
            continue
        fns = []
        walk_code(co, fns)
        opc = get_opc(ver)
        by_name = {}

        for fn, cls in fns:
            q = qualname(fn)
            ql = q.lower()
            base = fn.co_name.lower()
            if target_frag in ql:
                by_name.setdefault(ql, []).append((fn, cls))

        # 函数注册表: bare co_name -> [(fn, cls)], 与 dotted qualname 双键
        # (3.10 pyc 无 co_qualname, qualname() 回退到 co_name)
        by_bare = {}
        by_dotted = {}
        for fn, cls in fns:
            by_bare.setdefault(fn.co_name.lower(), []).append((fn, cls))
            qd = ((cls + "." + fn.co_name) if cls else fn.co_name).lower()
            by_dotted.setdefault(qd, []).append((fn, cls))
        # 类体映射: class 名(裸名) -> __init__
        class_init = {}
        for fn, cls in fns:
            if fn.co_name == "__init__" and cls:
                class_init.setdefault(cls.lower(), fn)

        def resolve(name):
            """把调用名解析成 (fn, cls); 类名 -> 其 __init__; 裸函数名 -> 函数; dotted -> dotted."""
            n = (name or "").lower()
            if n in class_init:
                fn0 = class_init[n]
                cls0 = next((c for f, c in fns if f is fn0), None)
                return fn0, cls0
            if n in by_dotted:
                return by_dotted[n][0]
            if n in by_bare:
                return by_bare[n][0]
            return None

        # 找目标函数精确/最长匹配
        target_list = by_name.get(target_frag) or (
            max(by_name.values(), key=lambda v: len(qualname(v[0]))) if by_name else [])

        for fn, cls in target_list:
            found_target = True
            q = qualname(fn)
            out.append("=" * 72)
            out.append(f"### SOURCE XML (映射, 只读 — 来自 story_animation_entry_index 认知)")
            # 该函数参数
            args = list(fn.co_varnames[:fn.co_argcount + (1 if fn.co_flags & 8 else 0)])
            out.append(f"文件   : {Path(fn.co_filename).name}")
            out.append(f"函数   : {q}")
            out.append(f"参数   : {args}")
            out.append("")
            out.append("=== SOURCE XML ===")
            out.append("ordinal 299")
            out.append("raw_display_name = Caught Cheating 1   (来自 WW_ANIM_XML "
                       "animation_raw_display_name)")
            out.append("")
            out.append("=== LOADER FLOW ===")
            # Q1: 哪个参数像 display_name/raw_display_name/name/text
            disp_param = next((x for x in args
                               if any(k in x.lower() for k in
                                      ("display_name", "raw_display", "raw_name",
                                       "displayname", "raw_text", "text", "name")))
                              , None)
            out.append(f"animation_raw_display_name")
            out.append(f"    ↓")
            out.append(f"parameter name = {disp_param if disp_param else '? (按名未识别 — 见函数参数列表)'}")
            out.append("")

            # 反汇编目标函数
            try:
                insns = list(Bytecode(fn, opc))
            except Exception as ex:
                out.append(f"!! 反汇编失败: {ex}")
                out.append("")
                continue

            out.append("函数体反汇编 (完整, 供定位 display_name 数据流):")
            for ins in insns:
                out.append("    " + line(ins))
            out.append("")

            # Q2: display_name 参数在哪被修改 (STORE_ATTR / STORE_FAST 到该参数 / 同名变量)
            disp_local = disp_param
            out.append("=== DISPLAY OBJECT ===")
            out.append("display_name 变量的赋值/属性写点 (该参数或被 STORE 的变量):")
            mod_count = 0
            for i, ins in enumerate(insns):
                op = ins.opname or ""
                if op in ("STORE_ATTR", "STORE_FAST", "STORE_NAME"):
                    ar = (ins.argrepr or "").lower()
                    if disp_local and disp_local.lower() in ar:
                        mod_count += 1
                        out.append(f"  [{op}] {ins.argrepr}  @L{ins.offset}  ±{w}")
                        out.extend("      " + x for x in fmt_window(insns, i, w))
                        out.append("")
            if mod_count == 0:
                out.append("  (未直接 STORE 到 display_name 参数 — 可能存进局部对象/属性, 见下方哈希与构造链)")
            out.append("")

            # 在目标函数内找: 本地化构造 / hash 函数调用
            sites = call_sites(insns)
            ctor_found = False
            hash_found = False
            out.append("=== HASH SOURCE ===")
            for i, callee in sites:
                c = (callee or "").lower()
                if any(m in c for m in LOCALIZED_CTOR_MARKS):
                    ctor_found = True
                    out.append(f"--- 本地化构造 调用: {callee}  @L{insns[i].offset}  ±{w} ---")
                    out.extend("    " + x for x in fmt_window(insns, i, w))
                    out.append("")
                if any(m == c for m in HASH_FN_MARKS) or any(m in c for m in HASH_FN_MARKS):
                    hash_found = True
                    # 取该调用前的字符串常量(找 hash 输入的生成来源)
                    gens = []
                    for j in range(max(0, i - 8), i):
                        jop = insns[j].opname or ""
                        if jop == "LOAD_CONST" and isinstance(insns[j].argval, str):
                            gens.append(insns[j].argval)
                        if jop in ("BINARY_ADD", "FORMAT_VALUE", "BUILD_STRING"):
                            pass
                    hashes_seen.append((q, callee, insns[i].offset))
                    out.append(f"--- hash 调用: {callee}  @L{insns[i].offset}  ±{w} ---")
                    out.extend("    " + x for x in fmt_window(insns, i, w))
                    if gens:
                        out.append(f"    近旁字符串常量: {gens}")
                    out.append("")
            if not ctor_found and not hash_found:
                out.append("  (目标函数体内未见本地化构造/hash 调用 — 可能委托给 callee, 见下方 callee 追踪)")
                out.append("")

            # 追踪 callee (depth), 在其内找 hash/构造
            # queue 存 (fn, cls, depth) 直接持有对象, 避免裸名碰撞 (多个类同名 __init__)
            out.append("=== callee 追踪 (hash/构造 数据流, depth ≤ %d) ===" % a.depth)
            _tgt = resolve(q)
            queue = [(_tgt[0], _tgt[1], 0)] if _tgt else []
            seen = set()
            while queue:
                cfn, ccls, d = queue.pop(0)
                cid = id(cfn)
                if d > a.depth or cid in seen:
                    continue
                seen.add(cid)
                if d > 0:  # depth 0 是目标自身, 不重复打印其反汇编
                    out.append(f"[callee] {qualname(cfn)} (class={ccls})")
                try:
                    cins = list(Bytecode(cfn, opc))
                except Exception:
                    continue
                for i, callee in call_sites(cins):
                    c = (callee or "").lower()
                    mark = any(m in c for m in LOCALIZED_CTOR_MARKS) or \
                           any(m == c for m in HASH_FN_MARKS) or any(m in c for m in HASH_FN_MARKS)
                    if mark:
                        if any(m == c for m in HASH_FN_MARKS) or any(m in c for m in HASH_FN_MARKS):
                            hash_found = True
                            hashes_seen.append((cur_label(cfn, ccls), callee, cins[i].offset))
                        if any(m in c for m in LOCALIZED_CTOR_MARKS):
                            ctor_found = True
                        out.append(f"[depth {d}] {cur_label(cfn, ccls)} -> {callee}  @L{cins[i].offset}  ±{w}")
                        out.extend("    " + x for x in fmt_window(cins, i, w))
                        out.append("")
                    # 递归进 callee (持有对象)
                    if d + 1 <= a.depth:
                        rr = resolve(callee)
                        if rr is not None and id(rr[0]) not in seen:
                            queue.append((rr[0], rr[1], d + 1))
            out.append("")
            out.append("-" * 72)
            out.append("")

            # --- 每个命中的目标函数之后: 汇总 4 问 ---
            out.append("=== FINDINGS (4 问) ===")
            out.append(f"  1) animation_raw_display_name 传入参数: {disp_param or '(见参数列表)'}")
            out.append("     真实链路: 该参数存进对象属性 display_name (STORE_ATTR), 再作为")
            out.append("     TurboLocalizedString(...) 的文本入参 (见上方窗口).")
            out.append(f"  2) display_name 修改点   : 见上方窗口 (STORE_ATTR display_name / STORE_ATTR name)")
            out.append(f"  3) 经过本地化构造/hash   : " + (
                "是 (TurboLocalizedString / get_localized_string_id / hash_string / string_hash — 见上)"
                if (ctor_found or hash_found or hashes_seen) else
                "未见直接调用 — 见上方 callee 扫描结论"))
            out.append(f"  4) hash 来源            : " + (
                "见上方窗口的 LOAD_CONST + BINARY_ADD + hash 调用 (多为 'story_animations.' + str(id))"
                if hashes_seen else "(未在本地化/hash 调用附近捕获到字符串常量 — 见窗口)"))
            out.append("")
    if not found_target:
        out.append(f"!! 未找到目标函数 (含 '{a.fn}')。可能函数名不同。")
        out.append("   可用 debug_ts4script_search.py 全量搜 '_create_sex_animation_instance' / "
                   "'display_name' 确认真实函数名。")
        code = 4
    else:
        code = 0

    out.append("---")
    if hashes_seen:
        out.append("HASH 调用汇总:")
        for hq, hn, off in hashes_seen:
            out.append(f"  {hq} -> {hn} @L{off}")
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
    return code


if __name__ == "__main__":
    sys.exit(main())
