#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P18 Story animation localization 来源审计 (只读, 不再改 WW_ANIM_XML)

背景: P17 已确认 animation_display_name 也不影响 Story 显示。
      已排除 raw_display_name / display_name / clip-ANIM_RCOL /
      actor graph / 外部 registry。
      显示源应在运行时 localization (animation_id -> text key/hash -> STBL)。

目标: 深挖 animation_instance.pyc 的三个方法:
    - get_display_name()
    - get_original_string_display_name()
    - get_animation_id()
  找 animation_id -> text key/hash -> STBL 的路径。

对每个方法:
    1) 全量反汇编 (xdis)
    2) 定位 RETURN 指令, 回溯返回值的来源 (LOAD_FAST/LOAD_ATTR/调用结果)
    3) 检测是否调用 localization/STBL/hash/key 相关函数
       (get_localized_string/get_string/localize/STBL/localization/
        get_hash/hash/get_text/get_final_string 等)
    4) 记录该方法读取/写入的 self 属性与常量(候选 key/本地化键)

输出:
    - display_name 返回值来源
    - original_string_display_name 来源
    - 是否调用 localization/STBL/hash
    - (若字节码可确定) Caught Cheating 1 对应的 key/hash

fail-closed: 源缺->2; 无 WW XML->3; 目录缺->4; xdis 缺失->7;
  找不到 animation_instance.pyc->5; 目标方法缺失->6; 正常 0。
只读: 不改文件, 不写 Mods。ZERO_WRITE_TO_MODS=YES。

