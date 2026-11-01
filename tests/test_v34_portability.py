"""
tests/test_v34_portability.py
-------------------------------
ReelForge V3.4 portability layer tests:

1. Safe whisper.cpp subprocess construction, output parsing, timeout handling,
   per-call temporary-file cleanup, and concurrent-call isolation.
2. Project-root-anchored path resolution (independent of process CWD),
   with environment-variable overrides preserved.

These tests never download Whisper models or process real audio: subprocesses
and model/binary availability are mocked.
"""

import importlib
import os
import tempfile
import threading
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


class WhisperCppAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.video = root / "clip.mp4"
        self.video.write_bytes(b"mock video")
        self.model = root / "ggml-base.bin"
        self.model.write_bytes(b"mock model")

    def _process(self, calls, output="  Hello from whisper.cpp  ", fail_at=None,
                 timeout_at=None):
        class FakeProcess:
            def __init__(self, args):
                self.args = args
                self.returncode = 0
                self.terminated = False
                self.killed = False

            def communicate(inner, timeout=None):
                calls.append((inner.args, timeout, inner))
                if timeout_at == len(calls):
                    raise transcriber.subprocess.TimeoutExpired(inner.args, timeout)
                if fail_at == len(calls):
                    inner.returncode = 7
                elif "-otxt" in inner.args:
                    prefix = Path(inner.args[inner.args.index("-of") + 1])
                    prefix.with_suffix(".txt").write_text(output, encoding="utf-8")
                else:
                    Path(inner.args[-1]).write_bytes(b"wav")
                return "sensitive output", "sensitive diagnostics"

            def terminate(inner):
                inner.terminated = True
                inner.returncode = -15

            def kill(inner):
                inner.killed = True
                inner.returncode = -9

        def popen(args, **kwargs):
            self.assertIsInstance(args, list)
            self.assertFalse(kwargs["shell"])
            return FakeProcess(args)

        return popen

    def _configured(self):
        return (
            mock.patch.object(transcriber, "WHISPER_CPP_MODEL_PATH", str(self.model)),
            mock.patch.object(transcriber.shutil, "which", side_effect=lambda name: name),
        )

    def test_ffmpeg_and_whisper_cli_arguments_and_transcript_file_parsing(self):
        calls = []
        p_model, p_which = self._configured()
        with p_model, p_which, mock.patch.object(
            transcriber.subprocess, "Popen", side_effect=self._process(calls)
        ):
            result = transcriber.transcribe_reel(self.video)

        self.assertEqual(result, "Hello from whisper.cpp")
        ffmpeg_args, _, _ = calls[0]
        self.assertEqual(ffmpeg_args[0], "ffmpeg")
        self.assertEqual(ffmpeg_args[ffmpeg_args.index("-ac") + 1], "1")
        self.assertEqual(ffmpeg_args[ffmpeg_args.index("-ar") + 1], "16000")
        self.assertEqual(ffmpeg_args[ffmpeg_args.index("-c:a") + 1], "pcm_s16le")
        cli_args, _, _ = calls[1]
        self.assertEqual(cli_args[0], "whisper-cli")
        self.assertEqual(cli_args[cli_args.index("-m") + 1], str(self.model.resolve()))
        self.assertEqual(cli_args[cli_args.index("-t") + 1], "2")
        self.assertIn("-otxt", cli_args)
        self.assertIn("-nt", cli_args)
        self.assertIn("-np", cli_args)
        self.assertEqual(Path(cli_args[cli_args.index("-f") + 1]).suffix, ".wav")
        self.assertFalse(Path(ffmpeg_args[-1]).parent.exists())

    def test_empty_transcript_is_returned_as_empty_string(self):
        calls = []
        p_model, p_which = self._configured()
        with p_model, p_which, mock.patch.object(
            transcriber.subprocess, "Popen", side_effect=self._process(calls, output="  \n")
        ):
            self.assertEqual(transcriber.transcribe_reel(self.video), "")

    def test_missing_input_binary_or_model_is_rejected(self):
        with self.assertRaises(FileNotFoundError):
            transcriber.transcribe_reel(self.video.parent / "missing.mp4")

        with mock.patch.object(transcriber, "WHISPER_CPP_MODEL_PATH", str(self.model)), \
             mock.patch.object(transcriber.shutil, "which", return_value=None):
            with self.assertRaisesRegex(FileNotFoundError, "FFmpeg"):
                transcriber.transcribe_reel(self.video)

        with mock.patch.object(transcriber, "WHISPER_CPP_MODEL_PATH", ""), \
             mock.patch.object(transcriber.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "WHISPER_CPP_MODEL_PATH"):
                transcriber.transcribe_reel(self.video)
            popen.assert_not_called()

        with mock.patch.object(transcriber, "WHISPER_CPP_MODEL_PATH", str(self.video.parent / "absent" / "ggml-base.bin")):
            with self.assertRaises(FileNotFoundError):
                transcriber.transcribe_reel(self.video)

        with mock.patch.object(transcriber, "WHISPER_CPP_MODEL_PATH", str(self.model)), \
             mock.patch.object(transcriber.shutil, "which", side_effect=lambda name: None if name == "whisper-cli" else name):
            with self.assertRaisesRegex(FileNotFoundError, "whisper.cpp"):
                transcriber.transcribe_reel(self.video)

    def test_ffmpeg_failure_cleans_temp_directory(self):
        calls = []
        p_model, p_which = self._configured()
        with p_model, p_which, mock.patch.object(
            transcriber.subprocess, "Popen", side_effect=self._process(calls, fail_at=1)
        ):
            with self.assertRaisesRegex(RuntimeError, "FFmpeg.*exit code 7"):
                transcriber.transcribe_reel(self.video)
        temp_wav = Path(calls[0][0][-1])
        self.assertFalse(temp_wav.parent.exists())

    def test_cli_failure_cleans_temp_directory(self):
        calls = []
        p_model, p_which = self._configured()
        with p_model, p_which, mock.patch.object(
            transcriber.subprocess, "Popen", side_effect=self._process(calls, fail_at=2)
        ):
            with self.assertRaisesRegex(RuntimeError, "whisper.cpp.*exit code 7"):
                transcriber.transcribe_reel(self.video)
        self.assertFalse(Path(calls[0][0][-1]).parent.exists())

    def test_timeout_terminates_and_reaps_child_and_cleans_files(self):
        calls = []
        p_model, p_which = self._configured()
        with p_model, p_which, mock.patch.object(
            transcriber.subprocess, "Popen", side_effect=self._process(calls, timeout_at=1)
        ):
            with self.assertRaisesRegex(TimeoutError, "FFmpeg"):
                transcriber.transcribe_reel(self.video)
        self.assertTrue(calls[0][2].terminated)
        self.assertFalse(Path(calls[0][0][-1]).parent.exists())

    def test_timeout_kills_child_when_termination_does_not_finish(self):
        calls = []
        process = mock.Mock()
        process.returncode = None
        process.communicate.side_effect = [
            transcriber.subprocess.TimeoutExpired(["ffmpeg"], 300),
            transcriber.subprocess.TimeoutExpired(["ffmpeg"], 5),
            ("", ""),
        ]
        p_model, p_which = self._configured()
        with p_model, p_which, mock.patch.object(
            transcriber.subprocess, "Popen", return_value=process
        ):
            with self.assertRaises(TimeoutError):
                transcriber.transcribe_reel(self.video)
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.communicate.call_count, 3)

    def test_concurrent_calls_use_distinct_temporary_directories(self):
        paths = []
        lock = threading.Lock()

        def popen(args, **kwargs):
            class Process:
                returncode = 0
                def communicate(self, timeout=None):
                    if "-otxt" in args:
                        Path(args[args.index("-of") + 1] + ".txt").write_text("ok", encoding="utf-8")
                    else:
                        Path(args[-1]).write_bytes(b"wav")
                    with lock:
                        paths.append(Path(args[-1] if "-otxt" not in args else args[args.index("-of") + 1]).parent)
                    return "", ""
            return Process()

        p_model, p_which = self._configured()
        with p_model, p_which, mock.patch.object(transcriber.subprocess, "Popen", side_effect=popen):
            results = []
            threads = [threading.Thread(target=lambda: results.append(transcriber.transcribe_reel(self.video)))
                       for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(results, ["ok", "ok"])
        self.assertEqual(len(set(paths)), 2)
        self.assertTrue(all(not path.exists() for path in paths))


if __name__ == "__main__":
    unittest.main()
