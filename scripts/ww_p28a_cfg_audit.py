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
    """返回 list of {'prio':int,'pattern':str}. 逐行防御: 任何单行解析异常都跳过该行, 不中断."""
    rules, cur = [], None
    for raw in (text or "").splitlines():
        try:
            line = raw.strip()
            if not line:
                continue
            m = re.match(r"(?i)^\s*Priority\s+(\d+)\s*$", line)
            if m:
                cur = int(m.group(1))
                continue
            m2 = re.match(r"(?i)^\s*PackedFile\s+(.+?)\s*$", line)
            if m2 and cur is not None:
                rules.append({"prio": cur, "pattern": m2.group(1).strip()})
        except Exception:
            continue
    return rules


def glob_match(pattern, rel_path):
    """Sims4 风格小 glob: '*'=单段内任意, '**'=跨段. 返回 bool.
    统一把 '\\' 与 '/' 都视为路径分隔符, 平台无关.
    防御: 递归深度受限, 绝不抛出 (任何异常按不匹配处理)."""
    def norm(s):
        return s.replace("\\", "/")
    try:
        seg_pat = norm(pattern).split("/")
        seg_path = norm(rel_path).split("/")
        return _gm(seg_pat, seg_path, 0)
    except Exception:
        # 防御: 任何匹配异常都不应中断审计; 视为不命中.
        return False


_BOUND = 5000


def _gm(pats, segs, depth):
    if depth > _BOUND:
        return False
    if not pats:
        return not segs
    p = pats[0]
    if p == "**":
        # 匹配 0..n 段
        for i in range(len(segs) + 1):
            if _gm(pats[1:], segs[i:], depth + 1):
                return True
        return False
    if not segs:
        return False
    return _seg(p, segs[0]) and _gm(pats[1:], segs[1:], depth + 1)


def _seg(pat, s):
    # 单段: 支持完整通配 (简化: 以 '**' 或 '*' 处理段内全部, 否则精确)
    if pat in ("*", "**"):
        return True
    # 通用 glob 段匹配 (fnmatch)
    import fnmatch
    return fnmatch.fnmatchcase(s, pat)


def classify(rules, rel_path):
    """返回 (covered:bool, prio:int|None) — 命中规则的有效最高 priority (数值大=高优先).
    仅用于判读信息输出; 决策主路径使用 eff()."""
    best = None
    for r in rules:
        if glob_match(r["pattern"], rel_path):
            if best is None or r["prio"] > best:
                best = r["prio"]
    return (best is not None, best)


