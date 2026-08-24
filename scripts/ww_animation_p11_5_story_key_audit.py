#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P11.5 Story text-key / hash 审计 (只读) — 找 Story 用的文本 key, 而非文本本身

结论背景: P11 已排除 raw_display / STBL 文本 / tuningXML 命中。
  (P9/P10: animation_raw_display_name 对 Story 不生效)
  -> Story 显示名很可能通过 hash/key 间接读取 (runtime 先去某表查 key ->
     再据 key 取文本)。本审计专注: ordinal 124-126 vs 299-306 的
     【key 类字段】差异, 不搜字符串。

目标:
  1. 扫描 WW_ANIM_XML ordinal 124-126 vs 299-306 的所有:
     - hash / 64bit hex / string_id / text_key / name_key / display_key
     - STBL reference / TGI reference / GUID
  2. 输出:
     a. Addicted 独有 key (124-126 有, 299-306 无)
     b. Caught Cheating 独有 key (299-306 有, 124-126 无)
     c. 两者字段 root path 差异 (如 root[6][1] path)
  3. 重点回答: Story 显示名是不是通过 hash/key 间接读取?

判定:
  - 若 Story 有而 Normal 无的 key 字段(含 hash/TGI 形态) -> 很可能 Story 用
    该 key 间接取文本 -> 需给 Story 的 key 建立显示映射(P12)
  - 若两者 key 集合一致且 key 值一致 -> Story 与 Normal 读取机制同源,
    显示差异在 WW runtime 对 Story 的特殊处理, 或另一 package
  - 若 Normal 有而 Story 无的 key -> 缺注册键导致 Story 不注册

fail-closed: 源缺->2; 无 WW->3; ordinal 越界->4; 仅读, 不生成包, 不写 Mods。

用法 (Windows):
  python scripts/ww_animation_p11_5_story_key_audit.py "<SRC.package>" \
      [--ok 124 125 126] [--fail 299 ... 306] [--out-dir output/ww_p115]
