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

import datetime
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import zoneinfo

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

# ── Cloud Engine: Google Gemini Flash (Free Tier) ─────────────────────────────
GEMINI_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "data", "gemini_config.json")
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
KNOWN_SHUTDOWN_MODELS = {
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash-lite-001",
}
GEMINI_COOLDOWN_SECONDS = 600  # 10 minutes cooldown on HTTP 429 / RESOURCE_EXHAUSTED

# Module-level state for Gemini quota cooldown and session error tracking
_gemini_cooldown_until: float = 0.0
_gemini_session_disabled: bool = False
_gemini_disabled_reason: Optional[str] = None
_gemini_last_error_type: Optional[str] = None


def _redact(text: Any, key: Optional[str] = None) -> str:
    """Redact API key and any URL query parameter containing a key from text."""
    if text is None:
        return ""
    s = str(text)
    active_key = key or get_gemini_api_key()
    if active_key and str(active_key).strip():
        s = s.replace(str(active_key).strip(), "[REDACTED_API_KEY]")
    # Redact URL query parameter patterns like ?key=... or &key=...
    s = re.sub(r"([?&]key=)[^&\s'\"]+", r"\1[REDACTED_API_KEY]", s)
    return s


def reset_gemini_state() -> None:
    """Reset Gemini runtime cooldown and session disabled state (for testing and UI reset)."""
    global _gemini_cooldown_until, _gemini_session_disabled, _gemini_disabled_reason, _gemini_last_error_type
    _gemini_cooldown_until = 0.0
    _gemini_session_disabled = False
    _gemini_disabled_reason = None
    _gemini_last_error_type = None


def get_pacific_date_str() -> str:
    """Return current date in US Pacific time (YYYY-MM-DD) where quota resets at midnight."""
    try:
        zi = zoneinfo.ZoneInfo("America/Los_Angeles")
        return datetime.datetime.now(zi).strftime("%Y-%m-%d")
    except Exception:
        tz_pacific = datetime.timezone(datetime.timedelta(hours=-7))
        return datetime.datetime.now(tz_pacific).strftime("%Y-%m-%d")


def get_gemini_daily_calls() -> int:
    """Retrieve number of successful Gemini calls today (Pacific time)."""
    cfg = load_gemini_config()
    daily = cfg.get("daily_calls", {})
    today_pt = get_pacific_date_str()
    if daily.get("date") == today_pt:
        return int(daily.get("count", 0))
    return 0


