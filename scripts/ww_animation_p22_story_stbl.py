#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P22 Story animation STBL key 定位 (只读, 不改 package)

pipeline:
  1. 从 WW_ANIM_XML 提取 ordinal 299-306 的 animation_id (整数值字段, 可 hex/dec)
  2. 对每个 id 计算 key string = "story_animations." + str(animation_id)
     再算 Sims4 hash key = FNV-1 32-bit(key string)
  3. 扫描所有可访问 STBL (0x220557DA):
       - 源 package 自身
       - --dir 下所有 .package (Mods)
     (游戏资源目录如可用, 通过 --game-dir 额外传入; 未给则跳过)
  4. 对每个 ordinal 输出: ordinal / animation_id / key string / fnv32 key / STBL原文本
  5. 判定落点: 若 hash key 命中原文本 Caught Cheating N -> 找到 STBL 落点,
     下一步做 STBL override package, 不再改 WW_ANIM_XML

Sims4 本地化 key 用 FNV-1 32-bit (offset 0x811C9DC5, prime 0x01000193)。
display_name (P21) = get_localized_string_id(hash("story_animations."+str(id)))
  -> hash(...) 即 FNV-32, 产出 32-bit STBL key。

fail-closed: 源缺->2; 无 WW_ANIM_XML->3; 目录缺->4;
  STBL 提取依赖 (src/dbpf_fast, text_extractor) 缺失->5; 正常 0。
  仅读; 不写 Mods; ZERO_WRITE_TO_MODS=YES。

