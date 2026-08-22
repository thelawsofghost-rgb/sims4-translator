# TEST C EASY — 候选定位：真人截图 → entry 映射（给 Dorothy 的交接补充）

> 日期：2026-08-22
> 结论先行：**在本 Linux 环境无法完成真实截图→entry 映射**（真实 479-entry 包只在 Windows，
> 本地仅有合成白盒 fixture，其 display 名为虚构，**不含**截图动画）。为避免造假，我不在
> fixture 上硬凑匹配。下面是「如何在真机拿到映射清单」的命令 + 候选表模板 + 我已写好的
> 3 个最优目标选择标准，供 Dorothy 拿到清单后一键套用。

---

## 0. 为什么不能直接给「候选表」

Dorothy 列的 Bed/Floor/Sofa 动画（如 `MF Simple FootJob`、`Pounded standing | Rough doggy`、
`Passionate loveseat | Cowgirl`）都是**真人游戏内显示名**。要映射到 entry，必须拿真实包的
479 条 `animation_raw_display_name` 全集与它们比对。

但：
- **真实包不在本机**（`C:\Users\thela\...\WW_Nevely42_Animations.package` 只在 Windows）；
- 本地 `output/ww_animation_testc_easy_nevely42/*.package` 是**合成白盒 fixture**，名称全是我虚构
  （`Bedtime Story`、`Couch Cuddle`、`Desk Duty`…），**不含**截图动画名；
- 拿 fixture 冒充真实映射 = 造假，违反「display 名必须从源读、不硬编码、不猜」。

**因此正确顺序是：先在真机导出真实 display 全集 → 再匹配 → 再选 3 个。** 我不能跳过第一步。

---

## 1. 真机导出「游戏显示名 → entry/TGI → source 位置」全集（只读）

工具：`scripts/ww_animation_corpus_survey_v1.py`（**只读**，已把每个 entry 的精确
`animation_raw_display_name`、TGI、source 路径、entry 位置提取为 CSV）。

Windows PowerShell（在仓库根 `sims4-translator\`）：
```powershell
python scripts\ww_animation_corpus_survey_v1.py `
  --mods-root "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods\2026.7.20" `
  --out-dir output\ww_animation_corpus_v1
```
产物：`output\ww_animation_corpus_v1\*.csv`，每行一个 entry，含
`package_path / tgi(type,group,instance) / animation_raw_display_name / entry 位置 / 其他字段`。
跑完把该 CSV 发我，我就能精确建「显示名 → entry/TGI → source 位置」映射并输出候选表。

> 若想要更聚焦的 raw 定位 dump，可用只读 locator：
> `python scripts\ww_animation_testc_locator.py --source "<真实包路径>"`（现支持 `--easy-top`），
> 但它默认只打 3 个 target + easy 候选，不全量。survey 是拿「全集」的正确入口。

---

## 2. 候选表模板（拿到真实清单后我按此输出）

Dorothy 要的字段，逐行：

| 字段 | 含义/来源 |
|------|-----------|
| `category` | `animation_category`（VAGINAL/ANAL/ORAL/HANDJOB/TEASING…，仅 XML 值） |
| `display_name` | `animation_raw_display_name`（源自真实包） |
| `source_entry` | 该 entry 在 `animations_list` 中的 ordinal（0-based）+ 定位坐标 |
| `instance` | WW XML 的 instance id（`0xB3390ED6606C10E7`，所有 entry 共用同一 WW XML） |
| `TGI` | `0x7DF2169C / 0x00000000 / 0xB3390ED6606C10E7` |
| `是否存在于479-entry` | YES/NO（比对全集） |
| `是否适合作为TEST C EASY` | 按第 3 节标准 YES/NO + 理由 |

---

## 3. 3 个最优目标选择标准（Dorothy 要求的三个条件，已固化）

对每个命中截图名的真实 entry，判「最适合作为 TEST C EASY」：

1. **游戏内名字唯一**：479 条中该 `display_name` 只出现 1 次（`_display_values` 去重计数 == 1）。
   - 同名多次出现（序列段 `X 2`/`X 3`…）→ 排除，避免真人验证时难分辨、也避免 sequence 覆盖逻辑。
2. **entry 定位明确**：`animation_locations` 恰为 Bed / Floor / Sofa 之一，且**无 custom_location**、
   **无 `*CUSTOM VOICES*` 之类的显示名后缀干扰**，人类在对应普通菜单可直接搜到。
3. **不涉及复杂覆盖逻辑**：非 sequence（无同名 stem 的 phase 兄弟）、actors 数 ≤ 2、
   category 为常见动作、`animation_allowed_for_random` 无异常。
   → 满足以上者评为 **EASY_GOOD**；缺任一关键项评为 **EASY_SKIP** + 具体理由。

---

## 4. 我下一步的动作（等 Dorothy）

- 本机**未**生成任何 package、未写 Mods、未动源、未改 writer/validator（遵嘱）。
- 等 Dorothy 提供真实 `ww_animation_corpus_v1` 清单（或确认我直接读真实包的方式）。
- 拿到清单后：建映射 → 输出完整候选表（第 2 节字段）→ 按第 3 节选出并锁定 3 个 EASY 目标
  （含 ordinal、display 新旧值、location/category/actors 证据）→ 交 Dorothy 确认。
- **Dorothy 确认后才运行 writer**（`--easy --force`）生成 sidecar。现在不生成。
