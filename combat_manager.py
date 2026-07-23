"""
combat_manager.py — Phase 4
Owns 100% of combat math. The LLM narrates outcomes; Python decides them.

Design principles (spec Section 9):
- All dice rolls, hit/miss, damage, conditions, and initiative are pure Python.
- No LLM calls inside this module.
- Extraction calls are SKIPPED during combat (spec 9b-6); this module is the only
  thing that writes to combat_state.
- Round-based narration: Python resolves the WHOLE round first, then assembles ONE
  combined result block for the narrative LLM (spec 9b-iii).
- start_combat() is idempotent — a second call while combat is active is a no-op
  (spec 9b-iv idempotency guard, needed because both manual button and extraction
  call trigger can fire in the same turn).
"""

import copy
import json
import logging
import random
import re
from typing import Any, Dict, List, Optional, Tuple

# ── Logger ────────────────────────────────────────────────────────────────────
logger = logging.getLogger("combat_manager")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setLevel(logging.DEBUG)
    _h.setFormatter(logging.Formatter("[COMBAT] %(levelname)s: %(message)s"))
    logger.addHandler(_h)

# ── Monster catalog cache ─────────────────────────────────────────────────────
_monster_catalog: Optional[Dict] = None

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


# ════════════════════════════════════════════════════════════════════════════════
# CONDITIONS (spec Section 20)
# ════════════════════════════════════════════════════════════════════════════════

CONDITIONS = {"prone", "poisoned", "stunned", "restrained", "frightened"}

# Mechanical effect lookup — used by resolve_attack() before rolling.
CONDITION_EFFECTS: Dict[str, Dict[str, bool]] = {
    "prone":       {"attacks_against_have_advantage": True},
    "poisoned":    {"attack_rolls_disadvantage": True},
    "stunned":     {"skip_turn": True},
    "restrained":  {"attack_rolls_disadvantage": True, "attacks_against_have_advantage": True},
    "frightened":  {"cannot_approach_source": True},
}


def apply_condition(combatant: Dict[str, Any], condition: str, duration: int) -> None:
    """
    Apply a named condition to a combatant for `duration` rounds.
    Spec Section 20: conditions are only applied via a static applies_condition
    field on a monster attack — never decided ad hoc by the LLM.

    If the condition is already active, refreshes the duration to max(current, new).
    Unknown condition names are rejected and logged.

    Args:
        combatant: the combatant dict (player, enemy, or companion).
        condition:  one of CONDITIONS.
        duration:   number of rounds. Must be >= 1.
    """
    if condition not in CONDITIONS:
        logger.debug(f"apply_condition: '{condition}' not in fixed vocabulary — rejected.")
        return
    if not isinstance(duration, int) or duration < 1:
        logger.debug(f"apply_condition: invalid duration {duration!r} — rejected.")
        return

    active = combatant.setdefault("active_conditions", [])
    for entry in active:
        if entry["condition"] == condition:
            entry["duration"] = max(entry["duration"], duration)
            logger.debug(f"apply_condition: refreshed '{condition}' on '{combatant.get('id')}' to {entry['duration']} rounds.")
            return

    active.append({"condition": condition, "duration": duration})
    logger.debug(f"apply_condition: applied '{condition}' to '{combatant.get('id')}' for {duration} rounds.")


def tick_conditions(combat_state: Dict[str, Any]) -> None:
    """
    Decrement all active condition durations by 1 at the END of a round.
    Conditions reaching 0 are removed automatically.
    Spec Section 20: 'Duration is a fixed number of rounds set at application time,
    decremented by tick_conditions() each round, removed automatically at zero.'

    Args:
        combat_state: the live combat_state dict from world_state.
    """
    all_combatants: list
    if isinstance(combat_state.get("player_combatant"), dict):
        all_combatants = (
            [combat_state["player_combatant"]]
            + combat_state.get("enemies", [])
            + combat_state.get("companions", [])
        )
    else:
        all_combatants = (
            combat_state.get("enemies", [])
            + combat_state.get("companions", [])
        )

    for combatant in all_combatants:
        active = combatant.get("active_conditions", [])
        updated = []
        for entry in active:
            entry["duration"] -= 1
            if entry["duration"] > 0:
                updated.append(entry)
            else:
                logger.debug(f"tick_conditions: '{entry['condition']}' expired on '{combatant.get('id')}'.")
        combatant["active_conditions"] = updated


