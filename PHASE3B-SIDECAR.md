# Phase 3B-SIDECAR — 独立汉化覆盖包 (Dorothy 汇报 + 推荐 writer 方案)

日期: 2026-08-14
状态: 方向已转正 — 不再修改原 Pose package,改为生成**独立中文覆盖包 (sidecar override)**。
      S4S 实机已验证: 修改原包 CHS STBL 后游戏能启动并显示中文。

## 0. 为什么 sidecar 是对的 (MVP 验收)
原包完全不动;新生一个只含简中 STBL 的小包放 Mods。游戏加载时按
**group + instance + locale** 命中 sidecar STBL 覆盖原包同名 STBL。
标准汉化 mod 正是此机制。

MVP 验收 (Embracing 只这一个包):
- 原 `...t0nischwartz.package` + `!Embracing_Faces_CHS_TEST.package` 同时放 Mods
- 游戏正常启动;第一层 "相拥";第二层 "左/右";描述中文;原 Pose 功能正常

第一版测试文本:
- Embracing Faces -> 相拥
- Left -> 左
- Right -> 右
- A pose for two sims based off the meme -> 基于该迷因的双人姿势
- 作者 t0nischwartz 保持原样

## 1. 审计 (任务 A) — 成熟汉化 sidecar 结构
脚本: `scripts/audit_sidecar.py` (纯只读, 不碰原包)
```
python scripts\audit_sidecar.py "D:\projects\sims4_trans\Mods_sample"
```
对 `!simkatu_music_..._CHT_CHS_ABonnie.package`、`!Tmex-TOOL_CHT_CHS_ABonnie.package`
输出: resource count / Type/Group/Instance / locale / 是否只有STBL / 是否同时含 CHS+CHT。

已本地验证脚本正确 (fixture: 多locale / CHS单STBL 均准确)。**待用户 Windows 端跑真实汉化包**。
审计回答的关键问题: 成熟汉化是否 = 独立 STBL override; STBL TGI 是否与原 mod 对应; 是否单包同时含 CHS/CHT。

## 2. 推荐 writer (任务 B) — 复用成熟 s4pi, 不手写 serializer
✅ 仓库已自带 **`lib/s4pi_src`** = s4pe 底层 s4pi 库源码 (= S4S/成熟工具同源)。这就是复用对象。

已核对 s4pi 权威 STBL/包序列化:
- **`StblResource.cs`**: 权威 STBL 读写。序列化时 **`stringLength@17-20` 会回填实际大小**
  (Σ(每 key UTF-16 长+1)), 而我们之前 fixture/原包此处=0 => 这是 golden 对照需重点盯的差异点之一。
- **`Package.cs SaveAs`**: 索引压缩元数据约定 `Compressed=0x5A42`(压缩) / `0x0000`(不压),
  `Filesize=Memsize` 当不压缩。索引类型按 type/group/instance 去重降维 (indexType)。
- **`Package.AddResource(rk, stream, rejectDups)`** / **`GetResource(rc)`** / **`SaveAs(path)`** 齐备。

**推荐方案: 独立 C# console helper (依托 s4pi)**, 流程:
1. `NewPackage` 建空包
2. 读原包 CHS STBL (inst 0x014EACCF17C8B091) → `StblResource` 改 4 条文本
3. `AddResource(原TGI, STBL流, true)` — 保持 group=0x80000000 + 完整 instance
4. `SaveAs("!Embracing_Faces_CHS_TEST.package")`
→ 完全复用 s4pi 序列化, 零手写 DBPF。

⚠️ 约束: s4pi 目标 .NET Framework 4.0, 需 **Windows** 下 msbuild/Visual Studio 编译。
本 Linux 机器无 dotnet/mono, `patch_stbl.py` 的 C# helper 构建**必须在 Windows 端**。

## 3. 落地路径 (建议三选一)
- **P1 (推荐, 全自动)**: 我在仓库写 `sidecar_builder/` C# 工程 (引用本地 lib/s4pi_src),
  用户 Windows 端 msbuild 编译后单命令跑: 读原包 + 读 `mvp_map.csv` + 出 sidecar。
  Python 只管 extraction/mapping/translation/QA, C# helper 只管序列化 — 严格分层。
- **P2 (零编译)**: 用户 s4pe GUI 手工做一次 golden sidecar; 我们逐字节解析其输出,
  把 s4pi 的实际写盘布局固化成我们的 "writer 规范", 再回填给 patch_stbl。 (慢, 但无需 Windows 编译)
- **P3 (最省事验证)**: 先用 S4S GUI 手动生成 1 个 sidecar 做 MVP 实机验收, 之后再补自动化。

**推荐 P1**, 但 MVP 第一关 (先证明 sidecar 机制成立 + 游戏能显示中文) 可先用 P3 快速验证,
同时我并行准备 P1 的 C# 工程。

## 4. 当前冻结规则 (沿用)
- 不跑 659 包; 不碰 frozen 翻译 (TOTAL=1968/override=114/PASS=1722/REVIEW=246/ERROR=0/miss=0)
- 不覆盖任何原 package; 不碰 Animation
- 自制 `patch_stbl.py` DBPF 写回已冻结, 不再 test6/test7
- 先出 审计结果 + writer 方案, 给 Dorothy, 获批准后再继续

## 5. 下一步 (待批准后)
1. ~~Windows 跑 `audit_sidecar.py` 审计~~ -> 已完成, sidecar 模式确认
2. ~~S4S 手工 sidecar MVP~~ -> 已完成, 实机 PASS
3. (进行中) P1 C# helper `sidecar_builder/` 自动化

