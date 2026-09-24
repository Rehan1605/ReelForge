"""ReelForge V3.5 — Layer 2: independent worker process tests.

Proves:
- the bot can enqueue jobs without running an in-process worker,
- a standalone worker can claim/process jobs through the shared store,
- multiple workers cannot claim the same job,
- lease recovery still works,
- Telegram notifications remain correct (progress + final, no credential/state
  leakage into the worker),
- a failing job does not kill the worker loop (bot/worker isolation),
- single-process (embedded worker) mode remains the default and compatible.
"""
import asyncio
import datetime
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot import notifier as notifier_mod
from bot.notifier import JobNotifier, notifier_kind_for_job, result_summary_text
import bot.telegram_bot as tb
import processing.worker as worker_mod
import storage.job as job_mod

from tests.test_v35_layer1_jobs import CLAIM, NOW, URL, FakeCollection


class _RunningLoop:
    """Context manager running a real event loop on a background thread."""

    def __enter__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        return self.loop

    def __exit__(self, *exc):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)


def _ok_result(reel_id="ABc123XYZ", onenote_success=True, onenote_error=None):
    return {
        "success": True, "cached": False, "onenote_success": onenote_success,
        "onenote_error": onenote_error, "category": "Food", "error": None,
        "brain": {"id": reel_id, "knowledge": {"category": "Food", "summary": "s"},
                  "content": {"caption": "T"}, "source": {"shortcode": reel_id}},
        "brain_path": None, "transcript": "", "knowledge": {},
    }


def _make_col():
    col = FakeCollection()
    col.add_unique_partial(
        "idx_job_claim_active_unique",
        ("claim_key", "active"),
        lambda d: d.get("active") is True,
    )
    return col


# ---------------------------------------------------------------------------
# Embedded worker toggle
# ---------------------------------------------------------------------------

class TestEmbeddedWorkerToggle(unittest.TestCase):
    def test_embedded_worker_enabled_by_default(self):
        # Single-process mode stays the default for local development.
        import config
        self.assertTrue(config.REELFORGE_EMBEDDED_WORKER)

    def test_run_bot_starts_embedded_worker_when_enabled(self):
        ab = MagicMock()
        ab.token.return_value = ab
        ab.post_init.return_value = ab
        ab.post_shutdown.return_value = ab
        app = MagicMock()
        ab.build.return_value = app

        with (
            patch.object(tb, "REELFORGE_EMBEDDED_WORKER", True),
            patch.object(tb, "ApplicationBuilder", return_value=ab),
        ):
            tb.run_bot()

        ab.post_init.assert_called_once()
        ab.post_shutdown.assert_called_once()
        self.assertTrue(app.add_handler.called, "handlers must still be registered")

    def test_run_bot_runs_without_worker_when_disabled(self):
        ab = MagicMock()
        ab.token.return_value = ab
        app = MagicMock()
        ab.build.return_value = app

        with (
            patch.object(tb, "REELFORGE_EMBEDDED_WORKER", False),
            patch.object(tb, "ApplicationBuilder", return_value=ab),
        ):
            tb.run_bot()

        ab.post_init.assert_not_called()
        ab.post_shutdown.assert_not_called()
        self.assertTrue(app.add_handler.called, "handlers must still be registered")

    def test_receive_enqueues_without_worker(self):
        # The bot path only writes to MongoDB; no worker task is required to
        # accept and acknowledge a reel request.
        update = MagicMock()
        update.message = MagicMock()
        update.message.text = URL
        update.message.reply_text = AsyncMock()
        update.message.reply_document = AsyncMock()
        context = MagicMock()

        expected_job = {"_id": "job_bot_enqueue", "status": "queued"}
        with patch.object(tb, "get_cached_brain_object", return_value=None) as mock_cache, \
             patch.object(tb, "submit_job", return_value=(expected_job, False)) as mock_submit:
            asyncio.run(tb.receive(update, context))

        mock_cache.assert_called_once()
        mock_submit.assert_called_once()
        self.assertEqual(mock_submit.call_args.kwargs["claim_key"], "ABc123XYZ")
        reply = update.message.reply_text.call_args[0][0]
        self.assertIn("Queued", reply)


# ---------------------------------------------------------------------------
# Standalone worker notification
# ---------------------------------------------------------------------------

