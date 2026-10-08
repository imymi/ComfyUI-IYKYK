import copy
import dataclasses
import hashlib
import json
import re
from pathlib import Path
from random import Random
import sys
from collections import Counter

from nodes import IYKYKPromptGenerator
from lib.sampler import DataSampler
from lib.conflict_resolver import ConflictResolver
from lib.models import PromptAtom, SemanticFacts, TagProvenance

ROOT = Path("/Users/jacobyang/Hermes/ComfyUI-IYKYK")
DATA_DIR = ROOT / "data"

EXPECTED_HASHES = {
    "expressions.json": "b2052279dbf12657bff56668291a2301c6b5783809ae44b6691f68a6c61248ef",
    "lighting.json": "ce7950c99822156b39eb6a859856c57d4d0e33dc337f7b92ca9c1b17d2f2c3ac",
    "accessories.json": "45ff0c77286c47cda4f8b1b5ef9d1157870f1b70747b19e3f6fb90e7fbca5bc0",
    "shot_types.json": "24ae39af0753b17172471edf4bec6665fdbfcae6168dd72fad0bb6adde98b702",
    "film_stocks.json": "8eab7b02f23cf11160d8c62c335de16dc64b93142aa339079da91644b9a22c5e",
    "poses.json": "c77c98a2ca3890c26be4262bbee073b085ccf83fafa95ac7480ae6dda8b3a0f2",
    "scenes.json": "35804b30eb79a9b8d169f93e8c4adf484ce7dfe676fa408ded3c9dd746d1b0fa",
    "clothing.json": "41132a62ff3b310b2ccb3bdbea4fb6ef0d889023711ced5e8b25f2b8c66befca",
}

# ─────────────────────────────────────────────────────────────
# 1. 资产哈希验证
# ─────────────────────────────────────────────────────────────

def verify_file_hashes():
    print("=== [1] Verifying Data File Hashes ===")
    all_ok = True
    for fname, expected in EXPECTED_HASHES.items():
        p = DATA_DIR / fname
        if not p.exists():
            print(f"[FAIL] Missing {fname}")
            all_ok = False
            continue
        actual = hashlib.sha256(p.read_bytes()).hexdigest()
        if actual == expected:
            print(f"[PASS] {fname}: {actual}")
        else:
            print(f"[FAIL] {fname}: expected {expected}, got {actual}")
            all_ok = False
    return all_ok

# ─────────────────────────────────────────────────────────────
# 2. 83 条隔离项全量对账与物理隔离核验 (同一函数评估正常与变异反例)
# ─────────────────────────────────────────────────────────────

def load_all_quarantined_records():
    ledgers = [
        (3, ROOT / "scratch/m4_batch3_execution_ledger.json"),
        (4, ROOT / "scratch/m4_batch4_execution_ledger.json"),
        (5, ROOT / "scratch/m4_batch5_execution_ledger.json"),
        (6, ROOT / "scratch/m4_batch6_execution_ledger.json"),
    ]
    quarantined = []
    for b, lpath in ledgers:
        with open(lpath) as fp:
            d = json.load(fp)
        for r in d.get("records", []):
            if r.get("status") == "QUARANTINED" or r.get("decision") == "DEFERRED_ISSUE" or "QUARANTINE" in str(r.get("status")):
                quarantined.append(r)
    return quarantined

def extract_production_text_and_ids(filepath: Path):
    doc = json.loads(filepath.read_text(encoding="utf-8"))
    tag_ids = set()
    tag_texts = set()
    item_ids = set()

    def walk(obj):
        if isinstance(obj, dict):
            if "id" in obj:
                item_ids.add(str(obj["id"]).strip().lower())
            if "label" in obj:
                item_ids.add(str(obj["label"]).strip().lower())
            if "text" in obj:
                tag_texts.add(str(obj["text"]).strip().lower())
                if "id" in obj:
                    tag_ids.add(str(obj["id"]).strip().lower())
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for x in obj:
                walk(x)

    walk(doc)
    return item_ids, tag_ids, tag_texts

