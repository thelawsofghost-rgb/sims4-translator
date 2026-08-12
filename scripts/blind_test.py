#!/usr/bin/env python3
"""
盲测工具: 对多个真实 .package 运行完整分类管线, 统计分类与 False Positive。

用法:
    python scripts\blind_test.py "路径\包1.package" "路径\包2.package" ...

说明:
  - 使用真实 safe_parse / backend / Classifier.classify_from_texts
  - 报告每个包的分类等级 + evidence + missing + reason + 资源类型清单
  - 重点: 是否有 CONFIRMED_WW / CONFIRMED_POSE 误报 (False Positive)
"""
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dbpf_fast import safe_parse
from backend import get_backend
from classifier import Classifier, ConfLevel
from resource_types import RESOURCE_TYPES
from text_extractor import extract_ww_display_texts, extract_stbl_strings
from classifier import POSE_SIGNATURES as _POSE_SIG


def analyze(pkg: str):
    p = Path(pkg)
    print("=" * 72)
    print(f"文件: {p.name}")
    if not p.exists():
        print("  !! 不存在")
        return
    idx, err = safe_parse(p)
    if err or idx is None:
        print(f"  解析失败: {err}")
        return
    type_ids = {e.type_id for e in idx.entries}
    cnt = Counter(e.type_id for e in idx.entries)
    print(f"  entry={len(idx.entries)} 大小={p.stat().st_size} 字节")
    print(f"  类型清单: " + ", ".join(
        f"0x{t:08X}({RESOURCE_TYPES.name_for(t)})x{n}" for t, n in cnt.most_common()))

    try:
        backend = get_backend("readonly").open(p)
        idx2 = backend.read_index()
        entries = idx2.entries
        # 读候选 XML (模拟 Stage2, 含 WW_ANIM_XML zlib 解压)
        import zlib as _z
        xml_texts = []
        for e in entries:
            if (RESOURCE_TYPES.is_snippet(e.type_id)
                    or RESOURCE_TYPES.is_tuning_xml(e.type_id)
                    or RESOURCE_TYPES.is_known_safely(e.type_id, "WW_ANIM_XML")):
                data = backend.read_small_resource(e)
                if not data:
                    continue
                if data[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
                    try:
                        data = _z.decompress(data)
                    except Exception:
                        pass
                try:
                    xml_texts.append(data.decode("utf-8", errors="ignore"))
                except Exception:
                    pass
        stbl_present = any(RESOURCE_TYPES.is_stbl(e.type_id) for e in entries)
        has_clip = any(RESOURCE_TYPES.is_clip(e.type_id) for e in entries)

        # 调试打印: 候选 XML 原文 (截断), 用于人工确认 Pose/WW 专属结构
        if "--xml" in sys.argv:
            for i, txt in enumerate(xml_texts):
                head = txt[:600].replace("\n", " ")
                print(f"  [XML {i}] {head}")
        clip_names = set()
        for e in entries:
            if RESOURCE_TYPES.is_clip(e.type_id):
                cd = backend.read_small_resource(e)
                if cd:
                    import re as _re
                    for enc in ("utf-8", "utf-16-le", "latin-1"):
                        try:
                            t = cd.decode(enc, errors="ignore")
                        except Exception:
                            continue
                        for m in _re.finditer(r"[A-Za-z][A-Za-z0-9_\-:\s]{2,80}", t):
                            cand = m.group().strip()
                            if cand and _re.search(r"[A-Za-z]{3}", cand) \
                               and sum(1 for ch in cand if ch.isalnum() or ch in "_:- ") / len(cand) > 0.7:
                                clip_names.add(cand)

        cls = Classifier().classify_from_texts(
            type_ids=type_ids, xml_texts=xml_texts, stbl_present=stbl_present,
            clip_names=clip_names)

        print(f"  分类: {cls.level}")
        print(f"    证据: {cls.evidence}")
        if cls.missing:
            print(f"    缺:   {cls.missing}")
        print(f"    理由: {cls.reason}")

        # Pose 命中诊断: 若被判 Pose, 打印命中的签名 + 上下文, 排查误判根源
        if cls.level == "CONFIRMED_POSE" or ("--xml" in sys.argv and any(
                s in txt for txt in xml_texts for s in _POSE_SIG)):
            hit_sigs = sorted({s for txt in xml_texts for s in _POSE_SIG if s in txt})
            if hit_sigs:
                print(f"    POSE 命中签名: {hit_sigs}")
                if "--xml" in sys.argv:
                    import re as _r
                    for txt in xml_texts:
                        for s in hit_sigs:
                            for m in _r.finditer(_r.escape(s), txt):
                                st = max(0, m.start() - 60)
                                ctx = txt[st:m.end() + 60].replace("\n", " ")
                                print(f"      [{s}] ...{ctx}...")
                                break

        # 提取实际可见文本 (WW XML 显示名)
        visible = []
        for txt in xml_texts:
            visible += extract_ww_display_texts(txt)
        if visible:
            print(f"  WW 显示名文本: {visible}")

        # STBL 文本 (仅非候选时也展示, 供人工判断翻译价值)
        stbl_strings = []
        for e in entries:
            if RESOURCE_TYPES.is_stbl(e.type_id):
                data = backend.read_small_resource(e)
                if data:
                    stbl_strings += extract_stbl_strings(data)
        if stbl_strings:
            uniq = list(dict.fromkeys(t for _, t in stbl_strings))
            print(f"  STBL 提取文本 ({len(uniq)} 条): {uniq}")
        backend.close()
    except Exception as e:
        print(f"  深扫描异常: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for a in sys.argv[1:]:
        analyze(a)
    print("=" * 72)
    print("盲测完成")
