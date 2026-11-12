# ComfyUI-IYKYK v1.2.1 10,000 种子与全四节点综合审计报告

- **审计提交 Commit**: `0681f10b0705dc8eb86822ef12d0898127573224` (Tree: `e50040053370676055ef8e2138458616b18e9289`, Dirty: `True`)
- **核心源码摘要 (`core_source_digest`)**: `0b74c7411d4a58da5b10ac5d63834c3ba01bcf9e128a9eca3f264df73405beb2`
- **10k 种子汇总哈希 (`target_batch_hash`)**: `b8775f683c9f329bf39be9e6ac1bd85cba499d2a59cddea250f503a63d9661be`
- **归档文件**: `scratch/audit_v1_2_1_10k_evidence.json.gz` (`6,762,487` 字节, SHA-256: `4b918de0b7f883b7fb9fe02846054cac72285d0b723f420f55c2467317596326`)
- **执行命令**: `/Users/jacobyang/Hermes/.venv/bin/python scratch/run_v1_2_1_10k_audit.py --seeds 10000 --strat-seeds 500`
- **环境**: Python `3.9.6` / `Darwin 27.2.0 (arm64)` / jsonschema `4.25.1`

## 一、10,000 种子全链路质量门禁（seed=0..9999）

| 门禁指标 | 失败数 | 状态 |
|---|---|---|
| `determinism` | 0 | PASS |
| `oracle_validation` | 0 | PASS |
| `residual_conflicts` | 0 | PASS |
| `word_budget` | 0 | PASS |
| `prompt_syntax` | 0 | PASS |
| `provenance_closed_loop` | 0 | PASS |
| `secondary_idempotency` | 0 | PASS |
| `carrier_coherence` | 0 | PASS |

- **质量门禁失败种子数**: `0`
- **等级分布**: `{'L1': 1701, 'L2': 1645, 'L3': 1644, 'L4': 1681, 'L5': 1702, 'L6': 1627}`
- **分支分布**: `{'l1_full_clothed': 1701, 'l2_backless': 303, 'l2_collar_neckline': 256, 'l2_leg_slit': 281, 'l2_midriff_waist': 272, 'l2_shoulder_strap': 269, 'l2_sleeve_wrist': 264, 'l3_bikini_top_removed': 207, 'l3_bra_pushed_up': 190, 'l3_open_shirt_breasts': 192, 'l3_panties_only': 212, 'l3_pulled_down_dress': 203, 'l3_raised_top_breasts': 218, 'l3_topless_pants_kept': 202, 'l3_topless_skirt_kept': 220, 'l4_accessory_only': 341, 'l4_bra_and_panties': 356, 'l4_hosiery_footwear_only': 318, 'l4_panties_only': 356, 'l4_towel_sheet_wrap': 310, 'l5_clothes_discarded_nearby': 829, 'l5_full_nude_standing_or_frontal': 873, 'l6_frontal_genital_display': 433, 'l6_lying_spread_display': 397, 'l6_rear_bent_display': 386, 'l6_squatting_self_touch': 411}`

## 二、全四公开节点分层矩阵验证

- **`IYKYKPresetBrowser`**: 检验 125 组，失败 `0`
- **`IYKYKCustomSlotCombiner`**: 检验 200 组，失败 `0`
- **`IYKYKPromptGenerator` + `IYKYKPromptDiagnostics` (L1-L6×500)**: 检验 3000 组，失败 `0`

## 三、与 v1.2.0 归档（`audit_m5_3_evidence.json.gz`）逐种子因果归因比对