def evaluate_quarantine_isolation(cache_map: dict, quarantined_list: list) -> list:
    """真实物理隔离判定器：检查生产缓存中是否存在任何隔离项泄露。"""
    leaks = []
    for q in quarantined_list:
        tf = q.get("target_file")
        if tf not in cache_map:
            continue
        item_ids, tag_ids, tag_texts = cache_map[tf]
        clean_text = (q.get("clean_text") or "").strip().lower()
        tag_id = (q.get("target_tag_id") or "").strip().lower()
        item_id = (q.get("target_item_id") or "").strip().lower()

        if clean_text and clean_text in tag_texts:
            leaks.append((q["mapping_id"], "tag_text", clean_text, tf))
        if tag_id and tag_id in tag_ids:
            leaks.append((q["mapping_id"], "tag_id", tag_id, tf))
        if item_id in ("quarantine", "quarantined_artifacts") and item_id in item_ids:
            leaks.append((q["mapping_id"], "quarantine_container", item_id, tf))
    return leaks

def verify_targeted_isolation():
    print("\n=== [2] Targeted Isolation Verification for 83 Entities (Fail-Closed) ===")
    all_ok = True
    quarantined = load_all_quarantined_records()
    if len(quarantined) != 83:
        print(f"[FAIL] Expected exactly 83 quarantined records across ledgers, found {len(quarantined)}")
        return False
    print(f"[INFO] Extracted {len(quarantined)} quarantined records from Batch 3..6 ledgers.")

    file_cache = {}
    for q in quarantined:
        tf = q.get("target_file")
        if tf not in file_cache:
            file_cache[tf] = extract_production_text_and_ids(DATA_DIR / tf)

    # 2.1 正常数据核验：必须 0 泄露
    real_leaks = evaluate_quarantine_isolation(file_cache, quarantined)
    if real_leaks:
        print(f"[FAIL] Found {len(real_leaks)} leaks of quarantined items in production data!")
        for l in real_leaks:
            print("  Leak:", l)
        all_ok = False
    else:
        print(f"[PASS] All 83 quarantined entities confirmed 100% physically isolated from production slots.")

    # 2.2 验证合法同词主题 cosplay 完整保留
    themes_doc = json.loads((DATA_DIR / "themes.json").read_text(encoding="utf-8"))
    cosplay_tags = []
    for g in themes_doc.get("themes", []):
        for t in g.get("tags", []):
            if "cosplay" in t.get("text", "").lower():
                cosplay_tags.append(t.get("text"))
    if cosplay_tags:
        print(f"[PASS] Legitimate 'cosplay' theme tags successfully retained in themes.json: {cosplay_tags}")
    else:
        print("[FAIL] Legitimate theme 'cosplay' was accidentally lost from themes.json!")
        all_ok = False

    # 2.3 变异反例检验：将变异数据传入同一个 evaluate_quarantine_isolation 检查函数
    mutated_cache = {k: (set(v[0]), set(v[1]), set(v[2])) for k, v in file_cache.items()}
    # 模拟在 poses.json 中注入被隔离词 "casing ejection"
    if "poses.json" in mutated_cache:
        mutated_cache["poses.json"][2].add("casing ejection")
    # 模拟在 scenes.json 中注入隔离容器 "quarantine"
    if "scenes.json" in mutated_cache:
        mutated_cache["scenes.json"][0].add("quarantine")

    mutated_leaks = evaluate_quarantine_isolation(mutated_cache, quarantined)
    detected_phrases = [l[2] for l in mutated_leaks]
    if "casing ejection" not in detected_phrases:
        print("[FAIL] evaluate_quarantine_isolation failed to detect injected 'casing ejection'!")
        all_ok = False
    else:
        print(f"[PASS] Fail-Closed verified: same evaluate_quarantine_isolation() successfully detected {len(mutated_leaks)} leaks in mutated cache ({detected_phrases}).")

    return all_ok

# ─────────────────────────────────────────────────────────────
# 3. 未展开模板语法与原文保真检查 (包含来源实体对照)
# ─────────────────────────────────────────────────────────────

