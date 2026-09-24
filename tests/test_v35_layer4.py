"""ReelForge V3.5 — Layer 4: idempotent processing / safe retry tests.

Covers: first-publish slot creation + recording, retry-before-OneNote,
retry-after-create (crash window B), retry-after-persist (window C reuse),
per-user publication isolation, force/reprocess replacement semantics,
concurrent workers leading to a single page, OneNote failure marking, lease
expiry reclaim, the legacy global (None user) key, and the writer-level
wait-then-reuse resolution.

Uses an in-memory FakeCollection (no live MongoDB required) exactly like the
V3.5 Layer 1/2 suites, plus a thread-safe variant for the concurrency test.
"""
import datetime
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pymongo

from onenote.writer import OneNoteWriter
from storage import publication as pub


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
            if got != expected:
                return False
    return True


def _apply(update, doc):
    for op, spec in update.items():
        if op == "$set":
            for k, v in spec.items():
                _set(doc, k, v)
        elif op == "$unset":
            for k in spec:
                parts = k.split(".")
                cur = doc
                for part in parts[:-1]:
                    if not isinstance(cur, dict):
                        break
                    cur = cur.get(part)
                if isinstance(cur, dict):
                    cur.pop(parts[-1], None)
        else:
            raise NotImplementedError(f"fake op {op}")
    return doc


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction=1):
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    """In-memory stand-in for the subset of pymongo used by storage.publication."""

    def __init__(self):
        self.docs = []
        self._lock = threading.RLock()

    def insert_one(self, doc):
        with self._lock:
            self.docs.append(doc)

    def find_one(self, filter_dict):
        with self._lock:
            for doc in self.docs:
                if _match(filter_dict, doc):
                    return dict(doc)
            return None

    def find(self, filter_dict):
        with self._lock:
            return _FakeCursor(d for d in self.docs if _match(filter_dict, d))

    def find_one_and_update(self, filter_dict, update, sort=None, return_document=None):
        with self._lock:
            matches = [d for d in self.docs if _match(filter_dict, d)]
            if not matches:
                return None
            target = matches[0]
            _apply(update, target)
            if return_document == pymongo.ReturnDocument.AFTER:
                return dict(target)
            return dict(target)

    def update_one(self, filter_dict, update):
        with self._lock:
            for doc in self.docs:
                if _match(filter_dict, doc):
                    _apply(update, doc)
                    return _UpdateResult(True)
            return _UpdateResult(False)


class _UpdateResult:
    def __init__(self, matched):
        self.matched_count = 1 if matched else 0
        self.modified_count = 1 if matched else 0


NOW = datetime.datetime(2026, 9, 14, 12, 0, 0, tzinfo=datetime.timezone.utc)
LATER = NOW + datetime.timedelta(seconds=1800)  # well past the 900s lease


def _seed_brain(col, reel_id="reel_alpha", user="usr_1"):
    doc = {"_id": reel_id, "id": reel_id, "ownership": {"saved_by": [user]}}
    col.docs.append(doc)
    return doc


def _brain(reel_id="reel_alpha"):
    return {
        "id": reel_id,
        "knowledge": {"category": "Food", "summary": "x", "title": "How to cook"},
        "content": {"caption": "A great reel", "transcript": ""},
        "media": {},
        "source": {"shortcode": reel_id},
    }


class StubClient:
    def __init__(self, fail_create=False):
        self.created_pages = []
        self.fail_create = fail_create
        self.notebook = {"id": "nb1", "displayName": "InstaBrain"}
        self.section = {"id": "sec1"}

    def get_or_create_notebook(self, name):
        return self.notebook

    def create_section(self, notebook_id, section_name):
        return self.section

    def create_page(self, section_id, title, html_content, thumbnail_path=None):
        if self.fail_create:
            raise Exception("Graph API 400: request malformed")
        page = {
            "id": f"page_{len(self.created_pages) + 1}",
            "links": {"oneNoteWebUrl": {"href": f"https://onenote.com/p{len(self.created_pages) + 1}"}},
            "title": title,
        }
        self.created_pages.append(page)
        return page


