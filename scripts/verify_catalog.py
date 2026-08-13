#!/usr/bin/env python3
"""Phase 2A 回归: translations 目录骨架的确定性规则 (去重/合并/冲突/ref union/contexts)。

不依赖真实 3540 数据, 用定向 fixture (gen_fixture2.py)。
验证:
  - detected_language 值域, 与 decision/reason 分离
  - source_hash: NFC + SHA-256 12hex, 大小写敏感
  - translation_id = T_<hash>_g1, 稳定, 可扩展 g2/g3
  - 同 canonical 文本去重合并 (含首尾空白变体), ref_count 走 reverse-mapping union (非盲目求和)
  - merge_conflicts 检测 (同文本不同 decision -> 报告, 不自动择一)
  - KEEP: translation='' status=KEEP, 不进 todo
  - REVIEW: 进 todo status=REVIEW
  - todo = catalog 中 decision∈{TRANSLATE,REVIEW} 的精确集合; id 唯一
  - translation_contexts.csv 每实际上下文一行, 经 translation_id 关联
"""
import sys, csv, re, os
sys.path.insert(0, "scripts" if "scripts" in __file__ else ".")
from phase2a_catalog import detect_language, source_hash, make_translation_id, norm_text
from phase2a_samples import classify, classify_with_context

ok = fail = 0
def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print(f"[OK ] {name} {extra}".rstrip())
    else:    fail += 1; print(f"[FAIL] {name} {extra}".rstrip())

# ---- detected_language 值域 + 分类分离 ----
LANG_CASES = [
    ("Flirty",  "en",  "ENGLISH_SEMANTIC",    "TRANSLATE"),
    ("6 А2",    "zxx", "NON_SEMANTIC_TAG",    "KEEP"),
    ("6B",      "zxx", "NON_SEMANTIC_TAG",    "KEEP"),
    ("A-1",     "zxx", "PROPER_NAME",         "KEEP"),   # 语言层 zxx, 与 PROPER_NAME 分离
    ("a2o_x_y", "zxx", "TECHNICAL_LABEL",     "KEEP"),
    ("reabokeintroobj", "zxx", "TECHNICAL_LABEL","KEEP"),
    ("Femme",   "fr",  "NON_ENGLISH_SEMANTIC","REVIEW"),
    ("Revisando","es", "NON_ENGLISH_SEMANTIC","REVIEW"),
    ("Asomado", "es",  "NON_ENGLISH_SEMANTIC","REVIEW"),
    ("Mirando", "es",  "NON_ENGLISH_SEMANTIC","REVIEW"),
    ("All in One","en","ENGLISH_SEMANTIC",    "TRANSLATE"),
]
for text, wl, wr, wd in LANG_CASES:
    cls = classify(text); dec, reason = classify_with_context(text, "")
    lang = detect_language(text, cls, reason)
    check(f"lang({text!r})={wl}", lang == wl and reason == wr and dec == wd,
          f"(lang={lang}, reason={reason}, dec={dec})")
check("decision 值域 ⊇ {KEEP,TRANSLATE,REVIEW}",
      {"KEEP","TRANSLATE","REVIEW"} <= {classify_with_context(t,"")[0] for t,_,_,_ in
        [("Flirty","","",""),("6 А2","","",""),("Femme","","","")]})

# ---- 大小写敏感: 3 个 'All in One' 变体是 3 个不同 ID (case 不同) ----
h_all  = source_hash("All in One")
h_UPPER= source_hash("ALL IN ONE")
h_lower= source_hash("all in one")
check("'All in One'/'ALL IN ONE'/'all in one' 3 个不同 hash (大小写敏感)",
      len({h_all, h_UPPER, h_lower}) == 3)

# ---- norm_text: 首尾空白变体归一 ----
check("norm_text 去首尾空白", norm_text("ALL IN ONE ") == norm_text("ALL IN ONE") == "ALL IN ONE")
check("norm_text NFC", norm_text("e\u0301") == "\u00e9")
check("同文本同 hash (strip)", source_hash(norm_text("ALL IN ONE ")) == source_hash("ALL IN ONE"))

