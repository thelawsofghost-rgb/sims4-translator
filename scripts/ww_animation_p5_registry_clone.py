#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P5 取证实验 — clone 外部 Story registry 条目 (只读源, 只写 sidecar 包)。

背景: M2-M5 在 WW_ANIM_XML 内部改 4 字段都让 Caught Cheating 显示
"NOT Caught Cheating" (runtime 不认). P4 假设 Story animation 由【外部
资源】(registry/mapping/tuning/index) 注册; 新 XML instance 不在该注册表,
故运行时判定为无效 Story。

P5 实验: 不仅改 WW_ANIM_XML, 还【克隆】外部注册资源里引用该 Story 动画
  的条目, 把注册目标从旧 WW_ANIM_XML instance 指向【新】instance (+ 新
  clip/display), 生成 sidecar 包让 runtime 认新动画。

两种注入策略 (--strategy):
  clone     : 复制 registry 里引用 299 的每个条目块, 重写 instance TGI /
              clip / display 后插入到原条目之后 (纯增量, 保留原条目).
              XML 形态 registry 用结构化块克隆; binary/STBL 退化为
              字节 find+replace. (缺省)
  swap-ref  : 将 registry 正文中旧 WW_ANIM_XML instance hex 直接替换为
              新 instance hex (原地, 有损但最简单).

fail-closed:
  - 找不到 registry 资源 (无引用 299 的非 WW 资源)       -> exit 4
  - WW XML 中找不到 ordinal 299                          -> exit 3
  - registry 里找不到引用 299 / 旧 TGI 的条目块           -> exit 5
  - 新 instance == 旧 instance                           -> exit 6
  - 目标 ordinal 缺中文翻译 (--display 未给且无 translations) -> exit 7

只读源, 只写 out-dir。不改 Mods (ZERO_WRITE_TO_MODS=YES)。
用法 (Windows):
  python scripts/ww_animation_p5_registry_clone.py "<SRC.package>" \
      --ordinal 299 --display "抓奸 1" \
      --out-dir output/ww_p5
  可选: --strategy clone|swap-ref --registry-tgi 0x... (显式指定注册资源;
        缺省自动找引用该 ordinal 的非 WW 资源中 type 最像 registry 者)
