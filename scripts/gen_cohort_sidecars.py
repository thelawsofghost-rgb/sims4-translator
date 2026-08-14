#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 3B2-SIDECAR —— Production-style CHS sidecar 批量生成器 (frozen 10-cohort)

目标 (用户 2026-08-15 定案, cohort_selection.csv 已冻结):
  对 frozen cohort 的 10 个 ELIGIBLE_EXISTING_CHS 源包, 各生成 1 个独立 CHS
  override sidecar (COMPLETE-STBL, 复用已通过游戏 canary 的 writer + UTF8
  EntrySize backport)。全部输出到 Mods 外独立目录, 不写原包, 不写 Mods。
  本脚本【只负责编排】: extraction/mapping/translation/QA 由 Python 完成,
  序列化完全交给 SidecarBuilder.exe (s4pi COMPLETE-STBL writer)。

铁律 (全部 fail-fast, 不补 key、不猜):
  * 一个 source 对应一个 sidecar; 只选 exact existing 0x01 CHS target TGI。
  * 只改 frozen structural mapping 的 approved player-visible keys:
        PACK_TITLE        = PosePackInstance-level display_name   (pack_level, 非0 hash)
        PACK_DESCRIPTION  = PosePackInstance-level description    (pack_level, 仅存在且 resolve)
        POSE_DISPLAY_NAME = pose_list/pose 内 pose_display_name   (in_pose)
  * source 文本与 target STBL 不符 / duplicate KeyHash / unresolved 引用 /
    missing CHS / unexpected TGI / 无译文 一律 fail-fast 该包, 绝不写 sidecar。

验证 (每 sidecar 都必须):
  writer VERIFY=PASS (SidecarBuilder.exe 内部 11 项 verify)
  independent audit = PASS (audit_canary_pair.py, 独立只读二次审计, 不调 writer reopen)
  exact TGI match / resource_count=1 / STBL version/count/order/flags preserved
  only approved keys changed / untouched entries byte/logical equivalent
  STRING_LENGTH == Σ(UTF8 byte count + 1)   (audit 内硬校验)

输出:
  <out_dir>/<slot>_<basename>_CHS.package     每个 sidecar
  <out_dir>/cohort_sidecar_manifest.csv       manifest (见 _MAN_COLS)
  终端逐包 summary; 结束时汇总 10 包 PASS 情况。

用法 (Windows, 先 cd 到仓库根, 短路径):
  python scripts\gen_cohort_sidecars.py ^
      --cohort output\cohort_selection.csv ^
      --out-dir output\cohort_sidecars ^
      --writer sidecar_builder\bin\Debug\SidecarBuilder.exe ^
      --overrides output\translation_overrides.csv ^
      --done output\translation_done.csv ^
      --cache output\translation_cache.db

