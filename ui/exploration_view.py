"""
ui/exploration_view.py — Exploration HUD, compass navigation, imprisonment/escape, BG3 skill checks, and scene interactions.
"""

from typing import Any, Dict, List, Optional
import re
import streamlit as st

import check_manager
import combat_manager
import dungeon_manager
import llm_handler
import state_manager
from ui.session import (
    auto_save,
    get_active_quest_count,
    get_current_active_model,
    get_display_spell_catalog,
    get_known_spell_count,
    get_player_currency,
    get_ration_count,
    get_session_memory_manager,
    resolve_turn_extraction,
)
from ui.dialogs import (
    show_camp_dialog,
    show_character_dialog,
    show_inventory_dialog,
    show_quests_dialog,
    show_spells_dialog,
)
from ui.sidebar import render_sidebar
from ui.combat_view import render_combat_panel
from ui.town_view import render_town_tab


def apply_escape_success(world: Dict[str, Any], player: Dict[str, Any]) -> str:
    """
    Apply state changes for a successful escape:
    - Sets player status to 'normal'
    - Relocates player to the nearest visited safe room
    Returns the safe room id.
    """
    player["status"] = "normal"
    safe_room = dungeon_manager.find_nearest_visited_safe_room(world)
    world["current_location"] = safe_room
    return safe_room


def attempt_escape(world: Dict[str, Any], player: Dict[str, Any]) -> Dict[str, Any]:
    """
    Attempt to escape captive status via a Hard DEX check.
    Advances game time by 1 step (turn cost).
    If successful, applies escape state changes.
    If failed and player has Inspiration > 0, constructs pending_reroll dictionary with kind='escape'.

    Returns:
        Dict with keys: 'success', 'res', 'safe_room', 'pending_reroll'
    """
    esc_res = state_manager.resolve_check("DEX", "hard", player)

    # Advance time by 1 step (costs a turn)
    time_res = dungeon_manager.advance_time(world, steps=1, character_state=player)
    if time_res.get("period_changed"):
        state_manager.handle_period_change(world, player)

    safe_room = None
    pending_reroll = None

    if esc_res["success"]:
        safe_room = apply_escape_success(world, player)
    else:
        if state_manager.get_inspiration(player) > 0:
            pending_reroll = {
                "stat": "DEX",
                "difficulty": "hard",
                "dc": esc_res["dc"],
                "modifier": esc_res["modifier"],
                "proficiency": esc_res.get("proficiency", 0),
                "bonus": esc_res.get("bonus", 0),
                "original_roll": esc_res["roll"],
                "original_total": esc_res["total"],
                "skill": None,
                "kind": "escape",
            }

    return {
        "success": esc_res["success"],
        "res": esc_res,
        "safe_room": safe_room,
        "pending_reroll": pending_reroll,
    }


def resolve_inspiration_reroll(
    pending: Dict[str, Any],
    player: Dict[str, Any],
    world: Dict[str, Any],
    roll_fn: Any = state_manager._roll_d20,
) -> Dict[str, Any]:
    """
    Resolve an inspiration reroll using D&D 5e / BG3 rules.
    Takes max(original_roll, reroll_die), calculates total against dc.
    If pending kind is 'escape', success is True, and player is held captive/imprisoned:
        calls apply_escape_success(world, player) and sets escaped=True.
    """
    reroll_die = roll_fn()
    original_roll = pending.get("original_roll", 1)
    final_die = max(original_roll, reroll_die)
    mod = pending.get("modifier", 0)
    prof = pending.get("proficiency", 0)
    bonus = pending.get("bonus", 0)
    dc = pending.get("dc", 10)

    new_total = final_die + mod + prof + bonus
    is_crit = (final_die == 20)
    is_fumble = (final_die == 1)
    new_succ = True if is_crit else (False if is_fumble else (new_total >= dc))

    escaped = False
    safe_room = None

    if pending.get("kind") == "escape" and new_succ:
        is_held = (
            player.get("status") == "captive"
            or bool(player.get("crime_state", {}).get("imprisoned"))
            or bool(world.get("is_imprisoned"))
        )
        if is_held:
            safe_room = apply_escape_success(world, player)
            escaped = True

    return {
        "reroll_die": reroll_die,
        "final_die": final_die,
        "new_total": new_total,
        "success": new_succ,
        "escaped": escaped,
        "safe_room": safe_room,
    }


def calculate_check_success_chance(
    total_modifier: int,
    dc: int,
    has_advantage: bool = False,
) -> int:
    """
    Calculate the exact D&D 5e / BG3 percentage chance (5% to 99%) of passing a d20 check
    given total_modifier and dc (accounting for Nat 20 auto-success and Nat 1 auto-fail).
    """
    success_faces = 0
    for die in range(1, 21):
        if die == 20:
            success_faces += 1
        elif die == 1:
            continue
        elif (die + total_modifier) >= dc:
            success_faces += 1
    p = success_faces / 20.0
    if has_advantage:
        p = 1.0 - ((1.0 - p) ** 2)
    return int(round(p * 100))


def build_choice_card_metadata(
    action_text: str,
    player: Dict[str, Any],
    index: int = 1,
) -> Dict[str, Any]:
    """
    Analyze a suggested action string to build transparent choice preview metadata
    inspired by '1 ใน 65 ล้าน' and Baldur's Gate 3:
    - Detects skill checks, DC, stat modifier, and exact % chance of success.
    - Detects Class / Background roleplay tags (e.g. [Bard], [Fighter], [Soldier]).
    """
    text = (action_text or "").strip()
    m_tag = re.match(r"^\s*\[([^\]]+)\]\s*(.*)$", text)
    tag_name = m_tag.group(1).strip() if m_tag else None
    check_spec = check_manager.detect_action_skill_check(text, player) if player else None

    if check_spec:
        tot_mod = check_spec["total_modifier"]
        dc = check_spec["dc"]
        pct = calculate_check_success_chance(tot_mod, dc)
        if pct >= 65:
            odds_color = "#4ade80"
            odds_bg = "rgba(74, 222, 128, 0.14)"
            odds_border = "rgba(74, 222, 128, 0.4)"
        elif pct >= 40:
            odds_color = "#fbbf24"
            odds_bg = "rgba(251, 191, 36, 0.14)"
            odds_border = "rgba(251, 191, 36, 0.4)"
        else:
            odds_color = "#f87171"
            odds_bg = "rgba(248, 113, 113, 0.14)"
            odds_border = "rgba(248, 113, 113, 0.4)"

        prof_badge = " ★ชำนาญ" if check_spec.get("is_proficient") else ""
        tag_prefix = f"[{tag_name}] • " if (tag_name and tag_name.lower() != check_spec["skill_name"].lower()) else ""
        return {
            "index": index,
            "action_text": text,
            "has_check": True,
            "is_roleplay_tag": bool(tag_name),
            "tag_name": tag_name,
            "stat": check_spec["stat"],
            "skill": check_spec["skill"],
            "skill_name": check_spec["skill_name"],
            "skill_thai": check_spec["skill_thai"],
            "dc": dc,
            "total_modifier": tot_mod,
            "success_pct": pct,
            "category_label": f"🎲 {tag_prefix}{check_spec['stat']} ({check_spec['skill_name']}) {tot_mod:+d}{prof_badge}",
            "odds_label": f"DC {dc} • โอกาส {pct}%",
            "odds_color": odds_color,
            "odds_bg": odds_bg,
            "odds_border": odds_border,
            "sub_caption": f"ทอยทักษะ {check_spec['skill_thai']} ({check_spec['difficulty'].capitalize()})",
        }

    if m_tag and tag_name:
        return {
            "index": index,
            "action_text": text,
            "has_check": False,
            "is_roleplay_tag": True,
            "tag_name": tag_name,
            "success_pct": 100,
            "category_label": f"◆ บทบาท [{tag_name}]",
            "odds_label": "สำเร็จทันที (Roleplay)",
            "odds_color": "#f3d08a",
            "odds_bg": "rgba(212, 175, 106, 0.16)",
            "odds_border": "rgba(212, 175, 106, 0.45)",
            "sub_caption": f"ใช้เอกลักษณ์ของ {tag_name} ในการดำเนินเรื่อง",
        }

    low = text.lower()
    if any(k in low for k in ("พูดคุย", "ถาม", "สนทนา", "ทักทาย", "เจรจา")):
        cat_lbl = "💬 สนทนา & ข่าวสาร"
        sub_cap = "พูดคุยโต้ตอบกับตัวละครในฉาก"
    elif any(k in low for k in ("ร้านค้า", "โรงเตี๊ยม", "ซื้อ", "ขาย", "กระดาน", "ภารกิจ")):
        cat_lbl = "🏛️ สถานที่ & บริการ"
        sub_cap = "ติดต่อสถานที่หรือตรวจสอบภารกิจ"
    elif any(k in low for k in ("โจมตี", "ต่อสู้", "ชักดาบ", "ซุ่ม")):
        cat_lbl = "⚔️ เตรียมปะทะ"
        sub_cap = "อาจนำไปสู่การต่อสู้แบบ Turn-Based"
    else:
        cat_lbl = "🧭 สำรวจ & ดำเนินเรื่อง"
        sub_cap = "สำรวจสภาพแวดล้อมและดำเนินเนื้อเรื่องต่อ"

    return {
        "index": index,
        "action_text": text,
        "has_check": False,
        "is_roleplay_tag": False,
        "success_pct": 100,
        "category_label": cat_lbl,
        "odds_label": "ไม่ต้องทอยเต๋า",
        "odds_color": "#38bdf8",
        "odds_bg": "rgba(56, 189, 248, 0.12)",
        "odds_border": "rgba(56, 189, 248, 0.35)",
        "sub_caption": sub_cap,
    }


