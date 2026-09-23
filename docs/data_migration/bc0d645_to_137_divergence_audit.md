# 双版本全量种子差异归因审查报告

- **基线提交**: `bc0d645` (31 款)
- **受测版本**: `v1.1.0-rc8` (HEAD: `3d84eb0725`) (137 款完整目录)
- **测试种子范围**: Seeds `0..9999` (共 10,000 种子)
- **基线汇总哈希**: `4525786e7273dc0694e64ec216d4fc9510f211d32de7bdfaa00a12f7a5f320e2` (与冻结基线 100% 吻合)
- **当前汇总哈希**: `a39a823d09b3b817107ba6a6ebdd5fdb261f4f71bd8148bd7856533fdf21c218`
- **未解释差异项 (UNEXPLAINED)**: **`0`** (硬失败门禁要求 == 0)
- **完整归档依据**: `bc0d645_to_137_divergence_audit.json.gz` (包含全部 4682 组差异逐原子归因依据)

## 差异分布全景统计

| 统计维度 | 种子数量 | 占比 | 状态 |
|---|---|---|---|
| 完全一致种子 (Bit-exact Identical) | 5,318 | 53.18% | PASS |
| 差异种子总数 (Divergent Seeds) | 4,682 | 46.82% | INFO |
| 零归因遗漏/未解释差异 (UNEXPLAINED) | 0 | 0.00% | PASS |

## 差异主要归因分类分布

| 主归因类别 | 影响种子数 | 归因机理解释 |
|---|---|---|
| `clothing_pool_expansion_category_shift` | 4,193 (89.56%) | 款式词库从 31 款扩充至 137 款，随机抽取的款式 ID 发生跨款式位移 |
| `clothing_pool_expansion_intra_slot_shift` | 414 (8.84%) | 款式相同，但扩充目录 PRNG 步进或多变体/叶子标签离散抽样产生槽位内位移 |
| `state_lacks_carrier` | 75 (1.60%) | 承载物消解/冲突规则决策精确介入 (state_lacks_carrier) |
