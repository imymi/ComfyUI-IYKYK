# 更新日志 (CHANGELOG)

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/) 规范。

---

## [v1.1.0-rc6] - 2026-09-02

> 💡 **审查与架构声明**：  
> 本版本的全面架构审查、数据驱动 `extension_policy`（24/24 扩展逐 ID 100% 可达）、Draft-7 全量 19 运行时 Schema 强门禁、运行时清单与真隔离发布烟测、Span 级受保护语法字节保留及发布自动化测试套件方案均出自 **GPT-5.6 Sol**。

### 🌟 核心改进与技术落实

#### 1. 数据驱动 `extension_policy` 与 24/24 扩展逐 ID 100% 可达 (`data/clothing.json`, `lib/sampler.py`)
- **单一事实来源与 ID 修正**：
  - 在 `clothing.json` 中配置单一事实来源 `extension_policy`，明确划分 L2/L3/L4 的露肤、透度与情趣衣柜启用 tier ID；
  - 修正 7 个 tier ID 拼写差异，使 9 档露肤、5 档透肉、10 类情趣衣柜扩展在自动链路中 **24/24 逐 ID 100% 稳定可达**；
  - 采样器完全转为数据驱动，删除 Python 中硬编码的 ID 集合；
  - 自动化测试实测断言 `hit_tier_ids == set(all_24_tier_ids)`；L1、L5、L6 各自 1000 种子扩展命中数均为 0（零污染）。

#### 2. 标准 Draft-7 递归校验器与全量 19 运行时 Schema 覆盖 (`scripts/validate_data.py`, `schemas/`)
- **真·Draft-7 深度递归校验与强门禁**：
  - 覆盖全部 19 个运行时数据文件的 Schema 定义，包含 `pattern`, `enum`, `minItems`, `maxItems`, `uniqueItems`, `required`, `properties`；
  - 缺失任意一个 Schema 或出现任何校验违规时 `--strict` 立即非零退出；
  - 负向测试覆盖：空 Schema 目录拦截、非法 scene ID pattern 拦截、Rule 10 缺少字段拦截、必需 Rule ID 替换拦截等 14 项变异测试全部通过。

#### 3. 运行时清单与真隔离发布烟测 (`lib/runtime_manifest.py`, `scripts/build_release.py`)
- **零源码泄漏的独立沙箱验证**：
  - 新增 `lib/runtime_manifest.py` 统一定义 `RUNTIME_DATA_FILES`（19 个文件）并打包入库；
  - 烟测采用 `python3 -I` 与解压沙箱目录 `cwd`，断言已加载模块路径严格位于解压沙箱内，项目源码路径 0 泄漏；
  - 支持从 `/private/tmp` 或任何空临时目录独立执行烟测并稳定通过。

#### 4. Rule 9～12 规则目录项与真实 tags 逐项精确匹配 (`data/conflict_rules.json`, `tests/test_catalog_rule_reachability.py`)
- **结构化区分 catalog terms 与 custom aliases**：
  - 规则数据显式区分为 `catalog_terms`（必须 100% 存在于数据文件 `tags` 中）与 `custom_aliases`；
  - 测试直接递归提取数据文件的 `tags` 集合逐项精确断言，杜绝精选白名单跳过问题；
  - 真正从 `DataSampler` 采样进入 `ConflictResolver` 验证端到端消解。

#### 5. Span 级受保护语法字节保留与栈式嵌套校验 (`lib/assembler.py`, `lib/conflict_resolver.py`)
- **受保护结构 100% 字节级不可变**：
  - 建立受保护 span 识别机制，`<lora:model  v2:0.5>`, `"quoted  phrase, x"`, `<lora:spinning room:1.0>`, `"spinning room"`, `escaped\,  comma` 经 `finalize_prompt` 100% 原样保留；
  - 普通文本中的 `spinning room` 正确替换为 `drunken stupor`；
  - 引入栈式括号校验器 `validate_brackets_stack`，严防 `([)]` 等交叉嵌套假绿。

#### 6. 多规则冲突消解引擎全面扩展至 17 大物理与视觉自洽消解规则 (`data/conflict_rules.json`, `lib/conflict_resolver.py`, `tests/test_conflict_engine_matrix.py`)
- **全 15 槽位深度交叉复核与 5 大新增规则落地**：
  - **景别特写 × 下肢足部自洽 (Rule 13 `framing_lower_body_coherence`)**：面部/极致特写时自动剔除高跟鞋、大腿袜、吊袜带、膝靴等下半身足部干扰词条，防止构图注意力割裂与背景畸形肢体；
  - **饰品遮挡 × 视线面部动作自洽 (Rule 14 `accessory_occlusion_gaze_coherence`)**：蒙眼布/遮眼/闭眼状态下自动剔除直视镜头、眨眼等矛盾动作，消除布条上强行画眼睛的视觉伪影；
  - **胶片风格 × 光影色彩互斥 (Rule 15 `monochrome_film_chroma_coherence`)**：黑白/单色胶片下消解彩虹/高饱和 RGB 霓虹色彩，保留明暗反差与影调反差；
  - **服装款式 × 解构状态互斥 (Rule 16 `clothing_style_state_coherence`)**：连体泳衣/死库水禁止解纽扣/掀裙；牛仔裤/长裤禁止裙开衩与裙摆飘动；
  - **多手持道具唯一性消解 (Rule 17 `handheld_props_single_holder`)**：同时出现多个手持道具动作时仅保留首个主手持动作，彻底消除 AI 生成 3 只手以上的多肢体异常。

