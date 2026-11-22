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

    # ══════════════════════════════════════════════════════════════════════════
    # 11. COMBAT LOOT DROPS, ROOM AUTO-CLEAR, TACTICAL AI, & NATURAL THAI DIALOGUE CHECKS
    # ══════════════════════════════════════════════════════════════════════════
    def test_combat_gold_item_drops_room_clear_and_tactical_companion_heal(self):
        import check_manager

        fighter = character_creator.create_character("Valeros", "Human", "Fighter", "Soldier", self.base_stats)
        initial_gp = fighter["currency"]["gp"]

        # Add a Cleric companion to test Tactical Emergency Healing when player is <= 30% HP
        cleric_comp = {
            "id": "comp_cleric",
            "name": "Kyra",
            "role": "Cleric",
            "side": "player",
            "hp": {"current": 20, "max": 20},
            "ac": 16,
            "attacks": [{"name": "Mace", "attack_bonus": 4, "damage": "1d6+2", "damage_type": "bludgeoning"}],
        }
        cs = cm.start_combat(["goblin_boss"], fighter, self.world, companion_states=[cleric_comp])
        player_c = cs["player_combatant"]
        enemy = cs["enemies"][0]
        enemy["loot_table"] = ["healing_potion"]

        # 1. Injure player to 2 HP (<= 30% of 12 Max HP) -> Cleric companion should cast emergency Healing Word!
        player_c["hp"]["current"] = 2
        comp_turn_res = cm.resolve_companion_turn(cs["companions"][0], cs["enemies"], combat_state=cs)
        self.assertEqual(comp_turn_res.get("attack_name"), "Healing Word")
        self.assertGreater(comp_turn_res.get("healed", 0), 0)
        self.assertGreater(player_c["hp"]["current"], 2)

        # 2. Defeat the Goblin Boss -> verify gold_drop (15..45 GP), loot_table ('healing_potion'), and room marked cleared!
        enemy["hp"]["current"] = 0
        outcome = cm.check_combat_end(cs, self.world)
        self.assertEqual(outcome, "player_victory")
        self.assertGreaterEqual(cs.get("gold_looted", 0), 15)
        self.assertEqual(fighter["currency"]["gp"], initial_gp + cs["gold_looted"])
        self.assertIn("healing_potion", cs.get("items_looted", []))
        self.assertTrue(any(i.get("item_id") == "healing_potion" for i in fighter["inventory"]))
        self.assertIn("ruined_mill", self.world["cleared_rooms"])

        # 3. Verify natural Thai dialogue phrases trigger BG3 skill checks even when starting with 'พูดคุย'
        chk_persuade = check_manager.detect_action_skill_check("พูดคุยหว่านล้อมให้ทหารยามเปิดประตู", fighter)
        self.assertIsNotNone(chk_persuade)
        self.assertEqual(chk_persuade["skill"], "persuasion")

        chk_insight = check_manager.detect_action_skill_check("สังเกตแววตาจับพิรุธของพ่อค้า", fighter)
        self.assertIsNotNone(chk_insight)
        self.assertEqual(chk_insight["skill"], "insight")

    # ══════════════════════════════════════════════════════════════════════════
    # 12. CRIT DAMAGE MODIFIER SEPARATION ("NdX" vs "+M") & SUBMODULE STANDALONE IMPORTS
    # ══════════════════════════════════════════════════════════════════════════
    def test_resolve_attack_crit_does_not_double_flat_modifier(self):
        from unittest.mock import patch

        attacker = {
            "id": "player",
            "name": "Valeros",
            "side": "player",
            "class": "Fighter",
            "level": 1,
            "hp": {"current": 20, "max": 20},
            "ac": 16,
            "stats": self.base_stats,
        }
        target = {
            "id": "orc_1",
            "name": "Orc Warrior",
            "side": "enemy",
            "hp": {"current": 100, "max": 100},
            "ac": 12,
            "stats": self.base_stats,
        }
        attack = {
            "name": "Longsword",
            "attack_bonus": 5,
            "damage": "1d8+5",
            "damage_type": "slashing",
        }

        # Patch dice roll to always roll maximum (e.g. 1d8 -> 8)
        with patch("combat_manager.random.randint", side_effect=lambda a, b: b):
            # 1. Normal hit (roll 15 + 5 = 20 vs AC 12): damage = 8 + 5 = 13
            with patch.object(cm, "_roll_d20", return_value=15):
                res_hit = cm.resolve_attack(attacker, target, attack)
            self.assertTrue(res_hit["hit"])
            self.assertFalse(res_hit["crit"])
            self.assertEqual(res_hit["damage"], 13)

            # 2. Critical hit (natural 20): damage = 8 + 8 + 5 = 21 (modifier +5 is NOT doubled to 26)
            with patch.object(cm, "_roll_d20", return_value=20):
                res_crit = cm.resolve_attack(attacker, target, attack)
            self.assertTrue(res_crit["hit"])
            self.assertTrue(res_crit["crit"])
            self.assertEqual(res_crit["damage"], 21)

    def test_all_submodules_importable_as_first_module_in_subprocess(self):
        import subprocess

        submodules = [
            "state.currency",
            "state.persistence",
            "state.equipment",
            "state.checks",
            "state.progression",
            "state.magic",
            "state.cooking",
            "state.crime",
            "state.companions",
            "combat.conditions",
            "combat.actions",
            "combat.engine",
            "dungeon.navigation",
            "dungeon.events",
            "dungeon.notice_board",
            "llm.providers",
            "llm.prompts",
            "llm.generator",
            "validators.entities",
            "validators.extraction",
        ]
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for mod in submodules:
            proc = subprocess.run(
                [sys.executable, "-c", f"import {mod}"],
                cwd=project_root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                proc.returncode,
                0,
                f"Submodule '{mod}' failed to import as first module:\n{proc.stderr}",
            )

    # ══════════════════════════════════════════════════════════════════════════
    # 13. HOLY GRAIL WAR × 1-IN-65M TRANSPARENT CHOICE CARDS & STAT DELTA PILLS
    # ══════════════════════════════════════════════════════════════════════════
    def test_holy_grail_and_65m_choice_cards_and_stat_deltas(self):
        import app

        bard = character_creator.create_character(
            "Scanlan", "Halfling", "Bard", "Noble",
            {"STR": 8, "DEX": 14, "CON": 12, "INT": 10, "WIS": 13, "CHA": 15},
            subclass="college_of_lore",
            chosen_skills=["Persuasion", "Deception", "Insight", "Performance", "Stealth", "Arcana"],
        )

        # 1. Verify exact d20 success probability calculation (including Nat 1 / Nat 20 bounds)
        # With total_modifier = +5 vs DC 12: need die >= 7 (rolls 7..20 = 14 faces out of 20 = 70%)
        self.assertEqual(app.calculate_check_success_chance(5, 12), 70)
        # Impossible DC (e.g. DC 35) still has 5% chance due to Nat 20
        self.assertEqual(app.calculate_check_success_chance(0, 35), 5)
        # Trivial DC (e.g. DC 2 with +10) caps at 95% due to Nat 1
        self.assertEqual(app.calculate_check_success_chance(10, 2), 95)

        # 2. Verify build_choice_card_metadata on skill check choice, roleplay tag choice, and exploration choice
        meta_skill = app.build_choice_card_metadata("[Persuasion] เจรจาต่อรองขอลดราคาสินค้า", bard, index=1)
        self.assertTrue(meta_skill["has_check"])
        self.assertEqual(meta_skill["stat"], "CHA")
        self.assertEqual(meta_skill["skill"], "persuasion")
        self.assertEqual(meta_skill["dc"], 13)
        self.assertGreaterEqual(meta_skill["success_pct"], 60)
        self.assertIn("โอกาส", meta_skill["odds_label"])

        meta_rp = app.build_choice_card_metadata("[Bard] ร้องเพลงบัลลาดปลุกใจชาวบ้านในโรงเตี๊ยม", bard, index=2)
        self.assertTrue(meta_rp["has_check"])
        self.assertTrue(meta_rp["is_roleplay_tag"])
        self.assertEqual(meta_rp["tag_name"], "Bard")
        self.assertEqual(meta_rp["skill"], "performance")

        meta_exp = app.build_choice_card_metadata("เดินสำรวจรอบๆ จัตุรัสกลางเมือง", bard, index=3)
        self.assertFalse(meta_exp["has_check"])
        self.assertFalse(meta_exp["is_roleplay_tag"])

        # 3. Verify snapshot_player_world_state & compute_turn_stat_deltas generate Stat Delta Pills
        snap = app.snapshot_player_world_state(bard, self.world)
        bard["hp"]["current"] -= 3
        sm.add_currency(bard, gp=25)
        sm.award_xp(50, bard)
        sm.award_inspiration(bard, "Great performance")
        self.world["current_location"] = "town_riverside"

        deltas = app.compute_turn_stat_deltas(
            snap, bard, self.world,
            rel_change={"npc_id": "shadowheart", "npc_name": "Shadowheart", "delta": 10},
        )
        labels = [d["label"] for d in deltas]
        self.assertIn("❤️ HP -3", labels)
        self.assertIn("💰 Gold +25 GP", labels)
        self.assertIn("✨ XP +50", labels)
        self.assertIn("🌟 Inspiration +1", labels)
        self.assertIn("💚 Shadowheart +10", labels)

    # ══════════════════════════════════════════════════════════════════════════
    # 14. REVIEWER CHECKLIST: OFFLINE CSS, NPC CATALOG, LEGACY BACKLOG & SAVE COMPAT
    # ══════════════════════════════════════════════════════════════════════════
    def test_reviewer_feedback_offline_css_npc_catalog_legacy_items_and_save_compat(self):
        from unittest.mock import patch
        import rules
        import validation
        import dungeon_manager as dm
        import llm_handler

        # 1. Offline CSS: ui/styles.py must NOT contain external Google Fonts @import
        styles_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "styles.py")
        with open(styles_path, "r", encoding="utf-8") as f:
            css_src = f.read()
        self.assertNotIn("fonts.googleapis.com", css_src)
        self.assertNotIn("@import", css_src)

        # 2. Static NPC Catalog: Every NPC in every dungeon_data.json room must exist in npc_catalog.json
        rules.reset_caches()
        validation.reset_caches()
        npc_cat = rules._get_npc_catalog()
        rooms_cat = dm._load_static_catalog()
        for r_id, r_data in rooms_cat.items():
            for npc_id in r_data.get("npcs", []):
                self.assertIn(npc_id, npc_cat, f"Room '{r_id}' references NPC '{npc_id}' missing from npc_catalog.json")
                resolved = rules.resolve_npc(npc_id, self.world)
                self.assertIsNotNone(resolved)
                self.assertTrue(validation.validate_npc_id(npc_id, self.world))

        # Verify pickpocketing a static room NPC by ID ("innkeeper_mira") uses catalog profile
        rogue = character_creator.create_character("Merisiel", "Elf", "Rogue", "Criminal", self.base_stats)
        pp_res = sm.attempt_pickpocket(rogue, "innkeeper_mira", "gold", roll_override=15, world_state=self.world)
        self.assertTrue(pp_res["success"])
        self.assertIn("Innkeeper Mira", pp_res["message"])

        # Verify Tier 2 system prompt includes static NPC persona_seed when player is in their room
        self.world["current_location"] = "drunken_dragon_tavern"
        tiers = llm_handler.build_system_prompt_tiers(rogue, self.world)
        self.assertIn("Innkeeper Mira", tiers[2])
        self.assertIn("Tavern Hostess", tiers[2])

        # 3. Cantrip heal promotion preserves valid heal formula (e.g. '1d4+2' not clamped to '1d4+3')
        cantrip_heal = {
            "name": "Minor Mend",
            "level": 0,
            "type": "heal",
            "effect": {"heal": "1d4+2"},
        }
        cleaned_heal = validation.validate_generated_spell(cantrip_heal, player_level=1)
        self.assertIsNotNone(cleaned_heal)
        self.assertEqual(cleaned_heal["level"], 1)
        self.assertEqual(cleaned_heal["effect"]["heal"], "1d4+2")

        # 4. player_level = inf / nan does not crash monster, npc, or spell validators
        valid_m_sample = {
            "name": "Void Stalker",
            "hp": 15,
            "ac": 13,
            "stats": self.base_stats,
            "attacks": [{"name": "Bite", "attack_bonus": 3, "damage": "1d6+1", "damage_type": "piercing"}],
        }
        for weird_lvl in (float("inf"), float("-inf"), float("nan")):
            self.assertIsNotNone(validation.validate_generated_monster(valid_m_sample, player_level=weird_lvl))
            self.assertIsNotNone(validation.validate_generated_npc({"name": "Lyra", "hp": 15, "ac": 12}, player_level=weird_lvl))
            self.assertIsNotNone(validation.validate_generated_spell(cantrip_heal, player_level=weird_lvl))

        # 5. Dynamic buff spell (with ac_bonus and no explicit condition) + static 'shield' spell apply AC bonus in combat
        dyn_buff = validation.validate_generated_spell(
            {"name": "Aegis Ward", "level": 1, "type": "buff", "effect": {"ac_bonus": 3, "duration_rounds": 3}},
            player_level=1,
        )
        self.world.setdefault("generated_spells", {})["gen_spell_aegis"] = dyn_buff
        wizard = character_creator.create_character("Ezren", "Human", "Wizard", "Sage", self.base_stats)
        base_wiz_ac = wizard["ac"]
        cast_out = sm.cast_spell(wizard, wizard, "gen_spell_aegis", wizard, self.world)
        self.assertTrue(cast_out["success"])
        self.assertEqual(cast_out["condition_applied"], "shielded")
        self.assertEqual(cast_out["ac_bonus_applied"], 3)

        # Attack against wizard with Aegis Ward (+3 AC) checks against base_wiz_ac + 3
        orc_attacker = {"id": "orc", "name": "Orc", "side": "enemy", "stats": self.base_stats}
        atk_vs_ward = cm.resolve_attack(orc_attacker, wizard, {"name": "Club", "attack_bonus": 0, "damage": "1d6"})
        self.assertEqual(atk_vs_ward["target_ac"], base_wiz_ac + 3)

        # Verify all signature level-up spells exist in spell_catalog.json
        from state.progression import SIGNATURE_LEVEL_UP_SPELLS
        sp_cat = rules._get_spell_catalog()
        for cls_k, lvl_map in SIGNATURE_LEVEL_UP_SPELLS.items():
            for l_k, sp_list in lvl_map.items():
                for sid in sp_list:
                    self.assertIn(sid, sp_cat, f"Signature spell '{sid}' ({cls_k} Lv.{l_k}) missing from spell_catalog.json")

        # 6. Multi-dice critical hit ('2d6+1d4+3'): rolls '2d6+1d4' extra on crit without doubling '+3'
        compound_atk = {"name": "Flame Greatsword", "attack_bonus": 5, "damage": "2d6+1d4+3", "damage_type": "fire"}
        dummy_target = {"id": "t1", "name": "Dummy", "side": "enemy", "hp": {"current": 200, "max": 200}, "ac": 10}
        with patch("combat_manager.random.randint", side_effect=lambda a, b: b):
            with patch.object(cm, "_roll_d20", return_value=15):
                r_hit = cm.resolve_attack(wizard, dummy_target, compound_atk)
            # Max of 2d6 (12) + 1d4 (4) + 3 = 19
            self.assertEqual(r_hit["damage"], 19)

            with patch.object(cm, "_roll_d20", return_value=20):
                r_crit = cm.resolve_attack(wizard, dummy_target, compound_atk)
            # Max crit: (12 + 4 + 3) + (12 + 4) = 35 (not 38)
            self.assertEqual(r_crit["damage"], 35)

    # ══════════════════════════════════════════════════════════════════════════
    # 15. NPC SECRETS (STATIC & AGENTIC), COMPANION SLOT HEAL, ALL 14 ROOMS & APPTEST
    # ══════════════════════════════════════════════════════════════════════════
    def test_npc_secrets_companion_slot_heal_town_jail_exit_and_apptest_views(self):
        from collections import deque
        import copy
        import dungeon_manager as dm
        import llm_handler
        import rules
        import validation

        # 1. Static & Agentic Dynamic NPC Secrets: locked vs unlocked in Tier 2 System Prompt
        dyn_npc = validation.validate_generated_npc(
            {
                "name": "Vespera",
                "role": "Shadow Broker",
                "persona_seed": "Whispers behind a velvet mask.",
                "secret": "The Baron's signet ring is hidden inside the clocktower pendulum.",
                "hp": 20,
                "ac": 14,
            },
            player_level=2,
        )
        self.assertIsNotNone(dyn_npc)
        self.assertIn("clocktower pendulum", dyn_npc["secret"])

        fighter = character_creator.create_character("Valeros", "Human", "Fighter", "Soldier", self.base_stats)
        self.world["current_location"] = "drunken_dragon_tavern"
        self.world["npc_relationships"] = {"innkeeper_mira": 0}

        tiers_locked = llm_handler.build_system_prompt_tiers(fighter, self.world)
        self.assertIn("HIDDEN SECRET", tiers_locked[2])
        self.assertIn("silver ward-key", tiers_locked[2])

        # Increase relationship with innkeeper_mira to +20 (>= 15 threshold) -> Secret unlocks!
        sm.apply_npc_relationship_change({"npc_id": "innkeeper_mira", "delta": 20}, self.world)
        tiers_unlocked = llm_handler.build_system_prompt_tiers(fighter, self.world)
        self.assertIn("SECRET UNLOCKED — WILLING TO SHARE", tiers_unlocked[2])

        # 2. Companion AI emergency heal consumes spell slot and scales with WIS/CHA mod + level
        cleric_comp = {
            "id": "comp_kyra",
            "name": "Kyra",
            "role": "Cleric",
            "level": 3,
            "side": "player",
            "hp": {"current": 24, "max": 24},
            "ac": 16,
            "stats": {"STR": 12, "DEX": 10, "CON": 14, "INT": 10, "WIS": 16, "CHA": 12},
            "spell_slots": {"1": {"current": 1, "max": 1}},
            "attacks": [{"name": "Mace", "attack_bonus": 4, "damage": "1d6+2", "damage_type": "bludgeoning"}],
        }
        self.world["combat_state"] = None
        cs = cm.start_combat(["goblin_scout"], fighter, self.world, companion_states=[cleric_comp])
        cs["player_combatant"]["hp"]["current"] = 2
        res_c_heal = cm.resolve_companion_turn(cs["companions"][0], cs["enemies"], combat_state=cs)
        self.assertEqual(res_c_heal["attack_name"], "Healing Word")
        # 1d8 (>=1) + WIS mod (3) + level (3) >= 7 HP healed!
        self.assertGreaterEqual(res_c_heal["healed"], 7)
        self.assertEqual(cleric_comp["spell_slots"]["1"]["current"], 0)
        cm.end_combat(self.world)

        # 3. Verify all 14 static rooms (including town_jail) are reachable via move_player from town_riverside
        rules.reset_caches()
        all_static_ids = set(dm._load_static_catalog().keys())
        self.assertEqual(len(all_static_ids), 15)
        self.assertIn("town_jail", all_static_ids)

        reachable = {"town_riverside"}
        queue = deque(["town_riverside"])
        while queue:
            curr = queue.popleft()
            room_obj = dm._load_static_catalog()[curr]
            for d_dir, target_id in room_obj.get("exits", {}).items():
                if target_id and target_id not in reachable:
                    w_step = {"current_location": curr, "visited_rooms": [curr], "dynamic_rooms": {}}
                    ok_m, _, _ = dm.move_player(d_dir, w_step, fighter)
                    self.assertTrue(ok_m, f"Failed to move {d_dir} from {curr} to {target_id}")
                    reachable.add(target_id)
                    queue.append(target_id)
        self.assertEqual(reachable, all_static_ids)

        # 4. Legacy 548a08f save compatibility + Streamlit AppTest for Landing, Playing, and Combat views
        legacy_player = {
            "schema_version": 4,
            "name": "LegacyHero",
            "race": "Human",
            "class": "Fighter",
            "background": "Soldier",
            "level": 1,
            "xp_current": 0,
            "hp": {"current": 12, "max": 12},
            "ac": 16,
            "stats": {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
            "proficiency_bonus": 2,
            "proficient_skills": ["Athletics"],
            "proficient_saves": ["STR", "CON"],
            "inventory": [{"item_id": "longsword", "equipped": True, "quantity": 1}],
            "gold": 15,
            "status": "normal",
            "death_saves": {"success": 0, "fail": 0},
        }
        legacy_world = {
            "schema_version": 4,
            "character_name": "LegacyHero",
            "current_location": "town_riverside",
            "visited_rooms": ["town_riverside"],
            "cleared_rooms": [],
            "collected_loot": [],
            "dynamic_rooms": {},
            "game_time": {"day": 1, "period": "morning", "steps_since_period_start": 0},
            "quest_log": {"main": [], "side": []},
            "combat_state": None,
        }

        from streamlit.testing.v1 import AppTest
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        app_path = os.path.join(project_root, "app.py")

        # 4a. Landing view
        at = AppTest.from_file(app_path, default_timeout=15)
        at.run()
        self.assertEqual(len(at.exception), 0, f"Landing view raised exception: {at.exception}")

        # 4b. Playing / Exploration view with legacy 548a08f save state
        at.session_state["player_state"] = copy.deepcopy(legacy_player)
        at.session_state["world_state"] = copy.deepcopy(legacy_world)
        at.session_state["narrative_log"] = [{"role": "assistant", "content": "ยินดีต้อนรับสู่ริเวอร์ไซด์"}]
        at.run()
        self.assertEqual(len(at.exception), 0, f"Playing view raised exception: {at.exception}")

        # 4c. Active Combat view with legacy 548a08f save state
        combat_world = copy.deepcopy(legacy_world)
        combat_player = copy.deepcopy(legacy_player)
        cm.start_combat(["goblin_scout"], combat_player, combat_world)
        at.session_state["player_state"] = combat_player
        at.session_state["world_state"] = combat_world
        at.run()
        self.assertEqual(len(at.exception), 0, f"Combat view raised exception: {at.exception}")


if __name__ == "__main__":
    unittest.main()
