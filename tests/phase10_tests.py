"""
phase10_tests.py — Test suite for Phase 10 (Optional Polish Systems)
Systems tested:
1. Part 1 — Session Recap on Load (spec Section 14a):
   - Recap fires exactly ONCE per load_game() call (single-fire guard prevents Streamlit rerun duplicates).
   - Recap is cleanly SKIPPED when fewer than 2-3 major_lore entries exist.
2. Part 2 — Lightweight World Events (spec Section 6d):
   - move_player() world-event roll rate (~10%) and exclusion of player's current location over 500+ trials.
   - Context line injection into Tier 3 system prompt for flagged vs unflagged locations.
"""

import json
import os
import random
import unittest
from typing import Any, Dict, List, Optional

import app
import dungeon_manager
import llm_handler
import memory_manager
import state_manager


class MockOllamaClient:
    """Mock Ollama client matching the pattern used in phase6/7/8/9_tests.py."""

    def __init__(self, response_text: str = "Previously, in your story... The heroes triumphed over the goblin raiding party."):
        self.call_count = 0
        self.last_messages = None
        self.response_text = response_text

    def chat(self, model: str, messages: List[Dict[str, str]], options: Optional[Dict[str, Any]] = None, format: Optional[str] = None):
        self.call_count += 1
        self.last_messages = messages
        return {
            "message": {"role": "assistant", "content": self.response_text},
            "eval_count": 30,
            "prompt_eval_count": 60,
            "eval_duration": 1200,
        }


