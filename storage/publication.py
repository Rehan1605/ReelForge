"""
ReelForge V3.5 — Layer 4: Idempotent OneNote Publication Store.

Guarantees that publishing a Brain Object to OneNote is idempotent per
``(reel_id, user)`` so that a crashed worker's recovered job cannot blindly
create a duplicate OneNote page.

Where state lives
-----------------
Publication state is stored inline on the existing ``brains`` document as a
``publications`` map keyed by user:

    brains["publications"][publication_key(user_id)] = {
        "state": "creating" | "replacing" | "complete" | "failed",
        "owner_token": "<per-worker secret>",
        "created_at"/"started_at"/"updated_at"/"lease_expires_at",
        "page_id", "page_url", "section_id",
        "section_name", "notebook_name", "title", "error", "force",
    }

``publication_key(None)`` returns ``"global"`` (legacy token_cache.bin flow).
``publication_key(user_id)`` returns the user_id itself, so every user of a
shared reel gets an independent, correctly scope-locked page.

Why no new index
----------------
Exclusive ownership is enforced with atomic MongoDB ``find_one_and_update``
state-guarded transitions against the brains document's immutable ``_id``.
Every worker that races to own a slot transitions the SAME pre-existing
(not created) field under a unique document, so MongoDB already serializes
the writers with native document-level atomicity. Introducing a mandatory
new unique index or collection would be an overloaded facility the system
does not need.

Slot lifecycle
--------------
    (absent)            -> creating      fresh first publish (worker owns)
    creating/replacing  -> complete      page created + identity recorded
    creating/replacing  -> failed        page creation raised
    complete            -> replacing     force / reprocess (fresh page)
    creating/replacing/failed -> (create|replace)   lease-expired reclaim

Failure windows (documented, unavoidable)
-----------------------------------------
B. Crash between page creation and recording the identity: the slot stays
   ``creating`` until its lease expires, a recovery worker then reclaims it
   and creates a fresh page (the old page is an orphaned OneNote page).
D. Network timeout right after a successful create: same as B.
These are genuine external-side-effect windows that no retry protocol can
close without a Graph-side transaction primitive, which does not exist.
"""
from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config import BRAINS_DIR

try:
    import pymongo
    from pymongo import ReturnDocument
except ImportError:
    pymongo = None
    ReturnDocument = None

STATE_CREATING = "creating"
STATE_REPLACING = "replacing"
STATE_COMPLETE = "complete"
STATE_FAILED = "failed"

LEASE_TTL_SECONDS = 900
DEFAULT_PUBLISH_WAIT_SECONDS = 45
DEFAULT_POLL_SECONDS = 0.25


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _as_dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def publication_key(user_id: str | None) -> str:
    """Stable per-user slot key. ``None`` (legacy global) -> ``"global"``."""
    return user_id if user_id else "global"


def _col_or_default(col):
    if col is not None:
        return col
    from storage.brain_object import _get_brains_col
    return _get_brains_col()


# ---------------------------------------------------------------------------
# Local JSON fallback (mirrors brain_object.py dual-write)
# ---------------------------------------------------------------------------

_DT_FIELDS = ("created_at", "updated_at", "started_at", "lease_expires_at")


def _record_to_iso(record: dict) -> dict:
    out = {}
    for key, value in record.items():
        if key in _DT_FIELDS and isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def _record_from_iso(record: dict) -> dict:
    out = {}
    for key, value in record.items():
        if key in _DT_FIELDS and isinstance(value, str):
            try:
                out[key] = datetime.fromisoformat(value)
                continue
            except ValueError:
                pass
        out[key] = value
    return out


def _local_brain_path(reel_id: str) -> Path:
    return Path(BRAINS_DIR) / f"{reel_id}.json"


def _read_local_publication(reel_id: str, key: str) -> dict | None:
    try:
        path = _local_brain_path(reel_id)
        if not path.is_file():
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        record = (data.get("publications") or {}).get(key)
        return _record_from_iso(record) if record else None
    except Exception:
        return None


