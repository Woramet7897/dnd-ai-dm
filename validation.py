"""
validation.py — Phase 1
Validation layer between extraction call raw JSON output and apply_state_updates().
Every extraction output MUST pass through validate_extraction_output() before being applied.
Nothing from the LLM extraction call is trusted or applied without passing through here first.

Design principles (per spec Section 13b / PART 5b):
- Field-level filtering, NEVER all-or-nothing rejection of the whole payload.
- Dropped fields are logged with a reason, never silently swallowed.
- Out-of-range numeric values are DROPPED, not clamped, to avoid guessing a "corrected" number.
- One retry on a full JSON parse failure, then fall back to no-op. Never crash the game loop.
"""

import json
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional

# Catalog directory — resolved relative to this file so loading succeeds
# regardless of the cwd when the process was launched (spec bug-fix, Phase 4).
_CATALOG_DIR = os.path.dirname(os.path.abspath(__file__))

# dungeon_manager imported lazily below to avoid circular imports at module load
_dungeon_manager = None

def _get_dungeon_manager():
    global _dungeon_manager
    if _dungeon_manager is None:
        import dungeon_manager as dm
        _dungeon_manager = dm
    return _dungeon_manager


# ─── Logger (debug-level, not shown to the player) ───────────────────────────
logger = logging.getLogger("validation")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setLevel(logging.DEBUG)
    _handler.setFormatter(logging.Formatter("[VALIDATION] %(levelname)s: %(message)s"))
    logger.addHandler(_handler)

# ─── Fixed vocabularies ───────────────────────────────────────────────────────
ACTION_TAGS: List[str] = [
    "honesty", "kindness", "curiosity", "humor",
    "cruelty", "greed", "cowardice", "bravery", "flirtation",
]

# ─── Numeric sanity bounds ────────────────────────────────────────────────────
# Values outside these ranges are hallucinated extremes — drop them, do NOT clamp.
NUMERIC_BOUNDS: Dict[str, tuple] = {
    "hp_change":           (-200, 200),   # max realistic single hit/heal in 1-5 scope
    "npc_relationship_change_delta": (-50, 50),
}

# ─── Prompt Injection & Sanitization Patterns ────────────────────────────────
PROMPT_INJECTION_PATTERNS: List[re.Pattern] = [
    re.compile(r"\[/?system[^\]]*\]", re.IGNORECASE),
    re.compile(r"ignore\s+(all\s+|previous\s+|prior\s+)?instructions?", re.IGNORECASE),
    re.compile(r"system\s*prompt:?", re.IGNORECASE),
    re.compile(r"new\s+instructions?:?", re.IGNORECASE),
]

def sanitize_text(text: Any, max_len: int = 40, allow_newlines: bool = False) -> str:
    """
    Sanitize LLM-generated string:
    - Enforces string type
    - Strips HTML tags
    - Strips URLs (http://, https://)
    - Strips markdown link syntax [text](url) -> text
    - Neutralizes known prompt injection phrases
    - Strips markdown formatting characters (*, _, #, `, ~, >, [, ])
    - Strips control characters (and newlines unless allow_newlines=True)
    - Collapses multiple whitespace
    - Clamps to max_len
    """
    if not isinstance(text, str):
        return ""

    s = text
    # Remove HTML tags
    s = re.sub(r"<[^>]+>", "", s)
    # Remove URLs
    s = re.sub(r"https?://\S+", "", s)
    # Strip markdown link target syntax
    s = re.sub(r"\[([^\]]+)\]\([^\)]*\)", r"\1", s)
    # Neutralize prompt injection phrases
    for pat in PROMPT_INJECTION_PATTERNS:
        s = pat.sub("", s)
    # Remove markdown formatting characters
    s = re.sub(r"[*_#`~>\[\]\\]", "", s)

    # Strip control characters
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

    # Collapse multiple whitespace
    s = re.sub(r"[ \t]+", " ", s).strip()
    return s[:max_len].strip()


DICE_FORMULA_REGEX: re.Pattern = re.compile(r"^(\d+)d(\d+)(?:([+-])(\d+))?$")

def calculate_average_dice_damage(formula: str) -> float:
    """
    Parse a dice formula like '1d6+2', '2d8', '1d10-1' and compute average damage.
    Returns 0.0 if invalid.
    """
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


# ─── Level-scaled monster safety bounds (D&D 5e CR guidelines) ────────────────
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
}


