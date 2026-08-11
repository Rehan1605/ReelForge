from onenote.formatter import format_brain_object
from onenote.graph_client import GraphClient

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

    def write(self, brain):
        category = brain["knowledge"]["category"]
        title = brain["knowledge"]["title"]
        section = self.get_section_for_category(category)
        html_content = format_brain_object(brain)

        return self.client.create_page(
            section["id"],
            title,
            html_content
        )