产物: output/ww_p115/p11_5_story_key_audit.txt + .csv
"""
import argparse
import csv
import importlib.util
import re
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

WW_ANIM_XML = 0x7DF2169C
OUT_DIR = Path("output/ww_p115")

# key 类字段名片段 (n= 或 root path 末端含这些)
KEY_NAME_FRAG = ("hash", "string_id", "text_key", "name_key", "display_key",
                 "_key", "key_", "stbl", "stbl_key", "tgi", "guid", "instance",
                 "ref", "id", "msd", "tuning", "link", "pointer", "resource")
# key 值形态: 16 位/8 位 hex、GUID、TGI triple、纯数字长串(>8位十进制)
HEX16 = re.compile(r"0x[0-9A-Fa-f]{16}")
HEX8 = re.compile(r"0x[0-9A-Fa-f]{8}")
RAW16 = re.compile(r"\b[0-9A-Fa-f]{16}\b")
RAW8 = re.compile(r"\b[0-9A-Fa-f]{8}\b")
TRI = re.compile(r"0x[0-9A-Fa-f]{8}[,;/\s]+0x[0-9A-Fa-f]{16}", re.I)
LONGDEC = re.compile(r"\b[0-9]{9,}\b")


def is_key_value(v):
    """值是不是 key/hash 形态 (不含纯 display 单词)。"""
    v = str(v)
    if not v:
        return False
    if v.lower() in ("true", "false", "none", "null"):
        return False
    if re.fullmatch(r"[\w .\-'/()]+", v) and " " in v.strip():  # 人类可读短语
        return False
    return bool(HEX16.search(v) or HEX8.search(v) or RAW16.search(v)
                or TRI.search(v) or LONGDEC.search(v) or re.fullmatch(r"\d+", v))


def is_key_name(n):
    nl = (n or "").lower()
    return any(f in nl for f in KEY_NAME_FRAG)


def build_path_map(root):
    """一次遍历, 为 root 下所有节点计算 root-path 字符串与父子序号。
    不依赖 list.index(cur), 遍历时直接记录 child index。
    返回 {"node_id": "root[i][j]..."}。"""
    paths = {id(root): "root"}
    stack = [(root, "root")]
    while stack:
        el, el_path = stack.pop()
        for idx, c in enumerate(el):
            c_path = f"{el_path}[{idx}]"
            paths[id(c)] = c_path
            stack.append((c, c_path))
    return paths


def node_root_path(root, node, paths):
    """用 build_path_map 的结果取路径; 不再回溯/不再 list.index。"""
    return paths.get(id(node), "root?")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="源 WW package")
    ap.add_argument("--ok", nargs="*", type=int, default=[124, 125, 126])
    ap.add_argument("--fail", nargs="*", type=int,
                    default=[299, 300, 301, 302, 303, 304, 305, 306])
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: 源不存在 {src}", file=sys.stderr); return 2
    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    ww_first, err = _p7.load_xml(src)
    if ww_first is None:
        print(f"ERROR: {err}", file=sys.stderr); return 3
    all_ords = list(dict.fromkeys(a.ok + a.fail))
    blocks, err = _p7.ordinal_blocks(src, ww_first, all_ords)
    if blocks is None:
        print(f"ERROR: {err}", file=sys.stderr); return 4

    # 构建每 ord 的 root-path 映射 (一次遍历带 index, 不依赖 list.index)
    path_maps = {}
    for o, root in blocks.items():
        path_maps[o] = build_path_map(root)

    ok_set, fail_set = set(a.ok), set(a.fail)

    # 收集每个 ord 的 key 类字段 + 值形态
    # rows: (ord, tag, n, value, path, is_keyname, is_keyval, tgis)
    key_rows = []
    for o in all_ords:
        root = blocks[o]
        paths = path_maps[o]
        for node in root.iter():
            lt = node.tag.rsplit('}', 1)[-1] if isinstance(node.tag, str) else None
            if lt not in ("T", "E", "I"):
                continue
            n = node.get("n") or ""
            v = (node.text or "").strip()
            p = node_root_path(root, node, paths)
            tgis = _p7.find_tgis(v)
            is_kn = is_key_name(n)
            is_kv = is_key_value(v) or bool(tgis)
            if is_kn or is_kv or tgis:
                key_rows.append((o, lt, n, v, p, is_kn, is_kv, ";".join(tgis)))

    # 聚合 key 字段 (以 root path + tag + n 为键, 归并多值)
    # 先按 n 聚合 (同名字段跨 ord), 记录 path 与值
    by_field = {}   # n -> {ord -> (path, value, is_kn, is_kv)}
    for (o, lt, n, v, p, is_kn, is_kv, tgis) in key_rows:
        key = n if n else f"<{lt}>@{p}"
        by_field.setdefault(key, {})[o] = (p, v, is_kn, is_kv, tgis)

    L = []
    L.append("=== P11.5 STORY TEXT-KEY / HASH 审计 (只读) ===")
    L.append(f"WW_ANIM_XML instance = 0x{ww_first.instance_id:016X}")
    L.append(f"Normal(Addicted)= {a.ok}")
    L.append(f"Story(Caught Cheating)= {a.fail}")
    L.append(f"背景: P9/P10 证 raw_display 对 Story 不生效; P11 无 STBL/tuning 命中")
    L.append(f"目标: 找 Story 用的文本 key (hash/key/ref/TGI), 而非文本本身")
    L.append("")

    # --- key 字段集合差异 ---
    add_only, cc_only = [], []
    for field, ords in by_field.items():
        s = set(ords.keys())
        if s & fail_set and not (s & ok_set):
            cc_only.append(field)
        elif s & ok_set and not (s & fail_set):
            add_only.append(field)

    L.append("=== 1) Addicted 独有 key 字段 (124-126 有, 299-306 无) ===")
    if add_only:
        for f in sorted(add_only):
            ords = by_field[f]
            o = a.ok[0]
            p, v, is_kn, is_kv, t = ords.get(o, ("", "", False, False, ""))
            L.append(f"  {f!r}  path={p or '—'}  value={v or '(缺失)'!r}  key形态值={is_kv}  TGI={t or '-'}")
    else:
        L.append("  (无 — 无 Addicted 独有 key 字段)")
    L.append("")

    L.append("=== 2) Caught Cheating 独有 key 字段 (299-306 有, 124-126 无) ===")
    if cc_only:
        for f in sorted(cc_only):
            ords = by_field[f]
            o = a.fail[0]
            p, v, is_kn, is_kv, t = ords.get(o, ("", "", False, False, ""))
            L.append(f"  {f!r}  path={p or '—'}  value={v or '(缺失)'!r}  key形态值={is_kv}  TGI={t or '-'}")
    else:
        L.append("  (无 — 无 Caught Cheating 独有 key 字段)")
    L.append("")

    # --- 同名字段 path / value 差异 ---
    L.append("=== 3) 同名字段 root path / 取值 差异 (Story vs Normal) ===")
    diff_fields = []
    for field, ords in by_field.items():
        ok_ords = {o: ords[o] for o in ords if o in ok_set}
        fa_ords = {o: ords[o] for o in ords if o in fail_set}
        if not ok_ords or not fa_ords:
            continue
        ok_paths = {p for (p, v, k, kv, t) in ok_ords.values()}
        fa_paths = {p for (p, v, k, kv, t) in fa_ords.values()}
        ok_vals = {v for (p, v, k, kv, t) in ok_ords.values()}
        fa_vals = {v for (p, v, k, kv, t) in fa_ords.values()}
        if ok_paths != fa_paths or ok_vals != fa_vals:
            diff_fields.append((field, sorted(ok_paths), sorted(ok_vals),
                                sorted(fa_paths), sorted(fa_vals)))
    if diff_fields:
        for f, op, ov, fp, fv in sorted(diff_fields, key=lambda x: x[0]):
            L.append(f"  {f!r}:")
            L.append(f"    NORMAL path={op} value={ov[:6]}")
            L.append(f"    STORY  path={fp} value={fv[:6]}")
    else:
        L.append("  (所有同名字段 path 与 value 均一致)")
    L.append("")

    # --- 4) 结构 root-path 全对比 (字段路径差异, 含非 key 字段) ---
    L.append("=== 4) 全字段 root path 对比 (结构差异) ===")
    # 完整字段路径集: 每个 field 为所有分析 ordinal 都建条目(缺失用空占位),
    # 保证报告阶段 .get() 取不到时不抛 KeyError。
    full_paths = {}   # n -> {ordinal -> (path, value)}
    present_in = {}   # n -> set(ordinal) 真实出现(不含占位), 供独有字段判定
    for o in all_ords:
        root = blocks[o]
        paths = path_maps[o]
        for node in root.iter():
            lt = node.tag.rsplit('}', 1)[-1] if isinstance(node.tag, str) else None
            if lt not in ("T", "E", "I"):
                continue
            n = node.get("n") or ""
            v = (node.text or "").strip()
            p = node_root_path(root, node, paths)
            full_paths.setdefault(n, {})[o] = (p, v)
            present_in.setdefault(n, set()).add(o)
    # 为每个见过的字段补齐所有 ordinal (缺失 -> 空占位), 杜绝 KeyError
    for n in list(full_paths):
        for o in all_ords:
            full_paths[n].setdefault(o, ("", ""))
    only_in_add = {n for n, s in present_in.items() if s & ok_set and not (s & fail_set)}
    only_in_cc = {n for n, s in present_in.items() if s & fail_set and not (s & ok_set)}
    if only_in_add:
        L.append("  仅 Addicted 有的字段:")
        for n in sorted(only_in_add):
            p, v = full_paths[n].get(a.ok[0], ("", ""))
            L.append(f"    {n!r} path={p or '—'} value={v or '(缺失)'}")
    if only_in_cc:
        L.append("  仅 Caught Cheating 有的字段:")
        for n in sorted(only_in_cc):
            p, v = full_paths[n].get(a.fail[0], ("", ""))
            L.append(f"    {n!r} path={p or '—'} value={v or '(缺失)'}")
    if not only_in_add and not only_in_cc:
        L.append("  (无仅单系列字段 — 结构字段集合一致)")
    L.append("")

    # --- 重点回答 ---
    L.append("=== 重点回答: Story 显示名是否通过 hash/key 间接读取? ===")
    any_fail_key = any(r[6] or r[5] for r in key_rows if r[0] in fail_set)
    fail_key_fields = sorted({r[2] for r in key_rows if r[0] in fail_set and (r[5] or r[6])})
    L.append(f"  Story(299-306) key 类字段命中数 = {len(fail_key_fields)}: {fail_key_fields[:20]}")
    if fail_key_fields:
        L.append("  -> Story 条目内有 key/hash 形态字段, 可能通过 key 间接取文本")
        L.append("     下一步(P12): 把这些 key 映射到显示文本 (查 STBL/全局表)")
    else:
        L.append("  -> Story 条目内【无 key/hash 形态字段】")
        L.append("     显示不在 WW_ANIM_XML 内 -> 需查 WW runtime 全局 story 表/另一包")
    L.append("")

    txt = "\n".join(L)
    txt_path = out_dir / "p11_5_story_key_audit.txt"
    txt_path.write_text(txt, encoding="utf-8")

    csv_path = out_dir / "p11_5_story_key_audit.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ordinal", "series", "tag", "n", "path", "value",
                    "is_keyname", "is_keyvalue", "tgis"])
        for (o, lt, n, v, p, is_kn, is_kv, tgis) in key_rows:
            w.writerow([o, "STORY" if o in fail_set else "NORMAL", lt, n, p, v,
                        int(is_kn), int(is_kv), tgis])
    print(txt)
    print(f"OUT_TXT={txt_path}")
    print(f"OUT_CSV={csv_path}")
    print("P11_5_STORY_KEY_AUDIT=OK (只读, 未生成包, 未动 Mods)")
    print("ZERO_WRITE_TO_MODS=YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
