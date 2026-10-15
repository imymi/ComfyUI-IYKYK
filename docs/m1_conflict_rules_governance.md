# ComfyUI-IYKYK 17 条冲突规则逐条件分级治理规约 (M1-D4)

- **当前规则源文件**：[`data/conflict_rules.json`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/data/conflict_rules.json)
- **冲突消解执行引擎**：[`lib/conflict_resolver.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/lib/conflict_resolver.py#L1784)
- **治理原则**：
  1. **分类细化至具体触发条件**：同一条规则内部严格拆分子条件，细分为【硬约束】、【软偏好】与【增强处理】，禁止全规则粗暴贴单标签。
  2. **外部输入优先级铁律**：外部覆盖输入（端口传入文本）享有最高优先权；冲突消解优先淘汰或退让系统自动生成内容；若两项外部输入存在不可消解的物理硬冲突，显式抛错提示，禁止静默吞没。
  3. **手部容量模型重构**：由粗暴的“只要有手部占用或多道具就一律删除”改为严格的 `2-hand capacity model`（可用手数 vs 道具双手/单手需求）。
  4. **幂等性与可追溯性**：单次消解与二次消解结果严格幂等，所有变更在诊断报告记录完整的决策原因与来源追溯。

---

## 一、三层规则行为范式定义

| 规则层级 | 语义本质与判定依据 | 消解动作与外部优先准则 | 适用典型场景 |
|---|---|---|---|
| **【硬约束】<br>(Hard Constraint)** | 明确的物理不可能性、绝对生理互斥、空间场所分裂、服装载体物理结构不匹配。 | **强制消解**：若两项系统内容互斥，按依赖排序或胜负表消解败者；若外部与系统互斥，**强制保留外部、淘汰系统**；若多项外部内容互斥，**明确抛错**。 | 全裸与内衣并存、视线几何不可达、双手被占仍持物、无纽扣衣物解纽扣。 |
| **【软偏好】<br>(Soft Preference)** | 审美倾向、情绪搭配建议、画质等级契合度、常见视觉惯例。 | **排序/调优，非强制删除**：用于自动采样排序与平滑过滤；**绝对禁止删除用户明确指定的外部覆盖文本**。外部与系统冲突时，直接抑制系统软偏好。 | 害羞情绪与直视镜头（属于合理特例）、手机画质词与精致妆容搭配倾向。 |
| **【增强处理】<br>(Enhancement)** | 物理渲染真实感补充、皮肤融合、材质边缘平滑。 | **只增不改**：仅用于向系统生成内容追加修饰词；**严禁改写、替换或破坏外部覆盖的原始内容**。 | 纹身真皮层融合标签注入、材质物理状态补充。 |

---

## 二、17 条冲突规则逐条件分级治理总表

### 规则 1：`nudity_clothing_conflicts` (裸露与内衣状态互斥)
- **条件 1.1【硬约束】私处/全胸暴露与覆盖内衣物理互斥**
  - **触发条件**：检测到 `pussy visible`, `exposed breasts`, `bare body` 等完全裸露标记，同时存在 `panties showing`, `wearing bra`。
  - **允许动作**：若为系统生成，强制剥离/删除内衣词；若外部覆盖为全裸，系统内衣自动清除；若外部同时输入全裸与内裤，显式报错。
  - **反例与合法例外**：半脱、移位状态（如 `panties pulled aside`, `bra pushed up`）为合法物理状态，**不得触发互斥删除**。
- **条件 1.2【硬约束】微透视状态下的下装存在性**
  - **允许动作**：透视衣物与内部可见内衣属于合法图层组合，禁止触发裸露全清。

---

### 规则 2：`material_penetration` (材质穿透伪影)
- **条件 2.1【增强处理/系统规范】系统提示词禁用纯透明词引发伪影**
  - **触发条件**：系统生成池抽中 `sheer`, `transparent`, `see-through`。
  - **允许动作**：替换为具备物理厚度与光影折射的具象表达（如 `translucent silk fabric with rim light`）。
  - **外部优先准则**：若外部覆盖端口明确传入 `sheer dress`，**原样保留，禁止替换改写**。

---

### 规则 3：`gaze_angle_geometry` (视线与镜头角度几何匹配)
- **条件 3.1【硬约束】背面视角禁止与镜头正面对视**
  - **触发条件**：镜头角度为 `from behind`, `back view`，且人物动作非回头，同时出现 `looking at viewer`, `eye contact`。
  - **允许动作**：消解对视词，自动回退为 `looking away` 或顺应背向视线。若外部指定背影且指定对视，检查是否有 `looking back over shoulder`，若无则报错。
  - **合法反例**：`looking back over shoulder`, `turning head back`（回头看）为合法几何动作，**严禁误删**。

---

### 规则 4：`gaze_mutual_exclusion` (视线方向唯一性)
- **条件 4.1【硬约束】单人物视线多向互斥**
  - **触发条件**：同一人物同时出现 `looking at viewer` 与 `looking away / averted gaze`。
  - **允许动作**：系统内容保留主视线，删除冲突辅视线；外部指定明确视线时优先覆盖系统视线。

---

### 规则 5：`liquid_restrictions` (液体微量法则与渲染安全)
- **条件 5.1【软偏好】微量液体飞溅与全身大面积浸润倾向**
  - **触发条件**：非浴室/雨水场景下出现微量汗珠或泪滴。
  - **允许动作**：限制液体修饰词扩散，避免全图溢出。外部指定时不予干涉。

---

### 规则 6：`device_quality_compatibility` (设备与画质等级兼容性)
- **条件 6.1【软偏好】Lo-Fi 设备（手机/监控）与 8K 商业写实词**
  - **触发条件**：设备标记为 `smartphone selfie`, `cctv snapshot`，系统同时混入 `8k master work`, `hasselblad medium format`。
  - **允许动作**：自动采样时抑制影棚极致画质词；**若用户外部指定手机自拍并指定 8K，原样保留外部，仅作软提示**。

---

### 规则 7：`tattoo_dermal_fusion` (纹身真皮层皮肤融合)
- **条件 7.1【增强处理】纹身图层与皮肤融合词注入**
  - **触发条件**：检测到纹身选择器生效，且属于系统生成流水线。
  - **允许动作**：补充 6 词真皮层融合修饰词（`ink beneath skin, epidermal texture...`）。
  - **外部优先准则**：外部覆盖自定义纹身文本时，仅在外部文本未包含质感时追加，不破坏外部指定图样。

---

### 规则 8：`spatial_environmental_mutual_exclusion` (空间与环境自洽互斥)
- **条件 8.1【硬约束】完全封闭室内与野外露天环境并存**
  - **触发条件**：同时出现 `indoor bedroom / enclosed classroom` 与 `dense forest / snowy mountain / outdoor meadow`。
  - **允许动作**：保留主情境空间，剔除次生冲突环境词。
- **条件 8.2【合法反例豁免】室内窗光与开阔窗景**
  - **合法反例**：`indoor classroom with open window showing cherry blossoms`（室内带窗看室外）、`semi-open onsen pavilion`（半露天温泉亭）。**严禁将窗外自然景色判定为室内外互斥而粗暴删除！**

---

### 规则 9：`pose_hand_occupation` (姿势手部占用与手持道具互斥 —— 【核心重构】)
- **现有缺陷**：原规则只要检测到姿势含有手部动作（哪怕单手托腮、单手插兜），一律粗暴将手持物（如单反相机、扇子）删除。
- **重构方案：双手容量模型 (2-Hand Capacity Model)**：
  - 人体可用手部容量基准：`total_hands = 2`。
  - **动作占用统计**：
    - 双手占用 (`hands_used = 2`)：`hands behind back`, `tying hair with both hands`, `crossed arms`, `hands on hips (both)`。
    - 单手占用 (`hands_used = 1`)：`hand on cheek`, `waving one hand`, `hand in pocket`, `adjusting glasses with one hand`。
    - 空闲 (`hands_used = 0`)：常规站姿、坐姿无手部特定约束。
  - **道具需求统计**：
    - 单手道具 (`requires = 1`)：`holding smartphone`, `holding wine glass`, `holding fan`, `holding compact camera`。
    - 双手道具 (`requires = 2`)：`holding long rifle with both hands`, `holding large katana with two hands`, `carrying heavy tray`。
- **条件 9.1【硬约束】总需求容量超限消解**
  - **触发条件**：`hands_used + props_required > 2`。
  - **消解动作**：
    - 若双手被占（`hands_used = 2`），且存在手持道具，则道具无法手持，消除道具手持动作或退回挂饰；
    - 若外部覆盖传入道具，优先保留外部道具，将系统生成的双手动作降级为单手或自适应动作。
- **条件 9.2【合法反例支持】单手占用 + 单手持物合法共存**
  - **反例场景**：**`hand on cheek (单手占用 1)` + `holding camera (单手持物 1)` -> 容量为 2，完全合法！**
  - **判定要求**：新引擎必须判定通过，**严禁再将相机从提示词中删除！**

---

### 规则 10：`emotion_gaze_affinity` (情绪与眼神方向一致性)
- **条件 10.1【软偏好/非必然冲突】害羞、高冷与眼神搭配**
  - **现有缺陷**：原规则将“害羞与直视”一律判定为必然冲突并强删眼神。
  - **治理修正**：降级为软偏好；现实中存在“害羞地直视（勇敢对视）”或“冷漠地瞥向镜头”，不再作为硬约束互斥；仅在系统自动生成阶段进行概率微调，**绝不删除用户明确输入的视线词**。

---

### 规则 11：`environmental_lighting_coherence` (光照与环境一致性 —— 【核心重构】)
- **条件 11.1【硬约束】正午烈日与深邃午夜星空互斥**
  - **触发条件**：`midday bright sunlight` 与 `midnight starry sky / pitch black night` 物理互斥。
  - **允许动作**：消解冲突时间/光源。
- **条件 11.2【合法反例豁免】室内窗光、人工混合光源与阴雨天光**
  - **合法反例**：
    1. `indoor dimly lit room with bright sunlight through window slits`（暗室百叶窗日光条纹）；
    2. `night street with bright neon + tungsten shop light`（夜间多光源混合）；
    3. `rainy overcast with soft rim light`。
  - **判定要求**：此类有特定物理光源依托的搭配全部判定为合法，严禁单凭包含“night”或“sunlight”词根就一刀切误删。

---

### 规则 12：`makeup_details_coherence` (妆容与细节自洽)
- **条件 12.1【硬约束】完全素颜与浓重糊妆互斥**
  - **触发条件**：`bare face / no makeup` 与 `heavy smudged mascara / red lipstick`。
  - **允许动作**：若外部指定素颜，清除系统浓妆；若外部指定浓妆，清除系统素颜。

---

### 规则 13：`framing_lower_body_coherence` (景别特写与下肢/足部元素自洽)
- **条件 13.1【硬约束】脸部极特写与全身鞋袜动作互斥**
  - **触发条件**：景别为 `extreme close-up`, `face macro`，同时出现 `high heels`, `barefoot on floor`。
  - **允许动作**：在极特写下剔除画面绝对不可见区域的足部动作，防止扩散模型渲染多肢体畸变。
  - **合法反例**：中景 (medium shot) 或全身特写属于合法可视范围，不予剔除。

---

### 规则 14：`accessory_occlusion_gaze_coherence` (饰品遮挡与视线自洽)
- **条件 14.1【硬约束】完全遮光眼罩与对视动作互斥**
  - **触发条件**：配饰包含 `opaque blindfold`, `eyes covered completely`，同时出现 `staring into camera`。
  - **消解动作**：抑制对视动作，修正为盲视或感知动作。

---

### 规则 15：`monochrome_film_chroma_coherence` (黑白胶片与高饱和色彩互斥)
- **条件 15.1【硬约束】黑白单色胶卷与高饱和彩虹色彩互斥**
  - **触发条件**：胶片为 `kodak tri-x 400 (monochrome)`, `black and white film`，同时存在 `vibrant neon colors`, `saturated rainbow palette`。
  - **消解动作**：消解色彩饱和度描述，转化为对比度与明暗影调（`high contrast, rich grayscale`）。

---

### 规则 16：`clothing_style_state_coherence` (服装款式与解构状态互斥 —— 【载体绑定】)
- **条件 16.1【硬约束】服装部件物理载体缺失互斥**
  - **触发条件**：服装为长裤/紧身短裤（无裙摆载体），出现 `skirt lift / billowing skirt`；或服装为连体泳衣/拉链皮衣（无纽扣载体），出现 `unbuttoning shirt`。
  - **消解动作**：清除无承载物的状态片段。
- **条件 16.2【覆盖后载体重新校验】**
  - **重构准则**：当外部覆盖端口传入新的服装款式（例如将白衬衫覆盖为连体泳衣）后，**必须立即清空旧服装的物理承载物事实，并对下游状态片段重新执行载体存在性验证**，彻底杜绝白衬衫解纽扣动作残留到连体泳衣上的语义污染。

---

### 规则 17：`handheld_props_single_holder` (多手持道具消解 —— 【手部容量整合】)
- **现有缺陷**：原规则无论是否有两只手，只要出现 2 个手持道具就一律干掉第 2 个。
- **重构方案**：
  - **条件 17.1【合法共存】单手道具 + 单手道具（共 2 件）**：
    - 典型合法场景：`holding smartphone in left hand` + `holding coffee cup in right hand`（左手拿手机、右手拿咖啡）。
    - **判定要求**：只要 `total_required <= 2` 且没有双手姿势占用，**完全允许双持道具，取消强制一刀切！**
  - **条件 17.2【硬约束】超过 2 件手持道具或单手持两件道具**：
    - 当道具数 > 2，或特定手部超载时，保留最高优先级主道具，消解超限道具。外部覆盖内容绝对优先保留。

---

## 三、治理交付检查清单 (M1-D4 Verification Sign-off)

- [x] 全部 17 条规则均逐条列出其稳定 ID。
- [x] 全部规则均细化到具体触发条件，明确标注【硬约束】、【软偏好】或【增强处理】。
- [x] 手部容量模型（Rule 9 & Rule 17）完成 2-hand 数学模型重构，支持单手占用+持物、双道具共存反例。
- [x] 场景与光影（Rule 8 & Rule 11）补全室内窗光、混合光源等合法物理反例。
- [x] 服装款式与状态（Rule 16）明确覆盖后清空旧载体事实并重新校验链路。
- [x] 统一规范外部优先原则：外部 > 预设/系统，外部硬冲突阻断报错。
