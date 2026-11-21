"""
combat/engine.py — Combat state lifecycle, initiative, AI turn resolution, round significance, and round orchestration.
Includes BG3 class/feat/condition hooks (Alert feat +5 initiative, Paralyzed/Sleeping skip turn, Dueling fighting style,
Multiple player actions per turn via pending_player_actions, and Extra Attack).
"""

import copy
import logging
import random
from typing import Any, Dict, List, Optional

import combat_manager
from rules import _get_item_catalog, _inventory_item_info, get_modifier
from combat.conditions import _get_condition_set, tick_conditions, tick_surface

logger = logging.getLogger("combat_manager")


def _get_dex_mod(combatant: Dict[str, Any]) -> int:
    """Return the DEX modifier (floor((DEX - 10) / 2)) for a combatant."""
    dex = combatant.get("stats", {}).get("DEX", 10)
    return (dex - 10) // 2


def roll_initiative(combatants: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Roll initiative for all combatants and return them sorted highest-first.
    Ties broken by DEX modifier, then by random tiebreak.
    Respects Alert feat (+5 initiative).
    """
    for c in combatants:
        roll = combat_manager._roll_d20()
        dex_m = _get_dex_mod(c)
        real_p = c.get("_player_state", c)
        feats = c.get("feats", real_p.get("feats", []))
        feat_bonus = 5 if "alert" in feats else 0
        c["initiative"] = roll + dex_m + feat_bonus
        c["_initiative_tiebreak"] = random.random()
        logger.debug(f"Initiative: {c.get('name')} rolled {roll}+{dex_m}+{feat_bonus}={c['initiative']}")

    combatants.sort(
        key=lambda c: (c["initiative"], _get_dex_mod(c), c["_initiative_tiebreak"]),
        reverse=True,
    )
    for c in combatants:
        c.pop("_initiative_tiebreak", None)

    return combatants


def _build_combatant_from_catalog(
    monster_id: str,
    instance_index: int = 1,
    world_state: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Build a live combatant dict from the monster catalog entry or world_state["generated_monsters"]."""
    catalog = combat_manager._get_monster_catalog()
    template = catalog.get(monster_id)
    if template is None and world_state and isinstance(world_state, dict):
        template = world_state.get("generated_monsters", {}).get(monster_id)

    if template is None:
        logger.debug(f"_build_combatant_from_catalog: '{monster_id}' not in catalog or generated_monsters.")
        return None

    c = copy.deepcopy(template)
    c["id"] = f"{monster_id}_{instance_index}"
    c["side"] = "enemy"
    c.setdefault("initiative", None)
    c.setdefault("active_conditions", [])
    if isinstance(c.get("hp"), dict):
        c["hp"] = {"current": c["hp"]["max"], "max": c["hp"]["max"]}
    return c


def _player_attacks(player_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build the attack list for the player based on currently-equipped weapons in inventory."""
    catalog = _get_item_catalog()

    stats = player_state.get("stats", {})
    prof = player_state.get("proficiency_bonus", 2)
    str_mod = get_modifier(stats.get("STR", 10))
    dex_mod = get_modifier(stats.get("DEX", 10))
    fighting_style = player_state.get("fighting_style")

    equipped_weapons = []
    for item in player_state.get("inventory", []):
        if isinstance(item, dict) and item.get("equipped") is True:
            item_id = item.get("item_id")
            info = _inventory_item_info(item, catalog)
            if info.get("type") == "weapon":
                effects = info.get("effects", {})
                is_finesse = info.get("finesse", False)
                is_ranged = info.get("ranged", False) or effects.get("ranged", False)
                is_two_handed = info.get("two_handed", False)
                stat_mod = dex_mod if is_ranged else (max(str_mod, dex_mod) if is_finesse else str_mod)
                magic_bonus = effects.get("attack_bonus", 0)
                total_attack_bonus = prof + stat_mod + magic_bonus

                dmg_str = effects.get("damage", "1d4")
                # Dueling fighting style adds +2 damage to one-handed melee weapons if damage expr doesn't already have bonus
                if fighting_style == "dueling" and not is_ranged and not is_two_handed and "+" not in dmg_str and "-" not in dmg_str:
                    total_dmg_mod = stat_mod + 2
                    if total_dmg_mod > 0:
                        dmg_str = f"{dmg_str}+{total_dmg_mod}"
                elif "+" not in dmg_str and "-" not in dmg_str and stat_mod > 0:
                    dmg_str = f"{dmg_str}+{stat_mod}"

                attack = {
                    "name": info.get("name", item_id.capitalize()),
                    "attack_bonus": total_attack_bonus,
                    "damage": dmg_str,
                    "damage_type": effects.get("damage_type", "slashing"),
                    "applies_condition": effects.get("applies_condition", None),
                    "ranged": is_ranged,
                    "two_handed": is_two_handed,
                }
                equipped_weapons.append(attack)

    if equipped_weapons:
        return equipped_weapons

    unarmed_bonus = prof + str_mod
    return [
        {"name": "Unarmed Strike", "attack_bonus": unarmed_bonus, "damage": "1+0",
         "damage_type": "bludgeoning", "applies_condition": None, "ranged": False}
    ]


def start_combat(
    enemy_ids: List[str],
    player_state: Dict[str, Any],
    world_state: Dict[str, Any],
    companion_states: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Instantiate combat_state and write it into world_state["combat_state"].
    Idempotency guard (spec 9b-iv): if combat_state is already active, returns None.
    """
    if world_state.get("combat_state") is not None:
        logger.debug("start_combat: combat already active — no-op (idempotency guard).")
        return None

    id_counts: Dict[str, int] = {}
    enemies: List[Dict[str, Any]] = []
    for mid in enemy_ids:
        id_counts[mid] = id_counts.get(mid, 0) + 1
        c = _build_combatant_from_catalog(mid, id_counts[mid], world_state=world_state)
        if c is None:
            logger.debug(f"start_combat: unknown enemy '{mid}' — skipped.")
            continue
        enemies.append(c)

    if not enemies:
        logger.debug("start_combat: no valid enemies — combat not started.")
        return None

    player_state.setdefault("hp", {"current": 10, "max": 10})
    player_state.setdefault("active_conditions", [])
    player_state.setdefault("death_saves", {"success": 0, "fail": 0})
    player_state.setdefault("status", "normal")
    player_state.setdefault("inventory", [])
    import state_manager
    state_manager._ensure_currency(player_state)
    player_state.setdefault("xp_current", 0)
    player_state.setdefault("level", 1)
    player_state.setdefault("proficiency_bonus", 2)
    player_state.setdefault("spell_slots", {})

    player_c: Dict[str, Any] = {
        "id":                "player",
        "name":              player_state.get("name", "Adventurer"),
        "side":              "player",
        "class":             player_state.get("class", "Fighter"),
        "subclass":          player_state.get("subclass"),
        "fighting_style":    player_state.get("fighting_style"),
        "feats":             player_state.get("feats", []),
        "hp":                player_state["hp"],
        "ac":                player_state.get(
            "ac",
            10 + (player_state.get("stats", {}).get("DEX", 10) - 10) // 2,
        ),
        "stats":             player_state.get("stats", {}),
        "attacks":           _player_attacks(player_state),
        "initiative":        None,
        "active_conditions": player_state["active_conditions"],
        "death_saves":       player_state["death_saves"],
        "inventory":         player_state["inventory"],
        "spell_slots":       player_state["spell_slots"],
        "status":            player_state["status"],
        "currency":          player_state["currency"],
        "gold":              player_state["gold"],
        "xp_current":        player_state["xp_current"],
        "level":             player_state["level"],
        "proficiency_bonus": player_state["proficiency_bonus"],
        "proficient_skills": player_state.get("proficient_skills", []),
        "has_high_ground":   player_state.get("has_high_ground", False),
        "weapon_actions_available": player_state.get("weapon_actions_available", True),
        "_player_state":     player_state,
    }

    companions: List[Dict[str, Any]] = []
    target_comps = companion_states
    if target_comps is None:
        target_comps = [
            c for c in (world_state.get("party", {}).get("companions", []) if isinstance(world_state, dict) else [])
            if isinstance(c, dict) and c.get("hp", {}).get("current", 0) > 0
        ]

    for comp in target_comps:
        comp.setdefault("side", "player")
        comp.setdefault("active_conditions", [])
        comp.setdefault("initiative", None)
        comp.setdefault("death_saves", {"success": 0, "fail": 0})
        comp.setdefault("hp", {"current": 10, "max": 10})
        comp.setdefault("has_high_ground", False)
        companions.append(comp)

    for enemy in enemies:
        enemy.setdefault("has_high_ground", False)

    all_combatants = [player_c] + companions + enemies
    roll_initiative(all_combatants)
    turn_order = [c["id"] for c in all_combatants]

    room_hazards = []
    if world_state:
        import dungeon_manager
        c_room = dungeon_manager.get_current_room(world_state)
        if c_room:
            room_hazards = list(c_room.get("hazards", []))

    combat_state: Dict[str, Any] = {
        "round":                  1,
        "turn_index":             0,
        "turn_order":             turn_order,
        "player_combatant":       player_c,
        "enemies":                enemies,
        "companions":             companions,
        "round_log":              [],
        "status":                 "active",
        "outcome":                None,
        "room_surface":           {"type": None, "duration": 0},
        "smoke_active":           False,
        "smoke_duration":         0,
        "room_hazards":           room_hazards,
        # BG3 Turn Action Economy State
        "actions_used":           0,
        "bonus_actions_used":     0,
        "extra_actions_granted":  0,
        "pending_player_actions": [],
    }

    world_state["combat_state"] = combat_state
    logger.debug(
        f"start_combat: combat started with {len(enemies)} enemy(s). "
        f"Turn order: {turn_order}"
    )
    return combat_state


def sync_player_state(player_c: Dict[str, Any]) -> None:
    """Sync scalar primitive fields from player_c back to underlying real player_state dict."""
    real_state = player_c.get("_player_state")
    if real_state is None or not isinstance(real_state, dict):
        return

    for key in ("currency", "gold", "status", "xp_current", "level", "proficiency_bonus", "ac", "weapon_actions_available"):
        if key in player_c:
            real_state[key] = player_c[key]

    if "inventory" in player_c:
        real_state["inventory"] = player_c["inventory"]


def end_combat(world_state: Dict[str, Any]) -> None:
    """Clear combat_state from world_state, marking combat as over."""
    cs = world_state.get("combat_state")
    if cs is None:
        return
    player_c = cs.get("player_combatant")
    if player_c:
        sync_player_state(player_c)
    world_state["combat_state"] = None
    logger.debug("end_combat: combat_state cleared.")


def resolve_enemy_turn(
    enemy: Dict[str, Any],
    targets: List[Dict[str, Any]],
    combat_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve an enemy's turn: pick a target and attack with the first non-None attack."""
    e_conds = _get_condition_set(enemy)
    for skip_cond in ("stunned", "held", "paralyzed", "sleeping"):
        if skip_cond in e_conds:
            logger.debug(f"resolve_enemy_turn: {enemy.get('name')} is {skip_cond} — skip.")
            return {"attacker_id": enemy["id"], "attacker_name": enemy.get("name", ""),
                    "skipped": True, "reason": skip_cond}

    living_targets = [t for t in targets if t.get("hp", {}).get("current", 0) > 0]
    if not living_targets:
        return {"attacker_id": enemy["id"], "attacker_name": enemy.get("name", ""),
                "skipped": True, "reason": "no_targets"}

    target = living_targets[0]
    attacks = enemy.get("attacks", [])
    if not attacks:
        return {"attacker_id": enemy["id"], "attacker_name": enemy.get("name", ""),
                "skipped": True, "reason": "no_attacks"}

    res = combat_manager.resolve_attack(enemy, target, attacks[0], combat_state=combat_state)
    # Armor of Agathys cold retaliation if target has armor_of_agathys and temp HP
    if res.get("hit") and not attacks[0].get("ranged", False):
        if "armor_of_agathys" in _get_condition_set(target):
            e_hp = enemy.setdefault("hp", {"current": 10, "max": 10})
            e_hp["current"] = max(0, e_hp["current"] - 5)
            res["agathys_retaliation"] = 5
    return res


def resolve_companion_turn(
    companion: Dict[str, Any],
    enemies: List[Dict[str, Any]],
    combat_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve a companion's turn: attack the lowest-HP living enemy."""
    c_conds = _get_condition_set(companion)
    for skip_cond in ("stunned", "held", "paralyzed", "sleeping"):
        if skip_cond in c_conds:
            logger.debug(f"resolve_companion_turn: {companion.get('name')} is {skip_cond} — skip.")
            return {"attacker_id": companion["id"], "attacker_name": companion.get("name", ""),
                    "skipped": True, "reason": skip_cond}

    living_enemies = [e for e in enemies if e.get("hp", {}).get("current", 0) > 0]
    if not living_enemies:
        return {"attacker_id": companion["id"], "attacker_name": companion.get("name", ""),
                "skipped": True, "reason": "no_targets"}

    target = min(living_enemies, key=lambda e: e.get("hp", {}).get("current", 9999))
    attacks = companion.get("attacks", [])
    if not attacks:
        return {"attacker_id": companion["id"], "attacker_name": companion.get("name", ""),
                "skipped": True, "reason": "no_attacks"}

    return combat_manager.resolve_attack(companion, target, attacks[0], combat_state=combat_state)


def classify_round_significance(
    round_results: List[Dict[str, Any]],
    combat_state: Dict[str, Any],
) -> Dict[str, Any]:
    """Split all round events into 'significant' and 'routine' lists."""
    significant = []
    routine = []

    def _is_below_25pct(result: Dict[str, Any]) -> bool:
        target_id = result.get("target_id")
        for pool in ("enemies", "companions"):
            for c in combat_state.get(pool, []):
                if c["id"] == target_id:
                    hp_max = c.get("hp", {}).get("max", 1)
                    hp_cur = result.get("target_hp_after", 0)
                    return hp_cur < hp_max * 0.25
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
            or result.get("action_type") in ("shove", "weapon_action", "class_action", "parley")
            or result.get("cleave_damage", 0) > 0
            or result.get("smite_damage", 0) > 0
            or result.get("sneak_attack_damage", 0) > 0
            or _is_below_25pct(result)
        )
        if is_sig:
            significant.append(result)
        else:
            routine.append(result)

    return {"significant": significant, "routine": routine}


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
    """Build a plain-text summary of routine hits/misses using canned templates."""
    lines = []
    for r in routine_results:
        if r.get("skipped"):
            name = r.get("attacker_name", "Someone")
            reason = r.get("reason", "skipped")
            lines.append(f"{name} skips their turn ({reason}).")
            continue

        if r.get("action_type") in ("shove", "weapon_action", "class_action"):
            lines.append(r.get("narration", ""))
            continue

        attacker = r.get("attacker_name", "?")
        target = r.get("target_name", "?")
        weapon = r.get("attack_name", "attack")
        damage = r.get("damage", 0)
        dtype = r.get("damage_type", "")

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
    """Assemble the '[System: Round Result]' block sent to the narrative LLM."""
    lines = ["[System: Round Result]", f"Round {round_num}:"]

    for r in significant:
        if r.get("action_type") in ("shove", "weapon_action", "class_action", "parley") and r.get("narration"):
            lines.append(f"- {r.get('narration')}")
            continue

        attacker = r.get("attacker_name", "?")
        target = r.get("target_name", "?")
        weapon = r.get("attack_name", "attack")
        damage = r.get("damage", 0)

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


def check_combat_end(combat_state: Dict[str, Any], world_state: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Check whether combat should end ('player_victory', 'player_defeat', or None)."""
    if combat_state.get("status") == "ended":
        return combat_state.get("outcome")

    enemies = combat_state.get("enemies", [])
    player_c = combat_state.get("player_combatant", {})

    all_enemies_down = len(enemies) > 0 and all(e.get("hp", {}).get("current", 0) <= 0 for e in enemies)

    if all_enemies_down:
        combat_state["status"] = "ended"
        combat_state["outcome"] = "player_victory"
        logger.debug("check_combat_end: player_victory — all enemies downed.")

        import state_manager
        total_xp = sum(e.get("xp_value", 50) for e in enemies)
        p_state = player_c.get("_player_state", player_c)

        state_manager.award_xp(total_xp, p_state)
        combat_state["xp_gained"] = total_xp

        leveled_up = False
        while state_manager.check_level_up(p_state):
            state_manager.apply_level_up(p_state)
            leveled_up = True
        combat_state["leveled_up"] = leveled_up

        companions = combat_state.get("companions", [])
        teammate_lost = any(comp.get("hp", {}).get("current", 0) <= 0 for comp in companions)
        if not teammate_lost and p_state.get("hp", {}).get("current", 0) > 0:
            state_manager.check_inspiration_trigger(p_state, "combat_victory_no_casualties", {"success": True})

        if world_state and isinstance(world_state, dict):
            cur_loc = world_state.get("current_location")
            enemy_names = [e.get("monster_id", e.get("id", "")) for e in enemies]
            nb = world_state.get("notice_board", {})
            for entry in list(nb.get("entries", [])):
                if entry.get("type") == "bounty" and entry.get("status") == "accepted":
                    t_room = entry.get("target_room_id")
                    t_mon = entry.get("target_monster")
                    if (not t_room or t_room == cur_loc) and any(t_mon in en for en in enemy_names):
                        import dungeon_manager
                        dungeon_manager.complete_notice_board_quest(world_state, entry["id"], p_state)

        for key in ("currency", "gold", "status", "xp_current", "level", "proficiency_bonus", "ac", "inspiration"):
            if key in p_state:
                player_c[key] = p_state[key]
        sync_player_state(player_c)

        return "player_victory"

    player_fails = player_c.get("death_saves", {}).get("fail", 0)
    has_downed_outcome = combat_state.get("downed_outcome") is not None

    if player_fails >= 3 or has_downed_outcome:
        combat_state["status"] = "ended"
        combat_state["outcome"] = "player_defeat"
        if not has_downed_outcome:
            import state_manager
            downed_res = state_manager.resolve_downed_outcome(player_c, combat_state, world_state)
            combat_state["downed_outcome"] = downed_res
        sync_player_state(player_c)
        logger.debug("check_combat_end: player_defeat — player death save fails >= 3 or downed_outcome present.")
        return "player_defeat"

    return None


def resolve_round(
    combat_state: Dict[str, Any],
    player_attack_result: Optional[Dict[str, Any]] = None,
    world_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Orchestrate a full combat round in initiative order and return a complete round summary dict."""
    if player_attack_result is not None and world_state is None:
        if isinstance(player_attack_result, dict) and ("schema_version" in player_attack_result or "current_location" in player_attack_result):
            world_state = player_attack_result
            player_attack_result = None

    round_num = combat_state["round"]
    turn_order = combat_state["turn_order"]
    player_c = combat_state["player_combatant"]
    enemies = combat_state["enemies"]
    companions = combat_state["companions"]

    round_results: List[Dict[str, Any]] = []

    # Collect any pending player actions (e.g., Bonus Action + Action used in the same BG3 turn)
    pending_actions = combat_state.get("pending_player_actions", [])
    if pending_actions:
        round_results.extend(pending_actions)
        combat_state["pending_player_actions"] = []

    if player_attack_result is not None:
        if isinstance(player_attack_result, list):
            round_results.extend(player_attack_result)
        else:
            round_results.append(player_attack_result)

    for cid in turn_order:
        if cid == "player":
            continue

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

        if combatant.get("hp", {}).get("current", 0) <= 0:
            continue

        if combatant["side"] == "enemy":
            player_alive = player_c.get("hp", {}).get("current", 0) > 0
            living_targets = ([player_c] if player_alive else []) + [
                comp for comp in companions if comp.get("hp", {}).get("current", 0) > 0
            ]
            result = resolve_enemy_turn(combatant, living_targets, combat_state=combat_state)
        else:
            result = resolve_companion_turn(combatant, enemies, combat_state=combat_state)

        round_results.append(result)

    import state_manager
    if player_c.get("hp", {}).get("current", 0) <= 0:
        ds_res = state_manager.resolve_death_save(player_c)
        if ds_res.get("exhausted"):
            downed_res = state_manager.resolve_downed_outcome(player_c, combat_state, world_state)
            combat_state["downed_outcome"] = downed_res

    for comp in companions:
        if comp.get("hp", {}).get("current", 0) <= 0:
            ds_res = state_manager.resolve_death_save(comp)
            if ds_res.get("exhausted"):
                state_manager.resolve_downed_outcome(comp, combat_state, world_state)

    combat_state["round_log"].append(round_results)

    classified = classify_round_significance(round_results, combat_state)
    significant = classified["significant"]
    routine = classified["routine"]

    tick_conditions(combat_state)
    tick_surface(combat_state)

    combat_state["round"] += 1
    # Reset BG3 turn economy counters for the new round
    combat_state["actions_used"] = 0
    combat_state["bonus_actions_used"] = 0
    combat_state["extra_actions_granted"] = 0

    routine_summary = build_routine_summary(routine)
    narration_block = build_round_narration_block(round_num, significant, routine_summary)

    combat_outcome = check_combat_end(combat_state, world_state)
    sync_player_state(player_c)

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
