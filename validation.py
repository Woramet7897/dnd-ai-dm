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
from typing import Any, Dict, List, Optional

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

# ─── Catalog caches (loaded once, then reused) ───────────────────────────────
_item_catalog: Optional[Dict] = None
_monster_catalog: Optional[Dict] = None

def _get_item_catalog() -> Dict:
    global _item_catalog
    if _item_catalog is None:
        try:
            with open("item_catalog.json", "r", encoding="utf-8") as f:
                _item_catalog = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load item_catalog.json: {e}")
            _item_catalog = {}
    return _item_catalog

def _get_monster_catalog() -> Dict:
    global _monster_catalog
    if _monster_catalog is None:
        try:
            with open("monster_catalog.json", "r", encoding="utf-8") as f:
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


def validate_item_id(item_id: Any) -> bool:
    """
    Return True if item_id exists in item_catalog.json.
    Unknown IDs are logged and must be dropped by the caller — never applied.

    Args:
        item_id: raw value from extraction output (should be a string).

    Returns:
        True if valid, False if unknown.
    """
    if not isinstance(item_id, str):
        logger.debug(f"item_id is not a string (got {type(item_id).__name__}: {item_id!r}) — invalid.")
        return False

    catalog = _get_item_catalog()
    if item_id not in catalog:
        logger.debug(f"Unknown item_id '{item_id}' — not in item_catalog.json, dropped.")
        return False
    return True


def validate_monster_ids(enemy_ids: Any) -> List[str]:
    """
    Validate a list of monster IDs against monster_catalog.json.
    Unknown IDs are dropped individually; valid ones are kept.

    Args:
        enemy_ids: raw value from extraction output (should be a list of strings).

    Returns:
        list of valid monster IDs.
    """
    if not isinstance(enemy_ids, list):
        logger.debug(f"enemy_ids is not a list (got {type(enemy_ids).__name__}) — returning empty.")
        return []

    catalog = _get_monster_catalog()
    valid = []
    for mid in enemy_ids:
        if not isinstance(mid, str):
            logger.debug(f"monster_id entry is not a string: {mid!r} — dropped.")
            continue
        if mid in catalog:
            valid.append(mid)
        else:
            logger.debug(f"Unknown monster_id '{mid}' — not in monster_catalog.json, dropped.")
    return valid


def validate_npc_id(npc_id: Any, world_state: Dict) -> bool:
    """
    Return True if npc_id exists in npc_relationships OR party.companions in the current world_state.
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

    logger.debug(f"Unknown npc_id '{npc_id}' — not in npc_relationships or party.companions, dropped.")
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


def validate_extraction_output(raw: Any, world_state: Optional[Dict] = None) -> Dict:
    """
    Top-level entry point. Runs every sub-validator field by field on the raw extraction output.
    Returns a cleaned dict containing only what passed validation.
    Logs every dropped field with a reason.

    Field-level filtering — a bad action_tag does NOT throw away hp_change.
    Never raises an exception. Returns {} on catastrophic input.

    Args:
        raw: the raw dict from the extraction LLM call (already JSON-parsed).
        world_state: current world save dict (needed for npc_id validation).
                     If None, npc_id validation is skipped and npc_relationship_change is dropped.

    Returns:
        Cleaned dict ready for apply_state_updates().
    """
    if world_state is None:
        world_state = {}

    if not isinstance(raw, dict):
        logger.debug(f"Extraction output is not a dict (got {type(raw).__name__}) — returning empty.")
        return {}

    cleaned: Dict = {}

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
                if validate_item_id(su_raw["add_item_id"]):
                    su_clean["add_item_id"] = su_raw["add_item_id"]

            # remove_item_id
            if "remove_item_id" in su_raw:
                if validate_item_id(su_raw["remove_item_id"]):
                    su_clean["remove_item_id"] = su_raw["remove_item_id"]

            # gold_change — allow any int within reason
            if "gold_change" in su_raw:
                result = validate_numeric_range("gold_change", su_raw["gold_change"], -10000, 10000)
                if result is not None:
                    su_clean["gold_change"] = result

            # Pass through other string fields directly (location, quest updates etc.)
            for str_field in ("new_location", "add_quest", "remove_quest"):
                if str_field in su_raw and isinstance(su_raw[str_field], str):
                    su_clean[str_field] = su_raw[str_field]

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
            valid_enemies = validate_monster_ids(cs["enemies"])
            if valid_enemies:
                cleaned["combat_start"] = {"enemies": valid_enemies}
            else:
                logger.debug("combat_start.enemies had no valid monster IDs — combat_start dropped.")
        else:
            logger.debug(f"combat_start malformed (no 'enemies' key or not a dict) — dropped.")

    # ── world_updates ─────────────────────────────────────────────────────────
    if "world_updates" in raw:
        wu = raw["world_updates"]
        if isinstance(wu, dict):
            cleaned["world_updates"] = wu  # structural validation deferred to dungeon_manager
        else:
            logger.debug(f"world_updates is not a dict — dropped.")

    # ── quest_updates ─────────────────────────────────────────────────────────
    if "quest_updates" in raw:
        qu = raw["quest_updates"]
        if isinstance(qu, dict):
            cleaned["quest_updates"] = qu
        else:
            logger.debug(f"quest_updates is not a dict — dropped.")

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
            npc_id = nrc.get("npc_id")
            delta = nrc.get("delta")

            npc_ok = validate_npc_id(npc_id, world_state)
            d_min, d_max = NUMERIC_BOUNDS["npc_relationship_change_delta"]
            delta_ok = validate_numeric_range("npc_relationship_change.delta", delta, d_min, d_max)

            if npc_ok and delta_ok is not None:
                cleaned["npc_relationship_change"] = {"npc_id": npc_id, "delta": delta_ok}
            else:
                logger.debug("npc_relationship_change dropped (bad npc_id or delta out of range).")
        else:
            logger.debug(f"npc_relationship_change is not a dict — dropped.")

    return cleaned
