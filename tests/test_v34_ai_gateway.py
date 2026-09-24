"""
tests/test_v34_ai_gateway.py
------------------------------
ReelForge V3.4 Layer 3 (AI gateway independence) tests.

Proves that every AI/vision/evaluation request is routed through the
configurable gateway configuration and does NOT assume a locally running
OmniRoute at localhost:20128:

  1. config.OMNIROUTE_BASE_URL defaults to the current local loopback for
     development, and is fully overridable via environment variables
     (OMNIROUTE_BASE_URL, with OPENAI_BASE_URL as a fallback).
  2. The text client (processing.llm_client) posts to the configured base
     URL (remote-style) and never references localhost.
  3. The vision client (call_vision_model) posts to the configured base URL.
  4. Evaluation/extractors reuse the same llm_client functions.
  5. API keys come only from environment variables; the Dockerfile bakes
     no secrets.

No network calls are made: all HTTP requests are mocked.
"""

import importlib
import json
import os
import unittest
from pathlib import Path
from unittest import mock

import config as config_module
import processing.llm_client as llm_client
import processing.vision_analyzer as vision_analyzer

REMOTE_URL = "https://llm.prod.example.net/v1"


class GatewayConfigTests(unittest.TestCase):
    def _restore(self, original_env):
        os.environ.clear()
        os.environ.update(original_env)
        importlib.reload(config_module)

    def _reload_without_dotenv(self):
        """Reload config with .env loading neutralized so these tests assert
        the code's own default/fallback semantics rather than .env contents."""
        with mock.patch.object(os.environ, "setdefault", lambda *a, **k: None):
            return importlib.reload(config_module)

    def test_default_gateway_preserves_local_loopback(self):
        original_env = dict(os.environ)
        os.environ.pop("OMNIROUTE_BASE_URL", None)
        os.environ.pop("OPENAI_BASE_URL", None)
        try:
            reloaded = self._reload_without_dotenv()
            self.assertEqual(reloaded.OMNIROUTE_BASE_URL, "http://localhost:20128/v1")
        finally:
            self._restore(original_env)

    def test_remote_gateway_via_env_override(self):
        original_env = dict(os.environ)
        os.environ["OMNIROUTE_BASE_URL"] = REMOTE_URL
        os.environ["OMNIROUTE_API_KEY"] = "sk-env-key-123"
        try:
            reloaded = self._reload_without_dotenv()
            self.assertEqual(reloaded.OMNIROUTE_BASE_URL, REMOTE_URL)
            self.assertEqual(reloaded.OMNIROUTE_API_KEY, "sk-env-key-123")
        finally:
            self._restore(original_env)

    def test_openai_base_url_fallback(self):
        original_env = dict(os.environ)
        os.environ.pop("OMNIROUTE_BASE_URL", None)
        os.environ["OPENAI_BASE_URL"] = "https://upstream.example.com/v1"
        try:
            reloaded = self._reload_without_dotenv()
            self.assertEqual(reloaded.OMNIROUTE_BASE_URL, "https://upstream.example.com/v1")
        finally:
            self._restore(original_env)

    def test_openai_api_key_fallback(self):
        original_env = dict(os.environ)
        os.environ.pop("OMNIROUTE_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ["OPENAI_API_KEY"] = "sk-upstream-fallback"
        try:
            reloaded = self._reload_without_dotenv()
            self.assertEqual(reloaded.OMNIROUTE_API_KEY, "sk-upstream-fallback")
        finally:
            self._restore(original_env)

    def test_api_key_defaults_to_empty_when_unset(self):
        original_env = dict(os.environ)
        os.environ.pop("OMNIROUTE_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)
        try:
            reloaded = self._reload_without_dotenv()
            self.assertEqual(reloaded.OMNIROUTE_API_KEY, "")
        finally:
            self._restore(original_env)

    def test_dockerfile_bakes_no_secrets(self):
        dockerfile = (config_module.ROOT_DIR / "Dockerfile").read_text(encoding="utf-8")
        self.assertNotRegex(dockerfile, r"sk-[A-Za-z0-9]{8}")
        for line in dockerfile.splitlines():
            env_line = line.strip()
            if env_line.startswith("ENV "):
                self.assertNotIn("API_KEY", env_line.upper())
                self.assertNotIn("SECRET", env_line.upper())
                self.assertNotIn("TOKEN", env_line.upper())
        self.assertNotIn("--build-arg", dockerfile)


class LlmClientGatewayTests(unittest.TestCase):
    def _fake_response(self, content):
        resp = mock.Mock()
        resp.status_code = 200
        resp.headers = {}
        payload = {"choices": [{"message": {"content": content}}]}
        resp.json.return_value = payload
        resp.text = json.dumps(payload)
        return resp

    def test_generate_text_posts_to_configured_remote_url(self):
        resp = self._fake_response("  hello  ")
        with mock.patch("requests.post", return_value=resp) as mock_post:
            with mock.patch("processing.llm_client.OMNIROUTE_BASE_URL", REMOTE_URL):
                with mock.patch("processing.llm_client.OMNIROUTE_API_KEY", "sk-remote-key"):
                    out = llm_client.generate_text("ignored")
        self.assertEqual(out, "hello")
        posted_url = mock_post.call_args[0][0]
        self.assertEqual(posted_url, f"{REMOTE_URL}/chat/completions")
        self.assertNotIn("localhost", posted_url)
        self.assertNotIn("127.0.0.1", posted_url)

    def test_generate_text_sends_bearer_from_configured_key(self):
        resp = self._fake_response("hi")
        with mock.patch("requests.post", return_value=resp) as mock_post:
            with mock.patch("processing.llm_client.OMNIROUTE_BASE_URL", REMOTE_URL):
                with mock.patch("processing.llm_client.OMNIROUTE_API_KEY", "sk-remote-key"):
                    llm_client.generate_text("ignored")
        self.assertEqual(
            mock_post.call_args[1]["headers"]["Authorization"],
            "Bearer sk-remote-key",
        )
        self.assertEqual(
            mock_post.call_args[1]["json"]["model"],
            llm_client.TEXT_MODEL,
        )

    def test_generate_text_omits_auth_when_no_key(self):
        resp = self._fake_response("hi")
        with mock.patch("requests.post", return_value=resp) as mock_post:
            with mock.patch("processing.llm_client.OMNIROUTE_BASE_URL", REMOTE_URL):
                with mock.patch("processing.llm_client.OMNIROUTE_API_KEY", ""):
                    llm_client.generate_text("ignored")
        self.assertNotIn("Authorization", mock_post.call_args[1]["headers"])

    def test_all_text_eval_callers_reuse_the_gateway_client(self):
        modules = [
            "processing.categorizer",
            "processing.ai_extractor",
            "processing.food_extractor",
            "processing.finance_extractor",
            "processing.other_extractor",
            "processing.movies_edits_extractor",
            "processing.photography_extractor",
            "processing.gym_extractor",
            "processing.productivity_extractor",
            "processing.programming_extractor",
            "processing.travel_extractor",
            "evaluation.evaluate",
        ]
        for module_name in modules:
            mod = importlib.import_module(module_name)
            if hasattr(mod, "generate_json"):
                self.assertIs(
                    mod.generate_json,
                    llm_client.generate_json,
                    f"{module_name}.generate_json is not the shared gateway client",
                )
            if hasattr(mod, "generate_text"):
                self.assertIs(
                    mod.generate_text,
                    llm_client.generate_text,
                    f"{module_name}.generate_text is not the shared gateway client",
                )


class VisionGatewayTests(unittest.TestCase):
    def _fake_vision_response(self, content):
        resp = mock.Mock()
        resp.status_code = 200
        resp.headers = {}
        payload = {"choices": [{"message": {"content": content}}]}
        resp.json.return_value = payload
        resp.text = json.dumps(payload)
        return resp

    def test_call_vision_model_posts_to_remote_url(self):
        content = json.dumps({"visual_summary": "S", "on_screen_text": []})
        resp = self._fake_vision_response(content)
        with mock.patch("requests.post", return_value=resp) as mock_post:
            with mock.patch.object(
                vision_analyzer,
                "_encode_image_to_base64",
                return_value="data:image/jpeg;base64,AAA",
            ):
                result = vision_analyzer.call_vision_model(
                    "prompt",
                    [Path("frame.jpg")],
                    model="vision-model",
                    base_url=REMOTE_URL,
                    api_key="sk-vision-key",
                )
        posted_url = mock_post.call_args[0][0]
        self.assertEqual(posted_url, f"{REMOTE_URL}/chat/completions")
        self.assertNotIn("localhost", posted_url)
        self.assertEqual(
            mock_post.call_args[1]["headers"]["Authorization"],
            "Bearer sk-vision-key",
        )
        self.assertEqual(result["visual_summary"], "S")

    def test_call_vision_model_defaults_to_configured_base_url(self):
        content = json.dumps({"visual_summary": "S", "on_screen_text": []})
        resp = self._fake_vision_response(content)
        with mock.patch("requests.post", return_value=resp) as mock_post:
            with mock.patch.object(vision_analyzer, "OMNIROUTE_BASE_URL", REMOTE_URL):
                with mock.patch.object(vision_analyzer, "OMNIROUTE_API_KEY", "sk-vision"):
                    with mock.patch.object(
                        vision_analyzer,
                        "_encode_image_to_base64",
                        return_value="data:image/jpeg;base64,AAA",
                    ):
                        vision_analyzer.call_vision_model(
                            "prompt",
                            [Path("frame.jpg")],
                            model="vision-model",
                        )
        posted_url = mock_post.call_args[0][0]
        self.assertEqual(posted_url, f"{REMOTE_URL}/chat/completions")
        self.assertNotIn("localhost", posted_url)


if __name__ == "__main__":
    unittest.main()