#### 7. 核心情境亲和度矩阵全面扩展至 14 大情境与全槽位交叉复核 (`lib/sampler.py`, `tests/test_context_affinity_matrix.py`)
- **全量 14 大情境作为一等公民完整覆盖**：
  - 将原仅有 8 类的亲和度矩阵扩展至全部 14 大标准情境：`school` (校园), `office` (职场), `medical` (医疗), `onsen_bath` (温泉/浴室), `bondage_sm` (束缚/调教), `traditional` (和风/传统), `transit` (通勤/电车), `outdoor` (户外/海滩), `dining` (餐饮/咖啡厅), `nightlife` (夜店/酒吧), `domestic` (居家/人妻), `adult` (风俗/私密影棚), `special` (特殊密室/废墟), `generic` (通用/日常)；
  - 彻底消除父级回退，每个情境均享有专属的第一级亲和度映射（涵盖服装、角色、妆容、发型、头饰首饰、道具、纹身、液体效果等全槽位）；
  - 矩阵中所有引用的槽位 ID 经自动化交叉复核验证，与对应数据文件真实 ID **100% 逐项精确存在**（0 无效 ID）；
  - 优化 `detect_context` 关键词匹配与优先级（解决“调教”误触发“教”导致的校园误判）。

#### 8. 发布工程自动化测试门禁与全量 88 项测试矩阵 (`tests/test_release_build.py`, `tests/test_context_affinity_matrix.py`)
- **发布自动化与全量测试套件**：
  - 新增 `test_release_build.py` 自动化测试，验证版本不一致阻断且源码零篡改、双构建 SHA256 绝对一致、临时目录隔离烟测通过；
  - 新增 `test_context_affinity_matrix.py` 覆盖 14 大情境亲和度矩阵及加权采样依从性测试；
  - 全量测试套件增至 **88 项单元测试全部通过（100% Pass，0 失败 0 错误）**。

---

## [v1.1.0-rc5] - 2026-09-02

> 💡 **审查与架构声明**：  
> 本版本的全面架构审查、默认自动联动扩展流水线修复、保护语法与 250 词边界加固、严格 Schema 校验器设计、测试规则直读与构建零源码修改方案均出自 **GPT-5.6 Sol**。

### 🌟 核心改进与技术落实

#### 1. 默认服装自动联动（Auto Link Nudity）扩展流水线统一 (`lib/sampler.py`)
- **彻底消除提前返回缺陷**：
  - 抽取统一的 `_apply_clothing_extensions()` 流水线，消除 `Auto Link Nudity` 在基础 override 处提前 return 的缺陷；
  - 确保默认下拉状态下，L2、L3、L4 均能稳定受控采样 9 档露肤、5 档透度与 10 类情趣衣柜扩展标签；
  - 全部 24 个扩展 tier（包含 `open_sides` 侧缝全开）通过稳定 ID 映射 100% 在自动链路中可达；
  - L1、L5、L6 严格保持 0 命中（1000 种子 0 污染）。

#### 2. 受保护语法全链路保护与 250 词边界原子性截断 (`lib/assembler.py`, `lib/conflict_resolver.py`)
- **字节级原样保留**：
  - 彻底移除 `sanitize_prompt()` 中全局逗号正则 `re.sub(r",(\S)", ...)`，保留 `<lora:name,v2:0.5>`、转义逗号 `tag\,with comma`、双引号及括号；
  - 词数预算以 `PromptFragment` 序列为单位严格计算，结构化片段原子性整块纳入或跳过，**杜绝字符串级切词破坏括号语法闭合**。

#### 3. 严格 Schema 校验器与 19 个运行时数据文件门禁 (`scripts/validate_data.py`, `schemas/`)
- **Draft-7 递归校验与领域规则强门禁**：
  - 补齐/完善全部 19 个运行时文件的 Schema 约束与 Draft-7 递归校验引擎；
  - 纳入 4 个负向变异测试（Rule 9 缺失、服装类别重复、L2 联动清空、扩展 tier 重复）并实现 100% 拦截；
  - 统一 `RUNTIME_DATA_FILES` 单一事实来源，测试专用数据 `scene_context_expectations.json` 移入 `tests/fixtures/`。

