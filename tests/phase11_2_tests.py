"""
phase11_2_tests.py — Phase 11.2 Shove Action & High Ground Tests

Verifies:
  1. High Ground: +2 attack bonus for ranged attacks and spells when attacker has high ground.
  2. High Ground: Disadvantage on ranged attacks and spells targeting a combatant on high ground.
  3. Shove Contest: Attacker rolls Athletics vs Target's higher of Athletics / Acrobatics.
  4. Shove Contest: Attacker losing results in clean no-op (state unchanged, 0 damage, no condition).
  5. Shove Prone: Attacker winning with shove_type='prone' applies 'prone' condition (dur 2) and removes high ground.
  6. Shove Push: 5ft push without hazards leaves target with no damage and removes high ground.
  7. Shove Hazard (near_chasm): Failed DC 13 DEX save instantly kills Small/Medium creature.
  8. Shove Hazard (near_chasm): Failed DC 13 DEX save deals 3d6 damage to Large+ creature.
  9. Shove Hazard (near_chasm / acid_pool): Successful DC 13 DEX save prevents falling/hazard damage.
 10. Shove Hazard (acid_pool): Failed DC 13 DEX save deals 3d6 acid damage.
 11. Full Round & Narration Integration: Shove integrates into resolve_round(), classify_round_significance(), and narration block.
"""

import unittest
from unittest.mock import patch
import combat_manager as cm
import state_manager as sm


