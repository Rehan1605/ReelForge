"""
ReelForge V3.5 — Durable Processing Job Store (MongoDB).

Minimal durable queue/job abstraction layered on the existing MongoDB Atlas
dependency. One ``reel_jobs`` collection, four explicit states, atomic state
transitions, duplicate-concurrent protection via a partial unique index, and
lease-based crash/restart recovery.

States
------
    queued       -> accepted by the submitter, waiting for a worker
    queued       -> processing   (atomic claim by exactly one worker)
    processing   -> completed | failed   (atomic terminal transition)
    processing   -> queued      (lease expiry recovery / bounded retry)

Design rules
------------
- The absurd scenario is avoided by construction: a claim is a single
  ``find_one_and_update`` guarded by ``status == queued``, so at most one
  worker ever holds a job.
- Duplicate concurrent processing is prevented by the partial unique index
  ``(claim_key, active)`` where ``active == True``. Only ``queued`` and
  ``processing`` jobs are ``active``; terminal jobs are ``active=False`` and
  therefore never block a fresh submission of the same reel.
- Failures are terminal (no blind auto-retry of pipeline failures). Retries
  come exclusively from lease-expiry recovery (crashed/restarted workers),
  bounded by ``max_attempts``.
- Ownership is carried as ``user_id`` (primary owner, drives OneNote and brain
  ownership) plus ``requested_by`` (every user awaiting completion).
"""
import pymongo
import secrets
from datetime import datetime, timedelta, timezone

from storage.db import ensure_job_indexes, get_collection

JOB_TYPE_REEL = "reel"
JOB_TYPE_FORCE = "force"
JOB_TYPE_REPROCESS = "reprocess"

STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_FAILED)

DEFAULT_MAX_ATTEMPTS = 3
LEASE_TTL_SECONDS = 900

_indexes_initialized: bool = False


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Collection access
# ---------------------------------------------------------------------------

def _ensure_indexes(col) -> None:
    global _indexes_initialized
    if _indexes_initialized or col is None:
        return
    try:
        ensure_job_indexes(col)
        _indexes_initialized = True
    except Exception:
        pass


def _get_jobs_col():
    try:
        col = get_collection("reel_jobs")
        _ensure_indexes(col)
        return col
    except Exception as e:
        raise RuntimeError(f"MongoDB jobs store unavailable: {e}") from e


def _col_or_default(col):
    return col if col is not None else _get_jobs_col()


# ---------------------------------------------------------------------------
# Schema / lifecycle
# ---------------------------------------------------------------------------

def new_job(
    claim_key: str,
    reel_url: str,
    user_id: str | None = None,
    *,
    force: bool = False,
    reel_id: str | None = None,
    chat_id=None,
    job_type: str = JOB_TYPE_REEL,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    now: datetime | None = None,
) -> dict:
    """Build a fresh ``queued`` job document (no I/O)."""
    now = now or now_utc()
    return {
        "_id": f"job_{secrets.token_urlsafe(12)}",
        "job_type": job_type,
        "claim_key": claim_key,
        "reel_url": reel_url,
        "reel_id": reel_id,
        "user_id": user_id,
        "requested_by": [user_id] if user_id else [],
        "chats": [chat_id] if chat_id is not None else [],
        "force": bool(force),
        "status": STATUS_QUEUED,
        "active": True,
        "attempts": 0,
        "max_attempts": max_attempts,
        "claimed_by": None,
        "timestamps": {
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "updated_at": now,
        },
        "lease": {"expires_at": None},
        "result": None,
        "error": None,
        "recovered": False,
    }


