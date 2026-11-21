"""
llm/providers.py — Cloud (Groq LPU, Google Gemini Flash) and Local (Ollama) LLM provider clients,
credential redaction, rate-limit cooldown management, and model resolution.
"""

import datetime
import json
import logging
import os
import re
import time
import zoneinfo
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("llm_handler")

MODEL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.\-]*$")


def _redact(text: Any, key: Optional[str] = None) -> str:
    """Redact Gemini and Groq API keys and URL query parameters containing keys."""
    if text is None:
        return ""
    import llm_handler
    s = str(text)
    keys_to_redact = set()
    if key and str(key).strip():
        keys_to_redact.add(str(key).strip())
    gem_key = llm_handler.get_gemini_api_key()
    if gem_key and str(gem_key).strip():
        keys_to_redact.add(str(gem_key).strip())
    groq_key = llm_handler.get_groq_api_key()
    if groq_key and str(groq_key).strip():
        keys_to_redact.add(str(groq_key).strip())

    for k in keys_to_redact:
        s = s.replace(k, "[REDACTED_API_KEY]")

    s = re.sub(r"([?&]key=)[^&\s'\"]+", r"\1[REDACTED_API_KEY]", s)
    s = re.sub(r"(Bearer\s+)[^&\s'\"]+", r"\1[REDACTED_API_KEY]", s)
    return s


def reset_gemini_state() -> None:
    """Reset Gemini runtime cooldown and session disabled state (for testing and UI reset)."""
    import llm_handler
    llm_handler._gemini_cooldown_until = 0.0
    llm_handler._gemini_session_disabled = False
    llm_handler._gemini_disabled_reason = None
    llm_handler._gemini_last_error_type = None


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
    import llm_handler
    cfg = llm_handler.load_gemini_config()
    daily = cfg.get("daily_calls", {})
    today_pt = llm_handler.get_pacific_date_str()
    if daily.get("date") == today_pt:
        return int(daily.get("count", 0))
    return 0


def _update_gemini_config_fields(**fields: Any) -> bool:
    """
    Safely update non-credential metadata fields (e.g. daily_calls, user_daily_limit)
    in data/gemini_config.json without touching api_key, model, or engine.
    """
    import llm_handler
    protected_fields = {"api_key", "model", "engine"}
    fields_to_write = {k: v for k, v in fields.items() if k not in protected_fields}
    if not fields_to_write:
        return False

    cfg_path = llm_handler.GEMINI_CONFIG_PATH
    existing_data: Dict[str, Any] = {}
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
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
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(existing_data, f, indent=2)
        return True
    except Exception as ex:
        logger.warning(f"Failed to update Gemini config fields: {ex}")
        return False


def increment_gemini_daily_calls() -> int:
    """Increment successful Gemini call count for current Pacific date and persist."""
    import llm_handler
    today_pt = llm_handler.get_pacific_date_str()
    count = llm_handler.get_gemini_daily_calls() + 1
    daily_data = {"date": today_pt, "count": count}
    _update_gemini_config_fields(daily_calls=daily_data)
    return count


def get_gemini_user_daily_limit() -> Optional[int]:
    """Retrieve user-configured daily Gemini call limit if set."""
    import llm_handler
    cfg = llm_handler.load_gemini_config()
    lim = cfg.get("user_daily_limit")
    if lim is not None and str(lim).isdigit() and int(lim) > 0:
        return int(lim)
    return None


def set_gemini_user_daily_limit(limit: Optional[int]) -> None:
    """Save user-configured daily Gemini call limit."""
    _update_gemini_config_fields(user_daily_limit=limit)


def get_gemini_status() -> Dict[str, Any]:
    """Expose current Gemini status for UI and monitoring."""
    import llm_handler
    now = time.time()
    in_cooldown = now < llm_handler._gemini_cooldown_until
    return {
        "engine": llm_handler.get_active_engine(),
        "cooldown_until": llm_handler._gemini_cooldown_until if in_cooldown else None,
        "cooldown_seconds_remaining": int(llm_handler._gemini_cooldown_until - now) if in_cooldown else 0,
        "session_disabled": llm_handler._gemini_session_disabled,
        "disabled_reason": llm_handler._gemini_disabled_reason,
        "last_error_type": llm_handler._gemini_last_error_type,
        "calls_today": llm_handler.get_gemini_daily_calls(),
    }


