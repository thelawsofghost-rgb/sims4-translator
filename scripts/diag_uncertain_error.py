#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读诊断: 剩余 3 个 SKIP_MAPPING_UNCERTAIN + 2 个 ERROR。

用法 (Windows):
    python scripts\\diag_uncertain_error.py "<pkg1>" "<pkg2>" ...

对 SKIP_MAPPING_UNCERTAIN 包: 逐条输出 unresolved player-visible ref 的
full provenance (field category / XML field+path / raw+normalized hash /
exact CHS target TGI / key 是否在 target CHS / 其他 0x01 STBL / 其他 locale STBL /
命中的 TGI + source text)。

对 ERROR 包: path_exists / file size / DBPF magic / major / minor /
exception type / exception message / 解析失败阶段 + 只读归因。

完全不写任何文件; 不改 coverage/cohort/writer; 不生成 sidecar; 不写 Mods; 不碰 Animation。
"""
import sys, os, struct, importlib.util
from pathlib import Path
from xml.etree import ElementTree as ET

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_HERE))

_spec = importlib.util.spec_from_file_location("pose_coverage", str(_HERE / "pose_coverage.py"))
pc = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(pc)

from dbpf_fast import safe_parse
from backend import get_backend

STBL_TID = 0x220557DA
LOCALE_CHS = 0x01

_PV_NAMES = {
    "display_name": "PACK_TITLE",
    "description": "PACK_DESCRIPTION",
    "pose_display_name": "POSE_DISPLAY_NAME",
}


def _norm(v):
    """与 pose_coverage.parse_display_hash 完全一致 (zero sentinel -> None)。"""
    return pc.parse_display_hash(v)


# ---------------------------------------------------------------- mapping uncertain 诊断
def diag_uncertain(path):
    print("=" * 78)
    print(f"PACKAGE: {path}")
    print("=" * 78)
    if not os.path.exists(path):
        print("  [ERROR] 文件不存在")
        return
    print(f"  file_size = {os.path.getsize(path)} bytes")

    idx, err = safe_parse(path)
    if err or idx is None:
        print(f"  [ERROR] safe_parse 失败: {err}")
        return

    backend = get_backend("readonly").open(path)

    # ---- 全部 STBL 汇总: inst -> (ver, comp, kvs) ----
    stbl_parsed = {}
    for e in idx.entries:
        if e.type_id != STBL_TID:
            continue
        try:
            data = backend.read_small_resource(e, max_bytes=2 * 1024 * 1024)
        except Exception:
            data = None
        pr = pc.parse_stbl(data) if data else None
        if pr:
            stbl_parsed[e.instance_id] = pr
    # 需要 entry 对象以输出 TGI; 重建 map
    stbl_inst_map = {e.instance_id: e for e in idx.entries if e.type_id == STBL_TID}

    def tgi(inst):
        e = stbl_inst_map.get(inst)
        if not e:
            return f"0x220557DA/0x80000000/0x{inst:016X}"
        return (f"0x220557DA/0x{e.group_id:08X}/0x{e.instance_id:016X}")

    # 各 locale 集合
    chs_all = {i: kvs for i, (v, c, kvs) in stbl_parsed.items() if ((i >> 56) & 0xFF) == LOCALE_CHS}
    other_loc = {i: kvs for i, (v, c, kvs) in stbl_parsed.items() if ((i >> 56) & 0xFF) != LOCALE_CHS}
    chs_sorted = sorted(chs_all.keys())

    print(f"  STBL 总数 = {len(stbl_parsed)}")
    print(f"  0x01 CHS STBL 数 = {len(chs_all)}  (TGI: {', '.join(tgi(i) for i in chs_sorted)})")
    print(f"  其他 locale STBL 数 = {len(other_loc)}")

    if len(chs_all) != 1:
        print(f"  [NOTE] CHS 目标 STBL 数 {len(chs_all)} != 1 (若 >1 属 AMBIGUOUS, 非本类)")
        # 仍逐条输出所有 CHS 的 key 池
        chs_key_sets = {i: set(kh for kh, _, _ in kvs) for i, kvs in chs_all.items()}
    else:
        chs_key_sets = {i: set(kh for kh, _, _ in kvs) for i, kvs in chs_all.items()}
    chs_key_union = set().union(*chs_key_sets.values()) if chs_key_sets else set()
    other_key_union = set().union(*(set(kh for kh, _, _ in kvs) for kvs in other_loc.values())) if other_loc else set()

    # ---- 位置门控遍历 XML, 收集 pv refs (与 pose_coverage 同逻辑) ----
    xmls = pc.read_xml_payloads(backend, idx.entries)
    posexmls = [x for x in xmls if pc.is_pose_pack_root(x[1])]
    print(f"  PosePack XML = {len(posexmls)} / 全部 XML = {len(xmls)}")

    rows = []  # (field, raw, norm, cls, pvc, xinst_id, path_str)
    none_skipped = 0
    none_refs = []  # (field, raw, xinst_id, path) 只供透明展示, 不计入 unresolved
    for xinst_id, root, _raw in posexmls:
        parent_map = {c: p for p in root.iter() for c in p}
        root_parent = None
        for el, pack_level, in_pose in pc._walk_ctx(root):
            n = el.attrib.get("n")
            if not n:
                continue
            if any(True for _ in el) and el.text is None:
                continue
            val = (el.text or "").strip()
            if not val:
                continue
            nl = n.lower()
            # 与 pose_coverage 完全一致的 pvc 门控
            pvc = None
            if nl == "display_name" and pack_level:
                pvc = "TRANSLATE"
            elif nl == "description" and pack_level:
                pvc = "TRANSLATE"
            elif nl == "pose_display_name" and in_pose:
                pvc = "TRANSLATE"
            if pvc is None:
                continue
            h = _norm(val)
            if h is None:
                # 与 pose_coverage 的 parse_display_hash 一致: zero/invalid sentinel = 无引用。
                # 不计 pv total / 不计 unresolved / 不进入打印候选。只旁路做透明计数。
                none_skipped += 1
                none_refs.append((nl, val, xinst_id, _xml_path(el, parent_map)))
                continue
            rows.append((nl, val, h, pvc, xinst_id, _xml_path(el, parent_map)))

    print(f"\n  位置门控后 player-visible refs 总数 = {len(rows)}  "
          f"(另跳过 None/zero sentinel=无引用 {none_skipped})")
    if none_refs:
        print("  被滤掉的 None/zero sentinel refs(不计 pv total/unresolved):")
        for nl, raw, xid, xp in none_refs:
            print(f"    field={nl} raw={raw!r} xinst=0x{xid:016X} path={xp}")
    if not rows:
        print("  (无位置门控后的 pv refs)")

    # 分组: 每个 hash 一条
    from collections import OrderedDict
    by_hash = OrderedDict()
    for nl, raw, h, pvc, xid, xpath in rows:
        by_hash.setdefault(h, []).append((nl, raw, pvc, xid, xpath))

    for h, refs in by_hash.items():
        nl, raw, pvc, xid, xpath = refs[0]
        fcat = _PV_NAMES.get(nl, pvc)
        # 防御: h 绝不为 None (收集时已滤), 但绝不冒险用 None 做 :08X
        hstr = f"None (无引用/已滤)" if h is None else f"0x{h:08X}"
        print(f"\n  --- unresolved 候选 (hash={hstr}, {fcat}) ---")
        print(f"    field           : {nl}")
        print(f"    raw hash        : {raw}")
        print(f"    normalized hash : {hstr}")
        for (nl2, raw2, pvc2, xid2, xp) in refs:
            print(f"    XML ref         : field={nl2} cls={pvc2} xinst=0x{xid2:016X} path={xp}")
        if h is None:
            # 防御: 收集时已滤, 理论上不可达; 绝不冒险继续
            print("    -> hash 无效/zero: 已滤, 不计 unresolved")
            continue

        # 命中判定
        tgt_hits = [i for i in chs_all if h in chs_key_sets[i]]
        other0x01 = [i for i in chs_all if h in chs_key_sets[i]]  # 同集合, 只是直观呈现
        other_loc_hits = [i for i in other_loc if h in other_key_union and h in set(kh for kh, _, _ in other_loc[i])]
        print(f"    exact CHS target TGI : {tgi(chs_sorted[0]) if len(chs_sorted)==1 else '(多 CHS, 无单一 target)'}")
        if tgt_hits:
            for i in tgt_hits:
                txt = next((t for kh, _, t in chs_all[i] if kh == h), None)
                print(f"      命中 target CHS 0x{i:016X}: 存在! source={txt!r}")
        else:
            print(f"      key 在 target CHS 中: 不存在")
        if other0x01:
            print(f"      key 在(其他)0x01 CHS STBL: {', '.join(f'0x{i:016X}' for i in other0x01)}")
        else:
            print(f"      key 在(其他)0x01 CHS STBL: 无")
        if other_loc_hits:
            for i in other_loc_hits:
                txt = next((t for kh, _, t in other_loc[i] if kh == h), None)
                print(f"      key 在其他 locale STBL 0x{i:016X} ({tgi(i)}): source={txt!r}")
        else:
            print(f"      key 在其他 locale STBL: 无")

    backend.close()


def _xml_path(el, parent_map):
    """从 el 到根部的节点名路径 (parent_map: child->parent), 用于 provenance。"""
    parts = []
    node = el
    while node is not None:
        n = node.attrib.get("n")
        name = n if n else (node.tag or "?")
        parts.append(name)
        node = parent_map.get(node)
    return "/".join(reversed(parts))


# ---------------------------------------------------------------- ERROR 诊断
def diag_error(path):
    print("=" * 78)
    print(f"PACKAGE: {path}")
    print("=" * 78)
    exists = os.path.exists(path)
    print(f"  path_exists    = {exists}")
    if not exists:
        print("  -> 损坏归因: UNKNOWN (文件不存在)")
        return
    size = os.path.getsize(path)
    print(f"  actual_file_size = {size} bytes")
    if size < 0x44:
        print("  -> 损坏归因: TOO_SMALL (不足以容纳 DBPF Header + Index)")
        return

    # 直接读原始 header, 不依赖 safe_parse 的封装
    magic = major = minor = None
    with open(path, "rb") as fh:
        header = fh.read(16)
    if len(header) >= 4:
        magic = header[0:4]
    if len(header) >= 12:
        major, minor = struct.unpack("<II", header[4:12])
    print(f"  DBPF magic       = {magic!r} ({'' if magic == b'DBPF' else '!= DBPF'})")
    print(f"  major / minor    = {major} / {minor}")

    try:
        idx, err = safe_parse(path)
    except Exception as e:
        import traceback
        print(f"  exception_type    = {type(e).__name__}")
        print(f"  exception_message = {e}")
        print(f"  parser_failed_stage = 'safe_parse raise (non-caught)'")
        print("  -> 损坏归因: UNKNOWN_PARSE_EXCEPTION")
        return
    if err is None and idx is not None:
        print(f"  safe_parse = OK (index entries={len(idx.entries)})")
        print(f"  -> 注意: 该包在当前解析下并不报 ERROR; 若此前 scan 报 ERROR 可能是其他资源阶段")
        print(f"  -> 损坏归因: UNKNOWN (safe_parse 通过)")
        return
    # err != None
    if err == "ERROR_UNSUPPORTED_DBPF":
        print(f"  exception_type    = UnsupportedDBPFError (由 safe_parse 归类)")
        maybe = ""
        if major not in (2, 3, 4):
            maybe = f" -> major={major} 不在受支持集合 {{2,3,4}}"
        print(f"  parser_failed_stage = 'DBPF index read (UnsupportedDBPFError)'{maybe}")
        print(f"  -> 损坏归因: UNSUPPORTED_DBPF (无法确认的 Header/Index 布局, 非简单损坏)")
    else:  # 'ERROR'
        # 直接尝试读 index 看异常细节
        try:
            with open(path, "rb") as fh:
                fh.seek(0, 2); sz = fh.tell()
                from dbpf_fast import FastIndexReader
                fh.seek(0); r = FastIndexReader(fh, sz); r.read_index()
            print(f"  parser_failed_stage = 'safe_parse ERROR 但手动 read_index 通过 (异常在别处)'")
            print(f"  -> 损坏归因: UNKNOWN")
        except Exception as e2:
            import traceback
            print(f"  exception_type    = {type(e2).__name__}")
            print(f"  exception_message = {e2}")
            tb = traceback.format_exc().strip().splitlines()
            print(f"  last_trace_frame  = {tb[-2] if len(tb) >= 2 else tb[-1] if tb else ''}")
            print(f"  parser_failed_stage = 'FastIndexReader.read_index'")
            msg = str(e2)
            if "expect" in msg.lower() or "too short" in msg.lower() or "index" in msg.lower():
                print(f"  -> 损坏归因: CORRUPT_INDEX (index 解析失败 / 长度不符)")
            elif magic != b"DBPF":
                print(f"  -> 损坏归因: NOT_A_DBPF (magic 不符: {magic!r})")
            else:
                print(f"  -> 损坏归因: UNKNOWN")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    mode = sys.argv[1]
    paths = sys.argv[2:]
    if mode == "--uncertain":
        for p in paths:
            diag_uncertain(p)
    elif mode == "--error":
        for p in paths:
            diag_error(p)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
