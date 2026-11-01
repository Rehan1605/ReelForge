"""Mocked vision request tests for the fixed OpenRouter free route."""
import json
import inspect
import unittest
from pathlib import Path
from unittest import mock

import config
from processing import vision_analyzer


class TestOpenRouterVision(unittest.TestCase):
    def response(self, content=None, status=200):
        body = {"choices": [{"message": {"content": json.dumps(content or {"visual_summary": "demo"})}}]}
        result = mock.Mock()
        result.status_code = status
        result.headers = {}
        result.text = json.dumps(body)
        result.json.return_value = body
        return result

    def test_fixed_route_and_openai_image_payload(self):
        with mock.patch.object(config, "OPENROUTER_API_KEY", "test-key"):
            with mock.patch.object(vision_analyzer, "_encode_image_to_base64", return_value="data:image/jpeg;base64,abc"):
                with mock.patch("processing.openrouter_gateway.requests.post", return_value=self.response()) as post:
                    result = vision_analyzer.call_vision_model("inspect", [Path("frame.jpg")])
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(kwargs["json"]["model"], "openrouter/free")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(kwargs["json"]["messages"][0]["content"][1]["type"], "image_url")
        self.assertEqual(result["visual_summary"], "demo")

    def test_vision_errors_do_not_expose_provider_body(self):
        response = self.response(status=400)
        response.text = "secret/private provider response"
        with mock.patch.object(config, "OPENROUTER_API_KEY", "test-key"):
            with mock.patch("processing.openrouter_gateway.requests.post", return_value=response):
                with self.assertRaises(RuntimeError) as err:
                    vision_analyzer.call_vision_model("private prompt", [])
        self.assertNotIn("secret/private", str(err.exception))

    def test_vision_rejects_invalid_or_incomplete_content_without_reasoning_fallback(self):
        invalid_bodies = (
            (
                "null content with reasoning",
                {"choices": [{"message": {"content": None, "reasoning": "private reasoning", "reasoning_details": [{"text": "private detail"}]}, "finish_reason": "stop"}]},
            ),
            ("missing content", {"choices": [{"message": {"reasoning": "private reasoning"}, "finish_reason": "stop"}]}),
            ("empty content", {"choices": [{"message": {"content": "  ", "reasoning": "private reasoning"}, "finish_reason": "stop"}]}),
            ("non-string content", {"choices": [{"message": {"content": {"text": "private reasoning"}}, "finish_reason": "stop"}]}),
            ("length finish reason", {"choices": [{"message": {"content": '{"visual_summary":"partial"', "reasoning": "private reasoning"}, "finish_reason": "length"}]}),
        )

        with mock.patch.object(config, "OPENROUTER_API_KEY", "test-key"):
            for label, body in invalid_bodies:
                with self.subTest(response=label):
                    response = self.response()
                    response.json.return_value = body
                    response.text = json.dumps(body)
                    with mock.patch(
                        "processing.openrouter_gateway.requests.post",
                        return_value=response,
                    ) as post:
                        with self.assertRaises(ValueError) as err:
                            vision_analyzer.call_vision_model("inspect", [])
                    self.assertEqual(post.call_count, 1)
                    self.assertNotIn("private reasoning", str(err.exception))
                    self.assertNotIn("private detail", str(err.exception))

    def test_vision_returns_only_content_and_never_reasoning_fields(self):
        body = {
            "choices": [{
                "message": {
                    "content": '{"visual_summary":"visible answer"}',
                    "reasoning": "private reasoning",
                    "reasoning_details": [{"text": "private detail"}],
                },
                "finish_reason": "stop",
            }]
        }
        response = self.response()
        response.json.return_value = body
        response.text = json.dumps(body)
        with mock.patch.object(config, "OPENROUTER_API_KEY", "test-key"):
            with mock.patch("processing.openrouter_gateway.requests.post", return_value=response):
                result = vision_analyzer.call_vision_model("inspect", [])

        self.assertEqual(result["visual_summary"], "visible answer")
        self.assertNotIn("reasoning", result)
        self.assertNotIn("private", str(result))

    def test_model_and_endpoint_overrides_are_not_in_public_signatures(self):
        self.assertNotIn("model", inspect.signature(vision_analyzer.call_vision_model).parameters)
        self.assertNotIn("base_url", inspect.signature(vision_analyzer.analyze_vision).parameters)
        self.assertFalse(hasattr(vision_analyzer, "check_vision_gateway"))


if __name__ == "__main__":
    unittest.main()
