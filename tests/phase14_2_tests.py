"""
phase14_2_tests.py — Sub-phase 14.2 Camp Companion Dialogue (Approval System) Tests
Verifies:
1. Camp dialogue prompt is built from real get_relevant_lore() output (not hardcoded filler)
2. Each of the 3 response paths adjusts approval in the correct direction:
   - agree: +5 approval
   - disagree: -5 approval
   - neutral: 0 approval
3. Context gating: only triggers in a camp/rest context, NOT mid-combat or mid-dungeon-crawl
4. Resting (short and long rest) resets camp_dialogue_pending = True for party companions
5. Clamping approval strictly between 0 and 100
6. Graceful lore-grounded fallback on missing or malformed LLM response
7. Session state initialization in app.py
"""

import unittest
from typing import Any, Dict, List, Optional

import app
import dungeon_manager
import llm_handler
import state_manager


class MockCampDialogueClient:
    """Mock client for testing generate_camp_dialogue."""

    def __init__(self, response_text: str = ""):
        self.response_text = response_text
        self.last_messages: Optional[List[Dict[str, str]]] = None
        self.last_prompt: str = ""
        self.call_count = 0

    def chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        format: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
    ):
        self.call_count += 1
        self.last_messages = messages
        if messages:
            self.last_prompt = messages[-1].get("content", "")
        return {
            "message": {"content": self.response_text},
            "eval_count": 40,
            "prompt_eval_count": 80,
            "eval_duration": 1500,
            "total_duration": 1800,
        }


class MockMemoryManager:
    """Mock memory manager returning controlled lore items."""

    def __init__(self, lore_entries: Optional[List[Dict[str, Any]]] = None):
        self.lore_entries = lore_entries or []
        self.last_query: Optional[str] = None

    def get_relevant_lore(self, query: str, n_results: int = 3) -> List[Dict[str, Any]]:
        self.last_query = query
        return self.lore_entries[:n_results]


