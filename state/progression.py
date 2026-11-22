"""
state/progression.py — Experience points, leveling up, BG3 class progression, Feats/ASI,
time period changes, short/long rests, and downed outcomes.
"""

import json
import logging
import math
import os
import random
from typing import Any, Dict, List, Optional, Tuple

import paths
from rules import _get_item_catalog, get_modifier
from state.currency import SP_PER_GP, _ensure_currency, subtract_currency

logger = logging.getLogger("state_manager")

LEVEL_THRESHOLDS: Dict[int, int] = {1: 0, 2: 300, 3: 900, 4: 2700, 5: 6500}

BG3_FEATS: Dict[str, Dict[str, str]] = {
    "alert": {
        "name": "Alert (ระวังภัยสูงสุด)",
        "description": "+5 Initiative — ได้เริ่มเทิร์นก่อนศัตรูเกือบทุกครั้ง และไม่ถูก Surprise",
    },
    "tough": {
        "name": "Tough (อึดถึกทน)",
        "description": "+2 Max HP ต่อเลเวลของตัวละคร (เพิ่มเลือดสูงสุดทันทีและทุกเลเวลถัดไป)",
    },
    "great_weapon_master": {
        "name": "Great Weapon Master (จ้าวศัสตราหนัก)",
        "description": "+5 ความเสียหายเมื่อโจมตีระยะประชิด (Melee Damage Bonus)",
    },
    "sharpshooter": {
        "name": "Sharpshooter (จอมแม่นปืน/ธนู)",
        "description": "+5 ความเสียหายเมื่อโจมตีระยะไกล (Ranged Damage Bonus)",
    },
    "war_caster": {
        "name": "War Caster (จอมเวทสมรภูมิ)",
        "description": "ได้เปรียบ (Advantage) เมื่อทอยรักษาสมาธิเวท (Concentration Save)",
    },
    "lucky": {
        "name": "Lucky (ดวงมหาโชค)",
        "description": "เพิ่มแต้ม Inspiration สูงสุด +1 และรับ 1 Inspiration ทันที",
    },
}

SIGNATURE_LEVEL_UP_SPELLS: Dict[str, Dict[int, List[str]]] = {
    "wizard": {
        2: ["scorching_ray", "misty_step"],
        3: ["hold_person", "shatter"],
        4: ["mirror_image"],
        5: ["fireball", "haste", "counterspell"],
    },
    "cleric": {
        2: ["lesser_restoration", "spiritual_weapon"],
        3: ["hold_person", "silence"],
        4: ["remove_curse"],
        5: ["spirit_guardians", "mass_healing_word", "revivify"],
    },
    "bard": {
        2: ["shatter", "invisibility"],
        3: ["hold_person", "lesser_restoration"],
        4: ["silence"],
        5: ["hypnotic_pattern", "mass_healing_word"],
    },
    "paladin": {
        2: ["cure_wounds", "divine_favor", "bless", "command"],
        3: ["shield_of_faith", "thunderous_smite"],
        5: ["lesser_restoration", "magic_weapon"],
    },
    "warlock": {
        2: ["hellish_rebuke"],
        3: ["misty_step", "scorching_ray", "hold_person"],
        5: ["fireball", "hunger_of_hadar", "counterspell"],
    },
    "fighter": {
        3: ["shield", "magic_missile", "fire_bolt"],  # Eldritch Knight
    },
    "rogue": {
        3: ["sleep", "invisibility", "minor_illusion"],  # Arcane Trickster
    },
    "druid": {
        2: ["lesser_restoration", "speak_with_animals"],
        3: ["hold_person", "misty_step"],
        5: ["mass_healing_word", "revivify"],
    },
    "sorcerer": {
        2: ["detect_thoughts", "scorching_ray", "misty_step"],
        3: ["hold_person", "invisibility"],
        5: ["fireball", "haste", "counterspell"],
    },
    "ranger": {
        2: ["cure_wounds", "speak_with_animals", "entangle"],
        3: ["lesser_restoration", "misty_step"],
        5: ["silence"],
    },
}


def award_xp(amount: int, state: Dict[str, Any]) -> int:
    """Add XP to state["xp_current"]. Negative or zero amounts are clean no-ops."""
    if amount <= 0:
        return state.get("xp_current", 0)
    current_xp = state.get("xp_current", 0)
    new_xp = current_xp + amount
    state["xp_current"] = new_xp
    logger.debug(f"award_xp: awarded {amount} XP. Total now: {new_xp}")
    return new_xp