def load_gemini_config() -> Dict[str, Any]:
    """Load local Gemini API configuration if present, substituting shutdown models."""
    import llm_handler
    cfg_path = llm_handler.GEMINI_CONFIG_PATH
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            saved_model = cfg.get("model")
            if saved_model in llm_handler.KNOWN_SHUTDOWN_MODELS:
                logger.warning(
                    f"Configured model '{saved_model}' is shut down. "
                    f"Substituting default '{llm_handler.DEFAULT_GEMINI_MODEL}'."
                )
                cfg["model"] = llm_handler.DEFAULT_GEMINI_MODEL
            return cfg
        except Exception:
            return {}
    return {}


def save_gemini_config(
    api_key: str,
    model: str = "gemini-3.6-flash",
    engine: str = "gemini",
    reset_state: bool = True,
    **kwargs: Any,
) -> bool:
    """Save Gemini API configuration locally in data/gemini_config.json."""
    import llm_handler
    clean_model = model.strip() if model else llm_handler.DEFAULT_GEMINI_MODEL
    if not MODEL_NAME_PATTERN.match(clean_model):
        logger.warning(
            f"Model name '{clean_model}' does not match allowed pattern. "
            "Configuration was not saved."
        )
        return False
    cfg_path = llm_handler.GEMINI_CONFIG_PATH
    try:
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        existing = {}
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                existing = {}
        existing["api_key"] = api_key.strip()
        existing["model"] = clean_model
        existing["engine"] = engine.strip()
        for k, v in kwargs.items():
            existing[k] = v
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
        if reset_state:
            reset_gemini_state()
        return True
    except Exception as ex:
        logger.error(f"Failed to save Gemini config: {ex}")
        return False


def get_gemini_api_key() -> Optional[str]:
    """Retrieve Gemini API Key from environment or local config file."""
    import llm_handler
    env_k = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if env_k and env_k.strip():
        return env_k.strip()
    cfg = llm_handler.load_gemini_config()
    cfg_k = cfg.get("api_key")
    if cfg_k and str(cfg_k).strip():
        return str(cfg_k).strip()
    return None


def load_groq_config() -> Dict[str, Any]:
    """Load Groq API configuration from data/groq_config.json."""
    import llm_handler
    cfg_path = llm_handler.GROQ_CONFIG_PATH
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            return {}
    return {}


def reset_groq_state() -> None:
    """Reset Groq runtime cooldown and session disabled state (for testing and UI reset)."""
    import llm_handler
    llm_handler._groq_cooldown_until = 0.0
    llm_handler._groq_session_disabled = False
    llm_handler._groq_disabled_reason = None
    llm_handler._groq_last_error_type = None


def get_groq_status() -> Dict[str, Any]:
    """Expose current Groq status for UI and monitoring."""
    import llm_handler
    now = time.time()
    in_cooldown = now < llm_handler._groq_cooldown_until
    return {
        "engine": llm_handler.get_active_engine(),
        "cooldown_until": llm_handler._groq_cooldown_until if in_cooldown else None,
        "cooldown_seconds_remaining": int(llm_handler._groq_cooldown_until - now) if in_cooldown else 0,
        "session_disabled": llm_handler._groq_session_disabled,
        "disabled_reason": llm_handler._groq_disabled_reason,
        "last_error_type": llm_handler._groq_last_error_type,
    }


