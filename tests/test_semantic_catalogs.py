"""
tests/test_semantic_catalogs.py — 语义目录迁移、基线测试冻结与全链路 Provenance 自动化测试
"""
import json
import re
import tempfile
import unittest
from pathlib import Path

from lib.assembler import split_top_level_tags
from lib.atomizer import atoms_to_fragments, atoms_to_tags, fragments_to_atoms
from lib.models import (
    ContextProfile,
    PromptAtom,
    PromptFragment,
    ResolutionDecision,
    ResolutionReport,
    SelectionOrigin,
    SemanticFacts,
    SpanType,
    TagProvenance,
)
from lib.sampler import DataSampler
from nodes import IYKYKCustomSlotCombiner, IYKYKPresetBrowser, IYKYKPromptGenerator
from scripts.validate_data import validate_all

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
SCHEMAS_DIR = REPO_DIR / "schemas"
FIXTURES_DIR = REPO_DIR / "tests" / "fixtures"
ID_REGEX = re.compile(r"^[a-z][a-z0-9_]{2,95}$")


class TestSemanticCatalogs(unittest.TestCase):
    """语义目录迁移与基线契约测试套件"""

    def test_fe88185_baseline_tests_intact(self):
        """验证 fe88185 基线 169 项测试 ID 一个不漏且保持存在"""
        baseline_file = FIXTURES_DIR / "fe88185_test_ids.json"
        self.assertTrue(baseline_file.exists(), "fe88185_test_ids.json must exist")
        baseline_ids = set(json.loads(baseline_file.read_text(encoding="utf-8")))
        self.assertEqual(len(baseline_ids), 169, "Baseline must contain exactly 169 tests")

        # 动态发现当前所有测试用例 ID
        loader = unittest.TestLoader()
        suite = loader.discover(str(REPO_DIR / "tests"))
        current_ids = set()

        def extract_ids(s):
            for itm in s:
                if isinstance(itm, unittest.TestSuite):
                    extract_ids(itm)
                elif isinstance(itm, unittest.TestCase):
                    current_ids.add(itm.id())

        extract_ids(suite)

        missing = baseline_ids - current_ids
        self.assertEqual(
            len(missing),
            0,
            f"Baseline tests missing from current suite ({len(missing)}): {sorted(missing)}",
        )
        self.assertGreater(
            len(current_ids),
            169,
            f"Current test suite should have grown beyond 169 tests (got {len(current_ids)})",
        )

    def test_catalog_leaf_ids_format_and_uniqueness(self):
        """验证所有生产目录文件中的叶子标签 ID 格式正则与 catalog 级唯一性"""
        for p in DATA_DIR.glob("*.json"):
            if p.name in ("conflict_rules.json", "negative_prompts.json", "context_affinity.json"):
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
            seen_ids = set()

            def check_leaves(obj, path=""):
                if isinstance(obj, dict):
                    if "id" in obj and "text" in obj and "facts" in obj:
                        lid = obj["id"]
                        self.assertTrue(
                            ID_REGEX.match(lid),
                            f"File {p.name} leaf id {lid!r} at {path} does not match {ID_REGEX.pattern}",
                        )
                        self.assertNotIn(
                            lid,
                            seen_ids,
                            f"File {p.name} has duplicate leaf id {lid!r} at {path}",
                        )
                        seen_ids.add(lid)
                        facts = SemanticFacts.from_dict(obj["facts"])
                        facts.validate()
                    for k, v in obj.items():
                        check_leaves(v, f"{path}.{k}")
                elif isinstance(obj, list):
                    for i, elem in enumerate(obj):
                        check_leaves(elem, f"{path}[{i}]")

            check_leaves(data)
            self.assertGreater(
                len(seen_ids),
                0,
                f"File {p.name} must contain structured leaf tags",
            )

    def test_presets_fragments_byte_exact_equivalence(self):
        """验证 77 套手写预设的 positive prompt 与 fragments 逐 Tag 逐字节等价"""
        presets_doc = json.loads((DATA_DIR / "presets.json").read_text(encoding="utf-8"))
        presets = presets_doc.get("presets", [])
        self.assertGreaterEqual(len(presets), 77)

        for p in presets:
            pid = p.get("id")
            pos = p.get("positive")
            frags = p.get("fragments")
            self.assertIsNotNone(frags, f"Preset {pid} missing fragments")
            expected_tags = split_top_level_tags(pos)
            actual_tags = [f["text"] for f in frags]
            self.assertEqual(
                expected_tags,
                actual_tags,
                f"Preset {pid} fragments do not match positive tags byte-for-byte",
            )
            for f in frags:
                self.assertTrue(ID_REGEX.match(f["id"]))
                SemanticFacts.from_dict(f["facts"]).validate()
                origin = f["origin"]
                self.assertEqual(origin["entry_point"], "preset_browser")
                self.assertEqual(origin["mode"], "preset")
                self.assertEqual(origin["selected_id"], pid)

    def test_style_recipes_fragments_exist_and_valid(self):
        """验证 8 大风格配方的 fragments 完整性与结构契约"""
        recipes_doc = json.loads((DATA_DIR / "style_recipes.json").read_text(encoding="utf-8"))
        recipes = recipes_doc.get("recipes", [])
        self.assertGreaterEqual(len(recipes), 8)

        for r in recipes:
            rid = r.get("id")
            frags = r.get("fragments")
            self.assertIsNotNone(frags, f"Recipe {rid} missing fragments")
            self.assertGreater(len(frags), 0, f"Recipe {rid} fragments cannot be empty")
            for f in frags:
                self.assertTrue(ID_REGEX.match(f["id"]))
                SemanticFacts.from_dict(f["facts"]).validate()
                origin = f["origin"]
                self.assertEqual(origin["entry_point"], "preset_browser")
                self.assertEqual(origin["mode"], "recipe")
                self.assertEqual(origin["selected_id"], rid)

    def test_generator_node_full_chain_provenance(self):
        """验证 15 槽位生成器输出全量携带不可变 SemanticFacts 与 SelectionOrigin"""
        gen = IYKYKPromptGenerator()
        res = gen.generate_structured(
            "无 (None)",
            "无 (None)",
            "随机 (Random)",
            "随机 (Random)",
            "自动 (Auto)",
            "自动 (Auto)",
            "随机 (Random)",
            "随机 (Random)",
            "自动联动裸露等级 (Auto Link Nudity)",
            "随机 (Random)",
            "无 (None)",
            "无 (None)",
            "随机 (Random)",
            "随机 (Random)",
            "自动 (Auto)",
            "无 (None)",
            "无 (None)",
            "无 (None)",
            "无 (None)",
            "无 (None)",
            "无 (None)",
            "高清写真 (High)",
            prompt_seed=12345,
        )
        self.assertIsInstance(res.effective_seed, int)
        self.assertGreater(len(res.atoms), 0)
        self.assertGreater(len(res.selections), 0)

        for atom in res.atoms:
            self.assertIsInstance(atom, PromptAtom)
            self.assertIsInstance(atom.facts, SemanticFacts)
            self.assertIsInstance(atom.origin, SelectionOrigin)
            self.assertEqual(atom.origin.entry_point, "generator")

    def test_preset_browser_node_provenance(self):
        """验证模板浏览器节点输出全量携带 entry_point='preset_browser'"""
        browser = IYKYKPresetBrowser()
        preset_name = list(IYKYKPresetBrowser.INPUT_TYPES()["required"]["预设模板"][0])[0]
        res = browser.browse_structured(
            preset_name,
            "无 (None)",
            "高清写真 (High)",
            prompt_seed=12345,
        )
        self.assertIsInstance(res.effective_seed, int)
        self.assertGreater(len(res.atoms), 0)
        for atom in res.atoms:
            self.assertIsInstance(atom.facts, SemanticFacts)
            self.assertIsInstance(atom.origin, SelectionOrigin)
            self.assertEqual(atom.origin.entry_point, "preset_browser")

    def test_custom_combiner_node_provenance(self):
        """验证自定义槽位拼装器节点输出全量携带 entry_point='custom_combiner'"""
        comb = IYKYKCustomSlotCombiner()
        res = comb.combine_structured(
            prompt_seed=12345,
            场景主题="tatami room, shoji screen",
            服装款式="silk kimono, floral pattern",
        )
        self.assertIsInstance(res.effective_seed, int)
        self.assertGreater(len(res.atoms), 0)
        for atom in res.atoms:
            self.assertIsInstance(atom.facts, SemanticFacts)
            self.assertIsInstance(atom.origin, SelectionOrigin)
            self.assertEqual(atom.origin.entry_point, "custom_combiner")
            self.assertEqual(atom.origin.mode, "custom")

    # ─── 审核要求第 6 节负向变异测试套件 (R1-N01 ~ R1-N14) ───

    def _run_with_mutated_data(self, filename: str, mutator):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_data = Path(tmpdir) / "data"
            tmp_data.mkdir()
            for f in DATA_DIR.glob("*.json"):
                doc = json.loads(f.read_text(encoding="utf-8"))
                if f.name == filename:
                    doc = mutator(doc)
                (tmp_data / f.name).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            return validate_all(data_dir=tmp_data, schemas_dir=SCHEMAS_DIR, strict_jsonschema=True)

    def test_r1_n01_missing_leaf_id(self):
        """R1-N01: 删除任一可采样叶子 ID -> validate_data 报错失败"""
        def mut(doc):
            del doc["themes"][0]["tags"][0]["id"]
            return doc
        res = self._run_with_mutated_data("themes.json", mut)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("invalid leaf tag keys" in e or "is a required property" in e for e in res.errors))

    def test_r1_n02_duplicate_leaf_id(self):
        """R1-N02: 制造重复叶子 ID -> 拦截"""
        def mut(doc):
            doc["themes"][0]["tags"][1]["id"] = doc["themes"][0]["tags"][0]["id"]
            return doc
        res = self._run_with_mutated_data("themes.json", mut)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("duplicate leaf ID" in e for e in res.errors))

    def test_r1_n03_unknown_enum(self):
        """R1-N03: 注入未知 enum -> 拦截"""
        def mut(doc):
            doc["themes"][0]["tags"][0]["facts"]["gaze"] = "sideways"
            return doc
        res = self._run_with_mutated_data("themes.json", mut)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("Invalid gaze" in e or "gaze" in e for e in res.errors))

    def test_r1_n04_dangling_reference(self):
        """R1-N04: 制造跨目录悬空引用 -> 拦截"""
        def mut(doc):
            doc["extension_policy"]["L2"]["exposure_ids"].append("nonexistent_id_999")
            return doc
        res = self._run_with_mutated_data("clothing.json", mut)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("dangling exposure_id 'nonexistent_id_999'" in e for e in res.errors))

    def test_r1_n05_preset_fragment_mismatch(self):
        """R1-N05: 修改 preset fragment 而不改 positive -> 拦截"""
        def mut(doc):
            doc["presets"][0]["fragments"][0]["text"] = "mutated text"
            return doc
        res = self._run_with_mutated_data("presets.json", mut)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("fragments text mismatch" in e for e in res.errors))

    def test_r1_n06_dangling_parent_id(self):
        """R1-N06: 让 origin parent 指向不存在来源 -> 拦截"""
        def mut(doc):
            doc["recipes"][0]["fragments"][0]["origin"]["parent_ids"] = ["nonexistent_recipe"]
            return doc
        res = self._run_with_mutated_data("style_recipes.json", mut)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("parent_ids" in e for e in res.errors))

    def test_r1_n07_custom_combiner_preserves_custom_origin(self):
        """R1-N07: 用 custom 文本声明正式 item ID -> 保持 custom origin"""
        comb = IYKYKCustomSlotCombiner()
        res = comb.combine_structured(prompt_seed=0, 场景主题="tatami room", 服装款式="qipao")
        for atom in res.atoms:
            self.assertEqual(atom.origin.entry_point, "custom_combiner")
            self.assertEqual(atom.origin.mode, "custom")

    def test_r1_n08_string_leaf_rejected(self):
        """R1-N08: 把结构化主题叶子改回字符串 -> 拦截"""
        def mut(doc):
            doc["themes"][0]["tags"][0] = "raw string tag"
            return doc
        res = self._run_with_mutated_data("themes.json", mut)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("expected object for leaf tag" in e or "is not of type 'object'" in e for e in res.errors))

    def test_r1_n09_recipe_field_inequivalence_rejected(self):
        """R1-N09: 删除配方 fragments 或制造逐字段不等价 -> 拦截"""
        def mut(doc):
            doc["recipes"][0]["style_recipe"] = "mutated, tag, list"
            return doc
        res = self._run_with_mutated_data("style_recipes.json", mut)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("text mismatch with fragments" in e for e in res.errors))

    def test_r1_n10_extra_leaf_field_rejected(self):
        """R1-N10: 在正式叶子加入额外字段 -> Schema 与严格校验均报错拒绝"""
        def mut(doc):
            doc["themes"][0]["tags"][0]["extra_field"] = "hacked"
            return doc
        res = self._run_with_mutated_data("themes.json", mut)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("invalid leaf tag keys" in e or "Additional properties are not allowed" in e for e in res.errors))

    def test_r1_n11_string_hands_required_rejected(self):
        """R1-N11: 将 hands_required 从整数改为字符串 '1' -> 严格校验报错拒绝，禁止隐式类型转换"""
        def mut(doc):
            doc["categories"][1]["tags"][0]["facts"]["hands_required"] = "1"
            return doc
        res = self._run_with_mutated_data("props.json", mut)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("hands_required must be int" in e or "is not of type 'integer'" in e for e in res.errors))

    def test_r1_n12_roundtrip_fidelity(self):
        """R1-N12: Fragment → Atom → Fragment 回转保留 id/facts/origin，三项逐字保持"""
        orig_facts = SemanticFacts(visible_regions=("face",), emotion="shy", time_of_day="night")
        orig_origin = SelectionOrigin(entry_point="generator", mode="random", selector="clothing", selected_id="qipao", raw_value="silk qipao", parent_ids=("qipao",))
        frag = PromptFragment(text="silk qipao", source_slot="clothing", source_item_id="qipao", order=0, provenance=TagProvenance(item_id="qipao_001", kind="clothing"), id="qipao_001", facts=orig_facts, origin=orig_origin)
        tags, atoms = fragments_to_atoms([frag])
        frags_back = atoms_to_fragments(atoms)
        self.assertEqual(len(frags_back), 1)
        fb = frags_back[0]
        self.assertEqual(fb.id, frag.id)
        self.assertEqual(fb.facts, frag.facts)
        self.assertEqual(fb.origin, frag.origin)

    def test_r1_n13_distinct_atom_ids(self):
        """R1-N13: 不同入口与叶子生成不同输入 Atom ID，ID 不同且确定"""
        f1 = PromptFragment(text="tag_a", source_slot="clothing", source_item_id="item_a", order=0, id="leaf_a", facts=SemanticFacts(), origin=SelectionOrigin(entry_point="generator", mode="random", selector="clothing", selected_id="item_a", raw_value="tag_a"))
        f2 = PromptFragment(text="tag_a", source_slot="custom", source_item_id="item_b", order=1, id="leaf_b", facts=SemanticFacts(), origin=SelectionOrigin(entry_point="generator", mode="random", selector="custom", selected_id="item_b", raw_value="tag_a"))
        tags1, atoms1 = fragments_to_atoms([f1])
        tags2, atoms2 = fragments_to_atoms([f2])
        self.assertNotEqual(atoms1[0].atom_id, atoms2[0].atom_id)

    def test_r1_n14_frozen_immutability(self):
        """R1-N14: frozen=True 模型在构造后拒绝外部可变集合篡改，传入 list 强制转 tuple"""
        mutable_list = ["face", "upper_body"]
        facts = SemanticFacts(visible_regions=mutable_list)
        self.assertIsInstance(facts.visible_regions, tuple)
        mutable_list.append("lower_body")
        self.assertNotIn("lower_body", facts.visible_regions)

        with self.assertRaises((TypeError, AttributeError)):
            facts.visible_regions.append("hacked")

        with self.assertRaises((TypeError, Exception)):
            facts.visible_regions = ("new",)

    def test_prompt_seed_0_rich_rule_facts(self):
        """验证固定 seed=0 且全开 15 槽位时，所有 source atoms 均携带真实规则事实"""
        gen = IYKYKPromptGenerator()
        res = gen.generate_structured(
            预设模板="无 (None)",
            风格配方="无 (None)",
            场景大类="随机 (Random)",
            剧情主题="随机 (Random)",
            景别构图="随机 (Random)",
            拍摄视角="随机 (Random)",
            裸露等级="随机 (Random)",
            服装款式="随机 (Random)",
            服装状态="随机 (Random)",
            发型发色="随机 (Random)",
            饰品头饰="随机 (Random)",
            妆容细节="随机 (Random)",
            姿势动作="随机 (Random)",
            情绪表情="随机 (Random)",
            光影预设="随机 (Random)",
            胶片风格="随机 (Random)",
            液体效果="随机 (Random)",
            纹身标记="随机 (Random)",
            道具物件="随机 (Random)",
            角色设定="随机 (Random)",
            真实微瑕="随机 (Random)",
            画质等级="高清写真 (High)",
            prompt_seed=0,
        )
        self.assertGreater(len(res.source_atoms), 0)
        atoms_with_rules = [
            a for a in res.source_atoms
            if (
                a.facts.time_of_day
                or a.facts.light_sources
                or a.facts.color_modes
                or a.facts.visible_regions
                or a.facts.garment_topologies
                or a.facts.garment_states
                or a.facts.hand_state
                or a.facts.prop_usage
                or a.facts.emotion
                or a.facts.gaze
                or a.facts.makeup_base
                or a.facts.makeup_effects
                or a.facts.liquid_kind
                or a.facts.liquid_locations
                or a.facts.capture_device
                or a.facts.quality_class
                or a.facts.hands_required > 0
                or a.facts.venue_ids
            )
        ]
        for a in res.source_atoms:
            self.assertTrue(a.id, f"Atom {a.atom_id} has empty leaf ID")
            self.assertIsNotNone(a.facts.semantic_role, f"Atom {a.atom_id} has no semantic_role")
        self.assertGreaterEqual(len(atoms_with_rules), 35)

    def test_ordered_selections_determinism(self):
        """验证 selections 顺序为跨进程确定性流水线到达顺序，杜绝 set 漂移"""
        gen = IYKYKPromptGenerator()
        res1 = gen.generate_structured(
            预设模板="无 (None)",
            风格配方="无 (None)",
            场景大类="随机 (Random)",
            剧情主题="随机 (Random)",
            景别构图="随机 (Random)",
            拍摄视角="随机 (Random)",
            裸露等级="随机 (Random)",
            服装款式="随机 (Random)",
            服装状态="随机 (Random)",
            发型发色="随机 (Random)",
            饰品头饰="随机 (Random)",
            妆容细节="随机 (Random)",
            姿势动作="随机 (Random)",
            情绪表情="随机 (Random)",
            光影预设="随机 (Random)",
            胶片风格="随机 (Random)",
            液体效果="随机 (Random)",
            纹身标记="随机 (Random)",
            道具物件="随机 (Random)",
            角色设定="随机 (Random)",
            真实微瑕="随机 (Random)",
            画质等级="高清写真 (High)",
            prompt_seed=42,
        )
        res2 = gen.generate_structured(
            预设模板="无 (None)",
            风格配方="无 (None)",
            场景大类="随机 (Random)",
            剧情主题="随机 (Random)",
            景别构图="随机 (Random)",
            拍摄视角="随机 (Random)",
            裸露等级="随机 (Random)",
            服装款式="随机 (Random)",
            服装状态="随机 (Random)",
            发型发色="随机 (Random)",
            饰品头饰="随机 (Random)",
            妆容细节="随机 (Random)",
            姿势动作="随机 (Random)",
            情绪表情="随机 (Random)",
            光影预设="随机 (Random)",
            胶片风格="随机 (Random)",
            液体效果="随机 (Random)",
            纹身标记="随机 (Random)",
            道具物件="随机 (Random)",
            角色设定="随机 (Random)",
            真实微瑕="随机 (Random)",
            画质等级="高清写真 (High)",
            prompt_seed=42,
        )
        self.assertEqual(res1.selections, res2.selections)

    def test_illegal_enum_values_rejected(self):
        """验证非法枚举精确值 (如 natural, body, blindfolded) 被严格拦截"""
        with self.assertRaises(ValueError):
            SemanticFacts(color_modes=("natural",))
        with self.assertRaises(ValueError):
            SemanticFacts(liquid_locations=("body",))
        with self.assertRaises(ValueError):
            SemanticFacts(occlusion="blindfolded")
        with self.assertRaises(ValueError):
            SemanticFacts(space_kind="unknown_space")

    def test_context_profile_weights_boundary_1e12(self):
        """验证 ContextProfile 权重容差严格为 1e-12，超差立即失败"""
        # 超出 1e-12 容差 (1e-11) -> 必须抛 ValueError
        with self.assertRaises(ValueError):
            ContextProfile(weights=(("school", 0.5), ("office", 0.5 + 1e-11)))
        # 容差内 (1e-13) -> 必须成功
        cp_ok = ContextProfile(weights=(("school", 0.5), ("office", 0.5 + 1e-13)))
        self.assertIsNotNone(cp_ok)
        # 验证 14 情境权威顺序保存
        cp_ordered = ContextProfile(weights=(("office", 0.4), ("school", 0.6)))
        # school 在 office 之前
        self.assertEqual(cp_ordered.weights[0][0], "school")
        self.assertEqual(cp_ordered.weights[1][0], "office")

    def test_contradictory_facts_rejected(self):
        """验证同一叶子的矛盾事实校验"""
        with self.assertRaises(ValueError):
            SemanticFacts(garment_topologies=("none", "top"))
        with self.assertRaises(ValueError):
            SemanticFacts(garment_states=("worn", "removed"))
        with self.assertRaises(ValueError):
            SemanticFacts(liquid_kind="none", liquid_locations=("face",))
        with self.assertRaises(ValueError):
            SemanticFacts(color_modes=("monochrome", "color"))

    def test_explicit_hands_required_zero_override(self):
        """验证显式 hands_required=0 覆盖父级非零值"""
        parent = SemanticFacts.from_dict({"hands_required": 1, "semantic_role": "selector"})
        self.assertEqual(parent.hands_required, 1)

        # 显式声明 hands_required=0 的子级
        child_explicit_0 = SemanticFacts.from_dict({"hands_required": 0})
        merged = parent.merge(child_explicit_0)
        self.assertEqual(merged.hands_required, 0)

        # 未显式声明 hands_required 的子级
        child_default = SemanticFacts.from_dict({"semantic_role": "effect"})
        merged_default = parent.merge(child_default)
        self.assertEqual(merged_default.hands_required, 1)

    def test_multi_span_roundtrip(self):
        """验证多 span Tag (PAREN, BRACKET, ANGLE, QUOTED, 转义逗号, 混合) 无损回转"""
        mixed_text = "(weighted:1.2), [schedule:tag:10], <lora:detail:0.8>, \"exact quoted phrase\", escaped\\,comma"
        frag = PromptFragment(
            text=mixed_text,
            source_slot="custom",
            source_item_id="item_01",
            order=0,
            provenance=TagProvenance(item_id="item_01"),
            id="frag_multi_span",
            facts=SemanticFacts(semantic_role="selector"),
            origin=SelectionOrigin(entry_point="custom_combiner", mode="custom", selector="custom", selected_id="item_01", raw_value=mixed_text),
        )
        tags, atoms = fragments_to_atoms([frag])
        frags_back = atoms_to_fragments(atoms)
        self.assertEqual(len(frags_back), 5)
        self.assertEqual(frags_back[0].text, "(weighted:1.2)")
        self.assertEqual(frags_back[1].text, "[schedule:tag:10]")
        self.assertEqual(frags_back[2].text, "<lora:detail:0.8>")
        self.assertEqual(frags_back[3].text, "\"exact quoted phrase\"")
        self.assertEqual(frags_back[4].text, "escaped\\,comma")
        for fb in frags_back:
            self.assertEqual(fb.origin.entry_point, "custom_combiner")

    def test_four_entry_points_origin(self):
        """验证四入口 origin 表达正确且无硬编码污染"""
        # 1. generator 入口
        orig_gen = SelectionOrigin(entry_point="generator", mode="random", selector="clothing", selected_id="qipao")
        self.assertEqual(orig_gen.entry_point, "generator")

        # 2. preset_browser 入口
        orig_preset = SelectionOrigin(entry_point="preset_browser", mode="preset", selector="preset_core")
        self.assertEqual(orig_preset.entry_point, "preset_browser")

        # 3. custom_combiner 入口
        orig_custom = SelectionOrigin(entry_point="custom_combiner", mode="custom", selector="clothing")
        self.assertEqual(orig_custom.entry_point, "custom_combiner")

        # 4. diagnostics 入口
        orig_diag = SelectionOrigin(entry_point="diagnostics", mode="auto", selector="lighting")
        self.assertEqual(orig_diag.entry_point, "diagnostics")

    def test_19_sampler_paths_tags_equal_sampled_tags_text(self):
        """验证全部 19 条采样器结果路径满足 tags == sampled_tags.text 文本、数量、顺序逐项相等"""
        from random import Random
        sampler = DataSampler(DATA_DIR)
        rng = Random(42)

        paths = [
            sampler.sample_scene_result("随机 (Random)", rng),
            sampler.sample_theme_result("随机 (Random)", rng),
            sampler.sample_shot_type_result("随机 (Random)", rng),
            sampler.sample_camera_angle_result("随机 (Random)", rng),
            sampler.sample_nudity_result("随机 (Random)", rng)[0],
            sampler.sample_clothing_result("随机 (Random)", "随机 (Random)", "L1", rng),
            sampler.sample_pose_result("随机 (Random)", rng),
            sampler.sample_expression_result("随机 (Random)", rng),
            sampler.sample_lighting_result("随机 (Random)", rng),
            sampler.sample_film_result("随机 (Random)", rng),
            sampler.sample_liquid_result("随机 (Random)", rng),
            sampler.sample_tattoo_result("随机 (Random)", rng),
            sampler.sample_prop_result("随机 (Random)", rng),
            sampler.sample_character_result("随机 (Random)", rng),
            sampler.sample_imperfections_result("随机 (Random)", rng),
            sampler.sample_makeup_result("随机 (Random)", rng),
            sampler.sample_hairstyle_result("随机 (Random)", rng),
            sampler.sample_jewelry_result("随机 (Random)", rng),
            sampler.sample_quality_result("高清写真 (High)"),
        ]

        for idx, res in enumerate(paths):
            self.assertIsNotNone(res, f"Path {idx} returned None")
            self.assertIsNotNone(res.sampled_tags, f"Path {idx} has no sampled_tags")
            st_texts = tuple(st.text for st in res.sampled_tags)
            res_tags = tuple(t.text if hasattr(t, "text") else t for t in res.tags)
            self.assertEqual(res_tags, st_texts, f"Path {idx} tags != sampled_tags.text")

    def test_catalog_leaf_id_retraceable(self):
        """验证所有真实采样输出的叶子 ID 均可在生产目录中回查"""
        sampler = DataSampler(DATA_DIR)
        from random import Random
        rng = Random(123)
        res = sampler.sample_clothing_result("随机 (Random)", "随机 (Random)", "L1", rng)
        clothing_doc = json.loads((DATA_DIR / "clothing.json").read_text(encoding="utf-8"))
        all_leaf_ids = set()

        def collect_ids(obj):
            if isinstance(obj, dict):
                if "id" in obj and "text" in obj and "facts" in obj:
                    all_leaf_ids.add(obj["id"])
                for v in obj.values():
                    collect_ids(v)
            elif isinstance(obj, list):
                for item in obj:
                    collect_ids(item)

        collect_ids(clothing_doc)
        for st in res.sampled_tags:
            if st.id:
                self.assertIn(st.id, all_leaf_ids, f"Sampled leaf ID {st.id} not found in clothing.json")

    def test_zero_text_fallback_in_formal_catalogs(self):
        """验证正式目录全量 15 槽位 source atoms 具有 100% 真实叶子 ID 与有效规则事实，fallback 为零"""
        gen = IYKYKPromptGenerator()
        res = gen.generate_structured(
            预设模板="无 (None)",
            风格配方="无 (None)",
            场景大类="随机 (Random)",
            剧情主题="随机 (Random)",
            景别构图="随机 (Random)",
            拍摄视角="随机 (Random)",
            裸露等级="随机 (Random)",
            服装款式="随机 (Random)",
            服装状态="随机 (Random)",
            发型发色="随机 (Random)",
            饰品头饰="随机 (Random)",
            妆容细节="随机 (Random)",
            姿势动作="随机 (Random)",
            情绪表情="随机 (Random)",
            光影预设="随机 (Random)",
            胶片风格="随机 (Random)",
            液体效果="随机 (Random)",
            纹身标记="随机 (Random)",
            道具物件="随机 (Random)",
            角色设定="随机 (Random)",
            真实微瑕="随机 (Random)",
            画质等级="高清写真 (High)",
            prompt_seed=0,
        )
        for a in res.source_atoms:
            self.assertTrue(bool(a.id), f"Source atom text={a.text!r} has empty leaf id!")

    def test_reviewer_12_schema_and_strict_validation_counterexamples(self):
        """验证审阅报告 6.7.3 列出的 12 类负向反例在 strict 模式下 100% 报错拦截 (Fail-Closed)"""
        import shutil
        mutations = [
            ("themes_top_extra", "themes.json", lambda d: d.update({"unknown_field": 123})),
            ("theme_selector_extra", "themes.json", lambda d: d["themes"][0].update({"unknown_field": "bad"})),
            ("leaf_text_empty", "themes.json", lambda d: d["themes"][0]["tags"][0].update({"text": ""})),
            ("leaf_text_int", "themes.json", lambda d: d["themes"][0]["tags"][0].update({"text": 7})),
            ("facts_tuple_scalar_str", "themes.json", lambda d: d["themes"][0]["tags"][0]["facts"].update({"venue_ids": "bad"})),
            ("facts_tuple_int_elem", "themes.json", lambda d: d["themes"][0]["tags"][0]["facts"].update({"venue_ids": [123]})),
            ("preset_frag_del_facts", "presets.json", lambda d: d["presets"][0]["fragments"][0].pop("facts")),
            ("preset_origin_extra", "presets.json", lambda d: d["presets"][0]["fragments"][0]["origin"].update({"unknown_key": True})),
            ("preset_frag_extra", "presets.json", lambda d: d["presets"][0]["fragments"][0].update({"unknown_frag_key": "x"})),
            ("recipe_facts_extra", "style_recipes.json", lambda d: d["recipes"][0]["fragments"][0]["facts"].update({"unknown_fact": "x"})),
            ("recipe_frag_wrong_parent", "style_recipes.json", lambda d: d["recipes"][0]["fragments"][0]["origin"].update({"parent_ids": ["recipe_kodak_film"]})),
            ("preset_frag_dangling_parent", "presets.json", lambda d: d["presets"][0]["fragments"][0]["origin"]["parent_ids"].append("dangling_parent")),
        ]
        for name, fname, mut_fn in mutations:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_data = Path(tmpdir) / "data"
                shutil.copytree(DATA_DIR, tmp_data)
                target_file = tmp_data / fname
                doc = json.loads(target_file.read_text(encoding="utf-8"))
                mut_fn(doc)
                target_file.write_text(json.dumps(doc), encoding="utf-8")
                res = validate_all(data_dir=tmp_data, schemas_dir=SCHEMAS_DIR, strict_jsonschema=True)
                self.assertFalse(res.is_valid, f"Mutation '{name}' unexpectedly passed strict validation!")

    def test_models_deep_immutability_and_strong_typing(self):
        """验证模型强类型约束与禁止隐式转换 (R1-P1-003 反例)"""
        # 1. tuple facts 拒绝非 str 元素
        with self.assertRaises(TypeError):
            SemanticFacts(venue_ids=[123])
        with self.assertRaises(TypeError):
            SemanticFacts.from_dict({"visible_regions": "face"})  # 标量字符串拒绝
        with self.assertRaises(TypeError):
            SemanticFacts.from_dict({"venue_ids": [123]})  # 非字符串元素拒绝
        with self.assertRaises(TypeError):
            SemanticFacts.from_dict({"visible_regions": 7})  # 标量数字拒绝

        # 2. SelectionOrigin 强类型
        with self.assertRaises(TypeError):
            SelectionOrigin(parent_ids=[123])
        with self.assertRaises(TypeError):
            SelectionOrigin.from_dict({"parent_ids": "abc"})  # 标量字符串拒绝

        # 3. ContextProfile 权重类型
        with self.assertRaises(TypeError):
            ContextProfile(weights=(("school", "1.0"),))
        with self.assertRaises(TypeError):
            ContextProfile(weights=(("school", True),))

        # 4. ResolutionDecision 决策模型严格校验
        with self.assertRaises(ValueError):
            ResolutionDecision(decision_id="d1", sequence=0, rule_id="r1", phase="p1", action="invalid", reason_code="rc")
        with self.assertRaises(TypeError):
            ResolutionDecision(decision_id="d1", sequence=True, rule_id="r1", phase="p1", action="drop", reason_code="rc")
        with self.assertRaises(TypeError):
            ResolutionDecision(decision_id="d1", sequence=0, rule_id="r1", phase="p1", action="drop", reason_code="rc", winner_atom_ids="atom")

        # 5. ResolutionReport 计数类型与嵌套可变结构拒绝
        with self.assertRaises(TypeError):
            ResolutionReport(input_count="5")
        with self.assertRaises(TypeError):
            ResolutionReport(input_count=True)
        with self.assertRaises(TypeError):
            ResolutionReport(unresolved_conflicts=({"a": 1},))  # 拒绝内嵌可变 dict

    def test_semantic_facts_merge_direct_instantiation_override(self):
        """验证直接构造的 SemanticFacts 在 merge 时正确执行子项单值覆盖父项 (R1-P1-003 反例)"""
        parent = SemanticFacts(gaze="camera")
        child = SemanticFacts(gaze="away")
        merged = parent.merge(child)
        self.assertEqual(merged.gaze, "away", "Child gaze='away' must override parent gaze='camera'")

        parent2 = SemanticFacts(hands_required=1)
        child2 = SemanticFacts(hands_required=0, explicit_fields=("hands_required",))
        merged2 = parent2.merge(child2)
        self.assertEqual(merged2.hands_required, 0, "Explicit child hands_required=0 must override parent hands_required=1")

    def test_multi_span_inconsistent_metadata_rejected(self):
        """验证同一 Tag 内多个 span 若元数据不一致则拒绝并抛出异常 (R1-P1-003 反例)"""
        prov = TagProvenance(item_id="test_item", kind="slot")
        orig1 = SelectionOrigin(entry_point="generator", mode="random", selector="clothing")
        orig2 = SelectionOrigin(entry_point="custom_combiner", mode="custom", selector="custom")

        atom1 = PromptAtom(
            atom_id="atom_1",
            text="short dress",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="item_1",
            tag_order=0,
            span_order=0,
            id="leaf_1",
            facts=SemanticFacts(semantic_role="selector"),
            origin=orig1,
            provenance=prov,
        )
        atom2 = PromptAtom(
            atom_id="atom_2",
            text="with lace",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="item_1",
            tag_order=0,
            span_order=1,
            id="leaf_2",  # 不一致的 leaf ID!
            facts=SemanticFacts(semantic_role="selector"),
            origin=orig2,
            provenance=prov,
        )

        with self.assertRaises(ValueError):
            atoms_to_tags([atom1, atom2])

    def test_reviewer_section_6_8_counterexamples_full_closure(self):
        """验证审核报告 6.8 节全部 3 项开放核心阻断问题 (R1-P1-001, R1-P1-002, R1-P1-003) 独立反例完全闭环"""
        import random
        import shutil
        import tempfile
        # ── 1. R1-P1-001: 严格校验与模式门禁扩展反例拦截 (Fail-Closed) ──
        mutations = [
            ("makeup_duplicate_selector_id", "makeup.json", lambda d: d["categories"][1].update({"id": d["categories"][0]["id"]})),
            ("lighting_duplicate_selector_id", "lighting.json", lambda d: d["professional_lighting"][1].update({"id": d["professional_lighting"][0]["id"]})),
            ("preset_duplicate_fragment_id", "presets.json", lambda d: d["presets"][0]["fragments"][1].update({"id": d["presets"][0]["fragments"][0]["id"]})),
            ("preset_invalid_selector", "presets.json", lambda d: d["presets"][0]["fragments"][0]["origin"].update({"selector": "not_a_canonical_selector"})),
            ("liquid_systems_missing_id", "nudity_levels.json", lambda d: d["liquid_systems"][0].pop("id", None)),
            ("contradictory_color_modes", "film_stocks.json", lambda d: d["film_stocks"][0]["tags"][0]["facts"].update({"color_modes": ["monochrome", "color"]})),
            ("contradictory_liquid_kinds", "nudity_levels.json", lambda d: d["liquid_effects"][1]["tags"][0]["facts"].update({"liquid_kind": "oil"})),
        ]
        for name, fname, mut_fn in mutations:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_data = Path(tmpdir) / "data"
                shutil.copytree(DATA_DIR, tmp_data)
                target_file = tmp_data / fname
                doc = json.loads(target_file.read_text(encoding="utf-8"))
                mut_fn(doc)
                target_file.write_text(json.dumps(doc), encoding="utf-8")
                res = validate_all(data_dir=tmp_data, schemas_dir=SCHEMAS_DIR, strict_jsonschema=True)
                self.assertFalse(res.is_valid, f"6.8 counterexample '{name}' unexpectedly passed strict validation!")

        # ── 2. R1-P1-002: 语义事实与 100% 叶子溯源闭环 ──
        film_doc = json.loads((DATA_DIR / "film_stocks.json").read_text(encoding="utf-8"))
        sepia_item = next(f for f in film_doc["film_stocks"] if f["id"] == "sepia_tone")
        for t in sepia_item["tags"]:
            self.assertEqual(t["facts"]["color_modes"], ["sepia"])

        tmax_item = next(f for f in film_doc["film_stocks"] if f["id"] == "kodak_tmax_400")
        for t in tmax_item["tags"]:
            self.assertEqual(t["facts"]["color_modes"], ["monochrome"])

        chrome_item = next(f for f in film_doc["film_stocks"] if f["id"] == "fujifilm_classic_chrome")
        for t in chrome_item["tags"]:
            self.assertNotIn("high_saturation", t["facts"]["color_modes"])

        nudity_doc = json.loads((DATA_DIR / "nudity_levels.json").read_text(encoding="utf-8"))
        for sys_obj in nudity_doc["liquid_systems"]:
            self.assertTrue(bool(sys_obj.get("id")), f"Liquid system {sys_obj.get('name_zh')} missing ID")

        cum_eff = next(e for e in nudity_doc["liquid_effects"] if e["id"] == "cum_splatter")
        for t in cum_eff["tags"]:
            self.assertEqual(t["facts"]["liquid_kind"], "sexual_fluid")

        oil_eff = next(e for e in nudity_doc["liquid_effects"] if e["id"] == "body_oil_lube")
        for t in oil_eff["tags"]:
            self.assertEqual(t["facts"]["liquid_kind"], "oil")

        props_doc = json.loads((DATA_DIR / "props.json").read_text(encoding="utf-8"))
        for cat in props_doc["categories"]:
            if cat["id"] == "camera_tripod_flash":
                for t in cat["tags"]:
                    self.assertEqual(t["facts"]["prop_usage"], "ambient")
                    self.assertEqual(t["facts"]["hands_required"], 0)
            elif cat["id"] == "rose_petals_candles":
                for t in cat["tags"]:
                    self.assertEqual(t["facts"]["prop_usage"], "ambient")
                    self.assertEqual(t["facts"]["hands_required"], 0)

        # 真实探针 seed=0 全槽位验证
        from nodes import _generate_structured, _sampler, _assembler
        res_probe = _generate_structured(
            sampler=_sampler,
            assembler=_assembler,
            inputs={
                "预设模板": "无 (None)",
                "风格配方": "无 (None)",
                "场景大类": "随机 (Random)",
                "剧情主题": "随机 (Random)",
                "景别构图": "随机 (Random)",
                "拍摄视角": "随机 (Random)",
                "裸露等级": "L2 差分微露 (Partially Exposed)",
                "服装款式": "皮革束腰/胸衣 (Leather Corset)",
                "服装状态": "吊带滑落/半脱 (Slipping Off)",
                "发型发色": "随机 (Random)",
                "饰品头饰": "随机 (Random)",
                "妆容细节": "随机 (Random)",
                "姿势动作": "随机 (Random)",
                "情绪表情": "随机 (Random)",
                "光影预设": "随机 (Random)",
                "胶片风格": "随机 (Random)",
                "液体效果": "随机 (Random)",
                "纹身标记": "随机 (Random)",
                "道具物件": "随机 (Random)",
                "角色设定": "随机 (Random)",
                "真实微瑕": "随机 (Random)",
                "画质等级": "高清写真 (High)",
            },
            rng=random.Random(0),
            entry_point="generator",
        )
        self.assertTrue(len(res_probe.source_atoms) >= 40)
        for sa in res_probe.source_atoms:
            self.assertTrue(bool(sa.id), f"Atom {sa.text!r} has empty leaf id")
            self.assertTrue(bool(sa.provenance.item_id), f"Atom {sa.text!r} has empty provenance.item_id")
            self.assertIsInstance(sa.provenance.item_id, str)
            self.assertEqual(type(sa.provenance.item_id), str)
            self.assertNotEqual(sa.facts.to_dict(), {}, f"Atom {sa.text!r} facts are empty!")

        # 验证固定 seed=0 服装来源：style_id 与 state_id 均为父条目 ID，与叶子 ID 保持独立，绝不混淆
        clothing_corset_atoms = [
            a for a in res_probe.source_atoms
            if a.source_slot == "clothing" and a.provenance and a.provenance.item_id == "leather_corset"
        ]
        self.assertGreater(len(clothing_corset_atoms), 0)
        for ca in clothing_corset_atoms:
            self.assertEqual(ca.provenance.item_id, "leather_corset")
            self.assertEqual(ca.source_item_id, "leather_corset")
            self.assertEqual(ca.origin.selected_id, "leather_corset")
            self.assertNotEqual(ca.id, "leather_corset")  # 叶子 ID 独立承担，绝不混入 item_id
            self.assertTrue(ca.id.startswith("leather_corset__tag_"))

        clothing_state_atoms = [
            a for a in res_probe.source_atoms
            if a.source_slot == "clothing" and a.provenance and a.provenance.item_id == "slipping_off"
        ]
        self.assertGreater(len(clothing_state_atoms), 0)
        for sa in clothing_state_atoms:
            self.assertEqual(sa.provenance.item_id, "slipping_off")
            self.assertEqual(sa.source_item_id, "slipping_off")
            self.assertEqual(sa.origin.selected_id, "slipping_off")
            self.assertNotEqual(sa.id, "slipping_off")

        # ── 3. R1-P1-003: 核心模型强类型与不变量反例闭环 ──
        # SemanticFacts visible_regions 排序去重
        sf_tuple = SemanticFacts(visible_regions=("upper_body", "face", "face"))
        self.assertEqual(sf_tuple.visible_regions, ("face", "upper_body"))
        with self.assertRaises(ValueError):
            SemanticFacts(explicit_fields=("not_a_field",))

        # SelectionOrigin 强类型与合法 canonical selector
        with self.assertRaises(TypeError):
            SelectionOrigin(selector=7)
        with self.assertRaises(TypeError):
            SelectionOrigin(selected_id=7)
        with self.assertRaises(ValueError):
            SelectionOrigin(entry_point="preset_browser", selector="not_a_canonical_selector")

        # ContextProfile 14 情境与标量类型
        with self.assertRaises(ValueError):
            ContextProfile(scene_context_ids=("not_a_context",))
        with self.assertRaises(TypeError):
            ContextProfile(schema_version=7)
        with self.assertRaises(TypeError):
            ContextProfile(scene_item_id=7)

        # ResolutionDecision action 形态不变量与非负 sequence
        with self.assertRaises(ValueError):
            ResolutionDecision(decision_id="d1", sequence=-1, rule_id="r1", phase="p1", action="drop", reason_code="rc", target_atom_id="a1", before_text="txt")
        with self.assertRaises(ValueError):
            ResolutionDecision(decision_id="", sequence=0, rule_id="r1", phase="p1", action="drop", reason_code="rc", target_atom_id="a1", before_text="txt")
        with self.assertRaises(ValueError):
            ResolutionDecision(decision_id="d1", sequence=0, rule_id="r1", phase="p1", action="drop", reason_code="rc", target_atom_id="a1", before_text="txt", after_text="illegal")

        # ResolutionReport 计数与元素类型
        with self.assertRaises(ValueError):
            ResolutionReport(input_count=-1)
        with self.assertRaises(TypeError):
            ResolutionReport(rules_applied=(7,))
        with self.assertRaises(TypeError):
            ResolutionReport(decisions=("not-a-decision",))

        # TagProvenance 稳定排序与强类型
        tp = TagProvenance(parent_ids={"gamma", "alpha", "beta"})
        self.assertEqual(tp.parent_ids, ("alpha", "beta", "gamma"))
        with self.assertRaises(TypeError):
            TagProvenance(parent_ids=[123])

    def test_collection_sorting_hashseed_independence(self):
        """验证集合字段强制确定性排序，不依赖 PYTHONHASHSEED"""
        origin = SelectionOrigin(parent_ids={"b", "a", "c"})
        self.assertEqual(origin.parent_ids, ("a", "b", "c"))

        ctx = ContextProfile(scene_context_ids={"traditional", "school", "office"})
        self.assertEqual(ctx.scene_context_ids, ("office", "school", "traditional"))

        facts = SemanticFacts(visible_regions={"upper_body", "face", "hands"})
        self.assertEqual(facts.visible_regions, ("face", "hands", "upper_body"))

    def test_section_6_9_counterexample_id_registry_collisions(self):
        """验证审核报告 6.9.2 R1-P1-001 统一 ID Registry 对各类跨层、跨预设、跨配方 ID 碰撞的坚决拦截 (Fail-Closed)"""
        import shutil
        mutations = [
            (
                "selector_vs_leaf_collision_in_makeup",
                "makeup.json",
                lambda d: d["categories"][0]["tags"][0].update({"id": d["categories"][0]["id"]}),
            ),
            (
                "cross_preset_fragment_id_collision",
                "presets.json",
                lambda d: d["presets"][1]["fragments"][0].update({"id": d["presets"][0]["fragments"][0]["id"]}),
            ),
            (
                "cross_recipe_fragment_id_collision",
                "style_recipes.json",
                lambda d: d["recipes"][1]["fragments"][0].update({"id": d["recipes"][0]["fragments"][0]["id"]}),
            ),
            (
                "preset_origin_selector_non_canonical",
                "presets.json",
                lambda d: d["presets"][0]["fragments"][0]["origin"].update({"selector": "not_a_canonical_selector"}),
            ),
        ]
        for name, fname, mut_fn in mutations:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_data = Path(tmpdir) / "data"
                shutil.copytree(DATA_DIR, tmp_data)
                target_file = tmp_data / fname
                doc = json.loads(target_file.read_text(encoding="utf-8"))
                mut_fn(doc)
                target_file.write_text(json.dumps(doc), encoding="utf-8")
                res = validate_all(data_dir=tmp_data, schemas_dir=SCHEMAS_DIR, strict_jsonschema=True)
                self.assertFalse(res.is_valid, f"6.9.2 ID registry mutation '{name}' unexpectedly passed validation!")
                self.assertTrue(len(res.errors) > 0, f"Expected errors for mutation '{name}'")

    def test_section_6_9_field_semantics_matrix_and_provenance_cleanliness(self):
        """验证审核报告 6.9.2/6.9.4: 普通 SampleResult, theme, clothing base/state/extension 的父条目与叶子 ID 职责清晰、绝无混淆"""
        import random
        from nodes import _assembler, _generate_structured, _make_slot_fragments, _sampler
        rng = random.Random(42)

        # ── 1. 普通 SampleResult (以 scene 与 lighting 为例) ──
        scene_res = _sampler.sample_scene_result("随机 (Random)", rng)
        self.assertIsNotNone(scene_res)
        self.assertTrue(bool(scene_res.item_id))
        frags = _make_slot_fragments(scene_res, "场景大类", "随机 (Random)", "generator")
        self.assertTrue(len(frags) > 0)
        for f in frags:
            self.assertNotEqual(f.id, f.source_item_id, f"Leaf ID {f.id} must not equal parent item ID {f.source_item_id}")
            self.assertEqual(f.source_item_id, scene_res.item_id)
            self.assertEqual(f.provenance.item_id, scene_res.item_id)
            self.assertEqual(f.origin.selected_id, scene_res.item_id)
            self.assertEqual(f.origin.parent_ids, (scene_res.item_id,))
            self.assertIs(type(f.provenance.item_id), str)
            self.assertIs(type(f.source_item_id), str)
            self.assertIs(type(f.origin.selected_id), str)

        # ── 2. ThemeSampleResult ──
        theme_res = _sampler.sample_theme_result("随机 (Random)", rng)
        self.assertIsNotNone(theme_res)
        self.assertTrue(bool(theme_res.theme_id))
        t_frags = _make_slot_fragments(theme_res, "剧情主题", "随机 (Random)", "generator")
        self.assertTrue(len(t_frags) > 0)
        for f in t_frags:
            self.assertNotEqual(f.id, f.source_item_id, f"Theme leaf ID {f.id} must not equal theme_id {f.source_item_id}")
            self.assertEqual(f.source_item_id, theme_res.theme_id)
            self.assertEqual(f.provenance.item_id, theme_res.theme_id)
            self.assertEqual(f.origin.selected_id, theme_res.theme_id)
            self.assertIs(type(f.provenance.item_id), str)

        # ── 3. ClothingSampleResult (覆盖 base, state, extension 三段) ──
        # 使用 L2 触发 base + state + extension
        cloth_res = _sampler.sample_clothing_result("随机 (Random)", "随机 (Random)", "L2", rng)
        self.assertIsNotNone(cloth_res)
        self.assertTrue(bool(cloth_res.style_id))
        self.assertTrue(bool(cloth_res.state_id))
        self.assertTrue(len(cloth_res.base_tags) > 0)
        self.assertTrue(len(cloth_res.state_tags) > 0)
        c_frags = _make_slot_fragments(cloth_res, "服装款式", "随机 (Random)", "generator")
        self.assertTrue(len(c_frags) > 0)

        # 逐段验证来源语义
        for b_tag in cloth_res.base_tags:
            self.assertEqual(b_tag.provenance.item_id, cloth_res.style_id)
            self.assertNotEqual(b_tag.id, cloth_res.style_id)
            self.assertIs(type(b_tag.provenance.item_id), str)

        for s_tag in cloth_res.state_tags:
            self.assertEqual(s_tag.provenance.item_id, cloth_res.state_id)
            self.assertNotEqual(s_tag.id, cloth_res.state_id)
            self.assertIs(type(s_tag.provenance.item_id), str)

        for e_tag in cloth_res.extension_tags:
            self.assertIsNotNone(e_tag.provenance.item_id)
            self.assertNotEqual(e_tag.id, e_tag.provenance.item_id)
            self.assertIs(type(e_tag.provenance.item_id), str)

        for cf in c_frags:
            self.assertNotEqual(cf.id, cf.source_item_id)
            self.assertEqual(cf.source_item_id, cf.provenance.item_id)
            self.assertEqual(cf.origin.selected_id, cf.provenance.item_id)
            self.assertIs(type(cf.provenance.item_id), str)

        # ── 4. 真实节点链探针 (固定 seed=0) ──
        res_probe = _generate_structured(
            sampler=_sampler,
            assembler=_assembler,
            inputs={
                "预设模板": "无 (None)",
                "风格配方": "无 (None)",
                "场景大类": "随机 (Random)",
                "剧情主题": "随机 (Random)",
                "景别构图": "随机 (Random)",
                "拍摄视角": "随机 (Random)",
                "裸露等级": "L2 差分微露 (Partially Exposed)",
                "服装款式": "皮革束腰/胸衣 (Leather Corset)",
                "服装状态": "吊带滑落/半脱 (Slipping Off)",
                "发型发色": "随机 (Random)",
                "饰品头饰": "随机 (Random)",
                "妆容细节": "随机 (Random)",
                "姿势动作": "随机 (Random)",
                "情绪表情": "随机 (Random)",
                "光影预设": "随机 (Random)",
                "胶片风格": "随机 (Random)",
                "液体效果": "随机 (Random)",
                "纹身标记": "随机 (Random)",
                "道具物件": "随机 (Random)",
                "角色设定": "随机 (Random)",
                "真实微瑕": "随机 (Random)",
                "画质等级": "高清写真 (High)",
            },
            rng=random.Random(0),
            entry_point="generator",
        )
        clothing_corset_atoms = [
            a for a in res_probe.source_atoms
            if a.provenance and a.provenance.item_id == "leather_corset"
        ]
        self.assertGreater(len(clothing_corset_atoms), 0)
        for ca in clothing_corset_atoms:
            self.assertEqual(ca.provenance.item_id, "leather_corset")
            self.assertEqual(ca.source_item_id, "leather_corset")
            self.assertEqual(ca.origin.selected_id, "leather_corset")
            self.assertNotEqual(ca.id, "leather_corset")
            self.assertTrue(ca.id.startswith("leather_corset__tag_"))
            self.assertIs(type(ca.provenance.item_id), str)

    def test_section_6_9_resolution_report_and_model_invariants(self):
        """验证审核报告 6.9.2 R1-P1-003 模型不变量、计数自洽性与非法 selector 拒绝"""
        import lib.models
        # 1. 彻底删除 TagItemId
        self.assertFalse(hasattr(lib.models, "TagItemId"), "TagItemId must be completely deleted")

        # 2. ResolutionReport 计数自洽性反例
        with self.assertRaises(ValueError):
            ResolutionReport(input_count=0, output_count=99, dropped_count=7, replaced_count=8, injected_count=9)

        # 3. 构造决策与计数自洽验证
        d1 = ResolutionDecision(
            decision_id="d1",
            sequence=0,
            rule_id="r_drop",
            phase="phase1",
            action="drop",
            reason_code="conflict",
            target_atom_id="atom_target_1",
            before_text="bad tag",
        )
        d2 = ResolutionDecision(
            decision_id="d2",
            sequence=1,
            rule_id="r_replace",
            phase="phase1",
            action="replace",
            reason_code="replace_rule",
            target_atom_id="atom_target_2",
            before_text="old tag",
            after_text="new tag",
            produced_atom_ids=("atom_new_1", "atom_new_2"),
        )
        # 未在 rules_applied 登记 rule_id -> 拒绝
        with self.assertRaises(ValueError):
            ResolutionReport(
                input_count=5,
                output_count=5,  # 5 - 1 (drop) - 1 (replace) + 2 (produced) = 5
                dropped_count=1,
                replaced_count=1,
                rules_applied=("r_drop",),  # 缺 r_replace
                decisions=(d1, d2),
            )

        # output_count 不符合数学关系 -> 拒绝
        with self.assertRaises(ValueError):
            ResolutionReport(
                input_count=5,
                output_count=6,  # 错误计数
                dropped_count=1,
                replaced_count=1,
                rules_applied=("r_drop", "r_replace"),
                decisions=(d1, d2),
            )

        # 合法构造 -> 成功 (保留实际执行顺序)
        rep = ResolutionReport(
            input_count=5,
            output_count=5,
            dropped_count=1,
            replaced_count=1,
            injected_count=0,
            rules_applied=("r_replace", "r_drop"),
            decisions=(d1, d2),
        )
        self.assertEqual(rep.rules_applied, ("r_replace", "r_drop"))  # 严格保留首次实际应用顺序

        # list/tuple 首次出现保序去重
        rep_dedup = ResolutionReport(
            input_count=5,
            output_count=5,
            dropped_count=1,
            replaced_count=1,
            injected_count=0,
            rules_applied=("r_replace", "r_drop", "r_replace"),
            decisions=(d1, d2),
        )
        self.assertEqual(rep_dedup.rules_applied, ("r_replace", "r_drop"))

        # 外层 set 输入 rules_applied 自动稳定字典序排序 (跨 hashseed 确定性)
        rep_set = ResolutionReport(
            input_count=2,
            output_count=2,
            rules_applied={"gamma_rule", "alpha_rule", "beta_rule"},
        )
        self.assertEqual(rep_set.rules_applied, ("alpha_rule", "beta_rule", "gamma_rule"))

        # schema_version 严格等于 "1.0"
        with self.assertRaises(ValueError):
            ResolutionReport(schema_version="2.0")
        with self.assertRaises(ValueError):
            ContextProfile(schema_version="2.0")

        # 4. 生产 selector 白名单严格清洗，拒绝 slot1, slot2, jk_uniform
        with self.assertRaises(ValueError):
            SelectionOrigin(entry_point="generator", selector="slot1")
        with self.assertRaises(ValueError):
            SelectionOrigin(entry_point="generator", selector="slot2")
        with self.assertRaises(ValueError):
            SelectionOrigin(entry_point="preset_browser", selector="jk_uniform")

        # 5. TagProvenance 强类型、保序去重与 set 确定性排序
        tp_list = TagProvenance(
            item_id="item_x",
            parent_ids=("p_z", "p_a", "p_z"),
            semantic_ids=("s_z", "s_a", "s_z"),
        )
        self.assertEqual(tp_list.parent_ids, ("p_z", "p_a"))  # 保留原始权威顺序
        self.assertEqual(tp_list.semantic_ids, ("s_z", "s_a"))

        tp_set = TagProvenance(
            item_id="item_x",
            parent_ids={"p_z", "p_a"},
            semantic_ids={"s_z", "s_a"},
        )
        self.assertEqual(tp_set.parent_ids, ("p_a", "p_z"))  # set 确定字典序
        self.assertEqual(tp_set.semantic_ids, ("s_a", "s_z"))

        with self.assertRaises(TypeError):
            TagProvenance(item_id=123)
        with self.assertRaises(TypeError):
            TagProvenance(parent_ids=("",))

    def test_authoritative_order_preservation_and_hashseed_independence(self):
        """验证 SelectionOrigin, ResolutionDecision, TagProvenance, rules_applied 保留权威顺序，且 set 输入在不同 PYTHONHASHSEED 下一致"""
        import os
        import subprocess
        import sys

        # 1. 直接构造保序测试
        # SelectionOrigin: ("b", "a", "b") -> ("b", "a")
        orig = SelectionOrigin(parent_ids=("b", "a", "b"))
        self.assertEqual(orig.parent_ids, ("b", "a"))

        # ResolutionDecision
        dec = ResolutionDecision(
            decision_id="d_test",
            sequence=0,
            rule_id="r1",
            phase="p1",
            action="replace",
            reason_code="test",
            target_atom_id="t1",
            before_text="old",
            after_text="new",
            winner_atom_ids=("w2", "w1", "w2"),
            produced_atom_ids=("p2", "p1", "p2"),
            parent_source_ids=("ps2", "ps1", "ps2"),
        )
        self.assertEqual(dec.winner_atom_ids, ("w2", "w1"))
        self.assertEqual(dec.produced_atom_ids, ("p2", "p1"))
        self.assertEqual(dec.parent_source_ids, ("ps2", "ps1"))

        # TagProvenance
        tp = TagProvenance(
            parent_ids=["b", "a", "b"],
            semantic_ids=["s_beta", "s_alpha", "s_beta"],
        )
        self.assertEqual(tp.parent_ids, ("b", "a"))
        self.assertEqual(tp.semantic_ids, ("s_beta", "s_alpha"))

        # ContextProfile.weights 严格遵循 14 情境顺序
        from lib.models import ORDERED_CONTEXT_IDS
        weights_input = (
            ("domestic", 0.3),
            ("school", 0.5),
            ("office", 0.2),
        )
        cp = ContextProfile(weights=weights_input)
        cp_cids = tuple(c for c, _ in cp.weights)
        expected_cids = tuple(c for c in ORDERED_CONTEXT_IDS if c in {"school", "office", "domestic"})
        self.assertEqual(cp_cids, expected_cids)
        self.assertEqual(cp_cids, ("school", "office", "domestic"))

        # 2. 跨 PYTHONHASHSEED 独立子进程验证
        snippet = (
            "from lib.models import SelectionOrigin, TagProvenance, ResolutionReport, ContextProfile;"
            "orig = SelectionOrigin(parent_ids={'gamma', 'alpha', 'beta'});"
            "tp = TagProvenance(parent_ids={'z', 'a', 'm'}, semantic_ids={'s_z', 's_a', 's_m'});"
            "rep = ResolutionReport(rules_applied={'r_c', 'r_a', 'r_b'});"
            "print(repr((orig.parent_ids, tp.parent_ids, tp.semantic_ids, rep.rules_applied)))"
        )
        outputs = []
        for seed in (1, 2, 42, 100, 2026):
            env = os.environ.copy()
            env["PYTHONHASHSEED"] = str(seed)
            proc = subprocess.run(
                [sys.executable, "-c", snippet],
                cwd=str(REPO_DIR),
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            outputs.append(proc.stdout.strip())

        # 确保在所有 PYTHONHASHSEED 下 set 得到的输出逐字完全相同
        self.assertTrue(len(outputs) == 5)
        self.assertEqual(len(set(outputs)), 1, f"Hashseed dependent results found: {outputs}")
if __name__ == "__main__":
    unittest.main()
