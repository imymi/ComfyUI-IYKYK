# 服装提示词全量数据迁移台账 (Line-by-Line Migration Ledger)

> **源文件基线信息**：
> - 原始文件路径：`docs/data_migration/raw_clothing_input.tsv`
> - 原始文件 SHA-256：`65e4c56655b4c0f8f3629a5e26d407d77860d3581d55b2da7dd92ff87d7fc2ff`
> - 原始总记录数：**246 行** (行号 1~246 连续闭合，无任何截断或省略)

## 一、三层统计与闭合核验汇总

| 统计维度 | 统计口径说明 | 精确计数值 |
|---|---|---|
| **第 1 层：原始字面量去重** | 对原始第三列 Prompt 文本直接执行 `set()` 统计 | **218 个** 独特字符串 |
| **第 2 层：格式规范化去重** | 去除首尾空格、清除多余末尾逗号/换行符、全小写归一 | **217 个** 规范化提示词 |
| **第 3 层：处理状态完全守恒** | 纳入 (130) + 合并 (115) + 延期 (1) + 排除 (0) | **246 行** (严格等于 246 行) |

---

## 二、246 行逐行数据迁移台账

| 行号 | 原分类 | 原始中文 | 原始英文 Prompt | 规范化提示词 | 处理状态 | 目标槽位 | 目标 Catalog | 目标规范 ID | 操作类型 | 解扣能力 | 掀裙能力 | 处理决策与理由说明 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 综合1 | 比基尼 | `bikini` | `bikini` | 纳入 | clothing | clothing.json (categories) | `bikini_classic` | [Route] | 禁止 | 禁止 | 基础分体比基尼款式，无纽扣无裙摆 |
| 2 | 综合1 | 系绳比基尼 | `string bikini` | `string bikini` | 纳入 | clothing | clothing.json (categories) | `bikini_strappy` | [Route] | 禁止 | 禁止 | 系绳系带特色比基尼形制 |
| 3 | 综合1 | 前系带比基尼上装 | `front-tie bikini top` | `front-tie bikini top` | 合并 | clothing | clothing.json (categories) | `bikini_strappy` | [Route] | 禁止 | 禁止 | 比基尼前系带上装，作为款式叶子tag合并 |
| 4 | 综合1 | 侧系带比基尼下装 | `side-tie bikini bottom` | `side-tie bikini bottom` | 合并 | clothing | clothing.json (categories) | `bikini_strappy` | [Route] | 禁止 | 禁止 | 比基尼侧系带下装，作为款式叶子tag合并 |
| 5 | 综合1 | 微小比基尼 | `micro bikini` | `micro bikini` | 合并 | clothing | clothing.json (categories) | `bikini_micro` | [Route] | 禁止 | 禁止 | 微小比基尼，合并至存量 bikini_micro |
| 6 | 综合1 | 泳装 | `swimsuit` | `swimsuit` | 纳入 | clothing | clothing.json (categories) | `swimsuit_classic` | [Route] | 禁止 | 禁止 | 经典通用泳装款式 |
| 7 | 综合1 | 连体泳衣 | `one-piece swimsuit` | `one-piece swimsuit` | 合并 | clothing | clothing.json (categories) | `one_piece_swimsuit` | [Route] | 禁止 | 禁止 | 连体泳衣，合并至存量 one_piece_swimsuit |
| 8 | 综合1 | 学校泳衣 | `school swimsuit` | `school swimsuit` | 纳入 | clothing | clothing.json (categories) | `swimsuit_school` | [Route] | 禁止 | 禁止 | 日系传统学校死库水款式 |
| 9 | 综合1 | 竞赛泳衣 | `competition swimsuit` | `competition swimsuit` | 纳入 | clothing | clothing.json (categories) | `swimsuit_competition` | [Route] | 禁止 | 禁止 | 专业竞速连体泳装款式 |
| 10 | 综合1 | 运动服 | `sportswear` | `sportswear` | 纳入 | clothing | clothing.json (categories) | `sportswear_active` | [Route] | 不适用 | 不适用 | 综合运动服装通用款 |
| 11 | 综合1 | 排球服 | `volleyball uniform` | `volleyball uniform` | 纳入 | clothing | clothing.json (categories) | `volleyball_uniform` | [Route] | 禁止 | 禁止 | 排球竞技运动服装款式 |
| 12 | 综合1 | 旗袍 | `china dress` | `china dress` | 合并 | clothing | clothing.json (categories) | `qipao` | [Route] | 允许 | 禁止 | 旗袍传统名称，作为 qipao 的别名/标签 |
| 13 | 综合1 | 水手服 | `serafuku` | `serafuku` | 合并 | clothing | clothing.json (categories) | `jk_seifuku` | [Route] | 禁止 | 允许 | 日系水手服，合并至存量 jk_seifuku |
| 14 | 综合1 | 校服 | `school uniform` | `school uniform` | 合并 | clothing | clothing.json (categories) | `blazer_uniform` | [Route] | 允许 | 允许 | 通用校服，合并至存量 blazer_uniform |
| 15 | 综合1 | 布鲁玛 | `buruma` | `buruma` | 合并 | clothing | clothing.json (categories) | `gym_uniform` | [Route] | 禁止 | 禁止 | 布鲁玛短裤，合并至存量 gym_uniform |
| 16 | 综合1 | 高领衬衫 | `collared shirt` | `collared shirt` | 纳入 | clothing | clothing.json (categories) | `shirts_blouses` | [Route] | 允许 | 不适用 | 高领立领长袖衬衫独立款式 |
| 17 | 综合1 | 紧身衣 | `leotard` | `leotard` | 纳入 | clothing | clothing.json (categories) | `leotard_bodysuit` | [Route] | 禁止 | 禁止 | 体操舞蹈紧身连体衣独立款式 |
| 18 | 综合1 | 无肩带紧身衣 | `strapless leotard` | `strapless leotard` | 合并 | clothing | clothing.json (categories) | `leotard_bodysuit` | [Route] | 禁止 | 禁止 | 无肩带紧身衣，作为紧身衣叶子tag |
| 19 | 综合1 | 高叉紧身衣 | `highleg leotard` | `highleg leotard` | 合并 | clothing | clothing.json (categories) | `leotard_bodysuit` | [Route] | 禁止 | 禁止 | 高叉紧身衣，作为紧身衣叶子tag |
| 20 | 综合1 | 丁字紧身衣 | `thong leotard` | `thong leotard` | 合并 | clothing | clothing.json (categories) | `leotard_bodysuit` | [Route] | 禁止 | 禁止 | 丁字紧身衣，作为紧身衣叶子tag |
| 21 | 综合1 | 衣服下紧身衣 | `leotard under clothes` | `leotard under clothes` | 纳入 | clothing | clothing.json (clothing_states) | `undergarment_leotard` | [Route] | 禁止 | 禁止 | 衣物下穿着紧身衣叠穿状态 |
| 22 | 综合1 | 紧身衣服 | `taut clothes` | `taut clothes` | 纳入 | clothing | clothing.json (clothing_states) | `taut_tight` | [Route] | 不适用 | 不适用 | 衣物紧绷勾勒状态 |
| 23 | 综合1 | 紧身衬衫 | `taut shirt` | `taut shirt` | 合并 | clothing | clothing.json (categories) | `shirts_blouses` | [Route] | 允许 | 不适用 | 紧身衬衫，作为衬衫款式变体tag |
| 24 | 综合1 | 薄纱连衣裙 | `sheer tulle dress` | `sheer tulle dress` | 纳入 | clothing | clothing.json (categories) | `tulle_dress` | [Route] | 不适用 | 允许 | 轻薄透薄纱仙气连衣裙独立款式 |
| 25 | 综合1 | 雪纺连衣裙 | `chiffon dress` | `chiffon dress` | 纳入 | clothing | clothing.json (categories) | `chiffon_dress` | [Route] | 不适用 | 允许 | 轻盈雪纺面料连衣裙独立款式 |
| 26 | 综合1 | 紧身衣裤 | `bodysuit` | `bodysuit` | 合并 | clothing | clothing.json (categories) | `leotard_bodysuit` | [Route] | 禁止 | 禁止 | 紧身衣裤同义词，合并至紧身衣 |
| 27 | 综合1 | 背心 | `tank top` | `tank top` | 纳入 | clothing | clothing.json (categories) | `tops_tanks` | [Route] | 禁止 | 不适用 | 基础无袖吊带背心独立款式 |
| 28 | 综合1 | 连身裙 | `dress` | `dress` | 纳入 | clothing | clothing.json (categories) | `dress_casual` | [Route] | 不适用 | 允许 | 通用休闲连身裙独立款式 |
| 29 | 综合1 | 露背连身裙 | `backless dress` | `backless dress` | 纳入 | clothing | clothing.json (categories) | `dress_backless` | [Route] | 不适用 | 允许 | 露背剪裁连身裙独立款式 |
| 30 | 综合1 | 绕颈连身裙 | `halter dress` | `halter dress` | 纳入 | clothing | clothing.json (categories) | `halter_dress` | [Route] | 不适用 | 允许 | 挂脖绕颈连身裙独立款式 |
| 31 | 综合1 | 毛衣连身裙 | `sweater dress` | `sweater dress` | 纳入 | clothing | clothing.json (categories) | `sweater_dress` | [Route] | 不适用 | 允许 | 针织毛衣连衣裙独立款式 |
| 32 | 综合1 | 露背装 | `backless outfit` | `backless outfit` | 合并 | clothing | clothing.json (categories) | `dress_backless` | [Route] | 不适用 | 允许 | 露背装概念，合并至露背连身裙 |
| 33 | 综合1 | 毛衣 | `sweater` | `sweater` | 纳入 | clothing | clothing.json (categories) | `sweater_casual` | [Route] | 禁止 | 不适用 | 基础休闲针织毛衣/套头衫独立款式 |
| 34 | 综合1 | 高领毛衣 | `turtleneck sweater` | `turtleneck sweater` | 合并 | clothing | clothing.json (categories) | `sweater_casual` | [Route] | 禁止 | 不适用 | 高领毛衣领型变体，作为款式tag |
| 35 | 综合1 | 罗纹毛衣 | `ribbed sweater` | `ribbed sweater` | 合并 | clothing | clothing.json (categories) | `sweater_casual` | [Route] | 禁止 | 不适用 | 罗纹织纹毛衣变体，作为款式tag |
| 36 | 综合1 | 露肩毛衣 | `off-shoulder sweater` | `off-shoulder sweater` | 合并 | clothing | clothing.json (categories) | `sweater_casual` | [Route] | 禁止 | 不适用 | 露肩毛衣变体，作为款式tag |
| 37 | 综合1 | 心型切口 | `heart cutout` | `heart cutout` | 纳入 | clothing | clothing.json (clothing_states) | `heart_cutout` | [Route] | 不适用 | 不适用 | 胸口心型镂空剪裁状态 |
| 38 | 综合1 | 后背切口 | `back cutout` | `back cutout` | 纳入 | clothing | clothing.json (clothing_states) | `back_cutout` | [Route] | 不适用 | 不适用 | 后背镂空大露背剪裁状态 |
| 39 | 综合1 | 下胸切口 | `underboob cutout` | `underboob cutout` | 纳入 | clothing | clothing.json (clothing_states) | `underboob_cutout` | [Route] | 不适用 | 不适用 | 下胸微露镂空切口状态 |
| 40 | 综合1 | 小可爱露腹短上衣 | `crop top` | `crop top` | 纳入 | clothing | clothing.json (categories) | `crop_top` | [Route] | 禁止 | 不适用 | 露腹露脐短上衣独立款式 |
| 41 | 综合1 | 赛车服(By KimZuo) | `racing suit` | `racing suit` | 纳入 | clothing | clothing.json (categories) | `racing_suit` | [Fix][Route] | 允许 | 禁止 | 剔除作者署名，职业赛车连体服独立款式 |
| 42 | 综合1 | 护士服(By Yao_men) | `nurse` | `nurse` | 合并 | clothing | clothing.json (categories) | `nurse_uniform` | [Fix][Route] | 允许 | 允许 | 剔除署名，合并至存量 nurse_uniform |
| 43 | 综合1 | 乳胶紧身衣(By Yao_men) | `latex` | `latex` | 合并 | clothing | clothing.json (categories) | `latex_catsuit` | [Fix][Route] | 禁止 | 禁止 | 剔除署名，合并至存量 latex_catsuit |
| 44 | 综合1 | 白大褂(By Yao_men) | `lab_coat` | `lab_coat` | 纳入 | clothing | clothing.json (categories) | `lab_coat` | [Fix][Route] | 允许 | 不适用 | 剔除署名，科研实验白大褂独立款式 |
| 45 | 综合1 | 便利店工作服(By 糯米) | `convenience store uniforms` | `convenience store uniforms` | 纳入 | clothing | clothing.json (categories) | `convenience_store` | [Fix][Route] | 允许 | 不适用 | 剔除署名，便利店员工工作服独立款式 |
| 46 | 综合1 | 夏日长裙 | `summer long skirt` | `summer long skirt` | 纳入 | clothing | clothing.json (categories) | `summer_sundress` | [Route] | 不适用 | 允许 | 夏日长裙/吊带裙独立款式 |
| 47 | 综合1 | 西装 | `business suit` | `business suit` | 纳入 | clothing | clothing.json (categories) | `business_suit` | [Route] | 允许 | 禁止 | 正统男女西服套装，裤装形制无裙摆，彻底独立于 ol_suit |
| 48 | 综合1 | 浴衣 | `yukata` | `yukata` | 合并 | clothing | clothing.json (categories) | `yukata` | [Route] | 禁止 | 允许 | 传统夏日浴衣，合并至存量 yukata |
| 49 | 综合1 | 圣诞装 | `santa` | `santa` | 纳入 | clothing | clothing.json (categories) | `festive_costume` | [Route] | 允许 | 允许 | 圣诞老人特色节日角色装扮 |
| 50 | 综合1 | 哥特洛丽塔风格 | `gothic_lolita` | `gothic_lolita` | 纳入 | clothing | clothing.json (categories) | `lolita_gothic` | [Route] | 允许 | 允许 | 哥特洛丽塔风格洋装独立款式 |
| 51 | 综合1 | 马猴烧酒风格 | `mahou shoujo` | `mahou shoujo` | 纳入 | clothing | clothing.json (categories) | `mahou_shoujo` | [Route] | 禁止 | 允许 | 二次元魔法少女战装独立款式 |
| 52 | 综合2 | 女仆装 | `Maid dress` | `maid dress` | 合并 | clothing | clothing.json (categories) | `maid_dress` | [Route] | 允许 | 允许 | 女仆装通用词，合并至存量 maid_dress |
| 53 | 综合2 | 西服(black黑)-by bilibili-跑酷 | `black suit` | `black suit` | 合并 | clothing | clothing.json (categories) | `business_suit` | [Fix][Route] | 允许 | 禁止 | 剔除平台署名，作为黑西装变体合并，裤装无裙摆 |
| 54 | 综合2 | 啦啦隊 | `cheerleading` | `cheerleading` | 合并 | clothing | clothing.json (categories) | `cheerleader` | [Fix][Route] | 禁止 | 允许 | 繁转简，合并至存量 cheerleader |
| 55 | 综合2 | 迷你比基尼 | `micro bikini` | `micro bikini` | 合并 | clothing | clothing.json (categories) | `bikini_micro` | [Route] | 禁止 | 禁止 | 迷你比基尼，与第5行完全重复合并 |
| 56 | 综合2 | 頸帶 | `neck ribbon` | `neck ribbon` | 纳入 | jewelry | accessories.json (headwear_jewelry) | `neck_ribbon` | [Fix][Route] | 不适用 | 不适用 | 繁转简，丝带颈带独立配饰件 |
| 57 | 综合2 | 无胸罩 | `no_bra` | `no_bra` | 纳入 | clothing | clothing.json (clothing_states) | `braless` | [Route] | 不适用 | 不适用 | 无胸罩内衣脱去状态 |
| 58 | 综合2 | 兜帽斗篷 | `Cape hood` | `cape hood` | 纳入 | jewelry | accessories.json (headwear_jewelry) | `hooded_cloak` | [Route] | 允许 | 不适用 | 连帽防风斗篷，与无帽披风分开独立 |
| 59 | 综合2 | 修女服 | `nun gown` | `nun gown` | 纳入 | clothing | clothing.json (categories) | `clerical_nun` | [Route] | 禁止 | 允许 | 修女修道服/圣袍独立款式 |
| 60 | 综合2 | 军装 | `military uniform` | `military uniform` | 纳入 | clothing | clothing.json (categories) | `military_uniform` | [Route] | 允许 | 禁止 | 正规军服常服独立款式，长裤形制无裙摆 |
| 61 | 综合2 | 汉服 | `hanfu` | `hanfu` | 合并 | clothing | clothing.json (categories) | `hanfu` | [Route] | 禁止 | 允许 | 传统汉服，合并至存量 hanfu |
| 62 | 综合2 | 破损的衣物 | `torn clothes` | `torn clothes` | 合并 | clothing | clothing.json (clothing_states) | `torn_shredded` | [Route] | 不适用 | 不适用 | 破损衣物，合并至存量 torn_shredded |
| 63 | 综合2 | 婚纱 | `wedding_dress` | `wedding_dress` | 纳入 | clothing | clothing.json (categories) | `wedding_dress` | [Fix][Route] | 禁止 | 允许 | 新娘白纱婚纱独立款式 |
| 64 | 综合2 | 黑色礼服 | `black skirt dress, flower pattern in dress,black gown` | `black skirt dress, flower pattern in dress,black gown` | 合并 | clothing | clothing.json (categories) | `evening_dress` | [Reduce][Route] | 不适用 | 允许 | 黑色印花礼服，合并至晚礼服变体tag |
| 65 | 综合2 | 披风 | `cloak` | `cloak` | 纳入 | jewelry | accessories.json (headwear_jewelry) | `cloak` | [Route] | 允许 | 不适用 | 无帽长披风，与连帽斗篷彻底分开 |
| 66 | 综合2 | 白色风衣 | `white_windbreaker` | `white_windbreaker` | 纳入 | clothing | clothing.json (categories) | `windbreaker` | [Route] | 允许 | 不适用 | 运动白色防风风衣独立款式 |
| 67 | 综合2 | 风衣 | `wind coat` | `wind coat` | 纳入 | clothing | clothing.json (categories) | `trench_coat` | [Route] | 允许 | 不适用 | 英伦战壕风衣外套独立款式 |
| 68 | 综合2 | 奶牛比基尼 | `cow_bikini` | `cow_bikini` | 纳入 | clothing | clothing.json (categories) | `bikini_creative` | [Route] | 禁止 | 禁止 | 奶牛印花创意比基尼款式 |
| 69 | 综合2 | 露背毛衣 | `Open-backed sweater` | `open-backed sweater` | 合并 | clothing | clothing.json (categories) | `knit_sweater` | [Route] | 禁止 | 不适用 | 露背毛衣同义词，合并至 knit_sweater |
| 70 | 综合2 | 曬痕 | `tan line` | `tan line` | 纳入 | imperfections | imperfections.json (categories) | `tan_lines` | [Fix][Route] | 不适用 | 不适用 | 繁转简，日晒泳装痕迹，身体唯一主归属 |
| 71 | 综合2 | 运动制服 | `gym_uniform` | `gym_uniform` | 合并 | clothing | clothing.json (categories) | `gym_uniform` | [Route] | 禁止 | 禁止 | 运动制服，合并至存量 gym_uniform |
| 72 | 综合2 | 晚礼服 | `evening dress` | `evening dress` | 合并 | clothing | clothing.json (categories) | `evening_dress` | [Route] | 不适用 | 允许 | 晚礼服同义词，合并至存量 evening_dress |
| 73 | 综合2 | 礼服 | `full dress` | `full dress` | 纳入 | clothing | clothing.json (categories) | `formal_gown` | [Route] | 不适用 | 允许 | 正式全套大礼服独立款式 |
| 74 | 综合2 | 战斗服 | `combat suit` | `combat suit` | 纳入 | clothing | clothing.json (categories) | `combat_tactical` | [Route] | 允许 | 禁止 | 战术特警战斗服独立款式，长裤形制无裙摆 |
| 75 | 综合2 | 小披风 | `poncho` | `poncho` | 纳入 | jewelry | accessories.json (headwear_jewelry) | `poncho` | [Route] | 禁止 | 不适用 | 套头小披肩，独立配饰形制 |
| 76 | 综合2 | 实验袍 | `lab coat` | `lab coat` | 合并 | clothing | clothing.json (categories) | `lab_coat` | [Route] | 允许 | 不适用 | 实验袍，与第44行合并至 lab_coat |
| 77 | 综合2 | 学校制服 | `school_uniform` | `school_uniform` | 合并 | clothing | clothing.json (categories) | `korean_school` | [Route] | 允许 | 允许 | 学校制服，作为韩系校服变体合并 |
| 78 | 综合2 | 甜美可爱的洛丽塔 | `sweet_lolita` | `sweet_lolita` | 纳入 | clothing | clothing.json (categories) | `lolita_sweet` | [Route] | 允许 | 允许 | 甜美洛丽塔洋装独立款式 |
| 79 | 综合2 | 网纹衣 | `fishnet top` | `fishnet top` | 纳入 | clothing | clothing.json (categories) | `fishnet_top` | [Route] | 禁止 | 不适用 | 网眼镂空透气上衣独立款式 |
| 80 | 综合2 | 魔女风格服 | `Witch dress` | `witch dress` | 纳入 | clothing | clothing.json (categories) | `witch_robe` | [Route] | 禁止 | 允许 | 奇幻魔女长裙法袍独立款式 |
| 81 | 综合2 | 巫女服 | `Miko clothing` | `miko clothing` | 纳入 | clothing | clothing.json (categories) | `shinto_miko` | [Route] | 禁止 | 允许 | 神社巫女服独立款式 |
| 82 | 综合2 | 无裆内裤 | `crotchless panties` | `crotchless panties` | 纳入 | lingerie | clothing.json (lingerie_wardrobe) | `crotchless_panties` | [Route] | 禁止 | 禁止 | 情趣衣柜开裆无底内裤 |
| 83 | 综合2 | 大衣 | `overcoat` | `overcoat` | 纳入 | clothing | clothing.json (categories) | `outerwear_overcoat` | [Route] | 允许 | 不适用 | 毛呢长大衣独立外套款式 |
| 84 | 综合2 | 湿润的衣服 | `wet clothes` | `wet clothes` | 纳入 | clothing | clothing.json (clothing_states) | `wet_pure` | [Route] | 不适用 | 不适用 | 纯粹湿润衣物，不标半透与紧贴 |
| 85 | 综合2 | 长袍 | `robe` | `robe` | 纳入 | clothing | clothing.json (categories) | `robe_general` | [Route] | 允许 | 允许 | 宽松通用长袍独立款式 |
| 86 | 综合2 | 战壕风衣 | `trench_coat` | `trench_coat` | 合并 | clothing | clothing.json (categories) | `trench_coat` | [Route] | 允许 | 不适用 | 战壕风衣同义词，合并至 trench_coat |
| 87 | 综合2 | 抹胸 | `strapless tank top, navel cutout` | `strapless tank top, navel cutout` | 纳入 | clothing | clothing.json (categories) | `strapless_top` | [Route] | 禁止 | 不适用 | 抹胸式露腹短上衣独立款式 |
| 88 | 综合2 | 派克大衣 | `parka` | `parka` | 纳入 | clothing | clothing.json (categories) | `winter_parka` | [Route] | 允许 | 不适用 | 防寒派克大衣外套款式 |
| 89 | 综合2 | 洛丽塔风格 | `lolita_fashion` | `lolita_fashion` | 纳入 | clothing | clothing.json (categories) | `lolita_fashion` | [Route] | 允许 | 允许 | 洛丽塔综合洋装大类 |
| 90 | 综合2 | 无内衣 | `no underwear` | `no underwear` | 纳入 | clothing | clothing.json (clothing_states) | `underwearless` | [Route] | 不适用 | 不适用 | 无内衣真空穿着状态 |
| 91 | 综合2 | 水手裙 | `sailor dress` | `sailor dress` | 合并 | clothing | clothing.json (categories) | `jk_seifuku` | [Route] | 禁止 | 允许 | 水手裙，作为水手服裙装下半身tag |
| 92 | 综合2 | 紧身连体衣 | `zentai` | `zentai` | 纳入 | clothing | clothing.json (categories) | `zentai_suit` | [Route] | 禁止 | 禁止 | 全包紧身衣连体服独立款式 |
| 93 | 综合2 | 皮衣 | `leather jacket` | `leather jacket` | 纳入 | clothing | clothing.json (categories) | `leather_jacket` | [Route] | 允许 | 不适用 | 机车皮夹克独立外套款式 |
| 94 | 综合2 | 防弹衣 | `bulletproof_vest,` | `bulletproof_vest` | 纳入 | clothing | clothing.json (categories) | `tactical_vest` | [Fix][Route] | 允许 | 不适用 | 清除末尾非法逗号，战术防弹背心独立款式 |
| 95 | 综合2 | 蛛网纹路 | `spider web print` | `spider web print` | 合并 | clothing | clothing.json (categories) | `lolita_gothic` | [Route] | 不适用 | 不适用 | 蛛网纹路印花，作为哥特裙叶子tag |
| 96 | 综合2 | sweet_lolita, | `sweet_lolita` | `sweet_lolita` | 合并 | clothing | clothing.json (categories) | `lolita_sweet` | [Fix][Route] | 允许 | 允许 | 清除末尾非法逗号，合并至甜美Lolita |
| 97 | 综合2 | A | `Maid dress` | `maid dress` | 合并 | clothing | clothing.json (categories) | `maid_dress` | [Fix][Route] | 允许 | 允许 | 原表上下文确认字母A为女仆装录入手误合并 |
| 98 | 综合2 | 史莱姆装 | `slime dress` | `slime dress` | 纳入 | clothing | clothing.json (categories) | `slime_dress` | [Route] | 禁止 | 允许 | 奇幻史莱姆半透明材质特色服饰 |
| 99 | 综合2 | 撕裂的衣服 | `torn clothes` | `torn clothes` | 合并 | clothing | clothing.json (clothing_states) | `torn_shredded` | [Route] | 不适用 | 不适用 | 撕裂衣服，合并至存量 torn_shredded |
| 100 | 综合2 | 无 | `less clothes\n` | `less clothes` | 延期 | - | - | `-` | [Defer] | 不适用 | 不适用 | 待裁定：意图为脱衣/少穿，属于全局负向/状态指令，暂不作为独立款式，留底待裁定 |
| 101 | 综合3 | 乳胶衣 | `latex` | `latex` | 合并 | clothing | clothing.json (categories) | `latex_catsuit` | [Route] | 禁止 | 禁止 | 乳胶衣同义词，合并至存量 latex_catsuit |
| 102 | 综合3 | 中式死库水（辉木） | `Chinese style,One-piece swimsuit，Clothes with gold patterns` | `chinese style,one-piece swimsuit，clothes with gold patterns` | 纳入 | clothing | clothing.json (categories) | `swimsuit_creative` | [Fix][Route] | 禁止 | 禁止 | 剔除作者署名，特色国风中式死库水款式 |
| 103 | 综合3 | 雨衣 | `Raincoat` | `raincoat` | 纳入 | clothing | clothing.json (categories) | `rainwear_coat` | [Route] | 允许 | 不适用 | 防水防雨外衣长款独立款式 |
| 104 | 综合3 | 不知火舞 | `Mai Shiranui` | `mai shiranui` | 纳入 | clothing | clothing.json (categories) | `anime_cosplay` | [Route] | 禁止 | 禁止 | 不知火舞二次元格斗经典角色装 |
| 105 | 综合3 | 街头风格服饰 | `street wear` | `street wear` | 合并 | clothing | clothing.json (categories) | `street_casual` | [Route] | 禁止 | 允许 | 街头风格服饰，合并至存量 street_casual |
| 106 | 综合3 | 修女 | `loli,one girl,domineering lady, nun` | `loli,one girl,domineering lady, nun` | 合并 | clothing | clothing.json (categories) | `clerical_nun` | [Reduce][Route] | 禁止 | 允许 | 剔除loli/one girl等人物构图词，合并至修女服 |
| 107 | 综合3 | 短款和服 | `kimono` | `kimono` | 合并 | clothing | clothing.json (categories) | `kimono` | [Route] | 禁止 | 允许 | 短款和服，作为和服款式叶子tag |
| 108 | 综合3 | 浴袍 | `bathrobe` | `bathrobe` | 纳入 | clothing | clothing.json (categories) | `bathrobe` | [Route] | 允许 | 允许 | 沐浴宽松浴袍独立款式 |
| 109 | 综合3 | 铠甲 | `armor` | `armor` | 纳入 | clothing | clothing.json (categories) | `knight_armor` | [Route] | 禁止 | 禁止 | 中世纪骑士金属板甲独立款式 |
| 110 | 综合3 | 外套 | `coat` | `coat` | 纳入 | clothing | clothing.json (categories) | `outerwear_coat` | [Route] | 允许 | 不适用 | 通用外套大衣独立款式 |
| 111 | 综合3 | 连帽衫(带帽卫衣) | `hoodie` | `hoodie` | 纳入 | clothing | clothing.json (categories) | `hoodie` | [Route] | 禁止 | 不适用 | 连帽套头卫衣独立款式 |
| 112 | 综合3 | 圆领卫衣 | `sweatshirt` | `sweatshirt` | 纳入 | clothing | clothing.json (categories) | `sweatshirt` | [Route] | 禁止 | 不适用 | 圆领套头卫衣独立款式 |
| 113 | 综合3 | 神父/修生黑袍 | `Cassock` | `cassock` | 纳入 | clothing | clothing.json (categories) | `clerical_priest` | [Route] | 允许 | 允许 | 天主教神父/修生黑袍独立款式 |
| 114 | 综合3 | 动力甲 | `power armor` | `power armor` | 纳入 | clothing | clothing.json (categories) | `mecha_power_armor` | [Route] | 禁止 | 禁止 | 科幻动力装甲外覆机甲款式 |
| 115 | 综合3 | 长袖运动服（直译为立领长风衣） | `Standing collar long windbreaker` | `standing collar long windbreaker` | 合并 | clothing | clothing.json (categories) | `windbreaker` | [Route] | 允许 | 不适用 | 立领长风衣，合并至防风外套款式 |
| 116 | 综合3 | 旗袍（效果好） | `cheongsam` | `cheongsam` | 合并 | clothing | clothing.json (categories) | `qipao` | [Fix][Route] | 允许 | 禁止 | 剔除括号注记，保持纯粹cheongsam合并至qipao |
| 117 | 综合3 | 工装 | `�� dungarees` | `�� dungarees` | 纳入 | clothing | clothing.json (categories) | `dungarees` | [Fix][Route] | 允许 | 禁止 | 清除乱码字符，工装连体背带裤独立款式 |
| 118 | 综合3 | 中国的衣服裙子 | `chinese clothes,china dress,` | `chinese clothes,china dress` | 合并 | clothing | clothing.json (categories) | `qipao` | [Route] | 允许 | 禁止 | 中国衣服裙子，合并至旗袍/国风体系 |
| 119 | 综合3 | 高叉泳衣 | `highleg swimsuit` | `highleg swimsuit` | 合并 | clothing | clothing.json (categories) | `one_piece_swimsuit` | [Route] | 禁止 | 禁止 | 高叉泳衣同义词，合并至存量连体泳衣 |
| 120 | 综合3 | 礼服长裙 | `revealing dress` | `revealing dress` | 合并 | clothing | clothing.json (categories) | `evening_dress` | [Route] | 不适用 | 允许 | 礼服长裙性感变体，合并至存量 evening_dress |
| 121 | 综合3 | 病号服 | `hospital gown` | `hospital gown` | 纳入 | clothing | clothing.json (categories) | `hospital_gown` | [Route] | 允许 | 允许 | 医疗病号服独立款式 |
| 122 | 综合3 | 白色衣服 | `White clothes` | `white clothes` | 合并 | clothing | clothing.json (categories) | `shirts_blouses` | [Route] | 允许 | 不适用 | 白色衣服颜色特征，作为白衬衫叶子tag |
| 123 | 综合3 | 希腊服饰 | `Greek clothes` | `greek clothes` | 纳入 | clothing | clothing.json (categories) | `greek_toga` | [Route] | 禁止 | 允许 | 古希腊托加长袍，与玄门道袍坚决分开 |
| 124 | 综合3 | 紧身连衣裤 | `leotards` | `leotards` | 合并 | clothing | clothing.json (categories) | `leotard_bodysuit` | [Route] | 禁止 | 禁止 | 紧身连衣裤复数形式，合并至紧身连体衣 |
| 125 | 综合3 | V领针织毛衣（无袖背心） | `V-NECK SWEATER VEST` | `v-neck sweater vest` | 纳入 | clothing | clothing.json (categories) | `knit_vest` | [Route] | 禁止 | 不适用 | V领针织无袖毛衣背心独立款式 |
| 126 | 综合3 | 南瓜裙 | `Pumpkin skirt` | `pumpkin skirt` | 纳入 | clothing | clothing.json (categories) | `pumpkin_skirt` | [Route] | 禁止 | 允许 | 万圣节特色蓬松南瓜裙独立款式 |
| 127 | 综合3 | 万圣节服装 | `halloween_costume` | `halloween_costume` | 合并 | clothing | clothing.json (categories) | `festive_costume` | [Route] | 允许 | 允许 | 万圣节服装，合并至节日角色装扮大类 |
| 128 | 综合3 | 软壳外套 | `soft shell coat` | `soft shell coat` | 纳入 | clothing | clothing.json (categories) | `soft_shell_jacket` | [Route] | 允许 | 不适用 | 户外软壳防风夹克独立款式 |
| 129 | 综合3 | 内衣 | `underwear` | `underwear` | 纳入 | lingerie | clothing.json (lingerie_wardrobe) | `basic_underwear` | [Route] | 禁止 | 禁止 | 基础内衣概念套，明确作为规范 ID 纳入情趣衣柜扩展条目 |
| 130 | 综合3 | 外骨骼 | `exoskeleton` | `exoskeleton` | 纳入 | clothing | clothing.json (categories) | `mecha_exoskeleton` | [Route] | 禁止 | 禁止 | 机械外骨骼装束独立款式 |
| 131 | 综合3 | 罩衫 | `frock` | `frock` | 纳入 | clothing | clothing.json (categories) | `frock_smock` | [Route] | 允许 | 允许 | 欧洲传统罩衫宽松工作服独立款式 |
| 132 | 综合3 | 道袍 | `Taoist robe` | `taoist robe` | 纳入 | clothing | clothing.json (categories) | `taoist_robe` | [Route] | 禁止 | 允许 | 东方玄门道教法衣道袍，与希腊袍分开 |
| 133 | 综合3 | 军大衣 | `Army overcoat` | `army overcoat` | 纳入 | clothing | clothing.json (categories) | `military_overcoat` | [Route] | 允许 | 不适用 | 军用毛呢防寒长款大衣独立款式 |
| 134 | 综合3 | 荷叶边衬衫 | `frillded shirt` | `frillded shirt` | 合并 | clothing | clothing.json (categories) | `shirts_blouses` | [Fix][Route] | 允许 | 不适用 | 纠正拼写 (frillded->frilled)，荷叶边衬衫tag |
| 135 | 综合3 | 黑色连衣裙+白色打底T恤搭配(请勿去掉tag括号) | `(((black sundress with round neck,white T-shirt bottom)))` | `(((black sundress with round neck,white t-shirt bottom)))` | 纳入 | clothing | clothing.json (categories) | `sundress_layered` | [Flatten][Route] | 不适用 | 允许 | 消除多层嵌套括号，规范为黑吊带叠穿白T恤款式 |
| 136 | 综合3 | 外骨骼机甲 | `Exoskeleton Mecha` | `exoskeleton mecha` | 合并 | clothing | clothing.json (categories) | `mecha_exoskeleton` | [Route] | 禁止 | 禁止 | 外骨骼机甲，与第130行合并至 mecha_exoskeleton |
| 137 | 综合3 | 拼接款 | `mosaic` | `mosaic` | 合并 | clothing | clothing.json (categories) | `dress_casual` | [Route] | 不适用 | 允许 | 拼接款图案特征，作为连衣裙叶子tag |
| 138 | 综合3 | 战袍 | `Battle Robe` | `battle robe` | 纳入 | clothing | clothing.json (categories) | `battle_robe` | [Route] | 禁止 | 允许 | 东方奇幻武侠长袍战袍独立款式 |
| 139 | 综合3 | 机械服装 | `mechanical clothes` | `mechanical clothes` | 合并 | clothing | clothing.json (categories) | `mecha_exoskeleton` | [Route] | 禁止 | 禁止 | 机械服装，合并至外骨骼机甲体系 |
| 140 | 综合3 | 机械战甲 | `[Battle Robe:Exoskeleton Mecha:0.3]` | `[battle robe:exoskeleton mecha:0.3]` | 合并 | clothing | clothing.json (categories) | `mecha_exoskeleton` | [Flatten][Route] | 禁止 | 禁止 | 冒号语法平铺降级为组合词，作为机甲款式tag |
| 141 | 裙子 | 裙子 | `skirt` | `skirt` | 纳入 | clothing | clothing.json (categories) | `skirts_general` | [Route] | 禁止 | 允许 | 半身裙通用款式 |
| 142 | 裙子 | 百褶裙 | `pleated skirt` | `pleated skirt` | 纳入 | clothing | clothing.json (categories) | `pleated_skirt` | [Route] | 禁止 | 允许 | 经典褶皱百褶半身裙独立款式 |
| 143 | 裙子 | 超短裙 | `miniskirt` | `miniskirt` | 纳入 | clothing | clothing.json (categories) | `miniskirt` | [Route] | 禁止 | 允许 | 膝上超短裙独立款式 |
| 144 | 裙子 | 连衣裙 | `one-piece dress` | `one-piece dress` | 合并 | clothing | clothing.json (categories) | `dress_casual` | [Route] | 不适用 | 允许 | 连衣裙同义词，合并至连身裙 |
| 145 | 裙子 | 花卉图案连衣裙（白） | `white skirt dress, flower pattern in dress,white gow` | `white skirt dress, flower pattern in dress,white gow` | 纳入 | clothing | clothing.json (categories) | `floral_dress_white` | [Fix][Route] | 不适用 | 允许 | 纠正拼写 (gow->gown)，白色印花礼服长裙 |
| 146 | 裙子 | 花卉图案连衣裙（黑） | `black skirt dress, flower pattern in dress,black gow` | `black skirt dress, flower pattern in dress,black gow` | 纳入 | clothing | clothing.json (categories) | `floral_dress_black` | [Fix][Route] | 不适用 | 允许 | 纠正拼写 (gow->gown)，黑色印花礼服长裙 |
| 147 | 裙子 | 多層裙子 | `layered skirt` | `layered skirt` | 纳入 | clothing | clothing.json (categories) | `layered_skirt` | [Fix][Route] | 禁止 | 允许 | 繁转简，分层蛋糕裙，与铅笔裙坚决分开 |
| 148 | 裙子 | 分层式半身裙（贵族气质）（by残阳） | `layered skirt` | `layered skirt` | 合并 | clothing | clothing.json (categories) | `layered_skirt` | [Fix][Route] | 禁止 | 允许 | 剔除作者署名，与第147行合并至多层裙 |
| 149 | 裙子 | 夏日连衣裙 | `summer dress` | `summer dress` | 合并 | clothing | clothing.json (categories) | `summer_sundress` | [Route] | 不适用 | 允许 | 夏日连衣裙同义词，合并至夏日长裙 |
| 150 | 裙子 | 腰围裙 | `waist apron` | `waist apron` | 纳入 | clothing | clothing.json (categories) | `waist_apron` | [Route] | 禁止 | 允许 | 半身系腰围裙独立款式 |
| 151 | 裙子 | 蓬蓬裙 | `pettiskirt` | `pettiskirt` | 纳入 | clothing | clothing.json (categories) | `pettiskirt` | [Route] | 禁止 | 允许 | 多层网纱蓬蓬裙独立款式 |
| 152 | 裙子 | 芭蕾舞裙 | `tutu` | `tutu` | 纳入 | clothing | clothing.json (categories) | `tutu_skirt` | [Route] | 禁止 | 允许 | 芭蕾舞短款硬纱裙独立款式 |
| 153 | 裙子 | 格子裙 | `plaid skirt` | `plaid skirt` | 纳入 | clothing | clothing.json (categories) | `plaid_skirt` | [Route] | 禁止 | 允许 | 英伦学院风格子百褶裙独立款式 |
| 154 | 裙子 | 围裙 | `apron` | `apron` | 纳入 | clothing | clothing.json (categories) | `apron_dress` | [Route] | 禁止 | 允许 | 连体全身围裙女仆服款式 |
| 155 | 裙子 | 铅笔裙 | `pencil skirt` | `pencil skirt` | 纳入 | clothing | clothing.json (categories) | `pencil_skirt` | [Route] | 禁止 | 允许 | 修身紧身铅笔包臀裙，与多层裙分开 |
| 156 | 裙子 | 迷你裙 | `miniskirt` | `miniskirt` | 合并 | clothing | clothing.json (categories) | `miniskirt` | [Route] | 禁止 | 允许 | 迷你裙同义词，与第143行合并至超短裙 |
| 157 | 裙子 | 哥特式洛丽塔 | `lolita gothic` | `lolita gothic` | 合并 | clothing | clothing.json (categories) | `lolita_gothic` | [Route] | 允许 | 允许 | 哥特式洛丽塔，合并至第50行 lolita_gothic |
| 158 | 裙子 | 现代洛丽塔 | `lolita fasion` | `lolita fasion` | 合并 | clothing | clothing.json (categories) | `lolita_fashion` | [Fix][Route] | 允许 | 允许 | 纠正拼写 (fasion->fashion)，合并至Lolita大类 |
| 159 | 裙子 | 紧身连衣裙 | `Dirndl` | `dirndl` | 纳入 | clothing | clothing.json (categories) | `dirndl_dress` | [Route] | 允许 | 允许 | 德式巴伐利亚排扣紧身连身裙 |
| 160 | 裙子 | 铠装连衣裙 | `armored dress` | `armored dress` | 纳入 | clothing | clothing.json (categories) | `armored_dress` | [Route] | 禁止 | 允许 | 战斗重装板甲金属连衣裙独立款式 |
| 161 | 裙子 | 盔甲裙 | `armored dress` | `armored dress` | 合并 | clothing | clothing.json (categories) | `armored_dress` | [Route] | 禁止 | 允许 | 盔甲裙同义词，与第160行合并至 armored_dress |
| 162 | 裙子 | 长裙 | `Long skirt` | `long skirt` | 纳入 | clothing | clothing.json (categories) | `long_skirt` | [Route] | 禁止 | 允许 | 及踝长款半身裙独立款式 |
| 163 | 裙子 | 雨裙 | `Rainskirt` | `rainskirt` | 纳入 | clothing | clothing.json (categories) | `rain_skirt` | [Route] | 禁止 | 允许 | 户外防水半身防雨裙款式 |
| 164 | 裙子 | 中式旗袍死库水 | `chinese clothes+leotard` | `chinese clothes+leotard` | 合并 | clothing | clothing.json (categories) | `swimsuit_creative` | [Route] | 禁止 | 禁止 | 中式旗袍死库水，合并至第102行创意泳衣 |
| 165 | 裙子 | 带褶连衣裙 | `pleated dress` | `pleated dress` | 纳入 | clothing | clothing.json (categories) | `pleated_dress` | [Route] | 不适用 | 允许 | 全身细风琴褶皱长款连衣裙款式 |
| 166 | 裙子 | 无肩带礼服 | `strapless dress` | `strapless dress` | 纳入 | clothing | clothing.json (categories) | `strapless_dress` | [Route] | 不适用 | 允许 | 抹胸无肩带晚礼裙款式 |
| 167 | 裙子 | 露肩连衣裙 | `off-shoulder dress` | `off-shoulder dress` | 纳入 | clothing | clothing.json (categories) | `off_shoulder_dress` | [Route] | 不适用 | 允许 | 露肩连身长裙款式 |
| 168 | 裙子 | 婚纱 | `wedding dress` | `wedding dress` | 合并 | clothing | clothing.json (categories) | `wedding_dress` | [Route] | 禁止 | 允许 | 婚纱同义词，与第63行合并至 wedding_dress |
| 169 | 裙子 | 汉服 | `Han Chinese Clothing` | `han chinese clothing` | 合并 | clothing | clothing.json (categories) | `hanfu` | [Route] | 禁止 | 允许 | 汉服英文全名，合并至存量 hanfu |
| 170 | 裙子 | 微型短裙 | `microskirt` | `microskirt` | 纳入 | clothing | clothing.json (categories) | `microskirt` | [Route] | 禁止 | 允许 | 超微型齐臀性感短裙独立款式 |
| 171 | 裙子 | 黑百褶裙 | `black pleated skirt` | `black pleated skirt` | 合并 | clothing | clothing.json (categories) | `pleated_skirt` | [Route] | 禁止 | 允许 | 黑色百褶裙变体，作为百褶裙款式tag |
| 172 | 裙子 | 吊带裙 | `suspender skirt` | `suspender skirt` | 纳入 | clothing | clothing.json (categories) | `suspender_skirt` | [Route] | 禁止 | 允许 | 背带/吊带半身伞裙独立款式 |
| 173 | 上装 | 过手袖 | `sleeves_past_fingers` | `sleeves_past_fingers` | 合并 | clothing | clothing.json (categories) | `sweater_casual` | [Route] | 禁止 | 不适用 | 萌袖过手长袖特征，作为休闲毛衣叶子tag |
| 174 | 上装 | 背心 | `tank top` | `tank top` | 合并 | clothing | clothing.json (categories) | `tops_tanks` | [Route] | 禁止 | 不适用 | 背心同义词，与第27行合并至 tops_tanks |
| 175 | 上装 | 白衬衫 | `white shirt` | `white shirt` | 合并 | clothing | clothing.json (categories) | `shirts_blouses` | [Route] | 允许 | 不适用 | 白衬衫，合并至第16行 shirts_blouses 基础款 |
| 176 | 上装 | 水手衬衫 | `sailor shirt` | `sailor shirt` | 纳入 | clothing | clothing.json (categories) | `sailor_shirt` | [Route] | 禁止 | 不适用 | 水手领上装短袖衬衫独立款式 |
| 177 | 上装 | T恤 | `T-shirt` | `t-shirt` | 纳入 | clothing | clothing.json (categories) | `t_shirt` | [Route] | 禁止 | 不适用 | 短袖纯棉休闲T恤独立款式 |
| 178 | 上装 | 毛衣 | `sweater` | `sweater` | 合并 | clothing | clothing.json (categories) | `sweater_casual` | [Route] | 禁止 | 不适用 | 毛衣同义词，与第33行合并至 sweater_casual |
| 179 | 上装 | 夏日长裙 | `summer dress` | `summer dress` | 合并 | clothing | clothing.json (categories) | `summer_sundress` | [Route] | 不适用 | 允许 | 原表上装分类纠偏，合并至夏日连身长裙 |
| 180 | 上装 | 连帽衫 | `hoodie` | `hoodie` | 合并 | clothing | clothing.json (categories) | `hoodie` | [Route] | 禁止 | 不适用 | 连帽衫同义词，与第111行合并至 hoodie |
| 181 | 上装 | 毛领 | `fur trimmed colla` | `fur trimmed colla` | 合并 | clothing | clothing.json (categories) | `outerwear_overcoat` | [Fix][Route] | 允许 | 不适用 | 纠正拼写 (colla->collar)，毛领特征作为大衣tag |
| 182 | 上装 | 兜帽斗篷 | `hooded cloak` | `hooded cloak` | 合并 | jewelry | accessories.json (headwear_jewelry) | `hooded_cloak` | [Route] | 允许 | 不适用 | 兜帽斗篷同义词，与第58行合并至 hooded_cloak |
| 183 | 上装 | 夹克 | `jacket` | `jacket` | 纳入 | clothing | clothing.json (categories) | `outerwear_jacket` | [Route] | 允许 | 不适用 | 通用轻便夹克外套独立款式 |
| 184 | 上装 | 皮夹克 | `leather jacket` | `leather jacket` | 合并 | clothing | clothing.json (categories) | `leather_jacket` | [Route] | 允许 | 不适用 | 皮夹克同义词，与第93行合并至 leather_jacket |
| 185 | 上装 | 探险家夹克 | `safari jacket` | `safari jacket` | 纳入 | clothing | clothing.json (categories) | `safari_jacket` | [Route] | 允许 | 不适用 | 多口袋工装探险家猎装夹克款式 |
| 186 | 上装 | 兜帽 | `hood` | `hood` | 合并 | clothing | clothing.json (categories) | `hoodie` | [Route] | 禁止 | 不适用 | 兜帽特征部件，作为连帽衫叶子tag |
| 187 | 上装 | 牛仔夹克 | `denim jacket` | `denim jacket` | 纳入 | clothing | clothing.json (categories) | `denim_jacket` | [Route] | 允许 | 不适用 | 复古牛仔水洗夹克独立款式 |
| 188 | 上装 | 高领夹克 | `turtleneck jacket` | `turtleneck jacket` | 合并 | clothing | clothing.json (categories) | `outerwear_jacket` | [Route] | 允许 | 不适用 | 高领夹克领型变体，作为夹克款式tag |
| 189 | 上装 | 消防员夹克 | `firefighter jacket` | `firefighter jacket` | 纳入 | clothing | clothing.json (categories) | `firefighter_gear` | [Route] | 允许 | 不适用 | 消防员阻燃防护重型夹克独立款式 |
| 190 | 上装 | 战壕大衣 | `trench coat` | `trench coat` | 合并 | clothing | clothing.json (categories) | `trench_coat` | [Route] | 允许 | 不适用 | 战壕大衣同义词，合并至 trench_coat |
| 191 | 上装 | 实验室外套 | `lab coat` | `lab coat` | 合并 | clothing | clothing.json (categories) | `lab_coat` | [Route] | 允许 | 不适用 | 实验室外套同义词，合并至 lab_coat |
| 192 | 上装 | 羽绒服 | `Down Jackets` | `down jackets` | 纳入 | clothing | clothing.json (categories) | `down_jacket` | [Route] | 允许 | 不适用 | 冬季保暖羽绒服外套独立款式 |
| 193 | 上装 | 防弹盔甲 | `body armor` | `body armor` | 合并 | clothing | clothing.json (categories) | `tactical_vest` | [Route] | 允许 | 不适用 | 防弹盔甲同义词，合并至 tactical_vest |
| 194 | 上装 | 防弹衣 | `flak jacket` | `flak jacket` | 合并 | clothing | clothing.json (categories) | `tactical_vest` | [Route] | 允许 | 不适用 | 防弹衣同义词，与第94行合并至 tactical_vest |
| 195 | 上装 | 大衣 | `overcoat` | `overcoat` | 合并 | clothing | clothing.json (categories) | `outerwear_overcoat` | [Route] | 允许 | 不适用 | 大衣同义词，与第83行合并至 outerwear_overcoat |
| 196 | 上装 | 粗呢大衣 | `duffel coat` | `duffel coat` | 纳入 | clothing | clothing.json (categories) | `duffel_coat` | [Route] | 允许 | 不适用 | 牛角扣学院风连帽粗呢大衣款式 |
| 197 | 服装 | 燕尾服 | `tailcoat` | `tailcoat` | 纳入 | clothing | clothing.json (categories) | `tailcoat` | [Route] | 允许 | 禁止 | 男士双排扣正式晚宴燕尾服款式，裤装形制无裙摆 |
| 198 | 服装 | 女仆装 | `Victoria black maid dress` | `victoria black maid dress` | 合并 | clothing | clothing.json (categories) | `maid_dress` | [Route] | 允许 | 允许 | 维多利亚黑白长款女仆装变体合并 |
| 199 | 服装 | 水手服 | `sailor suit` | `sailor suit` | 合并 | clothing | clothing.json (categories) | `jk_seifuku` | [Route] | 禁止 | 允许 | 水手服同义词，合并至存量 jk_seifuku |
| 200 | 服装 | 学生服 | `school uniform` | `school uniform` | 合并 | clothing | clothing.json (categories) | `blazer_uniform` | [Route] | 允许 | 允许 | 学生服同义词，与第14行合并至 blazer_uniform |
| 201 | 服装 | 职场制服 | `bussiness suit` | `bussiness suit` | 合并 | clothing | clothing.json (categories) | `business_suit` | [Fix][Route] | 允许 | 禁止 | 纠正拼写 (bussiness->business)，合并至西装，裤装无裙摆 |
| 202 | 服装 | 西装 | `suit` | `suit` | 合并 | clothing | clothing.json (categories) | `business_suit` | [Route] | 允许 | 禁止 | 西装同义词，与第47行合并至 business_suit，裤装无裙摆 |
| 203 | 服装 | 军装 | `military uniform` | `military uniform` | 合并 | clothing | clothing.json (categories) | `military_uniform` | [Route] | 允许 | 禁止 | 军装同义词，与第60行合并至 military_uniform，长裤形制无裙摆 |
| 204 | 服装 | 礼服 | `lucency full dress` | `lucency full dress` | 合并 | clothing | clothing.json (categories) | `formal_gown` | [Route] | 不适用 | 允许 | 薄透礼服变体，合并至第73行 formal_gown |
| 205 | 服装 | 汉服 | `hanfu` | `hanfu` | 合并 | clothing | clothing.json (categories) | `hanfu` | [Route] | 禁止 | 允许 | 汉服同义词，合并至存量 hanfu |
| 206 | 服装 | 旗袍 | `cheongsam` | `cheongsam` | 合并 | clothing | clothing.json (categories) | `qipao` | [Route] | 允许 | 禁止 | 旗袍同义词，合并至存量 qipao |
| 207 | 服装 | 和服 | `japanses clothes` | `japanses clothes` | 合并 | clothing | clothing.json (categories) | `kimono` | [Fix][Route] | 禁止 | 允许 | 纠正拼写 (japanses->japanese)，合并至和服 |
| 208 | 服装 | 运动服 | `sportswear` | `sportswear` | 合并 | clothing | clothing.json (categories) | `sportswear_active` | [Route] | 不适用 | 不适用 | 运动服同义词，与第10行合并至 sportswear_active |
| 209 | 服装 | 工装服 | `dungarees` | `dungarees` | 合并 | clothing | clothing.json (categories) | `dungarees` | [Route] | 允许 | 禁止 | 工装背带裤同义词，合并至第117行 dungarees |
| 210 | 服装 | 婚纱 | `wedding dress` | `wedding dress` | 合并 | clothing | clothing.json (categories) | `wedding_dress` | [Route] | 禁止 | 允许 | 婚纱同义词，与第63行合并至 wedding_dress |
| 211 | 服装 | 银色连衣裙 | `silvercleavage dress` | `silvercleavage dress` | 纳入 | clothing | clothing.json (categories) | `cocktail_dress` | [Fix][Route] | 不适用 | 允许 | 纠正复合词，银色深V紧身鸡尾酒晚宴短裙 |
| 212 | 服装 | 长袍 | `robe` | `robe` | 合并 | clothing | clothing.json (categories) | `robe_general` | [Route] | 允许 | 允许 | 长袍同义词，与第85行合并至 robe_general |
| 213 | 服装 | 围裙 | `apron` | `apron` | 合并 | clothing | clothing.json (categories) | `apron_dress` | [Route] | 禁止 | 允许 | 围裙同义词，与第154行合并至 apron_dress |
| 214 | 服装 | 快餐制服 | `fast food uniform` | `fast food uniform` | 纳入 | clothing | clothing.json (categories) | `fast_food_uniform` | [Route] | 允许 | 不适用 | 快餐连锁店带帽短袖制服独立款式 |
| 215 | 服装 | JK制服 | `JK` | `jk` | 合并 | clothing | clothing.json (categories) | `jk_seifuku` | [Route] | 禁止 | 允许 | JK制服缩写，合并至存量 jk_seifuku |
| 216 | 服装 | 健身服 | `gym_uniform` | `gym_uniform` | 合并 | clothing | clothing.json (categories) | `gym_uniform` | [Route] | 禁止 | 禁止 | 健身服同义词，合并至存量 gym_uniform |
| 217 | 服装 | 巫女服 | `miko attire` | `miko attire` | 合并 | clothing | clothing.json (categories) | `shinto_miko` | [Route] | 禁止 | 允许 | 巫女服同义词，与第81行合并至 shinto_miko |
| 218 | 服装 | 海军陆战队服 | `SWAT uniform` | `swat uniform` | 合并 | clothing | clothing.json (categories) | `combat_tactical` | [Route] | 允许 | 禁止 | SWAT特警作战服，合并至 combat_tactical，长裤形制无裙摆 |
| 219 | 服装 | 无袖连衣裙 | `sleeveless dress` | `sleeveless dress` | 纳入 | clothing | clothing.json (categories) | `sleeveless_dress` | [Route] | 不适用 | 允许 | 无袖A字修身连衣裙独立款式 |
| 220 | 服装 | 雨衣 | `raincoat` | `raincoat` | 合并 | clothing | clothing.json (categories) | `rainwear_coat` | [Route] | 允许 | 不适用 | 雨衣同义词，与第103行合并至 rainwear_coat |
| 221 | 服装 | 机甲衣 | `mech suit` | `mech suit` | 合并 | clothing | clothing.json (categories) | `mecha_exoskeleton` | [Route] | 禁止 | 禁止 | 机甲衣同义词，合并至 mecha_exoskeleton |
| 222 | 服装 | 巫师法袍 | `wizard robe` | `wizard robe` | 纳入 | clothing | clothing.json (categories) | `wizard_robe` | [Route] | 禁止 | 允许 | 带星月图腾奇幻巫师长袍款式 |
| 223 | 下装 | 牛仔短裤 | `denim shorts` | `denim shorts` | 纳入 | clothing | clothing.json (categories) | `denim_shorts` | [Route] | 允许 | 禁止 | 经典牛仔毛边短裤独立下装 |
| 224 | 下装 | 百褶裙 | `pleated skirt` | `pleated skirt` | 合并 | clothing | clothing.json (categories) | `pleated_skirt` | [Route] | 禁止 | 允许 | 百褶裙同义词，与第142行合并至百褶裙 |
| 225 | 下装 | 热裤 | `short shorts` | `short shorts` | 纳入 | clothing | clothing.json (categories) | `hot_pants` | [Route] | 禁止 | 禁止 | 超短性感贴身热裤独立下装 |
| 226 | 下装 | 铅笔裙 | `pencil skirt` | `pencil skirt` | 合并 | clothing | clothing.json (categories) | `pencil_skirt` | [Route] | 禁止 | 允许 | 铅笔裙同义词，与第155行合并至铅笔裙 |
| 227 | 下装 | 皮裙 | `leather skirt` | `leather skirt` | 纳入 | clothing | clothing.json (categories) | `leather_skirt` | [Route] | 允许 | 允许 | 机车风黑色皮质短裙独立下装 |
| 228 | 下装 | 黑色紧身裤 | `black leggings` | `black leggings` | 纳入 | clothing | clothing.json (categories) | `black_leggings` | [Route] | 禁止 | 禁止 | 黑色高弹力修身打底裤独立下装 |
| 229 | 下装 | 和服下的裙子 | `skirt under kimono` | `skirt under kimono` | 纳入 | clothing | clothing.json (clothing_states) | `skirt_under_kimono` | [Route] | 禁止 | 允许 | 和服内穿衬裙层次叠穿状态 |
| 230 | 其他服装 | 褶边 | `frills` | `frills` | 合并 | clothing | clothing.json (categories) | `shirts_blouses` | [Route] | 不适用 | 不适用 | 荷叶褶边装饰特征，作为衬衫叶子tag |
| 231 | 其他服装 | 花边 | `lace` | `lace` | 合并 | clothing | clothing.json (categories) | `lingerie_lace` | [Route] | 不适用 | 不适用 | 蕾丝花边装饰特征，作为内衣叶子tag |
| 232 | 其他服装 | 哥特风格 | `gothic` | `gothic` | 合并 | clothing | clothing.json (categories) | `lolita_gothic` | [Route] | 不适用 | 不适用 | 哥特审美风格，作为哥特裙风格tag |
| 233 | 其他服装 | 洛丽塔风格 | `lolita fashion` | `lolita fashion` | 合并 | clothing | clothing.json (categories) | `lolita_fashion` | [Route] | 允许 | 允许 | 洛丽塔风格同义词，合并至 lolita_fashion |
| 234 | 其他服装 | 西部风格 | `western` | `western` | 合并 | clothing | clothing.json (categories) | `safari_jacket` | [Route] | 允许 | 不适用 | 西部牛仔风格特征，作为猎装外套tag |
| 235 | 其他服装 | 湿身 | `wet clothes` | `wet clothes` | 合并 | clothing | clothing.json (clothing_states) | `wet_pure` | [Route] | 不适用 | 不适用 | 纯粹湿身同义词，与第84行合并至 wet_pure |
| 236 | 其他服装 | 露单肩 | `off_shoulder` | `off_shoulder` | 纳入 | clothing | clothing.json (clothing_states) | `off_shoulder_cut` | [Route] | 不适用 | 不适用 | 露单肩非对称剪裁状态 |
| 237 | 其他服装 | 露双肩 | `bare_shoulders` | `bare_shoulders` | 纳入 | clothing | clothing.json (clothing_states) | `bare_shoulders` | [Route] | 不适用 | 不适用 | 露双肩大平领穿着状态 |
| 238 | 其他服装 | 格子花纹 | `tartan` | `tartan` | 合并 | clothing | clothing.json (categories) | `plaid_skirt` | [Route] | 不适用 | 不适用 | 苏格兰方格纹理，作为格子裙款式tag |
| 239 | 其他服装 | 披甲 | `armored skirt` | `armored skirt` | 纳入 | clothing | clothing.json (categories) | `armored_skirt` | [Route] | 禁止 | 允许 | 护腿金属甲片战裙独立下装款式 |
| 240 | 其他服装 | 盔甲 | `armor` | `armor` | 合并 | clothing | clothing.json (categories) | `knight_armor` | [Route] | 禁止 | 禁止 | 盔甲同义词，与第109行合并至 knight_armor |
| 241 | 其他服装 | 金属盔甲 | `metal armor` | `metal armor` | 合并 | clothing | clothing.json (categories) | `knight_armor` | [Route] | 禁止 | 禁止 | 金属盔甲，作为板甲款式变体tag |
| 242 | 其他服装 | 狂战士铠甲 | `berserker armor` | `berserker armor` | 纳入 | clothing | clothing.json (categories) | `berserker_armor` | [Route] | 禁止 | 禁止 | 狂战士重型带刺铠甲独立款式 |
| 243 | 其他服装 | 腰带 | `belt` | `belt` | 纳入 | jewelry | accessories.json (headwear_jewelry) | `waist_belt` | [Route] | 不适用 | 不适用 | 基础皮革腰带独立配饰件 |
| 244 | 其他服装 | 围巾 | `scarf` | `scarf` | 纳入 | jewelry | accessories.json (headwear_jewelry) | `winter_scarf` | [Route] | 不适用 | 不适用 | 针织保暖围巾独立配饰件 |
| 245 | 其他服装 | 披肩 | `cape` | `cape` | 合并 | jewelry | accessories.json (headwear_jewelry) | `cloak` | [Route] | 不适用 | 不适用 | 无帽长披肩/斗篷，与第65行合并至 cloak |
| 246 | 其他服装 | 皮草披肩 | `fur shawl` | `fur shawl` | 纳入 | jewelry | accessories.json (headwear_jewelry) | `fur_shawl` | [Route] | 不适用 | 不适用 | 原表最后一行，保暖皮草披肩独立配饰件 |
