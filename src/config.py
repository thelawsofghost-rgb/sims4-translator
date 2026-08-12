#!/usr/bin/env python3
"""配置加载 — 支持 config.yaml + 环境变量覆盖, 无默认路径硬编码"""

import os
from pathlib import Path
from typing import Any, Dict

try:
    import yaml
except ImportError:
    yaml = None


def load_config(path: str | Path | None = None) -> Dict[str, Any]:
    """加载配置。路径未指定或文件不存在时返回空字典(交给调用方决定默认)。"""

    cfg: Dict[str, Any] = {}
    cfg_file = Path(path) if path else None

    if cfg_file and cfg_file.exists():
        try:
            if yaml is None:
                raise RuntimeError("缺少 PyYAML: pip install pyyaml")
            with open(cfg_file, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                if isinstance(data, dict):
                    cfg.update(data)
        except Exception as e:
            print(f"[警告] 配置文件读取失败 {cfg_file}: {e}")

    # 环境变量覆盖 (前缀 SIMS4_)
    for key, val in os.environ.items():
        if key.startswith("SIMS4_"):
            cfg[key[6:].lower()] = val

    return cfg
