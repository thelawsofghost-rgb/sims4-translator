#!/usr/bin/env python3
"""Phase 2A 前置: 100 条代表性样本抽取 + 轻量文本类型分类 (只读, 不写 package)。

目的: 正式批量生成 translations.csv 前, 先审 100 条样本文风与分类是否符合预期。

分类 (轻量启发式, 供人工确认; 作者/角色/品牌名默认保留不硬翻):
  ENGLISH_SEMANTIC   可读英文短语/姿势描述 (送翻译)
  PROPER_NAME        疑似人名/作者名/角色名/品牌 (保留, 默认不译)
  NON_ENGLISH        非英文 (德语/法语/韩文/日文等)
  SYMBOL_OR_MIXED    符号/混合/难以归类的
  NUMERIC_IN_NAME    带数字但含语义 (e.g. "Pose 2 variant") —— 与纯序号码分开

translation_id 预览: candidate 表先给 source_text+context 分组示意,
  正式 translations.csv 将用 translation_id (source_text+context_group) 承载拆分。

输入: output/pose_translation_candidates.csv (3567 语义候选, 列:
       source_text, ref_count, unique_keys, sample_package, sample_pose_pack,
       sample_stbl_instance, sample_locale, sample_neighbor_poses)
输出: output/translation_samples_100.csv
"""
import sys, csv, re
from pathlib import Path
from collections import Counter, defaultdict

out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "D:/projects/sims4_trans/output")
cand_csv = out_dir / "pose_translation_candidates.csv"

