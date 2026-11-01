"""V3.6.5 tests for durable per-user publication fan-out and retries."""

from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

import pymongo

from onenote.writer import OneNoteWriter
from processing import publication_fanout as fanout
from processing import worker as worker_mod
from storage import job as job_mod
from storage import publication as pub
from tests.test_v35_layer1_jobs import FakeCollection as JobCollection
from tests.test_v35_layer4 import FakeCollection as BrainCollection


REEL_ID = "shared_reel"
URL = f"https://www.instagram.com/reel/{REEL_ID}/"
USERS = ["usr_one", "usr_two"]


def _brain():
    return {
        "id": REEL_ID,
        "knowledge": {"category": "Food", "title": "Shared recipe", "summary": "s"},
        "content": {"caption": "caption", "transcript": "transcript"},
        "source": {"shortcode": REEL_ID, "url": URL},
        "ownership": {"saved_by": list(USERS), "archived_by": []},
        "media": {},
    }


def _seed_brain(col):
    col.docs.append({
        "_id": REEL_ID,
        "id": REEL_ID,
        "ownership": {"saved_by": list(USERS), "archived_by": []},
    })


class _AuthFailureClient:
    def authenticate(self):
        raise RuntimeError("Microsoft account not connected. Please use /connect in Telegram.")


class _PageClient:
    def __init__(self, user_id, pages):
        self.user_id = user_id
        self.pages = pages

    def authenticate(self):
        return None

    def get_or_create_notebook(self, _name):
        return {"id": f"nb-{self.user_id}"}

    def create_section(self, _notebook_id, _section_name):
        return {"id": f"section-{self.user_id}"}

    def create_page(self, *_args, **_kwargs):
        self.pages[self.user_id] = self.pages.get(self.user_id, 0) + 1
        return {
            "id": f"page-{self.user_id}",
            "links": {"oneNoteWebUrl": {"href": f"https://onenote.invalid/{self.user_id}"}},
        }


def _writer(user_id, pages, *, no_auth=False):
    writer = OneNoteWriter.__new__(OneNoteWriter)
    writer.user_id = user_id
    writer.client = _AuthFailureClient() if no_auth else _PageClient(user_id, pages)
    writer._cache_onenote_metadata = lambda *_a, **_kw: None
    writer._notebook_name = lambda: "InstaBrain"
    return writer


def _job_col():
    col = JobCollection()
    col.add_unique_partial(
        "idx_job_claim_active_unique",
        ("claim_key", "active"),
        lambda doc: doc.get("active") is True,
    )
    return col


class _ThreadSafeJobCollection(JobCollection):
    def __init__(self):
        super().__init__()
        self.lock = threading.RLock()

    def insert_one(self, doc):
        with self.lock:
            return super().insert_one(doc)

    def find_one(self, filt):
        with self.lock:
            return super().find_one(filt)

    def update_one(self, filt, update):
        with self.lock:
            return super().update_one(filt, update)


