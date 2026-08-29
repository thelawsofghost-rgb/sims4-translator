#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p27_tgi_check.py —— P27 override 包 TGI 机验 (只读, 复用现有 parser)

被 scripts/ww_p27_deploy.ps1 调用。对待部署的 .package 本身做一次机器校验,
不依赖 report txt:

  1. WW_ANIM_XML (0x7DF2169C) 数量必须 == 1
  2. type 必须 == 0x7DF2169C
  3. instance 必须 == EXPECTED_REAL_INSTANCE (真机 0x43F3438A94EDEB2B)
  4. 绝不允许出现白盒 instance 0x4444444400000002 (出现即视为异常, 拒绝部署)

复用 src/dbpf_fast.safe_parse (与 P27 override 生成脚本相同的高可信解析器),
不另起脆弱 parser。

退出码:
  0  = PASS (TGI 符合真机预期)
  2  = 文件不存在 / 不可读
  3  = safe_parse 解析失败
  4  = WW_ANIM_XML 数量 != 1 或 type/instance 不匹配或命中白盒 instance

用法:
  python scripts\\ww_p27_tgi_check.py "<override.package>"

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
except Exception as ex:  # pragma: no cover
    print(f"ERROR: 依赖加载失败: {ex}", file=sys.stderr)
    sys.exit(5)

WW_ANIM_XML = 0x7DF2169C
EXPECTED_REAL_INSTANCE = 0x43F3438A94EDEB2B
WHITEBOX_INSTANCE = 0x4444444400000002


def fmt_inst(i):
    return f"0x{i:016X}"


def main():
    if len(sys.argv) < 2:
        print("ERROR: 用法 python ww_p27_tgi_check.py <override.package>", file=sys.stderr)
        return 2
    pkg = Path(sys.argv[1])
    if not pkg.is_file():
        print(f"ERROR: override 不存在 {pkg} (exit 2)", file=sys.stderr)
        return 2

    idx, err = safe_parse(pkg)
    if err is not None or idx is None:
        print(f"ERROR: 解析失败 {pkg}: err={err} (exit 3)", file=sys.stderr)
        return 3

    ww = [e for e in idx.entries if getattr(e, "type_id", 0) == WW_ANIM_XML]
    if len(ww) != 1:
        print(f"ERROR: WW_ANIM_XML 数量={len(ww)} (需==1) (exit 4)", file=sys.stderr)
        return 4

    e = ww[0]
    t = getattr(e, "type_id", 0)
    g = getattr(e, "group_id", 0)
    inst = getattr(e, "instance_id", None)
    if inst is None or not isinstance(inst, int):
        inst_fmt = "None"
        inst_int = None
    else:
        inst_fmt = fmt_inst(inst)
        inst_int = inst

    ok = True
    lines = []
    lines.append(f"WW_ANIM_XML_COUNT={len(ww)}")
    lines.append(f"TYPE=0x{t:08X}")
    lines.append(f"GROUP=0x{g:08X}")
    lines.append(f"INSTANCE={inst_fmt}")

    if t != WW_ANIM_XML:
        ok = False
        lines.append(f"TYPE_CHECK=FAIL (期望 0x{WW_ANIM_XML:08X})")
    else:
        lines.append(f"TYPE_CHECK=OK")

    if inst_int != EXPECTED_REAL_INSTANCE:
        ok = False
        lines.append(f"INSTANCE_CHECK=FAIL (期望 {fmt_inst(EXPECTED_REAL_INSTANCE)}, 实 {inst_fmt})")
    else:
        lines.append(f"INSTANCE_CHECK=OK")

    if inst_int == WHITEBOX_INSTANCE:
        ok = False
        lines.append("WHITEBOX_INSTANCE=DETECTED (0x4444444400000002) -> 拒绝部署")

    print("\n".join(lines))
    print(f"VERDICT={'PASS' if ok else 'FAIL'}")
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
