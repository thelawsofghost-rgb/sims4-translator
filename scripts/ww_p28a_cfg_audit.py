#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p28a_cfg_audit.py —— P28A 只读审计 Resource.cfg (Linux/沙箱可测, 纯静态)

分两个模式:
  模式 check  (默认): 只读解析 + 判读, 不修改任何文件.
  模式 propose:      在 check 基础上输出"待追加"的精确 ASCII 行 + 建议 Priority,
                     供 ww_p28a_priority_canary.ps1 使用 (ps1 只追加 audit 输出的确切 token,
                     不复制解析逻辑; 单一事实来源).

对 Resource.cfg 解析:
  * 逐行读取 (UTF-8/ASCII 均可; 失败降级 latin-1 不影响 ASCII 关键字提取)
  * 识别 'Priority <int>' 行 (大小写不敏感), 其后 'PackedFile <pattern>' 归属当前 Priority
  * 计算 min_priority (Sims4: 数值越小 = 优先级越高/越晚加载优先)
  * 对给定相对路径, 用 1 段 '*' 与跨段 '**' 规则做 glob 匹配判读命中

输出 (ASCII, 除文件内容原样引用的路径/规则外):
  RESOURCE_CFG_EXISTS=YES|NO
  RESOURCE_CFG_SHA_BEFORE=<sha256>   (仅文件存在时)
  PRIORITY_MIN=<int>
  PRIORITY_COUNT=<int>
  RULE_COUNT=<int>
  ROOT_PKG_COVERED=YES|NO        (root '*.package' 命中当前 root override)
  SUBDIR_PKG_COVERED=YES|NO      (子目录规则命中 '2026.7.20/' 源包)
  P27_DIR_COVERED=YES|NO         (已存在 P27_Overrides 规则?)
  P27_EXISTING_PRIORITY=<int|0>
  PROPOSED_PRIORITY=<int>        (要新增的高优先级, 必 < min; 若已有更低则复用)
  APPEND_REQUIRED=YES|NO
  APPEND_LINES=<base64>          (待追加的原始 cfg 行, base64 编码, 防止换行/编码歧义)
  VERDICT=OK|FAIL
  REASON=<code>

退出码:
  0 = 可决策 (存在 && 可解析; VERDICT=OK 或已给出 APPEND_REQUIRED)
  2 = cfg 不存在
  3 = 无任何 Priority 规则 (无法建立优先级依据, fail-closed)
  4 = 无法读取

用法:
  python scripts\\ww_p28a_cfg_audit.py check  "<resource.cfg>" ["<root_override.abs>"] ["<subdir_src.abs>"]
  python scripts\\ww_p28a_cfg_audit.py propose "<resource.cfg>" ["<root_override.abs>"] ["<subdir_src.abs>"]
