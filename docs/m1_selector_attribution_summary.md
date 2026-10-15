# ComfyUI-IYKYK 全量选择器归属与分级治理总表 (M1-D3 修订版)

- **明细台账文件**：[`docs/m1_selector_attribution_ledger.tsv`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/docs/m1_selector_attribution_ledger.tsv)（包含全部 14 个采样槽位、496 个选择器的逐项归属台账）
- **审核覆盖率**：**100.0%**（已审核 496 / 总计 496，**尚未审核 = 0**）
- **槽位覆盖**：全部 14 个采样槽位（服装 165、光影 59、发型 57、胶片 39、情绪 31、饰品 29、道具 23、妆容 17、景别 16、视角 15、姿势 15、纹身 12、角色 11、液体 7）

---

## 一、审核处置分类与原则说明

1. **`assigned_contexts`（定向情境加权候选，共 191 项 / 38.5%）**：
   - 具备明确的空间场所、文化形制、使用职能或视觉典型关联；
   - 依据强弱关系分配 `4（强关联）`、`2（适配）`、`1（弱关联）` 权重；
   - 仅在其所属情境处于激活或混合状态时获得加权提升。
2. **`intentional_global_only`（有意仅保留全局探索，共 305 项 / 61.5%）**：
   - 经过 100% 逐项语义审查，确认该条目**无特定排他性情境偏向**（如通用基础日常短袖、中性牛仔裤、通用平视视角、中性三点布光、无特定情绪偏向的写实相纸）；
   - **有意不纳入任何单一情境加权池**，但完整保留在系统 `0.15` 全局采样池中；
   - 保证系统在保持情境自洽的前提下，仍具备跨情境搭配与偶发创新的自由度，绝非“遗漏未审”。
3. **彻底纠正启发式子串匹配误判**：
   - 纠正了上一轮将 `one_piece_swimsuit`（泳衣）与 `latex_catsuit`（乳胶衣）因包含 `suit` 子串而误判为“职场正装”的严重缺陷；
   - 经逐项语义核实：`one_piece_swimsuit` 正确归属于 `outdoor:2, onsen_bath:2, adult:2`；`latex_catsuit` 正确归属于 `bondage_sm:4, adult:2, special:2`。

---

## 二、14 个槽位 496 选择器宏观统计分布全景表

| 槽位 (Slot) | 注册选择器总数 | 定向加权候选 (`assigned_contexts`) | 有意仅保留全局探索 (`intentional_global_only`) | 尚未审核 (`pending`) | 审核完成度 |
|---|---|---|---|---|---|
| **服装 (clothing)** | 165 | 72 | 93 | **0** | **100.0%** |
| **光影 (lighting)** | 59 | 24 | 35 | **0** | **100.0%** |
| **发型 (hairstyle)** | 57 | 16 | 41 | **0** | **100.0%** |
| **胶片 (film)** | 39 | 12 | 27 | **0** | **100.0%** |
| **情绪 (expression)** | 31 | 13 | 18 | **0** | **100.0%** |
| **饰品 (jewelry)** | 29 | 16 | 13 | **0** | **100.0%** |
| **道具 (props)** | 23 | 14 | 9 | **0** | **100.0%** |
| **妆容 (makeup)** | 17 | 14 | 3 | **0** | **100.0%** |
| **景别 (shot_type)** | 16 | 6 | 10 | **0** | **100.0%** |
| **视角 (camera_angle)** | 15 | 8 | 7 | **0** | **100.0%** |
| **姿势 (pose)** | 15 | 9 | 6 | **0** | **100.0%** |
| **纹身 (tattoo)** | 12 | 6 | 6 | **0** | **100.0%** |
| **角色 (character)** | 11 | 9 | 2 | **0** | **100.0%** |
| **液体 (liquids)** | 7 | 4 | 3 | **0** | **100.0%** |
| **合计** | **496** | **191 (38.5%)** | **305 (61.5%)** | **0 (0.0%)** | **100.0%** |

---

## 三、典型槽位代表性选择器归属核定

| 槽位 | 选择器 ID | 中文名称 | 当前情境数 | 处置决定 | 拟分配情境与权重 | 归属与排除理由 |
|---|---|---|---|---|---|---|
| clothing | `jk_seifuku` | 水手服/JK制服 | 2 | `assigned_contexts` | `school:4,transit:2` | 典型日本女高中生制服，强绑定校园，适配通学交通。 |
| clothing | `sailor_shirt` | 水手服上衣 (真实ID) | 0 | `assigned_contexts` | `school:4,transit:2` | 真实目录款式，补充入校园制服强关联池。 |
| clothing | `one_piece_swimsuit` | 高叉连体泳衣 | 2 | `assigned_contexts` | `outdoor:2,onsen_bath:2,adult:2` | 修正子串误判：正确定位为水域与私密泳装，绝非职场。 |
| clothing | `latex_catsuit` | 乳胶紧身衣 | 4 | `assigned_contexts` | `bondage_sm:4,adult:2,special:2` | 修正子串误判：正确定位为特种束缚与成人私密，绝非职场。 |
| clothing | `ol_suit` | OL通勤正装套裙 | 2 | `assigned_contexts` | `office:4,transit:2` | 职场标准通勤正装，强关联办公室与通勤电车。 |
| clothing | `t_shirt` | 基础短袖T恤 | 0 | `intentional_global_only` | `none` | 基础百搭中性款，无排他情境绑定，有意仅保留在全局池 (15%)。 |
| lighting | `lighting_japanese_fresh` | 日系清新自然光 | 5 | `assigned_contexts` | `school:4,outdoor:4` | 清新通透自然光，强关联校园日间与室外。 |
| lighting | `prof_light_fluorescent` | 荧光灯顶光 (真实ID) | 0 | `assigned_contexts` | `office:4,medical:4,transit:2` | 真实目录光源：典型写字楼、医院科室与地铁车厢日光灯。 |
| lighting | `ext_aw_light_warm_golden_hour` | 黄金时段暖光 (真实ID) | 0 | `assigned_contexts` | `outdoor:4,school:2` | 真实目录光源：户外日落与放学黄昏绝美斜射日光。 |
| lighting | `prof_light_three_point` | 三点标准影棚光 (真实ID) | 0 | `intentional_global_only` | `none` | 中性商业写真三点布光，有意保留全局探索池自由抽样。 |
| props | `digital_camera_in_hand` | 手持数码相机 (真实ID) | 0 | `assigned_contexts` | `outdoor:2,domestic:2,nightlife:2` | 街拍出行与生活自拍，适配户外与夜生活。 |
| props | `student_leather_bag` | 学生皮革书包 | 1 | `assigned_contexts` | `school:4` | 专属学生书包，强关联校园。 |
| character | `jk_schoolgirl` | 女高中生 | 1 | `assigned_contexts` | `school:4,transit:2` | 专属女高中生角色，强关联校园与通学出行。 |
| character | `female_boss` | 女上司/女主管 | 1 | `assigned_contexts` | `office:4,transit:2` | 专属职场角色，强关联写字楼。 |

全部 496 条选择器的逐项审核结论详见配套台账文件 [`docs/m1_selector_attribution_ledger.tsv`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/docs/m1_selector_attribution_ledger.tsv)。
