"""ReelForge V3.5 — Layer 3: job status / lifecycle observability tests.

Covers:
- authorized job retrieval (storage layer): own job vs another user's job,
  unknown job ids, secondary requesters
- lifecycle timestamps + attempts + lease hygiene across queued -> processing
  -> completed | failed, including lease recovery
- /job and /jobs Telegram command behavior (status card, failure reason,
  generic unauthorized reply, listing, registration in both worker modes)
- embedded + independent worker compatibility via the shared storage path
- existing notification behavior remains intact
"""
import asyncio
import datetime
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import bot.telegram_bot as tb
import processing.worker as worker_mod
import storage.job as job_mod

from tests.test_v35_layer1_jobs import CLAIM, NOW, URL, FakeCollection

LATER = NOW + datetime.timedelta(minutes=30)


def _make_col():
    col = FakeCollection()
    col.add_unique_partial(
        "idx_job_claim_active_unique",
        ("claim_key", "active"),
        lambda d: d.get("active") is True,
    )
    return col


def _ok_result():
    return {
        "success": True, "cached": False, "onenote_success": True,
        "onenote_error": None, "category": "Food", "error": None,
        "brain": {"id": "ABc123XYZ", "knowledge": {"category": "Food"}},
        "brain_path": None, "transcript": "", "knowledge": {},
    }


def _msg_update():
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    msg.text = ""
    upd = MagicMock()
    upd.effective_user = MagicMock()
    upd.effective_user.id = 100
    upd.effective_chat = MagicMock()
    upd.callback_query = None
    upd.message = msg
    return upd


def _user_context(user_id="usr_1"):
    ctx = MagicMock()
    ctx.args = []
    return ctx


# ---------------------------------------------------------------------------
# Storage: authorized retrieval + lifecycle
# ---------------------------------------------------------------------------

