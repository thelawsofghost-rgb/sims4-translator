#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
production_resolver.py —— Phase 3B2-SIDECAR run2 production resolver（五源终态 + 一致性校验）
================================================================================================
背景: 真实 Windows historical-source reduction audit PASS 后, production resolver 输入
正式冻结为五份 (preflight 与 generation 必须使用完全相同):

  production_overlay  = 217  (explicit latest terminal override, 最高权威)
  title_final         = 407  (新批次 final)
  desc_final          = 190  (新批次 final)
  translation_done    = 1888 (historical final translation fallback)
  translation_catalog = 3540 (decision/index ONLY, 不是 final payload)

以下文件【退出】production resolver, 只保留 provenance/audit (base114 已被
production overlay217 完整包含, 见 reduction audit):
  translation_overrides.final2.csv
  translation_overrides.csv            (= base114)

严禁把以下作为 production final source:
  translation_overrides.csv            (base114, 已退出 → 只读 provenance)
  translation_overrides.final2.csv     (final2, 已退出 → 只读 provenance)
  translation_done_batch_title.csv
  translation_done_batch_title_retry.csv
  translation_done_batch_title_retry_v2.csv
  translation_done_batch_desc.csv
  translation_cache.db                (phrase cache, 非 final QA artifact, 禁止作 payload)

resolver 语义 (层级由高到低, 但【同级/不同级真正 final 源间不允许静默覆盖】):
  1. production_overlay    = 最新 explicit terminal outcome (最高权威)
  2. title_final / desc_final = 新批次 final
  3. translation_done      = historical final translation fallback
  4. translation_catalog   = decision/index only (不提供 translation payload)

catalog decision 语义:
  KEEP     -> 无更高层终态时 -> KEEP
  TRANSLATE-> 必须能从 overlay/title/desc/done 找到非空 final translation, 否则 MISSING
  REVIEW   -> 有更高层终态 -> superseded; 无更高层终态 -> unresolved

禁止使用: catalog.translation 与 translation_cache.db 作为 final translation payload。

superseded (合法历史修订, 仅计数, 绝不 HARD-FAIL):
  production overlay > historical done           -> 计 historical_superseded
  production final   > old catalog decision      -> 计 catalog_decision_superseded

真正 HARD-FAIL (本模块核心, fail-closed):
  A) source_text mismatch        : tid 命中但 normalized source_text 不符
  B) source 内部 duplicate key 且 outcome 不同
  C) title_final vs desc_final 同 key outcome 不一致
  D) 当前 cohort catalog TRANSLATE 最终无 payload (MISSING)
  E) 当前 cohort REVIEW 且无更高层终态 (unresolved)

多源一致性铁律 (同 key 出现在 ≥2 production source):
  * action 类别必须一致 (TRANSLATE/KEEP 互斥); 冲突 -> HARD-FAIL (C 类)
  * TRANSLATE 译文必须一致; 不一致 -> HARD-FAIL
  * 高权威源与低权威源同 key 允许"修订" (overlay 定稿覆盖 done) -> superseded 计数, 不冲突
  * catalog.translation 不作为 payload 比较 (decision/index only)
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
        return f"T_{h:08x}_g{locale}"


# 文件名黑名单: 这些只能做历史证据/缓存/provenance, 绝不能作为 production final source
BANNED_PRODUCTION_SOURCE = {
    # 已退出 resolver 的历史 final 层 (只读 provenance/audit)
    "translation_overrides.csv",           # base114
    "translation_overrides.final2.csv",    # final2
    # batch/retry/desc-done 只作历史证据
    "translation_done_batch_title.csv",
    "translation_done_batch_title_retry.csv",
    "translation_done_batch_title_retry_v2.csv",
    "translation_done_batch_desc.csv",
    # phrase cache 非 final QA artifact
    "translation_cache.db",
}

# 每个 production 源应预期的行/非空数 (用户冻结值; 违反即 HARD-FAIL)
#   注意: done 与 catalog 用 unique (tid,norm_source) 判定; overlay/title/desc 用行数
EXPECTED_ROWS = {
    "title_final": 407,
    "desc_final": 190,
    "production_overlay": 217,
    "translation_done_nonempty_unique": 1888,
    "catalog_rows": 3540,
}


