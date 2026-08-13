#!/usr/bin/env python3
"""Phase 2A 骨架: 生成 完整候选审计总账 translation_catalog.csv + 待译清单 translations_todo.csv。

只读, 不改任何 .package, 不调用翻译引擎。

输入: output/pose_translation_candidates.csv  (3567 语义候选, 列:
       source_text, ref_count, unique_keys, sample_package, sample_pose_pack,
       sample_stbl_instance, sample_locale, sample_neighbor_poses, sample_neighbor_display_texts)
输出:
  output/translation_catalog.csv  全部候选 (含 KEEP), 完整审计总账;
                                  列: translation_id, source_text, decision, reason,
                                      detected_language, translation, status,
                                      ref_count, package_count, packages,
                                      sample_package, sample_pose_pack, sample_stbl_instance,
                                      sample_locale, neighbor_display_texts, notes, source_hash
  output/translations_todo.csv    仅 TRANSLATE + REVIEW; translation 空; status=PENDING/REVIEW
                                  列: translation_id, source_text, decision, reason,
                                      detected_language, translation, status, source_hash

约定 (2026-08-13 用户确认):
  - translation_id 为内容派生稳定 ID: T_{source_hash}_g1
      source_hash = Unicode NFC(source_text) -> SHA-256 前 12 hex (不做 lower, 保留大小写/标点/数字/结构)
      以后同一 source_text 拆上下文 -> g2/g3..., 一旦分配必须持久化, 不得按行顺序重编号。
      额外保留 source_hash 列。
  - KEEP: translation="", decision=KEEP, status=KEEP (不用占位符, 不复制 source_text 到 translation)
  - TRANSLATE -> todo, translation="", status=PENDING
  - REVIEW    -> todo, translation="", status=REVIEW (翻译结果未经 APPROVED 不得进入 Phase 2B)
  - detected_language 与 decision/reason 分离:
      en/es/fr/de/ru/zh/ja/ko...  具体语言
      und = undetermined           无法判定语言
      zxx = no linguistic content  纯编号/ID/技术串 (语言无意义)
      mul = mixed languages         混合语言 (需要时)
    约定: 6 А2 / A-1 / a2o_xxx_loop -> zxx (是否 KEEP 仍由 decision/reason 决定)
          Revisando -> es ; Femme -> fr
  - packages 仅供审计, 绝不可用于定位写回。写回定位由 Phase 2B 另行 JOIN
    pose_reverse_mapping_full.csv 按 package_path / pose_pack_instance / pose entry /
    STBL resource instance / locale / STBL key 精确完成。

严格校验 (不满足即停止, 不继续写):
  TRANSLATE + REVIEW 必须 = 1987 (1980 + 7)。否则报错退出。
  translation_id 全局唯一性。
"""
import sys, csv, re, hashlib, unicodedata
from pathlib import Path
from collections import Counter, defaultdict
from phase2a_samples import classify_with_context, classify

out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "D:/projects/sims4_trans/output")
# --strict 生产严格校验 (计数必须=1980/1580/7/3567, todo 必须=1987)。
#    无 --strict 时仅做完整性校验 (ID 唯一/KEEP 无占位), 便于 fixture 回归。
STRICT = "--strict" in sys.argv
cand_csv = out_dir / "pose_translation_candidates.csv"

# ---------------- 语言检测 (与 decision/reason 分离) ----------------
def is_cjk(s):  return any('\u4e00' <= c <= '\u9fff' for c in s)
def is_kr(s):   return any('\uac00' <= c <= '\ud7af' or '\u3130' <= c <= '\u318f' for c in s)
def is_jp(s):   return any('\u3040' <= c <= '\u30ff' for c in s)
def is_cyrillic(s): return any('\u0400' <= c <= '\u04ff' for c in s)

# 拉丁借词 -> 具体语言 (西/法/德/意/葡, 低误伤)
_LEX_ES = {"el","la","los","las","y","un","una","con","para","por","del","que","de","se",
           "hombre","mujer","mujeres","hombre","señor","señora","revisando","asomado","mirando",
           "sentado","sentada","de","pie","manos","cabeza","brazos","piernas","beso","bailando"}
_LEX_FR = {"le","la","les","et","un","une","des","du","de","en","pour","avec","femme","homme",
           "señor","garçon","fille","baiser","danse"}
_LEX_DE = {"der","die","das","und","ein","eine","mit","für","von","zu","auf","sitzend","stehend"}
_LEX_IT = {"il","lo","la","i","gli","e","un","una","con","per","da","del","della","seduto","in","piedi"}
_LEX_PT = {"o","a","os","as","e","um","uma","com","para","por","do","da","de","em","sentado","em","pé"}

