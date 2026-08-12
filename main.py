#!/usr/bin/env python3
"""
sims4-translator — Sims 4 动作/动画包自动汉化工具
Phase 1: Scanner v0.1 (只扫描, 只分类, 只提取显示文本, 修改文件数=0)

架构原则:
  - 核心分类引擎不依赖具体 DBPF 实现 (通过 IPackageBackend 抽象层)
  - Resource Type ID 集中在 resource_types.py, 禁止散落 magic numbers
  - 无法确认 = SKIP (FALSE NEGATIVE OK, FALSE POSITIVE NOT OK)
"""

import argparse
import os
import sys
import time
from pathlib import Path

# 项目根 = main.py 所在目录; src 在其下
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
# 确保从项目根运行
os.chdir(ROOT)

from scanner import Scanner
from config import load_config


def main():
    parser = argparse.ArgumentParser(
        description="Sims 4 动作/动画包汉化工具 — Phase 1 Scanner v0.1 (只读)"
    )
    parser.add_argument(
        "--mods", "-m",
        help="Sims 4 Mods 文件夹路径 (默认从 config.yaml 读取)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry Run 模式 (默认开启): 只扫描分类提取, 不翻译不修改不备份",
    )
    parser.add_argument(
        "--config", "-c",
        default=str(ROOT / "config.yaml"),
        help="配置文件路径 (默认: config.yaml)",
    )
    parser.add_argument(
        "--output", "-o",
        help="输出目录 (默认: output/)",
    )
    parser.add_argument(
        "--force-rescan",
        action="store_true",
        help="忽略增量缓存, 强制全量重扫",
    )
    args = parser.parse_args()

    # 加载配置
    cfg = load_config(args.config)
    mods_path = args.mods or cfg.get("mods_path") or cfg.get("mods_root")
    if not mods_path:
        print("[错误] 未指定 Mods 路径。用 --mods 传入, 或在 config.yaml 设置 mods_path。")
        sys.exit(1)

    mods_path = Path(mods_path).expanduser()
    if not mods_path.is_dir():
        print(f"[错误] Mods 路径不存在: {mods_path}")
        sys.exit(1)

    output_dir = Path(args.output or cfg.get("output_dir") or (ROOT / "output")).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Scanner 强制只读 — 本版本永远为 dry-run
    scanner = Scanner(
        mods_path=mods_path,
        output_dir=output_dir,
        cfg=cfg,
        dry_run=True,  # Phase 1 硬编码 Dry Run
        force_rescan=args.force_rescan,
    )

    start = time.time()
    try:
        stats = scanner.run()
    except KeyboardInterrupt:
        print("\n[中断] 扫描已停止。")
        sys.exit(130)

    elapsed = time.time() - start
    print(f"\n总耗时: {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
