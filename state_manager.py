"""
state_manager.py — Core D&D 5e State & Math Facade.

Modularized into the `state/` package:
  - state/currency.py     : Tri-denomination currency (GP/SP/CP) & pricing
  - state/persistence.py  : Atomic JSON save/load & rolling backup rotation
  - state/equipment.py    : AC calculation, equip/unequip, identify, curses, consumables, shops
  - state/checks.py       : Inspiration, ability checks, death saves, concentration, state/quest updates
  - state/progression.py  : XP, leveling up, short/long rest, downed outcomes
  - state/magic.py        : Spell learning, spell save/attack resolution, spellcasting
  - state/cooking.py      : Food spoilage and camp cooking
  - state/crime.py        : Pickpocketing, guard persuasion, imprisonment, prison break, bounty
  - state/companions.py   : Companion recruitment, dismissal, approval, camp dialogue responses

All symbols remain exposed on `state_manager` for 100% backward compatibility.
"""

import copy
import json
import logging
import math
import os
import random
import tempfile
import uuid
from typing import Any, Dict, List, Optional, Tuple, Union

import paths
import rules
from rules import (
    CONCENTRATION_DC_FLOOR,
    DIFFICULTY_TO_DC,
    SKILL_TO_STAT,
    STAT_NAMES,
    _get_item_catalog,
    _get_shop_catalog,
    _get_spell_catalog,
    _inventory_item_info,
    _roll_d20,
    _roll_dice,
    get_modifier,
    get_spell_catalog,
    is_proficient,
    resolve_item,
    resolve_spell,
)

logger = logging.getLogger("state_manager")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[STATE] %(levelname)s: %(message)s"))
    logger.addHandler(_h)

SUPPORTED_SCHEMA_VERSION = 4

SAVES_DIR = paths.SAVES_DIR
WORLD_SAVES_DIR = paths.WORLD_SAVES_DIR
BACKUP_DIR = paths.BACKUP_DIR
MAX_BACKUPS = 3
_CATALOG_DIR = paths.CATALOG_DIR


def __getattr__(name: str) -> Any:
    if name in ("_item_catalog", "_spell_catalog", "_shop_catalog"):
        return getattr(rules, name)
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


# ── Re-export from state/ submodules ──────────────────────────────────────────

from state.currency import (
    CP_PER_GP,
    CP_PER_SP,
    SP_PER_GP,
    _ensure_currency,
    _from_copper,
    add_currency,
    can_afford,
    format_currency,
    get_item_price_breakdown,
    get_item_sell_breakdown,
    get_total_copper,
    gold_to_mixed_currency,
    subtract_currency,
)

from state.persistence import (
    _atomic_write,
    _backup_dir,
    _rotate_backup,
    _save_path,
    _world_save_path,
    load_character,
    load_world,
    save_character,
    save_world,
)

from state.equipment import (
    ConditionEntry,
    _compute_ac,
    apply_condition_to_state,
    buy_item,
    equip_item,
    generate_item,
    get_active_effects,
    identify_item,
    pay_cleric_remove_curse,
    register_generated_item,
    remove_curse,
    sell_item,
    unequip_item,
    use_consumable,
)

from state.checks import (
    MAX_INSPIRATION,
    apply_npc_relationship_change,
    apply_quest_updates,
    apply_state_updates,
    award_inspiration,
    check_inspiration_trigger,
    get_inspiration,
    log_roll,
    reroll_check,
    resolve_check,
    resolve_concentration_check,
    resolve_death_save,
    spend_inspiration,
)

from state.progression import (
    BG3_FEATS,
    LEVEL_THRESHOLDS,
    SIGNATURE_LEVEL_UP_SPELLS,
    apply_feat_or_asi,
    apply_level_up,
    award_xp,
    check_level_up,
    handle_period_change,
    long_rest,
    perform_short_rest,
    resolve_downed_outcome,
)

from state.magic import (
    cast_spell,
    learn_spell,
    resolve_spell_attack,
    resolve_spell_save,
)

from state.cooking import (
    cook_meal,
    tick_food_spoilage,
)

from state.crime import (
    attempt_guard_persuasion,
    attempt_lockpick_escape,
    attempt_pickpocket,
    imprison_player,
    pay_bounty,
    serve_prison_time,
)

from state.companions import (
    MAX_PARTY_COMPANIONS,
    dismiss_companion,
    get_companion_approval,
    recruit_companion,
    respond_to_camp_dialogue,
)


# ── Cross-module convenience forwarders ──────────────────────────────────────

def advance_time(*args, **kwargs):
    """Convenience forwarder to dungeon_manager.advance_time."""
    import dungeon_manager
    return dungeon_manager.advance_time(*args, **kwargs)


def refresh_notice_board(*args, **kwargs):
    """Forwarder to dungeon_manager.refresh_notice_board."""
    import dungeon_manager
    return dungeon_manager.refresh_notice_board(*args, **kwargs)


def get_notice_board(*args, **kwargs):
    """Forwarder to dungeon_manager.get_notice_board."""
    import dungeon_manager
    return dungeon_manager.get_notice_board(*args, **kwargs)


def accept_notice_board_quest(*args, **kwargs):
    """Forwarder to dungeon_manager.accept_notice_board_quest."""
    import dungeon_manager
    return dungeon_manager.accept_notice_board_quest(*args, **kwargs)


def complete_notice_board_quest(*args, **kwargs):
    """Forwarder to dungeon_manager.complete_notice_board_quest."""
    import dungeon_manager
    return dungeon_manager.complete_notice_board_quest(*args, **kwargs)


def is_camp_context(world_state: Optional[Dict[str, Any]] = None) -> bool:
    """Forwarder to dungeon_manager.is_camp_context."""
    import dungeon_manager
    return dungeon_manager.is_camp_context(world_state)


def generate_camp_dialogue(*args, **kwargs):
    """Forwarder to llm_handler.generate_camp_dialogue."""
    import llm_handler
    return llm_handler.generate_camp_dialogue(*args, **kwargs)
