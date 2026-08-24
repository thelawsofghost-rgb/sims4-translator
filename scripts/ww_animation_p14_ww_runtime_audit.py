#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P14 WW 核心 runtime 审计 (只读, 扫 .ts4script zip 内 Python 源码)。

背景 (已确立):
  - P11.5: Story 条目内无 display/text/hash/STBL-ref key
  - P4:    本包内无非 WW 资源引用 WW XML instance
  - P13:   Mods 目录【无】外部 package/script 只注册 Caught Cheating
    -> 外部 registry 路线排除; Story 分支必在 WW 核心 runtime 里

本审计目标 (只读): 递归扫 --dir 下所有 .ts4script (zip), 解出内嵌
成员(源 .py 或编译 .pyc), 全文(小写)检索以下关键字:
  animation_category / STORY / story / animation_stage_name /
  animation_next_stages / register_animation / display_name /
  ww_animations / animation_registry

重点:
  1) 定位哪个成员文件处理 animation_category=STORY 分支
  2) 输出命中点【上下文窗口】(命中前后 N 字节/行), 直接看到分支逻辑
     (STORY 如何影响 display_name 读取路径)
  3) 对比普通动画与 Story 动画代码路径差异的依据行

不改 WW_ANIM_XML (只读)。不生成包, 不碰 Mods (ZERO_WRITE_TO_MODS=YES)。

fail-closed: 源缺->2; 无 WW->3; 目录缺->4; 目录内无 ts4script->5;
  全部 ts4script 无法解压->6。

用法 (Windows, 只读):
  python scripts/ww_animation_p14_ww_runtime_audit.py \
      "<SRC.package>" --dir "C:\\...\\Mods" [--out-dir output/ww_p14]
