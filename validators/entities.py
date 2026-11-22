"""
validators/entities.py — Text sanitization, prompt-injection stripping, dice damage average math,
and validation of dynamically generated Monsters, NPCs, Items, Events, and Spells.
"""

import logging
import math
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("validation")

PROMPT_INJECTION_PATTERNS: List[re.Pattern] = [
    re.compile(r"\[/?system[^\]]*\]", re.IGNORECASE),
    re.compile(r"ignore\s+(all\s+|previous\s+|prior\s+)?instructions?", re.IGNORECASE),
    re.compile(r"system\s*prompt:?", re.IGNORECASE),
    re.compile(r"new\s+instructions?:?", re.IGNORECASE),
]

DICE_FORMULA_REGEX: re.Pattern = re.compile(r"^(\d+)d(\d+)(?:([+-])(\d+))?$")

MONSTER_BOUNDS_BY_LEVEL: Dict[int, Dict[str, Any]] = {
    1: {"max_hp": 30,  "max_ac": 15, "max_attack_bonus": 5, "max_avg_damage": 10.0, "max_cr": 1.0, "max_xp": 200},
    2: {"max_hp": 45,  "max_ac": 16, "max_attack_bonus": 6, "max_avg_damage": 15.0, "max_cr": 2.0, "max_xp": 450},
    3: {"max_hp": 65,  "max_ac": 16, "max_attack_bonus": 7, "max_avg_damage": 22.0, "max_cr": 3.0, "max_xp": 700},
    4: {"max_hp": 85,  "max_ac": 17, "max_attack_bonus": 7, "max_avg_damage": 28.0, "max_cr": 4.0, "max_xp": 1100},
    5: {"max_hp": 110, "max_ac": 18, "max_attack_bonus": 8, "max_avg_damage": 35.0, "max_cr": 5.0, "max_xp": 1800},
}

VALID_DAMAGE_TYPES: set = {
    "slashing", "piercing", "bludgeoning", "fire", "cold", "lightning",
    "poison", "acid", "necrotic", "radiant", "force", "psychic",
}

VALID_MONSTER_CONDITIONS: set = {
    "poisoned", "prone", "blinded", "stunned", "frightened", "charmed",
    "burning", "dazed", "slowed", "restrained", "sleeping", "held",
}

NPC_BOUNDS_BY_LEVEL: Dict[int, Dict[str, Any]] = {
    1: {"max_hp": 30,  "max_ac": 16, "max_attack_bonus": 5, "max_avg_damage": 10.0},
    2: {"max_hp": 40,  "max_ac": 16, "max_attack_bonus": 6, "max_avg_damage": 14.0},
    3: {"max_hp": 55,  "max_ac": 17, "max_attack_bonus": 7, "max_avg_damage": 18.0},
    4: {"max_hp": 70,  "max_ac": 17, "max_attack_bonus": 7, "max_avg_damage": 22.0},
    5: {"max_hp": 85,  "max_ac": 18, "max_attack_bonus": 8, "max_avg_damage": 26.0},
}

VALID_ITEM_TYPES: set = {
    "weapon", "wearable", "consumable", "tool", "food", "instrument", "ration", "raw_food",
}

VALID_WEAPON_SLOTS: set = {"main_hand", "off_hand"}
VALID_WEARABLE_SLOTS: set = {"chest", "cloak", "ring", "off_hand", "head", "feet"}

SPELL_BOUNDS_BY_LEVEL: Dict[int, Dict[str, float]] = {
    0: {"max_avg_damage": 10.0, "max_avg_heal": 0.0},
    1: {"max_avg_damage": 16.0, "max_avg_heal": 12.0},
    2: {"max_avg_damage": 24.0, "max_avg_heal": 20.0},
    3: {"max_avg_damage": 35.0, "max_avg_heal": 30.0},
}

VALID_SPELL_TYPES: set = {"attack_roll", "attack_save", "heal", "buff", "utility"}
VALID_SAVE_STATS: set = {"STR", "DEX", "CON", "INT", "WIS", "CHA"}


def _safe_int(value: Any, default: int = 0) -> int:
    """Safely convert value to int, handling inf, nan, strings, None without throwing."""
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return default
        try:
            return int(value)
        except (ValueError, OverflowError):
            return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            f = float(value.strip())
            if math.isnan(f) or math.isinf(f):
                return default
            return int(f)
        except (ValueError, OverflowError):
            return default
    return default


