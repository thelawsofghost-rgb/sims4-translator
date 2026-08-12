#!/usr/bin/env python3
"""
批量 Pose 结构复核脚本 —— 只认 package 内部真实 XML 结构, 不依据文件名/作者/旧分类。

设计原则:
  1. 只读取 scan_report.csv 中 confidence_level==CONFIRMED_POSE 的 package_path (权威来源)。
  2. 每个包重新读取候选 XML (Snippet / Tuning XML / WW_ANIM_XML), zlib 解压。
  3. 【本次真正落地】UTF-8 解码成功 ≠ XML 合法。必须由真正的 XML parser (xml.etree)
     成功 parse 成元素树, 才视为 "xml_parse_ok"。
  4. 在解析成功的 XML 树里, 用结构性证据判定 Pose, 而非字符串搜索:
       - c="PosePackInstance" (I 元素属性)
       - m="poseplayer" (I 元素属性)
       - s4s_mod_type = POSE_PACK (T 元素 n="s4s_mod_type")
       - pose_list (L 元素 n="pose_list")
       - pose_list 下的 pose entries (pose_name / pose_display_name)
  5. 文件名(Animation/Hair/Pose 等)绝不作为验证证据; 仅做最后人工查看时参考。
  6. 只读, 不翻译, 不写回, 不修改任何 package。

输出:
  - 终端统计 (见需求清单)
  - output/pose_verification.csv
用法:
  python scripts\\verify_poses.py "D:\\sims4_trans\\output"   (output 目录)
"""

import sys, zlib, csv
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from resource_types import RESOURCE_TYPES
from backend import get_backend
from dbpf_fast import safe_parse

# 只读这几类 "XML-ish" 候选 (与 scanner._read_candidate_xmls 一致)
def _is_xml_candidate_type(tid: int) -> bool:
    return (RESOURCE_TYPES.is_snippet(tid)
            or RESOURCE_TYPES.is_tuning_xml(tid)
            or RESOURCE_TYPES.is_known_safely(tid, "WW_ANIM_XML"))


