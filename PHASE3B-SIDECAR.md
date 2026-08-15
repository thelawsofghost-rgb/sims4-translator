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

== scope 修正 (2026-08-15): --done 按批隔离 ==
phase2b_translate 原逻辑: --id-from-file/ONLY_ID 只过滤 need_translate(待翻译子集),
但 done 由所有 decided 行组装 -> 用整份 manifest+定位某批时, done 会泄出其它 batch 的行
(空译文 PENDING), 破坏按批隔离与 union-dup 校验。
修正: 当 ONLY_ID/ONLY_REGEX/ONLY_CATEGORY 任一启用, 计算 scope_tids(全部 decided 中命中 filter 的 tid),
done 仅写命中行, 其余不写出。未启用定位时 scope=None 全量, 旧行为不变。
白盒: 623 行 manifest + 29 POSE 定位 -> done=29 行、非 pose 泄出=0; 回归 69/69 不受影响。batch plan 冻结未改。

== Phase2B 最小改动接入 manifest (不另写翻译器) ==
phase2b_translate.py 新增两个 CLI flag (不新增翻译引擎, 不建新层):
  --todo <csv>  默认仍是 output/translations_todo.csv; 传入则直接以该文件为 workset/todo 源
                (本增量轮传 translation_batch_manifest.csv, 它是唯一 workset/batch source of truth)
  --done <csv>  默认仍是 translation_done.csv; 传入时写该路径, 避免覆盖 frozen 旧 done
  decision 缺列时 load_todo 缺省为 TRANSLATE; detected_language 缺失时自动 detect。
  glossary/protected spans/override/cache/QA/done 写回逻辑完全复用, 零改动。
manifest 无 context/decision/detected_language 列也能安全入流 (白盒验证)。

三个 batch 执行命令 (每批独立 --done, 各自产物; 先只跑第1批29):
先由 batch_plan 落盘每批 tid (每行一个, 供 --id-from-file):
  python scripts\batch_plan.py --ws output\translation_incremental_workset.csv --out output\translation_batch_manifest.csv --audit output\translation_overlap_audit.csv --tids output\batch_tids
  -> 生成 output\batch_tids\batch_POSE_DISPLAY_NAME.tids (29) /
     batch_PACK_TITLE.tids (407) / batch_PACK_DESCRIPTION.tids (190)
批1 POSE_DISPLAY_NAME (29):
  python scripts\phase2b_translate.py output --todo output\translation_batch_manifest.csv --done output\translation_done_batch_pose.csv --id-from-file output\batch_tids\batch_POSE_DISPLAY_NAME.tids
批2 PACK_TITLE (407):
  python scripts\phase2b_translate.py output --todo output\translation_batch_manifest.csv --done output\translation_done_batch_title.csv --id-from-file output\batch_tids\batch_PACK_TITLE.tids
批3 PACK_DESCRIPTION (190):
  python scripts\phase2b_translate.py output --todo output\translation_batch_manifest.csv --done output\translation_done_batch_desc.csv --id-from-file output\batch_tids\batch_PACK_DESCRIPTION.tids
  (multi-provenance 3 条已由 batch_plan 唯一分配到 POSE 批, 不会在其它批出现, 禁重复成立)

QA/invariant 每批:
  --id-from-file 只处理指定 tid; --done 独立落盘 -> union 可用三批 done 的 tid 并集核对 ==626,
  批次间交集 ==0, missing==0, duplicate==0。模型输出仅 draft: final QA 须过
  phase2b_qa.py (glossary/protected-span/source exact-match) 后才能进 final。

白盒: Phase2B 直接吞 manifest (decision 缺省 TRANSLATE, detected 自动) 且不碰 frozen
  translations_todo.csv/translation_done.csv; 旧路径回归 69/69 不受影响。