class TestStandaloneNotifier(unittest.TestCase):
    def test_notifier_kind_mapping(self):
        self.assertEqual(notifier_kind_for_job({"job_type": "force"}), "force")
        self.assertEqual(notifier_kind_for_job({"job_type": "reprocess"}), "reprocess")
        self.assertEqual(notifier_kind_for_job({"job_type": "reel"}), "fresh")
        self.assertEqual(notifier_kind_for_job({}), "fresh")

    def test_standalone_notifier_sends_to_all_chats(self):
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock())

        with _RunningLoop() as loop:
            notifier = JobNotifier(bot=bot, loop=loop, chats=[111, 222], kind="fresh")
            notifier.progress("Downloading reel...")

        self.assertEqual(bot.send_message.await_count, 2)
        sent_texts = [c.kwargs["text"] for c in bot.send_message.await_args_list]
        sent_chats = [c.kwargs["chat_id"] for c in bot.send_message.await_args_list]
        self.assertEqual(set(sent_chats), {111, 222})
        self.assertEqual(set(sent_texts), {"Downloading reel..."})

    def test_standalone_notifier_final_success_sends_summary_and_document(self):
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock())
        bot.send_document = AsyncMock(return_value=MagicMock())

        with TemporaryDirectory() as tmp:
            brain_id = "test_standalone_reel"
            brain_path = Path(tmp) / f"{brain_id}.json"
            brain_path.write_text(json.dumps({"id": brain_id}), encoding="utf-8")
            result = _ok_result(reel_id=brain_id)
            result["brain_path"] = str(brain_path)

            with _RunningLoop() as loop:
                notifier = JobNotifier(bot=bot, loop=loop, chats=[7], kind="fresh")
                notifier.final(result)

        self.assertEqual(bot.send_message.await_count, 1)
        text = bot.send_message.await_args_list[0].kwargs["text"]
        self.assertIn("OneNote Page Created", text)
        self.assertIn("InstaBrain Summary", text)
        self.assertEqual(bot.send_document.await_count, 1)
        self.assertEqual(bot.send_document.await_args_list[0].kwargs["filename"], f"{brain_id}.json")

    def test_standalone_notifier_final_failure_sends_error(self):
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock())

        with _RunningLoop() as loop:
            notifier = JobNotifier(bot=bot, loop=loop, chats=[7], kind="fresh")
            notifier.final({"success": False, "error": "categorization failed"})

        self.assertEqual(bot.send_message.await_count, 1)
        text = bot.send_message.await_args_list[0].kwargs["text"]
        self.assertIn("Processing failed", text)
        self.assertIn("categorization failed", text)

    def test_send_failure_is_swallowed(self):
        # A broken network/API must not raise out of the notifier.
        bot = MagicMock()
        bot.send_message = AsyncMock(side_effect=RuntimeError("Telegram API down"))

        with _RunningLoop() as loop:
            notifier = JobNotifier(bot=bot, loop=loop, chats=[7], kind="fresh")
            notifier.progress("Downloading...")
            notifier.final({"success": False, "error": "boom"})


class TestNotificationText(unittest.TestCase):
    def test_result_summary_fresh_onenote_success(self):
        text = result_summary_text(_ok_result(), "fresh")
        self.assertIn("✅ OneNote Page Created in Section: Food", text)
        self.assertIn("InstaBrain Summary", text)
        self.assertNotIn("OneNote publishing failed", text)

    def test_result_summary_force(self):
        text = result_summary_text(_ok_result(), "force")
        self.assertIn("⚡ Force-Processed (Fresh Extraction)", text)

    def test_result_summary_reprocess(self):
        text = result_summary_text(_ok_result(reel_id="XYZ789"), "reprocess")
        self.assertIn("🔄 Successfully Reprocessed `XYZ789`", text)

    def test_result_summary_onenote_failure_issue_message(self):
        result = _ok_result(onenote_success=False, onenote_error="authentication failed")
        text = result_summary_text(result, "fresh")
        self.assertIn("needs to be refreshed", text)
        self.assertIn("/connect", text)

    def test_result_summary_does_not_leak_token(self):
        result = _ok_result(onenote_success=False,
                            onenote_error="refresh_token ACCESS_SUPER_SECRET grant")
        text = result_summary_text(result, "fresh")
        self.assertNotIn("ACCESS_SUPER_SECRET", text)

    def test_worker_notifier_holds_no_credentials(self):
        with _RunningLoop() as loop:
            notifier = JobNotifier(bot=object(), loop=loop, chats=[1], kind="fresh")
            state = vars(notifier)
        self.assertNotIn("token", " ".join(state.keys()))
        self.assertEqual(state["_chats"], [1])


# ---------------------------------------------------------------------------
# Worker semantics across processes
# ---------------------------------------------------------------------------

