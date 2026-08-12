#!/usr/bin/env python3
"""Phase 2A 前置: 100 条代表性样本抽取 + 轻量文本类型分类 (只读, 不写 package)。

目的: 正式批量生成 translations.csv 前, 先审 100 条样本文风与分类是否符合预期。

分类 (轻量启发式, 供人工确认; 作者/角色/品牌名默认保留不硬翻):
  ENGLISH_SEMANTIC    可读英文短语/姿势描述 (送翻译)
  SEMANTIC_WITH_NUM   带数字但含真实语义 (翻文字, 保序号; e.g. "Bed 2 - Kissing Belly")
  NON_SEMANTIC_TAG    编号/序号标签 非语义 (e.g. "1" "2.1" "F2" "3M" "Pose 1") 默认不译
  PROPER_NAME         强势专名 (作者 handle / 角色 / 品牌 / 账号格式) 保留不译
  SYMBOL_OR_MIXED     符号/混合/难以归类
  SEMANTIC_UNCERTAIN  拿不准是否专名→标此, 不硬猜 (宁可不确定, 不误跳 PROPER_NAME)
  NON_ENGLISH         非英文 (德语/法语/韩文/日文等)

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

# ---------------- 新分类规则 (2026-08-12 重构, 用户四方向确认后) ----------------
# 目标类别: ENGLISH_SEMANTIC / SEMANTIC_WITH_NUM / NON_SEMANTIC_TAG /
#           PROPER_NAME / SYMBOL_OR_MIXED / SEMANTIC_UNCERTAIN (另有 NON_ENGLISH)

# 常见英文功能词/介词 -> 有功能词=语义文本
_FUNC = {"a","an","the","on","off","in","out","up","down","to","of","for","with",
         "by","and","or","at","from","over","under","into","onto","as","is","be",
         "while","when","after","before","during","her","his","my","your","are","was","not"}

# 位置/方向/动作短词白名单: 一定是语义
_DIR_SEM = {"left","right","top","bottom","front","back","up","down","sit","stood","stand",
            "kneel","lay","lie","lying","sitting","standing","in","out","on","over","under"}

# 姿势/成人相关强语义词: 命中即语义(即便标题大小写/单短词)
_SEM_WORD = {"kiss","kissing","kicked","kicking","open","opening","door","wall","bed","stairs","stair",
             "belly","breasts","breast","tease","teasing","massage","flirty","sass","couch","love",
             "ride","riding","clean","drunk","drunk","confession","goth","emotion","argue","argument",
             "listen","listening","music","sad","male","female","punch","pulling","dance","dancing",
             "thrust","oral","nude","strip","orgasm","lick","rub","grind","spank"}

# 纯编号标签正则 -> NON_SEMANTIC_TAG
#   "1" "2.1" "F2" "3M" "1F" "Pose 1" "Pose 2" "3A" "4B" "2b" "m1" "f4"
_TAG_STRICT = re.compile(r"^(?:\d+(?:\.\d+)?|[FfMm][0-9]+(?:[A-Za-z]*)|(?:[Pp]ose\s*)?\d+[A-Za-z]*[A-Za-z]*)$")
# 宽松补充: 纯 "字母+数字" 或 "数字+字母" 无空格且整体较短
_TAG_ALNUM = re.compile(r"^(?=.{1,4}$)[A-Za-z]*[0-9][A-Za-z0-9]*$")
_TAG_POSE = re.compile(r"^pose\s*\d+$", re.I)

# 专名强证据: 账号格式 / 作者 handle / 明显标识符 (含数字字母混合且无空格的笔名)
#   t0nischwartz / Simmerianne93 / mdrayvv / GREENSOUL(?), 但 GREENSOUL 更像作者封名
_HANDLE_LIKE = re.compile(r"^(?=.*[0-9])(?=.*[A-Za-z])[A-Za-z0-9_.-]{1,20}$")
# 全小写短词串可能是作者小写笔名 (若不在姿势词表)


def _is_non_semantic_tag(t: str) -> bool:
    if _TAG_POSE.match(t):
        return True
    if _TAG_STRICT.match(t):
        return True
    if _TAG_ALNUM.match(t):
        return True
    return False


def classify(s: str) -> str:
    t = (s or "").strip()
    if not t:
        return "EMPTY"
    if is_cjk(t) or is_kr(t) or is_jp(t):
        return "NON_ENGLISH"
    n_nonlat = sum(1 for c in t if ord(c) > 127)
    if n_nonlat >= 2:
        return "NON_ENGLISH" if (is_cjk(t) or is_kr(t) or is_jp(t)) else "SYMBOL_OR_MIXED"

    tl = t.lower()
    words = re.findall(r"[A-Za-z]+", t)
    has_func = any(w.lower() in _FUNC for w in words)
    has_sem_word = any(w.lower().rstrip("s") in _SEM_WORD or w.lower() in _SEM_WORD for w in words)

    # 1) 方向上方向/动作短词白名单 -> 语义
    if tl in _DIR_SEM:
        return "ENGLISH_SEMANTIC"

    # 2) 纯编号标签 -> 不译
    if _is_non_semantic_tag(t):
        return "NON_SEMANTIC_TAG"

    # 3) 账号/作者 handle 强证据: 字母+数字无空格短标识符, 且不在常见语义词表
    if _HANDLE_LIKE.match(t) and not tl in _SEM_WORD and not tl in _DIR_SEM:
        # 排除形如 "Pose1" 这类 = 已被 _TAG_POSE 捕获; 剩下的像是笔名
        return "PROPER_NAME"

    # 4) 带数字但含真实语义 -> SEMANTIC_WITH_NUM (保序号翻文字)
    if re.search(r"\d", t):
        # 只要不是纯标签, 且不是纯 handle, 带数字+有英文词 -> 语义带序号
        if words and (has_func or has_sem_word or len(words) >= 2):
            return "SEMANTIC_WITH_NUM"
        # 带数字但无明确语义证据: 拿不准 -> 语义不确定
        return "SEMANTIC_UNCERTAIN"

    # 5) 英文单词/短语
    if not any(re.match(r"[A-Za-z]", w) for w in words):
        return "SYMBOL_OR_MIXED"

    # 6) 姿势语义词命中 -> 语义 (Flirty/Massage/Tease/Sass/Kiss...)
    if has_sem_word:
        return "ENGLISH_SEMANTIC"

    # 7) 有功能词 (介词/连词) -> 语义短语
    if has_func:
        return "ENGLISH_SEMANTIC"

    # 8) 多词短语 (>=2 词) 无功能词 -> 语义 (标题大小写不再当专名证据)
    if len(words) >= 2 and len(t) <= 40:
        return "ENGLISH_SEMANTIC"

    # 9) 单短词(非语义词表、非功能词): 无法确认是专名还是姿势名 -> 语义不确定
    if len(words) == 1 and len(words[0]) <= 15:
        return "SEMANTIC_UNCERTAIN"

    # 10) 其他(长串内部名等) -> 语义
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
long_sem = [r for r in rows if r["_cls"] in ("ENGLISH_SEMANTIC", "SEMANTIC_WITH_NUM") and len(r["source_text"]) > 40]
pick(lambda r: r in long_sem, 15, "长姿势名")

# 3) Left / Right 及方向类
pick(lambda r: r["source_text"].strip().lower() in {"left","right","top","bottom","front","back"}, 6, "Left/Right/方向")

# 4) 专名 (PROPER_NAME)
pick(lambda r: r["_cls"] == "PROPER_NAME", 10, "专名/作者")

# 5) 带数字语义名 (SEMANTIC_WITH_NUM)
pick(lambda r: r["_cls"] == "SEMANTIC_WITH_NUM", 14, "带数字语义名")

# 6) 非语义标签 (NON_SEMANTIC_TAG)
pick(lambda r: r["_cls"] == "NON_SEMANTIC_TAG", 10, "非语义标签")

# 7) 语义不确定 (SEMANTIC_UNCERTAIN)
pick(lambda r: r["_cls"] == "SEMANTIC_UNCERTAIN", 8, "语义不确定")

# 8) 成人/姿势术语 (含关键词)
adult_kw = re.compile(r"(sex|kiss|fuck|fuckin|blow|oral|thrust|penetrat|nude|strip|bdsm|mastur|orgasm|cum|erect|arous|bondage|vibrat|fellat|cunniling|anal|breast|ass\b|booty|pussy|dick|whore|lust|seduct|flirt|tease|massage|sass)", re.I)
pick(lambda r: bool(adult_kw.search(r["source_text"])), 12, "成人/姿势术语")

# 9) 其他 (补足到 100)
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
