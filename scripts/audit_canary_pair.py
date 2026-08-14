#!/usr/bin/env python3
"""Phase 3B-SIDECAR: 独立只读二次审计 —— source vs canary 输出包逐项比对。

复用 canonical DBPF parser (dbpf_fast) 与同 STBL 读取实现 (audit_sidecar.read_stbl_entries),
不调用 SidecarBuilder 自己的 reopen verify, 保证审计视角独立。

对比对象:
  -source <golden.package>   原 mod 的 CHS STBL (只读)
  -out   <canary.package>    SidecarBuilder COMPLETE-STBL 生成的独立包 (只读)

输出 (明确的验收标签):
  RESOURCE_COUNT
  STBL_COUNT
  TGI
  SOURCE_ENTRIES
  OUTPUT_ENTRIES
  CHANGED_KEYS
  UNTOUCHED_MISMATCHES
  AUDIT=PASS/FAIL

判定标准 (对 canary 输出包):
  - 必须是合法 DBPF
  - resource_count == 1
  - STBL_count == 1
  - TGI == TYPE 0x220557DA / GROUP 0x80000000 / INSTANCE 由参数给出 (通常就是 source CHS 的 instance)
  - STBL version == 5, entries == 5
  - 完整 key/value 精确匹配期望集合
  - 与 source CHS STBL 逐项: KeyHash 顺序完全相同, Flags 完全相同,
    除指定的 modified key 外所有 StringValue 完全相同, 无新增/删除 key, 无额外 resource

用法:
  python scripts/audit_canary_pair.py -source <GOLDEN> -out <CANARY> \
      -exp-inst 0x014EACCF17C8B091 \
      -m 0x763F7534 \
      -expected-keys 0x30A06E9B:t0nischwartz,0xFDD36EF2:左A,0xC34411E9:右A,0x552CC77A:相拥,0x763F7534:自动测试双人表情包相拥姿势

只读, 绝不改写任何 package。
"""
import sys, os, struct, argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dbpf_fast import safe_parse
import audit_sidecar as A  # 复用 locale_of / read_stbl_entries / STBL_TID (canonical, 不重复造 parser)

LOCALE_CHS = 0x01


def locale_of(inst: int) -> int:
    return (inst >> 56) & 0xFF


def read_one_stbl(path: str, e) -> tuple:
    """读单个 STBL 资源, 返回 (version, is_compressed, reserved, [(kh,flags,text)]).
    Header 布局按 s4pi StblResource (vendored 源码核实): magic@0(u32) version@4(u16)
    isComp@6(u8) count@7(u64) reserved@15(2B) stringLength@17(u32) entries@21."""
    try:
        off = e.offset & 0x7FFFFFFF
        sz = e.size & 0x7FFFFFFF
        with open(path, "rb") as fh:
            fh.seek(off); body = fh.read(sz)
        if body[0:4] != b"STBL":
            return None, None, None, None
        version = struct.unpack_from("<H", body, 4)[0]
        is_comp = body[6]
        count = struct.unpack_from("<Q", body, 7)[0]
        reserved = body[15:17]
        keys = []
        o = 21
        for _ in range(count):
            if o + 7 > len(body):
                break
            kh = struct.unpack_from("<I", body, o)[0]
            fl = body[o + 4]
            ln = struct.unpack_from("<H", body, o + 5)[0]
            txt = body[o + 7:o + 7 + ln].decode("utf-8", errors="replace")
            keys.append((kh, fl, txt))
            o += 7 + ln
        return version, is_comp, reserved, keys
    except Exception:
        return None, None, None, None


