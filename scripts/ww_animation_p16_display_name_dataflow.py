#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P16 WW 核心 .pyc 字节码数据流深挖 (只读, 不再扫资源)。

目标: 追踪 animations_loader.pyc::_create_sex_animation_instance (真机 line 118)
     display_name 的最终赋值来源。

问题 (要回答):
  为什么修改 WW_ANIM_XML 的 animation_raw_display_name,
  但 AnimationInstance.get_display_name() 不变化?

  display_name 最终从哪里赋值:
    - animation_raw_display_name ?
    - animation_stage_name ?
    - animation_id ?
    - localization/STBL / get_string / localize / hash ?
    - 其他字段?

引擎: xdis + load_module_from_file_object (任意 Python 版本 .pyc, 只读)。

分析 (只读, 逐函数):
  1) 从 .ts4script (zip) 内取出 animations_loader.pyc
  2) 定位 _create_sex_animation_instance (按名, 报告真实行号/参数/局部变量)
  3) 该函数全量反汇编 + 构建指令窗口表
  4) STORE display_name 前后 30 条指令窗口
  5) 检测函数调用 (CALL_FUNCTION*/CALL_METHOD/LOAD_METHOD) 及其目标名;
     标记 "候选显示名来源" 调用: get_string/localize/hash/get_display_name/
       get_localized_string/get_final_string/localization/localize_string 等
  6) 追踪 display_name 的来源: 从 STORE 点回溯上游最近一次对
     display_name 赋值的操作 (参数/属性/本地赋值), 列出 LOAD/STORE 链
  7) 若 display_name 最终落到 AnimationInstance (属性存到 self.xxx),
     列出该 STORE_ATTR 目标名与来源

fail-closed: 源缺->2; 无 WW->3; 目录缺->4; 找不到 loader.pyc->5;
  xdis 缺失->7; loader 内找不到目标函数->8; 正常 0。
不改任何文件。ZERO_WRITE_TO_MODS=YES。

