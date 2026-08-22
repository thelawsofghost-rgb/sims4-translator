# 交接文档 — TEST C 大型 WW 动画包 sidecar canary（给新的 Dorothy）

> 生成时间：2026-08-22（GMT+8）
> Git HEAD：`d4d21884b7b11b7dfadfc3a84652561fd939c5be`（= origin/main，tree clean，PUSH_OK）
> 上一任 Dorothy 上下文用尽，本文件用于让新的 Dorothy 无缝接上。**先读本文件再动手。**
> 说明：本工作区 memory 索引服务暂停（embedding provider 变更），**不要依赖 memory_search**；
> 一切以 git history + 脚本内容 + 本文件为准（三者一致、互相印证）。

---

## 1. 一句话任务

在【不修改源包 / 不改架构 / 不部署】的前提下，为一个大型 WickedWhims 动画包
`WW_Nevely42_Animations.package`（**479 个 animation entry，1 个 WW XML**）生成一个只改
**3 个动画显示名**的独立 exact-TGI sidecar 包，作为真人验证（canary）用的安全补丁。

这是一个**纯研究/验证**任务：所有产物都是**只读分析工具 + 独立 sidecar 生成脚本**。
**绝不写 Mods、绝不改源 package、绝不 production 部署**，直到 Dorothy 审阅真人静态报告后批准。

---

## 2. 核心文件（都在 `sims4-translator/scripts/`，全部 committed + pushed）

| 文件 | 作用 | 状态 |
|------|------|------|
| `ww_animation_canary_builder.py` | 底层 DBPF 2.1 只读解析/写包（TGI 读取、entry meta、decompress、`build_package`）。**架构基石，勿动** | ✅ 稳定 |
| `ww_animation_sidecar_testc.py` | **TEST C 主线 writer**：生成 479-entry 包的唯一 WW XML sidecar，只改 3 个 display | ✅ 主线 |
| `ww_animation_testc_forensic.py` | 取证模块：验证 sidecar 是否能在不破坏源语义下补丁回源 | ✅ 稳定 |
| `ww_animation_testc_locator.py` | **只读**定位器：dump 目标 entry 元数据 + 中文摘要 + 游戏内定位指南 + easy 候选 | ✅ 稳定 |
| `ww_animation_sidecar_testb.py` | TEST B（小型/单 XML 版，先导验证） | ✅ 已完成 |
| `ww_animation_corpus_survey_v1.py` / `_triage_v1.py` | 全库只读扫描 + 分流审计 | ✅ 已完成 |

**只改 `ww_animation_sidecar_testc.py`（target 集/目录/报告键可参数化）。其余脚本保持只读、不再改动。**

---

## 3. 真实源包关键常量（勿硬编码到别处，脚本内 `SH_EXPECTED` 已锁）

- 源路径（真机 Windows）：
  `C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods\2026.7.20\WW_Nevely42_Animations.package`
- **SHA256 = `cd0093f2ec4b896121fa465672584c12384465b631c1d9128fe97d360b87d416`**（`SH_EXPECTED`）
- WW XML TGI：**type `0x7DF2169C`**，group `0x00000000`，instance `0xB3390ED6606C10E7`
- DBPF **2.1**；WW XML 是唯一一个 `0x7DF2169C`（`WW_XML_COUNT=1`）
- animation entry：**479 个**（`EXACT_ONE=479`，`SECOND_CANARY_PREFLIGHT_PASS=YES`）

---

## 4. 工作模式 / 铁律（沿用至今，务必继续遵守）

1. **ZERO_WRITE_TO_MODS = YES**：不写 Mods、不改源 package、不 auto-deploy、不 production、不翻译。
2. **只读对待 source**：所有工具只从源包读取；唯一可写的产物是 out_dir 里的 sidecar `.package`。
3. **display 名必须从源读取（`sv_src[ordinal]`），绝不硬编码动画名**。
4. **fail-closed**：结构不符合预期（WW XML ≠ 1、entry ≠ 479、meta 数不匹配等）→ 报错退出，不生成。
5. **source-faithful writer 不变量**（TEST C 区别于普通 writer 的关键）：
   - DBPF minor 2.1 / header_comp 从源沿用；
   - **high bits（offset/size 高位）、field8（comp_type）从源保留**；
   - **field7（mem_size）= 新解压后的实际长度**（不得沿用旧值；这是之前修过的语义 bug）；
   - **stored size = 实际压缩后长度**；
   - `ALL_RESOURCE_RANGES_VALID`、`PARSER_VALID`、`SOURCE_FILE_BYTES_UNCHANGED`、`SOURCE_SHA_VERIFIED` 全过才 PASS。
