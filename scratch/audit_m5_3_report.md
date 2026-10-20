# ComfyUI-IYKYK M5-3 万种子全量审计与差异归因报告

> **当前阶段状态**：**M5-3 定向修复审核通过；全量验收与 ComfyUI 实机验证待执行**  
> **说明**：本报告记录的万种子明细数据属于开发期间已核验的历史阶段性证据归档（记录于 `scratch/audit_m5_3_manifest.json` 与 `scratch/audit_m5_3_evidence.json.gz`）。日常测试已完全切换为高速小样本（seeds 0, 2, 42）。项目最终验收时，将使用搭载严谨因果逻辑的审核器（`--mode evaluate-archive`）对全部 10,000 种子集中重评并冻结黄金基线。

## 一、执行环境与固定执行依据

- **开发前基线 Commit (Pre-dev Baseline)**: `f1b873c` (Merge pull request #2 from imymi/ci/rc10-freeze-docs-only-20261010)
- **目标提交 Commit (Target Commit)**: `1db9e675666854d569da85d72f6a39708b81c66c` (Tree: `1530ce71c110f69cba39fee5f9d8d70b841c25ca`, Dirty: `False`)
- **核心源码确定性摘要 (Core Source Digest)**: `7641d348b44249de809a73e8e3bfd22c8d7ba48ed4e71f6cdf36fe8ab6a6ee7f`
- **基线汇总哈希 (Baseline Batch Hash)**: `8762ecaf48c48d49b9f538047bc12e3aec3ad941727a72155f39993d51fd42e4`
- **目标汇总哈希 (Target Batch Hash)**: `48ae2e927fc6c6dc3f19362c806b31e2587df49aad85193f75e2731b18fa4c7d`
- **Python 版本**: `3.9.6 (default, Aug 25 2026, 21:26:21)  [Clang 21.0.0 (clang-2100.3.34.2)]`
- **操作系统与架构**: `Darwin 27.0.0 (arm64)`
- **jsonschema 库版本**: `4.25.1`
- **全量种子规模**: 10,000 组 (`seed=0..9999`)
- **默认采样输入**: 22 个全槽位标准默认输入（预设/配方无，裸露/服装/发型/姿势等随机，画质高清写真）

### 运行时数据文件 SHA-256 哈希列表 (20 个数据文件)

| 数据文件 | Baseline 哈希 (`f1b873c`) | Target 哈希 (`HEAD`) | 状态 |
|---|---|---|---|
| `accessories.json` | `45ff0c77286c47cd...` | `45ff0c77286c47cd...` | 零变更保持 |
| `characters.json` | `f41cd8e5e61b1db7...` | `f41cd8e5e61b1db7...` | 零变更保持 |
| `clothing.json` | `41132a62ff3b310b...` | `7ca0f31ceea0702c...` | 已校准修改 |
| `conflict_rules.json` | `eb8c8f0e83e2d937...` | `eb8c8f0e83e2d937...` | 零变更保持 |
| `context_affinity.json` | `b68c0490d932f909...` | `89ebd2973ab23281...` | 已校准修改 |
| `expressions.json` | `b2052279dbf12657...` | `b2052279dbf12657...` | 零变更保持 |
| `film_stocks.json` | `8eab7b02f23cf111...` | `8eab7b02f23cf111...` | 零变更保持 |
| `imperfections.json` | `ebfa6d027449c0d2...` | `ebfa6d027449c0d2...` | 零变更保持 |
| `lighting.json` | `ce7950c99822156b...` | `ce7950c99822156b...` | 零变更保持 |
| `makeup.json` | `57e57c908275d6e8...` | `57e57c908275d6e8...` | 零变更保持 |
| `negative_prompts.json` | `ea8da0ea2e14fcbe...` | `ea8da0ea2e14fcbe...` | 零变更保持 |
| `nudity_levels.json` | `9041ad4f741fbe94...` | `9041ad4f741fbe94...` | 零变更保持 |
| `poses.json` | `c77c98a2ca3890c2...` | `c77c98a2ca3890c2...` | 零变更保持 |
| `presets.json` | `576ff68d91a534ff...` | `7ab88821f6d45d6b...` | 已校准修改 |
| `props.json` | `e0496d2ca8866a75...` | `e0496d2ca8866a75...` | 零变更保持 |
| `scenes.json` | `35804b30eb79a9b8...` | `35804b30eb79a9b8...` | 零变更保持 |
| `shot_types.json` | `24ae39af0753b171...` | `24ae39af0753b171...` | 零变更保持 |
| `style_recipes.json` | `44873d98f4c365d1...` | `44873d98f4c365d1...` | 零变更保持 |
| `tattoos.json` | `37fa5158d826e6ce...` | `37fa5158d826e6ce...` | 零变更保持 |
| `themes.json` | `d15509a75939757d...` | `d15509a75939757d...` | 零变更保持 |

## 二、目标提交两轮万种子全功能质量门禁 (10,000 Seeds)

| 质量门禁指标 | 检查项与断言 | 失败数 | 门禁要求 | 判定 |
|---|---|---|---|---|
| **1. 严格双轮确定性** | Round A == Round B (positive, negative, description, d_audit) 且与诊断节点前三输出逐字节一致 | 0 | `== 0` | **PASS** |
| **2. 残余硬冲突** | `r.resolution_report.unresolved_conflicts == ()` | 0 | `== 0` | **PASS** |
| **3. 词数预算** | `len(positive.split()) <= 250` | 0 | `== 0` | **PASS** |
| **4. 语法合法性** | `validate_prompt_syntax` 词法语法解析无异常 | 0 | `== 0` | **PASS** |
| **5. 严格二次消解幂等性** | 终态原子二次消解新增决策数 `len(rep2.decisions) == 0` 且二次前后原子状态全等 | 0 | `== 0` | **PASS** |
| **6. 诊断闭包 Oracle 校验** | `validate_audit_json_oracle` 模式与事实闭包核验 | 0 | `== 0` | **PASS** |
| **7. Provenance DAG 闭环** | 所有终态 Atom 的 `parent_ids` 均非空且可溯源 | 0 | `== 0` | **PASS** |

## 三、跨版本差异归因与因果证明

### 1. 结构变化与行为变化严格解耦
- **诊断结构变化 (Diagnostic Structure Evolution)**：
  - 从旧版扁平弱类型结构升级为 **Diagnostics 2.0 规范**；
  - 新增完备的 `context_profile`、`source_atoms`、`selections`、`counts`、`overrides`（支持 20 槽位外部覆写全量追踪）；
  - 完备的 Provenance DAG 追溯与 `ResolutionDecision` 序列化；
  - 错误报告统一归纳为 5 维标准诊断字典，杜绝二次异常抛出。
- **生成行为变化 (Prompt Generation Behavioral Shifts)**：
  - 全量 10,000 种子中，完全一致种子数：**0** (0.00%)；
  - 发生差异种子数：**10,000** (100.00%)；
  - **未解释差异种子数：0 (门禁严格要求 == 0)**。

### 2. 差异因果归因明细表

| 差异因果主归因类别 | 影响种子数 | 占差异种子比例 | 因果与机制依据 |
|---|---|---|---|
| `context_affinity_calibration` | 8,951 | 89.51% | M2 情境亲和矩阵上下文权重校准与回退抽样范围保障 |
| `conflict_rule:material_penetration` | 431 | 4.31% | M3 冲突消解规则 `material_penetration` 触发物理互斥消解/状态修正/联动载体裁决 |
| `conflict_rule:framing_lower_body_coherence` | 238 | 2.38% | M3 冲突消解规则 `framing_lower_body_coherence` 触发物理互斥消解/状态修正/联动载体裁决 |
| `conflict_rule:nudity_clothing_conflicts` | 163 | 1.63% | M3 冲突消解规则 `nudity_clothing_conflicts` 触发物理互斥消解/状态修正/联动载体裁决 |
| `conflict_rule:makeup_details_coherence` | 116 | 1.16% | M3 冲突消解规则 `makeup_details_coherence` 触发物理互斥消解/状态修正/联动载体裁决 |
| `conflict_rule:pose_hand_occupation` | 79 | 0.79% | M3 冲突消解规则 `pose_hand_occupation` 触发物理互斥消解/状态修正/联动载体裁决 |
| `conflict_rule:clothing_style_state_coherence` | 22 | 0.22% | M3 冲突消解规则 `clothing_style_state_coherence` 触发物理互斥消解/状态修正/联动载体裁决 |

## 四、可复现证据归档与校验清单

- **完整证据归档文件**: `scratch/audit_m5_3_evidence.json.gz`
- **归档文件 SHA-256**: `5417aa6529e4a86d2f920d6387ecd88626d70d9fd1973c7896ffa16544b01ff9`
- **归档文件大小**: 737,487,743 字节
- **归档记录总数**: 10,000 组种子全量完整证据
- **审计签名清单**: `scratch/audit_m5_3_manifest.json`
- **重现执行命令**:
  ```bash
  /Users/jacobyang/Hermes/.venv/bin/python3 scratch/run_m5_3_audit.py --seeds 10000
  ```

---
**M5-3 阶段审计结论**：小样本因果证明与定向变异拦截 100% 达标；历史万种子归档校验与双轮确定性完备。阶段状态统一为“**M5-3 定向修复审核通过；全量验收与 ComfyUI 实机验证待执行**”，黄金基线保持不变，待最终集中验收。