## 6. P1 C# helper — Windows 编译与运行 (最小工程)
工程: `sidecar_builder/` (Program.cs + SidecarBuilder.csproj)
复用: 仓库 `lib/s4pi_src` (s4pi 源码工程, 成熟 writer; 序列化零手写)
依赖闭包: SidecarBuilder -> Package + StblResource -> Interfaces + Settings + CS System Classes
(CS System Classes 产出 System.Custom.dll; 各 ProjectGuid 已按真实 csproj 核对)

## 7. 生成 sidecar 前最后一道准确率验证 — Tibo131 单包精确映射
脚本: `scripts/audit_tibo_exact_map.py` (只读, 不生成任何包)

功能: 把 Tibo131 PosePackInstance XML 的玩家可见字段【精确 join】到包内全部 STBL key,
      逐 key 输出 TRANSLATE / KEEP / UNMAPPED_UNCERTAIN 三类判定。
铁律: 【不】按 STBL 文本长相直接判 TRANSLATE —— 判定只来自 XML 结构引用
      (pose_display_name 等 DISPLAY 类字段哈希引用 → TRANSLATE;
       creator/pose_name 等 AUTHORISH 类 → KEEP; 无引用 → UNMAPPED_UNCERTAIN 绝不猜)。

Windows 跑 (先 git pull):
```
cd D:\projects\sims4_trans
```
```
git pull
```
```
python .\scripts\audit_tibo_exact_map.py --dump-xml "<Tibo131_PosePack 2.package 完整路径>"
```
人工保护/作者 key 可加 --keep (当 XML 无结构引用但已知该 key 属作者/包标题时):
```
python .\scripts\audit_tibo_exact_map.py --dump-xml --keep 0xXXXX:作者Tibo131 "<包>"
```
输出: 终端 + `output\tibo131_exact_map.csv`。

特别核对项 (用户在生成前要求):
- Tibo131 (作者) → 应 KEEP
- "Tibo131 Standing Pose Pack #2" → 查是否 PACK_TITLE (XML 结构里 pack_title 字段, 通常为明文非哈希)
- Pose 1/2/... → 查是否 pose_display_name 哈希引用 → TRANSLATE
- J → 无结构证据不得翻 (UNMAPPED/UNCERTAIN)

fixture 回归: `scripts/_tibo_fixture.py` 生成含作者+包标题+3个 pose 显示名的测试包,
  已本地验证: pose_display_name→TRANSLATE, 作者/包标题→KEEP(override), 孤立 J→UNMAPPED。

## 编译 (Windows, .NET Framework 4.0; 仅依赖仓库内容 + NuGet restore)
已内建 MSB3644 修复: 工程引用 Microsoft.NETFramework.ReferenceAssemblies 1.0.3
(自包含 net40 reference assemblies, 无需装 .NET 4.0 Developer Pack; 其 build targets 自动解析
FrameworkPathOverride / 禁用 FrameworkPathOverride / 显式引用 mscorlib, 沿 ProjectReference
transitively 传遍 vendored s4pi 工程)。

依赖: s4pi 源码已 vendored 到 `vendor/s4pi/` (固定 commit fff1936, GPL v3,
见 vendor/s4pi/README-S4PI-VENDOR.md), Windows git clone/pull 即可获得, 无需任何手动寻找。

⚠️ 用 Debug 配置 (勿用 Release): 上游 s4pi 的 PreBuildEvent 硬编码
`bin\Debug\CreateAssemblyVersion` (生成 Properties\AssemblyVersion.cs), 仅 Debug 可命中;
Release 会因找不到该 exe 而失败。Debug 对构建工具已足够。

### 一键构建脚本 (固化; 唯一需要的命令)
```
cd D:\projects\sims4_trans
```
```
git pull
```
```
powershell -ExecutionPolicy Bypass -File sidecar_builder\build_sidecar_builder.ps1
```
脚本自动完成 (Windows MSBuild 18.4 实测):
1. 用 vswhere 定位最新带 Microsoft.Component.MSBuild 的 MSBuild (不在 PATH 也能找到; 找不到明确 fail)
2. Restore (恢复 SidecarBuilder 的 PackageReference)
3. 动态解析 net40 reference assemblies:
   `%USERPROFILE%\.nuget\packages\microsoft.netframework.referenceassemblies.net40\1.0.3\build\.NETFramework\v4.0`
   (遵守 NUGET_PACKAGES; 不硬编码用户名/本机绝对路径)
4. 把 `FrameworkPathOverride=<net40路径>` 作为【全局 MSBuild property】与 SolutionDir 一起传给 Build → 传播到所有 vendored ProjectReference child (旧式 csproj 不靠 PackageReference 自动传播, 必须全局传)
5. 自动编 CreateAssemblyVersion (Debug/AnyCPU/FrameworkPathOverride) → 自动编 5 个 vendored s4pi 工程 → 自动编 SidecarBuilder
产物: `sidecar_builder\bin\Debug\SidecarBuilder.exe`

> 不需要用户: 手工设 PATH / 手工传 FrameworkPathOverride / 手工传 SolutionDir / 手工先编 CreateAssemblyVersion。

---

## 生产 writer 接口 (COMPLETE-STBL, 已实现)
`SidecarBuilder.exe` 现为【正式完整 STBL writer】: 以整张原始 CHS STBL 为基线,
只替换精确批准的 key value, 其余全量保留, 生成独立 override 包。

