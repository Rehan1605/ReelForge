"""ReelForge V3.5 — Layer 1: durable processing job foundation tests.

Covers: job creation, state transitions, atomic claim, duplicate-concurrent
protection, failure/retry bounds, ownership association, restart/lease
recovery, and worker run/finalize semantics.

Uses an in-memory FakeCollection (no live MongoDB required) so the suite is
deterministic and offline, matching the V2.5/V2.6/V3.4 unit-test style.
"""
import datetime
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pymongo
from pymongo.errors import DuplicateKeyError

from storage import job as job_mod
from storage.db import ensure_job_indexes


def _resolve(doc, key):
    cur = doc
    for part in key.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def _set(doc, key, value):
    parts = key.split(".")
    cur = doc
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _delete(doc, key):
    parts = key.split(".")
    cur = doc
    for part in parts[:-1]:
        if not isinstance(cur, dict):
            return
        cur = cur.get(part)
        if not isinstance(cur, dict):
            return
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)


def _match(filter_dict, doc):
    for key, expected in filter_dict.items():
        got = _resolve(doc, key)
        if isinstance(expected, dict) and any(str(k).startswith("$") for k in expected):
            for op, operand in expected.items():
                if op == "$lt":
                    if not (got is not None and got < operand):
                        return False
                elif op == "$lte":
                    if not (got is not None and got <= operand):
                        return False
                elif op == "$gt":
                    if not (got is not None and got > operand):
                        return False
                elif op == "$gte":
                    if not (got is not None and got >= operand):
                        return False
                elif op == "$ne":
                    if got == operand:
                        return False
                elif op == "$in":
                    if got not in operand:
                        return False
                elif op == "$nin":
                    if got in operand:
                        return False
                elif op == "$exists":
                    if (got is None) == bool(operand):
                        return False
                else:
                    raise NotImplementedError(f"fake op {op}")
        else:
            if isinstance(got, list):
                if expected not in got:
                    return False
            elif got != expected:
                return False
    return True


def _apply(update, doc):
    for op, spec in update.items():
        if op == "$set":
            for k, v in spec.items():
                _set(doc, k, v)
        elif op == "$inc":
            for k, v in spec.items():
                _set(doc, k, (_resolve(doc, k) or 0) + v)
        elif op == "$addToSet":
            for k, v in spec.items():
                items = [v] if not isinstance(v, (list, tuple)) else list(v)
                cur = list(_resolve(doc, k) or [])
                for it in items:
                    if it not in cur:
                        cur.append(it)
                _set(doc, k, cur)
        elif op == "$unset":
            for k in spec:
                _delete(doc, k)
        else:
            raise NotImplementedError(f"fake op {op}")
    return doc


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction=1):
        rev = (direction or 1) < 0
        try:
            self._docs = sorted(self._docs, key=lambda d: _resolve(d, key) or "", reverse=rev)
        except TypeError:
            pass
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


class _UpdateResult:
    def __init__(self, matched):
        self.matched_count = 1 if matched else 0
        self.modified_count = 1 if matched else 0


class FakeCollection:
    """Tiny in-memory stand-in for the subset of pymongo we use in storage.job."""

    def __init__(self):
        self.docs = []
        self.unique_partials = []  # [{"fields": [...], "filter": callable, "name": str}]

    def add_unique_partial(self, name, fields, filter_fn):
        self.unique_partials.append({"name": name, "fields": list(fields), "filter": filter_fn})

    def insert_one(self, doc):
        for spec in self.unique_partials:
            if not spec["filter"](doc):
                continue
            for other in self.docs:
                if spec["filter"](other) and all(
                    _resolve(doc, f) == _resolve(other, f) for f in spec["fields"]
                ):
                    raise DuplicateKeyError("E11000 duplicate key error (faked)")
        self.docs.append(doc)

    def find_one(self, filter_dict):
        for doc in self.docs:
            if _match(filter_dict, doc):
                return dict(doc)
        return None

    def find(self, filter_dict):
        return _FakeCursor(d for d in self.docs if _match(filter_dict, d))

    def find_one_and_update(self, filter_dict, update, sort=None, return_document=None):
        matches = [d for d in self.docs if _match(filter_dict, d)]
        if not matches:
            return None
        if sort:
            key, direction = sort[0]
            rev = (direction or 1) < 0
            matches = sorted(matches, key=lambda d: _resolve(d, key) or "", reverse=rev)
        target = matches[0]
        before = dict(target)
        _apply(update, target)
        if return_document == pymongo.ReturnDocument.AFTER:
            return dict(target)
        return before

    def update_one(self, filter_dict, update):
        for doc in self.docs:
            if _match(filter_dict, doc):
                _apply(update, doc)
                return _UpdateResult(True)
        return _UpdateResult(False)

    def create_index(self, keys, name, **kwargs):
        self.index_records = getattr(self, "index_records", [])
        self.index_records.append({"keys": list(keys), "name": name, **kwargs})