def submit_job(
    claim_key: str,
    reel_url: str,
    user_id: str | None = None,
    *,
    force: bool = False,
    reel_id: str | None = None,
    chat_id=None,
    job_type: str = JOB_TYPE_REEL,
    col=None,
    now: datetime | None = None,
) -> tuple[dict, bool]:
    """
    Durably enqueue a reel job. Returns ``(job, duplicate)``.

    ``duplicate`` is True when an identical ``claim_key`` already has an active
    (queued/processing) job. In that case the existing job is returned and the
    new user/chat is merged into it, so two workers can never process the same
    reel concurrently.
    """
    from pymongo.errors import DuplicateKeyError

    col = _col_or_default(col)
    doc = new_job(
        claim_key=claim_key,
        reel_url=reel_url,
        user_id=user_id,
        force=force,
        reel_id=reel_id,
        chat_id=chat_id,
        job_type=job_type,
        now=now,
    )

    try:
        col.insert_one(doc)
        return doc, False
    except DuplicateKeyError:
        existing = col.find_one({"claim_key": claim_key, "active": True})
        if existing is None:
            col.insert_one(doc)
            return doc, False
        if user_id and user_id not in (existing.get("requested_by") or []):
            col.update_one(
                {"_id": existing["_id"]},
                {
                    "$addToSet": {"requested_by": user_id},
                    "$set": {"timestamps.updated_at": now or now_utc()},
                },
            )
        if chat_id is not None and chat_id not in (existing.get("chats") or []):
            col.update_one(
                {"_id": existing["_id"]},
                {
                    "$addToSet": {"chats": chat_id},
                    "$set": {"timestamps.updated_at": now or now_utc()},
                },
            )
        if force and not existing.get("force"):
            col.update_one(
                {"_id": existing["_id"]},
                {"$set": {"force": True, "timestamps.updated_at": now or now_utc()}},
            )
        merged = col.find_one({"_id": existing["_id"]}) or existing
        return merged, True


def get_job(job_id: str, col=None) -> dict | None:
    col = _col_or_default(col)
    return col.find_one({"_id": job_id})


def get_job_for_user(job_id: str, user_id: str | None, col=None) -> dict | None:
    """
    Authorized job retrieval. Returns the job ONLY when ``user_id`` is the
    primary owner or appears in ``requested_by``; otherwise returns None.

    Authorization lives at the storage layer — a user can never inspect
    another user's private job by guessing its ``_id``.
    """
    if not job_id or not user_id:
        return None
    col = _col_or_default(col)
    job = col.find_one({"_id": job_id})
    if job is None:
        return None
    if user_id == job.get("user_id") or user_id in (job.get("requested_by") or []):
        return job
    return None


def list_jobs_for_user(
    user_id: str | None,
    status: str | None = None,
    limit: int = 10,
    col=None,
) -> list[dict]:
    """
    Recent jobs for a requesting user only (primary owner or secondary
    requester, matched against ``requested_by``). Newest first.
    """
    if not user_id:
        return []
    col = _col_or_default(col)
    query = {"requested_by": user_id}
    if status is not None:
        query["status"] = status
    return list(
        col.find(query).sort("timestamps.created_at", -1).limit(max(limit, 1))
    )


def claim_next_job(
    worker_id: str,
    col=None,
    now: datetime | None = None,
) -> dict | None:
    """
    Atomically claim the oldest queued job. Returns the job now in
    ``processing`` state, or None when the queue is empty.
    """
    now = now or now_utc()
    col = _col_or_default(col)
    return col.find_one_and_update(
        {"status": STATUS_QUEUED, "active": True},
        {
            "$set": {
                "status": STATUS_PROCESSING,
                "claimed_by": worker_id,
                "timestamps.started_at": now,
                "timestamps.updated_at": now,
                "lease.expires_at": now + timedelta(seconds=LEASE_TTL_SECONDS),
            },
            "$inc": {"attempts": 1},
        },
        sort=[("timestamps.created_at", 1)],
        return_document=pymongo.ReturnDocument.AFTER,
    )


def renew_job_lease(
    job_id: str,
    claimed_by: str | None,
    col=None,
    now: datetime | None = None,
    lease_ttl: int = LEASE_TTL_SECONDS,
) -> bool:
    """
    Refresh the claim lease on a ``processing`` job so slow pipeline steps can
    never out-live the lease and trigger a needless duplicate recovery.

    Guarded so only the worker that currently holds the claim can renew it: a
    content update requires ``status == processing``, ``active == True``, and
    ``claimed_by == claimed_by``. Returns True when the lease was renewed.
    """
    if not job_id or not claimed_by:
        return False
    now = now or now_utc()
    col = _col_or_default(col)
    res = col.update_one(
        {
            "_id": job_id,
            "status": STATUS_PROCESSING,
            "active": True,
            "claimed_by": claimed_by,
        },
        {
            "$set": {
                "lease.expires_at": now + timedelta(seconds=lease_ttl),
                "timestamps.updated_at": now,
            }
        },
    )
    return (res.matched_count or 0) == 1


