#!/usr/bin/env python3
"""
Tibo131 单包精确映射审计 (Phase 3B2 Step③ 前置最后一关)

目标: 在生成 sidecar 前, 把 PosePackInstance 的玩家可见字段【精确 join】到
      包内全部 STBL key, 输出三类判定:

  TRANSLATE          : 有结构证据证明玩家可见 (如 pose_display_name 引用) 且能精确 join 到 STBL key
  KEEP               : 有结构证据属于 author / protected / 内部代码 (pose_name, 作者字段等)
  UNMAPPED_UNCERTAIN : 无结构证据 (STBL 里存在但没有任何 XML 字段引用, 或引用断裂)

铁律:
  * 只读, 修改 package 数量 = 0。
  * 【不】根据 STBL 文本长相直接判 TRANSLATE —— 判定只来自 XML 结构引用。
  * pose_name 不视为玩家可见; 仅 pose_display_name 及其明确显示类字段才算。
  * 引不到 XML 的 key 一律 UNMAPPED_UNCERTAIN, 绝不猜。
  * 输出每 key: keyHash / flags / STBL 文本 / 结构来源(哪个XML字段) / 判定 / 依据。

用法:
  python scripts\audit_tibo_exact_map.py "<Tibo131_PosePack 2.package>"
  (可选) --dump-xml  额外打印 PosePackInstance 里所有 <T n=...> 字段名与文本, 便于人工核对结构性字段

输出:
  终端 + output/tibo131_exact_map.csv  (若 output/ 不存在则同目录)
"""
import sys, os, zlib, csv, struct
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse
from backend import get_backend

STBL_TID = 0x220557DA
MAGIC = b"STBL"


def _is_xml_candidate(tid):
    from resource_types import RESOURCE_TYPES
    return (RESOURCE_TYPES.is_snippet(tid)
            or RESOURCE_TYPES.is_tuning_xml(tid)
            or RESOURCE_TYPES.is_known_safely(tid, "WW_ANIM_XML"))


