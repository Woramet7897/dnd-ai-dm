"""
phase12_1_tests.py — Tests for Phase 12.1: Inspiration Points & Background Triggers

Covers:
  - award_inspiration capping at max_inspiration (4)
  - spend_inspiration consumption and 0-point protection
  - controlled d20 reroll test: mock d20 low pre-reroll, high post-reroll, confirming
    the higher roll is used and transforms failure to success
  - reroll_check function consuming inspiration and recalculating total/success
  - Background triggers:
    * Criminal: theft / lockpicking / sleight of hand / stealth
    * Soldier: combat victory with no casualties, and tactical shove assist
    * Sage: solved an Arcana check
    * Inactive/irrelevant backgrounds do not falsely trigger
  - combat_manager.resolve_shove integration with Soldier inspiration
  - combat_manager.check_combat_end integration with Soldier inspiration
  - character_creator.derive_stats includes inspiration=0 and max_inspiration=4
"""

import unittest
from unittest.mock import patch

import state_manager
import combat_manager
import character_creator


class TestPhase12_1Inspiration(unittest.TestCase):
    def setUp(self):
        self.player = {
            "name": "TestHero",
            "background": "Criminal",
            "inspiration": 0,
            "max_inspiration": 4,
            "stats": {"STR": 10, "DEX": 14, "CON": 10, "INT": 12, "WIS": 10, "CHA": 10},
            "proficiency_bonus": 2,
            "proficient_skills": ["Stealth", "Sleight of Hand"],
            "hp": {"current": 20, "max": 20},
        }

    # ── 1. Award Inspiration & Cap ────────────────────────────────────────────
    def test_award_inspiration_up_to_cap(self):
        """award_inspiration increments up to max_inspiration (4), then is a no-op."""
        p = dict(self.player)
        self.assertEqual(state_manager.get_inspiration(p), 0)

        # 1st award
        self.assertTrue(state_manager.award_inspiration(p, "Good roleplay"))
        self.assertEqual(state_manager.get_inspiration(p), 1)

        # 2nd, 3rd, 4th awards
        self.assertTrue(state_manager.award_inspiration(p))
        self.assertTrue(state_manager.award_inspiration(p))
        self.assertTrue(state_manager.award_inspiration(p))
        self.assertEqual(state_manager.get_inspiration(p), 4)

        # 5th award must fail (capped at 4) and be a no-op
        self.assertFalse(state_manager.award_inspiration(p, "Beyond cap"))
        self.assertEqual(state_manager.get_inspiration(p), 4)

    def test_award_inspiration_dict_structure(self):
        """award_inspiration works if inspiration is stored as {'current': x, 'max': 4}."""
        p = {"inspiration": {"current": 3, "max": 4}}
        self.assertTrue(state_manager.award_inspiration(p))
        self.assertEqual(p["inspiration"]["current"], 4)
        self.assertFalse(state_manager.award_inspiration(p))
        self.assertEqual(p["inspiration"]["current"], 4)

    # ── 2. Spend Inspiration ──────────────────────────────────────────────────
    def test_spend_inspiration(self):
        """spend_inspiration consumes 1 point; returns False when at 0."""
        p = dict(self.player)
        p["inspiration"] = 2

        self.assertTrue(state_manager.spend_inspiration(p))
        self.assertEqual(state_manager.get_inspiration(p), 1)

        self.assertTrue(state_manager.spend_inspiration(p))
        self.assertEqual(state_manager.get_inspiration(p), 0)

        # Cannot spend when 0
        self.assertFalse(state_manager.spend_inspiration(p))
        self.assertEqual(state_manager.get_inspiration(p), 0)

    # ── 3. Controlled d20 Reroll Test (DoD Requirement) ──────────────────────
    def test_controlled_reroll_takes_higher_value_and_changes_outcome(self):
        """
        DoD: mock d20 to known low value pre-reroll (3), high value post-reroll (18).
        Confirm higher value is used and changes outcome from failure to success.
        """
        p = dict(self.player)
        p["inspiration"] = 2
        # DC for medium is 13.
        # DEX mod for 14 is +2. With roll 3, total = 5 -> Failure.
        # With reroll 18, total = 20 -> Success.

        mock_dice_sequence = [3, 18]
        with patch("state_manager._roll_d20", side_effect=mock_dice_sequence):
            res = state_manager.resolve_check(
                stat="DEX",
                difficulty="medium",
                state=p,
                use_inspiration=True,
            )

        self.assertEqual(res["roll"], 18)
        self.assertEqual(res["total"], 20)
        self.assertTrue(res["success"])
        self.assertTrue(res["inspiration_spent"])
        self.assertEqual(state_manager.get_inspiration(p), 1)

    def test_reroll_check_post_hoc(self):
        """reroll_check consumes 1 inspiration and takes the higher of old and new roll."""
        p = dict(self.player)
        p["inspiration"] = 1

        prev_result = {
            "roll": 4,
            "modifier": 1,
            "proficiency": 0,
            "bonus": 0,
            "total": 5,
            "dc": 13,
            "difficulty": "medium",
            "stat": "STR",
            "skill": None,
            "success": False,
            "critical": False,
            "fumble": False,
        }

        # Mock reroll to 16
        with patch("state_manager._roll_d20", return_value=16):
            new_res = state_manager.reroll_check(prev_result, p)

        self.assertTrue(new_res["rerolled"])
        self.assertEqual(new_res["roll"], 16)
        self.assertEqual(new_res["total"], 17)
        self.assertTrue(new_res["success"])
        self.assertEqual(state_manager.get_inspiration(p), 0)

        # Trying to reroll again with 0 inspiration fails cleanly
        no_insp_res = state_manager.reroll_check(new_res, p)
        self.assertFalse(no_insp_res["rerolled"])
        self.assertIn("No inspiration", no_insp_res["reason"])

    # ── 4. Background Inspiration Triggers ───────────────────────────────────
    def test_criminal_background_triggers(self):
        """Criminal receives inspiration on successful lockpick / theft / sleight of hand / stealth."""
        crim = {"background": "Criminal", "inspiration": 0, "max_inspiration": 4}

        # Successful lockpick
        self.assertTrue(state_manager.check_inspiration_trigger(crim, "lockpick", {"success": True}))
        self.assertEqual(state_manager.get_inspiration(crim), 1)

        # Successful sleight of hand
        self.assertTrue(state_manager.check_inspiration_trigger(crim, "sleight_of_hand", {"success": True}))
        self.assertEqual(state_manager.get_inspiration(crim), 2)

        # Failed attempt does not award inspiration
        self.assertFalse(state_manager.check_inspiration_trigger(crim, "theft", {"success": False}))
        self.assertEqual(state_manager.get_inspiration(crim), 2)

        # Irrelevant trigger does not award inspiration
        self.assertFalse(state_manager.check_inspiration_trigger(crim, "arcana", {"success": True}))
        self.assertEqual(state_manager.get_inspiration(crim), 2)

    def test_sage_background_triggers(self):
        """Sage receives inspiration on successful Arcana check."""
        sage = {
            "background": "Sage",
            "inspiration": 0,
            "max_inspiration": 4,
            "stats": {"INT": 16},
            "proficiency_bonus": 2,
            "proficient_skills": ["Arcana"],
        }

        # Via resolve_check with skill="Arcana"
        with patch("state_manager._roll_d20", return_value=15):
            res = state_manager.resolve_check("INT", "medium", sage, skill="Arcana")

        self.assertTrue(res["success"])
        self.assertEqual(state_manager.get_inspiration(sage), 1)

        # Failed Arcana check does not award inspiration
        with patch("state_manager._roll_d20", return_value=1):
            res_fail = state_manager.resolve_check("INT", "hard", sage, skill="Arcana")
        self.assertFalse(res_fail["success"])
        self.assertEqual(state_manager.get_inspiration(sage), 1)

    def test_soldier_combat_victory_no_casualties(self):
        """Soldier receives inspiration on victory with no casualties."""
        soldier = {
            "id": "player",
            "name": "Warrior",
            "background": "Soldier",
            "inspiration": 0,
            "max_inspiration": 4,
            "hp": {"current": 25, "max": 25},
            "stats": {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
            "level": 1,
            "xp_current": 0,
        }

        cs = {
            "status": "active",
            "enemies": [{"id": "goblin", "hp": {"current": 0, "max": 7}, "xp_value": 50}],
            "companions": [{"id": "comp_1", "hp": {"current": 10, "max": 12}}],
            "player_combatant": soldier,
        }

        outcome = combat_manager.check_combat_end(cs)
        self.assertEqual(outcome, "player_victory")
        self.assertEqual(state_manager.get_inspiration(soldier), 1)

    def test_soldier_combat_victory_with_casualties_no_award(self):
        """Soldier does NOT receive inspiration if a companion was killed."""
        soldier = {
            "id": "player",
            "name": "Warrior",
            "background": "Soldier",
            "inspiration": 0,
            "max_inspiration": 4,
            "hp": {"current": 25, "max": 25},
            "stats": {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
            "level": 1,
            "xp_current": 0,
        }

        cs = {
            "status": "active",
            "enemies": [{"id": "goblin", "hp": {"current": 0, "max": 7}, "xp_value": 50}],
            "companions": [{"id": "comp_1", "hp": {"current": 0, "max": 12}}],  # dead companion!
            "player_combatant": soldier,
        }

        outcome = combat_manager.check_combat_end(cs)
        self.assertEqual(outcome, "player_victory")
        self.assertEqual(state_manager.get_inspiration(soldier), 0)

    def test_soldier_shove_assist_trigger(self):
        """Soldier receives inspiration on successful Shove action."""
        soldier = {
            "id": "player",
            "name": "Warrior",
            "background": "Soldier",
            "inspiration": 0,
            "max_inspiration": 4,
            "stats": {"STR": 16, "DEX": 10, "CON": 12, "INT": 10, "WIS": 10, "CHA": 10},
            "proficiency_bonus": 2,
            "proficient_skills": ["Athletics"],
            "hp": {"current": 20, "max": 20},
        }
        target = {
            "id": "enemy",
            "name": "Orc",
            "stats": {"STR": 10, "DEX": 10},
            "hp": {"current": 15, "max": 15},
            "size": "Medium",
        }

        # Attacker rolls high (15 + 5 = 20), Target rolls low (5 + 0 = 5)
        with patch("combat_manager._roll_d20", side_effect=[15, 5]):
            res = combat_manager.resolve_shove(soldier, target, shove_type="push")

        self.assertTrue(res["success"])
        self.assertEqual(state_manager.get_inspiration(soldier), 1)

    def test_unrelated_background_does_not_trigger(self):
        """Folk Hero does not gain inspiration from Arcana or lockpicking."""
        fh = {
            "background": "Folk Hero",
            "inspiration": 0,
            "max_inspiration": 4,
            "stats": {"INT": 14},
            "proficiency_bonus": 2,
        }
        with patch("state_manager._roll_d20", return_value=18):
            state_manager.resolve_check("INT", "medium", fh, skill="Arcana")

        self.assertEqual(state_manager.get_inspiration(fh), 0)

    # ── 5. Character Creator Integration ──────────────────────────────────────
    def test_character_creator_initializes_inspiration(self):
        """character_creator.derive_stats sets inspiration=0 and max_inspiration=4."""
        sheet = character_creator.derive_stats(
            name="Valeros",
            race_id="human",
            class_id="fighter",
            background_id="soldier",
            base_stats={"STR": 15, "DEX": 13, "CON": 14, "INT": 10, "WIS": 12, "CHA": 8},
            campaign_tone="classic",
        )
        self.assertIn("inspiration", sheet)
        self.assertEqual(sheet["inspiration"], 0)
        self.assertIn("max_inspiration", sheet)
        self.assertEqual(sheet["max_inspiration"], 4)

    def test_case_insensitive_skill_proficiency_and_trigger(self):
        """Passing lowercase skill still applies proficiency bonus and triggers background."""
        crim = {
            "background": "Criminal",
            "inspiration": 0,
            "max_inspiration": 4,
            "stats": {"DEX": 14},  # +2 mod
            "proficiency_bonus": 2,
            "proficient_skills": ["Sleight of Hand"],
        }
        with patch("state_manager._roll_d20", return_value=10):
            res = state_manager.resolve_check("DEX", "medium", crim, skill="sleight of hand")

        self.assertEqual(res["proficiency"], 2)
        self.assertEqual(res["total"], 14)  # 10 + 2 + 2 = 14 >= DC 13
        self.assertTrue(res["success"])
        self.assertEqual(state_manager.get_inspiration(crim), 1)


if __name__ == "__main__":
    unittest.main()
