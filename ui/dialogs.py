"""
ui/dialogs.py — Modal dialogs for Character Sheet, Inventory, Quests, Spells, and Camp/Rest.
"""

import random
from typing import Any, Dict, Optional
import streamlit as st

import combat_manager
import dungeon_manager
import state_manager
from ui.session import (
    auto_save,
    get_current_active_model,
    get_display_spell_catalog,
    get_player_currency,
    get_ration_count,
    get_session_memory_manager,
)


@st.dialog("👤 ข้อมูลตัวละคร (Character Sheet)", width="large")
def show_character_dialog():
    player = st.session_state.get("player_state")
    world = st.session_state.get("world_state")
    if not player or not world:
        return

    lvl = player.get("level", 1)
    race = player.get("race", "")
    cls_name = player.get("class_name", player.get("class", ""))
    sub_name = player.get("subclass_name") or (player.get("subclass", "").replace("_", " ").title() if player.get("subclass") else "")
    fs_name = player.get("fighting_style", "").replace("_", " ").title() if player.get("fighting_style") else ""
    bg = player.get("background", "")
    hp_info = player.get("hp", {"current": 10, "max": 10})
    cur_hp = hp_info.get("current", 0)
    max_hp = hp_info.get("max", 10)
    temp_hp = hp_info.get("temp", 0)
    ac = player.get("ac", 10)
    xp_cur = player.get("xp_current", 0)
    next_xp = state_manager.LEVEL_THRESHOLDS.get(lvl + 1, "MAX")
    insp = state_manager.get_inspiration(player)
    max_insp = player.get("max_inspiration", state_manager.MAX_INSPIRATION)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f"### {player.get('name', 'Adventurer')}")
        sub_lbl = f" ({sub_name})" if sub_name else ""
        st.caption(f"Level {lvl} {race} {cls_name}{sub_lbl}")
        if fs_name:
            st.caption(f"⚔️ Style: {fs_name}")
    with col2:
        thp_lbl = f" (+{temp_hp})" if temp_hp > 0 else ""
        st.metric("HP", f"{cur_hp} / {max_hp}{thp_lbl}")
        st.metric("Armor Class", f"{ac} AC")
    with col3:
        st.metric("XP", f"{xp_cur} / {next_xp}")
        st.metric("Inspiration", f"{insp} / {max_insp}")
    with col4:
        st.markdown(f"**Background:** {bg or 'None'}")
        speed = player.get("speed", 30)
        st.markdown(f"**Speed:** {speed} ft")
        pb = player.get("proficiency_bonus", 2)
        st.markdown(f"**Proficiency:** +{pb}")

    st.markdown("---")
    st.markdown("##### ⚔️ Ability Scores & Modifiers")
    stats = player.get("stats", {})
    stat_cols = st.columns(6)
    for idx, sname in enumerate(["STR", "DEX", "CON", "INT", "WIS", "CHA"]):
        val = stats.get(sname, 10)
        mod = state_manager.get_modifier(val)
        with stat_cols[idx]:
            st.metric(sname, val, f"{mod:+d}")

    # BG3 Class Resources, Unlocked Features, and Feats
    c_res = player.get("class_resources", {})
    c_feats = player.get("class_features", [])
    unlocked_feats = player.get("feats", [])
    if c_res or c_feats or unlocked_feats:
        st.markdown("---")
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            st.markdown("##### 🔥 Class Resources & Feats")
            for r_k, r_v in c_res.items():
                if isinstance(r_v, dict) and "max" in r_v and r_v.get("max", 0) > 0:
                    st.markdown(f"• **{r_k.replace('_', ' ').title()}:** `{r_v.get('current', 0)} / {r_v.get('max', 0)}`")
                elif isinstance(r_v, int) and r_k == "sneak_attack_dice":
                    st.markdown(f"• **Sneak Attack:** `+{r_v}d6`")
            if unlocked_feats:
                for fid in unlocked_feats:
                    finfo = state_manager.BG3_FEATS.get(fid, {"name": fid.title(), "description": ""})
                    st.markdown(f"• 🌟 **Feat — {finfo['name']}:** {finfo['description']}")
        with col_f2:
            if c_feats:
                st.markdown("##### 🛡️ Class Features")
                for cf in c_feats:
                    st.caption(f"✓ {cf}")

    # Level 4 Feat / Ability Score Improvement Selection (if pending or Lv4+ without feat chosen)
    if player.get("pending_level_up_choices", 0) > 0 or (lvl >= 4 and not unlocked_feats and player.get("pending_level_up_choices") is None):
        st.markdown("---")
        st.success("🌟 **Level Up Feature Unlocked! เลือก Feat หรือ เพิ่มค่าสถานะ (Ability Score Improvement)**")
        choice_mode = st.radio(
            "เลือกรูปแบบการอัปเกรด:",
            ["🌟 เลือก Feat พิเศษ (BG3 Feat)", "📈 เพิ่มค่าสถานะ +2 (Ability Score Improvement)"],
            horizontal=True,
            key="dlg_lvl4_mode",
        )
        if "Feat" in choice_mode:
            feat_keys = list(state_manager.BG3_FEATS.keys())
            chosen_feat = st.selectbox(
                "เลือก Feat:",
                feat_keys,
                format_func=lambda k: f"{state_manager.BG3_FEATS[k]['name']} — {state_manager.BG3_FEATS[k]['description']}",
                key="dlg_lvl4_feat_sel",
            )
            if st.button("✅ ยืนยันการเลือก Feat", type="primary", use_container_width=True, key="dlg_btn_confirm_feat"):
                ok_f, msg_f = state_manager.apply_feat_or_asi(player, "feat", feat_id=chosen_feat)
                if ok_f:
                    auto_save()
                    st.toast(msg_f)
                    st.rerun()
                else:
                    st.error(msg_f)
        else:
            stat_opts = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]
            c_s1, c_s2 = st.columns(2)
            with c_s1:
                s1 = st.selectbox("ค่าสถานะที่ 1 (+1):", stat_opts, key="dlg_asi_s1")
            with c_s2:
                s2 = st.selectbox("ค่าสถานะที่ 2 (+1, เลือกซ้ำเพื่อรับ +2):", stat_opts, key="dlg_asi_s2")
            if st.button("✅ ยืนยันการเพิ่มค่าสถานะ", type="primary", use_container_width=True, key="dlg_btn_confirm_asi"):
                ok_a, msg_a = state_manager.apply_feat_or_asi(player, "asi", stat_1=s1, stat_2=s2)
                if ok_a:
                    auto_save()
                    st.toast(msg_a)
                    st.rerun()
                else:
                    st.error(msg_a)

    # Party Companions & Approval
    party = world.get("party", {})
    companions = party.get("companions", [])
    if companions:
        st.markdown("---")
        st.markdown("##### 👥 สหายร่วมเดินทาง (Companions)")
        for comp in companions:
            cid = comp.get("id") or comp.get("name")
            c_name = comp.get("name", cid)
            c_app = state_manager.get_companion_approval(world, cid)
            c_approval = c_app.get("approval", 50)
            col_c1, col_c2, col_c3 = st.columns([2, 1, 1])
            with col_c1:
                st.markdown(f"**{c_name}** &bull; ความสนิทสนม: `{c_approval}/100`")
                st.progress(max(0.0, min(1.0, c_approval / 100)))
            with col_c2:
                if st.button(f"💬 พูดคุยรอบกองไฟ", key=f"dlg_talk_{cid}", use_container_width=True):
                    mm = get_current_active_model()
                    mem = get_session_memory_manager()
                    dlg = state_manager.generate_camp_dialogue(
                        cid,
                        mem,
                        player,
                        world,
                        model=mm,
                        use_live_llm=True,
                        ollama_model=st.session_state.get("selected_model"),
                    )
                    if dlg:
                        st.session_state["active_camp_dialogue"] = dlg
                        st.rerun()
            with col_c3:
                with st.popover("บอกลา"):
                    st.write(f"ต้องการให้ {c_name} ออกจากปาร์ตี้หรือไม่?")
                    if st.button("ยืนยัน", key=f"dlg_dismiss_{cid}", use_container_width=True):
                        try:
                            state_manager.dismiss_companion(cid, world)
                            auto_save()
                            st.toast(f"{c_name} ออกจากกลุ่มแล้ว")
                            st.rerun()
                        except Exception as ex:
                            st.error(f"Error dismissing {c_name}: {ex}")

        # Interactive Camp Dialogue & Freeform Chat Box
        active_dlg = st.session_state.get("active_camp_dialogue")
        if isinstance(active_dlg, dict):
            st.markdown("---")
            d_cid = active_dlg.get("companion_id", "")
            d_cname = active_dlg.get("companion_name", "Companion")
            st.info(f"🔥 **บทสนทนารอบกองไฟกับ {d_cname}**\n\n> *\"{active_dlg.get('statement', '')}\"*")
            opts = active_dlg.get("options", {})
            c_o1, c_o2, c_o3 = st.columns(3)
            chosen_resp_type = None
            chosen_resp_text = ""
            with c_o1:
                if st.button(f"💚 เห็นด้วย: {opts.get('agree', 'เห็นด้วย')[:30]}", key="camp_opt_agree", use_container_width=True):
                    chosen_resp_type = "agree"
                    chosen_resp_text = opts.get("agree", "")
            with c_o2:
                if st.button(f"⚖️ เป็นกลาง: {opts.get('neutral', 'เป็นกลาง')[:30]}", key="camp_opt_neutral", use_container_width=True):
                    chosen_resp_type = "neutral"
                    chosen_resp_text = opts.get("neutral", "")
            with c_o3:
                if st.button(f"💔 ไม่เห็นด้วย: {opts.get('disagree', 'ไม่เห็นด้วย')[:30]}", key="camp_opt_disagree", use_container_width=True):
                    chosen_resp_type = "disagree"
                    chosen_resp_text = opts.get("disagree", "")

            if chosen_resp_type:
                r_out = state_manager.respond_to_camp_dialogue(d_cid, chosen_resp_type, world, active_dlg)
                delta = r_out.get("approval_change", 0)
                st.session_state["narrative_log"].append({
                    "role": "user",
                    "content": f"(พูดคุยกับ {d_cname} รอบกองไฟ) \"{chosen_resp_text}\""
                })
                st.session_state["narrative_log"].append({
                    "role": "assistant",
                    "content": f"🔥 **{d_cname}** รับฟังคำพูดของคุณ (ความสนิทสนม `{delta:+d}` → `{r_out.get('new_approval')}/100`)"
                })
                st.session_state["active_camp_dialogue"] = None
                auto_save()
                st.toast(f"คุยกับ {d_cname} แล้ว! (ความสนิทสนม {delta:+d})")
                st.rerun()

            # Freeform chat input with companion!
            free_chat = st.text_input(
                f"💬 หรือพิมพ์พูดคุยกับ {d_cname} อย่างอิสระ:",
                placeholder=f"ถามเรื่องราวส่วนตัว ปรึกษาแผนการ หรือชวน {d_cname} คุยเรื่องอะไรก็ได้...",
                key="camp_freeform_input",
            )
            if st.button(f"🗣️ ส่งคำพูดคุยกับ {d_cname}", use_container_width=True, key="btn_camp_freeform"):
                if free_chat.strip():
                    import llm_handler
                    action_line = f'พูดคุยกับสหาย {d_cname} รอบกองไฟ: "{free_chat.strip()}"'
                    st.session_state["narrative_log"].append({"role": "user", "content": action_line})
                    hist = st.session_state.get("history_buffer", [])
                    active_m = get_current_active_model()
                    res_chat = llm_handler.generate_narrative_response(
                        user_input=action_line,
                        player_state=player,
                        world_state=world,
                        history=hist,
                        model=active_m,
                        single_pass=True,
                        ollama_model=st.session_state.get("selected_model"),
                    )
                    reply_txt = res_chat.get("narrative", "")
                    st.session_state["narrative_log"].append({"role": "assistant", "content": reply_txt})
                    st.session_state["history_buffer"].append({"role": "user", "content": action_line})
                    st.session_state["history_buffer"].append({"role": "assistant", "content": reply_txt})
                    state_manager.respond_to_camp_dialogue(d_cid, "agree", world, active_dlg)
                    st.session_state["active_camp_dialogue"] = None
                    auto_save()
                    st.toast(f"💬 {d_cname} ตอบกลับในบันทึกเนื้อเรื่องแล้ว! (ความสนิทสนม +5)")
                    st.rerun()


