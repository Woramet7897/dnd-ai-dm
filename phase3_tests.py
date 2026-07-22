"""
phase3_tests.py -- Definition of Done test script for Phase 3.
Tests dungeon_manager.py: move_player, register_new_location, validate_new_location,
mark_room_cleared, get_room_loot, and the DEV-2 resolution in validation.py.
Run with: python phase3_tests.py
No Streamlit. No Ollama. Pure Python.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dungeon_manager as dm
from validation import validate_extraction_output

PASS = 0
FAIL = 0

def check(label: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        print(f"  [PASS] {label}")
        PASS += 1
    else:
        print(f"  [FAIL] {label}  {detail}")
        FAIL += 1


def fresh_world(location="town_riverside"):
    """Minimal schema_version 4 world save dict."""
    return {
        "schema_version": 4,
        "current_location": location,
        "visited_rooms": [location],
        "dynamic_rooms": {},
        "cleared_rooms": [],
        "collected_loot": [],
        "npc_relationships": {},
        "party": {"companions": []},
        "quest_log": {"main": [], "side": []},
    }


# ===============================================================================
print("=" * 65)
print("TEST 1 -- get_current_room()")
print("=" * 65)

w = fresh_world("town_riverside")
room = dm.get_current_room(w)
print(f"\n  current_location='town_riverside': name='{room.get('name')}'")
check("Returns room dict for valid location", room is not None)
check("Room name is 'Riverside Village'", room.get("name") == "Riverside Village")
check("Room is_safe=True for town", room.get("is_safe") is True)

w_bad = fresh_world("void_of_nothingness")
room_bad = dm.get_current_room(w_bad)
print(f"\n  current_location='void_of_nothingness': result={room_bad}")
check("Returns None for unknown room", room_bad is None)
print()


# ===============================================================================
print("=" * 65)
print("TEST 2 -- move_player(): valid moves between hand-authored rooms")
print("=" * 65)

# Start at town_riverside, move north to forest_edge
w = fresh_world("town_riverside")
ok, msg, new_room = dm.move_player("north", w)
print(f"\n  Move north from town_riverside:")
print(f"    ok={ok}, msg='{msg}'")
print(f"    new location='{w['current_location']}', room name='{new_room.get('name') if new_room else None}'")
check("Move north succeeds", ok is True)
check("current_location updated to 'forest_edge'", w["current_location"] == "forest_edge")
check("Returned room name is 'Edge of Thornwood Forest'",
      new_room is not None and "Thornwood" in new_room.get("name", ""))
check("forest_edge added to visited_rooms", "forest_edge" in w["visited_rooms"])

# Now move south, back to town_riverside
ok2, msg2, new_room2 = dm.move_player("south", w)
print(f"\n  Move south from forest_edge:")
print(f"    ok={ok2}, msg='{msg2}'")
print(f"    new location='{w['current_location']}'")
check("Move south back to town succeeds", ok2 is True)
check("current_location updated to 'town_riverside'", w["current_location"] == "town_riverside")

# Move east from town_riverside to old_bridge
ok3, _, new_room3 = dm.move_player("east", w)
print(f"\n  Move east from town_riverside:")
print(f"    ok={ok3}, new location='{w['current_location']}'")
check("Move east to old_bridge succeeds", ok3 is True)
check("current_location='old_bridge'", w["current_location"] == "old_bridge")

# Invalid direction
ok4, msg4, _ = dm.move_player("up", w)
print(f"\n  Move 'up' (invalid direction): ok={ok4}, msg='{msg4}'")
check("Invalid direction returns False", ok4 is False)
check("Error message mentions 'up'", "up" in msg4.lower())

# Blocked exit (south from old_bridge is None)
w_ob = fresh_world("old_bridge")
ok5, msg5, _ = dm.move_player("south", w_ob)
print(f"\n  Move south from old_bridge (null exit): ok={ok5}, msg='{msg5}'")
check("Null exit returns False", ok5 is False)
check("Message says no exit exists", "no exit" in msg5.lower())

# Chain move: town -> forest_edge -> forest_clearing -> goblin_camp
w_chain = fresh_world("town_riverside")
moves = [("north", "forest_edge"), ("north", "forest_clearing"), ("north", "goblin_camp")]
for direction, expected in moves:
    ok_c, _, _ = dm.move_player(direction, w_chain)
    check(f"Chain move '{direction}' to '{expected}' succeeds",
          ok_c and w_chain["current_location"] == expected)
print()


# ===============================================================================
print("=" * 65)
print("TEST 3 -- register_new_location(): valid new room")
print("=" * 65)

w = fresh_world("town_riverside")
new_room_data = {
    "id": "haunted_cellar",
    "name": "Haunted Cellar",
    "type": "dungeon",
    "description": "A damp stone cellar beneath an abandoned farmhouse. Chains hang from the walls.",
    "exits": {
        "north": "town_riverside",  # connects to existing room
        "south": None,
        "east": None,
        "west": None,
    },
    "is_safe": False,
    "shops": [],
    "npcs": [],
    "loot": [],
    "encounter_table": ["skeleton", "zombie"],
}
ok, reason = dm.register_new_location(new_room_data, w)
print(f"\n  Register 'haunted_cellar': ok={ok}, reason='{reason}'")
check("Valid new room registers successfully", ok is True)
check("Room present in dynamic_rooms", "haunted_cellar" in w.get("dynamic_rooms", {}))
check("Room accessible via get_current_room after registration",
      dm._get_room("haunted_cellar", w) is not None)
print()


# ===============================================================================
print("=" * 65)
print("TEST 4 -- register_new_location(): REJECTS DUPLICATE ID")
print("=" * 65)

# Try to register a room with an ID that already exists in static catalog
w2 = fresh_world("town_riverside")
duplicate = {
    "id": "forest_edge",   # already in static catalog
    "name": "Overwritten Forest",
    "type": "wilderness",
    "description": "This should not be allowed.",
    "exits": {"north": None, "south": None, "east": None, "west": None},
    "is_safe": False,
}
ok_dup, reason_dup = dm.register_new_location(duplicate, w2)
print(f"\n  Register duplicate 'forest_edge': ok={ok_dup}, reason='{reason_dup}'")
check("Duplicate ID rejected (False)", ok_dup is False)
check("Reason mentions 'forest_edge'", "forest_edge" in reason_dup)
check("Static catalog room NOT overwritten",
      dm._get_room("forest_edge", w2).get("name") == "Edge of Thornwood Forest")

# Try to register a room with a previously-registered dynamic ID
w3 = fresh_world("town_riverside")
dm.register_new_location(new_room_data, w3)  # register haunted_cellar
ok_dup2, reason_dup2 = dm.register_new_location(new_room_data, w3)  # try again
print(f"\n  Re-register same dynamic room: ok={ok_dup2}, reason='{reason_dup2}'")
check("Duplicate dynamic ID also rejected", ok_dup2 is False)
print()


# ===============================================================================
print("=" * 65)
print("TEST 5 -- register_new_location(): REJECTS bad connects_to")
print("=" * 65)

w4 = fresh_world("town_riverside")
bad_exit_room = {
    "id": "mystery_cave",
    "name": "Mystery Cave",
    "type": "dungeon",
    "description": "A dark cave.",
    "exits": {
        "north": "nonexistent_portal_room_xyz",  # does NOT exist
        "south": "town_riverside",
        "east": None,
        "west": None,
    },
    "is_safe": False,
}
ok_bad, reason_bad = dm.register_new_location(bad_exit_room, w4)
print(f"\n  Register room with bad exit 'nonexistent_portal_room_xyz':")
print(f"  ok={ok_bad}, reason='{reason_bad}'")
check("Room with nonexistent exit rejected (False)", ok_bad is False)
check("Reason mentions the bad exit room ID", "nonexistent_portal_room_xyz" in reason_bad)
check("Room NOT added to dynamic_rooms", "mystery_cave" not in w4.get("dynamic_rooms", {}))

# Also test: invalid room type
w5 = fresh_world("town_riverside")
bad_type_room = {
    "id": "space_station",
    "name": "Space Station",
    "type": "sci_fi",   # not valid
    "description": "This is not a D&D setting.",
    "exits": {"north": None, "south": None, "east": None, "west": None},
    "is_safe": False,
}
ok_type, reason_type = dm.register_new_location(bad_type_room, w5)
print(f"\n  Register room with invalid type 'sci_fi': ok={ok_type}")
check("Invalid room type rejected", ok_type is False)
check("Reason mentions 'sci_fi'", "sci_fi" in reason_type)
print()


# ===============================================================================
print("=" * 65)
print("TEST 6 -- mark_room_cleared() and is_room_safe()")
print("=" * 65)

w6 = fresh_world("forest_edge")
safe_before = dm.is_room_safe("forest_edge", w6)
print(f"\n  forest_edge is_safe before clearing: {safe_before}")
check("Wilderness room not safe before clearing", safe_before is False)

cleared_ok = dm.mark_room_cleared("forest_edge", w6)
safe_after = dm.is_room_safe("forest_edge", w6)
print(f"  After mark_room_cleared: cleared_ok={cleared_ok}, is_safe={safe_after}")
check("mark_room_cleared returns True for valid room", cleared_ok is True)
check("Room is safe after clearing", safe_after is True)
check("forest_edge in cleared_rooms", "forest_edge" in w6["cleared_rooms"])

# Unknown room
ok_unk = dm.mark_room_cleared("void_zone", w6)
check("mark_room_cleared returns False for unknown room", ok_unk is False)
print()


# ===============================================================================
print("=" * 65)
print("TEST 7 -- get_room_loot()")
print("=" * 65)

w7 = fresh_world("forest_clearing")
loot = dm.get_room_loot("forest_clearing", w7)
print(f"\n  First loot from forest_clearing: {loot}")
check("Loot returned on first collection", len(loot) > 0)
check("torch loot present", any(l.get("item_id") == "torch" for l in loot))

loot2 = dm.get_room_loot("forest_clearing", w7)
print(f"  Second loot from forest_clearing (already collected): {loot2}")
check("Empty list on second collection (consumed)", loot2 == [])
check("forest_clearing in collected_loot", "forest_clearing" in w7["collected_loot"])

loot_empty = dm.get_room_loot("town_riverside", w7)
print(f"  Loot from town_riverside (no loot defined): {loot_empty}")
check("Room with no loot returns empty list", loot_empty == [])
print()


# ===============================================================================
print("=" * 65)
print("TEST 8 -- DEV-2 RESOLVED: validation.py now calls validate_new_location()")
print("=" * 65)
print("  Payloads with world_updates.new_location are now validated through")
print("  dungeon_manager.validate_new_location() before passing through.\n")

w_val = fresh_world("town_riverside")

# 8a: Valid new_location in world_updates -- should pass through
valid_nl_payload = {
    "world_updates": {
        "new_location": {
            "id": "secret_grotto",
            "name": "Secret Grotto",
            "type": "dungeon",
            "description": "A hidden grotto behind a waterfall.",
            "exits": {
                "north": "town_riverside",
                "south": None, "east": None, "west": None,
            },
            "is_safe": False,
        }
    }
}
result_a = validate_extraction_output(valid_nl_payload, world_state=w_val)
print(f"  Valid new_location output keys: {list(result_a.get('world_updates', {}).keys())}")
check("Valid new_location passes validation and appears in output",
      "new_location" in result_a.get("world_updates", {}))

# 8b: Invalid new_location (duplicate ID) -- should be stripped from world_updates
invalid_nl_payload = {
    "world_updates": {
        "new_location": {
            "id": "forest_edge",   # duplicate!
            "name": "Overwritten",
            "type": "wilderness",
            "description": "...",
            "exits": {"north": None, "south": None, "east": None, "west": None},
            "is_safe": False,
        },
        "some_flag": True,   # other keys should survive even if new_location is stripped
    }
}
result_b = validate_extraction_output(invalid_nl_payload, world_state=w_val)
print(f"  Duplicate new_location stripped. world_updates={result_b.get('world_updates')}")
check("Duplicate new_location stripped from world_updates",
      "new_location" not in result_b.get("world_updates", {}))
check("Other world_updates keys (some_flag) survive despite bad new_location",
      result_b.get("world_updates", {}).get("some_flag") is True)

# 8c: Invalid connects_to in new_location -- should be stripped
bad_exit_nl_payload = {
    "world_updates": {
        "new_location": {
            "id": "another_cave",
            "name": "Another Cave",
            "type": "dungeon",
            "description": "...",
            "exits": {
                "north": "nonexistent_xyz_room",  # bad exit
                "south": None, "east": None, "west": None,
            },
            "is_safe": False,
        }
    }
}
result_c = validate_extraction_output(bad_exit_nl_payload, world_state=w_val)
print(f"  Bad connects_to new_location stripped. world_updates={result_c.get('world_updates')}")
check("Bad connects_to new_location stripped from world_updates",
      "new_location" not in result_c.get("world_updates", {}))
print()


# ===============================================================================
print("=" * 65)
print(f"RESULTS:  {PASS} passed,  {FAIL} failed")
print("=" * 65)
sys.exit(0 if FAIL == 0 else 1)
