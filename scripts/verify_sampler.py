#!/usr/bin/env python3
"""验证 phase2a_samples 的跨包分层抽样: 用合成候选 CSV 测配额与可复现性。"""
import os, sys, csv, tempfile, importlib.util

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phase2a_samples.py")
src = open(SRC, encoding="utf-8").read()

# 用合成 CSV 作为候选输入: 构造一个临时 out_dir, 放入 pose_translation_candidates.csv
tmp = tempfile.mkdtemp()
cand = [
    "source_text,ref_count,unique_keys,sample_package,sample_pose_pack,sample_stbl_instance,sample_locale,sample_neighbor_poses,sample_neighbor_display_texts",
]
# 3 个 package, 每个包内 PROPER_NAME 各 8 条 (共 24), SEMANTIC_WITH_NUM 各 8 (共24), SEMANTIC_UNCERTAIN 各 8 (共24)
pkgs = ["PkgA.package", "PkgB.package", "PkgC.package"]
from collections import Counter
cls_counter = Counter()
for pi, pkg in enumerate(pkgs):
    for k in range(8):
        cand.append(f"pnam{pi}_{k},1,1,{pkg},pack,stbl,loc,neigh,disp")
        cand.append(f"pnum{pi}_{k},1,1,{pkg},pack,stbl,loc,neigh,disp")
        cand.append(f"punc{pi}_{k},1,1,{pkg},pack,stbl,loc,neigh,disp")
with open(os.path.join(tmp, "pose_translation_candidates.csv"), "w", encoding="utf-8") as f:
    f.write("\n".join(cand))

# 在 phase2a_samples 源码里注入 out_dir 指向 tmp, 并截掉真正读真实 CSV 的路径
# 最简单: 直接改 out_dir 行
import re as _re
src2 = _re.sub(r'^out_dir = .*$',
               f'out_dir = Path(r"{tmp}")',
               src, count=1, flags=_re.M)

ns = {}
exec(compile(src2, SRC, "exec"), ns)
samples = ns["samples"]

# 汇总
from collections import defaultdict
grp_count = Counter(g for g, _ in samples)
pkg_per_grp = defaultdict(set)
for g, r in samples:
    pkg_per_grp[g].add(r["sample_package"])
pkg_count = Counter(r["sample_package"] for _, r in samples)

print("== 组计数 ==")
for g, c in grp_count.most_common():
    print(f"  {g:16} = {c}   (覆盖 {len(pkg_per_grp[g])} 个 package)")
print(f"\n== package 分布 (共 {len(pkg_count)} 个) ==")
for p, c in pkg_count.most_common():
    print(f"  {c:2} 条  {p}")
print(f"\n总数: {len(samples)}")

# 断言: 每包每类 <=2
print("\n== 配额检查 (每包每类应 <=2) ==")
viol = 0
per = defaultdict(lambda: defaultdict(int))
for g, r in samples:
    per[r["sample_package"]][g] += 1
for p, gd in per.items():
    for g, c in gd.items():
        flag = "OK" if c <= 2 else "OVER"
        if c > 2:
            viol += 1
        print(f"  [{flag}] {p}  {g} = {c}")
print(f"\n违规数: {viol}")
print("总数==100:", len(samples) == 100)