@st.dialog("🎒 กระเป๋าสัมภาระ & ถุงเงิน (Inventory)", width="large")
def show_inventory_dialog():
    player = st.session_state.get("player_state")
    world = st.session_state.get("world_state")
    if not player or not world:
        return

    inventory = player.get("inventory", [])
    ration_qty = get_ration_count(player)
    curr = get_player_currency(player)

    pouch_html = (
        '<div style="background: rgba(15, 23, 42, 0.75); border: 1px solid rgba(148, 163, 184, 0.2); border-radius: 8px; padding: 12px 16px; margin-bottom: 16px;">'
        '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; border-bottom: 1px solid rgba(148, 163, 184, 0.15); padding-bottom: 6px;">'
        '<span style="font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.08em; color: #94a3b8; font-weight: 700;">Coin Pouch (ถุงเหรียญเงิน)</span>'
        f'<span style="font-size: 0.8rem; color: #e2e8f0; background: rgba(51, 65, 85, 0.6); padding: 3px 10px; border-radius: 4px; border: 1px solid rgba(148, 163, 184, 0.25);">Rations: <strong style="color: #bef264;">{ration_qty}</strong></span>'
        '</div>'
        '<div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; text-align: center;">'
        '<div style="background: rgba(30, 41, 59, 0.6); border-radius: 6px; padding: 8px 4px; border: 1px solid rgba(234, 179, 8, 0.35);">'
        '<div style="font-size: 0.75rem; color: #facc15; font-weight: 700; letter-spacing: 0.05em;">GP (ทอง)</div>'
        f'<div style="font-size: 1.25rem; font-weight: 700; color: #fef08a; line-height: 1.2;">{curr["gp"]}</div>'
        '</div>'
        '<div style="background: rgba(30, 41, 59, 0.6); border-radius: 6px; padding: 8px 4px; border: 1px solid rgba(203, 213, 225, 0.35);">'
        '<div style="font-size: 0.75rem; color: #cbd5e1; font-weight: 700; letter-spacing: 0.05em;">SP (เงิน)</div>'
        f'<div style="font-size: 1.25rem; font-weight: 700; color: #f8fafc; line-height: 1.2;">{curr["sp"]}</div>'
        '</div>'
        '<div style="background: rgba(30, 41, 59, 0.6); border-radius: 6px; padding: 8px 4px; border: 1px solid rgba(249, 115, 22, 0.35);">'
        '<div style="font-size: 0.75rem; color: #fb923c; font-weight: 700; letter-spacing: 0.05em;">CP (ทองแดง)</div>'
        f'<div style="font-size: 1.25rem; font-weight: 700; color: #fed7aa; line-height: 1.2;">{curr["cp"]}</div>'
        '</div>'
        '</div>'
        '</div>'
    )
    st.markdown(pouch_html, unsafe_allow_html=True)

    if not inventory:
        st.info("กระเป๋าสัมภาระว่างเปล่า")
    else:
        st.markdown("##### 📦 รายการสิ่งของในกระเป๋า")
        catalog = state_manager._get_item_catalog()
        bound_cursed_items = []
        for idx, item in enumerate(inventory):
            if not isinstance(item, dict):
                continue
            item_id = item.get("item_id", "unknown")
            qty = item.get("quantity", 1)
            is_equipped = item.get("equipped", False)
            info = catalog.get(item_id, {})
            if not info and world:
                info = world.get("generated_items", {}).get(item_id, {})

            is_id = item.get("identified", info.get("identified", True))
            is_bound = item.get("cannot_unequip", False)
            if is_bound:
                bound_cursed_items.append((item_id, info.get("name", item_id)))

            if not is_id:
                item_name = info.get("unidentified_name", item.get("unidentified_name", "Unidentified Item"))
                id_tag = " ❓ *(ยังไม่ระบุ)*"
            else:
                item_name = info.get("name", item.get("name", item_id.replace("_", " ").title()))
                id_tag = ""

            itype = info.get("type", "misc")
            islot = info.get("slot", "")
            slot_tag = f" `[{islot}]`" if islot else ""
            status_tag = " 🟢 **(สวมใส่อยู่)**" if is_equipped else ""
            curse_tag = " 🔒 *(สาปติดตัว)*" if is_bound else ""
            fresh_tag = f" ⏳ (สดอีก {item['freshness_days']} วัน)" if "freshness_days" in item and not is_bound and is_id else ""

            price_str = ""
            if "price_cp" in info:
                price_str = f" • {info['price_cp']} CP"
            elif "price_sp" in info:
                price_str = f" • {info['price_sp']} SP"
            elif info.get("value_gold", 0) > 0:
                price_str = f" • {info['value_gold']} GP"

            col_i1, col_i2 = st.columns([3, 1])
            with col_i1:
                st.markdown(f"**{item_name}** x{qty}{slot_tag}{status_tag}{curse_tag}{id_tag}{fresh_tag}{price_str}")
                desc = info.get("description", "")
                if desc:
                    st.caption(desc)
            with col_i2:
                if not is_id:
                    if st.button("ตรวจสอบ", key=f"dlg_inv_id_{idx}_{item_id}", use_container_width=True):
                        chk = state_manager.resolve_check("INT", "medium", player, skill="Arcana")
                        ok_id = state_manager.identify_item(player, item_id, chk["total"], world)
                        if ok_id:
                            st.toast(f"ตรวจสอบสำเร็จ: {item_name}!")
                        else:
                            st.toast("ตรวจสอบล้มเหลว")
                        auto_save()
                        st.rerun()
                elif itype in ("wearable", "weapon"):
                    if is_equipped:
                        if st.button("ถอดออก", key=f"dlg_inv_uneq_{idx}_{item_id}", use_container_width=True):
                            ok_un, msg_un = state_manager.unequip_item(item_id, player)
                            if not ok_un:
                                st.error(msg_un)
                            auto_save()
                            st.rerun()
                    else:
                        if st.button("สวมใส่", key=f"dlg_inv_eq_{idx}_{item_id}", use_container_width=True):
                            ok_eq, msg_eq = state_manager.equip_item(item_id, player, world)
                            if not ok_eq:
                                st.error(msg_eq)
                            auto_save()
                            st.rerun()
                elif itype in ("consumable", "food"):
                    if st.button("ใช้งาน", key=f"dlg_inv_use_{idx}_{item_id}", use_container_width=True):
                        ok_u, msg_u, res_u = state_manager.use_consumable(item_id, player)
                        if ok_u:
                            st.toast(f"ใช้งาน {item_name} แล้ว!")
                        else:
                            st.error(msg_u)
                        auto_save()
                        st.rerun()

        if bound_cursed_items:
            st.markdown("---")
            st.error("🔒 คุณสวมใส่ไอเทมต้องสาปที่ไม่สามารถถอดออกได้เอง")
            current_room = dungeon_manager.get_current_room(world)
            is_town_or_safe = current_room and (current_room.get("is_safe") or current_room.get("type") == "town")
            if is_town_or_safe:
                if st.button("⛪ นักบวชประจำเมือง: ทำพิธีถอนคำสาป (50 GP)", use_container_width=True):
                    ok_c, msg_c = state_manager.pay_cleric_remove_curse(player, world_state=world)
                    if ok_c:
                        st.toast(msg_c)
                    else:
                        st.error(msg_c)
                    auto_save()
                    st.rerun()

        # Camp cooking section
        combat_active = world.get("combat_state") is not None
        if not combat_active:
            st.markdown("---")
            st.markdown("##### 🍳 Camp Cooking (ปรุงอาหารแคมป์)")
            food_candidates = [
                i for i in inventory
                if isinstance(i, dict) and (
                    i.get("raw_food") or i.get("type") in ("raw_food", "food", "rotten_food")
                    or "freshness_days" in i or i.get("item_id") in ("raw_food", "raw_meat", "wild_vegetables", "rotten_food", "trail_rations")
                )
            ]
            if len(food_candidates) == 0:
                st.caption("ไม่มีวัตถุดิบทำอาหาร สามารถหาผักป่าหรือล่าสัตว์เพื่อนำมาปรุงได้")
            else:
                f_names = [f"{it.get('name', it.get('item_id'))} (x{it.get('quantity', 1)})" for it in food_candidates]
                c_idx1 = st.selectbox("วัตถุดิบ 1", range(len(food_candidates)), format_func=lambda x: f_names[x], key="dlg_cook_ing1")
                c_idx2 = st.selectbox("วัตถุดิบ 2", range(len(food_candidates)), format_func=lambda x: f_names[x], key="dlg_cook_ing2")
                if st.button("🍲 ปรุงอาหารแคมป์ (DC 12)", use_container_width=True, key="dlg_btn_cook"):
                    res_c = state_manager.cook_meal(player, food_candidates[c_idx1], food_candidates[c_idx2])
                    if res_c.get("success"):
                        st.toast("ปรุงอาหารสำเร็จ: Hearty Stew!")
                    elif res_c.get("burned"):
                        st.toast("อาหารไหม้!")
                    else:
                        st.error(res_c.get("message", "Cooking failed."))
                    auto_save()
                    st.rerun()


