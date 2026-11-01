"""Per-requester OneNote publication for a shared Reel Brain Object."""

from __future__ import annotations

from onenote.writer import OneNoteWriter
from storage.brain_object import associate_user_with_brain, load_brain_object
from storage.publication import get_publication


def _requesters(job: dict) -> list[str]:
    """Normalize new and legacy jobs into a stable, de-duplicated audience."""
    values = list(job.get("requested_by") or [])
    primary = job.get("user_id")
    if primary:
        values.insert(0, primary)
    return list(dict.fromkeys(uid for uid in values if isinstance(uid, str) and uid))


def publish_to_requesters(
    brain: dict,
    job: dict,
    *,
    writer_factory=None,
    col=None,
) -> dict:
    """Publish independently to each requester, continuing after user errors.

    The existing publication slot is authoritative for each (reel, user).
    A completed primary publication from ``process_reel`` is reused without a
    second Graph call. Other users receive their own writer and token cache.
    """
    reel_id = brain.get("id")
    if not reel_id:
        return {"published": 0, "failed": len(_requesters(job)), "pending": 0}

    users = _requesters(job)
    if not users:
        # Preserve the legacy token_cache.bin path for non-user CLI callers;
        # process_reel remains responsible for its publication in that mode.
        return {"published": 0, "failed": 0, "pending": 0}

    writer_factory = writer_factory or OneNoteWriter
    primary = job.get("user_id")
    primary_attempted = not bool(job.get("publication_only")) and "onenote_success" in job
    # Worker callers add this marker only after process_reel has returned.
    primary_attempted = bool(job.get("primary_publication_attempted", primary_attempted))
    outcomes = []

    for user_id in users:
        try:
            if not associate_user_with_brain(reel_id, user_id):
                outcomes.append((user_id, "failed"))
                continue

            record = get_publication(reel_id, user_id=user_id, col=col)
            if record and record.get("state") == "complete":
                outcomes.append((user_id, "published"))
                continue

            if user_id == primary and primary_attempted:
                # The pipeline already attempted this user's page. Keep its
                # durable failed/pending slot for an explicit publication retry.
                outcomes.append((user_id, "failed" if record else "pending"))
                continue

            writer = writer_factory(user_id)
            writer.write(brain, col=col)
            outcomes.append((user_id, "published"))
        except Exception:
            # Do not include user IDs or provider exception text in job output.
            outcomes.append((user_id, "failed"))

    return {
        "published": sum(state == "published" for _, state in outcomes),
        "failed": sum(state == "failed" for _, state in outcomes),
        "pending": sum(state == "pending" for _, state in outcomes),
    }


def publish_for_user(reel_id: str, user_id: str, *, writer_factory=None) -> dict:
    """Retry one user's publication without downloading or processing media."""
    brain = load_brain_object(reel_id, user_id=user_id)
    writer = (writer_factory or OneNoteWriter)(user_id)
    writer.write(brain, force=False)
    return {
        "success": True,
        "cached": True,
        "onenote_success": True,
        "onenote_error": None,
        "brain": brain,
        "brain_path": None,
        "transcript": (brain.get("content") or {}).get("transcript"),
        "category": (brain.get("knowledge") or {}).get("category"),
        "knowledge": brain.get("knowledge"),
        "error": None,
    }
