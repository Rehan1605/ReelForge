"""Mocked contract tests for ReelForge's fixed OpenRouter route."""
import json
import unittest
from unittest import mock

import config
from evaluation import evaluate as evaluation
from processing import llm_client, openrouter_gateway


class TestOpenRouterGateway(unittest.TestCase):
    def response(self, content="ok", status=200):
        result = mock.Mock()
        result.status_code = status
        result.headers = {}
        result.text = json.dumps({"choices": [{"message": {"content": content}}]})
        result.json.return_value = {"choices": [{"message": {"content": content}}]}
        return result

    def test_fixed_endpoint_model_and_authentication(self):
        with mock.patch.object(config, "OPENROUTER_API_KEY", "test-key"):
            with mock.patch("processing.openrouter_gateway.requests.post", return_value=self.response()) as post:
                self.assertEqual(llm_client.generate_text("private prompt"), "ok")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(kwargs["json"]["model"], "openrouter/free")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")
        self.assertFalse(kwargs["allow_redirects"])

    def test_missing_key_fails_before_network(self):
        with mock.patch.object(config, "OPENROUTER_API_KEY", ""):
            with mock.patch("processing.openrouter_gateway.requests.post") as post:
                with self.assertRaisesRegex(RuntimeError, "OPENROUTER_API_KEY"):
                    llm_client.generate_text("prompt")
        post.assert_not_called()

    def test_json_parsing_and_sanitized_malformed_output(self):
        with mock.patch.object(llm_client, "generate_text", return_value='```json\n{"topic":"x"}\n```'):
            self.assertEqual(llm_client.generate_json("prompt"), {"topic": "x"})
        with mock.patch.object(llm_client, "generate_text", return_value="private model output"):
            with self.assertRaises(ValueError) as err:
                llm_client.generate_json("prompt")
        self.assertNotIn("private model output", str(err.exception))

    def test_paid_or_provider_routing_fields_rejected(self):
        with mock.patch.object(config, "OPENROUTER_API_KEY", "test-key"):
            for payload in (
                {"model": "paid/model"},
                {"model": "openrouter/free", "provider": {"order": ["provider"]}},
                {"model": "openrouter/free", "models": ["paid/model"]},
            ):
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    openrouter_gateway.post_chat_completion(payload, 1)

    def test_rate_limit_is_bounded_and_has_no_fallback(self):
        limited = self.response(status=429)
        limited.headers = {"Retry-After": "0"}
        with mock.patch.object(config, "OPENROUTER_API_KEY", "test-key"):
            with mock.patch("processing.openrouter_gateway.requests.post", return_value=limited) as post:
                with mock.patch("processing.openrouter_gateway.time.sleep"):
                    with self.assertRaisesRegex(RuntimeError, "rate limit"):
                        llm_client.generate_text("prompt")
        self.assertEqual(post.call_count, 2)

    def test_evaluation_has_no_model_override(self):
        import inspect
        self.assertNotIn("model", inspect.signature(evaluation.evaluate_reel_evidence).parameters)
        self.assertNotIn("model", inspect.signature(evaluation.evaluate_reel).parameters)

    def test_evaluation_uses_fixed_openrouter_endpoint_and_model(self):
        response = self.response(content='{"mock":"judge result"}')
        with mock.patch.object(config, "OPENROUTER_API_KEY", "test-key"):
            with mock.patch("processing.openrouter_gateway.requests.post", return_value=response) as post:
                with mock.patch.object(
                    evaluation,
                    "validate_and_normalize_judge_output",
                    return_value={"judge_model": "openrouter/free"},
                ):
                    result = evaluation.evaluate_reel_evidence(
                        reel_id="test-reel",
                        source_url="https://example.invalid/reel/test-reel",
                        creator="tester",
                        caption="caption",
                        transcript="transcript",
                        vision_analysis=None,
                        category="Other",
                        knowledge={"category": "Other"},
                    )

        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(kwargs["json"]["model"], "openrouter/free")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(result["judge_model"], "openrouter/free")

if __name__ == "__main__":
    unittest.main()