== TITLE 407 reconciliation + retry manifest + completion gate 修复 (2026-08-15, 二次修正) ==
真实统计 (Windows fe2996f 运行) + 用户裁决 (正交 ROW_STATE + PHRASE_FAILURE):
  A SAME_AS_SOURCE_SEMANTIC = 42
  B SAME_AS_SOURCE_NONSEMANTIC candidate = 0
  C TRANSLATED = 365
  D unresolved cache-miss phrase = 5   (D∩A=4, D−A=1)
  核对: 42 + 0 + 365 + 5 = 412; 但 rows=407, C=365 与 A/D 有重叠(部分 D∩A 语义已在 A)。
  TITLE 终态： 3 terminal KEEP + 2 manual final TRANSLATE + 37 semantic retry + 1 D−A retry + 364 QA
  核对: 3+2+37+1+364 = 407。
  engine run failed=8, 但 cache 重建仅确定 5 unresolved; 另 3 未经解释, 不声称已解决。
  后续翻译运行必须直接持久化 failed_phrase 明细, 不再靠 cache 反推。

retry_v2 真实运行 (2026-08-15, 36 行, 不重跑; 模型已停): rows=36 DONE=28 QA_FAIL=8
  empty=0 sameAsSource=8。8 QA_FAIL 已人工定稿入 manual-final; 28 DONE 为 accepted
  model result (其中 8 条又经人工内容修正入 manual-final, 见下)。remaining retry=0。

TITLE 407 最终唯一终态 (build_title_final.py, deterministic, 只读, 零模型):
  KEEP                      = 3
  manual final TRANSLATE    = 4 (先前) + 8 (retry_v2 QA_FAIL) + 8 (DONE 内容修正) = 20
  accepted model result     = 28 DONE − 8 (修正给 manual) = 20
  clean changed/QA          = 364
  核对: 3 + 20 + 20 + 364 = 407。 REVIEW/QA_FAIL=0, empty=0, dup=0, mismatch=0。

reconciliation 输入 (Windows 真实):
  --draft output/translation_done_batch_title.csv        (407)
  --retry output/translation_done_batch_title_retry_v2.csv (36)
  --keep  configs/title_terminal_keep.c26.csv            (3)
  --transl configs/title_manual_translate.c26.csv        (20)
precedence: manual final > terminal KEEP > retry_v2 accepted DONE > original clean。
HARD-FAIL: duplicate tid / source_text mismatch / retry 非 draft 子集 /
  retry_v2 QA_FAIL 或未裁决且不在 manual (不明覆盖) / 终态配置悬空 /
  计数核对 != 407。输出 output/translation_done_title_final.csv (不覆盖旧证据)。

B 类人工裁决 (configs/title_terminal_keep.c26.csv + configs/title_manual_translate.c26.csv):
  3 terminal KEEP:  simonly_VixenPoster#1/#2/#3 -> action=KEEP (不进 retry/workset)
  2 manual final TRANSLATE:
    RosieSimsie_NSFW_CouplePoses_AllYours -> RosieSimsie_NSFW_情侣姿势_全属于你
    motherlode_fight -> motherlode_打斗
    -> action=TRANSLATE + 人工定稿 translation, 禁止再送模型
  B→KEEP=3, B→TRANSLATE=2, REVIEW=0。禁止仅凭 underscore/camelCase 判 PACK_TITLE technical KEEP。

retry set (build_title_retry.py, 2026-08-15 修正口径):
  retry = (A − terminal_KEEP − manual_final_TRANSLATE) ∪ (D − A)
  = (42 − 3 − 2) ∪ (1) = 37 ∪ 1 = 38 unique tid
  白盒: retry unique=38, dup=0, terminal KEEP∩=0, manual final∩=0, 407核对 PASS。
  manual final 2 条绝不回 model workset。

TITLE 两层 reconciliation 下游可读 (2026-08-15 三次修正, production base 锁定 114):
  production base (frozen, 唯一, byte/content 不变):
    output/translation_overrides.csv = 114
  增量层 (仅以下三):
    configs/translation_overrides.c26_pose_keep.csv -> 26 pose C26 KEEP
    configs/title_terminal_keep.c26.csv             ->  3 title terminal KEEP
    configs/title_manual_translate.c26.csv          -> 20 title manual final TRANSLATE (4 先前 + 8 retry_v2 QA_FAIL + 8 DONE 修正)
  新 derived 文件由 build_override_overlay.py 生成 (只读 base, 绝不改 frozen):
    output/translation_overrides.production.csv
  合并保证 (用户裁决): deterministic + 幂等;
     同 tid 不同 normalized source        -> HARD-FAIL;
     同 (tid,ns) 不同 action             -> HARD-FAIL;
     同 (tid,ns) 不同 translation        -> HARD-FAIL;
     缺必需层 / 缺 source_text / action 非法 -> HARD-FAIL;
     报告各 layer 行数 + 各增量层 vs BASE 交集 + 增量层间交集 + 最终 unique
       (由真实输入验证, 不硬编码 145; 若全 disjoint 则 114+26+3+2=145)。
  实测 (Windows 真实审计, 用户锁定):
    final3(114)=canonical(114) 完全相同历史快照 -> 不 merge;
    final2(111)=canonical 严格子集             -> 不参与;
    final(95) 含 5 条旧值后被 canonical 修正   -> 禁止参与;
    root 22/73 = 早期历史资产                  -> 不作为 production input。
  CLI: python scripts/build_override_overlay.py output [--out <p>] [--no-write]
  下游显式传 .production.csv 读取 (不是改 frozen base)。

