#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ZERO-DEPLOY deployment manifest builder / preflight for the 441 pipeline.

输入 (均必需):
  --manifest-v1     output/production_sidecars_441_v1_manifest.csv
  --manifest-retry6 output/production_sidecars_retry6_v1_manifest.csv
  --mods-root       真实 Mods 根, 例如 C:\\Users\\thela\\Documents\\Electronic Arts\\The Sims 4\\Mods

只收:
  441_v1  manifest outcome == PASS  (且有非空 sidecar_path)  -> 期望 432
  retry6  manifest outcome == PASS  (且有非空 sidecar_path)  -> 期望   6
  (PASS_NOOP_KEEP_ONLY 无 sidecar, 不计入 deployment; KEEP-only 在本阶段不产生可部署文件)

必须得到:
  438 unique source packages
  438 unique sidecars
  duplicate source = 0
  duplicate target = 0
  missing staged artifact = 0

每行生成:
  source_package / source_sha256 / sidecar_staging_path / sidecar_sha256 /
  target_directory / target_filename / target_path / existing_target /
  existing_other_CHS_sidecar / collision_status

target contract (本机已真实验证规则, 仅记录, 不泛化为 Sims 全局 load-order 语义):
  - 与 source package 同目录
  - filename lexical earlier (target < source 字典序)
  - prefix = 000_
  - original package untouched

Mods 扫描: 检查每个 target_directory 中现有
  - 000_*.package / *_CHS.package 测试 sidecar (尤其旧 Anger / Gounafiers 测试文件)
  - 与 production sidecar 同 TGI (STBL instance) 共存 => collision_status = TGI_COEXIST

本阶段 ZERO DEPLOY: 只读 (open/sha256/dbpf parse), 不 copy / move / delete / rename Mods 中任何文件。

输出 (fail-closed, 已存在则拒写除非 --force):
  output/production_deployment_438_manifest.csv
  output/production_deployment_438_report.md
