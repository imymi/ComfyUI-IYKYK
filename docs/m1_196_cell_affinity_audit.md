# ComfyUI-IYKYK 196 单元情境亲和矩阵审核与治理方案 (M1-D2 修订版)

- **当前矩阵规约**：14 个情境 (Context) × 14 个采样槽位 (Slot)，共 196 个配置单元。
- **配置基线位置**：[`data/context_affinity.json`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/data/context_affinity.json)
- **核心计算实现**：[`lib/context_affinity.py`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/lib/context_affinity.py#L27)
- **真实注册选择器库**：14 槽位共计 **496 个选择器**（经 `load_slot_id_registries` 严谨加载）。
- **情境范围 (14)**：`school`, `office`, `medical`, `onsen_bath`, `bondage_sm`, `traditional`, `nightlife`, `domestic`, `transit`, `outdoor`, `dining`, `adult`, `special`, `generic`。
- **采样槽位 (14)**：`clothing`, `character`, `makeup`, `hairstyle`, `jewelry`, `props`, `tattoo`, `liquids`, `pose`, `expression`, `lighting`, `film`, `shot_type`, `camera_angle`。

---

## 一、当前 196 单元候选数量全景现状表（精确核实值）

经对 [`data/context_affinity.json`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/data/context_affinity.json) 逐单元遍历，14 情境 × 14 槽位各单元候选出现次数总和精确为 **603 次**：

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
| **槽位候选总和** | 61 | 36 | 55 | 52 | 44 | 47 | 12 | 34 | 48 | 43 | 42 | 43 | 42 | 44 | **603** |

---

## 二、真实 ID 边界与三大治理原则

1. **绝对零幻觉（100% 真实注册 ID）**：
   - 本轮开发明确不扩充词库规模，所有拟调整的候选 ID **必须全部存在于现有目录注册表中**；
   - 彻底剔除上一版草稿中的 `sailor_summer`、`doctor_coat`、`lighting_shadowless_surgical` 等伪造 ID，全部替换为已注册的标准 ID（如 `sailor_shirt`、`lab_coat`、`prof_light_fluorescent` 等）。
2. **权重三级分层（4-2-1 梯度模型）**：
   - **4 (强关联)**：代表该情境的核心标志性符号（如校园的水手服制服、医院的护士服与白大褂）；
   - **2 (适配)**：在该情境下高频自然出现、合情合理的搭配（如校园的体操服、通勤的西装风衣）；
   - **1 (弱关联)**：具备融合可能性的过渡搭配；
   - **0 (全局探索)**：无亲和依据的选择器不进入加权候选，但保留在 15% 的全局池中，杜绝无故不可达。
3. **`generic` 中性均分**：
   - 通用情境 `generic` 消除人为偏倚，所有合法条目按等权处理，保持中性基础。

---

## 三、14 情境逐情境真实 ID 拟调整与权重明细表

### 1. 服装槽位 (clothing, 注册总数 165)

| 情境 | 原候选清单 (原覆盖 28 款) | 拟调整后真实候选 ID 清单与权重 (4/2/1) | 调整理由与语义事实依据 |
|---|---|---|---|
| **school** | `jk_seifuku`, `blazer_uniform`, `gym_uniform`, `korean_school` | `jk_seifuku: 4`<br>`blazer_uniform: 4`<br>`sailor_shirt: 4`<br>`gym_uniform: 2`<br>`korean_school: 2`<br>`swimsuit_school: 1` | 补充真实目录中的水手服上衣 (`sailor_shirt`)；体操服与韩式校服定为适配(2)；死库水泳衣定为弱关联(1)。 |
| **medical** | `nurse_uniform` | `nurse_uniform: 4`<br>`lab_coat: 4`<br>`hospital_gown: 2` | 彻底解决医疗情境仅 1 款候选的枯竭问题；补充真实目录医生袍 (`lab_coat`) 与病号服 (`hospital_gown`)。 |
| **office** | `ol_suit`, `knit_sweater`, `evening_dress`, `street_casual` | `ol_suit: 4`<br>`business_suit: 4`<br>`pencil_skirt: 4`<br>`shirts_blouses: 2`<br>`trousers_general: 2`<br>`knit_sweater: 1` | 剔除不合时宜的晚礼服；补充商务西服 (`business_suit`)、铅笔裙与衬衫；针织衫降为弱关联。 |
| **onsen_bath** | `yukata`, `kimono`, `one_piece_swimsuit`, `bikini_micro` | `yukata: 4`<br>`bathrobe: 4`<br>`silk_robe: 2`<br>`one_piece_swimsuit: 1`<br>`bikini_micro: 1` | 补充真实浴室浴袍 (`bathrobe`) 与丝绸睡袍；连体泳衣与微型比基尼定为弱关联(1)。 |
| **bondage_sm** | `latex_catsuit`, `leather_corset`, `lingerie_lace` | `latex_catsuit: 4`<br>`leather_corset: 4`<br>`zentai_suit: 2`<br>`leather_skirt: 2`<br>`lingerie_lace: 1` | 补充真实目录全身紧身衣 (`zentai_suit`) 与皮质短裙 (`leather_skirt`)，强化特种物理质感。 |
| **traditional** | `kimono`, `yukata`, `furisode`, `qipao`, `hanfu`, `modern_chinese`, `hanbok` | `kimono: 4`<br>`furisode: 4`<br>`qipao: 4`<br>`hanfu: 4`<br>`shinto_miko: 4`<br>`yukata: 2`<br>`hanbok: 2`<br>`modern_chinese: 2`<br>`uchikake: 2` | 补充巫女服 (`shinto_miko`) 与打褂礼服 (`uchikake`)；分级区分礼装(4)与节庆休闲(2)。 |
| **nightlife** | `bunny_suit`, `party_club`, `lingerie_lace`, `latex_catsuit`, `evening_dress` | `party_club: 4`<br>`cocktail_dress: 4`<br>`evening_dress: 4`<br>`leather_skirt: 2`<br>`bunny_suit: 2`<br>`lingerie_lace: 1` | 补充鸡尾酒裙 (`cocktail_dress`)；兔女郎与皮裙作为特色夜店装定为适配(2)。 |
| **domestic** | `silk_robe`, `camisole_slip`, `knit_sweater`, `street_casual`, `lingerie_lace` | `silk_robe: 4`<br>`camisole_slip: 4`<br>`hoodie: 2`<br>`sweatshirt: 2`<br>`knit_sweater: 2`<br>`apron_dress: 2`<br>`street_casual: 1` | 补充连帽衫 (`hoodie`)、卫衣 (`sweatshirt`) 与围裙 (`apron_dress`)，丰富居家真实感。 |
| **transit** | `jk_seifuku`, `blazer_uniform`, `ol_suit`, `street_casual`, `knit_sweater` | `ol_suit: 4`<br>`jk_seifuku: 4`<br>`blazer_uniform: 2`<br>`trench_coat: 2`<br>`denim_jacket: 1` | 补充通勤防风风衣 (`trench_coat`) 与牛仔外套；强化早晚电车通学通勤符号。 |
| **outdoor** | `bikini_micro`, `one_piece_swimsuit`, `street_casual`, `gym_uniform`, `cheerleader` | `denim_shorts: 4`<br>`summer_sundress: 4`<br>`sundress_layered: 2`<br>`hot_pants: 2`<br>`gym_uniform: 2`<br>`street_casual: 2`<br>`bikini_classic: 1` | 补充牛仔短裤 (`denim_shorts`)、太阳裙 (`summer_sundress`) 与热裤 (`hot_pants`)；泳衣降为弱关联。 |
| **dining** | `maid_dress`, `waitress_uniform`, `street_casual`, `qipao`, `kimono` | `maid_dress: 4`<br>`waitress_uniform: 4`<br>`waist_apron: 4`<br>`qipao: 2`<br>`dress_casual: 2`<br>`street_casual: 1` | 补充餐饮咖啡半身围裙 (`waist_apron`) 与休闲连衣裙；女仆与侍应生为强关联。 |
| **adult** | `bunny_suit`, `maid_dress`, `latex_catsuit`, `leather_corset`, `bikini_micro`, `lingerie_lace` | `lingerie_lace: 4`<br>`lingerie_bra: 4`<br>`lingerie_panties: 4`<br>`latex_catsuit: 2`<br>`leather_corset: 2`<br>`bikini_micro: 2` | 核心强化真实蕾丝内衣 (`lingerie_lace`, `lingerie_bra`, `lingerie_panties`)。 |
| **special** | `latex_catsuit`, `leather_corset`, `lingerie_lace` | `cyberpunk_plugsuit: 4`<br>`cyberpunk_tactical_bodysuit: 4`<br>`lolita_gothic: 4`<br>`lolita_sweet: 2`<br>`latex_catsuit: 2` | 补充赛博朋克防护服 (`cyberpunk_plugsuit`) 与哥特洛丽塔 (`lolita_gothic`)。 |
| **generic** | `street_casual`, `knit_sweater`, `camisole_slip`, `silk_robe` | `street_casual: 1`<br>`knit_sweater: 1`<br>`t_shirt: 1`<br>`denim_jeans: 1` | 消除偏向，采用中性基础便服均权分布。 |

---

### 2. 光影槽位 (lighting, 注册总数 59)

| 情境 | 原候选清单 (原覆盖 10 款) | 拟调整后真实候选 ID 清单与权重 (4/2/1) | 调整理由与光源事实依据 |
|---|---|---|---|
| **school** | `japanese_fresh`, `backlit_hair`, `studio_commercial` | `lighting_japanese_fresh: 4`<br>`prof_light_high_key: 4`<br>`prof_light_ambient: 2`<br>`ext_aw_light_warm_golden_hour: 2`<br>`lighting_backlit_hair: 1` | 清新通透自然光与高调光照(4)；补充放学黄昏暖光 (`ext_aw_light_warm_golden_hour`)。 |
| **office** | `studio_commercial`, `cinematic_dark`, `japanese_fresh` | `prof_light_fluorescent: 4`<br>`lighting_studio_commercial: 4`<br>`prof_light_led_panel: 2`<br>`lighting_japanese_fresh: 1` | 彻底剔除暗夜电影光；补充写字楼真实荧光顶灯 (`prof_light_fluorescent`) 与 LED 平板灯。 |
| **medical** | `studio_commercial`, `japanese_fresh`, `backlit_hair` | `prof_light_fluorescent: 4`<br>`prof_light_high_key: 4`<br>`lighting_studio_commercial: 2` | 医院冷白荧光顶灯与明亮高调光；消除逆光发丝干扰。 |
| **onsen_bath** | `onsen_intimate`, `backlit_hair`, `rain_wet` | `lighting_onsen_intimate: 4`<br>`lighting_rain_wet: 4`<br>`prof_light_ambient: 2`<br>`ext_aw_light_candlelight: 2` | 温泉特有私密水汽朦胧光与湿润反光(4)；补充和风烛光暖光。 |
| **bondage_sm** | `cinematic_dark`, `voyeur_private`, `love_hotel_neon` | `lighting_cinematic_dark: 4`<br>`lighting_voyeur_private: 4`<br>`prof_light_low_key: 4`<br>`prof_light_gelled: 2`<br>`lighting_love_hotel_neon: 1` | 电影暗调与窥视侧光(4)；补充低调深邃光 (`prof_light_low_key`) 与彩胶滤镜光。 |
| **traditional** | `oil_classical`, `backlit_hair`, `japanese_fresh` | `lighting_oil_classical: 4`<br>`ext_aw_light_candlelight: 4`<br>`ext_aw_light_warm_golden_hour: 2`<br>`lighting_japanese_fresh: 2` | 古典油画暖光与幽微烛光 (`ext_aw_light_candlelight`)；补充庭院黄金落日光。 |
| **nightlife** | `love_hotel_neon`, `cinematic_dark`, `backlit_hair` | `lighting_love_hotel_neon: 4`<br>`prof_light_tungsten: 4`<br>`lighting_cinematic_dark: 2`<br>`ext_aw_light_direct_flash: 2` | 霓虹氛围光与吧台钨丝灯点光源 (`prof_light_tungsten`)；补充夜场机顶直闪光。 |
| **domestic** | `bedroom_private`, `japanese_fresh`, `backlit_hair` | `lighting_bedroom_private: 4`<br>`ext_aw_light_soft_bounced: 4`<br>`prof_light_practical: 2`<br>`lighting_japanese_fresh: 1` | 卧室私密暖光与柔和反弹漫射光 (`ext_aw_light_soft_bounced`)；补充实体台灯环境光。 |
| **transit** | `voyeur_private`, `japanese_fresh`, `rain_wet` | `prof_light_fluorescent: 4`<br>`lighting_voyeur_private: 2`<br>`lighting_rain_wet: 2`<br>`lighting_japanese_fresh: 1` | 补充车站车厢顶灯荧光日光灯 (`prof_light_fluorescent`)。 |
| **outdoor** | `japanese_fresh`, `backlit_hair`, `rain_wet` | `lighting_japanese_fresh: 4`<br>`ext_aw_light_warm_golden_hour: 4`<br>`prof_light_high_key: 2`<br>`lighting_rain_wet: 2`<br>`lighting_backlit_hair: 1` | 日间自然光与黄金时段斜射日光 (`ext_aw_light_warm_golden_hour`) 作为核心双强关联。 |
| **dining** | `japanese_fresh`, `oil_classical`, `studio_commercial` | `prof_light_tungsten: 4`<br>`ext_aw_light_candlelight: 2`<br>`lighting_oil_classical: 2`<br>`lighting_studio_commercial: 1` | 餐厅餐桌聚光暖调钨丝灯 (`prof_light_tungsten`) 与浪漫烛光。 |
| **adult** | `love_hotel_neon`, `cinematic_dark`, `voyeur_private` | `lighting_voyeur_private: 4`<br>`lighting_love_hotel_neon: 4`<br>`lighting_cinematic_dark: 2`<br>`prof_light_low_key: 2` | 私密窥探弱光与情调霓虹灯光(4)；低调暗光(2)。 |
| **special** | `cinematic_dark`, `voyeur_private`, `love_hotel_neon` | `ext_aw_light_bioluminescent: 4`<br>`prof_light_gelled: 4`<br>`lighting_cinematic_dark: 2`<br>`special_effects_00: 2` | 生物荧光 (`ext_aw_light_bioluminescent`) 与强烈彩胶色光特效 (`prof_light_gelled`)。 |
| **generic** | `japanese_fresh`, `backlit_hair`, `studio_commercial` | `lighting_japanese_fresh: 1`<br>`lighting_studio_commercial: 1`<br>`prof_light_three_point: 1`<br>`ext_aw_light_soft_bounced: 1` | 标准通用三点光与中性商用光平权均分。 |

---

### 3. 其余 12 槽位代表性真实候选调整规约摘要

- **角色 (character, 11)**：校园绑定 `jk_schoolgirl:4`；职场绑定 `female_boss:4, ol_subordinate:4`；医疗绑定 `kind_nurse:4`；餐饮绑定 `french_maid:4`；夜生活绑定 `hostess_cabaret:4`；居家绑定 `married_housewife:4`。
- **道具 (props, 23)**：校园绑定 `student_leather_bag:4, study_stationery_set:4`；医疗绑定 `medical_stethoscope:4`；传统绑定 `oiled_paper_umbrella:4, embroidered_round_fan:4`；酒吧就餐绑定 `wine_glass_goblet:4`；居家绑定 `cute_cat_on_bed:4, fluffy_teddy_bear:4`；街拍与夜生活适配 `digital_camera_in_hand:2`。
- **发型 (hairstyle, 57)**：校园适配学生双马尾/双麻花 (`twintails_high:2, braided_twins:2, bob_cut:2`)；职场适配干练大背头与锁骨发 (`slicked_back:2, collarbone_lob:2`)；传统适配姬发与盘发 (`hime_cut:2, ext_aw_hair_crown_braid_updo:2`)；夜宴适配大波浪卷 (`big_wavy_curls:2`)。
- **妆容 (makeup, 17)**：校园绑定伪素颜 (`school_barely_there:4`)；职场绑定通勤混血立体修容 (`asian_hybrid_contour:4`)；国风传统绑定朱砂古典妆 (`chinese_vermilion:4`)；夜店特种绑定暗黑哥特与烟熏猫眼 (`gothic_dark:4, smoky_cat_eye:4`)；成人私密绑定微醺潮红 (`climax_flush:4`)。
- **液体 (liquids, 7)**：温泉水疗强关联水滴 (`wet_water_drops:4`)；成人私密强关联精油 (`body_oil_lube:4`)；运动关联微汗 (`sweat_glistening:2`)。
- **纹身 (tattoo, 12)**：极道传统关联和风刺青龙纹 (`japanese_irezumi_dragon:4`)；特殊科幻关联条形码 (`barcode_serial_number:4`)；成人私密关联下腹与臀部花卉 (`lewd_womb_pubic:4, rose_flower_hip:4`)。

全部 496 个选择器的详细配置与处置详见 [`docs/m1_selector_attribution_ledger.tsv`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/docs/m1_selector_attribution_ledger.tsv)。
