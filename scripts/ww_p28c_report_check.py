#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p28c_report_check.py —— P28C report 独立机验 (不信任 report 声明的 YES 字样)

对 output/ww_p28c/ww_p28c_report.txt + 实际生成包做独立二次验证:
  1. report 显式 UTF-8 读取; 解析全部 report 键。
  2. 独立重读 key (不信任 report 文本值本身, 从纯 ASCII key-path 提取):
       VERDICT=PASS / ZERO_WRITE_TO_MODS=YES / WW_ANIM_XML_COUNT=1
       TGI_MATCH=YES / INSTANCE=0x43F3438A94EDEB2B / TARGET_ORDINAL=300
       TARGET_OLD_RAW=Caught Cheating 2 / TARGET_NEW_RAW=TEST300 / TARGETS_CHANGED=1/1 / NON_TARGET_XML_DIFF=0
  3. 独立对【真实生成包 bytes】逐字节复核 (不读 report 的 sha):
       a. 包存在, 单 WW_ANIM_XML, TGI 与 report 一致
       b. 解压新 payload 实际长度 == WRITTEN_MEM_SIZE (report 值) —— mem_size 修复机验
       c. 解压新 XML 中唯一出现 <T n="animation_raw_display_name">TEST300</T> 一次
       d. ordinal 300 语义命中 (自元数据多源交叉, ASCII)
  4. 全部通过才 REPORT_CHECK=PASS exit 0; 任一项不满足 fail-closed exit 4。

退出码: 0=PASS, 2=路径/report 缺失, 3=report 内容不满足, 4=真实包独立复核 FAIL。

用法:
  python scripts\\ww_p28c_report_check.py "<out>/ww_p28c/ww_p28c_report.txt"