输入:
```
bin\Debug\SidecarBuilder.exe \
  -source  <source_package>      # 原 mod 只读打开
  -out     <output_path>         # !<mod>_CHS.package
  -type    0x220557DA            # 目标 STBL 精确 Type
  -group   0x80000000            # 目标 STBL 精确 Group
  -inst    0x...                 # 目标 STBL 精确 Instance (唯一 TGI 锁定)
  -locale  0x01                  # 可选: 校验 instance 高字节==0x01 (CHS_CN), 不符 fail
  -m       KEYHASH:EXPECTED:VALUE   # 可多次: 仅当该 key 当前文本==EXPECTED 时改为 VALUE
```
> exact TGI 用于唯一锁定目标资源 (找不到/多个 → fail)。`-locale` 与 exact-instance 双重校验。

### Writer 流程 (12 步, 已实现于 Program.cs)
1. 只读打开 source_package (`Package.OpenPackage(1, path, readwrite:false)`)
2. GetResourceList 中按精确 Type/Group/Instance 找唯一目标 STBL (≠1 → fail)
3. `((APackage)src).GetResource(targetEntry)` 取原始 STBL stream
4. `StblResource.StblResource` 解析完整 STBL
5. 记录 source: Version / IsCompressed / Reserved / Entries.Count / 每项 KeyHash/Flags/StringValue
6. 对每个 modification: key 必须唯一存在, StringValue 必须 == expected_source, 否则 fail-fast 不写包
7. 仅修改已有 StringEntry.StringValue; 不 new/delete/reorder entry, 不改 KeyHash, 不改 Flags
8. 新建 Package, 只加入修改后的完整 STBL (非 partial), TGI 与 source 完全一致
9. SaveAs output
10. reopen output
11. verify: resource_count==1, TGI exact, Version 同, entry_count 同, KeyHash 集合/顺序同,
    Flags 全同, modified values==target, untouched values==source
12. 输出 manifest / 验证摘要 (VERIFY=PASS/FAIL; 失败 return 2)

### API 依据 (已核 vendored 源码, 未猜):
- `StblResource.StblResource` 嵌套 `public class StringEntry`/`StringEntryList`; 完整类型
  `StblResource.StblResource.StringEntry` (namespace.StblResource class.StringEntry)
- `IPackage` 无 `GetResource`; `GetResource(IResourceIndexEntry)` 是 `APackage` 抽象成员
  → 需 `((APackage)src).GetResource(e)` 调用
- `IPackage` 有 `GetResourceList`(List<IResourceIndexEntry>) 与 `AddResource(IResourceKey,Stream,bool)`
- `TGIBlock(int,EventHandler,uint type,uint group,ulong instance)` 实现 IResourceKey
- `AResource.Stream` 在 dirty 时重跑 `UnParse()`; `StringValue` setter 触发 OnElementChanged
  → 改完值后 `stbl.Stream` 返回重新序列化的完整 STBL

---

## ⚠️ 已淘汰: -k partial 模式 (serializer smoke test)
旧版 `-k key:value` 是【新建空 STBL、只加命令行传入 key】的 partial STBL, 已从生产路径淘汰。
严禁用于生产 pipeline / 严禁进游戏。/ 如需验证 s4pi 序列化链路, 请改用上面的正式输入
(以任意含 CHS STBL 的 source 跑一遍并通过 VERIFY)。

### 硬性禁止
- 禁止根据文本扫描决定修改
- 禁止 source-text-only join
- 禁止 partial STBL (只含部分 key 的 STBL 不进游戏)

### Tibo131 期望验收数 (canary 阶段)
- source CHS entries = 54
- 输出 sidecar CHS entries 也必须 = 54
- TRANSLATE 36 可修改
- KEEP 1 不动
- UNMAPPED_UNCERTAIN 17 不动


---

## 🔬 Forensic: 为什么 s4pi SaveAs 产物游戏拒收 (证据链, 2026-08-15)

**现状**: 自动 s4pi COMPLETE-STBL sidecar — VERIFY=PASS + 独立 AUDIT=PASS, 但游戏加载 FAIL;
人工 S4S sidecar 游戏加载 OK。翻译文本不同(raw payload 不必相同); 差异在 container/index/metadata。

**工具**: `scripts/dbpf_forensic.py` (只读) 输出 raw-byte/header/index/STBL 层逐字段六进制对照,
分类 [PAYLOAD]/[CONTAINER]/[S4S-WROTE]/[S4SPI-WROTE]。用法见 %HEAD%。

### 已核 vendored s4pi `Package.SaveAs` 行为 (root cause 候选, 非猜测):
- 先 `w.Write(header)` 整体写 96-byte header (来自 source 打开时拷贝), 再只 patch:
  - count @0x24 (setIndexcount: 写 pos 36)
  - index_size @0x2C (setIndexsize: 写 pos 44)
  - index_offset @0x40 (setIndexposition: 先写 0 到 pos 40(0x28), 再写 indexpos 到 pos 64(0x40))
  - unused4 @0x3C 强制 = 3 (setUnused4)
- **indexType 自动判并 = 对单 STBL 包恒为 0x07**:
  `(InstHigh 唯一<=1 ? 0x04) | (Group 唯一<=1 ? 0x02) | (Type 唯一<=1 ? 0x01)` → 0x07
