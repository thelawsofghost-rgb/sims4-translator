#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p28b_zero_xml_report_check.py —— P28B 零修改 XML 报告机验 (纯 Python, 显式 UTF-8)

被 scripts/ww_p28b_zero_xml_deploy.ps1 调用。只读 report txt, 用 read_text(encoding="utf-8")
显式解码 (不依赖宿主脚本编码), 校验 P28B 生成报告满足全部 fail-closed 前提:

  * VERDICT: PASS
  * ZERO_WRITE_TO_MODS = YES
  * SOURCE_XML_SHA256 与 OVERRIDE_XML_SHA256 相同 (XML_BYTE_IDENTICAL)
  * XML_BYTE_IDENTICAL = YES
  * WW_ANIM_XML_COUNT = 1
  * type = 0x7DF2169C
  * INSTANCE = 0x43F3438A94EDEB2B
  * TGI_MATCH = YES
  * 不得出现 0x4444444400000002 (white-box fixture)

所有中文/值语义均在本 Python 文件内 (UTF-8 安全); 输出仅 ASCII 机器行。
退出码: 0=PASS, 2=缺失/不可读 (NO_REPORT), 3=校验 FAIL。

用法:
  python scripts\\ww_p28b_zero_xml_report_check.py "<report.txt>"
只读; 不改任何文件。
"""
import sys
from pathlib import Path

EXPECTED_INSTANCE = "0x43F3438A94EDEB2B"
EXPECTED_TYPE = "0x7DF2169C"
WHITEBOX_INSTANCE = "0x4444444400000002"


def main():
    if len(sys.argv) < 2:
        print("REPORT_CHECK=NO_ARGS", file=sys.stderr)
        return 2
    rp = Path(sys.argv[1])
    if not rp.is_file():
        print(f"REPORT_CHECK=NO_REPORT {rp}", file=sys.stderr)
        return 2
    try:
        text = rp.read_text(encoding="utf-8")
    except Exception as ex:
        print(f"REPORT_CHECK=UNREADABLE:{ex}", file=sys.stderr)
        return 2

    def kv(k):
        for line in text.splitlines():
            if line.startswith(k + "="):
                return line[len(k) + 1:].strip()
        return None

    verdict = kv("VERDICT")
    zw = kv("ZERO_WRITE_TO_MODS")
    src_xsha = kv("SOURCE_XML_SHA256")
    ovr_xsha = kv("OVERRIDE_XML_SHA256")
    ident = kv("XML_BYTE_IDENTICAL")
    cnt = kv("WW_ANIM_XML_COUNT")
    typ = kv("TYPE")
    inst = kv("INSTANCE")
    tgi = kv("TGI_MATCH")

    ok = True
    lines = []
    if verdict != "PASS":
        ok = False; lines.append(f"VERDICT_CHECK=FAIL (got {verdict})")
    if zw != "YES":
        ok = False; lines.append(f"ZERO_WRITE_CHECK=FAIL (got {zw})")
    if src_xsha is None or ovr_xsha is None or src_xsha != ovr_xsha:
        ok = False; lines.append("XML_SHA_MISMATCH (SOURCE_XML_SHA256 != OVERRIDE_XML_SHA256)")
    if ident != "YES":
        ok = False; lines.append(f"XML_BYTE_IDENTICAL_CHECK=FAIL (got {ident})")
    if cnt != "1":
        ok = False; lines.append(f"WW_ANIM_XML_COUNT_CHECK=FAIL (got {cnt})")
    if typ != EXPECTED_TYPE:
        ok = False; lines.append(f"TYPE_CHECK=FAIL (got {typ})")
    if inst != EXPECTED_INSTANCE:
        ok = False; lines.append(f"INSTANCE_CHECK=FAIL (got {inst})")
    if tgi != "YES":
        ok = False; lines.append(f"TGI_MATCH_CHECK=FAIL (got {tgi})")
    if WHITEBOX_INSTANCE in text:
        ok = False; lines.append("WHITEBOX_INSTANCE=DETECTED (0x4444444400000002) -> 拒绝部署")

    if ok:
        print("REPORT_CHECK=PASS")
        print(f"SOURCE_XML_SHA256={src_xsha}")
        print(f"OVERRIDE_XML_SHA256={ovr_xsha}")
        print("XML_BYTE_IDENTICAL=YES")
        print(f"INSTANCE={EXPECTED_INSTANCE}")
        print("VERDICT=PASS")
        return 0
    for ln in lines:
        print(ln)
    print("VERDICT=FAIL")
    return 3


if __name__ == "__main__":
    sys.exit(main())