def complete_job(
    job_id: str,
    result: dict | None = None,
    col=None,
    now: datetime | None = None,
) -> bool:
    """Move a processing job to ``completed`` (terminal)."""
    now = now or now_utc()
    col = _col_or_default(col)
    res = col.update_one(
        {"_id": job_id, "status": STATUS_PROCESSING},
        {
            "$set": {
                "status": STATUS_COMPLETED,
                "active": False,
                "result": result or {},
                "error": None,
                "claimed_by": None,
                "lease.expires_at": None,
                "timestamps.finished_at": now,
                "timestamps.updated_at": now,
            }
        },
    )
    return (res.matched_count or 0) == 1


def fail_job(
    job_id: str,
    error: str,
    col=None,
    now: datetime | None = None,
) -> bool:
    """Move a processing job to ``failed`` (terminal)."""
    now = now or now_utc()
    col = _col_or_default(col)
    res = col.update_one(
        {"_id": job_id, "status": STATUS_PROCESSING},
        {
            "$set": {
                "status": STATUS_FAILED,
                "active": False,
                "error": str(error),
                "claimed_by": None,
                "lease.expires_at": None,
                "timestamps.finished_at": now,
                "timestamps.updated_at": now,
            }
        },
    )
    return (res.matched_count or 0) == 1


def recover_stale_jobs(col=None, now: datetime | None = None) -> list[dict]:
    """
    Crash/restart recovery. Re-queues processing jobs whose lease has expired
    (their worker died). Jobs that already exhausted ``max_attempts`` are moved
    to ``failed``. Returns a list of ``{"job_id", "action"}`` records.
    """
    now = now or now_utc()
    col = _col_or_default(col)
    stale = list(
        col.find(
            {
                "status": STATUS_PROCESSING,
                "active": True,
                "lease.expires_at": {"$lt": now},
            }
        )
    )
    actions = []
    for job in stale:
        job_id = job["_id"]
        attempts = job.get("attempts") or 0
        max_attempts = job.get("max_attempts") or DEFAULT_MAX_ATTEMPTS
        if attempts >= max_attempts:
            col.update_one(
                {"_id": job_id},
                {
                    "$set": {
                        "status": STATUS_FAILED,
                        "active": False,
                        "error": "lease expired after max attempts",
                        "claimed_by": None,
                        "lease.expires_at": None,
                        "timestamps.finished_at": now,
                        "timestamps.updated_at": now,
                    }
                },
            )
            actions.append({"job_id": job_id, "action": "failed_expired"})
        else:
            col.update_one(
                {"_id": job_id},
                {
                    "$set": {
                        "status": STATUS_QUEUED,
                        "recovered": True,
                        "claimed_by": None,
                        "timestamps.started_at": None,
                        "lease.expires_at": None,
                        "timestamps.updated_at": now,
                    }
                },
            )
            actions.append({"job_id": job_id, "action": "requeued"})
    return actions


def list_jobs(
    status: str | None = None,
    user_id: str | None = None,
    limit: int = 50,
    col=None,
) -> list[dict]:
    """List recent jobs, optionally filtered by status and/or primary owner."""
    col = _col_or_default(col)
    query = {}
    if status is not None:
        query["status"] = status
    if user_id is not None:
        query["user_id"] = user_id
    return list(
        col.find(query).sort("timestamps.created_at", -1).limit(max(limit, 1))
    )


def summarize_result(result: dict) -> dict:
    """Small projection of a pipeline result dict stored on the job."""
    brain = result.get("brain") or {}
    return {
        "success": bool(result.get("success")),
        "cached": bool(result.get("cached")),
        "onenote_success": bool(result.get("onenote_success")),
        "onenote_error": result.get("onenote_error"),
        "category": result.get("category"),
        "reel_id": brain.get("id"),
        "error": result.get("error"),
    }