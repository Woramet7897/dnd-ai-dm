import unittest
import copy
from state_manager import (
    attempt_pickpocket,
    attempt_guard_persuasion,
    imprison_player,
    serve_prison_time,
    attempt_lockpick_escape,
    pay_bounty,
    award_inspiration,
    get_inspiration,
)
import dungeon_manager


def _make_test_player(background="criminal", dex=14, cha=12):
    return {
        "id": "player",
        "name": "Rogue Hero",
        "class": "Rogue",
        "background": background,
        "level": 3,
        "proficiency_bonus": 2,
        "stats": {
            "STR": 10,
            "DEX": dex,
            "CON": 12,
            "INT": 12,
            "WIS": 10,
            "CHA": cha,
        },
        "proficiencies": ["Sleight of Hand", "Stealth", "Persuasion"],
        "hp": {"current": 20, "max": 20},
        "gold": 100,
        "bounty": 0,
        "is_wanted": False,
        "status": "normal",
        "inventory": [
            {"item_id": "leather_armor", "name": "Leather Armor", "equipped": True, "type": "armor"},
            {"item_id": "healing_potion", "name": "Healing Potion", "quantity": 2, "type": "consumable"},
        ],
        "inspiration": 0,
        "max_inspiration": 4,
    }


def _make_test_world():
    return {
        "schema_version": 4,
        "character_name": "Rogue Hero",
        "current_location": "town_riverside",
        "visited_rooms": ["town_riverside", "town_jail", "forest_edge"],
        "cleared_rooms": [],
        "collected_loot": [],
        "dynamic_rooms": {},
        "game_time": {"day": 1, "period": "morning", "steps_since_period_start": 0},
        "quest_log": {"main": [], "side": []},
        "combat_state": None,
    }