def _update_gemini_config_fields(**fields: Any) -> bool:
    """
    Safely update non-credential metadata fields (e.g. daily_calls, user_daily_limit)
    in data/gemini_config.json without touching api_key, model, or engine.
    - Reads existing file directly.
    - Merges ONLY the given fields, never writes api_key/model/engine.
    - Does nothing (with logger.warning) if existing file is corrupt rather than overwriting it.
    - If file does not exist, creates it with only the given fields.
    """
    protected_fields = {"api_key", "model", "engine"}
    fields_to_write = {k: v for k, v in fields.items() if k not in protected_fields}
    if not fields_to_write:
        return False

    existing_data: Dict[str, Any] = {}
    if os.path.exists(GEMINI_CONFIG_PATH):
        try:
            with open(GEMINI_CONFIG_PATH, "r", encoding="utf-8") as f:
                content = f.read()
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                logger.warning("Gemini config file is not a valid JSON dictionary. Skipping metadata update to preserve file.")
                return False
            existing_data = parsed
        except Exception as ex:
            logger.warning(f"Gemini config file exists but is corrupt/unreadable: {ex}. Skipping metadata update to preserve file.")
            return False

    existing_data.update(fields_to_write)
    try:
        os.makedirs(os.path.dirname(GEMINI_CONFIG_PATH), exist_ok=True)
        with open(GEMINI_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(existing_data, f, indent=2)
        return True
    except Exception as ex:
        logger.warning(f"Failed to update Gemini config fields: {ex}")
        return False


def increment_gemini_daily_calls() -> int:
    """Increment successful Gemini call count for current Pacific date and persist."""
    today_pt = get_pacific_date_str()
    count = get_gemini_daily_calls() + 1
    daily_data = {"date": today_pt, "count": count}
    _update_gemini_config_fields(daily_calls=daily_data)
    return count


def get_gemini_user_daily_limit() -> Optional[int]:
    """Retrieve user-configured daily Gemini call limit if set."""
    cfg = load_gemini_config()
    lim = cfg.get("user_daily_limit")
    if lim is not None and str(lim).isdigit() and int(lim) > 0:
        return int(lim)
    return None


def set_gemini_user_daily_limit(limit: Optional[int]) -> None:
    """Save user-configured daily Gemini call limit."""
    _update_gemini_config_fields(user_daily_limit=limit)


def get_gemini_status() -> Dict[str, Any]:
    """
    Expose current Gemini status for UI and monitoring.
    Returns dict with keys: engine, cooldown_until, cooldown_seconds_remaining,
    session_disabled, disabled_reason, last_error_type, calls_today.
    """
    now = time.time()
    in_cooldown = now < _gemini_cooldown_until
    return {
        "engine": get_active_engine(),
        "cooldown_until": _gemini_cooldown_until if in_cooldown else None,
        "cooldown_seconds_remaining": int(_gemini_cooldown_until - now) if in_cooldown else 0,
        "session_disabled": _gemini_session_disabled,
        "disabled_reason": _gemini_disabled_reason,
        "last_error_type": _gemini_last_error_type,
        "calls_today": get_gemini_daily_calls(),
    }


def load_gemini_config() -> Dict[str, Any]:
    """Load local Gemini API configuration if present, substituting shutdown models."""
    if os.path.exists(GEMINI_CONFIG_PATH):
        try:
            with open(GEMINI_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            saved_model = cfg.get("model")
            if saved_model in KNOWN_SHUTDOWN_MODELS:
                logger.warning(
                    f"Configured model '{saved_model}' is shut down. "
                    f"Substituting default '{DEFAULT_GEMINI_MODEL}'."
                )
                cfg["model"] = DEFAULT_GEMINI_MODEL
            return cfg
        except Exception:
            return {}
    return {}


MODEL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.\-]*$")


def save_gemini_config(
    api_key: str,
    model: str = DEFAULT_GEMINI_MODEL,
    engine: str = "gemini",
    reset_state: bool = True,
    **kwargs: Any,
) -> bool:
    """Save Gemini API configuration locally in data/gemini_config.json."""
    clean_model = model.strip() if model else DEFAULT_GEMINI_MODEL
    if not MODEL_NAME_PATTERN.match(clean_model):
        logger.warning(
            f"Model name '{clean_model}' does not match allowed pattern. "
            "Configuration was not saved."
        )
        return False
    try:
        if reset_state:
            reset_gemini_state()
        os.makedirs(os.path.dirname(GEMINI_CONFIG_PATH), exist_ok=True)
        existing = {}
        if os.path.exists(GEMINI_CONFIG_PATH):
            try:
                with open(GEMINI_CONFIG_PATH, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                existing = {}
        existing["api_key"] = api_key.strip()
        existing["model"] = clean_model
        existing["engine"] = engine.strip()
        for k, v in kwargs.items():
            existing[k] = v
        with open(GEMINI_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
        return True
    except Exception as ex:
        logger.error(f"Failed to save Gemini config: {ex}")
        return False


def get_gemini_api_key() -> Optional[str]:
    """Retrieve Gemini API Key from environment or local config file."""
    env_k = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if env_k and env_k.strip():
        return env_k.strip()
    cfg = load_gemini_config()
    cfg_k = cfg.get("api_key")
    if cfg_k and str(cfg_k).strip():
        return str(cfg_k).strip()
    return None


def get_active_engine() -> str:
    """
    Return active LLM engine: 'gemini' or 'ollama'.
    Falls back to 'ollama' if cooldown is active or Gemini is disabled for the session.
    """
    global _gemini_cooldown_until, _gemini_session_disabled
    now = time.time()
    if _gemini_session_disabled or now < _gemini_cooldown_until:
        return "ollama"

    env_e = os.environ.get("LLM_ENGINE")
    if env_e:
        return env_e.lower().strip()
    cfg = load_gemini_config()
    if cfg.get("engine") == "gemini" and get_gemini_api_key():
        return "gemini"
    return "ollama"


def call_gemini_api(
    contents: List[Dict[str, Any]],
    system_instruction: Optional[str] = None,
    temperature: float = 0.7,
    response_mime_type: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    bypass_state: bool = False,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """
    Call Google Gemini REST API directly using requests.
    Zero heavy SDK dependencies, works across all Python versions.
    Classifies error kinds: 'quota', 'auth', 'not_found', 'timeout', 'network', 'other'.
    When bypass_state=True (used by test_gemini_connection):
      - Skips session_disabled and cooldown short-circuits.
      - Does not increment daily call counter.
      - Free of side-effects on live session globals (_gemini_session_disabled,
        _gemini_cooldown_until, _gemini_disabled_reason, _gemini_last_error_type).
      - Logs test failures at WARNING level instead of ERROR.
    """
    global _gemini_cooldown_until, _gemini_session_disabled, _gemini_disabled_reason, _gemini_last_error_type
    import requests

    now = time.time()
    if not bypass_state:
        if _gemini_session_disabled:
            return None, {
                "error": _redact(f"Gemini is disabled for this session: {_gemini_disabled_reason}", api_key),
                "error_type": _gemini_last_error_type or "other",
                "elapsed_seconds": 0.0,
            }
        if now < _gemini_cooldown_until:
            rem = int(_gemini_cooldown_until - now)
            return None, {
                "error": _redact(f"Gemini quota cooldown active ({rem}s remaining)", api_key),
                "error_type": "quota",
                "elapsed_seconds": 0.0,
            }

    key = api_key or get_gemini_api_key()
    if not key:
        if not bypass_state:
            _gemini_last_error_type = "auth"
        return None, {"error": "Missing Gemini API Key", "error_type": "auth", "elapsed_seconds": 0.0}

    cfg = load_gemini_config()
    chosen_model = model or cfg.get("model") or DEFAULT_GEMINI_MODEL
    if chosen_model in KNOWN_SHUTDOWN_MODELS:
        logger.warning(
            _redact(
                f"Requested model '{chosen_model}' is shut down. "
                f"Substituting default '{DEFAULT_GEMINI_MODEL}'.",
                key,
            )
        )
        chosen_model = DEFAULT_GEMINI_MODEL
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{chosen_model}:generateContent"

    payload: Dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": 4096,
        }
    }
    if system_instruction:
        payload["systemInstruction"] = {
            "parts": [{"text": system_instruction}]
        }
    if response_mime_type:
        payload["generationConfig"]["responseMimeType"] = response_mime_type

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": key,
    }

    start_t = time.time()
    try:
        resp = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=25
        )
        elapsed = time.time() - start_t
        if resp.status_code != 200:
            resp_text = resp.text or ""
            resp_text = _redact(resp_text, key)
            truncated_body = resp_text[:300] + "..." if len(resp_text) > 300 else resp_text
            err_msg = _redact(f"HTTP {resp.status_code}: {truncated_body}", key)

            if resp.status_code == 429 or "RESOURCE_EXHAUSTED" in resp_text:
                error_type = "quota"
            elif resp.status_code in (401, 403) or "PERMISSION_DENIED" in resp_text or "API_KEY_INVALID" in resp_text:
                error_type = "auth"
            elif resp.status_code == 404:
                error_type = "not_found"
            else:
                error_type = "other"

            if bypass_state:
                logger.warning(
                    _redact(
                        f"Gemini connection test failed ({error_type}, HTTP {resp.status_code}): {truncated_body}",
                        key,
                    )
                )
            else:
                if error_type == "quota":
                    _gemini_cooldown_until = time.time() + GEMINI_COOLDOWN_SECONDS
                    _gemini_last_error_type = "quota"
                    logger.warning(
                        _redact(
                            f"Gemini quota exhausted (HTTP {resp.status_code}). "
                            f"Cooldown activated for {GEMINI_COOLDOWN_SECONDS}s. Falling back to Ollama.",
                            key,
                        )
                    )
                elif error_type == "auth":
                    _gemini_session_disabled = True
                    _gemini_disabled_reason = "Authentication failed (Invalid API key)"
                    _gemini_last_error_type = "auth"
                    logger.error(
                        _redact(
                            f"Gemini authentication failed (HTTP {resp.status_code}): {truncated_body}. "
                            "Gemini has been disabled for this session. Please check your API key at https://aistudio.google.com/app/apikey",
                            key,
                        )
                    )
                elif error_type == "not_found":
                    _gemini_session_disabled = True
                    _gemini_disabled_reason = (
                        f"Model '{chosen_model}' not found or project lacks access (HTTP 404). "
                        "Choose another model and press 'ทดสอบ'."
                    )
                    _gemini_last_error_type = "not_found"
                    logger.error(
                        _redact(
                            f"Gemini model '{chosen_model}' not found (HTTP 404). "
                            "Model may be shut down or the API key's project may not have access to this model. "
                            "Gemini has been disabled for this session. Please choose an active model in AI settings and press 'ทดสอบ' (Test) to reactivate.",
                            key,
                        )
                    )
                else:
                    _gemini_last_error_type = "other"
                    logger.error(_redact(f"Gemini API returned error: {err_msg}", key))

            return None, {"error": _redact(err_msg, key), "error_type": error_type, "elapsed_seconds": elapsed}

        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            if bypass_state:
                logger.warning("Gemini connection test failed: No candidates returned by Gemini")
            else:
                _gemini_last_error_type = "other"
            return None, {"error": "No candidates returned by Gemini", "error_type": "other", "elapsed_seconds": elapsed}

        first_cand = candidates[0]
        text = first_cand.get("content", {}).get("parts", [{}])[0].get("text", "")
        usage = data.get("usageMetadata", {})
        calls_today = get_gemini_daily_calls() if bypass_state else increment_gemini_daily_calls()
        metrics = {
            "eval_count": usage.get("candidatesTokenCount", 0),
            "prompt_eval_count": usage.get("promptTokenCount", 0),
            "eval_duration": 0,
            "total_duration": 0,
            "elapsed_seconds": elapsed,
            "engine": "gemini",
            "model": chosen_model,
            "calls_today": calls_today,
        }
        return text, metrics
    except Exception as ex:
        elapsed = time.time() - start_t
        redacted_ex = _redact(str(ex), key)
        if isinstance(ex, requests.Timeout):
            error_type = "timeout"
        elif isinstance(ex, (requests.ConnectionError, requests.RequestException)):
            error_type = "network"
        else:
            error_type = "other"

        if bypass_state:
            logger.warning(
                _redact(
                    f"Gemini connection test failed ({error_type}): {redacted_ex}",
                    key,
                )
            )
        else:
            _gemini_last_error_type = error_type
            if error_type == "timeout":
                logger.warning(
                    _redact(
                        f"Gemini API call timed out after {elapsed:.2f}s: {redacted_ex}. "
                        "Falling back to Ollama without cooldown.",
                        key,
                    )
                )
            elif error_type == "network":
                logger.error(
                    _redact(
                        f"Gemini API network error after {elapsed:.2f}s: {redacted_ex}. "
                        "Falling back to Ollama.",
                        key,
                    )
                )
            else:
                logger.error(_redact(f"Gemini API call failed after {elapsed:.2f}s: {redacted_ex}", key))

        return None, {"error": redacted_ex, "error_type": error_type, "elapsed_seconds": elapsed}