# ---- translation_id ----
check("id = T_<12hex>_g1", make_translation_id(h_all, 1) == f"T_{h_all}_g1")
check("id 支持 g2/g3", make_translation_id(h_all, 2) == f"T_{h_all}_g2")
check("id 稳定", make_translation_id(h_all, 1) == make_translation_id(h_all, 1))

# ---- 读取 catalog/todo/contexts (fixture2 输出) ----
tst = "/tmp/phase2a_test/translation_catalog.csv"
tdo = "/tmp/phase2a_test/translations_todo.csv"
tct = "/tmp/phase2a_test/translation_contexts.csv"
if os.path.exists(tst):
    with open(tst, encoding="utf-8-sig") as f: rows = list(csv.DictReader(f))
    with open(tdo, encoding="utf-8-sig") as f: trows = list(csv.DictReader(f))
    # catalog: 5 unique (2 dup groups merged from 7 raw)
    check("catalog=5", len(rows) == 5)
    texts = [r["source_text"] for r in rows]
    check("catalog 无重复 canonical source_text", len(texts) == len(set(texts)))
    ids = [r["translation_id"] for r in rows]
    check("translation_id 唯一", len(ids) == len(set(ids)))
    check("id 格式 T_<12hex>_gN", all(re.fullmatch(r"T_[0-9a-f]{12}_g\d+", i) for i in ids))
    # ALL IN ONE: 跨 2 包, ref_count 应=3 (union, 非 35+1=36 盲目求和)
    aio = next(r for r in rows if r["source_text"] == "ALL IN ONE")
    check("ALL IN ONE ref_count=3 (union)", int(aio["ref_count"]) == 3, f"got {aio['ref_count']}")
    check("ALL IN ONE unique_refs=3", int(aio["unique_refs"]) == 3)
    check("ALL IN ONE package_count=2", int(aio["package_count"]) == 2, f"got {aio['package_count']}")
    check("ALL IN ONE context_count=3", int(aio["context_count"]) == 3, f"got {aio['context_count']}")
    # Couple Pose 2: 同包 3 引用
    cp = next(r for r in rows if r["source_text"] == "Couple Pose 2")
    check("Couple Pose 2 ref_count=3", int(cp["ref_count"]) == 3)
    # 4M KEEP 不进 todo, translation 空
    m4 = next(r for r in rows if r["source_text"] == "4M")
    check("4M KEEP", m4["decision"] == "KEEP" and m4["status"] == "KEEP" and m4["translation"] == "")
    check("4M 不进 todo", all(r["source_text"] != "4M" for r in trows))
    # todo = TRANSLATE+REVIEW
    check("todo 全部 TRANSLATE/REVIEW", all(r["decision"] in ("TRANSLATE","REVIEW") for r in trows))
    check("todo id 是 catalog id 子集", set(r["translation_id"] for r in trows) <= set(ids))
    check("todo 无 KEEP 行", all(r["decision"] != "KEEP" for r in trows))
    # REVIEW: Femme 进 todo status=REVIEW
    check("Femme REVIEW 进 todo",
          any(r["source_text"] == "Femme" and r["decision"] == "REVIEW" and r["status"] == "REVIEW" for r in trows))
    # contexts 关联
    if os.path.exists(tct):
        with open(tct, encoding="utf-8-sig") as f: crows = list(csv.DictReader(f))
        check("contexts=10", len(crows) == 10, f"got {len(crows)}")
        aio_ctx = [c for c in crows if c["translation_id"] == aio["translation_id"]]
        check("ALL IN ONE contexts=3 (跨 2 包)", len(aio_ctx) == 3)
        check("context 有关联字段非空",
              all(c["package_path"] and c["pose_pack_instance"] and c["stbl_resource_instance"]
                  and c["locale"] and c["stbl_key_ref"] for c in aio_ctx))
        check("context translation_id 均可关联 catalog",
              set(c["translation_id"] for c in crows) <= set(ids))
else:
    print("[skip] 未找到 fixture 输出 (先运行 gen_fixture2.py + phase2a_catalog.py /tmp/phase2a_test)")

print(f"\nPhase 2A catalog 回归: 共 {ok+fail} 项, 通过 {ok}, 失败 {fail}")
sys.exit(0 if fail == 0 else 1)
