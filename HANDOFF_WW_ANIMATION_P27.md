# 交接文档 — WW 动画中文化全链路 (P21–P27 及管线全貌)

> 生成时间：2026-08-29（GMT+8）
> Git HEAD：`0f2c065`（= origin/main，tree clean，PUSH_OK）
> 用途：潜龙(Dorothy)上下文用尽/需要交接，本文件让任何接手者无缝接上。**先读本文件再动手。**
> 说明：本工作区 memory 索引服务暂停（embedding provider 变更，`memory_search` 返回
> `index metadata is missing`）。**不要依赖 memory_search**；一切以 git history + 脚本内容 +
> 本文件为准（三者一致、互相印证）。修复索引可跑 `openclaw memory index --force`。

---

## 0. 仓库位置与铁律

- 工作区：`/root/.openclaw/workspace/sims4-translator/`
- 真机源包(Windows, Dorothy 机器)：
  `C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods\2026.7.20\WW_Nevely42_Animations.package`
- 真机脚本包：
  `C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods\WickedWhimsMod\TURBODRIVER_WickedWhims_Scripts.ts4script`
- **铁律**：
  1. `ZERO_WRITE_TO_MODS = YES`——只读源包、不写 Mods、不 auto-deploy、不 production、不触源。
  2. 任何「部署 / 写 Mods / 触源」动作必须先经真人批准。
  3. 只读审计脚本一律不改源包（源 sha256 前后不变即为通过）。
  4. fail-closed：静态验证不通过就不写输出包，宁可漏不可误。

---

## 1. 一句话任务

把 WickedWhims 动画包 `WW_Nevely42_Animations.package`（**479 个 animation entry，1 个 WW_ANIM_XML**）
的**动画 Story 显示名**中文化。当前聚焦「抓奸 1..8」这 8 条（ordinal 299–306），经 P27 生成
XML override 包验证。路线已锁定（见 §3），**不再查 STBL/hash/L18n**。

---

## 2. 已确认的翻译路线（权威，勿再推翻）

```
WW_ANIM_XML (type=0x7DF2169C)
  → animation_raw_display_name          (XML <T n="animation_raw_display_name">英文</T>)
  → SexAnimationInstance.display_name   (由 __init__ 参数 animation_raw_display_name 赋值)
  → TurboLocalizedString                (同一 raw 值喂给构造)
```

- P21 结论：Story(299) display_name = `get_l18n_service().get_localized_string_id(hash("story_animations."+str(id)))`
  但 P27 方向调整后**不再走这条 hash/STBL 链**——直接用 XML 的 `animation_raw_display_name`。
- 用户最终裁定（P27）：源 of truth per ordinal = `animation_raw_display_name`（非 animation_id）。
  Story 条目在真实 XML 中**无 animation_id 字段**。

---

## 3. 关键资源类型 / 常量