def detect_language(s: str, cls: str, reason: str) -> str:
    """返回 detected_language 值域: en/es/fr/de/ru/zh/ja/ko/... | und | zxx | mul"""
    t = (s or "").strip()
    # 纯编号/ID/技术串 -> 无语言内容 (zxx)。判定: 无任何 >=2 字母的英文单词,
    # 即只有数字 + 单字母索引 + 符号 (A-1 / 6 А2 / 6B / x_2 / 4.1 / a2o_... 中无完整词)。
    # 即便 classifier 把它归到 PROPER_NAME (name-group 启发式), 语言层仍判 zxx ——
    # 语言字段与 decision/reason 分离, 是否 KEEP 仍由 decision 决定。
    words = re.findall(r"[A-Za-z]{2,}", t)
    has_alpha_word = any(len(w) >= 2 for w in words)
    if not has_alpha_word:
        return "zxx"
    if reason in ("NON_SEMANTIC_TAG", "TECHNICAL_LABEL"):
        return "zxx"
    # 技术串即便含完整英文词 (a2o_phone_call_talk_neutral_02_seated_x 有完整 snake 词)
    # 仍属无语言内容: 由 reason 决定
    if reason == "TECHNICAL_LABEL":
        return "zxx"
    if cls == "NON_ENGLISH" or is_cjk(t) or is_kr(t) or is_jp(t):
        if is_cjk(t): return "zh"
        if is_kr(t):  return "ko"
        if is_jp(t):  return "ja"
    if is_cyrillic(t):
        return "ru"
    # 拉丁非英语语义 (Femme/Revisando/Asomado) -> 尝试具体语言
    if cls == "NON_ENGLISH_SEMANTIC":
        lw = words
        lset = set(w.lower() for w in lw)
        if lset & _LEX_ES: return "es"
        if lset & _LEX_FR: return "fr"
        if lset & _LEX_DE: return "de"
        if lset & _LEX_IT: return "it"
        if lset & _LEX_PT: return "pt"
        return "und"  # 非英语但认不出具体语种
    # 其余默认英文语义 (分类器里的 ENGLISH_SEMANTIC/SEMANTIC_WITH_NUM 语义体)
    return "en"


def source_hash(s: str) -> str:
    """内容派生稳定 hash: NFC(source_text) -> SHA-256 -> 前 12 hex。不做 lower。"""
    norm = unicodedata.normalize("NFC", s)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]


def make_translation_id(source_hash_val: str, group: int = 1) -> str:
    return f"T_{source_hash_val}_g{group}"



