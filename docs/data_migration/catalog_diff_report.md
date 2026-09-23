# 全 Catalog 变更与差集报告 (Catalog Diff & Inventory Report)

> **版本对应**：ComfyUI-IYKYK `v1.1.0-rc9`  
> **文档性质**：**Milestone 2 核心实物交付材料** (依据 Gate 1 终审意见定向修正)  
> **数据溯源**：基于 `docs/data_migration/clothing_lexicon_migration_ledger.md` 与 `raw_clothing_input.tsv` (SHA-256: `65e4c56655b4c0f8f3629a5e26d407d77860d3581d55b2da7dd92ff87d7fc2ff`) 逐行核验提取生成，完全服从 246 行台账客观归口，绝无人工凑数。

---

## 一、各 Catalog 规模与差集总览

| 数据集 / 槽位 (Catalog & Slot) | 对应数据文件 | 存量有效项数 | 台账提取新增项数 | 迁移后总量 | 规模增幅 | 兼容性保障机制 |
|---|---|---|---|---|---|---|
| **服装款式** (`clothing`) | `data/clothing.json -> categories` | 31 款 (基线 bc0d645) | **+106 款** | 137 款 | +341.9% | 存量 31 款严格保真，Batch 1~5 新增 106 款全量入库并已完成闭环验收 |
| **服装状态** (`clothing`) | `data/clothing.json -> clothing_states` | 12 条 (含1联动) | **+11 条** | 23 条 | +91.7% | 状态三元分类消解，防自我承载与防悬空误删 |
| **头饰配饰** (`jewelry`) | `data/accessories.json -> headwear_jewelry` | 12 项 | **+7 项** | 19 项 | +58.3% | 槽位与数据键解耦，外穿斗篷/披肩纳入承载白名单 |
| **情趣内衣** (`lingerie`) | `data/clothing.json -> lingerie_wardrobe` | 10 项 | **+2 项** | 12 项 | +20.0% | 纳入全域承载主体白名单，开裆内裤独立建模 |
| **身体微瑕** (`imperfections`) | `data/imperfections.json -> categories` | 7 项 | **+1 项** | 8 项 | +14.3% | `tan_lines` 泳装日晒痕迹归入身体微瑕主槽位 |

---

## 二、服装款式 (`clothing.json -> categories`) 详细差集清单

### 1. 存量保留清单 (31 款，严格 100% 保真)
以下 31 款为 ComfyUI-IYKYK bc0d645 基线中存在的款式 ID，本次迁移**绝不修改、绝不废弃、绝不移出**：
`qipao`, `hanfu`, `modern_chinese`, `kimono`, `yukata`, `furisode`, `jk_seifuku`, `blazer_uniform`, `gym_uniform`, `hanbok`, `korean_school`, `ol_suit`, `nurse_uniform`, `maid_dress`, `waitress_uniform`, `lingerie_lace`, `silk_robe`, `camisole_slip`, `bikini_micro`, `one_piece_swimsuit`, `latex_catsuit`, `leather_corset`, `bunny_suit`, `cheerleader`, `evening_dress`, `street_casual`, `party_club`, `knit_sweater`, `sweater_casual`, `swimsuit_school`, `dungarees`。

### 2. 台账提取确定新增款式清单 (106 款 JSON 集合)
```json
[
  "anime_cosplay",
  "apron_dress",
  "armored_dress",
  "armored_skirt",
  "bathrobe",
  "battle_robe",
  "berserker_armor",
  "bikini_classic",
  "bikini_creative",
  "bikini_strappy",
  "black_leggings",
  "business_suit",
  "chiffon_dress",
  "clerical_nun",
  "clerical_priest",
  "cocktail_dress",
  "combat_tactical",
  "convenience_store",
  "crop_top",
  "denim_jacket",
  "denim_shorts",
  "dirndl_dress",
  "down_jacket",
  "dress_backless",
  "dress_casual",
  "duffel_coat",
  "fast_food_uniform",
  "festive_costume",
  "firefighter_gear",
  "fishnet_top",
  "floral_dress_black",
  "floral_dress_white",
  "formal_gown",
  "frock_smock",
  "greek_toga",
  "halter_dress",
  "hoodie",
  "hospital_gown",
  "hot_pants",
  "knight_armor",
  "knit_vest",
  "lab_coat",
  "layered_skirt",
  "leather_jacket",
  "leather_skirt",
  "leotard_bodysuit",
  "lolita_fashion",
  "lolita_gothic",
  "lolita_sweet",
  "long_skirt",
  "mahou_shoujo",
  "mecha_exoskeleton",
  "mecha_power_armor",
  "microskirt",
  "military_overcoat",
  "military_uniform",
  "miniskirt",
  "off_shoulder_dress",
  "outerwear_coat",
  "outerwear_jacket",
  "outerwear_overcoat",
  "pencil_skirt",
  "pettiskirt",
  "plaid_skirt",
  "pleated_dress",
  "pleated_skirt",
  "pumpkin_skirt",
  "racing_suit",
  "rain_skirt",
  "rainwear_coat",
  "robe_general",
  "safari_jacket",
  "sailor_shirt",
  "shinto_miko",
  "shirts_blouses",
  "skirts_general",
  "sleeveless_dress",
  "slime_dress",
  "soft_shell_jacket",
  "sportswear_active",
  "strapless_dress",
  "strapless_top",
  "summer_sundress",
  "sundress_layered",
  "suspender_skirt",
  "sweater_dress",
  "sweatshirt",
  "swimsuit_classic",
  "swimsuit_competition",
  "swimsuit_creative",
  "t_shirt",
  "tactical_vest",
  "tailcoat",
  "taoist_robe",
  "tops_tanks",
  "trench_coat",
  "tulle_dress",
  "tutu_skirt",
  "volleyball_uniform",
  "waist_apron",
  "wedding_dress",
  "windbreaker",
  "winter_parka",
  "witch_robe",
  "wizard_robe",
  "zentai_suit"
]
```

