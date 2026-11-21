"""
ui/landing_view.py — Character creation and save game loader landing screen (BG3 Overhaul).
Supports all 8 classes, BG3 subclasses/domains/oaths/patrons, fighting styles, skill choices, and starting spell customization.
"""

from typing import Dict
import streamlit as st

import character_creator
import state_manager
from ui.styles import apply_custom_css
from ui.session import list_saved_characters, load_game


CLASS_FLAVOR = {
    "Fighter": {
        "quote": "คมดาบไม่เคยโกหก... ไม่ว่าจะเผชิญหน้ากับศัตรูหน้าไหน ความแข็งแกร่งและระเบียบวินัยจะนำพาชัยชนะมาให้",
        "desc": "ผู้เชี่ยวชาญการต่อสู้และอาวุธทุกรูปแบบ มี Second Wind, Action Surge และกระบวนท่ายุทธวิธี",
    },
    "Wizard": {
        "quote": "ความรู้คืออำนาจที่ไร้ขอบเขต... เส้นสายมนตราแห่งจักรวาลรอคอยให้ข้าเป็นผู้ร่ายและควบคุม",
        "desc": "จอมเวทผู้ศึกษาตำราเวทลึกลับ สามารถฟื้นฟูสล็อตเวทด้วย Arcane Recovery และร่ายเวททำลายล้าง",
    },
    "Rogue": {
        "quote": "เงามืดคือสหายที่ซื่อสัตย์ที่สุด... ฝีเท้าที่เงียบงันและการโจมตีจุดตายก่อนที่ศัตรูจะทันรู้ตัวคืองานศิลปะของข้า",
        "desc": "ผู้เชี่ยวชาญการลอบเร้น โจมตีจุดตาย Sneak Attack อัตโนมัติ และใช้ Cunning Action พลิกแพลงในเทิร์นเดียว",
    },
    "Cleric": {
        "quote": "ตราบใดที่ศรัทธายังคงอยู่ แสงสว่างแห่งทวยเทพจะคอยปกป้องคุ้มครองพวกเรา และแผดเผาความชั่วร้ายให้มลายสิ้น",
        "desc": "ผู้รับใช้ทวยเทพที่เปี่ยมด้วยพลังศักดิ์สิทธิ์ มี Channel Divinity ทั้งสายรักษาและสายแสงทำลายล้าง",
    },
    "Bard": {
        "quote": "โลกใบนี้คือเวทีการแสดง และทุกการเดินทางคือบทกวีอันยิ่งใหญ่... ฟังเสียงดนตรีของข้า แล้วเจ้าจะก้าวข้ามขีดจำกัด!",
        "desc": "ผู้ใช้เสียงเพลงเป็นเวทมนตร์ ปลุกใจเพื่อนด้วย Bardic Inspiration (+1d6) และใช้เพลงดาบ Flourish",
    },
    "Paladin": {
        "quote": "ด้วยคำสัตย์สาบานอันศักดิ์สิทธิ์ ข้าจะเป็นโล่ให้ผู้อ่อนแอ และเป็นทัณฑ์สวรรค์ฟาดฟันเหล่าอธรรมให้สิ้นซาก!",
        "desc": "อัศวินศักดิ์สิทธิ์ผู้ผสานคมดาบและเวทมนตร์ ฟาดฟันด้วย Divine Smite (+2d8 Radiant) และรักษาด้วย Lay on Hands",
    },
    "Barbarian": {
        "quote": "ปล่อยให้ความเดือดดาลครอบงำ! เกราะเหล็กหรือจะสู้กล้ามเนื้อและสัญชาตญาณดิบที่ไม่รู้จักคำว่าถอย!",
        "desc": "นักรบคลั่งผู้มีเลือดสูงสุดในเกม เข้าสู่โหมด Rage (+2 ดาเมจ & ลดดาเมจกายภาพครึ่งหนึ่ง) และตี Reckless Attack",
    },
    "Warlock": {
        "quote": "พันธสัญญาลี้ลับแลกมาด้วยพลังอำนาจเหนือมนุษย์... ลำแสงมรณะของข้าจะฉีกกระชากทุกสิ่งที่ขวางทาง!",
        "desc": "ผู้ทำพันธสัญญากับตัวตนต่างมิติ ยิงลำแสง Eldritch Blast และฟื้นฟูสล็อตเวททั้งหมดได้ทันทีเมื่อพักสั้น (Short Rest)",
    },
}