- `PackageIndex.Save` 对置位字段做【类型去重】(值写一次到 index header, 每 entry 行省略该字段,
  只写剩余字段: inst_low + Chunkoffset + Filesize + Memsize 等 16B/entry)
- 压缩标记: 只当 `value.Length < Memsize` 才置 `Compressed=0x5A42`(ZB/zlib), Filesize=压缩长;
  未压缩则 `Compressed=0x0000, Filesize=Memsize`

### 头号嫌疑 (待 forensic 原始字节证实):
**s4pi 输出 indexType=0x07 的 type-bit 去重索引 (s3pi 遗留格式), 而 TS4 游戏 loader 与 S4S
期望 flat 32B/entry 索引 (indextype=0x0000)。** dbpf_fast (canonical TS4 parser) 读 flat 索引。
若 forensic 的 INDEX_TYPE 显示 A(S4S)=0x00000000 flat 而 B(s4pi)=0x00000007 去重 → 实锤。

另两个待核疑点:
1. unused4 被 s4pi 强制 =3, S4S 的 golden 若为不同值 → CONTAINER 差异
2. minor 被 s4pi 归一为 1 (CheckHeader 要求), S4S 若写其它 minor → 差异

### 决策门 (用户锁定):
**forensic 若不能给出有证据的、明确字段级修复 → 不再维护自制 s4pi writer。**
下一主线 = S4S-compatible serializer (复用 S4S 自身 Save 程序集/API/DLL, 或调用其 serializer)。
冻结: Tibo / 10/50/659 / Animation / s4pi 参数猜测 / 翻译与 mapping 层不动 (mapping+COMPLETE-STBL 保留)。

---

## ✅ 根因实锤: 旧 s4pi STBL stringLength UTF-8 低估 bug (2026-08-15, forensic 确认)

**字段级证据 (非猜测)**: forensic 显示 s4pi 输出 STBL header `stringLength=36` 而实际
string data = Σ(UTF8 byte count + 1) = **70**。S4S golden = 52 与 Σ 完全一致。

**根因链 (vendored s4pi `StblResource.cs` 核实)**:
- `StringEntry.EntrySize` 旧实现: `(uint)(StringValue.Length + 1)` → 用 .NET `string.Length`
  (UTF-16 code unit, 中文每字=1)
- 实际 `UnParse` 写 entry 用 `Encoding.UTF8.GetBytes(StringValue)` + `(ushort)str.Length`
  (中文每字=3 字节)
- `UnParse` 累加 `actualSize += entry.EntrySize` 并写回 header `stringLength`
  → 中文场景 stringLength 系统性低估 → 游戏读 STBL 越界/截断 → 拒载
- S4S 用 UTF-8 byte count 得 52 (正确); 旧 s4pi 得 36 (低估)

**单变量 backport (仅改 1 处, 其余全不动)**:
- `EntrySize` → `(uint)Encoding.UTF8.GetByteCount(StringValue) + 1`
- 不改: PackageIndex / indexType / SaveAs / DBPF header / writer mapping / COMPLETE-STBL 流程

### 进游戏前 forensic 必须证明 (验收门)
```
INDEX_TYPE = 0x00000007   (保持不变)
output entries = 5
stringLength = 70          (必须修正, 与 Σ 一致)
only 0x763F7534 changed
AUDIT=PASS
```
- 若 stringLength=70 且 INDEX_TYPE 仍 0x07 的新 canary **游戏能启动** → 实锤根因=该 UTF-8
  length bug; INDEX_TYPE 0x07 对该 canary 不是 blocker
- 若仍无法启动 → stringLength bug 已修但还有第二个问题 → 才进入 INDEX_TYPE flat-index 单变量实验

### 新增硬校验 (audit 工具)
- `audit_canary_pair.py` / `dbpf_forensic.py` 现输出 `STRING_LENGTH`(header) 与
  `CALCULATED_STRING_DATA_LENGTH`(= Σ UTF8+1), 二者必须相等; 不等 → 硬错
  (dbpf_forensic 归 [CONTAINER], 不再自动归 [PAYLOAD] normal)

---

## 🗺 659 CONFIRMED_POSE 只读 coverage + 10-cohort 选择 (2026-08-15)

**新增 `scripts/pose_coverage.py`** (只读, Windows 上跑真实包; 本地仅白盒合成验证):

- 输入: `--list <文件>` (一行一包) 或默认 `output/pose_verification.csv` 过滤 `POSE_VERIFIED`
- 每包覆盖列 (全输出到 `coverage.csv`):
  package_path / file_size / PosePackInstance_count / STBL_count_total /
  CHS_0x01_exists / CHS_target_STBL_count / CHS_target_TGI(s) / CHS_entry_count /
  pack_title_ref_count / pack_description_ref_count / pose_display_name_ref_count /
  exact_structural_translate_count / keep_count / unmapped_uncertain_count /
  STBL_version / compression_state /
  non_ascii_source_present / long_string_present / repeated_source_text_present /
  multiple_target_STBL_families / status / reason
- player-visible model: PACK_TITLE(display_name/title/pack_title) · PACK_DESCRIPTION(description)
  · POSE_DISPLAY_NAME(pose_display_name) · KEEP(creator_name/creator/author) ·
  非UI默认不翻 (pose_name/sort_name/internal)。不按“像英文”判 TRANSLATE, 只认结构引用。
