"""ReelForge V3.5 — Layer 5: worker reliability / recovery hardening tests.

Covers:
- claim lease renewal: guarded refresh, ownership enforcement, terminal no-op
- failure classification: pipeline exception vs error-result vs OneNote
  failure (OneNote failure still completes the job), max-attempts finality
- worker resilience: notifier failures non-fatal, a persistence outage on a
  successful pipeline is never falsely notified as failed, a bad job never
  kills the worker loop, shutdown/cancellation leaves the job recoverable
- the background lease renewer keeps the claim fresh during a long run
- job recovery never touches lived publication slots; a recovered job reuses
  the durable publication record (Layer 4 interaction, no duplicate page)
- /job status card shows lease expiry + completed result lines safely

Uses in-memory FakeCollections (no live MongoDB required), matching the
V2.5/V2.6/V3.4/V3.5 Layer 1-4 offline unit-test style.
"""
import asyncio
import datetime
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import bot.telegram_bot as tb
import processing.worker as worker_mod
import storage.job as job_mod
from storage import publication as pub

from tests.test_v35_layer1_jobs import CLAIM, NOW, URL, FakeCollection as JobFakeCollection
from tests.test_v35_layer4 import (
    _brain,
    _make_writer,
    _seed_brain,
    _write,
    FakeCollection as PubFakeCollection,
)

LATER = NOW + datetime.timedelta(minutes=30)
TARGET_LEASE = LATER + datetime.timedelta(seconds=900)


def _make_jobs_col():
    col = JobFakeCollection()
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


class _RecordingNotifier:
    def __init__(self):
        self.progress_messages = []
        self.final_results = []

    def progress(self, text):
        self.progress_messages.append(text)

    def final(self, result):
        self.final_results.append(result)


class _ProgressRaisingNotifier(_RecordingNotifier):
    def progress(self, text):
        self.progress_messages.append(text)
        raise RuntimeError("telegram progress send failed")


class _FinalRaisingNotifier(_RecordingNotifier):
    def final(self, result):
        self.final_results.append(result)
        raise RuntimeError("telegram final send failed")


class _FlakyCollection(JobFakeCollection):
    """Job collection whose writes can fail mid-flight (simulated outage)."""

    def __init__(self):
        super().__init__()
        self.fail_update_one = False

    def update_one(self, filter_dict, update):
        if self.fail_update_one:
            raise RuntimeError("MongoDB connection lost")
        return super().update_one(filter_dict, update)


# ---------------------------------------------------------------------------
# Part A — claim lease renewal (storage.job.renew_job_lease)
# ---------------------------------------------------------------------------

class TestLeaseRenewal(unittest.TestCase):
    def setUp(self):
        self.col = _make_jobs_col()
        self.job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        self.claimed = job_mod.claim_next_job("w1", col=self.col, now=NOW)
        self.jid = self.job["_id"]
        self.original = NOW + datetime.timedelta(seconds=900)

    def test_renew_refreshes_expiry_and_updated_at(self):
        ok = job_mod.renew_job_lease(self.jid, "w1", col=self.col, now=LATER)
        self.assertTrue(ok)
        stored = job_mod.get_job(self.jid, col=self.col)
        self.assertEqual(stored["lease"]["expires_at"], LATER + datetime.timedelta(seconds=900))
        self.assertEqual(stored["timestamps"]["updated_at"], LATER)
        self.assertEqual(stored["status"], job_mod.STATUS_PROCESSING)

    def test_renew_requires_same_worker(self):
        ok = job_mod.renew_job_lease(self.jid, "w-someone-else", col=self.col, now=LATER)
        self.assertFalse(ok)
        stored = job_mod.get_job(self.jid, col=self.col)
        self.assertEqual(stored["lease"]["expires_at"], self.original)

    def test_renew_after_terminal_is_noop(self):
        job_mod.complete_job(self.jid, {"success": True}, col=self.col, now=NOW)
        ok = job_mod.renew_job_lease(self.jid, "w1", col=self.col, now=LATER)
        self.assertFalse(ok)
        stored = job_mod.get_job(self.jid, col=self.col)
        self.assertEqual(stored["status"], job_mod.STATUS_COMPLETED)
        self.assertIsNone(stored["lease"]["expires_at"])

    def test_renew_unclaimed_job_is_noop(self):
        col = _make_jobs_col()
        queued, _ = job_mod.submit_job("OTHER", "https://instagram.com/reel/OTHER/", col=col, now=NOW)
        ok = job_mod.renew_job_lease(queued["_id"], "w1", col=col, now=LATER)
        self.assertFalse(ok)
        self.assertEqual(job_mod.get_job(queued["_id"], col=col)["status"], job_mod.STATUS_QUEUED)

    def test_renew_missing_guard_is_noop(self):
        self.assertFalse(job_mod.renew_job_lease("", "w1", col=self.col, now=NOW))
        self.assertFalse(job_mod.renew_job_lease(self.jid, "", col=self.col, now=NOW))


