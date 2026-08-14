#!/usr/bin/env python3
"""Phase 3B-SIDECAR: 只读审计成熟汉化 sidecar 包。

只读分析 Mods 中现有的中文汉化 override 包(如 !simkatu_..._CHT_CHS_ABonnie.package,
!Tmex-TOOL_CHT_CHS_ABonnie.package),回答:
  - resource count / Type / Group / Instance
  - locale 判定 (每 STBL 从 instance 高字节读出)
  - 是否只有 STBL
  - STBL TGI 是否与被汉化对应(审计侧不加载原 mod,仅报告 TGI 供人工比对)
  - 是否同时包含 CHS/CHT

用法:
  python scripts/audit_sidecar.py <dir_or_package> [dir_or_package ...]
  - 传目录: 递归扫该目录下所有 *.package
  - 传文件: 只审计该文件
只读, 绝不改写任何 package。
"""
import sys, os, struct

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from dbpf_fast import safe_parse, DBPFIndex, ResourceEntry

STBL_TID = 0x220557DA

def locale_of(inst: int) -> int:
    """STBL instance 高字节 = locale。0x01=CHS, 0x02=CHT, 0x00=EN 等。"""
    return (inst >> 56) & 0xFF

def audit_pkg(path: str):
    idx, err = safe_parse(path)
    if err is not None:
        print(f"== {path}")
        print(f"   [SKIP] 解析失败: {err}")
        print()
        return
    assert idx is not None
    entries: list[ResourceEntry] = idx.entries
    total = len(entries)
    stbl = [e for e in entries if e.type_id == STBL_TID]
    nonstbl = [e for e in entries if e.type_id != STBL_TID]
    print(f"== {path}")
    print(f"   major={idx.major} minor={idx.minor} count={total} "
          f"index_offset={idx.index_offset} index_size={idx.index_size}")
    print(f"   resource count: {total}  | STBL: {len(stbl)}  | 非STBL: {len(nonstbl)}")

    if stbl:
        locs = sorted({locale_of(e.instance_id) for e in stbl})
        locname = {0x00:"EN",0x01:"CHS",0x02:"CHT"}.get(locs[0] if len(locs)==1 else -1, "多locale")
        print(f"   STBL locale(s): {[f'0x{l:02X}' for l in locs]} -> {locname}")
        print(f"   {'只有STBL' if not nonstbl else f'含{len(nonstbl)}个非STBL'}")
    fr = total//1
    for e in entries:
        tag = "STBL" if e.type_id == STBL_TID else f"0x{e.type_id:08X}"
        loc = f" locale=0x{locale_of(e.instance_id):02X}" if e.type_id == STBL_TID else ""
        print(f"     [{tag}] group=0x{e.group_id:08X} inst=0x{e.instance_id:016X}{loc} "
              f"off={e.offset & 0x7FFFFFFF} sz={e.size & 0x7FFFFFFF} comp={bool(e.offset & 0x80000000)}")
    print()

def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/audit_sidecar.py <dir|package> [dir|package ...]")
        return 1
    targets = []
    for t in sys.argv[1:]:
        if os.path.isdir(t):
            for root, _, files in os.walk(t):
                for f in files:
                    if f.lower().endswith(".package"):
                        targets.append(os.path.join(root, f))
        elif os.path.isfile(t):
            targets.append(t)
    if not targets:
        print("[WARN] 未找到任何 .package 文件")
        return 0
    print(f"[audit] {len(targets)} 个 package 待审计\n")
    for t in sorted(targets):
        audit_pkg(os.path.realpath(t))
    return 0

if __name__ == "__main__":
    sys.exit(main())
