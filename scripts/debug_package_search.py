#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
debug_package_search.py —— 只读在 Sims 4 .package 内搜索关键词

读取 package, 遍历其中 XML / TEXT 资源, 对每条资源文本搜索关键词, 输出:
  resource type / group / instance + 原始文本命中上下文。

关键词 (大小写不敏感):
  animation_display_name
  Caught Cheating
  story

只读: 不修改 package。ZERO_WRITE_TO_MODS=YES。

用法:
  python scripts/debug_package_search.py <file.package> [--all] [--kw 额外关键词]...
  --all: 打印每个资源的全部文本 (默认只打印命中上下文 ±N char)
"""
import argparse
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "ww_canary_builder", SCRIPT_DIR / "ww_animation_canary_builder.py")
    wb = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(wb)
except Exception as ex:
    print(f"ERROR: 无法加载 ww_animation_canary_builder: {ex}")
    sys.exit(7)

KEYWORDS = ("animation_display_name", "Caught Cheating", "story")
XML_TYPES = (0x7DF2169C, 0x545AC2C2, 0x0333406C)   # WW_ANIM_XML / tuning / xml
TEXT_TYPES = (0x220557DA, 0x220557D4)              # STBL / text list
CONTEXT = 80


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else str(tag)


def scan_text(text, kws, context_lines):
    out = []
    for kw in kws:
        low = text.lower()
        k = kw.lower()
        start = 0
        while True:
            i = low.find(k, start)
            if i < 0:
                break
            s = max(0, i - context_lines)
            e = min(len(text), i + len(k) + context_lines)
            out.append((kw, text[s:e]))
            start = i + len(k)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("package")
    ap.add_argument("--all", action="store_true", help="打印每个资源全部文本")
    ap.add_argument("--kw", action="append", default=[], help="额外关键词")
    ap.add_argument("--ctx", type=int, default=CONTEXT, help="上下文字符数")
    a = ap.parse_args()

    pkg = Path(a.package)
    if not pkg.is_file():
        print(f"ERROR: 文件不存在 {pkg}"); return 2

    kws = list(KEYWORDS) + a.kw
    ctx = a.ctx

    idx, err = wb.safe_parse(pkg)
    if err is not None or idx is None:
        print(f"ERROR: 解析失败: {err}"); return 3

    print("=== PACKAGE SEARCH ===")
    print(f"文件: {pkg.name}")
    print(f"关键词: {', '.join(kws)}  (大小写不敏感)")
    print(f"资源总数: {len(idx.entries)}")
    print("")

    # 按类型分类 (type 可能是 int 或已格式化字符串)
    def _typ(e):
        return e.type_id if isinstance(e.type_id, int) else int(str(e.type_id), 16)

    def _grp(e):
        return e.group_id if isinstance(e.group_id, int) else int(str(e.group_id), 16)

    xml_entries = [e for e in idx.entries if _typ(e) in XML_TYPES]
    txt_entries = [e for e in idx.entries if _typ(e) in TEXT_TYPES]
    known = set(id(e) for e in xml_entries + txt_entries)
    others = [e for e in idx.entries if id(e) not in known]

    scanned = 0
    hits = 0
    for kind, entries in (("XML", xml_entries), ("TEXT", txt_entries), ("OTHER", others)):
        for e in entries:
            scanned += 1
            body = wb.read_body_raw(pkg, e)
            if body is None:
                continue
            if hasattr(wb, "decompress_maybe"):
                body = wb.decompress_maybe(body)
            try:
                text = body.decode("utf-8", errors="replace")
            except Exception:
                text = repr(body)
            if kind != "OTHER":
                found = scan_text(text, kws, ctx)
                if not found and not a.all:
                    continue
                hits += len(found)
                print(f"### [{kind}] type=0x{_typ(e):08X} group=0x{_grp(e):08X} instance=0x{e.instance_id:016X}")
                if a.all:
                    print("  [全文]")
                    print("  " + text.replace(chr(10), chr(10) + "  ")[:2000])
                for kw, ctx_text in found:
                    print(f"  <<< 命中 '{kw}':")
                    print("      ..." + ctx_text.replace(chr(10), " ") + "...")
                print("")
            else:
                # OTHER: 仅当含关键词才打印
                found = scan_text(text, kws, ctx)
                if not found:
                    continue
                hits += len(found)
                print(f"### [OTHER] type=0x{_typ(e):08X} group=0x{_grp(e):08X} instance=0x{e.instance_id:016X}")
                for kw, ctx_text in found:
                    print(f"  <<< 命中 '{kw}':")
                    print("      ..." + ctx_text.replace(chr(10), " ") + "...")
                print("")

    print(f"扫描资源数 = {scanned}   命中关键词次数 = {hits}")
    if hits == 0:
        print("")
        print("  无命中。若期望关键词存在, 可能:")
        print("  - 关键词以二进制/压缩形式存储 (STBL 是二进制编码, 需解码为可读文本)")
        print("  - 在其它 package / .ts4script / runtime 里 (非本包)")
        print("  => STBL 文本请用 dump_stbl / debug_stbl_reverse_hash 解码。")
    print("")
    print("ZERO_WRITE_TO_MODS=YES (只读)")
    return 0 if hits else 4


if __name__ == "__main__":
    sys.exit(main())
