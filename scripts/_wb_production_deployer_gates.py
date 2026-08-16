#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""白盒2: deployer 门 — SOURCE_HASH_FAIL / SIDECAR_HASH_FAIL / TGI_FAIL / LEXICAL_FAIL / malformed-report gate"""
import csv, os, subprocess, sys, tempfile, shutil
from pathlib import Path

BASE = "/root/.openclaw/workspace/sims4-translator"
SCRIPT = os.path.join(BASE, "scripts", "production_deployment_deployer.py")
STBL_TID = 0x220557DA; STBL_GROUP = 0x80000000

src = open("/tmp/wb_reconcile.py", encoding="utf-8").read()
top = src[:src.index("def main():")]
ns = {}; exec(compile(top, "wb_reconcile", "exec"), ns)
make_pkg = ns["make_pkg"]; sha = ns["sha"]


def tgi_str(i):
    return "0x%08X/0x%08X/0x%016X" % (STBL_TID, STBL_GROUP, i)


def run(a, cwd=BASE):
    return subprocess.run([sys.executable, SCRIPT] + a, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", cwd=cwd)


def build_manifest(base, mods, sr6, n_copies=12):
    rows = []
    for i in range(n_copies):
        g = mods / f"g{i:04d}" / f"G{i:04d}.package"
        make_pkg(g, [0x0100_0000_7000_0000 + i], 80000 + i)
        gs = sr6 / f"{90000+i:04d}_G{i:04d}_CHS.package"
        make_pkg(gs, [0x0100_0000_7000_0000 + i], 100000 + i)
        tgt = mods / f"g{i:04d}" / ("!" + f"G{i:04d}" + "_CHS.package")
        rows.append({"package_path": str(g), "source_basename": g.name,
                     "source_sha256": sha(g), "CHS_target_TGI": tgi_str(0x0100_0000_7000_0000 + i),
                     "candidate_basename": tgt.name, "candidate_target_path": str(tgt),
                     "sidecar_staging_path": str(gs), "prod_sidecar_sha256": sha(gs),
                     "duplicate_source_group": "NO", "deployment_action": "COPY_REQUIRED"})
    return rows


def write_manifest(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader()
        for rr in rows:
            w.writerow(rr)


def main():
    fails = []
    def check(n, c, d=""):
        print(("PASS" if c else "FAIL") + f"  {n}" + (f"  {d}" if d else ""))
        if not c: fails.append(n)

    base = Path(tempfile.mkdtemp(prefix="wb_deploy2_"))
    mods = base / "Mods"; mods.mkdir()
    sr6 = base / "sr6"; sr6.mkdir()
    rec_csv = base / "rec.csv"; rec_md = base / "rec.md"
    report = base / "report.md"

    # ---- happy: 全 PASS ----
    rows = build_manifest(base, mods, sr6)
    man = base / "m.csv"; write_manifest(man, rows)
    r = run(["--manifest", str(man), "--mods-root", str(mods),
             "--receipt-csv", str(rec_csv), "--receipt-md", str(rec_md),
             "--report", str(report)])
    check("happy PASS", r.returncode == 0 and "DRY_RUN: PASS" in r.stdout, f"rc={r.returncode}\n{r.stdout}")

    # ---- SOURCE_HASH_FAIL: 篡改 source 文件 ----
    rows2 = build_manifest(base, mods, sr6)
    # 篡改第一个 source
    src0 = Path(rows2[0]["package_path"]); src0.write_bytes(src0.read_bytes() + b"\x00")
    man2 = base / "m2.csv"; shutil.copytree(base, base / "x2") if False else None
    with open(man2, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows2[0].keys())); w.writeheader()
        for rr in rows2: w.writerow(rr)
    r2 = run(["--manifest", str(man2), "--mods-root", str(mods),
              "--receipt-csv", str(rec_csv), "--receipt-md", str(rec_md), "--report", str(report)])
    check("SOURCE_HASH_FAIL=1", "SOURCE_HASH_FAIL=1" in r2.stdout, r2.stdout)
    check("src-hash DRY FAIL", "DRY_RUN: FAIL" in r2.stdout and r2.returncode == 3, f"rc={r2.returncode}")
    # 还原 (复制回正确字节会改变 sha, 直接重建)
    make_pkg(src0, [0x0100_0000_7000_0000], 80000)  # 与 build 相同 seed

    # ---- SIDECAR_HASH_FAIL: 篡改 staging ----
    rows3 = build_manifest(base, mods, sr6)
    sp0 = Path(rows3[0]["sidecar_staging_path"]); sp0.write_bytes(sp0.read_bytes() + b"\x00")
    man3 = base / "m3.csv"
    with open(man3, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows3[0].keys())); w.writeheader()
        for rr in rows3: w.writerow(rr)
    r3 = run(["--manifest", str(man3), "--mods-root", str(mods),
              "--receipt-csv", str(rec_csv), "--receipt-md", str(rec_md), "--report", str(report)])
    check("SIDECAR_HASH_FAIL=1", "SIDECAR_HASH_FAIL=1" in r3.stdout, r3.stdout)
    make_pkg(sp0, [0x0100_0000_7000_0000], 100000)

    # ---- TGI_FAIL: staging SHA==manifest 但 embedded TGI != manifest 声明的 TGI ----
    rows4 = build_manifest(base, mods, sr6)
    rows4[1]["CHS_target_TGI"] = tgi_str(0x0100_0000_7FFF_9999)  # 声明不同 instance (staging 实际是 ...0001)
    man4 = base / "m4.csv"
    with open(man4, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows4[0].keys())); w.writeheader()
        for rr in rows4: w.writerow(rr)
    r4 = run(["--manifest", str(man4), "--mods-root", str(mods),
              "--receipt-csv", str(rec_csv), "--receipt-md", str(rec_md), "--report", str(report)])
    check("TGI_FAIL=1", "TGI_FAIL=1" in r4.stdout, r4.stdout)
    check("tgi DRY FAIL", "DRY_RUN: FAIL" in r4.stdout and r4.returncode == 3, f"rc={r4.returncode}")

    # ---- LEXICAL_FAIL: candidate 名不满足 < ----
    rows5 = build_manifest(base, mods, sr6)
    bad = dict(rows5[2]); bad["candidate_basename"] = "Z999_CHS.package"  # 'Z' > 'G'
    bad["candidate_target_path"] = str(Path(bad["candidate_target_path"]).parent / "Z999_CHS.package")
    rows5[2] = bad
    man5 = base / "m5.csv"
    with open(man5, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows5[0].keys())); w.writeheader()
        for rr in rows5: w.writerow(rr)
    r5 = run(["--manifest", str(man5), "--mods-root", str(mods),
              "--receipt-csv", str(rec_csv), "--receipt-md", str(rec_md), "--report", str(report)])
    check("LEXICAL_FAIL=1", "LEXICAL_FAIL=1" in r5.stdout, r5.stdout)

    # ---- malformed report gate: report 声明 malformed>0 => apply 拒绝 ----
    rowsG = build_manifest(base, mods, sr6)
    manG = base / "mg.csv"
    with open(manG, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rowsG[0].keys())); w.writeheader()
        for rr in rowsG: w.writerow(rr)
    report_bad = base / "report_bad.md"
    report_bad.write_text("- malformed_selected_source = 1\n- malformed_existing_localization_candidate = 0\n", encoding="utf-8")
    rG = run(["--manifest", str(manG), "--mods-root", str(mods),
              "--receipt-csv", str(rec_csv), "--receipt-md", str(rec_md),
              "--apply", "--ignore-ts4", "--report", str(report_bad)])
    check("apply blocked by malformed report", rG.returncode == 4 and "malformed_selected_source=1" in rG.stdout, f"rc={rG.returncode}\n{rG.stdout}")
    check("no targets written (fail-closed)", not list(mods.rglob("!G*_CHS.package")), "")

    print(f"\nHARNESS {'ALL PASS' if not fails else 'FAIL: ' + str(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
