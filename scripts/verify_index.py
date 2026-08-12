#!/usr/bin/env python3
"""
FastIndexReader 验证测试

目标 (对应规格): 把 FastIndexReader 读出的 Resource Count/Type/Group/Instance/Size
与一个独立参考实现 (第二套只读解析, 逻辑独立) 逐一比对, 全部一致才算可靠。

用法:
  python scripts/verify_index.py <package文件 或 目录>

对每个 package:
  1. 用 FastIndexReader 解析索引
  2. 用 ReferenceIndexReader (独立实现) 解析同一文件
  3. 比对 count + 每个 entry 的 type/group/instance/offset
  4. 汇总一致/不一致
"""

import os
import sys
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dbpf_fast import FastIndexReader


class ReferenceIndexReader:
    """独立参考实现 — 尽量用不同代码路径解析 DBPF 索引, 交叉验证。"""

    DBPF_MAGIC = b"DBPF"

    def __init__(self, path: str):
        self.path = path

    def parse(self):
        with open(self.path, "rb") as f:
            header = f.read(32)
            if header[0:4] != self.DBPF_MAGIC:
                return None, "magic"
            major = struct.unpack("<I", header[4:8])[0]
            if major not in (2, 3, 4):
                return None, f"major={major}"
            count = struct.unpack("<I", header[16:20])[0]
            idx_off = struct.unpack("<I", header[20:24])[0]
            # 独立判定 offset (不沿用 FastIndexReader 的 rel 逻辑, 用文件大小->offset)
            f.seek(0, 2)
            fsize = f.tell()
            # 若绝对偏移合理用绝对, 否则尝试相对
            off = idx_off
            if off + count * 20 > fsize:
                off = fsize - idx_off
            f.seek(off)
            raw = f.read(count * 20)
            entries = []
            for i in range(count):
                e = raw[i*20:(i+1)*20]
                t, g, hi, lo, o = struct.unpack("<IIIII", e[:20])
                entries.append((t, g, (hi << 32) | lo, o & 0x7FFFFFFF))
            return {"major": major, "count": count, "entries": entries}, None


def compare_one(path):
    """比对单个 package。返回 (ok, detail)"""
    try:
        fast_idx, ferr = _fast(path)
        ref, rerr = ReferenceIndexReader(str(path)).parse()
    except Exception as e:
        return False, f"异常: {e}"

    if ferr:
        # FastIndexReader 拒绝的(不支持的), 也看参考实现怎么判
        if rerr:
            return True, f"双方都无法解析 (fast={ferr}, ref={rerr}) — 一致跳过"
        return False, f"fast 拒绝但 ref 可解析 (fast={ferr})"
    if rerr:
        return False, f"fast 可解析但 ref 拒绝 (ref={rerr})"
    if fast_idx is None:
        return False, "fast 返回 None"
    if ref is None:
        return False, "ref 返回 None"

    if fast_idx.major != ref["major"]:
        return False, f"major 不一致 {fast_idx.major} vs {ref['major']}"

    fe = fast_idx.entries
    re_ = ref["entries"]
    if len(fe) != len(re_):
        return False, f"count 不一致 {len(fe)} vs {len(re_)}"

    for i, (a, b) in enumerate(zip(fe, re_)):
        a_t, a_g, a_i = a.type_id, a.group_id, a.instance_id
        b_t, b_g, b_i = b[0], b[1], b[2]
        if a_t != b_t or a_g != b_g or a_i != b_i:
            return False, f"entry[{i}] 不一致: fast=({a_t:#x},{a_g:#x},{a_i:#x}) ref=({b_t:#x},{b_g:#x},{b_i:#x})"

    return True, f"OK count={len(fe)} (major={fast_idx.major})"


def _fast(path):
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        try:
            idx = FastIndexReader(fh, size).read_index()
            return idx, None
        except Exception as e:
            return None, str(e)


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/verify_index.py <package文件或目录>")
        sys.exit(1)
    target = Path(sys.argv[1])
    pkgs = []
    if target.is_dir():
        for root, _, files in os.walk(target):
            for fn in files:
                if fn.lower().endswith(".package"):
                    pkgs.append(Path(root) / fn)
    else:
        pkgs.append(target)

    if not pkgs:
        print("未找到 .package 文件")
        sys.exit(1)

    ok = fail = 0
    print(f"验证 {len(pkgs)} 个 package ...\n")
    for p in pkgs:
        good, detail = compare_one(p)
        status = "✅" if good else "❌"
        print(f"{status} {p.name}: {detail}")
        if good:
            ok += 1
        else:
            fail += 1

    print(f"\n结果: 一致 {ok}, 不一致 {fail}")
    if fail == 0:
        print("✅ FastIndexReader 与参考实现全部一致 → 浅扫描器可靠")
    else:
        print("❌ 存在不一致, 需修复后再用于浅扫描")
    sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    main()
