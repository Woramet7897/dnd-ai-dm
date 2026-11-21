"""
state/cooking.py — Food freshness spoilage and camp cooking system (Module B §5 / Phase 12.3).
"""

import logging
from typing import Any, Dict, List, Optional, Union

from rules import _get_item_catalog, get_modifier, is_proficient
from state.checks import check_inspiration_trigger
from state.equipment import apply_condition_to_state, get_active_effects

logger = logging.getLogger("state_manager")


def tick_food_spoilage(
    character_state: Dict[str, Any],
    days: int = 1,
) -> List[Dict[str, Any]]:
    """
    Module B §5 / Phase 12.3:
    Tick down freshness_days on raw_food items in character inventory by `days`.
    When freshness_days <= 0, flips the item to 'rotten_food'.
    Returns list of items that spoiled during this tick.
    """
    if not character_state or not isinstance(character_state, dict):
        return []

    inventory = character_state.get("inventory", [])
    if not isinstance(inventory, list):
        return []

    catalog = _get_item_catalog()
    spoiled_items = []

    for item in inventory:
        if not isinstance(item, dict):
            continue

        item_id = item.get("item_id", "")
        item_info = catalog.get(item_id, {})

        if (
            item.get("rotten")
            or item_id == "rotten_food"
            or item.get("type") in ("rotten_food", "spoiled_food")
            or item_info.get("rotten")
            or item_info.get("type") in ("rotten_food", "spoiled_food")
        ):
            continue

        is_raw = (
            item.get("raw_food") is True
            or item.get("type") == "raw_food"
            or item_info.get("raw_food") is True
            or item_info.get("type") == "raw_food"
            or "freshness_days" in item
            or "freshness_days" in item_info
        )

        if is_raw:
            if "freshness_days" not in item:
                item["freshness_days"] = item_info.get("freshness_days", 5)

            item["freshness_days"] -= days
            if item["freshness_days"] <= 0:
                item["freshness_days"] = 0
                item["item_id"] = "rotten_food"
                item["name"] = "Rotten Food"
                item["type"] = "rotten_food"
                item["raw_food"] = False
                item["rotten"] = True
                item["description"] = "Spoiled, foul-smelling food. Consuming or cooking with it carries great risk."
                spoiled_items.append(item)
                logger.info(f"Item '{item_id}' spoiled into rotten_food.")

    return spoiled_items


