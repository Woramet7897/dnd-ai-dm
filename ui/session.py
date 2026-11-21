"""
ui/session.py — Session state management, game loading/saving, and state extraction helpers.
"""

import logging
import os
from typing import Any, Dict, List, Optional
import streamlit as st

import dungeon_manager
import llm_handler
import memory_manager
import state_manager

logger = logging.getLogger("app")


def init_session_state():
    """Ensure all required session state variables exist."""
    if "player_state" not in st.session_state:
        st.session_state["player_state"] = None
    if "world_state" not in st.session_state:
        st.session_state["world_state"] = None
    if "history_buffer" not in st.session_state:
        st.session_state["history_buffer"] = []
    if "narrative_log" not in st.session_state:
        st.session_state["narrative_log"] = []
    if "current_char_name" not in st.session_state:
        st.session_state["current_char_name"] = None
    if "last_action_msg" not in st.session_state:
        st.session_state["last_action_msg"] = None
    if "action_suggestions" not in st.session_state:
        st.session_state["action_suggestions"] = list(llm_handler.DEFAULT_ACTION_SUGGESTIONS)
    if "active_camp_dialogue" not in st.session_state:
        st.session_state["active_camp_dialogue"] = None
    if "memory_manager" not in st.session_state:
        st.session_state["memory_manager"] = None


def get_session_memory_manager() -> Optional[Any]:
    """Retrieve or initialize MemoryManager for current character in session state."""
    char_name = st.session_state.get("current_char_name")
    if not char_name:
        ps = st.session_state.get("player_state")
        if isinstance(ps, dict):
            char_name = ps.get("name")
    if not char_name:
        return st.session_state.get("memory_manager")

    mm = st.session_state.get("memory_manager")
    if mm is not None and getattr(mm, "character_name", None) == char_name:
        return mm

    try:
        mm = memory_manager.MemoryManager(char_name)
        st.session_state["memory_manager"] = mm
        return mm
    except Exception as ex:
        logger.warning(f"Failed to initialize MemoryManager for '{char_name}': {ex}")
        st.session_state["memory_manager"] = None
        return None


def auto_save():
    """Save both player and world states atomically, including narrative log and suggestions."""
    char_state = st.session_state.get("player_state")
    world_state = st.session_state.get("world_state")
    if char_state and world_state:
        char_name = char_state.get("name")
        if char_name:
            if "narrative_log" in st.session_state:
                world_state["narrative_log"] = list(st.session_state["narrative_log"])
            if "action_suggestions" in st.session_state:
                world_state["action_suggestions"] = list(st.session_state["action_suggestions"])
            state_manager.save_character(char_name, char_state)
            state_manager.save_world(char_name, world_state)


def list_saved_characters() -> List[str]:
    """Scan saves/ directory for existing character JSON files."""
    saves_dir = state_manager.SAVES_DIR
    if not os.path.exists(saves_dir):
        return []
    saves = []
    for fname in os.listdir(saves_dir):
        if fname.endswith(".json") and not fname.startswith("."):
            char_name = fname[:-5]
            saves.append(char_name)
    return sorted(saves)


def get_current_active_model() -> str:
    """Return appropriate model identifier matching the currently active LLM engine."""
    engine = llm_handler.get_active_engine()
    if engine == "groq":
        cfg = llm_handler.load_groq_config()
        return cfg.get("model") or llm_handler.DEFAULT_GROQ_MODEL
    elif engine == "gemini":
        cfg = llm_handler.load_gemini_config()
        return cfg.get("model") or llm_handler.DEFAULT_GEMINI_MODEL
    else:
        return st.session_state.get("selected_model") or llm_handler.resolve_model()