def find_unexpanded_templates(prompt: str):
    """检测最终提示词中残留的各类未展开模板与通配符语法。
    
    涵盖：
    1. 裸通配符引用: __by_source/...__ 或 __wildcard__
    2. 分支选择候选项: {red|blue}, {4::black|white}, {|, suffix}
    3. 动态数量抽取: {1-3$$hair_styles}
    4. 变量占位符与插值: {color}, {hair_lengths}
    5. 任何未展开的花括号结构: {[^{}]+}
    """
    issues = []
    # 1. 通配符引用
    wc = re.findall(r"__[\w\-\/]+__", prompt)
    if wc:
        issues.extend([f"wildcard:{w}" for w in wc])
    # 2. 分支语法
    alts = re.findall(r"\{[^{}]*\|[^{}]*\}", prompt)
    if alts:
        issues.extend([f"choice:{a}" for a in alts])
    # 3. 动态范围抽取
    ranges = re.findall(r"\{[0-9]+-[0-9]+\$\$[^{}]*\}", prompt)
    if ranges:
        issues.extend([f"range:{r}" for r in ranges])
    # 4. 其它所有花括号结构
    braces = re.findall(r"\{[^{}]+\}", prompt)
    for b in braces:
        if b not in alts and b not in ranges:
            issues.append(f"brace:{b}")
    return issues

def verify_template_syntax_and_prompts():
    print("\n=== [3] Template Syntax Coverage & Source Fidelity Check (Fail-Closed) ===")
    all_ok = True

    # 3.1 验证元数据模板在 accessories.json 中合法保存
    acc_doc = json.loads((DATA_DIR / "accessories.json").read_text(encoding="utf-8"))
    found_template = False
    for item in acc_doc.get("hairstyles", []):
        if item.get("id") == "hair_ensemble_composite":
            for t in item.get("tags", []):
                gm = t.get("facts", {}).get("governance_metadata", {})
                if "color_emphasis_wrapper" in gm and "raw_template_format" in gm:
                    found_template = True
                    print(f"[PASS] Valid metadata template confirmed in accessories.json: wrapper={gm['color_emphasis_wrapper']!r}, raw={gm['raw_template_format']!r}")
    if not found_template:
        print("[FAIL] Expected metadata template not found in accessories.json hair_ensemble_composite!")
        all_ok = False

    # 3.2 来源原文保真对照：对照 SRC_HAIR_00005 与目标映射，杜绝 bash PID 展开污染
    src_entities_text = (ROOT / "scratch/rc10_source_entities.tsv").read_text(encoding="utf-8")
    tgt_mappings_text = (ROOT / "scratch/rc10_target_mappings.tsv").read_text(encoding="utf-8")
    acc_text = (DATA_DIR / "accessories.json").read_text(encoding="utf-8")

    expected_macro = "{1-3$$__by_source/skyyysi/02_hair/hair_styles__}"
    corrupted_pattern = re.compile(r"\{1-3\d+hair_styles\}")

    # 检查生产数据与映射台账中是否残留任何 bash PID 展开错误
    if corrupted_pattern.search(acc_text):
        print("[FAIL] Detected bash PID corruption {1-3<PID>hair_styles} in data/accessories.json!")
        all_ok = False
    elif corrupted_pattern.search(tgt_mappings_text):
        print("[FAIL] Detected bash PID corruption {1-3<PID>hair_styles} in rc10_target_mappings.tsv!")
        all_ok = False
    else:
        print("[PASS] Zero bash PID expansions ({1-3<PID>...}) confirmed across production data and mapping ledgers.")

    # 确认源实体、映射台账与生产元数据三者严格对齐保留原文宏
    if expected_macro not in src_entities_text:
        print(f"[FAIL] Expected macro '{expected_macro}' missing from rc10_source_entities.tsv (SRC_HAIR_00005)!")
        all_ok = False
    elif expected_macro not in tgt_mappings_text:
        print(f"[FAIL] Expected macro '{expected_macro}' missing from rc10_target_mappings.tsv (MAP_00132)!")
        all_ok = False
    elif expected_macro not in acc_text:
        print(f"[FAIL] Expected macro '{expected_macro}' missing from data/accessories.json!")
        all_ok = False
    else:
        print(f"[PASS] 100% Fidelity confirmed: exact macro '{expected_macro}' strictly preserved across Source -> Ledger -> Production.")

    # 3.3 构造 6 组未展开反例，断言检查器 100% 拦截
    dirty_counterexamples = [
        ("Wildcard reference", "a beautiful girl in __by_source/skyyysi/09_style/colors__ dress"),
        ("Choice alternation", "a girl with {red|blue} hair"),
        ("Weighted choice", "a girl with {4::black|white} choker"),
        ("Dynamic range selector", "a girl with {1-3$$hair_styles}"),
        ("Color variable", "a girl with ({color} hair)"),
        ("Optional prefix/suffix", "a girl with {|, hair_ornaments}"),
    ]
    for label, dirty_prompt in dirty_counterexamples:
        detected = find_unexpanded_templates(dirty_prompt)
        if not detected:
            print(f"[FAIL] Check failed: dirty prompt '{dirty_prompt}' was NOT detected!")
            all_ok = False
        else:
            print(f"[PASS] Counterexample caught: {label} -> {detected}")

    # 干净提示词断言通过
    clean_prompt = "masterpiece, 1girl, black hair, looking at camera, high quality"
    if find_unexpanded_templates(clean_prompt):
        print("[FAIL] False positive on clean prompt!")
        all_ok = False
    else:
        print("[PASS] Clean prompt evaluated with 0 false positives.")

    return all_ok

