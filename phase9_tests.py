"""
phase9_tests.py — Test suite for Phase 9 (Remaining Systems)
Systems tested:
1. XP / Leveling (award_xp, check_level_up, apply_level_up, combat_manager integration, multi-level jump, level 5 cap)
2. Spellcasting (resolve_spell_save, resolve_spell_attack, cast_spell, spell slot deduction)
3. Status Effects (apply_condition immunity rejection, effect application, tick expiry)
4. Companion Dismissal (dismiss_companion moving to former_companions, recording last_location, combat rejection)
5. Lazy Item Generation (resolve_item catalog fallback, generate_item rarity determinism, validate_item_id)
"""

import json
import os
import unittest
from unittest.mock import patch

import combat_manager
import dungeon_manager
import state_manager
import validation


class TestPhase9Systems(unittest.TestCase):

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
            "party": {"companions": [{"id": "goblin_friend", "name": "Grib", "hp": {"current": 8, "max": 8}}], "former_companions": []},
            "generated_items": {},
            "combat_state": None,
        }

        self.character_state = {
            "schema_version": 4,
            "name": "Hero",
            "race": "Human",
            "class": "Bard",
            "level": 1,
            "xp_current": 0,
            "stats": {"STR": 10, "DEX": 14, "CON": 14, "INT": 10, "WIS": 10, "CHA": 16},
            "hp": {"current": 10, "max": 10},
            "ac": 14,
            "gold": 100,
            "proficiency_bonus": 2,
            "proficient_skills": ["Persuasion", "Performance"],
            "proficient_saves": ["DEX", "CHA"],
            "spell_slots": {"1": {"max": 2, "current": 2}},
            "known_spells": ["vicious_mockery", "healing_word"],
            "status": "normal",
            "active_conditions": [],
            "death_saves": {"success": 0, "fail": 0},
            "inventory": [
                {"item_id": "longsword", "equipped": True, "quantity": 1},
            ],
        }

    # ══════════════════════════════════════════════════════════════════════════
    # 1. XP / LEVELING TESTS
    # ══════════════════════════════════════════════════════════════════════════

    def test_award_xp_and_level_up(self):
        # 1. Award 300 XP (threshold for Level 2)
        state_manager.award_xp(300, self.character_state)
        self.assertEqual(self.character_state["xp_current"], 300)
        self.assertTrue(state_manager.check_level_up(self.character_state))

        # 2. Apply level up
        res = state_manager.apply_level_up(self.character_state)
        self.assertTrue(res["leveled_up"])
        self.assertEqual(self.character_state["level"], 2)
        self.assertEqual(self.character_state["hp"]["max"], 17) # 10 + avg 5 + CON mod 2 = 17
        self.assertEqual(self.character_state["hp"]["current"], 17)
        self.assertEqual(self.character_state["proficiency_bonus"], 2)
        self.assertEqual(self.character_state["spell_slots"]["1"]["max"], 3)

    def test_multi_level_threshold_crossing(self):
        # Award 3000 XP in one go (crosses Level 1 -> 2 -> 3 -> 4 at once)
        state_manager.award_xp(3000, self.character_state)
        self.assertEqual(self.character_state["xp_current"], 3000)

        leveled_count = 0
        while state_manager.check_level_up(self.character_state):
            res = state_manager.apply_level_up(self.character_state)
            self.assertTrue(res["leveled_up"])
            leveled_count += 1

        self.assertEqual(leveled_count, 3)
        self.assertEqual(self.character_state["level"], 4)
        self.assertEqual(self.character_state["spell_slots"]["1"]["max"], 4)
        self.assertEqual(self.character_state["spell_slots"]["2"]["max"], 3)

    def test_award_xp_negative_or_zero(self):
        state_manager.award_xp(0, self.character_state)
        self.assertEqual(self.character_state["xp_current"], 0)
        state_manager.award_xp(-100, self.character_state)
        self.assertEqual(self.character_state["xp_current"], 0)

    def test_level_cap_at_level_5(self):
        self.character_state["level"] = 5
        state_manager.award_xp(10000, self.character_state)
        self.assertFalse(state_manager.check_level_up(self.character_state))
        res = state_manager.apply_level_up(self.character_state)
        self.assertFalse(res["leveled_up"])
        self.assertEqual(self.character_state["level"], 5)

    def test_xp_award_real_combat_orchestration_integration(self):
        # Fights 2 goblin scouts (50 XP each = 100 XP total)
        combat_manager.start_combat(["goblin_scout", "goblin_scout"], self.character_state, self.world_state)
        cs = self.world_state["combat_state"]
        self.world_state["player_state"] = self.character_state

        # Force all enemies to 0 HP
        for e in cs["enemies"]:
            e["hp"]["current"] = 0

        # Check combat end
        outcome = combat_manager.check_combat_end(cs, self.world_state)
        self.assertEqual(outcome, "player_victory")
        self.assertEqual(cs["xp_gained"], 100) # 2 * 50 = 100
        self.assertEqual(self.character_state["xp_current"], 100)

    # ══════════════════════════════════════════════════════════════════════════
    # 2. SPELLCASTING TESTS
    # ══════════════════════════════════════════════════════════════════════════

    def test_resolve_spell_save_dc_and_target_roll(self):
        # Bard CHA 16 (+3), prof +2 -> DC = 8 + 2 + 3 = 13
        spell = {
            "name": "Vicious Mockery",
            "level": 0,
            "type": "attack_save",
            "save_stat": "WIS",
            "effect": {"damage": "1d4"},
            "on_fail_extra": "disadvantage_next_attack",
        }
        target = {"name": "Goblin", "stats": {"WIS": 10}, "hp": {"current": 10, "max": 10}}

        # Force target d20 roll = 5 (+0 mod = 5 < DC 13) -> Failure
        with patch("state_manager._roll_d20", return_value=5):
            res = state_manager.resolve_spell_save(self.character_state, target, spell)
            self.assertEqual(res["dc"], 13)
            self.assertFalse(res["success"])
            self.assertGreater(res["damage"], 0)
            self.assertEqual(res["condition_applied"], "disadvantage_next_attack")

        # Force target d20 roll = 18 (+0 mod = 18 >= DC 13) -> Success
        with patch("state_manager._roll_d20", return_value=18):
            target["hp"]["current"] = 10
            res2 = state_manager.resolve_spell_save(self.character_state, target, spell)
            self.assertEqual(res2["dc"], 13)
            self.assertTrue(res2["success"])
            self.assertEqual(res2["damage"], 0)

    def test_resolve_spell_attack(self):
        spell = {
            "name": "Fire Bolt",
            "level": 0,
            "type": "attack_roll",
            "effect": {"damage": "1d10"},
        }
        target = {"name": "Orc", "ac": 12, "hp": {"current": 15, "max": 15}}

        # Force roll 15 (+5 to hit = 20 vs AC 12) -> Hit
        with patch("state_manager._roll_d20", return_value=15):
            res = state_manager.resolve_spell_attack(self.character_state, target, spell)
            self.assertTrue(res["hit"])
            self.assertGreater(res["damage"], 0)

    def test_cast_spell_slot_deduction(self):
        target = {"name": "Companion", "hp": {"current": 4, "max": 10}}
        # Cast level 1 healing_word
        res = state_manager.cast_spell(self.character_state, target, "healing_word", self.character_state)
        self.assertTrue(res["success"])
        self.assertEqual(self.character_state["spell_slots"]["1"]["current"], 1)
        self.assertGreater(target["hp"]["current"], 4)

        # Cast again to exhaust slots
        res2 = state_manager.cast_spell(self.character_state, target, "healing_word", self.character_state)
        self.assertTrue(res2["success"])
        self.assertEqual(self.character_state["spell_slots"]["1"]["current"], 0)

        # Cast third time when slots are empty -> Rejection
        res3 = state_manager.cast_spell(self.character_state, target, "healing_word", self.character_state)
        self.assertFalse(res3["success"])
        self.assertIn("No level 1 spell slots", res3["reason"])

    # ══════════════════════════════════════════════════════════════════════════
    # 3. STATUS EFFECTS TESTS
    # ══════════════════════════════════════════════════════════════════════════

    def test_apply_condition_immunity_rejection(self):
        target = {"id": "skeleton_1", "immunities": ["poisoned"], "active_conditions": []}
        # Attempt to apply poisoned condition to skeleton (immune)
        combat_manager.apply_condition(target, "poisoned", duration=2)
        self.assertEqual(len(target["active_conditions"]), 0)

        # Non-immune condition (prone) should succeed
        combat_manager.apply_condition(target, "prone", duration=2)
        self.assertEqual(len(target["active_conditions"]), 1)
        self.assertEqual(target["active_conditions"][0]["condition"], "prone")

    def test_condition_effect_on_next_roll_and_expiry(self):
        attacker = {"id": "hero", "stats": {"STR": 14}, "active_conditions": [{"condition": "poisoned", "duration": 1}]}
        target = {"id": "goblin", "ac": 10, "hp": {"current": 10, "max": 10}}
        attack = {"name": "Slash", "attack_bonus": 2, "damage": "1d6"}

        # Roll attack while poisoned -> disadvantage applied
        with patch("combat_manager._roll_d20", side_effect=[18, 4]): # Disadvantage picks min (4)
            res = combat_manager.resolve_attack(attacker, target, attack)
            self.assertEqual(res["raw_roll"], 4)

        # Tick conditions -> duration hits 0 and condition expires
        cs = {"enemies": [], "companions": [], "player_combatant": attacker}
        combat_manager.tick_conditions(cs)
        self.assertEqual(len(attacker["active_conditions"]), 0)

    # ══════════════════════════════════════════════════════════════════════════
    # 4. COMPANION DISMISSAL TESTS
    # ══════════════════════════════════════════════════════════════════════════

    def test_dismiss_companion_moves_to_former_companions(self):
        res = state_manager.dismiss_companion("goblin_friend", self.world_state)
        self.assertEqual(res["npc_id"], "goblin_friend")
        self.assertEqual(res["dismissed_at"], "ruined_mill")
        self.assertEqual(len(self.world_state["party"]["companions"]), 0)
        self.assertEqual(len(self.world_state["party"]["former_companions"]), 1)
        self.assertEqual(self.world_state["party"]["former_companions"][0]["last_location"], "ruined_mill")

    def test_dismiss_companion_rejections(self):
        # 1. Nonexistent companion
        with self.assertRaises(ValueError):
            state_manager.dismiss_companion("nonexistent_npc", self.world_state)

        # 2. Mid-combat dismissal rejection
        self.world_state["combat_state"] = {"status": "active"}
        with self.assertRaises(ValueError):
            state_manager.dismiss_companion("goblin_friend", self.world_state)

    # ══════════════════════════════════════════════════════════════════════════
    # 5. LAZY ITEM GENERATION TESTS
    # ══════════════════════════════════════════════════════════════════════════

    def test_resolve_item_fallback_to_generated_items(self):
        # Static item lookup
        item1 = state_manager.resolve_item("longsword", self.world_state)
        self.assertIsNotNone(item1)
        self.assertEqual(item1["name"], "Longsword")

        # Generate custom item
        gen_id, gen_item = state_manager.generate_item("rare", self.world_state)
        self.assertIn(gen_id, self.world_state["generated_items"])

        # Dynamic item lookup from world_state
        item2 = state_manager.resolve_item(gen_id, self.world_state)
        self.assertIsNotNone(item2)
        self.assertEqual(item2["rarity"], "rare")

    def test_generate_item_rarity_deterministic_stat_resolution(self):
        # Generate item from rarity tag only
        gen_id, item = state_manager.generate_item("rare", self.world_state)
        self.assertEqual(item["value_gold"], 200)
        self.assertEqual(item["effects"]["ac_bonus"], 2)
        self.assertEqual(item["effects"]["saving_throw_bonus"], 1)

    def test_validate_item_id_accepts_both_sources(self):
        # Static item
        self.assertTrue(validation.validate_item_id("longsword", self.world_state))

        # Dynamic item
        gen_id, _ = state_manager.generate_item("uncommon", self.world_state)
        self.assertTrue(validation.validate_item_id(gen_id, self.world_state))

        # Unknown item
        self.assertFalse(validation.validate_item_id("absurd_fake_item_123", self.world_state))


if __name__ == "__main__":
    unittest.main()
