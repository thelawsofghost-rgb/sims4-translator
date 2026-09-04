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
- [x] Windows 真包单条测试 #1: `Left->左` → **游戏报“文件损坏”** (v1 build_dbpf 重建破坏结构)
      → v1 根因: 置零 index flags/reserved、剥压缩标记、移除 body padding、重算绝对 index offset
- [x] Windows 真包单条测试 #2 (v2 原位外科手术): 生成 CHS_test2 → **游戏无法启动**
      → v2 根因 (真包 diag 确诊): 本系列 pose 包 STBL 是 **raw 存储** (size=130, 5 keys)。
        stbl_surgical_edit 把短文本(左)写进原槽并改长度字段 -> 解析器按 7+len 推进
        但物理槽仍 7+old_len -> 后续 key 错位 -> STBL 结构损坏。
        (我的 VERI 只查目标 key 漏过; 本地复现确认解码失败)
- [x] patch_stbl **v3 relayout 修复** (faad304): 重建整个 STBL + 正确 DBPF relayout;
      fixture 全过, 但 Windows 真包测试 #3 仍失败: `de4e14f9...` 游戏拒绝启动
      → 定位: relayout 压缩 CHS STBL 1B 后 index_offset 512->511 + 后续资源 offset 全 -1,
        loader 对结构位移敏感。
- [x] patch_stbl **首选方案 (a4a52fa): 保持原体积的 inplace 原位写入** — 不重排!
      → 关键洞察: STBL 解析器按 count 顺序读满即停, 忽略最后一个 key 之后的尾部字节。
        新 STBL 短了 1B, 在最后 key 之后补零到 == 原 size -> 存储体积不变 -> 
        文件大小/index_offset/index_size/count/全部 index entry 均与原始逐字节一致;
        仅目标 STBL body 区内容字节变 (Left->左 + 尾部补零), 等同手工 hex 编辑。
        新增 [STRUCT] 验证: 结构性零改动。新增 scripts/cmp_pkg.py 全量对比诊断。
        fixture (raw + zlib) 均 [STRUCT] PASS + [VERI] PASS。
- [ ] Windows 真包单条测试 #4: 用 inplace 方案重新生成 → 游戏加载显示中文
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

---

## P28B-0 (真机 DONE 2026-09-04: LOADS_OK, Nevely 正常存在; 已由 Dorothy 实测并回滚。下面脚本为历史留存, 不再部署)
P28B-1 (zero-XML single-resource override) 真机亦 LOADS_OK 并已 ROLLBACK_OK。
由此真机链路证明: Priority 600>500 / duplicate full package / single-resource same-TGI override /
build_package() 0 修改 XML 均可被 Sims4+WW 接受。剩余问题集中在 P27 修改 XML 后的资源 -> 转移 P28C。

历史脚本(不跑):
  scripts/ww_p28b0_full_clone.py / _report_check.py / _tgi_check.py / _cfg_audit.py / _deploy.ps1 / _rollback.ps1 / _static_check.py / _wintest.py

P28B-0 = 完全禁止 build_package 的"整包 byte-identical clone" control:
  - 真实源包 bytes -> output clone (整个文件 byte-for-byte copy, 不 parse/不 rebuild/不改 header/index/resource)
  - 验证 SOURCE_PACKAGE_SHA256 / CLONE_PACKAGE_SHA256 / PACKAGE_BYTE_IDENTICAL=YES / 长度一致 / entries 原样
  - 放更高 Priority: 情况A FULL_PACKAGE_CLONE=LOADS_OK -> 才执行 P28B-1; 情况B BREAKS_LOAD -> 问题在 duplicate/precedence/registration 语义, 先调查 WW 如何发现注册 animation package
  - 独立目录 P28B0_Overrides, 独立备份 Resource.cfg.p28b0_backup, 不与 P28B-1 混用
  - post-write re-audit: Resource.cfg 写完后、Copy 前再跑 ww_p28b0_cfg_audit.py check, 必须实际得
      SOURCE_EFFECTIVE_PRIORITY=500 / P28B0_OVERRIDE_EFFECTIVE_PRIORITY=600 / PRIORITY_RELATION=OVERRIDE_HIGHER
    才复制; 否则 fail-closed (不能只依赖 proposed priority)

已实现 (8 files, commit xx):
  scripts/ww_p28b0_full_clone.py                (生成器: shutil.copyfile 全文件字节复制 + 逐块SHA + 逐字节比对 + report)
  scripts/ww_p28b0_full_clone_report_check.py   (独立重读 report + 对 source/clone 实际文件独立逐字节比对)
  scripts/ww_p28b0_full_clone_tgi_check.py      (独立 raw-index 条目普查: 同条目数/单WW_XML/TGI一致/权威 instance)
  scripts/ww_p28b0_cfg_audit.py                 (P28B0 专属 cfg 审计 + 决策; 输出 P28B0_OVERRIDE_EFFECTIVE_PRIORITY)
  scripts/ww_p28b0_full_clone_deploy.ps1        (部署: 双重验证->备份->_append->post-write re-audit(须 override_eff>src_eff)->copy, 全 ASCII+Run-Python)
  scripts/ww_p28b0_full_clone_rollback.ps1      (回滚: 只删 P28B0 clone/空目录/独立backup恢复 cfg+校验SHA/删缓存)
  scripts/ww_p28b0_full_clone_static_check.py   (21 项静态不变式全 PASS)
  scripts/ww_p28b0_full_clone_wintest.py        (34 项全链路沙箱测试 PASS)

真机命令 (交给 Dorothy):
  生成: python D:\projects\sims4_trans\scripts\ww_p28b0_full_clone.py --source "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods\2026.7.20\WW_Nevely42_Animations.package" --force
  部署: powershell -ExecutionPolicy Bypass -File .\scripts\ww_p28b0_full_clone_deploy.ps1
  回滚: powershell -ExecutionPolicy Bypass -File .\scripts\ww_p28b0_full_clone_rollback.ps1

安全: 源包只读/只写 output/只写 P28B0 clone/Resource.cfg 必须备份/localthumbcache 可删/rollback 恢复 cfg SHA/不启 P24/不自动开游戏。
保留 fa6da01(P28B-1), 不废弃; 仅 P28B-0 LOADS_OK 后执行。

---

## P28C (IMPLEMENTED + TESTED 2026-09-04) FIXED mem_size + ASCII single-field canary

