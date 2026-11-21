"""
combat/actions.py — Attack resolution, Shove action (Phase 11.2), Cooldown-Gated Weapon Actions (Phase 11.3),
and Baldur's Gate 3 Class Actions & Subclass Mechanics.
"""

import logging
from typing import Any, Dict, Optional

import combat_manager
from combat.conditions import (
    CONDITIONS,
    _get_condition_set,
    _get_stat_mod,
    apply_condition,
    remove_condition,
    trigger_lightning_surface_reaction,
)

logger = logging.getLogger("combat_manager")


def resolve_attack(
    attacker: Dict[str, Any],
    target: Dict[str, Any],
    attack: Dict[str, Any],
    combat_state: Optional[Dict[str, Any]] = None,
    use_divine_smite: bool = False,
    use_reckless_attack: bool = False,
    maneuver: Optional[str] = None,
    use_flourish: bool = False,
) -> Dict[str, Any]:
    """
    Resolve a single attack from attacker -> target.
    Python decides hit/miss, damage, crits, fumbles, and conditions.
    Includes BG3 Class & Condition Mechanics:
      - Rogue Sneak Attack (+1d6 to +3d6 automatic when Advantage, hidden, target incapacitated, or ally in combat)
      - Paladin Divine Smite (+2d8 radiant damage using a spell slot)
      - Barbarian Rage (+2 melee damage, physical damage resistance on target) & Reckless Attack (Advantage)
      - Fighter Champion Improved Critical (19-20), Battle Master Maneuvers (Menacing/Trip/Precision +1d8)
      - Bard College of Swords Defensive Flourish (+1d6 damage & +4 AC via shielded/mirror_image)
      - Conditions: Paralyzed/Sleeping auto-crit, Bless (+1d4 to hit), Bardic Inspiration (+1d6), Hex (+1d6 necrotic),
        Divine Favor (+1d4 radiant), Guiding Bolt Marked (Advantage), Vow of Enmity (Advantage), Dodging (Disadvantage).
    """
    attacker_conditions = _get_condition_set(attacker)
    target_conditions = _get_condition_set(target)

    real_attacker = attacker.get("_player_state", attacker)
    a_class = str(attacker.get("class", real_attacker.get("class", ""))).strip().lower()
    a_subclass = str(attacker.get("subclass", real_attacker.get("subclass", ""))).strip().lower()
    a_level = attacker.get("level", real_attacker.get("level", 1))
    a_feats = attacker.get("feats", real_attacker.get("feats", []))

    has_advantage = False
    has_disadvantage = False

    if any(c in target_conditions for c in ("prone", "restrained", "stunned", "held", "paralyzed", "sleeping", "guiding_bolt_marked")):
        has_advantage = True
    if any(c in attacker_conditions for c in ("invisible", "hidden", "vow_of_enmity")) or use_reckless_attack:
        has_advantage = True

    if any(c in attacker_conditions for c in ("poisoned", "restrained", "exhausted", "frightened", "dazed", "disadvantage_next_attack")):
        has_disadvantage = True
    if any(c in target_conditions for c in ("dodging", "invisible", "hidden")):
        has_disadvantage = True

    is_ranged = attack.get("ranged", False) or any(k in attack.get("name", "").lower() for k in ("bow", "crossbow", "ranged"))
    if is_ranged and combat_state and combat_state.get("smoke_active"):
        has_disadvantage = True

    attacker_high_ground = attacker.get("has_high_ground", False)
    target_high_ground = target.get("has_high_ground", False)

    if is_ranged and target_high_ground:
        has_disadvantage = True

    # Rogue Sneak Attack eligibility check (before advantage/disadvantage cancellation if ally present)
    has_active_ally = False
    if combat_state and attacker.get("side") == "player":
        has_active_ally = any(c.get("hp", {}).get("current", 0) > 0 for c in combat_state.get("companions", []))
    sneak_attack_eligible = (
        a_class == "rogue"
        and not has_disadvantage
        and (has_advantage or has_active_ally or any(c in target_conditions for c in ("prone", "restrained", "stunned", "held", "paralyzed", "sleeping", "dazed")))
    )

    if has_advantage and has_disadvantage:
        has_advantage = has_disadvantage = False

    if has_advantage:
        raw_roll = max(combat_manager._roll_d20(), combat_manager._roll_d20())
    elif has_disadvantage:
        raw_roll = min(combat_manager._roll_d20(), combat_manager._roll_d20())
    else:
        raw_roll = combat_manager._roll_d20()

    attack_bonus = attack.get("attack_bonus", 0)
    if is_ranged and attacker_high_ground:
        attack_bonus += 2

    # Fighting Style: Archery (+2 to ranged attack rolls)
    fighting_style = attacker.get("fighting_style", real_attacker.get("fighting_style"))
    if is_ranged and fighting_style == "archery":
        attack_bonus += 2

    # Bless (+1d4) & Bardic Inspiration (+1d6) & Precision Attack (+1d8)
    extra_to_hit = 0
    if "blessed" in attacker_conditions:
        extra_to_hit += combat_manager.roll_dice("1d4")
    if "bardic_inspired" in attacker_conditions:
        extra_to_hit += combat_manager.roll_dice("1d6")
        remove_condition(attacker, "bardic_inspired")
        if "_player_state" in attacker:
            remove_condition(real_attacker, "bardic_inspired")

    maneuver_used = None
    if maneuver == "precision_attack":
        res_pool = real_attacker.get("class_resources", {}).get("superiority_dice", {})
        if res_pool.get("current", 0) > 0:
            res_pool["current"] -= 1
            extra_to_hit += combat_manager.roll_dice("1d8")
            maneuver_used = "precision_attack"

    total_to_hit = raw_roll + attack_bonus + extra_to_hit
    target_ac = target.get("ac", 10)
    if "dazed" in target_conditions:
        target_ac -= 1
    if "slowed" in target_conditions:
        target_ac -= 2
    if "shielded" in target_conditions:
        target_ac += 5
    if "hasted" in target_conditions:
        target_ac += 2
    if "mirror_image" in target_conditions:
        target_ac += 3

    # Champion Fighter (Lv3+) or Assassin Rogue vs Surprised/Sleeping/Paralyzed
    crit_threshold = 19 if (a_class == "fighter" and a_subclass == "champion" and a_level >= 3) else 20
    crit = (raw_roll >= crit_threshold) or (
        raw_roll > 1 and ("held" in target_conditions or "paralyzed" in target_conditions or "sleeping" in target_conditions)
    )
    fumble = (raw_roll == 1)
    hit = crit or (not fumble and total_to_hit >= target_ac)

    # Consume one-shot attacker conditions
    if "disadvantage_next_attack" in attacker_conditions:
        remove_condition(attacker, "disadvantage_next_attack")
    if "hidden" in attacker_conditions:
        remove_condition(attacker, "hidden")
        if "_player_state" in attacker:
            remove_condition(real_attacker, "hidden")
    if "invisible" in attacker_conditions:
        remove_condition(attacker, "invisible")
        if "_player_state" in attacker:
            remove_condition(real_attacker, "invisible")

    damage = 0
    condition_applied = None
    sneak_attack_damage = 0
    smite_damage = 0
    maneuver_damage = 0

    if hit:
        if "guiding_bolt_marked" in target_conditions:
            remove_condition(target, "guiding_bolt_marked")

        damage_expr = attack.get("damage", "1d4")
        try:
            damage = combat_manager.roll_dice(damage_expr)
        except ValueError:
            damage = 1

        if crit:
            try:
                damage += combat_manager.roll_dice(damage_expr)
            except ValueError:
                damage += 1

        # Barbarian Rage bonus (+2 melee damage)
        if not is_ranged and "raging" in attacker_conditions:
            damage += 2

        # Feats: Great Weapon Master (+5 melee) / Sharpshooter (+5 ranged)
        if not is_ranged and "great_weapon_master" in a_feats:
            damage += 5
        if is_ranged and "sharpshooter" in a_feats:
            damage += 5

        # Rogue Sneak Attack (+1d6 at Lv1-2, +2d6 at Lv3-4, +3d6 at Lv5)
        if sneak_attack_eligible:
            sa_dice = (a_level + 1) // 2
            sa_expr = f"{sa_dice * (2 if crit else 1)}d6"
            sneak_attack_damage = combat_manager.roll_dice(sa_expr)
            damage += sneak_attack_damage

        # Paladin Divine Smite (+2d8 radiant damage using a spell slot)
        if use_divine_smite and not is_ranged:
            slots = real_attacker.get("spell_slots", attacker.get("spell_slots", {}))
            used_slot_lvl = None
            for s_lvl in ("1", "2", "3"):
                if slots.get(s_lvl, {}).get("current", 0) > 0:
                    slots[s_lvl]["current"] -= 1
                    used_slot_lvl = int(s_lvl)
                    break
            if used_slot_lvl:
                smite_dice = (1 + used_slot_lvl) * (2 if crit else 1)
                smite_damage = combat_manager.roll_dice(f"{smite_dice}d8")
                damage += smite_damage

        # Fighter Battle Master Maneuvers (Menacing Attack / Trip Attack)
        if maneuver in ("menacing_attack", "trip_attack"):
            res_pool = real_attacker.get("class_resources", {}).get("superiority_dice", {})
            if res_pool.get("current", 0) > 0:
                res_pool["current"] -= 1
                maneuver_used = maneuver
                maneuver_damage = combat_manager.roll_dice("2d8" if crit else "1d8")
                damage += maneuver_damage
                m_cond = "frightened" if maneuver == "menacing_attack" else "prone"
                apply_condition(target, m_cond, duration=2)
                condition_applied = m_cond

        # Bard College of Swords: Defensive Flourish (+1d6 damage & +3 AC via mirror_image)
        if use_flourish and a_class == "bard":
            bi = real_attacker.get("bardic_inspiration", {})
            if bi.get("current", 0) > 0:
                bi["current"] -= 1
                damage += combat_manager.roll_dice("1d6")
                apply_condition(attacker, "mirror_image", duration=1)

        # Divine Favor (+1d4 radiant) & Hex (+1d6 necrotic)
        if "divine_favor" in attacker_conditions:
            damage += combat_manager.roll_dice("1d4")
        if "hexed" in target_conditions:
            damage += combat_manager.roll_dice("1d6")

        # Target physical damage resistance if Raging
        dtype = str(attack.get("damage_type", "")).lower()
        if "raging" in target_conditions and dtype in ("slashing", "piercing", "bludgeoning"):
            damage = max(1, damage // 2)

        target_hp = target.setdefault("hp", {"current": 1, "max": 1})
        temp_hp = target_hp.get("temp", target.get("temp_hp", 0))
        damage_to_apply = damage
        if temp_hp > 0:
            if damage_to_apply <= temp_hp:
                target_hp["temp"] = temp_hp - damage_to_apply
                if "temp_hp" in target:
                    target["temp_hp"] = target_hp["temp"]
                damage_to_apply = 0
            else:
                damage_to_apply -= temp_hp
                target_hp["temp"] = 0
                if "temp_hp" in target:
                    target["temp_hp"] = 0
        target_hp["current"] = max(0, target_hp["current"] - damage_to_apply)

        # Wake up sleeping target on taking damage
        if "sleeping" in target_conditions and damage > 0:
            remove_condition(target, "sleeping")

        cond = attack.get("applies_condition")
        if cond and cond in CONDITIONS:
            apply_condition(target, cond, duration=2)
            condition_applied = cond

        if damage > 0 and attack.get("damage_type") == "lightning" and combat_state:
            surface = combat_state.get("room_surface", {})
            if surface.get("type") == "water":
                trigger_lightning_surface_reaction(combat_state)

    target_downed = target.get("hp", {}).get("current", 1) <= 0

    result = {
        "attacker_id":         attacker.get("id", "unknown"),
        "attacker_name":       attacker.get("name", "Unknown"),
        "target_id":           target.get("id", "unknown"),
        "target_name":         target.get("name", "Unknown"),
        "attack_name":         attack.get("name", "Attack"),
        "raw_roll":            raw_roll,
        "total_to_hit":        total_to_hit,
        "target_ac":           target_ac,
        "hit":                 hit,
        "crit":                crit,
        "fumble":              fumble,
        "damage":              damage,
        "damage_type":         attack.get("damage_type", ""),
        "sneak_attack_damage": sneak_attack_damage,
        "smite_damage":        smite_damage,
        "maneuver_used":       maneuver_used,
        "maneuver_damage":     maneuver_damage,
        "condition_applied":   condition_applied,
        "target_hp_after":     target.get("hp", {}).get("current", 0),
        "target_downed":       target_downed,
    }
    logger.debug(
        f"resolve_attack: {attacker.get('name')} → {target.get('name')}: "
        f"roll={raw_roll}+{attack_bonus}={total_to_hit} vs AC {target_ac} → "
        f"{'CRIT' if crit else 'FUMBLE' if fumble else 'HIT' if hit else 'MISS'}, "
        f"dmg={damage}"
    )
    return result


def resolve_class_action(
    actor: Dict[str, Any],
    action_id: str,
    target: Optional[Dict[str, Any]] = None,
    combat_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Resolve a Baldur's Gate 3 Class / Subclass Action or Bonus Action:
      - second_wind (Fighter Bonus Action: heal 1d10 + level)
      - action_surge (Fighter Free/Bonus: grants +1 extra Action this turn)
      - cunning_action_hide (Rogue Bonus Action: grants 'hidden' condition -> Advantage on next attack)
      - arcane_recovery (Wizard Bonus Action: restores 1 expended level 1 or 2 spell slot)
      - channel_divinity_preserve_life (Life Cleric Action: heals player & allies for 3 * level HP)
      - channel_divinity_radiance (Light Cleric Action: deals 2d10 + level radiant damage to all enemies)
      - turn_undead (Cleric Action: frightens/dazes undead or all enemies)
      - bardic_inspiration (Bard Bonus Action: grants 'bardic_inspired' +1d6 to self or ally)
      - lay_on_hands (Paladin Action/Bonus: heals 5 * level HP from Lay on Hands pool)
      - vow_of_enmity (Vengeance Paladin Bonus Action: grants 'vow_of_enmity' Advantage for 3 rounds)
      - rage (Barbarian Bonus Action: enters 'raging' for 5 rounds -> +2 melee dmg & physical resistance)
      - frenzied_strike (Berserker Barbarian Bonus Action: make a melee weapon attack as a bonus action)
    """
    real_p = actor.get("_player_state", actor)
    actor_name = actor.get("name", real_p.get("name", "Hero"))
    level = actor.get("level", real_p.get("level", 1))
    resources = real_p.setdefault("class_resources", {})

    if action_id == "second_wind":
        sw = resources.setdefault("second_wind", {"current": 1, "max": 1})
        if sw.get("current", 0) <= 0:
            return {"success": False, "reason": "Second Wind already used (recharges on Short Rest)."}
        sw["current"] -= 1
        heal_amt = combat_manager.roll_dice("1d10") + level
        hp = actor.setdefault("hp", real_p.setdefault("hp", {"current": 10, "max": 10}))
        hp["current"] = min(hp.get("max", 10), hp.get("current", 0) + heal_amt)
        return {
            "success": True,
            "action_type": "class_action",
            "class_action_id": "second_wind",
            "action_cost": "bonus_action",
            "attacker_id": actor.get("id", "player"),
            "attacker_name": actor_name,
            "healed": heal_amt,
            "narration": f"{actor_name} uses Second Wind, recovering {heal_amt} HP!",
        }

    elif action_id == "action_surge":
        as_pool = resources.setdefault("action_surge", {"current": 1, "max": 1})
        if as_pool.get("current", 0) <= 0:
            return {"success": False, "reason": "Action Surge already used (recharges on Short Rest)."}
        as_pool["current"] -= 1
        if combat_state is not None:
            combat_state["extra_actions_granted"] = combat_state.get("extra_actions_granted", 0) + 1
        return {
            "success": True,
            "action_type": "class_action",
            "class_action_id": "action_surge",
            "action_cost": "free",
            "attacker_id": actor.get("id", "player"),
            "attacker_name": actor_name,
            "extra_action": True,
            "narration": f"{actor_name} surges with adrenaline (Action Surge), gaining an extra Action this turn!",
        }

    elif action_id == "cunning_action_hide":
        apply_condition(actor, "hidden", duration=2)
        if "_player_state" in actor:
            apply_condition(real_p, "hidden", duration=2)
        return {
            "success": True,
            "action_type": "class_action",
            "class_action_id": "cunning_action_hide",
            "action_cost": "bonus_action",
            "attacker_id": actor.get("id", "player"),
            "attacker_name": actor_name,
            "condition_applied": "hidden",
            "narration": f"{actor_name} slips into the shadows (Cunning Action: Hide), gaining Advantage on their next attack!",
        }

    elif action_id == "arcane_recovery":
        ar = resources.setdefault("arcane_recovery", {"current": 1, "max": 1})
        if ar.get("current", 0) <= 0:
            return {"success": False, "reason": "Arcane Recovery already used today (recharges on Long Rest)."}
        slots = real_p.get("spell_slots", actor.get("spell_slots", {}))
        restored_tier = None
        for tier in ("2", "1"):
            if tier in slots and slots[tier].get("current", 0) < slots[tier].get("max", 0):
                slots[tier]["current"] += 1
                restored_tier = tier
                break
        if not restored_tier:
            return {"success": False, "reason": "All spell slots are already at maximum."}
        ar["current"] -= 1
        return {
            "success": True,
            "action_type": "class_action",
            "class_action_id": "arcane_recovery",
            "action_cost": "bonus_action",
            "attacker_id": actor.get("id", "player"),
            "attacker_name": actor_name,
            "restored_slot_level": int(restored_tier),
            "narration": f"{actor_name} channels Arcane Recovery, restoring a Level {restored_tier} spell slot!",
        }

    elif action_id == "channel_divinity_preserve_life":
        cd = resources.setdefault("channel_divinity", {"current": 1, "max": 1})
        if cd.get("current", 0) <= 0:
            return {"success": False, "reason": "No Channel Divinity charges remaining (recharges on Short Rest)."}
        cd["current"] -= 1
        heal_amt = 3 * level
        allies = [actor]
        if combat_state:
            allies.extend([c for c in combat_state.get("companions", []) if c.get("hp", {}).get("current", 0) > 0])
        for ally in allies:
            a_hp = ally.setdefault("hp", {"current": 10, "max": 10})
            a_hp["current"] = min(a_hp.get("max", 10), a_hp.get("current", 0) + heal_amt)
        return {
            "success": True,
            "action_type": "class_action",
            "class_action_id": "channel_divinity_preserve_life",
            "action_cost": "action",
            "attacker_id": actor.get("id", "player"),
            "attacker_name": actor_name,
            "healed": heal_amt,
            "narration": f"{actor_name} invokes Channel Divinity: Preserve Life, healing the party for {heal_amt} HP!",
        }

    elif action_id == "channel_divinity_radiance":
        cd = resources.setdefault("channel_divinity", {"current": 1, "max": 1})
        if cd.get("current", 0) <= 0:
            return {"success": False, "reason": "No Channel Divinity charges remaining (recharges on Short Rest)."}
        cd["current"] -= 1
        enemies = [e for e in (combat_state.get("enemies", []) if combat_state else ([target] if target else [])) if e and e.get("hp", {}).get("current", 0) > 0]
        total_dmg = 0
        any_downed = False
        for e in enemies:
            dmg = combat_manager.roll_dice("2d10") + level
            e_hp = e.setdefault("hp", {"current": 10, "max": 10})
            e_hp["current"] = max(0, e_hp["current"] - dmg)
            total_dmg += dmg
            if e_hp["current"] <= 0:
                any_downed = True
        return {
            "success": True,
            "action_type": "class_action",
            "class_action_id": "channel_divinity_radiance",
            "action_cost": "action",
            "attacker_id": actor.get("id", "player"),
            "attacker_name": actor_name,
            "damage": total_dmg,
            "target_downed": any_downed,
            "narration": f"{actor_name} unleashes Radiance of the Dawn, searing all enemies for {total_dmg} total radiant damage!",
        }

    elif action_id == "bardic_inspiration":
        bi = real_p.setdefault("bardic_inspiration", {"current": 1, "max": 1})
        if bi.get("current", 0) <= 0:
            return {"success": False, "reason": "No Bardic Inspiration charges remaining."}
        bi["current"] -= 1
        t = target if (target and target.get("side") == "player") else actor
        apply_condition(t, "bardic_inspired", duration=3)
        if t.get("id") == "player" and "_player_state" in actor:
            apply_condition(real_p, "bardic_inspired", duration=3)
        return {
            "success": True,
            "action_type": "class_action",
            "class_action_id": "bardic_inspiration",
            "action_cost": "bonus_action",
            "attacker_id": actor.get("id", "player"),
            "attacker_name": actor_name,
            "condition_applied": "bardic_inspired",
            "narration": f"{actor_name} inspires {t.get('name', actor_name)} with Bardic Inspiration (+1d6 to next attack roll)!",
        }

    elif action_id == "lay_on_hands":
        loh = resources.setdefault("lay_on_hands", {"current": level * 5, "max": level * 5})
        if loh.get("current", 0) <= 0:
            return {"success": False, "reason": "Lay on Hands pool is depleted (recharges on Long Rest)."}
        heal_amt = loh["current"]
        loh["current"] = 0
        t = target if (target and target.get("side") == "player") else actor
        t_hp = t.setdefault("hp", {"current": 10, "max": 10})
        t_hp["current"] = min(t_hp.get("max", 10), t_hp.get("current", 0) + heal_amt)
        return {
            "success": True,
            "action_type": "class_action",
            "class_action_id": "lay_on_hands",
            "action_cost": "action",
            "attacker_id": actor.get("id", "player"),
            "attacker_name": actor_name,
            "healed": heal_amt,
            "narration": f"{actor_name} channels Lay on Hands, restoring {heal_amt} HP to {t.get('name', actor_name)}!",
        }

    elif action_id == "vow_of_enmity":
        co = resources.setdefault("channel_oath", {"current": 1, "max": 1})
        if co.get("current", 0) <= 0:
            return {"success": False, "reason": "No Channel Oath charges remaining."}
        co["current"] -= 1
        apply_condition(actor, "vow_of_enmity", duration=3)
        if "_player_state" in actor:
            apply_condition(real_p, "vow_of_enmity", duration=3)
        return {
            "success": True,
            "action_type": "class_action",
            "class_action_id": "vow_of_enmity",
            "action_cost": "bonus_action",
            "attacker_id": actor.get("id", "player"),
            "attacker_name": actor_name,
            "condition_applied": "vow_of_enmity",
            "narration": f"{actor_name} swears a Vow of Enmity, gaining Advantage on attack rolls for 3 rounds!",
        }

    elif action_id == "rage":
        rc = resources.setdefault("rage_charges", {"current": 2, "max": 2})
        if rc.get("current", 0) <= 0:
            return {"success": False, "reason": "No Rage charges remaining (recharges on Long Rest)."}
        rc["current"] -= 1
        apply_condition(actor, "raging", duration=5)
        if "_player_state" in actor:
            apply_condition(real_p, "raging", duration=5)
        return {
            "success": True,
            "action_type": "class_action",
            "class_action_id": "rage",
            "action_cost": "bonus_action",
            "attacker_id": actor.get("id", "player"),
            "attacker_name": actor_name,
            "condition_applied": "raging",
            "narration": f"{actor_name} enters a Barbarian RAGE (+2 melee damage, resistance to physical damage)!",
        }

    elif action_id == "frenzied_strike":
        if "raging" not in _get_condition_set(actor):
            return {"success": False, "reason": "Frenzied Strike requires Raging to be active."}
        if not target:
            return {"success": False, "reason": "No target selected for Frenzied Strike."}
        attacks = actor.get("attacks", [{"name": "Greataxe", "attack_bonus": 5, "damage": "1d12+3", "damage_type": "slashing"}])
        atk_res = resolve_attack(actor, target, attacks[0], combat_state=combat_state)
        atk_res["action_type"] = "class_action"
        atk_res["class_action_id"] = "frenzied_strike"
        atk_res["action_cost"] = "bonus_action"
        atk_res["success"] = True
        atk_res["narration"] = (
            f"{actor_name} lashes out with a Frenzied Strike against {target.get('name')} for {atk_res['damage']} damage!"
            if atk_res["hit"] else f"{actor_name}'s Frenzied Strike against {target.get('name')} misses!"
        )
        return atk_res

    return {"success": False, "reason": f"Unknown class action '{action_id}'."}


def _get_skill_mod(combatant: Dict[str, Any], skill_name: str) -> int:
    """Return total skill modifier for a combatant (ability mod + proficiency bonus if proficient)."""
    stat = "STR" if skill_name == "Athletics" else "DEX" if skill_name == "Acrobatics" else "STR"
    mod = _get_stat_mod(combatant, stat)
    prof_skills = combatant.get("proficient_skills", [])
    if not prof_skills and "_player_state" in combatant:
        prof_skills = combatant["_player_state"].get("proficient_skills", [])
    if skill_name in prof_skills:
        mod += combatant.get("proficiency_bonus", 2)
    return mod


def resolve_shove(
    attacker: Dict[str, Any],
    target: Dict[str, Any],
    room_hazards: Optional[Any] = None,
    combat_state: Optional[Dict[str, Any]] = None,
    shove_type: str = "push",
) -> Dict[str, Any]:
    """Resolve a Shove action (Module A §2 / Phase 11.2)."""
    if isinstance(room_hazards, dict) and "turn_order" in room_hazards:
        combat_state = room_hazards
        room_hazards = combat_state.get("room_hazards", [])
    elif isinstance(room_hazards, str) and room_hazards in ("push", "prone"):
        shove_type = room_hazards
        room_hazards = combat_state.get("room_hazards", []) if combat_state else []
    elif isinstance(room_hazards, str):
        room_hazards = [room_hazards]
    elif room_hazards is None:
        if combat_state and "room_hazards" in combat_state:
            room_hazards = combat_state.get("room_hazards", [])
        else:
            room_hazards = []
    else:
        room_hazards = list(room_hazards)

    attacker_conds = _get_condition_set(attacker)
    target_conds = _get_condition_set(target)

    attacker_disadv = any(c in attacker_conds for c in ("poisoned", "exhausted", "restrained", "dazed"))
    attacker_adv = "raging" in attacker_conds
    if attacker_adv and not attacker_disadv:
        attacker_roll = max(combat_manager._roll_d20(), combat_manager._roll_d20())
    elif attacker_disadv and not attacker_adv:
        attacker_roll = min(combat_manager._roll_d20(), combat_manager._roll_d20())
    else:
        attacker_roll = combat_manager._roll_d20()

    attacker_mod = _get_skill_mod(attacker, "Athletics")
    attacker_total = attacker_roll + attacker_mod

    target_ath_mod = _get_skill_mod(target, "Athletics")
    target_acro_mod = _get_skill_mod(target, "Acrobatics")
    if target_acro_mod > target_ath_mod:
        target_skill = "Acrobatics"
        target_mod = target_acro_mod
    else:
        target_skill = "Athletics"
        target_mod = target_ath_mod

    target_disadv = any(c in target_conds for c in ("poisoned", "exhausted", "restrained"))
    target_adv = False
    if target_adv and not target_disadv:
        target_roll = max(combat_manager._roll_d20(), combat_manager._roll_d20())
    elif target_disadv and not target_adv:
        target_roll = min(combat_manager._roll_d20(), combat_manager._roll_d20())
    else:
        target_roll = combat_manager._roll_d20()

    target_total = target_roll + target_mod

    attacker_name = attacker.get("name", "Attacker")
    target_name = target.get("name", "Target")

    if attacker_total <= target_total:
        logger.debug(
            f"resolve_shove: FAIL {attacker_name} ({attacker_total}) vs {target_name} ({target_total})"
        )
        return {
            "success":           False,
            "action_type":       "shove",
            "shove_type":        shove_type,
            "attacker_id":       attacker.get("id", "player"),
            "attacker_name":     attacker_name,
            "target_id":         target.get("id", "target"),
            "target_name":       target_name,
            "attacker_roll":     attacker_roll,
            "attacker_mod":      attacker_mod,
            "attacker_total":    attacker_total,
            "target_skill":      target_skill,
            "target_roll":       target_roll,
            "target_mod":        target_mod,
            "target_total":      target_total,
            "damage":            0,
            "damage_type":       "",
            "condition_applied": None,
            "hazard_triggered":  None,
            "hazard_saved":      False,
            "target_hp_after":   target.get("hp", {}).get("current", 0),
            "target_downed":     target.get("hp", {}).get("current", 0) <= 0,
            "narration":         f"{attacker_name} attempts to shove {target_name}, but {target_name} holds their ground ({target_total} vs {attacker_total}).",
        }

    damage = 0
    damage_type = ""
    condition_applied = None
    hazard_triggered = None
    hazard_saved = False

    if shove_type == "prone":
        apply_condition(target, "prone", duration=2)
        condition_applied = "prone"
        if target.get("has_high_ground"):
            target["has_high_ground"] = False
        narration = f"{attacker_name} forcefully shoves {target_name} to the ground, knocking them prone!"
    else:
        if target.get("has_high_ground"):
            target["has_high_ground"] = False

        if "near_chasm" in room_hazards:
            hazard = "near_chasm"
        elif "acid_pool" in room_hazards:
            hazard = "acid_pool"
        else:
            hazard = None

        if hazard:
            hazard_triggered = hazard
            dex_mod = _get_stat_mod(target, "DEX")
            prof_saves = target.get("saving_throw_proficiencies", target.get("proficient_saves", []))
            if not prof_saves and "_player_state" in target:
                prof_saves = target["_player_state"].get("saving_throw_proficiencies", [])
            if "DEX" in prof_saves:
                dex_mod += target.get("proficiency_bonus", 2)

            save_roll = combat_manager._roll_d20()
            save_total = save_roll + dex_mod
            save_passed = (save_roll == 20) or (save_roll != 1 and save_total >= 13)

            if save_passed:
                hazard_saved = True
                narration = (
                    f"{attacker_name} shoves {target_name} toward the {hazard.replace('_', ' ')}, "
                    f"but {target_name} passes a DC 13 Dexterity save ({save_total}) and catches their footing!"
                )
            else:
                hazard_saved = False
                target_size = str(target.get("size", "Medium")).strip().lower()
                is_large_or_larger = target_size in ("large", "huge", "gargantuan")

                if hazard == "near_chasm":
                    if is_large_or_larger:
                        damage = combat_manager.roll_dice("3d6")
                        damage_type = "bludgeoning"
                        target_hp = target.setdefault("hp", {"current": 1, "max": 1})
                        target_hp["current"] = max(0, target_hp["current"] - damage)
                        narration = (
                            f"{attacker_name} shoves the massive {target_name} into the chasm! "
                            f"{target_name} fails the DC 13 DEX save ({save_total}), taking {damage} falling damage!"
                        )
                    else:
                        damage_type = "fall"
                        target_hp = target.setdefault("hp", {"current": 1, "max": 1})
                        damage = target_hp.get("current", 1)
                        target_hp["current"] = 0
                        narration = (
                            f"{attacker_name} shoves {target_name} over the edge into the chasm! "
                            f"{target_name} fails the DC 13 DEX save ({save_total}) and plunges to their death!"
                        )
                elif hazard == "acid_pool":
                    damage = combat_manager.roll_dice("3d6")
                    damage_type = "acid"
                    target_hp = target.setdefault("hp", {"current": 1, "max": 1})
                    target_hp["current"] = max(0, target_hp["current"] - damage)
                    narration = (
                        f"{attacker_name} shoves {target_name} into the bubbling acid pool! "
                        f"{target_name} fails the DC 13 DEX save ({save_total}), taking {damage} acid damage!"
                    )
        else:
            narration = f"{attacker_name} shoves {target_name} back 5 feet!"

    target_downed = target.get("hp", {}).get("current", 0) <= 0

    try:
        import state_manager
        real_attacker = attacker.get("_player_state", attacker)
        state_manager.check_inspiration_trigger(real_attacker, "shove_assist", {"success": True})
    except Exception as e:
        logger.debug(f"resolve_shove: inspiration trigger check failed: {e}")

    return {
        "success":           True,
        "action_type":       "shove",
        "shove_type":        shove_type,
        "attacker_id":       attacker.get("id", "player"),
        "attacker_name":     attacker_name,
        "target_id":         target.get("id", "target"),
        "target_name":       target_name,
        "attacker_roll":     attacker_roll,
        "attacker_mod":      attacker_mod,
        "attacker_total":    attacker_total,
        "target_skill":      target_skill,
        "target_roll":       target_roll,
        "target_mod":        target_mod,
        "target_total":      target_total,
        "damage":            damage,
        "damage_type":       damage_type,
        "condition_applied": condition_applied,
        "hazard_triggered":  hazard_triggered,
        "hazard_saved":      hazard_saved,
        "target_hp_after":   target.get("hp", {}).get("current", 0),
        "target_downed":     target_downed,
        "narration":         narration,
    }


def resolve_combat_parley(
    actor: Dict[str, Any],
    target: Dict[str, Any],
    skill: str = "Persuasion",
    speech_text: str = "",
    combat_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Resolve a Tabletop D&D Combat Parley / Social Action (Persuasion, Intimidation, or Deception)
    during active combat!
      - Persuasion vs Enemy WIS save/insight (DC = 10 + enemy WIS mod + challenge bonus):
          On critical or high roll (total >= DC + 3 or target HP <= 50%): enemy surrenders / flees (HP -> 0, peaceful resolution)!
          On normal success: enemy hesitates and is Charmed & Dazed for 2 rounds!
      - Intimidation vs Enemy WIS (DC = 10 + enemy WIS mod):
          On critical or target HP <= 35%: enemy panics and flees combat (HP -> 0)!
          On normal success: enemy is Frightened & Dazed for 2 rounds!
      - Deception vs Enemy WIS/INT (DC = 10 + enemy WIS mod):
          On success: enemy is tricked, becoming Dazed & Prone (off-balance) for 2 rounds, and actor gains Hidden (Advantage)!
    """
    import state_manager
    real_p = actor.get("_player_state", actor)
    actor_name = actor.get("name", real_p.get("name", "Hero"))
    target_name = target.get("name", "Enemy")

    skill_norm = (skill or "Persuasion").strip().title()
    if skill_norm not in ("Persuasion", "Intimidation", "Deception"):
        skill_norm = "Persuasion"

    cha_mod = _get_stat_mod(actor, "CHA")
    prof_skills = actor.get("proficient_skills", real_p.get("proficient_skills", []))
    is_prof = any(s.lower() == skill_norm.lower() for s in prof_skills)
    prof_bonus = actor.get("proficiency_bonus", real_p.get("proficiency_bonus", 2)) if is_prof else 0

    a_conds = _get_condition_set(actor)
    bless_bonus = combat_manager.roll_dice("1d4") if "blessed" in a_conds else 0
    bi_bonus = 0
    if "bardic_inspired" in a_conds:
        bi_bonus = combat_manager.roll_dice("1d6")
        remove_condition(actor, "bardic_inspired")

    raw_roll = combat_manager._roll_d20()
    total_mod = cha_mod + prof_bonus + bless_bonus + bi_bonus
    total_roll = raw_roll + total_mod

    enemy_wis_mod = _get_stat_mod(target, "WIS")
    e_hp = target.get("hp", {"current": 10, "max": 10})
    hp_ratio = e_hp.get("current", 10) / max(1, e_hp.get("max", 10))
    # Wounded enemies are easier to persuade or intimidate!
    wounded_penalty = -2 if hp_ratio <= 0.5 else 0
    dc = max(8, 12 + enemy_wis_mod + wounded_penalty)

    crit = (raw_roll == 20)
    fumble = (raw_roll == 1)
    success = crit or (not fumble and total_roll >= dc)

    speech_snippet = f' ("{speech_text}")' if speech_text else ""
    condition_applied = None
    enemy_surrendered = False

    if not success:
        narration = (
            f"{actor_name} attempts to {skill_norm.lower()} {target_name}{speech_snippet} "
            f"(rolled {raw_roll}{total_mod:+d}={total_roll} vs DC {dc}), but {target_name} refuses to listen!"
        )
    else:
        # Check Background Inspiration Trigger for social success!
        try:
            state_manager.check_inspiration_trigger(real_p, skill_norm.lower(), {"success": True})
        except Exception:
            pass

        if skill_norm == "Persuasion":
            if crit or total_roll >= dc + 3 or hp_ratio <= 0.5:
                e_hp["current"] = 0
                enemy_surrendered = True
                narration = (
                    f"{actor_name} speaks with compelling conviction to {target_name}{speech_snippet} "
                    f"({skill_norm} {total_roll} vs DC {dc})! Moved by the words, {target_name} lowers their weapon and surrenders peacefully!"
                )
            else:
                apply_condition(target, "charmed", duration=2)
                apply_condition(target, "dazed", duration=2)
                condition_applied = "charmed"
                narration = (
                    f"{actor_name}'s persuasive words{speech_snippet} ({total_roll} vs DC {dc}) make {target_name} hesitate in doubt (Charmed & Dazed)!"
                )
        elif skill_norm == "Intimidation":
            if crit or total_roll >= dc + 4 or hp_ratio <= 0.4:
                e_hp["current"] = 0
                enemy_surrendered = True
                narration = (
                    f"{actor_name} unleashes a terrifying threat at {target_name}{speech_snippet} "
                    f"({skill_norm} {total_roll} vs DC {dc})! Broken by terror, {target_name} drops their weapon and flees the battlefield!"
                )
            else:
                apply_condition(target, "frightened", duration=2)
                apply_condition(target, "dazed", duration=2)
                condition_applied = "frightened"
                narration = (
                    f"{actor_name} intimidates {target_name}{speech_snippet} ({total_roll} vs DC {dc}), leaving them shaking with fear (Frightened & Dazed)!"
                )
        else:  # Deception
            apply_condition(target, "dazed", duration=2)
            apply_condition(actor, "hidden", duration=2)
            condition_applied = "dazed"
            narration = (
                f"{actor_name} deceives {target_name} with a clever bluff{speech_snippet} ({total_roll} vs DC {dc})! "
                f"{target_name} is caught completely off-guard (Dazed) while {actor_name} gains the upper hand (Advantage)!"
            )

    return {
        "success":           True,
        "parley_success":    success,
        "action_type":       "parley",
        "skill":             skill_norm,
        "speech_text":       speech_text,
        "attacker_id":       actor.get("id", "player"),
        "attacker_name":     actor_name,
        "target_id":         target.get("id", "enemy"),
        "target_name":       target_name,
        "raw_roll":          raw_roll,
        "modifier":          total_mod,
        "total":             total_roll,
        "dc":                dc,
        "crit":              crit,
        "fumble":            fumble,
        "damage":            0,
        "condition_applied": condition_applied,
        "enemy_surrendered": enemy_surrendered,
        "target_hp_after":   e_hp.get("current", 0),
        "target_downed":     e_hp.get("current", 0) <= 0,
        "narration":         narration,
    }


def get_weapon_action_info(attack: Dict[str, Any]) -> Dict[str, Any]:
    """Return weapon action metadata for an attack based on its damage_type (Module A §3 / Phase 11.3)."""
    dtype = str(attack.get("damage_type", "slashing")).strip().lower()
    if dtype == "bludgeoning":
        return {
            "name": "Concussive Smash",
            "damage_type": "bludgeoning",
            "description": "On hit, target must pass a CON save (DC 8 + prof + STR mod) or become dazed (-1 AC, disadvantage on attacks) for 1 round.",
        }
    elif dtype == "piercing":
        return {
            "name": "Hamstring",
            "damage_type": "piercing",
            "description": "On hit, target is slowed (movement penalty) for 2 rounds.",
        }
    else:
        return {
            "name": "Cleave",
            "damage_type": "slashing",
            "description": "Hits primary target and deals STR modifier damage to an adjacent enemy.",
        }


def resolve_weapon_action(
    attacker: Dict[str, Any],
    target: Dict[str, Any],
    attack: Dict[str, Any],
    combat_state: Optional[Dict[str, Any]] = None,
    adjacent_target: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve a Cooldown-Gated Weapon Action (Module A §3 / Phase 11.3)."""
    available = attacker.get("weapon_actions_available")
    if available is None and "_player_state" in attacker:
        available = attacker["_player_state"].get("weapon_actions_available", True)
    if available is False:
        logger.debug(f"resolve_weapon_action: {attacker.get('name')} weapon action on cooldown.")
        return {
            "success": False,
            "hit": False,
            "damage": 0,
            "action_type": "weapon_action",
            "reason": "Weapon action is on cooldown. Requires a short rest to recharge.",
            "attacker_id": attacker.get("id"),
            "target_id": target.get("id"),
        }

    attacker["weapon_actions_available"] = False
    if "_player_state" in attacker and isinstance(attacker["_player_state"], dict):
        attacker["_player_state"]["weapon_actions_available"] = False

    action_info = get_weapon_action_info(attack)
    action_name = action_info["name"]

    result = combat_manager.resolve_attack(attacker, target, attack, combat_state=combat_state)
    result["action_type"] = "weapon_action"
    result["weapon_action_name"] = action_name
    result["success"] = True

    attacker_name = attacker.get("name", "Attacker")
    target_name = target.get("name", "Target")

    if not result.get("hit"):
        result["narration"] = f"{attacker_name} attempts {action_name} against {target_name}, but misses!"
        return result

    if action_name == "Concussive Smash":
        prof = attacker.get("proficiency_bonus", 2)
        str_mod = _get_stat_mod(attacker, "STR")
        dc = 8 + prof + str_mod

        target_con_mod = _get_stat_mod(target, "CON")
        target_prof_saves = target.get("saving_throw_proficiencies", target.get("proficient_saves", []))
        if not target_prof_saves and "_player_state" in target:
            target_prof_saves = target["_player_state"].get("saving_throw_proficiencies", [])
        if "CON" in target_prof_saves:
            target_con_mod += target.get("proficiency_bonus", 2)

        con_roll = combat_manager._roll_d20()
        con_total = con_roll + target_con_mod
        save_passed = (con_roll == 20) or (con_roll != 1 and con_total >= dc)

        result["save_dc"] = dc
        result["save_roll"] = con_roll
        result["save_total"] = con_total
        result["save_passed"] = save_passed

        if save_passed:
            result["narration"] = (
                f"{attacker_name} hits {target_name} with a Concussive Smash for {result['damage']} bludgeoning damage, "
                f"but {target_name} withstands the concussion with a DC {dc} CON save ({con_total})!"
            )
        else:
            apply_condition(target, "dazed", duration=1)
            result["condition_applied"] = "dazed"
            result["narration"] = (
                f"{attacker_name} smashes {target_name} with a Concussive Smash for {result['damage']} damage! "
                f"{target_name} fails the DC {dc} CON save ({con_total}) and is DAZED (-1 AC, disadvantage on attacks)!"
            )

    elif action_name == "Cleave":
        cleave_damage = max(1, _get_stat_mod(attacker, "STR"))
        if adjacent_target is None and combat_state:
            for e in combat_state.get("enemies", []):
                if e.get("id") != target.get("id") and e.get("hp", {}).get("current", 0) > 0:
                    adjacent_target = e
                    break

        if adjacent_target:
            adj_hp = adjacent_target.setdefault("hp", {"current": 1, "max": 1})
            adj_hp["current"] = max(0, adj_hp["current"] - cleave_damage)
            adj_downed = adj_hp["current"] <= 0
            result["cleave_target_id"] = adjacent_target.get("id")
            result["cleave_target_name"] = adjacent_target.get("name")
            result["cleave_damage"] = cleave_damage
            result["cleave_target_hp_after"] = adj_hp["current"]
            result["cleave_target_downed"] = adj_downed
            adj_msg = ", downing them!" if adj_downed else f" ({adj_hp['current']} HP remaining)!"
            result["narration"] = (
                f"{attacker_name} strikes {target_name} with a Cleave for {result['damage']} slashing damage, "
                f"and arcs the blade into {adjacent_target.get('name')} for {cleave_damage} damage{adj_msg}"
            )
        else:
            result["narration"] = (
                f"{attacker_name} hits {target_name} with a sweeping Cleave for {result['damage']} slashing damage!"
            )

    elif action_name == "Hamstring":
        apply_condition(target, "slowed", duration=2)
        result["condition_applied"] = "slowed"
        result["narration"] = (
            f"{attacker_name} pierces {target_name} with a Hamstring strike for {result['damage']} piercing damage! "
            f"{target_name} is SLOWED for 2 rounds!"
        )

    return result