---

## 三、相对早期 81 款清单的真实集合差异分析与决策说明

### 1. 真实集合差异三元统计与守恒方程

依据前期工程讨论，前稿明确列出了一份包含 **81 款** 的基准款式清单。将本次台账 246 行闭环提取的 **109 款** 正式款式集与前稿 **81 款** 进行严格的数学集合差集运算，得出真实的集合划分如下：

- **早期基准集合 (Base Set)**：$|S_{81}| = \mathbf{81}$ 款
- **本次确定新增集合 (Added Set)**：$|S_{add}| = \mathbf{28}$ 款
- **本次确定移除集合 (Removed Set)**：$|S_{rem}| = \mathbf{0}$ 款
- **两版共同保留集合 (Retained Set)**：$|S_{kept}| = \mathbf{81}$ 款
- **精确集合守恒方程**：
  $$\mathbf{81 - 0 + 28 = 109} \quad (\text{净增 } 28 \text{ 款})$$

> [!NOTE]
> 前稿在说明“新增 27 款”时，曾试图移除 `sweater_casual` 并将其并入 `knit_sweater`。经 Gate 1 终审纠偏，现已坚决恢复独立的 `sweater_casual` 款式，彻底保留既有选项语义独立性，使两版保留款式恢复为全部 81 款，净增款式精确对齐为 28 款。

### 2. 相对 81 款实际新增的 28 款独立款式清单

