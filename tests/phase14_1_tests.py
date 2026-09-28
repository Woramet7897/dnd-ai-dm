"""
phase14_1_tests.py — Sub-phase 14.1 Contextual Action Suggestions Tests
Verifies:
1. Well-formed JSON block parsed into suggestions list
2. Suggestions stripped from displayed narrative (zero leakage)
3. Missing/malformed JSON falls back cleanly to the 3 default strings
4. Output always contains exactly 3 suggestions (padded / truncated)
5. Unfenced raw JSON parsed and stripped cleanly
6. Combat round narration carries suggestions directive & extracts suggestions
7. Exception resilience returns default suggestions
8. app.py session state initialization
"""

import sys
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import app
import llm_handler


class MockNarrativeClient:
    """Mock client for testing generate_narrative_response."""

    def __init__(self, response_text: str):
        self.response_text = response_text
        self.last_messages: Optional[List[Dict[str, str]]] = None
        self.call_count = 0

    def chat(self, model: str, messages: List[Dict[str, str]], options: Optional[Dict[str, Any]] = None):
        self.call_count += 1
        self.last_messages = messages
        return {
            "message": {"content": self.response_text},
            "eval_count": 25,
            "prompt_eval_count": 50,
            "eval_duration": 1000,
            "total_duration": 1200,
        }