def sanitize_text(text: Any, max_len: int = 40, allow_newlines: bool = False) -> str:
    """Sanitize LLM-generated string against HTML, markdown links, control characters, and prompt injection."""
    if not isinstance(text, str):
        return ""

    s = text
    for _ in range(10):
        prev = s
        s = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", s)
        s = re.sub(r"<[^>]+>", "", s)
        s = re.sub(r"https?://\S+", "", s)
        s = re.sub(r"\[([^\]]+)\]\([^\)]*\)", r"\1", s)
        s = re.sub(r"[*_#`~>\\]", "", s)
        for pat in PROMPT_INJECTION_PATTERNS:
            s = pat.sub("", s)
        s = re.sub(r"[\[\]]", "", s)
        if s == prev:
            break

    cleaned_chars = []
    for ch in s:
        if ch in ('\n', '\r'):
            if allow_newlines:
                cleaned_chars.append('\n')
            else:
                cleaned_chars.append(' ')
        elif ch == '\t':
            cleaned_chars.append(' ')
        elif ch.isprintable():
            cleaned_chars.append(ch)
    s = "".join(cleaned_chars)

    s = re.sub(r"[ \t]+", " ", s).strip()
    return s[:max_len].strip()


def calculate_average_dice_damage(formula: str) -> float:
    """Parse a dice formula like '1d6+2', '2d8', '1d10-1' and compute average damage."""
    if not isinstance(formula, str):
        return 0.0
    m = DICE_FORMULA_REGEX.match(formula.strip().lower())
    if not m:
        return 0.0
    num_dice = int(m.group(1))
    die_size = int(m.group(2))
    sign = m.group(3)
    mod = int(m.group(4)) if m.group(4) else 0

    if num_dice <= 0 or die_size <= 0:
        return 0.0

    avg_per_die = (die_size + 1) / 2.0
    total = num_dice * avg_per_die
    if sign == "+":
        total += mod
    elif sign == "-":
        total -= mod
    return max(0.0, total)


