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


def apply_custom_css():
    """Inject modern Dark Fantasy RPG custom CSS styling."""
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700;800&family=Inter:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }

    h1, h2, h3 {
        font-family: 'Cinzel', serif !important;
        letter-spacing: 0.02em;
    }

    /* Tabs Styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background-color: rgba(19, 27, 46, 0.7);
        padding: 6px;
        border-radius: 12px;
        border: 1px solid rgba(255, 255, 255, 0.08);
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px;
        padding: 8px 18px;
        color: #94a3b8;
        font-weight: 500;
        transition: all 0.2s ease;
    }
    .stTabs [data-baseweb="tab"]:hover {
        color: #f1f5f9;
        background-color: rgba(255, 255, 255, 0.04);
    }
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, rgba(225, 29, 72, 0.25) 0%, rgba(225, 29, 72, 0.1) 100%) !important;
        color: #ffffff !important;
        font-weight: 600 !important;
        border-bottom: 2px solid #e11d48 !important;
    }

    /* Metric Cards */
    div[data-testid="stMetric"] {
        background: rgba(19, 27, 46, 0.85);
        border: 1px solid rgba(255, 255, 255, 0.07);
        border-radius: 10px;
        padding: 10px 14px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
    }

    /* Primary buttons */
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #b91c1c 0%, #e11d48 100%);
        border: 1px solid rgba(255, 255, 255, 0.18);
        color: #ffffff;
        font-weight: 600;
        border-radius: 8px;
        box-shadow: 0 4px 14px rgba(225, 29, 72, 0.35);
        transition: all 0.2s ease;
    }
    .stButton > button[kind="primary"]:hover {
        box-shadow: 0 6px 20px rgba(225, 29, 72, 0.55);
        transform: translateY(-1px);
    }

    /* Chat message container */
    div[data-testid="stChatMessage"] {
        background: rgba(19, 27, 46, 0.55);
        border: 1px solid rgba(255, 255, 255, 0.05);
        border-radius: 12px;
        margin-bottom: 8px;
    }

    /* Sliders track */
    div[data-baseweb="slider"] {
        margin-top: 6px;
    }
    </style>
    """, unsafe_allow_html=True)


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
    if "active_camp_dialogue" not in st.session_state:
        st.session_state["active_camp_dialogue"] = None


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

CLASS_PRIORITY = {
    "Fighter": ["STR", "CON", "DEX", "WIS", "INT", "CHA"],
    "Wizard":  ["INT", "DEX", "CON", "WIS", "CHA", "STR"],
    "Rogue":   ["DEX", "CON", "INT", "CHA", "WIS", "STR"],
    "Cleric":  ["WIS", "CON", "STR", "DEX", "CHA", "INT"],
    "Bard":    ["CHA", "DEX", "CON", "WIS", "INT", "STR"],
}


def get_recommended_stats(cls_name: str, race_name: str) -> Dict[str, int]:
    """Return an optimal 27-point buy distribution tailored to class and racial stat bonuses."""
    races_cat = character_creator._load_catalog("races_catalog.json")
    race_key = race_name.lower().replace("-", "_").replace(" ", "_")
    r_bonuses = races_cat.get(race_key, {}).get("stat_bonuses", {})
    prio = CLASS_PRIORITY.get(cls_name, CLASS_PRIORITY["Fighter"])

    base = {s: 8 for s in character_creator.STAT_NAMES}
    values = [15, 14, 13, 12, 10, 8]
    for stat, val in zip(prio, values):
        base[stat] = val

    s1, s2 = prio[0], prio[1]
    b1, b2 = r_bonuses.get(s1, 0), r_bonuses.get(s2, 0)
    if b1 == 2 and b2 == 1:
        base[s1] = 14
        base[s2] = 15

    return base


def render_landing_view():
    apply_custom_css()

    st.markdown("""
    <div style="text-align: center; padding: 18px 0 12px 0;">
        <h1 style="font-size: 2.3rem; margin-bottom: 6px; background: linear-gradient(90deg, #f8fafc, #fda4af); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">
            ⚔️ D&D 5e AI DM Engine
        </h1>
        <p style="color: #94a3b8; font-size: 1.05rem; margin: 0;">
            A Tactical Single-Player D&D 5th Edition Adventure powered by AI
        </p>
    </div>
    """, unsafe_allow_html=True)

    saved_chars = list_saved_characters()

    # Center-aligned main card container
    col_pad_l, col_main, col_pad_r = st.columns([1, 10, 1])

    with col_main:
        tab_create, tab_load = st.tabs([
            "✨ สร้างตัวละครใหม่ (Create Character)",
            "📂 โหลดตัวละครเดิม (Load Save)",
        ])

        with tab_load:
            st.markdown("##### 📂 บันทึกตัวละครเดิมที่มีอยู่ในระบบ")
            if saved_chars:
                col_sel, col_btn = st.columns([3, 1])
                with col_sel:
                    selected_char = st.selectbox("เลือกตัวละคร:", saved_chars, label_visibility="collapsed")
                with col_btn:
                    if st.button("▶️ เข้าสู่เกม", use_container_width=True, type="primary"):
                        if load_game(selected_char):
                            st.rerun()
            else:
                st.info("ยังไม่มีข้อมูลตัวละครที่บันทึกไว้ — สลับไปที่แท็บ 'สร้างตัวละครใหม่' เพื่อเริ่มการผจญภัยได้เลย!")

        with tab_create:
            # Identity Section
            st.markdown("##### 👤 ข้อมูลอัตลักษณ์ตัวละคร (Identity & Vocation)")
            col_name, col_bg = st.columns(2)
            with col_name:
                char_name = st.text_input("ชื่อตัวละคร (Character Name):", value="Valeros", key="input_char_name")
            with col_bg:
                bg = st.selectbox(
                    "ภูมิหลัง (Background):",
                    ["Folk Hero", "Acolyte", "Criminal", "Noble", "Sage", "Soldier"],
                    key="select_bg",
                )

            col_r, col_c = st.columns(2)
            with col_r:
                race = st.selectbox(
                    "เผ่าพันธุ์ (Race):",
                    ["Human", "Elf", "Dwarf", "Halfling", "Tiefling", "Half-Orc", "Dragonborn"],
                    key="select_race",
                )
            with col_c:
                cls_name = st.selectbox(
                    "คลาส (Class):",
                    ["Fighter", "Wizard", "Rogue", "Cleric", "Bard"],
                    key="select_class",
                )

            st.markdown("---")
            st.markdown("##### ⚔️ จัดสรรค่าพลัง Point-Buy (27 แต้มรวม)")
            st.caption("สถิติแนะนำจะปรับเปลี่ยนให้เข้ากับคลาสและเผ่าโดยอัตโนมัติ — สามารถปรับเลื่อนสไลเดอร์เพื่อดูการคำนวณแต้มแบบสดๆ ได้")

            rec_stats = get_recommended_stats(cls_name, race)
            races_cat = character_creator._load_catalog("races_catalog.json")
            race_key = race.lower().replace("-", "_").replace(" ", "_")
            r_bonuses = races_cat.get(race_key, {}).get("stat_bonuses", {})

            # 2 Columns for Ability Sliders (Physical on Left, Mental on Right)
            col_stat_l, col_stat_r = st.columns(2)
            stats_alloc = {}

            with col_stat_l:
                st.markdown("###### 🗡️ ด้านกายภาพ (Physical Attributes)")
                for s in ["STR", "DEX", "CON"]:
                    bonus = r_bonuses.get(s, 0)
                    bonus_lbl = f" (+{bonus} {race})" if bonus > 0 else ""
                    default_val = rec_stats.get(s, 10)
                    val = st.slider(
                        f"{s}{bonus_lbl}",
                        min_value=8,
                        max_value=15,
                        value=default_val,
                        key=f"pb_{cls_name}_{race}_{s}",
                    )
                    stats_alloc[s] = val

            with col_stat_r:
                st.markdown("###### 🧠 ด้านสติปัญญาและมนตรา (Mental & Magic)")
                for s in ["INT", "WIS", "CHA"]:
                    bonus = r_bonuses.get(s, 0)
                    bonus_lbl = f" (+{bonus} {race})" if bonus > 0 else ""
                    default_val = rec_stats.get(s, 10)
                    val = st.slider(
                        f"{s}{bonus_lbl}",
                        min_value=8,
                        max_value=15,
                        value=default_val,
                        key=f"pb_{cls_name}_{race}_{s}",
                    )
                    stats_alloc[s] = val

            # Live Point-Buy Budget Calculation
            total_spent = sum(character_creator.POINT_BUY_COST.get(v, 0) for v in stats_alloc.values())
            remaining = character_creator.POINT_BUY_BUDGET - total_spent

            col_pb1, col_pb2, col_pb_status = st.columns([1, 1, 2])
            with col_pb1:
                st.metric("Point-Buy ที่ใช้", f"{total_spent} / 27")
            with col_pb2:
                st.metric("แต้มคงเหลือ", f"{remaining} แต้ม")
            with col_pb_status:
                ok_pts, pts_msg = character_creator.validate_point_buy(stats_alloc)
                if not ok_pts:
                    st.error(f"⚠️ {pts_msg}")
                elif remaining > 0:
                    st.info(f"💡 คงเหลืออีก {remaining} แต้ม — สามารถเพิ่มค่าพลังได้")
                else:
                    st.success("✅ Point-Buy Valid: ใช้ครบ 27 / 27 แต้มพอดี!")

            # Live Final Scores & Modifiers (6 Cards)
            st.markdown("###### 📊 คะแนนสุทธิพร้อมโบนัสเผ่า (Final Scores & Modifiers)")
            cols_summary = st.columns(6)
            for idx, s in enumerate(character_creator.STAT_NAMES):
                base_v = stats_alloc[s]
                r_b = r_bonuses.get(s, 0)
                final_v = base_v + r_b
                mod = character_creator.get_modifier(final_v)
                with cols_summary[idx]:
                    st.metric(
                        label=s,
                        value=f"{final_v}",
                        delta=f"{mod:+d}",
                    )

            st.markdown("---")
            col_reset, col_submit = st.columns([1, 2])
            with col_reset:
                if st.button("🔄 รีเซ็ตค่าแนะนำ (Reset)", use_container_width=True):
                    for s in character_creator.STAT_NAMES:
                        st.session_state[f"pb_{cls_name}_{race}_{s}"] = rec_stats[s]
                    st.rerun()

            with col_submit:
                submitted = st.button("⚔️ สร้างตัวละครและเริ่มการผจญภัย", type="primary", use_container_width=True)

            if submitted:
                if not char_name.strip():
                    st.error("กรุณาระบุชื่อตัวละคร")
                elif not ok_pts:
                    st.error(f"การจัดสรรแต้ม Point-Buy ไม่ถูกต้อง: {pts_msg}")
                else:
                    try:
                        player = character_creator.create_character(
                            name=char_name.strip(),
                            race=race,
                            class_name=cls_name,
                            background=bg,
                            stats=stats_alloc,
                        )
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
                            "content": f"Welcome to Riverside Village, **{char_name.strip()}**! Your journey begins here.",
                        }]

                        st.success(f"สร้างตัวละคร '{char_name.strip()}' สำเร็จ!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"เกิดข้อผิดพลาดในการสร้างตัวละคร: {e}")


# ────────────────────────────────────────────────────────────────────────────────
# Main Playing Interface & Sidebar
# ────────────────────────────────────────────────────────────────────────────────

def render_sidebar():
    """Render character sheet, stats, location, and time in the sidebar."""
    player = st.session_state.get("player_state")
    world = st.session_state.get("world_state")
    if not player or not world:
        return

    combat_active = world.get("combat_state") is not None

    # ── Pinned Always-Visible Top Section ──────────────────────────────────────
    st.sidebar.title(f"🛡️ {player.get('name', 'Adventurer')}")
    st.sidebar.caption(f"Level {player.get('level', 1)} {player.get('race', '')} {player.get('class_name', player.get('class', ''))}")

    # HP Progress Bar & Key Vitals
    hp_info = player.get("hp", {"current": 10, "max": 10})
    cur_hp = hp_info.get("current", 0)
    max_hp = hp_info.get("max", 10)
    hp_pct = max(0.0, min(1.0, cur_hp / max(1, max_hp)))

    col_hp, col_ac = st.sidebar.columns(2)
    with col_hp:
        st.metric("HP", f"{cur_hp} / {max_hp}")
    with col_ac:
        st.metric("AC", player.get("ac", 10))
    st.sidebar.progress(hp_pct)

    # Time & Location
    gt = world.get("game_time", {})
    day = gt.get("day", 1)
    period = gt.get("period", "morning").capitalize()
    current_room = dungeon_manager.get_current_room(world)
    room_name = current_room.get("name", "Unknown") if current_room else "Unknown"
    room_type = current_room.get("type", "wilderness").capitalize() if current_room else ""

    col_loc, col_time = st.sidebar.columns(2)
    with col_loc:
        st.caption(f"📍 **{room_name}** ({room_type})")
    with col_time:
        st.caption(f"⏳ **Day {day}**, {period}")

    # Active Conditions & Wanted Notice
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

    if player.get("is_wanted") or player.get("bounty", 0) > 0:
        bounty_val = player.get("bounty", 0)
        st.sidebar.error(f"🚨 **WANTED!** Bounty: **{bounty_val} GP**")
        if current_room and current_room.get("type") == "town":
            if st.sidebar.button(f"🏛️ Pay Off Bounty ({bounty_val} GP)", use_container_width=True):
                ok_b, msg_b = state_manager.pay_bounty(player)
                if ok_b:
                    st.toast(msg_b)
                    st.session_state["narrative_log"].append({
                        "role": "assistant",
                        "content": f"🏛️ {msg_b}"
                    })
                else:
                    st.sidebar.error(msg_b)
                auto_save()
                st.rerun()

    st.sidebar.markdown("---")

    # ── Sidebar Tabs ───────────────────────────────────────────────────────────
    with st.sidebar:
        tab_char, tab_inv, tab_quests, tab_spells = st.tabs([
            "👤 Character",
            "🎒 Inventory",
            "📜 Quests",
            "✨ Spells",
        ])

    # ── TAB 1: CHARACTER ───────────────────────────────────────────────────────
    with tab_char:
        # XP & Inspiration Metrics
        xp_cur = player.get("xp_current", 0)
        lvl = player.get("level", 1)
        next_xp = state_manager.LEVEL_THRESHOLDS.get(lvl + 1, "MAX")
        insp = state_manager.get_inspiration(player)
        max_insp = player.get("max_inspiration", state_manager.MAX_INSPIRATION)

        col_xp, col_insp = st.columns(2)
        with col_xp:
            st.metric("XP", f"{xp_cur} / {next_xp}")
        with col_insp:
            st.metric("Inspiration", f"{insp} / {max_insp}")

        # Ability Stats
        st.markdown("##### Ability Scores")
        stats = player.get("stats", {})
        col_s1, col_s2, col_s3 = st.columns(3)
        with col_s1:
            str_val = stats.get("STR", 10)
            st.metric("STR", str_val, f"{state_manager.get_modifier(str_val):+d}")
            int_val = stats.get("INT", 10)
            st.metric("INT", int_val, f"{state_manager.get_modifier(int_val):+d}")
        with col_s2:
            dex_val = stats.get("DEX", 10)
            st.metric("DEX", dex_val, f"{state_manager.get_modifier(dex_val):+d}")
            wis_val = stats.get("WIS", 10)
            st.metric("WIS", wis_val, f"{state_manager.get_modifier(wis_val):+d}")
        with col_s3:
            con_val = stats.get("CON", 10)
            st.metric("CON", con_val, f"{state_manager.get_modifier(con_val):+d}")
            cha_val = stats.get("CHA", 10)
            st.metric("CHA", cha_val, f"{state_manager.get_modifier(cha_val):+d}")

        # Party Companions & Approval
        party = world.get("party", {})
        companions = party.get("companions", [])
        if companions:
            st.markdown("---")
            st.markdown("##### 👥 Companions")
            for comp in companions:
                c_name = comp.get("name", comp.get("id", "Companion"))
                c_id = comp.get("id", c_name)
                c_app = state_manager.get_companion_approval(world, c_id)
                c_approval = c_app.get("approval", 50)
                col_c_info, col_c_btn = st.columns([2, 1])
                with col_c_info:
                    st.markdown(f"**{c_name}** ({c_approval}/100)")
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

            # Camp Companion Dialogue
            if not combat_active and dungeon_manager.is_camp_context(world):
                st.markdown("##### 🏕️ Camp Dialogue")
                active_dlg = st.session_state.get("active_camp_dialogue")
                if active_dlg:
                    c_name = active_dlg.get("companion_name", "Companion")
                    st.markdown(f"**{c_name}:** *\"{active_dlg.get('statement', '')}\"*")
                    col_ag, col_dis, col_neu = st.columns(3)
                    with col_ag:
                        if st.button("Agree (+5)", key="camp_resp_agree", use_container_width=True):
                            res = state_manager.respond_to_camp_dialogue(
                                active_dlg["companion_id"], "agree", world, active_dlg
                            )
                            st.session_state["narrative_log"].append({
                                "role": "assistant",
                                "content": f"🏕️ **Camp Conversation:** You agreed with {c_name}. ({c_name}'s approval increased to {res['new_approval']}/100)"
                            })
                            st.session_state["active_camp_dialogue"] = None
                            auto_save()
                            st.rerun()
                    with col_dis:
                        if st.button("Disagree (-5)", key="camp_resp_disagree", use_container_width=True):
                            res = state_manager.respond_to_camp_dialogue(
                                active_dlg["companion_id"], "disagree", world, active_dlg
                            )
                            st.session_state["narrative_log"].append({
                                "role": "assistant",
                                "content": f"🏕️ **Camp Conversation:** You disagreed with {c_name}. ({c_name}'s approval decreased to {res['new_approval']}/100)"
                            })
                            st.session_state["active_camp_dialogue"] = None
                            auto_save()
                            st.rerun()
                    with col_neu:
                        if st.button("Neutral (0)", key="camp_resp_neutral", use_container_width=True):
                            res = state_manager.respond_to_camp_dialogue(
                                active_dlg["companion_id"], "neutral", world, active_dlg
                            )
                            st.session_state["narrative_log"].append({
                                "role": "assistant",
                                "content": f"🏕️ **Camp Conversation:** You had a calm, neutral exchange with {c_name}. (Approval: {res['new_approval']}/100)"
                            })
                            st.session_state["active_camp_dialogue"] = None
                            auto_save()
                            st.rerun()
                else:
                    mm = memory_manager.MemoryManager(player.get("name", "Adventurer"))
                    for comp in companions:
                        cid = comp.get("id") or comp.get("name")
                        c_name = comp.get("name", cid)
                        c_app = state_manager.get_companion_approval(world, cid)
                        is_pending = c_app.get("camp_dialogue_pending", True)
                        if is_pending:
                            if st.button(f"💬 Talk with {c_name}", key=f"btn_camp_dlg_{cid}", use_container_width=True):
                                dlg = state_manager.generate_camp_dialogue(cid, mm, player, world)
                                if dlg:
                                    st.session_state["active_camp_dialogue"] = dlg
                                    st.rerun()
                        else:
                            st.caption(f"✓ Spoke with {c_name} this rest.")

    # ── TAB 2: INVENTORY ───────────────────────────────────────────────────────
    with tab_inv:
        inventory = player.get("inventory", [])
        ration_qty = sum(i.get("quantity", 1) for i in inventory if isinstance(i, dict) and i.get("item_id") == "trail_rations")

        col_g, col_r = st.columns(2)
        with col_g:
            st.metric("Gold", f"{player.get('gold', 0)} GP")
        with col_r:
            st.metric("Rations", ration_qty)

        if not inventory:
            st.info("Inventory is empty.")
        else:
            catalog = state_manager._get_item_catalog()
            bound_cursed_items = []
            for idx, item in enumerate(inventory):
                if not isinstance(item, dict):
                    continue
                item_id = item.get("item_id", "unknown")
                qty = item.get("quantity", 1)
                is_equipped = item.get("equipped", False)
                info = catalog.get(item_id, {})
                if not info and world:
                    info = world.get("generated_items", {}).get(item_id, {})

                is_id = item.get("identified", info.get("identified", True))
                is_bound = item.get("cannot_unequip", False)
                if is_bound:
                    bound_cursed_items.append((item_id, info.get("name", item_id)))

                if not is_id:
                    item_name = info.get("unidentified_name", item.get("unidentified_name", "Unidentified Item"))
                    id_tag = " ❓ *(Unidentified)*"
                else:
                    item_name = info.get("name", item.get("name", item_id.replace("_", " ").title()))
                    id_tag = ""

                itype = info.get("type", "misc")
                islot = info.get("slot", "")
                slot_tag = f" [{islot}]" if islot else ""
                status_tag = " *(Equipped)*" if is_equipped else ""
                curse_tag = " 🔒 *(Bound)*" if is_bound else ""
                fresh_tag = f" ⏳ ({item['freshness_days']}d)" if "freshness_days" in item and not is_bound and is_id else ""

                col_i1, col_i2 = st.columns([3, 2])
                with col_i1:
                    st.markdown(f"**{item_name}** x{qty}{slot_tag}{status_tag}{curse_tag}{id_tag}{fresh_tag}")
                with col_i2:
                    if not is_id:
                        if st.button("Identify", key=f"inv_id_{idx}_{item_id}", use_container_width=True):
                            chk = state_manager.resolve_check("INT", "medium", player, skill="Arcana")
                            ok_id = state_manager.identify_item(player, item_id, chk["total"], world)
                            if ok_id:
                                st.toast(f"Identified {item_name}!")
                                st.session_state["narrative_log"].append({
                                    "role": "assistant",
                                    "content": f"🔍 You studied the item (Arcana {chk['total']} vs DC {info.get('identify_dc', 13)}) and successfully identified it as **{info.get('name', item_id)}**!"
                                })
                            else:
                                st.toast(f"Failed to identify {item_name}.")
                                st.session_state["narrative_log"].append({
                                    "role": "assistant",
                                    "content": f"🔍 You examined the item (Arcana {chk['total']} vs DC {info.get('identify_dc', 13)}), but its true nature remains shrouded in mystery."
                                })
                            auto_save()
                            st.rerun()
                    elif itype in ("wearable", "weapon"):
                        if is_equipped:
                            if st.button("Unequip", key=f"inv_unequip_{idx}_{item_id}", use_container_width=True):
                                ok_un, msg_un = state_manager.unequip_item(item_id, player)
                                if not ok_un:
                                    st.error(msg_un)
                                    st.toast(msg_un)
                                auto_save()
                                st.rerun()
                        else:
                            if st.button("Equip", key=f"inv_equip_{idx}_{item_id}", use_container_width=True):
                                ok_eq, msg_eq = state_manager.equip_item(item_id, player, world)
                                if not ok_eq:
                                    st.error(msg_eq)
                                    st.toast(msg_eq)
                                auto_save()
                                st.rerun()
                    elif itype in ("consumable", "food"):
                        if st.button("Use", key=f"inv_use_{idx}_{item_id}", use_container_width=True):
                            ok_u, msg_u, res_u = state_manager.use_consumable(item_id, player)
                            if ok_u:
                                healed = res_u.get("healed", 0)
                                temp_hp = res_u.get("temp_hp", 0)
                                ex_cleared = res_u.get("exhaustion_cleared", False)
                                details = []
                                if healed: details.append(f"Restored {healed} HP")
                                if temp_hp: details.append(f"+{temp_hp} Temp HP")
                                if ex_cleared: details.append("Cleared Exhaustion")
                                summary = ", ".join(details) if details else "Consumed"
                                st.toast(f"Used {item_name}! {summary}")
                                st.session_state["narrative_log"].append({
                                    "role": "assistant",
                                    "content": f"🍲 You consumed **{item_name}**! {summary}. (HP: {player['hp']['current']}/{player['hp']['max']})"
                                })
                            else:
                                st.error(msg_u)
                            auto_save()
                            st.rerun()

            if bound_cursed_items:
                st.markdown("---")
                st.caption("🔒 You bear cursed items that cannot be removed.")
                is_town_or_safe = current_room and (current_room.get("is_safe") or current_room.get("type") == "town")
                if is_town_or_safe:
                    if st.button("⛪ Town Cleric: Remove Curse (50 GP)", use_container_width=True):
                        ok_c, msg_c = state_manager.pay_cleric_remove_curse(player, world_state=world)
                        if ok_c:
                            st.toast(msg_c)
                            st.session_state["narrative_log"].append({
                                "role": "assistant",
                                "content": f"⛪ {msg_c} (Gold remaining: {player.get('gold', 0)} GP)"
                            })
                        else:
                            st.error(msg_c)
                        auto_save()
                        st.rerun()

        # Camp Cooking inside Inventory tab
        if not combat_active:
            st.markdown("---")
            st.markdown("##### 🍳 Camp Cooking")
            food_candidates = [
                i for i in inventory
                if isinstance(i, dict) and (
                    i.get("raw_food") or i.get("type") in ("raw_food", "food", "rotten_food")
                    or "freshness_days" in i or i.get("item_id") in ("raw_food", "raw_meat", "wild_vegetables", "rotten_food", "trail_rations")
                )
            ]
            if len(food_candidates) == 0:
                st.caption("No cooking ingredients. Forage or hunt to find food.")
            else:
                f_names = [f"{it.get('name', it.get('item_id'))} (x{it.get('quantity', 1)})" for it in food_candidates]
                c_idx1 = st.selectbox("Ingredient 1", range(len(food_candidates)), format_func=lambda x: f_names[x], key="camp_cook_ing1")
                c_idx2 = st.selectbox("Ingredient 2", range(len(food_candidates)), format_func=lambda x: f_names[x], key="camp_cook_ing2")
                if st.button("🍲 Cook Meal (DC 12)", use_container_width=True, key="btn_camp_cook"):
                    res_c = state_manager.cook_meal(player, food_candidates[c_idx1], food_candidates[c_idx2])
                    if res_c.get("success"):
                        st.toast("Cooked Hearty Stew!")
                        st.session_state["narrative_log"].append({
                            "role": "assistant",
                            "content": f"🍲 **Camp Cooking:** {res_c['message']}"
                        })
                    elif res_c.get("burned"):
                        st.toast("Burned the meal!")
                        st.session_state["narrative_log"].append({
                            "role": "assistant",
                            "content": f"🔥 **Camp Cooking:** {res_c['message']}"
                        })
                    else:
                        st.error(res_c.get("message", "Cooking failed."))
                    auto_save()
                    st.rerun()

    # ── TAB 3: QUESTS ──────────────────────────────────────────────────────────
    with tab_quests:
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

    # ── TAB 4: SPELLS ──────────────────────────────────────────────────────────
    with tab_spells:
        spell_slots = player.get("spell_slots", {})
        known_spells = player.get("known_spells", [])
        if not spell_slots and not known_spells:
            st.info("No spellcasting ability or known spells.")
        else:
            if spell_slots:
                st.markdown("##### Spell Slots")
                for lvl_s, sinfo in spell_slots.items():
                    if isinstance(sinfo, dict):
                        st.markdown(f"Level {lvl_s}: **{sinfo.get('current', 0)} / {sinfo.get('max', 0)}**")
            if known_spells:
                st.markdown("##### Known Spells")
                sp_cat = state_manager._get_spell_catalog()
                for sp_id in known_spells:
                    sp_data = sp_cat.get(sp_id, {})
                    sp_name = sp_data.get("name", sp_id.replace("_", " ").title())
                    sp_lvl = sp_data.get("level", 0)
                    lvl_lbl = "Cantrip" if sp_lvl == 0 else f"Lvl {sp_lvl}"
                    st.markdown(f"- **{sp_name}** ({lvl_lbl})")

    # ── Pinned Bottom Controls (Rest & Save/Exit) ──────────────────────────────
    st.sidebar.markdown("---")
    if not combat_active:
        if st.sidebar.button("☕ Take Short Rest (1 hr)", use_container_width=True):
            state_manager.perform_short_rest(player, world)
            auto_save()
            st.session_state["narrative_log"].append({
                "role": "assistant",
                "content": "You take a short rest, catching your breath and checking your gear. Weapon actions recharged!"
            })
            st.rerun()

        if current_room and current_room.get("type") == "town":
            if st.sidebar.button("⛺ Take Long Rest (Town)", use_container_width=True):
                state_manager.long_rest(world, player)
                auto_save()
                st.session_state["narrative_log"].append({
                    "role": "assistant",
                    "content": f"You spend the night resting comfortably at Riverside Village. HP fully restored! Time is now **Day {world['game_time']['day']}, Morning**."
                })
                st.rerun()

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


def resolve_player_combat_action(
    action_type: str,
    player_c: Dict[str, Any],
    selected_target: Optional[Dict[str, Any]],
    selected_attack: Optional[Dict[str, Any]] = None,
    selected_spell_id: Optional[str] = None,
    selected_shove_type: Optional[str] = "push",
    selected_adj_target: Optional[Dict[str, Any]] = None,
    cs: Optional[Dict[str, Any]] = None,
    player: Optional[Dict[str, Any]] = None,
    world: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    Build player_attack_result and execute combat_manager.resolve_round().
    Standalone and importable for automated testing (protects against parameter swapping).

    Returns:
        Tuple of (player_attack_result, round_result_dict)
    """
    player_alive = player_c.get("hp", {}).get("current", 0) > 0 if player_c else False
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
        elif ("Weapon Action" in action_type or action_type == "weapon_action") and selected_attack:
            w_res = combat_manager.resolve_weapon_action(
                attacker=player_c,
                target=selected_target,
                attack=selected_attack,
                combat_state=cs,
                adjacent_target=selected_adj_target,
            )
            player_attack_result = w_res
        elif "Shove" in action_type or action_type == "shove":
            shove_res = combat_manager.resolve_shove(
                attacker=player_c,
                target=selected_target,
                combat_state=cs,
                shove_type=selected_shove_type or "push",
            )
            player_attack_result = shove_res
        elif ("Cast Spell" in action_type or action_type in ("spell", "cast_spell")) and selected_spell_id:
            cast_res = state_manager.cast_spell(
                caster=player_c,
                target=selected_target,
                spell_id=selected_spell_id,
                character_state=player or {},
                world_state=world,
            )
            if not cast_res.get("success"):
                return cast_res, {"error": cast_res.get("reason", "Failed to cast spell.")}
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
    return player_attack_result, res_round


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
        room_hazards = cs.get("room_hazards", [])
        if room_hazards:
            st.warning(f"⚠️ **Environmental Hazards: {', '.join(h.replace('_', ' ').title() for h in room_hazards)}**")

        col_c1, col_c2 = st.columns([2, 1])

        with col_c1:
            st.markdown(f"##### Round {round_num} — Active Combatants")
            p_cur_hp = max(0, player_c.get("hp", {}).get("current", 0))
            p_max_hp = max(1, player_c.get("hp", {}).get("max", 10))
            p_pct = max(0.0, min(1.0, p_cur_hp / p_max_hp))
            p_hg = " [⛰️ High Ground]" if player_c.get("has_high_ground") else ""
            col_p1, col_p2 = st.columns([1, 1])
            with col_p1:
                st.markdown(f"🧑 **{player_c.get('name', 'Adventurer')}** (You) | AC: {player_c.get('ac', 10)}{p_hg}")
            with col_p2:
                st.progress(p_pct, text=f"{p_cur_hp}/{p_max_hp} HP")

            for comp in companions:
                c_hp = comp.get("hp", {})
                c_cur = max(0, c_hp.get("current", 0))
                c_max = max(1, c_hp.get("max", 10))
                c_pct = max(0.0, min(1.0, c_cur / c_max))
                c_hg = " [⛰️ High Ground]" if comp.get("has_high_ground") else ""
                col_comp1, col_comp2 = st.columns([1, 1])
                with col_comp1:
                    st.markdown(f"🤝 **{comp.get('name', 'Companion')}** | AC: {comp.get('ac', 10)}{c_hg}")
                with col_comp2:
                    st.progress(c_pct, text=f"{c_cur}/{c_max} HP")

            st.markdown("---")
            st.markdown("###### Foes")
            if enemies:
                num_e_cols = min(len(enemies), 3) if len(enemies) > 1 else 1
                if num_e_cols > 1:
                    e_cols = st.columns(num_e_cols)
                    for idx, enemy in enumerate(enemies):
                        with e_cols[idx % num_e_cols]:
                            e_id = enemy.get("id", "enemy")
                            e_name = enemy.get("name", e_id)
                            e_hp = enemy.get("hp", {})
                            cur_e_hp = max(0, e_hp.get("current", 0))
                            max_e_hp = max(1, e_hp.get("max", 1))
                            pct = max(0.0, min(1.0, cur_e_hp / max_e_hp))
                            e_hg = " [⛰️ High Ground]" if enemy.get("has_high_ground") else ""
                            conds = f" *({', '.join(enemy.get('conditions', []))})*" if enemy.get("conditions") else ""
                            st.markdown(f"👹 **{e_name}**{e_hg}")
                            st.caption(f"AC: {enemy.get('ac', 10)}{conds}")
                            st.progress(pct, text=f"{cur_e_hp}/{max_e_hp} HP")
                else:
                    for enemy in enemies:
                        e_id = enemy.get("id", "enemy")
                        e_name = enemy.get("name", e_id)
                        e_hp = enemy.get("hp", {})
                        cur_e_hp = max(0, e_hp.get("current", 0))
                        max_e_hp = max(1, e_hp.get("max", 1))
                        pct = max(0.0, min(1.0, cur_e_hp / max_e_hp))
                        e_hg = " [⛰️ High Ground]" if enemy.get("has_high_ground") else ""
                        conds = f" *({', '.join(enemy.get('conditions', []))})*" if enemy.get("conditions") else ""
                        col_e1, col_e2 = st.columns([1, 1])
                        with col_e1:
                            st.markdown(f"👹 **{e_name}** | AC: {enemy.get('ac', 10)}{e_hg}{conds}")
                        with col_e2:
                            st.progress(pct, text=f"{cur_e_hp}/{max_e_hp} HP")

        living_enemies = [e for e in enemies if e.get("hp", {}).get("current", 0) > 0]
        player_alive = player_c.get("hp", {}).get("current", 0) > 0

        with col_c2:
            st.markdown("##### Combat Actions")
            btn_disabled = (cs.get("status") == "ended") or (cs.get("downed_outcome") is not None)

            selected_target = None
            selected_attack = None
            selected_spell_id = None
            selected_shove_type = "push"
            selected_adj_target = None
            action_type = "⚔️ Weapon Attack"

            known_spells = player.get("known_spells", [])
            sp_catalog = state_manager._get_spell_catalog()
            w_avail = player.get("weapon_actions_available", True) and player_c.get("weapon_actions_available", True)

            if player_alive and (living_enemies or known_spells):
                act_options = ["⚔️ Weapon Attack", "🫸 Shove"]
                if w_avail:
                    act_options.append("💥 Weapon Action")
                if known_spells:
                    act_options.append("🪄 Cast Spell")
                action_type = st.radio("Action:", act_options, horizontal=True, key=f"c_act_type_{round_num}")

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
                elif action_type == "💥 Weapon Action" and living_enemies:
                    target_map = {f"{e.get('name', e.get('id'))} (HP: {e.get('hp', {}).get('current', 0)})": e for e in living_enemies}
                    chosen_target_label = st.selectbox("🎯 Target Enemy", list(target_map.keys()), key=f"wact_target_sel_{round_num}")
                    selected_target = target_map[chosen_target_label]

                    attacks = player_c.get("attacks", [])
                    if not attacks:
                        attacks = [{"name": "Unarmed Strike", "attack_bonus": 2, "damage": "1+0", "damage_type": "bludgeoning", "ranged": False}]

                    def _fmt_wact(a):
                        winfo = combat_manager.get_weapon_action_info(a)
                        return f"{a.get('name', 'Attack')} → {winfo['name']} ({winfo['damage_type'].title()})"

                    wact_map = {_fmt_wact(a): a for a in attacks}
                    chosen_wact_label = st.selectbox("💥 Weapon Action", list(wact_map.keys()), key=f"wact_sel_{round_num}")
                    selected_attack = wact_map[chosen_wact_label]

                    w_info = combat_manager.get_weapon_action_info(selected_attack)
                    st.info(f"💥 **{w_info['name']}**: {w_info['description']}")

                    if w_info["name"] == "Cleave":
                        other_enemies = [e for e in living_enemies if e.get("id") != selected_target.get("id")]
                        if other_enemies:
                            adj_map = {f"{e.get('name', e.get('id'))} (HP: {e.get('hp', {}).get('current', 0)})": e for e in other_enemies}
                            chosen_adj_label = st.selectbox("🎯 Adjacent Enemy (Cleave)", list(adj_map.keys()), key=f"cleave_adj_{round_num}")
                            selected_adj_target = adj_map[chosen_adj_label]
                elif action_type == "🫸 Shove" and living_enemies:
                    target_map = {f"{e.get('name', e.get('id'))} (HP: {e.get('hp', {}).get('current', 0)})": e for e in living_enemies}
                    chosen_target_label = st.selectbox("🎯 Target Enemy to Shove", list(target_map.keys()), key=f"shove_target_sel_{round_num}")
                    selected_target = target_map[chosen_target_label]

                    shove_goal = st.radio("Shove Goal:", ["Push 5ft", "Knock Prone"], horizontal=True, key=f"shove_goal_{round_num}")
                    selected_shove_type = "prone" if shove_goal == "Knock Prone" else "push"
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

            if not player_alive:
                button_label = "⏳ Endure Round"
            elif action_type == "⚔️ Weapon Attack":
                button_label = "⚔️ Attack & End Round"
            elif action_type == "💥 Weapon Action":
                w_info_btn = combat_manager.get_weapon_action_info(selected_attack) if selected_attack else {"name": "Weapon Action"}
                button_label = f"💥 {w_info_btn['name']} & End Round"
            elif action_type == "🫸 Shove":
                button_label = "🫸 Shove & End Round"
            elif action_type == "🪄 Cast Spell":
                button_label = "🪄 Cast Spell & End Round"
            else:
                button_label = "⚔️ Attack & End Round"

            if st.button(button_label, disabled=btn_disabled, use_container_width=True, type="primary"):
                # 1. Resolve player's combat action & round
                player_attack_result, res_round = resolve_player_combat_action(
                    action_type=action_type,
                    player_c=player_c,
                    selected_target=selected_target,
                    selected_attack=selected_attack,
                    selected_spell_id=selected_spell_id,
                    selected_shove_type=selected_shove_type,
                    selected_adj_target=selected_adj_target,
                    cs=cs,
                    player=player,
                    world=world,
                )
                if res_round.get("error"):
                    st.error(res_round["error"])
                    st.stop()

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
                cap_reason = player.get("captivity_reason", "combat_defeat")
                if cap_reason == "crime":
                    st.markdown("##### ⚖️ Imprisoned in Town Jail")
                    days_left = player.get("prison_days_left", 1)
                    st.warning(f"You are locked in a jail cell! Remaining sentence: **{days_left} day(s)**.")
                    confiscated = player.get("confiscated_items", [])
                    if confiscated:
                        st.caption(f"🔒 {len(confiscated)} personal item(s) locked in the evidence chest.")

                    col_srv, col_brk = st.columns(2)
                    with col_srv:
                        if st.button("⚖️ Serve Time", use_container_width=True, type="primary"):
                            res_s = state_manager.serve_prison_time(player, world)
                            st.toast("Sentence completed!")
                            st.session_state["narrative_log"].append({
                                "role": "assistant",
                                "content": f"⚖️ **Sentence Served:** {res_s['message']}"
                            })
                            auto_save()
                            st.rerun()
                    with col_brk:
                        if st.button("🔓 Lockpick Escape", use_container_width=True):
                            res_e = state_manager.attempt_lockpick_escape(player, world)
                            if res_e["success"]:
                                st.toast("Escape Successful!")
                                st.session_state["narrative_log"].append({
                                    "role": "assistant",
                                    "content": f"🎉 **Prison Break:** {res_e['message']}"
                                })
                            else:
                                st.error(res_e["message"])
                                st.session_state["narrative_log"].append({
                                    "role": "assistant",
                                    "content": f"❌ **Prison Break Failed:** {res_e['message']}"
                                })
                            auto_save()
                            st.rerun()
                else:
                    st.markdown("##### 🔒 Captive — Attempt Escape")
                    st.info("You are held captive! Normal movement is disabled until you escape.")
                    if st.button("🔓 Attempt Escape", use_container_width=True, type="primary"):
                        # One resolve_check against hard difficulty (DC 16)
                        esc_res = state_manager.resolve_check("DEX", "hard", player)
                        
                        # Advance time by 1 step (costs a turn)
                        time_res = dungeon_manager.advance_time(world, steps=1, character_state=player)
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

            # Hidden Cache Search (Module C §2 Rumor)
            has_hidden_cache = bool(current_room.get("hidden_cache")) and (f"{room_id}_hidden_cache" not in world.get("collected_loot", []))
            if has_hidden_cache:
                if st.button("🔍 Search Hidden Cache (Rumor)", use_container_width=True, type="secondary"):
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
                        msg_loot = f"🔍 You investigated the rumors and uncovered a secret cache: **{', '.join(looted_names)}**!"
                        st.session_state["narrative_log"].append({"role": "assistant", "content": msg_loot})
                        auto_save()
                        st.rerun()

            # Manual Ability Check, Spells & Combat Launchers
            known_spells = player.get("known_spells", [])
            has_ooc_spells = bool(known_spells)
            room_npcs = current_room.get("npcs", [])

            col_counts = 2
            if has_ooc_spells:
                col_counts += 1
            if room_npcs:
                col_counts += 1
            cols_actions = st.columns(col_counts)

            c_idx = 0
            with cols_actions[c_idx]:
                c_idx += 1
                with st.popover("🎲 Make Ability Check"):
                    chk_stat = st.selectbox("Stat:", ["STR", "DEX", "CON", "INT", "WIS", "CHA"])
                    chk_diff = st.selectbox("Difficulty:", ["easy", "medium", "hard", "very_hard"])
                    chk_skill = st.text_input("Skill (optional, e.g. Arcana, Stealth, Athletics):", "")
                    insp_count = state_manager.get_inspiration(player)
                    use_insp = st.checkbox(f"Use Inspiration (Reroll, have {insp_count})", disabled=(insp_count <= 0))
                    if st.button("Roll d20", use_container_width=True):
                        chk_res = state_manager.resolve_check(
                            chk_stat,
                            chk_diff,
                            player,
                            skill=chk_skill.strip() if chk_skill else None,
                            use_inspiration=use_insp,
                        )
                        succ_str = "SUCCESS ✅" if chk_res["success"] else "FAILURE ❌"
                        insp_tag = " (Inspiration Used ✨)" if chk_res.get("inspiration_spent") else ""
                        skill_tag = f" [{chk_res['skill']}]" if chk_res.get("skill") else ""
                        roll_msg = (
                            f"🎲 **{chk_stat}{skill_tag} Check ({chk_diff.capitalize()} DC {chk_res['dc']})**: "
                            f"Rolled {chk_res['roll']} + mod {chk_res['modifier']} = **{chk_res['total']}** → **{succ_str}**{insp_tag}"
                        )
                        st.session_state["narrative_log"].append({"role": "assistant", "content": roll_msg})
                        auto_save()
                        st.rerun()

            if room_npcs:
                with cols_actions[c_idx]:
                    c_idx += 1
                    with st.popover("🦹 Pickpocket"):
                        st.caption("Attempt to steal from an NPC using Sleight of Hand.")
                        target_npc = st.selectbox("Target NPC", room_npcs, key="pp_target_npc")
                        st_item = st.selectbox("Item to Steal", ["gold", "healing_potion", "torch", "rope", "dagger"], key="pp_item")
                        if st.button("🧤 Attempt Steal", use_container_width=True, key="btn_pp_steal"):
                            pp_res = state_manager.attempt_pickpocket(player, target_npc, st_item, world_state=world)
                            if pp_res["success"]:
                                st.toast(pp_res["message"])
                                st.session_state["narrative_log"].append({
                                    "role": "assistant",
                                    "content": f"🦹 **Pickpocket:** {pp_res['message']} (Rolled {pp_res['total']} vs DC {pp_res['dc']})"
                                })
                            else:
                                st.error(pp_res["message"])
                                st.session_state["narrative_log"].append({
                                    "role": "assistant",
                                    "content": f"🚨 **Caught!** {pp_res['message']} (Rolled {pp_res['total']} vs DC {pp_res['dc']})"
                                })
                            auto_save()
                            st.rerun()

            with cols_actions[c_idx]:
                c_idx += 1
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
                with cols_actions[c_idx]:
                    c_idx += 1
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

            # Tavern Notice Board UI (Module C §2 / Phase 13.5)
            if current_room.get("id") in ("town_riverside", "tavern") or current_room.get("notice_board") is not None or "innkeeper_mira" in current_room.get("npcs", []):
                st.markdown("---")
                st.markdown("##### 📜 Tavern Notice Board")
                nb_entries = dungeon_manager.get_notice_board(world)
                if not nb_entries:
                    st.caption("No notices currently posted. Check back in a few days!")
                else:
                    for entry in nb_entries:
                        e_id = entry.get("id")
                        e_type = entry.get("type", "quest")
                        e_title = entry.get("title", "Notice")
                        e_desc = entry.get("description", "")
                        e_status = entry.get("status", "available")
                        icon = "🗡️" if e_type == "bounty" else ("📦" if e_type == "delivery" else "🗺️")

                        with st.expander(f"{icon} {e_title} [{e_status.capitalize()}]", expanded=(e_status == "available")):
                            st.write(e_desc)
                            if entry.get("reward_gold"):
                                st.caption(f"💰 Reward: **{entry['reward_gold']} GP**")
                            if entry.get("target_room_name"):
                                st.caption(f"📍 Location: **{entry['target_room_name']}**")

                            if e_status == "available":
                                if st.button(f"📜 Accept Quest", key=f"acc_nb_{e_id}", use_container_width=True):
                                    res_acc = dungeon_manager.accept_notice_board_quest(world, e_id)
                                    st.toast(res_acc["message"])
                                    st.session_state["narrative_log"].append({
                                        "role": "assistant",
                                        "content": f"📜 **Accepted Quest from Notice Board:** {e_title}"
                                    })
                                    auto_save()
                                    st.rerun()
                            elif e_status == "accepted":
                                if e_type == "delivery":
                                    tgt_item = entry.get("target_item")
                                    has_it = any(isinstance(it, dict) and it.get("item_id") == tgt_item for it in player.get("inventory", []))
                                    if st.button(f"📦 Complete Delivery ({entry.get('reward_gold')} GP)", key=f"comp_nb_{e_id}", disabled=not has_it, use_container_width=True):
                                        res_cmp = dungeon_manager.complete_notice_board_quest(world, e_id, player)
                                        if res_cmp["success"]:
                                            st.toast(res_cmp["message"])
                                            st.session_state["narrative_log"].append({
                                                "role": "assistant",
                                                "content": f"🎉 **Quest Completed:** {res_cmp['message']}"
                                            })
                                            auto_save()
                                            st.rerun()
                                        else:
                                            st.error(res_cmp["message"])
                                else:
                                    st.info("Status: Quest active in your quest log! Fulfill the objectives to earn the bounty.")
                            elif e_status == "completed":
                                st.success("✅ Quest Completed!")


# ────────────────────────────────────────────────────────────────────────────────
# Main Routing
# ────────────────────────────────────────────────────────────────────────────────

def main():
    init_session_state()
    apply_custom_css()

    player = st.session_state.get("player_state")
    world = st.session_state.get("world_state")

    if not player or not world:
        render_landing_view()
    else:
        render_playing_view()


if __name__ == "__main__":
    main()
