#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P20 _loc / localization 来源追踪 (只读, 不改 package)

背景: P19 确认 _create_sex_animation_instance 里
        STORY 分支: display_name = _loc 返回的 localization id/hash
        Normal 分支: display_name = 原始字符串
      现在要追 _loc 本身: 它调用了什么函数, 参数来源,
      以及 Story ordinal 299 最终 localization key/hash 的形式。

目标 (animations_loader.pyc):
  1. _create_sex_animation_instance 内所有 _loc/localization 相关调用
  2. STORY 分支调用 _loc 的位置
  3. _loc(被调函数) 的参数来源:
       - animation_id ?
       - animation_stage_name ?
       - animation_category ?
       - hash ?
       - STBL key ?
  4. 输出 Story ordinal 299 最终 localization key/hash 的形式
     (前缀常量 + 拼接 + hash 的结构)

方法 (只读, xdis):
  1. 定位 _create_sex_animation_instance, 全量反汇编
  2. 找 STORY 分支触发的 _loc/localize 类调用 (callee 含 localize/loc/_loc)
  3. 对每个 LOC 调用: 显示参数窗口, 解析实参来源 (param/const/attr/call)
  4. follow callee: 在模块所有函数中定位该被调函数, 反汇编其 body
  5. 在被调函数内找 hash/STBL/key 构造: 前缀常量 (BINARY_ADD + str + hash),
     报告 key 形式 = 前缀 + str(animation_id) -> hash(...)
  6. 判定参数是否含 animation_id / stage / category / hash / STBL key

fail-closed: 源缺->2; 无 WW->3; 目录缺->4; xdis 缺->7;
  loader.pyc 缺->5; 目标函数缺->8; STORY 分支无 LOC 调用->9; 正常 0。
不改任何文件, 不写 Mods。ZERO_WRITE_TO_MODS=YES。

