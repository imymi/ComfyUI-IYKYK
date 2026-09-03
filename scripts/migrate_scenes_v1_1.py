#!/usr/bin/env python3
"""
migrate_scenes_v1_1.py — 重新迁移 scenes.json 数据与上下文期望表

规则：
1. 以可信原始数据基线（/tmp/scenes_baseline.json 或 git 09d9942:data/scenes.json）为唯一输入
2. 单次分支安全展平标签：if ',' in t: ... elif '/' in t: ... else: ...
3. 保序严格去重：
   - anchor_tags 无重复
   - detail_tags 无重复
   - anchor_tags 与 detail_tags 零交集
   - tags 兼容字段无重复
4. 语义化拆分 anchor（地点等价词）与 detail（共存环境细节）
5. 生成/核对 data/scene_context_expectations.json 显式映射
6. 默认 dry-run 仅输出报告，传入 --write 参数才覆盖 data/scenes.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, Set, Tuple

REPO_DIR = Path(__file__).parent.parent
DATA_DIR = REPO_DIR / "data"
BASELINE_PATH = Path("/tmp/scenes_baseline.json")


def load_baseline_data() -> dict:
    if not BASELINE_PATH.is_file():
        raise FileNotFoundError(f"Baseline file {BASELINE_PATH} not found.")
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


# 显式分类上下文映射规则
CATEGORY_DEFAULT_CONTEXT = {
    "室内私密场景": "domestic",
    "洗浴/温泉场景（日本AV核心！）": "onsen_bath",
    "学校场景（JAV核心主题）": "school",
    "职场空间（OL/职女系列）": "office",
    "公共交通/交通工具场景": "transit",
    "商业/服务场所": "generic",
    "AV拍摄/制作场景（日本特有！）": "adult",
    "日式风俗/夜生活": "nightlife",
    "和风传统空间": "traditional",
    "医疗/护理场景": "medical",
    "居住/社区场景": "domestic",
    "户外/露出场景": "outdoor",
    "监禁/密室场景": "bondage_sm",
    "公共场所/街头系列": "generic",
    "都市建筑/写字楼系列": "office",
    "娱乐场所系列": "nightlife",
    "餐饮/包厢系列": "dining",
    "交通枢纽/旅途系列": "transit",
    "学校/教育设施系列": "school",
    "成人场所/特殊行业系列": "adult",
    "特殊空间/不可能空间": "generic",
    "其他日本AV常见场景": "generic",
    "魔术镜号/特殊车辆（JAV独有）": "adult",
    "影像编辑室/导演审片室（AV制作场景）": "adult",
}

# 个别子项精确覆盖
ITEM_CONTEXT_OVERRIDES = {
    "家庭浴室": ("onsen_bath", "bathroom"),
    "温泉旅馆": ("onsen_bath", "onsen_indoor"),
    "露天风吕": ("onsen_bath", "onsen_outdoor"),
    "更衣室": ("onsen_bath", "onsen_changing"),
    "海边/泳池": ("outdoor", "beach"),
    "便利店/超市": ("generic", "convenience_store"),
    "餐饮娱乐": ("dining", "dining_room"),
    "居酒屋/包厢": ("dining", "izakaya"),
    "餐厅包间": ("dining", "restaurant"),
    "咖啡厅/包厢": ("dining", "cafe"),
    "拉面店/吧台": ("dining", "ramen_shop"),
    "路边摊/大排档": ("dining", "food_stall"),
    "酒吧/柜台": ("nightlife", "bar"),
    "情侣餐厅/包厢": ("dining", "love_hotel_dining"),
    "和室": ("traditional", "washitsu"),
    "寺庙/神社": ("traditional", "shrine"),
    "茶室/庭院": ("traditional", "tea_room"),
    "老宅/走廊": ("traditional", "traditional_house"),
    "漫画网吧/漫画喫茶": ("nightlife", "manga_cafe"),
    "卡拉OK/包厢": ("nightlife", "karaoke"),
    "游戏中心/ arcade": ("nightlife", "arcade"),
    "保龄球/台球厅": ("nightlife", "billiards"),
    "电影院/后排": ("nightlife", "cinema"),
    "按摩店/足疗": ("nightlife", "massage"),
    "健身房/淋浴间": ("generic", "gym"),
    "游泳池/更衣室": ("onsen_bath", "pool_changing"),
    "公共厕所": ("generic", "public_restroom"),
    "电梯": ("generic", "elevator"),
    "楼梯间/安全通道": ("generic", "stairwell"),
    "天台/屋顶": ("outdoor", "rooftop"),
    "地下车库/停车场": ("generic", "parking_garage"),
    "自动贩卖机角落": ("outdoor", "street_corner"),
    "公交站/雨棚": ("transit", "bus_stop"),
    "小巷/后街": ("outdoor", "alley"),
    "街头/斑马线": ("outdoor", "street"),
    "公园长椅/草丛": ("outdoor", "park_bushes"),
    "河堤/桥下": ("outdoor", "riverbank"),
}

LABEL_DISAMBIGUATION = {
    ("学校场景（JAV核心主题）", "特殊场所"): "学校保健室",
    ("日式风俗/夜生活", "特殊场所"): "风俗窥视室",
    ("公共场所/街头系列", "天台/屋顶"): "天台/大厦楼顶",
    ("学校/教育设施系列", "天台/屋顶"): "学校天台",
}


def clean_and_flatten_tags(raw_tags: list) -> list[str]:
    flat: list[str] = []
    for item in raw_tags:
        if not item or not isinstance(item, str):
            continue
        text = item.strip()
        if not text or text == "内容" or text == "章节":
            continue

        if "," in text:
            parts = text.split(",")
        elif "/" in text:
            parts = text.split("/")
        else:
            parts = [text]

        for p in parts:
            clean_p = p.strip()
            if clean_p and clean_p != "内容" and clean_p != "章节":
                flat.append(clean_p)

    # 保序严格去重
    return list(dict.fromkeys(flat))


def split_anchor_and_details(sub_name: str, tags: list[str]) -> Tuple[list[str], list[str]]:
    """
    语义化拆分：anchor 为等价场所名称，detail 为可共存的环境细节。
    严格保证 anchor 与 detail 零交集。
    """
    if len(tags) <= 1:
        return tags, []



    anchors = []
    details = []

    for t in tags:
        t_lower = t.lower()
        # 如果是首个 tag 或包含明显独立地点词且不含纯细节词
        if not anchors:
            anchors.append(t)
        elif len(anchors) < 3 and not any(k in t_lower for k in ["lighting", "light", "messy", "sheets", "pillow", "aftermath", "view", "mist", "steam", "chains"]):
            # 候选可替代 anchor
            anchors.append(t)
        else:
            details.append(t)

    # 确保 detail 中绝不包含 anchor 中的词
    anchors = list(dict.fromkeys(anchors))
    details = [d for d in dict.fromkeys(details) if d not in anchors]

    return anchors, details


def generate_migration():
    baseline = load_baseline_data()
    categories = baseline.get("scenes", [])

    structured_categories = []
    expectations: Dict[str, dict] = {}
    seen_ids: Set[str] = set()
    slug_counts: Dict[str, int] = {}

    total_items = 0
    total_tags_count = 0

    for cat_idx, cat in enumerate(categories):
        cat_name = cat.get("category", "")
        default_ctx = CATEGORY_DEFAULT_CONTEXT.get(cat_name, "generic")
        new_items = []

        for item in cat.get("items", []):
            total_items += 1
            raw_sub_name = item.get("subcategory", "")
            sub_name = LABEL_DISAMBIGUATION.get((cat_name, raw_sub_name), raw_sub_name)
            raw_tags = item.get("tags", [])
            atmosphere = item.get("atmosphere", "")

            cleaned_tags = clean_and_flatten_tags(raw_tags)
            anchors, details = split_anchor_and_details(sub_name, cleaned_tags)

            # 确定 context 与 exclusive_group
            override = ITEM_CONTEXT_OVERRIDES.get(sub_name)
            if override:
                ctx, ex_grp = override
            else:
                ctx = default_ctx
                ex_grp = ctx

            # 生成稳定唯一 ID
            primary_anchor = anchors[0] if anchors else "room"
            slug = re.sub(r"[^a-z0-9_]", "_", primary_anchor.lower().replace(" ", "_")).strip("_")
            slug = f"scene_{slug}"
            slug_counts[slug] = slug_counts.get(slug, 0) + 1
            sid = f"{slug}_{slug_counts[slug]}" if slug_counts[slug] > 1 else slug
            seen_ids.add(sid)

            # 严格断言
            assert len(anchors) == len(set(anchors)), f"Duplicate in anchors for {sid}"
            assert len(details) == len(set(details)), f"Duplicate in details for {sid}"
            assert set(anchors).isdisjoint(set(details)), f"Intersection between anchors & details in {sid}"

            entry = {
                "id": sid,
                "label": sub_name,
                "subcategory": sub_name,
                "context_ids": [ctx],
                "anchor_tags": anchors,
                "detail_tags": details,
                "exclusive_group": ex_grp,
                "tags": anchors + details,
                "atmosphere": atmosphere,
            }
            new_items.append(entry)
            total_tags_count += len(anchors) + len(details)

            expectations[sid] = {
                "label": sub_name,
                "category": cat_name,
                "expected_context": ctx,
                "exclusive_group": ex_grp,
                "anchor_count": len(anchors),
                "detail_count": len(details),
            }

        structured_categories.append({
            "category": cat_name,
            "items": new_items,
        })

    migrated_scenes = {"scenes": structured_categories}
    return migrated_scenes, expectations, total_items, total_tags_count


def main():
    parser = argparse.ArgumentParser(description="Migrate scenes.json data cleanly.")
    parser.add_argument("--write", action="store_true", help="Actually overwrite data/scenes.json and expectations")
    args = parser.parse_args()

    migrated_scenes, expectations, total_items, total_tags = generate_migration()

    print("Migration Summary:")
    print(f"  Categories: {len(migrated_scenes['scenes'])}")
    print(f"  Items:      {total_items}")
    print(f"  Total tags: {total_tags}")
    print("  Anchor/Detail overlap: ZERO (Verified)")
    print("  Duplicate tags: ZERO (Verified)")

    if args.write:
        scenes_file = DATA_DIR / "scenes.json"
        scenes_file.write_text(json.dumps(migrated_scenes, ensure_ascii=False, indent=2), encoding="utf-8")

        exp_file = DATA_DIR / "scene_context_expectations.json"
        exp_file.write_text(json.dumps(expectations, ensure_ascii=False, indent=2), encoding="utf-8")

        print("\n✅ Successfully wrote migrated data to:")
        print(f"   - {scenes_file}")
        print(f"   - {exp_file}")
    else:
        print("\n[Dry Run] Use --write to apply changes.")


if __name__ == "__main__":
    main()