用法 (Windows, 只读):
  python scripts\ww_animation_p22_story_stbl.py `
      "<WW.package>" --dir "C:\\...\\Mods" [--game-dir "D:\\...\\Data" ] [--out-dir output/ww_p22] [--ordinals 299-306]
"""
import argparse
import csv
import sys
from importlib import import_module
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
try:
    from dbpf_fast import safe_parse
    from backend import get_backend
    from resource_types import RESOURCE_TYPES
except Exception as ex:
    print(f"ERROR: 依赖加载失败: {ex} (需在 sims4-translator 内运行)", file=sys.stderr)
    sys.exit(5)
import struct as _struct
import zlib as _zlib

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import ww_animation_p7_story_chain_audit as P7   # load_xml / ordinal_blocks
import ww_animation_canary_builder as wb        # read_body_raw / parse_anim_xml

WW_ANIM_XML = 0x7DF2169C
STBL = 0x220557DA
ANIM_ID_FIELD = "animation_id"
STORY_PREFIX = "story_animations."
OUT_DIR = Path("output/ww_p22")


# --- FNV-1 32-bit (Sims 4 string hash) ---
def fnv32(s: str) -> int:
    h = 0x811C9DC5
    p = 0x01000193
    for b in s.encode("utf-8"):
        h ^= b
        h = (h * p) & 0xFFFFFFFF
    return h


def parse_ordinals(spec: str):
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def parse_anim_id(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(raw, 16) if raw.lower().startswith("0x") else int(raw, 10)
    except ValueError:
        return None


def is_local(local):
    return local is None or local in ("T", "E", "I")


def extract_entry_anim_id(el):
    """el = ET entry; 返回 (animation_id node tag, raw, int值) 或 (None,...)."""
    for child in el.iter():
        n = child.get("n")
        if (n or "") == ANIM_ID_FIELD:
            tag = child.tag.rsplit("}", 1)[-1] if isinstance(child.tag, str) else child.tag
            raw = (child.text or "").strip()
            return tag, raw, parse_anim_id(raw)
    return None, None, None


def parse_stbl(blob: bytes):
    """真 STBL 解析: 返回 {keyHash: text}. 支持 v5 (UTF-8) 与 v4 (UTF-16LE);
    兼容整体 zlib 压缩。布局参照 s4pi StblResource.cs:
      magic 'STBL' | version u16 | isCompressed u8 | numEntries u64 |
      reserved 2 | stringLength u32 | 每项: keyHash u32 | flags u8 | len u16 | bytes."""
    data = blob
    if data[:2] == b"\x78\x9c" or data[:2] == b"\x78\xda" or data[:2] == b"\x78\x01":
        try:
            data = _zlib.decompress(data)
        except Exception:
            pass
    if data[:4] != b"STBL":
        # 容忍前置垃圾: 找 magic
        i = data.find(b"STBL")
        if i < 0:
            return {}
        data = data[i:]
    ver = _struct.unpack_from("<H", data, 4)[0]
    if ver == 5:
        _comp = data[6]
        n = _struct.unpack_from("<Q", data, 7)[0]
        pos = 7 + 8 + 2 + 4  # comp + n + reserved + stringLength
        out = {}
        for _ in range(n):
            if pos + 8 > len(data):
                break
            key = _struct.unpack_from("<I", data, pos)[0]
            flags = data[pos + 4]
            ln = _struct.unpack_from("<H", data, pos + 5)[0]
            tb = data[pos + 7: pos + 7 + ln]
            try:
                txt = tb.decode("utf-8").rstrip("\x00")
            except Exception:
                txt = tb.decode("utf-16-le", errors="ignore").rstrip("\x00")
            out[key] = txt
            pos += 7 + ln
        return out
    if ver == 4:
        n = _struct.unpack_from("<I", data, 6)[0]
        pos = 10
        out = {}
        for _ in range(n):
            if pos + 6 > len(data):
                break
            key = _struct.unpack_from("<I", data, pos)[0]
            flags = data[pos + 4]
            ln = _struct.unpack_from("<H", data, pos + 5)[0]
            tb = data[pos + 7: pos + 7 + ln]
            try:
                txt = tb.decode("utf-16-le").rstrip("\x00")
            except Exception:
                txt = tb.decode("utf-8", errors="ignore").rstrip("\x00")
            out[key] = txt
            pos += 7 + ln
        return out
    return {}


def scan_stbl_packages(paths):
    """扫描一组 package 文件, 返回 {fnv32_key: [(instance_id, text, pkg)]} 与 {text: count}."""
    by_key = {}
    by_text = {}
    total_stbl = 0
    for p in paths:
        try:
            idx, err = safe_parse(str(p))
        except Exception:
            continue
        if err is not None or idx is None:
            continue
        try:
            backend = get_backend("readonly").open(str(p))
        except Exception:
            continue
        for e in idx.entries:
            if e.type_id != STBL:
                continue
            total_stbl += 1
            try:
                data = backend.read_small_resource(e)
            except Exception:
                continue
            if not data:
                continue
            try:
                stbl = parse_stbl(data)
            except Exception:
                continue
            for k, txt in stbl.items():
                if not txt:
                    continue
                by_key.setdefault(k, []).append((e.instance_id, txt, p.name))
                by_text[txt] = by_text.get(txt, 0) + 1
    return by_key, by_text, total_stbl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--dir", required=True, help="Mods 目录 (递归扫 .package)")
    ap.add_argument("--game-dir", default=None, help="游戏数据目录 (可选, 递归扫 .package)")
    ap.add_argument("--ordinals", default="299-306", help="ordinal 区间, 默认 299-306")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: 源不存在 {src}", file=sys.stderr); return 2
    d = Path(a.dir)
    if not d.is_dir():
        print(f"ERROR: --dir 不存在 {d}", file=sys.stderr); return 4
    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    ordinals = parse_ordinals(a.ordinals)

    ww_first, werr = P7.load_xml(src)
    if ww_first is None:
        print(f"ERROR: {werr}", file=sys.stderr); return 3
    blocks, berr = P7.ordinal_blocks(src, ww_first, ordinals)
    if berr is not None:
        print(f"ERROR: {berr}", file=sys.stderr); return 3

    L = []
    L.append("=== P22 Story animation STBL key 定位 (只读) ===")
    L.append(f"源 = {src.name}")
    L.append(f"Mods 目录 = {d}")
    L.append(f"游戏目录 = {a.game_dir or '(未提供, 跳过)'}")
    L.append(f"ordinals = {ordinals}")
    L.append("")

    # 1) 提取 animation_id + 计算 key
    L.append("=== 1) animation_id + key string + FNV-32 hash ===")
    rows = []
    for o in ordinals:
        el = blocks[o]
        tag, raw, aid = extract_entry_anim_id(el)
        if aid is None:
            L.append(f"  [{o}] !! 无 animation_id 节点 (tag={tag}, raw={raw!r})")
            rows.append({"ordinal": o, "animation_id": "", "key_str": "", "fnv32": "",
                         "tag": tag or "", "raw": raw or ""})
            continue
        key_str = STORY_PREFIX + str(aid)
        h = fnv32(key_str)
        L.append(f"  [{o}] animation_id={aid} (tag={tag}, raw={raw!r})")
        L.append(f"        key = {key_str!r}")
        L.append(f"        fnv32 = 0x{h:08X}  (STBL key)")
        rows.append({"ordinal": o, "animation_id": aid, "key_str": key_str,
                     "fnv32": f"0x{h:08X}", "tag": tag or "", "raw": raw or ""})
    L.append("")

    # 2) 扫描 STBL
    L.append("=== 2) 扫描 STBL 资源 ===")
    pkg_paths = [src]
    scan_dirs = [d]
    if a.game_dir:
        scan_dirs.append(Path(a.game_dir))
    for sd in scan_dirs:
        if sd.is_dir():
            pkg_paths.extend(sorted(p for p in sd.rglob("*.package") if p.is_file()))
    if not pkg_paths:
        pkg_paths = [src]
    by_key, by_text, total_stbl = scan_stbl_packages(pkg_paths)
    L.append(f"  扫描 package 数 = {len(pkg_paths)}   STBL 资源总数 = {total_stbl}")
    L.append(f"  唯一 key 数 = {len(by_key)}   样例文本数 = {len(by_text)}")
    L.append("")

    # 3) 匹配 + 落点判定
    L.append("=== 3) STBL key -> 原文本 落点匹配 ===")
    hits = 0
    for r in rows:
        o = r["ordinal"]
        if not r["fnv32"]:
            L.append(f"  [{o}] (无 animation_id, 跳过)")
            continue
        hk = int(r["fnv32"], 16)
        found = by_key.get(hk)
        if found:
            texts = sorted(set(t for _, t, _ in found))
            L.append(f"  [{o}] key 0x{hk:08X} 命中 {len(found)} 处:")
            for iid, txt, pn in sorted(set((i, t, p) for i, t, p in found)):
                L.append(f"         STBL inst=0x{iid:016X}  [{pn}]  text={txt!r}")
            hits += 1
            r["hit"] = "YES"; r["text"] = "; ".join(sorted(set(t for _, t, _ in found)))
        else:
            L.append(f"  [{o}] key 0x{hk:08X} 未命中任何 STBL")
            r["hit"] = "NO"; r["text"] = ""
    L.append("")

    # 4) 反向: 按显示文本找 STBL (Caught Cheating N 出现在哪个 STBL / key)
    L.append("=== 4) 反向文本匹配 (原显示文本 -> STBL key) ===")
    probe = [f"Caught Cheating {r['ordinal']-298}" for r in rows if r["ordinal"] <= 306]
    key_by_text = {}
    for sh, lst in by_key.items():
        for _, txt, pn in lst:
            key_by_text.setdefault(txt, set()).add(sh)
    for p in probe:
        shs = key_by_text.get(p)
        if shs:
            L.append(f"  文本 {p!r} -> STBL key(s): {[f'0x{s:08X}' for s in sorted(shs)]}")
        else:
            L.append(f"  文本 {p!r} -> 未在 STBL 中发现 (可能文本不同或 STBL 缺失)")
    L.append("")

    # 5) 结论
    L.append("=== 5) 结论 ===")
    if hits == len([r for r in rows if r["fnv32"]]):
        L.append("  => 所有 Story ordinal 的 hash key 均命中 STBL -> 显示来源 = STBL, 落点已定")
        L.append("  => 下一步: 制作 STBL override package (不改 WW_ANIM_XML)")
    elif hits > 0:
        L.append(f"  => 部分命中 ({hits}/{len(rows)}). 见 3). 未命中者可能用非前缀 key 或文本在游戏资源内")
    else:
        L.append("  => 无 STBL 命中. 可能: 前缀 'story_animations.' 非真实 / hash 非 FNV-32 / STBL 在游戏资源内未扫")
        L.append("     -> 需 --game-dir 或真机字节码里 hash 真相 (P21 输出)")

    L.append("")
    L.append("ZERO_WRITE_TO_MODS=YES (只读, 不修改任何 package)")
    txt = "\n".join(L)
    (out_dir / "p22_story_stbl.txt").write_text(txt, encoding="utf-8")
    with open(out_dir / "p22_story_stbl.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ordinal", "animation_id", "key_str", "fnv32", "tag", "raw", "hit", "text"])
        for r in rows:
            w.writerow([r["ordinal"], r["animation_id"], r["key_str"], r["fnv32"],
                        r["tag"], r["raw"], r.get("hit", ""), r.get("text", "")])
    print(txt)
    print(f"OUT_TXT={out_dir/'p22_story_stbl.txt'}")
    print(f"OUT_CSV={out_dir/'p22_story_stbl.csv'}")
    print("P22_STORY_STBL=OK (只读)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
