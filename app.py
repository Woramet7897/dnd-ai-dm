"""
app.py — Phase 7 MVP Core Loop
Streamlit Interface for D&D 5e AI DM Engine.

Wires together all modules (character_creator, validation, state_manager,
dungeon_manager, combat_manager, memory_manager, llm_handler) into a working,
interactive Streamlit application.
"""

import json
import logging
import math
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
    if "action_suggestions" not in st.session_state:
        st.session_state["action_suggestions"] = list(llm_handler.DEFAULT_ACTION_SUGGESTIONS)


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


def load_game(char_name: str, client: Optional[Any] = None) -> bool:
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

            # Session Recap on Load (spec Section 14a / Phase 10 Part 1)
            recap_text = ""
            recap_key = f"recap_generated_{char_name}"
            if not st.session_state.get(recap_key):
                st.session_state[recap_key] = True
                mm = memory_manager.MemoryManager(char_name)
                major_ids = mm.get_all_major_lore_ids()
                if len(major_ids) >= 2:
                    major_col = mm._get_major_collection()
                    docs_res = major_col.get(where={"character": {"$eq": char_name}}, include=["documents"])
                    docs = docs_res.get("documents", [])
                    if len(docs) >= 2:
                        major_entries = [{"text": d, "type": "major"} for d in docs[-3:]]
                        res = llm_handler.generate_narrative_response(
                            user_input="Please provide a concise 'Previously, in your story...' recap paragraph summarizing our major past chapters.",
                            player_state=player,
                            world_state=world,
                            lore_entries=major_entries,
                            client=client,
                        )
                        recap_narrative = res.get("narrative", "")
                        if res.get("suggestions"):
                            st.session_state["action_suggestions"] = res["suggestions"]
                        if recap_narrative:
                            if not recap_narrative.startswith("Previously"):
                                recap_narrative = f"Previously, in your story...\n{recap_narrative}"
                            recap_text = f"\n\n📖 **Session Recap:**\n{recap_narrative}"

            st.session_state["narrative_log"].append({
                "role": "assistant",
                "content": f"Loaded save for **{char_name}**. You are currently in **{room_name}**.{recap_text}"
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
            
            race = st.selectbox("Race:", ["Human", "Elf", "Dwarf", "Halfling", "Tiefling", "Half-Orc", "Dragonborn"])
            cls_name = st.selectbox("Class:", ["Fighter", "Wizard", "Rogue", "Cleric", "Bard"])
            bg = st.selectbox("Background:", ["Folk Hero", "Acolyte", "Criminal", "Noble", "Sage", "Soldier"])

            st.markdown("##### Ability Scores (Point-Buy: 27 Points Total)")
            c_str = st.slider("STR", 8, 15, 14)
            c_dex = st.slider("DEX", 8, 15, 14)
            c_con = st.slider("CON", 8, 15, 12)
            c_int = st.slider("INT", 8, 15, 10)
            c_wis = st.slider("WIS", 8, 15, 10)
            c_cha = st.slider("CHA", 8, 15, 8)

            stats_alloc = {"STR": c_str, "DEX": c_dex, "CON": c_con, "INT": c_int, "WIS": c_wis, "CHA": c_cha}
            total_spent = sum(character_creator.POINT_BUY_COST.get(v, 0) for v in stats_alloc.values())
            remaining = character_creator.POINT_BUY_BUDGET - total_spent

            ok_pts, pts_msg = character_creator.validate_point_buy(stats_alloc)
            if not ok_pts:
                st.warning(f"⚠️ Point-Buy Allocation: {pts_msg}")
            else:
                st.success(f"✅ Point-Buy Valid: ใช้แต้มไป {total_spent} / 27 แต้ม (คงเหลือ {remaining} แต้ม)")

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

    # ── Inventory & Equipment Expander ──────────────────────────────────────────
    with st.sidebar.expander("🎒 Inventory & Equipment", expanded=False):
        if not inventory:
            st.info("Inventory is empty.")
        else:
            catalog = state_manager._get_item_catalog()
            for idx, item in enumerate(inventory):
                if not isinstance(item, dict):
                    continue
                item_id = item.get("item_id", "unknown")
                qty = item.get("quantity", 1)
                is_equipped = item.get("equipped", False)
                info = catalog.get(item_id, {})
                item_name = info.get("name", item_id.replace("_", " ").title())
                itype = info.get("type", "misc")
                islot = info.get("slot", "")
                slot_tag = f" [{islot}]" if islot else ""
                status_tag = " *(Equipped)*" if is_equipped else ""

                col_i1, col_i2 = st.columns([3, 2])
                with col_i1:
                    st.markdown(f"**{item_name}** x{qty}{slot_tag}{status_tag}")
                with col_i2:
                    if itype in ("wearable", "weapon"):
                        if is_equipped:
                            if st.button("Unequip", key=f"inv_unequip_{idx}_{item_id}", use_container_width=True):
                                state_manager.unequip_item(item_id, player)
                                auto_save()
                                st.rerun()
                        else:
                            if st.button("Equip", key=f"inv_equip_{idx}_{item_id}", use_container_width=True):
                                state_manager.equip_item(item_id, player)
                                auto_save()
                                st.rerun()
                    elif itype == "consumable":
                        if st.button("Use", key=f"inv_use_{idx}_{item_id}", use_container_width=True):
                            ok_u, msg_u, res_u = state_manager.use_consumable(item_id, player)
                            if ok_u:
                                healed = res_u.get("healed", 0)
                                st.toast(f"Used {item_name}! Restored {healed} HP.")
                                st.session_state["narrative_log"].append({
                                    "role": "assistant",
                                    "content": f"🧪 You drank a **{item_name}** and regained {healed} HP! (HP: {player['hp']['current']}/{player['hp']['max']})"
                                })
                            else:
                                st.error(msg_u)
                            auto_save()
                            st.rerun()

    # ── Quest Log Expander ───────────────────────────────────────────────────────
    with st.sidebar.expander("📜 Quest Log", expanded=False):
        q_log = world.get("quest_log", {})
        main_q = q_log.get("main", [])
        side_q = q_log.get("side", [])
        if not main_q and not side_q:
            st.info("No active quests in journal.")
        else:
            if main_q:
                st.markdown("##### 🌟 Main Quests")
                for q in main_q:
                    st.markdown(f"**{q.get('title')}** `({q.get('status', 'active')})`")
                    if q.get("description"):
                        st.caption(q["description"])
                    for obj in q.get("objectives", []):
                        chk = "✅" if obj.get("done") else "⬜"
                        st.markdown(f"- {chk} {obj.get('description', 'Objective')}")
            if side_q:
                st.markdown("##### 📌 Side Quests")
                for q in side_q:
                    st.markdown(f"**{q.get('title')}** `({q.get('status', 'active')})`")
                    if q.get("description"):
                        st.caption(q["description"])
                    for obj in q.get("objectives", []):
                        chk = "✅" if obj.get("done") else "⬜"
                        st.markdown(f"- {chk} {obj.get('description', 'Objective')}")

    # ── Spell Slots Display (if caster) ─────────────────────────────────────────
    spell_slots = player.get("spell_slots", {})
    known_spells = player.get("known_spells", [])
    if spell_slots or known_spells:
        with st.sidebar.expander("✨ Spellbook & Slots", expanded=False):
            if spell_slots:
                st.markdown("##### Spell Slots")
                for lvl, sinfo in spell_slots.items():
                    if isinstance(sinfo, dict):
                        st.markdown(f"Level {lvl}: **{sinfo.get('current', 0)} / {sinfo.get('max', 0)}**")
            if known_spells:
                st.markdown("##### Known Spells")
                sp_cat = state_manager._get_spell_catalog()
                for sp_id in known_spells:
                    sp_data = sp_cat.get(sp_id, {})
                    sp_name = sp_data.get("name", sp_id.replace("_", " ").title())
                    sp_lvl = sp_data.get("level", 0)
                    lvl_lbl = "Cantrip" if sp_lvl == 0 else f"Lvl {sp_lvl}"
                    st.markdown(f"- **{sp_name}** ({lvl_lbl})")

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
        companions = cs.get("companions", [])
        player_c = cs.get("player_combatant", {})

        # Display active surface & smoke status (Phase 11.1)
        room_surface = cs.get("room_surface", {})
        surf_type = room_surface.get("type")
        surf_dur = room_surface.get("duration", 0)
        if surf_type:
            st.info(f"🌊 **Active Surface: {surf_type.upper()}** ({surf_dur} round{'s' if surf_dur != 1 else ''} remaining)")
        if cs.get("smoke_active"):
            smoke_dur = cs.get("smoke_duration", 0)
            st.warning(f"💨 **Smoke Active**: Ranged attacks suffer disadvantage ({smoke_dur} round{'s' if smoke_dur != 1 else ''} remaining)")

        col_c1, col_c2 = st.columns([2, 1])

        with col_c1:
            st.markdown(f"##### Round {round_num} — Active Combatants")
            p_cur_hp = player_c.get("hp", {}).get("current", 0)
            p_max_hp = player_c.get("hp", {}).get("max", 10)
            st.markdown(f"🧑 **{player_c.get('name', 'Adventurer')}** (You) | HP: {p_cur_hp}/{p_max_hp} | AC: {player_c.get('ac', 10)}")

            for comp in companions:
                c_hp = comp.get("hp", {})
                st.markdown(f"🤝 **{comp.get('name', 'Companion')}** | HP: {c_hp.get('current', 0)}/{c_hp.get('max', 10)} | AC: {comp.get('ac', 10)}")

            st.markdown("---")
            for enemy in enemies:
                e_id = enemy.get("id", "enemy")
                e_name = enemy.get("name", e_id)
                e_hp = enemy.get("hp", {})
                cur_e_hp = e_hp.get("current", 0)
                max_e_hp = e_hp.get("max", 1)
                st.markdown(f"👹 **{e_name}** | HP: {cur_e_hp}/{max_e_hp} | AC: {enemy.get('ac', 10)}")

        living_enemies = [e for e in enemies if e.get("hp", {}).get("current", 0) > 0]
        player_alive = player_c.get("hp", {}).get("current", 0) > 0

        with col_c2:
            st.markdown("##### Combat Actions")
            btn_disabled = (cs.get("status") == "ended") or (cs.get("downed_outcome") is not None)

            selected_target = None
            selected_attack = None
            selected_spell_id = None
            action_type = "⚔️ Weapon Attack"

            known_spells = player.get("known_spells", [])
            sp_catalog = state_manager._get_spell_catalog()

            if player_alive and (living_enemies or known_spells):
                if known_spells:
                    action_type = st.radio("Action:", ["⚔️ Weapon Attack", "🪄 Cast Spell"], horizontal=True, key=f"c_act_type_{round_num}")

                if action_type == "⚔️ Weapon Attack" and living_enemies:
                    target_map = {f"{e.get('name', e.get('id'))} (HP: {e.get('hp', {}).get('current', 0)})": e for e in living_enemies}
                    chosen_target_label = st.selectbox("🎯 Target Enemy", list(target_map.keys()), key=f"target_sel_{round_num}")
                    selected_target = target_map[chosen_target_label]

                    attacks = player_c.get("attacks", [])
                    if not attacks:
                        attacks = [{"name": "Unarmed Strike", "attack_bonus": 2, "damage": "1+0", "damage_type": "bludgeoning", "ranged": False}]

                    def _fmt_atk(a):
                        ranged_tag = " [Ranged]" if a.get("ranged") else ""
                        return f"{a.get('name', 'Attack')} (+{a.get('attack_bonus', 0)}, {a.get('damage', '1d4')} {a.get('damage_type', '')}){ranged_tag}"

                    atk_map = {_fmt_atk(a): a for a in attacks}
                    chosen_atk_label = st.selectbox("⚔️ Weapon / Attack", list(atk_map.keys()), key=f"atk_sel_{round_num}")
                    selected_attack = atk_map[chosen_atk_label]
                elif action_type == "🪄 Cast Spell" and known_spells:
                    def _fmt_sp(sid):
                        sinfo = sp_catalog.get(sid, {})
                        slvl = sinfo.get("level", 0)
                        lvl_tag = "Cantrip" if slvl == 0 else f"Lvl {slvl}"
                        slots_left = ""
                        if slvl > 0:
                            s_cur = player.get("spell_slots", {}).get(str(slvl), {}).get("current", 0)
                            slots_left = f" [{s_cur} slot(s)]"
                        return f"{sinfo.get('name', sid)} ({lvl_tag}){slots_left}"

                    sp_map = {_fmt_sp(s): s for s in known_spells}
                    chosen_sp_label = st.selectbox("🪄 Select Spell", list(sp_map.keys()), key=f"spell_sel_{round_num}")
                    selected_spell_id = sp_map[chosen_sp_label]
                    chosen_sp_data = sp_catalog.get(selected_spell_id, {})
                    sp_type = chosen_sp_data.get("type", "attack_save")

                    # Target selection based on spell type
                    if sp_type == "heal":
                        heal_targets = {f"You ({player_c.get('name')}) [HP: {player_c.get('hp',{}).get('current')}/{player_c.get('hp',{}).get('max')}]": player_c}
                        for comp in companions:
                            heal_targets[f"🤝 {comp.get('name')} [HP: {comp.get('hp',{}).get('current')}/{comp.get('hp',{}).get('max')}]"] = comp
                        chosen_ht = st.selectbox("🎯 Target (Heal)", list(heal_targets.keys()), key=f"heal_target_sel_{round_num}")
                        selected_target = heal_targets[chosen_ht]
                    elif living_enemies:
                        target_map = {f"{e.get('name', e.get('id'))} (HP: {e.get('hp', {}).get('current', 0)})": e for e in living_enemies}
                        chosen_target_label = st.selectbox("🎯 Target Enemy", list(target_map.keys()), key=f"spell_target_sel_{round_num}")
                        selected_target = target_map[chosen_target_label]
                    else:
                        selected_target = player_c
            elif not player_alive:
                st.warning("⚠️ You are down! Rolling death saves...")

            button_label = "⚔️ Attack & End Round" if (player_alive and action_type == "⚔️ Weapon Attack") else ("🪄 Cast Spell & End Round" if player_alive else "⏳ Endure Round")
            if st.button(button_label, disabled=btn_disabled, use_container_width=True, type="primary"):
                # 1. Resolve player's attack or spell if conscious
                player_attack_result = None
                if player_alive and selected_target:
                    p_conds = combat_manager._get_condition_set(player_c)
                    if "stunned" in p_conds:
                        player_attack_result = {
                            "attacker_id": player_c.get("id", "player"),
                            "attacker_name": player_c.get("name", "Player"),
                            "skipped": True,
                            "reason": "stunned",
                        }
                    elif action_type == "🪄 Cast Spell" and selected_spell_id:
                        cast_res = state_manager.cast_spell(
                            caster=player_c,
                            target=selected_target,
                            spell_id=selected_spell_id,
                            character_state=player,
                            world_state=world,
                        )
                        if not cast_res.get("success"):
                            st.error(cast_res.get("reason", "Failed to cast spell."))
                            st.stop()
                        player_attack_result = cast_res
                    elif selected_attack:
                        player_attack_result = combat_manager.resolve_attack(
                            player_c,
                            selected_target,
                            selected_attack,
                            combat_state=cs,
                        )

                # 2. Resolve full combat round (Python math)
                res_round = combat_manager.resolve_round(
                    combat_state=cs,
                    player_attack_result=player_attack_result,
                    world_state=world,
                )
                combat_manager.sync_player_state(player_c)
                narration_block = res_round["narration_block"]
                outcome = res_round["combat_outcome"]

                # 3. EXACTLY ONE narrative LLM call for the entire round
                hist = st.session_state.get("history_buffer", [])
                narrative_res = llm_handler.generate_narrative_response(
                    user_input="",
                    player_state=player,
                    world_state=world,
                    history=hist,
                    round_result=narration_block,
                )

                narration_text = narrative_res.get("narrative", "")
                if narrative_res.get("suggestions"):
                    st.session_state["action_suggestions"] = narrative_res["suggestions"]
                full_combat_msg = f"{narration_block}\n\n*{narration_text}*"

                # 4. Append turn to narrative log & history buffer
                st.session_state["narrative_log"].append({"role": "assistant", "content": full_combat_msg})
                st.session_state["history_buffer"].append({"role": "assistant", "content": narration_text})

                # 5. Check combat end
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

            # Display active combat suggestions
            combat_suggestions = st.session_state.get("action_suggestions") or list(llm_handler.DEFAULT_ACTION_SUGGESTIONS)
            st.caption("💡 **Tactical Suggestions:**")
            for csug in combat_suggestions[:3]:
                st.markdown(f"- *{csug}*")

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

            # Contextual Action Suggestions (Sub-phase 14.1 / Module D)
            suggestions = st.session_state.get("action_suggestions") or list(llm_handler.DEFAULT_ACTION_SUGGESTIONS)
            st.caption("💡 **Suggested Actions:**")
            sug_cols = st.columns(3)
            clicked_suggestion = None
            for idx, sug_text in enumerate(suggestions[:3]):
                with sug_cols[idx]:
                    if st.button(f"✨ {sug_text}", key=f"sug_btn_{idx}", use_container_width=True):
                        clicked_suggestion = sug_text

            # Action Text Input Form
            with st.form("action_form", clear_on_submit=True):
                user_action = st.text_input("What do you want to do?", placeholder="e.g. Look around the room, talk to the merchant...")
                sub_act = st.form_submit_button("Submit Action", use_container_width=True)

            action_to_process = None
            if clicked_suggestion:
                action_to_process = clicked_suggestion.strip()
            elif sub_act and user_action.strip():
                action_to_process = user_action.strip()

            if action_to_process:
                st.session_state["narrative_log"].append({"role": "user", "content": action_to_process})

                # 1. Narrative Call
                hist = st.session_state.get("history_buffer", [])
                res = llm_handler.generate_narrative_response(
                    user_input=action_to_process,
                    player_state=player,
                    world_state=world,
                    history=hist,
                )
                narrative_text = res.get("narrative", "")
                if res.get("suggestions"):
                    st.session_state["action_suggestions"] = res["suggestions"]

                # 2. Extraction Call
                ext_res = llm_handler.generate_extraction_response(
                    narrative_text=narrative_text,
                    player_state=player,
                    world_state=world,
                )

                # 3. Apply state updates and events if present
                if ext_res:
                    if "state_updates" in ext_res and ext_res["state_updates"]:
                        state_manager.apply_state_updates(ext_res["state_updates"], player, world)
                    if "quest_updates" in ext_res and ext_res["quest_updates"]:
                        state_manager.apply_quest_updates(ext_res["quest_updates"], world)
                    if "combat_start" in ext_res and ext_res["combat_start"]:
                        c_enemies = ext_res["combat_start"].get("enemies", [])
                        if c_enemies:
                            combat_manager.start_combat(c_enemies, player, world)
                            st.session_state["narrative_log"].append({
                                "role": "assistant",
                                "content": f"⚔️ Combat initiated against **{', '.join(e.replace('_', ' ').title() for e in c_enemies)}**!"
                            })
                    if "world_updates" in ext_res and ext_res["world_updates"]:
                        w_up = ext_res["world_updates"]
                        if "new_location" in w_up and w_up["new_location"]:
                            ok_nl, reason_nl = dungeon_manager.register_new_location(w_up["new_location"], world)
                            if ok_nl:
                                nl_name = w_up["new_location"].get("name", "New Area")
                                st.session_state["narrative_log"].append({
                                    "role": "assistant",
                                    "content": f"🗺️ **New Area Discovered:** {nl_name}!"
                                })
                    if "requires_roll" in ext_res and ext_res["requires_roll"]:
                        rr = ext_res["requires_roll"]
                        stat = rr.get("stat", "STR")
                        diff = rr.get("difficulty", "medium")
                        chk_res = state_manager.resolve_check(stat, diff, player)
                        succ_str = "SUCCESS ✅" if chk_res["success"] else "FAILURE ❌"
                        st.session_state["narrative_log"].append({
                            "role": "assistant",
                            "content": f"🎲 **Automatic Check ({diff.capitalize()} {stat} DC {chk_res['dc']})**: Rolled {chk_res['roll']} + {chk_res['modifier']} = **{chk_res['total']}** → **{succ_str}**"
                        })

                st.session_state["narrative_log"].append({"role": "assistant", "content": narrative_text})
                st.session_state["history_buffer"].append({"role": "user", "content": action_to_process})
                st.session_state["history_buffer"].append({"role": "assistant", "content": narrative_text})

                auto_save()
                st.rerun()

            # Room Loot Interaction
            room_id = current_room.get("id")
            has_loot = bool(current_room.get("loot")) and (room_id not in world.get("collected_loot", []))
            if has_loot:
                if st.button("💎 Search / Loot Room", use_container_width=True, type="secondary"):
                    looted = dungeon_manager.get_room_loot(room_id, world)
                    if looted:
                        item_cat = state_manager._get_item_catalog()
                        looted_names = []
                        for lit in looted:
                            li_id = lit.get("item_id")
                            li_qty = lit.get("quantity", 1)
                            li_name = item_cat.get(li_id, {}).get("name", li_id)
                            looted_names.append(f"{li_name} (x{li_qty})")
                            pinv = player.setdefault("inventory", [])
                            existing = next((i for i in pinv if isinstance(i, dict) and i.get("item_id") == li_id), None)
                            if existing and existing.get("quantity") is not None:
                                existing["quantity"] = existing.get("quantity", 1) + li_qty
                            elif not existing:
                                pinv.append({"item_id": li_id, "equipped": False, "quantity": li_qty})
                        msg_loot = f"💎 You searched the room and found: **{', '.join(looted_names)}**!"
                        st.session_state["narrative_log"].append({"role": "assistant", "content": msg_loot})
                        auto_save()
                        st.rerun()

            # Manual Ability Check, Spells & Combat Launchers
            known_spells = player.get("known_spells", [])
            has_ooc_spells = bool(known_spells)

            col_counts = 3 if has_ooc_spells else 2
            cols_actions = st.columns(col_counts)

            with cols_actions[0]:
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

            with cols_actions[1]:
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

            if has_ooc_spells:
                with cols_actions[2]:
                    with st.popover("✨ Cast Spell"):
                        sp_cat = state_manager._get_spell_catalog()
                        def _fmt_ooc_sp(sid):
                            sinfo = sp_cat.get(sid, {})
                            slvl = sinfo.get("level", 0)
                            lvl_tag = "Cantrip" if slvl == 0 else f"Lvl {slvl}"
                            return f"{sinfo.get('name', sid)} ({lvl_tag})"

                        ooc_map = {_fmt_ooc_sp(s): s for s in known_spells}
                        chosen_ooc_lbl = st.selectbox("Spell:", list(ooc_map.keys()), key="ooc_sp_sel")
                        chosen_ooc_id = ooc_map[chosen_ooc_lbl]
                        ooc_data = sp_cat.get(chosen_ooc_id, {})

                        ooc_targets = {f"You ({player.get('name')})": player}
                        for comp in world.get("party", {}).get("companions", []):
                            ooc_targets[f"🤝 {comp.get('name')}"] = comp
                        chosen_ooc_tgt_lbl = st.selectbox("Target:", list(ooc_targets.keys()), key="ooc_tgt_sel")
                        chosen_ooc_tgt = ooc_targets[chosen_ooc_tgt_lbl]

                        if st.button("Cast", key="ooc_cast_btn", use_container_width=True):
                            c_res = state_manager.cast_spell(
                                caster=player,
                                target=chosen_ooc_tgt,
                                spell_id=chosen_ooc_id,
                                character_state=player,
                                world_state=world,
                            )
                            if not c_res.get("success"):
                                st.error(c_res.get("reason", "Failed to cast."))
                            else:
                                h_val = c_res.get("healed")
                                if h_val:
                                    st.toast(f"Cast {ooc_data.get('name')}! Restored {h_val} HP.")
                                    st.session_state["narrative_log"].append({
                                        "role": "assistant",
                                        "content": f"✨ You cast **{ooc_data.get('name')}** on **{chosen_ooc_tgt.get('name')}**, restoring {h_val} HP!"
                                    })
                                else:
                                    st.toast(f"Cast {ooc_data.get('name')}!")
                                    st.session_state["narrative_log"].append({
                                        "role": "assistant",
                                        "content": f"✨ You cast **{ooc_data.get('name')}**!"
                                    })
                                auto_save()
                                st.rerun()

            # Town Shops & Merchants UI
            if current_room.get("shops"):
                st.markdown("---")
                st.markdown("##### 🏪 Town Shops & Merchants")
                shop_cat = state_manager._get_shop_catalog()
                gt = world.get("game_time", {})
                cur_period = gt.get("period", "morning")

                for shop_id in current_room["shops"]:
                    s_data = shop_cat.get(shop_id)
                    if not s_data:
                        continue
                    s_name = s_data.get("name", shop_id.title())
                    s_desc = s_data.get("description", "")
                    open_periods = s_data.get("open_periods", [])
                    is_open = cur_period in open_periods

                    with st.expander(f"🏪 {s_name} ({'Open' if is_open else 'Closed'})", expanded=False):
                        st.caption(f"*{s_desc}*")
                        if not is_open:
                            st.warning(f"This shop is closed for the {cur_period}. Open during: {', '.join(open_periods)}.")
                        else:
                            buy_tab, sell_tab = st.tabs(["🛍️ Buy Goods", "💰 Sell Items"])
                            with buy_tab:
                                sell_items = s_data.get("sell_items", [])
                                item_cat = state_manager._get_item_catalog()
                                for s_item_id in sell_items:
                                    i_info = item_cat.get(s_item_id, {})
                                    i_name = i_info.get("name", s_item_id)
                                    i_val = i_info.get("value_gold", 0)
                                    cost = math.ceil(i_val * s_data.get("sell_multiplier", 1.0))
                                    col_b1, col_b2 = st.columns([3, 1])
                                    with col_b1:
                                        st.markdown(f"**{i_name}** — {cost} GP")
                                        if i_info.get("description"):
                                            st.caption(i_info["description"])
                                    with col_b2:
                                        can_afford = player.get("gold", 0) >= cost
                                        if st.button(f"Buy ({cost} GP)", key=f"buy_{shop_id}_{s_item_id}", disabled=not can_afford, use_container_width=True):
                                            ok_b, msg_b = state_manager.buy_item(s_item_id, shop_id, player, world)
                                            if ok_b:
                                                st.toast(msg_b)
                                                auto_save()
                                                st.rerun()
                                            else:
                                                st.error(msg_b)

                            with sell_tab:
                                inv = player.get("inventory", [])
                                non_equipped = [i for i in inv if isinstance(i, dict) and not i.get("equipped")]
                                if not non_equipped:
                                    st.info("No unequipped items available to sell.")
                                else:
                                    item_cat = state_manager._get_item_catalog()
                                    for s_item in non_equipped:
                                        s_iid = s_item.get("item_id")
                                        s_qty = s_item.get("quantity", 1)
                                        i_info = item_cat.get(s_iid, {})
                                        i_name = i_info.get("name", s_iid)
                                        i_val = i_info.get("value_gold", 0)
                                        gain = math.floor(i_val * s_data.get("buy_multiplier", 0.5))
                                        col_s1, col_s2 = st.columns([3, 1])
                                        with col_s1:
                                            st.markdown(f"**{i_name}** (x{s_qty}) — Sells for {gain} GP")
                                        with col_s2:
                                            if st.button(f"Sell (+{gain} GP)", key=f"sell_{shop_id}_{s_iid}", use_container_width=True):
                                                ok_s, msg_s = state_manager.sell_item(s_iid, shop_id, player, world)
                                                if ok_s:
                                                    st.toast(msg_s)
                                                    auto_save()
                                                    st.rerun()
                                                else:
                                                    st.error(msg_s)


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
