"""
phase12_3_tests.py — Phase 12.3: Food Spoilage & Camp Cooking Test Suite
Spec source: Module B §5 / PHASE_11_14_ROADMAP.md Phase 12.3

DoD:
- Unit test advancing time 5 days on a raw-food item confirms it becomes rotten_food.
- Test both cook_meal branches (success: creates Hearty Stew, +5 temp HP, clears exhaustion; failure: burns ingredients).
- Test the rotten-food poison path (CON save DC 13 or Poisoned for 8 hours).
- Test edge cases: stacks, missing ingredients, combat temp HP absorption, long rest spoilage.
"""

import copy
import unittest
from unittest.mock import patch

import combat_manager as cm
import dungeon_manager as dm
import state_manager as sm


class TestPhase12_3FoodSpoilageAndCooking(unittest.TestCase):

    def setUp(self):
        self.player = {
            "name": "Ranger Cook",
            "stats": {"STR": 12, "DEX": 14, "CON": 12, "INT": 10, "WIS": 14, "CHA": 10},
            "hp": {"current": 10, "max": 10},
            "ac": 12,
            "level": 1,
            "proficiency_bonus": 2,
            "proficient_skills": ["Survival"],
            "saving_throw_proficiencies": ["CON"],
            "active_conditions": [],
            "inventory": [],
            "gold": 50,
            "weapon_actions_available": True,
        }
        self.world_state = {
            "game_time": {
                "day": 1,
                "period": "morning",
                "steps_since_period_start": 0,
            },
            "current_location": "forest_clearing",
        }

    # ── 1. Food Spoilage: Advance 5 Days on Raw Food ──────────────────────────
    def test_raw_food_spoils_after_5_days_via_days_param(self):
        """Advancing time 5 days via days=5 turns raw_food into rotten_food."""
        p = copy.deepcopy(self.player)
        p["inventory"] = [
            {"item_id": "raw_meat", "name": "Raw Meat", "type": "raw_food", "raw_food": True, "freshness_days": 5, "quantity": 1}
        ]
        w = copy.deepcopy(self.world_state)

        res = dm.advance_time(w, days=5, character_state=p)

        self.assertEqual(p["inventory"][0]["item_id"], "rotten_food")
        self.assertEqual(p["inventory"][0]["name"], "Rotten Food")
        self.assertEqual(p["inventory"][0]["freshness_days"], 0)
        self.assertTrue(p["inventory"][0]["rotten"])
        self.assertEqual(len(res.get("spoiled_items", [])), 1)

    def test_raw_food_spoils_step_by_step_across_5_days(self):
        """Advancing 80 steps (16 steps/day * 5 days) flips raw_food to rotten_food."""
        p = copy.deepcopy(self.player)
        p["inventory"] = [
            {"item_id": "raw_food", "name": "Raw Food", "type": "raw_food", "freshness_days": 5, "quantity": 2}
        ]
        w = copy.deepcopy(self.world_state)

        # 4 days (64 steps) -> freshness becomes 1, not yet rotten
        for _ in range(4):
            dm.advance_time(w, steps=16, character_state=p)
        self.assertEqual(p["inventory"][0]["freshness_days"], 1)
        self.assertEqual(p["inventory"][0]["item_id"], "raw_food")

        # 5th day (16 steps) -> freshness hits 0, flips to rotten_food
        dm.advance_time(w, steps=16, character_state=p)
        self.assertEqual(p["inventory"][0]["item_id"], "rotten_food")
        self.assertEqual(p["inventory"][0]["freshness_days"], 0)
        self.assertTrue(p["inventory"][0]["rotten"])

    def test_advance_time_with_character_state_in_world(self):
        """advance_time discovers character_state inside world_state dictionary."""
        p = copy.deepcopy(self.player)
        p["inventory"] = [
            {"item_id": "wild_vegetables", "freshness_days": 5, "quantity": 1}
        ]
        w = copy.deepcopy(self.world_state)
        w["character_state"] = p

        dm.advance_time(w, days=5)
        self.assertEqual(p["inventory"][0]["item_id"], "rotten_food")

    # ── 2. Camp Cooking: Success Branch ──────────────────────────────────────
    def test_cook_meal_success_creates_hearty_stew_and_benefits(self):
        """Survival check DC 12 success creates Hearty Stew (+5 temp HP, clears exhaustion)."""
        p = copy.deepcopy(self.player)
        p["active_conditions"] = ["exhausted"]
        p["inventory"] = [
            {"item_id": "raw_meat", "quantity": 1, "freshness_days": 4},
            {"item_id": "wild_vegetables", "quantity": 1, "freshness_days": 4},
        ]

        # WIS mod is +2, proficient +2 -> roll 8 gives total 12 (meets DC 12)
        res = sm.cook_meal(p, "raw_meat", "wild_vegetables", survival_roll=8)

        self.assertTrue(res["success"])
        self.assertFalse(res["burned"])
        self.assertEqual(res["meal"], "Hearty Stew")
        self.assertEqual(res["temp_hp"], 5)
        self.assertTrue(res["exhaustion_cleared"])

        # Player state checks
        self.assertEqual(p["hp"].get("temp"), 5)
        self.assertEqual(p.get("temp_hp"), 5)
        self.assertNotIn("exhausted", p["active_conditions"])

        # Inventory checks: ingredients consumed, Hearty Stew added
        item_ids = [it["item_id"] for it in p["inventory"]]
        self.assertNotIn("raw_meat", item_ids)
        self.assertNotIn("wild_vegetables", item_ids)
        self.assertIn("hearty_stew", item_ids)

    # ── 3. Camp Cooking: Burned / Failure Branch ──────────────────────────────
    def test_cook_meal_failure_burns_ingredients(self):
        """Survival check failure (roll < DC 12) burns ingredients with no Hearty Stew."""
        p = copy.deepcopy(self.player)
        p["active_conditions"] = ["exhausted"]
        p["inventory"] = [
            {"item_id": "raw_meat", "quantity": 1, "freshness_days": 4},
            {"item_id": "wild_vegetables", "quantity": 1, "freshness_days": 4},
        ]

        # Natural 1 always fails
        res = sm.cook_meal(p, "raw_meat", "wild_vegetables", survival_roll=1)

        self.assertFalse(res["success"])
        self.assertTrue(res["burned"])
        self.assertIsNone(res["meal"])
        self.assertEqual(res["temp_hp"], 0)
        self.assertFalse(res["exhaustion_cleared"])

        # Player state remains exhausted with no temp HP
        self.assertIn("exhausted", p["active_conditions"])
        self.assertEqual(p["hp"].get("temp", 0), 0)

        # Ingredients burned (consumed from inventory)
        item_ids = [it["item_id"] for it in p["inventory"]]
        self.assertNotIn("raw_meat", item_ids)
        self.assertNotIn("wild_vegetables", item_ids)
        self.assertNotIn("hearty_stew", item_ids)

    # ── 4. Camp Cooking: Rotten Food Poison Path ──────────────────────────────
    def test_cook_with_rotten_food_failed_con_save_applies_poisoned(self):
        """Cooking with rotten_food and failing DC 13 CON save applies Poisoned for 8 hours."""
        p = copy.deepcopy(self.player)
        p["inventory"] = [
            {"item_id": "rotten_food", "quantity": 1, "rotten": True},
            {"item_id": "raw_meat", "quantity": 1, "freshness_days": 3},
        ]

        # CON mod is +1, proficient +2 -> roll 9 gives total 12 (fails DC 13)
        res = sm.cook_meal(p, "rotten_food", "raw_meat", con_save_roll=9, survival_roll=15)

        self.assertTrue(res["poisoned"])
        self.assertIn("poisoned", p["active_conditions"])
        self.assertTrue(any(c == "poisoned" or (isinstance(c, dict) and c.get("condition") == "poisoned") for c in p["active_conditions"]))

        # Duration is 8 hours / 8
        poison_entry = next((c for c in p["active_conditions"] if (c == "poisoned" or (isinstance(c, dict) and c.get("condition") == "poisoned"))), None)
        self.assertIsNotNone(poison_entry)
        if isinstance(poison_entry, dict):
            self.assertEqual(poison_entry.get("duration"), 8)

    def test_cook_with_rotten_food_successful_con_save_avoids_poison(self):
        """Cooking with rotten_food and passing DC 13 CON save avoids Poisoned condition."""
        p = copy.deepcopy(self.player)
        p["inventory"] = [
            {"item_id": "rotten_food", "quantity": 1, "rotten": True},
            {"item_id": "raw_meat", "quantity": 1, "freshness_days": 3},
        ]

        # CON save roll 15 + mod >= 13 -> succeeds
        res = sm.cook_meal(p, "rotten_food", "raw_meat", con_save_roll=15, survival_roll=15)

        self.assertFalse(res["poisoned"])
        self.assertNotIn("poisoned", p["active_conditions"])

    # ── 5. Ingredient Handling: Same Ingredient Multi-Stack ──────────────────
    def test_cook_meal_with_stack_of_two_same_ingredients(self):
        """Cooking with two units of the same stacked item consumes 2 and succeeds."""
        p = copy.deepcopy(self.player)
        p["inventory"] = [
            {"item_id": "raw_meat", "quantity": 3, "freshness_days": 4}
        ]

        res = sm.cook_meal(p, "raw_meat", "raw_meat", survival_roll=15)
        self.assertTrue(res["success"])
        self.assertEqual(p["inventory"][0]["quantity"], 1)  # 3 - 2 = 1 left
        self.assertIn("hearty_stew", [it["item_id"] for it in p["inventory"]])

    def test_cook_meal_fails_on_missing_ingredient(self):
        """cook_meal rejects cleanly when ingredient is not in inventory."""
        p = copy.deepcopy(self.player)
        p["inventory"] = [{"item_id": "raw_meat", "quantity": 1}]

        res = sm.cook_meal(p, "raw_meat", "wild_vegetables")
        self.assertFalse(res["success"])
        self.assertFalse(res["burned"])
        self.assertIn("Missing ingredient", res["message"])
        self.assertEqual(p["inventory"][0]["quantity"], 1)  # not consumed

    # ── 6. Long Rest Overnight Spoilage ──────────────────────────────────────
    def test_long_rest_ticks_food_freshness_down(self):
        """long_rest() advances day and ages raw food by 1 day."""
        p = copy.deepcopy(self.player)
        p["inventory"] = [
            {"item_id": "raw_meat", "freshness_days": 1, "quantity": 1}
        ]
        w = copy.deepcopy(self.world_state)

        sm.long_rest(w, p)

        # Since freshness was 1, 1 day of long rest spoiled it
        self.assertEqual(p["inventory"][0]["item_id"], "rotten_food")
        self.assertEqual(p["inventory"][0]["freshness_days"], 0)

    # ── 7. Eating Hearty Stew via use_consumable ─────────────────────────────
    def test_use_consumable_hearty_stew(self):
        """Consuming Hearty Stew from inventory grants +5 temp HP and clears exhaustion."""
        p = copy.deepcopy(self.player)
        p["active_conditions"] = ["exhausted"]
        p["inventory"] = [
            {"item_id": "hearty_stew", "quantity": 1}
        ]

        ok, msg, res = sm.use_consumable("hearty_stew", p)
        self.assertTrue(ok)
        self.assertEqual(p["hp"].get("temp"), 5)
        self.assertNotIn("exhausted", p["active_conditions"])
        self.assertEqual(len(p["inventory"]), 0)

    # ── 8. Temporary HP Damage Absorption in Combat ──────────────────────────
    def test_combat_temp_hp_absorbs_damage_first(self):
        """Temporary hit points absorb incoming attack damage before current HP is reduced."""
        p = copy.deepcopy(self.player)
        p["hp"] = {"current": 10, "max": 10, "temp": 5}
        w = copy.deepcopy(self.world_state)

        cs = cm.start_combat(["goblin_scout"], p, w)
        goblin = cs["enemies"][0]
        pc = cs["player_combatant"]

        # Goblin attack dealing 3 damage
        attack = {"name": "Scimitar", "attack_bonus": 4, "damage": "1d6+2", "damage_type": "slashing"}

        # Patch d20 to hit AC 12 (roll 15), damage roll returns 3
        with patch("combat_manager._roll_d20", return_value=15), patch("combat_manager.roll_dice", return_value=3):
            cm.resolve_attack(goblin, pc, attack, combat_state=cs)

        # 3 damage absorbed by 5 temp HP: temp HP becomes 2, current HP stays 10!
        self.assertEqual(pc["hp"]["temp"], 2)
        self.assertEqual(pc["hp"]["current"], 10)


if __name__ == "__main__":
    unittest.main()