只读; 不改任何文件。
"""
import base64
import hashlib
import os
import re
import sys
from pathlib import PurePath, Path

P27_DIR = "P27_Overrides"
P27_PATTERN = "PackedFile P27_Overrides/*.package"
ROOT_OVERRIDE = "ZZZ_WW_P27_DisplayName_Override.package"   # 旧 root 部署名 (仅判读用)
SUB_SOURCE_SEED = "2026.7.20"                                # 源包子目录 (仅判读用)


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(1 << 16), b""):
            h.update(blk)
    return h.hexdigest().lower()


def parse_cfg(text):
    """返回 (rules, priorities) : rules=[{'prio':int,'pattern':str}...], priorities=set"""
    rules = []
    cur = None
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"(?i)^\s*Priority\s+(\d+)\s*$", line)
        if m:
            # 只认行首关键字, 避免误匹配 PackedFile 内含 Priority 的情况
            cur = int(m.group(1))
            continue
        m2 = re.match(r"(?i)^\s*PackedFile\s+(.+?)\s*$", line)
        if m2:
            if cur is None:
                continue  # 无归属 Priority 的 PackedFile, 忽略 (不参与决策)
            rules.append({"prio": cur, "pattern": m2.group(1)})
    return rules


def glob_match(pattern, rel_path):
    """Sims4 风格小 glob: '*'=单段内任意, '**'=跨段. 返回 bool.
    统一把 '\\' 与 '/' 都视为路径分隔符, 平台无关."""
    def norm(s):
        return s.replace("\\", "/")
    seg_pat = norm(pattern).split("/")
    seg_path = norm(rel_path).split("/")
    return _gm(seg_pat, seg_path)


def _gm(pats, segs):
    if not pats:
        return not segs
    p = pats[0]
    if p == "**":
        # 匹配 0..n 段
        for i in range(len(segs) + 1):
            if _gm(pats[1:], segs[i:]):
                return True
        return False
    if not segs:
        return False
    return _seg(p, segs[0]) and _gm(pats[1:], segs[1:])


def _seg(pat, s):
    # 单段: 支持完整通配 (简化: 以 '**' 或 '*' 处理段内全部, 否则精确)
    if pat in ("*", "**"):
        return True
    # 通用 glob 段匹配 (fnmatch)
    import fnmatch
    return fnmatch.fnmatchcase(s, pat)


def classify(rules, rel_path):
    """返回 (covered:bool, prio:int|None) — 命中规则的最低 prio(数值最小=最强)."""
    best = None
    for r in rules:
        if glob_match(r["pattern"], rel_path):
            if best is None or r["prio"] < best:
                best = r["prio"]
    return (best is not None, best)


def main():
    if len(sys.argv) < 3:
        print("usage: ww_p28a_cfg_audit.py check|propose <resource.cfg> [root_override_abs] [subdir_src_abs]")
        return 2
    mode = sys.argv[1].lower()
    cfg = Path(sys.argv[2])
    root_abs = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
    sub_abs = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else None

    if not cfg.is_file():
        print("RESOURCE_CFG_EXISTS=NO")
        print("VERDICT=FAIL")
        print("REASON=CFG_MISSING")
        return 2

    print("RESOURCE_CFG_EXISTS=YES")
    cfg_sha = sha256_file(cfg)
    print(f"RESOURCE_CFG_SHA_BEFORE={cfg_sha}")

    try:
        text = cfg.read_text(encoding="utf-8")
    except Exception:
        text = cfg.read_text(encoding="latin-1")  # 降级, 不影响 ASCII 关键字
    rules = parse_cfg(text)

    prios = sorted({r["prio"] for r in rules})
    if not prios:
        print("PRIORITY_COUNT=0")
        print("VERDICT=FAIL")
        print("REASON=NO_PRIORITY_RULES")
        return 3
    min_prio = min(prios)
    print(f"PRIORITY_MIN={min_prio}")
    print(f"PRIORITY_COUNT={len(prios)}")
    print(f"RULE_COUNT={len(rules)}")

    # 判读轨迹: root override 相对 Mods 根 = 文件名 (在根, 无子目录)
    root_rel = PurePath(root_abs).name if root_abs else ROOT_OVERRIDE
    sub_rel = str(PurePath(sub_abs)) if sub_abs else f"{SUB_SOURCE_SEED}/{PurePath(sub_abs or 'WW_Nevely42_Animations.package').name}"

    root_cov, root_prio = classify(rules, root_rel)
    sub_cov, sub_prio = classify(rules, sub_rel)
    p27_cov, p27_prio = classify(rules, f"{P27_DIR}/{PurePath(root_abs).name if root_abs else ROOT_OVERRIDE}")

    print(f"ROOT_PKG_COVERED={'YES' if root_cov else 'NO'}")
    print(f"ROOT_PKG_PRIO={root_prio if root_cov else 0}")
    print(f"SUBDIR_PKG_COVERED={'YES' if sub_cov else 'NO'}")
    print(f"SUBDIR_PKG_PRIO={sub_prio if sub_cov else 0}")
    print(f"P27_DIR_COVERED={'YES' if p27_cov else 'NO'} (generic glob)")

    # 专门规则: 显式指向 P27_Overrides 的 PackedFile (非通用 glob 覆盖)
    dedicated_p27 = [r for r in rules if "P27_Overrides" in r["pattern"].replace("\\", "/")]
    ded_p27_prio = min(r["prio"] for r in dedicated_p27) if dedicated_p27 else None
    print(f"P27_DIR_DEDICATED_RULE={'YES' if dedicated_p27 else 'NO'}")
    print(f"P27_DEDICATED_PRIORITY={ded_p27_prio if ded_p27_prio is not None else 0}")

    # 决策: 需要新增高优先级规则吗?
    #   原则: 不盲目追加/不制造无依据规则. 仅当尚无针对 P27_Overrides 的显式规则时新增,
    #   且新 Priority 必须严格低于(数值更小于)当前所有规则的最小值 -> 明确最高优先级.
    append_required = False
    if ded_p27_prio is not None and ded_p27_prio <= min_prio:
        # 已存在专门且优先级不劣于全局最低 -> 复用, 不追加
        proposed = ded_p27_prio
        print("APPEND_REQUIRED=NO")
    else:
        # 无专门规则, 或专门规则优先级不够高 -> 新增一条严格更优的规则
        proposed = max(1, min_prio - 100)
        if proposed >= min_prio or proposed < 1:
            proposed = max(1, min_prio - 1)   # min>1 时取 min-1; min=1 时保持 1
        append_required = True
        # 若已有专门规则但不够优, 提示 (不覆盖, 仍追加一条更优) — 不删原规则
        if ded_p27_prio is not None:
            print("NOTE=EXISTING_DEDICATED_P27_RULE_LOWER_THAN_MIN")
        print("APPEND_REQUIRED=YES")

    print(f"PROPOSED_PRIORITY={proposed}")

    # 待追加行 (base64, 避免编码/换行歧义) —— 始终给出, ps1 仅在 APPEND_REQUIRED=YES 时追加
    lines_txt = f"Priority {proposed}\nPackedFile {P27_DIR}/*.package\n"
    enc = base64.b64encode(lines_txt.encode("utf-8")).decode("ascii")
    print(f"APPEND_LINES={enc}")

    print("VERDICT=OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
