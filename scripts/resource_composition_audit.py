#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
resource_composition_audit.py — 只读 DBPF 资源构成 root-cause 审计
========================================================================
目的
====
G-O/G-S gate 对 known functional-object 样本 (Kritical Brainwashing Machine)
出现 FALSE NEGATIVE (verdict=STANDALONE_POSE_PACK)。本工具从 DBPF 层枚举
【全部】resources (不只当前 XML roots), 回答: Kritical 相比真正 Pose Pack
到底多了什么 package-level 正向「功能物品/交互」证据, 或当前 parser 漏看了什么。

重点区分三种假设:
  1) 功能证据存在, 但位于当前未扫描的 resource type
  2) 功能证据在 XML/tuning 内, 但 current detector 没识别
  3) Kritical 的物品本体其实由其它 package 提供, 本包只是 companion pose package

允许 (无作者黑名单 / 无文件名特判 / 不调 pose_display_refs 阈值):
  逐资源枚举 type/group/instance/count/compression/parser 语义类型
  XML/tuning 资源额外输出 root tag/class + 相关结构引用 (object/interaction 等)

只读: 不写 .package / 不调模型 / 不改 coverage / 448 / classifier。

用法:
  python scripts/resource_composition_audit.py <p1.package> [p2.package ...] \
      [--out output/resource_composition_audit.csv]
