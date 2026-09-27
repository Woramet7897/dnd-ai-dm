import unittest
import copy
import dungeon_manager
import state_manager
import combat_manager


def _make_test_world():
    return {
        "schema_version": 4,
        "character_name": "Ranger Hero",
        "current_location": "town_riverside",
        "visited_rooms": ["town_riverside", "forest_edge", "forest_clearing", "ruined_mill"],
        "cleared_rooms": [],
        "collected_loot": [],
        "dynamic_rooms": {},
        "game_time": {"day": 1, "period": "morning", "steps_since_period_start": 0},
        "quest_log": {"main": [], "side": []},
        "combat_state": None,
    }


def _make_test_player():
    return {
        "id": "player",
        "name": "Ranger Hero",
        "class": "Ranger",
        "level": 2,
        "proficiency_bonus": 2,
        "stats": {"STR": 12, "DEX": 16, "CON": 14, "INT": 10, "WIS": 14, "CHA": 10},
        "hp": {"current": 18, "max": 18},
        "ac": 14,
        "gold": 50,
        "inventory": [
            {"item_id": "shortbow", "name": "Shortbow", "equipped": True, "type": "weapon"},
            {"item_id": "herbs", "name": "Medicinal Herbs", "quantity": 2, "type": "consumable"},
        ],
    }