rows = []
with open(cand_csv, encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        rows.append(r)
print(f"候选总数: {len(rows)}")

# ---------------- 轻量分类 ----------------
def is_cjk(s):  return any('\u4e00' <= c <= '\u9fff' for c in s)
def is_kr(s):   return any('\uac00' <= c <= '\ud7af' or '\u3130' <= c <= '\u318f' for c in s)
def is_jp(s):   return any('\u3040' <= c <= '\u30ff' for c in s)
CJK_REDUCE = str.maketrans("", "", "\u4e00-\u9fff\uac00-\ud7af\u3040-\u30ff\u3000-\u303f")
def ascii_alpha(s): return re.sub(r"[^A-Za-z]", "", s)

# 作者/角色名常见特征: 姓名大小写 (PascalCase 多词), 含 . 或 ' 或 &, 无小写动词短语
_KNOWN_AUTHOR_TOKENS = {"sims","sim","pose","poser","studio","cc","mod","by","the","and","of","for",
                        "meme","pack","collection","vol","set","part","v","mvp","tutorial"}

def classify(s: str) -> str:
    t = (s or "").strip()
    if not t:
        return "EMPTY"
    # 方向/短动词白名单 -> 语义 (Left/Right/Sit/Stand...)
    _WL = {"left","right","top","bottom","front","back","up","down","sit","stand",
           "in","out","on","off","over","under","kneel","lay","lie","pose","hold"}
    if t.lower() in _WL:
        return "ENGLISH_SEMANTIC"
    alpha = ascii_alpha(t)
    if not alpha:
        if any(c.isdigit() for c in t):
            return "SYMBOL_OR_MIXED"
        if is_cjk(t) or is_kr(t) or is_jp(t):
            return "NON_ENGLISH"
        return "SYMBOL_OR_MIXED"
    # 非拉丁脚本
    n_nonlat = sum(1 for c in t if ord(c) > 127)
    if n_nonlat >= 2:
        return "NON_ENGLISH" if (is_cjk(t) or is_kr(t) or is_jp(t)) else "SYMBOL_OR_MIXED"
    # 独立数字 token (空格分隔或边界) -> NUMERIC_IN_NAME; 单词内嵌数字 (a2o/t0nischwartz) 不算
    standalone_num = bool(re.search(r"(^|\s)\d+(\s|$)", t)) or bool(re.fullmatch(r"\d+[A-Za-z]+|[A-Za-z]+\d+", t))
    FUNC = {"a","an","the","on","off","in","out","up","down","to","of","for","with",
            "by","and","or","at","from","over","under","into","onto","as","is","be",
            "while","when","after","before","during","her","his","my","your","on","a"}
    words = re.findall(r"[A-Za-z]+", t)
    lower = t.lower()
    has_func = any(w.lower() in FUNC for w in words)
    # PROPER_NAME: 全大写单词串, 无功能词, 且非明显动词短语; 单标题词不在常用英文词表
    title_words_upper = len(words) >= 1 and all(w[0].isupper() for w in words)
    common = {"left","right","kiss","sit","stand","pose","face","hair","out","in",
              "hold","hug","bed","chair","table","floor","wall","dance","walk","run"}
    if title_words_upper and not has_func:
        # 若含 pose/姿势相关常用词或为超长内部名 -> 语义; 否则像人名
        if len(words) >= 2 and len(t) <= 28 and not any(w.lower() in common for w in words):
            return "PROPER_NAME"
        if len(words) == 1 and len(words[0]) <= 15 and words[0].lower() not in common:
            return "PROPER_NAME"
    if has_func:
        return "ENGLISH_SEMANTIC"
    if standalone_num:
        return "NUMERIC_IN_NAME"
    if has_small := any(c.islower() for c in t):
        return "ENGLISH_SEMANTIC"
    return "ENGLISH_SEMANTIC"

# 打分类
for r in rows:
    r["_cls"] = classify(r["source_text"])
    r["_ref"] = int(r.get("ref_count") or 0)

cls_dist = Counter(r["_cls"] for r in rows)
print("\n类型分布 (3567 候选):")
for k, v in cls_dist.most_common():
    print(f"  {k:18} = {v}")

# ---------------- 分层抽样 100 条 ----------------
samples = []
seen = set()

def pick(pred, n, label):
    got = 0
    for r in rows:
        if got >= n:
            break
        if r["source_text"] not in seen and pred(r):
            samples.append((label, r))
            seen.add(r["source_text"])
            got += 1

# 1) 普通短句 (ENGLISH_SEMANTIC, 短)
short_sem = [r for r in rows if r["_cls"] == "ENGLISH_SEMANTIC" and len(r["source_text"]) <= 24]
pick(lambda r: r in short_sem, 18, "普通短句")

# 2) 长姿势名
long_sem = [r for r in rows if r["_cls"] == "ENGLISH_SEMANTIC" and len(r["source_text"]) > 40]
pick(lambda r: r in long_sem, 15, "长姿势名")

# 3) Left / Right 及方向类
pick(lambda r: r["source_text"].strip().lower() in {"left","right","top","bottom","front","back"}, 6, "Left/Right/方向")

# 4) 疑似人名/作者名
pick(lambda r: r["_cls"] == "PROPER_NAME", 14, "疑似人名/作者")

# 5) 带数字的名称 (NUMERIC_IN_NAME)
pick(lambda r: r["_cls"] == "NUMERIC_IN_NAME", 12, "带数字名称")

# 6) 高重复率文本 (ref_count 高)
highref = sorted([r for r in rows if r["source_text"] not in seen], key=lambda r: -r["_ref"])
pick(lambda r: r in highref and r["_ref"] >= 3, 15, "高重复率")

# 7) 成人/姿势术语 (含关键词)
adult_kw = re.compile(r"(sex|kiss|fuck|fuckin|blow|oral|thrust|penetrat|nude|strip|bdsm|mastur|orgasm|cum|erect|arous|bondage|vibrat|fellat|cunniling|anal|breast|ass\b|booty|pussy|dick|whore|lust|seduct|flirt|tease)", re.I)
pick(lambda r: bool(adult_kw.search(r["source_text"])), 12, "成人/姿势术语")

# 8) 其他 (补足到 100)
need = 100 - len(samples)
for r in rows:
    if need <= 0:
        break
    if r["source_text"] not in seen:
        samples.append(("其他/补充", r))
        seen.add(r["source_text"])
        need -= 1

# ---------------- 输出 ----------------
print(f"\n抽样总数: {len(samples)} / 100")
out_cols = ["sample_group", "text_class", "source_text", "ref_count", "unique_keys",
            "sample_package", "sample_pose_pack", "sample_stbl_instance", "sample_locale",
            "neighbor_poses"]
sample_out = out_dir / "translation_samples_100.csv"
with open(sample_out, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=out_cols)
    w.writeheader()
    for grp, r in samples:
        w.writerow({
            "sample_group": grp,
            "text_class": r["_cls"],
            "source_text": r["source_text"],
            "ref_count": r.get("ref_count", ""),
            "unique_keys": r.get("unique_keys", ""),
            "sample_package": r.get("sample_package", ""),
            "sample_pose_pack": r.get("sample_pose_pack", ""),
            "sample_stbl_instance": r.get("sample_stbl_instance", ""),
            "sample_locale": r.get("sample_locale", ""),
            "neighbor_poses": r.get("sample_neighbor_poses", ""),
        })
print(f"已写出: {sample_out}")
print("\n按样本组统计:")
for grp, cnt in Counter(g for g, _ in samples).most_common():
    print(f"  {grp:16} = {cnt}")