def expect_tgi(idx, inst):
    """只允许恰好 1 个 STBL 资源, 且 TGI 完全匹配。返回 (ok_reason_or_None)。"""
    stbl = [e for e in idx.entries if e.type_id == A.STBL_TID]
    if len(stbl) != 1:
        return "resource_count/STBL 预期 1, 实际 " + str(len(stbl))
    e = stbl[0]
    if e.type_id != A.STBL_TID:
        return f"type 0x{e.type_id:08X} != 0x{A.STBL_TID:08X}"
    # group: 须为 0x80000000
    if (e.group_id & 0xFFFFFFFF) != 0x80000000:
        return f"group 0x{e.group_id:08X} != 0x80000000"
    if inst is not None and e.instance_id != inst:
        return f"instance 0x{e.instance_id:016X} != expected 0x{inst:016X}"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-source", required=True)
    ap.add_argument("-out", required=True)
    ap.add_argument("-exp-inst", default=None, help="期望 STBL instance, 如 0x014EACCF17C8B091")
    ap.add_argument("-m", action="append", default=[], help="modified key hash (hex, 可重复)")
    ap.add_argument("-expected-keys", default="",
                    help="期望完整 key/value: KEYHASH:TEXT,KEYHASH:TEXT,... (验证输出有这些 key 与精确文本)")
    args = ap.parse_args()

    src_path = os.path.realpath(args.source)
    out_path = os.path.realpath(args.out)
    exp_inst = int(args.exp_inst, 16) if args.exp_inst else None
    exp_keys = {}
    if args.expected_keys:
        for kv in args.expected_keys.split(","):
            kh_s, txt = kv.split(":", 1)
            exp_keys[int(kh_s, 16)] = txt
    modified = {int(x, 16) for x in args.m}

    # 解析两个包 (独立 parser)
    src_idx, src_err = safe_parse(src_path)
    out_idx, out_err = safe_parse(out_path)
    if src_err:
        print(f"SOURCE_PARSE_ERROR {src_path}: {src_err}"); return 2
    if out_err:
        print(f"OUTPUT_PARSE_ERROR {out_path}: {out_err}"); return 2
    assert src_idx is not None and out_idx is not None

    # ---- canary 输出包自身判定 ----
    out_stbl = [e for e in out_idx.entries if e.type_id == A.STBL_TID]
    RESOURCE_COUNT = len(out_idx.entries)
    STBL_COUNT = len(out_stbl)
    tgi_err = expect_tgi(out_idx, exp_inst)
    got_inst = out_stbl[0].instance_id if out_stbl else None

    out_version, out_comp, out_res, out_keys = None, None, None, None
    if out_stbl:
        out_version, out_comp, out_res, out_keys = read_one_stbl(out_path, out_stbl[0])

    # ---- source CHS STBL (若 source 含 CHS 则取之, 否则取唯一 STBL) ----
    src_stbl = [e for e in src_idx.entries if e.type_id == A.STBL_TID]
    src_chs = [e for e in src_stbl if locale_of(e.instance_id) == LOCALE_CHS]
    src_sel = (src_chs[0] if src_chs else src_stbl[0]) if src_stbl else None
    src_version, src_comp, src_res, src_keys = None, None, None, None
    if src_sel:
        src_version, src_comp, src_res, src_keys = read_one_stbl(src_path, src_sel)

    # ---- 比对 ----
    problems = []
    if RESOURCE_COUNT != 1:
        problems.append(f"RESOURCE_COUNT={RESOURCE_COUNT} != 1")
    if STBL_COUNT != 1:
        problems.append(f"STBL_COUNT={STBL_COUNT} != 1")
    if tgi_err:
        problems.append("TGI " + tgi_err)
    else:
        print(f"TGI = 0x{A.STBL_TID:08X} / 0x80000000 / 0x{got_inst:016X}")

    # version
    if out_version != 5:
        problems.append(f"OUTPUT version={out_version} != 5")
    if src_version is not None and out_version != src_version:
        problems.append(f"output version {out_version} != source version {src_version}")

    # entries 数量
    SOURCE_ENTRIES = len(src_keys) if src_keys else 0
    OUTPUT_ENTRIES = len(out_keys) if out_keys else 0
    if OUTPUT_ENTRIES != SOURCE_ENTRIES:
        problems.append(f"OUTPUT_ENTRIES={OUTPUT_ENTRIES} != SOURCE_ENTRIES={SOURCE_ENTRIES}")

    # expected key/value 全集
    if out_keys is not None:
        out_map = {kh: (fl, txt) for kh, fl, txt in out_keys}
        for kh, want_txt in exp_keys.items():
            if kh not in out_map:
                problems.append(f"missing expected key 0x{kh:08X}")
            elif out_map[kh][1] != want_txt:
                problems.append(f"key 0x{kh:08X} value {out_map[kh][1]!r} != expected {want_txt!r}")

    # source vs output 逐项
    CHANGED_KEYS = []
    UNTOUCHED_MISMATCHES = []
    if src_keys is not None and out_keys is not None:
        # KeyHash 顺序
        src_hashes = [kh for kh, _, _ in src_keys]
        out_hashes = [kh for kh, _, _ in out_keys]
        if src_hashes != out_hashes:
            problems.append("KeyHash 顺序/集合不一致: src=" +
                            ",".join(f"0x{h:08X}" for h in src_hashes) +
                            " out=" + ",".join(f"0x{h:08X}" for h in out_hashes))
        # Flags + StringValue
        src_map = {kh: (fl, txt) for kh, fl, txt in src_keys}
        for kh, ofl, otxt in out_keys:
            if kh not in src_map:
                problems.append(f"output 有新增 key 0x{kh:08X}")
                continue
            sfl, stxt = src_map[kh]
            if ofl != sfl:
                problems.append(f"key 0x{kh:08X} flags {ofl} != source {sfl}")
            if kh in modified:
                if otxt == stxt:
                    problems.append(f"modified key 0x{kh:08X} 未变化 (仍={stxt!r})")
                else:
                    CHANGED_KEYS.append(kh)
            else:
                if otxt != stxt:
                    UNTOUCHED_MISMATCHES.append((kh, stxt, otxt))
        # 无删除 key
        for kh in src_map:
            if kh not in {h for h, _, _ in out_keys}:
                problems.append(f"output 删除 key 0x{kh:08X}")
    else:
        problems.append("无法读取 source 或 output STBL entries")

    # 无额外 resource
    if len(out_idx.entries) != len([e for e in out_idx.entries if e.type_id == A.STBL_TID]):
        problems.append("output 含非 STBL resource 或重复")

    print()
    print(f"RESOURCE_COUNT = {RESOURCE_COUNT}")
    print(f"STBL_COUNT = {STBL_COUNT}")
    if got_inst is not None:
        print(f"TGI = 0x{A.STBL_TID:08X} / 0x80000000 / 0x{got_inst:016X}")
    print(f"SOURCE_ENTRIES = {SOURCE_ENTRIES}")
    print(f"OUTPUT_ENTRIES = {OUTPUT_ENTRIES}")
    print(f"CHANGED_KEYS = " + (",".join(f"0x{h:08X}" for h in CHANGED_KEYS) if CHANGED_KEYS else "(none)"))
    if UNTOUCHED_MISMATCHES:
        print(f"UNTOUCHED_MISMATCHES = " +
              ",".join(f"0x{h:08X}{st!r}->{ot!r}" for h, st, ot in UNTOUCHED_MISMATCHES))
    else:
        print("UNTOUCHED_MISMATCHES = 0")
    if out_comp is not None and out_comp != 0:
        problems.append(f"output compressed={out_comp} != 0")
    for p in problems:
        print("AUDIT-ISSUE: " + p)

    ok = not problems and not UNTOUCHED_MISMATCHES
    print()
    print("AUDIT=" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