#### 4. Rule 9~12 规则可达性直接枚举与真实消解 (`tests/test_catalog_rule_reachability.py`)
- **测试直接消费配置**：
  - 测试直接遍历 `self.rules[id]` 数组，确保配置空或缺失时必定报错；
  - 真实词库目录可达性 100% 覆盖。

#### 5. 构建脚本只读验证与源码零修改 (`scripts/build_release.py`)
- **确定性构建零漂移**：
  - 构建脚本对 `js/version.js` 改为只读校验，版本不一致直接中止构建；
  - 新增独立的开发辅助同步脚本 `scripts/sync_version.py`；
  - 确保构建前后 `git status --porcelain` 绝对一致。

---

## [v1.1.0-rc4] - 2026-09-02

> 💡 **审查与架构声明**：  
> 本版本的全面架构审查、嵌套道具两级采样修复、规则与实际词库全量对齐、服装扩展数据接入、测试正则退格符修复、Schema 负向测试安全隔离与可达性测试套件均出自 **GPT-5.6 Sol**。

### 🌟 核心改进与技术落实

#### 1. 嵌套道具两级安全采样 (`lib/sampler.py`)
- **多条目道具互斥抽取**：
  - 修复 `props.json` 中采用 `items` 嵌套结构的 4 大分类（数码微单、团扇/油纸伞、鲜花花束、毛绒玩偶）采样为空的缺陷；
  - 实现两级随机采样：先根据 RNG 选取单个子项（如团扇与油纸伞二选一），再从中选取 tags，**杜绝同一采样中混拼冲突道具**；
  - 保证 15 个道具分类全部 100% 输出有效非空 tags 且确定性复现。

#### 2. Rule 9~12 规则与真实词库全量对齐 (`data/conflict_rules.json`, `lib/conflict_resolver.py`)
- **真实词库单点事实来源**：
  - 剔除合成短语假阳性，将 Rule 9~12 的触发词与禁用词逐项与 `poses.json`、`props.json`、`expressions.json`、`lighting.json`、`makeup.json`、`scenes.json` 真实 tags 精准对齐；
  - 移除 Python 中硬编码的 `busy_patterns`，直接以 JSON 规则配置作为单一事实来源；
  - 真实消解：双手忙姿态（背手、四肢着地、抓床单等）与真实手持道具（手机录像、手柄、微单、团扇等）互斥；害羞与掠夺挑逗眼神互斥；日光与夜景互斥；裸肌与蹭花唇膏/晕妆互斥。

#### 3. 服装扩展数据（9档露肤/5档透肉/10类衣柜）接入运行链路 (`lib/sampler.py`)
- **扩展梯度可达性打通**：
  - 新增 `list_sfw_exposure_tiers`、`sample_sfw_exposure`；
  - 新增 `list_cloth_transparency_tiers`、`sample_cloth_transparency`；
  - 新增 `list_lingerie_wardrobe`、`sample_lingerie_wardrobe`；
  - 新增 `test_props_and_extensions_reachability.py` 验证 100% 可达与确定性。

#### 4. 测试套件加固与正则退格符修复 (`tests/test_nudity_levels.py`)
- **测试真实有效性恢复**：
  - 将 34 处包含退格符 `\x08` 的正则字符串全面修复为真正的 raw regex `r"\b...\b"`；
  - 新增 `test_regex_fails_on_injected_violation` 自检：断言人为注入违规词时正则必定捕获报错；
  - 新增 `test_no_c0_control_characters_in_source`：扫描源文件中是否存在非法 C0 控制字符。

#### 5. JSON Schema 负向测试安全临时隔离 (`tests/test_schema_negatives.py`)
- **真实数据零污染防护**：
  - 负向拦截测试改用 `tempfile.TemporaryDirectory` 复制临时目录进行变异测试，绝不修改真实 `data/scenes.json`；
  - 每次测试运行前后断言真实数据文件 SHA256 绝对不变。

#### 6. 前端 UI 默认值隔离与一键还原修复 (`js/iykyk_ui.js`)
- **节点默认值隔离**：
  - 将 3 个节点的默认值按 ComfyUI 节点类独立隔离存储（`NODE_DEFAULTS`），彻底修复点击「恢复默认选项」导致下拉框变空白的 Bug；
  - 增加下拉选项合法性校验与安全回退保障。

---

## [v1.1.0-rc3] - 2026-09-01

> 💡 **审查与架构声明**：  
> 本版本的全面架构审查、数据质量复评、JSON Schema 体系设计、全量自动化测试矩阵及全部技术修改方案均出自 **GPT-5.6 Sol**。

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