def parse_stbl(data):
    if not data:
        return None
    if data[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        try:
            data = zlib.decompress(data)
        except Exception:
            pass
    if data[:4] != MAGIC:
        return None
    try:
        off = 4
        version = struct.unpack_from("<H", data, off)[0]; off += 2
        _c = data[off]; off += 1
        num = struct.unpack_from("<Q", data, off)[0]; off += 8
        off += 2
        _sl = struct.unpack_from("<I", data, off)[0]; off += 4
        if version != 5:
            return None
        # 同时保留 flags（keyHash -> (flags, text)），canary 已证 flags 正常
        result = {}
        for _ in range(num):
            if off + 8 > len(data):
                break
            kh = struct.unpack_from("<I", data, off)[0]; off += 4
            fl = data[off]; off += 1
            ln = struct.unpack_from("<H", data, off)[0]; off += 2
            if off + ln > len(data):
                break
            txt = data[off:off + ln].decode("utf-8", errors="replace")
            off += ln
            result[kh] = (fl, txt)
        return result
    except Exception:
        return None


def parse_display_hash(s):
    if not s:
        return None
    s = s.strip()
    try:
        v = int(s, 16) if s.lower().startswith("0x") else int(s, 0)
        return v & 0xFFFFFFFF
    except ValueError:
        return None


def read_xml_payloads(backend, entries):
    """返回 [(instance_id, raw_xml_str)] —— 已做 zlib/utf 探测, 要求真能 ET.parse。"""
    out = []
    for e in entries:
        if not _is_xml_candidate(e.type_id):
            continue
        try:
            data = backend.read_small_resource(e, max_bytes=1024 * 1024)
        except Exception:
            continue
        if not data:
            continue
        if data[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
            try:
                data = zlib.decompress(data)
            except Exception:
                pass
        for enc in ("utf-8", "utf-16-le"):
            try:
                raw = data.decode(enc)
                ET.fromstring(raw)
            except Exception:
                continue
            out.append((getattr(e, "instance_id", 0), raw))
            break
    return out


def is_hash_like(s):
    s = (s or "").strip()
    if not s:
        return False
    try:
        parse_display_hash(s)
        return True
    except Exception:
        return False


def build_field_references(root, pack_inst):
    """
    遍历整棵 PosePackInstance XML, 收集所有【字段名及其值】。
    返回:
      refs  : [(field_name, raw_value, hash_int_or_None)]
      display_fields : 语义上“玩家可见显示”的字段名集合 (已确证/高置信)
      authorish_fields: 作者/内部字段名集合
    这里【不凭值猜翻译】, 只凭字段名语义 + 是否 hash 引用 STBL 来判。
    """
    refs = []
    display_fields = set()
    authorish_fields = set()

    # 字段名 → 语义归类 (保守): 只把名字明确是“显示/标题”的当玩家可见,
    # 明确是“名称/作者/内部”的当 protected。其余不预判, 靠是否有 hash 引用 + 归类表。
    DISPLAY_HINTS = ("display_name", "display", "pack_title", "title", "modal_name",
                     "ui_name", "tooltip", "label")
    AUTHOR_HINTS = ("author", "creator", "pose_name", "creator_name", "by_line",
                    "internal_name", "clip_name", "animation_name", "raw_display",
                    "file_name", "package_name", "id", "unique_id", "key" "version",
                    "category", "tags", "tag")

    def classify_field(fname):
        fl = (fname or "").lower()
        if any(h in fl for h in DISPLAY_HINTS):
            return "DISPLAY"
        if any(h in fl for h in AUTHOR_HINTS):
            return "AUTHORISH"
        return "OTHER"

    seen_keys = set()
    for el in root.iter():
        n = el.attrib.get("n")
        tag = el.tag or ""
        if not n:
            continue
        # 只处理叶子文本元素 (含文本且无子元素), 避免同一字段被祖先/子重复计数
        has_children = any(True for _ in el)
        if has_children and el.text is None:
            continue
        val = ""
        if el.text:
            val = el.text.strip()
        # 容器元素(如 <U>)本身无 n= 文本; <T n=..>text</T> 才有值
        if tag in ("T", "U", "L", "S", "E", "I", "V", "M", "B", "A") and not has_children and val:
            cls = classify_field(n)
            h = parse_display_hash(val) if is_hash_like(val) else None
            refs.append((n, val, h, cls))
            if cls == "DISPLAY":
                display_fields.add(n)
            elif cls == "AUTHORISH":
                authorish_fields.add(n)

    return refs, display_fields, authorish_fields


def main():
    args = []
    keep_overrides = {}   # keyHash_int -> reason
    dump_xml = False
    i = 1  # 跳过 sys.argv[0] (程序名)
    while i < len(sys.argv):
        a = sys.argv[i]
        if a == "--dump-xml":
            dump_xml = True
        elif a == "--keep":
            # 用法: --keep KEYHASH:REASON   (人工判定保护/作者字段, 覆盖无结构证据的 UNMAPPED)
            raw = sys.argv[i + 1]; i += 1
            if ":" in raw:
                kh_s, reason = raw.split(":", 1)
            else:
                kh_s, reason = raw, "人工指定 KEEP"
            keep_overrides[int(kh_s, 0)] = reason
        else:
            args.append(a)
        i += 1
    if not args:
        print("用法: python scripts\\audit_tibo_exact_map.py [--dump-xml] [--keep KEYHASH:REASON ...] <package>")
        return 1
    pkg_path = Path(args[0])
    if not pkg_path.exists():
        print(f"[ERROR] 文件不存在: {pkg_path}")
        return 1

    idx, err = safe_parse(str(pkg_path))
    if err or idx is None:
        print(f"[ERROR] DBPF 解析失败: {err}")
        return 1

    backend = get_backend("readonly").open(str(pkg_path))

    # ---- 1) 收集全部 STBL key ----
    stbl_keys = {}   # kh -> (flags, text)
    stbl_insts = {}  # kh -> [instance_id...]
    for e in idx.entries:
        if e.type_id == STBL_TID:
            try:
                data = backend.read_small_resource(e, max_bytes=2 * 1024 * 1024)
            except Exception:
                continue
            parsed = parse_stbl(data) if data else None
            if parsed:
                for kh, (fl, txt) in parsed.items():
                    stbl_keys[kh] = (fl, txt)
                    stbl_insts.setdefault(kh, []).append(getattr(e, "instance_id", 0))

    # ---- 2) 收集所有 XML (PosePackInstance 及候选) ----
    xml_payloads = read_xml_payloads(backend, idx.entries)

    # ---- 3) 从所有 XML 汇总 结构引用 ----
    # key_hash -> {source_field, source_inst, raw_value}
    struct_ref = {}
    all_field_refs = []  # (field, raw, hash, cls)  用于人肉核对
    display_ref_fields = set()
    authorish_ref_fields = set()
    for xinst_id, raw in xml_payloads:
        try:
            root = ET.fromstring(raw)
        except Exception:
            continue
        refs, dispf, authf = build_field_references(root, xinst_id)
        all_field_refs.extend((xinst_id, n, v, h, c) for (n, v, h, c) in refs)
        display_ref_fields |= set(dispf)
        authorish_ref_fields |= set(authf)
        for (n, v, h, c) in refs:
            if h is not None:
                struct_ref.setdefault(h, []).append((n, xinst_id, v, c))
    backend.close()

    # ---- 4) 逐 STBL key 判定 ----
    rows = []
    for kh in sorted(stbl_keys):
        fl, txt = stbl_keys[kh]
        insts = ";".join(f"0x{i:016X}" for i in sorted(stbl_insts.get(kh, [])))
        refs = struct_ref.get(kh, [])
        manual_keep = keep_overrides.get(kh)

        if manual_keep is not None:
            verdict = "KEEP"
            source = "人工规则(无结构证据或结构性证据不足)"
            reason = f"人工指定 KEEP: {manual_keep}"
        elif not refs:
            verdict = "UNMAPPED_UNCERTAIN"
            reason = "STBL 存在但无任何 XML 字段引用 (无结构证据)"
            source = ""
        else:
            # 取引用: 有无 DISPLAY 类字段引用
            disp_hits = [r for r in refs if r[3] == "DISPLAY"]
            auth_hits = [r for r in refs if r[3] == "AUTHORISH"]
            if disp_hits:
                verdict = "TRANSLATE"
                fld, xinst, val, _ = disp_hits[0]
                source = f"pose_pack=0x{xinst:016X} <{fld}>"
                reason = f"结构字段 `{fld}` 精确引用该 key"
            elif auth_hits:
                verdict = "KEEP"
                fld, xinst, val, _ = auth_hits[0]
                source = f"pose_pack=0x{xinst:016X} <{fld}>"
                reason = f"结构字段 `{fld}` (作者/内部) 引用 → 保留"
            else:
                # 被 OTHER 类字段引用 → 无明确玩家可见证据
                verdict = "UNMAPPED_UNCERTAIN"
                flds = ",".join(sorted({r[0] for r in refs}))
                source = f"被非显示字段引用: {flds}"
                reason = f"引用字段 {flds} 不属于已确证的玩家可见显示字段"

        rows.append({
            "key_hash": f"0x{kh:08X}",
            "flags": str(fl),
            "stbl_text": txt,
            "stbl_instance": insts,
            "verdict": verdict,
            "source": source,
            "reason": reason,
        })

    # ---- 5) 输出 ----
    print(f"\n== {pkg_path.name}")
    print(f"   STBL keys 总数: {len(stbl_keys)}")
    print(f"   XML (PosePackInstance 候选) 数: {len(xml_payloads)}")
    print(f"   玩家可见显示字段(结构): {sorted(display_ref_fields) if display_ref_fields else '(侦测到)'}")
    print(f"   作者/内部字段(结构): {sorted(authorish_ref_fields) if authorish_ref_fields else '(无)'}")
    print(f"\n   判定汇总:")
    t = sum(1 for r in rows if r["verdict"] == "TRANSLATE")
    k = sum(1 for r in rows if r["verdict"] == "KEEP")
    u = sum(1 for r in rows if r["verdict"] == "UNMAPPED_UNCERTAIN")
    print(f"     TRANSLATE: {t}")
    print(f"     KEEP:      {k}")
    print(f"     UNMAPPED_UNCERTAIN: {u}")

    print("\n   ---- 明细 ----")
    for r in rows:
        print(f"   [{r['verdict']:<20}] key={r['key_hash']} fl={r['flags']} :: {r['stbl_text']!r}")
        print(f"        {r['reason']}  [{r['source']}]")

    if dump_xml:
        print("\n   ---- PosePackInstance 全部字段 (结构核对) ----")
        for xinst_id, n, v, h, c in all_field_refs:
            print(f"     0x{xinst_id:016X} <{n}> cls={c} hash={f'0x{h:08X}' if h is not None else '-'} = {v!r}")

    # ---- 写 CSV ----
    out_dir = Path(os.getcwd()) / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / "tibo131_exact_map.csv"
    cols = ["key_hash", "flags", "stbl_text", "stbl_instance", "verdict", "source", "reason"]
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c_: r.get(c_, "") for c_ in cols})
    print(f"\n已写出: {out_csv}  ({len(rows)} 行)")
    print("提示: TRANSLATE 依据是结构字段引用, 未按文本长相猜; 请人工复核 XML dump (加 --dump-xml)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