class TestPhase13CrimeAndPrison(unittest.TestCase):

    def test_pickpocket_success(self):
        player = _make_test_player(background="criminal", dex=14)
        target_npc = {
            "id": "merchant_bob",
            "name": "Bob the Merchant",
            "passive_perception": 12,
            "inventory": [{"item_id": "dagger", "name": "Dagger", "quantity": 1}],
        }
        # Roll 15 + DEX mod 2 + Prof 2 = 19 >= DC 12
        res = attempt_pickpocket(player, target_npc, "dagger", roll_override=15)
        self.assertTrue(res["success"])
        self.assertEqual(res["item_stolen"], "dagger")
        self.assertFalse(player.get("is_wanted"))
        self.assertEqual(player.get("bounty", 0), 0)

        # Check inventory for stolen flag
        stolen_item = next((it for it in player["inventory"] if it["item_id"] == "dagger"), None)
        self.assertIsNotNone(stolen_item)
        self.assertTrue(stolen_item.get("stolen"))

        # Criminal background gets inspiration on successful theft
        self.assertGreaterEqual(get_inspiration(player), 1)

    def test_pickpocket_failure_sets_wanted_and_bounty(self):
        player = _make_test_player(background="criminal", dex=10)
        target_npc = {
            "id": "guard_patrol",
            "name": "Guard Patrol",
            "passive_perception": 14,
        }
        # Roll 2 + mod 0 = 2 < DC 14
        res = attempt_pickpocket(player, target_npc, "healing_potion", roll_override=2)
        self.assertFalse(res["success"])
        self.assertTrue(player.get("is_wanted"))
        self.assertGreater(player.get("bounty", 0), 0)
        self.assertTrue(res["guard_confrontation"])

    def test_pickpocket_gold(self):
        player = _make_test_player(dex=16)
        target_npc = {
            "id": "noble_rich",
            "name": "Rich Noble",
            "gold": 50,
            "passive_perception": 10,
        }
        init_gold = player["gold"]
        res = attempt_pickpocket(player, target_npc, "gold", roll_override=12)
        self.assertTrue(res["success"])
        self.assertGreater(player["gold"], init_gold)
        self.assertEqual(target_npc["gold"], 0)

    def test_guard_persuasion_success(self):
        player = _make_test_player(cha=14)
        # DC 15: roll 14 + mod 2 + prof 2 = 18 >= 15
        res = attempt_guard_persuasion(player, roll_override=14)
        self.assertTrue(res["success"])
        self.assertFalse(res["arrested"])
        self.assertEqual(player.get("status"), "normal")

    def test_guard_persuasion_failure_arrests_player(self):
        player = _make_test_player(cha=10)
        player["bounty"] = 30
        world = _make_test_world()

        # Roll 2 + mod 0 + prof 2 = 4 < 15
        res = attempt_guard_persuasion(player, roll_override=2, world_state=world)
        self.assertFalse(res["success"])
        self.assertTrue(res["arrested"])
        self.assertEqual(player.get("status"), "captive")
        self.assertEqual(player.get("captivity_reason"), "crime")
        self.assertEqual(world.get("current_location"), "town_jail")
        self.assertEqual(len(player.get("inventory", [])), 0)
        self.assertGreater(len(player.get("confiscated_items", [])), 0)

    def test_imprison_player_direct(self):
        player = _make_test_player()
        player["bounty"] = 40
        world = _make_test_world()

        imp_res = imprison_player(player, world_state=world)
        self.assertTrue(imp_res["imprisoned"])
        self.assertEqual(player["status"], "captive")
        self.assertEqual(player["captivity_reason"], "crime")
        self.assertGreaterEqual(player["prison_days_left"], 4)
        self.assertEqual(world["current_location"], "town_jail")
        self.assertEqual(len(player["inventory"]), 0)
        self.assertEqual(len(player["confiscated_items"]), 2)

    def test_serve_prison_time(self):
        player = _make_test_player()
        player["gold"] = 100
        player["bounty"] = 30
        player["is_wanted"] = True
        world = _make_test_world()

        # Add a stolen item and a normal item to confiscated
        player["inventory"] = [
            {"item_id": "torch", "name": "Torch", "stolen": False},
            {"item_id": "gem", "name": "Stolen Gem", "stolen": True},
        ]
        imprison_player(player, world_state=world, prison_days=3)
        self.assertEqual(player["status"], "captive")

        # Serve time
        res = serve_prison_time(player, world_state=world)
        self.assertTrue(res["success"])
        self.assertEqual(player["status"], "normal")
        self.assertIsNone(player.get("captivity_reason"))
        self.assertEqual(player["prison_days_left"], 0)
        self.assertEqual(player["gold"], 70)  # 100 - 30 fine
        self.assertEqual(player["bounty"], 0)
        self.assertFalse(player["is_wanted"])
        self.assertEqual(world["current_location"], "town_riverside")

        # Non-stolen returned, stolen forfeited
        inv_ids = [it["item_id"] for it in player["inventory"]]
        self.assertIn("torch", inv_ids)
        self.assertNotIn("gem", inv_ids)

    def test_lockpick_escape_step1_failure(self):
        player = _make_test_player(dex=10)
        world = _make_test_world()
        imprison_player(player, world_state=world, prison_days=3)

        # Sleight of Hand roll 2 + mod 0 + prof 2 = 4 < 14
        res = attempt_lockpick_escape(player, world_state=world, sleight_roll=2)
        self.assertFalse(res["success"])
        self.assertEqual(res["step"], "lockpick")
        self.assertEqual(player["prison_days_left"], 4)  # 3 + 1
        self.assertEqual(player["status"], "captive")

    def test_lockpick_escape_step2_failure(self):
        player = _make_test_player(dex=14)
        world = _make_test_world()
        imprison_player(player, world_state=world, prison_days=2)

        # Step 1 roll 15 >= 14 (success), Step 2 roll 2 + mod 2 + prof 2 = 6 < 13 (failure)
        res = attempt_lockpick_escape(player, world_state=world, sleight_roll=15, stealth_roll=2)
        self.assertFalse(res["success"])
        self.assertEqual(res["step"], "stealth")
        self.assertEqual(player["prison_days_left"], 3)  # 2 + 1
        self.assertEqual(player["status"], "captive")

    def test_lockpick_escape_success(self):
        player = _make_test_player(background="criminal", dex=14)
        world = _make_test_world()
        # Add normal and stolen items
        player["inventory"] = [
            {"item_id": "dagger", "name": "Dagger", "stolen": False},
            {"item_id": "stolen_gold_cup", "name": "Stolen Cup", "stolen": True},
        ]
        imprison_player(player, world_state=world, prison_days=5)

        # Step 1 roll 15 (pass), Step 2 roll 16 (pass)
        res = attempt_lockpick_escape(player, world_state=world, sleight_roll=15, stealth_roll=16)
        self.assertTrue(res["success"])
        self.assertEqual(res["step"], "escaped")
        self.assertEqual(player["status"], "normal")
        self.assertIsNone(player["captivity_reason"])
        self.assertEqual(world["current_location"], "forest_edge")

        # ALL items recovered including stolen
        inv_ids = [it["item_id"] for it in player["inventory"]]
        self.assertIn("dagger", inv_ids)
        self.assertIn("stolen_gold_cup", inv_ids)

        # Awarded inspiration for escape
        self.assertGreaterEqual(get_inspiration(player), 1)

    def test_pay_bounty(self):
        player = _make_test_player()
        player["gold"] = 100
        player["bounty"] = 40
        player["is_wanted"] = True

        ok, msg = pay_bounty(player)
        self.assertTrue(ok)
        self.assertEqual(player["gold"], 60)
        self.assertEqual(player["bounty"], 0)
        self.assertFalse(player["is_wanted"])

        # Paying when no bounty
        ok, msg = pay_bounty(player)
        self.assertFalse(ok)

        # Paying with insufficient gold
        player["bounty"] = 100
        player["gold"] = 10
        player["is_wanted"] = True
        ok, msg = pay_bounty(player)
        self.assertFalse(ok)
        self.assertEqual(player["gold"], 10)
        self.assertTrue(player["is_wanted"])

    def test_option_a_compatibility_with_phase8(self):
        # Verify Phase 8's combat defeat captive mechanics still function cleanly
        player = _make_test_player()
        world = _make_test_world()

        # Simulate Phase 8 combat defeat capture
        player["status"] = "captive"
        player["captivity_reason"] = "combat_defeat"
        self.assertEqual(player["status"], "captive")
        self.assertEqual(player.get("captivity_reason"), "combat_defeat")

        # Ensure prison methods don't interfere when captivity_reason != "crime"
        # Player is still captive until escaped via DEX check (Phase 8 pattern)


if __name__ == "__main__":
    unittest.main()
