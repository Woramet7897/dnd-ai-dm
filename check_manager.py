"""
check_manager.py — Baldur's Gate 3 style interactive skill checks for D&D 5e AI DM.
Provides skill detection, difficulty mapping, and check card formatting.
"""

from typing import Dict, Any, Optional, Tuple, List
import re
import state_manager

# Mapping of standard D&D 5e skills to primary ability score, English & Thai names
SKILL_MAP: Dict[str, Dict[str, str]] = {
    "athletics": {"stat": "STR", "name": "Athletics", "thai": "การกีฬา/ปีนป่าย"},
    "acrobatics": {"stat": "DEX", "name": "Acrobatics", "thai": "กายกรรม/ทรงตัว"},
    "sleight_of_hand": {"stat": "DEX", "name": "Sleight of Hand", "thai": "มือไว/สะเดาะกลอน"},
    "stealth": {"stat": "DEX", "name": "Stealth", "thai": "ย่องเบา/ซ่อนตัว"},
    "arcana": {"stat": "INT", "name": "Arcana", "thai": "ศาสตร์เวทมนตร์"},
    "history": {"stat": "INT", "name": "History", "thai": "ประวัติศาสตร์"},
    "investigation": {"stat": "INT", "name": "Investigation", "thai": "สืบสวน/ค้นหาเบาะแส"},
    "nature": {"stat": "INT", "name": "Nature", "thai": "ธรรมชาติวิทยา"},
    "religion": {"stat": "INT", "name": "Religion", "thai": "ศาสนศาสตร์/พิธีกรรม"},
    "animal_handling": {"stat": "WIS", "name": "Animal Handling", "thai": "การฝึกสัตว์"},
    "insight": {"stat": "WIS", "name": "Insight", "thai": "หยั่งรู้/จับโกหก"},
    "medicine": {"stat": "WIS", "name": "Medicine", "thai": "การแพทย์/ปฐมพยาบาล"},
    "perception": {"stat": "WIS", "name": "Perception", "thai": "การสังเกตการณ์"},
    "survival": {"stat": "WIS", "name": "Survival", "thai": "การเอาชีวิตรอด/แกะรอย"},
    "deception": {"stat": "CHA", "name": "Deception", "thai": "การตบตา/โกหก"},
    "intimidation": {"stat": "CHA", "name": "Intimidation", "thai": "การข่มขู่/คุกคาม"},
    "performance": {"stat": "CHA", "name": "Performance", "thai": "การแสดง"},
    "persuasion": {"stat": "CHA", "name": "Persuasion", "thai": "การโน้มน้าว/เจรจา"},
}

# Aliases for skill names in Thai / English tags
TAG_ALIASES: Dict[str, str] = {
    "athletics": "athletics",
    "ปีนป่าย": "athletics",
    "กายกีฬา": "athletics",
    "acrobatics": "acrobatics",
    "กายกรรม": "acrobatics",
    "sleight of hand": "sleight_of_hand",
    "sleight_of_hand": "sleight_of_hand",
    "สะเดาะกลอน": "sleight_of_hand",
    "มือไว": "sleight_of_hand",
    "stealth": "stealth",
    "ย่องเบา": "stealth",
    "ซ่อนตัว": "stealth",
    "arcana": "arcana",
    "เวทมนตร์": "arcana",
    "history": "history",
    "ประวัติศาสตร์": "history",
    "investigation": "investigation",
    "สืบสวน": "investigation",
    "ค้นหา": "investigation",
    "nature": "nature",
    "ธรรมชาติ": "nature",
    "religion": "religion",
    "ศาสนา": "religion",
    "animal handling": "animal_handling",
    "animal_handling": "animal_handling",
    "คุมสัตว์": "animal_handling",
    "insight": "insight",
    "จับโกหก": "insight",
    "หยั่งรู้": "insight",
    "medicine": "medicine",
    "การแพทย์": "medicine",
    "ปฐมพยาบาล": "medicine",
    "perception": "perception",
    "สังเกต": "perception",
    "สังเกตการณ์": "perception",
    "survival": "survival",
    "แกะรอย": "survival",
    "ตามรอย": "survival",
    "เอาชีวิตรอด": "survival",
    "deception": "deception",
    "โกหก": "deception",
    "ตบตา": "deception",
    "intimidation": "intimidation",
    "ข่มขู่": "intimidation",
    "performance": "performance",
    "การแสดง": "performance",
    "persuasion": "persuasion",
    "เจรจา": "persuasion",
    "เกลี้ยกล่อม": "persuasion",
    "โน้มน้าว": "persuasion",
    # Class / Background Roleplay Dialogue Tags -> Mapped to signature D&D 5e social/knowledge skills
    "bard": "performance",
    "paladin": "persuasion",
    "cleric": "religion",
    "wizard": "arcana",
    "warlock": "intimidation",
    "barbarian": "intimidation",
    "rogue": "deception",
    "fighter": "athletics",
    "criminal": "deception",
    "noble": "persuasion",
    "sage": "history",
    "acolyte": "religion",
    "soldier": "intimidation",
    "folk hero": "persuasion",
}

