# ComfyUI-IYKYK M1 196 单元情境亲和矩阵完整审核与处置表 (M1-D2)

- **基线 Commit SHA**：`f1b873c2479c287817f5780991e6621e31b9f848`
- **采样情境集合 (14)**：`school`, `office`, `medical`, `onsen_bath`, `bondage_sm`, `traditional`, `nightlife`, `domestic`, `transit`, `outdoor`, `dining`, `adult`, `special`, `generic`
- **采样槽位集合 (14)**：`clothing`, `character`, `makeup`, `hairstyle`, `jewelry`, `props`, `tattoo`, `liquids`, `pose`, `expression`, `lighting`, `film`, `shot_type`, `camera_angle`
- **总单元格数**：14 情境 × 14 槽位 = **196 单元**
- **数据源绑定**：本表与 [`docs/m1_selector_attribution_ledger.tsv`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/docs/m1_selector_attribution_ledger.tsv) 共享同一 Python SSoT 数据字典，实现**归属与权重 100% 字节级一致，0 处差异，0 个幽灵 ID**。

---

## 一、196 单元处置状态统计摘要

| 处置类型 (Disposition) | 单元格数量 | 占比 | 核心语义说明 |
|---|---|---|---|
| `neutral` (中性无偏置抽样) | 14 | 7.1% | 14 个 `generic` 槽位统一执行 $C(generic, s) = G_s$ 中性抽样，无情境偏置，消除原先仅有少数等权候选导致的局部失真 |
| `propose_expand` (拟扩充候选) | 29 | 14.8% | 针对性扩充高适配度的真实目录选择器，打破服装仅覆盖 28 款、光影仅覆盖 10 款的局限 |
| `propose_adjust_weight` (拟调整权重) | 149 | 76.0% | 消除原矩阵全部候选权重均为 1.0 的均一化缺陷，确立“强关联 4、适配 2、弱关联 1”的区分度分布 |
| `retain_as_is` (保持原样) | 4 | 2.0% | 经逐项语义核验，当前候选与权重已自洽自足，本轮保持不变 |
| **合计** | **196** | **100.0%** | **全量 196 个单元逐格登记完毕，无一遗漏** |

---

## 二、196 单元逐格审核与处置总表

