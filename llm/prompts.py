"""
llm/prompts.py — Tiered system prompt construction (Tiers 0-4), token budget enforcement,
history window formatting, and action suggestion / single-pass JSON parsing.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("llm_handler")

DEFAULT_NUM_CTX = 4096
MAX_PROMPT_RATIO = 0.70

CONDENSED_RULESET = """
D&D 5e Rules Cheat-Sheet for DM Narration:
- Ability Checks & Skills: d20 + modifier + proficiency (if proficient) vs DC. Nat 20 is a critical success; Nat 1 is a critical failure.
- Saving Throws: Triggered by spells, traps, or hazards. Death saves: 3 successes stabilize, 3 failures result in death.
- Combat & Damage: Attacks compare d20 + attack bonus vs Target AC. Hits deal rolled damage. Critical hits double damage dice.
- Concentration: When taking damage while concentrating on a spell, must succeed on a Constitution save or lose concentration.
- Downed / 0 HP: At 0 HP, character falls unconscious and makes death saves. Healing restores consciousness immediately.
- Role of the DM: You narrate the world, NPCs, and outcomes of checks/combat. Do NOT invent or recalculate numerical results; obey System Roll/Round Results exactly.
""".strip()

DEFAULT_ACTION_SUGGESTIONS: List[str] = [
    "โจมตีศัตรูที่ใกล้ที่สุด",
    "ตั้งท่าป้องกัน (Dodge)",
    "สำรวจหาจุดได้เปรียบ",
]

SUGGESTIONS_DIRECTIVE: str = (
    "\n\n[Action Suggestions Directive]\n"
    "In exploration scenes (out of combat), guide the player by concluding with clear points of interest or questions about their next move.\n"
    "At the very end of your response, provide exactly 3 short suggested actions "
    "(under 8 words each, concrete, context-appropriate to the scene just narrated, in Thai, in first-person or imperative). "
    "Output them strictly as a trailing JSON block:\n"
    "```json\n"
    '{"suggestions": ["<action 1>", "<action 2>", "<action 3>"]}\n'
    "```"
)

SINGLE_PASS_DIRECTIVE = (
    "\n\n[Action Suggestions & State Updates Directive / คำสั่งระบบปิดท้าย]\n"
    "In exploration scenes (out of combat), guide the player by concluding with clear points of interest or questions about their next move.\n"
    "คำสั่งสำคัญ: หลังจากบรรยายเนื้อเรื่องภาษาไทย 3-4 ย่อหน้าอย่างละเอียดเสร็จแล้ว ในบรรทัดล่างสุดให้ปิดท้ายคำตอบด้วยบล็อก JSON เสมอ (output exactly one trailing JSON block at the very end):\n"
    "```json\n"
    "{\n"
    '  "suggestions": ["<action 1 in Thai>", "<action 2 in Thai>", "<action 3 in Thai>"],\n'
    '  "state_updates": {\n'
    '    "hp_change": null,\n'
    '    "add_item_id": null,\n'
    '    "remove_item_id": null,\n'
    '    "gold_change": null,\n'
    '    "move_to_location_id": null\n'
    '  },\n'
    '  "requires_roll": null,\n'
    '  "combat_start": null,\n'
    '  "world_updates": null\n'
    "}\n"
    "```\n"
    "Rules:\n"
    "- suggestions: exactly 3 short actions (under 10 words each, in Thai). Include at least 1-2 expressive dialogue or roleplay options when NPCs/companions are present. If an action involves a skill check or uses the player's Class/Background identity, prefix it in brackets like Tabletop D&D / BG3 (e.g. '[Persuasion] เจรจาขอต่อรองอย่างสุภาพ', '[Insight] สังเกตพิรุธในแววตา', '[Deception] โกหกปั้นน้ำเป็นตัว', '[Bard] ร้องเพลงกล่อมให้คลายกังวล').\n"
    "- If gold was earned or spent, set gold_change to integer (e.g. 5, -2) otherwise null.\n"
    "- If player moved to another room, set move_to_location_id otherwise null.\n"
    "- If action requires an ability check, set requires_roll to {\"stat\": \"STR\"|\"DEX\"|\"CON\"|\"INT\"|\"WIS\"|\"CHA\", \"difficulty\": \"easy\"|\"medium\"|\"hard\"} otherwise null.\n"
    "- If combat started, set combat_start to {\"enemies\": [\"enemy_id\"]} otherwise null.\n"
    "- If a new dungeon room, cave, or town area was discovered, set world_updates to {\"new_location\": {\"id\": \"unique_snake_id\", \"name\": \"Area Name\", \"type\": \"dungeon\"|\"wilderness\"|\"town\", \"description\": \"...\", \"exits\": {\"south\": \"<current_location_id>\", \"north\": null, \"east\": null, \"west\": null}, \"is_safe\": false}} otherwise null.\n"
    "- Output ONLY the JSON block at the end. Do not add any prose after it."
)

EXTRACTION_INSTRUCTION = """
Analyze the DM Narrative and Player Action below. Output a SINGLE JSON object detailing any state changes that occurred.
Do NOT invent DC numbers. If a roll is required, set "requires_roll.difficulty" to one of: "easy", "medium", "hard", "very_hard".
If an entirely new monster appears in combat not present in standard enemies (goblin_scout, goblin_warrior, goblin_boss, skeleton, bandit, bandit_captain, giant_spider, wolf, orc, zombie, cultist, town_guard, ogre, ghoul), specify its definition under "generated_monsters" keyed by an alias name, and put that alias in "combat_start.enemies".
If an entirely new custom item is found/rewarded not present in standard catalog, specify its definition under "generated_items" keyed by an alias name, and put that alias in "state_updates.add_item_id".
If an entirely new NPC or companion appears or joins the party, specify their definition under "generated_npcs" keyed by an alias name. If they join the party, put that alias in "state_updates.recruit_companion_id".
If an entirely new world event, rumor, or notice board announcement occurs, specify its definition under "generated_events" keyed by an alias name.
If an entirely new custom spell or ancient scroll is learned/discovered, specify its definition under "generated_spells" keyed by an alias name. If the player learns it, put that alias in "state_updates.learn_spell_id".
If an entirely new dungeon room, town, or wilderness area is discovered or entered, specify it under "world_updates.new_location" with fields: "id" (unique snake_case string), "name" (str), "type" ("town"|"wilderness"|"dungeon"), "description" (str), "exits" (dict with "north","south","east","west" mapping at least one direction back to an existing room ID), and "is_safe" (bool).