class TestHighGroundMechanics(unittest.TestCase):
    def setUp(self):
        self.attacker = {
            "id": "attacker",
            "name": "Attacker",
            "stats": {"STR": 14, "DEX": 16, "CON": 12, "INT": 10, "WIS": 10, "CHA": 10},
            "active_conditions": [],
            "has_high_ground": False,
        }
        self.target = {
            "id": "target",
            "name": "Target",
            "ac": 12,
            "hp": {"current": 20, "max": 20},
            "stats": {"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
            "active_conditions": [],
            "has_high_ground": False,
        }
        self.ranged_attack = {
            "name": "Shortbow",
            "attack_bonus": 5,
            "damage": "1d6+3",
            "damage_type": "piercing",
            "ranged": True,
        }
        self.melee_attack = {
            "name": "Shortsword",
            "attack_bonus": 5,
            "damage": "1d6+3",
            "damage_type": "piercing",
            "ranged": False,
        }

    def test_ranged_attack_gets_plus_2_on_high_ground(self):
        self.attacker["has_high_ground"] = True
        with patch("combat_manager._roll_d20", return_value=10):
            res = cm.resolve_attack(self.attacker, self.target, self.ranged_attack)
            # base attack_bonus 5 + high ground 2 = 7; total = 10 + 7 = 17
            self.assertEqual(res["total_to_hit"], 17)
            self.assertTrue(res["hit"])

    def test_melee_attack_does_not_get_high_ground_bonus(self):
        self.attacker["has_high_ground"] = True
        with patch("combat_manager._roll_d20", return_value=10):
            res = cm.resolve_attack(self.attacker, self.target, self.melee_attack)
            # melee gets base bonus 5 only; total = 10 + 5 = 15
            self.assertEqual(res["total_to_hit"], 15)

    def test_ranged_attack_against_high_ground_has_disadvantage(self):
        self.target["has_high_ground"] = True
        # Roll sequence for disadvantage: min(18, 4) = 4
        with patch("combat_manager._roll_d20", side_effect=[18, 4]):
            res = cm.resolve_attack(self.attacker, self.target, self.ranged_attack)
            self.assertEqual(res["raw_roll"], 4)
            # 4 + 5 = 9 vs AC 12 -> miss
            self.assertFalse(res["hit"])

    def test_melee_attack_against_high_ground_no_disadvantage(self):
        self.target["has_high_ground"] = True
        with patch("combat_manager._roll_d20", return_value=14):
            res = cm.resolve_attack(self.attacker, self.target, self.melee_attack)
            self.assertEqual(res["raw_roll"], 14)
            self.assertTrue(res["hit"])

    def test_spell_attack_high_ground_bonus_and_disadvantage(self):
        caster = {
            "id": "wizard",
            "name": "Mage",
            "class": "Wizard",
            "proficiency_bonus": 2,
            "stats": {"INT": 16, "DEX": 12, "CON": 10, "STR": 10, "WIS": 10, "CHA": 10},
            "active_conditions": [],
            "has_high_ground": True,
        }
        target = {
            "id": "target",
            "name": "Target",
            "ac": 14,
            "hp": {"current": 20, "max": 20},
            "active_conditions": [],
            "has_high_ground": False,
        }
        fire_bolt = {
            "name": "Fire Bolt",
            "level": 0,
            "effect": {"type": "damage", "damage": "1d10", "damage_type": "fire"},
        }
        # Caster INT mod = +3, prof = +2, high_ground = +2 -> to_hit_bonus = 7
        with patch("state_manager._roll_d20", return_value=10), patch("state_manager._roll_dice", return_value=8):
            res = sm.resolve_spell_attack(caster, target, fire_bolt)
            self.assertEqual(res["to_hit_bonus"], 7)
            self.assertEqual(res["total_to_hit"], 17)
            self.assertTrue(res["hit"])

        # Target with high ground gives disadvantage to spell attack
        caster["has_high_ground"] = False
        target["has_high_ground"] = True
        with patch("state_manager._roll_d20", side_effect=[18, 5]), patch("state_manager._roll_dice", return_value=8):
            res2 = sm.resolve_spell_attack(caster, target, fire_bolt)
            self.assertEqual(res2["raw_roll"], 5)
            # 5 + 5 = 10 vs AC 14 -> miss
            self.assertFalse(res2["hit"])


class TestShoveMechanics(unittest.TestCase):
    def setUp(self):
        self.attacker = {
            "id": "fighter",
            "name": "Fighter",
            "stats": {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
            "proficiency_bonus": 2,
            "proficient_skills": ["Athletics"],
            "active_conditions": [],
        }
        self.target = {
            "id": "orc",
            "name": "Orc",
            "size": "Medium",
            "stats": {"STR": 14, "DEX": 10, "CON": 12, "INT": 8, "WIS": 8, "CHA": 8},
            "proficiency_bonus": 2,
            "proficient_skills": [],
            "hp": {"current": 15, "max": 15},
            "active_conditions": [],
            "has_high_ground": True,
        }

    def test_shove_attacker_loses_clean_noop(self):
        # Attacker rolls 5 + STR mod 3 + prof 2 = 10
        # Target rolls 12 + STR mod 2 = 14 -> Target wins
        with patch("combat_manager._roll_d20", side_effect=[5, 12]):
            res = cm.resolve_shove(self.attacker, self.target, room_hazards=["near_chasm"])
            self.assertFalse(res["success"])
            self.assertEqual(res["damage"], 0)
            self.assertIsNone(res["condition_applied"])
            # State strictly unchanged
            self.assertEqual(self.target["hp"]["current"], 15)
            self.assertEqual(len(self.target["active_conditions"]), 0)
            self.assertTrue(self.target["has_high_ground"])

    def test_shove_prone_success(self):
        # Attacker rolls 15 + 5 = 20
        # Target rolls 8 + 2 = 10 -> Attacker wins
        with patch("combat_manager._roll_d20", side_effect=[15, 8]):
            res = cm.resolve_shove(self.attacker, self.target, shove_type="prone")
            self.assertTrue(res["success"])
            self.assertEqual(res["condition_applied"], "prone")
            self.assertEqual(self.target["active_conditions"][0]["condition"], "prone")
            # Prone strips high ground
            self.assertFalse(self.target["has_high_ground"])

    def test_shove_push_no_hazard(self):
        # Attacker rolls 15 + 5 = 20 vs 8 + 2 = 10
        with patch("combat_manager._roll_d20", side_effect=[15, 8]):
            res = cm.resolve_shove(self.attacker, self.target, shove_type="push")
            self.assertTrue(res["success"])
            self.assertEqual(res["damage"], 0)
            self.assertIsNone(res["hazard_triggered"])
            self.assertEqual(self.target["hp"]["current"], 15)
            # Pushed strips high ground
            self.assertFalse(self.target["has_high_ground"])

    def test_shove_chasm_medium_instant_death_on_failed_save(self):
        # Attacker rolls 16 + 5 = 21
        # Target contest rolls 6 + 2 = 8
        # Target DEX save: rolls 5 + DEX mod 0 = 5 vs DC 13 -> FAIL
        with patch("combat_manager._roll_d20", side_effect=[16, 6, 5]):
            res = cm.resolve_shove(self.attacker, self.target, room_hazards=["near_chasm"])
            self.assertTrue(res["success"])
            self.assertEqual(res["hazard_triggered"], "near_chasm")
            self.assertFalse(res["hazard_saved"])
            self.assertTrue(res["target_downed"])
            self.assertEqual(self.target["hp"]["current"], 0)
            self.assertEqual(res["damage_type"], "fall")

    def test_shove_chasm_large_takes_3d6_on_failed_save(self):
        self.target["size"] = "Large"
        self.target["hp"] = {"current": 30, "max": 30}
        # Attacker contest 16 + 5 = 21, Target contest 6 + 2 = 8, Target DEX save 6 + 0 = 6 (fail)
        with patch("combat_manager._roll_d20", side_effect=[16, 6, 6]), patch("combat_manager.roll_dice", return_value=12):
            res = cm.resolve_shove(self.attacker, self.target, room_hazards=["near_chasm"])
            self.assertTrue(res["success"])
            self.assertEqual(res["hazard_triggered"], "near_chasm")
            self.assertFalse(res["hazard_saved"])
            self.assertEqual(res["damage"], 12)
            self.assertEqual(res["damage_type"], "bludgeoning")
            self.assertEqual(self.target["hp"]["current"], 18)
            self.assertFalse(res["target_downed"])

    def test_shove_chasm_passed_save_avoids_fall(self):
        # Attacker contest 16 + 5 = 21, Target contest 6 + 2 = 8, Target DEX save 14 + 0 = 14 >= 13 (SUCCESS)
        with patch("combat_manager._roll_d20", side_effect=[16, 6, 14]):
            res = cm.resolve_shove(self.attacker, self.target, room_hazards=["near_chasm"])
            self.assertTrue(res["success"])
            self.assertEqual(res["hazard_triggered"], "near_chasm")
            self.assertTrue(res["hazard_saved"])
            self.assertEqual(res["damage"], 0)
            self.assertEqual(self.target["hp"]["current"], 15)

    def test_shove_acid_pool_deals_3d6_acid_on_failed_save(self):
        # Attacker contest 16 + 5 = 21, Target contest 6 + 2 = 8, Target DEX save 4 + 0 = 4 (fail)
        with patch("combat_manager._roll_d20", side_effect=[16, 6, 4]), patch("combat_manager.roll_dice", return_value=11):
            res = cm.resolve_shove(self.attacker, self.target, room_hazards=["acid_pool"])
            self.assertTrue(res["success"])
            self.assertEqual(res["hazard_triggered"], "acid_pool")
            self.assertFalse(res["hazard_saved"])
            self.assertEqual(res["damage"], 11)
            self.assertEqual(res["damage_type"], "acid")
            self.assertEqual(self.target["hp"]["current"], 4)

    def test_target_uses_higher_of_athletics_or_acrobatics(self):
        # Target with high DEX and Acrobatics proficiency
        acrobatic_target = {
            "id": "rogue",
            "name": "Rogue",
            "stats": {"STR": 8, "DEX": 18, "CON": 12, "INT": 12, "WIS": 12, "CHA": 10},
            "proficiency_bonus": 2,
            "proficient_skills": ["Acrobatics"],
            "hp": {"current": 12, "max": 12},
            "active_conditions": [],
        }
        # Attacker rolls 10 + 5 = 15
        # Rogue chooses Acrobatics: roll 10 + DEX mod 4 + prof 2 = 16 -> Rogue wins
        with patch("combat_manager._roll_d20", side_effect=[10, 10]):
            res = cm.resolve_shove(self.attacker, acrobatic_target)
            self.assertEqual(res["target_skill"], "Acrobatics")
            self.assertFalse(res["success"])


class TestShoveRoundIntegration(unittest.TestCase):
    def test_resolve_round_with_shove_narration_and_classification(self):
        player_state = {
            "name": "Hero",
            "stats": {"STR": 16, "DEX": 14, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
            "hp": {"current": 20, "max": 20},
            "ac": 16,
            "active_conditions": [],
            "death_saves": {"success": 0, "fail": 0},
            "inventory": [],
            "spell_slots": {},
            "status": "normal",
            "gold": 50,
            "xp_current": 0,
            "level": 1,
            "proficiency_bonus": 2,
            "proficient_skills": ["Athletics"],
        }
        world_state = {"current_location": "ruined_mill"}
        cs = cm.start_combat(["goblin_scout"], player_state, world_state)
        cs["room_hazards"] = ["near_chasm"]
        enemy = cs["enemies"][0]

        # Player shoves goblin into chasm (instant death)
        with patch("combat_manager._roll_d20", side_effect=[18, 5, 4]):
            shove_res = cm.resolve_shove(cs["player_combatant"], enemy, combat_state=cs)
            self.assertTrue(shove_res["target_downed"])

        round_res = cm.resolve_round(cs, player_attack_result=shove_res, world_state=world_state)
        # Check round classification
        sig = round_res["significant"]
        self.assertTrue(any(e.get("action_type") == "shove" for e in sig))
        # Check narration block includes shove and chasm death
        narration_block = round_res["narration_block"]
        self.assertIn("plunges to their death", narration_block)
        # Combat ends in victory because last enemy died
        self.assertEqual(round_res["combat_outcome"], "player_victory")


if __name__ == "__main__":
    unittest.main()
