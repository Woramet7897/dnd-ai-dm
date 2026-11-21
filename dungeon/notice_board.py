"""
dungeon/notice_board.py — Tavern Notice Board Quest Generator & Completion (Phase 13.5 / Module C §2).
"""

import copy
import logging
from typing import Any, Dict, List, Optional

import dungeon_manager
from dungeon.navigation import _all_known_room_ids, _get_room

logger = logging.getLogger("dungeon_manager")


def _find_tavern_room_id(world_state: Dict[str, Any]) -> str:
    """Find the tavern or central town room in world_state."""
    all_rooms = _all_known_room_ids(world_state)
    if "tavern" in all_rooms:
        return "tavern"
    for r_id in all_rooms:
        r_obj = _get_room(r_id, world_state) or {}
        if "tavern" in r_id.lower() or "tavern" in r_obj.get("name", "").lower():
            return r_id
    if "town_riverside" in all_rooms:
        return "town_riverside"
    return next(iter(all_rooms)) if all_rooms else "town_riverside"


def refresh_notice_board(
    world_state: Dict[str, Any],
    force: bool = False,
) -> List[Dict[str, Any]]:
    """
    Every in-game 3 days, generates quests in the tavern room (Module C §2):
    1. Bounty (kill a monster in an uncleared room).
    2. Item Delivery (deliver herbs/rations to an NPC, 1.5x payout).
    3. Rumor (reveals a `hidden_cache: true` flag on a target room).
    """
    gt = world_state.setdefault("game_time", {"day": 1, "period": "morning", "steps_since_period_start": 0})
    current_day = gt.get("day", 1)

    nb = world_state.setdefault("notice_board", {})
    last_day = nb.get("last_refreshed_day", 0)

    if not force and last_day > 0 and (current_day - last_day) < 3 and "entries" in nb:
        return nb.get("entries", [])

    all_rooms = _all_known_room_ids(world_state)
    refresh_count = nb.get("refresh_count", 0) + 1

    entries = []

    # ── 1. Bounty Quest: Kill monster in uncleared room ────────────────────────
    cleared = set(world_state.get("cleared_rooms", []))
    bounty_candidates = [
        r_id for r_id in sorted(all_rooms)
        if r_id not in cleared and (_get_room(r_id, world_state) or {}).get("type") in ("wilderness", "dungeon")
    ]
    if not bounty_candidates:
        bounty_candidates = [r_id for r_id in sorted(all_rooms) if (_get_room(r_id, world_state) or {}).get("type") != "town"] or sorted(all_rooms)

    bounty_room_id = bounty_candidates[(refresh_count - 1) % len(bounty_candidates)]
    b_room_obj = _get_room(bounty_room_id, world_state) or {}
    b_room_name = b_room_obj.get("name", bounty_room_id.replace("_", " ").title())
    enc_table = b_room_obj.get("encounter_table", ["goblin_scout"])
    target_monster = enc_table[(refresh_count - 1) % len(enc_table)] if enc_table else "goblin_scout"

    bounty_entry = {
        "id": f"bounty_{bounty_room_id}_{target_monster}_{refresh_count}",
        "type": "bounty",
        "title": f"Bounty: Slay {target_monster.replace('_', ' ').title()}",
        "description": f"A bounty has been posted for eliminating a dangerous {target_monster.replace('_', ' ')} spotted in {b_room_name}.",
        "target_room_id": bounty_room_id,
        "target_room_name": b_room_name,
        "target_monster": target_monster,
        "reward_gold": 50,
        "status": "available",
    }
    entries.append(bounty_entry)

    # ── 2. Item Delivery Quest: Deliver herbs/rations to an NPC, 1.5x payout ───
    npc_targets = []
    for r_id in sorted(all_rooms):
        r_obj = _get_room(r_id, world_state) or {}
        for npc in r_obj.get("npcs", []):
            npc_targets.append((r_id, npc, r_obj.get("name", r_id.replace("_", " ").title())))

    if not npc_targets:
        npc_targets = [("town_riverside", "captain_valdis", "Riverside Village")]

    del_room_id, target_npc, del_room_name = npc_targets[(refresh_count - 1) % len(npc_targets)]
    delivery_item = "herbs" if refresh_count % 2 != 0 else "rations"
    base_val = 10 if delivery_item == "herbs" else 4
    reward_gold = int(base_val * 1.5)
    payout = max(15, reward_gold * 3 if reward_gold < 15 else reward_gold)

    delivery_entry = {
        "id": f"delivery_{target_npc}_{delivery_item}_{refresh_count}",
        "type": "delivery",
        "title": f"Delivery: Supplies for {target_npc.replace('_', ' ').title()}",
        "description": f"Deliver {delivery_item} to {target_npc.replace('_', ' ').title()} in {del_room_name}. Standard 1.5x premium paid on delivery.",
        "target_npc": target_npc,
        "target_item": delivery_item,
        "target_room_id": del_room_id,
        "target_room_name": del_room_name,
        "base_value": base_val,
        "reward_gold": payout,
        "status": "available",
    }
    entries.append(delivery_entry)

    # ── 3. Rumor: Reveals a `hidden_cache: true` flag on a target room ─────────
    rumor_candidates = [
        r_id for r_id in sorted(all_rooms)
        if (_get_room(r_id, world_state) or {}).get("type") in ("wilderness", "dungeon")
    ]
    if not rumor_candidates:
        rumor_candidates = sorted(all_rooms)

    rumor_room_id = rumor_candidates[(refresh_count + 1) % len(rumor_candidates)]
    r_room_obj = _get_room(rumor_room_id, world_state) or {}
    r_room_name = r_room_obj.get("name", rumor_room_id.replace("_", " ").title())

    if rumor_room_id not in world_state.setdefault("dynamic_rooms", {}):
        static_copy = dungeon_manager._load_static_catalog().get(rumor_room_id)
        if static_copy:
            world_state["dynamic_rooms"][rumor_room_id] = copy.deepcopy(static_copy)
        else:
            world_state["dynamic_rooms"][rumor_room_id] = {"id": rumor_room_id, "name": r_room_name}
    world_state["dynamic_rooms"][rumor_room_id]["hidden_cache"] = True

    rumor_entry = {
        "id": f"rumor_cache_{rumor_room_id}_{refresh_count}",
        "type": "rumor",
        "title": f"Rumor: Secret Cache in {r_room_name}",
        "description": f"Tavern patrons whisper of a hidden cache of treasure and supplies concealed within {r_room_name}.",
        "target_room_id": rumor_room_id,
        "target_room_name": r_room_name,
        "revealed": True,
        "status": "available",
    }
    entries.append(rumor_entry)

    nb["last_refreshed_day"] = current_day
    nb["refresh_count"] = refresh_count
    nb["entries"] = entries

    tavern_id = _find_tavern_room_id(world_state)
    if tavern_id not in world_state.setdefault("dynamic_rooms", {}):
        t_copy = dungeon_manager._load_static_catalog().get(tavern_id)
        if t_copy:
            world_state["dynamic_rooms"][tavern_id] = copy.deepcopy(t_copy)
        else:
            world_state["dynamic_rooms"][tavern_id] = {"id": tavern_id}
    world_state["dynamic_rooms"][tavern_id]["notice_board"] = entries

    logger.info(f"refresh_notice_board: refreshed notice board for Day {current_day} ({len(entries)} quests posted).")
    return entries


