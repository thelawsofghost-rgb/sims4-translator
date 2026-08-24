#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P23 Story STBL override package 生成 (只读源, 新建 package)

目标: 把 Story animation 的显示文本替换为中文, 通过 **STBL override package**
实现 —— 完全不修改原 WW_ANIM_XML。

依据:
  - P18/P19/P20/P21: Story display = get_localized_string_id(hash("story_animations."+id)),
    hash 即 FNV-32。
  - P22: 已确认 fnv32("story_animations."+str(animation_id)) 命中 STBL key。
本脚本:
  1. 从源 WW package 读取其 STBL 资源 (type 0x220557DA), 提取:
       type / group / instance / locale(即 instance) / compression meta / header_comp / major/minor
  2. 对目标 ordinal (默认 299-306), 从源 WW_ANIM_XML 提取真实 animation_id,
     计算 key = fnv32("story_animations."+str(id)), 映射到中文 (默认 抓奸 N / 可 -t 自定义)。
  3. 基于源 STBL 的 key->text 合并覆盖层: 保留其它所有 key, 只覆盖目标 key 为中文。
     若目标 key 在源 STBL 中不存在, 也新增 (override 仍生效, 因游戏按 key 查表)。
  4. 写出新 package (仅含该 STBL, source-faithful meta, 同 type/group/instance/locale)。
  5. 静态验证: 重读新 package -> 每个 key 存在 / text==中文 / TGI==源 TGI (locale 一致)。

输出: pkg 路径 + STBL 资源信息 + key 覆盖列表 + 验证结果。

fail-closed: 源缺->2; 无 WW_ANIM_XML->3; --dir 缺->4(占位, 本脚本不需要 Mods 写);
  源无 STBL->9; 构建/验证失败->6; 正常 0。
  只读源; 只写 --out_dir 内的新 package; ZERO_WRITE_TO_MODS=YES (除非 --out-dir 指向 Mods)。

用法:
  python scripts\\ww_animation_p23_story_stbl_override.py \\
      "<WW.package>" --out-dir output\\ww_p23 [--ordinals 299-306] \\
      [-t '299=抓奸 1' -t '300=抓奸 2' ...]