| 常量 | 值 | 说明 |
|------|-----|------|
| WW_ANIM_XML | `0x7DF2169C` | 动画注册 XML，唯一 |
| STBL | `0x220557DA` | 字符串表（不再使用，除非退回旧路线） |
| tuning | `0x545AC2C2` | — |
| xml | `0x0333406C` | — |
| text list | `0x220557D4` | — |
| 源 XML 结构 | `<U n="WW"><L n="animations_list"><U n="anmN">...| 每个条目按出现顺序编号 ordinal（0-based） |
| 目标 ordinal | 299–306 | 8 条「Caught Cheating 1..8」→「抓奸 1..8」 |

---

## 4. 当前阶段：P27（已完成，待真机验证）

| 项 | 值 |
|----|-----|
| 脚本 | `scripts/ww_animation_xml_displayname_override.py` |
| 提交 | `0f2c065`（pushed） |
| 作用 | 生成 WW_ANIM_XML display_name override 包 |
| 输入 | `WW_Nevely42_Animations.package` |
| 输出 | `output/ww_p27/ww_p27_displayname_override.package` + `.txt` + `mapping.csv` |
| 内容 | 只替换 299–306 的 `animation_raw_display_name` →「抓奸 N」，**其余 312 条目逐字节不变**（精确 `<T n="...">OLD</T>` 片段替换，diff=0） |
| 验证 | 重读生成包：type=0x7DF2169C、instance 与源一致、8/8 已替换 → `VERDICT: PASS` |
| 白盒 | 用 `/tmp/patchtest/WW_Nevely42_Animations.package`（320 条目 fixture）验证：源 sha 不变、TGI 正确、mapping.csv 正确 |
| 退出码 | 0 PASS / 2 源缺 / 3 无或非单 WW_ANIM_XML / 4 无 animations_list / 5 目标 ordinal 缺失或缺 raw / 6 构建或验证失败 |

**真机运行**（Dorothy 待办）：
```powershell
git pull
python scripts\ww_animation_xml_displayname_override.py "C:\...\WW_Nevely42_Animations.package"
```
回贴 `output\ww_p27\ww_p27_displayname_override.txt`。VERDICT=PASS 即可以部署到 Mods（需真人批准）。

---

## 5. 前置只读审计脚本（全部 committed + pushed，真机待跑）

| 提交 | 脚本 | 作用 | 退出码 |
|------|------|------|--------|
| `aa3f51b` | `story_display_name_patch_test.py` | 只读：分析 ordinal 299–306 的 raw/孪生 display 字段/hash-key 字段，给「改 raw 是否影响 display_name」建议 | 0/2/3/4/5 |
| `9612b61` | `story_display_name_source_trace.py` | 只读：锁定 `SexAnimationInstance.__init__`，Q1 display_name 是否参数(否,实为 raw)→Q2 STORE_ATTR→Q3 TurboLocalizedString 链 | 0/2/3/4/7 |
| `2dd017f` | `story_display_name_consumer_trace.py` | 只读：扫全 ts4script 的 `LOAD_ATTR display_name`，按 class/function 命名上下文过滤，±30 窗口 | 0/2/3/4/7 |
| `74e63c7` | `story_display_name_flow_trace.py` | 只读：xdis 反汇编 `_create_sex_animation_instance` + callee(depth≤3)，答 4 问 | 0/2/3/4/7 |
| `c861410` | `story_animation_entry_index.py` | 只读：建 XML 条目索引（ordinal→字段），默认 ordinal 295–310 | 0/2/3/4/5 |
| `e8717ff` | `story_animation_xml_trace.py` | 只读：扫 XML 资源定位 299–306 的 Caught Cheating | 0/2/3 |
| `e555270` | `debug_localized_string_constructor.py` | 只读：追 TurboLocalizedString 对象创建 | 0/2/3/4/7 |
| `7030124` | `story_hash_global_audit.py` | 只读：Story hash 全局 STBL 审计（旧路线，已废弃方向） | fail4 |
| `6fbc227` | `debug_package_search.py` | 只读：package 文本搜索 | 0/3/4 |
| `02f2613` | `ww_animation_p26_localized_string_trace.py` | P26 只读反向追 localized_string | 0/2/3/4/7 |
| `396d7fa` | `ww_animation_p25_l18n_service.py` | P25 只读解析 l18n_service | 0/2/3/4/7 |
| `47768cb` | `debug_ts4script_search.py` | 只读 .ts4script 反汇编搜索 | 0/2/3/7 |
| `a2af79d` | `debug_stbl_reverse_hash.py` | 只读反向 hash 发现（旧路线） | — |
| `ffd1abe` | `ww_animation_p23_story_stbl_override.py` | P23 旧路线（STBL override，已被 P27 替代） | 0/2/3/4/6/9 |

> ⚠️ **P22/P23/P25/P26/STBL/hash 整条路线已废弃**（方向调整 P27）。脚本保留在仓库供回溯，
> 但**新工作一律走 P27 XML override 路线**。

---

## 6. 早期管线背景（Phase 1–3B，已冻结）

- 本项目始于「Sims 4 动作/动画包自动汉化工具」：Phase 1 Scanner（只读）、Phase 2/3 翻译。
- 已做：Pose/WW 分类、显示文本提取、STBL locale 取证（CHS=0x01）、TEST A/B/C sidecar canary 体系。
- 关键冻结结论（PHASE3B-GOLDEN）：自制 package writer 已冻结于「source-faithful」哲学——
  写包必须保留 source 的 major/minor/header_comp/每 entry 的压缩高 bit/mem_size/comp_type，
  不得 normalize。`ww_animation_canary_builder.py` 是架构基石，勿动。
- 旧 `TODO.md` / `README.md` 记录早期 Phase 待办（Type ID 核实等），与当前 P27 主线无关。

---

## 7. 任务清单（待办，按优先级）

### 🔴 当前主线（P27 验证 → 部署）
- [ ] **真机跑 P27**（Dorothy）：`git pull` + 跑 override 脚本 → 回贴 txt → 确认 VERDICT=PASS
- [ ] **部署到 Mods**（真人批准）：把 `ww_p27_displayname_override.package` 放入 Mods，删 `localthumbcache`，进游戏验证 299–306 显示「抓奸 N」
- [ ] **若 PASS** → 启动 P24 批量扩展：把全部 479 条（或全部 Story 条）display 名中文化（复用 P27 同一机制，扩展到更多 ordinal）

### 🟡 真机待跑（只读审计，回贴结果给我判读）
- [ ] `story_display_name_patch_test.py`（真机 WW 包）→ 确认仅 raw、无 hash/key → 佐证 P27 可行性
- [ ] `story_display_name_source_trace.py`（真机 ts4script）→ 确认真实 `__init__` 签名
- [ ] `story_display_name_consumer_trace.py`（真机 ts4script）→ 确认谁消费 display_name、是否终止于 TurboLocalizedString
- [ ] `story_display_name_flow_trace.py`（真机 ts4script）→ 确认 Q1-Q4
- [ ] `story_animation_entry_index.py`（真机包, --start 295 --end 310）→ 确认 ordinal→entry 映射
- [ ] `story_animation_xml_trace.py`（真机包）→ 确认 299–306 是否含「Caught Cheating N」
- [ ] `debug_localized_string_constructor.py`（真机 ts4script）
- [ ] `debug_ts4script_search.py` / `debug_package_search.py`

### ⚪ 已废弃（仅回溯，勿投入）
- [ ] P22/P23/P25/P26/STBL/hash 全链验证（P27 已替代其方向）

---

## 8. 已完成的决策（勿再纠结）

- WW 动画是独立翻译域：479/479 display 名需新译，0 可复用。
- M2 模型：每个 localized 动画 = 完整 XML 副本，仅 instance + display 变（P27 改为单包单 XML override）。
- P23 不跟随源包内 STBL（源 STBL 是陷阱，仅 2 无关 key）。
- Story 条目无 animation_id → 定位须按 ordinal（出现顺序）+ raw 文本。
- 所有只读脚本 fail-closed，源包 sha 不变。

---

## 9. 交接后第一动作

1. `git pull`（应已在 `0f2c065`）
2. 真机跑 §7 主线的第一项：P27 override 生成。
3. 把 `output/ww_p27/ww_p27_displayname_override.txt` 与最终判定回给潜龙。
