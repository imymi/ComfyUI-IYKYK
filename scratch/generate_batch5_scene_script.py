#!/usr/bin/env python3
"""
scratch/generate_batch5_scene_script.py
严谨生成场景环境库批次 5 (2,335 条实体, SRC_SCENE_04277 ~ SRC_SCENE_06611) 审核与映射脚本 scratch/apply_m32_batch5_scene.py：

严格遵循用户三项实施原则与 M3.2 语义台账规范：
1. [原则 1] 62 条同名候选上下文复核与基线时间/空间属性解耦：
   - 经逐条核对基线父条目语义，37 条确属合法复用 (REUSE_EXISTING)；
   - 25 条同名但基线父级严重错配 (如 river/lake 挂在 bathtub 下、swimming pool 挂在 bowling alley 下、zoo 挂在 aquarium 下、warehouse 挂在 SM locked room 下、bus stop 挂在 highway bus 下) 判定为 NEW_STYLE；
   - 来源未明文说明时间约束的条目，一律记录 time_of_day: "unspecified"，绝不硬编码常见情境（如 river/lake/casino/zoo/swimming pool/classroom 等全部清除无依据的 day/night）；
   - library (SRC_SCENE_06095) 核心事实记录 venue_ids: ["library"], time_of_day: "unspecified"，基线历史偏好写入 context_affinity: ["school"], baseline_default_time_of_day: "day"，绝不在核心事实中强制限定为学校且白天。
2. [原则 2] 按场所本身分类与真实空间关系判定：
   - abandoned pier 严格归入码头/水岸 (waterfront_pier)，保留 condition: "abandoned"，严禁因包含 abandoned 错误塞入废弃建筑；
   - 空间属性 (space_kind) 按物理实体与真实空间关系判定（全词/词界匹配）：
     - 热气球越过峡谷 (hot air balloon ride over a scenic canyon)、雪山顶 (snowy mountaintop) 标定为 outdoor，绝不默认 indoor；
     - supermarket 优先全词匹配至 commercial_retail (indoor)，绝不被 market 劫持至 urban_streetscape (outdoor)；
     - 无法确定室内/户外时，严格保留 space_kind: "unspecified"。
3. [原则 3] 复合场景明确拆分与无场所条目隔离：
   - 无明确物理场所的活动、旅途与事件条目 (如 ice skating 求婚、stargazing 求婚、bike ride 接吻、road trip 接吻、scenic drive 求婚、丧尸爆发幸存者、黄金城探险)，标记 DEFERRED_ISSUE 隔离入 quarantine，严禁凭空生成虚假场所映射；
   - 复合探险/寻宝叙事 (a group of ... to find <lost treasure>)：
     - 剔除行为与任务目的后提取纯净场所 (如 a mysterious and treacherous swamp, an ancient temple, a magical desert)；
     - 任务目的记录在 semantic_facts_json 的 deconstructed_event.quest_objective 中。
   - 叙事型整身长句模板 (BKWILDCARDS 837 条)：
     - 解构为 venue_name、space_kind、embedded_props、embedded_palette、embedded_lighting 五大成分并标定 slot_attribution。
4. 纯剧情/非空间抽象条目隔离 (DEFERRED_ISSUE)：
   - 涵盖抽象设定、任务情节、种族/战争概念等无实体空间场所的条目，一律隔离排除入 quarantine。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
import re
from collections import defaultdict, Counter

REPO_DIR = Path(__file__).resolve().parent.parent

def slugify(text: str) -> str:
    s = re.sub(r'[^a-zA-Z0-9]+', '_', text).strip('_').lower()
    return s.rstrip('_')

# ==============================================================================
# 1. 隔离待决议非空间/纯剧情/无场地抽象条目 (DEFERRED_ISSUE, 71条)
# ==============================================================================
DEFERRED_ITEMS = {
    # --- 1A. 用户审查明确指出的无物理场所活动/事件条目 (7条) ---
    "SRC_SCENE_05281": ("a surprise proposal while ice skating", "ACTIVITY_WITHOUT_VENUE", "仅含求婚交互与滑冰运动动作，缺少具体物理空间场所名称，隔离排除出场景库"),
    "SRC_SCENE_05282": ("a surprise proposal while stargazing", "ACTIVITY_WITHOUT_VENUE", "仅含求婚交互与观星行为动作，缺少具体物理空间场所名称，隔离排除出场景库"),
    "SRC_SCENE_05057": ("a romantic kiss on a scenic bike ride", "ACTIVITY_WITHOUT_VENUE", "仅含接吻与自行车骑行行程，缺少具体物理空间场所名称，隔离排除出场景库"),
    "SRC_SCENE_05059": ("a romantic kiss on a scenic road trip", "ACTIVITY_WITHOUT_VENUE", "仅含接吻与公路旅行旅程，缺少具体物理空间场所名称，隔离排除出场景库"),
    "SRC_SCENE_05273": ("a surprise proposal on a scenic drive", "ACTIVITY_WITHOUT_VENUE", "仅含求婚交互与汽车驾驶路程，缺少具体物理空间场所名称，隔离排除出场景库"),
    "SRC_SCENE_04689": ("a group of survivors in a zombie outbreak", "EVENT_WITHOUT_VENUE", "仅含幸存者群体与丧尸爆发危机事件背景，缺少具体物理空间场所名称，隔离排除出场景库"),
    "SRC_SCENE_04684": ("a group of explorers set out to find a legendary city made of gold", "QUEST_WITHOUT_VENUE", "仅含探险队出发寻找黄金城的任务动机，缺少当前具体物理空间场所，隔离排除出场景库"),

    # --- 1B. 纯任务/寻找宝物抽象题材 (无物理空间, 5条) ---
    "SRC_SCENE_04982": ("a quest for a legendary sword", "NON_SPATIAL_QUEST_HOOK", "上游词表将寻找传说之剑的任务情节误分入场景库，无实体场所名词，隔离排除"),
    "SRC_SCENE_04983": ("a quest to defeat an ancient evil", "NON_SPATIAL_QUEST_HOOK", "上游词表将击败远古邪恶的任务情节误分入场景库，无实体场所名词，隔离排除"),
    "SRC_SCENE_04984": ("a quest to find a magical grail", "NON_SPATIAL_QUEST_HOOK", "上游词表将寻找圣杯的任务情节误分入场景库，无实体场所名词，隔离排除"),
    "SRC_SCENE_04985": ("a quest to retrieve a powerful artifact", "NON_SPATIAL_QUEST_HOOK", "上游词表将寻找神器的任务情节误分入场景库，无实体场所名词，隔离排除"),
    "SRC_SCENE_04986": ("a quest to save a magical kingdom", "NON_SPATIAL_QUEST_HOOK", "上游词表将拯救王国的任务情节误分入场景库，无实体场所名词，隔离排除"),

    # --- 1C. 诅咒物品与超自然超能力抽象物 (非场景环境, 8条) ---
    "SRC_SCENE_04459": ("a cursed object brings death and destruction", "NON_SPATIAL_PLOT_HOOK", "上游词表将诅咒物品抽象剧情误分入场景库，不含空间场所环境描述，隔离排除"),
    "SRC_SCENE_04460": ("a cursed object brings death to its owner", "NON_SPATIAL_PLOT_HOOK", "上游词表将诅咒物品抽象剧情误分入场景库，不含空间场所环境描述，隔离排除"),
    "SRC_SCENE_04461": ("a cursed object brings inanimate objects to life", "NON_SPATIAL_PLOT_HOOK", "上游词表将诅咒物品抽象设定误分入场景库，不含空间场所环境描述，隔离排除"),
    "SRC_SCENE_04462": ("a cursed object brings misfortune to life", "NON_SPATIAL_PLOT_HOOK", "上游词表将诅咒物品抽象剧情误分入场景库，不含空间场所环境描述，隔离排除"),
    "SRC_SCENE_04463": ("a cursed object with deadly powers", "NON_SPATIAL_PLOT_HOOK", "上游词表将道具属性设定误分入场景库，不含空间场所环境描述，隔离排除"),
    "SRC_SCENE_04464": ("a cursed object with mysterious powers", "NON_SPATIAL_PLOT_HOOK", "上游词表将道具属性设定误分入场景库，不含空间场所环境描述，隔离排除"),
    "SRC_SCENE_04465": ("a cursed object with reality-bending powers", "NON_SPATIAL_PLOT_HOOK", "上游词表将道具属性设定误分入场景库，不含空间场所环境描述，隔离排除"),
    "SRC_SCENE_04466": ("a cursed object with supernatural power", "NON_SPATIAL_PLOT_HOOK", "上游词表将道具属性设定误分入场景库，不含空间场所环境描述，隔离排除"),

    # --- 1D. 纯角色冒险/讨伐任务情节 (无明确物理场所, 6条) ---
    "SRC_SCENE_04686": ("a group of knights set out to defeat a powerful sorcerer who threatens to destroy the world", "CHARACTER_QUEST_PLOT", "上游词表将骑士讨伐巫师剧情误分入场景库，无实体场所名词，隔离排除"),
    "SRC_SCENE_04687": ("a group of rebels attempt to overthrow a tyrannical king who possesses a powerful magical artifact", "CHARACTER_QUEST_PLOT", "上游词表将起义军推翻暴君剧情误分入场景库，无实体场所名词，隔离排除"),
    "SRC_SCENE_04696": ("a group of travelers journey to a faraway land to find a powerful sorcerer who can help them defeat a great evil", "CHARACTER_QUEST_PLOT", "上游词表将旅人寻找巫师任务误分入场景库，无实体场所名词，隔离排除"),
    "SRC_SCENE_04697": ("a group of warriors set out to defeat a powerful necromancer who seeks to conquer the world", "CHARACTER_QUEST_PLOT", "上游词表将战士讨伐死灵法师误分入场景库，无实体场所名词，隔离排除"),
    "SRC_SCENE_04698": ("a group of warriors set out to defeat an immortal warlord who seeks to conquer the world and enslave mankind", "CHARACTER_QUEST_PLOT", "上游词表将战士对抗军阀剧情误分入场景库，无实体场所名词，隔离排除"),
    "SRC_SCENE_04699": ("a group of warriors set out to find and destroy an ancient cursed weapon before it falls into the wrong hands", "CHARACTER_QUEST_PLOT", "上游词表将战士销毁武器剧情误分入场景库，无实体场所名词，隔离排除"),

    # --- 1E. 角色人设与种族冲突抽象剧情 (5条) ---
    "SRC_SCENE_04763": ("a human-alien hybrid fights for acceptance", "CHARACTER_IDENTITY_PLOT", "上游词表将人外混血剧情误分入场景库，属角色设定，隔离排除"),
    "SRC_SCENE_04764": ("a human-alien hybrid fights for freedom", "CHARACTER_IDENTITY_PLOT", "上游词表将人外混血斗争剧情误分入场景库，属角色设定，隔离排除"),
    "SRC_SCENE_04765": ("a human-like robots are hunted by humanity", "CHARACTER_IDENTITY_PLOT", "上游词表将人形机器人被捕猎剧情误分入场景库，属角色关系，隔离排除"),
    "SRC_SCENE_04766": ("a human-like robots demand equal rights", "CHARACTER_IDENTITY_PLOT", "上游词表将机器人平权运动误分入场景库，属世界观设定，隔离排除"),
    "SRC_SCENE_04767": ("a human-like robots gain consciousness", "CHARACTER_IDENTITY_PLOT", "上游词表将机器人觉醒剧情误分入场景库，属角色剧情，隔离排除"),

    # --- 1F. 科学实验与超自然事件抽象叙事 (4条) ---
    "SRC_SCENE_04816": ("a malfunctioning teleportation experiment", "ABSTRACT_SCIENCE_CONCEPT", "上游词表将传送实验故障抽象事件误分入场景库，无具体场所名词，隔离排除"),
    "SRC_SCENE_04817": ("a malfunctioning teleportation experiment leads to disaster", "ABSTRACT_SCIENCE_CONCEPT", "上游词表将传送灾难情节误分入场景库，无具体场所名词，隔离排除"),
    "SRC_SCENE_05004": ("a reality-bending supernatural experiment", "ABSTRACT_SCIENCE_CONCEPT", "上游词表将超现实实验抽象设定误分入场景库，无实体空间名词，隔离排除"),
    "SRC_SCENE_05240": ("a supernatural political campaign", "ABSTRACT_SOCIAL_CONCEPT", "上游词表将超自然政治竞选抽象概念误分入场景库，无实体空间，隔离排除"),

    # --- 1G. 科学家创造造物剧情 (4条) ---
    "SRC_SCENE_05114": ("a scientist creates a creature that turns on him", "CHARACTER_PLOT_HOOK", "上游词表将科学家造物反噬剧情误分入场景库，无空间名词，隔离排除"),
    "SRC_SCENE_05116": ("a scientist creates a new form of life", "CHARACTER_PLOT_HOOK", "上游词表将科学家创造生命剧情误分入场景库，无空间名词，隔离排除"),
    "SRC_SCENE_05117": ("a scientist creates a new species", "CHARACTER_PLOT_HOOK", "上游词表将科学家创造物种剧情误分入场景库，无空间名词，隔离排除"),
    "SRC_SCENE_05118": ("a scientist creates a time machine", "CHARACTER_PLOT_HOOK", "上游词表将科学家造时光机剧情误分入场景库，无空间名词，隔离排除"),

    # --- 1H. 秘密结社/社团人设清单 (12条) ---
    "SRC_SCENE_05132": ("a secret society of alchemists", "CHARACTER_FACTION_LORE", "上游词表将炼金术士秘密结社阵营误分入场景库，非空间场所，隔离排除"),
    "SRC_SCENE_05133": ("a secret society of immortal humans", "CHARACTER_FACTION_LORE", "上游词表将永生者组织误分入场景库，非空间场所，隔离排除"),
    "SRC_SCENE_05134": ("a secret society of magic wielders", "CHARACTER_FACTION_LORE", "上游词表将施法者结社误分入场景库，非空间场所，隔离排除"),
    "SRC_SCENE_05135": ("a secret society of powerful magic wielders fights a mysterious force", "CHARACTER_FACTION_LORE", "上游词表将结社战斗剧情误分入场景库，非空间场所，隔离排除"),
    "SRC_SCENE_05136": ("a secret society of powerful witches", "CHARACTER_FACTION_LORE", "上游词表将女巫结社误分入场景库，非空间场所，隔离排除"),
    "SRC_SCENE_05137": ("a secret society of shapeshifters", "CHARACTER_FACTION_LORE", "上游词表将变形者结社误分入场景库，非空间场所，隔离排除"),
    "SRC_SCENE_05138": ("a secret society of shapeshifting humans", "CHARACTER_FACTION_LORE", "上游词表将变形者社团误分入场景库，非空间场所，隔离排除"),
    "SRC_SCENE_05139": ("a secret society of supernatural beings", "CHARACTER_FACTION_LORE", "上游词表将超自然社团误分入场景库，非空间场所，隔离排除"),
    "SRC_SCENE_05140": ("a secret society of time travelers", "CHARACTER_FACTION_LORE", "上游词表将时间旅行者社团误分入场景库，非空间场所，隔离排除"),
    "SRC_SCENE_05141": ("a secret society of vampire nobles", "CHARACTER_FACTION_LORE", "上游词表将吸血鬼贵族社团误分入场景库，非空间场所，隔离排除"),
    "SRC_SCENE_05142": ("a secret society of witches and warlocks", "CHARACTER_FACTION_LORE", "上游词表将男女巫结社误分入场景库，非空间场所，隔离排除"),
    "SRC_SCENE_05143": ("a secret society of witches in modern times", "CHARACTER_FACTION_LORE", "上游词表将现代女巫结社误分入场景库，非空间场所，隔离排除"),

    # --- 1I. 世界观设定抽象描述 (8条) ---
    "SRC_SCENE_05181": ("a society where people can communicate telepathically", "ABSTRACT_WORLDBUILDING", "上游词表将心灵感应社会设定误分入场景库，非实体空间，隔离排除"),
    "SRC_SCENE_05182": ("a society where people can control gravity", "ABSTRACT_WORLDBUILDING", "上游词表将重力控制社会设定误分入场景库，非实体空间，隔离排除"),
    "SRC_SCENE_05183": ("a society where people can control the elements", "ABSTRACT_WORLDBUILDING", "上游词表将元素控制社会设定误分入场景库，非实体空间，隔离排除"),
    "SRC_SCENE_05184": ("a society where people can control their dreams", "ABSTRACT_WORLDBUILDING", "上游词表将梦境控制社会设定误分入场景库，非实体空间，隔离排除"),
    "SRC_SCENE_05185": ("a society where people can genetically enhance themselves to have extraordinary abilities", "ABSTRACT_WORLDBUILDING", "上游词表将基因强化社会设定误分入场景库，非实体空间，隔离排除"),
    "SRC_SCENE_05186": ("a society where people can teleport", "ABSTRACT_WORLDBUILDING", "上游词表将传送社会设定误分入场景库，非实体空间，隔离排除"),
    "SRC_SCENE_05187": ("a society where people can upload their consciousness to a virtual world", "ABSTRACT_WORLDBUILDING", "上游词表将意识上传设定误分入场景库，非实体空间，隔离排除"),
    "SRC_SCENE_05188": ("a society where people's memories can be edited or erased", "ABSTRACT_WORLDBUILDING", "上游词表将记忆擦除设定误分入场景库，非实体空间，隔离排除"),

    # --- 1J. 时间旅行者剧情与困境 (7条) ---
    "SRC_SCENE_05303": ("a time traveler changes history unintentionally", "TIME_TRAVEL_PLOT_HOOK", "上游词表将改变历史剧情误分入场景库，无实体场所，隔离排除"),
    "SRC_SCENE_05304": ("a time traveler changes the past", "TIME_TRAVEL_PLOT_HOOK", "上游词表将改变过去剧情误分入场景库，无实体场所，隔离排除"),
    "SRC_SCENE_05310": ("a time traveler's conundrum", "TIME_TRAVEL_PLOT_HOOK", "上游词表将时间旅行者难题误分入场景库，无实体场所，隔离排除"),
    "SRC_SCENE_05311": ("a time traveler's mission to prevent a disaster", "TIME_TRAVEL_PLOT_HOOK", "上游词表将阻止灾难剧情误分入场景库，无实体场所，隔离排除"),
    "SRC_SCENE_05312": ("a time traveler's mission to save humanity", "TIME_TRAVEL_PLOT_HOOK", "上游词表将拯救人类剧情误分入场景库，无实体场所，隔离排除"),
    "SRC_SCENE_05313": ("a time traveler's struggle to change the future", "TIME_TRAVEL_PLOT_HOOK", "上游词表将改变未来剧情误分入场景库，无实体场所，隔离排除"),
    "SRC_SCENE_05314": ("a time traveler's struggle to prevent a catastrophic future", "TIME_TRAVEL_PLOT_HOOK", "上游词表将阻止浩劫剧情误分入场景库，无实体场所，隔离排除"),

    # --- 1K. 宏观战争抽象题材 (2条) ---
    "SRC_SCENE_05361": ("a war between humanity and advanced robots", "THEME_TAG_ABSTRACT", "上游词表将人机战争抽象题材误分入场景库，非空间场所，隔离排除"),
    "SRC_SCENE_05362": ("a war between humanity and artificial intelligence", "THEME_TAG_ABSTRACT", "上游词表将人类与AI战争抽象题材误分入场景库，非空间场所，隔离排除"),

    # --- 1L. 抽象科学/时间发现 (3条) ---
    "SRC_SCENE_04519": ("a discovery of a parallel timeline where history took a different path", "ABSTRACT_CONCEPT", "上游词表将平行时间线概念误分入场景库，非实体空间，隔离排除"),
    "SRC_SCENE_04521": ("a discovery of a way to travel through time", "ABSTRACT_CONCEPT", "上游词表将时间旅行方式概念误分入场景库，非实体空间，隔离排除"),
    "SRC_SCENE_04525": ("a discovery of faster-than-light travel", "ABSTRACT_CONCEPT", "上游词表将超光速旅行概念误分入场景库，非实体空间，隔离排除"),
}

# ==============================================================================
# 2. 真实基线复用映射 (REUSE_EXISTING, 37条, 来源未说明时间一律 time_of_day: unspecified)
# ==============================================================================
REUSE_BASELINE_MAP = {
    "SRC_SCENE_05406": ("abandoned building", "scene_abandoned_building", "scene_abandoned_building__tag_000", "abandoned building", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["abandoned"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05427": ("amusement park", "scene_amusement_park", "scene_amusement_park__tag_000", "amusement park", {"semantic_role": "scene_detail", "space_kind": "outdoor", "venue_ids": ["entertainment"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05544": ("aquarium", "scene_aquarium", "scene_aquarium__tag_000", "aquarium", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["entertainment"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05547": ("arcade", "scene_arcade", "scene_arcade__tag_000", "arcade", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["entertainment"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05595": ("basement", "scene_basement", "scene_basement__tag_000", "basement", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["domestic"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05596": ("bathroom", "scene_house", "scene_house__tag_011", "bathroom", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["domestic"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05597": ("bathtub", "scene_bathtub", "scene_bathtub__tag_000", "bathtub", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["bathroom"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05606": ("bedroom", "scene_bedroom", "scene_bedroom__tag_000", "bedroom", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["domestic"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05616": ("boardroom", "scene_meeting_room", "scene_meeting_room__tag_002", "boardroom", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["office"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05647": ("bowling alley", "scene_bowling_alley", "scene_bowling_alley__tag_000", "bowling alley", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["entertainment"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05653": ("bus interior", "scene_bus_interior", "scene_bus_interior__tag_000", "bus interior", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["transit"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05672": ("carousel", "scene_amusement_park", "scene_amusement_park__tag_007", "carousel", {"semantic_role": "scene_detail", "space_kind": "outdoor", "venue_ids": ["entertainment"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05693": ("classroom", "scene_classroom", "scene_classroom__tag_000", "classroom", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["school"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05726": ("convenience store", "scene_convenience_store", "scene_convenience_store__tag_000", "convenience store", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["convenience_store"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05746": ("cubicle", "scene_office", "scene_office__tag_002", "cubicle", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["office"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05771": ("dressing room", "scene_dressing_room", "scene_dressing_room__tag_000", "dressing room", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["dressing_room"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05817": ("ferris wheel", "scene_amusement_park", "scene_amusement_park__tag_006", "ferris wheel", {"semantic_role": "scene_detail", "space_kind": "outdoor", "venue_ids": ["entertainment"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05824": ("fitting room", "scene_fitting_room", "scene_fitting_room__tag_000", "fitting room", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["commercial"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05845": ("garage", "scene_house", "scene_house__tag_012", "garage", {"semantic_role": "scene_detail", "space_kind": "indoor_or_semi_open", "venue_ids": ["domestic"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05846": ("garden", "scene_house", "scene_house__tag_013", "garden", {"semantic_role": "scene_detail", "space_kind": "outdoor", "venue_ids": ["domestic"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05873": ("graveyard", "scene_cemetery_at_night", "scene_cemetery_at_night__tag_003", "graveyard", {"semantic_role": "scene_detail", "space_kind": "outdoor", "venue_ids": ["cemetery"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05920": ("hot spring", "scene_hot_spring", "scene_hot_spring__tag_000", "hot spring", {"semantic_role": "scene_detail", "space_kind": "semi_open", "venue_ids": ["hot_spring"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05923": ("hotel room", "scene_bedroom", "scene_bedroom__tag_001", "hotel room", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["hospitality"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05924": ("house", "scene_house", "scene_house__tag_000", "house", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["domestic"], "time_of_day": "unspecified"}),
    "SRC_SCENE_05965": ("kitchen", "scene_house", "scene_house__tag_002", "kitchen", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["domestic"], "time_of_day": "unspecified"}),
    "SRC_SCENE_06095": ("library", "scene_library", "scene_library__tag_000", "library", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["library"], "time_of_day": "unspecified", "context_affinity": ["school"], "baseline_default_time_of_day": "day"}),
    "SRC_SCENE_06101": ("living room", "scene_house", "scene_house__tag_001", "living room", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["domestic"], "time_of_day": "unspecified"}),
    "SRC_SCENE_06182": ("movie theater", "scene_movie_theater", "scene_movie_theater__tag_000", "movie theater", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["entertainment"], "time_of_day": "unspecified"}),
    "SRC_SCENE_06217": ("office", "scene_office", "scene_office__tag_000", "office", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["office"], "time_of_day": "unspecified"}),
    "SRC_SCENE_06237": ("parking lot", "scene_parking_lot", "scene_parking_lot__tag_000", "parking lot", {"semantic_role": "scene_detail", "space_kind": "outdoor", "venue_ids": ["transit"], "time_of_day": "unspecified"}),
    "SRC_SCENE_06315": ("rooftop garden", "scene_office_building_rooftop", "scene_office_building_rooftop__tag_002", "rooftop garden", {"semantic_role": "scene_detail", "space_kind": "outdoor", "venue_ids": ["urban_rooftop"], "time_of_day": "unspecified"}),
    "SRC_SCENE_06317": ("rooftop terrace", "scene_office_building_rooftop", "scene_office_building_rooftop__tag_003", "rooftop terrace", {"semantic_role": "scene_detail", "space_kind": "outdoor", "venue_ids": ["urban_rooftop"], "time_of_day": "unspecified"}),
    "SRC_SCENE_06337": ("sauna", "scene_public_bath", "scene_public_bath__tag_005", "sauna", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["bathhouse"], "time_of_day": "unspecified"}),
    "SRC_SCENE_06374": ("shrine", "scene_shrine", "scene_shrine__tag_000", "shrine", {"semantic_role": "scene_detail", "space_kind": "outdoor", "venue_ids": ["traditional"], "time_of_day": "unspecified"}),
    "SRC_SCENE_06400": ("staircase", "scene_staircase", "scene_staircase__tag_000", "staircase", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["urban"], "time_of_day": "unspecified"}),
    "SRC_SCENE_06466": ("temple", "scene_temple", "scene_temple__tag_000", "temple", {"semantic_role": "scene_detail", "space_kind": "outdoor", "venue_ids": ["traditional"], "time_of_day": "unspecified"}),
    "SRC_SCENE_06501": ("train interior", "scene_train_interior", "scene_train_interior__tag_000", "train interior", {"semantic_role": "scene_detail", "space_kind": "indoor", "venue_ids": ["transit"], "time_of_day": "unspecified"}),
}

# ==============================================================================
# 3. 错配复用纠偏 (NEW_STYLE, 25条, 纠正基线 bathtub/bowling_alley 等不当父级, 时间未说明一律 unspecified)
# ==============================================================================
COMPROMISED_REUSE_MAP = {
    # 阳台：半开放户外空间，非纯室内房屋
    "SRC_SCENE_05584": ("balcony", "domestic_balcony", "domestic_balcony__tag_000", "balcony", {"space_kind": "semi_open", "venue_ids": ["domestic"], "time_of_day": "unspecified"}, "居住阳台空间：纠正从属，标定为半开放户外空间（semi_open），避免继承纯室内居室属性，时间未说明标为 unspecified"),
    # 公交车站：户外街头交通站点，非高速公路大巴车厢
    "SRC_SCENE_05654": ("bus stop", "transit_bus_stop", "transit_bus_stop__tag_000", "bus stop", {"space_kind": "outdoor", "venue_ids": ["transit"], "time_of_day": "unspecified"}, "户外公交站台：独立为户外公共交通设施（outdoor），避免错误继承大巴车厢内部属性，时间未说明标为 unspecified"),
    # 车内空间：通用车辆座舱，非限定后排座椅
    "SRC_SCENE_05670": ("car interior", "vehicle_car_interior", "vehicle_car_interior__tag_000", "car interior", {"space_kind": "indoor", "venue_ids": ["vehicle"], "time_of_day": "unspecified"}, "汽车座舱内部：独立通用车内空间，避免狭窄限定为后排座椅，时间未说明标为 unspecified"),
    # 赌场：大型商业博彩空间，非日式帕青哥弹子房
    "SRC_SCENE_05675": ("casino", "venue_casino", "venue_casino__tag_000", "casino", {"space_kind": "indoor", "venue_ids": ["gaming"], "time_of_day": "unspecified"}, "大型赌场娱乐场所：独立国际通用博彩大厅，来源未限定时间，标定 time_of_day: unspecified，严禁硬编码夜间"),
    # 衣帽间：独立储物空间，非狭窄更衣储物柜
    "SRC_SCENE_05699": ("closet", "domestic_closet", "domestic_closet__tag_000", "closet", {"space_kind": "indoor", "venue_ids": ["domestic"], "time_of_day": "unspecified"}, "家庭衣帽间/壁橱：独立室内收纳空间，避免错误挂载于更衣更衣柜，时间未说明标为 unspecified"),
    # 暖房/日光温室：半开放植物温室，非封闭玻璃密室
    "SRC_SCENE_05722": ("conservatory", "botanical_conservatory", "botanical_conservatory__tag_000", "conservatory", {"space_kind": "semi_open", "venue_ids": ["botanical"], "time_of_day": "unspecified"}, "日光温室/暖房：独立植物景观空间，标定半开放（semi_open），避免归入封闭玻璃房，时间未说明标为 unspecified"),
    # 施工工地：大型户外工业建筑工地，非小巷胡同
    "SRC_SCENE_05723": ("construction site", "industrial_construction_site", "industrial_construction_site__tag_000", "construction site", {"space_kind": "outdoor", "venue_ids": ["industrial"], "time_of_day": "unspecified"}, "建筑施工工地：独立户外工业施工场地，避免降级为后街暗巷子标签，时间未说明标为 unspecified"),
    # 玻璃温室大棚
    "SRC_SCENE_05885": ("greenhouse", "botanical_greenhouse", "botanical_greenhouse__tag_000", "greenhouse", {"space_kind": "semi_open", "venue_ids": ["botanical"], "time_of_day": "unspecified"}, "农业/植物玻璃温室：独立半开放温室，避免错误从属于监禁密室体系，时间未说明标为 unspecified"),
    # 自然湖泊：宏大户外自然水体，绝非浴缸！
    "SRC_SCENE_05974": ("lake", "nature_lake", "nature_lake__tag_000", "lake", {"space_kind": "outdoor", "venue_ids": ["natural_water"], "time_of_day": "unspecified"}, "自然湖泊景观：彻底纠正基线将自然湖泊错误塞入浴缸 (scene_bathtub) 的严重事实错误，标定户外自然水体，时间未说明标为 unspecified"),
    # 更衣室/储物间：公共更衣空间，非学校体育器材室
    "SRC_SCENE_06105": ("locker room", "facility_locker_room", "facility_locker_room__tag_000", "locker room", {"space_kind": "indoor", "venue_ids": ["athletic"], "time_of_day": "unspecified"}, "体育场馆更衣室：独立运动设施更衣空间，避免错误从属于器材室，时间未说明标为 unspecified"),
    # 温泉/风吕：挂载至现有 hot_spring，绝非浴缸！
    "SRC_SCENE_06222": ("onsen", "scene_hot_spring", "scene_hot_spring__ext_onsen", "onsen", {"space_kind": "semi_open", "venue_ids": ["hot_spring"], "time_of_day": "unspecified"}, "日式温泉风吕：挂载至现有 hot_spring 作为和风变体，彻底纠正将其归入浴缸 (bathtub) 的错误，时间未说明标为 unspecified"),
    # 街头电话亭：户外城市街道公共设施，非更衣柜
    "SRC_SCENE_06245": ("phone booth", "urban_phone_booth", "urban_phone_booth__tag_000", "phone booth", {"space_kind": "outdoor", "venue_ids": ["urban"], "time_of_day": "unspecified"}, "户外街头电话亭：独立城市街头设施，避免错误归入密室储物柜，时间未说明标为 unspecified"),
    # 泳池水体：户外/开阔泳池，非浴缸
    "SRC_SCENE_06264": ("pool", "venue_swimming_pool", "venue_swimming_pool__pool", "pool", {"space_kind": "outdoor", "venue_ids": ["aquatic"], "time_of_day": "unspecified"}, "游泳池水域：独立水上运动与休闲水域，避免错误塞入浴缸，时间未说明标为 unspecified"),
    # 泳池边：开阔池畔甲板/休闲区，非保龄球馆！
    "SRC_SCENE_06265": ("poolside", "venue_swimming_pool", "venue_swimming_pool__poolside", "poolside", {"space_kind": "outdoor", "venue_ids": ["aquatic"], "time_of_day": "unspecified"}, "泳池畔甲板区域：彻底纠正将池畔错误归入保龄球馆 (scene_bowling_alley) 的错误，时间未说明标为 unspecified"),
    # 牢房监室：司法羁押监室，非审讯室
    "SRC_SCENE_06270": ("prison cell", "detention_prison_cell", "detention_prison_cell__tag_000", "prison cell", {"space_kind": "indoor", "venue_ids": ["detention"], "time_of_day": "unspecified"}, "监狱牢房监室：独立封闭羁押场所，避免错误塞入审讯室，时间未说明标为 unspecified"),
    # 自然河流：宏大户外自然流水，绝非浴缸！
    "SRC_SCENE_06299": ("river", "nature_river", "nature_river__tag_000", "river", {"space_kind": "outdoor", "venue_ids": ["natural_water"], "time_of_day": "unspecified"}, "自然河流景观：彻底纠正将自然河流塞入浴缸 (scene_bathtub) 的严重事实错误，标定户外自然水体，时间未说明标为 unspecified"),
    # 河岸水滨：开阔户外河堤，非夜间公园
    "SRC_SCENE_06301": ("riverbank", "nature_riverbank", "nature_riverbank__tag_000", "riverbank", {"space_kind": "outdoor", "venue_ids": ["natural_water"], "time_of_day": "unspecified"}, "河岸/河畔堤坝：独立水岸户外景观，避免错误限定为夜间公园，时间未说明标为 unspecified"),
    # 建筑物天台：户外高空建筑屋顶，非保健室！
    "SRC_SCENE_06313": ("rooftop", "urban_architecture_rooftop", "urban_architecture_rooftop__tag_000", "rooftop", {"space_kind": "outdoor", "venue_ids": ["urban_rooftop"], "time_of_day": "unspecified"}, "建筑开阔天台：彻底纠正将天台塞入医务保健室 (scene_nurse_office) 的严重错误，时间未说明标为 unspecified"),
    # 屋顶泳池：高端户外高空泳池，非夜间天台下属
    "SRC_SCENE_06316": ("rooftop pool", "venue_rooftop_pool", "venue_rooftop_pool__tag_000", "rooftop pool", {"space_kind": "outdoor", "venue_ids": ["urban_rooftop", "aquatic"], "time_of_day": "unspecified"}, "天台高空泳池：独立高空水域场所，避免限定为夜间环境，时间未说明标为 unspecified"),
    # 历史遗迹群：户外古文明断壁残垣，非现代废弃建筑！
    "SRC_SCENE_06320": ("ruins", "historical_ruins", "historical_ruins__tag_000", "ruins", {"space_kind": "outdoor", "venue_ids": ["ancient"], "time_of_day": "unspecified"}, "历史遗迹群：独立古代文明废墟景观，避免与现代废弃危房混淆，时间未说明标为 unspecified"),
    # 楼梯井/安全通道
    "SRC_SCENE_06401": ("stairwell", "architecture_stairwell", "architecture_stairwell__tag_000", "stairwell", {"space_kind": "indoor", "venue_ids": ["transit"], "time_of_day": "unspecified"}, "建筑楼梯井：独立室内垂直通道，避免局限于家庭住宅居室，时间未说明标为 unspecified"),
    # 游泳池场所：开阔水上运动场馆，非保龄球馆！
    "SRC_SCENE_06436": ("swimming pool", "venue_swimming_pool", "venue_swimming_pool__tag_000", "swimming pool", {"space_kind": "outdoor_or_indoor", "venue_ids": ["aquatic"], "time_of_day": "unspecified"}, "游泳池场馆：彻底纠正将游泳池塞入保龄球馆 (scene_bowling_alley) 的错误，时间未说明标为 unspecified"),
    # 卫生间独立隔间
    "SRC_SCENE_06496": ("toilet stall", "sanitary_toilet_stall", "sanitary_toilet_stall__tag_000", "toilet stall", {"space_kind": "indoor", "venue_ids": ["sanitary"], "time_of_day": "unspecified"}, "卫生间隔间：独立私密卫生隔间，避免限定为学校厕所，时间未说明标为 unspecified"),
    # 工业大仓库：大型工业仓储厂房，非密室逃脱/SM暗室！
    "SRC_SCENE_06570": ("warehouse", "industrial_warehouse", "industrial_warehouse__tag_000", "warehouse", {"space_kind": "indoor", "venue_ids": ["industrial"], "time_of_day": "unspecified"}, "工业大仓库：彻底纠正将大型仓储厂房塞入监禁密室 (scene_locked_room) 的错误，时间未说明标为 unspecified"),
    # 动物园：大型户外陆生野生动物园，非室内水族馆！
    "SRC_SCENE_06610": ("zoo", "venue_zoo", "venue_zoo__tag_000", "zoo", {"space_kind": "outdoor", "venue_ids": ["wildlife"], "time_of_day": "unspecified"}, "动物园：彻底纠正将陆生野生动物园塞入水族馆 (scene_aquarium) 的严重错误，时间未说明标为 unspecified"),
}

# ==============================================================================
# 4. 原则 2 场所本身为主分类与废弃状态保真 (VENUE-FIRST & DECAY STATE)
# ==============================================================================
SPECIAL_VENUE_STATE_MAP = {
    "SRC_SCENE_05407": {
        "tag_id": "waterfront_pier__abandoned",
        "item_id": "waterfront_pier",
        "tag_text": "abandoned pier",
        "decision": "NEW_STYLE",
        "role": "selector",
        "facts": {"venue_type": "pier", "space_kind": "outdoor", "decay_state": "abandoned", "condition": "abandoned", "time_of_day": "unspecified"},
        "rationale": "废弃码头水岸：落实按场所本身分类原则，归入码头水岸类 (waterfront_pier) 并保留废弃状态 (decay_state: abandoned)，严禁因包含 abandoned 错误并入废弃建筑",
    },
    "SRC_SCENE_04367": {
        "tag_id": "cityscape_abandoned",
        "item_id": "urban_cityscape",
        "tag_text": "a city abandoned for years",
        "decision": "NEW_STYLE",
        "role": "selector",
        "facts": {"venue_type": "city", "space_kind": "outdoor", "decay_state": "abandoned", "condition": "abandoned", "time_of_day": "unspecified"},
        "rationale": "废弃城市景观：按城市全景场所分类，保留多年废弃无人状态",
    },
    "SRC_SCENE_05091": {
        "tag_id": "village_ruin__abandoned_weed_choked",
        "item_id": "village_settlement",
        "tag_text": "a ruined abandoned village, roofless cottages and a choked well overgrown with weeds under an overcast sky, a leaning signpost, weathered-grey and green palette, dim desolate light",
        "decision": "NEW_STYLE",
        "role": "selector",
        "facts": {
            "venue_type": "village",
            "space_kind": "outdoor",
            "decay_state": "ruined_abandoned",
            "time_of_day": "unspecified",
            "is_prose_template": True,
            "embedded_palette": "weathered-grey and green palette",
            "embedded_lighting": "dim desolate light",
            "slot_attribution": {
                "venue_target": "scenes.json",
                "props_treatment": "embedded_environmental_dressing",
                "palette_treatment": "embedded_scene_palette",
                "lighting_treatment": "embedded_ambient_lighting",
                "independent_slot_conflict_policy": "harmonize_or_suppress_independent_lighting",
            },
        },
        "rationale": "荒废村落遗址：按村落聚落归类，保留杂草丛生荒废断壁残垣状态与环境叙事（调色板与荒寂微光标定为内嵌氛围属性）",
    },
}

# ==============================================================================
# 5. 原则 3 复合场景拆分与保留规则 (ENSEMBLE_COMBO, 事件解耦)
# ==============================================================================
def infer_composite_venue_space_kind(venue: str) -> str:
    v_low = venue.lower()
    # 1. 明确封闭交通座舱或室内场所优先判定 (避免火车 train 因包含 rain 子串被误判为户外)
    if re.search(r'\b(train|train\s+ride|subway|metro|tram|monorail|railway|cabin|room|ballroom|house|hotel|airport|church|restaurant|cafe|bar|indoor)\b', v_low):
        return "indoor"
    if re.search(r'\b(hot\s+spring)\b', v_low):
        return "semi_open"
    if re.search(r'\b(castle|landmark|sports\s+game)\b', v_low):
        return "outdoor_or_indoor"
    # 2. 明确自然户外、高空空中或开阔水陆景观 (严格全词/词界匹配，杜绝子串误判)
    if re.search(r'\b(balloon|canyon|mountaintop|mountain|mountains|peak|beach|coast|coastal|island|archipelago|forest|rainforest|jungle|desert|swamp|lake|river|waterfall|countryside|valley|snow|rain|rainy|blossoms|northern\s+lights|aurora|park|vineyard|garden|bridge|street|rooftop|ski\s+lift|ferris\s+wheel|swing|lighthouse|ruin|ruins|monument|eiffel\s+tower|theme\s+park|carnival|festival|horseback|boat|gondola|riverboat)\b', v_low):
        return "outdoor"
    return "unspecified"


def parse_composite_event_scene(raw_text: str, eid: str) -> dict | None:
    t = raw_text.strip()
    t_low = t.lower()

    # 1. 惊喜求婚 (a surprise proposal at/in/during/on/under/by <venue>)
    m_prop = re.match(r"^a surprise proposal (?:at|in|during|on|while|under|by) (.+)$", t, re.IGNORECASE)
    if m_prop:
        venue = m_prop.group(1).strip()
        v_slug = slugify(venue)
        sk = infer_composite_venue_space_kind(venue)
        tod = "dawn" if "dawn" in venue.lower() else "sunset" if "sunset" in venue.lower() else "night" if "night" in venue.lower() else "unspecified"
        return {
            "source_entity_id": eid,
            "decision": "ENSEMBLE_COMBO",
            "rationale": f"复合场景解构：核心背景场所提取为‘{venue}’；人物交互事件‘求婚 (surprise proposal)’解耦记录，排除出纯场景输出，归入人物交互/动作槽位处理，避免侵入场景槽位",
            "mappings": [{
                "target_catalog_file": "scenes.json",
                "target_item_id": f"event_setting__{v_slug}",
                "target_tag_id": f"event_setting__{v_slug}__tag_000",
                "target_role": "selector",
                "target_tag_text": venue,
                "merged_legacy_id": "",
                "facts": {
                    "extracted_venue": venue,
                    "space_kind": sk,
                    "time_of_day": tod,
                    "deconstructed_event": {
                        "event_type": "romantic_interaction",
                        "event_action": "surprise proposal",
                        "treatment_decision": "decouple_to_interaction_slot",
                        "action_target_slot": "pose_interaction",
                        "excluded_from_pure_scene_output": True,
                    },
                },
            }],
        }

    # 2. 浪漫接吻 (a romantic kiss during/on/at/in/under/while/by <venue>)
    m_kiss = re.match(r"^a romantic kiss (?:during|on|at|in|under|while|by) (.+)$", t, re.IGNORECASE)
    if m_kiss:
        venue = m_kiss.group(1).strip()
        v_slug = slugify(venue)
        sk = infer_composite_venue_space_kind(venue)
        tod = "dawn" if "dawn" in venue.lower() else "sunset" if "sunset" in venue.lower() else "night" if "night" in venue.lower() else "unspecified"
        return {
            "source_entity_id": eid,
            "decision": "ENSEMBLE_COMBO",
            "rationale": f"复合场景解构：核心背景场所提取为‘{venue}’；人物交互行为‘接吻 (romantic kiss)’解耦记录，排除出纯场景输出，归入人物交互/亲密体态槽位处理",
            "mappings": [{
                "target_catalog_file": "scenes.json",
                "target_item_id": f"event_setting__{v_slug}",
                "target_tag_id": f"event_setting__{v_slug}__tag_000",
                "target_role": "selector",
                "target_tag_text": venue,
                "merged_legacy_id": "",
                "facts": {
                    "extracted_venue": venue,
                    "space_kind": sk,
                    "time_of_day": tod,
                    "deconstructed_event": {
                        "event_type": "romantic_interaction",
                        "event_action": "romantic kiss",
                        "treatment_decision": "decouple_to_interaction_slot",
                        "action_target_slot": "pose_interaction",
                        "excluded_from_pure_scene_output": True,
                    },
                },
            }],
        }

    # 3. 依依惜别 (1条, a bittersweet goodbye at an airport)
    if t_low.startswith("a bittersweet goodbye at "):
        venue = t[len("a bittersweet goodbye at "):].strip()
        v_slug = slugify(venue)
        return {
            "source_entity_id": eid,
            "decision": "ENSEMBLE_COMBO",
            "rationale": f"复合场景解构：核心背景场所提取为‘{venue}’；人物离别事件‘依依惜别 (bittersweet goodbye)’解耦记录，排除出纯场景输出，归入人物情态/互动槽位",
            "mappings": [{
                "target_catalog_file": "scenes.json",
                "target_item_id": f"event_setting__{v_slug}",
                "target_tag_id": f"event_setting__{v_slug}__tag_000",
                "target_role": "selector",
                "target_tag_text": venue,
                "merged_legacy_id": "",
                "facts": {
                    "extracted_venue": venue,
                    "space_kind": "indoor",
                    "time_of_day": "unspecified",
                    "deconstructed_event": {
                        "event_type": "emotional_interaction",
                        "event_action": "bittersweet goodbye",
                        "treatment_decision": "decouple_to_interaction_slot",
                        "action_target_slot": "pose_interaction",
                        "excluded_from_pure_scene_output": True,
                    },
                },
            }],
        }

    # 4. 探险/幸存者团队在特定场所的复合叙事 (精细剥离行为与任务目的)
    # 4a. 包含明确寻宝/寻找目的的探险句：剥离任务目的
    m_find = re.match(r"^a group of (\w+) (?:venture into|journey through|journey to) (.+?) to find (.+)$", t, re.IGNORECASE)
    if m_find:
        group_type = m_find.group(1).strip()
        venue = m_find.group(2).strip()
        quest_obj = "find " + m_find.group(3).strip()
        v_slug = slugify(venue)
        sk = infer_composite_venue_space_kind(venue)
        return {
            "source_entity_id": eid,
            "decision": "ENSEMBLE_COMBO",
            "rationale": f"复合场景解构：核心背景场所提取为‘{venue}’；任务目的‘{quest_obj}’与团队行动解耦记录，排除出纯场景输出，归入故事情节/任务槽位处理",
            "mappings": [{
                "target_catalog_file": "scenes.json",
                "target_item_id": f"quest_setting__{v_slug}",
                "target_tag_id": f"quest_setting__{v_slug}__tag_000",
                "target_role": "selector",
                "target_tag_text": venue,
                "merged_legacy_id": "",
                "facts": {
                    "extracted_venue": venue,
                    "space_kind": sk,
                    "time_of_day": "unspecified",
                    "deconstructed_event": {
                        "event_type": "quest_exploration",
                        "event_action": f"{group_type} quest",
                        "quest_objective": quest_obj,
                        "treatment_decision": "decouple_to_interaction_slot",
                        "action_target_slot": "narrative_quest",
                        "excluded_from_pure_scene_output": True,
                    },
                },
            }],
        }

    # 4b. 盗贼偷宝句：剥离盗宝动作与目的
    m_steal = re.match(r"^a group of (\w+) attempt to steal (.+?) from (.+)$", t, re.IGNORECASE)
    if m_steal:
        group_type = m_steal.group(1).strip()
        quest_obj = "steal " + m_steal.group(2).strip()
        venue = m_steal.group(3).strip()
        v_slug = slugify(venue)
        sk = infer_composite_venue_space_kind(venue)
        return {
            "source_entity_id": eid,
            "decision": "ENSEMBLE_COMBO",
            "rationale": f"复合场景解构：核心背景场所提取为‘{venue}’；任务动作‘{quest_obj}’解耦记录，排除出纯场景输出，归入团队行动槽位处理",
            "mappings": [{
                "target_catalog_file": "scenes.json",
                "target_item_id": f"quest_setting__{v_slug}",
                "target_tag_id": f"quest_setting__{v_slug}__tag_000",
                "target_role": "selector",
                "target_tag_text": venue,
                "merged_legacy_id": "",
                "facts": {
                    "extracted_venue": venue,
                    "space_kind": sk,
                    "time_of_day": "unspecified",
                    "deconstructed_event": {
                        "event_type": "quest_exploration",
                        "event_action": f"{group_type} theft attempt",
                        "quest_objective": quest_obj,
                        "treatment_decision": "decouple_to_interaction_slot",
                        "action_target_slot": "narrative_quest",
                        "excluded_from_pure_scene_output": True,
                    },
                },
            }],
        }

    # 4c. 通用团队探索发现句 (含具体场所)
    m_grp = re.match(r"^a group of (\w+) (?:explore|venture into|discover|in) (.+)$", t, re.IGNORECASE)
    if m_grp:
        group_type = m_grp.group(1).strip()
        venue = m_grp.group(2).strip()
        v_slug = slugify(venue)
        sk = infer_composite_venue_space_kind(venue)
        return {
            "source_entity_id": eid,
            "decision": "ENSEMBLE_COMBO",
            "rationale": f"复合场景解构：核心背景场所提取为‘{venue}’；团队行动/探险情节解耦记录，排除出纯场景输出，归入人物群体/故事情节槽位",
            "mappings": [{
                "target_catalog_file": "scenes.json",
                "target_item_id": f"quest_setting__{v_slug}",
                "target_tag_id": f"quest_setting__{v_slug}__tag_000",
                "target_role": "selector",
                "target_tag_text": venue,
                "merged_legacy_id": "",
                "facts": {
                    "extracted_venue": venue,
                    "space_kind": sk,
                    "time_of_day": "unspecified",
                    "deconstructed_event": {
                        "event_type": "quest_exploration",
                        "event_action": f"{group_type} exploration",
                        "treatment_decision": "decouple_to_interaction_slot",
                        "action_target_slot": "narrative_quest",
                        "excluded_from_pure_scene_output": True,
                    },
                },
            }],
        }

    return None

# ==============================================================================
# 6. 原则 3 BKWILDCARDS 五段式叙事场景长句解析与槽位归属
# ==============================================================================
def parse_bkwildcards_prose(raw_text: str, eid: str) -> dict:
    parts = [p.strip() for p in raw_text.split(",") if p.strip()]
    venue_part = parts[0] if parts else raw_text
    venue_slug = slugify(venue_part)

    # 提取灯效/光照 (通常在最后 1~2 段，含 light, glow, gloom, illumination, firelight, lamp 等)
    lighting_text = "unspecified ambient lighting"
    palette_text = "natural tones"
    props_list = []

    for p in parts[1:]:
        p_low = p.lower()
        if any(k in p_low for k in ["palette", "hues", "tones", "colors"]):
            palette_text = p
        elif any(k in p_low for k in ["light", "glow", "gloom", "illumination", "firelight", "haze", "shaft", "sunlight", "drizzle", "mist"]):
            lighting_text = p
        else:
            props_list.append(p)

    # 判断空间形态 space_kind (严格词界匹配)
    v_low = venue_part.lower()
    if re.search(r'\b(room|couch|hall|interior|corridor|shop|parlor|flat|chamber|dungeon|lab|tavern|inn|keep|library|closet|booth|cabin)\b', v_low):
        space_kind = "indoor"
    elif re.search(r'\b(cave|mine|barrow|tunnel|catacomb|crypt|vault)\b', v_low):
        space_kind = "subterranean"
    elif re.search(r'\b(road|canyon|street|forest|mountain|beach|harbor|desert|ruin|wall|gate|bridge|river|glade|meadow|sky|plaza)\b', v_low):
        space_kind = "outdoor"
    else:
        space_kind = "indoor_or_outdoor"

    facts = {
        "is_prose_template": True,
        "venue_name": venue_part,
        "space_kind": space_kind,
        "time_of_day": "unspecified",
        "embedded_props": props_list[:3],
        "embedded_palette": palette_text,
        "embedded_lighting": lighting_text,
        "slot_attribution": {
            "venue_target": "scenes.json",
            "props_treatment": "embedded_environmental_dressing",
            "palette_treatment": "embedded_scene_palette",
            "lighting_treatment": "embedded_ambient_lighting",
            "independent_slot_conflict_policy": "harmonize_or_suppress_independent_lighting",
        },
    }

    tag_id = f"prose_scene__{venue_slug}"
    return {
        "source_entity_id": eid,
        "decision": "NEW_STYLE",
        "rationale": f"新增自然语言整景叙述模板：核心场所‘{venue_part}’；提取道具为环境陈设，提取色调‘{palette_text}’与氛围光‘{lighting_text}’，明确下游槽位消冲突策略",
        "mappings": [{
            "target_catalog_file": "scenes.json",
            "target_item_id": "prose_scene_templates",
            "target_tag_id": tag_id,
            "target_role": "selector",
            "target_tag_text": raw_text,
            "merged_legacy_id": "",
            "facts": facts,
        }],
    }

# ==============================================================================
# 7. 地理地标与著名国家公园近义聚类 (消除 Yellowstone 等冗余)
# ==============================================================================
LANDMARK_CLUSTERS = {
    # 黄石国家公园
    "SRC_SCENE_06603": ("Yellowstone", "geographic_landmarks", "geographic_landmarks__yellowstone_national_park", "Yellowstone National Park", "STYLE_VARIANT", "variant", "地理地标同义变体：Yellowstone 归并至核心规范 Yellowstone National Park"),
    "SRC_SCENE_06604": ("Yellowstone National Park", "geographic_landmarks", "geographic_landmarks__yellowstone_national_park", "Yellowstone National Park", "NEW_STYLE", "selector", "新增地理地标核心标签：黄石国家公园 (Yellowstone National Park)"),
    # 优胜美地国家公园
    "SRC_SCENE_06605": ("Yosemite National Park", "geographic_landmarks", "geographic_landmarks__yosemite_national_park", "Yosemite National Park", "NEW_STYLE", "selector", "新增地理地标核心标签：优胜美地国家公园 (Yosemite National Park)"),
    "SRC_SCENE_06606": ("Yosemite National Park, California", "geographic_landmarks", "geographic_landmarks__yosemite_national_park", "Yosemite National Park", "STYLE_VARIANT", "variant", "地理地标同义变体：带州名写法归并至核心规范 Yosemite National Park"),
    # 锡安国家公园
    "SRC_SCENE_06609": ("Zion National Park", "geographic_landmarks", "geographic_landmarks__zion_national_park", "Zion National Park", "NEW_STYLE", "selector", "新增地理地标核心标签：锡安国家公园 (Zion National Park)"),
    # 长江
    "SRC_SCENE_06602": ("Yangtze River", "geographic_landmarks", "geographic_landmarks__yangtze_river", "Yangtze River", "NEW_STYLE", "selector", "新增地理水系地标：长江 (Yangtze River)"),
    # 禅宗寺院
    "SRC_SCENE_06607": ("zen monastery", "traditional_monastery", "traditional_monastery__zen", "zen monastery", "NEW_STYLE", "selector", "新增文化宗教建筑：禅修寺院 (zen monastery)"),
}

# 机器人围城近义聚类 (a city under siege by ...)
ROBOT_SIEGE_CLUSTERS = {
    "SRC_SCENE_04372": ("a city under attack by giant robots", "STYLE_VARIANT", "variant", "科幻围城近义变体：巨型机器人袭击城市，归并至机器人围城核心标签"),
    "SRC_SCENE_04373": ("a city under siege by an army of robots", "STYLE_VARIANT", "variant", "科幻围城近义变体：机器人军团围城，归并至机器人围城核心标签"),
    "SRC_SCENE_04375": ("a city under siege by giant robots", "STYLE_VARIANT", "variant", "科幻围城近义变体：巨型机器人围城，归并至机器人围城核心标签"),
    "SRC_SCENE_04376": ("a city under siege by robots", "NEW_STYLE", "selector", "新增科幻末世核心主题：机器人围攻城市 (a city under siege by robots)"),
    "SRC_SCENE_04368": ("a city is hit by a deadly virus outbreak", "STYLE_VARIANT", "variant", "末日灾变近义变体：城市爆发致命病毒，归并至病毒爆发废墟城市核心标签"),
    "SRC_SCENE_04369": ("a city plagued by a deadly virus outbreak", "NEW_STYLE", "selector", "新增末日灾变核心场景：致命病毒肆虐的城市 (a city plagued by a deadly virus outbreak)"),
}

# ==============================================================================
# 8. 通用短词条与其余实体分类器 (全词界匹配，空间关系优先级明确)
# ==============================================================================
def classify_general_scene(raw_low: str) -> tuple[str, str]:
    # 1. 明确室内空间优先 (beach house interior, coastal interior 等室内词优先于周围环境修饰词)
    if re.search(r'\b(interior|interiors|indoor|indoors|inside)\b', raw_low):
        if re.search(r'\b(spaceship|space\s+station|cockpit|starship)\b', raw_low):
            return 'scifi_futuristic', 'indoor'
        if re.search(r'\b(car|bus|train|airplane|aircraft|tank|vehicle)\b', raw_low):
            return 'vehicle_interior', 'indoor'
        return 'interior_room', 'indoor'
    # 2. 商业零售专项 (supermarket 优先全词匹配，绝不落入 market)
    if re.search(r'\b(supermarket|grocery\s+store|convenience\s+store|department\s+store|shopping\s+mall)\b', raw_low):
        return 'commercial_retail', 'indoor'
    # 3. 餐饮休闲商业服务
    if re.search(r'\b(cafe|cafeteria|coffee\s+shop|restaurant|bistro|diner|bar|pub|tavern|bakery|deli|shop|boutique|store)\b', raw_low):
        return 'commercial_service', 'indoor'
    # 4. 居室特定房间 (室内)
    if re.search(r'\b(bedroom|bed\s+room|living\s+room|kitchen|bathroom|dining\s+room|basement|attic|hallway|corridor|ballroom|boardroom|auditorium|cellar|pantry|foyer|lobby|lounge)\b', raw_low):
        return 'interior_room', 'indoor'
    # 5. 科幻与高科技场景
    if re.search(r'\b(spaceship|space\s+station|cyberpunk|futuristic|cybernetic|sci-fi|laboratory|hangar|starship|mothership|cockpit)\b', raw_low):
        return 'scifi_futuristic', 'indoor'
    # 6. 历史遗迹与古典宫殿建筑
    if re.search(r'\b(castle|palace|citadel|fortress|tower|dungeon|crypt|catacomb|throne\s+room|cathedral|chapel|monastery|temple|ruin|ruins)\b', raw_low):
        return 'historical_architecture', 'indoor_or_outdoor'
    # 7. 自然户外景观 (严格词界匹配，排在明确室内之后)
    if re.search(r'\b(forest|forests|mountain|mountains|mountaintop|beach|beaches|valley|valleys|desert|deserts|plains|canyon|canyons|glade|waterfall|waterfalls|river|rivers|ocean|oceans|jungle|jungles|swamp|swamps|island|islands|sea|seas|seascape|seascapes|sky|meadow|meadows|hills|lake|lakes|pond|ponds|marsh|tundra|steppe|woodland|coast|coastal|shore|cliff|cliffs)\b', raw_low):
        return 'nature_landscape', 'outdoor'
    # 8. 城市户外街景
    if re.search(r'\b(street|streets|alley|alleys|alleyway|avenue|boulevard|plaza|square|crossroad|crossroads|sidewalk|sidewalks|market|marketplace|bazaar)\b', raw_low):
        return 'urban_streetscape', 'outdoor'
    # 9. 兜底未定
    return 'venue_general', 'unspecified'

# ==============================================================================
# 主生成逻辑
# ==============================================================================
def main():
    print("=== 开始生成 M3.2 批次 5 场景环境库审核脚本 ===")
    source_path = REPO_DIR / "scratch/rc10_source_entities.tsv"
    with open(source_path, "r", encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f, delimiter="\t"))

    scene_rows = [r for r in all_rows if r["category"] == "scene"]
    print(f"载入待审核场景实体共: {len(scene_rows)} 条")

    reviews = []
    seen_prose_tags = {}

    for row in scene_rows:
        eid = row["entity_id"]
        raw = row["source_raw_text"]
        raw_low = raw.lower().strip()

        # ----------------------------------------------------------------------
        # 1. 隔离待决议项 (DEFERRED_ISSUE, 71条)
        # ----------------------------------------------------------------------
        if eid in DEFERRED_ITEMS:
            term, issue_code, rationale = DEFERRED_ITEMS[eid]
            reviews.append({
                "source_entity_id": eid,
                "decision": "DEFERRED_ISSUE",
                "rationale": f"{rationale} (问题代号: {issue_code})",
                "mappings": [{
                    "target_catalog_file": "scenes.json",
                    "target_item_id": "quarantine",
                    "target_tag_id": f"quarantine__{slugify(eid)}",
                    "target_role": "quarantined",
                    "target_tag_text": raw,
                    "merged_legacy_id": "",
                    "facts": {"quarantine_reason": issue_code, "is_quarantined": True, "excluded_from_catalog": True, "time_of_day": "unspecified"},
                }],
            })
            continue

        # ----------------------------------------------------------------------
        # 2. 地理地标与著名国家公园近义聚类
        # ----------------------------------------------------------------------
        if eid in LANDMARK_CLUSTERS:
            text, item_id, tag_id, tag_text, dec, role, rat = LANDMARK_CLUSTERS[eid]
            reviews.append({
                "source_entity_id": eid,
                "decision": dec,
                "rationale": rat,
                "mappings": [{
                    "target_catalog_file": "scenes.json",
                    "target_item_id": item_id,
                    "target_tag_id": tag_id,
                    "target_role": role,
                    "target_tag_text": tag_text,
                    "merged_legacy_id": "",
                    "facts": {"space_kind": "outdoor", "venue_category": "landmark", "location_type": "national_park_or_nature", "time_of_day": "unspecified"},
                }],
            })
            continue

        # ----------------------------------------------------------------------
        # 3. 机器人/灾变围城聚类
        # ----------------------------------------------------------------------
        if eid in ROBOT_SIEGE_CLUSTERS:
            text, dec, role, rat = ROBOT_SIEGE_CLUSTERS[eid]
            tag_id = "urban_cityscape__city_under_siege_robots" if "robot" in text else "urban_cityscape__city_virus_outbreak"
            canonical_text = "a city under siege by robots" if "robot" in text else "a city plagued by a deadly virus outbreak"
            reviews.append({
                "source_entity_id": eid,
                "decision": dec,
                "rationale": rat,
                "mappings": [{
                    "target_catalog_file": "scenes.json",
                    "target_item_id": "urban_cityscape",
                    "target_tag_id": tag_id,
                    "target_role": role,
                    "target_tag_text": canonical_text,
                    "merged_legacy_id": "",
                    "facts": {"space_kind": "outdoor", "venue_category": "cityscape", "theme": "sci_fi_apocalypse", "time_of_day": "unspecified"},
                }],
            })
            continue

        # ----------------------------------------------------------------------
        # 4. 原则 2 场所本身分类与状态保留 (SPECIAL_VENUE_STATE_MAP, 含 abandoned pier)
        # ----------------------------------------------------------------------
        if eid in SPECIAL_VENUE_STATE_MAP:
            s_info = SPECIAL_VENUE_STATE_MAP[eid]
            reviews.append({
                "source_entity_id": eid,
                "decision": s_info["decision"],
                "rationale": s_info["rationale"],
                "mappings": [{
                    "target_catalog_file": "scenes.json",
                    "target_item_id": s_info["item_id"],
                    "target_tag_id": s_info["tag_id"],
                    "target_role": s_info["role"],
                    "target_tag_text": s_info["tag_text"],
                    "merged_legacy_id": "",
                    "facts": s_info["facts"],
                }],
            })
            continue

        # ----------------------------------------------------------------------
        # 5. 原则 1 真实基线复用 (REUSE_EXISTING, 37条, 来源未说明时间一律 unspecified)
        # ----------------------------------------------------------------------
        if eid in REUSE_BASELINE_MAP:
            name, item_id, tag_id, tag_text, facts = REUSE_BASELINE_MAP[eid]
            if eid == "SRC_SCENE_06095":
                rat = f"100% 精确复用现有场景库标签：{tag_id}；核心事实标定通用图书馆 (venue_ids: ['library'], time_of_day: 'unspecified')，基线历史偏好 (school, day) 降级为上下文亲和度与默认偏好，避免强行限定为学校白天"
            else:
                rat = f"100% 精确复用现有场景库标签：{tag_id}，父级条目 {item_id} 上下文吻合，时间未说明标为 unspecified"
            reviews.append({
                "source_entity_id": eid,
                "decision": "REUSE_EXISTING",
                "rationale": rat,
                "mappings": [{
                    "target_catalog_file": "scenes.json",
                    "target_item_id": item_id,
                    "target_tag_id": tag_id,
                    "target_role": "selector",
                    "target_tag_text": tag_text,
                    "merged_legacy_id": tag_id,
                    "facts": facts,
                }],
            })
            continue

        # ----------------------------------------------------------------------
        # 6. 原则 1 错配复用纠偏 (NEW_STYLE, 25条, 避免 bathtub 等错误父级)
        # ----------------------------------------------------------------------
        if eid in COMPROMISED_REUSE_MAP:
            name, item_id, tag_id, tag_text, facts, rat = COMPROMISED_REUSE_MAP[eid]
            reviews.append({
                "source_entity_id": eid,
                "decision": "NEW_STYLE",
                "rationale": rat,
                "mappings": [{
                    "target_catalog_file": "scenes.json",
                    "target_item_id": item_id,
                    "target_tag_id": tag_id,
                    "target_role": "selector",
                    "target_tag_text": tag_text,
                    "merged_legacy_id": "",
                    "facts": facts,
                }],
            })
            continue

        # ----------------------------------------------------------------------
        # 7. 原则 3 复合场景拆分与保留规则 (ENSEMBLE_COMBO, 事件解耦)
        # ----------------------------------------------------------------------
        combo_res = parse_composite_event_scene(raw, eid)
        if combo_res:
            reviews.append(combo_res)
            continue

        # ----------------------------------------------------------------------
        # 8. 原则 3 BKWILDCARDS 自然语言整身叙述模板 (837条)
        # ----------------------------------------------------------------------
        if row["upstream_repos"] == "BKWILDCARDS" or (len(raw.split(",")) >= 4 and any(k in raw_low for k in ["palette", "light", "glow", "gloom"])):
            parsed_prose = parse_bkwildcards_prose(raw, eid)
            # 处理重复场所变体聚类
            tag_id = parsed_prose["mappings"][0]["target_tag_id"]
            if tag_id in seen_prose_tags:
                first_eid, first_text = seen_prose_tags[tag_id]
                parsed_prose["decision"] = "STYLE_VARIANT"
                parsed_prose["mappings"][0]["target_role"] = "variant"
                parsed_prose["mappings"][0]["target_tag_text"] = first_text
                parsed_prose["rationale"] = f"自然语言整景叙述同义变体：归并至核心场所形态 {tag_id} (首发: {first_eid})，消除多余新建标签"
            else:
                seen_prose_tags[tag_id] = (eid, parsed_prose["mappings"][0]["target_tag_text"])
            reviews.append(parsed_prose)
            continue

        # ----------------------------------------------------------------------
        # 9. 通用短词条与其余实体分类 (按物理场所主词正交划分，全词界优先匹配)
        # ----------------------------------------------------------------------
        slug = slugify(raw)
        item_id, space_kind = classify_general_scene(raw_low)

        tag_id = f"{item_id}__{slug}"
        facts = {
            "space_kind": space_kind,
            "venue_category": item_id,
            "setting_genre": "fantasy" if "fantasy" in raw_low or "magic" in raw_low else "modern_or_general",
            "time_of_day": "unspecified",
        }
        reviews.append({
            "source_entity_id": eid,
            "decision": "NEW_STYLE",
            "rationale": f"新增场景环境规范条目：{raw}（主类: {item_id}，空间属性: {space_kind}，时间属性: unspecified）",
            "mappings": [{
                "target_catalog_file": "scenes.json",
                "target_item_id": item_id,
                "target_tag_id": tag_id,
                "target_role": "selector",
                "target_tag_text": raw,
                "merged_legacy_id": "",
                "facts": facts,
            }],
        })

    print(f"审核记录构建完成，总数: {len(reviews)} (期望: 2335)")
    assert len(reviews) == 2335, f"Expected 2335, got {len(reviews)}"

    # 统计审核决定
    dec_counts = Counter(r["decision"] for r in reviews)
    print(f"审核决定分布: {dict(dec_counts)}")

    # 验证同一目标 ID 规范定义一致性
    tag_defs = {}
    conflict_count = 0
    for r in reviews:
        for m in r["mappings"]:
            tid = m["target_tag_id"]
            fpath = m["target_catalog_file"]
            item_id = m["target_item_id"]
            tag_text = m["target_tag_text"]
            if tid not in tag_defs:
                tag_defs[tid] = (fpath, item_id, tag_text, r["source_entity_id"])
            else:
                prev_fpath, prev_item_id, prev_tag_text, prev_eid = tag_defs[tid]
                if fpath != prev_fpath or item_id != prev_item_id or tag_text != prev_tag_text:
                    print(f"[CONFLICT] Tag {tid} mismatch: {r['source_entity_id']} vs {prev_eid}")
                    conflict_count += 1
    assert conflict_count == 0, f"发现 {conflict_count} 处目标标签定义冲突！"
    print(f"[OK] 全部 {len(reviews)} 条场景审核记录构建完成，目标标签 ID 100% 保持唯一规范定义！")

    # 生成落地脚本 scratch/apply_m32_batch5_scene.py
    apply_script_path = REPO_DIR / "scratch/apply_m32_batch5_scene.py"
    script_content = f"""#!/usr/bin/env python3
