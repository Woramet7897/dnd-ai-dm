"""
ui/combat_view.py — Combat HUD, BG3 Turn Action Economy (Action + Bonus Action), target selection, and player combat action resolution.
"""

from typing import Any, Dict, List, Optional, Tuple
import streamlit as st

import combat_manager
import dungeon_manager
import llm_handler
import state_manager
from ui.session import auto_save, get_current_active_model, get_display_spell_catalog


def _select_enemy_target(living_enemies: List[Dict[str, Any]], label: str, key: str) -> Dict[str, Any]:
    """
    Render target selectbox for living enemies and return the chosen enemy dict.
    Preserves exact label and widget key.
    """
    target_map = {f"{e.get('name', e.get('id'))} (HP: {e.get('hp', {}).get('current', 0)})": e for e in living_enemies}
    chosen_target_label = st.selectbox(label, list(target_map.keys()), key=key)
    return target_map[chosen_target_label]


def _get_max_actions_and_bonus(player_c: Dict[str, Any], player: Dict[str, Any], cs: Dict[str, Any]) -> Tuple[int, int]:
    """Calculate max Actions and Bonus Actions available to the player in the current turn (BG3 rules)."""
    p_conds = combat_manager._get_condition_set(player_c)
    max_actions = 1 + cs.get("extra_actions_granted", 0)
    if "hasted" in p_conds:
        max_actions += 1

    cls_id = str(player.get("class", player_c.get("class", ""))).strip().lower()
    sub_id = str(player.get("subclass", player_c.get("subclass", ""))).strip().lower()
    lvl = player.get("level", player_c.get("level", 1))

    # Thief Rogue at Lv3+ gets Fast Hands (2 Bonus Actions per turn!)
    max_bonus = 2 if (cls_id == "rogue" and sub_id == "thief" and lvl >= 3) else 1
    return max_actions, max_bonus


