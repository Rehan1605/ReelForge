import json
import re
import requests
from pathlib import Path

from config import TEXT_MODEL


def _strip_fences(text):
    """
    Remove markdown code fences if the model wrapped the JSON output.
    Handles ```json ... ```, ``` ... ```, and surrounding whitespace.
    """
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text


def extract_other_knowledge(caption, transcript):
    prompt_path = Path("prompts") / "other_extractor.txt"

    prompt = prompt_path.read_text(encoding="utf-8")

    prompt = (
        prompt.replace("{{", "{")
        .replace("}}", "}")
        .replace("{caption}", caption or "")
        .replace("{transcript}", transcript or "")
    )

    response = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": TEXT_MODEL,
            "prompt": prompt,
            "stream": False
        }
    )

    result = _strip_fences(response.json()["response"])

    try:
        return json.loads(result)
    except json.JSONDecodeError as e:
        raise ValueError("Other extractor returned invalid JSON.") from e
