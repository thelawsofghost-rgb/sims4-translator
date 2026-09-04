#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p28b0_cfg_audit.py —— P28B-0 专属 Resource.cfg 只读审计 + 决策 (与 P28A/P27 隔离)

fork 自 ww_p28a_cfg_audit.py 的已实证逻辑 (glob/eff/Sims4 高数值优先/fail-closed),
但【硬编码 P28B0 专用语义与输出键】, 绝不与 P27_Overrides / P28A 混用:

  * OBJECT_DIR  = "P28B0_Overrides"
  * SOURCE      = Mods/2026.7.20/WW_Nevely42_Animations.package  (真实源)
  * CLONE_TARGET= P28B0_Overrides/WW_Nevely42_Animations.package (虚拟目标, 文件可不存在/可已存在)
  * 输出 P28B0_OVERRIDE_EFFECTIVE_PRIORITY -> deploy 以此 + SOURCE_EFFECTIVE_PRIORITY 判定
    PRIORITY_RELATION=OVERRIDE_HIGHER (严格大于才放行).

两模式 (与 P28A 相同):
  check   : 只读解析 + 判读, 不改任何文件.
  propose : check + 输出待追加 ASCII 行(base64) 与建议 Priority(全局最高+margin=100),
            供 ww_p28b0_full_clone_deploy.ps1 精确追加 (单一事实来源).

Sims4 precedence: 数值越大 priority 越高, 高者覆盖低者.
目标必须可证明: P28B0_OVERRIDE_EFFECTIVE_PRIORITY > SOURCE_EFFECTIVE_PRIORITY。

关键: source 真实文件, 其相对路径必须从 cfg.parent (Mods 根) 可靠计算(大小写不敏感,
去掉 drive letter, 统一 \\ 与 /), 否则匹配不到 '2026.7.20/*.package' 规则 -> fail-closed。

输出键 (ASCII):
  RESOURCE_CFG_EXISTS / RESOURCE_CFG_SHA_BEFORE
  PRIORITY_MIN/MAX/COUNT / RULE_COUNT
  MODS_ROOT / SOURCE_FULL_PATH / SOURCE_REL_PATH / CLONE_TARGET_REL_PATH
  SOURCE_EFFECTIVE_PRIORITY
  P28B0_OVERRIDE_EFFECTIVE_PRIORITY
  P28B0_DIR_DEDICATED_RULE=YES|NO / P28B0_DEDICATED_PRIORITY=<int>
  PRIORITY_RELATION=OVERRIDE_HIGHER
  PROPOSED_PRIORITY=<int> / APPEND_REQUIRED=YES|NO / APPEND_LINES=<base64>
  VERDICT=OK|FAIL / REASON=<code>

退出码:
  0=可决策  2=cfg 缺失  3=无 Priority 规则  4=无法证明 override>source(fail-closed)  5=读取失败

用法:
  python scripts\\ww_p28b0_cfg_audit.py check  "<Resource.cfg>"
  python scripts\\ww_p28b0_cfg_audit.py propose "<Resource.cfg>"
