"""
llm/generator.py — Narrative generation, state update extraction, and camp companion dialogue.
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from llm.prompts import (
    DEFAULT_ACTION_SUGGESTIONS,
    DEFAULT_NUM_CTX,
    EXTRACTION_INSTRUCTION,
    SINGLE_PASS_DIRECTIVE,
    SUGGESTIONS_DIRECTIVE,
    assemble_system_prompt,
    extract_and_strip_single_pass,
    extract_and_strip_suggestions,
    format_llm_history,
)

logger = logging.getLogger("llm_handler")


def generate_narrative_response(
    user_input: str,
    player_state: Dict[str, Any],
    world_state: Dict[str, Any],
    history: Optional[List[Dict[str, str]]] = None,
    lore_entries: Optional[List[Any]] = None,
    companions_present: Optional[List[Dict[str, Any]]] = None,
    roll_result: Optional[str] = None,
    round_result: Optional[str] = None,
    include_suggestions: bool = True,
    single_pass: bool = False,
    model: str = "llama3",
    num_ctx: int = DEFAULT_NUM_CTX,
    client: Optional[Any] = None,
    ollama_model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate narrative response from Cloud engines (Groq/Gemini) or local Ollama.
    Streams/returns plain text narrative with NO JSON leakage.
    """
    import llm_handler
    system_prompt, tier_costs, dropped_logs = assemble_system_prompt(
        player_state=player_state,
        world_state=world_state,
        lore_entries=lore_entries,
        companions_present=companions_present,
        num_ctx=num_ctx,
    )

    content_parts = []
    if roll_result:
        content_parts.append(roll_result.strip())
    if round_result:
        content_parts.append(round_result.strip())
    if user_input and user_input.strip():
        content_parts.append(user_input.strip())

    user_content = "\n\n".join(content_parts) if content_parts else "The scene continues."
    if single_pass:
        user_content += SINGLE_PASS_DIRECTIVE
    elif include_suggestions:
        user_content += SUGGESTIONS_DIRECTIVE

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(format_llm_history(history, max_turns=6))
    messages.append({"role": "user", "content": user_content})

    options = {"num_ctx": num_ctx, "temperature": 0.7}

    effective_model = llm_handler.resolve_model(model, client=client)
    effective_fallback_model = llm_handler.resolve_model(ollama_model, client=client) if ollama_model else llm_handler.resolve_model(None, client=client)
    logger.debug(f"Calling LLM narrative model='{effective_model}', num_ctx={num_ctx}, msg_count={len(messages)}.")
    start_t = time.time()

    try:
        if client is not None:
            response = client.chat(model=effective_model, messages=messages, options=options)
            raw_text = response.get("message", {}).get("content", "")
            metrics = {
                "eval_count": response.get("eval_count", 0),
                "prompt_eval_count": response.get("prompt_eval_count", 0),
                "eval_duration": response.get("eval_duration", 0),
                "total_duration": response.get("total_duration", 0),
                "elapsed_seconds": time.time() - start_t,
            }
        elif llm_handler.get_active_engine() == "groq" and llm_handler.get_groq_api_key():
            groq_messages = [{"role": "system", "content": system_prompt}]
            for h in format_llm_history(history, max_turns=12):
                groq_messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
            groq_messages.append({"role": "user", "content": user_content})

            groq_cfg = llm_handler.load_groq_config()
            cached_models = llm_handler._groq_models_cache.get("models", [])
            if model and (model in cached_models or model in llm_handler.GROQ_AVAILABLE_MODELS):
                groq_m = model
            else:
                groq_m = groq_cfg.get("model") or llm_handler.DEFAULT_GROQ_MODEL
            g_text, g_metrics = llm_handler.call_groq_api(
                messages=groq_messages,
                model=groq_m,
                temperature=0.7,
            )
            if g_text is not None:
                raw_text = g_text
                metrics = g_metrics
            else:
                logger.warning(f"Groq API call failed ({g_metrics.get('error')}). Attempting fallback...")
                import ollama  # type: ignore
                response = ollama.chat(model=effective_fallback_model, messages=messages, options=options)
                raw_text = response.get("message", {}).get("content", "")
                metrics = {
                    "eval_count": response.get("eval_count", 0),
                    "prompt_eval_count": response.get("prompt_eval_count", 0),
                    "eval_duration": response.get("eval_duration", 0),
                    "total_duration": response.get("total_duration", 0),
                    "elapsed_seconds": time.time() - start_t,
                }
        elif llm_handler.get_active_engine() == "gemini" and llm_handler.get_gemini_api_key():
            gemini_contents = []
            for h in format_llm_history(history, max_turns=12):
                g_role = "model" if h.get("role") == "assistant" else "user"
                gemini_contents.append({"role": g_role, "parts": [{"text": h.get("content", "")}]})
            gemini_contents.append({"role": "user", "parts": [{"text": user_content}]})

            g_text, g_metrics = llm_handler.call_gemini_api(
                contents=gemini_contents,
                system_instruction=system_prompt,
                temperature=0.7,
            )
            if g_text is not None:
                raw_text = g_text
                metrics = g_metrics
            else:
                logger.warning("Gemini API call failed or timed out. Attempting fallback...")
                import ollama  # type: ignore
                response = ollama.chat(model=effective_fallback_model, messages=messages, options=options)
                raw_text = response.get("message", {}).get("content", "")
                metrics = {
                    "eval_count": response.get("eval_count", 0),
                    "prompt_eval_count": response.get("prompt_eval_count", 0),
                    "eval_duration": response.get("eval_duration", 0),
                    "total_duration": response.get("total_duration", 0),
                    "elapsed_seconds": time.time() - start_t,
                }
        else:
            import ollama  # type: ignore
            ollama_target = effective_fallback_model if (ollama_model or effective_model in llm_handler.KNOWN_SHUTDOWN_MODELS or "gemini" in effective_model or "qwen/" in effective_model) else effective_model
            response = ollama.chat(model=ollama_target, messages=messages, options=options)
            raw_text = response.get("message", {}).get("content", "")
            metrics = {
                "eval_count": response.get("eval_count", 0),
                "prompt_eval_count": response.get("prompt_eval_count", 0),
                "eval_duration": response.get("eval_duration", 0),
                "total_duration": response.get("total_duration", 0),
                "elapsed_seconds": time.time() - start_t,
            }

        elapsed = time.time() - start_t
        if single_pass:
            clean_narrative, suggestions, single_pass_updates = extract_and_strip_single_pass(
                raw_text, world_state=world_state, player_state=player_state
            )
        else:
            clean_narrative, suggestions = extract_and_strip_suggestions(raw_text)
            single_pass_updates = None

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
            "suggestions": suggestions,
            "single_pass_updates": single_pass_updates,
        }

    except Exception as e:
        elapsed = time.time() - start_t
        logger.error(f"Narrative LLM call failed after {elapsed:.2f}s: {e}")
        return {
            "narrative": (
                f"The Dungeon Master pauses for a moment, pondering the turn... (Ollama error: {e})\n\n"
                f"💡 *คำแนะนำ: หากไม่ได้ติดตั้ง Ollama ในเครื่อง กรุณาเปิดแถบเมนูด้านซ้าย **🤖 Dungeon Master AI** "
                f"แล้วใส่ API Key ของ **🚀 Groq Cloud** หรือ **⚡ Google Gemini** เพื่อใช้งาน AI ผ่าน Cloud*"
            ),
            "metrics": {"eval_count": 0, "prompt_eval_count": 0, "eval_duration": 0, "elapsed_seconds": elapsed},
            "token_costs": tier_costs,
            "dropped_tiers": dropped_logs,
            "suggestions": list(DEFAULT_ACTION_SUGGESTIONS),
            "error": str(e),
        }


