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
        # 读候选 XML (模拟 Stage2)
        xml_texts = []
        for e in entries:
            if RESOURCE_TYPES.is_snippet(e.type_id) or RESOURCE_TYPES.is_tuning_xml(e.type_id):
                data = backend.read_small_resource(e)
                if data:
                    xml_texts.append(data.decode("utf-8", errors="ignore"))
        stbl_present = any(RESOURCE_TYPES.is_stbl(e.type_id) for e in entries)
        has_clip = any(RESOURCE_TYPES.is_clip(e.type_id) for e in entries)

        cls = Classifier().classify_from_texts(
            type_ids=type_ids, xml_texts=xml_texts, stbl_present=stbl_present)

        print(f"  分类: {cls.level}")
        print(f"    证据: {cls.evidence}")
        if cls.missing:
            print(f"    缺:   {cls.missing}")
        print(f"    理由: {cls.reason}")

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