def check_level_up(state: Dict[str, Any]) -> bool:
    """Check if state["xp_current"] crosses threshold for level + 1 (capped at level 5)."""
    lvl = state.get("level", 1)
    if lvl >= 5:
        return False
    threshold = LEVEL_THRESHOLDS.get(lvl + 1, 999999)
    return state.get("xp_current", 0) >= threshold


def _sync_class_resources_on_level_up(state: Dict[str, Any], new_lvl: int) -> None:
    """Update class resources and unlocked features when leveling up."""
    cls_id = str(state.get("class", state.get("class_name", "Fighter"))).strip().lower()
    resources = state.setdefault("class_resources", {})

    if cls_id == "fighter":
        resources.setdefault("second_wind", {"current": 1, "max": 1})
        if new_lvl >= 2:
            resources["action_surge"] = {"current": 1, "max": 1}
        if new_lvl >= 3:
            resources["superiority_dice"] = {"current": 4, "max": 4}
    elif cls_id == "rogue":
        resources["sneak_attack_dice"] = (new_lvl + 1) // 2
        resources["cunning_action"] = True
    elif cls_id == "wizard":
        resources.setdefault("arcane_recovery", {"current": 1, "max": 1})
    elif cls_id == "cleric":
        resources.setdefault("channel_divinity", {"current": 1, "max": 1})
    elif cls_id == "bard":
        cha_mod = max(1, get_modifier(state.get("stats", {}).get("CHA", 10)))
        bi = state.setdefault("bardic_inspiration", {"current": cha_mod, "max": cha_mod})
        bi["max"] = cha_mod
        bi["current"] = cha_mod
        resources["bardic_inspiration"] = bi
    elif cls_id == "paladin":
        resources["lay_on_hands"] = {"current": new_lvl * 5, "max": new_lvl * 5}
        resources.setdefault("channel_oath", {"current": 1, "max": 1})
    elif cls_id == "barbarian":
        rage_max = 3 if new_lvl >= 3 else 2
        resources["rage_charges"] = {"current": rage_max, "max": rage_max}
    elif cls_id == "warlock":
        resources["pact_magic"] = True
    elif cls_id == "druid":
        resources.setdefault("wild_shape", {"current": 2, "max": 2})
    elif cls_id == "sorcerer":
        resources["sorcery_points"] = {"current": new_lvl, "max": new_lvl}
    elif cls_id == "monk":
        resources["ki_points"] = {"current": new_lvl, "max": new_lvl}
    elif cls_id == "ranger":
        resources["favored_enemy"] = True

    # Load features_by_level from classes_catalog.json
    try:
        cat_path = os.path.join(paths.CATALOG_DIR, "classes_catalog.json")
        with open(cat_path, "r", encoding="utf-8") as f:
            classes_cat = json.load(f)
        cls_data = classes_cat.get(cls_id, {})
        lvl_feats = cls_data.get("features_by_level", {}).get(str(new_lvl), [])
        c_feats = state.setdefault("class_features", [])
        for feat_name in lvl_feats:
            if feat_name not in c_feats:
                c_feats.append(feat_name)
    except Exception:
        pass