def validate_generated_monster(m_data: Any, player_level: int = 1) -> Optional[Dict[str, Any]]:
    """
    Validate a dynamic monster definition against level-scaled bounds and sanitize text.
    Returns cleaned monster template dict or None if invalid/out-of-bounds.
    """
    if not isinstance(m_data, dict):
        logger.debug(f"validate_generated_monster: input is not a dict ({type(m_data).__name__}) — dropped.")
        return None

    # 1. Name
    name = sanitize_text(m_data.get("name"), max_len=40)
    if not name:
        logger.debug("validate_generated_monster: missing or empty monster name — dropped.")
        return None

    # 2. Level bounds
    lvl = max(1, min(5, int(player_level) if isinstance(player_level, (int, float)) else 1))
    bounds = MONSTER_BOUNDS_BY_LEVEL[lvl]

    # 3. HP
    raw_hp = m_data.get("hp")
    if isinstance(raw_hp, dict):
        hp_val = raw_hp.get("max") or raw_hp.get("current")
    elif isinstance(raw_hp, (int, float)):
        hp_val = int(raw_hp)
    else:
        logger.debug(f"validate_generated_monster: invalid hp spec ({raw_hp!r}) — dropped.")
        return None

    if not isinstance(hp_val, int) or hp_val < 1 or hp_val > bounds["max_hp"]:
        logger.debug(f"validate_generated_monster: hp={hp_val} out of bounds (1..{bounds['max_hp']}) — dropped.")
        return None

    # 4. AC
    ac = m_data.get("ac")
    if not isinstance(ac, int) or ac < 5 or ac > bounds["max_ac"]:
        logger.debug(f"validate_generated_monster: ac={ac} out of bounds (5..{bounds['max_ac']}) — dropped.")
        return None

    # 5. Stats
    raw_stats = m_data.get("stats")
    if not isinstance(raw_stats, dict):
        logger.debug("validate_generated_monster: missing stats dict — dropped.")
        return None
    clean_stats: Dict[str, int] = {}
    for st in ("STR", "DEX", "CON", "INT", "WIS", "CHA"):
        v = raw_stats.get(st)
        if not isinstance(v, int) or v < 1 or v > 24:
            logger.debug(f"validate_generated_monster: stat {st}={v} invalid (1..24) — dropped.")
            return None
        clean_stats[st] = v

    # 6. Attacks
    raw_attacks = m_data.get("attacks")
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

        clean_attacks.append({
            "name": atk_name,
            "attack_bonus": bonus,
            "damage": dmg_str,
            "damage_type": dtype,
            "applies_condition": clean_cond,
        })

    if not clean_attacks:
        logger.debug("validate_generated_monster: no valid attacks survived — dropped.")
        return None

    # 7. XP Value
    raw_xp = m_data.get("xp_value", 50)
    xp_val = int(raw_xp) if isinstance(raw_xp, (int, float)) else 50
    if xp_val < 0 or xp_val > bounds["max_xp"]:
        xp_val = min(bounds["max_xp"], max(0, xp_val))

    # 8. Gold drop
    raw_gold = m_data.get("gold_drop", {})
    if isinstance(raw_gold, dict):
        g_min = max(0, min(50, int(raw_gold.get("min", 0))))
        g_max = max(g_min, min(50, int(raw_gold.get("max", g_min))))
    else:
        g_min, g_max = 0, 5
    clean_gold = {"min": g_min, "max": g_max}

    # 9. Challenge rating
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

    # Optional immunities / resistances
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


# ─── Dynamic NPC / Companion Safety Bounds & Validator ────────────────────────
NPC_BOUNDS_BY_LEVEL: Dict[int, Dict[str, Any]] = {
    1: {"max_hp": 30,  "max_ac": 16, "max_attack_bonus": 5, "max_avg_damage": 10.0},
    2: {"max_hp": 40,  "max_ac": 16, "max_attack_bonus": 6, "max_avg_damage": 14.0},
    3: {"max_hp": 55,  "max_ac": 17, "max_attack_bonus": 7, "max_avg_damage": 18.0},
    4: {"max_hp": 70,  "max_ac": 17, "max_attack_bonus": 7, "max_avg_damage": 22.0},
    5: {"max_hp": 85,  "max_ac": 18, "max_attack_bonus": 8, "max_avg_damage": 26.0},
}