def _get_condition_set(combatant: Dict[str, Any]) -> set:
    """Return the set of currently active condition names for a combatant."""
    return {e["condition"] for e in combatant.get("active_conditions", [])}


# ════════════════════════════════════════════════════════════════════════════════
# DICE ROLLING
# ════════════════════════════════════════════════════════════════════════════════

def _roll_d20() -> int:
    """Roll a single d20. Module-level so tests can monkeypatch it."""
    return random.randint(1, 20)


def roll_dice(dice_str: str) -> int:
    """
    Parse and roll a dice expression such as '1d6+2', '2d8', or '1d4-1'.
    Returns the total roll result (minimum 1).

    Args:
        dice_str: dice expression string from the monster/item catalog.

    Returns:
        Integer result of the roll (>= 1).

    Raises:
        ValueError if the expression cannot be parsed.
    """
    dice_str = dice_str.strip().replace(" ", "")

    # Path 1: standard NdX±M form (e.g. '1d6+2', '2d8', '1d4-1')
    m = re.match(r"^(\d+)d(\d+)([+-]\d+)?$", dice_str, re.IGNORECASE)
    if m:
        num_dice  = int(m.group(1))
        die_size  = int(m.group(2))
        modifier  = int(m.group(3)) if m.group(3) else 0
        total = sum(random.randint(1, die_size) for _ in range(num_dice)) + modifier
        return max(1, total)

    # Path 2: flat integer with optional modifier (e.g. '1', '1+0', '5', '3-1').
    # Used by Unarmed Strike and any catalog entry that deals a fixed flat amount.
    m2 = re.match(r"^(\d+)([+-]\d+)?$", dice_str)
    if m2:
        base     = int(m2.group(1))
        modifier = int(m2.group(2)) if m2.group(2) else 0
        return max(1, base + modifier)

    raise ValueError(f"Cannot parse dice expression: '{dice_str}'")


# ════════════════════════════════════════════════════════════════════════════════
# INITIATIVE
# ════════════════════════════════════════════════════════════════════════════════

def _get_dex_mod(combatant: Dict[str, Any]) -> int:
    """Return the DEX modifier (floor((DEX - 10) / 2)) for a combatant."""
    dex = combatant.get("stats", {}).get("DEX", 10)
    return (dex - 10) // 2


