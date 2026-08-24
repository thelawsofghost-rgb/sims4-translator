#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P19 SexAnimationInstance 构造调用追踪 (只读, 不生成包)

背景: P18 确认 get_display_name() 读 self.display_name; 非字符串时进
      get_l18n_service().get_localized_string_id()。现在要定位
      SexAnimationInstance(...) 构造时 display_name 参数的实际来源,
      以及 Story vs Normal 传参类型差异。

目标 (animations_loader.pyc::_create_sex_animation_instance):
  1. 定位 SexAnimationInstance 构造调用位置
  2. 该调用 display_name 参数的实际来源 (str / hash / int / localization id)
  3. Story(299) 与 普通(124) 传参差异:
       - str (原始字符串)
       - hash/int (哈希值)
       - localization id (本地化键, 非字符串)
  4. 输出: CALL 参数窗口 / display_name 参数来源 / Story vs Normal 差异

方法 (只读, xdis):
  - 从 .ts4script (zip) 取 animations_loader.pyc
  - 定位 _create_sex_animation_instance
  - 全量反汇编
  - 找 callee 含 "SexplAnimationInstance" 的 CALL
  - 显示该 CALL 的完整参数窗口 (之前所有 LOAD/CALL 指令)
  - 分离 kwargs: CALL_FUNCTION_KW 前有 LOAD_CONST 'display_name' 等 key
  - 对 display_name 参数回溯来源:
       值来自 LOAD_CONST <int/str> / LOAD_GLOBAL localize / LOAD_GLOBAL hash
       / LOAD_FAST(参数) / LOAD_ATTR / STORE_FAST 局部等
  - 报告该来源是 "str 字面量" / "hash/本地化键" / "参数透传"

fail-closed: 源缺->2; 无 WW->3; 目录缺->4; xdis 缺失->7;
  loader.pyc 缺失->5; 函数缺失->8; 构造调用缺失->9; 正常 0。
不改任何文件, 不写 Mods。ZERO_WRITE_TO_MODS=YES。

