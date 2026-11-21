"""
dungeon_manager.py — Room Navigation, World Events & Notice Board Facade.

Modularized into the `dungeon/` package:
  - dungeon/navigation.py   : Room lookup, movement, time progression, loot collection, safe room BFS
  - dungeon/events.py       : Lightweight World Events and Interactive Environmental Objects
  - dungeon/notice_board.py : Tavern Notice Board Quest Generator & Completion

All symbols remain exposed on `dungeon_manager` for 100% backward compatibility.
"""

import json
import logging
import os
from typing import Any, Dict, Optional

import paths

logger = logging.getLogger("dungeon_manager")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[DUNGEON] %(levelname)s: %(message)s"))
    logger.addHandler(_h)

_DUNGEON_DATA_PATH = os.path.join(paths.CATALOG_DIR, "dungeon_data.json")
_static_catalog: Optional[Dict[str, Any]] = None


def _load_static_catalog() -> Dict[str, Any]:
    """Load dungeon_data.json once and cache it in module scope."""
    global _static_catalog
    if _static_catalog is None:
        with open(_DUNGEON_DATA_PATH, "r", encoding="utf-8") as f:
            _static_catalog = json.load(f)
    return _static_catalog


# ── Re-export from dungeon/ submodules ────────────────────────────────────────

from dungeon.navigation import (
    DAY_PERIODS,
    STEPS_PER_PERIOD,
    _REQUIRED_ROOM_FIELDS,
    _VALID_DIRECTIONS,
    _VALID_ROOM_TYPES,
    _all_known_room_ids,
    _get_room,
    advance_time,
    find_nearest_visited_safe_room,
    get_available_exits,
    get_current_room,
    get_room_loot,
    is_camp_context,
    is_room_safe,
    mark_room_cleared,
    move_player,
    normalize_new_location,
    register_new_location,
    validate_new_location,
)

from dungeon.events import (
    WORLD_EVENT_DEFINITIONS,
    check_and_trigger_world_event,
    get_active_world_events_for_location,
    interact_with_object,
    register_dynamic_event,
)

from dungeon.notice_board import (
    _find_tavern_room_id,
    accept_notice_board_quest,
    complete_notice_board_quest,
    get_notice_board,
    refresh_notice_board,
)