背景: P27 ww_animation_xml_displayname_override.py:207 复用 source 旧 field7 (P27 mem_size re-regression)。
P28B 真机链路已证 build_package() 在 0 修改 XML 下可用; 剩余问题集中在 P27 修改 XML 后的资源 ->
怀疑修改后 field7 != 新 payload 真实解压长导致 XML 资源损坏。P28C = 只改 ordinal 299 -> TEST299 (ASCII),
修复 mem_size = 新解压实际长, 独立验证。

期望机验:
  SOURCE_MEM_SIZE=... / NEW_XML_DECOMPRESSED_SIZE=... / WRITTEN_MEM_SIZE=...
  MEM_SIZE_MATCH_NEW_XML=YES  (WRITTEN == NEW_XML_DECOMPRESSED_SIZE)
  TARGET_ORDINAL=299 / TARGET_NEW_RAW=TEST299 / TARGETS_CHANGED=1/1 / NON_TARGET_XML_DIFF=0
  P27_MEM_SIZE_REGRESSION_FIXED=YES

generator 修复点 (区别于 P27 坏行 m0.get("mem_size", len(new_plain))):
  new_xml_decompressed_size = len(decompress_maybe(new_body))
  written_mem_size = new_xml_decompressed_size; meta mem_size = written_mem_size (绝不沿用 source field7)
  沙箱实证: 源旧 field7=56898, 改 299 后新解压长=56819(mem 已变), 写入 WRITTEN=56819==56819 (若 P27 会写 56898 错误)。

产物: output/ww_p28c/WW_P28C_TEST299_Override.package + ww_p28c_report.txt + mapping.csv

已实现 (8 files):
  scripts/ww_p28c_ascii_canary.py      (生成器; 只改 ordinal299->TEST299; 修 mem_size=新解压长; 机验 NON_TARGET_XML_DIFF=0)
  scripts/ww_p28c_report_check.py      (独立重读 report + 对真实包 bytes 独立复核 mem_size/TGI/TEST299)
  scripts/ww_p28c_tgi_check.py         (独立 raw-index 普查: source 多条目 vs override 单 WW_XML, TGI 一致)
  scripts/ww_p28c_cfg_audit.py         (P28C 专属 cfg 审计; 输出 P28C_OVERRIDE_EFFECTIVE_PRIORITY)
  scripts/ww_p28c_deploy.ps1           (部署: report_check+tgi_check+MEM_SIZE+299 通过 -> 备份->append->post-write re-audit(override600>src500)->copy)
  scripts/ww_p28c_rollback.ps1         (回滚: 只删 P28C_Overrides + backup 恢复 cfg + 校验 SHA)
  scripts/ww_p28c_static_check.py      (28 项静态不变式 PASS)
  scripts/ww_p28c_wintest.py           (38 项全链路沙箱测试 PASS, 含 mem_size re-regression 证明 + 负向)
  P27 mem_size fix 未改动 P27 本文件(保持可对比); P28C 为新独立生成器。

真机命令 (交给 Dorothy):
  git pull
  生成: python D:\projects\sims4_trans\scripts\ww_p28c_ascii_canary.py --source "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods\2026.7.20\WW_Nevely42_Animations.package" --force
  部署: powershell -ExecutionPolicy Bypass -File .\scripts\ww_p28c_deploy.ps1
  回滚: powershell -ExecutionPolicy Bypass -File .\scripts\ww_p28c_rollback.ps1

game 结果解释:
  A. Nevely 正常 + ordinal 299 显示 TEST299 -> ASCII_DISPLAY_OVERRIDE_WORKS (mem_size fix 生效, 中文可复推)
  B. Nevely 正常 + 299 仍英文           -> RESOURCE_LOADS_BUT_DISPLAY_STALE (display 缓存/WW 语义, 非 mem_size)
  C. Nevely 整组消失                     -> MODIFIED_XML_BREAKS_LOAD (改 mem_size 后仍加载失败, 更深问题)

安全: 源只读/只写 output/只写 P28C_Overrides/Resource.cfg 备份/rollback 恢复 SHA/不中文/不 P24/不 479 批量/不自动部署/不启动游戏。



---

## P29-A (IMPLEMENTED + OFFLINE-TESTED 2026-09-04) SexAnimationInstance constructor runtime trace (debug-only)

目的(单一): P28C TEST299 override 生效时回答 CONSTRUCTOR_ARG=TEST299 还是 =OLD。
不一次 hook constructor+UI+picker; 无 P29-B; 不 reimport; 不中文; 不 P24; 不改 WW 原 ts4script; 不改源 Nevely package。

Hook 点 (committed decompiled transcription, 真机需再确认签名——若不同即报告不硬 hook):
  SexAnimationInstance.__init__(self, animation_id, animation_raw_display_name, animation_type)
  内: self.h=hash_string("story_animations."+str(animation_id)); self.display_name=raw;
      self.localized=TurboLocalizedString(self.h, raw); self.name=raw
  即 constructor 收到 TEST299 -> display_name/name==TEST299; 收到 OLD -> OLD(UI 仍 OLD 但 CONSTRUCTOR_ARG=OLD 则问题在 loader/import 上游)。

实现(独立 debug ts4script monkey-patch wrapper, 只记录不改参/不改 display/localized/不改返回):
  scripts/ww_p29a_mod.py              运行时 hook 模块(顶层模块; 包裹原 __init__->调用原 __init__ 不变->读 display_name/name/localized.hash 记录; 错则 restore 原并 re-raise; 动态定位类: wickedwhims.sex.animations.* 候选 + sys.modules 扫描 + 仅包裹签名匹配目标形)
  scripts/ww_p29a_logic_test.py       OFFLINE: 对 stand-in 类(严格同签名)证明 wrap 语义 + RAW_ARG(positional bind)/INSTANCE_* / hash / TEST299 vs OLD 匹配 + sig 不匹配不包裹(fail-closed) → PASS
  scripts/ww_p29a_static_check.py     静态安全门(不写 display_name/name/localized; 不读 localized.text; 调用原 __init__; restore-on-error; 无 walrus) → PASS
  scripts/ww_p29a_build_ts4script.py  packer: src.py -> ts4script(zip member ww_p29a_mod.pyc), 自省 member import → OK
  scripts/ww_p29a_build_on_win.ps1    Windows: 用 GAME python 编译(保证 pyc magic 匹配), 否则 fail-closed(可 -GamePython / WW_GAME_PYTHON)
  scripts/ww_p29a_deploy.ps1          ONE-KEY: static+logic 门 → game-python build → copy 进 Mods root 记录 SHA → (可选)调 P28C deploy 重上 TEST299 override
  scripts/ww_p29a_rollback.ps1        只删 ww_p29a_debug.ts4script + P29 log/flag; 若 P29A 曾 redep P28C 则调 P28C rollback 恢复 base; 不碰其它 Mod
  scripts/ww_p29a_wintest.py          OFFLINE 三gate: STATIC/LOGIC/BUILD 全 PASS

