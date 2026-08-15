#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
production_resolver.py —— Phase 3B2-SIDECAR run2 production resolver + 多源一致性校验
=========================================================================================
背景: TITLE407 + DESC190 + production overlay 217 已在真实 Windows FINAL_HARD_GATE PASS 并冻结。
run2 的 gen_cohort_sidecars 必须只用这 3 个 production final 作为生产终态源:
    TITLE      : output/translation_done_title_final.csv        (407)
    DESCRIPTION: output/translation_done_desc_final.csv         (190)
    overrides  : output/translation_overrides.production.csv    (217  derived overlay)

严禁把以下作为 production final source (只能作历史证据/缓存, 不能覆盖 final):
    translation_done_batch_title.csv
    translation_done_batch_title_retry.csv
    translation_done_batch_title_retry_v2.csv
    translation_done_batch_desc.csv
    translation_cache.db

fail-closed 铁律 (本模块核心):
  * 多源一致性: 同一个 (tid, norm_source) 若出现在 ≥2 个 production source,
    其 action 类别 (TRANSLATE 与 KEEP 互斥) 与 translation 必须一致;
    任何不一致 -> HARD-FAIL (绝不“后加载覆盖前加载”解决)。
  * 禁止源: 若调用方把 batch/retry/desc-done/cache 当作 production source 传入,
    一律 HARD-FAIL (文件名黑名单)。
  * 缺失列 / 0 行 / 文件不存在 -> HARD-FAIL (启动即 FAIL, 禁止静默空表)。
  * resolve() 语义: 返回 (translation, tag):
      tag='KEEP'                        -> 已审核保持原文 (合法, 不译)
      tag='TRANSLATE' (去重后)           -> 有效译文
      tag='SOURCE_MISMATCH'             -> tid 命中但 normalized source_text 不符 (fail-fast)
      tag='MISSING'                     -> 三 production 源均无终态 (fail-fast)

  注意: 本模块只消费 3 个 production final; 旧 catalog terminal (frozen) 作为
  precedence 最低的 fallback 仅当 3 源全无时查询 —— 由 gen_cohort_sidecars 的
  production terminal override / final TITLE / final DESC / 旧 catalog 顺序承载。
