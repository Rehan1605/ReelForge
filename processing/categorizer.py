import requests
from pathlib import Path

from config import TEXT_MODEL, CATEGORIES


def categorize(caption, transcript):
    prompt_path = Path("prompts") / "categorizer.txt"

    prompt = prompt_path.read_text(encoding="utf-8")

    categories = "\n".join(f"- {category}" for category in CATEGORIES)

    prompt = prompt.format(
        categories=categories,
        caption=caption,
        transcript=transcript
    )

    response = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": TEXT_MODEL,
            "prompt": prompt,
            "stream": False
        }
    )

    raw_category = response.json()["response"]
    category = raw_category.strip()

    print(f"Raw categorizer response: {raw_category}")
    print(f"Parsed category: {category}")

    return category
