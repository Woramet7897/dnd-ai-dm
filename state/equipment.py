"""
state/equipment.py — Armor Class calculation, equipment toggling, identification, curses, consumables, and shops.
"""

import copy
import logging
import uuid
from typing import Any, Dict, Optional, Tuple

from rules import (
    _get_item_catalog,
    _get_shop_catalog,
    _inventory_item_info,
    get_modifier,
    is_proficient,
)
from state.currency import (
    _ensure_currency,
    add_currency,
    can_afford,
    format_currency,
    get_item_price_breakdown,
    get_item_sell_breakdown,
    subtract_currency,
)

logger = logging.getLogger("state_manager")


class ConditionEntry(dict):
    """
    A dict subclass for active conditions that compares equal to strings,
    supporting both 'condition in active_conditions' (str check) and
    entry['condition'] / entry['name'] (dict inspection) without breaking
    json serialization or duration decrementing in combat_manager.
    """
    def __eq__(self, other):
        if isinstance(other, str):
            return self.get("condition") == other or self.get("name") == other
        return super().__eq__(other)


def apply_condition_to_state(
    character_state: Dict[str, Any],
    condition: str,
    duration_rounds: int = 2,
    duration_hours: Optional[int] = None,
) -> bool:
    """Apply a status condition to character_state. Checks condition immunities."""
    immunities = character_state.get("immunities", [])
    if condition in immunities:
        logger.debug(f"apply_condition_to_state: character is immune to '{condition}'.")
        return False

    active_conds = character_state.setdefault("active_conditions", [])
    if isinstance(active_conds, list):
        for entry in active_conds:
            if entry == condition:
                return True
            if isinstance(entry, dict) and (entry.get("name") == condition or entry.get("condition") == condition):
                entry["duration"] = max(entry.get("duration", 0), duration_rounds)
                if duration_hours is not None:
                    entry["duration_hours"] = max(entry.get("duration_hours", 0), duration_hours)
                return True

        entry_data = {
            "condition": condition,
            "name": condition,
            "duration": duration_rounds,
        }
        if duration_hours is not None:
            entry_data["duration_hours"] = duration_hours
        active_conds.append(ConditionEntry(entry_data))
    return True


def _compute_ac(character_state: Dict[str, Any]) -> int:
    """
    Compute total Armor Class (AC) from equipped items + DEX modifier.
    Spec Section 7b:
    - Base AC comes from equipped chest item's ac_base (or 10 if unarmored).
    - DEX mod is added if unarmored or if chest armor's stat_mod == "DEX".
    - Adds sum of ac_bonus from all equipped items (shield, cloak, etc.).
    """
    catalog = _get_item_catalog()
    inventory = character_state.get("inventory", [])
    stats = character_state.get("stats", {})
    dex_mod = get_modifier(stats.get("DEX", 10))

    equipped_chest = None
    ac_bonus_total = 0

    for item in inventory:
        if isinstance(item, dict) and item.get("equipped") is True:
            info = _inventory_item_info(item, catalog)
            slot = info.get("slot")
            effects = info.get("effects", {})

            if slot == "chest":
                equipped_chest = info

            if "ac_bonus" in effects:
                ac_bonus_total += effects.get("ac_bonus", 0)
            if "ac_penalty" in effects:
                ac_bonus_total -= effects.get("ac_penalty", 0)

            is_item_cursed = item.get("cursed")
            if is_item_cursed is None:
                is_item_cursed = info.get("cursed", False)

            if is_item_cursed:
                curse_eff = item.get("curse_effect") or info.get("curse_effect", {})
                if "ac_bonus" in curse_eff and "ac_bonus" not in effects:
                    ac_bonus_total += curse_eff.get("ac_bonus", 0)
                if "ac_penalty" in curse_eff and "ac_penalty" not in effects:
                    ac_bonus_total -= curse_eff.get("ac_penalty", 0)

    if equipped_chest is not None:
        chest_effects = equipped_chest.get("effects", {})
        base_ac = chest_effects.get("ac_base", 10)
        stat_mod = chest_effects.get("stat_mod")
        if stat_mod == "DEX":
            total_ac = base_ac + dex_mod + ac_bonus_total
        else:
            total_ac = base_ac + ac_bonus_total
        if character_state.get("fighting_style") == "defence":
            total_ac += 1
    else:
        cls_name = str(character_state.get("class", character_state.get("class_name", ""))).strip().lower()
        if cls_name == "barbarian":
            con_mod = max(0, get_modifier(stats.get("CON", 10)))
            total_ac = 10 + dex_mod + con_mod + ac_bonus_total
        elif cls_name == "monk":
            wis_mod = max(0, get_modifier(stats.get("WIS", 10)))
            total_ac = 10 + dex_mod + wis_mod + ac_bonus_total
        elif cls_name == "sorcerer" and character_state.get("subclass") == "draconic_bloodline":
            total_ac = 13 + dex_mod + ac_bonus_total
        else:
            total_ac = 10 + dex_mod + ac_bonus_total

    return total_ac