# ---------------------------------------------------------------------------
# Part B — failure classification and terminal finality
# ---------------------------------------------------------------------------

class TestFailureClassification(unittest.TestCase):
    def setUp(self):
        self.col = _make_jobs_col()

    def _submit_and_claim(self):
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        return job_mod.claim_next_job("w1", col=self.col, now=NOW)

    def test_process_exception_marks_failed_and_notifies_once(self):
        notifier = _RecordingNotifier()

        def stub(url, progress, force, user_id):
            raise RuntimeError("worker blew up")

        result = worker_mod.run_claimed_job(
            self._submit_and_claim(), make_notifier=lambda j: notifier,
            process_fn=stub, col=self.col, now=NOW,
        )
        self.assertFalse(result["success"])
        self.assertEqual(notifier.final_results, [{"success": False, "error": "worker blew up"}])
        self.assertEqual(self._stored()["status"], job_mod.STATUS_FAILED)
        self.assertEqual(self._stored()["error"], "worker blew up")

    def test_pipeline_error_result_marks_failed(self):
        def stub(url, progress, force, user_id):
            return {"success": False, "error": "Categorization Failed: x"}

        worker_mod.run_claimed_job(self._submit_and_claim(), process_fn=stub, col=self.col, now=NOW)
        stored = self._stored()
        self.assertEqual(stored["status"], job_mod.STATUS_FAILED)
        self.assertIn("Categorization Failed", stored["error"])

    def test_onenote_failure_still_completes_job(self):
        def stub(url, progress, force, user_id):
            result = _ok_result()
            result["onenote_success"] = False
            result["onenote_error"] = "Graph API 400: request malformed"
            return result

        result = worker_mod.run_claimed_job(self._submit_and_claim(), process_fn=stub, col=self.col, now=NOW)
        self.assertTrue(result["success"])
        stored = self._stored()
        self.assertEqual(stored["status"], job_mod.STATUS_COMPLETED)
        self.assertFalse(stored["result"]["onenote_success"])

    def test_max_attempts_terminal_and_never_reclaimed(self):
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        t = NOW
        for _ in range(job_mod.DEFAULT_MAX_ATTEMPTS):
            self.assertIsNotNone(job_mod.claim_next_job("w", col=self.col, now=t))
            t += datetime.timedelta(seconds=1800)
            job_mod.recover_stale_jobs(col=self.col, now=t)
        stored = job_mod.get_job(job["_id"], col=self.col)
        self.assertEqual(stored["status"], job_mod.STATUS_FAILED)
        self.assertEqual(stored["attempts"], job_mod.DEFAULT_MAX_ATTEMPTS)
        self.assertFalse(stored["active"])
        self.assertEqual(job_mod.recover_stale_jobs(col=self.col, now=t + datetime.timedelta(hours=1)), [])
        self.assertIsNone(job_mod.claim_next_job("w", col=self.col, now=t))

    def _stored(self):
        doc = self.col.docs[-1]
        return job_mod.get_job(doc["_id"], col=self.col)


# ---------------------------------------------------------------------------
# Part C — worker resilience
# ---------------------------------------------------------------------------