RACE_FLAVOR = {
    "Human": "มนุษย์ผู้มีความมุ่งมั่นไม่ย่อท้อและปรับตัวได้อย่างยอดเยี่ยม",
    "Elf": "เอลฟ์ผู้สง่างาม สืบสายเลือดแห่งเฟย์ที่ต้านทานมนต์สะกดและมีประสาทสัมผัสฉับไว",
    "Dwarf": "คนแคระผู้ทรหดอดทน ต้านทานพิษและมีจิตวิญญาณแห่งช่างฝีมือใต้ขุนเขา",
    "Halfling": "ฮาล์ฟลิงผู้คล่องแคล่วและโชคดี มีความกล้าหาญซ่อนอยู่ภายใต้รูปร่างกะทัดรัด",
    "Tiefling": "ไทฟลิงผู้มีสายเลือดแห่งขุมนรก มีความต้านทานไฟและสายตามองทะลุความมืด",
    "Half-Orc": "ฮาล์ฟออร์คผู้ดุดัน แข็งแกร่ง และมีพลังใจในการเอาชีวิตรอดที่ไร้ขีดจำกัด",
    "Dragonborn": "ดรากอนบอร์นผู้สืบสายเลือดจากมังกรโบราณ เปี่ยมด้วยเกียรติยศและลมหายใจมังกร",
}

CLASS_PRIORITY = {
    "Fighter":   ["STR", "CON", "DEX", "WIS", "INT", "CHA"],
    "Wizard":    ["INT", "DEX", "CON", "WIS", "CHA", "STR"],
    "Rogue":     ["DEX", "CON", "INT", "CHA", "WIS", "STR"],
    "Cleric":    ["WIS", "CON", "STR", "DEX", "CHA", "INT"],
    "Bard":      ["CHA", "DEX", "CON", "WIS", "INT", "STR"],
    "Paladin":   ["STR", "CHA", "CON", "WIS", "DEX", "INT"],
    "Barbarian": ["STR", "CON", "DEX", "WIS", "CHA", "INT"],
    "Warlock":   ["CHA", "CON", "DEX", "WIS", "INT", "STR"],
}


def get_recommended_stats(cls_name: str, race_name: str) -> Dict[str, int]:
    """Return an optimal 27-point buy distribution tailored to class and racial stat bonuses."""
    races_cat = character_creator._load_catalog("races_catalog.json")
    race_key = race_name.lower().replace("-", "_").replace(" ", "_")
    r_bonuses = races_cat.get(race_key, {}).get("stat_bonuses", {})
    prio = CLASS_PRIORITY.get(cls_name, CLASS_PRIORITY["Fighter"])

    base = {s: 8 for s in character_creator.STAT_NAMES}
    values = [15, 14, 13, 12, 10, 8]
    for stat, val in zip(prio, values):
        base[stat] = val

    s1, s2 = prio[0], prio[1]
    b1, b2 = r_bonuses.get(s1, 0), r_bonuses.get(s2, 0)
    if b1 == 2 and b2 == 1:
        base[s1] = 14
        base[s2] = 15

    return base