\"\"\"
scratch/apply_m32_batch5_scene.py
安全落地应用 M3.2 批次 5 场景环境库审核结果 (共 2,335 条实体)。
\"\"\"
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR / "scratch"))

from apply_m32_reviews import apply_reviews_to_ledger

true = True
false = False
null = None

BATCH5_SCENE_REVIEWS = {json.dumps(reviews, ensure_ascii=False, indent=2)}

def main():
    parser = argparse.ArgumentParser(description="安全应用 M3.2 批次 5 场景环境审核成果")
    parser.add_argument("--allow-overwrite-decision", action="store_true", default=False)
    parser.add_argument("--allow-overwrite-rationale", action="store_true", default=False)
    parser.add_argument("--allow-overwrite-mappings", action="store_true", default=False)
    args = parser.parse_args()

    print(f"=== 安全应用 M3.2 批次 5 场景环境审核成果 (共 {{len(BATCH5_SCENE_REVIEWS)}} 条实体) ===")
    print(f"保护策略: 决定覆盖={{args.allow_overwrite_decision}}, 备注覆盖={{args.allow_overwrite_rationale}}, 映射覆盖={{args.allow_overwrite_mappings}}")

    source_path = REPO_DIR / "scratch/rc10_source_entities.tsv"
    target_path = REPO_DIR / "scratch/rc10_target_mappings.tsv"

    updated_sources, total_mappings = apply_reviews_to_ledger(
        reviews_list=BATCH5_SCENE_REVIEWS,
        source_entities_path=source_path,
        target_mappings_path=target_path,
        allow_overwrite_decision=args.allow_overwrite_decision,
        allow_overwrite_rationale=args.allow_overwrite_rationale,
        allow_overwrite_mappings=args.allow_overwrite_mappings,
    )
    print(f"[SUCCESS] 主表已更新 {{updated_sources}} 条实体，目标映射表增量维护后共 {{total_mappings}} 条映射。")

if __name__ == "__main__":
    main()
"""
    apply_script_path.write_text(script_content, encoding="utf-8")
    print(f"[OK] 成功生成落地脚本: {apply_script_path}")

if __name__ == "__main__":
    main()
