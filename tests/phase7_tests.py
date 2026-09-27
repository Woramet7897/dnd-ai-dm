"""
phase7_tests.py — Test suite for Phase 7 (app.py core loop + time & supply system)
Tests:
1. Deterministic time advancement via advance_time().
2. Rations & exhausted condition handling outside town vs in town.
3. Long rest time jump, HP restore, and exhausted clearing.
4. Shop hours gating (open_periods array in shop_catalog.json).
5. One-narrative-call-per-round combat invariant.
"""

import json
import os
import unittest
from unittest.mock import MagicMock

import combat_manager
import dungeon_manager
import llm_handler
import state_manager


class TestPhase7TimeAndSupply(unittest.TestCase):

    def setUp(self):
        self.world_state = {
            "schema_version": 4,
            "character_name": "TestHero",
            "current_location": "town_riverside",
            "visited_rooms": ["town_riverside"],
            "cleared_rooms": [],
            "collected_loot": [],
            "dynamic_rooms": {},
            "game_time": {"day": 1, "period": "morning", "steps_since_period_start": 0},
            "quest_log": {"main": [], "side": []},
            "combat_state": None,
        }

        self.character_state = {
            "schema_version": 4,
            "name": "TestHero",
            "race": "Human",
            "class_name": "Fighter",
            "level": 1,
            "stats": {"STR": 16, "DEX": 14, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
            "hp": {"current": 12, "max": 12},
            "ac": 16,
            "gold": 50,
            "proficiency_bonus": 2,
            "proficient_skills": ["Athletics"],
            "proficient_saves": ["STR", "CON"],
            "active_conditions": [],
            "inventory": [
                {"item_id": "trail_rations", "equipped": False, "quantity": 2},
                {"item_id": "longsword", "equipped": True, "quantity": 1},
            ],
        }

    def test_advance_time(self):
        # Initial: day 1, morning, steps 0
        res = dungeon_manager.advance_time(self.world_state, steps=1)
        self.assertEqual(res["steps_since_period_start"], 1)
        self.assertFalse(res["period_changed"])

        # 3 more steps -> total 4 steps -> period change to afternoon
        for _ in range(3):
            res = dungeon_manager.advance_time(self.world_state, steps=1)
        self.assertEqual(res["period"], "afternoon")
        self.assertEqual(res["steps_since_period_start"], 0)
        self.assertTrue(res["period_changed"])

        # Advance through evening, night -> wrap to next day morning
        dungeon_manager.advance_time(self.world_state, steps=4)  # -> evening
        self.assertEqual(self.world_state["game_time"]["period"], "evening")
        dungeon_manager.advance_time(self.world_state, steps=4)  # -> night
        self.assertEqual(self.world_state["game_time"]["period"], "night")
        dungeon_manager.advance_time(self.world_state, steps=4)  # -> morning of day 2
        self.assertEqual(self.world_state["game_time"]["period"], "morning")
        self.assertEqual(self.world_state["game_time"]["day"], 2)

    def test_rations_consumed_outside_town(self):
        # Move to wilderness location
        self.world_state["current_location"] = "forest_edge"

        # Initially 2 rations
        self.assertEqual(self.character_state["inventory"][0]["quantity"], 2)

        # Trigger period change
        ok, msg = state_manager.handle_period_change(self.world_state, self.character_state)
        self.assertTrue(ok)
        self.assertEqual(self.character_state["inventory"][0]["quantity"], 1)
        self.assertNotIn("exhausted", self.character_state["active_conditions"])

        # Trigger another period change -> 0 rations left
        ok, msg = state_manager.handle_period_change(self.world_state, self.character_state)
        self.assertTrue(ok)
        self.assertEqual(len(self.character_state["inventory"]), 1)  # rations removed

        # Trigger another period change with NO rations -> applied exhausted condition
        ok, msg = state_manager.handle_period_change(self.world_state, self.character_state)
        self.assertFalse(ok)
        self.assertIn("exhausted", self.character_state["active_conditions"])

    def test_rations_not_consumed_in_town(self):
        self.world_state["current_location"] = "town_riverside"
        ok, msg = state_manager.handle_period_change(self.world_state, self.character_state)
        self.assertTrue(ok)
        # Rations count unchanged (still 2)
        self.assertEqual(self.character_state["inventory"][0]["quantity"], 2)

    def test_long_rest(self):
        # Set damaged HP and exhausted condition
        self.character_state["hp"]["current"] = 4
        self.character_state["active_conditions"].append("exhausted")
        self.world_state["game_time"] = {"day": 1, "period": "night", "steps_since_period_start": 2}

        state_manager.long_rest(self.world_state, self.character_state)

        self.assertEqual(self.character_state["hp"]["current"], 12)
        self.assertNotIn("exhausted", self.character_state["active_conditions"])
        self.assertEqual(self.world_state["game_time"]["day"], 2)
        self.assertEqual(self.world_state["game_time"]["period"], "morning")
        self.assertEqual(self.world_state["game_time"]["steps_since_period_start"], 0)

    def test_shop_hours_gating(self):
        # Ironforge Smithy is open in morning and afternoon only
        self.world_state["game_time"]["period"] = "morning"
        ok, msg = state_manager.buy_item("shield", "blacksmith", self.character_state, self.world_state)
        self.assertTrue(ok, f"Failed buy in morning: {msg}")

        # Set time to night -> shop closed
        self.world_state["game_time"]["period"] = "night"
        ok_closed, msg_closed = state_manager.buy_item("mace", "blacksmith", self.character_state, self.world_state)
        self.assertFalse(ok_closed)
        self.assertIn("closed", msg_closed.lower())

    def test_single_narrative_call_per_combat_round_invariant(self):
        # Start combat
        combat_manager.start_combat(["goblin_scout"], self.character_state, self.world_state)
        cs = self.world_state["combat_state"]

        mock_client = MagicMock()
        mock_client.chat.return_value = {
            "message": {"content": "The goblin swipes fiercely, but your blade turns the attack aside."}
        }

        # Resolve single round
        res_round = combat_manager.resolve_round(cs, world_state=self.world_state)
        narration_block = res_round["narration_block"]
        outcome = res_round["combat_outcome"]

        res = llm_handler.generate_narrative_response(
            user_input="",
            player_state=self.character_state,
            world_state=self.world_state,
            round_result=narration_block,
            client=mock_client,
        )

        # Assert mock LLM client was called EXACTLY ONCE for the whole combat round
        self.assertEqual(mock_client.chat.call_count, 1)
        self.assertIn("goblin", res["narrative"].lower())


if __name__ == "__main__":
    unittest.main()
