from onenote.writer import OneNoteWriter
from storage.brain_object import load_latest_brain_object


brain = load_latest_brain_object()
writer = OneNoteWriter()
writer.write(brain)

print("✅ Page successfully written to OneNote.")