def _sync_local_publication(reel_id: str, key: str, record: dict) -> None:
    try:
        from storage.brain_object import _atomic_write_json
        path = _local_brain_path(reel_id)
        if not path.is_file():
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        publications = data.setdefault("publications", {})
        publications[key] = _record_to_iso(record)
        _atomic_write_json(path, data)
    except Exception as e:
        print(f"Notice: Could not sync local publication record: {e}")


# ---------------------------------------------------------------------------
# Record helpers
# ---------------------------------------------------------------------------

def _slot(state: str, now: datetime, lease_expires_at: datetime) -> dict:
    return {
        "state": state,
        "owner_token": secrets.token_urlsafe(16),
        "created_at": now,
        "started_at": now,
        "updated_at": now,
        "lease_expires_at": lease_expires_at,
        "page_id": None,
        "page_url": None,
        "section_id": None,
        "section_name": None,
        "notebook_name": None,
        "title": None,
        "error": None,
        "force": state == STATE_REPLACING,
    }


def _active_slot_set(slot: str, state: str, now: datetime, lease_expires_at: datetime) -> dict:
    return {
        f"{slot}.state": state,
        f"{slot}.owner_token": secrets.token_urlsafe(16),
        f"{slot}.started_at": now,
        f"{slot}.lease_expires_at": lease_expires_at,
        f"{slot}.updated_at": now,
        f"{slot}.error": None,
        f"{slot}.force": state == STATE_REPLACING,
    }


def _lease_expired(record: dict, now: datetime) -> bool:
    expires_at = _as_dt(record.get("lease_expires_at"))
    if expires_at is None:
        return True
    return expires_at <= now


def _result(owned: bool, action: str, record: dict) -> dict:
    return {"owned": owned, "action": action, "record": record}


def _read_record(col, reel_id: str, key: str) -> dict | None:
    if col is None:
        return None
    doc = col.find_one({"_id": reel_id})
    if doc is None:
        doc = col.find_one({"id": reel_id})
    if not doc:
        return None
    publications = doc.get("publications") or {}
    return publications.get(key)


