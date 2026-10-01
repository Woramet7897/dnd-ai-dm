"""
combat_manager.py — Phase 4
Owns 100% of combat math. The LLM narrates outcomes; Python decides them.

Design principles (spec Section 9):
- All dice rolls, hit/miss, damage, conditions, and initiative are pure Python.
- No LLM calls inside this module.
- Extraction calls are SKIPPED during combat (spec 9b-6); this module is the only
  thing that writes to combat_state.
- Round-based narration: Python resolves the WHOLE round first, then assembles ONE
  combined result block for the narrative LLM (spec 9b-iii).
- start_combat() is idempotent — a second call while combat is active is a no-op
  (spec 9b-iv idempotency guard, needed because both manual button and extraction
  call trigger can fire in the same turn).
"""

import copy
import json
import logging
import os
import random
import re
from typing import Any, Dict, List, Optional, Tuple

# Catalog directory — resolved relative to this file so loading succeeds
# regardless of the cwd when the process was launched (spec bug-fix, Phase 4).
_CATALOG_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Logger ────────────────────────────────────────────────────────────────────
logger = logging.getLogger("combat_manager")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setLevel(logging.DEBUG)
    _h.setFormatter(logging.Formatter("[COMBAT] %(levelname)s: %(message)s"))
    logger.addHandler(_h)

# ── Monster catalog cache ─────────────────────────────────────────────────────
_monster_catalog: Optional[Dict] = None

def _get_monster_catalog() -> Dict:
    global _monster_catalog
    if _monster_catalog is None:
        try:
            path = os.path.join(_CATALOG_DIR, "monster_catalog.json")
            with open(path, "r", encoding="utf-8") as f:
                _monster_catalog = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load monster_catalog.json: {e}")
            _monster_catalog = {}
    return _monster_catalog


# ════════════════════════════════════════════════════════════════════════════════
# CONDITIONS (spec Section 20)
# ════════════════════════════════════════════════════════════════════════════════

CONDITIONS = {"prone", "poisoned", "stunned", "restrained", "frightened", "exhausted", "burning", "dazed", "slowed"}

# Mechanical effect lookup — used by resolve_attack() before rolling.
CONDITION_EFFECTS: Dict[str, Dict[str, bool]] = {
    "prone":       {"attacks_against_have_advantage": True},
    "poisoned":    {"attack_rolls_disadvantage": True},
    "stunned":     {"skip_turn": True},
    "restrained":  {"attack_rolls_disadvantage": True, "attacks_against_have_advantage": True},
    "frightened":  {"cannot_approach_source": True},
    "exhausted":   {"attack_rolls_disadvantage": True, "ability_checks_disadvantage": True},
    "burning":     {},
    "dazed":       {"attack_rolls_disadvantage": True, "ac_penalty": 1, "lose_reaction": True},
    "slowed":      {"movement_penalty": True},
}


def apply_condition(combatant: Dict[str, Any], condition: str, duration: int) -> None:
    """
    Apply a named condition to a combatant for `duration` rounds.
    Spec Section 20: conditions are only applied via a static applies_condition
    field on a monster attack — never decided ad hoc by the LLM.

    If the condition is already active, refreshes the duration to max(current, new).
    Unknown condition names are rejected and logged.

    Args:
        combatant: the combatant dict (player, enemy, or companion).
        condition:  one of CONDITIONS.
        duration:   number of rounds. Must be >= 1.
    """
    if condition not in CONDITIONS:
        logger.debug(f"apply_condition: '{condition}' not in fixed vocabulary — rejected.")
        return
    if not isinstance(duration, int) or duration < 1:
        logger.debug(f"apply_condition: invalid duration {duration!r} — rejected.")
        return

    immunities = combatant.get("immunities", [])
    if condition in immunities:
        logger.debug(f"apply_condition: combatant '{combatant.get('id')}' is immune to '{condition}' — rejected.")
        return

    active = combatant.setdefault("active_conditions", [])
    for entry in active:
        if entry["condition"] == condition:
            entry["duration"] = max(entry["duration"], duration)
            logger.debug(f"apply_condition: refreshed '{condition}' on '{combatant.get('id')}' to {entry['duration']} rounds.")
            return

    active.append({"condition": condition, "duration": duration})
    logger.debug(f"apply_condition: applied '{condition}' to '{combatant.get('id')}' for {duration} rounds.")


def tick_conditions(combat_state: Dict[str, Any]) -> None:
    """
    Decrement all active condition durations by 1 at the END of a round.
    Conditions reaching 0 are removed automatically.
    Spec Section 20: 'Duration is a fixed number of rounds set at application time,
    decremented by tick_conditions() each round, removed automatically at zero.'

    Args:
        combat_state: the live combat_state dict from world_state.
    """
    all_combatants: list
    if isinstance(combat_state.get("player_combatant"), dict):
        all_combatants = (
            [combat_state["player_combatant"]]
            + combat_state.get("enemies", [])
            + combat_state.get("companions", [])
        )
    else:
        all_combatants = (
            combat_state.get("enemies", [])
            + combat_state.get("companions", [])
        )

    for combatant in all_combatants:
        active = combatant.get("active_conditions", [])
        updated = []
        for entry in active:
            entry["duration"] -= 1
            if entry["duration"] > 0:
                updated.append(entry)
            else:
                logger.debug(f"tick_conditions: '{entry['condition']}' expired on '{combatant.get('id')}'.")
        combatant["active_conditions"] = updated


def _get_condition_set(combatant: Dict[str, Any]) -> set:
    """Return the set of currently active condition names for a combatant."""
    return {e["condition"] for e in combatant.get("active_conditions", [])}


# ════════════════════════════════════════════════════════════════════════════════
# SURFACE SYSTEM (Phase 11.1 / Module A §1)
# ════════════════════════════════════════════════════════════════════════════════

VALID_SURFACES = {None, "grease", "water", "fire", "electrified_water", "blood"}


