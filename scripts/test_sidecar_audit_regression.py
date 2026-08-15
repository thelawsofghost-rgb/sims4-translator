#!/usr/bin/env python3
"""Phase 3B-SIDECAR: run2 generation 修复回归 —— A(noop KEEP-only) / B(压缩STBL+ AUDIT_ERROR) / C(malformed mapping)

覆盖 (不调真实 LLM / writer / 真实包, 仅本地 fixture):
  A) run_one 对 KEEP-only package -> PASS_NOOP_KEEP_ONLY + SKIP_NO_OUTPUT, 不调 writer, 不生成 sidecar
  B) audit_canary_pair.read_one_stbl 对 zlib 压缩 source 正确解压读出 entries (不再 SOURCE_ENTRIES=0)
     -- 且对真正读不了的 source 显式 SOURCE_AUDIT_ERROR (SOURCE_ENTRIES=None), 绝伪造 0
  C) -expected-key 独立 argv token: 翻译含逗号+冒号不再崩; malformed token(缺冒号) -> HARD-FAIL rc=2 精确报错

用法: python scripts/test_sidecar_audit_regression.py
退出码: 0=全部通过; 1=有失败。
"""
import sys, os, struct, zlib, subprocess, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


import _tibo_fixture as TF
import gen_cohort_sidecars as G


# ---- 纯 STBL-only 输出包 (真正的 sidecar) ----
def make_stbl_only(path, entries):
    from _tibo_fixture import STBL_TID, CHS_INST
    data = TF.build_stbl(entries)
    count = 1; HEADER = 0x44; PAD = 4; ENTRY = 32
    off = HEADER + PAD + count * ENTRY
    buf = bytearray(off + len(data))
    buf[0:4] = b"DBPF"
    struct.pack_into("<I", buf, 4, 2); struct.pack_into("<I", buf, 8, 0)
    struct.pack_into("<I", buf, 0x24, count)
    struct.pack_into("<I", buf, 0x2C, PAD + count * ENTRY)
    struct.pack_into("<I", buf, 0x40, HEADER)
    p = HEADER; struct.pack_into("<I", buf, p, 0); p += PAD
    struct.pack_into("<I", buf, p, STBL_TID); p += 4
    struct.pack_into("<I", buf, p, 0x80000000); p += 4
    struct.pack_into("<I", buf, p, CHS_INST >> 32); p += 4
    struct.pack_into("<I", buf, p, CHS_INST & 0xFFFFFFFF); p += 4
    struct.pack_into("<I", buf, p, off); p += 4
    struct.pack_into("<I", buf, p, len(data)); p += 4
    struct.pack_into("<I", buf, p, 0); p += 4; struct.pack_into("<I", buf, p, 0); p += 4
    buf[off:] = data
    open(path, "wb").write(bytes(buf))


def make_pkg_compressed_stbl(path, stbl_entries, xml_payload):
    """构建 DBPF: XML + zlib 压缩的 CHS STBL (模拟 slot7 源包)."""
    from _tibo_fixture import XML_TID, STBL_TID, CHS_INST
    stbl_z = zlib.compress(TF.build_stbl(stbl_entries))
    data_items = [(XML_TID, 0, 0x0000000000000001, xml_payload),
                  (STBL_TID, 0x80000000, CHS_INST, stbl_z)]
    count = len(data_items); HEADER = 0x44; PAD = 4; ENTRY = 32
    index_size = PAD + count * ENTRY; index_offset = HEADER; data_off = index_offset + index_size
    idx = []; off = data_off
    for (t, g, inst, d) in data_items:
        sz = len(d); idx.append((t, g, inst, off, sz)); off += sz
    buf = bytearray(off)
    buf[0:4] = b"DBPF"
    struct.pack_into("<I", buf, 4, 2); struct.pack_into("<I", buf, 8, 0)
    struct.pack_into("<I", buf, 0x24, count)
    struct.pack_into("<I", buf, 0x2C, index_size)
    struct.pack_into("<I", buf, 0x40, index_offset)
    p = index_offset; struct.pack_into("<I", buf, p, 0); p += PAD
    for (t, g, inst, o, sz) in idx:
        struct.pack_into("<I", buf, p, t); p += 4
        struct.pack_into("<I", buf, p, g); p += 4
        struct.pack_into("<I", buf, p, inst >> 32); p += 4
        struct.pack_into("<I", buf, p, inst & 0xFFFFFFFF); p += 4
        struct.pack_into("<I", buf, p, o); p += 4
        struct.pack_into("<I", buf, p, sz); p += 4
        struct.pack_into("<I", buf, p, 0); p += 4; struct.pack_into("<I", buf, p, 0); p += 4
    p = data_off
    for (t, g, inst, o, sz) in idx:
        for (t2, g2, inst2, d) in data_items:
            if (t2, g2, inst2) == (t, g, inst):
                buf[p:p + sz] = d; p += sz; break
    open(path, "wb").write(bytes(buf))


