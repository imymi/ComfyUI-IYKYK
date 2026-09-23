# 第 3 步增量双版本差异因果归因审计报告

- **增量基线**: `2df289a` (第 2 步收尾提交)
- **受测版本**: 工作区当前状态 (HEAD: `2df289a` + 第 3 步改动)
- **种子范围**: Seeds `0..9999` (共 10,000 种子)
- **基线汇总哈希**: `3e1291ae60af887ebde1869a8fb60f7937424198c1db6a4856bb90aa954725fc` (✅ 100% 吻合)
- **当前汇总哈希**: `ab5a633cfb3fde70d9a6c629ee65a540955d87ee143e66570d780743246cab89`
- **未解释差异项 (UNEXPLAINED)**: **`0`** (PASS (硬门禁通过))
- **权威证据清单**: `scratch/audit_step3_manifest.json`
- **本地归档文件**: `scratch/audit_step3_evidence.json.gz` (142,222,803 字节, 135.6 MiB)
- **原始归档校验和**: `3f06db3894828270604683862c6261f7f62a98ee4117f1302f33684582660b81` (SHA-256)
- **归档策略说明**: 证据文件超过 GitHub 100 MiB 限制且未配置 Git LFS，采取本地保留 + `.gitignore` 忽略策略；仓库保存权威清单、校验和与确定性复现脚本。重跑可保证底层 JSON 语义数据、双版本批哈希与 0 未解释项 100% 确定性一致（gzip 压缩包自身 SHA-256 可能因生成时间戳差异而不同）。
- **确定性复现命令**: `python scratch/audit_step3_cross_catalog.py --seeds 10000 --output-archive scratch/audit_step3_evidence.json.gz --output-report scratch/audit_step3_report.md`

## 差异统计

| 统计指标 | 种子数 | 占比 | 门禁状态 |
|---|---|---|---|
| 完全一致种子 (Bit-exact Identical) | 647 | 6.47% | PASS |
| 差异种子总数 (Divergent Seeds) | 9,353 | 93.53% | INFO |
| 未解释差异 (UNEXPLAINED) | 0 | 0.00% | PASS |

## 归因分类明细

| 归因分类 | 差异种子数 | 占差异比例 |
|---|---|---|
| `PRNG_CANDIDATE_SHIFT(imperfections)` | 5,655 | 60.46% |
| `PRNG_CANDIDATE_SHIFT(imperfections,jewelry)` | 1,611 | 17.22% |
| `NEW_IMPERFECTION_SAMPLED(tan_lines)` | 1,169 | 12.50% |
| `PRNG_CANDIDATE_SHIFT(jewelry)` | 169 | 1.81% |
| `NEW_LINGERIE_SAMPLED(basic_underwear)` | 100 | 1.07% |
| `NEW_JEWELRY_SAMPLED(poncho)` | 89 | 0.95% |
| `NEW_JEWELRY_SAMPLED(neck_ribbon)` | 89 | 0.95% |
| `NEW_JEWELRY_SAMPLED(fur_shawl)` | 88 | 0.94% |
| `NEW_LINGERIE_SAMPLED(crotchless_panties)` | 84 | 0.90% |
| `NEW_JEWELRY_SAMPLED(hooded_cloak)` | 77 | 0.82% |
| `NEW_JEWELRY_SAMPLED(cloak)` | 77 | 0.82% |
| `NEW_JEWELRY_SAMPLED(waist_belt)` | 75 | 0.80% |
| `NEW_JEWELRY_SAMPLED(winter_scarf)` | 70 | 0.75% |
