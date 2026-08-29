#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p28b_zero_xml_tgi_check.py —— P28B 零修改 XML 包机验 (独立于 report, 复用现有 parser)

被 scripts/ww_p28b_zero_xml_deploy.ps1 调用。对待部署的生成 .package 独立做一次机器校验,
不依赖 report txt, 并且直接与真实源 package 的 WW_ANIM_XML raw body 做字节级比对:

  1. WW_ANIM_XML (0x7DF2169C) 数量必须 == 1
  2. type 必须 == 0x7DF2169C
  3. instance 必须 == EXPECTED_REAL_INSTANCE (真机 0x43F3438A94EDEB2B)
  4. 绝不允许出现白盒 instance 0x4444444400000002
  5. override 的 WW XML raw body 必须与 source 的 WW XML raw body 逐字节相等
     (XML_BYTE_IDENTICAL, 由本脚本独立从两个 package 读出比, 不信任 report)

复用 src/dbpf_fast.safe_parse + ww_animation_canary_builder.read_body_raw (高可信解析器).

退出码:
  0  = PASS
  2  = 参数不足 / 文件缺失 / 不可读
  3  = safe_parse 解析失败
  4  = WW_ANIM_XML 数量 !=1 / TGI 不匹配 / 命中白盒 / XML body 不一致

用法:
  python scripts\\ww_p28b_zero_xml_tgi_check.py "<override.package>" "<source.package>"
只读; 不改任何文件。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT / "src", Path(__file__).resolve().parent):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

try:
    from dbpf_fast import safe_parse
    from ww_animation_canary_builder import read_body_raw
except Exception as ex:  # pragma: no cover
    print(f"ERROR: 依赖加载失败: {ex}", file=sys.stderr)
    sys.exit(5)

WW_ANIM_XML = 0x7DF2169C
EXPECTED_REAL_INSTANCE = 0x43F3438A94EDEB2B
WHITEBOX_INSTANCE = 0x4444444400000002


def fmt_inst(i):
    return f"0x{i:016X}"


def find_ww(pkg):
    idx, err = safe_parse(pkg)
    if err is not None or idx is None:
        return None, None, f"parse fail: {err}"
    ww = [e for e in idx.entries if getattr(e, "type_id", 0) == WW_ANIM_XML]
    return idx, ww, None


def main():
    if len(sys.argv) < 3:
        print("ERROR: 用法 python ww_p28b_zero_xml_tgi_check.py <override.package> <source.package>", file=sys.stderr)
        return 2
    opkg = Path(sys.argv[1])
    spkg = Path(sys.argv[2])
    if not opkg.is_file():
        print(f"ERROR: override 不存在 {opkg} (exit 2)", file=sys.stderr)
        return 2
    if not spkg.is_file():
        print(f"ERROR: source 不存在 {spkg} (exit 2)", file=sys.stderr)
        return 2

    idxO, wwO, oerr = find_ww(opkg)
    idxS, wwS, serr = find_ww(spkg)
    if oerr or idxO is None:
        print(f"ERROR: override 解析失败: {oerr} (exit 3)", file=sys.stderr)
        return 3
    if serr or idxS is None:
        print(f"ERROR: source 解析失败: {serr} (exit 3)", file=sys.stderr)
        return 3
    if len(wwO) != 1:
        print(f"ERROR: override WW_ANIM_XML 数量={len(wwO)} (需==1) (exit 4)", file=sys.stderr)
        return 4
    if len(wwS) != 1:
        print(f"ERROR: source WW_ANIM_XML 数量={len(wwS)} (需==1) (exit 4)", file=sys.stderr)
        return 4

    eo = wwO[0]
    es = wwS[0]
    t = getattr(eo, "type_id", 0)
    g = getattr(eo, "group_id", 0)
    inst = getattr(eo, "instance_id", None)
    inst_fmt = fmt_inst(inst) if isinstance(inst, int) else "None"
    g_src = getattr(es, "group_id", 0)
    inst_src = getattr(es, "instance_id", None)

    ok = True
    lines = [f"WW_ANIM_XML_COUNT=1", f"TYPE=0x{t:08X}", f"GROUP=0x{g:08X}", f"INSTANCE={inst_fmt}"]
    if t != WW_ANIM_XML:
        ok = False; lines.append(f"TYPE_CHECK=FAIL (需 0x{WW_ANIM_XML:08X})")
    else:
        lines.append("TYPE_CHECK=OK")
    if inst != EXPECTED_REAL_INSTANCE:
        ok = False; lines.append(f"INSTANCE_CHECK=FAIL (需 {fmt_inst(EXPECTED_REAL_INSTANCE)})")
    else:
        lines.append("INSTANCE_CHECK=OK")
    if inst == WHITEBOX_INSTANCE:
        ok = False; lines.append("WHITEBOX_INSTANCE=DETECTED -> 拒绝部署")
    if g != g_src or inst != inst_src:
        ok = False; lines.append("TGI_MATCH=FAIL (override TGI 与 source 不一致)")
    else:
        lines.append("TGI_MATCH=YES")

    # 独立字节比对: override 的 WW XML raw body == source 的 WW XML raw body
    bo = read_body_raw(opkg, eo)
    bs = read_body_raw(spkg, es)
    byte_identical = (bo == bs)
    if not byte_identical:
        ok = False
        lines.append("XML_BYTE_IDENTICAL=NO (override XML body != source XML body)")
    else:
        lines.append("XML_BYTE_IDENTICAL=YES")

    lines.append(f"VERDICT={'PASS' if ok else 'FAIL'}")
    print("\n".join(lines))
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