def is_saved_gemini_pair(api_key: Optional[str], model: Optional[str]) -> bool:
    """
    Check if the given (api_key, model) matches the currently configured / active pair.
    Uses get_gemini_api_key() (which resolves env vars first, then saved config)
    and load_gemini_config()["model"] (falling back to DEFAULT_GEMINI_MODEL).
    """
    cfg = load_gemini_config()
    configured_key = (get_gemini_api_key() or "").strip()
    configured_model = (cfg.get("model") or DEFAULT_GEMINI_MODEL).strip()

    test_k = (api_key or "").strip()
    test_m = (model or DEFAULT_GEMINI_MODEL).strip()
    return (test_k == configured_key) and (test_m == configured_model)


def test_gemini_connection(api_key: str, model: str = DEFAULT_GEMINI_MODEL) -> Tuple[bool, str]:
    """
    Test Gemini API connectivity with a simple ping prompt.
    Bypasses cooldown/disabled checks so users can test and recover after fixing model or key.
    If connectivity succeeds, resets cooldown/disabled state ONLY IF the tested (api_key, model)
    pair equals the currently configured pair OR the session was already disabled/in cooldown.
    """
    res, metrics = call_gemini_api(
        contents=[{"role": "user", "parts": [{"text": "Reply with 'OK' only."}]}],
        api_key=api_key,
        model=model,
        temperature=0.1,
        bypass_state=True,
    )
    if res is not None:
        now = time.time()
        was_impaired = _gemini_session_disabled or (now < _gemini_cooldown_until)
        is_same_pair = is_saved_gemini_pair(api_key, model)

        if was_impaired or is_same_pair:
            reset_gemini_state()

        elapsed = metrics.get("elapsed_seconds", 0)
        return True, f"เชื่อมต่อสำเร็จใน {elapsed:.2f}s! ({model})"
    return False, metrics.get("error", "Unknown error")