NOW = datetime.datetime(2026, 9, 14, 12, 0, 0, tzinfo=datetime.timezone.utc)
URL = "https://www.instagram.com/reel/ABc123XYZ/"
CLAIM = "ABc123XYZ"


class TestJobSchema(unittest.TestCase):
    def test_new_job_defaults(self):
        job = job_mod.new_job(CLAIM, URL, "usr_1", now=NOW)
        self.assertEqual(job["status"], job_mod.STATUS_QUEUED)
        self.assertTrue(job["active"])
        self.assertEqual(job["attempts"], 0)
        self.assertEqual(job["claimed_by"], None)
        self.assertEqual(job["result"], None)
        self.assertEqual(job["error"], None)
        self.assertEqual(job["requested_by"], ["usr_1"])
        self.assertEqual(job["timestamps"]["created_at"], NOW)
        self.assertEqual(job["timestamps"]["finished_at"], None)
        self.assertEqual(job["lease"]["expires_at"], None)
        self.assertTrue(job["_id"].startswith("job_"))

    def test_summarize_result_projection(self):
        summary = job_mod.summarize_result(
            {"success": True, "cached": False, "onenote_success": True,
             "onenote_error": None, "category": "Food", "brain": {"id": "X1"},
             "vision_status": "degraded", "error": None}
        )
        self.assertEqual(summary["reel_id"], "X1")
        self.assertEqual(summary["category"], "Food")
        self.assertEqual(summary["vision_status"], "degraded")

    def test_summarize_result_remains_compatible_without_vision_status(self):
        summary = job_mod.summarize_result(
            {"success": True, "cached": False, "onenote_success": True,
             "onenote_error": None, "category": "Food", "brain": {"id": "X1"}, "error": None}
        )
        self.assertNotIn("vision_status", summary)


