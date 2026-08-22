#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEST C EASY — 候选定位分析器 (只读, 吃 corpus survey 的 animation_entries.csv)

Dorothy 在 Windows 跑 `ww_animation_corpus_survey_v1.py --mods-root ...` 得到
`output\ww_animation_corpus_v1\animation_entries.csv` (每行一个 entry, 含
ww_xml_tgi / entry_ordinal / animation_raw_display_name / animation_locations /
animation_custom_locations / animation_category / animation_tags / animation_loops /
actor_count / actor_* 等)。

本工具对该 CSV 做:
  1. 全量统计: total entries / unique display / duplicate 数量 / location 分类 (Bed/Floor/Sofa/Other)
  2. 对 Dorothy 截图目标做精确搜索 (大小写不敏感 trim 匹配), 命中即列出该 entry 细节
  3. 对命中项输出 EASY 评分 (名字唯一 / location 明确 / 无 custom / actors<=2 / 非常规后缀)

只读, 不生成 package, 不写 Mods。用法:
  python scripts/ww_animation_testc_candidate_from_csv.py --csv "<path>\animation_entries.csv"
"""
import argparse, csv, re, sys
from collections import Counter, defaultdict

SCREENSHOTS = {
    "BED": [
        "Lying Sideway | Footjob",
        "MF FootJob",
        "MF Simple FootJob",
        "MF KneeJob",
        "First Time 4",
        "First Time 5 - Premature Ejac",
        "First Time 6",
    ],
    "FLOOR": [
        "Pounded standing | Rough doggy",
        "Wide open legs | Kneeling doggystyle",
        "Wide open legs | Reverse Cowgirl",
    ],
    "SOFA": [
        "Passionate loveseat | Cowgirl",
        "Standing Reversed Cowgirl",
        "Rough Doggy",
    ],
}

LOC_CN = {"BED": "床", "FLOOR": "地板", "SOFA": "沙发", "DESK": "书桌", "CHAIR": "椅子",
          "TABLE": "桌子", "WALL": "墙", "WINDOW": "窗", "POOL": "泳池", "SHOWER": "淋浴"}


def norm(s):
    return (s or "").strip().lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="animation_entries.csv 完整路径 (真机 survey 产物)")
    a = ap.parse_args()

    rows = []
    with open(a.csv, newline="", encoding="utf-8-sig") as fh:
        rd = csv.DictReader(fh)
        for r in rd:
            rows.append(r)
    n = len(rows)
    print(f"CSV_PATH={a.csv}")
    print(f"TOTAL_ENTRIES={n}")

    disp = Counter(norm(r.get("animation_raw_display_name", "")) for r in rows)
    blank = disp.pop("", 0)
    unique = sum(1 for k, v in disp.items() if v == 1)
    dup = sum(1 for k, v in disp.items() if v > 1)
    print(f"UNIQUE_DISPLAY_NAMES={unique}")
    print(f"DUPLICATE_NAMES={dup}  (display 名出现>=2 次的 distinct 名)")
    print(f"EMPTY_DISPLAY={blank}")
    dup_examples = [k for k, v in disp.items() if v > 1][:10]
    if dup_examples:
        print(f"DUPLICATE_EXAMPLES={', '.join(dup_examples)}")

    # location 分类计数 (取 animation_locations 的首个 token 归类)
    loc_counter = Counter()
    for r in rows:
        locs = norm(r.get("animation_locations", "")).split(",")
        locs = [x for x in locs if x]
        if not locs:
            loc_counter["EMPTY"] += 1
        else:
            loc_counter[locs[0].upper()] += 1
    print("LOCATION_CLASSIFICATION:")
    for k in ("BED", "FLOOR", "SOFA", "CHAIR", "DESK", "TABLE", "WALL", "WINDOW", "POOL", "SHOWER"):
        if loc_counter.get(k):
            print(f"  {k}={loc_counter[k]}  ({LOC_CN.get(k,k)})")
    KNOWN = {"BED", "FLOOR", "SOFA", "CHAIR", "DESK", "TABLE", "WALL", "WINDOW", "POOL", "SHOWER"}
    other = sum(v for k, v in loc_counter.items() if k not in KNOWN)
    print(f"  OTHER={other}  (含 EMPTY={loc_counter.get('EMPTY',0)})")

    # ---- 截图目标精确搜索 ----
    by_disp = defaultdict(list)
    for r in rows:
        by_disp[norm(r.get("animation_raw_display_name", ""))].append(r)

    print("\n===== SCREENSHOT TARGET SEARCH =====")
    hits = []
    for loc, names in SCREENSHOTS.items():
        for wanted in names:
            target = norm(wanted)
            matched = by_disp.get(target, [])
            if matched:
                for r in matched:
                    hits.append((loc, wanted, r))
                print(f"[{loc}] HIT  '{wanted}'  -> {len(matched)} entry(ies)")
            else:
                # 近似: 检查是否作为子串出现 (仅提示, 不算精确命中)
                partial = [d for d in by_disp if target in d or d in target]
                if partial:
                    print(f"[{loc}] NOT-EXACT  '{wanted}'  (近似子串: {', '.join(partial[:5])})")
                else:
                    print(f"[{loc}] MISS  '{wanted}'")

    # ---- 命中项 EASY 评分 ----
    print("\n===== EASY_SCORING (命中项) =====")
    if not hits:
        print("无精确命中 -> 无 EASY 候选。需核对截图名与真实包 display 名是否一致。")
        return 0

    for loc, wanted, r in hits:
        dn = r.get("animation_raw_display_name", "")
        reasons = []
        score = "EASY_GOOD"
        # 1 名字唯一
        if disp.get(norm(dn), 1) > 1:
            score = "EASY_SKIP"
            reasons.append("display 名不唯一(有同名/序列)")
        # 2 location 明确: 目标截图分类是否在 animation_locations 中
        rl = norm(r.get("animation_locations", ""))
        want_loc = norm(loc)
        if want_loc not in rl.split(","):
            reasons.append(f"location 不含目标{loc}(实为 {r.get('animation_locations','')})")
        # 3 无 custom location
        cus = norm(r.get("animation_custom_locations", ""))
        if cus and cus not in ("", "0"):
            if score == "EASY_GOOD":
                score = "EASY_SKIP"
            reasons.append(f"custom_location={cus}")
        # 4 actors <= 2
        ac = r.get("actor_count", "")
        try:
            if int(ac) > 2:
                if score == "EASY_GOOD":
                    score = "EASY_SKIP"
                reasons.append(f"actor_count={ac}>2")
        except Exception:
            pass
        # 5 非常规后缀干扰
        if "custom voices" in norm(dn) or re.search(r"\*", dn):
            reasons.append("display 含 *CUSTOM VOICES* 类后缀")
        print(f"[{loc}] ordinal={r.get('entry_ordinal','?')}  {score}")
        print(f"    display={dn}")
        print(f"    category={r.get('animation_category','')}  locations={r.get('animation_locations','')}  custom={r.get('animation_custom_locations','')}")
        print(f"    ww_xml_tgi={r.get('ww_xml_tgi','')}  source={r.get('source_path','')}")
        print(f"    tags={r.get('animation_tags','')}  loops={r.get('animation_loops','')}  actors={r.get('actor_count','')}")
        print(f"    actor_types={r.get('animation_type','')}  genders={r.get('animation_genders','')}")
        for w in reasons:
            print(f"    - {w}")
    print("\nZERO_WRITE_TO_MODS=YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
