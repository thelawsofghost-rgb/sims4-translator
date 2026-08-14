# 待办 & 已知问题

## P0: Resource Type ID 权威核实 (阻塞因素)

CLIP (0x0354E541) 和 SNIPPET (0x052FE820) 尚未从权威来源核实。
当前它们标记为 UNVERIFIED，因此**不会参与判定** — 这是有意为之的安全设计
(FALSE POSITIVE NOT ACCEPTABLE: 未核实就拒绝判定, 而不是瞎猜)。

**在核实之前, Scanner 对 WW 动画包/Pose 包只能判 UNCERTAIN, 无法判 CONFIRMED。**

### 需要核实 Type ID (来源建议排行):
1. **Sims4Studio (S4S) 源码** — 最权威, 含 ClipResourceType / Snippet 定义
2. **s4pe (Sims 4 版) 源码** — GitHub s4ptacle/s4pe (注意与 s4pi-Sims3 区分)
3. **Sims 4 Studio 社区 wiki / DBPF 工具文档**

### 具体待核实项:
- [ ] `0x0354E541` 是否 = Sims4 CLIP (动画剪辑)
- [ ] `0x052FE820` 是否 = Snippet (Pose Pack 定义)
- [ ] `0x00B2D882` 到底是 Binary XML 还是 DDS 图片 (旧 s4pi 有冲突标记)
- [ ] `0x545AC6A4` 是否 = TTAB
- [ ] `0x73E93EEB` Sims4 中的含义

### 核实后操作:
1. 在 `src/resource_types.py` 对应条目设为 `verified=True` 并填 source
2. 重跑 `scripts/smoke_test_classifier.py` 确认功能逻辑正常
3. 用真实动作包跑 verify_index 交叉验证

## P1: Verify 交叉验证

- [ ] 用真实 WW 动画包 / Pose Pack package 跑 `scripts/verify_index.py`
- [ ] 与 Sims4Studio/s4pe 的 Resource 解读结果对比
- [ ] 确认 Shallow Scanner 读出的 count/type/group/instance/size 全部一致

## P2: 读取 size 字段

当前 FastIndexReader entry 只解析到 offset, size 字段在部分 DBPF 布局中和
offset 高位/相邻字段有关联, 需根据核实后的布局填上 size。

## P3: Phase 2 (翻译)

- [ ] 文本去重 + 批量翻译
- [ ] translation_cache.json
- [ ] 翻译报告

## Phase 3A: 中文 STBL locale 取证台账 (READ-ONLY, 不写 package)

### HARD FACT (已二进制/实测锁定, 不可推翻)
0. **Sims4 locale byte 官方确认 (CHS/CHT)**: `0x01 = 简体中文 CHS`, `0x02 = 繁体中文 CHT`
   (Dorothy 官方确认, 与 WW CHS 包 `0x01`/`0x02` 交叉验证一致)
   → 中文写回 = 修改 **locale 0x01** 的现有 STBL 实例 (Pose 包已预置全套 locale 变体):
   **已升级为 HARD FACT** (原 LIKELY: 已存在 18 变体 → 改现有而非新增)
1. **canonical STBL v5 parser** (`scripts/map_pose_texts.py::parse_stbl`) 已确认无 off-by-one;
   自写 probe 版本 off-by-one (offset 8 vs 7) 已删除, probe 改为复用 canonical。
   WW 权威验证: count 读 `<Q`@offset7 = 0x1A64 = **6756**。fail-fast 全过。
2. **WW CHS** (`WickedWhims_LP_CHS_*.package`):
   - locale `0x01` / `0x02` 两实例
   - 各 **6756 keys**, payload/内容 **identical** (均为中文)
   - inst: `012E440D74EB747B` / `022E440D74EB747B`
3. **Embracing Faces** 18 个 STBL locale 变体, 各 **5 keys**, 内容 identical (纯英文占位):
   locales = `0x00,0x01,0x02,0x03,0x04,0x05,0x06,0x07,0x08,0x0B,0x0C,0x0D,0x0E,0x0F,0x11,0x12,0x13,0x15`
   共享字符串块 `4EACCF17C8B091` (仅最高字节不同)
4. **simkatu** 18 个 STBL locale 变体, 各 **52 keys**, 内容 identical (纯英文占位), 共享块 `C53AB22366DA6C`
5. **Phase 3A 全程 READ-ONLY** — 不写 package, 不修改 Pose, 不硬编码 locale
6. STBL locale 码 = instance_id **最高 1 字节** (bits 56-63), 其余 56 位 = 共享字符串块 hash
   (`scripts/map_pose_texts.py::locale_of_stbl` 实测)
7. `LOCALE_BYTE_KNOWN` 保留为空 — 宁 UNKNOWN 不臆测语言名

### LIKELY (待官方 locale 映射确认后升级为 HARD FACT)
- 若官方 CHS locale byte **已存在**于 18 个 Pose STBL 变体中,
  则写回应**修改对应现有 STBL**, 而非新增资源。
  等官方 locale 映射确认后升级为 HARD FACT。

### PENDING (进行中, READ-ONLY)
- [ ] ~~扫描官方 Strings_CHS_CN/Strings_CHT_CN 包做交叉验证~~
      **已由 Dorothy 官方确认 locale 映射 (0x01=CHS, 0x02=CHT), 无需再扫描官包**

### Phase 3B (写回, 单条 MVP, 进行中)
- [x] `scripts/patch_stbl.py` MVP 已开发 + fixture 回归通过
      (CHS locale 0x01 只改目标 key; EN locale 0x00 / 其他 resource 原样; copy-on-write)
- [x] `--inspect` 只读模式 (列出 locale 0x01 STBL 全部 keyHash + TID)
- [ ] Windows 真包单条测试: Embracing Faces 改 1 字符串 → 游戏加载 + 显示中文
- [ ] 保留: 原包备份 / 未修改 resource / 其他 locale STBL (已验证)
- [ ] 暂不做: 全量 1968 / 全扫 Mods / 批处理
- [ ] 已知限制: DBPF index entry flags/reserved MVP 重建置 0 (原通常为 0)

---

## P4: Phase 3 (写回)

- [ ] package 备份
- [ ] 修改副本 + 写回
- [ ] 重新打开验证
- [ ] 正式输出汉化 package

## 明确不做 (规格允许范围外)
缩略图修复 / AI看图 / 动作包删除移动 / MOD整理 / 文件名汉化 / 衣服家具功能MOD汉化 / 重复检测 / UI / 自动下载
