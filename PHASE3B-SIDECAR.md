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

编译 (Windows, .NET Framework 4.0; 仅依赖仓库内容 + NuGet restore):
已内建 MSB3644 修复: 工程引用 Microsoft.NETFramework.ReferenceAssemblies 1.0.3
(自包含 net40 reference assemblies, 无需装 .NET 4.0 Developer Pack; FrameworkPathOverride
沿 ProjectReference 传给 vendored s4pi 工程一并生效)。

依赖: s4pi 源码已 vendored 到 `vendor/s4pi/` (固定 commit fff1936, GPL v3,
见 vendor/s4pi/README-S4PI-VENDOR.md), Windows git clone/pull 即可获得, 无需任何手动寻找。

⚠️ 用默认 Debug 配置 (不要加 /p:Configuration=Release): 上游 s4pi 的 PreBuildEvent
硬编码 `bin\Debug\CreateAssemblyVersion` (生成 Properties\AssemblyVersion.cs),
仅在 Debug 下可命中; Release 会因找不到该 exe 而失败。Debug 对构建工具已足够。
先 restore 再 build, 两步:
```
cd D:\projects\sims4_trans\sidecar_builder
```
```
msbuild SidecarBuilder.csproj /t:Restore
```
```
msbuild SidecarBuilder.csproj
```
产物: `bin\Debug\SidecarBuilder.exe`

运行 (建单 STBL 包 + 重开核对):
```
bin\Debug\SidecarBuilder.exe -out out.package -type 0x220557DA -group 0x80000000 -inst 0x014EACCF17C8B091 -k FDD36EF2:左 -k 552CC77A:相拥
```
验收 (Step③ 第一关):
1. Windows 能编译
2. 能创建只含 1 个 STBL 的 package
3. 重新打开能核对 TGI + entries
通过后才进入 1包 -> 10包 -> 50包 -> 659包。
