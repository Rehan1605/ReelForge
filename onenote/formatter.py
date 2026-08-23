from datetime import datetime
from html import escape
from urllib.parse import urlsplit, urlunsplit


def format_date(value):
    if not value:
        return value

    try:
        if isinstance(value, str) and value.isdigit() and len(value) == 8:
            return datetime.strptime(value, "%Y%m%d").strftime("%d %b %Y")

        return datetime.fromisoformat(value).strftime("%d %b %Y")
    except (TypeError, ValueError):
        return value


def clean_url(value):
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def add_text_section(parts, heading, text):
    """
    Add a heading and paragraph if text exists.
    """
    if not text:
        return

    parts.append(f"<h2>{escape(heading)}</h2>")
    parts.append(f"<p>{escape(str(text))}</p>")


def add_list_section(parts, heading, items):
    """
    Add a bullet list if items exist.
    """
    if not items:
        return

    items = [item for item in items if item]

    if not items:
        return

    parts.append(f"<h2>{escape(heading)}</h2>")
    parts.append("<ul>")

    for item in items:
        parts.append(f"<li>{escape(str(item))}</li>")

    parts.append("</ul>")


def add_resources_section(parts, resources):
    """
    Add resources with hyperlinks.
    """
    if not resources:
        return

    valid = []

    for resource in resources:
        if not isinstance(resource, dict):
            continue

        name = resource.get("name", "").strip()
        url = resource.get("url", "").strip()

        if name or url:
            valid.append((name, url))

    if not valid:
        return

    parts.append("<h2>Resources</h2>")
    parts.append("<ul>")

    for name, url in valid:
        if url:
            label = name if name else url
            parts.append(
                f'<li><a href="{escape(url)}">{escape(label)}</a></li>'
            )
        else:
            parts.append(f"<li>{escape(name)}</li>")

    parts.append("</ul>")

def format_to_html(brain: dict) -> str:
    """
    Convert a Brain Object into OneNote-compatible HTML.
    """

    knowledge = brain.get("knowledge", {})

    parts = []

    parts.append("<html>")
    parts.append("<body>")

    timestamps = brain.get("timestamps", {})
    source = brain.get("source", {})

    add_text_section(parts, "Category", knowledge.get("category"))
    add_text_section(parts, "Reel Date", format_date(timestamps.get("reel_created")))
    add_text_section(parts, "Saved to InstaBrain", format_date(timestamps.get("processed_at")))

    source_url = source.get("url")
    if source_url:
        source_url = clean_url(source_url)
        parts.append("<h2>Source Reel</h2>")
        parts.append(
            f'<p><a href="{escape(source_url)}">🎬 Open Instagram Reel</a></p>'
        )

    add_text_section(parts, "Summary", knowledge.get("summary"))
    add_text_section(parts, "Main Topic", knowledge.get("main_topic"))
    add_text_section(parts, "Difficulty", knowledge.get("difficulty"))

    add_list_section(parts, "Key Concepts", knowledge.get("key_concepts"))
    add_list_section(parts, "Tools", knowledge.get("tools"))
    add_resources_section(parts, knowledge.get("resources"))
    add_list_section(parts, "Best Practices", knowledge.get("best_practices"))
    add_list_section(parts, "Mistakes to Avoid", knowledge.get("mistakes_to_avoid"))
    add_list_section(parts, "Action Items", knowledge.get("action_items"))
    add_list_section(parts, "Tags", knowledge.get("tags"))

    handled_keys = {
        "category",
        "title",
        "summary",
        "main_topic",
        "difficulty",
        "key_concepts",
        "tools",
        "resources",
        "best_practices",
        "mistakes_to_avoid",
        "action_items",
        "tags",
    }

    for key, value in knowledge.items():
        if key in handled_keys or not value:
            continue

        heading = key.replace("_", " ").title()

        if isinstance(value, list):
            add_list_section(parts, heading, value)
        elif isinstance(value, str):
            add_text_section(parts, heading, value)

    parts.append("</body>")
    parts.append("</html>")

    return "\n".join(parts)


def format_brain_object(brain: dict) -> str:
    return format_to_html(brain)