def is_combat_active(world_state: Optional[Dict[str, Any]], combat_active: Optional[bool] = None) -> bool:
    """Check if combat is currently active (Spec 9b-6: extraction call skipped during combat)."""
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
    player_state: Optional[Dict[str, Any]] = None,
    combat_active: Optional[bool] = None,
    model: str = "llama3",
    num_ctx: int = DEFAULT_NUM_CTX,
    client: Optional[Any] = None,
    ollama_model: Optional[str] = None,
) -> Dict[str, Any]:
    """Perform extraction call to parse JSON state updates from narrative + player action."""
    import llm_handler
    import validation
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

    effective_model = llm_handler.resolve_model(model, client=client)
    effective_fallback_model = llm_handler.resolve_model(ollama_model, client=client) if ollama_model else llm_handler.resolve_model(None, client=client)
    logger.debug(f"Calling LLM extraction model='{effective_model}', num_ctx={num_ctx}.")
    start_t = time.time()

    def do_call(msgs: List[Dict[str, str]], force_ollama: bool = False) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        m: Dict[str, Any] = {}
        try:
            if client is not None:
                resp = client.chat(model=effective_model, messages=msgs, format="json", options=options)
                m = {
                    "eval_count": resp.get("eval_count", 0),
                    "prompt_eval_count": resp.get("prompt_eval_count", 0),
                    "eval_duration": resp.get("eval_duration", 0),
                    "total_duration": resp.get("total_duration", 0),
                    "engine": "client",
                }
                content = resp.get("message", {}).get("content", "")
            elif not force_ollama and llm_handler.get_active_engine() == "groq" and llm_handler.get_groq_api_key():
                groq_cfg = llm_handler.load_groq_config()
                cached_models = llm_handler._groq_models_cache.get("models", [])
                if model and (model in cached_models or model in llm_handler.GROQ_AVAILABLE_MODELS):
                    groq_m = model
                else:
                    groq_m = groq_cfg.get("model") or llm_handler.DEFAULT_GROQ_MODEL
                g_text, g_metrics = llm_handler.call_groq_api(
                    messages=msgs,
                    model=groq_m,
                    temperature=0.1,
                    response_json=True,
                )
                m = g_metrics or {}
                if g_text is not None:
                    content = g_text
                else:
                    import ollama  # type: ignore
                    resp = ollama.chat(model=effective_fallback_model, messages=msgs, format="json", options=options)
                    m = {
                        "eval_count": resp.get("eval_count", 0),
                        "prompt_eval_count": resp.get("prompt_eval_count", 0),
                        "eval_duration": resp.get("eval_duration", 0),
                        "total_duration": resp.get("total_duration", 0),
                        "engine": "ollama",
                    }
                    content = resp.get("message", {}).get("content", "")
            elif not force_ollama and llm_handler.get_active_engine() == "gemini" and llm_handler.get_gemini_api_key():
                g_prompt = msgs[-1].get("content", "")
                g_text, g_metrics = llm_handler.call_gemini_api(
                    contents=[{"role": "user", "parts": [{"text": g_prompt}]}],
                    temperature=0.1,
                    response_mime_type="application/json",
                )
                m = g_metrics or {}
                if g_text is not None:
                    content = g_text
                else:
                    import ollama  # type: ignore
                    resp = ollama.chat(model=effective_fallback_model, messages=msgs, format="json", options=options)
                    m = {
                        "eval_count": resp.get("eval_count", 0),
                        "prompt_eval_count": resp.get("prompt_eval_count", 0),
                        "eval_duration": resp.get("eval_duration", 0),
                        "total_duration": resp.get("total_duration", 0),
                        "engine": "ollama",
                    }
                    content = resp.get("message", {}).get("content", "")
            else:
                import ollama  # type: ignore
                ollama_target = effective_fallback_model if (ollama_model or force_ollama or effective_model in llm_handler.KNOWN_SHUTDOWN_MODELS or "gemini" in effective_model or "qwen/" in effective_model) else effective_model
                resp = ollama.chat(model=ollama_target, messages=msgs, format="json", options=options)
                m = {
                    "eval_count": resp.get("eval_count", 0),
                    "prompt_eval_count": resp.get("prompt_eval_count", 0),
                    "eval_duration": resp.get("eval_duration", 0),
                    "total_duration": resp.get("total_duration", 0),
                    "engine": "ollama",
                }
                content = resp.get("message", {}).get("content", "")

            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                parsed = {}
            return parsed, m
        except Exception as ex:
            err_m = dict(m) if m else {}
            err_m["error"] = str(ex)
            return None, err_m

    raw_dict, metrics1 = do_call(messages)

    if raw_dict is None:
        was_gemini = metrics1.get("engine") == "gemini"
        if was_gemini:
            logger.warning(
                f"Extraction attempt 1 via Gemini failed to parse valid JSON ({metrics1.get('error')}). "
                "Skipping second Gemini call to prevent double-billing quota; attempting local Ollama fallback retry..."
            )
        else:
            logger.warning(f"Extraction attempt 1 failed to parse valid JSON ({metrics1.get('error')}). Retrying once...")

        messages.append({
            "role": "user",
            "content": "CRITICAL ERROR: Your previous response was not valid JSON. Output ONLY a raw, valid JSON object matching the requested schema. No markdown, no prose.",
        })
        raw_dict, metrics2 = do_call(messages, force_ollama=was_gemini)
        if raw_dict is None:
            logger.error(f"Extraction attempt 2 failed after retry ({metrics2.get('error')}). Falling back to empty update.")
            return {}

    elapsed = time.time() - start_t
    logger.info(
        f"Extraction complete in {elapsed:.2f}s "
        f"(eval_count={metrics1.get('eval_count', 0)}, prompt_eval_count={metrics1.get('prompt_eval_count', 0)})."
    )

    try:
        cleaned = validation.validate_extraction_output(raw_dict, world_state=world_state, player_state=player_state)
        return cleaned
    except Exception as ex:
        logger.error(f"validate_extraction_output failed: {ex}")
        return {}


