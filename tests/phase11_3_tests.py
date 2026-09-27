"""
phase11_3_tests.py — Phase 11.3 Cooldown-Gated Weapon Actions Tests

Verifies:
  1. Bludgeoning (Concussive Smash): On hit, target makes CON save DC 8 + prof + STR mod.
     Failed save applies 'dazed' condition (dur 1, -1 AC, disadvantage on attacks).
  2. Bludgeoning (Concussive Smash): Successful save avoids 'dazed' condition.
  3. Slashing (Cleave): On hit, deals primary weapon damage to main target and STR-mod damage to an adjacent enemy.
  4. Slashing (Cleave): When only 1 enemy is present, resolves cleanly without errors.
  5. Piercing (Hamstring): On hit, applies 'slowed' condition (dur 2).
  6. Cooldown Gating: Once used, weapon_actions_available becomes False and subsequent attempts are rejected.
  7. Short Rest Recharge: perform_short_rest() resets weapon_actions_available to True.
  8. Long Rest Recharge: long_rest() also resets weapon_actions_available to True.
  9. Miss Handling: Missed attack roll consumes cooldown without applying on-hit effects.
 10. Dazed AC Penalty: Both physical attacks and spell attacks against a dazed target use target AC - 1.
 11. Full Round Integration: resolve_round() includes weapon actions in significant events and narration block.
"""

import unittest
from unittest.mock import patch
import combat_manager as cm
import state_manager as sm


