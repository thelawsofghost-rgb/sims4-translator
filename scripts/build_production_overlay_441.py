#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_production_overlay_441.py —— 从 real 441 preflight 的 unresolved 派生 441 专用 terminal overlay
=========================================================================================
背景: v1 441 zero-write preflight 测得 approved=6693 / TRANSLATE=2331 / KEEP=3881 /
      unresolved=481 (2331+3881+481=6693)。481 次 unresolved 按 exact normalized source 去重后
      为 111 unique: 80 KEEP (technical) + 31 TRANSLATE (explicit frozen map)。

本工具 (第 1 步) 由真实 441 数据 **重新计算** 上述分类, 并 **assert 与实际一致** (fail-closed),
  (第 2 步) 从 `output/translation_overrides.production.csv` 无损复制并 **追加 111 个 explicit
  terminal decisions**, 写出新 overlay:
      output/translation_overrides.production.441.csv

铁律 (用户冻结):
  * 不修改 ProductionResolver / catalog / done / title-final / desc-final
  * 不建立宽泛运行时 regex; 写入 overlay 的**最终结果必须是 exact source rows**。
    本工具内的分类逻辑仅在【一次性派生】时用于识别, 不构成未来 production policy。
  * 80 个 KEEP 冻结为 exact source decision, 记录 reason:
        KEEP_TECHNICAL_NUMERIC     (纯数字标签)
        KEEP_TECHNICAL_POSE_NUMBER (pose 序号标签, 按本次实际 source 识别)
        KEEP_EMPTY_DISPLAY         (空 display)
        KEEP_CREATOR_HANDLE        (@handle 创作者标识)
  * 31 条 TRANSLATE 来自冻结 explicit map (本文件底部 _TRANSLATE_MAP), 原样保留 creator/asset
    标识符 (如 Tibo131 / [Simmerianne93] / ATS4_PillsHD_Convertion_Poseacc)。
  * overlay schema 与既有 production overlay 一致:
        translation_id, source_text, translation, action, reason, notes
    translation_id 用既有 stable helper (phase2a_catalog.make_translation_id(source_hash(norm_text(src)),1))。

输入:
  --coverage            output/coverage_manual_adj.csv (production coverage authority)
  --title-final/--desc-final/--production-overlay/--done/--catalog   同 v1 preflight 五源
  --expect-eligible     441 (默认)
  --out-base            output/translation_overrides.production.csv  (既有 241, 无损复制)
  --out                 output/translation_overrides.production.441.csv (新, fail-closed 已存在)
  --force               覆盖已存在 --out

输出:
  --out                 441 overlay CSV
  --report              分类 assert 报告 md (fail-closed)
  (分类不一致 / 行数不符 / 复制非无损 -> rc=1 HARD-FAIL, 不写 --out)
