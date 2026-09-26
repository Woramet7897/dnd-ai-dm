"""
app.py — Phase 7 MVP Core Loop
Streamlit Interface for D&D 5e AI DM Engine.

Wires together all modules (character_creator, validation, state_manager,
dungeon_manager, combat_manager, memory_manager, llm_handler) into a working,
interactive Streamlit application.
"""

import json
import logging
import os
import streamlit as st
from typing import Any, Dict, List, Optional

import character_creator
import combat_manager
import dungeon_manager
import llm_handler
import memory_manager
import state_manager
import validation

logger = logging.getLogger("app")
logger.setLevel(logging.DEBUG)

# Page configuration
st.set_page_config(
    page_title="D&D 5e AI DM",
    page_icon="🎲",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ────────────────────────────────────────────────────────────────────────────────
# Session State Helpers
# ────────────────────────────────────────────────────────────────────────────────

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


def auto_save():
    """Save both player and world states atomically."""
    char_state = st.session_state.get("player_state")
    world_state = st.session_state.get("world_state")
    if char_state and world_state:
        char_name = char_state.get("name")
        if char_name:
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


def load_game(char_name: str) -> bool:
    """Load character and world state from disk."""
    try:
        player = state_manager.load_character(char_name)
        world = state_manager.load_world(char_name)
        st.session_state["player_state"] = player
        st.session_state["world_state"] = world
        st.session_state["current_char_name"] = char_name

        # Initial welcome entry in narrative log if empty
        if not st.session_state["narrative_log"]:
            room = dungeon_manager.get_current_room(world)
            room_name = room.get("name", "Unknown Location") if room else "Unknown"
            st.session_state["narrative_log"].append({
                "role": "assistant",
                "content": f"Loaded save for **{char_name}**. You are currently in **{room_name}**."
            })
        return True
    except Exception as e:
        st.error(f"Failed to load save '{char_name}': {e}")
        return False


# ────────────────────────────────────────────────────────────────────────────────
# Character Creation / Landing View
# ────────────────────────────────────────────────────────────────────────────────

def render_landing_view():
    st.title("🎲 D&D 5e AI DM Engine (MVP Milestone)")
    st.markdown("Welcome, adventurer. Select an existing save or create a new character to begin.")

    saved_chars = list_saved_characters()

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("📂 Load Existing Character")
        if saved_chars:
            selected_char = st.selectbox("Select Character Save:", saved_chars)
            if st.button("▶️ Load Character", use_container_width=True):
                if load_game(selected_char):
                    st.rerun()
        else:
            st.info("No existing character saves found.")

    with col2:
        st.subheader("✨ Create New Character")
        with st.form("character_creation_form"):
            char_name = st.text_input("Character Name:", value="Valeros")
            
            race = st.selectbox("Race:", ["Human", "Elf", "Dwarf", "Halfling", "Dragonborn"])
            cls_name = st.selectbox("Class:", ["Fighter", "Wizard", "Rogue", "Cleric"])
            bg = st.selectbox("Background:", ["Folk Hero", "Acolyte", "Criminal", "Noble", "Sage", "Soldier"])

            st.markdown("##### Ability Scores (Point-Buy: 27 Points Total)")
            c_str = st.slider("STR", 8, 15, 14)
            c_dex = st.slider("DEX", 8, 15, 14)
            c_con = st.slider("CON", 8, 15, 12)
            c_int = st.slider("INT", 8, 15, 10)
            c_wis = st.slider("WIS", 8, 15, 10)
            c_cha = st.slider("CHA", 8, 15, 8)

            stats_alloc = {"STR": c_str, "DEX": c_dex, "CON": c_con, "INT": c_int, "WIS": c_wis, "CHA": c_cha}
            ok_pts, pts_msg = character_creator.validate_point_buy(stats_alloc)
            if not ok_pts:
                st.warning(f"⚠️ Point-Buy Allocation: {pts_msg}")
            else:
                st.success(f"✅ {pts_msg}")

            submitted = st.form_submit_button("⚔️ Create Character", use_container_width=True)
            if submitted:
                if not char_name.strip():
                    st.error("Please enter a character name.")
                elif not ok_pts:
                    st.error("Invalid point-buy stats allocation.")
                else:
                    try:
                        # Build character
                        player = character_creator.create_character(
                            name=char_name.strip(),
                            race=race,
                            class_name=cls_name,
                            background=bg,
                            stats=stats_alloc,
                        )
                        # Add starting trail_rations if not present
                        inv = player.setdefault("inventory", [])
                        if not any(i.get("item_id") == "trail_rations" for i in inv):
                            inv.append({"item_id": "trail_rations", "equipped": False, "quantity": 3})

                        world = {
                            "schema_version": 4,
                            "character_name": char_name.strip(),
                            "current_location": "town_riverside",
                            "visited_rooms": ["town_riverside"],
                            "cleared_rooms": [],
                            "collected_loot": [],
                            "dynamic_rooms": {},
                            "game_time": {"day": 1, "period": "morning", "steps_since_period_start": 0},
                            "quest_log": {"main": [], "side": []},
                            "combat_state": None,
                        }

                        state_manager.save_character(char_name.strip(), player)
                        state_manager.save_world(char_name.strip(), world)

                        st.session_state["player_state"] = player
                        st.session_state["world_state"] = world
                        st.session_state["current_char_name"] = char_name.strip()
                        st.session_state["narrative_log"] = [{
                            "role": "assistant",
                            "content": f"Welcome to Riverside Village, **{char_name.strip()}**! Your journey begins here."
                        }]

                        st.success(f"Character '{char_name.strip()}' created successfully!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error creating character: {e}")


# ────────────────────────────────────────────────────────────────────────────────
# Main Playing Interface & Sidebar
# ────────────────────────────────────────────────────────────────────────────────

def render_sidebar():
    """Render character sheet, stats, location, and time in the sidebar."""
    player = st.session_state.get("player_state")
    world = st.session_state.get("world_state")
    if not player or not world:
        return

    st.sidebar.title(f"🛡️ {player.get('name', 'Adventurer')}")
    st.sidebar.caption(f"Level {player.get('level', 1)} {player.get('race', '')} {player.get('class_name', player.get('class', ''))}")

    # HP Progress Bar
    hp_info = player.get("hp", {"current": 10, "max": 10})
    cur_hp = hp_info.get("current", 0)
    max_hp = hp_info.get("max", 10)
    hp_pct = max(0.0, min(1.0, cur_hp / max(1, max_hp)))
    st.sidebar.markdown(f"**HP:** {cur_hp} / {max_hp}")
    st.sidebar.progress(hp_pct)

    # XP & Level Display
    xp_cur = player.get("xp_current", 0)
    lvl = player.get("level", 1)
    next_xp = state_manager.LEVEL_THRESHOLDS.get(lvl + 1, "MAX")
    st.sidebar.markdown(f"**XP:** {xp_cur} / {next_xp}")

    col_ac, col_gold = st.sidebar.columns(2)
    with col_ac:
        st.markdown(f"**AC:** {player.get('ac', 10)}")
    with col_gold:
        st.markdown(f"**Gold:** {player.get('gold', 0)} GP")

    # Ability Stats & Mods
    st.sidebar.markdown("---")
    st.sidebar.markdown("##### Ability Stats")
    stats = player.get("stats", {})
    cols_s1, cols_s2, cols_s3 = st.sidebar.columns(3)
    with cols_s1:
        str_val = stats.get("STR", 10)
        st.markdown(f"**STR:** {str_val} ({state_manager.get_modifier(str_val):+d})")
        int_val = stats.get("INT", 10)
        st.markdown(f"**INT:** {int_val} ({state_manager.get_modifier(int_val):+d})")
    with cols_s2:
        dex_val = stats.get("DEX", 10)
        st.markdown(f"**DEX:** {dex_val} ({state_manager.get_modifier(dex_val):+d})")
        wis_val = stats.get("WIS", 10)
        st.markdown(f"**WIS:** {wis_val} ({state_manager.get_modifier(wis_val):+d})")
    with cols_s3:
        con_val = stats.get("CON", 10)
        st.markdown(f"**CON:** {con_val} ({state_manager.get_modifier(con_val):+d})")
        cha_val = stats.get("CHA", 10)
        st.markdown(f"**CHA:** {cha_val} ({state_manager.get_modifier(cha_val):+d})")

    # Time & Location
    st.sidebar.markdown("---")
    gt = world.get("game_time", {})
    day = gt.get("day", 1)
    period = gt.get("period", "morning").capitalize()
    st.sidebar.markdown(f"**Time:** Day {day}, {period}")

    current_room = dungeon_manager.get_current_room(world)
    room_name = current_room.get("name", "Unknown") if current_room else "Unknown"
    room_type = current_room.get("type", "wilderness").capitalize() if current_room else ""
    st.sidebar.markdown(f"**Location:** {room_name} ({room_type})")

    # Active Conditions & Rations
    active_conds = player.get("active_conditions", [])
    if active_conds:
        formatted_conds = []
        for c in active_conds:
            if isinstance(c, str):
                formatted_conds.append(c)
            elif isinstance(c, dict):
                c_name = c.get("name", c.get("condition", "unknown"))
                dur = c.get("duration")
                formatted_conds.append(f"{c_name} ({dur} rds)" if dur else c_name)
        st.sidebar.warning(f"⚠️ Conditions: {', '.join(formatted_conds)}")

    # Party Companions & Dismissal UI
    party = world.get("party", {})
    companions = party.get("companions", [])
    if companions:
        st.sidebar.markdown("---")
        st.sidebar.markdown("##### 👥 Party Companions")
        for comp in companions:
            c_name = comp.get("name", comp.get("id", "Companion"))
            c_id = comp.get("id", c_name)
            col_c_info, col_c_btn = st.sidebar.columns([2, 1])
            with col_c_info:
                st.markdown(f"• **{c_name}**")
            with col_c_btn:
                with st.popover("Dismiss"):
                    st.write(f"Dismiss {c_name}?")
                    if st.button("Confirm Dismiss", key=f"dismiss_{c_id}", use_container_width=True):
                        try:
                            state_manager.dismiss_companion(c_id, world)
                            auto_save()
                            st.toast(f"{c_name} has left your party.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error: {e}")

    # Rations count
    inventory = player.get("inventory", [])
    ration_qty = sum(i.get("quantity", 1) for i in inventory if isinstance(i, dict) and i.get("item_id") == "trail_rations")
    st.sidebar.markdown(f"🍞 **Trail Rations:** {ration_qty}")

    # Town Actions
    if current_room and current_room.get("type") == "town":
        if st.sidebar.button("⛺ Take Long Rest (Town)", use_container_width=True):
            state_manager.long_rest(world, player)
            auto_save()
            st.session_state["narrative_log"].append({
                "role": "assistant",
                "content": f"You spend the night resting comfortably at Riverside Village. HP fully restored! Time is now **Day {world['game_time']['day']}, Morning**."
            })
            st.rerun()

    # Save & Exit Controls
    st.sidebar.markdown("---")
    col_save, col_exit = st.sidebar.columns(2)
    with col_save:
        if st.button("💾 Save Game", use_container_width=True):
            auto_save()
            st.toast("Game saved successfully!")
    with col_exit:
        if st.button("🚪 Main Menu", use_container_width=True):
            auto_save()
            st.session_state["player_state"] = None
            st.session_state["world_state"] = None
            st.rerun()


def render_playing_view():
    player = st.session_state.get("player_state")
    world = st.session_state.get("world_state")
    if not player or not world:
        return

    render_sidebar()

    current_room = dungeon_manager.get_current_room(world)
    if not current_room:
        st.error("Current location is invalid or missing.")
        return

    # Header Room Description
    st.title(f"📍 {current_room.get('name', 'Unknown Room')}")
    st.info(f"*{current_room.get('description', '')}*")

    # Status message if any
    if st.session_state.get("last_action_msg"):
        st.toast(st.session_state["last_action_msg"])
        st.session_state["last_action_msg"] = None

    # Story & Narrative Log
    st.markdown("### 📜 Narrative Log")
    log_container = st.container(height=350)
    with log_container:
        for entry in st.session_state["narrative_log"]:
            role = entry.get("role", "assistant")
            icon = "🧑‍🌾" if role == "user" else "🎲"
            with st.chat_message(role, avatar=icon):
                st.markdown(entry.get("content", ""))

    combat_active = world.get("combat_state") is not None

    # ────────────────────────────────────────────────────────────────────────────
    # COMBAT PANEL (when combat_state is active)
    # ────────────────────────────────────────────────────────────────────────────
    if combat_active:
        st.markdown("---")
        st.error("⚔️ **COMBAT IN PROGRESS**")
        cs = world["combat_state"]
        round_num = cs.get("round", 1)
        enemies = cs.get("enemies", [])

        col_c1, col_c2 = st.columns([2, 1])

        with col_c1:
            st.markdown(f"##### Round {round_num} — Active Combatants")
            for enemy in enemies:
                e_id = enemy.get("id", "enemy")
                e_name = enemy.get("name", e_id)
                e_hp = enemy.get("hp", {})
                cur_e_hp = e_hp.get("current", 0)
                max_e_hp = e_hp.get("max", 1)
                st.markdown(f"👹 **{e_name}** | HP: {cur_e_hp}/{max_e_hp} | AC: {enemy.get('ac', 10)}")

        with col_c2:
            st.markdown("##### Combat Actions")
            btn_disabled = (cs.get("status") == "ended") or (cs.get("downed_outcome") is not None)
            if st.button("⚔️ Resolve Combat Round", disabled=btn_disabled, use_container_width=True, type="primary"):
                # 1. Resolve full combat round (Python math)
                res_round = combat_manager.resolve_round(cs, world)
                narration_block = res_round["narration_block"]
                outcome = res_round["combat_outcome"]

                # 2. EXACTLY ONE narrative LLM call for the entire round
                hist = st.session_state.get("history_buffer", [])
                narrative_res = llm_handler.generate_narrative_response(
                    user_input="",
                    player_state=player,
                    world_state=world,
                    history=hist,
                    round_result=narration_block,
                )

                narration_text = narrative_res.get("narrative", "")
                full_combat_msg = f"{narration_block}\n\n*{narration_text}*"

                # 3. Append turn to narrative log & history buffer
                st.session_state["narrative_log"].append({"role": "assistant", "content": full_combat_msg})
                st.session_state["history_buffer"].append({"role": "assistant", "content": narration_text})

                # 4. Check combat end
                if outcome == "player_victory":
                    combat_manager.end_combat(world)
                    st.success("🎉 Combat Victory! All enemies have been defeated.")
                    st.session_state["narrative_log"].append({
                        "role": "assistant",
                        "content": "🏆 **Victory!** You defeated your foes and stand triumphant."
                    })
                elif outcome == "player_defeat":
                    out_res = cs.get("downed_outcome") or state_manager.resolve_downed_outcome(player, cs, world)
                    combat_manager.end_combat(world)
                    
                    out_name = out_res.get("outcome", "robbed_and_left")
                    pen = out_res.get("penalty", {})

                    if out_name == "robbed_and_left":
                        msg = f"⚠️ **Defeat!** You were knocked unconscious, robbed of {pen.get('gold_lost', 0)} GP, and left for dead. You awaken at **{pen.get('relocated_to')}** with 1 HP."
                    elif out_name == "captured":
                        msg = "⚠️ **Defeat!** You were captured by your enemies! You are now held captive."
                    else:  # rescued_by_npc
                        msg = f"⚠️ **Defeat!** A wanderer rescued you from death! Restored to {pen.get('hp_restored', 1)} HP."

                    st.error(msg)
                    st.session_state["narrative_log"].append({
                        "role": "assistant",
                        "content": msg
                    })

                auto_save()
                st.rerun()

    # ────────────────────────────────────────────────────────────────────────────
    # EXPLORATION PANEL (when NOT in combat)
    # ────────────────────────────────────────────────────────────────────────────
    else:
        st.markdown("---")
        col_nav, col_act = st.columns([1, 2])

        with col_nav:
            if player.get("status") == "captive":
                st.markdown("##### 🔒 Captive — Attempt Escape")
                st.info("You are held captive! Normal movement is disabled until you escape.")
                if st.button("🔓 Attempt Escape", use_container_width=True, type="primary"):
                    # One resolve_check against hard difficulty (DC 16)
                    esc_res = state_manager.resolve_check("DEX", "hard", player)
                    
                    # Advance time by 1 step (costs a turn)
                    time_res = dungeon_manager.advance_time(world, steps=1)
                    if time_res.get("period_changed"):
                        state_manager.handle_period_change(world, player)

                    if esc_res["success"]:
                        player["status"] = "normal"
                        safe_room = dungeon_manager.find_nearest_visited_safe_room(world)
                        world["current_location"] = safe_room
                        st.success("🎉 Escape Successful!")
                        st.session_state["narrative_log"].append({
                            "role": "assistant",
                            "content": f"🔓 **Escape Successful!** (Rolled {esc_res['total']} vs DC {esc_res['dc']}). You broke free from your bonds and fled to **{safe_room}**."
                        })
                    else:
                        st.error("❌ Escape Failed!")
                        st.session_state["narrative_log"].append({
                            "role": "assistant",
                            "content": f"🔒 **Escape Failed!** (Rolled {esc_res['total']} vs DC {esc_res['dc']}). The guards catch you. Time passes..."
                        })
                    auto_save()
                    st.rerun()
            else:
                st.markdown("##### 🧭 Navigation & Movement")
                exits = dungeon_manager.get_available_exits(world)
                
                nav_cols = st.columns(2)
                directions = [("North ⬆️", "north"), ("South ⬇️", "south"), ("East ➡️", "east"), ("West ⬅️", "west")]
                
                for idx, (label, dir_key) in enumerate(directions):
                    col_idx = idx % 2
                    dest_id = exits.get(dir_key)
                    with nav_cols[col_idx]:
                        if dest_id is not None:
                            if st.button(label, key=f"move_{dir_key}", use_container_width=True):
                                ok_m, msg_m, new_room = dungeon_manager.move_player(dir_key, world, player)
                                if ok_m and new_room:
                                    st.session_state["last_action_msg"] = msg_m
                                    st.session_state["narrative_log"].append({
                                        "role": "user",
                                        "content": f"I move {dir_key} into {new_room.get('name')}."
                                    })
                                    auto_save()
                                    st.rerun()
                        else:
                            st.button(label, key=f"move_disabled_{dir_key}", disabled=True, use_container_width=True)

        with col_act:
            st.markdown("##### 🎭 Actions & Interaction")
            
            # Action Text Input Form
            with st.form("action_form", clear_on_submit=True):
                user_action = st.text_input("What do you want to do?", placeholder="e.g. Look around the room, talk to the merchant...")
                sub_act = st.form_submit_button("Submit Action", use_container_width=True)
                
                if sub_act and user_action.strip():
                    st.session_state["narrative_log"].append({"role": "user", "content": user_action.strip()})
                    
                    # 1. Narrative Call
                    hist = st.session_state.get("history_buffer", [])
                    res = llm_handler.generate_narrative_response(
                        user_input=user_action.strip(),
                        player_state=player,
                        world_state=world,
                        history=hist,
                    )
                    narrative_text = res.get("narrative", "")
                    
                    # 2. Extraction Call
                    ext_res = llm_handler.generate_extraction_response(
                        narrative_text=narrative_text,
                        player_state=player,
                        world_state=world,
                    )
                    
                    # 3. Apply state updates if present
                    if ext_res:
                        if "state_updates" in ext_res and ext_res["state_updates"]:
                            state_manager.apply_state_updates(ext_res["state_updates"], player, world)
                        if "quest_updates" in ext_res and ext_res["quest_updates"]:
                            state_manager.apply_quest_updates(ext_res["quest_updates"], world)

                    st.session_state["narrative_log"].append({"role": "assistant", "content": narrative_text})
                    st.session_state["history_buffer"].append({"role": "user", "content": user_action.strip()})
                    st.session_state["history_buffer"].append({"role": "assistant", "content": narrative_text})

                    auto_save()
                    st.rerun()

            # Manual Ability Check & Combat Launchers
            col_chk, col_cmbt = st.columns(2)
            with col_chk:
                with st.popover("🎲 Make Ability Check"):
                    chk_stat = st.selectbox("Stat:", ["STR", "DEX", "CON", "INT", "WIS", "CHA"])
                    chk_diff = st.selectbox("Difficulty:", ["easy", "medium", "hard", "very_hard"])
                    if st.button("Roll d20", use_container_width=True):
                        chk_res = state_manager.resolve_check(chk_stat, chk_diff, player)
                        succ_str = "SUCCESS ✅" if chk_res["success"] else "FAILURE ❌"
                        roll_msg = (
                            f"🎲 **{chk_stat} Check ({chk_diff.capitalize()} DC {chk_res['dc']})**: "
                            f"Rolled {chk_res['roll']} + mod {chk_res['modifier']} = **{chk_res['total']}** → **{succ_str}**"
                        )
                        st.session_state["narrative_log"].append({"role": "assistant", "content": roll_msg})
                        auto_save()
                        st.rerun()

            with col_cmbt:
                if st.button("⚔️ Attack / Trigger Combat", use_container_width=True):
                    # Check room encounter table or spawn default goblins
                    enc_table = current_room.get("encounter_table", ["goblin_scout"])
                    monster_id = enc_table[0] if enc_table else "goblin_scout"
                    
                    # Start combat idempotently
                    combat_manager.start_combat([monster_id], player, world)
                    auto_save()
                    st.session_state["narrative_log"].append({
                        "role": "assistant",
                        "content": f"⚔️ Combat initiated against **{monster_id.replace('_', ' ').title()}**!"
                    })
                    st.rerun()


# ────────────────────────────────────────────────────────────────────────────────
# Main Routing
# ────────────────────────────────────────────────────────────────────────────────

def main():
    init_session_state()

    player = st.session_state.get("player_state")
    world = st.session_state.get("world_state")

    if not player or not world:
        render_landing_view()
    else:
        render_playing_view()


if __name__ == "__main__":
    main()
