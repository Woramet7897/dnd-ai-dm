"""
state_manager.py — Phase 2
Core D&D 5e math engine. Zero LLM involvement, zero Ollama calls.
Python owns 100% of all math: dice, modifiers, proficiency, advantage/disadvantage,
crit/fumble, death saves, concentration DC, DC selection, and state routing.

Scope: levels 1-5. Schema version 4 saves only.

Functions implemented this phase:
  get_modifier, is_proficient, resolve_check, resolve_death_save,
  resolve_concentration_check, apply_state_updates, log_roll

Stub functions (# PHASE 6+):
  award_xp, check_level_up, apply_level_up,
  buy_item, sell_item,
  resolve_spell_save, resolve_downed_outcome, dismiss_companion
"""

import json
import math
import os
import random
import tempfile
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("state_manager")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[STATE] %(levelname)s: %(message)s"))
    logger.addHandler(_h)

# ─── Schema version this module understands ──────────────────────────────────
SUPPORTED_SCHEMA_VERSION = 4

# ─── Difficulty enum → DC mapping (spec Section 8a / PART 5a) ────────────────
# The LLM NEVER emits a raw DC. It only emits a difficulty enum string.
# Python owns the mapping — this is the single authoritative table.
DIFFICULTY_TO_DC: Dict[str, int] = {
    "easy":      10,
    "medium":    13,
    "hard":      16,
    "very_hard": 19,
}

# ─── Skill → governing ability stat (5e standard) ────────────────────────────
SKILL_TO_STAT: Dict[str, str] = {
    "Acrobatics": "DEX", "Animal Handling": "WIS", "Arcana": "INT",
    "Athletics": "STR", "Deception": "CHA", "History": "INT",
    "Insight": "WIS", "Intimidation": "CHA", "Investigation": "INT",
    "Medicine": "WIS", "Nature": "INT", "Perception": "WIS",
    "Performance": "CHA", "Persuasion": "CHA", "Religion": "INT",
    "Sleight of Hand": "DEX", "Stealth": "DEX", "Survival": "WIS",
}

# ─── Save directories (per spec Section 3 / 5a) ──────────────────────────────
SAVES_DIR        = "saves"
WORLD_SAVES_DIR  = "world_saves"
BACKUP_DIR       = "save_backups"
MAX_BACKUPS      = 3

# ─── Concentration check DC floor (spec Section 8 / PART 5a) ─────────────────
CONCENTRATION_DC_FLOOR = 10

# ─── Item Catalog Loader (spec Section 7b) ────────────────────────────────────
_CATALOG_DIR = os.path.dirname(os.path.abspath(__file__))
_item_catalog: Optional[Dict[str, Any]] = None
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


# ════════════════════════════════════════════════════════════════════════════════
# EQUIPMENT & CONSUMABLES SYSTEM (spec Section 7b)
# ════════════════════════════════════════════════════════════════════════════════

def _compute_ac(character_state: Dict[str, Any]) -> int:
    """
    Compute total Armor Class (AC) from equipped items + DEX modifier.
    Spec Section 7b:
    - Base AC comes from equipped chest item's ac_base (or 10 if unarmored).
    - DEX mod is added if unarmored or if chest armor's stat_mod == "DEX".
    - Adds sum of ac_bonus from all equipped items (shield, cloak, etc.).
    """
    catalog = _get_item_catalog()
    inventory = character_state.get("inventory", [])
    stats = character_state.get("stats", {})
    dex_mod = get_modifier(stats.get("DEX", 10))

    equipped_chest = None
    ac_bonus_total = 0

    for item in inventory:
        if isinstance(item, dict) and item.get("equipped") is True:
            item_id = item.get("item_id")
            info = catalog.get(item_id, {})
            slot = info.get("slot")
            effects = info.get("effects", {})

            if slot == "chest":
                equipped_chest = info

            if "ac_bonus" in effects:
                ac_bonus_total += effects.get("ac_bonus", 0)

    if equipped_chest is not None:
        chest_effects = equipped_chest.get("effects", {})
        base_ac = chest_effects.get("ac_base", 10)
        stat_mod = chest_effects.get("stat_mod")
        if stat_mod == "DEX":
            total_ac = base_ac + dex_mod + ac_bonus_total
        else:
            total_ac = base_ac + ac_bonus_total
    else:
        # Unarmored: 10 + DEX mod
        total_ac = 10 + dex_mod + ac_bonus_total

    return total_ac