_TMP = tempfile.mkdtemp(prefix="sidecar_audit_reg_")


# ============================================================ A
def test_A():
    pkg = os.path.join(_TMP, "a_keeponly.package")
    author = "Tibo131"; pack = "Pack X"; poses = ["P1", "P2", "P3"]
    displays = ["Left", "Right", "Kiss"]
    kh = [TF.fnv32(d) for d in displays]
    stbl = [(TF.fnv32(author), 0, author), (TF.fnv32(pack), 0, pack)] + \
           [(kh[i], 0, displays[i]) for i in range(3)]
    TF.make_pkg(pkg, stbl, TF.build_xml(poses, kh))
    approved = G.approved_pv_refs(pkg)[2]
    check("A.approved 非空", len(approved) > 0)

    class AllKeep:
        def resolve(self, s):
            return (None, "KEEP")

    outdir = os.path.join(_TMP, "a_out")
    os.makedirs(outdir, exist_ok=True)
    row = G.run_one(1, pkg, outdir, "/nonexistent/writer.exe", AllKeep(), "")
    check("A.PASS_NOOP_KEEP_ONLY", row["writer_verify"] == "PASS_NOOP_KEEP_ONLY",
          row["writer_verify"])
    check("A.SKIP_NO_OUTPUT", row["audit_result"] == "SKIP_NO_OUTPUT", row["audit_result"])
    check("A.无 error", row["error"] == "")
    check("A.不生成 sidecar 文件", not os.path.exists(row["output_sidecar"]),
          row["output_sidecar"])


