# ComfyUI-IYKYK 全量选择器归属与分级治理总表 (M1-D3)

- **明细台账文件**：[`docs/m1_selector_attribution_ledger.tsv`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/docs/m1_selector_attribution_ledger.tsv)（TSV 格式，含全部 224 个核心选择器的逐项归属台账）
- **审核覆盖率**：**100.0%**（已审核 224 / 总计 224，**尚未审核 = 0**）
- **核心槽位范围**：服装选择器 165 个（`clothing.json`）+ 光影选择器 59 个（`lighting.json`）

---

## 一、审核处置分类与原则说明

依据审核方指导准则，本次审核严格杜绝“未明确归属即假定无亲和”的模糊状态，明确划分为两大正向决策状态：

1. **`assigned_contexts`（定向情境加权候选，共 114 个条目）**：
   - 具备明确的情境空间、文化形制、使用功能或典型视觉关联；
   - 依据强弱关系分配 `4（强关联）`、`2（适配）`、`1（弱关联）` 权重；
   - 仅在其所属情境处于激活或混合状态时获得加权提升。
2. **`intentional_global_only`（有意仅保留全局探索，共 110 个条目）**：
   - 经过 100% 人工/语义逐项审查，确认该条目**无特定排他性情境偏向**（如通用基础日常打底衫、中性纯白短袖、多场景适用的标准影棚商业布光）；
   - **有意不纳入任何单一情境加权池**，但完整保留在系统 `0.15` 全局采样池中；
   - 保证系统在保持情境自洽的前提下，仍具备跨情境搭配与偶发创新的自由度，绝非“遗漏未审”。
3. **`generic` 中性分布统一保障**：
   - 通用情境 `generic` 对全部合法选择器实施均权无偏分布，杜绝向特定款式注入隐式偏好。

---

## 二、槽位归属宏观统计分布

| 槽位 (Slot) | 总选择器数 | 定向加权候选 (`assigned_contexts`) | 有意仅保留全局探索 (`intentional_global_only`) | 尚未审核 (`pending`) | 审核完成度 |
|---|---|---|---|---|---|
| **服装 (clothing)** | 165 | 86 (52.1%) | 79 (47.9%) | **0 (0.0%)** | **100.0%** |
| **光影 (lighting)** | 59 | 28 (47.5%) | 31 (52.5%) | **0 (0.0%)** | **100.0%** |
| **合计** | **224** | **114 (50.9%)** | **110 (49.1%)** | **0 (0.0%)** | **100.0%** |

---

## 三、典型选择器归属示例摘录

| 选择器 ID | 槽位 | 中文名称 | 当前情境数 | 处置决定 | 拟分配情境与权重 | 归属理由依据 |
|---|---|---|---|---|---|---|
| `jk_seifuku` | clothing | 水手服/JK制服 | 2 | `assigned_contexts` | `school:4,transit:2` | 典型日本女高中生制服，强绑定校园，适配通学电车/公交通勤。 |
| `sailor_summer` | clothing | 夏季水手服 | 0 | `assigned_contexts` | `school:4,transit:2` | 原亲和池遗漏的夏装校园核心制服，补充入校园强关联池。 |
| `doctor_coat` | clothing | 白大褂/医师袍 | 0 | `assigned_contexts` | `medical:4` | 彻底修复医疗情境仅护士服单一款式的枯竭问题，强关联医疗。 |
| `scrubs` | clothing | 洗手服/手术服 | 0 | `assigned_contexts` | `medical:4` | 医护手术室、急诊典型职能服饰，强关联医疗。 |
| `street_casual` | clothing | 街头休闲便服 | 4 | `assigned_contexts` | `outdoor:2,transit:2,domestic:1` | 通用日常休闲，适度作为户外、通勤与日常中性过渡。 |
| `tshirt_basic_white` | clothing | 基础款白T恤 | 0 | `intentional_global_only` | `none` | 基础百搭打底，无特定空间排他偏向，有意仅保留在全局池供自由采样。 |
| `lighting_sunlight_classroom_window` | lighting | 教室窗前日光 | 0 | `assigned_contexts` | `school:4,outdoor:2` | 教室与书桌窗前自然天光，强绑定校园室内与室外清晨。 |
| `lighting_shadowless_surgical` | lighting | 无影手术灯光 | 0 | `assigned_contexts` | `medical:4` | 医疗与诊疗室典型硬光源，强绑定医疗情境。 |
| `lighting_studio_commercial` | lighting | 商业影棚标准光 | 4 | `intentional_global_only` | `none` | 中性专业写真与标准打光，无排他场所属性，保留全局探索。 |

完整 224 条选择器的逐项处置、权重与理由详见配套 [`docs/m1_selector_attribution_ledger.tsv`](file:///Users/jacobyang/Hermes/iykyk-affinity-override/docs/m1_selector_attribution_ledger.tsv)。
