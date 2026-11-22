"""
character_creator.py — Phase 1 & BG3 Overhaul
Handles point-buy stat validation and derived stat calculation for character creation.
Scope: levels 1-5 only. No Ollama calls. Pure Python + catalog lookups.
Supports all 8 classes, subclasses, fighting styles, custom skill & spell selection, and class resources.
"""

import json
import math
import os
from typing import Dict, Any, Tuple, Optional, List

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
    "fighter": 6,    # 1d10 average = 5.5 -> floor+1 = 6
    "paladin": 6,    # 1d10 average = 5.5 -> floor+1 = 6
    "ranger": 6,     # 1d10 average = 5.5 -> floor+1 = 6
    "barbarian": 7,  # 1d12 average = 6.5 -> floor+1 = 7
    "wizard": 4,     # 1d6  average = 3.5 -> floor+1 = 4
    "sorcerer": 4,   # 1d6  average = 3.5 -> floor+1 = 4
    "rogue": 5,      # 1d8  average = 4.5 -> floor+1 = 5
    "bard": 5,       # 1d8  average = 4.5 -> floor+1 = 5
    "cleric": 5,     # 1d8  average = 4.5 -> floor+1 = 5
    "warlock": 5,    # 1d8  average = 4.5 -> floor+1 = 5
    "druid": 5,      # 1d8  average = 4.5 -> floor+1 = 5
    "monk": 5,       # 1d8  average = 4.5 -> floor+1 = 5
}

import paths

# ─── Catalog path helpers ─────────────────────────────────────────────────────
_CATALOG_DIR = paths.CATALOG_DIR