--- phase2b 消费 production overlay (集成, 2026-08-15) ---
  默认 phase2b_translate.py 读 frozen output/translation_overrides.csv(114) ——
    仅因生成了 production overlay(145) 不会自动改读。必须显式:
  python phase2b_translate.py output \
        --overrides output/translation_overrides.production.csv \
        --id <38 tids> --todo <manifest> [--engine none]
  -ORIDRES_PATH 生效时 OVER_FILE=该文件 (只读); 缺省回退 frozen (向后兼容)。
  不修改 frozen base114 / 不回写 145 / 不改变 scope-at-load /
   不改变 authoritative gate / 不改 POLICY-CONFLICT。
  retry preflight (加载后, 不调用模型):
    requested=38 scoped=38 unique=38 production_overrides_loaded=145
    terminal_KEEP_hit=0 manual_final_hit=0 authoritative_TRANSLATE=38
    硬校验: production=145 & KEEP/manual hit=0 & auth_TR=38, 不满足 exit 4。
  白盒 6 场景 PASS (flag 回退/接production/load145/retry不命中终态/invariant/反例)。

--- BUG1/2/3 修复 (2026-08-15 真实 retry 暴露, 全部白盒+regression 验证) ---
BUG1 invalid echo cache 不计 hit:
  - hit 路径 (load 循环): cached 译文 == normalized source phrase -> 视为 miss,
    不 materialize, 重翻; cache_echo_rejected 计数暴露在 [结果] 行。
  - materialize_from_cache: per-phrase echo -> 返回 (None,PENDING)。
  - 物理 row 保留, 读层忽略。
BUG2 completion gate 改 segment-level (不再只看整行):
  - 对每个 model-required semantic seg (j["pending"]):
      resolved 缺失 -> QA_FAIL;  resolved==source seg -> QA_FAIL;
    全部 resolved 且非 echo 才 DONE。
  - protected/glossary/明确 KEEP seg 不参与。
  - 捕获 "Wait... It's You! - Pose Pack"->"等等...是你！ - Pose Pack"(Pose Pack echo)
    与 "Emotions - Sad"->"情绪 - Sad"(Sad echo) 两个假 DONE。
BUG3 authoritative unchanged gate:
  - authoritative TRANSLATE + 整行 unchanged + 无 terminal KEEP/manual evidence
    -> QA_FAIL (不能因 classifier 判无 pending phrase 就 unchanged+DONE)。
改进 [结果] 汇总: rows/DONE/QA_FAIL/unique/empty/sameAsSource/cache_echo_rejected。
诊断 (deterministic, 无模型): scripts/diag_retry_segments.py
  python scripts/diag_retry_segments.py output --done output/translation_done_batch_title.csv
  输出 38 行 segment trace: tid/row_status/seg_idx/seg_type/source_phrase/
    resolved/resolution(cache|glossary|model|protected|UNRESOLVED)/required/echo
  汇总: cached semantic echo count / rows affected / partial untranslated rows。
验证: test_phase2b_regression.py 74 PASS (含 BUG1/2/3 回归); e2e --engine none
  严格 QA_FAIL + re-translate 白盒 PASS; diag stuck-echo 捕获 PASS。

--- BUG4 (2026-08-15 用户新诊断) ---
问题: required_translate / j["pending"] 对 PACK_TITLE creator/identifier 保护不完整。
      创作 token (simmer_creator)/(UNI)/creator prefixes 被当可译语义 -> 假 echo QA_FAIL。
  真实假阳性: (simmer_creator) - Male poses #1 / (UNI) Emotion Poses Adult;
  真实漏译照常: Wait... It's You! - Pose Pack(Pose Pack 未翻) / Emotions - Sad(Sad 未翻)。
