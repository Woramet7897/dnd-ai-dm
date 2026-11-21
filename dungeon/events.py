"""
dungeon/events.py — Lightweight World Events (Phase 10 Part 2) and Interactive Environmental Objects.
"""

import copy
import logging
import random
import uuid
from typing import Any, Dict, List, Optional

from dungeon.navigation import _all_known_room_ids, _get_room

logger = logging.getLogger("dungeon_manager")

WORLD_EVENT_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "goblin_camp_grew": {
        "allowed_types": {"wilderness", "dungeon"},
        "context_line": "Recent reports say a nearby goblin camp has grown larger and more aggressive.",
    },
    "merchant_route_reopened": {
        "allowed_types": {"town", "wilderness"},
        "context_line": "Travelers report that the regional merchant trade route has recently reopened.",
    },
    "ancient_shrine_glowing": {
        "allowed_types": {"wilderness", "dungeon"},
        "context_line": "Whispers say strange mystical lights have been seen emanating from nearby ancient ruins.",
    },
    "bandit_activity_increased": {
        "allowed_types": {"wilderness", "town"},
        "context_line": "Local guard patrols warn that bandit activity has increased along the surrounding roads.",
    },
    "unusual_fog_settled": {
        "allowed_types": {"wilderness", "dungeon", "town"},
        "context_line": "An unnatural, heavy mist has settled over the area, dampening sound and visibility.",
    },
}


