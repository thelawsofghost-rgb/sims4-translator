# TEST C EASY — 真人验证失败诊断（只读分析，不改 writer）

> 日期：2026-08-24；状态：Dorothy 已确认 writer PASS / sidecar 生成成功 / sidecar 已在 Mods /
> 游戏能看到 source animation / 但游戏显示仍为 OLD display_name。
> 本报告**只读诊断**：不改 writer、不改 validator、不重设计。目标=判定失败属于 A/B/C/D。

---

## 1. 先回答 Dorothy Q1：sidecar 内容正确性（可通过构造验证）

writer 的 sidecar 生成是**字节保真覆盖拼接**：
- `_entry_partition()` 把 `animations_list` 内部文本切成「entry <U> 段 + 段间空白」的覆盖分割；
- 仅对目标 ordinal 的 `animation_raw_display_name` 节点文本做替换（`_replace_display_in_block`，逐 ordinal 精确定位，禁全局替换）；
- 新 body = `compress_like(原 body)` 重压缩，**TGI 与源完全一致**（同一 type/group/instance）；
- sidecar = **同一个 WW XML TGI 下、仅 3 处 display 文本不同、字节其余全保留** 的 1-resource 包。

Gate 已固化为报告项（真机/白盒一致）：
- `SIDECAR_RESOURCE_COUNT=1`
- `SOURCE_WW_XML_TGI_EQUAL_SIDECAR=YES`（duplicate-TGI 设计本身）
- `DISPLAY_SEMANTIC_DIFF_COUNT=3` / `UNCHANGED_DISPLAY_ENTRY_COUNT=476` / `INTERNAL_XML_SEMANTIC_DIFF_COUNT=0`
- `PARSER_VALID=YES`、`SOURCE_SHA_VERIFIED=YES`

> **结论：sidecar 内容层面正确——问题不在 sidecar 里 3 个 display 没改，而在游戏/WW 加载层。**
> 这正是 Dorothy 的怀疑方向。

---

## 2. Sims 4 / WW 资源 override 机制（事实 + 假设分开标注）

### 2.1 Sims 4 DBPF 资源的「同 TGI 跨包」行为
- **事实**：Sims 4 的资源管理器按 `(type, group, instance)` 索引资源；当 Mods 下多个 `.package` 存在**同 TGI** 资源时，游戏选择**其中一个**进入内存 —— 通常 **load-order 决定谁生效**（后加载者覆盖/被覆盖）。这是 S4PE 生态里“override 包”能工作的基础。
- **事实**：WW 的动画数据是 `0x7DF2169C` XML（`WickedWhimsAnimationData`），display 名直接读 XML 里的 `animation_raw_display_name` 文本，**没有 locale 位机制**（不同于 STBL `0x220557DA` 靠 instance 高字节区分 CHS/CHT 的做法）。所以「改动一个同 TGI 的 XML」要让游戏用新版，只能靠**资源覆盖生效**，没有别的通道。
- **关键区分（本项目 STBL 汉化 vs 本 TEST C）**：
  - 生产汉化(STBL)：sidecar 用**不同 locale instance**（如 CHS `0x...01` / 源 EN `0x...00`），两者**并存不冲突**，游戏按 locale 选。这能工作，是因为 STBL 天生多 locale。
  - TEST C(WW XML)：sidecar 用**同 TGI**（`0x7DF2169C/0x00000000/0xB3390ED6606C10E7`）**覆盖**源。这是**纯覆盖语义**——依赖 load order / 重复键解析，**不是** STBL 那种并存机制。

### 2.2 为什么会「看到 source animation 但显示旧名」
- 游戏加载了 **source 包的该 XML**（覆盖没生效）→ 显示源里的 OLD 名；sidecar 里的 NEW 名没被采用。→ 落在 **A(load order) 或 B(同 TGI 不覆盖)**。
- 或 WW 用自己的**动画缓存/导入态**（`animation data` 解析后缓存）→ 即使 XML 更新，WW 仍用旧的 display → **C(WW 缓存)**。
- 或 WW 压根**不加载 sidecar 的这个 XML**（结构被判定不受支持/被忽略）→ **D(sidecar 被游戏拒绝)**。
- 「能看到 source animation」说明 WW 至少加载了该包族，但不能区分到底加载了谁的 XML。

---

## 3. 最小验证方案（Dorothy 的 A/B/C，全部只读/可逆，不动 writer）

### A. `zzz_` 重命名调 load order（测 A）
目标：让 sidecar **最后加载**，从而若 override 是 load-order 决定，则生效。
- 把 sidecar 包重命名为类似 `zzz_TESTC_EASY.package`（字母序靠后 → 通常更晚加载）。
- 观察是否显示 `【大包EASY_C3】...`。
- 判定：
  - 改名后显示 NEW → **A (load order)**，非设计缺陷。
  - 改名后仍 OLD → A 排除，进入 B/C/D。

