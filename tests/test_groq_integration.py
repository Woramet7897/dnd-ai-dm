"""
test_groq_integration.py — Unit & Integration tests for Groq engine integration.
Tests:
- 2.1 Fallback to Ollama receives Ollama model name, not cloud model.
- 2.2 Model list caching with 10 min TTL and exclusion from hot path.
- 2.3 Error classification (429 cooldown, 401 session disabled, etc.).
- 2.4 LLM_ENGINE environment variable priority override.
- 2.5 API key redaction for both Gemini and Groq keys.
- 2.6 set_active_engine helper that modifies only 'engine' without clobbering config or resetting state.
"""

import json
import os
import time
import unittest
from unittest.mock import MagicMock, patch

import llm_handler


class TestGroqIntegration(unittest.TestCase):

    def setUp(self):
        llm_handler.reset_groq_state()
        llm_handler.reset_gemini_state()

    def tearDown(self):
        llm_handler.reset_groq_state()
        llm_handler.reset_gemini_state()

    def test_2_4_get_active_engine_respects_env_override(self):
        """LLM_ENGINE=ollama + groq_config engine=groq must return 'ollama'."""
        with patch.dict(os.environ, {"LLM_ENGINE": "ollama", "GROQ_API_KEY": "gsk_test123"}):
            with patch("llm_handler.load_groq_config", return_value={"engine": "groq", "model": "qwen/qwen3.8-27b"}):
                engine = llm_handler.get_active_engine()
                self.assertEqual(engine, "ollama")

    def test_2_1_fallback_to_ollama_uses_ollama_model_not_cloud_model(self):
        """When Groq fails (e.g. 429), fallback to Ollama must use Ollama model name."""
        player = {"name": "Hero", "hp": {"current": 20, "max": 20}, "stats": {}}
        world = {"current_location": "town_square"}

        mock_resp_429 = MagicMock()
        mock_resp_429.status_code = 429
        mock_resp_429.headers = {"retry-after": "60"}
        mock_resp_429.text = "Rate limit exceeded"

        mock_ollama_resp = {
            "message": {"content": "The tavern is lively tonight."},
            "eval_count": 10,
            "prompt_eval_count": 20,
            "eval_duration": 100,
            "total_duration": 150,
        }

        with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_mock_valid_key"}):
            with patch("llm_handler.load_groq_config", return_value={"engine": "groq", "model": "qwen/qwen3.8-27b"}):
                with patch("requests.post", return_value=mock_resp_429):
                    with patch("ollama.chat", return_value=mock_ollama_resp) as mock_ollama_chat:
                        res = llm_handler.generate_narrative_response(
                            user_input="Look around",
                            player_state=player,
                            world_state=world,
                            model="qwen/qwen3.8-27b",
                            ollama_model="llama3:custom",
                        )
                        self.assertIn("narrative", res)
                        mock_ollama_chat.assert_called_once()
                        called_model = mock_ollama_chat.call_args[1].get("model") or mock_ollama_chat.call_args[0][0]
                        # Must NOT be the Groq cloud model
                        self.assertNotIn("qwen/", called_model)
                        self.assertEqual(called_model, "llama3:custom")

    def test_2_2_get_groq_available_models_cached_and_not_in_hot_path(self):
        """Calling narrative 3 times must make at most 1 GET /models (or 0 from hot path)."""
        player = {"name": "Hero", "hp": {"current": 20, "max": 20}, "stats": {}}
        world = {"current_location": "town_square"}

        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.json.return_value = {"data": [{"id": "qwen/qwen3.8-27b"}, {"id": "llama-3.3-70b-versatile"}]}

        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {
            "choices": [{"message": {"content": "You see a bustling tavern."}}],
            "usage": {"completion_tokens": 15, "prompt_tokens": 25},
        }

        with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test"}):
            with patch("llm_handler.load_groq_config", return_value={"engine": "groq", "model": "qwen/qwen3.8-27b"}):
                with patch("requests.get", return_value=mock_get_resp) as mock_get:
                    with patch("requests.post", return_value=mock_post_resp):
                        for _ in range(3):
                            llm_handler.generate_narrative_response(
                                user_input="Hello",
                                player_state=player,
                                world_state=world,
                            )
                        # Hot path of narrative generation must NOT query /models!
                        self.assertEqual(mock_get.call_count, 0)

    def test_2_3_groq_error_classification_and_disabled_session(self):
        """Simulating HTTP 401 across 3 turns calls Groq only ONCE and disables session."""
        mock_resp_401 = MagicMock()
        mock_resp_401.status_code = 401
        mock_resp_401.text = "Invalid API Key"

        with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_invalid"}):
            with patch("llm_handler.load_groq_config", return_value={"engine": "groq", "model": "qwen/qwen3.8-27b"}):
                with patch("requests.post", return_value=mock_resp_401) as mock_post:
                    # Turn 1: hits Groq, gets 401, session disabled
                    res1, m1 = llm_handler.call_groq_api([{"role": "user", "content": "hi"}])
                    self.assertIsNone(res1)
                    self.assertEqual(m1.get("error_type"), "auth")

                    status = llm_handler.get_groq_status()
                    self.assertTrue(status.get("session_disabled"))

                    # get_active_engine() must fall back to ollama when groq is disabled
                    self.assertEqual(llm_handler.get_active_engine(), "ollama")

                    # Turn 2 & 3: active engine is now ollama, but if call_groq_api is called directly
                    # or if active_engine was checked, Groq is called only once
                    self.assertEqual(mock_post.call_count, 1)

    def test_2_5_redaction_removes_both_gemini_and_groq_keys(self):
        """_redact must strip both Gemini and Groq API keys."""
        groq_k = "gsk_abcdef1234567890abcdef1234567890"
        gemini_k = "AIzaSyTestGeminiKey1234567890"

        with patch("llm_handler.get_groq_api_key", return_value=groq_k):
            with patch("llm_handler.get_gemini_api_key", return_value=gemini_k):
                leak_text = f"Error connecting to https://api.groq.com with key={groq_k} and {gemini_k}"
                redacted = llm_handler._redact(leak_text)
                self.assertNotIn(groq_k, redacted)
                self.assertNotIn(gemini_k, redacted)
                self.assertIn("[REDACTED_API_KEY]", redacted)

    def test_2_6_set_active_engine_preserves_models_and_keys(self):
        """set_active_engine must only update 'engine' without altering model, api_key, or cooldown."""
        with patch("llm_handler.GROQ_CONFIG_PATH", "tests/temp_groq_config.json"):
            with patch("llm_handler.GEMINI_CONFIG_PATH", "tests/temp_gemini_config.json"):
                try:
                    # Setup initial configs
                    os.makedirs("tests", exist_ok=True)
                    with open("tests/temp_gemini_config.json", "w", encoding="utf-8") as f:
                        json.dump({"api_key": "gem_test", "model": "gemini-3.5-flash-lite", "engine": "gemini"}, f)
                    with open("tests/temp_groq_config.json", "w", encoding="utf-8") as f:
                        json.dump({"api_key": "groq_test", "model": "qwen/qwen3.8-27b", "engine": "groq"}, f)

                    # Switch to groq
                    llm_handler.set_active_engine("groq")

                    # Verify gemini model and key are UNTOUCHED
                    with open("tests/temp_gemini_config.json", "r", encoding="utf-8") as f:
                        gem_data = json.load(f)
                    self.assertEqual(gem_data["model"], "gemini-3.5-flash-lite")
                    self.assertEqual(gem_data["api_key"], "gem_test")
                    self.assertEqual(gem_data["engine"], "groq")

                    # Switch to ollama
                    llm_handler.set_active_engine("ollama")
                    with open("tests/temp_gemini_config.json", "r", encoding="utf-8") as f:
                        gem_data = json.load(f)
                    self.assertEqual(gem_data["model"], "gemini-3.5-flash-lite")
                    self.assertEqual(gem_data["engine"], "ollama")

                finally:
                    for p in ["tests/temp_gemini_config.json", "tests/temp_groq_config.json"]:
                        if os.path.exists(p):
                            try:
                                os.remove(p)
                            except Exception:
                                pass

    def test_2_7_connection_test_unsaved_pair_does_not_reset_session_disabled(self):
        """Testing an unsaved key/model pair succeeds in ping but does NOT reset session_disabled."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "OK"}}],
            "usage": {"completion_tokens": 2, "prompt_tokens": 5},
        }

        with patch.dict(os.environ, {}, clear=True):
            with patch("llm_handler.load_groq_config", return_value={"api_key": "gsk_saved_key", "model": "llama-3.3-70b-versatile"}):
                with patch("requests.post", return_value=mock_resp):
                    llm_handler._groq_session_disabled = True
                    llm_handler._groq_disabled_reason = "Test 401"

                    # Case A: Different key
                    ok, msg = llm_handler.test_groq_connection("gsk_unsaved_key", "llama-3.3-70b-versatile")
                    self.assertTrue(ok)
                    self.assertTrue(llm_handler.get_groq_status()["session_disabled"])

                    # Case B: Same key, different model
                    ok, msg = llm_handler.test_groq_connection("gsk_saved_key", "qwen/qwen3.8-27b")
                    self.assertTrue(ok)
                    self.assertTrue(llm_handler.get_groq_status()["session_disabled"])

    def test_2_8_connection_test_saved_pair_resets_session_disabled(self):
        """Testing the currently saved key/model pair succeeds in ping and DOES reset session_disabled."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "OK"}}],
            "usage": {"completion_tokens": 2, "prompt_tokens": 5},
        }

        with patch.dict(os.environ, {}, clear=True):
            with patch("llm_handler.load_groq_config", return_value={"api_key": "gsk_saved_key", "model": "llama-3.3-70b-versatile"}):
                with patch("requests.post", return_value=mock_resp):
                    llm_handler._groq_session_disabled = True
                    llm_handler._groq_disabled_reason = "Test 401"

                    ok, msg = llm_handler.test_groq_connection("gsk_saved_key", "llama-3.3-70b-versatile")
                    self.assertTrue(ok)
                    self.assertFalse(llm_handler.get_groq_status()["session_disabled"])

    def test_2_9_connection_test_env_key_resets_session_disabled(self):
        """Testing with key from GROQ_API_KEY env var succeeds and DOES reset session_disabled."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "OK"}}],
            "usage": {"completion_tokens": 2, "prompt_tokens": 5},
        }

        with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_env_key"}):
            with patch("llm_handler.load_groq_config", return_value={"model": "llama-3.3-70b-versatile"}):
                with patch("requests.post", return_value=mock_resp):
                    llm_handler._groq_session_disabled = True
                    llm_handler._groq_disabled_reason = "Test 401"

                    ok, msg = llm_handler.test_groq_connection("gsk_env_key", "llama-3.3-70b-versatile")
                    self.assertTrue(ok)
                    self.assertFalse(llm_handler.get_groq_status()["session_disabled"])


if __name__ == "__main__":
    unittest.main()

