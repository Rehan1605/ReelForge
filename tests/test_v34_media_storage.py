"""
tests/test_v34_media_storage.py
----------------------------------
ReelForge V3.4 Layer 4 (media/storage independence) tests.

Focused on:
  1. Media workspace configurability (WORKSPACE_DIR) incl. Linux-style paths.
  2. FFmpeg lookup without laptop-specific directories on non-Windows workers.
  3. Per-reel media cleanup isolation (no cross-job deletion, numeric-prefix safe).
  4. KEEP_VIDEOS=False preserved.
  5. Telegram /get exports the Brain Object from memory (no local brains/ read).
  6. MongoDB remains the source of truth with the default brains dir.

No network calls are made; all HTTP/Mongo access is mocked or avoided.
"""

import asyncio
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config as config_module

ROOT_DIR = Path(__file__).resolve().parent.parent


class ConfigMediaTests(unittest.TestCase):
    def _restore(self, original_env):
        os.environ.clear()
        os.environ.update(original_env)
        importlib.reload(config_module)

    def _reload_without_dotenv(self):
        """Reload config with .env loading neutralized so these tests assert
        the code's own defaults/overrides rather than .env contents."""
        with mock.patch.object(os.environ, "setdefault", lambda *a, **k: None):
            return importlib.reload(config_module)

    def test_workspace_dir_env_override_linux_style(self):
        original_env = dict(os.environ)
        os.environ["WORKSPACE_DIR"] = "/tmp/reelforge/worker-42"
        try:
            reloaded = self._reload_without_dotenv()
            self.assertEqual(reloaded.WORKSPACE_DIR, "/tmp/reelforge/worker-42")
            # Must be plain (no drive/backslash conventions required)
            self.assertNotIn("\\", reloaded.WORKSPACE_DIR)
        finally:
            self._restore(original_env)

    def test_workspace_and_brains_dirs_root_anchored_by_default(self):
        original_env = dict(os.environ)
        os.environ.pop("WORKSPACE_DIR", None)
        os.environ.pop("BRAINS_DIR", None)
        try:
            reloaded = self._reload_without_dotenv()
            root = str(Path(reloaded.ROOT_DIR))
            self.assertEqual(reloaded.WORKSPACE_DIR, os.path.join(root, "reels"))
            self.assertEqual(reloaded.BRAINS_DIR, os.path.join(root, "brains"))
        finally:
            self._restore(original_env)

    def test_ffmpeg_path_env_override(self):
        original_env = dict(os.environ)
        os.environ["FFMPEG_PATH"] = "/opt/ffmpeg/bin"
        try:
            reloaded = self._reload_without_dotenv()
            self.assertEqual(reloaded.FFMPEG_PATH, "/opt/ffmpeg/bin")
        finally:
            self._restore(original_env)

    def test_ffmpeg_path_default_empty_on_linux(self):
        # Test the default-path function directly without reloading config:
        # reloading under a patched os.name breaks pathlib (PosixPath cannot
        # be instantiated on Windows), and the function reads os.name live.
        with mock.patch.object(os, "name", "posix"):
            self.assertEqual(config_module._default_ffmpeg_path(), "")
        if os.name == "nt":
            self.assertTrue(config_module._default_ffmpeg_path().strip())

    def test_ffmpeg_path_default_nonempty_on_windows(self):
        if os.name != "nt":
            self.skipTest("Windows-only default")
        original_env = dict(os.environ)
        os.environ.pop("FFMPEG_PATH", None)
        try:
            reloaded = self._reload_without_dotenv()
            self.assertTrue(reloaded.FFMPEG_PATH.strip())
        finally:
            self._restore(original_env)