# Regex keywords matching actions that should trigger a check
KEYWORD_RULES = [
    # Survival (WIS)
    (r"(?:แกะรอย|ตามรอย|ร่องรอย|สะกดรอย|ล่าสัตว์|หาน้ำ|หาอาหาร|เอาชีวิตรอด|\btrack\b|\bsurvival\b)", "survival", "medium"),
    # Perception (WIS)
    (r"(?:มองหา|สอดส่อง|ฟังเสียง|ตรวจตรา|สำรวจรอบ|\bperception\b|\blisten closely\b)", "perception", "medium"),
    # Investigation (INT)
    (r"(?:สืบหา|สืบสวน|ค้นหาเบาะแส|ค้นหาของซ่อน|ตรวจค้นห้อง|ชันสูตร|ตรวจสอบอย่างละเอียด|\binvestigat\w*\b)", "investigation", "medium"),
    # Stealth (DEX)
    (r"(?:ย่อง|แอบ|ซ่อนตัว|ลอบเข้า|หลบสายตา|ล่องหน|\bstealth\b|\bsneak\b|\bhide\b)", "stealth", "medium"),
    # Sleight of Hand (DEX)
    (r"(?:สะเดาะกลอน|ปลดล็อค|ล้วงกระเป๋า|ขโมย|สับเปลี่ยน|\bpick lock\b|\bsleight of hand\b|\bpocket\b)", "sleight_of_hand", "medium"),
    # Athletics (STR)
    (r"(?:ปีน|กระโดดข้าม|ว่ายน้ำทวน|พังประตู|งัดประตู|ผลักหิน|ยกรถ|\bathletics\b|\bclimb\b|\bjump across\b|\bforce open\b)", "athletics", "medium"),
    # Acrobatics (DEX)
    (r"(?:ทรงตัว|ตีลังกา|หลบกับดัก|\bacrobatic\b|\bacrobatics\b|\bbalance\b)", "acrobatics", "medium"),
    # Arcana (INT)
    (r"(?:ตรวจจับเวท|อ่านรูน|พลังเวท|วงแหวนเวท|ศิลาเวท|คัมภีร์เวท|\barcana\b|\bdetect magic\b)", "arcana", "medium"),
    # History (INT)
    (r"(?:ประวัติศาสตร์|ตราประจำตระกูล|อักษรโบราณ|ตำนานเก่าแก่|\bhistory\b)", "history", "medium"),
    # Nature (INT)
    (r"(?:พืชสมุนไพร|พิษพืช|\bnature\b|\bherbs\b)", "nature", "medium"),
    # Religion (INT)
    (r"(?:แท่นบูชา|บทสวด|สัญลักษณ์เทพ|รูปปั้นบูชา|\breligion\b|\baltar\b)", "religion", "medium"),
    # Insight (WIS)
    (r"(?:จับโกหก|จับพิรุธ|อ่านสีหน้า|สังเกตท่าที|สังเกตแววตา|อ่านใจ|หยั่งรู้|ดูว่าโกหก|\binsight\b|\bread motive\w*\b)", "insight", "medium"),
    # Medicine (WIS)
    (r"(?:รักษาแผล|ปฐมพยาบาล|ดูอาการบาดเจ็บ|ตรวจพิษ|\bmedicine\b|\bfirst aid\b)", "medicine", "medium"),
    # Animal Handling (WIS)
    (r"(?:ปลอบสัตว์|ลูบหัวสัตว์|ทำให้ม้าสงบ|ฝึกสัตว์|\banimal handling\b)", "animal_handling", "medium"),
    # Performance (CHA)
    (r"(?:ร้องเพลง|บรรเลงเพลง|ดีดพิณ|เป่าขลุ่ย|เล่าเรื่องตลก|แสดงละคร|ร่ายรำ|\bperform\w*\b|\bsing a song\b)", "performance", "medium"),
    # Persuasion (CHA)
    (r"(?:เกลี้ยกล่อม|หว่านล้อม|เจรจา|ขอร้อง|โน้มน้าว|โน้มน้าวใจ|ขอความเห็นใจ|ต่อรอง|ต่อรองราคา|ชักจูง|พูดให้ใจเย็น|ปลอบโยน|จีบ|เกี้ยวพาราสี|\bpersua\w*\b|\bnegotiate\b|\bconvince\b|\bcharm\b)", "persuasion", "medium"),
    # Deception (CHA)
    (r"(?:โกหก|ตบตา|หลอกว่า|หลอกล่อ|ปั้นน้ำเป็นตัว|เสแสร้ง|แกล้งทำเป็น|ปลอมตัว|อำพรางความจริง|บลัฟ|\bdeceiv\w*\b|\bdeception\b|\bbluff\b|\blie\b)", "deception", "medium"),
    # Intimidation (CHA)
    (r"(?:ข่มขู่|ขู่บังคับ|ขู่ให้กลัว|คุกคาม|ตะคอกใส่|ชักดาบขู่|บีบบังคับ|แผ่จิตสังหาร|ข่มขวัญ|\bintimidat\w*\b|\bthreaten\b)", "intimidation", "medium"),
]

