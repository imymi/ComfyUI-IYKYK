# ComfyUI-IYKYK M5-4 使用文档、迁移指南与最终验收清单

> **当前阶段状态**：**M5-3 定向修复与归档因果重评通过；ComfyUI 实机四类场景验证通过；黄金基线已接入**  
> **目标版本提交 (Git Commit)**：`1db9e675666854d569da85d72f6a39708b81c66c`  
> **开发前基线提交 (Baseline Commit)**：`f1b873c` (Merge pull request #2 from imymi/ci/rc10-freeze-docs-only-20261010)  
> **黄金基线测试哈希 (`m5_expected_hash`)**：对应 [`tests/test_rc8_quality_gate.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_rc8_quality_gate.py#L1088) 中的 `m5_expected_hash` (`48ae2e927fc6c6dc3f19362c806b31e2587df49aad85193f75e2731b18fa4c7d`)。已保留 M1 历史演进链基线并绑定已审核的 M5 归档与输入契约，严禁与 Git 提交号混淆。

---

## 一、系统概览与节点使用文档 (Usage Documentation)

### 1. 核心节点与职责解耦
- **生成器节点**：`IYKYK Prompt Generator` (`IYKYKPromptGenerator`)
- **诊断节点**：`IYKYK Prompt Diagnostics` (`IYKYKPromptDiagnostics`)
- **实现文件**：[`nodes.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/nodes.py)
- **底层架构解耦**：
  - 词法解析：[`lib/lexer.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/lib/lexer.py)
  - 词库采样：[`lib/sampler.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/lib/sampler.py)
  - 冲突消解与语义裁决：[`lib/conflict_resolver.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/lib/conflict_resolver.py)
  - 上下文亲和度矩阵：[`lib/context_affinity.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/lib/context_affinity.py)
  - 终态原子装配：[`lib/assembler.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/lib/assembler.py)
  - 诊断与闭环验证：[`lib/models.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/lib/models.py)

### 2. 节点输入端口规范
节点输入由 **22 个主槽位/选项必选输入**、**2 个框架通用可选输入** 以及 **20 槽位外部覆写端口 (Override Ports)** 构成。

#### (1) 22 个主输入参数 (与原执行器完全一致)
| 输入参数名 | 默认值 | 采样机制 / 选项说明 |
|---|---|---|
| `预设模板` | "无 (None)" | 预设模版，可一键锁定核心风格与情境组合 |
| `风格配方` | "无 (None)" | 艺术风格配方，注入色调、胶片与光影组合 |
| `场景大类` | "随机 (Random)" | 场景环境（卧室、街头、海滩、病房、废墟等） |
| `剧情主题` | "随机 (Random)" | 主题情境（日常、私密、拘束、医疗等） |
| `景别构图` | "自动 (Auto)" | **自动上下文亲和抽样**（全身、中景、特写、大特写等） |
| `拍摄视角` | "自动 (Auto)" | **自动上下文亲和抽样**（平视、俯视、仰视、第一人称等） |
| `裸露等级` | "随机 (Random)" | 尺度等级（L1 包裹暗示 至 L6 特写全见） |
| `服装款式` | "随机 (Random)" | 服装款式（制服、泳装、内衣、便服、礼服等） |
| `服装状态` | "自动联动裸露等级 (Auto Link Nudity)" | **规则固定联动**（根据裸露等级自动确定受损/半解/穿着状态） |
| `发型发色` | "随机 (Random)" | 发型与头饰（高马尾、丸子头、披肩发、短发等） |
| `饰品头饰` | "随机 (Random)" | 首饰饰品（项链、耳环、身体链、手铐、发饰等） |
| `妆容细节` | "随机 (Random)" | 妆容细节（素颜、浓妆、眼泪、红晕、唇印等） |
| `姿势动作` | "随机 (Random)" | 姿势动作（站姿、坐姿、趴姿、跪姿、动态手部动作等） |
| `情绪表情` | "随机 (Random)" | 面部表情与情绪（微笑、沉醉、诱惑、痛苦、羞耻等） |
| `光影预设` | "自动 (Auto)" | **自动上下文亲和抽样**（自然窗光、霓虹侧光、伦勃朗光等） |
| `胶片风格` | "随机 (Random)" | 胶片色调（柯达、富士、拍立得、黑白高反差等） |
| `液体效果` | "随机 (Random)" | 液体体液系统（汗水淋漓、水珠滑落、泪水、体液等） |
| `纹身标记` | "随机 (Random)" | 纹身图案（曼陀罗、花卉、正字计数、锁骨小图等） |
| `道具物件` | "随机 (Random)" | 道具与伴随物（花束、蜡烛、宠物猫、手机、饮品等） |
| `角色设定` | "随机 (Random)" | 人格与角色特征（护士、邻家女友、写真偶像、职场等） |
| `真实微瑕` | "随机 (Random)" | 皮肤细节与质感（汗水、泛红、泪痕、毛孔、细微瑕疵） |
| `画质等级` | "高清写真 (High)" | 基础画质保障与负向提示词增强等级 |

#### (2) 框架可选输入
- `prompt_seed` (INT, 默认 -1): 随机种子，-1 为动态随机抽卡，非负整数保证 100% 确定性可复现。
- `自定义提示词` (STRING, 默认 ""): 追加到正向提示词，支持多行文本、LoRA 和权重语法。

---

## 二、20 槽位外部覆写端口规范 (Override Ports Specification)

节点通过 `f"{slot}_override"` 后缀端口支持对全项目 20 个规范槽位（18 个装配流水线槽位 + 2 个辅助扩展槽位）进行精确接管：

### 1. 20 个规范覆写端口清单
1. `scene_theme_override`：外部场景主题覆盖（整合场景大类与剧情环境，非独立拆分）。
2. `shot_type_override`：外部景别构图覆盖。
3. `camera_angle_override`：外部拍摄视角覆盖。
4. `character_override`：外部角色设定覆盖。
5. `nudity_override`：外部裸露等级覆盖。
6. `clothing_override`：外部服装款式覆盖。
7. `lighting_override`：外部光影氛围覆盖。
8. `pose_override`：外部姿势动作覆盖。
9. `expression_override`：外部情绪表情覆盖。
10. `makeup_override`：外部妆容细节覆盖。
11. `hairstyle_override`：外部发型发色覆盖。
12. `jewelry_override`：外部饰品头饰覆盖。
13. `imperfections_override`：外部真实微瑕覆盖。
14. `tattoo_override`：外部纹身标记覆盖。
15. `props_override`：外部道具物件覆盖。
16. `liquids_override`：外部液体效果覆盖。
17. `film_override`：外部风格胶片覆盖。
18. `quality_override`：外部画质等级覆盖（接管渲染画质与摄影修饰）。
19. `style_recipe_override`：外部风格配方覆盖（接管艺术风格配方）。
20. `custom_override`：外部自定义追加覆盖（注入自定义扩展提示词）。

### 2. 覆写输入格式支持
覆写端口支持以下三种数据输入形式：
1. **纯文本字符串 (Comma-separated Tags)**：如 `"black silk nightgown, lace trim"`，自动原子化为 `PromptAtom`。
2. **结构化字典 (Atom / Tag Dict)**：如 `{"text": "black silk nightgown", "facts": {"visible_regions": ["upper_body"]}}`。
3. **PromptAtom 实例列表**：由上游高级自定义节点直接注入的强类型原子序列。

### 3. 覆写语义与溯源契约
- **外部标记**：所有由 override 端口注入的原子，其 `is_external_atom()` 均严格返回 `True`。
- **Provenance 跟踪**：`origin.mode = "override"`，`origin.selector = "<slot_name>"`。
- **物理冲突消解**：外部覆写原子拥有与内生采样原子同等的语义分析待遇，若覆写文本与裸露等级或其他物理规则产生硬冲突，冲突消解引擎将记录并在 `diagnostics_audit.overrides` 中完整披露。

---

## 三、节点输出项与 Diagnostics 2.0 规范

### 1. 节点输出项区分
各节点根据其业务定位提供明确的输出项定义：
- **`IYKYKPromptGenerator` (提示词生成器)** 输出 **3 项**：
  1. `正面提示词 (STRING)` (`positive_prompt`)
  2. `负面提示词 (STRING)` (`negative_prompt`)
  3. `中文场景描述 (STRING)` (`prompt_description`)
- **`IYKYKPromptDiagnostics` (提示词诊断节点)** 输出 **4 项**：
  1. `正面提示词 (STRING)` (`positive_prompt`)
  2. `负面提示词 (STRING)` (`negative_prompt`)
  3. `中文场景描述 (STRING)` (`prompt_description`)
  4. `审计报告 JSON (STRING)` (`diagnostics_audit`)

### 2. Diagnostics 2.0 结构化 JSON 契约
审计报告符合 `schemas/diagnostics.schema.json` 契约，核心结构如下：
```json
{
  "schema_version": "2.0",
  "status": "success",
  "effective_seed": 42,
  "context_profile": {
    "scene_item_id": "scene_honeymoon_suite",
    "theme_id": "theme_chikan_ntr",
    "scene_context_ids": ["domestic"],
    "theme_context_ids": ["adult", "transit"],
    "weights": {
      "domestic": 0.625,
      "adult": 0.1875,
      "transit": 0.1875
    }
  },
  "selections": [
    {
      "selector": "clothing",
      "selected_id": "convenience_store",
      "mode": "random",
      "raw_value": "随机 (Random)",
      "sampling_rationale": {
        "active_contexts": ["domestic", "adult", "transit"],
        "candidate_filter": {
          "total_candidates": 38,
          "excluded_hard_conflicts": [],
          "passed_candidates": 38
        },
        "distribution": {
          "camisole_slip": 0.0825,
          "convenience_store": 0.0125
        },
        "fallback": {"used": false, "reason": null},
        "final_selection": {
          "mode": "affinity_sampling",
          "selected_id": "convenience_store"
        }
      },
      "source_atoms": [ ... ],
      "accepted_atoms": [ ... ]
    }
  ],
  "decisions": [
    {
      "decision_id": "dec_0000_nudity_clothing_conflicts_drop",
      "sequence": 0,
      "rule_id": "nudity_clothing_conflicts",
      "phase": "anchors",
      "action": "drop",
      "reason_code": "nudity_removes_clothing",
      "target_atom_id": "atom_123456",
      "winner_atom_ids": ["atom_654321"],
      "produced_atom_ids": [],
      "before_text": "erotic close-up",
      "after_text": null
    }
  ],
  "overrides": [
    {
      "canonical_slot": "scene_theme",
      "source_port": "scene_theme_override",
      "status": "not_provided",
      "blank_fallback_reason": "omitted",
      "raw_input": null,
      "injected_atom_ids": [],
      "replaced_target_ids": [],
      "replaced_texts": []
    }
  ],
  "counts": {
    "accepted_atoms": 26,
    "budget_filtered": 0,
    "deduplicated": 0,
    "dropped": 6,
    "injected": 0,
    "produced": 0,
    "replaced": 0,
    "source_atoms": 32
  },
  "rules_applied": ["nudity_clothing_conflicts"],
  "unresolved_conflicts": [],
  "error_info": null
}
```

---

## 四、版本迁移与差异说明 (Migration Notes)

从 `v1.1.0-rc10` (`f1b873c`) 升级至当前提交 `1db9e67` 的核心行为与接口变更：

### 1. 结构化与接口变更
- **诊断结构全面升级为 Diagnostics 2.0**：弃用旧版扁平字典，统一采用包含 `effective_seed`、`context_profile`、`selections`、`decisions`、`overrides`（数组型）和 `counts`（8 项集合分区计数）的全闭环 Provenance DAG。
- **支持 20 槽位全覆盖覆写**：不再局限于旧版的 6 个槽位覆写，允许任意外部工作流精细接管任一环节。
- **全状态二次消解幂等性**：终态输出经过二次消解验证，确保无新增决策且原子语义字段（`facts`、`provenance`、`origin`）零漂移。

### 2. 生成行为差异归因说明
> **说明**：以下数据为**历史阶段性运行结果**。在项目最终验收时，将使用搭载 v3.0 严谨因果逻辑的审核器（`--mode evaluate-archive`）对全部 10,000 个种子重新执行全量因果归因判定。
- **情境亲和度校准 (历史 8,951 种子，89.51%)**：
  - 更新了 196 个情境—槽位单元的候选权重矩阵；
  - 修复了回退均匀采样偏差，改为严格归一化概率离散采样。
- **物理规则与语义互斥裁决 (历史 1,049 种子，10.49%)**：
  - `material_penetration` (431 种子)：解决贴身内衣、丝袜与外衣材质穿透与可见性冲突；
  - `framing_lower_body_coherence` (238 种子)：面部特写与胸部构图下自动裁决隐藏不可见下半身与鞋袜标签；
  - `nudity_clothing_conflicts` (163 种子)：全裸/显微细节等级下自动消除多余服装；
  - `makeup_details_coherence` (116 种子)：消除面部浓妆与素颜/红晕之间的矛盾；
  - `pose_hand_occupation` (79 种子)：手部占用与道具持握的物理互斥裁决；
  - `clothing_style_state_coherence` (22 种子)：服装款式与其特定受损/穿着状态的一致性保护。

---

## 五、最终验收项目清单 (Final Acceptance Checklist)

> **注意**：日常开发已采用小样本（seeds 0, 2, 42）高速测试（耗时 < 12 秒），全量耗时测试统一留待**项目验收**时集中执行。  
> **关于局部测试用例说明**：小样本单元测试中的规则产物用例（`test_03j`）系人工构造的无纹身场景，专门用于核验规则产物随母原子消失的局部因果分支逻辑，不能替代相同输入下的真实跨版本全链路验证。

| 验收项目 | 执行范围与命令 | 通过标准 | 当前就绪状态 |
|---|---|---|---|
| **1. 小样本因果、归档证据与负向测试** | `python3 -m unittest tests/test_m5_3_audit_engine.py` | 54 项变异、流式边界与归档证据反例全部通过 (< 2.6s) | **已通过 (54/54 OK)** |
| **2. 运行时数据文件完整性** | `python3 scripts/validate_data.py` | 20 个 JSON 数据文件零错误、零警告 | **已通过 (0 errors, 0 warnings)** |
| **3. 诊断与 Oracle 闭包验证** | `python3 -m unittest tests/test_prompt_diagnostics.py` | 60 项诊断 Oracle 测试全部通过 | **已通过** |
| **4. 万种子双轮质量门禁** | `python3 scratch/run_m5_3_audit.py --mode evaluate-archive` | 7 大门禁零失败，未解释差异严格为 0 | **已通过 (10,000 种子 0 未解释，116 道具子流验证)** |
| **5. 预设与槽位全量矩阵覆盖** | 80 组节点—槽位组合与 693 组预设配方测试 | 关联测试 100% 通过，规则 Schema 零漂移 | **已通过 (693 组预设配方验证完成)** |
| **6. ComfyUI 实机工作流验证** | 启动 ComfyUI 实机环境，核验四类关键场景（基础生成与双轮确定性、Diagnostics 2.0 闭包、单槽位外部覆写接管、物理规则尺度与服装联动） | 节点加载无报错，文本生成、override 接管与诊断输出正常 | **已通过（据实机反馈，四类场景验证通过；20 槽位全覆盖已关联单元与矩阵自动化测试完成全面证明）** |
| **7. 黄金基线测试哈希接入与证据绑定** | 运行 [`tests/test_rc8_quality_gate.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/tests/test_rc8_quality_gate.py#L1088) 中的黄金校验 | 保留历史演进链，绑定 M5 归档证据，流式重算哈希 48ae2e92...，防篡改反例通过，禁止意外重新生成 | **已通过（test_03 / test_03b / test_03c 全部通过）** |

---

## 六、交付与归档证据文件索引 (Delivery Artifacts Index)

### 1. 随代码提交的核心固件与工具

| 交付文件路径 | 大小 (Bytes) | SHA-256 校验和 | 属性与说明 |
|---|---|---|---|
| [`scratch/audit_m5_3_sample_seeds.json`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/scratch/audit_m5_3_sample_seeds.json) | 2,340,517 | `f978a52188c65497dc0e836dfb6119576731986cd2890b62c61ef325020c476d` | 日常高速测试小样本真实固件（包含且仅包含代表种子 0、2、42） |
| [`scratch/audit_m5_3_prop_seeds.json`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/scratch/audit_m5_3_prop_seeds.json) | 2,144,508 | `17493f6c6ae90425eeacaa64c97f47ad4385afd5d40a2acdf68ec6e401b97e23` | **代表种子 124、149、200**（道具抽样子流真实样本固件，该文件实际包含且仅包含 3 个代表性样本） |
| [`scratch/audit_m5_3_reeval_manifest.json`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/scratch/audit_m5_3_reeval_manifest.json) | 2,059 | `b7fad9071a8b9402843fa42e6942956af937f87fc1a9200f0dcc5177a2c23bdf` | 万种子因果重评清单（记录 7 大门禁、核心源码确定性摘要与目标汇总哈希） |
| [`scratch/pack_comfy_node.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/scratch/pack_comfy_node.py) | 8,388 | `a593b79453b7b7ecfd7989e86e95b0aad60b7859d6b36e84232c87596d39bf61` | ComfyUI 纯净轻量化节点打包与自动化验证脚本 |

### 2. 离线只读大归档台账（保持只读复用）

| 离线归档文件路径 | 大小 (Bytes) | SHA-256 校验和 | 属性与说明 |
|---|---|---|---|
| `scratch/audit_m5_3_evidence.json.gz` | 737,487,743 | `5417aa6529e4a86d2f920d6387ecd88626d70d9fd1973c7896ffa16544b01ff9` | 历史万种子双轮生成全量证据归档（只读复用，7 大门禁全绿） |
| `scratch/controlled_ref_data_10k.json.gz` | 77,984,059 | `3268962e6621bee8c10ced9f3ac3899244f3f30920734b7856df17881b42b2e4` | M1 垂直切片受控参考数据快照（历史 M1 基线缓存） |

### 3. ComfyUI 纯净发布包

- **目录产物**：[`dist/ComfyUI-IYKYK/`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/dist/ComfyUI-IYKYK)（43 个运行时核心文件，22.85 MB）
- **ZIP 发布包**：[`dist/ComfyUI-IYKYK.zip`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/dist/ComfyUI-IYKYK.zip)（1.34 MB，SHA-256: `8b35a37f00f90774e403fc0fa2445c416f6dd6fddc5971be28d5ef8415f94c2a`）
