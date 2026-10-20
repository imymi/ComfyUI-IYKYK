# ComfyUI-IYKYK M5-3 只读归档因果重评独立报告

> **说明**：本报告由审核器在只读模式下对历史证据归档（`audit_m5_3_evidence.json.gz`）进行完整流式逐条因果重评生成。历史归档文件（`audit_m5_3_evidence.json.gz`）、原始清单（`audit_m5_3_manifest.json`）及历史报告（`audit_m5_3_report.md`）保持原样只读，未被修改或覆盖。

## 一、重评执行依据与环境绑定

- **源归档文件**: `scratch/audit_m5_3_evidence.json.gz`
- **源归档 SHA-256**: `5417aa6529e4a86d2f920d6387ecd88626d70d9fd1973c7896ffa16544b01ff9`
- **源归档大小**: 737,487,743 字节
- **审核器脚本**: `scratch/run_m5_3_audit.py`
- **审核器脚本 SHA-256**: `62faa4edaf68010d25ddbd767f1adb6cfbdbfcd964b2b1ca21e8ac2fac70cb43`
- **目标提交 Commit**: `1db9e675666854d569da85d72f6a39708b81c66c` (Tree: `1530ce71c110f69cba39fee5f9d8d70b841c25ca`, Dirty: `False`)
- **核心源码摘要**: `7641d348b44249de809a73e8e3bfd22c8d7ba48ed4e71f6cdf36fe8ab6a6ee7f`
- **目标批次哈希**: `48ae2e927fc6c6dc3f19362c806b31e2587df49aad85193f75e2731b18fa4c7d`
- **基线批次哈希**: `8762ecaf48c48d49b9f538047bc12e3aec3ad941727a72155f39993d51fd42e4`
- **执行时间**: `2026-10-20 12:35:55 UTC`
- **处理种子总数**: 10,000 组 (连续种子 0..9999)
- **流式读取方式**: 基于 JSONDecoder 块读取流式解包，单条 record 增量解码，无全量内存展开
- **重评总耗时**: 73.94 秒 (平均 7.39 毫秒/种子)

## 二、逐条质量门禁重验结果（复用历史运行证据）

> 本节指标复用归档记录中两轮运行（Round A / Round B）时的原始质量门禁检查与 Oracle 闭包标记，验证历史生成质量。

| 质量门禁指标 | 检查项 | 失败数 | 门禁要求 | 判定 |
|---|---|---|---|---|
| **严格双轮确定性** | `Round A == Round B` | 0 | `== 0` | **PASS** |
| **残余硬冲突** | `unresolved_conflicts == ()` | 0 | `== 0` | **PASS** |
| **词数预算** | `len(positive.split()) <= 250` | 0 | `== 0` | **PASS** |
| **语法合法性** | `validate_prompt_syntax 通过` | 0 | `== 0` | **PASS** |
| **严格二次消解幂等性** | `二次消解决策数 == 0 且原子全等` | 0 | `== 0` | **PASS** |
| **诊断闭包 Oracle 校验** | `validate_audit_json_oracle 通过` | 0 | `== 0` | **PASS** |
| **Provenance DAG 闭环** | `终态原子 parent_ids 非空可追溯` | 0 | `== 0` | **PASS** |

## 三、逐条因果重评归因统计（当前审核引擎现场证明）

> 本节由当前最新审核器对 10,000 组跨版本差异执行现场因果推导，包含 M3 冲突消解规则核实、M2 亲和矩阵合法性与分布验证，以及道具槽位（props）基于手部容量约束与独立子流 RNG 的确定性受控重放。

- **完全一致种子数**: **0** (0.00%)
- **发生差异种子数**: **10,000** (100.00%)
- **其中包含道具手部容量与子流抽样差异种子数**: **116** (全部通过受控重放数学证明)
- **未解释差异种子数**: **0** (门禁严格要求 == 0)

### 差异因果类别明细表

| 差异主归因类别 | 种子数 | 占差异比例 | 机制与规则依据 |
|---|---|---|---|
| `context_affinity_calibration` | 8,943 | 89.43% | M2 情境亲和矩阵上下文权重校准与回退抽样范围保障 |
| `conflict_rule:material_penetration` | 444 | 4.44% | M3 冲突消解规则 `material_penetration` 触发物理互斥消解/状态修正/联动载体裁决 |
| `conflict_rule:framing_lower_body_coherence` | 233 | 2.33% | M3 冲突消解规则 `framing_lower_body_coherence` 触发物理互斥消解/状态修正/联动载体裁决 |
| `conflict_rule:nudity_clothing_conflicts` | 163 | 1.63% | M3 冲突消解规则 `nudity_clothing_conflicts` 触发物理互斥消解/状态修正/联动载体裁决 |
| `conflict_rule:makeup_details_coherence` | 116 | 1.16% | M3 冲突消解规则 `makeup_details_coherence` 触发物理互斥消解/状态修正/联动载体裁决 |
| `conflict_rule:pose_hand_occupation` | 79 | 0.79% | M3 冲突消解规则 `pose_hand_occupation` 触发物理互斥消解/状态修正/联动载体裁决 |
| `conflict_rule:clothing_style_state_coherence` | 22 | 0.22% | M3 冲突消解规则 `clothing_style_state_coherence` 触发物理互斥消解/状态修正/联动载体裁决 |

## 四、未解释差异清单

未发现任何未解释差异种子，全部差异均具备严格物理规则、亲和矩阵或受控重放因果证明。

---
**重评结论**：全部 10,000 条记录流式重验完成，未解释差异数为 **0**。