@st.dialog("📜 สมุดบันทึกภารกิจ (Quest Journal)", width="large")
def show_quests_dialog():
    world = st.session_state.get("world_state")
    if not world:
        return
    q_log = world.get("quest_log", {})
    main_q = q_log.get("main", [])
    side_q = q_log.get("side", [])
    if not main_q and not side_q:
        st.info("ไม่มีภารกิจค้างอยู่ในสมุดบันทึกในขณะนี้")
    else:
        if main_q:
            st.markdown("##### 🌟 ภารกิจหลัก (Main Quests)")
            for q in main_q:
                with st.container(border=True):
                    st.markdown(f"**{q.get('title')}** `({q.get('status', 'active')})`")
                    if q.get("description"):
                        st.caption(q["description"])
                    for obj in q.get("objectives", []):
                        chk = "✅" if obj.get("done") else "⬜"
                        st.markdown(f"- {chk} {obj.get('description', 'Objective')}")
        if side_q:
            st.markdown("##### 📌 ภารกิจรอง (Side Quests)")
            for q in side_q:
                with st.container(border=True):
                    st.markdown(f"**{q.get('title')}** `({q.get('status', 'active')})`")
                    if q.get("description"):
                        st.caption(q["description"])
                    for obj in q.get("objectives", []):
                        chk = "✅" if obj.get("done") else "⬜"
                        st.markdown(f"- {chk} {obj.get('description', 'Objective')}")


