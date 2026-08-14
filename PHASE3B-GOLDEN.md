# Phase 3B-GOLDEN — s4pi/s4pe Golden Reference 对照

状态: **自制 package writer 已冻结**。不再试 test6/test7。
目标: 用成熟工具链 s4pi/s4pe 对原始包做**完全相同的某一次修改**,生成 golden package,
     再逐字段/逐字节对照,反推我们 writer 遗漏的结构或元数据。

## 测试基线 (已证伪的假设)
自制 writer 已依次排除:
- v1 全量 relayout           -> 游戏拒绝
- v2 原位手术(中段 padding)  -> 游戏拒绝
- v3 relayout(正确结构)      -> 游戏拒绝
- inplace 尾部补零           -> 游戏拒绝 (STRUCT PASS 但 loader 不接受)
- test5 strict 等长(零补零零重排) -> 游戏仍拒绝

**test5 是"纯 4 字节原位 hex 编辑"**: 原包 vs test5 输出,除 CHS STBL `Left`->`左␣`
的字节 47-50 (4c656674 -> e5b7a620) 外,其余逐字节一致 (文件大小 644=644, index_offset 512=512,
所有 entry offset/size/flags/reserved 不变)。游戏仍无法启动。
=> 结论: 问题不在 DBPF 书签/offset/体积/补零。必然在 STBL 资源本身的某种语义/元数据,
   或包内存在配套的校验/哈希资源 (real 包有 8 个非 STBL 资源,需重点核查)。

## 待执行 (用户主导,我辅助)
1. 用 s4pe GUI(或 s4pi CLI)对原始包副本做同一次修改:
   - package: `Embracing Faces Meme Pose by t0nischwartz.package`
   - target : STBL locale=0x01, instance=0x014EACCF17C8B091, key=0xFDD36EF2
   - edit   : `Left` -> `左 `(左+ASCII空格, 与 test5 完全一致)
   - 输出 golden package
2. 第一关: golden 单独放 Mods,看 Sims4 能否启动。
   - golden 也不能启动 => 问题可能在 STBL/key 修改方式或资源语义/测试假设,不在我们的 writer。
   - golden 能启动     => 我们的 writer 必然漏了某个结构/元数据。
3. 若 golden 能启动: original vs golden、test5 vs golden 完整二进制/结构比较
   (header/index/body size 之外,把 s4pi 保存后实际变化的所有字段全部列出)。

## 关键 STBL 事实 (real 包 diag, 供 golden 对照)
- CHS STBL: locale=0x01, inst=0x014EACCF17C8B091, offset=474?, size=130, **raw(未压缩)**,
  body8=`5354424c05000005`, rawFlags=0x00000082, rawReserved=0x00010000, 5 keys
- STBL v5 头: `5354424c`(STBL) + `0500`(ver=5) + `00`(flag byte) + `0500000000000000`(count=5,
  little-endian u64 @7-14) + `0000`(reserved) + `00000000`(str_len=0 @17-20) + entries@21
- entry: keyHash<u32> + flags<u8> + len<u16> + utf8 text
- fixture 逐 key (realish2, 与 real 同形): key0 t0nischwartz(len12) key1 Left(len4) key2 Right(len5)
  key3 Embracing Faces(len15) key4 A pose...meme(len38); 总数 130B
- **所有 STBL entry flags=0**; STBL header flag byte@6=0x00

## real 包非 STBL 资源 (疑点)
real 包共 26 资源 = 18 STBL + **8 非 STBL**。8 个非 STBL 中可能含:
- 哈希/校验类资源(loader 据此校验 STBL 内容一致性)
- 资源引用/依赖索引
需 golden 对照时重点看这 8 个是否被 s4pi 改动。

## cmp_pkg.py 用法
```
python scripts/cmp_pkg.py original.package golden.package   # original vs golden
python scripts/cmp_pkg.py test5.package  golden.package     # test5  vs golden
```
逐字段 header + 逐 entry(identity/off/size/comp/flags/reserved) + index order/pad + 逐 body 内容。