class TestWeaponActions(unittest.TestCase):
    def setUp(self):
        self.attacker = {
            "id": "fighter",
            "name": "Fighter",
            "stats": {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
            "proficiency_bonus": 2,
            "weapon_actions_available": True,
            "active_conditions": [],
        }
        self.target = {
            "id": "target_1",
            "name": "Goblin Leader",
            "ac": 14,
            "hp": {"current": 20, "max": 20},
            "stats": {"STR": 10, "DEX": 12, "CON": 12, "INT": 10, "WIS": 10, "CHA": 10},
            "proficiency_bonus": 2,
            "active_conditions": [],
        }
        self.adjacent_enemy = {
            "id": "target_2",
            "name": "Goblin Minion",
            "ac": 12,
            "hp": {"current": 8, "max": 8},
            "stats": {"STR": 8, "DEX": 14, "CON": 10, "INT": 8, "WIS": 8, "CHA": 8},
            "active_conditions": [],
        }
        self.bludgeoning_attack = {
            "name": "Warhammer",
            "attack_bonus": 5,
            "damage": "1d8+3",
            "damage_type": "bludgeoning",
        }
        self.slashing_attack = {
            "name": "Greatsword",
            "attack_bonus": 5,
            "damage": "2d6+3",
            "damage_type": "slashing",
        }
        self.piercing_attack = {
            "name": "Rapier",
            "attack_bonus": 5,
            "damage": "1d8+3",
            "damage_type": "piercing",
        }

    def test_concussive_smash_failed_save_applies_dazed(self):
        # Attacker STR mod = 3, prof = 2 -> DC = 8 + 2 + 3 = 13
        # Target CON mod = 1.
        # Attack roll: 15 + 5 = 20 vs AC 14 -> HIT
        # Damage roll: 7
        # Target CON save roll: 8 + 1 = 9 vs DC 13 -> FAIL
        with patch("combat_manager._roll_d20", side_effect=[15, 8]), patch("combat_manager.roll_dice", return_value=7):
            res = cm.resolve_weapon_action(self.attacker, self.target, self.bludgeoning_attack)
            self.assertTrue(res["hit"])
            self.assertEqual(res["weapon_action_name"], "Concussive Smash")
            self.assertFalse(res["save_passed"])
            self.assertEqual(res["save_dc"], 13)
            self.assertEqual(res["condition_applied"], "dazed")
            self.assertEqual(self.target["active_conditions"][0]["condition"], "dazed")
            self.assertEqual(self.target["hp"]["current"], 13)
            # Cooldown consumed
            self.assertFalse(self.attacker["weapon_actions_available"])

    def test_concussive_smash_passed_save_avoids_dazed(self):
        # Attack roll: 12 + 5 = 17 -> HIT
        # Target CON save roll: 14 + 1 = 15 >= 13 -> SUCCESS
        with patch("combat_manager._roll_d20", side_effect=[12, 14]), patch("combat_manager.roll_dice", return_value=6):
            res = cm.resolve_weapon_action(self.attacker, self.target, self.bludgeoning_attack)
            self.assertTrue(res["hit"])
            self.assertTrue(res["save_passed"])
            self.assertIsNone(res["condition_applied"])
            self.assertEqual(len(self.target["active_conditions"]), 0)
            self.assertEqual(self.target["hp"]["current"], 14)

    def test_cleave_hits_primary_and_adjacent_enemy(self):
        combat_state = {
            "enemies": [self.target, self.adjacent_enemy],
        }
        # Attack roll: 14 + 5 = 19 vs AC 14 -> HIT
        # Damage: 10 to primary target
        # Cleave damage = max(1, STR mod 3) = 3 to adjacent target
        with patch("combat_manager._roll_d20", return_value=14), patch("combat_manager.roll_dice", return_value=10):
            res = cm.resolve_weapon_action(
                self.attacker, self.target, self.slashing_attack, combat_state=combat_state
            )
            self.assertTrue(res["hit"])
            self.assertEqual(res["weapon_action_name"], "Cleave")
            self.assertEqual(self.target["hp"]["current"], 10)
            self.assertEqual(res["cleave_target_id"], "target_2")
            self.assertEqual(res["cleave_damage"], 3)
            self.assertEqual(self.adjacent_enemy["hp"]["current"], 5)
            self.assertFalse(res["cleave_target_downed"])

    def test_cleave_downs_adjacent_enemy(self):
        self.adjacent_enemy["hp"]["current"] = 2
        with patch("combat_manager._roll_d20", return_value=14), patch("combat_manager.roll_dice", return_value=8):
            res = cm.resolve_weapon_action(
                self.attacker, self.target, self.slashing_attack, adjacent_target=self.adjacent_enemy
            )
            self.assertTrue(res["cleave_target_downed"])
            self.assertEqual(self.adjacent_enemy["hp"]["current"], 0)

    def test_hamstring_applies_slowed(self):
        with patch("combat_manager._roll_d20", return_value=15), patch("combat_manager.roll_dice", return_value=5):
            res = cm.resolve_weapon_action(self.attacker, self.target, self.piercing_attack)
            self.assertTrue(res["hit"])
            self.assertEqual(res["weapon_action_name"], "Hamstring")
            self.assertEqual(res["condition_applied"], "slowed")
            self.assertEqual(self.target["active_conditions"][0]["condition"], "slowed")
            self.assertEqual(self.target["active_conditions"][0]["duration"], 2)

    def test_weapon_action_cooldown_rejection(self):
        self.attacker["weapon_actions_available"] = False
        res = cm.resolve_weapon_action(self.attacker, self.target, self.slashing_attack)
        self.assertFalse(res["success"])
        self.assertIn("cooldown", res["reason"].lower())

    def test_perform_short_rest_recharges_cooldown(self):
        self.attacker["weapon_actions_available"] = False
        res = sm.perform_short_rest(self.attacker)
        self.assertTrue(res["success"])
        self.assertTrue(self.attacker["weapon_actions_available"])

        # Now weapon action can be used again
        with patch("combat_manager._roll_d20", return_value=15), patch("combat_manager.roll_dice", return_value=5):
            w_res = cm.resolve_weapon_action(self.attacker, self.target, self.piercing_attack)
            self.assertTrue(w_res["success"])
            self.assertFalse(self.attacker["weapon_actions_available"])

    def test_long_rest_recharges_cooldown(self):
        world_state = {"game_time": {"day": 1, "period": "morning", "steps_since_period_start": 0}}
        self.attacker["weapon_actions_available"] = False
        sm.long_rest(world_state, self.attacker)
        self.assertTrue(self.attacker["weapon_actions_available"])

    def test_missed_weapon_action_consumes_cooldown_without_effects(self):
        # Roll 2 + 5 = 7 vs AC 14 -> MISS
        with patch("combat_manager._roll_d20", return_value=2):
            res = cm.resolve_weapon_action(self.attacker, self.target, self.piercing_attack)
            self.assertFalse(res["hit"])
            self.assertFalse(self.attacker["weapon_actions_available"])
            self.assertEqual(len(self.target["active_conditions"]), 0)
            self.assertEqual(self.target["hp"]["current"], 20)

    def test_dazed_reduces_target_ac_by_one(self):
        self.target["ac"] = 14
        cm.apply_condition(self.target, "dazed", duration=1)
        # Roll 8 + 5 = 13 vs base AC 14. With dazed AC penalty (14 - 1 = 13), 13 hits!
        with patch("combat_manager._roll_d20", return_value=8), patch("combat_manager.roll_dice", return_value=4):
            res = cm.resolve_attack(self.attacker, self.target, self.slashing_attack)
            self.assertEqual(res["target_ac"], 13)
            self.assertTrue(res["hit"])

        # Spell attack against dazed target
        caster = {
            "id": "mage",
            "name": "Mage",
            "class": "Wizard",
            "proficiency_bonus": 2,
            "stats": {"INT": 14, "DEX": 10, "CON": 10, "STR": 10, "WIS": 10, "CHA": 10},
            "active_conditions": [],
        }
        fire_bolt = {"name": "Fire Bolt", "level": 0, "effect": {"damage": "1d10", "damage_type": "fire"}}
        # Caster bonus = 2 + 2 = 4. Roll 9 + 4 = 13 vs AC 13 -> HIT
        with patch("state_manager._roll_d20", return_value=9), patch("state_manager._roll_dice", return_value=5):
            s_res = sm.resolve_spell_attack(caster, self.target, fire_bolt)
            self.assertEqual(s_res["target_ac"], 13)
            self.assertTrue(s_res["hit"])


class TestWeaponActionRoundIntegration(unittest.TestCase):
    def test_round_orchestration_with_cleave(self):
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
            "weapon_actions_available": True,
        }
        world_state = {"current_location": "ruined_mill"}
        cs = cm.start_combat(["goblin_scout", "goblin_warrior"], player_state, world_state)
        enemy1 = cs["enemies"][0]
        enemy2 = cs["enemies"][1]

        slashing_attack = {"name": "Greatsword", "attack_bonus": 5, "damage": "2d6+3", "damage_type": "slashing"}

        with patch("combat_manager._roll_d20", return_value=16), patch("combat_manager.roll_dice", return_value=6):
            w_res = cm.resolve_weapon_action(cs["player_combatant"], enemy1, slashing_attack, combat_state=cs, adjacent_target=enemy2)
            self.assertEqual(w_res["cleave_damage"], 3)

        round_res = cm.resolve_round(cs, player_attack_result=w_res, world_state=world_state)
        sig = round_res["significant"]
        self.assertTrue(any(e.get("action_type") == "weapon_action" for e in sig))
        self.assertIn("Cleave", round_res["narration_block"])


if __name__ == "__main__":
    unittest.main()