用法 (Windows, 只读):
  pip install xdis
  python scripts\ww_animation_p18_story_localization.py `
      "<SRC.package>" --dir "C:\\...\\Mods" [--out-dir output/ww_p18]
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
import ww_animation_p15_pyc_deep_dive as P15   # 复用 xdis 装载 + get_opc/walk_code
import ww_animation_p7_story_chain_audit as _p7

if not P15.XDIS:
    print("ERROR: 缺依赖 xdis —— 请先: pip install xdis", file=sys.stderr)
    sys.exit(7)
from xdis.load import load_module_from_file_object
from xdis.disasm import Bytecode as XBytecode

INSTANCE_NAME = "animation_instance.pyc"   # zip 内 basename
TARGET_METHODS = ("get_display_name", "get_original_string_display_name",
                  "get_animation_id")

# localization/STBL/hash 相关函数名 (子串匹配)
LOC_HINTS = ("get_localized_string", "get_string", "localize", "stbl", "localization",
             "localized", "get_hash", "hash", "get_text", "get_final_string",
             "translate", "get_localization_table", "shell", "get_localized")

# 候选 key/本地化常量 (字符串常量, 近似 key)
KEY_HINTS = ("display", "story_stbl", "stbl", "_key", "localized_key",
             "string_id", "text_key", "display_key", "animation_id", "_hash")

OUT_DIR = Path("output/ww_p18")


def find_instance_from_dir(d):
    """从 Mods 目录所有 .ts4script zip 内找 animation_instance.pyc -> bytes."""
    for sp in [p for p in d.rglob("*.ts4script") if p.is_file()]:
        try:
            with _zipfile.ZipFile(sp) as z:
                for name in z.namelist():
                    if Path(name).name == INSTANCE_NAME:
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


def fmt_ins(i):
    a = i.argrepr or ""
    return f"{i.offset:4d} {i.opname:22s} {a}"


def named_calls(fn, lines):
    """返回 {call_idx: callee_name} —— 追踪 LOAD_GLOBAL/NAME/METHOD -> CALL.
    (CPython: f(a..) 先 LOAD f 再 LOAD a; 取连续 LOAD 段最老祖=可调用对象)"""
    last_named = {}
    enc_off = [i for i, it in enumerate(lines) if it.opname == "LOAD_CONST"]
    # 用 offset 为键; 简化: 扫描建立 offset->name
    off_name = {}
    for i, it in enumerate(lines):
        if it.opname in ("LOAD_GLOBAL", "LOAD_NAME", "LOAD_METHOD", "IMPORT_NAME",
                         "LOAD_METHOD_HANDLE"):
            off_name[it.offset] = it.argrepr
    out = []
    for i, it in enumerate(lines):
        if it.opname not in ("CALL_FUNCTION", "CALL_FUNCTION_EX", "CALL_METHOD", "CALL"):
            continue
        run = []
        j = i - 1
        while j >= 0 and j >= i - 8 and lines[j].opname.startswith(("LOAD_", "IMPORT_")):
            run.append(j); j -= 1
        callee = "?"
        for k in reversed(run):   # 从最老 -> 最近, 第一个命名即调用对象
            if lines[k].offset in off_name:
                callee = off_name[lines[k].offset]
                break
        out.append((i, it, callee))
    return out


def method_analysis(fn, opc, L):
    lines = list(XBytecode(fn, opc))
    L.append(f"--- 方法 {fn.co_name}  line={fn.co_firstlineno}  指令数={len(lines)} ---")
    L.append(f"   co_names: {list(fn.co_names)}")
    L.append(f"   co_varnames: {list(fn.co_varnames)}")
    consts = {c for c in fn.co_consts if isinstance(c, str)}
    if consts:
        L.append(f"   字符串常量: {sorted(consts)}")
    L.append("")

    L.append("   [全量反汇编]")
    for i, it in enumerate(lines):
        L.append(f"      [{i:4d}] {fmt_ins(it)}")
    L.append("")

    # 命名调用
    calls = named_calls(fn, lines)
    L.append("   [命名调用]")
    loc_hits = [c for c in calls if any(h in c[2].lower() for h in LOC_HINTS)]
    for i, it, callee in calls:
        mark = " <LOC>" if any(h in callee.lower() for h in LOC_HINTS) else ""
        L.append(f"      [{i:4d}] call -> {callee}{mark}")
    L.append(f"   << localization/STBL/hash 相关调用数: {len(loc_hits)} >>")
    L.append("")

    # RETURNs 来源
    L.append("   [RETURN 来源]")
    ret_sources = []
    for i, it in enumerate(lines):
        if it.opname != "RETURN_VALUE":
            continue
        src = []
        for j in range(i - 1, max(-1, i - 6), -1):
            if lines[j].opname.startswith("LOAD_"):
                src.append(f"{lines[j].opname}:{lines[j].argrepr}")
                break
            if lines[j].opname in ("CALL_FUNCTION", "CALL_FUNCTION_EX", "CALL_METHOD", "CALL"):
                # 找该 CALL 的 callee
                callee = next((c[2] for c in calls if c[0] == j), "?")
                src.append(f"CALL->{callee}")
                break
        ret_sources.append((i, src[0] if src else "?"))
    for i, s in ret_sources:
        L.append(f"      [{i:4d}] RETURN 值来源: {s}")
    L.append("")

    # self 属性读写
    attrs = {}
    for i, it in enumerate(lines):
        if it.opname in ("LOAD_ATTR", "STORE_ATTR"):
            attrs.setdefault(it.opname, []).append(it.argrepr)
    if attrs:
        L.append(f"   [属性读写] LOAD_ATTR={dict.fromkeys(attrs.get('LOAD_ATTR', []))}")
        L.append(f"                STORE_ATTR={dict.fromkeys(attrs.get('STORE_ATTR', []))}")
    L.append("")
    return calls, ret_sources


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

    got = find_instance_from_dir(d)
    if got is None:
        print(f"ERROR: 未在目录 {d} 的 .ts4script 内找到 {INSTANCE_NAME}", file=sys.stderr)
        return 5
    ts4path, member, data = got

    try:
        res = load_module_from_file_object(_io.BytesIO(data), filename=INSTANCE_NAME)
    except Exception as ex:
        print(f"ERROR: xdis 解析 {INSTANCE_NAME} 失败: {ex}", file=sys.stderr); return 6
    ver, co = res[0], res[3]; impl = res[4]
    opc = P15.get_opc(ver)

    L = []
    L.append("=== P18 Story localization 来源审计 (只读) ===")
    L.append(f"源 = {src.name}")
    L.append(f"ts4script = {ts4path}")
    L.append(f"member = {member}   pyc size = {len(data)}")
    L.append(f"python = {ver}  impl = {impl}")
    L.append("")

    funcs = []; P15.walk_code(co, funcs)
    L.append(f"模块内函数: {', '.join(f.co_name for f in funcs)}")
    L.append("")

    all_calls = {}
    ret_src_by_method = {}
    found_methods = []
    for tname in TARGET_METHODS:
        fn = None
        for f in funcs:
            if f.co_name == tname:
                fn = f; break
        if fn is None:
            L.append(f"!!! 未找到方法 {tname} (继续)")
            ret_src_by_method[tname] = None
            continue
        found_methods.append(tname)
        calls, rets = method_analysis(fn, opc, L)
        all_calls[tname] = calls
        ret_src_by_method[tname] = rets

    # ---- 汇总结论 ----
    L.append("=== 结论 ===")
    for tname in TARGET_METHODS:
        rets = ret_src_by_method.get(tname)
        calls = all_calls.get(tname) or []
        L.append(f"[{tname}]")
        if rets is None:
            L.append("   (方法不存在)")
            continue
        L.append(f"   RETURN 值来源: {[s for _, s in rets] or '(无)'}")
        loc = [c[2] for c in calls if any(h in c[2].lower() for h in LOC_HINTS)]
        L.append(f"   localization/STBL/hash 调用: {loc or '(无)'}")

    # Caught Cheating key/hash 判定
    L.append("")
    L.append("=== Caught Cheating 1 -> key/hash 判定 ===")
    L.append("   (仅当字节码把 animation_id 映射为字符串 key / 整数 hash 时可确定)")
    # 收集所有方法中出现的 string key 候选常量与 hash 调用
    key_consts = set()
    hash_calls = set()
    for tname, calls in all_calls.items():
        fn = next((f for f in funcs if f.co_name == tname), None)
        if not fn:
            continue
        for c in fn.co_consts:
            if isinstance(c, str) and any(h in c.lower() for h in KEY_HINTS):
                key_consts.add(c)
        for i, it, callee in calls:
            if "hash" in callee.lower():
                # 看 hash 的实参: 可能是常量, 或 BINARY_ADD 拼装的 key (含前缀常量)
                lines = list(XBytecode(fn, opc))
                arg_note = None
                for j in range(i - 1, max(-1, i - 6), -1):
                    if lines[j].opname == "LOAD_CONST":
                        arg_note = f"const:{lines[j].argrepr}"
                        break
                    if lines[j].opname in ("BINARY_ADD", "BINARY_OP"):
                        # 拼装 key: 找最近前缀常量
                        for k in range(j - 1, max(-1, j - 5), -1):
                            if lines[k].opname == "LOAD_CONST":
                                arg_note = f"composite(+{lines[k].argrepr} + str(animation_id))"
                                break
                        break
                hash_calls.add((tname, arg_note or "runtime arg"))
    if key_consts:
        L.append(f"   候选字符串 key 常量: {sorted(key_consts)}")
    if hash_calls:
        L.append(f"   hash 调用的实参(可能为常量): {hash_calls}")
    if not key_consts and not hash_calls:
        L.append("   (字节码中未直接见 key/hash 常量 -> 键由运行时/外部表生成,"
                 " 需进一步查 STBL 资源)")
    L.append("")

    L.append("ZERO_WRITE_TO_MODS=YES (只读, 未修改任何文件)")
    txt = "\n".join(L)
    (out_dir / "p18_story_localization.txt").write_text(txt, encoding="utf-8")
    # csv: 各方法反汇编
    with open(out_dir / "p18_story_localization.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["method", "idx", "offset", "op", "arg"])
        for tname in TARGET_METHODS:
            fn = next((f for f in funcs if f.co_name == tname), None)
            if not fn:
                continue
            for i, it in enumerate(XBytecode(fn, opc)):
                w.writerow([tname, i, it.offset, it.opname, it.argrepr or ""])
    print(txt)
    print(f"OUT_TXT={out_dir/'p18_story_localization.txt'}")
    print("P18_STORY_LOCALIZATION=OK (只读)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