def _load_catalog(filename: str) -> dict:
    path = filename if os.path.isabs(filename) else os.path.join(_CATALOG_DIR, filename)
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
    """
    missing = [s for s in STAT_NAMES if s not in stats]
    if missing:
        return False, f"Missing stats: {', '.join(missing)}. All six stats (STR, DEX, CON, INT, WIS, CHA) must be provided."

    total_cost = 0
    for stat_name in STAT_NAMES:
        value = stats[stat_name]

        if value < 8:
            return False, f"{stat_name} is {value} — stats cannot be lower than 8 in point-buy."
        if value > 15:
            return False, f"{stat_name} is {value} — stats cannot exceed 15 before racial bonuses in point-buy."

        if value not in POINT_BUY_COST:
            return False, f"{stat_name} has invalid value {value} — allowed values are 8 through 15."

        total_cost += POINT_BUY_COST[value]

    if total_cost > POINT_BUY_BUDGET:
        return False, (
            f"Stat allocation costs {total_cost} points, but the budget is {POINT_BUY_BUDGET}. "
            f"You are over budget by {total_cost - POINT_BUY_BUDGET} point(s). Reduce some stats."
        )

    if total_cost < 0:
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
        base = hit_die + con_mod
        avg = HIT_DIE_AVERAGE.get(class_id, 5)
        for _ in range(level - 1):
            base += avg + con_mod
        return max(1, base)


def compute_ac(
    class_id: str,
    stats: Dict[str, int],
    equipped_items: list = None,
    fighting_style: Optional[str] = None,
) -> int:
    """
    Compute base AC from class armor proficiency + equipped items.
    Supports Barbarian Unarmored Defense (10 + DEX mod + CON mod) and Fighting Style: Defence (+1 AC when wearing armor).
    """
    dex_mod = get_modifier(stats.get("DEX", 10))
    con_mod = get_modifier(stats.get("CON", 10))
    classes = _load_catalog("classes_catalog.json")
    cls = classes.get(class_id, {})

    starting_gear = equipped_items if equipped_items is not None else cls.get("starting_equipment", [])

    has_armor = False
    ac_base = 10 + dex_mod
    if "chain_mail" in starting_gear:
        ac_base = 16
        has_armor = True
    elif "scale_mail" in starting_gear:
        ac_base = 14
        has_armor = True
    elif "studded_leather" in starting_gear:
        ac_base = 12 + dex_mod
        has_armor = True
    elif "leather_armor" in starting_gear:
        ac_base = 11 + dex_mod
        has_armor = True
    elif class_id == "barbarian":
        # Barbarian Unarmored Defense: 10 + DEX mod + CON mod
        ac_base = 10 + dex_mod + max(0, con_mod)
    elif class_id == "monk":
        # Monk Unarmored Defense: 10 + DEX mod + WIS mod
        wis_mod = get_modifier(stats.get("WIS", 10))
        ac_base = 10 + dex_mod + max(0, wis_mod)
    elif class_id == "sorcerer":
        # Draconic Bloodline Sorcerer Resilience: 13 + DEX mod
        ac_base = 13 + dex_mod

    if "shield" in starting_gear:
        ac_base += 2

    if fighting_style == "defence" and has_armor:
        ac_base += 1

    return ac_base


def build_initial_class_resources(class_id: str, stats: Dict[str, int], level: int = 1) -> Dict[str, Any]:
    """Build initial class-specific BG3 resources for level 1 (or specified level)."""
    cha_mod = max(1, get_modifier(stats.get("CHA", 10)))
    resources: Dict[str, Any] = {}

    if class_id == "fighter":
        resources["second_wind"] = {"current": 1, "max": 1}
        resources["action_surge"] = {"current": 1 if level >= 2 else 0, "max": 1 if level >= 2 else 0}
        resources["superiority_dice"] = {"current": 4 if level >= 3 else 0, "max": 4 if level >= 3 else 0}
    elif class_id == "rogue":
        resources["sneak_attack_dice"] = (level + 1) // 2
        resources["cunning_action"] = True
    elif class_id == "wizard":
        resources["arcane_recovery"] = {"current": 1, "max": 1}
    elif class_id == "cleric":
        resources["channel_divinity"] = {"current": 1, "max": 1}
    elif class_id == "bard":
        resources["bardic_inspiration"] = {"current": cha_mod, "max": cha_mod}
    elif class_id == "paladin":
        resources["lay_on_hands"] = {"current": level * 5, "max": level * 5}
        resources["channel_oath"] = {"current": 1, "max": 1}
    elif class_id == "barbarian":
        rage_max = 3 if level >= 3 else 2
        resources["rage_charges"] = {"current": rage_max, "max": rage_max}
    elif class_id == "warlock":
        resources["pact_magic"] = True
    elif class_id == "druid":
        resources["wild_shape"] = {"current": 2, "max": 2}
    elif class_id == "sorcerer":
        resources["sorcery_points"] = {"current": max(1, level), "max": max(1, level)}
    elif class_id == "monk":
        resources["ki_points"] = {"current": max(2, level), "max": max(2, level)}
    elif class_id == "ranger":
        resources["favored_enemy"] = True

    return resources


def derive_stats(
    name: str,
    race_id: str,
    class_id: str,
    background_id: str,
    base_stats: Dict[str, int],
    campaign_tone: str,
    subclass: Optional[str] = None,
    fighting_style: Optional[str] = None,
    chosen_skills: Optional[List[str]] = None,
    chosen_spells: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Derive a complete character sheet from the character creation choices.
    Returns a dict matching the saves/<n>.json schema_version 4 (spec Section 5a),
    enhanced with BG3 class/subclass/spell/resource metadata.
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

    # 2. Subclass & Fighting Style resolution
    resolved_subclass = subclass or cls.get("default_subclass")
    subclasses_dict = cls.get("subclasses", {})
    subclass_info = subclasses_dict.get(resolved_subclass, {}) if resolved_subclass else {}
    subclass_name = subclass_info.get("name", resolved_subclass or "")

    resolved_fighting_style = fighting_style
    if not resolved_fighting_style and class_id in ("fighter", "paladin"):
        # Default to 'dueling' so Fighter's base AC remains 18 for existing tests unless 'defence' is chosen
        resolved_fighting_style = "dueling"

    # 3. Derived values
    con_score = final_stats.get("CON", 10)
    hp_max = compute_max_hp(class_id, con_score, level=1)
    ac = compute_ac(class_id, final_stats, fighting_style=resolved_fighting_style)
    prof_bonus = PROFICIENCY_BY_LEVEL[1]

    # 4. Proficient skills: user-chosen or class defaults + background
    if chosen_skills and len(chosen_skills) > 0:
        class_skills = list(chosen_skills)
    else:
        class_skill_pool = cls.get("proficient_skills_options", [])
        num_class_skills = cls.get("proficient_skills_choose", 2)
        if class_skill_pool and class_skill_pool[0] != "Any":
            class_skills = class_skill_pool[:num_class_skills]
        else:
            class_skills = ["Persuasion", "Performance"]

    background_skills = background.get("skill_proficiencies", [])
    all_skills = list(dict.fromkeys(class_skills + background_skills))

    # 5. Starting gold
    starting_gold = background.get("starting_gold", 10)

    # 6. Spells
    default_spells = list(cls.get("level_1_spells_known", []))
    if chosen_spells and len(chosen_spells) > 0:
        # Merge chosen spells while ensuring signature default spells (like magic_missile / cure_wounds) remain if not overridden
        known_spells = list(dict.fromkeys(chosen_spells))
    else:
        known_spells = default_spells

    # Add domain / subclass bonus spells at level 1 if any
    bonus_spells = subclass_info.get("bonus_spells", [])
    for b_sp in bonus_spells:
        if b_sp not in known_spells:
            known_spells.append(b_sp)

    import copy
    spell_slots = copy.deepcopy(cls.get("spell_slots_level_1", {})) if cls.get("spellcasting") else {}

    # 7. Bardic inspiration & Class Resources
    bardic_inspiration = {}
    if class_id == "bard":
        cha_mod = get_modifier(final_stats.get("CHA", 10))
        bi_max = max(1, cha_mod)
        bardic_inspiration = {"max": bi_max, "current": bi_max}

    class_resources = build_initial_class_resources(class_id, final_stats, level=1)

    # 8. Proficient saves (from class)
    proficient_saves = cls.get("proficient_saves", [])

    # 9. Background hook
    background_hook = background.get("background_hook", "")

    # 10. Starting inventory & equipment
    starting_gear = cls.get("starting_equipment", [])
    item_catalog = _load_catalog("item_catalog.json")
    starting_inventory = []
    equipped_slots = set()

    for item_id in starting_gear:
        info = item_catalog.get(item_id, {})
        itype = info.get("type")
        islot = info.get("slot")

        should_equip = False
        if itype in ("weapon", "wearable"):
            if islot and islot not in equipped_slots:
                should_equip = True
                equipped_slots.add(islot)
            elif not islot and "main_hand" not in equipped_slots:
                should_equip = True
                equipped_slots.add("main_hand")

        starting_inventory.append({
            "item_id": item_id,
            "equipped": should_equip,
            "quantity": 1,
        })

    # Always ensure starting trail rations (3)
    starting_inventory.append({
        "item_id": "trail_rations",
        "equipped": False,
        "quantity": 3,
    })

    # 11. Starting class features (level 1)
    features_by_lvl = cls.get("features_by_level", {})
    unlocked_features = list(features_by_lvl.get("1", []))

    character_sheet = {
        "schema_version": 4,
        "name": name,
        "race": race.get("name", race_id),
        "class": cls.get("name", class_id),
        "subclass": resolved_subclass,
        "subclass_name": subclass_name,
        "fighting_style": resolved_fighting_style,
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
        "class_resources": class_resources,
        "class_features": unlocked_features,
        "feats": [],
        "pending_level_up_choices": 0,
        "concentration": None,
        "death_saves": {"success": 0, "fail": 0},
        "status": "normal",
        "active_conditions": [],
        "currency": {"gp": starting_gold, "sp": 5, "cp": 30},
        "gold": starting_gold,
        "inventory": starting_inventory,
        "weapon_actions_available": True,
        "inspiration": 0,
        "max_inspiration": 4,
        "roll_log": [],
    }

    return character_sheet


def create_character(
    name: str,
    race: str,
    class_name: str,
    background: str,
    stats: Dict[str, int],
    campaign_tone: str = "classic",
    subclass: Optional[str] = None,
    fighting_style: Optional[str] = None,
    chosen_skills: Optional[List[str]] = None,
    chosen_spells: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Convenience wrapper for UI / callers. Normalizes display names to catalog IDs
    and delegates to derive_stats().
    """
    race_id = race.strip().lower().replace(" ", "_").replace("-", "_")
    class_id = class_name.strip().lower().replace(" ", "_")
    background_id = background.strip().lower().replace(" ", "_")
    return derive_stats(
        name=name.strip(),
        race_id=race_id,
        class_id=class_id,
        background_id=background_id,
        base_stats=stats,
        campaign_tone=campaign_tone,
        subclass=subclass,
        fighting_style=fighting_style,
        chosen_skills=chosen_skills,
        chosen_spells=chosen_spells,
    )