def validate_generated_monster(m_data: Any, player_level: int = 1) -> Optional[Dict[str, Any]]:
    """Validate a dynamic monster definition against level-scaled bounds and sanitize text."""
    if not isinstance(m_data, dict):
        logger.debug(f"validate_generated_monster: input is not a dict ({type(m_data).__name__}) — dropped.")
        return None

    name = sanitize_text(m_data.get("name"), max_len=40)
    if not name:
        logger.debug("validate_generated_monster: missing or empty monster name — dropped.")
        return None

    lvl = max(1, min(5, _safe_int(player_level, 1)))
    bounds = MONSTER_BOUNDS_BY_LEVEL[lvl]

    raw_hp = m_data.get("hp")
    if isinstance(raw_hp, dict):
        hp_val = raw_hp.get("max") or raw_hp.get("current")
    elif isinstance(raw_hp, (int, float)) and not isinstance(raw_hp, bool):
        hp_val = _safe_int(raw_hp, -1)
    else:
        logger.debug(f"validate_generated_monster: invalid hp spec ({raw_hp!r}) — dropped.")
        return None

    if not isinstance(hp_val, int) or hp_val < 1 or hp_val > bounds["max_hp"]:
        logger.debug(f"validate_generated_monster: hp={hp_val} out of bounds (1..{bounds['max_hp']}) — dropped.")
        return None

    ac = m_data.get("ac")
    if not isinstance(ac, int) or ac < 5 or ac > bounds["max_ac"]:
        logger.debug(f"validate_generated_monster: ac={ac} out of bounds (5..{bounds['max_ac']}) — dropped.")
        return None

    raw_stats = m_data.get("stats")
    if raw_stats is not None and not isinstance(raw_stats, dict):
        logger.debug("validate_generated_monster: stats is not a dict — dropped.")
        return None
    clean_stats: Dict[str, int] = {}
    raw_stats_dict = raw_stats if isinstance(raw_stats, dict) else {}
    for st in ("STR", "DEX", "CON", "INT", "WIS", "CHA"):
        v = raw_stats_dict.get(st, 10)
        if not isinstance(v, int) or v < 1 or v > 24:
            logger.debug(f"validate_generated_monster: stat {st}={v} invalid (1..24) — dropped.")
            return None
        clean_stats[st] = v

    raw_attacks = m_data.get("attacks")
    if raw_attacks is None:
        raw_attacks = [{"name": f"{name} Strike", "attack_bonus": 3, "damage": "1d6+1", "damage_type": "bludgeoning"}]
    if not isinstance(raw_attacks, list) or not raw_attacks:
        logger.debug("validate_generated_monster: missing or empty attacks list — dropped.")
        return None
    if len(raw_attacks) > 3:
        raw_attacks = raw_attacks[:3]

    clean_attacks: List[Dict[str, Any]] = []
    for atk in raw_attacks:
        if not isinstance(atk, dict):
            continue
        atk_name = sanitize_text(atk.get("name", "Attack"), max_len=30) or "Attack"
        bonus = atk.get("attack_bonus", 0)
        if not isinstance(bonus, int) or bonus < -2 or bonus > bounds["max_attack_bonus"]:
            logger.debug(f"validate_generated_monster: attack_bonus={bonus} out of bounds — dropped.")
            return None

        dmg_str = str(atk.get("damage", "")).strip().lower()
        avg_dmg = calculate_average_dice_damage(dmg_str)
        if avg_dmg <= 0.0 or avg_dmg > bounds["max_avg_damage"]:
            logger.debug(f"validate_generated_monster: damage='{dmg_str}' (avg={avg_dmg}) out of bounds (max={bounds['max_avg_damage']}) — dropped.")
            return None

        dtype = str(atk.get("damage_type", "bludgeoning")).strip().lower()
        if dtype not in VALID_DAMAGE_TYPES:
            dtype = "bludgeoning"

        cond = atk.get("applies_condition")
        if cond and str(cond).strip().lower() in VALID_MONSTER_CONDITIONS:
            clean_cond = str(cond).strip().lower()
        else:
            clean_cond = None

        atk_entry: Dict[str, Any] = {
            "name": atk_name,
            "attack_bonus": bonus,
            "damage": dmg_str,
            "damage_type": dtype,
            "applies_condition": clean_cond,
        }
        if atk.get("ranged"):
            atk_entry["ranged"] = True
        clean_attacks.append(atk_entry)

    if not clean_attacks:
        logger.debug("validate_generated_monster: no valid attacks survived — dropped.")
        return None

    raw_xp = m_data.get("xp_value", 50)
    xp_val = _safe_int(raw_xp, 50)
    if xp_val < 0 or xp_val > bounds["max_xp"]:
        xp_val = min(bounds["max_xp"], max(0, xp_val))

    raw_gold = m_data.get("gold_drop", {})
    if isinstance(raw_gold, dict):
        g_min = max(0, min(50, _safe_int(raw_gold.get("min", 0), 0)))
        g_max = max(g_min, min(50, _safe_int(raw_gold.get("max", g_min), g_min)))
    else:
        g_min, g_max = 0, 5
    clean_gold = {"min": g_min, "max": g_max}

    raw_cr = m_data.get("challenge_rating", 0.25)
    try:
        cr_val = float(raw_cr)
        if cr_val < 0.0 or cr_val > bounds["max_cr"]:
            cr_val = bounds["max_cr"]
    except (ValueError, TypeError):
        cr_val = 0.25

    monster_dict: Dict[str, Any] = {
        "name": name,
        "hp": {"current": hp_val, "max": hp_val},
        "ac": ac,
        "stats": clean_stats,
        "attacks": clean_attacks,
        "xp_value": xp_val,
        "gold_drop": clean_gold,
        "challenge_rating": cr_val,
        "active_conditions": [],
    }

    if isinstance(m_data.get("immunities"), list):
        monster_dict["immunities"] = [
            str(im).strip().lower() for im in m_data["immunities"]
            if isinstance(im, str) and str(im).strip().lower() in (VALID_MONSTER_CONDITIONS | {"poison", "exhaustion"})
        ]
    if isinstance(m_data.get("resistances"), list):
        monster_dict["resistances"] = [
            str(res).strip().lower() for res in m_data["resistances"]
            if isinstance(res, str) and str(res).strip().lower() in VALID_DAMAGE_TYPES
        ]

    return monster_dict