- status: ELIGIBLE_EXISTING_CHS / SKIP_NO_CHS / SKIP_AMBIGUOUS_TGI /
  SKIP_MAPPING_UNCERTAIN / ERROR。缺 0x01 CHS 一律 SKIP, 不自行创建、不推导 TGI。
- cohort: 程序化选 10 (确定性 tie-break, 非随机/非人工名字), 类别缺则 NOT_PRESENT_IN_CORPUS,
  不伪造: ①最小 ②最大 ③中等 ④多PosePackInstance ⑤多family/多target ⑥title+desc
  ⑦仅pose_display ⑧非ASCII ⑨长字符串 ⑩repeated/protected, +压缩包(若存在)。
- 输出: `coverage.csv` / `cohort_selection.csv` / `coverage_report.md`
- 本阶段只读: 不生成 10 sidecar / 不写 Mods / 不批 50/659 / 不碰 Animation /
  不处理缺 CHS 创建规则 / 不动 frozen 9061 层。

**白盒验证 (合成 corpus)**: 5 状态全分类对; 四态 scan 端到端对 (ELIGIBLE: PPI=1,
trans=4 但 title 精确计数=1 不再被 pose_display_name 误吞 / NO_CHS / MAPPING_UNCERTAIN /
pose_list 兜底); main() 产出 3 文件; cohort 类别缺则 NOT_PRESENT (不伪造)。

---

## 🎯 Cohort Sidecar Production 生成 (2026-08-15) — frozen 10 包

**cohort_selection.csv 已冻结** (rows=10 / distinct=10 / nonEligible=0 / missing=0), 不再改 selector。

**新增 `scripts/gen_cohort_sidecars.py`** (Windows 上跑; 只生成+离线验证, 不写原包/不写 Mods/
不做游戏启动/不碰 Animation):

- 读 frozen `cohort_selection.csv` 每行 (source_package)
- 每 source 包: 复用 `pose_coverage` 冻结映射, 抽【位置门控】approved player-visible keys:
  PACK_TITLE(PosePackInstance-level display_name) / PACK_DESCRIPTION(description, 仅存在且 resolve)
  / POSE_DISPLAY_NAME(pose_list/pose 内 pose_display_name)
- 只认 exact existing 0x01 CHS target TGI; TGI 由实际 target STBL entry 派生
  (不猜、不推导、不创建); CHS_target_STBL_count != 1 一律 fail-fast
- 逐 approved key: 引用 hash 必须在 exact CHS target STBL key 全集内 (否则 unresolved fail-fast);
  target STBL 含重复 KeyHash 一律 fail-fast; source_text 取 target STBL 现值
- 译文解析优先级: `translation_overrides.csv`(T_<hash>_g1) -> `translation_done.csv` -> `translation_cache.db`
  ; 匹配键 = (translation_id, normalized source_text) 两者同时一致才命中 (与 frozen phase2b 一致);
     tid 由 `source_hash(norm_text(source_text))` 派生 (源文本 hash, 非 STBL KeyHash);
  ; TRANSLATE -> 有效译文, 进 SidecarBuilder `-m`, translated_key_count+1;
    KEEP -> 合法终态 (已审核决定保持原文), 不传 `-m`, COMPLETE-STBL 原样保留, keep_key_count+1, 不报错;
    MISSING / unresolved REVIEW / SOURCE_MISMATCH -> fail-fast (不生成该包);
    SOURCE_MISMATCH = tid 命中但 normalized source_text 不一致 (绝不静默取用错误译文);
  ; 不变式: translated_key_count + keep_key_count == approved_key_count; 且 modified_key_count == translated_key_count
- 强 preflight (启动即 FAIL, 禁止静默空表): 显式指定的 overrides/done/cache 必须成功加载;
    文件不存在 / 0 行 / schema 缺列 (overrides 需 translation_id,source_text,translation,action;
    done 需 translation_id,source_text,translation,status; cache 需 phrase_cache 表 +
    translation_id,source_phrase,translation 列) -> RuntimeError rc=2, 且在建 out-dir 之前执行。
- cache.db 按 translation_id (stable ID) join + 校验 source_phrase 一致 (不再按全文 source_phrase 精确查,
  因 cache 主键为 request_fingerprint 且存的是分段原子短语)。
- output-dir 防 stale: 目标 out-dir 已存在且非空 -> refuse/fail-fast (rc=2, 不自动删旧文件)
- 调 `SidecarBuilder.exe` (COMPLETE-STBL writer, 含 UTF8 EntrySize backport):
  `-m KEYHASH:SOURCE:TRANSLATION` 每 approved key 一条; writer 内部做 expected-source 校验
- 每个 sidecar 必须: writer VERIFY=PASS + `audit_canary_pair.py` AUDIT=PASS
  (独立只读二次审计: exact TGI / resource_count=1 / version·count·order·flags preserved /
   only approved keys changed / untouched 等价 / STRING_LENGTH==Σ(UTF8+1))
- 输出: `<out_dir>/<slot>_<basename>_CHS.package` (每 source 一个) +
  `<out_dir>/cohort_sidecar_manifest.csv` (slot/source/output/target_TGI/
  approved_key_count/translated_key_count/keep_key_count/modified_key_count/
  writer_verify/audit_result/error)

用法 (Windows, 短路径, 先 cd 仓库根):
```
python scripts\gen_cohort_sidecars.py --cohort output\cohort_selection.csv --out-dir output\cohort_sidecars --writer sidecar_builder\bin\Debug\SidecarBuilder.exe --overrides output\translation_overrides.csv --done output\translation_done.csv --cache output\translation_cache.db
```