# ============================================================ B
def test_B():
    import audit_canary_pair as AC
    from dbpf_fast import safe_parse
    # 压缩 source
    src = os.path.join(_TMP, "b_src.package")
    poses = ["P1", "P2", "P3"]
    kh = [TF.fnv32(d) for d in ["Doom", "Gloom", "Boom"]]
    src_entries = [(TF.fnv32("B"), 0, "B"), (TF.fnv32("pack"), 0, "pack")] + \
                  [(kh[i], 0, ["Doom", "Gloom", "Boom"][i]) for i in range(3)]
    make_pkg_compressed_stbl(src, src_entries, TF.build_xml(poses, kh))

    idx, _ = safe_parse(src)
    chs = [e for e in idx.entries if e.type_id == AC.A.STBL_TID and ((e.instance_id >> 56) & 0xFF) == 1][0]
    ver, comp, res, slen, keys, err = AC.read_one_stbl(src, chs)
    check("B.压缩 source 解压读出", keys is not None and len(keys) == 5,
          f"ver={ver} len={len(keys) if keys else None} err={err}")
    check("B.无 read error", err is None)

    # 真正读不了的 source -> AUDIT_ERROR (SOURCE_ENTRIES=None), 绝伪造 0
    bad = os.path.join(_TMP, "b_bad.package")
    garbage = b"\x00\x01\x02\x03NOT_A_STBL__garbage_without_magic"[:20]
    data_items = [(0x0333406C, 0, 1, TF.build_xml(["P1"], [TF.fnv32("Doom")])),
                  (0x220557DA, 0x80000000, 0x014EACCF17C8B091, garbage)]
    count = 2; HEADER = 0x44; PAD = 4; ENTRY = 32
    idxs = []; off = HEADER + PAD + count * ENTRY
    for (t, g, inst, d) in data_items:
        sz = len(d); idxs.append((t, g, inst, off, sz)); off += sz
    buf = bytearray(off)
    buf[0:4] = b"DBPF"
    struct.pack_into("<I", buf, 4, 2); struct.pack_into("<I", buf, 8, 0)
    struct.pack_into("<I", buf, 0x24, count)
    struct.pack_into("<I", buf, 0x2C, PAD + count * ENTRY)
    struct.pack_into("<I", buf, 0x40, HEADER)
    p = HEADER; struct.pack_into("<I", buf, p, 0); p += PAD
    for (t, g, inst, o, sz) in idxs:
        struct.pack_into("<I", buf, p, t); p += 4
        struct.pack_into("<I", buf, p, g); p += 4
        struct.pack_into("<I", buf, p, inst >> 32); p += 4
        struct.pack_into("<I", buf, p, inst & 0xFFFFFFFF); p += 4
        struct.pack_into("<I", buf, p, o); p += 4
        struct.pack_into("<I", buf, p, sz); p += 4
        struct.pack_into("<I", buf, p, 0); p += 4; struct.pack_into("<I", buf, p, 0); p += 4
    p = HEADER + PAD + count * ENTRY
    for (t, g, inst, o, sz) in idxs:
        for (t2, g2, inst2, d) in data_items:
            if (t2, g2, inst2) == (t, g, inst):
                buf[p:p + sz] = d; p += sz; break
    open(bad, "wb").write(bytes(buf))
    bidx, _ = safe_parse(bad)
    bchs = [e for e in bidx.entries if e.type_id == AC.A.STBL_TID][0]
    bver, bcomp, bres, bslen, bkeys, berr = AC.read_one_stbl(bad, bchs)
    check("B.不可读 source -> err 非 None", berr is not None, berr)
    check("B.不可读 source -> keys None (不伪造 0)", bkeys is None)

    # 完整 audit 命令 (压缩 source + 纯STBL output + 逗号冒号文本) -> PASS
    # 注意: 输出必须是 complete clone (全部 5 个 key, 仅改 Doom), 否则审计按 no-add 不变式正确 FAIL。
    out = os.path.join(_TMP, "b_out.package")
    make_stbl_only(out, [(TF.fnv32("B"), 0, "B"), (TF.fnv32("pack"), 0, "pack"),
                         (kh[0], 0, "黑暗, 深渊, 冒号:测试"), (kh[1], 0, "Gloom"), (kh[2], 0, "Boom")])
    exp = "0x014EACCF17C8B091"
    cmd = [sys.executable, os.path.join(HERE, "audit_canary_pair.py"),
           "-source", src, "-out", out, "-exp-inst", exp,
           "-m", f"0x{kh[0]:08X}", "-expected-key", f"0x{kh[0]:08X}:黑暗, 深渊, 冒号:测试"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    check("B.压缩源 + 逗号冒号 + 纯STBL 输出 -> AUDIT=PASS",
          "AUDIT=PASS" in r.stdout and r.returncode == 0, f"rc={r.returncode}")

    # B2: output 多于 source (新增 key) -> 必须 FAIL (complete-clone / no-add 不变式)
    out2 = os.path.join(_TMP, "b_out_add.package")
    make_stbl_only(out2, [(TF.fnv32("B"), 0, "B"), (TF.fnv32("pack"), 0, "pack"),
                          (kh[0], 0, "黑暗, 深渊, 冒号:测试"), (kh[1], 0, "Gloom"),
                          (kh[2], 0, "Boom"), (0x99999999, 0, "NEV")])
    cmd2 = [sys.executable, os.path.join(HERE, "audit_canary_pair.py"),
            "-source", src, "-out", out2, "-exp-inst", exp,
            "-m", f"0x{kh[0]:08X}", "-expected-key", f"0x{kh[0]:08X}:黑暗, 深渊, 冒号:测试"]
    r2 = subprocess.run(cmd2, capture_output=True, text=True)
    check("B.新增 key -> no-add 不变式 FAIL (不因 writer 自报 PASS 而放行)",
          "AUDIT=FAIL" in r2.stdout and r2.returncode == 1,
          f"rc={r2.returncode} out={r2.stdout.strip()[-200:]}")


# ============================================================ C
def test_C():
    src = os.path.join(_TMP, "c_src.package")
    out = os.path.join(_TMP, "c_out.package")
    kh = TF.fnv32("Doom")
    TF.make_pkg(src, [(kh, 0, "Doom")], TF.build_xml(["P1"], [kh]))
    make_stbl_only(out, [(kh, 0, "黑暗, 深渊, 冒号:测试")])
    exp = "0x014EACCF17C8B091"
    # C1: 逗号+冒号翻译, 独立 token -> PASS
    cmd = [sys.executable, os.path.join(HERE, "audit_canary_pair.py"),
           "-source", src, "-out", out, "-exp-inst", exp,
           "-m", f"0x{kh:08X}", "-expected-key", f"0x{kh:08X}:黑暗, 深渊, 冒号:测试"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    check("C.逗号+冒号翻译不再崩溃且 AUDIT=PASS",
          "AUDIT=PASS" in r.stdout and r.returncode == 0, f"rc={r.returncode}")
    # C2: malformed token(缺冒号) -> HARD-FAIL rc=2 精确报错
    cmd2 = [sys.executable, os.path.join(HERE, "audit_canary_pair.py"),
            "-source", src, "-out", out, "-exp-inst", exp, "-expected-key", "o world"]
    r2 = subprocess.run(cmd2, capture_output=True, text=True)
    check("C.malformed token -> HARD-FAIL rc=2",
          r2.returncode == 2 and "malformed -expected-key token 'o world'" in r2.stdout,
          f"rc={r2.returncode} out={r2.stdout.strip()}")


test_A()
test_B()
test_C()

shutil.rmtree(_TMP, ignore_errors=True)
print(f"\n==== 结果: PASS={len(PASS)}  FAIL={len(FAIL)} ====")
sys.exit(1 if FAIL else 0)
