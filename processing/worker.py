"""
ReelForge V3.5 — Durable job worker.

Claims queued jobs from MongoDB (atomic ``queued -> processing``), executes them
through the existing ``process_reel`` pipeline, and finalizes their state
(``completed`` / ``failed``). Crash recovery re-runs through lease-expiry
recovery in :mod:`storage.job`.

The worker is intentionally thin: it does not reimplement the pipeline. It
carries optional user notification over a caller-supplied notifier factory.
"""
import asyncio
import os
import threading
import traceback

from processing.pipeline import process_reel
from storage.brain_object import associate_user_with_brain
from storage.job import (
    LEASE_TTL_SECONDS,
    claim_next_job,
    complete_job,
    fail_job,
    recover_stale_jobs,
    renew_job_lease,
    summarize_result,
)

DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_WORKER_PREFIX = "reelforge-worker"

# Heartbeat cadence for keeping a long pipeline run's claim lease fresh.
LEASE_RENEW_INTERVAL_SECONDS = LEASE_TTL_SECONDS / 3


def _default_worker_id() -> str:
    return f"{DEFAULT_WORKER_PREFIX}-{os.getpid()}"


def _associate_secondary_users(job, result) -> None:
    """Attach every non-primary requester to the finished brain (ownership)."""
    brain = result.get("brain") or {}
    reel_id = brain.get("id") or job.get("reel_id")
    if not reel_id:
        return
    primary = job.get("user_id")
    for uid in job.get("requested_by") or []:
        if uid and uid != primary:
            try:
                associate_user_with_brain(reel_id, uid)
            except Exception as e:
                print(f"Notice: could not associate user {uid} with {reel_id}: {e}")


class _LeaseRenewer(threading.Thread):
    """Background heartbeat that keeps a processing job's claim lease fresh.

    Slow pipeline steps (Whisper model load, AI review, OneNote publish) can
    exceed the default 900s claim lease. Without renewal the lease would expire
    mid-run and another worker would recover the job, duplicating work. The
    renewer is only active while ``run_claimed_job`` holds the job and is
    stopped before any terminal state is written. ``daemon=True`` so a hung
    worker is still recovered after lease expiry once this process dies.
    """

    def __init__(
        self,
        job_id: str,
        claimed_by: str | None,
        col=None,
        now=None,
        interval: float | None = None,
    ):
        super().__init__(daemon=True)
        self._job_id = job_id
        self._claimed_by = claimed_by
        self._col = col
        self._now = now
        self._interval = interval or LEASE_RENEW_INTERVAL_SECONDS
        self._stop = threading.Event()

    def run(self):
        while not self._stop.wait(self._interval):
            try:
                renew_job_lease(
                    self._job_id, self._claimed_by, col=self._col, now=self._now
                )
            except Exception as e:
                print(f"[worker] lease renew failed for {self._job_id}: {e}")

    def stop(self):
        self._stop.set()
        self.join(timeout=1.0)


def run_claimed_job(
    job,
    make_notifier=None,
    process_fn=process_reel,
    col=None,
    now=None,
    renewal_interval: float | None = None,
) -> dict:
    """
    Execute an already-claimed job through the pipeline and finalize its state.

    ``process_fn(url, progress_callback, force, user_id)`` is the pipeline
    entrypoint (defaults to :func:`processing.pipeline.process_reel`).

    Reliability semantics:
    - the claim lease is refreshed in the background while the job runs;
    - success notifications fire only after the ``completed`` transition is
      durably persisted, so a persistence outage can never turn a successful
      job into a false ``failed`` notification;
    - notification failures (progress or final) are non-fatal and never change
      job state.

    Returns the pipeline result dict (or a synthetic failure dict on raise).
    """
    notifier = make_notifier(job) if make_notifier else None

    def _safe_notify(text):
        if notifier is None:
            return
        progress = getattr(notifier, "progress", None)
        if not progress:
            return
        try:
            progress(text)
        except Exception as e:
            print(f"Notice: progress notification failed for {job['_id']}: {e}")

    def _safe_final(info):
        if notifier is None:
            return
        final = getattr(notifier, "final", None)
        if not final:
            return
        try:
            final(info)
        except Exception as e:
            print(f"Notice: final notification failed for {job['_id']}: {e}")
            traceback.print_exc()

    renewer = _LeaseRenewer(
        job["_id"],
        job.get("claimed_by"),
        col=col,
        now=now,
        interval=renewal_interval,
    )
    renewer.start()

    try:
        result = process_fn(
            job["reel_url"],
            _safe_notify,
            bool(job.get("force")),
            job.get("user_id"),
        )

        if result.get("success"):
            _associate_secondary_users(job, result)
            try:
                persisted = complete_job(
                    job["_id"], summarize_result(result), col=col, now=now
                )
            except Exception as e:
                # Pipeline side effects already happened but the terminal write
                # failed (Mongo outage). Do NOT notify "failed" -- that would be
                # a false negative. Leave the job processing; lease recovery
                # requeues it and Layer 4 publication slots dedup the OneNote
                # page. (An offline/disabled Mongo raises, not returns False.)
                traceback.print_exc()
                print(
                    f"Notice: could not persist completion for {job['_id']}; "
                    f"lease recovery will re-run it: {e}"
                )
                return {
                    "success": False,
                    "error": "completion not persisted; will be retried via lease recovery",
                    "durable_pending": True,
                }
            if not persisted:
                print(
                    f"Notice: completion did not match a processing state for {job['_id']}"
                )
            _safe_final(result)
            return result

        error = result.get("error") or "Pipeline failed"
        try:
            fail_job(job["_id"], error, col=col, now=now)
        except Exception as e:
            print(
                f"Notice: could not persist failure for {job['_id']}; "
                f"lease recovery will re-run it: {e}"
            )
        _safe_final({"success": False, "error": error})
        return result

    except Exception as e:
        traceback.print_exc()
        error = str(e)
        try:
            fail_job(job["_id"], error, col=col, now=now)
        except Exception as e:
            print(
                f"Notice: could not persist failure for {job['_id']}; "
                f"lease recovery will re-run it: {e}"
            )
        failure = {"success": False, "error": error}
        _safe_final(failure)
        return failure

    finally:
        renewer.stop()