def _fresh_create(col, reel_id: str, slot: str, now: datetime, force: bool, lease_expires_at: datetime):
    """Atomically insert a fresh slot on the brains doc (by ``_id``, then ``id``)."""
    record = _slot(STATE_REPLACING if force else STATE_CREATING, now, lease_expires_at)
    claimed = col.find_one_and_update(
        {"_id": reel_id, slot: {"$exists": False}},
        {"$set": {slot: record}},
        return_document=ReturnDocument.AFTER if ReturnDocument is not None else True,
    )
    if claimed is not None:
        return claimed
    return col.find_one_and_update(
        {"id": reel_id, slot: {"$exists": False}},
        {"$set": {slot: record}},
        return_document=ReturnDocument.AFTER if ReturnDocument is not None else True,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_publication(reel_id: str, user_id: str | None = None, col=None) -> dict | None:
    """Return the current publication record for ``(reel_id, user)`` or None."""
    if not reel_id:
        return None
    key = publication_key(user_id)
    collection = _col_or_default(col)
    if collection is not None:
        record = _read_record(collection, reel_id, key)
        if record is not None:
            return record
    return _read_local_publication(reel_id, key)


def claim_publication_slot(
    reel_id: str,
    user_id: str | None = None,
    *,
    force: bool = False,
    col=None,
    now: datetime | None = None,
    lease_ttl: int = LEASE_TTL_SECONDS,
) -> dict:
    """
    Atomically reserve the OneNote publication slot for ``(reel_id, user)``.

    Returns ``{"owned": bool, "action": str, "record": dict}`` where
    ``action`` is one of:

      create   - first publish (or failed/expired reclaim); owned=True
      replace  - force/reprocess fresh page; owned=True
      reuse    - a completed page already exists; owned=False, no Graph call
      wait     - another worker owns the slot; call ``await_publication``
    """
    now = now or now_utc()
    col = _col_or_default(col)
    key = publication_key(user_id)
    slot = f"publications.{key}"
    lease_expires_at = now + timedelta(seconds=lease_ttl)

    if col is None:
        # No shared store (local-only / test isolation): nothing to coordinate
        # against, so this worker always owns a fresh publication. This
        # preserves legacy single-process semantics.
        record = _slot(STATE_REPLACING if force else STATE_CREATING, now, lease_expires_at)
        return _result(True, "replace" if force else "create", record)

    record = _read_record(col, reel_id, key)

    if record is None:
        claimed = _fresh_create(col, reel_id, slot, now, force, lease_expires_at)
        if claimed is not None:
            rec = claimed["publications"][key]
            return _result(True, "replace" if force else "create", rec)
        record = _read_record(col, reel_id, key)
        if record is None:
            # The brains document is absent (synthetic/test/local brain). There
            # is no durable record to coordinate against, so fall back to the
            # legacy always-create behavior rather than blocking publishing.
            rec = _slot(STATE_REPLACING if force else STATE_CREATING, now, lease_expires_at)
            return _result(True, "replace" if force else "create", rec)

    state = record.get("state")

    if state == STATE_COMPLETE:
        if not force:
            return _result(False, "reuse", record)
        claimed = col.find_one_and_update(
            {"_id": reel_id, f"{slot}.state": STATE_COMPLETE},
            {"$set": _active_slot_set(slot, STATE_REPLACING, now, lease_expires_at)},
            return_document=ReturnDocument.AFTER if ReturnDocument is not None else True,
        )
        if claimed is not None:
            return _result(True, "replace", claimed["publications"][key])
        record = _read_record(col, reel_id, key)
        return _result(False, "wait", record)

    if state in (STATE_CREATING, STATE_REPLACING):
        if not _lease_expired(record, now):
            return _result(False, "wait", record)
        # Lease expired: the previous worker is presumed dead. Reclaim.

    if state in (STATE_CREATING, STATE_REPLACING, STATE_FAILED):
        new_state = STATE_REPLACING if force else STATE_CREATING
        claimed = col.find_one_and_update(
            {"_id": reel_id, f"{slot}.state": {"$in": [STATE_CREATING, STATE_REPLACING, STATE_FAILED]}},
            {"$set": _active_slot_set(slot, new_state, now, lease_expires_at)},
            return_document=ReturnDocument.AFTER if ReturnDocument is not None else True,
        )
        if claimed is not None:
            return _result(True, "replace" if force else "create", claimed["publications"][key])
        record = _read_record(col, reel_id, key)
        return _result(False, "wait", record)

    return _result(False, "wait", record)


def _clean_page_info(page_info: dict) -> dict:
    """Keep only persistable (str/None) page identity fields.

    Guards against non-BSON values (e.g. MagicMock responses in tests or odd
    Graph payloads) ever reaching a Mongo ``$set``.
    """
    cleaned = {}
    for field in ("page_id", "page_url", "section_id", "section_name", "notebook_name", "title"):
        value = page_info.get(field)
        if value is None or isinstance(value, str):
            cleaned[field] = value
    return cleaned


def complete_publication(
    reel_id: str,
    user_id: str | None,
    owner_token: str,
    page_info: dict,
    col=None,
    now: datetime | None = None,
) -> dict:
    """Record the created page identity and mark the slot ``complete``.

    Guarded by ``owner_token`` so only the worker that won the slot can record
    the outcome (a late, lease-expired worker cannot clobber the new winner).
    """
    now = now or now_utc()
    col = _col_or_default(col)
    key = publication_key(user_id)
    slot = f"publications.{key}"
    page_info = _clean_page_info(page_info)

    if col is None:
        local = _read_local_publication(reel_id, key)
        record = dict(local) if local else _slot(STATE_CREATING, now, now)
        record.update({k: v for k, v in page_info.items() if v is not None})
        record["state"] = STATE_COMPLETE
        record["lease_expires_at"] = None
        record["error"] = None
        record["updated_at"] = now
        _sync_local_publication(reel_id, key, record)
        return record

    update = {
        f"{slot}.state": STATE_COMPLETE,
        f"{slot}.lease_expires_at": None,
        f"{slot}.error": None,
        f"{slot}.updated_at": now,
    }
    for field in ("page_id", "page_url", "section_id", "section_name", "notebook_name", "title"):
        value = page_info.get(field)
        if value is not None:
            update[f"{slot}.{field}"] = value

    claimed = col.find_one_and_update(
        {
            "_id": reel_id,
            f"{slot}.state": {"$in": [STATE_CREATING, STATE_REPLACING]},
            f"{slot}.owner_token": owner_token,
        },
        {"$set": update},
        return_document=ReturnDocument.AFTER if ReturnDocument is not None else True,
    )
    if claimed is None:
        current = _read_record(col, reel_id, key)
        if current is None:
            # Brains doc absent entirely: no durable record exists to persist
            # into. Return a synthesized completed record so the caller (and
            # legacy local/test flows) can proceed normally.
            record = _slot(STATE_CREATING, now, now)
            record.update({k: v for k, v in page_info.items() if v is not None})
            record["state"] = STATE_COMPLETE
            record["lease_expires_at"] = None
            record["error"] = None
            record["updated_at"] = now
            return record
        raise RuntimeError(f"Publication slot for '{reel_id}' is no longer owned; cannot record the page.")

    record = claimed["publications"][key]
    _sync_local_publication(reel_id, key, record)
    return record


def fail_publication_slot(
    reel_id: str,
    user_id: str | None,
    owner_token: str,
    error: str,
    col=None,
    now: datetime | None = None,
) -> dict | None:
    """Mark the slot ``failed`` after a page-creation failure.

    A failed slot keeps no page identity, so the next worker can safely
    reclaim it and create a fresh page (no orphan reference).
    """
    now = now or now_utc()
    col = _col_or_default(col)
    key = publication_key(user_id)
    slot = f"publications.{key}"

    if col is None:
        local = _read_local_publication(reel_id, key)
        record = dict(local) if local else _slot(STATE_CREATING, now, now)
        record["state"] = STATE_FAILED
        record["lease_expires_at"] = None
        record["error"] = str(error)
        record["updated_at"] = now
        _sync_local_publication(reel_id, key, record)
        return record

    claimed = col.find_one_and_update(
        {
            "_id": reel_id,
            f"{slot}.state": {"$in": [STATE_CREATING, STATE_REPLACING]},
            f"{slot}.owner_token": owner_token,
        },
        {
            "$set": {
                f"{slot}.state": STATE_FAILED,
                f"{slot}.lease_expires_at": None,
                f"{slot}.error": str(error),
                f"{slot}.updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER if ReturnDocument is not None else True,
    )
    if claimed is None:
        current = _read_record(col, reel_id, key)
        if current is None:
            record = _slot(STATE_CREATING, now, now)
            record["state"] = STATE_FAILED
            record["lease_expires_at"] = None
            record["error"] = str(error)
            record["updated_at"] = now
            return record
        return None

    record = claimed["publications"][key]
    _sync_local_publication(reel_id, key, record)
    return record


def await_publication(
    reel_id: str,
    user_id: str | None = None,
    *,
    col=None,
    timeout: float | None = None,
    poll: float = DEFAULT_POLL_SECONDS,
) -> dict | None:
    """Poll until a slot the caller does not own reaches a terminal state.

    Returns the record as soon as it is ``complete`` or ``failed``, or the
    last non-terminal record after ``timeout`` seconds (caller must inspect
    ``state``). Returns None only when no record exists at all.
    """
    timeout = DEFAULT_PUBLISH_WAIT_SECONDS if timeout is None else max(timeout, 0.0)
    deadline = time.time() + timeout
    while True:
        record = get_publication(reel_id, user_id=user_id, col=col)
        state = (record or {}).get("state")
        if state in (STATE_COMPLETE, STATE_FAILED):
            return record
        if time.time() >= deadline:
            return record
        time.sleep(poll)