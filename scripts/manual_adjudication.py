#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
manual_adjudication.py — 显式人工裁决 registry (SHA256 精确身份匹配)
=====================================================================
configs/pose_manual_adjudications.csv 的加载/校验 + 精确身份匹配。

裁决原则 (用户 2026-08-15 终审):
  * 人工裁决 = production classification precedence 的最末层:
        baseline classification  ->  strong auto gate  ->  exact manual adjudication
  * manual adjudication 只能作用于【精确匹配的 frozen package identity】(package SHA256)。
    禁止按 basename / author / 路径黑名单匹配。
  * basename / path 只是可读诊断字段 (human-readable), 永不用于匹配。
  * 若文件内容更新导致 SHA256 改变 -> 旧人工裁决【不继承】-> fail conservative (重新 review),
    绝不用旧 hash 去匹配新文件。
  * 只对 decision=SKIP 的精确匹配行强制 SKIP_FALSE_POSITIVE_INTERNAL_POSE;
    不移除任何包 (KEEP 由 baseline/strong 自然裁决, 无需 registry 排除规则)。
  * 不扩大 STRONG_OBJECT_FOOTPRINT 自动 gate (gate 保持冻结)。

Registry schema (CSV, BOM utf-8-sig):
  package_sha256       匹配身份 (唯一、必填; 64-hex)
  decision             SKIP 或 KEEP (仅 SKIP 会强制改 status)
  semantic_status      对应 status (SKIP_FALSE_POSITIVE_INTERNAL_POSE)
  decision_basis       MANUAL_REVIEW_CONFIRMED 等
  evidence_note        人工复核备注 (自由文本)
  basename             可读诊断 (不用于匹配)
  path                 可读诊断 (不用于匹配)
"""
import os, sys, csv, hashlib
from pathlib import Path

DEFAULT_REGISTRY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "configs", "pose_manual_adjudications.csv")

REQ_COLS = ["package_sha256", "decision", "semantic_status", "decision_basis", "evidence_note"]
OPT_COLS = ["basename", "path"]  # 只读诊断, 不参与匹配


def sha256_of(path: str) -> str:
    """计算包文件 SHA256 (流式, 大文件安全); 失败抛 IOError。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _norm_sha(s: str) -> str:
    s = (s or "").strip().lower()
    if not s:
        return ""
    # 允许带 0x 前缀 / 空格 / 连字符; 归一化为 64-hex (无前缀)
    s = s.replace("0x", "").replace("-", "").replace(" ", "").lower()
    if len(s) != 64 or any(c not in "0123456789abcdef" for c in s):
        raise ValueError(f"非法 SHA256: {s!r} (需 64-hex)")
    return s


def load_registry(path: str = None) -> dict:
    """加载并校验 registry。返回 {sha256: row} (sha256 已归一化)。

    - 重复/非法 sha256 -> raise ValueError (fail-closed, 不猜测)。
    - 缺 REQUIRED 列 -> raise ValueError。
    - decision/语义字段不做枚举强约束 (可由新值扩展), 但 SKIP 行的
      semantic_status 必须非空, 否则无法安全执行。
    """
    path = path or DEFAULT_REGISTRY
    if not os.path.isfile(path):
        # 无 registry = 无人工裁决层 (不 fail); 由调用方决定. 返回空 dict.
        return {}
    rows = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or []
        for c in REQ_COLS:
            if c not in cols:
                raise ValueError(f"registry 缺必需列: {c} (实际: {cols})")
        for ln, r in enumerate(reader, start=2):
            sha = _norm_sha(r.get("package_sha256"))
            if not sha:
                raise ValueError(f"registry 第 {ln} 行 package_sha256 为空")
            if sha in rows:
                raise ValueError(f"registry 重复 SHA256: {sha} (第 {ln} 行)")
            dec = (r.get("decision") or "").strip()
            if dec == "SKIP":
                ss = (r.get("semantic_status") or "").strip()
                db = (r.get("decision_basis") or "").strip()
                if not ss:
                    raise ValueError(f"registry 第 {ln} 行 decision=SKIP 但 semantic_status 为空 (无法安全执行)")
                if not db:
                    raise ValueError(f"registry 第 {ln} 行 decision=SKIP 但 decision_basis 为空")
            rows[sha] = {
                "package_sha256": sha,
                "decision": dec or "KEEP",
                "semantic_status": (r.get("semantic_status") or "").strip(),
                "decision_basis": (r.get("decision_basis") or "").strip(),
                "evidence_note": (r.get("evidence_note") or "").strip(),
                "basename": (r.get("basename") or "").strip(),
                "path": (r.get("path") or "").strip(),
                "_line": ln,
            }
    return rows