def cook_meal(
    player_state: Dict[str, Any],
    ingredient_1: Union[str, Dict[str, Any]],
    ingredient_2: Union[str, Dict[str, Any]],
    survival_roll: Optional[int] = None,
    con_save_roll: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Module B §5 / Phase 12.3:
    Camp cooking:
    - Survival check DC 12:
        Success: creates 'Hearty Stew' (+5 temp HP, clears 1 exhaustion stack).
        Failure: burns the ingredients.
    - Cooking with rotten_food: CON save DC 13 or Poisoned for 8 hours.
    """
    import state_manager
    inventory = player_state.get("inventory", [])
    catalog = _get_item_catalog()

    def _get_id(ing):
        if isinstance(ing, dict):
            return ing.get("item_id") or ing.get("id") or ing.get("name", "")
        return str(ing)

    id1 = _get_id(ingredient_1)
    id2 = _get_id(ingredient_2)

    def _matches(it, target_id):
        if not isinstance(it, dict):
            return False
        return it.get("item_id") == target_id or it.get("name") == target_id

    item1 = None
    if isinstance(ingredient_1, dict) and ingredient_1 in inventory:
        item1 = ingredient_1
    else:
        for it in inventory:
            if _matches(it, id1):
                item1 = it
                break

    if item1 is None:
        return {
            "success": False,
            "burned": False,
            "meal": None,
            "item_created": None,
            "temp_hp": 0,
            "exhaustion_cleared": False,
            "poisoned": False,
            "message": f"Missing ingredient '{id1}' in inventory.",
        }

    item2 = None
    if isinstance(ingredient_2, dict) and ingredient_2 in inventory and ingredient_2 is not item1:
        item2 = ingredient_2
    else:
        if id1 == id2 or (item1 and _matches(item1, id2)):
            qty1 = item1.get("quantity", 1)
            if qty1 >= 2:
                item2 = item1
            else:
                for it in inventory:
                    if it is not item1 and _matches(it, id2):
                        item2 = it
                        break
        else:
            for it in inventory:
                if it is not item1 and _matches(it, id2):
                    item2 = it
                    break

    if item2 is None:
        return {
            "success": False,
            "burned": False,
            "meal": None,
            "item_created": None,
            "temp_hp": 0,
            "exhaustion_cleared": False,
            "poisoned": False,
            "message": f"Missing ingredient '{id2}' in inventory.",
        }

    def _is_rotten(it, tid):
        if not isinstance(it, dict):
            return tid == "rotten_food"
        info = catalog.get(it.get("item_id", tid), {})
        return (
            it.get("rotten") is True
            or it.get("item_id") == "rotten_food"
            or tid == "rotten_food"
            or it.get("type") in ("rotten_food", "spoiled_food")
            or info.get("rotten") is True
            or info.get("type") in ("rotten_food", "spoiled_food")
            or ("freshness_days" in it and it["freshness_days"] <= 0)
        )

    is_rotten = _is_rotten(item1, id1) or _is_rotten(item2, id2)

    qty1 = item1.get("quantity", 1)
    if qty1 > 1:
        item1["quantity"] = qty1 - 1
    else:
        if item1 in inventory:
            inventory.remove(item1)

    qty2 = item2.get("quantity", 1)
    if qty2 > 1:
        item2["quantity"] = qty2 - 1
    else:
        if item2 in inventory:
            inventory.remove(item2)

    poisoned_applied = False
    con_save_info = None

    if is_rotten:
        stats = player_state.get("stats", {})
        con_val = stats.get("CON", 10)
        con_mod = get_modifier(con_val)
        prof_saves = player_state.get("saving_throw_proficiencies", player_state.get("proficient_saves", []))
        prof_bonus = player_state.get("proficiency_bonus", 2) if "CON" in prof_saves else 0
        active_eff = get_active_effects(player_state)
        save_bonus = active_eff.get("saving_throw_bonus", 0)

        con_roll = con_save_roll if con_save_roll is not None else state_manager._roll_d20()
        if con_roll == 20:
            con_success = True
        elif con_roll == 1:
            con_success = False
        else:
            con_total = con_roll + con_mod + prof_bonus + save_bonus
            con_success = (con_total >= 13)

        con_save_info = {
            "roll": con_roll,
            "modifier": con_mod,
            "prof_bonus": prof_bonus,
            "save_bonus": save_bonus,
            "total": con_roll + con_mod + prof_bonus + save_bonus,
            "dc": 13,
            "success": con_success,
        }

        if not con_success:
            apply_condition_to_state(player_state, "poisoned", duration_rounds=8, duration_hours=8)
            player_state["poisoned"] = True
            poisoned_applied = True
            logger.info("cook_meal: failed CON save vs rotten food! Applied Poisoned for 8 hours.")
        else:
            logger.info("cook_meal: succeeded CON save vs rotten food.")

    stats = player_state.get("stats", {})
    wis_val = stats.get("WIS", 10)
    wis_mod = get_modifier(wis_val)
    is_prof = is_proficient("Survival", player_state)
    prof_bonus = player_state.get("proficiency_bonus", 2) if is_prof else 0
    active_eff = get_active_effects(player_state)
    skill_bonus = active_eff.get("skill_bonus", {}).get("Survival", 0)

    cond_names = [c["condition"] if isinstance(c, dict) else c for c in player_state.get("active_conditions", [])]
    has_disadvantage = any(c in ("poisoned", "exhausted") for c in cond_names)

    if survival_roll is not None:
        s_roll = survival_roll
    elif has_disadvantage:
        r1, r2 = state_manager._roll_d20(), state_manager._roll_d20()
        s_roll = min(r1, r2)
    else:
        s_roll = state_manager._roll_d20()

    if s_roll == 20:
        surv_success = True
    elif s_roll == 1:
        surv_success = False
    else:
        s_total = s_roll + wis_mod + prof_bonus + skill_bonus
        surv_success = (s_total >= 12)

    survival_check_info = {
        "roll": s_roll,
        "modifier": wis_mod,
        "proficiency": prof_bonus,
        "skill_bonus": skill_bonus,
        "total": s_roll + wis_mod + prof_bonus + skill_bonus,
        "dc": 12,
        "success": surv_success,
    }

    if surv_success:
        check_inspiration_trigger(player_state, "survival", {"success": True})

        existing_stew = next((it for it in inventory if isinstance(it, dict) and it.get("item_id") == "hearty_stew"), None)
        if existing_stew:
            existing_stew["quantity"] = existing_stew.get("quantity", 1) + 1
        else:
            inventory.append({
                "item_id": "hearty_stew",
                "name": "Hearty Stew",
                "type": "food",
                "quantity": 1,
                "effects": {"temp_hp": 5, "clear_exhaustion": 1},
                "description": "A thick, steaming bowl of savory camp stew. Provides +5 temporary HP and relieves exhaustion.",
            })

        hp = player_state.setdefault("hp", {"current": 10, "max": 10})
        hp["temp"] = max(hp.get("temp", 0), 5)
        player_state["temp_hp"] = hp["temp"]

        exhaustion_cleared = False
        if player_state.get("exhaustion_level", 0) > 0:
            player_state["exhaustion_level"] -= 1
            exhaustion_cleared = True

        active_conds = player_state.setdefault("active_conditions", [])
        if "exhausted" in active_conds:
            active_conds.remove("exhausted")
            exhaustion_cleared = True

        to_remove = []
        for c in active_conds:
            if isinstance(c, dict) and (c.get("condition") == "exhausted" or c.get("name") == "exhausted"):
                if c.get("duration", 1) > 1:
                    c["duration"] -= 1
                else:
                    to_remove.append(c)
                exhaustion_cleared = True
        for c in to_remove:
            active_conds.remove(c)

        msg = "Successfully cooked a Hearty Stew! Gained +5 temporary HP and relieved exhaustion."
        if poisoned_applied:
            msg += " However, you fell ill from rotten food and are Poisoned for 8 hours!"

        return {
            "success": True,
            "burned": False,
            "meal": "Hearty Stew",
            "item_created": "hearty_stew",
            "temp_hp": 5,
            "exhaustion_cleared": exhaustion_cleared,
            "poisoned": poisoned_applied,
            "con_save": con_save_info,
            "survival_check": survival_check_info,
            "message": msg,
        }
    else:
        msg = "You burned the ingredients! The meal was ruined."
        if poisoned_applied:
            msg += " And you fell ill from rotten food and are Poisoned for 8 hours!"

        return {
            "success": False,
            "burned": True,
            "meal": None,
            "item_created": None,
            "temp_hp": 0,
            "exhaustion_cleared": False,
            "poisoned": poisoned_applied,
            "con_save": con_save_info,
            "survival_check": survival_check_info,
            "message": msg,
        }