白盒验证 (合成 corpus, 本机): approved_pv_refs 三类别齐全 + 唯一 CHS target; TranslationResolver
OVERRIDE/KEEP/MISSING 优先级正确; mixed(TRANSLATE+KEEP) 只改 translated keys → PASS;
all-KEEP → 不因 KEEP 报 missing → PASS; 真 MISSING → FAIL 不生成; stale 非空 out-dir → FAIL(rc=2)
不自动删旧文件; main() 端到端混合+全KEEP 2 包全 PASS + manifest 3 新增列 + 不变式成立。
加 (2026-08-15 诊断后): 强 preflight 5 项 (overrides/done 缺文件·0行·缺列 / cache 缺文件·缺列)
全 PASS; (tid,norm_source) 精确匹配 + KEEP保留 + source mismatch→SOURCE_MISMATCH fail-fast +
cache by translation_id 命中 全 PASS (白盒 12/12)。

只读资产诊断工具 (Windows, 对真实 frozen asset):
```
python scripts\diag_resolver_assets.py --done output\translation_done.csv --cache output\translation_cache.db
```
输出 done.csv 的绝对路径/exists/大小/BOM/headers/总行数/resolver 载入行数 + 6 个稳定ID
(T_b56169d01d20_g1 等) 的 exact lookup (row 是否存在/实际 source_text/norm/hash_ok/status/translation/
resolver 接受或拒绝原因), 以及 cache.db 的表/schema/行数 + 6 ID 的 by-tid/by-source_hash/
by-source_phrase 命中与 resolver 实际查询语义。只读, 不改 frozen data, 不生成 sidecar。

frozen decision-catalog gap inventory (Windows, 只读, 不加缓存不生成 sidecar):
```
python scripts\gap_inventory.py --catalog output\translation_catalog.csv --overrides output\translation_overrides.csv --overrides2 output\translation_overrides.final2.csv --done output\translation_done.csv
```
对最终 ELIGIBLE 包的 approved unique player-visible keys 全量 join 到权威 catalog
(translation_catalog.csv: translation_id/source_text/decision/source_hash), 按 unique source 分类:
  A CATALOG_KEEP / B CATALOG_TRANSLATE_RESOLVED / C CATALOG_TRANSLATE_MISSING_RESULT(异常) /
  D NEW_SOURCE_NOT_IN_CATALOG (再按 PACK_TITLE/PACK_DESCRIPTION/POSE_DISPLAY_NAME 拆分);
输出 unique 总数 + A/B/C/D 各多少 + 各字段类别 + 各影响 package 数 + 6 样本归属。
translation_cache.db 不作为最终译文 fallback。

Production 最终语义 (暂定, 待 gap inventory 后定): catalog decision=KEEP -> KEEP 合法不要求
done/cache; catalog=TRANSLATE/APPROVED -> 必须 override/done 有最终译文否则 FAIL;
不在 catalog -> NEW_SOURCE 不得自动猜译文。

== 增量 translation decision layer (真实 gap inventory 结果, 只处理 C+D) ==
真实 approved unique source = 3194: A CATALOG_KEEP=1122, B=1310,
C CATALOG_TRANSLATE_MISSING_RESULT=29 (全部 POSE_DISPLAY_NAME, 影响9包),
D NEW_SOURCE_NOT_IN_CATALOG=733 (PACK_TITLE=434, PACK_DESCRIPTION=199, POSE_DISPLAY_NAME=103)。
不改 resolver/writer/coverage/cohort, 不生成 sidecar; 本轮只做 catalog/decision, 不调模型。

C 类 29 条 -> translation_missing_result.csv (只读提取, 已是 frozen TRANSLATE, 不重新分类,
后续只需补最终译文):
```
python scripts\c_extract.py --gap output\gap_inventory.csv --out output\translation_missing_result.csv
```
列: translation_id, source_text, source_hash, old_catalog_decision, provenance,
affected_package_count, packages