class ManualAdjudicator:
    """按精确 SHA256 身份匹配的人工裁决器 (生产层只读使用)。

    用法:
        ma = ManualAdjudicator(registry_path)   # registry 不存在 -> 空, 不报错
        ma.load()                               # 显式 (重新) 加载
        hit = ma.match(package_path)            # 计算 sha256 + 精确匹配 -> row | None
    """

    def __init__(self, registry_path: str = None):
        self.registry_path = registry_path or DEFAULT_REGISTRY
        self._rows = None

    def load(self):
        self._rows = load_registry(self.registry_path)
        return self

    @property
    def rows(self):
        if self._rows is None:
            self.load()
        return self._rows

    def match(self, package_path: str) -> dict | None:
        """对真实包路径计算 SHA256 并精确匹配 registry; 命中返回 registry row。"""
        if not package_path or not os.path.isfile(package_path):
            return None
        if not self.rows:
            return None
        sha = sha256_of(package_path)
        row = self.rows.get(sha)
        if row is None:
            return None
        # 仅 SKIP 生效; 内容已变 (hash 不匹配) 自然不命中 -> 不继承, fail conservative
        if row["decision"] != "SKIP":
            return None
        return row

    def apply(self, row: dict) -> tuple[bool, dict | None]:
        """对 scan 产出的 row (含 package_path) 应用人工裁决。

        生产 precedence: baseline -> strong auto gate -> manual (本层)。
        仅当精确 SHA256 命中且 decision=SKIP 时:
            强制 semantic_status=SKIP_FALSE_POSITIVE_INTERNAL_POSE (覆盖 baseline/strong),
            reason 标 MANUAL_REVIEW_CONFIRMED + evidence_note。
        返回 (applied, registry_row)。
        """
        p = row.get("package_path")
        hit = self.match(p)
        if hit is None:
            return False, None
        row["manual_adjudicated"] = 1
        row["manual_sha256"] = hit["package_sha256"]
        row["manual_decision"] = hit["decision"]
        row["manual_basis"] = hit["decision_basis"]
        row["manual_evidence"] = hit["evidence_note"]
        ss = hit["semantic_status"] or "SKIP_FALSE_POSITIVE_INTERNAL_POSE"
        row["status"] = ss
        row["semantic_status"] = ss
        row["decision_subtype"] = "MANUAL_REVIEW_CONFIRMED"
        row["reason"] = (f"MANUAL_REVIEW_CONFIRMED (exact SHA256 identity "
                         f"{hit['package_sha256'][:12]}…): {hit['decision_basis']}"
                         + (f" | {hit['evidence_note']}" if hit['evidence_note'] else ""))
        return True, hit


def main():
    import argparse
    ap = argparse.ArgumentParser(description="manual adjudication registry 校验/匹配工具")
    ap.add_argument("--registry", default=DEFAULT_REGISTRY, help="registry CSV 路径")
    ap.add_argument("--check", action="store_true", help="仅校验 registry schema")
    ap.add_argument("--path", help="对给定的真实包路径计算 SHA256 并匹配 registry")
    a = ap.parse_args()

    try:
        rows = load_registry(a.registry)
    except ValueError as ex:
        print(f"[ERROR] registry 校验失败: {ex}")
        return 2

    if a.check:
        n_skip = sum(1 for r in rows.values() if r["decision"] == "SKIP")
        n_keep = sum(1 for r in rows.values() if r["decision"] == "KEEP")
        print(f"[OK] registry: {a.registry}")
        print(f"  SH256 记录数: {len(rows)}  (SKIP={n_skip}, KEEP={n_keep})")
        if rows:
            print("  SHA256 全部合法且唯一 ✓")
        return 0

    if a.path:
        if not os.path.isfile(a.path):
            print(f"[ERROR] 包文件不存在: {a.path}")
            return 2
        sha = sha256_of(a.path)
        hit = rows.get(sha)
        print(f"SHA256: {sha}")
        if hit is None:
            print("  [NO-MATCH] 该包不在 manual registry (无人工裁决, 走 baseline/strong)")
        else:
            print(f"  [HIT] decision={hit['decision']} semantic_status={hit['semantic_status']} "
                  f"basis={hit['decision_basis']}")
            if hit["evidence_note"]:
                print(f"  evidence_note: {hit['evidence_note']}")
        return 0

    if rows:
        print(f"[OK] registry {a.registry} 加载成功, {len(rows)} 条.")
    else:
        print(f"[INFO] registry 不存在或为空: {a.registry} (无人工裁决层)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