def get_notice_board(world_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return active notice board entries, generating them if not yet initialized."""
    nb = world_state.get("notice_board", {})
    if not nb or "entries" not in nb:
        return refresh_notice_board(world_state)
    return nb.get("entries", [])


def accept_notice_board_quest(
    world_state: Dict[str, Any],
    quest_id: str,
) -> Dict[str, Any]:
    """Accept an available notice board quest and register it into world_state['quest_log']['side']."""
    nb = world_state.get("notice_board", {})
    entries = nb.get("entries", [])
    target = next((q for q in entries if q.get("id") == quest_id), None)
    if not target:
        return {"success": False, "message": f"Quest '{quest_id}' not found on notice board."}

    target["status"] = "accepted"

    ql = world_state.setdefault("quest_log", {"main": [], "side": []})
    existing = next((q for q in ql.get("side", []) if q.get("id") == quest_id), None)
    if not existing:
        ql.setdefault("side", []).append({
            "id": target["id"],
            "title": target["title"],
            "status": "active",
            "description": target["description"],
            "type": target.get("type", "side"),
            "target_room_id": target.get("target_room_id"),
            "target_monster": target.get("target_monster"),
            "target_npc": target.get("target_npc"),
            "target_item": target.get("target_item"),
            "reward_gold": target.get("reward_gold", 0),
            "objectives": [{"description": target["title"], "done": False}],
        })

    logger.info(f"accept_notice_board_quest: accepted quest '{quest_id}'.")
    return {"success": True, "quest": target, "message": f"Accepted quest: {target['title']}"}


def complete_notice_board_quest(
    world_state: Dict[str, Any],
    quest_id: str,
    character_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Complete an accepted notice board quest, granting reward and updating quest log."""
    nb = world_state.get("notice_board", {})
    entries = nb.get("entries", [])
    target = next((q for q in entries if q.get("id") == quest_id), None)
    if not target:
        return {"success": False, "message": f"Quest '{quest_id}' not found."}

    if target.get("type") == "delivery":
        item_id = target.get("target_item")
        if character_state is not None and item_id:
            inv = character_state.get("inventory", [])
            has_item = False
            for it in inv:
                if isinstance(it, dict) and it.get("item_id") == item_id:
                    if it.get("quantity", 1) > 1:
                        it["quantity"] -= 1
                    else:
                        inv.remove(it)
                    has_item = True
                    break
            if not has_item:
                return {
                    "success": False,
                    "message": f"Cannot complete delivery: you do not have '{item_id}' in inventory.",
                }

    target["status"] = "completed"
    reward = target.get("reward_gold", 0)
    if character_state is not None and reward > 0:
        import state_manager
        state_manager.add_currency(character_state, gp=reward)

    ql = world_state.get("quest_log", {})
    for q in ql.get("side", []):
        if q.get("id") == quest_id:
            q["status"] = "completed"
            for obj in q.get("objectives", []):
                obj["done"] = True

    logger.info(f"complete_notice_board_quest: completed '{quest_id}', rewarded {reward} GP.")
    return {
        "success": True,
        "reward_gold": reward,
        "message": f"Completed quest: {target['title']}! Received {reward} GP reward.",
    }
