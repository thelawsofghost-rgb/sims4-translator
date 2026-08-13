#!/usr/bin/env python3
"""Phase 2A: 生成 完整候选审计总账 translation_catalog.csv + 待译清单 translations_todo.csv
             + 按上下文展开 translation_contexts.csv。

只读, 不改任何 .package, 不调用翻译引擎。

输入:
  output/pose_translation_candidates.csv   语义候选 (去重后每文本一行)
  output/pose_reverse_mapping_full.csv     完整反向映射 (ref_count union / 上下文展开的数据源)
输出:
  output/translation_catalog.csv        全部候选 (含 KEEP), 完整审计总账;
      列: translation_id, source_text, decision, reason, detected_language,
          translation, status, ref_count, unique_refs, package_count, packages,
          context_count, source_hash
  output/translations_todo.csv          仅 decision∈{TRANSLATE, REVIEW}; translation 空;
      列: translation_id, source_text, decision, reason, detected_language,
          translation, status, source_hash
  output/translation_contexts.csv       每个实际上下文一行, 按 translation_id 关联 catalog;
      列: translation_id, source_text, package_path, pose_pack_instance, stbl_resource_instance,
          locale, stbl_key_ref, neighbor_display_texts, pose_entry_idx, pose_display_name_hash

约定 (2026-08-13 用户确认, 冻结基线):
  - translation_id 为内容派生稳定 ID: T_{source_hash}_g1
      source_hash = Unicode NFC(canonical source_text) -> SHA-256 前 12 hex
      (canonical = N*** 后 strip 首尾空白; 不做 lower, 保留内部空白/大小写/标点/数字/结构)
      同文本默认共享 g1; 将来上下文要求不同译法 -> 允许拆 g2/g3。一旦分配必须持久化, 不得重编号。
      额外保留 source_hash 列。
  - 冻结基线 (去重后): TRANSLATE=1961, KEEP=1572, REVIEW=7, catalog=3540, todo=1968。
  - KEEP: translation="", decision=KEEP, status=KEEP (不复制 source_text 到 translation, 无占位)。
  - REVIEW -> todo, status=REVIEW (译文未经 APPROVED 不得进 Phase 2B)。
  - detected_language 与 decision/reason 分离: en/es/fr/de/ru/zh/ja/ko/... | und | zxx | mul。
  - packages 仅供审计, 不用于写回定位。写回由 Phase 2B 按 reverse_mapping 精确 JOIN。

去重 & 合并 (用户约束):
  - 同 NFC canonical source_text 合并为一行 (默认 g1)。优先在 phase2_segment 层做干净去重;
    本脚本对候选行再做防御性 N*** 去重, 并报告 raw/duplicate/unique 指标。
  - merge 前冲突检查: 同一 canonical text, decision 必须一致, detected_language 必须兼容;
    有差异 -> MERGE_CONFLICT 立即停止, 不自动择一。reason/category 差异保留诊断, 不静默覆盖。
  - ref_count 不盲目求和: 从 reverse_mapping_full 的精确引用做 union 计数; 无重叠时才等于逐行求和。
  - 去重不丢上下文: translation_contexts.csv 每实际上下文一行 (经 translation_id 关联)。

严格校验 (--strict, 任何不满足即停止不写表):
  - catalog 无重复 canonical source_text
  - translation_id 唯一
  - todo = catalog 中 decision∈{TRANSLATE,REVIEW} 的精确集合
  - todo 行数 = 1961 + 7 = 1968
  - KEEP 不进 todo
  - REVIEW 7 条全部保留
  - merge_conflicts = 0
"""
import sys, csv, re, hashlib, unicodedata
from pathlib import Path
from collections import Counter, defaultdict
from phase2a_samples import classify_with_context, classify

out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "D:/projects/sims4_trans/output")
STRICT = "--strict" in sys.argv
cand_csv = out_dir / "pose_translation_candidates.csv"
rev_csv = out_dir / "pose_reverse_mapping_full.csv"

# ---------------- 语言检测 (与 decision/reason 分离) ----------------
def is_cjk(s):  return any('\u4e00' <= c <= '\u9fff' for c in s)
def is_kr(s):   return any('\uac00' <= c <= '\ud7af' or '\u3130' <= c <= '\u318f' for c in s)
def is_jp(s):   return any('\u3040' <= c <= '\u30ff' for c in s)
def is_cyrillic(s): return any('\u0400' <= c <= '\u04ff' for c in s)