@st.dialog("✨ ตำราเวทมนตร์ (Spellbook)", width="large")
def show_spells_dialog():
    player = st.session_state.get("player_state")
    if not player:
        return
    spell_slots = player.get("spell_slots", {})
    known_spells = player.get("known_spells", [])
    if not spell_slots and not known_spells:
        st.info("ตัวละครไม่มีความสามารถในการร่ายเวทมนตร์")
        return

    if player.get("concentration"):
        st.warning(f"🧠 **กำลังเพ่งสมาธิเวท (Active Concentration):** `{player['concentration']}`")

    if spell_slots:
        st.markdown("##### 🔮 ช่องร่ายเวท (Spell Slots)")
        slot_cols = st.columns(max(1, len(spell_slots)))
        for idx, (lvl_s, sinfo) in enumerate(spell_slots.items()):
            if isinstance(sinfo, dict):
                with slot_cols[idx]:
                    st.metric(f"Level {lvl_s}", f"{sinfo.get('current', 0)} / {sinfo.get('max', 0)}")

    world_for_spells = st.session_state.get("world_state")
    sp_cat = get_display_spell_catalog(world_for_spells)

    if known_spells:
        st.markdown("##### 📖 รายการเวทมนตร์ที่เรียนรู้ (Known Spells)")
        for sp_id in known_spells:
            sp_data = sp_cat.get(sp_id, {})
            sp_name = sp_data.get("name", sp_id.replace("_", " ").title())
            sp_lvl = sp_data.get("level", 0)
            lvl_lbl = "Cantrip" if sp_lvl == 0 else f"Level {sp_lvl}"
            cost_lbl = "Bonus Action" if sp_data.get("action_cost") == "bonus_action" else ("Reaction" if sp_data.get("action_cost") == "reaction" else "Action")
            conc_lbl = " • 🧠 Concentration" if sp_data.get("concentration") else ""
            with st.container(border=True):
                st.markdown(f"**{sp_name}** `{lvl_lbl}` • `{cost_lbl}`{conc_lbl}")
                if sp_data.get("description"):
                    st.caption(sp_data["description"])

    # Allow spellcasters to learn additional class spells unlocked up to their current max spell level
    cls_name = player.get("class", "Wizard")
    max_cast_lvl = max([0] + [int(k) for k, v in spell_slots.items() if isinstance(v, dict) and v.get("max", 0) > 0])
    learnable = [
        sid for sid, sdata in sp_cat.items()
        if sid not in known_spells
        and sdata.get("level", 0) <= max_cast_lvl
        and cls_name in sdata.get("class", [])
    ]
    if learnable:
        st.markdown("---")
        with st.expander("📚 ศึกษาและเตรียมเวทมนตร์เพิ่มเติมในคลาสของคุณ (Prepare / Learn Class Spells)"):
            sel_new_sp = st.selectbox(
                "เลือกเวทมนตร์ที่ต้องการเรียนรู้เพิ่ม:",
                learnable,
                format_func=lambda sid: f"{sp_cat[sid].get('name', sid)} (Lv {sp_cat[sid].get('level', 0)}) — {sp_cat[sid].get('description', '')}",
                key="dlg_learn_new_spell_sel",
            )
            if st.button("📖 เรียนรู้เวทมนตร์นี้", use_container_width=True, key="dlg_btn_learn_spell"):
                state_manager.learn_spell(sel_new_sp, player, world_for_spells)
                auto_save()
                st.toast(f"เรียนรู้ {sp_cat[sel_new_sp].get('name', sel_new_sp)} เรียบร้อย!")
                st.rerun()