# ─────────────────────────────────────────────────────────────
# 4. 真实判定器定义 (同一函数评估正常数据与变异反例)
# ─────────────────────────────────────────────────────────────

def evaluate_clothing_atoms(atoms: list, expected_item_id: str) -> None:
    """服装单品款式互斥与归属判定器。"""
    cloth_atoms = [a for a in atoms if a.source_slot in ("clothing", "clothing_state")]
    if not cloth_atoms:
        raise ValueError("0 clothing atoms produced")
    base_atoms = [a for a in cloth_atoms if getattr(a.provenance, "kind", None) == "base_clothing"]
    if len(base_atoms) != 1:
        raise ValueError(f"Expected exactly 1 base_clothing atom, got {len(base_atoms)}: {[a.text for a in base_atoms]}")
    if base_atoms[0].provenance.item_id != expected_item_id:
        raise ValueError(f"Base clothing atom item_id mismatch: expected {expected_item_id}, got {base_atoms[0].provenance.item_id}")

def evaluate_pose_template_atoms(atoms: list) -> None:
    """整身叙述姿态单选判定器。"""
    p_atoms = [a for a in atoms if a.source_slot == "pose"]
    if not p_atoms:
        raise ValueError("0 pose atoms produced")
    unique_tags = {a.id for a in p_atoms}
    if len(unique_tags) != 1:
        raise ValueError(f"Pose atoms originated from {len(unique_tags)} different tags: {unique_tags}")

def evaluate_water_scene_result(res_obj, exp_item: str) -> None:
    """水体自然场景语义与空间判定器。"""
    if res_obj.item_id != exp_item:
        raise ValueError(f"Expected item_id {exp_item}, got {res_obj.item_id}")
    if not res_obj.sampled_tags:
        raise ValueError("No sampled tags in scene result")
    for tag in res_obj.sampled_tags:
        if tag.facts.space_kind != "outdoor":
            raise ValueError(f"space_kind must be outdoor, got {tag.facts.space_kind}")
        if "natural_water" not in (tag.facts.venue_ids or ()):
            raise ValueError(f"venue_ids must contain natural_water, got {tag.facts.venue_ids}")
        if "bathtub" in tag.text.lower():
            raise ValueError(f"text leaked bathtub: {tag.text}")

def evaluate_library_atom_facts(atom: PromptAtom) -> None:
    """图书馆场景事实与场所归属判定器。"""
    if atom.facts.time_of_day not in (None, "unspecified"):
        raise ValueError(f"Hardcoded daytime: time_of_day={atom.facts.time_of_day}")
    if "library" not in (atom.facts.venue_ids or ()):
        raise ValueError(f"venue_ids missing library: {atom.facts.venue_ids}")
    if "school library" not in atom.text.lower():
        if atom.facts.venue_ids == ("school",) or "school" in atom.facts.venue_ids:
            raise ValueError(f"General library atom falsely marked as school: text={atom.text!r}, venue_ids={atom.facts.venue_ids}")

# ─────────────────────────────────────────────────────────────
# 5. 500 种子全槽位语义及历史反例实测
# ─────────────────────────────────────────────────────────────