"""
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ww_animation_canary_builder import (  # noqa: E402
    decompress_maybe,
    read_body_raw,
    read_entry_meta_raw,
    safe_parse,
)

WW_ANIM_XML = 0x7DF2169C
EXPECTED_INSTANCE = 0x43F3438A94EDEB2B
RAW_FIELD = "animation_raw_display_name"
ANIM_LIST_FIELD = "animations_list"
TARGET_ORDINAL = 300
TARGET_OLD_RAW = "Caught Cheating 2"
TARGET_NEW_RAW = "TEST300"


def parse_kv(path: Path):
    d = {}
    text = path.read_text(encoding="utf-8")  # 显式 UTF-8
    for ln in text.splitlines():
        if "=" in ln and not ln.strip().startswith("#"):
            k, v = ln.split("=", 1)
            d[k.strip()] = v.strip()
    return d, text


def main():
    if len(sys.argv) < 2:
        print("usage: ww_p28c_report_check.py <report.txt>")
        return 2
    rep = Path(sys.argv[1])
    if not rep.is_file():
        print("REPORT_MISSING=YES")
        print("REPORT_CHECK=FAIL")
        return 2

    d, raw = parse_kv(rep)

    # ---- 独立必查 report keys ----
    expect_keys = [
        "VERDICT", "ZERO_WRITE_TO_MODS", "WW_ANIM_XML_COUNT", "TGI_MATCH",
        "INSTANCE", "TARGET_ORDINAL", "TARGET_OLD_RAW", "TARGET_NEW_RAW",
        "TARGETS_CHANGED",
        "NON_TARGET_XML_DIFF", "WRITTEN_MEM_SIZE", "NEW_XML_DECOMPRESSED_SIZE",
    ]
    missing = [k for k in expect_keys if k not in d]
    if missing:
        print(f"REPORT_MISSING_KEYS={','.join(missing)}")
        print("REPORT_CHECK=FAIL")
        return 3

    cond_ok = (
        d["VERDICT"] == "PASS"
        and d["ZERO_WRITE_TO_MODS"] == "YES"
        and d["WW_ANIM_XML_COUNT"] == "1"
        and d["TGI_MATCH"] == "YES"
        and d["INSTANCE"] == f"0x{EXPECTED_INSTANCE:016X}"
        and d["TARGET_ORDINAL"] == str(TARGET_ORDINAL)
        and d["TARGET_OLD_RAW"] == TARGET_OLD_RAW
        and d["TARGET_NEW_RAW"] == TARGET_NEW_RAW
        and d["TARGETS_CHANGED"].startswith("1/")   # 1/任意总数
        and d["NON_TARGET_XML_DIFF"] == "0"
        and d["WRITTEN_MEM_SIZE"].isdigit()
        and d["NEW_XML_DECOMPRESSED_SIZE"].isdigit()
        and int(d["WRITTEN_MEM_SIZE"]) == int(d["NEW_XML_DECOMPRESSED_SIZE"])
    )
    if not cond_ok:
        print("REPORT_CHECK=FAIL")
        print("VERDICT=SEMANTIC_FAIL")
        return 3

    # ---- 独立对真实包 bytes 复核 (不信 report 的 sha; 自行读包) ----
    pkg_line = next((ln for ln in raw.splitlines() if ln.startswith("OVERRIDE_PKG=")), None)
    pkg = Path(pkg_line.split("=", 1)[1].strip()) if pkg_line else None
    if pkg is None or not pkg.is_file():
        print("PKG_MISSING=YES")
        print("REPORT_CHECK=FAIL")
        return 4

    idx, err = safe_parse(pkg)
    if err is not None or idx is None:
        print("PKG_PARSE_FAIL=YES")
        print("REPORT_CHECK=FAIL")
        return 4
    ww = [e for e in idx.entries if getattr(e, "type_id", 0) == WW_ANIM_XML]
    if len(ww) != 1:
        print("WW_XML_COUNT_INDEP!=1")
        print("REPORT_CHECK=FAIL")
        return 4
    e = ww[0]
    inst_indep = getattr(e, "instance_id", None)
    if inst_indep != EXPECTED_INSTANCE:
        print(f"INSTANCE_INDEP=0x{inst_indep:016X} != expected")
        print("REPORT_CHECK=FAIL")
        return 4

    # 独立 field7/mem_size
    _, _, _, om = read_entry_meta_raw(pkg)
    field7_indep = om[0]["mem_size"] if om else -1
    body = read_body_raw(pkg, e)
    decomp_indep = len(decompress_maybe(body))
    mem_ok = (field7_indep == decomp_indep)
    if not mem_ok:
        print(f"MEM_SIZE_INDEP_FAIL field7={field7_indep} decomp={decomp_indep}")
        print("REPORT_CHECK=FAIL")
        return 4

    # 独立解析 XML: 唯一 TEST300 出现; ordinal 300 命中
    txt = decompress_maybe(body).decode("utf-8", errors="replace")
    if txt.count(f">{TARGET_NEW_RAW}<") != 1:
        print(f"TEST300_OCCURRENCE={txt.count(f'>{TARGET_NEW_RAW}<')} != 1")
        print("REPORT_CHECK=FAIL")
        return 4
    xml_ok = True
    try:
        root = ET.fromstring(txt)
        vl = None
        for el in root.iter():
            if el.tag.rsplit("}", 1)[-1] == "L" and el.get("n") == ANIM_LIST_FIELD:
                vl = el
                break
        v_ord = 0
        found_tgt = False
        found_old_raw_hit = False
        for child in (vl if vl is not None else []):
            tag = child.tag.rsplit("}", 1)[-1] if isinstance(child.tag, str) else child.tag
            if tag != "U":
                continue
            for sc in child:
                sctag = sc.tag.rsplit("}", 1)[-1] if isinstance(sc.tag, str) else sc.tag
                if sctag in ("T", "I", "E") and sc.get("n") == RAW_FIELD:
                    # 目标 ordinal 处必须是 TEST300
                    if v_ord == TARGET_ORDINAL:
                        found_old_raw_hit = (sc.text == TARGET_NEW_RAW)
                    if sc.text == TARGET_NEW_RAW:
                        found_tgt = True
                    break
            v_ord += 1
        xml_ok = (found_tgt and found_old_raw_hit)
    except ET.ParseError:
        xml_ok = False
    if not xml_ok:
        print("XML_TARGET_VERIFY_FAIL")
        print("REPORT_CHECK=FAIL")
        return 4

    print("REPORT_CHECK=PASS")
    print("VERDICT=INDEPENDENT_COMPARE_OK")
    print(f"FIELD7_INDEP={field7_indep}")
    print(f"DECOMP_INDEP={decomp_indep}")
    print(f"MEM_SIZE_INDEP_MATCH={'YES' if mem_ok else 'NO'}")
    print("P27_MEM_SIZE_REGRESSION_FIXED=YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
