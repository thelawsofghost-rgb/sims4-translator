#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""READ-ONLY lexical-earlier audit for the 436 DEPLOY rows.

背景: Canary 真机结果 Anika=FAIL。既有 game-proven deployment contract 要求
  target_basename 在字典序上严格早于 source_basename (target < source),
  确保 target sidecar 按 Mods load order 后加载覆盖 source。
  但 '000_'-prefix 技巧对以低位 ASCII (<= 0x2F: 空格 与 ! "# $ % & ' ( ) * + , - . / 等)
  开头的 source 失效: '0'(0x30) 字典序排在它们之后, 故 target 不早于 source。

本审计【ZERO WRITE TO MODS】, 逐行比较 436 个 DEPLOY rows:

  source_basename = Path(package_path).name
  target_basename = source stem 生成的部署目标名 (与 production_deploy_preflight_438.py 的
                    target_filename_for() 完全一致):
                      f"000_{source_stem}_CHS.package"
  target_lexically_earlier = target_basename.lower() < source_basename.lower()
    (Python codepoint 字典序比较; 与我们生成/部署时使用的确定性 ordinal basename
     比较一致, 不依赖 PowerShell culture sort)

输出所有 FAIL rows (target 未 lexical earlier), 并统计:
  DEPLOY rows = 436
  lexical earlier PASS = ?
  lexical earlier FAIL = ?
  FAIL-by-first-char 分布 (尤其 ! " # $ % & ' ( ) * + , - . / 数字 _ [ { 等开头)

特别核查 source basename 以 ! ( [ { _ 数字 等开头的情况。

产出:
  output/deployment_lexical_audit.csv    (所有 436 行: source_basename, target_basename,
                                          target_lexically_earlier, source_first_char, verdict)
  output/deployment_lexical_audit_report.md
终局:
  DEPLOY rows = 436
  lexical earlier PASS = ?
  lexical earlier FAIL = ?
  leading-char breakdown (仅 FAIL)
  LEXICAL_AUDIT: PASS|FAIL   (PASS 当且仅当 FAIL==0)

fail-closed: 输出已存在则拒写除非 --force; FAIL>0 -> rc=1 (fail-closed, 不引导部署)。
"""
import argparse
import csv
import sys
from pathlib import Path

MAN_OUT = "output/deployment_lexical_audit.csv"
REP_OUT = "output/deployment_lexical_audit_report.md"


def target_filename_for(source_name: str) -> str:
    """与 production_deploy_preflight_438.target_filename_for() 逐字一致。"""
    stem = Path(source_name).stem
    return f"000_{stem}_CHS.package"


def _res(p: str) -> Path:
    return Path(p).expanduser().resolve()


def main():
    ap = argparse.ArgumentParser(description="READ-ONLY lexical-earlier audit (436 DEPLOY rows)")
    ap.add_argument("--selection", required=True)
    ap.add_argument("--out", default=MAN_OUT)
    ap.add_argument("--report", default=REP_OUT)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    out = Path(a.out).expanduser()
    rep = Path(a.report).expanduser()
    if (out.exists() or rep.exists()) and not a.force:
        print(f"[FAIL-CLOSED] 输出已存在, refuse (rc=1) 除非 --force: {out} | {rep}")
        return 1

    sel = _res(a.selection)
    deploys = []
    with open(sel, "r", newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("deployment_class", "") == "DEPLOY":
                deploys.append(r)
    n_deploy = len(deploys)
    print(f"[load] DEPLOY rows = {n_deploy}")

    rows = []
    for r in deploys:
        src = Path(r.get("package_path", "")).name
        tgt = target_filename_for(src)
        earlier = tgt.lower() < src.lower()
        rows.append({
            "source_basename": src,
            "target_basename": tgt,
            "target_lexically_earlier": "YES" if earlier else "NO",
            "source_first_char": src[:1],
            "source_first_ord": hex(ord(src[0])) if src else "",
            "verdict": "PASS" if earlier else "FAIL",
        })

    n_pass = sum(1 for x in rows if x["verdict"] == "PASS")
    n_fail = n_deploy - n_pass
    # FAIL 按首字符分组
    fail_by_first = {}
    for x in rows:
        if x["verdict"] == "FAIL":
            fail_by_first[x["source_first_char"]] = fail_by_first.get(x["source_first_char"], 0) + 1

    ok = (n_fail == 0)
    verdict = "PASS" if ok else "FAIL"

    # ---- 确定性安全 naming policy 推荐 (只读, 不改名不部署) ----
    # 需求: 对每个 source, target_basename < source_basename (Python codepoint 序)。
    # 现有 '000_'-prefix (首字 '0'=0x30) 只对首字 > '0' 的 source 成立;
    # 对首字 < '0' (0x00-0x2F: 空格与 !"#$%&'()*+,-./) 失效。
    # 策略: 选一个固定 prefix P, 其首码位严格小于 436 个 source 首码位的最小值 m,
    #   则 P+stem+_CHS 恒早于该 source。P 必须是合法 Windows 文件名部分。
    all_first = [x["source_first_char"] for x in rows]
    m = min((ord(c) for c in all_first), default=0x7F) if all_first else 0x7F
    m_char = chr(m) if all_first else ""
    # 可打印合法文件首字候选 (升序), 取第一个严格 < m 的
    candidates = [ch for ch in map(chr, range(0x20, 0x7F))]
    # 排除 Windows 文件名非法字符
    illegal = set('<>:"/\\|?*')
    candidates = [c for c in candidates if c not in illegal and c != '.']
    chosen = next((c for c in candidates if ord(c) < m), None)
    if chosen is None:
        # m 已是最小可打印格即空格; 改用两字符降级前缀: 特殊字符 + '0'
        chosen = None
        policy_ok = False
        policy_note = ("首码位最小为空格(0x20), 无可打印字符 < 它; 需特殊处理(见 policy_note)")
    else:
        prefix = chosen + "00_"
        policy_passes = 0
        for x in rows:
            s = x["source_basename"]
            tgt_new = prefix + Path(s).stem + "_CHS.package"
            if tgt_new.lower() < s.lower():
                policy_passes += 1
        policy_ok = (policy_passes == n_deploy)
        policy_note = (f"首码位最小 source = {m_char!r} (ord {m}); 选择 prefix 首字 {chosen!r} (ord {ord(chosen)}) < {m}; "
                       f"prefix = {prefix!r}; 在 {n_deploy} 行中均满足 target<source: {policy_ok}")
    # FAIL 行在现有策略下的暴露数 = n_fail; 新策略下如 policy_ok 则全 0
    policy_fail_under_new = (0 if policy_ok else None)

    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["source_basename", "target_basename", "target_lexically_earlier",
            "source_first_char", "source_first_ord", "verdict"]
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for x in rows:
            w.writerow(x)

    rep.parent.mkdir(parents=True, exist_ok=True)
    L = []
    L.append("# Lexical-Earlier Audit (READ-ONLY, ZERO WRITE TO MODS)")
    L.append("")
    L.append(f"- selection: {sel}  (DEPLOY rows = {n_deploy})")
    L.append("- 比较规则: target_basename.lower() < source_basename.lower()")
    L.append("  (与 production_deploy_preflight_438.py 的确定性 ordinal basename 比较一致;")
    L.append("   target 名由 target_filename_for() 生成: `000_{source_stem}_CHS.package`)")
    L.append("- ZERO WRITE TO MODS: 仅读数, 不改名不动文件不部署")
    L.append("")
    L.append("## 终局")
    L.append(f"DEPLOY rows = {n_deploy}")
    L.append(f"lexical earlier PASS = {n_pass}")
    L.append(f"lexical earlier FAIL = {n_fail}")
    L.append(f"LEXICAL_AUDIT: {verdict}")
    L.append("")
    L.append("## FAIL 首字符分布")
    if fail_by_first:
        for ch, cnt in sorted(fail_by_first.items(), key=lambda kv: ord(kv[0])):
            L.append(f"- {repr(ch)} (ord {hex(ord(ch))}): {cnt}")
    else:
        L.append("- (无 FAIL)")
    L.append("")
    L.append("## 确定性安全 target naming policy (推荐, 暂不执行)")
    L.append(f"- 现有 prefix '000_' (首字 '0'=0x30) 对首字<='0' 的 source 失效:")
    L.append(f"  位置规则 '0'(0x30) > 低位标点/空格 (0x20-0x2F), 故 target 不早于 source。")
    L.append(f"- 436 个 source 首码位最小值 m = {hex(m)} ({m_char!r}).")
    if chosen is None:
        L.append(f"- 无可打印合法首字 < m; 需特殊处理 (m 为空格等极端情况).")
    else:
        prefix = chosen + "00_"
        L.append(f"- 推荐 prefix 首字 = {repr(chosen)} (ord {ord(chosen)}) < m, prefix = {prefix!r}.")
        L.append(f"- 用 prefix 重生成后, {n_deploy} 行全部满足 target<source: {policy_ok}")
        if not policy_ok:
            L.append(f"- 警告: 新 prefix 仍有未满足行 (policy_fail_under_new 待查).")
    L.append("- 约束: prefix 必须是合法 Windows 文件名部分 (不含 < > : \" / \\ | ? * 且不以点结尾).")
    L.append("- 本报告只读; 不重命名任何现有 Mods 文件, 不 bulk deploy.")
    L.append("")
    L.append("## 全部 FAIL rows")
    for x in rows:
        if x["verdict"] == "FAIL":
            L.append(f"- FAIL  source={x['source_basename']!r}  target={x['target_basename']!r}"
                     f"  (first={x['source_first_char']!r} ord={x['source_first_ord']})")
    L.append("")
    L.append(f"LEXICAL_AUDIT: {verdict}")
    with open(rep, "w", newline="", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    print(f"## LEXICAL_AUDIT: {verdict}")
    print(f"DEPLOY rows = {n_deploy}")
    print(f"lexical earlier PASS = {n_pass}")
    print(f"lexical earlier FAIL = {n_fail}")
    if chosen is not None:
        prefix = chosen + "00_"
        print(f"POLICY: prefix={prefix!r} (首字ord={ord(chosen)} < m={hex(m)}); "
              f"{n_deploy} 行全满足 target<source: {policy_ok}")
    else:
        print(f"POLICY: 无可打印首字 < m={hex(m)}, 需特殊处理")
    print(f"output: {out}")
    print(f"report: {rep}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
