"""
ui/town_view.py — Town shops, buying/selling items, and Tavern Notice Board UI.
"""

from typing import Any, Dict, List
import streamlit as st

import dungeon_manager
import state_manager
from ui.session import auto_save


def get_sell_button_key(shop_id: str, index: int, item_id: str) -> str:
    """Generate a unique Streamlit widget key for selling an inventory item."""
    return f"sell_{shop_id}_{index}_{item_id}"


def get_sell_button_keys(shop_id: str, inventory: List[Dict[str, Any]]) -> List[str]:
    """Generate all sell button widget keys for unequipped items in inventory."""
    return [
        get_sell_button_key(shop_id, idx, item.get("item_id", ""))
        for idx, item in enumerate(inventory)
        if isinstance(item, dict) and not item.get("equipped")
    ]


def render_town_tab(player: Dict[str, Any], world: Dict[str, Any], current_room: Dict[str, Any]):
    """Render Town Shops & Merchants and the Tavern Notice Board."""
    if current_room.get("shops"):
        st.markdown("---")
        st.markdown("##### 🏪 Town Shops & Merchants")
        shop_cat = state_manager._get_shop_catalog()
        gt = world.get("game_time", {})
        cur_period = gt.get("period", "morning")

        active_shop = st.session_state.get("active_visiting_shop")
        if active_shop and active_shop in current_room["shops"]:
            s_data = shop_cat.get(active_shop, {})
            s_name = s_data.get("name", active_shop.title())
            s_desc = s_data.get("description", "")
            open_periods = s_data.get("open_periods", [])
            is_open = cur_period in open_periods

            col_sh_h, col_sh_exit = st.columns([3, 1])
            with col_sh_h:
                st.markdown(f"#### 🏪 {s_name}")
                st.caption(f"*{s_desc}*")
            with col_sh_exit:
                if st.button("⬅️ เดินออกจากร้าน", use_container_width=True, key="btn_exit_shop"):
                    st.session_state["active_visiting_shop"] = None
                    st.session_state["narrative_log"].append({
                        "role": "assistant",
                        "content": f"🚶‍♂️ คุณเดินก้าวเท้าออกจากร้าน **{s_name}** กลับมายังบริเวณจัตุรัส"
                    })
                    auto_save()
                    st.rerun()

            if not is_open:
                st.warning(f"ขณะนี้ร้านปิดให้บริการสำหรับช่วง {cur_period} (เปิดให้บริการช่วง: {', '.join(open_periods)})")
            else:
                haggle_info = world.get("shop_haggle_state", {}).get(active_shop, {})
                disc_mult = float(haggle_info.get("discount_multiplier", 1.0))
                disc_pct = int(haggle_info.get("discount_percent", 0))
                cur_day = int(world.get("game_time", {}).get("day", 1))
                already_haggled = (haggle_info.get("haggled_day") == cur_day)

                with st.container(border=True):
                    st.markdown("##### 🗣️ ต่อรองราคา & ขอดูสินค้าลับใต้เคาน์เตอร์ (BG3 Social Bartering)")
                    if already_haggled:
                        if disc_pct > 0:
                            st.success(f"✨ ต่อรองราคาสำเร็จแล้ววันนี้! ได้รับส่วนลด **{disc_pct}%** และเปิดคลังสินค้าลับใต้เคาน์เตอร์แล้ว")
                        else:
                            st.warning("⚠️ วันนี้คุณต่อรองราคาพลาดไปแล้ว (ราคาสินค้า +15% สำหรับวันนี้)")
                    else:
                        c_hag1, c_hag2 = st.columns([2.2, 1])
                        with c_hag1:
                            haggle_skill = st.selectbox(
                                "เลือกทักษะเจรจากับพ่อค้า:",
                                [
                                    "Persuasion (หว่านล้อมขอลดราคาอย่างเป็นมิตร — DC 13)",
                                    "Deception (แกล้งติตำหนิสินค้าเพื่อกดราคา — DC 13)",
                                    "Intimidation (ข่มขู่พ่อค้าให้ลดราคา — DC 13, พลาดโดนแจ้งยาม)",
                                ],
                                key=f"sel_haggle_{active_shop}",
                            )
                        with c_hag2:
                            st.markdown("<div style='padding-top: 26px;'></div>", unsafe_allow_html=True)
                            if st.button("🎲 ทอยต่อรองราคา", key=f"btn_haggle_{active_shop}", type="primary", use_container_width=True):
                                sk_clean = haggle_skill.split(" (")[0].strip()
                                h_res = state_manager.attempt_shop_haggle(active_shop, player, world, skill=sk_clean)
                                if h_res["success"]:
                                    st.toast(f"🎉 ลดราคา {h_res['discount_percent']}% + ปลดล็อกสินค้าลับ!")
                                else:
                                    st.toast("❌ ต่อรองราคาไม่สำเร็จ")
                                st.session_state["narrative_log"].append({
                                    "role": "assistant",
                                    "content": h_res["message"],
                                })
                                auto_save()
                                st.rerun()

                buy_tab, sell_tab = st.tabs(["🛍️ Buy Goods (ซื้อสินค้า)", "💰 Sell Items (ขายของ)"])
                with buy_tab:
                    sell_items = list(s_data.get("sell_items", []))
                    if haggle_info.get("secret_unlocked") and haggle_info.get("secret_item_id"):
                        if haggle_info["secret_item_id"] not in sell_items:
                            sell_items.append(haggle_info["secret_item_id"])
                    item_cat = state_manager._get_item_catalog()
                    eff_sell_mult = s_data.get("sell_multiplier", 1.0) * disc_mult
                    for s_item_id in sell_items:
                        is_secret = (s_item_id == haggle_info.get("secret_item_id"))
                        i_info = item_cat.get(s_item_id) or world.get("generated_items", {}).get(s_item_id) or haggle_info.get("secret_item") or {}
                        i_name = i_info.get("name", s_item_id)
                        cost_info = state_manager.get_item_price_breakdown(i_info, eff_sell_mult)
                        cost_text = cost_info["text"]
                        secret_badge = " 🗝️ **[สินค้าลับใต้เคาน์เตอร์]**" if is_secret else ""
                        disc_badge = f" *(ลด {disc_pct}%)*" if disc_pct > 0 else ""
                        col_b1, col_b2 = st.columns([3, 1])
                        with col_b1:
                            st.markdown(f"**{i_name}**{secret_badge} — {cost_text}{disc_badge}")
                            if i_info.get("description"):
                                st.caption(i_info["description"])
                        with col_b2:
                            can_afford = state_manager.can_afford(player, gp=cost_info["gp"], sp=cost_info["sp"], cp=cost_info["cp"])
                            if st.button(f"Buy ({cost_text})", key=f"buy_{active_shop}_{s_item_id}", disabled=not can_afford, use_container_width=True):
                                ok_b, msg_b = state_manager.buy_item(s_item_id, active_shop, player, world)
                                if ok_b:
                                    st.toast(msg_b)
                                    auto_save()
                                    st.rerun()
                                else:
                                    st.error(msg_b)

                with sell_tab:
                    inv = player.get("inventory", [])
                    non_equipped = [(idx, i) for idx, i in enumerate(inv) if isinstance(i, dict) and not i.get("equipped")]
                    if not non_equipped:
                        st.info("No unequipped items available to sell.")
                    else:
                        item_cat = state_manager._get_item_catalog()
                        for idx, s_item in non_equipped:
                            s_iid = s_item.get("item_id")
                            s_qty = s_item.get("quantity", 1)
                            i_info = state_manager._inventory_item_info(s_item, item_cat)
                            if not i_info and world:
                                i_info = world.get("generated_items", {}).get(s_iid, {})
                            i_name = i_info.get("name", s_iid)
                            gain_info = state_manager.get_item_sell_breakdown(i_info, s_data.get("buy_multiplier", 0.5))
                            gain_text = gain_info["text"]
                            col_s1, col_s2 = st.columns([3, 1])
                            with col_s1:
                                st.markdown(f"**{i_name}** (x{s_qty}) — Sells for {gain_text}")
                            with col_s2:
                                sell_key = get_sell_button_key(active_shop, idx, s_iid)
                                if st.button(f"Sell (+{gain_text})", key=sell_key, use_container_width=True):
                                    ok_s, msg_s = state_manager.sell_item(s_iid, active_shop, player, world)
                                    if ok_s:
                                        st.toast(msg_s)
                                        auto_save()
                                        st.rerun()
                                    else:
                                        st.error(msg_s)
        else:
            st.caption("เลือกร้านค้าในบริเวณเพื่อก้าวเท้าเดินเข้าไปติดต่อซื้อขาย:")
            for shop_id in current_room["shops"]:
                s_data = shop_cat.get(shop_id)
                if not s_data:
                    continue
                s_name = s_data.get("name", shop_id.title())
                s_desc = s_data.get("description", "")
                open_periods = s_data.get("open_periods", [])
                is_open = cur_period in open_periods
                open_status = "🟢 เปิดให้บริการ" if is_open else f"🔴 ปิดบริการ (เปิดช่วง: {', '.join(open_periods)})"

                col_s_card, col_s_btn = st.columns([3, 1])
                with col_s_card:
                    st.markdown(f"**🏪 {s_name}** &bull; <span style='font-size: 0.85rem; color: #94a3b8;'>{open_status}</span>", unsafe_allow_html=True)
                    st.caption(f"*{s_desc}*")
                with col_s_btn:
                    if is_open:
                        if st.button("🚶‍♂️ แวะเข้าร้าน", key=f"btn_enter_{shop_id}", use_container_width=True):
                            st.session_state["active_visiting_shop"] = shop_id
                            st.session_state["narrative_log"].append({
                                "role": "assistant",
                                "content": f"🚶‍♂️ คุณก้าวเท้าเดินเข้าไปในร้าน **{s_name}** กลิ่นอายและบรรยากาศในร้านต้อนรับการมาเยือน พ่อค้ามองมาที่คุณพร้อมบริการ"
                            })
                            auto_save()
                            st.rerun()
                    else:
                        st.button("🔒 ร้านปิด", key=f"btn_closed_{shop_id}", disabled=True, use_container_width=True)

    # Tavern Notice Board UI (Module C §2 / Phase 13.5)
    if current_room.get("id") in ("town_riverside", "tavern") or current_room.get("notice_board") is not None or "innkeeper_mira" in current_room.get("npcs", []):
        st.markdown("---")
        c_nb_h, c_nb_med = st.columns([2.2, 1.3])
        with c_nb_h:
            st.markdown("##### 📜 Tavern Notice Board (กระดานภารกิจ & ไกล่เกลี่ย)")
        with c_nb_med:
            if st.button("🕊️ ขอรับงานไกล่เกลี่ย / สืบสวน", key="btn_post_mediation_nb", use_container_width=True):
                med_q = dungeon_manager.post_mediation_quest(world)
                dungeon_manager.accept_notice_board_quest(world, med_q["id"])
                st.toast(f"รับงานสายเจรจา: {med_q['title']}")
                st.session_state["narrative_log"].append({
                    "role": "assistant",
                    "content": f"🕊️ **รับภารกิจสายเจรจา/สืบสวน:** {med_q['title']} — {med_q['description']}",
                })
                auto_save()
                st.rerun()

        nb_entries = dungeon_manager.get_notice_board(world)
        if not nb_entries:
            st.caption("No notices currently posted. Check back in a few days!")
        else:
            for entry in nb_entries:
                e_id = entry.get("id")
                e_type = entry.get("type", "quest")
                e_title = entry.get("title", "Notice")
                e_desc = entry.get("description", "")
                e_status = entry.get("status", "available")
                icon = "🗡️" if e_type == "bounty" else ("📦" if e_type == "delivery" else ("🕊️" if e_type == "mediation" else "🗺️"))

                with st.expander(f"{icon} {e_title} [{e_status.capitalize()}]", expanded=(e_status in ("available", "accepted"))):
                    st.write(e_desc)
                    if entry.get("reward_gold"):
                        pac_lbl = " *(+25 GP โบนัสเจรจาสันติหากกล่อมให้ยอมแพ้โดยไม่ฆ่า!)*" if e_type == "bounty" else ""
                        st.caption(f"💰 Reward: **{entry['reward_gold']} GP**{pac_lbl}")
                    if entry.get("target_room_name"):
                        st.caption(f"📍 Location: **{entry['target_room_name']}**")

                    if e_status == "available":
                        if st.button("📜 Accept Quest", key=f"acc_nb_{e_id}", use_container_width=True):
                            res_acc = dungeon_manager.accept_notice_board_quest(world, e_id)
                            st.toast(res_acc["message"])
                            st.session_state["narrative_log"].append({
                                "role": "assistant",
                                "content": f"📜 **Accepted Quest from Notice Board:** {e_title}"
                            })
                            auto_save()
                            st.rerun()
                    elif e_status == "accepted":
                        if e_type == "delivery":
                            tgt_item = entry.get("target_item")
                            has_it = any(isinstance(it, dict) and it.get("item_id") == tgt_item for it in player.get("inventory", []))
                            if st.button(f"📦 Complete Delivery ({entry.get('reward_gold')} GP)", key=f"comp_nb_{e_id}", disabled=not has_it, use_container_width=True):
                                res_cmp = dungeon_manager.complete_notice_board_quest(world, e_id, player)
                                if res_cmp["success"]:
                                    st.toast(res_cmp["message"])
                                    st.session_state["narrative_log"].append({
                                        "role": "assistant",
                                        "content": f"🎉 **Quest Completed:** {res_cmp['message']}"
                                    })
                                    auto_save()
                                    st.rerun()
                                else:
                                    st.error(res_cmp["message"])
                        elif e_type == "mediation":
                            c_m1, c_m2 = st.columns([2, 1])
                            with c_m1:
                                med_sk = st.selectbox(
                                    "เลือกทักษะที่ใช้ไกล่เกลี่ย/สืบสวน:",
                                    ["Persuasion", "Insight", "Investigation"],
                                    key=f"med_sk_{e_id}",
                                )
                            with c_m2:
                                st.markdown("<div style='padding-top: 26px;'></div>", unsafe_allow_html=True)
                                if st.button("🕊️ ทอยไกล่เกลี่ย", key=f"btn_med_{e_id}", type="primary", use_container_width=True):
                                    res_m = dungeon_manager.resolve_mediation_quest(world, e_id, player, skill=med_sk)
                                    st.toast(res_m["message"])
                                    st.session_state["narrative_log"].append({
                                        "role": "assistant",
                                        "content": res_m["message"],
                                    })
                                    auto_save()
                                    st.rerun()
                        elif e_type == "bounty":
                            tgt_rm = entry.get("target_room_id")
                            tgt_mon = entry.get("target_monster", "")
                            is_rm_cleared = (tgt_rm in world.get("cleared_rooms", [])) or bool(entry.get("pacified_completed")) or (f"pacified_{tgt_mon}" in world.get("generated_npcs", {}))
                            if is_rm_cleared:
                                if st.button(f"🏆 ส่งมอบภารกิจ Bounty / Pacifist Resolution", key=f"comp_bounty_{e_id}", type="primary", use_container_width=True):
                                    res_cmp = dungeon_manager.complete_notice_board_quest(world, e_id, player)
                                    if res_cmp["success"]:
                                        st.toast(res_cmp["message"])
                                        st.session_state["narrative_log"].append({
                                            "role": "assistant",
                                            "content": f"🎉 **Bounty Completed:** {res_cmp['message']}"
                                        })
                                        auto_save()
                                        st.rerun()
                            else:
                                st.info("Status: เดินทางไปยังพื้นที่เป้าหมายเพื่อปราบมอนสเตอร์ หรือใช้ปุ่ม '🕊️ เจรจาเลี่ยงศึก' กล่อมให้ยอมแพ้เพื่อรับโบนัสสันติ +25 GP!")
                        else:
                            st.info("Status: Quest active in your quest log! Fulfill the objectives to earn the bounty.")
                    elif e_status == "completed":
                        st.success("✅ Quest Completed!")

    if not current_room.get("shops") and not (current_room.get("id") in ("town_riverside", "tavern") or current_room.get("notice_board") is not None or "innkeeper_mira" in current_room.get("npcs", [])):
        st.caption("🏛️ ไม่มีร้านค้าหรือกระดานประกาศในบริเวณนี้ (มีเฉพาะในเมืองหรือโรงเตี๊ยม)")