"""
import sys, os, csv, argparse, zlib
from pathlib import Path
from collections import Counter, defaultdict
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse
from backend import get_backend
from resource_types import RESOURCE_TYPES
type_label = RESOURCE_TYPES.name_for
is_known_safely = RESOURCE_TYPES.is_known_safely

# 语义类型 / 结构信号 (全部与 classifier 现有口径一致, 无新增 magic)
GAMEPLAY_REF_KEYS = (
    "object_definition", "object_definition_name", "interaction", "commodity",
    "buff", "moodlet", "gameplay", "object_function", "simulation", "loot",
    "situation", "recipe", "score", "trait", "career", "skill", "statistic",
    "object_overflow", "satisfaction", "purchase_price", "environment_score",
)
INTERACTION_KEYS = ("interaction", "object_function", "pie_menu", "social_interaction")
POSEPACK_KEYS = ("pose_list", "posepack", "position", "actor")
XML_TEXT_TIDS = {0x0333406C, 0x052FE820, 0x7DF2169C, 0x00AE4E07}  # tuning/snippet/ww/binary-ish
# 已知纯附随/非功能语义的容器 root class (不构成"功能物品本体"证据)
NON_FUNCTIONAL_UI_KEYS = ("ui", "string", "flyout", "menu", "badge", "tutorial",
                          "notification", "icon", "texture", "tag", "wallpaper", "floor")


def _norm(s):
    return (s or "").lower().replace("-", "").replace("_", "").replace(" ", "")


def _decompress(raw: bytes):
    if raw[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        try:
            return zlib.decompress(raw)
        except Exception:
            return raw
    return raw


def _parse_text(raw: bytes):
    d = _decompress(raw)
    for enc in ("utf-8", "utf-16-le"):
        try:
            return d.decode(enc)
        except Exception:
            continue
    return None


def _analyze_xml(raw: bytes, path_label: str):
    """返回 (root_tag, c_class, ref_keys, posepack_root, interaction_root)。"""
    txt = _parse_text(raw)
    if txt is None:
        return ("(unparseable)", "", [], False, False)
    try:
        root = ET.fromstring(txt)
    except Exception:
        # 可能 binary XML: 提取结构信号靠子串
        return ("(binary_xml)", "", [], ("pose_list" in txt), _has_interaction_ref(txt))
    tag = root.tag or ""
    c_attr = ""
    # c= / class= / m= 属性
    for k in ("c", "class", "m"):
        v = root.get(k)
        if v:
            c_attr = v
            break
    refs = []
    rl = txt.lower()
    for key in GAMEPLAY_REF_KEYS:
        if key in rl:
            refs.append(key)
    has_pose = _norm(c_attr).find("posepack") >= 0 or "pose_list" in rl
    has_int = bool(set(refs) & set(INTERACTION_KEYS))
    return (tag or "(no_tag)", c_attr, sorted(set(refs)), has_pose, has_int)


def _has_interaction_ref(txt: str):
    rl = txt.lower()
    return any(k in rl for k in INTERACTION_KEYS)


def audit_package(path: str) -> list:
    """枚举全部 resources, 返回行字典列表 (一次性, 单个包)。"""
    if not os.path.exists(path):
        return [{"package": path, "error": "MISSING_FILE"}]
    idx, err = safe_parse(path)
    if err or idx is None:
        return [{"package": path, "error": f"DBPF_FAIL:{err}"}]
    try:
        backend = get_backend("readonly").open(path)
    except Exception as e:
        return [{"package": path, "error": f"OPEN_FAIL:{e}"}]

    rows = []
    try:
        for e in idx.entries:
            row = {
                "package": path,
                "type_id": f"0x{e.type_id:08X}",
                "group_id": f"0x{e.group_id:08X}",
                "instance": f"0x{e.instance_id:016X}",
                "size": e.size,
                "compressed": "Y" if e.is_compressed else "N",
                "semantic": type_label(e.type_id),
                "verified": "Y" if is_known_safely(e.type_id, "") else "N",
                "root_tag": "",
                "c_class": "",
                "gameplay_refs": "",
                "posepack_root": "",
                "interaction_root": "",
            }
            # ---- 只对文本类 XML/tuning 做结构提炼 (不读 BLOD/DDS/CLIP 大资源) ----
# 语义标签: 0x7DF2169C 在本上下文是 c=PosePackInstance 的 tuning XML (非 WW 专属动画)。
# 报告中性标签: TUNING_XML/PosePackInstance (见 user 2026-08-15 术语修正, 不改 production)。
            if e.type_id in XML_TEXT_TIDS and e.size and e.size <= 4 * 1024 * 1024:
                raw = backend.read_small_resource(e, 4 * 1024 * 1024)
                if raw:
                    tag, c_attr, refs, hp, hi = _analyze_xml(raw, path)
                    row["root_tag"] = tag
                    row["c_class"] = c_attr
                    row["gameplay_refs"] = ";".join(refs)
                    row["posepack_root"] = "Y" if hp else ""
                    row["interaction_root"] = "Y" if hi else ""
            rows.append(row)
    finally:
        try:
            backend.close()
        except Exception:
            pass
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("packages", nargs="+", help="一个或多个 .package 路径")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    all_rows = []
    for p in a.packages:
        all_rows.extend(audit_package(p))

    # 直方图
    hist = Counter((r.get("package"), r.get("semantic")) for r in all_rows)
    print("===== resource-type histogram (package -> semantic type -> count) =====")
    bypkg = defaultdict(Counter)
    for r in all_rows:
        bypkg[r["package"]][r["semantic"]] += 1
    for pkg, c in bypkg.items():
        print(f"\n[{os.path.basename(pkg)}]  total_resources={sum(c.values())}")
        for sem, n in sorted(c.items(), key=lambda x: -x[1]):
            print(f"    {sem:24s} {n}")

    # XML root / 功能证据明细
    print("\n===== XML/tuning 结构明细 (仅文本类) =====")
    for r in all_rows:
        if r.get("root_tag") or r.get("c_class"):
            print(f"  [{os.path.basename(r['package'])}] {r['semantic']} {r['type_id']} "
                  f"root=<{r['root_tag']} c={r['c_class']}> refs=[{r['gameplay_refs']}] "
                  f"pose={r['posepack_root'] or '-'} inter={r['interaction_root'] or '-'}")

    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            w.writeheader()
            w.writerows(all_rows)
        print(f"\n[WROTE] {a.out} ({len(all_rows)} resources, 只读)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