class TestJobStatusAndAccess(unittest.TestCase):
    def setUp(self):
        self.col = _make_col()

    def test_submit_then_status_queued(self):
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        stored = job_mod.get_job_for_user(job["_id"], "usr_1", col=self.col)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["status"], job_mod.STATUS_QUEUED)
        self.assertEqual(stored["timestamps"]["created_at"], NOW)
        self.assertEqual(stored["timestamps"]["finished_at"], None)
        self.assertIsNone(stored["lease"]["expires_at"])

    def test_claim_sets_processing_timestamps_and_lease(self):
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        job_mod.claim_next_job("w1", col=self.col, now=NOW)
        stored = job_mod.get_job_for_user(job["_id"], "usr_1", col=self.col)
        self.assertEqual(stored["status"], job_mod.STATUS_PROCESSING)
        self.assertEqual(stored["timestamps"]["started_at"], NOW)
        self.assertEqual(stored["claimed_by"], "w1")
        self.assertEqual(stored["lease"]["expires_at"], NOW + datetime.timedelta(seconds=900))
        self.assertEqual(stored["attempts"], 1)

    def test_complete_job_timestamps_and_lease_cleanup(self):
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        job_mod.claim_next_job("w1", col=self.col, now=NOW)
        ok = job_mod.complete_job(job["_id"], {"success": True}, col=self.col, now=LATER)
        self.assertTrue(ok)
        stored = job_mod.get_job_for_user(job["_id"], "usr_1", col=self.col)
        self.assertEqual(stored["status"], job_mod.STATUS_COMPLETED)
        self.assertFalse(stored["active"])
        self.assertEqual(stored["timestamps"]["finished_at"], LATER)
        self.assertIsNone(stored["lease"]["expires_at"])
        self.assertIsNone(stored["claimed_by"])
        self.assertEqual(stored["attempts"], 1)

    def test_fail_job_timestamps_and_lease_cleanup(self):
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        job_mod.claim_next_job("w1", col=self.col, now=NOW)
        ok = job_mod.fail_job(job["_id"], "categorization failed", col=self.col, now=LATER)
        self.assertTrue(ok)
        stored = job_mod.get_job_for_user(job["_id"], "usr_1", col=self.col)
        self.assertEqual(stored["status"], job_mod.STATUS_FAILED)
        self.assertFalse(stored["active"])
        self.assertEqual(stored["timestamps"]["finished_at"], LATER)
        self.assertIsNone(stored["lease"]["expires_at"])
        self.assertIsNone(stored["claimed_by"])
        self.assertEqual(stored["error"], "categorization failed")
        self.assertIsNotNone(stored, "failed jobs must remain inspectable, not deleted")

    def test_lease_recovery_requeues_and_attempts_visible(self):
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        job_mod.claim_next_job("w-dead", col=self.col, now=NOW)
        later = NOW + datetime.timedelta(seconds=1800)
        actions = job_mod.recover_stale_jobs(col=self.col, now=later)
        self.assertEqual([a["action"] for a in actions], ["requeued"])
        stored = job_mod.get_job_for_user(job["_id"], "usr_1", col=self.col)
        self.assertEqual(stored["status"], job_mod.STATUS_QUEUED)
        self.assertTrue(stored["recovered"])
        self.assertEqual(stored["attempts"], 1, "attempt count persists across recovery")
        self.assertIsNone(stored["claimed_by"])
        self.assertIsNone(stored["lease"]["expires_at"])
        claimed_again = job_mod.claim_next_job("w-fresh", col=self.col, now=later)
        self.assertEqual(claimed_again["attempts"], 2, "recovery attempt bump is visible")

    def test_lease_expiry_max_attempts_marks_failed(self):
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        t = NOW
        for _ in range(job_mod.DEFAULT_MAX_ATTEMPTS):
            job_mod.claim_next_job("w", col=self.col, now=t)
            t += datetime.timedelta(seconds=1800)
            job_mod.recover_stale_jobs(col=self.col, now=t)
        stored = job_mod.get_job_for_user(job["_id"], "usr_1", col=self.col)
        self.assertEqual(stored["status"], job_mod.STATUS_FAILED)
        self.assertEqual(stored["error"], "lease expired after max attempts")
        self.assertEqual(stored["attempts"], job_mod.DEFAULT_MAX_ATTEMPTS)

    def test_updated_at_bumped_on_merged_requester(self):
        j1, _ = job_mod.submit_job(CLAIM, URL, "usr_1", chat_id=1, col=self.col, now=NOW)
        j2, dup = job_mod.submit_job(CLAIM, URL, "usr_2", chat_id=2, col=self.col, now=LATER)
        self.assertTrue(dup)
        stored = job_mod.get_job(j1["_id"], col=self.col)
        self.assertEqual(stored["timestamps"]["updated_at"], LATER)
        self.assertEqual(sorted(stored["requested_by"]), ["usr_1", "usr_2"])

    def test_user_can_inspect_own_job(self):
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        self.assertIsNotNone(job_mod.get_job_for_user(job["_id"], "usr_1", col=self.col))

    def test_secondary_requester_can_inspect_job(self):
        j1, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        job_mod.submit_job(CLAIM, URL, "usr_2", col=self.col, now=NOW)
        self.assertIsNotNone(job_mod.get_job_for_user(j1["_id"], "usr_2", col=self.col))

    def test_user_cannot_inspect_other_users_job(self):
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        self.assertIsNone(job_mod.get_job_for_user(job["_id"], "usr_9", col=self.col))

    def test_anonymous_cannot_inspect(self):
        job, _ = job_mod.submit_job(CLAIM, URL, None, col=self.col, now=NOW)
        self.assertIsNone(job_mod.get_job_for_user(job["_id"], None, col=self.col))

    def test_unknown_job_id(self):
        self.assertIsNone(job_mod.get_job_for_user("job_nope", "usr_1", col=self.col))

    def test_list_jobs_for_user_only_own_jobs(self):
        job_a, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        job_mod.submit_job("OTHER", "https://instagram.com/reel/OTHER/", "usr_2", col=self.col, now=NOW)
        lst = job_mod.list_jobs_for_user("usr_1", col=self.col)
        self.assertEqual([j["_id"] for j in lst], [job_a["_id"]])

    def test_list_jobs_for_user_includes_secondary(self):
        job_a, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        job_mod.submit_job(CLAIM, URL, "usr_2", col=self.col, now=NOW)
        lst = job_mod.list_jobs_for_user("usr_2", col=self.col)
        self.assertEqual([j["_id"] for j in lst], [job_a["_id"]])

    def test_list_jobs_for_user_status_filter_and_order(self):
        job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        j2, _ = job_mod.submit_job("BBB", "https://instagram.com/reel/BBB/", "usr_1", col=self.col, now=LATER)
        all_jobs = job_mod.list_jobs_for_user("usr_1", col=self.col)
        self.assertEqual(all_jobs[0]["_id"], j2["_id"], "newest first")
        queued = job_mod.list_jobs_for_user("usr_1", status=job_mod.STATUS_QUEUED, col=self.col)
        self.assertEqual(len(queued), 2)
        job_mod.claim_next_job("w1", col=self.col, now=LATER)
        queued = job_mod.list_jobs_for_user("usr_1", status=job_mod.STATUS_QUEUED, col=self.col)
        self.assertEqual(len(queued), 1)


