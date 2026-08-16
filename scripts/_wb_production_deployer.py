#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""白盒: production_deployment_deployer.py — dry-run / apply / toctou / drift / rollback"""
import csv, os, shutil, subprocess, sys, tempfile
from pathlib import Path

BASE = "/root/.openclaw/workspace/sims4-translator"
SCRIPT = os.path.join(BASE, "scripts", "production_deployment_deployer.py")
STBL_TID = 0x220557DA
STBL_GROUP = 0x80000000

src = open("/tmp/wb_reconcile.py", encoding="utf-8").read()
top = src[:src.index("def main():")]
ns = {}
exec(compile(top, "wb_reconcile", "exec"), ns)
make_pkg = ns["make_pkg"]
sha = ns["sha"]


def tgi_str(i):
    return "0x%08X/0x%08X/0x%016X" % (STBL_TID, STBL_GROUP, i)


def run(a, cwd=BASE):
    return subprocess.run([sys.executable, SCRIPT] + a, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", cwd=cwd)


def main():
    fails = []
    def check(n, c, d=""):
        print(("PASS" if c else "FAIL") + f"  {n}" + (f"  {d}" if d else ""))
        if not c: fails.append(n)

    base = Path(tempfile.mkdtemp(prefix="wb_deploy_"))
    mods = base / "Mods"
    sv = base / "sv"; sv.mkdir()
    sr6 = base / "sr6"; sr6.mkdir()

    # ---- 构建 436 行 manifest: 432 COPY + 1 EXACT + 3 ALTNAME ----
    rows = []
    # 1 EXACT: Anika (source !Anika_Argument.package, candidate already present == prod)
    src_anika = mods / "anika" / "!Anika_Argument.package"
    make_pkg(src_anika, [0x0100_0000_5000_0001], 9001)
    prod_anika = sr6 / "001_Anika_CHS.package"
    make_pkg(prod_anika, [0x0100_0000_5000_0001], 19001)
    cand_anika = mods / "anika" / "!!Anika_Argument_CHS.package"
    make_pkg(cand_anika, [0x0100_0000_5000_0001], 19001)
    rows.append({"package_path": str(src_anika), "source_basename": src_anika.name,
                 "source_sha256": sha(src_anika), "CHS_target_TGI": tgi_str(0x0100_0000_5000_0001),
                 "candidate_basename": cand_anika.name, "candidate_target_path": str(cand_anika),
                 "sidecar_staging_path": str(prod_anika), "prod_sidecar_sha256": sha(prod_anika),
                 "duplicate_source_group": "NO", "deployment_action": "EXACT_CANDIDATE_SATISFIED"})
    # 3 ALTNAME: Anger / Tibo / Gounafier (existing 000_ altname == prod, source exists)
    alt_specs = [("AngerFrustrationandRageflowur", 0x0100_0000_5000_0002),
                 ("Tibo131_PosePack 2", 0x0100_0000_5000_0003),
                 ("02_Gounafiers_Poses_Public_Ver (1)", 0x0100_0000_5000_0004)]
    for k, (stem, inst) in enumerate(alt_specs):
        sd = mods / f"alt{k}"
        srcp = sd / f"{stem}.package"
        make_pkg(srcp, [inst], 9100 + k)
        prods = sr6 / f"0{k}0_{stem}_CHS.package"
        make_pkg(prods, [inst], 19100 + k)
        alt_f = sd / f"000_{stem}_CHS.package"
        make_pkg(alt_f, [inst], 19100 + k)
        rows.append({"package_path": str(srcp), "source_basename": srcp.name,
                     "source_sha256": sha(srcp), "CHS_target_TGI": tgi_str(inst),
                     "candidate_basename": "!" + stem + "_CHS.package",
                     "candidate_target_path": str(sd / ("!" + stem + "_CHS.package")),
                     "sidecar_staging_path": str(prods), "prod_sidecar_sha256": sha(prods),
                     "duplicate_source_group": "NO", "deployment_action": "EQUIVALENT_ALTNAME_SATISFIED"})
    # 432 COPY_REQUIRED: sources present + staging present, target absent
    copy_rows = []
    for i in range(432):
        g = mods / f"g{i:04d}" / f"G{i:04d}.package"
        make_pkg(g, [0x0100_0000_6000_0000 + i], 20000 + i)
        gs = sr6 / f"{40000+i:04d}_G{i:04d}_CHS.package"
        make_pkg(gs, [0x0100_0000_6000_0000 + i], 50000 + i)
        tgt = mods / f"g{i:04d}" / ("!" + f"G{i:04d}" + "_CHS.package")
        copy_rows.append({"package_path": str(g), "source_basename": g.name,
                          "source_sha256": sha(g), "CHS_target_TGI": tgi_str(0x0100_0000_6000_0000 + i),
                          "candidate_basename": tgt.name, "candidate_target_path": str(tgt),
                          "sidecar_staging_path": str(gs), "prod_sidecar_sha256": sha(gs),
                          "duplicate_source_group": "NO", "deployment_action": "COPY_REQUIRED"})
    rows.extend(copy_rows)
    assert len(rows) == 436, len(rows)

    man = base / "manifest.csv"
    with open(man, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)

    rec_csv = base / "rec.csv"
    rec_md = base / "rec.md"

    # ---------- DRY-RUN ----------
    r = run(["--manifest", str(man), "--mods-root", str(mods),
             "--receipt-csv", str(rec_csv), "--receipt-md", str(rec_md)])
    out = r.stdout
    check("dry rc==0", r.returncode == 0, f"rc={r.returncode}\n{out}")
    check("DEPLOY_TOTAL=436", "DEPLOY_TOTAL=436" in out, out)
    check("MANIFEST_COPY_REQUIRED=432", "MANIFEST_COPY_REQUIRED=432" in out, out)
    check("WOULD_COPY=432", "WOULD_COPY=432" in out, out)
    check("CURRENT_ALREADY_SATISFIED=0", "CURRENT_ALREADY_SATISFIED=0" in out, out)
    check("CURRENT+WOULD==432", "CURRENT_ALREADY_SATISFIED + WOULD_COPY = 432" in out, out)
    check("DRY_RUN: PASS", "DRY_RUN: PASS" in out, out)
    check("dry ZERO WRITE (no targets)", not list(mods.rglob("!G*_CHS.package")), "targets written in dry!")

    # ---------- APPLY (允许写) ----------
    r2 = run(["--manifest", str(man), "--mods-root", str(mods),
              "--receipt-csv", str(rec_csv), "--receipt-md", str(rec_md),
              "--apply", "--ignore-ts4"])
    out2 = r2.stdout
    check("apply rc==0", r2.returncode == 0, f"rc={r2.returncode}\n{out2}\n{r2.stderr}")
    check("COPIED=432", "COPIED=432" in out2, out2)
    check("APPLY: PASS", "APPLY: PASS" in out2, out2)
    # 432 targets written with correct SHA
    written = list(mods.rglob("!G*_CHS.package"))
    check("432 targets written", len(written) == 432, f"got {len(written)}")
    all_ok_sha = all(sha(t) == next(rr["prod_sidecar_sha256"] for rr in rows
                     if rr["candidate_target_path"] == str(t)) for t in written)
    check("all written SHA == staging", all_ok_sha, "")
    # receipt exists with 432 COPIED ABSENT
    rec_rows = list(csv.DictReader(open(rec_csv, encoding="utf-8-sig")))
    n_copied = sum(1 for x in rec_rows if x["action"] == "COPIED" and x["pre_state"] == "ABSENT")
    check("receipt 432 COPIED/ABSENT", n_copied == 432, f"got {n_copied}")
    check("receipt has target_sha256_after", all(x.get("target_sha256_after") for x in rec_rows if x["action"] == "COPIED"), "")

    # ---------- 二次 dry-run: 现在全部已是 ALREADY_SATISFIED ----------
    r3 = run(["--manifest", str(man), "--mods-root", str(mods),
              "--receipt-csv", str(rec_csv), "--receipt-md", str(rec_md)])
    out3 = r3.stdout
    check("2nd dry ALREADY=432", "CURRENT_ALREADY_SATISFIED=432" in out3, out3)
    check("2nd dry WOULD_COPY=0", "WOULD_COPY=0" in out3, out3)

    # ---------- ROLLBACK (应删除 432 COPIED) ----------
    r4 = run(["--rollback", "--mods-root", str(mods), "--receipt-csv", str(rec_csv)])
    out4 = r4.stdout
    check("rollback rc==0", r4.returncode == 0, f"rc={r4.returncode}\n{out4}")
    check("rollback deleted=432", "deleted=432" in out4, out4)
    check("rollback removed targets", not list(mods.rglob("!G*_CHS.package")), "")

    # ---------- 冻结保护区 rollback 拒绝 ----------
    rec_rows = list(csv.DictReader(open(rec_csv, encoding="utf-8-sig")))
    # 伪造一条 ABSENT+COPIED 指向 Anika candidate (冻结保护区) -> 应 REFUSE
    fake = dict(rec_rows[0])
    fake["target_path"] = str(cand_anika)
    fake["target_sha256_after"] = sha(cand_anika)
    fake["pre_state"] = "ABSENT"; fake["action"] = "COPIED"
    fakecsv = base / "rec2.csv"
    with open(fakecsv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(fake.keys())); w.writeheader(); w.writerow(fake)
    r5 = run(["--rollback", "--mods-root", str(mods), "--receipt-csv", str(fakecsv)])
    check("rollback REFUSE frozen Anika", "REFUSE" in r5.stdout, r5.stdout)
    check("Anika candidate still present", cand_anika.exists(), "")

    # ---------- drift: target 已存在且字节不同 -> HARD_ABORT ----------
    dr = base / "Mods_drift"
    shutil.copytree(mods, dr)
    # manifest2: 把候选路径重写到 dr (模拟在不同点对同一逻辑目标做判定)
    man2_rows = []
    for rr in rows:
        r2 = dict(rr)
        for k in ("package_path", "candidate_target_path"):
            if rr.get(k):
                p = Path(rr[k])
                if p.is_relative_to(mods):
                    r2[k] = str(dr / p.relative_to(mods))
        man2_rows.append(r2)
    # 制造一个 target 已存在但字节不同 (同 TGI instance, 不同 seed)
    first_copy = next(rr for rr in rows if rr["deployment_action"] == "COPY_REQUIRED")
    drift_target_dr = Path(man2_rows[rows.index(first_copy)]["candidate_target_path"])
    drift_target_dr.parent.mkdir(parents=True, exist_ok=True)
    drift_inst = int(first_copy["CHS_target_TGI"].split("/")[-1], 16)
    make_pkg(drift_target_dr, [drift_inst], 99999)  # 同 TGI instance, 不同字节 -> HARD_ABORT
    man2 = base / "manifest2.csv"
    with open(man2, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader()
        for rr in man2_rows:
            w.writerow(rr)
    r6 = run(["--manifest", str(man2), "--mods-root", str(dr),
              "--receipt-csv", str(rec_csv), "--receipt-md", str(rec_md)])
    out6 = r6.stdout
    check("drift STATE_DRIFT_CONFLICT>=1", "STATE_DRIFT_CONFLICT=1" in out6, out6)
    check("drift DRY_RUN: FAIL", "DRY_RUN: FAIL" in out6, out6)
    check("drift rc==3", r6.returncode == 3, f"rc={r6.returncode}\n{out6}")

    print(f"\nHARNESS {'ALL PASS' if not fails else 'FAIL: ' + str(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