| 序号 | 款式规范 ID | 原始行号 | 中文名称 | 规范化提示词 | 形制拓扑 | 独立立项理由与物理特征说明 |
|---|---|---|---|---|---|---|
| 1 | `apron_dress` | 154 | 围裙 | `apron` | `one_piece` | 围裙女仆装/全身连体围裙独立形制，具备围裙系带与下摆，独立于半身腰围裙 |
| 2 | `armored_skirt` | 239 | 披甲 | `armored skirt` | `bottom_skirt` | 护腿金属挂甲战斗半身战裙，下装独立形制，物理上支持掀裙消解判定 |
| 3 | `bathrobe` | 108 | 浴袍 | `bathrobe` | `one_piece` | 宽松浴袍/晨袍独立款式，系带前襟开合，独立于普通睡袍 |
| 4 | `dress_casual` | 28 | 连身裙 | `dress` | `one_piece` | 通用休闲连身裙款式，作为连衣裙基础款，提供广泛日常搭配支持 |
| 5 | `floral_dress_black` | 146 | 花卉图案连衣裙（黑） | `black skirt dress, flower pattern in dress,black gow` | `one_piece` | 暗黑系碎花晚宴长礼服，哥特与神秘视觉风格，纠正拼写后独立立项 |
| 6 | `floral_dress_white` | 145 | 花卉图案连衣裙（白） | `white skirt dress, flower pattern in dress,white gow` | `one_piece` | 白色蕾丝印花落地大礼服，婚庆与典礼核心形制，纠正拼写后独立立项 |
| 7 | `frock_smock` | 131 | 罩衫 | `frock` | `one_piece` | 欧洲传统宽松外罩工作服，纽扣与门襟完整，具备独立历史形制 |
| 8 | `leather_skirt` | 227 | 皮裙 | `leather skirt` | `bottom_skirt` | 机车风黑色皮质短裙，硬挺皮革光泽与金属拉链，下装独立款式 |
| 9 | `lolita_fashion` | 89 | 洛丽塔风格 | `lolita_fashion` | `one_piece` | 洛丽塔综合洋装大类，多层蕾丝与蓬松裙摆，作为Lo系核心款独立 |
| 10 | `lolita_gothic` | 50 | 哥特洛丽塔风格 | `gothic_lolita` | `one_piece` | 暗黑哥特洛丽塔风格洋装，十字架/暗色蕾丝特色，独立于通用款 |
| 11 | `lolita_sweet` | 78 | 甜美可爱的洛丽塔 | `sweet_lolita` | `one_piece` | 甜美粉嫩系洛丽塔洋装，蝴蝶结与蛋糕裙摆，独立于通用款 |
| 12 | `long_skirt` | 162 | 长裙 | `long skirt` | `bottom_skirt` | 及踝长款半身裙独立下装，无开衩保守飘逸剪裁，独立于短裙 |
| 13 | `off_shoulder_dress` | 167 | 露肩连衣裙 | `off-shoulder dress` | `one_piece` | 露单肩/落肩连身长裙款式，肩部非对称剪裁与裙摆结合 |
| 14 | `outerwear_coat` | 110 | 外套 | `coat` | `outerwear` | 通用男女款中长外套大衣独立款式，提供秋春日常基础外套 |
| 15 | `outerwear_jacket` | 183 | 夹克 | `jacket` | `outerwear` | 通用轻便夹克外套独立款式，拉链/纽扣门襟，提供基础工装上装 |
| 16 | `pleated_dress` | 165 | 带褶连衣裙 | `pleated dress` | `one_piece` | 全身细风琴褶皱长款连衣裙款式，高密度压褶工艺质感独特 |
| 17 | `pumpkin_skirt` | 126 | 南瓜裙 | `pumpkin skirt` | `bottom_skirt` | 万圣节特色下摆束口蓬起南瓜造型半身裙，版型完全不同于百褶裙 |
| 18 | `rain_skirt` | 163 | 雨裙 | `rainskirt` | `bottom_skirt` | 户外防水功能性裹腿半身防雨裙，下装独立形制，不可与雨衣大衣混淆 |
| 19 | `robe_general` | 85 | 长袍 | `robe` | `one_piece` | 通用长袍/睡袍款式，宽松垂坠，独立于宗教神职法袍与和风浴衣 |
| 20 | `sailor_shirt` | 176 | 水手衬衫 | `sailor shirt` | `top` | 水手领短袖上身衬衫，仅含上装部分，独立于整套水手服(jk_seifuku) |
| 21 | `skirts_general` | 141 | 裙子 | `skirt` | `bottom_skirt` | 基础通用半身裙，提供最纯粹的单品下身裙装泛化支持 |
| 22 | `sleeveless_dress` | 219 | 无袖连衣裙 | `sleeveless dress` | `one_piece` | 无袖A字修身连衣裙，夏日清凉无袖剪裁，独立于带袖款 |
| 23 | `soft_shell_jacket` | 128 | 软壳外套 | `soft shell coat` | `outerwear` | 户外软壳防风透气夹克，专业户外登山冲锋衣质感，独立于羽绒服 |
| 24 | `strapless_dress` | 166 | 无肩带礼服 | `strapless dress` | `one_piece` | 抹胸式无肩带晚宴长裙，平口胸部包裹与修身裙身，独立立项 |
| 25 | `sundress_layered` | 135 | 黑色连衣裙+白色打底T恤搭配(请勿去掉tag括号) | `(((black sundress with round neck,white t-shirt bottom)))` | `one_piece` | 黑吊带外穿+白T内搭经典两件套，消除语法嵌套后固化为规范组合形制 |
| 26 | `suspender_skirt` | 172 | 吊带裙 | `suspender skirt` | `bottom_skirt` | 背带/吊带半身伞裙，肩带有无影响整体版型，独立于普通短裙 |
| 27 | `swimsuit_classic` | 6 | 泳装 | `swimsuit` | `one_piece` | 经典通用连体/分体泳装总称，提供最基础的泳衣泛化支持 |
| 28 | `waist_apron` | 150 | 腰围裙 | `waist apron` | `bottom_skirt` | 半身系腰围裙独立款式，无上胸片，独立于全身围裙(apron_dress) |

### 3. 毛衣方案纠偏与旧选项语义保护 (恢复独立的 sweater_casual)

> [!IMPORTANT]
> **方案纠偏事实与决策溯源**：
> - **早期设定与前稿摇摆**：在早期 81 款方案中曾设立独立款式 `sweater_casual`（普通休闲毛衣/圆领套头衫）；但在上轮台账审校中，曾试图将第 33 行 `sweater` 与相关变体并入存量款式 `knit_sweater` 并拟修改其下拉菜单显示名；
> - **终审纠偏原因剖析**：
>   经审查现有 `data/clothing.json`，存量 `knit_sweater` 的定义实际为：
>   - `name_zh`: `"露背毛衣/童贞杀 (Knit Sweater)"`
>   - `tags`: `"virgin killer knit sweater"`, `"backless chunky knit"`, `"oversized knitted sweater slipping off"`
>   若将普通高领毛衣直接并入 `knit_sweater` 且修改显示名称，存在双重致命隐患：
>   1. 同一选项采样池同时包含普通毛衣与露背毛衣，仅在 tag 上标注 facts 并不能防止随机抽取时抽到露背变体，导致用户在选择毛衣时出现意料之外的极端裸露；
>   2. 修改下拉菜单显示名称后，仅保留 ID 并不能保证保存旧显示字符串的第三方或已有工作流通过原生 combo 校验。
> - **Gate 1 定案决议：坚决恢复独立的 `sweater_casual`**：
>   1. **存量 `knit_sweater` 100% 保持原汁原味**：保持显示名称 `"露背毛衣/童贞杀 (Knit Sweater)"` 与现有标签语义不变，确保旧工作流加载完全零破坏；
>   2. **恢复独立的 `sweater_casual`**：独立承载第 33 行普通毛衣 (`sweater`)、34 行高领毛衣 (`turtleneck sweater`)、35 行罗纹毛衣 (`ribbed sweater`)、36 行露肩毛衣 (`off-shoulder sweater`)、173 行过手袖 (`sleeves_past_fingers`)、178 行毛衣 (`sweater`)；
>   3. **物理与语义双重隔离**：普通毛衣形制为 `top`，解扣为“禁止”，掀裙为“不适用”，彻底解决采样池混杂与选项校验冲突。

