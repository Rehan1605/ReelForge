from storage.brain_object import load_latest_brain_object
from onenote.formatter import format_to_html

brain = load_latest_brain_object()

html = format_to_html(brain)

with open("preview.html", "w", encoding="utf-8") as f:
    f.write(html)

print("✅ preview.html created")