# Exclusion regex for purely informational or everyday actions
EXCLUSION_RULES = [
    r"^(?:เดิน|มุ่งหน้า|ก้าว|เดินทาง)(?:ไป|ออก)?\s*(?:ทาง|ทิศ|กลับ|เข้า)?",
    r"^(?:ซื้อ|ขาย|ดูสินค้า)",
    r"^(?:พัก|นอน|กางเต็นท์)",
    r"^(?:ดื่ม|กิน)",
    r"^(?:พูดคุย|ทักทาย|ถามชื่อ|ถามข่าวสาร|ถามทาง)\b(?!.*(?:หว่านล้อม|เกลี้ยกล่อม|โน้มน้าว|ต่อรอง|ข่มขู่|ขู่|โกหก|หลอก|ตบตา|จับโกหก|จับพิรุธ|จีบ|ร้องเพลง))",
]


def detect_action_skill_check(
    action_text: str,
    player_state: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Detect if an action text requires an ability/skill check.
    Supports BG3-style bracketed tags (e.g. '[Survival] แกะรอยมอนสเตอร์')
    as well as natural language keywords in Thai and English.

    Returns dict with check metadata, or None if no check is needed.
    """
    if not action_text or not action_text.strip():
        return None

    raw_text = action_text.strip()

    # 1. Check for explicit bracket tag e.g. [Survival] or [แกะรอย]
    tag_match = re.match(r"^\[([A-Za-zก-๙_ ]+)\]\s*(.*)", raw_text)
    if tag_match:
        tag_key = tag_match.group(1).strip().lower()
        cleaned_action = tag_match.group(2).strip() or raw_text
        if tag_key in TAG_ALIASES:
            skill_id = TAG_ALIASES[tag_key]
            info = SKILL_MAP.get(skill_id, {"stat": "WIS", "name": skill_id.title(), "thai": skill_id})
            return _build_check_spec(skill_id, info, "medium", cleaned_action, player_state)
        if tag_key in {
            "fighter", "wizard", "rogue", "cleric", "bard", "paladin", "barbarian", "warlock",
            "folk hero", "folk_hero", "acolyte", "criminal", "noble", "sage", "soldier", "entertainer",
        }:
            return None

    # 2. Check prioritized covert / active intent keywords first (stealth)
    # Even if preceded by casual movement like "เดินไปแอบฟังที่ประตู" or rest like "พักอยู่หลังหินแล้วแอบดูยาม"
    stealth_pat = r"(?:ย่อง|แอบ|ซ่อนตัว|ลอบเข้า|หลบสายตา|ล่องหน|\bstealth\b|\bsneak\b|\bhide\b)"
    if re.search(stealth_pat, raw_text, re.IGNORECASE):
        info = SKILL_MAP.get("stealth", {"stat": "DEX", "name": "Stealth", "thai": "ย่องเบา/ซ่อนตัว"})
        return _build_check_spec("stealth", info, "medium", raw_text, player_state)

    # 3. Check exclusion patterns (casual dialogue, pure movement, shopping, resting)
    for ex_pat in EXCLUSION_RULES:
        if re.search(ex_pat, raw_text, re.IGNORECASE):
            return None

    # 4. Keyword inference loop for all other natural language actions
    for pat, skill_id, diff in KEYWORD_RULES:
        if skill_id != "stealth" and re.search(pat, raw_text, re.IGNORECASE):
            info = SKILL_MAP.get(skill_id, {"stat": "WIS", "name": skill_id.title(), "thai": skill_id})
            return _build_check_spec(skill_id, info, diff, raw_text, player_state)

    return None


def _build_check_spec(
    skill_id: str,
    info: Dict[str, str],
    difficulty: str,
    action_text: str,
    player_state: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Helper to assemble complete BG3 skill check specification."""
    stat = info["stat"]
    dc = state_manager.DIFFICULTY_TO_DC.get(difficulty, 13)

    stats = player_state.get("stats", {}) if isinstance(player_state, dict) else {}
    stat_val = stats.get(stat, 10)
    mod = state_manager.get_modifier(stat_val)

    is_prof = False
    if isinstance(player_state, dict):
        is_prof = state_manager.is_proficient(skill_id, player_state) or state_manager.is_proficient(info["name"], player_state)

    prof_bonus = player_state.get("proficiency_bonus", 2) if (is_prof and isinstance(player_state, dict)) else 0
    total_mod = mod + prof_bonus

    return {
        "skill": skill_id,
        "skill_name": info["name"],
        "skill_thai": info["thai"],
        "stat": stat,
        "difficulty": difficulty,
        "dc": dc,
        "stat_val": stat_val,
        "modifier": mod,
        "proficiency": prof_bonus,
        "is_proficient": is_prof,
        "total_modifier": total_mod,
        "action_text": action_text,
    }