class TestWorkerResilience(unittest.TestCase):
    def _ok_stub(self, *a, **k):
        return _ok_result()

    def test_progress_notifier_failure_is_non_fatal(self):
        self.col = _make_jobs_col()
        job = _submit_claim(self.col)
        notifier = _ProgressRaisingNotifier()

        result = worker_mod.run_claimed_job(
            job, make_notifier=lambda j: notifier, process_fn=self._ok_stub,
            col=self.col, now=NOW,
        )
        self.assertTrue(result["success"])
        self.assertEqual(len(notifier.final_results), 1)
        self.assertEqual(job_mod.get_job(job["_id"], col=self.col)["status"], job_mod.STATUS_COMPLETED)

    def test_final_notifier_failure_is_non_fatal(self):
        self.col = _make_jobs_col()
        job = _submit_claim(self.col)
        notifier = _FinalRaisingNotifier()

        result = worker_mod.run_claimed_job(
            job, make_notifier=lambda j: notifier, process_fn=self._ok_stub,
            col=self.col, now=NOW,
        )
        self.assertTrue(result["success"])
        self.assertEqual(job_mod.get_job(job["_id"], col=self.col)["status"], job_mod.STATUS_COMPLETED)

    def test_success_persistence_failure_never_notifies_false_failure(self):
        self.col = _FlakyCollection()
        self.col.add_unique_partial(
            "idx_job_claim_active_unique",
            ("claim_key", "active"),
            lambda d: d.get("active") is True,
        )
        job = _submit_claim(self.col)
        notifier = _RecordingNotifier()

        self.col.fail_update_one = True
        result = worker_mod.run_claimed_job(
            job, make_notifier=lambda j: notifier, process_fn=self._ok_stub,
            col=self.col, now=NOW,
        )
        self.col.fail_update_one = False

        self.assertFalse(result["success"])
        self.assertTrue(result.get("durable_pending"))
        self.assertEqual(notifier.final_results, [], "no false 'failed' notification on persistence outage")
        stored = job_mod.get_job(job["_id"], col=self.col)
        self.assertEqual(stored["status"], job_mod.STATUS_PROCESSING, "job left for lease recovery")

        # Recovery re-runs it once Mongo is back; the job eventually completes.
        job_mod.recover_stale_jobs(col=self.col, now=LATER)
        self.assertEqual(job_mod.get_job(job["_id"], col=self.col)["status"], job_mod.STATUS_QUEUED)
        reclaimed = job_mod.claim_next_job("w2", col=self.col, now=LATER)
        self.assertEqual(reclaimed["attempts"], 2)
        worker_mod.run_claimed_job(reclaimed, process_fn=self._ok_stub, col=self.col, now=LATER)
        self.assertEqual(job_mod.get_job(job["_id"], col=self.col)["status"], job_mod.STATUS_COMPLETED)

    def test_failure_persistence_failure_still_notifies_failure(self):
        self.col = _FlakyCollection()
        self.col.add_unique_partial(
            "idx_job_claim_active_unique",
            ("claim_key", "active"),
            lambda d: d.get("active") is True,
        )
        job = _submit_claim(self.col)
        notifier = _RecordingNotifier()

        def bad(url, progress, force, user_id):
            raise RuntimeError("reel download failed")

        self.col.fail_update_one = True
        result = worker_mod.run_claimed_job(
            job, make_notifier=lambda j: notifier, process_fn=bad,
            col=self.col, now=NOW,
        )
        self.col.fail_update_one = False

        self.assertFalse(result["success"])
        self.assertEqual(notifier.final_results, [{"success": False, "error": "reel download failed"}],
                         "a real failure is still reported even if persisting it failed")
        self.assertEqual(job_mod.get_job(job["_id"], col=self.col)["status"], job_mod.STATUS_PROCESSING)

    async def _loop_driver(self, col, stub, stop_check, timeout=10.0):
        deadline = time.time() + timeout
        task = asyncio.create_task(
            worker_mod.worker_loop(
                poll_interval=0.01, col=col, process_fn=stub,
            )
        )
        try:
            while time.time() < deadline:
                if stop_check():
                    break
                await asyncio.sleep(0.01)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def test_bad_job_does_not_kill_worker_loop(self):
        col = _make_jobs_col()
        j1, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=col, now=NOW)
        j2, _ = job_mod.submit_job("OTHER", "https://instagram.com/reel/OTHER/", "usr_1", col=col, now=LATER)
        seen = []

        def stub(url, progress, force, user_id):
            if url == URL:
                raise RuntimeError("bad media payload")
            seen.append(url)
            return _ok_result()

        def done():
            d = job_mod.get_job(j2["_id"], col=col)
            return d and d["status"] == job_mod.STATUS_COMPLETED

        asyncio.run(self._loop_driver(col, stub, done))
        self.assertEqual(job_mod.get_job(j1["_id"], col=col)["status"], job_mod.STATUS_FAILED)
        self.assertEqual(seen, ["https://instagram.com/reel/OTHER/"])
        self.assertEqual(job_mod.get_job(j2["_id"], col=col)["status"], job_mod.STATUS_COMPLETED)

    def test_worker_loop_cancellation_does_not_corrupt_job(self):
        col = _make_jobs_col()
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=col, now=NOW)
        release = threading.Event()

        def blocking(url, progress, force, user_id):
            release.wait(timeout=10)
            return _ok_result()

        async def scenario():
            task = asyncio.create_task(
                worker_mod.worker_loop(poll_interval=0.01, col=col, process_fn=blocking)
            )
            # Wait (without blocking the event loop) until the job is claimed
            # and the blocking pipeline step is executing in the executor thread.
            deadline = time.time() + 10
            while time.time() < deadline:
                if job_mod.get_job(job["_id"], col=col)["status"] == job_mod.STATUS_PROCESSING:
                    break
                await asyncio.sleep(0.005)
            # In-flight job is being executed: cancel the loop like Ctrl+C.
            # A running to_thread executor future is not abortable, so the loop
            # only stops once the current job finishes naturally.
            task.cancel()
            await asyncio.sleep(0.05)
            assert job_mod.get_job(job["_id"], col=col)["status"] == job_mod.STATUS_PROCESSING, \
                "cancellation must not write a terminal state"
            release.set()
            try:
                await task
                self.fail("worker_loop should have been cancelled")
            except asyncio.CancelledError:
                pass
            return job["_id"], col

        jid, col = asyncio.run(scenario())
        self.assertEqual(job_mod.get_job(jid, col=col)["status"], job_mod.STATUS_COMPLETED,
                         "the in-flight job completes naturally before shutdown")


