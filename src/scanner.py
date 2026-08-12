#!/usr/bin/env python3
"""
Scanner v0.1 — 主扫描编排器

Phase 1 严格只读:
  - 扫描
  - 分类
  - 提取显示文本
  - 生成报告 (scan_report.csv / summary.txt / performance_report.txt / errors.log)
  - 修改文件数必须 = 0

两阶段扫描:
  Stage 1 (浅): 读 DBPF index, 快速排除普通 CC
  Stage 2 (深): 只对含 CLIP(第一门槛) + XML/Snippet 的候选, 读小体积 XML 提取文本
"""

import csv
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Any

from backend import get_backend
from cache import ScanCache
from classifier import Classifier, ConfLevel, Classification
from dbpf_fast import safe_parse, UnsupportedDBPFError
from text_extractor import (
    extract_ww_display_texts,
    extract_stbl_strings,
    classify_text_intent,
)
from resource_types import RESOURCE_TYPES, list_verified_ids, verified_type_count


class Scanner:
    def __init__(self, mods_path: Path, output_dir: Path, cfg: dict,
                 dry_run: bool = True, force_rescan: bool = False):
        self.mods_path = mods_path
        self.output_dir = output_dir
        self.dry_run = dry_run  # Phase 1 恒 True
        self.force_rescan = force_rescan
        self.cfg = cfg
        self.classifier = Classifier()
        self.cache = ScanCache(output_dir / "mod_index_cache.db")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 统计
        self.stats = {
            "packages_total": 0,
            "disk_size_bytes": 0,
            "disk_size_gb": 0.0,
            ConfLevel.CONFIRMED_WW: 0,
            ConfLevel.CONFIRMED_POSE: 0,
            ConfLevel.UNCERTAIN: 0,
            ConfLevel.NON_ANIMATION: 0,
            ConfLevel.ERROR: 0,
            ConfLevel.ERROR_UNSUPPORTED_DBPF: 0,
            "modified": 0,
            "visible_text": 0,
            "english": 0,
            "chinese": 0,
            "uncertain_text": 0,
            "skip_reasons": {},
        }

        self.scan_rows: List[Dict] = []
        self.errors: List[str] = []
        self.performance = {}

    # ---------------- 主入口 ----------------
    def run(self) -> Dict[str, Any]:
        t_total = time.time()
        t0 = time.time()
        files = self._collect_packages()
        self.performance["collect"] = time.time() - t0

        # Stage 1: 浅扫描
        t1 = time.time()
        candidates = self._stage1_shallow(files)
        self.performance["stage1"] = time.time() - t1

        # Stage 2: 深度扫描 (只对候选)
        t2 = time.time()
        self._stage2_deep(candidates)
        self.performance["stage2"] = time.time() - t2

        self.performance["total"] = time.time() - t_total

        # 生成报告
        self._write_reports()
        self._write_summary()
        self._write_performance()
        self._write_errors()

        self.cache.close()
        return self.stats

    # ---------------- Stage 0: 收集文件 ----------------
    def _collect_packages(self) -> List[Path]:
        pkgs = []
        self.stats["disk_size_bytes"] = 0
        for root, dirs, files in os.walk(self.mods_path):
            for fn in files:
                if fn.lower().endswith(".package"):
                    p = Path(root) / fn
                    try:
                        sz = p.stat().st_size
                    except OSError:
                        continue
                    self.stats["disk_size_bytes"] += sz
                    pkgs.append(p)
        self.stats["packages_total"] = len(pkgs)
        self.stats["disk_size_gb"] = round(self.stats["disk_size_bytes"] / (1024**3), 2)
        return pkgs

    # ---------------- Stage 1: 浅扫描 ----------------
    def _stage1_shallow(self, files: List[Path]) -> List[Path]:
        candidates = []
        for p in files:
            self.stats["packages_total"]  # already counted
            try:
                st = p.stat()
            except OSError:
                self._mark_error(p, "stat失败")
                continue

            # 增量缓存复用
            if not self.force_rescan:
                cached = self.cache.get(str(p))
                if cached and cached.get("scan_status") != "unavailable":
                    # 未变 → 复用
                    if self.cache.is_unchanged(str(p), st.st_size, st.st_mtime):
                        self._reuse_cached(p, cached)
                        continue

            # 解析索引
            idx, err = safe_parse(p)
            if err or idx is None:
                self._record_error(p, f"DBPF 解析失败: {err}")
                self._cache_failed(p, st, err)
                continue

            type_ids = {e.type_id for e in idx.entries}
            has_clip_guess = self._clip_present(type_ids)

            # 候选门槛: CLIP (第一门槛) + XML/Snippet 后级信号
            has_xml_signal = self._xml_signal(type_ids)
            is_candidate = has_clip_guess or has_xml_signal

            # 记录 Stage1 摘要到内存
            self._stage1_row(p, st, type_ids, is_candidate)
            if is_candidate:
                candidates.append(p)
            else:
                # 快速排除: 记为 NON_ANIMATION (无 CLIP/XML 信号)
                cls = Classification(level=ConfLevel.NON_ANIMATION, reason="浅扫描无动画信号, 非候选")
                self._cache_classified(p, st, cls, type_ids, has_clip_guess)
        return candidates

    # ---------------- Stage 2: 深度扫描 ----------------
    def _stage2_deep(self, candidates: List[Path]) -> None:
        for p in candidates:
            try:
                st = p.stat()
            except OSError:
                continue
            try:
                backend = get_backend("readonly").open(p)
                idx = backend.read_index()
                type_ids = {e.type_id for e in idx.entries}
                has_clip = self._clip_present(type_ids)
                stbl_present = self._stbl_present(type_ids)

                # 读取小体积 XML 文本 (候选才有)
                xml_texts = self._read_candidate_xmls(backend, idx.entries)

                cls = self.classifier.classify_from_texts(
                    type_ids=type_ids,
                    xml_texts=xml_texts,
                    stbl_present=stbl_present,
                )

                # 提取可见文本
                visible = []
                for txt in xml_texts:
                    visible += extract_ww_display_texts(txt)
                # STBL 提取 (仅对 CONFIRMED 相关)
                stbl_strings = []
                if cls.level in (ConfLevel.CONFIRMED_WW, ConfLevel.CONFIRMED_POSE):
                    for e in idx.entries:
                        if RESOURCE_TYPES.is_stbl(e.type_id):
                            data = backend.read_small_resource(e)
                            if data:
                                stbl_strings += extract_stbl_strings(data)

                # 文本意图计数
                eng = chn = unc = 0
                for t in visible:
                    intent = classify_text_intent(t)
                    if intent == "TRANSLATE":
                        eng += 1
                    elif intent == "CHINESE":
                        chn += 1
                    elif intent == "SKIP_UNCERTAIN":
                        unc += 1
                for _, t in stbl_strings:
                    intent = classify_text_intent(t)
                    if intent == "TRANSLATE":
                        eng += 1
                    elif intent == "CHINESE":
                        chn += 1
                    elif intent == "SKIP_UNCERTAIN":
                        unc += 1

                # 统计
                self._count_class(cls.level)
                if cls.level in (ConfLevel.CONFIRMED_WW, ConfLevel.CONFIRMED_POSE):
                    self.stats["visible_text"] += (eng + chn + unc)
                    self.stats["english"] += eng
                    self.stats["chinese"] += chn
                    self.stats["uncertain_text"] += unc

                self._cache_classified_full(p, st, cls, type_ids, has_clip, stbl_present,
                                            eng, chn, unc, visible)
                self._stage2_row(p, cls, eng, chn, unc)
                backend.close()
            except Exception as e:
                self._record_error(p, f"深扫描异常: {e}")
                self.stats[ConfLevel.ERROR] = self.stats.get(ConfLevel.ERROR, 0) + 1

    # ---------------- 辅助 ----------------
    def _clip_present(self, type_ids) -> bool:
        # 只有当 CLIP Type ID 已 VERIFIED 才返回 True
        return any(RESOURCE_TYPES.is_clip(t) for t in type_ids)

    def _stbl_present(self, type_ids) -> bool:
        return any(RESOURCE_TYPES.is_stbl(t) for t in type_ids)

    def _xml_signal(self, type_ids) -> bool:
        # 候选信号: snippet/tuning_xml (已核实) 任一存在
        return any(RESOURCE_TYPES.is_snippet(t) or RESOURCE_TYPES.is_tuning_xml(t)
                   for t in type_ids)

    def _read_candidate_xmls(self, backend, entries, max_xml=512 * 1024) -> List[str]:
        texts = []
        for e in entries:
            # 只读已验证的 XML-ish 类型的小资源
            if RESOURCE_TYPES.is_snippet(e.type_id) or RESOURCE_TYPES.is_tuning_xml(e.type_id):
                data = backend.read_small_resource(e, max_bytes=max_xml)
                if data:
                    try:
                        texts.append(data.decode("utf-8", errors="ignore"))
                    except Exception:
                        pass
        return texts

    def _count_class(self, level: str):
        self.stats[level] = self.stats.get(level, 0) + 1

    def _mark_error(self, p, reason):
        self.stats[ConfLevel.ERROR] = self.stats.get(ConfLevel.ERROR, 0) + 1
        self._record_error(p, reason)

    def _record_error(self, p, reason):
        self.errors.append(f"{p} | {reason}")
        self._scan_row(self._base_row(p, ConfLevel.ERROR, reason, "ERROR"))

    def _cache_failed(self, p, st, err):
        level = "ERROR_UNSUPPORTED_DBPF" if err == "ERROR_UNSUPPORTED_DBPF" else "ERROR"
        self.cache.set_status(str(p), st.st_size, st.st_mtime,
                              level=level, scan_status="unavailable",
                              reason=err or "ERROR")

    def _base_row(self, p, level, reason, status="ok"):
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        return {
            "package_path": str(p),
            "package_size": size,
            "package_type": "",
            "confidence_level": level,
            "classification_reason": reason,
            "clip_present": 0,
            "ww_xml_present": 0,
            "pose_snippet_present": 0,
            "stbl_present": 0,
            "visible_text_count": 0,
            "english_text_count": 0,
            "chinese_text_count": 0,
            "uncertain_text_count": 0,
            "scan_status": status,
        }

    def _scan_row(self, row):
        self.scan_rows.append(row)

    def _stage1_row(self, p, st, type_ids, is_candidate):
        row = self._base_row(p, "PENDING", "Stage1 候选" if is_candidate else "Stage1 非候选")
        row["clip_present"] = 1 if self._clip_present(type_ids) else 0
        row["stbl_present"] = 1 if self._stbl_present(type_ids) else 0
        if not is_candidate:
            row["confidence_level"] = ConfLevel.NON_ANIMATION
            row["scan_status"] = "filtered_stage1"
        self._scan_row(row)

    def _reuse_cached(self, p, cached):
        row = self._base_row(p, cached.get("level") or ConfLevel.NON_ANIMATION,
                             cached.get("reason") or "缓存复用", cached.get("scan_status") or "cached")
        row.update({
            "clip_present": cached.get("has_clip") or 0,
            "ww_xml_present": cached.get("has_ww_xml") or 0,
            "stbl_present": cached.get("has_stbl") or 0,
            "visible_text_count": cached.get("visible_text_count") or 0,
            "english_text_count": cached.get("english_count") or 0,
            "chinese_text_count": cached.get("chinese_count") or 0,
            "uncertain_text_count": cached.get("uncertain_count") or 0,
        })
        self._scan_row(row)
        if cached.get("level"):
            self._count_class(cached["level"])

    def _cache_classified(self, p, st, cls, type_ids, has_clip):
        self.cache.set_status(str(p), st.st_size, st.st_mtime,
                              level=cls.level, reason=cls.reason,
                              evidence=";".join(cls.evidence),
                              has_clip=1 if has_clip else 0,
                              has_ww_xml=1 if cls.level == ConfLevel.CONFIRMED_WW else 0,
                              has_pose_snippet=1 if cls.level == ConfLevel.CONFIRMED_POSE else 0,
                              has_stbl=1 if self._stbl_present(type_ids) else 0,
                              scan_status="ok")

    def _cache_classified_full(self, p, st, cls, type_ids, has_clip, stbl_present,
                               eng, chn, unc, visible):
        self.cache.set_status(str(p), st.st_size, st.st_mtime,
                              level=cls.level, reason=cls.reason,
                              evidence=";".join(cls.evidence),
                              has_clip=1 if has_clip else 0,
                              has_ww_xml=1 if cls.level == ConfLevel.CONFIRMED_WW else 0,
                              has_pose_snippet=1 if cls.level == ConfLevel.CONFIRMED_POSE else 0,
                              has_stbl=1 if stbl_present else 0,
                              visible_text_count=len(visible),
                              english_count=eng, chinese_count=chn, uncertain_count=unc,
                              scan_status="ok")

    def _stage2_row(self, p, cls, eng, chn, unc):
        # 更新 PENDING 行为最终分类
        for row in self.scan_rows:
            if row["package_path"] == str(p) and row["confidence_level"] == "PENDING":
                row["confidence_level"] = cls.level
                row["classification_reason"] = cls.reason
                row["scan_status"] = "classified"
                row["english_text_count"] = eng
                row["chinese_text_count"] = chn
                row["uncertain_text_count"] = unc
                row["visible_text_count"] = eng + chn + unc
                row["ww_xml_present"] = 1 if cls.level == ConfLevel.CONFIRMED_WW else 0
                row["pose_snippet_present"] = 1 if cls.level == ConfLevel.CONFIRMED_POSE else 0
                break

    # ---------------- 报告 ----------------
    def _write_reports(self):
        path = self.output_dir / "scan_report.csv"
        fieldnames = [
            "package_path", "package_size", "package_type", "confidence_level",
            "classification_reason", "clip_present", "ww_xml_present",
            "pose_snippet_present", "stbl_present", "visible_text_count",
            "english_text_count", "chinese_text_count", "uncertain_text_count",
            "scan_status",
        ]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for row in self.scan_rows:
                w.writerow({k: row.get(k, "") for k in fieldnames})

    def _write_summary(self):
        s = self.stats
        path = self.output_dir / "summary.txt"
        lines = [
            "========== Sims 4 Animation Translator ==========",
            "",
            f"扫描 package: {s['packages_total']}",
            f"总大小: {s['disk_size_gb']} GB",
            "",
            f"{ConfLevel.CONFIRMED_WW}: {s.get(ConfLevel.CONFIRMED_WW, 0)}",
            f"{ConfLevel.CONFIRMED_POSE}: {s.get(ConfLevel.CONFIRMED_POSE, 0)}",
            f"{ConfLevel.UNCERTAIN}: {s.get(ConfLevel.UNCERTAIN, 0)}",
            f"{ConfLevel.NON_ANIMATION}: {s.get(ConfLevel.NON_ANIMATION, 0)}",
            f"{ConfLevel.ERROR}: {s.get(ConfLevel.ERROR, 0)}",
            f"{ConfLevel.ERROR_UNSUPPORTED_DBPF}: {s.get(ConfLevel.ERROR_UNSUPPORTED_DBPF, 0)}",
            "",
            "翻译层 (Phase 1 仅扫描, 不翻译):",
            f"发现玩家可见文本: {s.get('visible_text', 0)}",
            f"英文: {s.get('english', 0)}",
            f"原本中文: {s.get('chinese', 0)}",
            f"无法确定: {s.get('uncertain_text', 0)}",
            "",
            "写入层:",
            "成功修改 package: 0 (Phase 1 只读)",
            "误修改非动作 MOD: 0 (必须为 0)",
            "",
            "✅ Phase 1 = Dry Run, 修改文件数 = 0",
        ]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        # 更详细: 分类依据抽样 (写入 evidence 报告)
        ev_path = self.output_dir / "classification_evidence.txt"
        ev_lines = []
        ev_lines.append(f"Resource Type ID 核实状态: {verified_type_count()} 个已核实")
        ev_lines.append(list_verified_ids())
        ev_lines.append("")
        for row in self.scan_rows:
            if row["confidence_level"] in (ConfLevel.CONFIRMED_WW, ConfLevel.CONFIRMED_POSE, ConfLevel.UNCERTAIN):
                ev_lines.append(f"[{row['confidence_level']}] {row['package_path']}")
                ev_lines.append(f"  依据: {row.get('classification_reason','')}")
                ev_lines.append(f"  CLIP: {row.get('clip_present')} WWXML: {row.get('ww_xml_present')} Pose: {row.get('pose_snippet_present')} STBL: {row.get('stbl_present')}")
                ev_lines.append("")
        with open(ev_path, "w", encoding="utf-8") as f:
            f.write("\n".join(ev_lines) + "\n")

    def _write_performance(self):
        path = self.output_dir / "performance_report.txt"
        p = self.performance
        lines = [
            "===== Performance =====",
            "",
            f"Packages: {self.stats['packages_total']}",
            f"Disk size: {self.stats['disk_size_gb']} GB",
            f"收集文件: {p.get('collect', 0):.1f}s",
            f"Stage 1 (浅扫描): {p.get('stage1', 0):.1f}s",
            f"Stage 2 (深扫描): {p.get('stage2', 0):.1f}s",
            f"Total: {p.get('total', 0):.1f}s",
            "",
            "使用说明: Stage1 只读 DBPF index, 不整读 package;",
            "本地缓存 mod_index_cache.db 将加速后续增量扫描。",
        ]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def _write_errors(self):
        path = self.output_dir / "errors.log"
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"错误总数: {len(self.errors)}\n")
            for e in self.errors:
                f.write(e + "\n")
