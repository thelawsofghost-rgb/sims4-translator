#!/usr/bin/env python3
"""
Phase 2 — read-only 引用关系映射 (不生成/写回任何汉化 package)。

对 659 个 POSE_VERIFIED 包建立:
    PosePackInstance → pose_list → pose entry → pose_display_name → STBL key → 当前显示文本

铁律:
  * 只读, 修改 package 数量必须 = 0。
  * pose_name 【不默认】视为玩家可见文本; 仅当它能证明用于 UI 显示时才列入翻译目标。
  * 优先只认明确的 pose_display_name / STBL 引用。
  * 记录 STBL 的语言/locale 信息 (为后续"新增/修改中文本地化字符串"做准备, 不默认覆盖英文原文)。
  * 引用断裂 / STBL key 找不到 / 一个 key 对应多个异常结果 → MAPPING_UNCERTAIN, 绝不猜。

STBL v5 权威格式 (s4pi StblResource.cs):
  header : 'STBL'(4) + version(u16=5) + isCompressed(u8) + numEntries(u64) + reserved(2) + stringLength(u32)
  entry  : keyHash(u32) + flags(u8) + length(u16) + UTF-8 bytes(length)
  (STBL 资源体整体可能先被 zlib 压缩)

Locale: Sims 4 惯例 —— locale 编码在 STBL 资源的 instance_id 高位 (不是文件内字段)。
  取 inst_id 高 16 位 (inst >> 48) 作为 Locale 码, 并登记已知映射供人工核对。

用法:
  python scripts\\map_pose_texts.py "D:\\sims4_trans\\output"
输出:
  - output/pose_text_mapping.csv   (每个 pose_display_name 引用一条)
  - output/pose_text_mapping_summary.txt
"""

import sys, zlib, csv, struct
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from resource_types import RESOURCE_TYPES
from backend import get_backend
from dbpf_fast import safe_parse

STBL_TID = 0x220557DA
MAGIC = b"STBL"

# STBL locale 编码 = 64-bit instance_id 的【最高字节】 (bits 56-63)。
# 实测证 (t0nischwartz 包): 同一字符串块存在 0x00/01/02/03/.../0x0B 各 locale 变体,
# 仅最高字节不同, 其余 56 位字符串块 hash 相同。
#
# ⚠️ 语言名映射表: 库内无权威来源, 【不编造】。仅登记已确证项, 其余标 UNKNOWN。
LOCALE_BYTE_KNOWN = {
    # 未确证, 全部留空; 宁可 locales=UNKNOWN 也不放错语言名
}
PLACEHOLDER_KEY = 0x00000000  # 作者未给该姿势设置显示名 (空占位, 非引用失败)


def _is_xml_candidate(tid: int) -> bool:
    return (RESOURCE_TYPES.is_snippet(tid)
            or RESOURCE_TYPES.is_tuning_xml(tid)
            or RESOURCE_TYPES.is_known_safely(tid, "WW_ANIM_XML"))