修复 (精确可审计, 非宽泛启发式; 禁止: 首英文==作者/下划线都KEEP/title-case都翻):
  - 新增 frozen config configs/title_creator_prefix.c26.csv: 逐 source 锁定 creator 前缀
    (simmer_creator / (UNI) / xLienaEnna(post-bracket, 方括号已prot故只prot作者token) /
    Siimplysims / Loulicorn / Grownasssimmer(+Kaley 双词) / NA_ / Acha /
    RosieSimsie / motherlode / simonly)。
  - title_creator_protection(text) -> (prot_spans, reasons): 精确词首匹配 + 词边界,
    吞后置分隔符/空白保 rebuild 空界; 方括号 token 已由 _BRACKET_TAG 整体 prot 不重复。
  - split_semantic_spans(force_prot_spans): 起始前缀整体 prot + 剩余递归切分;
    中置 creator(如 ]xLienaEnna) 按段 token demote prot。
  - 生产路径 (jobs build) + materialize_from_cache + diag 全部先 title_creator_protection
    再 glossary/pending -> creator 不 required_translate、不 echo 假阳性、rebuild 原样保留。
  - 负例白盒: Simpler/xcreator/Pretty Smile Poses 不被动 (无过度保护)。
**NA_ edge (2026-08-15 用户新诊断, 已修)**: matcher 原先对所有 creator prefix
  统一要求末尾 word-boundary。NA_ 末尾是下划线 (word-char), 故 NA_Arrested 的
  _ 与 A 间无 \b -> NA_ 命中失败, 整串判 required+echo。修复: 对以明确 separator
  (_ / - 等) 结尾的 frozen prefix 用字面起始匹配, 不要求末尾 word-boundary。
  结果 (实测):
    NA_Arrested posepack       -> protected=NA_ / required=Arrested posepack
    NA_Tender love posepack    -> protected=NA_ / required=Tender love posepack
  负例: 普通含下划线语义标题 (Arrested_Tender posepack) / 未 frozen 的 X_ 前缀
  不因 _ 自动 prot (仍 source-specific + frozen-config driven, 拒绝宽泛 ^[A-Z]+_)。
  regression: 86->93 PASS (含 NA_ x2, 负例 x3)。
手动终态 +2 (manual 2->4): 用户裁决这2条不再交模型:
  T_1296d0b19078_g1 [Raspberrywhimss] Sweet Like Cinammon -> [Raspberrywhimss] 肉桂般甜蜜
    (旧模型错译"香草味肉桂")
  T_438c8bd18eda_g1 [ROSELIPA] 2AM -> [ROSELIPA] 凌晨2点 (无语义可翻段)
结构重算 (用户裁决, 真实输入推导非硬编码): KEEP=3 manual=20 retry=0(total) clean=364
  total=407。remaining model retry=0 (retry_v2 已消费: 28 DONE + 8 QA_FAIL 全入 manual)。
production overlay (真实输入计算, 不硬编码): base114 + pose26 + keep3 + manual20 = 163 unique
  (若全部 disjoint; 由 build_override_overlay.py 自真实输入推导并报告, 非硬编 163)。
preflight 不再硬编码 145/38: 期望值从当前 production overlay (len(ovr)) 与 retry manifest
  (requested/authoritative) 实际推导; 硬校验 = KEEP/manual 不入 retry + authoritative==retry
  行数 + scope 不丢行。build_title_retry 硬编码 38/3/2 移除 -> 自洽推导。
diag_retry_segments 输出新增 protected/reason 列; 要求输出每个 echo segment 的
  tid/source_phrase/protected=yes-no/required_translate/reason, 确认 creator 不再真失败。

--- BUG5: 英文 apostrophe contraction segmentation (2026-08-15 用户新诊断) ---
真实错误: [AA] I'm here -> [AA] I我在这里。根因: I 被 standalone/single-letter
  protection 命中 -> prot; 'm here 被当语义翻译 -> rebuild = I + 我在这里。
