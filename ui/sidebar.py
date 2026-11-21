"""
ui/sidebar.py — Character summary sidebar and AI Engine configuration (Groq, Gemini, Ollama).
"""

import re
import time
import streamlit as st

import dungeon_manager
import llm_handler
import state_manager
from ui.session import (
    auto_save,
    get_active_quest_count,
    get_known_spell_count,
    get_player_currency,
    get_ration_count,
)
from ui.dialogs import (
    show_camp_dialog,
    show_character_dialog,
    show_inventory_dialog,
    show_quests_dialog,
    show_spells_dialog,
)


def render_sidebar():
    """Render character sheet, stats, location, and time in the sidebar."""
    player = st.session_state.get("player_state")
    world = st.session_state.get("world_state")
    if not player or not world:
        return

    # ── Pinned Always-Visible Top Section ──────────────────────────────────────
    p_name = player.get("name", "Adventurer")
    p_lvl = player.get("level", 1)
    p_race = player.get("race", "")
    p_cls = player.get("class_name", player.get("class", ""))
    p_sub = player.get("subclass_name") or (player.get("subclass", "").replace("_", " ").title() if player.get("subclass") else "")
    sub_tag = f" ({p_sub})" if p_sub else ""
    st.sidebar.markdown(f"### 🛡️ {p_name}")
    st.sidebar.caption(f"Level {p_lvl} &bull; {p_race} {p_cls}{sub_tag}")

    # HP & Vitals (Non-truncating compact view)
    hp_info = player.get("hp", {"current": 10, "max": 10})
    cur_hp = hp_info.get("current", 0)
    max_hp = hp_info.get("max", 10)
    hp_pct = max(0.0, min(1.0, cur_hp / max(1, max_hp)))
    ac = player.get("ac", 10)
    xp_cur = player.get("xp_current", 0)
    next_xp = state_manager.LEVEL_THRESHOLDS.get(p_lvl + 1, "MAX")
    insp = state_manager.get_inspiration(player)
    max_insp = player.get("max_inspiration", state_manager.MAX_INSPIRATION)

    st.sidebar.markdown(f"**❤️ HP:** `{cur_hp} / {max_hp}` &nbsp;&nbsp;|&nbsp;&nbsp; **🛡️ AC:** `{ac}`")
    st.sidebar.progress(hp_pct)
    st.sidebar.markdown(f"⭐ **XP:** `{xp_cur} / {next_xp}` &nbsp;&nbsp;|&nbsp;&nbsp; 💡 **Insp:** `{insp} / {max_insp}`")

    # Time & Location
    gt = world.get("game_time", {})
    day = gt.get("day", 1)
    period = gt.get("period", "morning").capitalize()
    current_room = dungeon_manager.get_current_room(world)
    room_name = current_room.get("name", "Unknown") if current_room else "Unknown"
    room_type = current_room.get("type", "wilderness").capitalize() if current_room else ""

    st.sidebar.markdown(f"📍 **{room_name}** `({room_type})`")
    st.sidebar.caption(f"⏳ **Day {day}**, {period}")

    # Coin Pouch & Provisions
    curr = get_player_currency(player)
    gp, sp, cp = curr.get("gp", 0), curr.get("sp", 0), curr.get("cp", 0)
    rations = get_ration_count(player)
    st.sidebar.markdown(
        f"💰 **เงิน:** `{gp} GP` &nbsp; `{sp} SP` &nbsp; `{cp} CP`  \n"
        f"🍖 **เสบียง:** `{rations}` ชุด"
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown("##### 🧭 เมนูหลัก (Game Sheets)")

    if st.sidebar.button("👤 ข้อมูลตัวละคร (Character)", use_container_width=True, key="sb_btn_char"):
        show_character_dialog()

    inv_count = len(player.get("inventory", []))
    if st.sidebar.button(f"🎒 กระเป๋าสัมภาระ ({inv_count} ชิ้น)", use_container_width=True, key="sb_btn_inv"):
        show_inventory_dialog()

    active_quests_cnt = get_active_quest_count(world)
    if st.sidebar.button(f"📜 บันทึกภารกิจ ({active_quests_cnt} รายการ)", use_container_width=True, key="sb_btn_quests"):
        show_quests_dialog()

    spell_cnt = get_known_spell_count(player)
    if st.sidebar.button(f"✨ คัมภีร์เวทมนตร์ ({spell_cnt})", use_container_width=True, key="sb_btn_spells"):
        show_spells_dialog()

    camp_status = "🏕️ ในแคมป์" if world.get("at_camp") else "⛺ พักผ่อน"
    if st.sidebar.button(f"⛺ พักผ่อน & แคมป์ ({camp_status})", use_container_width=True, key="sb_btn_camp"):
        show_camp_dialog()

    # Active Conditions & Wanted Notice
    active_conds = player.get("active_conditions", [])
    if active_conds:
        formatted_conds = []
        for c in active_conds:
            if isinstance(c, str):
                formatted_conds.append(c)
            elif isinstance(c, dict):
                c_name = c.get("name", c.get("condition", "unknown"))
                dur = c.get("duration")
                formatted_conds.append(f"{c_name} ({dur} rds)" if dur else c_name)
        st.sidebar.warning(f"⚠️ Conditions: {', '.join(formatted_conds)}")

    if player.get("is_wanted") or player.get("bounty", 0) > 0:
        bounty_val = player.get("bounty", 0)
        st.sidebar.error(f"🚨 **WANTED!** Bounty: **{bounty_val} GP**")
        if current_room and current_room.get("type") == "town":
            if st.sidebar.button(f"🏛️ Pay Off Bounty ({bounty_val} GP)", use_container_width=True):
                ok_b, msg_b = state_manager.pay_bounty(player)
                if ok_b:
                    st.toast(msg_b)
                    st.session_state["narrative_log"].append({
                        "role": "assistant",
                        "content": f"🏛️ {msg_b}"
                    })
                else:
                    st.sidebar.error(msg_b)
                auto_save()
                st.rerun()

    # ── AI Engine & Model Settings ───────────────────────────────────────────
    active_engine = llm_handler.get_active_engine()
    gemini_key = llm_handler.get_gemini_api_key() or ""
    groq_key = llm_handler.get_groq_api_key() or ""
    installed_models = llm_handler.get_installed_models()

    # Expand automatically if no cloud key is configured and Ollama is not running
    no_engine_ready = (not groq_key) and (not gemini_key) and (not installed_models)

    with st.sidebar.expander("🤖 Dungeon Master AI (Engine & Models)", expanded=no_engine_ready):
        engine_opts = [
            "🚀 Groq Cloud",
            "⚡ Google Gemini",
            "🏠 Local Ollama (Optional)",
        ]
        # Default to Groq Cloud first when Ollama is not installed on the machine
        if active_engine == "gemini" and gemini_key:
            default_engine_idx = 1
        elif active_engine == "ollama" and installed_models:
            default_engine_idx = 2
        else:
            default_engine_idx = 0

        chosen_engine_label = st.radio(
            "เลือก Engine AI:",
            options=engine_opts,
            index=default_engine_idx,
            key="sb_engine_radio"
        )
        is_groq = "Groq" in chosen_engine_label
        is_gemini = "Gemini" in chosen_engine_label

        target_engine = "groq" if is_groq else ("gemini" if is_gemini else "ollama")
        if target_engine != active_engine:
            if target_engine == "groq" and groq_key:
                llm_handler.set_active_engine("groq")
                active_engine = "groq"
                st.toast("⚡ สลับมาใช้ Groq Cloud เรียบร้อย")
                st.rerun()
            elif target_engine == "gemini" and gemini_key:
                llm_handler.set_active_engine("gemini")
                active_engine = "gemini"
                st.toast("⚡ สลับมาใช้ Google Gemini เรียบร้อย")
                st.rerun()
            elif target_engine == "ollama" and installed_models:
                llm_handler.set_active_engine("ollama")
                active_engine = "ollama"
                st.toast("🏠 สลับมาใช้ Local Ollama เรียบร้อย")
                st.rerun()

        if is_groq:
            st.markdown("##### 🚀 Groq Cloud (LPU Ultra-Fast)")
            st.caption("เร็ว 1-2 วินาที • ใช้งานผ่าน Cloud ได้ทันทีโดยไม่ต้องมี Ollama ในเครื่อง")

            grq_input = st.text_input(
                "Groq API Key:",
                value=groq_key,
                type="password",
                placeholder="gsk_...",
                key="sb_groq_key_input",
                help="รับ API Key ฟรีได้ที่ https://console.groq.com/keys"
            )

            groq_model_opts = llm_handler.get_groq_available_models(api_key=grq_input or groq_key)
            recommended_models = ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"]
            for rm in reversed(recommended_models):
                if rm in groq_model_opts:
                    groq_model_opts.remove(rm)
                    groq_model_opts.insert(0, rm)
            if "Other (กำหนดเอง)" not in groq_model_opts:
                groq_model_opts.append("Other (กำหนดเอง)")

            cur_grq_cfg = llm_handler.load_groq_config()
            cur_grq_model = cur_grq_cfg.get("model", llm_handler.DEFAULT_GROQ_MODEL)
            if cur_grq_model not in groq_model_opts:
                groq_model_opts.insert(0, cur_grq_model)

            grq_idx = groq_model_opts.index(cur_grq_model) if cur_grq_model in groq_model_opts else 0
            chosen_grq_model = st.selectbox(
                "รุ่นโมเดล Groq:",
                options=groq_model_opts,
                index=grq_idx,
                key="sb_groq_model_selector",
                help="เลือกโมเดลที่ต้องการใช้งาน เช่น qwen/qwen3.8-27b"
            )
            if chosen_grq_model == "Other (กำหนดเอง)":
                custom_grq_name = st.text_input(
                    "ป้อนชื่อรุ่นโมเดล:",
                    value=cur_grq_model if cur_grq_model not in ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"] else "",
                    placeholder="e.g. llama-3.3-70b-versatile",
                    key="sb_groq_custom_model_input"
                )
                if custom_grq_name.strip():
                    chosen_grq_model = custom_grq_name.strip()

            if chosen_grq_model != cur_grq_model and chosen_grq_model != "Other (กำหนดเอง)":
                llm_handler.save_groq_config(model=chosen_grq_model)
                st.toast(f"เปลี่ยนโมเดล Groq เป็น: {chosen_grq_model}")

            c_save_g, c_test_g = st.columns(2)
            with c_save_g:
                if st.button("💾 บันทึก Key", use_container_width=True, key="btn_save_groq_key"):
                    if not grq_input.strip():
                        st.error("กรุณาระบุ Groq API Key")
                    else:
                        saved_ok = llm_handler.save_groq_config(api_key=grq_input, model=chosen_grq_model, engine="groq")
                        if saved_ok:
                            st.toast("บันทึก Groq API Key และเปิดใช้งานสำเร็จ!")
                            st.rerun()
                        else:
                            st.error("บันทึกไม่สำเร็จ")
            with c_test_g:
                if st.button("🔌 ทดสอบ", use_container_width=True, key="btn_test_groq"):
                    if not grq_input.strip():
                        st.error("กรุณาระบุ Groq API Key")
                    else:
                        with st.spinner("กำลังทดสอบการเชื่อมต่อ Groq..."):
                            ok_conn, msg_conn = llm_handler.test_groq_connection(grq_input, model=chosen_grq_model)
                        if ok_conn:
                            st.success(f"🟢 {msg_conn}")
                            if not llm_handler.is_saved_groq_pair(grq_input, chosen_grq_model):
                                st.info("ค่าที่ทดสอบนี้ยังไม่ได้ถูกบันทึก เกมยังใช้ Key/โมเดลเดิมอยู่ — กด '💾 บันทึก Key' เพื่อใช้ค่านี้จริง")
                        else:
                            st.error(f"🔴 {msg_conn}")

            # Groq engine status display
            grq_status = llm_handler.get_groq_status()
            if grq_status.get("session_disabled"):
                st.error(f"⚠️ **สถานะ:** ถูกระงับในเซสชันนี้ ({grq_status.get('disabled_reason', 'Error')})")
            elif grq_status.get("cooldown_seconds_remaining", 0) > 0:
                st.warning(f"⏳ **สถานะ:** พักคูลดาวน์โควต้า (HTTP 429) — เหลืออีก {grq_status['cooldown_seconds_remaining']} วินาที")
            elif groq_key:
                st.markdown("🟢 **สถานะ:** พร้อมใช้งาน (Groq LPU)")
            else:
                st.markdown("🟡 **สถานะ:** รอใส่ API Key")
            st.markdown("[👉 ตรวจสอบโควตาและรับ Groq API Key ฟรี](https://console.groq.com/keys)")

        elif is_gemini:
            st.markdown("##### ⚡ Google Gemini Flash")
            st.caption("ใช้งานผ่าน Cloud ฟรีจาก Google AI Studio โดยไม่ต้องมี Ollama ในเครื่อง")

            k_input = st.text_input(
                "Gemini API Key:",
                value=gemini_key,
                type="password",
                placeholder="AIzaSy...",
                key="sb_gemini_key_input",
                help="กดรับ API Key ฟรีได้ที่ https://aistudio.google.com"
            )

            gemini_model_opts = ["gemini-3.6-flash", "gemini-3.5-flash-lite"]
            cur_cfg = llm_handler.load_gemini_config()
            cur_g_model = cur_cfg.get("model", llm_handler.DEFAULT_GEMINI_MODEL)
            if cur_g_model in llm_handler.KNOWN_SHUTDOWN_MODELS:
                cur_g_model = llm_handler.DEFAULT_GEMINI_MODEL
            if cur_g_model not in gemini_model_opts:
                gemini_model_opts.append(cur_g_model)
            gemini_model_opts.append("Other (กำหนดเอง)")

            g_idx = gemini_model_opts.index(cur_g_model) if cur_g_model in gemini_model_opts else 0
            chosen_g_model = st.selectbox(
                "รุ่นโมเดล Gemini:",
                options=gemini_model_opts,
                index=g_idx,
                key="sb_gemini_model_selector",
                help="เลือกรุ่นโมเดล Gemini จาก Google AI Studio"
            )
            if chosen_g_model == "Other (กำหนดเอง)":
                custom_model_name = st.text_input(
                    "ป้อนชื่อรุ่นโมเดล:",
                    value=cur_g_model if cur_g_model not in ["gemini-3.6-flash", "gemini-3.5-flash-lite"] else "",
                    placeholder="e.g. gemini-3.6-flash",
                    key="sb_gemini_custom_model_input"
                )
                chosen_g_model = custom_model_name.strip()

            is_valid_model = bool(re.match(r"^[a-z0-9][a-z0-9.\-]*$", chosen_g_model))

            col_k1, col_k2 = st.columns(2)
            with col_k1:
                if st.button("💾 บันทึก Key", use_container_width=True, key="btn_save_gemini_key"):
                    if not k_input:
                        st.warning("กรุณากรอก API Key ก่อนบันทึก")
                    elif not is_valid_model:
                        st.error("ชื่อรุ่นโมเดลไม่ถูกต้อง (อนุญาตเฉพาะตัวพิมพ์เล็ก a-z, 0-9, จุด, และขีด เช่น gemini-3.6-flash)")
                    else:
                        saved_ok = llm_handler.save_gemini_config(api_key=k_input, model=chosen_g_model, engine="gemini")
                        if saved_ok:
                            llm_handler.reset_gemini_state()
                            st.toast("บันทึก Gemini API Key เรียบร้อยแล้ว!")
                            st.rerun()
                        else:
                            st.error("ไม่สามารถบันทึกการตั้งค่า Gemini ได้ กรุณาตรวจสอบข้อมูล")
            with col_k2:
                if st.button("🔌 ทดสอบ", use_container_width=True, key="btn_test_gemini"):
                    if not k_input:
                        st.warning("กรุณาใส่ API Key")
                    elif not is_valid_model:
                        st.error("ชื่อรุ่นโมเดลไม่ถูกต้อง (อนุญาตเฉพาะตัวพิมพ์เล็ก a-z, 0-9, จุด, และขีด เช่น gemini-3.6-flash)")
                    else:
                        ok_conn, msg_conn = llm_handler.test_gemini_connection(k_input, model=chosen_g_model)
                        if ok_conn:
                            st.success(msg_conn)
                            if not llm_handler.is_saved_gemini_pair(k_input, chosen_g_model):
                                st.info("ค่าที่ทดสอบนี้ยังไม่ได้ถูกบันทึก เกมยังใช้ Key/โมเดลเดิมอยู่ — กด '💾 บันทึก Key' เพื่อใช้ค่านี้จริง")
                        else:
                            st.error(f"ทดสอบไม่สำเร็จ (เกมปัจจุบันยังใช้ค่าเดิมอยู่): {msg_conn}")

            # Gemini engine status & Daily calls display
            g_status = llm_handler.get_gemini_status()
            calls_today = g_status.get("calls_today", 0)

            st.markdown(f"📊 **Gemini calls today:** `{calls_today}` *(รีเซ็ตเที่ยงคืน Pacific)*")

            if g_status.get("session_disabled"):
                st.error(f"⚠️ **สถานะ:** ถูกระงับในเซสชันนี้ ({g_status.get('disabled_reason', 'Error')})")
            elif g_status.get("cooldown_seconds_remaining", 0) > 0:
                st.warning(f"⏳ **สถานะ:** พักคูลดาวน์โควต้า (HTTP 429) — เหลืออีก {g_status['cooldown_seconds_remaining']} วินาที")
            elif gemini_key:
                st.markdown("🟢 **สถานะ:** พร้อมใช้งาน (Gemini Flash)")
            else:
                st.markdown("🟡 **สถานะ:** รอใส่ API Key")

            # User-customizable daily quota limit (from https://aistudio.google.com/rate-limit)
            user_lim = llm_handler.get_gemini_user_daily_limit()
            user_lim_input = st.number_input(
                "กำหนดขีดจำกัดจำนวนครั้งต่อวัน (Optional):",
                min_value=0,
                max_value=100000,
                value=int(user_lim) if user_lim is not None else 0,
                step=50,
                key="sb_gemini_daily_limit_input",
                help="ป้อนขีดจำกัดจากหน้า AI Studio ของคุณ (https://aistudio.google.com/rate-limit) เพื่อให้ระบบช่วยเตือน (ใส่ 0 หากไม่ระบุ)"
            )
            if user_lim_input > 0:
                if user_lim != user_lim_input:
                    llm_handler.set_gemini_user_daily_limit(user_lim_input)
                pct = calls_today / user_lim_input
                if pct >= 1.0:
                    st.error(f"🚨 ใช้โควต้าครบกำหนด {calls_today}/{user_lim_input} ครั้งแล้ว")
                elif pct >= 0.8:
                    st.warning(f"⚠️ เรียกใช้งานเกิน 80% ของขีดจำกัดที่ตั้งไว้ ({calls_today}/{user_lim_input} ครั้ง - {int(pct * 100)}%)")
            elif user_lim is not None and user_lim_input == 0:
                llm_handler.set_gemini_user_daily_limit(None)

            st.markdown("[👉 คลิกที่นี่เพื่อรับ Gemini API Key ฟรี](https://aistudio.google.com/app/apikey)")

        else:
            # Local Ollama Mode
            if active_engine in ("gemini", "groq") and st.button("สลับมาใช้ Ollama", key="btn_switch_ollama"):
                llm_handler.set_active_engine("ollama")
                st.rerun()

            if installed_models:
                st.markdown("🟢 **สถานะ:** เชื่อมต่อสำเร็จ (Online)")
                if "selected_model" not in st.session_state or st.session_state["selected_model"] not in installed_models:
                    st.session_state["selected_model"] = llm_handler.resolve_model()

                curr_idx = installed_models.index(st.session_state["selected_model"]) if st.session_state["selected_model"] in installed_models else 0
                chosen_m = st.selectbox(
                    "โมเดลในเครื่อง:",
                    options=installed_models,
                    index=curr_idx,
                    key="sb_model_selector",
                    help="เลือกโมเดล AI ในเครื่องที่จะใช้บรรยายเนื้อเรื่อง"
                )
                if chosen_m != st.session_state.get("selected_model"):
                    st.session_state["selected_model"] = chosen_m
                    st.toast(f"เปลี่ยนโมเดล AI เป็น: {chosen_m}")
            else:
                st.markdown("⚪ **สถานะ:** ไม่พบ Ollama ในเครื่อง")
                st.info("💡 หากไม่มี Ollama ในเครื่อง แนะนำให้เลือก **🚀 Groq Cloud** หรือ **⚡ Google Gemini** ด้านบนแทน (ใช้งานฟรีและรวดเร็วมาก)")

    st.sidebar.markdown("---")

    col_save, col_exit = st.sidebar.columns(2)
    with col_save:
        if st.button("💾 Save Game", use_container_width=True):
            auto_save()
            st.toast("Game saved successfully!")
    with col_exit:
        if st.button("🚪 Main Menu", use_container_width=True):
            auto_save()
            st.session_state["player_state"] = None
            st.session_state["world_state"] = None
            st.rerun()