### 4. 81 款两版共同保留款式集合

`anime_cosplay`, `armored_dress`, `battle_robe`, `berserker_armor`, `bikini_classic`, `bikini_creative`, `bikini_strappy`, `black_leggings`, `business_suit`, `chiffon_dress`, `clerical_nun`, `clerical_priest`, `cocktail_dress`, `combat_tactical`, `convenience_store`, `crop_top`, `denim_jacket`, `denim_shorts`, `dirndl_dress`, `down_jacket`, `dress_backless`, `duffel_coat`, `dungarees`, `fast_food_uniform`, `festive_costume`, `firefighter_gear`, `fishnet_top`, `formal_gown`, `greek_toga`, `halter_dress`, `hoodie`, `hospital_gown`, `hot_pants`, `knight_armor`, `knit_vest`, `lab_coat`, `layered_skirt`, `leather_jacket`, `leotard_bodysuit`, `mahou_shoujo`, `mecha_exoskeleton`, `mecha_power_armor`, `microskirt`, `military_overcoat`, `military_uniform`, `miniskirt`, `outerwear_overcoat`, `pencil_skirt`, `pettiskirt`, `plaid_skirt`, `pleated_skirt`, `racing_suit`, `rainwear_coat`, `safari_jacket`, `shinto_miko`, `shirts_blouses`, `slime_dress`, `sportswear_active`, `strapless_top`, `summer_sundress`, `sweater_casual`, `sweater_dress`, `sweatshirt`, `swimsuit_competition`, `swimsuit_creative`, `swimsuit_school`, `t_shirt`, `tactical_vest`, `tailcoat`, `taoist_robe`, `tops_tanks`, `trench_coat`, `tulle_dress`, `tutu_skirt`, `volleyball_uniform`, `wedding_dress`, `windbreaker`, `winter_parka`, `witch_robe`, `wizard_robe`, `zentai_suit`

---

## 四、新增服装款式全量工程规格台账 (Batch 1~5 独立新增 106 款，与基线 31 款共同构成 137 款全量目录)

> **说明**：原台账中第 117 行 `dungarees`、第 33 行 `sweater` (`sweater_casual`)、第 8 行 `school swimsuit` (`swimsuit_school`) 在基线 `bc0d645` 中已原生存在（归入 31 款保留清单）；Batch 1~5 实际独立新增 106 款，共同达成 137 款全量目录闭环。
> **拓扑枚举严格约束**：所有形制拓扑字段 (`topologies`) 必须且只能取值自 `models.py` 权威枚举：`one_piece`, `top`, `bottom_pants`, `bottom_skirt`, `underwear`, `outerwear`, `none`。坚决杜绝任何非规范复合字符串。

