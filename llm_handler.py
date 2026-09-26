"""
llm_handler.py — Phase 6
The Brain of the D&D AI DM Engine.

Spec Section 12, 12a-d, 13, 13b / Part 11 & 13.
Python owns 100% of all math, rules, rolls, and state routing.
LLM owns narrative generation and unstructured intention extraction.

Features:
1. Tiered System Prompt (0: Override, 1: Tone/Hook, 2: Companions, 3: RAG Lore, 4: Condensed Rules)
2. Token Budget Enforcement (70% cap of num_ctx, drops Tiers 4 -> 3 -> 2; Tiers 0 and 1 never dropped)
3. Two-Call Architecture:
   - Narrative Call: streams/returns plain narrative using full tiered prompt + 6-turn history buffer.
   - Extraction Call: minimal prompt (no system prompt/history/lore), returns JSON schema, 1-retry policy on syntax error.
4. Validation Integration: passes raw extraction JSON through validation.py's validate_extraction_output().
5. Model-Swap Decision: defaults to SAME model (llama3) for narrative and extraction to prevent 4GB VRAM swap overhead.
"""

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import validation

# ── Logger ────────────────────────────────────────────────────────────────────
logger = logging.getLogger("llm_handler")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setLevel(logging.DEBUG)
    _h.setFormatter(logging.Formatter("[LLM] %(levelname)s: %(message)s"))
    logger.addHandler(_h)

# ── Model Configuration & Defaults ────────────────────────────────────────────
# Spec 12d: Default to the SAME model for both narrative and extraction calls.
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "llama3")
DEFAULT_NUM_CTX = 4096
MAX_PROMPT_RATIO = 0.70  # 70% of num_ctx for system prompt + history budget

# ── Tier 4: Condensed Rules Cheat-Sheet (Spec 12a #5) ──────────────────────────
# Bullet-point form containing only the essential facts the model needs for narration.
# Replaces the full prose ruleset to save tokens (the single biggest token saving).
CONDENSED_RULESET = """
D&D 5e Rules Cheat-Sheet for DM Narration:
- Ability Checks & Skills: d20 + modifier + proficiency (if proficient) vs DC. Nat 20 is a critical success; Nat 1 is a critical failure.
- Saving Throws: Triggered by spells, traps, or hazards. Death saves: 3 successes stabilize, 3 failures result in death.
- Combat & Damage: Attacks compare d20 + attack bonus vs Target AC. Hits deal rolled damage. Critical hits double damage dice.
- Concentration: When taking damage while concentrating on a spell, must succeed on a Constitution save or lose concentration.
- Downed / 0 HP: At 0 HP, character falls unconscious and makes death saves. Healing restores consciousness immediately.
- Role of the DM: You narrate the world, NPCs, and outcomes of checks/combat. Do NOT invent or recalculate numerical results; obey System Roll/Round Results exactly.
""".strip()


# ════════════════════════════════════════════════════════════════════════════════
# TOKEN COUNTING & BUDGET MANAGEMENT (Spec Section 13)
# ════════════════════════════════════════════════════════════════════════════════