修复: contraction 必须先于 single-letter/technical 保护识别。当 "大写字母+apostrophe
  ('或curly’)+字母" (如 I'm/It's) 时跳过 single-letter prot, 整个 contraction
  流入 sem_buf 保持一个语义短语。覆盖: I'm, it's, don't, can't, won't, let's,
  you're, we're, they've (ASCII ' 与 curly ’)。实测:
    [AA] I'm here -> [AA](prot) + "I'm here"(sem)   (无 prot I)
  保留 standalone I/F/B/M/A 真正单独技术 token 原规则 (I, F, B 2 仍 prot)。

--- Pose Player 领域术语表 (2026-08-15 用户裁决, 新 deterministic glossary) ---
  _GLOSSARY 新增: "pose pack"/"posepack" -> "姿势包" (casefold, 大小写不敏感)。
  Pose Pack / pose pack / POSEPACK / posepack 全命中, 不再进模型;
  不允许同一 TITLE batch 混出"动作包 / 姿势包"。行内嵌词给 gloss_hint
  (如 NA_Arrested posepack -> 剩余语义词带 posepack=姿势包 提示)。

--- retry_v2 人工定稿 + 日志语义 (2026-08-15) ---
  8 QA_FAIL 入 manual-final (tid 见 configs/title_manual_translate.c26.csv);
  8 DONE 内容修正待用户提供 tid 后补入 -> manual 12+8=20。
  日志口径修复: 进度/完成行拆分 attempted / transport_success /
  accepted_translation / engine_error / unresolved (-成功歧义); echo_rejected
  仍由 [结果] 汇总 cache_echo_rejected= 打印。不阻塞 production, DESC 前已修。

A/D evidence 由 diag_title_qa 自动落盘 (不手工构造, 2026-08-15):
  python scripts/diag_title_qa.py output --done <done> --also-failed \
      --a-out output/title_A_tids.csv --d-out output/title_D_tids.csv
  A 文件: translation_id, source_text        (A unique tid 硬=42)
  D 文件: translation_id, source_text, segment_index, source_phrase (D unique=5)
  built-in invariant 门: A=42 / D=5 / D∩A=4 / D−A=1, 不满足则拒绝 (不喂 retry)。
  build_title_retry --a title_A_tids.csv --d title_D_tids.csv 直接消费 (不手工做)。

TITLE 两层 reconciliation 下游可读 (原记录, merge_title_reconciliation 已废弃):
  合并进 canonical output/translation_overrides.csv 的旧方案已替换为以上非破坏 overlay。
  phase2b_translate.load_overrides() / c_extract+final_todo.load_terminal_keep_tids() /
  conflict audit 改为从 .production.csv (显式 --overrides) 读取。

completion gate 修复 (phase2b_translate.py, 2026-08-15):
  对 authoritative TRANSLATE:
    1) 任一 model-required semantic phrase unresolved/engine failed -> QA_FAIL/PENDING
       (禁止原文 fallback 后仍 DONE)
    2) semantic 最终译文 == semantic source (模型 echo / 缓存 echo) -> QA_FAIL/PENDING
       (模型回显不叫成功, 不入 cache final)
    3) 仅 protected/glossary/明确 non-semantic terminal evidence 才允许 unchanged DONE/KEEP
  _on_done() 不再丢弃 [ERR/空/echo]: 失败 key 记入 fail_map -> completion gate + 持久化报告。
  每运行新增持久化失败报告: output/translation_phrase_failures_<batch>.csv
    (translation_id, segment_index, source_phrase, error)。白盒 9/9 PASS。
  materialize_from_cache 路径同样堵 echo (缓存物化译文==原文且含 pending -> QA_FAIL)。

production workset: 排除 3 个 terminal KEEP 后 407 -> 404 authoritative (不硬塞回 407)。
  2 manual pretranslated + 38 model retry = 40 TRANSLATE; 其余 364 QA (无已知失败)。
Phase2B scope-at-load / authoritative / POLICY-CONFLICT 全保留。
translation_done_batch_title.csv 仍为 draft, 禁止直接 merge。DESC 190 继续暂停。

