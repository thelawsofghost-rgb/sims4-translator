#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEST C 取证模块 (FULL byte-level patch-back proof)。

在【真实 Windows】上对:
  - 真实 source WW_Nevely42_Animations.package
  - 真实 TEST C sidecar (WW_Nevely42_Animations_SIDECAR_TESTC.package)
做完整 XML 取证, 判定 479-tail-diff 是 WRITER ARTIFACT 还是 VALIDATOR ARTIFACT。

本模块【只读 & 独立**, 不改 writer、不重新生成 artifact**, 只对已生成的两个
.package 做 proof-of-byte-equality。

方法 (section A/C):
  1) decompress 两边 WW XML -> logical bytes (utf-8)。
  2) 只针对三个已批准 target entry (ordinal 0 / 239 / 478):
     在 sidecar logical XML 内, 把
        【大包C1】OLD_1 / 【大包C2】OLD_2 / 【大包C3】OLD_3
     精确恢复成各自原始 OLD。
     必须用 entry-scoped exact selector (同一 U block 内的 <T n=...> 节点),
     禁止全局 str.replace。
  3) 得 SIDECAR_PATCHED_BACK_LOGICAL_BYTES, 与 SOURCE_LOGICAL_BYTES byte-for-byte 比较。

  - 若 EQUAL=YES  -> WRITER_XML_COLLATERAL_CHANGE=NO, VALIDATOR_TAIL_FALSE_POSITIVE=YES
  - 若 EQUAL=NO   -> WRITER_XML_COLLATERAL_CHANGE=YES, VALIDATOR_TAIL_FALSE_POSITIVE=NO,
                     并输出残余差异 region 分类 (offset/preview/classification)。

输出全部 E 键 (TEST_C_XML_FORENSIC block)。