def apply_level_up(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Increments level, updates proficiency_bonus, hit points (max and current),
    class features/resources, signature spells, and spell slots for spellcasting classes.
    At Level 4, grants a pending Feat / ASI choice (`pending_level_up_choices`).
    """
    if not check_level_up(state):
        return {"leveled_up": False, "level": state.get("level", 1)}

    old_lvl = state.get("level", 1)
    new_lvl = min(5, old_lvl + 1)
    state["level"] = new_lvl

    state["proficiency_bonus"] = 3 if new_lvl == 5 else 2

    cls_name = state.get("class", state.get("class_name", "Fighter"))
    cls_id = str(cls_name).strip().lower()
    if cls_name in ("Fighter", "Paladin", "Ranger") or cls_id in ("fighter", "paladin", "ranger"):
        hit_die_avg = 6
    elif cls_name == "Barbarian" or cls_id == "barbarian":
        hit_die_avg = 7
    elif cls_name in ("Wizard", "Sorcerer") or cls_id in ("wizard", "sorcerer"):
        hit_die_avg = 4
    else:
        hit_die_avg = 5

    con_mod = get_modifier(state.get("stats", {}).get("CON", 10))
    tough_bonus = 2 if "tough" in state.get("feats", []) else 0
    draconic_bonus = 1 if (cls_id == "sorcerer" and state.get("subclass") == "draconic_bloodline") else 0
    hp_gain = max(1, hit_die_avg + con_mod + tough_bonus + draconic_bonus)

    hp = state.setdefault("hp", {"current": 10, "max": 10})
    hp["max"] = hp.get("max", 10) + hp_gain
    hp["current"] = hp.get("current", 10) + hp_gain

    # Full casters: Bard, Cleric, Wizard, Druid, Sorcerer
    if cls_name in ("Bard", "Cleric", "Wizard", "Druid", "Sorcerer") or cls_id in ("bard", "cleric", "wizard", "druid", "sorcerer"):
        slots = state.setdefault("spell_slots", {})
        target_slots_table = {
            1: {"1": 2},
            2: {"1": 3},
            3: {"1": 4, "2": 2},
            4: {"1": 4, "2": 3},
            5: {"1": 4, "2": 3, "3": 2},
        }
        target_cfg = target_slots_table.get(new_lvl, {"1": 2})
        for tier_str, target_max in target_cfg.items():
            curr_info = slots.setdefault(tier_str, {"max": 0, "current": 0})
            old_max = curr_info.get("max", 0)
            delta = max(0, target_max - old_max)
            curr_info["max"] = target_max
            curr_info["current"] = curr_info.get("current", 0) + delta

    # Half-casters: Paladin, Ranger (unlock/upgrade spell slots at Level 2+)
    elif cls_id in ("paladin", "ranger"):
        slots = state.setdefault("spell_slots", {})
        paladin_slots_table = {
            2: {"1": 2},
            3: {"1": 3},
            4: {"1": 3},
            5: {"1": 4, "2": 2},
        }
        target_cfg = paladin_slots_table.get(new_lvl, {})
        for tier_str, target_max in target_cfg.items():
            curr_info = slots.setdefault(tier_str, {"max": 0, "current": 0})
            old_max = curr_info.get("max", 0)
            delta = max(0, target_max - old_max)
            curr_info["max"] = target_max
            curr_info["current"] = curr_info.get("current", 0) + delta

    # Pact Magic: Warlock (recharges on Short Rest; upgrades slot tiers at Lv3 and Lv5)
    elif cls_id == "warlock":
        slots = state.setdefault("spell_slots", {})
        warlock_slots_table = {
            1: {"1": 1},
            2: {"1": 2},
            3: {"1": 2, "2": 2},
            4: {"1": 2, "2": 2},
            5: {"1": 2, "2": 2, "3": 2},
        }
        target_cfg = warlock_slots_table.get(new_lvl, {"1": 2})
        for tier_str, target_max in target_cfg.items():
            curr_info = slots.setdefault(tier_str, {"max": 0, "current": 0})
            old_max = curr_info.get("max", 0)
            delta = max(0, target_max - old_max)
            curr_info["max"] = target_max
            curr_info["current"] = curr_info.get("current", 0) + delta

    # Eldritch Knight / Arcane Trickster (Lv3+)
    elif (cls_id == "fighter" and state.get("subclass") == "eldritch_knight" and new_lvl >= 3) or (
        cls_id == "rogue" and state.get("subclass") == "arcane_trickster" and new_lvl >= 3
    ):
        slots = state.setdefault("spell_slots", {})
        curr_info = slots.setdefault("1", {"max": 0, "current": 0})
        target_max = 3 if new_lvl >= 4 else 2
        delta = max(0, target_max - curr_info.get("max", 0))
        curr_info["max"] = target_max
        curr_info["current"] = curr_info.get("current", 0) + delta

    # Auto-learn signature spells for this level if applicable
    known = state.setdefault("known_spells", [])
    sig_for_cls = SIGNATURE_LEVEL_UP_SPELLS.get(cls_id, {}).get(new_lvl, [])
    learned_now = []
    if cls_id in ("wizard", "cleric", "bard", "paladin", "warlock", "druid", "sorcerer", "ranger") or (
        cls_id == "fighter" and state.get("subclass") == "eldritch_knight"
    ) or (
        cls_id == "rogue" and state.get("subclass") == "arcane_trickster"
    ):
        for sp_id in sig_for_cls:
            if sp_id not in known:
                known.append(sp_id)
                learned_now.append(sp_id)

    _sync_class_resources_on_level_up(state, new_lvl)

    # At Level 4, grant a Feat / Ability Score Improvement choice
    if new_lvl == 4:
        state["pending_level_up_choices"] = state.get("pending_level_up_choices", 0) + 1

    logger.info(f"apply_level_up: {state.get('name')} reached Level {new_lvl}! HP +{hp_gain} (max={hp['max']}).")
    return {
        "leveled_up":        True,
        "old_level":         old_lvl,
        "new_level":         new_lvl,
        "hp_gain":           hp_gain,
        "hp_max":            hp["max"],
        "proficiency_bonus": state["proficiency_bonus"],
        "spells_learned":    learned_now,
    }


def apply_feat_or_asi(
    player_state: Dict[str, Any],
    choice_type: str,
    feat_id: Optional[str] = None,
    stat_1: Optional[str] = None,
    stat_2: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Apply a Level 4 BG3 Feat or Ability Score Improvement (+2 to one stat, or +1 to two stats).
    """
    if choice_type == "asi":
        stats = player_state.setdefault("stats", {})
        if not stat_1 or stat_1 not in stats:
            return False, "กรุณาเลือกค่าสถานะที่ต้องการเพิ่ม"
        old_con_mod = get_modifier(stats.get("CON", 10))
        if not stat_2 or stat_2 == stat_1:
            stats[stat_1] = min(20, stats.get(stat_1, 10) + 2)
            msg = f"เพิ่มค่าสถานะ {stat_1} +2 (เป็น {stats[stat_1]}) เรียบร้อย!"
        else:
            stats[stat_1] = min(20, stats.get(stat_1, 10) + 1)
            stats[stat_2] = min(20, stats.get(stat_2, 10) + 1)
            msg = f"เพิ่มค่าสถานะ {stat_1} +1 และ {stat_2} +1 เรียบร้อย!"

        new_con_mod = get_modifier(stats.get("CON", 10))
        if new_con_mod > old_con_mod:
            hp_diff = (new_con_mod - old_con_mod) * player_state.get("level", 1)
            hp = player_state.setdefault("hp", {"current": 10, "max": 10})
            hp["max"] += hp_diff
            hp["current"] += hp_diff
        import state_manager
        player_state["ac"] = state_manager._compute_ac(player_state)
        if player_state.get("pending_level_up_choices", 0) > 0:
            player_state["pending_level_up_choices"] -= 1
        return True, msg

    elif choice_type == "feat":
        if not feat_id or feat_id not in BG3_FEATS:
            return False, f"ไม่พบ Feat '{feat_id}'"
        feats = player_state.setdefault("feats", [])
        if feat_id in feats:
            return False, f"คุณมี Feat '{BG3_FEATS[feat_id]['name']}' อยู่แล้ว"
        feats.append(feat_id)

        if feat_id == "tough":
            lvl = player_state.get("level", 1)
            hp_bonus = 2 * lvl
            hp = player_state.setdefault("hp", {"current": 10, "max": 10})
            hp["max"] += hp_bonus
            hp["current"] += hp_bonus
        elif feat_id == "lucky":
            player_state["max_inspiration"] = player_state.get("max_inspiration", 4) + 1
            player_state["inspiration"] = min(player_state["max_inspiration"], player_state.get("inspiration", 0) + 1)

        if player_state.get("pending_level_up_choices", 0) > 0:
            player_state["pending_level_up_choices"] -= 1
        return True, f"ปลดล็อก Feat: {BG3_FEATS[feat_id]['name']} เรียบร้อย!"

    return False, "ตัวเลือกไม่ถูกต้อง"


def handle_period_change(
    world_state: Dict[str, Any],
    character_state: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    Spec Section 22d:
    Called when advance_time() triggers period_changed == True.
    If player is outside town, consumes 1 ration item. If none available, applies 'exhausted'.
    """
    import dungeon_manager
    current_room = dungeon_manager.get_current_room(world_state)
    if current_room and current_room.get("type") == "town":
        return True, "In town — no rations consumed."

    catalog = _get_item_catalog()
    inventory = character_state.get("inventory", [])
    ration_item = None
    for item in inventory:
        if isinstance(item, dict):
            item_id = item.get("item_id")
            info = catalog.get(item_id, {})
            if info.get("type") == "ration" and item.get("quantity", 0) > 0:
                ration_item = item
                break

    active_conditions = character_state.setdefault("active_conditions", [])

    if ration_item:
        qty = ration_item.get("quantity", 1)
        if qty > 1:
            ration_item["quantity"] = qty - 1
        else:
            inventory.remove(ration_item)
        if "exhausted" in active_conditions:
            active_conditions.remove("exhausted")
            logger.debug("Consumed ration — removed exhausted condition.")
        return True, "Consumed a ration."
    else:
        if "exhausted" not in active_conditions:
            active_conditions.append("exhausted")
            logger.debug("No rations — applied exhausted condition.")
        return False, "Out of rations! You are now exhausted."


def long_rest(
    world_state: Dict[str, Any],
    character_state: Dict[str, Any],
) -> None:
    """
    Spec Section 22c & BG3 Overhaul:
    Hard overwrite game_time to morning of next day, restore HP to max, clear exhausted,
    restore ALL spell slots and ALL class resources (Rage, Lay on Hands, Arcane Recovery, Bardic Inspiration, etc.).
    """
    gt = world_state.setdefault("game_time", {"day": 1, "period": "morning", "steps_since_period_start": 0})
    gt["period"] = "morning"
    gt["day"] = gt.get("day", 1) + 1
    gt["steps_since_period_start"] = 0

    hp = character_state.setdefault("hp", {"current": 10, "max": 10})
    hp["current"] = hp.get("max", 10)

    active_conditions = character_state.setdefault("active_conditions", [])
    if "exhausted" in active_conditions:
        active_conditions.remove("exhausted")

    import state_manager
    state_manager.tick_food_spoilage(character_state, days=1)

    character_state["weapon_actions_available"] = True
    character_state["concentration"] = None

    # Restore all Spell Slots to max
    for tier_info in character_state.get("spell_slots", {}).values():
        if isinstance(tier_info, dict) and "max" in tier_info:
            tier_info["current"] = tier_info["max"]

    # Restore Bardic Inspiration
    bi = character_state.get("bardic_inspiration")
    if isinstance(bi, dict) and "max" in bi:
        bi["current"] = bi["max"]

    # Restore all Class Resources
    for res_val in character_state.get("class_resources", {}).values():
        if isinstance(res_val, dict) and "max" in res_val:
            res_val["current"] = res_val["max"]

    for comp in world_state.get("party", {}).get("companions", []):
        cid = comp.get("id") or comp.get("name")
        if cid:
            c_app = state_manager.get_companion_approval(world_state, cid)
            c_app["camp_dialogue_pending"] = True

    logger.debug("Long rest completed. HP, spell slots, and class resources restored.")


def perform_short_rest(
    character_state: Dict[str, Any],
    world_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Perform a short rest:
    - Recharges weapon actions
    - Restores up to 50% Max HP (BG3 Short Rest healing)
    - Recharges Short Rest class resources (Fighter Second Wind/Action Surge/Superiority Dice, Cleric Channel Divinity,
      Paladin Channel Oath, Bard Font of Inspiration at Lv5+, and Warlock Pact Magic spell slots!)
    - Advances time by 1 step
    """
    character_state["weapon_actions_available"] = True

    # BG3 Short Rest: heal 50% of Max HP if injured (safe: never reduces HP, and if full HP does nothing)
    hp = character_state.get("hp")
    if isinstance(hp, dict) and "current" in hp and "max" in hp:
        heal_amt = max(1, hp["max"] // 2)
        hp["current"] = min(hp["max"], hp["current"] + heal_amt)

    cls_id = str(character_state.get("class", character_state.get("class_name", ""))).strip().lower()
    resources = character_state.get("class_resources", {})
    for r_key in ("second_wind", "action_surge", "superiority_dice", "channel_divinity", "channel_oath"):
        if r_key in resources and isinstance(resources[r_key], dict):
            resources[r_key]["current"] = resources[r_key].get("max", 1)

    # Warlock Pact Magic: recharge all spell slots on Short Rest!
    if cls_id == "warlock" or resources.get("pact_magic"):
        for tier_info in character_state.get("spell_slots", {}).values():
            if isinstance(tier_info, dict) and "max" in tier_info:
                tier_info["current"] = tier_info["max"]

    # Bard Lv5 Font of Inspiration: recharge Bardic Inspiration on Short Rest
    if cls_id == "bard" and character_state.get("level", 1) >= 5:
        bi = character_state.get("bardic_inspiration")
        if isinstance(bi, dict) and "max" in bi:
            bi["current"] = bi["max"]

    if world_state:
        import dungeon_manager
        import state_manager
        dungeon_manager.advance_time(world_state, steps=1, character_state=character_state)
        for comp in world_state.get("party", {}).get("companions", []):
            cid = comp.get("id") or comp.get("name")
            if cid:
                c_app = state_manager.get_companion_approval(world_state, cid)
                c_app["camp_dialogue_pending"] = True
    logger.debug("perform_short_rest: weapon_actions_available and short-rest class resources recharged.")
    return {
        "success": True,
        "weapon_actions_available": True,
        "message": "You take a short rest. Your weapon actions and short-rest class abilities are recharged!",
    }


def resolve_downed_outcome(
    combatant: Dict[str, Any],
    combat_state: Optional[Dict[str, Any]] = None,
    world_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Spec Section 17a (Kenshi-lite downed outcome system).
    Selects one of ['robbed_and_left', 'captured', 'rescued_by_npc'] using context-weighted choice.
    """
    import dungeon_manager

    outcomes = ["robbed_and_left", "captured", "rescued_by_npc"]

    room = dungeon_manager.get_current_room(world_state) if world_state else None
    room_type = room.get("type", "wilderness") if room else "wilderness"
    is_safe = room.get("is_safe", False) if room else False

    if is_safe or room_type == "town":
        weights = [0.20, 0.10, 0.70]
    elif room_type == "dungeon":
        weights = [0.50, 0.35, 0.15]
    else:
        weights = [0.45, 0.25, 0.30]

    outcome = random.choices(outcomes, weights=weights, k=1)[0]
    penalty: Dict[str, Any] = {
        "gold_lost": 0,
        "items_lost": [],
        "hp_set_to": None,
        "hp_restored": None,
        "status": "normal",
        "relocated_to": None,
    }

    combatant["death_saves"] = {"success": 0, "fail": 0}

    hp_dict = combatant.setdefault("hp", {"current": 0, "max": 10})
    max_hp = hp_dict.get("max", 10)

    if outcome == "robbed_and_left":
        _ensure_currency(combatant)
        cur_total_gp = combatant["currency"]["gp"] + combatant["currency"]["sp"] // SP_PER_GP
        gold_lost = random.randint(math.ceil(cur_total_gp * 0.5), max(1, cur_total_gp)) if cur_total_gp > 0 else 0
        subtract_currency(combatant, gp=gold_lost)

        inventory = combatant.get("inventory", [])
        items_lost = []
        kept_inventory = []
        for item in inventory:
            if isinstance(item, dict) and item.get("equipped") is True:
                kept_inventory.append(item)
            else:
                item_id = item.get("item_id") if isinstance(item, dict) else item
                items_lost.append(item_id)
        combatant["inventory"] = kept_inventory

        hp_dict["current"] = 1
        combatant["status"] = "normal"

        relocated_to = dungeon_manager.find_nearest_visited_safe_room(world_state) if world_state else "town_riverside"
        if world_state:
            world_state["current_location"] = relocated_to

        penalty["gold_lost"] = gold_lost
        penalty["items_lost"] = items_lost
        penalty["hp_set_to"] = 1
        penalty["status"] = "normal"
        penalty["relocated_to"] = relocated_to

    elif outcome == "captured":
        combatant["status"] = "captive"
        if hp_dict.get("current", 0) <= 0:
            hp_dict["current"] = 1
        penalty["status"] = "captive"
        penalty["hp_set_to"] = hp_dict["current"]

    elif outcome == "rescued_by_npc":
        hp_restored = max(1, max_hp // 2)
        hp_dict["current"] = hp_restored
        combatant["status"] = "normal"

        penalty["hp_restored"] = hp_restored
        penalty["hp_now"] = hp_restored
        penalty["status"] = "normal"

    if combat_state:
        combat_state["status"] = "ended"
        combat_state["outcome"] = "player_defeat"

    logger.info(f"resolve_downed_outcome: outcome='{outcome}', penalty={penalty}")
    return {"outcome": outcome, "penalty": penalty}
