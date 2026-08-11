import json

from storage.brain_object import load_latest_brain_object


brain = load_latest_brain_object()

print("Top-level keys:")
print(list(brain.keys()))

print("\nKnowledge keys:")
print(list(brain["knowledge"].keys()))

print("\nknowledge.category:")
print(brain["knowledge"].get("category"))

print("\nroot category:")
print(brain.get("category"))

print("\nknowledge.title:")
print(brain["knowledge"].get("title"))

print("\nFull knowledge object:")
print(json.dumps(brain["knowledge"], indent=4))
