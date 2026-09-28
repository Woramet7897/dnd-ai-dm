"""
phase15_gemini_tests.py — Tests for Gemini Integration Hardening

Covers:
1. 429 / RESOURCE_EXHAUSTED -> cooldown activates and get_active_engine() returns "ollama"
2. 404 -> Gemini disabled for session
3. Timeout -> falls back without cooldown
4. Shutdown-model substitution
5. Retry does not call Gemini a second time (single-billing quota)
6. Counter rolls over at Pacific midnight (America/Los_Angeles)
7. API Key never appears in any log record (assertLogs)
"""

import datetime
import json
import logging
import os
import unittest
from unittest.mock import MagicMock, patch

import llm_handler


class TestPhase15GeminiHardening(unittest.TestCase):

    def setUp(self):
        llm_handler.reset_gemini_state()
        self.dummy_key = "AIzaSyTestKey_12345_SECRET_TOKEN"
        self.test_config_path = os.path.join(os.path.dirname(__file__), "test_gemini_config.json")
        self.orig_config_path = llm_handler.GEMINI_CONFIG_PATH
        llm_handler.GEMINI_CONFIG_PATH = self.test_config_path
        if os.path.exists(self.test_config_path):
            os.remove(self.test_config_path)

    def tearDown(self):
        llm_handler.reset_gemini_state()
        llm_handler.GEMINI_CONFIG_PATH = self.orig_config_path
        if os.path.exists(self.test_config_path):
            try:
                os.remove(self.test_config_path)
            except Exception:
                pass

    def test_429_cooldown_activates_and_switches_engine(self):
        """429 HTTP error activates 10m cooldown and get_active_engine() returns 'ollama'."""
        llm_handler.save_gemini_config(api_key=self.dummy_key, model="gemini-3.6-flash", engine="gemini")
        self.assertEqual(llm_handler.get_active_engine(), "gemini")

        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = json.dumps({
            "error": {
                "code": 429,
                "message": "Resource has been exhausted",
                "status": "RESOURCE_EXHAUSTED"
            }
        })

        with patch("requests.post", return_value=mock_resp):
            res, metrics = llm_handler.call_gemini_api(
                contents=[{"role": "user", "parts": [{"text": "Hello"}]}],
                api_key=self.dummy_key,
            )

        self.assertIsNone(res)
        self.assertEqual(metrics.get("error_type"), "quota")
        self.assertEqual(llm_handler.get_active_engine(), "ollama")

        status = llm_handler.get_gemini_status()
        self.assertGreater(status["cooldown_seconds_remaining"], 0)
        self.assertIsNotNone(status["cooldown_until"])
        self.assertEqual(status["last_error_type"], "quota")

    def test_404_disables_gemini_for_session(self):
        """404 HTTP error permanently disables Gemini for the session and falls back to ollama."""
        llm_handler.save_gemini_config(api_key=self.dummy_key, model="gemini-3.6-flash", engine="gemini")
        self.assertEqual(llm_handler.get_active_engine(), "gemini")

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = json.dumps({"error": {"code": 404, "message": "Model not found"}})

        with patch("requests.post", return_value=mock_resp):
            res, metrics = llm_handler.call_gemini_api(
                contents=[{"role": "user", "parts": [{"text": "Hello"}]}],
                api_key=self.dummy_key,
            )

        self.assertIsNone(res)
        self.assertEqual(metrics.get("error_type"), "not_found")
        self.assertEqual(llm_handler.get_active_engine(), "ollama")

        status = llm_handler.get_gemini_status()
        self.assertTrue(status["session_disabled"])
        self.assertEqual(status["last_error_type"], "not_found")
        self.assertIn("not found", status["disabled_reason"].lower())

        # Ensure subsequent calls are rejected without making network requests
        with patch("requests.post") as post_mock:
            res2, metrics2 = llm_handler.call_gemini_api(
                contents=[{"role": "user", "parts": [{"text": "Hello again"}]}],
                api_key=self.dummy_key,
            )
            post_mock.assert_not_called()
            self.assertIsNone(res2)
            self.assertEqual(metrics2.get("error_type"), "not_found")

    def test_timeout_fallback_without_cooldown(self):
        """Timeout error falls back to Ollama without setting cooldown."""
        import requests
        llm_handler.save_gemini_config(api_key=self.dummy_key, model="gemini-3.6-flash", engine="gemini")
        self.assertEqual(llm_handler.get_active_engine(), "gemini")

        with patch("requests.post", side_effect=requests.Timeout("Request timed out")):
            res, metrics = llm_handler.call_gemini_api(
                contents=[{"role": "user", "parts": [{"text": "Hello"}]}],
                api_key=self.dummy_key,
            )

        self.assertIsNone(res)
        self.assertEqual(metrics.get("error_type"), "timeout")

        status = llm_handler.get_gemini_status()
        self.assertFalse(status["session_disabled"])
        self.assertEqual(status["cooldown_seconds_remaining"], 0)
        self.assertIsNone(status["cooldown_until"])
        # Engine remains gemini because no quota cooldown was applied
        self.assertEqual(llm_handler.get_active_engine(), "gemini")

    def test_shutdown_model_substitution(self):
        """Models in KNOWN_SHUTDOWN_MODELS are replaced with DEFAULT_GEMINI_MODEL."""
        dead_model = "gemini-2.0-flash"
        self.assertIn(dead_model, llm_handler.KNOWN_SHUTDOWN_MODELS)

        # 1. Saved in config
        llm_handler.save_gemini_config(api_key=self.dummy_key, model=dead_model, engine="gemini")
        loaded_cfg = llm_handler.load_gemini_config()
        self.assertEqual(loaded_cfg["model"], llm_handler.DEFAULT_GEMINI_MODEL)

        # 2. Passed directly to call_gemini_api
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Response from new default"}]}}],
            "usageMetadata": {
                "candidatesTokenCount": 5,
                "promptTokenCount": 10,
            }
        }

        with patch("requests.post", return_value=mock_resp) as mock_post:
            res, metrics = llm_handler.call_gemini_api(
                contents=[{"role": "user", "parts": [{"text": "Hi"}]}],
                api_key=self.dummy_key,
                model=dead_model,
            )
            self.assertIsNotNone(res)
            call_args, call_kwargs = mock_post.call_args
            called_url = call_args[0]
            self.assertNotIn(dead_model, called_url)
            self.assertIn(llm_handler.DEFAULT_GEMINI_MODEL, called_url)
            self.assertEqual(metrics["model"], llm_handler.DEFAULT_GEMINI_MODEL)

    def test_retry_does_not_call_gemini_second_time(self):
        """When attempt 1 via Gemini fails JSON parsing, Gemini is NOT called for retry."""
        llm_handler.save_gemini_config(api_key=self.dummy_key, model="gemini-3.6-flash", engine="gemini")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "INVALID_JSON_HERE"}]}}],
            "usageMetadata": {
                "candidatesTokenCount": 5,
                "promptTokenCount": 10,
            }
        }

        with patch("requests.post", return_value=mock_resp) as mock_post, \
             patch("ollama.chat") as mock_ollama:
            mock_ollama.side_effect = Exception("Ollama offline")
            updates = llm_handler.extract_state_updates(
                narrative_text="A door opens.",
                user_input="Open door"
            )
            # Gemini API must be called exactly once
            self.assertEqual(mock_post.call_count, 1)

    def test_counter_rolls_over_at_pacific_midnight(self):
        """Call counter rolls over when the Pacific date changes."""
        yesterday_pt = "2026-09-27"
        today_pt = "2026-09-28"

        # Simulate saved count from yesterday
        cfg = {
            "api_key": self.dummy_key,
            "model": "gemini-3.6-flash",
            "engine": "gemini",
            "daily_calls": {
                "date": yesterday_pt,
                "count": 42
            }
        }
        with open(self.test_config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f)

        with patch("llm_handler.get_pacific_date_str", return_value=today_pt):
            # Yesterday's calls should not count towards today
            self.assertEqual(llm_handler.get_gemini_daily_calls(), 0)

            # Incrementing for today should start at 1
            count1 = llm_handler.increment_gemini_daily_calls()
            self.assertEqual(count1, 1)
            self.assertEqual(llm_handler.get_gemini_daily_calls(), 1)

            # Next call on same date increments to 2
            count2 = llm_handler.increment_gemini_daily_calls()
            self.assertEqual(count2, 2)
            self.assertEqual(llm_handler.get_gemini_daily_calls(), 2)

    def test_api_key_never_appears_in_log_records(self):
        """API key is never output in any log record, URL query string, or error message."""
        secret_key = "AIzaSySuperSecretKey9988776655"
        llm_handler.save_gemini_config(api_key=secret_key, model="gemini-3.6-flash", engine="gemini")

        mock_resp_err = MagicMock()
        mock_resp_err.status_code = 401
        mock_resp_err.text = f'{{"error": "Invalid API key provided: {secret_key}"}}'

        with self.assertLogs("llm_handler", level="DEBUG") as log_ctx:
            with patch("requests.post", return_value=mock_resp_err) as mock_post:
                res, metrics = llm_handler.call_gemini_api(
                    contents=[{"role": "user", "parts": [{"text": "Hello"}]}],
                    api_key=secret_key,
                )

            # Check request headers and URL
            call_args, call_kwargs = mock_post.call_args
            called_url = call_args[0]
            headers = call_kwargs["headers"]

            self.assertNotIn(secret_key, called_url)
            self.assertNotIn("key=", called_url)
            self.assertEqual(headers.get("x-goog-api-key"), secret_key)

            # Check all log output records
            for record in log_ctx.output:
                self.assertNotIn(secret_key, record, f"API key leaked in log record: {record}")

    def test_corrupt_config_not_overwritten_on_successful_call(self):
        """A corrupt config file is preserved and not overwritten when recording daily calls."""
        corrupt_content = '{"api_key": "saved_secret", "corrupt_json_missing_brace": '
        with open(self.test_config_path, "w", encoding="utf-8") as f:
            f.write(corrupt_content)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Hello world"}]}}],
            "usageMetadata": {"candidatesTokenCount": 5, "promptTokenCount": 10},
        }

        with patch("requests.post", return_value=mock_resp):
            res, metrics = llm_handler.call_gemini_api(
                contents=[{"role": "user", "parts": [{"text": "Hi"}]}],
                api_key="valid_key",
            )
            self.assertIsNotNone(res)

        # Config file must remain completely unchanged
        with open(self.test_config_path, "r", encoding="utf-8") as f:
            content_after = f.read()
        self.assertEqual(content_after, corrupt_content)

    def test_env_key_only_does_not_create_engine_gemini_config(self):
        """When API key comes from env var only, incrementing counter never writes engine or api_key."""
        if os.path.exists(self.test_config_path):
            os.remove(self.test_config_path)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Hello world"}]}}],
            "usageMetadata": {"candidatesTokenCount": 5, "promptTokenCount": 10},
        }

        with patch.dict(os.environ, {"GEMINI_API_KEY": "env_secret_key"}), \
             patch("requests.post", return_value=mock_resp):
            res, metrics = llm_handler.call_gemini_api(
                contents=[{"role": "user", "parts": [{"text": "Hi"}]}],
            )
            self.assertIsNotNone(res)

        # Config file may contain daily_calls, but must NEVER contain api_key or engine
        if os.path.exists(self.test_config_path):
            with open(self.test_config_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertNotIn("api_key", saved)
            self.assertNotIn("engine", saved)

    def test_connection_error_redacts_api_key_in_logs_and_metrics(self):
        """ConnectionError with ?key=... URL fragment redacts key in logs and metrics."""
        import requests
        secret_key = "AIzaSySecretNetworkKey_998877"
        llm_handler.save_gemini_config(api_key=secret_key, model="gemini-3.6-flash", engine="gemini")

        conn_err_msg = f"Failed to establish connection: https://generativelanguage.googleapis.com/v1beta/models?key={secret_key}&alt=json"

        with self.assertLogs("llm_handler", level="ERROR") as log_ctx:
            with patch("requests.post", side_effect=requests.ConnectionError(conn_err_msg)):
                res, metrics = llm_handler.call_gemini_api(
                    contents=[{"role": "user", "parts": [{"text": "Hello"}]}],
                    api_key=secret_key,
                )

        self.assertIsNone(res)
        self.assertEqual(metrics.get("error_type"), "network")
        self.assertNotIn(secret_key, metrics.get("error", ""))
        self.assertIn("[REDACTED_API_KEY]", metrics.get("error", ""))

        for record in log_ctx.output:
            self.assertNotIn(secret_key, record, f"API key leaked in log record: {record}")
            self.assertIn("[REDACTED_API_KEY]", record)

    def test_test_gemini_connection_bypasses_404_disabled_and_recovers_engine(self):
        """Test button bypasses session-disabled state, doesn't increment quota, and recovers engine to gemini."""
        llm_handler.save_gemini_config(api_key=self.dummy_key, model="bad-model", engine="gemini")

        # 1. Trigger 404 to disable Gemini
        mock_404 = MagicMock()
        mock_404.status_code = 404
        mock_404.text = json.dumps({"error": {"code": 404, "message": "Model not found"}})

        with patch("requests.post", return_value=mock_404):
            res, metrics = llm_handler.call_gemini_api(
                contents=[{"role": "user", "parts": [{"text": "Test"}]}],
                api_key=self.dummy_key,
            )

        self.assertIsNone(res)
        self.assertEqual(llm_handler.get_active_engine(), "ollama")
        status = llm_handler.get_gemini_status()
        self.assertTrue(status["session_disabled"])
        self.assertIn("Choose another model and press 'ทดสอบ'", status["disabled_reason"])
        initial_calls = llm_handler.get_gemini_daily_calls()

        # 2. Test button called with valid model and 200 response
        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "OK"}]}}],
            "usageMetadata": {"candidatesTokenCount": 2, "promptTokenCount": 5},
        }

        with patch("requests.post", return_value=mock_200):
            ok, msg = llm_handler.test_gemini_connection(self.dummy_key, model="gemini-3.6-flash")

        self.assertTrue(ok)
        self.assertIn("เชื่อมต่อสำเร็จ", msg)
        # Session disabled state should be recovered
        recovered_status = llm_handler.get_gemini_status()
        self.assertFalse(recovered_status["session_disabled"])
        self.assertEqual(llm_handler.get_active_engine(), "gemini")
        # Test ping must NOT increment daily calls
        self.assertEqual(llm_handler.get_gemini_daily_calls(), initial_calls)

    def test_invalid_model_name_rejected_by_save_config(self):
        """save_gemini_config rejects invalid model names containing spaces, uppercase, or special characters."""
        invalid_names = [
            "Gemini-3.6-Flash",
            "gemini 3.6 flash",
            "gemini@3.6!",
            "-invalid-start",
            ".invalid-start",
        ]
        for inv in invalid_names:
            saved = llm_handler.save_gemini_config(api_key=self.dummy_key, model=inv, engine="gemini")
            self.assertFalse(saved, f"Expected model '{inv}' to be rejected")

        # Valid names should be accepted
        valid_names = ["gemini-3.6-flash", "gemini-3.5-flash-lite", "custom-model.v1", "model123"]
        for val in valid_names:
            saved = llm_handler.save_gemini_config(api_key=self.dummy_key, model=val, engine="gemini")
            self.assertTrue(saved, f"Expected model '{val}' to be accepted")
            loaded = llm_handler.load_gemini_config()
            self.assertEqual(loaded.get("model"), val)


if __name__ == "__main__":
    unittest.main()