def get_active_effects(character_state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Aggregate all effects from currently-equipped items in character inventory.
    Spec Section 7b: Single source of truth for equipment effects queried by
    combat_manager and validation.py.

    Returns dict like:
      {
        "ac_bonus_total": 3,
        "saving_throw_bonus": 1,
        "attack_bonus": 0,
        "skill_bonus": {"Sleight of Hand": 2},
        "enables_spellcasting": True
      }
    """
    catalog = _get_item_catalog()
    inventory = character_state.get("inventory", [])

    ac_bonus_total = 0
    saving_throw_bonus = 0
    attack_bonus = 0
    skill_bonus: Dict[str, int] = {}
    enables_spellcasting = False

    for item in inventory:
        if isinstance(item, dict) and item.get("equipped") is True:
            item_id = item.get("item_id")
            info = catalog.get(item_id, {})
            effects = info.get("effects", {})

            if "ac_bonus" in effects:
                ac_bonus_total += effects["ac_bonus"]
            if "saving_throw_bonus" in effects:
                saving_throw_bonus += effects["saving_throw_bonus"]
            if "attack_bonus" in effects:
                attack_bonus += effects["attack_bonus"]
            if "enables_spellcasting" in effects and effects["enables_spellcasting"]:
                enables_spellcasting = True
            if "skill_bonus" in effects and isinstance(effects["skill_bonus"], dict):
                for sk, bon in effects["skill_bonus"].items():
                    skill_bonus[sk] = skill_bonus.get(sk, 0) + bon

    return {
        "ac_bonus_total": ac_bonus_total,
        "saving_throw_bonus": saving_throw_bonus,
        "attack_bonus": attack_bonus,
        "skill_bonus": skill_bonus,
        "enables_spellcasting": enables_spellcasting,
    }


def equip_item(item_id: str, character_state: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Equip an item from character inventory.
    Spec Section 7b:
    - Fail if not carried, or if item type is not 'wearable' or 'weapon'.
    - If another equipped item occupies the same slot, unequip it first.
    - Set target item's 'equipped': True.
    - Recompute character_state['ac'] in place.
    - Return (True, "") or (False, reason). Never partially apply.
    """
    inventory = character_state.get("inventory", [])
    target_item = None
    for item in inventory:
        if isinstance(item, dict) and item.get("item_id") == item_id:
            target_item = item
            break

    if target_item is None:
        return False, f"Item '{item_id}' not found in inventory."

    catalog = _get_item_catalog()
    item_info = catalog.get(item_id)
    if not item_info:
        return False, f"Item '{item_id}' not found in item catalog."

    item_type = item_info.get("type")
    if item_type not in ("wearable", "weapon"):
        return False, f"Item '{item_id}' of type '{item_type}' cannot be equipped."

    target_slot = item_info.get("slot")
    if target_slot:
        for other_item in inventory:
            if isinstance(other_item, dict) and other_item is not target_item and other_item.get("equipped") is True:
                other_id = other_item.get("item_id")
                other_info = catalog.get(other_id, {})
                if other_info.get("slot") == target_slot:
                    other_item["equipped"] = False

    target_item["equipped"] = True
    character_state["ac"] = _compute_ac(character_state)
    logger.debug(f"equip_item: equipped '{item_id}' in slot '{target_slot}'. New AC: {character_state['ac']}.")
    return True, ""


def unequip_item(item_id: str, character_state: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Unequip an item in character inventory.
    Spec Section 7b:
    - Set 'equipped': False (no-op success if already unequipped).
    - Recompute character_state['ac'].
    - Return (True, "") or (False, reason).
    """
    inventory = character_state.get("inventory", [])
    target_item = None
    for item in inventory:
        if isinstance(item, dict) and item.get("item_id") == item_id:
            target_item = item
            break

    if target_item is None:
        return False, f"Item '{item_id}' not found in inventory."

    target_item["equipped"] = False
    character_state["ac"] = _compute_ac(character_state)
    logger.debug(f"unequip_item: unequipped '{item_id}'. New AC: {character_state['ac']}.")
    return True, ""


def use_consumable(item_id: str, character_state: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Use a consumable item (e.g. healing potion) from inventory.
    Spec Section 7b:
    - Works identically in or out of combat.
    - Validate item exists in inventory, type == 'consumable'.
    - Apply effect (e.g. 'heal': roll_dice(effects['heal']), add to HP capped at max).
    - Decrement quantity; remove item from inventory if quantity hits 0.
    - Return (True, "", {"healed": N, "hp_now": X}) or (False, reason, {}).
    """
    inventory = character_state.get("inventory", [])
    target_item = None
    for item in inventory:
        if isinstance(item, dict) and item.get("item_id") == item_id:
            target_item = item
            break

    if target_item is None:
        return False, f"Item '{item_id}' not found in inventory.", {}

    catalog = _get_item_catalog()
    item_info = catalog.get(item_id)
    if not item_info:
        return False, f"Item '{item_id}' not found in item catalog.", {}

    if item_info.get("type") != "consumable":
        return False, f"Item '{item_id}' is not a consumable item.", {}

    qty = target_item.get("quantity", 1)
    if qty <= 0:
        return False, f"Item '{item_id}' quantity is 0.", {}

    effects = item_info.get("effects", {})
    result_dict: Dict[str, Any] = {}

    if "heal" in effects:
        from combat_manager import roll_dice
        heal_expr = effects["heal"]
        heal_amount = roll_dice(heal_expr)

        hp = character_state.setdefault("hp", {"current": 10, "max": 10})
        old_hp = hp.get("current", 0)
        max_hp = hp.get("max", 10)
        new_hp = min(max_hp, old_hp + heal_amount)
        actual_healed = new_hp - old_hp
        hp["current"] = new_hp

        result_dict["healed"] = actual_healed
        result_dict["hp_now"] = new_hp

    # Decrement quantity and remove if 0
    target_item["quantity"] = qty - 1
    if target_item["quantity"] <= 0:
        inventory.remove(target_item)

    logger.debug(f"use_consumable: used '{item_id}'. Result: {result_dict}.")
    return True, "", result_dict



# ════════════════════════════════════════════════════════════════════════════════
# SAVE FILE I/O
# ════════════════════════════════════════════════════════════════════════════════

def _save_path(character_name: str) -> str:
    return os.path.join(SAVES_DIR, f"{character_name}.json")

def _world_save_path(character_name: str) -> str:
    return os.path.join(WORLD_SAVES_DIR, f"{character_name}_world.json")

def _backup_dir(character_name: str) -> str:
    return os.path.join(BACKUP_DIR, character_name)


def load_character(character_name: str) -> Dict[str, Any]:
    """
    Load a character save. Raises ValueError on schema version mismatch
    rather than crashing obscurely (spec Section 5a).
    """
    path = _save_path(character_name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"No save found for '{character_name}' at {path}")
    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f)
    version = state.get("schema_version")
    if version != SUPPORTED_SCHEMA_VERSION:
        raise ValueError(
            f"Save file schema_version={version} is not supported. "
            f"This engine requires schema_version={SUPPORTED_SCHEMA_VERSION}. "
            f"Do not try to load old saves — the format changed."
        )
    return state


def load_world(character_name: str) -> Dict[str, Any]:
    """Load world state. Raises ValueError on schema version mismatch."""
    path = _world_save_path(character_name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"No world save found for '{character_name}' at {path}")
    with open(path, "r", encoding="utf-8") as f:
        world = json.load(f)
    version = world.get("schema_version")
    if version != SUPPORTED_SCHEMA_VERSION:
        raise ValueError(
            f"World save schema_version={version} not supported. "
            f"Requires schema_version={SUPPORTED_SCHEMA_VERSION}."
        )
    return world


def _atomic_write(path: str, data: Dict[str, Any]):
    """Write JSON atomically using a temp file + os.replace() (spec Section 5c)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    dir_ = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _rotate_backup(character_name: str, state: Dict[str, Any]):
    """Keep last MAX_BACKUPS autosaves in save_backups/<name>/ (spec Section 5d)."""
    backup_dir = _backup_dir(character_name)
    os.makedirs(backup_dir, exist_ok=True)
    # Shift existing backups up by one
    for i in range(MAX_BACKUPS - 1, 0, -1):
        src = os.path.join(backup_dir, f"backup_{i}.json")
        dst = os.path.join(backup_dir, f"backup_{i + 1}.json")
        if os.path.exists(src):
            # Remove oldest if at cap
            if i + 1 > MAX_BACKUPS:
                os.unlink(src)
            else:
                os.replace(src, dst)
    # Write current state as backup_1
    _atomic_write(os.path.join(backup_dir, "backup_1.json"), state)


def save_character(character_name: str, state: Dict[str, Any]):
    """Atomic save + rolling backup rotation."""
    _rotate_backup(character_name, state)
    _atomic_write(_save_path(character_name), state)


def save_world(character_name: str, world: Dict[str, Any]):
    """Atomic world save (no backup rotation — backup covers char sheet only)."""
    _atomic_write(_world_save_path(character_name), world)


# ════════════════════════════════════════════════════════════════════════════════
# CORE MATH
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


# ─── Inspiration Points Constants & System (Module B §1 / Phase 12.1) ─────────
MAX_INSPIRATION = 4

def award_inspiration(player_state: Dict[str, Any], reason: str = "") -> bool:
    """
    Award 1 Inspiration Point to the player (Module B §1 / Phase 12.1).
    Capped at MAX_INSPIRATION (4). Returns True if awarded, False if already at cap.
    """
    if "inspiration" not in player_state:
        player_state["inspiration"] = 0

    if isinstance(player_state["inspiration"], dict):
        cur = player_state["inspiration"].get("current", 0)
        max_cap = player_state["inspiration"].get("max", MAX_INSPIRATION)
        if cur >= max_cap:
            logger.debug(f"award_inspiration: at cap ({cur}/{max_cap}) — no-op.")
            return False
        player_state["inspiration"]["current"] = cur + 1
        logger.info(f"award_inspiration: +1 ({cur+1}/{max_cap}). Reason: {reason}")
        return True
    else:
        cur = int(player_state["inspiration"])
        max_cap = player_state.get("max_inspiration", MAX_INSPIRATION)
        if cur >= max_cap:
            logger.debug(f"award_inspiration: at cap ({cur}/{max_cap}) — no-op.")
            return False
        player_state["inspiration"] = cur + 1
        logger.info(f"award_inspiration: +1 ({cur+1}/{max_cap}). Reason: {reason}")
        return True


def spend_inspiration(player_state: Dict[str, Any]) -> bool:
    """
    Spend 1 Inspiration Point from player_state (Module B §1 / Phase 12.1).
    Returns True if successfully consumed, False if 0 points available.
    """
    if "inspiration" not in player_state:
        player_state["inspiration"] = 0

    if isinstance(player_state["inspiration"], dict):
        cur = player_state["inspiration"].get("current", 0)
        if cur <= 0:
            return False
        player_state["inspiration"]["current"] = cur - 1
        logger.info(f"spend_inspiration: consumed 1 point ({cur-1} remaining).")
        return True
    else:
        cur = int(player_state["inspiration"])
        if cur <= 0:
            return False
        player_state["inspiration"] = cur - 1
        logger.info(f"spend_inspiration: consumed 1 point ({cur-1} remaining).")
        return True


def get_inspiration(player_state: Dict[str, Any]) -> int:
    """Return the current number of inspiration points."""
    val = player_state.get("inspiration", 0)
    if isinstance(val, dict):
        return int(val.get("current", 0))
    return int(val)


def check_inspiration_trigger(
    character_state: Dict[str, Any],
    trigger_type: str,
    details: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Check if a game event satisfies the character's background inspiration trigger (Module B §1 / Phase 12.1).
    - Criminal: successful theft / lockpick / Sleight of Hand / Stealth
    - Soldier: won combat without losing a teammate, or successful tactical Shove
    - Sage: successful Arcana check
    - Acolyte: Religion / Insight check
    - Folk Hero: Survival / Animal Handling check
    - Noble: Persuasion / History check
    - Entertainer: Performance / Acrobatics check
    """
    if details is None:
        details = {}

    bg = str(character_state.get("background", "")).strip().lower().replace(" ", "_").replace("-", "_")

    if bg == "criminal":
        if trigger_type in ("theft", "lockpick", "sleight_of_hand", "stealth") and details.get("success", True):
            return award_inspiration(character_state, "Criminal: successful theft or lockpicking")
    elif bg == "soldier":
        if trigger_type == "combat_victory_no_casualties":
            return award_inspiration(character_state, "Soldier: won battle without losing a teammate")
        elif trigger_type == "shove_assist" and details.get("success", True):
            return award_inspiration(character_state, "Soldier: tactical shove assist")
    elif bg == "sage":
        if trigger_type == "arcana" and details.get("success", True):
            return award_inspiration(character_state, "Sage: solved an Arcana check")
    elif bg == "acolyte":
        if trigger_type in ("religion", "insight") and details.get("success", True):
            return award_inspiration(character_state, "Acolyte: upheld religious rites")
    elif bg == "folk_hero":
        if trigger_type in ("survival", "animal_handling") and details.get("success", True):
            return award_inspiration(character_state, "Folk Hero: heroic feat of survival")
    elif bg == "noble":
        if trigger_type in ("persuasion", "history") and details.get("success", True):
            return award_inspiration(character_state, "Noble: diplomatic leadership")
    elif bg == "entertainer":
        if trigger_type in ("performance", "acrobatics") and details.get("success", True):
            return award_inspiration(character_state, "Entertainer: crowd-pleasing performance")

    return False


def resolve_check(
    stat: str,
    difficulty: str,
    state: Dict[str, Any],
    advantage: bool = False,
    disadvantage: bool = False,
    proficient: bool = False,
    bonus_dice: Optional[str] = None,
    skill: Optional[str] = None,
    use_inspiration: bool = False,
) -> Dict[str, Any]:
    """
    Resolve a D&D 5e ability check using the difficulty enum.

    The LLM only ever provides a difficulty string ('easy'|'medium'|'hard'|'very_hard').
    Python maps it to a DC here — the LLM never touches a raw DC number.

    Rules:
      - Nat 20 (before modifiers) ALWAYS succeeds, regardless of DC or modifiers.
      - Nat 1 (before modifiers) ALWAYS fails, regardless of DC or modifiers.
      - Advantage: roll twice, take higher.
      - Disadvantage: roll twice, take lower.
      - Advantage and disadvantage cancel out (roll once, no modifier).
      - use_inspiration: spends 1 point and rerolls, taking the higher roll.

    Returns dict with keys: roll, modifier, proficiency, bonus, total, dc, success, critical, fumble.
    """
    if difficulty not in DIFFICULTY_TO_DC:
        raise ValueError(
            f"Unknown difficulty '{difficulty}'. Must be one of: {list(DIFFICULTY_TO_DC.keys())}"
        )
    dc = DIFFICULTY_TO_DC[difficulty]

    stats = state.get("stats", {})
    stat_value = stats.get(stat, 10)
    modifier = get_modifier(stat_value)

    if skill and not proficient:
        if is_proficient(skill, state):
            proficient = True

    prof_bonus = state.get("proficiency_bonus", 2)
    prof_contribution = prof_bonus if proficient else 0

    inspiration_used = False
    if use_inspiration and spend_inspiration(state):
        inspiration_used = True

    # Resolve advantage/disadvantage — they cancel if both are true
    if advantage and not disadvantage:
        roll1, roll2 = _roll_d20(), _roll_d20()
        roll = max(roll1, roll2)
    elif disadvantage and not advantage:
        roll1, roll2 = _roll_d20(), _roll_d20()
        roll = min(roll1, roll2)
    else:
        roll = _roll_d20()

    if inspiration_used:
        reroll = _roll_d20()
        roll = max(roll, reroll)

    # Nat 20 / nat 1 are checked on the raw die, before any modifiers
    critical = (roll == 20)
    fumble   = (roll == 1)

    # Bonus dice (e.g. Bardic Inspiration 1d6)
    bonus = _roll_dice(bonus_dice) if bonus_dice else 0

    total = roll + modifier + prof_contribution + bonus

    # Nat 20 always succeeds, nat 1 always fails — these override total vs DC
    if critical:
        success = True
    elif fumble:
        success = False
    else:
        success = total >= dc

    if success and skill:
        trigger = str(skill).strip().lower().replace(" ", "_")
        check_inspiration_trigger(state, trigger, {"success": True})

    return {
        "roll":              roll,
        "modifier":          modifier,
        "proficiency":       prof_contribution,
        "bonus":             bonus,
        "total":             total,
        "dc":                dc,
        "difficulty":        difficulty,
        "stat":              stat,
        "skill":             skill,
        "success":           success,
        "critical":          critical,
        "fumble":            fumble,
        "inspiration_spent": inspiration_used,
    }


def reroll_check(
    previous_result: Dict[str, Any],
    state: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Spend 1 inspiration point to reroll an ability check (Module B §1 / Phase 12.1).
    Takes the higher of the previous roll and the new roll.
    """
    if not spend_inspiration(state):
        res = dict(previous_result)
        res["rerolled"] = False
        res["reason"] = "No inspiration points available."
        return res

    new_roll = _roll_d20()
    effective_roll = max(previous_result.get("roll", 1), new_roll)
    total = effective_roll + previous_result.get("modifier", 0) + previous_result.get("proficiency", 0) + previous_result.get("bonus", 0)

    critical = (effective_roll == 20)
    fumble = (effective_roll == 1)
    if critical:
        success = True
    elif fumble:
        success = False
    else:
        success = total >= previous_result.get("dc", 10)

    res = dict(previous_result)
    res.update({
        "roll":              effective_roll,
        "raw_reroll":        new_roll,
        "total":             total,
        "success":           success,
        "critical":          critical,
        "fumble":            fumble,
        "rerolled":          True,
        "inspiration_spent": True,
    })
    if success and res.get("skill"):
        trigger = str(res["skill"]).strip().lower().replace(" ", "_")
        check_inspiration_trigger(state, trigger, {"success": True})
    return res


def resolve_death_save(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Roll a death saving throw for a downed character.
    DC 10. Nat 20 = stabilize immediately (success=True, stabilized=True).
    Nat 1 = two failures (counts double).
    Three successes = stabilized. Three failures = death saves exhausted
    (resolve_downed_outcome fires in Phase 8 — not game-over).

    Mutates state["death_saves"] in place and returns a result dict.
    """
    roll = _roll_d20()
    death_saves = state.setdefault("death_saves", {"success": 0, "fail": 0})

    stabilized = False
    exhausted  = False

    if roll == 20:
        # Nat 20: immediate stabilize, regain 1 HP
        stabilized = True
        death_saves["success"] = 0
        death_saves["fail"]    = 0
        state["hp"]["current"] = 1
        state["status"]        = "normal"
        success = True
    elif roll == 1:
        # Nat 1: two failures
        death_saves["fail"] = min(3, death_saves["fail"] + 2)
        success = False
    elif roll >= 10:
        death_saves["success"] = min(3, death_saves["success"] + 1)
        success = True
    else:
        death_saves["fail"] = min(3, death_saves["fail"] + 1)
        success = False

    if death_saves["success"] >= 3 and not stabilized:
        stabilized = True
        death_saves["success"] = 0
        death_saves["fail"]    = 0
        state["status"]        = "normal"

    if death_saves["fail"] >= 3:
        exhausted = True  # resolve_downed_outcome fires (Phase 8)

    return {
        "roll":        roll,
        "dc":          10,
        "success":     success,
        "critical":    roll == 20,
        "fumble":      roll == 1,
        "stabilized":  stabilized,
        "exhausted":   exhausted,
        "death_saves": dict(death_saves),
    }


def resolve_concentration_check(damage_taken: int, state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Auto-triggered inside apply_state_updates() whenever hp_change < 0
    and state['concentration'] is not None.

    DC = max(10, damage_taken // 2)  [spec Section 8 / PART 5a]
    Uses CON saving throw. Character is proficient if CON is in proficient_saves.
    On failure: concentration spell is cleared.

    Returns the check result dict (same shape as resolve_check).
    """
    dc_value = max(CONCENTRATION_DC_FLOOR, damage_taken // 2)

    stats     = state.get("stats", {})
    con_value = stats.get("CON", 10)
    modifier  = get_modifier(con_value)
    proficient_saves = state.get("proficient_saves", [])
    proficient = "CON" in proficient_saves
    prof_bonus = state.get("proficiency_bonus", 2)
    prof_contribution = prof_bonus if proficient else 0

    roll = _roll_d20()
    critical = (roll == 20)
    fumble   = (roll == 1)
    total    = roll + modifier + prof_contribution

    if critical:
        success = True
    elif fumble:
        success = False
    else:
        success = total >= dc_value

    if not success:
        logger.debug(
            f"Concentration check failed (roll={roll}, total={total}, dc={dc_value}). "
            f"Clearing concentration spell: {state.get('concentration')}"
        )
        state["concentration"] = None

    return {
        "roll":        roll,
        "modifier":    modifier,
        "proficiency": prof_contribution,
        "bonus":       0,
        "total":       total,
        "dc":          dc_value,
        "stat":        "CON",
        "success":     success,
        "critical":    critical,
        "fumble":      fumble,
        "concentration_broken": not success,
    }


def log_roll(entry: Dict[str, Any], state: Dict[str, Any]):
    """
    Append a roll result to state['roll_log'].
    Keeps the last 50 entries to avoid unbounded growth.
    """
    roll_log = state.setdefault("roll_log", [])
    roll_log.append(entry)
    if len(roll_log) > 50:
        state["roll_log"] = roll_log[-50:]


def apply_state_updates(updates: Dict[str, Any], state: Dict[str, Any],
                        world_state: Optional[Dict[str, Any]] = None
                        ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    Apply validated extraction-call updates to the character state (and optionally world state).

    IMPORTANT: This function must only ever be called with output that has already passed
    validate_extraction_output() in validation.py — never with raw LLM output.

    Handles: hp_change (with auto concentration check), gold_change, add_item_id,
             remove_item_id, move_to_location_id.
    Quest logic lives exclusively in apply_quest_updates() — not here.

    Returns: (updated state, concentration_check_result | None)
    """
    concentration_result = None

    # ── HP change ─────────────────────────────────────────────────────────────
    if "hp_change" in updates:
        delta = updates["hp_change"]
        hp = state.setdefault("hp", {"current": 10, "max": 10})
        old_hp = hp["current"]
        hp["current"] = max(0, min(hp["max"], old_hp + delta))

        # Auto-trigger concentration check on any damage (spec Section 8)
        if delta < 0 and state.get("concentration") is not None:
            damage_taken = abs(delta)
            concentration_result = resolve_concentration_check(damage_taken, state)
            log_roll({"type": "concentration_check", **concentration_result}, state)

        # Downed at 0 HP
        if hp["current"] == 0 and state.get("status") == "normal":
            state["status"] = "downed"
            logger.debug(f"Character downed at 0 HP.")

    # ── Gold change ───────────────────────────────────────────────────────────
    if "gold_change" in updates:
        state["gold"] = max(0, state.get("gold", 0) + updates["gold_change"])

    # ── Add item ──────────────────────────────────────────────────────────────
    if "add_item_id" in updates:
        item_id = updates["add_item_id"]
        inventory = state.setdefault("inventory", [])
        # Check if already carrying (stack consumables, don't duplicate wearables)
        existing = next((i for i in inventory if i.get("item_id") == item_id), None)
        if existing and existing.get("quantity") is not None:
            existing["quantity"] = existing.get("quantity", 1) + 1
        elif not existing:
            inventory.append({"item_id": item_id, "equipped": False, "quantity": 1})

    # ── Remove item ───────────────────────────────────────────────────────────
    if "remove_item_id" in updates:
        item_id = updates["remove_item_id"]
        inventory = state.get("inventory", [])
        for i, item in enumerate(inventory):
            if item.get("item_id") == item_id:
                qty = item.get("quantity", 1)
                if qty > 1:
                    item["quantity"] = qty - 1
                else:
                    inventory.pop(i)
                break

    # ── Location change (move_to_location_id) ─────────────────────────────────
    # Renamed from 'new_location' to avoid collision with world_updates.new_location
    # (which registers a brand-new room). Validation.py already verified the room exists.
    if world_state is not None and "move_to_location_id" in updates:
        dest_id = updates["move_to_location_id"]
        world_state["current_location"] = dest_id
        visited = world_state.setdefault("visited_rooms", [])
        if dest_id not in visited:
            visited.append(dest_id)

    return state, concentration_result


def apply_quest_updates(
    quest_updates: Dict[str, Any],
    world_state: Dict[str, Any],
) -> None:
    """
    Apply a validated quest_updates dict (from validate_quest_updates()) to world_state.
    This is the ONLY code path that writes to quest_log — not apply_state_updates().

    quest_updates shape (spec Section 12b):
      new_quest        (dict, optional) — {title, quest_type ('main'|'side'), description}
      objective_update (dict, optional) — {quest_id, objective_index, done}

    Caller: app.py game loop (Phase 7) calls this AFTER apply_state_updates().
    Must only be called with output from validate_quest_updates() — never raw LLM output.
    """
    quest_log = world_state.setdefault("quest_log", {"main": [], "side": []})

    if "new_quest" in quest_updates:
        nq = quest_updates["new_quest"]
        quest_type = nq.get("quest_type", "side")
        # Generate a stable slug id so objective_update can match by id (not just title).
        # Format: "q_" + lowercase alphanumeric slug of the title.
        # Collision-safe: if the same slug already exists in quest_log, append _2, _3, etc.
        import re as _re
        base_slug = "q_" + _re.sub(r"[^a-z0-9]+", "_", nq["title"].lower()).strip("_")
        existing_ids = {
            q.get("id")
            for cat in ("main", "side")
            for q in quest_log.get(cat, [])
            if q.get("id")
        }
        quest_id = base_slug
        suffix = 2
        while quest_id in existing_ids:
            quest_id = f"{base_slug}_{suffix}"
            suffix += 1
        new_entry = {
            "id":          quest_id,
            "title":       nq["title"],
            "status":      "active",
            "description": nq.get("description", ""),
            "objectives":  [],
        }
        quest_log.setdefault(quest_type, []).append(new_entry)
        logger.debug(f"apply_quest_updates: added '{nq['title']}' (id='{quest_id}') to {quest_type} log.")

    if "objective_update" in quest_updates:
        ou = quest_updates["objective_update"]
        qid       = ou["quest_id"]
        obj_idx   = ou["objective_index"]
        done      = ou["done"]
        found = False
        for category in ("main", "side"):
            for quest in quest_log.get(category, []):
                if quest.get("title") == qid or quest.get("id") == qid:
                    objectives = quest.setdefault("objectives", [])
                    # Extend list if objective_index is beyond current length
                    while len(objectives) <= obj_idx:
                        objectives.append({"description": "", "done": False})
                    objectives[obj_idx]["done"] = done
                    found = True
                    logger.debug(
                        f"apply_quest_updates: objective[{obj_idx}] of '{qid}' set done={done}."
                    )
                    break
            if found:
                break
        if not found:
            logger.debug(f"apply_quest_updates: quest_id '{qid}' not found in quest_log — no-op.")


# ════════════════════════════════════════════════════════════════════════════════
# STUB FUNCTIONS — implemented in later phases
# ════════════════════════════════════════════════════════════════════════════════

LEVEL_THRESHOLDS: Dict[int, int] = {1: 0, 2: 300, 3: 900, 4: 2700, 5: 6500}

def award_xp(amount: int, state: Dict[str, Any]) -> int:
    """
    Add XP to state["xp_current"]. Idempotent-safety (no double-award) is caller's responsibility.
    Negative or zero amounts are clean no-ops.
    """
    if amount <= 0:
        return state.get("xp_current", 0)
    current_xp = state.get("xp_current", 0)
    new_xp = current_xp + amount
    state["xp_current"] = new_xp
    logger.debug(f"award_xp: awarded {amount} XP. Total now: {new_xp}")
    return new_xp


def check_level_up(state: Dict[str, Any]) -> bool:
    """
    Check if state["xp_current"] crosses threshold for level + 1 (capped at level 5).
    """
    lvl = state.get("level", 1)
    if lvl >= 5:
        return False
    threshold = LEVEL_THRESHOLDS.get(lvl + 1, 999999)
    return state.get("xp_current", 0) >= threshold


def apply_level_up(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Increments level, updates proficiency_bonus, hit points (max and current),
    and spell slots for spellcasting classes. Safely callable in a loop.
    """
    if not check_level_up(state):
        return {"leveled_up": False, "level": state.get("level", 1)}

    old_lvl = state.get("level", 1)
    new_lvl = min(5, old_lvl + 1)
    state["level"] = new_lvl

    # Update proficiency bonus
    state["proficiency_bonus"] = 3 if new_lvl == 5 else 2

    # Update Hit Points
    cls_name = state.get("class", state.get("class_name", "Fighter"))
    if cls_name in ("Fighter", "Paladin"):
        hit_die_avg = 6
    elif cls_name == "Barbarian":
        hit_die_avg = 7
    elif cls_name == "Wizard":
        hit_die_avg = 4
    else:  # Rogue, Bard, Cleric, Monk, Druid, Ranger, etc.
        hit_die_avg = 5

    con_mod = get_modifier(state.get("stats", {}).get("CON", 10))
    hp_gain = max(1, hit_die_avg + con_mod)

    hp = state.setdefault("hp", {"current": 10, "max": 10})
    hp["max"] = hp.get("max", 10) + hp_gain
    hp["current"] = hp.get("current", 10) + hp_gain

    # Update spell slots for casters
    if cls_name in ("Bard", "Cleric", "Wizard"):
        slots = state.setdefault("spell_slots", {})
        target_slots_table = {
            1: {"1": 2},
            2: {"1": 3},
            3: {"1": 4, "2": 2},
            4: {"1": 4, "2": 3},
            5: {"1": 4, "2": 3, "3": 2},
        }
        target_cfg = target_slots_table.get(new_lvl, {"1": 2})
        for tier_str, target_max in target_cfg.items():
            curr_info = slots.setdefault(tier_str, {"max": 0, "current": 0})
            old_max = curr_info.get("max", 0)
            delta = max(0, target_max - old_max)
            curr_info["max"] = target_max
            curr_info["current"] = curr_info.get("current", 0) + delta

    logger.info(f"apply_level_up: {state.get('name')} reached Level {new_lvl}! HP +{hp_gain} (max={hp['max']}).")
    return {
        "leveled_up": True,
        "old_level":  old_lvl,
        "new_level":  new_lvl,
        "hp_gain":    hp_gain,
        "hp_max":     hp["max"],
        "proficiency_bonus": state["proficiency_bonus"],
    }

def handle_period_change(
    world_state: Dict[str, Any],
    character_state: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    Spec Section 22d:
    Called when advance_time() triggers period_changed == True.
    If player is outside town, consumes 1 ration item. If none available, applies 'exhausted'.
    """
    import dungeon_manager
    current_room = dungeon_manager.get_current_room(world_state)
    if current_room and current_room.get("type") == "town":
        return True, "In town — no rations consumed."

    catalog = _get_item_catalog()
    inventory = character_state.get("inventory", [])
    ration_item = None
    for item in inventory:
        if isinstance(item, dict):
            item_id = item.get("item_id")
            info = catalog.get(item_id, {})
            if info.get("type") == "ration" and item.get("quantity", 0) > 0:
                ration_item = item
                break

    active_conditions = character_state.setdefault("active_conditions", [])

    if ration_item:
        qty = ration_item.get("quantity", 1)
        if qty > 1:
            ration_item["quantity"] = qty - 1
        else:
            inventory.remove(ration_item)
        if "exhausted" in active_conditions:
            active_conditions.remove("exhausted")
            logger.debug("Consumed ration — removed exhausted condition.")
        return True, "Consumed a ration."
    else:
        if "exhausted" not in active_conditions:
            active_conditions.append("exhausted")
            logger.debug("No rations — applied exhausted condition.")
        return False, "Out of rations! You are now exhausted."


def long_rest(
    world_state: Dict[str, Any],
    character_state: Dict[str, Any],
) -> None:
    """
    Spec Section 22c:
    Hard overwrite game_time to morning of next day, restore HP to max, clear exhausted.
    """
    gt = world_state.setdefault("game_time", {"day": 1, "period": "morning", "steps_since_period_start": 0})
    gt["period"] = "morning"
    gt["day"] = gt.get("day", 1) + 1
    gt["steps_since_period_start"] = 0

    hp = character_state.setdefault("hp", {"current": 10, "max": 10})
    hp["current"] = hp.get("max", 10)

    active_conditions = character_state.setdefault("active_conditions", [])
    if "exhausted" in active_conditions:
        active_conditions.remove("exhausted")

    character_state["weapon_actions_available"] = True
    logger.debug("Long rest completed. HP restored, time advanced to next morning, weapon actions recharged.")


def perform_short_rest(
    character_state: Dict[str, Any],
    world_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Spec Module A §3 / Phase 11.3:
    Perform a short rest.
    Resets character_state['weapon_actions_available'] = True.
    If world_state is provided, advances time by 1 step.
    """
    character_state["weapon_actions_available"] = True
    if world_state:
        import dungeon_manager
        dungeon_manager.advance_time(world_state, character_state)
    logger.debug("perform_short_rest: weapon_actions_available reset to True.")
    return {
        "success": True,
        "weapon_actions_available": True,
        "message": "You take a short rest. Your weapon actions are recharged!",
    }


def buy_item(
    item_id: str,
    shop_id: str,
    character_state: Dict[str, Any],
    world_state: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """
    Buy an item from a shop (spec Section 19b & Section 22f).
    Checks shop hours (open_periods against world_state["game_time"]["period"]).
    Deducts gold (value_gold * sell_multiplier) and adds item to inventory.
    """
    shops = _get_shop_catalog()
    shop = shops.get(shop_id)
    if not shop:
        return False, f"Shop '{shop_id}' not found."

    # Shop hours check (spec Section 22f)
    if world_state and "game_time" in world_state:
        period = world_state["game_time"].get("period")
        open_periods = shop.get("open_periods")
        if open_periods and period not in open_periods:
            return False, f"The shop '{shop.get('name', shop_id)}' is closed for the {period}."

    sell_items = shop.get("sell_items", [])
    if item_id not in sell_items:
        return False, f"Shop '{shop.get('name', shop_id)}' does not sell '{item_id}'."

    catalog = _get_item_catalog()
    item_info = catalog.get(item_id)
    if not item_info:
        return False, f"Item '{item_id}' not found in catalog."

    sell_mult = shop.get("sell_multiplier", 1.0)
    cost = math.ceil(item_info.get("value_gold", 0) * sell_mult)

    current_gold = character_state.get("gold", 0)
    if current_gold < cost:
        return False, f"Not enough gold. Costs {cost} GP, you have {current_gold} GP."

    character_state["gold"] = current_gold - cost

    # Add item to inventory
    inventory = character_state.setdefault("inventory", [])
    existing = next((i for i in inventory if isinstance(i, dict) and i.get("item_id") == item_id), None)
    if existing and existing.get("quantity") is not None:
        existing["quantity"] = existing.get("quantity", 1) + 1
    elif not existing:
        inventory.append({"item_id": item_id, "equipped": False, "quantity": 1})

    logger.debug(f"buy_item: bought '{item_id}' from '{shop_id}' for {cost} GP. Gold remaining: {character_state['gold']}.")
    return True, f"Bought {item_info.get('name', item_id)} for {cost} GP."


def sell_item(
    item_id: str,
    shop_id: str,
    character_state: Dict[str, Any],
    world_state: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """
    Sell an item to a shop (spec Section 19b & Section 22f).
    Checks shop hours. Adds gold (value_gold * buy_multiplier) and removes item from inventory.
    """
    shops = _get_shop_catalog()
    shop = shops.get(shop_id)
    if not shop:
        return False, f"Shop '{shop_id}' not found."

    # Shop hours check (spec Section 22f)
    if world_state and "game_time" in world_state:
        period = world_state["game_time"].get("period")
        open_periods = shop.get("open_periods")
        if open_periods and period not in open_periods:
            return False, f"The shop '{shop.get('name', shop_id)}' is closed for the {period}."

    inventory = character_state.get("inventory", [])
    target_item = None
    for item in inventory:
        if isinstance(item, dict) and item.get("item_id") == item_id:
            target_item = item
            break

    if target_item is None:
        return False, f"You don't have '{item_id}' to sell."

    catalog = _get_item_catalog()
    item_info = catalog.get(item_id, {})
    buy_mult = shop.get("buy_multiplier", 0.5)
    gain = math.floor(item_info.get("value_gold", 0) * buy_mult)

    # Decrement/remove item
    qty = target_item.get("quantity", 1)
    if qty > 1:
        target_item["quantity"] = qty - 1
    else:
        inventory.remove(target_item)

    character_state["gold"] = character_state.get("gold", 0) + gain
    character_state["ac"] = _compute_ac(character_state)

    logger.debug(f"sell_item: sold '{item_id}' to '{shop_id}' for {gain} GP. Gold now: {character_state['gold']}.")
    return True, f"Sold {item_info.get('name', item_id)} for {gain} GP."

_spell_catalog: Optional[Dict[str, Any]] = None

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


def resolve_spell_save(
    caster: Dict[str, Any],
    target: Dict[str, Any],
    spell: Dict[str, Any],
    state: Optional[Dict[str, Any]] = None,
    combat_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Spec Section 8b / 18b / PART 4b:
    Resolve a save-forcing spell (e.g. Vicious Mockery, Sacred Flame, Thunderwave).
    Save DC = 8 + caster's proficiency_bonus + caster's spellcasting ability modifier.
    Target rolls the d20 save against this DC.
    """
    caster_prof = caster.get("proficiency_bonus", 2)
    c_class = caster.get("class", caster.get("class_name", "Bard"))

    if c_class in ("Wizard",):
        cast_stat = "INT"
    elif c_class in ("Cleric", "Druid"):
        cast_stat = "WIS"
    else:
        cast_stat = "CHA"

    cast_mod = get_modifier(caster.get("stats", {}).get(cast_stat, 10))
    dc = 8 + caster_prof + cast_mod

    save_stat = spell.get("save_stat", "DEX")
    target_stats = target.get("stats", {})
    t_mod = get_modifier(target_stats.get(save_stat, 10))
    t_prof_saves = target.get("proficient_saves", [])
    t_prof_bonus = target.get("proficiency_bonus", 2) if save_stat in t_prof_saves else 0

    roll = _roll_d20()
    critical = (roll == 20)
    fumble   = (roll == 1)
    total    = roll + t_mod + t_prof_bonus

    if critical:
        success = True
    elif fumble:
        success = False
    else:
        success = total >= dc

    damage = 0
    condition_applied = None
    if not success:
        eff = spell.get("effect", {})
        if "damage" in eff:
            damage = _roll_dice(eff["damage"])
            target_hp = target.setdefault("hp", {"current": 10, "max": 10})
            target_hp["current"] = max(0, target_hp.get("current", 0) - damage)

            if damage > 0 and eff.get("damage_type") == "lightning" and combat_state:
                surface = combat_state.get("room_surface", {})
                if surface.get("type") == "water":
                    import combat_manager
                    combat_manager.trigger_lightning_surface_reaction(combat_state)
        if spell.get("on_fail_extra"):
            condition_applied = spell["on_fail_extra"]

    return {
        "attacker_id":   caster.get("id", "player"),
        "attacker_name": caster.get("name", "Caster"),
        "caster_name":   caster.get("name", "Caster"),
        "target_id":     target.get("id", "unknown"),
        "target_name":   target.get("name", "Target"),
        "spell_name":    spell.get("name", "Spell"),
        "attack_name":   spell.get("name", "Spell"),
        "dc":            dc,
        "roll":          roll,
        "modifier":      t_mod + t_prof_bonus,
        "total":         total,
        "success":       success,
        "critical":      critical,
        "fumble":        fumble,
        "damage":        damage,
        "condition_applied": condition_applied,
        "target_hp_after": target.get("hp", {}).get("current", 0),
        "target_downed": target.get("hp", {}).get("current", 0) <= 0,
    }


def resolve_spell_attack(
    caster: Dict[str, Any],
    target: Dict[str, Any],
    spell: Dict[str, Any],
    combat_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Spec Section 18b / PART 4b:
    Resolve an attack-roll spell (e.g. Fire Bolt, Guiding Bolt, Magic Missile).
    Caster rolls d20 + proficiency_bonus + spellcasting_ability_mod vs target AC.
    Magic Missile has auto_hit: True (bypasses attack roll).
    """
    caster_prof = caster.get("proficiency_bonus", 2)
    c_class = caster.get("class", caster.get("class_name", "Wizard"))
    if c_class in ("Wizard",):
        cast_stat = "INT"
    elif c_class in ("Cleric", "Druid"):
        cast_stat = "WIS"
    else:
        cast_stat = "CHA"
    cast_mod = get_modifier(caster.get("stats", {}).get(cast_stat, 10))
    to_hit_bonus = caster_prof + cast_mod

    # High Ground logic (Phase 11.2)
    caster_high_ground = caster.get("has_high_ground", False)
    target_high_ground = target.get("has_high_ground", False)

    if caster_high_ground:
        to_hit_bonus += 2

    caster_conds = set(c.get("condition") if isinstance(c, dict) else str(c) for c in caster.get("active_conditions", []))
    has_disadvantage = target_high_ground or any(c in caster_conds for c in ("poisoned", "restrained", "exhausted", "frightened", "dazed"))
    has_advantage = False

    if has_advantage and has_disadvantage:
        has_advantage = has_disadvantage = False

    eff = spell.get("effect", {})
    target_ac = target.get("ac", 10)
    target_conds = set(c.get("condition") if isinstance(c, dict) else str(c) for c in target.get("active_conditions", []))
    if "dazed" in target_conds:
        target_ac -= 1

    if eff.get("auto_hit") is True:
        hit = True
        crit = False
        fumble = False
        raw_roll = 20
        total_to_hit = 99
    else:
        if has_advantage:
            raw_roll = max(_roll_d20(), _roll_d20())
        elif has_disadvantage:
            raw_roll = min(_roll_d20(), _roll_d20())
        else:
            raw_roll = _roll_d20()
        crit = (raw_roll == 20)
        fumble = (raw_roll == 1)
        total_to_hit = raw_roll + to_hit_bonus
        if crit:
            hit = True
        elif fumble:
            hit = False
        else:
            hit = total_to_hit >= target_ac

    damage = 0
    if hit:
        dmg_expr = eff.get("damage", "1d6")
        damage = _roll_dice(dmg_expr)
        if crit:
            damage += _roll_dice(dmg_expr)
        target_hp = target.setdefault("hp", {"current": 10, "max": 10})
        target_hp["current"] = max(0, target_hp.get("current", 0) - damage)

        if damage > 0 and eff.get("damage_type") == "lightning" and combat_state:
            surface = combat_state.get("room_surface", {})
            if surface.get("type") == "water":
                import combat_manager
                combat_manager.trigger_lightning_surface_reaction(combat_state)

    return {
        "attacker_id":   caster.get("id", "player"),
        "attacker_name": caster.get("name", "Caster"),
        "caster_name":   caster.get("name", "Caster"),
        "target_id":     target.get("id", "unknown"),
        "target_name":   target.get("name", "Target"),
        "spell_name":    spell.get("name", "Spell"),
        "attack_name":   spell.get("name", "Spell"),
        "raw_roll":      raw_roll,
        "to_hit_bonus":  to_hit_bonus,
        "total_to_hit":  total_to_hit,
        "target_ac":     target_ac,
        "hit":           hit,
        "crit":          crit,
        "fumble":        fumble,
        "damage":        damage,
        "target_hp_after": target.get("hp", {}).get("current", 0),
        "target_downed": target.get("hp", {}).get("current", 0) <= 0,
    }


def cast_spell(
    caster: Dict[str, Any],
    target: Dict[str, Any],
    spell_id: str,
    character_state: Dict[str, Any],
    world_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Cast a spell by spell_id. Validates spell, decrements slots if level > 0, and resolves effect.
    """
    catalog = _get_spell_catalog()
    spell = catalog.get(spell_id)
    if not spell:
        return {"success": False, "reason": f"Unknown spell '{spell_id}'."}

    lvl = spell.get("level", 0)
    if lvl > 0:
        slots = character_state.setdefault("spell_slots", {})
        lvl_str = str(lvl)
        slot_info = slots.get(lvl_str, {"max": 0, "current": 0})
        if slot_info.get("current", 0) <= 0:
            return {"success": False, "reason": f"No level {lvl} spell slots remaining."}
        slot_info["current"] -= 1

    combat_state = world_state.get("combat_state") if world_state else None
    s_type = spell.get("type")
    if s_type == "attack_save":
        res = resolve_spell_save(caster, target, spell, character_state, combat_state=combat_state)
    elif s_type == "attack_roll":
        res = resolve_spell_attack(caster, target, spell, combat_state=combat_state)
    elif s_type == "heal":
        eff = spell.get("effect", {})
        heal_val = _roll_dice(eff.get("heal", "1d4+3"))
        t_hp = target.setdefault("hp", {"current": 10, "max": 10})
        t_hp["current"] = min(t_hp.get("max", 10), t_hp.get("current", 0) + heal_val)
        res = {
            "caster_name": caster.get("name", "Caster"),
            "target_name": target.get("name", "Target"),
            "spell_name":  spell.get("name", "Spell"),
            "healed":      heal_val,
            "target_hp_after": t_hp["current"],
            "success":     True,
        }
    else:
        res = {
            "caster_name": caster.get("name", "Caster"),
            "spell_name":  spell.get("name", "Spell"),
            "success":     True,
            "effect":      spell.get("effect"),
        }

    res["success"] = True
    return res


def resolve_downed_outcome(
    combatant: Dict[str, Any],
    combat_state: Optional[Dict[str, Any]] = None,
    world_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Spec Section 17a (Kenshi-lite downed outcome system).
    When a combatant's HP hits 0 and death saves fail 3 times (or player is defeated):
    Selects one of ['robbed_and_left', 'captured', 'rescued_by_npc'] using context-weighted choice.
    """
    import dungeon_manager

    outcomes = ["robbed_and_left", "captured", "rescued_by_npc"]

    # Calculate contextual weights (spec 17a)
    room = dungeon_manager.get_current_room(world_state) if world_state else None
    room_type = room.get("type", "wilderness") if room else "wilderness"
    is_safe = room.get("is_safe", False) if room else False

    if is_safe or room_type == "town":
        weights = [0.20, 0.10, 0.70]
    elif room_type == "dungeon":
        weights = [0.50, 0.35, 0.15]
    else:
        weights = [0.45, 0.25, 0.30]

    outcome = random.choices(outcomes, weights=weights, k=1)[0]
    penalty: Dict[str, Any] = {
        "gold_lost": 0,
        "items_lost": [],
        "hp_set_to": None,
        "hp_restored": None,
        "status": "normal",
        "relocated_to": None,
    }

    # Reset death saves count
    combatant["death_saves"] = {"success": 0, "fail": 0}

    hp_dict = combatant.setdefault("hp", {"current": 0, "max": 10})
    max_hp = hp_dict.get("max", 10)

    if outcome == "robbed_and_left":
        # Lose portion of gold (50-100%) and all unequipped inventory items
        cur_gold = combatant.get("gold", 0)
        gold_lost = random.randint(math.ceil(cur_gold * 0.5), cur_gold) if cur_gold > 0 else 0
        combatant["gold"] = max(0, cur_gold - gold_lost)

        inventory = combatant.get("inventory", [])
        items_lost = []
        kept_inventory = []
        for item in inventory:
            if isinstance(item, dict) and item.get("equipped") is True:
                kept_inventory.append(item)
            else:
                item_id = item.get("item_id") if isinstance(item, dict) else item
                items_lost.append(item_id)
        combatant["inventory"] = kept_inventory

        # HP set to EXACTLY 1
        hp_dict["current"] = 1
        combatant["status"] = "normal"

        # Relocate to nearest previously-visited safe location
        relocated_to = dungeon_manager.find_nearest_visited_safe_room(world_state) if world_state else "town_riverside"
        if world_state:
            world_state["current_location"] = relocated_to

        penalty["gold_lost"] = gold_lost
        penalty["items_lost"] = items_lost
        penalty["hp_set_to"] = 1
        penalty["status"] = "normal"
        penalty["relocated_to"] = relocated_to

    elif outcome == "captured":
        # Status set to captive (no item loss, no HP change)
        combatant["status"] = "captive"
        if hp_dict.get("current", 0) <= 0:
            hp_dict["current"] = 1  # Consciousness restored at captive location
        penalty["status"] = "captive"
        penalty["hp_set_to"] = hp_dict["current"]

    elif outcome == "rescued_by_npc":
        # Partial HP restored (50% max HP), no item loss
        hp_restored = max(1, max_hp // 2)
        hp_dict["current"] = hp_restored
        combatant["status"] = "normal"

        penalty["hp_restored"] = hp_restored
        penalty["hp_now"] = hp_restored
        penalty["status"] = "normal"

    if combat_state:
        combat_state["status"] = "ended"
        combat_state["outcome"] = "player_defeat"

    logger.info(f"resolve_downed_outcome: outcome='{outcome}', penalty={penalty}")
    return {"outcome": outcome, "penalty": penalty}


def dismiss_companion(npc_id: str, world_state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Spec Section 21 / PART 9:
    Move companion from party.companions to party.former_companions, recording last_location.
    Disallowed during active combat.
    """
    if world_state.get("combat_state") is not None:
        raise ValueError("Cannot dismiss companions during active combat.")

    party = world_state.setdefault("party", {"companions": [], "former_companions": []})
    companions = party.setdefault("companions", [])
    former = party.setdefault("former_companions", [])

    target_idx = None
    target_comp = None
    for idx, comp in enumerate(companions):
        if comp.get("id") == npc_id or comp.get("name") == npc_id:
            target_idx = idx
            target_comp = comp
            break

    if target_comp is None:
        raise ValueError(f"Companion '{npc_id}' is not in active party.")

    companions.pop(target_idx)
    target_comp["last_location"] = world_state.get("current_location", "town_riverside")
    former.append(target_comp)

    logger.info(f"dismiss_companion: '{npc_id}' moved to former_companions at {target_comp['last_location']}.")
    return {"npc_id": npc_id, "dismissed_at": target_comp["last_location"]}


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


import uuid

def generate_item(
    rarity: str,
    world_state: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """
    Spec Section 7c / PART 4a-ii:
    Generate a new item deterministically from a rarity tag ('common', 'uncommon', 'rare').
    Writes to world_state["generated_items"] (never item_catalog.json).
    Returns (item_id, item_dict).
    """
    rarity = rarity.lower().strip() if isinstance(rarity, str) else "common"
    if rarity not in ("common", "uncommon", "rare"):
        rarity = "common"

    rarity_tables = {
        "common":   {"gold": 15, "ac_bonus": 1, "name_suffix": "of Quality"},
        "uncommon": {"gold": 50, "ac_bonus": 1, "saving_throw_bonus": 1, "name_suffix": "of Might"},
        "rare":     {"gold": 200, "ac_bonus": 2, "saving_throw_bonus": 1, "name_suffix": "of Distinction"},
    }
    spec_row = rarity_tables[rarity]

    gen_id = f"gen_{uuid.uuid4().hex[:8]}"
    item_dict = {
        "item_id": gen_id,
        "name": f"Item {spec_row['name_suffix']}",
        "type": "wearable",
        "slot": "ring",
        "rarity": rarity,
        "value_gold": spec_row["gold"],
        "effects": {
            "ac_bonus": spec_row["ac_bonus"],
        }
    }

    if "saving_throw_bonus" in spec_row:
        item_dict["effects"]["saving_throw_bonus"] = spec_row["saving_throw_bonus"]

    gen_items = world_state.setdefault("generated_items", {})
    gen_items[gen_id] = item_dict

    logger.info(f"generate_item: generated item '{gen_id}' ({rarity}) in world_state.")
    return gen_id, item_dict


def apply_condition_to_state(
    character_state: Dict[str, Any],
    condition: str,
    duration_rounds: int = 2,
) -> bool:
    """
    Apply a status condition to character_state. Checks condition immunities.
    """
    immunities = character_state.get("immunities", [])
    if condition in immunities:
        logger.debug(f"apply_condition_to_state: character is immune to '{condition}'.")
        return False

    active_conds = character_state.setdefault("active_conditions", [])
    if isinstance(active_conds, list):
        for entry in active_conds:
            if entry == condition:
                return True
            if isinstance(entry, dict) and entry.get("name") == condition:
                entry["duration"] = max(entry.get("duration", 0), duration_rounds)
                return True
        active_conds.append({"name": condition, "duration": duration_rounds})
    return True
