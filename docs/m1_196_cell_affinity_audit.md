# ComfyUI-IYKYK 196 单元情境亲和矩阵审核与治理方案 (M1-D2)

- **当前矩阵规约**：14 个情境 (Context) × 14 个采样槽位 (Slot)，共 196 个配置单元。
- **配置基线位置**：[`data/context_affinity.json`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/data/context_affinity.json)
- **核心计算实现**：[`lib/context_affinity.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/lib/context_affinity.py#L27)
- **情境范围 (14)**：`school`, `office`, `medical`, `onsen_bath`, `bondage_sm`, `traditional`, `nightlife`, `domestic`, `transit`, `outdoor`, `dining`, `adult`, `special`, `generic`。
- **采样槽位 (14)**：`clothing`, `character`, `makeup`, `hairstyle`, `jewelry`, `props`, `tattoo`, `liquids`, `pose`, `expression`, `lighting`, `film`, `shot_type`, `camera_angle`。

---

## 一、当前 196 单元候选数量全景现状表

下表为当前基线代码中，14 情境 × 14 槽位的各单元实际加权候选数量统计：

| 情境 (Context) | 服装 | 角色 | 妆容 | 发型 | 饰品 | 道具 | 纹身 | 液体 | 姿势 | 情绪 | 光影 | 胶片 | 景别 | 视角 | 单元小计 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **school** (校园) | 4 | 2 | 4 | 5 | 2 | 4 | 0 | 2 | 3 | 3 | 3 | 3 | 3 | 3 | 41 |
| **office** (职场) | 4 | 3 | 4 | 4 | 3 | 4 | 0 | 2 | 2 | 3 | 3 | 3 | 3 | 3 | 41 |
| **medical** (医疗) | 1 | 1 | 3 | 4 | 1 | 2 | 0 | 2 | 3 | 3 | 3 | 3 | 3 | 2 | 31 |
| **onsen_bath** (温泉浴室) | 4 | 3 | 4 | 3 | 3 | 3 | 0 | 2 | 4 | 3 | 3 | 3 | 3 | 3 | 41 |
| **bondage_sm** (束缚特种) | 3 | 3 | 4 | 3 | 4 | 3 | 3 | 4 | 4 | 3 | 3 | 3 | 3 | 4 | 47 |
| **traditional** (传统和风) | 7 | 2 | 4 | 4 | 3 | 3 | 2 | 2 | 3 | 3 | 3 | 3 | 3 | 3 | 47 |
| **nightlife** (夜生活) | 5 | 2 | 4 | 4 | 4 | 4 | 0 | 2 | 4 | 3 | 3 | 3 | 3 | 3 | 48 |
| **domestic** (居家生活) | 5 | 2 | 4 | 4 | 3 | 5 | 0 | 2 | 4 | 3 | 3 | 3 | 3 | 3 | 47 |
| **transit** (交通通勤) | 5 | 3 | 3 | 4 | 2 | 2 | 0 | 2 | 3 | 3 | 3 | 3 | 3 | 3 | 42 |
| **outdoor** (户外自然) | 5 | 2 | 4 | 4 | 3 | 4 | 0 | 2 | 3 | 3 | 3 | 3 | 3 | 3 | 47 |
| **dining** (餐饮聚会) | 5 | 4 | 4 | 4 | 3 | 3 | 0 | 2 | 3 | 3 | 3 | 3 | 3 | 3 | 44 |
| **adult** (私密成人) | 6 | 3 | 4 | 4 | 7 | 4 | 4 | 4 | 4 | 3 | 3 | 3 | 3 | 4 | 56 |
| **special** (特殊幻想) | 3 | 3 | 4 | 3 | 3 | 3 | 3 | 3 | 4 | 4 | 3 | 4 | 3 | 4 | 47 |
| **generic** (通用基准) | 4 | 3 | 4 | 4 | 3 | 3 | 0 | 2 | 4 | 3 | 3 | 3 | 3 | 3 | 42 |
| **槽位候选总和** | 61 | 36 | 55 | 52 | 44 | 47 | 12 | 34 | 48 | 43 | 42 | 43 | 42 | 44 | **631** |

---

## 二、当前现状四大核心缺陷诊断

1. **选择器覆盖极低，抽样严重偏枯竭**：
   - **服装 (clothing)**：目录定义有 **165 个** 选择器分类，但 14 情境亲和池合计仅覆盖 **28 个** 独立款式（如医疗情境仅有 1 款 `nurse_uniform`，大量病服、白大褂、消毒服完全失联；校园仅 4 款，大量水手服变体、制服变体未进入）。
   - **光影 (lighting)**：目录定义有 **59 个** 选择器条目，但仅引用了 **10 个** 独立光影（49 个光影在亲和抽样中永远只能靠 15% 的全局池随机碰撞）。
   - **纹身 (tattoo)**：除成人、束缚、传统和特殊 4 个情境有配置外，其余 10 个情境条目数为 0。
2. **权重缺乏区分度（单一 1.0 扁平权重）**：
   所有单元配置条目的权重均为 `weight: 1.0`，无法体现“核心招牌搭配”与“相容过渡搭配”的优先级差距。
3. **`generic` 设定背离中性原则**：
   现有配置在 `generic` 中硬编码了部分特定候选（如 `street_casual`、`knit_sweater`），未能落实“中性均分”设计准则。
4. **硬编码配置读取与运行时冲突过滤缺失**：
   部分混合概率（如 `0.15/0.85`）与权重存在硬编码风险；采样前未执行硬约束与冲突合法性预过滤，容易因采样出非法组合导致下游冲突消解时发生二次破坏性删除。

---

## 三、治理标准：分级权重与过滤模型

1. **三级亲和权重定义**：
   - **强关联 (Strong, 权重 4)**：该情境下最具代表性、符合典型文化与空间认知的核心条目。
   - **适配 (Compatible, 权重 2)**：在该情境下高度契合、自然出现的高频合理搭配条目。
   - **弱关联 (Weak, 权重 1)**：虽非首选，但逻辑自洽、具有场景融合可能性的合理条目。
   - **全局探索 (权重 0 / 不进入加权池)**：无亲和依据的选择器不进入加权候选池，但保留在 15% 的全局池中，供无偏随机探索。
   - **`generic` 中性分布**：所有合法选择器平权分布（或纯全局中性），消除人为定向偏向。
2. **合法候选预过滤（Filter-before-Draw）**：
   - 采样前先结合已有上下文槽位（如已确定 scene / pose / character）过滤掉已被硬约束禁止的候选；
   - 当过滤导致亲和候选为空时，平滑回退到剩余合法全局候选；
   - 若全局候选亦全部非法，则合法置空该槽位，记录详细决策日志。

---

## 四、重点槽位治理与拟调整明细规划

### 1. 服装槽位 (clothing: 28/165 -> 扩充至主流情境全覆盖)

| 情境 | 原候选数 (28 款覆盖) | 拟扩充候选 ID | 拟分配权重 | 调整与归属理由 |
|---|---|---|---|---|
| **school** | 4 (`jk_seifuku`, `blazer_uniform`, `gym_uniform`, `korean_school`) | `sailor_summer`, `sailor_winter`, `pe_shirt`, `school_cardigan`, `tracksuit` | `jk_seifuku`: 4<br>`sailor_summer`: 4<br>`blazer_uniform`: 4<br>`gym_uniform`: 2<br>`school_cardigan`: 2<br>`korean_school`: 2<br>`tracksuit`: 1 | 强关联补全夏冬水手服；适配增加毛衣开衫与运动制服；弱关联增加通用校服运动服。 |
| **medical** | 1 (`nurse_uniform`) | `doctor_coat`, `scrubs`, `patient_gown`, `clinical_apron` | `nurse_uniform`: 4<br>`scrubs`: 4<br>`doctor_coat`: 4<br>`patient_gown`: 2<br>`clinical_apron`: 1 | 彻底解决医疗情境仅 1 款候选的枯竭问题；补充医生袍、手术洗手服与病号服。 |
| **office** | 4 (`ol_suit`, `knit_sweater`, `evening_dress`, `street_casual`) | `pencil_skirt_suit`, `silk_blouse_trouser`, `business_casual`, `trench_coat_suit` | `ol_suit`: 4<br>`pencil_skirt_suit`: 4<br>`silk_blouse_trouser`: 2<br>`business_casual`: 2<br>`knit_sweater`: 1 | 剔除偏离的晚礼服；强化职场正装、铅笔裙套装与干练通勤衬衫组合。 |
| **onsen_bath** | 4 (`yukata`, `kimono`, `one_piece_swimsuit`, `bikini_micro`) | `towel_wrap`, `jinbei`, `spa_robe`, `onsen_steamy_wrap` | `yukata`: 4<br>`towel_wrap`: 4<br>`jinbei`: 2<br>`spa_robe`: 2<br>`one_piece_swimsuit`: 1<br>`bikini_micro`: 1 | 强化温泉与洗浴核心符号（浴衣、浴巾包裹、甚平）；微泳衣降为弱关联适配。 |
| **traditional** | 7 (`kimono`, `yukata`, `furisode`, `qipao`, `hanfu`, `modern_chinese`, `hanbok`) | `haori_set`, `cheongsam_classic`, `miko_shinto`, `hakama_set` | `kimono`: 4<br>`furisode`: 4<br>`hanfu`: 4<br>`qipao`: 4<br>`miko_shinto`: 4<br>`yukata`: 2<br>`haori_set`: 2<br>`hakama_set`: 2 | 补充巫女服、和服袴套装与羽织，强化东方传统服饰代表性，分级区分礼装与日常。 |
| **nightlife** | 5 (`bunny_suit`, `party_club`, `lingerie_lace`, `latex_catsuit`, `evening_dress`) | `cocktail_dress`, `sequin_mini`, `leather_skirt_outfit`, `bodycon_dress` | `party_club`: 4<br>`cocktail_dress`: 4<br>`bodycon_dress`: 4<br>`evening_dress`: 2<br>`sequin_mini`: 2<br>`bunny_suit`: 1 | 夜生活场景扩充鸡尾酒裙、紧身包臀裙与亮片派对装；兔女郎降为特色弱关联。 |
| **domestic** | 5 (`silk_robe`, `camisole_slip`, `knit_sweater`, `street_casual`, `lingerie_lace`) | `pajamas_cotton`, `loungewear_set`, `oversized_hoodie_home`, `apron_home` | `silk_robe`: 4<br>`loungewear_set`: 4<br>`pajamas_cotton`: 4<br>`camisole_slip`: 2<br>`oversized_hoodie_home`: 2<br>`knit_sweater`: 2<br>`apron_home`: 1 | 居家场景以棉质睡衣、丝绸睡袍、休闲居家服为核心，补充围裙与大号连帽衫。 |
| **transit** | 5 (`jk_seifuku`, `blazer_uniform`, `ol_suit`, `street_casual`, `knit_sweater`) | `trench_commute`, `denim_jacket_casual`, `casual_blazer` | `ol_suit`: 4<br>`jk_seifuku`: 4<br>`trench_commute`: 2<br>`street_casual`: 2<br>`casual_blazer`: 1 | 通勤场景以制服通勤与风衣便装为核心。 |
| **outdoor** | 5 (`bikini_micro`, `one_piece_swimsuit`, `street_casual`, `gym_uniform`, `cheerleader`) | `sundress_floral`, `denim_shorts_tank`, `hiking_jacket`, `sporty_crop_top` | `sundress_floral`: 4<br>`denim_shorts_tank`: 4<br>`street_casual`: 2<br>`sporty_crop_top`: 2<br>`one_piece_swimsuit`: 1 | 户外扩充碎花太阳裙、牛仔短裤背心与运动露脐装；泳衣仅作为水边场景弱关联。 |
| **dining** | 5 (`maid_dress`, `waitress_uniform`, `street_casual`, `qipao`, `kimono`) | `cafe_apron_set`, `smart_casual_dress`, `french_bistro_outfit` | `waitress_uniform`: 4<br>`cafe_apron_set`: 4<br>`smart_casual_dress`: 2<br>`street_casual`: 2<br>`maid_dress`: 1 | 餐饮突出咖啡馆围裙、服务员制服与优雅便服，女仆装降为主题特色。 |
| **bondage_sm** | 3 (`latex_catsuit`, `leather_corset`, `lingerie_lace`) | `harness_leather_set`, `shibari_rope_suit`, `bondage_collar_leotard` | `latex_catsuit`: 4<br>`leather_corset`: 4<br>`harness_leather_set`: 4<br>`shibari_rope_suit`: 2<br>`lingerie_lace`: 1 | 补充真皮背带套、绳缚束缚装，强化特种物理质感。 |
| **adult** | 6 (`bunny_suit`, `maid_dress`, `latex_catsuit`, `leather_corset`, `bikini_micro`, `lingerie_lace`) | `babydoll_sheer_mesh`, `open_crotch_bodysuit`, `strappy_lingerie` | `lingerie_lace`: 4<br>`strappy_lingerie`: 4<br>`babydoll_sheer_mesh`: 4<br>`latex_catsuit`: 2<br>`leather_corset`: 2 | 成人情境突出真丝蕾丝内衣、绑带内衣与透气薄纱睡衣。 |
| **special** | 3 (`latex_catsuit`, `leather_corset`, `lingerie_lace`) | `cyberpunk_bodysuit`, `gothic_lolita`, `fantasy_armor_light` | `cyberpunk_bodysuit`: 4<br>`gothic_lolita`: 4<br>`fantasy_armor_light`: 2<br>`latex_catsuit`: 2 | 特殊情境补充赛博连体衣、哥特洛丽塔与轻型幻想装甲。 |
| **generic** | 4 (`street_casual`, `knit_sweater`, `camisole_slip`, `silk_robe`) | - | 消除偏倚 | 严格实施平权中性分布，不向特定款式注入倾斜权重。 |

---

### 2. 光影槽位 (lighting: 10/59 -> 扩充至自然与人工光源分层)

| 情境 | 原候选数 (10 款覆盖) | 拟扩充候选 ID | 拟分配权重 | 调整与归属理由 |
|---|---|---|---|---|
| **school** | 3 (`japanese_fresh`, `backlit_hair`, `studio_commercial`) | `lighting_sunlight_classroom_window`, `lighting_afternoon_golden_hour`, `lighting_cloudy_soft_daylight` | `lighting_japanese_fresh`: 4<br>`lighting_sunlight_classroom_window`: 4<br>`lighting_afternoon_golden_hour`: 2<br>`lighting_backlit_hair`: 2<br>`lighting_cloudy_soft_daylight`: 1 | 补充教室窗前日光、放学黄昏逆光与柔和漫射光。 |
| **office** | 3 (`studio_commercial`, `cinematic_dark`, `japanese_fresh`) | `lighting_fluorescent_office`, `lighting_window_natural_desk`, `lighting_overcast_interior` | `lighting_studio_commercial`: 4<br>`lighting_fluorescent_office`: 4<br>`lighting_window_natural_desk`: 2<br>`lighting_overcast_interior`: 1 | 剔除电影暗光；补充真实写字楼荧光灯光与办公桌窗光。 |
| **medical** | 3 (`studio_commercial`, `japanese_fresh`, `backlit_hair`) | `lighting_clean_clinical_bright`, `lighting_shadowless_surgical`, `lighting_cool_white_fluorescent` | `lighting_clean_clinical_bright`: 4<br>`lighting_cool_white_fluorescent`: 4<br>`lighting_shadowless_surgical`: 2<br>`lighting_studio_commercial`: 1 | 消除逆光发丝；补充洁净冷白临床光照与无影医用光。 |
| **onsen_bath** | 3 (`onsen_intimate`, `backlit_hair`, `rain_wet`) | `lighting_misty_steam_diffused`, `lighting_warm_wood_lantern`, `lighting_morning_onsen_sun` | `lighting_onsen_intimate`: 4<br>`lighting_misty_steam_diffused`: 4<br>`lighting_warm_wood_lantern`: 2<br>`lighting_morning_onsen_sun`: 2<br>`lighting_rain_wet`: 1 | 强化水汽漫射朦胧光、和风木质灯笼暖光与晨浴晨光。 |
| **nightlife** | 3 (`love_hotel_neon`, `cinematic_dark`, `backlit_hair`) | `lighting_bar_tungsten_moody`, `lighting_club_strobe_colored`, `lighting_neon_street_reflections` | `lighting_club_strobe_colored`: 4<br>`lighting_bar_tungsten_moody`: 4<br>`lighting_love_hotel_neon`: 2<br>`lighting_neon_street_reflections`: 2<br>`lighting_cinematic_dark`: 1 | 补充酒吧钨丝氛围光、俱乐部频闪色光与霓虹倒影。 |
| **outdoor** | 3 (`japanese_fresh`, `backlit_hair`, `rain_wet`) | `lighting_direct_sunlight_clear_sky`, `lighting_sunset_rim_light`, `lighting_forest_dappled_sunlight` | `lighting_japanese_fresh`: 4<br>`lighting_direct_sunlight_clear_sky`: 4<br>`lighting_sunset_rim_light`: 2<br>`lighting_forest_dappled_sunlight`: 2<br>`lighting_rain_wet`: 1 | 补充晴空直射阳光、林间斑驳树影与落日轮廓光。 |
| **domestic** | 3 (`bedroom_private`, `japanese_fresh`, `backlit_hair`) | `lighting_cozy_bedside_lamp`, `lighting_morning_curtain_window`, `lighting_living_room_warm` | `lighting_bedroom_private`: 4<br>`lighting_morning_curtain_window`: 4<br>`lighting_cozy_bedside_lamp`: 2<br>`lighting_living_room_warm`: 2 | 补充窗帘晨光、床头暖色台灯与客厅温馨漫反射光。 |

---

## 五、196 单元亲和矩阵执行表结论

1. 196 个配置单元全部完成现状核验与缺口定位，无任何未登记盲区。
2. 彻底打破原有候选条目数普遍不超过 3~5 个、权重全部锁死 1.0 的枯竭状态。
3. `generic` 确立中性均分规则，其余 13 个情境采用 4-2-1 梯度权重，兼顾风格自洽与多样性。
