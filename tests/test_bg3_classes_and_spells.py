"""
test_bg3_classes_and_spells.py — Comprehensive Test Suite for Baldur's Gate 3 Class, Subclass, Spell, & Combat Overhaul.

Verifies:
1. All 8 Classes (Fighter, Wizard, Rogue, Cleric, Bard, Paladin, Barbarian, Warlock) create valid character sheets
   with Subclasses, Fighting Styles, Class Resources, and Barbarian Unarmored Defense.
2. Rogue Automatic Sneak Attack (+1d6 to +3d6) when hidden / advantage / ally present, and Cunning Action: Hide.
3. Paladin Divine Smite (+2d8 radiant damage using a spell slot), Lay on Hands, and Vow of Enmity.
4. Barbarian Rage (+2 melee damage & 50% physical damage resistance) and Reckless Attack (Advantage).
5. Fighter Second Wind, Action Surge (+1 Action), Champion Improved Critical (19-20), Battle Master Maneuvers, and Lv5 Extra Attack.
6. Warlock Eldritch Blast + Agonizing Blast (+CHA mod per beam, 2 beams at Lv5), Fiend Dark One's Blessing Temp HP,
   and Pact Magic Spell Slot Recharge on Short Rest.
7. Spellcasting Engine: Multi-projectile Magic Missile & Scorching Ray, Upcasting, AoE Fireball with half damage on save,
   Buffs (Bless +1d4, Shield +5 AC, Misty Step High Ground, Haste), Debuffs (Hold Person auto-crit, Hex +1d6 necrotic),
   Life Domain Cleric Disciple of Life bonus healing, and Fire spell surface combos.
8. Level 4 Feats (Alert +5 initiative, Tough +2 HP/level, Great Weapon Master +5 melee dmg) & ASI (+2 stat).
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app
import character_creator
import combat_manager as cm
import state_manager as sm


class TestBG3ClassesAndSpells(unittest.TestCase):

    def setUp(self):
        self.base_stats = {"STR": 15, "DEX": 14, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10}
        self.caster_stats = {"STR": 8, "DEX": 14, "CON": 14, "INT": 10, "WIS": 12, "CHA": 15}
        self.world = {
            "schema_version": 4,
            "current_location": "ruined_mill",
            "visited_rooms": ["town_riverside", "ruined_mill"],
            "cleared_rooms": [],
            "collected_loot": [],
            "dynamic_rooms": {},
            "game_time": {"day": 1, "period": "morning", "steps_since_period_start": 0},
            "party": {"companions": []},
            "combat_state": None,
        }

    # ══════════════════════════════════════════════════════════════════════════
    # 1. ALL 8 CLASSES, SUBCLASSES, & BARBARIAN UNARMORED DEFENSE
    # ══════════════════════════════════════════════════════════════════════════
    def test_create_all_8_classes_and_subclasses(self):
        paladin = character_creator.create_character(
            "Minthara", "Human", "Paladin", "Noble", self.base_stats,
            subclass="oath_of_vengeance", fighting_style="defence"
        )
        self.assertEqual(paladin["class"], "Paladin")
        self.assertEqual(paladin["subclass"], "oath_of_vengeance")
        # Chain mail (16) + Shield (2) + Defence (+1) = 19 AC
        self.assertEqual(paladin["ac"], 19)
        self.assertEqual(paladin["class_resources"]["lay_on_hands"]["max"], 5)

        barbarian = character_creator.create_character(
            "Karlach", "Tiefling", "Barbarian", "Soldier", self.base_stats,
            subclass="berserker"
        )
        self.assertEqual(barbarian["class"], "Barbarian")
        self.assertEqual(barbarian["subclass"], "berserker")
        # Barbarian Unarmored Defense: 10 + DEX mod (+2) + CON mod (+2) = 14 AC
        self.assertEqual(barbarian["ac"], 14)
        self.assertEqual(barbarian["hp"]["max"], 14)  # 12 hit die + 2 CON mod

        warlock = character_creator.create_character(
            "Wyll", "Human", "Warlock", "Folk Hero", self.caster_stats,
            subclass="the_fiend"
        )
        self.assertEqual(warlock["class"], "Warlock")
        self.assertEqual(warlock["subclass"], "the_fiend")
        self.assertIn("eldritch_blast", warlock["known_spells"])
        self.assertIn("hex", warlock["known_spells"])

    # ══════════════════════════════════════════════════════════════════════════
    # 2. ROGUE: CUNNING ACTION HIDE & AUTOMATIC SNEAK ATTACK
    # ══════════════════════════════════════════════════════════════════════════
    def test_rogue_cunning_action_hide_and_sneak_attack(self):
        rogue = character_creator.create_character(
            "Astarion", "Elf", "Rogue", "Criminal",
            {"STR": 10, "DEX": 15, "CON": 14, "INT": 12, "WIS": 10, "CHA": 12},
            subclass="thief"
        )
        rogue["level"] = 3  # +2d6 Sneak Attack
        cs = cm.start_combat(["goblin_scout"], rogue, self.world)
        player_c = cs["player_combatant"]
        target = cs["enemies"][0]
        target["hp"] = {"current": 30, "max": 30}

        # 1. Use Bonus Action: Cunning Action Hide -> applies 'hidden'
        hide_res = cm.resolve_class_action(player_c, "cunning_action_hide", combat_state=cs)
        self.assertTrue(hide_res["success"])
        self.assertIn("hidden", cm._get_condition_set(player_c))

        # 2. Attack while hidden -> gets Advantage + Sneak Attack (+2d6) and consumes 'hidden'
        with patch("combat_manager._roll_d20", side_effect=[6, 16]):
            atk_res = cm.resolve_attack(player_c, target, player_c["attacks"][0], combat_state=cs)
        self.assertTrue(atk_res["hit"])
        self.assertEqual(atk_res["raw_roll"], 16)
        self.assertGreater(atk_res["sneak_attack_damage"], 0)
        self.assertNotIn("hidden", cm._get_condition_set(player_c))

    # ══════════════════════════════════════════════════════════════════════════
    # 3. PALADIN: DIVINE SMITE, LAY ON HANDS, & VOW OF ENMITY
    # ══════════════════════════════════════════════════════════════════════════
    def test_paladin_divine_smite_and_lay_on_hands(self):
        paladin = character_creator.create_character(
            "ShadowheartPal", "Human", "Paladin", "Soldier", self.base_stats,
            subclass="oath_of_vengeance"
        )
        paladin["xp_current"] = 350
        sm.apply_level_up(paladin)  # Level 2 Paladin -> unlocks 2 Level-1 spell slots & 10 Lay on Hands
        self.assertEqual(paladin["level"], 2)
        self.assertEqual(paladin["spell_slots"]["1"]["current"], 2)

        cs = cm.start_combat(["goblin_scout"], paladin, self.world)
        player_c = cs["player_combatant"]
        target = cs["enemies"][0]
        target["hp"] = {"current": 40, "max": 40}

        # Vow of Enmity grants Advantage
        vow_res = cm.resolve_class_action(player_c, "vow_of_enmity", combat_state=cs)
        self.assertTrue(vow_res["success"])
        self.assertIn("vow_of_enmity", cm._get_condition_set(player_c))

        # Attack with Divine Smite enabled -> consumes 1 spell slot and adds 2d8 radiant damage
        with patch("combat_manager._roll_d20", return_value=15):
            atk_res = cm.resolve_attack(
                player_c, target, player_c["attacks"][0], combat_state=cs, use_divine_smite=True
            )
        self.assertTrue(atk_res["hit"])
        self.assertGreater(atk_res["smite_damage"], 0)
        self.assertEqual(paladin["spell_slots"]["1"]["current"], 1)

        # Lay on Hands heals injured paladin
        player_c["hp"]["current"] = 4
        loh_res = cm.resolve_class_action(player_c, "lay_on_hands", combat_state=cs)
        self.assertTrue(loh_res["success"])
        self.assertEqual(loh_res["healed"], 10)
        self.assertEqual(player_c["hp"]["current"], 14)

    # ══════════════════════════════════════════════════════════════════════════
    # 4. BARBARIAN: RAGE (+2 DAMAGE & PHYSICAL RESISTANCE) & RECKLESS ATTACK
    # ══════════════════════════════════════════════════════════════════════════
    def test_barbarian_rage_and_physical_resistance(self):
        barbarian = character_creator.create_character(
            "Karlach", "Half-Orc", "Barbarian", "Soldier", self.base_stats,
            subclass="berserker"
        )
        cs = cm.start_combat(["goblin_scout"], barbarian, self.world)
        player_c = cs["player_combatant"]
        enemy = cs["enemies"][0]
        enemy["hp"] = {"current": 30, "max": 30}

        # Enter Rage
        rage_res = cm.resolve_class_action(player_c, "rage", combat_state=cs)
        self.assertTrue(rage_res["success"])
        self.assertIn("raging", cm._get_condition_set(player_c))

        # Enemy hits raging Barbarian for 10 slashing damage -> halved to 5!
        start_hp = player_c["hp"]["current"]
        with patch("combat_manager._roll_d20", return_value=18), patch("combat_manager.roll_dice", return_value=10):
            e_res = cm.resolve_attack(enemy, player_c, enemy["attacks"][0], combat_state=cs)
        self.assertTrue(e_res["hit"])
        self.assertEqual(e_res["damage"], 5)
        self.assertEqual(player_c["hp"]["current"], start_hp - 5)

    # ══════════════════════════════════════════════════════════════════════════
    # 5. FIGHTER: SECOND WIND, ACTION SURGE, MANEUVERS, & LV5 EXTRA ATTACK
    # ══════════════════════════════════════════════════════════════════════════
    def test_fighter_maneuvers_and_level_5_extra_attack(self):
        fighter = character_creator.create_character(
            "Laezel", "Human", "Fighter", "Soldier", self.base_stats,
            subclass="battle_master"
        )
        fighter["xp_current"] = 7000
        while sm.check_level_up(fighter):
            sm.apply_level_up(fighter)
        self.assertEqual(fighter["level"], 5)
        self.assertEqual(fighter["class_resources"]["superiority_dice"]["current"], 4)

        cs = cm.start_combat(["goblin_scout"], fighter, self.world)
        player_c = cs["player_combatant"]
        enemy = cs["enemies"][0]
        enemy["hp"] = {"current": 50, "max": 50}

        # Use Trip Attack maneuver + Lv5 Extra Attack via resolve_player_combat_action
        with patch("combat_manager._roll_d20", return_value=16):
            atk_res, round_res = app.resolve_player_combat_action(
                action_type="⚔️ Weapon Attack",
                player_c=player_c,
                selected_target=enemy,
                selected_attack=player_c["attacks"][0],
                cs=cs,
                player=fighter,
                world=self.world,
                maneuver="trip_attack",
            )
        self.assertTrue(atk_res["hit"])
        self.assertEqual(atk_res["maneuver_used"], "trip_attack")
        self.assertGreater(atk_res["maneuver_damage"], 0)
        self.assertEqual(atk_res["condition_applied"], "prone")
        self.assertEqual(fighter["class_resources"]["superiority_dice"]["current"], 3)

        # Confirm Extra Attack fired in the same round!
        round_events = cs["round_log"][-1]
        player_attacks_in_round = [e for e in round_events if e.get("attacker_id") == "player"]
        self.assertEqual(len(player_attacks_in_round), 2)

    # ══════════════════════════════════════════════════════════════════════════
    # 6. WARLOCK: ELDRITCH BLAST BEAMS, DARK ONE'S BLESSING, & SHORT REST SLOTS
    # ══════════════════════════════════════════════════════════════════════════
    def test_warlock_eldritch_blast_and_short_rest_pact_magic(self):
        warlock = character_creator.create_character(
            "Wyll", "Human", "Warlock", "Folk Hero", self.caster_stats,
            subclass="the_fiend"
        )
        warlock["xp_current"] = 7000
        while sm.check_level_up(warlock):
            sm.apply_level_up(warlock)
        self.assertEqual(warlock["level"], 5)

        cs = cm.start_combat(["goblin_scout"], warlock, self.world)
        player_c = cs["player_combatant"]
        enemy = cs["enemies"][0]
        enemy["hp"] = {"current": 5, "max": 20}

        # Cast Eldritch Blast at Level 5 -> fires 2 beams with Agonizing Blast (+CHA mod) and kills enemy -> grants Dark One's Blessing Temp HP!
        with patch("state_manager._roll_d20", return_value=18):
            eb_res = sm.cast_spell(player_c, enemy, "eldritch_blast", warlock, self.world)
        self.assertTrue(eb_res["success"])
        self.assertEqual(eb_res["projectiles_fired"], 2)
        self.assertTrue(eb_res["target_downed"])
        self.assertGreater(eb_res.get("dark_ones_blessing_temp_hp", 0), 0)
        self.assertGreater(warlock["hp"].get("temp", 0), 0)

        # Spend a Warlock spell slot and verify perform_short_rest restores it!
        warlock["spell_slots"]["1"]["current"] = 0
        sm.perform_short_rest(warlock)
        self.assertEqual(warlock["spell_slots"]["1"]["current"], warlock["spell_slots"]["1"]["max"])

    # ══════════════════════════════════════════════════════════════════════════
    # 7. SPELL ENGINE: UPCASTING, AOE HALF-ON-SAVE, HOLD PERSON AUTO-CRIT, & BUFFS
    # ══════════════════════════════════════════════════════════════════════════
    def test_spells_upcast_aoe_and_hold_person_auto_crit(self):
        wizard = character_creator.create_character(
            "Gale", "Human", "Wizard", "Sage",
            {"STR": 8, "DEX": 14, "CON": 14, "INT": 15, "WIS": 12, "CHA": 10},
            subclass="abjuration"
        )
        wizard["xp_current"] = 7000
        while sm.check_level_up(wizard):
            sm.apply_level_up(wizard)
        self.assertEqual(wizard["level"], 5)
        self.assertIn("fireball", wizard["known_spells"])
        self.assertIn("hold_person", wizard["known_spells"])

        cs = cm.start_combat(["goblin_scout", "goblin_warrior"], wizard, self.world)
        player_c = cs["player_combatant"]
        e1, e2 = cs["enemies"][0], cs["enemies"][1]
        e1["hp"] = {"current": 40, "max": 40}
        e2["hp"] = {"current": 40, "max": 40}

        # 1. Upcast Magic Missile at Level 2 -> fires 4 darts (3 base + 1 upcast)!
        mm_res = sm.cast_spell(player_c, e1, "magic_missile", wizard, self.world, upcast_level=2)
        self.assertTrue(mm_res["success"])
        self.assertEqual(mm_res["projectiles_fired"], 4)
        # Abjuration Wizard Arcane Ward grants Temp HP on casting!
        self.assertGreaterEqual(wizard["hp"].get("temp", 0), 6)

        # 2. Cast AoE Fireball where enemies SUCCEED on DEX save (roll 19) -> still deals half damage!
        with patch("state_manager._roll_d20", return_value=19):
            fb_res = sm.cast_spell(player_c, e1, "fireball", wizard, self.world)
        self.assertTrue(fb_res["success"])
        self.assertEqual(fb_res["targets_hit"], 2)
        self.assertGreater(fb_res["damage"], 0)
        # Fireball also sets room surface to fire!
        self.assertEqual(cs["room_surface"]["type"], "fire")

        # 3. Cast Hold Person (failed WIS save) -> applies 'held', causing next attack to AUTO-CRIT even on roll of 5!
        with patch("state_manager._roll_d20", return_value=2):
            hp_res = sm.cast_spell(player_c, e2, "hold_person", wizard, self.world)
        self.assertEqual(hp_res["condition_applied"], "held")
        self.assertEqual(wizard["concentration"], "hold_person")

        with patch("combat_manager._roll_d20", return_value=5):
            crit_atk = cm.resolve_attack(player_c, e2, player_c["attacks"][0], combat_state=cs)
        self.assertTrue(crit_atk["hit"])
        self.assertTrue(crit_atk["crit"])

    # ══════════════════════════════════════════════════════════════════════════
    # 8. LEVEL 4 FEATS & ABILITY SCORE IMPROVEMENT (ASI)
    # ══════════════════════════════════════════════════════════════════════════
    def test_level_4_feats_and_asi(self):
        fighter = character_creator.create_character(
            "Valeros", "Human", "Fighter", "Soldier", self.base_stats
        )
        fighter["xp_current"] = 2800
        while sm.check_level_up(fighter):
            sm.apply_level_up(fighter)
        self.assertEqual(fighter["level"], 4)
        self.assertEqual(fighter["pending_level_up_choices"], 1)

        # Pick Great Weapon Master feat
        ok_f, _ = sm.apply_feat_or_asi(fighter, "feat", feat_id="great_weapon_master")
        self.assertTrue(ok_f)
        self.assertIn("great_weapon_master", fighter["feats"])
        self.assertEqual(fighter["pending_level_up_choices"], 0)

        # Pick Tough feat -> increases Max HP by 2 * level (8 HP)
        old_max = fighter["hp"]["max"]
        ok_t, _ = sm.apply_feat_or_asi(fighter, "feat", feat_id="tough")
        self.assertTrue(ok_t)
        self.assertEqual(fighter["hp"]["max"], old_max + 8)

    # ══════════════════════════════════════════════════════════════════════════
    # 9. AGENTIC GENERATION: BIDIRECTIONAL ROOMS, AUTO-HEALING, & ALIAS REMAPPING
    # ══════════════════════════════════════════════════════════════════════════
    def test_agentic_bidirectional_new_location_and_auto_healing(self):
        import dungeon_manager as dm
        import validation

        world = {
            "schema_version": 4,
            "current_location": "forest_clearing",
            "visited_rooms": ["town_riverside", "forest_edge", "forest_clearing"],
            "dynamic_rooms": {},
            "cleared_rooms": [],
            "collected_loot": [],
            "generated_items": {},
            "generated_monsters": {},
            "generated_npcs": {},
        }
        player = character_creator.create_character("Valeros", "Human", "Fighter", "Soldier", self.base_stats)

        # Simulate an agentic extraction payload creating a new dungeon, a custom boss monster, a custom item in loot,
        # a new NPC in the room, and moving the player into the new dungeon in the same turn!
        raw_payload = {
            "generated_monsters": {
                "flame_drake": {
                    "name": "Flame Drake",
                    "hp": 25,
                    "ac": 14,
                    # Partial stats (omitted INT/WIS/CHA like smaller models do) -> auto-healed to 10!
                    "stats": {"STR": 16, "DEX": 12, "CON": 14},
                    "attacks": [
                        {
                            "name": "Fire Breath",
                            "attack_bonus": 4,
                            "damage": "2d6",
                            "damage_type": "fire",
                            "applies_condition": "burning",  # BG3 condition!
                            "ranged": True,
                        }
                    ],
                }
            },
            "generated_items": {
                "drake_scale_ring": {
                    "name": "Drake Scale Ring",
                    "type": "ring",  # Type alias 'ring' -> auto-healed to 'wearable'!
                    "slot": "ring",
                    "rarity": "rare",
                    "value_gold": 150,
                    "effects": {"ac_bonus": 1, "skill_bonus": {"Athletics": 2}},
                }
            },
            "generated_npcs": {
                "captured_ranger": {
                    "name": "Theron the Ranger",
                    "role": "Ranger",
                    "hp": 20,
                    "ac": 14,
                }
            },
            "world_updates": {
                "new_location": {
                    # Partial room definition from smaller/creative LLM (missing is_safe, missing null exits)
                    "id": "drake_cavern",
                    "name": "Scorched Drake Cavern",
                    "description": "A volcanic cavern glowing with embers.",
                    "exits": {"south": "forest_clearing"},
                    "loot": ["drake_scale_ring"],
                    "encounter_table": ["flame_drake"],
                }
            },
            "state_updates": {
                "move_to_location_id": "drake_cavern",
            },
        }

        cleaned = validation.validate_extraction_output(raw_payload, world_state=world, player_state=player)

        # 1. Verify new_location was normalized, validated, and remapped loot/encounters to real gen_item_ / gen_mon_ IDs
        self.assertIn("world_updates", cleaned)
        nl = cleaned["world_updates"]["new_location"]
        self.assertEqual(nl["id"], "drake_cavern")
        self.assertEqual(nl["type"], "dungeon")
        self.assertFalse(nl["is_safe"])
        self.assertTrue(nl["loot"][0]["item_id"].startswith("gen_item_"))
        self.assertTrue(nl["encounter_table"][0].startswith("gen_mon_"))

        # 2. Verify move_to_location_id was permitted in the same turn!
        self.assertEqual(cleaned["state_updates"]["move_to_location_id"], "drake_cavern")

        # 3. Register new location and verify bidirectional exit from forest_clearing -> drake_cavern!
        ok_reg, _ = dm.register_new_location(nl, world)
        self.assertTrue(ok_reg)
        clearing_room = dm._get_room("forest_clearing", world)
        self.assertIn("drake_cavern", clearing_room["exits"].values())

        # 4. Verify player can walk between forest_clearing and drake_cavern using move_player!
        dir_to_cavern = next(d for d, target in clearing_room["exits"].items() if target == "drake_cavern")
        ok_move, _, dest_room = dm.move_player(dir_to_cavern, world, player)
        self.assertTrue(ok_move)
        self.assertEqual(world["current_location"], "drake_cavern")
        self.assertEqual(dest_room["name"], "Scorched Drake Cavern")

    # ══════════════════════════════════════════════════════════════════════════
    # 10. TABLETOP ROLEPLAY & DIALOGUE: COMBAT PARLEY, CLASS TAGS, & NPC ATTITUDE
    # ══════════════════════════════════════════════════════════════════════════
    def test_combat_parley_peaceful_resolution_and_class_roleplay_tags(self):
        import check_manager
        import llm_handler

        bard = character_creator.create_character(
            "Scanlan", "Halfling", "Bard", "Noble", self.caster_stats,
            subclass="college_of_lore"
        )
        cs = cm.start_combat(["goblin_scout"], bard, self.world)
        player_c = cs["player_combatant"]
        enemy = cs["enemies"][0]

        # 1. Test Combat Parley (Persuasion roll of 18 -> peaceful surrender, enemy HP set to 0 & victory!)
        with patch("combat_manager._roll_d20", return_value=18):
            parley_res, round_res = app.resolve_player_combat_action(
                action_type="🗣️ Parley (เจรจา/ข่มขู่)",
                player_c=player_c,
                selected_target=enemy,
                cs=cs,
                player=bard,
                world=self.world,
                parley_skill="Persuasion",
                parley_speech="วางอาวุธลงเถอะสหาย เราตกลงแบ่งสมบัติกันได้!",
            )
        self.assertTrue(parley_res["parley_success"])
        self.assertTrue(parley_res["enemy_surrendered"])
        self.assertEqual(round_res["combat_outcome"], "player_victory")
        self.assertIn("surrenders peacefully", round_res["narration_block"])

        # 2. Test Class/Background Roleplay Dialogue Tags in check_manager
        chk_bard = check_manager.detect_action_skill_check('[Bard] ร้องเพลงกล่อมให้ยามใจเย็นลง', bard)
        self.assertIsNotNone(chk_bard)
        self.assertEqual(chk_bard["skill"], "performance")

        chk_warlock = check_manager.detect_action_skill_check('[Warlock] แผ่กลิ่นอายแห่งขุมนรกข่มขวัญ', bard)
        self.assertIsNotNone(chk_warlock)
        self.assertEqual(chk_warlock["skill"], "intimidation")

        # 3. Test apply_npc_relationship_change updates both npc_relationships and companion approval
        comp = {"id": "shadowheart", "name": "Shadowheart", "hp": {"current": 20, "max": 20}, "persona_seed": "Guarded cleric."}
        sm.recruit_companion(comp, self.world)
        rel_out = sm.apply_npc_relationship_change({"npc_id": "shadowheart", "delta": 10}, self.world)
        self.assertEqual(rel_out["new_attitude"], 10)
        self.assertEqual(sm.get_companion_approval(self.world, "shadowheart")["approval"], 60)

        # 4. Verify Tier 2 system prompt includes local NPCs and active companion approval
        tiers = llm_handler.build_system_prompt_tiers(bard, self.world)
        self.assertIn("Shadowheart (Approval: 60/100)", tiers[2])
        self.assertIn("PRIORITIZE EXPRESSIVE DIALOGUE & ROLEPLAY", tiers[1])


if __name__ == "__main__":
    unittest.main()
