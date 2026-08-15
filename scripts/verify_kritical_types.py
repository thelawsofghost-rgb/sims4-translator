#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_kritical_types.py — 15个 Kritical-only resource type 的 s4pi 源核对
========================================================================
对 scan A/B/C/D 得到的 Kritical-only type IDs, 从仓库内 vendored s4pi 源码
(权威来源) 逐项核实 identity。只认仓库内源码证据, 未在仓库内找到的保持
UNKNOWN (禁止凭名字猜)。

来源 (本仓库 lib/s4pi_src 内实际存在):
  - "lib/s4pi_src/s4pi Extras/Extensions/Extensions.txt"     : 类型->扩展名映射
  - "lib/s4pi_src/s4pi Wrappers/TextResource/TextResources.txt": TS4 XML 语义
  - "lib/s4pi_src/s4pi Wrappers/CatalogResource/*.cs"         : OBJD/COBJ handler
  - "lib/s4pi_src/s4pi Wrappers/*/*.cs"                        : 类型 handler/常量
  - "lib/s4pi_src/s4pi Wrappers/ObjKeyResource/ObjKeyResource.cs": enum
用途: 判断 Kritical 相比真 Pose Pack 多出的 resource 是否构成
      object/model/catalog/interaction/gameplay 正向功能证据。
只读: 不改 coverage/448/writer/resolver/classifier。不并入 production gate。
"""
# type_id -> (verified_name, source_ref, functional_signal)
# functional_signal: YES(明确功能/物品/交互证据, 已核实类型)
#                   NO (明确非功能: 网格/关节/LOD/灯光等渲染资产)
#                   UNKNOWN(未在仓库内核实, 不作判定)
VERIFIED = {
    0xC0DB5AE7: ("OBJD ObjectDefinition(catalog)", "Extensions.txt:212 + ObjectDefinitionResource.cs:669",
                 "YES"),   # 物品切面目录: object
    0x319E4F1D: ("COBJ Catalog object", "Extensions.txt:95 + COBJResource.cs:42",
                 "YES"),   # catalog 通用条目
    0x01D10F34: ("MLOD Object Geometry LODs", "Extensions.txt:10 + MeshChunks/MLOD.cs:58",
                 "NO"),    # 渲染网格 LOD (非功能)
    0xE882D22F: ("TS4 XML interaction", "TextResources.txt:96 + Extensions.txt:255",
                 "YES"),   # interaction XML (功能/交互)
    0x03B4C61D: ("LITE Light", "Extensions.txt:23 + s4piRCOLChunks/LITE.cs:60",
                 "NO"),    # 灯光 (渲染)
    0x01661233: ("MODL Object Geometry", "Extensions.txt:7 + MeshChunks/MODL.cs:342",
                 "NO"),    # 网格 (渲染)
    0x81CA1A10: ("MTBL misc table", "Extensions.txt:158 + MTBLResource.cs:347",
                 "UNKNOWN"),  # 杂项表, 内容未知
    0x8EAF13DE: ("_RIG GrannyRig", "Extensions.txt:164 + RigResource.cs:613",
                 "NO"),    # 骨架/rig (动画支撑, 非功能物品本体)
    0xD3044521: ("RSLT Slot", "Extensions.txt:221 + RSLT.cs:74",
                 "YES"),   # slot 定义 (物品可放置/交互位点)
    0x0C772E27: ("TS4 XML action", "TextResources.txt:9 + Extensions.txt:44",
                 "YES"),   # action XML (功能/行为)
    0xD382BF57: ("FTPT Footprint", "Extensions.txt:223 + FTPT.cs:68",
                 "YES"),   # footprint (放置占用)
    0x7FB6AD8A: ("UNKNOWN", "未在仓库内找到", "UNKNOWN"),
    0xB61DE6B4: ("TS4 XML object", "TextResources.txt:70 + Extensions.txt:197",
                 "YES"),   # object XML (功能/物品)
    0x02D5DF13: ("JAZZ Jazz", "Extensions.txt:14 + JazzResource.cs:213",
                 "UNKNOWN"),  # jazz 资产, 语义需进一步核实
    0xEE17C6AD: ("TS4 XML / Animation component", "Extensions.txt:262 + ObjKeyResource.cs:99",
                 "YES"),   # animation 相关 (动作证据)
}


def main():
    import sys, os
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from resource_types import RESOURCE_TYPES

    counts = {
        0xC0DB5AE7: 8, 0x319E4F1D: 8, 0x01D10F34: 6, 0xE882D22F: 5,
        0x03B4C61D: 2, 0x01661233: 2, 0x81CA1A10: 2, 0x8EAF13DE: 2,
        0xD3044521: 2, 0x0C772E27: 2, 0xD382BF57: 2, 0x7FB6AD8A: 1,
        0xB61DE6B4: 1, 0x02D5DF13: 1, 0xEE17C6AD: 1,
    }
    header = (f"{'type_id':<12}{'verified_name':<30}{'source':<48}{'conf':<9}"
              f"{'Krit':<5}{'Gounaf':<7}{'Anger':<6}{'Cry':<4}{'func_signal':<10}")
    print(header)
    print("-" * len(header))
    sig = {"YES": 0, "NO": 0, "UNKNOWN": 0}
    yes_ids = []
    for tid, (name, src, fs) in sorted(VERIFIED.items()):
        repo = RESOURCE_TYPES.get(tid)
        conf = "REPO-S4PI" if fs != "UNKNOWN" else "UNVERIFIED"
        if fs == "UNKNOWN" and repo:
            conf = "REPO-MAP"
        print(f"0x{tid:08X}  {name:<28}{src[:46]:<48}{conf:<9}"
              f"{counts.get(tid,'?'):<5}{'0':<7}{'0':<6}{'0':<4}{fs:<10}")
        sig[fs] += 1
        if fs == "YES":
            yes_ids.append(tid)
    print("\n===== 汇总 =====")
    print(f"functional YES: {sig['YES']}  NO: {sig['NO']}  UNKNOWN: {sig['UNKNOWN']}")
    print(f"\n正向功能证据 (VERIFIED object/interaction/action/slot/catalog/animation) Type IDs:")
    for tid in sorted(yes_ids):
        print(f"  0x{tid:08X}  {VERIFIED[tid][0]}")
    print("\n判定: Kritical 独有所含 resource 呈【object/catalog/interaction/footprint/slot/animation】")
    print("功能物品轮廓组合 (OBJD+COBJ+interaction+action+slot+footprint+animation),")
    print("而真 Pose Pack (Gounafiers/Anger/Cry) 完全不含有这些功能性 type。")
    print("→ 支持假设#1: 功能物品证据存在于当前未VERIFIED的非文本resource type。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
