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


def resolve_exploration_parley(
    player: Dict[str, Any],
    world: Dict[str, Any],
    current_room: Dict[str, Any],
    skill: str = "Persuasion",
    speech_text: str = "",
    use_guidance: bool = False,
    use_social_advantage: bool = False,
    use_inspiration: bool = False,
) -> Dict[str, Any]:
    """
    Tabletop D&D 5e / BG3 Pre-Combat Pacifist Encounter Resolution:
    Allows players who prefer talking over fighting to negotiate, bluff, intimidate, or use Animal Handling
    with hostile creatures in an uncleared room BEFORE combat starts!
    On success:
      - Marks the room as cleared
      - Awards full Milestone Social XP and gold tribute from the encounter monsters
      - Checks level-up
      - Converts the lead monster into a friendly pacified NPC in the room (with a secret!)
    """
    import copy
    room_id = current_room.get("id", world.get("current_location", "unknown"))
    enc_table = list(current_room.get("encounter_table", []))
    if not enc_table:
        return {"success": False, "parley_success": False, "reason": "No hostile encounter present in this room."}

    skill_norm = (skill or "Persuasion").strip()
    stat = "WIS" if skill_norm.lower() in ("animal handling", "animal_handling", "insight") else "CHA"
    is_prof = state_manager.is_proficient(skill_norm, player)
    bonus_dice = "1d4" if use_guidance else None

    chk_res = state_manager.resolve_check(
        stat=stat,
        difficulty="medium",
        state=player,
        advantage=use_social_advantage,
        proficient=is_prof,
        bonus_dice=bonus_dice,
        skill=skill_norm,
        use_inspiration=use_inspiration,
    )

    mon_cat = combat_manager._get_monster_catalog()
    gen_mons = world.get("generated_monsters", {})
    lead_id = enc_table[0]
    lead_def = mon_cat.get(lead_id) or gen_mons.get(lead_id) or {"name": lead_id.replace("_", " ").title(), "xp_value": 50}
    lead_name = lead_def.get("name", lead_id.replace("_", " ").title())

    if not chk_res["success"]:
        return {
            "success": True,
            "parley_success": False,
            "check_result": chk_res,
            "target_name": lead_name,
            "xp_gained": 0,
            "gold_gained": 0,
            "pacified_npc_id": None,
            "message": (
                f"❌ การเจรจากับ **{lead_name}** ไม่สำเร็จ "
                f"(ทอย {stat} [{skill_norm}] ได้ **{chk_res['total']}** vs **DC {chk_res['dc']}**)"
            ),
        }

    # Social Victory! Award full encounter XP + gold tribute & clear room
    total_xp = 0
    total_gold = 0
    for mid in enc_table:
        mdef = mon_cat.get(mid) or gen_mons.get(mid) or {}
        total_xp += int(mdef.get("xp_value", 50))
        g_drop = mdef.get("gold_drop")
        if isinstance(g_drop, dict):
            total_gold += max(5, int(g_drop.get("max", 10)))
        else:
            total_gold += 10

    state_manager.award_xp(total_xp, player)
    state_manager.add_currency(player, gp=total_gold)
    leveled_up = False
    while state_manager.check_level_up(player):
        state_manager.apply_level_up(player)
        leveled_up = True

    dungeon_manager.mark_room_cleared(room_id, world)

    # Convert lead monster into a friendly talkable/recruitable NPC in this room
    npc_slug = f"pacified_{lead_id}"
    gen_npcs = world.setdefault("generated_npcs", {})
    if npc_slug not in gen_npcs:
        hp_val = lead_def.get("hp", {}).get("max", 20) if isinstance(lead_def.get("hp"), dict) else int(lead_def.get("hp", 20))
        gen_npcs[npc_slug] = {
            "id": npc_slug,
            "name": f"{lead_name} (พันธมิตรที่เจรจาสำเร็จ)",
            "role": "Pacified Ally",
            "persona_seed": f"A former guardian of {current_room.get('name', room_id)} who was won over by {player.get('name', 'the hero')}'s words.",
            "secret": f"Knows the hidden passages and secret caches inside {current_room.get('name', room_id)}.",
            "hp": {"current": hp_val, "max": hp_val},
            "ac": lead_def.get("ac", 13),
            "stats": lead_def.get("stats", {"STR": 12, "DEX": 12, "CON": 12, "INT": 10, "WIS": 12, "CHA": 10}),
            "attacks": lead_def.get("attacks", [{"name": "Strike", "attack_bonus": 4, "damage": "1d6+2", "damage_type": "slashing"}]),
            "approval": 65,
            "side": "player",
        }

    dyn_rooms = world.setdefault("dynamic_rooms", {})
    if room_id not in dyn_rooms:
        dyn_rooms[room_id] = copy.deepcopy(current_room)
    r_npcs = dyn_rooms[room_id].setdefault("npcs", [])
    if npc_slug not in r_npcs:
        r_npcs.append(npc_slug)
    if npc_slug not in current_room.setdefault("npcs", []):
        current_room["npcs"].append(npc_slug)

    world.setdefault("npc_relationships", {})[npc_slug] = 20

    # Track Pacifist / Peacemaker Reputation & Notice Board Pacifist Bounty completion
    world["pacified_count"] = int(world.get("pacified_count", 0)) + 1
    player["pacified_count"] = world["pacified_count"]
    peacemaker_unlocked = False
    if world["pacified_count"] >= 2:
        player["peacemaker_active"] = True
        world["peacemaker_title"] = "🕊️ ผู้สร้างสันติลิ้นทองคำ (Silver-Tongued Peacemaker)"
        peacemaker_unlocked = True

    for nb_e in world.get("notice_board", {}).get("entries", []):
        if isinstance(nb_e, dict) and nb_e.get("type") == "bounty":
            if nb_e.get("target_room_id") == room_id or nb_e.get("target_monster") in enc_table:
                nb_e["pacified_completed"] = True

    speech_note = f' ด้วยคำพูด *"{speech_text}"*' if speech_text else ""
    lvl_note = " 🆙 **(Level Up!)**" if leveled_up else ""
    pm_note = " 🕊️ *(ปลดล็อกฉายา: ผู้สร้างสันติลิ้นทองคำ +2 Persuasion!)*" if peacemaker_unlocked else ""
    return {
        "success": True,
        "parley_success": True,
        "check_result": chk_res,
        "target_name": lead_name,
        "xp_gained": total_xp,
        "gold_gained": total_gold,
        "leveled_up": leveled_up,
        "pacified_npc_id": npc_slug,
        "peacemaker_active": bool(player.get("peacemaker_active")),
        "message": (
            f"🕊️ **ชนะด้วยการเจรจา (Social Victory)!** คุณใช้ **{skill_norm}** กล่อม **{lead_name}**{speech_note} สำเร็จ "
            f"(ทอยได้ **{chk_res['total']}** vs **DC {chk_res['dc']}**)! "
            f"ได้รับ **+{total_xp} XP**, ของกำนัล **+{total_gold} GP**{lvl_note}{pm_note} และ **{lead_name}** ยอมเป็นพันธมิตรในพื้นที่นี้โดยไม่ต้องต่อสู้!"
        ),
    }


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
    Render the sleek CRPG Top HUD Bar with character identity, inline vitals, location, and time.
    Zero 4-space markdown indentation so no HTML ever leaks as a code block.
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
        "morning": "☀️ เช้า (Morning)",
        "afternoon": "🌤️ บ่าย (Afternoon)",
        "evening": "🌅 พลบค่ำ (Evening)",
        "night": "🌙 กลางคืน (Night)",
    }
    p_label = period_icons.get(period, "☀️ เช้า")

    hp = player.get("hp", {"current": 10, "max": 10})
    hp_cur = max(0, hp.get("current", 10))
    hp_max = max(1, hp.get("max", 10))
    hp_temp = hp.get("temp", player.get("temp_hp", 0))
    hp_pct = min(100, int((hp_cur / hp_max) * 100))
    hp_color = "#4ade80" if hp_pct >= 60 else ("#fbbf24" if hp_pct >= 30 else "#f87171")
    temp_str = f"+{hp_temp}" if hp_temp and hp_temp > 0 else ""

    ac = player.get("ac", 10)
    xp_cur = player.get("xp_current", 0)
    thresholds = state_manager.LEVEL_THRESHOLDS
    next_xp = thresholds.get(min(5, lvl + 1), 6500)
    xp_text = f"{xp_cur} (MAX)" if lvl >= 5 else f"{xp_cur}/{next_xp}"

    insp_cur = state_manager.get_inspiration(player)
    insp_max = player.get("max_inspiration", state_manager.MAX_INSPIRATION)

    curr = get_player_currency(player)
    gp_val = curr.get("gp", 0)
    sp_val = curr.get("sp", 0)
    rations = get_ration_count(player)

    room_name = current_room.get("name", "Unknown")
    is_safe = current_room.get("is_safe", False)
    zone_badge = "🛡️ เขตปลอดภัย" if is_safe else "⚔️ เขตอันตราย"
    pm_badge = '<span class="hgw-badge-pill">🕊️ ผู้สร้างสันติ (+2 Persuasion)</span>' if player.get("peacemaker_active") else ""

    html = (
        f'<div class="crpg-hud-bar">'
        f'<div class="crpg-hud-top">'
        f'<div>'
        f'<span class="crpg-hero-name">◆ {name}</span>'
        f'<span class="crpg-hero-sub">Lv.{lvl} {race} {cls_name}{sub_display} &bull; {bg}</span>'
        f'</div>'
        f'<div class="crpg-vitals-strip">'
        f'<span class="crpg-vital-chip">❤️ <span style="color:{hp_color};">{hp_cur}/{hp_max}{temp_str}</span></span>'
        f'<span class="crpg-vital-chip">🛡️ <span style="color:#38bdf8;">{ac} AC</span></span>'
        f'<span class="crpg-vital-chip">✨ <span style="color:#c084fc;">{xp_text} XP</span></span>'
        f'<span class="crpg-vital-chip">🌟 <span style="color:#f3d08a;">{insp_cur}/{insp_max} Insp</span></span>'
        f'<span class="crpg-vital-chip">💰 <span style="color:#fbbf24;">{gp_val}G {sp_val}S</span> &bull; 🍖 {rations}</span>'
        f'{pm_badge}'
        f'<span class="hgw-badge-pill">📍 {room_name} ({zone_badge})</span>'
        f'<span class="hgw-badge-pill">🗓️ วันที่ {day} &bull; {p_label}</span>'
        f'</div>'
        f'</div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def render_scene_stage_banner(current_room: Dict[str, Any], world: Dict[str, Any]) -> None:
    """
    Render the compact gold-framed Scene Stage Card at the top of the Left Story Pane.
    Single-line / unindented HTML guarantees Markdown never interprets any tags as raw code blocks.
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
        svg_art = (
            '<svg viewBox="0 0 800 90" width="100%" height="100%" preserveAspectRatio="none">'
            '<circle cx="670" cy="26" r="14" fill="none" stroke="#d4af6a" stroke-width="1.5" opacity="0.85"/>'
            '<path d="M0,88 L0,65 L55,65 L55,44 L85,26 L115,44 L115,65 L170,65 L170,40 L205,18 L240,40 L240,65 L310,65 L310,52 L360,52 L360,34 L385,12 L410,34 L410,52 L470,52 L470,65 L560,65 L560,42 L595,24 L630,42 L630,65 L800,65 L800,90 Z" fill="#0b0d14" stroke="#d4af6a" stroke-width="1.2" opacity="0.9"/>'
            '<rect x="195" y="46" width="6" height="8" fill="#f3d08a" opacity="0.75"/>'
            '<rect x="208" y="46" width="6" height="8" fill="#f3d08a" opacity="0.75"/>'
            '<rect x="380" y="40" width="6" height="9" fill="#f3d08a" opacity="0.85"/>'
            '</svg>'
        )
        type_pill = "🏘️ เขตเมือง (Town)"
    elif room_type == "dungeon":
        svg_art = (
            '<svg viewBox="0 0 800 90" width="100%" height="100%" preserveAspectRatio="none">'
            '<path d="M180,90 L180,38 A65,36 0 0,1 310,38 L310,90" fill="#07080d" stroke="#d4af6a" stroke-width="1.4" opacity="0.85"/>'
            '<path d="M490,90 L490,38 A65,36 0 0,1 620,38 L620,90" fill="#07080d" stroke="#d4af6a" stroke-width="1.4" opacity="0.85"/>'
            '<circle cx="400" cy="36" r="18" fill="none" stroke="#d4af6a" stroke-width="1.5" opacity="0.75"/>'
            '<polygon points="400,22 405,36 400,50 395,36" fill="#f3d08a" opacity="0.8"/>'
            '</svg>'
        )
        type_pill = "🏰 ดันเจี้ยน (Dungeon)"
    else:
        svg_art = (
            '<svg viewBox="0 0 800 90" width="100%" height="100%" preserveAspectRatio="none">'
            '<circle cx="650" cy="24" r="13" fill="none" stroke="#f3d08a" stroke-width="1.5" opacity="0.85"/>'
            '<path d="M0,88 L90,44 L175,76 L290,28 L410,72 L530,32 L650,70 L750,40 L800,66 L800,90 L0,90 Z" fill="#0d111c" stroke="#d4af6a" stroke-width="1.1" opacity="0.75"/>'
            '</svg>'
        )
        type_pill = "🌲 ธรรมชาติ (Wilderness)"

    npcs = current_room.get("npcs", [])
    npc_str = f"🧑 ผู้คนในฉาก: {', '.join(n.replace('_', ' ').title() for n in npcs)}" if npcs else "🍃 ไร้ผู้คนในบริเวณใกล้เคียง"

    ev_deltas = st.session_state.get("last_turn_deltas") or []
    delta_pills_html = ""
    if ev_deltas:
        pills = []
        for d_item in ev_deltas:
            k_cls = "hgw-delta-pos" if d_item.get("kind") == "pos" else ("hgw-delta-neg" if d_item.get("kind") == "neg" else "hgw-delta-neu")
            pills.append(f'<span class="hgw-delta-pill {k_cls}">{d_item.get("label", "")}</span>')
        delta_pills_html = f'<div class="hgw-delta-row">{"".join(pills)}</div>'

    html = (
        f'<div class="crpg-scene-card">'
        f'<div class="crpg-scene-art" style="background: {bg_grad};">{svg_art}</div>'
        f'<div class="crpg-scene-content">'
        f'<div class="crpg-scene-title-row">'
        f'<h2 class="crpg-scene-title">◆ {room_name}</h2>'
        f'<div style="display:flex;gap:6px;flex-wrap:wrap;">'
        f'<span class="hgw-badge-pill">{type_pill}</span>'
        f'<span class="hgw-badge-pill">{npc_str}</span>'
        f'</div>'
        f'</div>'
        f'<p class="crpg-scene-desc">{room_desc}</p>'
        f'{delta_pills_html}'
        f'</div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def _process_player_turn_action(
    action_to_process: str,
    player: Dict[str, Any],
    world: Dict[str, Any],
    current_room: Dict[str, Any],
) -> None:
    """Process a chosen or typed player action (checks for BG3 skill roll first, otherwise runs DM turn)."""
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


def _render_navigation_column(player: Dict[str, Any], world: Dict[str, Any]):
    """Render Navigation & Movement: clear destination buttons for all 4 directions + World Atlas."""
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
            st.markdown("##### ⚖️ ถูกคุมขังในห้องขังเมือง (Town Jail)")
            days_left = player.get("prison_days_left", 1)
            st.warning(f"คุณถูกขังอยู่ในคุก! โทษที่เหลือ: **{days_left} วัน**")
            confiscated = player.get("confiscated_items", [])
            if confiscated:
                st.caption(f"🔒 สัมภาระ {len(confiscated)} ชิ้นถูกยึดไว้ในหีบของกลาง")

            col_srv, col_brk = st.columns(2)
            with col_srv:
                if st.button("⚖️ รับโทษตามกำหนด (Serve Time)", use_container_width=True, type="primary"):
                    res_s = state_manager.serve_prison_time(player, world)
                    st.toast("พ้นโทษแล้ว!")
                    st.session_state["narrative_log"].append({
                        "role": "assistant",
                        "content": f"⚖️ **Sentence Served:** {res_s['message']}"
                    })
                    auto_save()
                    st.rerun()
            with col_brk:
                if st.button("🔓 สะเดาะกุญแจแหกคุก (Lockpick)", use_container_width=True):
                    res_e = state_manager.attempt_lockpick_escape(player, world)
                    if res_e["success"]:
                        st.toast("แหกคุกสำเร็จ!")
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
            st.markdown("##### 🔒 ถูกจับตัวเป็นเชลย — พยายามหลบหนี")
            st.info("คุณถูกมัดตัวไว้! ต้องหลบหนีให้สำเร็จก่อนจึงจะเดินทางต่อได้")
            if st.button("🔓 พยายามดิ้นหลุดและหลบหนี (Attempt Escape)", use_container_width=True, type="primary"):
                esc_out = attempt_escape(world, player)
                esc_res = esc_out["res"]

                if esc_out["pending_reroll"]:
                    st.session_state["pending_inspiration_reroll"] = esc_out["pending_reroll"]
                else:
                    st.session_state.pop("pending_inspiration_reroll", None)

                if esc_out["success"]:
                    safe_room = esc_out["safe_room"]
                    st.success("🎉 หลบหนีสำเร็จ!")
                    st.session_state["narrative_log"].append({
                        "role": "assistant",
                        "content": f"🔓 **Escape Successful!** (Rolled {esc_res['total']} vs DC {esc_res['dc']}). You broke free from your bonds and fled to **{safe_room}**."
                    })
                else:
                    st.error("❌ หลบหนีไม่สำเร็จ!")
                    st.session_state["narrative_log"].append({
                        "role": "assistant",
                        "content": f"🔒 **Escape Failed!** (Rolled {esc_res['total']} vs DC {esc_res['dc']}). The guards catch you. Time passes..."
                    })
                auto_save()
                st.rerun()
        return

    exits = dungeon_manager.get_available_exits(world)
    dir_labels = [
        ("north", "⬆️ ทิศเหนือ (North)"),
        ("east", "➡️ ทิศตะวันออก (East)"),
        ("south", "⬇️ ทิศใต้ (South)"),
        ("west", "⬅️ ทิศตะวันตก (West)"),
    ]
    st.markdown("##### 🧭 เลือกเส้นทางออกเดินทาง (Direct Travel)")
    has_any_exit = False
    for d_key, d_title in dir_labels:
        dest_id = exits.get(d_key)
        if dest_id is not None:
            has_any_exit = True
            dest_room = dungeon_manager._get_room(dest_id, world) or {}
            dest_name = dest_room.get("name", dest_id)
            dest_type = dest_room.get("type", "wilderness").capitalize()
            is_clr = (dest_id in world.get("cleared_rooms", [])) or dest_room.get("is_safe")
            status_tag = "🛡️ ปลอดภัย" if is_clr else "⚔️ ยังไม่เคลียร์"
            btn_text = f"{d_title}  ➔  {dest_name} ({dest_type} • {status_tag})"
            if st.button(btn_text, key=f"move_{d_key}", use_container_width=True):
                ok_m, msg_m, new_room = dungeon_manager.move_player(d_key, world, player)
                if ok_m and new_room:
                    st.session_state.pop("pending_inspiration_reroll", None)
                    st.session_state.pop("active_visiting_shop", None)
                    st.session_state["last_action_msg"] = msg_m
                    st.session_state["narrative_log"].append({
                        "role": "user",
                        "content": f"ฉันออกเดินทางไปทางทิศ {d_key} มุ่งหน้าสู่ {new_room.get('name')}."
                    })
                    auto_save()
                    st.rerun()
    if not has_any_exit:
        st.caption("⛔ บริเวณนี้ไม่มีทางออกที่ชัดเจน")

    visited_ids = list(world.get("visited_rooms", []))
    cleared_ids = set(world.get("cleared_rooms", []))
    cur_id = world.get("current_location")
    if visited_ids:
        st.markdown("---")
        st.markdown(f"##### 🗺️ แผนที่โลกที่สำรวจแล้ว ({len(visited_ids)} พื้นที่)")
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


def _render_pending_checks_and_rerolls(player: Dict[str, Any], world: Dict[str, Any], current_room: Dict[str, Any]):
    """Render any pending BG3 Ability Check modal card or Heroic Inspiration Reroll banner at the top of the right cockpit."""
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

        insp_cnt = state_manager.get_inspiration(player)
        bond_info = player.get("party_bond_bonuses", {}) if isinstance(player.get("party_bond_bonuses"), dict) else {}
        auto_guid = bool(bond_info.get("auto_guidance"))

        with st.container(border=True):
            st.markdown(f"#### 🎲 D&D 5e / BG3 — ทอยเต๋าทดสอบทักษะ ({bg3_stat} • {bg3_skill})")
            st.markdown(f"🎯 **การกระทำ:** *\"{bg3_action}\"*")

            use_insp = st.checkbox(
                f"✨ ใช้ Inspiration ทอย 2 ลูก ({insp_cnt} แต้ม)",
                value=False,
                disabled=(insp_cnt <= 0),
                key="bg3_chk_use_insp",
            )
            use_guid = st.checkbox(
                "🙏 Guidance (+1d4 โบนัสเต๋านำทาง)",
                value=("guidance" in player.get("known_spells", []) or auto_guid),
                key="bg3_chk_use_guidance",
            )
            use_soc_adv = st.checkbox(
                "💖 Friends / Thaumaturgy (Advantage ทอย 2 ลูกเอาลูกสูง)",
                value=any(sp in player.get("known_spells", []) for sp in ("friends", "thaumaturgy", "charm_person", "detect_thoughts", "disguise_self")),
                key="bg3_chk_use_soc_adv",
            )

            pm_bonus = 2 if (bg3_spec.get("skill") == "persuasion" and player.get("peacemaker_active")) else 0
            bond_sk_bonus = int(bond_info.get("skill_bonuses", {}).get(str(bg3_spec.get("skill", "")), 0))
            eff_mod_preview = bg3_tot_mod + pm_bonus + bond_sk_bonus + (2 if use_guid else 0)
            bg3_chance_pct = calculate_check_success_chance(eff_mod_preview, bg3_dc, has_advantage=(use_insp or use_soc_adv))
            chance_color = "#4ade80" if bg3_chance_pct >= 65 else ("#fbbf24" if bg3_chance_pct >= 40 else "#f87171")

            card_html = (
                f'<div style="background:rgba(15,19,30,0.95);border:1px solid #d4af6a;border-radius:9px;padding:10px 14px;margin:8px 0;">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">'
                f'<span style="color:#38bdf8;font-weight:700;">{bg3_stat} ({bg3_skill}) — {bg3_thai}</span>'
                f'<span style="color:{chance_color};font-weight:700;font-size:1.05rem;">DC {bg3_dc} &bull; โอกาสสำเร็จ {bg3_chance_pct}%</span>'
                f'</div>'
                f'<div style="font-size:0.84rem;color:#cbd5e1;">'
                f'สแตท: <b>{bg3_mod:+d}</b> &bull; ความชำนาญ: <b>{f"{bg3_prof:+d}" if bg3_is_prof else "0"}</b>'
                + (f' &bull; สหายช่วย: <b>+{bond_sk_bonus}</b>' if bond_sk_bonus else '')
                + (f' &bull; ผู้สร้างสันติ: <b>+{pm_bonus}</b>' if pm_bonus else '')
                + (f' &bull; Guidance: <b>+1d4</b>' if use_guid else '')
                + f'</div></div>'
            )
            st.markdown(card_html, unsafe_allow_html=True)

            c_roll, c_cancel = st.columns([2.2, 1])
            with c_roll:
                roll_clicked = st.button("🎲 ทอยเต๋า d20 ทันที!", type="primary", use_container_width=True, key="btn_bg3_roll")
            with c_cancel:
                cancel_clicked = st.button("❌ ยกเลิก", use_container_width=True, key="btn_bg3_cancel")

            if cancel_clicked:
                st.session_state.pop("pending_bg3_check", None)
                st.rerun()

            if roll_clicked:
                before_snap = snapshot_player_world_state(player, world)
                chk_res = state_manager.resolve_check(
                    stat=bg3_stat,
                    difficulty=bg3_diff,
                    state=player,
                    advantage=use_soc_adv,
                    proficient=bg3_is_prof,
                    bonus_dice="1d4" if use_guid else None,
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

                if not is_succ and state_manager.get_inspiration(player) > 0:
                    st.session_state["pending_bg3_reroll"] = {
                        "action": bg3_action,
                        "spec": bg3_spec,
                        "prev_result": chk_res,
                    }
                else:
                    st.session_state.pop("pending_bg3_reroll", None)

                st.session_state.pop("pending_bg3_check", None)
                auto_save()
                st.rerun()

    # D&D 5.5e (2024) / BG3 Post-Roll Heroic Inspiration Reroll Banner
    pending_bg3_reroll = st.session_state.get("pending_bg3_reroll")
    if pending_bg3_reroll and state_manager.get_inspiration(player) > 0:
        pr_res = pending_bg3_reroll["prev_result"]
        pr_spec = pending_bg3_reroll["spec"]
        insp_rem = state_manager.get_inspiration(player)
        with st.container(border=True):
            st.warning(
                f"🔄 **D&D 5.5e / BG3 Heroic Inspiration Reroll:** การทอย **{pr_spec['stat']} ({pr_spec['skill_name']})** ล้มเหลว "
                f"(ได้ **{pr_res['total']}** vs **DC {pr_res['dc']}**) — มี **{insp_rem} Inspiration ✨** ต้องการทอยแก้ตัวหรือไม่?"
            )
            c_hr1, c_hr2 = st.columns([2, 1])
            with c_hr1:
                if st.button("✨ ใช้ 1 Inspiration ทอยแก้ตัว (Reroll)", key="btn_heroic_reroll_bg3", type="primary", use_container_width=True):
                    new_chk = state_manager.reroll_check(pr_res, player)
                    st.session_state.pop("pending_bg3_reroll", None)
                    r_badge = "✅ HEROIC REROLL SUCCESS!" if new_chk["success"] else "❌ REROLL STILL FAILED"
                    if new_chk["success"]:
                        st.balloons()
                    st.session_state["narrative_log"].append({
                        "role": "assistant",
                        "content": (
                            f"🔄 **[Heroic Inspiration Reroll] {pr_spec['stat']} ({pr_spec['skill_name']}) vs DC {new_chk['dc']}**\n\n"
                            f"ทอยใหม่ได้ `[{new_chk.get('raw_reroll', new_chk['roll'])}]` (เลือกค่าสูงสุด `[{new_chk['roll']}]`) → รวม **`{new_chk['total']}`** vs **DC `{new_chk['dc']}`** → **{r_badge}**"
                        ),
                    })
                    auto_save()
                    st.rerun()
            with c_hr2:
                if st.button("ยอมรับผลเดิม", key="btn_skip_heroic_reroll_bg3", use_container_width=True):
                    st.session_state.pop("pending_bg3_reroll", None)
                    st.rerun()

    # Legacy manual check / escape inspiration reroll
    pending_reroll = st.session_state.get("pending_inspiration_reroll")
    if pending_reroll and state_manager.get_inspiration(player) > 0:
        with st.container(border=True):
            st.warning(
                f"🎲 **ผลการทอยล่าสุดล้มเหลว ({pending_reroll['stat']} DC {pending_reroll['dc']})**: "
                f"ได้ **{pending_reroll['original_total']}** ❌ (มี **{state_manager.get_inspiration(player)} Inspiration**)"
            )
            col_reroll_btn, col_reroll_skip = st.columns([2, 1])
            with col_reroll_btn:
                if st.button("✨ ใช้ 1 Inspiration เพื่อทอยใหม่", type="primary", use_container_width=True, key="btn_insp_reroll"):
                    if state_manager.spend_inspiration(player):
                        reroll_res = resolve_inspiration_reroll(pending_reroll, player, world)
                        res_str = "SUCCESS ✅" if reroll_res["success"] else "FAILURE ❌"
                        st.session_state["narrative_log"].append({
                            "role": "assistant",
                            "content": (
                                f"✨ **Inspiration Reroll ({pending_reroll['stat']} DC {pending_reroll['dc']})**: "
                                f"ทอยใหม่ได้ {reroll_res['reroll_die']} → รวม **{reroll_res['new_total']}** → **{res_str}**"
                            )
                        })
                        if reroll_res["escaped"]:
                            st.session_state["narrative_log"].append({
                                "role": "assistant",
                                "content": f"🔓 **Escape Successful!** You broke free and fled to **{reroll_res['safe_room']}**."
                            })
                        del st.session_state["pending_inspiration_reroll"]
                        auto_save()
                        st.rerun()
            with col_reroll_skip:
                if st.button("ข้าม", use_container_width=True, key="btn_skip_reroll"):
                    del st.session_state["pending_inspiration_reroll"]
                    st.rerun()


def _render_actions_tab(player: Dict[str, Any], world: Dict[str, Any], current_room: Dict[str, Any]):
    """
    Render Tab 1 of the Right Cockpit:
    1. Hostile Encounter Pacifist Parley / Combat Card (if uncleared monsters are in the room)
    2. Full-Width Stacked BG3 Decision Choice Cards (100% readable, never truncated!)
    3. Always-Visible NPC Dialogue & Social Roleplay Studio
    """
    room_id = current_room.get("id")
    is_uncleared_hostile = (
        not current_room.get("is_safe", False)
        and bool(current_room.get("encounter_table"))
        and (room_id not in world.get("cleared_rooms", []))
    )

    if is_uncleared_hostile:
        enc_table = current_room.get("encounter_table", ["goblin_scout"])
        monster_id = enc_table[0] if enc_table else "goblin_scout"
        mon_name = monster_id.replace("_", " ").title()
        with st.container(border=True):
            st.markdown(f"##### ⚠️ เผชิญหน้าศัตรูในพื้นที่: **{mon_name}**")
            st.caption("คุณสามารถใช้วาทศิลป์เจรจาเลี่ยงศึก (ได้ XP เต็ม + เปลี่ยนศัตรูเป็น NPC พันธมิตร) หรือเปิดฉากต่อสู้ได้ทันที:")
            c_p1, c_p2 = st.columns(2)
            with c_p1:
                parley_skill = st.selectbox(
                    "ทักษะเจรจา:",
                    ["Persuasion (หว่านล้อม/ติดสินบน)", "Deception (หลอกล่อ)", "Intimidation (ข่มขู่)", "Animal Handling (ปลอบสัตว์)"],
                    key="exp_parley_skill",
                )
            with c_p2:
                parley_line = st.text_input("คำพูดเจรจา (ไม่บังคับ):", placeholder="เช่น 'เรามาตกลงกันอย่างสันติดีกว่า!'", key="exp_parley_line")
            c_pb1, c_pb2 = st.columns([1.6, 1])
            with c_pb1:
                if st.button("🕊️ เจรจาจบศึกด้วยการคุย (Social Parley)", use_container_width=True, type="primary", key="btn_exp_parley"):
                    sk_clean = parley_skill.split(" (")[0].strip()
                    before_snap = snapshot_player_world_state(player, world)
                    p_out = resolve_exploration_parley(
                        player,
                        world,
                        current_room,
                        skill=sk_clean,
                        speech_text=parley_line.strip(),
                        use_guidance=True,
                        use_social_advantage=any(sp in player.get("known_spells", []) for sp in ("friends", "thaumaturgy", "charm_person")),
                    )
                    turn_deltas = compute_turn_stat_deltas(before_snap, player, world)
                    st.session_state["last_turn_deltas"] = turn_deltas
                    st.session_state["narrative_log"].append({
                        "role": "assistant",
                        "content": p_out["message"],
                        "deltas": turn_deltas,
                    })
                    auto_save()
                    st.rerun()
            with c_pb2:
                if st.button("⚔️ เปิดฉากต่อสู้", use_container_width=True, key="btn_exp_start_combat"):
                    combat_manager.start_combat([monster_id], player, world)
                    auto_save()
                    st.session_state["narrative_log"].append({
                        "role": "assistant",
                        "content": f"⚔️ Combat initiated against **{mon_name}**!"
                    })
                    st.rerun()

    # ── Full-Width Stacked Decision Choice Cards (100% readable, zero truncation) ──
    suggestions = st.session_state.get("action_suggestions") or list(llm_handler.DEFAULT_ACTION_SUGGESTIONS)
    if suggestions == llm_handler.DEFAULT_ACTION_SUGGESTIONS:
        suggestions = [
            "[Persuasion] พูดคุยสอบถามข่าวลือและขอคำแนะนำจากชาวบ้านในจัตุรัส",
            "เดินไปที่โรงเตี๊ยมมังกรเมาเพื่อดูกระดานประกาศภารกิจ",
            "[Insight] สังเกตท่าทีของผู้คนและแวะดูสินค้าที่ร้านค้า",
        ]
        st.session_state["action_suggestions"] = list(suggestions)

    st.markdown("##### ◆ ทางเลือกการตัดสินใจ (คลิกเลือกได้เลย — แสดงทักษะ & โอกาสสำเร็จ)")
    clicked_suggestion = None
    for idx, sug_text in enumerate(suggestions[:3]):
        meta = build_choice_card_metadata(sug_text, player, index=idx + 1)
        header_html = (
            f'<div class="crpg-choice-header">'
            f'<span class="crpg-choice-cat">[{meta["index"]}] {meta["category_label"]} &bull; <span class="crpg-choice-meta">{meta["sub_caption"]}</span></span>'
            f'<span class="crpg-choice-odds" style="color:{meta["odds_color"]};background:{meta["odds_bg"]};border:1px solid {meta["odds_border"]};">{meta["odds_label"]}</span>'
            f'</div>'
        )
        st.markdown(header_html, unsafe_allow_html=True)
        if st.button(f"▶ {sug_text}", key=f"sug_btn_{idx}", use_container_width=True):
            clicked_suggestion = sug_text

    # ── Always-Visible Social Roleplay & NPC Dialogue Studio ──────────────────
    st.markdown("---")
    st.markdown("##### 🗣️ สตูดิโอสนทนา & สวมบทบาท (Social Roleplay & NPC Dialogue)")
    room_npcs = list(current_room.get("npcs", []))
    party_comps = [c.get("name", c.get("id")) for c in world.get("party", {}).get("companions", []) if isinstance(c, dict)]
    all_social_targets = list(dict.fromkeys([f"🧑 {n.replace('_', ' ').title()}" for n in room_npcs] + [f"🤝 {cn}" for cn in party_comps]))

    p_cls = player.get("class_name", player.get("class", "Fighter"))
    p_race = player.get("race", "Human")
    p_bg = player.get("background", "Folk Hero")

    col_soc_t, col_soc_a = st.columns(2)
    with col_soc_t:
        if all_social_targets:
            soc_target = st.selectbox("🎯 เลือกคู่สนทนาในฉาก:", all_social_targets + ["👥 ทุกคนในบริเวณนี้"], key="soc_target_sel")
            clean_sel = soc_target.replace("🧑 ", "").replace("🤝 ", "").replace("👥 ", "").strip()
            npc_key = clean_sel.lower().replace(" ", "_")
            npc_obj = state_manager.resolve_npc(npc_key, world) or state_manager.resolve_npc(clean_sel, world)
            if isinstance(npc_obj, dict):
                n_role = npc_obj.get("role", "NPC")
                n_id = npc_obj.get("id", npc_key)
                rel_score = world.get("npc_relationships", {}).get(n_id, world.get("npc_relationships", {}).get(clean_sel, 0))
                comp_app = world.get("companions_approval", {}).get(n_id, {}).get("approval")
                rel_badge = f"ความสนิท: {comp_app}/100" if comp_app is not None else f"ความสัมพันธ์: {rel_score:+d}"
                has_sec = bool(npc_obj.get("secret"))
                unlocked = (comp_app is not None and comp_app >= 65) or (rel_score >= 15)
                if has_sec:
                    sec_badge = f"🔓 ความลับ: *{npc_obj['secret']}*" if unlocked else "🔒 มีความลับซ่อนอยู่ (เพิ่มความสัมพันธ์ถึง +15 เพื่อเปิดเผย)"
                else:
                    sec_badge = "💬 พร้อมพูดคุยแลกเปลี่ยนข่าวสาร"
                st.caption(f"**{npc_obj.get('name', clean_sel)}** ({n_role}) &bull; `{rel_badge}`  \n{sec_badge}")
        else:
            soc_target = "ผู้คนในบริเวณนี้"
            st.caption("💬 พูดคุยกับผู้คนหรือสิ่งแวดล้อมในฉากได้อย่างอิสระ")

    with col_soc_a:
        soc_approach = st.selectbox(
            "🎭 ท่าที / ทักษะการพูดคุย (BG3 Tag):",
            [
                "[Persuasion] 🕊️ หว่านล้อม / เจรจาอย่างเป็นมิตร",
                "💬 พูดคุยถามข่าวสารทั่วไป (ไม่ต้องทอยเต๋า)",
                "[Insight] 👁️ จับโกหก / อ่านสีหน้าและเจตนาลึกๆ",
                "[Deception] 🃏 โกหก / ปั้นน้ำเป็นตัว / ตบตา",
                "[Intimidation] 👹 ข่มขู่ / กดดันด้วยบารมี",
                "[Detect Thoughts] 🧠 แอบอ่านความคิดและความลับในใจ",
                "[Speak with Animals] 🐾 พูดคุยสื่อสารกับสัตว์",
                "[Speak with Dead] 💀 ซักถามวิญญาณผู้ล่วงลับ",
                "[Disguise Self] 🎭 ปลอมตัวแฝงตัวเข้าสนทนา",
                f"[{p_cls}] 🌟 ใช้เอกลักษณ์คลาส {p_cls} ในการสนทนา",
                f"[{p_race}] 🧬 ใช้สายเลือดเผ่าพันธุ์ {p_race} ในการสนทนา",
                f"[{p_bg}] 📜 อ้างอิงภูมิหลัง ({p_bg})",
            ],
            key="soc_approach_sel",
        )

    soc_custom_line = st.text_input(
        "💬 คำพูดหรือประเด็นที่อยากคุย (เว้นว่างเพื่อให้ตั้งคำถามอัตโนมัติได้):",
        placeholder="เช่น 'เล่าความลับของหมู่บ้านนี้ให้ฟังหน่อย' หรือ 'มีงานอะไรให้ฉันช่วยไหม?'",
        key="soc_custom_line_in",
    )
    clicked_social_action = None
    if st.button("🗣️ เอ่ยคำพูด / เริ่มบทสนทนาทันที", type="primary", use_container_width=True, key="btn_soc_speak"):
        clean_tgt = soc_target.replace("🧑 ", "").replace("🤝 ", "").replace("👥 ", "")
        prefix_tag = ""
        if soc_approach.startswith("["):
            prefix_tag = soc_approach.split("]")[0] + "] "
        if soc_custom_line.strip():
            clicked_social_action = f'{prefix_tag}พูดคุยกับ {clean_tgt}: "{soc_custom_line.strip()}"'
        else:
            approach_desc = soc_approach.split("]")[-1].strip()
            clicked_social_action = f"{prefix_tag}{approach_desc} กับ {clean_tgt}"

    if clicked_suggestion:
        _process_player_turn_action(clicked_suggestion.strip(), player, world, current_room)
    elif clicked_social_action:
        _process_player_turn_action(clicked_social_action.strip(), player, world, current_room)


def _render_spells_and_tools_tab(player: Dict[str, Any], world: Dict[str, Any], current_room: Dict[str, Any]):
    """Render Tab 4: Out-of-Combat Spells, Pickpocketing, and Manual Ability Check."""
    known_spells = player.get("known_spells", [])
    if known_spells:
        st.markdown("##### ✨ ร่ายเวทมนตร์นอกฉากต่อสู้ (Social & Utility Spells)")
        sp_cat = get_display_spell_catalog(world)

        def _fmt_ooc_sp(sid):
            sinfo = sp_cat.get(sid, {})
            slvl = sinfo.get("level", 0)
            lvl_tag = "Cantrip (ไม่เสียสล็อต)" if slvl == 0 else f"Level {slvl}"
            return f"{sinfo.get('name', sid)} [{lvl_tag}]"

        ooc_map = {_fmt_ooc_sp(s): s for s in known_spells}
        c_sp1, c_sp2 = st.columns([2, 1])
        with c_sp1:
            chosen_ooc_lbl = st.selectbox("เลือกคาถา:", list(ooc_map.keys()), key="ooc_sp_sel")
            chosen_ooc_id = ooc_map[chosen_ooc_lbl]
            ooc_data = sp_cat.get(chosen_ooc_id, {})
            st.caption(f"*{ooc_data.get('description', '')}*")
        with c_sp2:
            ooc_targets = {f"ตัวเอง ({player.get('name')})": player}
            for comp in world.get("party", {}).get("companions", []):
                ooc_targets[f"🤝 {comp.get('name')}"] = comp
            chosen_ooc_tgt_lbl = st.selectbox("เป้าหมาย:", list(ooc_targets.keys()), key="ooc_tgt_sel")
            chosen_ooc_tgt = ooc_targets[chosen_ooc_tgt_lbl]

        if st.button("✨ ร่ายคาถานี้ (Cast Spell)", key="ooc_cast_btn", type="primary", use_container_width=True):
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
                    st.toast(f"ร่าย {ooc_data.get('name')}! ฟื้นฟู {h_val} HP")
                    st.session_state["narrative_log"].append({
                        "role": "assistant",
                        "content": f"✨ คุณร่าย **{ooc_data.get('name')}** ใส่ **{chosen_ooc_tgt.get('name')}** ฟื้นฟูพลังชีวิต **{h_val} HP**!"
                    })
                else:
                    st.toast(f"ร่าย {ooc_data.get('name')} สำเร็จ!")
                    st.session_state["narrative_log"].append({
                        "role": "assistant",
                        "content": f"✨ คุณร่ายคาถา **{ooc_data.get('name')}** ({ooc_data.get('description', '')})!"
                    })
                auto_save()
                st.rerun()
        st.markdown("---")

    room_npcs = current_room.get("npcs", [])
    if room_npcs:
        st.markdown("##### 🦹 ล้วงกระเป๋า NPC (Pickpocket — Sleight of Hand)")
        c_pp1, c_pp2, c_pp3 = st.columns([1.3, 1.3, 1])
        with c_pp1:
            target_npc = st.selectbox("เป้าหมาย NPC:", room_npcs, key="pp_target_npc")
        with c_pp2:
            st_item = st.selectbox("สิ่งที่จะขโมย:", ["gold", "healing_potion", "torch", "rope", "dagger"], key="pp_item")
        with c_pp3:
            st.markdown("<div style='padding-top:26px;'></div>", unsafe_allow_html=True)
            if st.button("🧤 ล้วงกระเป๋า", use_container_width=True, key="btn_pp_steal"):
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
        st.markdown("---")

    st.markdown("##### 🎲 ทอยเต๋าทดสอบค่าสถานะอิสระ (Manual Ability Check)")
    c_mc1, c_mc2, c_mc3 = st.columns(3)
    with c_mc1:
        chk_stat = st.selectbox("ค่าสถานะ:", ["CHA", "WIS", "INT", "DEX", "STR", "CON"], key="mc_stat")
    with c_mc2:
        chk_diff = st.selectbox("ความยาก:", ["easy", "medium", "hard", "very_hard"], index=1, key="mc_diff")
    with c_mc3:
        chk_skill = st.text_input("ทักษะ (เช่น Persuasion):", "", key="mc_skill")
    if st.button("🎲 ทอย d20 (Roll Check)", use_container_width=True, key="btn_mc_roll"):
        chk_res = state_manager.resolve_check(
            chk_stat,
            chk_diff,
            player,
            skill=chk_skill.strip() if chk_skill else None,
        )
        succ_str = "SUCCESS ✅" if chk_res["success"] else "FAILURE ❌"
        skill_tag = f" [{chk_res['skill']}]" if chk_res.get("skill") else ""
        roll_msg = (
            f"🎲 **{chk_stat}{skill_tag} Check ({chk_diff.capitalize()} DC {chk_res['dc']})**: "
            f"Rolled {chk_res['roll']} + mod {chk_res['modifier']} = **{chk_res['total']}** → **{succ_str}**"
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
        auto_save()
        st.rerun()


def _render_environment_tab(player: Dict[str, Any], world: Dict[str, Any], current_room: Dict[str, Any]):
    """Render Room Loot, Hidden Caches, and Interactive Objects."""
    room_id = current_room.get("id")
    has_loot = bool(current_room.get("loot")) and (room_id not in world.get("collected_loot", []))
    if has_loot:
        if st.button("💎 ค้นหาสมบัติในพื้นที่ (Search / Loot Room)", use_container_width=True, type="primary"):
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
                msg_loot = f"💎 คุณสำรวจพื้นที่และพบ: **{', '.join(looted_names)}**!"
                st.session_state["narrative_log"].append({"role": "assistant", "content": msg_loot})
                auto_save()
                st.rerun()

    has_hidden_cache = bool(current_room.get("hidden_cache")) and (f"{room_id}_hidden_cache" not in world.get("collected_loot", []))
    if has_hidden_cache:
        if st.button("🔍 ค้นหาที่ซ่อนสมบัติลับตามข่าวลือ (Hidden Cache)", use_container_width=True, type="primary"):
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
                msg_loot = f"🔍 คุณค้นพบที่ซ่อนสมบัติลับตามข่าวลือ: **{', '.join(looted_names)}**!"
                st.session_state["narrative_log"].append({"role": "assistant", "content": msg_loot})
                auto_save()
                st.rerun()

    interactive_objs = current_room.get("interactive_objects", []) if current_room else []
    unused_objs = [obj for obj in interactive_objs if not obj.get("used")]
    if unused_objs:
        st.markdown("##### 🎯 วัตถุและจุดน่าสนใจในฉาก (Interactive Objects)")
        for obj in unused_objs:
            obj_id = obj.get("id")
            obj_name = obj.get("name", obj_id)
            obj_desc = obj.get("description", "")
            col_obj_txt, col_obj_act = st.columns([2.5, 1])
            with col_obj_txt:
                st.caption(f"**{obj_name}**: {obj_desc}")
            with col_obj_act:
                if st.button(f"🎯 ตรวจสอบ", key=f"exp_obj_{obj_id}", use_container_width=True):
                    res_obj = dungeon_manager.interact_with_object(world, room_id, obj_id, character_state=player)
                    if res_obj.get("success"):
                        st.toast(f"ตรวจสอบ {obj_name} สำเร็จ!")
                        st.session_state["narrative_log"].append({
                            "role": "assistant",
                            "content": f"🎯 **Interactive Object:** {res_obj['message']}"
                        })
                        auto_save()
                        st.rerun()


def render_playing_view():
    """
    Main gameplay view — Modern Split-Screen CRPG Cockpit (Baldur's Gate 3 × Disco Elysium):
    - Top Row: Sleek CRPG Status Bar + 5 Quick-Access Sheet Buttons
    - Left Pane (Story & Scene Stage): Visual Scene Card, Scrollable Adventure Chronicle, & Pinned Freeform Input
    - Right Pane (Interactive Roleplay & World Cockpit):
        * Tab 1: 🗣️ ทางเลือก & สนทนา (Full-width Choice Cards, Pacifist Parley, & NPC Dialogue Studio)
        * Tab 2: 🧭 เดินทาง & สำรวจ (Direct Destination Travel, Loot, Objects & World Map)
        * Tab 3: 🏪 ร้านค้า & กระดานข่าว (Town Shops, Social Bartering/Haggling & Notice Board Quests)
        * Tab 4: 🪄 คาถา & ทอยเต๋า (Out-of-Combat Social Spells, Pickpocket & Manual Check)
    """
    player = st.session_state.get("player_state")
    world = st.session_state.get("world_state")
    if not player or not world:
        return

    render_sidebar()

    current_room = dungeon_manager.get_current_room(world)
    if not current_room:
        st.error("Current location is invalid or missing.")
        return

    # ── 1. Sleek Top CRPG HUD Bar + 5 Quick-Sheet Modals ──────────────────────
    render_top_status_banner(player, world, current_room)

    inv_count = len(player.get("inventory", []))
    active_q_count = get_active_quest_count(world)
    spell_cnt = get_known_spell_count(player)
    comp_cnt = len(world.get("party", {}).get("companions", []))
    comp_tag = f" & สหาย ({comp_cnt})" if comp_cnt else ""

    hud_c1, hud_c2, hud_c3, hud_c4, hud_c5 = st.columns(5)
    with hud_c1:
        if st.button(f"👤 ตัวละคร{comp_tag}", use_container_width=True, key="hud_char"):
            show_character_dialog()
    with hud_c2:
        if st.button(f"🎒 สัมภาระ ({inv_count})", use_container_width=True, key="hud_inv"):
            show_inventory_dialog()
    with hud_c3:
        if st.button(f"📜 ภารกิจ ({active_q_count})", use_container_width=True, key="hud_quests"):
            show_quests_dialog()
    with hud_c4:
        if st.button(f"✨ เวทมนตร์ ({spell_cnt})", use_container_width=True, key="hud_spells"):
            show_spells_dialog()
    with hud_c5:
        camp_label = "🏕️ ในแคมป์ (พักผ่อน)" if world.get("at_camp") else "⛺ พักผ่อน / แคมป์"
        if st.button(camp_label, use_container_width=True, key="hud_camp"):
            show_camp_dialog()

    if st.session_state.get("last_action_msg"):
        st.toast(st.session_state["last_action_msg"])
        st.session_state["last_action_msg"] = None

    # Clean up legacy single-line bullet points in initial prologue if present
    log = st.session_state.get("narrative_log", [])
    if log and "โรงเตี๊ยมมังกรเมา" in log[0].get("content", "") and "• 🍻" in log[0].get("content", ""):
        log[0]["content"] = log[0]["content"].replace("• 🍻", "\n- 🍻").replace("• 🏪", "\n- 🏪").replace("• 🌲", "\n- 🌲")

    char_display_name = player.get("name", "Player") if player else "Player"

    # ── 2. Split-Screen CRPG Main Stage (Left: Story & Scene | Right: Cockpit) ─
    col_story, col_cockpit = st.columns([1.28, 1.0], gap="medium")

    with col_story:
        render_scene_stage_banner(current_room, world)

        log_container = st.container(height=450)
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

        # Pinned Freeform Roleplay & Action Input directly under the Story Chronicle
        with st.form("action_form", clear_on_submit=True):
            c_in, c_sub = st.columns([3.2, 1.1])
            with c_in:
                user_action = st.text_input(
                    "พิมพ์คำพูดหรือการกระทำของคุณ:",
                    placeholder="💬 พิมพ์พูดคุยอิสระ หรือสั่งการกระทำ เช่น 'ชวน Mira คุยเรื่องข่าวลือในโรงเตี๊ยม'...",
                    label_visibility="collapsed",
                )
            with c_sub:
                sub_act = st.form_submit_button("🚀 ส่งคำพูด/ทำ", use_container_width=True)

        if sub_act and user_action.strip():
            _process_player_turn_action(user_action.strip(), player, world, current_room)

    with col_cockpit:
        combat_active = world.get("combat_state") is not None
        if combat_active:
            render_combat_panel(player, world, current_room)
        else:
            _render_pending_checks_and_rerolls(player, world, current_room)

            has_shops_or_nb = bool(current_room.get("shops")) or (
                current_room.get("id") in ("town_riverside", "tavern")
                or current_room.get("notice_board") is not None
                or "innkeeper_mira" in current_room.get("npcs", [])
            )
            town_tab_lbl = "🏪 ร้านค้า & ภารกิจ ✨" if has_shops_or_nb else "🏪 ร้านค้า & ภารกิจ"

            tab_act, tab_nav, tab_town, tab_spells = st.tabs([
                "🗣️ ทางเลือก & สนทนา",
                "🧭 เดินทาง & สำรวจ",
                town_tab_lbl,
                "🪄 คาถา & ทอยเต๋า",
            ])

            with tab_act:
                _render_actions_tab(player, world, current_room)

            with tab_nav:
                _render_environment_tab(player, world, current_room)
                _render_navigation_column(player, world)

            with tab_town:
                render_town_tab(player, world, current_room)

            with tab_spells:
                _render_spells_and_tools_tab(player, world, current_room)