def validate_generated_npc(npc_data: Any, player_level: int = 1) -> Optional[Dict[str, Any]]:
    """Validate a dynamic NPC/companion definition against safety bounds and sanitize text."""
    if not isinstance(npc_data, dict):
        logger.debug(f"validate_generated_npc: input is not a dict ({type(npc_data).__name__}) — dropped.")
        return None

    name = sanitize_text(npc_data.get("name"), max_len=40)
    if not name:
        logger.debug("validate_generated_npc: missing or empty NPC name — dropped.")
        return None

    role = sanitize_text(npc_data.get("role", npc_data.get("title", "Companion")), max_len=40) or "Companion"
    raw_persona = npc_data.get("persona_seed", npc_data.get("personality", ""))
    persona_seed = sanitize_text(raw_persona, max_len=150) if raw_persona else f"A companion named {name}."

    lvl = max(1, min(5, _safe_int(player_level, 1)))
    bounds = NPC_BOUNDS_BY_LEVEL[lvl]

    raw_hp = npc_data.get("hp", 15)
    if isinstance(raw_hp, dict):
        hp_val = raw_hp.get("max") or raw_hp.get("current", 15)
    elif isinstance(raw_hp, (int, float)) and not isinstance(raw_hp, bool):
        hp_val = _safe_int(raw_hp, -1)
    else:
        logger.debug(f"validate_generated_npc: invalid hp spec ({raw_hp!r}) — dropped.")
        return None

    if not isinstance(hp_val, int) or hp_val < 1 or hp_val > bounds["max_hp"]:
        logger.debug(f"validate_generated_npc: hp={hp_val} out of bounds (1..{bounds['max_hp']}) — dropped.")
        return None

    raw_ac = npc_data.get("ac", 12)
    if not isinstance(raw_ac, (int, float)):
        logger.debug(f"validate_generated_npc: invalid ac ({raw_ac!r}) — dropped.")
        return None
    ac = int(raw_ac)
    if ac < 8 or ac > bounds["max_ac"]:
        logger.debug(f"validate_generated_npc: ac={ac} out of bounds (8..{bounds['max_ac']}) — dropped.")
        return None

    raw_stats = npc_data.get("stats")
    clean_stats: Dict[str, int] = {}
    default_stats = {"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10}
    if isinstance(raw_stats, dict):
        for st in ("STR", "DEX", "CON", "INT", "WIS", "CHA"):
            v = raw_stats.get(st, 10)
            if not isinstance(v, (int, float)) or int(v) < 3 or int(v) > 20:
                clean_stats[st] = 10
            else:
                clean_stats[st] = int(v)
    else:
        clean_stats = default_stats

    raw_attacks = npc_data.get("attacks")
    clean_attacks: List[Dict[str, Any]] = []
    if isinstance(raw_attacks, list) and raw_attacks:
        for atk in raw_attacks[:2]:
            if not isinstance(atk, dict):
                continue
            atk_name = sanitize_text(atk.get("name", "Attack"), max_len=30) or "Attack"
            bonus = atk.get("attack_bonus", 2)
            if not isinstance(bonus, (int, float)) or not (-2 <= int(bonus) <= bounds["max_attack_bonus"]):
                bonus = 2
            else:
                bonus = int(bonus)

            dmg_str = str(atk.get("damage", "1d6")).strip().lower()
            avg_dmg = calculate_average_dice_damage(dmg_str)
            if avg_dmg <= 0.0 or avg_dmg > bounds["max_avg_damage"]:
                dmg_str = "1d6"

            dtype = str(atk.get("damage_type", "slashing")).strip().lower()
            if dtype not in VALID_DAMAGE_TYPES:
                dtype = "slashing"

            clean_attacks.append({
                "name": atk_name,
                "attack_bonus": bonus,
                "damage": dmg_str,
                "damage_type": dtype,
            })

    if not clean_attacks:
        clean_attacks = [{
            "name": f"{name}'s Strike",
            "attack_bonus": 3,
            "damage": "1d6+1",
            "damage_type": "slashing",
        }]

    raw_approval = npc_data.get("approval", 50)
    approval = _safe_int(raw_approval, 50)
    approval = max(0, min(100, approval))

    npc_dict: Dict[str, Any] = {
        "name": name,
        "role": role,
        "persona_seed": persona_seed,
        "hp": {"current": hp_val, "max": hp_val},
        "ac": ac,
        "stats": clean_stats,
        "attacks": clean_attacks,
        "approval": approval,
        "active_conditions": [],
        "side": "player",
        "death_saves": {"success": 0, "fail": 0},
    }
    raw_secret = npc_data.get("secret")
    if isinstance(raw_secret, str) and raw_secret.strip():
        npc_dict["secret"] = sanitize_text(raw_secret, max_len=200)
    return npc_dict


def validate_generated_item(item_data: Any) -> Optional[Dict[str, Any]]:
    """Validate a dynamic item definition against safety bounds and sanitize text."""
    if not isinstance(item_data, dict):
        logger.debug(f"validate_generated_item: input is not a dict ({type(item_data).__name__}) — dropped.")
        return None

    name = sanitize_text(item_data.get("name"), max_len=40)
    if not name:
        logger.debug("validate_generated_item: missing or empty item name — dropped.")
        return None

    itype = str(item_data.get("type", "")).strip().lower()
    item_type_aliases = {
        "armor": "wearable",
        "shield": "wearable",
        "accessory": "wearable",
        "ring": "wearable",
        "cloak": "wearable",
        "helmet": "wearable",
        "boots": "wearable",
        "potion": "consumable",
        "scroll": "consumable",
        "elixir": "consumable",
        "sword": "weapon",
        "bow": "weapon",
        "dagger": "weapon",
        "staff": "weapon",
        "wand": "weapon",
        "axe": "weapon",
        "mace": "weapon",
    }
    itype = item_type_aliases.get(itype, itype)
    if itype not in VALID_ITEM_TYPES:
        logger.debug(f"validate_generated_item: unknown item type '{itype}' — dropped.")
        return None

    slot = item_data.get("slot")
    if itype == "weapon":
        slot_clean = str(slot).strip().lower() if slot else "main_hand"
        if slot_clean not in VALID_WEAPON_SLOTS:
            slot_clean = "main_hand"
    elif itype == "wearable":
        slot_clean = str(slot).strip().lower() if slot else "chest"
        if slot_clean not in VALID_WEARABLE_SLOTS:
            slot_clean = "chest"
    else:
        slot_clean = None

    rarity = str(item_data.get("rarity", "common")).strip().lower()
    if rarity not in ("common", "uncommon", "rare"):
        rarity = "common"

    raw_gold = item_data.get("value_gold", 15)
    gold_val = int(raw_gold) if isinstance(raw_gold, (int, float)) else 15
    gold_val = max(0, min(1000, gold_val))

    raw_desc = item_data.get("description", "")
    desc = sanitize_text(raw_desc, max_len=200, allow_newlines=False) if raw_desc else f"A {rarity} {name}."

    raw_effects = item_data.get("effects", {})
    clean_effects: Dict[str, Any] = {}
    if isinstance(raw_effects, dict):
        if itype == "weapon":
            ab = raw_effects.get("attack_bonus", 0)
            if isinstance(ab, (int, float)) and -2 <= int(ab) <= 5:
                clean_effects["attack_bonus"] = int(ab)
            else:
                clean_effects["attack_bonus"] = 0

            raw_dmg = str(raw_effects.get("damage", "1d6")).strip().lower()
            if 0.0 < calculate_average_dice_damage(raw_dmg) <= 15.0:
                clean_effects["damage"] = raw_dmg
            else:
                clean_effects["damage"] = "1d6"

            dtype = str(raw_effects.get("damage_type", "slashing")).strip().lower()
            if dtype in VALID_DAMAGE_TYPES:
                clean_effects["damage_type"] = dtype
            else:
                clean_effects["damage_type"] = "slashing"

            if raw_effects.get("ranged"):
                clean_effects["ranged"] = True
            cond = raw_effects.get("applies_condition")
            if cond and str(cond).strip().lower() in VALID_MONSTER_CONDITIONS:
                clean_effects["applies_condition"] = str(cond).strip().lower()
        elif itype == "wearable":
            ac_b = raw_effects.get("ac_bonus")
            if isinstance(ac_b, (int, float)) and 0 <= int(ac_b) <= 3:
                clean_effects["ac_bonus"] = int(ac_b)
            ac_base = raw_effects.get("ac_base")
            if isinstance(ac_base, (int, float)) and 10 <= int(ac_base) <= 18:
                clean_effects["ac_base"] = int(ac_base)
            st_b = raw_effects.get("saving_throw_bonus")
            if isinstance(st_b, (int, float)) and 0 <= int(st_b) <= 2:
                clean_effects["saving_throw_bonus"] = int(st_b)
            if isinstance(raw_effects.get("skill_bonus"), dict):
                clean_sb = {}
                for sk_k, sk_v in raw_effects["skill_bonus"].items():
                    if isinstance(sk_k, str) and isinstance(sk_v, (int, float)) and 1 <= int(sk_v) <= 5:
                        clean_sb[sanitize_text(sk_k, max_len=25)] = int(sk_v)
                if clean_sb:
                    clean_effects["skill_bonus"] = clean_sb
        elif itype in ("consumable", "food"):
            raw_heal = raw_effects.get("heal")
            if raw_heal:
                heal_str = str(raw_heal).strip().lower()
                avg_h = calculate_average_dice_damage(heal_str)
                if 0.0 < avg_h <= 20.0:
                    clean_effects["heal"] = heal_str
                elif avg_h > 20.0:
                    clean_effects["heal"] = "1d4+3"
            temp_hp = raw_effects.get("temp_hp")
            if isinstance(temp_hp, (int, float)) and 0 < int(temp_hp) <= 20:
                clean_effects["temp_hp"] = int(temp_hp)

    item_dict: Dict[str, Any] = {
        "name": name,
        "type": itype,
        "rarity": rarity,
        "value_gold": gold_val,
        "description": desc,
        "effects": clean_effects,
    }
    if slot_clean is not None:
        item_dict["slot"] = slot_clean

    if item_data.get("finesse"):
        item_dict["finesse"] = True
    if item_data.get("ranged"):
        item_dict["ranged"] = True
    if item_data.get("two_handed"):
        item_dict["two_handed"] = True

    return item_dict


def validate_generated_event(
    event_data: Any,
    world_state: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Validate a dynamic world event or rumor against safety bounds and sanitize text."""
    if not isinstance(event_data, dict):
        logger.debug(f"validate_generated_event: input is not a dict ({type(event_data).__name__}) — dropped.")
        return None

    title = sanitize_text(event_data.get("title", "Local Rumor"), max_len=60)
    if not title:
        title = "Local Rumor"

    loc_id = event_data.get("location_id")
    import validation
    dm = validation._get_dungeon_manager()
    known_rooms = dm._all_known_room_ids(world_state if world_state else {})
    if not isinstance(loc_id, str) or loc_id not in known_rooms:
        loc_id = world_state.get("current_location", "town_riverside") if world_state else "town_riverside"

    raw_context = event_data.get("context_line", "")
    context_line = sanitize_text(raw_context, max_len=200, allow_newlines=False)
    if not context_line:
        logger.debug("validate_generated_event: missing or empty context_line — dropped.")
        return None

    raw_rumor = event_data.get("rumor_text")
    rumor_text = sanitize_text(raw_rumor, max_len=200, allow_newlines=False) if raw_rumor else None

    post_nb = bool(event_data.get("post_to_notice_board", False)) or (rumor_text is not None)

    raw_gold = event_data.get("reward_gold", 0)
    reward_gold = int(raw_gold) if isinstance(raw_gold, (int, float)) else 0
    reward_gold = max(0, min(100, reward_gold))

    return {
        "title": title,
        "location_id": loc_id,
        "context_line": context_line,
        "rumor_text": rumor_text,
        "post_to_notice_board": post_nb,
        "reward_gold": reward_gold,
    }


def validate_generated_spell(spell_data: Any, player_level: int = 1) -> Optional[Dict[str, Any]]:
    """Validate a dynamic spell or ancient scroll definition against D&D 5e bounds and sanitize text."""
    if not isinstance(spell_data, dict):
        logger.debug(f"validate_generated_spell: input is not a dict ({type(spell_data).__name__}) — dropped.")
        return None

    name = sanitize_text(spell_data.get("name"), max_len=40)
    if not name:
        logger.debug("validate_generated_spell: missing or empty spell name — dropped.")
        return None

    raw_lvl = spell_data.get("level", 1)
    lvl = max(0, min(3, _safe_int(raw_lvl, 1)))

    bounds = SPELL_BOUNDS_BY_LEVEL[lvl]

    stype = str(spell_data.get("type", "attack_roll")).strip().lower()
    if stype not in VALID_SPELL_TYPES:
        stype = "attack_roll"

    save_stat = None
    if stype == "attack_save":
        raw_stat = str(spell_data.get("save_stat", "DEX")).strip().upper()
        save_stat = raw_stat if raw_stat in VALID_SAVE_STATS else "DEX"

    slot_cost = lvl

    raw_effect = spell_data.get("effect", {})
    if not isinstance(raw_effect, dict):
        raw_effect = {}

    clean_effect: Dict[str, Any] = {}
    if stype in ("attack_roll", "attack_save"):
        raw_dmg = str(raw_effect.get("damage", "1d6")).strip().lower()
        avg_dmg = calculate_average_dice_damage(raw_dmg)
        if avg_dmg <= 0.0 or avg_dmg > bounds["max_avg_damage"]:
            raw_dmg = f"{lvl+1}d6" if lvl > 0 else "1d8"
        clean_effect["damage"] = raw_dmg

        dtype = str(raw_effect.get("damage_type", "force")).strip().lower()
        if dtype not in VALID_DAMAGE_TYPES:
            dtype = "force"
        clean_effect["damage_type"] = dtype

        if raw_effect.get("auto_hit") and stype == "attack_roll":
            clean_effect["auto_hit"] = True

    elif stype == "heal":
        if lvl == 0:
            lvl = 1
            slot_cost = 1
            bounds = SPELL_BOUNDS_BY_LEVEL[lvl]
        raw_heal = str(raw_effect.get("heal", "1d4+3")).strip().lower()
        avg_h = calculate_average_dice_damage(raw_heal)
        if avg_h <= 0.0 or avg_h > bounds["max_avg_heal"]:
            raw_heal = "1d4+3"
        clean_effect["heal"] = raw_heal

    elif stype == "buff":
        ac_b = _safe_int(raw_effect.get("ac_bonus", 2), 2)
        ac_b = max(1, min(5, ac_b))
        dur = _safe_int(raw_effect.get("duration_rounds", 3), 3)
        dur = max(1, min(10, dur))
        clean_effect["ac_bonus"] = ac_b
        clean_effect["duration_rounds"] = dur

    elif stype == "utility":
        if raw_effect.get("remove_curse"):
            clean_effect["remove_curse"] = True
        else:
            clean_effect["utility_desc"] = sanitize_text(
                raw_effect.get("utility_desc", "Magical utility effect"), max_len=100
            )

    raw_desc = spell_data.get("description", "")
    desc = sanitize_text(raw_desc, max_len=200, allow_newlines=False) if raw_desc else f"An ancient {stype} spell."

    raw_classes = spell_data.get("class", ["Wizard", "Cleric", "Bard"])
    clean_classes = []
    if isinstance(raw_classes, list):
        for c in raw_classes:
            if isinstance(c, str) and c.strip():
                clean_classes.append(c.strip().title())
    if not clean_classes:
        clean_classes = ["Wizard", "Cleric", "Bard"]

    spell_dict: Dict[str, Any] = {
        "name": name,
        "level": lvl,
        "type": stype,
        "action_cost": "bonus_action" if str(spell_data.get("action_cost", "")).strip().lower() == "bonus_action" else "action",
        "target_type": "aoe_enemies" if str(spell_data.get("target_type", "")).strip().lower() == "aoe_enemies" else "single_enemy",
        "slot_cost": slot_cost,
        "effect": clean_effect,
        "description": desc,
        "class": clean_classes,
    }
    if spell_data.get("half_on_save"):
        spell_dict["half_on_save"] = True
    if spell_data.get("concentration"):
        spell_dict["concentration"] = True
    if spell_data.get("surface") in ("fire", "grease", "water"):
        spell_dict["surface"] = spell_data["surface"]
    if save_stat is not None:
        spell_dict["save_stat"] = save_stat

    return spell_dict
