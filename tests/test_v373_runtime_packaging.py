"""V3.7.3-B startup checks and Docker packaging contract tests."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import bot.telegram_bot as telegram_bot
import config
import processing.transcriber as transcriber
import processing.worker as worker


ROOT = Path(__file__).resolve().parent.parent


class TranscriptionRuntimeValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.model = Path(self.temp.name) / "ggml-base.bin"
        self.model.write_bytes(b"mock multilingual Base model")
        self.binaries = {
            "ffmpeg": Path(self.temp.name) / "ffmpeg",
            "whisper-cli": Path(self.temp.name) / "whisper-cli",
        }
        for binary in self.binaries.values():
            binary.write_bytes(b"mock executable")

    def _validation_patches(self):
        return (
            patch.object(transcriber, "WHISPER_CPP_MODEL_PATH", str(self.model)),
            patch.object(transcriber.shutil, "which", side_effect=lambda name: str(self.binaries[name])),
            patch.object(transcriber.os, "access", return_value=True),
        )

    def test_runtime_validation_success_does_not_run_transcription(self):
        model_patch, which_patch, access_patch = self._validation_patches()
        with model_patch, which_patch, access_patch, patch.object(
            transcriber.subprocess, "Popen"
        ) as popen:
            self.assertTrue(transcriber.validate_transcription_runtime())
        popen.assert_not_called()

    def test_runtime_validation_rejects_missing_whisper_cli(self):
        with patch.object(transcriber, "WHISPER_CPP_MODEL_PATH", str(self.model)), \
             patch.object(transcriber.shutil, "which", side_effect=lambda name: None if name == "whisper-cli" else str(self.binaries[name])), \
             patch.object(transcriber.os, "access", return_value=True):
            with self.assertRaisesRegex(FileNotFoundError, "whisper.cpp"):
                transcriber.validate_transcription_runtime()

    def test_runtime_validation_rejects_missing_ffmpeg(self):
        with patch.object(transcriber, "WHISPER_CPP_MODEL_PATH", str(self.model)), \
             patch.object(transcriber.shutil, "which", side_effect=lambda name: None if name == "ffmpeg" else str(self.binaries[name])), \
             patch.object(transcriber.os, "access", return_value=True):
            with self.assertRaisesRegex(FileNotFoundError, "FFmpeg"):
                transcriber.validate_transcription_runtime()

    def test_runtime_validation_rejects_missing_model(self):
        missing_model = Path(self.temp.name) / "missing" / "ggml-base.bin"
        with patch.object(transcriber, "WHISPER_CPP_MODEL_PATH", str(missing_model)):
            with self.assertRaisesRegex(FileNotFoundError, "Base model"):
                transcriber.validate_transcription_runtime()

    def test_runtime_validation_rejects_non_multilingual_model_name(self):
        english_only = Path(self.temp.name) / "ggml-base.en.bin"
        english_only.write_bytes(b"not accepted")
        with patch.object(transcriber, "WHISPER_CPP_MODEL_PATH", str(english_only)):
            with self.assertRaisesRegex(ValueError, "multilingual"):
                transcriber.validate_transcription_runtime()

    def test_runtime_validation_rejects_unreadable_model(self):
        model_patch, which_patch, _ = self._validation_patches()
        with model_patch, which_patch, patch.object(transcriber.os, "access", return_value=False):
            with self.assertRaisesRegex(FileNotFoundError, "unreadable"):
                transcriber.validate_transcription_runtime()

    def test_runtime_validation_rejects_invalid_thread_count(self):
        model_patch, which_patch, access_patch = self._validation_patches()
        with model_patch, which_patch, access_patch, \
             patch.object(transcriber, "WHISPER_CPP_THREADS", 0):
            with self.assertRaisesRegex(ValueError, "WHISPER_CPP_THREADS"):
                transcriber.validate_transcription_runtime()

    def test_runtime_validation_rejects_invalid_timeout(self):
        model_patch, which_patch, access_patch = self._validation_patches()
        with model_patch, which_patch, access_patch, \
             patch.object(transcriber, "WHISPER_CPP_TIMEOUT", 0):
            with self.assertRaisesRegex(ValueError, "WHISPER_CPP_TIMEOUT"):
                transcriber.validate_transcription_runtime()

    def test_worker_validator_delegates_to_transcription_runtime(self):
        with patch.object(transcriber, "validate_transcription_runtime", return_value=True) as validate:
            self.assertTrue(worker.validate_worker_runtime())
        validate.assert_called_once_with()

    def test_standalone_worker_validates_before_starting_runtime(self):
        with patch.object(worker, "validate_worker_runtime", side_effect=RuntimeError("missing runtime")), \
             patch("processing.worker_workspace.activate_worker_workspace") as workspace, \
             patch.object(worker.asyncio, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "missing runtime"):
                worker.standalone_worker()
        workspace.assert_not_called()
        run.assert_not_called()


class EmbeddedWorkerValidationTests(unittest.TestCase):
    def _capture_startup_hook(self, embedded):
        builder = MagicMock()
        builder.token.return_value = builder
        app = MagicMock()
        builder.build.return_value = app
        captured = {}

        def capture(hook):
            captured["hook"] = hook
            return builder

        builder.post_init.side_effect = capture
        with patch.object(telegram_bot, "REELFORGE_EMBEDDED_WORKER", embedded), \
             patch.object(telegram_bot, "REELFORGE_START_OAUTH_SERVER", False), \
             patch.object(telegram_bot, "ApplicationBuilder", return_value=builder):
            telegram_bot.run_bot()
        return builder, captured.get("hook")

    def test_embedded_worker_validates_during_worker_startup(self):
        _, startup_hook = self._capture_startup_hook(True)
        self.assertIsNotNone(startup_hook)

        async def no_jobs(**kwargs):
            return None

        with patch.object(telegram_bot, "validate_worker_runtime") as validate, \
             patch.object(telegram_bot, "worker_loop", side_effect=no_jobs):
            asyncio.run(startup_hook(MagicMock()))
        validate.assert_called_once_with()

    def test_embedded_worker_does_not_start_job_loop_when_validation_fails(self):
        _, startup_hook = self._capture_startup_hook(True)
        with patch.object(telegram_bot, "validate_worker_runtime", side_effect=RuntimeError("model missing")), \
             patch.object(telegram_bot, "worker_loop") as worker_loop:
            with self.assertRaisesRegex(RuntimeError, "model missing"):
                asyncio.run(startup_hook(MagicMock()))
        worker_loop.assert_not_called()

    def test_bot_only_mode_does_not_register_worker_runtime_validation(self):
        builder, startup_hook = self._capture_startup_hook(False)
        self.assertIsNone(startup_hook)
        builder.post_init.assert_not_called()

        with patch.object(telegram_bot, "validate_worker_runtime") as validate:
            # run_bot() was bot-only, so its lifecycle never invokes the worker hook.
            validate.assert_not_called()


class DockerPackagingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        cls.ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        cls.env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        cls.worker_env = (ROOT / "WORKER.env.example").read_text(encoding="utf-8")

    def test_docker_build_pins_official_cpu_whisper_cpp_and_model_checksum(self):
        self.assertIn("https://github.com/ggml-org/whisper.cpp.git", self.dockerfile)
        self.assertIn("d1be6fde11ac6e0407606b4e42fe72d34add8037", self.dockerfile)
        self.assertIn("--target whisper-cli", self.dockerfile)
        self.assertIn("GGML_CUDA=OFF", self.dockerfile)
        self.assertIn("GGML_OPENMP=ON", self.dockerfile)
        self.assertIn("ggml-base.bin", self.dockerfile)
        self.assertNotIn("ggml-base.en.bin", self.dockerfile)
        self.assertIn("465707469ff3a37a2b9b8d8f89f2f99de7299dac", self.dockerfile)
        self.assertIn("sha1sum --check", self.dockerfile)

    def test_runtime_image_keeps_ffmpeg_and_openmp_but_not_torch(self):
        self.assertIn("ffmpeg", self.dockerfile)
        self.assertIn("libgomp1", self.dockerfile)
        self.assertNotIn("pip install --index-url https://download.pytorch.org", self.dockerfile)
        self.assertIn("WHISPER_CPP_MODEL_PATH=/opt/reelforge/models/ggml-base.bin", self.dockerfile)
        self.assertIn("FROM runtime-base AS runtime", self.dockerfile)

    def test_model_and_build_context_are_not_mixed_with_ephemeral_workspace(self):
        self.assertIn("/opt/reelforge/models/ggml-base.bin", self.dockerfile)
        self.assertNotIn("WORKSPACE_DIR", self.dockerfile)
        for ignored in (".env", "atlas-credentials.env", "reels/", "brains/", "tests/", "docs/", ".reelforge/"):
            self.assertIn(ignored, self.ignore)

    def test_environment_templates_share_all_whisper_cpp_names(self):
        expected = {
            "WHISPER_CPP_BIN",
            "WHISPER_CPP_MODEL_PATH",
            "WHISPER_CPP_THREADS",
            "WHISPER_CPP_TIMEOUT",
        }
        env_keys = {line.split("=", 1)[0] for line in self.env_example.splitlines() if "=" in line and not line.lstrip().startswith("#")}
        worker_keys = {line.split("=", 1)[0] for line in self.worker_env.splitlines() if "=" in line and not line.lstrip().startswith("#")}
        config_keys = {
            name for name in expected
            if f'"{name}"' in (ROOT / "config.py").read_text(encoding="utf-8")
        }
        self.assertEqual(expected, env_keys & expected)
        self.assertEqual(expected, worker_keys & expected)
        self.assertEqual(expected, config_keys)


if __name__ == "__main__":
    unittest.main()