== TITLE 407 QA 统计口径修正 (2026-08-15) ==
用户裁决: diag_title_qa 仅作证据采集器, 不自动 reconciliation, 不改 completion gate。
统计口径改为正交双维度:
  ROW_STATE (A/B/C 互斥, 求和==rows):
    A SAME_AS_SOURCE_SEMANTIC   : translation==source 且含玩家可见语义 -> FAIL/PENDING 候选
    B SAME_AS_SOURCE_LEGITIMATE : translation==source 且【强证据】合法保持原文 -> KEEP 候选
    C TRANSLATION_CHANGED       : translation != source
  PHRASE_FAILURE (D 独立维度, 每行 failed_phrase=yes/no): cache miss 且未被 protected/glossary/强证据覆盖
B 的强证据 (旧 translate_mode_for() 不可靠, 禁单独使用):
  B1 纯作者/handle (By/©/@/Credit + 名, 或纯 @handle)
  B3 纯编号/无实词技术标识 (全串无 >=3 字母英文实词, 如 2 F V2 / C1-3 M / 11 A2)
  B4 protected span 覆盖整个 source
  含英文实词的裸 title-case("Intense chemistry"/"Aylin Moss") 不自动归 B -> A 复核候选。
玩家可见语义标题 (Pose set #43 / Male poses #1 / Pretty Smile Poses / Bed Pose Pack)
即使旧 classifier 判 technical 也属 A(TRANSLATE)。intro-obj/placeholderIntro/8 *animation
含实词 -> 不静默 KEEP, 归 A 复核。

== TITLE 407 内容 completion 缺陷诊断 (2026-08-15, 实跑) ==
TITLE 实跑: rows=407 unique=407 pending=0 keep=0 emptyTranslation=0 sameAsSource=42。
工程 scope invariant PASS, 但内容 completion 判定有真实缺陷:
  1) translation!=''(非空) -> DONE 的完成门槛过松: 语义标题被原样写成 DONE。
  2) phrase 引擎 failure=8, 但最终 PENDING=0 -> failure propagation 缺陷。