class TestPhase14_1ContextualSuggestions(unittest.TestCase):

    def setUp(self):
        self.player_sample = {
            "name": "Valeros",
            "race": "Human",
            "class": "Fighter",
            "hp": {"current": 20, "max": 20},
            "stats": {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
            "ac": 16,
            "inventory": [],
            "status": "normal",
        }
        self.world_sample = {
            "current_location": "town_riverside",
            "game_time": {"day": 1, "period": "morning", "steps_since_period_start": 0},
            "combat_state": None,
        }

    def test_well_formed_fenced_json_suggestions(self):
        """DoD 1 & 2: Well-formed JSON block parsed into suggestions list and stripped from narrative."""
        raw_output = (
            "You step through the archway into the dusty armory. Racks of rusted weapons line the stone walls.\n"
            "```json\n"
            '{\n  "suggestions": ["สำรวจชั้นวางอาวุธ", "หยิบโล่ขึ้นมาตรวจดู", "เดินไปยังประตูทิศตะวันออก"]\n}\n'
            "```"
        )
        client = MockNarrativeClient(raw_output)
        res = llm_handler.generate_narrative_response(
            user_input="Look around the armory",
            player_state=self.player_sample,
            world_state=self.world_sample,
            client=client,
        )

        expected_suggestions = ["สำรวจชั้นวางอาวุธ", "หยิบโล่ขึ้นมาตรวจดู", "เดินไปยังประตูทิศตะวันออก"]
        self.assertEqual(res["suggestions"], expected_suggestions)
        self.assertEqual(len(res["suggestions"]), 3)

        # Confirm no JSON or markdown fence leakage in displayed narrative
        self.assertNotIn("```json", res["narrative"])
        self.assertNotIn("suggestions", res["narrative"])
        self.assertNotIn("{", res["narrative"])
        self.assertNotIn("}", res["narrative"])
        self.assertTrue(res["narrative"].startswith("You step through the archway"))

    def test_missing_suggestions_block_fallback(self):
        """DoD 3: Pure narrative with no JSON falls back cleanly to the 3 default strings."""
        raw_output = "The wind howls softly through the pine needles overhead. The path continues south."
        client = MockNarrativeClient(raw_output)
        res = llm_handler.generate_narrative_response(
            user_input="Listen carefully",
            player_state=self.player_sample,
            world_state=self.world_sample,
            client=client,
        )

        self.assertEqual(res["suggestions"], llm_handler.DEFAULT_ACTION_SUGGESTIONS)
        self.assertEqual(len(res["suggestions"]), 3)
        self.assertEqual(res["narrative"], raw_output)

    def test_malformed_json_fallback(self):
        """DoD 3: Malformed or broken JSON block falls back to default strings without error."""
        raw_output = (
            "A sudden shadow passes overhead.\n"
            "```json\n"
            '{"suggestions": ["มองขึ้นไปบนฟ้า", incomplete...\n'
            "```"
        )
        client = MockNarrativeClient(raw_output)
        res = llm_handler.generate_narrative_response(
            user_input="Look up",
            player_state=self.player_sample,
            world_state=self.world_sample,
            client=client,
        )

        self.assertEqual(res["suggestions"], llm_handler.DEFAULT_ACTION_SUGGESTIONS)
        self.assertEqual(len(res["suggestions"]), 3)
        self.assertNotIn("```json", res["narrative"])
        self.assertNotIn("incomplete", res["narrative"])
        self.assertEqual(res["narrative"], "A sudden shadow passes overhead.")

    def test_unclosed_truncated_json_stripped_completely(self):
        """Unclosed/truncated JSON block at the end is completely stripped from narrative."""
        raw_output = (
            "The story ends here.\n\n"
            "```json\n"
            '{\n  "suggestions": [\n    "action 1",\n    "action 2"'
        )
        client = MockNarrativeClient(raw_output)
        res = llm_handler.generate_narrative_response(
            user_input="Continue",
            player_state=self.player_sample,
            world_state=self.world_sample,
            client=client,
        )
        self.assertEqual(res["narrative"], "The story ends here.")
        self.assertNotIn("suggestions", res["narrative"])
        self.assertNotIn("{", res["narrative"])
        self.assertEqual(len(res["suggestions"]), 3)

    def test_unfenced_raw_json_suggestions(self):
        """Unfenced raw JSON at response end is parsed and stripped completely."""
        raw_output = (
            "The tavernkeeper slides a cold ale across the polished wooden counter.\n"
            '{"suggestions": ["จิบเครื่องดื่ม", "ถามเรื่องข่าวลือ", "จ่ายเงินค่าเหล้า"]}'
        )
        client = MockNarrativeClient(raw_output)
        res = llm_handler.generate_narrative_response(
            user_input="Order a drink",
            player_state=self.player_sample,
            world_state=self.world_sample,
            client=client,
        )

        expected = ["จิบเครื่องดื่ม", "ถามเรื่องข่าวลือ", "จ่ายเงินค่าเหล้า"]
        self.assertEqual(res["suggestions"], expected)
        self.assertEqual(len(res["suggestions"]), 3)
        self.assertNotIn("suggestions", res["narrative"])
        self.assertNotIn("{", res["narrative"])
        self.assertEqual(res["narrative"], "The tavernkeeper slides a cold ale across the polished wooden counter.")

    def test_under_three_suggestions_padded(self):
        """DoD 4: Fewer than 3 suggestions padded to exactly 3 using default suggestions."""
        raw_output = (
            "You light a torch.\n"
            "```json\n"
            '{"suggestions": ["ชูคบเพลิงส่องทาง"]}\n'
            "```"
        )
        client = MockNarrativeClient(raw_output)
        res = llm_handler.generate_narrative_response(
            user_input="Light torch",
            player_state=self.player_sample,
            world_state=self.world_sample,
            client=client,
        )

        self.assertEqual(len(res["suggestions"]), 3)
        self.assertEqual(res["suggestions"][0], "ชูคบเพลิงส่องทาง")
        self.assertEqual(res["suggestions"][1], llm_handler.DEFAULT_ACTION_SUGGESTIONS[0])
        self.assertEqual(res["suggestions"][2], llm_handler.DEFAULT_ACTION_SUGGESTIONS[1])

    def test_over_three_suggestions_truncated(self):
        """DoD 4: More than 3 suggestions truncated to first 3."""
        raw_output = (
            "You enter a bustling market.\n"
            "```json\n"
            '{"suggestions": ["ทักพ่อค้า", "ดูแผงผลไม้", "คุยกับทหารยาม", "ซื้อเสบียง", "ขโมยของ"]}\n'
            "```"
        )
        client = MockNarrativeClient(raw_output)
        res = llm_handler.generate_narrative_response(
            user_input="Explore market",
            player_state=self.player_sample,
            world_state=self.world_sample,
            client=client,
        )

        self.assertEqual(len(res["suggestions"]), 3)
        self.assertEqual(res["suggestions"], ["ทักพ่อค้า", "ดูแผงผลไม้", "คุยกับทหารยาม"])

    def test_combat_round_carries_suggestions(self):
        """Architectural Decision 1: Combat round narration includes suggestions directive & parses output."""
        combat_round_narration = "[System: Round Result] Valeros strikes the Goblin Scout for 8 damage."
        raw_output = (
            "Your steel cuts deep into the goblin's shoulder, throwing it off balance.\n"
            "```json\n"
            '{"suggestions": ["แทงซ้ำปิดฉาก", "ตั้งท่าป้องกัน (Dodge)", "ถอยออกมาตั้งหลัก"]}\n'
            "```"
        )
        client = MockNarrativeClient(raw_output)
        res = llm_handler.generate_narrative_response(
            user_input="",
            player_state=self.player_sample,
            world_state=self.world_sample,
            round_result=combat_round_narration,
            client=client,
        )

        # Check prompt contained suggestions directive in the user message
        last_user_msg = client.last_messages[-1]["content"]
        self.assertIn("Action Suggestions Directive", last_user_msg)
        self.assertIn("[System: Round Result]", last_user_msg)

        # Check extracted suggestions
        self.assertEqual(res["suggestions"], ["แทงซ้ำปิดฉาก", "ตั้งท่าป้องกัน (Dodge)", "ถอยออกมาตั้งหลัก"])
        self.assertEqual(len(res["suggestions"]), 3)
        self.assertNotIn("```json", res["narrative"])

    def test_exception_returns_default_suggestions(self):
        """Ollama client exception falls back safely to default suggestions without crash."""
        broken_client = MagicMock()
        broken_client.chat.side_effect = RuntimeError("Ollama connection timed out")

        res = llm_handler.generate_narrative_response(
            user_input="Check chest",
            player_state=self.player_sample,
            world_state=self.world_sample,
            client=broken_client,
        )

        self.assertEqual(res["suggestions"], llm_handler.DEFAULT_ACTION_SUGGESTIONS)
        self.assertEqual(len(res["suggestions"]), 3)
        self.assertIn("error", res)
        self.assertIn("Ollama error", res["narrative"])

    def test_app_session_state_init_contains_defaults(self):
        """Verify app.py initializes action_suggestions with the 3 spec defaults."""
        import streamlit as st
        st.session_state.clear()
        app.init_session_state()

        self.assertIn("action_suggestions", st.session_state)
        self.assertEqual(st.session_state["action_suggestions"], llm_handler.DEFAULT_ACTION_SUGGESTIONS)
        self.assertEqual(len(st.session_state["action_suggestions"]), 3)


if __name__ == "__main__":
    unittest.main()