def check_and_trigger_world_event(
    world_state: Dict[str, Any],
    chance: float = 0.10,
    rng_val: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Spec Section 6d / Phase 10 Part 2:
    On move_player() success, roll ~10% chance to set one world event flag
    on a location the player is NOT currently in.
    """
    roll = random.random() if rng_val is None else rng_val
    if roll >= chance:
        return None

    current_loc = world_state.get("current_location")
    all_rooms = list(_all_known_room_ids(world_state))
    eligible_locs = [rid for rid in all_rooms if rid != current_loc]

    if not eligible_locs:
        return None

    target_loc_id = random.choice(eligible_locs)
    target_room = _get_room(target_loc_id, world_state)
    room_type = target_room.get("type", "wilderness") if target_room else "wilderness"

    matching_flags = [
        flag_name for flag_name, info in WORLD_EVENT_DEFINITIONS.items()
        if room_type in info.get("allowed_types", {"wilderness"})
    ]

    if not matching_flags:
        matching_flags = list(WORLD_EVENT_DEFINITIONS.keys())

    chosen_flag = random.choice(matching_flags)

    event_flags = world_state.setdefault("world_event_flags", {})
    if not isinstance(event_flags, dict):
        event_flags = {}
        world_state["world_event_flags"] = event_flags

    loc_flags = event_flags.setdefault(target_loc_id, [])
    if chosen_flag not in loc_flags:
        loc_flags.append(chosen_flag)

    logger.debug(f"check_and_trigger_world_event: set flag '{chosen_flag}' on location '{target_loc_id}'.")
    return {"location_id": target_loc_id, "flag": chosen_flag}


def get_active_world_events_for_location(
    location_id: str,
    world_state: Dict[str, Any],
    consume: bool = True,
) -> List[str]:
    """Retrieve context lines for active world event flags at location_id."""
    event_flags = world_state.get("world_event_flags")
    if not isinstance(event_flags, dict) or location_id not in event_flags:
        return []

    loc_flags = event_flags.get(location_id, [])
    if not loc_flags:
        return []

    context_lines = []
    for flag in loc_flags:
        info = WORLD_EVENT_DEFINITIONS.get(flag)
        if not info:
            info = world_state.get("dynamic_world_events", {}).get(flag, {})
        line = info.get("context_line")
        if line:
            context_lines.append(f"[World Event] {line}")

    if consume:
        event_flags.pop(location_id, None)

    return context_lines


def register_dynamic_event(
    event_data: Dict[str, Any],
    world_state: Dict[str, Any],
) -> str:
    """Register a dynamically generated world event or rumor into world_state."""
    flag = f"dyn_event_{uuid.uuid4().hex[:8]}"
    dyn_events = world_state.setdefault("dynamic_world_events", {})
    dyn_events[flag] = dict(event_data)

    target_loc = event_data.get("location_id", world_state.get("current_location", "town_riverside"))
    event_flags = world_state.setdefault("world_event_flags", {})
    loc_flags = event_flags.setdefault(target_loc, [])
    if flag not in loc_flags:
        loc_flags.append(flag)

    if event_data.get("post_to_notice_board") or event_data.get("rumor_text"):
        nb = world_state.setdefault("notice_board", {})
        entries = nb.setdefault("entries", [])
        room_obj = _get_room(target_loc, world_state) or {}
        r_name = room_obj.get("name", target_loc.replace("_", " ").title())
        reward = int(event_data.get("reward_gold", 0))

        nb_entry = {
            "id": f"notice_{flag}",
            "type": "rumor" if reward <= 0 else "event_quest",
            "title": event_data.get("title", "Local Rumor"),
            "description": event_data.get("rumor_text") or event_data.get("context_line", ""),
            "target_room_id": target_loc,
            "target_room_name": r_name,
            "reward_gold": reward,
            "status": "available",
            "is_dynamic": True,
        }
        entries.append(nb_entry)

    logger.info(
        f"register_dynamic_event: registered dynamic event '{event_data.get('title')}' "
        f"as flag '{flag}' at '{target_loc}'."
    )
    return flag


def interact_with_object(
    world_state: Dict[str, Any],
    room_id: str,
    object_id: str,
    combat_state: Optional[Dict[str, Any]] = None,
    character_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Interact with an environmental object in a room (exploration or combat)."""
    room = _get_room(room_id, world_state)
    if not room:
        return {"success": False, "message": f"Room '{room_id}' not found."}

    if room_id not in world_state.get("dynamic_rooms", {}):
        world_state.setdefault("dynamic_rooms", {})[room_id] = copy.deepcopy(room)
    room = world_state["dynamic_rooms"][room_id]

    objs = room.get("interactive_objects", [])
    target_obj = next((o for o in objs if o.get("id") == object_id), None)
    if not target_obj:
        return {"success": False, "message": f"Object '{object_id}' not found in room."}

    if target_obj.get("used"):
        return {"success": False, "message": f"'{target_obj.get('name', object_id)}' has already been used."}

    target_obj["used"] = True
    obj_name = target_obj.get("name", object_id)
    msg_parts = [f"You interact with {obj_name}!"]

    if combat_state:
        import combat_manager
        surf = target_obj.get("surface")
        if surf:
            combat_manager.apply_surface(combat_state, surf)
            msg_parts.append(f"A surface of {surf.upper()} spreads across the area!")

        dmg_expr = target_obj.get("damage")
        if dmg_expr and combat_state.get("enemies"):
            living_enemies = [e for e in combat_state["enemies"] if e.get("hp", {}).get("current", 0) > 0]
            if living_enemies:
                from combat_manager import roll_dice
                dmg_val = roll_dice(dmg_expr)
                hit_names = []
                for enemy in living_enemies:
                    e_hp = enemy.setdefault("hp", {"current": 10, "max": 10})
                    e_hp["current"] = max(0, e_hp["current"] - dmg_val)
                    hit_names.append(enemy.get("name", enemy.get("id", "Enemy")))
                dmg_type_str = f" {target_obj.get('damage_type')}" if target_obj.get('damage_type') else ""
                msg_parts.append(f"It crashes down, dealing {dmg_val}{dmg_type_str} damage to {', '.join(hit_names)}!")
    else:
        surf = target_obj.get("surface")
        if surf:
            msg_parts.append(f"It creates a field of {surf} on the ground.")
        dmg = target_obj.get("damage")
        if dmg:
            msg_parts.append(f"It triggers a heavy impact with {dmg} destructive force!")

    full_msg = " ".join(msg_parts)
    return {
        "success": True,
        "object": target_obj,
        "message": full_msg,
    }
