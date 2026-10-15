# ComfyUI-IYKYK M1 全量测试基线与环境复现报告 (M1-D1)

- **基线 Commit SHA**：`f1b873c2479c287817f5780991e6621e31b9f848` (Merge pull request #2 from imymi/ci/rc10-freeze-docs-only-20261010)
- **独立工作区路径**：`/Users/jacobyang/Hermes/iykyk-affinity-override`
- **工作分支**：`feat/m1-governance-and-overrides`（基于 `f1b873c` 签出，工作树保持干净，与主目录 `.github/workflows/ci.yml` 既有修改物理隔离）
- **复现执行环境**：
  - **环境 A（全功能依赖环境，含 Draft-7 校验）**：`/Users/jacobyang/Hermes/.venv/bin/python3` (Python 3.9.6, 含 `jsonschema 4.25.1`, `pytest 8.4.2`)
  - **环境 B（纯系统原生环境，无第三方包）**：`/usr/bin/python3` (Python 3.9.6, macOS 15.3 Darwin 24.3.0 arm64)
  - **测试框架**：Python 标准库 `unittest`
  - **隔离变量**：`PYTHONPATH=.`

---

## 一、审核方原始 75 项核心基线复现证据

依据审核方第一轮验收基线核验指引，审核方原始运行的核心基线来自以下 5 个核心模块，共计 **75 项测试用例**：
1. [`tests/test_context_affinity_rc8.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_context_affinity_rc8.py) (18 项)
2. [`tests/test_slot_pipeline_integrity.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_slot_pipeline_integrity.py) (14 项)
3. [`tests/test_prompt_diagnostics.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_prompt_diagnostics.py) (38 项)
4. [`tests/test_custom_prompt.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_custom_prompt.py) (2 项)
5. [`tests/test_is_changed.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_is_changed.py) (3 项)

### 1. 执行命令与退出码

在具备 `jsonschema` 的 Python 环境下执行：

```bash
cd /Users/jacobyang/Hermes/iykyk-affinity-override
PYTHONPATH=. /Users/jacobyang/Hermes/.venv/bin/python3 -m unittest \
  tests/test_context_affinity_rc8.py \
  tests/test_slot_pipeline_integrity.py \
  tests/test_prompt_diagnostics.py \
  tests/test_custom_prompt.py \
  tests/test_is_changed.py
```

- **测试用例总数**：75
- **退出码 (Exit Code)**：`0`
- **测试结果**：`OK`
- **执行耗时**：`53.057s`

### 2. 原始 5 模块用例明细分布

| 模块文件 | 测试类 | 用例数 | 状态 | 覆盖核心语义 |
|---|---|---|---|---|
| [`tests/test_context_affinity_rc8.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_context_affinity_rc8.py) | `TestContextAffinityRC8` | 18 | `OK` | 14 情境 × 14 槽位 Registry 白名单、多信号归一化合并、加权抽样 |
| [`tests/test_slot_pipeline_integrity.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_slot_pipeline_integrity.py) | `TestSlotPipelineIntegrity` | 14 | `OK` | 槽位装配链路完整性、跨槽位契约约束、默认值与回退判定 |
| [`tests/test_prompt_diagnostics.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_prompt_diagnostics.py) | `TestPromptDiagnostics` | 38 | `OK` | 提示词诊断指标、Draft-7 JSON Schema 结构校验、异常边界报警 |
| [`tests/test_custom_prompt.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_custom_prompt.py) | `TestCustomPrompt` | 2 | `OK` | 自定义文本合并与追加逻辑、槽位隔离不扩散 |
| [`tests/test_is_changed.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_is_changed.py) | `TestIsChanged` | 3 | `OK` | ComfyUI `IS_CHANGED` 缓存失效哈希与增量变更触发机制 |
| **合计** | **5 个模块** | **75** | **100% 通过** | **75 项原始基线全绿无一失败，退出码 0** |

> **环境依赖关键说明**：
> 在纯系统原生环境 `/usr/bin/python3`（无第三方库）下执行该命令时，`tests/test_prompt_diagnostics.py` 中有 22 项负向 schema 断言会因为缺少 `jsonschema` 库而失败。因此，必须在配置了 `jsonschema 4.25.1` 的虚拟环境（如 `.venv` 或 CI 容器）中运行，才能实现 75 项 100% 完整通过。

---

## 二、专项 75 项冲突 DAG 与规则引擎验证证据

针对冲突规则治理与 DAG 拓扑消解，实施方补充运行了另一组 75 项专项测试：

### 1. 执行命令与退出码

```bash
cd /Users/jacobyang/Hermes/iykyk-affinity-override
PYTHONPATH=. /usr/bin/python3 -m unittest \
  tests/test_conflict_dag_and_invariants.py \
  tests/test_conflict_engine_matrix.py \
  tests/test_conflict_rule_fixtures.py \
  tests/test_context_affinity_rc8.py \
  tests/test_environmental_coherence.py
```

- **测试用例总数**：75
- **退出码 (Exit Code)**：`0`
- **测试结果**：`OK`
- **执行耗时**：`10.716s`

### 2. 专项 5 模块用例明细分布

| 模块文件 | 测试类 | 用例数 | 状态 | 覆盖核心语义 |
|---|---|---|---|---|
| [`tests/test_conflict_dag_and_invariants.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_conflict_dag_and_invariants.py) | `TestConflictDAGAndInvariants` | 31 | `OK` | DAG 无环性、确定性拓扑排序、二次消解幂等性、强弱冲突不变量 |
| [`tests/test_conflict_engine_matrix.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_conflict_engine_matrix.py) | `TestConflictEngineMatrix` | 7 | `OK` | 17 条规则矩阵触发正交性、跨规则状态传递 |
| [`tests/test_conflict_rule_fixtures.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_conflict_rule_fixtures.py) | `TestConflictRuleFixtures` | 16 | `OK` | 规则夹具输入/输出精确断言、删除/保留判定 |
| [`tests/test_context_affinity_rc8.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_context_affinity_rc8.py) | `TestContextAffinityRC8` | 18 | `OK` | 14 情境 × 14 槽位 Registry 白名单、多信号归一化合并、加权抽样 |
| [`tests/test_environmental_coherence.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_environmental_coherence.py) | `TestEnvironmentalCoherence` | 3 | `OK` | 空间环境互斥、昼夜与光影互斥判定 |
| **合计** | **5 个模块** | **75** | **100% 通过** | **75 项冲突 DAG 专项测试全部通过** |

---

## 三、M1 反例与规则双路径回归验证（6 项用例）

针对审核方指出的反例复现偏差（自由文本路径与携带结构化事实原子的路径行为差异），实施方直接消费 [`tests/fixtures/m1_counterexamples.json`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/fixtures/m1_counterexamples.json) 并在 [`tests/test_m1_counterexamples.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_m1_counterexamples.py) 中实现了双路径严格断言：

```bash
cd /Users/jacobyang/Hermes/iykyk-affinity-override
PYTHONPATH=. /Users/jacobyang/Hermes/.venv/bin/python3 -m unittest tests/test_m1_counterexamples.py
```

- **测试用例总数**：6
- **退出码 (Exit Code)**：`0`
- **测试结果**：`OK`
- **执行耗时**：`0.866s`

### 用例明细与复现结论

1. `test_01_fixture_document_integrity`（夹具文档结构完整性与 8 项典型反例契约校验）：
   - **判定分类**：**目标契约已冻结/登记 (Target Contract Frozen)**
   - **验证内容**：直接核验 [`tests/fixtures/m1_counterexamples.json`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/fixtures/m1_counterexamples.json) 夹具的 `schema_version="1.0"`，且完整登记了 `CE-HAND-01` 至 `CE-HAND-03`、`CE-LIGHT-01` 至 `CE-LIGHT-02`、`CE-OVERRIDE-01` 至 `CE-OVERRIDE-03` 等 8 项典型反例与覆盖规约。
2. `test_02_baseline_reproduction_ce_hand_01_dual_path`（`CE-HAND-01` 自由文本与结构化事实双路径复现）：
   - **判定分类**：**基线缺陷已复现 (Baseline Defect Reproduced)**
   - **自由文本路径**：消费 `inputs.free_text_path`（单手托腮、另一手持相机：`"sitting, hand on cheek, looking at viewer"` + `"holding compact camera with other hand"`），基线引擎未识别结构化事实，相机表面得以保留（`retained`）。
   - **结构化事实路径**：消费 `inputs.structured_atom_path`（携带 `hand_state="one_busy"` 姿态原子与 `prop_usage="handheld"` 相机原子），基线引擎 `pose_hand_occupation` 规则将其误判为冲突硬删除（`reason_code="busy_hands_remove_props"`），**确定性复现基线过度约束缺陷**。
3. `test_03_baseline_reproduction_ce_hand_02_dual_path`（`CE-HAND-02` 自由文本与结构化事实双路径复现）：
   - **判定分类**：**基线缺陷已复现 (Baseline Defect Reproduced)**
   - **自由文本路径**：消费 `inputs.free_text_path` (`"holding smartphone in left hand, holding coffee cup in right hand"`)，未精确命中单前缀，表面保留。
   - **结构化事实路径**：消费 `inputs.structured_atom_path`（两个 `prop_usage="handheld"` 道具原子），基线引擎 `handheld_props_single_holder` 规则强行丢弃第 2 个道具（`reason_code="single_handheld_prop_limit"`），**确定性复现基线过度约束缺陷**。
4. `test_04_baseline_reproduction_true_hard_conflict_ce_hand_03`（`CE-HAND-03` 真实硬冲突消解核验）：
   - **判定分类**：**合法消解已核验 (Legitimate Resolution Verified)**
   - **验证内容**：输入双手背后姿态原子（`hand_state="both_busy"`）与持酒杯道具原子，基线规则正确剔除持握道具，证明真实物理互斥判定合规，治理并不破坏真正物理互斥逻辑。
5. `test_05_target_capacity_model_mathematical_contracts`（拟重构 2-Hand 容量模型数学契约）：
   - **判定分类**：**目标契约已冻结/登记 (Target Contract Frozen)**
   - **验证内容**：核验拟在 M3 实现的手部容量模型数学不变量：单手托腮+持相机占用 2 只手 $\le 2$（容量合法）、双手分持两道具占用 2 只手 $\le 2$（容量合法）、双手被绑+持酒杯占用 3 只手 $> 2$（容量超限需消解）。
6. `test_06_slot_override_contract_fixture_specifications`（外部覆盖端口规约夹具校验）：
   - **判定分类**：**目标契约已冻结/登记 (Target Contract Frozen)**
   - **验证内容**：校验 `CE-OVERRIDE-01`（整槽替换且禁止混入旧载体语义）、`CE-OVERRIDE-02`（外部不可消解硬冲突显式报错）、`CE-OVERRIDE-03`（外部覆盖绝对优先于系统预设）等目标契约在反例夹具中的规约字段完整性。
   - **特别声明**：**本用例仅断言规格定义冻结，M3/M4 外部文本覆盖运行时逻辑尚未实现，绝不作为功能已生效的验收证据**。

---

## 四、全量测试套件收集台账（49 模块，共 591 项测试用例）

全量扫描仓库现有 49 个测试模块，共计收集到 **591 项测试用例**（上一轮 590 项，本轮反例拆分为双路径后增加 1 项，达到 591 项）。

| 序号 | 模块文件名 | 用例数 | 核心覆盖内容与语义 | 环境依赖说明 |
|---|---|---|---|---|
| 1 | [`tests/test_bracket_protection.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_bracket_protection.py) | 4 | LoRA、权重括号与转义字符保护契约 | 标准库 |
| 2 | [`tests/test_catalog_rule_reachability.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_catalog_rule_reachability.py) | 10 | 规则目录中可达性与孤立条目检测 | 标准库 |
| 3 | [`tests/test_ci_path_classification.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_ci_path_classification.py) | 9 | CI 文件改动分类器与流水线触发门禁 | 标准库 |
| 4 | [`tests/test_clothing_catalog_integrity.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_clothing_catalog_integrity.py) | 6 | 服装目录词库结构、属性完整性与 Schema | 标准库 |
| 5 | [`tests/test_clothing_expansion_batches.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_clothing_expansion_batches.py) | 14 | 服装扩充批次合法性与情境兼容性 | 标准库 |
| 6 | [`tests/test_conflict_dag_and_invariants.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_conflict_dag_and_invariants.py) | 31 | 冲突图 DAG 拓扑排序、幂等性与不变量 | 标准库 |
| 7 | [`tests/test_conflict_engine_matrix.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_conflict_engine_matrix.py) | 7 | 17 条规则交互矩阵与正交性验证 | 标准库 |
| 8 | [`tests/test_conflict_engine_ssot.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_conflict_engine_ssot.py) | 28 | 规则 SSOT 配置校验与执行链路 | 含 `jsonschema` 依赖 |
| 9 | [`tests/test_conflict_rule_fixtures.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_conflict_rule_fixtures.py) | 16 | 规则夹具输入/输出精确判定 | 标准库 |
| 10 | [`tests/test_context_affinity_matrix.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_context_affinity_matrix.py) | 4 | 亲和矩阵基础计算与分布归一化 | 标准库 |
| 11 | [`tests/test_context_affinity_rc8.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_context_affinity_rc8.py) | 18 | 14 情境 × 14 槽位采样白名单与加权抽样 | 标准库 |
| 12 | [`tests/test_custom_prompt.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_custom_prompt.py) | 2 | 自定义提示词合并逻辑与回退契约 | 标准库 |
| 13 | [`tests/test_data_contracts.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_data_contracts.py) | 3 | 数据目录 Schema 与字段契约约束 | 标准库 |
| 14 | [`tests/test_doc_contracts.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_doc_contracts.py) | 5 | 文档契约与代码标识一致性检验 | 标准库 |
| 15 | [`tests/test_emotion_gaze_cascade.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_emotion_gaze_cascade.py) | 2 | 表情与视线注视级联关系判定 | 标准库 |
| 16 | [`tests/test_environmental_coherence.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_environmental_coherence.py) | 3 | 环境一致性、室内外与昼夜排斥 | 标准库 |
| 17 | [`tests/test_finalize_boundaries.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_finalize_boundaries.py) | 7 | 250 词预算边界截断与括号完整性 | 标准库 (含随机压力测试) |
| 18 | [`tests/test_is_changed.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_is_changed.py) | 3 | ComfyUI IS_CHANGED 缓存判定 | 标准库 |
| 19 | [`tests/test_lexer_and_spans.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_lexer_and_spans.py) | 21 | 词法分析器分词与跨度提取准确性 | 标准库 |
| 20 | [`tests/test_m1_counterexamples.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_m1_counterexamples.py) | 6 | M1 规则反例双路径复现与外部覆盖防污染断言 | 标准库 |
| 21 | [`tests/test_m1_wildcards_slice.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_m1_wildcards_slice.py) | 25 | 通配符解析切片测试集 | 标准库 |
| 22 | [`tests/test_m2_wildcards_batch.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_m2_wildcards_batch.py) | 34 | 通配符批量处理切片测试集 | 标准库 |
| 23 | [`tests/test_m33_runtime_contracts.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_m33_runtime_contracts.py) | 41 | 运行时契约与节点输入输出接口验证 | 标准库 |
| 24 | [`tests/test_m3_slice_resolution.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_m3_slice_resolution.py) | 31 | 槽位解析与消解切片验证集 | 标准库 |
| 25 | [`tests/test_m4_batch1_ingestion.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_m4_batch1_ingestion.py) | 10 | 摄入切片批次 1 测试 | 标准库 |
| 26 | [`tests/test_m4_batch2_ingestion.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_m4_batch2_ingestion.py) | 10 | 摄入切片批次 2 测试 | 标准库 |
| 27 | [`tests/test_m4_batch3_ingestion.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_m4_batch3_ingestion.py) | 9 | 摄入切片批次 3 测试 | 标准库 |
| 28 | [`tests/test_m4_batch4_ingestion.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_m4_batch4_ingestion.py) | 11 | 摄入切片批次 4 测试 | 标准库 |
| 29 | [`tests/test_m4_batch5_ingestion.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_m4_batch5_ingestion.py) | 11 | 摄入切片批次 5 测试 | 标准库 |
| 30 | [`tests/test_m4_batch6_ingestion.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_m4_batch6_ingestion.py) | 10 | 摄入切片批次 6 测试 | 标准库 |
| 31 | [`tests/test_nudity_levels.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_nudity_levels.py) | 6 | 裸露度等级过滤与安全门禁 | 标准库 |
| 32 | [`tests/test_pipeline_bypass_and_provenance.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_pipeline_bypass_and_provenance.py) | 4 | 管道直通与来源追踪元数据验证 | 标准库 |
| 33 | [`tests/test_pose_hand_coherence.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_pose_hand_coherence.py) | 2 | 姿态与手部一致性判定 | 标准库 |
| 34 | [`tests/test_presets_matrix.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_presets_matrix.py) | 1 | 预设矩阵加载与槽位展开 | 标准库 |
| 35 | [`tests/test_prompt_diagnostics.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_prompt_diagnostics.py) | 38 | 提示词诊断体系、Draft-7 JSON Schema 校验 | 含 `jsonschema` 依赖 |
| 36 | [`tests/test_props_and_extensions_reachability.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_props_and_extensions_reachability.py) | 9 | 道具与扩展槽位可达性判定 | 标准库 |
| 37 | [`tests/test_random_generations.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_random_generations.py) | 1 | 随机抽样分布基本检验 | 标准库 |
| 38 | [`tests/test_rc8_quality_gate.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_rc8_quality_gate.py) | 6 | 10,000 随机种子基线聚合摘要门禁 | 标准库 (重型 10k 种子扫描) |
| 39 | [`tests/test_rc9_extended_states_and_catalogs.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_rc9_extended_states_and_catalogs.py) | 9 | 扩展状态与词库数据格式验证 | 标准库 |
| 40 | [`tests/test_rc9_step3_cross_catalogs.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_rc9_step3_cross_catalogs.py) | 13 | 跨目录约束与关联项一致性 | 标准库 |
| 41 | [`tests/test_release_build.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_release_build.py) | 19 | 发布归档与构建脚本依赖校验 | 依赖 Git 与归档环境 |
| 42 | [`tests/test_rng_seed.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_rng_seed.py) | 2 | 随机种子可复现性验证 | 标准库 |
| 43 | [`tests/test_scene_structure.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_scene_structure.py) | 3 | 场景结构分层解析与提取 | 标准库 |
| 44 | [`tests/test_schema_negatives.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_schema_negatives.py) | 15 | 负向 Schema 输入校验与报错捕获 | 含 `jsonschema` 依赖 |
| 45 | [`tests/test_selection_contracts.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_selection_contracts.py) | 11 | 选择器契约与返回值规范 | 标准库 |
| 46 | [`tests/test_semantic_catalogs.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_semantic_catalogs.py) | 42 | 语义目录分类、属性与层级结构完整性 | 标准库 |
| 47 | [`tests/test_slot_pipeline_integrity.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_slot_pipeline_integrity.py) | 14 | 槽位装配链路完整性验证 | 标准库 |
| 48 | [`tests/test_spatial_coherence.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_spatial_coherence.py) | 3 | 空间坐标与位置关系一致性 | 标准库 |
| 49 | [`tests/test_tattoo_scoping.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_tattoo_scoping.py) | 2 | 纹身作用域与身体部位边界约束 | 标准库 |
| **总计** | **49 个测试模块** | **591** | **全量测试资产完备登记** | **0 项遗漏** |

---

## 五、分批全量执行结果与原始日志存证（49 模块，共 591 项测试用例）

为落实审核方“完成全量运行，可以分批；提供各批命令、退出码、通过／失败／跳过数量及原始日志，明确标出未完成项”的要求，实施方将全量 49 个测试模块科学划分为 4 个测试批次，全量执行并生成了对应的独立运行日志存证：

### 1. 分批执行汇总台账

| 批次编号 | 覆盖核心领域 | 模块数 | 用例数 | 退出码 | 执行结果 | 耗时 | 原始日志存证 |
|---|---|---|---|---|---|---|---|
| **Batch 1** | 核心规则、冲突引擎与 DAG 不变量 | 16 | 162 | `0` | **162 Pass, 0 Fail, 0 Skip (100% OK)** | 74.957s | [`tests/logs/batch1_core_rules.log`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/logs/batch1_core_rules.log) |
| **Batch 2** | 运行时、分词器、选择器与 M1 反例 | 15 | 150 | `0` | **150 Pass, 0 Fail, 0 Skip (100% OK)** | 263.742s | [`tests/logs/batch2_runtime_lexer.log`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/logs/batch2_runtime_lexer.log) |
| **Batch 3** | 历史数据摄入切片与因果归因审计 | 9 | 151 | `0` | **151 Pass, 0 Fail, 0 Skip (100% OK)** | 1343.665s | [`tests/logs/batch3_ingestion.log`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/logs/batch3_ingestion.log) |
| **Batch 4** | 质量门禁、跨版本目录校验与边界截断 | 9 | 128 | `0` | **128 Pass, 0 Fail, 0 Skip (100% OK)** | 1847.060s | [`tests/logs/batch4_gates.log`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/logs/batch4_gates.log) |
| **全量合计** | **全部 49 个测试模块** | **49** | **591** | `0` | **591 Pass, 0 Fail, 0 Skip (100% OK)** | 3529.424s | **全部 4 批次独立原始日志完整归档** |

### 2. 各批次详细执行命令与模块清单

#### 批次 1 (Batch 1: 16 模块，162 用例)
- **执行命令**：
```bash
cd /Users/jacobyang/Hermes/iykyk-affinity-override
PYTHONPATH=. /Users/jacobyang/Hermes/.venv/bin/python3 -m unittest \
  tests/test_bracket_protection.py \
  tests/test_catalog_rule_reachability.py \
  tests/test_ci_path_classification.py \
  tests/test_clothing_catalog_integrity.py \
  tests/test_clothing_expansion_batches.py \
  tests/test_conflict_dag_and_invariants.py \
  tests/test_conflict_engine_matrix.py \
  tests/test_conflict_engine_ssot.py \
  tests/test_conflict_rule_fixtures.py \
  tests/test_context_affinity_matrix.py \
  tests/test_context_affinity_rc8.py \
  tests/test_custom_prompt.py \
  tests/test_data_contracts.py \
  tests/test_doc_contracts.py \
  tests/test_emotion_gaze_cascade.py \
  tests/test_environmental_coherence.py \
  2>&1 | tee tests/logs/batch1_core_rules.log
```
- **退出码**：`0`
- **执行结果**：`Ran 162 tests in 74.957s, OK`
- **日志链接**：[`tests/logs/batch1_core_rules.log`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/logs/batch1_core_rules.log)

#### 批次 2 (Batch 2: 15 模块，150 用例)
- **执行命令**：
```bash
cd /Users/jacobyang/Hermes/iykyk-affinity-override
PYTHONPATH=. /Users/jacobyang/Hermes/.venv/bin/python3 -m unittest \
  tests/test_is_changed.py \
  tests/test_lexer_and_spans.py \
  tests/test_m1_counterexamples.py \
  tests/test_m33_runtime_contracts.py \
  tests/test_nudity_levels.py \
  tests/test_pipeline_bypass_and_provenance.py \
  tests/test_pose_hand_coherence.py \
  tests/test_presets_matrix.py \
  tests/test_prompt_diagnostics.py \
  tests/test_props_and_extensions_reachability.py \
  tests/test_random_generations.py \
  tests/test_rng_seed.py \
  tests/test_scene_structure.py \
  tests/test_selection_contracts.py \
  tests/test_tattoo_scoping.py \
  2>&1 | tee tests/logs/batch2_runtime_lexer.log
```
- **退出码**：`0`
- **执行结果**：`Ran 150 tests in 263.742s, OK`
- **日志链接**：[`tests/logs/batch2_runtime_lexer.log`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/logs/batch2_runtime_lexer.log)

#### 批次 3 (Batch 3: 9 模块，151 用例)
- **执行命令**：
```bash
cd /Users/jacobyang/Hermes/iykyk-affinity-override
PYTHONPATH=. /Users/jacobyang/Hermes/.venv/bin/python3 -m unittest \
  tests/test_m1_wildcards_slice.py \
  tests/test_m2_wildcards_batch.py \
  tests/test_m3_slice_resolution.py \
  tests/test_m4_batch1_ingestion.py \
  tests/test_m4_batch2_ingestion.py \
  tests/test_m4_batch3_ingestion.py \
  tests/test_m4_batch4_ingestion.py \
  tests/test_m4_batch5_ingestion.py \
  tests/test_m4_batch6_ingestion.py \
  2>&1 | tee tests/logs/batch3_ingestion.log
```
- **退出码**：`0`
- **执行结果**：`Ran 151 tests in 1343.665s, OK`（含 10,000 种子全量因果归因证明链生成与完整归档，0 未解释漂移）
- **日志链接**：[`tests/logs/batch3_ingestion.log`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/logs/batch3_ingestion.log)

#### 批次 4 (Batch 4: 9 模块，128 用例)
- **执行命令**：
```bash
cd /Users/jacobyang/Hermes/iykyk-affinity-override
PYTHONPATH=. /Users/jacobyang/Hermes/.venv/bin/python3 -m unittest \
  tests/test_finalize_boundaries.py \
  tests/test_rc8_quality_gate.py \
  tests/test_rc9_extended_states_and_catalogs.py \
  tests/test_rc9_step3_cross_catalogs.py \
  tests/test_release_build.py \
  tests/test_schema_negatives.py \
  tests/test_semantic_catalogs.py \
  tests/test_slot_pipeline_integrity.py \
  tests/test_spatial_coherence.py \
  2>&1 | tee tests/logs/batch4_gates.log
```
- **退出码**：`0`
- **执行结果**：`Ran 128 tests in 1847.060s, OK`（含 1,000 次随机边界提示词截断压力测试与 10,000 随机种子质量门禁扫描）
- **日志链接**：[`tests/logs/batch4_gates.log`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/logs/batch4_gates.log)

---

## 六、基线定位与后续变更归因原则

1. **基线记录的是既有行为证据，而非禁止演进**：
   当前 75 项测试及 10,000 种子快照记录的是 rc10 版本的既有决策与文本生成行为。
2. **矩阵与规则优化的输出差异归因**：
   - 本轮治理修复“单手占用误删相机”及扩展亲和候选后，在相同随机种子下，必然会在部分种子上产生不同的文本生成结果与决策记录（例如相机被正确保留、服装候选更加丰富适配）。
   - 该类输出变化属于**预期内的修复与优化**，后续在 M5 验收时，必须提供**差异归因审计表（Divergence Audit）**逐条说明由于哪条规则修正或亲和候选扩展引起的差异，严禁直接粗暴覆盖快照来掩盖潜在缺陷。