if __name__ == "__main__":
    # ---------------- 装载候选 ----------------
    rows = []
    with open(cand_csv, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    print(f"[输入] 候选总数: {len(rows)}")

    # ---------------- 逐条分类 + 建 catalog ----------------
    _cat_cols = ["translation_id", "source_text", "decision", "reason", "detected_language",
                 "translation", "status", "ref_count", "package_count", "packages",
                 "sample_package", "sample_pose_pack", "sample_stbl_instance",
                 "sample_locale", "neighbor_display_texts", "notes", "source_hash"]
    _todo_cols = ["translation_id", "source_text", "decision", "reason",
                  "detected_language", "translation", "status", "source_hash"]

    def _packages_of(r):
        """packages 仅供审计: 用 source_text 在所有行聚合不同 package_count; sample_package 仅一行快照。"""
        # 这里只收集当前行的 sample_package 作为审计快照 (跨行聚合在下方统计阶段做)
        return (r.get("sample_package") or "").strip()

    cat_rows = []
    id_seen = defaultdict(int)   # translation_id -> count (全局唯一性校验)
    for r in rows:
        txt = (r.get("source_text") or "").strip()
        cls = classify(txt)
        dec, reason = classify_with_context(txt, r.get("sample_neighbor_display_texts") or "")
        lang = detect_language(txt, cls, reason)
        if dec == "TRANSLATE":
            status = "PENDING"
        elif dec == "REVIEW":
            status = "REVIEW"
        else:  # KEEP
            status = "KEEP"
        sh = source_hash(txt)
        tid = make_translation_id(sh, 1)
        id_seen[tid] += 1
        cat_rows.append({
            "translation_id": tid,
            "source_text": txt,
            "decision": dec,
            "reason": reason,
            "detected_language": lang,
            "translation": "",
            "status": status,
            "ref_count": r.get("ref_count", ""),
            "package_count": "",          # 下方聚合填充
            "packages": _packages_of(r),
            "sample_package": r.get("sample_package", ""),
            "sample_pose_pack": r.get("sample_pose_pack", ""),
            "sample_stbl_instance": r.get("sample_stbl_instance", ""),
            "sample_locale": r.get("sample_locale", ""),
            "neighbor_display_texts": r.get("sample_neighbor_display_texts", ""),
            "notes": "",
            "source_hash": sh,
        })

    # ---------------- packages 聚合 (审计用, 非写回定位) ----------------
    # 跨所有行, 同一 source_text 出现的不同 package 数 + 包名列表
    pkg_of_text = defaultdict(set)
    for r in rows:
        txt = (r.get("source_text") or "").strip()
        p = (r.get("sample_package") or "").strip()
        if p:
            pkg_of_text[txt].add(p)
    for cr in cat_rows:
        ps = sorted(pkg_of_text.get(cr["source_text"], set()))
        cr["package_count"] = len(ps)
        cr["packages"] = "; ".join(ps)

    # ---------------- 严格校验 (不满足即停止) ----------------
    print("\n[校验] 决策计数:")
    dec_cnt = Counter(cr["decision"] for cr in cat_rows)
    for k, v in dec_cnt.most_common():
        print(f"  {k:10} = {v}")
    total = len(cat_rows)
    n_todo_expected = dec_cnt["TRANSLATE"] + dec_cnt["REVIEW"]
    print(f"  TOTAL      = {total}")
    print(f"  TRANSLATE+REVIEW = {n_todo_expected}")

    if STRICT:
        if dec_cnt["TRANSLATE"] != 1980 or dec_cnt["KEEP"] != 1580 or dec_cnt["REVIEW"] != 7 or total != 3567:
            print("\n[!!] 决策计数与预期不符 (TRANSLATE=1980 / KEEP=1580 / REVIEW=7 / TOTAL=3567)。停止, 不写表。")
            sys.exit(2)
        if n_todo_expected != 1987:
            print(f"\n[!!] translations_todo 必须恰为 1987 行 (1980+7), 当前将生成 {n_todo_expected}。停止。")
            sys.exit(2)
    else:
        print(f"[fx] 非严格模式: 计数与生产预期可能不同 (TRANSLATE+REVIEW={n_todo_expected})")

    # translation_id 唯一性
    dups = {k: v for k, v in id_seen.items() if v > 1}
    if dups:
        print(f"\n[!!] translation_id 重复 {len(dups)} 组: {list(dups.items())[:10]}。停止。")
        sys.exit(2)

    # KEEP 完整性: translation 必须为空
    keep_bad = [cr for cr in cat_rows if cr["decision"] == "KEEP" and cr["translation"] != ""]
    if keep_bad:
        print(f"\n[!!] {len(keep_bad)} 条 KEEP 的 translation 未保持空。停止。")
        sys.exit(2)

    print("\n[校验] 完整性校验通过 (translation_id 唯一 + KEEP 无占位)" + (" + 计数精确匹配" if STRICT else ""))

    # ---------------- 写 catalog (全部候选) ----------------
    catalog_out = out_dir / "translation_catalog.csv"
    with open(catalog_out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=_cat_cols)
        w.writeheader()
        # 稳定输出顺序: 按 translation_id (内容哈希) 排序, 不依赖候选表行序
        for cr in sorted(cat_rows, key=lambda x: x["translation_id"]):
            w.writerow(cr)
    print(f"\n[输出] 完整审计总账: {catalog_out}  ({len(cat_rows)} 行)")

    # ---------------- 写 todo (仅 TRANSLATE + REVIEW) ----------------
    todo_rows = [cr for cr in cat_rows if cr["decision"] in ("TRANSLATE", "REVIEW")]
    todo_out = out_dir / "translations_todo.csv"
    with open(todo_out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=_todo_cols)
        w.writeheader()
        for cr in sorted(todo_rows, key=lambda x: x["translation_id"]):
            w.writerow({k: cr[k] for k in _todo_cols})
    print(f"[输出] 待译清单:       {todo_out}  ({len(todo_rows)} 行)")

    # ---------------- 统计报告 ----------------
    print("\n========== Phase 2A 骨架统计 ==========")
    print(f"总候选数 (catalog)         = {total}")
    print(f"TRANSLATE (->todo)         = {dec_cnt['TRANSLATE']}")
    print(f"KEEP     (仅 catalog)      = {dec_cnt['KEEP']}")
    print(f"REVIEW   (->todo,须批准)   = {dec_cnt['REVIEW']}")
    print(f"translations_todo 实际行数  = {len(todo_rows)}")
    print(f"translation_id 唯一性      = {'OK' if not dups else 'FAIL'}")

    lang_cnt = Counter(cr["detected_language"] for cr in cat_rows)
    print("\n各语言数量 (catalog):")
    for k, v in lang_cnt.most_common():
        print(f"  {k:5} = {v}")

    # REVIEW 明细 (进 todo 的 7 条, 含 4 条 NON_ENGLISH_SEMANTIC)
    print("\nREVIEW 明细 (7 条):")
    for cr in todo_rows:
        if cr["decision"] == "REVIEW":
            print(f"  [{cr['detected_language']:3}] {cr['source_text']!r}  (reason={cr['reason']})")

    # NON_ENGLISH_SEMANTIC 是否都进 REVIEW (用户约定)
    nes = [cr for cr in cat_rows if cr["reason"] == "NON_ENGLISH_SEMANTIC"]
    print(f"\nNON_ENGLISH_SEMANTIC 数 = {len(nes)}  (应=4, 且全部 decision=REVIEW)")
    for cr in nes:
        print(f"  [{cr['detected_language']:3}] {cr['source_text']!r}  decision={cr['decision']} status={cr['status']}")

    print("\n说明: packages 仅供审计, 不用于写回定位。")
    print("Phase 2B 将按 pose_reverse_mapping_full.csv 精确 JOIN (package_path/pose_pack_instance/"
          "pose entry/STBL instance/locale/STBL key) 完成写回。本轮不调用翻译引擎、不改任何 package。")