根因 (代码 + 白盒证明):
  - _on_done() 拒绝空/`[ERR` 值 -> 失败 phrase 永不进 phrase_res/resolved
  - rebuild() missing key -> 原文回填
  - 终态块 any(startswith("[ERR")) 永不可达 ([ERR 在入口被拒)
  - elif translation.strip(): -> DONE  (失败 phrase 回填原文 -> 非空 -> DONE)
  => 8 failed phrase 全部"原文回填但仍 DONE" (Option A, 错误)。

diag_title_qa.py (只读, 零模型) 按内容层语义证据把每行归 A/B/C/D:
  A. SAME_AS_SOURCE_SEMANTIC   : translation==source 且有 semantic token -> FAIL/PENDING
  B. SAME_AS_SOURCE_NONSEMANTIC: translation==source 且 non-semantic/technical -> REVIEW/KEEP
  C. TRANSLATED                : translation != source
  D. MODEL_PHRASE_FAILED       : phrase cache miss 且未由 glossary/protected 覆盖
  唯一合法保持原文 = protected span / glossary / 明确 non-semantic evidence。
  权威语义 = translate_mode_for(); KEEP(技术标识) 直接归 B。

不 merge translation_done_batch_title.csv (仅 draft/诊断产物)。DESC 暂停。
未改 frozen workset/batch/coverage/writer/decision; 未调模型。

== C26 批次1 决策 reconciliation (2026-08-15, 用户裁决) ==
批1 real 运行: scope=26, output=26, unique=26, pending=0, KEEP=0, empty=0,
但 QA 发现 26/26 translation==source —— 全是编号/性别/版本/变体/占位技术 pose label。

裁决: KEEP=26, TRANSLATE=0, REVIEW=0。KEEP 权威依据 = 内容层
translate_mode_for() 实际语义证据 (semantic_tokens==[] / _is_technical_identifier 命中),
不采用旧 classify_meta() 初筛 (SEMANTIC_WITH_NUM×23 / ENGLISH_SEMANTIC×3)。
reason 统一: 23×NON_SEMANTIC_TAG + 3×TECHNICAL_LABEL
(intro-obj / intro-npc / placeholderIntro => TECHNICAL_LABEL)。

新增 frozen terminal 层 output/translation_overrides.c26_pose_keep.csv (26 行 action=KEEP),
不改旧 frozen translation_catalog.csv, 不改 translation_overrides.csv/.final2.csv。
translation_done_batch_pose.csv 仅为 canary 诊断产物, 禁止 merge。

重建命令在 c_extract/final_todo 传入 --overrides output\translation_overrides.c26_pose_keep.csv
(与缺省的 overrides.csv/.final2.csv 一并传入)。

重建后预期数字 (wb_c26_reconcile 16/16 PASS):
  C 真待补=0  D=597  manual=5  final_todo=602  manual pretranslated=5  workset=597
  batches POSE=0/TITLE=407/DESC=190  union=597  inter=0  missing=0  duplicate=0
  authoritative workset ∩ terminal KEEP = 0; 26 tid 均不在 workset/batch/tids。

未改 Phase2B: scope-at-load / --authoritative / POLICY-CONFLICT 全保留。

== terminal-KEEP reconciliation (2026-08-15, 生产级) ==
真实 Windows 全量 audit: workset TRANSLATE=626, terminal KEEP override=4, conflicts=3:
  T_2a2ce211e44a_g1 sofa_footrest / T_37fdb2d0d954_g1 Smh / T_655ec0d7e2ec_g1 loop-obj

逐层 trace 确认根因: sofa_footrest 在 catalog decision=TRANSLATE/PENDING, override(KEEP) +
final2(KEEP) + done(KEEP) 三处 terminal KEEP, 但 gap_inventory 仍进
CATALOG_TRANSLATE_MISSING_RESULT -> missing_result 当待补 -> final_todo/workset 被错误带入
authoritative TRANSLATE。根因不在 Phase2B (POLICY-CONFLICT gate 保留)。

修复 C→final_todo 的 reconciliation (不改 frozen catalog/override/coverage/writer):
  catalog TRANSLATE + terminal KEEP (frozen override 图层 action=KEEP) -> RESOLVED_KEEP,
  不得进入 translation_missing_result / final_todo / workset。
  terminal KEEP 仅允许来自明确 frozen terminal layer (translation_overrides.csv /
  translation_overrides.final2.csv action=KEEP / 已确认 final done status=KEEP)。
  不允许普通 classifier 自动产生 KEEP 来排除 todo。

改动 (最小):
  - c_extract.py: 新增 --overrides(可多次); 缺省读 output/translation_overrides.csv +
    .final2.csv; action==KEEP 的 tid 从 C 类排除 (RESOLVED_KEEP)。C 29->26。
  - final_todo.py: 新增 --overrides; 同 reconciliation (防御旧 missing_result)。
    硬编码 631 -> 结构性 total==C+D+manual_TR。
  - bake_workset.py: 硬编码 626 -> 结构性 workset + manual_t == todo。
  - batch_plan.py: 硬编码 626 -> 结构性 union==n_total。

预期数字 (全部 PASS, wb_reconcile_full 9/9):
  excluded=3  C=26  todo=628  workset=623  batches=26/407/190
  union=623  inter=0  missing=0  duplicate=0  terminal-KEEP∩workset=0

== authoritative + scope-at-load 修正 (2026-08-15, 生产级) ==
问题1 done 泄出: 原 --id-file 只过滤 pending 子集, done 仍写整份 todo(626)。
问题2 权威决策被推翻: Phase2B 老 classifier 把部分 pose 描述 (Walk5/1B (animation)/
  8 *animation 等) 重判 KEEP, 29 个 POSE tid 全被排出 pending -> 589->0, 再靠 626 掩盖。

修复 (phase2b_translate.py, 不改 batch plan/catalog/workset/coverage/writer):
1) scope-at-load: --id/--id-from-file/--regex/--category 在 load_todo 后、任何
   classification/override/translation/write 之前先把 todo 裁成目标 tid 集。
   强 invariant: requested==scoped-todo, output⊆requested, extra=0。
   requested tid 不存在 -> [HARD-FAIL]。
2) --authoritative: 进入 manifest 的 decision=TRANSLATE 是权威, 老 classifier 不得改判
   KEEP/DONE_SKIP -> 强制 FULL/PARTIAL 进翻译; KEEP 形态也进。
   override 声称 KEEP 但权威 TRANSLATE -> [POLICY-CONFLICT] fail-fast。
   未完成行以 PENDING 显式呈现, 不靠 626 掩盖。

白盒 wb_authoritative_scope (9/9): 626+id29+authoritative -> scope=29, 待翻译=29
  (3 个 KEEP 形态强制进), done=29 绝不 626, done⊆requested, 无 KEEP; KEEP override
  -> POLICY-CONFLICT; 不存在 tid -> HARD-FAIL; PENDING 显式。全白盒 9 套 + 回归 69/69 PASS。