| 情境 (Context) | 采样槽位 (Slot) | 当前基线候选 (Current Baseline) | 拟调整候选与权重 (Proposed Candidates & Weights) | 处置类型 (Disposition) | 调整理由与情境自洽依据 (Rationale) |
|---|---|---|---|---|---|
| `school` | `clothing` | `jk_seifuku`, `blazer_uniform`, `gym_uniform`, `korean_school` | `blazer_uniform: 4`<br>`gym_uniform: 4`<br>`sailor_shirt: 4`<br>`swimsuit_school: 4`<br>`duffel_coat: 2`<br>`sundress_layered: 2`<br>`volleyball_uniform: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `school` | `character` | `jk_schoolgirl`, `strict_teacher` | `jk_schoolgirl: 4`<br>`strict_teacher: 4`<br>`neighbor_girlfriend: 2` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `school` | `makeup` | `natural_pure`, `cute_peachy`, `sweet_peach_milk`, `clear_water_bare` | `clear_water_bare: 4`<br>`cute_peachy: 4`<br>`natural_pure: 4`<br>`pure_desire_white_peach: 2`<br>`sweet_peach_milk: 2` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `school` | `hairstyle` | `twin_tails`, `high_ponytail`, `long_straight_black`, `braided_twins`, `bob_cut` | `bob_cut: 4`<br>`braided_twins: 4`<br>`high_ponytail: 4`<br>`twin_tails: 4`<br>`ext_aw_hair_long_parted_behind_ears: 2`<br>`hair_pageboy: 2`<br>`hime_cut: 2`<br>`long_straight_black: 2` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `school` | `jewelry` | `ribbon_bow`, `gold_collarbone_chain` | `acc_bowtie: 4`<br>`acc_hairband: 4`<br>`acc_hairclip: 4`<br>`acc_necktie: 4`<br>`ribbon_bow: 4` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `school` | `props` | `glasses_reading`, `smartphone_recording`, `sex_toy_vibrator`, `plush_doll_teddy` | `glasses_reading: 4`<br>`digital_camera_in_hand: 2`<br>`digital_camera_record: 2`<br>`fluffy_teddy_bear: 2`<br>`smartphone_recording: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `school` | `tattoo` | *none (neutral)* | `cute_heart_star: 2` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `school` | `liquids` | `sweat_glistening`, `none` | `sweat_glistening: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `school` | `pose` | `sitting`, `standing`, `dynamic` | `sitting: 4`<br>`standing: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `school` | `expression` | `emotion_shy`, `emotion_playful`, `emotion_restrained` | `emotion_happy: 4`<br>`emotion_playful: 4`<br>`emotion_shy: 4`<br>`emotion_amused: 2`<br>`emotion_focused: 2`<br>`emotion_proud: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `school` | `lighting` | `lighting_japanese_fresh`, `lighting_backlit_hair`, `lighting_studio_commercial` | `lighting_japanese_fresh: 4`<br>`prof_light_high_key: 4`<br>`ext_aw_light_warm_golden_hour: 2`<br>`lighting_backlit_hair: 2`<br>`lighting_studio_commercial: 1` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `school` | `film` | `fujifilm_superia_400`, `fujifilm_pro_400h`, `kodak_portra_400` | `fujifilm_superia_400: 4`<br>`fujifilm_pro_400h: 2`<br>`kodak_gold_200: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `school` | `shot_type` | `medium_close_up`, `medium_shot`, `full_body_shot` | `close_up: 2`<br>`medium_close_up: 2`<br>`medium_shot: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `school` | `camera_angle` | `eye_level`, `high_angle`, `over_the_shoulder` | `camera_orientation_frontal: 2`<br>`camera_orientation_three_quarter: 2`<br>`eye_level: 2`<br>`high_angle: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `office` | `clothing` | `ol_suit`, `knit_sweater`, `evening_dress`, `street_casual` | `business_suit: 4`<br>`ol_suit: 4`<br>`pencil_skirt: 4`<br>`lab_coat: 2`<br>`outerwear_coat: 2`<br>`outerwear_overcoat: 2`<br>`trench_coat: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `office` | `character` | `ol_subordinate`, `female_boss`, `strict_teacher` | `female_boss: 4`<br>`ol_subordinate: 4`<br>`strict_teacher: 2` | `propose_adjust_weight` | 保留基线候选集合，将基线均一化的 1.0 权重治理升级为强关联 4 / 适配 2 区分权重。 |
| `office` | `makeup` | `mature_wife`, `sultry_smoky`, `natural_pure`, `asian_hybrid_contour` | `asian_hybrid_contour: 2`<br>`natural_pure: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `office` | `hairstyle` | `low_ponytail`, `collarbone_lob`, `big_wavy_curls`, `messy_bun` | `collarbone_lob: 4`<br>`ext_aw_hair_long_parted_behind_ears: 4`<br>`hair_long_deep_side_part: 4`<br>`low_ponytail: 4`<br>`bob_cut: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `office` | `jewelry` | `pearl_necklace`, `gold_collarbone_chain`, `leather_choker` | `acc_necktie: 4`<br>`ext_aw_jewelry_pearl_drop_earrings: 2`<br>`pearl_necklace: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `office` | `props` | `glasses_reading`, `wine_glass_bottle`, `smartphone_recording`, `digital_camera_record` | `fashion_baguette_bag: 4`<br>`glasses_reading: 4`<br>`camera_tripod_flash: 2`<br>`fashion_clutch_bag: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `office` | `tattoo` | *none (neutral)* | *none (中性抽样)* | `retain_as_is` | 基线候选与权重已完全自洽，继续保持原样。 |
| `office` | `liquids` | `sweat_glistening`, `none` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `office` | `pose` | `sitting`, `standing` | `sitting: 4`<br>`standing: 2` | `propose_adjust_weight` | 保留基线候选集合，将基线均一化的 1.0 权重治理升级为强关联 4 / 适配 2 区分权重。 |
| `office` | `expression` | `emotion_restrained`, `emotion_detached`, `emotion_seductive` | `emotion_focused: 4`<br>`emotion_proud: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `office` | `lighting` | `lighting_studio_commercial`, `lighting_cinematic_dark`, `lighting_japanese_fresh` | `lighting_studio_commercial: 4`<br>`prof_light_fluorescent: 4`<br>`prof_light_led_panel: 4`<br>`lighting_japanese_fresh: 2`<br>`lighting_cinematic_dark: 1` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `office` | `film` | `fujifilm_classic_chrome`, `kodak_portra_400`, `cinestill_800t` | `kodak_portra_400: 4`<br>`fujifilm_provia_100f: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `office` | `shot_type` | `medium_shot`, `medium_close_up`, `medium_long_shot` | `close_up: 2`<br>`medium_close_up: 2`<br>`medium_shot: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `office` | `camera_angle` | `eye_level`, `low_angle`, `over_the_shoulder` | `camera_orientation_frontal: 2`<br>`camera_orientation_three_quarter: 2`<br>`eye_level: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `medical` | `clothing` | `nurse_uniform` | `lab_coat: 4`<br>`nurse_uniform: 4` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `medical` | `character` | `kind_nurse` | `kind_nurse: 4` | `propose_adjust_weight` | 保留基线候选集合，将基线均一化的 1.0 权重治理升级为强关联 4 / 适配 2 区分权重。 |
| `medical` | `makeup` | `natural_pure`, `cute_peachy`, `clear_water_bare` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `medical` | `hairstyle` | `low_ponytail`, `bob_cut`, `twin_tails`, `messy_bun` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `medical` | `jewelry` | `nurse_cap` | `nurse_cap: 4` | `propose_adjust_weight` | 保留基线候选集合，将基线均一化的 1.0 权重治理升级为强关联 4 / 适配 2 区分权重。 |
| `medical` | `props` | `glasses_reading`, `smartphone_recording` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `medical` | `tattoo` | *none (neutral)* | *none (中性抽样)* | `retain_as_is` | 基线候选与权重已完全自洽，继续保持原样。 |
| `medical` | `liquids` | `sweat_glistening`, `body_oil_lube` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `medical` | `pose` | `standing`, `sitting`, `lying` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `medical` | `expression` | `emotion_shy`, `emotion_submissive`, `emotion_restrained` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `medical` | `lighting` | `lighting_studio_commercial`, `lighting_japanese_fresh`, `lighting_backlit_hair` | `lighting_studio_commercial: 4`<br>`prof_light_fluorescent: 4`<br>`prof_light_led_panel: 4`<br>`lighting_backlit_hair: 2`<br>`lighting_japanese_fresh: 1` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `medical` | `film` | `kodak_max_400`, `kodak_portra_400`, `fujifilm_pro_400h` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `medical` | `shot_type` | `medium_shot`, `medium_close_up`, `full_body_shot` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `medical` | `camera_angle` | `eye_level`, `high_angle` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `onsen_bath` | `clothing` | `yukata`, `kimono`, `one_piece_swimsuit`, `bikini_micro` | `legwear_bare: 4`<br>`yukata: 4`<br>`kimono: 2`<br>`one_piece_swimsuit: 2`<br>`swimsuit_classic: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `onsen_bath` | `character` | `married_housewife`, `neighbor_girlfriend`, `gravure_idol` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `onsen_bath` | `makeup` | `wet_dewy`, `natural_pure`, `climax_flush`, `drunken_milky_youth` | `wet_dewy: 4`<br>`clear_water_bare: 2`<br>`climax_flush: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `onsen_bath` | `hairstyle` | `wet_hair_face`, `messy_bun`, `low_ponytail` | `wet_hair_face: 4` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `onsen_bath` | `jewelry` | `gold_collarbone_chain`, `pearl_necklace`, `ankle_bracelet` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `onsen_bath` | `props` | `wine_glass_bottle`, `ice_cubes`, `rose_petals_candles` | `rose_petals_candles: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `onsen_bath` | `tattoo` | *none (neutral)* | `rose_flower_hip: 2` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `onsen_bath` | `liquids` | `wet_water_drops`, `sweat_glistening` | `body_oil_lube: 4`<br>`wet_water_drops: 4` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `onsen_bath` | `pose` | `sitting`, `standing`, `lying`, `kneeling` | `gestures_self_touch: 2`<br>`lying: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `onsen_bath` | `expression` | `emotion_pleasure`, `emotion_shy`, `emotion_dazed` | `emotion_blissful: 4`<br>`emotion_relaxed: 4`<br>`emotion_dazed: 2`<br>`emotion_pleasure: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `onsen_bath` | `lighting` | `lighting_onsen_intimate`, `lighting_backlit_hair`, `lighting_rain_wet` | `lighting_onsen_intimate: 4`<br>`lighting_rain_wet: 4`<br>`lighting_backlit_hair: 2`<br>`professional_lighting_14: 2` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `onsen_bath` | `film` | `kodak_portra_400`, `fujifilm_pro_400h`, `kodak_gold_200` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `onsen_bath` | `shot_type` | `medium_shot`, `full_body_shot`, `medium_close_up` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `onsen_bath` | `camera_angle` | `eye_level`, `high_angle`, `overhead_birds_eye` | `high_angle: 2`<br>`overhead_birds_eye: 2`<br>`voyeur_peeping: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `bondage_sm` | `clothing` | `latex_catsuit`, `leather_corset`, `lingerie_lace` | `latex_catsuit: 4`<br>`leotard_bodysuit: 4`<br>`zentai_suit: 4` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `bondage_sm` | `character` | `french_maid`, `ol_subordinate`, `female_boss` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `bondage_sm` | `makeup` | `submissive_marked`, `ruined_crying`, `climax_flush`, `gothic_dark` | `gothic_dark: 4`<br>`ruined_crying: 4`<br>`submissive_marked: 4`<br>`climax_flush: 2` | `propose_adjust_weight` | 保留基线候选集合，将基线均一化的 1.0 权重治理升级为强关联 4 / 适配 2 区分权重。 |
| `bondage_sm` | `hairstyle` | `messy_bedhead`, `twin_tails`, `hair_in_mouth` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `bondage_sm` | `jewelry` | `leather_choker`, `lace_blindfold`, `nipple_rings`, `body_chain` | `combo_necklace_choker: 4`<br>`lace_blindfold: 4`<br>`leather_choker: 4`<br>`nipple_rings: 4` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `bondage_sm` | `props` | `bondage_rope_collar`, `sex_toy_vibrator`, `ice_cubes` | `bondage_rope_collar: 4`<br>`sex_toy_vibrator: 4` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `bondage_sm` | `tattoo` | `lewd_womb_pubic`, `barcode_serial_number`, `tally_marks_inner_thigh` | `tally_marks_inner_thigh: 4`<br>`barcode_serial_number: 2`<br>`japanese_irezumi_dragon: 2`<br>`lewd_womb_pubic: 2`<br>`snake_coiling: 2`<br>`underbust_mandala: 2` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `bondage_sm` | `liquids` | `saliva_drool`, `sweat_glistening`, `cum_splatter`, `body_oil_lube` | `body_oil_lube: 2`<br>`cum_splatter: 2`<br>`pussy_juice: 2`<br>`saliva_drool: 2`<br>`sweat_glistening: 2` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `bondage_sm` | `pose` | `kneeling`, `all_fours`, `lying`, `teasing` | `all_fours: 4`<br>`kneeling: 4`<br>`restraints_holds: 4` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `bondage_sm` | `expression` | `emotion_submissive`, `emotion_pain`, `emotion_dazed` | `emotion_fear: 4`<br>`emotion_pain: 4`<br>`emotion_restrained: 4`<br>`emotion_submissive: 4` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `bondage_sm` | `lighting` | `lighting_cinematic_dark`, `lighting_voyeur_private`, `lighting_love_hotel_neon` | `lighting_cinematic_dark: 4`<br>`lighting_voyeur_private: 4`<br>`prof_light_low_key: 4`<br>`lighting_love_hotel_neon: 2` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `bondage_sm` | `film` | `cinestill_800t`, `kodak_trix_400`, `ilford_hp5_plus` | `ilford_hp5_plus: 4`<br>`kodak_trix_400: 4`<br>`fujifilm_acros_100: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `bondage_sm` | `shot_type` | `medium_shot`, `full_body_shot`, `erotic_close_up` | `erotic_close_up: 4`<br>`extreme_close_up: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `bondage_sm` | `camera_angle` | `high_angle`, `low_angle`, `pov_first_person`, `dutch_angle` | `between_legs_angle: 2`<br>`pov_first_person: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `traditional` | `clothing` | `kimono`, `yukata`, `furisode`, `qipao`, `hanfu`, `modern_chinese`, `hanbok` | `dirndl_dress: 4`<br>`kimono: 4`<br>`qipao: 4`<br>`yukata: 4` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `traditional` | `character` | `married_housewife`, `neighbor_girlfriend` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `traditional` | `makeup` | `vintage_retro`, `chinese_vermilion`, `natural_pure`, `mature_wife` | `chinese_vermilion: 4`<br>`vintage_retro: 4` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `traditional` | `hairstyle` | `long_straight_black`, `hime_cut`, `low_ponytail`, `braided_twins` | `ext_aw_hair_low_nape_chignon: 4`<br>`hime_cut: 4`<br>`ext_aw_hair_vintage_finger_waves: 2`<br>`hair_pageboy: 2`<br>`long_straight_black: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `traditional` | `jewelry` | `pearl_necklace`, `gold_collarbone_chain`, `ribbon_bow` | `fur_shawl: 2`<br>`pearl_necklace: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `traditional` | `props` | `oriental_fan_umbrella`, `wine_glass_bottle`, `flower_bouquet_petals` | `embroidered_round_fan: 4`<br>`oil_paper_umbrella: 4`<br>`oriental_fan_umbrella: 4`<br>`flower_bouquet_petals: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `traditional` | `tattoo` | `japanese_irezumi_dragon`, `cherry_blossom_shoulder` | `cherry_blossom_shoulder: 4`<br>`japanese_irezumi_dragon: 4`<br>`spine_vertical_script: 2` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `traditional` | `liquids` | `sweat_glistening`, `none` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `traditional` | `pose` | `kneeling`, `sitting`, `standing` | `kneeling: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `traditional` | `expression` | `emotion_shy`, `emotion_restrained`, `emotion_seductive` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `traditional` | `lighting` | `lighting_oil_classical`, `lighting_backlit_hair`, `lighting_japanese_fresh` | `ext_aw_light_candlelight: 4`<br>`lighting_oil_classical: 4`<br>`lighting_backlit_hair: 2`<br>`lighting_japanese_fresh: 2`<br>`professional_lighting_15: 2` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `traditional` | `film` | `fujifilm_acros_100`, `kodak_portra_400`, `kodak_gold_200` | `fujifilm_acros_100: 4`<br>`fujifilm_provia_100f: 2`<br>`ilford_hp5_plus: 2`<br>`kodak_trix_400: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `traditional` | `shot_type` | `medium_shot`, `full_body_shot`, `medium_close_up` | `full_body_shot: 2`<br>`medium_long_shot: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `traditional` | `camera_angle` | `eye_level`, `high_angle`, `over_the_shoulder` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `nightlife` | `clothing` | `bunny_suit`, `party_club`, `lingerie_lace`, `latex_catsuit`, `evening_dress` | `bunny_suit: 4`<br>`cocktail_dress: 4`<br>`dress_backless: 4`<br>`evening_dress: 4` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `nightlife` | `character` | `hostess_cabaret`, `gravure_idol` | `hostess_cabaret: 4` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `nightlife` | `makeup` | `sultry_smoky`, `climax_flush`, `ruined_crying`, `gothic_dark` | `drunken_milky_youth: 4`<br>`sultry_smoky: 4`<br>`asian_hybrid_contour: 2`<br>`gothic_dark: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `nightlife` | `hairstyle` | `big_wavy_curls`, `twin_tails`, `hime_cut`, `hair_in_mouth` | `big_wavy_curls: 4`<br>`ext_aw_hair_vintage_finger_waves: 4`<br>`hair_1940s_waves: 4`<br>`hair_long_deep_side_part: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `nightlife` | `jewelry` | `leather_choker`, `body_chain`, `bunny_ears`, `cat_ears` | `bunny_ears: 4`<br>`fur_shawl: 4`<br>`cat_ears: 2`<br>`combo_necklace_choker: 2`<br>`ext_aw_jewelry_silver_hoop_earrings: 2`<br>`gold_collarbone_chain: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `nightlife` | `props` | `wine_glass_bottle`, `sex_toy_vibrator`, `smartphone_recording`, `digital_camera_record` | `fashion_clutch_bag: 4`<br>`wine_glass_bottle: 4`<br>`smartphone_recording: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `nightlife` | `tattoo` | *none (neutral)* | `butterfly_lower_back: 2`<br>`snake_coiling: 2` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `nightlife` | `liquids` | `sweat_glistening`, `saliva_drool` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `nightlife` | `pose` | `standing`, `dynamic`, `teasing`, `sitting` | `teasing: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `nightlife` | `expression` | `emotion_seductive`, `emotion_pleasure`, `emotion_playful` | `emotion_seductive: 4`<br>`emotion_yearning: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `nightlife` | `lighting` | `lighting_love_hotel_neon`, `lighting_cinematic_dark`, `lighting_backlit_hair` | `ext_aw_light_direct_flash: 4`<br>`lighting_love_hotel_neon: 4`<br>`prof_light_gelled: 4`<br>`prof_light_tungsten: 4`<br>`lighting_cinematic_dark: 2`<br>`lighting_backlit_hair: 1` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `nightlife` | `film` | `cinestill_800t`, `fujifilm_classic_negative`, `lomography_color` | `cinestill_800t: 4`<br>`expired_film: 2`<br>`film_stock_polaroid: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `nightlife` | `shot_type` | `medium_shot`, `full_body_shot`, `medium_close_up` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `nightlife` | `camera_angle` | `low_angle`, `dutch_angle`, `eye_level` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `domestic` | `clothing` | `silk_robe`, `camisole_slip`, `knit_sweater`, `street_casual`, `lingerie_lace` | `apron_dress: 4`<br>`silk_robe: 4`<br>`waist_apron: 4`<br>`legwear_bare: 2`<br>`lingerie_bra: 2`<br>`lingerie_lace: 2`<br>`lingerie_panties: 2`<br>`maid_dress: 2`<br>`summer_sundress: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `domestic` | `character` | `married_housewife`, `neighbor_girlfriend` | `married_housewife: 4`<br>`french_maid: 2`<br>`gravure_idol: 2`<br>`neighbor_girlfriend: 2` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `domestic` | `makeup` | `mature_wife`, `natural_pure`, `climax_flush`, `pure_desire_white_peach` | `clear_water_bare: 4`<br>`mature_wife: 4`<br>`natural_pure: 2`<br>`pure_desire_white_peach: 2`<br>`sweet_peach_milk: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `domestic` | `hairstyle` | `messy_bedhead`, `messy_bun`, `long_straight_black`, `hair_over_breast` | `messy_bedhead: 4`<br>`messy_bun: 4`<br>`ext_aw_hair_low_nape_chignon: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `domestic` | `jewelry` | `gold_collarbone_chain`, `ribbon_bow`, `ankle_bracelet` | `acc_hairband: 2`<br>`acc_hairclip: 2`<br>`cat_ears: 2`<br>`maid_headdress: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `domestic` | `props` | `cute_cat_on_bed`, `pillow_clutching`, `game_controller`, `wine_glass_bottle`, `rose_petals_candles` | `cute_cat_on_bed: 4`<br>`fluffy_teddy_bear: 4`<br>`game_controller: 4`<br>`pillow_clutching: 4`<br>`plush_doll_teddy: 4`<br>`embroidered_round_fan: 2`<br>`flower_bouquet_petals: 2`<br>`fresh_flower_bouquet: 2`<br>`glasses_reading: 2`<br>`rose_petals_candles: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `domestic` | `tattoo` | *none (neutral)* | `cute_heart_star: 2`<br>`spine_vertical_script: 2` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `domestic` | `liquids` | `sweat_glistening`, `saliva_drool` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `domestic` | `pose` | `lying`, `sitting`, `standing`, `teasing` | `lying: 4` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `domestic` | `expression` | `emotion_shy`, `emotion_seductive`, `emotion_dazed` | `emotion_relaxed: 4`<br>`emotion_blissful: 2`<br>`emotion_dazed: 2`<br>`emotion_happy: 2`<br>`emotion_playful: 2`<br>`emotion_shy: 2`<br>`emotion_tired: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `domestic` | `lighting` | `lighting_bedroom_private`, `lighting_japanese_fresh`, `lighting_backlit_hair` | `ext_aw_light_soft_bounced: 4`<br>`lighting_bedroom_private: 4`<br>`prof_light_practical: 4`<br>`lighting_japanese_fresh: 2`<br>`lighting_backlit_hair: 1` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `domestic` | `film` | `kodak_portra_400`, `kodak_gold_200`, `fujifilm_pro_400h` | `film_stock_polaroid: 4`<br>`expired_film: 2`<br>`kodak_gold_200: 2`<br>`kodak_portra_400: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `domestic` | `shot_type` | `medium_close_up`, `medium_shot`, `full_body_shot` | `close_up: 2`<br>`medium_close_up: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `domestic` | `camera_angle` | `eye_level`, `high_angle`, `over_the_shoulder` | `camera_orientation_frontal: 2`<br>`camera_orientation_three_quarter: 2`<br>`eye_level: 2`<br>`high_angle: 2`<br>`overhead_birds_eye: 2`<br>`pov_first_person: 2`<br>`voyeur_peeping: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `transit` | `clothing` | `jk_seifuku`, `blazer_uniform`, `ol_suit`, `street_casual`, `knit_sweater` | `duffel_coat: 4`<br>`outerwear_coat: 4`<br>`outerwear_overcoat: 4`<br>`rainwear_coat: 4`<br>`trench_coat: 4`<br>`business_suit: 2`<br>`ol_suit: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `transit` | `character` | `jk_schoolgirl`, `ol_subordinate`, `neighbor_girlfriend` | `female_boss: 2`<br>`jk_schoolgirl: 2`<br>`ol_subordinate: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `transit` | `makeup` | `natural_pure`, `cute_peachy`, `climax_flush` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `transit` | `hairstyle` | `low_ponytail`, `high_ponytail`, `long_straight_black`, `collarbone_lob` | `collarbone_lob: 2`<br>`low_ponytail: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `transit` | `jewelry` | `gold_collarbone_chain`, `ribbon_bow` | `winter_scarf: 4`<br>`ext_aw_jewelry_silver_hoop_earrings: 2`<br>`poncho: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `transit` | `props` | `smartphone_recording`, `glasses_reading` | `fashion_baguette_bag: 4`<br>`smartphone_recording: 4`<br>`digital_camera_in_hand: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `transit` | `tattoo` | *none (neutral)* | *none (中性抽样)* | `retain_as_is` | 基线候选与权重已完全自洽，继续保持原样。 |
| `transit` | `liquids` | `sweat_glistening`, `none` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `transit` | `pose` | `standing`, `sitting`, `dynamic` | `standing: 4`<br>`sitting: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `transit` | `expression` | `emotion_restrained`, `emotion_shy`, `emotion_fear` | `emotion_tired: 4`<br>`emotion_dazed: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `transit` | `lighting` | `lighting_voyeur_private`, `lighting_japanese_fresh`, `lighting_rain_wet` | `ext_aw_light_direct_flash: 4`<br>`lighting_rain_wet: 4`<br>`lighting_onsen_intimate: 2`<br>`lighting_voyeur_private: 2`<br>`prof_light_fluorescent: 2`<br>`prof_light_led_panel: 2`<br>`lighting_japanese_fresh: 1` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `transit` | `film` | `fujifilm_classic_chrome`, `fujifilm_superia_400`, `kodak_max_400` | `cinestill_800t: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `transit` | `shot_type` | `medium_shot`, `full_body_shot`, `medium_close_up` | `full_body_shot: 4`<br>`medium_long_shot: 2`<br>`medium_shot: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `transit` | `camera_angle` | `eye_level`, `voyeur_peeping`, `over_the_shoulder` | `low_angle: 2`<br>`voyeur_peeping: 2`<br>`worms_eye: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `outdoor` | `clothing` | `bikini_micro`, `one_piece_swimsuit`, `street_casual`, `gym_uniform`, `cheerleader` | `one_piece_swimsuit: 4`<br>`summer_sundress: 4`<br>`sundress_layered: 4`<br>`swimsuit_classic: 4`<br>`swimsuit_competition: 4`<br>`swimsuit_creative: 4`<br>`volleyball_uniform: 4`<br>`duffel_coat: 2`<br>`gym_uniform: 2`<br>`rainwear_coat: 2`<br>`swimsuit_school: 2`<br>`tactical_jumpsuit: 2`<br>`trench_coat: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `outdoor` | `character` | `gravure_idol`, `neighbor_girlfriend` | `gravure_idol: 2`<br>`neighbor_girlfriend: 2` | `propose_adjust_weight` | 保留基线候选集合，将基线均一化的 1.0 权重治理升级为强关联 4 / 适配 2 区分权重。 |
| `outdoor` | `makeup` | `natural_pure`, `cute_peachy`, `wet_dewy`, `sun_kissed` | `sun_kissed: 4`<br>`cute_peachy: 2`<br>`wet_dewy: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `outdoor` | `hairstyle` | `high_ponytail`, `twin_tails`, `space_buns`, `messy_bun` | `high_ponytail: 4`<br>`braided_twins: 2`<br>`hair_pixie_side_swept: 2`<br>`space_buns: 2`<br>`twin_tails: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `outdoor` | `jewelry` | `ribbon_bow`, `gold_collarbone_chain`, `ankle_bracelet` | `poncho: 4`<br>`winter_scarf: 4`<br>`body_chain: 2`<br>`hooded_cloak: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `outdoor` | `props` | `camera_tripod_flash`, `oriental_fan_umbrella`, `smartphone_recording`, `flower_bouquet_petals` | `digital_camera_in_hand: 4`<br>`digital_camera_record: 4`<br>`fresh_flower_bouquet: 2`<br>`oil_paper_umbrella: 2`<br>`oriental_fan_umbrella: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `outdoor` | `tattoo` | *none (neutral)* | `cherry_blossom_shoulder: 2` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `outdoor` | `liquids` | `wet_water_drops`, `sweat_glistening` | `sweat_glistening: 4`<br>`wet_water_drops: 2` | `propose_adjust_weight` | 保留基线候选集合，将基线均一化的 1.0 权重治理升级为强关联 4 / 适配 2 区分权重。 |
| `outdoor` | `pose` | `dynamic`, `standing`, `sitting` | `dynamic: 4`<br>`standing: 4`<br>`combat_action: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `outdoor` | `expression` | `emotion_playful`, `emotion_shy`, `emotion_seductive` | `emotion_happy: 4`<br>`emotion_amused: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `outdoor` | `lighting` | `lighting_japanese_fresh`, `lighting_backlit_hair`, `lighting_rain_wet` | `ext_aw_light_warm_golden_hour: 4`<br>`lighting_japanese_fresh: 4`<br>`prof_light_high_key: 4`<br>`professional_lighting_14: 4`<br>`professional_lighting_15: 4`<br>`lighting_rain_wet: 2`<br>`lighting_backlit_hair: 1` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `outdoor` | `film` | `kodak_ektar_100`, `fujifilm_velvia_50`, `cinestill_50d` | `fujifilm_pro_400h: 4`<br>`fujifilm_superia_400: 4`<br>`kodak_gold_200: 4`<br>`fujifilm_provia_100f: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `outdoor` | `shot_type` | `full_body_shot`, `medium_long_shot`, `medium_shot` | `full_body_shot: 4`<br>`medium_long_shot: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `outdoor` | `camera_angle` | `low_angle`, `eye_level`, `high_angle` | `low_angle: 4`<br>`worms_eye: 4` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `dining` | `clothing` | `maid_dress`, `waitress_uniform`, `street_casual`, `qipao`, `kimono` | `evening_dress: 4`<br>`fast_food_uniform: 4`<br>`maid_dress: 4`<br>`tailcoat: 4`<br>`waitress_uniform: 4`<br>`cocktail_dress: 2`<br>`dirndl_dress: 2`<br>`qipao: 2`<br>`waist_apron: 2`<br>`wedding_dress: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `dining` | `character` | `french_maid`, `neighbor_girlfriend`, `ol_subordinate`, `married_housewife` | `french_maid: 4`<br>`married_housewife: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `dining` | `makeup` | `cute_peachy`, `natural_pure`, `mature_wife`, `sweet_peach_milk` | `chinese_vermilion: 2`<br>`drunken_milky_youth: 2`<br>`mature_wife: 2`<br>`vintage_retro: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `dining` | `hairstyle` | `messy_bun`, `collarbone_lob`, `twin_tails`, `bob_cut` | `big_wavy_curls: 2`<br>`hair_1940s_waves: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `dining` | `jewelry` | `maid_headdress`, `pearl_necklace`, `gold_collarbone_chain` | `ext_aw_jewelry_pearl_drop_earrings: 4`<br>`maid_headdress: 4`<br>`pearl_necklace: 4`<br>`acc_bowtie: 2`<br>`gold_collarbone_chain: 2` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `dining` | `props` | `wine_glass_bottle`, `glasses_reading`, `smartphone_recording` | `fresh_flower_bouquet: 4`<br>`ice_cubes: 4`<br>`wine_glass_bottle: 4`<br>`fashion_clutch_bag: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `dining` | `tattoo` | *none (neutral)* | *none (中性抽样)* | `retain_as_is` | 基线候选与权重已完全自洽，继续保持原样。 |
| `dining` | `liquids` | `none`, `sweat_glistening` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `dining` | `pose` | `sitting`, `standing`, `teasing` | `sitting: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `dining` | `expression` | `emotion_playful`, `emotion_shy`, `emotion_seductive` | `emotion_amused: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `dining` | `lighting` | `lighting_japanese_fresh`, `lighting_oil_classical`, `lighting_studio_commercial` | `ext_aw_light_candlelight: 4`<br>`lighting_oil_classical: 4`<br>`lighting_japanese_fresh: 2`<br>`prof_light_gelled: 2`<br>`prof_light_tungsten: 2`<br>`lighting_studio_commercial: 1` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `dining` | `film` | `kodak_portra_400`, `kodak_gold_200`, `fujifilm_classic_chrome` | `kodak_portra_400: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `dining` | `shot_type` | `medium_shot`, `medium_close_up`, `full_body_shot` | `medium_close_up: 2`<br>`medium_shot: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `dining` | `camera_angle` | `eye_level`, `over_the_shoulder`, `high_angle` | `camera_orientation_three_quarter: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `adult` | `clothing` | `bunny_suit`, `maid_dress`, `latex_catsuit`, `leather_corset`, `bikini_micro`, `lingerie_lace` | `legwear_bare: 4`<br>`lingerie_bra: 4`<br>`lingerie_lace: 4`<br>`lingerie_panties: 4`<br>`apron_dress: 2`<br>`bunny_suit: 2`<br>`dress_backless: 2`<br>`latex_catsuit: 2`<br>`leotard_bodysuit: 2`<br>`one_piece_swimsuit: 2`<br>`slime_dress: 2`<br>`swimsuit_classic: 2`<br>`swimsuit_creative: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `adult` | `character` | `hostess_cabaret`, `gravure_idol`, `french_maid` | `gravure_idol: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `adult` | `makeup` | `sultry_smoky`, `climax_flush`, `ruined_crying`, `submissive_marked` | `climax_flush: 4`<br>`pure_desire_white_peach: 2`<br>`ruined_crying: 2`<br>`submissive_marked: 2`<br>`sultry_smoky: 2` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `adult` | `hairstyle` | `twin_tails`, `big_wavy_curls`, `wet_hair_face`, `hair_in_mouth` | `wet_hair_face: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `adult` | `jewelry` | `bunny_ears`, `cat_ears`, `maid_headdress`, `lace_blindfold`, `leather_choker`, `nipple_rings`, `body_chain` | `body_chain: 4`<br>`lace_blindfold: 4`<br>`nipple_rings: 4`<br>`bunny_ears: 2`<br>`leather_choker: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `adult` | `props` | `sex_toy_vibrator`, `camera_tripod_flash`, `bondage_rope_collar`, `ice_cubes` | `bondage_rope_collar: 4`<br>`rose_petals_candles: 4`<br>`sex_toy_vibrator: 4`<br>`flower_bouquet_petals: 2`<br>`ice_cubes: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `adult` | `tattoo` | `lewd_womb_pubic`, `barcode_serial_number`, `tally_marks_inner_thigh`, `butterfly_lower_back` | `lewd_womb_pubic: 4`<br>`tally_marks_inner_thigh: 4`<br>`butterfly_lower_back: 2`<br>`rose_flower_hip: 2`<br>`underbust_mandala: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `adult` | `liquids` | `pussy_juice`, `cum_splatter`, `body_oil_lube`, `saliva_drool` | `body_oil_lube: 4`<br>`cum_splatter: 4`<br>`pussy_juice: 4`<br>`saliva_drool: 2` | `propose_adjust_weight` | 保留基线候选集合，将基线均一化的 1.0 权重治理升级为强关联 4 / 适配 2 区分权重。 |
| `adult` | `pose` | `teasing`, `kneeling`, `all_fours`, `lying` | `gestures_self_touch: 4`<br>`restraints_holds: 4`<br>`teasing: 4`<br>`all_fours: 2`<br>`kneeling: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `adult` | `expression` | `emotion_seductive`, `emotion_pleasure`, `emotion_submissive` | `emotion_pleasure: 4`<br>`emotion_seductive: 4`<br>`emotion_submissive: 4`<br>`emotion_yearning: 4`<br>`emotion_blissful: 2`<br>`emotion_pain: 2`<br>`emotion_restrained: 2` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `adult` | `lighting` | `lighting_love_hotel_neon`, `lighting_cinematic_dark`, `lighting_voyeur_private` | `lighting_cinematic_dark: 4`<br>`lighting_love_hotel_neon: 4`<br>`lighting_voyeur_private: 4`<br>`prof_light_low_key: 4` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `adult` | `film` | `cinestill_800t`, `lomography_color`, `expired_film` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `adult` | `shot_type` | `erotic_close_up`, `close_up`, `medium_close_up` | `erotic_close_up: 4`<br>`extreme_close_up: 4` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `adult` | `camera_angle` | `pov_first_person`, `between_legs_angle`, `high_angle`, `low_angle` | `between_legs_angle: 4`<br>`pov_first_person: 4` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `special` | `clothing` | `latex_catsuit`, `leather_corset`, `lingerie_lace` | `armored_dress: 4`<br>`cyberpunk_plugsuit: 4`<br>`cyberpunk_tactical_bodysuit: 4`<br>`slime_dress: 4`<br>`tactical_jumpsuit: 4`<br>`wedding_dress: 4`<br>`bunny_suit: 2`<br>`latex_catsuit: 2`<br>`tailcoat: 2`<br>`zentai_suit: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `special` | `character` | `female_boss`, `kind_nurse`, `strict_teacher` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `special` | `makeup` | `submissive_marked`, `sultry_smoky`, `ruined_crying`, `gothic_dark` | `gothic_dark: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `special` | `hairstyle` | `hime_cut`, `long_straight_black`, `messy_bedhead` | `space_buns: 4`<br>`hair_pixie_side_swept: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `special` | `jewelry` | `leather_choker`, `lace_blindfold`, `body_chain` | `cat_ears: 4`<br>`cloak: 4`<br>`hooded_cloak: 4`<br>`bunny_ears: 2`<br>`ribbon_bow: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `special` | `props` | `bondage_rope_collar`, `camera_tripod_flash`, `sex_toy_vibrator` | `camera_tripod_flash: 4` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `special` | `tattoo` | `barcode_serial_number`, `snake_coiling`, `spine_vertical_script` | `barcode_serial_number: 4` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `special` | `liquids` | `saliva_drool`, `sweat_glistening`, `cum_splatter` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `special` | `pose` | `standing`, `lying`, `kneeling`, `teasing` | `combat_action: 4`<br>`dynamic: 4` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `special` | `expression` | `emotion_detached`, `emotion_dazed`, `emotion_fear`, `emotion_contrast` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `special` | `lighting` | `lighting_cinematic_dark`, `lighting_voyeur_private`, `lighting_love_hotel_neon` | `ext_aw_light_bioluminescent: 4`<br>`special_effects_00: 4`<br>`special_effects_01: 4`<br>`special_effects_02: 4`<br>`special_effects_03: 4`<br>`lighting_cinematic_dark: 2`<br>`lighting_love_hotel_neon: 2`<br>`lighting_voyeur_private: 2` | `propose_expand` | 在 baseline 候选基础上语义扩充高适配真实选择器，丰富情境抽样多样性，并按 4/2/1 实施差异化权重。 |
| `special` | `film` | `kodak_tmax_400`, `kodak_trix_400`, `cinestill_800t`, `lomography_color` | `expired_film: 2` | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `special` | `shot_type` | `medium_shot`, `full_body_shot`, `close_up` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `special` | `camera_angle` | `dutch_angle`, `voyeur_peeping`, `overhead_birds_eye`, `pov_first_person` | *none (中性抽样)* | `propose_adjust_weight` | 精简脱敏不适条目，保留并优化强契合候选的梯度权重分布。 |
| `generic` | `clothing` | `street_casual`, `knit_sweater`, `camisole_slip`, `silk_robe` | *none (中性抽样 $C(generic, s) = G_s$)* | `neutral` | 14 个 generic 单元统一明确为对槽位全部合法候选中性无偏置抽样，消除原先仅有少数等权候选导致的非预期偏置，保证与全局池 $G_s$ 严格自洽。 |
| `generic` | `character` | `neighbor_girlfriend`, `married_housewife`, `jk_schoolgirl` | *none (中性抽样 $C(generic, s) = G_s$)* | `neutral` | 14 个 generic 单元统一明确为对槽位全部合法候选中性无偏置抽样，消除原先仅有少数等权候选导致的非预期偏置，保证与全局池 $G_s$ 严格自洽。 |
| `generic` | `makeup` | `natural_pure`, `cute_peachy`, `mature_wife`, `pure_desire_white_peach` | *none (中性抽样 $C(generic, s) = G_s$)* | `neutral` | 14 个 generic 单元统一明确为对槽位全部合法候选中性无偏置抽样，消除原先仅有少数等权候选导致的非预期偏置，保证与全局池 $G_s$ 严格自洽。 |
| `generic` | `hairstyle` | `long_straight_black`, `high_ponytail`, `low_ponytail`, `collarbone_lob` | *none (中性抽样 $C(generic, s) = G_s$)* | `neutral` | 14 个 generic 单元统一明确为对槽位全部合法候选中性无偏置抽样，消除原先仅有少数等权候选导致的非预期偏置，保证与全局池 $G_s$ 严格自洽。 |
| `generic` | `jewelry` | `gold_collarbone_chain`, `ribbon_bow`, `pearl_necklace` | *none (中性抽样 $C(generic, s) = G_s$)* | `neutral` | 14 个 generic 单元统一明确为对槽位全部合法候选中性无偏置抽样，消除原先仅有少数等权候选导致的非预期偏置，保证与全局池 $G_s$ 严格自洽。 |
| `generic` | `props` | `smartphone_recording`, `cute_cat_on_bed`, `pillow_clutching` | *none (中性抽样 $C(generic, s) = G_s$)* | `neutral` | 14 个 generic 单元统一明确为对槽位全部合法候选中性无偏置抽样，消除原先仅有少数等权候选导致的非预期偏置，保证与全局池 $G_s$ 严格自洽。 |
| `generic` | `tattoo` | *none (neutral)* | *none (中性抽样 $C(generic, s) = G_s$)* | `neutral` | 14 个 generic 单元统一明确为对槽位全部合法候选中性无偏置抽样，消除原先仅有少数等权候选导致的非预期偏置，保证与全局池 $G_s$ 严格自洽。 |
| `generic` | `liquids` | `none`, `sweat_glistening` | *none (中性抽样 $C(generic, s) = G_s$)* | `neutral` | 14 个 generic 单元统一明确为对槽位全部合法候选中性无偏置抽样，消除原先仅有少数等权候选导致的非预期偏置，保证与全局池 $G_s$ 严格自洽。 |
| `generic` | `pose` | `standing`, `sitting`, `dynamic`, `lying` | *none (中性抽样 $C(generic, s) = G_s$)* | `neutral` | 14 个 generic 单元统一明确为对槽位全部合法候选中性无偏置抽样，消除原先仅有少数等权候选导致的非预期偏置，保证与全局池 $G_s$ 严格自洽。 |
| `generic` | `expression` | `emotion_shy`, `emotion_playful`, `emotion_restrained` | *none (中性抽样 $C(generic, s) = G_s$)* | `neutral` | 14 个 generic 单元统一明确为对槽位全部合法候选中性无偏置抽样，消除原先仅有少数等权候选导致的非预期偏置，保证与全局池 $G_s$ 严格自洽。 |
| `generic` | `lighting` | `lighting_japanese_fresh`, `lighting_backlit_hair`, `lighting_studio_commercial` | *none (中性抽样 $C(generic, s) = G_s$)* | `neutral` | 14 个 generic 单元统一明确为对槽位全部合法候选中性无偏置抽样，消除原先仅有少数等权候选导致的非预期偏置，保证与全局池 $G_s$ 严格自洽。 |
| `generic` | `film` | `kodak_max_400`, `fujifilm_superia_400`, `kodak_portra_400` | *none (中性抽样 $C(generic, s) = G_s$)* | `neutral` | 14 个 generic 单元统一明确为对槽位全部合法候选中性无偏置抽样，消除原先仅有少数等权候选导致的非预期偏置，保证与全局池 $G_s$ 严格自洽。 |
| `generic` | `shot_type` | `medium_shot`, `medium_close_up`, `full_body_shot` | *none (中性抽样 $C(generic, s) = G_s$)* | `neutral` | 14 个 generic 单元统一明确为对槽位全部合法候选中性无偏置抽样，消除原先仅有少数等权候选导致的非预期偏置，保证与全局池 $G_s$ 严格自洽。 |
| `generic` | `camera_angle` | `eye_level`, `high_angle`, `over_the_shoulder` | *none (中性抽样 $C(generic, s) = G_s$)* | `neutral` | 14 个 generic 单元统一明确为对槽位全部合法候选中性无偏置抽样，消除原先仅有少数等权候选导致的非预期偏置，保证与全局池 $G_s$ 严格自洽。 |