def roll_initiative(combatants: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Roll initiative for all combatants and return them sorted highest-first.
    Ties broken by DEX modifier, then by random tiebreak.

    Each combatant dict gets an 'initiative' key set to the raw d20+DEX roll.
    Spec Section 9b-2: 'Initiative: unchanged — roll_initiative(combatants).'

    Args:
        combatants: list of combatant dicts (player, enemies, companions mixed).

    Returns:
        Same list sorted by initiative descending (mutates combatants in-place, also returns).
    """
    for c in combatants:
        roll   = _roll_d20()
        dex_m  = _get_dex_mod(c)
        c["initiative"] = roll + dex_m
        c["_initiative_tiebreak"] = random.random()
        logger.debug(f"Initiative: {c.get('name')} rolled {roll}+{dex_m}={c['initiative']}")

    combatants.sort(
        key=lambda c: (c["initiative"], _get_dex_mod(c), c["_initiative_tiebreak"]),
        reverse=True,
    )
    for c in combatants:
        c.pop("_initiative_tiebreak", None)

    return combatants


# ════════════════════════════════════════════════════════════════════════════════
# COMBAT STATE MANAGEMENT
# ════════════════════════════════════════════════════════════════════════════════

def _build_combatant_from_catalog(monster_id: str, instance_index: int = 1) -> Optional[Dict[str, Any]]:
    """
    Build a live combatant dict from the monster catalog entry.
    Gives each instance a unique id suffix (e.g. 'goblin_scout_1', 'goblin_scout_2').
    Returns None if the monster_id is not found.
    """
    catalog = _get_monster_catalog()
    template = catalog.get(monster_id)
    if template is None:
        logger.debug(f"_build_combatant_from_catalog: '{monster_id}' not in catalog.")
        return None

    c = copy.deepcopy(template)
    c["id"]   = f"{monster_id}_{instance_index}"
    c["side"] = "enemy"
    c.setdefault("initiative", None)
    c.setdefault("active_conditions", [])
    # Ensure mutable HP
    if isinstance(c.get("hp"), dict):
        c["hp"] = {"current": c["hp"]["max"], "max": c["hp"]["max"]}
    return c


def start_combat(
    enemy_ids: List[str],
    player_state: Dict[str, Any],
    world_state: Dict[str, Any],
    companion_states: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Instantiate combat_state and write it into world_state["combat_state"].
    Rolls initiative for all combatants and sets the turn order.

    Idempotency guard (spec 9b-iv): if combat_state is already active, returns None.
    This is the single entry point for both the extraction-call trigger and the
    manual 'Attack' button — both must call this function, never write combat_state
    directly.

    Args:
        enemy_ids:        list of monster_catalog IDs (may contain duplicates).
        player_state:     character save dict (schema_version 4).
        world_state:      world save dict; combat_state written here.
        companion_states: list of companion combatant dicts (optional).

    Returns:
        The new combat_state dict, or None if combat was already active (no-op).
    """
    # ── Idempotency guard ─────────────────────────────────────────────────────
    if world_state.get("combat_state") is not None:
        logger.debug("start_combat: combat already active — no-op (idempotency guard).")
        return None

    # ── Build enemy combatants ────────────────────────────────────────────────
    id_counts: Dict[str, int] = {}
    enemies: List[Dict[str, Any]] = []
    for mid in enemy_ids:
        id_counts[mid] = id_counts.get(mid, 0) + 1
        c = _build_combatant_from_catalog(mid, id_counts[mid])
        if c is None:
            logger.debug(f"start_combat: unknown enemy '{mid}' — skipped.")
            continue
        enemies.append(c)

    if not enemies:
        logger.debug("start_combat: no valid enemies — combat not started.")
        return None

    # ── Build player combatant ────────────────────────────────────────────────
    player_c: Dict[str, Any] = {
        "id":               "player",
        "name":             player_state.get("name", "Adventurer"),
        "side":             "player",
        "hp":               dict(player_state.get("hp", {"current": 10, "max": 10})),
        "ac":               10 + (player_state.get("stats", {}).get("DEX", 10) - 10) // 2,
        "stats":            player_state.get("stats", {}),
        "attacks":          _player_attacks(player_state),
        "initiative":       None,
        "active_conditions": copy.deepcopy(player_state.get("active_conditions", [])),
    }

    # ── Build companion combatants ────────────────────────────────────────────
    companions: List[Dict[str, Any]] = []
    for comp in (companion_states or []):
        comp_c = copy.deepcopy(comp)
        comp_c.setdefault("side", "player")
        comp_c.setdefault("active_conditions", [])
        comp_c.setdefault("initiative", None)
        companions.append(comp_c)

    # ── Roll initiative for everyone ──────────────────────────────────────────
    all_combatants = [player_c] + companions + enemies
    roll_initiative(all_combatants)
    turn_order = [c["id"] for c in all_combatants]

    combat_state: Dict[str, Any] = {
        "round":            1,
        "turn_index":       0,          # index into turn_order
        "turn_order":       turn_order,
        "player_combatant": player_c,
        "enemies":          enemies,
        "companions":       companions,
        "round_log":        [],         # accumulated results for this round
        "status":           "active",   # 'active' | 'ended'
        "outcome":          None,       # filled by check_combat_end()
    }

    world_state["combat_state"] = combat_state
    logger.debug(
        f"start_combat: combat started with {len(enemies)} enemy(s). "
        f"Turn order: {turn_order}"
    )
    return combat_state


def _player_attacks(player_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Build a minimal attack list for the player based on their inventory.
    Returns a basic unarmed strike if no weapon is found.
    This is intentionally simple for Phase 4 — the Phase 6 item system will
    enrich this once item_catalog attack stats are integrated.
    """
    # Phase 4 placeholder — unarmed strike as guaranteed fallback.
    attacks = [
        {"name": "Unarmed Strike", "attack_bonus": 0, "damage": "1+0",
         "damage_type": "bludgeoning", "applies_condition": None}
    ]
    # If the player carries a dagger, give a simple melee attack.
    for item in player_state.get("inventory", []):
        item_id = item.get("item_id", "") if isinstance(item, dict) else ""
        if "dagger" in item_id.lower():
            attacks = [
                {"name": "Dagger", "attack_bonus": 2, "damage": "1d4+0",
                 "damage_type": "piercing", "applies_condition": None}
            ]
            break
    return attacks


def end_combat(world_state: Dict[str, Any]) -> None:
    """
    Clear combat_state from world_state, marking combat as over.
    Call after check_combat_end() returns a non-None outcome.
    Does nothing if combat is not active.
    """
    if world_state.get("combat_state") is None:
        return
    world_state["combat_state"] = None
    logger.debug("end_combat: combat_state cleared.")


# ════════════════════════════════════════════════════════════════════════════════
# ATTACK RESOLUTION (spec Section 9b-3)
# ════════════════════════════════════════════════════════════════════════════════

def resolve_attack(
    attacker: Dict[str, Any],
    target: Dict[str, Any],
    attack: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Resolve a single attack from attacker → target.
    Python decides hit/miss, damage, crits, fumbles, and conditions.
    The LLM only narrates the outcome — never decides it.

    Conditions that affect this roll (spec Section 20):
    - Attacker has 'poisoned' or 'restrained': attack roll made with disadvantage.
    - Target has 'prone' or 'restrained': attack roll made with advantage.
    - Attacker has 'stunned': turn is skipped (caller should check before calling).
    - 'frightened': cannot_approach_source — simplified to disadvantage for v1.

    Args:
        attacker: combatant dict (has stats, active_conditions, attacks).
        target:   combatant dict (has hp, ac, active_conditions).
        attack:   one entry from attacker["attacks"] (name, attack_bonus, damage,
                  damage_type, applies_condition).

    Returns:
        result dict with keys:
          hit (bool), crit (bool), fumble (bool), damage (int),
          damage_type (str), target_id (str), attacker_id (str),
          attack_name (str), condition_applied (str|None),
          target_hp_after (int), target_downed (bool)
    """
    attacker_conditions = _get_condition_set(attacker)
    target_conditions   = _get_condition_set(target)

    # ── Determine advantage / disadvantage ───────────────────────────────────
    has_advantage    = False
    has_disadvantage = False

    if "prone" in target_conditions or "restrained" in target_conditions:
        has_advantage = True
    if "poisoned" in attacker_conditions or "restrained" in attacker_conditions:
        has_disadvantage = True
    if "frightened" in attacker_conditions:
        has_disadvantage = True

    # Advantage and disadvantage cancel each other out (5e rule)
    if has_advantage and has_disadvantage:
        has_advantage = has_disadvantage = False

    # ── Roll to hit ───────────────────────────────────────────────────────────
    if has_advantage:
        raw_roll = max(_roll_d20(), _roll_d20())
    elif has_disadvantage:
        raw_roll = min(_roll_d20(), _roll_d20())
    else:
        raw_roll = _roll_d20()

    attack_bonus  = attack.get("attack_bonus", 0)
    total_to_hit  = raw_roll + attack_bonus
    target_ac     = target.get("ac", 10)

    crit   = (raw_roll == 20)
    fumble = (raw_roll == 1)
    hit    = crit or (not fumble and total_to_hit >= target_ac)

    # ── Roll damage ───────────────────────────────────────────────────────────
    damage = 0
    condition_applied = None

    if hit:
        damage_expr = attack.get("damage", "1d4")
        try:
            damage = roll_dice(damage_expr)
        except ValueError:
            damage = 1

        if crit:
            # Double dice on crit (simplified: roll damage again and add)
            try:
                damage += roll_dice(damage_expr)
            except ValueError:
                damage += 1

        # ── Apply damage to target ────────────────────────────────────────────
        target_hp = target.setdefault("hp", {"current": 1, "max": 1})
        target_hp["current"] = max(0, target_hp["current"] - damage)

        # ── Apply condition (from static attack field, not LLM) ───────────────
        cond = attack.get("applies_condition")
        if cond and cond in CONDITIONS:
            apply_condition(target, cond, duration=2)
            condition_applied = cond

    target_downed = target.get("hp", {}).get("current", 1) <= 0

    result = {
        "attacker_id":       attacker.get("id", "unknown"),
        "attacker_name":     attacker.get("name", "Unknown"),
        "target_id":         target.get("id", "unknown"),
        "target_name":       target.get("name", "Unknown"),
        "attack_name":       attack.get("name", "Attack"),
        "raw_roll":          raw_roll,
        "total_to_hit":      total_to_hit,
        "target_ac":         target_ac,
        "hit":               hit,
        "crit":              crit,
        "fumble":            fumble,
        "damage":            damage,
        "damage_type":       attack.get("damage_type", ""),
        "condition_applied": condition_applied,
        "target_hp_after":   target.get("hp", {}).get("current", 0),
        "target_downed":     target_downed,
    }
    logger.debug(
        f"resolve_attack: {attacker.get('name')} → {target.get('name')}: "
        f"roll={raw_roll}+{attack_bonus}={total_to_hit} vs AC {target_ac} → "
        f"{'CRIT' if crit else 'FUMBLE' if fumble else 'HIT' if hit else 'MISS'}, "
        f"dmg={damage}"
    )
    return result


# ════════════════════════════════════════════════════════════════════════════════
# TURN RESOLUTION
# ════════════════════════════════════════════════════════════════════════════════

def resolve_enemy_turn(
    enemy: Dict[str, Any],
    targets: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Resolve an enemy's turn: pick a target and attack with the first non-None attack.
    Enemies always attack the first living target in the targets list (simple AI for v1).
    If the enemy is stunned, turn is skipped.

    Args:
        enemy:   the enemy combatant dict.
        targets: list of potential targets (player + companions), all alive.

    Returns:
        result dict from resolve_attack, or a 'skip' result if stunned or no targets.
    """
    if "stunned" in _get_condition_set(enemy):
        logger.debug(f"resolve_enemy_turn: {enemy.get('name')} is stunned — skip.")
        return {"attacker_id": enemy["id"], "attacker_name": enemy.get("name", ""),
                "skipped": True, "reason": "stunned"}

    living_targets = [t for t in targets if t.get("hp", {}).get("current", 0) > 0]
    if not living_targets:
        return {"attacker_id": enemy["id"], "attacker_name": enemy.get("name", ""),
                "skipped": True, "reason": "no_targets"}

    target  = living_targets[0]
    attacks = enemy.get("attacks", [])
    if not attacks:
        return {"attacker_id": enemy["id"], "attacker_name": enemy.get("name", ""),
                "skipped": True, "reason": "no_attacks"}

    return resolve_attack(enemy, target, attacks[0])


def resolve_companion_turn(
    companion: Dict[str, Any],
    enemies: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Resolve a companion's turn: attack the lowest-HP living enemy.
    If the companion is stunned, turn is skipped.

    Args:
        companion: the companion combatant dict.
        enemies:   list of enemy combatants.

    Returns:
        result dict from resolve_attack, or a 'skip' result.
    """
    if "stunned" in _get_condition_set(companion):
        logger.debug(f"resolve_companion_turn: {companion.get('name')} is stunned — skip.")
        return {"attacker_id": companion["id"], "attacker_name": companion.get("name", ""),
                "skipped": True, "reason": "stunned"}

    living_enemies = [e for e in enemies if e.get("hp", {}).get("current", 0) > 0]
    if not living_enemies:
        return {"attacker_id": companion["id"], "attacker_name": companion.get("name", ""),
                "skipped": True, "reason": "no_targets"}

    # Target lowest-HP enemy (smart-ish companion AI)
    target  = min(living_enemies, key=lambda e: e.get("hp", {}).get("current", 9999))
    attacks = companion.get("attacks", [])
    if not attacks:
        return {"attacker_id": companion["id"], "attacker_name": companion.get("name", ""),
                "skipped": True, "reason": "no_attacks"}

    return resolve_attack(companion, target, attacks[0])


# ════════════════════════════════════════════════════════════════════════════════
# ROUND SIGNIFICANCE CLASSIFICATION (spec Section 9b-4)
# ════════════════════════════════════════════════════════════════════════════════

def classify_round_significance(
    round_results: List[Dict[str, Any]],
    combat_state: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Split all round events into 'significant' and 'routine' lists.
    Spec 9b-4:
      Significant: crit, fumble, downed/killed, any condition applied, any spell/special
                   item use, HP dropping below 25% for any named combatant.
      Routine:     a normal hit or miss with no special outcome.

    Args:
        round_results: list of result dicts from resolve_attack (and skip events).
        combat_state:  current combat_state (used to look up HP thresholds).

    Returns:
        {
          "significant": [result, ...],
          "routine":     [result, ...],
        }
    """
    significant = []
    routine     = []

    def _is_below_25pct(result: Dict[str, Any]) -> bool:
        """Check if target HP after attack is below 25% of max."""
        # Look up max HP from the live combatant
        target_id = result.get("target_id")
        for pool in ("enemies", "companions"):
            for c in combat_state.get(pool, []):
                if c["id"] == target_id:
                    hp_max = c.get("hp", {}).get("max", 1)
                    hp_cur = result.get("target_hp_after", 0)
                    return hp_cur < hp_max * 0.25
        # player
        if target_id == "player":
            pc = combat_state.get("player_combatant", {})
            hp_max = pc.get("hp", {}).get("max", 1)
            hp_cur = result.get("target_hp_after", 0)
            return hp_cur < hp_max * 0.25
        return False

    for result in round_results:
        if result.get("skipped"):
            routine.append(result)
            continue

        is_sig = (
            result.get("crit")
            or result.get("fumble")
            or result.get("target_downed")
            or result.get("condition_applied") is not None
            or _is_below_25pct(result)
        )
        if is_sig:
            significant.append(result)
        else:
            routine.append(result)

    return {"significant": significant, "routine": routine}


# ════════════════════════════════════════════════════════════════════════════════
# ROUTINE HIT TEMPLATE BANK (spec Section 9b-ii)
# ════════════════════════════════════════════════════════════════════════════════

_HIT_TEMPLATES = [
    "{attacker} lands a solid hit on {target} for {damage} {dtype} damage.",
    "{target} staggers as {attacker}'s {weapon} connects for {damage}.",
    "{attacker}'s {weapon} strikes {target} for {damage} {dtype} damage.",
]
_MISS_TEMPLATES = [
    "{attacker}'s {weapon} goes wide, missing {target} entirely.",
    "{target} sidesteps {attacker}'s {weapon}.",
    "{attacker} swings at {target} but fails to connect.",
]

def build_routine_summary(routine_results: List[Dict[str, Any]]) -> str:
    """
    Build a plain-text summary of routine hits/misses using canned templates.
    Spec 9b-ii: requires zero LLM involvement.

    Returns:
        A multi-line string with one sentence per routine event.
    """
    lines = []
    for r in routine_results:
        if r.get("skipped"):
            name = r.get("attacker_name", "Someone")
            reason = r.get("reason", "skipped")
            lines.append(f"{name} skips their turn ({reason}).")
            continue

        attacker = r.get("attacker_name", "?")
        target   = r.get("target_name", "?")
        weapon   = r.get("attack_name", "attack")
        damage   = r.get("damage", 0)
        dtype    = r.get("damage_type", "")

        if r.get("hit"):
            tmpl = random.choice(_HIT_TEMPLATES)
        else:
            tmpl = random.choice(_MISS_TEMPLATES)

        lines.append(
            tmpl.format(attacker=attacker, target=target, weapon=weapon,
                        damage=damage, dtype=dtype)
        )
    return "\n".join(lines) if lines else ""


def build_round_narration_block(
    round_num: int,
    significant: List[Dict[str, Any]],
    routine_summary: str,
) -> str:
    """
    Assemble the '[System: Round Result]' block sent to the narrative LLM.
    Spec 9b-iii format:
      [System: Round Result]
      Round N:
      - <significant event line>
      - (Routine: <summary>)
      Narrate this round based strictly on these results...

    Args:
        round_num:       current round number.
        significant:     list of significant result dicts.
        routine_summary: pre-built plain text for routine events.

    Returns:
        The complete string to inject as the system content for the narration call.
    """
    lines = [f"[System: Round Result]", f"Round {round_num}:"]

    for r in significant:
        attacker = r.get("attacker_name", "?")
        target   = r.get("target_name", "?")
        weapon   = r.get("attack_name", "attack")
        damage   = r.get("damage", 0)

        if r.get("fumble"):
            lines.append(f"- {attacker} critically fumbles with {weapon} — complete miss.")
        elif r.get("crit"):
            status = "defeated" if r.get("target_downed") else f"{r.get('target_hp_after')} HP remaining"
            lines.append(f"- {attacker} critically hits {target} with {weapon}: {damage} damage, {status}.")
        elif r.get("target_downed"):
            lines.append(f"- {attacker} attacks {target} with {weapon}: {damage} damage, {target} is downed.")
        elif r.get("condition_applied"):
            cond = r["condition_applied"]
            lines.append(f"- {attacker} attacks {target} with {weapon}: {damage} damage, {target} is now {cond}.")
        else:
            # Below 25% HP case
            lines.append(
                f"- {attacker} attacks {target} with {weapon}: {damage} damage "
                f"({r.get('target_hp_after')} HP remaining — critically low)."
            )

    if routine_summary:
        lines.append(f"- (Routine: {routine_summary})")

    lines.append(
        "Narrate this round based strictly on these results. "
        "Do not invent additional actions or change any outcome."
    )
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════════════
# COMBAT END CHECK
# ════════════════════════════════════════════════════════════════════════════════

def check_combat_end(combat_state: Dict[str, Any]) -> Optional[str]:
    """
    Check whether combat should end.
    Spec 9b-5: 'hp <= 0 → "downed", monster removed from turn order. check_combat_end() unchanged.'

    Returns:
        'player_victory'  — all enemies are downed.
        'player_defeat'   — player is downed (companions still alive = retreat, not death).
        None              — combat continues.
    """
    enemies    = combat_state.get("enemies", [])
    player_c   = combat_state.get("player_combatant", {})

    all_enemies_down = all(e.get("hp", {}).get("current", 0) <= 0 for e in enemies)
    player_down      = player_c.get("hp", {}).get("current", 0) <= 0

    if all_enemies_down:
        combat_state["status"]  = "ended"
        combat_state["outcome"] = "player_victory"
        logger.debug("check_combat_end: player_victory — all enemies downed.")
        return "player_victory"

    if player_down:
        combat_state["status"]  = "ended"
        combat_state["outcome"] = "player_defeat"
        logger.debug("check_combat_end: player_defeat — player downed.")
        return "player_defeat"

    return None


# ════════════════════════════════════════════════════════════════════════════════
# ROUND ORCHESTRATION (spec Section 9b-3)
# ════════════════════════════════════════════════════════════════════════════════

def resolve_round(
    combat_state: Dict[str, Any],
    player_attack_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Orchestrate a full combat round in initiative order and return a complete
    round summary dict for the narrative LLM.

    Design (spec Section 9b-3):
    - Python resolves the WHOLE round first, then assembles ONE combined result block.
    - The player's action is supplied by the caller via `player_attack_result` (driven
      by human input upstream); the player slot in turn_order is skipped by this function.
    - Enemy / companion actions are resolved by resolve_enemy_turn / resolve_companion_turn.
    - Live list references are used directly (no copies) so HP changes from earlier turns
      in the same round affect target selection for later turns.
    - Results are appended to combat_state["round_log"] before returning.
    - Conditions are ticked at end of round.
    - round counter increments AFTER resolution (so the narration block correctly names
      the round that just happened).
    - check_combat_end() is called last; its outcome is included in the return dict.

    Args:
        combat_state:        live combat_state dict from world_state.
        player_attack_result: result dict from resolve_attack() for the player's chosen
                              action, or None if the player did not attack this turn
                              (e.g. used an item, cast a non-attack spell, etc.).

    Returns:
        {
          "round_num":      int,   # the round that was just resolved
          "significant":    list,  # significant events (spec 9b-4)
          "routine":        list,  # routine hits/misses
          "routine_summary": str,  # canned-template summary of routine events
          "narration_block": str,  # '[System: Round Result]' block for narrative LLM
          "combat_outcome": str | None,  # 'player_victory' | 'player_defeat' | None
        }
    """
    round_num      = combat_state["round"]
    turn_order     = combat_state["turn_order"]
    player_c       = combat_state["player_combatant"]
    enemies        = combat_state["enemies"]     # live reference — mutated in place
    companions     = combat_state["companions"]  # live reference — mutated in place

    round_results: List[Dict[str, Any]] = []

    # ── Include player's action (supplied by caller) ───────────────────────────
    if player_attack_result is not None:
        round_results.append(player_attack_result)

    # ── Resolve all non-player combatants in initiative order ─────────────────
    for cid in turn_order:
        if cid == "player":
            continue  # player already handled above

        # Identify the combatant by id
        combatant = None
        for e in enemies:
            if e["id"] == cid:
                combatant = e
                break
        if combatant is None:
            for comp in companions:
                if comp["id"] == cid:
                    combatant = comp
                    break

        if combatant is None:
            logger.debug(f"resolve_round: combatant '{cid}' not found in enemies or companions — skipped.")
            continue

        # Skip already-downed combatants
        if combatant.get("hp", {}).get("current", 0) <= 0:
            continue

        if combatant["side"] == "enemy":
            # Targets: living player + companions
            player_alive = player_c.get("hp", {}).get("current", 0) > 0
            living_targets = ([player_c] if player_alive else []) + [
                comp for comp in companions if comp.get("hp", {}).get("current", 0) > 0
            ]
            result = resolve_enemy_turn(combatant, living_targets)
        else:
            # companion side — targets living enemies
            result = resolve_companion_turn(combatant, enemies)

        round_results.append(result)

    # ── Persist round results into round_log ──────────────────────────────────
    combat_state["round_log"].append(round_results)

    # ── Classify significance ─────────────────────────────────────────────────
    classified   = classify_round_significance(round_results, combat_state)
    significant  = classified["significant"]
    routine      = classified["routine"]

    # ── Tick conditions (end of round) ────────────────────────────────────────
    tick_conditions(combat_state)

    # ── Increment round counter ───────────────────────────────────────────────
    combat_state["round"] += 1

    # ── Build narration artefacts ─────────────────────────────────────────────
    routine_summary = build_routine_summary(routine)
    narration_block = build_round_narration_block(round_num, significant, routine_summary)

    # ── Check for combat end ──────────────────────────────────────────────────
    combat_outcome = check_combat_end(combat_state)

    logger.debug(
        f"resolve_round: round {round_num} done. "
        f"sig={len(significant)}, routine={len(routine)}, outcome={combat_outcome}"
    )

    return {
        "round_num":       round_num,
        "significant":     significant,
        "routine":         routine,
        "routine_summary": routine_summary,
        "narration_block": narration_block,
        "combat_outcome":  combat_outcome,
    }