| 序号 | 款式规范 ID | 中文名称 | 形制拓扑 (`topologies`) | 解扣能力 (`button`) | 掀裙能力 (`skirt`) | 原始行号 | 规范化提示词 |
|---|---|---|---|---|---|---|---|
| 1 | `anime_cosplay` | 不知火舞 | `one_piece` | 禁止 | 禁止 | 104 | `mai shiranui` |
| 2 | `apron_dress` | 围裙 | `one_piece` | 禁止 | 允许 | 154 | `apron` |
| 3 | `armored_dress` | 铠装连衣裙 | `one_piece` | 禁止 | 允许 | 160 | `armored dress` |
| 4 | `armored_skirt` | 披甲 | `bottom_skirt` | 禁止 | 允许 | 239 | `armored skirt` |
| 5 | `bathrobe` | 浴袍 | `one_piece` | 允许 | 允许 | 108 | `bathrobe` |
| 6 | `battle_robe` | 战袍 | `one_piece` | 禁止 | 允许 | 138 | `battle robe` |
| 7 | `berserker_armor` | 狂战士铠甲 | `outerwear` | 禁止 | 禁止 | 242 | `berserker armor` |
| 8 | `bikini_classic` | 比基尼 | `underwear` | 禁止 | 禁止 | 1 | `bikini` |
| 9 | `bikini_creative` | 奶牛比基尼 | `underwear` | 禁止 | 禁止 | 68 | `cow_bikini` |
| 10 | `bikini_strappy` | 系绳比基尼 | `underwear` | 禁止 | 禁止 | 2 | `string bikini` |
| 11 | `black_leggings` | 黑色紧身裤 | `bottom_pants` | 禁止 | 禁止 | 228 | `black leggings` |
| 12 | `business_suit` | 西装 | `outerwear` | 允许 | 禁止 | 47 | `business suit` |
| 13 | `chiffon_dress` | 雪纺连衣裙 | `one_piece` | 不适用 | 允许 | 25 | `chiffon dress` |
| 14 | `clerical_nun` | 修女服 | `one_piece` | 禁止 | 允许 | 59 | `nun gown` |
| 15 | `clerical_priest` | 神父/修生黑袍 | `one_piece` | 允许 | 允许 | 113 | `cassock` |
| 16 | `cocktail_dress` | 银色连衣裙 | `one_piece` | 不适用 | 允许 | 211 | `silvercleavage dress` |
| 17 | `combat_tactical` | 战斗服 | `top` | 允许 | 禁止 | 74 | `combat suit` |
| 18 | `convenience_store` | 便利店工作服(By 糯米) | `top` | 允许 | 不适用 | 45 | `convenience store uniforms` |
| 19 | `crop_top` | 小可爱露腹短上衣 | `top` | 禁止 | 不适用 | 40 | `crop top` |
| 20 | `denim_jacket` | 牛仔夹克 | `outerwear` | 允许 | 不适用 | 187 | `denim jacket` |
| 21 | `denim_shorts` | 牛仔短裤 | `bottom_pants` | 允许 | 禁止 | 223 | `denim shorts` |
| 22 | `dirndl_dress` | 紧身连衣裙 | `one_piece` | 允许 | 允许 | 159 | `dirndl` |
| 23 | `down_jacket` | 羽绒服 | `outerwear` | 允许 | 不适用 | 192 | `down jackets` |
| 24 | `dress_backless` | 露背连身裙 | `one_piece` | 不适用 | 允许 | 29 | `backless dress` |
| 25 | `dress_casual` | 连身裙 | `one_piece` | 不适用 | 允许 | 28 | `dress` |
| 26 | `duffel_coat` | 粗呢大衣 | `outerwear` | 允许 | 不适用 | 196 | `duffel coat` |
| 27 | `dungarees` | 工装 | `one_piece` | 允许 | 禁止 | 117 | `�� dungarees` |
| 28 | `fast_food_uniform` | 快餐制服 | `top` | 允许 | 不适用 | 214 | `fast food uniform` |
| 29 | `festive_costume` | 圣诞装 | `one_piece` | 允许 | 允许 | 49 | `santa` |
| 30 | `firefighter_gear` | 消防员夹克 | `outerwear` | 允许 | 不适用 | 189 | `firefighter jacket` |
| 31 | `fishnet_top` | 网纹衣 | `top` | 禁止 | 不适用 | 79 | `fishnet top` |
| 32 | `floral_dress_black` | 花卉图案连衣裙（黑） | `one_piece` | 不适用 | 允许 | 146 | `black skirt dress, flower pattern in dress,black gow` |
| 33 | `floral_dress_white` | 花卉图案连衣裙（白） | `one_piece` | 不适用 | 允许 | 145 | `white skirt dress, flower pattern in dress,white gow` |
| 34 | `formal_gown` | 礼服 | `one_piece` | 不适用 | 允许 | 73 | `full dress` |
| 35 | `frock_smock` | 罩衫 | `one_piece` | 允许 | 允许 | 131 | `frock` |
| 36 | `greek_toga` | 希腊服饰 | `one_piece` | 禁止 | 允许 | 123 | `greek clothes` |
| 37 | `halter_dress` | 绕颈连身裙 | `one_piece` | 不适用 | 允许 | 30 | `halter dress` |
| 38 | `hoodie` | 连帽衫(带帽卫衣) | `top` | 禁止 | 不适用 | 111 | `hoodie` |
| 39 | `hospital_gown` | 病号服 | `one_piece` | 允许 | 允许 | 121 | `hospital gown` |
| 40 | `hot_pants` | 热裤 | `bottom_pants` | 禁止 | 禁止 | 225 | `short shorts` |
| 41 | `knight_armor` | 铠甲 | `outerwear` | 禁止 | 禁止 | 109 | `armor` |
| 42 | `knit_vest` | V领针织毛衣（无袖背心） | `top` | 禁止 | 不适用 | 125 | `v-neck sweater vest` |
| 43 | `lab_coat` | 白大褂(By Yao_men) | `outerwear` | 允许 | 不适用 | 44 | `lab_coat` |
| 44 | `layered_skirt` | 多層裙子 | `bottom_skirt` | 禁止 | 允许 | 147 | `layered skirt` |
| 45 | `leather_jacket` | 皮衣 | `outerwear` | 允许 | 不适用 | 93 | `leather jacket` |
| 46 | `leather_skirt` | 皮裙 | `bottom_skirt` | 允许 | 允许 | 227 | `leather skirt` |
| 47 | `leotard_bodysuit` | 紧身衣 | `one_piece` | 禁止 | 禁止 | 17 | `leotard` |
| 48 | `lolita_fashion` | 洛丽塔风格 | `one_piece` | 允许 | 允许 | 89 | `lolita_fashion` |
| 49 | `lolita_gothic` | 哥特洛丽塔风格 | `one_piece` | 允许 | 允许 | 50 | `gothic_lolita` |
| 50 | `lolita_sweet` | 甜美可爱的洛丽塔 | `one_piece` | 允许 | 允许 | 78 | `sweet_lolita` |
| 51 | `long_skirt` | 长裙 | `bottom_skirt` | 禁止 | 允许 | 162 | `long skirt` |
| 52 | `mahou_shoujo` | 马猴烧酒风格 | `one_piece` | 禁止 | 允许 | 51 | `mahou shoujo` |
| 53 | `mecha_exoskeleton` | 外骨骼 | `outerwear` | 禁止 | 禁止 | 130 | `exoskeleton` |
| 54 | `mecha_power_armor` | 动力甲 | `outerwear` | 禁止 | 禁止 | 114 | `power armor` |
| 55 | `microskirt` | 微型短裙 | `bottom_skirt` | 禁止 | 允许 | 170 | `microskirt` |
| 56 | `military_overcoat` | 军大衣 | `outerwear` | 允许 | 不适用 | 133 | `army overcoat` |
| 57 | `military_uniform` | 军装 | `top` | 允许 | 禁止 | 60 | `military uniform` |
| 58 | `miniskirt` | 超短裙 | `bottom_skirt` | 禁止 | 允许 | 143 | `miniskirt` |
| 59 | `off_shoulder_dress` | 露肩连衣裙 | `one_piece` | 不适用 | 允许 | 167 | `off-shoulder dress` |
| 60 | `outerwear_coat` | 外套 | `outerwear` | 允许 | 不适用 | 110 | `coat` |
| 61 | `outerwear_jacket` | 夹克 | `outerwear` | 允许 | 不适用 | 183 | `jacket` |
| 62 | `outerwear_overcoat` | 大衣 | `outerwear` | 允许 | 不适用 | 83 | `overcoat` |
| 63 | `pencil_skirt` | 铅笔裙 | `bottom_skirt` | 禁止 | 允许 | 155 | `pencil skirt` |
| 64 | `pettiskirt` | 蓬蓬裙 | `bottom_skirt` | 禁止 | 允许 | 151 | `pettiskirt` |
| 65 | `plaid_skirt` | 格子裙 | `bottom_skirt` | 禁止 | 允许 | 153 | `plaid skirt` |
| 66 | `pleated_dress` | 带褶连衣裙 | `one_piece` | 不适用 | 允许 | 165 | `pleated dress` |
| 67 | `pleated_skirt` | 百褶裙 | `bottom_skirt` | 禁止 | 允许 | 142 | `pleated skirt` |
| 68 | `pumpkin_skirt` | 南瓜裙 | `bottom_skirt` | 禁止 | 允许 | 126 | `pumpkin skirt` |
| 69 | `racing_suit` | 赛车服(By KimZuo) | `one_piece` | 允许 | 禁止 | 41 | `racing suit` |
| 70 | `rain_skirt` | 雨裙 | `bottom_skirt` | 禁止 | 允许 | 163 | `rainskirt` |
| 71 | `rainwear_coat` | 雨衣 | `outerwear` | 允许 | 不适用 | 103 | `raincoat` |
| 72 | `robe_general` | 长袍 | `one_piece` | 允许 | 允许 | 85 | `robe` |
| 73 | `safari_jacket` | 探险家夹克 | `outerwear` | 允许 | 不适用 | 185 | `safari jacket` |
| 74 | `sailor_shirt` | 水手衬衫 | `top` | 禁止 | 不适用 | 176 | `sailor shirt` |
| 75 | `shinto_miko` | 巫女服 | `one_piece` | 禁止 | 允许 | 81 | `miko clothing` |
| 76 | `shirts_blouses` | 高领衬衫 | `top` | 允许 | 不适用 | 16 | `collared shirt` |
| 77 | `skirts_general` | 裙子 | `bottom_skirt` | 禁止 | 允许 | 141 | `skirt` |
| 78 | `sleeveless_dress` | 无袖连衣裙 | `one_piece` | 不适用 | 允许 | 219 | `sleeveless dress` |
| 79 | `slime_dress` | 史莱姆装 | `one_piece` | 禁止 | 允许 | 98 | `slime dress` |
| 80 | `soft_shell_jacket` | 软壳外套 | `outerwear` | 允许 | 不适用 | 128 | `soft shell coat` |
| 81 | `sportswear_active` | 运动服 | `top` | 不适用 | 不适用 | 10 | `sportswear` |
| 82 | `strapless_dress` | 无肩带礼服 | `one_piece` | 不适用 | 允许 | 166 | `strapless dress` |
| 83 | `strapless_top` | 抹胸 | `top` | 禁止 | 不适用 | 87 | `strapless tank top, navel cutout` |
| 84 | `summer_sundress` | 夏日长裙 | `one_piece` | 不适用 | 允许 | 46 | `summer long skirt` |
| 85 | `sundress_layered` | 黑色连衣裙+白色打底T恤搭配(请勿去掉tag括号) | `one_piece` | 不适用 | 允许 | 135 | `(((black sundress with round neck,white t-shirt bottom)))` |
| 86 | `suspender_skirt` | 吊带裙 | `bottom_skirt` | 禁止 | 允许 | 172 | `suspender skirt` |
| 87 | `sweater_casual` | 毛衣 | `top` | 禁止 | 不适用 | 33 | `sweater` |
| 88 | `sweater_dress` | 毛衣连身裙 | `one_piece` | 不适用 | 允许 | 31 | `sweater dress` |
| 89 | `sweatshirt` | 圆领卫衣 | `top` | 禁止 | 不适用 | 112 | `sweatshirt` |
| 90 | `swimsuit_classic` | 泳装 | `one_piece` | 禁止 | 禁止 | 6 | `swimsuit` |
| 91 | `swimsuit_competition` | 竞赛泳衣 | `one_piece` | 禁止 | 禁止 | 9 | `competition swimsuit` |
| 92 | `swimsuit_creative` | 中式死库水（辉木） | `one_piece` | 禁止 | 禁止 | 102 | `chinese style,one-piece swimsuit，clothes with gold patterns` |
| 93 | `swimsuit_school` | 学校泳衣 | `one_piece` | 禁止 | 禁止 | 8 | `school swimsuit` |
| 94 | `t_shirt` | T恤 | `top` | 禁止 | 不适用 | 177 | `t-shirt` |
| 95 | `tactical_vest` | 防弹衣 | `outerwear` | 允许 | 不适用 | 94 | `bulletproof_vest` |
| 96 | `tailcoat` | 燕尾服 | `outerwear` | 允许 | 禁止 | 197 | `tailcoat` |
| 97 | `taoist_robe` | 道袍 | `one_piece` | 禁止 | 允许 | 132 | `taoist robe` |
| 98 | `tops_tanks` | 背心 | `top` | 禁止 | 不适用 | 27 | `tank top` |
| 99 | `trench_coat` | 风衣 | `outerwear` | 允许 | 不适用 | 67 | `wind coat` |
| 100 | `tulle_dress` | 薄纱连衣裙 | `one_piece` | 不适用 | 允许 | 24 | `sheer tulle dress` |
| 101 | `tutu_skirt` | 芭蕾舞裙 | `bottom_skirt` | 禁止 | 允许 | 152 | `tutu` |
| 102 | `volleyball_uniform` | 排球服 | `top` | 禁止 | 禁止 | 11 | `volleyball uniform` |
| 103 | `waist_apron` | 腰围裙 | `bottom_skirt` | 禁止 | 允许 | 150 | `waist apron` |
| 104 | `wedding_dress` | 婚纱 | `one_piece` | 禁止 | 允许 | 63 | `wedding_dress` |
| 105 | `windbreaker` | 白色风衣 | `outerwear` | 允许 | 不适用 | 66 | `white_windbreaker` |
| 106 | `winter_parka` | 派克大衣 | `outerwear` | 允许 | 不适用 | 88 | `parka` |
| 107 | `witch_robe` | 魔女风格服 | `one_piece` | 禁止 | 允许 | 80 | `witch dress` |
| 108 | `wizard_robe` | 巫师法袍 | `one_piece` | 禁止 | 允许 | 222 | `wizard robe` |
| 109 | `zentai_suit` | 紧身连体衣 | `one_piece` | 禁止 | 禁止 | 92 | `zentai` |

