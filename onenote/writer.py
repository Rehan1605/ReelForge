import os

from onenote.formatter import format_brain_object
from onenote.graph_client import GraphClient
from onenote.sanitizer import sanitize_page_title
from storage.publication import (
    await_publication,
    claim_publication_slot,
    complete_publication,
    fail_publication_slot,
)

NOTEBOOK_NAME = "InstaBrain"

PUBLISH_RETRY_WAIT_SECONDS = 45


def _replay_publication(record):
    """Synthesize a create_page-like response from a recorded publication."""
    return {
        "id": record.get("page_id"),
        "links": {"oneNoteWebUrl": {"href": record.get("page_url")}},
        "title": record.get("title"),
        "section_id": record.get("section_id"),
        "reused": True,
    }

CATEGORY_SECTIONS = {
    "Programming": "Programming",
    "AI": "AI",
    "Food": "Food",
    "Photography": "Photography",
    "Gym": "Gym",
    "Finance": "Finance",
    "Productivity": "Productivity",
    "Travel": "Travel",
    "Movies & Edits": "Movies & Edits",
    "Other": "Other",
}


class OneNoteWriter:
    def __init__(self, user_id=None):
        """
        Create a OneNote publishing writer.

        Args:
            user_id: ReelForge user_id ('usr_...'). When provided, publishing
                targets that user's own Microsoft account via
                GraphClient(user_id). When None, the legacy global
                token_cache.bin flow is preserved via GraphClient().
        """
        self.user_id = user_id
        self.client = GraphClient(user_id=user_id) if user_id else GraphClient()
        self.client.authenticate()

    def _notebook_name(self):
        if not self.user_id:
            return NOTEBOOK_NAME

        try:
            from storage.user import get_user_microsoft
            ms = get_user_microsoft(self.user_id)
            if ms and ms.get("notebook_name"):
                return ms["notebook_name"]
        except Exception:
            pass

        return NOTEBOOK_NAME

    def _cache_onenote_metadata(self, notebook, section_name, section):
        if not self.user_id:
            return

        try:
            from storage.user import get_user_microsoft, update_user_microsoft
            ms = get_user_microsoft(self.user_id) or {}
            sections = dict(ms.get("sections") or {})
            notebook_id = (notebook or {}).get("id")
            section_id = (section or {}).get("id")

            if section_id:
                sections[section_name] = section_id

            update = {}
            if notebook_id:
                update["notebook_id"] = notebook_id
            if sections:
                update["sections"] = sections

            if update:
                update_user_microsoft(self.user_id, update)
        except Exception:
            pass

    def get_section_for_category(self, category):
        section_name = CATEGORY_SECTIONS.get(category)

        if section_name is None:
            raise Exception(f"No OneNote section mapped for category: {category}")

        notebook = self.client.get_or_create_notebook(self._notebook_name())
        section = self.client.create_section(notebook["id"], section_name)
        self._cache_onenote_metadata(notebook, section_name, section)
        return section

    def _page_title(self, brain):
        knowledge = brain.get("knowledge", {})
        title = knowledge.get("title")

        if not title:
            content = brain.get("content", {})
            source = brain.get("source", {})
            caption = content.get("caption") or ""

            if caption:
                title = caption.splitlines()[0]
            else:
                title = source.get("shortcode") or "InstaBrain Reel"

        return sanitize_page_title(title)

    def write(self, brain, force=False, col=None):
        """Publish a Brain Object to OneNote idempotently per (reel, user).

        A per-user publication slot is reserved first (see
        storage.publication). Once a page is recorded as ``complete``, later
        calls reuse the recorded page identity without touching Graph — so a
        crash-recovered job never blindly creates a duplicate page. ``force``
        (reprocess) intentionally creates a fresh replacement page.

        Args:
            brain:  Brain Object dict.
            force:  Force a fresh page even when a completed page exists.
            col:    Optional brains collection (offline tests). When None the
                    configured MongoDB brains collection is used.
        """
        try:
            reel_id = brain["id"]
            claim = claim_publication_slot(reel_id, self.user_id, force=force, col=col)
            action = claim["action"]

            if action == "reuse":
                record = claim["record"]
                if record.get("page_id"):
                    return _replay_publication(record)
                raise Exception("Publication record is incomplete.")

            if action == "wait":
                record = await_publication(
                    reel_id, self.user_id, col=col, timeout=PUBLISH_RETRY_WAIT_SECONDS
                )
                if not record:
                    raise Exception("Timed out waiting for a concurrent OneNote publish.")
                if record.get("state") != "complete":
                    raise Exception("A concurrent OneNote publish did not complete.")
                if not record.get("page_id"):
                    raise Exception("Publication record is incomplete.")
                return _replay_publication(record)

            # We own the slot: create a fresh page (first publish or force).
            category = brain["knowledge"]["category"]
            title = self._page_title(brain)
            section = self.get_section_for_category(category)
            section_name = CATEGORY_SECTIONS.get(category)

            # Check for local thumbnail file
            media = brain.get("media") or {}
            thumbnail_path = media.get("thumbnail_path")
            has_thumbnail = bool(thumbnail_path and os.path.isfile(thumbnail_path) and os.path.getsize(thumbnail_path) > 0)

            html_content = format_brain_object(brain, include_thumbnail=has_thumbnail)

            try:
                response = self.client.create_page(
                    section["id"],
                    title,
                    html_content,
                    thumbnail_path=thumbnail_path if has_thumbnail else None,
                )
            except Exception as e:
                fail_publication_slot(
                    reel_id, self.user_id, claim["record"].get("owner_token"),
                    str(e) or "OneNote page creation failed", col=col,
                )
                raise

            links = response.get("links") or {}
            page_url = (links.get("oneNoteWebUrl") or {}).get("href")

            complete_publication(
                reel_id, self.user_id, claim["record"].get("owner_token"),
                {
                    "page_id": response.get("id"),
                    "page_url": page_url,
                    "section_id": section.get("id"),
                    "section_name": section_name,
                    "notebook_name": self._notebook_name(),
                    "title": title,
                },
                col=col,
            )

            return response
        except Exception as e:
            if not self.user_id:
                raise

            message = str(e) or ""
            if (
                "Microsoft account not connected" in message
                or "Per-user Microsoft authentication" in message
                or "Not authenticated" in message
            ):
                raise

            raise Exception(
                "OneNote publishing failed for your Microsoft account. "
                "Please check your connection and try again, or reconnect via /connect."
            )