"""
import argparse
import csv
import importlib.util
import re
import sys
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
_spec = importlib.util.spec_from_file_location(
    "ww_canary_builder", SCRIPT_DIR / "ww_animation_canary_builder.py")
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)
import ww_animation_m2_diff_forensic as _diff
import ww_animation_p1_resource_forensic as _p1
import ww_animation_p7_story_chain_audit as _p7

WW_ANIM_XML = 0x7DF2169C
OUT_DIR = Path("output/ww_p14")

# P14 关键字 (小写)。按重要度排序。
KW = ["animation_category", "animation_stage_name", "animation_next_stages",
      "register_animation", "display_name", "story", "ww_animations",
      "animation_registry", "story_id", "animation_id"]
# STORY 字面量(常作为 animation_category 取值 / 类名 / 常量)
STORY_LIT = ["story"]
# 单个成员读取上限 (避免超大无用文件拖垮)
MEM_CAP = 16_000_000


def _read_script(fpath):
    """解 .ts4script (zip) 为 {member: lower_text} + {member: raw_len}。
    压缩 (pyc) 内字符串字面量以 utf-8/latin 可读, 按 latin-1 兜底解码。
    返回 (members) 或 None(无法解压)。"""
    try:
        with zipfile.ZipFile(fpath) as z:
            members = {}
            names = z.namelist()
            for n in names:
                info = z.getinfo(n)
                if info.file_size > MEM_CAP:
                    members[n] = "", info.file_size, info.compress_size
                    continue
                try:
                    data = z.read(n)
                except Exception:
                    members[n] = "", info.file_size, info.compress_size
                    continue
                # 优先 utf-8, 失败 latin-1 (pyc 字节兜底)
                try:
                    txt = data.decode("utf-8", errors="replace").lower()
                except Exception:
                    txt = data.decode("latin-1", errors="replace").lower()
                members[n] = txt, len(data), info.compress_size
            return members
    except Exception:
        return None


def _ctx(text, idx, k, win=180):
    """返回命中点上下文窗口 (原大小写不可恢复, 用 raw 小写窗口即可)。"""
    a = max(0, idx - win)
    b = min(len(text), idx + len(k) + win)
    return text[a:b]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--dir", required=True, help="递归扫描的 Mods 目录")
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

    # 源包: 校验 + 取 WW XML instance(用于上下文锚点, 不强依赖)
    ww_first, werr = _p7.load_xml(src)
    if ww_first is None:
        print(f"ERROR: {werr}", file=sys.stderr); return 3

    # 递归枚举 ts4script
    scripts = sorted(d.rglob("*.ts4script"))
    if not scripts:
        print(f"ERROR: {d} 下无任何 .ts4script", file=sys.stderr); return 5

    L = []
    L.append("=== P14 WW 核心 runtime 审计 (只读, .ts4script 内 Python 源码) ===")
    L.append(f"源 = {src.name}  (WW_ANIM_XML instance=0x{ww_first.instance_id:016x})")
    L.append(f"扫描目录 = {d}  (递归)  发现 .ts4script = {len(scripts)}")
    L.append(f"关键字 = {KW}")
    L.append("")

    csv_rows = []
    loosed = 0
    unpacked = 0
    for sp in scripts:
        members = _read_script(sp)
        rel = sp.relative_to(d)
        if members is None:
            L.append(f"### {rel} [无法解压/非zip]")
            csv_rows.append([str(rel), "(zip损坏)", "", "", ""])
            loosed += 1
            continue
        unpacked += 1
        L.append(f"### {rel}  成员数={len(members)}")
        # 每个成员的关键字命中统计
        file_hits = []
        for mname, (txt, raw_len, comp_len) in members.items():
            mname_l = mname.lower()
            hits = [k for k in KW if k in txt]
            # 是否有 STORY 字面量(独立词)
            has_story = ("story" in txt or "story" in mname_l)
            if hits or has_story:
                score = sum(len(set(re.findall(r'\b' + re.escape(k) + r'\w*', txt))) for k in hits)
                file_hits.append((mname, sorted(set(hits)), has_story, score, raw_len))
        # 排序: 含 animation_category 优先, 再按命中种类
        def sk(x):
            h = set(x[1])
            pri = 4 if "animation_category" in h else (3 if "animation_stage_name" in h
                  else (2 if "story" in x[1] or x[2] else 1))
            return (-pri, -x[3])
        file_hits.sort(key=sk)
        for mname, hits, has_story, score, rl in file_hits[:40]:
            L.append(f"    {mname}  hits={sorted(set(hits))}  story={has_story}  bytes={rl}")
            csv_rows.append([str(rel), mname, ";".join(sorted(set(hits))),
                             "Y" if has_story else "N", str(rl)])
    L.append("")

    # ---- 全 zip 级合并: 找最佳分支候选 (animation_category + STORY 同文件) ----
    if unpacked == 0:
        print(f"ERROR: 全部 {len(scripts)} 个 .ts4script 均无法解压/非zip", file=sys.stderr)
        return 6
    L.append("=== 分支定位: 哪个成员文件同时含 animation_category 与 STORY 处理 ===")
    L.append("(展示命中点上下文窗口, 直接看 STORY 分支如何影响 display_name)")
    shown = 0
    for sp in scripts:
        members = _read_script(sp)
        if members is None:
            continue
        rel = sp.relative_to(d)
        # 合并全文用于跨成员; 每个成员内定位
        for mname, (txt, raw_len, comp_len) in members.items():
            hints = [k for k in ("animation_category", "animation_stage_name",
                                 "animation_next_stages", "register_animation") if k in txt]
            st = [s for s in STORY_LIT if s in txt]
            if not (hints and st):
                continue
            if shown >= 16:
                break
            shown += 1
            L.append(f"### {rel} :: {mname}")
            # 优先展示 animation_category 附近的 STORY 窗口
            for k in ("animation_category", "animation_stage_name"):
                i = txt.find(k)
                while i != -1:
                    win = _ctx(txt, i, k)
                    L.append(f"    [{k}] ...{win}...")
                    nxt = txt.find(k, i + len(k))
                    if nxt == i:
                        break
                    i = nxt
            L.append("")
        if shown >= 16:
            break
    if shown == 0:
        L.append("(未在任何成员中同时找到 animation_category+story 处理块)")
    L.append("")

    # ---- 结论 ----
    L.append("=== 结论 ===")
    L.append(f"共 {len(scripts)} 个 ts4script, 成功解包 {unpacked}, 损坏/非zip {loosed}")
    with_cc_branch = 0
    for sp in scripts:
        members = _read_script(sp)
        if members is None:
            continue
        for mname, (txt, rl, cl) in members.items():
            if "animation_category" in txt and "story" in txt:
                with_cc_branch += 1
    if with_cc_branch:
        L.append(f"找到 {with_cc_branch} 个成员同时含 animation_category + story")
        L.append("-> WW runtime 很可能在 animation_category==STORY 时走独立路径")
        L.append("   (P15: 精确读该分支, 确认 Story display_name 从哪来)")
    else:
        L.append("未找到明确 animation_category+story 分支 (见上方命中明细)")
        L.append("-> 可能 WW 用常量/枚举(如 ANIM_CATEGORY_STORY) 或属性名不同;")
        L.append("   需扩大关键字范围或检查 .package 资源。")
    L.append("")
    L.append("ZERO_WRITE_TO_MODS=YES (只读, 未生成包, 未动 Mods)")

    txt_out = "\n".join(L)
    txt_path = out_dir / "p14_ww_runtime_audit.txt"
    txt_path.write_text(txt_out, encoding="utf-8")
    csv_path = out_dir / "p14_ww_runtime_audit.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["script", "member", "keywords", "has_story", "bytes"])
        w.writerows(csv_rows)

    print(txt_out)
    print(f"OUT_TXT={txt_path}")
    print(f"OUT_CSV={csv_path}")
    print("P14_WW_RUNTIME_AUDIT=OK (只读)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