6. **语义差异按 ordinal 逐条配对**（`_entry_paired_semantic_diff`），不做 path collapsing。
7. **writer 修复史（重要，勿回退）**：旧 writer 用首 path-match 法在原 XML 上做全局替换，丢了 entry 间 `\n\t` 空白，导致 479 项 tail whitespace 全变。已用 `_entry_partition()` **覆盖分区 splice**（gap/entry 段交替、字节保留）修复。
8. **不要削弱 validator**：当初 `INTERNAL_XML_SEMANTIC_DIFF_COUNT=479` 是 **writer 的 bug**（丢空白），validator 是对的。取证模块是 **只读** 的。
9. 中文映射**只允许来自 XML 明确枚举值**（BED=床、SOFA=沙发、FLOOR=地板、VAGINAL=阴道性交、ANAL=肛交、ORAL=口交、HANDJOB=手交、TEASING=挑逗、CLIMAX=高潮、MASTURBATION=自慰…）；无证据 = `UNKNOWN`；custom numeric ID = `CUSTOM_LOCATION_NAME=UNRESOLVED`（不猜家具）。

---

## 5. 当前进度（决策树）

### 已定论（勿重开）
- **根因 = WRITER ARTIFACT，不是 validator bug**。真实 Windows 结果一度 `INTERNAL_XML_SEMANTIC_DIFF_COUNT=479`（全 entry tail 空白丢失），根治于 writer 的 `_entry_partition()` 修复（commit `d8c348b`）。
- **TEST C 三个原始 target（ordinal 0/239/478）不适合真人验证**（由 locator 实证）：
  - 0 「Gearshift 1」：`custom_location=1743150887` → `HUMAN_CANARY_USABILITY=POOR`
  - 239 「Nice Ride Handsome 5」：`custom_location=1743150887` → `POOR`
  - 478 「Positive Vibrations 5」：`GOOD` 但 `location=WALL,WINDOW`，非首批普通菜单。
- 因此只做 **TEST C EASY 真人验证版**（换 target 集，**不改 writer/validator/sidecar 机制**）。

### TEST C EASY（当前焦点，commit `d4d2188`）
`ww_animation_sidecar_testc.py` 新增 `--easy` 变体。**target 集 = EASY_TARGETS：**

| 标记 | ordinal | OLD_DISPLAY（源读） | NEW_DISPLAY | location/category/actors |
|------|---------|--------------------|-------------|--------------------------|
| EASY_C1 | 35 | `Go To Sleep TWO SIMS 1` | `【大包EASY_C1】Go To Sleep TWO SIMS 1` | SOFA / HANDJOB / 2 |
| EASY_C2 | 36 | `Go To Sleep TWO SIMS 2` | `【大包EASY_C2】Go To Sleep TWO SIMS 2` | SOFA / VAGINAL / 2 |
| EASY_C3 | 82 | `You Belong To Me 1` | `【大包EASY_C3】You Belong To Me 1` | FLOOR / TEASING / 2 |

