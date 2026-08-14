#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pose_coverage.py — 659 CONFIRMED_POSE 只读 coverage 扫描 + 代表性 10-cohort 选择
================================================================================

本脚本对【真实 package】做覆盖扫描。真实的 659 包位于 Windows 生产机, 因此本脚本
在 Windows 上直接运行 (python scripts\\pose_coverage.py), 对真实包只读解析:
  * 不生成任何 sidecar
  * 不写 Mods
  * 不改动任何 .package (只读)
  * 不触碰 Animation / 不批 10/50/659 / 不处理缺 CHS 创建规则
  * 不改变 frozen 9061 pose action translation layer

输入 (优先级):
  1) --list <file>    明文 package 路径列表 (一行一个), 或
  2) 默认读取 D:/projects/sims4_trans/output/pose_verification.csv
     过滤 verification_status == "POSE_VERIFIED" (即 659 包清单)

输出 (写在工作目录 output/ 下, 若不存在则创建):
  coverage.csv          每包一行, 全字段
  coverage_report.md    汇总 + 各 status 数量 + cohort 名单与理由
  cohort_selection.csv  10-cohort 选择明细 (why / TGI / entries / 分类计数 ...)

状态分类 (status):
  ELIGIBLE_EXISTING_CHS   存在 0x01 CHS 目标 STBL 且 TGI 唯一可写
  SKIP_NO_CHS             缺 0x01 CHS (不自行创建)
  SKIP_AMBIGUOUS_TGI      CHS 目标 STBL >1 或多 target family, 无法唯一确定
  SKIP_MAPPING_UNCERTAIN  无结构证据 / pack 级字段引不到 STBL
  ERROR                   DBPF 解析失败等异常

铁律:
  * 不根据 STBL 文本“看起来像英文”判 TRANSLATE; 只认 XML 结构引用。
  * player-visible model:
      PACK_TITLE        <- display_name / pack_title / title (显示类)
      PACK_DESCRIPTION  <- description (非0且结构引用)
      POSE_DISPLAY_NAME <- pose_display_name
      KEEP              <- creator_name / creator / author 等
      非UI/默认不翻:     pose_name / sort_name / internal IDs