def resolve_player_combat_action(
    action_type: str,
    player_c: Dict[str, Any],
    selected_target: Optional[Dict[str, Any]],
    selected_attack: Optional[Dict[str, Any]] = None,
    selected_spell_id: Optional[str] = None,
    selected_shove_type: Optional[str] = "push",
    selected_adj_target: Optional[Dict[str, Any]] = None,
    cs: Optional[Dict[str, Any]] = None,
    player: Optional[Dict[str, Any]] = None,
    world: Optional[Dict[str, Any]] = None,
    use_divine_smite: bool = False,
    use_reckless_attack: bool = False,
    maneuver: Optional[str] = None,
    use_flourish: bool = False,
    upcast_level: Optional[int] = None,
    class_action_id: Optional[str] = None,
    parley_skill: Optional[str] = None,
    parley_speech: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    Build player_attack_result and execute combat_manager.resolve_round().
    Standalone and importable for automated testing (100% backward compatible with test_app_integrations.py).
    Also supports BG3 Divine Smite, Reckless Attack, Battle Master Maneuvers, Flourishes, Upcasting,
    Class Actions, and Extra Attack (Lv5 Martial classes strike twice on a Weapon Attack!).
    """
    player_alive = player_c.get("hp", {}).get("current", 0) > 0 if player_c else False
    player_attack_result = None

    if player_alive and (selected_target or class_action_id or selected_spell_id):
        p_conds = combat_manager._get_condition_set(player_c)
        skip_reason = next((c for c in ("stunned", "held", "paralyzed", "sleeping") if c in p_conds), None)
        if skip_reason:
            player_attack_result = {
                "attacker_id": player_c.get("id", "player"),
                "attacker_name": player_c.get("name", "Player"),
                "skipped": True,
                "reason": skip_reason,
            }
        elif ("Parley" in action_type or "เจรจา" in action_type or action_type == "parley") and selected_target:
            player_attack_result = combat_manager.resolve_combat_parley(
                actor=player_c,
                target=selected_target,
                skill=parley_skill or "Persuasion",
                speech_text=parley_speech or "",
                combat_state=cs,
            )
        elif ("Class Ability" in action_type or action_type == "class_action") and class_action_id:
            ca_res = combat_manager.resolve_class_action(
                actor=player_c,
                action_id=class_action_id,
                target=selected_target,
                combat_state=cs,
            )
            if not ca_res.get("success"):
                return ca_res, {"error": ca_res.get("reason", "Failed to use class ability.")}
            player_attack_result = ca_res
        elif ("Weapon Action" in action_type or action_type == "weapon_action") and selected_attack and selected_target:
            w_res = combat_manager.resolve_weapon_action(
                attacker=player_c,
                target=selected_target,
                attack=selected_attack,
                combat_state=cs,
                adjacent_target=selected_adj_target,
            )
            player_attack_result = w_res
        elif ("Shove" in action_type or action_type == "shove") and selected_target:
            shove_res = combat_manager.resolve_shove(
                attacker=player_c,
                target=selected_target,
                combat_state=cs,
                shove_type=selected_shove_type or "push",
            )
            player_attack_result = shove_res
        elif ("Cast Spell" in action_type or action_type in ("spell", "cast_spell")) and selected_spell_id:
            cast_res = state_manager.cast_spell(
                caster=player_c,
                target=selected_target or player_c,
                spell_id=selected_spell_id,
                character_state=player or {},
                world_state=world,
                upcast_level=upcast_level,
            )
            if not cast_res.get("success"):
                return cast_res, {"error": cast_res.get("reason", "Failed to cast spell.")}
            player_attack_result = cast_res
        elif selected_attack and selected_target:
            player_attack_result = combat_manager.resolve_attack(
                player_c,
                selected_target,
                selected_attack,
                combat_state=cs,
                use_divine_smite=use_divine_smite,
                use_reckless_attack=use_reckless_attack,
                maneuver=maneuver,
                use_flourish=use_flourish,
            )
            # BG3 Level 5 Extra Attack for Fighter, Paladin, Barbarian, College of Swords Bard
            real_p = player or player_c.get("_player_state", player_c)
            p_cls = str(real_p.get("class", "")).strip().lower()
            p_sub = str(real_p.get("subclass", "")).strip().lower()
            p_lvl = real_p.get("level", 1)
            has_extra_attack = p_lvl >= 5 and (
                p_cls in ("fighter", "paladin", "barbarian") or (p_cls == "bard" and p_sub == "college_of_swords")
            )
            if has_extra_attack and cs is not None:
                # Choose same target if still alive, else first living enemy
                second_target = selected_target
                if second_target.get("hp", {}).get("current", 0) <= 0:
                    living = [e for e in cs.get("enemies", []) if e.get("hp", {}).get("current", 0) > 0]
                    second_target = living[0] if living else None
                if second_target:
                    extra_res = combat_manager.resolve_attack(
                        player_c,
                        second_target,
                        selected_attack,
                        combat_state=cs,
                        use_reckless_attack=use_reckless_attack,
                    )
                    extra_res["attack_name"] = f"{selected_attack.get('name', 'Attack')} (Extra Attack)"
                    cs.setdefault("pending_player_actions", []).append(extra_res)

    # 2. Resolve full combat round (Python math)
    res_round = combat_manager.resolve_round(
        combat_state=cs,
        player_attack_result=player_attack_result,
        world_state=world,
    )
    return player_attack_result, res_round


def _get_available_bonus_actions(player: Dict[str, Any], player_c: Dict[str, Any], world: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build list of BG3 Bonus Actions currently available to the player."""
    cls_id = str(player.get("class", player_c.get("class", ""))).strip().lower()
    sub_id = str(player.get("subclass", player_c.get("subclass", ""))).strip().lower()
    lvl = player.get("level", 1)
    resources = player.get("class_resources", {})
    options: List[Dict[str, Any]] = []

    # 1. Shove (In BG3, Shove is a Bonus Action!)
    options.append({"id": "shove_push", "label": "🫸 Shove (ผลัก 5 ฟุต / ตกเหว)", "kind": "shove", "shove_type": "push"})
    options.append({"id": "shove_prone", "label": "🫸 Shove: Knock Prone (ผลักล้มคว่ำ)", "kind": "shove", "shove_type": "prone"})

    # 2. Healing Potion (In BG3, drinking a potion is a Bonus Action!)
    inv = player.get("inventory", [])
    has_potion = any(isinstance(i, dict) and i.get("item_id") == "healing_potion" and i.get("quantity", 0) > 0 for i in inv)
    if has_potion:
        options.append({"id": "drink_potion", "label": "🧪 ดื่มยาฟื้นพลัง (Healing Potion 2d4+2)", "kind": "consumable", "item_id": "healing_potion"})

    # 3. Class-specific Bonus Actions
    if cls_id == "fighter":
        if resources.get("second_wind", {}).get("current", 0) > 0:
            options.append({"id": "second_wind", "label": f"💨 Second Wind (ฟื้นฟู 1d10+{lvl} HP)", "kind": "class_action"})
        if lvl >= 2 and resources.get("action_surge", {}).get("current", 0) > 0:
            options.append({"id": "action_surge", "label": "⚡ Action Surge (+1 Action พิเศษในเทิร์นนี้!)", "kind": "class_action"})
    elif cls_id == "rogue":
        options.append({"id": "cunning_action_hide", "label": "🥷 Cunning Action: Hide (ซ่อนตัว -> Advantage โจมตีถัดไป)", "kind": "class_action"})
    elif cls_id == "wizard":
        if resources.get("arcane_recovery", {}).get("current", 0) > 0:
            options.append({"id": "arcane_recovery", "label": "🔮 Arcane Recovery (ฟื้นฟูสล็อตเวท 1 ช่อง)", "kind": "class_action"})
    elif cls_id == "bard":
        if player.get("bardic_inspiration", {}).get("current", 0) > 0:
            options.append({"id": "bardic_inspiration", "label": "🎵 Bardic Inspiration (บัฟ +1d6 การทอยโจมตี)", "kind": "class_action"})
    elif cls_id == "paladin":
        if sub_id == "oath_of_vengeance" and resources.get("channel_oath", {}).get("current", 0) > 0:
            options.append({"id": "vow_of_enmity", "label": "🔥 Vow of Enmity (ได้ Advantage โจมตี 3 เทิร์น)", "kind": "class_action"})
    elif cls_id == "barbarian":
        p_conds = combat_manager._get_condition_set(player_c)
        if "raging" not in p_conds and resources.get("rage_charges", {}).get("current", 0) > 0:
            options.append({"id": "rage", "label": "💢 Rage (เข้าโหมดคลั่ง: +2 Melee Dmg & ลดดาเมจกายภาพ 50%)", "kind": "class_action"})
        if "raging" in p_conds and sub_id == "berserker" and lvl >= 3:
            options.append({"id": "frenzied_strike", "label": "🪓 Frenzied Strike (โจมตีพิเศษด้วย Bonus Action)", "kind": "class_action"})

    # 4. Bonus Action Spells (Healing Word, Misty Step, Hex, Divine Favor, Shield, Shield of Faith)
    sp_cat = get_display_spell_catalog(world)
    for sid in player.get("known_spells", []):
        sdata = sp_cat.get(sid, {})
        if sdata.get("action_cost") in ("bonus_action", "reaction"):
            slvl = sdata.get("level", 0)
            s_cur = player.get("spell_slots", {}).get(str(slvl), {}).get("current", 0) if slvl > 0 else 99
            if s_cur > 0:
                options.append({
                    "id": f"ba_spell_{sid}",
                    "label": f"✨ ร่ายเวทเร็ว: {sdata.get('name', sid)} (Lv {slvl})",
                    "kind": "spell",
                    "spell_id": sid,
                })

    return options


def render_combat_panel(player: Dict[str, Any], world: Dict[str, Any], current_room: Dict[str, Any]):
    """Render the interactive turn-based D&D 5e combat panel with BG3 Action + Bonus Action Economy."""
    st.markdown("---")
    st.error("⚔️ **COMBAT IN PROGRESS — Baldur's Gate 3 Tactical Turn HUD**")
    cs = world["combat_state"]
    round_num = cs.get("round", 1)
    enemies = cs.get("enemies", [])
    companions = cs.get("companions", [])
    player_c = cs.get("player_combatant", {})

    # Display active surface & smoke status (Phase 11.1)
    room_surface = cs.get("room_surface", {})
    surf_type = room_surface.get("type")
    surf_dur = room_surface.get("duration", 0)
    if surf_type:
        st.info(f"🌊 **Active Surface: {surf_type.upper()}** ({surf_dur} round{'s' if surf_dur != 1 else ''} remaining)")
    if cs.get("smoke_active"):
        smoke_dur = cs.get("smoke_duration", 0)
        st.warning(f"💨 **Smoke Active**: Ranged attacks suffer disadvantage ({smoke_dur} round{'s' if smoke_dur != 1 else ''} remaining)")
    room_hazards = cs.get("room_hazards", [])
    if room_hazards:
        st.warning(f"⚠️ **Environmental Hazards: {', '.join(h.replace('_', ' ').title() for h in room_hazards)}**")
    c_objs = current_room.get("interactive_objects", []) if current_room else []
    unused_c_objs = [o for o in c_objs if not o.get("used")]
    if unused_c_objs:
        st.info(f"🎯 **Interactive Objects in Room: {', '.join(o.get('name', o.get('id')) for o in unused_c_objs)}**")

    col_c1, col_c2 = st.columns([2, 1.2])

    with col_c1:
        st.markdown(f"##### Round {round_num} — Active Combatants")
        p_cur_hp = max(0, player_c.get("hp", {}).get("current", 0))
        p_max_hp = max(1, player_c.get("hp", {}).get("max", 10))
        p_temp_hp = player_c.get("hp", {}).get("temp", player.get("hp", {}).get("temp", 0))
        p_pct = max(0.0, min(1.0, p_cur_hp / p_max_hp))
        p_hg = " [⛰️ High Ground]" if player_c.get("has_high_ground") else ""
        p_cond_set = combat_manager._get_condition_set(player_c)
        p_cond_str = f" • ✨ `{', '.join(sorted(p_cond_set))}`" if p_cond_set else ""
        p_conc_str = f" • 🧠 Conc: `{player.get('concentration')}`" if player.get("concentration") else ""
        temp_str = f" (+{p_temp_hp} Temp)" if p_temp_hp > 0 else ""

        col_p1, col_p2 = st.columns([1.2, 1])
        with col_p1:
            st.markdown(f"🧑 **{player_c.get('name', 'Adventurer')}** (You) | AC: {player_c.get('ac', 10)}{p_hg}{p_cond_str}{p_conc_str}")
        with col_p2:
            st.progress(p_pct, text=f"{p_cur_hp}/{p_max_hp} HP{temp_str}")

        for comp in companions:
            c_hp = comp.get("hp", {})
            c_cur = max(0, c_hp.get("current", 0))
            c_max = max(1, c_hp.get("max", 10))
            c_pct = max(0.0, min(1.0, c_cur / c_max))
            c_hg = " [⛰️ High Ground]" if comp.get("has_high_ground") else ""
            col_comp1, col_comp2 = st.columns([1.2, 1])
            with col_comp1:
                st.markdown(f"🤝 **{comp.get('name', 'Companion')}** | AC: {comp.get('ac', 10)}{c_hg}")
            with col_comp2:
                st.progress(c_pct, text=f"{c_cur}/{c_max} HP")

        st.markdown("---")
        st.markdown("###### Foes")
        if enemies:
            num_e_cols = min(len(enemies), 3) if len(enemies) > 1 else 1
            if num_e_cols > 1:
                e_cols = st.columns(num_e_cols)
                for idx, enemy in enumerate(enemies):
                    with e_cols[idx % num_e_cols]:
                        e_id = enemy.get("id", "enemy")
                        e_name = enemy.get("name", e_id)
                        e_hp = enemy.get("hp", {})
                        cur_e_hp = max(0, e_hp.get("current", 0))
                        max_e_hp = max(1, e_hp.get("max", 1))
                        pct = max(0.0, min(1.0, cur_e_hp / max_e_hp))
                        e_hg = " [⛰️ High Ground]" if enemy.get("has_high_ground") else ""
                        e_cset = combat_manager._get_condition_set(enemy)
                        conds = f" *({', '.join(sorted(e_cset))})*" if e_cset else ""
                        st.markdown(f"👹 **{e_name}**{e_hg}")
                        st.caption(f"AC: {enemy.get('ac', 10)}{conds}")
                        st.progress(pct, text=f"{cur_e_hp}/{max_e_hp} HP")
            else:
                for enemy in enemies:
                    e_id = enemy.get("id", "enemy")
                    e_name = enemy.get("name", e_id)
                    e_hp = enemy.get("hp", {})
                    cur_e_hp = max(0, e_hp.get("current", 0))
                    max_e_hp = max(1, e_hp.get("max", 1))
                    pct = max(0.0, min(1.0, cur_e_hp / max_e_hp))
                    e_hg = " [⛰️ High Ground]" if enemy.get("has_high_ground") else ""
                    e_cset = combat_manager._get_condition_set(enemy)
                    conds = f" *({', '.join(sorted(e_cset))})*" if e_cset else ""
                    col_e1, col_e2 = st.columns([1.2, 1])
                    with col_e1:
                        st.markdown(f"👹 **{e_name}** | AC: {enemy.get('ac', 10)}{e_hg}{conds}")
                    with col_e2:
                        st.progress(pct, text=f"{cur_e_hp}/{max_e_hp} HP")

    living_enemies = [e for e in enemies if e.get("hp", {}).get("current", 0) > 0]
    player_alive = player_c.get("hp", {}).get("current", 0) > 0
    cls_id = str(player.get("class", "")).strip().lower()
    sub_id = str(player.get("subclass", "")).strip().lower()
    lvl = player.get("level", 1)

    with col_c2:
        max_act, max_ba = _get_max_actions_and_bonus(player_c, player, cs)
        used_act = cs.get("actions_used", 0)
        used_ba = cs.get("bonus_actions_used", 0)
        rem_act = max(0, max_act - used_act)
        rem_ba = max(0, max_ba - used_ba)

        st.markdown("##### 🎯 Turn Action Economy")
        st.markdown(
            f"🟢 **Action:** `{rem_act}/{max_act}` &nbsp;&nbsp;|&nbsp;&nbsp; 🟠 **Bonus Action:** `{rem_ba}/{max_ba}`"
        )

        btn_disabled = (cs.get("status") == "ended") or (cs.get("downed_outcome") is not None)

        # ── 1. BONUS ACTION BAR (Can be used BEFORE main Action in the same turn!) ──
        if player_alive and rem_ba > 0 and living_enemies:
            ba_options = _get_available_bonus_actions(player, player_c, world)
            if ba_options:
                with st.expander(f"🟠 ใช้ Bonus Action ก่อนจบเทิร์น ({rem_ba} คงเหลือ)", expanded=False):
                    ba_map = {opt["label"]: opt for opt in ba_options}
                    chosen_ba_lbl = st.selectbox("เลือก Bonus Action:", list(ba_map.keys()), key=f"ba_sel_{round_num}_{used_ba}")
                    chosen_ba = ba_map[chosen_ba_lbl]
                    ba_target = _select_enemy_target(living_enemies, "🎯 เป้าหมาย Bonus Action:", key=f"ba_tgt_{round_num}_{used_ba}")

                    if st.button("🟠 กดใช้ Bonus Action ทันที", key=f"btn_exec_ba_{round_num}_{used_ba}", use_container_width=True):
                        ba_res = None
                        if chosen_ba["kind"] == "shove":
                            ba_res = combat_manager.resolve_shove(
                                attacker=player_c, target=ba_target, combat_state=cs, shove_type=chosen_ba["shove_type"]
                            )
                        elif chosen_ba["kind"] == "consumable":
                            ok_pot, _, pot_res = state_manager.use_consumable(chosen_ba["item_id"], player)
                            if ok_pot:
                                player_c["hp"] = player["hp"]
                                ba_res = {
                                    "action_type": "class_action",
                                    "attacker_id": "player",
                                    "attacker_name": player_c.get("name", "Player"),
                                    "healed": pot_res.get("healed", 0),
                                    "narration": f"{player_c.get('name')} drinks a Healing Potion (Bonus Action), restoring {pot_res.get('healed', 0)} HP!",
                                }
                        elif chosen_ba["kind"] == "class_action":
                            ba_res = combat_manager.resolve_class_action(
                                actor=player_c, action_id=chosen_ba["id"], target=ba_target, combat_state=cs
                            )
                        elif chosen_ba["kind"] == "spell":
                            ba_res = state_manager.cast_spell(
                                caster=player_c, target=ba_target, spell_id=chosen_ba["spell_id"],
                                character_state=player, world_state=world
                            )

                        if ba_res:
                            if ba_res.get("success") is False and ba_res.get("reason"):
                                st.error(ba_res["reason"])
                            else:
                                if chosen_ba.get("id") != "action_surge":
                                    cs["bonus_actions_used"] = used_ba + 1
                                cs.setdefault("pending_player_actions", []).append(ba_res)
                                msg_toast = ba_res.get("narration") or f"ใช้ {chosen_ba['label']} สำเร็จ!"
                                st.toast(f"🟠 {msg_toast}")
                                auto_save()
                                st.rerun()

        # Show queued Bonus Actions executed this turn
        pending_list = cs.get("pending_player_actions", [])
        if pending_list:
            for p_act in pending_list:
                p_narr = p_act.get("narration") or f"{p_act.get('attack_name', 'Action')} resolved."
                st.success(f"✓ **ทำแล้วในเทิร์นนี้:** {p_narr}")

        # ── 2. MAIN ACTION SECTION ──
        selected_target = None
        selected_attack = None
        selected_spell_id = None
        selected_shove_type = "push"
        selected_adj_target = None
        selected_class_action_id = None
        use_divine_smite = False
        use_reckless_attack = False
        selected_maneuver = None
        use_flourish = False
        selected_upcast_lvl = None
        selected_parley_skill = "Persuasion"
        selected_parley_speech = ""
        action_type = "⚔️ Weapon Attack"

        known_spells = player.get("known_spells", [])
        sp_catalog = get_display_spell_catalog(world)
        w_avail = player.get("weapon_actions_available", True) and player_c.get("weapon_actions_available", True)

        # Check main Action class abilities (Cleric Channel Divinity, Paladin Lay on Hands)
        resources = player.get("class_resources", {})
        main_class_actions = []
        if cls_id == "cleric" and resources.get("channel_divinity", {}).get("current", 0) > 0:
            if sub_id == "light_domain":
                main_class_actions.append(("channel_divinity_radiance", "☀️ Channel Divinity: Radiance of the Dawn (2d10+Lv Radiant AoE)"))
            else:
                main_class_actions.append(("channel_divinity_preserve_life", f"✨ Channel Divinity: Preserve Life (ฟื้นฟูทีม {3*lvl} HP)"))
        if cls_id == "paladin" and resources.get("lay_on_hands", {}).get("current", 0) > 0:
            loh_cur = resources["lay_on_hands"]["current"]
            main_class_actions.append(("lay_on_hands", f"🙌 Lay on Hands (รักษา {loh_cur} HP)"))

        if player_alive and (living_enemies or known_spells):
            act_options = ["⚔️ Weapon Attack", "🫸 Shove", "🗣️ Parley (เจรจา/ข่มขู่)"]
            if w_avail:
                act_options.append("💥 Weapon Action")
            if known_spells:
                act_options.append("🪄 Cast Spell")
            if main_class_actions:
                act_options.append("🌟 Class Ability")
            action_type = st.radio("Action:", act_options, horizontal=True, key=f"c_act_type_{round_num}")

            if action_type == "⚔️ Weapon Attack" and living_enemies:
                selected_target = _select_enemy_target(living_enemies, "🎯 Target Enemy", key=f"target_sel_{round_num}")

                attacks = player_c.get("attacks", [])
                if not attacks:
                    attacks = [{"name": "Unarmed Strike", "attack_bonus": 2, "damage": "1+0", "damage_type": "bludgeoning", "ranged": False}]

                def _fmt_atk(a):
                    ranged_tag = " [Ranged]" if a.get("ranged") else ""
                    return f"{a.get('name', 'Attack')} (+{a.get('attack_bonus', 0)}, {a.get('damage', '1d4')} {a.get('damage_type', '')}){ranged_tag}"

                atk_map = {_fmt_atk(a): a for a in attacks}
                chosen_atk_label = st.selectbox("⚔️ Weapon / Attack", list(atk_map.keys()), key=f"atk_sel_{round_num}")
                selected_attack = atk_map[chosen_atk_label]
                atk_dmg = selected_attack.get("damage", "1d4")
                atk_dtype = selected_attack.get("damage_type", "").title()
                atk_rng = "Ranged" if selected_attack.get("ranged") else "Melee"
                extra_atk_badge = " | 🔥 **Extra Attack (โจมตี 2 ครั้ง!)**" if (
                    lvl >= 5 and (cls_id in ("fighter", "paladin", "barbarian") or (cls_id == "bard" and sub_id == "college_of_swords"))
                ) else ""
                st.caption(f"💥 Damage: **{atk_dmg} {atk_dtype}** ({atk_rng}) | Attack Bonus: **+{selected_attack.get('attack_bonus', 0)}**{extra_atk_badge}")

                # BG3 Class Attack Toggles
                if cls_id == "paladin" and not selected_attack.get("ranged"):
                    has_smite_slot = any(player.get("spell_slots", {}).get(k, {}).get("current", 0) > 0 for k in ("1", "2", "3"))
                    if has_smite_slot:
                        use_divine_smite = st.checkbox("⚡ ใช้ Divine Smite (+2d8 Radiant Damage, ใช้ 1 Spell Slot)", key=f"chk_smite_{round_num}")

                if cls_id == "barbarian" and lvl >= 2 and not selected_attack.get("ranged"):
                    use_reckless_attack = st.checkbox("💢 ใช้ Reckless Attack (ทอยโจมตีแบบได้เปรียบ Advantage!)", key=f"chk_reckless_{round_num}")

                if cls_id == "fighter" and sub_id == "battle_master" and lvl >= 3:
                    sup_cur = resources.get("superiority_dice", {}).get("current", 0)
                    if sup_cur > 0:
                        man_choice = st.selectbox(
                            f"🎖️ Battle Master Maneuver ({sup_cur} Superiority Dice คงเหลือ):",
                            ["ไม่ใช้ (Normal Attack)", "menacing_attack", "trip_attack", "precision_attack"],
                            format_func=lambda m: {
                                "ไม่ใช้ (Normal Attack)": "ไม่ใช้ (Normal Attack)",
                                "menacing_attack": "Menacing Attack (+1d8 dmg & Frightened)",
                                "trip_attack": "Trip Attack (+1d8 dmg & Knock Prone)",
                                "precision_attack": "Precision Attack (+1d8 ความแม่นยำ To-Hit)",
                            }[m],
                            key=f"sel_maneuver_{round_num}",
                        )
                        if man_choice != "ไม่ใช้ (Normal Attack)":
                            selected_maneuver = man_choice

                if cls_id == "bard" and sub_id == "college_of_swords" and lvl >= 3:
                    bi_cur = player.get("bardic_inspiration", {}).get("current", 0)
                    if bi_cur > 0:
                        use_flourish = st.checkbox(f"🗡️ Defensive Flourish (+1d6 ดาเมจ & +3 AC, คงเหลือ {bi_cur})", key=f"chk_flourish_{round_num}")

            elif action_type == "💥 Weapon Action" and living_enemies:
                selected_target = _select_enemy_target(living_enemies, "🎯 Target Enemy", key=f"wact_target_sel_{round_num}")

                attacks = player_c.get("attacks", [])
                if not attacks:
                    attacks = [{"name": "Unarmed Strike", "attack_bonus": 2, "damage": "1+0", "damage_type": "bludgeoning", "ranged": False}]

                def _fmt_wact(a):
                    winfo = combat_manager.get_weapon_action_info(a)
                    return f"{a.get('name', 'Attack')} → {winfo['name']} ({winfo['damage_type'].title()})"

                wact_map = {_fmt_wact(a): a for a in attacks}
                chosen_wact_label = st.selectbox("💥 Weapon Action", list(wact_map.keys()), key=f"wact_sel_{round_num}")
                selected_attack = wact_map[chosen_wact_label]

                w_info = combat_manager.get_weapon_action_info(selected_attack)
                st.info(f"💥 **{w_info['name']}**: {w_info['description']}")

                if w_info["name"] == "Cleave":
                    other_enemies = [e for e in living_enemies if e.get("id") != selected_target.get("id")]
                    if other_enemies:
                        adj_map = {f"{e.get('name', e.get('id'))} (HP: {e.get('hp', {}).get('current', 0)})": e for e in other_enemies}
                        chosen_adj_label = st.selectbox("🎯 Adjacent Enemy (Cleave)", list(adj_map.keys()), key=f"cleave_adj_{round_num}")
                        selected_adj_target = adj_map[chosen_adj_label]

            elif action_type == "🫸 Shove" and living_enemies:
                selected_target = _select_enemy_target(living_enemies, "🎯 Target Enemy to Shove", key=f"shove_target_sel_{round_num}")

                shove_goal = st.radio("Shove Goal:", ["Push 5ft", "Knock Prone"], horizontal=True, key=f"shove_goal_{round_num}")
                selected_shove_type = "prone" if shove_goal == "Knock Prone" else "push"

            elif action_type == "🪄 Cast Spell" and known_spells:
                def _fmt_sp(sid):
                    sinfo = sp_catalog.get(sid, {})
                    slvl = sinfo.get("level", 0)
                    lvl_tag = "Cantrip" if slvl == 0 else f"Lvl {slvl}"
                    cost_tag = " [Bonus]" if sinfo.get("action_cost") == "bonus_action" else ""
                    slots_left = ""
                    if slvl > 0:
                        s_cur = player.get("spell_slots", {}).get(str(slvl), {}).get("current", 0)
                        slots_left = f" [{s_cur} slot(s)]"
                    return f"{sinfo.get('name', sid)} ({lvl_tag}){cost_tag}{slots_left}"

                sp_map = {_fmt_sp(s): s for s in known_spells}
                chosen_sp_label = st.selectbox("🪄 Select Spell", list(sp_map.keys()), key=f"spell_sel_{round_num}")
                selected_spell_id = sp_map[chosen_sp_label]
                chosen_sp_data = sp_catalog.get(selected_spell_id, {})
                sp_type = chosen_sp_data.get("type", "attack_save")
                sp_lvl = chosen_sp_data.get("level", 0)
                sp_eff = chosen_sp_data.get("effect", {})
                sp_desc = chosen_sp_data.get("description", "")
                sp_details = []
                if "damage" in sp_eff:
                    sp_details.append(f"💥 Damage: {sp_eff['damage']} {sp_eff.get('damage_type', '')}".strip())
                if "heal" in sp_eff:
                    sp_details.append(f"💚 Heal: {sp_eff['heal']}")
                if chosen_sp_data.get("target_type") == "aoe_enemies":
                    sp_details.append("🔥 AoE (โดนศัตรูทุกตัว)")
                if chosen_sp_data.get("projectiles", 1) > 1:
                    sp_details.append(f"☄️ {chosen_sp_data['projectiles']} Rays/Darts")
                if sp_eff.get("auto_hit"):
                    sp_details.append("✨ Auto-hit")
                if sp_details:
                    st.caption(f"{' | '.join(sp_details)} — *{sp_desc}*" if sp_desc else ' | '.join(sp_details))
                elif sp_desc:
                    st.caption(f"*{sp_desc}*")

                # Upcasting Selection (if spell level > 0 and higher slots are available)
                if sp_lvl > 0:
                    avail_slot_lvls = [
                        int(k) for k, v in player.get("spell_slots", {}).items()
                        if isinstance(v, dict) and v.get("current", 0) > 0 and int(k) >= sp_lvl
                    ]
                    if len(avail_slot_lvls) > 1:
                        selected_upcast_lvl = st.selectbox(
                            "🔼 ระดับสล็อตเวทที่ใช้ร่าย (Upcast Slot Level):",
                            sorted(avail_slot_lvls),
                            format_func=lambda l: f"Level {l} Slot" + (" (Upcast แรงขึ้น!)" if l > sp_lvl else " (Base Level)"),
                            key=f"upcast_sel_{round_num}",
                        )
                    elif len(avail_slot_lvls) == 1:
                        selected_upcast_lvl = avail_slot_lvls[0]

                # Target selection based on spell type
                if sp_type in ("heal", "buff"):
                    heal_targets = {f"You ({player_c.get('name')}) [HP: {player_c.get('hp',{}).get('current')}/{player_c.get('hp',{}).get('max')}]": player_c}
                    for comp in companions:
                        heal_targets[f"🤝 {comp.get('name')} [HP: {comp.get('hp',{}).get('current')}/{comp.get('hp',{}).get('max')}]"] = comp
                    chosen_ht = st.selectbox("🎯 Target (Self / Ally)", list(heal_targets.keys()), key=f"heal_target_sel_{round_num}")
                    selected_target = heal_targets[chosen_ht]
                elif living_enemies:
                    selected_target = _select_enemy_target(living_enemies, "🎯 Target Enemy", key=f"spell_target_sel_{round_num}")
                else:
                    selected_target = player_c

            elif "Parley" in action_type and living_enemies:
                selected_target = _select_enemy_target(living_enemies, "🎯 เลือกศัตรูที่ต้องการพูดคุย/ข่มขู่", key=f"parley_target_sel_{round_num}")
                selected_parley_skill = st.selectbox(
                    "🎭 ทักษะการเจรจา (Social Skill):",
                    ["Persuasion", "Intimidation", "Deception"],
                    format_func=lambda s: {
                        "Persuasion": "🕊️ Persuasion (เกลี้ยกล่อมให้วางอาวุธยอมจำนน / ทำให้ลังเล)",
                        "Intimidation": "👹 Intimidation (ข่มขู่ให้ขวัญหนีดีฝ่อ / วิ่งหนีออกจากสนามรบ)",
                        "Deception": "🃏 Deception (หลอกล่อเบี่ยงเบนความสนใจ -> ศัตรูมึนงง Dazed & เราได้ Advantage)",
                    }[s],
                    key=f"parley_skill_sel_{round_num}",
                )
                selected_parley_speech = st.text_input(
                    "💬 คำพูดของคุณที่จะตะโกนบอกศัตรู:",
                    placeholder="เช่น 'วางอาวุธซะ! หัวหน้าพวกแกทิ้งพวกแกไปแล้ว!' หรือ 'เราไม่ได้มาสู้ มาตกลงกันดีๆ!'",
                    key=f"parley_speech_in_{round_num}",
                )
                st.caption("💡 *ทิป: ศัตรูที่บาดเจ็บ (HP ต่ำกว่า 50%) จะยอมจำนนหรือวิ่งหนีง่ายขึ้นมาก!*")

            elif action_type == "🌟 Class Ability" and main_class_actions:
                ca_map = {lbl: cid for cid, lbl in main_class_actions}
                chosen_ca_lbl = st.selectbox("🌟 เลือกความสามารถประจำคลาส:", list(ca_map.keys()), key=f"ca_main_sel_{round_num}")
                selected_class_action_id = ca_map[chosen_ca_lbl]
                selected_target = living_enemies[0] if living_enemies else player_c

        elif not player_alive:
            st.warning("⚠️ You are down! Rolling death saves...")

        if not player_alive:
            button_label = "⏳ Endure Round"
        elif action_type == "⚔️ Weapon Attack":
            button_label = "⚔️ Attack & End Round"
        elif action_type == "💥 Weapon Action":
            w_info_btn = combat_manager.get_weapon_action_info(selected_attack) if selected_attack else {"name": "Weapon Action"}
            button_label = f"💥 {w_info_btn['name']} & End Round"
        elif action_type == "🫸 Shove":
            button_label = "🫸 Shove & End Round"
        elif action_type == "🪄 Cast Spell":
            button_label = "🪄 Cast Spell & End Round"
        elif action_type == "🌟 Class Ability":
            button_label = "🌟 Use Class Ability & End Round"
        elif "Parley" in action_type:
            button_label = f"🗣️ {selected_parley_skill} Parley & End Round"
        else:
            button_label = "⚔️ Attack & End Round"

        if unused_c_objs and player_alive:
            st.markdown("###### 🎯 Environmental Tactics")
            for c_obj in unused_c_objs:
                c_obj_id = c_obj.get("id")
                c_obj_name = c_obj.get("name", c_obj_id)
                col_cot, col_cob = st.columns([2, 1])
                with col_cot:
                    st.caption(f"**{c_obj_name}**: {c_obj.get('description', '')}")
                with col_cob:
                    if st.button("🎯 ใช้งาน", key=f"c_obj_{c_obj_id}_{round_num}", use_container_width=True, disabled=btn_disabled):
                        res_io = dungeon_manager.interact_with_object(
                            world, current_room.get("id"), c_obj_id, combat_state=cs, character_state=player
                        )
                        st.session_state["narrative_log"].append({
                            "role": "assistant",
                            "content": f"🎯 **Environmental Tactic:** {res_io.get('message')}"
                        })
                        c_outcome = combat_manager.check_combat_end(cs, world)
                        if not c_outcome:
                            res_rnd = combat_manager.resolve_round(cs, world_state=world)
                            if res_rnd.get("narration_block"):
                                st.session_state["narrative_log"].append({
                                    "role": "assistant",
                                    "content": res_rnd["narration_block"]
                                })
                        auto_save()
                        st.rerun()

        if st.button(button_label, disabled=btn_disabled, use_container_width=True, type="primary"):
            # 1. Resolve player's combat action & round
            player_attack_result, res_round = resolve_player_combat_action(
                action_type=action_type,
                player_c=player_c,
                selected_target=selected_target,
                selected_attack=selected_attack,
                selected_spell_id=selected_spell_id,
                selected_shove_type=selected_shove_type,
                selected_adj_target=selected_adj_target,
                cs=cs,
                player=player,
                world=world,
                use_divine_smite=use_divine_smite,
                use_reckless_attack=use_reckless_attack,
                maneuver=selected_maneuver,
                use_flourish=use_flourish,
                upcast_level=selected_upcast_lvl,
                class_action_id=selected_class_action_id,
                parley_skill=selected_parley_skill,
                parley_speech=selected_parley_speech,
            )
            if res_round.get("error"):
                st.error(res_round["error"])
                st.stop()

            combat_manager.sync_player_state(player_c)
            narration_block = res_round["narration_block"]
            outcome = res_round["combat_outcome"]

            # 3. EXACTLY ONE narrative LLM call for the entire round
            hist = st.session_state.get("history_buffer", [])
            active_model = get_current_active_model()
            with st.spinner("⚔️ DM กำลังบรรยายผลการต่อสู้..."):
                narrative_res = llm_handler.generate_narrative_response(
                    user_input="",
                    player_state=player,
                    world_state=world,
                    history=hist,
                    round_result=narration_block,
                    model=active_model,
                    ollama_model=st.session_state.get("selected_model"),
                )

            narration_text = narrative_res.get("narrative", "")
            if narrative_res.get("suggestions"):
                st.session_state["action_suggestions"] = narrative_res["suggestions"]
            full_combat_msg = f"{narration_block}\n\n*{narration_text}*"

            # 4. Append turn to narrative log & history buffer
            st.session_state["narrative_log"].append({"role": "assistant", "content": full_combat_msg})
            st.session_state["history_buffer"].append({"role": "assistant", "content": narration_text})

            # 5. Check combat end
            if outcome == "player_victory":
                xp_gained = cs.get("xp_gained", 0)
                gold_looted = cs.get("gold_looted", 0)
                items_looted = cs.get("items_looted", [])
                leveled_up = cs.get("leveled_up", False)
                combat_manager.end_combat(world)
                loot_parts = [f"+{xp_gained} XP"]
                if gold_looted > 0:
                    loot_parts.append(f"+{gold_looted} GP")
                if items_looted:
                    loot_parts.append(f"Items: {', '.join(items_looted)}")
                lvl_msg = " 🌟 **LEVEL UP!**" if leveled_up else ""
                reward_summary = f" ({' | '.join(loot_parts)}){lvl_msg}"
                st.success(f"🎉 Combat Victory! All enemies have been defeated.{reward_summary}")
                st.session_state["narrative_log"].append({
                    "role": "assistant",
                    "content": f"🏆 **Victory!** You defeated your foes and stand triumphant.{reward_summary}"
                })
                # Store combat victory in long-term ChromaDB memory!
                from ui.session import get_session_memory_manager
                mm = get_session_memory_manager()
                if mm is not None:
                    try:
                        enemy_names_str = ", ".join(e.get("name", "enemy") for e in enemies)
                        mm.add_turn(f"Defeated {enemy_names_str} in combat at {current_room.get('name', 'the battlefield')}{reward_summary}.")
                    except Exception:
                        pass
            elif outcome == "player_defeat":
                out_res = cs.get("downed_outcome") or state_manager.resolve_downed_outcome(player, cs, world)
                combat_manager.end_combat(world)

                out_name = out_res.get("outcome", "robbed_and_left")
                pen = out_res.get("penalty", {})

                if out_name == "robbed_and_left":
                    msg = f"⚠️ **Defeat!** You were knocked unconscious, robbed of {pen.get('gold_lost', 0)} GP, and left for dead. You awaken at **{pen.get('relocated_to')}** with 1 HP."
                elif out_name == "captured":
                    msg = "⚠️ **Defeat!** You were captured by your enemies! You are now held captive."
                else:  # rescued_by_npc
                    msg = f"⚠️ **Defeat!** A wanderer rescued you from death! Restored to {pen.get('hp_restored', 1)} HP."

                st.error(msg)
                st.session_state["narrative_log"].append({
                    "role": "assistant",
                    "content": msg
                })

            auto_save()
            st.rerun()

        # Display active combat suggestions
        combat_suggestions = st.session_state.get("action_suggestions") or list(llm_handler.DEFAULT_ACTION_SUGGESTIONS)
        st.caption("💡 **Tactical Suggestions:**")
        for csug in combat_suggestions[:3]:
            st.markdown(f"- *{csug}*")
