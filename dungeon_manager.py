"""
dungeon_manager.py — Phase 3
Handles room navigation, location registration, loot, and room state.
No LLM calls. No Streamlit. Pure Python + dungeon_data.json + world saves.

Scope: static catalog navigation + dynamic world-save updates.
World events (Phase 10) and companion-based room effects are excluded here.
"""

import copy
import json
import logging
import os
import random
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("dungeon_manager")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[DUNGEON] %(levelname)s: %(message)s"))
    logger.addHandler(_h)

# ─── Catalog path ─────────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))
_DUNGEON_DATA_PATH = os.path.join(_DIR, "dungeon_data.json")

# ─── Required fields for a valid room definition ─────────────────────────────
_REQUIRED_ROOM_FIELDS = {"id", "name", "type", "description", "exits", "is_safe"}
_VALID_ROOM_TYPES = {"town", "wilderness", "dungeon"}
_VALID_DIRECTIONS = {"north", "south", "east", "west"}

# ─── Time & Supply Constants (spec Section 22a) ──────────────────────────────
DAY_PERIODS = ["morning", "afternoon", "evening", "night"]
STEPS_PER_PERIOD = 4


# ────────────────────────────────────────────────────────────────────────────────
# Internal catalog helpers
# ────────────────────────────────────────────────────────────────────────────────

_static_catalog: Optional[Dict[str, Any]] = None

def _load_static_catalog() -> Dict[str, Any]:
    """Load dungeon_data.json once and cache it in module scope."""
    global _static_catalog
    if _static_catalog is None:
        with open(_DUNGEON_DATA_PATH, "r", encoding="utf-8") as f:
            _static_catalog = json.load(f)
    return _static_catalog


def _all_known_room_ids(world_state: Dict[str, Any]) -> set:
    """
    Return the set of ALL known room IDs: static catalog + any LLM-registered rooms.
    Used for cross-reference validation.
    """
    ids = set(_load_static_catalog().keys())
    ids.update(world_state.get("dynamic_rooms", {}).keys())
    return ids