def _maybe_rest_ambush(
    current_room: Optional[Dict[str, Any]],
    world: Dict[str, Any],
    player: Dict[str, Any],
    chance: float,
    message_prefix: str,
    message_suffix: str,
    check_town: bool,
) -> Optional[str]:
    """
    Check for an ambush during a rest and initiate combat if triggered.
    Maintains exact short-circuit order:
        is_unsafe and random.random() < chance and enc_table
    Returns:
        Ambush narrative message if ambush occurs, else None.
    """
    if check_town:
        is_unsafe = bool(
            current_room
            and (not current_room.get("is_safe"))
            and (current_room.get("type") != "town")
            and (current_room.get("id") not in world.get("cleared_rooms", []))
        )
    else:
        is_unsafe = bool(
            current_room
            and (not current_room.get("is_safe"))
            and (current_room.get("id") not in world.get("cleared_rooms", []))
        )

    enc_table = current_room.get("encounter_table", ["wolf"]) if current_room else ["wolf"]

    if is_unsafe and random.random() < chance and enc_table:
        ambush_enemy = random.choice(enc_table)
        combat_manager.start_combat([ambush_enemy], player, world)
        enemy_title = ambush_enemy.replace("_", " ").title()
        return f"{message_prefix} **{enemy_title}** {message_suffix}"

    return None