"""
import csv
from pathlib import Path

try:
    from phase2a_catalog import norm_text
except Exception:
    def norm_text(s):
        return (s or "").strip()

# translation_id 派生 (与 frozen phase2b / 旧 resolver 完全一致)
try:
    from phase2a_catalog import source_hash, make_translation_id
except Exception:
    import hashlib

    def source_hash(s):
        return int(hashlib.md5((s or "").encode("utf-8")).hexdigest()[:8], 16)

    def make_translation_id(h, locale):
        # 保守实现: 仅当无法 import 权威版本时的退化 (生产须用权威)
        return f"T_{h:08x}_g{locale}"


# 文件名黑名单: 这些只能做历史证据/缓存, 绝不能作为 production final source
BANNED_PRODUCTION_SOURCE = {
    "translation_done_batch_title.csv",
    "translation_done_batch_title_retry.csv",
    "translation_done_batch_title_retry_v2.csv",
    "translation_done_batch_desc.csv",
    "translation_cache.db",
}

# 每个 production 源应预期的行数 (用户冻结值; 违反即 HARD-FAIL)
EXPECTED_ROWS = {
    "title_final": 407,
    "desc_final": 190,
    "production_overlay": 217,
}


class ProductionResolver:
    """多 production-finaal 一致性 resolver (fail-closed)。"""

    def __init__(self, title_final=None, desc_final=None, production_overlay=None,
                 catalog=None):
        # 三类源 (translation_id, norm_source) -> dict(status/action/translation/origin/from)
        self.title = {}
        self.desc = {}
        self.overlay = {}
        self.catalog = {}          # 旧 frozen catalog terminal (precedence 最低)

        # 只读载入 (缺文件/0行/缺列 -> 抛错启动 FAIL)
        if title_final:
            self._banned_guard(str(title_final))
            self.title = self._load(title_final, "title_final", expected=EXPECTED_ROWS["title_final"])
        if desc_final:
            self._banned_guard(str(desc_final))
            self.desc = self._load(desc_final, "desc_final", expected=EXPECTED_ROWS["desc_final"])
        if production_overlay:
            self._banned_guard(str(production_overlay))
            self.overlay = self._load(production_overlay, "production_overlay",
                                      expected=EXPECTED_ROWS["production_overlay"])
        if catalog:
            self._banned_guard(str(catalog))
            self.catalog = self._load(catalog, "catalog", expected=None)

        self.consistency_errors = self._check_multi_source_consistency()

    # ------------------------------------------------------------------ 载入
    def _banned_guard(self, path):
        name = Path(path).name
        if name in BANNED_PRODUCTION_SOURCE:
            raise RuntimeError(
                f"preflight FAIL: {name!r} 是禁止的 production final source "
                f"(batch/retry/cache 只能作历史证据, 不能覆盖 final)")

    def _load(self, path, label, expected):
        p = Path(path)
        if not p.exists():
            raise RuntimeError(f"preflight FAIL: {label} 文件不存在: {p}")
        rows = []
        with open(p, encoding="utf-8-sig") as f:
            rdr = csv.DictReader(f)
            hdr = list(rdr.fieldnames or [])
            for need in ("translation_id", "source_text"):
                if need not in hdr:
                    raise RuntimeError(f"preflight FAIL: {label} schema 缺列 {need!r}; 实际列={hdr}")
            has_tr = "translation" in hdr
            if not has_tr:
                raise RuntimeError(f"preflight FAIL: {label} schema 缺列 'translation'; 实际列={hdr}")
            for r in rdr:
                tid = (r.get("translation_id") or "").strip()
                stxt = (r.get("source_text") or "").strip()
                if not tid or not stxt:
                    continue
                rows.append((tid, stxt, (r.get("translation") or "").strip(),
                             (r.get("status") or r.get("action") or r.get("origin") or "").strip()))
        if expected is not None and len(rows) != expected:
            raise RuntimeError(
                f"preflight FAIL: {label} 行数 {len(rows)} != 期望 {expected} (冻结值)")
        out = {}
        for tid, stxt, tr, st in rows:
            out[(tid, norm_text(stxt))] = {
                "translation": tr,
                "status": st.upper(),
                "is_keep": ("KEEP" in st.upper()),
                "source_text": stxt,
                "from": label,
            }
        if not out:
            raise RuntimeError(f"preflight FAIL: {label} 载入 0 行")
        return out

    # ------------------------------------------------------------------ 一致性
    def _check_multi_source_consistency(self):
        """同 (tid,norm_source) 在多个 production source 出现时, action 类别+译文必须一致。
        返回错误列表; 有错 -> HARD-FAIL (绝不后加载覆盖前加载)。"""
        errs = []
        sources = {
            "production_overlay": self.overlay,
            "title_final": self.title,
            "desc_final": self.desc,
            "catalog": self.catalog,
        }
        active = {k: v for k, v in sources.items() if v}
        keys = set()
        for v in active.values():
            keys |= set(v.keys())
        for k in sorted(keys, key=lambda x: (x[0], x[1])):
            hits = {name: v[k]["translation"] for name, v in active.items() if k in v}
            keeps = {name for name, v in active.items() if k in v and v[k]["is_keep"]}
            trans = {name for name, v in active.items() if k in v and not v[k]["is_keep"]}
            # action 类别冲突: 一源 KEEP 另一源 TRANSLATE
            if keeps and trans:
                errs.append(
                    f"(tid={k[0]}) action 冲突: KEEP={sorted(keeps)} vs TRANSLATE={sorted(trans)}")
                continue
            # 译文不一致 (KEEP 源 translation 为空, 不参与)
            if len(trans) >= 2:
                tvals = {name: hits[name] for name in trans}
                uniq = set(tvals.values())
                if len(uniq) > 1:
                    errs.append(
                        f"(tid={k[0]}) translation 不一致: " +
                        "; ".join(f"{n}={v!r}" for n, v in sorted(tvals.items())))
            # source_text 原字符串不一致 (norm 相同但原文不同)
            raw = {name: v[k]["source_text"] for name, v in active.items() if k in v}
            if len(set(raw.values())) > 1:
                errs.append(
                    f"(tid={k[0]}) norm_source 相同但原 source_text 不一致: " +
                    "; ".join(f"{n}={v!r}" for n, v in sorted(raw.items())))
        # 同 norm_text 不同 raw 但同 tid(跨文件) 已由上面覆盖; 同 tid 不同 norm_source:
        # 属不同 key, 各自独立合法。无额外处理。
        # 同 tid 出现在不同 source 但 norm 一致已核; 重复自身由 _load 覆盖。
        return errs

    def has_consistency_errors(self):
        return bool(self.consistency_errors)

    # ------------------------------------------------------------------ resolve
    def resolve(self, source_text):
        """(tid,norm_source) 在 3 production 源 + catalog 查询, 返回 (translation, tag)。
        precedence 仅当 key 只在单一源出现时生效; 多源出现已由一致性校验保证一致。"""
        if not source_text or not source_text.strip():
            return None, "MISSING"
        tid = make_translation_id(source_hash(norm_text(source_text)), 1)
        key = (tid, norm_text(source_text))
        for name in ("overlay", "title", "desc", "catalog"):
            d = getattr(self, name)
            hit = d.get(key)
            if hit is not None:
                if hit["is_keep"]:
                    return None, "KEEP"
                if hit["translation"]:
                    return hit["translation"], "TRANSLATE"
                return None, "MISSING"
        # 同 tid 命中但 norm_source 不符 -> SOURCE_MISMATCH
        for name in ("overlay", "title", "desc", "catalog"):
            for (k_tid, k_ns), hit in getattr(self, name).items():
                if k_tid == tid and k_ns != norm_text(source_text):
                    return None, "SOURCE_MISMATCH"
        return None, "MISSING"


def make_production_resolver(title_final, desc_final, production_overlay, catalog=None):
    """便捷构造 + 一致性校验; 有错 -> raise RuntimeError(HARD-FAIL)。"""
    r = ProductionResolver(title_final, desc_final, production_overlay, catalog)
    if r.has_consistency_errors():
        raise RuntimeError(
            "preflight FAIL: production 多源一致性冲突:\n  " + "\n  ".join(r.consistency_errors))
    return r