class TestConcurrentRequesterTracking(unittest.TestCase):
    def test_two_users_share_one_active_job_and_both_are_recorded(self):
        col = _ThreadSafeJobCollection()
        col.add_unique_partial(
            "idx_job_claim_active_unique",
            ("claim_key", "active"),
            lambda doc: doc.get("active") is True,
        )
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def submit(user_id, chat_id):
            try:
                barrier.wait()
                results.append(job_mod.submit_job(
                    REEL_ID, URL, user_id, reel_id=REEL_ID,
                    chat_id=chat_id, col=col,
                ))
            except Exception as exc:  # surface thread failures to unittest
                errors.append(exc)

        threads = [
            threading.Thread(target=submit, args=(USERS[0], 101)),
            threading.Thread(target=submit, args=(USERS[1], 202)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)

        self.assertEqual(errors, [])
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(col.docs), 1)
        self.assertEqual(set(col.docs[0]["requested_by"]), set(USERS))
        self.assertEqual(set(col.docs[0]["chats"]), {101, 202})
        self.assertEqual(len({job["_id"] for job, _duplicate in results}), 1)

    def test_request_racing_with_completion_is_not_lost(self):
        col = _job_col()
        original_update = col.update_one
        raced = {"value": False}

        def complete_before_attach(filt, update):
            if filt.get("active") is True and not raced["value"]:
                raced["value"] = True
                existing = col.docs[0]
                existing["active"] = False
                existing["status"] = job_mod.STATUS_COMPLETED
            return original_update(filt, update)

        col.update_one = complete_before_attach
        first, _ = job_mod.submit_job(REEL_ID, URL, USERS[0], col=col)
        # Restore the method for the second submission after installing race.
        col.update_one = complete_before_attach
        second, duplicate = job_mod.submit_job(REEL_ID, URL, USERS[1], col=col)

        self.assertFalse(duplicate)
        self.assertNotEqual(first["_id"], second["_id"])
        self.assertEqual(len(col.docs), 2)
        self.assertEqual(col.docs[1]["requested_by"], [USERS[1]])


