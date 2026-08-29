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
  * Sims4 precedence 语义: 数值越大 priority 越高, 高者覆盖低者
  * 对给定相对路径, 用 1 段 '*' 与跨段 '**' 规则做 glob 匹配; 命中多规则取有效最高 priority

输出 (ASCII, 除文件内容原样引用的路径/规则外):
  RESOURCE_CFG_EXISTS=YES|NO
  RESOURCE_CFG_SHA_BEFORE=<sha256>   (仅文件存在时)
  PRIORITY_MIN/MAX=<int>
  PRIORITY_COUNT=<int>
  RULE_COUNT=<int>
  ROOT_PKG_COVERED/SUBDIR_PKG_COVERED/P27_DIR_COVERED=YES|NO
  SOURCE_EFFECTIVE_PRIORITY=<int>     (源包有效最高, 0=未命中)
  P27_OVERRIDE_EFFECTIVE_PRIORITY=<int>(当前 P27 override 有效最高, 0=未命中)
  OLD_ROOT_EFFECTIVE_PRIORITY=<int>   (旧 root 包有效最高)
  PRIORITY_RELATION=OVERRIDE_HIGHER   (目标: override > source)
  PROPOSED_PRIORITY=<int>             (新增专用规则 priority = 全局最高+margin)
  APPEND_REQUIRED=YES|NO
  APPEND_LINES=<base64>               (待追加的原始 cfg 行, base64)
  VERDICT=OK|FAIL
  REASON=<code>

退出码:
  0 = 可决策 (VERDICT=OK, 且已给出 APPEND_REQUIRED)
  2 = cfg 不存在
  3 = 无任何 Priority 规则 (无法建立优先级依据, fail-closed)
  4 = 无法证明 OVERRIDE_PRIORITY_NOT_HIGHER (fail-closed, 绝不部署)
  5 = 无法读取

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
from pathlib import Path

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
    max_prio = max(prios)
    print(f"PRIORITY_MIN={min_prio}")
    print(f"PRIORITY_MAX={max_prio}")
    print(f"PRIORITY_COUNT={len(prios)}")
    print(f"RULE_COUNT={len(rules)}")

    # 判读轨迹: root override 相对 Mods 根 = 文件名 (在根, 无子目录)
    def base_name(p):
        # 兼容 Windows '\\' 与 POSIX '/' 分隔的绝对路径, 取末段文件名
        import ntpath
        return ntpath.basename(p.replace("/", "\\")) if p else ""

    root_rel = base_name(root_abs) if root_abs else ROOT_OVERRIDE
    sub_rel = (sub_abs if sub_abs else f"{SUB_SOURCE_SEED}/WW_Nevely42_Animations.package").replace("\\", "/")


    p27_override_rel = f"{P27_DIR}/{base_name(root_abs) if root_abs else ROOT_OVERRIDE}"
    root_cov, root_prio = classify(rules, root_rel)
    sub_cov, sub_prio = classify(rules, sub_rel)
    p27_cov, p27_prio = classify(rules, p27_override_rel)

    print(f"ROOT_PKG_COVERED={'YES' if root_cov else 'NO'}")
    print(f"ROOT_PKG_PRIO={root_prio if root_cov else 0}")
    print(f"SUBDIR_PKG_COVERED={'YES' if sub_cov else 'NO'}")
    print(f"SUBDIR_PKG_PRIO={sub_prio if sub_cov else 0}")
    print(f"P27_DIR_COVERED={'YES' if p27_cov else 'NO'} (generic glob)")

    # 专门规则: 显式指向 P27_Overrides 的 PackedFile (非通用 glob 覆盖)
    dedicated_p27 = [r for r in rules if "P27_Overrides" in r["pattern"].replace("\\", "/")]
    ded_p27_prio = max(r["prio"] for r in dedicated_p27) if dedicated_p27 else None
    print(f"P27_DIR_DEDICATED_RULE={'YES' if dedicated_p27 else 'NO'}")
    print(f"P27_DEDICATED_PRIORITY={ded_p27_prio if ded_p27_prio is not None else 0}")

    # ===================================================================
    # Sims 4 precedence 语义: 数值越大 priority 越高; 高者覆盖低者.
    # 对每一类包: 命中多个规则时取有效最高 priority (Sims4 实际 precedence).
    # 目标必须可证明: P27_OVERRIDE_EFFECTIVE_PRIORITY > SOURCE_EFFECTIVE_PRIORITY
    # ===================================================================
    def eff(rules, rel):
        vals = [r["prio"] for r in rules if glob_match(r["pattern"], rel)]
        return max(vals) if vals else None

    src_eff = eff(rules, sub_rel)            # 源包有效最高 priority
    ovr_eff = eff(rules, p27_override_rel)   # P27 override 有效最高
    root_eff = eff(rules, root_rel)          # 旧 root 包有效最高
    print(f"SOURCE_EFFECTIVE_PRIORITY={src_eff if src_eff is not None else 0}")
    print(f"P27_OVERRIDE_EFFECTIVE_PRIORITY={ovr_eff if ovr_eff is not None else 0}")
    print(f"OLD_ROOT_EFFECTIVE_PRIORITY={root_eff if root_eff is not None else 0}")

    # ---- 决策 ----
    append_required = False
    if ovr_eff is not None and ovr_eff > src_eff:
        # 已存在可证明高于源包的 override 有效优先级 -> 复用, 不追加
        proposed = ovr_eff
        print("PRIORITY_RELATION=OVERRIDE_HIGHER")
        print("APPEND_REQUIRED=NO")
    else:
        # 无法证明 override > source -> 必须新增一条严格高于所有可能与源包竞争的 priority
        #   取全局最高 + 裕量, 确保严格大于 source 及一切现有规则.
        margin = 100
        proposed = max_prio + margin
        if not (proposed > src_eff):
            # 极端: 若 still 不满足, fail-closed
            print("VERDICT=FAIL")
            print("REASON=OVERRIDE_PRIORITY_NOT_HIGHER")
            return 4
        append_required = True
        print(f"PRIORITY_RELATION=OVERRIDE_HIGHER (proposed {proposed} > source {src_eff})")
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