class TestJobsStore(unittest.TestCase):
    def setUp(self):
        self.col = FakeCollection()
        self.col.add_unique_partial(
            "idx_job_claim_active_unique",
            ("claim_key", "active"),
            lambda d: d.get("active") is True,
        )

    def test_submit_job_creates_queued_job(self):
        job, dup = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        self.assertFalse(dup)
        self.assertEqual(job["status"], job_mod.STATUS_QUEUED)
        self.assertIsNotNone(job_mod.get_job(job["_id"], col=self.col))

    def test_submit_job_dedupe_merges_users_and_chats(self):
        j1, _ = job_mod.submit_job(CLAIM, URL, "usr_1", chat_id=111, col=self.col, now=NOW)
        j2, dup = job_mod.submit_job(CLAIM, URL, "usr_2", chat_id=222, col=self.col, now=NOW)
        self.assertTrue(dup)
        self.assertEqual(j2["_id"], j1["_id"])
        self.assertIn("usr_1", j2["requested_by"])
        self.assertIn("usr_2", j2["requested_by"])
        self.assertEqual(sorted(j2["chats"]), [111, 222])
        self.assertEqual(len(self.col.docs), 1)

    def test_submit_force_merges_into_active_job(self):
        j1, _ = job_mod.submit_job(CLAIM, URL, "usr_1", force=False, col=self.col, now=NOW)
        j2, dup = job_mod.submit_job(CLAIM, URL, "usr_1", force=True, col=self.col, now=NOW)
        self.assertTrue(dup)
        self.assertEqual(j2["_id"], j1["_id"])
        self.assertTrue(j2["force"])

    def test_resubmit_allowed_after_completion(self):
        j1, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        job_mod.claim_next_job("w1", col=self.col, now=NOW)
        job_mod.complete_job(j1["_id"], {"success": True}, col=self.col, now=NOW)
        j2, dup = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        self.assertFalse(dup)
        self.assertNotEqual(j2["_id"], j1["_id"])

    def test_resubmit_allowed_after_failure(self):
        j1, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        job_mod.claim_next_job("w1", col=self.col, now=NOW)
        job_mod.fail_job(j1["_id"], "boom", col=self.col, now=NOW)
        j2, dup = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        self.assertFalse(dup)

    def test_claim_next_job_atomic_transition(self):
        job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        claimed = job_mod.claim_next_job("worker-A", col=self.col, now=NOW)
        self.assertEqual(claimed["status"], job_mod.STATUS_PROCESSING)
        self.assertEqual(claimed["claimed_by"], "worker-A")
        self.assertEqual(claimed["attempts"], 1)
        self.assertEqual(claimed["timestamps"]["started_at"], NOW)
        self.assertEqual(claimed["lease"]["expires_at"], NOW + datetime.timedelta(seconds=900))
        second = job_mod.claim_next_job("worker-B", col=self.col, now=NOW)
        self.assertIsNone(second, "second worker must not claim an in-flight job")

    def test_claim_oldest_first_fifo(self):
        t0 = NOW
        job_mod.submit_job("AAA", "https://instagram.com/reel/AAA/", col=self.col, now=t0)
        job_mod.submit_job("BBB", "https://instagram.com/reel/BBB/", col=self.col, now=t0 + datetime.timedelta(seconds=5))
        first = job_mod.claim_next_job("w1", col=self.col, now=t0 + datetime.timedelta(seconds=10))
        self.assertEqual(first["claim_key"], "AAA")

    def test_complete_job_terminal(self):
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        job_mod.claim_next_job("w1", col=self.col, now=NOW)
        ok = job_mod.complete_job(job["_id"], {"success": True}, col=self.col, now=NOW)
        self.assertTrue(ok)
        stored = job_mod.get_job(job["_id"], col=self.col)
        self.assertEqual(stored["status"], job_mod.STATUS_COMPLETED)
        self.assertFalse(stored["active"])
        self.assertEqual(stored["result"]["success"], True)
        self.assertEqual(stored["timestamps"]["finished_at"], NOW)

    def test_fail_job_terminal(self):
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        job_mod.claim_next_job("w1", col=self.col, now=NOW)
        ok = job_mod.fail_job(job["_id"], "categorization failed", col=self.col, now=NOW)
        self.assertTrue(ok)
        stored = job_mod.get_job(job["_id"], col=self.col)
        self.assertEqual(stored["status"], job_mod.STATUS_FAILED)
        self.assertFalse(stored["active"])
        self.assertEqual(stored["error"], "categorization failed")

    def test_recover_stale_job_requeues_crashed_worker(self):
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        job_mod.claim_next_job("w-dead", col=self.col, now=NOW)
        later = NOW + datetime.timedelta(seconds=1800)
        actions = job_mod.recover_stale_jobs(col=self.col, now=later)
        self.assertEqual([a["action"] for a in actions], ["requeued"])
        stored = job_mod.get_job(job["_id"], col=self.col)
        self.assertEqual(stored["status"], job_mod.STATUS_QUEUED)
        self.assertTrue(stored["recovered"])
        self.assertEqual(stored["claimed_by"], None)
        reclaimed = job_mod.claim_next_job("w-fresh", col=self.col, now=later)
        self.assertIsNotNone(reclaimed)
        self.assertEqual(reclaimed["attempts"], 2)

    def test_recover_stale_job_fails_after_max_attempts(self):
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        t = NOW
        actions = []
        for _ in range(job_mod.DEFAULT_MAX_ATTEMPTS):
            claimed = job_mod.claim_next_job("w", col=self.col, now=t)
            self.assertIsNotNone(claimed)
            t += datetime.timedelta(seconds=1800)
            actions += job_mod.recover_stale_jobs(col=self.col, now=t)
        self.assertEqual(
            actions,
            [
                {"job_id": job["_id"], "action": "requeued"},
                {"job_id": job["_id"], "action": "requeued"},
                {"job_id": job["_id"], "action": "failed_expired"},
            ],
        )
        stored = job_mod.get_job(job["_id"], col=self.col)
        self.assertEqual(stored["status"], job_mod.STATUS_FAILED)
        self.assertEqual(stored["error"], "lease expired after max attempts")

    def test_recover_ignores_active_lease(self):
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        job_mod.claim_next_job("w1", col=self.col, now=NOW)  # lease expires 900s out
        actions = job_mod.recover_stale_jobs(col=self.col, now=NOW + datetime.timedelta(seconds=60))
        self.assertEqual(actions, [])
        self.assertEqual(job_mod.get_job(job["_id"], col=self.col)["status"], job_mod.STATUS_PROCESSING)

    def test_list_jobs_filters(self):
        job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        j2, _ = job_mod.submit_job("OTHER", "https://instagram.com/reel/OTHER/", "usr_2", col=self.col, now=NOW)
        job_mod.claim_next_job("w1", col=self.col, now=NOW)
        job_mod.complete_job(j2["_id"], {"success": True}, col=self.col, now=NOW)
        all_jobs = job_mod.list_jobs(col=self.col)
        self.assertEqual(len(all_jobs), 2)
        queued = job_mod.list_jobs(status=job_mod.STATUS_QUEUED, col=self.col)
        self.assertEqual(len(queued), 1)
        by_user = job_mod.list_jobs(user_id="usr_2", col=self.col)
        self.assertEqual(len(by_user), 1)