def get_display_spell_catalog(world: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return merged spell catalog including static spells and world_state generated_spells."""
    return state_manager.get_spell_catalog(world)


def resolve_turn_extraction(
    action: str,
    narrative_text: str,
    res: Dict[str, Any],
    player: Dict[str, Any],
    world: Dict[str, Any],
    active_model: str,
    ollama_model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Determine state updates for a turn.
    - If is_passive_exploration_turn is True:
        Uses single_pass_updates from narrative call (if present) to avoid slow 2nd call.
    - If is_passive_exploration_turn is False (active turn):
        Always calls full extract_state_updates and uses its result instead of single_pass_updates
        to ensure quests, recruits, spells, items, and NPCs are fully extracted without double-apply.
    """
    if llm_handler.is_passive_exploration_turn(action, narrative_text):
        if res.get("single_pass_updates") is not None:
            return res["single_pass_updates"]
        logger.info("Single-pass JSON omitted on passive exploration turn; safely skipped 2nd LLM call.")
        return {}

    # Active turn: call full extract_state_updates
    ext_model = active_model
    if llm_handler.get_active_engine() == "ollama":
        installed_m = llm_handler.get_installed_models()
        if any("qwen2.5:3b" in str(m) for m in installed_m):
            ext_model = "qwen2.5:3b"
    return llm_handler.extract_state_updates(
        narrative_text=narrative_text,
        user_input=action,
        world_state=world,
        player_state=player,
        model=ext_model,
        ollama_model=ollama_model,
    )


def load_game(char_name: str, client: Optional[Any] = None) -> bool:
    """Load character and world state from disk, restoring complete narrative history."""
    try:
        player = state_manager.load_character(char_name)
        world = state_manager.load_world(char_name)
        state_manager._ensure_currency(player)
        st.session_state["player_state"] = player
        st.session_state["world_state"] = world
        st.session_state["current_char_name"] = char_name
        st.session_state.pop("pending_bg3_check", None)

        saved_log = world.get("narrative_log")
        saved_suggs = world.get("action_suggestions")
        if saved_suggs:
            st.session_state["action_suggestions"] = list(saved_suggs)

        # Restore past narrative history
        if saved_log:
            st.session_state["narrative_log"] = list(saved_log)
        else:
            st.session_state["narrative_log"] = []

        room = dungeon_manager.get_current_room(world)
        room_name = room.get("name", "Unknown Location") if room else "Unknown"

        # Session Recap on Load (spec Section 14a / Phase 10 Part 1)
        recap_text = ""
        recap_key = f"recap_generated_{char_name}"
        if not st.session_state.get(recap_key):
            st.session_state[recap_key] = True
            mm = get_session_memory_manager()
            if mm is not None:
                major_ids = mm.get_all_major_lore_ids()
                if len(major_ids) >= 2:
                    major_col = mm._get_major_collection()
                    docs_res = major_col.get(where={"character": {"$eq": char_name}}, include=["documents"])
                    docs = docs_res.get("documents", [])
                    if len(docs) >= 2:
                        major_entries = [{"text": d, "type": "major"} for d in docs[-3:]]
                        with st.spinner("📜 กำลังสรุปเรื่องราวที่ผ่านมา..."):
                            res = llm_handler.generate_narrative_response(
                                user_input="Please provide a concise 'Previously, in your story...' recap paragraph summarizing our major past chapters.",
                                player_state=player,
                                world_state=world,
                                lore_entries=major_entries,
                                model=get_current_active_model(),
                                client=client,
                                ollama_model=st.session_state.get("selected_model"),
                            )
                        recap_narrative = res.get("narrative", "")
                        if res.get("suggestions"):
                            st.session_state["action_suggestions"] = res["suggestions"]
                        if recap_narrative:
                            if not recap_narrative.startswith("Previously"):
                                recap_narrative = f"Previously, in your story...\n{recap_narrative}"
                            recap_text = f"\n\n📖 **Session Recap:**\n{recap_narrative}"

        if not st.session_state["narrative_log"]:
            st.session_state["narrative_log"].append({
                "role": "assistant",
                "content": f"Loaded save for **{char_name}**. You are currently in **{room_name}**.{recap_text}"
            })
        elif recap_text:
            st.session_state["narrative_log"].append({
                "role": "assistant",
                "content": f"📖 **Welcome back to your adventure, {char_name}!** You are currently in **{room_name}**.{recap_text}"
            })
        return True
    except Exception as e:
        st.error(f"Failed to load save '{char_name}': {e}")
        return False


# ────────────────────────────────────────────────────────────────────────────────
# State Extraction Helpers (Shared between Sidebar, Top HUD Bar & Dialogs)
# ────────────────────────────────────────────────────────────────────────────────

def get_ration_count(player: Optional[Dict[str, Any]]) -> int:
    """Return total quantity of trail rations in player inventory."""
    if not player or not isinstance(player, dict):
        return 0
    inv = player.get("inventory", [])
    return sum(
        item.get("quantity", 1)
        for item in inv
        if isinstance(item, dict) and item.get("item_id") == "trail_rations"
    )


def get_active_quest_count(world: Optional[Dict[str, Any]]) -> int:
    """Return count of active quests in quest_log (main + side)."""
    if not world or not isinstance(world, dict):
        return 0
    q_log = world.get("quest_log", {})
    if not isinstance(q_log, dict):
        return 0
    active_count = 0
    for q in q_log.get("main", []):
        if isinstance(q, dict) and q.get("status", "active") == "active":
            active_count += 1
    for q in q_log.get("side", []):
        if isinstance(q, dict) and q.get("status", "active") == "active":
            active_count += 1
    return active_count


def get_known_spell_count(player: Optional[Dict[str, Any]]) -> int:
    """Return count of known spells (including cantrips) in player state."""
    if not player or not isinstance(player, dict):
        return 0
    known = player.get("known_spells", [])
    return len(known) if isinstance(known, list) else 0


def get_player_currency(player: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """Return dict with gp, sp, cp currency keys from player state."""
    if not player or not isinstance(player, dict):
        return {"gp": 0, "sp": 0, "cp": 0}
    return state_manager._ensure_currency(player)