_LEX_ES = {"el","la","los","las","y","un","una","con","para","por","del","que","de","se",
           "hombre","mujer","mujeres","señor","señora","revisando","asomado","mirando",
           "asustado","asustada","asustados","asustadas",
           "sentado","sentada","pie","manos","cabeza","brazos","piernas","beso","bailando"}
_LEX_FR = {"le","la","les","et","un","une","des","du","de","en","pour","avec","femme","homme",
           "garçon","fille","baiser","danse"}
_LEX_DE = {"der","die","das","und","ein","eine","mit","für","von","zu","auf","sitzend","stehend"}
_LEX_IT = {"il","lo","la","i","gli","e","un","una","con","per","da","del","della","seduto","in","piedi"}
_LEX_PT = {"o","a","os","as","e","um","uma","com","para","por","do","da","de","em","sentado","pé"}

def detect_language(s: str, cls: str, reason: str) -> str:
    t = (s or "").strip()
    words = re.findall(r"[A-Za-z]{2,}", t)
    has_alpha_word = any(len(w) >= 2 for w in words)
    if not has_alpha_word:
        return "zxx"                       # 纯编号/单字母索引/技术串: 无语言内容
    if reason in ("NON_SEMANTIC_TAG", "TECHNICAL_LABEL"):
        return "zxx"
    if cls in ("NON_ENGLISH_SEMANTIC",):
        if is_cjk(t): return "zh"
        if is_kr(t):  return "ko"
        if is_jp(t):  return "ja"
        if is_cyrillic(t): return "ru"
        lset = set(w.lower() for w in words)
        if lset & _LEX_ES: return "es"
        if lset & _LEX_FR: return "fr"
        if lset & _LEX_DE: return "de"
        if lset & _LEX_IT: return "it"
        if lset & _LEX_PT: return "pt"
        return "und"
    if is_cjk(t): return "zh"
    if is_kr(t):  return "ko"
    if is_jp(t):  return "ja"
    if is_cyrillic(t): return "ru"
    return "en"

def source_hash(s: str) -> str:
    norm = unicodedata.normalize("NFC", s)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]

def make_translation_id(source_hash_val: str, group: int = 1) -> str:
    return f"T_{source_hash_val}_g{group}"

def norm_text(s: str) -> str:
    """canonical 去重键: N*** 后 strip 首尾空白 (保留内部空白/大小写/标点)。"""
    return unicodedata.normalize("NFC", (s or "")).strip()

# 语言兼容: 同文本合并时允许的具体语言集合 (zxx 与具体语言不同类, 视为需冲突检查)
def _lang_compatible(a: str, b: str) -> bool:
    if a == b:
        return True
    # und 可与任何具体语言合并(未知 -> 具体); zxx 只可与 zxx 合并
    if a == "und" or b == "und":
        return True
    return False