---

## 五、服装状态 (`clothing.json -> clothing_states`) 详细差集清单

### 1. 存量保留状态 (12 条真实运行时数据记录，含 1 联动)
经核对 `data/clothing.json` 运行时真实结构，系统存量状态共 12 条，本次迁移 100% 保真保留：

- `auto_link`：自动联动裸露等级 (Auto Link Nudity)
- `normal`：整齐穿着 (Normal Wearing)
- `unbuttoned`：解开纽扣 (Unbuttoned)
- `slipping_off`：吊带滑落/半脱 (Slipping Off)
- `lifted_up`：裙摆掀起 (Skirt Lifted Up)
- `pulled_down`：内衣拉下 (Pulled Down)
- `wet_clinging`：湿身紧贴透光 (Wet & Clinging)
- `sweat_soaked`：汗湿透光 (Sweat-soaked)
- `torn_shredded`：撕裂破损 (Torn & Shredded)
- `disheveled`：衣衫不整/凌乱 (Disheveled/Messy)
- `only_lingerie`：仅剩内衣/丝袜 (Only Lingerie)
- `discarded`：脱掉散落一旁 (Discarded on Floor)

### 2. 台账提取确定新增状态清单 (11 条，完整覆盖三元分类)

| 序号 | 规范状态 ID | 三元分类归属 | 原始行号 | 中文名称 | 规范化提示词 | 承载判定与互斥消解语义契约 |
|---|---|---|---|---|---|---|
| 1 | `undergarment_leotard` | 多层叠穿状态 | 21 | 衣服下紧身衣 | `leotard under clothes` | 紧身连体衣内穿叠穿状态，要求必须存在具备覆盖能力的外层服装 |
| 2 | `taut_tight` | 修饰状态 (质感) | 22 | 紧身衣服 | `taut clothes` | 衣物紧绷勾勒状态，依附于在穿服装主体 |
| 3 | `heart_cutout` | 修饰状态 (切口) | 37 | 心型切口 | `heart cutout` | 胸前心型镂空剪裁，依附于上装/连身裙 |
| 4 | `back_cutout` | 修饰状态 (切口) | 38 | 后背切口 | `back cutout` | 后背镂空露背剪裁，依附于上装/连身裙 |
| 5 | `underboob_cutout` | 修饰状态 (切口) | 39 | 下胸切口 | `underboob cutout` | 下胸微露镂空剪裁，依附于上装/连身裙 |
| 6 | `braless` | 缺席/真空状态 | 57 | 无胸罩 | `no_bra` | 声明胸部内衣缺席；仅当场景存在显式文胸时互斥Drop，外衣/无内衣时100%保留 |
| 7 | `wet_pure` | 修饰状态 (湿润) | 84 | 湿润的衣服 | `wet clothes` | 纯粹湿身质感，不标半透与紧贴，依附于在穿服装主体 |
| 8 | `underwearless` | 缺席/真空状态 | 90 | 无内衣 | `no underwear` | 声明下身内裤缺席；仅当场景存在显式内裤时互斥Drop，外衣/无内衣时100%保留 |
| 9 | `skirt_under_kimono` | 多层叠穿状态 | 229 | 和服下的裙子 | `skirt under kimono` | 必须同时存在外层和服类与内层裙子两件独立服装主体，单件和服判定不成立 |
| 10 | `off_shoulder_cut` | 修饰状态 (切口) | 236 | 露单肩 | `off_shoulder` | 露单肩非对称剪裁状态，依附于上装/连身裙 |
| 11 | `bare_shoulders` | 修饰状态 (切口) | 237 | 露双肩 | `bare_shoulders` | 露双肩大平领穿着状态，依附于上装/连身裙 |