用法 (Windows, 只读):
  pip install xdis
  python scripts\ww_animation_p16_display_name_dataflow.py `
      "<SRC.package>" --dir "C:\\...\\Mods" [--out-dir output/ww_p16]
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
_spec = importlib.util.spec_from_file_location(
    "ww_animation_canary_builder", SCRIPT_DIR / "ww_animation_canary_builder.py")
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)
import ww_animation_p7_story_chain_audit as _p7
import ww_animation_p15_pyc_deep_dive as P15   # 复用 get_opc/walk_code/find_branches

OUT_DIR = Path("output/ww_p16")

if not P15.XDIS:
    print("ERROR: 缺依赖 xdis —— 请先: pip install xdis", file=sys.stderr)
    sys.exit(7)
from xdis.load import load_module_from_file_object

LOADER_NAME = "animations_loader.pyc"   # zip 内 basename
TARGET_FN = "_create_sex_animation_instance"

# 显示名来源候选调用 (按子串匹配 CALL_METHOD/LOAD_METHOD 目标名)
CALL_HINTS = ("get_string", "localize", "hash", "get_display_name",
              "get_localized_string", "get_final_string", "localization",
              "localize_string", "get_text", "translate")

# 指令窗口半宽
WIN = 30


def load_loader_from_dir(d):
    """从 Mods 目录所有 .ts4script zip 内找 animations_loader.pyc -> bytes."""
    for sp in [p for p in d.rglob("*.ts4script") if p.is_file()]:
        try:
            with _zipfile.ZipFile(sp) as z:
                for name in z.namelist():
                    if Path(name).name == LOADER_NAME:
                        return sp, name, z.read(name)
        except Exception:
            continue
    return None


def insns(fn, opc):
    return list(P15.Bytecode(fn, opc))


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
        print(f"ERROR: 未在目录 {d} 的 .ts4script 内找到 {LOADER_NAME}", file=sys.stderr)
        return 5
    ts4path, member, data = got

    try:
        res = load_module_from_file_object(_io.BytesIO(data), filename=LOADER_NAME)
    except Exception as ex:
        print(f"ERROR: xdis 解析 {LOADER_NAME} 失败: {ex}", file=sys.stderr); return 6
    ver, co = res[0], res[3]; impl = res[4]
    opc = P15.get_opc(ver)

    fn = find_func(co, TARGET_FN)
    if fn is None:
        print(f"ERROR: {LOADER_NAME} 内未找到函数 {TARGET_FN}", file=sys.stderr)
        names = []
        funcs = []; P15.walk_code(co, funcs)
        for f in funcs:
            if "animation" in f.co_name or "display" in f.co_name or "instance" in f.co_name:
                names.append(f.co_name)
        print("相近函数: " + ", ".join(names[:30]) or "(无)")
        return 8

    lines = insns(fn, opc)
    L = []
    L.append("=== P16 display_name 字节码数据流 (只读) ===")
    L.append(f"源 = {src.name}")
    L.append(f"ts4script = {ts4path}")
    L.append(f"member = {member}   pyc size = {len(data)}")
    L.append(f"python = {ver}  impl = {impl}")
    L.append(f"目标函数 = {TARGET_FN}  line = {fn.co_firstlineno}  指令数 = {len(lines)}")
    L.append(f"参数 (co_varnames): {list(fn.co_varnames)}")
    L.append("")

    # 定位 display_name 的 STORE 点
    store_idx = []
    for i, it in enumerate(lines):
        if it.opname in ("STORE_FAST", "STORE_ATTR") and it.argrepr == "display_name":
            store_idx.append(i)
    L.append(f"display_name 直接 STORE 点 = {len(store_idx)} 处")
    if not store_idx:
        # 可能 display_name 是参数且从没被 STORE (直接作为来源)
        L.append("  (display_name 未被 STORE —— 说明它是参数/属性读取, 直接用作来源)")
        if "display_name" in fn.co_varnames:
            L.append("  display_name 是本函数参数 (co_varnames 含之) —— 由调用方传入!")
        param_uses = [(fmt_ins(lines[i]), i) for i, it in enumerate(lines)
                      if it.opname.startswith("LOAD_") and it.argrepr == "display_name"]
        for dsc, i in param_uses[:20]:
            L.append(f"    使用 {dsc}")
    L.append("")

    # 全量 STORE/LOAD display_name 位置
    L.append("--- display_name 在本函数内所有存取 ---")
    for i, it in enumerate(lines):
        if it.argrepr == "display_name" and (it.opname.startswith("LOAD_")
                                             or it.opname.startswith("STORE_")):
            L.append(f"   [{i:4d}] {fmt_ins(it)}")
    L.append("")

    # 候选来源调用检测: 追踪最近一次 LOAD_GLOBAL/LOAD_NAME/LOAD_METHOD 名,
    # 在 CALL_FUNCTION*/CALL_METHOD/CALL 处报告 "call -> 该名", 并标记含候选提示的
    last_named = {}
    cur_name = None
    for i, it in enumerate(lines):
        if it.opname in ("LOAD_GLOBAL", "LOAD_NAME", "LOAD_METHOD", "LOAD_METHOD_HANDLE"):
            cur_name = it.argrepr
            last_named[i] = cur_name
    L.append("--- 候选显示名来源调用 (get_string/localize/hash/get_display_name/...) ---")
    hits = []
    for i, it in enumerate(lines):
        if it.opname not in ("CALL_FUNCTION", "CALL_FUNCTION_EX", "CALL_METHOD", "CALL"):
            continue
        # 回找最近的连续 LOAD 段, 取段首(最老)命名 = 可调用对象 (CPython: f(args..) 先 LOAD f 再 LOAD args)
        callee = "?"
        run = []
        j = i - 1
        while j >= 0 and j >= i - 8 and lines[j].opname.startswith(("LOAD_", "IMPORT_")):
            run.append(j); j -= 1
        for k in reversed(run):   # 从最老(段首)开始
            if k in last_named:
                callee = last_named[k]
                break
        hint = any(h in callee for h in CALL_HINTS)
        if hint:
            hits.append((i, it, callee))
    if hits:
        for i, it, callee in hits:
            L.append(f"   CALL -> {callee}  @[{i:4d}]")  
            for j in range(max(0, i - 3), min(len(lines), i + 2)):
                mark = ">>" if j == i else "  "
                L.append(f"       {mark} [{j:4d}] {fmt_ins(lines[j])}")
    else:
        L.append("   (无匹配的显示名来源调用)")
    # 也列出本函数所有命名调用 (无提示过滤), 便于人工核对
    L.append("   --- 本函数全部命名调用 (LOAD_GLOBAL/LOAD_NAME -> CALL) ---")
    seen = set()
    for i, it in enumerate(lines):
        if it.opname not in ("CALL_FUNCTION", "CALL_FUNCTION_EX", "CALL_METHOD", "CALL"):
            continue
        callee = "?"
        run = []
        j = i - 1
        while j >= 0 and j >= i - 8 and lines[j].opname.startswith(("LOAD_", "IMPORT_")):
            run.append(j); j -= 1
        for k in reversed(run):
            if k in last_named:
                callee = last_named[k]; break
        if callee not in seen:
            seen.add(callee)
            L.append(f"       call -> {callee} @[{i:4d}]")
    L.append("")

    # STORE display_name 前后 30 条窗口
    L.append(f"=== STORE display_name 前后 {WIN} 条指令窗口 ===")
    for si in store_idx:
        L.append(f"----- 窗口 @ 指令[{si}] (offset {lines[si].offset}) -----")
        lo = max(0, si - WIN); hi = min(len(lines), si + WIN + 1)
        for j in range(lo, hi):
            mark = ">>>" if j == si else "   "
            L.append(f"{mark} [{j:4d}] {fmt_ins(lines[j])}")
        L.append("")
    L.append("")

    # 答案: display_name 可能来源汇总
    L.append("=== 结论线索 ===")
    L.append("A. type/provenance 判定:")
    if not store_idx and "display_name" in fn.co_varnames:
        L.append("   display_name 是函数参数, 本函数未重写 —— 最终值完全由调用方决定,")
        L.append("   需向上追踪调用 _create_sex_animation_instance 的地方。")
    elif store_idx:
        # 对每个 STORE 点看来源: STORE_ATTR xxx 形式为 LOAD <value> / LOAD <base> / STORE_ATTR xxx
        # 取 STORE 前最近的两个 LOAD: 前一个是 base(对象), 前两个是 value(值)
        used_sources = set()
        for si in store_idx:
            recent = []
            for j in range(si - 1, max(-1, si - 6), -1):
                if lines[j].opname.startswith("LOAD_"):
                    recent.append(f"{lines[j].opname}:{lines[j].argrepr}")
                    if len(recent) >= 2:
                        break
            if recent:
                # recent 从新到旧: [0]=base(对象), [1]=value(数据)
                val = recent[-1] if len(recent) >= 2 else recent[0]
                base = recent[0]
                used_sources.add(f"value={val} 而 base={base}")
        L.append(f"   本函数内 STORE display_name 的来源操作 (value/base) = {sorted(used_sources) or '(无法回溯)'}")
    else:
        L.append("   (display_name 未在本函数出现 STORE/参数 —— 属异常, 见上方存取段)")

    # 检查该函数是否把显示名写到 AnimationInstance 的某属性 (STORE_ATTR 到 self.xxx)
    self_attrs = {}
    for i, it in enumerate(lines):
        if it.opname == "STORE_ATTR" and it.argrepr:
            self_attrs.setdefault(it.argrepr, 0)
            self_attrs[it.argrepr] += 1
    if self_attrs:
        L.append("   B. 本函数对 self 的属性写入 (STORE_ATTR 目标): "
                 + ", ".join(f"{k}({v})" for k, v in self_attrs.items()))

    # 检查 display_name 相关属性读取 (LOAD_ATTR self.xxx 可能是来源)
    attr_reads = [it.argrepr for it in lines
                  if it.opname == "LOAD_ATTR" and ("display" in it.argrepr.lower()
                                                   or "name" in it.argrepr.lower())]
    if attr_reads:
        L.append("   C. 含 display/name 的属性读取 (LOAD_ATTR): "
                 + ", ".join(dict.fromkeys(attr_reads)))

    L.append("")
    L.append("ZERO_WRITE_TO_MODS=YES (只读, 未修改任何文件)")

    txt = "\n".join(L)
    (out_dir / "p16_display_name_dataflow.txt").write_text(txt, encoding="utf-8")
    # csv: 窗口 + 跳转表
    with open(out_dir / "p16_display_name_dataflow.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["idx", "offset", "op", "arg"])
        for i, it in enumerate(lines):
            w.writerow([i, it.offset, it.opname, it.argrepr or ""])
    print(txt)
    print(f"OUT_TXT={out_dir/'p16_display_name_dataflow.txt'}")
    print("P16_DISPLAY_NAME_DATAFLOW=OK (只读)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