def snapshot_player_world_state(player: Dict[str, Any], world: Dict[str, Any]) -> Dict[str, Any]:
    """Capture a lightweight before-turn snapshot of key player/world stats to compute stat delta pills."""
    hp_cur = player.get("hp", {}).get("current", 10) if isinstance(player, dict) else 10
    curr = get_player_currency(player)
    gold_gp = curr.get("gp", 0)
    xp_cur = player.get("xp_current", 0) if isinstance(player, dict) else 0
    lvl = player.get("level", 1) if isinstance(player, dict) else 1
    insp = state_manager.get_inspiration(player) if isinstance(player, dict) else 0
    inv_ids = [i.get("item_id") for i in player.get("inventory", []) if isinstance(i, dict)] if isinstance(player, dict) else []
    loc = world.get("current_location", "") if isinstance(world, dict) else ""
    q_cnt = get_active_quest_count(world) if isinstance(world, dict) else 0
    return {
        "hp": hp_cur,
        "gold": gold_gp,
        "xp": xp_cur,
        "level": lvl,
        "inspiration": insp,
        "inv_ids": list(inv_ids),
        "location": loc,
        "quest_count": q_cnt,
    }


def compute_turn_stat_deltas(
    before_snapshot: Dict[str, Any],
    player: Dict[str, Any],
    world: Dict[str, Any],
    rel_change: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    """
    Compute Stat Delta Pills (like 'The Night the Grail Fills' & '1 ใน 65 ล้าน')
    comparing before_snapshot against current player & world state.
    """
    if not before_snapshot or not isinstance(player, dict):
        return []

    deltas: List[Dict[str, str]] = []
    hp_now = player.get("hp", {}).get("current", 10)
    hp_diff = hp_now - before_snapshot.get("hp", hp_now)
    if hp_diff != 0:
        deltas.append({
            "label": f"❤️ HP {hp_diff:+d}",
            "kind": "pos" if hp_diff > 0 else "neg",
        })

    gold_now = get_player_currency(player).get("gp", 0)
    gold_diff = gold_now - before_snapshot.get("gold", gold_now)
    if gold_diff != 0:
        deltas.append({
            "label": f"💰 Gold {gold_diff:+d} GP",
            "kind": "pos" if gold_diff > 0 else "neg",
        })

    xp_now = player.get("xp_current", 0)
    xp_diff = xp_now - before_snapshot.get("xp", xp_now)
    if xp_diff != 0:
        deltas.append({
            "label": f"✨ XP {xp_diff:+d}",
            "kind": "pos",
        })

    lvl_now = player.get("level", 1)
    lvl_diff = lvl_now - before_snapshot.get("level", lvl_now)
    if lvl_diff > 0:
        deltas.append({
            "label": f"🆙 Level Up! (Lv.{lvl_now})",
            "kind": "pos",
        })

    insp_now = state_manager.get_inspiration(player)
    insp_diff = insp_now - before_snapshot.get("inspiration", insp_now)
    if insp_diff != 0:
        deltas.append({
            "label": f"🌟 Inspiration {insp_diff:+d}",
            "kind": "pos" if insp_diff > 0 else "neu",
        })

    if rel_change and isinstance(rel_change, dict) and rel_change.get("delta", 0) != 0:
        d_val = int(rel_change["delta"])
        npc_n = rel_change.get("npc_name", rel_change.get("npc_id", "NPC"))
        deltas.append({
            "label": f"{'💚' if d_val > 0 else '💔'} {npc_n} {d_val:+d}",
            "kind": "pos" if d_val > 0 else "neg",
        })

    if isinstance(world, dict):
        loc_now = world.get("current_location", "")
        if loc_now and loc_now != before_snapshot.get("location", loc_now):
            r_obj = dungeon_manager._get_room(loc_now, world) or {}
            r_name = r_obj.get("name", loc_now)
            deltas.append({
                "label": f"📍 ย้ายสู่ {r_name}",
                "kind": "neu",
            })

        q_now = get_active_quest_count(world)
        q_diff = q_now - before_snapshot.get("quest_count", q_now)
        if q_diff > 0:
            deltas.append({
                "label": f"📜 ภารกิจใหม่ +{q_diff}",
                "kind": "neu",
            })

    return deltas


def render_top_status_banner(
    player: Dict[str, Any],
    world: Dict[str, Any],
    current_room: Dict[str, Any],
) -> None:
    """
    Render the '1 ใน 65 ล้าน' × 'Holy Grail War' Top Status Banner with clear visual progress bars,
    character identity, day/time period, and wealth/inspiration counters.
    """
    name = player.get("name", "Adventurer")
    race = player.get("race", "Human")
    cls_name = player.get("class_name", player.get("class", "Fighter"))
    sub_name = player.get("subclass_name") or player.get("subclass") or ""
    sub_display = f" ({sub_name.replace('_', ' ').title()})" if sub_name else ""
    lvl = player.get("level", 1)
    bg = player.get("background", "Folk Hero")

    gt = world.get("game_time", {})
    day = gt.get("day", 1)
    period = str(gt.get("period", "morning")).lower()
    period_icons = {
        "morning": ("☀️ เช้า (Morning)", "#fbbf24"),
        "afternoon": ("🌤️ บ่าย (Afternoon)", "#38bdf8"),
        "evening": ("🌅 พลบค่ำ (Evening)", "#f97316"),
        "night": ("🌙 กลางคืน (Night)", "#a855f7"),
    }
    p_label, _ = period_icons.get(period, ("☀️ เช้า", "#fbbf24"))

    hp = player.get("hp", {"current": 10, "max": 10})
    hp_cur = max(0, hp.get("current", 10))
    hp_max = max(1, hp.get("max", 10))
    hp_temp = hp.get("temp", player.get("temp_hp", 0))
    hp_pct = min(100, int((hp_cur / hp_max) * 100))
    hp_color = "#4ade80" if hp_pct >= 60 else ("#fbbf24" if hp_pct >= 30 else "#f87171")
    temp_str = f" (+{hp_temp})" if hp_temp and hp_temp > 0 else ""

    ac = player.get("ac", 10)
    ac_pct = min(100, int((ac / 22.0) * 100))

    xp_cur = player.get("xp_current", 0)
    thresholds = state_manager.LEVEL_THRESHOLDS
    next_xp = thresholds.get(min(5, lvl + 1), 6500)
    prev_xp = thresholds.get(lvl, 0)
    if lvl >= 5:
        xp_pct = 100
        xp_text = f"{xp_cur} (MAX)"
    else:
        span = max(1, next_xp - prev_xp)
        xp_pct = min(100, max(0, int(((xp_cur - prev_xp) / span) * 100)))
        xp_text = f"{xp_cur} / {next_xp}"

    insp_cur = state_manager.get_inspiration(player)
    insp_max = player.get("max_inspiration", state_manager.MAX_INSPIRATION)
    insp_pct = min(100, int((insp_cur / max(1, insp_max)) * 100))

    curr = get_player_currency(player)
    gp_val = curr.get("gp", 0)
    sp_val = curr.get("sp", 0)
    rations = get_ration_count(player)
    gold_pct = min(100, int((min(gp_val, 300) / 300.0) * 100))

    room_name = current_room.get("name", "Unknown")
    is_safe = current_room.get("is_safe", False)
    zone_badge = "🛡️ เขตปลอดภัย" if is_safe else "⚔️ เขตอันตราย"

    st.markdown(
        f"""
        <div class="hgw-status-banner">
            <div class="hgw-banner-top">
                <div>
                    <span class="hgw-char-title">◆ {name}</span>
                    <span style="color: #94a3b8; font-size: 0.9rem; margin-left: 8px;">
                        Lv.{lvl} {race} {cls_name}{sub_display} &bull; {bg}
                    </span>
                </div>
                <div style="display: flex; flex-wrap: wrap; gap: 6px;">
                    <span class="hgw-badge-pill">🗓️ วันที่ {day} &bull; {p_label}</span>
                    <span class="hgw-badge-pill">📍 {room_name} ({zone_badge})</span>
                </div>
            </div>
            <div class="hgw-stats-grid">
                <div class="hgw-stat-box">
                    <div class="hgw-stat-header">
                        <span>❤️ พลังชีวิต (HP)</span>
                        <span class="hgw-stat-val" style="color: {hp_color};">{hp_cur}/{hp_max}{temp_str}</span>
                    </div>
                    <div class="hgw-bar-track">
                        <div class="hgw-bar-fill" style="width: {hp_pct}%; background: {hp_color};"></div>
                    </div>
                </div>
                <div class="hgw-stat-box">
                    <div class="hgw-stat-header">
                        <span>🛡️ เกราะป้องกัน (AC)</span>
                        <span class="hgw-stat-val" style="color: #38bdf8;">{ac} AC</span>
                    </div>
                    <div class="hgw-bar-track">
                        <div class="hgw-bar-fill" style="width: {ac_pct}%; background: linear-gradient(90deg, #0284c7, #38bdf8);"></div>
                    </div>
                </div>
                <div class="hgw-stat-box">
                    <div class="hgw-stat-header">
                        <span>✨ ประสบการณ์ (XP)</span>
                        <span class="hgw-stat-val" style="color: #c084fc;">{xp_text}</span>
                    </div>
                    <div class="hgw-bar-track">
                        <div class="hgw-bar-fill" style="width: {xp_pct}%; background: linear-gradient(90deg, #7e22ce, #c084fc);"></div>
                    </div>
                </div>
                <div class="hgw-stat-box">
                    <div class="hgw-stat-header">
                        <span>🌟 โชคชะตา (Insp.)</span>
                        <span class="hgw-stat-val" style="color: #f3d08a;">{insp_cur} / {insp_max}</span>
                    </div>
                    <div class="hgw-bar-track">
                        <div class="hgw-bar-fill" style="width: {insp_pct}%; background: linear-gradient(90deg, #b45309, #f3d08a);"></div>
                    </div>
                </div>
                <div class="hgw-stat-box">
                    <div class="hgw-stat-header">
                        <span>💰 ทรัพย์สิน & เสบียง</span>
                        <span class="hgw-stat-val" style="color: #fbbf24;">{gp_val}G {sp_val}S &bull; 🍖{rations}</span>
                    </div>
                    <div class="hgw-bar-track">
                        <div class="hgw-bar-fill" style="width: {gold_pct}%; background: linear-gradient(90deg, #ca8a04, #facc15);"></div>
                    </div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_scene_stage_banner(current_room: Dict[str, Any], world: Dict[str, Any]) -> None:
    """
    Render the gold-framed Scene Stage with minimalist vector/silhouette illustration
    ('The Night the Grail Fills' wireframe gold style × '1 ใน 65 ล้าน' framed stage viewport).
    """
    room_name = current_room.get("name", "Unknown Room")
    room_desc = current_room.get("description", "")
    room_type = current_room.get("type", "wilderness")
    period = str(world.get("game_time", {}).get("period", "morning")).lower()

    sky_gradients = {
        "morning": "linear-gradient(180deg, #172038 0%, #24324f 65%, #121622 100%)",
        "afternoon": "linear-gradient(180deg, #132238 0%, #1e334d 65%, #111520 100%)",
        "evening": "linear-gradient(180deg, #21152b 0%, #3b2032 65%, #11131d 100%)",
        "night": "linear-gradient(180deg, #070910 0%, #101526 65%, #0a0c14 100%)",
    }
    bg_grad = sky_gradients.get(period, sky_gradients["morning"])

    if room_type == "town":
        svg_art = """
        <svg viewBox="0 0 800 120" width="100%" height="100%" preserveAspectRatio="none">
            <circle cx="670" cy="34" r="16" fill="none" stroke="#d4af6a" stroke-width="1.6" opacity="0.85"/>
            <circle cx="675" cy="34" r="13" fill="#131929" opacity="0.75"/>
            <path d="M0,115 L0,85 L55,85 L55,62 L85,42 L115,62 L115,85 L170,85 L170,55 L205,30 L240,55 L240,85 L310,85 L310,70 L360,70 L360,48 L385,22 L410,48 L410,70 L470,70 L470,85 L560,85 L560,58 L595,38 L630,58 L630,85 L800,85 L800,120 Z" fill="#0b0d14" stroke="#d4af6a" stroke-width="1.2" opacity="0.9"/>
            <line x1="385" y1="6" x2="385" y2="22" stroke="#f3d08a" stroke-width="1.5"/>
            <rect x="195" y="62" width="7" height="9" fill="#f3d08a" opacity="0.75"/>
            <rect x="208" y="62" width="7" height="9" fill="#f3d08a" opacity="0.75"/>
            <rect x="378" y="56" width="6" height="10" fill="#f3d08a" opacity="0.85"/>
            <rect x="388" y="56" width="6" height="10" fill="#f3d08a" opacity="0.85"/>
            <rect x="590" y="66" width="8" height="8" fill="#f3d08a" opacity="0.7"/>
        </svg>
        """
        type_pill = "🏘️ เขตเมือง / ชุมชน (Town)"
    elif room_type == "dungeon":
        svg_art = """
        <svg viewBox="0 0 800 120" width="100%" height="100%" preserveAspectRatio="none">
            <path d="M0,0 L800,0 L800,120 L0,120 Z" fill="none"/>
            <path d="M180,120 L180,55 A65,45 0 0,1 310,55 L310,120" fill="#07080d" stroke="#d4af6a" stroke-width="1.4" opacity="0.85"/>
            <path d="M490,120 L490,55 A65,45 0 0,1 620,55 L620,120" fill="#07080d" stroke="#d4af6a" stroke-width="1.4" opacity="0.85"/>
            <circle cx="400" cy="48" r="22" fill="none" stroke="#d4af6a" stroke-width="1.5" opacity="0.75"/>
            <polygon points="400,32 406,48 400,64 394,48" fill="#f3d08a" opacity="0.8"/>
            <line x1="120" y1="0" x2="120" y2="120" stroke="#d4af6a" stroke-width="1" opacity="0.35"/>
            <line x1="680" y1="0" x2="680" y2="120" stroke="#d4af6a" stroke-width="1" opacity="0.35"/>
        </svg>
        """
        type_pill = "🏰 ดันเจี้ยน / โบราณสถาน (Dungeon)"
    else:
        svg_art = """
        <svg viewBox="0 0 800 120" width="100%" height="100%" preserveAspectRatio="none">
            <circle cx="650" cy="32" r="15" fill="none" stroke="#f3d08a" stroke-width="1.5" opacity="0.85"/>
            <path d="M0,115 L90,62 L175,102 L290,42 L410,98 L530,48 L650,96 L750,58 L800,88 L800,120 L0,120 Z" fill="#0d111c" stroke="#d4af6a" stroke-width="1.1" opacity="0.75"/>
            <polygon points="140,115 155,70 170,115" fill="#090b12" stroke="#d4af6a" stroke-width="1"/>
            <polygon points="220,115 238,62 256,115" fill="#090b12" stroke="#d4af6a" stroke-width="1"/>
            <polygon points="580,115 596,66 612,115" fill="#090b12" stroke="#d4af6a" stroke-width="1"/>
        </svg>
        """
        type_pill = "🌲 ป่าและดินแดนธรรมชาติ (Wilderness)"

    npcs = current_room.get("npcs", [])
    npc_str = f"🧑 ผู้คนในฉาก: {', '.join(n.replace('_', ' ').title() for n in npcs)}" if npcs else "🍃 ไร้ผู้คนในบริเวณใกล้เคียง"

    st.markdown(
        f"""
        <div class="hgw-scene-stage">
            <div class="hgw-scene-visual" style="background: {bg_grad};">
                {svg_art}
            </div>
            <div class="hgw-scene-body">
                <div class="hgw-scene-header">
                    <h2 class="hgw-scene-name">◆ {room_name}</h2>
                    <div style="display: flex; gap: 6px; flex-wrap: wrap;">
                        <span class="hgw-badge-pill">{type_pill}</span>
                        <span class="hgw-badge-pill">{npc_str}</span>
                    </div>
                </div>
                <p class="hgw-scene-desc">{room_desc}</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_navigation_column(player: Dict[str, Any], world: Dict[str, Any]):
    """Render left column: either Jail/Captive UI or 4-directional Compass."""
    is_locked_up = (
        player.get("status") == "captive"
        or bool(player.get("crime_state", {}).get("imprisoned"))
        or bool(world.get("is_imprisoned"))
    )
    if is_locked_up:
        cap_reason = player.get("captivity_reason")
        if not cap_reason:
            if player.get("crime_state", {}).get("imprisoned") or world.get("is_imprisoned"):
                cap_reason = "crime"
            else:
                cap_reason = "combat_defeat"
        if cap_reason == "crime":
            st.markdown("##### ⚖️ Imprisoned in Town Jail")
            days_left = player.get("prison_days_left", 1)
            st.warning(f"You are locked in a jail cell! Remaining sentence: **{days_left} day(s)**.")
            confiscated = player.get("confiscated_items", [])
            if confiscated:
                st.caption(f"🔒 {len(confiscated)} personal item(s) locked in the evidence chest.")

            col_srv, col_brk = st.columns(2)
            with col_srv:
                if st.button("⚖️ Serve Time", use_container_width=True, type="primary"):
                    res_s = state_manager.serve_prison_time(player, world)
                    st.toast("Sentence completed!")
                    st.session_state["narrative_log"].append({
                        "role": "assistant",
                        "content": f"⚖️ **Sentence Served:** {res_s['message']}"
                    })
                    auto_save()
                    st.rerun()
            with col_brk:
                if st.button("🔓 Lockpick Escape", use_container_width=True):
                    res_e = state_manager.attempt_lockpick_escape(player, world)
                    if res_e["success"]:
                        st.toast("Escape Successful!")
                        st.session_state["narrative_log"].append({
                            "role": "assistant",
                            "content": f"🎉 **Prison Break:** {res_e['message']}"
                        })
                    else:
                        st.error(res_e["message"])
                        st.session_state["narrative_log"].append({
                            "role": "assistant",
                            "content": f"❌ **Prison Break Failed:** {res_e['message']}"
                        })
                    auto_save()
                    st.rerun()
        else:
            st.markdown("##### 🔒 Captive — Attempt Escape")
            st.info("You are held captive! Normal movement is disabled until you escape.")
            if st.button("🔓 Attempt Escape", use_container_width=True, type="primary"):
                esc_out = attempt_escape(world, player)
                esc_res = esc_out["res"]

                if esc_out["pending_reroll"]:
                    st.session_state["pending_inspiration_reroll"] = esc_out["pending_reroll"]
                else:
                    st.session_state.pop("pending_inspiration_reroll", None)

                if esc_out["success"]:
                    safe_room = esc_out["safe_room"]
                    st.success("🎉 Escape Successful!")
                    st.session_state["narrative_log"].append({
                        "role": "assistant",
                        "content": f"🔓 **Escape Successful!** (Rolled {esc_res['total']} vs DC {esc_res['dc']}). You broke free from your bonds and fled to **{safe_room}**."
                    })
                else:
                    st.error("❌ Escape Failed!")
                    st.session_state["narrative_log"].append({
                        "role": "assistant",
                        "content": f"🔒 **Escape Failed!** (Rolled {esc_res['total']} vs DC {esc_res['dc']}). The guards catch you. Time passes..."
                    })
                auto_save()
                st.rerun()
    else:
        st.markdown("##### 🧭 Navigation & Movement")
        exits = dungeon_manager.get_available_exits(world)

        def _render_move_btn(dir_key, icon):
            dest_id = exits.get(dir_key)
            if dest_id is not None:
                dest_room = dungeon_manager._get_room(dest_id, world)
                dest_name = dest_room.get("name", dest_id) if dest_room else dest_id
                btn_label = f"{icon} {dir_key.capitalize()}"
                if st.button(btn_label, key=f"move_{dir_key}", use_container_width=True, help=f"เดินไปทางทิศ {dir_key.capitalize()}: {dest_name}"):
                    ok_m, msg_m, new_room = dungeon_manager.move_player(dir_key, world, player)
                    if ok_m and new_room:
                        st.session_state.pop("pending_inspiration_reroll", None)
                        st.session_state.pop("active_visiting_shop", None)
                        st.session_state["last_action_msg"] = msg_m
                        st.session_state["narrative_log"].append({
                            "role": "user",
                            "content": f"ฉันออกเดินทางไปทางทิศ {dir_key} มุ่งหน้าสู่ {new_room.get('name')}."
                        })
                        auto_save()
                        st.rerun()
            else:
                st.button(f"⛔ {dir_key.capitalize()}", key=f"move_disabled_{dir_key}", disabled=True, use_container_width=True, help="ทางตัน ไม่มีทางไป")

        # Row 1: North
        c_n1, c_n2, c_n3 = st.columns([1, 1.6, 1])
        with c_n2:
            _render_move_btn("north", "⬆️")

        # Row 2: West, Compass Center, East
        c_w, c_mid, c_e = st.columns([1.2, 0.6, 1.2])
        with c_w:
            _render_move_btn("west", "⬅️")
        with c_mid:
            st.markdown("<div style='text-align: center; font-size: 1.4rem; padding-top: 6px;'>🧭</div>", unsafe_allow_html=True)
        with c_e:
            _render_move_btn("east", "➡️")

        # Row 3: South
        c_s1, c_s2, c_s3 = st.columns([1, 1.6, 1])
        with c_s2:
            _render_move_btn("south", "⬇️")

        # Connected paths info card below compass
        avail_routes = []
        for d_k, d_icon in [("north", "⬆️"), ("east", "➡️"), ("south", "⬇️"), ("west", "⬅️")]:
            if exits.get(d_k) is not None:
                d_room = dungeon_manager._get_room(exits[d_k], world)
                d_name = d_room.get("name", exits[d_k]) if d_room else exits[d_k]
                d_type = d_room.get("type", "").capitalize() if d_room else ""
                avail_routes.append((d_k, d_icon, d_name, d_type))

        if avail_routes:
            with st.container(border=True):
                st.caption("🧭 **เส้นทางที่เปิดออก (Exits)**")
                for r_dir, r_icon, r_name, r_type in avail_routes:
                    type_badge = f" *({r_type})*" if r_type else ""
                    st.markdown(f"**{r_icon} {r_dir.capitalize()}:** {r_name}{type_badge}")
        else:
            st.caption("⛔ บริเวณนี้ไม่มีทางออกที่ชัดเจน (Dead End)")

        # Interactive World Atlas & Discovered Locations Minimap
        visited_ids = list(world.get("visited_rooms", []))
        cleared_ids = set(world.get("cleared_rooms", []))
        cur_id = world.get("current_location")
        if visited_ids:
            with st.expander(f"🗺️ แผนที่โลกที่สำรวจแล้ว ({len(visited_ids)} พื้นที่)", expanded=False):
                for vid in visited_ids:
                    v_room = dungeon_manager._get_room(vid, world)
                    if not v_room:
                        continue
                    v_name = v_room.get("name", vid)
                    v_type = v_room.get("type", "wilderness")
                    t_icon = "🏘️" if v_type == "town" else ("🏰" if v_type == "dungeon" else "🌲")
                    here_badge = " 📍 **(อยู่ที่นี่)**" if vid == cur_id else ""
                    clear_badge = " ✅" if (vid in cleared_ids or v_room.get("is_safe")) else ""
                    st.markdown(f"{t_icon} **{v_name}**{here_badge}{clear_badge}")


def _render_actions_tab(player: Dict[str, Any], world: Dict[str, Any], current_room: Dict[str, Any]):
    """Render Tab 1: BG3 Skill Checks, Suggested Actions, Chat Form, and Manual Rolls/Pickpocket/Spells."""
    pending_bg3 = st.session_state.get("pending_bg3_check")
    if pending_bg3:
        bg3_action = pending_bg3["action"]
        bg3_spec = pending_bg3["spec"]
        bg3_stat = bg3_spec["stat"]
        bg3_skill = bg3_spec["skill_name"]
        bg3_thai = bg3_spec["skill_thai"]
        bg3_dc = bg3_spec["dc"]
        bg3_diff = bg3_spec["difficulty"]
        bg3_mod = bg3_spec["modifier"]
        bg3_prof = bg3_spec["proficiency"]
        bg3_is_prof = bg3_spec["is_proficient"]
        bg3_tot_mod = bg3_spec["total_modifier"]
        bg3_chance_pct = calculate_check_success_chance(bg3_tot_mod, bg3_dc)

        st.markdown(
            f"""
            <div style="background: linear-gradient(135deg, rgba(22, 26, 39, 0.97) 0%, rgba(12, 14, 22, 0.99) 100%); 
                        border: 2px solid #d4af6a; border-radius: 12px; padding: 18px; margin-bottom: 20px;
                        box-shadow: 0 6px 24px rgba(212, 175, 106, 0.25);">
                <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">
                    <span style="font-family: 'Cinzel', serif; font-size: 1.15em; font-weight: 700; color: #f3d08a; letter-spacing: 0.05em;">
                        ◆ D&D 5e / BG3 — ABILITY CHECK
                    </span>
                    <span style="background: rgba(212, 175, 106, 0.2); border: 1px solid #d4af6a; color: #f3d08a; padding: 2px 12px; border-radius: 20px; font-size: 0.85em; font-weight: 600;">
                        DC {bg3_dc} ({bg3_diff.capitalize()}) &bull; โอกาสสำเร็จ {bg3_chance_pct}%
                    </span>
                </div>
                <div style="font-size: 1.05em; color: #f1f5f9; margin-bottom: 12px;">
                    🎯 <b>การกระทำ:</b> <i>"{bg3_action}"</i>
                </div>
                <div style="background: rgba(255, 255, 255, 0.04); border: 1px solid rgba(212, 175, 106, 0.2); border-radius: 8px; padding: 10px 14px; margin-bottom: 12px; display: flex; justify-content: space-around; text-align: center;">
                    <div>
                        <div style="font-size: 0.8em; color: #94a3b8;">ทักษะ / สแตท</div>
                        <div style="font-size: 1.1em; font-weight: 600; color: #38bdf8;">{bg3_stat} ({bg3_skill})</div>
                        <div style="font-size: 0.75em; color: #cbd5e1;">{bg3_thai}</div>
                    </div>
                    <div style="border-left: 1px solid rgba(255,255,255,0.1); padding-left: 14px;">
                        <div style="font-size: 0.8em; color: #94a3b8;">สแตทโมดิฟายเออร์</div>
                        <div style="font-size: 1.1em; font-weight: 600; color: #f1f5f9;">{bg3_mod:+d}</div>
                    </div>
                    <div style="border-left: 1px solid rgba(255,255,255,0.1); padding-left: 14px;">
                        <div style="font-size: 0.8em; color: #94a3b8;">ความชำนาญ (Proficiency)</div>
                        <div style="font-size: 1.1em; font-weight: 600; color: {'#4ade80' if bg3_is_prof else '#64748b'};">
                            {f"{bg3_prof:+d}" if bg3_is_prof else 'ไม่มี'}
                        </div>
                    </div>
                    <div style="border-left: 1px solid rgba(255,255,255,0.1); padding-left: 14px;">
                        <div style="font-size: 0.8em; color: #94a3b8;">โบนัสรวม</div>
                        <div style="font-size: 1.2em; font-weight: 700; color: #f3d08a;">{bg3_tot_mod:+d}</div>
                    </div>
                    <div style="border-left: 1px solid rgba(255,255,255,0.1); padding-left: 14px;">
                        <div style="font-size: 0.8em; color: #94a3b8;">โอกาสสำเร็จ</div>
                        <div style="font-size: 1.2em; font-weight: 700; color: {'#4ade80' if bg3_chance_pct >= 65 else ('#fbbf24' if bg3_chance_pct >= 40 else '#f87171')};">{bg3_chance_pct}%</div>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        insp_cnt = state_manager.get_inspiration(player)
        use_insp = st.checkbox(
            f"✨ ใช้แต้ม Inspiration (ทอยเต๋า 2 ลูกเลือกแต้มสูงสุด — มี {insp_cnt} แต้ม)",
            value=False,
            disabled=(insp_cnt <= 0),
            key="bg3_chk_use_insp",
        )

        c_roll, c_cancel = st.columns([3, 1])
        with c_roll:
            roll_clicked = st.button("🎲 ทอยเต๋า d20 (Roll Check)", type="primary", use_container_width=True, key="btn_bg3_roll")
        with c_cancel:
            cancel_clicked = st.button("❌ ข้าม / ทำอย่างอื่น", use_container_width=True, key="btn_bg3_cancel")

        if cancel_clicked:
            st.session_state.pop("pending_bg3_check", None)
            st.rerun()

        if roll_clicked:
            before_snap = snapshot_player_world_state(player, world)
            chk_res = state_manager.resolve_check(
                stat=bg3_stat,
                difficulty=bg3_diff,
                state=player,
                proficient=bg3_is_prof,
                skill=bg3_spec["skill"],
                use_inspiration=use_insp,
            )

            raw_die = chk_res["roll"]
            tot_val = chk_res["total"]
            dc_val = chk_res["dc"]
            chk_mod = chk_res.get("modifier", 0)
            chk_prof = chk_res.get("proficiency", 0)
            chk_bonus = chk_res.get("bonus", 0)
            tot_bonus = chk_mod + chk_prof + chk_bonus
            is_succ = chk_res["success"]
            is_crit = chk_res.get("critical", False)
            is_fumble = chk_res.get("fumble", False)

            if is_crit:
                badge = "🌟 CRITICAL SUCCESS (Nat 20)!"
                st.balloons()
            elif is_fumble:
                badge = "💀 CRITICAL FAILURE (Nat 1)!"
            elif is_succ:
                badge = "✅ SUCCESS (สำเร็จ)!"
            else:
                badge = "❌ FAILURE (ล้มเหลว)!"

            insp_note = " (ใช้แต้ม Inspiration ✨)" if chk_res.get("inspiration_spent") else ""
            roll_summary_msg = (
                f"🎲 **[BG3 Check] {bg3_stat} ({bg3_skill}) vs DC {dc_val}**\n\n"
                f"ทอยเต๋า d20 ได้ `[{raw_die}]` + โบนัส `{tot_bonus:+d}` = **`{tot_val}`** vs **DC `{dc_val}`** → **{badge}**{insp_note}"
            )

            st.session_state["narrative_log"].append({"role": "user", "content": bg3_action})
            st.session_state["narrative_log"].append({"role": "assistant", "content": roll_summary_msg})

            roll_inj = (
                f"[System: Roll Result — {bg3_stat} ({bg3_skill}) Check vs DC {dc_val}]\n"
                f"Player attempted action: '{bg3_action}'\n"
                f"Roll outcome: Rolled {raw_die} + {tot_bonus:+d} = {tot_val} vs DC {dc_val} -> {badge}.\n"
                f"MANDATORY: The check was a {'SUCCESS' if is_succ else 'FAILURE'}. "
                f"You MUST narrate the consequence reflecting this {'successful' if is_succ else 'failed'} attempt in rich Thai prose (3-4 paragraphs)."
            )

            hist = st.session_state.get("history_buffer", [])
            active_model = get_current_active_model()
            mm = get_session_memory_manager()
            relevant_lore = []
            if mm is not None:
                try:
                    relevant_lore = mm.get_relevant_lore(f"{current_room.get('name', '')} {bg3_action}", n_results=3)
                except Exception:
                    relevant_lore = []

            rel_res_turn = None
            with st.spinner("🎲 DM กำลังสรุปผลการทอยและเล่าเรื่องต่อ..."):
                res = llm_handler.generate_narrative_response(
                    user_input=bg3_action,
                    player_state=player,
                    world_state=world,
                    history=hist,
                    lore_entries=relevant_lore,
                    model=active_model,
                    roll_result=roll_inj,
                    single_pass=True,
                    ollama_model=st.session_state.get("selected_model"),
                )
                narrative_text = res.get("narrative", "")
                if res.get("suggestions"):
                    st.session_state["action_suggestions"] = res["suggestions"]

                ext_res = resolve_turn_extraction(
                    action=bg3_action,
                    narrative_text=narrative_text,
                    res=res,
                    player=player,
                    world=world,
                    active_model=active_model,
                    ollama_model=st.session_state.get("selected_model"),
                )

                if ext_res:
                    if "world_updates" in ext_res and ext_res["world_updates"]:
                        w_up = ext_res["world_updates"]
                        if "new_location" in w_up and w_up["new_location"]:
                            ok_nl, _ = dungeon_manager.register_new_location(w_up["new_location"], world)
                            if ok_nl:
                                nl_name = w_up["new_location"].get("name", "New Area")
                                st.session_state["narrative_log"].append({
                                    "role": "assistant",
                                    "content": f"🗺️ **New Area Discovered:** {nl_name}!"
                                })
                    if "state_updates" in ext_res and ext_res["state_updates"]:
                        state_manager.apply_state_updates(ext_res["state_updates"], player, world)
                    if "quest_updates" in ext_res and ext_res["quest_updates"]:
                        state_manager.apply_quest_updates(ext_res["quest_updates"], world)
                    if "npc_relationship_change" in ext_res and ext_res["npc_relationship_change"]:
                        rel_res_turn = state_manager.apply_npc_relationship_change(ext_res["npc_relationship_change"], world)
                        if rel_res_turn and rel_res_turn.get("delta", 0) != 0:
                            d_val = rel_res_turn["delta"]
                            icon_r = "💚" if d_val > 0 else "💔"
                            st.toast(f"{icon_r} {rel_res_turn['npc_name']}: ความสัมพันธ์ {d_val:+d}")
                    if "combat_start" in ext_res and ext_res["combat_start"]:
                        c_enemies = ext_res["combat_start"].get("enemies", [])
                        if c_enemies:
                            combat_manager.start_combat(c_enemies, player, world)
                            st.session_state["narrative_log"].append({
                                "role": "assistant",
                                "content": f"⚔️ Combat initiated against **{', '.join(e.replace('_', ' ').title() for e in c_enemies)}**!"
                            })

                turn_deltas = compute_turn_stat_deltas(before_snap, player, world, rel_change=rel_res_turn)
                turn_deltas.insert(0, {
                    "label": f"🎲 {bg3_stat} ({bg3_skill}) {tot_val} vs DC {dc_val}",
                    "kind": "pos" if is_succ else "neg",
                })
                st.session_state["last_turn_deltas"] = turn_deltas
                st.session_state["narrative_log"].append({
                    "role": "assistant",
                    "content": narrative_text,
                    "deltas": turn_deltas,
                })
                st.session_state["history_buffer"].append({"role": "user", "content": bg3_action})
                st.session_state["history_buffer"].append({"role": "assistant", "content": narrative_text})
                if mm is not None and narrative_text:
                    try:
                        mm.add_turn(f"[{current_room.get('name', 'Location')}] Player: {bg3_action} -> Roll: {badge} -> Outcome: {narrative_text[:280]}")
                    except Exception:
                        pass

            st.session_state.pop("pending_bg3_check", None)
            auto_save()
            st.rerun()

        st.stop()

    # Contextual Action Suggestions — '1 in 65 Million' × 'Holy Grail War' Transparent Choice Cards
    suggestions = st.session_state.get("action_suggestions") or list(llm_handler.DEFAULT_ACTION_SUGGESTIONS)
    if suggestions == llm_handler.DEFAULT_ACTION_SUGGESTIONS:
        suggestions = [
            "[Persuasion] พูดคุยสอบถามข่าวลือและขอคำแนะนำจากชาวบ้านในจัตุรัส",
            "เดินไปที่โรงเตี๊ยมมังกรเมาเพื่อดูกระดานประกาศภารกิจ",
            "[Insight] สังเกตท่าทีของผู้คนและแวะดูสินค้าที่ร้านค้า",
        ]
        st.session_state["action_suggestions"] = list(suggestions)

    st.markdown("##### ◆ ทางเลือกการตัดสินใจ (Decision Choices — แสดงโอกาสสำเร็จ & ทักษะที่ใช้)")
    sug_cols = st.columns(3)
    clicked_suggestion = None
    for idx, sug_text in enumerate(suggestions[:3]):
        meta = build_choice_card_metadata(sug_text, player, index=idx + 1)
        with sug_cols[idx]:
            st.markdown(
                f"""
                <div class="hgw-choice-preview">
                    <div class="hgw-choice-top">
                        <span class="hgw-choice-num">[{meta['index']}] {meta['category_label']}</span>
                        <span class="hgw-choice-odds" style="color: {meta['odds_color']}; background: {meta['odds_bg']}; border: 1px solid {meta['odds_border']};">
                            {meta['odds_label']}
                        </span>
                    </div>
                    <div class="hgw-choice-meta">{meta['sub_caption']}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if st.button(f"[{idx + 1}] {sug_text}", key=f"sug_btn_{idx}", use_container_width=True):
                clicked_suggestion = sug_text

    # Action Text Input Form
    with st.form("action_form", clear_on_submit=True):
        user_action = st.text_input("What do you want to do?", placeholder="e.g. Look around the room, talk to the merchant...")
        sub_act = st.form_submit_button("Submit Action", use_container_width=True)

    # ── 🎭 Social Roleplay & NPC Dialogue Panel (Tabletop D&D Style) ──────────
    room_npcs = list(current_room.get("npcs", []))
    party_comps = [c.get("name", c.get("id")) for c in world.get("party", {}).get("companions", []) if isinstance(c, dict)]
    all_social_targets = list(dict.fromkeys([f"🧑 {n.replace('_', ' ').title()}" for n in room_npcs] + [f"🤝 {cn}" for cn in party_comps]))

    clicked_social_action = None
    with st.expander("🗣️ สนทนา & สวมบทบาทตามคลาส/ทักษะ (Social Roleplay & Dialogue Builder)", expanded=False):
        p_cls = player.get("class_name", player.get("class", "Fighter"))
        p_bg = player.get("background", "Folk Hero")
        col_soc_t, col_soc_a = st.columns(2)
        with col_soc_t:
            if all_social_targets:
                soc_target = st.selectbox("🎯 คู่สนทนาในฉาก:", ["ทุกคนในบริเวณนี้"] + all_social_targets, key="soc_target_sel")
            else:
                soc_target = "ผู้คนในบริเวณนี้"
                st.caption("💬 สามารถพูดคุยกับสิ่งแวดล้อมหรือตัวละครในฉากได้อย่างอิสระ")
        with col_soc_a:
            soc_approach = st.selectbox(
                "🎭 ท่าที / กลยุทธ์การพูดคุย:",
                [
                    "💬 พูดคุยทั่วไป (Casual Conversation)",
                    "[Persuasion] 🕊️ หว่านล้อม / เจรจาต่อรองอย่างมีเหตุผล",
                    "[Deception] 🃏 โกหก / ปั้นน้ำเป็นตัว / ตบตา",
                    "[Intimidation] 👹 ข่มขู่ / กดดันด้วยท่าทีน่าเกรงขาม",
                    "[Insight] 👁️ จับโกหก / อ่านสีหน้าและเจตนาที่ซ่อนอยู่",
                    f"[{p_cls}] 🌟 ใช้เอกลักษณ์คลาส {p_cls} ในการสนทนา",
                    f"[{p_bg}] 📜 อ้างอิงประสบการณ์ภูมิหลัง ({p_bg})",
                ],
                key="soc_approach_sel",
            )
        soc_custom_line = st.text_input(
            "💬 คำพูดหรือประเด็นที่ต้องการคุย:",
            placeholder="เช่น 'เล่าความลับของหมู่บ้านนี้ให้ฟังหน่อย' หรือ 'วางอาวุธลงซะถ้าไม่อยากเจ็บตัว!'",
            key="soc_custom_line_in",
        )
        if st.button("🗣️ เริ่มบทสนทนา (Speak / Roleplay)", use_container_width=True, key="btn_soc_speak"):
            clean_tgt = soc_target.replace("🧑 ", "").replace("🤝 ", "")
            prefix_tag = ""
            if soc_approach.startswith("["):
                prefix_tag = soc_approach.split("]")[0] + "] "
            if soc_custom_line.strip():
                clicked_social_action = f'{prefix_tag}พูดคุยกับ {clean_tgt}: "{soc_custom_line.strip()}"'
            else:
                approach_desc = soc_approach.split("]")[-1].strip()
                clicked_social_action = f"{prefix_tag}{approach_desc} กับ {clean_tgt}"

    action_to_process = None
    if clicked_suggestion:
        action_to_process = clicked_suggestion.strip()
    elif clicked_social_action:
        action_to_process = clicked_social_action.strip()
    elif sub_act and user_action.strip():
        action_to_process = user_action.strip()

    if action_to_process:
        st.session_state.pop("pending_inspiration_reroll", None)
        check_spec = check_manager.detect_action_skill_check(action_to_process, player)
        if check_spec:
            st.session_state["pending_bg3_check"] = {
                "action": action_to_process,
                "spec": check_spec,
            }
            st.rerun()

        before_snap = snapshot_player_world_state(player, world)
        st.session_state["narrative_log"].append({"role": "user", "content": action_to_process})

        hist = st.session_state.get("history_buffer", [])
        active_model = get_current_active_model()
        mm = get_session_memory_manager()
        relevant_lore = []
        if mm is not None:
            try:
                relevant_lore = mm.get_relevant_lore(f"{current_room.get('name', '')} {action_to_process}", n_results=3)
            except Exception:
                relevant_lore = []

        with st.spinner("📖 DM กำลังเล่าเรื่องราวต่อ..."):
            res = llm_handler.generate_narrative_response(
                user_input=action_to_process,
                player_state=player,
                world_state=world,
                history=hist,
                lore_entries=relevant_lore,
                model=active_model,
                single_pass=True,
                ollama_model=st.session_state.get("selected_model"),
            )
            narrative_text = res.get("narrative", "")
            if res.get("suggestions"):
                st.session_state["action_suggestions"] = res["suggestions"]

            ext_res = resolve_turn_extraction(
                action=action_to_process,
                narrative_text=narrative_text,
                res=res,
                player=player,
                world=world,
                active_model=active_model,
                ollama_model=st.session_state.get("selected_model"),
            )

        rel_res_turn = None
        if ext_res:
            if "world_updates" in ext_res and ext_res["world_updates"]:
                w_up = ext_res["world_updates"]
                if "new_location" in w_up and w_up["new_location"]:
                    ok_nl, reason_nl = dungeon_manager.register_new_location(w_up["new_location"], world)
                    if ok_nl:
                        nl_name = w_up["new_location"].get("name", "New Area")
                        st.session_state["narrative_log"].append({
                            "role": "assistant",
                            "content": f"🗺️ **New Area Discovered:** {nl_name}!"
                        })
            if "state_updates" in ext_res and ext_res["state_updates"]:
                state_manager.apply_state_updates(ext_res["state_updates"], player, world)
            if "quest_updates" in ext_res and ext_res["quest_updates"]:
                state_manager.apply_quest_updates(ext_res["quest_updates"], world)
            if "npc_relationship_change" in ext_res and ext_res["npc_relationship_change"]:
                rel_res_turn = state_manager.apply_npc_relationship_change(ext_res["npc_relationship_change"], world)
                if rel_res_turn and rel_res_turn.get("delta", 0) != 0:
                    d_val = rel_res_turn["delta"]
                    icon_r = "💚" if d_val > 0 else "💔"
                    st.toast(f"{icon_r} {rel_res_turn['npc_name']}: ความสัมพันธ์ {d_val:+d}")
            if "combat_start" in ext_res and ext_res["combat_start"]:
                c_enemies = ext_res["combat_start"].get("enemies", [])
                if c_enemies:
                    combat_manager.start_combat(c_enemies, player, world)
                    st.session_state["narrative_log"].append({
                        "role": "assistant",
                        "content": f"⚔️ Combat initiated against **{', '.join(e.replace('_', ' ').title() for e in c_enemies)}**!"
                    })
            if "requires_roll" in ext_res and ext_res["requires_roll"]:
                rr = ext_res["requires_roll"]
                stat = rr.get("stat", "STR")
                diff = rr.get("difficulty", "medium")
                chk_res = state_manager.resolve_check(stat, diff, player)
                succ_str = "SUCCESS ✅" if chk_res["success"] else "FAILURE ❌"
                st.session_state["narrative_log"].append({
                    "role": "assistant",
                    "content": f"🎲 **Automatic Check ({diff.capitalize()} {stat} DC {chk_res['dc']})**: Rolled {chk_res['roll']} + {chk_res['modifier']} = **{chk_res['total']}** → **{succ_str}**"
                })
                if not chk_res["success"] and state_manager.get_inspiration(player) > 0:
                    st.session_state["pending_inspiration_reroll"] = {
                        "stat": stat,
                        "difficulty": diff,
                        "dc": chk_res["dc"],
                        "modifier": chk_res["modifier"],
                        "proficiency": chk_res.get("proficiency", 0),
                        "bonus": chk_res.get("bonus", 0),
                        "original_roll": chk_res["roll"],
                        "original_total": chk_res["total"],
                        "skill": chk_res.get("skill"),
                    }
                else:
                    st.session_state.pop("pending_inspiration_reroll", None)

        turn_deltas = compute_turn_stat_deltas(before_snap, player, world, rel_change=rel_res_turn)
        st.session_state["last_turn_deltas"] = turn_deltas
        st.session_state["narrative_log"].append({
            "role": "assistant",
            "content": narrative_text,
            "deltas": turn_deltas,
        })
        st.session_state["history_buffer"].append({"role": "user", "content": action_to_process})
        st.session_state["history_buffer"].append({"role": "assistant", "content": narrative_text})
        if mm is not None and narrative_text:
            try:
                mm.add_turn(f"[{current_room.get('name', 'Location')}] Player: {action_to_process} -> DM: {narrative_text[:280]}")
            except Exception:
                pass

        auto_save()
        st.rerun()

    st.markdown("---")
    st.caption("🎲 **Manual Actions & Checks:**")
    known_spells = player.get("known_spells", [])
    has_ooc_spells = bool(known_spells)
    room_npcs = current_room.get("npcs", [])

    col_counts = 2
    if has_ooc_spells:
        col_counts += 1
    if room_npcs:
        col_counts += 1
    cols_actions = st.columns(col_counts)

    c_idx = 0
    with cols_actions[c_idx]:
        c_idx += 1
        with st.popover("🎲 Make Ability Check"):
            chk_stat = st.selectbox("Stat:", ["STR", "DEX", "CON", "INT", "WIS", "CHA"])
            chk_diff = st.selectbox("Difficulty:", ["easy", "medium", "hard", "very_hard"])
            chk_skill = st.text_input("Skill (optional, e.g. Arcana, Stealth, Athletics):", "")
            insp_count = state_manager.get_inspiration(player)
            use_insp = st.checkbox(f"Use Inspiration (Reroll, have {insp_count})", disabled=(insp_count <= 0))
            if st.button("Roll d20", use_container_width=True):
                chk_res = state_manager.resolve_check(
                    chk_stat,
                    chk_diff,
                    player,
                    skill=chk_skill.strip() if chk_skill else None,
                    use_inspiration=use_insp,
                )
                succ_str = "SUCCESS ✅" if chk_res["success"] else "FAILURE ❌"
                insp_tag = " (Inspiration Used ✨)" if chk_res.get("inspiration_spent") else ""
                skill_tag = f" [{chk_res['skill']}]" if chk_res.get("skill") else ""
                roll_msg = (
                    f"🎲 **{chk_stat}{skill_tag} Check ({chk_diff.capitalize()} DC {chk_res['dc']})**: "
                    f"Rolled {chk_res['roll']} + mod {chk_res['modifier']} = **{chk_res['total']}** → **{succ_str}**{insp_tag}"
                )
                st.session_state["narrative_log"].append({"role": "assistant", "content": roll_msg})
                if not chk_res["success"] and state_manager.get_inspiration(player) > 0:
                    st.session_state["pending_inspiration_reroll"] = {
                        "stat": chk_stat,
                        "difficulty": chk_diff,
                        "dc": chk_res["dc"],
                        "modifier": chk_res["modifier"],
                        "proficiency": chk_res.get("proficiency", 0),
                        "bonus": chk_res.get("bonus", 0),
                        "original_roll": chk_res["roll"],
                        "original_total": chk_res["total"],
                        "skill": chk_skill.strip() if chk_skill else None,
                    }
                else:
                    st.session_state.pop("pending_inspiration_reroll", None)
                auto_save()
                st.rerun()

    if room_npcs:
        with cols_actions[c_idx]:
            c_idx += 1
            with st.popover("🦹 Pickpocket"):
                st.caption("Attempt to steal from an NPC using Sleight of Hand.")
                target_npc = st.selectbox("Target NPC", room_npcs, key="pp_target_npc")
                st_item = st.selectbox("Item to Steal", ["gold", "healing_potion", "torch", "rope", "dagger"], key="pp_item")
                if st.button("🧤 Attempt Steal", use_container_width=True, key="btn_pp_steal"):
                    pp_res = state_manager.attempt_pickpocket(player, target_npc, st_item, world_state=world)
                    if pp_res["success"]:
                        st.toast(pp_res["message"])
                        st.session_state["narrative_log"].append({
                            "role": "assistant",
                            "content": f"🦹 **Pickpocket:** {pp_res['message']} (Rolled {pp_res['total']} vs DC {pp_res['dc']})"
                        })
                    else:
                        st.error(pp_res["message"])
                        st.session_state["narrative_log"].append({
                            "role": "assistant",
                            "content": f"🚨 **Caught!** {pp_res['message']} (Rolled {pp_res['total']} vs DC {pp_res['dc']})"
                        })
                    auto_save()
                    st.rerun()

    with cols_actions[c_idx]:
        c_idx += 1
        if st.button("⚔️ Attack / Trigger Combat", use_container_width=True):
            enc_table = current_room.get("encounter_table", ["goblin_scout"])
            monster_id = enc_table[0] if enc_table else "goblin_scout"

            combat_manager.start_combat([monster_id], player, world)
            auto_save()
            st.session_state["narrative_log"].append({
                "role": "assistant",
                "content": f"⚔️ Combat initiated against **{monster_id.replace('_', ' ').title()}**!"
            })
            st.rerun()

    if has_ooc_spells:
        with cols_actions[c_idx]:
            c_idx += 1
            with st.popover("✨ Cast Spell"):
                sp_cat = get_display_spell_catalog(world)

                def _fmt_ooc_sp(sid):
                    sinfo = sp_cat.get(sid, {})
                    slvl = sinfo.get("level", 0)
                    lvl_tag = "Cantrip" if slvl == 0 else f"Lvl {slvl}"
                    return f"{sinfo.get('name', sid)} ({lvl_tag})"

                ooc_map = {_fmt_ooc_sp(s): s for s in known_spells}
                chosen_ooc_lbl = st.selectbox("Spell:", list(ooc_map.keys()), key="ooc_sp_sel")
                chosen_ooc_id = ooc_map[chosen_ooc_lbl]
                ooc_data = sp_cat.get(chosen_ooc_id, {})
                ooc_eff = ooc_data.get("effect", {})
                ooc_desc = ooc_data.get("description", "")
                ooc_details = []
                if "heal" in ooc_eff:
                    ooc_details.append(f"💚 Heal: {ooc_eff['heal']}")
                if "damage" in ooc_eff:
                    ooc_details.append(f"💥 Damage: {ooc_eff['damage']} {ooc_eff.get('damage_type', '')}".strip())
                if ooc_eff.get("auto_hit"):
                    ooc_details.append("✨ Auto-hit")
                ooc_dt_line = " | ".join(ooc_details)
                st.caption(f"{ooc_dt_line} — *{ooc_desc}*" if ooc_dt_line else f"*{ooc_desc}*")

                ooc_targets = {f"You ({player.get('name')})": player}
                for comp in world.get("party", {}).get("companions", []):
                    ooc_targets[f"🤝 {comp.get('name')}"] = comp
                chosen_ooc_tgt_lbl = st.selectbox("Target:", list(ooc_targets.keys()), key="ooc_tgt_sel")
                chosen_ooc_tgt = ooc_targets[chosen_ooc_tgt_lbl]

                if st.button("Cast", key="ooc_cast_btn", use_container_width=True):
                    c_res = state_manager.cast_spell(
                        caster=player,
                        target=chosen_ooc_tgt,
                        spell_id=chosen_ooc_id,
                        character_state=player,
                        world_state=world,
                    )
                    if not c_res.get("success"):
                        st.error(c_res.get("reason", "Failed to cast."))
                    else:
                        h_val = c_res.get("healed")
                        if h_val:
                            st.toast(f"Cast {ooc_data.get('name')}! Restored {h_val} HP.")
                            st.session_state["narrative_log"].append({
                                "role": "assistant",
                                "content": f"✨ You cast **{ooc_data.get('name')}** on **{chosen_ooc_tgt.get('name')}**, restoring {h_val} HP!"
                            })
                        else:
                            st.toast(f"Cast {ooc_data.get('name')}!")
                            st.session_state["narrative_log"].append({
                                "role": "assistant",
                                "content": f"✨ You cast **{ooc_data.get('name')}**!"
                            })
                        auto_save()
                        st.rerun()


def _render_environment_tab(player: Dict[str, Any], world: Dict[str, Any], current_room: Dict[str, Any]):
    """Render Tab 2: Room Loot, Hidden Caches, and Interactive Objects."""
    room_id = current_room.get("id")
    has_loot = bool(current_room.get("loot")) and (room_id not in world.get("collected_loot", []))
    if has_loot:
        if st.button("💎 Search / Loot Room", use_container_width=True, type="secondary"):
            looted = dungeon_manager.get_room_loot(room_id, world)
            if looted:
                import copy
                item_cat = state_manager._get_item_catalog()
                gen_items = world.get("generated_items", {})
                looted_names = []
                for lit in looted:
                    li_id = lit.get("item_id")
                    li_qty = lit.get("quantity", 1)
                    if li_id == "gold":
                        state_manager.add_currency(player, gp=li_qty)
                        looted_names.append(f"{li_qty} GP")
                        continue
                    item_def = item_cat.get(li_id) or gen_items.get(li_id) or {}
                    li_name = item_def.get("name", li_id)
                    looted_names.append(f"{li_name} (x{li_qty})")
                    pinv = player.setdefault("inventory", [])
                    existing = next((i for i in pinv if isinstance(i, dict) and i.get("item_id") == li_id), None)
                    if existing and existing.get("quantity") is not None:
                        existing["quantity"] = existing.get("quantity", 1) + li_qty
                    elif not existing:
                        new_entry = {"item_id": li_id, "equipped": False, "quantity": li_qty}
                        if li_id in gen_items:
                            new_entry["definition"] = copy.deepcopy(gen_items[li_id])
                        pinv.append(new_entry)
                msg_loot = f"💎 You searched the room and found: **{', '.join(looted_names)}**!"
                st.session_state["narrative_log"].append({"role": "assistant", "content": msg_loot})
                auto_save()
                st.rerun()

    has_hidden_cache = bool(current_room.get("hidden_cache")) and (f"{room_id}_hidden_cache" not in world.get("collected_loot", []))
    if has_hidden_cache:
        if st.button("🔍 Search Hidden Cache (Rumor)", use_container_width=True, type="secondary"):
            looted = dungeon_manager.get_room_loot(room_id, world)
            if looted:
                import copy
                item_cat = state_manager._get_item_catalog()
                gen_items = world.get("generated_items", {})
                looted_names = []
                for lit in looted:
                    li_id = lit.get("item_id")
                    li_qty = lit.get("quantity", 1)
                    if li_id == "gold":
                        state_manager.add_currency(player, gp=li_qty)
                        looted_names.append(f"{li_qty} GP")
                        continue
                    item_def = item_cat.get(li_id) or gen_items.get(li_id) or {}
                    li_name = item_def.get("name", li_id)
                    looted_names.append(f"{li_name} (x{li_qty})")
                    pinv = player.setdefault("inventory", [])
                    existing = next((i for i in pinv if isinstance(i, dict) and i.get("item_id") == li_id), None)
                    if existing and existing.get("quantity") is not None:
                        existing["quantity"] = existing.get("quantity", 1) + li_qty
                    elif not existing:
                        new_entry = {"item_id": li_id, "equipped": False, "quantity": li_qty}
                        if li_id in gen_items:
                            new_entry["definition"] = copy.deepcopy(gen_items[li_id])
                        pinv.append(new_entry)
                msg_loot = f"🔍 You investigated the rumors and uncovered a secret cache: **{', '.join(looted_names)}**!"
                st.session_state["narrative_log"].append({"role": "assistant", "content": msg_loot})
                auto_save()
                st.rerun()

    interactive_objs = current_room.get("interactive_objects", []) if current_room else []
    unused_objs = [obj for obj in interactive_objs if not obj.get("used")]
    if unused_objs:
        st.markdown("##### 🎯 Environmental Objects")
        for obj in unused_objs:
            obj_id = obj.get("id")
            obj_name = obj.get("name", obj_id)
            obj_desc = obj.get("description", "")
            col_obj_txt, col_obj_act = st.columns([3, 1])
            with col_obj_txt:
                st.caption(f"**{obj_name}**: {obj_desc}")
            with col_obj_act:
                if st.button(f"🎯 ใช้งาน {obj_name}", key=f"exp_obj_{obj_id}", use_container_width=True):
                    res_obj = dungeon_manager.interact_with_object(world, room_id, obj_id, character_state=player)
                    if res_obj.get("success"):
                        st.toast(f"ใช้งาน {obj_name} สำเร็จ!")
                        st.session_state["narrative_log"].append({
                            "role": "assistant",
                            "content": f"🎯 **Interactive Object:** {res_obj['message']}"
                        })
                        auto_save()
                        st.rerun()

    if not has_loot and not has_hidden_cache and not unused_objs:
        st.caption("🔍 ไม่มีวัตถุหรือหีบสมบัติพิเศษในบริเวณนี้")


def render_playing_view():
    """Main gameplay view: Top HUD bar, Narrative Log, and Combat or Exploration panels."""
    player = st.session_state.get("player_state")
    world = st.session_state.get("world_state")
    if not player or not world:
        return

    render_sidebar()

    current_room = dungeon_manager.get_current_room(world)
    if not current_room:
        st.error("Current location is invalid or missing.")
        return

    # ── 1 in 65 Million × Holy Grail War Top Status Banner ────────────────────
    render_top_status_banner(player, world, current_room)

    # ── Top RPG Quick-Access HUD Bar ──────────────────────────────────────────
    inv_count = len(player.get("inventory", []))
    active_q_count = get_active_quest_count(world)
    spell_cnt = get_known_spell_count(player)

    hud_c1, hud_c2, hud_c3, hud_c4, hud_c5 = st.columns(5)
    with hud_c1:
        if st.button("👤 ตัวละคร", use_container_width=True, key="hud_char", help="เปิดหน้าต่างข้อมูลตัวละคร (Character Sheet)"):
            show_character_dialog()
    with hud_c2:
        if st.button(f"🎒 สัมภาระ ({inv_count})", use_container_width=True, key="hud_inv", help="เปิดกระเป๋าสัมภาระ (Inventory)"):
            show_inventory_dialog()
    with hud_c3:
        if st.button(f"📜 ภารกิจ ({active_q_count})", use_container_width=True, key="hud_quests", help="เปิดสมุดบันทึกภารกิจ (Quests)"):
            show_quests_dialog()
    with hud_c4:
        if st.button(f"✨ เวทมนตร์ ({spell_cnt})", use_container_width=True, key="hud_spells", help="เปิดคัมภีร์เวทมนตร์ (Spells)"):
            show_spells_dialog()
    with hud_c5:
        camp_label = "🏕️ ในแคมป์" if world.get("at_camp") else "⛺ พักผ่อน"
        if st.button(camp_label, use_container_width=True, key="hud_camp", help="ตั้งแคมป์ & พักผ่อน (Rest & Camp)"):
            show_camp_dialog()

    # ── Gold-Framed Scene Stage Banner ────────────────────────────────────────
    render_scene_stage_banner(current_room, world)

    # Status message if any
    if st.session_state.get("last_action_msg"):
        st.toast(st.session_state["last_action_msg"])
        st.session_state["last_action_msg"] = None

    # Auto-enhance initial prologue if needed for current session
    log = st.session_state.get("narrative_log", [])
    if len(log) == 1 and "บทนำ: ก้าวแรกสู่ริเวอร์ไซด์" in log[0].get("content", "") and "โรงเตี๊ยมมังกรเมา" not in log[0].get("content", ""):
        log[0]["content"] += (
            "\n\nเบื้องหน้าของเจ้าคือจัตุรัสกลางหมู่บ้านที่เต็มไปด้วยชีวิตชีวา:\n"
            "• 🍻 **โรงเตี๊ยมมังกรเมา (The Drunken Dragon Tavern):** แหล่งรวมข่าวลือและ **กระดานประกาศภารกิจ (Notice Board)**\n"
            "• 🏪 **ตลาดและร้านค้า (Town Shops):** ร้านค้าทั่วไป โรงตีเหล็ก และร้านปรุงยา สำหรับเตรียมเสบียงและอาวุธ\n"
            "• 🌲 **เส้นทางออกนอกเมือง:** มุ่งหน้าสู่ป่าธอร์นวูดทางทิศเหนือ ซึ่งมีข่าวลือเรื่องก็อบลินและดันเจียนลึกลับ\n\n"
            "**เจ้าต้องการมุ่งหน้าไปสำรวจที่ไหนก่อนดี?** *(สามารถคลิกปุ่มตัวเลือกแนะนำด้านล่าง หรือพิมพ์คำสั่งสิ่งที่อยากทำได้เลย)*"
        )

    # ── Featured Current Event Card (Holy Grail War style) ────────────────────
    char_display_name = player.get("name", "Player") if player else "Player"
    total_msgs = len(log)
    latest_assistant_entry = next((e for e in reversed(log) if e.get("role") == "assistant"), None)
    if latest_assistant_entry:
        gt = world.get("game_time", {})
        ev_deltas = latest_assistant_entry.get("deltas") or st.session_state.get("last_turn_deltas") or []
        delta_pills_html = ""
        if ev_deltas:
            pills = []
            for d_item in ev_deltas:
                k_cls = "hgw-delta-pos" if d_item.get("kind") == "pos" else ("hgw-delta-neg" if d_item.get("kind") == "neg" else "hgw-delta-neu")
                pills.append(f'<span class="hgw-delta-pill {k_cls}">{d_item.get("label", "")}</span>')
            delta_pills_html = f'<div class="hgw-delta-row">{"".join(pills)}</div>'

        st.markdown(
            f"""
            <div class="hgw-featured-event">
                <div class="hgw-event-eyebrow">
                    <span>◆ เหตุการณ์ปัจจุบัน &bull; {current_room.get('name', 'Scene')}</span>
                    <span>Day {gt.get('day', 1)} &bull; {str(gt.get('period', 'morning')).capitalize()}</span>
                </div>
                {delta_pills_html}
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Story & Narrative Log Header
    col_nh, col_nc = st.columns([3, 1])
    with col_nh:
        st.markdown(f"### 📜 บันทึกการผจญภัย (Adventure Chronicle) &bull; <span style='font-size: 1.05rem; color: #f3d08a;'>{char_display_name}</span>", unsafe_allow_html=True)
    with col_nc:
        st.caption(f"💬 {total_msgs} เหตุการณ์ในบันทึก")

    log_container = st.container(height=500)
    with log_container:
        for entry in st.session_state["narrative_log"]:
            role = entry.get("role", "assistant")
            if role == "user":
                with st.chat_message("user", avatar="🧑‍💼"):
                    st.caption(f"**{char_display_name}**")
                    st.markdown(entry.get("content", ""))
            else:
                with st.chat_message("assistant", avatar="🎲"):
                    st.caption("**Dungeon Master AI**")
                    st.markdown(entry.get("content", ""))
                    entry_deltas = entry.get("deltas")
                    if entry_deltas:
                        pills_md = " ".join(f"`{d.get('label')}`" for d in entry_deltas if d.get("label"))
                        if pills_md:
                            st.caption(f"◆ ผลลัพธ์: {pills_md}")

    combat_active = world.get("combat_state") is not None

    if combat_active:
        render_combat_panel(player, world, current_room)
    else:
        st.markdown("---")
        col_nav, col_act = st.columns([1, 2])

        with col_nav:
            _render_navigation_column(player, world)

        with col_act:
            st.markdown("##### 🎭 Actions & Interaction")

            # Inspiration Reroll Prompt (BG3 style reroll on failed check)
            pending_reroll = st.session_state.get("pending_inspiration_reroll")
            if pending_reroll and state_manager.get_inspiration(player) > 0:
                st.warning(
                    f"🎲 **ผลการทอยล่าสุดล้มเหลว ({pending_reroll['stat']} DC {pending_reroll['dc']})**: "
                    f"ทอยได้ {pending_reroll['original_roll']} + mod {pending_reroll['modifier']} = **{pending_reroll['original_total']}** ❌ "
                    f"(คุณมี **{state_manager.get_inspiration(player)} Inspiration**)"
                )
                col_reroll_btn, col_reroll_skip = st.columns([2, 1])
                with col_reroll_btn:
                    if st.button("✨ ใช้ 1 Inspiration เพื่อทอยใหม่ (Reroll)", type="primary", use_container_width=True, key="btn_insp_reroll"):
                        if state_manager.spend_inspiration(player):
                            reroll_res = resolve_inspiration_reroll(pending_reroll, player, world)
                            reroll_die = reroll_res["reroll_die"]
                            final_die = reroll_res["final_die"]
                            new_total = reroll_res["new_total"]
                            new_succ = reroll_res["success"]
                            res_str = "SUCCESS ✅" if new_succ else "FAILURE ❌"
                            st.session_state["narrative_log"].append({
                                "role": "assistant",
                                "content": (
                                    f"✨ **Inspiration Reroll ({pending_reroll['stat']} DC {pending_reroll['dc']})**: "
                                    f"ทอยใหม่ได้ {reroll_die} (เดิม {pending_reroll['original_roll']} → เลือก {final_die}) "
                                    f"+ mod {pending_reroll['modifier']} = **{new_total}** → **{res_str}**"
                                )
                            })
                            if reroll_res["escaped"]:
                                st.session_state["narrative_log"].append({
                                    "role": "assistant",
                                    "content": f"🔓 **Escape Successful!** You used Inspiration to break free from your bonds and fled to **{reroll_res['safe_room']}**."
                                })
                            del st.session_state["pending_inspiration_reroll"]
                            auto_save()
                            st.rerun()
                with col_reroll_skip:
                    if st.button("ข้ามการทอยใหม่", use_container_width=True, key="btn_skip_reroll"):
                        del st.session_state["pending_inspiration_reroll"]
                        st.rerun()

            tab_act, tab_env, tab_town = st.tabs(["⚔️ แอ็กชัน & พูดคุย", "🔍 สำรวจฉาก & วัตถุ", "🏛️ สถานที่ & ร้านค้า"])

            with tab_act:
                _render_actions_tab(player, world, current_room)

            with tab_env:
                _render_environment_tab(player, world, current_room)

            with tab_town:
                render_town_tab(player, world, current_room)
