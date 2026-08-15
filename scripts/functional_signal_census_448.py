#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
functional_signal_census_448.py — 只读 448 functional-signal census
========================================================================
输入 (必须真实 Windows PASS 的 reconciliation 报告):
  output/pose_path_reconciliation_448.csv
只扫描其中 EXACT_PATH / UNIQUE_RELOCATED 的 resolved physical path。
(448 已验证 resolved=448/448, MISSING=0, AMBIGUOUS=0, MISMATCH=0;
 不允许用 stale coverage path 绕过 reconciliation。)

统计 8 个已 VERIFIED functional signals (全来自 lib/s4pi_src 权威 source):
  0xC0DB5AE7  OBJD / ObjectDefinition
  0x319E4F1D  COBJ / Catalog object
  0xE882D22F  interaction XML
  0x0C772E27  action XML
  0xB61DE6B4  object XML
  0xD3044521  RSLT / Slot
  0xD382BF57  FTPT / Footprint
  0xEE17C6AD  Animation component

输出 (每包):
  package_path, basename, OBJD_count, COBJ_count, interaction_count,
  action_count, object_xml_count, RSLT_count, FTPT_count,
  animation_component_count, functional_signal_type_count,
  functional_signal_resource_count, signal_signature

aggregate:
  每个 type 命中多少包; 任意 signal 命中多少包;
  >=2 signal types 命中; >=3 signal types 命中;
  常见 co-occurrence (OBJD+COBJ / +FTPT / +RSLT+FTPT / +interaction)
  已知锚点单独打印 (Kritical known-functional + Gounafiers/Anger/Cry genuine-Pose)

严格规则:
  - path 不存在 / 资源不可读 -> ERROR, 禁止当 signal=0 放行
  - 未把「任意一个 signal 出现」直接解释为 functional-object(本步只测稀有度/组合)
  - 不定义 production gate
  - 0x7DF2169C 保持中性语义 TUNING_XML/PosePackInstance (不叫 WW_ANIM_XML)
