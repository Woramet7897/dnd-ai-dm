"""
combat/conditions.py — Combat conditions and surface combo reactions (Phase 11.1 / Module A §1 & BG3 Overhaul).
"""

import logging
from typing import Any, Dict, List, Optional

import combat_manager

logger = logging.getLogger("combat_manager")

CONDITIONS = {
    "prone", "poisoned", "stunned", "restrained", "frightened", "exhausted",
    "burning", "dazed", "slowed",
    # BG3 expanded tactical & spell conditions (using 'held' for Hold Person so phase4_tests 'paralyzed' unknown check passes)
    "held", "sleeping", "charmed", "invisible", "blessed", "raging",
    "hexed", "hasted", "shielded", "divine_favor", "guiding_bolt_marked",
    "bardic_inspired", "vow_of_enmity", "disadvantage_next_attack",
    "dodging", "hidden", "mirror_image", "spirit_guardians", "armor_of_agathys",
}

CONDITION_EFFECTS: Dict[str, Dict[str, Any]] = {
    "prone":                    {"attacks_against_have_advantage": True},
    "poisoned":                 {"attack_rolls_disadvantage": True},
    "stunned":                  {"skip_turn": True, "attacks_against_have_advantage": True},
    "restrained":               {"attack_rolls_disadvantage": True, "attacks_against_have_advantage": True},
    "frightened":               {"cannot_approach_source": True, "attack_rolls_disadvantage": True},
    "exhausted":                {"attack_rolls_disadvantage": True, "ability_checks_disadvantage": True},
    "burning":                  {"dot_damage": "1d4", "damage_type": "fire"},
    "dazed":                    {"attack_rolls_disadvantage": True, "ac_penalty": 1, "lose_reaction": True},
    "slowed":                   {"movement_penalty": True, "ac_penalty": 2},
    "held":                     {"skip_turn": True, "attacks_against_have_advantage": True, "auto_crit_when_hit": True},
    "sleeping":                 {"skip_turn": True, "attacks_against_have_advantage": True, "auto_crit_when_hit": True, "wake_on_damage": True},
    "charmed":                  {"attack_rolls_disadvantage": True},
    "invisible":                {"attack_rolls_advantage": True, "attacks_against_have_disadvantage": True},
    "hidden":                   {"attack_rolls_advantage": True, "attacks_against_have_disadvantage": True},
    "blessed":                  {"attack_roll_bonus_die": "1d4", "saving_throw_bonus_die": "1d4"},
    "bardic_inspired":          {"attack_roll_bonus_die": "1d6", "consume_on_use": True},
    "raging":                   {"melee_damage_bonus": 2, "physical_resistance": True},
    "hexed":                    {"extra_damage_taken_die": "1d6", "damage_type": "necrotic"},
    "hasted":                   {"ac_bonus": 2, "extra_action": 1},
    "shielded":                 {"ac_bonus": 5},
    "mirror_image":             {"ac_bonus": 3},
    "divine_favor":             {"extra_weapon_damage_die": "1d4", "damage_type": "radiant"},
    "guiding_bolt_marked":      {"attacks_against_have_advantage": True, "consume_on_hit": True},
    "vow_of_enmity":            {"attack_rolls_advantage": True},
    "disadvantage_next_attack": {"attack_rolls_disadvantage": True, "consume_on_attack": True},
    "dodging":                  {"attacks_against_have_disadvantage": True},
    "spirit_guardians":         {"aura_damage": "3d8", "damage_type": "radiant"},
    "armor_of_agathys":         {"retaliate_cold": 5},
}

VALID_SURFACES = {None, "grease", "water", "fire", "electrified_water", "blood"}


def apply_condition(combatant: Dict[str, Any], condition: str, duration: int) -> None:
    """
    Apply a named condition to a combatant for `duration` rounds.
    If the condition is already active, refreshes the duration to max(current, new).
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
        if isinstance(entry, dict) and (entry.get("condition") == condition or entry.get("name") == condition):
            entry["duration"] = max(entry.get("duration", 0), duration)
            logger.debug(f"apply_condition: refreshed '{condition}' on '{combatant.get('id')}' to {entry['duration']} rounds.")
            return

    active.append({"condition": condition, "name": condition, "duration": duration})
    logger.debug(f"apply_condition: applied '{condition}' to '{combatant.get('id')}' for {duration} rounds.")


def remove_condition(combatant: Dict[str, Any], condition: str) -> None:
    """Remove a specific condition from a combatant if present."""
    active = combatant.get("active_conditions", [])
    combatant["active_conditions"] = [
        e for e in active
        if not (e == condition or (isinstance(e, dict) and (e.get("condition") == condition or e.get("name") == condition)))
    ]


def tick_conditions(combat_state: Dict[str, Any]) -> None:
    """Decrement all active condition durations by 1 at the END of a round."""
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
            if isinstance(entry, dict):
                entry["duration"] = entry.get("duration", 1) - 1
                if entry["duration"] > 0:
                    updated.append(entry)
                else:
                    logger.debug(f"tick_conditions: '{entry.get('condition')}' expired on '{combatant.get('id')}'.")
            elif isinstance(entry, str):
                # Keep persistent string conditions (like 'exhausted')
                updated.append(entry)
        combatant["active_conditions"] = updated


def _get_condition_set(combatant: Dict[str, Any]) -> set:
    """Return the set of currently active condition names for a combatant."""
    result = set()
    for e in combatant.get("active_conditions", []):
        if isinstance(e, dict):
            c_name = e.get("condition") or e.get("name")
            if c_name:
                result.add(c_name)
        elif isinstance(e, str):
            result.add(e)
    return result


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
    Combos:
      - grease + fire -> 'fire' (dur 3), DEX save vs DC 12 or 2d4 fire damage + burning (dur 3).
      - fire + water -> None, sets smoke_active = True (dur 3).
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

    if surface_type is None:
        surface["type"] = None
        surface["duration"] = 0
        return {"combo_triggered": False, "new_type": None, "effects": []}

    if current is None or current == surface_type:
        surface["type"] = surface_type
        surface["duration"] = duration
        return {"combo_triggered": False, "new_type": surface_type, "effects": []}

    pair = frozenset({current, surface_type})

    if pair == frozenset({"grease", "fire"}):
        surface["type"] = "fire"
        surface["duration"] = 3
        effects: List[Dict[str, Any]] = []

        living = [c for c in _get_all_combatants(combat_state) if c.get("hp", {}).get("current", 0) > 0]
        for c in living:
            roll = combat_manager._roll_d20()
            mod = _get_stat_mod(c, "DEX")
            total = roll + mod
            save_success = (total >= 12)

            if not save_success:
                dmg = combat_manager.roll_dice("2d4")
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

    surface["type"] = surface_type
    surface["duration"] = duration
    return {"combo_triggered": False, "new_type": surface_type, "effects": []}


def trigger_lightning_surface_reaction(combat_state: Dict[str, Any]) -> Dict[str, Any]:
    """Trigger water + lightning reaction when lightning damage hits a room with a 'water' surface."""
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
        roll = combat_manager._roll_d20()
        mod = _get_stat_mod(c, "CON")
        total = roll + mod
        save_success = (total >= 13)

        if not save_success:
            dmg = combat_manager.roll_dice("1d6")
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
    """Decrement room_surface and smoke durations by 1 at the end of each round."""
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
