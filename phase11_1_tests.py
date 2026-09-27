"""
phase11_1_tests.py — Unit test suite for Phase 11.1 Surface System (combat_manager.py)

DoD Verification:
1. Ignite grease-covered room (grease + fire):
   - Force DEX save to fail (monkeypatch _roll_d20 -> 1)
   - Confirm living combatants take 2d4 fire damage (2-8)
   - Confirm living combatants have 'burning' in active_conditions via direct dict inspection
   - Confirm room_surface becomes {"type": "fire", "duration": 3}
2. Surface expiry:
   - Apply surface duration 3
   - Call tick_surface() 3 times
   - Confirm room_surface == {"type": None, "duration": 0} after 3rd call, not before
3. Fire + Water combo:
   - Order-independent: water then fire AND fire then water
   - Clears room_surface to {"type": None, "duration": 0}
   - Sets smoke_active = True, smoke_duration = 3
   - Confirms ranged attacks in smoke suffer disadvantage, melee does not
4. Lightning reaction (water + lightning):
   - Safe no-op when room_surface is not 'water'
   - When 'water', transforms surface to 'electrified_water' (duration 3)
   - Living combatants roll CON save vs DC 13
   - On fail: takes 1d6 lightning damage, gains 'dazed' condition (duration 1)
   - Confirms 'dazed' attacker suffers disadvantage on attack rolls
5. Integration with resolve_attack(), state_manager spellcasting, and resolve_round()
"""

import copy
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import combat_manager as cm
import state_manager as sm


