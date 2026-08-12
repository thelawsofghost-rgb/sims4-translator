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
import sys, csv, re
from pathlib import Path
from collections import Counter, defaultdict
import xml.etree.ElementTree as ET
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse
from backend import get_backend
from resource_types import RESOURCE_TYPES

out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "D:/projects/sims4_trans/output")
mapping_csv = out_dir / "pose_text_mapping.csv"

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

# 预缓存 package 索引
pkg_cache = {}
for r in rows:
    p = r.get("package_path", "")
    if p and p not in pkg_cache:
        pkg_cache[p] = pkg_index(p)

for r in rows:
    if r.get("status") != "MAPPED":
        continue
    p = r.get("package_path", "")
    key = (r.get("stbl_key_hash") or "").strip()
    kh = display_hash_to_int(key) if key else None
    stbl_map, xml_pose, _ = pkg_cache.get(p, ({}, [], {}))
    inst_info = ("", "")
    if kh is not None and kh in stbl_map:
        inst_id, lb, txt = stbl_map[kh]
        inst_info = (f"0x{inst_id:016X}", f"byte_{lb:02X}")
    src = r.get("stbl_text") or ""
    cls = classify(src)
    row = {
        "package_path": p,
        "pose_pack_instance": ";".join(f"0x{i:016X}:{t}" for i, t in xml_pose) or "",
        "pose_entry_idx": "",
        "pose_display_name_hash": r.get("display_ref", ""),
        "stbl_resource_instance": inst_info[0],
        "locale_byte": f"{r.get('locale','')} | min_locale={inst_info[1]}",
        "source_text": src,
        "text_class": cls,
        "pose_name": r.get("pose_name", ""),
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

# ---------------- 输出: 语义文本去重候选表 ----------------
cand_cols = ["source_text", "ref_count", "unique_keys", "sample_package",
             "sample_pose_pack", "sample_stbl_instance", "sample_locale", "sample_neighbor_poses"]
sem_cand = []
for txt, cnt in sem_texts.most_common():
    # 找到该文本的所有出现, 采样首个 package 的上下文
    sample_pkg = ""; sample_pp = ""; sample_inst = ""; sample_locale = ""
    neighbor_poses = []
    for row in rev_rows:
        if row["source_text"] == txt:
            sample_pkg = row["package_path"]; sample_pp = row["pose_pack_instance"]
            sample_inst = row["stbl_resource_instance"]; sample_locale = row["locale_byte"]
            # 同包内与它相邻的 pose_name (上下文)
            same_pkg = [x for x in rev_rows
                        if x["package_path"] == row["package_path"] and row["pose_pack_instance"] and x["pose_pack_instance"] == row["pose_pack_instance"]]
            neighbor_poses = [x["pose_name"] for x in same_pkg][:6]
            break
    nkeys = len({(x["package_path"], x["pose_display_name_hash"]) for x in rev_rows if x["source_text"] == txt})
    sem_cand.append([txt, cnt, nkeys, sample_pkg.split("\\")[-1], sample_pp,
                     sample_inst, sample_locale, "; ".join(neighbor_poses)])

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