def render_landing_view():
    apply_custom_css()

    st.markdown("""
    <div style="background: linear-gradient(180deg, #131724 0%, #0b0d14 100%);
                border: 1px solid #d4af6a; border-radius: 14px; padding: 22px 24px 18px 24px;
                margin-bottom: 18px; text-align: center;
                box-shadow: 0 10px 32px rgba(0, 0, 0, 0.6), inset 0 0 0 1px rgba(212, 175, 106, 0.15);">
        <div style="font-size: 0.78rem; letter-spacing: 0.16em; color: #d4af6a; text-transform: uppercase; margin-bottom: 6px;">
            ◆ Tabletop D&D 5e &bull; Interactive Narrative RPG ◆
        </div>
        <h1 style="font-family: 'Cinzel', serif; font-size: 2.35rem; margin-bottom: 6px;
                   background: linear-gradient(90deg, #f3d08a, #d4af6a, #f8fafc);
                   -webkit-background-clip: text; -webkit-text-fill-color: transparent;">
            ⚔️ D&D 5e AI DM Engine
        </h1>
        <p style="color: #cbd5e1; font-size: 1.02rem; margin: 0;">
            ผจญภัยสวมบทบาท D&D 5e ขับเคลื่อนด้วย AI &bull; ระบบทักษะ Baldur's Gate 3 &bull; การ์ดตัวเลือกแสดงโอกาสสำเร็จชัดเจน
        </p>
    </div>
    """, unsafe_allow_html=True)

    saved_chars = list_saved_characters()

    col_pad_l, col_main, col_pad_r = st.columns([1, 10, 1])

    with col_main:
        tab_create, tab_load = st.tabs([
            "✨ สร้างตัวละครใหม่ (Create Character)",
            "📂 โหลดตัวละครเดิม (Load Save)",
        ])

        with tab_load:
            st.markdown("##### 📂 บันทึกตัวละครเดิมที่มีอยู่ในระบบ")
            if saved_chars:
                col_sel, col_btn = st.columns([3, 1])
                with col_sel:
                    selected_char = st.selectbox("เลือกตัวละคร:", saved_chars, label_visibility="collapsed")
                with col_btn:
                    if st.button("▶️ เข้าสู่เกม", use_container_width=True, type="primary"):
                        if load_game(selected_char):
                            st.rerun()
            else:
                st.info("ยังไม่มีข้อมูลตัวละครที่บันทึกไว้ — สลับไปที่แท็บ 'สร้างตัวละครใหม่' เพื่อเริ่มการผจญภัยได้เลย!")

        with tab_create:
            classes_cat = character_creator._load_catalog("classes_catalog.json")
            spells_cat = character_creator._load_catalog("spell_catalog.json")

            # Identity Section
            st.markdown("##### 👤 ข้อมูลอัตลักษณ์และสายอาชีพ (Identity, Class & Subclass)")
            col_name, col_bg = st.columns(2)
            with col_name:
                char_name = st.text_input("ชื่อตัวละคร (Character Name):", value="Valeros", key="input_char_name")
            with col_bg:
                bg = st.selectbox(
                    "ภูมิหลัง (Background):",
                    ["Folk Hero", "Acolyte", "Criminal", "Noble", "Sage", "Soldier"],
                    key="select_bg",
                )

            col_r, col_c = st.columns(2)
            with col_r:
                race = st.selectbox(
                    "เผ่าพันธุ์ (Race):",
                    ["Human", "Elf", "Dwarf", "Halfling", "Tiefling", "Half-Orc", "Dragonborn"],
                    key="select_race",
                )
            with col_c:
                cls_name = st.selectbox(
                    "คลาสหลัก (Class):",
                    ["Fighter", "Wizard", "Rogue", "Cleric", "Bard", "Paladin", "Barbarian", "Warlock"],
                    key="select_class",
                )

            cls_id = cls_name.lower()
            cls_data = classes_cat.get(cls_id, {})
            subclasses_dict = cls_data.get("subclasses", {})
            fighting_styles_dict = cls_data.get("fighting_styles", {})

            # Subclass & Fighting Style Selection (BG3 Style)
            col_sub, col_fs = st.columns(2)
            chosen_subclass_id = None
            chosen_fighting_style = None

            with col_sub:
                if subclasses_dict:
                    sub_options = list(subclasses_dict.keys())
                    sub_labels = {
                        k: f"{v.get('name', k)}"
                        for k, v in subclasses_dict.items()
                    }
                    chosen_subclass_id = st.selectbox(
                        "🌟 สายอาชีพย่อย (Subclass / Domain / Oath / Patron):",
                        options=sub_options,
                        format_func=lambda k: sub_labels[k],
                        key=f"select_subclass_{cls_id}",
                    )
                    sub_desc = subclasses_dict.get(chosen_subclass_id, {}).get("description", "")
                    if sub_desc:
                        st.caption(f"✨ *{sub_desc}*")

            with col_fs:
                if fighting_styles_dict:
                    fs_options = list(fighting_styles_dict.keys())
                    chosen_fighting_style = st.selectbox(
                        "⚔️ รูปแบบการต่อสู้ (Fighting Style):",
                        options=fs_options,
                        format_func=lambda k: f"{k.replace('_', ' ').title()} — {fighting_styles_dict[k]}",
                        key=f"select_fs_{cls_id}",
                    )
                else:
                    # Show class signature features preview
                    lvl1_feats = cls_data.get("features_by_level", {}).get("1", [])
                    if lvl1_feats:
                        st.markdown("**🔥 ความสามารถประจำคลาส (Level 1 Features):**")
                        for lf in lvl1_feats:
                            st.caption(f"• {lf}")

            # Skill & Spell Customization Expander
            with st.expander("🎯 ปรับแต่งทักษะความชำนาญ (Skills) และเวทมนตร์เริ่มต้น (Starting Spells)", expanded=False):
                skill_pool = cls_data.get("proficient_skills_options", [])
                num_choose = cls_data.get("proficient_skills_choose", 2)
                if skill_pool and skill_pool[0] == "Any":
                    skill_pool = [
                        "Athletics", "Acrobatics", "Sleight of Hand", "Stealth",
                        "Arcana", "History", "Investigation", "Nature", "Religion",
                        "Animal Handling", "Insight", "Medicine", "Perception", "Survival",
                        "Deception", "Intimidation", "Performance", "Persuasion",
                    ]
                default_skills = skill_pool[:num_choose] if len(skill_pool) >= num_choose else skill_pool
                chosen_skills = st.multiselect(
                    f"เลือกทักษะประจำคลาส ({num_choose} ทักษะ):",
                    options=skill_pool,
                    default=default_skills,
                    max_selections=num_choose,
                    key=f"select_skills_{cls_id}",
                )

                chosen_spells = None
                default_cls_spells = cls_data.get("level_1_spells_known", [])
                if cls_data.get("spellcasting") or default_cls_spells:
                    avail_spells = [
                        sid for sid, sdata in spells_cat.items()
                        if sdata.get("level", 0) <= 1 and (cls_name in sdata.get("class", []) or sid in default_cls_spells)
                    ]
                    valid_defaults = [s for s in default_cls_spells if s in avail_spells]
                    chosen_spells = st.multiselect(
                        "🪄 เลือก Cantrips และเวทมนตร์ระดับ 1 เริ่มต้น:",
                        options=avail_spells,
                        default=valid_defaults,
                        format_func=lambda sid: f"{spells_cat.get(sid, {}).get('name', sid)} ({'Cantrip' if spells_cat.get(sid, {}).get('level', 0) == 0 else 'Lv 1'}) — {spells_cat.get(sid, {}).get('description', '')[:60]}",
                        key=f"select_spells_{cls_id}",
                    )

            # Dynamic Character Flavor & Voice Quote Card
            cls_info = CLASS_FLAVOR.get(cls_name, {})
            race_info = RACE_FLAVOR.get(race, "")
            bgs_cat = character_creator._load_catalog("backgrounds_catalog.json")
            bg_key = bg.lower().replace(" ", "_")
            bg_hook = bgs_cat.get(bg_key, {}).get("background_hook", "")
            sub_display = subclasses_dict.get(chosen_subclass_id, {}).get("name", "") if chosen_subclass_id else ""
            sub_badge = f" ({sub_display})" if sub_display else ""

            st.markdown(
                f"""
                <div style="background: rgba(30, 41, 59, 0.75); border-left: 4px solid #f43f5e; padding: 14px 18px; border-radius: 8px; margin: 14px 0 16px 0; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);">
                    <div style="font-style: italic; font-size: 1.05rem; color: #f8fafc; margin-bottom: 8px; line-height: 1.5;">
                        &ldquo;{cls_info.get('quote', '')}&rdquo;
                    </div>
                    <div style="font-size: 0.88rem; color: #94a3b8; line-height: 1.45;">
                        <span style="color: #cbd5e1; font-weight: 600;">{char_name}</span> &bull; {race} {cls_name}{sub_badge} &bull; <span style="color: #fda4af;">{bg}</span><br/>
                        {cls_info.get('desc', '')}<br/>
                        {race_info} &bull; <em>{bg_hook}</em>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            st.markdown("---")
            st.markdown("##### ⚔️ จัดสรรค่าพลัง Point-Buy (27 แต้มรวม)")
            st.caption("สถิติแนะนำจะปรับเปลี่ยนให้เข้ากับคลาสและเผ่าโดยอัตโนมัติ — สามารถปรับเลื่อนสไลเดอร์เพื่อดูการคำนวณแต้มแบบสดๆ ได้")

            rec_stats = get_recommended_stats(cls_name, race)
            races_cat = character_creator._load_catalog("races_catalog.json")
            race_key = race.lower().replace("-", "_").replace(" ", "_")
            r_bonuses = races_cat.get(race_key, {}).get("stat_bonuses", {})

            reset_epoch = st.session_state.get(f"pb_reset_epoch_{cls_name}_{race}", 0)

            col_stat_l, col_stat_r = st.columns(2)
            stats_alloc = {}

            with col_stat_l:
                st.markdown("###### 🗡️ ด้านกายภาพ (Physical Attributes)")
                for s in ["STR", "DEX", "CON"]:
                    bonus = r_bonuses.get(s, 0)
                    bonus_lbl = f" (+{bonus} {race})" if bonus > 0 else ""
                    default_val = rec_stats.get(s, 10)
                    val = st.slider(
                        f"{s}{bonus_lbl}",
                        min_value=8,
                        max_value=15,
                        value=default_val,
                        key=f"pb_{cls_name}_{race}_{s}_{reset_epoch}",
                    )
                    stats_alloc[s] = val

            with col_stat_r:
                st.markdown("###### 🧠 ด้านสติปัญญาและมนตรา (Mental & Magic)")
                for s in ["INT", "WIS", "CHA"]:
                    bonus = r_bonuses.get(s, 0)
                    bonus_lbl = f" (+{bonus} {race})" if bonus > 0 else ""
                    default_val = rec_stats.get(s, 10)
                    val = st.slider(
                        f"{s}{bonus_lbl}",
                        min_value=8,
                        max_value=15,
                        value=default_val,
                        key=f"pb_{cls_name}_{race}_{s}_{reset_epoch}",
                    )
                    stats_alloc[s] = val

            total_spent = sum(character_creator.POINT_BUY_COST.get(v, 0) for v in stats_alloc.values())
            remaining = character_creator.POINT_BUY_BUDGET - total_spent

            col_pb1, col_pb2, col_pb_status = st.columns([1, 1, 2])
            with col_pb1:
                st.metric("Point-Buy ที่ใช้", f"{total_spent} / 27")
            with col_pb2:
                st.metric("แต้มคงเหลือ", f"{remaining} แต้ม")
            with col_pb_status:
                ok_pts, pts_msg = character_creator.validate_point_buy(stats_alloc)
                if not ok_pts:
                    st.error(f"⚠️ {pts_msg}")
                elif remaining > 0:
                    st.info(f"💡 คงเหลืออีก {remaining} แต้ม — สามารถเพิ่มค่าพลังได้")
                else:
                    st.success("✅ Point-Buy Valid: ใช้ครบ 27 / 27 แต้มพอดี!")

            st.markdown("###### 📊 คะแนนสุทธิพร้อมโบนัสเผ่า (Final Scores & Modifiers)")
            cols_summary = st.columns(6)
            for idx, s in enumerate(character_creator.STAT_NAMES):
                base_v = stats_alloc[s]
                r_b = r_bonuses.get(s, 0)
                final_v = base_v + r_b
                mod = character_creator.get_modifier(final_v)
                with cols_summary[idx]:
                    st.metric(
                        label=s,
                        value=f"{final_v}",
                        delta=f"{mod:+d}",
                    )

            st.markdown("---")
            col_reset, col_submit = st.columns([1, 2])
            with col_reset:
                if st.button("🔄 รีเซ็ตค่าแนะนำ (Reset)", use_container_width=True):
                    st.session_state[f"pb_reset_epoch_{cls_name}_{race}"] = reset_epoch + 1
                    st.rerun()

            with col_submit:
                submitted = st.button("⚔️ สร้างตัวละครและเริ่มการผจญภัย", type="primary", use_container_width=True)

            if submitted:
                if not char_name.strip():
                    st.error("กรุณาระบุชื่อตัวละคร")
                elif not ok_pts:
                    st.error(f"การจัดสรรแต้ม Point-Buy ไม่ถูกต้อง: {pts_msg}")
                else:
                    try:
                        player = character_creator.create_character(
                            name=char_name.strip(),
                            race=race,
                            class_name=cls_name,
                            background=bg,
                            stats=stats_alloc,
                            subclass=chosen_subclass_id,
                            fighting_style=chosen_fighting_style,
                            chosen_skills=chosen_skills if chosen_skills else None,
                            chosen_spells=chosen_spells if chosen_spells else None,
                        )
                        inv = player.setdefault("inventory", [])
                        if not any(i.get("item_id") == "trail_rations" for i in inv):
                            inv.append({"item_id": "trail_rations", "equipped": False, "quantity": 3})

                        world = {
                            "schema_version": 4,
                            "character_name": char_name.strip(),
                            "current_location": "town_riverside",
                            "visited_rooms": ["town_riverside"],
                            "cleared_rooms": [],
                            "collected_loot": [],
                            "dynamic_rooms": {},
                            "game_time": {"day": 1, "period": "morning", "steps_since_period_start": 0},
                            "quest_log": {"main": [], "side": []},
                            "combat_state": None,
                        }

                        intro_quote = cls_info.get("quote", "")
                        init_log = [{
                            "role": "assistant",
                            "content": (
                                f"🌅 **บทนำ: ก้าวแรกสู่ริเวอร์ไซด์ (Prologue: Arrival at Riverside Village)**\n\n"
                                f"สายลมยามเช้าพัดผ่านทิวไม้แห่งป่าธอร์นวูดมายังแม่น้ำซิลเวอร์ประกายระยิบระยับ... "
                                f"**{char_name.strip()}** {race_info} ในมาดของ {cls_name}{sub_badge} ผู้มีภูมิหลังเป็น {bg} "
                                f"ก้าวเท้าข้ามสะพานไม้เก่าเข้าสู่หมู่บ้านริมน้ำริเวอร์ไซด์\n\n"
                                f"> *\"{intro_quote}\"*\n\n"
                                f"กลิ่นขนมปังอบใหม่และควันจากเตาหลอมของโรงตีเหล็กลอยอวลในอากาศ ชาวบ้านเริ่มออกมาเปิดร้านและทำมาหากิน "
                                f"เมื่อมองไปรอบจัตุรัสกลางหมู่บ้าน เจ้าจะเห็นจุดน่าสนใจหลายแห่งที่รอให้สำรวจ:\n\n"
                                f"• 🍻 **โรงเตี๊ยมมังกรเมา (The Drunken Dragon Tavern):** แหล่งรวมข่าวลือ ข่าวกรอง และกระดานประกาศภารกิจล่าค่าหัว (Notice Board)\n"
                                f"• 🏪 **ตลาดและร้านค้า (Town Shops):** ร้านค้าทั่วไป โรงตีเหล็ก และร้านปรุงยา สำหรับเตรียมเสบียงและอาวุธ\n"
                                f"• 🌲 **เส้นทางออกนอกเมือง:** มุ่งหน้าสู่ป่าธอร์นวูดทางทิศเหนือ ซึ่งมีข่าวลือเรื่องก็อบลินและดันเจียนลึกลับ\n\n"
                                f"**เจ้าต้องการมุ่งหน้าไปสำรวจที่ไหนก่อนดี?** *(สามารถคลิกปุ่มตัวเลือกแนะนำด้านล่าง หรือพิมพ์คำสั่งสิ่งที่อยากทำได้เลย)*"
                            ),
                        }]
                        init_suggs = [
                            "เดินไปที่โรงเตี๊ยมเพื่อดูกระดานประกาศภารกิจ",
                            "แวะดูสินค้าที่ร้านค้าและโรงตีเหล็ก",
                            "พูดคุยสอบถามข่าวสารกับชาวบ้านในจัตุรัส",
                        ]

                        world["narrative_log"] = list(init_log)
                        world["action_suggestions"] = list(init_suggs)

                        state_manager.save_character(char_name.strip(), player)
                        state_manager.save_world(char_name.strip(), world)

                        st.session_state["player_state"] = player
                        st.session_state["world_state"] = world
                        st.session_state["current_char_name"] = char_name.strip()
                        st.session_state["narrative_log"] = list(init_log)
                        st.session_state["action_suggestions"] = list(init_suggs)

                        st.success(f"สร้างตัวละคร '{char_name.strip()}' สำเร็จ!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"เกิดข้อผิดพลาดในการสร้างตัวละคร: {e}")
