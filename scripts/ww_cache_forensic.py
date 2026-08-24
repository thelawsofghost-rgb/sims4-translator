#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEST C READ-ONLY WW CACHE FORENSIC ANALYZER (只读, 不修改任何文件)。

目标: 分析 WickedWhims 导出缓存文件 (如 animations_data_cache.ww),
回答 Dorothy 四问:
  1. 文件格式识别  (header / magic / 是否压缩或序列化)
  2. 搜索字符串:    "You Belong To Me 1" / "Go To Sleep TWO SIMS 1"
  3. 若存在:       输出附近结构 (offset / key / value / 是否关联 TGI/instance/animation ID)
  4. 搜索字段名:    animation_raw_display_name / animation_stage_name / display /
                    name / title / string / loc

设计原则:
  - 完全只读: 只 open('rb').read() 输入文件, 绝不写任何文件。
  - 格式无关探测: 先探测 magic/压缩/序列化, 再按探测结果解码。
  - 安全反序列化: 对 pickle 只用受限 unpickler (find_class 一律拒绝 → 绝不执行任意代码,
    只重建内置 dict/list/tuple/str/int/float/bool/None)。
  - 多编码/多容器扫描: 原始字节 + zlib 解压 + gzip 解压 + JSON 解析 + 受限 pickle 重建,
    各层都做字符串定位 (UTF-8 / UTF-16LE)。
  - 输出全部命中及其周围上下文 + 可能的 TGI/instance/int 关联, 不猜测语义。