安全: 只读; 不写 Mods / 不真机 / 不 translation / 不 production / 不碰 writer。
ZERO_WRITE_TO_MODS=YES。
"""

import argparse
import hashlib
import importlib.util
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MODULE = SCRIPT_DIR / "ww_animation_canary_builder.py"
_spec = importlib.util.spec_from_file_location("ww_canary_builder", MODULE)
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)

WW_ANIM_XML = wb.WW_ANIM_XML
DISP_FIELD = "animation_raw_display_name"
TARGET_ORDS = (0, 239, 478)
PREFIXES = {0: "大包C1", 239: "大包C2", 478: "大包C3"}


def _locate_animations_list(xml_text: str):
    s = re.search(r'<L\s+[^>]*\bn\s*=\s*"animations_list"[^>]*>', xml_text, re.S)
    if not s:
        return None, -1, -1
    open_end = s.end()
    depth = 1
    tag_re = re.compile(r"<(/?)([A-Za-z_][^\s/>]*)([^>]*?)(/?)>", re.S)
    for m in tag_re.finditer(xml_text, open_end):
        closing, name, _a, selfclose = m.group(1), m.group(2), m.group(3), m.group(4)
        if name == "L":
            if closing == "/" and not selfclose:
                depth -= 1
                if depth == 0:
                    return xml_text[open_end:m.start()], open_end, m.start()
            elif not closing and not selfclose:
                depth += 1
    return None, -1, -1


def _entry_blocks(list_text: str):
    blocks = []
    depth = 0
    start = None
    tag_re = re.compile(r"<(/?)([A-Za-z_][^\s/>]*)([^>]*?)(/?)>", re.S)
    for m in tag_re.finditer(list_text):
        closing, name, _a, selfclose = m.group(1), m.group(2), m.group(3), m.group(4)
        if name in ("?xml", "!DOCTYPE"):
            continue
        is_closing = closing == "/"
        is_selfclose = selfclose == "/"
        if is_closing and name == "U":
            if depth == 1 and start is not None:
                blocks.append((list_text[start:m.end()], (start, m.end())))
                start = None
            depth -= 1
            if depth < 0:
                depth = 0
        elif (not is_closing) and (not is_selfclose) and name == "U":
            if depth == 0:
                start = m.start()
            depth += 1
        elif (not is_closing) and (not is_selfclose):
            depth += 1
        elif is_closing and not is_selfclose:
            depth -= 1
            if depth < 0:
                depth = 0
    return blocks


def _entry_partition(list_text: str):
    blocks = _entry_blocks(list_text)
    parts = []
    pos = 0
    for btext, (bst, be) in blocks:
        if bst > pos:
            parts.append((False, list_text[pos:bst], (pos, bst)))
        parts.append((True, btext, (bst, be)))
        pos = be
    if pos < len(list_text):
        parts.append((False, list_text[pos:], (pos, len(list_text))))
    return parts


def _display_old_in_block(block_text: str):
    """返回该 entry block 内 display 的 (old_text, new_text_with_prefix)。节点数 !=1 返回 None。"""
    pat = re.compile(
        r'(<T\s+[^>]*\bn\s*=\s*"%s"[^>]*>)([^<]*)(</T>)' % re.escape(DISP_FIELD), re.S
    )
    ms = list(pat.finditer(block_text))
    if len(ms) != 1:
        return None
    return ms[0], ms[0].group(2)


def _replace_display(block_text: str, new_val: str):
    pat = re.compile(
        r'(<T\s+[^>]*\bn\s*=\s*"%s"[^>]*>)([^<]*)(</T>)' % re.escape(DISP_FIELD), re.S
    )
    ms = list(pat.finditer(block_text))
    if len(ms) != 1:
        return block_text, False
    m = ms[0]
    return block_text[:m.start(2)] + new_val + block_text[m.end(2):], True


def _display_values(text):
    pat = re.compile(r'<T\s+[^>]*\bn\s*=\s*"%s"[^>]*>([^<]*)</T>' % re.escape(DISP_FIELD), re.S)
    return [m.group(1) for m in pat.finditer(text)]


def _logical(pkg: Path):
    idx, err = wb.safe_parse(pkg)
    if err is not None or idx is None:
        raise RuntimeError(f"parse fail: {err}")
    ww = [e for e in idx.entries if e.type_id == WW_ANIM_XML]
    if len(ww) != 1:
        raise RuntimeError(f"WW XML count={len(ww)} (须 1)")
    body = wb.read_body_raw(pkg, ww[0])
    raw = wb.decompress_maybe(body)
    return raw.decode("utf-8"), ww[0]


def patch_back(side_text: str, src_text: str) -> tuple:
    """把 sidecar 三个 target entry 的 display 前缀去掉, 恢复成 src 对应 entry 的 OLD。

    返回 (patched_bytes, per-entry注入详情)。一切 entry-scoped, 禁全局 replace。
    从 src 真实读取每个 target entry 的 display OLD (不硬编码)。
    """
    _, sio, sie = _locate_animations_list(src_text)
    if sio < 0:
        raise RuntimeError("source 无 animations_list")
    src_blocks = _entry_blocks(src_text[sio:sie])
    if len(src_blocks) != 479:
        raise RuntimeError(f"source entry 数={len(src_blocks)} (须 479)")

    _, cio, cie = _locate_animations_list(side_text)
    if cio < 0:
        raise RuntimeError("sidecar 无 animations_list")
    side_blocks = _entry_blocks(side_text[cio:cie])
    if len(side_blocks) != 479:
        raise RuntimeError(f"sidecar entry 数={len(side_blocks)} (须 479)")

    # 从 source 读每个 target entry 的 OLD
    src_old = {}
    for ord0 in TARGET_ORDS:
        sb, (_s, _e) = src_blocks[ord0]
        r = _display_old_in_block(sb)
        if r is None:
            raise RuntimeError(f"source entry[{ord0}] display node 数 !=1")
        _m, old = r
        src_old[ord0] = old

    # 在 sidecar 里, 对每个 target entry 精确替换回 OLD
    side_parts = _entry_partition(side_text[cio:cie])
    out_parts = []
    entry_idx = 0
    inject = {}
    for is_entry, ptext, (_ps, _pe) in side_parts:
        if is_entry:
            if entry_idx in TARGET_ORDS:
                newb, ok = _replace_display(ptext, src_old[entry_idx])
                if not ok:
                    raise RuntimeError(f"sidecar entry[{entry_idx}] display node 数 !=1")
                out_parts.append(newb)
                inject[entry_idx] = True
            else:
                out_parts.append(ptext)
            entry_idx += 1
        else:
            out_parts.append(ptext)
    new_inner = "".join(out_parts)
    patched = side_text[:cio] + new_inner + side_text[cie:]
    return patched.encode("utf-8"), inject, src_old


def diff_regions(a: bytes, b: bytes, limit=20):
    """字节差异 region 列表 (首个不同字节到首个相同处)。返回 (count, regions)。"""
    n = min(len(a), len(b))
    regions = []
    i = 0
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            # 向后扩展到同区域连续差异 (含可能交错) 简化为 [i,j)
            regions.append((i, j))
            if len(regions) >= limit:
                break
            i = j
        else:
            i += 1
    # 尾部长度不同
    if len(a) != len(b):
        tl = min(len(a), len(b))
        if len(a) > tl:
            regions.append((tl, len(a)))
        elif len(b) > tl:
            regions.append((tl, len(b)))
    return len(regions), regions[:limit]


def classify(a: bytes, b: bytes, i: int, j: int):
    sa = a[i:j]
    sb = b[i:j]
    if sa.strip() == b"" and sb.strip() == b"" and sa != sb:
        return "WHITESPACE_ONLY"
    # 若 j-i 相同且内容格式化成一行
    try:
        if sa.replace(b" ", b"").replace(b"\n", b"").replace(b"\t", b"").replace(b"\r", b"") == \
           sb.replace(b" ", b"").replace(b"\n", b"").replace(b"\t", b"").replace(b"\r", b""):
            return "WHITESPACE_NORMALIZATION"
    except Exception:
        pass
    return "CONTENT_CHANGE"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="真实 source .package")
    ap.add_argument("--sidecar", required=True, help="真实 TEST C sidecar .package ")
    a = ap.parse_args()
    src = Path(a.source)
    side = Path(a.sidecar)
    if not src.is_file() or not side.is_file():
        print("ERROR: source/sidecar 不存在", file=sys.stderr)
        return 2

    src_text, se = _logical(src)
    side_text, _ce = _logical(side)
    src_bytes = src_text.encode("utf-8")
    side_bytes = side_text.encode("utf-8")

    patched, inject, src_old = patch_back(side_text, src_text)
    patched_sha = hashlib.sha256(patched).hexdigest()
    src_sha = hashlib.sha256(src_bytes).hexdigest()
    side_sha = hashlib.sha256(side_bytes).hexdigest()
    equal = patched == src_bytes

    resid_byte_count = 0
    resid_region_count = 0
    if not equal:
        reg_count, regs = diff_regions(patched, src_bytes)
        resid_region_count = reg_count
        resid_byte_count = sum(
            1 for k in range(max(len(patched), len(src_bytes)))
            if k >= len(patched) or k >= len(src_bytes) or patched[k] != src_bytes[k]
        )

    print("TEST_C_XML_FORENSIC:")
    print(f"  SOURCE_WW_XML_TGI=0x{se.type_id:08X}/0x{se.group_id:08X}/0x{se.instance_id:016X}")
    print(f"  SOURCE_ENCODING=utf-8")
    print(f"  SOURCE_LOGICAL_SIZE={len(src_bytes)}")
    print(f"  SIDECAR_LOGICAL_SIZE={len(side_bytes)}")
    print(f"  SIDECAR_PATCHED_BACK_SIZE={len(patched)}")
    print(f"  SOURCE_LOGICAL_SHA256={src_sha}")
    print(f"  SIDECAR_LOGICAL_SHA256={side_sha}")
    print(f"  SIDECAR_PATCHED_BACK_SHA256={patched_sha}")
    n_inj = sum(1 for v in inject.values() if v)
    print(f"  PATCHED_BACK_TARGET_ENTRIES={n_inj}")
    for ord0 in TARGET_ORDS:
        print(f"  TARGET_ORDINAL={ord0} OLD={src_old[ord0]!r}")
    print(f"  PATCHED_BACK_BYTES_EQUAL_SOURCE={'YES' if equal else 'NO'}")
    if equal:
        print(f"  WRITER_XML_COLLATERAL_CHANGE=NO")
        print(f"  VALIDATOR_TAIL_FALSE_POSITIVE=YES")
    else:
        print(f"  WRITER_XML_COLLATERAL_CHANGE=YES")
        print(f"  VALIDATOR_TAIL_FALSE_POSITIVE=NO")
        print(f"  RESIDUAL_DIFF_BYTE_COUNT={resid_byte_count}")
        print(f"  RESIDUAL_DIFF_REGION_COUNT={resid_region_count}")
        _rc, regs = diff_regions(patched, src_bytes)
        for k, (i, j) in enumerate(regs[:20], 1):
            print(f"  REGION_{k}:")
            print(f"    offset_source={i} offset_sidecar_patched={i} span={j - i}")
            print(f"    source_preview={src_bytes[i:j]!r}")
            print(f"    sidecar_preview={patched[i:j]!r}")
            print(f"    classification={classify(src_bytes, patched, i, j)}")
    print(f"  ZERO_WRITE_TO_MODS=YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