def run_500_seed_and_slot_verification():
    print("\n=== [4] 500-Seed Full-Slot Semantic & Counterexample Verification ===")
    all_ok = True
    gen = IYKYKPromptGenerator()
    sampler = DataSampler(DATA_DIR)

    # 5.1 500 种子全开槽位随机压力测试
    print("Testing 500 random seeds for full pipeline output...")
    template_leak_errors = []
    semantic_conflict_errors = []

    for seed in range(500):
        try:
            res = gen.generate_structured(
                预设模板="随机 (Random)",
                风格配方="随机 (Random)",
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
                prompt_seed=seed,
            )
            prompt = res.positive
            issues = find_unexpanded_templates(prompt)
            if issues:
                template_leak_errors.append((seed, issues, prompt))

            # 语义相容性核查：姿态不能同时包含互斥姿态
            pose_texts = [a.text.lower() for a in res.atoms if a.source_slot == "pose"]
            has_stand = any("standing" in pt for pt in pose_texts)
            has_crouch = any("crouching" in pt or "crouch" in pt for pt in pose_texts)
            if has_stand and has_crouch:
                semantic_conflict_errors.append((seed, f"Pose conflict: standing and crouching in pose atoms: {pose_texts}"))

            # 黑白胶卷时不能有彩色相纸
            film_atoms = [a for a in res.atoms if a.source_slot == "film"]
            has_mono_film = any(a.facts and "monochrome" in (a.facts.color_modes or ()) for a in film_atoms)
            if has_mono_film:
                has_color_paper = any("color photographic paper" in a.text.lower() or "vibrant color" in a.text.lower() for a in film_atoms)
                if has_color_paper:
                    semantic_conflict_errors.append((seed, f"Chroma conflict: monochrome film with color paper in {film_atoms}"))

        except Exception as e:
            semantic_conflict_errors.append((seed, f"Exception: {e}"))
            all_ok = False

    if template_leak_errors:
        print(f"[FAIL] Found {len(template_leak_errors)} seeds with unexpanded template syntax in prompt: {template_leak_errors[:3]}")
        all_ok = False
    else:
        print("[PASS] All 500 seeds produced final prompts with ZERO unexpanded templates/wildcards.")

    if semantic_conflict_errors:
        print(f"[FAIL] Found {len(semantic_conflict_errors)} semantic errors: {semantic_conflict_errors[:5]}")
        all_ok = False
    else:
        print("[PASS] All 500 seeds passed semantic consistency checks with 0 conflicts.")

    # 5.2 历史反例专项核验 (保证非空靶 + 真实断言 + 同一函数评估正常/变异反例)
    print("\n--- [4.2] Historical Counterexample Verification (Fail-Closed Evaluators) ---")

    # [Case 1] 服装款式单选互斥与相容属性附加 (奥黛/长衫/纱丽/旗袍)
    clothing_targets = [
        ("奥黛", "奥黛传统长衫 (Ao Dai)", "ao_dai"),
        ("长衫", "传统长衫与中式袍服 (Changshan)", "changshan"),
        ("纱丽", "传统莎丽裹裙长袍 (Sari)", "sari"),
        ("旗袍", "旗袍 (Qipao/Cheongsam)", "qipao"),
    ]
    last_cloth_atoms = None
    for c_label, c_opt, expected_id in clothing_targets:
        r = gen.generate_structured(
            预设模板="无 (None)",
            风格配方="无 (None)",
            场景大类="无 (None)",
            剧情主题="无 (None)",
            景别构图="无 (None)",
            拍摄视角="无 (None)",
            裸露等级="无 (None)",
            服装款式=c_opt,
            服装状态="无 (None)",
            prompt_seed=42,
        )
        try:
            evaluate_clothing_atoms(r.atoms, expected_id)
            cloth_atoms = [a for a in r.atoms if a.source_slot in ("clothing", "clothing_state")]
            base_atoms = [a for a in cloth_atoms if getattr(a.provenance, "kind", None) == "base_clothing"]
            print(f"[PASS] Case Clothing {c_label}: exactly 1 base clothing atom '{base_atoms[0].text[:45]}...' (item={expected_id})")
            last_cloth_atoms = r.atoms
        except Exception as e:
            print(f"[FAIL] evaluate_clothing_atoms rejected valid clothing {c_label}: {e}")
            all_ok = False

    # 服装 Fail-Closed 变异检验：向 evaluate_clothing_atoms 传入注入第 2 件 base_clothing 的变异列表
    if last_cloth_atoms:
        base_atom_clone = copy.deepcopy([a for a in last_cloth_atoms if getattr(a.provenance, "kind", None) == "base_clothing"][0])
        mutated_cloth_atoms = list(last_cloth_atoms) + [base_atom_clone]
        try:
            evaluate_clothing_atoms(mutated_cloth_atoms, "qipao")
            print("[FAIL] evaluate_clothing_atoms failed to reject duplicate base clothing!")
            all_ok = False
        except ValueError as e:
            print(f"[PASS] Fail-Closed verified: same evaluate_clothing_atoms() strictly rejected mutated duplicate clothing: {e}")

    # [Case 2] 整身姿态模版互斥 (100 Seeds 真实全量遍历)
    print("Testing Pose template single selection across 100 seeds (seed 0..99)...")
    pose_failures = []
    last_p_atoms = None
    for seed in range(100):
        r_gen = gen.generate_structured(
            预设模板="无 (None)",
            风格配方="无 (None)",
            场景大类="无 (None)",
            剧情主题="无 (None)",
            景别构图="无 (None)",
            拍摄视角="无 (None)",
            裸露等级="无 (None)",
            服装款式="无 (None)",
            服装状态="无 (None)",
            姿势动作="📜 自然语言整身叙述模板 (Prose Pose Templates)",
            prompt_seed=seed,
        )
        try:
            evaluate_pose_template_atoms(r_gen.atoms)
            last_p_atoms = r_gen.atoms
        except Exception as e:
            pose_failures.append((seed, str(e)))

    if pose_failures:
        print(f"[FAIL] Pose template failed on {len(pose_failures)}/100 seeds: {pose_failures[:3]}")
        all_ok = False
    else:
        print("[PASS] Case Pose Templates: 100/100 seeds confirmed exactly 1 prose template tag output.")

    # 姿态 Fail-Closed 变异检验：向 evaluate_pose_template_atoms 传入具有 2 个不同 tag 的姿态列表
    if last_p_atoms:
        target_p = [a for a in last_p_atoms if a.source_slot == "pose"][0]
        conflicting_atom = dataclasses.replace(
            target_p,
            id="mock_conflicting_pose_tag_id",
            text="standing while crouching"
        )
        mutated_pose_atoms = list(last_p_atoms) + [conflicting_atom]
        try:
            evaluate_pose_template_atoms(mutated_pose_atoms)
            print("[FAIL] evaluate_pose_template_atoms failed to reject conflicting pose tags!")
            all_ok = False
        except ValueError as e:
            print(f"[PASS] Fail-Closed verified: same evaluate_pose_template_atoms() strictly rejected mutated conflicting pose: {e}")

    # [Case 3] 河流与湖泊场景语义 (nature_river, outdoor, natural_water, 杜绝 bathtub)
    r_river = sampler.sample_scene_result("河流溪流", Random(42))
    r_lake = sampler.sample_scene_result("湖泊水域", Random(42))
    r_direct_river = sampler.sample_scene_result("river", Random(42))
    r_direct_lake = sampler.sample_scene_result("lake", Random(42))

    water_checks = [
        ("河流溪流", r_river, "nature_river"),
        ("湖泊水域", r_lake, "nature_lake"),
        ("文本直达 river", r_direct_river, "nature_river"),
        ("文本直达 lake", r_direct_lake, "nature_lake"),
    ]
    for label, res_obj, exp_item in water_checks:
        try:
            evaluate_water_scene_result(res_obj, exp_item)
            print(f"[PASS] Case Scene Water {label}: item={exp_item}, space=outdoor, venue=natural_water")
        except Exception as e:
            print(f"[FAIL] evaluate_water_scene_result rejected valid water scene {label}: {e}")
            all_ok = False

    # 水体 Fail-Closed 变异检验：向 evaluate_water_scene_result 传入变异对象
    tag0 = r_river.sampled_tags[0]
    bad_tag_indoor = dataclasses.replace(tag0, facts=dataclasses.replace(tag0.facts, space_kind="indoor"))
    bad_res_indoor = dataclasses.replace(r_river, sampled_tags=[bad_tag_indoor])
    try:
        evaluate_water_scene_result(bad_res_indoor, "nature_river")
        print("[FAIL] evaluate_water_scene_result failed to reject indoor water scene!")
        all_ok = False
    except ValueError as e:
        print(f"[PASS] Fail-Closed verified: same evaluate_water_scene_result() strictly rejected indoor space: {e}")

    bad_tag_bathtub = dataclasses.replace(tag0, text="bathing in a porcelain bathtub")
    bad_res_bathtub = dataclasses.replace(r_river, sampled_tags=[bad_tag_bathtub])
    try:
        evaluate_water_scene_result(bad_res_bathtub, "nature_river")
        print("[FAIL] evaluate_water_scene_result failed to reject bathtub text!")
        all_ok = False
    except ValueError as e:
        print(f"[PASS] Fail-Closed verified: same evaluate_water_scene_result() strictly rejected bathtub leak: {e}")

    # [Case 4] 图书馆场景事实 (无白天硬编码，通用设施禁止归属 school)
    lib_failures = []
    last_lib_atoms = []
    for seed in range(20):
        r_gen = gen.generate_structured(
            预设模板="无 (None)",
            风格配方="无 (None)",
            场景大类="图书馆/自习室",
            剧情主题="无 (None)",
            景别构图="无 (None)",
            拍摄视角="无 (None)",
            裸露等级="无 (None)",
            服装款式="无 (None)",
            服装状态="无 (None)",
            prompt_seed=seed,
        )
        s_atoms = [a for a in r_gen.atoms if getattr(a.provenance, "item_id", None) == "scene_library"]
        if not s_atoms:
            lib_failures.append((seed, "0 library atoms produced"))
            continue
        for a in s_atoms:
            try:
                evaluate_library_atom_facts(a)
                last_lib_atoms.append(a)
            except Exception as e:
                lib_failures.append((seed, str(e)))

    if lib_failures:
        print(f"[FAIL] Library checks failed on {len(lib_failures)} instances: {lib_failures[:3]}")
        all_ok = False
    else:
        print("[PASS] Case Library: 20 seeds verified with 0 daytime hardcoding and 0 general library school misclassifications.")

    # 图书馆 Fail-Closed 变异检验：向 evaluate_library_atom_facts 传入变异 atom
    if last_lib_atoms:
        atom0 = last_lib_atoms[0]
        bad_atom_day = dataclasses.replace(
            atom0,
            facts=dataclasses.replace(atom0.facts, time_of_day="day")
        )
        try:
            evaluate_library_atom_facts(bad_atom_day)
            print("[FAIL] evaluate_library_atom_facts failed to reject daytime hardcoding!")
            all_ok = False
        except ValueError as e:
            print(f"[PASS] Fail-Closed verified: same evaluate_library_atom_facts() strictly rejected daytime hardcode: {e}")

        bad_atom_school = dataclasses.replace(
            atom0,
            text="library stacks reading area",
            facts=dataclasses.replace(atom0.facts, venue_ids=("school",))
        )
        try:
            evaluate_library_atom_facts(bad_atom_school)
            print("[FAIL] evaluate_library_atom_facts failed to reject general library misclassified as school!")
            all_ok = False
        except ValueError as e:
            print(f"[PASS] Fail-Closed verified: same evaluate_library_atom_facts() strictly rejected legacy venue_ids=('school',): {e}")

    # [Case 5] 黑白胶卷色相相容性
    r_film = gen.generate_structured(胶片风格="Kodak Tri-X 400 (硬派粗粒·高反差黑白)", prompt_seed=5)
    film_atoms = [a for a in r_film.atoms if a.source_slot == "film"]
    if not film_atoms:
        print("[FAIL] Kodak Tri-X 400 produced 0 film atoms!")
        all_ok = False
    else:
        mono_atoms = [a for a in film_atoms if "monochrome" in (a.facts.color_modes or ())]
        if not mono_atoms:
            print("[FAIL] Kodak Tri-X 400 atoms missing monochrome color_mode!")
            all_ok = False
        else:
            if "color paper" in r_film.positive.lower() or "color photographic paper" in r_film.positive.lower():
                print("[FAIL] Monochrome film leaked color paper into prompt!")
                all_ok = False
            else:
                print(f"[PASS] Case Monochrome Film: Kodak Tri-X 400 verified with monochrome fact and 0 color paper leakage.")

    # [Case 6] L1 遮蔽过滤
    l1_res = sampler.sample_pose_result("dynamic__03__tag_023", Random(0), nudity_level_code="L1")
    l3_res = sampler.sample_pose_result("dynamic__03__tag_023", Random(0), nudity_level_code="L3")
    if l1_res.tags:
        print(f"[FAIL] L1 skirt lifted bypass: returned tags {l1_res.tags}")
        all_ok = False
    elif not l3_res.tags:
        print("[FAIL] L3 failed to return tag dynamic__03__tag_023!")
        all_ok = False
    else:
        print(f"[PASS] Case L1 Nudity: 'skirt lifted' successfully filtered out under L1, retained under L3.")

    # [Case 7] 皱眉微蹙 (emotion_frowning) 面部动作契约
    r_frown = gen.generate_structured(情绪表情="皱眉微蹙 (Frowning)", prompt_seed=10)
    frown_atoms = [a for a in r_frown.atoms if a.source_slot == "expression"]
    if not frown_atoms:
        print("[FAIL] Frowning produced 0 expression atoms!")
        all_ok = False
    else:
        has_frown = any("frown" in a.text.lower() for a in frown_atoms)
        if not has_frown:
            print(f"[FAIL] Frowning atoms missing 'frown': {[a.text for a in frown_atoms]}")
            all_ok = False
        else:
            print(f"[PASS] Case Emotion Frowning: facial action 'frown' verified without contract crash.")

    return all_ok

