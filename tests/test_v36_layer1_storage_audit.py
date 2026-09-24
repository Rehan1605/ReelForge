"""
ReelForge V3.6 Layer 1 — Media / Storage Lifecycle Audit tests.

AUDIT-ONLY layer. These tests pin down lifecycle facts that ALREADY exist in
the codebase (no new behavior):

- Workspace / brains default locations and the KEEP_VIDEOS default.
- yt-dlp media artifacts (mp4 / thumbnail / info.json) live in WORKSPACE_DIR
  and are removed per-reel by the pipeline (never touching brain files).
- clear_workspace() removes only top-level FILES, never directories.
- create_brain_object stores worker-local media paths in the Brain Object and
  writes an atomic local JSON backup (Mongo-primary via _get_brains_col()).
- get_cached_brain_object falls back to the local Brain JSON when Mongo is
  unavailable, still honoring ownership archive state.
- A pipeline cache hit works even when the local brain file is absent
  (Mongo remains the authority; the file is a backup/fallback).
- Vision analysis frame temp dirs are cleaned on both failure and success paths.
- _atomic_write_json leaves no partial/_tmp_ files behind.
- Storage-related Docker/dotfile facts: reels/, brains/, secrets are excluded
  from the image; the container entrypoint is the Telegram bot.
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from config import BRAINS_DIR, CATEGORIES, KEEP_VIDEOS, ROOT_DIR, WORKSPACE_DIR


def _valid_brain(reel_id="AbC123", user_id=None):
    brain = {
        "id": reel_id,
        "status": "downloaded",
        "source": {
            "platform": "instagram",
            "url": f"https://www.instagram.com/reel/{reel_id}/",
            "shortcode": reel_id,
        },
        "creator": {"username": "tester", "full_name": "tester"},
        "metrics": {"views": 1, "plays": 1},
        "content": {"caption": "A caption", "transcript": "transcript", "vision_analysis": None},
        "media": {"video_path": "", "thumbnail_path": None, "duration": 30},
        "knowledge": {
            "category": "Programming",
            "summary": "A definite, meaningful summary of the reel content.",
            "tags": ["python"],
            "structured_data": {},
        },
        "timestamps": {"reel_created": "20250101", "processed_at": "2026-01-01T00:00:00"},
        "provenance": {"extraction_version": "2.6.0"},
        "ownership": {
            "created_by": user_id,
            "saved_by": list({user_id}) if user_id else [],
            "archived_by": [],
        },
    }
    return brain


class ConfigLifecycleTests(unittest.TestCase):
    """Where media and brain artifacts live by default (config.py)."""

    def test_workspace_dir_defaults_to_repo_reels(self):
        self.assertEqual(str(ROOT_DIR / "reels"), WORKSPACE_DIR)
        self.assertTrue(os.path.isabs(WORKSPACE_DIR))

    def test_brains_dir_defaults_to_repo_brains(self):
        self.assertEqual(str(ROOT_DIR / "brains"), BRAINS_DIR)
        self.assertTrue(os.path.isabs(BRAINS_DIR))

    def test_keep_videos_is_off_by_default(self):
        self.assertFalse(KEEP_VIDEOS)

    def test_categories_allow_programming_for_valid_brains(self):
        self.assertIn("Programming", CATEGORIES)


class WorkspaceClearLifecycleTests(unittest.TestCase):
    """clear_workspace() removes top-level files but never directories."""

    def test_clear_workspace_removes_files_but_not_subdirs(self):
        from download import downloader

        ws = Path(tempfile.mkdtemp(prefix="rf_audit_ws_"))
        try:
            (ws / "AbC.mp4").write_text("x", encoding="utf-8")
            (ws / "AbC.jpg").write_text("x", encoding="utf-8")
            sub = ws / "archive"
            sub.mkdir()
            (sub / "keep.json").write_text("{}", encoding="utf-8")

            with mock.patch.object(downloader, "effective_workspace", return_value=ws):
                downloader.clear_workspace()

            self.assertEqual(sorted(p.name for p in ws.iterdir()), ["archive"])
            self.assertTrue((sub / "keep.json").is_file())
        finally:
            shutil.rmtree(ws, ignore_errors=True)


class PerReelCleanupLifecycleTests(unittest.TestCase):
    """_cleanup_reel_media removes only that reel's files, never Brain Objects."""

    def _paths(self):
        ws = Path(tempfile.mkdtemp(prefix="rf_audit_ws_"))
        brains = Path(tempfile.mkdtemp(prefix="rf_audit_brains_"))
        return ws, brains

    def test_cleanup_removes_mp4_thumb_and_infojson_but_keeps_brain(self):
        from processing import pipeline

        ws, brains = self._paths()
        try:
            for name in ("AbC123.mp4", "AbC123.jpg", "AbC123.info.json", "Other.mp4"):
                (ws / name).write_text("x", encoding="utf-8")
            (brains / "AbC123.json").write_text("{}", encoding="utf-8")

            with mock.patch.object(pipeline, "effective_workspace", return_value=Path(ws)), mock.patch.object(
                pipeline, "KEEP_VIDEOS", False
            ):
                pipeline._cleanup_reel_media("AbC123")

            self.assertEqual(sorted(p.name for p in ws.iterdir()), ["Other.mp4"])
            self.assertTrue((brains / "AbC123.json").is_file())
        finally:
            shutil.rmtree(ws, ignore_errors=True)
            shutil.rmtree(brains, ignore_errors=True)

    def test_cleanup_skips_other_users_prefix_like_reels(self):
        from processing import pipeline

        ws, brains = self._paths()
        try:
            (ws / "AbC123.mp4").write_text("x", encoding="utf-8")
            (ws / "AbC1234.mp4").write_text("x", encoding="utf-8")

            with mock.patch.object(pipeline, "effective_workspace", return_value=Path(ws)), mock.patch.object(
                pipeline, "KEEP_VIDEOS", False
            ):
                pipeline._cleanup_reel_media("AbC123")

            self.assertEqual(sorted(p.name for p in ws.iterdir()), ["AbC1234.mp4"])
        finally:
            shutil.rmtree(ws, ignore_errors=True)
            shutil.rmtree(brains, ignore_errors=True)

    def test_cleanup_is_noop_when_keep_videos_true(self):
        from processing import pipeline

        ws, brains = self._paths()
        try:
            (ws / "AbC123.mp4").write_text("x", encoding="utf-8")
            with mock.patch.object(pipeline, "effective_workspace", return_value=Path(ws)), mock.patch.object(
                pipeline, "KEEP_VIDEOS", True
            ):
                pipeline._cleanup_reel_media("AbC123")
            self.assertEqual(sorted(p.name for p in ws.iterdir()), ["AbC123.mp4"])
        finally:
            shutil.rmtree(ws, ignore_errors=True)
            shutil.rmtree(brains, ignore_errors=True)


