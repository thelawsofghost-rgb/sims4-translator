#!/usr/bin/env python3
"""Phase 2 最终细分 + 完整反向映射补全 (只读, 不写 package)。

原则:
  * NON_SEMANTIC_LABEL (1/2/3/Pose N 等序号型) = 有效显示文本但无语义, 默认不译。真 PLACEHOLDER_NO_DISPLAY 仅指 0x0/无 display name。
  * 去重仅用于减重复, 不丢上下文: 保留 9061 条完整反向映射, 每条可追溯
    package / PosePackInstance / pose_entry / pose_display_name hash / STBL resource instance / locale / source_text。
  * 短文本 (Left/Right/Sit/Stand...) 翻译须带上下文 (所属 Pose Pack + 相邻 pose entries)。
  * 19 个 locale byte 只记录事实, 不猜 zh-CN。正式写中文 STBL 前单独确认 Sims4 中文 locale/resource 标识。

输入: output/pose_text_mapping.csv (9061 MAPPED + 128 PLACEHOLDER + 6 REF_FAIL)
输出:
  output/pose_translation_candidates.csv    (语义文本, 去重去上下文候选表)
  output/pose_reverse_mapping_full.csv      (9061 条完整反向映射, 含 STBL resource instance + locale byte + PosePackInstance)
  终端统计
"""
import sys, csv, re, unicodedata
from pathlib import Path
from collections import Counter, defaultdict
import xml.etree.ElementTree as ET
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse
from backend import get_backend
from resource_types import RESOURCE_TYPES

out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "D:/projects/sims4_trans/output")
mapping_csv = out_dir / "pose_text_mapping.csv"

# ---------------- 文本归一化 (去重键) ----------------
# 同一显示文本在不同 STBL/条目里可能带首尾空白或同义组合/分解字符;
# 候选去重必须按 N*** 并为空白不变的 canonical 键, 否则同一文本会分裂成多个候选行。
def norm_text(s: str) -> str:
    """canonical 去重键: N*** 规范化后去首尾空白 (保留内部空白/大小写/标点)。"""
    return unicodedata.normalize("NFC", (s or "")).strip()

