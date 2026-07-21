import json
import os
from typing import Dict, Any

STATE_FILE = "player_state.json"

def load_state() -> Dict[str, Any]:
    """Load the player state from the JSON file."""
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_state(state: Dict[str, Any]):
    """Save the player state to the JSON file."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

def apply_state_updates(updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply dynamic state changes based on a dictionary of updates.
    Handles numeric changes (e.g., hp_change), list additions (add_item),
    and direct overwrites (location).
    """
    state = load_state()
    if not state:
        return state

    for key, value in updates.items():
        if key == "hp_change" and isinstance(value, (int, float)):
            state["hp"] = max(0, state.get("hp", 100) + value)
        elif key == "level_change" and isinstance(value, (int, float)):
            state["level"] = max(1, state.get("level", 1) + value)
        elif key == "add_item" and isinstance(value, str):
            if "inventory" not in state:
                state["inventory"] = []
            if value not in state["inventory"]:
                state["inventory"].append(value)
        elif key == "remove_item" and isinstance(value, str):
            if "inventory" in state and value in state["inventory"]:
                state["inventory"].remove(value)
        elif key == "add_quest" and isinstance(value, str):
            if "active_quests" not in state:
                state["active_quests"] = []
            if value not in state["active_quests"]:
                state["active_quests"].append(value)
        elif key == "remove_quest" and isinstance(value, str):
            if "active_quests" in state and value in state["active_quests"]:
                state["active_quests"].remove(value)
        elif key == "location" and isinstance(value, str):
            state["location"] = value
        # Add any direct overwrites here if needed (e.g., if LLM directly outputs "hp": 50 instead of "hp_change": -10)
        elif key in state:
            state[key] = value

    save_state(state)
    return state
