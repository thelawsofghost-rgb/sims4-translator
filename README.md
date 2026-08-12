# Sims 4 动作/动画包自动汉化工具 — Phase 1 Scanner v0.1

> 只处理动作/动画包。只翻玩家真正看到的动作文字。其他 MOD 零修改。
> 程序内部字段零修改。无法确认 = SKIP。宁可漏翻，不可误改。
> **Phase 1 只扫描、只分类、只提取，修改文件数 = 0。**

## 这是什么

精准识别 **Pose Player 动作包** 和 **WickedWhims 动画包**，为 Phase 2/3 的汉化做准备。
当前 V1 是 **Scanner（只读）**，绝不修改任何 `.package`。

## 目录结构

```
sims4-translator/
├── main.py                  # 命令行入口
├── config.yaml.example      # 配置模板 (复制为 config.yaml)
├── src/
│   ├── config.py            # 配置加载
│   ├── dbpf_fast.py         # Fast DBPF 只读索引解析器 (浅扫描核心)
│   ├── backend.py           # Package 读取抽象层 (后端可替换)
│   ├── classifier.py        # 分类引擎 (CONFIRMED_WW/POSE/UNCERTAIN 等)
│   ├── text_extractor.py    # 显示文本提取器 (WW/Pose 名称)
│   ├── resource_types.py    # Resource Type ID 集中映射表 (待核实清单)
│   ├── cache.py             # 增量扫描缓存 (SQLite)
│   └── scanner.py           # 扫描编排器
├── scripts/
│   └── verify_index.py      # FastIndexReader 交叉验证测试
├── lib/s4pi_src/            # 参考用 s4pi 源码 (Type ID 已核实来源之一)
└── output/                  # 报告输出 (运行时生成)
```

## 安装依赖

```bash
pip install pyyaml
```

## 配置 Mods 路径

```bash
cp config.yaml.example config.yaml
# 编辑 config.yaml, 填入 mods_path
```

或运行时用 `--mods` 参数：

```bash
python main.py --mods "C:/Users/Name/Documents/Electronic Arts/The Sims 4/Mods"
```

## 运行 (Dry Run)

Phase 1 永远是 Dry Run，不翻译、不修改、不备份：

```bash
# 方式1: 用 config.yaml
python main.py

# 方式2: 指定 Mods 路径
python main.py --mods "C:/.../Mods"

# 强制全量重扫 (忽略增量缓存)
python main.py --force-rescan
```

## 输出

运行后在 `output/` 生成：

| 文件 | 内容 |
|---|---|
| `scan_report.csv` | 每个 package 的分类结果、证据、文本统计 |
| `summary.txt` | 总体统计 (Confirmed/Uncertain/忽略 数量) |
| `classification_evidence.txt` | 每个动作包的分类判定依据 |
| `performance_report.txt` | 两阶段扫描耗时 |
| `errors.log` | 解析错误 (不会中断) |
| `mod_index_cache.db` | 增量缓存 (下次只扫变更) |

## 运行环境检测

在 Y9000P 上运行：

```bash
python --version
pip --version
dotnet --info
```

根据结果决定后续后端 (纯 Python 只读 或 C#/s4pi 写回)。

## 验证浅扫描器

```bash
python scripts/verify_index.py "C:/.../某个动作包.package"
python scripts/verify_index.py "C:/.../Mods"   # 批量
```

比对 FastIndexReader 读出的 Resource Count/Type/Group/Instance/Size 与参考实现，
**全部一致后才认为浅扫描器可靠**。

## Resource Type ID 状态

⚠️ 部分 Type ID 尚未从权威来源核实 (见 `src/resource_types.py`)。
**未核实的 Type ID 不会参与判定**，涉及它们的资源会进入 UNCERTAIN 而不是瞎猜。
核实后才能启用对应分类逻辑。

## 当前明确不做

- 不汉化衣服/家具/功能 MOD
- 不修改任何 package
- 不做缩略图修复 / AI看图 / MOD管理 / 重复检测 / UI / 自动下载
- 不解析 CLIP 动画内容 (只判断存在性)
- 不计算全文件 hash

## 核心原则

```
只处理动作/动画包。
只翻玩家真正看到的动作文字。
其他 MOD 零修改。
程序内部字段零修改。
无法确认 = SKIP。
宁可漏翻，不可误改。
所有操作必须可追踪、可统计、可验证。
第一次只扫描，不修改。
```