def get_installed_models(client: Optional[Any] = None) -> List[str]:
    """Retrieve list of locally installed model names from Ollama."""
    try:
        if client is not None:
            if hasattr(client, "list") and callable(client.list):
                res = client.list()
            else:
                return []
        else:
            import ollama
            res = ollama.list()

        models = []
        raw_list = getattr(res, "models", None)
        if raw_list is None and isinstance(res, dict):
            raw_list = res.get("models", [])
        if raw_list:
            for m in raw_list:
                name = getattr(m, "model", None) or getattr(m, "name", None)
                if not name and isinstance(m, dict):
                    name = m.get("model") or m.get("name")
                if name:
                    models.append(str(name))
        return models
    except Exception:
        return []


def resolve_model(model: Optional[str] = None, client: Optional[Any] = None) -> str:
    """
    Resolve model to use:
    - If client is provided (unit tests/mock), preserve specified model or DEFAULT_MODEL.
    - If model is explicitly specified and not None/empty and not DEFAULT_MODEL, use it.
    - If OLLAMA_MODEL is set in os.environ, use it.
    - Otherwise, query installed Ollama models:
      - If 'llama3' is installed, use 'llama3'.
      - Else if any typhoon model is installed (e.g. 'scb10x/llama3.1-typhoon2-8b-instruct:latest'), use it.
      - Else if any other models are installed, use the first installed model.
    - Fall back to DEFAULT_MODEL ("llama3").
    """
    if client is not None:
        return model if model else DEFAULT_MODEL

    env_m = os.environ.get("OLLAMA_MODEL")
    if env_m:
        return env_m

    if model and model != "llama3" and model != DEFAULT_MODEL:
        return model

    installed = get_installed_models()
    if installed:
        if "llama3" in installed or "llama3:latest" in installed:
            return "llama3"
        # Prioritize Typhoon 2 for rich Thai storytelling quality
        typhoon_candidates = [m for m in installed if "typhoon2-8b" in m or "typhoon" in m]
        if typhoon_candidates:
            return typhoon_candidates[0]
        # Look for qwen models next for fast inference
        qwen_candidates = [m for m in installed if "qwen" in m.lower()]
        if qwen_candidates:
            return qwen_candidates[0]
        return installed[0]

    return model if model else DEFAULT_MODEL

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
        f"Background / Motivation: {bg_hook}\n"
        f"DM Role & Narration Guidelines (MANDATORY):\n"
        f"1. You are the Dungeon Master (DM) narrating an epic fantasy adventure entirely in Thai (ภาษาไทย).\n"
        f"2. Always write rich, immersive, multi-paragraph responses (2-3 detailed paragraphs). Describe sensory details (sights, sounds, smells, lighting, weather), NPC personalities/dialogue, and local lore.\n"
        f"3. NEVER reply with only 1 short sentence or dry summary (e.g. 'ที่นี่มีโรงเตี๊ยมอยู่ครับ'). Make the world feel alive and atmospheric.\n"
        f"4. In non-combat exploration scenes, always conclude your narration by presenting clear points of interest, sensory clues, or leading questions to guide the player on where they can go or what they can interact with next. Never leave the player stranded without directions."
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
# ACTION SUGGESTIONS CONFIG & HELPERS (Sub-phase 14.1 / Module D)
# ════════════════════════════════════════════════════════════════════════════════

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


