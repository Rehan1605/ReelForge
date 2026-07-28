import json
import requests
from pathlib import Path

from config import TEXT_MODEL


def extract_programming_knowledge(caption, transcript):
    prompt_path = Path("prompts") / "programming_extractor.txt"

    prompt = prompt_path.read_text(encoding="utf-8")

    prompt = prompt.format(
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

    result = response.json()["response"].strip()
    print(f"Raw extractor response: {result}")

    try:
        return json.loads(result)
    except json.JSONDecodeError as e:
        raise ValueError("Programming extractor returned invalid JSON.") from e
