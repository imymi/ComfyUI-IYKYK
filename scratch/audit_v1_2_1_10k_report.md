# ComfyUI-IYKYK v1.2.1 10,000 种子与全四节点综合审计报告

- **审计提交 Commit**: `6d67ad5a3f615cdfcc01d3e178498df69dc1d4a9` (Tree: `b1ba47a54fc3db7b2f33fda906fe04fe73a603d9`, Dirty: `True`)
- **核心源码摘要 (`core_source_digest`)**: `91887118fbe4af25ac277b27515d9907c70ffbb7e806be5141ff45256faaa7fd`
- **10k 种子汇总哈希 (`target_batch_hash`)**: `a614506a1380c0e986303c9dd856bd4445a9a4d2549bb7b57bdfa42fef935d22`
- **归档文件**: `scratch/audit_v1_2_1_10k_evidence.json.gz` (`2,706,962` 字节, SHA-256: `5569d8ac9423b2f956beb7cefd663727709057269f62869da23309d0ef527cc1`)
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

- **总失败种子数**: `0`
- **等级分布**: `{'L1': 1701, 'L2': 1645, 'L3': 1644, 'L4': 1681, 'L5': 1702, 'L6': 1627}`
- **分支分布**: `{'l1_full_clothed': 1701, 'l2_backless': 303, 'l2_collar_neckline': 256, 'l2_leg_slit': 281, 'l2_midriff_waist': 272, 'l2_shoulder_strap': 269, 'l2_sleeve_wrist': 264, 'l3_bikini_top_removed': 207, 'l3_bra_pushed_up': 190, 'l3_open_shirt_breasts': 192, 'l3_panties_only': 212, 'l3_pulled_down_dress': 203, 'l3_raised_top_breasts': 218, 'l3_topless_pants_kept': 202, 'l3_topless_skirt_kept': 220, 'l4_accessory_only': 341, 'l4_bra_and_panties': 356, 'l4_hosiery_footwear_only': 318, 'l4_panties_only': 356, 'l4_towel_sheet_wrap': 310, 'l5_clothes_discarded_nearby': 829, 'l5_full_nude_standing_or_frontal': 873, 'l6_frontal_genital_display': 433, 'l6_lying_spread_display': 397, 'l6_rear_bent_display': 386, 'l6_squatting_self_touch': 411}`

## 二、全四公开节点分层矩阵验证

- **`IYKYKPresetBrowser`**: 检验 125 组，失败 `0`
- **`IYKYKCustomSlotCombiner`**: 检验 200 组，失败 `0`
- **`IYKYKPromptGenerator` + `IYKYKPromptDiagnostics` (L1-L6×500)**: 检验 3000 组，失败 `0`

## 三、与 v1.2.0 归档（`audit_m5_3_evidence.json.gz`）逐种子比对

- **v1.2.0 批次哈希**: `48ae2e927fc6c6dc3f19362c806b31e2587df49aad85193f75e2731b18fa4c7d`
- **完全一致种子数**: 0 / 10,000
- **差异种子数**: 10,000 / 10,000
- **差异归因分布**: `{'level_L1': 1701, 'level_L2': 1645, 'level_L3': 1644, 'level_L4': 1681, 'level_L5': 1702, 'level_L6': 1627, 'outfit_completion_applied': 1888}`