class ProductionResolver:
    """五源 production resolver (fail-closed)。

    源 (translation_id, norm_source) -> dict(...):
      self.overlay  : production_overlay  (最高权威 terminal override)
      self.title    : title_final         (新批次 final)
      self.desc     : desc_final          (新批次 final)
      self.done     : translation_done    (historical final fallback, 非空 payload 只)
      self.catalog  : translation_catalog (decision/index only)

    catalog 不含 translation payload 判定; done 只保留非空 translation。
    """

    def __init__(self, title_final=None, desc_final=None, production_overlay=None,
                 translation_done=None, translation_catalog=None):
        self.title = {}
        self.desc = {}
        self.overlay = {}
        self.done = {}
        self.catalog = {}
        self._internal_dup = {}   # label -> [(key, sorted(translation_values))]  内部重复 key 且 outcome 不同

        # 只读载入 (缺文件/0行/缺列 -> 抛错启动 FAIL)
        if title_final:
            self._banned_guard(str(title_final))
            self.title = self._load(title_final, "title_final",
                                    expected=EXPECTED_ROWS["title_final"])
        if desc_final:
            self._banned_guard(str(desc_final))
            self.desc = self._load(desc_final, "desc_final",
                                   expected=EXPECTED_ROWS["desc_final"])
        if production_overlay:
            self._banned_guard(str(production_overlay))
            self.overlay = self._load(production_overlay, "production_overlay",
                                      expected=EXPECTED_ROWS["production_overlay"])
        if translation_done:
            self._banned_guard(str(translation_done))
            self.done = self._load(translation_done, "translation_done",
                                   expected=None, nonempty_only=True)
        if translation_catalog:
            self._banned_guard(str(translation_catalog))
            self.catalog = self._load(translation_catalog, "translation_catalog",
                                      expected=EXPECTED_ROWS["catalog_rows"],
                                      nonempty_only=False, allow_empty_translation=True)

        # done 期望 1888 nonempty unique
        if self.done and len(self.done) != EXPECTED_ROWS["translation_done_nonempty_unique"]:
            raise RuntimeError(
                f"preflight FAIL: translation_done nonempty unique {len(self.done)} "
                f"!= 期望 {EXPECTED_ROWS['translation_done_nonempty_unique']}")

        self.consistency_errors = self._check_multi_source_consistency()
        # superseded 计数 (仅报告, 不 HARD-FAIL)
        self.historical_superseded, self.catalog_decision_superseded = self._count_superseded()

    # ------------------------------------------------------------------ 载入
    def _banned_guard(self, path):
        name = Path(path).name
        if name in BANNED_PRODUCTION_SOURCE:
            raise RuntimeError(
                f"preflight FAIL: {name!r} 是禁止的 production final source "
                f"(已退出 resolver / batch / retry / cache, 只能作历史证据)")

    def _load(self, path, label, expected, nonempty_only=False, allow_empty_translation=False):
        p = Path(path)
        if not p.exists():
            raise RuntimeError(f"preflight FAIL: {label} 文件不存在: {p}")
        rows = []
        dup_any = {}     # key -> set(translation)  内部 duplicate 检测
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
                tr = (r.get("translation") or "").strip()
                if nonempty_only and not tr:
                    continue                      # done: 只保留非空 payload
                key = (tid, norm_text(stxt))
                st = (r.get("status") or r.get("action") or r.get("origin") or "").strip()
                dup_any.setdefault(key, set()).add(tr)
                rows.append((key, tr, st, stxt))
        if expected is not None and len(rows) != expected:
            raise RuntimeError(
                f"preflight FAIL: {label} 行数 {len(rows)} != 期望 {expected} (冻结值)")
        out = {}
        seen_dup_key = set()
        internal_dup = []
        for key, tr, st, stxt in rows:
            if len(dup_any[key]) > 1 and key not in seen_dup_key:
                seen_dup_key.add(key)
                internal_dup.append((key, sorted(dup_any[key])))   # 内部重复且 outcome 不同, 每 key 仅报一次
            out[key] = {
                "translation": tr,
                "status": st.upper(),
                "is_keep": ("KEEP" in st.upper()),
                "source_text": stxt,
                "from": label,
            }
        self._internal_dup[label] = internal_dup   # HARD-FAIL (B 类), 按 label 记录
        if not out:
            raise RuntimeError(f"preflight FAIL: {label} 载入 0 行")
        return out

    # ------------------------------------------------------------------ 一致性
    def _check_multi_source_consistency(self):
        """真正 final 源之间冲突 -> HARD-FAIL。
        参与 equality 冲突判定的 final 源: overlay/title/desc (done 是 historical fallback,
        被 overlay/title/desc 覆盖属合法 superseded, 不算冲突; catalog 是 decision only)。"""
        errs = []
        sources = {
            "title_final": self.title,
            "desc_final": self.desc,
            "production_overlay": self.overlay,
        }
        active = {k: v for k, v in sources.items() if v}
        keys = set()
        for v in active.values():
            keys |= set(v.keys())

        # C 类: title vs desc 同 key outcome 不一致 (含 action 类别与译文)
        for k in sorted(keys, key=lambda x: (x[0], x[1])):
            hits = {name: v[k] for name, v in active.items() if k in v}
            if len(hits) < 2:
                continue
            # action 类别冲突
            keeps = {n for n, hit in hits.items() if hit["is_keep"]}
            trans = {n for n, hit in hits.items() if not hit["is_keep"]}
            if keeps and trans:
                errs.append(
                    f"(tid={k[0]}) action 冲突: KEEP={sorted(keeps)} vs TRANSLATE={sorted(trans)}")
                continue
            # 译文不一致 (KEEP 源 translation 空, 不参与)
            if len(trans) >= 2:
                tvals = {n: hits[n]["translation"] for n in trans}
                if len(set(tvals.values())) > 1:
                    errs.append(
                        f"(tid={k[0]}) translation 不一致: " +
                        "; ".join(f"{n}={v!r}" for n, v in sorted(tvals.items())))
            # norm_source 相同但原 source_text 不一致
            raw = {n: v["source_text"] for n, v in hits.items()}
            if len(set(raw.values())) > 1:
                errs.append(
                    f"(tid={k[0]}) norm_source 相同但原 source_text 不一致: " +
                    "; ".join(f"{n}={v!r}" for n, v in sorted(raw.items())))

        # B 类: 各 final 源内部 duplicate key 且 outcome 不同
        label_map = {"title_final": "title", "desc_final": "desc",
                     "production_overlay": "overlay", "translation_done": "done"}
        for label, attr in label_map.items():
            if not getattr(self, attr):
                continue
            for (key, vals) in self._internal_dup.get(label, []):
                errs.append(f"{label} 内部 duplicate key ({key[0]}) outcome 不同: {vals}")

        return errs

    def _count_superseded(self):
        """合法历史修订计数 (仅报告, 不 HARD-FAIL):
          historical_superseded      = overlay/title/desc 覆盖 done 的 key 数
          catalog_decision_superseded = overlay/title/desc 覆盖 catalog decision 的 key 数
        """
        hi = set(self.overlay) | set(self.title) | set(self.desc)
        historical = len(set(self.done) & hi)
        cat_keys = set(self.catalog)
        catalog_sup = len(cat_keys & hi)
        return historical, catalog_sup

    def has_consistency_errors(self):
        return bool(self.consistency_errors)

    # ------------------------------------------------------------------ 决策
    def resolve(self, source_text):
        """当前 cohort 的 source 决策。

        返回 (payload, tag):
          (translation, 'TRANSLATE')   有终态译文 (来自 overlay/title/desc/done)
          (None, 'KEEP')               catalog KEEP 且无更高层终态 (不译)
          (None, 'SOURCE_MISMATCH')    tid 命中但 norm_source 不符 (fail-fast)
          (None, 'MISSING')            catalog TRANSLATE 无 payload / 无终态 (D 类 HARD-FAIL)
          (None, 'MISSING_REVIEW')     catalog REVIEW 无更高层终态 (E 类 HARD-FAIL)
        """
        if not source_text or not source_text.strip():
            return None, "MISSING"
        tid = make_translation_id(source_hash(norm_text(source_text)), 1)
        key = (tid, norm_text(source_text))

        # 真正 final payload 源 (不含 catalog.translation): overlay > title > desc > done
        payload = None
        for name in ("overlay", "title", "desc", "done"):
            hit = getattr(self, name).get(key)
            if hit is not None:
                if hit["is_keep"]:
                    return None, "KEEP"            # 更高权威已 KEEP
                if hit["translation"]:
                    payload = hit["translation"]
                    break                          # 最高权威命中有译文即可
        if payload is not None:
            return payload, "TRANSLATE"

        # 无更高层 payload: 查 catalog decision
        cat = self.catalog.get(key)
        if cat is not None:
            dec = cat["status"] if cat["status"] in ("KEEP", "TRANSLATE", "APPROVED", "REVIEW") else \
                (cat["status"] or "")
            if dec in ("KEEP",):
                return None, "KEEP"
            if dec in ("TRANSLATE", "APPROVED"):
                # 需要 final payload; 已查过 overlay/title/desc/done 均无 -> MISSING (D 类)
                return None, "MISSING"
            if dec == "REVIEW":
                return None, "MISSING_REVIEW"      # 无更高层终态 (E 类)
            return None, "MISSING"

        # 未在 catalog: 若在任一 final payload 源命中也算已解决 (新源但已有终态)
        if payload is not None:
            return payload, "TRANSLATE"
        # tid 命中但 norm_source 不符 -> SOURCE_MISMATCH
        for name in ("overlay", "title", "desc", "done", "catalog"):
            for (k_tid, k_ns), hit in getattr(self, name).items():
                if k_tid == tid and k_ns != norm_text(source_text):
                    return None, "SOURCE_MISMATCH"
        return None, "MISSING"


def make_production_resolver(title_final, desc_final, production_overlay,
                             translation_done=None, translation_catalog=None):
    """便捷构造 + 一致性校验; 有错 -> raise RuntimeError(HARD-FAIL)。"""
    r = ProductionResolver(title_final, desc_final, production_overlay,
                           translation_done=translation_done,
                           translation_catalog=translation_catalog)
    if r.has_consistency_errors():
        raise RuntimeError(
            "preflight FAIL: production 多源一致性冲突:\n  " + "\n  ".join(r.consistency_errors))
    return r
