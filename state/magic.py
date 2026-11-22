"""
state/magic.py — Spell learning, spell save & attack resolution, and full BG3 spellcasting engine.
Supports:
- Single-target & AoE spells (half damage on save when half_on_save=True)
- Multi-projectile spells (Magic Missile, Scorching Ray, Eldritch Blast + Agonizing Blast)
- Upcasting (higher spell slot expenditure for extra dice or projectiles)
- Buffs & Debuffs (Bless, Shield, Haste, Hex, Hold Person, Sleep, Misty Step, Armor of Agathys, Mirror Image, Spirit Guardians)
- Concentration tracking
- Surface combos (Fire spells ignite grease / create fire surface; Lightning electrifies water)
- Subclass features (Life Domain Disciple of Life, Abjuration Arcane Ward, Evocation Sculpt Spells, Fiend Dark One's Blessing)
"""

import copy
import logging
from typing import Any, Dict, List, Optional

from rules import get_modifier, resolve_spell
from state.equipment import apply_condition_to_state, remove_curse

logger = logging.getLogger("state_manager")


def _get_caster_stat_and_mod(caster: Dict[str, Any]) -> tuple[str, int]:
    """Determine spellcasting ability and modifier based on class."""
    real_p = caster.get("_player_state", caster)
    c_class = str(caster.get("class", real_p.get("class", caster.get("class_name", "Wizard")))).strip()
    if c_class in ("Wizard", "Fighter", "Rogue"):
        cast_stat = "INT"
    elif c_class in ("Cleric", "Druid", "Ranger"):
        cast_stat = "WIS"
    else:
        # Bard, Paladin, Warlock, Sorcerer
        cast_stat = "CHA"
    stats = caster.get("stats", real_p.get("stats", {}))
    return cast_stat, get_modifier(stats.get(cast_stat, 10))


