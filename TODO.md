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

## P4: Phase 3 (写回)

- [ ] package 备份
- [ ] 修改副本 + 写回
- [ ] 重新打开验证
- [ ] 正式输出汉化 package

## 明确不做 (规格允许范围外)
缩略图修复 / AI看图 / 动作包删除移动 / MOD整理 / 文件名汉化 / 衣服家具功能MOD汉化 / 重复检测 / UI / 自动下载