def count_tokens(text: str) -> int:
    """
    Fast integer token estimation (~4 characters per token average in English).
    Used for pre-call budget checks across system prompt tiers.
    """
    if not text:
        return 0
    return max(0, len(text) // 4)


def build_system_prompt_tiers(
    player_state: Dict[str, Any],
    world_state: Dict[str, Any],
    lore_entries: Optional[List[Any]] = None,
    companions_present: Optional[List[Dict[str, Any]]] = None,
) -> Dict[int, str]:
    """
    Build system prompt components in priority tiers (0 to 4) per Spec Section 12a.

    Priority Tiers:
      Tier 0 (Never dropped): Override block — Python owns all math.
      Tier 1 (Never dropped): Campaign tone + background_hook.
      Tier 2 (Droppable): Present companions' persona_seed.
      Tier 3 (Droppable): RAG lore (major & minor lore).
      Tier 4 (Compressed/Droppable): Condensed rules cheat-sheet.
    """
    # Tier 0: Override block (Absolute math authority)
    tier_0 = (
        "[SYSTEM OVERRIDE — ABSOLUTE MATH AUTHORITY]\n"
        "Python owns all numerical calculations, dice rolls, difficulty classes (DC), armor classes (AC), "
        "hit points (HP), and damage.\n"
        "You must treat all [System: Roll Result] and [System: Round Result] blocks as absolute truth.\n"
        "Never contradict, recompute, or modify any numerical result or outcome provided by the system."
    )

    # Tier 1: Campaign tone + background hook + game time
    char_name = player_state.get("name", "Adventurer")
    char_race = player_state.get("race", "Unknown Race")
    char_class = player_state.get("class_name", player_state.get("class", "Unknown Class"))
    char_level = player_state.get("level", 1)
    bg_hook = player_state.get("background_hook", player_state.get("background", ""))
    tone = world_state.get("campaign_tone", world_state.get("tone", "High fantasy, responsive RPG adventure."))

    gt = world_state.get("game_time", {})
    day = gt.get("day", 1)
    period = gt.get("period", "morning")

    tier_1 = (
        f"Campaign Tone: {tone}\n"
        f"Player Character: {char_name} (Level {char_level} {char_race} {char_class}).\n"
        f"Time: Day {day}, {period}.\n"
        f"Background / Motivation: {bg_hook}"
    )

    # Tier 2: Companion persona seeds (only present companions)
    tier_2_parts = []
    if companions_present:
        for comp in companions_present:
            c_name = comp.get("name", "Companion")
            c_seed = comp.get("persona_seed", comp.get("description", ""))
            if c_seed:
                tier_2_parts.append(f"- {c_name}: {c_seed}")
            else:
                tier_2_parts.append(f"- {c_name}")
    tier_2 = "Active Companions in Scene:\n" + "\n".join(tier_2_parts) if tier_2_parts else ""

    # Tier 3: RAG lore entries & active location World Events
    tier_3_parts = []

    # Active location world event context line injection (spec Section 6d / Phase 10)
    cur_loc = world_state.get("current_location")
    event_flags = world_state.get("world_event_flags")
    if cur_loc and isinstance(event_flags, dict) and cur_loc in event_flags:
        loc_flags = event_flags.get(cur_loc, [])
        for flag in loc_flags:
            import dungeon_manager
            info = dungeon_manager.WORLD_EVENT_DEFINITIONS.get(flag, {})
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

    # Tier 4: Condensed rules cheat-sheet
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
    """
    Enforce context budget by dropping tiers from bottom (4 -> 3 -> 2) if total tokens
    exceed ~70% of num_ctx (Spec Section 13).

    Tiers 0 and 1 are NEVER dropped under any circumstances.

    Returns:
      (adjusted_tiers_dict, list_of_dropped_tier_log_messages)
    """
    limit = int(num_ctx * max_ratio)
    adjusted = dict(tiers)
    dropped_logs = []

    def total_cost() -> int:
        return sum(count_tokens(text) for text in adjusted.values() if text)

    current_total = total_cost()
    logger.debug(f"Budget check: total_tokens={current_total}, limit={limit} ({int(max_ratio*100)}% of num_ctx {num_ctx}).")

    if current_total <= limit:
        return adjusted, dropped_logs

    # Step 1: Drop Tier 4 (Condensed ruleset)
    if adjusted.get(4):
        tokens_before = count_tokens(adjusted[4])
        adjusted[4] = ""
        dropped_logs.append(f"Dropped Tier 4 (condensed rules cheat-sheet, -{tokens_before} tokens)")
        logger.debug(dropped_logs[-1])
        if total_cost() <= limit:
            return adjusted, dropped_logs

    # Step 2: Reduce/Drop Tier 3 (RAG lore)
    if adjusted.get(3):
        # Try reducing Tier 3 first (keep only the first major lore entry if multi-line)
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

        # If still over limit, drop Tier 3 completely
        tokens_before = count_tokens(adjusted[3])
        adjusted[3] = ""
        dropped_logs.append(f"Dropped Tier 3 (RAG lore, -{tokens_before} tokens)")
        logger.debug(dropped_logs[-1])
        if total_cost() <= limit:
            return adjusted, dropped_logs

    # Step 3: Drop Tier 2 (Companion persona seeds)
    if adjusted.get(2):
        tokens_before = count_tokens(adjusted[2])
        adjusted[2] = ""
        dropped_logs.append(f"Dropped Tier 2 (companion persona seeds, -{tokens_before} tokens)")
        logger.debug(dropped_logs[-1])
        if total_cost() <= limit:
            return adjusted, dropped_logs

    # Tiers 0 and 1 are NEVER dropped even if total exceeds limit.
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
    """
    Build, budget-check, and assemble the final system prompt string.

    Returns:
      (assembled_prompt_string, token_costs_per_tier_dict, dropped_logs)
    """
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


# ════════════════════════════════════════════════════════════════════════════════
# HISTORY BUFFER FORMATTING (Spec Section 13)
# ════════════════════════════════════════════════════════════════════════════════

def format_llm_history(history: Optional[List[Dict[str, str]]], max_turns: int = 6) -> List[Dict[str, str]]:
    """
    Format llm_context_window — a fixed 6-turn sliding window (up to max_turns * 2 messages)
    sent to the model. Filters out UI-only entries.
    """
    if not history:
        return []

    # Filter to standard role messages
    valid_msgs = [m for m in history if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    max_msgs = max_turns * 2
    return valid_msgs[-max_msgs:]


# ════════════════════════════════════════════════════════════════════════════════
# NARRATIVE CALL (Spec Section 12a / Part 11a)
# ════════════════════════════════════════════════════════════════════════════════

def generate_narrative_response(
    user_input: str,
    player_state: Dict[str, Any],
    world_state: Dict[str, Any],
    history: Optional[List[Dict[str, str]]] = None,
    lore_entries: Optional[List[Any]] = None,
    companions_present: Optional[List[Dict[str, Any]]] = None,
    roll_result: Optional[str] = None,
    round_result: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    num_ctx: int = DEFAULT_NUM_CTX,
    client: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Generate narrative response from Ollama.
    Streams/returns plain text narrative with NO JSON leakage.

    Args:
      user_input: action text typed by the player.
      player_state: current character save dict.
      world_state: current world save dict.
      history: conversation history list.
      lore_entries: RAG lore results from memory_manager.
      companions_present: active companions in current room.
      roll_result: system roll injection block (e.g. '[System: Roll Result] ...').
      round_result: system combat round narration block (e.g. '[System: Round Result] ...').
      model: Ollama model name (default llama3).
      num_ctx: explicit context window size.
      client: optional mock/custom Ollama client for unit tests.

    Returns:
      Dict with 'narrative', 'metrics', 'token_costs', 'dropped_tiers'.
    """
    system_prompt, tier_costs, dropped_logs = assemble_system_prompt(
        player_state=player_state,
        world_state=world_state,
        lore_entries=lore_entries,
        companions_present=companions_present,
        num_ctx=num_ctx,
    )

    # Format user prompt with system injection if present
    content_parts = []
    if roll_result:
        content_parts.append(roll_result.strip())
    if round_result:
        content_parts.append(round_result.strip())
    if user_input and user_input.strip():
        content_parts.append(user_input.strip())

    user_content = "\n\n".join(content_parts) if content_parts else "The scene continues."

    # Build Ollama message payload: system prompt + 6-turn history window + current user turn
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(format_llm_history(history, max_turns=6))
    messages.append({"role": "user", "content": user_content})

    options = {"num_ctx": num_ctx, "temperature": 0.7}

    logger.debug(f"Calling Ollama narrative model='{model}', num_ctx={num_ctx}, msg_count={len(messages)}.")
    start_t = time.time()

    try:
        if client is not None:
            response = client.chat(model=model, messages=messages, options=options)
        else:
            import ollama  # type: ignore
            response = ollama.chat(model=model, messages=messages, options=options)

        elapsed = time.time() - start_t
        raw_text = response.get("message", {}).get("content", "")

        # Spec 12a: Strip any accidental JSON block leakage from narrative output
        clean_narrative = re.sub(r"```json\s*.*?```", "", raw_text, flags=re.DOTALL).strip()

        metrics = {
            "eval_count": response.get("eval_count", 0),
            "prompt_eval_count": response.get("prompt_eval_count", 0),
            "eval_duration": response.get("eval_duration", 0),
            "total_duration": response.get("total_duration", 0),
            "elapsed_seconds": elapsed,
        }

        logger.info(
            f"Narrative response complete in {elapsed:.2f}s "
            f"(eval_count={metrics['eval_count']}, prompt_eval_count={metrics['prompt_eval_count']}, "
            f"eval_duration={metrics['eval_duration']})."
        )

        return {
            "narrative": clean_narrative,
            "metrics": metrics,
            "token_costs": tier_costs,
            "dropped_tiers": dropped_logs,
        }

    except Exception as e:
        elapsed = time.time() - start_t
        logger.error(f"Narrative Ollama call failed after {elapsed:.2f}s: {e}")
        return {
            "narrative": f"The Dungeon Master pauses for a moment, pondering the turn... (Ollama error: {e})",
            "metrics": {"eval_count": 0, "prompt_eval_count": 0, "eval_duration": 0, "elapsed_seconds": elapsed},
            "token_costs": tier_costs,
            "dropped_tiers": dropped_logs,
            "error": str(e),
        }


# ════════════════════════════════════════════════════════════════════════════════
# EXTRACTION CALL (Spec Section 12b, 13b / Part 11b)
# ════════════════════════════════════════════════════════════════════════════════

EXTRACTION_INSTRUCTION = """
Analyze the DM Narrative and Player Action below. Output a SINGLE JSON object detailing any state changes that occurred.
Do NOT invent DC numbers. If a roll is required, set "requires_roll.difficulty" to one of: "easy", "medium", "hard", "very_hard".

JSON Schema (all fields optional/nullable):
{
  "state_updates": {"hp_change": (int|null), "add_item_id": (str|null), "remove_item_id": (str|null), "gold_change": (int|null), "move_to_location_id": (str|null)},
  "requires_roll": {"stat": ("STR"|"DEX"|"CON"|"INT"|"WIS"|"CHA"), "difficulty": ("easy"|"medium"|"hard"|"very_hard")},
  "combat_start": {"enemies": [(str)]},
  "world_updates": {"new_location": (object|null)},
  "quest_updates": {"new_quest": (object|null), "objective_update": (object|null)},
  "action_tags": [(str)],
  "npc_relationship_change": {"npc_id": (str), "delta": (int)}
}
""".strip()


def is_combat_active(world_state: Optional[Dict[str, Any]], combat_active: Optional[bool] = None) -> bool:
    """
    Check if combat is currently active.
    Spec 9b(6): Extraction call is skipped entirely during active combat.
    """
    if combat_active is not None:
        return combat_active
    if isinstance(world_state, dict):
        cs = world_state.get("combat_state")
        if isinstance(cs, dict) and cs.get("status") == "active":
            return True
    return False


def extract_state_updates(
    narrative_text: str,
    user_input: str,
    world_state: Optional[Dict[str, Any]] = None,
    combat_active: Optional[bool] = None,
    model: str = DEFAULT_MODEL,
    num_ctx: int = DEFAULT_NUM_CTX,
    client: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Perform extraction call to parse JSON state updates from narrative + player action.
    Spec Section 12b, 13b & 9b(6):
    - SKIPPED ENTIRELY during combat turns (returns {} without firing Ollama API call).
    - Minimal prompt: narrative text + action + short instruction ONLY (NO system prompt, NO history, NO lore).
    - Format forced to 'json' in Ollama.
    - Retry policy: raw JSON syntax failure -> retry ONCE with stricter reminder -> fallback to {} if retry fails.
    - Raw output is ALWAYS validated through validation.validate_extraction_output().

    Returns:
      Cleaned dict ready for apply_state_updates().
    """
    # Spec 9b(6): Skip extraction call entirely during active combat
    if is_combat_active(world_state, combat_active):
        logger.debug("extract_state_updates: combat is active (spec 9b-6) — skipping extraction API call entirely.")
        return {}

    extraction_prompt = (
        f"{EXTRACTION_INSTRUCTION}\n\n"
        f"Player Action: {user_input}\n"
        f"DM Narrative: {narrative_text}"
    )

    messages = [{"role": "user", "content": extraction_prompt}]
    options = {"num_ctx": num_ctx, "temperature": 0.1}

    logger.debug(f"Calling Ollama extraction model='{model}', num_ctx={num_ctx}.")
    start_t = time.time()

    def do_call(msgs: List[Dict[str, str]]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        try:
            if client is not None:
                resp = client.chat(model=model, messages=msgs, format="json", options=options)
            else:
                import ollama  # type: ignore
                resp = ollama.chat(model=model, messages=msgs, format="json", options=options)

            m = {
                "eval_count": resp.get("eval_count", 0),
                "prompt_eval_count": resp.get("prompt_eval_count", 0),
                "eval_duration": resp.get("eval_duration", 0),
                "total_duration": resp.get("total_duration", 0),
            }

            content = resp.get("message", {}).get("content", "")
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                parsed = {}
            return parsed, m
        except Exception as ex:
            return None, {"error": str(ex)}

    # Attempt 1
    raw_dict, metrics1 = do_call(messages)

    # Retry Policy (Spec 13b): if JSON syntax parse failed, retry ONCE with strict reminder
    if raw_dict is None:
        logger.warning(f"Extraction attempt 1 failed to parse valid JSON ({metrics1.get('error')}). Retrying once...")
        messages.append({
            "role": "user",
            "content": "CRITICAL ERROR: Your previous response was not valid JSON. Output ONLY a raw, valid JSON object matching the requested schema. No markdown, no prose.",
        })
        raw_dict, metrics2 = do_call(messages)
        if raw_dict is None:
            logger.error(f"Extraction attempt 2 failed after retry ({metrics2.get('error')}). Falling back to empty update.")
            return {}

    elapsed = time.time() - start_t
    logger.info(
        f"Extraction complete in {elapsed:.2f}s "
        f"(eval_count={metrics1.get('eval_count', 0)}, prompt_eval_count={metrics1.get('prompt_eval_count', 0)})."
    )

    # Validate raw output through validation.py's validate_extraction_output()
    cleaned = validation.validate_extraction_output(raw_dict, world_state=world_state)
    return cleaned