def _make_writer(user_id="usr_1", fail_create=False):
    writer = OneNoteWriter.__new__(OneNoteWriter)
    writer.user_id = user_id
    writer.client = StubClient(fail_create=fail_create)
    return writer


def _write(writer, brain, col, **kwargs):
    with patch.object(writer, "_cache_onenote_metadata", lambda *a, **k: None), \
         patch.object(writer, "_notebook_name", lambda: "InstaBrain"):
        return writer.write(brain, col=col, **kwargs)


class TestPublicationStore(unittest.TestCase):
    def test_first_claim_creates_slot_and_owns(self):
        col = FakeCollection()
        _seed_brain(col)
        result = pub.claim_publication_slot("reel_alpha", "usr_1", col=col, now=NOW)
        self.assertTrue(result["owned"])
        self.assertEqual(result["action"], "create")
        self.assertEqual(result["record"]["state"], pub.STATE_CREATING)
        stored = pub.get_publication("reel_alpha", "usr_1", col=col)
        self.assertEqual(stored["state"], pub.STATE_CREATING)
        self.assertEqual(stored["page_id"], None)

    def test_normal_reuse_after_complete(self):
        col = FakeCollection()
        _seed_brain(col)
        claim = pub.claim_publication_slot("reel_alpha", "usr_1", col=col, now=NOW)
        pub.complete_publication(
            "reel_alpha", "usr_1", claim["record"]["owner_token"],
            {"page_id": "page_win", "page_url": "https://onenote.com/win", "title": "t"},
            col=col, now=NOW,
        )
        retry = pub.claim_publication_slot("reel_alpha", "usr_1", col=col, now=LATER)
        self.assertFalse(retry["owned"])
        self.assertEqual(retry["action"], "reuse")
        self.assertEqual(retry["record"]["page_id"], "page_win")
        self.assertEqual(retry["record"]["state"], pub.STATE_COMPLETE)

    def test_force_replace_after_complete(self):
        col = FakeCollection()
        _seed_brain(col)
        c1 = pub.claim_publication_slot("reel_alpha", "usr_1", col=col, now=NOW)
        pub.complete_publication("reel_alpha", "usr_1", c1["record"]["owner_token"],
                                {"page_id": "page_a"}, col=col, now=NOW)
        c2 = pub.claim_publication_slot("reel_alpha", "usr_1", force=True, col=col, now=LATER)
        self.assertTrue(c2["owned"])
        self.assertEqual(c2["action"], "replace")
        self.assertEqual(c2["record"]["state"], pub.STATE_REPLACING)
        pub.complete_publication("reel_alpha", "usr_1", c2["record"]["owner_token"],
                                {"page_id": "page_b"}, col=col, now=LATER)
        final = pub.get_publication("reel_alpha", "usr_1", col=col)
        self.assertEqual(final["state"], pub.STATE_COMPLETE)
        self.assertEqual(final["page_id"], "page_b")

    def test_reprocess_behaves_like_force(self):
        col = FakeCollection()
        _seed_brain(col)
        c1 = pub.claim_publication_slot("reel_alpha", "usr_1", col=col, now=NOW)
        pub.complete_publication("reel_alpha", "usr_1", c1["record"]["owner_token"],
                                {"page_id": "page_a"}, col=col, now=NOW)
        c2 = pub.claim_publication_slot("reel_alpha", "usr_1", force=True, col=col, now=LATER)
        self.assertTrue(c2["owned"])
        self.assertEqual(c2["action"], "replace")

    def test_per_user_slots_are_isolated(self):
        col = FakeCollection()
        _seed_brain(col, reel_id="shared", user="usr_1")
        c1 = pub.claim_publication_slot("shared", "usr_1", col=col, now=NOW)
        pub.complete_publication("shared", "usr_1", c1["record"]["owner_token"],
                                {"page_id": "page_u1"}, col=col, now=NOW)
        # User 2 does NOT inherit user 1's page.
        c2 = pub.claim_publication_slot("shared", "usr_2", col=col, now=NOW)
        self.assertTrue(c2["owned"])
        self.assertEqual(c2["record"]["state"], pub.STATE_CREATING)
        # User 1 still reuses their own page.
        r1 = pub.claim_publication_slot("shared", "usr_1", col=col, now=LATER)
        self.assertEqual(r1["action"], "reuse")
        self.assertEqual(r1["record"]["page_id"], "page_u1")

    def test_retry_before_onenote_recovery(self):
        # Crash before OneNote ever runs => no slot yet; recovery creates one page.
        col = FakeCollection()
        _seed_brain(col)
        claim = pub.claim_publication_slot("reel_alpha", "usr_1", col=col, now=NOW)
        self.assertTrue(claim["owned"])
        pub.complete_publication("reel_alpha", "usr_1", claim["record"]["owner_token"],
                                {"page_id": "page_1"}, col=col, now=NOW)
        final = pub.get_publication("reel_alpha", "usr_1", col=col)
        self.assertEqual(final["state"], pub.STATE_COMPLETE)
        self.assertEqual(final["page_id"], "page_1")

    def test_retry_after_crash_reclaims_expired_slot(self):
        # Crash window B: worker died mid-create; slot stuck `creating`.
        col = FakeCollection()
        _seed_brain(col)
        dead = pub.claim_publication_slot("reel_alpha", "usr_1", col=col, now=NOW)
        self.assertTrue(dead["owned"])
        reclaim = pub.claim_publication_slot("reel_alpha", "usr_1", col=col, now=LATER)
        self.assertTrue(reclaim["owned"])
        self.assertEqual(reclaim["record"]["state"], pub.STATE_CREATING)
        pub.complete_publication("reel_alpha", "usr_1", reclaim["record"]["owner_token"],
                                {"page_id": "page_2"}, col=col, now=LATER)
        final = pub.get_publication("reel_alpha", "usr_1", col=col)
        self.assertEqual(final["state"], pub.STATE_COMPLETE)
        self.assertEqual(final["page_id"], "page_2")

    def test_retry_after_persist_reuses_complete(self):
        col = FakeCollection()
        _seed_brain(col)
        c1 = pub.claim_publication_slot("reel_alpha", "usr_1", col=col, now=NOW)
        pub.complete_publication("reel_alpha", "usr_1", c1["record"]["owner_token"],
                                {"page_id": "persisted"}, col=col, now=NOW)
        for _ in range(3):
            retry = pub.claim_publication_slot("reel_alpha", "usr_1", col=col, now=LATER)
            self.assertEqual(retry["action"], "reuse")
            self.assertEqual(retry["record"]["page_id"], "persisted")
        self.assertEqual(len(col.docs), 1)
        self.assertEqual(pub.get_publication("reel_alpha", "usr_1", col=col)["page_id"], "persisted")

    def test_concurrent_claim_single_owner(self):
        col = FakeCollection()
        _seed_brain(col)
        a = pub.claim_publication_slot("reel_alpha", "usr_1", col=col, now=NOW)
        b = pub.claim_publication_slot("reel_alpha", "usr_1", col=col, now=NOW)
        self.assertTrue(a["owned"])
        self.assertEqual(b["action"], "wait")
        self.assertFalse(b["owned"])
        self.assertEqual(len(col.docs), 1)

    def test_failure_marks_slot_failed_and_reclaims(self):
        col = FakeCollection()
        _seed_brain(col)
        c1 = pub.claim_publication_slot("reel_alpha", "usr_1", col=col, now=NOW)
        pub.fail_publication_slot("reel_alpha", "usr_1", c1["record"]["owner_token"],
                                  "Graph API 400", col=col, now=NOW)
        failed = pub.get_publication("reel_alpha", "usr_1", col=col)
        self.assertEqual(failed["state"], pub.STATE_FAILED)
        self.assertIn("Graph API 400", failed["error"])
        retry = pub.claim_publication_slot("reel_alpha", "usr_1", col=col, now=LATER)
        self.assertTrue(retry["owned"])
        self.assertEqual(retry["action"], "create")

    def test_legacy_global_key(self):
        col = FakeCollection()
        _seed_brain(col)
        c1 = pub.claim_publication_slot("reel_alpha", None, col=col, now=NOW)
        self.assertTrue(c1["owned"])
        slot = col.find_one({"_id": "reel_alpha"})["publications"]
        self.assertIn("global", slot)
        self.assertNotIn(None, slot)
        pub.complete_publication("reel_alpha", None, c1["record"]["owner_token"],
                                {"page_id": "page_global"}, col=col, now=NOW)
        self.assertEqual(pub.get_publication("reel_alpha", None, col=col)["page_id"], "page_global")

    def test_claim_without_stored_brain_falls_back_to_legacy(self):
        # No brains doc exists (synthetic/test/local brain): there is no durable
        # record to coordinate against, so publishing falls back to
        # legacy always-create rather than failing.
        col = FakeCollection()
        result = pub.claim_publication_slot("nope", "usr_1", col=col, now=NOW)
        self.assertTrue(result["owned"])
        self.assertEqual(result["action"], "create")

    def test_await_returns_complete(self):
        col = FakeCollection()
        _seed_brain(col)
        c1 = pub.claim_publication_slot("reel_alpha", "usr_1", col=col, now=NOW)
        pub.complete_publication("reel_alpha", "usr_1", c1["record"]["owner_token"],
                                {"page_id": "page_win"}, col=col, now=NOW)
        with patch("storage.publication.time.sleep", lambda *a: None):
            rec = pub.await_publication("reel_alpha", "usr_1", col=col, timeout=5)
        self.assertEqual(rec["state"], pub.STATE_COMPLETE)
        self.assertEqual(rec["page_id"], "page_win")

    def test_await_returns_failed(self):
        col = FakeCollection()
        _seed_brain(col)
        c1 = pub.claim_publication_slot("reel_alpha", "usr_1", col=col, now=NOW)
        pub.fail_publication_slot("reel_alpha", "usr_1", c1["record"]["owner_token"],
                                  "boom", col=col, now=NOW)
        with patch("storage.publication.time.sleep", lambda *a: None):
            rec = pub.await_publication("reel_alpha", "usr_1", col=col, timeout=5)
        self.assertEqual(rec["state"], pub.STATE_FAILED)

    def test_await_times_out_on_stuck_creating(self):
        col = FakeCollection()
        _seed_brain(col)
        pub.claim_publication_slot("reel_alpha", "usr_1", col=col, now=NOW)
        with patch("storage.publication.time.sleep", lambda *a: None):
            rec = pub.await_publication("reel_alpha", "usr_1", col=col, timeout=0)
        self.assertEqual(rec["state"], pub.STATE_CREATING)