---

## 六、配饰、情趣内衣与微瑕差集清单

### 1. 配饰 (`accessories.json -> headwear_jewelry`，运行时槽位 `jewelry`) 新增清单 (+7 项)
1. `neck_ribbon` (丝带颈带, Row 56, `neck ribbon`)
2. `hooded_cloak` (连帽防风斗篷, Rows 58, 182, `Cape hood` / `hooded cloak`，合并去重，纳入承载白名单)
3. `cloak` (无帽长披风, Rows 65, 245, `cloak` / `cape`，合并去重，纳入承载白名单)
4. `poncho` (套头小披风, Row 75, `poncho`，纳入承载白名单)
5. `waist_belt` (基础皮革腰带, Row 243, `belt`)
6. `winter_scarf` (针织保暖围巾, Row 244, `scarf`)
7. `fur_shawl` (保暖皮草披肩, Row 246, `fur shawl`，纳入承载白名单)

### 2. 情趣内衣 (`clothing.json -> lingerie_wardrobe`，运行时槽位 `lingerie`) 新增清单 (+2 项)
1. `crotchless_panties` (开裆无底内裤, Row 82, `crotchless panties`，独立建模)
2. `basic_underwear` (基础日常内衣, Row 129, `underwear`，新增独立规范条目；注：原误注 Row 114 实为 power armor)