class TestLeaseRenewerThread(unittest.TestCase):
    def test_renewer_keeps_lease_fresh_during_processing(self):
        col = _make_jobs_col()
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=col, now=NOW)
        claimed = job_mod.claim_next_job("w1", col=col, now=NOW)
        self.assertEqual(claimed["lease"]["expires_at"], NOW + datetime.timedelta(seconds=900))

        def slow(url, progress, force, user_id):
            deadline = time.time() + 10
            while time.time() < deadline:
                doc = job_mod.get_job(claimed["_id"], col=col)
                if (doc["lease"] or {}).get("expires_at") == TARGET_LEASE:
                    return _ok_result()
                time.sleep(0.005)
            self.fail("lease was not refreshed by the background renewer during processing")

        result = worker_mod.run_claimed_job(
            claimed, process_fn=slow, col=col, now=LATER, renewal_interval=0.02,
        )
        self.assertTrue(result["success"])
        stored = job_mod.get_job(claimed["_id"], col=col)
        self.assertEqual(stored["status"], job_mod.STATUS_COMPLETED)
        self.assertIsNone(stored["lease"]["expires_at"], "terminal write clears the lease")


def _submit_claim(col):
    job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=col, now=NOW)
    return job_mod.claim_next_job("w1", col=col, now=NOW)


# ---------------------------------------------------------------------------
# Part D — Goal 9: job recovery never sabotages publication slots
# ---------------------------------------------------------------------------

