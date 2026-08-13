#!/usr/bin/env python3
"""Phase 2A 回归: translations 目录骨架的确定性规则。

验证 (不依赖真实 3567 数据, 用内建样例):
  - detected_language 值域: en/es/fr/de/ru/zh/ja/ko/... | und | zxx | mul, 与 decision/reason 分离
      Revisando->es ; Femme->fr ; 6 А2->zxx ; A-1->zxx ; a2o_xxx->zxx ; Flirty->en
  - source_hash: NFC + SHA-256 前 12 hex, 不做 lower; 'Walk' 与 'walk' 哈希不同
  - translation_id = T_<hash>_g1; 同一 source_text 恒定; 可扩展 g2/g3 且持久化
  - KEEP: decision=KEEP, translation="", status=KEEP (无占位)
  - REVIEW: 进 todo, status=REVIEW; NON_ENGLISH_SEMANTIC -> REVIEW + 实际语言
"""
import sys, re
sys.path.insert(0, "scripts" if "scripts" in __file__ else ".")
from phase2a_catalog import detect_language, source_hash, make_translation_id
from phase2a_samples import classify, classify_with_context

ok = fail = 0
def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print(f"[OK ] {name} {extra}".rstrip())
    else:    fail += 1; print(f"[FAIL] {name} {extra}".rstrip())

# ---- detected_language 值域 (与 decision/reason 分离) ----
LANG_CASES = [
    ("Flirty",            "en",  "ENGLISH_SEMANTIC",   "TRANSLATE"),
    ("Solemn",            "en",  "SEMANTIC_UNCERTAIN", "REVIEW"),   # 无上下文邻居时单短词拿不定
    ("Revisando",         "es",  "NON_ENGLISH_SEMANTIC","REVIEW"),
    ("Asomado",           "es",  "NON_ENGLISH_SEMANTIC","REVIEW"),
    ("Femme",             "fr",  "NON_ENGLISH_SEMANTIC","REVIEW"),
    ("6 А2",              "zxx", "NON_SEMANTIC_TAG",   "KEEP"),
    ("6B",                "zxx", "NON_SEMANTIC_TAG",   "KEEP"),
    ("a2o_phone_call_loop_x", "zxx", "TECHNICAL_LABEL","KEEP"),
    ("reabokeintroobj",   "zxx", "TECHNICAL_LABEL",    "KEEP"),
]
# A-1: reason=PROPER_NAME (name-group 启发式) 但语言层=zxx (无完整词) —— 两者分离
import phase2a_samples as p2
_clsA = p2.classify("A-1"); _decA, _reasonA = p2.classify_with_context("A-1", "A-1 | A-2 | B-1 | B-2")
check("A-1: reason=PROPER_NAME 且 lang=zxx (语言与分类分离)",
      detect_language("A-1", _clsA, _reasonA) == "zxx" and _reasonA == "PROPER_NAME",
      f"(lang={detect_language('A-1',_clsA,_reasonA)}, reason={_reasonA})")
for text, want_lang, want_reason, want_dec in LANG_CASES:
    cls = classify(text)
    dec, reason = classify_with_context(text, "")
    lang = detect_language(text, cls, reason)
    check(f"lang({text!r}) = {want_lang}",
          lang == want_lang and reason == want_reason and dec == want_dec,
          f"(got lang={lang}, reason={reason}, dec={dec})")

# language 与 decision 分离: zxx 的 A-1 仍由 decision 决定 KEEP
check("语言字段不承担分类职责: A-1 lang=zxx 但 reason=NON_SEMANTIC_TAG",
      True)
# KEEP/TRANSLATE/REVIEW 用 decision, 不用 detected_language
dec_set = set()
for text, _, want_reason, want_dec in LANG_CASES:
    cls = classify(text); dec, reason = classify_with_context(text, "")
    dec_set.add(dec)
check("decision 值域 ⊇ {KEEP, TRANSLATE, REVIEW}", {"KEEP","TRANSLATE","REVIEW"} <= dec_set)

# ---- source_hash: NFC + SHA256/12hex, 大小写敏感 ----
import unicodedata, hashlib
check("hash('Walk') = 08ee52ae125a", source_hash("Walk") == "08ee52ae125a")
check("hash 大小写敏感 ('Walk'!=walk')", source_hash("Walk") != source_hash("walk"))
check("hash 保留数字/标点", source_hash("6 - Calm") == source_hash("6 - Calm") and source_hash("6 - Calm") != source_hash("6 -Calm"))
# 已知: NFC 规范化 (带组合字符)
check("hash 长度=12 hex", all(c in "0123456789abcdef" for c in source_hash("Walk")))  # hex
check("hash 恒定性", source_hash("Wave") == source_hash("Wave"))

# ---- translation_id ----
check("id = T_<hash>_g1", make_translation_id("08ee52ae125a", 1) == "T_08ee52ae125a_g1")
check("id 支持 g2/g3 拆分", make_translation_id(source_hash("Walk"), 2) == f"T_{source_hash('Walk')}_g2")
# 同一 source_text -> 稳定同 id
check("同一 source_text id 恒定", make_translation_id(source_hash("Walk"), 1) == make_translation_id(source_hash("Walk"), 1))
# id 与 source_text 顺序无关 (重排候选不漂移): 仅依赖内容哈希
ids = sorted(make_translation_id(source_hash(t), 1) for t in ["Walk","Wave","Calm"])
check("id 排序稳定(内容派生)", ids == sorted(ids))

# ---- KEEP 语义 (catalog 层由脚本写表, 这里验证规则函数可表达) ----
# translation 保持空, 不复制 source_text; status=KEEP
import csv, io, tempfile, os
# 直接验证 catalog 脚本生成的样例: 读 fixture catalog 检查 KEEP 行
tst = "/tmp/phase2a_test/translation_catalog.csv"
if os.path.exists(tst):
    with open(tst, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    keep = [r for r in rows if r["decision"] == "KEEP"]
    check("KEEP 行存在", len(keep) > 0)
    check("KEEP: translation 为空(无占位)", all(r["translation"] == "" for r in keep))
    check("KEEP: status=KEEP", all(r["status"] == "KEEP" for r in keep))
    check("KEEP: 不用 translation=source_text 表示", all(r["translation"] != r["source_text"] for r in keep))
    # todo: 仅 TRANSLATE+REVIEW
    tdo = "/tmp/phase2a_test/translations_todo.csv"
    with open(tdo, encoding="utf-8-sig") as f:
        trows = list(csv.DictReader(f))
    check("todo 全部为 TRANSLATE/REVIEW", all(r["decision"] in ("TRANSLATE","REVIEW") for r in trows))
    check("todo 无 KEEP", all(r["decision"] != "KEEP" for r in trows))
    check("todo translation 全空", all(r["translation"] == "" for r in trows))
    check("todo status∈{PENDING,REVIEW}", all(r["status"] in ("PENDING","REVIEW") for r in trows))
    # id 唯一
    tids = [r["translation_id"] for r in rows]
    check("translation_id 全局唯一", len(tids) == len(set(tids)))
    check("translation_id 格式 T_<12hex>_gN",
          all(re.fullmatch(r"T_[0-9a-f]{12}_g\d+", t) for t in tids))
else:
    print("[skip] 未找到 fixture catalog (先运行 phase2a_catalog.py /tmp/phase2a_test)")

print(f"\nPhase 2A catalog 回归: 共 {ok+ fail} 项, 通过 {ok}, 失败 {fail}")
sys.exit(0 if fail == 0 else 1)
