"""
rules.py — Pure D&D 5e Rules, Catalogs, and Core Math Base Layer.
Pure standard library + paths.py only. No project module imports.
"""

import json
import logging
import math
import os
import random
from typing import Any, Dict, List, Optional

import paths

logger = logging.getLogger("rules")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setLevel(logging.DEBUG)
    _h.setFormatter(logging.Formatter("[RULES] %(levelname)s: %(message)s"))
    logger.addHandler(_h)

# ─── Difficulty to DC Mapping (D&D 5e standard) ──────────────────────────────
DIFFICULTY_TO_DC: Dict[str, int] = {
    "easy":      10,
    "medium":    13,
    "hard":      16,
    "very_hard": 19,
}

STAT_NAMES: List[str] = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]

SKILL_TO_STAT: Dict[str, str] = {
    "Athletics": "STR",
    "Acrobatics": "DEX", "Sleight of Hand": "DEX", "Stealth": "DEX",
    "Arcana": "INT", "History": "INT", "Investigation": "INT", "Nature": "INT", "Religion": "INT",
    "Animal Handling": "WIS", "Insight": "WIS", "Medicine": "WIS", "Perception": "WIS", "Survival": "WIS",
    "Deception": "CHA", "Intimidation": "CHA", "Performance": "CHA", "Persuasion": "CHA",
}

CONCENTRATION_DC_FLOOR: int = 10

# ─── Catalog path & caches ───────────────────────────────────────────────────
_CATALOG_DIR: str = paths.CATALOG_DIR
_item_catalog: Optional[Dict[str, Any]] = None
_spell_catalog: Optional[Dict[str, Any]] = None
_shop_catalog: Optional[Dict[str, Any]] = None


def _get_item_catalog() -> Dict[str, Any]:
    global _item_catalog
    if _item_catalog is None:
        try:
            path = os.path.join(_CATALOG_DIR, "item_catalog.json")
            with open(path, "r", encoding="utf-8") as f:
                _item_catalog = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load item_catalog.json: {e}")
            _item_catalog = {}
    return _item_catalog


def _get_spell_catalog() -> Dict[str, Any]:
    global _spell_catalog
    if _spell_catalog is None:
        try:
            path = os.path.join(_CATALOG_DIR, "spell_catalog.json")
            with open(path, "r", encoding="utf-8") as f:
                _spell_catalog = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load spell_catalog.json: {e}")
            _spell_catalog = {}
    return _spell_catalog


def _get_shop_catalog() -> Dict[str, Any]:
    global _shop_catalog
    if _shop_catalog is None:
        try:
            path = os.path.join(_CATALOG_DIR, "shop_catalog.json")
            with open(path, "r", encoding="utf-8") as f:
                _shop_catalog = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load shop_catalog.json: {e}")
            _shop_catalog = {}
    return _shop_catalog


def get_spell_catalog(world_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Return merged spell catalog containing static spells from spell_catalog.json
    and any generated spells present in world_state["generated_spells"].
    Does NOT modify the static catalog.
    """
    merged = dict(_get_spell_catalog())
    if world_state and isinstance(world_state, dict):
        gen = world_state.get("generated_spells", {})
        if isinstance(gen, dict):
            merged.update(gen)
    return merged


def resolve_item(
    item_id: str,
    world_state: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Spec Section 7c / PART 4a-ii:
    Single source of truth for resolving item definitions.
    Checks static item_catalog.json first, falls back to world_state["generated_items"].
    """
    catalog = _get_item_catalog()
    if item_id in catalog:
        return catalog[item_id]

    if world_state and isinstance(world_state, dict):
        gen_items = world_state.get("generated_items", {})
        if item_id in gen_items:
            return gen_items[item_id]

    return None


def resolve_spell(
    spell_id: str,
    world_state: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Single source of truth for resolving spell definitions.
    Checks static spell_catalog.json first, falls back to world_state["generated_spells"].
    """
    catalog = _get_spell_catalog()
    if spell_id in catalog:
        return catalog[spell_id]

    if world_state and isinstance(world_state, dict):
        gen_spells = world_state.get("generated_spells", {})
        if spell_id in gen_spells:
            return gen_spells[spell_id]

    return None


def _inventory_item_info(inv_item: Dict[str, Any], catalog: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Get full item info for an inventory entry.
    Checks static item catalog first.
    If not found in catalog, falls back to inv_item.get("definition", {}).
    If inv_item is already an info dict (or doesn't have 'item_id'), falls back gracefully.
    """
    if not isinstance(inv_item, dict):
        return {}
    item_id = inv_item.get("item_id")
    if catalog is None:
        catalog = _get_item_catalog()
    if item_id and item_id in catalog:
        return catalog[item_id]
    if "definition" in inv_item and isinstance(inv_item["definition"], dict):
        return inv_item["definition"]
    if item_id:
        return catalog.get(item_id, {})
    return inv_item


# ════════════════════════════════════════════════════════════════════════════════
# CORE MATH & DICE
# ════════════════════════════════════════════════════════════════════════════════

def get_modifier(stat_value: int) -> int:
    """D&D 5e ability modifier: floor((stat - 10) / 2)."""
    return math.floor((stat_value - 10) / 2)


def is_proficient(skill_or_save: str, state: Dict[str, Any]) -> bool:
    """
    Return True if the character is proficient in the given skill or saving throw.
    Checks both proficient_skills and proficient_saves lists in the character state.
    """
    skills = state.get("proficient_skills", [])
    saves  = state.get("proficient_saves", [])
    target = str(skill_or_save).strip().lower()
    return (
        any(target == str(s).strip().lower() for s in skills)
        or any(target == str(s).strip().lower() for s in saves)
    )


def _roll_d20() -> int:
    """Roll 1d20. Separate function so tests can monkeypatch it."""
    return random.randint(1, 20)


def _roll_dice(dice_string: str) -> int:
    """
    Parse and roll a dice expression like '2d6', '1d8+3', '1d4+2'.
    Returns total as int.
    """
    dice_string = dice_string.strip().lower()
    bonus = 0
    if "+" in dice_string:
        parts = dice_string.split("+", 1)
        dice_string = parts[0].strip()
        bonus = int(parts[1].strip())
    elif "-" in dice_string:
        parts = dice_string.split("-", 1)
        dice_string = parts[0].strip()
        bonus = -int(parts[1].strip())

    if "d" in dice_string:
        num, die = dice_string.split("d")
        num = int(num) if num else 1
        die = int(die)
        total = sum(random.randint(1, die) for _ in range(num))
    else:
        total = int(dice_string)

    return total + bonus
