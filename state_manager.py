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
    return skill_or_save in skills or skill_or_save in saves


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


def resolve_check(
    stat: str,
    difficulty: str,
    state: Dict[str, Any],
    advantage: bool = False,
    disadvantage: bool = False,
    proficient: bool = False,
    bonus_dice: Optional[str] = None,
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

    prof_bonus = state.get("proficiency_bonus", 2)
    prof_contribution = prof_bonus if proficient else 0

    # Resolve advantage/disadvantage — they cancel if both are true
    if advantage and not disadvantage:
        roll1, roll2 = _roll_d20(), _roll_d20()
        roll = max(roll1, roll2)
    elif disadvantage and not advantage:
        roll1, roll2 = _roll_d20(), _roll_d20()
        roll = min(roll1, roll2)
    else:
        roll = _roll_d20()

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

    return {
        "roll":        roll,
        "modifier":    modifier,
        "proficiency": prof_contribution,
        "bonus":       bonus,
        "total":       total,
        "dc":          dc,
        "difficulty":  difficulty,
        "stat":        stat,
        "success":     success,
        "critical":    critical,
        "fumble":      fumble,
    }


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
             remove_item_id, add_quest, remove_quest, new_location.

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

    # ── Quest updates ─────────────────────────────────────────────────────────
    # These arrive via world_state — handled here if world_state is provided
    if world_state is not None:
        quest_log = world_state.setdefault("quest_log", {"main": [], "side": []})

        if "add_quest" in updates:
            # Phase 2 DEV-1 RESOLVED: respect quest_type key ('main' | 'side').
            # add_quest can be either a plain string (title) or a dict with
            # {title, quest_type}. Defaults to 'side' when quest_type is absent.
            aq = updates["add_quest"]
            if isinstance(aq, dict):
                quest_title = aq.get("title", str(aq))
                quest_type  = aq.get("quest_type", "side")
            else:
                quest_title = str(aq)
                quest_type  = "side"

            if quest_type not in ("main", "side"):
                logger.debug(
                    f"add_quest.quest_type '{quest_type}' invalid — defaulting to 'side'."
                )
                quest_type = "side"

            new_q = {"title": quest_title, "status": "active", "objectives": []}
            quest_log.setdefault(quest_type, []).append(new_q)

        if "remove_quest" in updates:
            title = updates["remove_quest"]
            for category in ("main", "side"):
                quest_log[category] = [
                    q for q in quest_log.get(category, [])
                    if q.get("title") != title
                ]

        # ── Location change ───────────────────────────────────────────────────
        if "new_location" in updates:
            world_state["current_location"] = updates["new_location"]
            visited = world_state.setdefault("visited_rooms", [])
            if updates["new_location"] not in visited:
                visited.append(updates["new_location"])

    return state, concentration_result


# ════════════════════════════════════════════════════════════════════════════════
# STUB FUNCTIONS — implemented in later phases
# ════════════════════════════════════════════════════════════════════════════════

def award_xp(amount: int, state: Dict[str, Any]) -> Dict[str, Any]:
    # PHASE 6+ (XP/Leveling system, spec Section 17b / PART 8)
    raise NotImplementedError("award_xp — PHASE 6+")

def check_level_up(state: Dict[str, Any]) -> bool:
    # PHASE 6+ (XP/Leveling system)
    raise NotImplementedError("check_level_up — PHASE 6+")

def apply_level_up(state: Dict[str, Any]) -> Dict[str, Any]:
    # PHASE 6+ (XP/Leveling system)
    raise NotImplementedError("apply_level_up — PHASE 6+")

def buy_item(item_id: str, shop_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
    # PHASE 6+ (Economy system, spec Section 19b / PART 4c)
    raise NotImplementedError("buy_item — PHASE 6+")

def sell_item(item_id: str, shop_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
    # PHASE 6+ (Economy system)
    raise NotImplementedError("sell_item — PHASE 6+")

def resolve_spell_save(caster: Dict, target: Dict, spell: Dict,
                       state: Dict[str, Any]) -> Dict[str, Any]:
    # PHASE 6+ (Spellcasting system, spec Section 8b / PART 4b)
    # DC = 8 + proficiency_bonus + casting_stat_modifier (caster's stats set the DC,
    # TARGET rolls the save — direction is reversed from a normal check).
    raise NotImplementedError("resolve_spell_save — PHASE 6+")

def resolve_downed_outcome(combatant: Dict, combat_state: Dict,
                           world_state: Dict) -> Dict[str, Any]:
    # PHASE 8 (Death/downed outcome system, spec Section 17a / PART 7)
    raise NotImplementedError("resolve_downed_outcome — PHASE 8")

def dismiss_companion(npc_id: str, world_state: Dict[str, Any]) -> None:
    # PHASE 6+ (Companion dismissal, spec Section 21 / PART 9)
    raise NotImplementedError("dismiss_companion — PHASE 6+")