class TestWorkerSemantics(unittest.TestCase):
    def setUp(self):
        self.col = FakeCollection()
        self.col.add_unique_partial(
            "idx_job_claim_active_unique",
            ("claim_key", "active"),
            lambda d: d.get("active") is True,
        )

    def _ok_result(self, reel_id="ABc123XYZ"):
        return {
            "success": True, "cached": False, "onenote_success": True,
            "onenote_error": None, "category": "Food", "error": None,
            "brain": {"id": reel_id, "knowledge": {"category": "Food", "summary": "x"}},
            "brain_path": None, "transcript": "", "knowledge": {},
        }

    def test_process_next_job_success_path(self):
        from processing import worker as worker_mod
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        notifier = _RecordingNotifier()

        def stub(url, progress, force, user_id):
            progress("Downloading")
            progress("Categorizing")
            return self._ok_result()

        result = worker_mod.process_next_job(
            "w1", make_notifier=lambda j: notifier, process_fn=stub, col=self.col, now=NOW
        )
        self.assertTrue(result["success"])
        stored = job_mod.get_job(job["_id"], col=self.col)
        self.assertEqual(stored["status"], job_mod.STATUS_COMPLETED)
        self.assertEqual(stored["result"]["category"], "Food")
        self.assertEqual(notifier.progress_messages, ["Downloading", "Categorizing"])
        self.assertEqual(len(notifier.final_results), 1)
        self.assertTrue(notifier.final_results[0]["success"])

    def test_process_next_job_pipeline_failure_is_terminal(self):
        from processing import worker as worker_mod

        def stub(url, progress, force, user_id):
            return {"success": False, "error": "Categorization Failed: x"}

        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        worker_mod.process_next_job("w1", process_fn=stub, col=self.col, now=NOW)
        stored = job_mod.get_job(job["_id"], col=self.col)
        self.assertEqual(stored["status"], job_mod.STATUS_FAILED)
        self.assertIn("Categorization Failed", stored["error"])

    def test_process_next_job_exception_marks_failed(self):
        from processing import worker as worker_mod

        def stub(url, progress, force, user_id):
            raise RuntimeError("worker blew up")

        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        result = worker_mod.process_next_job("w1", process_fn=stub, col=self.col, now=NOW)
        self.assertFalse(result["success"])
        stored = job_mod.get_job(job["_id"], col=self.col)
        self.assertEqual(stored["status"], job_mod.STATUS_FAILED)
        self.assertEqual(stored["error"], "worker blew up")

    def test_process_next_job_idle_returns_none(self):
        from processing import worker as worker_mod
        self.assertIsNone(worker_mod.process_next_job("w1", col=self.col, now=NOW))

    def test_processing_uses_job_user_and_force(self):
        from processing import worker as worker_mod
        seen = {}

        def stub(url, progress, force, user_id):
            seen.update(url=url, force=force, user_id=user_id)
            return self._ok_result()

        job, _ = job_mod.submit_job(CLAIM, URL, "usr_42", force=True, col=self.col, now=NOW)
        worker_mod.process_next_job("w1", process_fn=stub, col=self.col, now=NOW)
        self.assertEqual(seen["user_id"], "usr_42")
        self.assertTrue(seen["force"])
        self.assertEqual(seen["url"], URL)

    def test_secondary_users_associated_on_success(self):
        from processing import worker as worker_mod
        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", chat_id=1, col=self.col, now=NOW)
        job_mod.submit_job(CLAIM, URL, "usr_2", chat_id=2, col=self.col, now=NOW)

        with patch("processing.worker.associate_user_with_brain") as mock_assoc:
            worker_mod.process_next_job("w1", process_fn=lambda *a, **k: self._ok_result(), col=self.col, now=NOW)
            mock_assoc.assert_called_once_with("ABc123XYZ", "usr_2")

    def test_notifier_final_gets_failure_after_exception(self):
        from processing import worker as worker_mod
        notifier = _RecordingNotifier()

        def stub(url, progress, force, user_id):
            raise ValueError("nope")

        job, _ = job_mod.submit_job(CLAIM, URL, "usr_1", col=self.col, now=NOW)
        worker_mod.process_next_job("w1", make_notifier=lambda j: notifier, process_fn=stub, col=self.col, now=NOW)
        self.assertEqual(len(notifier.final_results), 1)
        self.assertFalse(notifier.final_results[0]["success"])
        self.assertEqual(notifier.final_results[0]["error"], "nope")