用法:
  python scripts/ww_cache_forensic.py --file "Desktop/animations_data_cache.ww"
  python scripts/ww_cache_forensic.py --file X.ww --search "You Belong To Me 1" --search "Go To Sleep TWO SIMS 1"
  python scripts/ww_cache_forensic.py --file X.ww --field-census  (字段名搜索见内置需求 #4)

ZERO_WRITE_TO_MODS=YES
"""

import argparse
import gzip
import io
import json
import pickle
import re
import struct
import sys
import zlib
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. 文件格式识别
# ---------------------------------------------------------------------------
_KNOWN_MAGICS = {
    b"\x80": "python pickle (PICKLE, 可能多协议; 低字节决定协议版本)",
    b"\x1f\x8b": "gzip (GZIP)",
    b"\x78\x9c": "zlib (ZLIB, deflate)",
    b"\x78\x01": "zlib (ZLIB, no/low compression)",
    b"\x78\xda": "zlib (ZLIB, best compression)",
    b"{": "JSON object开头",
    b"[": "JSON array开头",
    b"\x89PNG": "PNG image (非动画缓存, 误选?)",
    b"MZ": "PE/EXE (非数据)",
}


def identify_header(data: bytes) -> dict:
    head = data[:32]
    res = {
        "size": len(data),
        "head_hex": head.hex(),
        "head_ascii": "".join(chr(b) if 32 <= b < 127 else "." for b in head),
        "magic_hits": [],
    }
    for magic, desc in _KNOWN_MAGICS.items():
        if data.startswith(magic):
            res["magic_hits"].append(f"{magic!r} = {desc}")
    # compression euristics
    res["is_zlib"] = _is_zlib(data)
    res["is_gzip"] = data[:2] == b"\x1f\x8b"
    # pickle protocol sniff (protocol in byte1 for \x80 + proto)
    if data[:1] == b"\x80" and len(data) >= 2:
        res["pickle_proto"] = data[1]
    return res


def _is_zlib(b: bytes) -> bool:
    return len(b) >= 2 and b[0] == 0x78 and b[1] in (0x01, 0x5E, 0x9C, 0xDA)


def try_decompress(data: bytes):
    """尝试 zlib / gzip 解压。返回 (label, payload) 或 (label, None)。"""
    if _is_zlib(data):
        try:
            return "zlib", zlib.decompress(data)
        except Exception:
            pass
    if data[:2] == b"\x1f\x8b":
        try:
            return "gzip", gzip.decompress(data)
        except Exception:
            pass
    return None, None


# ---------------------------------------------------------------------------
# 安全反序列化 (绝不执行任意代码)
# ---------------------------------------------------------------------------
class _SafeUnpickler(pickle.Unpickler):
    """受限 unpickler: 只允许内置容器/标量, 拒绝任何 class/import (防任意代码执行)。"""

    def find_class(self, module, name):
        raise pickle.UnpicklingError(f"blocked import {module}.{name}")


class _RestrictedBuilder:
    """从受限 unpickler 得到的张量中递归收集 dict/list/标量, 供字符串扫描。"""

    def __init__(self):
        self.containers = []

    def walk(self, obj):
        self.containers.append(obj)


def safe_unpickle(data: bytes):
    """受限反序列化。返回 (obj, err)。仅重建内置容器/标量。"""
    try:
        u = _SafeUnpickler(io.BytesIO(data))
        # 部分文件是裸 pickle, 也可能是多层嵌套 dump; 保护超长/异常
        u.load()  # noqa: B301 已受限
        return u, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# 字符串/字段扫描
# ---------------------------------------------------------------------------
def byte_find_all(hay: bytes, needle: bytes):
    """返回 needle 在 hay 中的所有 byte offset (重叠不计, 逐个平移)。"""
    out = []
    start = 0
    while True:
        i = hay.find(needle, start)
        if i < 0:
            break
        out.append(i)
        start = i + 1
    return out


def scan_bytes(data: bytes, searches):
    """在给定字节串中, 对每个搜索词(及 UTF-16LE 变体)找全部 offset + 上下文。"""
    hits = []
    for term in searches:
        tb = term.encode("utf-8")
        t16 = term.encode("utf-16-le")
        for off in byte_find_all(data, tb):
            hits.append((term, "utf-8", off))
        for off in byte_find_all(data, t16):
            hits.append((term, "utf-16le", off))
    # 上下文: offset-60 .. offset+120 (ASCII 可视化)
    ctx = {}
    for term, enc, off in hits:
        s = max(0, off - 60)
        e = min(len(data), off + len(term.encode("utf-8" if enc == "utf-8" else "utf-16-le")) + 90)
        ctx[(term, enc, off)] = data[s:e]
    return hits, ctx


def _ascii_ctx(b: bytes) -> str:
    s = "".join(chr(x) if 32 <= x < 127 else "." for x in b)
    return s


def scan_json(obj, searches, path="$"):
    """递归 JSON, 收集含搜索词的字符串节点及其 key/path。返回 list[dict]。"""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                for t in searches:
                    if t in v:
                        found.append({"path": f"{path}.{k}", "key": k, "value": v})
            else:
                found.extend(scan_json(v, searches, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                for t in searches:
                    if t in v:
                        found.append({"path": f"{path}[{i}]", "key": i, "value": v})
            else:
                found.extend(scan_json(v, searches, f"{path}[{i}]"))
    return found


def scan_container(obj, searches, path="$", key=None, depth=0):
    """通用递归: dict/list/tuple/str 容器, 找含搜索词的字符串, 输出 path/key/value。"""
    found = []
    if depth > 64:
        return found
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                for t in searches:
                    if t in v:
                        found.append({"path": f"{path}.{k}", "key": k, "value": v})
            else:
                found.extend(scan_container(v, searches, f"{path}.{k}", key=k, depth=depth + 1))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                for t in searches:
                    if t in v:
                        found.append({"path": f"{path}[{i}]", "key": i, "value": v})
            else:
                found.extend(scan_container(v, searches, f"{path}[{i}]", depth=depth + 1))
    return found


def ints_near(data: bytes, off: int, radius: int = 48):
    """在 off 附近找可能的 4/8 字节整数 (TGI/instance/id 候选)。只读启发。"""
    out = []
    s = max(0, off - radius)
    e = min(len(data), off + radius)
    window = data[s:e]
    for i in range(0, len(window) - 3, 1):
        u32 = struct.unpack_from("<I", window, i)[0]
        if u32 > 1000 and u32 != 0xFFFFFFFF:
            out.append(("u32_le", s + i, u32))
    return out[:8]


def hexdump_line(data: bytes, base: int, off: int, width: int = 16):
    """打印从 off 开始 width 字节的 hexdump 行 (含绝对偏移 + ascii)。返回下一个未打印偏移。"""
    chunk = data[off:off + width]
    hexs = " ".join(f"{b:02x}" for b in chunk)
    asci = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
    print(f"    {base:>8}  {hexs:<47}  |{asci}|")
    return off + width


def _is_plausible_oid(v: int) -> str:
    """启发式标注整数值大概是什么类型 (非结论)。"""
    if v == 0:
        return "zero/null"
    if 1 <= v <= 100000:
        return "small (index/count?)"
    if v & 0xFFFFFFFF00000000 == 0:
        return "u32-like"
    if v >> 40 == 0 and v >= (1 << 32):
        return "mid u64 (instance?)"
    if v >> 56 == 0:
        return "48-bit instance/TGI?"
    return "large u64 (hash/random?)"


def binary_dump_around(data: bytes, off: int, term: str, radius: int = 64):
    """输出搜索词命中位置的二进制结构: 对齐 hexdump + u32/u64 注解。只读。"""
    s = max(0, off - radius)
    e = min(len(data), off + len(term.encode("utf-8")) + radius)
    print(f"    BINARY_STRUCTURE @offset={off} (围绕 {term!r}, ±{radius}):")
    # 字符串终止方式探测
    pre = data[max(0, off - 8):off]
    if len(pre) >= 4 and pre[-4:-1] == b"\x00\x00\x00":
        print(f"      string_kind: 长度前缀候选 (前 4 字节小端长度={struct.unpack_from('<I', data, off-4)[0] if off>=4 else '?'})")
    elif off > 0 and data[off - 1] == 0:
        print("      string_kind: 空字符串(NUL)列表候选 (前一字节为 NUL)")
    # 前导字段 (off 之前 width 网格上的整数)
    print(f"      -- 前方 48 字节整数注解 (网格对齐, 每 4B 读 u32/u64) --")
    g = max(0, off - 48)
    for i in range(g, off, 4):
        if i + 4 > len(data):
            break
        u32 = struct.unpack_from("<I", data, i)[0]
        u64 = struct.unpack_from("<Q", data, i)[0] if i + 8 <= len(data) else None
        marks = []
        if u64 is not None and u64 != u32:
            marks.append(f"u64={u64} ({_is_plausible_oid(u64)})")
        if u64 is None or u64 == u32:
            marks.append(f"u32={u32} ({_is_plausible_oid(u32)})")
        if u32 == 0 and (i + 8 <= len(data)) and struct.unpack_from("<Q", data, i)[0] != 0:
            continue
        print(f"      +{i - off:>4}  u32_le[{i:>8}]={u32:>12}  " + "  ".join(marks))
    print(f"      -- hexdump --")
    cur = s
    while cur < e:
        cur = hexdump_line(data, cur, cur)
    # 后随字段 (字符串之后)
    p = off + len(term.encode("utf-8"))
    print(f"      -- 字符串之后 32 字节整数注解 --")
    for i in range(p, min(p + 32, len(data) - 3), 4):
        u32 = struct.unpack_from("<I", data, i)[0]
        u64 = struct.unpack_from("<Q", data, i)[0] if i + 8 <= len(data) else None
        marks = []
        if u64 is not None and u64 != u32:
            marks.append(f"u64={u64} ({_is_plausible_oid(u64)})")
        if u64 is None or u64 == u32:
            marks.append(f"u32={u32} ({_is_plausible_oid(u32)})")
        if marks:
            print(f"      +{i - p:>4}  u32_le[{i:>8}]={u32:>12}  " + "  ".join(marks))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="WW 缓存文件路径 (只读)")
    ap.add_argument("--search", action="append", default=[],
                    help="要搜索的字符串 (可多次; 默认: You Belong To Me 1 / Go To Sleep TWO SIMS 1)")
    ap.add_argument("--field-census", action="store_true",
                    help="额外扫描字段名 (animation_raw_display_name/animation_stage_name/display/name/title/string/loc)")
    ap.add_argument("--binary-dump", action="store_true",
                    help="对每个字节层命中输出附近二进制结构 (hexdump + u32/u64 注解 + 字符串终止探测)")
    ap.add_argument("--anchor", default=None,
                    help="候选锚点字符串 (如 PosePack clip) —— 输出其它命中相对它的偏移/距离")
    ap.add_argument("--radius", type=int, default=64,
                    help="--binary-dump 围绕半径 (默认 64)")
    a = ap.parse_args()

    p = Path(a.file)
    if not p.is_file():
        print("ERROR: 文件不存在", file=sys.stderr)
        return 2
    data = p.read_bytes()

    searches = a.search or ["You Belong To Me 1", "Go To Sleep TWO SIMS 1"]
    # 若给了 --anchor 且没显式给 --search, 把 anchor 也纳入搜索以便定位
    if a.anchor and a.anchor not in searches:
        searches = list(searches) + [a.anchor]
    if a.field_census:
        searches += ["animation_raw_display_name", "animation_stage_name",
                     "display", "name", "title", "string", "loc"]

    print("=" * 70)
    print("1) 文件格式识别")
    print("=" * 70)
    hdr = identify_header(data)
    for k, v in hdr.items():
        print(f"  {k}: {v!r}")

    print()
    print("=" * 70)
    print("2) 内容/解压/序列化分层探测")
    print("=" * 70)
    layers = [("raw", data)]
    label, payload = try_decompress(data)
    if payload:
        layers.append((label, payload))
        print(f"  检测到 {label} 解压, 解压后 {len(payload)} 字节")
    else:
        print("  未检测到 zlib/gzip 整体压缩")

    # JSON?
    json_obj = None
    for lname, ld in layers:
        t = ld.lstrip()
        if t[:1] in (b"{", b"["):
            try:
                json_obj = json.loads(ld)
                print(f"  [{lname}] 可解析为 JSON (顶层类型={type(json_obj).__name__})")
                break
            except Exception as e:
                print(f"  [{lname}] JSON 解析失败: {e}")

    # pickle? (受限, 只重建内置容器/标量)
    pickle_obj = None
    if hdr.get("pickle_proto") is not None or data[:1] in (b"}", b")", b"\x80"):
        obj, err = safe_unpickle(data)
        if err is None:
            pickle_obj = obj
            print("  可安全(受限)反序列化为 pickle 对象")
        else:
            print(f"  受限 pickle 解包失败 (可能非纯 pickle 或包含自定义类): {err}")

    print()
    print("=" * 70)
    print("3) 字符串搜索命中")
    print("=" * 70)
    total = 0
    all_byte_offsets = {}  # term -> {enc: [offsets]}, 用于 anchor 关联
    for lname, ld in layers:
        hits, ctx = scan_bytes(ld, searches)
        if not hits:
            continue
        # 记录该层的 offset 供 anchor 关联 (仅 utf-8)
        acc = all_byte_offsets.setdefault(lname, {})
        for (_t, enc, _o) in hits:
            if enc == "utf-8":
                acc.setdefault(_t, []).append(_o)
        unique_terms = sorted(set(t for t, _e, _o in hits))
        print(f"  --- 层 [{lname}] 命中 ---")
        print(f"      命中 {len(hits)} 处; 含搜索词: {unique_terms}")
        for (term, enc, off) in sorted(hits, key=lambda x: x[2]):
            c = ctx[(term, enc, off)]
            print(f"    offset={off:>8}  enc={enc:8}  term={term!r}")
            print(f"      [.. {_ascii_ctx(c)} ..]")
            if a.binary_dump:
                if enc == "utf-8":
                    binary_dump_around(ld, off, term, radius=a.radius)
                else:
                    print("      (UTF-16LE 命中, 跳过二进制结构 dump)")
            total += 1
    if total == 0:
        print("  原始/解压字节层: 无命中")

    # JSON/pickle 结构化层
    for lname, obj in (("json", json_obj), ("pickle", pickle_obj)):
        if obj is None:
            continue
        f = scan_container(obj, searches)
        if not f:
            continue
        print(f"  --- 结构化层 [{lname}] 含搜索词的字符串节点 ---")
        for it in f:
            print(f"    path={it['path']}  key={it['key']!r}  value={it['value']!r}")
        # 附: 该层是否有 TGI/instance 类似长整数 (启发)
        longints = []

        def _li(o):
            if isinstance(o, int) and o > (1 << 32) and o < (1 << 64):
                longints.append(o)
            elif isinstance(o, dict):
                for _k, v in o.items():
                    _li(v)
            elif isinstance(o, (list, tuple)):
                for v in o:
                    _li(v)

        try:
            _li(obj)
        except RecursionError:
            pass
        if longints:
            print(f"      结构内 8 字节长整数候选 (instance-like): {sorted(set(longints))[:12]}")

    # 锚点关联: 其它命中相对 anchor 的距离 (同一字节层)
    if a.anchor:
        print()
        print("=" * 70)
        print(f"ANCHOR_RELATIVE: 相对锚点 {a.anchor!r} 的距离 (逐层, utf-8)")
        print("=" * 70)
        for lname, acc in all_byte_offsets.items():
            anch = acc.get(a.anchor, [])
            anchor_offset = anch[0] if anch else None
            print(f"  --- 层 [{lname}] ---")
            if anchor_offset is None:
                print(f"      锚点 {a.anchor!r} 未命中")
                continue
            print(f"      锚点首命中 offset={anchor_offset}")
            for t in sorted(acc, key=lambda x: acc[x][0]):
                if t == a.anchor:
                    continue
                for o in acc[t]:
                    print(f"      {t!r:45} offset={o:>8}  相对锚点={o - anchor_offset:>+9}")

    print()
    print("ZERO_WRITE_TO_MODS=YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
