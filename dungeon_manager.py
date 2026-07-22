"""
dungeon_manager.py — Phase 3
Handles room navigation, location registration, loot, and room state.
No LLM calls. No Streamlit. Pure Python + dungeon_data.json + world saves.

Scope: static catalog navigation + dynamic world-save updates.
World events (Phase 10) and companion-based room effects are excluded here.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

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


def move_player(
    direction: str,
    world_state: Dict[str, Any],
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Attempt to move the player in the given direction.

    Rules:
      - direction must be one of north/south/east/west.
      - The exit at that direction must not be None.
      - The destination room must exist (static or dynamic).

    Args:
        direction: one of 'north', 'south', 'east', 'west'.
        world_state: current world save dict (mutated in place on success).

    Returns:
        (success: bool, message: str, new_room: dict | None)
        On success: world_state['current_location'] is updated and new_room is returned.
        On failure: world_state is unchanged, message explains why.
    """
    direction = direction.lower().strip()
    if direction not in _VALID_DIRECTIONS:
        return False, f"'{direction}' is not a valid direction. Use north, south, east, or west.", None

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
    if room_id in collected:
        return []  # Already looted

    loot = room.get("loot", [])
    if loot:
        collected.append(room_id)
        logger.debug(f"get_room_loot: '{room_id}' loot collected: {loot}")
    return list(loot)


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
