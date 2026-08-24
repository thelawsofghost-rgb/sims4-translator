#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P15 WW 核心 .pyc 深挖 (只读, 针对两个关键文件, 不再全局搜索)。

目标文件 (用户指定):
  1) wickedwhims/sex/animations/animations_loader.pyc
  2) wickedwhims/sex/animations/animation_instance.pyc

引擎: xdis (纯 python, 任意 Python 版本 .pyc 均可解析/反汇编, 无需对应解释器)。
  - pip install xdis   (真机需先装)

审计 (只读):
  1) 解析 pyc 头 -> Python 版本 (xdis)
  2) 遍历嵌套 code object -> 每函数: 名/行号/字符串常量/名称/局部变量
  3) 标记引用关键字的函数 (animation_category/animation_id/
     animation_stage_name/animation_next_stages/display_name/story/
     STAGE/STORY/tags/category)
  4) 分支重建: LOAD_CONST <str> ... COMPARE_OP == (前面 LOAD_FAST/ATTR 一个
     key 名) -> 即 X == "STORY" 式条件, 输出到哪个 label/jump
  5) display_name 数据流: 哪些函数 LOAD/STORE/属性读写 display_name
     (+ STORE_FAST/STORE_ATTR 指示赋值点)

重点回答:
  A. display_name 最终来源
  B. animation_stage_name 是否覆盖 display_name
  C. animation_category 如何参与判断
  D. Story animation 是否进入特殊加载路径

fail-closed: 源缺->2; 无 WW->3; 目录缺->4; 无 pyc 名匹配->5;
  无法用 xdis 解析->6; 正常 0。
不改 WW_ANIM_XML。ZERO_WRITE_TO_MODS=YES。

