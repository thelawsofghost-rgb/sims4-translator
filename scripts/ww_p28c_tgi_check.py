#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p28c_tgi_check.py —— P28C 独立 TGI/raw-index 普查 (source 实机多条目 vs override 单条目)

目的: 独立只读验证 P28C override package 的 WW_ANIM_XML TGI (type/group/instance)
确与源 WW package 唯一 WW_ANIM_XML 完全一致 (same-TGI override), 且 override 只含 1 个资源。

  source  : 真实 WW_Nevely42_Animations.package (多条目, 只读)
  override: output/ww_p28c/WW_P28C_TEST299_Override.package (单 WW_ANIM_XML)

机验 (raw-index, 不经 safe_parse 掩码, 直接读 32 字节 index):
  SOURCE_ENTRY_COUNT=<n>            (源多条目)
  OVERRIDE_ENTRY_COUNT=<1>
  WW_XML_SOURCE_INSTANCE=0x43F3438A94EDEB2B   (权威真实 instance, 非硬编码于 wintest fixture)
  WW_XML_SOURCE_TYPE=0x7DF2169C
  OVERRIDE_WW_XML_COUNT=1
  OVERRIDE_WW_XML_TYPE=<type> / GROUP=<group> / INSTANCE=<inst>
  TGI_EQUAL=YES                       (override WW XML TGI == source WW XML TGI)
  OVERRIDE_ONLY_ONE_RESOURCE=YES
  VERDICT=PASS

fail-closed 退出码: 0=PASS, 2=路径, 3=source 无单 WW_XML / override 架构不符 / TGI 不等。

用法:
  python scripts\\ww_p28c_tgi_check.py <source.package> <override.package>
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ww_animation_canary_builder import (  # noqa: E402
    read_entry_meta_raw,
)

WW_ANIM_XML = 0x7DF2169C


def fmt_inst(i):
    return f"0x{i:016X}"


def main():
    if len(sys.argv) < 3:
        print("usage: ww_p28c_tgi_check.py <source.package> <override.package>")
        return 2
    src = Path(sys.argv[1])
    ovr = Path(sys.argv[2])
    if not src.is_file() or not ovr.is_file():
        print("FILE_MISSING=YES")
        print("VERDICT=FAIL")
        return 2

    _maj, _min, _hc, src_meta = read_entry_meta_raw(src)
    src_xml = [m for m in src_meta if m["type"] == WW_ANIM_XML]
    if len(src_xml) != 1:
        print(f"SOURCE_WW_XML_COUNT={len(src_xml)} (需1)")
        print("VERDICT=FAIL")
        return 3
    sx = src_xml[0]
    src_inst = sx["inst"]
    src_group = sx["group"]

    _maj2, _min2, _hc2, ovr_meta = read_entry_meta_raw(ovr)
    ovr_xml = [m for m in ovr_meta if m["type"] == WW_ANIM_XML]

    ovr_count_ok = len(ovr_meta) == 1
    ovr_xml_ok = len(ovr_xml) == 1

    tgi_equal = False
    ovr_inst = ovr_group = ovr_type = 0
    if ovr_xml_ok:
        ox = ovr_xml[0]
        ovr_type = ox["type"]
        ovr_group = ox["group"]
        ovr_inst = ox["inst"]
        tgi_equal = (ovr_type == WW_ANIM_XML and ovr_group == src_group and ovr_inst == src_inst)

    verdict = (ovr_count_ok and ovr_xml_ok and tgi_equal)

    print(f"SOURCE_ENTRY_COUNT={len(src_meta)}")
    print(f"OVERRIDE_ENTRY_COUNT={len(ovr_meta)}")
    print(f"WW_XML_SOURCE_INSTANCE={fmt_inst(src_inst)}")
    print(f"WW_XML_SOURCE_GROUP=0x{src_group:08X}")
    print(f"WW_XML_SOURCE_TYPE=0x{WW_ANIM_XML:08X}")
    print(f"OVERRIDE_ONLY_ONE_RESOURCE={'YES' if ovr_count_ok else 'NO'}")
    print(f"OVERRIDE_WW_XML_COUNT={len(ovr_xml)}")
    print(f"OVERRIDE_WW_XML_TYPE=0x{ovr_type:08X}")
    print(f"OVERRIDE_WW_XML_GROUP=0x{ovr_group:08X}")
    print(f"OVERRIDE_WW_XML_INSTANCE={fmt_inst(ovr_inst)}")
    print(f"TGI_EQUAL={'YES' if tgi_equal else 'NO'}")
    print(f"VERDICT={'PASS' if verdict else 'FAIL'}")
    return 0 if verdict else 3


if __name__ == "__main__":
    sys.exit(main())
