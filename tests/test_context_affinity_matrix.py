"""
test_context_affinity_matrix.py — 核心 14 大情境亲和度矩阵与全槽位加权采样交叉复核测试
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from random import Random

from lib.sampler import CONTEXT_AFFINITY, CONTEXT_PARENT_MAPPING, DataSampler

DATA_DIR = Path(__file__).parent.parent / "data"


class TestContextAffinityMatrix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sampler = DataSampler(DATA_DIR)

        # 加载真实数据 ID 集合
        clothing_doc = json.loads((DATA_DIR / "clothing.json").read_text(encoding="utf-8"))
        cls.clothing_ids = {c["id"] for c in clothing_doc["categories"]}

        char_doc = json.loads((DATA_DIR / "characters.json").read_text(encoding="utf-8"))
        cls.char_ids = {c["id"] for c in char_doc["characters"]}

        makeup_doc = json.loads((DATA_DIR / "makeup.json").read_text(encoding="utf-8"))
        cls.makeup_ids = {m["id"] for m in makeup_doc["categories"]}

        acc_doc = json.loads((DATA_DIR / "accessories.json").read_text(encoding="utf-8"))
        cls.hair_ids = {h["id"] for h in acc_doc["hairstyles"]}
        cls.head_ids = {h["id"] for h in acc_doc["headwear_jewelry"]}

        props_doc = json.loads((DATA_DIR / "props.json").read_text(encoding="utf-8"))
        cls.prop_ids = {p["id"] for p in props_doc["categories"]}
        for p in props_doc["categories"]:
            if "items" in p:
                for it in p["items"]:
                    cls.prop_ids.add(it["id"])

        tattoos_doc = json.loads((DATA_DIR / "tattoos.json").read_text(encoding="utf-8"))
        cls.tattoo_ids = {t["id"] for t in tattoos_doc["categories"]}

        nudity_doc = json.loads((DATA_DIR / "nudity_levels.json").read_text(encoding="utf-8"))
        cls.liquid_ids = {liq["id"] for liq in nudity_doc["liquid_effects"]}

    def test_all_14_contexts_exist_in_matrix(self):
        """测试全部 14 个核心情境均在亲和度矩阵中作为一等公民完整存在"""
        expected_contexts = {
            "school",
            "office",
            "medical",
            "onsen_bath",
            "bondage_sm",
            "traditional",
            "nightlife",
            "domestic",
            "transit",
            "outdoor",
            "dining",
            "adult",
            "special",
            "generic",
        }
        self.assertEqual(set(CONTEXT_AFFINITY.keys()), expected_contexts)
        self.assertEqual(set(CONTEXT_PARENT_MAPPING.keys()), expected_contexts)

    def test_all_context_affinity_ids_100_percent_valid(self):
        """逐项断言 14 大情境下引用的所有槽位 ID 100% 存在于真实词库中"""
        for ctx, slots in CONTEXT_AFFINITY.items():
            for slot, ids in slots.items():
                if slot == "clothing":
                    for cid in ids:
                        self.assertIn(cid, self.clothing_ids, f"[{ctx}] clothing id '{cid}' not in clothing.json")
                elif slot == "characters":
                    for cid in ids:
                        self.assertIn(cid, self.char_ids, f"[{ctx}] character id '{cid}' not in characters.json")
                elif slot == "makeup":
                    for mid in ids:
                        self.assertIn(mid, self.makeup_ids, f"[{ctx}] makeup id '{mid}' not in makeup.json")
                elif slot == "hairstyles":
                    for hid in ids:
                        self.assertIn(hid, self.hair_ids, f"[{ctx}] hairstyle id '{hid}' not in accessories.json")
                elif slot == "headwear_jewelry":
                    for jid in ids:
                        self.assertIn(jid, self.head_ids, f"[{ctx}] headwear id '{jid}' not in accessories.json")
                elif slot == "props":
                    for pid in ids:
                        self.assertIn(pid, self.prop_ids, f"[{ctx}] prop id '{pid}' not in props.json")
                elif slot == "tattoos":
                    for tid in ids:
                        self.assertIn(tid, self.tattoo_ids, f"[{ctx}] tattoo id '{tid}' not in tattoos.json")
                elif slot == "liquids":
                    for lid in ids:
                        self.assertIn(lid, self.liquid_ids, f"[{ctx}] liquid id '{lid}' not in nudity_levels.json")

    def test_detect_context_all_14_scenarios(self):
        """测试 detect_context 精准识别全部 14 种情境关键字"""
        cases = [
            ("高中古典教室", "放学后的秘密", "school"),
            ("商务总裁办公室", "深夜加班", "office"),
            ("综合病院VIP病房", "体检检查", "medical"),
            ("露天雪景风吕", "私人温泉", "onsen_bath"),
            ("地下暗黑地牢", "紧缚调教", "bondage_sm"),
            ("和室榻榻米茶室", "传统和服祭典", "traditional"),
            ("满载通勤电车车厢", "早高峰拥挤", "transit"),
            ("夏日海滩沙滩漫步", "露天户外野外", "outdoor"),
            ("复古女仆咖啡厅", "下午茶甜品时光", "dining"),
            ("歌舞伎町地下酒吧", "微醺夜店派对", "nightlife"),
            ("温馨同居公寓卧室", "居家厨房人妻", "domestic"),
            ("成人私密写真影棚", "泡泡浴风俗", "adult"),
            ("废墟地下实验室", "特异密室", "special"),
            ("日常随拍", "随性写真", "generic"),
        ]
        for scene, theme, expected_ctx in cases:
            detected = self.sampler.detect_context(scene, theme)
            self.assertEqual(detected, expected_ctx, f"Failed for '{scene}' + '{theme}', got '{detected}' expected '{expected_ctx}'")

    def test_weighted_sampling_affinity_adherence(self):
        """测试在不同情境下随机采样服装时，亲和候选集被高度优先命中"""
        rng = Random(42)

        # 1. 校园情境：随机采样服装应高频命中 JK/西装制服/体操服
        school_clothings = [
            self.sampler.sample_clothing_with_nudity_linkage("随机 (Random)", "正常穿着 (Normal)", "L1", Random(i), context="school")[0]
            for i in range(50)
        ]
        school_keywords = ["seifuku", "uniform", "blazer", "school", "gym"]
        school_match = sum(1 for c in school_clothings if any(k in c.lower() for k in school_keywords))
        self.assertGreaterEqual(school_match, 35, f"School context clothing match {school_match}/50 too low")

        # 2. 温泉情境：随机采样服装应高频命中 浴衣/和服/泳装
        onsen_clothings = [
            self.sampler.sample_clothing_with_nudity_linkage("随机 (Random)", "正常穿着 (Normal)", "L1", Random(i), context="onsen_bath")[0]
            for i in range(50)
        ]
        onsen_keywords = ["yukata", "kimono", "swimsuit", "bikini"]
        onsen_match = sum(1 for c in onsen_clothings if any(k in c.lower() for k in onsen_keywords))
        self.assertGreaterEqual(onsen_match, 35, f"Onsen context clothing match {onsen_match}/50 too low")

        # 3. 户外情境：随机采样服装应高频命中 泳衣/比基尼/便服/体操服
        outdoor_clothings = [
            self.sampler.sample_clothing_with_nudity_linkage("随机 (Random)", "正常穿着 (Normal)", "L1", Random(i), context="outdoor")[0]
            for i in range(50)
        ]
        outdoor_keywords = ["bikini", "swimsuit", "swimwear", "rashguard", "beachwear", "casual", "gym", "cheerleader"]
        outdoor_match = sum(1 for c in outdoor_clothings if any(k in c.lower() for k in outdoor_keywords))
        self.assertGreaterEqual(outdoor_match, 35, f"Outdoor context clothing match {outdoor_match}/50 too low")


if __name__ == "__main__":
    unittest.main()
