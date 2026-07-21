"""
character_creator.py — Phase 1
Handles point-buy stat validation and derived stat calculation for character creation.
Scope: levels 1-5 only. No Ollama calls. Pure Python + catalog lookups.
"""

import json
import math
import os
from typing import Dict, Any, Tuple, Optional

# ─── Point-Buy Constants (D&D 5e PHB) ────────────────────────────────────────
POINT_BUY_COST: Dict[int, int] = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
POINT_BUY_BUDGET: int = 27
STAT_NAMES = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]

# ─── Proficiency Bonus by Level (levels 1-5 scope per spec 4e) ────────────────
PROFICIENCY_BY_LEVEL: Dict[int, int] = {1: 2, 2: 2, 3: 2, 4: 2, 5: 3}

# ─── XP Thresholds for levels 1-5 (milestone-leaning, per spec Section 17b) ─
XP_THRESHOLDS: Dict[int, int] = {1: 0, 2: 300, 3: 900, 4: 2700, 5: 6500}

# ─── Hit Die average per class (used for HP max on level-up) ─────────────────
HIT_DIE_AVERAGE: Dict[str, int] = {
    "fighter": 6,   # 1d10 average = 5.5 -> floor+1 = 6
    "wizard": 4,    # 1d6  average = 3.5 -> floor+1 = 4
    "rogue": 5,     # 1d8  average = 4.5 -> floor+1 = 5
    "bard": 5,      # 1d8  average = 4.5 -> floor+1 = 5
    "cleric": 5,    # 1d8  average = 4.5 -> floor+1 = 5
}

# ─── Catalog path helpers ─────────────────────────────────────────────────────
_CATALOG_DIR = os.path.dirname(os.path.abspath(__file__))

