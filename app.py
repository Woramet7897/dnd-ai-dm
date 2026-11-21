"""
app.py — Streamlit Entrypoint & Facade for D&D 5e AI DM Engine.

Routes between the Character Creation / Load Game screen (`ui.landing_view`)
and the Main Gameplay screen (`ui.exploration_view`), while re-exporting
shared helpers for backward compatibility with integration test suites.
"""

import json
import logging
import math
import os
import random
import re
import time
import streamlit as st

import character_creator
import check_manager
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

# Re-export UI modules and helper functions for backward compatibility & tests
from ui.styles import apply_custom_css
from ui.session import (
    init_session_state,
    get_session_memory_manager,
    auto_save,
    list_saved_characters,
    get_current_active_model,
    get_display_spell_catalog,
    resolve_turn_extraction,
    load_game,
    get_ration_count,
    get_active_quest_count,
    get_known_spell_count,
    get_player_currency,
)
from ui.landing_view import (
    CLASS_FLAVOR,
    RACE_FLAVOR,
    CLASS_PRIORITY,
    get_recommended_stats,
    render_landing_view,
)
from ui.dialogs import (
    show_character_dialog,
    show_inventory_dialog,
    show_quests_dialog,
    show_spells_dialog,
    _maybe_rest_ambush,
    show_camp_dialog,
)
from ui.sidebar import render_sidebar
from ui.combat_view import (
    _select_enemy_target,
    resolve_player_combat_action,
    render_combat_panel,
)
from ui.town_view import (
    get_sell_button_key,
    get_sell_button_keys,
    render_town_tab,
)
from ui.exploration_view import (
    apply_escape_success,
    attempt_escape,
    resolve_inspiration_reroll,
    render_playing_view,
)


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
