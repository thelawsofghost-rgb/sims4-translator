#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P21 display_name 真实来源追踪 (只读, 不改 package)

P20 假设 _create_sex_animation_instance 调模块 helper `_localize` —— 真机证伪,
此 helper 不存在。P21 不再假设任何 callee, 纯粹做字节码反向数据流:

1. 定位 SexAnimationInstance 构造 CALL_FUNCTION_KW, 确认 display_name 由哪个 local/attr 提供
2. 从函数开始反向追踪该 local 的每个 STORE_FAST / STORE_ATTR 来源
3. 沿 def-use 链回溯到终值:
   - LOAD_CONST(字符串)      -> A. 普通字符串
   - hash(...)/get_localized_string_id(...)/int 调用 -> B. hash/int localization id
   - 其他                   -> C. 其他对象
4. 输出 Story(299) display_name 真实类型 + 最终来源调用链
5. 附带: 无论哪条路径, 若含 get_localized_string_id / get_string / get_text / localize
   则标记「本地化 id 路径」; 若纯 str 常量/param 则 A。

方法: xdis 反汇编 _create_sex_animation_instance, 做本地 def-use 反向分析:
  - display_name 的实参 LOAD 点 -> 找到最近 STORE
  - STORE 值来源: 单条指令终结 (CALL_BASE / LOAD_CONST / LOAD_FAST / LOAD_GLOBAL / BINARY)
  - 若是 LOAD_FAST <其他local>, 递归追该 local 的 STORE
  - 记录 CALL 链 (含 get_l18n_service() 方法调用, hash, get_localized_string_id)

fail-closed: 源缺->2; 无 WW->3; 目录缺->4; xdis 缺->7;
  loader.pyc 缺->5; 目标函数缺->8; 无构造调用->9; 正常 0。
不改任何文件, 不写 Mods。ZERO_WRITE_TO_MODS=YES。