JSON Schema (all fields optional/nullable):
{
  "state_updates": {"hp_change": (int|null), "add_item_id": (str|null), "remove_item_id": (str|null), "gold_change": (int|null), "move_to_location_id": (str|null), "recruit_companion_id": (str|null), "learn_spell_id": (str|null)},
  "requires_roll": {"stat": ("STR"|"DEX"|"CON"|"INT"|"WIS"|"CHA"), "difficulty": ("easy"|"medium"|"hard"|"very_hard")},
  "generated_monsters": {"<enemy_alias>": {"name": (str), "hp": {"current": (int), "max": (int)}, "ac": (int), "stats": {"STR":(int),"DEX":(int),"CON":(int),"INT":(int),"WIS":(int),"CHA":(int)}, "attacks": [{"name":(str),"attack_bonus":(int),"damage":(str),"damage_type":(str),"applies_condition":(str|null),"ranged":(bool|null)}], "xp_value":(int), "gold_drop":{"min":(int),"max":(int)}, "challenge_rating":(float)}},
  "generated_items": {"<item_alias>": {"name": (str), "type": ("weapon"|"wearable"|"consumable"|"tool"|"food"), "slot": (str|null), "effects": (object), "rarity": ("common"|"uncommon"|"rare"), "value_gold": (int), "description": (str)}},
  "generated_npcs": {"<npc_alias>": {"name": (str), "role": (str|null), "persona_seed": (str|null), "secret": (str|null), "hp": (int|null), "ac": (int|null), "stats": {"STR":(int),"DEX":(int),"CON":(int),"INT":(int),"WIS":(int),"CHA":(int)}, "attacks": [{"name":(str),"attack_bonus":(int),"damage":(str),"damage_type":(str)}], "approval":(int|null)}},
  "generated_events": {"<event_alias>": {"title": (str), "location_id": (str|null), "context_line": (str), "rumor_text": (str|null), "post_to_notice_board": (bool|null), "reward_gold": (int|null)}},
  "generated_spells": {"<spell_alias>": {"name": (str), "level": (int), "type": ("attack_roll"|"attack_save"|"heal"|"buff"|"utility"), "save_stat": (str|null), "effect": (object), "description": (str)}},
  "combat_start": {"enemies": [(str)]},
  "world_updates": {"new_location": {"id": (str), "name": (str), "type": ("town"|"wilderness"|"dungeon"), "description": (str), "exits": {"north":(str|null),"south":(str|null),"east":(str|null),"west":(str|null)}, "is_safe": (bool), "loot": [(object)], "encounter_table": [(str)], "npcs": [(str)]}},
  "quest_updates": {"new_quest": (object|null), "objective_update": (object|null)},
  "action_tags": [(str)],
  "npc_relationship_change": {"npc_id": (str), "delta": (int)}
}
""".strip()


def count_tokens(text: str) -> int:
    """Fast integer token estimation (~4 characters per token average in English)."""
    if not text:
        return 0
    return max(0, len(text) // 4)


def build_system_prompt_tiers(
    player_state: Dict[str, Any],
    world_state: Dict[str, Any],
    lore_entries: Optional[List[Any]] = None,
    companions_present: Optional[List[Dict[str, Any]]] = None,
) -> Dict[int, str]:
    """Build system prompt components in priority tiers (0 to 4) per Spec Section 12a."""
    tier_0 = (
        "[SYSTEM OVERRIDE — ABSOLUTE MATH AUTHORITY]\n"
        "Python owns all numerical calculations, dice rolls, difficulty classes (DC), armor classes (AC), "
        "hit points (HP), and damage.\n"
        "You must treat all [System: Roll Result] and [System: Round Result] blocks as absolute truth.\n"
        "Never contradict, recompute, or modify any numerical result or outcome provided by the system."
    )

    char_name = player_state.get("name", "Adventurer")
    char_race = player_state.get("race", "Unknown Race")
    char_class = player_state.get("class_name", player_state.get("class", "Unknown Class"))
    char_sub = player_state.get("subclass_name") or player_state.get("subclass", "")
    sub_str = f" ({char_sub})" if char_sub else ""
    char_level = player_state.get("level", 1)
    bg_name = player_state.get("background", "")
    bg_hook = player_state.get("background_hook", bg_name)
    tone = world_state.get("campaign_tone", world_state.get("tone", "High fantasy, responsive RPG adventure."))

    gt = world_state.get("game_time", {})
    day = gt.get("day", 1)
    period = gt.get("period", "morning")

    tier_1 = (
        f"Campaign Tone: {tone}\n"
        f"Player Character: {char_name} (Level {char_level} {char_race} {char_class}{sub_str}, Background: {bg_name}).\n"
        f"Time: Day {day}, {period}.\n"
        f"Background / Motivation: {bg_hook}\n"
        f"DM Role, Dialogue & Roleplay Guidelines (MANDATORY):\n"
        f"1. You are a master Tabletop D&D 5e Dungeon Master (DM) narrating an interactive, character-driven fantasy adventure in Thai (ภาษาไทย).\n"
        f"2. PRIORITIZE EXPRESSIVE DIALOGUE & ROLEPLAY: When NPCs or party companions are present, write vivid, direct spoken dialogue in quotation marks (\"...\") with distinct voices, mannerisms, body language, secrets, humor, or hidden motives — never summarize conversations dryly.\n"
        f"3. REACT TO PLAYER IDENTITY: Have NPCs notice and react to {char_name}'s race ({char_race}), class ({char_class}{sub_str}), and background ({bg_name}). Reward clever banter, bluffing, intimidation, empathy, or unorthodox tabletop problem-solving.\n"
        f"4. ACTIVE COMPANION BANTER: If companions are in the party, let them chime in with short remarks, banter, advice, or emotional reactions during conversations and exploration.\n"
        f"5. Always write rich, immersive, multi-paragraph responses (2-4 paragraphs) and conclude non-combat scenes with clear hooks, NPC questions, or intriguing choices."
    )

    tier_2_parts = []
    if isinstance(world_state, dict) and int(world_state.get("pacified_count", 0)) > 0:
        p_cnt = int(world_state.get("pacified_count", 0))
        pm_title = world_state.get("peacemaker_title", "Silver-Tongued Diplomat")
        tier_2_parts.append(
            f"- Player Social Reputation: {pm_title} (Resolved {p_cnt} conflicts peacefully without bloodshed; NPCs respect the player's diplomacy and eloquence)."
        )
    active_comps = companions_present
    if active_comps is None and isinstance(world_state, dict):
        active_comps = world_state.get("party", {}).get("companions", [])
    if active_comps:
        for comp in active_comps:
            if not isinstance(comp, dict):
                continue
            c_id = comp.get("id") or comp.get("name", "Companion")
            c_name = comp.get("name", "Companion")
            c_seed = comp.get("persona_seed", comp.get("description", ""))
            c_secret = comp.get("secret", "")
            c_app = world_state.get("companions_approval", {}).get(c_id, {}).get("approval", comp.get("approval", 50)) if isinstance(world_state, dict) else 50
            sec_tag = ""
            if c_secret:
                if c_app >= 65:
                    sec_tag = f" [SECRET UNLOCKED — WILLING TO SHARE: {c_secret}]"
                else:
                    sec_tag = f" [HIDDEN SECRET (reveal on high trust or successful social check): {c_secret}]"
            if c_seed:
                tier_2_parts.append(f"- {c_name} (Approval: {c_app}/100): {c_seed}{sec_tag}")
            else:
                tier_2_parts.append(f"- {c_name} (Approval: {c_app}/100){sec_tag}")

    # Include Local NPCs in current room & NPC relationships so DM remembers how NPCs feel about the player!
    if isinstance(world_state, dict):
        cur_loc_id = world_state.get("current_location")
        room_npcs = []
        if cur_loc_id:
            try:
                import dungeon_manager
                c_room = dungeon_manager.get_current_room(world_state)
                if isinstance(c_room, dict):
                    room_npcs = list(c_room.get("npcs", []))
            except Exception:
                pass
        npc_rels = world_state.get("npc_relationships", {})
        gen_npcs = world_state.get("generated_npcs", {})
        try:
            from rules import _get_npc_catalog
            static_npcs = _get_npc_catalog()
        except Exception:
            static_npcs = {}
        npc_lines = []
        for rn in room_npcs:
            rel_val = npc_rels.get(rn, 0)
            rel_str = f", Attitude: {rel_val:+d}" if rel_val else ""
            # Check if this NPC has a persona in generated_npcs or static npc_catalog.json
            g_info = next((v for k, v in gen_npcs.items() if isinstance(v, dict) and (k == rn or v.get("name") == rn)), None)
            if not g_info and isinstance(static_npcs, dict):
                g_info = static_npcs.get(rn) or next((v for k, v in static_npcs.items() if isinstance(v, dict) and v.get("name") == rn), None)
            disp_name = g_info.get("name", rn) if isinstance(g_info, dict) else rn
            p_str = f" — {g_info.get('role', '')}: {g_info.get('persona_seed', '')}" if g_info else ""
            n_secret = g_info.get("secret", "") if isinstance(g_info, dict) else ""
            if n_secret:
                if rel_val >= 15:
                    p_str += f" [SECRET UNLOCKED — WILLING TO SHARE: {n_secret}]"
                else:
                    p_str += f" [HIDDEN SECRET (reveal if player succeeds on Persuasion/Deception/Intimidation/Insight or builds trust): {n_secret}]"
            npc_lines.append(f"- {disp_name} ({rn}){rel_str}{p_str}" if disp_name != rn else f"- {rn}{rel_str}{p_str}")
        if npc_lines:
            tier_2_parts.append("Local NPCs in Current Scene:\n" + "\n".join(npc_lines))

    tier_2 = "Active Companions & Scene NPCs:\n" + "\n".join(tier_2_parts) if tier_2_parts else ""

    tier_3_parts = []
    cur_loc = world_state.get("current_location")
    event_flags = world_state.get("world_event_flags")
    if cur_loc and isinstance(event_flags, dict) and cur_loc in event_flags:
        loc_flags = event_flags.get(cur_loc, [])
        for flag in loc_flags:
            import dungeon_manager
            info = dungeon_manager.WORLD_EVENT_DEFINITIONS.get(flag)
            if not info:
                info = world_state.get("dynamic_world_events", {}).get(flag, {})
            c_line = info.get("context_line")
            if c_line:
                tier_3_parts.append(f"- [WORLD EVENT] {c_line}")

    if lore_entries:
        for entry in lore_entries:
            if isinstance(entry, dict):
                l_text = entry.get("text", "")
                l_type = entry.get("type", "lore")
                if l_text:
                    tier_3_parts.append(f"- [{l_type.upper()}] {l_text}")
            elif isinstance(entry, str) and entry.strip():
                tier_3_parts.append(f"- {entry.strip()}")
    tier_3 = "RELEVANT CAMPAIGN LORE & PAST EVENTS:\n" + "\n".join(tier_3_parts) if tier_3_parts else ""

    tier_4 = CONDENSED_RULESET

    return {
        0: tier_0.strip(),
        1: tier_1.strip(),
        2: tier_2.strip(),
        3: tier_3.strip(),
        4: tier_4.strip(),
    }


def enforce_context_budget(
    tiers: Dict[int, str],
    num_ctx: int = DEFAULT_NUM_CTX,
    max_ratio: float = MAX_PROMPT_RATIO,
) -> Tuple[Dict[int, str], List[str]]:
    """Enforce context budget by dropping tiers from bottom (4 -> 3 -> 2) if total tokens exceed ~70% of num_ctx."""
    limit = int(num_ctx * max_ratio)
    adjusted = dict(tiers)
    dropped_logs = []

    def total_cost() -> int:
        return sum(count_tokens(text) for text in adjusted.values() if text)

    current_total = total_cost()
    logger.debug(f"Budget check: total_tokens={current_total}, limit={limit} ({int(max_ratio*100)}% of num_ctx {num_ctx}).")

    if current_total <= limit:
        return adjusted, dropped_logs

    if adjusted.get(4):
        tokens_before = count_tokens(adjusted[4])
        adjusted[4] = ""
        dropped_logs.append(f"Dropped Tier 4 (condensed rules cheat-sheet, -{tokens_before} tokens)")
        logger.debug(dropped_logs[-1])
        if total_cost() <= limit:
            return adjusted, dropped_logs

    if adjusted.get(3):
        lines = [line for line in adjusted[3].split("\n") if line.strip()]
        major_lines = [l for l in lines if "[MAJOR]" in l]
        if len(lines) > 1 and major_lines:
            reduced_t3 = "RELEVANT CAMPAIGN LORE & PAST EVENTS:\n" + major_lines[0]
            tokens_saved = count_tokens(adjusted[3]) - count_tokens(reduced_t3)
            adjusted[3] = reduced_t3
            dropped_logs.append(f"Reduced Tier 3 (RAG lore kept 1 major entry, -{tokens_saved} tokens)")
            logger.debug(dropped_logs[-1])
            if total_cost() <= limit:
                return adjusted, dropped_logs

        tokens_before = count_tokens(adjusted[3])
        adjusted[3] = ""
        dropped_logs.append(f"Dropped Tier 3 (RAG lore, -{tokens_before} tokens)")
        logger.debug(dropped_logs[-1])
        if total_cost() <= limit:
            return adjusted, dropped_logs

    if adjusted.get(2):
        tokens_before = count_tokens(adjusted[2])
        adjusted[2] = ""
        dropped_logs.append(f"Dropped Tier 2 (companion persona seeds, -{tokens_before} tokens)")
        logger.debug(dropped_logs[-1])
        if total_cost() <= limit:
            return adjusted, dropped_logs

    final_total = total_cost()
    logger.debug(f"Budget enforcement complete. Final prompt tokens={final_total} (Tiers 0 and 1 preserved).")
    return adjusted, dropped_logs


def assemble_system_prompt(
    player_state: Dict[str, Any],
    world_state: Dict[str, Any],
    lore_entries: Optional[List[Any]] = None,
    companions_present: Optional[List[Dict[str, Any]]] = None,
    num_ctx: int = DEFAULT_NUM_CTX,
    max_ratio: float = MAX_PROMPT_RATIO,
) -> Tuple[str, Dict[int, int], List[str]]:
    """Build, budget-check, and assemble the final system prompt string."""
    raw_tiers = build_system_prompt_tiers(player_state, world_state, lore_entries, companions_present)
    tier_costs = {t: count_tokens(text) for t, text in raw_tiers.items()}

    logger.debug(
        f"System prompt tier token costs: Tier 0={tier_costs[0]}, Tier 1={tier_costs[1]}, "
        f"Tier 2={tier_costs[2]}, Tier 3={tier_costs[3]}, Tier 4={tier_costs[4]} "
        f"(Raw Total={sum(tier_costs.values())})"
    )

    adjusted_tiers, dropped_logs = enforce_context_budget(raw_tiers, num_ctx, max_ratio)

    ordered_parts = [adjusted_tiers[i] for i in range(5) if adjusted_tiers.get(i)]
    prompt_str = "\n\n".join(ordered_parts)

    return prompt_str, tier_costs, dropped_logs


def format_llm_history(history: Optional[List[Dict[str, str]]], max_turns: int = 6) -> List[Dict[str, str]]:
    """Format llm_context_window — a fixed sliding window sent to the model."""
    if not history:
        return []

    valid_msgs = [m for m in history if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    max_msgs = max_turns * 2
    return valid_msgs[-max_msgs:]


def extract_and_strip_suggestions(raw_text: str) -> Tuple[str, List[str]]:
    """Extract trailing action suggestions JSON block and strip all JSON leakage from narrative."""
    suggestions: List[str] = []

    fence_pattern = r"```(?:json)?\s*(\{[\s\S]*?\"suggestions\"[\s\S]*?\})\s*```"
    match = re.search(fence_pattern, raw_text, flags=re.DOTALL | re.IGNORECASE)

    json_candidate = None
    if match:
        json_candidate = match.group(1).strip()
    else:
        raw_pattern = r"(\{[\s\r\n]*\"suggestions\"[\s\S]*?\})"
        match_raw = re.search(raw_pattern, raw_text, flags=re.DOTALL | re.IGNORECASE)
        if match_raw:
            json_candidate = match_raw.group(1).strip()

    if json_candidate:
        try:
            sanitized = re.sub(r",\s*([\]}])", r"\1", json_candidate)
            data = json.loads(sanitized)
            if isinstance(data, dict) and "suggestions" in data:
                raw_list = data["suggestions"]
                if isinstance(raw_list, list):
                    for item in raw_list:
                        s = str(item).strip()
                        if s:
                            suggestions.append(s)
        except Exception as parse_err:
            logger.debug(f"Action suggestions parse failed: {parse_err}")

    cleaned_suggestions: List[str] = []
    for s in suggestions:
        if len(cleaned_suggestions) < 3 and s not in cleaned_suggestions:
            cleaned_suggestions.append(s)

    for default_s in DEFAULT_ACTION_SUGGESTIONS:
        if len(cleaned_suggestions) >= 3:
            break
        if default_s not in cleaned_suggestions:
            cleaned_suggestions.append(default_s)

    while len(cleaned_suggestions) < 3:
        cleaned_suggestions.append(DEFAULT_ACTION_SUGGESTIONS[len(cleaned_suggestions)])

    clean_narrative = raw_text
    clean_narrative = re.sub(r"```json\s*[\s\S]*?```", "", clean_narrative, flags=re.DOTALL).strip()
    clean_narrative = re.sub(r"```\s*\{[\s\S]*?\}\s*```", "", clean_narrative, flags=re.DOTALL).strip()
    raw_sug_pattern = r"\{[\s\r\n]*\"suggestions\"[\s\S]*?\}"
    clean_narrative = re.sub(raw_sug_pattern, "", clean_narrative, flags=re.DOTALL | re.IGNORECASE).strip()
    clean_narrative = re.sub(r"(?:```(?:json)?\s*)?\{[\s\r\n]*\"suggestions\"[\s\S]*$", "", clean_narrative, flags=re.DOTALL | re.IGNORECASE).strip()
    clean_narrative = re.sub(r"```(?:json)?\s*$", "", clean_narrative, flags=re.DOTALL).strip()

    return clean_narrative, cleaned_suggestions


def extract_and_strip_single_pass(
    raw_text: str,
    world_state: Optional[Dict[str, Any]] = None,
    player_state: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[str], Optional[Dict[str, Any]]]:
    """Extract single-pass trailing JSON block, strip all JSON from narrative, and validate extracted state updates."""
    suggestions: List[str] = []
    state_updates_res: Optional[Dict[str, Any]] = None

    fence_pattern = r"```(?:json)?\s*(\{[\s\S]*?\})\s*```"
    matches = list(re.finditer(fence_pattern, raw_text, flags=re.DOTALL))

    json_candidate = None
    if matches:
        json_candidate = matches[-1].group(1).strip()
    else:
        raw_pattern = r"(\{[\s\r\n]*\"(?:suggestions|state_updates)\"[\s\S]*\})\s*$"
        m_raw = re.search(raw_pattern, raw_text, flags=re.DOTALL)
        if m_raw:
            json_candidate = m_raw.group(1).strip()

    if json_candidate:
        try:
            sanitized = re.sub(r",\s*([\]}])", r"\1", json_candidate)
            data = json.loads(sanitized)
            if isinstance(data, dict):
                if "suggestions" in data and isinstance(data["suggestions"], list):
                    for item in data["suggestions"]:
                        s = str(item).strip()
                        if s:
                            suggestions.append(s)
                try:
                    import validation
                    state_updates_res = validation.validate_extraction_output(
                        data, world_state=world_state, player_state=player_state
                    )
                except Exception as val_err:
                    logger.debug(f"Single-pass validation failed: {val_err}")
                    state_updates_res = {}
        except Exception as parse_err:
            logger.debug(f"Single-pass json parse failed: {parse_err}")

    cleaned_suggestions: List[str] = []
    for s in suggestions:
        if len(cleaned_suggestions) < 3 and s not in cleaned_suggestions:
            cleaned_suggestions.append(s)
    for default_s in DEFAULT_ACTION_SUGGESTIONS:
        if len(cleaned_suggestions) >= 3:
            break
        if default_s not in cleaned_suggestions:
            cleaned_suggestions.append(default_s)
    while len(cleaned_suggestions) < 3:
        cleaned_suggestions.append(DEFAULT_ACTION_SUGGESTIONS[len(cleaned_suggestions)])

    clean_narrative = raw_text
    clean_narrative = re.sub(r"```json\s*[\s\S]*?```", "", clean_narrative, flags=re.DOTALL).strip()
    clean_narrative = re.sub(r"```\s*\{[\s\S]*?\}\s*```", "", clean_narrative, flags=re.DOTALL).strip()
    clean_narrative = re.sub(r"\{[\s\r\n]*\"(?:suggestions|state_updates)\"[\s\S]*$", "", clean_narrative, flags=re.DOTALL | re.IGNORECASE).strip()
    clean_narrative = re.sub(r"```(?:json)?\s*$", "", clean_narrative, flags=re.DOTALL).strip()

    return clean_narrative, cleaned_suggestions, state_updates_res


def is_passive_exploration_turn(user_input: str, narrative_text: str = "") -> bool:
    """Determine if a turn is a purely narrative/dialogue/observational interaction."""
    if not user_input:
        return True

    text = (str(user_input) + " " + (str(narrative_text[:300]) if narrative_text else "")).lower()

    thai_active_triggers = [
        "โจมตี", "ฟัน", "ยิง", "สู้", "ฆ่า", "ร่ายมนต์", "แทง", "ต่อสู้",
        "ซื้อ", "ขาย", "จ่าย", "เช่า", "ขโมย", "ล้วงกระเป๋า", "เก็บ", "หยิบ", "เปิดหีบ", "ค้น", "มอบ", "ให้", "แลก", "ปล้น",
        "รับ", "ตกลง", "ภารกิจ", "เควส",
        "เรียนรู้", "คาถา", "ม้วนคัมภีร์",
        "เข้าร่วม", "จ้าง", "ชวน",
        "กิน", "ดื่ม", "ใช้ยา", "พักผ่อน", "นอนพัก",
        "เดินทางไป", "มุ่งหน้าสู่", "ออกจาก", "ลงดันเจี้ยน", "เข้าไปใน", "เปิดประตู", "สำรวจ",
    ]
    for trigger in thai_active_triggers:
        if trigger in text:
            return False

    en_pattern = (
        r"\b(?:attack|fight|cast|strike|shoot|kill|"
        r"buy|sell|pay|steal|loot|take|give|trade|search|explore|"
        r"drink|eat|potion|rest|sleep|"
        r"accept|quest|learn|join|recruit|hire|"
        r"travel to|go to|leave|enter|open door|dungeon)\b"
    )
    if re.search(en_pattern, text):
        return False

    return True