# ---------------------------------------------------------------------------
# Bot commands
# ---------------------------------------------------------------------------

class TestJobCommand(unittest.TestCase):
    def _run(self, handler, update, ctx):
        asyncio.run(handler(update, ctx))
        return update.message.reply_text.call_args[0][0]

    def test_job_command_no_args_shows_usage(self):
        update = _msg_update()
        ctx = _user_context()
        with patch.object(tb, "_resolve_user", return_value=({"user_id": "usr_1"}, False)):
            reply = self._run(tb.job_command, update, ctx)
        self.assertIn("Please provide a job ID", reply)

    def test_job_command_shows_status_card(self):
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=_make_col(), now=NOW)
        ctx = _user_context()
        ctx.args = [job["_id"]]
        update = _msg_update()
        with patch.object(tb, "_resolve_user", return_value=({"user_id": "usr_1"}, False)), \
             patch.object(tb, "get_job_for_user", return_value=job):
            reply = self._run(tb.job_command, update, ctx)
        self.assertIn(f"Job: `{job['_id']}`", reply)
        self.assertIn("Status: ⏳ queued", reply)
        self.assertIn("Type: reel", reply)
        self.assertIn("Attempts: 0 / 3", reply)
        self.assertIn("Created:", reply)
        self.assertNotIn("token", " ".join(reply.lower().split(":")))

    def test_job_command_failed_shows_error(self):
        col = _make_col()
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=col, now=NOW)
        job_mod.claim_next_job("w1", col=col, now=NOW)
        job_mod.fail_job(job["_id"], "downloaded media corrupted", col=col, now=LATER)
        failed = job_mod.get_job_for_user(job["_id"], "usr_1", col=col)
        ctx = _user_context()
        ctx.args = [failed["_id"]]
        update = _msg_update()
        with patch.object(tb, "_resolve_user", return_value=({"user_id": "usr_1"}, False)), \
             patch.object(tb, "get_job_for_user", return_value=failed):
            reply = self._run(tb.job_command, update, ctx)
        self.assertIn("Status: ❌ failed", reply)
        self.assertIn("Error: downloaded media corrupted", reply)
        self.assertIn("Finished:", reply)

    def test_job_command_recovered_attempt_marker_shown(self):
        col = _make_col()
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=col, now=NOW)
        job_mod.claim_next_job("w-dead", col=col, now=NOW)
        later = NOW + datetime.timedelta(seconds=1800)
        job_mod.recover_stale_jobs(col=col, now=later)
        requeued = job_mod.get_job_for_user(job["_id"], "usr_1", col=col)
        ctx = _user_context()
        ctx.args = [job["_id"]]
        update = _msg_update()
        with patch.object(tb, "_resolve_user", return_value=({"user_id": "usr_1"}, False)), \
             patch.object(tb, "get_job_for_user", return_value=requeued):
            reply = self._run(tb.job_command, update, ctx)
        self.assertIn("Attempts: 1 / 3", reply)
        self.assertIn("Recovered after worker interruption", reply)

    def test_job_command_unauthorized_is_generic(self):
        ctx = _user_context()
        ctx.args = ["job_secret"]
        update = _msg_update()
        with patch.object(tb, "_resolve_user", return_value=({"user_id": "usr_1"}, False)), \
             patch.object(tb, "get_job_for_user", return_value=None):
            reply = self._run(tb.job_command, update, ctx)
        self.assertIn("not found or you are not authorized", reply)
        self.assertNotIn("Status:", reply)

    def test_job_command_unresolved_user(self):
        update = _msg_update()
        ctx = _user_context()
        ctx.args = ["job_secret"]
        with patch.object(tb, "_resolve_user", return_value=(None, False)):
            reply = self._run(tb.job_command, update, ctx)
        self.assertIn("Could not resolve your account", reply)


