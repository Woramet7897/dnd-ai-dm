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
}

# Regex keywords matching actions that should trigger a check
KEYWORD_RULES = [
    # Survival (WIS)
    (r"(?:แกะรอย|ตามรอย|ร่องรอย|สะกดรอย|ล่าสัตว์|หาน้ำ|หาอาหาร|เอาชีวิตรอด|track|survival)", "survival", "medium"),
    # Perception (WIS)
    (r"(?:มองหา|สอดส่อง|ฟังเสียง|ตรวจตรา|สำรวจรอบ|perception|listen closely)", "perception", "medium"),
    # Investigation (INT)
    (r"(?:สืบหา|สืบสวน|ค้นหาเบาะแส|ค้นหาของซ่อน|ตรวจค้นห้อง|ชันสูตร|ตรวจสอบอย่างละเอียด|investigat)", "investigation", "medium"),
    # Stealth (DEX)
    (r"(?:ย่อง|แอบ|ซ่อนตัว|ลอบเข้า|หลบสายตา|ล่องหน|stealth|sneak|hide)", "stealth", "medium"),
    # Sleight of Hand (DEX)
    (r"(?:สะเดาะกลอน|ปลดล็อค|ล้วงกระเป๋า|ขโมย|สับเปลี่ยน|pick lock|sleight of hand|pocket)", "sleight_of_hand", "medium"),
    # Athletics (STR)
    (r"(?:ปีน|กระโดดข้าม|ว่ายน้ำทวน|พังประตู|งัดประตู|ผลักหิน|ยกรถ|athletics|climb|jump across|force open)", "athletics", "medium"),
    # Acrobatics (DEX)
    (r"(?:ทรงตัว|ตีลังกา|หลบกับดัก|acrobatic|balance)", "acrobatics", "medium"),
    # Arcana (INT)
    (r"(?:ตรวจจับเวท|อ่านรูน|พลังเวท|วงแหวนเวท|ศิลาเวท|คัมภีร์เวท|arcana|detect magic)", "arcana", "medium"),
    # History (INT)
    (r"(?:ประวัติศาสตร์|ตราประจำตระกูล|อักษรโบราณ|ตำนานเก่าแก่|history)", "history", "medium"),
    # Nature (INT)
    (r"(?:พืชสมุนไพร|พิษพืช|nature|herbs)", "nature", "medium"),
    # Religion (INT)
    (r"(?:แท่นบูชา|บทสวด|สัญลักษณ์เทพ|รูปปั้นบูชา|religion|altar)", "religion", "medium"),
    # Insight (WIS)
    (r"(?:จับโกหก|อ่านสีหน้า|สังเกตท่าที|หยั่งรู้|insight)", "insight", "medium"),
    # Medicine (WIS)
    (r"(?:รักษาแผล|ปฐมพยาบาล|ดูอาการบาดเจ็บ|ตรวจพิษ|medicine|first aid)", "medicine", "medium"),
    # Animal Handling (WIS)
    (r"(?:ปลอบสัตว์|ลูบหัวสัตว์|ทำให้ม้าสงบ|animal handling)", "animal_handling", "medium"),
    # Persuasion (CHA)
    (r"(?:เกลี้ยกล่อม|เจรจา|ขอร้อง|โน้มน้าวใจ|ขอความเห็นใจ|ต่อรองราคา|persua)", "persuasion", "medium"),
    # Deception (CHA)
    (r"(?:โกหก|ตบตา|หลอกว่า|เสแสร้ง|แกล้งทำเป็น|ปลอมตัว|deceiv|lie)", "deception", "medium"),
    # Intimidation (CHA)
    (r"(?:ข่มขู่|คุกคาม|ตะคอกใส่|ชักดาบขู่|บีบบังคับ|intimidat|threaten)", "intimidation", "medium"),
]

# Exclusion regex for purely informational or everyday actions
EXCLUSION_RULES = [
    r"^(?:เดิน|มุ่งหน้า|ก้าว|เดินทาง)(?:ไป|ออก)?\s*(?:ทาง|ทิศ|กลับ|เข้า)?",
    r"^(?:ซื้อ|ขาย|ดูสินค้า)",
    r"^(?:พัก|นอน|กางเต็นท์)",
    r"^(?:ดื่ม|กิน)",
    r"^(?:พูดคุย|ทักทาย|ถามชื่อ|ถามข่าวสาร|ถามทาง)\b",
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

    # 2. Check exclusion patterns (casual dialogue, pure movement, shopping)
    # But only if no specific skill keywords are present
    has_skill_keyword = False
    for pat, skill_id, diff in KEYWORD_RULES:
        if re.search(pat, raw_text, re.IGNORECASE):
            has_skill_keyword = True
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