def _load_catalog(filename: str) -> dict:
    path = os.path.join(_CATALOG_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_point_buy(stats: Dict[str, int]) -> Tuple[bool, Optional[str]]:
    """
    Validate a point-buy stat allocation.

    Args:
        stats: dict mapping stat name to value, e.g. {"STR": 10, "DEX": 14, ...}

    Returns:
        (True, None) if valid.
        (False, "<human-readable error>") if invalid.

    Rules:
        - All six stats must be present.
        - Each stat must be between 8 and 15 inclusive.
        - Total cost must not exceed POINT_BUY_BUDGET (27).
    """
    # Check all six stats are present
    missing = [s for s in STAT_NAMES if s not in stats]
    if missing:
        return False, f"Missing stats: {', '.join(missing)}. All six stats (STR, DEX, CON, INT, WIS, CHA) must be provided."

    total_cost = 0
    for stat_name in STAT_NAMES:
        value = stats[stat_name]

        # Range check
        if value < 8:
            return False, f"{stat_name} is {value} — stats cannot be lower than 8 in point-buy."
        if value > 15:
            return False, f"{stat_name} is {value} — stats cannot exceed 15 before racial bonuses in point-buy."

        # Valid value check (must be in cost table)
        if value not in POINT_BUY_COST:
            return False, f"{stat_name} has invalid value {value} — allowed values are 8 through 15."

        total_cost += POINT_BUY_COST[value]

    if total_cost > POINT_BUY_BUDGET:
        return False, (
            f"Stat allocation costs {total_cost} points, but the budget is {POINT_BUY_BUDGET}. "
            f"You are over budget by {total_cost - POINT_BUY_BUDGET} point(s). Reduce some stats."
        )

    if total_cost < 0:
        # Logically impossible given the cost table, but be explicit
        return False, "Total point cost is negative — something is very wrong with the stat values."

    return True, None


def get_modifier(stat_value: int) -> int:
    """Return the D&D 5e ability modifier for a given stat value."""
    return math.floor((stat_value - 10) / 2)


def compute_max_hp(class_id: str, con_score: int, level: int = 1) -> int:
    """
    Compute HP max at level 1 (max hit die + CON modifier).
    For levels 2+ use HIT_DIE_AVERAGE + CON mod per level.
    """
    classes = _load_catalog("classes_catalog.json")
    cls = classes.get(class_id, {})
    hit_die = cls.get("hit_die", 8)
    con_mod = get_modifier(con_score)

    if level == 1:
        return hit_die + con_mod
    else:
        # Level 1 = max die; subsequent levels = average roll + CON mod
        base = hit_die + con_mod
        avg = HIT_DIE_AVERAGE.get(class_id, 5)
        for _ in range(level - 1):
            base += avg + con_mod
        return max(1, base)


def compute_ac(class_id: str, stats: Dict[str, int], equipped_items: list = None) -> int:
    """
    Compute base AC from class armor proficiency + equipped items.
    Simplified for character creation: unarmored = 10 + DEX mod.
    Actual item effects applied by state_manager at runtime.
    """
    dex_mod = get_modifier(stats.get("DEX", 10))
    classes = _load_catalog("classes_catalog.json")
    cls = classes.get(class_id, {})
    armor_profs = cls.get("armor_proficiencies", [])

    # Bard/Rogue/Wizard start unarmored (base 10 + DEX)
    # Fighter starts in chain mail (AC 16, no DEX bonus)
    # Cleric starts in scale mail (AC 14, no DEX bonus)
    # Simplify: check starting equipment for armor type
    starting_gear = cls.get("starting_equipment", [])

    if "chain_mail" in starting_gear:
        return 16
    elif "scale_mail" in starting_gear:
        return 14
    elif "leather_armor" in starting_gear:
        return 11 + dex_mod  # leather = 11 + DEX
    else:
        return 10 + dex_mod  # unarmored


def derive_stats(
    name: str,
    race_id: str,
    class_id: str,
    background_id: str,
    base_stats: Dict[str, int],
    campaign_tone: str,
) -> Dict[str, Any]:
    """
    Derive a complete character sheet from the character creation choices.
    Returns a dict matching the saves/<n>.json schema_version 4 (spec Section 5a).

    Steps:
      1. Apply racial stat bonuses on top of point-buy base stats.
      2. Compute HP max, AC, proficiency bonus.
      3. Derive proficient skills (class + background).
      4. Set starting gold from background.
      5. Set starting spells if class is a spellcaster.
      6. Return full schema_version 4 character sheet dict.
    """
    races = _load_catalog("races_catalog.json")
    classes = _load_catalog("classes_catalog.json")
    backgrounds = _load_catalog("backgrounds_catalog.json")

    race = races.get(race_id, {})
    cls = classes.get(class_id, {})
    background = backgrounds.get(background_id, {})

    # 1. Apply racial bonuses
    final_stats = dict(base_stats)
    for stat, bonus in race.get("stat_bonuses", {}).items():
        final_stats[stat] = final_stats.get(stat, 8) + bonus

    # 2. Derived values
    con_score = final_stats.get("CON", 10)
    hp_max = compute_max_hp(class_id, con_score, level=1)
    ac = compute_ac(class_id, final_stats)
    prof_bonus = PROFICIENCY_BY_LEVEL[1]

    # 3. Proficient skills: class choices (pick first option set) + background
    class_skill_pool = cls.get("proficient_skills_options", [])
    # For creation defaults: take the first N from the pool
    num_class_skills = cls.get("proficient_skills_choose", 2)
    if class_skill_pool and class_skill_pool[0] != "Any":
        class_skills = class_skill_pool[:num_class_skills]
    else:
        # "Any" — default to Persuasion + Deception for Bard, etc.
        class_skills = ["Persuasion", "Performance"]

    background_skills = background.get("skill_proficiencies", [])
    all_skills = list(dict.fromkeys(class_skills + background_skills))  # deduplicate, preserve order

    # 4. Starting gold
    starting_gold = background.get("starting_gold", 10)

    # 5. Spells
    known_spells = cls.get("level_1_spells_known", [])
    spell_slots = cls.get("spell_slots_level_1", {}) if cls.get("spellcasting") else {}

    # 6. Bardic inspiration (Bard only)
    bardic_inspiration = {}
    if class_id == "bard":
        cha_mod = get_modifier(final_stats.get("CHA", 10))
        bi_max = max(1, cha_mod)
        bardic_inspiration = {"max": bi_max, "current": bi_max}

    # 7. Proficient saves (from class)
    proficient_saves = cls.get("proficient_saves", [])

    # 8. Background hook
    background_hook = background.get("background_hook", "")

    character_sheet = {
        "schema_version": 4,
        "name": name,
        "race": race.get("name", race_id),
        "class": cls.get("name", class_id),
        "background": background.get("name", background_id),
        "background_hook": background_hook,
        "level": 1,
        "xp_current": 0,
        "campaign_tone": campaign_tone,
        "hp": {"current": hp_max, "max": hp_max},
        "ac": ac,
        "stats": final_stats,
        "proficiency_bonus": prof_bonus,
        "proficient_skills": all_skills,
        "proficient_saves": proficient_saves,
        "spell_slots": spell_slots,
        "known_spells": known_spells,
        "bardic_inspiration": bardic_inspiration,
        "concentration": None,
        "death_saves": {"success": 0, "fail": 0},
        "status": "normal",
        "active_conditions": [],
        "gold": starting_gold,
        "inventory": [],
        "roll_log": [],
    }

    return character_sheet