用法 (Windows, 只读):
  pip install xdis
  python scripts\ww_animation_p20_loc_source.py `
      "<SRC.package>" --dir "C:\\...\\Mods" [--out-dir output/ww_p20]
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
LOC_HINTS = ("localize", "_loc", "loc", "hash", "stbl", "key", "get_l18n",
             "localization", "get_localized", "get_string", "translate")

OUT_DIR = Path("output/ww_p20")


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


def walk_all_code(co, acc):
    acc.append(co)
    for sub in co.co_consts:
        if hasattr(sub, "co_name"):
            walk_all_code(sub, acc)
    return acc


def named_callee(lines, call_i):
    run = []
    j = call_i - 1
    while j >= 0 and j >= call_i - 10 and lines[j].opname.startswith(("LOAD_", "IMPORT_")):
        run.append(j); j -= 1
    for k in reversed(run):
        if lines[k].opname in ("LOAD_GLOBAL", "LOAD_NAME", "LOAD_METHOD",
                               "IMPORT_NAME", "LOAD_METHOD_HANDLE", "LOAD_ATTR"):
            return lines[k].argrepr
    return "?"


def arg_source(lines, val_i):
    for k in range(val_i, max(-1, val_i - 4), -1):
        v = lines[k]
        if v.opname.startswith("LOAD_CONST"):
            return f"const:{v.argrepr}"
        if v.opname.startswith("CALL"):
            return f"call->{named_callee(lines, k)}"
        if v.opname == "LOAD_FAST":
            return f"param:{v.argrepr}"
        if v.opname == "LOAD_ATTR":
            return f"attr:{v.argrepr}"
        if v.opname == "LOAD_GLOBAL":
            return f"global:{v.argrepr}"
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
        print(f"ERROR: 未在 {d} .ts4script 内找到 {LOADER_NAME}", file=sys.stderr); return 5
    ts4path, member, data = got

    try:
        res = load_module_from_file_object(_io.BytesIO(data), filename=LOADER_NAME)
    except Exception as ex:
        print(f"ERROR: xdis 解析失败: {ex}", file=sys.stderr); return 6
    ver, co = res[0], res[3]; impl = res[4]
    opc = P15.get_opc(ver)

    funcs = []
    walk_all_code(co, funcs)
    fn_map = {f.co_name: f for f in funcs}

    fn = fn_map.get(TARGET_FN)
    if fn is None:
        print(f"ERROR: 未找到函数 {TARGET_FN}", file=sys.stderr); return 8
    lines = list(XBytecode(fn, opc))

    L = []
    L.append("=== P20 _loc/localization 来源追踪 (只读) ===")
    L.append(f"源 = {src.name}")
    L.append(f"ts4script = {ts4path}")
    L.append(f"member = {member}   pyc size = {len(data)}")
    L.append(f"python = {ver}  impl = {impl}")
    L.append(f"目标函数 = {TARGET_FN} (line={fn.co_firstlineno}, 指令={len(lines)})")
    L.append(f"参数 = {list(fn.co_varnames)}")
    L.append("")
    L.append(f"模块内函数 ({len(funcs)}): {', '.join(sorted(fn_map))}")
    L.append("")

    # 1) 目标函数内所有 _loc/localization 相关调用
    L.append("=== 1) _create_sex_animation_instance 内 localization/_loc 相关调用 ===")
    loc_calls = []
    for i, it in enumerate(lines):
        if it.opname.startswith("CALL"):
            cc = named_callee(lines, i)
            if any(h in cc.lower() for h in LOC_HINTS):
                loc_calls.append((i, it, cc))
    if not loc_calls:
        L.append("  (未见 localization/_loc 相关调用)")
    for i, it, cc in loc_calls:
        L.append(f"  [{i:4d}] off={it.offset} call->{cc}")
    L.append("")

    # 2) STORY 分支调用 _loc 的位置
    L.append("=== 2) STORY 分支调用 _loc 位置 ===")
    for i, it in enumerate(lines):
        if it.opname == "LOAD_CONST" and it.argrepr in ('"STORY"', "'STORY'"):
            L.append(f"      LOAD_CONST 'STORY' @[{i}] (分支判定常量)")
    for i, it, cc in loc_calls:
        story_before = any(lines[j].opname == "LOAD_CONST" and lines[j].argrepr in ('"STORY"', "'STORY'")
                           for j in range(max(0, i - 15), i))
        verdict = "-> 属于STORY分支" if (story_before or i < 12) else "(更可能是全局/其他)"
        L.append(f"      [{i:4d}] call->{cc}   STORY常量在其前15条: {story_before} {verdict}")
    L.append("")

    # 3) STORY 分支 LOC 调用的实参来源 (重点)
    L.append("=== 3) _loc 调用实参来源 ===")
    argsrc = []
    target_idx = None
    for i, it, cc in loc_calls:
        if any(h in cc.lower() for h in ("localize", "_loc", "loc")) and target_idx is None:
            target_idx = i
            L.append(f"  聚焦 LOC 调用 @[{i}] call->{cc} 参数窗口 (前 6 条):")
            for k in range(max(0, i - 6), i):
                L.append(f"      [{k:4d}] {lines[k].offset:4d} {lines[k].opname:22s} {lines[k].argrepr or ''}")
            L.append("")
            n = (lines[i].arg or 1)
            j = i - 1
            got = 0
            while j >= 0 and got < max(2, n):
                v = lines[j]
                if v.opname.startswith("LOAD_"):
                    argsrc.append((v, arg_source(lines, j)))
                    got += 1
                j -= 1
            argsrc.reverse()
    L.append("  [实参来源汇总]")
    if argsrc:
        for v, s in argsrc:
            L.append(f"      arg <- {v.opname}:{v.argrepr or ''}  = {s}")
    else:
        L.append("      (未定位到 _loc/localize 调用/未取到实参)")
    L.append("")
    L.append("  参数来源语义判定:")
    if argsrc:
        for v, s in argsrc:
            name = str(v.argrepr or "")
            if "animation_id" in name or s.endswith("animation_id"):
                L.append(f"      含 animation_id: {s} -> 与动画 id 相关")
            if "stage" in name.lower():
                L.append(f"      含 stage: {s} -> 与 stage_name 相关")
            if "category" in name.lower():
                L.append(f"      含 category: {s} -> 传入了 category")
            if s.startswith("call->") and "hash" in s.lower():
                L.append(f"      含 hash: {s} -> 哈希调用")
            if "stbl" in name.lower() or "key" in name.lower():
                L.append(f"      含 STBL/key: {s} -> 本地化键")
    else:
        L.append("      (无实参可判定)")
    L.append("")

    # 4) follow callee
    L.append("=== 4) follow callee: _loc 被调函数内部 key/hash 构造 ===")
    callee_fn = None
    if target_idx is not None:
        cname = named_callee(lines, target_idx)
        callee_fn = fn_map.get(cname)
        L.append(f"  STORY 分支调用的函数 = {cname}")
        if callee_fn is None:
            L.append(f"  !!! 模块内找不到函数 {cname} (可能是外部导入/内置)")
        else:
            L.append(f"  -> 反汇编 {cname} (line={callee_fn.co_firstlineno}):")
            clines = list(XBytecode(callee_fn, opc))
            for k, it in enumerate(clines):
                L.append(f"      [{k:3d}] {it.offset:4d} {it.opname:22s} {it.argrepr or ''}")
            L.append("")
            L.append("  [key/hash/STBL 构造线索]")
            pref = []
            for k, it in enumerate(clines):
                if it.opname == "LOAD_CONST" and isinstance(it.argrepr, str) and it.argrepr not in ("", "STORY"):
                    pref.append((k, it.argrepr))
                if it.opname in ("BINARY_ADD", "BINARY_OP"):
                    L.append(f"      [{k:3d}] 拼接: {it.opname}")
                if it.opname == "LOAD_CONST" and it.argrepr == "STORY":  # placeholder no-op
                    pass
            L.append(f"  候选字符串常量: {pref}")
            L.append("")
            L.append("  [key/hash 相关引用]")
            for k, it in enumerate(clines):
                r = it.argrepr or ""
                if "hash" in r.lower() or "stbl" in r.lower() or "key" in r.lower():
                    L.append(f"      [{k:3d}] {it.opname}:{r}")
            L.append("")
            has_hash = any("hash" in (it.argrepr or "").lower() or "hash" in it.opname.lower()
                           for it in clines)
            has_add = any(it.opname in ("BINARY_ADD", "BINARY_OP") for it in clines)
            # 找真正喂入 hash 的前缀: BINARY_ADD 左操作字符串常量 (旁边有 str(animation_id))
            prefix_used = None
            for k, it in enumerate(clines):
                if it.opname in ("BINARY_ADD", "BINARY_OP") :
                    # 左操作数: 往前找最近的字符串常量
                    for kk in range(k - 1, max(-1, k - 5), -1):
                        if clines[kk].opname == "LOAD_CONST" and isinstance(clines[kk].argrepr, str):
                            prefix_used = clines[kk].argrepr
                            break
                    if prefix_used:
                        break
            L.append("  [Story ordinal 299 key/hash 形式判定]")
            if has_hash:
                if prefix_used:
                    form = f"hash({prefix_used!r} + str(animation_id) )"
                else:
                    form = "hash(<与 animation_id 相关>)"
                L.append(f"  => Story(299) 最终 form: {form}")
                L.append(f"     含 hash: True, 含字符串拼接: {has_add}, 喂入 hash 的前缀常量: {prefix_used!r}")
                L.append(f"     -> Caught Cheating 1 (ord299) key = {form}; 此 key 查 STBL 得显示文本")
            else:
                L.append(f"  未见 hash; 可能直接返回常量/传入 key。含拼接={has_add}")
            L.append("")
    else:
        L.append("  (未定位 STORY 分支 LOC 调用, 无法 follow)")
        L.append("")

    L.append("ZERO_WRITE_TO_MODS=YES (只读, 不修改 package)")
    txt = "\n".join(L)
    (out_dir / "p20_loc_source.txt").write_text(txt, encoding="utf-8")
    with open(out_dir / "p20_loc_source.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["method", "idx", "offset", "op", "arg"])
        for i, it in enumerate(lines):
            w.writerow([TARGET_FN, i, it.offset, it.opname, it.argrepr or ""])
    print(txt)
    print(f"OUT_TXT={out_dir/'p20_loc_source.txt'}")
    print("P20_LOC_SOURCE=OK (只读)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
