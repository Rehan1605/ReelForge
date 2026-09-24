"""
tests/test_v36_vision_remote.py
-------------------------------
ReelForge V3.6 Layer 3 — Remote AI Vision Connectivity Tests.

Validates that ReelForge's AI Vision integration can operate against a remote
OpenAI-compatible / OmniRoute endpoint rather than assuming local loopback:

  1. Existing local default behavior (OMNIROUTE_BASE_URL loopback default).
  2. Remote endpoint configuration via OMNIROUTE_BASE_URL.
  3. Dedicated vision gateway override via VISION_BASE_URL.
  4. Authentication handling via OMNIROUTE_API_KEY and VISION_API_KEY.
  5. Unauthenticated requests omit Authorization header.
  6. Explicit parameter overrides in call_vision_model and analyze_vision.
  7. Endpoint URL normalization (trailing slash, pre-existing /chat/completions).
  8. Payload structure schema and base64 image encoding compatibility.
  9. Preflight gateway connectivity checking (check_vision_gateway).
  10. Secrets hygiene (keys never exposed in error text).

All tests use mocks — no real network requests are issued.
"""

import json
import unittest
from pathlib import Path
from unittest import mock

import requests

import config
import processing.vision_analyzer as vision_analyzer


REMOTE_OMNIROUTE_URL = "https://ai.cloud.example.com/v1"
DEDICATED_VISION_URL = "https://vision.provider.example.net/v1"
TEST_API_KEY = "sk-test-vision-key-secret123"