仅离线生成 + 验证, 不复制进 Mods, 不做游戏启动, 不碰 Animation。
"""
import sys, os, csv, sqlite3, subprocess, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pose_coverage as PC            # 复用 scan 映射 (冻结): _walk_ctx/_classify_field/parse_display_hash/parse_stbl/PV_* 等
from phase2a_catalog import source_hash, norm_text, make_translation_id
from dbpf_fast import safe_parse
from backend import get_backend
import audit_canary_pair as AC        # 独立只读二次审计


# ---------------------------------------------------------------- 常量
_COH_SLOT = "cohort_slot"
_COH_PATH = "package_path"
_COH_STATUS = "status"
_COH_TGI = "CHS_target_TGI"

_STBL_TID = PC.STBL_TID              # 0x220557DA
_LOCALE_CHS = PC.LOCALE_CHS          # 0x01
_GROUP = 0x80000000

_MAN_COLS = [
    "cohort_slot", "source_package", "output_sidecar", "target_TGI",
    "modified_key_count", "writer_verify", "audit_result", "error",
]


# ---------------------------------------------------------------- 译名解析
class TranslationResolver:
    """按优先级解析源文本 -> 中文译文:
       override(T_<hash>_g1) -> done.csv -> cache.db。
       KEEP(override/done) 视为“不译/保英文”; 找不到任何译名 -> 返回 None (上层 fail-fast)。
    """

    def __init__(self, overrides_path, done_path=None, cache_path=None):
        self.overrides = {}     # translation_id -> (translation, action)
        self.done = {}          # translation_id -> (translation, status)
        self.cache_db = str(cache_path) if cache_path and Path(cache_path).exists() else None

        overrides_path = Path(overrides_path) if overrides_path else None
        if overrides_path and overrides_path.exists():
            with open(overrides_path, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    tid = (r.get("translation_id") or "").strip()
                    if not tid:
                        continue
                    self.overrides[tid] = (r.get("translation", "").strip(),
                                           (r.get("action") or "").strip())
        if done_path and Path(done_path).exists():
            with open(done_path, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    tid = (r.get("translation_id") or "").strip()
                    if not tid:
                        continue
                    self.done[tid] = (r.get("translation", "").strip(),
                                      (r.get("status") or "").strip())

    def _cache_lookup(self, source_text):
        if not self.cache_db:
            return None
        try:
            conn = sqlite3.connect(self.cache_db)
            cur = conn.cursor()
            cur.execute(
                "SELECT translation FROM phrase_cache WHERE source_phrase=? "
                "AND translation IS NOT NULL AND translation != '' LIMIT 1",
                (source_text,))
            row = cur.fetchone()
            conn.close()
            return row[0] if row else None
        except Exception:
            return None

    def resolve(self, source_text):
        """返回 (translation, source_tag) 或 (None, tag)。
           KEEP 类 -> (None, 'KEEP') —— 上层据此 fail-fast (approved 玩家可见 key 不应被 KEEP)。"""
        if not source_text or not source_text.strip():
            return None, "EMPTY"
        tid = make_translation_id(source_hash(norm_text(source_text)), 1)
        if tid in self.overrides:
            tr, act = self.overrides[tid]
            if act == "KEEP":
                return None, "KEEP"
            if tr:
                return tr, "OVERRIDE"
        if tid in self.done:
            tr, st = self.done[tid]
            if st == "KEEP" or (st and "KEEP" in st.upper()):
                return None, "KEEP"
            if tr:
                return tr, "DONE"
        c = self._cache_lookup(source_text)
        if c:
            return c, "CACHE"
        return None, "MISSING"


# ---------------------------------------------------------------- 每包: 抽 approved keys + 译文
def approved_pv_refs(pkg_path):
    """返回 (target_tgi_str, key_map, approved, errors) — 均基于 exact CHS target STBL。

    key_map  : {kh: (flags, text)}  exact CHS target STBL 的 key 全集 (unique)
    approved : [(category, kh, source_text)]
    """
    row = PC.scan_package(pkg_path)
    errors = []

    if row["status"] != "ELIGIBLE_EXISTING_CHS":
        return None, None, None, [f"状态不符: 期望 ELIGIBLE_EXISTING_CHS, 实得 {row['status']}"]

    # 唯一 CHS target
    if row["CHS_target_STBL_count"] != 1:
        return None, None, None, [f"CHS 目标 STBL 数 != 1 ({row['CHS_target_STBL_count']})"]

    # exact target TGI: 由实际 target STBL entry 派生 (不依赖 scan 行的 TGI 字段名)
    idx, err = safe_parse(pkg_path)
    if err or idx is None:
        return None, None, None, [f"DBPF 解析失败: {err}"]
    be = get_backend("readonly").open(pkg_path)
    target_entry = None
    for e in idx.entries:
        if e.type_id == _STBL_TID and ((e.instance_id >> 56) & 0xFF) == _LOCALE_CHS:
            target_entry = e
            break
    if target_entry is None:
        be.close()
        return None, None, None, ["包内无 0x01 CHS STBL (coverage 与扫描不一致)"]
    data = be.read_small_resource(target_entry, max_bytes=2 * 1024 * 1024)
    be.close()
    pr = PC.parse_stbl(data)
    if pr is None:
        return None, None, None, ["CHS target STBL 解析失败"]
    version, comp, kvs = pr
    # duplicate KeyHash 硬检查
    seen = {}
    dup = set()
    for kh, fl, txt in kvs:
        if kh in seen:
            dup.add(kh)
        seen[kh] = True
    if dup:
        return None, None, None, [f"CHS target STBL 含重复 KeyHash: {['0x%08X' % k for k in sorted(dup)]}"]
    key_map = {kh: (fl, txt) for kh, fl, txt in kvs}
    target_inst = target_entry.instance_id
    target_tgi = f"0x{_STBL_TID:08X}/0x{_GROUP:08X}/0x{target_inst:016X}"

    # 逐 pose XML 收集位置门控后的 approved PV ref
    be2 = get_backend("readonly").open(pkg_path)
    try:
        xmls = PC.read_xml_payloads(be2, idx.entries)
    finally:
        be2.close()
    approved = []      # (category, kh, source_text)
    seen_cat_key = set()
    for xinst_id, root, _raw in xmls:
        if not PC.is_pose_pack_root(root):
            continue
        for el, pack_level, in_pose in PC._walk_ctx(root):
            n = el.attrib.get("n")
            if not n:
                continue
            has_children = any(True for _ in el)
            if has_children and el.text is None:
                continue
            val = (el.text or "").strip()
            if not val:
                continue
            h = PC.parse_display_hash(val)
            nl = n.lower()
            cat = None
            if nl == "display_name" and pack_level:
                cat = "PACK_TITLE"
            elif nl == "description" and pack_level:
                cat = "PACK_DESCRIPTION"
            elif nl == "pose_display_name" and in_pose:
                cat = "POSE_DISPLAY_NAME"
            if cat is None or h is None:
                continue
            if h not in key_map:
                errors.append(f"{cat} 引用 hash 0x{h:08X} 不在 exact CHS target STBL")
                continue
            src_text = key_map[h][1]
            approved.append((cat, h, src_text))
            seen_cat_key.add((cat, h, src_text))

    if errors:
        return None, None, None, errors

    # 去重 (同一 key 被多个 XML 引用 -> 只写一次)
    uniq = {}
    for cat, h, txt in approved:
        uniq[(cat, h)] = txt
    final = []
    for (cat, h), txt in uniq.items():
        final.append((cat, h, txt))

    return target_tgi, key_map, final, []


def resolve_all_approved(pv_list, resolver, overrides_path):
    """为每 approved key 解析译文。返回 (mods, errors)。
    mods = [(kh, source_text, translation, source_tag)]"""
    mods = []
    errors = []
    for cat, kh, src in pv_list:
        tr, tag = resolver.resolve(src)
        if tr is None:
            errors.append(f"{cat} key 0x{kh:08X} (source={src!r}) 译文缺失/被KEEP (tag={tag})")
            continue
        mods.append((kh, src, tr, tag))
    return mods, errors


# ---------------------------------------------------------------- 编排
def run_one(slot, src_path, out_dir, writer_exe, resolver, overrides_path):
    """生成单个 sidecar。返回 manifest row dict。"""
    src = Path(src_path)
    out_dir = Path(out_dir) if not isinstance(out_dir, Path) else out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    base = src.stem
    out_sidecar = out_dir / f"{slot:02d}_{base}_CHS.package"

    row = {c: "" for c in _MAN_COLS}
    row["cohort_slot"] = str(slot)
    row["source_package"] = str(src_path)
    row["output_sidecar"] = str(out_sidecar)

    # ---- 1) mapping: approved keys + target TGI (fail-fast) ----
    try:
        target_tgi, key_map, approved, errs = approved_pv_refs(str(src))
    except Exception as ex:
        row["error"] = f"mapping 异常: {ex}"
        return row
    if errs:
        row["error"] = "; ".join(errs)
        return row
    row["target_TGI"] = target_tgi

    # ---- 2) 译文解析 (fail-fast) ----
    mods, errs = resolve_all_approved(approved, resolver, overrides_path)
    if errs:
        row["error"] = "; ".join(errs)
        return row
    row["modified_key_count"] = str(len(mods))

    # ---- 3) writer CLI ----
    inst = target_tgi.split("/")[-1]
    args = [
        writer_exe,
        "-source", str(src),
        "-out", str(out_sidecar),
        "-type", "0x%08X" % _STBL_TID,
        "-group", "0x%08X" % _GROUP,
        "-inst", inst,
        "-locale", "0x01",
    ]
    for kh, s_text, tr, _tag in mods:
        args += ["-m", f"0x{kh:08X}:{s_text}:{tr}"]

    try:
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=120)
    except Exception as ex:
        row["error"] = f"writer 调用失败: {ex}"
        return row
    out_txt = (r.stdout or "") + "\n" + (r.stderr or "")
    if "VERIFY=PASS" in out_txt and r.returncode == 0:
        row["writer_verify"] = "PASS"
    elif "VERIFY=FAIL" in out_txt:
        row["writer_verify"] = "FAIL"
    else:
        row["writer_verify"] = "ERROR"
        row["error"] = (row["error"] + "; " if row["error"] else "") + "writer 输出无 VERIFY=PASS"
    if row["writer_verify"] != "PASS":
        # 保留 writer stderr/stdout 前 400 字符到 error
        snippet = out_txt.strip().replace("\n", " | ")[:400]
        row["error"] = (row["error"] + "; " if row["error"] else "") + f"writer: {snippet}"

    # ---- 4) 独立二次审计 ----
    if row["writer_verify"] == "PASS" and out_sidecar.exists():
        a_args = [sys.executable, str(Path(__file__).resolve().parent / "audit_canary_pair.py"),
                  "-source", str(src), "-out", str(out_sidecar),
                  "-exp-inst", inst]
        for kh, _, _, _ in mods:
            a_args += ["-m", f"0x{kh:08X}"]
        a_args += ["-expected-keys",
                   ",".join(f"0x{kh:08X}:{tr}" for kh, _, tr, _ in mods)]
        a = subprocess.run(a_args, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        aout = (a.stdout or "") + "\n" + (a.stderr or "")
        if "AUDIT=PASS" in aout and a.returncode == 0:
            row["audit_result"] = "PASS"
        elif "AUDIT=FAIL" in aout:
            row["audit_result"] = "FAIL"
            snippet = aout.strip().replace("\n", " | ")[:400]
            row["error"] = (row["error"] + "; " if row["error"] else "") + f"audit: {snippet}"
        else:
            row["audit_result"] = "ERROR"
            snippet = aout.strip().replace("\n", " | ")[:400]
            row["error"] = (row["error"] + "; " if row["error"] else "") + f"audit 无 AUDIT 标签: {snippet}"
    else:
        row["audit_result"] = "SKIP"
        if out_sidecar.exists():
            try:
                out_sidecar.unlink()
            except Exception:
                pass

    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--writer", required=True)
    ap.add_argument("--overrides", required=True)
    ap.add_argument("--done", default="")
    ap.add_argument("--cache", default="")
    a = ap.parse_args()

    cohort = Path(a.cohort)
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    with open(cohort, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)

    resolver = TranslationResolver(a.overrides, a.done, a.cache)

    results = []
    for r in rows:
        slot = r.get(_COH_SLOT, "")
        path = r.get(_COH_PATH, "")
        if not path:
            continue
        mr = run_one(int(slot) if str(slot).isdigit() else 0, path, out_dir, a.writer,
                     resolver, a.overrides)
        results.append(mr)
        # 终端逐包 summary
        status = "✔" if (mr["writer_verify"] == "PASS" and mr["audit_result"] == "PASS") else "✘"
        print(f"[{status}] slot={mr['cohort_slot']:>2} {Path(path).name:<40} "
              f"keys={mr['modified_key_count'] or '-'} writer={mr['writer_verify'] or '-'} "
              f"audit={mr['audit_result'] or '-'}" +
              (f"  ERR={mr['error']}" if mr.get("error") else ""))

    manifest = out_dir / "cohort_sidecar_manifest.csv"
    with open(manifest, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=_MAN_COLS)
        w.writeheader()
        for mr in results:
            w.writerow(mr)

    # 汇总
    total = len(results)
    full = sum(1 for m in results if m["writer_verify"] == "PASS" and m["audit_result"] == "PASS")
    print(f"\nmanifest: {manifest}")
    print(f"汇总: {total} 包, 全 PASS = {full}, 失败 = {total - full}")
    return 0 if full == total and total > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