def _read_xml_texts(backend, entries):
    """读取候选 XML 资源并 zlib 解压; 返回 (原始文本列表)。解析合法性在调用方判定。"""
    texts = []
    for e in entries:
        if not _is_xml_candidate_type(e.type_id):
            continue
        data = backend.read_small_resource(e, max_bytes=512 * 1024)
        if not data:
            continue
        if data[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
            try:
                data = zlib.decompress(data)
            except Exception:
                pass
        try:
            texts.append(data.decode("utf-8", errors="ignore"))
        except Exception:
            pass
    return texts


# ---- 结构提取 (在成功 parse 的 XML 树里做) ----
def _iter_elements(root):
    """迭代所有元素 (含根)。"""
    yield root
    yield from root.iter()


def _analyze_parsed_xml(root):
    """对已 parse 的 XML 树, 提取 Pose 结构性证据。返回 dict。"""
    res = {
        "PosePackInstance": False,
        "poseplayer_module": False,
        "POSE_PACK": False,
        "pose_list": False,
        "pose_entry_count": 0,
        "pose_name_count": 0,
        "pose_display_name_count": 0,
    }
    for el in _iter_elements(root):
        tag = el.tag if isinstance(el.tag, str) else str(el.tag)
        attrib = el.attrib

        # c="PosePackInstance" 或 m="poseplayer" (通常在 I 元素, 但放宽到任意元素)
        cval = attrib.get("c", "")
        mval = attrib.get("m", "")
        if cval == "PosePackInstance":
            res["PosePackInstance"] = True
        if mval == "poseplayer":
            res["poseplayer_module"] = True

        # s4s_mod_type = POSE_PACK: <T n="s4s_mod_type">POSE_PACK</T>
        if attrib.get("n") == "s4s_mod_type" and (el.text or "").strip() == "POSE_PACK":
            res["POSE_PACK"] = True

        # pose_list 容器: <L n="pose_list">...</L> (或任意元素 n="pose_list")
        if attrib.get("n") == "pose_list":
            res["pose_list"] = True
            # pose entries: 直接子元素通常是 <U> (一个 pose 一个 U)
            if el.text is None:
                res["pose_entry_count"] += len(list(el))

        # pose 定义字段计数 (整个树范围内)
        if attrib.get("n") == "pose_name":
            res["pose_name_count"] += 1
        if attrib.get("n") == "pose_display_name":
            res["pose_display_name_count"] += 1
    return res


def _verdict(c, parse_ok, any_xml_text):
    """
    依据真实结构给出 verification_status:
      POSE_VERIFIED / POSE_PARTIAL / NOT_POSE / ERROR
    不做"为了让 659 全过而放宽"。
    """
    if not parse_ok:
        return "ERROR", "XML 无法被 xml.etree 解析成功"
    core = c["PosePackInstance"] or c["POSE_PACK"]
    if core and c["pose_list"] and c["pose_entry_count"] > 0:
        return "POSE_VERIFIED", "PosePackInstance/POSE_PACK + pose_list + pose entries 齐全"
    if core and c["pose_list"]:
        return "POSE_PARTIAL", "有 Pose 核心结构 + pose_list, 但未发现 pose entries"
    if core:
        return "POSE_PARTIAL", "有 Pose 核心结构 (PosePackInstance/POSE_PACK), 缺 pose_list/entries"
    if (c["poseplayer_module"] or c["pose_list"] or c["pose_name_count"] > 0):
        return "POSE_PARTIAL", "仅部分 Pose 特征 (poseplayer/pose_list/pose_name), 缺核心结构"
    return "NOT_POSE", "XML 可解析但未发现 Pose Player 结构证据"


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts\\verify_poses.py <output_dir>")
        sys.exit(1)
    out_dir = Path(sys.argv[1])
    report = out_dir / "scan_report.csv"
    if not report.exists():
        report = out_dir / "pose_diagnosis.csv"
        # 仅路径列, 无 confidence_level → 用全部行
        src_mode = "diag"
    else:
        src_mode = "report"
    if not report.exists():
        print(f"找不到 scan_report.csv 或 pose_diagnosis.csv 于 {out_dir}")
        sys.exit(1)

    # 收集 CONFIRMED_POSE 包路径
    pose_paths = []
    with open(report, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            p = (row.get("package_path") or "").strip()
            if not p:
                continue
            if src_mode == "report" and row.get("confidence_level") != "CONFIRMED_POSE":
                continue
            pose_paths.append(Path(p))
    # 去重保序
    seen = set()
    unique = []
    for p in pose_paths:
        if str(p) not in seen:
            seen.add(str(p))
            unique.append(p)
    pose_paths = unique

    total = len(pose_paths)
    print(f"CONFIRMED_POSE 原数量: {total}  (来源: {report.name})")

    agg = {
        "xml_parse_ok": 0, "xml_parse_fail": 0,
        "PosePackInstance": 0, "poseplayer_module": 0, "POSE_PACK": 0,
        "pose_list": 0, "pose_entry_gt0": 0,
        "POSE_VERIFIED": 0, "POSE_PARTIAL": 0, "NOT_POSE": 0, "ERROR": 0,
    }

    rows = []
    for i, p in enumerate(pose_paths, 1):
        rec = {
            "package_path": str(p), "xml_parse_ok": 0,
            "PosePackInstance": 0, "poseplayer_module": 0, "POSE_PACK": 0,
            "pose_list": 0, "pose_entry_count": 0, "pose_name_count": 0,
            "pose_display_name_count": 0,
            "verification_status": "ERROR", "reason": "",
        }
        parse_ok = False
        try:
            if not p.exists():
                rec["reason"] = "文件不存在"
                rows.append(rec)
                agg["ERROR"] += 1
                continue
            idx, err = safe_parse(str(p))
            if err or idx is None:
                rec["reason"] = f"DBPF 解析失败: {err}"
                rows.append(rec)
                agg["ERROR"] += 1
                continue
            backend = get_backend("readonly").open(str(p))
            xml_texts = _read_xml_texts(backend, idx.entries)
            backend.close()

            # 【真正落地】必须由 xml.etree 成功 parse 成元素树
            for txt in xml_texts:
                try:
                    root = ET.fromstring(txt)
                except Exception:
                    continue
                parse_ok = True
                c = _analyze_parsed_xml(root)
                rec.update({
                    "xml_parse_ok": 1,
                    "PosePackInstance": int(c["PosePackInstance"]),
                    "poseplayer_module": int(c["poseplayer_module"]),
                    "POSE_PACK": int(c["POSE_PACK"]),
                    "pose_list": int(c["pose_list"]),
                    "pose_entry_count": c["pose_entry_count"],
                    "pose_name_count": c["pose_name_count"],
                    "pose_display_name_count": c["pose_display_name_count"],
                })
                status, reason = _verdict(c, True, xml_texts)
                rec["verification_status"] = status
                rec["reason"] = reason
                break  # 首个可解析 XML 即判定 (一个姿势包常含一个 Pose XML)
        except Exception as ex:
            rec["reason"] = f"异常: {ex}"

        # 聚合
        if rec["xml_parse_ok"]:
            agg["xml_parse_ok"] += 1
            if rec["PosePackInstance"]: agg["PosePackInstance"] += 1
            if rec["poseplayer_module"]: agg["poseplayer_module"] += 1
            if rec["POSE_PACK"]: agg["POSE_PACK"] += 1
            if rec["pose_list"]: agg["pose_list"] += 1
            if rec["pose_entry_count"] > 0: agg["pose_entry_gt0"] += 1
        else:
            agg["xml_parse_fail"] += 1
        agg[rec["verification_status"]] += 1
        rows.append(rec)

        if i % 100 == 0 or i == total:
            print(f"  进度 {i}/{total} ...")

    # ---- 输出统计 ----
    print("\n================ 批量 Pose 结构复核结果 ================")
    print(f"CONFIRMED_POSE 原数量: {total}")
    print(f"XML成功解析: {agg['xml_parse_ok']}")
    print(f"XML解析失败: {agg['xml_parse_fail']}")
    print(f"明确 PosePackInstance: {agg['PosePackInstance']}")
    print(f"明确 m=\"poseplayer\": {agg['poseplayer_module']}")
    print(f"明确 POSE_PACK: {agg['POSE_PACK']}")
    print(f"有 pose_list: {agg['pose_list']}")
    print(f"有有效 pose entries: {agg['pose_entry_gt0']}")
    print("")
    print(f"强验证通过 (POSE_VERIFIED): {agg['POSE_VERIFIED']}")
    print(f"结构不完整但疑似 Pose (POSE_PARTIAL): {agg['POSE_PARTIAL']}")
    print(f"明确不是 Pose (NOT_POSE): {agg['NOT_POSE']}")
    print(f"无法判断 (ERROR): {agg['ERROR']}")

    # ---- 写 CSV ----
    out_csv = out_dir / "pose_verification.csv"
    cols = ["package_path", "xml_parse_ok", "PosePackInstance", "poseplayer_module",
            "POSE_PACK", "pose_list", "pose_entry_count", "pose_name_count",
            "pose_display_name_count", "verification_status", "reason"]
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})
    print(f"\n已写出: {out_csv}")
    # 顺带落一份只含非 POSE_VERIFIED 的待人工名单 (文件名仅这里出现, 供人工查看)
    suspect = [r for r in rows if r["verification_status"] != "POSE_VERIFIED"]
    if suspect:
        sus_csv = out_dir / "pose_verification_suspects.csv"
        with open(sus_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in suspect:
                w.writerow({k: r.get(k, "") for k in cols})
        print(f"非 POSE_VERIFIED 名单: {len(suspect)} 条 → {sus_csv}")


if __name__ == "__main__":
    main()