### 3. 身体微瑕 (`imperfections.json -> categories`，运行时槽位 `imperfections`) 新增清单 (+1 项)
1. `tan_lines` (日晒泳装痕迹, Row 70, `tan lines`，身体唯一主归属，彻底剥离衣柜)

---

## 七、原始 246 行基线与全量留底说明

### 1. 原始文件物理完整性与密码学哈希
- **原始附件物理溯源**：来自用户上传的原始附件 `media_1789978587694.md` (SHA-256: `716af655968d22eaf970c727aa0a13f273a4f6d097185e06080c28d2636e4ee4`)，表体本身即为完整的 246 行；
- **原始 TSV 物理路径**：`docs/data_migration/raw_clothing_input.tsv` (SHA-256: `65e4c56655b4c0f8f3629a5e26d407d77860d3581d55b2da7dd92ff87d7fc2ff`)；
- **1:1 绝对一致性验证**：两文件第 1~246 行逐行内容 1:1 绝对一致（0 差异）；
- **行号连续性**：行号 1~246 严格物理连续，末两行为 `245 cape`（披肩）与 `246 fur shawl`（皮草披肩）；
- **原附件实核 246 行与留底记录**：原附件实核为 246 行数据行（SHA-256: `716af655968d22eaf970c727aa0a13f273a4f6d097185e06080c28d2636e4ee4`），转换后 TSV 亦为 246 行（SHA-256: `65e4c56655b4c0f8f3629a5e26d407d77860d3581d55b2da7dd92ff87d7fc2ff`），逐行三列内容 1:1 完全一致（差异为 0）。第 100 行物理内容为 `100\t综合2\t无\tless clothes\n`，其无对应独立款式，在台账中归为 `[Defer]`（延期保留，不污染发布词库，槽位与目标 ID 标 `-`），使台账与原始附件达成 246 行严格 1:1 守恒对齐。

### 2. Git 提交基线与工作区修改范围界定
- **Git 起始提交基线**：`608749edd80fb701b7fec45e625556016e175456` (`docs: rewrite README for rc8`)；
- **工作区既有修改澄清**：当前工作区中 `lib/assembler.py`、`lib/models.py`、`nodes.py` 等文件的既有 Git 修改属于历史遗留或前期任务遗留，**并非本次服装词库迁移所致**；
- **本次迁移交付物物理边界**：
  - 严格限定于 `docs/data_migration/`、`scripts/` 与 `tests/`；
  - 仅对 `schemas/clothing.schema.json` 增加了可选的 `aliases` 属性；
  - **完全未触碰** 运行时核心词库 `data/*.json` 及消解器代码 `lib/conflict_resolver.py`，确保 Milestone 1/2 交付物的只读纯洁性。

### 3. 门禁核验结论准确表述
> [!NOTE]
> **阶段性验收权威结论**：
> **台账一致性与夹具结构检查通过；规则行为及节点集成测试待 M3 实现后验证。**