def validate_generated_npc(npc_data: Any, player_level: int = 1) -> Optional[Dict[str, Any]]:
    """
    Validate a dynamic NPC/companion definition against safety bounds and sanitize text.
    Returns cleaned companion template dict or None if invalid.
    """
    if not isinstance(npc_data, dict):
        logger.debug(f"validate_generated_npc: input is not a dict ({type(npc_data).__name__}) — dropped.")
        return None

    # 1. Name
    name = sanitize_text(npc_data.get("name"), max_len=40)
    if not name:
        logger.debug("validate_generated_npc: missing or empty NPC name — dropped.")
        return None

    # 2. Role / Title
    role = sanitize_text(npc_data.get("role", npc_data.get("title", "Companion")), max_len=40) or "Companion"

    # 3. Personality / Persona Seed
    raw_persona = npc_data.get("persona_seed", npc_data.get("personality", ""))
    persona_seed = sanitize_text(raw_persona, max_len=150) if raw_persona else f"A companion named {name}."

    # 4. Level bounds
    lvl = max(1, min(5, int(player_level) if isinstance(player_level, (int, float)) else 1))
    bounds = NPC_BOUNDS_BY_LEVEL[lvl]

    # 5. HP
    raw_hp = npc_data.get("hp", 15)
    if isinstance(raw_hp, dict):
        hp_val = raw_hp.get("max") or raw_hp.get("current", 15)
    elif isinstance(raw_hp, (int, float)):
        hp_val = int(raw_hp)
    else:
        logger.debug(f"validate_generated_npc: invalid hp spec ({raw_hp!r}) — dropped.")
        return None

    if not isinstance(hp_val, int) or hp_val < 1 or hp_val > bounds["max_hp"]:
        logger.debug(f"validate_generated_npc: hp={hp_val} out of bounds (1..{bounds['max_hp']}) — dropped.")
        return None

    # 6. AC
    raw_ac = npc_data.get("ac", 12)
    if not isinstance(raw_ac, (int, float)):
        logger.debug(f"validate_generated_npc: invalid ac ({raw_ac!r}) — dropped.")
        return None
    ac = int(raw_ac)
    if ac < 8 or ac > bounds["max_ac"]:
        logger.debug(f"validate_generated_npc: ac={ac} out of bounds (8..{bounds['max_ac']}) — dropped.")
        return None

    # 7. Ability Stats
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

    # 8. Attacks
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

    # 9. Approval
    raw_approval = npc_data.get("approval", 50)
    approval = int(raw_approval) if isinstance(raw_approval, (int, float)) else 50
    approval = max(0, min(100, approval))

    return {
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


# ─── Dynamic Item Safety Bounds & Validator ──────────────────────────────────
VALID_ITEM_TYPES: set = {
    "weapon", "wearable", "consumable", "tool", "food", "instrument", "ration", "raw_food",
}

VALID_WEAPON_SLOTS: set = {"main_hand", "off_hand"}
VALID_WEARABLE_SLOTS: set = {"chest", "cloak", "ring", "off_hand", "head", "feet"}


def validate_generated_item(item_data: Any) -> Optional[Dict[str, Any]]:
    """
    Validate a dynamic item definition against safety bounds and sanitize text.
    Returns cleaned item template dict or None if invalid.
    """
    if not isinstance(item_data, dict):
        logger.debug(f"validate_generated_item: input is not a dict ({type(item_data).__name__}) — dropped.")
        return None

    # 1. Name
    name = sanitize_text(item_data.get("name"), max_len=40)
    if not name:
        logger.debug("validate_generated_item: missing or empty item name — dropped.")
        return None

    # 2. Type
    itype = str(item_data.get("type", "")).strip().lower()
    if itype not in VALID_ITEM_TYPES:
        logger.debug(f"validate_generated_item: unknown item type '{itype}' — dropped.")
        return None

    # 3. Slot (for weapons and wearables)
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

    # 4. Rarity
    rarity = str(item_data.get("rarity", "common")).strip().lower()
    if rarity not in ("common", "uncommon", "rare"):
        rarity = "common"

    # 5. Value gold
    raw_gold = item_data.get("value_gold", 15)
    gold_val = int(raw_gold) if isinstance(raw_gold, (int, float)) else 15
    gold_val = max(0, min(1000, gold_val))

    # 6. Description
    raw_desc = item_data.get("description", "")
    desc = sanitize_text(raw_desc, max_len=200, allow_newlines=False) if raw_desc else f"A {rarity} {name}."

    # 7. Effects
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
        elif itype in ("consumable", "food"):
            raw_heal = raw_effects.get("heal")
            if raw_heal and calculate_average_dice_damage(str(raw_heal)) > 0:
                clean_effects["heal"] = str(raw_heal).strip().lower()
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

    return item_dict


# ─── Catalog caches (loaded once, then reused) ───────────────────────────────
_item_catalog: Optional[Dict] = None
_monster_catalog: Optional[Dict] = None

def _get_item_catalog() -> Dict:
    global _item_catalog
    if _item_catalog is None:
        try:
            path = os.path.join(_CATALOG_DIR, "item_catalog.json")
            with open(path, "r", encoding="utf-8") as f:
                _item_catalog = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load item_catalog.json: {e}")
            _item_catalog = {}
    return _item_catalog

def _get_monster_catalog() -> Dict:
    global _monster_catalog
    if _monster_catalog is None:
        try:
            path = os.path.join(_CATALOG_DIR, "monster_catalog.json")
            with open(path, "r", encoding="utf-8") as f:
                _monster_catalog = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load monster_catalog.json: {e}")
            _monster_catalog = {}
    return _monster_catalog


# ─── Sub-validators ───────────────────────────────────────────────────────────

def validate_action_tags(tags: Any) -> List[str]:
    """
    Drop any tag not in the fixed ACTION_TAGS vocabulary.
    Field-level: only unknown tags are dropped, valid ones are kept.

    Args:
        tags: raw value from extraction output (should be a list of strings).

    Returns:
        list of valid tags (may be empty).
    """
    if not isinstance(tags, list):
        logger.debug(f"action_tags is not a list (got {type(tags).__name__}) — dropping entire field.")
        return []

    valid = []
    for tag in tags:
        if not isinstance(tag, str):
            logger.debug(f"action_tag entry is not a string: {tag!r} — dropped.")
            continue
        if tag in ACTION_TAGS:
            valid.append(tag)
        else:
            logger.debug(f"Unknown action_tag '{tag}' — not in fixed vocabulary, dropped.")
    return valid


# ─── Quest-status enum ───────────────────────────────────────────────────────
VALID_QUEST_STATUSES: set = {"active", "completed", "failed", "abandoned"}

def validate_quest_updates(quest_updates: Any) -> Optional[Dict]:
    """
    Validate and whitelist the quest_updates block from LLM extraction output.
    Shape matches spec Section 12b:
      new_quest        (dict, optional) — quest to create: {title (str), quest_type ('main'|'side'),
                                          description (str, optional)}
      objective_update (dict, optional) — {quest_id (str), objective_index (int), done (bool)}

    Any other key is silently dropped.
    Returns cleaned dict, or None if nothing valid survived.
    """
    if not isinstance(quest_updates, dict):
        logger.debug(f"quest_updates is not a dict ({type(quest_updates).__name__}) — dropped.")
        return None

    cleaned: Dict = {}

    # new_quest — create a brand-new quest entry
    if "new_quest" in quest_updates:
        nq = quest_updates["new_quest"]
        if isinstance(nq, dict):
            nq_clean: Dict = {}
            title = nq.get("title")
            if isinstance(title, str) and title.strip():
                nq_clean["title"] = title.strip()
            else:
                logger.debug(f"quest_updates.new_quest.title missing or not a string — dropped.")
            quest_type = nq.get("quest_type", "side")
            if quest_type not in ("main", "side"):
                logger.debug(f"quest_updates.new_quest.quest_type '{quest_type}' invalid — defaulting to 'side'.")
                quest_type = "side"
            nq_clean["quest_type"] = quest_type
            desc = nq.get("description")
            if isinstance(desc, str):
                nq_clean["description"] = desc
            if "title" in nq_clean:
                cleaned["new_quest"] = nq_clean
            else:
                logger.debug("quest_updates.new_quest had no valid title — dropped.")
        else:
            logger.debug(f"quest_updates.new_quest not a dict ({type(nq).__name__}) — dropped.")

    # objective_update — mark a quest objective done/undone
    if "objective_update" in quest_updates:
        ou = quest_updates["objective_update"]
        if isinstance(ou, dict):
            ou_clean: Dict = {}
            qid = ou.get("quest_id")
            if isinstance(qid, str) and qid.strip():
                ou_clean["quest_id"] = qid.strip()
            else:
                logger.debug("quest_updates.objective_update.quest_id missing or not a string — dropped.")
            obj_idx = ou.get("objective_index")
            if isinstance(obj_idx, int) and obj_idx >= 0:
                ou_clean["objective_index"] = obj_idx
            else:
                logger.debug(f"quest_updates.objective_update.objective_index invalid ({obj_idx!r}) — dropped.")
            done = ou.get("done")
            if isinstance(done, bool):
                ou_clean["done"] = done
            else:
                logger.debug(f"quest_updates.objective_update.done not a bool ({done!r}) — dropped.")
            if "quest_id" in ou_clean and "objective_index" in ou_clean and "done" in ou_clean:
                cleaned["objective_update"] = ou_clean
            else:
                logger.debug("quest_updates.objective_update missing required fields — dropped.")
        else:
            logger.debug(f"quest_updates.objective_update not a dict ({type(ou).__name__}) — dropped.")

    if not cleaned:
        logger.debug("quest_updates had no valid fields after filtering — dropped.")
        return None

    return cleaned

def validate_item_id(item_id: Any, world_state: Optional[Dict] = None) -> bool:
    """
    Return True if item_id exists in item_catalog.json OR generated_items in world_state.
    Unknown IDs are logged and must be dropped by the caller — never applied.

    Args:
        item_id: raw value from extraction output (should be a string).
        world_state: optional world save dict.

    Returns:
        True if valid, False if unknown.
    """
    if not isinstance(item_id, str):
        logger.debug(f"item_id is not a string (got {type(item_id).__name__}: {item_id!r}) — invalid.")
        return False

    catalog = _get_item_catalog()
    if item_id in catalog:
        return True

    if world_state and isinstance(world_state, dict):
        gen_items = world_state.get("generated_items", {})
        if item_id in gen_items:
            return True

    logger.debug(f"Unknown item_id '{item_id}' — not in item_catalog.json or generated_items, dropped.")
    return False


def validate_monster_ids(enemy_ids: Any, world_state: Optional[Dict] = None) -> List[str]:
    """
    Validate a list of monster IDs against monster_catalog.json and world_state["generated_monsters"].
    Unknown IDs are dropped individually; valid ones are kept.

    Args:
        enemy_ids: raw value from extraction output (should be a list of strings).
        world_state: optional world state dict containing dynamic monsters.

    Returns:
        list of valid monster IDs.
    """
    if not isinstance(enemy_ids, list):
        logger.debug(f"enemy_ids is not a list (got {type(enemy_ids).__name__}) — returning empty.")
        return []

    catalog = _get_monster_catalog()
    gen_monsters = world_state.get("generated_monsters", {}) if isinstance(world_state, dict) else {}
    valid = []
    for mid in enemy_ids:
        if not isinstance(mid, str):
            logger.debug(f"monster_id entry is not a string: {mid!r} — dropped.")
            continue
        if mid in catalog:
            valid.append(mid)
        elif mid in gen_monsters:
            valid.append(mid)
        else:
            logger.debug(f"Unknown monster_id '{mid}' — not in monster_catalog.json or generated_monsters, dropped.")
    return valid


def validate_npc_id(npc_id: Any, world_state: Dict) -> bool:
    """
    Return True if npc_id exists in npc_relationships, party.companions, or generated_npcs in world_state.
    Unknown NPC IDs are logged and must be dropped by the caller.

    Args:
        npc_id: raw value from extraction output.
        world_state: current world save dict.

    Returns:
        True if known, False if unknown.
    """
    if not isinstance(npc_id, str):
        logger.debug(f"npc_id is not a string (got {type(npc_id).__name__}: {npc_id!r}) — invalid.")
        return False

    # Check npc_relationships
    npc_rels = world_state.get("npc_relationships", {})
    if npc_id in npc_rels:
        return True

    # Check party.companions
    party = world_state.get("party", {})
    companions = party.get("companions", [])
    companion_ids = {c.get("id") for c in companions if isinstance(c, dict)}
    if npc_id in companion_ids:
        return True

    # Check generated_npcs
    gen_npcs = world_state.get("generated_npcs", {})
    if npc_id in gen_npcs:
        return True

    logger.debug(f"Unknown npc_id '{npc_id}' — not in npc_relationships, party.companions, or generated_npcs, dropped.")
    return False


def validate_numeric_range(field_name: str, value: Any, min_val: int, max_val: int) -> Optional[int]:
    """
    Check value is an int/float within [min_val, max_val].
    Out-of-range values are DROPPED (return None), not clamped.
    Dropping is safer than guessing a "corrected" number.

    Args:
        field_name: name of the field, used in log messages.
        value: raw value from extraction output.
        min_val: inclusive minimum.
        max_val: inclusive maximum.

    Returns:
        int value if valid, None if out-of-range or wrong type.
    """
    if not isinstance(value, (int, float)):
        logger.debug(f"Field '{field_name}' is not numeric (got {type(value).__name__}: {value!r}) — dropped.")
        return None

    int_value = int(value)
    if int_value < min_val or int_value > max_val:
        logger.debug(
            f"Field '{field_name}' value {int_value} is outside allowed range "
            f"[{min_val}, {max_val}] — dropped (not clamped)."
        )
        return None

    return int_value


def validate_extraction_output(
    raw: Any,
    world_state: Optional[Dict] = None,
    player_state: Optional[Dict] = None,
) -> Dict:
    """
    Top-level entry point. Runs every sub-validator field by field on the raw extraction output.
    Returns a cleaned dict containing only what passed validation.
    Logs every dropped field with a reason.

    Field-level filtering — a bad action_tag does NOT throw away hp_change.
    Never raises an exception. Returns {} on catastrophic input.

    Args:
        raw: the raw dict from the extraction LLM call (already JSON-parsed).
        world_state: current world save dict (needed for npc_id validation and dynamic registries).
                     If None, npc_id validation is skipped and npc_relationship_change is dropped.
        player_state: current player save dict (used for level-scaled monster bounds).

    Returns:
        Cleaned dict ready for apply_state_updates().
    """
    if world_state is None:
        world_state = {}

    if not isinstance(raw, dict):
        logger.debug(f"Extraction output is not a dict (got {type(raw).__name__}) — returning empty.")
        return {}

    cleaned: Dict = {}

    # ── generated_monsters (Spec: Dynamic Monster Generation) ─────────────────
    alias_to_real_mon_id: Dict[str, str] = {}
    if "generated_monsters" in raw and isinstance(raw["generated_monsters"], dict):
        catalog = _get_monster_catalog()
        p_lvl = 1
        if player_state and isinstance(player_state, dict):
            p_lvl = player_state.get("level", 1)

        for alias, m_data in raw["generated_monsters"].items():
            if not isinstance(alias, str) or not alias.strip():
                continue
            alias_clean = alias.strip().lower()

            # Rule 6: Catalog collision guard — Static catalog ALWAYS wins!
            if alias_clean in catalog:
                logger.warning(
                    f"generated_monsters rejected override of static catalog monster '{alias_clean}'."
                )
                continue

            valid_m = validate_generated_monster(m_data, player_level=p_lvl)
            if valid_m is None:
                logger.debug(f"generated_monster '{alias}' failed validation — dropped.")
                continue

            # Rule 6: Python-generated ID (prevent LLM ID spoofing)
            real_id = f"gen_mon_{uuid.uuid4().hex[:8]}"
            valid_m["id"] = real_id

            if world_state is not None and isinstance(world_state, dict):
                gen_monsters = world_state.setdefault("generated_monsters", {})
                gen_monsters[real_id] = valid_m
                logger.info(f"Registered dynamic monster '{valid_m['name']}' as '{real_id}' in world_state.")

            alias_to_real_mon_id[alias] = real_id
            alias_to_real_mon_id[alias_clean] = real_id

    # ── generated_items (Spec: Dynamic Item Generation) ───────────────────────
    alias_to_real_item_id: Dict[str, str] = {}
    if "generated_items" in raw and isinstance(raw["generated_items"], dict):
        item_catalog = _get_item_catalog()
        for alias, i_data in raw["generated_items"].items():
            if not isinstance(alias, str) or not alias.strip():
                continue
            alias_clean = alias.strip().lower()

            # Rule 6: Catalog collision guard — Static catalog ALWAYS wins!
            if alias_clean in item_catalog:
                logger.warning(
                    f"generated_items rejected override of static catalog item '{alias_clean}'."
                )
                continue

            valid_item = validate_generated_item(i_data)
            if valid_item is None:
                logger.debug(f"generated_item '{alias}' failed validation — dropped.")
                continue

            # Rule 6: Python-generated ID (prevent LLM ID spoofing)
            real_id = f"gen_item_{uuid.uuid4().hex[:8]}"
            valid_item["item_id"] = real_id

            if world_state is not None and isinstance(world_state, dict):
                gen_items = world_state.setdefault("generated_items", {})
                gen_items[real_id] = valid_item
                logger.info(f"Registered dynamic item '{valid_item['name']}' as '{real_id}' in world_state.")

            alias_to_real_item_id[alias] = real_id
            alias_to_real_item_id[alias_clean] = real_id

    # ── generated_npcs (Spec: Dynamic NPC / Companion Generation) ────────────
    alias_to_real_npc_id: Dict[str, str] = {}
    if "generated_npcs" in raw and isinstance(raw["generated_npcs"], dict):
        p_lvl = 1
        if player_state and isinstance(player_state, dict):
            p_lvl = player_state.get("level", 1)

        party_comp_ids = set()
        if world_state and isinstance(world_state, dict):
            party_comp_ids = {
                c.get("id") for c in world_state.get("party", {}).get("companions", [])
                if isinstance(c, dict) and c.get("id")
            }

        for alias, n_data in raw["generated_npcs"].items():
            if not isinstance(alias, str) or not alias.strip():
                continue
            alias_clean = alias.strip().lower()

            if alias_clean in party_comp_ids:
                logger.warning(
                    f"generated_npcs rejected override of existing companion '{alias_clean}'."
                )
                continue

            valid_npc = validate_generated_npc(n_data, player_level=p_lvl)
            if valid_npc is None:
                logger.debug(f"generated_npc '{alias}' failed validation — dropped.")
                continue

            # Rule 6: Python-generated ID (prevent LLM ID spoofing)
            real_id = f"gen_npc_{uuid.uuid4().hex[:8]}"
            valid_npc["id"] = real_id

            if world_state is not None and isinstance(world_state, dict):
                gen_npcs = world_state.setdefault("generated_npcs", {})
                gen_npcs[real_id] = valid_npc
                logger.info(f"Registered dynamic NPC '{valid_npc['name']}' as '{real_id}' in world_state.")

            alias_to_real_npc_id[alias] = real_id
            alias_to_real_npc_id[alias_clean] = real_id

    # ── state_updates ──────────────────────────────────────────────────────────
    if "state_updates" in raw:
        su_raw = raw["state_updates"]
        if isinstance(su_raw, dict):
            su_clean: Dict = {}

            # hp_change
            if "hp_change" in su_raw:
                hp_min, hp_max = NUMERIC_BOUNDS["hp_change"]
                result = validate_numeric_range("hp_change", su_raw["hp_change"], hp_min, hp_max)
                if result is not None:
                    su_clean["hp_change"] = result
                else:
                    logger.debug("hp_change dropped from state_updates.")

            # add_item_id
            if "add_item_id" in su_raw:
                raw_iid = su_raw["add_item_id"]
                target_iid = alias_to_real_item_id.get(
                    raw_iid, alias_to_real_item_id.get(raw_iid.lower(), raw_iid)
                ) if isinstance(raw_iid, str) else raw_iid
                if validate_item_id(target_iid, world_state):
                    su_clean["add_item_id"] = target_iid

            # remove_item_id
            if "remove_item_id" in su_raw:
                raw_iid = su_raw["remove_item_id"]
                target_iid = alias_to_real_item_id.get(
                    raw_iid, alias_to_real_item_id.get(raw_iid.lower(), raw_iid)
                ) if isinstance(raw_iid, str) else raw_iid
                if validate_item_id(target_iid, world_state):
                    su_clean["remove_item_id"] = target_iid

            # gold_change — allow any int within reason
            if "gold_change" in su_raw:
                result = validate_numeric_range("gold_change", su_raw["gold_change"], -10000, 10000)
                if result is not None:
                    su_clean["gold_change"] = result

            # recruit_companion_id / add_companion_id
            if "recruit_companion_id" in su_raw or "add_companion_id" in su_raw:
                raw_cid = su_raw.get("recruit_companion_id") or su_raw.get("add_companion_id")
                target_cid = alias_to_real_npc_id.get(
                    raw_cid, alias_to_real_npc_id.get(str(raw_cid).lower(), raw_cid)
                ) if isinstance(raw_cid, str) else raw_cid
                if validate_npc_id(target_cid, world_state):
                    su_clean["recruit_companion_id"] = target_cid

            # move_to_location_id — relocate player to an EXISTING room (Issue C fix).
            # Renamed from 'new_location' to avoid collision with world_updates.new_location
            # (which registers a brand-new room). Must be validated against known room IDs.
            if "move_to_location_id" in su_raw:
                dest_id = su_raw["move_to_location_id"]
                if isinstance(dest_id, str) and dest_id.strip():
                    dm = _get_dungeon_manager()
                    known = dm._all_known_room_ids(world_state if world_state else {})
                    if dest_id in known:
                        su_clean["move_to_location_id"] = dest_id
                    else:
                        logger.debug(
                            f"state_updates.move_to_location_id '{dest_id}' not in known rooms — dropped."
                        )
                else:
                    logger.debug("state_updates.move_to_location_id not a non-empty string — dropped.")

            if su_clean:
                cleaned["state_updates"] = su_clean
        else:
            logger.debug(f"state_updates is not a dict (got {type(su_raw).__name__}) — dropped.")

    # ── requires_roll ─────────────────────────────────────────────────────────
    # requires_roll — WHITELIST ONLY. Do not pass rr through unfiltered.
    # Any hallucinated 'dc', 'bonus', or other stray key from the LLM is
    # silently discarded here. Python owns DC via DIFFICULTY_TO_DC in state_manager.
    if "requires_roll" in raw:
        rr = raw["requires_roll"]
        VALID_D = {"easy", "medium", "hard", "very_hard"}
        VALID_S = {"STR", "DEX", "CON", "INT", "WIS", "CHA"}
        if isinstance(rr, dict):
            d, s = rr.get("difficulty"), rr.get("stat")
            if d not in VALID_D:
                logger.debug(f"requires_roll.difficulty '{d}' invalid — dropped.")
            elif s not in VALID_S:
                logger.debug(f"requires_roll.stat '{s}' invalid — dropped.")
            else:
                # Reconstruct from only the two validated fields — never pass rr as-is
                cleaned["requires_roll"] = {"difficulty": d, "stat": s}
        else:
            logger.debug("requires_roll not a dict — dropped.")

    # ── combat_start ──────────────────────────────────────────────────────────
    if "combat_start" in raw:
        cs = raw["combat_start"]
        if isinstance(cs, dict) and "enemies" in cs:
            raw_enemies = cs.get("enemies", [])
            remapped_enemies = []
            if isinstance(raw_enemies, list):
                for e in raw_enemies:
                    if isinstance(e, str):
                        remapped_enemies.append(alias_to_real_mon_id.get(e, alias_to_real_mon_id.get(e.lower(), e)))
                    else:
                        remapped_enemies.append(e)
            else:
                remapped_enemies = raw_enemies

            valid_enemies = validate_monster_ids(remapped_enemies, world_state=world_state)
            if valid_enemies:
                cleaned["combat_start"] = {"enemies": valid_enemies}
            else:
                logger.debug("combat_start.enemies had no valid monster IDs — combat_start dropped.")
        else:
            logger.debug(f"combat_start malformed (no 'enemies' key or not a dict) — dropped.")

    # ── world_updates — DEV-2 RESOLVED (Phase 3) ──────────────────────────────
    # new_location is now validated via dungeon_manager.validate_new_location().
    # Other world_updates keys (flags, event triggers) pass through structurally;
    # deep validation of those is deferred to dungeon_manager's write path.
    if "world_updates" in raw:
        wu = raw["world_updates"]
        if isinstance(wu, dict):
            wu_clean = dict(wu)  # shallow copy — we may strip new_location
            if "new_location" in wu_clean:
                nl = wu_clean["new_location"]
                dm = _get_dungeon_manager()
                ok, reason = dm.validate_new_location(nl, world_state)
                if not ok:
                    logger.debug(
                        f"world_updates.new_location failed validation — stripped from world_updates. "
                        f"Reason: {reason}"
                    )
                    del wu_clean["new_location"]
            if wu_clean:
                cleaned["world_updates"] = wu_clean
        else:
            logger.debug("world_updates is not a dict — dropped.")

    # ── quest_updates — DEV-3 RESOLVED ────────────────────────────────────────
    # validate_quest_updates() enforces a field whitelist + status enum.
    # Owner: validation.py (always was — dungeon_manager has no quest functions).
    if "quest_updates" in raw:
        qu_clean = validate_quest_updates(raw["quest_updates"])
        if qu_clean is not None:
            cleaned["quest_updates"] = qu_clean

    # ── action_tags ───────────────────────────────────────────────────────────
    if "action_tags" in raw:
        valid_tags = validate_action_tags(raw["action_tags"])
        if valid_tags:
            cleaned["action_tags"] = valid_tags
        # If empty after filtering, simply omit the key — no error

    # ── npc_relationship_change ───────────────────────────────────────────────
    if "npc_relationship_change" in raw:
        nrc = raw["npc_relationship_change"]
        if isinstance(nrc, dict):
            raw_nid = nrc.get("npc_id")
            target_nid = alias_to_real_npc_id.get(
                raw_nid, alias_to_real_npc_id.get(str(raw_nid).lower(), raw_nid)
            ) if isinstance(raw_nid, str) else raw_nid
            delta = nrc.get("delta")

            npc_ok = validate_npc_id(target_nid, world_state)
            d_min, d_max = NUMERIC_BOUNDS["npc_relationship_change_delta"]
            delta_ok = validate_numeric_range("npc_relationship_change.delta", delta, d_min, d_max)

            if npc_ok and delta_ok is not None:
                cleaned["npc_relationship_change"] = {"npc_id": target_nid, "delta": delta_ok}
            else:
                logger.debug("npc_relationship_change dropped (bad npc_id or delta out of range).")
        else:
            logger.debug(f"npc_relationship_change is not a dict — dropped.")

    return cleaned
