# ComfyUI-IYKYK M1 选择器情境归属与全量台账审核摘要 (M1-D3)

- **基线 Commit SHA**：`f1b873c2479c287817f5780991e6621e31b9f848`
- **采样槽位总数**：14
- **注册选择器总数**：**496 个选择器** (通过 `load_slot_id_registries(Path("data"))` 动态实时核验，0 遗漏，0 幽灵 ID)
- **数据一致性验证**：本摘要由单一数据源脚本 [`scripts/generate_m1_ssot.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/scripts/generate_m1_ssot.py) 自动生成，保证台账 TSV 与 196 单元矩阵审核表 100% 绝对一致。

---

## 一、14 个采样槽位全量选择器归属审核统计

| 采样槽位 (Slot) | 注册选择器总数 | 分配特定情境数 (assigned_contexts) | 仅保留全局探索数 (intentional_global_only) | 待审核数 (pending_review) | 审核覆盖率 |
|---|---|---|---|---|---|
| [`clothing`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/docs/m1_selector_attribution_ledger.tsv) | 165 | 114 (69.1%) | 51 (30.9%) | 0 (0.0%) | **100.0%** |
| [`character`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/docs/m1_selector_attribution_ledger.tsv) | 11 | 10 (90.9%) | 1 (9.1%) | 0 (0.0%) | **100.0%** |
| [`makeup`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/docs/m1_selector_attribution_ledger.tsv) | 17 | 17 (100.0%) | 0 (0.0%) | 0 (0.0%) | **100.0%** |
| [`hairstyle`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/docs/m1_selector_attribution_ledger.tsv) | 57 | 26 (45.6%) | 31 (54.4%) | 0 (0.0%) | **100.0%** |
| [`jewelry`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/docs/m1_selector_attribution_ledger.tsv) | 29 | 26 (89.7%) | 3 (10.3%) | 0 (0.0%) | **100.0%** |
| [`props`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/docs/m1_selector_attribution_ledger.tsv) | 23 | 22 (95.7%) | 1 (4.3%) | 0 (0.0%) | **100.0%** |
| [`tattoo`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/docs/m1_selector_attribution_ledger.tsv) | 12 | 11 (91.7%) | 1 (8.3%) | 0 (0.0%) | **100.0%** |
| [`liquids`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/docs/m1_selector_attribution_ledger.tsv) | 7 | 6 (85.7%) | 1 (14.3%) | 0 (0.0%) | **100.0%** |
| [`pose`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/docs/m1_selector_attribution_ledger.tsv) | 15 | 10 (66.7%) | 5 (33.3%) | 0 (0.0%) | **100.0%** |
| [`expression`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/docs/m1_selector_attribution_ledger.tsv) | 31 | 19 (61.3%) | 12 (38.7%) | 0 (0.0%) | **100.0%** |
| [`lighting`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/docs/m1_selector_attribution_ledger.tsv) | 59 | 39 (66.1%) | 20 (33.9%) | 0 (0.0%) | **100.0%** |
| [`film`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/docs/m1_selector_attribution_ledger.tsv) | 39 | 19 (48.7%) | 20 (51.3%) | 0 (0.0%) | **100.0%** |
| [`shot_type`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/docs/m1_selector_attribution_ledger.tsv) | 16 | 7 (43.8%) | 9 (56.2%) | 0 (0.0%) | **100.0%** |
| [`camera_angle`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/docs/m1_selector_attribution_ledger.tsv) | 15 | 10 (66.7%) | 5 (33.3%) | 0 (0.0%) | **100.0%** |
| **合计 (Total)** | **496** | **336 (67.7%)** | **160 (32.3%)** | **0 (0.0%)** | **100.0%** |

---

## 二、权重梯级分布与中性采样规范

1. **梯度权重设计契约**：
   - **强关联 (Weight 4)**：条目与情境具有极高文化/物理确定性（如制服之于学校、潜水服之于海洋、护士服之于医院、树影日光之于户外）。
   - **适配 (Weight 2)**：条目在情境下具备较高兼容性与美学和谐度，但非专属（如运动服之于校园、露背裙之于夜生活）。
   - **弱关联 (Weight 1)**：条目具备弱关联或过渡特征，在部分混合情境中可作为低频辅助采样。
   - **全局探索池 (Weight 0 / 15% 全局池)**：通用中性款式（如基础白衬衫、普通纯色T恤、中性影棚光照、常规平视机位），不进入特定情境加权候选池，保留全局 $G_s$ 探索机会。

2. **中性无偏置抽样规范**：
   - **generic 情境**：14 个槽位在 `generic` 情境下统一废除原先 4 候选的小集合限制，全部配置为 `mode="neutral"`，严格定义为 $C(generic, s) = G_s$。
   - **规范情境 tattoo**：`school`, `office`, `medical`, `dining` 4 个情境在 `tattoo` 槽位不额外赋予纹身类别亲和偏置，沿用合法全局候选分布；不新增禁用规则，不删除外部指定内容。

---

## 三、台账与矩阵双向一致性断言

- **TSV 候选在矩阵中的反映率**：100.0%（TSV 中每一个 `context:weight` 均 1:1 投影至对应矩阵单元）。
- **矩阵提案在 TSV 中的可溯源率**：100.0%（矩阵中每一个候选及其权重均在 TSV 对应行具备完全相同的数值与论证）。
- **幽灵 ID 检测结论**：**0 个不存在的 ID**（全部通过真实 JSON 目录 Schema 与键名白名单核验）。
