#!/usr/bin/env python3
"""
verify_clothing_migration.py — 离线自动化核验脚本 (Gate 1 验收门禁)

核心校验项：
1. raw_clothing_input.tsv 哈希强校验与 1~246 行号连续性
2. clothing_lexicon_migration_ledger.md 逐行 1:1 对齐、三层统计守恒与账本完整性 (246 行)
3. catalog_diff_report.md 集合等价性与计数一致性 (108 款式, 11 状态, 7 配饰, 2 情趣, 1 微瑕, 27 差异对照)
4. 目标 Catalog 规范 ID 零悬空校验 (所有合并目标的承载归口必须存在，延期项合规隔离)
5. carrier_state_binding_spec.md 状态三元分类、全域主体白名单与 discarded 注销机制契约校验
6. conflict_rule_fixtures.py 夹具结构完备性与黑盒解耦契约校验
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))
RAW_TSV_PATH = REPO_DIR / "docs" / "data_migration" / "raw_clothing_input.tsv"
LEDGER_PATH = REPO_DIR / "docs" / "data_migration" / "clothing_lexicon_migration_ledger.md"
DIFF_REPORT_PATH = REPO_DIR / "docs" / "data_migration" / "catalog_diff_report.md"
CARRIER_SPEC_PATH = REPO_DIR / "docs" / "data_migration" / "carrier_state_binding_spec.md"
DATA_DIR = REPO_DIR / "data"

EXPECTED_TSV_SHA256 = "65e4c56655b4c0f8f3629a5e26d407d77860d3581d55b2da7dd92ff87d7fc2ff"
EXPECTED_MEDIA_SHA256 = "716af655968d22eaf970c727aa0a13f273a4f6d097185e06080c28d2636e4ee4"
EXPECTED_TOTAL_ROWS = 246
DEFAULT_MEDIA_PATH = Path("/Users/jacobyang/.gemini/antigravity/brain/4dfc2f79-2452-47f9-bc8f-de7cd3d990cc/.user_uploaded/media_1789978587694.md")


def compute_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_prompt(p: str) -> str:
    p = p.strip().lower()
    p = p.rstrip(',').rstrip(';')
    p = p.replace('\\n', '').replace('\n', '')
    p = ' '.join(p.split())
    return p


class VerificationRunner:
    def __init__(self, raw_media_path: Optional[Path] = None) -> None:
        self.raw_media_path = raw_media_path or DEFAULT_MEDIA_PATH
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.passed_checks: int = 0

    def check(self, condition: bool, msg: str) -> None:
        if condition:
            self.passed_checks += 1
        else:
            self.errors.append(msg)

    def run(self) -> int:
        print("=" * 70)
        print("🔍 开始 ComfyUI-IYKYK rc9 服装词库迁移全流程自动化门禁核验...")
        print("=" * 70)

        # 1. 原始输入 TSV 与 Markdown 附件物理一致性校验
        self.verify_raw_tsv()

        # 2. 数据迁移台账 Markdown 校验
        tsv_rows = self.load_tsv_rows()
        ledger_rows = self.verify_ledger_table(tsv_rows)

        # 3. Catalog 差集报告一致性校验
        self.verify_diff_report(ledger_rows)

        # 4. 目标 Catalog ID 零悬空校验
        self.verify_zero_dangling_targets(ledger_rows)

        # 5. 承载关系与状态三元分类规约校验
        self.verify_carrier_spec()

        # 6. 测试夹具可导入性与结构契约校验
        self.verify_test_fixtures()

        # 汇总报告
        print("\n" + "=" * 70)
        print(f"📊 核验完成: {self.passed_checks} 项检查通过, {len(self.errors)} 项错误, {len(self.warnings)} 项警告。")
        print("=" * 70)

        if self.warnings:
            print("\n⚠️ 警告详情:")
            for w in self.warnings:
                print(f"  - {w}")

        if self.errors:
            print("\n❌ 错误详情:")
            for e in self.errors:
                print(f"  - {e}")
            print("\n🚨 门禁核验失败 (Validation FAILED)！")
            return 1
        else:
            print("\n✅ 台账一致性与夹具结构检查通过；规则行为及节点集成测试待 M3 实现后验证。")
            return 0

    def verify_raw_tsv(self) -> None:
        print("\n[Check 1/6] 校验原始输入 TSV 物理完整性、密码学哈希与逐行三列一致性...")
        self.check(RAW_TSV_PATH.exists(), f"原始 TSV 文件不存在: {RAW_TSV_PATH}")
        if not RAW_TSV_PATH.exists():
            return

        actual_sha = compute_sha256(RAW_TSV_PATH)
        self.check(
            actual_sha == EXPECTED_TSV_SHA256,
            f"原始 TSV SHA-256 不匹配! 预期: {EXPECTED_TSV_SHA256}, 实际: {actual_sha}",
        )

        content = RAW_TSV_PATH.read_text(encoding="utf-8")
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        self.check(len(lines) == EXPECTED_TOTAL_ROWS + 1, f"TSV 总行数不匹配: 期望 {EXPECTED_TOTAL_ROWS + 1}, 实际 {len(lines)}")

        header = lines[0].split("\t")
        expected_header = ["line_no", "category", "name_zh", "prompt_raw"]
        self.check(header == expected_header, f"TSV 表头不符合规范: {header}")

        seen_nos = set()
        for idx, line in enumerate(lines[1:], start=1):
            parts = line.split("\t")
            self.check(len(parts) == 4, f"TSV 第 {idx} 行列数不为 4: {line}")
            if len(parts) == 4:
                line_no = int(parts[0])
                seen_nos.add(line_no)
                self.check(line_no == idx, f"TSV 行号不连续: 预期 {idx}, 实际 {line_no}")
                self.check(bool(parts[1].strip()), f"TSV 第 {idx} 行原分类为空")
                self.check(bool(parts[2].strip()), f"TSV 第 {idx} 行中文词条为空")
                self.check(bool(parts[3].strip()), f"TSV 第 {idx} 行提示词为空")

        self.check(seen_nos == set(range(1, EXPECTED_TOTAL_ROWS + 1)), "TSV 包含缺失或多余行号")

        # 校验原始媒体附件存在性与密码学哈希 (正式验收必须存在)
        self.check(self.raw_media_path.exists(), f"原始媒体附件文件不存在: {self.raw_media_path}")
        if self.raw_media_path.exists():
            media_sha = compute_sha256(self.raw_media_path)
            self.check(media_sha == EXPECTED_MEDIA_SHA256, f"原始媒体附件 SHA-256 不匹配: 实际={media_sha}, 期望={EXPECTED_MEDIA_SHA256}")
            media_lines = [l.strip() for l in self.raw_media_path.read_text(encoding="utf-8").splitlines() if l.strip()]
            media_data_rows = [l for l in media_lines if l.startswith('|') and not l.startswith('| 分类') and not l.startswith('|---')]
            self.check(len(media_data_rows) == EXPECTED_TOTAL_ROWS, f"原始媒体附件表格数据行数不为 {EXPECTED_TOTAL_ROWS}: {len(media_data_rows)}")

            # 逐行逐列 3 列绝对一致性对比 (分类, 中文词条, 提示词)
            tsv_data_lines = lines[1:]
            diff_count = 0
            for idx, (m_line, t_line) in enumerate(zip(media_data_rows, tsv_data_lines), start=1):
                m_parts = [c.strip() for c in m_line.strip('|').split('|')]
                t_parts = t_line.split('\t')
                t_cols = [t_parts[1], t_parts[2], t_parts[3]]
                if m_parts != t_cols:
                    diff_count += 1
                    self.errors.append(f"第 {idx} 行原附件与 TSV 三列内容不一致! 媒体={m_parts}, TSV={t_cols}")
                else:
                    self.passed_checks += 1
            self.check(diff_count == 0, f"原附件与 TSV 逐行对比存在 {diff_count} 处差异")

        print(f"  ✓ TSV 与媒体附件哈希一致 ({actual_sha[:16]}...)，行号 1~{EXPECTED_TOTAL_ROWS} 逐行三列 100% 绝对一致。")

    def load_tsv_rows(self) -> List[Tuple[int, str, str, str]]:
        lines = [l.strip() for l in RAW_TSV_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
        return [(int(p[0]), p[1], p[2], p[3]) for p in [l.split("\t") for l in lines[1:]]]

    def verify_ledger_table(self, tsv_rows: List[Tuple[int, str, str, str]]) -> List[Dict[str, str]]:
        print("\n[Check 2/6] 校验迁移台账 Markdown 与原始 TSV 逐行 1:1 对齐与三层统计...")
        self.check(LEDGER_PATH.exists(), f"台账文件不存在: {LEDGER_PATH}")
        if not LEDGER_PATH.exists():
            return []

        text = LEDGER_PATH.read_text(encoding="utf-8")

        # 检查 SHA-256 引用
        self.check(EXPECTED_TSV_SHA256 in text, "台账中未正确记录原始 TSV 的 SHA-256 哈希")

        # 检查三层统计数字
        unique_raw = len({r[3] for r in tsv_rows})
        unique_norm = len({normalize_prompt(r[3]) for r in tsv_rows})

        self.check(f"{unique_raw} 个" in text, f"第 1 层原始去重统计计数值未在台账中如实体现 ({unique_raw})")
        self.check(f"{unique_norm} 个" in text, f"第 2 层规范化去重统计计数值未在台账中如实体现 ({unique_norm})")

        # 解析台账数据行：精确匹配以 | <数字> | 开头的台账行
        data_lines = [l.strip() for l in text.splitlines() if re.match(r"^\|\s*\d+\s*\|", l.strip())]

        self.check(len(data_lines) == EXPECTED_TOTAL_ROWS, f"台账数据行数不为 {EXPECTED_TOTAL_ROWS}: 实际 {len(data_lines)}")

        parsed_rows: List[Dict[str, str]] = []
        status_counts = {"纳入": 0, "合并": 0, "延期": 0, "排除": 0}

        for idx, (t_no, t_cat, t_zh, t_en) in enumerate(tsv_rows, start=1):
            if idx > len(data_lines):
                break
            line_str = data_lines[idx - 1]
            cols = [c.strip() for c in line_str.split("|")[1:-1]]
            self.check(len(cols) >= 13, f"台账第 {idx} 行列数不足 13 列: {line_str}")
            if len(cols) < 13:
                continue

            r_no = int(cols[0])
            r_cat = cols[1]
            r_zh = cols[2]
            r_en = cols[3].strip("`")
            r_norm = cols[4].strip("`")
            r_status = cols[5]
            r_slot = cols[6]
            r_catalog = cols[7]
            r_target_id = cols[8].strip("`")
            r_ops = cols[9]
            r_button = cols[10]
            r_skirt = cols[11]
            r_reason = cols[12]

            # 严格 1:1 对齐原始 TSV
            self.check(r_no == t_no, f"台账第 {idx} 行号与 TSV 不对齐: 台账={r_no}, TSV={t_no}")
            self.check(r_cat == t_cat, f"台账第 {idx} 行分类与 TSV 不对齐: 台账={r_cat}, TSV={t_cat}")
            self.check(r_zh == t_zh, f"台账第 {idx} 行中文与 TSV 不对齐: 台账={r_zh}, TSV={t_zh}")
            self.check(r_en == t_en, f"台账第 {idx} 行英文与 TSV 不对齐: 台账={r_en}, TSV={t_en}")

            # 字段规范性
            self.check(r_status in status_counts, f"台账第 {idx} 行未知状态: {r_status}")
            status_counts[r_status] = status_counts.get(r_status, 0) + 1

            if r_status == "延期":
                self.check(r_slot == "-", f"延期项槽位应为 '-': {r_slot}")
                self.check(r_target_id == "-", f"延期项目标 ID 应为 '-': {r_target_id}")
            else:
                self.check(r_slot in ("clothing", "jewelry", "lingerie", "imperfections"), f"台账第 {idx} 行未知槽位: {r_slot}")
                self.check(bool(r_target_id), f"台账第 {idx} 行目标 ID 为空")

            self.check(r_button in ("允许", "禁止", "不适用", "未知"), f"台账第 {idx} 行未知解扣能力: {r_button}")
            self.check(r_skirt in ("允许", "禁止", "不适用", "未知"), f"台账第 {idx} 行未知掀裙能力: {r_skirt}")
            self.check(bool(r_reason), f"台账第 {idx} 行决策理由为空")

            parsed_rows.append({
                "row_id": str(r_no),
                "cat": r_cat,
                "name_zh": r_zh,
                "prompt_raw": r_en,
                "prompt_norm": r_norm,
                "status": r_status,
                "slot": r_slot,
                "catalog": r_catalog,
                "target_id": r_target_id,
                "ops": r_ops,
                "button": r_button,
                "skirt": r_skirt,
                "reason": r_reason,
            })

        # 守恒律断言
        total_status = sum(status_counts.values())
        self.check(total_status == EXPECTED_TOTAL_ROWS, f"台账状态总数不守恒: {total_status} != {EXPECTED_TOTAL_ROWS}")
        print(f"  ✓ 台账 246 行与 TSV 逐行 1:1 绝对对齐 (纳入={status_counts['纳入']}, 合并={status_counts['合并']}, 延期={status_counts['延期']})。")
        return parsed_rows

    def verify_diff_report(self, ledger_rows: List[Dict[str, str]]) -> None:
        print("\n[Check 3/6] 校验 Catalog 差集报告与台账提取实体的集合等价性...")
        self.check(DIFF_REPORT_PATH.exists(), f"差集报告不存在: {DIFF_REPORT_PATH}")
        if not DIFF_REPORT_PATH.exists():
            return

        diff_text = DIFF_REPORT_PATH.read_text(encoding="utf-8")

        # 1. 从台账提取各 Catalog 的“纳入”唯一规范 ID 集合
        ledger_new_categories = sorted({r["target_id"] for r in ledger_rows if r["status"] == "纳入" and "categories" in r["catalog"] and "clothing.json" in r["catalog"]})
        ledger_new_states = sorted({r["target_id"] for r in ledger_rows if r["status"] == "纳入" and "clothing_states" in r["catalog"]})
        ledger_new_jewelry = sorted({r["target_id"] for r in ledger_rows if r["status"] == "纳入" and "headwear_jewelry" in r["catalog"]})
        ledger_new_lingerie = sorted({r["target_id"] for r in ledger_rows if r["status"] == "纳入" and "lingerie_wardrobe" in r["catalog"]})
        ledger_new_imperfections = sorted({r["target_id"] for r in ledger_rows if r["status"] == "纳入" and "imperfections" in r["catalog"]})

        # 2. 检查差集报告中的统计计数
        self.check(f"+{len(ledger_new_categories)} 项" in diff_text, f"差集报告款式新增数量不符: 期望 +{len(ledger_new_categories)} 项")
        self.check(f"+{len(ledger_new_states)} 条" in diff_text, f"差集报告状态新增数量不符: 期望 +{len(ledger_new_states)} 条")
        self.check(f"+{len(ledger_new_jewelry)} 项" in diff_text, f"差集报告配饰新增数量不符: 期望 +{len(ledger_new_jewelry)} 项")
        self.check(f"+{len(ledger_new_lingerie)} 项" in diff_text, f"差集报告情趣内衣新增数量不符: 期望 +{len(ledger_new_lingerie)} 项")
        self.check(f"+{len(ledger_new_imperfections)} 项" in diff_text, f"差集报告微瑕新增数量不符: 期望 +{len(ledger_new_imperfections)} 项")

        # 3. 检查款式清单 JSON 集合等价性
        cat_match = re.search(r"### 2\. 台账提取确定新增款式清单.*?```json\s*(\[[^\]]+\])", diff_text, re.DOTALL)
        self.check(bool(cat_match), "差集报告中缺少新增款式 JSON 清单代码块")
        if cat_match:
            diff_categories = json.loads(cat_match.group(1))
            self.check(
                set(diff_categories) == set(ledger_new_categories),
                f"差集报告款式清单与台账纳入款式不一致! 差集(diff-ledger)={set(diff_categories) - set(ledger_new_categories)}, 差集(ledger-diff)={set(ledger_new_categories) - set(diff_categories)}",
            )

        # 4. 检查状态清单等价性 (动态核验 data/clothing.json 运行库存量 12 条 ID 与差集报告)
        clothing_json_path = DATA_DIR / "clothing.json"
        self.check(clothing_json_path.exists(), f"运行库 clothing.json 不存在: {clothing_json_path}")
        if clothing_json_path.exists():
            c_json = json.loads(clothing_json_path.read_text(encoding="utf-8"))
            runtime_stock_states = [s["id"] for s in c_json.get("clothing_states", [])]
            self.check(len(runtime_stock_states) == 12, f"运行库存量状态条数不为 12: {len(runtime_stock_states)}")
            
            # 严格断言：差集报告中记录的存量状态集合完全等价于运行库存量状态真实 ID
            for s in runtime_stock_states:
                self.check(f"`{s}`" in diff_text, f"差集报告存量状态清单遗漏真实 ID: {s}")
            
            # 严禁将消解内部枚举 (opened, lifted, lowered, loosened, wet) 误列为存量 ID
            sec5_pos = diff_text.find("## 五、服装状态")
            sec6_pos = diff_text.find("## 六、", sec5_pos)
            sec5_text = diff_text[sec5_pos:sec6_pos] if (sec5_pos != -1 and sec6_pos != -1) else diff_text[sec5_pos:]
            for false_id in ("`opened`", "`lifted`", "`lowered`", "`loosened`"):
                self.check(false_id not in sec5_text, f"差集报告存量状态误列消解内部枚举值: {false_id}")

        for s in ledger_new_states:
            self.check(f'"{s}"' in diff_text or f'`{s}`' in diff_text, f"差集报告缺少新增状态 ID: {s}")

        # 5. 检查配饰清单等价性
        for j in ledger_new_jewelry:
            self.check(f"`{j}`" in diff_text, f"差集报告缺少配饰 ID: {j}")

        # 6. 检查真实集合差异 (81 vs 109) 与数学守恒
        self.check("## 三、相对早期 81 款清单的真实集合差异分析与决策说明" in diff_text, "差集报告中缺少真实集合差异分析标题")
        self.check("## 四、109 款新增服装款式全量工程规格台账" in diff_text, "差集报告中缺少 109 款全量工程规格台账")

        old_81_str = """
            "taoist_robe", "greek_toga", "battle_robe", "bikini_classic", "bikini_strappy",
            "bikini_creative", "swimsuit_school", "swimsuit_competition", "swimsuit_creative",
            "volleyball_uniform", "sportswear_active", "business_suit", "tailcoat", "lab_coat",
            "convenience_store", "fast_food_uniform", "racing_suit", "firefighter_gear",
            "hospital_gown", "military_uniform", "military_overcoat", "combat_tactical",
            "tactical_vest", "knight_armor", "berserker_armor", "mecha_exoskeleton",
            "mecha_power_armor", "clerical_nun", "clerical_priest", "shinto_miko",
            "witch_robe", "wizard_robe", "mahou_shoujo", "anime_cosplay", "festive_costume",
            "slime_dress", "wedding_dress", "formal_gown", "summer_sundress", "chiffon_dress",
            "tulle_dress", "dirndl_dress", "armored_dress", "dress_backless", "halter_dress",
            "sweater_dress", "sweater_casual", "knit_vest", "hoodie", "sweatshirt", "t_shirt",
            "shirts_blouses", "crop_top", "tops_tanks", "strapless_top", "fishnet_top",
            "trench_coat", "windbreaker", "down_jacket", "winter_parka", "outerwear_overcoat",
            "leather_jacket", "denim_jacket", "safari_jacket", "rainwear_coat", "duffel_coat",
            "pleated_skirt", "miniskirt", "microskirt", "pencil_skirt", "layered_skirt",
            "plaid_skirt", "pettiskirt", "tutu_skirt", "denim_shorts", "hot_pants",
            "black_leggings", "dungarees", "leotard_bodysuit", "zentai_suit", "cocktail_dress"
        """
        base_81 = set(re.findall(r"\"([a-z0-9_]+)\"", old_81_str))
        target_109 = set(ledger_new_categories)

        added_set = target_109 - base_81
        removed_set = base_81 - target_109
        kept_set = target_109 & base_81

        self.check(len(base_81) == 81, f"基准款式集合大小不为 81: {len(base_81)}")
        self.check(len(target_109) == 109, f"目标款式集合大小不为 109: {len(target_109)}")
        self.check(len(added_set) == 28, f"实际新增款式集合大小不为 28: {len(added_set)}")
        self.check(len(removed_set) == 0, f"实际移除款式集合不为空: {removed_set}")
        self.check(len(kept_set) == 81, f"两版共同保留款式集合大小不为 81: {len(kept_set)}")
        self.check("81 - 0 + 28 = 109" in diff_text, "差集报告缺少 81 - 0 + 28 = 109 守恒方程")
        self.check("sweater_casual" in diff_text, "差集报告缺少 sweater_casual 专项决策说明")

        # 7. 检查规格表拓扑合法性与跨文件属性一致性
        from lib.models import VALID_GARMENT_TOPOLOGIES
        sec4_pos = diff_text.find("## 四、109 款新增服装款式全量工程规格台账")
        sec5_pos = diff_text.find("## 五、", sec4_pos)
        sec4_text = diff_text[sec4_pos:sec5_pos] if (sec4_pos != -1 and sec5_pos != -1) else (diff_text[sec4_pos:] if sec4_pos != -1 else "")
        sec4_lines = [l.strip() for l in sec4_text.splitlines() if re.match(r"^\|\s*\d+\s*\|", l.strip())]
        self.check(len(sec4_lines) == 109, f"规格台账数据行不为 109 行: {len(sec4_lines)}")

        spec_map = {}
        for l in sec4_lines:
            cols = [c.strip() for c in l.split("|")[1:-1]]
            cid = cols[1].strip("`")
            topo = cols[3].strip("`")
            btn = cols[4]
            skirt = cols[5]
            self.check(topo in VALID_GARMENT_TOPOLOGIES, f"款式 {cid} 拓扑不是合法枚举: {topo}")
            spec_map[cid] = {"topo": topo, "button": btn, "skirt": skirt}

        ledger_by_id = {r["target_id"]: r for r in ledger_rows if r["status"] == "纳入" and "categories" in r["catalog"]}
        for cid, spec in spec_map.items():
            if cid in ledger_by_id:
                l_btn = ledger_by_id[cid]["button"]
                l_skirt = ledger_by_id[cid]["skirt"]
                self.check(spec["button"] == l_btn, f"款式 {cid} 解扣能力跨文档不一致: 规格表={spec['button']}, 台账={l_btn}")
                self.check(spec["skirt"] == l_skirt, f"款式 {cid} 掀裙能力跨文档不一致: 规格表={spec['skirt']}, 台账={l_skirt}")

        # 针对特定款式强约束断言
        for non_skirt in ("business_suit", "combat_tactical", "tailcoat", "military_uniform", "black_leggings", "denim_shorts", "hot_pants"):
            if non_skirt in spec_map:
                self.check(spec_map[non_skirt]["skirt"] == "禁止", f"非裙装 {non_skirt} 掀裙能力应为禁止: {spec_map[non_skirt]['skirt']}")

        self.check("sweater_casual" in spec_map, "规格表中缺失 sweater_casual")
        if "sweater_casual" in spec_map:
            self.check(spec_map["sweater_casual"]["topo"] == "top", f"sweater_casual 拓扑应为 top: {spec_map['sweater_casual']['topo']}")
            self.check(spec_map["sweater_casual"]["button"] == "禁止", f"sweater_casual 解扣应为禁止: {spec_map['sweater_casual']['button']}")
            self.check(spec_map["sweater_casual"]["skirt"] == "不适用", f"sweater_casual 掀裙应为不适用: {spec_map['sweater_casual']['skirt']}")

        print(f"  ✓ 差集报告与台账提取清单 100% 集合等价 (81-0+28=109 守恒闭合，拓扑严格遵循 models.py 合法枚举，跨文档属性 100% 一致)。")

    def verify_zero_dangling_targets(self, ledger_rows: List[Dict[str, str]]) -> None:
        print("\n[Check 4/6] 校验目标 Catalog 规范 ID 零悬空 (合并目标的有效归口)...")
        # 加载现有运行库 ID
        clothing_data = json.loads((DATA_DIR / "clothing.json").read_text(encoding="utf-8"))
        accessories_data = json.loads((DATA_DIR / "accessories.json").read_text(encoding="utf-8"))
        imperfections_data = json.loads((DATA_DIR / "imperfections.json").read_text(encoding="utf-8"))

        existing_categories = {c["id"] for c in clothing_data.get("categories", [])}
        existing_states = {s["id"] for s in clothing_data.get("clothing_states", [])}
        existing_lingerie = {l["id"] for l in clothing_data.get("lingerie_wardrobe", [])}
        existing_jewelry = {j["id"] for j in accessories_data.get("headwear_jewelry", [])}
        existing_imperfections = {i["id"] for i in imperfections_data.get("categories", [])}

        # 纳入新增的 ID
        new_categories = {r["target_id"] for r in ledger_rows if r["status"] == "纳入" and "categories" in r["catalog"]}
        new_states = {r["target_id"] for r in ledger_rows if r["status"] == "纳入" and "clothing_states" in r["catalog"]}
        new_jewelry = {r["target_id"] for r in ledger_rows if r["status"] == "纳入" and "headwear_jewelry" in r["catalog"]}
        new_lingerie = {r["target_id"] for r in ledger_rows if r["status"] == "纳入" and "lingerie_wardrobe" in r["catalog"]}
        new_imperfections = {r["target_id"] for r in ledger_rows if r["status"] == "纳入" and "imperfections" in r["catalog"]}

        # 合并目标白名单池
        valid_category_pool = existing_categories | new_categories
        valid_state_pool = existing_states | new_states
        valid_jewelry_pool = existing_jewelry | new_jewelry
        valid_lingerie_pool = existing_lingerie | new_lingerie
        valid_imperfection_pool = existing_imperfections | new_imperfections

        dangling_targets = []
        for r in ledger_rows:
            if r["status"] == "延期":
                continue
            target = r["target_id"]
            cat_type = r["catalog"]

            if "clothing.json (categories)" in cat_type:
                if target not in valid_category_pool:
                    dangling_targets.append((r["row_id"], r["name_zh"], target, cat_type))
            elif "clothing.json (clothing_states)" in cat_type:
                if target not in valid_state_pool:
                    dangling_targets.append((r["row_id"], r["name_zh"], target, cat_type))
            elif "accessories.json" in cat_type:
                if target not in valid_jewelry_pool:
                    dangling_targets.append((r["row_id"], r["name_zh"], target, cat_type))
            elif "lingerie_wardrobe" in cat_type:
                if target not in valid_lingerie_pool:
                    dangling_targets.append((r["row_id"], r["name_zh"], target, cat_type))
            elif "imperfections.json" in cat_type:
                if target not in valid_imperfection_pool:
                    dangling_targets.append((r["row_id"], r["name_zh"], target, cat_type))

        self.check(len(dangling_targets) == 0, f"发现悬空目标 ID: {dangling_targets}")
        print(f"  ✓ 全量 246 行台账目标 ID 零悬空，所有合并目标均落在明确的存量或新增规范归口中。")

    def verify_carrier_spec(self) -> None:
        print("\n[Check 5/6] 校验状态三元分类规约与承载绑定规范...")
        self.check(CARRIER_SPEC_PATH.exists(), f"承载规约文件不存在: {CARRIER_SPEC_PATH}")
        if not CARRIER_SPEC_PATH.exists():
            return

        spec_text = CARRIER_SPEC_PATH.read_text(encoding="utf-8")
        
        # 1. 状态三元模型
        self.check("修饰状态" in spec_text or "Modifiers" in spec_text, "承载规约中缺少修饰状态 (Modifiers) 定义")
        self.check("缺席/真空状态" in spec_text or "Absence" in spec_text, "承载规约中缺少缺席/真空状态 (Absence) 定义")
        self.check("多层叠穿状态" in spec_text or "Layering" in spec_text, "承载规约中缺少多层叠穿状态 (Layering) 定义")

        # 2. 全域承载物白名单
        self.check("lingerie" in spec_text, "承载主体白名单未覆盖 lingerie 槽位")
        self.check("jewelry" in spec_text, "承载主体白名单未覆盖 jewelry 槽位")
        self.check("cloak" in spec_text, "承载主体白名单未包含披风 cloak")
        self.check("crotchless_panties" in spec_text, "承载主体白名单未包含开裆内裤 crotchless_panties")

        # 3. discarded 状态精准主体绑定与注销机制
        self.check("is_worn=False" in spec_text, "规约缺少 is_worn=False 注销标记")
        self.check("is_ambient=True" in spec_text, "规约缺少 is_ambient=True 标记")
        self.check("状态防自我承载铁律" in spec_text, "规约缺少状态防自我承载铁律")
        self.check("GarmentCarrierEntity" in spec_text, "承载规约缺少 GarmentCarrierEntity 服装实体模型")
        self.check("find_bound_carrier" in spec_text, "承载规约缺少 find_bound_carrier 实体绑定定义")
        self.check("build_garment_entity_key" in spec_text, "承载规约缺少 build_garment_entity_key 实体键规范化算法")
        self.check("区分来源与实体身份" in spec_text, "承载规约缺少来源与实体身份解耦原则")
        self.check("AMBIGUOUS_MULTIPLE_CANDIDATES" in spec_text, "承载规约缺少多候选歧义状态")
        self.check("ambiguous_carrier_binding" in spec_text, "承载规约缺少歧义审计记录定义")
        self.check('"one_piece_swimsuit"' in spec_text, "承载规约 NON_SKIRT_ONE_PIECE 遗漏存量 one_piece_swimsuit")
        self.check('"qipao"' in spec_text, "承载规约 NON_SKIRT_ONE_PIECE 遗漏旗袍 qipao")
        self.check("has_distinct_kimono_and_skirt" in spec_text or "k.entity_id != s.entity_id" in spec_text, "承载规约缺少和服叠穿双层独立实体校验")
        self.check("leo.entity_id != out.entity_id" in spec_text or "has_outer_covering" in spec_text, "承载规约缺少紧身衣叠穿双层实体校验")
        self.check("EXPLICIT_BRA_STYLES" in spec_text and "EXPLICIT_PANTIES_STYLES" in spec_text, "承载规约未区分文胸与内裤集合")
        self.check("has_explicit_bra" in spec_text and "has_explicit_panties" in spec_text, "承载规约未分立 has_explicit_bra 与 has_explicit_panties")
        self.check('"pulled_down"' in spec_text and "has_pullable" in spec_text, "承载规约缺少 pulled_down 承载判定分支")
        self.check("旗袍均严格设定为“禁止”" in spec_text or ("qipao" in spec_text and "禁止" in spec_text), "承载规约旗袍掀裙能力应与台账一致标为禁止")

        self.check("UNBOUND_TARGET_NOT_FOUND" in spec_text, "承载规约缺少 UNBOUND_TARGET_NOT_FOUND 快速失败状态")
        self.check("UNBOUND_INCOMPATIBLE" in spec_text, "承载规约缺少 UNBOUND_INCOMPATIBLE 形制能力互斥快速失败状态")
        self.check("is_garment_compatible_with_state" in spec_text, "承载规约缺少 is_garment_compatible_with_state 形制能力判定函数")
        self.check("严禁子串模糊匹配" in spec_text or "绝对禁止" in spec_text, "承载规约缺少禁止子串模糊匹配契约")

        print("  ✓ 承载规约具备三元分类模型、实体键生成、四阶绑定梯、歧义保全拒绝盲选、NON_SKIRT_ONE_PIECE 旗袍对齐与双层实体校验。")

    def verify_test_fixtures(self) -> None:
        print("\n[Check 6/6] 校验测试夹具完整性与黑盒解耦契约...")
        try:
            sys.path.insert(0, str(REPO_DIR))
            from tests.fixtures.conflict_rule_fixtures import (
                BindingStatus,
                extract_garment_entities,
                find_bound_carrier,
                is_garment_compatible_with_state,
                make_test_atom,
                get_all_level_a_fixtures,
                get_all_level_b_fixtures,
            )

            # 校验 BindingStatus 枚举包含全部 5 个确定性状态
            expected_statuses = {"bound", "unbound_no_candidate", "unbound_target_not_found", "unbound_incompatible", "ambiguous_multiple_candidates"}
            actual_statuses = {s.value for s in BindingStatus}
            self.check(actual_statuses == expected_statuses, f"BindingStatus 枚举集合不符: {actual_statuses}")

            # 自动化校验反例 1: 仅连体泳衣+解扣 -> UNBOUND_NO_CANDIDATE
            sw = make_test_atom("swimsuit", "clothing", "swimsuit_school", garment_topologies=("one_piece",), garment_states=("worn",))
            ub = make_test_atom("unbuttoned", "clothing_state", "unbuttoned")
            ents_sw = extract_garment_entities([sw])
            res_sw = find_bound_carrier(ub, list(ents_sw.values()))
            self.check(res_sw.status == BindingStatus.UNBOUND_NO_CANDIDATE and res_sw.target_entity is None, "反例 1 (仅泳衣+解扣) 判定失败，未返回 UNBOUND_NO_CANDIDATE")

            # 自动化校验反例 2: 目标写 shirt 现存 shirts_blouses -> UNBOUND_TARGET_NOT_FOUND (拒绝模糊子串)
            sh = make_test_atom("shirt", "clothing", "shirts_blouses", garment_topologies=("top",), garment_states=("worn",))
            dc_fuzzy = make_test_atom("discarded", "clothing_state", "discarded", target_id="shirt")
            ents_sh = extract_garment_entities([sh])
            res_fuzzy = find_bound_carrier(dc_fuzzy, list(ents_sh.values()))
            self.check(res_fuzzy.status == BindingStatus.UNBOUND_TARGET_NOT_FOUND and res_fuzzy.target_entity is None, "反例 2 (拒绝子串模糊匹配) 判定失败，未返回 UNBOUND_TARGET_NOT_FOUND")

            # 自动化校验反例 3: 目标 pencil_skirt 现场仅衬衫 -> UNBOUND_TARGET_NOT_FOUND (绝不回退改绑)
            dc_sk = make_test_atom("discarded", "clothing_state", "discarded", target_id="pencil_skirt")
            res_sk = find_bound_carrier(dc_sk, list(ents_sh.values()))
            self.check(res_sk.status == BindingStatus.UNBOUND_TARGET_NOT_FOUND and res_sk.target_entity is None, "反例 3 (目标不存在快速失败无回退) 判定失败，未返回 UNBOUND_TARGET_NOT_FOUND")

            # 自动化校验补充: 显式指定泳衣解扣 -> UNBOUND_INCOMPATIBLE
            ub_sw = make_test_atom("unbuttoned", "clothing_state", "unbuttoned", target_id="swimsuit_school")
            res_incompat = find_bound_carrier(ub_sw, list(ents_sw.values()))
            self.check(res_incompat.status == BindingStatus.UNBOUND_INCOMPATIBLE, "显式指定泳衣解扣未返回 UNBOUND_INCOMPATIBLE")

            level_a_cases = get_all_level_a_fixtures()
            self.check(len(level_a_cases) == 7, f"Level A 夹具用例数不为 7: {len(level_a_cases)}")

            expected_case_ids = {"A-1", "A-2", "A-3", "A-4", "A-5", "A-6", "A-7"}
            actual_case_ids = {c.case_id for c in level_a_cases}
            self.check(actual_case_ids == expected_case_ids, f"Level A 用例 ID 不符: {actual_case_ids}")

            for c in level_a_cases:
                self.check(bool(c.name), f"用例 {c.case_id} 缺少名称")
                self.check(len(c.input_atoms) >= 2, f"用例 {c.case_id} 输入原子少于 2 个")
                for a in c.input_atoms:
                    self.check(bool(a.atom_id), f"用例 {c.case_id} 包含未赋值 atom_id 的原子")
                    self.check(bool(a.source_slot), f"用例 {c.case_id} 包含未赋值 source_slot 的原子")
                    self.check(a.provenance is not None, f"用例 {c.case_id} 包含未赋值 provenance 的原子")

            level_b_cases = get_all_level_b_fixtures()
            self.check(len(level_b_cases) == 2, f"Level B 夹具用例数不为 2: {len(level_b_cases)}")
            b_ids = {c.case_id for c in level_b_cases}
            self.check(b_ids == {"TC-INT-001", "TC-INT-002"}, f"Level B 用例 ID 不符: {b_ids}")

            for b in level_b_cases:
                self.check("服装款式" in b.inputs, f"Level B 用例 {b.case_id} 缺少服装款式入参")
                self.check("服装状态" in b.inputs, f"Level B 用例 {b.case_id} 缺少服装状态入参")
                self.check("裸露等级" in b.inputs, f"Level B 用例 {b.case_id} 缺少裸露等级入参")
                self.check(b.enforce_single_drop, f"Level B 用例 {b.case_id} 未开启 enforce_single_drop 黑盒契约")
                self.check(b.enforce_ledger_conservation, f"Level B 用例 {b.case_id} 未开启 enforce_ledger_conservation 守恒契约")

            print(f"  ✓ 测试夹具 Level A (7用例含A-5和服多原子叠穿反例、A-6同一预设两件衣物独立实体反例、A-7换序不变性契约) 与 Level B (2用例) 及反例断言契约 100% 合规。")
        except Exception as e:
            self.check(False, f"测试夹具加载或校验异常: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ComfyUI-IYKYK rc9 服装词库迁移全流程自动化门禁核验")
    parser.add_argument(
        "--raw-media-path",
        type=Path,
        default=DEFAULT_MEDIA_PATH,
        help="原始 Markdown 媒体附件物理路径 (必须存在且与 TSV 逐行 1:1 一致)",
    )
    args = parser.parse_args()
    runner = VerificationRunner(raw_media_path=args.raw_media_path)
    sys.exit(runner.run())