class CreateBrainObjectLifecycleTests(unittest.TestCase):
    """create_brain_object records worker-local media paths + local JSON backup."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="rf_audit_ws_"))
        self.brains = Path(tempfile.mkdtemp(prefix="rf_audit_brains_"))

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)
        shutil.rmtree(self.brains, ignore_errors=True)

    def test_records_workspace_media_paths_and_writes_local_backup(self):
        from storage import brain_object as sbo

        (self.ws / "AbC123.mp4").write_text("x", encoding="utf-8")
        (self.ws / "AbC123.jpg").write_text("x", encoding="utf-8")
        (self.ws / "AbC123.info.json").write_text("{}", encoding="utf-8")

        metadata = {
            "id": "AbC123",
            "webpage_url": "https://www.instagram.com/reel/AbC123/",
            "description": "Test reel caption",
            "uploader": "tester",
            "view_count": 10,
            "duration": 30,
            "upload_date": "20250101",
        }

        with mock.patch.object(sbo, "effective_workspace", return_value=self.ws), mock.patch.object(
            sbo, "BRAINS_DIR", str(self.brains)
        ), mock.patch.object(sbo, "_get_brains_col", return_value=None):
            brain = sbo.create_brain_object("https://www.instagram.com/reel/AbC123/", metadata)

        self.assertEqual(brain["id"], "AbC123")
        media = brain.get("media", {})
        self.assertEqual(media.get("video_path"), str(self.ws / "AbC123.mp4"))
        self.assertEqual(media.get("thumbnail_path"), str(self.ws / "AbC123.jpg"))

        backup = self.brains / "AbC123.json"
        self.assertTrue(backup.is_file())
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))["id"], "AbC123")

    def test_local_backup_is_atomic_no_tmp_remnants(self):
        from storage.brain_object import _atomic_write_json

        target = self.brains / "AbC123.json"
        _atomic_write_json(target, {"id": "AbC123", "ok": True})

        self.assertTrue(target.is_file())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["ok"], True)
        remnants = [p.name for p in self.brains.iterdir() if "_tmp_" in p.name]
        self.assertEqual(remnants, [])


class CacheFallbackLifecycleTests(unittest.TestCase):
    """Mongo is primary; the local Brain JSON is the offline/test fallback."""

    def setUp(self):
        self.brains = Path(tempfile.mkdtemp(prefix="rf_audit_brains_"))

    def tearDown(self):
        shutil.rmtree(self.brains, ignore_errors=True)

    def _write_local(self, reel_id, brain):
        path = self.brains / f"{reel_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        import storage.brain_object as sbo
        sbo._atomic_write_json(path, brain)

    def test_local_fallback_used_when_mongo_unavailable(self):
        from storage import brain_object as sbo

        brain = _valid_brain("AbC123")
        self._write_local("AbC123", brain)

        with mock.patch.object(sbo, "BRAINS_DIR", str(self.brains)), mock.patch.object(
            sbo, "_get_brains_col", return_value=None
        ):
            cached = sbo.get_cached_brain_object("AbC123")

        self.assertIsNotNone(cached)
        self.assertEqual(cached["id"], "AbC123")

    def test_local_fallback_honors_per_user_archive_state(self):
        from storage import brain_object as sbo

        brain = _valid_brain("AbC123", user_id="usr_1")
        brain["ownership"]["archived_by"] = ["usr_1"]
        self._write_local("AbC123", brain)

        with mock.patch.object(sbo, "BRAINS_DIR", str(self.brains)), mock.patch.object(
            sbo, "_get_brains_col", return_value=None
        ):
            hidden = sbo.get_cached_brain_object("AbC123", user_id="usr_1")
            visible = sbo.get_cached_brain_object("AbC123")

        self.assertIsNone(hidden)
        self.assertIsNotNone(visible)

    def test_cache_hit_without_local_file_proves_mongo_authority(self):
        from processing import pipeline

        brain = _valid_brain("AbC123")
        with mock.patch.object(
            pipeline, "get_cached_brain_object", return_value=brain
        ), mock.patch.object(pipeline, "BRAINS_DIR", str(self.brains)):
            result = pipeline.process_reel("https://www.instagram.com/reel/AbC123/")

        self.assertTrue(result["success"])
        self.assertTrue(result["cached"])
        expected_path = Path(self.brains) / "AbC123.json"
        self.assertEqual(Path(result["brain_path"]), expected_path)
        self.assertFalse(expected_path.exists())


class VisionTempLifecycleTests(unittest.TestCase):
    """Vision frame dirs (system temp) are cleaned on failure AND success."""

    def test_failure_path_cleans_temp_dir(self):
        from processing import vision_analyzer

        controlled = Path(tempfile.mkdtemp(prefix="rf_audit_controlled_"))
        controlled.mkdir(parents=True, exist_ok=True)

        def _fake_mkdtemp(*args, **kwargs):
            return str(controlled)

        with mock.patch.object(
            vision_analyzer, "sample_frames", side_effect=FileNotFoundError("nope")
        ), mock.patch.object(
            vision_analyzer.tempfile, "mkdtemp", side_effect=_fake_mkdtemp
        ):
            with self.assertRaises(FileNotFoundError):
                vision_analyzer.analyze_vision("/no/such/video.mp4")

        self.assertFalse(controlled.exists())

    def test_success_path_cleans_temp_dir(self):
        from processing import vision_analyzer

        controlled = Path(tempfile.mkdtemp(prefix="rf_audit_controlled_"))
        controlled.mkdir(parents=True, exist_ok=True)
        fake_frame = controlled / "frame_000.jpg"
        fake_frame.write_text("jpeg", encoding="utf-8")

        def _fake_mkdtemp(*args, **kwargs):
            return str(controlled)

        with mock.patch.object(
            vision_analyzer, "sample_frames", return_value=[fake_frame]
        ), mock.patch.object(
            vision_analyzer, "_build_prompt", return_value="prompt"
        ), mock.patch.object(
            vision_analyzer, "call_vision_model", return_value={"visual_summary": "ok"}
        ), mock.patch.object(
            vision_analyzer.tempfile, "mkdtemp", side_effect=_fake_mkdtemp
        ):
            result = vision_analyzer.analyze_vision("/no/such/video.mp4", update_brain=False)

        self.assertEqual(result, {"visual_summary": "ok"})
        self.assertFalse(controlled.exists())


class DockerCloudWorkerLifecycleTests(unittest.TestCase):
    """Fresh-container facts: local media/brains excluded from image."""

    def test_dockerignore_excludes_local_storage_and_secrets(self):
        content = (ROOT_DIR / ".dockerignore").read_text(encoding="utf-8")
        for token in ("reels/", "brains/", "scratch/", ".env", "atlas-credentials.env", "token_cache.bin"):
            self.assertIn(token, content)

    def test_dockerfile_entrypoint_is_the_telegram_bot(self):
        content = (ROOT_DIR / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("bot/telegram_bot.py", content)
        self.assertIn("CMD", content)

    def test_ops_doc_documents_media_as_ephemeral(self):
        content = (ROOT_DIR / "WORKER_OPERATIONS.md").read_text(encoding="utf-8")
        self.assertIn("Ephemeral, worker-local", content)
        self.assertIn("unique", content)

    def test_worker_env_documents_routing_nuance(self):
        content = (ROOT_DIR / "WORKER.env.example").read_text(encoding="utf-8")
        self.assertIn("unique per worker", content)
        self.assertIn("ends in '/brains'", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)