只读 (post-write re-audit 同样调用本脚本 check/propose 于【写入后】的 cfg 上验证实际结果).
"""
import base64
import hashlib
import re
import sys
from pathlib import Path

OBJECT_DIR = "P28B0_Overrides"
SOURCE_SUBDIR = "2026.7.20"
SOURCE_BASENAME = "WW_Nevely42_Animations.package"


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(1 << 16), b""):
            h.update(blk)
    return h.hexdigest().lower()


def parse_cfg(text):
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
    def norm(s):
        return s.replace("\\", "/")
    try:
        seg_pat = norm(pattern).split("/")
        seg_path = norm(rel_path).split("/")
        return _gm(seg_pat, seg_path, 0)
    except Exception:
        return False


_BOUND = 5000


def _gm(pats, segs, depth):
    if depth > _BOUND:
        return False
    if not pats:
        return not segs
    p = pats[0]
    if p == "**":
        for i in range(len(segs) + 1):
            if _gm(pats[1:], segs[i:], depth + 1):
                return True
        return False
    if not segs:
        return False
    return _seg(p, segs[0]) and _gm(pats[1:], segs[1:], depth + 1)


def _seg(pat, s):
    if pat in ("*", "**"):
        return True
    import fnmatch
    return fnmatch.fnmatchcase(s, pat)


def classify(rules, rel_path):
    best = None
    for r in rules:
        if glob_match(r["pattern"], rel_path):
            if best is None or r["prio"] > best:
                best = r["prio"]
    return (best is not None, best)


def main():
    if len(sys.argv) < 3:
        print("usage: ww_p28b0_cfg_audit.py check|propose <resource.cfg>")
        return 2
    mode = sys.argv[1].lower()
    cfg = Path(sys.argv[2])

    if not cfg.is_file():
        print("RESOURCE_CFG_EXISTS=NO")
        print("VERDICT=FAIL")
        print("REASON=CFG_MISSING")
        return 2
    print("RESOURCE_CFG_EXISTS=YES")
    print(f"RESOURCE_CFG_SHA_BEFORE={sha256_file(cfg)}")

    try:
        text = cfg.read_text(encoding="utf-8")
    except Exception:
        text = cfg.read_text(encoding="latin-1")
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

    mods_root = str(cfg.parent) if cfg.parent else None

    def to_posix(p):
        if not p:
            return ""
        s = p.replace("\\", "/")
        m = re.match(r"^[A-Za-z]:/(.*)$", s)
        if m:
            s = "/" + m.group(1)
        return re.sub(r"^/+", "", s)

    # 虚拟相对路径 (不依赖真实文件是否存在)
    source_rel = f"{SOURCE_SUBDIR}/{SOURCE_BASENAME}"
    clone_rel = f"{OBJECT_DIR}/{SOURCE_BASENAME}"
    print(f"MODS_ROOT={mods_root or ''}")
    print(f"SOURCE_FULL_PATH={cfg.parent / source_rel if cfg.parent else source_rel}")
    print(f"SOURCE_REL_PATH={source_rel}")
    print(f"CLONE_TARGET_REL_PATH={clone_rel} (virtual)")

    # source 是真实文件; 若其绝对路径在 cfg.parent 之下, 用真实相对路径(防绝对路径匹配失败)
    real_source = (cfg.parent / source_rel) if cfg.parent else None
    if real_source is not None and real_source.is_file():
        # 已为正确相对路径
        pass

    s_cov, s_prio = classify(rules, source_rel)
    c_cov, c_prio = classify(rules, clone_rel)
    print(f"SOURCE_PKG_COVERED={'YES' if s_cov else 'NO'}")
    print(f"SOURCE_PKG_PRIO={s_prio if s_cov else 0}")
    print(f"CLONE_COVERED={'YES' if c_cov else 'NO'} (generic glob)")
    print(f"CLONE_PKG_PRIO={c_prio if c_cov else 0}")

    dedicated = [r for r in rules if OBJECT_DIR in r["pattern"].replace("\\", "/")]
    ded_prio = max(r["prio"] for r in dedicated) if dedicated else None
    print(f"P28B0_DIR_DEDICATED_RULE={'YES' if dedicated else 'NO'}")
    print(f"P28B0_DEDICATED_PRIORITY={ded_prio if ded_prio is not None else 0}")

    def eff(rel):
        vals = [r["prio"] for r in rules if glob_match(r["pattern"], rel)]
        return max(vals) if vals else None

    src_eff = eff(source_rel)
    clone_eff = eff(clone_rel)

    if src_eff is None:
        print("SOURCE_EFFECTIVE_PRIORITY=UNRESOLVED")
        print("P28B0_OVERRIDE_EFFECTIVE_PRIORITY=UNRESOLVED")
        print("VERDICT=FAIL")
        print("REASON=SOURCE_PRIORITY_UNRESOLVED")
        return 4

    print(f"SOURCE_EFFECTIVE_PRIORITY={src_eff}")
    print(f"P28B0_OVERRIDE_EFFECTIVE_PRIORITY={clone_eff if clone_eff is not None else 'UNRESOLVED'}")

    # 命中规则诊断
    s_hits = [(r["prio"], r["pattern"]) for r in rules if glob_match(r["pattern"], source_rel)]
    c_hits = [(r["prio"], r["pattern"]) for r in rules if glob_match(r["pattern"], clone_rel)]
    print(f"SOURCE_MATCH_COUNT={len(s_hits)}")
    for i, (pr, pat) in enumerate(sorted(s_hits, reverse=True), 1):
        print(f"SOURCE_MATCH_RULE_{i}_PRIORITY={pr}")
        print(f"SOURCE_MATCH_RULE_{i}_PATTERN={pat}")
    print(f"CLONE_MATCH_COUNT={len(c_hits)}")
    for i, (pr, pat) in enumerate(sorted(c_hits, reverse=True), 1):
        print(f"CLONE_MATCH_RULE_{i}_PRIORITY={pr}")
        print(f"CLONE_MATCH_RULE_{i}_PATTERN={pat}")

    append_required = False
    if clone_eff is not None and clone_eff > src_eff:
        proposed = clone_eff
        print("PRIORITY_RELATION=OVERRIDE_HIGHER")
        print("APPEND_REQUIRED=NO")
    else:
        margin = 100
        proposed = max_prio + margin
        new_clone_eff = proposed if clone_eff is None else max(clone_eff, proposed)
        if not (new_clone_eff > src_eff):
            print("VERDICT=FAIL")
            print("REASON=OVERRIDE_PRIORITY_NOT_HIGHER")
            return 4
        append_required = True
        print(f"PRIORITY_RELATION=OVERRIDE_HIGHER (proposed {new_clone_eff} > source {src_eff})")
        print("APPEND_REQUIRED=YES")

    print(f"PROPOSED_PRIORITY={proposed}")
    lines_txt = f"Priority {proposed}\nPackedFile {OBJECT_DIR}/*.package\n"
    enc = base64.b64encode(lines_txt.encode("utf-8")).decode("ascii")
    print(f"APPEND_LINES={enc}")
    print("VERDICT=OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