输出(易回贴):
  HOOK_INSTALLED=YES/NO ; RAW_ARG=OLD|TEST299 ; INSTANCE_DISPLAY_NAME= ; INSTANCE_NAME= ; LOCALIZED_HASH= ; MATCH=OLD|TEST299|NONE ; VERDICT=TRACE_CAPTURED

判定(用户 2026-09-04):
  A. RAW_ARG=OLD & INSTANCE_DISPLAY_NAME=OLD -> P29A_RESULT=OVERRIDE_NOT_RECONSTRUCTING_TARGET (下一步查 loader/import registry/stable identity/同实例 reload)
  B. RAW_ARG=TEST299 & INSTANCE_DISPLAY_NAME=TEST299 -> P29A_RESULT=CONSTRUCTOR_RECEIVES_OVERRIDE (这时才做 P29-B: TurboLocalizedString/get_display_name/picker trace)
  C. hook 装上但 OLD 与 TEST299 都未出现 -> P29A_RESULT=TARGET_OBJECT_NOT_OBSERVED (先查目标映射/对象类型)

真机命令 (交给 Dorothy; 顺序: build+deploy -> 开游戏触发 Nevely 动画 -> 退出读 trace -> rollback):
  powershell -ExecutionPolicy Bypass -File .\scripts\ww_p29a_deploy.ps1 [-SkipP28C] [-GamePython <path>]
  powershell -ExecutionPolicy Bypass -File .\scripts\ww_p29a_rollback.ps1

安全/未决:
  - hook 的 in-game HOOK_INSTALLED=YES 只能在真机验证(sandbox 只证 logic/static/build)。若类名/模块路径/signature 与真机不符 -> hook 诚实报 HOOK_INSTALLED=NO 而非假 OLD。
  - pyc magic 必须由 game python 产出 -> build_on_win 找 game python, 找不到则 fail-closed(不猜版本)。

---

## P29-A hotfix (2026-09-04 17:12) ps1 param-placement bug + parse gate (bea8936->655c74b)

真机首执失败(未达 runtime hook): `ww_p29a_build_on_win.ps1` 脚本级 param(...) 前有可执行语句
($ErrorActionPreference/encoding lines), PS 把 param 当普通命令 -> "无法将param项识别为..."。

FAIL_OCCURRED_BEFORE_MODS_WRITE=YES: 真机输出停在 `--- BUILD (game python) ---` 的
`& $BUILD_WIN` 调用(该子脚本 parse 即抛错), deploy 在 $ErrorActionPreference=Stop 下终止,
从未到 Copy-Item(PLACE DEBUG TS4)与 P28C redep。未写 Mods\ww_p29a_debug.ts4script, 未动 P28C。

修复(全部 P29 ps1): 脚本级 param/CmdletBinding 移为第一个可执行构造; 其余(EAP/encoding/变量)
一律后置。统一 `[CmdletBinding()] param(...)` 模式。
  ww_p29a_build_on_win.ps1  (bug 源: EAP/encoding 在 param 前)
  ww_p29a_deploy.ps1        (本已 param 前置, 补 CmdletBinding/Set-StrictMode)
  ww_p29a_rollback.ps1      (无参脚本, 补空 param() 保持头规范)

新增 gate: scripts/ww_p29a_ps1_static_check.py
  A) PARAM_PLACEMENT(纯文本/跨平台, 无 PS 也可靠): 首个脚本级 header([CmdletBinding()]/param()
     最顶行)之前只允许注释/#requires/空行; 出现任何可执行语句 -> FAIL。
     回归实证: 对 bug 原形($ErrorActionPreference 在 param 前)返回 FAIL(exit 1); 修复后 PASS。
  B) REAL_PARSER(有 pwsh/powershell 时): Parser::ParseFile 要求 errors.Count=0; 无 host 显式
     SKIPPED(不静默当 clean)。Windows deploy 会真实执行。

deploy 顺序(fail-closed 保持): PS1_STRUCTURE -> STATIC -> LOGIC -> BUILD(game python build)
  -> build artifact verify -> 才 copy debug ts4script -> 才 P28C redep。任一 gate 失败不写 Mods/不动 P28C。

实验目标不变: P29-A 只测 constructor(RAW_ARG/INSTANCE_DISPLAY_NAME/NAME/LOCALIZED_HASH);
不 P29-B UI hook; 不改 WW ts4script / Nevely 源 package / P28C / P24。
真机勿跑旧 bea8936; pull 到新 commit 后再跑:
  powershell -ExecutionPolicy Bypass -File .\scripts\ww_p29a_deploy.ps1

---

## P29-A fix2 (2026-09-04 17:24) 编译器发现重设计: 不再找 game python.exe (a0b7bfd->b10f576)

真机二次失败(仍属 BUILD 层, 未进 runtime): 二次执行通过 PS1/STATIC/LOGIC gate 后
在 BUILD 报 REASON=Could not locate the game python automatically。
根因(用户点破): "找 game python.exe" 假设错误 -- TS4 只内嵌 python*_x64.dll(无 exe)。

设计修正(只改构建链, 不动 constructor trace 实验):
  GAME_PYTHON_EXE_ASSUMPTION=INVALID。DLL 不是编译器; 唯一能产出被游戏加载的 pyc 的
  办法是运行一个 LOCAL CPython, 其 importlib.util.MAGIC_NUMBER == 游戏 pyc magic。

新机制 (scripts/ww_p29a_game_py.py, 只读, 跨平台可测):
  magic-from-pyc --locate-mod <Mods>: 从 real WW *.ts4script 内已知可加载成员
     (animations_loader.pyc / animation_instance.pyc, 兜底任意 .pyc 成员) 读前4字节
     -> TARGET_PYC_MAGIC (最直接的兼容性 gate, 不假定版本)。
  compilers: 枚举本机可运行 CPython (py -0p / python / python3 / 常见安装目录),
     各自 sys.version + MAGIC_NUMBER.hex()。
  match --target <magic> [--prefer <py>]: 只允许 LOCAL_PYC_MAGIC==TARGET 者;
     无匹配 -> FAIL-CLOSED MATCH=NONE + 列出可用 -> 告知需装哪个 python major/minor。

