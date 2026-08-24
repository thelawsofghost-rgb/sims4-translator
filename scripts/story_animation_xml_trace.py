#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
story_animation_xml_trace.py —— 只读: 扫 WW_Nevely42_Animations.package 及全部 XML 资源

方向变更: Story 文本未走 TurboLocalizedString(hash,text) 构造链。回本源, 只追 XML 资源
(WWNNevely42_Animations.package 的 WW_ANIM_XML), 定位:
  1. animation_id
  2. ordinal=299-306 附近数据
  3. animation_raw_display_name
  4. Caught Cheating
  5. 输出每个命中资源的 package / type / group / instance + XML 上下文 ±200 字符

不扫 ts4script, 不 dump 全量, 只写 output/story_animation_xml_trace.txt (并同步 stdout)。

XML 资源类型:
  WW_ANIM_XML  = 0x7DF2169C   (动画注册)
  tuning       = 0x545AC2C2
  xml          = 0x0333406C
  (其它类型若带 .xml 语义也扫, 标注类型)

fail-closed / 只读: 源缺->2; 无任何 XML 资源->3; 无 xdis 无关(不依赖); 正常 0。
ZERO_WRITE_TO_MODS=YES。

用法 (Windows):
  python scripts\story_animation_xml_trace.py "WW_Nevely42_Animations.package"
      [--ordinals 299-306] [--ctx 200] [--out output/story_animation_xml_trace.txt]