def _get_room(room_id: str, world_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Look up a room by ID. Checks dynamic_rooms first (LLM-registered), then static catalog.
    Returns None if not found.
    """
    # Dynamic rooms override static catalog (LLM-expanded world takes priority)
    dynamic = world_state.get("dynamic_rooms", {})
    if room_id in dynamic:
        return dynamic[room_id]
    return _load_static_catalog().get(room_id)


# ────────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────────

def get_current_room(world_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Return the full room definition for the player's current location.
    Returns None if current_location is unset or points to an unknown room.
    """
    room_id = world_state.get("current_location")
    if not room_id:
        logger.debug("get_current_room: current_location not set in world_state.")
        return None
    room = _get_room(room_id, world_state)
    if room is None:
        logger.debug(f"get_current_room: unknown room_id '{room_id}'.")
    return room


def get_available_exits(world_state: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """
    Return the exits dict for the current room: {direction: room_id | None}.
    Returns empty dict if current room is unknown.
    """
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
    """
    Deterministically advance game_time in world_state by steps or days (spec Section 22b / Phase 12.3).
    Increments steps_since_period_start; when reaching STEPS_PER_PERIOD, resets to 0
    and advances period to the next in DAY_PERIODS (morning -> afternoon -> evening -> night -> morning).
    Increments day on night -> morning wrap.
    Ticks freshness_days on raw_food items in character inventory by the number of days passed.
    When freshness_days hits 0, raw_food flips to rotten_food.

    Returns dict with keys: day, period, steps_since_period_start, period_changed (bool), days_passed (int), spoiled_items (list).
    """
    # Guard if character_state was passed positionally as steps
    if isinstance(steps, dict) and character_state is None:
        character_state = steps
        steps = 1
    elif not isinstance(steps, int):
        steps = 1

    # Extract character_state if not explicitly provided
    if character_state is None and isinstance(world_state, dict):
        character_state = world_state.get("player_state") or world_state.get("character_state")
        if character_state is None and "inventory" in world_state and "game_time" not in world_state:
            character_state = world_state

    # Guard if world_state is only a character_state dict
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
            if next_idx == 0:  # wrapped night -> morning
                gt["day"] += 1
                days_passed += 1
            gt["period"] = DAY_PERIODS[next_idx]

    spoiled_items = []
    if days_passed > 0 and character_state is not None:
        import state_manager
        spoiled_items = state_manager.tick_food_spoilage(character_state, days=days_passed)

    # Check notice board refresh on 3-day boundary (Phase 13.5)
    nb_refreshed = False
    if isinstance(world_state, dict):
        current_day = gt.get("day", 1)
        nb = world_state.setdefault("notice_board", {})
        last_day = nb.get("last_refreshed_day", 0)
        if last_day == 0 or (current_day - last_day) >= 3:
            refresh_notice_board(world_state, force=True)
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
    """
    Attempt to move the player in the given direction.

    Rules:
      - direction must be one of north/south/east/west.
      - The exit at that direction must not be None.
      - The destination room must exist (static or dynamic).
      - On successful move, deterministically advances game_time by 1 step.
      - If period_changed == True and character_state is passed, triggers ration check (Section 22d).

    Args:
        direction: one of 'north', 'south', 'east', 'west'.
        world_state: current world save dict (mutated in place on success).
        character_state: optional character save dict for ration consumption.

    Returns:
        (success: bool, message: str, new_room: dict | None)
        On success: world_state['current_location'] is updated and new_room is returned.
        On failure: world_state is unchanged, message explains why.
    """
    direction = direction.lower().strip()
    if direction not in _VALID_DIRECTIONS:
        return False, f"'{direction}' is not a valid direction. Use north, south, east, or west.", None

    if character_state:
        is_locked_up = (
            character_state.get("status") == "captive"
            or bool(character_state.get("crime_state", {}).get("imprisoned"))
            or bool(world_state.get("is_imprisoned"))
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

    # Commit the move
    world_state["current_location"] = destination_id
    visited = world_state.setdefault("visited_rooms", [])
    if destination_id not in visited:
        visited.append(destination_id)

    # Advance game time (1 step per successful move)
    time_info = advance_time(world_state, steps=1, character_state=character_state)
    if time_info.get("period_changed") and character_state is not None:
        import state_manager
        state_manager.handle_period_change(world_state, character_state)

    # Lightweight World Events (~10% chance on player movement, spec Section 6d)
    check_and_trigger_world_event(world_state)

    logger.debug(f"move_player: moved to '{destination_id}' ({destination.get('name')})")
    return True, f"You move {direction} into {destination.get('name', destination_id)}.", destination


def validate_new_location(location_data: Any, world_state: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Validate a room definition proposed by the LLM before it can be registered.

    This is the function wired up in validation.py (resolving DEV-2 from Phase 1).
    Called by validate_extraction_output() when 'world_updates.new_location' is present.

    Rules checked:
      1. location_data must be a dict.
      2. Required fields: id, name, type, description, exits, is_safe.
      3. 'type' must be one of: town, wilderness, dungeon.
      4. 'exits' must be a dict of direction -> room_id | None.
      5. Each non-None exits value must point to an existing room (static or dynamic).
      6. Room ID must not already exist (no overwrites of existing rooms).

    Args:
        location_data: the proposed room dict from LLM extraction output.
        world_state: current world save (for existing room lookup).

    Returns:
        (True, "") if valid.
        (False, "<human-readable reason>") if invalid.
    """
    if not isinstance(location_data, dict):
        return False, f"new_location must be a dict, got {type(location_data).__name__}."

    # Required fields
    missing = _REQUIRED_ROOM_FIELDS - set(location_data.keys())
    if missing:
        return False, f"new_location is missing required fields: {sorted(missing)}."

    room_id = location_data["id"]
    if not isinstance(room_id, str) or not room_id.strip():
        return False, "new_location.id must be a non-empty string."

    # No duplicate IDs — no overwriting static or dynamic rooms
    known_ids = _all_known_room_ids(world_state)
    if room_id in known_ids:
        return (
            False,
            f"new_location.id '{room_id}' already exists — duplicate room IDs are not allowed. "
            f"The LLM must generate a unique ID for new locations.",
        )

    # Type check
    room_type = location_data.get("type")
    if room_type not in _VALID_ROOM_TYPES:
        return (
            False,
            f"new_location.type '{room_type}' is invalid. "
            f"Must be one of: {sorted(_VALID_ROOM_TYPES)}.",
        )

    # Exits validation
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
    Register a new LLM-generated room into world_state['dynamic_rooms']
    after it has passed validate_new_location().

    This is the single write path for LLM-generated rooms. It always
    re-validates before writing — callers may not skip validation.

    Returns:
        (True, "") on success.
        (False, "<reason>") on validation failure.
    """
    ok, reason = validate_new_location(location_data, world_state)
    if not ok:
        logger.debug(f"register_new_location rejected '{location_data.get('id')}': {reason}")
        return False, reason

    dynamic = world_state.setdefault("dynamic_rooms", {})
    room_id = location_data["id"]
    dynamic[room_id] = location_data
    logger.debug(f"register_new_location: registered new room '{room_id}' ({location_data.get('name')})")
    return True, ""


def mark_room_cleared(room_id: str, world_state: Dict[str, Any]) -> bool:
    """
    Mark a room as cleared (no active encounter) in world_state['cleared_rooms'].
    Cleared rooms suppress future random encounter rolls (spec Section 14).

    Returns True if the room exists and was successfully marked, False otherwise.
    """
    if _get_room(room_id, world_state) is None:
        logger.debug(f"mark_room_cleared: unknown room_id '{room_id}'.")
        return False

    cleared = world_state.setdefault("cleared_rooms", [])
    if room_id not in cleared:
        cleared.append(room_id)
        logger.debug(f"mark_room_cleared: '{room_id}' marked cleared.")
    return True


def get_room_loot(room_id: str, world_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Return the loot list for a room, respecting already-collected state.
    Loot is consumed on collection: entries are removed from world_state['collected_loot'].

    A room's loot is available if the room_id is NOT in world_state['collected_loot'].
    Returns [] if already collected or room is unknown.
    """
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
    """
    Return True if the room is a safe zone (no random encounters, rest allowed).
    Cleared rooms are also treated as safe.
    """
    room = _get_room(room_id, world_state)
    if room is None:
        return False
    if room.get("is_safe", False):
        return True
    if room_id in world_state.get("cleared_rooms", []):
        return True
    return False


def find_nearest_visited_safe_room(world_state: Dict[str, Any]) -> str:
    """
    Find the nearest previously-visited safe location to world_state['current_location'].
    Uses Breadth-First Search (BFS) over the known room graph (spec Section 17a).
    Returns room ID string (defaults to 'town_riverside' if no visited safe room found).
    """
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

    from collections import deque
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


# ════════════════════════════════════════════════════════════════════════════════
# LIGHTWEIGHT WORLD EVENTS (spec Section 6d / Phase 10 Part 2)
# ════════════════════════════════════════════════════════════════════════════════

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

    Args:
        world_state: current world save dict.
        chance: probability threshold (default 0.10 for ~10%).
        rng_val: optional float in [0.0, 1.0) to override random.random() for deterministic testing.

    Returns:
        Dict describing the triggered event (location_id, flag), or None if no event triggered.
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

    # Store in world_event_flags dict (mapping location_id -> list of flags)
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
    """
    Retrieve context lines for active world event flags at location_id.
    If consume is True, clears the flags for that location after reading
    (clears on visit policy so the prompt injection occurs once upon arrival).
    """
    event_flags = world_state.get("world_event_flags")
    if not isinstance(event_flags, dict) or location_id not in event_flags:
        return []

    loc_flags = event_flags.get(location_id, [])
    if not loc_flags:
        return []

    context_lines = []
    for flag in loc_flags:
        info = WORLD_EVENT_DEFINITIONS.get(flag, {})
        line = info.get("context_line")
        if line:
            context_lines.append(f"[World Event] {line}")

    if consume:
        event_flags.pop(location_id, None)

    return context_lines


# ────────────────────────────────────────────────────────────────────────────────
# PHASE 13.5 — NOTICE BOARD GENERATOR (MODULE C §2)
# ────────────────────────────────────────────────────────────────────────────────

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

    Guarantees:
    - All room IDs referenced come strictly from _all_known_room_ids(world_state).
    - Quests stored in world_state['notice_board'] and tavern room definition.
    - Sets hidden_cache: True on rumor target room in world_state['dynamic_rooms'].
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

    # Reveal hidden_cache: true on target room
    if rumor_room_id not in world_state.setdefault("dynamic_rooms", {}):
        static_copy = _load_static_catalog().get(rumor_room_id)
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

    # ── Store in notice_board state and in tavern room ────────────────────────
    nb["last_refreshed_day"] = current_day
    nb["refresh_count"] = refresh_count
    nb["entries"] = entries

    tavern_id = _find_tavern_room_id(world_state)
    if tavern_id not in world_state.setdefault("dynamic_rooms", {}):
        t_copy = _load_static_catalog().get(tavern_id)
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
    """
    Accept an available notice board quest and register it into world_state['quest_log']['side'].
    """
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
    """
    Complete an accepted notice board quest, granting reward and updating quest log.
    If delivery quest, validates character has target_item and consumes 1.
    """
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
        character_state["gold"] = character_state.get("gold", 0) + reward

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


def is_camp_context(world_state: Optional[Dict[str, Any]] = None) -> bool:
    """
    Check if the current game state qualifies as a camp / rest context (Spec Module D §2).
    Must return False if:
      - Mid-combat (world_state['combat_state']['status'] == 'active')
      - Mid-dungeon-crawl (in an uncleared or unsafe dungeon room)
      - World state is missing or empty
      - Player is imprisoned or captive
    Returns True if:
      - In a town, safe campsite, or resting at camp / short / long rest.
    """
    if not world_state or not isinstance(world_state, dict):
        return False

    # 1. Combat gating: mid-combat is NEVER a camp context
    combat_state = world_state.get("combat_state")
    if isinstance(combat_state, dict) and combat_state.get("status") == "active":
        return False

    # 2. Captivity gating
    if world_state.get("is_imprisoned") or world_state.get("status") == "captive":
        return False

    # 3. Explicit camp/resting flags
    if world_state.get("at_camp") is True or world_state.get("is_resting") is True:
        loc = world_state.get("current_location")
        if loc:
            room = _get_room(loc, world_state)
            if room and room.get("type") == "dungeon" and not is_room_safe(loc, world_state):
                return False
        return True

    # 4. Location-based check
    loc = world_state.get("current_location")
    if not loc:
        return False

    room = _get_room(loc, world_state)
    if room:
        rtype = room.get("type", "")
        # Dungeon room: uncleared/unsafe is definitely mid-dungeon crawl
        if rtype == "dungeon":
            return False
        # Town rooms are safe resting/camp areas
        if rtype == "town":
            return True
        # Wilderness: safe room or campfire
        if room.get("is_safe", False) or loc in world_state.get("cleared_rooms", []):
            if "camp" in loc.lower() or "safe" in loc.lower() or room.get("is_safe"):
                return True
    else:
        # Fallback for dynamic / test locations
        if "town" in loc.lower() or "camp" in loc.lower():
            return True
        if loc in world_state.get("cleared_rooms", []):
            return True

    return False


def interact_with_object(
    world_state: Dict[str, Any],
    room_id: str,
    object_id: str,
    combat_state: Optional[Dict[str, Any]] = None,
    character_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Interact with an environmental object in a room (exploration or combat).
    Supports triggering damage to enemies, setting surfaces, knocking prone, etc.
    """
    room = _get_room(room_id, world_state)
    if not room:
        return {"success": False, "message": f"Room '{room_id}' not found."}

    # Ensure dynamic copy of room exists so object state mutation is saved
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

    # In Combat effects
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
        # Out of combat
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