def get_active_effects(character_state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Aggregate all effects from currently-equipped items in character inventory.
    Spec Section 7b: Single source of truth for equipment effects queried by
    combat_manager and validation.py.
    """
    catalog = _get_item_catalog()
    inventory = character_state.get("inventory", [])

    ac_bonus_total = 0
    saving_throw_bonus = 0
    attack_bonus = 0
    skill_bonus: Dict[str, int] = {}
    enables_spellcasting = False

    for item in inventory:
        if isinstance(item, dict) and item.get("equipped") is True:
            info = _inventory_item_info(item, catalog)
            effects = dict(info.get("effects", {}))
            if "effects" in item and isinstance(item["effects"], dict):
                effects.update(item["effects"])

            if "ac_bonus" in effects:
                ac_bonus_total += effects["ac_bonus"]
            if "ac_penalty" in effects:
                ac_bonus_total -= effects["ac_penalty"]
            if "saving_throw_bonus" in effects:
                saving_throw_bonus += effects["saving_throw_bonus"]
            if "saving_throw_penalty" in effects:
                saving_throw_bonus -= effects["saving_throw_penalty"]
            if "attack_bonus" in effects:
                attack_bonus += effects["attack_bonus"]
            if "enables_spellcasting" in effects and effects["enables_spellcasting"]:
                enables_spellcasting = True
            if "skill_bonus" in effects and isinstance(effects["skill_bonus"], dict):
                for sk, bon in effects["skill_bonus"].items():
                    skill_bonus[sk] = skill_bonus.get(sk, 0) + bon

            is_item_cursed = item.get("cursed")
            if is_item_cursed is None:
                is_item_cursed = info.get("cursed", False)

            if is_item_cursed:
                curse_eff = item.get("curse_effect") or info.get("curse_effect", {})
                if "ac_bonus" in curse_eff and "ac_bonus" not in effects:
                    ac_bonus_total += curse_eff["ac_bonus"]
                if "ac_penalty" in curse_eff and "ac_penalty" not in effects:
                    ac_bonus_total -= curse_eff["ac_penalty"]
                if "saving_throw_bonus" in curse_eff and "saving_throw_bonus" not in effects:
                    saving_throw_bonus += curse_eff["saving_throw_bonus"]
                if "saving_throw_penalty" in curse_eff and "saving_throw_penalty" not in effects:
                    saving_throw_bonus -= curse_eff["saving_throw_penalty"]

    return {
        "ac_bonus_total": ac_bonus_total,
        "saving_throw_bonus": saving_throw_bonus,
        "attack_bonus": attack_bonus,
        "skill_bonus": skill_bonus,
        "enables_spellcasting": enables_spellcasting,
    }


def equip_item(
    item_id: str,
    character_state: Dict[str, Any],
    world_state: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Equip an item from character inventory (Spec Section 7b & Module B §2)."""
    inventory = character_state.get("inventory", [])
    target_item = None
    for item in inventory:
        if isinstance(item, dict) and item.get("item_id") == item_id:
            target_item = item
            break

    if target_item is None:
        return False, f"Item '{item_id}' not found in inventory."

    catalog = _get_item_catalog()
    item_info = _inventory_item_info(target_item, catalog)
    if not item_info and world_state:
        item_info = world_state.get("generated_items", {}).get(item_id)
    if not item_info:
        item_info = target_item

    item_type = item_info.get("type")
    if item_type not in ("wearable", "weapon"):
        return False, f"Item '{item_id}' of type '{item_type}' cannot be equipped."

    target_slot = item_info.get("slot")
    if target_slot:
        for other_item in inventory:
            if isinstance(other_item, dict) and other_item is not target_item and other_item.get("equipped") is True:
                other_id = other_item.get("item_id")
                other_info = _inventory_item_info(other_item, catalog)
                if other_info.get("slot") == target_slot:
                    if other_item.get("cannot_unequip") is True:
                        return False, f"Cannot equip '{item_id}': slot '{target_slot}' is occupied by cursed item '{other_id}' which cannot be unequipped."
                    other_item["equipped"] = False

    is_cursed = bool(target_item.get("cursed", False) or item_info.get("cursed", False))
    is_identified = target_item.get("identified")
    if is_identified is None:
        is_identified = item_info.get("identified", True)

    if is_cursed and not is_identified:
        target_item["cannot_unequip"] = True
        logger.warning(f"equip_item: equipped unidentified cursed item '{item_id}'. Locked cannot_unequip = True!")

    target_item["equipped"] = True
    character_state["ac"] = _compute_ac(character_state)
    logger.debug(f"equip_item: equipped '{item_id}' in slot '{target_slot}'. New AC: {character_state['ac']}.")
    return True, ""


def unequip_item(item_id: str, character_state: Dict[str, Any]) -> Tuple[bool, str]:
    """Unequip an item in character inventory."""
    inventory = character_state.get("inventory", [])
    target_item = None
    for item in inventory:
        if isinstance(item, dict) and item.get("item_id") == item_id:
            target_item = item
            break

    if target_item is None:
        return False, f"Item '{item_id}' not found in inventory."

    if target_item.get("cannot_unequip") is True:
        return False, f"Cannot unequip '{item_id}': item is cursed and bound to the wearer."

    target_item["equipped"] = False
    character_state["ac"] = _compute_ac(character_state)
    logger.debug(f"unequip_item: unequipped '{item_id}'. New AC: {character_state['ac']}.")
    return True, ""


def identify_item(
    player_state: Dict[str, Any],
    item_id: str,
    roll_total: int,
    world_state: Optional[Dict[str, Any]] = None,
) -> bool:
    """Attempt to identify an item in inventory (Module B §2 / Phase 12.2)."""
    inventory = player_state.get("inventory", [])
    target_item = None
    for item in inventory:
        if isinstance(item, dict) and item.get("item_id") == item_id:
            target_item = item
            break

    catalog = _get_item_catalog()
    item_info = catalog.get(item_id, {})
    if not item_info and world_state:
        item_info = world_state.get("generated_items", {}).get(item_id, {})

    if target_item is None and not item_info:
        logger.debug(f"identify_item: item '{item_id}' not found in inventory or catalog.")
        return False

    dc = 13
    if target_item and "identify_dc" in target_item:
        dc = target_item["identify_dc"]
    elif item_info and "identify_dc" in item_info:
        dc = item_info["identify_dc"]

    if roll_total >= dc:
        if target_item is not None:
            target_item["identified"] = True
            if "name" in item_info:
                target_item["name"] = item_info["name"]
        if item_info:
            item_info["identified"] = True
        logger.info(f"identify_item: successfully identified '{item_id}' (roll {roll_total} >= DC {dc}).")
        return True
    else:
        logger.info(f"identify_item: failed to identify '{item_id}' (roll {roll_total} < DC {dc}).")
        return False


def remove_curse(
    character_state: Dict[str, Any],
    item_id: Optional[str] = None,
) -> Tuple[bool, str]:
    """Remove curse from one or all equipped/carried items."""
    inventory = character_state.get("inventory", [])
    found = False
    for item in inventory:
        if isinstance(item, dict):
            if item_id is None or item.get("item_id") == item_id:
                found = True
                if item.get("cannot_unequip"):
                    item["cannot_unequip"] = False
                item["cursed"] = False

    if item_id and not found:
        return False, f"No item '{item_id}' found in inventory."

    character_state["ac"] = _compute_ac(character_state)
    return True, "Curse removed successfully."


def pay_cleric_remove_curse(
    character_state: Dict[str, Any],
    item_id: Optional[str] = None,
    world_state: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Pay 50 gold at a town cleric to remove curse."""
    _ensure_currency(character_state)
    if not can_afford(character_state, gp=50):
        return False, f"Insufficient gold. Town cleric requires 50 GP to remove a curse (have {format_currency(character_state)})."

    subtract_currency(character_state, gp=50)
    ok, msg = remove_curse(character_state, item_id)
    if ok:
        logger.info(f"pay_cleric_remove_curse: paid 50 GP to cleric. {msg}")
        return True, "Paid 50 GP to the town cleric. Curse has been lifted!"
    add_currency(character_state, gp=50)
    return False, msg


def use_consumable(item_id: str, character_state: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """Use a consumable item (e.g. healing potion) from inventory."""
    inventory = character_state.get("inventory", [])
    target_item = None
    for item in inventory:
        if isinstance(item, dict) and item.get("item_id") == item_id:
            target_item = item
            break

    if target_item is None:
        return False, f"Item '{item_id}' not found in inventory.", {}

    catalog = _get_item_catalog()
    item_info = _inventory_item_info(target_item, catalog)
    if not item_info:
        return False, f"Item '{item_id}' not found in item catalog.", {}

    if item_info.get("type") not in ("consumable", "food"):
        return False, f"Item '{item_id}' is not a consumable or food item.", {}

    qty = target_item.get("quantity", 1)
    if qty <= 0:
        return False, f"Item '{item_id}' quantity is 0.", {}

    effects = item_info.get("effects", {})
    result_dict: Dict[str, Any] = {}

    if "heal" in effects:
        from combat_manager import roll_dice
        heal_expr = effects["heal"]
        heal_amount = roll_dice(heal_expr)

        hp = character_state.setdefault("hp", {"current": 10, "max": 10})
        old_hp = hp.get("current", 0)
        max_hp = hp.get("max", 10)
        new_hp = min(max_hp, old_hp + heal_amount)
        actual_healed = new_hp - old_hp
        hp["current"] = new_hp

        result_dict["healed"] = actual_healed
        result_dict["hp_now"] = new_hp

    if "temp_hp" in effects:
        hp = character_state.setdefault("hp", {"current": 10, "max": 10})
        hp["temp"] = max(hp.get("temp", 0), effects["temp_hp"])
        character_state["temp_hp"] = hp["temp"]
        result_dict["temp_hp"] = hp["temp"]

    if "clear_exhaustion" in effects:
        active_conditions = character_state.setdefault("active_conditions", [])
        if "exhausted" in active_conditions:
            active_conditions.remove("exhausted")
        for c in list(active_conditions):
            if isinstance(c, dict) and (c.get("condition") == "exhausted" or c.get("name") == "exhausted"):
                active_conditions.remove(c)
        if character_state.get("exhaustion_level", 0) > 0:
            character_state["exhaustion_level"] -= 1
        result_dict["exhaustion_cleared"] = True

    target_item["quantity"] = qty - 1
    if target_item["quantity"] <= 0:
        inventory.remove(target_item)

    logger.debug(f"use_consumable: used '{item_id}'. Result: {result_dict}.")
    return True, "", result_dict


SHOP_SECRET_STOCK: Dict[str, Dict[str, Any]] = {
    "blacksmith": {
        "item_id": "silvered_rapier_of_eloquence",
        "name": "Silvered Rapier of Eloquence (+1)",
        "type": "weapon",
        "slot": "weapon",
        "rarity": "rare",
        "value_gold": 80,
        "damage": "1d8+1",
        "damage_type": "piercing",
        "finesse": True,
        "description": "ดาบเรเปียร์เคลือบเงินสลักอักขระนักการทูต (+1 Attack/Damage และ +2 Persuasion)",
        "effects": {"attack_bonus": 1, "skill_bonus": {"Persuasion": 2}},
    },
    "general_store": {
        "item_id": "cloak_of_silver_tongue",
        "name": "Cloak of the Silver Tongue",
        "type": "wearable",
        "slot": "cloak",
        "rarity": "uncommon",
        "value_gold": 60,
        "description": "ผ้าคลุมไหมทอเวทมนตร์แห่งวาทศิลป์ (+1 AC, +2 Persuasion และ +2 Deception)",
        "effects": {"ac_bonus": 1, "skill_bonus": {"Persuasion": 2, "Deception": 2}},
    },
    "apothecary": {
        "item_id": "elixir_of_mind_reading",
        "name": "Elixir of Silver Presence",
        "type": "consumable",
        "rarity": "uncommon",
        "value_gold": 40,
        "description": "ยาอมฤตเพิ่มเสน่ห์และฟื้นฟูพลังชีวิต (ฟื้นฟู 2d8+4 HP และมอบ +10 Temp HP)",
        "effects": {"heal": "2d8+4", "temp_hp": 10},
    },
    "default": {
        "item_id": "ring_of_diplomatic_grace",
        "name": "Ring of Diplomatic Grace",
        "type": "wearable",
        "slot": "ring",
        "rarity": "rare",
        "value_gold": 75,
        "description": "แหวนตราฑูตโบราณ (+1 AC, +1 Saving Throws และ +2 Persuasion)",
        "effects": {"ac_bonus": 1, "saving_throw_bonus": 1, "skill_bonus": {"Persuasion": 2}},
    },
}


def attempt_shop_haggle(
    shop_id: str,
    character_state: Dict[str, Any],
    world_state: Dict[str, Any],
    skill: str = "Persuasion",
    roll_override: Optional[int] = None,
) -> Dict[str, Any]:
    """
    BG3 / D&D 5e Social Bartering & Haggling:
    Attempt to haggle with a merchant using Persuasion, Deception, or Intimidation.
    On success:
      - Unlocks a 30% discount (or 50% on Critical / beating DC by 5+)
      - Unlocks the merchant's Secret Under-the-Counter Stock item!
    On failure:
      - Merchant raises prices by 15% for the day
      - Failed Intimidation alerts the town guards (+15 GP bounty)
    """
    import state_manager
    shops = _get_shop_catalog()
    shop = shops.get(shop_id, {"name": shop_id.replace("_", " ").title(), "sell_multiplier": 1.0})
    s_name = shop.get("name", shop_id.replace("_", " ").title())
    cur_day = int(world_state.get("game_time", {}).get("day", 1))

    haggle_map = world_state.setdefault("shop_haggle_state", {})
    existing = haggle_map.get(shop_id, {})
    if existing.get("haggled_day") == cur_day and roll_override is None:
        return {
            "success": False,
            "already_haggled": True,
            "discount_multiplier": existing.get("discount_multiplier", 1.0),
            "secret_unlocked": existing.get("secret_unlocked", False),
            "secret_item": existing.get("secret_item"),
            "message": f"คุณได้เจรจาต่อรองกับร้าน **{s_name}** ไปแล้วในวันนี้ (กลับมาเจรจาใหม่ในวันพรุ่งนี้)",
        }

    skill_norm = (skill or "Persuasion").strip()
    if roll_override is not None:
        dc = 13
        cha_mod = get_modifier(character_state.get("stats", {}).get("CHA", 10))
        prof_b = character_state.get("proficiency_bonus", 2) if is_proficient(skill_norm, character_state) else 0
        total = int(roll_override) + cha_mod + prof_b
        is_crit = (int(roll_override) == 20)
        is_fumble = (int(roll_override) == 1)
        is_succ = True if is_crit else (False if is_fumble else (total >= dc))
        chk_res = {
            "roll": int(roll_override),
            "total": total,
            "dc": dc,
            "success": is_succ,
            "critical": is_crit,
            "fumble": is_fumble,
            "skill": skill_norm,
        }
    else:
        chk_res = state_manager.resolve_check(
            stat="CHA",
            difficulty="medium",
            state=character_state,
            proficient=is_proficient(skill_norm, character_state),
            skill=skill_norm,
        )

    if chk_res["success"]:
        is_big_win = chk_res.get("critical") or (chk_res["total"] >= chk_res["dc"] + 5)
        disc_mult = 0.50 if is_big_win else 0.70
        disc_pct = 50 if is_big_win else 30

        sec_template = copy.deepcopy(SHOP_SECRET_STOCK.get(shop_id, SHOP_SECRET_STOCK["default"]))
        sec_id = sec_template["item_id"]
        world_state.setdefault("generated_items", {})[sec_id] = sec_template

        haggle_map[shop_id] = {
            "haggled_day": cur_day,
            "discount_multiplier": disc_mult,
            "discount_percent": disc_pct,
            "secret_unlocked": True,
            "secret_item_id": sec_id,
            "secret_item": sec_template,
        }
        return {
            "success": True,
            "check_result": chk_res,
            "discount_multiplier": disc_mult,
            "discount_percent": disc_pct,
            "secret_unlocked": True,
            "secret_item_id": sec_id,
            "secret_item": sec_template,
            "message": (
                f"🗣️ **ต่อรองราคาสำเร็จ!** คุณใช้ **{skill_norm}** (ทอยได้ **{chk_res['total']}** vs **DC {chk_res['dc']}**) "
                f"จนพ่อค้าร้าน **{s_name}** ยอมลดราคาสินค้าให้ **{disc_pct}%** และนำสินค้าลับใต้เคาน์เตอร์ **{sec_template['name']}** ออกมาวางขาย!"
            ),
        }
    else:
        disc_mult = 1.15
        bounty_added = 0
        if skill_norm.lower() == "intimidation":
            bounty_added = 15
            character_state["is_wanted"] = True
            character_state["bounty"] = int(character_state.get("bounty", 0)) + bounty_added

        haggle_map[shop_id] = {
            "haggled_day": cur_day,
            "discount_multiplier": disc_mult,
            "discount_percent": -15,
            "secret_unlocked": False,
            "secret_item_id": None,
            "secret_item": None,
        }
        bounty_msg = f" และพ่อค้าแจ้งความข้อหาข่มขู่ (ค่าหัว +{bounty_added} GP)!" if bounty_added else "!"
        return {
            "success": False,
            "check_result": chk_res,
            "discount_multiplier": disc_mult,
            "discount_percent": -15,
            "secret_unlocked": False,
            "bounty_added": bounty_added,
            "message": (
                f"❌ **ต่อรองราคาไม่สำเร็จ!** (ทอย {skill_norm} ได้ **{chk_res['total']}** vs **DC {chk_res['dc']}**) "
                f"พ่อค้าร้าน **{s_name}** ไม่พอใจและปรับราคาสินค้าขึ้น **+15%** สำหรับวันนี้{bounty_msg}"
            ),
        }


def buy_item(
    item_id: str,
    shop_id: str,
    character_state: Dict[str, Any],
    world_state: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Buy an item from a shop (spec Section 19b & Section 22f)."""
    shops = _get_shop_catalog()
    shop = shops.get(shop_id)
    if not shop:
        return False, f"Shop '{shop_id}' not found."

    if world_state and "game_time" in world_state:
        period = world_state["game_time"].get("period")
        open_periods = shop.get("open_periods")
        if open_periods and period not in open_periods:
            return False, f"The shop '{shop.get('name', shop_id)}' is closed for the {period}."

    haggle_info = (world_state or {}).get("shop_haggle_state", {}).get(shop_id, {})
    sell_items = list(shop.get("sell_items", []))
    if haggle_info.get("secret_unlocked") and haggle_info.get("secret_item_id"):
        if haggle_info["secret_item_id"] not in sell_items:
            sell_items.append(haggle_info["secret_item_id"])

    if item_id not in sell_items:
        return False, f"Shop '{shop.get('name', shop_id)}' does not sell '{item_id}'."

    catalog = _get_item_catalog()
    item_info = catalog.get(item_id)
    if not item_info and world_state:
        item_info = world_state.get("generated_items", {}).get(item_id)
    if not item_info and haggle_info.get("secret_item_id") == item_id:
        item_info = haggle_info.get("secret_item")
    if not item_info:
        return False, f"Item '{item_id}' not found in catalog."

    sell_mult = shop.get("sell_multiplier", 1.0) * float(haggle_info.get("discount_multiplier", 1.0))
    cost_info = get_item_price_breakdown(item_info, sell_mult)

    _ensure_currency(character_state)
    if not can_afford(character_state, gp=cost_info["gp"], sp=cost_info["sp"], cp=cost_info["cp"]):
        return False, f"Not enough funds. Costs {cost_info['text']}, you have {format_currency(character_state)}."
    subtract_currency(character_state, gp=cost_info["gp"], sp=cost_info["sp"], cp=cost_info["cp"])

    inventory = character_state.setdefault("inventory", [])
    existing = next((i for i in inventory if isinstance(i, dict) and i.get("item_id") == item_id), None)
    if existing and existing.get("quantity") is not None:
        existing["quantity"] = existing.get("quantity", 1) + 1
    elif not existing:
        new_inv_entry: Dict[str, Any] = {"item_id": item_id, "equipped": False, "quantity": 1}
        if item_id not in catalog and isinstance(item_info, dict):
            new_inv_entry["name"] = item_info.get("name", item_id)
            new_inv_entry["type"] = item_info.get("type", "wearable")
            if "slot" in item_info:
                new_inv_entry["slot"] = item_info["slot"]
            if "effects" in item_info:
                new_inv_entry["effects"] = copy.deepcopy(item_info["effects"])
        inventory.append(new_inv_entry)

    logger.debug(f"buy_item: bought '{item_id}' from '{shop_id}' for {cost_info['text']}. Remaining: {format_currency(character_state)}.")
    return True, f"Bought {item_info.get('name', item_id)} for {cost_info['text']}."


def sell_item(
    item_id: str,
    shop_id: str,
    character_state: Dict[str, Any],
    world_state: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Sell an item to a shop (spec Section 19b & Section 22f)."""
    shops = _get_shop_catalog()
    shop = shops.get(shop_id)
    if not shop:
        return False, f"Shop '{shop_id}' not found."

    if world_state and "game_time" in world_state:
        period = world_state["game_time"].get("period")
        open_periods = shop.get("open_periods")
        if open_periods and period not in open_periods:
            return False, f"The shop '{shop.get('name', shop_id)}' is closed for the {period}."

    inventory = character_state.get("inventory", [])
    target_item = None
    for item in inventory:
        if isinstance(item, dict) and item.get("item_id") == item_id:
            target_item = item
            break

    if target_item is None:
        return False, f"You don't have '{item_id}' to sell."

    catalog = _get_item_catalog()
    item_info = _inventory_item_info(target_item, catalog)
    buy_mult = shop.get("buy_multiplier", 0.5)
    gain_info = get_item_sell_breakdown(item_info, buy_mult)

    qty = target_item.get("quantity", 1)
    if qty > 1:
        target_item["quantity"] = qty - 1
    else:
        inventory.remove(target_item)

    add_currency(character_state, gp=gain_info["gp"], sp=gain_info["sp"], cp=gain_info["cp"])
    character_state["ac"] = _compute_ac(character_state)

    logger.debug(f"sell_item: sold '{item_id}' to '{shop_id}' for {gain_info['text']}. Funds now: {format_currency(character_state)}.")
    return True, f"Sold {item_info.get('name', item_id)} for {gain_info['text']}."


def generate_item(
    rarity: str,
    world_state: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """Generate a new item deterministically from a rarity tag ('common', 'uncommon', 'rare')."""
    rarity = rarity.lower().strip() if isinstance(rarity, str) else "common"
    if rarity not in ("common", "uncommon", "rare"):
        rarity = "common"

    rarity_tables = {
        "common":   {"gold": 15, "ac_bonus": 1, "name_suffix": "of Quality"},
        "uncommon": {"gold": 50, "ac_bonus": 1, "saving_throw_bonus": 1, "name_suffix": "of Might"},
        "rare":     {"gold": 200, "ac_bonus": 2, "saving_throw_bonus": 1, "name_suffix": "of Distinction"},
    }
    spec_row = rarity_tables[rarity]

    gen_id = f"gen_{uuid.uuid4().hex[:8]}"
    item_dict = {
        "item_id": gen_id,
        "name": f"Item {spec_row['name_suffix']}",
        "type": "wearable",
        "slot": "ring",
        "rarity": rarity,
        "value_gold": spec_row["gold"],
        "effects": {
            "ac_bonus": spec_row["ac_bonus"],
        }
    }

    if "saving_throw_bonus" in spec_row:
        item_dict["effects"]["saving_throw_bonus"] = spec_row["saving_throw_bonus"]

    gen_items = world_state.setdefault("generated_items", {})
    gen_items[gen_id] = item_dict

    logger.info(f"generate_item: generated item '{gen_id}' ({rarity}) in world_state.")
    return gen_id, item_dict


def register_generated_item(
    item_data: Dict[str, Any],
    world_state: Dict[str, Any],
    item_id: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Register a dynamic item definition into world_state["generated_items"]."""
    if not item_id or not isinstance(item_id, str):
        item_id = f"gen_item_{uuid.uuid4().hex[:8]}"
    item_dict = copy.deepcopy(item_data)
    item_dict["item_id"] = item_id
    gen_items = world_state.setdefault("generated_items", {})
    gen_items[item_id] = item_dict
    logger.info(f"register_generated_item: registered dynamic item '{item_id}' ({item_dict.get('name')}) in world_state.")
    return item_id, item_dict