- 输出目录：`output\ww_animation_testc_easy_nevely42\`，产物名 `WW_Nevely42_Animations_SIDECAR_TESTC.package`
- 报告 pass 键 = `TEST_C_EASY_STATIC_PASS`；原版仍保留 `C_TARGETS(0/239/478)` + `TEST_C_LARGE_PACKAGE_STATIC_PASS`，`ORIGINAL` mode 回归通过。
- 白盒 fixture 端到端已验证：`TEST_C_EASY_STATIC_PASS=YES`、`DISPLAY_SEMANTIC_DIFF_COUNT=3`、`UNCHANGED_DISPLAY_ENTRY_COUNT=476`、`UNCHANGED_DISPLAY_ENTRIES_EQUAL=YES`、`INTERNAL_XML_SEMANTIC_DIFF_COUNT=0`、`INTERNAL_DIFFS=none`、`SOURCE_FILE_BYTES_UNCHANGED=YES`、`ZERO_WRITE_TO_MODS=YES`。
- 真实 sidecar **尚未在 Windows 生成**（白盒产物已删/不入库，`output/` 在 .gitignore）。

---

## 6. 下一步（等真人验证，未部署）

### ① 真机生成 TEST C EASY sidecar（Windows PowerShell，不部署）
```powershell
cd <repo>\sims4-translator
python scripts\ww_animation_sidecar_testc.py --source "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods\2026.7.20\WW_Nevely42_Animations.package" --easy --force
```
预期：`TEST_C_EASY_STATIC_PASS=YES`、`INTERNAL_XML_SEMANTIC_DIFF_COUNT=0`。

### ② 真机取证实录（取证模块，可选/复核用）
```powershell
python scripts\ww_animation_testc_forensic.py --source "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods\2026.7.20\WW_Nevely42_Animations.package" --sidecar output\ww_animation_testc_easy_nevely42\WW_Nevely42_Animations_SIDECAR_TESTC.package
```
预期：`PATCHED_BACK_BYTES_EQUAL_SOURCE=YES`（证明 sidecar 与源同语义，唯一差异是 3 个 display）。

### ③ 真机定位辅助（只读，可选）
```powershell
python scripts\ww_animation_testc_locator.py --source "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods\2026.7.20\WW_Nevely42_Animations.package"
```

### ④ 真人进游戏验证
核对 SOFA（沙发）与 FLOOR（地板）普通菜单中能否搜到并显示
`【大包EASY_C1】Go To Sleep TWO SIMS 1`、`【大包EASY_C2】Go To Sleep TWO SIMS 2`、
`【大包EASY_C3】You Belong To Me 1`。

### ⑤ 部署（仅当 Dorothy 批准）
- 由你（Dorothy）审阅真人静态报告 + 真人实测结果。
- 批准后才生成/放置 `WW_Nevely42_Animations_CHS_TESTC.package` 类产物（当前**未做**）。

---

## 7. 复现/验证命令（白盒，不依赖真机）

真实源 Windows 包在 Linux 上不可得，我们一贯用**白盒 fixture**（479-entry、目标 display 名固定）跑全流程。
历史写法见 `/tmp` 曾用 `gen.py`（已被清理），做法是：用 `wb.build_package` 写一个 479-entry 的
`WW_Nevely42_Animations.package`，把 fixture SHA 经 `--expected-sha` 传入 writer，跑 `--easy` → 全 PASS。
如需复刻 fixture 生成器，规则：
- 479 个 `<U>` entry，每个含 `animation_raw_display_name / locations / custom_locations / category / tags / loops / allowed_for_random` + `<L n="actors_list">` 内 2 个 `<U>` actor（`actor_id / clip_name / animation_type / animation_genders`）。
- ordinal 35/36/82 的 display 名必须分别是上面 EASY 三行；其余可任意填充。
- 单 WW XML，TGI `0x7DF2169C/0x00000000/0xB3390ED6606C10E7`，DBPF 2.1，comp zlib(`0x5A42`)。

---

## 8. 交接后 Dorothy 应做的第一件事

1. 读 `scripts/ww_animation_sidecar_testc.py` 的 `run()` + `_entry_partition()` + 报告块，确认不变量（第 4 节 5~8 点）已实现。
2. 跑 `python scripts/ww_animation_sidecar_testc.py -h` 看 `--easy/--out-dir/--expected-sha`。
3. 若需要真机白盒复现，按第 7 节重建 fixture 并全 PASS。
4. **不要**重开「validator vs writer」之争（已定论 = writer artifact）；**不要**削弱 validator。
5. 任何「部署 / 写 Mods / 触源」动作**必须先经真人批准**，符合 `ZERO_WRITE_TO_MODS=YES`。

---

## 9. 最近提交（sha 前 7 位）

```
d4d2188  TEST C EASY 真人验证版 (--easy) — 不改架构          ← HEAD
c742254  TEST C LOCATOR + GAME SEARCH GUIDE + EASY CANARY CANDIDATES
3d2cd42  TEST C HUMAN-READABLE LOCATOR REPORT (只读)
d8c348b  TEST C writer 丢 whitespace 修复(覆盖分区) + 取证模块
90feac2  TEST C 大型 479-entry exact-TGI WW XML sidecar canary
7364236  WW Animation Corpus Survey V1.1 TRIAGE — 只读分流审计
a2c6dc6  WW Animation Corpus Survey V1 — 全库只读扫描
f8b697a  TEST B 独立 exact-TGI XML sidecar 生成器
0703f0c  DISPLAY_SEMANTIC_DIFF_COUNT 计数 bug 修复 + REVALIDATE
baa2975  CANARY_SOURCE_FAITHFUL internal 判定改 semantic tree diff
e74aeae  field7 语义修正 — 修改后 XML 的 field7/mem_size=新解压长度
1bf0574  CANARY_SOURCE_FAITHFUL — 只改目标 WW XML 一个 display
```