"""
import sys, os, zlib, csv, struct
from pathlib import Path
from collections import Counter, defaultdict
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse
from backend import get_backend
from resource_types import RESOURCE_TYPES

STBL_TID = 0x220557DA
MAGIC = b"STBL"
LOCALE_CHS = 0x01
DEFAULT_VERI = r"D:/projects/sims4_trans/output/pose_verification.csv"

# 玩家可见字段模型 (语义保守, 只认名字明确的)
DISPLAY_HINTS = ("display_name", "display", "pack_title", "title", "modal_name",
                 "ui_name", "tooltip", "label", "description")
AUTHOR_HINTS = ("author", "creator", "creator_name", "by_line", "pose_name",
                "internal_name", "clip_name", "animation_name", "raw_display",
                "file_name", "package_name", "id", "unique_id", "key", "version",
                "category", "tags", "tag", "sort_name")
# 明确记为用户可见字段 (用于 ref_count 精确计数)
PV_PACK_TITLE = ("display_name", "pack_title", "title", "modal_name", "ui_name", "label")
PV_PACK_DESC = ("description",)
PV_POSE_DISPLAY = ("pose_display_name",)

LONG_STRING_LEN = 200  # 长字符串阈值 (UTF-16 units 约为 bytes/2; 用字符数判)


# ---------------------------------------------------------------- 工具
def _is_xml_candidate(tid: int) -> bool:
    return (RESOURCE_TYPES.is_snippet(tid)
            or RESOURCE_TYPES.is_tuning_xml(tid)
            or RESOURCE_TYPES.is_known_safely(tid, "WW_ANIM_XML"))


def _decompress(data: bytes) -> bytes:
    if data[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        try:
            return zlib.decompress(data)
        except Exception:
            return data
    return data


def parse_stbl(data: bytes):
    """解析 STBL v5, 返回 (version, is_compressed, [(keyHash, flags, text)])。失败返回 None。"""
    if not data:
        return None
    data = _decompress(data)
    if data[:4] != MAGIC:
        return None
    try:
        off = 4
        version = struct.unpack_from("<H", data, off)[0]; off += 2
        comp = data[off]; off += 1
        num = struct.unpack_from("<Q", data, off)[0]; off += 8
        reserved = data[off:off + 2]; off += 2
        _sl = struct.unpack_from("<I", data, off)[0]; off += 4
        if version != 5:
            return None
        out = []
        for _ in range(num):
            if off + 8 > len(data):
                break
            kh = struct.unpack_from("<I", data, off)[0]; off += 4
            fl = data[off]; off += 1
            ln = struct.unpack_from("<H", data, off)[0]; off += 2
            if off + ln > len(data):
                break
            txt = data[off:off + ln].decode("utf-8", errors="replace")
            off += ln
            out.append((kh, fl, txt))
        return (version, comp, out)
    except Exception:
        return None


def read_xml_payloads(backend, entries):
    """返回 [(instance_id, root_et, raw)] —— 真能 ET.parse 的才算。"""
    out = []
    for e in entries:
        if not _is_xml_candidate(e.type_id):
            continue
        try:
            data = backend.read_small_resource(e, max_bytes=2 * 1024 * 1024)
        except Exception:
            continue
        if not data:
            continue
        data = _decompress(data)
        for enc in ("utf-8", "utf-16-le"):
            try:
                raw = data.decode(enc)
                root = ET.fromstring(raw)
            except Exception:
                continue
            out.append((getattr(e, "instance_id", 0), root, raw))
            break
    return out


def _classify_field(fname):
    fl = (fname or "").lower()
    if any(h in fl for h in DISPLAY_HINTS):
        return "DISPLAY"
    if any(h in fl for h in AUTHOR_HINTS):
        return "AUTHORISH"
    return "OTHER"


def is_hash_like(s):
    s = (s or "").strip()
    if not s:
        return False
    try:
        v = int(s, 16) if s.lower().startswith("0x") else int(s, 0)
        return True
    except ValueError:
        return False


def parse_display_hash(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return int(s, 16) if s.lower().startswith("0x") else int(s, 0)
    except ValueError:
        return None


def is_pose_pack_root(root):
    """PosePackInstance 判定: 根元素名或其 c= 属性含 pose_pack / posepackinstance, 或树内含 pose_list。"""
    tag = (root.tag or "").lower()
    cattr = ((root.attrib.get("c") or root.attrib.get("class") or "").lower()
             .replace("_", "").replace(" ", ""))
    tagn = tag.replace("_", "").replace(" ", "")
    if "pose_pack" in tag or "posepack" in tag or "posepack" in cattr:
        return True
    # 兜底: 树内存在 pose_list 节点
    for el in root.iter():
        n = (el.attrib.get("n") or "").lower()
        if n == "pose_list":
            return True
    return False


# ---------------------------------------------------------------- 单包扫描
def scan_package(path: str) -> dict:
    row = {
        "package_path": path,
        "file_size": 0,
        "PosePackInstance_count": 0,
        "STBL_count_total": 0,
        "CHS_0x01_exists": 0,
        "CHS_target_STBL_count": 0,
        "CHS_target_TGI(s)": "",
        "CHS_entry_count": 0,
        "pack_title_ref_count": 0,
        "pack_description_ref_count": 0,
        "pose_display_name_ref_count": 0,
        "exact_structural_translate_count": 0,
        "keep_count": 0,
        "unmapped_uncertain_count": 0,
        "STBL_version": "",
        "compression_state": "",
        "non_ascii_source_present": 0,
        "long_string_present": 0,
        "repeated_source_text_present": 0,
        "multiple_target_STBL_families": 0,
        "status": "ERROR",
        "reason": "",
    }

    idx, err = safe_parse(path)
    if err or idx is None:
        row["reason"] = f"DBPF 解析失败: {err}"
        return row
    try:
        row["file_size"] = os.path.getsize(path)
    except Exception:
        pass

    backend = get_backend("readonly").open(path)

    # ---- STBL 汇总 ----
    stbl_entries = [e for e in idx.entries if e.type_id == STBL_TID]
    row["STBL_count_total"] = len(stbl_entries)
    stbl_parsed = {}   # inst_id -> (version, comp, [(kh,fl,txt)])
    versions = set()
    compstates = set()
    stbl_inst = []     # (inst_id, version, comp, n_entries)
    for e in stbl_entries:
        try:
            data = backend.read_small_resource(e, max_bytes=2 * 1024 * 1024)
        except Exception:
            data = None
        pr = parse_stbl(data) if data else None
        if pr:
            ver, comp, kvs = pr
            stbl_parsed[e.instance_id] = (ver, comp, kvs)
            stbl_inst.append((e.instance_id, ver, comp, len(kvs)))
            versions.add(ver); compstates.add(comp)
    row["STBL_version"] = ",".join(str(v) for v in sorted(versions))
    row["compression_state"] = "COMPRESSED" if (1 in compstates or 0x5A42 in compstates) else ("UNCOMPRESSED" if compstates else "NONE")

    # 压缩态判别: comp 字段非 0 或资源 offset 高位压缩标记
    compressed_pkg = any(e.is_compressed for e in stbl_entries)
    if compressed_pkg:
        row["compression_state"] = "COMPRESSED"

    # ---- CHS 目标: locale 顶层字节 >>56 == 0x01 ----
    chs = [(i, v, c, kvs) for i, (v, c, kvs) in stbl_parsed.items() if ((i >> 56) & 0xFF) == LOCALE_CHS]
    row["CHS_0x01_exists"] = 1 if chs else 0
    row["CHS_target_STBL_count"] = len(chs)
    if chs:
        row["CHS_target_TGI(s)"] = ";".join(
            f"0x220557DA/0x80000000/0x{i:016X}" for i, *_ in chs)
        row["CHS_entry_count"] = sum(len(kvs) for *_ , kvs in chs)
        # 多 target family: 高 32 位(<locale 字节>)不同 -> 多 family
        fams = {(i >> 32) & 0xFFFFFFFF for i, *_ in chs}
        row["multiple_target_STBL_families"] = 1 if len(fams) > 1 else 0

    # ---- 所有 STBL key 池 (用于结构引用 join) ----
    stbl_keys = {}     # kh -> (flags, text)
    stbl_insts = {}    # kh -> [inst_id...]
    for i, (ver, comp, kvs) in stbl_parsed.items():
        for kh, fl, txt in kvs:
            stbl_keys[kh] = (fl, txt)
            stbl_insts.setdefault(kh, []).append(i)

    # ---- XML 结构引用 (PosePackInstance 及其它 tuning) ----
    xmls = read_xml_payloads(backend, idx.entries)
    posexmls = [x for x in xmls if is_pose_pack_root(x[1])]
    row["PosePackInstance_count"] = len(posexmls)

    struct_ref = {}    # kh -> [(field, cls, inst_id)]
    ref_pv_packtitle = 0
    ref_pv_packdesc = 0
    ref_pv_posedisplay = 0
    alltexts = []      # 全部 STBL 文本, 用于 repeated / non_ascii / long
    for xinst_id, root, raw in xmls:
        for el in root.iter():
            n = el.attrib.get("n")
            if not n:
                continue
            has_children = any(True for _ in el)
            if has_children and el.text is None:
                continue
            val = (el.text or "").strip()
            if not val:
                continue
            cls = _classify_field(n)
            h = parse_display_hash(val) if is_hash_like(val) else None
            nl = n.lower()
            # 精确字段名计数: 用 == 而非 substring, 避免 pose_display_name 误吞 display_name
            if nl in PV_PACK_TITLE: ref_pv_packtitle += 1
            if nl in PV_PACK_DESC: ref_pv_packdesc += 1
            if nl in PV_POSE_DISPLAY: ref_pv_posedisplay += 1
            if h is not None:
                struct_ref.setdefault(h, []).append((n, cls, xinst_id))
    row["pack_title_ref_count"] = ref_pv_packtitle
    row["pack_description_ref_count"] = ref_pv_packdesc
    row["pose_display_name_ref_count"] = ref_pv_posedisplay

    # ---- 逐 key 判定 (结构证据) ----
    translate = keep = unmapped = 0
    for kh, (fl, txt) in stbl_keys.items():
        refs = struct_ref.get(kh, [])
        if not refs:
            unmapped += 1
            continue
        disp = [r for r in refs if r[1] == "DISPLAY"]
        auth = [r for r in refs if r[1] == "AUTHORISH"]
        if disp:
            translate += 1
        elif auth:
            keep += 1
        else:
            unmapped += 1
        alltexts.append(txt)
    row["exact_structural_translate_count"] = translate
    row["keep_count"] = keep
    row["unmapped_uncertain_count"] = unmapped

    # ---- 文本特征 ----
    non_ascii = any(any(ord(ch) > 127 for ch in t) for t in alltexts)
    long_str = any(len(t) >= LONG_STRING_LEN for t in alltexts)
    cnt = Counter(t for t in alltexts if t.strip())
    repeated = any(n > 1 for n in cnt.values())
    row["non_ascii_source_present"] = 1 if non_ascii else 0
    row["long_string_present"] = 1 if long_str else 0
    row["repeated_source_text_present"] = 1 if repeated else 0

    backend.close()

    # ---- 状态分类 ----
    row = _classify(row)
    return row


def _classify(row: dict) -> dict:
    if row["CHS_0x01_exists"] == 0:
        row["status"] = "SKIP_NO_CHS"
        row["reason"] = "缺 0x01 CHS (不自行创建, 不推导新 Instance)"
        return row
    if row["CHS_target_STBL_count"] != 1:
        row["status"] = "SKIP_AMBIGUOUS_TGI"
        row["reason"] = f"CHS 目标 STBL 数 {row['CHS_target_STBL_count']} != 1 (多 target / 多 family)"
        return row
    if row["exact_structural_translate_count"] == 0 and row["keep_count"] == 0:
        row["status"] = "SKIP_MAPPING_UNCERTAIN"
        row["reason"] = "无任何结构证据引用 STBL key (全部 UNMAPPED)"
        return row
    row["status"] = "ELIGIBLE_EXISTING_CHS"
    row["reason"] = "存在唯一 0x01 CHS 目标且结构映射可精确 join"
    return row


# ---------------------------------------------------------------- 输入清单
def load_packages(list_path=None) -> list:
    if list_path:
        with open(list_path, encoding="utf-8-sig") as f:
            return [ln.strip() for ln in f if ln.strip()]
    if not os.path.exists(DEFAULT_VERI):
        print(f"[ERROR] 默认清单不存在: {DEFAULT_VERI}")
        print("  请用 --list <文件> 提供 package 路径列表 (一行一个)")
        sys.exit(2)
    paths = []
    with open(DEFAULT_VERI, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("verification_status") == "POSE_VERIFIED":
                p = (r.get("package_path") or "").strip()
                if p:
                    paths.append(p)
    return paths


# ---------------------------------------------------------------- cohort
def pick_cohort(rows):
    """按类别覆盖程序化挑选代表性 10 包 (确定性 tie-break, 非随机/非人工名字)。

    返回: [(slot, why, row), ...]   slot 为 1..N; 某类缺则标 NOT_PRESENT_IN_CORPUS。
    """
    elig = [r for r in rows if r["status"] == "ELIGIBLE_EXISTING_CHS"]
    eligible = set(r["package_path"] for r in elig)

    def pick(fn, eligible_set):
        cand = [r for r in rows if r["package_path"] in eligible_set and fn(r)]
        if not cand:
            return None
        # 确定性: 文件路径字典序最小
        return min(cand, key=lambda r: r["package_path"])

    picks = []          # (slot, why, row|None)
    used = set()

    def place(slot, why, r):
        picks.append((slot, why, r))
        if r is not None:
            used.add(r["package_path"])

    # 1) 最小 CHS STBL / entry 最少
    if elig:
        r = min(elig, key=lambda r: (r["CHS_entry_count"], r["package_path"]))
        place(1, "最小 CHS STBL / entry 最少", r)
    else:
        place(1, "最小 CHS STBL", None)

    # 2) 最大 CHS STBL / entry 很多
    if elig:
        r = max(elig, key=lambda r: (r["CHS_entry_count"], r["package_path"]))
        place(2, "最大 CHS STBL / entry 很多", r)
    else:
        place(2, "最大 CHS STBL", None)

    # 3) 普通中等规模 (中位 entry 数, 未被选)
    if elig:
        mid = sorted(r["CHS_entry_count"] for r in elig)
        med = mid[len(mid) // 2]
        r = min((r for r in elig if r["package_path"] not in used),
                key=lambda r: (abs(r["CHS_entry_count"] - med), r["package_path"]))
        place(3, "普通中等规模 (接近 median entry)", r)
    else:
        place(3, "普通中等规模", None)

    # 4) 多个 PosePackInstance
    r = pick(lambda r: r["PosePackInstance_count"] > 1, eligible - used)
    if r: place(4, "多个 PosePackInstance", r)
    else: place(4, "多个 PosePackInstance", None)

    # 5) 多个 STBL family / 多 target STBL
    r = pick(lambda r: r["multiple_target_STBL_families"] or r["CHS_target_STBL_count"] > 1,
             eligible - used)
    if r: place(5, "多个 STBL family / 多 target STBL", r)
    else: place(5, "多个 STBL family", None)

    # 6) pack title + description 都存在
    r = pick(lambda r: r["pack_title_ref_count"] > 0 and r["pack_description_ref_count"] > 0,
             eligible - used)
    if r: place(6, "pack title + description 都存在", r)
    else: place(6, "pack title + description", None)

    # 7) 只有 pose_display_name, 无 description
    r = pick(lambda r: r["pose_display_name_ref_count"] > 0 and r["pack_description_ref_count"] == 0,
             eligible - used)
    if r: place(7, "只有 pose_display_name, 无 description", r)
    else: place(7, "只有 pose_display_name", None)

    # 8) source 含非 ASCII / 特殊字符
    r = pick(lambda r: r["non_ascii_source_present"], eligible - used)
    if r: place(8, "source 含非 ASCII / 特殊字符", r)
    else: place(8, "source 非 ASCII", None)

    # 9) 长字符串
    r = pick(lambda r: r["long_string_present"], eligible - used)
    if r: place(9, "长字符串", r)
    else: place(9, "长字符串", None)

    # 10) repeated source / protected token 较多
    r = pick(lambda r: r["repeated_source_text_present"], eligible - used)
    if r: place(10, "repeated source / protected token", r)
    else: place(10, "repeated source", None)

    # 附加: compressed STBL/package (若 corpus 存在则优先补 1)
    compressed_exist = any("COMPRESSED" in r["compression_state"] for r in rows)
    if compressed_exist:
        r = pick(lambda r: "COMPRESSED" in r["compression_state"], eligible - used)
        if r:
            picks.append((11, "compressed STBL/package (corpus 存在, 优先纳入)", r))

    return picks


# ---------------------------------------------------------------- main
def main():
    list_path = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--list":
            list_path = args[i + 1]; i += 1
        i += 1
    if list_path and not os.path.exists(list_path):
        print(f"[ERROR] --list 文件不存在: {list_path}")
        return 2

    paths = load_packages(list_path)
    if not paths:
        print("[ERROR] 包清单为空")
        return 2
    print(f"待扫描包数: {len(paths)}")

    rows = []
    for idx, p in enumerate(paths, 1):
        try:
            r = scan_package(p)
        except Exception as ex:
            r = {"package_path": p, "status": "ERROR", "reason": f"扫描异常: {ex}"}
            for c in _COLS:
                r.setdefault(c, 0 if c not in ("STBL_version", "compression_state",
                                               "CHS_target_TGI(s)", "reason",
                                               "package_path", "status") else "")
            r["package_path"] = p; r["status"] = "ERROR"
        rows.append(r)
        if idx % 100 == 0:
            print(f"  {idx}/{len(paths)} ...")

    out_dir = Path(os.getcwd()) / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    cov_csv = out_dir / "coverage.csv"
    cols = _COLS
    with open(cov_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    print(f"\ncoverage: {cov_csv} ({len(rows)} 行)")

    # ---- status 汇总 ----
    sc = Counter(r["status"] for r in rows)
    print("\n--- status 数量 ---")
    for s in ("ELIGIBLE_EXISTING_CHS", "SKIP_NO_CHS", "SKIP_AMBIGUOUS_TGI",
              "SKIP_MAPPING_UNCERTAIN", "ERROR"):
        print(f"  {s}: {sc.get(s, 0)}")

    # ---- cohort ----
    picks = pick_cohort(rows)
    coh_csv = out_dir / "cohort_selection.csv"
    with open(coh_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["slot", "why", "package_path", "CHS_target_TGI", "CHS_entry_count",
                    "PosePackInstance_count", "structural_TRANSLATE", "KEEP", "UNMAPPED",
                    "pack_title_ref", "pack_description_ref", "pose_display_name_ref",
                    "STBL_version", "compression_state", "status"])
        for slot, why, r in picks:
            if r is None:
                w.writerow([slot, why + " = NOT_PRESENT_IN_CORPUS", "N/A", "", "", "", "", "", "", "", "", "", "", "", ""])
            else:
                w.writerow([slot, why, r["package_path"], r["CHS_target_TGI(s)"],
                            r["CHS_entry_count"], r["PosePackInstance_count"],
                            r["exact_structural_translate_count"], r["keep_count"],
                            r["unmapped_uncertain_count"],
                            r["pack_title_ref_count"], r["pack_description_ref_count"],
                            r["pose_display_name_ref_count"],
                            r["STBL_version"], r["compression_state"], r["status"]])
    print(f"cohort: {coh_csv} ({len(picks)} 行)")

    # ---- markdown report ----
    rep = out_dir / "coverage_report.md"
    with open(rep, "w", encoding="utf-8") as f:
        f.write("# 659 CONFIRMED_POSE coverage 报告 (只读)\n\n")
        f.write(f"扫描包数: {len(rows)}\n\n## status 数量\n\n")
        for s in ("ELIGIBLE_EXISTING_CHS", "SKIP_NO_CHS", "SKIP_AMBIGUOUS_TGI",
                  "SKIP_MAPPING_UNCERTAIN", "ERROR"):
            f.write(f"- {s}: {sc.get(s, 0)}\n")
        f.write("\n## 代表性 10-cohort (程序化选择, 非随机/非人工)\n\n")
        f.write("| slot | why | package | CHS TGI | CHS entries | TRANSLATE | KEEP | UNMAPPED |\n")
        f.write("|---|---|---|---|---|---|---|---|\n")
        for slot, why, r in picks:
            if r is None:
                f.write(f"| {slot} | {why} **NOT_PRESENT_IN_CORPUS** | - | - | - | - | - | - |\n")
            else:
                f.write(f"| {slot} | {why} | {Path(r['package_path']).name} | "
                        f"{r['CHS_target_TGI(s)']} | {r['CHS_entry_count']} | "
                        f"{r['exact_structural_translate_count']} | {r['keep_count']} | "
                        f"{r['unmapped_uncertain_count']} |\n")
        # compressed 存在性记录
        any_comp = any("COMPRESSED" in r["compression_state"] for r in rows)
        f.write(f"\ncompressed STBL/package 在 659 中: "
                f"{'存在 (已优先纳入 cohort)' if any_comp else 'NOT_PRESENT_IN_CORPUS'}\n")
    print(f"report: {rep}")
    return 0


_COLS = ["package_path", "file_size", "PosePackInstance_count", "STBL_count_total",
         "CHS_0x01_exists", "CHS_target_STBL_count", "CHS_target_TGI(s)", "CHS_entry_count",
         "pack_title_ref_count", "pack_description_ref_count", "pose_display_name_ref_count",
         "exact_structural_translate_count", "keep_count", "unmapped_uncertain_count",
         "STBL_version", "compression_state",
         "non_ascii_source_present", "long_string_present", "repeated_source_text_present",
         "multiple_target_STBL_families", "status", "reason"]


if __name__ == "__main__":
    sys.exit(main())
