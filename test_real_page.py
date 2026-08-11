from onenote.writer import OneNoteWriter
from storage.brain_object import load_latest_brain_object


brain = load_latest_brain_object()

print("Brain title:")
print(brain["knowledge"].get("title"))

print("Brain category:")
print(brain["knowledge"].get("category"))

writer = OneNoteWriter()
writer.write(brain)

print("✅ Real Brain Object successfully written to OneNote.")
