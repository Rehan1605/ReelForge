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
    def __init__(self):
        self.client = GraphClient()
        self.client.authenticate()

    def get_section_for_category(self, category):
        section_name = CATEGORY_SECTIONS.get(category)

        if section_name is None:
            raise Exception(f"No OneNote section mapped for category: {category}")

        notebook = self.client.get_or_create_notebook(NOTEBOOK_NAME)
        return self.client.create_section(notebook["id"], section_name)

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
        category = brain["knowledge"]["category"]
        title = self._page_title(brain)
        section = self.get_section_for_category(category)
        html_content = format_brain_object(brain)

        return self.client.create_page(
            section["id"],
            title,
            html_content
        )