def read_xml_texts(backend, entries):
    """读取候选 XML 并【要求真正 parse 成功】; 乱码一律丢弃。"""
    texts = []
    for e in entries:
        if not _is_xml_candidate(e.type_id):
            continue
        data = backend.read_small_resource(e, max_bytes=512 * 1024)
        if not data:
            continue
        if data[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
            try:
                data = zlib.decompress(data)
            except Exception:
                pass
        for enc in ("utf-8", "utf-16-le"):
            try:
                raw = data.decode(enc)
                ET.fromstring(raw)
            except Exception:
                continue
            texts.append(raw)
            break
    return texts


def parse_stbl(stbl_bytes: bytes):
    """解析 STBL v5, 返回 {keyHash_uint32: text}。失败返回 None。"""
    if not stbl_bytes:
        return None
    data = stbl_bytes
    if data[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        try:
            data = zlib.decompress(data)
        except Exception:
            pass
    if data[:4] != MAGIC:
        return None
    try:
        off = 4
        version = struct.unpack_from("<H", data, off)[0]; off += 2
        _is_compressed = data[off]; off += 1
        num_entries = struct.unpack_from("<Q", data, off)[0]; off += 8
        off += 2  # reserved
        _str_len = struct.unpack_from("<I", data, off)[0]; off += 4
        if version != 5:
            return None
        result = {}
        for _ in range(num_entries):
            if off + 8 > len(data):
                break
            key_hash = struct.unpack_from("<I", data, off)[0]; off += 4
            _flags = data[off]; off += 1
            length = struct.unpack_from("<H", data, off)[0]; off += 2
            if off + length > len(data):
                break
            txt = data[off:off + length].decode("utf-8", errors="replace")
            off += length
            result[key_hash] = txt
        return result
    except Exception:
        return None


def _xstr(tag, attrib, children):
    """取 <T n=...>text</T> 的文本。"""
    return (children.get(tag) or {}).get(attrib) or ""


def extract_pose_text_mapping(root):
    """
    从 PosePackInstance XML 树提取:
      [ { pose_name, display_key, display_hash } ... ]
    仅收录 pose_list 下的 pose entries 里的 pose_display_name 引用。
    pose_name 仅记录, 不视为翻译目标。
    """
    poses = []
    # 找 pose_list 容器
    for el in [root] + list(root.iter()):
        if el.attrib.get("n") == "pose_list" and el.text is None:
            # 每个子元素(通常 <U>) 是一个 pose entry
            for child in list(el):
                pname = ""
                disp = ""
                for sub in [child] + list(child.iter()):
                    if sub.attrib.get("n") == "pose_name" and sub.text:
                        pname = sub.text.strip()
                    if sub.attrib.get("n") == "pose_display_name" and sub.text:
                        disp = sub.text.strip()
                poses.append({"pose_name": pname, "display": disp})
    return poses


def parse_display_hash(disp: str):
    """把 pose_display_name 的值解析为 u32 hash; 形如 0xF4419F2B 或 0xF4419F2Bxxxxx。返回 int 或 None。"""
    if not disp:
        return None
    s = disp.strip()
    try:
        if s.lower().startswith("0x"):
            v = int(s, 16)
        else:
            v = int(s, 0)
        return v & 0xFFFFFFFF
    except ValueError:
        return None


def locale_of_stbl(inst_id: int):
    """从 STBL instance_id 的最高字节 (bits 56-63) 提取 Locale 码。

    实测证 (t0nischwartz 包 0x004EACCF17C8B091 .. 0x0B4EACCF17C8B091):
      locale 字节 = 最高字节 (0x00,0x01,...,0x0B); 其余 56 位是共享的字符串块 hash。
    之前 (inst>>48)&0xFFFF 取错位 (抓到最高2字节 → locale_0x00C5/0x004E 假值) 已修正。
    语言名映射若无权威来源则不臆测 → locale_known=False。
    """
    if inst_id is None:
        return None, "unknown", False
    byte = (inst_id >> 56) & 0xFF
    name = LOCALE_BYTE_KNOWN.get(byte)
    if name is None:
        return byte, f"locale_byte_0x{byte:02X}", False
    return byte, name, True


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts\\map_pose_texts.py <output_dir>")
        sys.exit(1)
    out_dir = Path(sys.argv[1])
    # 取验证过的最权威路径清单: 从 pose_verification.csv 读 POSE_VERIFIED 包
    veri = out_dir / "pose_verification.csv"
    paths = []
    if veri.exists():
        with open(veri, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if row.get("verification_status") == "POSE_VERIFIED":
                    p = (row.get("package_path") or "").strip()
                    if p:
                        paths.append(Path(p))
    # 去重保序
    seen = set(); unique = []
    for p in paths:
        if str(p) not in seen:
            seen.add(str(p)); unique.append(p)
    paths = unique

    total = len(paths)
    print(f"总 Pose 包: {total}")

    agg = {
        "pose_entries": 0,
        "mapped_ok": 0, "chinese": 0, "english": 0, "empty": 0,
        "placeholder": 0, "ref_fail": 0, "uncertain": 0,
    }
    rows = []
    per_pkg_rows = []

    for i, p in enumerate(paths, 1):
        pkg_pref = f"{p.name}"  # 仅用于打印, 不参与判定
        try:
            if not p.exists():
                per_pkg_rows.append({"package_path": str(p), "status": "ERROR", "reason": "文件不存在",
                                     "pose_entries": 0, "mapped_ok": 0, "chinese": 0, "english": 0,
                                     "empty": 0, "ref_fail": 0, "uncertain": 0})
                agg["uncertain"] += 1
                continue
            idx, err = safe_parse(str(p))
            if err or idx is None:
                per_pkg_rows.append({"package_path": str(p), "status": "ERROR", "reason": f"DBPF解析失败:{err}",
                                     "pose_entries": 0, "mapped_ok": 0, "chinese": 0, "english": 0,
                                     "empty": 0, "ref_fail": 0, "uncertain": 0})
                agg["uncertain"] += 1
                continue
            backend = get_backend("readonly").open(str(p))

            # 1) 读 XML, 提取 pose entries
            xml_texts = read_xml_texts(backend, idx.entries)
            poses = []
            for txt in xml_texts:
                try:
                    root = ET.fromstring(txt)
                except Exception:
                    continue
                poses += extract_pose_text_mapping(root)

            # 2) 读所有 STBL → keyHash→text 映射 (+ locale)
            stbl_map = {}
            locales = set()
            for e in idx.entries:
                if e.type_id == STBL_TID:
                    data = backend.read_small_resource(e, max_bytes=2 * 1024 * 1024)
                    parsed = parse_stbl(data) if data else None
                    if parsed is not None:
                        stbl_map.update(parsed)
                        byte, name, known = locale_of_stbl(e.instance_id)
                        locales.add(f"{name}(byte0x{byte:02X})" if byte is not None else name)
            backend.close()

            # 3) 逐 pose entry 映射
            pkg_plots = {"mapped_ok":0,"chinese":0,"english":0,"empty":0,"placeholder":0,"ref_fail":0,"uncertain":0}
            pkg_pose_entries = len(poses)
            agg["pose_entries"] += pkg_pose_entries

            for pose in poses:
                disp = pose.get("display") or ""
                ph = parse_display_hash(disp)
                row = {
                    "package_path": str(p),
                    "pose_name": pose.get("pose_name") or "",
                    "display_ref": disp,
                    "stbl_key_hash": f"0x{ph:08X}" if ph is not None else "",
                    "locale": ";".join(sorted(locales)) if locales else "",
                }
                if not disp:
                    row.update({"status": "EMPTY", "reason": "pose_display_name 为空"})
                    agg["empty"] += 1; pkg_plots["empty"] += 1
                elif ph is None:
                    row.update({"status": "REF_FAIL", "reason": f"display_ref 非 hash 无法解析: {disp!r}"})
                    agg["ref_fail"] += 1; pkg_plots["ref_fail"] += 1
                elif ph == PLACEHOLDER_KEY:
                    # 作者显式填 0x0 (未设置显示名): 不是引用失败, 也不是待翻文本
                    row.update({"status": "PLACEHOLDER_NO_DISPLAY",
                                "reason": "display_ref=0x0 作者未设置该姿势的显示名 (非待翻文本)"})
                    agg["placeholder"] += 1; pkg_plots["placeholder"] += 1
                else:
                    hits = [t for h, t in stbl_map.items() if h == ph]
                    if len(hits) == 1:
                        txt = hits[0]
                        row.update({"status": "MAPPED", "reason": "",
                                    "stbl_text": txt, "text_intent": "CHINESE" if _is_chinese(txt) else "ENGLISH"})
                        agg["mapped_ok"] += 1; pkg_plots["mapped_ok"] += 1
                        if _is_chinese(txt): agg["chinese"] += 1; pkg_plots["chinese"] += 1
                        else: agg["english"] += 1; pkg_plots["english"] += 1
                    elif len(hits) == 0:
                        row.update({"status": "REF_FAIL", "reason": f"STBL key 0x{ph:08X} 未找到"})
                        agg["ref_fail"] += 1; pkg_plots["ref_fail"] += 1
                    else:
                        row.update({"status": "MAPPING_UNCERTAIN",
                                    "reason": f"一个 key 0x{ph:08X} 对应 {len(hits)} 个文本: {hits!r}",
                                    "stbl_text": ";".join(hits)})
                        agg["uncertain"] += 1; pkg_plots["uncertain"] += 1
                rows.append(row)

            per_pkg_rows.append({
                "package_path": str(p), "status": "OK",
                "reason": "", "pose_entries": pkg_pose_entries,
                "mapped_ok": pkg_plots["mapped_ok"], "chinese": pkg_plots["chinese"],
                "english": pkg_plots["english"], "empty": pkg_plots["empty"],
                "placeholder": pkg_plots["placeholder"],
                "ref_fail": pkg_plots["ref_fail"], "uncertain": pkg_plots["uncertain"],
            })
        except Exception as ex:
            per_pkg_rows.append({"package_path": str(p), "status": "ERROR", "reason": f"异常:{ex}",
                                 "pose_entries": 0, "mapped_ok": 0, "chinese": 0, "english": 0,
                                 "empty": 0, "placeholder": 0, "ref_fail": 0, "uncertain": 0})
            agg["uncertain"] += 1
        if i % 150 == 0 or i == total:
            print(f"  进度 {i}/{total} ...")

    # ---- 输出 ----
    print("\n================ Pose 引用关系映射结果 ================")
    print(f"总 Pose 包: {total}")
    print(f"Pose entries 总数: {agg['pose_entries']}")
    print(f"成功映射到显示文本: {agg['mapped_ok']}")
    print(f"  原本中文: {agg['chinese']}")
    print(f"  英文: {agg['english']}")
    print(f"空名称: {agg['empty']} (纯空字符串)")
    print(f"PLACEHOLDER_NO_DISPLAY (display=0x0 作者未设名): {agg['placeholder']}")
    print(f"引用失败: {agg['ref_fail']}")
    print(f"MAPPING_UNCERTAIN: {agg['uncertain']}")

    # 每个 package 详细 CSV (pose 级)
    cols = ["package_path", "pose_name", "display_ref", "stbl_key_hash",
            "stbl_text", "text_intent", "locale", "status", "reason"]
    out_csv = out_dir / "pose_text_mapping.csv"
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})
    print(f"\npose 级映射已写出: {out_csv}  ({len(rows)} 行)")

    # 每个 package 汇总 CSV
    pcols = ["package_path", "status", "reason", "pose_entries", "mapped_ok",
             "chinese", "english", "empty", "placeholder", "ref_fail", "uncertain"]
    ppath = out_dir / "pose_text_mapping_per_package.csv"
    with open(ppath, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=pcols)
        w.writeheader()
        for r in per_pkg_rows:
            w.writerow({k: r.get(k, "") for k in pcols})
    print(f"package 级汇总已写出: {ppath}  ({len(per_pkg_rows)} 行)")

    # 汇总文本
    sum_path = out_dir / "pose_text_mapping_summary.txt"
    with open(sum_path, "w", encoding="utf-8") as f:
        f.write("========== Phase 2 引用关系映射 (只读, 不写回) ==========\n\n")
        f.write(f"总 Pose 包: {total}\n")
        f.write(f"Pose entries 总数: {agg['pose_entries']}\n")
        f.write(f"成功映射到显示文本: {agg['mapped_ok']}\n")
        f.write(f"  原本中文: {agg['chinese']}\n")
        f.write(f"  英文: {agg['english']}\n")
        f.write(f"空名称(纯空): {agg['empty']}\n")
        f.write(f"PLACEHOLDER_NO_DISPLAY (display=0x0 作者未设名): {agg['placeholder']}\n")
        f.write(f"引用失败: {agg['ref_fail']}\n")
        f.write(f"MAPPING_UNCERTAIN: {agg['uncertain']}\n\n")
        f.write("* pose_name 不默认视为玩家可见文本; 仅沿 pose_display_name → STBL 引用翻译\n")
        f.write("* 修改 package 数量 = 0 (本项目只读)\n")
    print(f"汇总: {sum_path}")


def _is_chinese(text: str) -> bool:
    if not text:
        return False
    cn = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')
    return cn / max(1, len(text)) > 0.3


if __name__ == "__main__":
    main()
