"""
state/crime.py — Pickpocketing, guard persuasion, imprisonment, prison break, and bounty payments (Phase 13.0).
"""

import copy
import logging
from typing import Any, Dict, Optional, Tuple, Union

from rules import _get_item_catalog, get_modifier, is_proficient
from state.checks import check_inspiration_trigger
from state.currency import (
    CP_PER_GP,
    _ensure_currency,
    add_currency,
    can_afford,
    format_currency,
    get_total_copper,
    subtract_currency,
)
from state.equipment import _compute_ac, get_active_effects

logger = logging.getLogger("state_manager")


def attempt_pickpocket(
    player_state: Dict[str, Any],
    target_npc: Union[str, Dict[str, Any]],
    item_id: str,
    roll_override: Optional[int] = None,
    world_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Attempt to pickpocket an item or gold from an NPC (Module B §3 / Phase 13.0)."""
    import state_manager
    if isinstance(target_npc, dict):
        npc_name = target_npc.get("name", "NPC")
        dc = target_npc.get("passive_perception", target_npc.get("dc", 13))
    else:
        from rules import resolve_npc
        npc_info = resolve_npc(str(target_npc), world_state)
        if isinstance(npc_info, dict):
            npc_name = npc_info.get("name", str(target_npc))
            dc = npc_info.get("passive_perception", npc_info.get("dc", 13))
        else:
            npc_name = str(target_npc)
            dc = 13

    catalog = _get_item_catalog()
    if item_id == "gold":
        if isinstance(target_npc, dict):
            item_val = target_npc.get("gold", 25)
        else:
            from rules import resolve_npc
            npc_info = resolve_npc(str(target_npc), world_state)
            item_val = npc_info.get("gold", 25) if isinstance(npc_info, dict) else 25
    else:
        info = catalog.get(item_id, {})
        if not info and world_state:
            info = world_state.get("generated_items", {}).get(item_id, {})
        item_val = info.get("value_gold", 10)

    stats = player_state.get("stats", {})
    dex_val = stats.get("DEX", 10)
    dex_mod = get_modifier(dex_val)
    is_prof = is_proficient("Sleight of Hand", player_state)
    prof_bonus = player_state.get("proficiency_bonus", 2) if is_prof else 0
    active_eff = get_active_effects(player_state)
    skill_bonus = active_eff.get("skill_bonus", {}).get("Sleight of Hand", 0)

    roll = roll_override if roll_override is not None else state_manager._roll_d20()
    if roll == 20:
        success = True
    elif roll == 1:
        success = False
    else:
        total = roll + dex_mod + prof_bonus + skill_bonus
        success = (total >= dc)

    total_roll = roll + dex_mod + prof_bonus + skill_bonus

    if success:
        check_inspiration_trigger(player_state, "sleight_of_hand", {"success": True})
        if item_id == "gold":
            add_currency(player_state, gp=item_val)
            if isinstance(target_npc, dict) and "gold" in target_npc:
                target_npc["gold"] = max(0, target_npc["gold"] - item_val)
        else:
            inv = player_state.setdefault("inventory", [])
            inv.append({
                "item_id": item_id,
                "name": catalog.get(item_id, {}).get("name", item_id.replace("_", " ").title()),
                "quantity": 1,
                "stolen": True,
                "type": catalog.get(item_id, {}).get("type", "misc"),
            })
            if isinstance(target_npc, dict) and "inventory" in target_npc:
                for it in target_npc["inventory"]:
                    if isinstance(it, dict) and it.get("item_id") == item_id:
                        if it.get("quantity", 1) > 1:
                            it["quantity"] -= 1
                        else:
                            target_npc["inventory"].remove(it)
                        break

        logger.info(f"attempt_pickpocket: successfully stole '{item_id}' from {npc_name} (roll {total_roll} >= DC {dc}).")
        return {
            "success": True,
            "roll": roll,
            "total": total_roll,
            "dc": dc,
            "item_stolen": item_id,
            "is_wanted": player_state.get("is_wanted", False),
            "bounty": player_state.get("bounty", 0),
            "guard_confrontation": False,
            "message": f"Successfully pickpocketed {item_id} from {npc_name}!",
        }
    else:
        bounty_added = max(20, item_val * 2)
        player_state["is_wanted"] = True
        player_state["bounty"] = player_state.get("bounty", 0) + bounty_added
        logger.warning(f"attempt_pickpocket: caught by {npc_name} (roll {total_roll} < DC {dc})! Bounty: {player_state['bounty']}.")
        return {
            "success": False,
            "roll": roll,
            "total": total_roll,
            "dc": dc,
            "item_stolen": None,
            "is_wanted": True,
            "bounty": player_state["bounty"],
            "bounty_added": bounty_added,
            "guard_confrontation": True,
            "message": f"You were caught pickpocketing {npc_name}! Guards alerted. Bounty: {player_state['bounty']} GP.",
        }


def imprison_player(
    player_state: Dict[str, Any],
    world_state: Optional[Dict[str, Any]] = None,
    prison_days: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Imprison the player under Option A (extends Phase 8 status: 'captive' with captivity_reason: 'crime').
    Confiscates all equipment and stolen items into confiscated_items.
    """
    sentence = prison_days if prison_days is not None else max(1, player_state.get("bounty", 20) // 10)
    player_state["status"] = "captive"
    player_state["captivity_reason"] = "crime"
    player_state["prison_days_left"] = sentence

    confiscated = player_state.get("inventory", [])
    player_state["confiscated_items"] = copy.deepcopy(confiscated)
    player_state["inventory"] = []

    player_state["ac"] = _compute_ac(player_state)

    if world_state:
        world_state["current_location"] = "town_jail"

    logger.info(f"imprison_player: imprisoned for {sentence} days. {len(confiscated)} items confiscated.")
    return {
        "imprisoned": True,
        "status": "captive",
        "captivity_reason": "crime",
        "prison_days_left": sentence,
        "confiscated_count": len(confiscated),
        "location": world_state.get("current_location", "town_jail") if world_state else "town_jail",
        "message": f"You have been imprisoned for {sentence} days. All gear and possessions confiscated into the evidence chest.",
    }


def attempt_guard_persuasion(
    player_state: Dict[str, Any],
    roll_override: Optional[int] = None,
    world_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Attempt to talk down confronting town guards with a DC 15 Persuasion check."""
    import state_manager
    stats = player_state.get("stats", {})
    cha_val = stats.get("CHA", 10)
    cha_mod = get_modifier(cha_val)
    is_prof = is_proficient("Persuasion", player_state)
    prof_bonus = player_state.get("proficiency_bonus", 2) if is_prof else 0
    active_eff = get_active_effects(player_state)
    skill_bonus = active_eff.get("skill_bonus", {}).get("Persuasion", 0)

    roll = roll_override if roll_override is not None else state_manager._roll_d20()
    if roll == 20:
        success = True
    elif roll == 1:
        success = False
    else:
        total = roll + cha_mod + prof_bonus + skill_bonus
        success = (total >= 15)

    total_roll = roll + cha_mod + prof_bonus + skill_bonus

    if success:
        logger.info(f"attempt_guard_persuasion: persuaded guard (roll {total_roll} >= DC 15).")
        return {
            "success": True,
            "roll": roll,
            "total": total_roll,
            "dc": 15,
            "arrested": False,
            "message": "You skillfully talked your way out of trouble! The guards let you off with a warning.",
        }
    else:
        logger.warning(f"attempt_guard_persuasion: failed persuasion (roll {total_roll} < DC 15) — arrested.")
        imp_res = imprison_player(player_state, world_state=world_state)
        return {
            "success": False,
            "roll": roll,
            "total": total_roll,
            "dc": 15,
            "arrested": True,
            "imprisonment": imp_res,
            "message": "Your excuses fail completely! The guards place you under arrest and escort you to the cells.",
        }


def serve_prison_time(
    player_state: Dict[str, Any],
    world_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Serve out the prison sentence, paying fine and returning non-stolen items."""
    days = max(1, player_state.get("prison_days_left", 1))

    if world_state:
        import dungeon_manager
        dungeon_manager.advance_time(world_state, days=days, character_state=player_state)

    fine = player_state.get("bounty", 0)
    _ensure_currency(player_state)
    total_gp_equiv = get_total_copper(player_state) // CP_PER_GP
    paid = min(total_gp_equiv, fine)
    subtract_currency(player_state, gp=paid)
    player_state["bounty"] = 0
    player_state["is_wanted"] = False

    confiscated = player_state.get("confiscated_items", [])
    returned = [it for it in confiscated if not it.get("stolen")]
    stolen_forfeited = [it for it in confiscated if it.get("stolen")]

    player_state.setdefault("inventory", []).extend(returned)
    player_state["confiscated_items"] = []

    player_state["status"] = "normal"
    player_state["captivity_reason"] = None
    player_state["prison_days_left"] = 0
    player_state["ac"] = _compute_ac(player_state)

    if world_state:
        world_state["current_location"] = "town_riverside"

    logger.info(f"serve_prison_time: served {days} days, fine {paid} GP paid, {len(returned)} items returned.")
    return {
        "success": True,
        "days_served": days,
        "fine_paid": paid,
        "items_returned_count": len(returned),
        "stolen_forfeited_count": len(stolen_forfeited),
        "status": "normal",
        "message": f"Served {days} days in prison and paid {paid} GP in fines. Non-stolen belongings returned, stolen contraband forfeited. Released back to town.",
    }


def attempt_lockpick_escape(
    player_state: Dict[str, Any],
    world_state: Optional[Dict[str, Any]] = None,
    sleight_roll: Optional[int] = None,
    stealth_roll: Optional[int] = None,
) -> Dict[str, Any]:
    """Multi-step prison break loop (Module B §3)."""
    import state_manager
    stats = player_state.get("stats", {})
    dex_val = stats.get("DEX", 10)
    dex_mod = get_modifier(dex_val)

    soh_prof = is_proficient("Sleight of Hand", player_state)
    prof_bonus = player_state.get("proficiency_bonus", 2)
    soh_bonus = prof_bonus if soh_prof else 0

    s_roll = sleight_roll if sleight_roll is not None else state_manager._roll_d20()
    if s_roll == 20:
        s_ok = True
    elif s_roll == 1:
        s_ok = False
    else:
        s_total = s_roll + dex_mod + soh_bonus
        s_ok = (s_total >= 14)

    s_total_calc = s_roll + dex_mod + soh_bonus

    if not s_ok:
        if world_state:
            import dungeon_manager
            dungeon_manager.advance_time(world_state, steps=1, character_state=player_state)
        player_state["prison_days_left"] = player_state.get("prison_days_left", 1) + 1
        logger.warning(f"attempt_lockpick_escape: failed lockpick (roll {s_total_calc} < DC 14). Sentence +1 day.")
        return {
            "success": False,
            "step": "lockpick",
            "roll": s_roll,
            "total": s_total_calc,
            "dc": 14,
            "prison_days_left": player_state["prison_days_left"],
            "message": "Failed to pick the cell lock (DC 14). The rattling alerted the warden, adding 1 day to your sentence!",
        }

    stl_prof = is_proficient("Stealth", player_state)
    stl_bonus = prof_bonus if stl_prof else 0

    st_roll = stealth_roll if stealth_roll is not None else state_manager._roll_d20()
    if st_roll == 20:
        st_ok = True
    elif st_roll == 1:
        st_ok = False
    else:
        st_total = st_roll + dex_mod + stl_bonus
        st_ok = (st_total >= 13)

    st_total_calc = st_roll + dex_mod + stl_bonus

    if not st_ok:
        if world_state:
            import dungeon_manager
            dungeon_manager.advance_time(world_state, steps=1, character_state=player_state)
        player_state["prison_days_left"] = player_state.get("prison_days_left", 1) + 1
        logger.warning(f"attempt_lockpick_escape: failed stealth (roll {st_total_calc} < DC 13). Sentence +1 day.")
        return {
            "success": False,
            "step": "stealth",
            "roll": st_roll,
            "total": st_total_calc,
            "dc": 13,
            "prison_days_left": player_state["prison_days_left"],
            "message": "You picked the lock, but guards caught you sneaking past the watchpost (DC 13)! Thrown back into your cell (+1 day).",
        }

    confiscated = player_state.get("confiscated_items", [])
    player_state.setdefault("inventory", []).extend(confiscated)
    player_state["confiscated_items"] = []

    player_state["status"] = "normal"
    player_state["captivity_reason"] = None
    player_state["prison_days_left"] = 0
    player_state["ac"] = _compute_ac(player_state)

    if world_state:
        world_state["current_location"] = "forest_edge"
        import dungeon_manager
        dungeon_manager.advance_time(world_state, steps=1, character_state=player_state)

    check_inspiration_trigger(player_state, "stealth", {"success": True})

    logger.info(f"attempt_lockpick_escape: escape successful! {len(confiscated)} items recovered.")
    return {
        "success": True,
        "step": "escaped",
        "recovered_count": len(confiscated),
        "fled_to": world_state.get("current_location", "forest_edge") if world_state else "forest_edge",
        "message": f"Successfully picked the cell lock (DC 14) and slipped past the guards (DC 13)! Retrieved all {len(confiscated)} items from the evidence chest and escaped to safety!",
    }


def pay_bounty(player_state: Dict[str, Any]) -> Tuple[bool, str]:
    """Pay off an active bounty with gold."""
    bounty = player_state.get("bounty", 0)
    if bounty <= 0:
        return False, "You do not have any active bounty."

    _ensure_currency(player_state)
    if not can_afford(player_state, gp=bounty):
        return False, f"Insufficient gold. Bounty is {bounty} GP (you have {format_currency(player_state)})."

    subtract_currency(player_state, gp=bounty)
    player_state["bounty"] = 0
    player_state["is_wanted"] = False
    logger.info(f"pay_bounty: paid {bounty} GP. Bounty cleared.")
    return True, f"Paid {bounty} GP bounty. You are no longer wanted by the law!"