ww_p29a_build_on_win.ps1 改写为 4 段:
  1) 读 live WW member -> TARGET_PYC_MAGIC
  2) match 选 magic 相符的本机编译器 (偏好 -GamePython/<env WW_GAME_PYTHON>, 但该 py
     仍必须 magic 相符, 不再当作 "游戏目录里的 exe")
  3) 用该编译器跑 build_ts4script.py
  4) 从产物 zip member 读回 BUILT_PYC_MAGIC 并断言 == TARGET; 不等即 FAIL。
  输出 TARGET_PYC_MAGIC/COMPILER_PATH/COMPILER_VERSION/BUILT_PYC_MAGIC/PYC_MAGIC_MATCH/
      BUILD=PASS。无匹配编译器绝不硬编译/不下载/不装/不猜版本。

deploy 顺序(fail-closed 保持): PS1_STRUCTURE -> STATIC -> LOGIC ->
  BUILD(magic gate 内) -> 才 PLACE DEBUG TS4 -> 才 P28C redep。BUILD 失败不写 Mods/不动 P28C。

OLD_WORKING_TS4SCRIPT_BUILD_FOUND=NO: git history 复查确认本仓库从未成功产出可被
  Sims 4 加载的 .ts4script(py) 构建 -- 历史 P13-P25 全是只读 xdis 反汇编 .pyc(读),
  P27/P28 交付的是 .package/XML/STBL 剪辑, 非编译 Python。故无既有 python/magic 可复用,
  此 magic-pinning 为新增。

wintest 新增 MAGIC gate(离线, 无需 pwsh): 合成一可加载 pyc(magic=宿主 magic, 代替
  Windows 上的 live WW member) -> magic-from-pyc 读得 TARGET -> match 选宿主编译器 ->
  build -> 断言 BUILT_PYC_MAGIC==TARGET。MAGIC/PS1/STATIC/LOGIC/BUILD 全 PASS。

真机勿跑旧 a0b7bfd; pull 到新 commit 后再跑:
  powershell -ExecutionPolicy Bypass -File .\scripts\ww_p29a_deploy.ps1
若不自动匹配到本机 python 则 fail-closed 列出, 按 REASON/GUIDANCE 装对应
CPython major.minor 后再带 -GamePython <path>\python.exe 重跑。

---

## P29-A fix3 (2026-09-04 17:42) 仅剩的 BUILD exit 1 根因 + 诊断 + py3.7 gate (74519d3->)

真机三跑: magic/compiler discovery 已通(TARGET_PYC_MAGIC=420d0d0a -> MATCH C:\...\Python37-32
python.exe 3.7.9), 但 real build exit 1, 且只见空 PY_STDERR=。

ROOT_CAUSE: scripts/ww_p29a_build_ts4script.py:84
    tmp_pyc.unlink(missing_ok=True)
Path.unlink(missing_ok=...) 是 Python 3.8+ 关键词; 在 CPython 3.7.9 抛
    TypeError: unlink() got an unexpected keyword argument 'missing_ok'
它位于 build() 的 finally:, 即使 py_compile 成功也在清理时抛错 -> 被 main 的
except Exception 吞掉 -> 返回 1 (FAIL_COMPILE)。sandbox 3.10 不触发, 故只在真机 3.7 暴露。
(全链仅此一处 3.8+ API; 语法经 ast feature_version=(3,7) 验证全 3.7-清洁。)

PY37_COMPAT_FIXED:
  * 删除 missing_ok -> 3.7-safe: if tmp_pyc.exists(): tmp_pyc.unlink() (try/except OSError)。
  * 新增 scripts/ww_p29a_py37_gate.py 双层级 gate:
      - 静态: 对三个输入 ast.parse(feature_version=(3,7)) 拒 >3.7 语法(walrus/f-string'= /
        match/...)，再加 3.8+ 运行 API deny-list(missing_ok/removeprefix/removesuffix/
        is_relative_to/functools.cache/importlib.metadata/ZoneInfo/PEP604 管道注解)。
      - 真实: 找到 python3.7 / py -3.7 时执行 python3.7 -m py_compile <inputs> (权威)。
      无 3.7 时静态通过则报 PY37_GATE=STATIC + PY37_REAL_COMPILE=SKIPPED。
  * build_on_win 在真实 build 前插入 3a.py3.7 compat gate(用匹配到的 3.7 编译器跑权威
    py_compile); deploy 在 LOGIC 后加静态 PY37_GATE; wintest 加 PY37 项。
  * 负向已验: 文件含 missing_ok -> PY37_GATE=FAIL(exit 1); walrus -> AST FAIL。

BUILD_DIAGNOSTICS_FIXED:
  * builder 失败不再吞: 新增 _fail() 在 stdout 打真话 P29A_BUILD=FAIL_* 行, 并把完整
    traceback print 到 STDERR(不再空 stderr)。已验: 坏源 -> exit1, stdout=FAIL_COMPILE,
    stderr=SyntaxError file/line。
  * build_on_win BUILD_FAIL 块改输出四段: BUILD_COMMAND= / BUILD_EXIT_CODE= /
    BUILD_STDOUT=(首见, 之前被吞) / BUILD_STDERR= ; 不再只给空 PY_STDERR=。

fail-closed 保持: script 层 gate(含新 PY37)全过才进真实 build; BUILD 失败不写 Mods、
不动 P28C。实验 scope 不变(仅 constructor trace)。

sandbox 已装 CPython 3.7.9(由源码, huawei 镜像), 用于在沙盒权威复现/证 3.7 compat。

## P29-A fix4 (2026-09-04 17:50) 4th real-machine BUILD-gate failure -> py37 argv shape

ROOT_CAUSE_CONFIRMED: ww_p29a_build_on_win.ps1 调 py37 gate 用单横线 "@(\"-py37\", ...)",
但 argparse 定义的是长选项 "--py37"; argparse 不接受单横缩写, 报
  ww_p29a_py37_gate.py: error: unrecognized arguments: -py37   (exit 2)
死在 build_on_win 的 "3a. py3.7 compat gate", 早于 real build / PLACE / P28C。
Python 3.7 compat / magic / hook / runtime 均无关。