"""
import argparse
import csv
import struct as _struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
try:
    from dbpf_fast import safe_parse
    from backend import get_backend
except Exception as ex:
    print(f"ERROR: 依赖加载失败: {ex}", file=sys.stderr)
    sys.exit(5)

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import ww_animation_p22_story_stbl as P22        # parse_stbl / fnv32 / parse_anim_id / extract_entry_anim_id
import ww_animation_canary_builder as wb          # build_package / read_entry_meta_raw / read_body_raw

WW_ANIM_XML = 0x7DF2169C
STBL = 0x220557DA
OUT_DIR = Path("output/ww_p23")


def serialize_stbl_v5(entries, version=5):
    """entries: dict{key:int -> text:str}. 产出 STBL v5 二进制 (未压缩)。"""
    pairs = [(k, t) for k, t in entries.items()]
    body = b"".join(
        _struct.pack("<IBH", k, 0, len(t.encode("utf-8"))) + t.encode("utf-8")
        for k, t in pairs
    )
    head = b"STBL" + _struct.pack("<HB", version, 0)
    head += _struct.pack("<Q", len(pairs))
    head += b"\x00\x00" + _struct.pack("<I", len(body))
    return head + body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--ordinals", default="299-306", help="ordinal 区间, 默认 299-306")
    ap.add_argument("-t", "--text", action="append", default=[], metavar="O=中文",
                    help="ordinal=中文 覆盖映射, 可多次; 缺省用 抓奸 N")
    ap.add_argument("--game-dir", default=None, help="(本脚本不需要; 仅兼容 P22 调用习惯)")
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: 源不存在 {src}", file=sys.stderr); return 2
    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    ordinals = P22.parse_ordinals(a.ordinals)

    # 1) 从源提取 WW_ANIM_XML entry + STBL 资源
    # P22 的 key 定位逻辑【直接复用】: ① 从 XML entry 提取 animation_id (forward),
    # ② 若 forward 失败, 用 P22 反向文本匹配 (Caught Cheating N -> STBL key) 兜底。
    # 不做任何重新从 XML root 搜寻 animation_id 的额外实现 —— 完全等同 P22。
    ww_first, werr = P22.P7.load_xml(src)
    if ww_first is None:
        print(f"ERROR: {werr}", file=sys.stderr); return 3
    blocks, berr = P22.P7.ordinal_blocks(src, ww_first, ordinals)
    if berr is not None:
        print(f"ERROR: {berr}", file=sys.stderr); return 3

    idx, ierr = safe_parse(str(src))
    if ierr is not None or idx is None:
        print(f"ERROR: 源解析失败: {ierr}", file=sys.stderr); return 3
    stbl_entries = [e for e in idx.entries if e.type_id == STBL]
    if not stbl_entries:
        print("ERROR: 源包内无 STBL 资源 (0x220557DA)", file=sys.stderr); return 9
    src_stbl_e = stbl_entries[0]
    backend = get_backend("readonly").open(str(src))
    src_body = backend.read_small_resource(src_stbl_e) or b""
    src_map = P22.parse_stbl(src_body)
    # 反向: 源 STBL 文本 -> key 集合 (P22 section-4 兜底)
    key_by_text = {}
    for k, txt in src_map.items():
        key_by_text.setdefault(txt, set()).add(k)

    major, minor, header_comp, src_meta = wb.read_entry_meta_raw(src)
    meta_map = {(m["type"], m["group"], m["inst"]): m for m in src_meta}
    m0 = meta_map.get((src_stbl_e.type_id, src_stbl_e.group_id, src_stbl_e.instance_id)) or {}

    L = []
    L.append("=== P23 Story STBL override package 生成 (新建, 只读源) ===")
    L.append(f"源 = {src.name}")
    L.append(f"ordinals = {ordinals}")
    L.append(f"源 STBL: type=0x{src_stbl_e.type_id:08X} group=0x{src_stbl_e.group_id:08X} "
             f"inst=0x{src_stbl_e.instance_id:016X} (locale 编码于 instance)  keys={len(src_map)}")
    L.append("")

    # 用户自定义覆盖映射 ordinal->中文
    override_zh = {}
    for t in a.text:
        if "=" in t:
            o_s, zh = t.split("=", 1)
            override_zh[int(o_s)] = zh
    L.append("=== 1) 提取 animation_id + 计算 STBL key + 中文映射 (复用 P22 逻辑) ===")
    L.append("")
    rows = []
    overrides = {}     # key -> 中文
    for o in ordinals:
        zh = override_zh.get(o, f"抓奸 {o - 298}")
        # route A (forward): P22.extract_entry_anim_id —— 同一函数, 同一 blocks
        el = blocks[o]
        tag, raw, aid = P22.extract_entry_anim_id(el)
        if aid is not None:
            key_str = "story_animations." + str(aid)
            hk = P22.fnv32(key_str)
            src_text = src_map.get(hk, "(源STBL无此key)")
            overrides[hk] = zh
            L.append(f"  [{o}] animation_id={aid} (tag={tag}, raw={raw!r})  key={key_str!r}  "
                     f"fnv32=0x{hk:08X}  源文本={src_text!r} -> 中文={zh!r}   [route=XML]")
            rows.append({"ordinal": o, "animation_id": aid, "key_str": key_str,
                         "fnv32": f"0x{hk:08X}", "zh": zh, "src_text": src_text,
                         "status": "OK"})
            continue
        # route B (reverse fallback): P22 section-4 —— 按原显示文本找 key
        probe = f"Caught Cheating {o - 298}"
        shs = sorted(key_by_text.get(probe, set()))
        if shs:
            hk = shs[0]
            overrides[hk] = zh
            L.append(f"  [{o}] !! XML 无 animation_id (tag={tag}, raw={raw!r}); 反向匹配 "
                     f"{probe!r} -> STBL key 0x{hk:08X}  [route=TEXT-FALLBACK]")
            rows.append({"ordinal": o, "animation_id": "", "key_str": "(反向文本)",
                         "fnv32": f"0x{hk:08X}", "zh": zh, "src_text": probe,
                         "status": "TEXT-FALLBACK"})
        else:
            L.append(f"  [{o}] !! 无 animation_id 且反向文本 {probe!r} 未命中 -> 无法确定 key, 计入缺失")
            rows.append({"ordinal": o, "animation_id": "", "key_str": "", "fnv32": "",
                         "zh": zh, "src_text": "", "status": "NO_KEY"})
    L.append("")

    # 硬门: 必须解析出全部目标 ordinal 的 key, 否则 fail-closed (不静默跳过)
    expected = len(ordinals)
    resolved = len(overrides)
    L.append(f"  目标 ordinal={expected}  已解析 key={resolved}")
    if resolved < expected:
        L.append("  !! 存在未解析 ordinal; fail-closed, 不写出覆盖包。")
        missing = [r["ordinal"] for r in rows if not r["fnv32"]]
        L.append(f"    未解析 ordinal: {missing}")
        txt = "\n".join(L)
        (out_dir / "p23_story_stbl_override.txt").write_text(txt, encoding="utf-8")
        print(txt)
        print("P23_STORY_OVERRIDE=PARTIAL_RESOLVE\n")
        return 6
    L.append("")

    # 2) 合并覆盖: 保留源全部 key, 覆盖目标 key
    L.append("=== 2) 合并覆盖 (源 STBL 全部 key + 目标 key 中文) ===")
    merged = dict(src_map)
    for k, zh in overrides.items():
        merged[k] = zh
    L.append(f"  源 keys={len(src_map)}  覆盖 keys={len(overrides)}  合并后 keys={len(merged)}")
    if src_map:
        same = sum(1 for k, v in merged.items() if src_map.get(k) == v)
        L.append(f"  未变化 keys={same}  变化或新增 keys={len(merged) - same}")
    L.append("")

    # 3) 写出新 package (source-faithful meta, 同 TGI)
    new_body = serialize_stbl_v5(merged)
    out_pkg = out_dir / f"{src.stem}_stbl_override.package"
    meta_for_stbl = {
        "comp_state": False,
        "comp_type": 0,
        "mem_size": len(new_body),
        "offset_high_bit": m0.get("offset_comp"),
        "size_high_bit": m0.get("size_comp"),
    }
    wb.build_package(
        [(src_stbl_e.type_id, src_stbl_e.group_id, src_stbl_e.instance_id, new_body, meta_for_stbl)],
        out_pkg, header_comp=header_comp, major=major, minor=minor,
    )
    L.append("=== 3) 写出新 package ===")
    L.append(f"  新package = {out_pkg}")
    L.append(f"  STBL: type=0x{src_stbl_e.type_id:08X} group=0x{src_stbl_e.group_id:08X} "
             f"inst=0x{src_stbl_e.instance_id:016X} (locale 同源)")
    L.append(f"  major={major} minor={minor} header_comp=0x{header_comp:X}")
    L.append("")

    # 4) 静态验证
    L.append("=== 4) 静态验证 (重读新 package) ===")
    v_ok = True
    v_idx, v_err = safe_parse(str(out_pkg))
    if v_err is not None or v_idx is None:
        L.append("  !! 重读失败; 验证不通过"); v_ok = False
    else:
        v_stbl = [e for e in v_idx.entries if e.type_id == STBL]
        if len(v_stbl) != 1:
            L.append(f"  !! 新包 STBL 资源数={len(v_stbl)} (需恰 1); 验证不通过"); v_ok = False
        else:
            e = v_stbl[0]
            tgi_ok = (e.type_id == src_stbl_e.type_id and e.group_id == src_stbl_e.group_id
                      and e.instance_id == src_stbl_e.instance_id)
            L.append(f"  TGI: type=0x{e.type_id:08X} group=0x{e.group_id:08X} "
                     f"inst=0x{e.instance_id:016X}  {'== 源 (locale 一致) OK' if tgi_ok else '!! != 源'}")
            if not tgi_ok:
                v_ok = False
            vb = get_backend("readonly").open(str(out_pkg)).read_small_resource(e) or b""
            vmap = P22.parse_stbl(vb)
            missing, wrong = [], []
            for k, zh in overrides.items():
                if k not in vmap:
                    missing.append(k); v_ok = False
                elif vmap[k] != zh:
                    wrong.append((k, vmap[k], zh)); v_ok = False
            L.append(f"  覆盖 key 校验: 需 {len(overrides)}  缺失={len(missing)}  文本错误={len(wrong)}")
            for k in missing:
                L.append(f"    MISSING 0x{k:08X}")
            for k, got, want in wrong:
                L.append(f"    WRONG 0x{k:08X} got={got!r} want={want!r}")
            if not missing and not wrong:
                L.append("  => 所有覆盖 key 存在且文本正确 (中文)")
    L.append("")

    # 5) 结论 + 落点说明
    L.append("=== 5) 结论 ===")
    if v_ok:
        L.append("  => STBL override package 生成 + 静态验证通过")
        L.append("  => 安装: 把新 package 放入 Mods（不改原 WW package 一行）")
        L.append("  => 若游戏仍显示英文: 该 STBL 可能非游戏实际读取的那张表, 需按 P22 反向文本匹配定位真实表")
    else:
        L.append("  => 验证失败 (见 4); 未交付。fail-closed 保持。")
    L.append("")
    L.append("ZERO_WRITE_TO_MODS=YES (只写 --out-dir; 不改源)")

    txt = "\n".join(L)
    (out_dir / "p23_story_stbl_override.txt").write_text(txt, encoding="utf-8")
    with open(out_dir / "p23_story_stbl_override.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ordinal", "animation_id", "key_str", "fnv32", "zh", "src_text", "status"])
        for r in rows:
            w.writerow([r["ordinal"], r["animation_id"], r["key_str"], r["fnv32"],
                        r["zh"], r["src_text"], r["status"]])
    print(txt)
    print(f"OUT_PKG={out_pkg}")
    print(f"OUT_TXT={out_dir/'p23_story_stbl_override.txt'}")
    print(f"OUT_CSV={out_dir/'p23_story_stbl_override.csv'}")
    print("P23_STORY_OVERRIDE=" + ("OK" if v_ok else "VERIFY_FAIL"))
    return 0 if v_ok else 6


if __name__ == "__main__":
    sys.exit(main())
