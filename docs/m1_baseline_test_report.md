# ComfyUI-IYKYK M1 全量测试基线与环境复现报告 (M1-D1)

- **基线 Commit SHA**：`f1b873c2479c287817f5780991e6621e31b9f848` (Merge pull request #2 from imymi/ci/rc10-freeze-docs-only-20261010)
- **独立工作区路径**：`/Users/jacobyang/Hermes/iykyk-affinity-override`
- **工作分支**：`feat/m1-governance-and-overrides`（基于 `f1b873c` 签出，工作树保持绝对干净，与主目录 `.github/workflows/ci.yml` 彻底物理隔离）
- **复现执行环境**：
  - Python 版本：`Python 3.9.6 (default, Mar 29 2024, 05:51:30)` (`/usr/bin/python3`)
  - 操作系统平台：`macOS (Darwin 24.3.0 Darwin Kernel Version 24.3.0: Fri Jan 10 23:36:34 PST 2025; root:xnu-11215.81.4~5/RELEASE_ARM64_T8103 arm64)`
  - 测试框架：Python 标准库 `unittest`
  - 环境隔离变量：`PYTHONPATH=.`

---

## 一、核心 75 项专项基线复现证据

依据审核方确认，本轮基线核验的 75 项核心测试来自冲突 DAG、矩阵、规则夹具、rc8 上下文亲和度及环境一致性 5 个核心模块。

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

### 2. 5 个模块用例明细分布

| 模块文件 | 测试类 | 用例数 | 状态 | 覆盖核心语义 |
|---|---|---|---|---|
| [`tests/test_conflict_dag_and_invariants.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_conflict_dag_and_invariants.py) | `TestConflictDAGAndInvariants` | 31 | `OK` | DAG 无环性、确定性拓扑排序、二次消解幂等性、强弱冲突不变量 |
| [`tests/test_conflict_engine_matrix.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_conflict_engine_matrix.py) | `TestConflictEngineMatrix` | 7 | `OK` | 17 条规则矩阵触发矩阵正交性、跨规则状态传递 |
| [`tests/test_conflict_rule_fixtures.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_conflict_rule_fixtures.py) | `TestConflictRuleFixtures` | 16 | `OK` | 规则夹具输入/输出精确断言、删除/保留判定 |
| [`tests/test_context_affinity_rc8.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_context_affinity_rc8.py) | `TestContextAffinityRC8` | 18 | `OK` | 14 情境 × 14 槽位 Registry 白名单、多信号归一化合并、加权抽样 |
| [`tests/test_environmental_coherence.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_environmental_coherence.py) | `TestEnvironmentalCoherence` | 3 | `OK` | 空间环境互斥、昼夜与光影互斥初步判定 |
| **合计** | **5 个模块** | **75** | **100% 通过** | **75 项基线无一失败，测试日志完整固化** |

---

## 二、全量测试套件扫描与既有依赖状态登记

全量扫描仓库现有 48 个测试模块，共计 450+ 测试用例。按环境与依赖分级登记如下：

### 1. 本地标准库直接通过的专项测试模块（28 模块，共 200+ 用例）
全部在 `/usr/bin/python3`（无第三方包）下无任何异常通过：
- [`tests/test_bracket_protection.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_bracket_protection.py) (4)
- [`tests/test_catalog_rule_reachability.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_catalog_rule_reachability.py) (10)
- [`tests/test_clothing_catalog_integrity.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_clothing_catalog_integrity.py) (6)
- [`tests/test_context_affinity_matrix.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_context_affinity_matrix.py) (4)
- [`tests/test_custom_prompt.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_custom_prompt.py) (2)
- [`tests/test_data_contracts.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_data_contracts.py) (3)
- [`tests/test_doc_contracts.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_doc_contracts.py) (5)
- [`tests/test_emotion_gaze_cascade.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_emotion_gaze_cascade.py) (2)
- [`tests/test_finalize_boundaries.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_finalize_boundaries.py) (7)
- [`tests/test_is_changed.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_is_changed.py) (3)
- [`tests/test_lexer_and_spans.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_lexer_and_spans.py) (21)
- [`tests/test_pipeline_bypass_and_provenance.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_pipeline_bypass_and_provenance.py) (4)
- [`tests/test_pose_hand_coherence.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_pose_hand_coherence.py) (2)
- [`tests/test_props_and_extensions_reachability.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_props_and_extensions_reachability.py) (9)
- [`tests/test_random_generations.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_random_generations.py) (1)
- [`tests/test_rng_seed.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_rng_seed.py) (2)
- [`tests/test_scene_structure.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_scene_structure.py) (3)
- [`tests/test_slot_pipeline_integrity.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_slot_pipeline_integrity.py) (14)
- [`tests/test_spatial_coherence.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_spatial_coherence.py) (3)
- [`tests/test_tattoo_scoping.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_tattoo_scoping.py) (2)
- 其余包含 M1~M4 摄入切片测试。

### 2. 第三方包依赖说明（单独登记，绝不假装通过）
- **`jsonschema` 依赖**：[`tests/test_conflict_engine_ssot.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_conflict_engine_ssot.py)（28 项用例中有 2 项直接执行 `import jsonschema`，用于对齐 Draft-7 校验）；在纯系统原生 Python 3.9 环境下因缺失 `jsonschema` 模块报 `ModuleNotFoundError`。在具备依赖的 CI 镜像（如 Node24/Python 3.11 容器）中完整通过。
- **构建打包与 Git 深度依赖**：[`tests/test_release_build.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_release_build.py) 依赖完整 Git 历史与 Release 归档打包环境。
- **10,000 种子全量扫描门禁**：[`tests/test_rc8_quality_gate.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_rc8_quality_gate.py) 包含 10,000 种子现场生成并比对 SHA-256 聚合摘要 `aa7581bc...`，单线程耗时约 60~90s，属于重型发布级质量门禁。

---

## 三、基线定位与后续变更归因原则

1. **基线记录的是证据，而非禁止演进**：
   当前 75 项测试及 10,000 种子快照记录的是 rc10 版本的既有决策与文本生成行为。
2. **矩阵与规则优化的输出差异归因**：
   - 本轮治理修复“单手占用误删相机”及扩展亲和候选后，在相同随机种子下，必然会在部分种子上产生不同的文本生成结果与决策记录（例如相机被正确保留、服装候选更加丰富适配）。
   - 该类输出变化属于**预期内的修复与优化**，后续在 M5 验收时，必须提供**差异归因审计表（Divergence Audit）**逐条说明由于哪条规则修正或亲和候选扩展引起的差异，严禁直接粗暴覆盖快照来掩盖潜在缺陷。
