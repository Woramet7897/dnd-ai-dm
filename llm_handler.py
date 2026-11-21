"""
llm_handler.py — LLM Orchestration & Multi-Engine Routing Facade.

Modularized into the `llm/` package:
  - llm/providers.py : Cloud (Groq LPU, Google Gemini Flash) and Local (Ollama) provider clients
  - llm/prompts.py   : Tiered system prompt construction (Tiers 0-4), token budget, and single-pass parsing
  - llm/generator.py : Narrative response generation, state update extraction, and camp dialogue

All symbols and module-level runtime globals remain exposed on `llm_handler` for 100% backward compatibility.
"""

import logging
import os
from typing import Any, Dict, Optional

import paths
import validation

logger = logging.getLogger("llm_handler")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setLevel(logging.DEBUG)
    _h.setFormatter(logging.Formatter("[LLM] %(levelname)s: %(message)s"))
    logger.addHandler(_h)

DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "llama3")
DEFAULT_NUM_CTX = 4096
MAX_PROMPT_RATIO = 0.70

GEMINI_CONFIG_PATH = os.path.join(paths.DATA_DIR, "gemini_config.json")
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
KNOWN_SHUTDOWN_MODELS = {
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash-lite-001",
}
GEMINI_COOLDOWN_SECONDS = 600

GROQ_CONFIG_PATH = os.path.join(paths.DATA_DIR, "groq_config.json")
DEFAULT_GROQ_MODEL = "qwen/qwen3.8-27b"
GROQ_AVAILABLE_MODELS = [
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]

# Module-level state for Gemini & Groq quota cooldown and session error tracking
_gemini_cooldown_until: float = 0.0
_gemini_session_disabled: bool = False
_gemini_disabled_reason: Optional[str] = None
_gemini_last_error_type: Optional[str] = None

_groq_cooldown_until: float = 0.0
_groq_session_disabled: bool = False
_groq_disabled_reason: Optional[str] = None
_groq_last_error_type: Optional[str] = None
_groq_models_cache: Dict[str, Any] = {"models": [], "expires_at": 0.0, "key": None}


# ── Re-export from llm/ submodules ────────────────────────────────────────────

from llm.providers import (
    MODEL_NAME_PATTERN,
    _redact,
    _update_gemini_config_fields,
    call_gemini_api,
    call_groq_api,
    get_active_engine,
    get_gemini_api_key,
    get_gemini_daily_calls,
    get_gemini_status,
    get_gemini_user_daily_limit,
    get_groq_api_key,
    get_groq_available_models,
    get_groq_status,
    get_installed_models,
    get_pacific_date_str,
    increment_gemini_daily_calls,
    is_saved_gemini_pair,
    is_saved_groq_pair,
    load_gemini_config,
    load_groq_config,
    reset_gemini_state,
    reset_groq_state,
    resolve_model,
    save_gemini_config,
    save_groq_config,
    set_active_engine,
    set_gemini_user_daily_limit,
    test_gemini_connection,
    test_groq_connection,
)

from llm.prompts import (
    CONDENSED_RULESET,
    DEFAULT_ACTION_SUGGESTIONS,
    EXTRACTION_INSTRUCTION,
    SINGLE_PASS_DIRECTIVE,
    SUGGESTIONS_DIRECTIVE,
    assemble_system_prompt,
    build_system_prompt_tiers,
    count_tokens,
    enforce_context_budget,
    extract_and_strip_single_pass,
    extract_and_strip_suggestions,
    format_llm_history,
    is_passive_exploration_turn,
)

from llm.generator import (
    extract_state_updates,
    generate_camp_dialogue,
    generate_narrative_response,
    is_combat_active,
)