class TestPhase14_2CampCompanionDialogue(unittest.TestCase):

    def setUp(self):
        self.player_sample = {
            "name": "Valeros",
            "race": "Human",
            "class": "Fighter",
            "hp": {"current": 25, "max": 25},
            "inventory": [],
            "status": "normal",
        }
        self.world_sample = {
            "current_location": "town_riverside",
            "game_time": {"day": 2, "period": "evening", "steps_since_period_start": 1},
            "combat_state": None,
            "cleared_rooms": [],
            "party": {
                "companions": [
                    {"id": "lyra_shadow", "name": "Lyra", "hp": {"current": 18, "max": 18}},
                    {"id": "karr_ironfist", "name": "Karr", "hp": {"current": 22, "max": 22}},
                ],
                "former_companions": [],
            },
            "companions_approval": {},
        }

    def test_camp_dialogue_prompt_contains_real_lore(self):
        """
        DoD 1: Unit test with a mock LLM client confirming a camp dialogue prompt
        is built from real get_relevant_lore() output (not hardcoded filler).
        """
        real_lore_content = "The party breached the goblin chief's sanctuary and recovered the sunstone amulet."
        mock_mm = MockMemoryManager([
            {"text": real_lore_content, "type": "major", "id": "lore_test_101"}
        ])

        mock_json_response = (
            '{\n'
            '  "statement": "Holding that sunstone amulet reminds me of why I fight.",\n'
            '  "topic": "The sunstone amulet recovery",\n'
            '  "options": {\n'
            '    "agree": "We did good work recovering that sacred relic.",\n'
            '    "disagree": "It was too dangerous; we almost got slaughtered.",\n'
            '    "neutral": "Amulet or not, we have a long road ahead."\n'
            '  }\n'
            '}'
        )
        mock_client = MockCampDialogueClient(mock_json_response)

        res = llm_handler.generate_camp_dialogue(
            companion_id="lyra_shadow",
            memory_manager=mock_mm,
            player_state=self.player_sample,
            world_state=self.world_sample,
            client=mock_client,
        )

        # 1. Verify client was called
        self.assertEqual(mock_client.call_count, 1)

        # 2. Verify prompt contains the EXACT real lore string
        self.assertIn(real_lore_content, mock_client.last_prompt)
        self.assertIn("Lyra", mock_client.last_prompt)

        # 3. Verify returned dialogue structure
        self.assertIsNotNone(res)
        self.assertEqual(res["companion_id"], "lyra_shadow")
        self.assertEqual(res["companion_name"], "Lyra")
        self.assertEqual(res["statement"], "Holding that sunstone amulet reminds me of why I fight.")
        self.assertEqual(res["topic"], "The sunstone amulet recovery")
        self.assertIn("agree", res["options"])
        self.assertIn("disagree", res["options"])
        self.assertIn("neutral", res["options"])

    def test_response_adjustments_agree_disagree_neutral(self):
        """
        DoD 2: Confirms each of the 3 response paths adjusts approval in the correct direction:
        - agree: +5
        - disagree: -5
        - neutral: 0
        """
        # Baseline: get_companion_approval starts at 50
        comp_app = state_manager.get_companion_approval(self.world_sample, "lyra_shadow")
        self.assertEqual(comp_app["approval"], 50)
        self.assertTrue(comp_app["camp_dialogue_pending"])

        # 1. Agree response -> 50 + 5 = 55
        res_agree = state_manager.respond_to_camp_dialogue(
            companion_id="lyra_shadow",
            response_type="agree",
            world_state=self.world_sample,
            dialogue_data={"topic": "Sunstone recovery"},
        )
        self.assertEqual(res_agree["approval_change"], 5)
        self.assertEqual(res_agree["new_approval"], 55)
        self.assertFalse(res_agree["camp_dialogue_pending"])
        self.assertEqual(res_agree["last_discussion_topic"], "Sunstone recovery")
        self.assertEqual(self.world_sample["companions_approval"]["lyra_shadow"]["approval"], 55)

        # 2. Disagree response -> 55 - 5 = 50
        res_disagree = state_manager.respond_to_camp_dialogue(
            companion_id="lyra_shadow",
            response_type="disagree",
            world_state=self.world_sample,
            dialogue_data={"topic": "Tavern argument"},
        )
        self.assertEqual(res_disagree["approval_change"], -5)
        self.assertEqual(res_disagree["new_approval"], 50)
        self.assertFalse(res_disagree["camp_dialogue_pending"])
        self.assertEqual(res_disagree["last_discussion_topic"], "Tavern argument")
        self.assertEqual(self.world_sample["companions_approval"]["lyra_shadow"]["approval"], 50)

        # 3. Neutral response -> 50 + 0 = 50
        res_neutral = state_manager.respond_to_camp_dialogue(
            companion_id="lyra_shadow",
            response_type="neutral",
            world_state=self.world_sample,
            dialogue_data={"topic": "Stargazing"},
        )
        self.assertEqual(res_neutral["approval_change"], 0)
        self.assertEqual(res_neutral["new_approval"], 50)
        self.assertFalse(res_neutral["camp_dialogue_pending"])
        self.assertEqual(res_neutral["last_discussion_topic"], "Stargazing")
        self.assertEqual(self.world_sample["companions_approval"]["lyra_shadow"]["approval"], 50)

    def test_approval_clamping_bounds(self):
        """Approval is clamped strictly between 0 and 100."""
        # Test ceiling at 100
        comp_app = state_manager.get_companion_approval(self.world_sample, "karr_ironfist")
        comp_app["approval"] = 98

        res_hi = state_manager.respond_to_camp_dialogue("karr_ironfist", "agree", self.world_sample)
        self.assertEqual(res_hi["new_approval"], 100)

        # Test floor at 0
        comp_app["approval"] = 3
        res_lo = state_manager.respond_to_camp_dialogue("karr_ironfist", "disagree", self.world_sample)
        self.assertEqual(res_lo["new_approval"], 0)

    def test_context_gating_mid_combat_rejected(self):
        """
        DoD 3a: Confirms camp dialogue does NOT trigger mid-combat.
        Returns None and does not invoke client.
        """
        self.world_sample["combat_state"] = {"status": "active", "round": 1}
        mock_client = MockCampDialogueClient("test")

        res = llm_handler.generate_camp_dialogue(
            companion_id="lyra_shadow",
            memory_manager=None,
            player_state=self.player_sample,
            world_state=self.world_sample,
            client=mock_client,
        )

        self.assertIsNone(res)
        self.assertEqual(mock_client.call_count, 0)
        self.assertFalse(dungeon_manager.is_camp_context(self.world_sample))

    def test_context_gating_mid_dungeon_crawl_rejected(self):
        """
        DoD 3b: Confirms camp dialogue does NOT trigger mid-dungeon-crawl
        (i.e. inside an uncleared / unsafe dungeon room).
        """
        # ruined_mill is a dungeon room with is_safe = False
        self.world_sample["current_location"] = "ruined_mill"
        self.world_sample["cleared_rooms"] = []
        mock_client = MockCampDialogueClient("test")

        res = llm_handler.generate_camp_dialogue(
            companion_id="lyra_shadow",
            memory_manager=None,
            player_state=self.player_sample,
            world_state=self.world_sample,
            client=mock_client,
        )

        self.assertIsNone(res)
        self.assertEqual(mock_client.call_count, 0)
        self.assertFalse(dungeon_manager.is_camp_context(self.world_sample))

    def test_context_gating_safe_camp_context_accepted(self):
        """
        DoD 3c: Camp dialogue succeeds in valid camp / rest contexts:
        - Town rooms (e.g. town_riverside)
        - Cleared rooms with camp set (at_camp: True)
        - Safe camp locations
        """
        # Town context
        self.world_sample["current_location"] = "town_riverside"
        self.assertTrue(dungeon_manager.is_camp_context(self.world_sample))

        # Cleared dungeon room with camp pitched
        cleared_world = {
            "current_location": "ruined_mill",
            "cleared_rooms": ["ruined_mill"],
            "at_camp": True,
            "combat_state": None,
        }
        self.assertTrue(dungeon_manager.is_camp_context(cleared_world))

        # Explicit rest context
        resting_world = {
            "current_location": "forest_edge",
            "is_resting": True,
            "combat_state": None,
        }
        self.assertTrue(dungeon_manager.is_camp_context(resting_world))

    def test_resting_resets_camp_dialogue_pending(self):
        """
        DoD 4: Taking a short rest or long rest resets camp_dialogue_pending = True
        for all active party companions.
        """
        # Initially set dialogue as not pending
        lyra_app = state_manager.get_companion_approval(self.world_sample, "lyra_shadow")
        karr_app = state_manager.get_companion_approval(self.world_sample, "karr_ironfist")
        lyra_app["camp_dialogue_pending"] = False
        karr_app["camp_dialogue_pending"] = False

        # Short rest resets pending
        state_manager.perform_short_rest(self.player_sample, self.world_sample)
        self.assertTrue(lyra_app["camp_dialogue_pending"])
        self.assertTrue(karr_app["camp_dialogue_pending"])

        # Mark consumed again
        lyra_app["camp_dialogue_pending"] = False
        karr_app["camp_dialogue_pending"] = False

        # Long rest resets pending
        state_manager.long_rest(self.world_sample, self.player_sample)
        self.assertTrue(lyra_app["camp_dialogue_pending"])
        self.assertTrue(karr_app["camp_dialogue_pending"])

    def test_malformed_llm_json_fallback_preserves_real_lore(self):
        """
        If LLM returns unparseable text or markdown garbage, fallback cleanly generates
        structured dialogue options incorporating the real retrieved lore.
        """
        real_lore_content = "The shadows deepened as the dragon circled the ruined watchtower."
        mock_mm = MockMemoryManager([
            {"text": real_lore_content, "type": "minor", "id": "lore_test_102"}
        ])

        # Garbage LLM output
        mock_client = MockCampDialogueClient("I cannot fulfill this request as a standard assistant.")

        res = llm_handler.generate_camp_dialogue(
            companion_id="karr_ironfist",
            memory_manager=mock_mm,
            player_state=self.player_sample,
            world_state=self.world_sample,
            client=mock_client,
        )

        self.assertIsNotNone(res)
        self.assertEqual(res["companion_id"], "karr_ironfist")
        self.assertEqual(res["companion_name"], "Karr")
        # Real lore text preserved in fallback statement
        self.assertIn(real_lore_content, res["statement"])
        self.assertIn("agree", res["options"])
        self.assertIn("disagree", res["options"])
        self.assertIn("neutral", res["options"])

    def test_app_session_state_active_camp_dialogue_init(self):
        """app.init_session_state() initializes active_camp_dialogue to None."""
        import streamlit as st
        # Clear if already present
        if "active_camp_dialogue" in st.session_state:
            del st.session_state["active_camp_dialogue"]

        app.init_session_state()
        self.assertIn("active_camp_dialogue", st.session_state)
        self.assertIsNone(st.session_state["active_camp_dialogue"])


if __name__ == "__main__":
    unittest.main()