def set_active_engine(engine: str) -> None:
    """Safely switch active engine by updating ONLY the 'engine' field in config files."""
    import llm_handler
    clean_eng = str(engine).lower().strip()

    try:
        grq_data: Dict[str, Any] = {}
        if os.path.exists(llm_handler.GROQ_CONFIG_PATH):
            with open(llm_handler.GROQ_CONFIG_PATH, "r", encoding="utf-8") as f:
                parsed = json.load(f)
                if isinstance(parsed, dict):
                    grq_data = parsed
        grq_data["engine"] = clean_eng
        os.makedirs(os.path.dirname(llm_handler.GROQ_CONFIG_PATH), exist_ok=True)
        with open(llm_handler.GROQ_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(grq_data, f, indent=2)
    except Exception as ex:
        logger.error(f"set_active_engine: failed to update Groq config: {ex}")

    try:
        gem_data: Dict[str, Any] = {}
        if os.path.exists(llm_handler.GEMINI_CONFIG_PATH):
            with open(llm_handler.GEMINI_CONFIG_PATH, "r", encoding="utf-8") as f:
                parsed = json.load(f)
                if isinstance(parsed, dict):
                    gem_data = parsed
        gem_data["engine"] = clean_eng
        os.makedirs(os.path.dirname(llm_handler.GEMINI_CONFIG_PATH), exist_ok=True)
        with open(llm_handler.GEMINI_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(gem_data, f, indent=2)
    except Exception as ex:
        logger.error(f"set_active_engine: failed to update Gemini config: {ex}")


def get_groq_available_models(api_key: Optional[str] = None) -> List[str]:
    """Retrieve list of active text models from Groq API or return default list (cached for 10 min)."""
    import llm_handler
    now = time.time()
    key = api_key or llm_handler.get_groq_api_key()

    cache = llm_handler._groq_models_cache
    if cache.get("models") and now < cache.get("expires_at", 0.0):
        if not key or cache.get("key") == key:
            return list(cache["models"])

    import requests
    if key:
        try:
            resp = requests.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                models = [
                    m["id"] for m in data.get("data", [])
                    if isinstance(m, dict) and "id" in m and not m["id"].startswith("whisper")
                ]
                if models:
                    sorted_models = sorted(models)
                    llm_handler._groq_models_cache = {
                        "models": sorted_models,
                        "expires_at": now + 600,
                        "key": key,
                    }
                    return sorted_models
            else:
                logger.warning(_redact(f"Failed to fetch Groq models (HTTP {resp.status_code}): {resp.text[:200]}", key))
        except Exception as ex:
            logger.debug(_redact(f"get_groq_available_models failed: {ex}", key))

    return list(llm_handler.GROQ_AVAILABLE_MODELS)


def save_groq_config(
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    engine: Optional[str] = None,
    **kwargs: Any,
) -> bool:
    """Save Groq API configuration locally in data/groq_config.json."""
    import llm_handler
    cfg_path = llm_handler.GROQ_CONFIG_PATH
    try:
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        existing = llm_handler.load_groq_config()
        if api_key is not None:
            existing["api_key"] = api_key.strip()
        if model is not None:
            existing["model"] = model.strip()
        if engine is not None:
            existing["engine"] = engine.strip()
        for k, v in kwargs.items():
            existing[k] = v
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
        return True
    except Exception as ex:
        logger.error(f"Failed to save Groq config: {ex}")
        return False


def get_groq_api_key() -> Optional[str]:
    """Retrieve Groq API Key from environment or local config file."""
    import llm_handler
    env_k = os.environ.get("GROQ_API_KEY")
    if env_k and env_k.strip():
        return env_k.strip()
    cfg = llm_handler.load_groq_config()
    cfg_k = cfg.get("api_key")
    if cfg_k and str(cfg_k).strip():
        return str(cfg_k).strip()
    return None


def get_active_engine() -> str:
    """Return active LLM engine: 'groq', 'gemini' or 'ollama'."""
    import llm_handler
    now = time.time()
    env_e = os.environ.get("LLM_ENGINE")
    if env_e:
        engine = env_e.lower().strip()
        if engine == "groq":
            if not llm_handler._groq_session_disabled and now >= llm_handler._groq_cooldown_until:
                return "groq"
            return "ollama"
        elif engine == "gemini":
            if not llm_handler._gemini_session_disabled and now >= llm_handler._gemini_cooldown_until:
                return "gemini"
            return "ollama"
        return engine

    groq_cfg = llm_handler.load_groq_config()
    if groq_cfg.get("engine") == "groq" and llm_handler.get_groq_api_key():
        if not llm_handler._groq_session_disabled and now >= llm_handler._groq_cooldown_until:
            return "groq"

    cfg = llm_handler.load_gemini_config()
    if cfg.get("engine") == "gemini" and llm_handler.get_gemini_api_key():
        if not llm_handler._gemini_session_disabled and now >= llm_handler._gemini_cooldown_until:
            return "gemini"

    return "ollama"


def call_groq_api(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.7,
    response_json: bool = False,
    api_key: Optional[str] = None,
    bypass_state: bool = False,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Call Groq Cloud REST API directly using requests."""
    import llm_handler
    now = time.time()

    if not bypass_state:
        if llm_handler._groq_session_disabled:
            return None, {
                "error": f"Groq disabled for this session: {llm_handler._groq_disabled_reason or 'Error'}",
                "error_type": "disabled",
                "elapsed_seconds": 0.0,
            }
        if now < llm_handler._groq_cooldown_until:
            remaining = int(llm_handler._groq_cooldown_until - now)
            return None, {
                "error": f"Groq quota cooldown active ({remaining}s remaining)",
                "error_type": "cooldown",
                "elapsed_seconds": 0.0,
            }

    import requests
    key = api_key or llm_handler.get_groq_api_key()
    if not key:
        return None, {"error": "Missing Groq API Key", "error_type": "auth", "elapsed_seconds": 0.0}

    cfg = llm_handler.load_groq_config()
    chosen_model = model or cfg.get("model") or llm_handler.DEFAULT_GROQ_MODEL
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "model": chosen_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 4096,
    }
    if response_json:
        payload["response_format"] = {"type": "json_object"}

    start_t = time.time()
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=25)
        elapsed = time.time() - start_t
        if resp.status_code != 200:
            resp_text = resp.text or ""
            resp_text = _redact(resp_text, key)
            truncated_body = resp_text[:300] + "..." if len(resp_text) > 300 else resp_text
            err_msg = _redact(f"HTTP {resp.status_code}: {truncated_body}", key)

            if resp.status_code == 429:
                error_type = "quota"
            elif resp.status_code in (401, 403):
                error_type = "auth"
            elif resp.status_code == 404 or "model_not_found" in resp_text:
                error_type = "not_found"
            elif resp.status_code >= 500:
                error_type = "server_error"
            else:
                error_type = "other"

            if bypass_state:
                logger.warning(_redact(f"Groq connection test failed ({error_type}, HTTP {resp.status_code}): {truncated_body}", key))
            else:
                if error_type == "quota":
                    retry_header = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
                    try:
                        cooldown_secs = int(retry_header) if retry_header else 60
                    except (ValueError, TypeError):
                        cooldown_secs = 60
                    llm_handler._groq_cooldown_until = time.time() + cooldown_secs
                    llm_handler._groq_last_error_type = "quota"
                    logger.warning(_redact(f"Groq rate limit exceeded (HTTP 429). Cooldown activated for {cooldown_secs}s. Falling back to Ollama.", key))
                elif error_type == "auth":
                    llm_handler._groq_session_disabled = True
                    llm_handler._groq_disabled_reason = "Authentication failed (Invalid Groq API key)"
                    llm_handler._groq_last_error_type = "auth"
                    logger.error(_redact(f"Groq authentication failed (HTTP {resp.status_code}): {truncated_body}. Groq disabled for this session.", key))
                elif error_type == "not_found":
                    llm_handler._groq_session_disabled = True
                    llm_handler._groq_disabled_reason = f"Model '{chosen_model}' not found (HTTP 404)"
                    llm_handler._groq_last_error_type = "not_found"
                    logger.error(_redact(f"Groq model '{chosen_model}' not found (HTTP 404). Groq disabled for this session.", key))
                else:
                    llm_handler._groq_last_error_type = error_type
                    logger.error(_redact(f"Groq API returned error: {err_msg}", key))

            return None, {"error": _redact(err_msg, key), "error_type": error_type, "elapsed_seconds": elapsed}

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return "", {"elapsed_seconds": elapsed}
        text = choices[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        metrics = {
            "eval_count": usage.get("completion_tokens", 0),
            "prompt_eval_count": usage.get("prompt_tokens", 0),
            "eval_duration": 0,
            "total_duration": 0,
            "elapsed_seconds": elapsed,
            "engine": "groq",
            "model": chosen_model,
        }
        return text, metrics
    except Exception as ex:
        elapsed = time.time() - start_t
        redacted_ex = _redact(str(ex), key)
        if isinstance(ex, requests.exceptions.Timeout):
            error_type = "timeout"
            logger.warning(_redact(f"Groq API call timed out after {elapsed:.2f}s. Falling back to Ollama.", key))
        elif isinstance(ex, requests.exceptions.RequestException):
            error_type = "network"
            logger.error(_redact(f"Groq API network error after {elapsed:.2f}s: {redacted_ex}. Falling back to Ollama.", key))
        else:
            error_type = "other"
            logger.error(_redact(f"Groq API call failed after {elapsed:.2f}s: {redacted_ex}", key))
        return None, {"error": redacted_ex, "error_type": error_type, "elapsed_seconds": elapsed}


def is_saved_groq_pair(api_key: Optional[str], model: Optional[str]) -> bool:
    """Check if the given (api_key, model) matches the currently configured / active Groq pair."""
    import llm_handler
    cfg = llm_handler.load_groq_config()
    configured_key = (llm_handler.get_groq_api_key() or "").strip()
    configured_model = (cfg.get("model") or llm_handler.DEFAULT_GROQ_MODEL).strip()

    test_k = (api_key or "").strip()
    test_m = (model or llm_handler.DEFAULT_GROQ_MODEL).strip()
    return (test_k == configured_key) and (test_m == configured_model)


def test_groq_connection(api_key: str, model: Optional[str] = None) -> Tuple[bool, str]:
    """Test Groq API connectivity with a simple ping prompt."""
    import llm_handler
    if not api_key or not api_key.strip():
        return False, "Groq API Key ว่างเปล่า"
    chosen_model = model or llm_handler.DEFAULT_GROQ_MODEL
    msgs = [{"role": "user", "content": "Reply with 'OK' only."}]
    txt, metrics = llm_handler.call_groq_api(
        messages=msgs,
        model=chosen_model,
        api_key=api_key.strip(),
        bypass_state=True,
    )
    if txt is not None:
        if is_saved_groq_pair(api_key, chosen_model):
            reset_groq_state()
        elapsed = metrics.get("elapsed_seconds", 0)
        return True, _redact(f"เชื่อมต่อ Groq สำเร็จใน {elapsed:.2f}s! ({chosen_model})", api_key)
    err = metrics.get("error", "Unknown error")
    if "404" in err or "model_not_found" in err:
        available = llm_handler.get_groq_available_models(api_key=api_key.strip())
        if available:
            return False, _redact(f"ไม่พบโมเดล '{chosen_model}' ในบัญชีนี้ โมเดลที่ใช้ได้คือ: {', '.join(available[:4])}", api_key)
    return False, _redact(f"เชื่อมต่อไม่สำเร็จ: {err}", api_key)


def call_gemini_api(
    contents: List[Dict[str, Any]],
    system_instruction: Optional[str] = None,
    temperature: float = 0.7,
    response_mime_type: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    bypass_state: bool = False,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Call Google Gemini REST API directly using requests."""
    import llm_handler
    import requests

    now = time.time()
    if not bypass_state:
        if llm_handler._gemini_session_disabled:
            return None, {
                "error": _redact(f"Gemini is disabled for this session: {llm_handler._gemini_disabled_reason}", api_key),
                "error_type": llm_handler._gemini_last_error_type or "other",
                "elapsed_seconds": 0.0,
            }
        if now < llm_handler._gemini_cooldown_until:
            rem = int(llm_handler._gemini_cooldown_until - now)
            return None, {
                "error": _redact(f"Gemini quota cooldown active ({rem}s remaining)", api_key),
                "error_type": "quota",
                "elapsed_seconds": 0.0,
            }

    key = api_key or llm_handler.get_gemini_api_key()
    if not key:
        if not bypass_state:
            llm_handler._gemini_last_error_type = "auth"
        return None, {"error": "Missing Gemini API Key", "error_type": "auth", "elapsed_seconds": 0.0}

    cfg = llm_handler.load_gemini_config()
    chosen_model = model or cfg.get("model") or llm_handler.DEFAULT_GEMINI_MODEL
    if chosen_model in llm_handler.KNOWN_SHUTDOWN_MODELS:
        logger.warning(
            _redact(
                f"Requested model '{chosen_model}' is shut down. "
                f"Substituting default '{llm_handler.DEFAULT_GEMINI_MODEL}'.",
                key,
            )
        )
        chosen_model = llm_handler.DEFAULT_GEMINI_MODEL
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
                    llm_handler._gemini_cooldown_until = time.time() + llm_handler.GEMINI_COOLDOWN_SECONDS
                    llm_handler._gemini_last_error_type = "quota"
                    logger.warning(
                        _redact(
                            f"Gemini quota exhausted (HTTP {resp.status_code}). "
                            f"Cooldown activated for {llm_handler.GEMINI_COOLDOWN_SECONDS}s. Falling back to Ollama.",
                            key,
                        )
                    )
                elif error_type == "auth":
                    llm_handler._gemini_session_disabled = True
                    llm_handler._gemini_disabled_reason = "Authentication failed (Invalid API key)"
                    llm_handler._gemini_last_error_type = "auth"
                    logger.error(
                        _redact(
                            f"Gemini authentication failed (HTTP {resp.status_code}): {truncated_body}. "
                            "Gemini has been disabled for this session. Please check your API key at https://aistudio.google.com/app/apikey",
                            key,
                        )
                    )
                elif error_type == "not_found":
                    llm_handler._gemini_session_disabled = True
                    llm_handler._gemini_disabled_reason = (
                        f"Model '{chosen_model}' not found or project lacks access (HTTP 404). "
                        "Choose another model and press 'ทดสอบ'."
                    )
                    llm_handler._gemini_last_error_type = "not_found"
                    logger.error(
                        _redact(
                            f"Gemini model '{chosen_model}' not found (HTTP 404). "
                            "Model may be shut down or the API key's project may not have access to this model. "
                            "Gemini has been disabled for this session. Please choose an active model in AI settings and press 'ทดสอบ' (Test) to reactivate.",
                            key,
                        )
                    )
                else:
                    llm_handler._gemini_last_error_type = "other"
                    logger.error(_redact(f"Gemini API returned error: {err_msg}", key))

            return None, {"error": _redact(err_msg, key), "error_type": error_type, "elapsed_seconds": elapsed}

        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            if bypass_state:
                logger.warning("Gemini connection test failed: No candidates returned by Gemini")
            else:
                llm_handler._gemini_last_error_type = "other"
            return None, {"error": "No candidates returned by Gemini", "error_type": "other", "elapsed_seconds": elapsed}

        first_cand = candidates[0]
        text = first_cand.get("content", {}).get("parts", [{}])[0].get("text", "")
        usage = data.get("usageMetadata", {})
        calls_today = llm_handler.get_gemini_daily_calls() if bypass_state else llm_handler.increment_gemini_daily_calls()
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
            llm_handler._gemini_last_error_type = error_type
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
    """Check if the given (api_key, model) matches the currently configured / active pair."""
    import llm_handler
    cfg = llm_handler.load_gemini_config()
    configured_key = (llm_handler.get_gemini_api_key() or "").strip()
    configured_model = (cfg.get("model") or llm_handler.DEFAULT_GEMINI_MODEL).strip()

    test_k = (api_key or "").strip()
    test_m = (model or llm_handler.DEFAULT_GEMINI_MODEL).strip()
    return (test_k == configured_key) and (test_m == configured_model)


def test_gemini_connection(api_key: str, model: str = "gemini-3.6-flash") -> Tuple[bool, str]:
    """Test Gemini API connectivity with a simple ping prompt."""
    import llm_handler
    res, metrics = llm_handler.call_gemini_api(
        contents=[{"role": "user", "parts": [{"text": "Reply with 'OK' only."}]}],
        api_key=api_key,
        model=model,
        temperature=0.1,
        bypass_state=True,
    )
    if res is not None:
        if is_saved_gemini_pair(api_key, model):
            reset_gemini_state()

        elapsed = metrics.get("elapsed_seconds", 0)
        return True, f"เชื่อมต่อสำเร็จใน {elapsed:.2f}s! ({model})"
    return False, metrics.get("error", "Unknown error")


def get_installed_models(client: Optional[Any] = None) -> List[str]:
    """Retrieve list of locally installed model names from Ollama (returns [] if Ollama is not running/installed)."""
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
    """Resolve model to use across Ollama or mock clients."""
    import llm_handler
    if client is not None:
        return model if model else llm_handler.DEFAULT_MODEL

    env_m = os.environ.get("OLLAMA_MODEL")
    if env_m:
        return env_m

    if model and model != "llama3" and model != llm_handler.DEFAULT_MODEL:
        return model

    installed = llm_handler.get_installed_models()
    if installed:
        if "llama3" in installed or "llama3:latest" in installed:
            return "llama3"
        typhoon_candidates = [m for m in installed if "typhoon2-8b" in m or "typhoon" in m]
        if typhoon_candidates:
            return typhoon_candidates[0]
        qwen_candidates = [m for m in installed if "qwen" in m.lower()]
        if qwen_candidates:
            return qwen_candidates[0]
        return installed[0]

    return model if model else llm_handler.DEFAULT_MODEL