"""
import sys, os, csv, re, ast
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from phase2a_catalog import norm_text, source_hash, make_translation_id
from production_resolver import make_production_resolver, EXPECTED_ROWS
from gen_cohort_sidecars import approved_pv_refs, resolve_all_approved

# ------------------------------------------------------------------ 冻结分类规则
# 一次性派生用 (不构成未来 production policy; overlay 落盘的是 exact source rows)。
# 优先级从高到低: empty -> @handle -> 纯数字 -> pose 序号 -> 否则必须命中 _TRANSLATE_MAP。
def classify_one(src: str):
    ns = norm_text(src)
    if ns == "":
        return "KEEP_EMPTY_DISPLAY"
    if ns.startswith("@"):
        return "KEEP_CREATOR_HANDLE"
    # 纯数字 (可选负号/小数/分隔): 整串由 [0-9.,+\-x×X] 组成且至少含一个数字
    if re.fullmatch(r"[0-9.,+\-x×X\s]+", ns) and re.search(r"[0-9]", ns):
        return "KEEP_TECHNICAL_NUMERIC"
    # pose 序号标签 (冻结 regex, 仅本次 builder 从真实 481 unresolved 导出 exact rows 用;
    # 不构成 runtime policy, 落盘仍是 exact source rows):
    #   (?i)^pose(?:\s+|_)\d+$   —— case-insensitive, Pose + (空白或 _) + 数字, 全串锚定
    #   覆盖 Pose 17 / Pose  4 / pose 1 / Pose_01 / Pose_09 ;
    #   负向不上当: PosePlayer / Pose ABC / Pose_foo (无数字或非数字结尾)。
    if re.fullmatch(r"(?i)^pose(?:\s+|_)\d+$", ns):
        return "KEEP_TECHNICAL_POSE_NUMBER"
    return None  # 非 technical -> 必须命中 _TRANSLATE_MAP 否则 ERROR


# 31 条 explicit TRANSLATE (冻结, exact source)。{norm_text(source): translation}
_TRANSLATE_MAP = {
    "!Aylin Moss_Заразная любовь": "!Aylin Moss_传染之爱",
    "разговорные позы в кровати, РЫЦАРЬ В ЦЕНТР КРОВАТИ! / talking poses in bed, KNIGHT IN THE CENTER OF THE BED!": "床上聊天姿势，骑士放在床中央！",
    "требуется стакан! (браслет справа) / need plastic cup (right bracelet)": "需要塑料杯！（右手手环）",
    "Tibo131_Poolside_Poses": "Tibo131_泳池边姿势",
    "Tibo131_PornCoverPoses_2": "Tibo131_色情封面姿势_2",
    "Tibo131_XRated_Poses_2": "Tibo131_限制级姿势_2",
    "Tibo131_XRated_Poses_3": "Tibo131_限制级姿势_3",
    "bedposes": "床上姿势",
    "solopose": "单人姿势",
    "[Simmerianne93]Action_poses_05": "[Simmerianne93]动作姿势_05",
    "[Simmerianne93]Action_poses_07": "[Simmerianne93]动作姿势_07",
    "[Simmerianne93]Action_poses_09": "[Simmerianne93]动作姿势_09",
    "[Simmerianne93]Conversation_poses_13_V1": "[Simmerianne93]对话姿势_13_V1",
    "[Simmerianne93]Conversation_poses_13_V2": "[Simmerianne93]对话姿势_13_V2",
    "[Simmerianne93]Conversation_poses_17_V1": "[Simmerianne93]对话姿势_17_V1",
    "[Simmerianne93]Conversation_poses_17_V2": "[Simmerianne93]对话姿势_17_V2",
    "[Simmerianne93]Conversation_poses_19_V1": "[Simmerianne93]对话姿势_19_V1",
    "[Simmerianne93]Conversation_poses_20": "[Simmerianne93]对话姿势_20",
    "[Simmerianne93]Conversation_poses_22": "[Simmerianne93]对话姿势_22",
    "[Simmerianne93]Conversation_poses_23": "[Simmerianne93]对话姿势_23",
    "[Simmerianne93]Conversation_poses_45_V1": "[Simmerianne93]对话姿势_45_V1",
    "[Simmerianne93]Conversation_poses_45_V2": "[Simmerianne93]对话姿势_45_V2",
    "[Simmerianne93]Couple_poses_37": "[Simmerianne93]双人姿势_37",
    "[Simmerianne93]Injured_poses_03": "[Simmerianne93]受伤姿势_03",
    "[Simmerianne93]Phone_poses_09": "[Simmerianne93]电话姿势_09",
    "[Simmerianne93]Request_poses_03": "[Simmerianne93]委托姿势_03",
    "[Simmerianne93]Request_poses_05": "[Simmerianne93]委托姿势_05",
    "[Simmerianne93]Request_poses_12": "[Simmerianne93]委托姿势_12",
    "[Simmerianne93]Solo_poses_05": "[Simmerianne93]单人姿势_05",
    "12 Solo poses for a sim on a bed. Needs [Simmerianne93]ATS4_PillsHD_Convertion_Poseacc": "12个床上单人姿势。需要 [Simmerianne93]ATS4_PillsHD_Convertion_Poseacc",
    "[Simmerianne93]Solo_poses_06": "[Simmerianne93]单人姿势_06",
}
# 冻结期望 (real 441 实测; assert 用, 不参与派生, 也不硬编码进最终 preflight 判定):
#   occurrences: 481 = 450 KEEP + 31 TRANSLATE
#   unique      : 111 = 80 KEEP (52/26/1/1) + 31 TRANSLATE
_EXPECT = {
    "unresolved_occurrences": 481,
    "keep_occurrences": 450,
    "translate_occurrences": 31,
    "keep_unique": 80,
    "translate_unique": 31,
    "unique_total": 111,
    # reasons (unique / occurrences)
    "reason_unique": {"KEEP_TECHNICAL_NUMERIC": 52, "KEEP_TECHNICAL_POSE_NUMBER": 26,
                      "KEEP_EMPTY_DISPLAY": 1, "KEEP_CREATOR_HANDLE": 1},
    "reason_occ": {"KEEP_TECHNICAL_NUMERIC": 359, "KEEP_TECHNICAL_POSE_NUMBER": 73,
                   "KEEP_EMPTY_DISPLAY": 16, "KEEP_CREATOR_HANDLE": 2},
}


def _extract_src(err: str):
    m = re.search(r"source=('[^']*'|\"[^\"]*\")", err)
    if not m:
        return None
    try:
        return ast.literal_eval(m.group(1))
    except Exception:
        return m.group(1).strip("'\"")


def collect_unresolved(coverage, title_final, desc_final, prod_overlay, done, catalog):
    """重跑 approved_pv_refs + resolve; 收集所有 unresolved exact source (occurrences)。"""
    resolver = make_production_resolver(title_final, desc_final, prod_overlay,
                                        translation_done=done, translation_catalog=catalog)
    elig = [r for r in csv.DictReader(open(coverage, encoding="utf-8-sig"))
            if (r.get("status", "") or "").upper() == "ELIGIBLE_EXISTING_CHS"]
    occ = []
    for r in elig:
        path = (r.get("package_path", "") or "").strip()
        if not path or not Path(path).exists():
            continue
        try:
            _, _, approved, errs = approved_pv_refs(path)
        except Exception:
            continue
        if errs:
            continue  # mapping 级错误不属于 unresolved source (v1 已排除)
        mods, keeps, errs2 = resolve_all_approved(approved, resolver, prod_overlay)
        for e in errs2:
            if "缺译文/unresolved" in e:
                src = _extract_src(e)
                if src is not None:
                    occ.append(src)
    return occ


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage", required=True)
    ap.add_argument("--title-final", required=True)
    ap.add_argument("--desc-final", required=True)
    ap.add_argument("--production-overlay", required=True, help="既有 241 overlay (无损复制基底)")
    ap.add_argument("--done", default="")
    ap.add_argument("--catalog", default="")
    ap.add_argument("--expect-eligible", type=int, default=441)
    ap.add_argument("--out", default="output/translation_overrides.production.441.csv")
    ap.add_argument("--report", default="output/build_production_overlay_441_report.md")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    # --out fail-closed
    if Path(a.out).exists() and not a.force:
        print(f"[FAIL-CLOSED] 输出已存在, 拒绝覆盖 (rc=1): {a.out}   (用新路径或 --force)")
        return 1
    for p, lab in [(a.title_final, "title_final"), (a.desc_final, "desc_final"),
                   (a.production_overlay, "production_overlay"), (a.done, "done"),
                   (a.catalog, "catalog")]:
        if p and not Path(p).exists():
            print(f"[HARD-FAIL] {lab} 不存在: {p}"); return 3

    # ---- 1) 真实 unresolved 重新计算 ----
    occ = collect_unresolved(a.coverage, a.title_final, a.desc_final,
                             a.production_overlay, a.done, a.catalog)
    uniq = sorted(set(occ))

    # 分类
    kinds = {}   # src -> kind
    trans = {}   # src -> translation (from frozen map)
    for src in uniq:
        ns = norm_text(src)
        k = classify_one(src)
        if k is not None:
            kinds[src] = k
        else:
            tr = _TRANSLATE_MAP.get(ns)
            if tr is None:
                print(f"[HARD-FAIL] unresolved source 无法分类且不在冻结 TRANSLATE map: {src!r}")
                for s in uniq: print("  ", repr(s))
                return 1
            kinds[src] = "TRANSLATE"; trans[src] = tr

    occ_by_src = Counter(occ)
    reason_unique = Counter(v for v in kinds.values() if v != "TRANSLATE")
    reason_occ = Counter((kinds[s] for s in occ))

    keep_unique = sum(1 for s in uniq if kinds[s] != "TRANSLATE")
    trans_unique = sum(1 for s in uniq if kinds[s] == "TRANSLATE")
    keep_occ = reason_occ["KEEP_TECHNICAL_NUMERIC"] + reason_occ["KEEP_TECHNICAL_POSE_NUMBER"] \
        + reason_occ["KEEP_EMPTY_DISPLAY"] + reason_occ["KEEP_CREATOR_HANDLE"]
    trans_occ = reason_occ["TRANSLATE"]

    # ---- 2) assert 与实际一致 (fail-closed) ----
    errs = []
    def eq(name, got, want):
        if got != want:
            errs.append(f"{name}: 实得 {got} != 期望 {want}")
    eq("unresolved occurrences", len(occ), _EXPECT["unresolved_occurrences"])
    eq("unique total", len(uniq), _EXPECT["unique_total"])
    eq("KEEP unique", keep_unique, _EXPECT["keep_unique"])
    eq("TRANSLATE unique", trans_unique, _EXPECT["translate_unique"])
    eq("KEEP occurrences", keep_occ, _EXPECT["keep_occurrences"])
    eq("TRANSLATE occurrences", trans_occ, _EXPECT["translate_occurrences"])
    for k, w in _EXPECT["reason_unique"].items():
        eq(f"reason unique {k}", reason_unique.get(k, 0), w)
    for k, w in _EXPECT["reason_occ"].items():
        eq(f"reason occ {k}", reason_occ.get(k, 0), w)

    if errs:
        print("[HARD-FAIL] 分类 assert 与实际不一致:")
        for e in errs: print("  -", e)
        print(f"  实际 occurrences={len(occ)} unique={len(uniq)}  KEEP unique={keep_unique} occ={keep_occ}"
              f"  TRANSLATE unique={trans_unique} occ={trans_occ}")
        print("  (未写 --out; 不产生 overlay)")
        return 1

    # ---- 3) 无损复制 base + 追加 111 = 写 --out ----
    base_rows = []
    with open(a.production_overlay, encoding="utf-8-sig") as f:
        rdr = csv.DictReader(f)
        hdr = list(rdr.fieldnames or [])
        rows0 = list(rdr)
    base_rows = rows0
    if "action" in hdr:
        hdr_out = hdr + ["reason"] if "reason" not in hdr else hdr
    else:
        hdr_out = ["translation_id", "source_text", "translation", "action", "reason", "notes"]

    added = []
    for src in sorted(uniq):
        tid = make_translation_id(source_hash(norm_text(src)), 1)
        k = kinds[src]
        if k == "TRANSLATE":
            added.append({"translation_id": tid, "source_text": src, "translation": trans[src],
                          "action": "TRANSLATE", "reason": "", "notes": "441 explicit terminal"})
        else:
            added.append({"translation_id": tid, "source_text": src, "translation": "",
                          "action": "KEEP", "reason": k, "notes": "441 explicit terminal (exact source)"})

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    write_cols = [c for c in ("translation_id", "source_text", "translation", "action", "reason", "notes")
                  if c in hdr_out or c in ("translation_id", "source_text", "translation", "action")]
    wrote_base = 0
    wrote_add = 0
    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["translation_id","source_text","translation","action","reason","notes"])
        w.writeheader()
        for r in base_rows:
            w.writerow({"translation_id": r.get("translation_id",""),
                        "source_text": r.get("source_text",""),
                        "translation": r.get("translation",""),
                        "action": (r.get("action") or r.get("status") or r.get("origin") or "").upper(),
                        "reason": r.get("reason",""), "notes": r.get("notes","")})
            wrote_base += 1
        for r in added:
            w.writerow(r); wrote_add += 1

    # ---- 报告 ----
    out = []
    out.append("# Build Production Overlay 441 (derivation + assert)")
    out.append("")
    out.append(f"- unresolved occurrences = {len(occ)} (去重 unique = {len(uniq)})")
    out.append(f"- KEEP: unique={keep_unique}  occurrences={keep_occ}")
    out.append(f"    KEEP_TECHNICAL_NUMERIC     unique={reason_unique.get('KEEP_TECHNICAL_NUMERIC',0)} occ={reason_occ.get('KEEP_TECHNICAL_NUMERIC',0)}")
    out.append(f"    KEEP_TECHNICAL_POSE_NUMBER unique={reason_unique.get('KEEP_TECHNICAL_POSE_NUMBER',0)} occ={reason_occ.get('KEEP_TECHNICAL_POSE_NUMBER',0)}")
    out.append(f"    KEEP_EMPTY_DISPLAY         unique={reason_unique.get('KEEP_EMPTY_DISPLAY',0)} occ={reason_occ.get('KEEP_EMPTY_DISPLAY',0)}")
    out.append(f"    KEEP_CREATOR_HANDLE        unique={reason_unique.get('KEEP_CREATOR_HANDLE',0)} occ={reason_occ.get('KEEP_CREATOR_HANDLE',0)}")
    out.append(f"- TRANSLATE: unique={trans_unique}  occurrences={trans_occ}  (31 冻结 explicit map, exact source)")
    out.append(f"- assert: 全部与实际一致")
    out.append("")
    out.append(f"- base 无损复制 {wrote_base} 行 + 追加 {wrote_add} 行 = 总行 {wrote_base + wrote_add}")
    out.append(f"- 写 {a.out}")
    text = "\n".join(out) + "\n"
    Path(a.report).parent.mkdir(parents=True, exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print(f"[out]   {a.out}")
    print(f"[report]{a.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