PY37_ARG_FIXED: build_on_win.ps1 -> @(\"--py37\", $GATE_PY, ...)。已在真实 from-source
  CPython 3.7.9 下验证: --py37 -> ARGV_FLAG=--py37 + PY37_GATE=REAL + exit 0; 单横 -py37
  -> argparse unrecognized (exit 2) 复现原错误。

ALL_CALL_SITES_AUDITED: grep 全 P29-A, 唯一坏点 build_on_win.ps1:144(现 --py37);
  deploy/wintest/rollback 不传该 flag(wintest 用无 flag 静态层 + PY37ARGV 精确 argv)。

REGRESSION_TEST_ADDED:
  * ps1_static_check.py 新增 C) PY37_ARGV_SHAPE(text, 跨平台): 拒绝任何单横 \"-py37\" 调用,
    并要求 gate PyArgs 含 --py37; 负向已验(单横 ps1 -> FAIL)。
  * py37_gate.py 解析后 echo ARGV_FLAG=--py37。
  * wintest 新增 PY37ARGV gate: (a) 扫三个 ps1 不得出现单横 -py37; (b) 真跑
    python ww_p29a_py37_gate.py --py37 <exe> <builder> <mod> <logic> 并断言 exit 0 +
    ARGV_FLAG=--py37。
  沙盒不再只测 Python gate 而漏 wrapper 拼出的真实参数。

FAIL_OCCURRED_BEFORE_MODS_WRITE=YES : 3a 在 build_on_win 内(先于 PLACE DEBUG TS4 与
  P28C redep 块); 失败即 exit 非0 -> BUILD_ON_WIN_FAIL, 未到 Copy-Item 亦未调 P28C。
P28C_TOUCHED=NO

## P29-A fix5 (2026-09-04 18:0x) discovery/timing -- make retry real + fully traced (round 1 runtime)

首轮真实 runtime: DEBUG ts4script imported OK + logger works (trace exists at %TEMP%),
但 class unavailable at hook import time and NOTHING retried (old main() did ONE
immediate attempt then a FABRICATED "(retrying, deferred schedule active)" -- no
schedule ever registered).  So HOOK_INSTALLED stayed NO and no RAW_ARG/OLD/TEST299.

OLD_DISCOVERY_FAILURE_ROOT_CAUSE: _register_scheduler() was effectively a no-op.  If
sims4 importable it ran _retry_on_zone() exactly once and returned -- no repeating
callback, no zone hook, no RETRY loop.  If sims4 absent it ran _retry_on_zone() once.
Either way only one attempt at boot; WW's SexAnimationInstance module is not yet in
sys.modules at our boot (ts4script import order not guaranteed / WW lazy), so class
never installed and no later attempt ever fired.

DEFERRED_RETRY_FIXED (ww_p29a_mod.py):
  * real bounded retry loop in discovery: _retry_once() (3 immediate attempts in
    main, then arm a repeating in-world scheduler), each attempt traced.
  * scheduler: _arm_via_zone() probes the LIVE zone/current_zone object and registers
    a repeating callback via whichever of (loading_screen_ended/alarm/zone_load/
    register_callback/loading_screen_started) is callable; SCHEDULER_ARMED=YES/NO
    emitted with WHICH zone symbols were actually present.  If nothing arms it says
    so (RETRY_CALLBACK_EXECUTED=NO) instead of a fake "retrying".  Main-thread only,
    never touches sims objects off-thread, no sleep.
  * discovery self-verifies via import + sys.modules scan; on success logs
    HOOK_INSTALLED=YES + HOOK_MODULE=<real module> + HOOK_CLASS +
    HOOK_RETRY_INDEX=<n>, AFTER which RAW_ARG/INSTANCE_*/LOCALIZED_HASH are recorded.
  * honest terminal failure block: HOOK_INSTALLED=NO / RETRY_COUNT / LAST_MODULE_PRESENT
    / LAST_CLASS_PRESENT / IMPORT_EXCEPTION / RETRY_CALLBACK_EXECUTED /
    RUNTIME_MODULE_CANDIDATES / VERDICT=FAIL_DISCOVERY.

