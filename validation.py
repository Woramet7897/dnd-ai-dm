"""
validation.py — Whitelist Sanitization & Extraction Validation Facade.

Modularized into the `validators/` package:
  - validators/entities.py   : Text sanitization, dice math, and generated Monster/NPC/Item/Event/Spell validation
  - validators/extraction.py : Field-level whitelist validation for LLM extraction output

All symbols remain exposed on `validation` for 100% backward compatibility.
"""

import json
import logging
import os
from typing import Dict, Optional

import paths

_CATALOG_DIR = paths.CATALOG_DIR
_dungeon_manager = None


def _get_dungeon_manager():
    global _dungeon_manager
    if _dungeon_manager is None:
        import dungeon_manager as dm
        _dungeon_manager = dm
    return _dungeon_manager


logger = logging.getLogger("validation")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setLevel(logging.DEBUG)
    _handler.setFormatter(logging.Formatter("[VALIDATION] %(levelname)s: %(message)s"))
    logger.addHandler(_handler)

_item_catalog: Optional[Dict] = None
_monster_catalog: Optional[Dict] = None
_spell_catalog: Optional[Dict] = None
_npc_catalog: Optional[Dict] = None


def reset_caches() -> None:
    """Clear cached catalogs on validation module and rules module."""
    global _item_catalog, _monster_catalog, _spell_catalog, _npc_catalog
    _item_catalog = None
    _monster_catalog = None
    _spell_catalog = None
    _npc_catalog = None
    import rules
    rules.reset_caches()


def _get_npc_catalog() -> Dict:
    global _npc_catalog
    if _npc_catalog is None:
        try:
            path = os.path.join(_CATALOG_DIR, "npc_catalog.json")
            with open(path, "r", encoding="utf-8") as f:
                _npc_catalog = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load npc_catalog.json: {e}")
            _npc_catalog = {}
    return _npc_catalog


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


def _get_spell_catalog() -> Dict:
    global _spell_catalog
    if _spell_catalog is None:
        try:
            path = os.path.join(_CATALOG_DIR, "spell_catalog.json")
            with open(path, "r", encoding="utf-8") as f:
                _spell_catalog = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load spell_catalog.json: {e}")
            _spell_catalog = {}
    return _spell_catalog


# ── Re-export from validators/ submodules ─────────────────────────────────────

from validators.entities import (
    DICE_FORMULA_REGEX,
    MONSTER_BOUNDS_BY_LEVEL,
    NPC_BOUNDS_BY_LEVEL,
    PROMPT_INJECTION_PATTERNS,
    SPELL_BOUNDS_BY_LEVEL,
    VALID_DAMAGE_TYPES,
    VALID_ITEM_TYPES,
    VALID_MONSTER_CONDITIONS,
    VALID_SAVE_STATS,
    VALID_SPELL_TYPES,
    VALID_WEAPON_SLOTS,
    VALID_WEARABLE_SLOTS,
    _safe_int,
    calculate_average_dice_damage,
    sanitize_text,
    validate_generated_event,
    validate_generated_item,
    validate_generated_monster,
    validate_generated_npc,
    validate_generated_spell,
)

from validators.extraction import (
    ACTION_TAGS,
    NUMERIC_BOUNDS,
    VALID_QUEST_STATUSES,
    _validate_extraction_output_inner,
    validate_action_tags,
    validate_extraction_output,
    validate_item_id,
    validate_monster_ids,
    validate_npc_id,
    validate_numeric_range,
    validate_quest_updates,
    validate_spell_id,
)