### B. 临时移除 source、仅留 sidecar（测 B 的极端版 + 测 D）
- 临时把 source 包移出 Mods（备份到 Mods 外），只留 sidecar。
- 若**动画仍存在**且显示 NEW → sidecar 被 WW 正常加载、内容可识别 → **B 排除 + D 排除**（此时纯 load-order A 的极端验证：无源可覆盖，只剩 sidecar）。
- 若**动画消失** → WW 依赖 source 包里的其它资源（如 clip/authoring），说明仅靠这个 XML sidecar 不足以独立呈现动画 → **D（结构/配套不足）或机制不支持独立 sidecar**。
- 若动画仍在但显示 OLD → 很反常（说明 WW 缓存/不是从 XML 实时读）→ 指向 **C**。
- ⚠️ 完成测试后**立刻恢复 source**（纯只读验证，不长期改动 Mods）。

### C. WW 缓存（测 C）
- 若 A、B 都不能解释，检查 WW 是否有动画数据缓存（删 WW 缓存/重新导入动画数据）。
- 更简单先试：**重启游戏** + 确保 WW 完整 `re-scan/import`（新版 WW 常需重新导入动画包）。
- 判定：清理/重导后显示 NEW → C。

### D. 结构是否被游戏接受（测 D，辅证）
- 确认 sidecar 的 WW XML 根结构 == source（writer 保真，应 << I c="WickedWhimsAnimationData" >> 一致）；
- 确认 sidecar 包 DBPF 版本/压缩标志与 source 相仿（writer 已保真，`DBPF_VERSION_EQUAL=YES`）；
- B 的「动画消失」会强证 D。

---

## 4. 判定框架（A/B/C/D 小结表）

| 实验结果 | 判定 |
|----------|------|
| zzz_ 改名后显示 NEW | **A load order**（最常见，修复=命名或确认加载序） |
| 移除 source 仅 sidecar → 动画在+显示 NEW | B 排除、D 排除、A 排除（纯 sidecar 生效） |
| 移除 source 仅 sidecar → 动画消失 | **D**（sidecar 独立不被接受，或 WW 需配套资源） |
| 移除 source 仅 sidecar → 动画在但仍 OLD | **C**（WW 缓存/非 XML 实时读，需重导动画数据） |
| 以上都 NEW 不了且改名无效 | **B 或机制上同 TGI 覆盖不被 WW 采纳** → 需换 override 思路（见下） |

---

## 5. 若证明是 B（同 TGI 不覆盖）或 D（结构不被接受）→ 最小 override 思路（供决策，非本次改动）

> 遵循「不重设计 writer、不修改 writer」约束，以下仅作**分析建议**，默认不实施。

- **思路 1（最可能被 WW 采纳）：直接对 source 包做原位改 XML，而不是另建 sidecar。**
  WW 对 `0x7DF2169C` XML 的加载若按“每个包各自解析、不跨包替换同 TGI”，则**同一 TGI 的第二个包根本没机会**——只有原位改源 XML 一定被读到。
  这与 TEST C「只产 sidecar、源只读」的约束冲突 → 需 Dorothy 决策是否放宽（部署时才允许写入 source 副本）。
- **思路 2：确认 WW 是否有「覆盖同名 XML」的自带机制**（部分 WW 版本对 animation 包有特定解析，可能按文件名/包内唯一 TGI 直接覆盖）。
- **思路 3：改用 STBL 汉化通道**（若 WW 的 display 名实为 STBL key 而非 XML 文本——需真机确证；但 TEST C 解析的是 XML `animation_raw_display_name`，故大概率不适用）。

---

## 6. 给 Dorothy 的下一步（只读，先做 A、再 B，都记录结果）

1. 先做 **A**：sidecar 改名 `zzz_TESTC_EASY.package`，进游戏看 C3 是否显示 `【大包EASY_C3】You Belong To Me 1`。
2. 再做 **B**：临时移 source（备份），仅留 sidecar，看动画是否在、显示 NEW/OLD/消失。
3. 回报三行结果：`A改名后显示=NEW|OLD`、`B仅sidecar=动画在|消失`、`B显示=NEW|OLD`。
4. 我据此落 A/B/C/D 判定；若落 D/B，再讨论 override 思路（不碰 writer）。

---

## 状态
- ✅ 只读分析：未改 writer/validator、未生成新 sidecar、未写 Mods。
- ✅ 确认 sidecar 内容正确（TGI 同、仅 3 处 display 差、零 internal diff）→ 问题定位到游戏/WW 加载层。
- ⏳ 待 Dorothy 跑 A/B 实验后落判定。