class TestJobsCommand(unittest.TestCase):
    def _run(self, handler, update, ctx):
        asyncio.run(handler(update, ctx))
        return update.message.reply_text.call_args[0][0]

    def test_jobs_command_empty(self):
        update = _msg_update()
        ctx = _user_context()
        with patch.object(tb, "_resolve_user", return_value=({"user_id": "usr_1"}, False)), \
             patch.object(tb, "list_jobs_for_user", return_value=[]):
            reply = self._run(tb.jobs_command, update, ctx)
        self.assertIn("no recent jobs", reply)

    def test_jobs_command_lists_recent_for_user(self):
        col = _make_col()
        j1, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=col, now=NOW)
        j2, _ = job_mod.submit_job("BBB", "https://instagram.com/reel/BBB/", "usr_1", col=col, now=LATER)
        ctx = _user_context()
        ctx.args = []
        update = _msg_update()
        with patch.object(tb, "_resolve_user", return_value=({"user_id": "usr_1"}, False)), \
             patch.object(tb, "list_jobs_for_user", return_value=[j2, j1]):
            reply = self._run(tb.jobs_command, update, ctx)
        self.assertIn(j2["_id"], reply)
        self.assertIn(j1["_id"], reply)
        self.assertIn("queued", reply)

    def test_jobs_command_does_not_leak_other_user_jobs(self):
        # The storage layer already filters; the command must render only what
        # it is given and never call the un-scoped get_job().
        ctx = _user_context()
        ctx.args = []
        update = _msg_update()
        with patch.object(tb, "_resolve_user", return_value=({"user_id": "usr_1"}, False)), \
             patch.object(tb, "list_jobs_for_user", return_value=[]) as mock_list:
            self._run(tb.jobs_command, update, ctx)
        mock_list.assert_called_once_with("usr_1", limit=5)


class TestCommandRegistration(unittest.TestCase):
    def _registered(self):
        ab = MagicMock()
        ab.token.return_value = ab
        ab.post_init.return_value = ab
        ab.post_shutdown.return_value = ab
        app = MagicMock()
        ab.build.return_value = app
        with patch.object(tb, "ApplicationBuilder", return_value=ab):
            tb.run_bot()
        registered = set()
        for call in app.add_handler.call_args_list:
            handler = call[0][0]
            for cmd in getattr(handler, "commands", []):
                registered.add(str(cmd))
        return registered

    def test_job_commands_registered_in_embedded_mode(self):
        with patch.object(tb, "REELFORGE_EMBEDDED_WORKER", True):
            registered = self._registered()
        self.assertIn("job", registered)
        self.assertIn("jobs", registered)

    def test_job_commands_registered_in_standalone_mode(self):
        with patch.object(tb, "REELFORGE_EMBEDDED_WORKER", False):
            registered = self._registered()
        self.assertIn("job", registered)
        self.assertIn("jobs", registered)


# ---------------------------------------------------------------------------
# Worker compatibility + notifications intact
# ---------------------------------------------------------------------------

class TestWorkerLifecycleCompatibility(unittest.TestCase):
    def setUp(self):
        self.col = _make_col()

    def test_worker_completes_job_and_status_accessible(self):
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        result = worker_mod.process_next_job(
            "standalone-1",
            process_fn=lambda *a, **k: _ok_result(),
            col=self.col,
            now=LATER,
        )
        self.assertTrue(result["success"])
        stored = job_mod.get_job_for_user(job["_id"], "usr_1", col=self.col)
        self.assertEqual(stored["status"], job_mod.STATUS_COMPLETED)
        self.assertEqual(stored["timestamps"]["finished_at"], LATER)

    def test_worker_failure_left_inspectable(self):
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        worker_mod.process_next_job(
            "standalone-1",
            process_fn=lambda *a, **k: {"success": False, "error": "yt-dlp dead"},
            col=self.col,
            now=LATER,
        )
        stored = job_mod.get_job_for_user(job["_id"], "usr_1", col=self.col)
        self.assertIsNotNone(stored, "failed job must remain inspectable")
        self.assertEqual(stored["status"], job_mod.STATUS_FAILED)
        self.assertIn("yt-dlp dead", stored["error"])

    def test_notifications_remain_intact_after_status_flow(self):
        class Rec:
            def __init__(self):
                self.progress_messages = []
                self.final_results = []
            def progress(self, text):
                self.progress_messages.append(text)
            def final(self, result):
                self.final_results.append(result)

        rec = Rec()
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)

        def stub(url, progress, force, user_id):
            progress("Downloading")
            return _ok_result()

        worker_mod.process_next_job(
            "standalone-1",
            make_notifier=lambda j: rec,
            process_fn=stub,
            col=self.col,
            now=LATER,
        )
        self.assertEqual(rec.progress_messages, ["Downloading"])
        self.assertEqual(len(rec.final_results), 1)
        self.assertTrue(rec.final_results[0]["success"])
        self.assertEqual(job_mod.get_job_for_user(job["_id"], "usr_1", col=self.col)["status"],
                         job_mod.STATUS_COMPLETED)

    def test_embedded_path_same_status_functions(self):
        # The embedded worker uses the exact same storage helpers as the
        # standalone worker; the status card renders from those docs.
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        status_text = tb._format_job_status(job)
        self.assertIn("queued", status_text)
        self.assertIn(job["_id"], status_text)


if __name__ == "__main__":
    unittest.main()