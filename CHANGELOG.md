# 更新日志 (CHANGELOG)

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/) 规范。

---

## [v1.1.0-rc3] - 2026-09-01

> 💡 **审查与架构声明**：  
> 本版本的全面架构审查、数据质量复评、JSON Schema 体系设计、全量自动化测试矩阵（31 项）及全部技术修改方案均出自 **GPT-5.6 Sol** (Architecture Review, Data Quality Auditing & Technical Solutions proposed by GPT-5.6 Sol)。

### 🌟 核心改进与技术落实

#### 1. 场景数据重新基线迁移与质量加固
- **可信基线迁移** (`scripts/migrate_scenes_v1_1.py`)：
  - 严格以 Git 基线 `09d9942:data/scenes.json` 为可信输入重新生成数据。
  - 单次分支安全展平标签，实现保序严格去重：
    - `anchor_tags` 内部零重复；
    - `detail_tags` 内部零重复；
    - `anchor_tags` 与 `detail_tags` **严格零交集 (Zero Intersection)**；
    - `tags` 兼容数组零重复。
- **显式上下文期望表与断言** (`data/scene_context_expectations.json`)：
  - 为全部 122 个场景建立显式语义映射，CI 对 122 个 ID 进行 100% 精确断言。
- **Context Affinity 映射补齐** (`lib/sampler.py`)：
  - 新增 `CONTEXT_PARENT_MAPPING`，将 `transit`、`outdoor`、`dining`、`adult`、`special`、`generic` 映射至明确父级情境，杜绝任何合法 Context 静默退化。
- **UI 展示名消歧**：
  - 消歧重名项（如区分 `学校保健室`、`风俗窥视室`、`天台/大厦楼顶`、`学校天台`），实现 122 个子分类展示名 100% 唯一。

#### 2. 结构化 Metadata 全链路流水线贯通
- **数据模型扩展** (`lib/models.py`)：
  - `PromptFragment` 扩展支持 `exclusive_group: Optional[str] = None`。
- **全链路元数据保留** (`nodes.py`, `lib/assembler.py`)：
  - 主生成路径直接从 `SampleResult` 构造携带 `source_item_id`、`context_ids` 与 `exclusive_group` 的 `PromptFragment`。
  - `PromptAssembler.assemble_to_fragments` 完整透传已有片段元数据，不再降级为纯文本。
- **空间冲突消解升级** (`lib/conflict_resolver.py`)：
  - 优先依据 `exclusive_group` 和输入声明顺序判定主空间，同 group 内 anchor 与 detail 自由共存，对无元数据的自由输入降级使用词法 fallback。

#### 3. JSON Schema 真正门禁与负向测试套件
- **Schema 与数据对齐** (`schemas/`)：
  - 8 个风格配方补充稳定 ID（`recipe_av_cover`, `recipe_90s_hk_cinema` 等），与 `recipes.schema.json` 严格一致；
  - `scenes.schema.json` 显式校验 14 大合法 Context 枚举。
- **数据强校验引擎** (`scripts/validate_data.py`)：
  - 自动调用 `jsonschema` 并执行跨文件非空与零交集强门禁。
- **负向测试套件** (`tests/test_schema_negatives.py`)：
  - 覆盖非法 Context ID、重复 Scene ID、空 Anchor、Anchor/Detail 交集冲突等 5 项负向拦截测试。

#### 4. 确定性可重复构建与 CI 双构建比对
- **单一版本源**：
  - 构建脚本从 `pyproject.toml` 动态读取版本，并自动同步生成 `js/version.js`，前端从 `version.js` 引用 `EXTENSION_VERSION`。
- **确定性可重复构建** (`scripts/build_release.py`, `.github/workflows/ci.yml`)：
  - 支持 `--output-dir` 参数，独立双目录构建得到 **100% 相同 SHA256**；
  - 自动运行全功能烟雾测试（覆盖 3 个节点的 `INPUT_TYPES`、`IS_CHANGED` 以及生成/浏览/拼装）。
- **口径澄清** (`README.md`)：
  - 明确区分 15 用户配置控件、16 步视觉认知流水线、18 项内部流水线调度槽位。

---

## [v1.1.0] - 2026-09-01

### 🚀 P1 & P2 架构级重构与工程化加固 (Architecture & Engineering Upgrade)

#### 1. 结构化提示词管道与统一 Finalize
- **引入 `PromptFragment` 模型** (`lib/models.py`)：
  - 弃用扁平字符串模糊查找与正则替换，重构为带槽位来源（`source_slot`）、稳定条目ID（`source_item_id`）与执行顺序（`order`）的结构化管道。
