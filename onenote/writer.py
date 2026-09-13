import os
from onenote.formatter import format_brain_object
from onenote.graph_client import GraphClient
from onenote.sanitizer import sanitize_page_title

NOTEBOOK_NAME = "InstaBrain"

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

    def write(self, brain):
        try:
            category = brain["knowledge"]["category"]
            title = self._page_title(brain)
            section = self.get_section_for_category(category)

            # Check for local thumbnail file
            media = brain.get("media") or {}
            thumbnail_path = media.get("thumbnail_path")
            has_thumbnail = bool(thumbnail_path and os.path.isfile(thumbnail_path) and os.path.getsize(thumbnail_path) > 0)

            html_content = format_brain_object(brain, include_thumbnail=has_thumbnail)

            return self.client.create_page(
                section["id"],
                title,
                html_content,
                thumbnail_path=thumbnail_path if has_thumbnail else None,
            )
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