**验证要求 (汇报)**: 10 个 sidecar 文件名 / 每包修改 key 数 / writer verify / independent audit / 是否全部 PASS。

---
== PACK_DESCRIPTION 190 启动 + 终态裁决 + content QA + final reconciliation (2026-08-15) ==

preflight (preflight_desc_190.py, 只读零模型):
  输入: translation_batch_manifest.csv + batch_PACK_DESCRIPTION.tids + translation_overrides.production.csv
  期望: requested=190 scoped=190 unique=190 authoritative_TRANSLATE=190
        production_overrides_loaded=163 terminal KEEP/manual conflict=0 -> PASS
  语义与 phase2b_translate.py 的 preflight (scope-at-load/authoritative/production overlay/终态不入批) 对齐。
  HARD-FAIL: tids duplicate / requested 非 manifest 子集 / assigned_batch!=PACK_DESCRIPTION / 冲突>0 / 计数!=190。
  白盒 PASS + 3 负例 gates。提交 b5ff702。

DESCRIPTION run (真实, 已跑): 190 = 173 DONE + 17 QA_FAIL (25 failed phrase = 23 ECHO + 2 EMPTY,
  completion gate 正常)。不再整体重跑模型。

17 QA_FAIL 人工终态裁决:
  terminal KEEP 2      -> configs/desc_terminal_keep.c26.csv
                          T_12fd8e705f78_g1 katverse.com (URL/域名)
                          T_823ade16cc5d_g1  NA_Iphone ACC (NA_+品牌+AC accessory)
  manual final 15      -> configs/desc_manual_translate.c26.csv (Dorothy 人工译文)
  build_desc_manual.py: 由真实 done QA_FAIL 行(tid+source) + dorothy 表(tid->final_translation)
    确定性生成 manual 15; HARD-FAIL 若缺译文/含KEEP tid/多余 tid。
  不修改旧 frozen layers。

DESCRIPTION protection regression (test 14, 123->135 PASS):
  URL/domain (katverse.com) / 产品型号 (iPhone 13 Pro Max) / creator 专名 (Musae/Soloriya/
  BRADFORD, Title-Case+ALL-CAPS) / asset identifier ([AnotherSimsStory]-...) /
  NA_/ACC accessory 标识 / 纯 '+' -> content-QA 不得判 candidate。
  不建宽泛"大写词/camelCase 全 protected"规则; 只按明确结构(source 专名/方括号/URL/型号)放行。
  负例: DIGIT_DROP / CJK_LATIN_GLUE(I我) / BRACKET_IMBALANCE 仍必检。

content QA (desc_content_qa.py, 只产生 REVIEW_CANDIDATE 不自动改):
  输入 translation_done_batch_desc.csv 的 DONE 行。6 规则:
    R1 residual English semantic fragments (排除白名单/保留专名/URL/型号)
    R2 Latin/CJK 异常黏连 (单字母 I我, 非多字母词)
    R3 source 数字完整保留
    R4 bracket/parentheses 平衡
    R5 bracketed creator/URL/accessory id 不被破坏
    R6 极端长度变化 (<1/3 或 >3x)
  输出 translation_desc_qa_candidates.csv (REVIEW_CANDIDATE)。报告 DONE input / suspicious / clean。

final reconciliation (build_desc_final.py, 只读零模型):
  precedence manual 15 > terminal KEEP 2 > accepted model DONE 173 = 190。
  输入 translation_done_batch_desc.csv + desc_manual_translate.c26.csv + desc_terminal_keep.c26.csv
  输出 translation_done_desc_final.csv (新 derived, 不覆盖旧证据)。
  期望: rows=190 uniqueTid=190 KEEP=2 MANUAL_FINAL=15 ACCEPTED_MODEL=173
        QA_FAIL=0 PENDING=0 REVIEW=0 empty=0 duplicate=0 source mismatch=0
        2+15+173=190 PASS。HARD-FAIL: 未裁决 QA_FAIL / 悬空终态 / manual∩keep / source mismatch。
  白盒 PASS + 3 负例 gates。不跑模型, 不生成 sidecar。先不 final merge。