def process_next_job(
    worker_id: str | None = None,
    make_notifier=None,
    process_fn=process_reel,
    col=None,
    now=None,
) -> dict | None:
    """
    Claim one queued job and execute it (single worker step).

    Returns the pipeline result dict, or None when the queue is idle.
    """
    job = claim_next_job(worker_id or _default_worker_id(), col=col, now=now)
    if job is None:
        return None
    return run_claimed_job(job, make_notifier=make_notifier, process_fn=process_fn, col=col, now=now)


async def worker_loop(
    make_notifier=None,
    worker_id: str | None = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    col=None,
    process_fn=process_reel,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """
    Runs forever: recover stale leases, then claim and process one job at a
    time. Designed to run as an asyncio task inside the bot process, or as a
    standalone worker entrypoint; multiple workers are safe because claims are
    atomic.
    """
    loop = loop or asyncio.get_running_loop()
    worker_id = worker_id or _default_worker_id()
    print(f"[worker/{worker_id}] durable job worker started (poll {poll_interval}s)")

    while True:
        try:
            recover_stale_jobs(col=col)
            job = await asyncio.to_thread(
                process_next_job, worker_id, make_notifier, process_fn, col
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[worker/{worker_id}] loop error: {e}")
            job = None

        if job is None:
            await asyncio.sleep(poll_interval)
        else:
            await asyncio.sleep(0.25)


def standalone_worker():
    """
    Run a fully independent worker process.

    Owns its own Telegram ``Bot`` client (from ``TELEGRAM_BOT_TOKEN``) used
    only to notify users; communicates with the bot process exclusively
    through the shared MongoDB ``reel_jobs`` collection.
    """
    from config import BOT_TOKEN
    from telegram import Bot as TelegramBot

    from bot.notifier import JobNotifier, notifier_kind_for_job

    async def main() -> None:
        loop = asyncio.get_running_loop()
        bot = TelegramBot(token=BOT_TOKEN)

        def make_notifier(job):
            kind = notifier_kind_for_job(job)
            return JobNotifier(
                bot=bot,
                loop=loop,
                chats=job.get("chats") or [],
                kind=kind,
            )

        try:
            print("[worker/standalone] initializing Telegram bot client...")
            await bot.initialize()
            await worker_loop(
                make_notifier=make_notifier,
                worker_id=f"standalone-{os.getpid()}",
                poll_interval=DEFAULT_POLL_INTERVAL,
            )
        finally:
            await bot.shutdown()
            print("[worker/standalone] Telegram bot client shut down.")

    # Each standalone worker process gets its own private, ephemeral media
    # workspace (WORKSPACE_DIR/workers/<id>). Embedded/single-process bots and
    # CLI runs do NOT activate isolation and keep the configured WORKSPACE_DIR.
    from processing.worker_workspace import activate_worker_workspace

    ws = activate_worker_workspace()
    print(f"[worker/standalone] worker workspace: {ws}")

    asyncio.run(main())


if __name__ == "__main__":
    standalone_worker()