def extract_and_strip_suggestions(raw_text: str) -> Tuple[str, List[str]]:
    """
    Extract trailing action suggestions JSON block and strip all JSON leakage from narrative.
    Guarantees exactly 3 suggestions (truncates if > 3, pads with defaults if < 3).
    Falls back cleanly to DEFAULT_ACTION_SUGGESTIONS if missing or malformed.
    """
    suggestions: List[str] = []

    # 1. Search for fenced or raw JSON block containing "suggestions"
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

    # 2. Guarantee exactly 3 suggestions (truncate / pad with defaults)
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

    # 3. Strip any JSON block leakage from narrative text (both fenced blocks and raw suggestions JSON)
    clean_narrative = raw_text
    clean_narrative = re.sub(r"```json\s*[\s\S]*?```", "", clean_narrative, flags=re.DOTALL).strip()
    clean_narrative = re.sub(r"```\s*\{[\s\S]*?\}\s*```", "", clean_narrative, flags=re.DOTALL).strip()
    raw_sug_pattern = r"\{[\s\r\n]*\"suggestions\"[\s\S]*?\}"
    clean_narrative = re.sub(raw_sug_pattern, "", clean_narrative, flags=re.DOTALL | re.IGNORECASE).strip()
    # Also strip unclosed or truncated trailing JSON / markdown fences at the end of narrative
    clean_narrative = re.sub(r"(?:```(?:json)?\s*)?\{[\s\r\n]*\"suggestions\"[\s\S]*$", "", clean_narrative, flags=re.DOTALL | re.IGNORECASE).strip()
    clean_narrative = re.sub(r"```(?:json)?\s*$", "", clean_narrative, flags=re.DOTALL).strip()

    return clean_narrative, cleaned_suggestions


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
    include_suggestions: bool = True,
    model: str = DEFAULT_MODEL,
    num_ctx: int = DEFAULT_NUM_CTX,
    client: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Generate narrative response from Ollama.
    Streams/returns plain text narrative with NO JSON leakage.
    Appends trailing action suggestions block instruction and extracts suggestions.

    Args:
      user_input: action text typed by the player.
      player_state: current character save dict.
      world_state: current world save dict.
      history: conversation history list.
      lore_entries: RAG lore results from memory_manager.
      companions_present: active companions in current room.
      roll_result: system roll injection block (e.g. '[System: Roll Result] ...').
      round_result: system combat round narration block (e.g. '[System: Round Result] ...').
      include_suggestions: whether to request action suggestions (default True).
      model: Ollama model name (default llama3).
      num_ctx: explicit context window size.
      client: optional mock/custom Ollama client for unit tests.

    Returns:
      Dict with 'narrative', 'metrics', 'token_costs', 'dropped_tiers', 'suggestions'.
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
    if include_suggestions:
        user_content += SUGGESTIONS_DIRECTIVE

    # Build Ollama message payload: system prompt + 6-turn history window + current user turn
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(format_llm_history(history, max_turns=6))
    messages.append({"role": "user", "content": user_content})

    options = {"num_ctx": num_ctx, "temperature": 0.7}

    effective_model = resolve_model(model, client=client)
    logger.debug(f"Calling Ollama narrative model='{effective_model}', num_ctx={num_ctx}, msg_count={len(messages)}.")
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
        elif get_active_engine() == "gemini" and get_gemini_api_key():
            gemini_contents = []
            for h in format_llm_history(history, max_turns=12):
                g_role = "model" if h.get("role") == "assistant" else "user"
                gemini_contents.append({"role": g_role, "parts": [{"text": h.get("content", "")}]})
            gemini_contents.append({"role": "user", "parts": [{"text": user_content}]})

            g_text, g_metrics = call_gemini_api(
                contents=gemini_contents,
                system_instruction=system_prompt,
                temperature=0.7,
            )
            if g_text is not None:
                raw_text = g_text
                metrics = g_metrics
            else:
                logger.warning("Gemini API call failed or timed out. Falling back to local Ollama...")
                import ollama  # type: ignore
                response = ollama.chat(model=effective_model, messages=messages, options=options)
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
            response = ollama.chat(model=effective_model, messages=messages, options=options)
            raw_text = response.get("message", {}).get("content", "")
            metrics = {
                "eval_count": response.get("eval_count", 0),
                "prompt_eval_count": response.get("prompt_eval_count", 0),
                "eval_duration": response.get("eval_duration", 0),
                "total_duration": response.get("total_duration", 0),
                "elapsed_seconds": time.time() - start_t,
            }

        elapsed = time.time() - start_t
        clean_narrative, suggestions = extract_and_strip_suggestions(raw_text)

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
        }

    except Exception as e:
        elapsed = time.time() - start_t
        logger.error(f"Narrative Ollama call failed after {elapsed:.2f}s: {e}")
        return {
            "narrative": f"The Dungeon Master pauses for a moment, pondering the turn... (Ollama error: {e})",
            "metrics": {"eval_count": 0, "prompt_eval_count": 0, "eval_duration": 0, "elapsed_seconds": elapsed},
            "token_costs": tier_costs,
            "dropped_tiers": dropped_logs,
            "suggestions": list(DEFAULT_ACTION_SUGGESTIONS),
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

    effective_model = resolve_model(model, client=client)
    logger.debug(f"Calling Ollama extraction model='{effective_model}', num_ctx={num_ctx}.")
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
            elif not force_ollama and get_active_engine() == "gemini" and get_gemini_api_key():
                g_prompt = msgs[-1].get("content", "")
                g_text, g_metrics = call_gemini_api(
                    contents=[{"role": "user", "parts": [{"text": g_prompt}]}],
                    temperature=0.1,
                    response_mime_type="application/json",
                )
                m = g_metrics or {}
                if g_text is not None:
                    content = g_text
                else:
                    import ollama  # type: ignore
                    resp = ollama.chat(model=effective_model, messages=msgs, format="json", options=options)
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
                resp = ollama.chat(model=effective_model, messages=msgs, format="json", options=options)
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

    # Attempt 1
    raw_dict, metrics1 = do_call(messages)

    # Retry Policy (Spec 13b & Step 3 Hardening):
    # Stop double-billing quota on retries.
    # When Gemini is used, response_mime_type="application/json" guarantees structured JSON output
    # directly from the Gemini API, making malformed JSON syntax exceedingly rare.
    # If attempt 1 was served by Gemini and still failed to parse valid JSON, calling Gemini a second time
    # would consume another cloud API request in the same turn (up to 3 calls: narrative + 2 extraction).
    # To protect the user's daily Gemini free-tier quota from double-billing, we route the retry
    # to local Ollama (force_ollama=True) or fall back to an empty update {} if Ollama is unreachable.
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

    # Validate raw output through validation.py's validate_extraction_output()
    cleaned = validation.validate_extraction_output(raw_dict, world_state=world_state)
    return cleaned


# ════════════════════════════════════════════════════════════════════════════════
# CAMP COMPANION DIALOGUE (Sub-phase 14.2 / Module D §2)
# ════════════════════════════════════════════════════════════════════════════════

def generate_camp_dialogue(
    companion_id: str,
    memory_manager: Any,
    player_state: Dict[str, Any],
    world_state: Optional[Dict[str, Any]] = None,
    client: Optional[Any] = None,
    model: str = DEFAULT_MODEL,
    num_ctx: int = DEFAULT_NUM_CTX,
) -> Optional[Dict[str, Any]]:
    """
    Spec Module D §2 / Phase 14.2:
    Generate camp companion dialogue reacting to recent notable events.
    - Context Gating: ONLY triggers in camp/rest context. Returns None if mid-combat
      or mid-dungeon-crawl.
    - Retrieval: Pulls recent get_relevant_lore() results from memory_manager.
    - Prompting: Mock/real LLM prompt built from real get_relevant_lore() output.
    - Returns companion reaction with 3 options: agree (+5), disagree (-5), neutral (0).
    """
    # 1. Context Gating: mid-combat or mid-dungeon-crawl must NEVER trigger camp dialogue
    if world_state is not None:
        import dungeon_manager
        if not dungeon_manager.is_camp_context(world_state):
            logger.debug(
                f"generate_camp_dialogue: not in camp/rest context "
                f"(combat={bool(world_state.get('combat_state'))}, loc={world_state.get('current_location')}). Returning None."
            )
            return None

    # 2. Pull recent get_relevant_lore() results (existing Phase 5 function)
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

    # Determine companion display name
    companion_name = companion_id.replace("_", " ").title()
    if world_state and isinstance(world_state, dict):
        party_comps = world_state.get("party", {}).get("companions", [])
        for c in party_comps:
            if isinstance(c, dict) and (c.get("id") == companion_id or c.get("name") == companion_id):
                companion_name = c.get("name", companion_name)
                break

    # 3. Build prompt with real lore text
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
        f"  }}\n"
        f"}}"
    )

    messages = [{"role": "user", "content": camp_prompt}]
    options = {"temperature": 0.7, "num_ctx": num_ctx}

    effective_model = resolve_model(model, client=client)
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

    if parsed_json is None and client is None and os.environ.get("OLLAMA_HOST"):
        try:
            import ollama
            resp = ollama.chat(model=effective_model, messages=messages, format="json", options=options)
            content = resp.get("message", {}).get("content", "")
            parsed_json = json.loads(content)
        except Exception as ex:
            logger.debug(f"generate_camp_dialogue: ollama call failed: {ex}")
            parsed_json = None

    # 4. Parse response or apply lore-grounded fallback
    if isinstance(parsed_json, dict) and "statement" in parsed_json:
        statement = parsed_json.get("statement", f"Resting by the fire brings to mind: {lore_text}")
        topic = parsed_json.get("topic") or (lore_text[:50] + "..." if len(lore_text) > 50 else lore_text)
        opts = parsed_json.get("options", {})
        agree_opt = opts.get("agree", "I agree with you. We made the right call.")
        disagree_opt = opts.get("disagree", "I disagree. We should have approached that differently.")
        neutral_opt = opts.get("neutral", "What's done is done. We must look forward.")

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

    # Robust lore-grounded fallback
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