class TestPhase13_5NoticeBoard(unittest.TestCase):

    def test_refresh_notice_board_initial_generation(self):
        world = _make_test_world()
        entries = dungeon_manager.refresh_notice_board(world)
        self.assertEqual(len(entries), 3)

        types = [e["type"] for e in entries]
        self.assertIn("bounty", types)
        self.assertIn("delivery", types)
        self.assertIn("rumor", types)

        nb = world.get("notice_board", {})
        self.assertEqual(nb.get("last_refreshed_day"), 1)
        self.assertEqual(len(nb.get("entries", [])), 3)

    def test_all_board_entries_reference_existing_room_ids(self):
        world = _make_test_world()
        # Add a dynamic room to ensure dynamic catalog is also tested
        dungeon_manager.register_new_location({
            "id": "abandoned_crypt",
            "name": "Abandoned Crypt",
            "type": "dungeon",
            "description": "Cold subterranean crypt.",
            "exits": {"north": None, "south": "forest_clearing", "east": None, "west": None},
            "is_safe": False,
            "encounter_table": ["skeleton"],
        }, world)

        known_rooms = dungeon_manager._all_known_room_ids(world)
        self.assertIn("abandoned_crypt", known_rooms)

        entries = dungeon_manager.refresh_notice_board(world, force=True)
        for entry in entries:
            target_room = entry.get("target_room_id")
            self.assertIsNotNone(target_room)
            self.assertIn(
                target_room,
                known_rooms,
                f"Notice board quest '{entry.get('title')}' references invalid room ID '{target_room}'!"
            )

    def test_bounty_targets_uncleared_room_and_monster(self):
        world = _make_test_world()
        world["cleared_rooms"] = ["forest_edge"]  # forest_edge is cleared

        entries = dungeon_manager.refresh_notice_board(world, force=True)
        bounty = next((e for e in entries if e["type"] == "bounty"), None)
        self.assertIsNotNone(bounty)
        self.assertNotIn(bounty["target_room_id"], world["cleared_rooms"])
        self.assertIsNotNone(bounty.get("target_monster"))
        self.assertGreater(bounty.get("reward_gold", 0), 0)

    def test_delivery_targets_npc_and_payout(self):
        world = _make_test_world()
        entries = dungeon_manager.refresh_notice_board(world, force=True)
        delivery = next((e for e in entries if e["type"] == "delivery"), None)
        self.assertIsNotNone(delivery)
        self.assertIn(delivery["target_item"], ["herbs", "rations"])
        self.assertIsNotNone(delivery.get("target_npc"))
        # 1.5x payout check
        self.assertGreater(delivery.get("reward_gold", 0), delivery.get("base_value", 0))

    def test_rumor_reveals_hidden_cache_flag_on_target_room(self):
        world = _make_test_world()
        entries = dungeon_manager.refresh_notice_board(world, force=True)
        rumor = next((e for e in entries if e["type"] == "rumor"), None)
        self.assertIsNotNone(rumor)
        self.assertTrue(rumor.get("revealed"))

        target_room_id = rumor["target_room_id"]
        room = dungeon_manager._get_room(target_room_id, world)
        self.assertIsNotNone(room)
        self.assertTrue(room.get("hidden_cache"), f"Room {target_room_id} missing hidden_cache: True!")

    def test_advance_time_past_3_day_boundary_generates_new_entries(self):
        world = _make_test_world()
        # Initialize board on Day 1
        initial_entries = dungeon_manager.refresh_notice_board(world)
        initial_ids = [e["id"] for e in initial_entries]
        self.assertEqual(world["notice_board"]["last_refreshed_day"], 1)

        # Advance 1 day -> Day 2: No refresh
        res_day2 = dungeon_manager.advance_time(world, days=1)
        self.assertEqual(world["game_time"]["day"], 2)
        self.assertFalse(res_day2.get("notice_board_refreshed"))
        current_ids = [e["id"] for e in world["notice_board"]["entries"]]
        self.assertEqual(current_ids, initial_ids)

        # Advance 1 more day -> Day 3: No refresh
        res_day3 = dungeon_manager.advance_time(world, days=1)
        self.assertEqual(world["game_time"]["day"], 3)
        self.assertFalse(res_day3.get("notice_board_refreshed"))
        current_ids = [e["id"] for e in world["notice_board"]["entries"]]
        self.assertEqual(current_ids, initial_ids)

        # Advance 1 more day -> Day 4 (3 days have elapsed: 4 - 1 = 3 >= 3): 3-Day Boundary Crossed!
        res_day4 = dungeon_manager.advance_time(world, days=1)
        self.assertEqual(world["game_time"]["day"], 4)
        self.assertTrue(res_day4.get("notice_board_refreshed"))
        self.assertEqual(world["notice_board"]["last_refreshed_day"], 4)

        new_ids = [e["id"] for e in world["notice_board"]["entries"]]
        self.assertNotEqual(initial_ids, new_ids, "Notice board entries should have refreshed on 3-day boundary!")

    def test_advance_time_days_3_direct_advancement(self):
        world = _make_test_world()
        initial_entries = dungeon_manager.refresh_notice_board(world)
        initial_ids = [e["id"] for e in initial_entries]

        # Advance directly by 3 days -> Day 4
        res = dungeon_manager.advance_time(world, days=3)
        self.assertEqual(world["game_time"]["day"], 4)
        self.assertTrue(res.get("notice_board_refreshed"))
        new_ids = [e["id"] for e in world["notice_board"]["entries"]]
        self.assertNotEqual(initial_ids, new_ids)

    def test_tavern_room_contains_notice_board(self):
        world = _make_test_world()
        entries = dungeon_manager.refresh_notice_board(world)
        tavern_room = dungeon_manager._get_room("town_riverside", world)
        self.assertIsNotNone(tavern_room)
        self.assertEqual(tavern_room.get("notice_board"), entries)

    def test_accept_notice_board_quest(self):
        world = _make_test_world()
        entries = dungeon_manager.refresh_notice_board(world)
        first_quest = entries[0]
        qid = first_quest["id"]

        acc_res = dungeon_manager.accept_notice_board_quest(world, qid)
        self.assertTrue(acc_res["success"])
        self.assertEqual(first_quest["status"], "accepted")

        # Confirm added to quest_log side quests
        side_quests = world.get("quest_log", {}).get("side", [])
        matched = next((q for q in side_quests if q.get("id") == qid), None)
        self.assertIsNotNone(matched)
        self.assertEqual(matched["status"], "active")

    def test_complete_delivery_quest(self):
        world = _make_test_world()
        player = _make_test_player()
        entries = dungeon_manager.refresh_notice_board(world)
        delivery = next((e for e in entries if e["type"] == "delivery"), None)
        self.assertIsNotNone(delivery)

        # Force delivery item to 'herbs' to match player inventory
        delivery["target_item"] = "herbs"
        qid = delivery["id"]
        dungeon_manager.accept_notice_board_quest(world, qid)

        init_gold = player["gold"]
        init_herbs = next((i["quantity"] for i in player["inventory"] if i["item_id"] == "herbs"), 0)

        cmp_res = dungeon_manager.complete_notice_board_quest(world, qid, player)
        self.assertTrue(cmp_res["success"])
        self.assertGreater(player["gold"], init_gold)

        new_herbs = next((i["quantity"] for i in player["inventory"] if i["item_id"] == "herbs"), 0)
        self.assertEqual(new_herbs, init_herbs - 1)
        self.assertEqual(delivery["status"], "completed")

    def test_complete_bounty_quest_via_combat_victory(self):
        world = _make_test_world()
        player = _make_test_player()
        entries = dungeon_manager.refresh_notice_board(world)
        bounty = next((e for e in entries if e["type"] == "bounty"), None)
        self.assertIsNotNone(bounty)

        bounty["target_monster"] = "goblin_scout"
        bounty["target_room_id"] = "forest_clearing"
        world["current_location"] = "forest_clearing"
        qid = bounty["id"]
        dungeon_manager.accept_notice_board_quest(world, qid)

        # Start combat with target monster
        combat_manager.start_combat(["goblin_scout"], player, world)
        cs = world["combat_state"]
        # Down the goblin
        for enemy in cs["enemies"]:
            enemy["hp"]["current"] = 0

        init_gold = player["gold"]
        outcome = combat_manager.check_combat_end(cs, world)
        self.assertEqual(outcome, "player_victory")

        # Bounty auto-completed on victory!
        self.assertEqual(bounty["status"], "completed")
        self.assertGreater(player["gold"], init_gold)

    def test_hidden_cache_loot_collection(self):
        world = _make_test_world()
        target_room = "forest_clearing"
        # Simulate room already collected normal loot
        world["collected_loot"] = [target_room]

        # Room normal loot should be empty
        loot_before = dungeon_manager.get_room_loot(target_room, world)
        self.assertEqual(loot_before, [])

        # Rumor reveals hidden cache
        entries = dungeon_manager.refresh_notice_board(world)
        # Manually ensure target_room has hidden_cache
        world["dynamic_rooms"].setdefault(target_room, {})["hidden_cache"] = True

        # Now get_room_loot collects hidden cache!
        cache_loot = dungeon_manager.get_room_loot(target_room, world)
        self.assertGreater(len(cache_loot), 0)
        self.assertIn(f"{target_room}_hidden_cache", world["collected_loot"])

        # Second collection is empty
        cache_loot_again = dungeon_manager.get_room_loot(target_room, world)
        self.assertEqual(cache_loot_again, [])


if __name__ == "__main__":
    unittest.main()
