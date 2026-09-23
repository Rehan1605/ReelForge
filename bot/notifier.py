"""
Shared Telegram notification logic for embedded and standalone workers.

Embedded mode (single-process, default for local development):
  The bot itself runs a background worker task; notifications are sent through
  the running Application's ``Bot`` instance and event loop.

Standalone mode (multi-process / cloud deployment):
  A separate ``python -m processing.worker`` process claims and processes jobs
  from the shared ``reel_jobs`` MongoDB collection.  Notifications are sent via
  its own ``telegram.Bot`` client, configured from the ``TELEGRAM_BOT_TOKEN``
  environment variable.  The bot process and worker process communicate only
  through MongoDB — no direct coupling.
"""
from __future__ import annotations

import asyncio
from pathlib import Path


# ---------------------------------------------------------------------------
# Formatting helpers (shared between bot commands and job notifiers)
# ---------------------------------------------------------------------------

def _first_available(knowledge: dict, keys: tuple[str, ...]):
    for key in keys:
        value = knowledge.get(key)
        if value:
            return value
    return []


def _list_items(items):
    if not items:
        return "None identified."
    return "\n".join(f"- {item}" for item in items)


def onenote_issue_message(onenote_error) -> str:
    """Turn a known OneNote publishing failure into a short, safe, actionable
    message.  Never echoes technical detail or credential material."""
    if not onenote_error:
        return "OneNote publishing failed."
    text = str(onenote_error)
    lowered = text.lower()

    if "not connected" in lowered and "/connect" in lowered:
        return (
            "Your Microsoft connection is not set up.\n\n"
            "Use /connect to connect your Microsoft account."
        )

    if (
        "reconnect via /connect" in lowered
        or "authentication failed" in lowered
        or "needs to be refreshed" in lowered
        or "invalid_grant" in lowered
    ):
        return (
            "Your Microsoft connection needs to be refreshed.\n\n"
            "Please use /connect to reconnect your account."
        )

    if any(
        marker in lowered
        for marker in (
            "token",
            "refresh_token",
            "access_token",
            "token_cache",
            "pkce",
            "verifier",
            "bearer",
            "authorization code",
            "secret",
            "state=",
            "grant",
        )
    ):
        return (
            "OneNote publishing failed.\n\n"
            "Please check your Microsoft connection, or reconnect via /connect."
        )

    return text


def format_summary(result):
    """Render the InstaBrain knowledge-card summary string."""
    brain = result["brain"] or {}
    knowledge = brain.get("knowledge") or {}
    content = brain.get("content") or {}
    source = brain.get("source") or {}
    caption = content.get("caption") or ""
    title = knowledge.get("title") or (
        caption.splitlines()[0] if caption else source.get("shortcode", "Untitled")
    )
    category = result.get("category") or knowledge.get("category") or "Unknown"
    summary = knowledge.get("summary") or "No summary available."
    key_takeaways = _first_available(
        knowledge,
        ("tips", "use_cases", "concepts", "techniques", "steps", "workflows"),
    )
    resources = []
    for key in (
        "websites", "tools", "apps", "editing_apps", "gear", "equipment", "models", "calculators",
    ):
        resources.extend(knowledge.get(key) or [])
    action_items = knowledge.get("action_items") or knowledge.get("tips") or []

    return (
        "InstaBrain Summary\n\n"
        f"Title: {title}\n\n"
        f"Summary:\n{summary}\n\n"
        f"Category:\n{category}\n\n"
        f"Key Takeaways:\n{_list_items(key_takeaways)}\n\n"
        f"Resources:\n{_list_items(resources)}\n\n"
        f"Action Items:\n{_list_items(action_items)}"
    )


def result_summary_text(result, kind="fresh"):
    """Build the terminal Telegram summary for a finished job/pipeline result."""
    category = result.get("category") or "Unknown"
    reel_id = (result.get("brain") or {}).get("id")

    if kind == "cached":
        status_line = (
            "⚡ Already Processed (Cached Brain Object)\n"
            f"✅ OneNote Section: {category}\n\n"
        )
    elif result.get("onenote_success"):
        if kind == "force":
            status_line = (
                "⚡ Force-Processed (Fresh Extraction)\n"
                f"✅ OneNote Page Created in Section: {category}\n\n"
            )
        elif kind == "reprocess":
            label = (
                f"🔄 Successfully Reprocessed `{reel_id}`" if reel_id else "🔄 Successfully Reprocessed"
            )
            status_line = f"{label}\n✅ OneNote Page Created in Section: {category}\n\n"
        else:
            status_line = f"✅ OneNote Page Created in Section: {category}\n\n"
    else:
        prefix = {
            "force": "⚡ Force-Processed (Fresh Extraction)",
            "reprocess": "🔄 Successfully Reprocessed",
            "fresh": "📥 Reel Processed",
        }.get(kind, "📥 Reel Processed")
        status_line = (
            f"{prefix}\n⚠️ Saved to Brain Object, but OneNote publishing failed: "
            f"{onenote_issue_message(result.get('onenote_error'))}\n\n"
        )

    return status_line + format_summary(result)


def notifier_kind_for_job(job: dict) -> str:
    """Derive the notification message kind from a job's ``job_type``."""
    return {
        "force": "force",
        "reprocess": "reprocess",
        "cached": "cached",
    }.get(job.get("job_type"), "fresh")


# ---------------------------------------------------------------------------
# Job notification bridge
# ---------------------------------------------------------------------------

class JobNotifier:
    """Thread-safe chat notifier bridging a worker thread to an event loop.

    In embedded mode, ``bot`` is the Application's Bot and ``loop`` is the
    Application's event loop.
    In standalone mode, ``bot`` is a pre-initialized standalone ``Bot`` and
    ``loop`` is the worker's own event loop.
    """

    def __init__(self, bot, loop, chats=None, kind="fresh"):
        self._bot = bot
        self._loop = loop
        self._chats = [c for c in (chats or []) if c is not None]
        self._kind = kind

    def _schedule(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def progress(self, text):
        async def _send():
            for chat_id in self._chats:
                try:
                    await self._bot.send_message(chat_id=chat_id, text=text)
                except Exception as e:
                    print(f"Notice: could not send progress to {chat_id}: {e}")

        try:
            self._schedule(_send())
        except Exception as e:
            print(f"Notice: progress notify failed: {e}")

    def final(self, result):
        async def _send():
            ok = bool(result.get("success"))
            if ok:
                summary = result_summary_text(result, self._kind)
            else:
                summary = f"❌ Processing failed: {result.get('error', 'Unknown error')}"

            for chat_id in self._chats:
                try:
                    await self._bot.send_message(chat_id=chat_id, text=summary)
                except Exception as e:
                    print(f"Notice: could not send result to {chat_id}: {e}")

                brain_path = result.get("brain_path")
                if ok and brain_path and Path(brain_path).exists():
                    try:
                        with open(brain_path, "rb") as f:
                            await self._bot.send_document(
                                chat_id=chat_id,
                                document=f,
                                filename=Path(brain_path).name,
                            )
                    except Exception as e:
                        print(f"Notice: could not attach brain file to {chat_id}: {e}")

        try:
            self._schedule(_send())
        except Exception as e:
            print(f"Notice: final notify failed: {e}")