D 类 733 unique source -> translation_delta_catalog.csv (用与原 phase2a_catalog.py 完全相同的
decision 规则 reclassify: 复用 phase2a_samples.classify + classify_with_context, 不重写规则;
不修改原 translation_catalog.csv):
```
python scripts\d_reclassify.py --gap output\gap_inventory.csv --out output\translation_delta_catalog.csv
```
列: translation_id, source_text, source_hash, decision, reason, provenance, ref_count,
package_count, packages; stable ID 继续 T_{source_hash}_g1 (禁 STBL KeyHash);
同一 source 多 provenance 时仍一条 unique source 但记录全部 provenance。
输出汇总: D 总数 / TRANSLATE / KEEP / REVIEW / 各 decision 按 provenance 分布 / 各影响 package 数 /
6 样本 delta decision。决策规则与 frozen 一致 (已验证: 'Pose 1'/'1'->KEEP(NON_SEMANTIC_TAG),
'Tibo131 Standing Pose Pack #2'->TRANSLATE(SEMANTIC_WITH_NUM), 'Gounafier's Pose Pack'->TRANSLATE)。

== set-loss 733->732 修复 (真实 gap D=733, POSE_DISPLAY_NAME 103->102) ==
Windows 真实确认: d_reclassify D=732 (PACK_TITLE=434, PACK_DESCRIPTION=199, POSE_DISPLAY=102),
比 gap D=733 (POSE_DISPLAY=103) 静默丢 1 条 POSE_DISPLAY_NAME unique source。
根因: d_reclassify 对 source_text 空/空白行 `if not src: continue` 静默跳过。
修复: 空/空白 source_text -> [HARD-FAIL] 打印全字段并 fail-fast (rc!=0), 不再静默丢;
尾部新增硬 invariant: D 输入 unique == delta 输出 unique, 违反即停止输出 (rc!=0),
并检查输出内 (tid,norm) 重复。

确定性 set diff (Windows, 只读, 定位 733->732 丢帧):
```
python scripts\set_diff.py --gap output\gap_inventory.csv --delta output\translation_delta_catalog.csv
```
A = gap class=D unique (tid, norm); B = delta 全部 (tid, norm); 输出 A 数/B 数/A-B/B-A +
A-B 每条完整字段 (translation_id/source_text repr/norm repr/source_hash/provenance/package) +
重放 d_reclassify 关键逻辑逐条判定丢弃机制 (EMPTY_SOURCE_TEXT / WHITESPACE_ONLY / 
NO_TRANSLATION_ID / NORMALIZATION_OR_TID_COLLISION), 不猜; 尾部 invariant A=='B'。
白盒: 空 source 行触发 HARD-FAIL (不静默), set_diff 定位 A-B=1 且机制=EMPTY_SOURCE_TEXT,
清洁路径 invariant PASS (输入 unique == 输出 unique)。

== EMPTY_SOURCE_NOOP 裁决 (Windows 实证确认 A-B = T_e3b0c44298fc_g1, src='') ==
该条 = 结构上 approved 的 player-visible key, 当前 CHS STBL value 为空; production 行为必须是
原样保留 (非待补 source, 非翻译缺失)。为避免为唯一一条空文本扩展 writer/orchestrator 状态机,
增量 catalog 显式记为 decision=KEEP, reason=EMPTY_SOURCE_NOOP, 保留
translation_id/source_hash/provenance/packages。

窄规则 (仅此条件可触发): norm_text(source_text) == '' -> KEEP / EMPTY_SOURCE_NOOP。
禁止把普通 MISSING / unresolved / unknown 自动降级为 KEEP (非空源仍走 frozen classify,
never EMPTY_SOURCE_NOOP)。

d_reclassify 已改: 空 text 不再 HARD-FAIL, 而是显式写入 translation_delta_catalog.csv
(decision=KEEP, reason=EMPTY_SOURCE_NOOP); D 输入/输出 unique invariant 仍严格相等
(输入 = 分类源 + EMPTY_SOURCE_NOOP); 单独打印 EMPTY_SOURCE_NOOP 计数。

白盒 (全部 PASS): empty source -> KEEP/EMPTY_SOURCE_NOOP 且字段保留;
ordinary missing non-empty source (居 'Standing Pose Left'/'1' etc) -> 绝不 KEEP/NOOP;
D 输入 unique == 输出 unique (含 empty 行)。预期重跑: D total=733, TRANSLATE=597,
KEEP=130, REVIEW=6, EMPTY_SOURCE_NOOP=1。

== 人工 REVIEW 裁决层 (6 条) + 最终待补清单合并 (631) ==
d_reclassify 的 6 条 REVIEW 已人工裁决: 5->TRANSLATE(附译文) + 1->KEEP(@ninawhims 作者handle)。
只新增一个增量人工裁决层, 不改 frozen translation_catalog.csv / coverage / cohort / writer / resolver。

1) scripts/m_review.py — 记录 6 条人工裁决 (只读合并 provenance/packages):
   输入 config/manual_review.tsv (tab: translation_id可空|source_text|final_decision|translation|reason)
   输出 output/translation_manual_review.csv (translation_id/source_text/source_hash/
   final_decision/translation/reason/provenance/package_count/packages)
   stable id 仍 T_{source_hash}_g1, 不信任手填 tid。窄校验 fail-fast: 源必须确为 delta REVIEW
   (禁止非 REVIEW 改判); final_decision 仅 TRANSLATE/KEEP; TRANSLATE 必须非空译文; KEEP 译文必空;
   输入==输出无重复。
```
python scripts\m_review.py --review config\manual_review.tsv --gap output\gap_inventory.csv --delta output\translation_delta_catalog.csv --out output\translation_manual_review.csv
```

2) scripts/final_todo.py — 合并 C + D-TRANSLATE + manual REVIEW->TRANSLATE 为最终待补清单:
   C(29, translation_missing_result.csv) + D TRANSLATE(597, translation_delta_catalog.csv)
   + manual TRANSLATE(5, translation_manual_review.csv) = 631。输出 output/translation_final_todo.csv
   (translation_id/source_text/source_hash/decision/reason/translation/provenance/package_count/packages)。
   硬 invariant fail-fast: todo unique 严格 == 29+597+5=631; REVIEW 未裁决必须 0 (delta REVIEW
   全部被人工裁决覆盖, TRANSLATE|KEEP 皆算覆盖); 组间 (tid,norm) 无重复; 631 不成立 -> 打印
   差异来源并停止, 不自动补数据。报告 REVIEW->KEEP=1, remaining REVIEW=0。
```
python scripts\final_todo.py --missing output\translation_missing_result.csv --delta output\translation_delta_catalog.csv --manual output\translation_manual_review.csv --out output\translation_final_todo.csv
```