def _get_all_combatants(combat_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return all combatants present in combat_state (player, enemies, companions)."""
    combatants: List[Dict[str, Any]] = []
    player_c = combat_state.get("player_combatant")
    if isinstance(player_c, dict):
        combatants.append(player_c)
    combatants.extend(combat_state.get("enemies", []))
    combatants.extend(combat_state.get("companions", []))
    return combatants


def _get_stat_mod(combatant: Dict[str, Any], stat: str) -> int:
    """Return the modifier for a named ability stat (floor((val - 10) / 2))."""
    val = combatant.get("stats", {}).get(stat, 10)
    return (val - 10) // 2


def apply_surface(
    combat_state: Dict[str, Any],
    surface_type: Optional[str],
    duration: int = 3,
) -> Dict[str, Any]:
    """
    Apply a surface to the combat room or resolve a surface combo.

    Valid surfaces: None, 'grease', 'water', 'fire', 'electrified_water', 'blood'.
    Combos:
      - grease + fire -> new surface 'fire' (dur 3), living combatants roll DEX save vs DC 12;
        on fail, take 2d4 fire damage and burning condition (dur 3).
      - fire + water -> new surface None, clears surface and sets smoke_active = True (dur 3),
        giving disadvantage to ranged attacks in room.
    """
    if surface_type not in VALID_SURFACES:
        logger.debug(f"apply_surface: '{surface_type}' not in valid surfaces — rejected.")
        return {
            "combo_triggered": False,
            "new_type": combat_state.get("room_surface", {}).get("type"),
            "effects": [],
        }

    surface = combat_state.setdefault("room_surface", {"type": None, "duration": 0})
    current = surface.get("type")

    # Explicit clear call
    if surface_type is None:
        surface["type"] = None
        surface["duration"] = 0
        return {"combo_triggered": False, "new_type": None, "effects": []}

    # Plain overwrite if empty or same
    if current is None or current == surface_type:
        surface["type"] = surface_type
        surface["duration"] = duration
        return {"combo_triggered": False, "new_type": surface_type, "effects": []}

    # Check for combos
    pair = frozenset({current, surface_type})

    # Combo 1: grease + fire
    if pair == frozenset({"grease", "fire"}):
        surface["type"] = "fire"
        surface["duration"] = 3
        effects: List[Dict[str, Any]] = []

        living = [c for c in _get_all_combatants(combat_state) if c.get("hp", {}).get("current", 0) > 0]
        for c in living:
            roll = _roll_d20()
            mod = _get_stat_mod(c, "DEX")
            total = roll + mod
            save_success = (total >= 12)

            if not save_success:
                dmg = roll_dice("2d4")
                c_hp = c.setdefault("hp", {"current": 1, "max": 1})
                c_hp["current"] = max(0, c_hp["current"] - dmg)
                apply_condition(c, "burning", 3)
                effects.append({
                    "combatant_id": c.get("id"),
                    "save_stat": "DEX",
                    "save_roll": roll,
                    "save_total": total,
                    "save_dc": 12,
                    "save_success": False,
                    "damage": dmg,
                    "damage_type": "fire",
                    "condition_applied": "burning",
                })
            else:
                effects.append({
                    "combatant_id": c.get("id"),
                    "save_stat": "DEX",
                    "save_roll": roll,
                    "save_total": total,
                    "save_dc": 12,
                    "save_success": True,
                    "damage": 0,
                })

        logger.info(f"apply_surface: grease+fire combo triggered! Surface is now 'fire', {len(effects)} saves resolved.")
        return {"combo_triggered": True, "combo": "grease_fire", "new_type": "fire", "effects": effects}

    # Combo 2: fire + water -> smoke
    if pair == frozenset({"fire", "water"}):
        surface["type"] = None
        surface["duration"] = 0
        combat_state["smoke_active"] = True
        combat_state["smoke_duration"] = 3
        logger.info("apply_surface: fire+water combo triggered! Surface extinguished, smoke active for 3 rounds.")
        return {
            "combo_triggered": True,
            "combo": "fire_water",
            "new_type": None,
            "smoke_active": True,
            "effects": [],
        }

    # Non-combo pair: overwrite
    surface["type"] = surface_type
    surface["duration"] = duration
    return {"combo_triggered": False, "new_type": surface_type, "effects": []}


def trigger_lightning_surface_reaction(combat_state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Trigger water + lightning reaction when lightning damage hits a room with a 'water' surface.
    Sets room_surface to 'electrified_water' for 3 rounds.
    All living combatants roll CON save vs DC 13.
    On fail: take 1d6 lightning damage and become 'dazed' for 1 round.
    If room_surface is not 'water', this is a safe no-op.
    """
    surface = combat_state.setdefault("room_surface", {"type": None, "duration": 0})
    current = surface.get("type")

    if current != "water":
        logger.debug(f"trigger_lightning_surface_reaction: current surface is '{current}' (not 'water') — no-op.")
        return {"combo_triggered": False, "new_type": current, "effects": []}

    surface["type"] = "electrified_water"
    surface["duration"] = 3
    effects: List[Dict[str, Any]] = []

    living = [c for c in _get_all_combatants(combat_state) if c.get("hp", {}).get("current", 0) > 0]
    for c in living:
        roll = _roll_d20()
        mod = _get_stat_mod(c, "CON")
        total = roll + mod
        save_success = (total >= 13)

        if not save_success:
            dmg = roll_dice("1d6")
            c_hp = c.setdefault("hp", {"current": 1, "max": 1})
            c_hp["current"] = max(0, c_hp["current"] - dmg)
            apply_condition(c, "dazed", 1)
            effects.append({
                "combatant_id": c.get("id"),
                "save_stat": "CON",
                "save_roll": roll,
                "save_total": total,
                "save_dc": 13,
                "save_success": False,
                "damage": dmg,
                "damage_type": "lightning",
                "condition_applied": "dazed",
            })
        else:
            effects.append({
                "combatant_id": c.get("id"),
                "save_stat": "CON",
                "save_roll": roll,
                "save_total": total,
                "save_dc": 13,
                "save_success": True,
                "damage": 0,
            })

    logger.info(f"trigger_lightning_surface_reaction: electrified_water combo triggered! {len(effects)} saves resolved.")
    return {"combo_triggered": True, "combo": "water_lightning", "new_type": "electrified_water", "effects": effects}


def tick_surface(combat_state: Dict[str, Any]) -> None:
    """
    Decrement room_surface duration by 1 at the end of each round.
    If duration reaches 0, resets to {"type": None, "duration": 0}.
    Also decrements smoke_duration and resets smoke_active when expired.
    """
    surface = combat_state.setdefault("room_surface", {"type": None, "duration": 0})
    if surface.get("duration", 0) > 0:
        surface["duration"] -= 1
        if surface["duration"] <= 0:
            surface["type"] = None
            surface["duration"] = 0

    if combat_state.get("smoke_duration", 0) > 0:
        combat_state["smoke_duration"] -= 1
        if combat_state["smoke_duration"] <= 0:
            combat_state["smoke_active"] = False
            combat_state["smoke_duration"] = 0


# ════════════════════════════════════════════════════════════════════════════════
# DICE ROLLING
# ════════════════════════════════════════════════════════════════════════════════

def _roll_d20() -> int:
    """Roll a single d20. Module-level so tests can monkeypatch it."""
    return random.randint(1, 20)


def roll_dice(dice_str: str) -> int:
    """
    Parse and roll a dice expression such as '1d6+2', '2d8', or '1d4-1'.
    Returns the total roll result (minimum 1).

    Args:
        dice_str: dice expression string from the monster/item catalog.

    Returns:
        Integer result of the roll (>= 1).

    Raises:
        ValueError if the expression cannot be parsed.
    """
    dice_str = dice_str.strip().replace(" ", "")

    # Path 1: standard NdX±M form (e.g. '1d6+2', '2d8', '1d4-1')
    m = re.match(r"^(\d+)d(\d+)([+-]\d+)?$", dice_str, re.IGNORECASE)
    if m:
        num_dice  = int(m.group(1))
        die_size  = int(m.group(2))
        modifier  = int(m.group(3)) if m.group(3) else 0
        total = sum(random.randint(1, die_size) for _ in range(num_dice)) + modifier
        return max(1, total)

    # Path 2: flat integer with optional modifier (e.g. '1', '1+0', '5', '3-1').
    # Used by Unarmed Strike and any catalog entry that deals a fixed flat amount.
    m2 = re.match(r"^(\d+)([+-]\d+)?$", dice_str)
    if m2:
        base     = int(m2.group(1))
        modifier = int(m2.group(2)) if m2.group(2) else 0
        return max(1, base + modifier)

    raise ValueError(f"Cannot parse dice expression: '{dice_str}'")


# ════════════════════════════════════════════════════════════════════════════════
# INITIATIVE
# ════════════════════════════════════════════════════════════════════════════════

def _get_dex_mod(combatant: Dict[str, Any]) -> int:
    """Return the DEX modifier (floor((DEX - 10) / 2)) for a combatant."""
    dex = combatant.get("stats", {}).get("DEX", 10)
    return (dex - 10) // 2


def roll_initiative(combatants: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Roll initiative for all combatants and return them sorted highest-first.
    Ties broken by DEX modifier, then by random tiebreak.

    Each combatant dict gets an 'initiative' key set to the raw d20+DEX roll.
    Spec Section 9b-2: 'Initiative: unchanged — roll_initiative(combatants).'

    Args:
        combatants: list of combatant dicts (player, enemies, companions mixed).

    Returns:
        Same list sorted by initiative descending (mutates combatants in-place, also returns).
    """
    for c in combatants:
        roll   = _roll_d20()
        dex_m  = _get_dex_mod(c)
        c["initiative"] = roll + dex_m
        c["_initiative_tiebreak"] = random.random()
        logger.debug(f"Initiative: {c.get('name')} rolled {roll}+{dex_m}={c['initiative']}")

    combatants.sort(
        key=lambda c: (c["initiative"], _get_dex_mod(c), c["_initiative_tiebreak"]),
        reverse=True,
    )
    for c in combatants:
        c.pop("_initiative_tiebreak", None)

    return combatants


# ════════════════════════════════════════════════════════════════════════════════
# COMBAT STATE MANAGEMENT
# ════════════════════════════════════════════════════════════════════════════════

def _build_combatant_from_catalog(
    monster_id: str,
    instance_index: int = 1,
    world_state: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Build a live combatant dict from the monster catalog entry or world_state["generated_monsters"].
    Gives each instance a unique id suffix (e.g. 'goblin_scout_1', 'goblin_scout_2').
    Returns None if the monster_id is not found.
    """
    catalog = _get_monster_catalog()
    template = catalog.get(monster_id)
    if template is None and world_state and isinstance(world_state, dict):
        template = world_state.get("generated_monsters", {}).get(monster_id)

    if template is None:
        logger.debug(f"_build_combatant_from_catalog: '{monster_id}' not in catalog or generated_monsters.")
        return None

    c = copy.deepcopy(template)
    c["id"]   = f"{monster_id}_{instance_index}"
    c["side"] = "enemy"
    c.setdefault("initiative", None)
    c.setdefault("active_conditions", [])
    # Ensure mutable HP
    if isinstance(c.get("hp"), dict):
        c["hp"] = {"current": c["hp"]["max"], "max": c["hp"]["max"]}
    return c


def start_combat(
    enemy_ids: List[str],
    player_state: Dict[str, Any],
    world_state: Dict[str, Any],
    companion_states: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Instantiate combat_state and write it into world_state["combat_state"].
    Rolls initiative for all combatants and sets the turn order.

    Idempotency guard (spec 9b-iv): if combat_state is already active, returns None.
    This is the single entry point for both the extraction-call trigger and the
    manual 'Attack' button — both must call this function, never write combat_state
    directly.

    Args:
        enemy_ids:        list of monster_catalog IDs (may contain duplicates).
        player_state:     character save dict (schema_version 4).
        world_state:      world save dict; combat_state written here.
        companion_states: list of companion combatant dicts (optional).

    Returns:
        The new combat_state dict, or None if combat was already active (no-op).
    """
    # ── Idempotency guard ─────────────────────────────────────────────────────
    if world_state.get("combat_state") is not None:
        logger.debug("start_combat: combat already active — no-op (idempotency guard).")
        return None

    # ── Build enemy combatants ────────────────────────────────────────────────
    id_counts: Dict[str, int] = {}
    enemies: List[Dict[str, Any]] = []
    for mid in enemy_ids:
        id_counts[mid] = id_counts.get(mid, 0) + 1
        c = _build_combatant_from_catalog(mid, id_counts[mid], world_state=world_state)
        if c is None:
            logger.debug(f"start_combat: unknown enemy '{mid}' — skipped.")
            continue
        enemies.append(c)

    if not enemies:
        logger.debug("start_combat: no valid enemies — combat not started.")
        return None

    # ── Build player combatant ────────────────────────────────────────────────
    # Ensure player_state has standard mutable keys initialized
    player_state.setdefault("hp", {"current": 10, "max": 10})
    player_state.setdefault("active_conditions", [])
    player_state.setdefault("death_saves", {"success": 0, "fail": 0})
    player_state.setdefault("status", "normal")
    player_state.setdefault("inventory", [])
    import state_manager
    state_manager._ensure_currency(player_state)
    player_state.setdefault("xp_current", 0)
    player_state.setdefault("level", 1)
    player_state.setdefault("proficiency_bonus", 2)
    player_state.setdefault("spell_slots", {})

    player_c: Dict[str, Any] = {
        "id":               "player",
        "name":             player_state.get("name", "Adventurer"),
        "side":             "player",
        "hp":               player_state["hp"],                 # SHARED BY REFERENCE
        "ac":               player_state.get(
            "ac",
            10 + (player_state.get("stats", {}).get("DEX", 10) - 10) // 2,
        ),
        "stats":            player_state.get("stats", {}),
        "attacks":          _player_attacks(player_state),
        "initiative":       None,
        "active_conditions": player_state["active_conditions"],  # SHARED BY REFERENCE
        "death_saves":      player_state["death_saves"],         # SHARED BY REFERENCE
        "inventory":        player_state["inventory"],          # SHARED BY REFERENCE
        "spell_slots":      player_state["spell_slots"],        # SHARED BY REFERENCE
        "status":           player_state["status"],
        "currency":         player_state["currency"],
        "gold":             player_state["gold"],
        "xp_current":       player_state["xp_current"],
        "level":            player_state["level"],
        "proficiency_bonus": player_state["proficiency_bonus"],
        "proficient_skills": player_state.get("proficient_skills", []),
        "has_high_ground":  player_state.get("has_high_ground", False),
        "weapon_actions_available": player_state.get("weapon_actions_available", True),
        "_player_state":    player_state,                        # Reference to real character dict
    }

    # ── Build companion combatants ────────────────────────────────────────────
    companions: List[Dict[str, Any]] = []
    target_comps = companion_states
    if target_comps is None:
        target_comps = [
            c for c in (world_state.get("party", {}).get("companions", []) if isinstance(world_state, dict) else [])
            if isinstance(c, dict) and c.get("hp", {}).get("current", 0) > 0
        ]

    for comp in target_comps:
        comp.setdefault("side", "player")
        comp.setdefault("active_conditions", [])
        comp.setdefault("initiative", None)
        comp.setdefault("death_saves", {"success": 0, "fail": 0})
        comp.setdefault("hp", {"current": 10, "max": 10})
        comp.setdefault("has_high_ground", False)
        companions.append(comp)  # SHARED BY REFERENCE — NO copy.deepcopy!

    for enemy in enemies:
        enemy.setdefault("has_high_ground", False)

    # ── Roll initiative for everyone ──────────────────────────────────────────
    all_combatants = [player_c] + companions + enemies
    roll_initiative(all_combatants)
    turn_order = [c["id"] for c in all_combatants]

    room_hazards = []
    if world_state:
        import dungeon_manager
        c_room = dungeon_manager.get_current_room(world_state)
        if c_room:
            room_hazards = list(c_room.get("hazards", []))

    combat_state: Dict[str, Any] = {
        "round":            1,
        "turn_index":       0,          # index into turn_order
        "turn_order":       turn_order,
        "player_combatant": player_c,
        "enemies":          enemies,
        "companions":       companions,
        "round_log":        [],         # accumulated results for this round
        "status":           "active",   # 'active' | 'ended'
        "outcome":          None,       # filled by check_combat_end()
        "room_surface":     {"type": None, "duration": 0},
        "smoke_active":     False,
        "smoke_duration":   0,
        "room_hazards":     room_hazards,
    }

    world_state["combat_state"] = combat_state
    logger.debug(
        f"start_combat: combat started with {len(enemies)} enemy(s). "
        f"Turn order: {turn_order}"
    )
    return combat_state


def _player_attacks(player_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Build the attack list for the player based on currently-equipped weapons in inventory.
    Spec Section 7b & Bugfix:
    - Finds inventory items where equipped == True and item_catalog type == 'weapon'.
    - Calculates total to-hit bonus:
        player proficiency_bonus + ability modifier (STR mod, or max(STR, DEX) for finesse weapons)
        + item's magic attack_bonus from catalog.
    - Unarmed Strike fallback uses proficiency_bonus + STR mod.
    """
    from state_manager import _get_item_catalog, get_modifier, _inventory_item_info
    catalog = _get_item_catalog()

    stats = player_state.get("stats", {})
    prof = player_state.get("proficiency_bonus", 2)
    str_mod = get_modifier(stats.get("STR", 10))
    dex_mod = get_modifier(stats.get("DEX", 10))

    equipped_weapons = []
    for item in player_state.get("inventory", []):
        if isinstance(item, dict) and item.get("equipped") is True:
            item_id = item.get("item_id")
            info = _inventory_item_info(item, catalog)
            if info.get("type") == "weapon":
                effects = info.get("effects", {})
                is_finesse = info.get("finesse", False)
                stat_mod = max(str_mod, dex_mod) if is_finesse else str_mod
                magic_bonus = effects.get("attack_bonus", 0)
                total_attack_bonus = prof + stat_mod + magic_bonus

                attack = {
                    "name": info.get("name", item_id.capitalize()),
                    "attack_bonus": total_attack_bonus,
                    "damage": effects.get("damage", "1d4"),
                    "damage_type": effects.get("damage_type", "slashing"),
                    "applies_condition": effects.get("applies_condition", None),
                    "ranged": info.get("ranged", False) or effects.get("ranged", False),
                }
                equipped_weapons.append(attack)

    if equipped_weapons:
        return equipped_weapons

    unarmed_bonus = prof + str_mod
    return [
        {"name": "Unarmed Strike", "attack_bonus": unarmed_bonus, "damage": "1+0",
         "damage_type": "bludgeoning", "applies_condition": None, "ranged": False}
    ]




def sync_player_state(player_c: Dict[str, Any]) -> None:
    """
    Sync scalar primitive fields (gold, status, xp_current, level, proficiency_bonus, ac)
    from player_c back to underlying real player_state dict.
    Nested mutable structures (hp, active_conditions, death_saves, inventory, spell_slots)
    are shared by reference object identity.
    """
    real_state = player_c.get("_player_state")
    if real_state is None or not isinstance(real_state, dict):
        return

    for key in ("currency", "gold", "status", "xp_current", "level", "proficiency_bonus", "ac", "weapon_actions_available"):
        if key in player_c:
            real_state[key] = player_c[key]

    if "inventory" in player_c:
        real_state["inventory"] = player_c["inventory"]


def end_combat(world_state: Dict[str, Any]) -> None:
    """
    Clear combat_state from world_state, marking combat as over.
    Call after check_combat_end() returns a non-None outcome.
    Does nothing if combat is not active.
    """
    cs = world_state.get("combat_state")
    if cs is None:
        return
    player_c = cs.get("player_combatant")
    if player_c:
        sync_player_state(player_c)
    world_state["combat_state"] = None
    logger.debug("end_combat: combat_state cleared.")


# ════════════════════════════════════════════════════════════════════════════════
# ATTACK RESOLUTION (spec Section 9b-3)
# ════════════════════════════════════════════════════════════════════════════════

def resolve_attack(
    attacker: Dict[str, Any],
    target: Dict[str, Any],
    attack: Dict[str, Any],
    combat_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Resolve a single attack from attacker → target.
    Python decides hit/miss, damage, crits, fumbles, and conditions.
    The LLM only narrates the outcome — never decides it.

    Conditions that affect this roll (spec Section 20 & Phase 11.1):
    - Attacker has 'poisoned', 'restrained', 'exhausted', 'frightened', or 'dazed': attack roll made with disadvantage.
    - Target has 'prone' or 'restrained': attack roll made with advantage.
    - Attacker has 'stunned': turn is skipped (caller should check before calling).
    - Room has 'smoke_active': ranged attacks suffer disadvantage.

    Args:
        attacker: combatant dict (has stats, active_conditions, attacks).
        target:   combatant dict (has hp, ac, active_conditions).
        attack:   one entry from attacker["attacks"] (name, attack_bonus, damage,
                  damage_type, applies_condition, ranged).
        combat_state: optional live combat_state dict for room-level effects (surfaces, smoke).

    Returns:
        result dict with keys:
          hit (bool), crit (bool), fumble (bool), damage (int),
          damage_type (str), target_id (str), attacker_id (str),
          attack_name (str), condition_applied (str|None),
          target_hp_after (int), target_downed (bool)
    """
    attacker_conditions = _get_condition_set(attacker)
    target_conditions   = _get_condition_set(target)

    # ── Determine advantage / disadvantage ───────────────────────────────────
    has_advantage    = False
    has_disadvantage = False

    if "prone" in target_conditions or "restrained" in target_conditions:
        has_advantage = True
    if "poisoned" in attacker_conditions or "restrained" in attacker_conditions or "exhausted" in attacker_conditions:
        has_disadvantage = True
    if "frightened" in attacker_conditions:
        has_disadvantage = True
    if "dazed" in attacker_conditions:
        has_disadvantage = True

    # Smoke: ranged attacks against targets in this room suffer disadvantage
    is_ranged = attack.get("ranged", False) or any(k in attack.get("name", "").lower() for k in ("bow", "crossbow", "ranged"))
    if is_ranged and combat_state and combat_state.get("smoke_active"):
        has_disadvantage = True

    # High Ground logic (Phase 11.2)
    attacker_high_ground = attacker.get("has_high_ground", False)
    target_high_ground   = target.get("has_high_ground", False)

    if is_ranged and target_high_ground:
        has_disadvantage = True

    # Advantage and disadvantage cancel each other out (5e rule)
    if has_advantage and has_disadvantage:
        has_advantage = has_disadvantage = False

    # ── Roll to hit ───────────────────────────────────────────────────────────
    if has_advantage:
        raw_roll = max(_roll_d20(), _roll_d20())
    elif has_disadvantage:
        raw_roll = min(_roll_d20(), _roll_d20())
    else:
        raw_roll = _roll_d20()

    attack_bonus  = attack.get("attack_bonus", 0)
    if is_ranged and attacker_high_ground:
        attack_bonus += 2
    total_to_hit  = raw_roll + attack_bonus
    target_ac     = target.get("ac", 10)
    if "dazed" in target_conditions:
        target_ac -= 1

    crit   = (raw_roll == 20)
    fumble = (raw_roll == 1)
    hit    = crit or (not fumble and total_to_hit >= target_ac)

    # ── Roll damage ───────────────────────────────────────────────────────────
    damage = 0
    condition_applied = None

    if hit:
        damage_expr = attack.get("damage", "1d4")
        try:
            damage = roll_dice(damage_expr)
        except ValueError:
            damage = 1

        if crit:
            # Double dice on crit (simplified: roll damage again and add)
            try:
                damage += roll_dice(damage_expr)
            except ValueError:
                damage += 1

        # ── Apply damage to target ────────────────────────────────────────────
        target_hp = target.setdefault("hp", {"current": 1, "max": 1})
        temp_hp = target_hp.get("temp", target.get("temp_hp", 0))
        damage_to_apply = damage
        if temp_hp > 0:
            if damage_to_apply <= temp_hp:
                target_hp["temp"] = temp_hp - damage_to_apply
                if "temp_hp" in target:
                    target["temp_hp"] = target_hp["temp"]
                damage_to_apply = 0
            else:
                damage_to_apply -= temp_hp
                target_hp["temp"] = 0
                if "temp_hp" in target:
                    target["temp_hp"] = 0
        target_hp["current"] = max(0, target_hp["current"] - damage_to_apply)

        # ── Apply condition (from static attack field, not LLM) ───────────────
        cond = attack.get("applies_condition")
        if cond and cond in CONDITIONS:
            apply_condition(target, cond, duration=2)
            condition_applied = cond

        # ── Trigger lightning surface reaction if lightning damage in water ───
        if damage > 0 and attack.get("damage_type") == "lightning" and combat_state:
            surface = combat_state.get("room_surface", {})
            if surface.get("type") == "water":
                trigger_lightning_surface_reaction(combat_state)

    target_downed = target.get("hp", {}).get("current", 1) <= 0

    result = {
        "attacker_id":       attacker.get("id", "unknown"),
        "attacker_name":     attacker.get("name", "Unknown"),
        "target_id":         target.get("id", "unknown"),
        "target_name":       target.get("name", "Unknown"),
        "attack_name":       attack.get("name", "Attack"),
        "raw_roll":          raw_roll,
        "total_to_hit":      total_to_hit,
        "target_ac":         target_ac,
        "hit":               hit,
        "crit":              crit,
        "fumble":            fumble,
        "damage":            damage,
        "damage_type":       attack.get("damage_type", ""),
        "condition_applied": condition_applied,
        "target_hp_after":   target.get("hp", {}).get("current", 0),
        "target_downed":     target_downed,
    }
    logger.debug(
        f"resolve_attack: {attacker.get('name')} → {target.get('name')}: "
        f"roll={raw_roll}+{attack_bonus}={total_to_hit} vs AC {target_ac} → "
        f"{'CRIT' if crit else 'FUMBLE' if fumble else 'HIT' if hit else 'MISS'}, "
        f"dmg={damage}"
    )
    return result


def _get_skill_mod(combatant: Dict[str, Any], skill_name: str) -> int:
    """Return total skill modifier for a combatant (ability mod + proficiency bonus if proficient)."""
    stat = "STR" if skill_name == "Athletics" else "DEX" if skill_name == "Acrobatics" else "STR"
    mod = _get_stat_mod(combatant, stat)
    prof_skills = combatant.get("proficient_skills", [])
    if not prof_skills and "_player_state" in combatant:
        prof_skills = combatant["_player_state"].get("proficient_skills", [])
    if skill_name in prof_skills:
        mod += combatant.get("proficiency_bonus", 2)
    return mod


def resolve_shove(
    attacker: Dict[str, Any],
    target: Dict[str, Any],
    room_hazards: Optional[Any] = None,
    combat_state: Optional[Dict[str, Any]] = None,
    shove_type: str = "push",
) -> Dict[str, Any]:
    """
    Resolve a Shove action (Module A §2 / Phase 11.2).

    Rules:
      - Attacker rolls Athletics (STR check) vs target's higher of Athletics (STR) or Acrobatics (DEX).
      - Attacker loses (total <= target total): clean no-op, state unchanged.
      - Attacker wins (total > target total):
          - shove_type == 'prone': target knocked prone (applies 'prone' condition, duration 2).
          - shove_type == 'push': target pushed 5ft.
            If room has 'near_chasm' or 'acid_pool' hazard:
              target rolls DC 13 DEX save:
                - If passed: catches footing, no damage.
                - If failed:
                    - 'near_chasm': instant death for Tiny/Small/Medium, or 3d6 damage for Large+.
                    - 'acid_pool': 3d6 acid damage.
    """
    if isinstance(room_hazards, dict) and "turn_order" in room_hazards:
        combat_state = room_hazards
        room_hazards = combat_state.get("room_hazards", [])
    elif isinstance(room_hazards, str) and room_hazards in ("push", "prone"):
        shove_type = room_hazards
        room_hazards = combat_state.get("room_hazards", []) if combat_state else []
    elif isinstance(room_hazards, str):
        room_hazards = [room_hazards]
    elif room_hazards is None:
        if combat_state and "room_hazards" in combat_state:
            room_hazards = combat_state.get("room_hazards", [])
        else:
            room_hazards = []
    else:
        room_hazards = list(room_hazards)

    attacker_conds = _get_condition_set(attacker)
    target_conds   = _get_condition_set(target)

    # ── Attacker roll (Athletics) ─────────────────────────────────────────────
    attacker_disadv = any(c in attacker_conds for c in ("poisoned", "exhausted", "restrained", "dazed"))
    attacker_adv = False
    if attacker_adv and not attacker_disadv:
        attacker_roll = max(_roll_d20(), _roll_d20())
    elif attacker_disadv and not attacker_adv:
        attacker_roll = min(_roll_d20(), _roll_d20())
    else:
        attacker_roll = _roll_d20()

    attacker_mod = _get_skill_mod(attacker, "Athletics")
    attacker_total = attacker_roll + attacker_mod

    # ── Target contest roll (Higher of Athletics vs Acrobatics) ───────────────
    target_ath_mod = _get_skill_mod(target, "Athletics")
    target_acro_mod = _get_skill_mod(target, "Acrobatics")
    if target_acro_mod > target_ath_mod:
        target_skill = "Acrobatics"
        target_mod = target_acro_mod
    else:
        target_skill = "Athletics"
        target_mod = target_ath_mod

    target_disadv = any(c in target_conds for c in ("poisoned", "exhausted", "restrained"))
    target_adv = False
    if target_adv and not target_disadv:
        target_roll = max(_roll_d20(), _roll_d20())
    elif target_disadv and not target_adv:
        target_roll = min(_roll_d20(), _roll_d20())
    else:
        target_roll = _roll_d20()

    target_total = target_roll + target_mod

    attacker_name = attacker.get("name", "Attacker")
    target_name = target.get("name", "Target")

    # ── Contest Evaluation ───────────────────────────────────────────────────
    if attacker_total <= target_total:
        # Attacker loses or ties: status quo maintained, state unchanged
        logger.debug(
            f"resolve_shove: FAIL {attacker_name} ({attacker_total}) vs {target_name} ({target_total})"
        )
        return {
            "success":           False,
            "action_type":       "shove",
            "shove_type":        shove_type,
            "attacker_id":       attacker.get("id", "player"),
            "attacker_name":     attacker_name,
            "target_id":         target.get("id", "target"),
            "target_name":       target_name,
            "attacker_roll":     attacker_roll,
            "attacker_mod":      attacker_mod,
            "attacker_total":    attacker_total,
            "target_skill":      target_skill,
            "target_roll":       target_roll,
            "target_mod":        target_mod,
            "target_total":      target_total,
            "damage":            0,
            "damage_type":       "",
            "condition_applied": None,
            "hazard_triggered":  None,
            "hazard_saved":      False,
            "target_hp_after":   target.get("hp", {}).get("current", 0),
            "target_downed":     target.get("hp", {}).get("current", 0) <= 0,
            "narration":         f"{attacker_name} attempts to shove {target_name}, but {target_name} holds their ground ({target_total} vs {attacker_total}).",
        }

    # ── Attacker Wins ────────────────────────────────────────────────────────
    damage = 0
    damage_type = ""
    condition_applied = None
    hazard_triggered = None
    hazard_saved = False

    if shove_type == "prone":
        apply_condition(target, "prone", duration=2)
        condition_applied = "prone"
        if target.get("has_high_ground"):
            target["has_high_ground"] = False
        narration = f"{attacker_name} forcefully shoves {target_name} to the ground, knocking them prone!"
    else:  # shove_type == "push" (5ft)
        if target.get("has_high_ground"):
            target["has_high_ground"] = False

        if "near_chasm" in room_hazards:
            hazard = "near_chasm"
        elif "acid_pool" in room_hazards:
            hazard = "acid_pool"
        else:
            hazard = None

        if hazard:
            hazard_triggered = hazard
            # Target rolls DEX save DC 13
            dex_mod = _get_stat_mod(target, "DEX")
            prof_saves = target.get("saving_throw_proficiencies", target.get("proficient_saves", []))
            if not prof_saves and "_player_state" in target:
                prof_saves = target["_player_state"].get("saving_throw_proficiencies", [])
            if "DEX" in prof_saves:
                dex_mod += target.get("proficiency_bonus", 2)

            save_roll = _roll_d20()
            save_total = save_roll + dex_mod
            save_passed = (save_roll == 20) or (save_roll != 1 and save_total >= 13)

            if save_passed:
                hazard_saved = True
                narration = (
                    f"{attacker_name} shoves {target_name} toward the {hazard.replace('_', ' ')}, "
                    f"but {target_name} passes a DC 13 Dexterity save ({save_total}) and catches their footing!"
                )
            else:
                hazard_saved = False
                target_size = str(target.get("size", "Medium")).strip().lower()
                is_large_or_larger = target_size in ("large", "huge", "gargantuan")

                if hazard == "near_chasm":
                    if is_large_or_larger:
                        damage = roll_dice("3d6")
                        damage_type = "bludgeoning"
                        target_hp = target.setdefault("hp", {"current": 1, "max": 1})
                        target_hp["current"] = max(0, target_hp["current"] - damage)
                        narration = (
                            f"{attacker_name} shoves the massive {target_name} into the chasm! "
                            f"{target_name} fails the DC 13 DEX save ({save_total}), taking {damage} falling damage!"
                        )
                    else:
                        damage_type = "fall"
                        target_hp = target.setdefault("hp", {"current": 1, "max": 1})
                        damage = target_hp.get("current", 1)
                        target_hp["current"] = 0
                        narration = (
                            f"{attacker_name} shoves {target_name} over the edge into the chasm! "
                            f"{target_name} fails the DC 13 DEX save ({save_total}) and plunges to their death!"
                        )
                elif hazard == "acid_pool":
                    damage = roll_dice("3d6")
                    damage_type = "acid"
                    target_hp = target.setdefault("hp", {"current": 1, "max": 1})
                    target_hp["current"] = max(0, target_hp["current"] - damage)
                    narration = (
                        f"{attacker_name} shoves {target_name} into the bubbling acid pool! "
                        f"{target_name} fails the DC 13 DEX save ({save_total}), taking {damage} acid damage!"
                    )
        else:
            narration = f"{attacker_name} shoves {target_name} back 5 feet!"

    target_downed = target.get("hp", {}).get("current", 0) <= 0

    try:
        import state_manager
        real_attacker = attacker.get("_player_state", attacker)
        state_manager.check_inspiration_trigger(real_attacker, "shove_assist", {"success": True})
    except Exception as e:
        logger.debug(f"resolve_shove: inspiration trigger check failed: {e}")

    return {
        "success":           True,
        "action_type":       "shove",
        "shove_type":        shove_type,
        "attacker_id":       attacker.get("id", "player"),
        "attacker_name":     attacker_name,
        "target_id":         target.get("id", "target"),
        "target_name":       target_name,
        "attacker_roll":     attacker_roll,
        "attacker_mod":      attacker_mod,
        "attacker_total":    attacker_total,
        "target_skill":      target_skill,
        "target_roll":       target_roll,
        "target_mod":        target_mod,
        "target_total":      target_total,
        "damage":            damage,
        "damage_type":       damage_type,
        "condition_applied": condition_applied,
        "hazard_triggered":  hazard_triggered,
        "hazard_saved":      hazard_saved,
        "target_hp_after":   target.get("hp", {}).get("current", 0),
        "target_downed":     target_downed,
        "narration":         narration,
    }


def get_weapon_action_info(attack: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return weapon action metadata for an attack based on its damage_type (Module A §3 / Phase 11.3).
    - Bludgeoning -> Concussive Smash (DC 8+prof+STR CON save, fail = dazed)
    - Slashing -> Cleave (STR mod damage to adjacent enemy)
    - Piercing -> Hamstring (slowed condition)
    """
    dtype = str(attack.get("damage_type", "slashing")).strip().lower()
    if dtype == "bludgeoning":
        return {
            "name": "Concussive Smash",
            "damage_type": "bludgeoning",
            "description": "On hit, target must pass a CON save (DC 8 + prof + STR mod) or become dazed (-1 AC, disadvantage on attacks) for 1 round.",
        }
    elif dtype == "piercing":
        return {
            "name": "Hamstring",
            "damage_type": "piercing",
            "description": "On hit, target is slowed (movement penalty) for 2 rounds.",
        }
    else:  # default slashing
        return {
            "name": "Cleave",
            "damage_type": "slashing",
            "description": "Hits primary target and deals STR modifier damage to an adjacent enemy.",
        }


def resolve_weapon_action(
    attacker: Dict[str, Any],
    target: Dict[str, Any],
    attack: Dict[str, Any],
    combat_state: Optional[Dict[str, Any]] = None,
    adjacent_target: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Resolve a Cooldown-Gated Weapon Action (Module A §3 / Phase 11.3).

    Rules:
      - Cooldown check: attacker must have weapon_actions_available == True.
      - Once used: weapon_actions_available becomes False until next short rest.
      - Resolves standard attack roll first.
      - On-hit effects per weapon damage type:
          - Bludgeoning (Concussive Smash): target rolls CON save DC (8 + prof + STR mod);
            on fail -> target gets 'dazed' condition (dur 1, -1 AC, disadvantage).
          - Slashing (Cleave): target takes regular damage, and one adjacent enemy takes STR mod damage.
          - Piercing (Hamstring): target gets 'slowed' condition (dur 2).
    """
    # ── Cooldown Check ────────────────────────────────────────────────────────
    available = attacker.get("weapon_actions_available")
    if available is None and "_player_state" in attacker:
        available = attacker["_player_state"].get("weapon_actions_available", True)
    if available is False:
        logger.debug(f"resolve_weapon_action: {attacker.get('name')} weapon action on cooldown.")
        return {
            "success": False,
            "hit": False,
            "damage": 0,
            "action_type": "weapon_action",
            "reason": "Weapon action is on cooldown. Requires a short rest to recharge.",
            "attacker_id": attacker.get("id"),
            "target_id": target.get("id"),
        }

    # Consume cooldown
    attacker["weapon_actions_available"] = False
    if "_player_state" in attacker and isinstance(attacker["_player_state"], dict):
        attacker["_player_state"]["weapon_actions_available"] = False

    action_info = get_weapon_action_info(attack)
    action_name = action_info["name"]

    # ── Execute Attack Roll ───────────────────────────────────────────────────
    result = resolve_attack(attacker, target, attack, combat_state=combat_state)
    result["action_type"] = "weapon_action"
    result["weapon_action_name"] = action_name
    result["success"] = True

    attacker_name = attacker.get("name", "Attacker")
    target_name = target.get("name", "Target")

    if not result.get("hit"):
        result["narration"] = f"{attacker_name} attempts {action_name} against {target_name}, but misses!"
        return result

    # ── On-Hit Effects ────────────────────────────────────────────────────────
    if action_name == "Concussive Smash":
        prof = attacker.get("proficiency_bonus", 2)
        str_mod = _get_stat_mod(attacker, "STR")
        dc = 8 + prof + str_mod

        target_con_mod = _get_stat_mod(target, "CON")
        target_prof_saves = target.get("saving_throw_proficiencies", target.get("proficient_saves", []))
        if not target_prof_saves and "_player_state" in target:
            target_prof_saves = target["_player_state"].get("saving_throw_proficiencies", [])
        if "CON" in target_prof_saves:
            target_con_mod += target.get("proficiency_bonus", 2)

        con_roll = _roll_d20()
        con_total = con_roll + target_con_mod
        save_passed = (con_roll == 20) or (con_roll != 1 and con_total >= dc)

        result["save_dc"] = dc
        result["save_roll"] = con_roll
        result["save_total"] = con_total
        result["save_passed"] = save_passed

        if save_passed:
            result["narration"] = (
                f"{attacker_name} hits {target_name} with a Concussive Smash for {result['damage']} bludgeoning damage, "
                f"but {target_name} withstands the concussion with a DC {dc} CON save ({con_total})!"
            )
        else:
            apply_condition(target, "dazed", duration=1)
            result["condition_applied"] = "dazed"
            result["narration"] = (
                f"{attacker_name} smashes {target_name} with a Concussive Smash for {result['damage']} damage! "
                f"{target_name} fails the DC {dc} CON save ({con_total}) and is DAZED (-1 AC, disadvantage on attacks)!"
            )

    elif action_name == "Cleave":
        cleave_damage = max(1, _get_stat_mod(attacker, "STR"))
        if adjacent_target is None and combat_state:
            for e in combat_state.get("enemies", []):
                if e.get("id") != target.get("id") and e.get("hp", {}).get("current", 0) > 0:
                    adjacent_target = e
                    break

        if adjacent_target:
            adj_hp = adjacent_target.setdefault("hp", {"current": 1, "max": 1})
            adj_hp["current"] = max(0, adj_hp["current"] - cleave_damage)
            adj_downed = adj_hp["current"] <= 0
            result["cleave_target_id"] = adjacent_target.get("id")
            result["cleave_target_name"] = adjacent_target.get("name")
            result["cleave_damage"] = cleave_damage
            result["cleave_target_hp_after"] = adj_hp["current"]
            result["cleave_target_downed"] = adj_downed
            adj_msg = f", downing them!" if adj_downed else f" ({adj_hp['current']} HP remaining)!"
            result["narration"] = (
                f"{attacker_name} strikes {target_name} with a Cleave for {result['damage']} slashing damage, "
                f"and arcs the blade into {adjacent_target.get('name')} for {cleave_damage} damage{adj_msg}"
            )
        else:
            result["narration"] = (
                f"{attacker_name} hits {target_name} with a sweeping Cleave for {result['damage']} slashing damage!"
            )

    elif action_name == "Hamstring":
        apply_condition(target, "slowed", duration=2)
        result["condition_applied"] = "slowed"
        result["narration"] = (
            f"{attacker_name} pierces {target_name} with a Hamstring strike for {result['damage']} piercing damage! "
            f"{target_name} is SLOWED for 2 rounds!"
        )

    return result


# ════════════════════════════════════════════════════════════════════════════════
# TURN RESOLUTION
# ════════════════════════════════════════════════════════════════════════════════

def resolve_enemy_turn(
    enemy: Dict[str, Any],
    targets: List[Dict[str, Any]],
    combat_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Resolve an enemy's turn: pick a target and attack with the first non-None attack.
    Enemies always attack the first living target in the targets list (simple AI for v1).
    If the enemy is stunned, turn is skipped.

    Args:
        enemy:   the enemy combatant dict.
        targets: list of potential targets (player + companions), all alive.
        combat_state: optional live combat_state dict.

    Returns:
        result dict from resolve_attack, or a 'skip' result if stunned or no targets.
    """
    if "stunned" in _get_condition_set(enemy):
        logger.debug(f"resolve_enemy_turn: {enemy.get('name')} is stunned — skip.")
        return {"attacker_id": enemy["id"], "attacker_name": enemy.get("name", ""),
                "skipped": True, "reason": "stunned"}

    living_targets = [t for t in targets if t.get("hp", {}).get("current", 0) > 0]
    if not living_targets:
        return {"attacker_id": enemy["id"], "attacker_name": enemy.get("name", ""),
                "skipped": True, "reason": "no_targets"}

    target  = living_targets[0]
    attacks = enemy.get("attacks", [])
    if not attacks:
        return {"attacker_id": enemy["id"], "attacker_name": enemy.get("name", ""),
                "skipped": True, "reason": "no_attacks"}

    return resolve_attack(enemy, target, attacks[0], combat_state=combat_state)


def resolve_companion_turn(
    companion: Dict[str, Any],
    enemies: List[Dict[str, Any]],
    combat_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Resolve a companion's turn: attack the lowest-HP living enemy.
    If the companion is stunned, turn is skipped.

    Args:
        companion: the companion combatant dict.
        enemies:   list of enemy combatants.
        combat_state: optional live combat_state dict.

    Returns:
        result dict from resolve_attack, or a 'skip' result.
    """
    if "stunned" in _get_condition_set(companion):
        logger.debug(f"resolve_companion_turn: {companion.get('name')} is stunned — skip.")
        return {"attacker_id": companion["id"], "attacker_name": companion.get("name", ""),
                "skipped": True, "reason": "stunned"}

    living_enemies = [e for e in enemies if e.get("hp", {}).get("current", 0) > 0]
    if not living_enemies:
        return {"attacker_id": companion["id"], "attacker_name": companion.get("name", ""),
                "skipped": True, "reason": "no_targets"}

    # Target lowest-HP enemy (smart-ish companion AI)
    target  = min(living_enemies, key=lambda e: e.get("hp", {}).get("current", 9999))
    attacks = companion.get("attacks", [])
    if not attacks:
        return {"attacker_id": companion["id"], "attacker_name": companion.get("name", ""),
                "skipped": True, "reason": "no_attacks"}

    return resolve_attack(companion, target, attacks[0], combat_state=combat_state)


# ════════════════════════════════════════════════════════════════════════════════
# ROUND SIGNIFICANCE CLASSIFICATION (spec Section 9b-4)
# ════════════════════════════════════════════════════════════════════════════════

def classify_round_significance(
    round_results: List[Dict[str, Any]],
    combat_state: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Split all round events into 'significant' and 'routine' lists.
    Spec 9b-4:
      Significant: crit, fumble, downed/killed, any condition applied, any spell/special
                   item use, HP dropping below 25% for any named combatant.
      Routine:     a normal hit or miss with no special outcome.

    Args:
        round_results: list of result dicts from resolve_attack (and skip events).
        combat_state:  current combat_state (used to look up HP thresholds).

    Returns:
        {
          "significant": [result, ...],
          "routine":     [result, ...],
        }
    """
    significant = []
    routine     = []

    def _is_below_25pct(result: Dict[str, Any]) -> bool:
        """Check if target HP after attack is below 25% of max."""
        # Look up max HP from the live combatant
        target_id = result.get("target_id")
        for pool in ("enemies", "companions"):
            for c in combat_state.get(pool, []):
                if c["id"] == target_id:
                    hp_max = c.get("hp", {}).get("max", 1)
                    hp_cur = result.get("target_hp_after", 0)
                    return hp_cur < hp_max * 0.25
        # player
        if target_id == "player":
            pc = combat_state.get("player_combatant", {})
            hp_max = pc.get("hp", {}).get("max", 1)
            hp_cur = result.get("target_hp_after", 0)
            return hp_cur < hp_max * 0.25
        return False

    for result in round_results:
        if result.get("skipped"):
            routine.append(result)
            continue

        is_sig = (
            result.get("crit")
            or result.get("fumble")
            or result.get("target_downed")
            or result.get("condition_applied") is not None
            or result.get("action_type") in ("shove", "weapon_action")
            or result.get("cleave_damage", 0) > 0
            or _is_below_25pct(result)
        )
        if is_sig:
            significant.append(result)
        else:
            routine.append(result)

    return {"significant": significant, "routine": routine}


# ════════════════════════════════════════════════════════════════════════════════
# ROUTINE HIT TEMPLATE BANK (spec Section 9b-ii)
# ════════════════════════════════════════════════════════════════════════════════

_HIT_TEMPLATES = [
    "{attacker} lands a solid hit on {target} for {damage} {dtype} damage.",
    "{target} staggers as {attacker}'s {weapon} connects for {damage}.",
    "{attacker}'s {weapon} strikes {target} for {damage} {dtype} damage.",
]
_MISS_TEMPLATES = [
    "{attacker}'s {weapon} goes wide, missing {target} entirely.",
    "{target} sidesteps {attacker}'s {weapon}.",
    "{attacker} swings at {target} but fails to connect.",
]

def build_routine_summary(routine_results: List[Dict[str, Any]]) -> str:
    """
    Build a plain-text summary of routine hits/misses using canned templates.
    Spec 9b-ii: requires zero LLM involvement.

    Returns:
        A multi-line string with one sentence per routine event.
    """
    lines = []
    for r in routine_results:
        if r.get("skipped"):
            name = r.get("attacker_name", "Someone")
            reason = r.get("reason", "skipped")
            lines.append(f"{name} skips their turn ({reason}).")
            continue

        if r.get("action_type") in ("shove", "weapon_action"):
            lines.append(r.get("narration", ""))
            continue

        attacker = r.get("attacker_name", "?")
        target   = r.get("target_name", "?")
        weapon   = r.get("attack_name", "attack")
        damage   = r.get("damage", 0)
        dtype    = r.get("damage_type", "")

        if r.get("hit"):
            tmpl = random.choice(_HIT_TEMPLATES)
        else:
            tmpl = random.choice(_MISS_TEMPLATES)

        lines.append(
            tmpl.format(attacker=attacker, target=target, weapon=weapon,
                        damage=damage, dtype=dtype)
        )
    return "\n".join(lines) if lines else ""


def build_round_narration_block(
    round_num: int,
    significant: List[Dict[str, Any]],
    routine_summary: str,
) -> str:
    """
    Assemble the '[System: Round Result]' block sent to the narrative LLM.
    Spec 9b-iii format:
      [System: Round Result]
      Round N:
      - <significant event line>
      - (Routine: <summary>)
      Narrate this round based strictly on these results...

    Args:
        round_num:       current round number.
        significant:     list of significant result dicts.
        routine_summary: pre-built plain text for routine events.

    Returns:
        The complete string to inject as the system content for the narration call.
    """
    lines = [f"[System: Round Result]", f"Round {round_num}:"]

    for r in significant:
        if r.get("action_type") in ("shove", "weapon_action"):
            lines.append(f"- {r.get('narration')}")
            continue

        attacker = r.get("attacker_name", "?")
        target   = r.get("target_name", "?")
        weapon   = r.get("attack_name", "attack")
        damage   = r.get("damage", 0)

        if r.get("fumble"):
            lines.append(f"- {attacker} critically fumbles with {weapon} — complete miss.")
        elif r.get("crit"):
            status = "defeated" if r.get("target_downed") else f"{r.get('target_hp_after')} HP remaining"
            lines.append(f"- {attacker} critically hits {target} with {weapon}: {damage} damage, {status}.")
        elif r.get("target_downed"):
            lines.append(f"- {attacker} attacks {target} with {weapon}: {damage} damage, {target} is downed.")
        elif r.get("condition_applied"):
            cond = r["condition_applied"]
            lines.append(f"- {attacker} attacks {target} with {weapon}: {damage} damage, {target} is now {cond}.")
        else:
            # Below 25% HP case
            lines.append(
                f"- {attacker} attacks {target} with {weapon}: {damage} damage "
                f"({r.get('target_hp_after')} HP remaining — critically low)."
            )

    if routine_summary:
        lines.append(f"- (Routine: {routine_summary})")

    lines.append(
        "Narrate this round based strictly on these results. "
        "Do not invent additional actions or change any outcome."
    )
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════════════
# COMBAT END CHECK
# ════════════════════════════════════════════════════════════════════════════════

def check_combat_end(combat_state: Dict[str, Any], world_state: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """
    Check whether combat should end (spec 9b-5 & Section 17a).

    Returns:
        'player_victory'  — all enemies are downed.
        'player_defeat'   — player death save failures >= 3 or downed_outcome present.
        None              — combat continues (e.g. player at 0 HP still accumulating death saves).
    """
    if combat_state.get("status") == "ended":
        return combat_state.get("outcome")

    enemies  = combat_state.get("enemies", [])
    player_c = combat_state.get("player_combatant", {})

    all_enemies_down = len(enemies) > 0 and all(e.get("hp", {}).get("current", 0) <= 0 for e in enemies)
    
    if all_enemies_down:
        combat_state["status"]  = "ended"
        combat_state["outcome"] = "player_victory"
        logger.debug("check_combat_end: player_victory — all enemies downed.")

        # Award XP for defeated enemies (spec Section 17b)
        import state_manager
        total_xp = sum(e.get("xp_value", 50) for e in enemies)
        p_state = player_c.get("_player_state", player_c)

        state_manager.award_xp(total_xp, p_state)
        combat_state["xp_gained"] = total_xp

        leveled_up = False
        while state_manager.check_level_up(p_state):
            state_manager.apply_level_up(p_state)
            leveled_up = True
        combat_state["leveled_up"] = leveled_up

        # Soldier background inspiration trigger: won combat without losing a teammate
        companions = combat_state.get("companions", [])
        teammate_lost = any(comp.get("hp", {}).get("current", 0) <= 0 for comp in companions)
        if not teammate_lost and p_state.get("hp", {}).get("current", 0) > 0:
            state_manager.check_inspiration_trigger(p_state, "combat_victory_no_casualties", {"success": True})

        # Check active notice board bounties completion
        if world_state and isinstance(world_state, dict):
            cur_loc = world_state.get("current_location")
            enemy_names = [e.get("monster_id", e.get("id", "")) for e in enemies]
            nb = world_state.get("notice_board", {})
            for entry in list(nb.get("entries", [])):
                if entry.get("type") == "bounty" and entry.get("status") == "accepted":
                    t_room = entry.get("target_room_id")
                    t_mon = entry.get("target_monster")
                    if (not t_room or t_room == cur_loc) and any(t_mon in en for en in enemy_names):
                        import dungeon_manager
                        dungeon_manager.complete_notice_board_quest(world_state, entry["id"], p_state)

        for key in ("currency", "gold", "status", "xp_current", "level", "proficiency_bonus", "ac", "inspiration"):
            if key in p_state:
                player_c[key] = p_state[key]
        sync_player_state(player_c)

        return "player_victory"

    player_fails = player_c.get("death_saves", {}).get("fail", 0)
    has_downed_outcome = combat_state.get("downed_outcome") is not None

    if player_fails >= 3 or has_downed_outcome:
        combat_state["status"]  = "ended"
        combat_state["outcome"] = "player_defeat"
        if not has_downed_outcome:
            import state_manager
            downed_res = state_manager.resolve_downed_outcome(player_c, combat_state, world_state)
            combat_state["downed_outcome"] = downed_res
        sync_player_state(player_c)
        logger.debug("check_combat_end: player_defeat — player death save fails >= 3 or downed_outcome present.")
        return "player_defeat"

    return None


# ════════════════════════════════════════════════════════════════════════════════
# ROUND ORCHESTRATION (spec Section 9b-3)
# ════════════════════════════════════════════════════════════════════════════════

def resolve_round(
    combat_state: Dict[str, Any],
    player_attack_result: Optional[Dict[str, Any]] = None,
    world_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Orchestrate a full combat round in initiative order and return a complete
    round summary dict for the narrative LLM.
    """
    # Defensive guard for positional argument mismatch: if caller passed world_state as 2nd arg
    if player_attack_result is not None and world_state is None:
        if isinstance(player_attack_result, dict) and ("schema_version" in player_attack_result or "current_location" in player_attack_result):
            world_state = player_attack_result
            player_attack_result = None

    round_num      = combat_state["round"]
    turn_order     = combat_state["turn_order"]
    player_c       = combat_state["player_combatant"]
    enemies        = combat_state["enemies"]     # live reference — mutated in place
    companions     = combat_state["companions"]  # live reference — mutated in place

    round_results: List[Dict[str, Any]] = []

    # ── Include player's action (supplied by caller) ───────────────────────────
    if player_attack_result is not None:
        round_results.append(player_attack_result)

    # ── Resolve all non-player combatants in initiative order ─────────────────
    for cid in turn_order:
        if cid == "player":
            continue  # player already handled above

        # Identify the combatant by id
        combatant = None
        for e in enemies:
            if e["id"] == cid:
                combatant = e
                break
        if combatant is None:
            for comp in companions:
                if comp["id"] == cid:
                    combatant = comp
                    break

        if combatant is None:
            logger.debug(f"resolve_round: combatant '{cid}' not found in enemies or companions — skipped.")
            continue

        # Skip already-downed combatants
        if combatant.get("hp", {}).get("current", 0) <= 0:
            continue

        if combatant["side"] == "enemy":
            # Targets: living player + companions
            player_alive = player_c.get("hp", {}).get("current", 0) > 0
            living_targets = ([player_c] if player_alive else []) + [
                comp for comp in companions if comp.get("hp", {}).get("current", 0) > 0
            ]
            result = resolve_enemy_turn(combatant, living_targets, combat_state=combat_state)
        else:
            # companion side — targets living enemies
            result = resolve_companion_turn(combatant, enemies, combat_state=combat_state)

        round_results.append(result)

    # ── Process death saves for downed player or companions ───────────────────
    import state_manager
    if player_c.get("hp", {}).get("current", 0) <= 0:
        ds_res = state_manager.resolve_death_save(player_c)
        if ds_res.get("exhausted"):
            downed_res = state_manager.resolve_downed_outcome(player_c, combat_state, world_state)
            combat_state["downed_outcome"] = downed_res

    for comp in companions:
        if comp.get("hp", {}).get("current", 0) <= 0:
            ds_res = state_manager.resolve_death_save(comp)
            if ds_res.get("exhausted"):
                state_manager.resolve_downed_outcome(comp, combat_state, world_state)

    # ── Persist round results into round_log ──────────────────────────────────
    combat_state["round_log"].append(round_results)

    # ── Classify significance ─────────────────────────────────────────────────
    classified   = classify_round_significance(round_results, combat_state)
    significant  = classified["significant"]
    routine      = classified["routine"]

    # ── Tick conditions & surfaces (end of round) ────────────────────────────
    tick_conditions(combat_state)
    tick_surface(combat_state)

    # ── Increment round counter ───────────────────────────────────────────────
    combat_state["round"] += 1

    # ── Build narration artefacts ─────────────────────────────────────────────
    routine_summary = build_routine_summary(routine)
    narration_block = build_round_narration_block(round_num, significant, routine_summary)

    # ── Check for combat end ──────────────────────────────────────────────────
    combat_outcome = check_combat_end(combat_state, world_state)
    sync_player_state(player_c)

    logger.debug(
        f"resolve_round: round {round_num} done. "
        f"sig={len(significant)}, routine={len(routine)}, outcome={combat_outcome}"
    )

    return {
        "round_num":       round_num,
        "significant":     significant,
        "routine":         routine,
        "routine_summary": routine_summary,
        "narration_block": narration_block,
        "combat_outcome":  combat_outcome,
    }
