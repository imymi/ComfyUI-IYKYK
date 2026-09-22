"""
conflict_rule_fixtures.py — 服装消解规则与承载关系两级测试夹具 (Level A & Level B)

本夹具为 Milestone 2 核心交付物，作为 Gate 1 物理验收对象：
- Level A: 纯原子测试夹具 (A-1 紧身连体服解扣互斥、A-2 带扣连体服合法反例、A-3 下身裁切上身保留反例、A-4 背景遗弃物反例)
- Level B: 节点端到端集成测试入参结构 (TC-INT-001 全裸状态清除、TC-INT-002 普通着装正常流)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from lib.models import (
    PromptAtom,
    SelectionOrigin,
    SemanticFacts,
    SpanType,
    TagProvenance,
)


def make_test_atom(
    text: str,
    source_slot: str,
    item_id: str,
    span_type: SpanType = SpanType.PLAIN,
    tag_order: int = 0,
    span_order: int = 0,
    kind: Optional[str] = None,
    garment_topologies: Tuple[str, ...] = (),
    garment_states: Tuple[str, ...] = (),
    entry_point: str = "generator",
    mode: str = "explicit",
    selector: Optional[str] = None,
    contains_blackbox: bool = False,
    extra_facts: Optional[Dict[str, Any]] = None,
    target_id: Optional[str] = None,
) -> PromptAtom:
    """构造用于测试的强类型 PromptAtom 实例。"""
    actual_selector = selector or source_slot
    actual_kind = kind or actual_selector
    atom_id = f"{actual_selector}__{item_id}__{tag_order}_{span_order}"

    facts_dict: Dict[str, Any] = {
        "garment_topologies": list(garment_topologies),
        "garment_states": list(garment_states),
    }
    if extra_facts:
        facts_dict.update(extra_facts)

    facts = SemanticFacts.from_dict(facts_dict)

    origin = SelectionOrigin(
        entry_point=entry_point,
        mode=mode,
        selector=actual_selector,
        selected_id=item_id,
        raw_value=item_id,
        parent_ids=(item_id,),
    )

    provenance = TagProvenance(
        item_id=item_id,
        semantic_ids=(f"{actual_selector}:{item_id}",),
        kind=actual_kind,
        parent_ids=(item_id,),
        source_mode=mode,
    )

    atom = PromptAtom(
        text=text,
        span_type=span_type,
        source_slot=source_slot,
        source_item_id=item_id,
        tag_order=tag_order,
        span_order=span_order,
        provenance=provenance,
        contains_blackbox=contains_blackbox,
        atom_id=atom_id,
        facts=facts,
        origin=origin,
        id=atom_id,
        target_id=target_id,
    )

    return atom


# ═══════════════════════════════════════════════════════════════════════════
# Level A 规则级原子测试夹具模型定义
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class LevelAFixtureCase:
    """Level A 纯原子测试夹具用例定义"""
    case_id: str
    name: str
    description: str
    input_atoms: Tuple[PromptAtom, ...]
    expected_dropped_atom_ids: Tuple[str, ...]
    expected_survived_atom_ids: Tuple[str, ...]
    expected_rule_id: str
    expected_reason_code: str
    is_counterexample: bool = False  # True 表示合法反例 (预期 0 drop)


def get_level_a_fixture_a1() -> LevelAFixtureCase:
    """
    用例 A-1 (解扣与无纽扣连体服互斥)：
    原子(one_piece_swimsuit, one_piece) + 原子(unbuttoned)
    预期：状态 unbuttoned 被 Drop，诊断为 one_piece_state_conflict，款式保留。
    """
    swimsuit = make_test_atom(
        text="school swimsuit",
        source_slot="clothing",
        item_id="swimsuit_school",
        tag_order=0,
        span_order=0,
        kind="clothing",
        garment_topologies=("one_piece",),
        garment_states=("worn",),
    )
    unbuttoned = make_test_atom(
        text="unbuttoned",
        source_slot="clothing_state",
        item_id="unbuttoned",
        tag_order=1,
        span_order=0,
        kind="clothing_state",
        garment_states=("opened",),
    )
    return LevelAFixtureCase(
        case_id="A-1",
        name="无纽扣连体服与解扣互斥",
        description="一体式连体泳衣物理上无门襟纽扣，当搭配 unbuttoned 时必须消解状态，保留款式",
        input_atoms=(swimsuit, unbuttoned),
        expected_dropped_atom_ids=(unbuttoned.atom_id,),
        expected_survived_atom_ids=(swimsuit.atom_id,),
        expected_rule_id="clothing_style_state_coherence",
        expected_reason_code="one_piece_state_conflict",
        is_counterexample=False,
    )


def get_level_a_fixture_a2() -> LevelAFixtureCase:
    """
    用例 A-2 (解扣与带纽扣连体服合法共存反例)：
    原子(dungarees, 工装背带裤, one_piece 带门襟/排扣) + 原子(unbuttoned)
    预期：两者合法共存，消解规则坚决不予删除！
    """
    dungarees = make_test_atom(
        text="denim dungarees overalls",
        source_slot="clothing",
        item_id="dungarees",
        tag_order=0,
        span_order=0,
        kind="clothing",
        garment_topologies=("one_piece", "bottom_pants"),
        garment_states=("worn",),
    )
    unbuttoned = make_test_atom(
        text="unbuttoned straps, undone buttons",
        source_slot="clothing_state",
        item_id="unbuttoned",
        tag_order=1,
        span_order=0,
        kind="clothing_state",
        garment_states=("opened",),
    )
    return LevelAFixtureCase(
        case_id="A-2",
        name="带纽扣连体服与解扣合法共存反例",
        description="工装背带裤 (dungarees) 具备合法搭扣与门襟，搭配 unbuttoned 为自然物理状态，严禁误杀",
        input_atoms=(dungarees, unbuttoned),
        expected_dropped_atom_ids=(),
        expected_survived_atom_ids=(dungarees.atom_id, unbuttoned.atom_id),
        expected_rule_id="clothing_style_state_coherence",
        expected_reason_code="none",
        is_counterexample=True,
    )


def get_level_a_fixture_a3() -> LevelAFixtureCase:
    """
    用例 A-3 (下装裁切与上身解扣保留反例)：
    上装(shirts_blouses, top) + 下装裁切缺失 + 掀裙(lifted_up) + 上身解扣(unbuttoned)
    预期：下身状态 lifted_up 因缺失下装承载主体被清理；上装与上身状态 unbuttoned 合法保留！
    """
    shirt = make_test_atom(
        text="white button-down shirt",
        source_slot="clothing",
        item_id="shirts_blouses",
        tag_order=0,
        span_order=0,
        kind="clothing",
        garment_topologies=("top",),
        garment_states=("worn",),
    )
    unbuttoned = make_test_atom(
        text="unbuttoned collar",
        source_slot="clothing_state",
        item_id="unbuttoned",
        tag_order=1,
        span_order=0,
        kind="clothing_state",
        garment_states=("opened",),
    )
    lifted_up = make_test_atom(
        text="skirt lifted up",
        source_slot="clothing_state",
        item_id="lifted_up",
        tag_order=2,
        span_order=0,
        kind="clothing_state",
        garment_states=("lifted",),
    )
    return LevelAFixtureCase(
        case_id="A-3",
        name="下装裁切与上身解扣保留反例",
        description="下装被裁切不存在时，lifted_up 失去承载主体被清理；上装与 unbuttoned 承载关系完好，必须保留",
        input_atoms=(shirt, unbuttoned, lifted_up),
        expected_dropped_atom_ids=(lifted_up.atom_id,),
        expected_survived_atom_ids=(shirt.atom_id, unbuttoned.atom_id),
        expected_rule_id="clothing_style_state_coherence",
        expected_reason_code="state_lacks_carrier",
        is_counterexample=True,
    )


def get_level_a_fixture_a4() -> LevelAFixtureCase:
    """
    用例 A-4 (背景遗弃物反例)：
    原子(business_suit, 标记为 discarded 背景物) + 原子(unbuttoned, 需在穿主体)
    预期：因为西装已处于 discarded 状态（在地上/散落一旁），失去人体在穿承载能力，
          人体上的 unbuttoned 动作修饰成为悬空修饰，被清理。
    """
    suit_discarded = make_test_atom(
        text="business suit discarded on floor",
        source_slot="clothing",
        item_id="business_suit",
        tag_order=0,
        span_order=0,
        kind="clothing",
        garment_topologies=("top", "outerwear"),
        garment_states=("discarded",),
    )
    unbuttoned = make_test_atom(
        text="unbuttoned",
        source_slot="clothing_state",
        item_id="unbuttoned",
        tag_order=1,
        span_order=0,
        kind="clothing_state",
        garment_states=("opened",),
    )
    return LevelAFixtureCase(
        case_id="A-4",
        name="背景遗弃物状态清理反例",
        description="西装被标记为 discarded 降级为环境道具，不可作为在穿解扣动作承载主体，unbuttoned 必须被剔除",
        input_atoms=(suit_discarded, unbuttoned),
        expected_dropped_atom_ids=(unbuttoned.atom_id,),
        expected_survived_atom_ids=(suit_discarded.atom_id,),
        expected_rule_id="clothing_style_state_coherence",
        expected_reason_code="state_lacks_carrier",
        is_counterexample=True,
    )


def get_level_a_fixture_a5() -> LevelAFixtureCase:
    """
    用例 A-5 (同一服装多原子不自发满足叠穿合法反例)：
    同一件和服 (kimono) 产出两枚原子：主体原子(kimono, one_piece) + 下摆原子(kimono, bottom_skirt，同属 kimono 实体)
    搭配叠穿修饰状态原子(skirt_under_kimono)
    预期：由于两枚和服原子属于同一个服装实体 (entity_id 相同)，不能自发满足 skirt_under_kimono 的内外双层独立服装实体要求，
          skirt_under_kimono 被 Drop，两枚和服原子保留。
    """
    kimono_body = make_test_atom(
        text="traditional japanese kimono",
        source_slot="clothing",
        item_id="kimono",
        tag_order=0,
        span_order=0,
        kind="clothing",
        garment_topologies=("one_piece",),
        garment_states=("worn",),
    )
    kimono_skirt_part = make_test_atom(
        text="kimono long skirt hem",
        source_slot="clothing",
        item_id="kimono",
        tag_order=0,
        span_order=1,
        kind="clothing",
        garment_topologies=("bottom_skirt",),
        garment_states=("worn",),
    )
    skirt_under_kimono = make_test_atom(
        text="skirt under kimono",
        source_slot="clothing_state",
        item_id="skirt_under_kimono",
        tag_order=1,
        span_order=0,
        kind="clothing_state",
        garment_states=("worn",),
    )
    return LevelAFixtureCase(
        case_id="A-5",
        name="同一服装多原子不自发满足叠穿反例",
        description="同一件和服产出的主体与下摆多枚原子属于同一实体，不可自发满足 skirt_under_kimono 的内外双层实体要求，状态必须被剔除",
        input_atoms=(kimono_body, kimono_skirt_part, skirt_under_kimono),
        expected_dropped_atom_ids=(skirt_under_kimono.atom_id,),
        expected_survived_atom_ids=(kimono_body.atom_id, kimono_skirt_part.atom_id),
        expected_rule_id="clothing_style_state_coherence",
        expected_reason_code="layering_mismatch",
        is_counterexample=True,
    )


def get_level_a_fixture_a6() -> LevelAFixtureCase:
    """
    用例 A-6 (同一预设展开两件衣物保留两个独立实体反例)：
    同一预设 (preset:preset_ol) 展开：
      - 上装衬衫 (shirts_blouses, top) 包含 2 枚原子：主体 (shirts_blouses) + 领口细节 (formal collar)
      - 下装包臀裙 (pencil_skirt, bottom_skirt) 包含 1 枚原子：裙子主体 (pencil_skirt)
    搭配掀裙状态原子 (lifted_up, 需要 skirt 承载)
    预期：
      - 尽管上装与下装共享上游来源 preset_ol，但必须按 build_garment_entity_key 分别聚合为 2 个独立实体：
        `preset:preset_ol#clothing#shirts_blouses` 与 `preset:preset_ol#clothing#pencil_skirt`；
      - lifted_up 通过裙摆能力 (skirt_capability_unique_match) 成功绑定到 pencil_skirt 实体；
      - 上装衬衫两枚原子、下装裙子原子与 lifted_up 状态原子全部合法保留，0 drop！
    """
    shirt_main = make_test_atom(
        text="white button-down shirt",
        source_slot="clothing",
        item_id="shirts_blouses",
        tag_order=0,
        span_order=0,
        kind="clothing",
        mode="preset",
        garment_topologies=("top",),
        garment_states=("worn",),
    )
    object.__setattr__(shirt_main.origin, "parent_ids", ("preset_ol",))
    object.__setattr__(shirt_main.provenance, "parent_ids", ("preset_ol",))

    shirt_collar = make_test_atom(
        text="formal spread collar",
        source_slot="clothing",
        item_id="shirts_blouses",
        tag_order=0,
        span_order=1,
        kind="clothing",
        mode="preset",
        garment_topologies=("top",),
        garment_states=("worn",),
    )
    object.__setattr__(shirt_collar.origin, "parent_ids", ("preset_ol",))
    object.__setattr__(shirt_collar.provenance, "parent_ids", ("preset_ol",))

    skirt = make_test_atom(
        text="black pencil skirt",
        source_slot="clothing",
        item_id="pencil_skirt",
        tag_order=1,
        span_order=0,
        kind="clothing",
        mode="preset",
        garment_topologies=("bottom_skirt",),
        garment_states=("worn",),
    )
    object.__setattr__(skirt.origin, "parent_ids", ("preset_ol",))
    object.__setattr__(skirt.provenance, "parent_ids", ("preset_ol",))

    lifted_up = make_test_atom(
        text="skirt lifted up",
        source_slot="clothing_state",
        item_id="lifted_up",
        tag_order=2,
        span_order=0,
        kind="clothing_state",
        garment_states=("lifted",),
    )

    return LevelAFixtureCase(
        case_id="A-6",
        name="同一预设来源下的两件衣物保留两个独立实体反例",
        description="同一预设展开的衬衫与包臀裙必须生成两个独立实体；掀裙动作精准绑定包臀裙，衬衫与裙子全量合法保留",
        input_atoms=(shirt_main, shirt_collar, skirt, lifted_up),
        expected_dropped_atom_ids=(),
        expected_survived_atom_ids=(shirt_main.atom_id, shirt_collar.atom_id, skirt.atom_id, lifted_up.atom_id),
        expected_rule_id="clothing_style_state_coherence",
        expected_reason_code="none",
        is_counterexample=True,
    )


def get_level_a_fixture_a7() -> LevelAFixtureCase:
    """
    用例 A-7 (两件衣物定向丢弃与输入换序不变性反例契约)：
    场景中同时存在两件在穿衣物：
      - 上装衬衫 (shirts_blouses, top, worn)
      - 下装百褶裙 (pleated_skirt, bottom_skirt, worn)
    伴随定向遗弃状态原子 (discarded)，明确指定 target 为 pleated_skirt (下装被脱下遗弃在旁)。
    预期：
      - 依据绑定阶梯锁定 pleated_skirt 实体进行同步注销；
      - 上装 shirts_blouses 实体 100% 完好不受影响 (仍为 worn 且保留)；
      - 换序不变性 (Permutation Invariance)：无论输入原子顺序如何改变，结果严格一致，绝不盲选 entities[0]。
    """
    shirt = make_test_atom(
        text="tailored dress shirt",
        source_slot="clothing",
        item_id="shirts_blouses",
        tag_order=0,
        span_order=0,
        kind="clothing",
        garment_topologies=("top",),
        garment_states=("worn",),
    )
    skirt = make_test_atom(
        text="navy pleated skirt",
        source_slot="clothing",
        item_id="pleated_skirt",
        tag_order=1,
        span_order=0,
        kind="clothing",
        garment_topologies=("bottom_skirt",),
        garment_states=("worn",),
    )
    discarded_skirt = make_test_atom(
        text="skirt discarded on chair",
        source_slot="clothing_state",
        item_id="discarded",
        tag_order=2,
        span_order=0,
        kind="clothing_state",
        garment_states=("discarded",),
        target_id="pleated_skirt",
    )

    return LevelAFixtureCase(
        case_id="A-7",
        name="两件衣物定向丢弃与输入换序不变性反例契约",
        description="两件衣物并存时，下装被定向 discarded 仅注销下装实体，上装衬衫严格完好保留，且结果对原子输入换序绝对不变",
        input_atoms=(shirt, skirt, discarded_skirt),
        expected_dropped_atom_ids=(),
        expected_survived_atom_ids=(shirt.atom_id, skirt.atom_id, discarded_skirt.atom_id),
        expected_rule_id="clothing_style_state_coherence",
        expected_reason_code="none",
        is_counterexample=True,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 服装实体聚合与绑定判定生产级收敛 (单源收敛：直接复用 lib.conflict_resolver)
# ═══════════════════════════════════════════════════════════════════════════

from lib.conflict_resolver import (
    ALLOWED_BUTTON_STYLES,
    NON_SKIRT_ONE_PIECE,
    BindingResult,
    BindingStatus,
    GarmentCarrierEntity,
    build_garment_entity_key,
    extract_garment_entities,
    find_bound_carrier,
    is_garment_compatible_with_state,
)


# ═══════════════════════════════════════════════════════════════════════════
# Level B 节点端到端集成测试夹具模型定义
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class LevelBFixtureCase:
    """Level B 节点端到端集成测试用例定义 (黑盒解耦断言契约)"""
    case_id: str
    name: str
    description: str
    inputs: Dict[str, Any]
    expected_positive_must_contain: Tuple[str, ...]
    expected_positive_must_not_contain: Tuple[str, ...]
    expected_drop_target_substrings: Tuple[str, ...] = ()
    expected_drop_rule_ids: Tuple[str, ...] = ()  # 解耦设计：不提前硬编码具体内部规则名，留空允许任何合规消解路径
    enforce_single_drop: bool = True  # 黑盒铁律：断言每个被 drop 的原子在审计账本中仅记录一次，零重复删除
    enforce_ledger_conservation: bool = True  # 黑盒铁律：断言全局审计账本严格满足输入输出完全守恒方程


LEVEL_B_CASES: Tuple[LevelBFixtureCase, ...] = (
    LevelBFixtureCase(
        case_id="TC-INT-001",
        name="全裸与服装及解扣状态全量清除",
        description="L5 极致全裸下，黑盒断言正向文本中绝无西装与解扣残留、每个被剔除项仅被删除一次、审计账本完全守恒",
        inputs={
            "预设模板": "无 (None)",
            "风格配方": "无 (None)",
            "场景大类": "无 (None)",
            "剧情主题": "无 (None)",
            "景别构图": "无 (None)",
            "拍摄视角": "无 (None)",
            "裸露等级": "L5 极致全裸 (Full Nude)",
            "服装款式": "职场西装 (Business Suit)",
            "服装状态": "解开纽扣 (Unbuttoned)",
            "发型发色": "无 (None)",
            "饰品头饰": "无 (None)",
            "妆容细节": "无 (None)",
            "姿势动作": "无 (None)",
            "情绪表情": "无 (None)",
            "光影预设": "无 (None)",
            "胶片风格": "无 (None)",
            "液体效果": "无 (None)",
            "纹身标记": "无 (None)",
            "道具物件": "无 (None)",
            "角色设定": "无 (None)",
            "真实微瑕": "无 (None)",
            "画质等级": "标准画质 (Standard)",
            "prompt_seed": 42,
        },
        expected_positive_must_contain=("full nude",),
        expected_positive_must_not_contain=("business suit", "unbuttoned", "suit"),
        expected_drop_rule_ids=(),  # 解耦：不硬编码具体规则 ID
        expected_drop_target_substrings=("business", "unbuttoned"),
        enforce_single_drop=True,
        enforce_ledger_conservation=True,
    ),
    LevelBFixtureCase(
        case_id="TC-INT-002",
        name="普通着装自洽正常流",
        description="针织毛衣 + normal + L2 差分微露，无规则误杀，正向提示词正常包含毛衣与合规状态",
        inputs={
            "预设模板": "无 (None)",
            "风格配方": "无 (None)",
            "场景大类": "无 (None)",
            "剧情主题": "无 (None)",
            "景别构图": "无 (None)",
            "拍摄视角": "无 (None)",
            "裸露等级": "L2 差分微露 (Partially Exposed)",
            "服装款式": "针织毛衣 (Knit Sweater)",
            "服装状态": "完好着装 (Normal)",
            "发型发色": "无 (None)",
            "饰品头饰": "无 (None)",
            "妆容细节": "无 (None)",
            "姿势动作": "无 (None)",
            "情绪表情": "无 (None)",
            "光影预设": "无 (None)",
            "胶片风格": "无 (None)",
            "液体效果": "无 (None)",
            "纹身标记": "无 (None)",
            "道具物件": "无 (None)",
            "角色设定": "无 (None)",
            "真实微瑕": "无 (None)",
            "画质等级": "标准画质 (Standard)",
            "prompt_seed": 42,
        },
        expected_positive_must_contain=("sweater",),
        expected_positive_must_not_contain=(),
        expected_drop_rule_ids=(),
        expected_drop_target_substrings=(),
    ),
)


def get_all_level_a_fixtures() -> Tuple[LevelAFixtureCase, ...]:
    """返回全量 Level A 测试夹具用例 (A-1 ~ A-7)"""
    return (
        get_level_a_fixture_a1(),
        get_level_a_fixture_a2(),
        get_level_a_fixture_a3(),
        get_level_a_fixture_a4(),
        get_level_a_fixture_a5(),
        get_level_a_fixture_a6(),
        get_level_a_fixture_a7(),
    )


def get_all_level_b_fixtures() -> Tuple[LevelBFixtureCase, ...]:
    """返回全量 Level B 集成测试夹具用例"""
    return LEVEL_B_CASES