- **v1.2.0 批次哈希**: `48ae2e927fc6c6dc3f19362c806b31e2587df49aad85193f75e2731b18fa4c7d`
- **完全一致种子数**: 0 / 10,000
- **差异种子数**: 10,000 / 10,000
- **已因果归因差异数**: 10,000 / 10,000
- **未解释差异数 (`unexplained_count`)**: `0` (`is_all_explained=True`)
- **等效等级跨版本保持失配数**: `0`
- **首分叉选择器分布**: `{'camera_angle': 251, 'clothing': 5, 'clothing_state': 1, 'nudity': 5008, 'pose': 1, 'shot_type': 1415, 'theme': 3319}`
- **主因归因分布**: `{'m1_m3_clothing_linkage_and_branch_filtering': 1, 'm1_m3_cross_slot_pool_filtering:clothing': 2, 'm1_m3_cross_slot_pool_filtering:shot_type': 1177, 'm1_m3_cross_slot_pool_filtering:theme': 3256, 'm1_m3_nudity_single_branch_convergence': 5008, 'm1_m3_pool_filter_same_category_subsample:clothing': 2, 'm1_m3_pool_filter_same_category_subsample:shot_type': 190, 'm1_m3_pool_filter_same_category_subsample:theme': 49, 'm1_m3_resolver_decision_on_same_category:camera_angle': 246, 'm1_m3_resolver_decision_on_same_category:shot_type': 45, 'm1_m3_resolver_decision_on_same_category:theme': 14, 'm2_outfit_completion_or_repair': 1, 'm3_nudity_subtag_pool_filtering:camera_angle': 5, 'm3_nudity_subtag_pool_filtering:pose': 1, 'm3_nudity_subtag_pool_filtering:shot_type': 3}`
- **已验证机制触发频次**: `{'m1_m3_clothing_linkage_and_branch_filtering': 6654, 'm1_m3_cross_slot_pool_filtering:clothing': 3648, 'm1_m3_cross_slot_pool_filtering:hairstyle': 945, 'm1_m3_cross_slot_pool_filtering:jewelry': 1888, 'm1_m3_cross_slot_pool_filtering:liquids': 1479, 'm1_m3_cross_slot_pool_filtering:props': 1006, 'm1_m3_cross_slot_pool_filtering:shot_type': 1767, 'm1_m3_cross_slot_pool_filtering:tattoo': 831, 'm1_m3_cross_slot_pool_filtering:theme': 3256, 'm1_m3_nudity_single_branch_convergence': 8292, 'm1_m3_pool_filter_same_category_subsample:clothing': 206, 'm1_m3_pool_filter_same_category_subsample:hairstyle': 1, 'm1_m3_pool_filter_same_category_subsample:jewelry': 84, 'm1_m3_pool_filter_same_category_subsample:liquids': 541, 'm1_m3_pool_filter_same_category_subsample:props': 69, 'm1_m3_pool_filter_same_category_subsample:shot_type': 190, 'm1_m3_pool_filter_same_category_subsample:tattoo': 345, 'm1_m3_pool_filter_same_category_subsample:theme': 49, 'm1_m3_resolver_decision_on_same_category:camera_angle': 1174, 'm1_m3_resolver_decision_on_same_category:clothing_state': 510, 'm1_m3_resolver_decision_on_same_category:expression': 2, 'm1_m3_resolver_decision_on_same_category:hairstyle': 38, 'm1_m3_resolver_decision_on_same_category:imperfections': 226, 'm1_m3_resolver_decision_on_same_category:jewelry': 354, 'm1_m3_resolver_decision_on_same_category:lighting': 10, 'm1_m3_resolver_decision_on_same_category:pose': 14, 'm1_m3_resolver_decision_on_same_category:props': 6, 'm1_m3_resolver_decision_on_same_category:shot_type': 45, 'm1_m3_resolver_decision_on_same_category:theme': 14, 'm2_controlled_rebuild': 319, 'm2_outfit_completion_or_repair': 1888, 'm2_outfit_minimal_fallback': 319, 'm3_nudity_subtag_pool_filtering:camera_angle': 106, 'm3_nudity_subtag_pool_filtering:hairstyle': 22, 'm3_nudity_subtag_pool_filtering:pose': 1193, 'm3_nudity_subtag_pool_filtering:shot_type': 80, 'm3_nudity_subtag_pool_filtering:tattoo': 82, 'verified_context_distribution_substream_replay:camera_angle': 1306, 'verified_context_distribution_substream_replay:character': 1463, 'verified_context_distribution_substream_replay:expression': 1843, 'verified_context_distribution_substream_replay:film': 1679, 'verified_context_distribution_substream_replay:hairstyle': 778, 'verified_context_distribution_substream_replay:lighting': 1987, 'verified_context_distribution_substream_replay:makeup': 1364, 'verified_context_distribution_substream_replay:pose': 1242, 'verified_context_distribution_substream_replay:props': 669, 'verified_context_distribution_substream_replay:shot_type': 391, 'verified_context_distribution_substream_replay:tattoo': 531, 'verified_upstream_substream_replay:clothing_state': 4}`
- **等级差异分布**: `{'level_L1': 1701, 'level_L2': 1645, 'level_L3': 1644, 'level_L4': 1681, 'level_L5': 1702, 'level_L6': 1627}`
