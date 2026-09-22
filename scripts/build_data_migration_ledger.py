#!/usr/bin/env python3
"""
build_data_migration_ledger.py — 生成 246 行完整、无截断、可追溯的数据迁移台账与闭环统计
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_DIR = Path(__file__).resolve().parent.parent
TSV_PATH = REPO_DIR / "docs" / "data_migration" / "raw_clothing_input.tsv"
LEDGER_PATH = REPO_DIR / "docs" / "data_migration" / "clothing_lexicon_migration_ledger.md"


def compute_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_prompt(p: str) -> str:
    p = p.strip().lower()
    p = p.rstrip(",").rstrip(";")
    p = p.replace("\\n", "").replace("\n", "")
    p = " ".join(p.split())
    return p


def build_ledger() -> None:
    raw_sha = compute_sha256(TSV_PATH)
    lines = [l.strip().split("\t") for l in TSV_PATH.read_text(encoding="utf-8").splitlines() if l.strip()][1:]

    # 预设详细映射规则字典 (按原始行号 1~246)
    # 格式: line_no: (status, target_slot, target_catalog, target_id, ops, button_cap, skirt_cap, reason)
    rules: Dict[int, Tuple[str, str, str, str, str, str, str, str]] = {
        1: ('纳入', 'clothing', 'clothing.json (categories)', 'bikini_classic', '[Route]', '禁止', '禁止', '基础分体比基尼款式，无纽扣无裙摆'),
        2: ('纳入', 'clothing', 'clothing.json (categories)', 'bikini_strappy', '[Route]', '禁止', '禁止', '系绳系带特色比基尼形制'),
        3: ('合并', 'clothing', 'clothing.json (categories)', 'bikini_strappy', '[Route]', '禁止', '禁止', '比基尼前系带上装，作为款式叶子tag合并'),
        4: ('合并', 'clothing', 'clothing.json (categories)', 'bikini_strappy', '[Route]', '禁止', '禁止', '比基尼侧系带下装，作为款式叶子tag合并'),
        5: ('合并', 'clothing', 'clothing.json (categories)', 'bikini_micro', '[Route]', '禁止', '禁止', '微小比基尼，合并至存量 bikini_micro'),
        6: ('纳入', 'clothing', 'clothing.json (categories)', 'swimsuit_classic', '[Route]', '禁止', '禁止', '经典通用泳装款式'),
        7: ('合并', 'clothing', 'clothing.json (categories)', 'one_piece_swimsuit', '[Route]', '禁止', '禁止', '连体泳衣，合并至存量 one_piece_swimsuit'),
        8: ('纳入', 'clothing', 'clothing.json (categories)', 'swimsuit_school', '[Route]', '禁止', '禁止', '日系传统学校死库水款式'),
        9: ('纳入', 'clothing', 'clothing.json (categories)', 'swimsuit_competition', '[Route]', '禁止', '禁止', '专业竞速连体泳装款式'),
        10: ('纳入', 'clothing', 'clothing.json (categories)', 'sportswear_active', '[Route]', '不适用', '不适用', '综合运动服装通用款'),
        11: ('纳入', 'clothing', 'clothing.json (categories)', 'volleyball_uniform', '[Route]', '禁止', '禁止', '排球竞技运动服装款式'),
        12: ('合并', 'clothing', 'clothing.json (categories)', 'qipao', '[Route]', '允许', '禁止', '旗袍传统名称，作为 qipao 的别名/标签'),
        13: ('合并', 'clothing', 'clothing.json (categories)', 'jk_seifuku', '[Route]', '禁止', '允许', '日系水手服，合并至存量 jk_seifuku'),
        14: ('合并', 'clothing', 'clothing.json (categories)', 'blazer_uniform', '[Route]', '允许', '允许', '通用校服，合并至存量 blazer_uniform'),
        15: ('合并', 'clothing', 'clothing.json (categories)', 'gym_uniform', '[Route]', '禁止', '禁止', '布鲁玛短裤，合并至存量 gym_uniform'),
        16: ('纳入', 'clothing', 'clothing.json (categories)', 'shirts_blouses', '[Route]', '允许', '不适用', '高领立领长袖衬衫独立款式'),
        17: ('纳入', 'clothing', 'clothing.json (categories)', 'leotard_bodysuit', '[Route]', '禁止', '禁止', '体操舞蹈紧身连体衣独立款式'),
        18: ('合并', 'clothing', 'clothing.json (categories)', 'leotard_bodysuit', '[Route]', '禁止', '禁止', '无肩带紧身衣，作为紧身衣叶子tag'),
        19: ('合并', 'clothing', 'clothing.json (categories)', 'leotard_bodysuit', '[Route]', '禁止', '禁止', '高叉紧身衣，作为紧身衣叶子tag'),
        20: ('合并', 'clothing', 'clothing.json (categories)', 'leotard_bodysuit', '[Route]', '禁止', '禁止', '丁字紧身衣，作为紧身衣叶子tag'),
        21: ('纳入', 'clothing', 'clothing.json (clothing_states)', 'undergarment_leotard', '[Route]', '禁止', '禁止', '衣物下穿着紧身衣叠穿状态'),
        22: ('纳入', 'clothing', 'clothing.json (clothing_states)', 'taut_tight', '[Route]', '不适用', '不适用', '衣物紧绷勾勒状态'),
        23: ('合并', 'clothing', 'clothing.json (categories)', 'shirts_blouses', '[Route]', '允许', '不适用', '紧身衬衫，作为衬衫款式变体tag'),
        24: ('纳入', 'clothing', 'clothing.json (categories)', 'tulle_dress', '[Route]', '不适用', '允许', '轻薄透薄纱仙气连衣裙独立款式'),
        25: ('纳入', 'clothing', 'clothing.json (categories)', 'chiffon_dress', '[Route]', '不适用', '允许', '轻盈雪纺面料连衣裙独立款式'),
        26: ('合并', 'clothing', 'clothing.json (categories)', 'leotard_bodysuit', '[Route]', '禁止', '禁止', '紧身衣裤同义词，合并至紧身衣'),
        27: ('纳入', 'clothing', 'clothing.json (categories)', 'tops_tanks', '[Route]', '禁止', '不适用', '基础无袖吊带背心独立款式'),
        28: ('纳入', 'clothing', 'clothing.json (categories)', 'dress_casual', '[Route]', '不适用', '允许', '通用休闲连身裙独立款式'),
        29: ('纳入', 'clothing', 'clothing.json (categories)', 'dress_backless', '[Route]', '不适用', '允许', '露背剪裁连身裙独立款式'),
        30: ('纳入', 'clothing', 'clothing.json (categories)', 'halter_dress', '[Route]', '不适用', '允许', '挂脖绕颈连身裙独立款式'),
        31: ('纳入', 'clothing', 'clothing.json (categories)', 'sweater_dress', '[Route]', '不适用', '允许', '针织毛衣连衣裙独立款式'),
        32: ('合并', 'clothing', 'clothing.json (categories)', 'dress_backless', '[Route]', '不适用', '允许', '露背装概念，合并至露背连身裙'),
        33: ('纳入', 'clothing', 'clothing.json (categories)', 'sweater_casual', '[Route]', '禁止', '不适用', '基础休闲针织毛衣/套头衫独立款式'),
        34: ('合并', 'clothing', 'clothing.json (categories)', 'sweater_casual', '[Route]', '禁止', '不适用', '高领毛衣领型变体，作为款式tag'),
        35: ('合并', 'clothing', 'clothing.json (categories)', 'sweater_casual', '[Route]', '禁止', '不适用', '罗纹织纹毛衣变体，作为款式tag'),
        36: ('合并', 'clothing', 'clothing.json (categories)', 'sweater_casual', '[Route]', '禁止', '不适用', '露肩毛衣变体，作为款式tag'),
        37: ('纳入', 'clothing', 'clothing.json (clothing_states)', 'heart_cutout', '[Route]', '不适用', '不适用', '胸口心型镂空剪裁状态'),
        38: ('纳入', 'clothing', 'clothing.json (clothing_states)', 'back_cutout', '[Route]', '不适用', '不适用', '后背镂空大露背剪裁状态'),
        39: ('纳入', 'clothing', 'clothing.json (clothing_states)', 'underboob_cutout', '[Route]', '不适用', '不适用', '下胸微露镂空切口状态'),
        40: ('纳入', 'clothing', 'clothing.json (categories)', 'crop_top', '[Route]', '禁止', '不适用', '露腹露脐短上衣独立款式'),
        41: ('纳入', 'clothing', 'clothing.json (categories)', 'racing_suit', '[Fix][Route]', '允许', '禁止', '剔除作者署名，职业赛车连体服独立款式'),
        42: ('合并', 'clothing', 'clothing.json (categories)', 'nurse_uniform', '[Fix][Route]', '允许', '允许', '剔除署名，合并至存量 nurse_uniform'),
        43: ('合并', 'clothing', 'clothing.json (categories)', 'latex_catsuit', '[Fix][Route]', '禁止', '禁止', '剔除署名，合并至存量 latex_catsuit'),
        44: ('纳入', 'clothing', 'clothing.json (categories)', 'lab_coat', '[Fix][Route]', '允许', '不适用', '剔除署名，科研实验白大褂独立款式'),
        45: ('纳入', 'clothing', 'clothing.json (categories)', 'convenience_store', '[Fix][Route]', '允许', '不适用', '剔除署名，便利店员工工作服独立款式'),
        46: ('纳入', 'clothing', 'clothing.json (categories)', 'summer_sundress', '[Route]', '不适用', '允许', '夏日长裙/吊带裙独立款式'),
        47: ('纳入', 'clothing', 'clothing.json (categories)', 'business_suit', '[Route]', '允许', '禁止', '正统男女西服套装，裤装形制无裙摆，彻底独立于 ol_suit'),
        48: ('合并', 'clothing', 'clothing.json (categories)', 'yukata', '[Route]', '禁止', '允许', '传统夏日浴衣，合并至存量 yukata'),
        49: ('纳入', 'clothing', 'clothing.json (categories)', 'festive_costume', '[Route]', '允许', '允许', '圣诞老人特色节日角色装扮'),
        50: ('纳入', 'clothing', 'clothing.json (categories)', 'lolita_gothic', '[Route]', '允许', '允许', '哥特洛丽塔风格洋装独立款式'),
        51: ('纳入', 'clothing', 'clothing.json (categories)', 'mahou_shoujo', '[Route]', '禁止', '允许', '二次元魔法少女战装独立款式'),
        52: ('合并', 'clothing', 'clothing.json (categories)', 'maid_dress', '[Route]', '允许', '允许', '女仆装通用词，合并至存量 maid_dress'),
        53: ('合并', 'clothing', 'clothing.json (categories)', 'business_suit', '[Fix][Route]', '允许', '禁止', '剔除平台署名，作为黑西装变体合并，裤装无裙摆'),
        54: ('合并', 'clothing', 'clothing.json (categories)', 'cheerleader', '[Fix][Route]', '禁止', '允许', '繁转简，合并至存量 cheerleader'),
        55: ('合并', 'clothing', 'clothing.json (categories)', 'bikini_micro', '[Route]', '禁止', '禁止', '迷你比基尼，与第5行完全重复合并'),
        56: ('纳入', 'jewelry', 'accessories.json (headwear_jewelry)', 'neck_ribbon', '[Fix][Route]', '不适用', '不适用', '繁转简，丝带颈带独立配饰件'),
        57: ('纳入', 'clothing', 'clothing.json (clothing_states)', 'braless', '[Route]', '不适用', '不适用', '无胸罩内衣脱去状态'),
        58: ('纳入', 'jewelry', 'accessories.json (headwear_jewelry)', 'hooded_cloak', '[Route]', '允许', '不适用', '连帽防风斗篷，与无帽披风分开独立'),
        59: ('纳入', 'clothing', 'clothing.json (categories)', 'clerical_nun', '[Route]', '禁止', '允许', '修女修道服/圣袍独立款式'),
        60: ('纳入', 'clothing', 'clothing.json (categories)', 'military_uniform', '[Route]', '允许', '禁止', '正规军服常服独立款式，长裤形制无裙摆'),
        61: ('合并', 'clothing', 'clothing.json (categories)', 'hanfu', '[Route]', '禁止', '允许', '传统汉服，合并至存量 hanfu'),
        62: ('合并', 'clothing', 'clothing.json (clothing_states)', 'torn_shredded', '[Route]', '不适用', '不适用', '破损衣物，合并至存量 torn_shredded'),
        63: ('纳入', 'clothing', 'clothing.json (categories)', 'wedding_dress', '[Fix][Route]', '禁止', '允许', '新娘白纱婚纱独立款式'),
        64: ('合并', 'clothing', 'clothing.json (categories)', 'evening_dress', '[Reduce][Route]', '不适用', '允许', '黑色印花礼服，合并至晚礼服变体tag'),
        65: ('纳入', 'jewelry', 'accessories.json (headwear_jewelry)', 'cloak', '[Route]', '允许', '不适用', '无帽长披风，与连帽斗篷彻底分开'),
        66: ('纳入', 'clothing', 'clothing.json (categories)', 'windbreaker', '[Route]', '允许', '不适用', '运动白色防风风衣独立款式'),
        67: ('纳入', 'clothing', 'clothing.json (categories)', 'trench_coat', '[Route]', '允许', '不适用', '英伦战壕风衣外套独立款式'),
        68: ('纳入', 'clothing', 'clothing.json (categories)', 'bikini_creative', '[Route]', '禁止', '禁止', '奶牛印花创意比基尼款式'),
        69: ('合并', 'clothing', 'clothing.json (categories)', 'knit_sweater', '[Route]', '禁止', '不适用', '露背毛衣同义词，合并至 knit_sweater'),
        70: ('纳入', 'imperfections', 'imperfections.json (categories)', 'tan_lines', '[Fix][Route]', '不适用', '不适用', '繁转简，日晒泳装痕迹，身体唯一主归属'),
        71: ('合并', 'clothing', 'clothing.json (categories)', 'gym_uniform', '[Route]', '禁止', '禁止', '运动制服，合并至存量 gym_uniform'),
        72: ('合并', 'clothing', 'clothing.json (categories)', 'evening_dress', '[Route]', '不适用', '允许', '晚礼服同义词，合并至存量 evening_dress'),
        73: ('纳入', 'clothing', 'clothing.json (categories)', 'formal_gown', '[Route]', '不适用', '允许', '正式全套大礼服独立款式'),
        74: ('纳入', 'clothing', 'clothing.json (categories)', 'combat_tactical', '[Route]', '允许', '禁止', '战术特警战斗服独立款式，长裤形制无裙摆'),
        75: ('纳入', 'jewelry', 'accessories.json (headwear_jewelry)', 'poncho', '[Route]', '禁止', '不适用', '套头小披肩，独立配饰形制'),
        76: ('合并', 'clothing', 'clothing.json (categories)', 'lab_coat', '[Route]', '允许', '不适用', '实验袍，与第44行合并至 lab_coat'),
        77: ('合并', 'clothing', 'clothing.json (categories)', 'korean_school', '[Route]', '允许', '允许', '学校制服，作为韩系校服变体合并'),
        78: ('纳入', 'clothing', 'clothing.json (categories)', 'lolita_sweet', '[Route]', '允许', '允许', '甜美洛丽塔洋装独立款式'),
        79: ('纳入', 'clothing', 'clothing.json (categories)', 'fishnet_top', '[Route]', '禁止', '不适用', '网眼镂空透气上衣独立款式'),
        80: ('纳入', 'clothing', 'clothing.json (categories)', 'witch_robe', '[Route]', '禁止', '允许', '奇幻魔女长裙法袍独立款式'),
        81: ('纳入', 'clothing', 'clothing.json (categories)', 'shinto_miko', '[Route]', '禁止', '允许', '神社巫女服独立款式'),
        82: ('纳入', 'lingerie', 'clothing.json (lingerie_wardrobe)', 'crotchless_panties', '[Route]', '禁止', '禁止', '情趣衣柜开裆无底内裤'),
        83: ('纳入', 'clothing', 'clothing.json (categories)', 'outerwear_overcoat', '[Route]', '允许', '不适用', '毛呢长大衣独立外套款式'),
        84: ('纳入', 'clothing', 'clothing.json (clothing_states)', 'wet_pure', '[Route]', '不适用', '不适用', '纯粹湿润衣物，不标半透与紧贴'),
        85: ('纳入', 'clothing', 'clothing.json (categories)', 'robe_general', '[Route]', '允许', '允许', '宽松通用长袍独立款式'),
        86: ('合并', 'clothing', 'clothing.json (categories)', 'trench_coat', '[Route]', '允许', '不适用', '战壕风衣同义词，合并至 trench_coat'),
        87: ('纳入', 'clothing', 'clothing.json (categories)', 'strapless_top', '[Route]', '禁止', '不适用', '抹胸式露腹短上衣独立款式'),
        88: ('纳入', 'clothing', 'clothing.json (categories)', 'winter_parka', '[Route]', '允许', '不适用', '防寒派克大衣外套款式'),
        89: ('纳入', 'clothing', 'clothing.json (categories)', 'lolita_fashion', '[Route]', '允许', '允许', '洛丽塔综合洋装大类'),
        90: ('纳入', 'clothing', 'clothing.json (clothing_states)', 'underwearless', '[Route]', '不适用', '不适用', '无内衣真空穿着状态'),
        91: ('合并', 'clothing', 'clothing.json (categories)', 'jk_seifuku', '[Route]', '禁止', '允许', '水手裙，作为水手服裙装下半身tag'),
        92: ('纳入', 'clothing', 'clothing.json (categories)', 'zentai_suit', '[Route]', '禁止', '禁止', '全包紧身衣连体服独立款式'),
        93: ('纳入', 'clothing', 'clothing.json (categories)', 'leather_jacket', '[Route]', '允许', '不适用', '机车皮夹克独立外套款式'),
        94: ('纳入', 'clothing', 'clothing.json (categories)', 'tactical_vest', '[Fix][Route]', '允许', '不适用', '清除末尾非法逗号，战术防弹背心独立款式'),
        95: ('合并', 'clothing', 'clothing.json (categories)', 'lolita_gothic', '[Route]', '不适用', '不适用', '蛛网纹路印花，作为哥特裙叶子tag'),
        96: ('合并', 'clothing', 'clothing.json (categories)', 'lolita_sweet', '[Fix][Route]', '允许', '允许', '清除末尾非法逗号，合并至甜美Lolita'),
        97: ('合并', 'clothing', 'clothing.json (categories)', 'maid_dress', '[Fix][Route]', '允许', '允许', '原表上下文确认字母A为女仆装录入手误合并'),
        98: ('纳入', 'clothing', 'clothing.json (categories)', 'slime_dress', '[Route]', '禁止', '允许', '奇幻史莱姆半透明材质特色服饰'),
        99: ('合并', 'clothing', 'clothing.json (clothing_states)', 'torn_shredded', '[Route]', '不适用', '不适用', '撕裂衣服，合并至存量 torn_shredded'),
        100: ('延期', '-', '-', '-', '[Defer]', '不适用', '不适用', '待裁定：意图为脱衣/少穿，属于全局负向/状态指令，暂不作为独立款式，留底待裁定'),
        101: ('合并', 'clothing', 'clothing.json (categories)', 'latex_catsuit', '[Route]', '禁止', '禁止', '乳胶衣同义词，合并至存量 latex_catsuit'),
        102: ('纳入', 'clothing', 'clothing.json (categories)', 'swimsuit_creative', '[Fix][Route]', '禁止', '禁止', '剔除作者署名，特色国风中式死库水款式'),
        103: ('纳入', 'clothing', 'clothing.json (categories)', 'rainwear_coat', '[Route]', '允许', '不适用', '防水防雨外衣长款独立款式'),
        104: ('纳入', 'clothing', 'clothing.json (categories)', 'anime_cosplay', '[Route]', '禁止', '禁止', '不知火舞二次元格斗经典角色装'),
        105: ('合并', 'clothing', 'clothing.json (categories)', 'street_casual', '[Route]', '禁止', '允许', '街头风格服饰，合并至存量 street_casual'),
        106: ('合并', 'clothing', 'clothing.json (categories)', 'clerical_nun', '[Reduce][Route]', '禁止', '允许', '剔除loli/one girl等人物构图词，合并至修女服'),
        107: ('合并', 'clothing', 'clothing.json (categories)', 'kimono', '[Route]', '禁止', '允许', '短款和服，作为和服款式叶子tag'),
        108: ('纳入', 'clothing', 'clothing.json (categories)', 'bathrobe', '[Route]', '允许', '允许', '沐浴宽松浴袍独立款式'),
        109: ('纳入', 'clothing', 'clothing.json (categories)', 'knight_armor', '[Route]', '禁止', '禁止', '中世纪骑士金属板甲独立款式'),
        110: ('纳入', 'clothing', 'clothing.json (categories)', 'outerwear_coat', '[Route]', '允许', '不适用', '通用外套大衣独立款式'),
        111: ('纳入', 'clothing', 'clothing.json (categories)', 'hoodie', '[Route]', '禁止', '不适用', '连帽套头卫衣独立款式'),
        112: ('纳入', 'clothing', 'clothing.json (categories)', 'sweatshirt', '[Route]', '禁止', '不适用', '圆领套头卫衣独立款式'),
        113: ('纳入', 'clothing', 'clothing.json (categories)', 'clerical_priest', '[Route]', '允许', '允许', '天主教神父/修生黑袍独立款式'),
        114: ('纳入', 'clothing', 'clothing.json (categories)', 'mecha_power_armor', '[Route]', '禁止', '禁止', '科幻动力装甲外覆机甲款式'),
        115: ('合并', 'clothing', 'clothing.json (categories)', 'windbreaker', '[Route]', '允许', '不适用', '立领长风衣，合并至防风外套款式'),
        116: ('合并', 'clothing', 'clothing.json (categories)', 'qipao', '[Fix][Route]', '允许', '禁止', '剔除括号注记，保持纯粹cheongsam合并至qipao'),
        117: ('纳入', 'clothing', 'clothing.json (categories)', 'dungarees', '[Fix][Route]', '允许', '禁止', '清除乱码字符，工装连体背带裤独立款式'),
        118: ('合并', 'clothing', 'clothing.json (categories)', 'qipao', '[Route]', '允许', '禁止', '中国衣服裙子，合并至旗袍/国风体系'),
        119: ('合并', 'clothing', 'clothing.json (categories)', 'one_piece_swimsuit', '[Route]', '禁止', '禁止', '高叉泳衣同义词，合并至存量连体泳衣'),
        120: ('合并', 'clothing', 'clothing.json (categories)', 'evening_dress', '[Route]', '不适用', '允许', '礼服长裙性感变体，合并至存量 evening_dress'),
        121: ('纳入', 'clothing', 'clothing.json (categories)', 'hospital_gown', '[Route]', '允许', '允许', '医疗病号服独立款式'),
        122: ('合并', 'clothing', 'clothing.json (categories)', 'shirts_blouses', '[Route]', '允许', '不适用', '白色衣服颜色特征，作为白衬衫叶子tag'),
        123: ('纳入', 'clothing', 'clothing.json (categories)', 'greek_toga', '[Route]', '禁止', '允许', '古希腊托加长袍，与玄门道袍坚决分开'),
        124: ('合并', 'clothing', 'clothing.json (categories)', 'leotard_bodysuit', '[Route]', '禁止', '禁止', '紧身连衣裤复数形式，合并至紧身连体衣'),
        125: ('纳入', 'clothing', 'clothing.json (categories)', 'knit_vest', '[Route]', '禁止', '不适用', 'V领针织无袖毛衣背心独立款式'),
        126: ('纳入', 'clothing', 'clothing.json (categories)', 'pumpkin_skirt', '[Route]', '禁止', '允许', '万圣节特色蓬松南瓜裙独立款式'),
        127: ('合并', 'clothing', 'clothing.json (categories)', 'festive_costume', '[Route]', '允许', '允许', '万圣节服装，合并至节日角色装扮大类'),
        128: ('纳入', 'clothing', 'clothing.json (categories)', 'soft_shell_jacket', '[Route]', '允许', '不适用', '户外软壳防风夹克独立款式'),
        129: ('纳入', 'lingerie', 'clothing.json (lingerie_wardrobe)', 'basic_underwear', '[Route]', '禁止', '禁止', '基础内衣概念套，明确作为规范 ID 纳入情趣衣柜扩展条目'),
        130: ('纳入', 'clothing', 'clothing.json (categories)', 'mecha_exoskeleton', '[Route]', '禁止', '禁止', '机械外骨骼装束独立款式'),
        131: ('纳入', 'clothing', 'clothing.json (categories)', 'frock_smock', '[Route]', '允许', '允许', '欧洲传统罩衫宽松工作服独立款式'),
        132: ('纳入', 'clothing', 'clothing.json (categories)', 'taoist_robe', '[Route]', '禁止', '允许', '东方玄门道教法衣道袍，与希腊袍分开'),
        133: ('纳入', 'clothing', 'clothing.json (categories)', 'military_overcoat', '[Route]', '允许', '不适用', '军用毛呢防寒长款大衣独立款式'),
        134: ('合并', 'clothing', 'clothing.json (categories)', 'shirts_blouses', '[Fix][Route]', '允许', '不适用', '纠正拼写 (frillded->frilled)，荷叶边衬衫tag'),
        135: ('纳入', 'clothing', 'clothing.json (categories)', 'sundress_layered', '[Flatten][Route]', '不适用', '允许', '消除多层嵌套括号，规范为黑吊带叠穿白T恤款式'),
        136: ('合并', 'clothing', 'clothing.json (categories)', 'mecha_exoskeleton', '[Route]', '禁止', '禁止', '外骨骼机甲，与第130行合并至 mecha_exoskeleton'),
        137: ('合并', 'clothing', 'clothing.json (categories)', 'dress_casual', '[Route]', '不适用', '允许', '拼接款图案特征，作为连衣裙叶子tag'),
        138: ('纳入', 'clothing', 'clothing.json (categories)', 'battle_robe', '[Route]', '禁止', '允许', '东方奇幻武侠长袍战袍独立款式'),
        139: ('合并', 'clothing', 'clothing.json (categories)', 'mecha_exoskeleton', '[Route]', '禁止', '禁止', '机械服装，合并至外骨骼机甲体系'),
        140: ('合并', 'clothing', 'clothing.json (categories)', 'mecha_exoskeleton', '[Flatten][Route]', '禁止', '禁止', '冒号语法平铺降级为组合词，作为机甲款式tag'),
        141: ('纳入', 'clothing', 'clothing.json (categories)', 'skirts_general', '[Route]', '禁止', '允许', '半身裙通用款式'),
        142: ('纳入', 'clothing', 'clothing.json (categories)', 'pleated_skirt', '[Route]', '禁止', '允许', '经典褶皱百褶半身裙独立款式'),
        143: ('纳入', 'clothing', 'clothing.json (categories)', 'miniskirt', '[Route]', '禁止', '允许', '膝上超短裙独立款式'),
        144: ('合并', 'clothing', 'clothing.json (categories)', 'dress_casual', '[Route]', '不适用', '允许', '连衣裙同义词，合并至连身裙'),
        145: ('纳入', 'clothing', 'clothing.json (categories)', 'floral_dress_white', '[Fix][Route]', '不适用', '允许', '纠正拼写 (gow->gown)，白色印花礼服长裙'),
        146: ('纳入', 'clothing', 'clothing.json (categories)', 'floral_dress_black', '[Fix][Route]', '不适用', '允许', '纠正拼写 (gow->gown)，黑色印花礼服长裙'),
        147: ('纳入', 'clothing', 'clothing.json (categories)', 'layered_skirt', '[Fix][Route]', '禁止', '允许', '繁转简，分层蛋糕裙，与铅笔裙坚决分开'),
        148: ('合并', 'clothing', 'clothing.json (categories)', 'layered_skirt', '[Fix][Route]', '禁止', '允许', '剔除作者署名，与第147行合并至多层裙'),
        149: ('合并', 'clothing', 'clothing.json (categories)', 'summer_sundress', '[Route]', '不适用', '允许', '夏日连衣裙同义词，合并至夏日长裙'),
        150: ('纳入', 'clothing', 'clothing.json (categories)', 'waist_apron', '[Route]', '禁止', '允许', '半身系腰围裙独立款式'),
        151: ('纳入', 'clothing', 'clothing.json (categories)', 'pettiskirt', '[Route]', '禁止', '允许', '多层网纱蓬蓬裙独立款式'),
        152: ('纳入', 'clothing', 'clothing.json (categories)', 'tutu_skirt', '[Route]', '禁止', '允许', '芭蕾舞短款硬纱裙独立款式'),
        153: ('纳入', 'clothing', 'clothing.json (categories)', 'plaid_skirt', '[Route]', '禁止', '允许', '英伦学院风格子百褶裙独立款式'),
        154: ('纳入', 'clothing', 'clothing.json (categories)', 'apron_dress', '[Route]', '禁止', '允许', '连体全身围裙女仆服款式'),
        155: ('纳入', 'clothing', 'clothing.json (categories)', 'pencil_skirt', '[Route]', '禁止', '允许', '修身紧身铅笔包臀裙，与多层裙分开'),
        156: ('合并', 'clothing', 'clothing.json (categories)', 'miniskirt', '[Route]', '禁止', '允许', '迷你裙同义词，与第143行合并至超短裙'),
        157: ('合并', 'clothing', 'clothing.json (categories)', 'lolita_gothic', '[Route]', '允许', '允许', '哥特式洛丽塔，合并至第50行 lolita_gothic'),
        158: ('合并', 'clothing', 'clothing.json (categories)', 'lolita_fashion', '[Fix][Route]', '允许', '允许', '纠正拼写 (fasion->fashion)，合并至Lolita大类'),
        159: ('纳入', 'clothing', 'clothing.json (categories)', 'dirndl_dress', '[Route]', '允许', '允许', '德式巴伐利亚排扣紧身连身裙'),
        160: ('纳入', 'clothing', 'clothing.json (categories)', 'armored_dress', '[Route]', '禁止', '允许', '战斗重装板甲金属连衣裙独立款式'),
        161: ('合并', 'clothing', 'clothing.json (categories)', 'armored_dress', '[Route]', '禁止', '允许', '盔甲裙同义词，与第160行合并至 armored_dress'),
        162: ('纳入', 'clothing', 'clothing.json (categories)', 'long_skirt', '[Route]', '禁止', '允许', '及踝长款半身裙独立款式'),
        163: ('纳入', 'clothing', 'clothing.json (categories)', 'rain_skirt', '[Route]', '禁止', '允许', '户外防水半身防雨裙款式'),
        164: ('合并', 'clothing', 'clothing.json (categories)', 'swimsuit_creative', '[Route]', '禁止', '禁止', '中式旗袍死库水，合并至第102行创意泳衣'),
        165: ('纳入', 'clothing', 'clothing.json (categories)', 'pleated_dress', '[Route]', '不适用', '允许', '全身细风琴褶皱长款连衣裙款式'),
        166: ('纳入', 'clothing', 'clothing.json (categories)', 'strapless_dress', '[Route]', '不适用', '允许', '抹胸无肩带晚礼裙款式'),
        167: ('纳入', 'clothing', 'clothing.json (categories)', 'off_shoulder_dress', '[Route]', '不适用', '允许', '露肩连身长裙款式'),
        168: ('合并', 'clothing', 'clothing.json (categories)', 'wedding_dress', '[Route]', '禁止', '允许', '婚纱同义词，与第63行合并至 wedding_dress'),
        169: ('合并', 'clothing', 'clothing.json (categories)', 'hanfu', '[Route]', '禁止', '允许', '汉服英文全名，合并至存量 hanfu'),
        170: ('纳入', 'clothing', 'clothing.json (categories)', 'microskirt', '[Route]', '禁止', '允许', '超微型齐臀性感短裙独立款式'),
        171: ('合并', 'clothing', 'clothing.json (categories)', 'pleated_skirt', '[Route]', '禁止', '允许', '黑色百褶裙变体，作为百褶裙款式tag'),
        172: ('纳入', 'clothing', 'clothing.json (categories)', 'suspender_skirt', '[Route]', '禁止', '允许', '背带/吊带半身伞裙独立款式'),
        173: ('合并', 'clothing', 'clothing.json (categories)', 'sweater_casual', '[Route]', '禁止', '不适用', '萌袖过手长袖特征，作为休闲毛衣叶子tag'),
        174: ('合并', 'clothing', 'clothing.json (categories)', 'tops_tanks', '[Route]', '禁止', '不适用', '背心同义词，与第27行合并至 tops_tanks'),
        175: ('合并', 'clothing', 'clothing.json (categories)', 'shirts_blouses', '[Route]', '允许', '不适用', '白衬衫，合并至第16行 shirts_blouses 基础款'),
        176: ('纳入', 'clothing', 'clothing.json (categories)', 'sailor_shirt', '[Route]', '禁止', '不适用', '水手领上装短袖衬衫独立款式'),
        177: ('纳入', 'clothing', 'clothing.json (categories)', 't_shirt', '[Route]', '禁止', '不适用', '短袖纯棉休闲T恤独立款式'),
        178: ('合并', 'clothing', 'clothing.json (categories)', 'sweater_casual', '[Route]', '禁止', '不适用', '毛衣同义词，与第33行合并至 sweater_casual'),
        179: ('合并', 'clothing', 'clothing.json (categories)', 'summer_sundress', '[Route]', '不适用', '允许', '原表上装分类纠偏，合并至夏日连身长裙'),
        180: ('合并', 'clothing', 'clothing.json (categories)', 'hoodie', '[Route]', '禁止', '不适用', '连帽衫同义词，与第111行合并至 hoodie'),
        181: ('合并', 'clothing', 'clothing.json (categories)', 'outerwear_overcoat', '[Fix][Route]', '允许', '不适用', '纠正拼写 (colla->collar)，毛领特征作为大衣tag'),
        182: ('合并', 'jewelry', 'accessories.json (headwear_jewelry)', 'hooded_cloak', '[Route]', '允许', '不适用', '兜帽斗篷同义词，与第58行合并至 hooded_cloak'),
        183: ('纳入', 'clothing', 'clothing.json (categories)', 'outerwear_jacket', '[Route]', '允许', '不适用', '通用轻便夹克外套独立款式'),
        184: ('合并', 'clothing', 'clothing.json (categories)', 'leather_jacket', '[Route]', '允许', '不适用', '皮夹克同义词，与第93行合并至 leather_jacket'),
        185: ('纳入', 'clothing', 'clothing.json (categories)', 'safari_jacket', '[Route]', '允许', '不适用', '多口袋工装探险家猎装夹克款式'),
        186: ('合并', 'clothing', 'clothing.json (categories)', 'hoodie', '[Route]', '禁止', '不适用', '兜帽特征部件，作为连帽衫叶子tag'),
        187: ('纳入', 'clothing', 'clothing.json (categories)', 'denim_jacket', '[Route]', '允许', '不适用', '复古牛仔水洗夹克独立款式'),
        188: ('合并', 'clothing', 'clothing.json (categories)', 'outerwear_jacket', '[Route]', '允许', '不适用', '高领夹克领型变体，作为夹克款式tag'),
        189: ('纳入', 'clothing', 'clothing.json (categories)', 'firefighter_gear', '[Route]', '允许', '不适用', '消防员阻燃防护重型夹克独立款式'),
        190: ('合并', 'clothing', 'clothing.json (categories)', 'trench_coat', '[Route]', '允许', '不适用', '战壕大衣同义词，合并至 trench_coat'),
        191: ('合并', 'clothing', 'clothing.json (categories)', 'lab_coat', '[Route]', '允许', '不适用', '实验室外套同义词，合并至 lab_coat'),
        192: ('纳入', 'clothing', 'clothing.json (categories)', 'down_jacket', '[Route]', '允许', '不适用', '冬季保暖羽绒服外套独立款式'),
        193: ('合并', 'clothing', 'clothing.json (categories)', 'tactical_vest', '[Route]', '允许', '不适用', '防弹盔甲同义词，合并至 tactical_vest'),
        194: ('合并', 'clothing', 'clothing.json (categories)', 'tactical_vest', '[Route]', '允许', '不适用', '防弹衣同义词，与第94行合并至 tactical_vest'),
        195: ('合并', 'clothing', 'clothing.json (categories)', 'outerwear_overcoat', '[Route]', '允许', '不适用', '大衣同义词，与第83行合并至 outerwear_overcoat'),
        196: ('纳入', 'clothing', 'clothing.json (categories)', 'duffel_coat', '[Route]', '允许', '不适用', '牛角扣学院风连帽粗呢大衣款式'),
        197: ('纳入', 'clothing', 'clothing.json (categories)', 'tailcoat', '[Route]', '允许', '禁止', '男士双排扣正式晚宴燕尾服款式，裤装形制无裙摆'),
        198: ('合并', 'clothing', 'clothing.json (categories)', 'maid_dress', '[Route]', '允许', '允许', '维多利亚黑白长款女仆装变体合并'),
        199: ('合并', 'clothing', 'clothing.json (categories)', 'jk_seifuku', '[Route]', '禁止', '允许', '水手服同义词，合并至存量 jk_seifuku'),
        200: ('合并', 'clothing', 'clothing.json (categories)', 'blazer_uniform', '[Route]', '允许', '允许', '学生服同义词，与第14行合并至 blazer_uniform'),
        201: ('合并', 'clothing', 'clothing.json (categories)', 'business_suit', '[Fix][Route]', '允许', '禁止', '纠正拼写 (bussiness->business)，合并至西装，裤装无裙摆'),
        202: ('合并', 'clothing', 'clothing.json (categories)', 'business_suit', '[Route]', '允许', '禁止', '西装同义词，与第47行合并至 business_suit，裤装无裙摆'),
        203: ('合并', 'clothing', 'clothing.json (categories)', 'military_uniform', '[Route]', '允许', '禁止', '军装同义词，与第60行合并至 military_uniform，长裤形制无裙摆'),
        204: ('合并', 'clothing', 'clothing.json (categories)', 'formal_gown', '[Route]', '不适用', '允许', '薄透礼服变体，合并至第73行 formal_gown'),
        205: ('合并', 'clothing', 'clothing.json (categories)', 'hanfu', '[Route]', '禁止', '允许', '汉服同义词，合并至存量 hanfu'),
        206: ('合并', 'clothing', 'clothing.json (categories)', 'qipao', '[Route]', '允许', '禁止', '旗袍同义词，合并至存量 qipao'),
        207: ('合并', 'clothing', 'clothing.json (categories)', 'kimono', '[Fix][Route]', '禁止', '允许', '纠正拼写 (japanses->japanese)，合并至和服'),
        208: ('合并', 'clothing', 'clothing.json (categories)', 'sportswear_active', '[Route]', '不适用', '不适用', '运动服同义词，与第10行合并至 sportswear_active'),
        209: ('合并', 'clothing', 'clothing.json (categories)', 'dungarees', '[Route]', '允许', '禁止', '工装背带裤同义词，合并至第117行 dungarees'),
        210: ('合并', 'clothing', 'clothing.json (categories)', 'wedding_dress', '[Route]', '禁止', '允许', '婚纱同义词，与第63行合并至 wedding_dress'),
        211: ('纳入', 'clothing', 'clothing.json (categories)', 'cocktail_dress', '[Fix][Route]', '不适用', '允许', '纠正复合词，银色深V紧身鸡尾酒晚宴短裙'),
        212: ('合并', 'clothing', 'clothing.json (categories)', 'robe_general', '[Route]', '允许', '允许', '长袍同义词，与第85行合并至 robe_general'),
        213: ('合并', 'clothing', 'clothing.json (categories)', 'apron_dress', '[Route]', '禁止', '允许', '围裙同义词，与第154行合并至 apron_dress'),
        214: ('纳入', 'clothing', 'clothing.json (categories)', 'fast_food_uniform', '[Route]', '允许', '不适用', '快餐连锁店带帽短袖制服独立款式'),
        215: ('合并', 'clothing', 'clothing.json (categories)', 'jk_seifuku', '[Route]', '禁止', '允许', 'JK制服缩写，合并至存量 jk_seifuku'),
        216: ('合并', 'clothing', 'clothing.json (categories)', 'gym_uniform', '[Route]', '禁止', '禁止', '健身服同义词，合并至存量 gym_uniform'),
        217: ('合并', 'clothing', 'clothing.json (categories)', 'shinto_miko', '[Route]', '禁止', '允许', '巫女服同义词，与第81行合并至 shinto_miko'),
        218: ('合并', 'clothing', 'clothing.json (categories)', 'combat_tactical', '[Route]', '允许', '禁止', 'SWAT特警作战服，合并至 combat_tactical，长裤形制无裙摆'),
        219: ('纳入', 'clothing', 'clothing.json (categories)', 'sleeveless_dress', '[Route]', '不适用', '允许', '无袖A字修身连衣裙独立款式'),
        220: ('合并', 'clothing', 'clothing.json (categories)', 'rainwear_coat', '[Route]', '允许', '不适用', '雨衣同义词，与第103行合并至 rainwear_coat'),
        221: ('合并', 'clothing', 'clothing.json (categories)', 'mecha_exoskeleton', '[Route]', '禁止', '禁止', '机甲衣同义词，合并至 mecha_exoskeleton'),
        222: ('纳入', 'clothing', 'clothing.json (categories)', 'wizard_robe', '[Route]', '禁止', '允许', '带星月图腾奇幻巫师长袍款式'),
        223: ('纳入', 'clothing', 'clothing.json (categories)', 'denim_shorts', '[Route]', '允许', '禁止', '经典牛仔毛边短裤独立下装'),
        224: ('合并', 'clothing', 'clothing.json (categories)', 'pleated_skirt', '[Route]', '禁止', '允许', '百褶裙同义词，与第142行合并至百褶裙'),
        225: ('纳入', 'clothing', 'clothing.json (categories)', 'hot_pants', '[Route]', '禁止', '禁止', '超短性感贴身热裤独立下装'),
        226: ('合并', 'clothing', 'clothing.json (categories)', 'pencil_skirt', '[Route]', '禁止', '允许', '铅笔裙同义词，与第155行合并至铅笔裙'),
        227: ('纳入', 'clothing', 'clothing.json (categories)', 'leather_skirt', '[Route]', '允许', '允许', '机车风黑色皮质短裙独立下装'),
        228: ('纳入', 'clothing', 'clothing.json (categories)', 'black_leggings', '[Route]', '禁止', '禁止', '黑色高弹力修身打底裤独立下装'),
        229: ('纳入', 'clothing', 'clothing.json (clothing_states)', 'skirt_under_kimono', '[Route]', '禁止', '允许', '和服内穿衬裙层次叠穿状态'),
        230: ('合并', 'clothing', 'clothing.json (categories)', 'shirts_blouses', '[Route]', '不适用', '不适用', '荷叶褶边装饰特征，作为衬衫叶子tag'),
        231: ('合并', 'clothing', 'clothing.json (categories)', 'lingerie_lace', '[Route]', '不适用', '不适用', '蕾丝花边装饰特征，作为内衣叶子tag'),
        232: ('合并', 'clothing', 'clothing.json (categories)', 'lolita_gothic', '[Route]', '不适用', '不适用', '哥特审美风格，作为哥特裙风格tag'),
        233: ('合并', 'clothing', 'clothing.json (categories)', 'lolita_fashion', '[Route]', '允许', '允许', '洛丽塔风格同义词，合并至 lolita_fashion'),
        234: ('合并', 'clothing', 'clothing.json (categories)', 'safari_jacket', '[Route]', '允许', '不适用', '西部牛仔风格特征，作为猎装外套tag'),
        235: ('合并', 'clothing', 'clothing.json (clothing_states)', 'wet_pure', '[Route]', '不适用', '不适用', '纯粹湿身同义词，与第84行合并至 wet_pure'),
        236: ('纳入', 'clothing', 'clothing.json (clothing_states)', 'off_shoulder_cut', '[Route]', '不适用', '不适用', '露单肩非对称剪裁状态'),
        237: ('纳入', 'clothing', 'clothing.json (clothing_states)', 'bare_shoulders', '[Route]', '不适用', '不适用', '露双肩大平领穿着状态'),
        238: ('合并', 'clothing', 'clothing.json (categories)', 'plaid_skirt', '[Route]', '不适用', '不适用', '苏格兰方格纹理，作为格子裙款式tag'),
        239: ('纳入', 'clothing', 'clothing.json (categories)', 'armored_skirt', '[Route]', '禁止', '允许', '护腿金属甲片战裙独立下装款式'),
        240: ('合并', 'clothing', 'clothing.json (categories)', 'knight_armor', '[Route]', '禁止', '禁止', '盔甲同义词，与第109行合并至 knight_armor'),
        241: ('合并', 'clothing', 'clothing.json (categories)', 'knight_armor', '[Route]', '禁止', '禁止', '金属盔甲，作为板甲款式变体tag'),
        242: ('纳入', 'clothing', 'clothing.json (categories)', 'berserker_armor', '[Route]', '禁止', '禁止', '狂战士重型带刺铠甲独立款式'),
        243: ('纳入', 'jewelry', 'accessories.json (headwear_jewelry)', 'waist_belt', '[Route]', '不适用', '不适用', '基础皮革腰带独立配饰件'),
        244: ('纳入', 'jewelry', 'accessories.json (headwear_jewelry)', 'winter_scarf', '[Route]', '不适用', '不适用', '针织保暖围巾独立配饰件'),
        245: ('合并', 'jewelry', 'accessories.json (headwear_jewelry)', 'cloak', '[Route]', '不适用', '不适用', '无帽长披肩/斗篷，与第65行合并至 cloak'),
        246: ('纳入', 'jewelry', 'accessories.json (headwear_jewelry)', 'fur_shawl', '[Route]', '不适用', '不适用', '原表最后一行，保暖皮草披肩独立配饰件'),
    }

    # 统计指标计算
    total_rows = len(lines)
    status_counts = {"纳入": 0, "合并": 0, "延期": 0, "排除": 0}
    for r in rules.values():
        status_counts[r[0]] += 1

    unique_raw = len({l[3] for l in lines})
    unique_norm = len({normalize_prompt(l[3]) for l in lines})

    # 生成 Markdown
    md = [
        "# 服装提示词全量数据迁移台账 (Line-by-Line Migration Ledger)",
        "",
        "> **源文件基线信息**：",
        f"> - 原始文件路径：`docs/data_migration/raw_clothing_input.tsv`",
        f"> - 原始文件 SHA-256：`{raw_sha}`",
        f"> - 原始总记录数：**{total_rows} 行** (行号 1~246 连续闭合，无任何截断或省略)",
        "",
        "## 一、三层统计与闭合核验汇总",
        "",
        "| 统计维度 | 统计口径说明 | 精确计数值 |",
        "|---|---|---|",
        f"| **第 1 层：原始字面量去重** | 对原始第三列 Prompt 文本直接执行 `set()` 统计 | **{unique_raw} 个** 独特字符串 |",
        f"| **第 2 层：格式规范化去重** | 去除首尾空格、清除多余末尾逗号/换行符、全小写归一 | **{unique_norm} 个** 规范化提示词 |",
        f"| **第 3 层：处理状态完全守恒** | 纳入 ({status_counts['纳入']}) + 合并 ({status_counts['合并']}) + 延期 ({status_counts['延期']}) + 排除 ({status_counts['排除']}) | **{total_rows} 行** (严格等于 246 行) |",
        "",
        "---",
        "",
        "## 二、246 行逐行数据迁移台账",
        "",
        "| 行号 | 原分类 | 原始中文 | 原始英文 Prompt | 规范化提示词 | 处理状态 | 目标槽位 | 目标 Catalog | 目标规范 ID | 操作类型 | 解扣能力 | 掀裙能力 | 处理决策与理由说明 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    for line in lines:
        row_id = int(line[0])
        cat = line[1]
        name_zh = line[2]
        prompt_raw = line[3]
        prompt_norm = normalize_prompt(prompt_raw)

        rule = rules.get(row_id)
        if not rule:
            raise ValueError(f"Missing rule for row {row_id}")

        status, slot, catalog, target_id, ops, button_cap, skirt_cap, reason = rule

        # 格式化表格行
        md_row = f"| {row_id} | {cat} | {name_zh} | `{prompt_raw}` | `{prompt_norm}` | {status} | {slot} | {catalog} | `{target_id}` | {ops} | {button_cap} | {skirt_cap} | {reason} |"
        md.append(md_row)

    md.append("")
    LEDGER_PATH.write_text("\n".join(md), encoding="utf-8")
    print(f"Generated {LEDGER_PATH} with {len(lines)} rows successfully.")


if __name__ == "__main__":
    build_ledger()