"""
import argparse
import csv
import hashlib
import importlib.util
import re
import sys
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
_spec = importlib.util.spec_from_file_location(
    "ww_canary_builder", SCRIPT_DIR / "ww_animation_canary_builder.py")
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)
import ww_animation_m2_batch as _m2
import ww_animation_m2_diff_forensic as _diff
import ww_animation_p1_resource_forensic as _p1

WW_ANIM_XML = 0x7DF2169C
OUT_DIR = Path("output/ww_p5")
DISPLAY_FIELD = "animation_raw_display_name"

# registry 类型倾向 (越小越像注册入口)
REG_PREF = [0x545AC2C2, 0x034AEECB, 0x073FAA27, 0x00B2D882, 0x220557DA, 0xBC4A5044]


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else None


def _ord_block(ord_blocks, o):
    return ord_blocks.get(o)


def _collect_ordinals(src, ww_first):
    body = wb.read_body_raw(src, ww_first)
    _s, xml_text, xerr = wb.parse_anim_xml(body)
    if xerr is not None:
        return None, xerr
    inner, _st, _en = _diff._locate_animations_list(xml_text)
    om = {}
    ei = 0
    for b, _r in _diff._entry_blocks(inner):
        if b.lstrip().startswith("<U"):
            om[ei] = b
            ei += 1
    return om, None


def _all_TI_leafs(block):
    """返回 block 里全部 T/E/I 叶 (n, tag, text)。"""
    out = []
    try:
        root = ET.fromstring(block)
    except Exception:
        return out
    for el in root.iter():
        lt = _local(el.tag)
        if lt in ("T", "E", "I"):
            out.append((el.get("n") or "", el.text or ""))
    return out


def find_ref_blocks(body_text, probe_strings):
    """在 registry 解码正文里找含任一 probe 的条目块。
    返回: (所有匹配的 XML <U>/<U n=> 块文本列表, 匹配到的 probe)。
    XML 形态逐 <U> 块判断; 非 XML 退化为整段命中。"""
    kind, text = _p1.decode_body(body_text)
    if kind == "xml":
        hits = []
        used = set()
        try:
            root = ET.fromstring(text)
        except Exception:
            root = None
        if root is not None:
            # 找顶层/任意 <U n=...> 块, 判断其下是否含 probe
            for u in root.iter():
                if _local(u.tag) != "U":
                    continue
                if u.get("n") and any(p in (ET.tostring(u, encoding="unicode").lower())
                                       for p in probe_strings):
                    hits.append((u.get("n") or "?", ET.tostring(u, encoding="unicode")))
                    used.update(p for p in probe_strings if p in ET.tostring(u, encoding="unicode").lower())
        return hits, used, kind
    lower = text.lower()
    hits = []
    used = set(p for p in probe_strings if p in lower)
    if used:
        hits.append(("?", text))
    return hits, used, kind


def rewrite_block_tgi_and_display(block_txt, old_tgi_hex, new_tgi_hex, old_disp, new_disp,
                                  old_clips, new_clips):
    """在克隆的 registry 块里替换: instance TGI 引用 + display 串 + clip 串。"""
    out = block_txt
    if old_tgi_hex:
        out = out.replace(old_tgi_hex, new_tgi_hex)
        # 也替换不带 0x 前缀 / 大小写变体
        for v in (old_tgi_hex.lower(), old_tgi_hex.upper(),
                  old_tgi_hex[2:], old_tgi_hex[2:].lower(), old_tgi_hex[2:].upper()):
            if v and v != new_tgi_hex:
                out = out.replace(v, new_tgi_hex)
    if old_disp and new_disp:
        out = out.replace(old_disp, new_disp)
    for oc, nc in zip(old_clips, new_clips):
        if oc and nc:
            out = out.replace(oc, nc)
    return out


def gen_registry_instance(src_reg_inst, ordinal, display, strategy):
    payload = f"reg|{src_reg_inst:x}|{ordinal}|{display}|{strategy}".encode("utf-8")
    h = hashlib.blake2b(payload, digest_size=6).digest()
    inst = int.from_bytes(h, "big") | 0x100000000
    return inst & 0x0000FFFFFFFFFFFF


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="源 WW package")
    ap.add_argument("--ordinal", type=int, default=299, help="目标 Story ordinal (缺省 299)")
    ap.add_argument("--display", default=None, help="中文显示名 (缺省读 --translations)")
    ap.add_argument("--translations", default=None,
                    help="翻译 CSV: ordinal,display_name,chinese_translation (ordinal 缺省列定义)")
    ap.add_argument("--strategy", choices=["clone", "swap-ref"], default="clone")
    ap.add_argument("--registry-tgi", default=None, help="显式注册资源 instance hex")
    ap.add_argument("--clip-template", default=None,
                    help="新 clip 名模板 (单 clip 用; 缺省自动 {src}_zh)")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_file():
        print(f"ERROR: source 不存在 {src}", file=sys.stderr); return 2
    out_dir = Path(a.out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 中文显示名 ----
    zh = a.display
    if zh is None:
        if not a.translations:
            print("ERROR: 需 --display 或 --translations", file=sys.stderr); return 7
        with open(a.translations, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if int(row.get("ordinal", "0")) == a.ordinal:
                    zh = row.get("chinese_translation", "").strip()
                    break
    if not zh:
        print(f"ERROR: ordinal {a.ordinal} 缺中文翻译", file=sys.stderr); return 7

    # ---- 解析源 ----
    idx, err = wb.safe_parse(src)
    if err is not None or idx is None:
        print(f"ERROR: 源解析失败 {err}", file=sys.stderr); return 3
    entries = idx.entries
    ww = [e for e in entries if e.type_id == WW_ANIM_XML]
    if not ww:
        print("ERROR: 无 WW_ANIM_XML", file=sys.stderr); return 3
    ww_first = ww[0]
    old_ww_tgi = f"0x{ww_first.instance_id:016x}"

    ord_blocks, xerr = _collect_ordinals(src, ww_first)
    if xerr is not None or ord_blocks is None:
        print(f"ERROR: WW XML 解析失败 {xerr}", file=sys.stderr); return 3
    if a.ordinal not in ord_blocks:
        print(f"ERROR: ordinal {a.ordinal} 越界 {len(ord_blocks)}", file=sys.stderr); return 3
    src_block = ord_blocks[a.ordinal]

    # 源条目专属串 (供 registry 匹配)
    src_leafs = _all_TI_leafs(src_block)
    src_disp = next((v for n, v in src_leafs if n == DISPLAY_FIELD), "")
    src_clips = [v for n, v in src_leafs if n == "animation_clip_name"]
    src_id_vals = [v for n, v in src_leafs if n in ("animation_id", "actor_id")]

    # 新 clip 名
    if a.clip_template:
        new_clips = [a.clip_template] if len(src_clips) <= 1 else \
                    [a.clip_template.format(src=c, i=i) for i, c in enumerate(src_clips)]
    else:
        new_clips = [f"{c}_zh" for c in src_clips]

    # 块匹配 probe: 只用【该 ordinal 专属】串 (clip 名/显示名/animation_id),
    # 不能用共享的 WW instance TGI (会被所有条目命中 -> 过度克隆)
    probe_strings = [p.lower() for p in ([src_disp] + src_clips + src_id_vals) if p]
    if not probe_strings:
        print("ERROR: 源条目无 clip/display/animation_id 可作 probe", file=sys.stderr); return 5

    # ---- 定位 registry 资源 ----
    reg_entry = None
    reg_key = None
    candidates = []
    for e in entries:
        if e.type_id == WW_ANIM_XML:
            continue
        raw = wb.read_body_raw(src, e)
        body = wb.decompress_maybe(raw)
        kind, text = _p1.decode_body(body)
        lower = text.lower()
        # 该资源正文是否含源条目的专属串
        matched = [p for p in probe_strings if p in lower] if (a.registry_tgi is None or
                    f"0x{e.instance_id:016x}" == a.registry_tgi) else []
        if matched:
            pref = REG_PREF.index(e.type_id) if e.type_id in REG_PREF else 99
            candidates.append((pref, e.type_id, e.group_id, e.instance_id, kind, matched, len(body)))
    if a.registry_tgi:
        T = a.registry_tgi
        exact = [c for c in candidates if f"0x{c[3]:016x}" == T]
        if exact:
            candidates = exact
        else:
            # 用户显式指定但无命中 -> 直接那个资源做 registry
            ke = next((e for e in entries if f"0x{e.instance_id:016x}" == T and e.type_id != WW_ANIM_XML), None)
            if ke is None:
                print(f"ERROR: --registry-tgi {T} 不存在", file=sys.stderr); return 4
            raw = wb.read_body_raw(src, ke)
            body = wb.decompress_maybe(raw)
            kind, text = _p1.decode_body(body)
            lower = text.lower()
            matched = [p for p in probe_strings if p in lower]
            candidates = [(REG_PREF.index(ke.type_id) if ke.type_id in REG_PREF else 99,
                           ke.type_id, ke.group_id, ke.instance_id, kind, matched, len(body))]
    if not candidates:
        print("ERROR: 无引用该 ordinal 的外部 registry 资源 (先跑 P4 确认)",
              file=sys.stderr); return 4
    candidates.sort(key=lambda c: (c[0], c[6]))
    pref, rtype, rgroup, rinst, rkind, rmatched, rsize = candidates[0]
    reg_key = (rtype, rgroup, rinst)
    reg_entry = next(e for e in entries if (e.type_id, e.group_id, e.instance_id) == reg_key)
    print(f"REGISTRY 选定: type=0x{rtype:08X} g=0x{rgroup:016X} 0x{rinst:016X} "
          f"kind={rkind} size={rsize} 命中={rmatched}")

    # ---- 读 registry 原始 body ----
    raw = wb.read_body_raw(src, reg_entry)
    reg_body = wb.decompress_maybe(raw)
    compressed = raw != reg_body
    rmeta = wb.read_entry_meta_raw(src)  # 拿 header/comp 供重建
    # 简化: 仅当具体 meta 可取得时才用, 否则默认
    meta = {}
    try:
        _mj, _mn, header_comp0, metas = rmeta
        # 找对应 entry 的 meta
        for i, m in enumerate(metas):
            if i == entries.index(reg_entry):
                meta = dict(m); meta["comp_state"] = compressed
                break
    except Exception:
        pass
    meta.setdefault("comp_state", compressed)
    meta.setdefault("comp_type", 0x5A42)
    meta.setdefault("mem_size", len(reg_body))

    # ---- 构造新 WW_ANIM_XML instance ----
    old_disp = src_disp
    _o, new_block, ok = _m2._replace_one_display(src_block, zh)
    if not ok or new_block is None:
        print("ERROR: 源条目 raw_display 节点数 != 1", file=sys.stderr); return 3
    if old_disp == zh:
        print("ERROR: 新显示名与源相同", file=sys.stderr); return 7

    salt = 0
    while True:
        new_ww_inst = _m2.gen_instance(ww_first.instance_id, a.ordinal, zh, salt)
        if new_ww_inst != ww_first.instance_id and new_ww_inst != 0:
            break
        salt += 1
    new_ww_tgi = f"0x{new_ww_inst:016x}"

    nhits = None
    # ---- 构造新 registry body ----
    if a.strategy == "swap-ref":
        new_reg_body = reg_body
        iv = old_ww_tgi
        reps = 0
        for v in (iv, iv.lower(), iv.upper(), iv[2:], iv[2:].lower(), iv[2:].upper()):
            cnt = new_reg_body.count(v.encode())
            if cnt:
                new_reg_body = new_reg_body.replace(v.encode(), new_ww_tgi.encode())
                reps += cnt
        if old_disp and zh:
            cnt = new_reg_body.count(old_disp.encode())
            if cnt:
                new_reg_body = new_reg_body.replace(old_disp.encode(), zh.encode())
                reps += cnt
        if reps == 0:
            print("ERROR: registry 里找不到旧 WW TGI/display 引用 (swap-ref 无可替换)",
                  file=sys.stderr); return 5
        if reps > 1:
            print(f"  [warning] swap-ref 替换了 {reps} 处旧 WW TGI 引用 (registry 多条目共享同一 WW XML instance; "
                  f"原地有损, 会把全部指向新 instance)")
        new_reg_inst = gen_registry_instance(rinst, a.ordinal, zh, "swap-ref")
    else:  # clone
        hits, used, kind = find_ref_blocks(reg_body, probe_strings)
        nhits = len(hits)
        if not hits:
            print("ERROR: registry 里找不到引用该 ordinal/旧 TGI 的条目块 (clone 无可克隆)",
                  file=sys.stderr); return 5
        new_reg_body = reg_body
        if kind != "xml":
            # 非 XML (binary/STBL): 无法安全克隆结构块, 退化为原地字节重写
            # (仍生成新 registry TGI, 视作'新注册副本')
            print(f"  [clone] kind={kind} -> 退化为原地字节重写 (binary 无可克隆块)")
            reps = 0
            for v in {old_ww_tgi, old_ww_tgi.lower(), old_ww_tgi.upper(),
                      old_ww_tgi[2:], old_ww_tgi[2:].lower(), old_ww_tgi[2:].upper()}:
                c = new_reg_body.count(v.encode())
                if c:
                    new_reg_body = new_reg_body.replace(v.encode(), new_ww_tgi.encode())
                    reps += c
            if old_disp and zh:
                c = new_reg_body.count(old_disp.encode())
                if c:
                    new_reg_body = new_reg_body.replace(old_disp.encode(), zh.encode())
                    reps += c
            if reps == 0:
                print("ERROR: binary registry 无可替换字节", file=sys.stderr); return 5
        else:
            # XML: 真正克隆条目块 (在解码文本上操作再回编码)
            _k, reg_text = _p1.decode_body(reg_body)
            new_text = reg_text
            for _nm, blk in hits:
                newblk = rewrite_block_tgi_and_display(
                    blk, old_ww_tgi, new_ww_tgi, old_disp, zh, src_clips, new_clips)
                new_text = new_text.replace(blk, blk + "\n" + newblk, 1)
            new_reg_body = new_text.encode()
        new_reg_inst = gen_registry_instance(rinst, a.ordinal, zh, "clone")

    # ---- 写 sidecar 包 ----
    ww_xml_full = _reassemble_xml(ord_blocks, a.ordinal, new_block)
    ww_full = ww_xml_full.encode()
    # 真机 WW XML 形态: 非 zlib plaintext, size_high_bit=1, comp_type=0x5A42
    ww_meta = {"comp_state": False, "size_high_bit": 1, "mem_size": len(ww_full)}
    reg_meta = dict(meta)
    # registry 原样保留其压缩态; mem_size 需为存储体长度(非 zlib)或解压长(zlib)
    reg_meta["mem_size"] = len(new_reg_body) if not wb._is_zlib(new_reg_body) else _reg_decomp_len(new_reg_body)
    items = [
        (WW_ANIM_XML, ww_first.group_id, new_ww_inst, ww_full, ww_meta),
        (reg_key[0], reg_key[1], new_reg_inst, new_reg_body, reg_meta),
    ]
    out_pkg = out_dir / f"p5_ord{a.ordinal}_{a.strategy}.package"
    try:
        wb.build_package(items, out_pkg, header_comp=2, major=2, minor=0)
    except Exception as ex:
        print(f"ERROR: 生成失败 {ex}", file=sys.stderr); return 8

    # 摘要
    print("=" * 60)
    nhits = nhits if nhits is not None else "n/a"
    print(f"P5 {a.strategy}: ordinal={a.ordinal}  '{old_disp}' -> '{zh}'")
    print(f"新 WW_ANIM_XML instance : {new_ww_tgi}")
    print(f"新 registry instance    : 0x{new_reg_inst:016x}")
    print(f"新 clip 名(s): {new_clips}")
    print(f"registry 类型           : 0x{rtype:08X} 策略={a.strategy} 命中块={nhits}")
    with open(out_dir / "p5_summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ordinal", "old_display", "new_display", "old_ww_tgi", "new_ww_tgi",
                    "reg_type", "reg_old_inst", "reg_new_inst", "strategy", "new_clips", "out_pkg"])
        w.writerow([a.ordinal, old_disp, zh, old_ww_tgi, new_ww_tgi,
                    f"0x{rtype:08X}", f"0x{rinst:016X}", f"0x{new_reg_inst:016X}",
                    a.strategy, ";".join(new_clips), out_pkg.name])
    print(f"OUT_PKG={out_pkg}")
    print(f"OUT_SUMMARY={out_dir / 'p5_summary.csv'}")
    print("P5_REGISTRY_CLONE=OK (只读源; 只写 sidecar, 未动 Mods)")
    print("ZERO_WRITE_TO_MODS=YES")
    return 0


def _reassemble_xml(ord_blocks, target, new_block):
    """用新条目块替换目标 ordinal 后重拼 animations_list (保持其它条目原样)。"""
    n = len(ord_blocks)
    parts = []
    for i in range(n):
        parts.append(new_block if i == target else ord_blocks[i])
    inner = "\n".join(parts)
    return '<?xml version="1.0"?><U n="WW_ANIMATION"><L n="animations_list">\n' + inner + '\n</L></U>'


def _reg_decomp_len(body: bytes) -> int:
    """registry body 解压后长度 (供 mem_size); 非 zlib 返回存储长。"""
    if wb._is_zlib(body):
        try:
            return len(zlib.decompress(body))
        except Exception:
            return len(body)
    return len(body)


if __name__ == "__main__":
    sys.exit(main())
