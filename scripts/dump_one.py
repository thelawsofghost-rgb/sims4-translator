#!/usr/bin/env python3
"""诊断: dump 指定 package 中 0x7DF2169C (WW_ANIM_XML) 的真实内容 + 检查是否含 Pose 标签。

用法:
    python scripts\\dump_one.py "<完整路径>"

robust 版: 路径直接作为单个参数, 不玩多参数/变量展开; 打印解压后完整文本 + 关键字段命中。
"""
import sys, zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse
from backend import get_backend

WANT = 0x7DF2169C
POSE_TAGS = ["PosePackInstance", "poseplayer", "POSE_PACK", "pose_list",
             "pose_display_name", "pose_name"]
WW_KEYS = ["animation_raw_display_name", "animation_display_name", "animation_clip_name",
           "animation_actors_list", "animation_category", "animation_tags",
           "animation_locations", "animation_author", "raw_display_name",
           "dancer_animation_clip_name", "StripperDance"]


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts\\dump_one.py \"<package路径>\"")
        sys.exit(1)
    p = Path(sys.argv[1])
    print("路径:", p)
    print("存在?", p.exists(), "| 大小?", p.stat().st_size if p.exists() else "-")
    idx, err = safe_parse(str(p))
    if err or idx is None:
        print("safe_parse 失败:", err)
        return
    print("entry 总数:", len(idx.entries))
    backend = get_backend("readonly").open(str(p))
    found = 0
    for e in idx.entries:
        if e.type_id != WANT:
            continue
        found += 1
        data = backend.read_small_resource(e, max_bytes=2 * 1024 * 1024)
        print(f"\n===== WW_ANIM_XML inst=0x{e.instance_id:016X} size={e.size} comp={e.is_compressed} =====")
        if not data:
            print("  READ_FAIL")
            continue
        print(f"  头16字节 hex: {data[:16].hex()}")
        # zlib 解压
        raw = data
        is_zlib = data[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01")
        if is_zlib:
            try:
                raw = zlib.decompress(data)
            except Exception as ex:
                print(f"  zlib 解压失败: {ex}")
                raw = data
        for enc in ("utf-8", "utf-16-le", "latin-1"):
            try:
                txt = raw.decode(enc, errors="ignore")
            except Exception:
                continue
            printable = "".join(ch if (ch.isprintable() or ch in "\n\t") else "·" for ch in txt)
            if not any(c.isalnum() for c in printable):
                continue
            print(f"  [{enc}] 长度={len(txt)}")
            shown = printable if len(printable) < 2000 else printable[:2000] + f" ...(共{len(printable)})"
            print("  " + shown)
            # 命中检查
            ww_hits = [k for k in WW_KEYS if k in txt]
            pose_hits = [k for k in POSE_TAGS if k in txt]
            print(f"  >>> WW 关键字段: {ww_hits}")
            print(f"  >>> POSE 标签命中: {pose_hits}")
            break
    if found == 0:
        print("  (该包无 0x7DF2169C 资源)")
    backend.close()


if __name__ == "__main__":
    main()