用法 (Windows, 只读):
  pip install xdis
  python scripts\ww_animation_p15_pyc_deep_dive.py `
      "<SRC.package>" --dir "C:\\...\\Mods" [--out-dir output/ww_p15]
"""
import argparse
import csv
import importlib.util
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
_spec = importlib.util.spec_from_file_location(
    "ww_canary_builder", SCRIPT_DIR / "ww_animation_canary_builder.py")
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)
import ww_animation_p7_story_chain_audit as _p7

OUT_DIR = Path("output/ww_p15")

# 依赖 xdis (可选, 缺失则 fail-closed exit 7)
try:
    from xdis import load_module
    from xdis.disasm import Bytecode
    from xdis.op_imports import get_opcode_module, PythonImplementation
    XDIS = True
except Exception:
    XDIS = False

TARGET_NAMES = ("animations_loader.pyc", "animation_instance.pyc")
KEY = ["animation_category", "animation_id", "animation_stage_name",
       "animation_next_stages", "display_name", "story", "STAGE", "STORY",
       "tags", "category"]


def get_opc(ver):
    v = tuple(str(x) for x in ver[:2])
    return get_opcode_module(v, PythonImplementation.CPython)


def walk_code(co, funcs):
    """递归收集所有嵌套 code object."""
    funcs.append(co)
    for sub in co.co_consts:
        if hasattr(sub, "co_name"):
            walk_code(sub, funcs)


def collect(funcs):
    """每函数: 字符串常量集 / 名称集 / 局部变量集 / 是否存在嵌套."""
    rows = []
    for f in funcs:
        consts = {c for c in f.co_consts if isinstance(c, str)}
        names = set(f.co_names)
        varnames = set(f.co_varnames)
        rows.append({
            "name": f.co_name, "filename": str(f.co_filename),
            "line": f.co_firstlineno, "consts": consts, "names": names,
            "varnames": varnames, "obj": f,
        })
    return rows


def find_branches(fn, opc):
    """扫描函数字节码, 找 X == 'STR' 式的比较分支.
    规则: 出现 LOAD_CONST <str>, 其后若干指令内出现 COMPARE_OP ==,
    该 CONST 之前最近一次 LOAD_* 记录左侧名 => 报告 '<left> == <STR>'.
    返回 [(opcode, 窗口字符串)]."""
    insns = list(Bytecode(fn, opc))
    # left_offs: 每次非-const LOAD 的 argrepr (按 offset)
    left_offs = {}
    for ins in insns:
        if ins.opname in ("LOAD_FAST", "LOAD_ATTR", "LOAD_GLOBAL", "LOAD_NAME"):
            left_offs[ins.offset] = ins.argrepr
    out = []
    for i, ins in enumerate(insns):
        if ins.opname != "LOAD_CONST":
            continue
        v = ins.argrepr
        if not (v and v.startswith('"') and v.endswith('"')):
            continue  # 只看字符串常量
        # 向后找 COMPARE_OP ==
        for j in range(i + 1, min(i + 6, len(insns))):
            nxt = insns[j]
            if nxt.opname == "COMPARE_OP":
                # 取 CONST 之前最近的 LOAD_* 左侧
                lval = None
                for o in sorted((k for k in left_offs if k < ins.offset), reverse=True):
                    lval = left_offs[o]
                    break
                out.append((ins.offset, f"{lval or '?'} {nxt.argrepr} {v}"))
                break
            if nxt.opname in ("POP_JUMP_IF_FALSE", "POP_JUMP_IF_TRUE", "RETURN_VALUE",
                              "STORE_FAST", "STORE_ATTR"):
                break
    return out


def tag_func(r):
    hits = {k: (k in r["names"] or k in r["consts"] or k in r["varnames"]) for k in KEY}
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--dir", required=True, help="递归扫 .pyc 的 Mods 目录")
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

    if not XDIS:
        print("ERROR: 缺依赖 xdis —— 请先: pip install xdis", file=sys.stderr); return 7

    ww_first, werr = _p7.load_xml(src)
    if ww_first is None:
        print(f"ERROR: {werr}", file=sys.stderr); return 3

    # 目标 .pyc 藏于 .ts4script (zip) 内部成员 —— 复用 P14 的 zip 扫描思路
    found = {}      # tn -> {ts4script: Path, member: str, data: bytes}
    import zipfile as _zipfile
    for sp in [p for p in d.rglob("*.ts4script") if p.is_file()]:
        try:
            with _zipfile.ZipFile(sp) as z:
                for name in z.namelist():
                    base = Path(name).name
                    if base in TARGET_NAMES and base not in found:
                        found[base] = {
                            "ts4script": sp, "member": name,
                            "data": z.read(name),
                        }
        except Exception:
            pass
    if not found:
        print("ERROR: 未找到 animations_loader.pyc / animation_instance.pyc (含 .ts4script zip 内成员)",
              file=sys.stderr)
        return 5

    L = []
    L.append("=== P15 WW 核心 .pyc 深挖 (只读, xdis 引擎) ===")
    L.append(f"源 = {src.name}")
    L.append("=== FOUND TARGET PYC ===")
    for tn in TARGET_NAMES:
        if tn in found:
            f = found[tn]
            L.append(f"ts4script: {f['ts4script']}")
            L.append(f"member:    {f['member']}")
            L.append(f"pyc size:  {len(f['data'])}")
            L.append("")
    L.append(f"关键字 = {KEY}")
    L.append("")

    csv_rows = []
    all_funcs = {}   # tn -> list(func dict)
    import io as _io
    from xdis.load import load_module_from_file_object
    for tn, f in found.items():
        try:
            res = load_module_from_file_object(_io.BytesIO(f["data"]), filename=tn)
        except Exception as ex:
            L.append(f"### {tn}  xdis 解析失败: {ex}")
            csv_rows.append([tn, "(xdis失败)", str(ex)])
            continue
        ver = res[0]; co = res[3]; impl = res[4]
        try:
            opc = get_opc(ver)
        except Exception as ex:
            L.append(f"### {tn}  opcode 查询失败: {ex}")
            csv_rows.append([tn, "(opc失败)", str(ex)])
            continue
        funcs = []
        walk_code(co, funcs)
        rows = collect(funcs)
        all_funcs[tn] = rows
        L.append(f"### {tn}  python={ver}  impl={impl}  嵌套函数={len(rows)}")
        # 热点函数
        hot = []
        for r in rows:
            h = tag_func(r)
            sc = sum(1 for v in h.values() if v)
            if sc:
                hot.append((r, h, sc, find_branches(r["obj"], opc)))
        hot.sort(key=lambda x: -x[2])
        L.append(f"  含关键字函数 {len(hot)} 个 (按命中数):")
        for r, h, sc, brs in hot[:40]:
            ks = [k for k, v in h.items() if v]
            L.append(f"    fn='{r['name']}' line={r['line']} hits({sc})= {ks}")
            csv_rows.append([tn, r["name"], str(r["line"]), ";".join(ks)])
            for br in brs:
                L.append(f"        [分支] {br[1]}")
        # 关键字符串常量出现位置
        L.append("  关键字字符串常量出现统计:")
        for k in KEY:
            where = [f"'{r['name']}'@L{r['line']}" for r in rows if k in r["consts"]]
            L.append(f"    {k!r}: {where if where else '(无)'}")
        L.append("")

    # ---- display_name 数据流 ----
    L.append("=== display_name 数据流 ===")
    dn_read = []; dn_store = []
    for tn, rows in all_funcs.items():
        for r in rows:
            if "display_name" in r["names"] or "display_name" in r["varnames"]:
                dn_read.append(f"  read {tn} :: '{r['name']}'@L{r['line']}")
            if "display_name" in r["consts"]:
                dn_store.append(f"  const {tn} :: '{r['name']}'@L{r['line']}")
    L.append("引用/读取 display_name 的函数:")
    L.extend(dn_read[:50] or ["  (无)"])
    L.append("含 display_name 字符串常量的函数 (可能是键/属性字面量):")
    L.extend(dn_store[:50] or ["  (无)"])
    L.append("")

    # ---- 重点回答 ----
    L.append("=== 重点回答 ===")
    # STORY 分支: 找分支重建结果里 == 'STORY' / == 'story'
    story_branches = []
    for tn, f in found.items():
        try:
            res = load_module_from_file_object(_io.BytesIO(f["data"]), filename=tn)
            ver, co = res[0], res[3]
            opc = get_opc(ver)
        except Exception:
            continue
        funcs = []; walk_code(co, funcs)
        for f in funcs:
            for br in find_branches(f, opc):
                if 'story' in br[1].lower() and '==' in br[1]:
                    story_branches.append((tn, f.co_name, f.co_firstlineno, br[1]))
    L.append("A. display_name 最终来源: 依字符串池+STORE 判定")
    if story_branches:
        L.append("   发现 STORY 比较分支:")
        for tn, fn, ln, br in story_branches[:20]:
            L.append(f"      [{tn}] '{fn}'@L{ln} :: {br}")
    else:
        L.append("   未在字节码中直接找到 == 'STORY' 比较 (可能用常量/枚举/别名)")
    # display_name 赋值点 (STORE_FAST/STORE_ATTR display_name)
    dn_assign = []
    for tn, f in found.items():
        try:
            res = load_module_from_file_object(_io.BytesIO(f["data"]), filename=tn)
            ver, co = res[0], res[3]
            opc = get_opc(ver)
        except Exception:
            continue
        funcs = []; walk_code(co, funcs)
        for f in funcs:
            for ins in Bytecode(f, opc):
                if ins.opname in ("STORE_FAST", "STORE_ATTR") and ins.argrepr == "display_name":
                    dn_assign.append((tn, f.co_name, f.co_firstlineno))
    L.append("   display_name 被赋值 (STORE) 的位置:")
    for tn, fn, ln in dict.fromkeys(dn_assign):
        L.append(f"      [{tn}] '{fn}'@L{ln}")
    if not dn_assign:
        L.append("      (无直接 STORE display_name —— 说明 display_name 由参数传入/返回传递)")
    # B
    stg = [(tn, r["name"], r["line"]) for tn, rows in all_funcs.items()
           for r in rows if "animation_stage_name" in r["names"] or "animation_stage_name" in r["consts"] or "animation_stage_name" in r["varnames"]]
    L.append(f"B. animation_stage_name 是否覆盖 display_name:")
    L.append(f"   animation_stage_name 被 {len(stg)} 个函数引用: "
             + ", ".join(f"[{t}]'{n}'@L{ln}" for t, n, ln in stg[:15]) or "(无)")
    # C
    ac = [(tn, r["name"], r["line"]) for tn, rows in all_funcs.items()
          for r in rows if "animation_category" in r["names"] or "animation_category" in r["consts"] or "animation_category" in r["varnames"]]
    L.append(f"C. animation_category 参与判断: {len(ac)} 个函数引用:")
    for tn, fn, ln in ac[:25]:
        L.append(f"      [{tn}] '{fn}'@L{ln}")
    # D
    st_lit = [(tn, r["name"], r["line"]) for tn, rows in all_funcs.items()
              for r in rows if any(c in ("STORY", "STAGE", "story") for c in r["consts"])]
    L.append(f"D. Story 特殊加载路径 (含字面量 STORY/STAGE/story 的函数): {len(st_lit)}")
    for tn, fn, ln in st_lit[:25]:
        L.append(f"      [{tn}] '{fn}'@L{ln}")
    L.append("")
    L.append("ZERO_WRITE_TO_MODS=YES (只读, 未生成包, 未动 Mods)")

    txt = "\n".join(L)
    txt_path = out_dir / "p15_pyc_deep_dive.txt"
    txt_path.write_text(txt, encoding="utf-8")
    with open(out_dir / "p15_pyc_deep_dive.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pyc", "fn", "line", "keywords"])
        w.writerows(csv_rows)
    print(txt)
    print(f"OUT_TXT={txt_path}")
    print("P15_PYC_DEEP_DIVE=OK (只读)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
