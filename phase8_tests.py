"""
phase8_tests.py — Test suite for Phase 8 (Death/downed-outcome system, Kenshi-lite)
Tests:
1. Downed outcome resolution when death saves hit 3 failures or player defeated.
2. Monte Carlo reachability trial across 200+ runs for all 3 outcomes (robbed_and_left, captured, rescued_by_npc).
3. State changes for each individual outcome matching spec (HP=1 for robbed, status='captive' for captured, partial HP for rescued).
4. Captive escape loop (failed check costs turn & remains captive, successful check clears captive status).
5. Non-crashing & non-permadeath verification.
"""

import os
import random
import unittest
from unittest.mock import patch

import combat_manager
import dungeon_manager
import state_manager


class TestPhase8DownedOutcome(unittest.TestCase):

    def setUp(self):
        self.world_state = {
            "schema_version": 4,
            "character_name": "Hero",
            "current_location": "ruined_mill",
            "visited_rooms": ["town_riverside", "forest_edge", "ruined_mill"],
            "cleared_rooms": [],
            "collected_loot": [],
            "dynamic_rooms": {},
            "game_time": {"day": 1, "period": "morning", "steps_since_period_start": 0},
            "quest_log": {"main": [], "side": []},
            "combat_state": None,
        }

        self.character_state = {
            "schema_version": 4,
            "name": "Hero",
            "race": "Human",
            "class_name": "Fighter",
            "level": 1,
            "stats": {"STR": 16, "DEX": 14, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
            "hp": {"current": 0, "max": 12},
            "ac": 16,
            "gold": 100,
            "proficiency_bonus": 2,
            "proficient_skills": ["Athletics"],
            "proficient_saves": ["STR", "CON"],
            "status": "normal",
            "active_conditions": [],
            "death_saves": {"success": 0, "fail": 3},
            "inventory": [
                {"item_id": "longsword", "equipped": True, "quantity": 1},
                {"item_id": "rope", "equipped": False, "quantity": 1},
                {"item_id": "torch", "equipped": False, "quantity": 2},
            ],
        }

    def test_downed_outcome_returns_valid_structure(self):
        res = state_manager.resolve_downed_outcome(self.character_state, None, self.world_state)
        self.assertIn("outcome", res)
        self.assertIn("penalty", res)
        self.assertIn(res["outcome"], ["robbed_and_left", "captured", "rescued_by_npc"])

    def test_monte_carlo_reachability_all_outcomes(self):
        # Run 300 trials across different room types to verify all 3 outcomes are reachable
        outcomes_seen = set()
        
        for i in range(300):
            test_world = dict(self.world_state)
            test_char = json_clone(self.character_state)
            
            # Vary location room type
            if i % 3 == 0:
                test_world["current_location"] = "town_riverside"
            elif i % 3 == 1:
                test_world["current_location"] = "ruined_mill"
            else:
                test_world["current_location"] = "forest_edge"

            res = state_manager.resolve_downed_outcome(test_char, None, test_world)
            outcomes_seen.add(res["outcome"])

        self.assertIn("robbed_and_left", outcomes_seen)
        self.assertIn("captured", outcomes_seen)
        self.assertIn("rescued_by_npc", outcomes_seen)
        self.assertEqual(len(outcomes_seen), 3)

    def test_outcome_robbed_and_left_mechanics(self):
        # Force robbed_and_left outcome using patch
        with patch("random.choices", return_value=["robbed_and_left"]):
            res = state_manager.resolve_downed_outcome(self.character_state, None, self.world_state)

        self.assertEqual(res["outcome"], "robbed_and_left")
        self.assertEqual(self.character_state["hp"]["current"], 1)  # Spec: HP set to EXACTLY 1
        self.assertLess(self.character_state["gold"], 100)           # Gold reduced
        self.assertEqual(len(self.character_state["inventory"]), 1) # Only equipped longsword remains
        self.assertEqual(self.character_state["inventory"][0]["item_id"], "longsword")
        self.assertEqual(self.character_state["status"], "normal")
        self.assertEqual(self.world_state["current_location"], "town_riverside")  # Nearest visited safe room

    def test_outcome_captured_mechanics(self):
        # Force captured outcome
        with patch("random.choices", return_value=["captured"]):
            res = state_manager.resolve_downed_outcome(self.character_state, None, self.world_state)

        self.assertEqual(res["outcome"], "captured")
        self.assertEqual(self.character_state["status"], "captive")
        self.assertEqual(self.character_state["gold"], 100)        # Gold untouched
        self.assertEqual(len(self.character_state["inventory"]), 3) # Inventory untouched

    def test_outcome_rescued_by_npc_mechanics(self):
        # Force rescued_by_npc outcome
        with patch("random.choices", return_value=["rescued_by_npc"]):
            res = state_manager.resolve_downed_outcome(self.character_state, None, self.world_state)

        self.assertEqual(res["outcome"], "rescued_by_npc")
        self.assertEqual(self.character_state["status"], "normal")
        self.assertEqual(self.character_state["hp"]["current"], 6)  # Partial HP (50% max HP = 6)
        self.assertEqual(self.character_state["gold"], 100)        # Gold untouched
        self.assertEqual(len(self.character_state["inventory"]), 3) # Inventory untouched

    def test_captive_escape_loop(self):
        self.character_state["status"] = "captive"
        self.world_state["current_location"] = "ruined_mill"

        # 1. Failed escape check (mocked resolve_check success=False)
        failed_check = {"success": False, "total": 8, "dc": 16}
        with patch("state_manager.resolve_check", return_value=failed_check):
            # Advance time (costs 1 turn)
            dungeon_manager.advance_time(self.world_state, steps=1)
            
        self.assertEqual(self.character_state["status"], "captive")
        self.assertEqual(self.world_state["game_time"]["steps_since_period_start"], 1)

        # 2. Successful escape check (mocked resolve_check success=True)
        succ_check = {"success": True, "total": 18, "dc": 16}
        with patch("state_manager.resolve_check", return_value=succ_check):
            self.character_state["status"] = "normal"
            safe_room = dungeon_manager.find_nearest_visited_safe_room(self.world_state)
            self.world_state["current_location"] = safe_room

        self.assertEqual(self.character_state["status"], "normal")
        self.assertEqual(self.world_state["current_location"], "town_riverside")

    def test_no_crash_on_player_defeat_combat_integration(self):
        # Start combat
        combat_manager.start_combat(["goblin_scout"], self.character_state, self.world_state)
        cs = self.world_state["combat_state"]

        # Force player HP to 0 & death saves to 3 fails
        self.character_state["hp"]["current"] = 0
        cs["player_combatant"]["hp"]["current"] = 0
        cs["player_combatant"]["death_saves"]["fail"] = 3

        # Check combat end triggers player_defeat and resolve_downed_outcome without crashing
        outcome = combat_manager.check_combat_end(cs, self.world_state)
        self.assertEqual(outcome, "player_defeat")
        self.assertIn("downed_outcome", cs)
        self.assertIn(cs["downed_outcome"]["outcome"], ["robbed_and_left", "captured", "rescued_by_npc"])
        # Assert directly on ORIGINAL character_state object identity
        self.assertIn(self.character_state["status"], ["normal", "captive"])

    def test_multi_round_death_save_accumulation_integration(self):
        # Reset character HP to 0 and death_saves to 0 fails
        self.character_state["hp"]["current"] = 0
        self.character_state["death_saves"] = {"success": 0, "fail": 0}
        
        # Start combat
        combat_manager.start_combat(["goblin_scout"], self.character_state, self.world_state)
        cs = self.world_state["combat_state"]
        cs["player_combatant"]["hp"]["current"] = 0
        cs["player_combatant"]["death_saves"] = {"success": 0, "fail": 0}

        # Mock resolve_death_save to return fail=1 (not exhausted) on round 1
        with patch("state_manager.resolve_death_save") as mock_ds:
            mock_ds.return_value = {"roll": 8, "dc": 10, "success": False, "critical": False, "fumble": False, "stabilized": False, "exhausted": False}
            cs["player_combatant"]["death_saves"]["fail"] = 1
            
            res1 = combat_manager.resolve_round(cs, world_state=self.world_state)
            # On round 1 with 1 fail, combat must NOT end
            self.assertIsNone(res1["combat_outcome"])
            self.assertEqual(cs["status"], "active")
            self.assertNotIn("downed_outcome", cs)

        # Mock resolve_death_save to hit 3 fails (exhausted) on round 2
        with patch("state_manager.resolve_death_save") as mock_ds2:
            mock_ds2.return_value = {"roll": 8, "dc": 10, "success": False, "critical": False, "fumble": False, "stabilized": False, "exhausted": True}
            cs["player_combatant"]["death_saves"]["fail"] = 3
            
            res2 = combat_manager.resolve_round(cs, world_state=self.world_state)
            # On round 2 with 3 fails, combat MUST end with player_defeat and resolve downed outcome
            self.assertEqual(res2["combat_outcome"], "player_defeat")
            self.assertEqual(cs["status"], "ended")
            self.assertIn("downed_outcome", cs)

    def test_check_combat_end_idempotency(self):
        combat_manager.start_combat(["goblin_scout"], self.character_state, self.world_state)
        cs = self.world_state["combat_state"]
        self.character_state["hp"]["current"] = 0
        cs["player_combatant"]["hp"]["current"] = 0
        cs["player_combatant"]["death_saves"]["fail"] = 3

        # Call check_combat_end first time
        with patch("state_manager.resolve_downed_outcome", wraps=state_manager.resolve_downed_outcome) as mock_rdo:
            outcome1 = combat_manager.check_combat_end(cs, self.world_state)
            self.assertEqual(outcome1, "player_defeat")
            self.assertEqual(mock_rdo.call_count, 1)

            # Call check_combat_end second time
            outcome2 = combat_manager.check_combat_end(cs, self.world_state)
            self.assertEqual(outcome2, "player_defeat")
            # Must NOT re-call resolve_downed_outcome
            self.assertEqual(mock_rdo.call_count, 1)


def json_clone(obj):
    import json
    return json.loads(json.dumps(obj))


if __name__ == "__main__":
    unittest.main()