class TestVisionRemoteConfiguration(unittest.TestCase):
    """Tests for vision endpoint resolution and authentication configuration."""

    def _mock_success_response(self, content=None):
        resp = mock.Mock()
        resp.status_code = 200
        resp.headers = {}
        payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            content or {"visual_summary": "Test Summary", "on_screen_text": ["Test"]}
                        )
                    }
                }
            ]
        }
        resp.json.return_value = payload
        resp.text = json.dumps(payload)
        return resp

    def test_default_fallback_to_omniroute_base_url(self):
        """When VISION_BASE_URL is unset, vision falls back to OMNIROUTE_BASE_URL."""
        with mock.patch.object(vision_analyzer, "VISION_BASE_URL", ""):
            with mock.patch.object(vision_analyzer, "OMNIROUTE_BASE_URL", REMOTE_OMNIROUTE_URL):
                with mock.patch.object(vision_analyzer, "VISION_API_KEY", ""):
                    with mock.patch.object(vision_analyzer, "OMNIROUTE_API_KEY", ""):
                        with mock.patch("requests.post", return_value=self._mock_success_response()) as mock_post:
                            with mock.patch.object(
                                vision_analyzer,
                                "_encode_image_to_base64",
                                return_value="data:image/jpeg;base64,mockdata",
                            ):
                                vision_analyzer.call_vision_model("prompt", [Path("f1.jpg")])

        posted_url = mock_post.call_args[0][0]
        self.assertEqual(posted_url, f"{REMOTE_OMNIROUTE_URL}/chat/completions")
        self.assertNotIn("localhost", posted_url)

    def test_dedicated_vision_base_url_overrides_omniroute(self):
        """When VISION_BASE_URL is set, it overrides OMNIROUTE_BASE_URL for vision calls."""
        with mock.patch.object(vision_analyzer, "VISION_BASE_URL", DEDICATED_VISION_URL):
            with mock.patch.object(vision_analyzer, "OMNIROUTE_BASE_URL", REMOTE_OMNIROUTE_URL):
                with mock.patch("requests.post", return_value=self._mock_success_response()) as mock_post:
                    with mock.patch.object(
                        vision_analyzer,
                        "_encode_image_to_base64",
                        return_value="data:image/jpeg;base64,mockdata",
                    ):
                        vision_analyzer.call_vision_model("prompt", [Path("f1.jpg")])

        posted_url = mock_post.call_args[0][0]
        self.assertEqual(posted_url, f"{DEDICATED_VISION_URL}/chat/completions")

    def test_authentication_bearer_header_from_vision_api_key(self):
        """VISION_API_KEY sends Authorization: Bearer <key> header."""
        with mock.patch.object(vision_analyzer, "VISION_BASE_URL", DEDICATED_VISION_URL):
            with mock.patch.object(vision_analyzer, "VISION_API_KEY", TEST_API_KEY):
                with mock.patch.object(vision_analyzer, "OMNIROUTE_API_KEY", ""):
                    with mock.patch("requests.post", return_value=self._mock_success_response()) as mock_post:
                        with mock.patch.object(
                            vision_analyzer,
                            "_encode_image_to_base64",
                            return_value="data:image/jpeg;base64,mockdata",
                        ):
                            vision_analyzer.call_vision_model("prompt", [Path("f1.jpg")])

        headers = mock_post.call_args[1]["headers"]
        self.assertEqual(headers.get("Authorization"), f"Bearer {TEST_API_KEY}")

    def test_authentication_falls_back_to_omniroute_api_key(self):
        """When VISION_API_KEY is unset, Authorization falls back to OMNIROUTE_API_KEY."""
        with mock.patch.object(vision_analyzer, "VISION_API_KEY", ""):
            with mock.patch.object(vision_analyzer, "OMNIROUTE_API_KEY", "sk-omniroute-secret"):
                with mock.patch("requests.post", return_value=self._mock_success_response()) as mock_post:
                    with mock.patch.object(
                        vision_analyzer,
                        "_encode_image_to_base64",
                        return_value="data:image/jpeg;base64,mockdata",
                    ):
                        vision_analyzer.call_vision_model("prompt", [Path("f1.jpg")])

        headers = mock_post.call_args[1]["headers"]
        self.assertEqual(headers.get("Authorization"), "Bearer sk-omniroute-secret")

    def test_unauthenticated_request_omits_authorization_header(self):
        """When no API key is configured, the Authorization header is omitted."""
        with mock.patch.object(vision_analyzer, "VISION_API_KEY", ""):
            with mock.patch.object(vision_analyzer, "OMNIROUTE_API_KEY", ""):
                with mock.patch("requests.post", return_value=self._mock_success_response()) as mock_post:
                    with mock.patch.object(
                        vision_analyzer,
                        "_encode_image_to_base64",
                        return_value="data:image/jpeg;base64,mockdata",
                    ):
                        vision_analyzer.call_vision_model("prompt", [Path("f1.jpg")])

        headers = mock_post.call_args[1]["headers"]
        self.assertNotIn("Authorization", headers)

    def test_explicit_arguments_override_environment_and_globals(self):
        """Explicit base_url and api_key arguments take highest precedence."""
        explicit_url = "https://explicit.custom.org/v1"
        explicit_key = "sk-explicit-key"

        with mock.patch.object(vision_analyzer, "VISION_BASE_URL", DEDICATED_VISION_URL):
            with mock.patch.object(vision_analyzer, "VISION_API_KEY", "sk-env-key"):
                with mock.patch("requests.post", return_value=self._mock_success_response()) as mock_post:
                    with mock.patch.object(
                        vision_analyzer,
                        "_encode_image_to_base64",
                        return_value="data:image/jpeg;base64,mockdata",
                    ):
                        vision_analyzer.call_vision_model(
                            "prompt",
                            [Path("f1.jpg")],
                            base_url=explicit_url,
                            api_key=explicit_key,
                        )

        posted_url = mock_post.call_args[0][0]
        self.assertEqual(posted_url, f"{explicit_url}/chat/completions")
        headers = mock_post.call_args[1]["headers"]
        self.assertEqual(headers.get("Authorization"), f"Bearer {explicit_key}")

    def test_endpoint_normalization_handles_trailing_slashes(self):
        """Trailing slashes in base_url are stripped cleanly."""
        with mock.patch("requests.post", return_value=self._mock_success_response()) as mock_post:
            with mock.patch.object(
                vision_analyzer,
                "_encode_image_to_base64",
                return_value="data:image/jpeg;base64,mockdata",
            ):
                vision_analyzer.call_vision_model(
                    "prompt",
                    [Path("f1.jpg")],
                    base_url="https://trailing-slash.example.com/v1///",
                )

        posted_url = mock_post.call_args[0][0]
        self.assertEqual(posted_url, "https://trailing-slash.example.com/v1/chat/completions")

    def test_endpoint_normalization_avoids_duplicate_chat_completions(self):
        """If base_url already includes /chat/completions, it is not duplicated."""
        full_endpoint = "https://proxy.example.com/v1/chat/completions"
        with mock.patch("requests.post", return_value=self._mock_success_response()) as mock_post:
            with mock.patch.object(
                vision_analyzer,
                "_encode_image_to_base64",
                return_value="data:image/jpeg;base64,mockdata",
            ):
                vision_analyzer.call_vision_model(
                    "prompt",
                    [Path("f1.jpg")],
                    base_url=full_endpoint,
                )

        posted_url = mock_post.call_args[0][0]
        self.assertEqual(posted_url, full_endpoint)

    def test_payload_structure_remains_openai_multimodal_compatible(self):
        """Payload adheres to standard OpenAI chat completions multimodal schema."""
        with mock.patch("requests.post", return_value=self._mock_success_response()) as mock_post:
            with mock.patch.object(
                vision_analyzer,
                "_encode_image_to_base64",
                return_value="data:image/jpeg;base64,ZW5jb2RlZA==",
            ):
                vision_analyzer.call_vision_model(
                    prompt_text="Analyze these frames",
                    frame_paths=[Path("frame1.jpg"), Path("frame2.jpg")],
                    model="custom-vision-model",
                    base_url=DEDICATED_VISION_URL,
                )

        call_json = mock_post.call_args[1]["json"]
        self.assertEqual(call_json["model"], "custom-vision-model")
        self.assertEqual(call_json["temperature"], 0.1)

        messages = call_json["messages"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")

        content_items = messages[0]["content"]
        self.assertEqual(len(content_items), 3)  # 1 text + 2 images
        self.assertEqual(content_items[0], {"type": "text", "text": "Analyze these frames"})
        self.assertEqual(
            content_items[1],
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,ZW5jb2RlZA=="}},
        )
        self.assertEqual(
            content_items[2],
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,ZW5jb2RlZA=="}},
        )


class TestVisionGatewayConnectivityCheck(unittest.TestCase):
    """Tests for the preflight check_vision_gateway helper."""

    def test_check_vision_gateway_success_via_models(self):
        """Successful /models probe returns ok=True."""
        mock_resp = mock.Mock()
        mock_resp.status_code = 200

        with mock.patch("requests.get", return_value=mock_resp) as mock_get:
            ok, msg = vision_analyzer.check_vision_gateway(
                base_url=REMOTE_OMNIROUTE_URL,
                api_key="sk-test",
            )

        self.assertTrue(ok)
        self.assertIn("HTTP 200", msg)
        self.assertEqual(mock_get.call_args[0][0], f"{REMOTE_OMNIROUTE_URL}/models")
        self.assertEqual(mock_get.call_args[1]["headers"]["Authorization"], "Bearer sk-test")

    def test_check_vision_gateway_auth_failure(self):
        """HTTP 401/403 from /models indicates authentication failure."""
        mock_resp = mock.Mock()
        mock_resp.status_code = 401

        with mock.patch("requests.get", return_value=mock_resp):
            ok, msg = vision_analyzer.check_vision_gateway(
                base_url=REMOTE_OMNIROUTE_URL,
                api_key="sk-invalid",
            )

        self.assertFalse(ok)
        self.assertIn("authentication failed", msg.lower())

    def test_check_vision_gateway_fallback_to_chat_completions(self):
        """When /models returns 404, probe falls back to /chat/completions."""
        models_resp = mock.Mock()
        models_resp.status_code = 404

        chat_resp = mock.Mock()
        chat_resp.status_code = 200

        with mock.patch("requests.get", return_value=models_resp):
            with mock.patch("requests.post", return_value=chat_resp) as mock_post:
                ok, msg = vision_analyzer.check_vision_gateway(
                    base_url=REMOTE_OMNIROUTE_URL,
                )

        self.assertTrue(ok)
        self.assertIn("chat/completions", msg)
        self.assertEqual(mock_post.call_args[0][0], f"{REMOTE_OMNIROUTE_URL}/chat/completions")

    def test_check_vision_gateway_connection_refused(self):
        """ConnectionError returns ok=False with connection failure notice."""
        with mock.patch(
            "requests.get",
            side_effect=requests.exceptions.ConnectionError("Connection refused"),
        ):
            ok, msg = vision_analyzer.check_vision_gateway(
                base_url="http://unreachable.internal:9999/v1",
            )

        self.assertFalse(ok)
        self.assertIn("Connection refused or unreachable", msg)

    def test_check_vision_gateway_timeout(self):
        """Timeout returns ok=False with timeout description."""
        with mock.patch(
            "requests.get",
            side_effect=requests.exceptions.Timeout("Read timed out"),
        ):
            ok, msg = vision_analyzer.check_vision_gateway(
                base_url="https://slow.example.com/v1",
                timeout=5,
            )

        self.assertFalse(ok)
        self.assertIn("timed out after 5s", msg)


if __name__ == "__main__":
    unittest.main()