用法 (Windows, 只读):
  pip install xdis
  python scripts\ww_animation_p21_display_name_source.py `
      "<SRC.package>" --dir "C:\\...\\Mods" [--out-dir output/ww_p21]
"""
import argparse
import csv
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
CTOR_SUBSTR = "SexAnimationInstance"
LOC_ID_HINTS = ("get_l18n_service", "get_localized_string_id", "get_localized_string",
                "get_string", "get_text", "localize", "translate", "get_localization")
HASH_HINTS = ("hash",)

OUT_DIR = Path("output/ww_p21")


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


def callee_run(lines, call_i):
    run = []
    j = call_i - 1
    while j >= 0 and j >= call_i - 10 and lines[j].opname.startswith(("LOAD_", "IMPORT_")):
        run.append(j); j -= 1
    return [lines[k] for k in run]


def named_callee(lines, call_i, run=None):
    # CALL_METHOD: 方法名在最近的 LOAD_METHOD (可能在多条件调用之前, 中间隔着 CALL)
    if lines[call_i].opname == "CALL_METHOD":
        for j in range(call_i - 1, max(-1, call_i - 30), -1):
            if lines[j].opname == "LOAD_METHOD":
                return lines[j].argrepr
    run = run if run is not None else callee_run(lines, call_i)
    for it in reversed(run):
        if it.opname in ("LOAD_GLOBAL", "LOAD_NAME", "LOAD_METHOD", "IMPORT_NAME",
                         "LOAD_METHOD_HANDLE", "LOAD_ATTR"):
            return it.argrepr
    return "?"


def is_call_base(op):
    return op.startswith("CALL") or op in ("CALL_FUNCTION_EX", "CALL_METHOD")


class DisplayTracer:
    """反向 def-use 追踪器: 从 display_name 实参点回溯终值来源. 纯分析, 不改源码."""

    def __init__(self, lines):
        self.lines = lines

    def _store_src(self, store_i):
        """STORE 在 store_i; 回溯其值来源 (紧邻值指令)."""
        for k in range(store_i - 1, max(-1, store_i - 6), -1):
            it = self.lines[k]
            if it.opname == "LOAD_CONST":
                return ("CONST", it.argrepr, k)
            if is_call_base(it.opname):
                return ("CALL", named_callee(self.lines, k), k)
            if it.opname == "LOAD_FAST":
                return ("LOCAL", it.argrepr, k)
            if it.opname == "LOAD_ATTR":
                return ("ATTR", it.argrepr, k)
            if it.opname == "LOAD_GLOBAL":
                return ("GLOBAL", it.argrepr, k)
            if it.opname in ("BINARY_SUBSCR", "BINARY_ADD", "BINARY_OP"):
                return ("BINOP", it.opname, k)
        return ("?", None, store_i)

    def resolve(self, load_i):
        """从 LOAD_FAST <name> 在 load_i 反向: 返回 (元类别, 描述, 调用链, 证据行)."""
        if self.lines[load_i].opname != "LOAD_FAST":
            return ("?", f"非局部变量 {self.lines[load_i].opname}", [], load_i)
        name = self.lines[load_i].argrepr
        call_chain = []
        evidence = []
        cur_i = load_i
        depth = 0
        seen = set()
        while depth < 8:
            # 找 cur_i 之前最近的同名 STORE_FAST
            s_i = None
            for j in range(cur_i - 1, -1, -1):
                if self.lines[j].opname == "STORE_FAST" and self.lines[j].argrepr == name:
                    s_i = j
                    break
            if s_i is None:
                return ("PARAM", f"局部 {name} 无 STORE 记录(参数或函数外定义)",
                        call_chain, load_i)
            if s_i in seen:
                return ("LOOP", f"循环依赖 {name}", call_chain, s_i)
            seen.add(s_i)
            evidence.append((s_i, self.lines[s_i].opname, name))
            cat, val, src_i = self._store_src(s_i)
            if cat == "CALL":
                call_chain.append((s_i, val))
                # 若是 get_l18n_service().method 形式, 提取方法名
                markers = []
                for it in callee_run(self.lines, s_i):
                    if it.argrepr:
                        markers.append(it.argrepr)
                return (cat, f"call->{val}", call_chain, s_i)
            if cat == "CONST":
                return (cat, f"const:{val}", call_chain, s_i)
            if cat == "BINOP":
                # BINARY_ADD/SUBSCR 等 -> 组合来源, 回溯左/右操作数
                call_chain.append((s_i, val))
                return (cat, f"组合:{val} (见窗口)", call_chain, s_i)
            if cat == "ATTR":
                return (cat, f"attr:{val}", call_chain, s_i)
            if cat == "GLOBAL":
                return (cat, f"global:{val}", call_chain, s_i)
            if cat == "LOCAL":
                name = val  # 跳转到另一个 local, 继续
                cur_i = s_i
                depth += 1
                continue
            return ("?", val, call_chain, s_i)
        return ("DEPTH", "追踪过深(>8), 终止", call_chain, cur_i)


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
    tr = DisplayTracer(lines)

    L = []
    L.append("=== P21 display_name 真实来源追踪 (只读) ===")
    L.append(f"源 = {src.name}")
    L.append(f"ts4script = {ts4path}")
    L.append(f"member = {member}   pyc size = {len(data)}")
    L.append(f"python = {ver}  impl = {impl}")
    L.append(f"目标函数 = {TARGET_FN} (line={fn.co_firstlineno}, 指令={len(lines)})")
    L.append(f"局部变量 = {list(fn.co_varnames)}")
    L.append("")

    # 1) 定位所有 SexAnimationInstance 构造调用 + 其 display_name 实参点
    L.append("=== 1) SexAnimationInstance 构造调用 + display_name 实参 LOAD ===")
    ctor_call_idx = None
    disp_load_idx = None
    kw_names = []
    for i, it in enumerate(lines):
        if is_call_base(it.opname):
            cc = named_callee(lines, i)
            if CTOR_SUBSTR in (cc or ""):
                ctor_call_idx = i
                L.append(f"  构造调用 @[{i}] off={it.offset} call->{cc} ({it.opname})")
                # 找 kw 元组常量 (最后一条 LOAD_CONST 含 '(' )
                kw_idx = None
                for k in range(i - 1, max(-1, i - 4), -1):
                    r = lines[k].argrepr or ""
                    if lines[k].opname == "LOAD_CONST" and "(" in r:
                        kw_idx = k
                        break
                if kw_idx is not None:
                    raw = lines[kw_idx].argrepr
                    kw_names = [x.strip().strip("'\"") for x in raw.strip("()").split(",")]
                    L.append(f"    kw 元组 @[{kw_idx}]: {kw_names}")
                    # display_name 在元组位置 -> 对应实参值 LOAD = 元组前 (len-kw- 第pos个值)
                    if "display_name" in kw_names:
                        pos = kw_names.index("display_name")  # 0-based
                        # 元组前收集 len(kw_names) 个值 LOAD, 取第 pos 个
                        vals = []
                        k = kw_idx - 1
                        while k >= 0 and len(vals) < len(kw_names):
                            if lines[k].opname.startswith(("LOAD_", "CALL_FUNCTION")):
                                vals.append(k)
                            k -= 1
                        vals.reverse()
                        if pos < len(vals):
                            disp_load_idx = vals[pos]
                            L.append(f"    display_name 实参值 LOAD @[{disp_load_idx}] -> "
                                     f"{lines[disp_load_idx].opname}:{lines[disp_load_idx].argrepr or ''}")
                        else:
                            L.append(f"    (display_name 实参值未定位 pos={pos}/{len(vals)})")
                break
    if ctor_call_idx is None:
        L.append("  !!! 未见 SexAnimationInstance 构造调用")
        L.append("ZERO_WRITE_TO_MODS=YES (只读, 未调用构造, 退出)")
        txt = "\n".join(L)
        (out_dir / "p21_display_name_source.txt").write_text(txt, encoding="utf-8")
        print(txt)
        print("P21_DISPLAY_NAME_SOURCE=NO_CTOR (只读)")
        return 9
    L.append("")

    # 2) display_name 全部 STORE 点 (STORE_FAST 任何名字 + STORE_ATTR)
    L.append("=== 2) display_name 相关 STORE_FAST / STORE_ATTR 来源 ===")
    stores = []
    for i, it in enumerate(lines):
        if it.opname == "STORE_FAST":
            src = tr._store_src(i)
            stores.append((i, "FAST", it.argrepr, src))
        elif it.opname == "STORE_ATTR":
            src = tr._store_src(i)
            stores.append((i, "ATTR", it.argrepr, src))
    for i, kind, name, src in stores:
        L.append(f"  [{i:4d}] STORE_{kind} {name}  <- {src[0]}:{src[1] if src[1] is not None else src[2]}")
    L.append("")

    # 3) 反向追踪 display_name 实参
    L.append("=== 3) display_name 实参反向追踪 (def-use 链) ===")
    if disp_load_idx is not None:
        cat, desc, chain, ev = tr.resolve(disp_load_idx)
        L.append(f"  实参 LOAD @[{disp_load_idx}] -> 终值类别: {cat}")
        L.append(f"  display_name 真实来源: {desc}")
        L.append(f"  调用链 (idx=call, callee):")
        for s_i, cal in chain:
            L.append(f"      [{s_i:4d}] call->{cal}")
        L.append(f"  证据 STORE 点: {ev}")
        L.append("")
        # 4) 类别判断
        L.append("=== 4) 类型判定 ===")
        call_names = [c for _, c in chain]
        has_loc = any(any(h in (c or "").lower() for h in LOC_ID_HINTS) for c in call_names)
        has_hash = any(any(h in (c or "").lower() for h in HASH_HINTS) for c in call_names)
        # 若 STORY 分支含 get_localized_string_id, 展开其参数窗口找内部 hash 构造
        inner_hash = []
        inner_prefix = None
        if any("get_localized_string_id" in (c or "") for c in call_names):
            # 从 STORY 分支 display 的 STORE 点往前扫到函数起点, 找 hash/前缀
            story_store = ev if ev is not None else disp_load_idx
            for j in range(max(0, story_store - 20), story_store):
                it = tr.lines[j]
                rr = it.argrepr or ""
                if it.opname in ("LOAD_GLOBAL", "LOAD_NAME") and "hash" in rr.lower():
                    inner_hash.append((j, rr))
                if it.opname == "LOAD_CONST" and isinstance(it.argrepr, str) \
                        and rr not in ("ERROR", '"STORY"', "'STORY'", '""', 'None'):
                    inner_prefix = it.argrepr  # 取最后一个(最靠近 hash 构造的)字符串常量
            L.append(f"  内部 hash 调用: {[h for _, h in inner_hash]}")
            L.append(f"  喂入 hash 的前缀常量: {inner_prefix!r}")
        if cat == "CONST" and isinstance(desc.split(":", 1)[-1] if ":" in desc else desc, str):
            L.append(f"  => A. 普通字符串 (const 直接传入)")
            verdict = "A"
        elif cat == "CALL":
            if has_loc or has_hash or inner_hash:
                L.append(f"  => B. hash/int localization id (含本地化/hash 调用)")
                L.append(f"     本地化调用: {[c for c in call_names if any(h in c.lower() for h in LOC_ID_HINTS)]}")
                L.append(f"     hash 调用:  {([n for _, n in inner_hash] or [c for c in call_names if has_hash])}")
                if inner_prefix and inner_hash:
                    L.append(f"     Story(299) key 构造: hash({inner_prefix!r} + str(animation_id)) -> get_localized_string_id")
                verdict = "B"
            else:
                L.append(f"  => C. 其他对象 (call->{call_names[-1] if call_names else '?'}, 非本地化/hash)")
                verdict = "C"
        elif cat in ("BINOP", "GLOBAL", "ATTR"):
            L.append(f"  => C. 其他对象 (类别 {cat}: {desc})")
            verdict = "C"
        elif cat == "PARAM":
            L.append(f"  => C/参数 (来自函数参数 {desc})")
            verdict = "C"
        else:
            L.append(f"  => ? (类别 {cat}: {desc})")
            verdict = "?"
        L.append(f"  判定: Story(299) animation_display_name = 类型 {verdict}")
        if verdict == "B":
            L.append("  => 下一步: 查对应 STBL key (见 P22), 或注入 get_localized_string_id 键")
        L.append("")
    else:
        L.append("  !!! 无法定位 display_name 实参 LOAD")
        L.append("")

    # 5) 全窗口 STORY/Normal 分支差异 (display 各 STORE 来源 + 构造调用前窗口)
    L.append("=== 5) Story vs Normal 分支 display 赋值差异 (字节码窗口) ===")
    # 找所有 display 局部 STORE 及其来源调用/常量
    disp_stores = [(i, kind, name, src) for i, kind, name, src in stores
                   if name in ("display", "display_name") or kind == "ATTR"]
    for i, kind, name, src in disp_stores:
        t = "str" if src[0] == "CONST" else ("本地化/hash" if src[0] == "CALL" else src[0])
        L.append(f"      [{i:4d}] display({name}) <- {src[0]}:{src[1] if src[1] is not None else src[2]}  类型线索={t}")
    # 构造调用前 14 条完整窗口
    if ctor_call_idx is not None:
        L.append(f"  构造调用 @[{ctor_call_idx}] 前 14 条窗口:")
        for k in range(max(0, ctor_call_idx - 14), ctor_call_idx):
            L.append(f"      [{k:4d}] {lines[k].offset:4d} {lines[k].opname:22s} {lines[k].argrepr or ''}")
    L.append("")

    L.append("ZERO_WRITE_TO_MODS=YES (只读, 不修改 package)")
    txt = "\n".join(L)
    (out_dir / "p21_display_name_source.txt").write_text(txt, encoding="utf-8")
    with open(out_dir / "p21_display_name_source.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["method", "idx", "offset", "op", "arg"])
        for i, it in enumerate(lines):
            w.writerow([TARGET_FN, i, it.offset, it.opname, it.argrepr or ""])
    print(txt)
    print(f"OUT_TXT={out_dir/'p21_display_name_source.txt'}")
    print(f"P21_DISPLAY_NAME_SOURCE={verdict if disp_load_idx is not None else 'NO_LOAD'} (只读)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
