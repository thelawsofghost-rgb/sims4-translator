#!/usr/bin/env python3
"""
诊断: 深度 dump 指定 type 的所有实例, 尝试 zlib 解压 + 多编码解码, 找出真正的 WW 动画 XML。

用法:
    python scripts\dump_xml.py <package路径> <type_id_hex> [type_id_hex ...]

重点排查:
    - 0x7DF2169C  (WW_ANIM_XML?  疑似二进制, 但在 WW_0nizu 里有 2 个)
    - 0x00B2D882  (BINARY_XML,  疑似明文 XML)
目标: 确认哪一种是含 animation_raw_display_name / animation_clip_name 的真 WW XML。
"""
import sys, zlib, struct
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse
from backend import get_backend


def decode_candidates(data: bytes):
    """返回候选文本列表: zlib 解压后 / 原样, 各尝试 utf-8, utf-16-le"""
    candidates = [("raw", data)]
    if data[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        try:
            candidates.append(("zlib", zlib.decompress(data)))
        except Exception:
            pass
    results = []
    for tag, d in candidates:
        for enc in ("utf-8", "utf-16-le"):
            try:
                txt = d.decode(enc, errors="ignore")
            except Exception:
                continue
            printable = "".join(ch if ch.isprintable() or ch in "\n\t" else "·" for ch in txt)
            if printable.strip("·\n\t ") and any(c.isalnum() for c in printable):
                results.append((f"{tag}/{enc}", printable))
    return results


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    p = sys.argv[1]
    wants = {int(a, 0) for a in sys.argv[2:]}
    idx, err = safe_parse(p)
    if err:
        print(f"解析失败: {err}")
        sys.exit(1)
    backend = get_backend("readonly").open(p)
    for e in idx.entries:
        if e.type_id not in wants:
            continue
        data = backend.read_small_resource(e, max_bytes=512 * 1024)
        if not data:
            print(f"\n=== type=0x{e.type_id:08X} inst=0x{e.instance_id:016X} size={e.size} comp={e.is_compressed} READ_FAIL ===")
            continue
        print(f"\n===== type=0x{e.type_id:08X} inst=0x{e.instance_id:016X} size={e.size} comp={e.is_compressed} =====")
        # 头部原始 hex (前 64 字节)
        print(f"  头64字节: {data[:64].hex()}")
        found_text = False
        for tag, txt in decode_candidates(data):
            # 压缩/隐藏过长文本
            shown = txt if len(txt) < 1500 else txt[:1500] + f" ...(共{len(txt)}字符)"
            print(f"  [{tag}] {shown}")
            found_text = True
            # 若找到 animation_raw_display_name / animation_clip_name 重点标出
            for key in ("animation_raw_display_name", "animation_display_name",
                        "animation_clip_name", "animation_actors_list",
                        "animation_category", "animation_tags", "animation_locations",
                        "animation_author"):
                if key in txt:
                    print(f"    >>> 含关键字段: {key}")
        if not found_text:
            print("  (无可读文本)")
            # 统计可打印 ASCII 占比, 判断是否二进制
            ascii_ratio = sum(1 for b in data if 32 <= b < 127) / max(1, len(data))
            print(f"  ASCII 可打印占比: {ascii_ratio:.2%}")
    backend.close()
    print("\n\n=== dump_xml 完成 ===")


if __name__ == "__main__":
    main()
