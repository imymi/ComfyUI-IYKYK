# ComfyUI-IYKYK M5-3 只读归档因果重评独立报告

> **说明**：本报告由审核器在只读模式下对历史证据归档（`audit_m5_3_evidence.json.gz`）进行完整流式逐条因果重评生成。历史归档文件、原始清单及历史报告保持原样，未被修改或覆盖。

## 一、重评执行依据与环境绑定

- **源归档文件**: `scratch/audit_m5_3_evidence.json.gz`
- **源归档 SHA-256**: `5417aa6529e4a86d2f920d6387ecd88626d70d9fd1973c7896ffa16544b01ff9`
- **源归档大小**: 737,487,743 字节
- **审核器脚本**: `scratch/run_m5_3_audit.py`
- **审核器脚本 SHA-256**: `091ce81717c7ca8a45eec25dab0b81abaa35c624ca433c16b55f446fd3751e0f`
- **目标提交 Commit**: `1db9e675666854d569da85d72f6a39708b81c66c` (Tree: `1530ce71c110f69cba39fee5f9d8d70b841c25ca`, Dirty: `False`)
- **核心源码摘要**: `7641d348b44249de809a73e8e3bfd22c8d7ba48ed4e71f6cdf36fe8ab6a6ee7f`
- **目标批次哈希**: `48ae2e927fc6c6dc3f19362c806b31e2587df49aad85193f75e2731b18fa4c7d`
- **基线批次哈希**: `8762ecaf48c48d49b9f538047bc12e3aec3ad941727a72155f39993d51fd42e4`
- **执行时间**: `2026-10-20 09:35:32 UTC`
- **处理种子总数**: 10,000 组
- **重评总耗时**: 73.15 秒

## 二、逐条质量门禁重验结果

| 质量门禁指标 | 检查项 | 失败数 | 门禁要求 | 判定 |
|---|---|---|---|---|
| **严格双轮确定性** | `Round A == Round B` | 0 | `== 0` | **PASS** |
| **残余硬冲突** | `unresolved_conflicts == ()` | 0 | `== 0` | **PASS** |
| **词数预算** | `len(positive.split()) <= 250` | 0 | `== 0` | **PASS** |
| **语法合法性** | `validate_prompt_syntax 通过` | 0 | `== 0` | **PASS** |
| **严格二次消解幂等性** | `二次消解决策数 == 0 且原子全等` | 0 | `== 0` | **PASS** |
| **诊断闭包 Oracle 校验** | `validate_audit_json_oracle 通过` | 0 | `== 0` | **PASS** |
| **Provenance DAG 闭环** | `终态原子 parent_ids 非空可追溯` | 0 | `== 0` | **PASS** |

## 三、逐条因果重评归因统计

- **完全一致种子数**: **0** (0.00%)
- **发生差异种子数**: **10,000** (100.00%)
- **未解释差异种子数**: **116** (门禁严格要求 == 0)

### 差异因果类别明细表

| 差异主归因类别 | 种子数 | 占差异比例 | 机制与规则依据 |
|---|---|---|---|
| `context_affinity_calibration` | 8,846 | 88.46% | M2 情境亲和矩阵上下文权重校准与回退抽样范围保障 |
| `conflict_rule:material_penetration` | 443 | 4.43% | M3 冲突消解规则 `material_penetration` 触发物理互斥消解/状态修正/联动载体裁决 |
| `conflict_rule:framing_lower_body_coherence` | 229 | 2.29% | M3 冲突消解规则 `framing_lower_body_coherence` 触发物理互斥消解/状态修正/联动载体裁决 |
| `conflict_rule:nudity_clothing_conflicts` | 163 | 1.63% | M3 冲突消解规则 `nudity_clothing_conflicts` 触发物理互斥消解/状态修正/联动载体裁决 |
| `UNEXPLAINED` | 116 | 1.16% | M2 情境亲和矩阵上下文权重校准与回退抽样范围保障 |
| `conflict_rule:makeup_details_coherence` | 114 | 1.14% | M3 冲突消解规则 `makeup_details_coherence` 触发物理互斥消解/状态修正/联动载体裁决 |
| `conflict_rule:pose_hand_occupation` | 67 | 0.67% | M3 冲突消解规则 `pose_hand_occupation` 触发物理互斥消解/状态修正/联动载体裁决 |
| `conflict_rule:clothing_style_state_coherence` | 22 | 0.22% | M3 冲突消解规则 `clothing_style_state_coherence` 触发物理互斥消解/状态修正/联动载体裁决 |