class TestPerUserPublicationFanout(unittest.TestCase):
    def setUp(self):
        self.col = BrainCollection()
        _seed_brain(self.col)
        self.pages = {}
        self.formatter_patcher = patch("onenote.writer.format_brain_object", return_value="<html/>")
        self.formatter_patcher.start()
        self.job = {
            "user_id": USERS[0],
            "requested_by": list(USERS),
            "chats": [101, 202],
        }

    def tearDown(self):
        self.formatter_patcher.stop()

    def _factory(self, user_id):
        return _writer(user_id, self.pages)

    def test_each_requester_gets_an_independent_page_and_slot(self):
        with patch.object(fanout, "associate_user_with_brain", return_value=True):
            result = fanout.publish_to_requesters(
                _brain(), self.job, writer_factory=self._factory, col=self.col,
            )

        self.assertEqual(result, {"published": 2, "failed": 0, "pending": 0})
        self.assertEqual(self.pages, {USERS[0]: 1, USERS[1]: 1})
        for user_id in USERS:
            record = pub.get_publication(REEL_ID, user_id, col=self.col)
            self.assertEqual(record["state"], pub.STATE_COMPLETE)
            self.assertEqual(record["page_id"], f"page-{user_id}")

    def test_one_unconnected_user_does_not_block_another(self):
        def factory(user_id):
            return _writer(user_id, self.pages, no_auth=(user_id == USERS[0]))

        with patch.object(fanout, "associate_user_with_brain", return_value=True):
            result = fanout.publish_to_requesters(
                _brain(), self.job, writer_factory=factory, col=self.col,
            )

        self.assertEqual(result, {"published": 1, "failed": 1, "pending": 0})
        self.assertEqual(self.pages, {USERS[1]: 1})
        self.assertEqual(pub.get_publication(REEL_ID, USERS[0], col=self.col)["state"], pub.STATE_FAILED)
        self.assertEqual(pub.get_publication(REEL_ID, USERS[1], col=self.col)["state"], pub.STATE_COMPLETE)

    def test_failed_publication_can_retry_without_replacing_successful_page(self):
        fail_first = {USERS[0]: True}

        def factory(user_id):
            return _writer(user_id, self.pages, no_auth=fail_first.get(user_id, False))

        with patch.object(fanout, "associate_user_with_brain", return_value=True):
            first = fanout.publish_to_requesters(
                _brain(), self.job, writer_factory=factory, col=self.col,
            )
            fail_first[USERS[0]] = False
            second = fanout.publish_to_requesters(
                _brain(), self.job, writer_factory=factory, col=self.col,
            )

        self.assertEqual(first, {"published": 1, "failed": 1, "pending": 0})
        self.assertEqual(second, {"published": 2, "failed": 0, "pending": 0})
        self.assertEqual(self.pages, {USERS[0]: 1, USERS[1]: 1})

    def test_cached_retry_reuses_page_for_user_already_completed(self):
        with patch.object(fanout, "associate_user_with_brain", return_value=True):
            fanout.publish_to_requesters(_brain(), self.job, writer_factory=self._factory, col=self.col)
            repeated = fanout.publish_to_requesters(
                _brain(), self.job, writer_factory=self._factory, col=self.col,
            )
        self.assertEqual(repeated, {"published": 2, "failed": 0, "pending": 0})
        self.assertEqual(self.pages, {USERS[0]: 1, USERS[1]: 1})

    def test_worker_restart_reuses_completed_user_slot_and_finishes_remaining_user(self):
        pages = {}
        records = {}
        created_before_crash = {USERS[0]: 0, USERS[1]: 0}
        second_writer_constructions = {"count": 0}

        class RestartWriter:
            def __init__(self, user_id):
                self.user_id = user_id

            def write(self, _brain, **_kwargs):
                if self.user_id in records:
                    return {"reused": True, "id": f"page-{self.user_id}"}
                created_before_crash[self.user_id] += 1
                records[self.user_id] = {"state": "complete"}
                pages[self.user_id] = True
                return {"id": f"page-{self.user_id}"}

        result = {
            "success": True, "cached": True, "onenote_success": True,
            "category": "Food", "brain": _brain(), "brain_path": None,
        }
        job = {
            "_id": "job_fanout", "claimed_by": "worker", "job_type": "reel",
            "user_id": USERS[0], "requested_by": list(USERS), "reel_url": URL,
            "force": False,
        }
        job_col = _job_col()
        stored = job_mod.new_job(REEL_ID, URL, USERS[0])
        stored.update({"_id": job["_id"], "status": job_mod.STATUS_PROCESSING,
                       "active": True, "claimed_by": "worker"})
        job_col.docs.append(stored)

        def factory(user_id):
            if user_id == USERS[1] and second_writer_constructions["count"] == 0:
                second_writer_constructions["count"] += 1
                # Simulate process death after user one's slot was committed
                # but before user two's publication began.
                raise KeyboardInterrupt()
            return RestartWriter(user_id)

        with (
            patch.object(worker_mod, "_associate_secondary_users"),
            patch.object(fanout, "associate_user_with_brain", return_value=True),
            patch.object(fanout, "get_publication", side_effect=lambda _r, user_id, **_kw: records.get(user_id)),
            patch.object(fanout, "OneNoteWriter", side_effect=factory),
        ):
            with self.assertRaises(KeyboardInterrupt):
                worker_mod.run_claimed_job(
                    job, process_fn=lambda *_args: result, col=job_col, renewal_interval=60,
                )
            job_col.docs[0]["status"] = job_mod.STATUS_PROCESSING
            job_col.docs[0]["active"] = True
            restarted = worker_mod.run_claimed_job(
                job, process_fn=lambda *_args: result, col=job_col, renewal_interval=60,
            )

        self.assertTrue(restarted["success"])
        self.assertEqual(created_before_crash, {USERS[0]: 1, USERS[1]: 1})
        self.assertEqual(pages, {USERS[0]: True, USERS[1]: True})

    def test_publication_retry_job_does_not_invoke_reel_pipeline(self):
        job = {
            "_id": "job_retry", "claimed_by": "worker", "job_type": job_mod.JOB_TYPE_PUBLICATION,
            "user_id": USERS[1], "reel_id": REEL_ID, "reel_url": URL,
            "force": False,
        }
        col = _job_col()
        stored = job_mod.new_job("publication", URL, USERS[1])
        stored.update({"_id": job["_id"], "status": job_mod.STATUS_PROCESSING,
                       "active": True, "claimed_by": "worker"})
        col.docs.append(stored)
        with patch.object(worker_mod, "publish_for_user", return_value={
            "success": True, "cached": True, "onenote_success": True,
            "category": "Food", "brain": _brain(),
        }) as publish, patch.object(worker_mod, "_associate_secondary_users"):
            result = worker_mod.run_claimed_job(
                job, process_fn=lambda *_args: self.fail("pipeline must not run"),
                col=col, renewal_interval=60,
            )
        publish.assert_called_once_with(REEL_ID, USERS[1])
        self.assertTrue(result["success"])

    def test_worker_processes_once_then_fans_out_to_all_requesters(self):
        job_col = _job_col()
        job = {
            "_id": "job_shared", "claimed_by": "worker", "job_type": "reel",
            "user_id": USERS[0], "requested_by": list(USERS), "reel_url": URL,
            "force": False,
        }
        stored = job_mod.new_job(REEL_ID, URL, USERS[0])
        stored.update({"_id": job["_id"], "status": job_mod.STATUS_PROCESSING,
                       "active": True, "claimed_by": "worker"})
        job_col.docs.append(stored)
        pipeline_calls = []
        process_result = {
            "success": True, "cached": False, "onenote_success": True,
            "category": "Food", "brain": _brain(), "brain_path": None,
        }

        def process_once(*_args):
            pipeline_calls.append("processed")
            return dict(process_result)

        with (
            patch.object(worker_mod, "_associate_secondary_users"),
            patch.object(worker_mod, "publish_to_requesters", return_value={
                "published": 2, "failed": 0, "pending": 0,
            }) as publish_all,
        ):
            result = worker_mod.run_claimed_job(
                job, process_fn=process_once, col=job_col, renewal_interval=60,
            )

        self.assertEqual(pipeline_calls, ["processed"])
        self.assertEqual(set(publish_all.call_args.args[1]["requested_by"]), set(USERS))
        self.assertEqual(result["publications"]["published"], 2)

    def test_requester_attached_during_pipeline_is_published_before_completion(self):
        job_col = _job_col()
        stored = job_mod.new_job(REEL_ID, URL, USERS[0])
        stored.update({"_id": "job_race", "status": job_mod.STATUS_PROCESSING,
                       "active": True, "claimed_by": "worker", "requesters_version": 1})
        job_col.docs.append(stored)
        claimed = dict(stored)
        pipeline_calls = []
        seen_audiences = []

        def process_and_attach(*_args):
            pipeline_calls.append(True)
            job_mod.submit_job(REEL_ID, URL, USERS[1], col=job_col)
            return {
                "success": True, "cached": False, "onenote_success": True,
                "category": "Food", "brain": _brain(),
            }

        def publish(_brain_value, audience):
            seen_audiences.append(list(audience.get("requested_by", [])))
            return {"published": 2, "failed": 0, "pending": 0}

        with patch.object(worker_mod, "_associate_secondary_users"), patch.object(
            worker_mod, "publish_to_requesters", side_effect=publish,
        ):
            result = worker_mod.run_claimed_job(
                claimed, process_fn=process_and_attach, col=job_col,
                renewal_interval=60,
            )

        self.assertTrue(result["success"])
        self.assertEqual(pipeline_calls, [True])
        self.assertEqual(len(job_col.docs), 1)
        self.assertEqual(set(job_col.docs[0]["requested_by"]), set(USERS))
        self.assertEqual(seen_audiences[-1], USERS)

    def test_retry_checks_user_ownership_before_loading_or_publishing(self):
        with patch.object(fanout, "load_brain_object", side_effect=PermissionError("denied")) as load:
            with self.assertRaises(PermissionError):
                fanout.publish_for_user(REEL_ID, USERS[1], writer_factory=lambda _uid: self.fail("must not publish"))
        load.assert_called_once_with(REEL_ID, user_id=USERS[1])

    def test_writer_constructs_graph_client_with_each_requesters_identity(self):
        with patch("onenote.writer.GraphClient") as graph_client:
            OneNoteWriter(USERS[0])
            OneNoteWriter(USERS[1])

        self.assertEqual(
            [call.kwargs["user_id"] for call in graph_client.call_args_list],
            USERS,
        )


if __name__ == "__main__":
    unittest.main()