@st.dialog("⛺ พักผ่อน & ตั้งแคมป์ (Rest & Camp)", width="medium")
def show_camp_dialog():
    player = st.session_state.get("player_state")
    world = st.session_state.get("world_state")
    if not player or not world:
        return
    current_room = dungeon_manager.get_current_room(world)
    combat_active = world.get("combat_state") is not None
    if combat_active:
        st.warning("⚔️ ไม่สามารถพักผ่อนได้ขณะอยู่ในการต่อสู้!")
        return

    # Outdoor camp setup toggle
    if current_room and current_room.get("type") != "town":
        is_camp = world.get("at_camp", False)
        if not is_camp:
            if st.button("🏕️ กางเต็นท์ตั้งแคมป์ (Set Up Camp)", use_container_width=True, key="dlg_btn_setup_camp"):
                world["at_camp"] = True
                auto_save()
                st.toast("ตั้งแคมป์เรียบร้อยแล้ว!")
                st.rerun()
        else:
            if st.button("🎒 เก็บแคมป์เตรียมเดินทางต่อ (Pack Up)", use_container_width=True, key="dlg_btn_pack_camp"):
                world["at_camp"] = False
                auto_save()
                st.toast("เก็บแคมป์แล้ว!")
                st.rerun()
        st.markdown("---")

    st.markdown("##### ☕ พักช่วงสั้น (Short Rest - 1 hr)")
    st.caption("นั่งพักผ่อนสั้นๆ ตรวจเช็กอาวุธ ฟื้นฟูท่าไม้ตายอาวุธ (Weapon Actions)")
    if st.button("☕ Take Short Rest", key="dlg_btn_short_rest", use_container_width=True):
        ambush_msg = _maybe_rest_ambush(
            current_room,
            world,
            player,
            chance=0.15,
            message_prefix="🚨 **Ambush!** ขณะกำลังนั่งพักผ่อนสั้นๆ ศัตรู",
            message_suffix="พุ่งเข้าจู่โจมคุณอย่างกะทันหัน!",
            check_town=True,
        )
        if ambush_msg:
            st.session_state["narrative_log"].append({
                "role": "assistant",
                "content": ambush_msg
            })
        else:
            state_manager.perform_short_rest(player, world)
            st.session_state["narrative_log"].append({
                "role": "assistant",
                "content": "You take a short rest, catching your breath and checking your gear. Weapon actions recharged!"
            })
        auto_save()
        st.rerun()

    st.markdown("---")
    st.markdown("##### ⛺ พักผ่อนยาว (Long Rest - 8 hrs)")
    if current_room and current_room.get("type") == "town":
        st.caption("พักค้างคืนที่โรงเตี๊ยมในเมือง ฟื้นฟูพลังชีวิต (HP) และสล็อตเวทมนตร์เต็มจำนวน")
        if st.button("⛺ Take Long Rest (Town Inn)", key="dlg_btn_long_inn", use_container_width=True, type="primary"):
            state_manager.long_rest(world, player)
            st.session_state["narrative_log"].append({
                "role": "assistant",
                "content": f"You spend the night resting comfortably at Riverside Village. HP fully restored! Time is now **Day {world['game_time']['day']}, Morning**."
            })
            auto_save()
            st.rerun()
    else:
        st.caption("ตั้งแคมป์พักแรมกลางแจ้ง ต้องใช้เสบียง (Trail Rations) 1 ชุด และระวังศัตรูซุ่มโจมตี")
        if st.button("⛺ Take Long Rest (Camp Outdoors)", key="dlg_btn_long_camp", use_container_width=True, type="primary"):
            ambush_msg = _maybe_rest_ambush(
                current_room,
                world,
                player,
                chance=0.25,
                message_prefix="🚨 **Night Ambush!** กลางดึกขณะกำลังหลับพักแรม กลิ่นคาวดึงดูด",
                message_suffix="มาจู่โจมแคมป์ของคุณ!",
                check_town=False,
            )
            if ambush_msg:
                st.session_state["narrative_log"].append({
                    "role": "assistant",
                    "content": ambush_msg
                })
            else:
                state_manager.long_rest(world, player)
                world["at_camp"] = True
                st.session_state["narrative_log"].append({
                    "role": "assistant",
                    "content": f"⛺ You set up camp and rest safely through the night. HP fully restored! Time is now **Day {world['game_time']['day']}, Morning**."
                })
            auto_save()
            st.rerun()
