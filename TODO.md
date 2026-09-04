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