只读: 不改 coverage / 448 / classifier / writer / resolver; 无作者黑名单 / 无 filename 特判。
"""
import sys, os, csv, argparse
from pathlib import Path
from collections import Counter, defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# 8 个 VERIFIED functional type ids (source: lib/s4pi_src 权威核实)
FUNCTIONAL_SIGNALS = {
    0xC0DB5AE7: "OBJD",       # ObjectDefinition(catalog)
    0x319E4F1D: "COBJ",       # Catalog object
    0xE882D22F: "interaction",# TS4 XML interaction
    0x0C772E27: "action",     # TS4 XML action
    0xB61DE6B4: "object_xml", # TS4 XML object
    0xD3044521: "RSLT",       # Slot
    0xD382BF57: "FTPT",       # Footprint
    0xEE17C6AD: "animation",  # Animation component
}
SIGNAL_NAMES = list(FUNCTIONAL_SIGNALS.keys())

# 已知锚点 (仅用于报告展示, 不作判定/特判)
KNOWN_FUNCTIONAL = "_Kritical_BrainwashingMachine1g.package"
KNOWN_POSE = [
    "Gounafiers_Poses_Public_Ver (1).package",
    "AngerFrustrationandRageflowur.package",
    "Cry Animation_Sitting (tinisims).package",
]

# 常见 co-occurrence 模板 (模板 = 必须包含的 type)
COOC = {
    "OBJD+COBJ":        [0xC0DB5AE7, 0x319E4F1D],
    "OBJD+COBJ+FTPT":   [0xC0DB5AE7, 0x319E4F1D, 0xD382BF57],
    "OBJD+COBJ+RSLT+FTPT": [0xC0DB5AE7, 0x319E4F1D, 0xD3044521, 0xD382BF57],
    "OBJD+COBJ+interaction": [0xC0DB5AE7, 0x319E4F1D, 0xE882D22F],
}


def _load_reconciliation(rep: str) -> list:
    """返回 [(resolved_path, original_path, status)] — 仅 EXACT_PATH/UNIQUE_RELOCATED。"""
    out = []
    with open(rep, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            status = (row.get("resolution_status") or "").strip()
            resolved = (row.get("resolved_path") or "").strip()
            if status in ("EXACT_PATH", "UNIQUE_RELOCATED"):
                if not resolved:
                    raise SystemExit(f"[ERROR] {status} 行但 resolved_path 为空: {row}")
                out.append((resolved, row.get("original_path", ""), status))
    return out


def _count_signals(path: str) -> dict:
    """返回 {signal_name: count} 或抛错 (path 不存在/不可读)。"""
    from dbpf_fast import safe_parse
    if not os.path.isfile(path):
        raise FileNotFoundError(f"path 不存在/不可读: {path}")
    idx, err = safe_parse(path)
    if err or idx is None:
        raise RuntimeError(f"DBPF 解析失败: {err or 'idx=None'}")
    counts = Counter()
    try:
        for e in idx.entries:
            if e.type_id in FUNCTIONAL_SIGNALS:
                counts[FUNCTIONAL_SIGNALS[e.type_id]] += 1
    except Exception as ex:
        raise RuntimeError(f"读资源异常: {ex}")
    return dict(counts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reconciliation", required=True,
                    help="output/pose_path_reconciliation_448.csv (Windows PASS)")
    ap.add_argument("--out", default="output/functional_signal_census_448.csv")
    a = ap.parse_args()

    pairs = _load_reconciliation(a.reconciliation)
    print(f"[input] reconciliation EXACT_PATH/UNIQUE_RELOCATED rows = {len(pairs)}")

    rows = []
    errors = []
    for resolved, orig, status in pairs:
        base = os.path.basename(resolved)
        try:
            cnt = _count_signals(resolved)
        except Exception as e:
            # ERROR: 不可当 signal=0 放行
            rows.append({
                "package_path": resolved, "basename": base, "status": status,
                **{F"{FUNCTIONAL_SIGNALS[t]}_count": "ERROR" for t in SIGNAL_NAMES},
                "functional_signal_type_count": "ERROR",
                "functional_signal_resource_count": "ERROR",
                "signal_signature": f"ERROR:{e}",
            })
            errors.append((resolved, str(e)))
            continue
        sig_present = [FUNCTIONAL_SIGNALS[t] for t in SIGNAL_NAMES if cnt.get(FUNCTIONAL_SIGNALS[t], 0) > 0]
        sig_counts = [f"{FUNCTIONAL_SIGNALS[t]}={cnt.get(FUNCTIONAL_SIGNALS[t],0)}" for t in SIGNAL_NAMES if cnt.get(FUNCTIONAL_SIGNALS[t],0) > 0]
        rows.append({
            "package_path": resolved, "basename": base, "status": status,
            **{F"{FUNCTIONAL_SIGNALS[t]}_count": cnt.get(FUNCTIONAL_SIGNALS[t], 0) for t in SIGNAL_NAMES},
            "functional_signal_type_count": len(sig_present),
            "functional_signal_resource_count": sum(cnt.values()),
            "signal_signature": "+".join(sig_counts) if sig_counts else "NONE",
        })

    # ---- aggregate ----
    def norm(r):
        return r if r["functional_signal_type_count"] != "ERROR" else None

    valid = [r for r in rows if r["functional_signal_type_count"] != "ERROR"]
    type_hits = Counter()
    for r in valid:
        for t in SIGNAL_NAMES:
            if r[f"{FUNCTIONAL_SIGNALS[t]}_count"] > 0:
                type_hits[FUNCTIONAL_SIGNALS[t]] += 1
    any_signal = sum(1 for r in valid if r["functional_signal_type_count"] > 0)
    ge2 = sum(1 for r in valid if r["functional_signal_type_count"] >= 2)
    ge3 = sum(1 for r in valid if r["functional_signal_type_count"] >= 3)
    cooc = {}
    for name, tids in COOC.items():
        cooc[name] = 0
        for r in valid:
            if all(r[f"{FUNCTIONAL_SIGNALS[t]}_count"] > 0 for t in tids):
                cooc[name] += 1

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    fieldnames = ["package_path", "basename", "status"] + [F"{FUNCTIONAL_SIGNALS[t]}_count" for t in SIGNAL_NAMES] + [
        "functional_signal_type_count", "functional_signal_resource_count", "signal_signature"]
    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"[WROTE] {a.out} ({len(rows)} 行, 只读)")

    # ---- 打印 aggregate ----
    print("\n===== aggregate (per type: 命中包数) =====")
    for name in [FUNCTIONAL_SIGNALS[t] for t in SIGNAL_NAMES]:
        print(f"  {name:10s} 命中 {type_hits.get(name,0)} 包")
    print(f"\n  任意 signal 命中: {any_signal} 包")
    print(f"  >=2 signal types: {ge2} 包")
    print(f"  >=3 signal types: {ge3} 包")
    print("\n===== co-occurrence =====")
    for name, n in cooc.items():
        print(f"  {name:26s} {n} 包")
    print("\n===== 已知锚点 =====")
    for r in rows:
        b = r["basename"]
        tag = ""
        if b == KNOWN_FUNCTIONAL:
            tag = "  <-- known-functional (Kritical)"
        elif b in KNOWN_POSE:
            tag = "  <-- known-Pose (真机确认)"
        if tag:
            print(f"  {b}: type={r['functional_signal_type_count']} res={r['functional_signal_resource_count']} "
                  f"sig=[{r['signal_signature']}]{tag}")

    # ---- ERROR 报告 ----
    if errors:
        print(f"\n[ERROR] {len(errors)} 包不可读(已标ERROR, 未当0放行):")
        for p, e in errors[:20]:
            print(f"  {os.path.basename(p)}: {e}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