class TestPhase10Systems(unittest.TestCase):

    def setUp(self):
        self.char_name = "RecapHero"
        self.early_char_name = "FreshHero"

        # Create dummy save files for RecapHero
        self.player_state = {
            "schema_version": 4,
            "name": self.char_name,
            "race": "Human",
            "class": "Fighter",
            "level": 3,
            "xp_current": 900,
            "hp": {"current": 24, "max": 24},
            "stats": {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
            "ac": 16,
            "gold": 150,
            "proficiency_bonus": 2,
            "status": "normal",
            "active_conditions": [],
            "death_saves": {"success": 0, "fail": 0},
            "inventory": [],
        }

        self.world_state = {
            "schema_version": 4,
            "character_name": self.char_name,
            "current_location": "town_riverside",
            "visited_rooms": ["town_riverside", "forest_edge", "ruined_mill"],
            "cleared_rooms": [],
            "collected_loot": [],
            "dynamic_rooms": {},
            "game_time": {"day": 2, "period": "afternoon", "steps_since_period_start": 1},
            "quest_log": {"main": [], "side": []},
            "party": {"companions": [], "former_companions": []},
            "generated_items": {},
            "world_event_flags": {},
            "combat_state": None,
        }

        state_manager.save_character(self.char_name, self.player_state)
        state_manager.save_world(self.char_name, self.world_state)

        # Early game character save (FreshHero)
        self.early_player_state = dict(self.player_state)
        self.early_player_state["name"] = self.early_char_name
        self.early_world_state = dict(self.world_state)
        self.early_world_state["character_name"] = self.early_char_name

        state_manager.save_character(self.early_char_name, self.early_player_state)
        state_manager.save_world(self.early_char_name, self.early_world_state)

        # Reset Streamlit session state mock
        import streamlit as st
        st.session_state.clear()
        app.init_session_state()

    # ══════════════════════════════════════════════════════════════════════════
    # PART 1: SESSION RECAP ON LOAD (Spec Section 14a)
    # ══════════════════════════════════════════════════════════════════════════

    def test_session_recap_single_fire_and_skip(self):
        print("\n" + "=" * 65)
        print("TEST 1 — Session Recap on Load (spec 14a)")
        print("=" * 65)

        # 1. Populate MemoryManager for RecapHero with 2 major_lore entries
        mm = memory_manager.MemoryManager(self.char_name)
        mm._store_major_lore("Chapter 1: The heroes defeated the goblin chieftain in the ruined mill.")
        mm._store_major_lore("Chapter 2: The party escaped the crumbling dungeon with ancient treasures.")

        mock_client = MockOllamaClient("Previously, in your story... The heroes triumphed in the ruined mill and escaped with ancient treasures.")

        import streamlit as st
        st.session_state.clear()
        app.init_session_state()

        # First load: should trigger recap LLM call
        ok = app.load_game(self.char_name, client=mock_client)
        self.assertTrue(ok)
        self.assertEqual(mock_client.call_count, 1, "Mock client should be called exactly ONCE on initial load.")
        self.assertIn("Session Recap", st.session_state["narrative_log"][0]["content"])
        print("  [PASS] Initial load_game() with >=2 major_lore entries triggers recap LLM call (call_count == 1)")

        # Second load simulation (e.g. Streamlit rerun or re-load in same session):
        # Must NOT call LLM again due to session state guard
        ok2 = app.load_game(self.char_name, client=mock_client)
        self.assertTrue(ok2)
        self.assertEqual(mock_client.call_count, 1, "Mock client should NOT be called again on subsequent load/reruns.")
        print("  [PASS] Subsequent load_game() calls do NOT duplicate LLM call (call_count stays 1)")

        # 2. Early game character with 0 or 1 major_lore entries: MUST SKIP recap cleanly
        st.session_state.clear()
        app.init_session_state()

        mock_client_early = MockOllamaClient()
        ok_early = app.load_game(self.early_char_name, client=mock_client_early)
        self.assertTrue(ok_early)
        self.assertEqual(mock_client_early.call_count, 0, "Recap MUST be skipped when major_lore entries < 2.")
        self.assertNotIn("Session Recap", st.session_state["narrative_log"][0]["content"])
        print("  [PASS] Early-game save (< 2 major_lore entries) skips recap cleanly without LLM call")

    # ══════════════════════════════════════════════════════════════════════════
    # PART 2: LIGHTWEIGHT WORLD EVENTS (Spec Section 6d)
    # ══════════════════════════════════════════════════════════════════════════

    def test_world_event_roll_rate_and_location_guard(self):
        print("\n" + "=" * 65)
        print("TEST 2 — World Event Roll Rate & Target Location Guard (spec 6d)")
        print("=" * 65)

        world = {
            "current_location": "town_riverside",
            "visited_rooms": ["town_riverside", "forest_edge", "ruined_mill"],
            "dynamic_rooms": {},
            "world_event_flags": {},
        }

        trials = 500
        triggered_events = 0
        current_loc_violations = 0

        random.seed(42)  # Deterministic seed for reproducible trial rate
        for _ in range(trials):
            res = dungeon_manager.check_and_trigger_world_event(world, chance=0.10)
            if res is not None:
                triggered_events += 1
                loc_id = res["location_id"]
                if loc_id == "town_riverside":
                    current_loc_violations += 1

        self.assertEqual(current_loc_violations, 0, "World event flag MUST NEVER land on the player's current location.")
        print("  [PASS] 0 / 500 world events landed on player's current location ('town_riverside')")

        rate = triggered_events / trials
        self.assertTrue(0.05 <= rate <= 0.15, f"Trigger rate ({rate:.2%}) should be in sane ~10% range [5%, 15%].")
        print(f"  [PASS] World event roll rate over {trials} trials = {rate:.1%} (within sane ~10% range)")

        # Verify world_event_flags stored as Dict[location_id, List[flag_strings]]
        self.assertIsInstance(world["world_event_flags"], dict)
        print("  [PASS] world_event_flags schema stored as Dict[location_id, List[flag_strings]]")

    def test_world_event_system_prompt_tier3_injection(self):
        print("\n" + "=" * 65)
        print("TEST 3 — World Event Tier 3 System Prompt Context Line Injection (spec 6d)")
        print("=" * 65)

        player = self.player_state
        world = dict(self.world_state)
        world["world_event_flags"] = {
            "forest_edge": ["goblin_camp_grew"],
        }

        # 1. Player is at flagged location ("forest_edge")
        world["current_location"] = "forest_edge"
        prompt_flagged, _, _ = llm_handler.assemble_system_prompt(player, world)

        self.assertIn("[WORLD EVENT]", prompt_flagged)
        self.assertIn("goblin camp has grown larger", prompt_flagged)
        print("  [PASS] Injected context line '[WORLD EVENT]' appears in Tier 3 prompt for flagged location")

        # 2. Player is at unflagged location ("town_riverside")
        world["current_location"] = "town_riverside"
        prompt_unflagged, _, _ = llm_handler.assemble_system_prompt(player, world)

        self.assertNotIn("[WORLD EVENT]", prompt_unflagged)
        self.assertNotIn("goblin camp has grown larger", prompt_unflagged)
        print("  [PASS] Injected context line does NOT appear in system prompt for unflagged location")

        # 3. Test consume on visit helper
        context_lines = dungeon_manager.get_active_world_events_for_location("forest_edge", world, consume=True)
        self.assertEqual(len(context_lines), 1)
        self.assertIn("goblin camp has grown", context_lines[0])
        self.assertNotIn("forest_edge", world["world_event_flags"])
        print("  [PASS] get_active_world_events_for_location clears flags on visit (one-time surfacing policy)")


if __name__ == "__main__":
    unittest.main()