class TranscriberPathTests(unittest.TestCase):
    """Verify the FFMPEG_PATH handling in processing/transcriber via subprocesses
    so the import-time side effect on os.environ['PATH'] is observed cleanly."""

    def _run(self, ffmpeg_path):
        env = dict(os.environ)
        if env.get("PYTHONPATH"):
            env["PYTHONPATH"] = str(ROOT_DIR) + os.pathsep + env["PYTHONPATH"]
        else:
            env["PYTHONPATH"] = str(ROOT_DIR)
        code = (
            "import os\n"
            f"os.environ['FFMPEG_PATH'] = {ffmpeg_path!r}\n"
            "os.environ['PATH'] = 'BASE_DIR'\n"
            "import processing.transcriber\n"
            "print('REPORT=' + os.environ['PATH'][:len(os.environ['PATH'])])\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
            cwd=str(ROOT_DIR),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_no_path_prefix_when_ffmpeg_path_empty(self):
        out = self._run("")
        self.assertEqual(out, "REPORT=BASE_DIR")

    def test_path_prefixes_custom_ffmpeg_dir(self):
        out = self._run("/opt/ffmpeg/bin")
        self.assertEqual(out, "REPORT=/opt/ffmpeg/bin" + os.pathsep + "BASE_DIR")


class CleanupIsolationTests(unittest.TestCase):
    def _make_workspace(self, names):
        ws = Path(tempfile.mkdtemp(prefix="reelforge_ws_"))
        for name in names:
            (ws / name).write_text("x", encoding="utf-8")
        return ws

    def test_per_reel_cleanup_is_prefix_safe(self):
        from processing.pipeline import _cleanup_reel_media

        ws = self._make_workspace(
            ["AbC.mp4", "AbC.jpg", "AbC.info.json", "AbC1.mp4", "xYz.mp4"]
        )
        try:
            with mock.patch("processing.pipeline.effective_workspace", return_value=ws), mock.patch(
                "processing.pipeline.KEEP_VIDEOS", False
            ):
                _cleanup_reel_media("AbC")
            remaining = sorted(p.name for p in ws.iterdir())
            self.assertEqual(remaining, ["AbC1.mp4", "xYz.mp4"])
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_keep_videos_true_preserves_media(self):
        from processing.pipeline import _cleanup_reel_media

        ws = self._make_workspace(["AbC.mp4", "AbC.jpg", "AbC.info.json"])
        try:
            with mock.patch("processing.pipeline.effective_workspace", return_value=ws), mock.patch(
                "processing.pipeline.KEEP_VIDEOS", True
            ):
                _cleanup_reel_media("AbC")
            remaining = sorted(p.name for p in ws.iterdir())
            self.assertEqual(remaining, ["AbC.info.json", "AbC.jpg", "AbC.mp4"])
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_cleanup_noop_without_reel_id(self):
        from processing.pipeline import _cleanup_reel_media

        ws = self._make_workspace(["AbC.mp4"])
        try:
            with mock.patch("processing.pipeline.effective_workspace", return_value=ws), mock.patch(
                "processing.pipeline.KEEP_VIDEOS", False
            ):
                _cleanup_reel_media(None)
            self.assertEqual(sorted(p.name for p in ws.iterdir()), ["AbC.mp4"])
        finally:
            shutil.rmtree(ws, ignore_errors=True)


class TelegramGetCommandTests(unittest.TestCase):
    """/get must export the Brain Object from memory, not read the local
    brains/ directory (removes the laptop-filesystem dependency)."""

    class FakeMessage:
        def __init__(self):
            self.sent = []

        async def reply_text(self, text):
            self.sent.append(("text", text))

        async def reply_document(self, document=None, filename=None, **kwargs):
            self.sent.append(("doc", document, filename))

    class FakeUpdate:
        def __init__(self):
            self.message = TelegramGetCommandTests.FakeMessage()

    class FakeContext:
        def __init__(self, args):
            self.args = args

    def test_get_serializes_brain_from_memory(self):
        from bot import telegram_bot

        brain = {
            "id": "AbC123",
            "source": {"url": "https://www.instagram.com/reel/AbC123/"},
            "knowledge": {"category": "AI", "summary": "Test summary", "tags": ["ai"]},
        }
        update = self.FakeUpdate()
        context = self.FakeContext(["AbC123"])

        with mock.patch.object(
            telegram_bot, "_resolve_user", return_value=(None, None)
        ) as mock_resolve, mock.patch.object(
            telegram_bot, "get_cached_brain_object", return_value=brain
        ) as mock_cache:
            asyncio.run(telegram_bot.get_command(update, context))

        mock_cache.assert_called_once_with("AbC123", user_id=None)
        docs = [s for s in update.message.sent if s[0] == "doc"]
        self.assertEqual(len(docs), 1)
        _, stream, filename = docs[0]
        self.assertEqual(filename, "AbC123.json")
        payload = json.loads(stream.read().decode("utf-8"))
        self.assertEqual(payload, brain)


class MongoSourceOfTruthTests(unittest.TestCase):
    """MongoDB stays the authoritative store with the default brains dir;
    custom dirs only trigger local-mode for test isolation."""

    def test_mongo_used_with_default_brains_dir(self):
        from storage import brain_object as sbo

        class FakeCol:
            def create_index(self, *args, **kwargs):
                return None

            def index_information(self):
                return {}

        col = FakeCol()
        with mock.patch.object(sbo, "get_collection", return_value=col), mock.patch.object(
            sbo, "_ensure_indexes", lambda c: None
        ):
            self.assertFalse(sbo._is_custom_brains_dir())
            self.assertIs(sbo._get_brains_col(), col)

    def test_custom_brains_dir_stays_local_mode(self):
        from storage import brain_object as sbo

        with mock.patch.object(sbo, "BRAINS_DIR", "/tmp/worker-brains"):
            self.assertTrue(sbo._is_custom_brains_dir())


if __name__ == "__main__":
    unittest.main()