#!/usr/bin/env python3
"""Phase 3A 只读取证: 扫指定 .package 内所有 STBL 的 locale(最高字节) + key 数 + 抽样。
复用 canonical parser (map_pose_texts.parse_stbl, 已验证产出 9061 条 reverse mapping),
不另写 STBL parser。fail-fast: 结构不符即 ERROR, 不静默 keys=0。
READ-ONLY, 不写任何文件。
用法: python scripts/probe_stbl_locale.py <package_path...>
"""
import sys, zlib, struct
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse
from backend import get_backend
# canonical parser: map_pose_texts 的同名函数 (已验证 reverse mapping 全量成功)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from map_pose_texts import parse_stbl


def locale_of(inst: int) -> int:
    return (inst >> 56) & 0xFF


def selftest() -> int:
    """回归: 直接喂 STBL 字节测 fail-fast 分支 (不依赖 DBPF 生成器)."""
    def mk(entries, count_override=None, magic=b"STBL", ver=5):
        body = bytearray(magic); body += struct.pack("<H", ver); body += b"\x00"
        body += struct.pack("<Q", len(entries) if count_override is None else count_override)
        body += b"\x00\x00" + struct.pack("<I", 0)
        for kh, t in entries:
            tb = t.encode(); body += struct.pack("<I", kh) + b"\x00" + struct.pack("<H", len(tb)) + tb
        return bytes(body)

    e = [(1, "a"), (2, "b")]
    cases = {
        "normal(2)":        mk(e),
        "count_mismatch":   mk(e, count_override=5),
        "count_zero":       mk([], count_override=0),
        "bad_magic":        mk(e, magic=b"BAD!"),
        "bad_ver":          mk(e, ver=9),
    }
    fails = 0
    for name, data in cases.items():
        if len(data) < 15 or data[:4] != b"STBL" or int.from_bytes(data[4:6], "little") != 5:
            verdict = "ERROR(fail-fast)"
        else:
            declared = struct.unpack_from("<Q", data, 7)[0]
            parsed = parse_stbl(data)
            nk = len(parsed) if parsed else 0
            if parsed is None or nk == 0 or declared != nk:
                verdict = "ERROR(fail-fast)"
            else:
                verdict = "PASS"
        exp = "ERROR" if ("bad" in name or "mismatch" in name or "zero" in name) else "PASS"
        ok = (verdict.startswith(exp))
        if not ok: fails += 1
        print(f"  {name:16} -> {verdict:18} expect {exp:5} : {'OK' if ok else 'FAIL'}")
    print(f"\nselftest: {'全部通过' if fails==0 else str(fails)+' FAIL'}")
    return 1 if fails else 0


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        return selftest()
    if len(sys.argv) < 2:
        print("用法: python scripts/probe_stbl_locale.py <package_path...>"); return 2
    rc = 0
    for p in sys.argv[1:]:
        pp = Path(p)
        if not pp.exists():
            print(f"[缺文件] {p}"); rc = 1; continue
        idx, err = safe_parse(str(pp))
        if err or idx is None:
            print(f"[解析失败] {p}: {err}"); rc = 1; continue
        bk = get_backend("readonly").open(str(pp))
        stbls = [e for e in idx.entries if e.type_id == 0x220557DA]
        print(f"\n=== {pp.name} : STBL 资源 {len(stbls)} 个 ===")
        for e in stbls:
            inst = getattr(e, "instance_id", 0)
            lo = locale_of(inst)
            data = bk.read_small_resource(e, max_bytes=2 * 1024 * 1024)
            if data is None:
                print(f"  locale=0x{lo:02X} inst={inst:016X} [读取失败]"); rc = 1; continue
            raw = data
            if raw[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
                try:
                    raw = zlib.decompress(raw)
                except Exception as ex:
                    print(f"  locale=0x{lo:02X} inst={inst:016X} [zlib失败 {ex}]"); rc = 1; continue
            if raw[:4] != b"STBL":
                print(f"  locale=0x{lo:02X} inst={inst:016X} [ERROR magic={raw[:4]!r} != STBL]"); rc = 1; continue
            # 结构校验: version + declared count (WW CHS 实测: count=<Q>@offset7)
            if len(raw) < 15 or int.from_bytes(raw[4:6], "little") != 5:
                print(f"  locale=0x{lo:02X} inst={inst:016X} [ERROR unsupported version/short]"); rc = 1; continue
            declared = struct.unpack_from("<Q", raw, 7)[0]
            parsed = parse_stbl(data)  # canonical, zlib-aware
            if parsed is None:
                print(f"  locale=0x{lo:02X} inst={inst:016X} [ERROR canonical parse=None (mangled)]"); rc = 1; continue
            nk = len(parsed)
            if nk == 0:
                print(f"  locale=0x{lo:02X} inst={inst:016X} [ERROR parse=0 key]"); rc = 1; continue
            if declared != nk:
                print(f"  locale=0x{lo:02X} inst={inst:016X} [ERROR declared={declared} != parsed={nk}]"); rc = 1; continue
            sam = ""
            if parsed:
                first = next(iter(parsed.items()))
                sam = repr(first[1][:40])
            print(f"  locale=0x{lo:02X} inst={inst:016X} keys={nk}  e.g. {sam}")
        bk.close()
    print("\n取证完成 (只读; 有 ERROR 时 rc!=0)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