def main():
    if len(sys.argv) < 3:
        print("usage: ww_p28a_cfg_audit.py check|propose <resource.cfg> [root_override_abs] [subdir_src_abs] [--p27-dir P28B_Overrides]")
        return 2
    mode = sys.argv[1].lower()
    cfg = Path(sys.argv[2])
    root_abs = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
    sub_abs = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else None
    # 可选覆盖 override 目录名 (默认 P27_Overrides; P28B 传入 P28B_Overrides 复用同一审计决策)
    p27_dir = P27_DIR
    if "--p27-dir" in sys.argv:
        _i = sys.argv.index("--p27-dir")
        if len(sys.argv) > _i + 1 and sys.argv[_i + 1]:
            p27_dir = sys.argv[_i + 1]
    _P27_DIR = p27_dir

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

    # 判读轨迹: 包相对 Mods 根 (Resource.cfg.parent) 的 POSIX 风格路径.
    # 关键: source 是真实存在文件, 其 relative 必须从 cfg.parent 可靠计算, 不能用完整绝对路径
    #      (否则匹配不到 '2026.7.20/*.package' 之类的规则 → src_eff=None).
    def base_name(p):
        import ntpath
        return ntpath.basename(p.replace("/", "\\")) if p else ""

    def to_posix(p):
        if not p:
            return ""
        s = p.replace("\\", "/")
        m = re.match(r"^[A-Za-z]:/(.*)$", s)  # 去 drive letter
        if m:
            s = "/" + m.group(1)
        return re.sub(r"^/+", "", s)

    def rel_to_base(full_abs, base_abs):
        """full_abs 相对 base_abs 的 POSIX 相对路径; 大小写不敏感; 不在其下则 None."""
        def segs(p):
            s = [x for x in to_posix(p).split("/") if x not in ("", ".")]
            return s, [x.lower() for x in s]
        fs, fl = segs(full_abs)
        bs, bl = segs(base_abs)
        if not bs or len(fs) < len(bs):
            return None
        for a, b in zip(fl, bl):
            if a != b:
                return None
        return "/".join(fs[len(bs):]) or None

    mods_root = str(cfg.parent) if cfg.parent else None
    root_rel = base_name(root_abs) if root_abs else ROOT_OVERRIDE
    # source 相对路径: 优先从 cfg.parent 计算; 否则退回 seed 相对路径 (测试/无法确定根时).
    sub_rel = rel_to_base(sub_abs, mods_root) if (sub_abs and mods_root) else None
    if sub_rel is None:
        sub_rel = (sub_abs if sub_abs else f"{SUB_SOURCE_SEED}/WW_Nevely42_Animations.package").replace("\\", "/")
    p27_override_rel = f"{_P27_DIR}/{base_name(root_abs) if root_abs else ROOT_OVERRIDE}"

    # ---- 诊断输出 (read-only) ----
    print(f"MODS_ROOT={mods_root or ''}")
    print(f"SOURCE_FULL_PATH={sub_abs or ''}")
    print(f"SOURCE_REL_PATH={sub_rel}")
    print(f"OLD_ROOT_REL_PATH={root_rel}")
    print(f"P27_TARGET_REL_PATH={p27_override_rel} (virtual, 文件可不存在)")
    root_cov, root_prio = classify(rules, root_rel)
    sub_cov, sub_prio = classify(rules, sub_rel)
    p27_cov, p27_prio = classify(rules, p27_override_rel)

    print(f"ROOT_PKG_COVERED={'YES' if root_cov else 'NO'}")
    print(f"ROOT_PKG_PRIO={root_prio if root_cov else 0}")
    print(f"SUBDIR_PKG_COVERED={'YES' if sub_cov else 'NO'}")
    print(f"SUBDIR_PKG_PRIO={sub_prio if sub_cov else 0}")
    print(f"P27_DIR_COVERED={'YES' if p27_cov else 'NO'} (generic glob)")

    # 专门规则: 显式指向 P27_Overrides 的 PackedFile (非通用 glob 覆盖)
    dedicated_p27 = [r for r in rules if _P27_DIR in r["pattern"].replace("\\", "/")]
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
    ovr_eff = eff(rules, p27_override_rel)   # P27 override 有效最高 (虚拟目标)
    root_eff = eff(rules, root_rel)          # 旧 root 包有效最高

    # ---- fail-closed 守卫: 任何 priority 未解析都不能参与比较 (绝不 int>None) ----
    if src_eff is None:
        # 源包是真实存在的文件, 必应命中至少一条规则; 未解析 = 审计/规则异常, 拒绝部署.
        print("SOURCE_EFFECTIVE_PRIORITY=UNRESOLVED")
        print("P27_OVERRIDE_EFFECTIVE_PRIORITY=UNRESOLVED")
        print("OLD_ROOT_EFFECTIVE_PRIORITY=UNRESOLVED")
        print("VERDICT=FAIL")
        print("REASON=SOURCE_PRIORITY_UNRESOLVED")
        return 4

    print(f"SOURCE_EFFECTIVE_PRIORITY={src_eff}")
    print(f"P27_OVERRIDE_EFFECTIVE_PRIORITY={ovr_eff if ovr_eff is not None else 'UNRESOLVED'}")
    print(f"OLD_ROOT_EFFECTIVE_PRIORITY={root_eff if root_eff is not None else 'UNRESOLVED'}")

    # ---- 命中规则诊断: 列出 source 与 override target 命中的每条规则 ----
    src_hits = [(r["prio"], r["pattern"]) for r in rules if glob_match(r["pattern"], sub_rel)]
    p27_hits = [(r["prio"], r["pattern"]) for r in rules if glob_match(r["pattern"], p27_override_rel)]
    print(f"SOURCE_MATCH_COUNT={len(src_hits)}")
    for i, (prio, pat) in enumerate(sorted(src_hits, reverse=True), 1):
        print(f"SOURCE_MATCH_RULE_{i}_PRIORITY={prio}")
        print(f"SOURCE_MATCH_RULE_{i}_PATTERN={pat}")
    print(f"P27_TARGET_MATCH_COUNT={len(p27_hits)}")
    for i, (prio, pat) in enumerate(sorted(p27_hits, reverse=True), 1):
        print(f"P27_TARGET_MATCH_RULE_{i}_PRIORITY={prio}")
        print(f"P27_TARGET_MATCH_RULE_{i}_PATTERN={pat}")

    # ---- 决策 ----
    append_required = False
    if ovr_eff is not None and ovr_eff > src_eff:
        # 已存在可证明高于源包的 override 有效优先级 -> 复用, 不追加
        proposed = ovr_eff
        print("PRIORITY_RELATION=OVERRIDE_HIGHER")
        print("APPEND_REQUIRED=NO")
    else:
        # 无法证明 override > source -> 拟新增一条严格高于一切现有规则的 priority,
        #   并以"拟议规则加入后的模型"重新计算 override 有效优先级, 证明严格大于 source 才放行.
        margin = 100
        proposed = max_prio + margin
        # 拟议规则加入后的 override 有效优先级 = max(现有 override 命中, proposed)
        new_ovr_eff = proposed if ovr_eff is None else max(ovr_eff, proposed)
        if not (new_ovr_eff > src_eff):
            print("VERDICT=FAIL")
            print("REASON=OVERRIDE_PRIORITY_NOT_HIGHER")
            return 4
        append_required = True
        print(f"PRIORITY_RELATION=OVERRIDE_HIGHER (proposed {new_ovr_eff} > source {src_eff})")
        print("APPEND_REQUIRED=YES")

    print(f"PROPOSED_PRIORITY={proposed}")

    # 待追加行 (base64, 避免编码/换行歧义) —— 始终给出, ps1 仅在 APPEND_REQUIRED=YES 时追加
    lines_txt = f"Priority {proposed}\nPackedFile {_P27_DIR}/*.package\n"
    enc = base64.b64encode(lines_txt.encode("utf-8")).decode("ascii")
    print(f"APPEND_LINES={enc}")

    print("VERDICT=OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