人工裁决内容 (config/manual_review.tsv, 6 条):
  !Aylin Moss_Заразная любовь -> TRANSLATE !Aylin Moss_传染的爱 (T_e14c9f042f51_g1)
  @ninawhims -> KEEP (T_429855a0e4e8_g1)
  bedposes -> TRANSLATE 床上姿势 (T_dd992d915086_g1)
  solopose -> TRANSLATE 单人姿势 (T_4aba8e9d6dce_g1)
  разговорные позы в кровати, РЫЦАРЬ В ЦЕНТР КРОВАТИ! / talking poses in bed,... -> TRANSLATE 床上交谈姿势，骑士位于床中央！ (T_851d01b43295_g1)
  требуется стакан! (браслет справа) / need plastic cup (right bracelet) -> TRANSLATE 需要塑料杯！（右手手镯） (T_571dfb8eeb80_g1)

预期: manual out 6 (5T+1K, REVIEW=0); final todo unique=631 (C=29, D_TR=597, MAN_T=5),
REVIEW->KEEP=1, remaining REVIEW=0, MISSING 未裁决=0。全尺度白盒 PASS (合成 C=29/D_TR=597/REV=6/KEEP=1/NOOP=1 -> 631 严格成立)。

本轮只生成 decision 与 todo 清单; 不调用模型批量翻译, 不生成 sidecar, 不重跑10包。

== 增量翻译 workset (626) ==
decision 层冻结后, 真正需翻译流水线生成译文的只有:
  final todo = 631; manual pretranslated = 5 (已有人工最终译文, 禁再送模型);
  实际 model workset = C29 + D_TRANSLATE597 = 626;  626 + 5 = 631。
KEEP (manual KEEP @ninawhims / EMPTY_SOURCE_NOOP / D KEEP) 全部禁止进翻译。

script: scripts/bake_workset.py
  输入 --todo translation_final_todo.csv(631) + --manual translation_manual_review.csv(6)
  输出 output/translation_incremental_workset.csv (626)
  逻辑: workset = todo 中 decision==TRANSLATE 且非 manual pretranslated (按 stable tid+norm 精确排除);
  硬 invariant fail-fast: workset 严格==626; 626+5==631; (tid,norm) 无重复; source_text/source_hash exact
  (不重算不改, 直接透传); workset 全 TRANSLATE。manual pretranslated 保留在 final merge (不丢)。
  不调用模型 / 不生成 sidecar / 不重跑包; 复用 frozen glossary/overrides/protected spans/translation policy
  (Phase2B 翻译时沿用); 不重新生成旧译文; cache 不当 final QA 译文。
```
python scripts\bake_workset.py --todo output\translation_final_todo.csv --manual output\translation_manual_review.csv --out output\translation_incremental_workset.csv
```
白盒 PASS: todo=631(含5 manual-T) -> workset=626, 626+5==631, KEEP 排除, manual-T 排除,
source_text/source_hash exact 对应, provenance 分布报告。

== Overlap Audit + Batch Plan (626 唯一 tid, 每个 tid 为一翻译单位) ==
Windows 真实 provenance: PACK_DESCRIPTION=193, PACK_TITLE=407, POSE_DISPLAY_NAME=29, 和=629 != 626
-> 差异来自 multi-provenance source (同一 tid 跨多个 provenance)。启动翻译前只做一次只读 audit+batch:

script: scripts/batch_plan.py
  1) overlap audit: unique tid=626; single-provenance; multi-provenance + 各组合及数量
  2) deterministic batch plan: 每 translation_id 只分配到一个 batch (唯一单位),
     multi-provenance 只进一个 batch 禁重复; 优先级 POSE_DISPLAY_NAME > PACK_TITLE > PACK_DESCRIPTION
     (含 POSE -> POSE; 否则含 TITLE -> TITLE; 否则 DESC)。
  输入 --ws translation_incremental_workset.csv(626);
  输出 --out output/translation_batch_manifest.csv
        (translation_id, source_text, source_hash, provenance, assigned_batch)
  硬 invariant fail-fast: batch unique tid union == 626; batches 交集 == 0; missing == 0; duplicate == 0。
```
python scripts\batch_plan.py --ws output\translation_incremental_workset.csv --out output\translation_batch_manifest.csv --audit output\translation_overlap_audit.csv
```
实测(白盒, 629=626+3 场景): multi=3 (PACK_TITLE+POSE_DISPLAY_NAME), 单 prov=623;
batch: POSE_DISPLAY_NAME=29, PACK_TITLE=407, PACK_DESCRIPTION=190 (3 个 multi 归入 POSE 优先),
union=626, inter=0, missing=0, dup=0 -> PASS。确认任何 batch 均以 translation_id 为唯一单位,
同一 tid 无论几个 provenance 只送模型一次。

Phase2B 分批执行建议 (workset=626 + batch manifest PASS 后再启动):
  沿用现有 translation 流水线 (glossary/overrides/protected spans/policy 不变)。建议按
  assigned_batch 分批执行: batch_plan 已把每个 tid 唯一划分到 POSE_DISPLAY_NAME(29) /
  PACK_TITLE(407) / PACK_DESCRIPTION(190); 同 tid 跨多 provenance 只进一个 batch 不重翻。
  每批完成后跑 invariant: 该批已译 + manual 5 不重翻; 无重复 tid。模型只产译文草稿,
  final QA 须过 glossary+protected spans+人工 review, cache 仅作草稿参考不作最终。

**验证要求 (汇报)**: 10 个 sidecar 文件名 / 每包修改 key 数 / writer verify / independent audit / 是否全部 PASS。