"""
import argparse
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

WW_ANIM_XML = 0x7DF2169C
TUNING = 0x545AC2C2
XML_GENERIC = 0x0333406C
STBL = 0x220557DA

XML_TYPES = {
    WW_ANIM_XML: "WW_ANIM_XML",
    TUNING: "tuning",
    XML_GENERIC: "xml",
}
ANIM_ID_FIELD = "animation_id"
DISPLAY_FIELD = "animation_raw_display_name"
STORY_TEXTS = ("caught cheating", "抓奸")


def _fmt_tgi(e):
    t = getattr(e, "type_id", 0)
    g = getattr(e, "group_id", 0)
    i = getattr(e, "instance_id", None)
    inst = f"0x{i:016X}" if isinstance(i, int) else str(i)
    return f"type=0x{t:08X} group=0x{g:08X} instance={inst}"


def parse_ordinals(spec):
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def parse_anim_id(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(raw, 16) if raw.lower().startswith("0x") else int(raw, 10)
    except ValueError:
        return None


def entry_ordinal(name):
    """从条目名 anmNNN 提取 ordinal NNN."""
    m = re.match(r"anm(\d+)", (name or ""))
    return int(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pkg")
    ap.add_argument("--ordinals", default="299-306")
    ap.add_argument("--ctx", type=int, default=200)
    ap.add_argument("--out", default="output/story_animation_xml_trace.txt")
    a = ap.parse_args()

    src = Path(a.pkg)
    if not src.is_file():
        print(f"ERROR: 源不存在 {src} (exit 2)", file=sys.stderr)
        return 2
    ords = parse_ordinals(a.ordinals)
    want = set(ords)
    ctx = a.ctx

    idx, err = wb.safe_parse(src)
    if err is not None:
        print(f"ERROR: 解析失败 {src}: {err} (exit 3)", file=sys.stderr)
        return 3

    xml_entries = [e for e in idx.entries
                   if getattr(e, "type_id", 0) in XML_TYPES]

    # --- ordf 需要读的 ordinal: 也把 ordinal-1..ordinal+2 作为"附近数据" ---
    near = set()
    for o in ords:
        for d in range(-2, 3):
            if o + d >= 0:
                near.add(o + d)

    out = []
    out.append("=== STORY ANIMATION XML TRACE (只读) ===")
    out.append(f"源      : {src.name}")
    out.append(f"扫描类型 : " + ", ".join(f"0x{t:08X}({name})" for t, name in XML_TYPES.items()))
    out.append(f"XML 资源数: {len(xml_entries)}")
    out.append(f"目标 ordinal: {ords} (±2 附近数据)")
    out.append("")

    n_res = 0
    n_ord = 0
    n_disp = 0
    n_caught = 0

    for e in xml_entries:
        type_id = getattr(e, "type_id", 0)
        tname = XML_TYPES.get(type_id, f"0x{type_id:08X}")
        try:
            body = wb.read_body_raw(src, e)
        except Exception as ex:
            out.append(f"[skip] {_fmt_tgi(e)} read_body_raw -> {ex}")
            continue
        try:
            body = wb.decompress_maybe(body)
        except Exception:
            pass
        try:
            text = body.decode("utf-8", errors="replace")
        except Exception:
            text = None

        if text is None:
            continue

        # 3) animation_raw_display_name / 4) Caught Cheating 命中
        disp_hits = [m.start() for m in re.finditer(re.escape(DISPLAY_FIELD), text)]
        caught_hits = []
        for m in re.finditer(r"([Cc]aught\s+[Cc]heating|抓奸)", text):
            caught_hits.append(m.start())

        n_res += 1
        header = (f"### resource: package={src.name} "
                  f"{_fmt_tgi(e)}  [{tname}]")
        wrote_header = False

        def emit_header():
            nonlocal wrote_header
            if not wrote_header:
                out.append(header)
                out.append("")
                wrote_header = True

        # 1)+2) animation_id 条目 + ordinal 附近数据
        #     按条目 <U n="anmN"> 切块更精确: 只扫目标/附近 ordinal 或含 Caught Cheating 的条目
        for em in re.finditer(r"<U\b([^>]*\bn=\"anm\d+\"[^>]*)>(.*?)</U>", text, re.S):
            attr, inner = em.group(1), em.group(2)
            ordn = entry_ordinal(_m.group(1) if (_m := re.search(r'n="(anm\d+)"', attr)) else None)
            # 该条目是否含 animation_id
            idm = re.search(r'<([A-Z])\s+n="%s"\s*>([^<]*)</\1>' % ANIM_ID_FIELD, inner)
            aid = parse_anim_id(idm.group(2)) if idm else None
            has_disp = DISPLAY_FIELD in inner
            has_caught = any(k in inner.lower() for k in ("caught cheating", "抓奸"))
            # 只输出相关条目: 目标/附近 ordinal 或 含 Caught Cheating 或 带 animation_id 且为目标附近
            if ordn is None:
                continue
            relevant = (ordn in want) or (ordn in near) or has_caught
            if not relevant:
                continue
            if ordn in want and aid is not None:
                n_ord += 1
            if has_disp:
                n_disp += 1
            if has_caught:
                n_caught += 1
            emit_header()
            label = f"ordinal {ordn}" if ordn is not None else "?"
            out.append(f"  [{label}] animation_id={aid}  display_name={has_disp}  "
                       f"CaughtCheating={has_caught}")
            out.append(f"  -- XML 上下文 ±{ctx} --")
            lo = max(0, em.start() - ctx)
            hi = min(len(text), em.end() + ctx)
            out.append(text[lo:hi])
            out.append("")
            out.append("-" * 72)
            out.append("")

        # 3) 单独的 display_name 命中 (不在 anm 条目内, 兜底)
        for hp in disp_hits:
            emit_header()
            out.append(f"  [display_name] 命中 @{hp}  ±{ctx}")
            lo = max(0, hp - ctx)
            hi = min(len(text), hp + len(DISPLAY_FIELD) + ctx)
            out.append(text[lo:hi])
            out.append("-" * 72)
            out.append("")
        # 4) 单独的 Caught Cheating (兜底)
        for cp in caught_hits:
            if not any(k in text[max(0, cp - 300): cp + 300].lower()
                       for k in ("<u", "<t", "<i", "anm")):
                emit_header()
                out.append(f"  [CaughtCheating] 命中 @{cp}  ±{ctx}")
                lo = max(0, cp - ctx)
                hi = min(len(text), cp + 40 + ctx)
                out.append(text[lo:hi])
                out.append("-" * 72)
                out.append("")

    if n_res == 0:
        out.append("!! 未找到任何 XML 资源 (type 0x7DF2169C / 0x545AC2C2 / 0x0333406C)。")
        out.append("   若源包用其它类型承载动画定义, 见 package 头部列出实际类型。")
        outcome = 3
    else:
        outcome = 0

    out.append("---")
    out.append(f"XML 资源处理  = {n_res}")
    out.append(f"目标 ordinal 条目命中（含 animation_id）= {n_ord}")
    out.append(f"含 animation_raw_display_name 条目 = {n_disp}")
    out.append(f"含 'Caught Cheating/抓奸' 文本条目 = {n_caught}")
    if n_ord == 0 and n_caught == 0:
        out.append("")
        out.append("!! 目标 ordinal 或 Caught Cheating 未在 XML 中出现。")
        out.append("   说明显示文本不来自本 XML (可能在纯 STBL / 其它包 / 运行期 L18n)。")
    out.append("")
    out.append("ZERO_WRITE_TO_MODS=YES (只读)")

    text_out = "\n".join(out)
    out_path = Path(a.out)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text_out, encoding="utf-8")
        print(f"trace 已写入: {out_path}")
    except Exception as ex:
        print(f"WARN: 写文件失败: {ex}", file=sys.stderr)
    print(text_out)
    return outcome


if __name__ == "__main__":
    sys.exit(main())
