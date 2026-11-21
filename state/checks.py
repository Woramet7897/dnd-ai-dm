"""
state/checks.py — Inspiration points, ability checks, death saves, concentration checks, and state/quest update application.
"""

import copy
import logging
import re as _re
from typing import Any, Dict, Optional, Tuple

import state_manager
from rules import (
    CONCENTRATION_DC_FLOOR,
    DIFFICULTY_TO_DC,
    _get_item_catalog,
    get_modifier,
    is_proficient,
)
from state.currency import _ensure_currency, add_currency, subtract_currency

logger = logging.getLogger("state_manager")

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
    """Check if a game event satisfies the character's background inspiration trigger."""
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
    Uses state_manager._roll_d20() and state_manager._roll_dice() so test patches apply cleanly.
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

    if advantage and not disadvantage:
        roll1, roll2 = state_manager._roll_d20(), state_manager._roll_d20()
        roll = max(roll1, roll2)
    elif disadvantage and not advantage:
        roll1, roll2 = state_manager._roll_d20(), state_manager._roll_d20()
        roll = min(roll1, roll2)
    else:
        roll = state_manager._roll_d20()

    if inspiration_used:
        reroll = state_manager._roll_d20()
        roll = max(roll, reroll)

    critical = (roll == 20)
    fumble = (roll == 1)

    bonus = state_manager._roll_dice(bonus_dice) if bonus_dice else 0

    total = roll + modifier + prof_contribution + bonus

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
    """Spend 1 inspiration point to reroll an ability check."""
    if not spend_inspiration(state):
        res = dict(previous_result)
        res["rerolled"] = False
        res["reason"] = "No inspiration points available."
        return res

    new_roll = state_manager._roll_d20()
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
    """Roll a death saving throw for a downed character."""
    roll = state_manager._roll_d20()
    death_saves = state.setdefault("death_saves", {"success": 0, "fail": 0})

    stabilized = False
    exhausted = False

    if roll == 20:
        stabilized = True
        death_saves["success"] = 0
        death_saves["fail"] = 0
        state["hp"]["current"] = 1
        state["status"] = "normal"
        success = True
    elif roll == 1:
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
        death_saves["fail"] = 0
        state["status"] = "normal"

    if death_saves["fail"] >= 3:
        exhausted = True

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
    """Auto-triggered inside apply_state_updates() whenever hp_change < 0 and concentration is active."""
    dc_value = max(CONCENTRATION_DC_FLOOR, damage_taken // 2)

    stats = state.get("stats", {})
    con_value = stats.get("CON", 10)
    modifier = get_modifier(con_value)
    proficient_saves = state.get("proficient_saves", [])
    proficient = "CON" in proficient_saves
    prof_bonus = state.get("proficiency_bonus", 2)
    prof_contribution = prof_bonus if proficient else 0

    roll = state_manager._roll_d20()
    critical = (roll == 20)
    fumble = (roll == 1)
    total = roll + modifier + prof_contribution

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
    """Append a roll result to state['roll_log'] (keeps last 50 entries)."""
    roll_log = state.setdefault("roll_log", [])
    roll_log.append(entry)
    if len(roll_log) > 50:
        state["roll_log"] = roll_log[-50:]


def apply_state_updates(
    updates: Dict[str, Any],
    state: Dict[str, Any],
    world_state: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Apply validated extraction-call updates to the character state (and optionally world state)."""
    concentration_result = None

    if "hp_change" in updates:
        delta = updates["hp_change"]
        hp = state.setdefault("hp", {"current": 10, "max": 10})
        old_hp = hp["current"]
        hp["current"] = max(0, min(hp["max"], old_hp + delta))

        if delta < 0 and state.get("concentration") is not None:
            damage_taken = abs(delta)
            concentration_result = state_manager.resolve_concentration_check(damage_taken, state)
            log_roll({"type": "concentration_check", **concentration_result}, state)

        if hp["current"] == 0 and state.get("status") == "normal":
            state["status"] = "downed"
            logger.debug("Character downed at 0 HP.")

    if "gold_change" in updates:
        _ensure_currency(state)
        change = updates["gold_change"]
        if change >= 0:
            add_currency(state, gp=change)
        else:
            if not subtract_currency(state, gp=abs(change)):
                state["currency"] = {"gp": 0, "sp": 0, "cp": 0}
                state["gold"] = 0

    if "add_item_id" in updates:
        item_id = updates["add_item_id"]
        inventory = state.setdefault("inventory", [])
        existing = next((i for i in inventory if i.get("item_id") == item_id), None)
        if existing and existing.get("quantity") is not None:
            existing["quantity"] = existing.get("quantity", 1) + 1
            if "definition" not in existing and world_state and "generated_items" in world_state:
                gen_def = world_state["generated_items"].get(item_id)
                if gen_def:
                    existing["definition"] = copy.deepcopy(gen_def)
        elif not existing:
            item_entry: Dict[str, Any] = {"item_id": item_id, "equipped": False, "quantity": 1}
            cat = _get_item_catalog()
            if item_id not in cat and world_state and "generated_items" in world_state:
                gen_def = world_state["generated_items"].get(item_id)
                if gen_def:
                    item_entry["definition"] = copy.deepcopy(gen_def)
            inventory.append(item_entry)

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

    if world_state is not None and "move_to_location_id" in updates:
        dest_id = updates["move_to_location_id"]
        world_state["current_location"] = dest_id
        visited = world_state.setdefault("visited_rooms", [])
        if dest_id not in visited:
            visited.append(dest_id)

    if world_state is not None and "recruit_companion_id" in updates:
        cid = updates["recruit_companion_id"]
        gen_npcs = world_state.get("generated_npcs", {})
        npc_data = gen_npcs.get(cid)
        if not npc_data:
            former = world_state.get("party", {}).get("former_companions", [])
            npc_data = next((c for c in former if c.get("id") == cid), None)
        if npc_data:
            state_manager.recruit_companion(npc_data, world_state)

    if "learn_spell_id" in updates or "add_spell_id" in updates:
        sid = updates.get("learn_spell_id") or updates.get("add_spell_id")
        if sid:
            state_manager.learn_spell(sid, state, world_state=world_state)

    return state, concentration_result


def apply_npc_relationship_change(
    nrc: Dict[str, Any],
    world_state: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Apply an npc_relationship_change dict ({'npc_id': str, 'delta': int}) to world_state.
    Updates both world_state['npc_relationships'][npc_id] and, if npc_id is a party companion,
    updates world_state['companions_approval'][npc_id]['approval'] as well!
    """
    if not isinstance(nrc, dict) or not isinstance(world_state, dict):
        return None
    npc_id = nrc.get("npc_id")
    delta = nrc.get("delta")
    if not isinstance(npc_id, str) or not isinstance(delta, (int, float)):
        return None

    delta_int = int(delta)
    rels = world_state.setdefault("npc_relationships", {})
    old_rel = rels.get(npc_id, 0)
    rels[npc_id] = max(-100, min(100, old_rel + delta_int))

    # Also check if npc_id matches a companion in party
    companions = world_state.get("party", {}).get("companions", [])
    comp_match = next(
        (c for c in companions if isinstance(c, dict) and (c.get("id") == npc_id or c.get("name") == npc_id)),
        None,
    )
    comp_name = npc_id
    if comp_match:
        cid = comp_match.get("id") or comp_match.get("name")
        comp_name = comp_match.get("name", cid)
        app_info = state_manager.get_companion_approval(world_state, cid)
        app_info["approval"] = max(0, min(100, app_info.get("approval", 50) + delta_int))
    else:
        gen_npc = world_state.get("generated_npcs", {}).get(npc_id)
        if isinstance(gen_npc, dict):
            comp_name = gen_npc.get("name", npc_id)

    return {
        "npc_id": npc_id,
        "npc_name": comp_name,
        "delta": delta_int,
        "new_attitude": rels[npc_id],
    }


def apply_quest_updates(
    quest_updates: Dict[str, Any],
    world_state: Dict[str, Any],
) -> None:
    """Apply a validated quest_updates dict to world_state."""
    quest_log = world_state.setdefault("quest_log", {"main": [], "side": []})

    if "new_quest" in quest_updates:
        nq = quest_updates["new_quest"]
        quest_type = nq.get("quest_type", "side")
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
        qid = ou["quest_id"]
        obj_idx = ou["objective_index"]
        done = ou["done"]
        found = False
        for category in ("main", "side"):
            for quest in quest_log.get(category, []):
                if quest.get("title") == qid or quest.get("id") == qid:
                    objectives = quest.setdefault("objectives", [])
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