## 四、未解释差异清单

共发现 116 个未解释种子：

- **Seed 124**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 124, slot 'props', removed atom 'smartphone in hand recording' dropped silently without rule decision despite target selecting identical item 'smartphone_recording'."]
- **Seed 149**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 149, slot 'props', removed atom 'metal handcuffs on headboard' dropped silently without rule decision despite target selecting identical item 'bondage_rope_collar'."]
- **Seed 200**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 200, slot 'props', removed atom 'ice bucket on table' dropped silently without rule decision despite target selecting identical item 'ice_cubes'."]
- **Seed 280**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 280, slot 'props', removed atom 'crimson hemp rope bindings' dropped silently without rule decision despite target selecting identical item 'bondage_rope_collar'."]
- **Seed 287**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 287, slot 'props', removed atom 'bottle of champagne on ice' dropped silently without rule decision despite target selecting identical item 'wine_glass_bottle'."]
- **Seed 332**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 332, slot 'props', removed atom 'smartphone in hand recording' dropped silently without rule decision despite target selecting identical item 'smartphone_recording'."]
- **Seed 374**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 374, slot 'props', removed atom 'bouquet wrapped in crinkled craft paper' dropped silently without rule decision despite target selecting identical item 'fresh_flower_bouquet'."]
- **Seed 444**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 444, slot 'props', removed atom 'bouquet wrapped in crinkled craft paper' dropped silently without rule decision despite target selecting identical item 'fresh_flower_bouquet'."]
- **Seed 455**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 455, slot 'props', removed atom 'half-empty wine glass on nightstand' dropped silently without rule decision despite target selecting identical item 'wine_glass_bottle'."]
- **Seed 521**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 521, slot 'props', removed atom 'smartphone in hand recording' dropped silently without rule decision despite target selecting identical item 'smartphone_recording'."]
- **Seed 580**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 580, slot 'props', removed atom 'professional DSLR on tripod in background' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 696**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 696, slot 'props', removed atom 'bullet vibrator resting on skin' dropped silently without rule decision despite target selecting identical item 'sex_toy_vibrator'."]
- **Seed 780**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 780, slot 'props', removed atom 'glistening lube bottle nearby' dropped silently without rule decision despite target selecting identical item 'sex_toy_vibrator'."]
- **Seed 1048**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 1048, slot 'props', removed atom 'studio lighting equipment' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 1075**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 1075, slot 'props', removed atom 'stemware with red wine' dropped silently without rule decision despite target selecting identical item 'wine_glass_bottle'."]
- **Seed 1134**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 1134, slot 'props', removed atom 'ring light reflection in eyes' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 1259**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 1259, slot 'props', removed atom 'melting ice cube resting on collarbone' dropped silently without rule decision despite target selecting identical item 'ice_cubes'."]
- **Seed 1342**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 1342, slot 'props', removed atom 'phone face down on bed' dropped silently without rule decision despite target selecting identical item 'smartphone_recording'."]
- **Seed 1402**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 1402, slot 'props', removed atom 'flower petals pressed against chest' dropped silently without rule decision despite target selecting identical item 'fresh_flower_bouquet'."]
- **Seed 1410**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 1410, slot 'props', removed atom 'studio lighting equipment' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 1448**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 1448, slot 'props', removed atom 'smartphone in hand recording' dropped silently without rule decision despite target selecting identical item 'smartphone_recording'."]
- **Seed 1705**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 1705, slot 'props', removed atom 'bouquet wrapped in crinkled craft paper' dropped silently without rule decision despite target selecting identical item 'fresh_flower_bouquet'."]
- **Seed 1861**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 1861, slot 'props', removed atom 'magic wand vibrator on bed' dropped silently without rule decision despite target selecting identical item 'sex_toy_vibrator'."]
- **Seed 1952**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 1952, slot 'props', removed atom 'studio lighting equipment' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 2053**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 2053, slot 'props', removed atom 'leather collar with O-ring' dropped silently without rule decision despite target selecting identical item 'bondage_rope_collar'."]
- **Seed 2067**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 2067, slot 'props', removed atom 'bottle of champagne on ice' dropped silently without rule decision despite target selecting identical item 'wine_glass_bottle'."]
- **Seed 2237**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 2237, slot 'props', removed atom 'fingers tightly gripping wrinkled bed sheets' dropped silently without rule decision despite target selecting identical item 'pillow_clutching'."]
- **Seed 2285**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 2285, slot 'props', removed atom 'smartphone in hand recording' dropped silently without rule decision despite target selecting identical item 'smartphone_recording'."]
- **Seed 2327**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 2327, slot 'props', removed atom 'professional DSLR on tripod in background' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 2383**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 2383, slot 'props', removed atom 'glistening lube bottle nearby' dropped silently without rule decision despite target selecting identical item 'sex_toy_vibrator'."]
- **Seed 2470**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 2470, slot 'props', removed atom 'magic wand vibrator on bed' dropped silently without rule decision despite target selecting identical item 'sex_toy_vibrator'."]
- **Seed 2472**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 2472, slot 'props', removed atom 'studio lighting equipment' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 2477**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 2477, slot 'props', removed atom 'stemware with red wine' dropped silently without rule decision despite target selecting identical item 'wine_glass_bottle'."]
- **Seed 2553**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 2553, slot 'props', removed atom 'soft plush toy on bed' dropped silently without rule decision despite target selecting identical item 'fluffy_teddy_bear'."]
- **Seed 2604**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 2604, slot 'props', removed atom 'smartphone in hand recording' dropped silently without rule decision despite target selecting identical item 'smartphone_recording'."]
- **Seed 2631**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 2631, slot 'props', removed atom 'bouquet wrapped in crinkled craft paper' dropped silently without rule decision despite target selecting identical item 'fresh_flower_bouquet'."]
- **Seed 2681**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 2681, slot 'props', removed atom 'glistening lube bottle nearby' dropped silently without rule decision despite target selecting identical item 'sex_toy_vibrator'."]
- **Seed 2729**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 2729, slot 'props', removed atom 'bouquet wrapped in crinkled craft paper' dropped silently without rule decision despite target selecting identical item 'fresh_flower_bouquet'."]
- **Seed 2758**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 2758, slot 'props', removed atom 'studio lighting equipment' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 2861**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 2861, slot 'props', removed atom 'holding game controller' dropped silently without rule decision despite target selecting identical item 'game_controller'."]
- **Seed 2874**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 2874, slot 'props', removed atom 'professional DSLR on tripod in background' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 2932**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 2932, slot 'props', removed atom 'bouquet wrapped in crinkled craft paper' dropped silently without rule decision despite target selecting identical item 'fresh_flower_bouquet'."]
- **Seed 3055**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 3055, slot 'props', removed atom 'smartphone in hand recording' dropped silently without rule decision despite target selecting identical item 'smartphone_recording'."]
- **Seed 3101**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 3101, slot 'props', removed atom 'smartphone in hand recording' dropped silently without rule decision despite target selecting identical item 'smartphone_recording'."]
- **Seed 3116**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 3116, slot 'props', removed atom 'glistening lube bottle nearby' dropped silently without rule decision despite target selecting identical item 'sex_toy_vibrator'."]
- **Seed 3223**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 3223, slot 'props', removed atom 'studio lighting equipment' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 3232**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 3232, slot 'props', removed atom 'stemware with red wine' dropped silently without rule decision despite target selecting identical item 'wine_glass_bottle'."]
- **Seed 3484**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 3484, slot 'props', removed atom 'bouquet wrapped in crinkled craft paper' dropped silently without rule decision despite target selecting identical item 'fresh_flower_bouquet'."]
- **Seed 3493**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 3493, slot 'props', removed atom 'leather collar with O-ring' dropped silently without rule decision despite target selecting identical item 'bondage_rope_collar'."]
- **Seed 3495**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 3495, slot 'props', removed atom 'bouquet wrapped in crinkled craft paper' dropped silently without rule decision despite target selecting identical item 'fresh_flower_bouquet'."]
- **Seed 3504**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 3504, slot 'props', removed atom 'fingers tightly gripping wrinkled bed sheets' dropped silently without rule decision despite target selecting identical item 'pillow_clutching'."]
- **Seed 3507**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 3507, slot 'props', removed atom 'studio lighting equipment' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 3508**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 3508, slot 'props', removed atom 'holding lush fresh floral bouquet' dropped silently without rule decision despite target selecting identical item 'fresh_flower_bouquet'."]
- **Seed 3816**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 3816, slot 'props', removed atom 'arms wrapped around cute stuffed animal' dropped silently without rule decision despite target selecting identical item 'fluffy_teddy_bear'."]
- **Seed 3823**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 3823, slot 'props', removed atom 'professional DSLR on tripod in background' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 3887**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 3887, slot 'props', removed atom 'leather collar with O-ring' dropped silently without rule decision despite target selecting identical item 'bondage_rope_collar'."]
- **Seed 3896**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 3896, slot 'props', removed atom 'professional DSLR on tripod in background' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 3946**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 3946, slot 'props', removed atom 'studio lighting equipment' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 3953**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 3953, slot 'props', removed atom 'glistening lube bottle nearby' dropped silently without rule decision despite target selecting identical item 'sex_toy_vibrator'."]
- **Seed 4131**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 4131, slot 'props', removed atom 'holding lush fresh floral bouquet' dropped silently without rule decision despite target selecting identical item 'fresh_flower_bouquet'."]
- **Seed 4160**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 4160, slot 'props', removed atom 'soft plush toy on bed' dropped silently without rule decision despite target selecting identical item 'fluffy_teddy_bear'."]
- **Seed 4299**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 4299, slot 'props', removed atom 'smartphone in hand recording' dropped silently without rule decision despite target selecting identical item 'smartphone_recording'."]
- **Seed 4303**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 4303, slot 'props', removed atom 'studio lighting equipment' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 4346**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 4346, slot 'props', removed atom 'melting ice cube resting on collarbone' dropped silently without rule decision despite target selecting identical item 'ice_cubes'."]
- **Seed 4400**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 4400, slot 'props', removed atom 'smartphone in hand recording' dropped silently without rule decision despite target selecting identical item 'smartphone_recording'."]
- **Seed 4514**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 4514, slot 'props', removed atom 'smartphone in hand recording' dropped silently without rule decision despite target selecting identical item 'smartphone_recording'."]
- **Seed 4541**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 4541, slot 'props', removed atom 'smartphone in hand recording' dropped silently without rule decision despite target selecting identical item 'smartphone_recording'."]
- **Seed 4617**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 4617, slot 'props', removed atom 'stemware with red wine' dropped silently without rule decision despite target selecting identical item 'wine_glass_bottle'."]
- **Seed 4724**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 4724, slot 'props', removed atom 'water trickle running down chest' dropped silently without rule decision despite target selecting identical item 'ice_cubes'."]
- **Seed 4820**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 4820, slot 'props', removed atom 'melting ice cube resting on collarbone' dropped silently without rule decision despite target selecting identical item 'ice_cubes'."]
- **Seed 4843**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 4843, slot 'props', removed atom 'melting ice cube resting on collarbone' dropped silently without rule decision despite target selecting identical item 'ice_cubes'."]
- **Seed 4865**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 4865, slot 'props', removed atom 'stemware with red wine' dropped silently without rule decision despite target selecting identical item 'wine_glass_bottle'."]
- **Seed 4903**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 4903, slot 'props', removed atom 'smartphone in hand recording' dropped silently without rule decision despite target selecting identical item 'smartphone_recording'."]
- **Seed 4971**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 4971, slot 'props', removed atom 'fingers tightly gripping wrinkled bed sheets' dropped silently without rule decision despite target selecting identical item 'pillow_clutching'."]
- **Seed 4992**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 4992, slot 'props', removed atom 'ice bucket on table' dropped silently without rule decision despite target selecting identical item 'ice_cubes'."]
- **Seed 5143**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 5143, slot 'props', removed atom 'phone face down on bed' dropped silently without rule decision despite target selecting identical item 'smartphone_recording'."]
- **Seed 5297**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 5297, slot 'props', removed atom 'ring light reflection in eyes' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 5350**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 5350, slot 'props', removed atom 'stemware with red wine' dropped silently without rule decision despite target selecting identical item 'wine_glass_bottle'."]
- **Seed 5361**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 5361, slot 'props', removed atom 'holding lush fresh floral bouquet' dropped silently without rule decision despite target selecting identical item 'fresh_flower_bouquet'."]
- **Seed 5410**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 5410, slot 'props', removed atom 'melting ice cube resting on collarbone' dropped silently without rule decision despite target selecting identical item 'ice_cubes'."]
- **Seed 5606**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 5606, slot 'props', removed atom 'professional DSLR on tripod in background' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 5751**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 5751, slot 'props', removed atom 'ring light reflection in eyes' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 5801**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 5801, slot 'props', removed atom 'smartphone in hand recording' dropped silently without rule decision despite target selecting identical item 'smartphone_recording'."]
- **Seed 5856**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 5856, slot 'props', removed atom 'flower petals pressed against chest' dropped silently without rule decision despite target selecting identical item 'fresh_flower_bouquet'."]
- **Seed 5881**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 5881, slot 'props', removed atom 'ice bucket on table' dropped silently without rule decision despite target selecting identical item 'ice_cubes'."]
- **Seed 5908**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 5908, slot 'props', removed atom 'metal handcuffs on headboard' dropped silently without rule decision despite target selecting identical item 'bondage_rope_collar'."]
- **Seed 6043**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 6043, slot 'props', removed atom 'studio lighting equipment' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 6178**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 6178, slot 'props', removed atom 'crimson hemp rope bindings' dropped silently without rule decision despite target selecting identical item 'bondage_rope_collar'."]
- **Seed 6396**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 6396, slot 'props', removed atom 'smartphone in hand recording' dropped silently without rule decision despite target selecting identical item 'smartphone_recording'."]
- **Seed 7099**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 7099, slot 'props', removed atom 'bottle of champagne on ice' dropped silently without rule decision despite target selecting identical item 'wine_glass_bottle'."]
- **Seed 7257**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 7257, slot 'props', removed atom 'smartphone in hand recording' dropped silently without rule decision despite target selecting identical item 'smartphone_recording'."]
- **Seed 7521**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 7521, slot 'props', removed atom 'glistening lube bottle nearby' dropped silently without rule decision despite target selecting identical item 'sex_toy_vibrator'."]
- **Seed 7684**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 7684, slot 'props', removed atom 'bullet vibrator resting on skin' dropped silently without rule decision despite target selecting identical item 'sex_toy_vibrator'."]
- **Seed 8011**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 8011, slot 'props', removed atom 'studio lighting equipment' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 8067**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 8067, slot 'props', removed atom 'screen light glowing on face' dropped silently without rule decision despite target selecting identical item 'smartphone_recording'."]
- **Seed 8136**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 8136, slot 'props', removed atom 'studio lighting equipment' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 8150**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 8150, slot 'props', removed atom 'studio lighting equipment' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 8189**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 8189, slot 'props', removed atom 'metal handcuffs on headboard' dropped silently without rule decision despite target selecting identical item 'bondage_rope_collar'."]
- **Seed 8269**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 8269, slot 'props', removed atom 'professional DSLR on tripod in background' dropped silently without rule decision despite target selecting identical item 'camera_tripod_flash'."]
- **Seed 8331**: ["UNEXPLAINED_SILENT_ATOM_DROP: seed 8331, slot 'props', removed atom 'smartphone in hand recording' dropped silently without rule decision despite target selecting identical item 'smartphone_recording'."]

---
**重评结论**：全部 10,000 条记录流式重验完成，未解释差异数为 **116**。