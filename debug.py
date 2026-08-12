#!/usr/bin/env python3
"""临时调试 v2: 用 safe_parse 诊断真实 package 为何失败"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from dbpf_fast import safe_parse, UnsupportedDBPFError, FastIndexReader, DBPFError

p = "C:/Users/thela/Documents/Electronic Arts/The Sims 4/Mods/2026.4.28/WWLaserAnimations.package"

# 步骤1: 直接看文件头 (正确 seek 回 0)
with open(p, "rb") as f:
    f.seek(0)
    raw = f.read(32)
    print("文件头 32 字节 hex:", raw.hex())
    print("magic:", raw[0:4])

# 步骤2: 手调用 FastIndexReader, 抓具体异常
print("\n--- FastIndexReader 直接调用 ---")
try:
    with open(p, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        idx = FastIndexReader(f, size).read_index()
        print("成功, entries:", len(idx.entries))
except UnsupportedDBPFError as e:
    print("UnsupportedDBPFError:", e)
except DBPFError as e:
    print("DBPFError:", e)
except Exception as e:
    import traceback
    traceback.print_exc()

# 步骤3: safe_parse
print("\n--- safe_parse ---")
idx, err = safe_parse(p)
print("err:", err)
import dbpf_fast
print("S4S_LAST_ERROR:", dbpf_fast.S4S_LAST_ERROR)