class TestPhase11_1SurfaceSystem(unittest.TestCase):

    def _build_combat_state(self):
        player_state = {
            "name": "Valeros",
            "hp": {"current": 20, "max": 20},
            "stats": {"STR": 14, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
            "ac": 14,
            "active_conditions": [],
            "death_saves": {"success": 0, "fail": 0},
            "status": "normal",
            "inventory": [],
            "gold": 0,
            "xp_current": 0,
            "level": 1,
            "proficiency_bonus": 2,
            "spell_slots": {},
        }
        player_c = {
            "id": "player",
            "name": "Valeros",
            "side": "player",
            "hp": player_state["hp"],
            "ac": 14,
            "stats": player_state["stats"],
            "attacks": [{"name": "Shortsword", "attack_bonus": 4, "damage": "1d6+2", "damage_type": "slashing", "ranged": False}],
            "active_conditions": player_state["active_conditions"],
            "death_saves": player_state["death_saves"],
            "inventory": player_state["inventory"],
            "spell_slots": player_state["spell_slots"],
            "_player_state": player_state,
        }
        enemy_c = {
            "id": "goblin_1",
            "name": "Goblin",
            "side": "enemy",
            "hp": {"current": 15, "max": 15},
            "ac": 12,
            "stats": {"STR": 8, "DEX": 10, "CON": 10, "INT": 10, "WIS": 8, "CHA": 8},
            "attacks": [{"name": "Scimitar", "attack_bonus": 4, "damage": "1d6+2", "damage_type": "slashing", "ranged": False}],
            "active_conditions": [],
        }
        companion_c = {
            "id": "companion_1",
            "name": "Lydia",
            "side": "player",
            "hp": {"current": 18, "max": 18},
            "ac": 13,
            "stats": {"STR": 12, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
            "attacks": [{"name": "Mace", "attack_bonus": 3, "damage": "1d6+1", "damage_type": "bludgeoning", "ranged": False}],
            "active_conditions": [],
        }
        downed_enemy = {
            "id": "goblin_dead",
            "name": "Dead Goblin",
            "side": "enemy",
            "hp": {"current": 0, "max": 10},
            "ac": 10,
            "stats": {"STR": 8, "DEX": 10, "CON": 10, "INT": 8, "WIS": 8, "CHA": 8},
            "attacks": [],
            "active_conditions": [],
        }

        return {
            "round": 1,
            "turn_index": 0,
            "turn_order": ["player", "companion_1", "goblin_1", "goblin_dead"],
            "player_combatant": player_c,
            "enemies": [enemy_c, downed_enemy],
            "companions": [companion_c],
            "round_log": [],
            "status": "active",
            "outcome": None,
            "room_surface": {"type": None, "duration": 0},
            "smoke_active": False,
            "smoke_duration": 0,
        }

    # =========================================================================
    # DoD 1: Grease + Fire ignition combo
    # =========================================================================
    def test_dod1_grease_fire_ignition_failed_save(self):
        """Simulate igniting grease with failed DEX save: 2d4 fire damage + burning condition."""
        cs = self._build_combat_state()
        cs["room_surface"] = {"type": "grease", "duration": 2}

        # Monkeypatch _roll_d20 to return 1 -> DEX save total is 1 + 0 = 1 vs DC 12 (FAILS)
        with patch.object(cm, "_roll_d20", return_value=1):
            res = cm.apply_surface(cs, "fire")

        # 1. Surface check via direct dict inspection
        self.assertEqual(cs["room_surface"], {"type": "fire", "duration": 3})
        self.assertTrue(res["combo_triggered"])
        self.assertEqual(res["combo"], "grease_fire")

        # 2. Living combatants take damage in 2d4 range (2-8)
        p_c = cs["player_combatant"]
        e_c = cs["enemies"][0]
        comp_c = cs["companions"][0]
        dead_e = cs["enemies"][1]

        self.assertTrue(12 <= p_c["hp"]["current"] <= 18, f"Player HP unexpected: {p_c['hp']['current']}")
        self.assertTrue(7 <= e_c["hp"]["current"] <= 13, f"Enemy HP unexpected: {e_c['hp']['current']}")
        self.assertTrue(10 <= comp_c["hp"]["current"] <= 16, f"Companion HP unexpected: {comp_c['hp']['current']}")
        self.assertEqual(dead_e["hp"]["current"], 0, "Downed enemy should remain at 0 HP")

        # 3. Direct dict inspection of active_conditions for 'burning'
        p_burning = [c for c in p_c["active_conditions"] if c["condition"] == "burning"]
        self.assertEqual(len(p_burning), 1)
        self.assertEqual(p_burning[0]["duration"], 3)

        e_burning = [c for c in e_c["active_conditions"] if c["condition"] == "burning"]
        self.assertEqual(len(e_burning), 1)
        self.assertEqual(e_burning[0]["duration"], 3)

        comp_burning = [c for c in comp_c["active_conditions"] if c["condition"] == "burning"]
        self.assertEqual(len(comp_burning), 1)
        self.assertEqual(comp_burning[0]["duration"], 3)

        dead_burning = [c for c in dead_e["active_conditions"] if c["condition"] == "burning"]
        self.assertEqual(len(dead_burning), 0, "Downed enemy should not get burning condition")

    def test_dod1_grease_fire_ignition_successful_save(self):
        """Simulate igniting grease with successful DEX save: 0 damage and no burning condition."""
        cs = self._build_combat_state()
        cs["room_surface"] = {"type": "grease", "duration": 2}

        # Monkeypatch _roll_d20 to return 20 -> DEX save total is 20 + 0 = 20 vs DC 12 (SUCCEEDS)
        with patch.object(cm, "_roll_d20", return_value=20):
            res = cm.apply_surface(cs, "fire")

        self.assertEqual(cs["room_surface"], {"type": "fire", "duration": 3})
        self.assertTrue(res["combo_triggered"])

        p_c = cs["player_combatant"]
        self.assertEqual(p_c["hp"]["current"], 20)
        self.assertEqual(len(p_c["active_conditions"]), 0)

    # =========================================================================
    # DoD 2: Surface expiry
    # =========================================================================
    def test_dod2_surface_expiry(self):
        """Apply surface duration 3, call tick_surface() 3 times -> expires only on 3rd call."""
        cs = self._build_combat_state()
        cm.apply_surface(cs, "fire", duration=3)
        self.assertEqual(cs["room_surface"], {"type": "fire", "duration": 3})

        # Tick 1
        cm.tick_surface(cs)
        self.assertEqual(cs["room_surface"], {"type": "fire", "duration": 2})

        # Tick 2
        cm.tick_surface(cs)
        self.assertEqual(cs["room_surface"], {"type": "fire", "duration": 1})

        # Tick 3 -> expires!
        cm.tick_surface(cs)
        self.assertEqual(cs["room_surface"], {"type": None, "duration": 0})

    # =========================================================================
    # DoD 3: Fire + Water combo (smoke & ranged attack disadvantage)
    # =========================================================================
    def test_dod3_fire_water_combo_order_independence(self):
        """Test both water then fire, AND fire then water."""
        # Order A: water then fire
        cs_a = self._build_combat_state()
        cs_a["room_surface"] = {"type": "water", "duration": 3}
        res_a = cm.apply_surface(cs_a, "fire")

        self.assertTrue(res_a["combo_triggered"])
        self.assertEqual(res_a["combo"], "fire_water")
        self.assertEqual(cs_a["room_surface"], {"type": None, "duration": 0})
        self.assertTrue(cs_a["smoke_active"])
        self.assertEqual(cs_a["smoke_duration"], 3)

        # Order B: fire then water
        cs_b = self._build_combat_state()
        cs_b["room_surface"] = {"type": "fire", "duration": 3}
        res_b = cm.apply_surface(cs_b, "water")

        self.assertTrue(res_b["combo_triggered"])
        self.assertEqual(res_b["combo"], "fire_water")
        self.assertEqual(cs_b["room_surface"], {"type": None, "duration": 0})
        self.assertTrue(cs_b["smoke_active"])
        self.assertEqual(cs_b["smoke_duration"], 3)

    def test_dod3_smoke_disadvantage_on_ranged_attacks_only(self):
        """Smoke causes disadvantage on ranged attacks, but not melee attacks."""
        cs = self._build_combat_state()
        cs["smoke_active"] = True
        cs["smoke_duration"] = 3

        attacker = cs["player_combatant"]
        target = cs["enemies"][0]

        ranged_attack = {
            "name": "Longbow",
            "attack_bonus": 5,
            "damage": "1d8",
            "damage_type": "piercing",
            "ranged": True,
        }
        melee_attack = {
            "name": "Greatsword",
            "attack_bonus": 5,
            "damage": "2d6",
            "damage_type": "slashing",
            "ranged": False,
        }

        # Resolve ranged attack in smoke -> disadvantage (rolls 15 and 5, takes min = 5)
        with patch.object(cm, "_roll_d20", side_effect=[15, 5]):
            res_ranged = cm.resolve_attack(attacker, target, ranged_attack, combat_state=cs)
        self.assertEqual(res_ranged["raw_roll"], 5)

        # Resolve melee attack in smoke -> normal (single roll 15)
        with patch.object(cm, "_roll_d20", side_effect=[15, 5]):
            res_melee = cm.resolve_attack(attacker, target, melee_attack, combat_state=cs)
        self.assertEqual(res_melee["raw_roll"], 15)

    def test_dod3_smoke_ticking_and_expiry(self):
        """Smoke duration decrements with tick_surface() and expires after 3 ticks."""
        cs = self._build_combat_state()
        cs["smoke_active"] = True
        cs["smoke_duration"] = 3

        cm.tick_surface(cs)
        self.assertTrue(cs["smoke_active"])
        self.assertEqual(cs["smoke_duration"], 2)

        cm.tick_surface(cs)
        self.assertTrue(cs["smoke_active"])
        self.assertEqual(cs["smoke_duration"], 1)

        cm.tick_surface(cs)
        self.assertFalse(cs["smoke_active"])
        self.assertEqual(cs["smoke_duration"], 0)

    # =========================================================================
    # DoD 4: Water + Lightning reaction
    # =========================================================================
    def test_dod4_lightning_reaction_no_op_without_water(self):
        """trigger_lightning_surface_reaction() is safe no-op if surface is not 'water'."""
        # Case A: surface is None
        cs1 = self._build_combat_state()
        res1 = cm.trigger_lightning_surface_reaction(cs1)
        self.assertFalse(res1["combo_triggered"])
        self.assertEqual(cs1["room_surface"], {"type": None, "duration": 0})

        # Case B: surface is 'fire'
        cs2 = self._build_combat_state()
        cs2["room_surface"] = {"type": "fire", "duration": 2}
        res2 = cm.trigger_lightning_surface_reaction(cs2)
        self.assertFalse(res2["combo_triggered"])
        self.assertEqual(cs2["room_surface"], {"type": "fire", "duration": 2})

    def test_dod4_lightning_reaction_with_water_failed_save(self):
        """When water is present, lightning creates electrified_water, deals 1d6 damage, applies dazed."""
        cs = self._build_combat_state()
        cs["room_surface"] = {"type": "water", "duration": 2}

        # Monkeypatch _roll_d20 to return 1 -> CON save is 1 + 0 = 1 vs DC 13 (FAILS)
        with patch.object(cm, "_roll_d20", return_value=1):
            res = cm.trigger_lightning_surface_reaction(cs)

        self.assertTrue(res["combo_triggered"])
        self.assertEqual(cs["room_surface"], {"type": "electrified_water", "duration": 3})

        p_c = cs["player_combatant"]
        self.assertTrue(14 <= p_c["hp"]["current"] <= 19, f"Player HP unexpected: {p_c['hp']['current']}")

        p_dazed = [c for c in p_c["active_conditions"] if c["condition"] == "dazed"]
        self.assertEqual(len(p_dazed), 1)
        self.assertEqual(p_dazed[0]["duration"], 1)

    def test_dod4_dazed_condition_causes_disadvantage(self):
        """Attacker with 'dazed' condition makes attack rolls with disadvantage."""
        cs = self._build_combat_state()
        attacker = cs["player_combatant"]
        target = cs["enemies"][0]
        cm.apply_condition(attacker, "dazed", duration=1)

        melee_attack = {
            "name": "Shortsword",
            "attack_bonus": 4,
            "damage": "1d6+2",
            "damage_type": "slashing",
            "ranged": False,
        }
        # With disadvantage, rolls [15, 5] -> takes min = 5
        with patch.object(cm, "_roll_d20", side_effect=[15, 5]):
            res = cm.resolve_attack(attacker, target, melee_attack, combat_state=cs)
        self.assertEqual(res["raw_roll"], 5)

    # =========================================================================
    # DoD 5 & Extra: Combat / Spell integration & Edge Cases
    # =========================================================================
    def test_resolve_attack_lightning_triggers_surface_reaction(self):
        """An attack dealing lightning damage in a water room triggers electrified_water."""
        cs = self._build_combat_state()
        cs["room_surface"] = {"type": "water", "duration": 2}

        attacker = cs["player_combatant"]
        target = cs["enemies"][0]

        lightning_attack = {
            "name": "Shock Dagger",
            "attack_bonus": 10,
            "damage": "1d4+2",
            "damage_type": "lightning",
            "ranged": False,
        }

        # Force hit
        with patch.object(cm, "_roll_d20", return_value=15):
            res = cm.resolve_attack(attacker, target, lightning_attack, combat_state=cs)

        self.assertTrue(res["hit"])
        self.assertEqual(cs["room_surface"]["type"], "electrified_water")

    def test_state_manager_cast_spell_lightning_triggers_surface_reaction(self):
        """Casting a lightning spell via state_manager triggers electrified_water in combat_state."""
        cs = self._build_combat_state()
        cs["room_surface"] = {"type": "water", "duration": 2}

        world_state = {
            "combat_state": cs,
            "character_state": {
                "spell_slots": {"1": {"max": 2, "current": 2}},
                "stats": {"INT": 14},
                "proficiency_bonus": 2,
            },
        }

        caster = cs["player_combatant"]
        target = cs["enemies"][0]

        # Use shocking_grasp from spell catalog
        with patch.object(sm, "_roll_dice", return_value=5), patch.object(sm, "_roll_d20", return_value=15):
            res = sm.cast_spell(caster, target, "shocking_grasp", world_state["character_state"], world_state=world_state)

        self.assertTrue(res.get("hit", False))
        self.assertEqual(cs["room_surface"]["type"], "electrified_water")

    def test_invalid_surface_rejected(self):
        """Invalid surface name rejected without error and without modifying existing surface."""
        cs = self._build_combat_state()
        cs["room_surface"] = {"type": "water", "duration": 2}

        res = cm.apply_surface(cs, "invalid_acid_surface")
        self.assertFalse(res["combo_triggered"])
        self.assertEqual(cs["room_surface"], {"type": "water", "duration": 2})

    def test_explicit_surface_clear(self):
        """Applying None surface clears room_surface."""
        cs = self._build_combat_state()
        cs["room_surface"] = {"type": "blood", "duration": 2}

        res = cm.apply_surface(cs, None)
        self.assertFalse(res["combo_triggered"])
        self.assertEqual(cs["room_surface"], {"type": None, "duration": 0})

    def test_non_combo_surface_overwrite(self):
        """Applying blood over water cleanly overwrites without combo."""
        cs = self._build_combat_state()
        cs["room_surface"] = {"type": "water", "duration": 2}

        res = cm.apply_surface(cs, "blood", duration=3)
        self.assertFalse(res["combo_triggered"])
        self.assertEqual(cs["room_surface"], {"type": "blood", "duration": 3})

    def test_start_combat_initializes_surface_fields(self):
        """start_combat() sets room_surface and smoke fields to defaults."""
        world_state = {"combat_state": None}
        player_state = {
            "name": "Hero",
            "hp": {"current": 10, "max": 10},
            "stats": {"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
            "ac": 10,
        }
        cs = cm.start_combat(["goblin_scout"], player_state, world_state)
        self.assertIsNotNone(cs)
        self.assertEqual(cs.get("room_surface"), {"type": None, "duration": 0})
        self.assertFalse(cs.get("smoke_active"))
        self.assertEqual(cs.get("smoke_duration"), 0)

    def test_resolve_round_ticks_surface_and_conditions(self):
        """resolve_round() ticks down surface duration at the end of each round."""
        cs = self._build_combat_state()
        cs["room_surface"] = {"type": "fire", "duration": 2}
        cs["smoke_active"] = True
        cs["smoke_duration"] = 2

        # Round 1 action
        player_action = {"type": "attack", "target_id": "goblin_1", "attack_index": 0}

        # Mock rolls to avoid killing anyone
        with patch.object(cm, "_roll_d20", return_value=5):
            res_round = cm.resolve_round(cs, player_attack_result=player_action)

        # After round ends, duration should be 1
        self.assertEqual(cs["room_surface"], {"type": "fire", "duration": 1})
        self.assertTrue(cs["smoke_active"])
        self.assertEqual(cs["smoke_duration"], 1)

    def test_resolve_round_defensive_guard_recovers_positional_world_state(self):
        """Passing world_state as 2nd positional arg is cleanly intercepted by the defensive guard."""
        cs = self._build_combat_state()
        world_dict = {"schema_version": 4, "current_location": "forest_edge"}

        with patch.object(cm, "_roll_d20", return_value=5):
            res_round = cm.resolve_round(cs, world_dict)

        # Confirm round_log does NOT contain the world_dict
        latest_log = cs["round_log"][-1]
        for entry in latest_log:
            self.assertNotEqual(entry, world_dict)
            self.assertIn("attacker_id", entry)

    def test_resolve_round_with_real_player_attack_result(self):
        """Player attack generated by resolve_attack() is cleanly integrated into resolve_round narration."""
        cs = self._build_combat_state()
        p_c = cs["player_combatant"]
        target_e = cs["enemies"][0]
        attack = p_c["attacks"][0]

        # Force hit
        with patch.object(cm, "_roll_d20", return_value=15):
            player_atk = cm.resolve_attack(p_c, target_e, attack, combat_state=cs)

        self.assertTrue(player_atk["hit"])
        self.assertGreater(player_atk["damage"], 0)

        with patch.object(cm, "_roll_d20", return_value=5):
            res_round = cm.resolve_round(cs, player_attack_result=player_atk)

        narration = res_round["narration_block"]
        self.assertIn(p_c["name"], narration)
        self.assertIn(target_e["name"], narration)
        self.assertNotIn("?'s attack goes wide", narration)


if __name__ == "__main__":
    unittest.main()