class _RecordingNotifier:
    def __init__(self):
        self.progress_messages = []
        self.final_results = []

    def progress(self, text):
        self.progress_messages.append(text)

    def final(self, result):
        self.final_results.append(result)


class TestJobIndexes(unittest.TestCase):
    def test_ensure_job_indexes_defines_duplicate_guard(self):
        col = FakeCollection()
        ensure_job_indexes(col)
        records = getattr(col, "index_records", [])
        names = {r["name"] for r in records}
        self.assertIn("idx_job_claim_active_unique", names)
        dupe = next(r for r in records if r["name"] == "idx_job_claim_active_unique")
        self.assertTrue(dupe["unique"])
        self.assertEqual(dupe["partialFilterExpression"], {"active": {"$eq": True}})
        self.assertIn("idx_job_status_lease", names)
        self.assertIn("idx_job_user_created", names)

    def test_fake_partial_unique_enforced(self):
        # Behavioural proof that the partial unique index blocks concurrent
        # active jobs but not terminal re-submissions.
        col = FakeCollection()
        col.add_unique_partial(
            "idx_job_claim_active_unique",
            ("claim_key", "active"),
            lambda d: d.get("active") is True,
        )
        job_mod.submit_job(CLAIM, URL, "usr_1", col=col, now=NOW)
        dup = job_mod.new_job(CLAIM, URL, "usr_2", now=NOW)
        with self.assertRaises(DuplicateKeyError):
            col.insert_one(dup)


if __name__ == "__main__":
    unittest.main()