- **三入口公共后处理流水线** (`lib/assembler.py`)：
  - 生成器、预设浏览器与自定义拼装器统一汇入 `finalize_prompt`：
    1. 空白与格式标准化；
    2. 基于 `PromptFragment` 结构化消解冲突；
    3. 保留首见顺序去重；
    4. 完整片段边界 250 词上限截断（绝不破坏嵌套括号）。
- **顶层逗号解析与高级语法保护** (`split_top_level_tags`)：
  - 完整保护 ComfyUI 权重语法 `(masterpiece:1.2)`、步数混合 `[blouse:sweater:10]` 及 `<lora:name:0.8>`，忽略被括号包裹的内部逗号。

#### 2. 确定性可复现与随机数链加固
- **引入 `prompt_seed` 控件** (`nodes.py`)：
  - 节点新增 `prompt_seed`（`-1` 保持自动动态随机，非负整数开启 100% 确定性复现）。
  - 单一 RNG 链自顶向下传递，杜绝模块调用全局 `random.*` 泄漏。
  - `IS_CHANGED` 智能缓存：固定 seed 时返回参数哈希，启用 ComfyUI 原生缓存。

#### 3. 冲突消解精准作用域修复
- **修复纹身标签误触发** (`lib/conflict_resolver.py`)：
  - 纹身真皮层融合标签严格收拢至 `tattoo` 槽位，彻底消除 `pink`（粉色）、`drink`（饮料）、`link` 等正常单词引发的幽灵误判。
- **空间互斥按顺序判定**：
  - 严格按提示词声明的 `order` 锁定第一个有效主场景，消除字典遍历顺序不确定性。

#### 4. 工程化门禁、Schema 校验与纯净打包
- **自动化测试体系** (`tests/`)：
  - 新增 16 项自动化测试（RNG 500 次复现性、77 预设 × 8 配方矩阵、括号语法保护、纹身作用域、空间互斥）。
- **数据集 Schema 与校验工具** (`scripts/validate_data.py`)：
  - 自动校验 19 个运行时 JSON 文件完整性与 ID 唯一性。
- **显式运行时诊断** (`DataLoadError`):
  - 缺失或语法错误的数据文件提供精确行列报错，杜绝静默返回 `{}`。
- **发布打包自动化** (`scripts/build_release.py`, `pyproject.toml`)：
  - 严格通过白名单构建纯净发布包，排除测试脚本与中间素材。

---

## [v1.0.2] - 2026-09-01

### 🛡️ 空间与环境自洽互斥引擎 (Spatial & Environmental Coherence Engine)
- **重构空间与剧情题材词库** (`data/themes.json`, `data/scenes.json`)：
  - 将 `themes.json` 从原本包含大量混合场景的粗糙列表重构为 **39 套纯粹的 JAV 经典剧情与氛围题材**，彻底剥离重复物理场所。
  - 清理废弃或歧义词条（将 `spinning room` 精准修正为 `drunken stupor` 醉酒状态）。
  - 在 `scenes.json` 引入 **单一空间锚点与兼容细节机制**，单次采样严格选定一个自洽的物理场所。
- **新增规则 8：空间与环境自洽互斥引擎** (`lib/conflict_resolver.py`, `data/conflict_rules.json`)：
  - **场所集群互斥**：自动消解「温泉洗浴」与「餐饮包厢」、「交通工具」、「写字楼/教室」的混杂冲突。
  - **室内外物理互斥**：自动检测露天雪景/野外草丛与室内温泉/更衣室等冲突。
  - **温泉/居家子空间细化去重**：自动消解 `indoor onsen`、`rotenburo`、`onsen changing room` 的矛盾并存。
- **自洽场景采样器升级** (`lib/sampler.py`)：
  - 单次随机出词严格锁定单一主锚点及至多 1~2 个兼容环境细节。

---

## [v1.0.1] - 2026-09-01

### 🔧 优化与改进 (Improvements & Fixes)
- **预设模板与风格配方融合优化** (`lib/assembler.py`)：在预设模板模式下支持叠加 8 大风格配方。
- **情境感知关键词库扩展** (`lib/sampler.py`)：扩展 `detect_context` 智能推断关键词矩阵。
- **AI 辅助开发声明 (AI-Assisted Development Statement)**：明确声明协作开发机制。
- **全量稳定性与压力验证**：完成全部 77 套手写预设模板、8 种风格配方与 100 轮随机抽卡压力测试。

---

## [v1.0.0] - 2026-09-01

### 🚀 核心特性发布
- **项目正式发布**：基于 [ShuaiHui/nsfw-prompt-templates-asian](https://github.com/ShuaiHui/nsfw-prompt-templates-asian) 词库规范构建。
- **16 步装配流水线**、**8 大情境亲和度矩阵**、**裸露等级 × 服装状态咬合**、**7 大冲突消解引擎**、**全中文本地化 UI**。