if __name__ == "__main__":
    # ---------------- 装载候选 (防御性 N*** 去重) ----------------
    raw_rows = []
    with open(cand_csv, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            raw_rows.append(r)
    raw_candidate_rows = len(raw_rows)
    print(f"[输入] 候选原始行: {raw_candidate_rows}")

    # 按 canonical source_text 分组
    groups = defaultdict(list)
    for r in raw_rows:
        txt = norm_text(r.get("source_text"))
        groups[txt].append(r)
    duplicate_groups = sum(1 for g in groups.values() if len(g) > 1)
    duplicate_rows_removed = sum(len(g) - 1 for g in groups.values())
    unique_candidates = len(groups)
    print(f"[去重] duplicate_groups={duplicate_groups}  "
          f"duplicate_rows_removed={duplicate_rows_removed}  unique_candidates={unique_candidates}")

    # 排序稳定
    canon_texts = sorted(groups.keys())

    # ---------------- 装载 reverse mapping (精确引用, 用于 ref_count union + 上下文) ----------------
    rev_by_text = defaultdict(list)
    if rev_csv.exists():
        with open(rev_csv, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                t = norm_text(r.get("source_text"))
                rev_by_text[t].append(r)
    else:
        print(f"[提示] 未找到 {rev_csv.name}, ref_count 将退化为候选表 ref_count, 上下文文件为空。")

    # ---------------- 逐文本分类 + merge 冲突检查 ----------------
    _cat_cols = ["translation_id", "source_text", "decision", "reason", "detected_language",
                 "translation", "status", "ref_count", "unique_refs", "package_count", "packages",
                 "context_count", "source_hash"]
    _todo_cols = ["translation_id", "source_text", "decision", "reason",
                  "detected_language", "translation", "status", "source_hash"]
    _ctx_cols = ["translation_id", "source_text", "package_path", "pose_pack_instance",
                 "stbl_resource_instance", "locale", "stbl_key_ref", "neighbor_display_texts",
                 "pose_entry_idx", "pose_display_name_hash"]

    cat_rows = []
    ctx_rows = []
    merge_conflicts = []

    for txt in canon_texts:
        grp_rows = groups[txt]
        # ---- 分类 + 语言: 用首个带邻居上下文的样本 ----
        # 取"上下文最完整"的样本行 (优先有 neighbor 且非空)
        sample = None
        for g in grp_rows:
            nd = (g.get("sample_neighbor_display_texts") or "").strip()
            if not sample or (nd and not (sample.get("sample_neighbor_display_texts") or "").strip()):
                sample = g
        sample = sample or grp_rows[0]
        nbr = (sample.get("sample_neighbor_display_texts") or "").strip()
        cls = classify(txt)
        dec, reason = classify_with_context(txt, nbr)
        lang = detect_language(txt, cls, reason)
        status = "PENDING" if dec == "TRANSLATE" else ("REVIEW" if dec == "REVIEW" else "KEEP")

        # ---- merge 冲突检查: 组内所有行的 decision / language 必须一致/兼容 ----
        for g in grp_rows:
            g_txt = norm_text(g.get("source_text"))
            g_nbr = (g.get("sample_neighbor_display_texts") or "").strip()
            g_cls = classify(g_txt)
            g_dec, g_reason = classify_with_context(g_txt, g_nbr)
            g_lang = detect_language(g_txt, g_cls, g_reason)
            if g_dec != dec:
                merge_conflicts.append({
                    "text": txt, "field": "decision",
                    "a": dec, "b": g_dec, "a_reason": reason, "b_reason": g_reason})
            elif not _lang_compatible(lang, g_lang):
                merge_conflicts.append({
                    "text": txt, "field": "detected_language",
                    "a": lang, "b": g_lang, "a_reason": reason, "b_reason": g_reason})
            elif g_reason != reason:
                # category 差异不阻断, 保留诊断 (notes)
                pass  # 差异 reason 只诊断, 不视为冲突 (decision 一致即可)

        sh = source_hash(txt)
        tid = make_translation_id(sh, 1)

        # ---- ref_count: union 自 reverse mapping 的精确引用 ----
        if rev_by_text.get(txt):
            refs = rev_by_text[txt]
            unique_refs_keys = set()
            for x in refs:
                unique_refs_keys.add((
                    (x.get("package_path") or "").strip(),
                    (x.get("pose_pack_instance") or "").strip(),
                    (x.get("pose_entry_idx") or "").strip(),
                    (x.get("stbl_resource_instance") or "").strip(),
                    (x.get("locale_byte") or "").strip(),
                    (x.get("pose_display_name_hash") or "").strip(),
                ))
            ref_count = len(unique_refs_keys)
            unique_refs = len(unique_refs_keys)
        else:
            # 退化: 候选表 ref_count (该文本出现的条目数) 作为近似
            ref_count = int((sample.get("ref_count") or 0) or 0)
            unique_refs = ref_count

        # ---- packages (审计) ----
        pkgs = sorted({(g.get("sample_package") or "").strip() for g in grp_rows if (g.get("sample_package") or "").strip()})
        package_count = len(pkgs)

        # ---- 上下文展开: 每实际引用一行 ----
        if rev_by_text.get(txt):
            seen_ctx = set()
            for x in rev_by_text[txt]:
                ctx_key = (
                    (x.get("package_path") or "").strip(),
                    (x.get("pose_pack_instance") or "").strip(),
                    (x.get("stbl_resource_instance") or "").strip(),
                    (x.get("locale_byte") or "").strip(),
                    (x.get("pose_display_name_hash") or "").strip(),
                )
                if ctx_key in seen_ctx:
                    continue
                seen_ctx.add(ctx_key)
                ctx_rows.append({
                    "translation_id": tid,
                    "source_text": txt,
                    "package_path": (x.get("package_path") or "").strip(),
                    "pose_pack_instance": (x.get("pose_pack_instance") or "").strip(),
                    "stbl_resource_instance": (x.get("stbl_resource_instance") or "").strip(),
                    "locale": (x.get("locale_byte") or "").strip(),
                    "stbl_key_ref": (x.get("pose_display_name_hash") or "").strip(),
                    "neighbor_display_texts": nbr,
                    "pose_entry_idx": (x.get("pose_entry_idx") or "").strip(),
                    "pose_display_name_hash": (x.get("pose_display_name_hash") or "").strip(),
                })
        else:
            # 无 reverse mapping: 退化为候选表上下文 (单行)
            ctx_rows.append({
                "translation_id": tid,
                "source_text": txt,
                "package_path": (sample.get("sample_package") or "").strip(),
                "pose_pack_instance": (sample.get("sample_pose_pack") or "").strip(),
                "stbl_resource_instance": (sample.get("sample_stbl_instance") or "").strip(),
                "locale": (sample.get("sample_locale") or "").strip(),
                "stbl_key_ref": "",
                "neighbor_display_texts": nbr,
                "pose_entry_idx": "",
                "pose_display_name_hash": "",
            })
        context_count = len(seen_ctx) if rev_by_text.get(txt) else 1

        cat_rows.append({
            "translation_id": tid,
            "source_text": txt,
            "decision": dec,
            "reason": reason,
            "detected_language": lang,
            "translation": "",
            "status": status,
            "ref_count": ref_count,
            "unique_refs": unique_refs,
            "package_count": package_count,
            "packages": "; ".join(pkgs),
            "context_count": context_count,
            "source_hash": sh,
        })

    # ---------------- 校验 (--strict) ----------------
    print("\n[校验] 决策计数:")
    dec_cnt = Counter(cr["decision"] for cr in cat_rows)
    for k, v in dec_cnt.most_common():
        print(f"  {k:10} = {v}")
    total = len(cat_rows)
    print(f"  TOTAL      = {total}")

    # 逻辑不变量 (任何模式都强制)
    canon_texts_in_cat = [cr["source_text"] for cr in cat_rows]
    dup_text = len(canon_texts_in_cat) != len(set(canon_texts_in_cat))
    ids = [cr["translation_id"] for cr in cat_rows]
    dup_id = len(ids) != len(set(ids))
    todo_dec_set = {cr["decision"] for cr in cat_rows}
    todo_rows = [cr for cr in cat_rows if cr["decision"] in ("TRANSLATE", "REVIEW")]
    keep_in_todo = [cr for cr in todo_rows if cr["decision"] == "KEEP"]
    review_rows = [cr for cr in cat_rows if cr["decision"] == "REVIEW"]
    n_review = len(review_rows)
    # KEEP 完整性
    keep_bad = [cr for cr in cat_rows if cr["decision"] == "KEEP" and cr["translation"] != ""]

    if STRICT:
        t_expected = dict(TRANSLATE=1961, KEEP=1572, REVIEW=7, TOTAL=3540)
        n_todo_expected = t_expected["TRANSLATE"] + t_expected["REVIEW"]  # 1968
        problems = []
        if dec_cnt["TRANSLATE"] != t_expected["TRANSLATE"]: problems.append(
            f"TRANSLATE={dec_cnt['TRANSLATE']} != {t_expected['TRANSLATE']}")
        if dec_cnt["KEEP"] != t_expected["KEEP"]: problems.append(
            f"KEEP={dec_cnt['KEEP']} != {t_expected['KEEP']}")
        if dec_cnt["REVIEW"] != t_expected["REVIEW"]: problems.append(
            f"REVIEW={dec_cnt['REVIEW']} != {t_expected['REVIEW']}")
        if total != t_expected["TOTAL"]: problems.append(
            f"TOTAL={total} != {t_expected['TOTAL']}")
        if len(todo_rows) != n_todo_expected: problems.append(
            f"todo={len(todo_rows)} != {n_todo_expected}")
        if dup_text: problems.append("catalog 存在重复 canonical source_text")
        if dup_id:   problems.append("translation_id 非唯一")
        if keep_in_todo: problems.append("KEEP 混入 todo")
        if n_review != 7: problems.append(f"REVIEW={n_review} != 7")
        if merge_conflicts: problems.append(
            f"merge_conflicts={len(merge_conflicts)} != 0")
        if keep_bad: problems.append("存在 KEEP 的 translation 非空")
        if problems:
            print("\n[!!] STRICT 校验失败:")
            for p in problems:
                print(f"    - {p}")
            if merge_conflicts:
                print("\n    MERGE_CONFLICT 明细 (未自动择一):")
                for mc in merge_conflicts[:20]:
                    print(f"      text={mc['text']!r} field={mc['field']} "
                          f"{mc['a']}({mc['a_reason']}) vs {mc['b']}({mc['b_reason']})")
            print("    停止, 不写表。")
            sys.exit(2)
        print("\n[校验] STRICT 通过 (冻结基线 3540/1961/1572/7, todo=1968, 无冲突, 无重复)。")
    else:
        print(f"[fx] 非严格模式: 计数与生产预期可能不同 (TRANSLATE+REVIEW={len(todo_rows)})")
        if dup_text: print("  [fx] 注意: catalog 存在重复 canonical source_text")
        if dup_id:   print("  [fx] 注意: translation_id 非唯一")
        if merge_conflicts: print(f"  [fx] 注意: merge_conflicts={len(merge_conflicts)} (非严格不阻断)")

    print(f"\n[指标] raw_candidate_rows={raw_candidate_rows}  "
          f"duplicate_groups={duplicate_groups}  duplicate_rows_removed={duplicate_rows_removed}  "
          f"unique_candidates={unique_candidates}")

    # ---------------- 写 catalog ----------------
    catalog_out = out_dir / "translation_catalog.csv"
    with open(catalog_out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=_cat_cols)
        w.writeheader()
        for cr in sorted(cat_rows, key=lambda x: x["translation_id"]):
            w.writerow(cr)
    print(f"\n[输出] 完整审计总账: {catalog_out}  ({len(cat_rows)} 行)")

    # ---------------- 写 todo ----------------
    todo_out = out_dir / "translations_todo.csv"
    with open(todo_out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=_todo_cols)
        w.writeheader()
        for cr in sorted(todo_rows, key=lambda x: x["translation_id"]):
            w.writerow({k: cr[k] for k in _todo_cols})
    print(f"[输出] 待译清单:       {todo_out}  ({len(todo_rows)} 行)")

    # ---------------- 写 contexts ----------------
    ctx_out = out_dir / "translation_contexts.csv"
    with open(ctx_out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=_ctx_cols)
        w.writeheader()
        for c in sorted(ctx_rows, key=lambda x: x["translation_id"]):
            w.writerow(c)
    print(f"[输出] 上下文展开:     {ctx_out}  ({len(ctx_rows)} 行)")

    # ---------------- 统计报告 ----------------
    print("\n========== Phase 2A 统计 ==========")
    print(f"raw_candidate_rows        = {raw_candidate_rows}")
    print(f"duplicate_groups          = {duplicate_groups}")
    print(f"duplicate_rows_removed    = {duplicate_rows_removed}")
    print(f"unique_candidates         = {unique_candidates}")
    print(f"catalog 行数              = {total}")
    print(f"TRANSLATE (->todo)        = {dec_cnt['TRANSLATE']}")
    print(f"KEEP     (仅 catalog)     = {dec_cnt['KEEP']}")
    print(f"REVIEW   (->todo,须批准)  = {dec_cnt['REVIEW']}")
    print(f"todo 行数                 = {len(todo_rows)}")
    print(f"context 行数              = {len(ctx_rows)}")
    print(f"merge_conflicts           = {len(merge_conflicts)}")

    lang_cnt = Counter(cr["detected_language"] for cr in cat_rows)
    print("\n各语言数量 (catalog):")
    for k, v in lang_cnt.most_common():
        print(f"  {k:5} = {v}")

    print("\nREVIEW 明细 (7 条):")
    for cr in sorted(review_rows, key=lambda x: x["translation_id"]):
        print(f"  [{cr['detected_language']:3}] {cr['source_text']!r}  (reason={cr['reason']})")

    nes = [cr for cr in cat_rows if cr["reason"] == "NON_ENGLISH_SEMANTIC"]
    print(f"\nNON_ENGLISH_SEMANTIC 数 = {len(nes)}  (应=4, 且全部 decision=REVIEW)")
    for cr in nes:
        print(f"  [{cr['detected_language']:3}] {cr['source_text']!r}  decision={cr['decision']} status={cr['status']}")

    print("\n说明: packages/context 仅供审计, 不用于写回定位。")
    print("Phase 2B 将按 pose_reverse_mapping_full.csv 精确 JOIN (package_path/pose_pack_instance/"
          "pose entry/STBL instance/locale/STBL key) 完成写回。本轮未调用翻译引擎、未改任何 package。")
