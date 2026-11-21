"""
dungeon/navigation.py — Room lookup, movement, time progression, loot collection, and safe room search.
"""

from collections import deque
import logging
from typing import Any, Dict, List, Optional, Tuple, Union

import dungeon_manager

logger = logging.getLogger("dungeon_manager")

_REQUIRED_ROOM_FIELDS = {"id", "name", "type", "description", "exits", "is_safe"}
_VALID_ROOM_TYPES = {"town", "wilderness", "dungeon"}
_VALID_DIRECTIONS = {"north", "south", "east", "west"}
_OPPOSITE_DIRECTION = {"north": "south", "south": "north", "east": "west", "west": "east"}

DAY_PERIODS = ["morning", "afternoon", "evening", "night"]
STEPS_PER_PERIOD = 4


def _all_known_room_ids(world_state: Dict[str, Any]) -> set:
    """Return the set of ALL known room IDs: static catalog + any LLM-registered rooms."""
    ids = set(dungeon_manager._load_static_catalog().keys())
    ids.update(world_state.get("dynamic_rooms", {}).keys())
    return ids


def _get_room(room_id: str, world_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Look up a room by ID. Checks dynamic_rooms first (LLM-registered), then static catalog."""
    dynamic = world_state.get("dynamic_rooms", {})
    if room_id in dynamic:
        return dynamic[room_id]
    return dungeon_manager._load_static_catalog().get(room_id)


def get_current_room(world_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the full room definition for the player's current location."""
    room_id = world_state.get("current_location")
    if not room_id:
        logger.debug("get_current_room: current_location not set in world_state.")
        return None
    room = _get_room(room_id, world_state)
    if room is None:
        logger.debug(f"get_current_room: unknown room_id '{room_id}'.")
    return room


def get_available_exits(world_state: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Return the exits dict for the current room: {direction: room_id | None}."""
    room = get_current_room(world_state)
    if room is None:
        return {}
    return room.get("exits", {})


def advance_time(
    world_state: Dict[str, Any],
    steps: Union[int, Dict[str, Any]] = 1,
    character_state: Optional[Dict[str, Any]] = None,
    days: int = 0,
) -> Dict[str, Any]:
    """Deterministically advance game_time in world_state by steps or days (spec Section 22b / Phase 12.3)."""
    if isinstance(steps, dict) and character_state is None:
        character_state = steps
        steps = 1
    elif not isinstance(steps, int):
        steps = 1

    if character_state is None and isinstance(world_state, dict):
        character_state = world_state.get("player_state") or world_state.get("character_state")
        if character_state is None and "inventory" in world_state and "game_time" not in world_state:
            character_state = world_state

    if isinstance(world_state, dict) and "inventory" in world_state and "game_time" not in world_state:
        world_state = world_state.setdefault("_world_state", {
            "game_time": {"day": 1, "period": "morning", "steps_since_period_start": 0}
        })

    gt = world_state.setdefault("game_time", {
        "day": 1,
        "period": "morning",
        "steps_since_period_start": 0
    })

    steps_per_day = STEPS_PER_PERIOD * len(DAY_PERIODS)
    if days > 0:
        steps_from_days = days * steps_per_day
        steps_to_run = steps_from_days if steps == 1 else (steps_from_days + steps)
    else:
        steps_to_run = steps

    period_changed = False
    days_passed = 0
    for _ in range(steps_to_run):
        gt["steps_since_period_start"] += 1
        if gt["steps_since_period_start"] >= STEPS_PER_PERIOD:
            gt["steps_since_period_start"] = 0
            period_changed = True
            current_idx = DAY_PERIODS.index(gt["period"]) if gt["period"] in DAY_PERIODS else 0
            next_idx = (current_idx + 1) % len(DAY_PERIODS)
            if next_idx == 0:
                gt["day"] += 1
                days_passed += 1
            gt["period"] = DAY_PERIODS[next_idx]

    spoiled_items = []
    if days_passed > 0 and character_state is not None:
        import state_manager
        spoiled_items = state_manager.tick_food_spoilage(character_state, days=days_passed)

    nb_refreshed = False
    if isinstance(world_state, dict):
        current_day = gt.get("day", 1)
        nb = world_state.setdefault("notice_board", {})
        last_day = nb.get("last_refreshed_day", 0)
        if last_day == 0 or (current_day - last_day) >= 3:
            dungeon_manager.refresh_notice_board(world_state, force=True)
            nb_refreshed = True

    res = dict(gt)
    res["period_changed"] = period_changed
    res["days_passed"] = days_passed
    res["spoiled_items"] = spoiled_items
    res["notice_board_refreshed"] = nb_refreshed
    return res


def move_player(
    direction: str,
    world_state: Dict[str, Any],
    character_state: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Attempt to move the player in the given direction."""
    direction = direction.lower().strip()
    if direction not in _VALID_DIRECTIONS:
        return False, f"'{direction}' is not a valid direction. Use north, south, east, or west.", None

    if character_state is None:
        character_state = world_state.get("player_state") if isinstance(world_state, dict) else None
        if character_state is None:
            logger.warning("move_player called without character_state; captive/imprisonment check may be incomplete.")

    is_locked_up = bool(world_state.get("is_imprisoned"))
    if character_state:
        is_locked_up = is_locked_up or (
            character_state.get("status") == "captive"
            or bool(character_state.get("crime_state", {}).get("imprisoned"))
        )
    if is_locked_up:
        return False, "Cannot move — you are imprisoned or held captive! You must escape first.", None

    current_room = get_current_room(world_state)
    if current_room is None:
        return False, "Cannot move — current location is unknown.", None

    exits = current_room.get("exits", {})
    destination_id = exits.get(direction)

    if destination_id is None:
        return (
            False,
            f"There is no exit to the {direction} from {current_room.get('name', 'here')}.",
            None,
        )

    destination = _get_room(destination_id, world_state)
    if destination is None:
        logger.debug(
            f"move_player: exit '{direction}' points to '{destination_id}' "
            f"which doesn't exist in catalog or dynamic_rooms."
        )
        return (
            False,
            f"The path to the {direction} seems to lead nowhere (destination '{destination_id}' not found).",
            None,
        )

    world_state["current_location"] = destination_id
    visited = world_state.setdefault("visited_rooms", [])
    if destination_id not in visited:
        visited.append(destination_id)

    time_info = advance_time(world_state, steps=1, character_state=character_state)
    if time_info.get("period_changed") and character_state is not None:
        import state_manager
        state_manager.handle_period_change(world_state, character_state)

    dungeon_manager.check_and_trigger_world_event(world_state)

    logger.debug(f"move_player: moved to '{destination_id}' ({destination.get('name')})")
    return True, f"You move {direction} into {destination.get('name', destination_id)}.", destination


def normalize_new_location(location_data: Any, world_state: Dict[str, Any]) -> Any:
    """
    Auto-heal / normalize a partial new_location dict from smaller or creative LLMs
    so it satisfies required fields if basic identity (id/name) is provided,
    while preserving strict validation for tests passing explicitly invalid types/IDs.
    """
    if not isinstance(location_data, dict):
        return location_data

    loc = dict(location_data)

    # If model gave 'name' or 'id' only, or omitted 'description' / 'is_safe' / 'exits' when connects_to or direction is hinted
    if "name" not in loc and isinstance(loc.get("id"), str) and loc["id"].strip():
        loc["name"] = loc["id"].replace("_", " ").title()
    elif "id" not in loc and isinstance(loc.get("name"), str) and loc["name"].strip():
        import re
        slug = re.sub(r"[^a-z0-9]+", "_", loc["name"].strip().lower()).strip("_")
        if slug:
            loc["id"] = f"dyn_loc_{slug[:24]}"

    if "type" not in loc and "id" in loc and "name" in loc:
        name_l = str(loc.get("name", "")).lower()
        if any(w in name_l for w in ("town", "village", "city", "camp", "haven", "เมือง", "หมู่บ้าน")):
            loc["type"] = "town"
        elif any(w in name_l for w in ("cave", "crypt", "dungeon", "ruin", "cellar", "temple", "ถ้ำ", "ดันเจี้ยน", "วิหาร", "สุสาน")):
            loc["type"] = "dungeon"
        else:
            loc["type"] = "wilderness"

    if "description" not in loc and "id" in loc and "name" in loc:
        loc["description"] = f"An unexplored area known as {loc['name']}."

    if "is_safe" not in loc and "type" in loc:
        loc["is_safe"] = (loc.get("type") == "town")

    # Normalize exits: if model provided connects_to / direction instead of full exits dict, or omitted null directions
    cur_loc = world_state.get("current_location", "town_riverside") if isinstance(world_state, dict) else "town_riverside"
    if "exits" not in loc and ("connects_to" in loc or "direction" in loc):
        conn = loc.get("connects_to", cur_loc)
        dir_from_new = str(loc.get("direction", "south")).lower().strip()
        if dir_from_new not in _VALID_DIRECTIONS:
            dir_from_new = "south"
        loc["exits"] = {"north": None, "south": None, "east": None, "west": None}
        loc["exits"][dir_from_new] = conn
    elif isinstance(loc.get("exits"), dict):
        ex = dict(loc["exits"])
        for d in _VALID_DIRECTIONS:
            ex.setdefault(d, None)
        # If all exits are None, connect back to current_location via an available opposite direction
        if all(ex.get(d) is None for d in _VALID_DIRECTIONS) and cur_loc:
            parent_room = _get_room(cur_loc, world_state) if isinstance(world_state, dict) else None
            parent_exits = parent_room.get("exits", {}) if isinstance(parent_room, dict) else {}
            chosen_back_dir = "south"
            for p_dir in ("north", "east", "west", "south"):
                if parent_exits.get(p_dir) is None:
                    chosen_back_dir = _OPPOSITE_DIRECTION[p_dir]
                    break
            ex[chosen_back_dir] = cur_loc
        loc["exits"] = ex

    return loc


def validate_new_location(location_data: Any, world_state: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate a room definition proposed by the LLM before it can be registered."""
    if not isinstance(location_data, dict):
        return False, f"new_location must be a dict, got {type(location_data).__name__}."

    missing = _REQUIRED_ROOM_FIELDS - set(location_data.keys())
    if missing:
        return False, f"new_location is missing required fields: {sorted(missing)}."

    room_id = location_data["id"]
    if not isinstance(room_id, str) or not room_id.strip():
        return False, "new_location.id must be a non-empty string."

    known_ids = _all_known_room_ids(world_state)
    if room_id in known_ids:
        return (
            False,
            f"new_location.id '{room_id}' already exists — duplicate room IDs are not allowed. "
            f"The LLM must generate a unique ID for new locations.",
        )

    room_type = location_data.get("type")
    if room_type not in _VALID_ROOM_TYPES:
        return (
            False,
            f"new_location.type '{room_type}' is invalid. "
            f"Must be one of: {sorted(_VALID_ROOM_TYPES)}.",
        )

    exits = location_data.get("exits")
    if not isinstance(exits, dict):
        return False, "new_location.exits must be a dict."

    for direction, dest in exits.items():
        if direction not in _VALID_DIRECTIONS:
            return (
                False,
                f"new_location.exits has invalid direction '{direction}'. "
                f"Must be one of: {sorted(_VALID_DIRECTIONS)}.",
            )
        if dest is not None:
            if not isinstance(dest, str):
                return (
                    False,
                    f"new_location.exits.{direction} must be a room_id string or null, "
                    f"got {type(dest).__name__}.",
                )
            if dest not in known_ids:
                return (
                    False,
                    f"new_location.exits.{direction} points to '{dest}' "
                    f"which does not exist in the static catalog or dynamic_rooms. "
                    f"connects_to must reference an existing room.",
                )

    return True, ""


def register_new_location(
    location_data: Dict[str, Any],
    world_state: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    Register a new LLM-generated room into world_state['dynamic_rooms'].
    Also establishes bidirectional exits on connected rooms so the player can actually walk into the new room!
    """
    ok, reason = validate_new_location(location_data, world_state)
    if not ok:
        logger.debug(f"register_new_location rejected '{location_data.get('id') if isinstance(location_data, dict) else location_data}': {reason}")
        return False, reason

    import copy
    dynamic = world_state.setdefault("dynamic_rooms", {})
    room_id = location_data["id"]

    # Ensure optional room lists exist cleanly
    clean_room = copy.deepcopy(location_data)
    clean_room.setdefault("shops", ["general_store"] if clean_room.get("type") == "town" else [])
    clean_room.setdefault("npcs", [])
    clean_room.setdefault("loot", [])
    clean_room.setdefault("encounter_table", ["goblin_scout"] if clean_room.get("type") == "dungeon" else [])
    clean_room.setdefault("interactive_objects", [])
    clean_room.setdefault("hazards", [])

    dynamic[room_id] = clean_room

    # Establish bidirectional exits: if new_room has exit `dir -> target_room`, link `target_room` back to `room_id`
    exits = clean_room.get("exits", {})
    for direction, target_id in exits.items():
        if not target_id or direction not in _OPPOSITE_DIRECTION:
            continue
        opp_dir = _OPPOSITE_DIRECTION[direction]
        target_room = _get_room(target_id, world_state)
        if not target_room:
            continue
        # Copy static room into dynamic_rooms before mutating its exits so static catalog remains pure across saves/tests
        if target_id not in dynamic:
            target_room = copy.deepcopy(target_room)
            dynamic[target_id] = target_room
        t_exits = target_room.setdefault("exits", {"north": None, "south": None, "east": None, "west": None})
        if t_exits.get(opp_dir) is None:
            t_exits[opp_dir] = room_id
            logger.info(f"register_new_location: linked '{target_id}' exit '{opp_dir}' -> '{room_id}'.")
        else:
            # If opposite direction is already occupied on target_room, try any free direction on target_room
            if room_id not in t_exits.values():
                for free_d in ("north", "east", "west", "south"):
                    if t_exits.get(free_d) is None:
                        t_exits[free_d] = room_id
                        logger.info(f"register_new_location: linked '{target_id}' free exit '{free_d}' -> '{room_id}'.")
                        break

    logger.debug(f"register_new_location: registered new room '{room_id}' ({clean_room.get('name')})")
    return True, ""


def mark_room_cleared(room_id: str, world_state: Dict[str, Any]) -> bool:
    """Mark a room as cleared in world_state['cleared_rooms']."""
    if _get_room(room_id, world_state) is None:
        logger.debug(f"mark_room_cleared: unknown room_id '{room_id}'.")
        return False

    cleared = world_state.setdefault("cleared_rooms", [])
    if room_id not in cleared:
        cleared.append(room_id)
        logger.debug(f"mark_room_cleared: '{room_id}' marked cleared.")
    return True


def get_room_loot(room_id: str, world_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the loot list for a room, respecting already-collected state."""
    room = _get_room(room_id, world_state)
    if room is None:
        logger.debug(f"get_room_loot: unknown room_id '{room_id}'.")
        return []

    collected = world_state.setdefault("collected_loot", [])
    cache_key = f"{room_id}_hidden_cache"
    has_cache = bool(room.get("hidden_cache")) and (cache_key not in collected)

    if room_id in collected:
        if has_cache:
            collected.append(cache_key)
            cache_loot = room.get("hidden_cache_loot") or [
                {"item_id": "gold", "amount_min": 25, "amount_max": 50, "quantity": 35},
                {"item_id": "healing_potion", "quantity": 1}
            ]
            logger.info(f"get_room_loot: '{room_id}' hidden cache collected: {cache_loot}")
            return list(cache_loot)
        return []

    loot = list(room.get("loot", []))
    if has_cache:
        collected.append(cache_key)
        cache_loot = room.get("hidden_cache_loot") or [
            {"item_id": "gold", "amount_min": 25, "amount_max": 50, "quantity": 35},
            {"item_id": "healing_potion", "quantity": 1}
        ]
        loot.extend(cache_loot)

    if loot:
        collected.append(room_id)
        logger.debug(f"get_room_loot: '{room_id}' loot collected: {loot}")
    return loot


def is_room_safe(room_id: str, world_state: Dict[str, Any]) -> bool:
    """Return True if the room is a safe zone (no random encounters, rest allowed)."""
    room = _get_room(room_id, world_state)
    if room is None:
        return False
    if room.get("is_safe", False):
        return True
    if room_id in world_state.get("cleared_rooms", []):
        return True
    return False


def find_nearest_visited_safe_room(world_state: Dict[str, Any]) -> str:
    """Find the nearest previously-visited safe location to world_state['current_location'] via BFS."""
    visited = world_state.get("visited_rooms", [])
    current = world_state.get("current_location", "town_riverside")

    safe_visited = [
        rid for rid in visited
        if is_room_safe(rid, world_state) or (_get_room(rid, world_state) and _get_room(rid, world_state).get("type") == "town")
    ]

    if not safe_visited:
        return "town_riverside"

    if current in safe_visited:
        return current

    queue = deque([(current, 0)])
    seen = {current}

    while queue:
        curr_id, dist = queue.popleft()
        if curr_id in safe_visited:
            return curr_id

        room = _get_room(curr_id, world_state)
        if room:
            exits = room.get("exits", {})
            for direction, dest_id in exits.items():
                if dest_id and dest_id not in seen:
                    seen.add(dest_id)
                    queue.append((dest_id, dist + 1))

    return safe_visited[0] if safe_visited else "town_riverside"


def is_camp_context(world_state: Optional[Dict[str, Any]] = None) -> bool:
    """Check if the current game state qualifies as a camp / rest context (Spec Module D §2)."""
    if not world_state or not isinstance(world_state, dict):
        return False

    combat_state = world_state.get("combat_state")
    if isinstance(combat_state, dict) and combat_state.get("status") == "active":
        return False

    if world_state.get("is_imprisoned") or world_state.get("status") == "captive":
        return False

    if world_state.get("at_camp") is True or world_state.get("is_resting") is True:
        loc = world_state.get("current_location")
        if loc:
            room = _get_room(loc, world_state)
            if room and room.get("type") == "dungeon" and not is_room_safe(loc, world_state):
                return False
        return True

    loc = world_state.get("current_location")
    if not loc:
        return False

    room = _get_room(loc, world_state)
    if room:
        rtype = room.get("type", "")
        if rtype == "dungeon":
            return False
        if rtype == "town":
            return True
        if room.get("is_safe", False) or loc in world_state.get("cleared_rooms", []):
            if "camp" in loc.lower() or "safe" in loc.lower() or room.get("is_safe"):
                return True
    else:
        if "town" in loc.lower() or "camp" in loc.lower():
            return True
        if loc in world_state.get("cleared_rooms", []):
            return True

    return False