def generate_camp_dialogue(
    companion_id: str,
    memory_manager: Any,
    player_state: Dict[str, Any],
    world_state: Optional[Dict[str, Any]] = None,
    client: Optional[Any] = None,
    model: str = "llama3",
    num_ctx: int = DEFAULT_NUM_CTX,
    use_live_llm: Optional[bool] = None,
    ollama_model: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Spec Module D §2 / Phase 14.2:
    Generate camp companion dialogue reacting to recent notable events.
    """
    import llm_handler
    if world_state is not None:
        import dungeon_manager
        if not dungeon_manager.is_camp_context(world_state):
            logger.debug(
                f"generate_camp_dialogue: not in camp/rest context "
                f"(combat={bool(world_state.get('combat_state'))}, loc={world_state.get('current_location')}). Returning None."
            )
            return None

    lore_entries = []
    if memory_manager is not None:
        try:
            if hasattr(memory_manager, "get_relevant_lore"):
                lore_entries = memory_manager.get_relevant_lore(
                    f"notable events encounters adventure {companion_id}",
                    n_results=3,
                )
            elif callable(memory_manager):
                lore_entries = memory_manager(
                    f"notable events encounters adventure {companion_id}",
                    n_results=3,
                )
        except Exception as ex:
            logger.warning(f"generate_camp_dialogue: get_relevant_lore call failed: {ex}")
            lore_entries = []

    extracted_lore: List[str] = []
    if isinstance(lore_entries, list):
        for entry in lore_entries:
            if isinstance(entry, dict) and "text" in entry:
                extracted_lore.append(str(entry["text"]).strip())
            elif isinstance(entry, str):
                extracted_lore.append(entry.strip())

    if extracted_lore:
        lore_text = " ".join(extracted_lore).strip()
    else:
        lore_text = "We have survived perilous battles and traveled through dangerous lands together."

    companion_name = companion_id.replace("_", " ").title()
    if world_state and isinstance(world_state, dict):
        party_comps = world_state.get("party", {}).get("companions", [])
        for c in party_comps:
            if isinstance(c, dict) and (c.get("id") == companion_id or c.get("name") == companion_id):
                companion_name = c.get("name", companion_name)
                break

    camp_prompt = (
        f"You are the companion {companion_name} resting at camp with the party.\n"
        f"Reflect on these recent events:\n"
        f"{lore_text}\n\n"
        f"Generate a brief campfire reaction or thought from {companion_name}, along with 3 player response options:\n"
        f"- agree: Player agrees with or supports {companion_name}\n"
        f"- disagree: Player challenges or disagrees with {companion_name}\n"
        f"- neutral: Player gives a calm or practical non-committal response\n\n"
        f"Respond strictly in JSON format with schema:\n"
        f"{{\n"
        f'  "statement": "<companion speech at camp reflecting on the events>",\n'
        f'  "topic": "<short summary of discussion topic>",\n'
        f'  "options": {{\n'
        f'    "agree": "<player agreement response>",\n'
        f'    "disagree": "<player disagreement response>",\n'
        f'    "neutral": "<player neutral response>"\n'
        f'  }}\n'
        f"}}"
    )

    messages = [{"role": "user", "content": camp_prompt}]
    options = {"temperature": 0.7, "num_ctx": num_ctx}

    effective_model = llm_handler.resolve_model(model, client=client)
    effective_fallback_model = llm_handler.resolve_model(ollama_model, client=client) if ollama_model else llm_handler.resolve_model(None, client=client)
    parsed_json = None

    if client is not None:
        try:
            resp = client.chat(model=effective_model, messages=messages, format="json", options=options)
            content = resp.get("message", {}).get("content", "")
            parsed_json = json.loads(content)
        except Exception as ex:
            logger.warning(f"generate_camp_dialogue: client chat failed: {ex}")
            try:
                m = re.search(r"\{[\s\S]*\}", content)
                if m:
                    parsed_json = json.loads(m.group(0))
            except Exception:
                parsed_json = None
    elif use_live_llm is False:
        parsed_json = None
    else:
        engine = llm_handler.get_active_engine()
        if engine == "groq" and llm_handler.get_groq_api_key():
            groq_cfg = llm_handler.load_groq_config()
            cached_models = llm_handler._groq_models_cache.get("models", [])
            if model and (model in cached_models or model in llm_handler.GROQ_AVAILABLE_MODELS):
                groq_m = model
            else:
                groq_m = groq_cfg.get("model") or llm_handler.DEFAULT_GROQ_MODEL
            g_text, g_metrics = llm_handler.call_groq_api(
                messages,
                model=groq_m,
                temperature=0.7,
                response_json=True,
                api_key=llm_handler.get_groq_api_key(),
            )
            if g_text:
                try:
                    parsed_json = json.loads(g_text)
                except Exception:
                    m = re.search(r"\{[\s\S]*\}", g_text)
                    if m:
                        try:
                            parsed_json = json.loads(m.group(0))
                        except Exception:
                            parsed_json = None
            if parsed_json is None:
                try:
                    import ollama
                    resp = ollama.chat(model=effective_fallback_model, messages=messages, format="json", options=options)
                    content = resp.get("message", {}).get("content", "")
                    parsed_json = json.loads(content)
                except Exception as ex:
                    logger.debug(f"generate_camp_dialogue: ollama fallback failed: {ex}")
                    parsed_json = None

        elif engine == "gemini" and llm_handler.get_gemini_api_key():
            cfg = llm_handler.load_gemini_config()
            gemini_m = model if (model and "gemini" in model) else (cfg.get("model") or llm_handler.DEFAULT_GEMINI_MODEL)
            gemini_messages = [{"role": "user", "parts": [{"text": camp_prompt}]}]
            g_text, g_metrics = llm_handler.call_gemini_api(
                gemini_messages,
                model=gemini_m,
                temperature=0.7,
                response_mime_type="application/json",
                api_key=llm_handler.get_gemini_api_key(),
            )
            if g_text:
                try:
                    parsed_json = json.loads(g_text)
                except Exception:
                    m = re.search(r"\{[\s\S]*\}", g_text)
                    if m:
                        try:
                            parsed_json = json.loads(m.group(0))
                        except Exception:
                            parsed_json = None
            if parsed_json is None:
                try:
                    import ollama
                    resp = ollama.chat(model=effective_fallback_model, messages=messages, format="json", options=options)
                    content = resp.get("message", {}).get("content", "")
                    parsed_json = json.loads(content)
                except Exception as ex:
                    logger.debug(f"generate_camp_dialogue: ollama fallback failed: {ex}")
                    parsed_json = None

        else:
            try:
                import ollama
                ollama_target = effective_fallback_model if (ollama_model or model in llm_handler.KNOWN_SHUTDOWN_MODELS or "gemini" in model or "qwen/" in model) else llm_handler.resolve_model(model)
                resp = ollama.chat(model=ollama_target, messages=messages, format="json", options=options)
                content = resp.get("message", {}).get("content", "")
                parsed_json = json.loads(content)
            except Exception as ex:
                logger.debug(f"generate_camp_dialogue: ollama call failed: {ex}")
                parsed_json = None

    if isinstance(parsed_json, dict) and "statement" in parsed_json:
        statement = str(parsed_json.get("statement", "")).strip() or f"Resting by the fire brings to mind: {lore_text}"
        topic = str(parsed_json.get("topic", "")).strip() or (lore_text[:50] + "..." if len(lore_text) > 50 else lore_text)
        opts = parsed_json.get("options")
        if not isinstance(opts, dict):
            opts = {}
        agree_opt = str(opts.get("agree", "")).strip() or "I agree with you. We made the right call."
        disagree_opt = str(opts.get("disagree", "")).strip() or "I disagree. We should have approached that differently."
        neutral_opt = str(opts.get("neutral", "")).strip() or "What's done is done. We must look forward."

        return {
            "companion_id": companion_id,
            "companion_name": companion_name,
            "statement": statement,
            "topic": topic,
            "options": {
                "agree": agree_opt,
                "disagree": disagree_opt,
                "neutral": neutral_opt,
            },
        }

    statement = (
        f"Sitting by the fire, I keep thinking about what happened: {lore_text}. "
        f"Do you think we handled that the right way?"
    )
    topic = lore_text[:60] + "..." if len(lore_text) > 60 else lore_text
    return {
        "companion_id": companion_id,
        "companion_name": companion_name,
        "statement": statement,
        "topic": topic,
        "options": {
            "agree": "You're right to think on it. I believe we did what was necessary.",
            "disagree": "I think we made a mistake there, and we should be honest about it.",
            "neutral": "We survived it, and that's what counts. We need our rest for tomorrow.",
        },
    }
