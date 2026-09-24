"""
tests/test_v34_portability.py
-------------------------------
ReelForge V3.4 portability layer tests:

1. Lazy Whisper model loading (no model/work download at import time,
   load-once reuse across transcriptions, configurable model cache dir).
2. Project-root-anchored path resolution (independent of process CWD),
   with environment-variable overrides preserved.

These tests never download Whisper models or process real audio: the
Whisper load path is fully mocked.
"""

import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import config as config_module
import processing.transcriber as transcriber


class PathResolutionTests(unittest.TestCase):
    def test_root_dir_is_repository_root(self):
        self.assertTrue(config_module.ROOT_DIR.is_absolute())
        self.assertEqual(config_module.ROOT_DIR, Path(config_module.__file__).resolve().parent)
        self.assertTrue((config_module.ROOT_DIR / "config.py").is_file())

    def test_project_dirs_are_absolute_and_anchored_to_root(self):
        for name in ("WORKSPACE_DIR", "BRAINS_DIR", "PROMPTS_DIR"):
            value = getattr(config_module, name)
            self.assertTrue(Path(value).is_absolute(), f"{name} must be absolute")
        if "WORKSPACE_DIR" not in os.environ:
            self.assertEqual(Path(config_module.WORKSPACE_DIR), config_module.ROOT_DIR / "reels")
        if "BRAINS_DIR" not in os.environ:
            self.assertEqual(Path(config_module.BRAINS_DIR), config_module.ROOT_DIR / "brains")
        if "PROMPTS_DIR" not in os.environ:
            self.assertEqual(Path(config_module.PROMPTS_DIR), config_module.ROOT_DIR / "prompts")

    def test_prompts_dir_exists(self):
        self.assertTrue(Path(config_module.PROMPTS_DIR).is_dir())

    def test_config_reload_from_foreign_cwd_stays_anchored_to_root(self):
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                reloaded = importlib.reload(config_module)
                self.assertEqual(reloaded.ROOT_DIR, Path(config_module.__file__).resolve().parent)
                self.assertTrue(Path(reloaded.WORKSPACE_DIR).is_absolute())
                self.assertTrue(Path(reloaded.BRAINS_DIR).is_absolute())
                self.assertTrue(Path(reloaded.PROMPTS_DIR).is_absolute())
                if "WORKSPACE_DIR" not in os.environ:
                    self.assertNotIn(
                        str(Path(tmp).resolve()),
                        str(Path(reloaded.WORKSPACE_DIR).resolve()),
                    )
            finally:
                os.chdir(original_cwd)
                importlib.reload(config_module)

    def test_env_variable_overrides_survive_reload(self):
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                with mock.patch.dict(os.environ, {"WORKSPACE_DIR": tmp}, clear=False):
                    reloaded = importlib.reload(config_module)
                    self.assertEqual(reloaded.WORKSPACE_DIR, tmp)
                    if "BRAINS_DIR" not in os.environ:
                        self.assertEqual(
                            Path(reloaded.BRAINS_DIR),
                            reloaded.ROOT_DIR / "brains",
                        )
            finally:
                os.chdir(original_cwd)
                importlib.reload(config_module)


class WhisperLazyLoadingTests(unittest.TestCase):
    def setUp(self):
        transcriber.reset_model_for_tests()

    def tearDown(self):
        transcriber.reset_model_for_tests()

    def _fake_video_file(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        video = Path(td.name) / "clip.mp4"
        video.write_bytes(b"not-a-real-video")
        return video

    def test_module_import_has_no_model_side_effects(self):
        self.assertIsNone(transcriber._model)
        self.assertFalse(hasattr(transcriber, "model"))
        self.assertFalse(hasattr(transcriber, "whisper"))

    def test_model_is_loaded_lazily_and_reused(self):
        fake_model = mock.Mock()
        fake_model.transcribe.return_value = {"text": "  Hello from whisper  "}
        video = self._fake_video_file()
        with mock.patch.object(
            transcriber, "_load_whisper_model", return_value=fake_model
        ) as mock_load:
            first = transcriber.transcribe_reel(video)
            second = transcriber.transcribe_reel(video)
        self.assertEqual(first, "Hello from whisper")
        self.assertEqual(second, "Hello from whisper")
        mock_load.assert_called_once_with()
        self.assertEqual(fake_model.transcribe.call_count, 2)

    def test_reset_model_for_tests_forces_reload(self):
        fake_model = mock.Mock()
        fake_model.transcribe.return_value = {"text": " x "}
        video = self._fake_video_file()
        with mock.patch.object(
            transcriber, "_load_whisper_model", return_value=fake_model
        ) as mock_load:
            transcriber.transcribe_reel(video)
            transcriber.reset_model_for_tests()
            transcriber.transcribe_reel(video)
        self.assertEqual(mock_load.call_count, 2)

    def test_transcribe_rejects_missing_file(self):
        missing = Path(tempfile.gettempdir()) / "definitely-missing-reelforge-xyz.mp4"
        with self.assertRaises(FileNotFoundError):
            transcriber.transcribe_reel(missing)

    def test_single_thread_lazy_load_never_duplicates(self):
        fake_model = mock.Mock()
        fake_model.transcribe.return_value = {"text": " y "}
        video = self._fake_video_file()
        with mock.patch.object(
            transcriber, "_load_whisper_model", return_value=fake_model
        ) as mock_load:
            results = [transcriber.transcribe_reel(video) for _ in range(3)]
        self.assertEqual(results, ["y", "y", "y"])
        mock_load.assert_called_once_with()

    def test_load_wires_configurable_cache_dir(self):
        fake_whisper = types.ModuleType("whisper")
        fake_whisper.load_model = mock.Mock(return_value=object())
        with mock.patch.dict(sys.modules, {"whisper": fake_whisper}):
            with mock.patch.object(
                transcriber, "WHISPER_CACHE_DIR", "/custom/whisper/cache"
            ):
                transcriber._load_whisper_model()
        fake_whisper.load_model.assert_called_once_with(
            transcriber.WHISPER_MODEL, download_root="/custom/whisper/cache"
        )

    def test_load_uses_default_cache_when_unset(self):
        fake_whisper = types.ModuleType("whisper")
        fake_whisper.load_model = mock.Mock(return_value=object())
        with mock.patch.dict(sys.modules, {"whisper": fake_whisper}):
            with mock.patch.object(transcriber, "WHISPER_CACHE_DIR", None):
                transcriber._load_whisper_model()
        fake_whisper.load_model.assert_called_once_with(
            transcriber.WHISPER_MODEL, download_root=None
        )

    def test_load_rejects_missing_whisper_runtime(self):
        with self.assertRaises(ImportError):
            with mock.patch.dict(
                sys.modules,
                {"whisper": None},
            ):
                with mock.patch.object(transcriber, "WHISPER_CACHE_DIR", None):
                    transcriber._load_whisper_model()


if __name__ == "__main__":
    unittest.main()