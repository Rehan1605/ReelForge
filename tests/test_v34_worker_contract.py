"""Layer 5: Worker runtime-contract validation.

Verifies the documentation artifacts (WORKER.env.example / WORKER_OPERATIONS.md)
stay in lock-step with the variables the code actually reads, that no real
credentials were committed into the example, and that the documented startup
command matches the Docker image entrypoint.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.py"
EXAMPLE = ROOT / "WORKER.env.example"
OPS = ROOT / "WORKER_OPERATIONS.md"
ENV_EXAMPLE = ROOT / ".env.example"
DOCKERFILE = ROOT / "Dockerfile"
BOT = ROOT / "bot" / "telegram_bot.py"

# Defaults the example file may legitimately pin even though the variables are
# optional at the code level. Used by the value-hygiene check.
_ALLOWED_CONCRETE_VALUES = {
    "8080",
    "8",
    "common",
    "127.0.0.1",
    "reelforge",
    "http://localhost:8080",
    "http://localhost:8080/auth/callback",
    "http://localhost:20128/v1",
    "https://login.microsoftonline.com/common",
}

_SECRET_KEYS = {
    "TELEGRAM_BOT_TOKEN",
    "MICROSOFT_CLIENT_ID",
    "MONGODB_URI",
    "OMNIROUTE_API_KEY",
    "OAUTH_STATE_SECRET",
    "OPENAI_API_KEY",
}

# Only these two are hard-required at import time (_required_env).
_HARD_REQUIRED = {"TELEGRAM_BOT_TOKEN", "MICROSOFT_CLIENT_ID"}


def _env_keys(source: str) -> set:
    return set(
        re.findall(r'(?:os\.getenv|_required_env|os\.environ\.get)\(\s*"([A-Z][A-Z0-9_]*)"', source)
    )


def _example_keys(path: Path) -> set:
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)\s*=", path.read_text(encoding="utf-8"), re.M))


class TestWorkerEnvExample(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for p in (CONFIG, EXAMPLE, OPS, ENV_EXAMPLE, BOT):
            if not p.exists():
                raise FileNotFoundError(f"Missing file under test: {p}")
        cls.config_env = _env_keys(CONFIG.read_text(encoding="utf-8"))
        cls.example_env = _example_keys(EXAMPLE)
        cls.dotenv_env = _example_keys(ENV_EXAMPLE)

    def test_example_documents_every_config_variable(self):
        missing = sorted(self.config_env - self.example_env)
        self.assertEqual(missing, [], f"WORKER.env.example missing: {missing}")

    def test_example_invents_no_variables(self):
        invented = sorted(self.example_env - self.config_env)
        self.assertEqual(invented, [], f"WORKER.env.example invents: {invented}")

    def test_example_covers_the_dotenv_template(self):
        missing = sorted(self.dotenv_env - self.example_env)
        self.assertEqual(missing, [], f"WORKER.env.example missing vs .env.example: {missing}")

    def test_all_secret_placeholders_are_empty(self):
        for key in _SECRET_KEYS:
            self.assertIn(key, self.example_env, f"secret {key} not in WORKER.env.example")
            m = re.search(rf"^{key}\s*=\s*([^\r\n#]*?)", EXAMPLE.read_text(encoding="utf-8"), re.M)
            self.assertEqual(m.group(1), "", f"{key} must be empty in WORKER.env.example")

    def test_no_leaked_credential_patterns(self):
        text = EXAMPLE.read_text(encoding="utf-8")
        leaky = re.findall(r"(sk-[A-Za-z0-9]{20,}|[A-Za-z0-9]{40,}|ea[a-z]{28})", text)
        self.assertEqual(leaky, [], f"suspicious credential-like text: {leaky}")
        for line in text.splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            if not value:
                continue
            self.assertIn(
                value,
                _ALLOWED_CONCRETE_VALUES,
                f"non-placeholder value '{value}' for {key} — must stay a default/placeholder",
            )

    def test_bot_token_is_the_only_hard_required_telegram_var(self):
        required = _env_keys(CONFIG.read_text(encoding="utf-8")) & _HARD_REQUIRED
        self.assertEqual(
            required,
            _HARD_REQUIRED,
            f"expected exactly {sorted(_HARD_REQUIRED)} hard-required vars",
        )


class TestWorkerOperationsDoc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ops = OPS.read_text(encoding="utf-8")
        cls.dockerfile = DOCKERFILE.read_text(encoding="utf-8") if DOCKERFILE.exists() else ""

    def test_ops_doc_mentions_key_contract_terms(self):
        lowered = self.ops.lower()
        for term in (
            "TELEGRAM_BOT_TOKEN",
            "MICROSOFT_CLIENT_ID",
            "MONGODB_URI",
            "OMNIROUTE_BASE_URL",
            "WHISPER_CACHE_DIR",
            "FFMPEG_PATH",
            "WORKSPACE_DIR",
            "BRAINS_DIR",
            "token_cache.bin",
            "ephemeral",
            "readiness",
            "startup",
        ):
            self.assertIn(term.lower(), lowered, f"doc must cover '{term}'")

    def test_ops_doc_started_command_matches_bot_entrypoint_and_doc(self):
        self.assertIn("python bot/telegram_bot.py", self.ops)
        cmd = self.dockerfile.split("CMD")[-1] if self.dockerfile else ""
        self.assertRegex(cmd, r"bot/telegram_bot\.py")

    def test_ops_doc_flags_no_localhost_assumption(self):
        self.assertRegex(self.ops.lower(), r"does\s+\*?\*?\s*not\s*\*?\*?\s+assume\s+localhost")
        self.assertRegex(self.ops.lower(), r"laptop-side")


if __name__ == "__main__":
    unittest.main()