# ─────────────────────────────────────────────────────────────
# 6. 台账数字与实体映射关系核验
# ─────────────────────────────────────────────────────────────

def verify_numerical_ledgers():
    print("\n=== [5] Source & Target Numerical Ledger Reconciliation ===")
    all_ok = True
    src_file = ROOT / "scratch/rc10_source_entities.tsv"
    map_file = ROOT / "scratch/rc10_target_mappings.tsv"

    src_lines = [l for l in src_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    map_lines = [l for l in map_file.read_text(encoding="utf-8").splitlines() if l.strip()]

    total_sources = len(src_lines) - 1
    total_mappings = len(map_lines) - 1

    print(f"Total Source Entities: {total_sources} (expected 6939)")
    print(f"Total Target Mappings: {total_mappings} (expected 6940)")

    if total_sources != 6939:
        print(f"[FAIL] Expected exactly 6939 source entities, got {total_sources}")
        all_ok = False
    else:
        print("[PASS] Source entities count matches 6,939.")

    if total_mappings != 6940:
        print(f"[FAIL] Expected exactly 6940 target mappings, got {total_mappings}")
        all_ok = False
    else:
        print("[PASS] Target mappings count matches 6,940.")

    # 核算隔离项与合格入库数
    quarantined = [l for l in map_lines[1:] if "QUARANTINE" in l.upper() or "DEFERRED_ISSUE" in l.upper()]
    qualified_count = total_mappings - len(quarantined)

    print(f"Quarantined Mappings: {len(quarantined)} (expected 83)")
    print(f"Qualified Ingested Mappings: {qualified_count} (expected 6857)")

    if len(quarantined) != 83:
        print(f"[FAIL] Expected exactly 83 quarantined mappings, got {len(quarantined)}")
        all_ok = False
    else:
        print("[PASS] Quarantined mappings count matches 83.")

    if qualified_count != 6857:
        print(f"[FAIL] Expected exactly 6857 qualified mappings, got {qualified_count}")
        all_ok = False
    else:
        print("[PASS] Qualified mappings count matches 6,857.")

    # 查验 1 对 2 拆分映射的来源实体
    src_ids = [l.split("\t")[1] for l in map_lines[1:]]
    counts = Counter(src_ids)
    split_sources = [sid for sid, c in counts.items() if c > 1]
    print(f"1-to-2 Split Source Entity: {split_sources}")
    if split_sources != ["SRC_ACC_06897"]:
        print(f"[FAIL] Expected 1-to-2 split source to be ['SRC_ACC_06897'], got {split_sources}")
        all_ok = False
    else:
        print("[PASS] Confirmed exactly 1 source entity (SRC_ACC_06897) split into 2 target mappings.")

    return all_ok

if __name__ == "__main__":
    ok1 = verify_file_hashes()
    ok2 = verify_targeted_isolation()
    ok3 = verify_template_syntax_and_prompts()
    ok4 = run_500_seed_and_slot_verification()
    ok5 = verify_numerical_ledgers()
    if ok1 and ok2 and ok3 and ok4 and ok5:
        print("\n🎉 ALL REVISED RC10 INTEGRATION VERIFICATIONS PASSED SUCCESSFULLY!")
        sys.exit(0)
    else:
        print("\n❌ SOME VERIFICATIONS FAILED!")
        sys.exit(1)
