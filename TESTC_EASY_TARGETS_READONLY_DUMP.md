# TEST C EASY — 最终 3 目标只读定位表（ordinal 35 / 36 / 82）

> 日期：2026-08-22；来源：`scripts/ww_animation_sidecar_testc.py`（EASY_TARGETS 常量为准）
> 与 writer 读取逻辑一致，未生成 package，未写 Mods。

---

## 关键前提（所有 3 个 target 共用同一个 WW XML）

真实源包是**单一 WW registration XML**（`WW_ANIM_XML=0x7DF2169C`，`WW_XML_COUNT=1`，fail-closed）。
因此 **instance / type / group / 精确 TGI 对 3 个 target 完全相同** —— 不存在每个 target 独立的 resource；
3 个 target 只是同一 WW XML 内 `animations_list` 里不同 ordinal 的 entry。

writer 的 TGI **从 source 读取**（不硬编码实例号）：取唯一 `0x7DF2169C` entry 的
`(type_id, group_id, instance_id)`；若 WW_XML_COUNT≠1 则 fail-closed 报错退出（guarantee 单 XML）。

---

## 1. instance / type / group / 精确 TGI（共用，唯一 WW XML）

| 项 | 值 | 说明 |
|----|-----|------|
| **type** | `0x7DF2169C` | `WW_ANIM_XML`（WickedWhims Animation registration XML） |
| **group** | `0x00000000` | 真实包 metadata（source-faithful） |
| **instance** | `0xB3390ED6606C10E7` | 真实包 metadata（source-faithful） |
| **exact TGI** | `0x7DF2169C / 0x00000000 / 0xB3390ED6606C10E7` | writer 从源读、按此写回，TGI 不变 |

> 注：group / instance 虽以真实包经验值列出，但 writer 不硬编码——它读源包该 entry 的实测 metadata。
> 若真实包 metadata 与该值有出入，writer 以源为准（fail-closed）。以上为当前真实包确认值。

---

## 2. source entry location（3 个 target 在 WW XML 内）

| 标记 | ordinal（0-based） | 1-based | 显示名（源读，非硬编码） | 位置 |
|------|-------------------|---------|--------------------------|------|
| EASY_C1 | **35** | 36 | `Go To Sleep TWO SIMS 1` | `animations_list` 第 36 个 `<U>`（0-based 35） |
| EASY_C2 | **36** | 37 | `Go To Sleep TWO SIMS 2` | `animations_list` 第 37 个 `<U>`（0-based 36） |
| EASY_C3 | **82** | 83 | `You Belong To Me 1` | `animations_list` 第 83 个 `<U>`（0-based 82） |

- 替换规则：仅替换 `animation_raw_display_name` 节点文本（ORDINAL 精确定位，禁 ordinal guessing / 全局替换）。
- fail-closed：`n_entries 须 =479`；target ordinal < 479；同 ordinal 不重复；display 节点数须=1；二次替换须一致。

---

## 3. writer target format（writer 将生成的 NEW_DISPLAY）

仅把 display 前缀 + 原文（**前缀不带数字后缀**，避免干扰定位）：

| 标记 | ordinal | OLD_DISPLAY（源读） | NEW_DISPLAY（writer 写） |
|------|---------|--------------------|--------------------------|
| EASY_C1 | 35 | `Go To Sleep TWO SIMS 1` | `【大包EASY_C1】Go To Sleep TWO SIMS 1` |
| EASY_C2 | 36 | `Go To Sleep TWO SIMS 2` | `【大包EASY_C2】Go To Sleep TWO SIMS 2` |
| EASY_C3 | 82 | `You Belong To Me 1` | `【大包EASY_C3】You Belong To Me 1` |

> OLD_DISPLAY 一律从源包该 ordinal 读取（本表所列值为当前真实包经验/白盒一致值；writer 运行时会二次读取并校验）。

---

## 4. writer 生成命令（等 Dorothy 确认后执行，现在不跑）

```powershell
python scripts\ww_animation_sidecar_testc.py `
  --source "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods\2026.7.20\WW_Nevely42_Animations.package" `
  --easy --force
```
- 输出目录：`output\ww_animation_testc_easy_nevely42\`
- 产物：`WW_Nevely42_Animations_SIDECAR_TESTC.package`
- 报告 pass 键：`TEST_C_EASY_STATIC_PASS`
- 预期：`DISPLAY_SEMANTIC_DIFF_COUNT=3`、`UNCHANGED_DISPLAY_ENTRY_COUNT=476`、`INTERNAL_XML_SEMANTIC_DIFF_COUNT=0`、`ZERO_WRITE_TO_MODS=YES`

---

## 状态
- ✅ 未生成 package
- ✅ 未写 Mods、未动源、未改 writer/validator
- ✅ 只读输出完成