用法 (Windows, 只读):
  pip install xdis
  python scripts\ww_animation_p19_constructor_call.py `
      "<SRC.package>" --dir "C:\\...\\Mods" [--out-dir output/ww_p19]
"""
import argparse
import csv
import importlib.util
import io as _io
import sys
import zipfile as _zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import ww_animation_p15_pyc_deep_dive as P15
import ww_animation_p7_story_chain_audit as _p7

if not P15.XDIS:
    print("ERROR: 缺依赖 xdis —— 请先: pip install xdis", file=sys.stderr)
    sys.exit(7)
from xdis.load import load_module_from_file_object
from xdis.disasm import Bytecode as XBytecode

LOADER_NAME = "animations_loader.pyc"
TARGET_FN = "_create_sex_animation_instance"
CTOR_KEY = "SexAnimationInstance"          # 构造函数名 (子串)
ARG_FOCUS = "display_name"                 # 要追踪的参数名

OUT_DIR = Path("output/ww_p19")

# 值类型启发
STR_CONSTS = set()
LOC_HINTS = ("localize", "get_localized_string_id", "get_localized_string",
             "get_l18n_service", "localization", "stbl", "get_string",
             "get_hash", "hash", "localize_string")


def load_loader_from_dir(d):
    for sp in [p for p in d.rglob("*.ts4script") if p.is_file()]:
        try:
            with _zipfile.ZipFile(sp) as z:
                for name in z.namelist():
                    if Path(name).name == LOADER_NAME:
                        return sp, name, z.read(name)
        except Exception:
            continue
    return None


def find_func(co, name):
    if co.co_name == name:
        return co
    for sub in co.co_consts:
        if hasattr(sub, "co_name"):
            r = find_func(sub, name)
            if r:
                return r
    return None


def fmt_ins(i, offmap=True):
    return f"{i.offset:4d} {i.opname:22s} {i.argrepr or ''}"


def named_callee(lines, call_i):
    """找 CALL 的调用对象名: 回溯连续 LOAD_*/IMPORT_* 段第一命名."""
    run = []
    j = call_i - 1
    while j >= 0 and j >= call_i - 10 and lines[j].opname.startswith(("LOAD_", "IMPORT_")):
        run.append(j); j -= 1
    for k in reversed(run):
        if lines[k].opname in ("LOAD_GLOBAL", "LOAD_NAME", "LOAD_METHOD",
                               "IMPORT_NAME", "LOAD_METHOD_HANDLE"):
            return lines[k].argrepr
        if lines[k].opname == "LOAD_ATTR":
            return lines[k].argrepr
    return "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--dir", required=True)
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

    ww_first, werr = _p7.load_xml(src)
    if ww_first is None:
        print(f"ERROR: {werr}", file=sys.stderr); return 3

    got = load_loader_from_dir(d)
    if got is None:
        print(f"ERROR: 未在目录 {d} .ts4script 内找到 {LOADER_NAME}", file=sys.stderr); return 5
    ts4path, member, data = got

    try:
        res = load_module_from_file_object(_io.BytesIO(data), filename=LOADER_NAME)
    except Exception as ex:
        print(f"ERROR: xdis 解析失败: {ex}", file=sys.stderr); return 6
    ver, co = res[0], res[3]; impl = res[4]
    opc = P15.get_opc(ver)

    fn = find_func(co, TARGET_FN)
    if fn is None:
        print(f"ERROR: loader 内未找到函数 {TARGET_FN}", file=sys.stderr); return 8
    lines = list(XBytecode(fn, opc))

    L = []
    L.append("=== P19 SexAnimationInstance 构造调用追踪 (只读) ===")
    L.append(f"源 = {src.name}")
    L.append(f"ts4script = {ts4path}")
    L.append(f"member = {member}   pyc size = {len(data)}")
    L.append(f"python = {ver}  impl = {impl}")
    L.append(f"目标函数 = {TARGET_FN}  (line={fn.co_firstlineno}, 指令数={len(lines)})")
    L.append(f"参数 = {list(fn.co_varnames)}")
    consts = {c for c in fn.co_consts if isinstance(c, str)}
    if consts:
        L.append(f"字符串常量 = {sorted(consts)}")
    L.append("")

    # 找 SexAnimationInstance 构造调用
    ctor_calls = []
    for i, it in enumerate(lines):
        if it.opname not in ("CALL_FUNCTION", "CALL_FUNCTION_EX", "CALL_METHOD", "CALL",
                             "CALL_FUNCTION_KW", "CALL_FUNCTION_KW_3_11"):
            continue
        callee = named_callee(lines, i)
        if CTOR_KEY.lower() in callee.lower():
            ctor_calls.append((i, it, callee))

    if not ctor_calls:
        print(f"ERROR: 未找到 {CTOR_KEY} 构造调用 (callee 含 SexAnimationInstance)", file=sys.stderr)
        L.append(f"!!! 未找到 {CTOR_KEY} 构造调用")
        L.append("ZERO_WRITE_TO_MODS=YES (只读)")
        (out_dir / "p19_constructor_call.txt").write_text("\n".join(L), encoding="utf-8")
        return 9

    for ci, cit, ccallee in ctor_calls:
        L.append(f"=== 构造调用 #{len(ctor_calls)>1 and ci or 0} @[{ci}] offset={cit.offset} : {ccallee} ===")
        # 参数窗口: 该 CALL 之前 12 条指令
        L.append(f"  [CALL 参数窗口 (前 12 条)]")
        for j in range(max(0, ci - 12), ci + 1):
            L.append(f"      [{j:4d}] {fmt_ins(lines[j])}")
        L.append("")

        # 分离 kwargs (CALL_FUNCTION_KW 前有 LOAD_CONST key 序列)
        kw = {}
        if cit.opname.startswith("CALL_FUNCTION_KW") or cit.opname == "CALL_FUNCTION_KW_3_11":
            # 收集 key 常量 (紧邻 CALL 前的 LOAD_CONST <str> 是 key)
            kws = []
            j = ci - 1
            while j >= 0 and len(kws) < 5 and lines[j].opname == "LOAD_CONST":
                if isinstance(lines[j].argrepr, str) or (lines[j].argrepr or "").startswith('"'):
                    kws.append(lines[j].argrepr)
                j -= 1
            L.append(f"  kwargs keys (近 CALL): {list(reversed(kws))}")
        # 定位 display_name 参数: 找 LOAD_CONST 'display_name' 之后那个 CALL 的参数值
        L.append(f"  [display_name 参数来源追踪]")
        focus_found = False
        # ---- 对每个 CALL_FUNCTION_KW: 解析 kw 元组 -> 依序回溯各实参值 source, 命中 display_name ----
        for j, it in enumerate(lines):
            if it.opname not in ("CALL_FUNCTION_KW", "CALL_FUNCTION_KW_3_11"):
                continue
            # 找紧邻的 kw 元组常量 (LOAD_CONST (..,..)) 在 CALL 前
            tup_idx = None
            for k in range(j - 1, max(-1, j - 3), -1):
                if lines[k].opname == "LOAD_CONST" and "(" in (lines[k].argrepr or "") and "'" in lines[k].argrepr:
                    tup_idx = k
                    break
            if tup_idx is None:
                continue
            raw = lines[tup_idx].argrepr
            # 解析元组项: 去括号分割, 去引号
            names = [x.strip().strip("'\"") for x in raw.strip("()").split(",")]
            n_kw = len(names)
            # 从元组往前收集 n_kw 个值 (每个 kw 一个 LOAD/CALL/STORE 值)
            vals = []
            k = tup_idx - 1
            while k >= 0 and len(vals) < n_kw:
                v = lines[k]
                if v.opname.startswith("LOAD_CONST"):
                    vals.append((v.offset, f"const:{v.argrepr}"))
                elif v.opname.startswith("CALL"):
                    vals.append((v.offset, f"call->{named_callee(lines, k)}"))
                elif v.opname == "LOAD_FAST":
                    vals.append((v.offset, f"param/local:{v.argrepr}"))
                elif v.opname == "LOAD_ATTR":
                    vals.append((v.offset, f"attr:{v.argrepr}"))
                elif v.opname == "LOAD_GLOBAL":
                    vals.append((v.offset, f"global:{v.argrepr}"))
                else:
                    k -= 1
                    continue
                k -= 1
            vals.reverse()
            focus_found = True
            L.append(f"      CALL_FUNCTION_KW args (kw元组 {names}):")
            for idx2, nm in enumerate(names):
                vsrc = vals[idx2][1] if idx2 < len(vals) else "?"
                mark = "  <== display_name" if nm == ARG_FOCUS else ""
                L.append(f"          {nm} = {vsrc}{mark}")
        if not focus_found:
            L.append(f"      (字节码中未见带 kw 元组的调用; display_name 可能为位置参数或被改名)")

        # 该 CALL 的所有命名调用 (窗口内) -> 类型判定
        L.append(f"  [CALL 前 12 条内命名调用 -> 值类型线索]")
        for j in range(max(0, ci - 12), ci):
            if lines[j].opname.startswith("CALL"):
                cc = named_callee(lines, j)
                tag = " <LOC/hash>" if any(h in cc.lower() for h in LOC_HINTS) else ""
                L.append(f"      [{j:4d}] call->{cc}{tag}")
        L.append("")

    # Story vs Normal: 从字节码分支结构推断
    L.append("=== Story vs Normal 传参差异 (字节码层) ===")
    # 依赖 display 局部的 STORE 来源定类型
    disp_stores = {}
    for j, it in enumerate(lines):
        if it.opname == "STORE_FAST" and it.argrepr == "display":
            src = "?"
            for k in range(j - 1, max(-1, j - 4), -1):
                v = lines[k]
                if v.opname in ("LOAD_FAST", "LOAD_ATTR", "LOAD_GLOBAL"):
                    src = f"{v.opname}:{v.argrepr}"; break
                if v.opname == "BINARY_SUBSCR":
                    src = "_loc[...] (本地化返回值取元素)"; break
                if v.opname.startswith("CALL"):
                    src = f"call->{named_callee(lines, k)}"; break
            disp_stores[j] = src
    L.append("  display 局部各 STORE 点来源 (决定传入类型):")
    for j, it in enumerate(lines):
        if it.opname == "STORE_FAST" and it.argrepr == "display":
            src = disp_stores.get(j, "?")
            ty = "localization/id/hash(非字符串)" if ("_loc" in src or "call" in src or "BINARY" in src) else "str"
            L.append(f"      [{j:4d}] display = {src}  -> 传入类型: {ty}")
    L.append("  分支映射: 含 _localize/hash/_loc 的调用路径 = STORY(本地化键);")
    L.append("             直接读 animation_raw_display_name/stage_name = Normal(str)。")
    L.append("  => 预期: Story 传 localization id/hash; Normal 传原始字符串。")
    L.append("")
    # 全局查找 localize/l18n/hash 调用
    loccalls = []
    for i, it in enumerate(lines):
        if it.opname.startswith("CALL"):
            cc = named_callee(lines, i)
            if any(h in cc.lower() for h in LOC_HINTS):
                loccalls.append((i, cc))
    if loccalls:
        L.append(f"  函数内 localization/STBL/hash 相关调用:")
        for i, cc in loccalls:
            L.append(f"      [{i:4d}] {cc}")
    else:
        L.append("  函数内未见直接 localization/STBL/hash 调用 (构造时可能直接传 str;"
                 " Story 的本地化可能在别处/构造后设置)")
    # 所有命名调用
    L.append("  [全函数命名调用]")
    seen = set()
    for i, it in enumerate(lines):
        if it.opname.startswith("CALL"):
            cc = named_callee(lines, i)
            if cc not in seen:
                seen.add(cc)
                L.append(f"      {cc}")
    L.append("")
    L.append("ZERO_WRITE_TO_MODS=YES (只读, 不生成包, 未修改任何文件)")
    txt = "\n".join(L)
    (out_dir / "p19_constructor_call.txt").write_text(txt, encoding="utf-8")
    with open(out_dir / "p19_constructor_call.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["idx", "offset", "op", "arg"])
        for i, it in enumerate(lines):
            w.writerow([i, it.offset, it.opname, it.argrepr or ""])
    print(txt)
    print(f"OUT_TXT={out_dir/'p19_constructor_call.txt'}")
    print("P19_CONSTRUCTOR_CALL=OK (只读)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