class TestWriterIdempotency(unittest.TestCase):
    def _complete_col(self, page="page_a"):
        col = FakeCollection()
        _seed_brain(col)
        return col

    def test_first_write_creates_and_records_page(self):
        col = FakeCollection()
        _seed_brain(col)
        writer = _make_writer("usr_1")
        result = _write(writer, _brain(), col)
        self.assertFalse(result.get("reused"))
        self.assertEqual(len(writer.client.created_pages), 1)
        stored = pub.get_publication("reel_alpha", "usr_1", col=col)
        self.assertEqual(stored["state"], pub.STATE_COMPLETE)
        self.assertEqual(stored["page_id"], "page_1")
        self.assertEqual(stored["section_id"], "sec1")
        self.assertEqual(stored["title"], "How to cook")

    def test_retry_after_persist_no_duplicate(self):
        col = FakeCollection()
        _seed_brain(col)
        writer = _make_writer("usr_1")
        first = _write(writer, _brain(), col)
        self.assertTrue(first.get("id"), "page_1")
        second = _write(writer, _brain(), col)
        self.assertTrue(second["reused"])
        self.assertEqual(second["id"], "page_1")
        self.assertEqual(len(writer.client.created_pages), 1, "no second Graph call on reuse")

    def test_force_write_replaces_existing_page(self):
        col = FakeCollection()
        _seed_brain(col)
        writer = _make_writer("usr_1")
        _write(writer, _brain(), col)
        forced = _write(writer, _brain(), col, force=True)
        self.assertFalse(forced.get("reused"))
        self.assertEqual(len(writer.client.created_pages), 2)
        stored = pub.get_publication("reel_alpha", "usr_1", col=col)
        self.assertEqual(stored["state"], pub.STATE_COMPLETE)
        self.assertEqual(stored["page_id"], "page_2")

    def test_onenote_failure_marks_slot_failed(self):
        col = FakeCollection()
        _seed_brain(col)
        writer = _make_writer("usr_2", fail_create=True)
        with self.assertRaises(Exception):
            _write(writer, _brain(), col)
        stored = pub.get_publication("reel_alpha", "usr_2", col=col)
        self.assertEqual(stored["state"], pub.STATE_FAILED)
        self.assertIn("Graph API 400", stored["error"])

    def test_wait_then_reuse_resolves_without_duplicate(self):
        # Winner holds the slot as `creating`; winner finishes; loser (this
        # writer) waits, then reuses the winner's page without any Graph call.
        col = FakeCollection()
        _seed_brain(col)
        winner = pub.claim_publication_slot("reel_alpha", "usr_1", col=col, now=NOW)
        self.assertTrue(winner["owned"])

        # Winner records completion.
        pub.complete_publication("reel_alpha", "usr_1", winner["record"]["owner_token"],
                                {"page_id": "page_win", "page_url": "u", "title": "How to cook"},
                                col=col, now=NOW)

        writer = _make_writer("usr_1")
        with patch("storage.publication.time.sleep", lambda *a: None):
            result = _write(writer, _brain(), col)
        self.assertTrue(result["reused"])
        self.assertEqual(result["id"], "page_win")
        self.assertEqual(len(writer.client.created_pages), 0, "loser never calls Graph")
        final = pub.get_publication("reel_alpha", "usr_1", col=col)
        self.assertEqual(final["state"], pub.STATE_COMPLETE)
        self.assertEqual(final["page_id"], "page_win")

    def test_wait_when_winner_fails_raises(self):
        # Deterministic proof of the writer's wait-then-fail branch: a losing
        # worker whose slot becomes FAILED mid-wait must raise, never create.
        col = FakeCollection()
        _seed_brain(col)

        # Winner claimed and then crashed, leaving a live lease...
        winner_claim = pub.claim_publication_slot("reel_alpha", "usr_1", col=col, now=NOW)
        self.assertEqual(
            pub.claim_publication_slot("reel_alpha", "usr_1", col=col, now=NOW)["action"],
            "wait",
            "winner's live lease forces the second claim onto the wait path",
        )
        pub.fail_publication_slot("reel_alpha", "usr_1", winner_claim["record"]["owner_token"],
                                  "winner crashed", col=col, now=NOW)

        # The loser's writer observes the failed slot after claiming as a wait.
        writer = _make_writer("usr_1")
        with patch("onenote.writer.claim_publication_slot",
                   return_value={"owned": False, "action": "wait", "record": {"state": "failed"}}), \
             patch("onenote.writer.await_publication",
                   return_value={"state": "failed", "error": "winner crashed"}):
            with self.assertRaises(Exception):
                _write(writer, _brain(), col)
        self.assertEqual(len(writer.client.created_pages), 0, "loser must never touch Graph")


class TestConcurrentWorkers(unittest.TestCase):
    def test_two_workers_produce_single_page(self):
        col = FakeCollection()
        _seed_brain(col, reel_id="reel_cc")
        brain = _brain("reel_cc")
        results = {}
        errors = []
        writers = []
        lock = threading.Lock()

        def run(idx):
            try:
                writer = _make_writer("usr_1")
                outcome = _write(writer, brain, col)
                with lock:
                    writers.append(writer)
                    results[idx] = outcome
            except Exception as e:  # pragma: no cover - defensive
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [])
        self.assertEqual(
            sorted(results.keys()), [0, 1],
            "both workers must succeed via own-or-reuse",
        )
        all_pages = [p for w in writers for p in w.client.created_pages]
        self.assertEqual(len(all_pages), 1, "concurrent workers must produce a single page")
        final = pub.get_publication("reel_cc", "usr_1", col=col)
        self.assertEqual(final["state"], pub.STATE_COMPLETE)
        self.assertEqual(final["page_id"], all_pages[0]["id"])
        reused = [v for v in results.values() if v.get("reused")]
        self.assertEqual(len(reused), 1, "the losing worker reuses the winner's page")


if __name__ == "__main__":
    unittest.main()