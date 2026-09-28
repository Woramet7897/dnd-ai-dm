"""
test_app_integrations.py — Comprehensive Integration Test Suite
Verifies all systems wired into app.py:
1. Character creation with starting equipment & shield AC bonus
2. Equipment toggling (equip/unequip) & consumable usage
3. Spellcasting resolution (attack_roll, attack_save, heal) & spell slots
4. Room loot collection & consumption
5. Town shops buy/sell with gold & open periods
6. Full combat round resolution with weapon and spell attacks
7. app.resolve_player_combat_action() execution & argument integrity:
   - Weapon attack routing with non-swapped arguments to resolve_round
   - Weapon action routing (cleave/concussive/hamstring)
   - Shove action routing (knock prone / push)
   - Spellcast routing with slot deduction
   - Stunned player skips attack
   - Cast failure error handling
   - Explicit verification that player_attack_result and world_state are NEVER swapped
"""

import math
import os
import unittest
from unittest.mock import MagicMock, patch

import app
import character_creator
import combat_manager
import dungeon_manager
import llm_handler
import state_manager
import validation


class TestAppIntegrations(unittest.TestCase):

    def setUp(self):
        self.stats = {"STR": 16, "DEX": 14, "CON": 14, "INT": 10, "WIS": 10, "CHA": 8}
        self.fighter = character_creator.create_character("Valeros", "Human", "Fighter", "Soldier", self.stats)
        self.wizard = character_creator.create_character("Ezren", "Elf", "Wizard", "Sage", {"STR": 8, "DEX": 14, "CON": 12, "INT": 16, "WIS": 12, "CHA": 10})
        self.cleric = character_creator.create_character("Kyra", "Human", "Cleric", "Acolyte", {"STR": 14, "DEX": 10, "CON": 14, "INT": 10, "WIS": 16, "CHA": 12})
        self.world = {
            "schema_version": 4,
            "character_name": "Valeros",
            "current_location": "town_riverside",
            "visited_rooms": ["town_riverside"],
            "cleared_rooms": [],
            "collected_loot": [],
            "dynamic_rooms": {},
            "game_time": {"day": 1, "period": "morning", "steps_since_period_start": 0},
            "quest_log": {"main": [], "side": []},
            "party": {"companions": [], "former_companions": []},
            "combat_state": None,
        }

    def test_character_creation_starting_equipment(self):
        """DoD: All classes spawn with appropriate starting gear, weapon/armor equipped, and correct AC."""
        # Fighter: Chain mail (16) + Shield (+2) = 18 AC
        self.assertEqual(self.fighter["ac"], 18)
        f_inv = self.fighter["inventory"]
        f_equipped = [i["item_id"] for i in f_inv if i.get("equipped")]
        self.assertIn("chain_mail", f_equipped)
        self.assertIn("shield", f_equipped)
        self.assertIn("longsword", f_equipped)
        # Rations present
        ration = next((i for i in f_inv if i.get("item_id") == "trail_rations"), None)
        self.assertIsNotNone(ration)
        self.assertEqual(ration["quantity"], 3)

        # Wizard: Elf +2 DEX -> 14 + 2 = 16 DEX (mod +3). Unarmored AC: 10 + 3 = 13.
        self.assertEqual(self.wizard["ac"], 13)
        self.assertIn("magic_missile", self.wizard["known_spells"])
        self.assertEqual(self.wizard["spell_slots"]["1"]["current"], 2)

        # Cleric: Scale mail (14) + Shield (+2) = 16 AC
        self.assertEqual(self.cleric["ac"], 16)
        c_equipped = [i["item_id"] for i in self.cleric["inventory"] if i.get("equipped")]
        self.assertIn("scale_mail", c_equipped)
        self.assertIn("shield", c_equipped)
        self.assertIn("mace", c_equipped)
        self.assertIn("cure_wounds", self.cleric["known_spells"])

    def test_equipment_toggle_and_consumable(self):
        """DoD: Equipping/unequipping updates AC; using potion heals and decrements count."""
        # Unequip shield -> AC drops from 18 to 16
        ok, _ = state_manager.unequip_item("shield", self.fighter)
        self.assertTrue(ok)
        self.assertEqual(self.fighter["ac"], 16)

        # Re-equip shield -> AC back to 18
        ok, _ = state_manager.equip_item("shield", self.fighter)
        self.assertTrue(ok)
        self.assertEqual(self.fighter["ac"], 18)

        # Add potion and use it
        self.fighter["hp"]["current"] = 5
        self.fighter["inventory"].append({"item_id": "healing_potion", "equipped": False, "quantity": 1})
        ok, msg, res = state_manager.use_consumable("healing_potion", self.fighter)
        self.assertTrue(ok)
        self.assertGreater(self.fighter["hp"]["current"], 5)
        # Potion should be removed from inventory
        self.assertFalse(any(i.get("item_id") == "healing_potion" for i in self.fighter["inventory"]))

    def test_spellcasting_resolution_and_slots(self):
        """DoD: Casting spells deducts slots, resolves attack_roll / attack_save / heal properly."""
        target_orc = {"name": "Orc", "ac": 12, "stats": {"WIS": 10, "DEX": 10}, "hp": {"current": 20, "max": 20}}

        # Magic Missile (auto-hit, level 1)
        res = state_manager.cast_spell(self.wizard, target_orc, "magic_missile", self.wizard, self.world)
        self.assertTrue(res["success"])
        self.assertTrue(res.get("hit"))
        self.assertEqual(self.wizard["spell_slots"]["1"]["current"], 1)
        self.assertLess(target_orc["hp"]["current"], 20)
        self.assertEqual(res["attack_name"], "Magic Missile")

        # Cure Wounds (heal, level 1)
        injured_fighter = dict(self.fighter)
        injured_fighter["hp"]["current"] = 5
        res_heal = state_manager.cast_spell(self.cleric, injured_fighter, "cure_wounds", self.cleric, self.world)
        self.assertTrue(res_heal["success"])
        self.assertGreater(injured_fighter["hp"]["current"], 5)
        self.assertEqual(self.cleric["spell_slots"]["1"]["current"], 1)

    def test_room_loot_collection(self):
        """DoD: Searching room with loot collects items and prevents duplicate looting."""
        # Forest clearing has torch x2
        self.world["current_location"] = "forest_clearing"
        loot = dungeon_manager.get_room_loot("forest_clearing", self.world)
        self.assertEqual(len(loot), 1)
        self.assertEqual(loot[0]["item_id"], "torch")
        self.assertIn("forest_clearing", self.world["collected_loot"])

        # Second loot attempt returns empty
        loot_again = dungeon_manager.get_room_loot("forest_clearing", self.world)
        self.assertEqual(loot_again, [])

    def test_town_shops_buy_sell(self):
        """DoD: Player can buy and sell items in town shops with correct prices."""
        self.fighter["gold"] = 100
        # General store is open in the morning. Healing potion value_gold is 15 GP.
        ok_buy, msg_buy = state_manager.buy_item("healing_potion", "general_store", self.fighter, self.world)
        self.assertTrue(ok_buy)
        self.assertEqual(self.fighter["gold"], 85)  # 100 - 15 = 85 GP
        self.assertTrue(any(i.get("item_id") == "healing_potion" for i in self.fighter["inventory"]))

        # Sell healing potion back (15 * 0.5 = 7 GP)
        ok_sell, msg_sell = state_manager.sell_item("healing_potion", "general_store", self.fighter, self.world)
        self.assertTrue(ok_sell)
        self.assertEqual(self.fighter["gold"], 92)  # 85 + 7 = 92 GP

    def test_combat_round_with_spell_attack(self):
        """DoD: Spell cast result passed into combat_manager.resolve_round resolves smoothly."""
        cs = combat_manager.start_combat(["goblin_scout"], self.wizard, self.world)
        self.assertIsNotNone(cs)

        # Cast magic missile against enemy
        target_goblin = cs["enemies"][0]
        spell_res = state_manager.cast_spell(cs["player_combatant"], target_goblin, "magic_missile", self.wizard, self.world)
        self.assertTrue(spell_res["success"])

        # Resolve round
        res_round = combat_manager.resolve_round(combat_state=cs, player_attack_result=spell_res, world_state=self.world)
        self.assertIn("narration_block", res_round)
        self.assertIn("Ezren", res_round["narration_block"])

    def test_app_resolve_player_combat_action_weapon_attack(self):
        """Verify app.resolve_player_combat_action correctly routes weapon attack without argument swapping."""
        cs = combat_manager.start_combat(["goblin_scout"], self.fighter, self.world)
        player_c = cs["player_combatant"]
        target = cs["enemies"][0]
        attack = {"name": "Longsword", "damage_dice": "1d8", "attack_bonus": 5, "damage_type": "slashing"}

        with patch.object(combat_manager, "resolve_round", wraps=combat_manager.resolve_round) as mock_rr:
            atk_res, res_round = app.resolve_player_combat_action(
                action_type="⚔️ Weapon Attack",
                player_c=player_c,
                selected_target=target,
                selected_attack=attack,
                cs=cs,
                player=self.fighter,
                world=self.world,
            )

            # Assert resolve_round was called exactly once with non-swapped arguments
            mock_rr.assert_called_once()
            called_kwargs = mock_rr.call_args.kwargs
            self.assertEqual(called_kwargs["combat_state"], cs)
            self.assertEqual(called_kwargs["player_attack_result"], atk_res)
            self.assertEqual(called_kwargs["world_state"], self.world)
            # Crucial check: verify world_state and player_attack_result were NOT swapped!
            self.assertNotEqual(called_kwargs["player_attack_result"], self.world)
            self.assertNotEqual(called_kwargs["world_state"], atk_res)

            self.assertIsNotNone(atk_res)
            self.assertIn("hit", atk_res)
            self.assertIn("narration_block", res_round)

    def test_app_resolve_player_combat_action_weapon_action(self):
        """Verify app.resolve_player_combat_action routes weapon actions (Cleave/Concussive/Hamstring)."""
        cs = combat_manager.start_combat(["goblin_scout", "goblin_warrior"], self.fighter, self.world)
        player_c = cs["player_combatant"]
        target = cs["enemies"][0]
        adj_target = cs["enemies"][1]
        attack = {"name": "Longsword", "damage_dice": "1d8", "attack_bonus": 5, "damage_type": "slashing"}

        with patch.object(combat_manager, "resolve_round", wraps=combat_manager.resolve_round) as mock_rr:
            atk_res, res_round = app.resolve_player_combat_action(
                action_type="💥 Weapon Action",
                player_c=player_c,
                selected_target=target,
                selected_attack=attack,
                selected_adj_target=adj_target,
                cs=cs,
                player=self.fighter,
                world=self.world,
            )

            mock_rr.assert_called_once()
            called_kwargs = mock_rr.call_args.kwargs
            self.assertEqual(called_kwargs["combat_state"], cs)
            self.assertEqual(called_kwargs["player_attack_result"], atk_res)
            self.assertEqual(called_kwargs["world_state"], self.world)
            self.assertNotEqual(called_kwargs["player_attack_result"], self.world)

            self.assertIsNotNone(atk_res)
            self.assertIn("weapon_action_name", atk_res)
            self.assertEqual(atk_res.get("action_type"), "weapon_action")

    def test_app_resolve_player_combat_action_shove(self):
        """Verify app.resolve_player_combat_action routes shove action."""
        cs = combat_manager.start_combat(["goblin_scout"], self.fighter, self.world)
        player_c = cs["player_combatant"]
        target = cs["enemies"][0]

        with patch.object(combat_manager, "resolve_round", wraps=combat_manager.resolve_round) as mock_rr:
            atk_res, res_round = app.resolve_player_combat_action(
                action_type="🫸 Shove",
                player_c=player_c,
                selected_target=target,
                selected_shove_type="knock_prone",
                cs=cs,
                player=self.fighter,
                world=self.world,
            )

            mock_rr.assert_called_once()
            called_kwargs = mock_rr.call_args.kwargs
            self.assertEqual(called_kwargs["player_attack_result"], atk_res)
            self.assertEqual(called_kwargs["world_state"], self.world)
            self.assertNotEqual(called_kwargs["player_attack_result"], self.world)

            self.assertIsNotNone(atk_res)
            self.assertIn("success", atk_res)
            self.assertIsInstance(atk_res.get("success"), bool)
            self.assertEqual(atk_res.get("action_type"), "shove")

    def test_app_resolve_player_combat_action_cast_spell(self):
        """Verify app.resolve_player_combat_action routes cast_spell."""
        cs = combat_manager.start_combat(["goblin_scout"], self.wizard, self.world)
        player_c = cs["player_combatant"]
        target = cs["enemies"][0]

        with patch.object(combat_manager, "resolve_round", wraps=combat_manager.resolve_round) as mock_rr:
            atk_res, res_round = app.resolve_player_combat_action(
                action_type="🪄 Cast Spell",
                player_c=player_c,
                selected_target=target,
                selected_spell_id="magic_missile",
                cs=cs,
                player=self.wizard,
                world=self.world,
            )

            mock_rr.assert_called_once()
            called_kwargs = mock_rr.call_args.kwargs
            self.assertEqual(called_kwargs["player_attack_result"], atk_res)
            self.assertEqual(called_kwargs["world_state"], self.world)
            self.assertNotEqual(called_kwargs["player_attack_result"], self.world)

            self.assertIsNotNone(atk_res)
            self.assertTrue(atk_res.get("success"))

    def test_app_resolve_player_combat_action_stunned_skips_attack(self):
        """Verify player with stunned condition skips attack and passes skipped result to resolve_round."""
        cs = combat_manager.start_combat(["goblin_scout"], self.fighter, self.world)
        player_c = cs["player_combatant"]
        target = cs["enemies"][0]
        combat_manager.apply_condition(player_c, "stunned", duration=1)
        attack = {"name": "Longsword", "damage_dice": "1d8", "attack_bonus": 5, "damage_type": "slashing"}

        atk_res, res_round = app.resolve_player_combat_action(
            action_type="⚔️ Weapon Attack",
            player_c=player_c,
            selected_target=target,
            selected_attack=attack,
            cs=cs,
            player=self.fighter,
            world=self.world,
        )

        self.assertIsNotNone(atk_res)
        self.assertTrue(atk_res.get("skipped"))
        self.assertEqual(atk_res.get("reason"), "stunned")
        self.assertIn("narration_block", res_round)

    def test_app_resolve_player_combat_action_cast_failure_returns_error(self):
        """Verify spell cast failure (e.g. out of slots) returns error dict without corrupting round."""
        cs = combat_manager.start_combat(["goblin_scout"], self.wizard, self.world)
        player_c = cs["player_combatant"]
        target = cs["enemies"][0]
        # Exhaust slots
        self.wizard["spell_slots"]["1"]["current"] = 0

        atk_res, res_round = app.resolve_player_combat_action(
            action_type="🪄 Cast Spell",
            player_c=player_c,
            selected_target=target,
            selected_spell_id="magic_missile",
            cs=cs,
            player=self.wizard,
            world=self.world,
        )

        self.assertFalse(atk_res.get("success"))
        self.assertIn("error", res_round)

    def test_interact_with_object_exploration_and_combat(self):
        """DoD: interact_with_object marks used=True, triggers surface/damage in combat, and works out of combat."""
        # Exploration interaction in ruined_mill
        res_exp = dungeon_manager.interact_with_object(self.world, "ruined_mill", "rotten_support_beam", character_state=self.fighter)
        self.assertTrue(res_exp["success"])
        self.assertTrue(res_exp["object"]["used"])

        # Second interaction should fail (already used)
        res_exp2 = dungeon_manager.interact_with_object(self.world, "ruined_mill", "rotten_support_beam", character_state=self.fighter)
        self.assertFalse(res_exp2["success"])

        # Combat interaction with chandelier (deals damage + grease surface)
        cs = combat_manager.start_combat(["goblin_scout"], self.fighter, self.world)
        res_c = dungeon_manager.interact_with_object(self.world, "ruined_mill", "chandelier", combat_state=cs, character_state=self.fighter)
        self.assertTrue(res_c["success"])
        self.assertEqual(cs.get("room_surface", {}).get("type"), "grease")
        # Check enemy took damage from chandelier (2d6)
        enemy_hp = cs["enemies"][0]["hp"]["current"]
        self.assertLess(enemy_hp, 10)

    def test_move_player_blocked_when_imprisoned_or_captive(self):
        """DoD: move_player returns False when character is captive or has crime_state.imprisoned."""
        self.fighter["status"] = "captive"
        ok, msg, _ = dungeon_manager.move_player("north", self.world, self.fighter)
        self.assertFalse(ok)
        self.assertIn("imprisoned or held captive", msg)

        # Test crime_state.imprisoned
        self.fighter["status"] = "normal"
        self.fighter["crime_state"] = {"imprisoned": True}
        ok2, msg2, _ = dungeon_manager.move_player("north", self.world, self.fighter)
        self.assertFalse(ok2)
        self.assertIn("imprisoned or held captive", msg2)

    def test_move_player_failsafe_when_character_state_none(self):
        """DoD: move_player blocks movement when world_state.is_imprisoned is True even if character_state is None."""
        self.world["is_imprisoned"] = True
        ok, msg, _ = dungeon_manager.move_player("north", self.world, character_state=None)
        self.assertFalse(ok)
        self.assertIn("imprisoned or held captive", msg)

    def test_state_extraction_helpers_currency_rations_quests_spells(self):
        """DoD: Verify shared helpers correctly extract non-zero values matching actual state schema."""
        test_player = {
            "currency": {"gp": 42, "sp": 17, "cp": 9},
            "inventory": [
                {"item_id": "trail_rations", "quantity": 5},
                {"item_id": "torch", "quantity": 2},
                {"item_id": "trail_rations", "quantity": 3},
            ],
            "known_spells": ["fire_bolt", "magic_missile", "shield"],
        }
        test_world = {
            "quest_log": {
                "main": [
                    {"id": "mq1", "status": "active", "title": "Main 1"},
                    {"id": "mq2", "status": "completed", "title": "Main 2"},
                ],
                "side": [
                    {"id": "sq1", "status": "active", "title": "Side 1"},
                    {"id": "sq2", "status": "failed", "title": "Side 2"},
                ],
            }
        }

        # Currency helper
        curr = app.get_player_currency(test_player)
        self.assertEqual(curr["gp"], 42)
        self.assertEqual(curr["sp"], 17)
        self.assertEqual(curr["cp"], 9)

        # Rations helper: 5 + 3 = 8
        self.assertEqual(app.get_ration_count(test_player), 8)

        # Known spells helper: 3
        self.assertEqual(app.get_known_spell_count(test_player), 3)

        # Active quests helper: 1 main active + 1 side active = 2
        self.assertEqual(app.get_active_quest_count(test_world), 2)

        # Null / Empty state safety guards
        self.assertEqual(app.get_ration_count(None), 0)
        self.assertEqual(app.get_ration_count({}), 0)
        self.assertEqual(app.get_known_spell_count(None), 0)
        self.assertEqual(app.get_known_spell_count({}), 0)
        self.assertEqual(app.get_active_quest_count(None), 0)
        self.assertEqual(app.get_active_quest_count({}), 0)
        self.assertEqual(app.get_player_currency(None), {"gp": 0, "sp": 0, "cp": 0})

    def test_time_module_imported(self):
        """DoD: Verify time module is imported in app.py to prevent NameError in Ollama starter button."""
        self.assertTrue(hasattr(app, "time"))
        self.assertTrue(callable(getattr(app.time, "sleep", None)))


if __name__ == "__main__":
    unittest.main()