"""
import argparse
import csv
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dbpf_fast import safe_parse  # noqa: E402

_STBL_TID = 0x220557DA
_STBL_GROUP = 0x80000000

_EXPECT_V1 = 432
_EXPECT_RETRY = 6
_EXPECT_TOTAL = 438

MAN_OUT = "output/production_deployment_438_manifest.csv"
REP_OUT = "output/production_deployment_438_report.md"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _res(p: str) -> Path:
    return Path(p).expanduser().resolve()


def load_pass_rows(manifest: Path, label: str):
    """读取 manifest, 返回 outcome==PASS 且有 sidecar_path 的行列表。"""
    if not manifest.exists():
        print(f"[HARD-FAIL] manifest 不存在 (rc=2): {manifest}")
        sys.exit(2)
    rows = []
    with open(manifest, "r", newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("outcome", "") == "PASS" and r.get("sidecar_path", "").strip():
                rows.append(r)
    print(f"[load] {label}: PASS rows = {len(rows)}")
    return rows


def target_filename_for(source_name: str) -> str:
    """部署目标文件名: prefix 000_ + source stem + _CHS.package。
    保证 lexical earlier: '000_' 开头字典序恒早于以字母/非零数字开头的源文件名。"""
    stem = Path(source_name).stem
    return f"000_{stem}_CHS.package"


def scan_dir_stbl_instances(pkg: Path):
    """返回该 package 内 STBL 实例 id 集合 (只读)。解析失败返回空集。"""
    insts = set()
    idx, err = safe_parse(pkg)
    if err is not None or idx is None:
        return insts
    for e in idx.entries:
        if e.type_id == _STBL_TID and e.group_id == _STBL_GROUP and e.instance_id is not None:
            insts.add(e.instance_id)
    return insts


def main():
    ap = argparse.ArgumentParser(description="ZERO-DEPLOY deployment manifest builder / preflight (438)")
    ap.add_argument("--manifest-v1", required=True)
    ap.add_argument("--manifest-retry6", required=True)
    ap.add_argument("--mods-root", required=True)
    ap.add_argument("--out", default=MAN_OUT)
    ap.add_argument("--report", default=REP_OUT)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    out = Path(a.out).expanduser()
    rep = Path(a.report).expanduser()
    if (out.exists() or rep.exists()) and not a.force:
        print(f"[FAIL-CLOSED] 输出已存在, refuse (rc=1) 除非 --force: {out} | {rep}")
        return 1

    mods_root = _res(a.mods_root)
    if not mods_root.is_dir():
        print(f"[HARD-FAIL] mods-root 不是目录 (rc=2): {mods_root}")
        return 2

    rows_v1 = load_pass_rows(_res(a.manifest_v1), "441_v1")
    rows_retry = load_pass_rows(_res(a.manifest_retry6), "retry6")
    # NOOP = PASS_NOOP_KEEP_ONLY 行 (441_v1 内, 无 sidecar, 不计入 deployment)
    noop = 0
    with open(_res(a.manifest_v1), "r", newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("outcome", "") == "PASS_NOOP_KEEP_ONLY":
                noop += 1
    if len(rows_v1) != _EXPECT_V1 or len(rows_retry) != _EXPECT_RETRY:
        print(f"[HARD-FAIL] PASS 行数与验收不符 (rc=2): 441_v1={len(rows_v1)} (期望 {_EXPECT_V1}), "
              f"retry6={len(rows_retry)} (期望 {_EXPECT_RETRY})")
        return 2

    all_rows = rows_v1 + rows_retry
    total = len(all_rows)
    if total != _EXPECT_TOTAL:
        print(f"[HARD-FAIL] 总 PASS 行 {total} != {_EXPECT_TOTAL} (rc=2)")
        return 2

    # ---------- 唯一性 + 缺失 artifact ----------
    src_seen = {}
    side_seen = {}
    dup_src = dup_side = 0
    missing = 0
    for r in all_rows:
        sp = _res(r["package_path"])
        ss = _res(r["sidecar_path"])
        if sp in src_seen:
            dup_src += 1
        else:
            src_seen[sp] = r
        if ss in side_seen:
            dup_side += 1
        else:
            side_seen[ss] = r
        if not ss.is_file():
            missing += 1

    # ---------- 逐行 target 派生 + Mods 扫描 ----------
    out_rows = []
    tgt_seen = {}
    n_collision = 0        # existing_target or TGI_COEXIST
    n_other_chs = 0        # 同目录存在其他 CHS/000_ sidecar
    n_not_lex_earlier = 0  # target 未 lexical earlier (违背 contract)
    for r in all_rows:
        sp = _res(r["package_path"])
        ss = _res(r["sidecar_path"])
        target_dir = sp.parent
        source_name = sp.name
        target_name = target_filename_for(source_name)
        target_path = target_dir / target_name

        existing_target = target_path.exists()
        # 同目录其他侧车/测试 sidecar (排除目标自身 + 源 package)
        other_chs = []
        if target_dir.is_dir():
            for pk in sorted(target_dir.glob("*.package")):
                if pk.resolve() == target_path.resolve() or pk.resolve() == sp.resolve():
                    continue
                # 000_ / _CHS / Anger / Gounafiers 字样的既有文件一律记录
                low = pk.name.lower()
                if low.startswith("000_") or low.endswith("_chs.package") or \
                   "anger" in low or "gounafiers" in low or "test" in low:
                    other_chs.append(pk.name)
        if other_chs:
            n_other_chs += 1
        # TGI 共存: 目标 dir 内既有 package 是否含与 production sidecar 相同 STBL instance
        tgi_inst = None
        tgi = r.get("CHS_target_TGI", "")
        if "/" in tgi:
            try:
                tgi_inst = int(tgi.split("/")[-1], 16)
            except ValueError:
                tgi_inst = None
        tgi_collide = False
        if tgi_inst is not None and target_dir.is_dir():
            for pk in sorted(target_dir.glob("*.package")):
                if pk.resolve() == sp.resolve():
                    continue
                if tgi_inst in scan_dir_stbl_instances(pk):
                    tgi_collide = True
                    break

        collision = "NONE"
        if existing_target:
            collision = "TARGET_EXISTS"
        if tgi_collide:
            collision = "TGI_COEXIST" if collision == "NONE" else collision + "+TGI_COEXIST"
        if existing_target or tgi_collide:
            n_collision += 1

        lex_earlier = target_name.lower() < source_name.lower()
        if not lex_earlier:
            n_not_lex_earlier += 1

        # target 唯一性
        if target_path in tgt_seen:
            dup_tgt = True
        else:
            tgt_seen[target_path] = r
            dup_tgt = False

        out_rows.append({
            "source_package": str(sp),
            "source_sha256": r.get("source_sha256", ""),
            "sidecar_staging_path": str(ss),
            "sidecar_sha256": sha256_file(ss) if ss.is_file() else "",
            "target_directory": str(target_dir),
            "target_filename": target_name,
            "target_path": str(target_path),
            "existing_target": "YES" if existing_target else "NO",
            "existing_other_CHS_sidecar": ";".join(other_chs) if other_chs else "",
            "collision_status": collision,
            "lexical_earlier": "YES" if lex_earlier else "NO",
            "duplicate_target": "YES" if dup_tgt else "NO",
            "source_TGI": tgi,
        })

    dup_target = sum(1 for x in out_rows if x["duplicate_target"] == "YES")

    # ---------- 汇总 ----------
    n_source = len(src_seen)
    n_side = len(side_seen)
    ok = (n_source == _EXPECT_TOTAL and n_side == _EXPECT_TOTAL and
          dup_src == 0 and dup_target == 0 and missing == 0)
    verdict = "PASS" if ok else "FAIL"

    # ---------- 输出 (ZERO DEPLOY) ----------
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["source_package", "source_sha256", "sidecar_staging_path", "sidecar_sha256",
            "target_directory", "target_filename", "target_path", "existing_target",
            "existing_other_CHS_sidecar", "collision_status", "lexical_earlier",
            "duplicate_target", "source_TGI"]
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for row in out_rows:
            w.writerow(row)

    rep.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    lines.append("# 441 Deployment Preflight (ZERO DEPLOY)")
    lines.append("")
    lines.append(f"- manifest_v1   : {_res(a.manifest_v1)}  (PASS rows = {len(rows_v1)})")
    lines.append(f"- manifest_retry6: {_res(a.manifest_retry6)}  (PASS rows = {len(rows_retry)})")
    lines.append(f"- mods_root     : {mods_root}")
    lines.append("")
    lines.append("## authority")
    lines.append(f"- original staging PASS = {len(rows_v1)}")
    lines.append(f"- retry6 staging PASS   = {len(rows_retry)}")
    lines.append(f"- production sidecars   = {n_side}")
    lines.append(f"- KEEP-only NOOP        = {noop} (不计入 deployment)")
    lines.append("")
    lines.append("## uniqueness 检查")
    lines.append(f"- unique source packages = {n_source}")
    lines.append(f"- unique sidecars        = {n_side}")
    lines.append(f"- duplicate source       = {dup_src}")
    lines.append(f"- duplicate target       = {dup_target}")
    lines.append(f"- missing staged artifact= {missing}")
    lines.append("")
    lines.append("## target contract (本机已验证, 仅记录不泛化)")
    lines.append("- 与 source package 同目录")
    lines.append("- filename lexical earlier (target < source 字典序)")
    lines.append("- prefix = 000_")
    lines.append("- original package untouched")
    lines.append(f"- 违背 lexical-earlier 行数 = {n_not_lex_earlier}")
    lines.append("")
    lines.append("## Mods 扫描")
    lines.append(f"- existing-target collisions      = {n_collision}")
    lines.append(f"- 同目录既有其他 CHS/000_ sidecar = {n_other_chs}")
    lines.append("")
    lines.append("## 终局")
    lines.append(f"production rows = {total}")
    lines.append(f"existing-target collisions = {n_collision}")
    lines.append(f"existing localization sidecars = {n_other_chs}")
    lines.append(f"missing artifacts = {missing}")
    lines.append(f"duplicate targets = {dup_target}")
    lines.append(f"DEPLOYMENT_PREFLIGHT: {verdict}")
    with open(rep, "w", newline="", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # stdout summary
    print(f"## DEPLOYMENT_PREFLIGHT: {verdict}")
    print(f"production rows = {total}")
    print(f"existing-target collisions = {n_collision}")
    print(f"existing localization sidecars = {n_other_chs}")
    print(f"missing artifacts = {missing}")
    print(f"duplicate targets = {dup_target}")
    print(f"output: {out}")
    print(f"report: {rep}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
