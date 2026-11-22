"""
combat_manager.py — Turn-Based D&D 5e Combat Engine Facade.

Modularized into the `combat/` package:
  - combat/conditions.py : Combat conditions, surfaces, and combo reactions
  - combat/actions.py    : Attack resolution, Shove action, and Cooldown-Gated Weapon Actions
  - combat/engine.py     : Initiative, combat lifecycle, AI turns, round significance, and round orchestration

All symbols remain exposed on `combat_manager` for 100% backward compatibility.
"""

import json
import logging
import os
import random
import re
from typing import Any, Dict, List, Optional

import paths
from rules import _get_item_catalog, _inventory_item_info, get_modifier

_CATALOG_DIR = paths.CATALOG_DIR

logger = logging.getLogger("combat_manager")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setLevel(logging.DEBUG)
    _h.setFormatter(logging.Formatter("[COMBAT] %(levelname)s: %(message)s"))
    logger.addHandler(_h)

_monster_catalog: Optional[Dict] = None


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


def _roll_d20() -> int:
    """Roll a single d20. Module-level so tests can monkeypatch it."""
    return random.randint(1, 20)


def roll_dice(dice_str: str) -> int:
    """Parse and roll a dice expression such as '1d6+2', '2d8', '1d4-1', or '2d6+1d4+3'."""
    dice_str = dice_str.strip().replace(" ", "")

    m = re.match(r"^(\d+)d(\d+)([+-]\d+)?$", dice_str, re.IGNORECASE)
    if m:
        num_dice = int(m.group(1))
        die_size = int(m.group(2))
        modifier = int(m.group(3)) if m.group(3) else 0
        total = sum(random.randint(1, die_size) for _ in range(num_dice)) + modifier
        return max(1, total)

    m2 = re.match(r"^(\d+)([+-]\d+)?$", dice_str)
    if m2:
        base = int(m2.group(1))
        modifier = int(m2.group(2)) if m2.group(2) else 0
        return max(1, base + modifier)

    # Support multi-dice compound formulas like '2d6+1d4' or '2d6+1d4+3'
    if re.match(r"^\d+d\d+(?:[+-](?:\d+d\d+|\d+))+$", dice_str, re.IGNORECASE):
        total = 0
        for token in re.findall(r"[+-]?\d+(?:d\d+)?", dice_str, re.IGNORECASE):
            sign = -1 if token.startswith("-") else 1
            clean_tok = token.lstrip("+-")
            if "d" in clean_tok.lower():
                nd, ds = clean_tok.lower().split("d", 1)
                total += sign * sum(random.randint(1, int(ds)) for _ in range(int(nd)))
            else:
                total += sign * int(clean_tok)
        return max(1, total)

    raise ValueError(f"Cannot parse dice expression: '{dice_str}'")


# ── Re-export from combat/ submodules ─────────────────────────────────────────

from combat.conditions import (
    CONDITIONS,
    CONDITION_EFFECTS,
    VALID_SURFACES,
    _get_all_combatants,
    _get_condition_set,
    _get_stat_mod,
    apply_condition,
    apply_surface,
    remove_condition,
    tick_conditions,
    tick_surface,
    trigger_lightning_surface_reaction,
)

from combat.actions import (
    _get_skill_mod,
    get_weapon_action_info,
    resolve_attack,
    resolve_class_action,
    resolve_combat_parley,
    resolve_shove,
    resolve_weapon_action,
)

from combat.engine import (
    _HIT_TEMPLATES,
    _MISS_TEMPLATES,
    _build_combatant_from_catalog,
    _get_dex_mod,
    _player_attacks,
    build_round_narration_block,
    build_routine_summary,
    check_combat_end,
    classify_round_significance,
    end_combat,
    resolve_companion_turn,
    resolve_enemy_turn,
    resolve_round,
    roll_initiative,
    start_combat,
    sync_player_state,
)