# ---------------- 读取映射 ----------------
rows = []
with open(mapping_csv, encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        rows.append(r)

by_pkg = defaultdict(list)
for r in rows:
    by_pkg[r.get("package_path", "")].append(r)

# ---------------- 工具: 序号/无语义判定 ----------------
_NUM_RE = re.compile(r"^\s*\d+\s*$")                      # 纯数字
_POSEN_RE = re.compile(r"^\s*[Pp]ose\s*[-_]?\s*\d+\s*$")  # Pose 1 / Pose-1 / pose_2
_ORD_RE = re.compile(r"^\s*(1st|2nd|3rd|[0-9]+[thrd]{2})\s*$", re.I)  # 1st 2nd 3rd
_SHORT_NUM = {"top","bottom","front","back","left","right"}  # 独立方向/A 词 —— 虽短但算语义, 需上下文

def classify(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return "PLACEHOLDER_NO_DISPLAY"  # 空/无显示名
    if _NUM_RE.match(t) or _POSEN_RE.match(t) or _ORD_RE.match(t):
        return "NON_SEMANTIC_LABEL"
    return "SEMANTIC_TEXT"

# ---------------- 构建 per-package 的 STBL key → (instance, locale_byte) + PosePackInstance 定位 ----------------
def pkg_index(p):
    """返回 (stbl_map, xml_pose_instances, stbl_inst_by_text_target)
       stbl_map: keyHash(int) -> (instance_id, locale_byte, text)
       xml_pose_instances: list of (xml_instance_id, root_tag) —— 托管 pose_list 的 XML 资源 (PosePackInstance 载体)"""
    idx, err = safe_parse(p)
    if err or not idx:
        return {}, [], {}
    backend = get_backend("readonly").open(p)
    stbl_map = {}
    xml_pose = []
    for e in idx.entries:
        if e.type_id == 0x220557DA:
            try:
                d = backend.read_small_resource(e, max_bytes=2 * 1024 * 1024)
            except Exception:
                continue
            if not d:
                continue
            try:
                m = parse_stbl(d)
            except Exception:
                m = None
            if m:
                lb = (e.instance_id >> 56) & 0xFF
                for k, txt in m.items():
                    stbl_map[k] = (e.instance_id, lb, txt)
        elif (RESOURCE_TYPES.is_snippet(e.type_id)
              or RESOURCE_TYPES.is_tuning_xml(e.type_id)
              or RESOURCE_TYPES.is_known_safely(e.type_id, "WW_ANIM_XML")):
            try:
                d = backend.read_small_resource(e, max_bytes=2 * 1024 * 1024)
            except Exception:
                continue
            if d:
                try:
                    root = ET.fromstring(d)
                except Exception:
                    continue
                # 该 XML 是否含 pose_list
                has_pose = any(el.attrib.get("n") == "pose_list" and el.text is None
                               for el in root.iter())
                if has_pose:
                    xml_pose.append((e.instance_id, root.tag))
    backend.close()
    return stbl_map, xml_pose, {}

def parse_stbl(d: bytes):
    """s4pi v5, zlib-aware: 返回 keyHash(int)->str 或 None"""
    if d[:4] == b"STBL":
        body = d[4:]
    elif d[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        import zlib
        try:
            body = zlib.decompress(d)
        except Exception:
            return None
        if body[:4] != b"STBL":
            # 解压后再带 STBL 头
            body = body
    else:
        return None
    # 定位 STBL 头
    off = body.find(b"STBL")
    if off < 0:
        return None
    body = body[off:]
    if len(body) < 16:
        return None
    ver = int.from_bytes(body[4:6], "little")
    if ver != 5:
        return None
    n = int.from_bytes(body[8:16], "little")
    o = 18
    out = {}
    for _ in range(n):
        if o + 7 > len(body):
            break
        kh = int.from_bytes(body[o:o+4], "little")
        flags = body[o+4]
        ln = int.from_bytes(body[o+5:o+7], "little")
        o += 7
        if o + ln > len(body):
            break
        txt = body[o:o+ln].decode("utf-8", "replace")
        out[kh] = txt
        o += ln
    return out

# ---------------- 主流程: 逐 MAPPED 行补全 + 分类 ----------------
rev_rows = []
sem_texts = Counter()   # semantic source_text -> count
non_sem = Counter()
chinese_any = Counter()

def display_hash_to_int(d):
    if not d:
        return None
    s = d.strip()
    try:
        v = int(s, 16) if s.lower().startswith("0x") else int(s, 0)
        return v & 0xFFFFFFFF
    except ValueError:
        return None

rev_cols = ["package_path", "pose_pack_instance", "pose_entry_idx", "pose_display_name_hash",
            "stbl_resource_instance", "locale_byte", "source_text", "text_class", "pose_name"]

# 上下文(pack instance / stbl instance / entry idx)直接取自上游 pose_text_mapping.csv,
# 上游 map_pose_texts 已成功解析并写入, 不再在此重扫 package (避免重复解析不一致)。
for r in rows:
    if r.get("status") != "MAPPED":
        continue
    p = r.get("package_path", "")
    src = norm_text(r.get("stbl_text"))
    cls = classify(src)
    pp = (r.get("pose_pack_instance") or "").strip()
    inst = (r.get("stbl_resource_instance") or "").strip()
    # 规范化 pack instance 为 0x... : tag 形式 (上游已是 0x..; 无 tag 时补占位)
    pp_norm = ";".join(f"{x.strip()}:p" if ":" not in x else x.strip()
                        for x in pp.split(";") if x.strip()) if pp else ""
    row = {
        "package_path": p,
        "pose_pack_instance": pp_norm,
        "pose_entry_idx": (r.get("pose_entry_idx") or "").strip(),
        "pose_display_name_hash": r.get("display_ref", ""),
        "stbl_resource_instance": inst,
        "locale_byte": (r.get("locale") or "").strip(),
        "source_text": src,
        "text_class": cls,
        "pose_name": (r.get("pose_name") or "").strip(),
    }
    rev_rows.append(row)
    if cls == "SEMANTIC_TEXT":
        sem_texts[src] += 1
    else:
        non_sem[src] += 1
    if cls == "SEMANTIC_TEXT" and re.search(r"[\u4e00-\u9fff]", src):
        chinese_any[src] += 1

# ---------------- 统计 ----------------
mapped_count = len(rev_rows)
sem_count = sum(sem_texts.values())
non_count = sum(non_sem.values())
unique_sem = len(sem_texts)
unique_non = len(non_sem)

placeholders = sum(1 for r in rows if r.get("status") == "PLACEHOLDER_NO_DISPLAY")
reffail = sum(1 for r in rows if r.get("status") == "REF_FAIL")

print("=============== Phase 2 最终细分 ===============")
print(f"mapped_pose_entries            = {mapped_count}")
print(f"unique_source_texts            = {unique_sem + unique_non}")
print(f"  SEMANTIC_TEXT entries        = {sem_count}  (去重候选 {unique_sem})")
print(f"  NON_SEMANTIC_LABEL entries   = {non_count}  (去重候选 {unique_non})")
print(f"PLACEHOLDER_NO_DISPLAY         = {placeholders}  (0x0/无 display name)")
print(f"REF_FAIL                       = {reffail}")
print(f"(含中文的语义文本 unique)        = {len(chinese_any)}  <- 本应为0 (英文包)")

# ---------------- 输出: 完整反向映射 (9061) ----------------
rev_out = out_dir / "pose_reverse_mapping_full.csv"
with open(rev_out, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=rev_cols)
    w.writeheader()
    for row in rev_rows:
        w.writerow({k: row.get(k, "") for k in rev_cols})
print(f"\n完整反向映射已写出: {rev_out}  ({len(rev_rows)} 行)")

# ---- 上下文覆盖诊断: 定位 pose_pack_instance / stbl_resource_instance 在哪层丢失 ----
_n_pp = sum(1 for r in rev_rows if (r.get("pose_pack_instance") or "").strip())
_n_inst = sum(1 for r in rev_rows if (r.get("stbl_resource_instance") or "").strip())
_n_pose_name = sum(1 for r in rev_rows if (r.get("pose_name") or "").strip())
print("[诊断] rev_rows 上下文覆盖:")
print(f"  pose_pack_instance 非空   = {_n_pp}/{len(rev_rows)}")
print(f"  stbl_resource_instance 非空= {_n_inst}/{len(rev_rows)}")
print(f"  pose_name 非空           = {_n_pose_name}/{len(rev_rows)}")
if _n_pp == 0:
    print("  !! rev_rows 层 pose_pack_instance 全空 -> 上游 map_pose_texts 未检出 XML(含 pose_list)的 instance id")
if _n_inst == 0:
    print("  !! rev_rows 层 stbl_resource_instance 全空 -> 上游 map_pose_texts 未写入 STBL instance id")

# ---------------- 输出: 语义文本去重候选表 ----------------
cand_cols = ["source_text", "ref_count", "unique_keys", "sample_package",
             "sample_pose_pack", "sample_stbl_instance", "sample_locale",
             "sample_neighbor_poses", "sample_neighbor_display_texts"]

# 预建: 每个 (package, pose_pack_instance) 组 的 member rows (用于相邻上下文),
#       以 pose_pack_instance 的单条目 id 为单位, 而非整个 ";";" 连接串
# pose_pack_instance 形如 "0x...:tag;0x...:tag" -> 拆成单 id 列表
from collections import defaultdict
pp_members = defaultdict(list)   # (pkg, single_pp_id) -> [row,...]
for row in rev_rows:
    pp = (row.get("pose_pack_instance") or "").strip()
    pkg = row.get("package_path", "")
    if pp:
        for seg in pp.split(";"):
            seg = seg.strip()
            if seg:
                pp_members[(pkg, seg)].append(row)

sem_cand = []
for txt, cnt in sem_texts.most_common():
    occ = [row for row in rev_rows if row["source_text"] == txt]
    # 挑一个“上下文最完整”的出现（优先有 pose_pack_instance 且有 stbl_resource_instance）
    candidate = None
    for row in occ:
        if not (row.get("pose_pack_instance") or "").strip():
            continue
        if candidate is None or not (candidate.get("stbl_resource_instance") or "").strip() \
           and (row.get("stbl_resource_instance") or "").strip():
            candidate = row
    if candidate is None:
        candidate = occ[0] if occ else {k: "" for k in rev_cols}

    sample_pkg = candidate.get("package_path", "") or ""
    sample_pp = candidate.get("pose_pack_instance", "") or ""
    sample_inst = candidate.get("stbl_resource_instance", "") or ""
    sample_locale = candidate.get("locale_byte", "") or ""

    # 相邻姿势上下文 1) 内部 pose_name (供技术定位)
    #               2) 真实玩家显示文字 neighbor_display_texts (同类 source_text, 供翻译消歧)
    neighbor_poses = []
    neighbor_display_texts = []
    pkg = candidate.get("package_path", "") or ""
    for seg in (sample_pp.split(";") if sample_pp else []):
        seg = seg.strip()
        if not seg:
            continue
        for x in pp_members.get((pkg, seg), []):
            pn = (x.get("pose_name") or "").strip()
            dt = (x.get("source_text") or "").strip()
            if pn and pn not in neighbor_poses:
                neighbor_poses.append(pn)
            if dt and dt not in neighbor_display_texts:
                neighbor_display_texts.append(dt)
        if len(neighbor_display_texts) >= 8:
            break
    neighbor_poses = neighbor_poses[:6]
    neighbor_display_texts = neighbor_display_texts[:8]

    nkeys = len({(x.get("package_path"), x.get("pose_display_name_hash")) for x in occ})
    # 包名定为实际 .package 文件 basename; 若单元格意外是 dict(旧输出), 防御性取其中 package_path
    if isinstance(sample_pkg, dict):
        sample_pkg = str(sample_pkg.get("package_path", "") or "")
    pkg_base = str(sample_pkg).replace("\\", "/").split("/")[-1]
    sem_cand.append([txt, cnt, nkeys, pkg_base, sample_pp,
                     sample_inst, sample_locale, "; ".join(neighbor_poses),
                     " | ".join(neighbor_display_texts)])

cand_out = out_dir / "pose_translation_candidates.csv"
with open(cand_out, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(cand_cols)
    for c in sem_cand:
        w.writerow(c)
print(f"语义文本去重候选表已写出: {cand_out}  ({len(sem_cand)} 个唯一语义文本)")

# 短歧义文本单独提示
short = [t for t in sem_texts if t.strip().lower() in
         {"left","right","top","bottom","front","back","sit","stand","up","down","on","off","in","out"}]
print(f"\n⚠ 短/可能歧义文本 (需带上下文): {len(short)} 个 -> {sorted(short)}")