class TestStandaloneWorkerClaims(unittest.TestCase):
    def setUp(self):
        self.col = _make_col()

    def test_standalone_worker_claims_and_processes(self):
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        result = worker_mod.process_next_job(
            "standalone-1",
            process_fn=lambda *a, **k: _ok_result(),
            col=self.col,
            now=NOW,
        )
        self.assertTrue(result["success"])
        stored = job_mod.get_job(job["_id"], col=self.col)
        self.assertEqual(stored["status"], job_mod.STATUS_COMPLETED)

    def test_multiple_workers_cannot_claim_same_job(self):
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        gate = threading.Event()
        result_box = {}

        def slow_process(url, progress, force, user_id):
            gate.wait(timeout=10)
            return _ok_result()

        t = threading.Thread(
            target=lambda: result_box.__setitem__("r", worker_mod.process_next_job(
                "worker-A", process_fn=slow_process, col=self.col, now=NOW))
        )
        t.start()
        try:
            # Wait until worker-A holds the job in processing state.
            deadline = time.time() + 10
            while time.time() < deadline:
                held = self.col.find_one({"_id": job["_id"], "status": "processing"})
                if held:
                    break
                time.sleep(0.01)
            self.assertIsNotNone(held, "worker-A should hold the job")

            # worker-B must NOT claim it and finds the queue idle.
            self.assertIsNone(worker_mod.process_next_job("worker-B", col=self.col, now=NOW))
            self.assertEqual(
                self.col.find_one({"_id": job["_id"]})["status"], "processing"
            )
        finally:
            gate.set()
            t.join(timeout=10)

        self.assertTrue(result_box["r"]["success"])
        self.assertEqual(
            self.col.find_one({"_id": job["_id"]})["status"], "completed"
        )

    def test_recovered_lease_is_claimable_by_new_worker(self):
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        job_mod.claim_next_job("dead-worker", col=self.col, now=NOW)
        later = NOW + datetime.timedelta(seconds=1800)
        actions = job_mod.recover_stale_jobs(col=self.col, now=later)
        self.assertEqual(actions[0]["action"], "requeued")
        reclaimed = worker_mod.process_next_job(
            "fresh-worker",
            process_fn=lambda *a, **k: _ok_result(),
            col=self.col,
            now=later,
        )
        self.assertTrue(reclaimed["success"])
        stored = job_mod.get_job(job["_id"], col=self.col)
        self.assertEqual(stored["status"], "completed")


# ---------------------------------------------------------------------------
# Worker failure isolation
# ---------------------------------------------------------------------------

class TestWorkerFailureIsolation(unittest.IsolatedAsyncioTestCase):
    async def test_worker_loop_survives_failing_job(self):
        col = _make_col()
        job_ok_seen = {"done": False}

        behaviors = iter(["boom", "ok"])

        def process_fn(url, progress, force, user_id):
            try:
                phase = next(behaviors)
            except StopIteration:
                phase = "ok"
            if phase == "boom":
                raise RuntimeError("worker blew up mid-job")
            job_ok_seen["done"] = True
            return _ok_result(reel_id=url.split("/")[-2])

        job1, _ = job_mod.submit_job("AAA", "https://instagram.com/reel/AAA/", "usr_1", col=col)
        job2, _ = job_mod.submit_job("BBB", "https://instagram.com/reel/BBB/", "usr_2", col=col)

        task = asyncio.create_task(
            worker_mod.worker_loop(
                make_notifier=None,
                worker_id="test-loop",
                poll_interval=0.01,
                col=col,
                process_fn=process_fn,
            )
        )
        try:
            await asyncio.sleep(0.4)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self.assertTrue(job_ok_seen["done"], "loop must keep running after a failure")
        self.assertEqual(job_mod.get_job(job1["_id"], col=col)["status"], "failed")
        self.assertEqual(job_mod.get_job(job2["_id"], col=col)["status"], "completed")

    async def test_worker_loop_survives_store_errors(self):
        # A transient MongoDB hiccup inside the loop must be swallowed, not kill
        # the loop task — the bot (embedded worker) stays responsive.
        col = _make_col()
        orig = col.find_one_and_update
        first = {"called": False}

        def flaky_find_one_and_update(*a, **k):
            if not first["called"]:
                first["called"] = True
                raise RuntimeError("Mongo transient failure")
            return orig(*a, **k)

        col.find_one_and_update = flaky_find_one_and_update

        task = asyncio.create_task(
            worker_mod.worker_loop(
                make_notifier=None,
                worker_id="test-loop",
                poll_interval=0.01,
                col=col,
                process_fn=lambda *a, **k: _ok_result(),
            )
        )
        try:
            await asyncio.sleep(0.15)
            self.assertTrue(task is not None and not task.done())
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


if __name__ == "__main__":
    unittest.main()