RETRY_TRACE_ADDED (task #3/#5): each attempt logs RETRY_INDEX/RETRY_AT/MODULE_PRESENT
  /CLASS_PRESENT/IMPORT_EXCEPTION/(read-only)[name for sys.modules if wickedwhims &
  animation|sex] -> RUNTIME_MODULE_CANDIDATES.  Offline tested (fake sims4+Zone):
  late-load -> RETRY_INDEX=1 MODULE_PRESENT=YES CLASS_PRESENT=YES HOOK_INSTALLED=YES.

LIVE CLASS MODULE RE-VERIFY (task #1): scripts/ww_p29a_live_class_probe.py re-scans
  the CURRENT TURBODRIVER_WickedWhims_Scripts.ts4script with xdis for any member whose
  __init__ carries exactly (animation_id, animation_raw_display_name, animation_type)
  and reports LIVE_CLASS_MODULE / LIVE_CLASS_CONFIRMED.  ww_p29a_liveprobe.ps1 locates
  the WW ts4script under -Mods and runs it (read-only).  Prior confirmed path
  wickedwhims/sex/animations/animation_instance.pyc is the WORKING hypothesis; the
  probe + the mod's in-game RUNTIME_MODULE_CANDIDATES are the authoritative live truth
  for the CURRENT WW version.  (Offline: probe mechanism validated pos+neg on a
  synthesized fixture; real answer requires Dorothy's live WW file.)

TASK #1 note: cannot re-decompile Dorothy's live WW from the Linux sandbox (the real
  ts4script only exists on the real machine).  Provide the ps1 so Dorothy gets
  LIVE_CLASS_MODULE/CONFIRMED in one command against whatever WW is installed now.

Tests: added LIVECLS + EXPANDED LOGIC (phase-2 fake-sims4 scheduler/discovery asserts)
  + PY37GATE/PY37ARGV kept.  All offline PASS.  py_compile clean.  ps1 PARAM_PLACEMENT
  PASS incl. new liveprobe.  Real 3.7.9 gate on mod+probe = REAL PASS (mod remains
  3.7-safe: no 3.8-only APIs).  Scope unchanged: constructor trace only, no WW
  ts4script / Nevely / P28C / P24 / zh / P29-B.

## P29-A LIVE_WW_REWRITE (2026-09-04 ~18:2x) -- current-WW signature is different; P-series transcription is STALE

真机 (Dorothy, 3.7.9 / magic 420d0d0a, native marshal) 证伪旧签名:
  OLD transcription (self, animation_id, animation_raw_display_name, animation_type)
  = STALE / INVALID_FOR_CURRENT_WW.
  LIVE current __init__: self, animation_id, display_name, display_icon, author,
  author_id, ... (29 params total per native probe); body references
  display_name_override / original_instance / identifier_cache (LIVE co_names).
  animation_raw_display_name XML field IS NO LONGER the constructor param name.

DECISIONS:
  * Do NOT deploy the OLD constructor hook.  Do NOT make P29-B.
  * Distinguish LIVE_CLASS_PRESENT (class+init exist NOW) from
    EXPECTED_SIGNATURE_MATCH (merely matches the OLD stale shape) -- do NOT collapse
    a signature mismatch into CONFIRMED=NO.
  * P28C override (ordinal 299 -> TEST299, XML display_name) is unchanged/valid.

ARTIFACTS (this commit):
  1. scripts/ww_p29a_live_probe.py  -- NATIVE probe (real matching CPython,
     marshal.loads(pyc[16:]), NO xdis).  Reads WW pyc header; asserts local magic ==
     WW magic == 420d0d0a; walks nested code objs; emits LIVE_CLASS_PRESENT,
     CLASS_HOME_MEMBER, LIVE_INIT_ARGS, LIVE_INIT_NAMES, DISPLAY_PARAM_INDEX2,
     EXPECTED_SIGNATURE_MATCH (informational), OLD_SIGNATURE_MATCH, OLD_TRANSCRIPTION_
     STALE, LIVE_SIGNATURE_HASH, PARAMS_JSON.  Exit 0 class found / 2 missing / 3 magic
     mismatch / 4 marshal error.  (Companion ww_p29a_live_class_probe.py removed --
     it was the xdis backend superseded by native marshal.)
  2. scripts/ww_p29a_static_trace.py -- native static DATAFLOW trace of loader ->
     constructor (tasks #2/#3): native marshal + stdlib dis across all pyc members
     (loader+instance scanned first).  Emits per-member FN rows: constructor-call
     windows (LOAD/CALL stack), display_name/display_name_override STORE sites,
     and string constants (to recover XML keys).  --detail prints raw LOAD/CALL
     windows.  (Output is the artifact; final label fields below are inferred on the
     real machine from this report.)
  3. scripts/ww_p29a_liveprobe.ps1  -- re-verifies LIVE class contract on the real
     machine with the 3.7.9 python (default path given by Dorothy).
  4. scripts/ww_p29a_static_trace.ps1 -- runs the tracer against the live WW loader.
  5. ww_p29a_mod.py hook REWRITTEN for current signature (task #4): wrap gate now
     requires sig[1]=animation_id AND sig[2]=display_name (the current shape).  Emits
     per ctor: ANIMATION_ID, DISPLAY_NAME_ARG (positional index1 param / kwarg),
     AUTHOR, AUTHOR_ID, INSTANCE_DISPLAY_NAME, INSTANCE_DISPLAY_NAME_OVERRIDE,
     ORIGINAL_INSTANCE, MATCH.  MATCH=TEST299/OLD only when an actually-carried value
     equals a marker; otherwise records truthful value (no forced binary).  It does
     NOT wrap the stale animation_raw_display_name shape (fail-closed).
  6. ww_p29a_logic_test.py rewritten to the current signature + new emit assertions
     (DISPLAY_NAME_ARG, INSTANCE_DISPLAY_NAME_OVERRIDE, ORIGINAL_INSTANCE, AUTHOR,
     AUTHOR_ID, MATCH classification incl OTHER, override-in-body observed,
     sig-mismatch not wrapped).  Phase-2 discovery/scheduler test preserved.

STATUS: wintest all PASS (MAGIC/PS1/STATIC/LOGIC/PY37/PY37ARGV/LIVECLS/BUILD).
  Real-3.7.9 compile of mod/logic/probe/tracer = OK.  Deploy NOT run (pending the
  real-machine static trace per user directive) -- old hook is NOT deployed.
NEXT_WINDOWS_COMMAND (task #1 live re-verify):
  powershell -ExecutionPolicy Bypass -File .\scripts\ww_p29a_liveprobe.ps1
NEXT_WINDOWS_COMMAND (task #2/#3 static dataflow):
  powershell -ExecutionPolicy Bypass -File .\scripts\ww_p29a_static_trace.ps1 -Detail
  paste output back -> infer CALLER_FUNCTION / DISPLAY_NAME_ARGUMENT_SOURCE /
  DISPLAY_NAME_OVERRIDE_BEHAVIOR / RAW_FIELD_TO_DISPLAY_NAME_CHAIN.
Scope unchanged: no WW ts4script / Nevely / P28C / P24 / zh / P29-B.

## P29-A DISPLAY_SOURCE_PIN (2026-09-04) -- animation_raw_display_name -> display_name -> ctor

New read-only bytecode dataflow pin (native 3.7.9 marshal, NO xdis):
  scripts/ww_p29a_display_source_trace.py + scripts/ww_p29a_display_source_trace.ps1

Target fn = _create_sex_animation_instance in animations_loader.pyc.  Emits
  CALLER_FUNCTION
  RAW_FIELD_LITERAL_PRESENT        ('animation_raw_display_name' const within fn)
  RAW_FIELD_LITERAL_IN_LOADER      (same literal anywhere in the loader member)
  RAW_FIELD_READ_PATTERN           (.get / [] window consuming the literal)
  DISPLAY_NAME_STORE_COUNT / DISPLAY_NAME_STORE_PATTERN  (producer of display_name
                                                          incl. copy-chain resolution)
  CTOR_ARG[i]=... per SexAnimationInstance(...) CALL arg; the display arg is detected
              by producer referencing the local display_name (live ctor contract:
              self,animation_id,display_name,display_icon,... so the passed slot is
              CALL-arg index 1).
  DISPLAY_NAME_ARGUMENT_TO_CTOR
  RAW_TO_DISPLAY_CHAIN = CONFIRMED  ONLY for a clean unbranching container-read ->
                         display_name -> ctor-arg in the SAME fn; PARTIAL_HELPER_ROUTED
                         when the raw literal only lives in a callee/helper (the real
                         hop is reported verbatim, never simplified) ; NO_RAW_LITERAL
                         when the field is gone.
Override consolidation from animation_instance.pyc (live body names, already evidenced):
  DISPLAY_NAME_OVERRIDE_PRIORITY / SET_DISPLAY_NAME_WRITES_OVERRIDE /
  GET_DISPLAY_NAME_FALLBACK_TO_BASE + DISPLAY_NAME_OVERRIDE_BEHAVIOR one-liner.
VM handles 3.7 CALL_METHOD (receiver-in-method) so args aren't shifted -- validated
on synthesized fixtures under the REAL 3.7.9 (magic 420d0d0a): clean chain -> CONFIRMED
with CTOR_ARG[1]=L:display_name ; helper/fallback variant -> PARTIAL (not faked).

Enforced what Dorothy asked NOT to do: no P29-A runtime deploy, no P29-B, no WW or
Nevely ts4script edit, no P28C/P24 change, no Chinese.  Pure read-only native trace.
ps1 static gate now checks 7 ps1 scripts (build_on_win/deploy/rollback/liveprobe/
static_trace/display_source_trace + debug runner).  wintest gained DISP mechanism
(offline fixtures, both branches PASS).  wintest all PASS.

## P29-A PS1 PARSER-BUG FIX (2026-09-04) -- real-machine parser failure, not WW/python

Real-machine symptom: `ww_p29a_display_source_trace.ps1` died at the PowerShell PARSER
stage ("expression missing ')'", "statement/block missing '}'") BEFORE any native
trace ran.  Root cause: lines 31-33 used a fragile multiline `Fail ("..." + "...")`
concatenation whose )/} landed wrong; param-placement/py37-argv pure-text gates could
NOT see it (they only inspect text above the first header).

Fixes (commit):
  * ww_p29a_display_source_trace.ps1: replaced the multiline-`+` throw with a single
    plain `throw "PY37_MISSING=$Py37; ..."` string.  No cross-line concat.  (The rest
    is unchanged: param first, 2> to %TEMP% stderr file, no &&, no $Args misuse.)
  * ww_p29a_ps1_static_check.py: added STRUCT_BALANCE -- a deterministic tokenizer
    state-machine (NOT a per-line regex) that skips comments / '...' / "..." (backtick
    + doubled-quote aware) / here-strings and tracks () [] {} depth across lines.
    Catches the parser-stage bug class regardless of whether a PowerShell host exists.
    Printed per-ps1 as STRUCT_BALANCE=PASS/FAIL + the offending line.  Includes
    SELF_BALANCE_TESTS proving the detector flags an extra unbalanced brace AND does
    not false-positive on valid tricky PS (braces in strings, ${scope}, here-strings,
    single-line { ... } Fail blocks).  ww_p29a_display_source_trace.ps1 is registered
    in the default ps1 list (6 ps1s, all STRUCT_BALANCE=PASS).
  * REAL_PARSER (true [System.Management.Automation.Language.Parser]::ParseFile) still
    runs when pwsh/powershell is on PATH (best effort, FAILs the gate on >0 errors);
    on Linux sandbox it is SKIPPED (no engine).  STRUCT_BALANCE is the enforced,
    engine-free gate that always runs; on Dorothy's Windows machine REAL_PARSER also
    runs over every P29 ps1.

Re-verified offline: all 6 ps1 STRUCT_BALANCE/PARAM_PLACEMENT/PY37_ARGV_SHAPE PASS,
SELF_BALANCE=PASS, and the full wintest is green
(MAGIC/PS1/STATIC/LOGIC/PY37/PY37ARGV/LIVECLS/DISP/BUILD = P29A_WINTEST=PASS).

Scope unchanged: read-only display-source pin only (animation_raw_display_name ->
display_name -> SexAnimationInstance).  No P29-A deploy, no P29-B, no WW/Nevely/
P28C/P24, no Chinese.  Dorothy: `git pull`, then re-run the ps1 (parser stage now
passes; the static trace runs under the real 3.7.9).

## P29-A DISPLAY-ORIGIN AUDIT + CALLSITE FIX (2026-09-04) -- read-only, current WW

Real-machine DISPLAY trace (from the ps1, run on Dorothy's box) RETIRED the old
"animation_raw_display_name -> SexAnimationInstance" assumption for good.  The live
_create_sex_animation_instance does:

    display_name = animation_tuning.animation_display_name     # store#8
        ... SexAnimationInstance(animation_id=.., display_name=display_name, ..)

and the display tuple is READ-ONLY on a tuning object (no const literal in the fn),
with the raw field read elsewhere as a sibling.  (P28C sets animation_raw_display_name,
which is a SEPARATE sibling -> explains "raw='TEST299' but UI still English".)

NEW (commit):
  * scripts/ww_p29a_display_origin_trace.py -- whole-ts4script READ-ONLY native-marshal
    audit (magic-verify every .pyc == 420d0d0a, native marshal, NO xdis):
      - who STOREs animation_display_name / animation_raw_display_name across ALL
        members: ANIMATION_DISPLAY_NAME_WRITERS= / ANIMATION_RAW_DISPLAY_NAME_WRITERS=
        with MODULE= FUNCTION= BYTECODE_CONTEXT= per writer
      - _create_sex_animation_instance param list + ANIMATION_TUNING_SOURCE
        (ctor-parameter vs local) + ANIMATION_TUNING_TYPE_HINT / CREATED_BY (the
        member::fn that STORE-writes the tuning fields is the creator signal; py3.7
        marshal has no co_qualname so exact class name is honestly noted as not named)
      - RAW_TO_ANIMATION_DISPLAY_RELATION = DIRECT|TRANSFORMED|INDEPENDENT|UNRESOLVED,
        decided from real per-function mini-VM producer maps (NOT fragile +/-N op
        windows that conflated adjacent statements), with REL_REASON bytecode evidence
      - XML/tuning keys: XML_KEY_FOR_ANIMATION_DISPLAY_NAME / _RAW_DISPLAY_NAME
        (literal const vs NOT_LITERAL / ATTRIBUTE_DERIVED) + XML_KEY_LITERAL per key
  * scripts/ww_p29a_display_origin_trace.ps1 (NEW) -- one-key runner on the REAL box:
        powershell -ExecutionPolicy Bypass -File .\scripts\ww_p29a_display_origin_trace.ps1
    param/[CmdletBinding()] first, 2> to %TEMP% stderr, no &&, no $Args.
  * ww_p29a_display_source_trace.py: FIXED the callsite false negative (Task 5).
    The mini-VM now reconstructs CALL_FUNCTION_KW (3.7: the kw-NAMES tuple sits on top
    and is NOT part of argc -- pop it first, remaining n are the arg VALUES, callable
    below).  Real WW uses CALL_FUNCTION_KW, previously the display arg was reported
    UNRESOLVED(no SexAnimationInstance CALL in fn); now emits CTOR_CALL_OFFSET/CTOR_ARG[i]
    and DISPLAY_NAME_ARGUMENT_TO_CTOR=L:display_name for CALL_FUNCTION/CALL_FUNCTION_KW/
    CALL_METHOD alike.
  * ww_p29a_ps1_static_check.py: registered ww_p29a_display_origin_trace.ps1 (7 ps1).
  * ww_p29a_wintest.py: new ORIGIN mechanism gate (codes["ORIGIN"]) building DIRECT/
    TRANSFORMED/INDEPENDENT loader fixtures + a CALL_FUNCTION_KW loader, asserting
    RAW_TO_ANIMATION_DISPLAY_RELATION=DIRECT/TRANSFORMED/INDEPENDENT and that the KW
    call yields DISPLAY_NAME_ARGUMENT_TO_CTOR=L:display_name (never UNRESOLVED).

Verified offline: origin tracer compiles + runs under the REAL 3.7.9; DIRECT/
TRANSFORMED/INDEPENDENT each reproduce on fixtures; cross-member writer attribution
works; CALL_FUNCTION_KW callsite fixed (CTOR_ARG[1]=L:display_name on a kw-only ctor).
All wintest gates green (MAGIC/PS1/STATIC/LOGIC/PY37/PY37ARGV/LIVECLS/DISP/ORIGIN/
BUILD -> P29A_WINTEST=PASS).  ps1 static: 7/7 STRUCT_BALANCE=PASS.

Deploy NOT run (read-only per user).  No P29-B, no new canary, no WW/Nevely/P28C/P24,
no Chinese.  Dorothy: git pull, run the origin ps1, paste back
ANIMATION_*_WRITERS / ANIMATION_TUNING_SOURCE / XML_KEY_* / RAW_TO_ANIMATION_DISPLAY_RELATION.

## P29-TUNING RUN-TIME __init__-FREE LOADER TRACE (2026-09-04 18:37) -- observation only

Authoritative real static (Dorothy, 18:37) RETIRED the "raw is the wrong field / raw
derives display" assumptions for good:
    CALLER_FUNCTION=_create_sex_animation_instance   FN_PARAMS=animation_tuning, animation_override
    DISPLAY_NAME_STORE_PATTERN= animation_tuning.animation_display_name -> local display_name
    RAW_FIELD_READ_PATTERN= LOAD_ATTR animation_raw_display_name
    ANIMATION_DISPLAY_NAME_WRITERS=(none)  ANIMATION_RAW_DISPLAY_NAME_WRITERS=(none)
    XML_KEY_FOR_ANIMATION_DISPLAY_NAME=NOT_LITERAL/ATTRIBUTE_DERIVED
    XML_KEY_FOR_ANIMATION_RAW_DISPLAY_NAME=literal 'animation_raw_display_name'
    DISPLAY_NAME_OVERRIDE_BEHAVIOR= override_wins_else_base
Both fields are RUNTIME attrs of the same animation_tuning object; WW has no STORE
writer -> relation is decided at tuning/parser/dynamic-descriptor layer -> ONLY
observable at runtime. So: NO SexAnimationInstance.__init__ hook.

NEW ww_p29_tuning_mod.py (+ deploy/read_log/rollback/report_check ps1+py).  Hooks the
REAL module-level loader  wickedwhims.sex.animations.animations_loader._create_sex_animation_instance
(animation_tuning, animation_override) by REBINDING the module attr + re-pointing any
already-imported alias binding; calls the ORIGINAL with identical args + returns its
value untouched; reads BEFORE  TUNING_TYPE/TUNING_MODULE/RAW_ATTR/DISPLAY_ATTR/
ANIMATION_OVERRIDE_PRESENT and AFTER RETURN_INSTANCE_DISPLAY_NAME/
RETURN_INSTANCE_DISPLAY_NAME_OVERRIDE/AUTHOR/ANIMATION_NAME/ANIMATION_IDENTIFIER,
all read-only.  Match keep = raw/display/return carries TEST299 or "Caught Cheating 1".
Verdicts (only on real marker equality, never fabricated):
    A raw=TEST299 disp=OLD            -> P29_RESULT=RAW_CHANGED_DISPLAY_DERIVED_OLD
    B raw=OLD disp=OLD                -> P29_RESULT=OVERRIDE_NOT_PRESENT_IN_RUNTIME_TUNING
    C raw=TEST299 disp=TEST299 ret=TEST299 -> P29_RESULT=TUNING_AND_INSTANCE_CORRECT
    other marker combo                -> P29_RESULT=MATCH_TARGET_OTHER_PATTERN (truthful)
    D target never seen (post-session, from the real log) -> report_check emits
      P29_RESULT=TARGET_TUNING_NOT_OBSERVED  (never guessed mid-run)
Discovery/timing reuses the P29-A-proven real scheduler (module appears in sys.modules
LATE -> in-world retry installs; NO fake "deferred schedule active"):
    HOOK_INSTALLED=YES/NO  HOOK_MODULE=...  RETRY_COUNT=...  HOOK_TARGET=...

REUSE (zero change to P29-A behavior): generalized ww_p29a_build_ts4script.py
(--member/--probe-attr/--probe-mod, defaults = P29-A) and ww_p29a_build_on_win.ps1
(-SrcMod/-OutTs4 TUNING mode) so the same magic-matched compiler + py37 gate drive
the tuning ts4script.  Registered 3 ps1s in ww_p29a_ps1_static_check (10 ps1).
wintest: +TSTATIC/TLOGIC/TBUILD gates -> P29A_WINTEST=PASS (13 gates).

Deploy NOT run (observation-only; user runs on real box).  No P29-B UI hook, no
animation_display_name edit, no new package canary, no P24, no Chinese, no WW/Nevely
source change.  P28C TEST299 auto-deploy is the already-verified artifact; rollback
restores base via P28C rollback when flagged.
Dorothy one-key:  git pull ; powershell -ExecutionPolicy Bypass -File .\scripts\ww_p29_tuning_deploy.ps1
                  (launch, trigger Nevely ordinal 299) ;
                  powershell -ExecutionPolicy Bypass -File .\scripts\ww_p29_tuning_read_log.ps1
                  powershell -ExecutionPolicy Bypass -File .\scripts\ww_p29_tuning_rollback.ps1