def _apply_damage_to_target(target: Dict[str, Any], damage: int, damage_type: str = "") -> int:
    """Apply damage to target respecting temp_hp, raging physical resistance, and sleeping wake-up."""
    if damage <= 0:
        return 0

    t_conds = set(
        c.get("condition") if isinstance(c, dict) else str(c)
        for c in target.get("active_conditions", [])
    )
    if "raging" in t_conds and damage_type in ("slashing", "piercing", "bludgeoning"):
        damage = max(1, damage // 2)

    target_hp = target.setdefault("hp", {"current": 10, "max": 10})
    temp_hp = target_hp.get("temp", target.get("temp_hp", 0))
    dmg_left = damage
    if temp_hp > 0:
        if dmg_left <= temp_hp:
            target_hp["temp"] = temp_hp - dmg_left
            if "temp_hp" in target:
                target["temp_hp"] = target_hp["temp"]
            dmg_left = 0
        else:
            dmg_left -= temp_hp
            target_hp["temp"] = 0
            if "temp_hp" in target:
                target["temp_hp"] = 0

    target_hp["current"] = max(0, target_hp.get("current", 0) - dmg_left)

    # Wake up sleeping targets on taking damage
    if "sleeping" in t_conds:
        target["active_conditions"] = [
            e for e in target.get("active_conditions", [])
            if not (e == "sleeping" or (isinstance(e, dict) and (e.get("condition") == "sleeping" or e.get("name") == "sleeping")))
        ]

    return damage


def learn_spell(
    spell_id: str,
    player_state: Dict[str, Any],
    world_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Learn a spell by adding its spell_id to player_state['known_spells'].
    Validates against static and generated spells via resolve_spell().
    """
    spell = resolve_spell(spell_id, world_state=world_state)
    if not spell:
        logger.warning(f"learn_spell: unknown spell '{spell_id}'.")
        return {"status": "unknown_spell", "spell_id": spell_id}

    known = player_state.setdefault("known_spells", [])
    if spell_id in known:
        return {"status": "already_known", "spell_id": spell_id, "spell": spell}

    known.append(spell_id)
    logger.info(f"learn_spell: {player_state.get('name')} learned '{spell.get('name')}' ({spell_id}).")
    return {"status": "learned", "spell_id": spell_id, "spell": spell}


def resolve_spell_save(
    caster: Dict[str, Any],
    target: Dict[str, Any],
    spell: Dict[str, Any],
    state: Optional[Dict[str, Any]] = None,
    combat_state: Optional[Dict[str, Any]] = None,
    extra_dice: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Spec Section 8b / 18b / PART 4b & BG3 Overhaul:
    Resolve a save-forcing spell (e.g. Vicious Mockery, Sacred Flame, Thunderwave, Fireball, Hold Person).
    Supports half_on_save=True (e.g. Fireball, Burning Hands, Thunderwave, Shatter, Lightning Bolt).
    """
    import state_manager
    caster_prof = caster.get("proficiency_bonus", 2)
    _, cast_mod = _get_caster_stat_and_mod(caster)
    dc = 8 + caster_prof + cast_mod

    save_stat = spell.get("save_stat", "DEX")
    target_stats = target.get("stats", {})
    t_mod = get_modifier(target_stats.get(save_stat, 10))
    t_prof_saves = target.get("proficient_saves", [])
    t_prof_bonus = target.get("proficiency_bonus", 2) if save_stat in t_prof_saves else 0

    t_conds = set(
        c.get("condition") if isinstance(c, dict) else str(c)
        for c in target.get("active_conditions", [])
    )

    roll = state_manager._roll_d20()
    critical = (roll == 20)
    fumble = (roll == 1)

    bless_bonus = state_manager._roll_dice("1d4") if "blessed" in t_conds else 0
    total = roll + t_mod + t_prof_bonus + bless_bonus

    if critical:
        success = True
    elif fumble:
        success = False
    else:
        success = total >= dc

    damage = 0
    condition_applied = None
    eff = spell.get("effect", {})
    dtype = eff.get("damage_type", "")

    if not success:
        if "damage" in eff:
            damage = state_manager._roll_dice(eff["damage"])
            if extra_dice:
                for d_expr in extra_dice:
                    damage += state_manager._roll_dice(d_expr)
            _apply_damage_to_target(target, damage, dtype)

            if damage > 0 and dtype == "lightning" and combat_state:
                surface = combat_state.get("room_surface", {})
                if surface.get("type") == "water":
                    import combat_manager
                    combat_manager.trigger_lightning_surface_reaction(combat_state)

        cond = spell.get("on_fail_extra") or eff.get("condition")
        if cond:
            condition_applied = cond
            dur = eff.get("duration", eff.get("duration_rounds", 2))
            import combat_manager
            if cond in combat_manager.CONDITIONS:
                combat_manager.apply_condition(target, cond, dur)
            else:
                apply_condition_to_state(target, cond, duration_rounds=dur)
    else:
        # Target succeeded on saving throw: check half_on_save
        if spell.get("half_on_save") and "damage" in eff:
            raw_dmg = state_manager._roll_dice(eff["damage"])
            if extra_dice:
                for d_expr in extra_dice:
                    raw_dmg += state_manager._roll_dice(d_expr)
            damage = max(1, raw_dmg // 2)
            _apply_damage_to_target(target, damage, dtype)

    return {
        "attacker_id":       caster.get("id", "player"),
        "attacker_name":     caster.get("name", "Caster"),
        "caster_name":       caster.get("name", "Caster"),
        "target_id":         target.get("id", "unknown"),
        "target_name":       target.get("name", "Target"),
        "spell_name":        spell.get("name", "Spell"),
        "attack_name":       spell.get("name", "Spell"),
        "dc":                dc,
        "roll":              roll,
        "modifier":          t_mod + t_prof_bonus,
        "total":             total,
        "success":           success,
        "critical":          critical,
        "fumble":            fumble,
        "damage":            damage,
        "damage_type":       dtype,
        "condition_applied": condition_applied,
        "target_hp_after":   target.get("hp", {}).get("current", 0),
        "target_downed":     target.get("hp", {}).get("current", 0) <= 0,
    }


def resolve_spell_attack(
    caster: Dict[str, Any],
    target: Dict[str, Any],
    spell: Dict[str, Any],
    combat_state: Optional[Dict[str, Any]] = None,
    num_projectiles: Optional[int] = None,
    extra_dice: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Spec Section 18b / PART 4b & BG3 Overhaul:
    Resolve an attack-roll spell (e.g. Fire Bolt, Guiding Bolt, Magic Missile, Scorching Ray, Eldritch Blast).
    Supports multi-projectile spells and condition modifiers.
    """
    import state_manager
    caster_prof = caster.get("proficiency_bonus", 2)
    _, cast_mod = _get_caster_stat_and_mod(caster)
    to_hit_bonus = caster_prof + cast_mod

    caster_high_ground = caster.get("has_high_ground", False)
    target_high_ground = target.get("has_high_ground", False)

    if caster_high_ground:
        to_hit_bonus += 2

    caster_conds = set(c.get("condition") if isinstance(c, dict) else str(c) for c in caster.get("active_conditions", []))
    target_conds = set(c.get("condition") if isinstance(c, dict) else str(c) for c in target.get("active_conditions", []))

    has_disadvantage = target_high_ground or any(
        c in caster_conds for c in ("poisoned", "restrained", "exhausted", "frightened", "dazed", "disadvantage_next_attack")
    ) or any(c in target_conds for c in ("dodging", "invisible", "hidden"))

    has_advantage = any(
        c in caster_conds for c in ("invisible", "hidden", "vow_of_enmity")
    ) or any(
        c in target_conds for c in ("prone", "restrained", "stunned", "held", "paralyzed", "sleeping", "guiding_bolt_marked")
    )

    if has_advantage and has_disadvantage:
        has_advantage = has_disadvantage = False

    eff = spell.get("effect", {})
    dtype = eff.get("damage_type", "")
    target_ac = target.get("ac", 10)
    if "dazed" in target_conds:
        target_ac -= 1
    if "slowed" in target_conds:
        target_ac -= 2
    if "shielded" in target_conds:
        shield_ac = 5
        for c_entry in target.get("active_conditions", []):
            if isinstance(c_entry, dict) and (c_entry.get("condition") == "shielded" or c_entry.get("name") == "shielded"):
                if "ac_bonus" in c_entry:
                    shield_ac = int(c_entry["ac_bonus"])
                    break
        target_ac += shield_ac
    if "hasted" in target_conds:
        target_ac += 2
    if "mirror_image" in target_conds:
        target_ac += 3

    real_p = caster.get("_player_state", caster)
    c_class = str(caster.get("class", real_p.get("class", ""))).strip().lower()
    c_level = caster.get("level", real_p.get("level", 1))

    # Determine projectile count (Magic Missile = 3, Scorching Ray = 3, Eldritch Blast at Lv5 = 2)
    proj_count = num_projectiles if num_projectiles is not None else spell.get("projectiles", 1)
    if spell.get("name") == "Eldritch Blast" and c_level >= 5 and num_projectiles is None:
        proj_count = 2

    # Agonizing Blast bonus for Warlocks Lv2+ casting Eldritch Blast
    agonizing_bonus = cast_mod if (spell.get("name") == "Eldritch Blast" and c_class == "warlock" and c_level >= 2) else 0
    # Empowered Evocation bonus for Lv5 Evocation Wizards
    evocation_bonus = cast_mod if (c_class == "wizard" and real_p.get("subclass") == "evocation" and c_level >= 5) else 0

    total_damage = 0
    any_hit = False
    any_crit = False
    any_fumble = False
    first_raw_roll = 0
    first_total_to_hit = 0
    ray_results = []

    for p_idx in range(proj_count):
        if eff.get("auto_hit") is True:
            hit = True
            crit = False
            fumble = False
            raw_roll = 20
            total_to_hit = 99
        else:
            if has_advantage:
                raw_roll = max(state_manager._roll_d20(), state_manager._roll_d20())
            elif has_disadvantage:
                raw_roll = min(state_manager._roll_d20(), state_manager._roll_d20())
            else:
                raw_roll = state_manager._roll_d20()

            bless_bonus = state_manager._roll_dice("1d4") if "blessed" in caster_conds else 0
            crit = (raw_roll == 20) or ("held" in target_conds) or ("paralyzed" in target_conds) or ("sleeping" in target_conds)
            fumble = (raw_roll == 1)
            total_to_hit = raw_roll + to_hit_bonus + bless_bonus
            if crit:
                hit = True
            elif fumble:
                hit = False
            else:
                hit = total_to_hit >= target_ac

        if p_idx == 0:
            first_raw_roll = raw_roll
            first_total_to_hit = total_to_hit

        proj_dmg = 0
        if hit:
            any_hit = True
            if crit:
                any_crit = True
            dmg_expr = eff.get("damage", "1d6")
            proj_dmg = state_manager._roll_dice(dmg_expr) + agonizing_bonus
            if p_idx == 0 and evocation_bonus > 0:
                proj_dmg += evocation_bonus
            if p_idx == 0 and extra_dice:
                for d_expr in extra_dice:
                    proj_dmg += state_manager._roll_dice(d_expr)
            if crit:
                proj_dmg += state_manager._roll_dice(dmg_expr)
            # Hex bonus damage (+1d6 necrotic per hit!)
            if "hexed" in target_conds:
                proj_dmg += state_manager._roll_dice("1d6")

            _apply_damage_to_target(target, proj_dmg, dtype)
            total_damage += proj_dmg
        else:
            if fumble and p_idx == 0:
                any_fumble = True

        ray_results.append({"ray": p_idx + 1, "hit": hit, "crit": crit, "damage": proj_dmg, "roll": raw_roll})

    # Consume one-shot conditions
    if "guiding_bolt_marked" in target_conds and any_hit:
        import combat_manager
        combat_manager.remove_condition(target, "guiding_bolt_marked")
    if "disadvantage_next_attack" in caster_conds:
        import combat_manager
        combat_manager.remove_condition(caster, "disadvantage_next_attack")
    if "hidden" in caster_conds:
        import combat_manager
        combat_manager.remove_condition(caster, "hidden")

    condition_applied = None
    if any_hit:
        cond = spell.get("on_hit_extra") or eff.get("condition")
        if cond:
            condition_applied = cond
            import combat_manager
            if cond in combat_manager.CONDITIONS:
                combat_manager.apply_condition(target, cond, 2)
            else:
                apply_condition_to_state(target, cond, duration_rounds=2)

        if total_damage > 0 and dtype == "lightning" and combat_state:
            surface = combat_state.get("room_surface", {})
            if surface.get("type") == "water":
                import combat_manager
                combat_manager.trigger_lightning_surface_reaction(combat_state)

    return {
        "attacker_id":       caster.get("id", "player"),
        "attacker_name":     caster.get("name", "Caster"),
        "caster_name":       caster.get("name", "Caster"),
        "target_id":         target.get("id", "unknown"),
        "target_name":       target.get("name", "Target"),
        "spell_name":        spell.get("name", "Spell"),
        "attack_name":       spell.get("name", "Spell"),
        "raw_roll":          first_raw_roll,
        "to_hit_bonus":      to_hit_bonus,
        "total_to_hit":      first_total_to_hit,
        "target_ac":         target_ac,
        "hit":               any_hit,
        "crit":              any_crit,
        "fumble":            any_fumble,
        "damage":            total_damage,
        "damage_type":       dtype,
        "projectiles_fired": proj_count,
        "ray_results":       ray_results,
        "condition_applied": condition_applied,
        "target_hp_after":   target.get("hp", {}).get("current", 0),
        "target_downed":     target.get("hp", {}).get("current", 0) <= 0,
    }


def cast_spell(
    caster: Dict[str, Any],
    target: Dict[str, Any],
    spell_id: str,
    character_state: Dict[str, Any],
    world_state: Optional[Dict[str, Any]] = None,
    upcast_level: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Cast a spell by spell_id.
    Validates spell, decrements slots (supporting upcast_level), tracks concentration,
    applies subclass bonuses (Life Domain, Abjuration Ward, Fiend Temp HP), triggers surface combos,
    and resolves single-target, multi-ray, AoE, buff, debuff, or heal effects.
    """
    import state_manager
    spell = resolve_spell(spell_id, world_state=world_state)
    if not spell:
        return {"success": False, "reason": f"Unknown spell '{spell_id}'."}

    base_lvl = spell.get("level", 0)
    cast_lvl = upcast_level if (upcast_level is not None and upcast_level >= base_lvl) else base_lvl

    if base_lvl > 0:
        slots = character_state.setdefault("spell_slots", {})
        lvl_str = str(cast_lvl)
        slot_info = slots.get(lvl_str, {"max": 0, "current": 0})
        if slot_info.get("current", 0) <= 0:
            return {"success": False, "reason": f"No level {cast_lvl} spell slots remaining."}
        slot_info["current"] -= 1

    level_diff = max(0, cast_lvl - base_lvl)
    extra_dice: List[str] = []
    if level_diff > 0 and spell.get("upcast_die"):
        for _ in range(level_diff):
            extra_dice.append(spell["upcast_die"])

    num_projectiles = None
    if spell.get("projectiles"):
        num_projectiles = spell["projectiles"] + (level_diff * spell.get("upcast_projectiles", 0))

    # Track Concentration
    if spell.get("concentration"):
        character_state["concentration"] = spell_id
        caster["concentration"] = spell_id

    # Abjuration Wizard: Arcane Ward grants +3 temp HP per spell level (min 2) on casting a spell
    c_class = str(character_state.get("class", "")).strip().lower()
    c_subclass = character_state.get("subclass", "")
    if c_class == "wizard" and c_subclass == "abjuration" and base_lvl > 0:
        ward_hp = max(2, cast_lvl * 3)
        p_hp = character_state.setdefault("hp", {"current": 10, "max": 10})
        p_hp["temp"] = max(p_hp.get("temp", 0), ward_hp)
        caster.setdefault("hp", p_hp)["temp"] = p_hp["temp"]

    combat_state = world_state.get("combat_state") if world_state else None
    s_type = spell.get("type")
    target_type = spell.get("target_type", "single_enemy")
    eff = spell.get("effect", {})

    # 1. AoE Enemies (Burning Hands, Thunderwave, Shatter, Fireball, Lightning Bolt, Arms of Hadar, Spirit Guardians)
    if s_type == "attack_save" and target_type == "aoe_enemies" and combat_state and combat_state.get("enemies"):
        living_enemies = [e for e in combat_state.get("enemies", []) if e.get("hp", {}).get("current", 0) > 0]
        if not living_enemies and target:
            living_enemies = [target]

        aoe_results = []
        total_aoe_dmg = 0
        any_downed = False
        for enemy in living_enemies:
            r = state_manager.resolve_spell_save(
                caster, enemy, spell, character_state, combat_state=combat_state, extra_dice=extra_dice
            )
            aoe_results.append(r)
            total_aoe_dmg += r.get("damage", 0)
            if r.get("target_downed"):
                any_downed = True

        primary_res = aoe_results[0] if aoe_results else {
            "attacker_id": caster.get("id", "player"),
            "attacker_name": caster.get("name", "Caster"),
            "caster_name": caster.get("name", "Caster"),
            "target_id": target.get("id", "aoe") if target else "aoe",
            "target_name": target.get("name", "All Enemies") if target else "All Enemies",
            "spell_name": spell.get("name", "Spell"),
            "attack_name": spell.get("name", "Spell"),
            "damage": 0,
        }
        res = dict(primary_res)
        res["damage"] = total_aoe_dmg
        res["aoe_results"] = aoe_results
        res["targets_hit"] = len(aoe_results)
        res["target_downed"] = any_downed

    elif s_type == "attack_save":
        res = state_manager.resolve_spell_save(
            caster, target, spell, character_state, combat_state=combat_state, extra_dice=extra_dice
        )

    elif s_type == "attack_roll":
        res = state_manager.resolve_spell_attack(
            caster, target, spell, combat_state=combat_state, num_projectiles=num_projectiles, extra_dice=extra_dice
        )

    elif s_type == "heal":
        if target_type == "all_allies" and combat_state:
            allies = [combat_state.get("player_combatant", caster)] + [
                c for c in combat_state.get("companions", []) if c.get("hp", {}).get("current", 0) > 0
            ]
        else:
            allies = [target if target else caster]

        total_healed = 0
        for ally in allies:
            if not isinstance(ally, dict):
                continue
            heal_val = state_manager._roll_dice(eff.get("heal", "1d4+3"))
            for d_expr in extra_dice:
                heal_val += state_manager._roll_dice(d_expr)
            # Life Domain Cleric: Disciple of Life (+2 + spell level HP)
            if c_class == "cleric" and c_subclass == "life_domain":
                heal_val += 2 + cast_lvl

            t_hp = ally.setdefault("hp", {"current": 10, "max": 10})
            t_hp["current"] = min(t_hp.get("max", 10), t_hp.get("current", 0) + heal_val)
            total_healed += heal_val

        main_t = target if target else caster
        res = {
            "attacker_id":     caster.get("id", "player"),
            "attacker_name":   caster.get("name", "Caster"),
            "caster_name":     caster.get("name", "Caster"),
            "target_name":     main_t.get("name", "Target"),
            "spell_name":      spell.get("name", "Spell"),
            "attack_name":     spell.get("name", "Spell"),
            "healed":          total_healed,
            "target_hp_after": main_t.get("hp", {}).get("current", 0),
            "success":         True,
        }

    elif s_type == "buff":
        cond = eff.get("condition")
        ac_bonus_val = int(eff["ac_bonus"]) if isinstance(eff.get("ac_bonus"), (int, float)) else 0
        if not cond and ac_bonus_val > 0:
            cond = "shielded"
        dur = eff.get("duration", eff.get("duration_rounds", 3))
        if target_type == "all_allies" and combat_state:
            buff_targets = [combat_state.get("player_combatant", caster)] + combat_state.get("companions", [])
        else:
            buff_targets = [target if (target and target.get("side") == "player") else caster]

        import combat_manager
        for bt in buff_targets:
            if not isinstance(bt, dict):
                continue
            if cond:
                if cond in combat_manager.CONDITIONS:
                    combat_manager.apply_condition(bt, cond, dur)
                else:
                    apply_condition_to_state(bt, cond, duration_rounds=dur)
                if ac_bonus_val > 0:
                    for c_entry in bt.get("active_conditions", []):
                        if isinstance(c_entry, dict) and (c_entry.get("condition") == cond or c_entry.get("name") == cond):
                            c_entry["ac_bonus"] = ac_bonus_val
                    bt["temp_ac_bonus"] = ac_bonus_val
                if bt.get("id") == "player" or bt is character_state:
                    apply_condition_to_state(character_state, cond, duration_rounds=dur)
                    if ac_bonus_val > 0:
                        for c_entry in character_state.get("active_conditions", []):
                            if isinstance(c_entry, dict) and (c_entry.get("condition") == cond or c_entry.get("name") == cond):
                                c_entry["ac_bonus"] = ac_bonus_val
                        character_state["temp_ac_bonus"] = ac_bonus_val
            if "temp_hp" in eff:
                thp = eff["temp_hp"] + (level_diff * 5)
                bt_hp = bt.setdefault("hp", {"current": 10, "max": 10})
                bt_hp["temp"] = max(bt_hp.get("temp", 0), thp)
                bt["temp_hp"] = bt_hp["temp"]
                if bt.get("id") == "player":
                    character_state.setdefault("hp", bt_hp)["temp"] = bt_hp["temp"]
            if eff.get("grant_high_ground") or eff.get("high_ground"):
                bt["has_high_ground"] = True
                if bt.get("id") == "player":
                    character_state["has_high_ground"] = True

        res = {
            "attacker_id":       caster.get("id", "player"),
            "attacker_name":     caster.get("name", "Caster"),
            "caster_name":       caster.get("name", "Caster"),
            "target_name":       buff_targets[0].get("name", caster.get("name", "Caster")) if buff_targets else caster.get("name", "Caster"),
            "spell_name":        spell.get("name", "Spell"),
            "attack_name":       spell.get("name", "Spell"),
            "condition_applied": cond,
            "ac_bonus_applied":  ac_bonus_val,
            "effect":            eff,
            "success":           True,
        }

    elif s_type == "debuff":
        cond = eff.get("condition")
        dur = eff.get("duration", 3)
        import combat_manager
        if target and cond:
            # Sleep check: if sleep_hp_pool is defined, roll sleep pool
            if "sleep_hp_pool" in eff:
                pool = state_manager._roll_dice(eff["sleep_hp_pool"])
                for d_expr in extra_dice:
                    pool += state_manager._roll_dice(d_expr)
                t_cur_hp = target.get("hp", {}).get("current", 10)
                if t_cur_hp <= pool:
                    combat_manager.apply_condition(target, cond, dur)
                    applied = cond
                else:
                    applied = None
            else:
                if cond in combat_manager.CONDITIONS:
                    combat_manager.apply_condition(target, cond, dur)
                else:
                    apply_condition_to_state(target, cond, duration_rounds=dur)
                applied = cond
        else:
            applied = None

        res = {
            "attacker_id":       caster.get("id", "player"),
            "attacker_name":     caster.get("name", "Caster"),
            "caster_name":       caster.get("name", "Caster"),
            "target_id":         target.get("id", "unknown") if target else "unknown",
            "target_name":       target.get("name", "Target") if target else "Target",
            "spell_name":        spell.get("name", "Spell"),
            "attack_name":       spell.get("name", "Spell"),
            "condition_applied": applied,
            "effect":            eff,
            "success":           True,
        }

    elif s_type == "utility" and eff.get("remove_curse"):
        target_state = target if target and "inventory" in target else character_state
        remove_curse(target_state)
        res = {
            "caster_name": caster.get("name", "Caster"),
            "target_name": target.get("name", "Target") if target else caster.get("name", "Caster"),
            "spell_name":  spell.get("name", "Spell"),
            "attack_name": spell.get("name", "Spell"),
            "success":     True,
            "effect":      "remove_curse",
            "message":     "Curse lifted from target.",
        }
    else:
        res = {
            "caster_name": caster.get("name", "Caster"),
            "spell_name":  spell.get("name", "Spell"),
            "attack_name": spell.get("name", "Spell"),
            "success":     True,
            "effect":      eff,
        }

    # Trigger surface combos for fire spells (Fire Bolt, Burning Hands, Scorching Ray, Fireball)
    if spell.get("surface") and combat_state:
        import combat_manager
        surf_res = combat_manager.apply_surface(combat_state, spell["surface"], duration=3)
        res["surface_reaction"] = surf_res

    # Warlock Fiend Patron: Dark One's Blessing (gain CHA mod + level Temp HP on downing an enemy)
    if res.get("target_downed") and c_class == "warlock" and c_subclass == "the_fiend":
        _, cha_mod = _get_caster_stat_and_mod(character_state)
        blessing_thp = max(1, cha_mod + character_state.get("level", 1))
        p_hp = character_state.setdefault("hp", {"current": 10, "max": 10})
        p_hp["temp"] = max(p_hp.get("temp", 0), blessing_thp)
        caster.setdefault("hp", p_hp)["temp"] = p_hp["temp"]
        res["dark_ones_blessing_temp_hp"] = blessing_thp

    res["action_cost"] = spell.get("action_cost", "action")
    res["upcast_level"] = cast_lvl
    res["success"] = True
    return res