class TestRecoveryPublicationIsolation(unittest.TestCase):
    def test_job_recovery_never_touches_live_publication_slot(self):
        jobs = _make_jobs_col()
        brains = PubFakeCollection()
        _seed_brain(brains, reel_id="reel_alpha", user="usr_1")

        live = pub.claim_publication_slot("reel_alpha", "usr_1", col=brains, now=NOW)
        self.assertTrue(live["owned"])

        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", reel_id="reel_alpha", col=jobs, now=NOW)
        job_mod.claim_next_job("w-crashed", col=jobs, now=NOW)

        before = dict(brains.docs[0])
        actions = job_mod.recover_stale_jobs(col=jobs, now=LATER)
        self.assertEqual([a["action"] for a in actions], ["requeued"])
        self.assertEqual(brains.docs[0], before, "job recovery must not touch the publication slot")

        slot = pub.get_publication("reel_alpha", "usr_1", col=brains)
        self.assertEqual(slot["state"], pub.STATE_CREATING)
        self.assertEqual(slot["owner_token"], live["record"]["owner_token"])

    def test_recovered_job_reuses_persisted_publication(self):
        jobs = _make_jobs_col()
        brains = PubFakeCollection()
        _seed_brain(brains, reel_id="reel_alpha", user="usr_1")

        c1 = pub.claim_publication_slot("reel_alpha", "usr_1", col=brains, now=NOW)
        pub.complete_publication(
            "reel_alpha", "usr_1", c1["record"]["owner_token"],
            {"page_id": "page_win", "page_url": "https://onenote.com/win", "title": "t"},
            col=brains, now=NOW,
        )

        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", reel_id="reel_alpha", col=jobs, now=NOW)
        job_mod.claim_next_job("w-crashed", col=jobs, now=NOW)
        job_mod.recover_stale_jobs(col=jobs, now=LATER)

        writer = _make_writer("usr_1")

        def stub(url, progress, force, user_id):
            write_result = dict(_write(writer, _brain("reel_alpha"), brains, force=bool(force)))
            write_result["success"] = True
            write_result["brain"] = _brain("reel_alpha")
            return write_result

        result = worker_mod.process_next_job("w-fresh", process_fn=stub, col=jobs, now=LATER)
        self.assertTrue(result["success"])
        self.assertEqual(writer.client.created_pages, [], "recovered job must reuse, not create a page")
        final = pub.get_publication("reel_alpha", "usr_1", col=brains)
        self.assertEqual(final["state"], pub.STATE_COMPLETE)
        self.assertEqual(final["page_id"], "page_win")
        self.assertEqual(job_mod.get_job(job["_id"], col=jobs)["status"], job_mod.STATUS_COMPLETED)


# ---------------------------------------------------------------------------
# Part E — /job status card visibility (Layer 5 additions)
# ---------------------------------------------------------------------------

class TestJobStatusCardAdditions(unittest.TestCase):
    def _card(self, **overrides):
        job = {
            "_id": "job_xyz",
            "job_type": "reel",
            "status": "queued",
            "active": True,
            "attempts": 0,
            "max_attempts": 3,
            "recovered": False,
            "claimed_by": None,
            "timestamps": {"created_at": NOW, "started_at": None, "finished_at": None},
            "lease": {"expires_at": None},
            "result": None,
            "error": None,
        }
        job.update(overrides)
        return tb._format_job_status(job)

    def test_processing_shows_lease_expiry(self):
        text = self._card(status="processing", lease={"expires_at": LATER})
        self.assertIn("Lease expires:", text)
        self.assertIn("⚙️ processing", text)

    def test_queued_hides_lease_and_result_lines(self):
        text = self._card()
        self.assertNotIn("Lease expires:", text)
        self.assertNotIn("Result:", text)

    def test_completed_shows_result_and_publish_flag(self):
        text = self._card(
            status="completed",
            result={"success": True, "category": "Food", "onenote_success": True},
        )
        self.assertIn("Result:", text)
        self.assertIn("Category: Food", text)
        self.assertIn("OneNote: published", text)

    def test_completed_onenote_failure_shows_not_published(self):
        text = self._card(
            status="completed",
            result={"success": True, "category": "Food", "onenote_success": False},
        )
        self.assertIn("OneNote: not published", text)

    def test_failed_without_error_still_safe(self):
        text = self._card(status="failed", error=None)
        self.assertIn("❌ failed", text)
        self.assertNotIn("Error:", text)


if __name__ == "__main__":
    unittest.main()